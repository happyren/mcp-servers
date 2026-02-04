"""OpenCode instance factory.

Factory for creating and managing OpenCode agent instances.
This extracts the existing OpenCode spawning logic into a reusable factory.
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx

from .base import InstanceFactory
from ..instance import InstanceState, OpenCodeInstance

logger = logging.getLogger(__name__)

# Default model configuration
DEFAULT_PROVIDER = "deepseek"
DEFAULT_MODEL = "deepseek-reasoner"


class OpenCodeInstanceFactory(InstanceFactory):
    """Factory for creating OpenCode instances.
    
    OpenCode is an AI coding agent that runs as a subprocess with an HTTP API.
    This factory manages spawning OpenCode processes and health checking them.
    """
    
    @property
    def instance_type(self) -> str:
        return "opencode"
    
    @property
    def command_prefix(self) -> str:
        return "opencode"
    
    @property
    def default_config(self) -> Dict[str, Any]:
        return {
            "provider_id": DEFAULT_PROVIDER,
            "model_id": DEFAULT_MODEL,
            "no_browser": True,
        }
    
    async def create(
        self,
        instance_id: str,
        directory: Path,
        port: int,
        config: Dict[str, Any],
    ) -> OpenCodeInstance:
        """Create and start an OpenCode instance.
        
        Args:
            instance_id: Unique instance identifier
            directory: Working directory for the instance
            port: HTTP API port
            config: Instance configuration
            
        Returns:
            Created OpenCodeInstance
        """
        # Merge with defaults
        merged_config = {**self.default_config, **config}
        
        provider_id = merged_config.get("provider_id", DEFAULT_PROVIDER)
        model_id = merged_config.get("model_id", DEFAULT_MODEL)
        
        # Build spawn command
        cmd = self.get_spawn_command_list(port, merged_config)
        
        logger.info(f"Spawning OpenCode: {' '.join(cmd)} in {directory}")
        
        # Prepare environment
        env = os.environ.copy()
        env.update(self.get_env_vars(merged_config))
        
        # Spawn process
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=directory,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        # Create instance object
        instance = OpenCodeInstance(
            id=instance_id,
            directory=directory,
            port=port,
            state=InstanceState.STARTING,
            pid=process.pid,
            process=process,
            started_at=datetime.now(),
            provider_id=provider_id,
            model_id=model_id,
            name=directory.name,
        )
        
        logger.info(f"Created OpenCode instance {instance.short_id} on port {port}")
        return instance
    
    async def health_check(self, instance: OpenCodeInstance) -> bool:
        """Check OpenCode health via HTTP API.
        
        Args:
            instance: Instance to check
            
        Returns:
            True if healthy
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{instance.url}/api/health")
                if resp.status_code == 200:
                    instance.last_health_check = datetime.now()
                    instance.health_check_failures = 0
                    if instance.state == InstanceState.STARTING:
                        instance.state = InstanceState.RUNNING
                    return True
        except Exception as e:
            logger.debug(f"Health check failed for {instance.short_id}: {e}")
            instance.health_check_failures += 1
        
        return False
    
    def get_spawn_command_list(self, port: int, config: Dict[str, Any]) -> List[str]:
        """Get the command to spawn OpenCode.
        
        Args:
            port: HTTP API port
            config: Instance configuration
            
        Returns:
            Command as list of strings
        """
        provider_id = config.get("provider_id", DEFAULT_PROVIDER)
        model_id = config.get("model_id", DEFAULT_MODEL)
        
        cmd = [
            "opencode",
            "--http-port", str(port),
            "--provider", provider_id,
            "--model", model_id,
        ]
        
        if config.get("no_browser", True):
            cmd.append("--no-browser")
        
        return cmd
    
    def get_env_vars(self, config: Dict[str, Any]) -> Dict[str, str]:
        """Get environment variables for OpenCode.
        
        Args:
            config: Instance configuration
            
        Returns:
            Environment variables
        """
        env = {}
        
        # Pass through API keys if configured
        if "api_key" in config:
            env["OPENCODE_API_KEY"] = config["api_key"]
        
        return env


def generate_instance_id() -> str:
    """Generate a unique instance ID."""
    return str(uuid4())
