"""Process manager for OpenCode instances.

Handles spawning, monitoring, and terminating OpenCode subprocess instances.
Includes PID file management for orphan process cleanup after crashes.

Now supports pluggable instance factories for multi-bot architecture.
"""

import asyncio
import json
import logging
import os
import shutil
import signal
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

import httpx

from .instance import InstanceState, OpenCodeInstance
from .project_detector import detect_project_name
from .pid_manager import PIDManager
from .port_allocator import PortAllocator
from .instance_factories import InstanceFactory, get_registry

logger = logging.getLogger("telegram_controller.process_manager")


# Health check settings
HEALTH_CHECK_INTERVAL = 10.0  # seconds
HEALTH_CHECK_TIMEOUT = 5.0    # seconds
MAX_HEALTH_FAILURES = 3       # failures before marking unreachable

# Startup settings
STARTUP_TIMEOUT = 30.0        # seconds to wait for HTTP API
STARTUP_POLL_INTERVAL = 0.5   # seconds between startup polls

# Process termination settings
GRACEFUL_SHUTDOWN_TIMEOUT = 10.0  # seconds to wait for graceful shutdown
PORT_RELEASE_WAIT = 1.0           # seconds to wait after kill for port release


class ProcessManager:
    """Manages OpenCode subprocess instances.
    
    Responsibilities:
    - Spawn new OpenCode instances with specific directories and ports
    - Monitor running instances via health checks
    - Gracefully terminate instances
    - Track port allocation
    - Auto-restart crashed instances (optional)
    """
    
    def __init__(
        self,
        state_dir: Path | None = None,
        opencode_path: str | None = None,
        auto_restart: bool = True,
        on_instance_change: Callable[[OpenCodeInstance], None] | None = None,
    ):
        """Initialize the process manager.
        
        Args:
            state_dir: Directory for state persistence
            opencode_path: Path to opencode binary (auto-detected if None)
            auto_restart: Whether to auto-restart crashed instances
            on_instance_change: Callback when instance state changes
        """
        self.state_dir = state_dir or Path("~/.local/share/telegram_controller").expanduser()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        
        self.state_file = self.state_dir / "instances.json"
        self.logs_dir = self.state_dir / "logs"
        self.logs_dir.mkdir(exist_ok=True)
        
        # Initialize sub-managers
        self._pid_manager = PIDManager(self.state_dir / "pids")
        self._port_allocator = PortAllocator()
        
        # Auto-detect opencode binary
        self.opencode_path = opencode_path or self._find_opencode()
        
        self.auto_restart = auto_restart
        self.on_instance_change = on_instance_change
        
        # Active instances by ID
        self.instances: dict[str, OpenCodeInstance] = {}
        
        # HTTP client for health checks
        self.http_client: httpx.AsyncClient | None = None
        
        # Background tasks
        self._health_check_task: asyncio.Task | None = None
        self._running = False
        
        # Load persisted state
        self._load_state()
    
    def _find_opencode(self) -> str:
        """Find the opencode binary in PATH or common locations."""
        # Check if it's in PATH
        opencode_in_path = shutil.which("opencode")
        if opencode_in_path:
            return opencode_in_path
        
        # Check common locations
        common_paths = [
            Path.home() / ".local" / "bin" / "opencode",
            Path.home() / "go" / "bin" / "opencode",
            Path("/usr/local/bin/opencode"),
            Path("/opt/homebrew/bin/opencode"),
        ]
        
        for path in common_paths:
            if path.exists():
                return str(path)
        
        # Default to "opencode" and hope it's available
        logger.warning("opencode binary not found in PATH or common locations")
        return "opencode"
    
    # Delegate to port allocator
    @property
    def used_ports(self) -> set[int]:
        """Get the set of used ports."""
        return self._port_allocator.used_ports
    
    def _generate_instance_id(self) -> str:
        """Generate a unique instance ID."""
        return uuid.uuid4().hex[:12]
    
    def _load_state(self) -> None:
        """Load persisted instance state."""
        if not self.state_file.exists():
            return
        
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            for instance_data in data.get("instances", []):
                instance = OpenCodeInstance.from_dict(instance_data)
                # Mark as stopped since we're reloading (process is gone)
                if instance.state in (InstanceState.RUNNING, InstanceState.STARTING):
                    instance.state = InstanceState.STOPPED
                    instance.pid = None
                    instance.process = None
                
                self.instances[instance.id] = instance
                # Only mark ports as used for instances that could still be running
                # Stopped instances' ports should be available for reuse
                if instance.state not in (InstanceState.STOPPED, InstanceState.CRASHED):
                    self._port_allocator.mark_used(instance.port)
            
            logger.info(f"Loaded {len(self.instances)} instances from state file")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
    
    def _save_state(self) -> None:
        """Persist instance state to file."""
        try:
            data = {
                "instances": [inst.to_dict() for inst in self.instances.values()],
                "updated_at": datetime.now().isoformat(),
            }
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
    
    async def start(self) -> None:
        """Start the process manager background tasks."""
        if self._running:
            return
        
        # Clean up any orphan processes from previous crashed sessions
        managed_pids = {inst.pid for inst in self.instances.values() if inst.pid}
        self._pid_manager.cleanup_orphans(managed_pids)
        
        self._running = True
        self.http_client = httpx.AsyncClient(timeout=HEALTH_CHECK_TIMEOUT)
        
        # Start health check loop
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        
        logger.info("Process manager started")
    
    async def stop(self) -> None:
        """Stop the process manager and all instances."""
        self._running = False
        
        # Cancel health check task
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
        
        # Stop all running instances
        await self.stop_all_instances()
        
        # Close HTTP client
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None
        
        # Save final state
        self._save_state()
        
        logger.info("Process manager stopped")
    
    async def spawn_instance(
        self,
        directory: str | Path,
        name: str | None = None,
        provider_id: str = "deepseek",
        model_id: str = "deepseek-reasoner",
        port: int | None = None,
    ) -> OpenCodeInstance:
        """Spawn a new OpenCode instance.
        
        Args:
            directory: Working directory for the instance
            name: Human-readable name for the instance
            provider_id: Default AI provider
            model_id: Default AI model
            port: Specific port to use (auto-allocated if None)
            
        Returns:
            The created OpenCodeInstance
        """
        directory = Path(directory).expanduser().resolve()
        
        if not directory.exists():
            raise ValueError(f"Directory does not exist: {directory}")
        
        # Check if an instance already exists for this directory
        for inst in self.instances.values():
            if inst.directory == directory and inst.is_alive:
                logger.info(f"Instance already running for {directory}")
                return inst
        
        # Allocate port
        if port is None:
            port = self._port_allocator.allocate()
        else:
            port = self._port_allocator.allocate_specific(port)
        
        # Auto-detect project name if not provided
        if name is None:
            name = detect_project_name(directory)
        
        # Create instance
        instance = OpenCodeInstance(
            id=self._generate_instance_id(),
            directory=directory,
            port=port,
            state=InstanceState.STARTING,
            name=name or directory.name,
            provider_id=provider_id,
            model_id=model_id,
            started_at=datetime.now(),
        )
        
        # Prepare log files
        stdout_log = self.logs_dir / f"{instance.id}_stdout.log"
        stderr_log = self.logs_dir / f"{instance.id}_stderr.log"
        
        # Build command - use "opencode serve" for headless HTTP server mode
        cmd = [
            self.opencode_path,
            "serve",
            "--port", str(port),
            "--hostname", "127.0.0.1",
        ]
        
        logger.info(f"Spawning OpenCode instance: {' '.join(cmd)} in {directory}")
        
        try:
            # Open log files
            stdout_file = open(stdout_log, "a", encoding="utf-8")
            stderr_file = open(stderr_log, "a", encoding="utf-8")
            
            # Write header
            timestamp = datetime.now().isoformat()
            stdout_file.write(f"\n--- Instance started at {timestamp} ---\n")
            stderr_file.write(f"\n--- Instance started at {timestamp} ---\n")
            stdout_file.flush()
            stderr_file.flush()
            
            # Spawn process
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(directory),
                stdout=stdout_file,
                stderr=stderr_file,
                stdin=asyncio.subprocess.DEVNULL,
                # Don't propagate signals to child
                start_new_session=True,
            )
            
            instance.process = process
            instance.pid = process.pid
            
            # Write PID file for orphan tracking
            self._pid_manager.write_pid(instance.id, process.pid)
            
            self.instances[instance.id] = instance
            self._save_state()
            
            logger.info(f"Spawned instance {instance.short_id} (PID {instance.pid}) on port {port}")
            
            # Wait for HTTP API to become available
            if await self._wait_for_startup(instance):
                instance.state = InstanceState.RUNNING
                instance.last_health_check = datetime.now()
                logger.info(f"Instance {instance.short_id} is now running")
            else:
                instance.state = InstanceState.CRASHED
                instance.error_message = "HTTP API did not start in time"
                logger.error(f"Instance {instance.short_id} failed to start")
            
            self._save_state()
            
            if self.on_instance_change:
                self.on_instance_change(instance)
            
            return instance
            
        except Exception as e:
            logger.error(f"Failed to spawn instance: {e}")
            self._port_allocator.release(port)
            instance.state = InstanceState.CRASHED
            instance.error_message = str(e)
            self.instances[instance.id] = instance
            self._save_state()
            raise
    
    async def spawn_instance_with_factory(
        self,
        directory: str | Path,
        instance_type: str = "opencode",
        name: str | None = None,
        config: Dict | None = None,
        port: int | None = None,
    ) -> OpenCodeInstance:
        """Spawn an instance using a factory.
        
        This is the preferred method for multi-bot architecture.
        
        Args:
            directory: Working directory for the instance
            instance_type: Factory type ('opencode', 'quantcode', etc.)
            name: Human-readable name for the instance
            config: Type-specific configuration
            port: Specific port to use (auto-allocated if None)
            
        Returns:
            The created instance
            
        Raises:
            ValueError: If instance type is not registered
        """
        directory = Path(directory).expanduser().resolve()
        
        if not directory.exists():
            raise ValueError(f"Directory does not exist: {directory}")
        
        # Get factory
        registry = get_registry()
        factory = registry.get(instance_type)
        
        if factory is None:
            raise ValueError(f"Unknown instance type: {instance_type}. Available: {registry.list_types()}")
        
        # Check if an instance already exists for this directory
        for inst in self.instances.values():
            if inst.directory == directory and inst.is_alive:
                logger.info(f"Instance already running for {directory}")
                return inst
        
        # Allocate port
        if port is None:
            port = self._port_allocator.allocate()
        else:
            port = self._port_allocator.allocate_specific(port)
        
        # Auto-detect project name if not provided
        if name is None:
            name = detect_project_name(directory)
        
        # Merge config with defaults
        default_config = registry.get_default_config(instance_type)
        merged_config = {**default_config, **(config or {})}
        
        instance_id = self._generate_instance_id()
        
        try:
            # Create instance via factory
            instance = await factory.create(
                instance_id=instance_id,
                directory=directory,
                port=port,
                config=merged_config,
            )
            
            # Override name if provided
            if name:
                instance.name = name
            
            # Store instance type for later use
            instance.instance_type = instance_type
            
            # Write PID file for orphan tracking
            if instance.pid:
                self._pid_manager.write_pid(instance.id, instance.pid)
            
            self.instances[instance.id] = instance
            self._save_state()
            
            logger.info(f"Spawned {instance_type} instance {instance.short_id} on port {port}")
            
            # Wait for HTTP API to become available
            if await self._wait_for_startup_with_factory(instance, factory):
                instance.state = InstanceState.RUNNING
                instance.last_health_check = datetime.now()
                logger.info(f"Instance {instance.short_id} is now running")
            else:
                instance.state = InstanceState.CRASHED
                instance.error_message = "HTTP API did not start in time"
                logger.error(f"Instance {instance.short_id} failed to start")
            
            self._save_state()
            
            if self.on_instance_change:
                self.on_instance_change(instance)
            
            return instance
            
        except Exception as e:
            logger.error(f"Failed to spawn instance: {e}")
            self._port_allocator.release(port)
            raise
    
    async def _wait_for_startup_with_factory(
        self,
        instance: OpenCodeInstance,
        factory: InstanceFactory,
    ) -> bool:
        """Wait for instance startup using factory health check."""
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < STARTUP_TIMEOUT:
            # Check if process is still running
            if instance.process and instance.process.returncode is not None:
                instance.error_message = f"Process exited with code {instance.process.returncode}"
                return False
            
            # Try factory health check
            if await factory.health_check(instance):
                return True
            
            await asyncio.sleep(STARTUP_POLL_INTERVAL)
        
        return False
    
    async def _wait_for_startup(self, instance: OpenCodeInstance) -> bool:
        """Wait for an instance's HTTP API to become available."""
        if not self.http_client:
            self.http_client = httpx.AsyncClient(timeout=HEALTH_CHECK_TIMEOUT)
        
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < STARTUP_TIMEOUT:
            # Check if process is still running
            if instance.process and instance.process.returncode is not None:
                instance.error_message = f"Process exited with code {instance.process.returncode}"
                return False
            
            # Try health check
            try:
                response = await self.http_client.get(f"{instance.url}/global/health")
                if response.status_code == 200:
                    return True
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            
            await asyncio.sleep(STARTUP_POLL_INTERVAL)
        
        return False
    
    async def stop_instance(self, instance_id: str, force: bool = False) -> bool:
        """Stop a running instance.
        
        Args:
            instance_id: ID of the instance to stop
            force: If True, use SIGKILL instead of SIGTERM
            
        Returns:
            True if instance was stopped successfully
        """
        instance = self.instances.get(instance_id)
        if not instance:
            logger.warning(f"Instance {instance_id} not found")
            return False
        
        port = instance.port
        
        # If process is already dead, just clean up state
        if not instance.process or instance.process.returncode is not None:
            instance.state = InstanceState.STOPPED
            instance.pid = None
            instance.process = None
            self._port_allocator.release(port)
            self._pid_manager.remove_pid(instance_id)
            self._save_state()
            return True
        
        instance.state = InstanceState.STOPPING
        pid = instance.pid
        
        try:
            # First try graceful termination
            if force:
                logger.info(f"Force killing instance {instance.short_id} (PID {pid})")
                instance.process.kill()
            else:
                logger.info(f"Terminating instance {instance.short_id} (PID {pid})")
                instance.process.terminate()
            
            # Wait for process to exit
            try:
                await asyncio.wait_for(instance.process.wait(), timeout=GRACEFUL_SHUTDOWN_TIMEOUT)
                logger.info(f"Instance {instance.short_id} terminated gracefully")
            except asyncio.TimeoutError:
                logger.warning(f"Instance {instance.short_id} did not stop gracefully, killing")
                instance.process.kill()
                try:
                    await asyncio.wait_for(instance.process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    # Process really stuck, try OS-level kill
                    logger.error(f"Instance {instance.short_id} stuck, trying OS kill")
                    if pid is not None:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except (OSError, ProcessLookupError):
                            pass
            
            # Clean up state
            instance.state = InstanceState.STOPPED
            instance.pid = None
            instance.process = None
            self._port_allocator.release(port)
            self._pid_manager.remove_pid(instance_id)
            self._save_state()
            
            # Wait briefly for port to be released by OS
            await asyncio.sleep(PORT_RELEASE_WAIT)
            
            # Verify port is actually released
            max_port_wait = 5.0
            waited = 0.0
            while waited < max_port_wait and not PortAllocator.is_port_available(port):
                await asyncio.sleep(0.5)
                waited += 0.5
            
            if not PortAllocator.is_port_available(port):
                logger.warning(f"Port {port} still in use after stopping instance {instance.short_id}")
            else:
                logger.info(f"Port {port} released successfully")
            
            logger.info(f"Stopped instance {instance.short_id}")
            
            if self.on_instance_change:
                self.on_instance_change(instance)
            
            return True
            
        except Exception as e:
            logger.error(f"Error stopping instance {instance.short_id}: {e}")
            instance.error_message = str(e)
            # Still mark as stopped and release port
            instance.state = InstanceState.STOPPED
            instance.pid = None
            instance.process = None
            self._port_allocator.release(port)
            self._pid_manager.remove_pid(instance_id)
            self._save_state()
            return False
    
    async def stop_all_instances(self) -> None:
        """Stop all running instances."""
        tasks = []
        for instance_id, instance in self.instances.items():
            if instance.is_alive:
                tasks.append(self.stop_instance(instance_id))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def restart_instance(self, instance_id: str) -> OpenCodeInstance | None:
        """Restart an instance.
        
        Stops the instance if running, then respawns it. If the old port is
        still in use, allocates a new one.
        """
        instance = self.instances.get(instance_id)
        if not instance:
            return None
        
        old_port = instance.port
        directory = instance.directory
        name = instance.name
        provider_id = instance.provider_id
        model_id = instance.model_id
        
        # Stop if running
        if instance.is_alive:
            await self.stop_instance(instance_id)
        
        instance.restart_count += 1
        
        # Determine port to use - try old port first, allocate new if in use
        port_to_use: Optional[int] = None
        if PortAllocator.is_port_available(old_port):
            port_to_use = old_port
            logger.info(f"Reusing port {old_port} for restart")
        else:
            logger.warning(f"Port {old_port} still in use, allocating new port")
            port_to_use = None  # Will auto-allocate
        
        # Remove old instance
        del self.instances[instance_id]
        self._port_allocator.release(old_port)
        
        try:
            # Respawn with new or same port
            return await self.spawn_instance(
                directory=directory,
                name=name,
                provider_id=provider_id,
                model_id=model_id,
                port=port_to_use,
            )
        except Exception as e:
            logger.error(f"Failed to restart instance: {e}")
            # Re-add the old instance as crashed
            instance.state = InstanceState.CRASHED
            instance.error_message = str(e)
            self.instances[instance_id] = instance
            self._save_state()
            return instance
    
    async def remove_instance(self, instance_id: str) -> bool:
        """Remove an instance from management.
        
        Stops the instance if running and removes it from tracking.
        """
        instance = self.instances.get(instance_id)
        if not instance:
            return False
        
        # Stop if running
        if instance.is_alive:
            await self.stop_instance(instance_id)
        
        # Release port and remove PID file
        self._port_allocator.release(instance.port)
        self._pid_manager.remove_pid(instance_id)
        
        # Remove from tracking
        del self.instances[instance_id]
        self._save_state()
        
        logger.info(f"Removed instance {instance.short_id}")
        return True
    
    async def _health_check_loop(self) -> None:
        """Background task to check health of all running instances."""
        while self._running:
            try:
                await self._check_all_instances()
            except Exception as e:
                logger.error(f"Health check error: {e}")
            
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)
    
    async def _check_all_instances(self) -> None:
        """Check health of all running instances."""
        if not self.http_client:
            return
        
        for instance in list(self.instances.values()):
            if not instance.is_alive:
                continue
            
            # Check if process is still running
            if instance.process and instance.process.returncode is not None:
                instance.state = InstanceState.CRASHED
                instance.error_message = f"Process exited with code {instance.process.returncode}"
                instance.pid = None
                self._pid_manager.remove_pid(instance.id)
                logger.warning(f"Instance {instance.short_id} crashed")
                
                if self.auto_restart and instance.restart_count < 3:
                    logger.info(f"Auto-restarting instance {instance.short_id}")
                    asyncio.create_task(self.restart_instance(instance.id))
                
                if self.on_instance_change:
                    self.on_instance_change(instance)
                continue
            
            # HTTP health check
            try:
                response = await self.http_client.get(f"{instance.url}/global/health")
                if response.status_code == 200:
                    instance.last_health_check = datetime.now()
                    instance.health_check_failures = 0
                    if instance.state == InstanceState.UNREACHABLE:
                        instance.state = InstanceState.RUNNING
                        if self.on_instance_change:
                            self.on_instance_change(instance)
                else:
                    instance.health_check_failures += 1
            except (httpx.ConnectError, httpx.TimeoutException):
                instance.health_check_failures += 1
            
            # Mark as unreachable if too many failures
            if instance.health_check_failures >= MAX_HEALTH_FAILURES:
                if instance.state == InstanceState.RUNNING:
                    instance.state = InstanceState.UNREACHABLE
                    logger.warning(f"Instance {instance.short_id} is unreachable")
                    if self.on_instance_change:
                        self.on_instance_change(instance)
        
        self._save_state()
    
    def get_instance(self, instance_id: str) -> OpenCodeInstance | None:
        """Get an instance by ID or short ID."""
        # Try exact match
        if instance_id in self.instances:
            return self.instances[instance_id]
        
        # Try short ID match
        for inst_id, instance in self.instances.items():
            if inst_id.startswith(instance_id):
                return instance
        
        return None
    
    def get_instance_by_directory(self, directory: str | Path) -> OpenCodeInstance | None:
        """Get an instance by its directory."""
        directory = Path(directory).expanduser().resolve()
        for instance in self.instances.values():
            if instance.directory == directory:
                return instance
        return None
    
    def get_running_instances(self) -> list[OpenCodeInstance]:
        """Get all running instances."""
        return [inst for inst in self.instances.values() if inst.is_alive]
    
    def list_instances(self) -> list[OpenCodeInstance]:
        """Get all instances."""
        return list(self.instances.values())
