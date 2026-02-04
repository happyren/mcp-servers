"""Controller-level command handlers.

Handles commands like /open, /list, /switch, /kill, /status, etc.
Supports multi-bot architecture with different instance types.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

from ..instance import InstanceState, OpenCodeInstance
from ..instance_factories import get_registry

if TYPE_CHECKING:
    from ..controller import TelegramController

logger = logging.getLogger("telegram_controller.commands")


@dataclass
class CommandResponse:
    """Response from a command handler."""
    text: str
    keyboard: Optional[list[list[dict[str, str]]]] = None


class ControllerCommands:
    """Handles controller-level commands."""
    
    def __init__(self, controller: "TelegramController"):
        """Initialize command handlers.
        
        Args:
            controller: Parent controller instance
        """
        self.controller = controller
    
    @property
    def process_manager(self):
        return self.controller.process_manager
    
    @property
    def session_router(self):
        return self.controller.session_router
    
    @property
    def telegram(self):
        return self.controller.telegram
    
    async def handle(
        self,
        text: str,
        chat_id: int,
        topic_id: Optional[int] = None,
    ) -> Optional[Union[str, CommandResponse]]:
        """Handle a potential controller command.
        
        Args:
            text: Message text
            chat_id: Telegram chat ID
            topic_id: Optional topic ID
            
        Returns:
            Response if this was a controller command, None otherwise
        """
        text = text.strip()
        if not text.startswith("/"):
            return None
        
        parts = text[1:].split(None, 1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        # Controller commands
        handlers = {
            "open": self._cmd_open,
            "switch": self._cmd_switch,
            "list": self._cmd_list,
            "projects": self._cmd_list,  # Alias
            "instances": self._cmd_list,  # Alias
            "kill": self._cmd_kill,
            "stop": self._cmd_kill,  # Alias
            "close": self._cmd_close,
            "restart": self._cmd_restart,
            "status": self._cmd_status,
            "help": self._cmd_help,
            "current": self._cmd_current,
            "threads": self._cmd_threads,
        }
        
        handler = handlers.get(cmd)
        if handler:
            return await handler(args, chat_id, topic_id)
        
        return None
    
    def get_instance_commands(self) -> set[str]:
        """Get the set of commands that should be forwarded to instances."""
        return {
            "sessions", "session", "models", "agents", "config",
            "files", "read", "find", "findfile", "find-symbol", "find_symbol",
            "prompt", "shell", "diff", "todo", "fork", "abort", "delete",
            "share", "unshare", "revert", "unrevert", "summarize",
            "info", "messages", "init", "pending", "health",
            "vcs", "lsp", "formatter", "mcp", "dispose", "commands",
            "directory", "project",
        }
    
    async def _cmd_help(self, args: str, chat_id: int, topic_id: Optional[int] = None) -> str:
        """Show help for controller commands."""
        return """
*Telegram Controller*

*Getting Started*
Start a reply thread and send a message - you'll see an instance picker.
Or use `/open <path>` to connect the thread to a new project.

*Project Management*
`/open <path>` - Open project in current thread
`/list` - List all running instances
`/switch [id]` - Switch to different instance
`/current` - Show current instance
`/close` - Stop current instance
`/kill <id>` - Stop specific instance
`/status` - Instance status overview
`/threads` - List thread-instance mappings

*Session Commands*
`/sessions` - List sessions
`/session` - New session
`/models` - List/set models

*File Commands*
`/files` `/read <path>` `/find <pattern>`

*Other*
`/diff` `/todo` `/pending` `/health`

