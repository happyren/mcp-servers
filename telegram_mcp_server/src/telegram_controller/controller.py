"""Telegram Controller - Main daemon that orchestrates everything.

The Controller is a standalone daemon that:
1. Polls Telegram for incoming messages
2. Routes messages to the appropriate OpenCode instance
3. Spawns/manages multiple OpenCode instances
4. Sends responses back to Telegram
"""

import argparse
import asyncio
import json
import logging
import os
import signal
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Tuple, Union

import httpx

from telegram_mcp_server.config import get_settings, Settings
from telegram_mcp_server.telegram_client import TelegramClient

# Import bridge components for command handling
from telegram_bridge.opencode_client import OpenCodeClient
from telegram_bridge.command_handler import CommandHandler as BridgeCommandHandler
from telegram_bridge.command_handler import CommandResponse as BridgeCommandResponse

from .instance import InstanceState, OpenCodeInstance
from .process_manager import ProcessManager
from .session_router import SessionRouter
from .notifications import NotificationManager
from .handlers import ControllerCommands, CallbackHandler, MessageHandler
from .handlers.commands import CommandResponse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("telegram_controller")


# Constants
POLL_TIMEOUT = 30  # Long polling timeout in seconds
DEFAULT_MODEL_PROVIDER = "deepseek"
DEFAULT_MODEL_ID = "deepseek-reasoner"

# Default favourite models for model picker
DEFAULT_FAVOURITE_MODELS = [
    ("github-copilot", "claude-sonnet-4"),
    ("github-copilot", "gpt-4.1"),
    ("deepseek", "deepseek-reasoner"),
    ("deepseek", "deepseek-chat"),
    ("anthropic", "claude-sonnet-4-20250514"),
    ("github-copilot", "claude-opus-4.5"),
    ("github-copilot", "claude-sonnet-4.5"),
    ("moonshotai-cn", "kimi-k2.5"),
    ("minimax", "minimax-m2.1"),
    ("zhipuai-coding-plan", "GLM-4.7"),
]