*Tip:* Each reply thread can be connected to a different project!
        """.strip()
    
    async def _cmd_open(self, args: str, chat_id: int, topic_id: Optional[int] = None) -> str:
        """Open a project directory, spawning a new instance.
        
        Usage:
            /open <path>
            /open <path> --type opencode
            /open <path> --type quantcode
        """
        if not args:
            types = get_registry().list_types()
            types_str = ", ".join(types) if types else "opencode"
            return (
                "Usage: `/open <path>` [--type TYPE]\n\n"
                f"Available types: `{types_str}`\n\n"
                "Example: `/open ~/projects/my-app`\n"
                "Example: `/open ~/quant/pipeline --type quantcode`"
            )
        
        # Parse arguments: <path> [--type TYPE]
        instance_type = "opencode"  # Default
        path_str = args
        
        # Check for --type argument
        type_match = re.search(r'--type\s+(\w+)', args)
        if type_match:
            instance_type = type_match.group(1).lower()
            path_str = re.sub(r'--type\s+\w+', '', args).strip()
        
        # Parse path
        path_parts = path_str.split()
        if not path_parts:
            return "Please provide a directory path."
        
        path = Path(path_parts[0]).expanduser().resolve()
        
        if not path.exists():
            return f"Directory does not exist: `{path}`"
        
        if not path.is_dir():
            return f"Not a directory: `{path}`"
        
        # Validate instance type
        registry = get_registry()
        if not registry.has_type(instance_type):
            types_str = ", ".join(registry.list_types())
            return f"Unknown instance type: `{instance_type}`\n\nAvailable types: `{types_str}`"
        
        project_name = path.name
        logger.info(f"_cmd_open: chat_id={chat_id}, topic_id={topic_id}, path={path}, type={instance_type}")
        
        # Get or spawn instance
        instance = await self._get_or_spawn_instance(path, instance_type=instance_type)
        if isinstance(instance, str):
            return instance
        
        # Map to current context
        self.session_router.set_current_instance(chat_id, instance, topic_id=topic_id)
        
        # If this is a thread, create 1:1 mapping
        type_label = f" ({instance_type})" if instance_type != "opencode" else ""
        if topic_id is not None:
            self.session_router.set_topic_instance(chat_id, topic_id, instance.id)
            self.session_router._save_state()
            await self.controller._rename_topic(chat_id, topic_id, project_name)
            return (
                f"📁 Connected thread to *{project_name}*{type_label}\n\n"
                f"Path: `{path}`\n"
                f"Instance: `{instance.short_id}`\n\n"
                f"Send any message to chat with {instance_type.title()}."
            )
        
        return (
            f"📁 Opened *{project_name}*{type_label}\n\n"
            f"Path: `{path}`\n"
            f"Instance: `{instance.short_id}` on port {instance.port}\n\n"
            f"Send any message to chat with {instance_type.title()}."
        )
    
    async def _get_or_spawn_instance(
        self,
        path: Path,
        instance_type: str = "opencode",
    ) -> Union[OpenCodeInstance, str]:
        """Get existing instance for directory or spawn a new one.
        
        Args:
            path: Directory path
            instance_type: Type of instance to spawn ('opencode', 'quantcode', etc.)
            
        Returns:
            Instance or error message
        """
        # Check if instance already exists
        existing = self.process_manager.get_instance_by_directory(path)
        if existing and existing.is_alive:
            # Check if type matches
            if existing.instance_type != instance_type:
                return (
                    f"Instance already running at `{path}` with type `{existing.instance_type}`.\n\n"
                    f"Use `/kill {existing.short_id}` to stop it first, then open with new type."
                )
            return existing
        
        # Check if factory supports this type
        registry = get_registry()
        if not registry.has_type(instance_type):
            return f"Unknown instance type: `{instance_type}`"
        
        # Spawn new instance - use factory-based method if available
        try:
            # Try factory-based spawn first (preferred for multi-bot)
            if hasattr(self.process_manager, 'spawn_instance_with_factory'):
                instance = await self.process_manager.spawn_instance_with_factory(
                    directory=path,
                    instance_type=instance_type,
                    name=path.name,
                    config={
                        "provider_id": self.controller.default_provider,
                        "model_id": self.controller.default_model,
                    },
                )
            else:
                # Fallback to legacy spawn (only supports opencode)
                if instance_type != "opencode":
                    return f"Instance type `{instance_type}` not supported in legacy mode."
                
                instance = await self.process_manager.spawn_instance(
                    directory=path,
                    name=path.name,
                    provider_id=self.controller.default_provider,
                    model_id=self.controller.default_model,
                )
            
            if instance.state != InstanceState.RUNNING:
                return f"Failed to start instance: {instance.error_message or 'Unknown error'}"
            
            return instance
            
        except Exception as e:
            return f"Failed to spawn instance: {str(e)[:200]}"
    
    async def _cmd_list(
        self, args: str, chat_id: int, topic_id: Optional[int] = None
    ) -> Union[str, CommandResponse]:
        """List all running instances."""
        all_instances = self.process_manager.list_instances()
        
        # Check and clean dead instances
        instances_to_remove = []
        running_instances = []
        
        for inst in all_instances:
            if inst.state in (InstanceState.STOPPED, InstanceState.CRASHED):
                instances_to_remove.append(inst.id)
            elif inst.process and inst.process.returncode is not None:
                instances_to_remove.append(inst.id)
            elif inst.is_alive:
                try:
                    client = self.controller._get_instance_client(inst)
                    await asyncio.wait_for(client.health_check(), timeout=2.0)
                    running_instances.append(inst)
                except Exception:
                    logger.warning(f"Instance {inst.short_id} not responding, removing")
                    instances_to_remove.append(inst.id)
            else:
                instances_to_remove.append(inst.id)
        
        # Remove dead instances
        for inst_id in instances_to_remove:
            await self.process_manager.remove_instance(inst_id)
            self.session_router.remove_instance_references(inst_id)
            if inst_id in self.controller.instance_clients:
                try:
                    await self.controller.instance_clients[inst_id].close()
                except Exception:
                    pass
                del self.controller.instance_clients[inst_id]
            keys_to_remove = [k for k in self.controller.instance_handlers if k.startswith(f"{inst_id}:")]
            for k in keys_to_remove:
                del self.controller.instance_handlers[k]
        
        if instances_to_remove:
            logger.info(f"Cleaned up {len(instances_to_remove)} dead instance(s)")
        
        if not running_instances:
            return "No running instances.\n\nUse `/open <path>` to start a new OpenCode instance."
        
        current_id = self.session_router.get_current_instance_id(chat_id, topic_id)
        
        keyboard: list[list[dict[str, str]]] = []
        for inst in running_instances:
            current_marker = " 👈" if inst.id == current_id else ""
            keyboard.append([{
                "text": f"🟢 {inst.short_id} - {inst.display_name}{current_marker}",
                "callback_data": f"instance:{inst.id}",
            }])
        
        current_text = ""
        if current_id:
            current_inst = self.process_manager.get_instance(current_id)
            if current_inst and current_inst.is_alive:
                current_text = f"\nCurrent: `{current_inst.short_id}` ({current_inst.display_name})"
            else:
                self.session_router.clear_current_instance(chat_id)
        
        text = f"*Projects* ({len(running_instances)}){current_text}\n\nTap to switch:"
        return CommandResponse(text=text, keyboard=keyboard)
    
    async def _cmd_switch(
        self, args: str, chat_id: int, topic_id: Optional[int] = None
    ) -> Union[str, CommandResponse]:
        """Switch to a different instance."""
        if not args:
            return await self._cmd_list(args, chat_id, topic_id)
        
        instance = self.process_manager.get_instance(args)
        if not instance:
            return f"Instance `{args}` not found.\n\nUse `/list` to see available instances."
        
        if not instance.is_alive:
            return (
                f"Instance `{instance.short_id}` is not running ({instance.state.value}).\n\n"
                f"Use `/restart {instance.short_id}` to restart it."
            )
        
        self.session_router.set_current_instance(chat_id, instance, topic_id=topic_id)
        
        if topic_id is not None:
            self.session_router.set_topic_instance(chat_id, topic_id, instance.id)
        
        return f"Switched to instance `{instance.short_id}` ({instance.display_name})"
    
    async def _cmd_current(self, args: str, chat_id: int, topic_id: Optional[int] = None) -> str:
        """Show current instance."""
        instance_id = self.session_router.get_current_instance_id(chat_id, topic_id)
        if not instance_id:
            return "No instance selected.\n\nUse `/open <path>` to open a project or `/switch` to select an instance."
        
        instance = self.process_manager.get_instance(instance_id)
        if not instance:
            return "Current instance no longer exists.\n\nUse `/list` to see available instances."
        
        uptime = ""
        if instance.uptime_seconds:
            mins = int(instance.uptime_seconds / 60)
            uptime = f"\nUptime: {mins} minutes"
        
        return f"""