class TelegramController:
    """Main controller daemon for Telegram-OpenCode integration.
    
    This is the core component that:
    - Runs as a standalone daemon (not inside OpenCode)
    - Polls Telegram for messages
    - Manages multiple OpenCode instances via ProcessManager
    - Routes messages to instances via SessionRouter
    - Handles controller-level commands (/open, /switch, /list, /kill)
    """
    
    def __init__(
        self,
        state_dir: Optional[Path] = None,
        default_provider: str = DEFAULT_MODEL_PROVIDER,
        default_model: str = DEFAULT_MODEL_ID,
    ):
        """Initialize the controller.
        
        Args:
            state_dir: Directory for state persistence
            default_provider: Default AI provider for new instances
            default_model: Default AI model for new instances
        """
        self.state_dir = state_dir or Path("~/.local/share/telegram_controller").expanduser()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        
        # Configuration
        self.settings = get_settings()
        self.default_provider = default_provider
        self.default_model = default_model
        
        # Core components
        self.process_manager = ProcessManager(
            state_dir=self.state_dir,
            on_instance_change=self._on_instance_change,
        )
        self.session_router = SessionRouter(state_dir=self.state_dir)
        
        # Telegram clients
        self.telegram = TelegramClient(
            bot_token=self.settings.bot_token,
            base_url=self.settings.api_base_url,
        )
        
        # HTTP clients for OpenCode instances
        self.http_clients: dict[str, httpx.AsyncClient] = {}
        
        # OpenCodeClient and CommandHandler per instance
        self.instance_clients: dict[str, OpenCodeClient] = {}
        self.instance_handlers: dict[str, BridgeCommandHandler] = {}
        
        # Per-chat state for command handlers
        self.chat_sessions: dict[int, str] = {}  # chat_id -> current session_id in instance
        
        # Favourite models for model picker
        self._favourite_models = self._load_favourite_models()
        
        # Polling state
        self.last_offset = self._load_offset()
        self.offset_file = self.state_dir / "polling_offset.json"
        self.queue_file = self.state_dir / "message_inbox.json"
        
        # Running state
        self.running = False
        self._shutdown_event = asyncio.Event()
        
        # Processed message tracking
        self.processed_ids: set[int] = set()
        
        # Initialize handler modules
        self._notification_manager = NotificationManager(self)
        self._command_handler = ControllerCommands(self)
        self._callback_handler = CallbackHandler(self)
        self._message_handler = MessageHandler(self)
        
        # Expose notification tracking for callback handler
        self._notified_pending = self._notification_manager.get_notified_pending()
        
        # Background polling task and update queue
        self._poll_task: Optional[asyncio.Task] = None
        self._update_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        
        # Bot info (populated on start)
        self.bot_username: str = ""
        self.bot_has_private_topics: bool = False
        
        logger.info(f"Controller initialized with state dir: {self.state_dir}")
    
    def _load_favourite_models(self) -> list[Tuple[str, str]]:
        """Load favourite models from environment or use defaults."""
        env_models = os.environ.get("TELEGRAM_FAVOURITE_MODELS", "")
        if env_models:
            models = []
            for entry in env_models.split(","):
                entry = entry.strip()
                if "/" in entry:
                    parts = entry.split("/", 1)
                    models.append((parts[0].strip(), parts[1].strip()))
            if models:
                return models
        return DEFAULT_FAVOURITE_MODELS
    
    def _load_offset(self) -> int:
        """Load the last polling offset."""
        offset_file = self.state_dir / "polling_offset.json"
        if not offset_file.exists():
            return 0
        try:
            with open(offset_file, "r") as f:
                data = json.load(f)
                return data.get("offset", 0)
        except Exception:
            return 0
    
    def _save_offset(self, offset: int) -> None:
        """Save the polling offset."""
        offset_file = self.state_dir / "polling_offset.json"
        with open(offset_file, "w") as f:
            json.dump({"offset": offset, "updated_at": datetime.now().isoformat()}, f)
    
    def _on_instance_change(self, instance: OpenCodeInstance) -> None:
        """Callback when an instance changes state."""
        logger.info(f"Instance {instance.short_id} changed to {instance.state.value}")
        
        # If instance stopped/crashed, notify connected chats
        if instance.state in (InstanceState.CRASHED, InstanceState.STOPPED):
            chat_ids = self.session_router.get_chats_for_instance(instance.id)
            for chat_id in chat_ids:
                asyncio.create_task(self._notify_instance_state(chat_id, instance))
    
    async def _notify_instance_state(self, chat_id: int, instance: OpenCodeInstance) -> None:
        """Notify a chat about instance state change."""
        try:
            if instance.state == InstanceState.CRASHED:
                text = f"OpenCode instance `{instance.short_id}` ({instance.display_name}) crashed.\n\n"
                if instance.error_message:
                    text += f"Error: {instance.error_message[:200]}\n\n"
                text += "Use `/list` to see available instances or `/open <path>` to start a new one."
            elif instance.state == InstanceState.STOPPED:
                text = f"OpenCode instance `{instance.short_id}` ({instance.display_name}) has stopped."
            else:
                return
            
            await self.telegram.send_message(chat_id=str(chat_id), text=text)
        except Exception as e:
            logger.error(f"Failed to notify chat {chat_id}: {e}")
    
    def _get_instance_client(self, instance: OpenCodeInstance) -> OpenCodeClient:
        """Get or create OpenCodeClient for an instance."""
        if instance.id not in self.instance_clients:
            client = OpenCodeClient(base_url=instance.url)
            self.instance_clients[instance.id] = client
        return self.instance_clients[instance.id]
    
    def _get_instance_handler(
        self,
        instance: OpenCodeInstance,
        chat_id: int,
    ) -> BridgeCommandHandler:
        """Get or create CommandHandler for an instance."""
        key = f"{instance.id}:{chat_id}"
        
        if key not in self.instance_handlers:
            client = self._get_instance_client(instance)
            session_id = self.chat_sessions.get(chat_id)
            
            def set_model_cb(provider: str, model: str) -> None:
                self.session_router.set_model_preference(chat_id, provider, model)
            
            def get_model_cb() -> Optional[Tuple[str, str]]:
                provider, model = self.session_router.get_model_preference(chat_id)
                if provider and model:
                    return (provider, model)
                return None
            
            handler = BridgeCommandHandler(
                opencode=client,
                current_session_id=session_id,
                set_model_callback=set_model_cb,
                get_model_callback=get_model_cb,
                favourite_models=self._favourite_models,
            )
            self.instance_handlers[key] = handler
        
        return self.instance_handlers[key]
    
    def _update_handler_session(self, chat_id: int, session_id: Optional[str]) -> None:
        """Update session ID in handler after session switch."""
        if session_id:
            self.chat_sessions[chat_id] = session_id
        elif chat_id in self.chat_sessions:
            del self.chat_sessions[chat_id]
        
        instance_id = self.session_router.get_current_instance_id(chat_id)
        if instance_id:
            key = f"{instance_id}:{chat_id}"
            if key in self.instance_handlers:
                self.instance_handlers[key].current_session_id = session_id
    
    async def start(self) -> None:
        """Start the controller daemon."""
        if self.running:
            return
        
        self.running = True
        self._shutdown_event.clear()
        
        # Check bot info and capabilities
        try:
            bot_info = await self.telegram.get_me()
            self.bot_username = bot_info.get("username", "")
            self.bot_has_private_topics = bot_info.get("has_topics_enabled", False)
            
            if self.bot_has_private_topics:
                logger.info(f"Bot @{self.bot_username} has private chat topics enabled")
            else:
                logger.info(f"Bot @{self.bot_username} - private chat topics NOT enabled")
                logger.info("Enable via BotFather: /mybots → Bot Settings → Topics in Private Chats")
        except Exception as e:
            logger.warning(f"Could not get bot info: {e}")
            self.bot_username = ""
            self.bot_has_private_topics = False
        
        # Start process manager
        await self.process_manager.start()
        
        # Start notification manager
        await self._notification_manager.start()
        
        # Start background polling task
        self._poll_task = asyncio.create_task(self._background_poll_loop())
        
        # Setup signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._handle_signal)
        
        logger.info("Controller started")
    
    async def stop(self) -> None:
        """Stop the controller daemon gracefully."""
        logger.info("Stopping controller...")
        self.running = False
        self._shutdown_event.set()
        
        # Cancel background polling task
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        
        # Stop notification manager
        await self._notification_manager.stop()
        
        # Stop process manager
        await self.process_manager.stop()
        
        # Close HTTP clients
        for client in self.http_clients.values():
            await client.aclose()
        self.http_clients.clear()
        
        # Close instance clients
        for client in self.instance_clients.values():
            await client.close()
        self.instance_clients.clear()
        self.instance_handlers.clear()
        
        # Close Telegram client
        await self.telegram.close()
        
        logger.info("Controller stopped")
    
    def _handle_signal(self) -> None:
        """Handle shutdown signals."""
        logger.info("Received shutdown signal")
        self._shutdown_event.set()
    
    async def run(self) -> None:
        """Main run loop."""
        await self.start()
        
        try:
            while self.running and not self._shutdown_event.is_set():
                try:
                    try:
                        update = await asyncio.wait_for(
                            self._update_queue.get(),
                            timeout=1.0
                        )
                    except asyncio.TimeoutError:
                        continue
                    
                    asyncio.create_task(self._process_update(update))
                    
                except Exception as e:
                    logger.error(f"Error in main loop: {e}")
                    await asyncio.sleep(0.1)
        finally:
            await self.stop()
    
    async def _background_poll_loop(self) -> None:
        """Background task that polls Telegram and puts updates into a queue."""
        logger.info("Background polling started")
        
        while self.running and not self._shutdown_event.is_set():
            try:
                updates = await self.telegram.get_updates_with_callbacks(
                    offset=self.last_offset,
                    limit=100,
                    timeout=POLL_TIMEOUT,
                )
                
                if not updates:
                    continue
                
                new_offset = self.last_offset
                
                for update in updates:
                    update_id = update.get("update_id", 0)
                    if update_id >= new_offset:
                        new_offset = update_id + 1
                    
                    await self._update_queue.put(update)
                
                if new_offset != self.last_offset:
                    self.last_offset = new_offset
                    self._save_offset(new_offset)
                    
            except asyncio.CancelledError:
                logger.info("Background polling cancelled")
                break
            except Exception as e:
                logger.error(f"Polling error: {e}")
                await asyncio.sleep(1)
        
        logger.info("Background polling stopped")
    
    async def _process_update(self, update: dict[str, Any]) -> None:
        """Process a single Telegram update."""
        try:
            if msg := update.get("message"):
                await self._handle_message(msg)
            
            if callback := update.get("callback_query"):
                await self._callback_handler.handle(callback)
        except Exception as e:
            logger.error(f"Error processing update: {e}")
    
    async def _handle_message(self, msg: dict[str, Any]) -> None:
        """Handle an incoming Telegram message."""
        msg_id = msg.get("message_id", 0)
        if msg_id in self.processed_ids:
            return
        
        chat = msg.get("chat", {})
        chat_id = chat.get("id", 0)
        is_forum = chat.get("is_forum", False)
        text = msg.get("text", "")
        from_user = msg.get("from", {})
        username = from_user.get("username", "Unknown")
        
        # Extract topic ID for forum groups
        topic_id: Optional[int] = None
        is_known_forum = self.session_router.is_forum_chat(chat_id)
        has_thread_id = msg.get("message_thread_id") is not None
        
        if is_forum or msg.get("is_topic_message") or has_thread_id or is_known_forum:
            topic_id = msg.get("message_thread_id")
            if is_forum:
                self.session_router.mark_chat_as_forum(chat_id)
        
        # Handle forum topic service messages
        if msg.get("forum_topic_created"):
            await self._handle_topic_created(chat_id, msg)
            self.processed_ids.add(msg_id)
            return
        
        if msg.get("forum_topic_closed"):
            await self._handle_topic_closed(chat_id, topic_id, msg)
            self.processed_ids.add(msg_id)
            return
        
        if msg.get("forum_topic_reopened"):
            await self._handle_topic_reopened(chat_id, topic_id, msg)
            self.processed_ids.add(msg_id)
            return
        
        if not text:
            self.processed_ids.add(msg_id)
            return
        
        logger.info(f"Message from @{username} in chat {chat_id} (topic={topic_id}): {text[:50]}...")
        
        try:
            # Check if it's a controller command
            response = await self._handle_controller_command(text, chat_id, topic_id)
            
            if response is not None:
                await self._send_response(chat_id, response, topic_id)
            else:
                # Not a controller command, forward to OpenCode instance
                await self._message_handler.forward_to_instance(chat_id, text, username, topic_id)
            
            self.processed_ids.add(msg_id)
            
        except Exception as e:
            logger.error(f"Error handling message: {e}")
            try:
                await self._send_text(chat_id, f"Error: {str(e)[:200]}", topic_id)
            except Exception:
                pass
    
    async def _send_response(
        self,
        chat_id: int,
        response: Union[str, CommandResponse],
        topic_id: Optional[int] = None,
    ) -> None:
        """Send a response to a chat or topic."""
        if isinstance(response, CommandResponse):
            if response.keyboard:
                if topic_id is not None:
                    await self.telegram.send_message_with_keyboard_to_topic(
                        chat_id=str(chat_id),
                        message_thread_id=topic_id,
                        text=response.text,
                        inline_keyboard=response.keyboard,
                    )
                else:
                    await self.telegram.send_message_with_keyboard(
                        chat_id=str(chat_id),
                        text=response.text,
                        inline_keyboard=response.keyboard,
                    )
            else:
                await self._send_text(chat_id, response.text, topic_id)
        else:
            await self._send_text(chat_id, str(response), topic_id)
    
    async def _send_text(
        self,
        chat_id: int,
        text: str,
        topic_id: Optional[int] = None,
    ) -> None:
        """Send a text message to a chat or topic."""
        try:
            if topic_id is not None:
                await self.telegram.send_message_to_topic(
                    chat_id=str(chat_id),
                    message_thread_id=topic_id,
                    text=text,
                )
            else:
                await self.telegram.send_message(
                    chat_id=str(chat_id),
                    text=text,
                )
        except Exception as e:
            error_msg = str(e).lower()
            if topic_id is not None and ("thread not found" in error_msg or "message_thread_id" in error_msg):
                logger.warning(f"Topic {topic_id} in chat {chat_id} appears to be deleted, cleaning up mapping")
                self.session_router.clear_topic_instance(chat_id, topic_id)
                self.session_router.clear_current_instance(chat_id, topic_id)
            raise
    
    async def _handle_topic_created(self, chat_id: int, msg: dict[str, Any]) -> None:
        """Handle forum topic created service message."""
        topic_data = msg.get("forum_topic_created", {})
        topic_id = msg.get("message_thread_id")
        topic_name = topic_data.get("name", "")
        logger.info(f"Topic created in chat {chat_id}: {topic_name} (id={topic_id})")
    
    async def _handle_topic_closed(self, chat_id: int, topic_id: Optional[int], msg: dict[str, Any]) -> None:
        """Handle forum topic closed service message."""
        logger.info(f"Topic closed in chat {chat_id}: topic_id={topic_id}")
        
        if topic_id is None:
            return
        
        instance_id = self.session_router.get_instance_for_topic(chat_id, topic_id)
        if instance_id:
            instance = self.process_manager.get_instance(instance_id)
            if instance and instance.is_alive:
                logger.info(f"Stopping instance {instance.short_id} because topic was closed")
                await self.process_manager.stop_instance(instance_id)
            
            self.session_router.clear_topic_instance(chat_id, topic_id)
    
    async def _handle_topic_reopened(self, chat_id: int, topic_id: Optional[int], msg: dict[str, Any]) -> None:
        """Handle forum topic reopened service message."""
        logger.info(f"Topic reopened in chat {chat_id}: topic_id={topic_id}")
    
    async def _handle_controller_command(
        self,
        text: str,
        chat_id: int,
        topic_id: Optional[int] = None,
    ) -> Optional[Union[str, CommandResponse]]:
        """Handle controller-level commands."""
        text = text.strip()
        if not text.startswith("/"):
            return None
        
        parts = text[1:].split(None, 1)
        cmd = parts[0].lower()
        
        # Check if it's a controller command
        response = await self._command_handler.handle(text, chat_id, topic_id)
        if response is not None:
            return response
        
        # Check if it's an instance command
        if cmd in self._command_handler.get_instance_commands():
            return await self._forward_command_to_instance(text, chat_id, topic_id)
        
        return None
    
    async def _forward_command_to_instance(
        self,
        text: str,
        chat_id: int,
        topic_id: Optional[int] = None,
    ) -> Optional[Union[str, CommandResponse]]:
        """Forward a command to the current instance's command handler."""
        instance_id = self.session_router.get_current_instance_id(chat_id, topic_id)
        
        if not instance_id:
            return "No instance selected.\n\nUse `/open <path>` to open a project or `/list` to see available instances."
        
        instance = self.process_manager.get_instance(instance_id)
        if not instance or not instance.is_alive:
            return f"Instance `{instance_id[:8]}` is not running.\n\nUse `/list` to select a different instance."
        
        handler = self._get_instance_handler(instance, chat_id)
        
        try:
            result = await handler.handle_command(text, chat_id)
            
            if result is None:
                return None
            
            if isinstance(result, BridgeCommandResponse):
                return CommandResponse(text=result.text, keyboard=result.keyboard)
            
            if handler.current_session_id:
                self._update_handler_session(chat_id, handler.current_session_id)
            
            return str(result)
            
        except Exception as e:
            logger.error(f"Error forwarding command to instance: {e}")
            return f"Error: {str(e)[:200]}"
    
    async def _rename_topic(self, chat_id: int, topic_id: int, name: str) -> bool:
        """Rename a topic to the given name."""
        try:
            await self.telegram.edit_forum_topic(
                chat_id=str(chat_id),
                message_thread_id=topic_id,
                name=name[:128],
            )
            logger.info(f"Renamed thread {topic_id} in chat {chat_id} to '{name}'")
            return True
        except Exception as e:
            logger.debug(f"Could not rename thread {topic_id}: {e}")
            return False
    
    # Delegate to notification manager
    async def _check_pending_for_instance(
        self,
        instance: OpenCodeInstance,
        chat_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        """Check for pending questions/permissions for a specific instance and notify."""
        await self._notification_manager.check_pending_for_instance(instance, chat_id, topic_id)
    
    # Delegate to message handler
    async def _poll_and_forward_response(
        self,
        instance: OpenCodeInstance,
        chat_id: int,
        topic_id: Optional[int] = None,
        timeout: float = 300.0,
    ) -> None:
        """Poll for OpenCode response after answering a question and forward to Telegram."""
        await self._message_handler.poll_and_forward_response(instance, chat_id, topic_id, timeout)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Telegram Controller - Manage multiple OpenCode instances via Telegram",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start the controller daemon
  telegram-controller

  # Start with custom state directory
  telegram-controller --state-dir ~/.telegram-controller

  # Start with specific default model
  telegram-controller --provider anthropic --model claude-sonnet-4-20250514

Environment variables:
  TELEGRAM_BOT_TOKEN   - Bot token (required)
  TELEGRAM_CHAT_ID     - Default chat ID (optional)
        """,
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="Directory for state persistence (default: ~/.local/share/telegram_controller)",
    )
    parser.add_argument(
        "--provider",
        default=os.environ.get("TELEGRAM_PROVIDER", DEFAULT_MODEL_PROVIDER),
        help=f"Default AI provider (default: {DEFAULT_MODEL_PROVIDER})",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("TELEGRAM_MODEL", DEFAULT_MODEL_ID),
        help=f"Default AI model (default: {DEFAULT_MODEL_ID})",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    
    return parser.parse_args()


async def async_main() -> None:
    """Async entry point."""
    args = parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    controller = TelegramController(
        state_dir=args.state_dir,
        default_provider=args.provider,
        default_model=args.model,
    )
    
    await controller.run()


def main() -> None:
    """Sync entry point."""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")


if __name__ == "__main__":
    main()