*Current Instance*

ID: `{instance.short_id}`
Name: {instance.display_name}
Directory: `{instance.directory}`
Port: {instance.port}
State: {instance.state.value}
Model: `{instance.provider_id}/{instance.model_id}`{uptime}
        """.strip()
    
    async def _cmd_close(self, args: str, chat_id: int, topic_id: Optional[int] = None) -> str:
        """Close the current instance."""
        instance_id = self.session_router.get_current_instance_id(chat_id, topic_id)
        
        if not instance_id:
            return "No instance selected.\n\nUse `/list` to see running instances."
        
        instance = self.process_manager.get_instance(instance_id)
        if not instance:
            self.session_router.clear_current_instance(chat_id, topic_id)
            return "Instance not found. Cleared reference."
        
        display_name = instance.display_name
        short_id = instance.short_id
        
        if instance.is_alive:
            success = await self.process_manager.stop_instance(instance_id)
            if not success:
                return f"Failed to stop instance `{short_id}` ({display_name})"
        
        self.session_router.clear_current_instance(chat_id, topic_id)
        
        if topic_id is not None:
            self.session_router.clear_topic_instance(chat_id, topic_id)
        
        key = f"{instance_id}:{chat_id}"
        if key in self.controller.instance_handlers:
            del self.controller.instance_handlers[key]
        
        return (
            f"Closed instance `{short_id}` ({display_name})\n\n"
            "Use `/open <path>` to start a new instance or `/list` to see running instances."
        )
    
    async def _cmd_kill(
        self, args: str, chat_id: int, topic_id: Optional[int] = None
    ) -> Union[str, CommandResponse]:
        """Stop an instance."""
        if not args:
            instances = self.process_manager.get_running_instances()
            if not instances:
                return "No running instances to stop."
            
            keyboard: list[list[dict[str, str]]] = []
            for inst in instances:
                keyboard.append([{
                    "text": f"🗑️ {inst.short_id} - {inst.display_name}",
                    "callback_data": f"kill:{inst.id}",
                }])
            
            return CommandResponse(text="*Stop Instance*\n\nSelect instance to stop:", keyboard=keyboard)
        
        instance = self.process_manager.get_instance(args)
        if not instance:
            return f"Instance `{args}` not found."
        
        if not instance.is_alive:
            return f"Instance `{instance.short_id}` is already stopped."
        
        success = await self.process_manager.stop_instance(instance.id)
        if success:
            if self.session_router.get_current_instance_id(chat_id, topic_id) == instance.id:
                self.session_router.clear_current_instance(chat_id, topic_id)
            return f"Stopped instance `{instance.short_id}` ({instance.display_name})"
        else:
            return f"Failed to stop instance `{instance.short_id}`"
    
    async def _cmd_restart(self, args: str, chat_id: int, topic_id: Optional[int] = None) -> str:
        """Restart an instance."""
        if not args:
            return "Usage: `/restart <instance_id>`"
        
        instance = self.process_manager.get_instance(args)
        if not instance:
            return f"Instance `{args}` not found."
        
        try:
            new_instance = await self.process_manager.restart_instance(instance.id)
            if new_instance and new_instance.state == InstanceState.RUNNING:
                return f"Restarted instance `{new_instance.short_id}` ({new_instance.display_name})"
            else:
                error = new_instance.error_message if new_instance else "Unknown error"
                return f"Failed to restart instance: {error}"
        except Exception as e:
            return f"Error restarting instance: {str(e)[:200]}"
    
    async def _cmd_status(self, args: str, chat_id: int, topic_id: Optional[int] = None) -> str:
        """Show status of all instances."""
        instances = self.process_manager.list_instances()
        
        if not instances:
            return "No instances configured."
        
        lines = ["*Instance Status*\n"]
        running = stopped = crashed = 0
        
        for inst in instances:
            state_emoji = {
                InstanceState.RUNNING: "🟢",
                InstanceState.STARTING: "🟡",
                InstanceState.STOPPING: "🟠",
                InstanceState.STOPPED: "⚫",
                InstanceState.CRASHED: "🔴",
                InstanceState.UNREACHABLE: "⚪",
            }.get(inst.state, "❓")
            
            if inst.state == InstanceState.RUNNING:
                running += 1
            elif inst.state == InstanceState.STOPPED:
                stopped += 1
            elif inst.state == InstanceState.CRASHED:
                crashed += 1
            
            uptime = ""
            if inst.uptime_seconds:
                mins = int(inst.uptime_seconds / 60)
                uptime = f" ({mins}m)"
            
            lines.append(f"{state_emoji} `{inst.short_id}` {inst.display_name}{uptime}")
            if inst.error_message:
                lines.append(f"   Error: {inst.error_message[:50]}")
        
        lines.append(f"\n🟢 Running: {running} | ⚫ Stopped: {stopped} | 🔴 Crashed: {crashed}")
        
        return "\n".join(lines)
    
    async def _cmd_threads(
        self, args: str, chat_id: int, topic_id: Optional[int] = None
    ) -> Union[str, CommandResponse]:
        """List all thread-instance mappings for this chat."""
        topics = self.session_router.get_topics_for_chat(chat_id)
        
        if not topics:
            return "No threads mapped to instances yet.\n\nStart a reply thread and send a message to see the instance picker."
        
        lines = ["*Thread Mappings*\n"]
        current_topic_id = topic_id
        
        for tid, instance_id in sorted(topics, key=lambda x: x[0]):
            instance = self.process_manager.get_instance(instance_id)
            
            if instance:
                status = "🟢" if instance.is_alive else "⚫"
                name = instance.name or instance.directory.name
                short_id = instance_id[:8]
                marker = " ← you are here" if tid == current_topic_id else ""
                
                lines.append(f"{status} Thread `{tid}`: *{name}*{marker}")
                lines.append(f"   Instance: `{short_id}` | `{instance.directory.name}`")
            else:
                lines.append(f"⚪ Thread `{tid}`: _(instance removed)_")
            
            lines.append("")
        
        return "\n".join(lines).strip()
