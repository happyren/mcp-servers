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
import subprocess
import webbrowser
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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("telegram_controller")


# Constants
POLL_TIMEOUT = 30  # Long polling timeout in seconds
TYPING_INTERVAL = 4.0  # Send typing indicator every N seconds
PENDING_CHECK_INTERVAL = 10.0  # Check for pending permissions/questions every N seconds
DEFAULT_MODEL_PROVIDER = "github-copilot"
DEFAULT_MODEL_ID = "claude-sonnet-4"

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


@dataclass
class CommandResponse:
    """Response from a command handler."""
    text: str
    keyboard: Optional[list[list[dict[str, str]]]] = None


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
        
        # Pending notifications tracking (request_id -> set of chat_ids already notified)
        self._notified_pending: dict[str, set[int]] = {}
        
        # Background task for pending checks
        self._pending_check_task: Optional[asyncio.Task] = None
        
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
                # Queue notification (will be sent on next poll cycle)
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
        """Get or create CommandHandler for an instance.
        
        Each chat gets its own handler to track session state per chat.
        """
        # Key includes both instance and chat for per-chat session tracking
        key = f"{instance.id}:{chat_id}"
        
        if key not in self.instance_handlers:
            client = self._get_instance_client(instance)
            
            # Get the current session for this chat in this instance
            session_id = self.chat_sessions.get(chat_id)
            
            # Create callbacks for model management
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
        
        # Update all handlers for this chat
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
        
        # Start pending notifications check loop
        self._pending_check_task = asyncio.create_task(self._pending_check_loop())
        
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
        
        # Cancel pending check task
        if self._pending_check_task:
            self._pending_check_task.cancel()
            try:
                await self._pending_check_task
            except asyncio.CancelledError:
                pass
        
        # Stop process manager (stops all instances)
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
    
    async def _pending_check_loop(self) -> None:
        """Background task to check for pending permissions and questions.
        
        Polls all running instances for pending items and notifies
        connected Telegram chats.
        """
        while self.running:
            try:
                await self._check_all_pending()
            except Exception as e:
                logger.error(f"Error in pending check loop: {e}")
            
            await asyncio.sleep(PENDING_CHECK_INTERVAL)
    
    async def _check_all_pending(self) -> None:
        """Check all instances for pending permissions/questions."""
        running_instances = self.process_manager.get_running_instances()
        
        for instance in running_instances:
            try:
                # Get chats connected to this instance (non-topic mode)
                chat_ids = self.session_router.get_chats_for_instance(instance.id)
                # Get topics connected to this instance (thread mode)
                topic_mappings = self.session_router.get_topics_for_instance(instance.id)
                
                if not chat_ids and not topic_mappings:
                    continue
                
                client = self._get_instance_client(instance)
                
                # Check pending permissions
                try:
                    permissions = await asyncio.wait_for(
                        client.list_pending_permissions(),
                        timeout=5.0
                    )
                    for perm in permissions:
                        await self._notify_pending_permission(instance, perm, chat_ids, topic_mappings)
                except asyncio.TimeoutError:
                    logger.debug(f"Timeout checking permissions for {instance.short_id}")
                except Exception as e:
                    logger.debug(f"Error checking permissions for {instance.short_id}: {e}")
                
                # Check pending questions
                try:
                    questions = await asyncio.wait_for(
                        client.list_pending_questions(),
                        timeout=5.0
                    )
                    for question in questions:
                        await self._notify_pending_question(instance, question, chat_ids, topic_mappings)
                except asyncio.TimeoutError:
                    logger.debug(f"Timeout checking questions for {instance.short_id}")
                except Exception as e:
                    logger.debug(f"Error checking questions for {instance.short_id}: {e}")
                    
            except Exception as e:
                logger.debug(f"Error checking pending for {instance.short_id}: {e}")
    
    async def _notify_pending_permission(
        self,
        instance: OpenCodeInstance,
        permission: dict[str, Any],
        chat_ids: list[int],
        topic_mappings: Optional[list[tuple[int, int]]] = None,
    ) -> None:
        """Notify chats about a pending permission request."""
        request_id = permission.get("id", "")
        if not request_id:
            return
        
        if topic_mappings is None:
            topic_mappings = []
        
        # Check which chats/topics we've already notified
        # Use set[Any] since we store both int (chat_id) and tuple[int, int] (chat_id, topic_id)
        if request_id not in self._notified_pending:
            self._notified_pending[request_id] = set()
        
        notified: set[Any] = self._notified_pending[request_id]
        
        # Build message
        perm_type = permission.get("permission", "unknown")
        patterns = permission.get("patterns", [])
        pattern_text = ", ".join(str(p)[:50] for p in patterns[:3])
        if len(patterns) > 3:
            pattern_text += f" (+{len(patterns) - 3} more)"
        
        text = f"🔐 *Permission Request* ({instance.display_name})\n\n"
        text += f"Type: `{perm_type}`\n"
        if pattern_text:
            text += f"Pattern: `{pattern_text}`\n"
        
        # Build keyboard with permission options
        keyboard = [[
            {"text": "✅ Allow", "callback_data": f"perm:y:{request_id}"},
            {"text": "♾️ Always", "callback_data": f"perm:a:{request_id}"},
            {"text": "❌ Reject", "callback_data": f"perm:n:{request_id}"},
        ]]
        
        # Notify topic-mapped chats first (thread mode)
        for chat_id, topic_id in topic_mappings:
            notif_key = (chat_id, topic_id)
            if notif_key in notified:
                continue
            
            try:
                await self.telegram.send_message_with_keyboard_to_topic(
                    chat_id=str(chat_id),
                    message_thread_id=topic_id,
                    text=text,
                    inline_keyboard=keyboard,
                )
                notified.add(notif_key)
                logger.info(f"Sent permission notification to chat {chat_id} topic {topic_id}")
            except Exception as e:
                logger.error(f"Failed to send permission notification to topic: {e}")
        
        # Notify non-topic chats (legacy mode)
        for chat_id in chat_ids:
            if chat_id in notified:
                continue
            
            try:
                await self.telegram.send_message_with_keyboard(
                    chat_id=str(chat_id),
                    text=text,
                    inline_keyboard=keyboard,
                )
                notified.add(chat_id)
                logger.info(f"Sent permission notification to chat {chat_id}")
            except Exception as e:
                logger.error(f"Failed to send permission notification: {e}")
    
    async def _notify_pending_question(
        self,
        instance: OpenCodeInstance,
        question: dict[str, Any],
        chat_ids: list[int],
        topic_mappings: Optional[list[tuple[int, int]]] = None,
    ) -> None:
        """Notify chats about a pending question."""
        request_id = question.get("id", "")
        if not request_id:
            return
        
        if topic_mappings is None:
            topic_mappings = []
        
        # Check which chats/topics we've already notified
        # Use set[Any] since we store both int (chat_id) and tuple[int, int] (chat_id, topic_id)
        if request_id not in self._notified_pending:
            self._notified_pending[request_id] = set()
        
        notified: set[Any] = self._notified_pending[request_id]
        
        # Build message from questions
        q_list = question.get("questions", [])
        if not q_list:
            return
        
        q = q_list[0]  # Handle first question
        header = q.get("header", "Question")
        question_text = q.get("question", "")
        options = q.get("options", [])
        
        text = f"❓ *{header}* ({instance.display_name})\n\n"
        text += question_text
        
        # Build keyboard with options
        keyboard: list[list[dict[str, str]]] = []
        for idx, opt in enumerate(options[:6]):  # Max 6 options
            label = opt.get("label", f"Option {idx + 1}")
            keyboard.append([{
                "text": label[:30],  # Truncate long labels
                "callback_data": f"q:{request_id}:{idx}",
            }])
        
        # Notify topic-mapped chats first (thread mode)
        for chat_id, topic_id in topic_mappings:
            notif_key = (chat_id, topic_id)
            if notif_key in notified:
                continue
            
            try:
                await self.telegram.send_message_with_keyboard_to_topic(
                    chat_id=str(chat_id),
                    message_thread_id=topic_id,
                    text=text,
                    inline_keyboard=keyboard,
                )
                notified.add(notif_key)
                logger.info(f"Sent question notification to chat {chat_id} topic {topic_id}")
            except Exception as e:
                logger.error(f"Failed to send question notification to topic: {e}")
        
        # Notify non-topic chats (legacy mode)
        for chat_id in chat_ids:
            if chat_id in notified:
                continue
            
            try:
                await self.telegram.send_message_with_keyboard(
                    chat_id=str(chat_id),
                    text=text,
                    inline_keyboard=keyboard,
                )
                notified.add(chat_id)
                logger.info(f"Sent question notification to chat {chat_id}")
            except Exception as e:
                logger.error(f"Failed to send question notification: {e}")

    async def _check_pending_for_instance(
        self,
        instance: OpenCodeInstance,
        chat_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        """Check for pending questions/permissions for a specific instance and notify.
        
        This is called immediately after sending a message for faster feedback.
        """
        try:
            client = self._get_instance_client(instance)
            
            # Determine notification targets
            if topic_id is not None:
                topic_mappings = [(chat_id, topic_id)]
                chat_ids: list[int] = []
            else:
                topic_mappings = []
                chat_ids = [chat_id]
            
            # Check pending permissions
            try:
                permissions = await asyncio.wait_for(
                    client.list_pending_permissions(),
                    timeout=3.0
                )
                for perm in permissions:
                    await self._notify_pending_permission(instance, perm, chat_ids, topic_mappings)
            except asyncio.TimeoutError:
                logger.debug(f"Timeout checking permissions for {instance.short_id}")
            except Exception as e:
                logger.debug(f"Error checking permissions for {instance.short_id}: {e}")
            
            # Check pending questions
            try:
                questions = await asyncio.wait_for(
                    client.list_pending_questions(),
                    timeout=3.0
                )
                for question in questions:
                    await self._notify_pending_question(instance, question, chat_ids, topic_mappings)
            except asyncio.TimeoutError:
                logger.debug(f"Timeout checking questions for {instance.short_id}")
            except Exception as e:
                logger.debug(f"Error checking questions for {instance.short_id}: {e}")
                
        except Exception as e:
            logger.debug(f"Error in immediate pending check: {e}")

    async def run(self) -> None:
        """Main run loop."""
        await self.start()
        
        try:
            while self.running and not self._shutdown_event.is_set():
                try:
                    await self._poll_and_process()
                except Exception as e:
                    logger.error(f"Error in main loop: {e}")
                    await asyncio.sleep(1)
        finally:
            await self.stop()
    
    async def _poll_and_process(self) -> None:
        """Poll Telegram and process messages."""
        try:
            updates = await self.telegram.get_updates_with_callbacks(
                offset=self.last_offset,
                limit=100,
                timeout=POLL_TIMEOUT,
            )
            
            if not updates:
                return
            
            new_offset = self.last_offset
            
            for update in updates:
                update_id = update.get("update_id", 0)
                if update_id >= new_offset:
                    new_offset = update_id + 1
                
                # Handle message updates
                if msg := update.get("message"):
                    await self._handle_message(msg)
                
                # Handle callback queries (button clicks)
                if callback := update.get("callback_query"):
                    await self._handle_callback(callback)
            
            if new_offset != self.last_offset:
                self.last_offset = new_offset
                self._save_offset(new_offset)
                
        except Exception as e:
            logger.error(f"Polling error: {e}")
    
    async def _handle_message(self, msg: dict[str, Any]) -> None:
        """Handle an incoming Telegram message."""
        msg_id = msg.get("message_id", 0)
        if msg_id in self.processed_ids:
            return
        
        chat = msg.get("chat", {})
        chat_id = chat.get("id", 0)
        chat_type = chat.get("type", "")
        is_forum = chat.get("is_forum", False)
        text = msg.get("text", "")
        from_user = msg.get("from", {})
        username = from_user.get("username", "Unknown")
        
        # Extract topic ID for forum groups
        # Check multiple conditions for forum detection:
        # 1. chat.is_forum flag (most reliable)
        # 2. is_topic_message flag on the message
        # 3. message_thread_id present (even without flags)
        # 4. Chat previously identified as forum
        topic_id: Optional[int] = None
        is_known_forum = self.session_router.is_forum_chat(chat_id)
        has_thread_id = msg.get("message_thread_id") is not None
        
        if is_forum or msg.get("is_topic_message") or has_thread_id or is_known_forum:
            topic_id = msg.get("message_thread_id")
            # Mark this chat as a forum for future reference
            if is_forum:
                self.session_router.mark_chat_as_forum(chat_id)
            
            # If we're in a forum but no topic_id, it might be the General topic
            # The General topic has message_thread_id=1, but sometimes Telegram
            # doesn't include it in messages sent to General
            if topic_id is None and (is_forum or is_known_forum):
                # Route to chat-level instance (General topic behavior)
                pass
        
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
                # It was a controller command
                await self._send_response(chat_id, response, topic_id)
            else:
                # Not a controller command, forward to OpenCode instance
                await self._forward_to_instance(chat_id, text, username, topic_id)
            
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
        """Send a text message to a chat or topic.
        
        If sending to a topic fails with 'message thread not found', the topic
        mapping will be cleaned up automatically.
        """
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
            # Check if the error indicates the topic was deleted
            if topic_id is not None and ("thread not found" in error_msg or "message_thread_id" in error_msg):
                logger.warning(f"Topic {topic_id} in chat {chat_id} appears to be deleted, cleaning up mapping")
                # Clean up the topic mapping
                self.session_router.clear_topic_instance(chat_id, topic_id)
                self.session_router.clear_current_instance(chat_id, topic_id)
            # Re-raise the exception
            raise
    
    async def _handle_topic_created(self, chat_id: int, msg: dict[str, Any]) -> None:
        """Handle forum topic created service message."""
        topic_data = msg.get("forum_topic_created", {})
        topic_id = msg.get("message_thread_id")
        topic_name = topic_data.get("name", "")
        
        logger.info(f"Topic created in chat {chat_id}: {topic_name} (id={topic_id})")
        # Topic creation is handled - no action needed here
        # User can use /open in the topic to attach a project
    
    async def _handle_topic_closed(self, chat_id: int, topic_id: Optional[int], msg: dict[str, Any]) -> None:
        """Handle forum topic closed service message."""
        logger.info(f"Topic closed in chat {chat_id}: topic_id={topic_id}")
        
        if topic_id is None:
            return
        
        # Get the instance mapped to this topic
        instance_id = self.session_router.get_instance_for_topic(chat_id, topic_id)
        if instance_id:
            # Stop the instance when topic is closed
            instance = self.process_manager.get_instance(instance_id)
            if instance and instance.is_alive:
                logger.info(f"Stopping instance {instance.short_id} because topic was closed")
                await self.process_manager.stop_instance(instance_id)
            
            # Clear the topic mapping
            self.session_router.clear_topic_instance(chat_id, topic_id)
    
    async def _handle_topic_reopened(self, chat_id: int, topic_id: Optional[int], msg: dict[str, Any]) -> None:
        """Handle forum topic reopened service message."""
        logger.info(f"Topic reopened in chat {chat_id}: topic_id={topic_id}")
        # Could restart the instance here, but let user do it manually with /open
    
    async def _safe_answer_callback(
        self,
        callback_id: str,
        text: str = "",
        show_alert: bool = False,
    ) -> bool:
        """Safely answer a callback query, ignoring errors if expired.
        
        Returns True if answered successfully, False otherwise.
        """
        try:
            await self.telegram.answer_callback_query(callback_id, text=text, show_alert=show_alert)
            return True
        except Exception as e:
            # Callback may have expired or already been answered - this is fine
            logger.debug(f"Could not answer callback (likely expired): {e}")
            return False
    
    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        """Handle a callback query (button click)."""
        callback_id = callback.get("id", "")
        data = callback.get("data", "")
        from_user = callback.get("from", {})
        message = callback.get("message", {})
        chat = message.get("chat", {})
        chat_id = chat.get("id", 0)
        original_msg_id = message.get("message_id", 0)
        
        # Extract topic_id from the message if it's in a forum topic
        # Check multiple conditions for forum detection
        topic_id: Optional[int] = None
        is_forum = chat.get("is_forum", False)
        is_known_forum = self.session_router.is_forum_chat(chat_id)
        has_thread_id = message.get("message_thread_id") is not None
        
        if message.get("is_topic_message") or is_forum or has_thread_id or is_known_forum:
            topic_id = message.get("message_thread_id")
            # Mark this chat as a forum for future reference
            if is_forum:
                self.session_router.mark_chat_as_forum(chat_id)
        
        logger.info(f"Callback from {from_user.get('username')}: {data} (topic={topic_id})")
        
        try:
            # Ignore placeholder callbacks
            if data == "ignore":
                await self._safe_answer_callback(callback_id)
                return
            
            # Handle instance selection: instance:<id>
            if data.startswith("instance:"):
                instance_id = data[9:]
                await self._switch_to_instance(chat_id, instance_id, callback_id, original_msg_id, topic_id)
                return
            
            # Handle instance kill: kill:<id>
            if data.startswith("kill:"):
                instance_id = data[5:]
                await self._kill_instance(chat_id, instance_id, callback_id, original_msg_id, topic_id)
                return
            
            # Handle session selection: session:<id>
            if data.startswith("session:"):
                session_id = data[8:]
                await self._switch_to_session(chat_id, session_id, callback_id, original_msg_id, topic_id)
                return
            
            # Handle model selection: setmodel:<provider>:<model> or sm:<hash>
            if data.startswith("setmodel:") or data.startswith("sm:"):
                await self._handle_model_selection(chat_id, data, callback_id, original_msg_id, topic_id)
                return
            
            # Handle session delete: delete:<id>
            if data.startswith("delete:"):
                session_id = data[7:]
                await self._handle_session_delete(chat_id, session_id, callback_id, original_msg_id, topic_id)
                return
            
            # Handle permission response: perm:<y|a|n>:<request_id>
            if data.startswith("perm:"):
                await self._handle_permission_callback(chat_id, data, callback_id, original_msg_id, topic_id)
                return
            
            # Handle question response: q:<request_id>:<option_idx>
            if data.startswith("q:"):
                await self._handle_question_callback(chat_id, data, callback_id, original_msg_id, topic_id)
                return
            
            # Handle thread instance selection: thread_inst:<topic_id>:<instance_id>
            if data.startswith("thread_inst:"):
                parts = data[12:].split(":", 1)
                if len(parts) == 2:
                    thread_id = int(parts[0])
                    instance_id = parts[1]
                    await self._handle_thread_instance_selection(
                        chat_id, thread_id, instance_id, callback_id, original_msg_id
                    )
                return
            
            # Answer unknown callbacks
            await self._safe_answer_callback(callback_id)
            
        except Exception as e:
            logger.error(f"Error handling callback: {e}")
            # Try to answer with error, but don't fail if callback expired
            await self._safe_answer_callback(callback_id, text=f"Error: {str(e)[:100]}", show_alert=True)
    
    async def _handle_controller_command(
        self,
        text: str,
        chat_id: int,
        topic_id: Optional[int] = None,
    ) -> Optional[Union[str, CommandResponse]]:
        """Handle controller-level commands.
        
        Returns None if text is not a controller command.
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
            "projects": self._cmd_list,  # Alias - show running instances
            "instances": self._cmd_list,  # Alias
            "kill": self._cmd_kill,
            "stop": self._cmd_kill,  # Alias
            "close": self._cmd_close,  # Stop current instance
            "restart": self._cmd_restart,
            "status": self._cmd_status,
            "help": self._cmd_help,
            "current": self._cmd_current,
            "threads": self._cmd_threads,  # List thread-instance mappings
        }
        
        handler = handlers.get(cmd)
        if handler:
            return await handler(args, chat_id, topic_id)
        
        # Instance-level commands - forward to current instance's handler
        instance_commands = {
            "sessions", "session", "models", "agents", "config",
            "files", "read", "find", "findfile", "find-symbol", "find_symbol",
            "prompt", "shell", "diff", "todo", "fork", "abort", "delete",
            "share", "unshare", "revert", "unrevert", "summarize",
            "info", "messages", "init", "pending", "health",
            "vcs", "lsp", "formatter", "mcp", "dispose", "commands",
            "directory", "project",
        }
        
        if cmd in instance_commands:
            return await self._forward_command_to_instance(text, chat_id, topic_id)
        
        # Unknown command
        return None
    
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
    
    async def _forward_command_to_instance(
        self,
        text: str,
        chat_id: int,
        topic_id: Optional[int] = None,
    ) -> Optional[Union[str, CommandResponse]]:
        """Forward a command to the current instance's command handler."""
        # Get current instance for this chat/topic
        instance_id = self.session_router.get_current_instance_id(chat_id, topic_id)
        
        if not instance_id:
            return "No instance selected.\n\nUse `/open <path>` to open a project or `/list` to see available instances."
        
        instance = self.process_manager.get_instance(instance_id)
        if not instance or not instance.is_alive:
            return f"Instance `{instance_id[:8]}` is not running.\n\nUse `/list` to select a different instance."
        
        # Get handler for this instance and chat
        handler = self._get_instance_handler(instance, chat_id)
        
        try:
            result = await handler.handle_command(text, chat_id)
            
            if result is None:
                # Not a recognized command
                return None
            
            # Convert BridgeCommandResponse to our CommandResponse
            if isinstance(result, BridgeCommandResponse):
                return CommandResponse(text=result.text, keyboard=result.keyboard)
            
            # Update session tracking if handler's session changed
            if handler.current_session_id:
                self._update_handler_session(chat_id, handler.current_session_id)
            
            return str(result)
            
        except Exception as e:
            logger.error(f"Error forwarding command to instance: {e}")
            return f"Error: {str(e)[:200]}"
    
    async def _cmd_open(self, args: str, chat_id: int, topic_id: Optional[int] = None) -> str:
        """Open a project directory, spawning a new OpenCode instance.
        
        If used in a thread (reply thread), maps the instance to that thread.
        If used in main chat, maps the instance to the main chat context.
        
        Usage: /open <path>
        """
        if not args:
            return "Usage: `/open <path>`\n\nExample: `/open ~/projects/my-app`"
        
        # Parse path (ignore any extra args for now)
        path_str = args.split()[0]
        path = Path(path_str).expanduser().resolve()
        
        if not path.exists():
            return f"Directory does not exist: `{path}`"
        
        if not path.is_dir():
            return f"Not a directory: `{path}`"
        
        project_name = path.name
        
        logger.info(f"_cmd_open: chat_id={chat_id}, topic_id={topic_id}, path={path}")
        
        # Get or spawn instance
        instance = await self._get_or_spawn_instance(path)
        if isinstance(instance, str):
            # Error message returned
            return instance
        
        # Map to current context (thread or main chat)
        self.session_router.set_current_instance(chat_id, instance, topic_id=topic_id)
        
        # If this is a thread, create 1:1 mapping
        if topic_id is not None:
            self.session_router.set_topic_instance(chat_id, topic_id, instance.id)
            self.session_router._save_state()
            await self._rename_topic(chat_id, topic_id, project_name)
            return f"📁 Connected thread to *{project_name}*\n\nPath: `{path}`\nInstance: `{instance.short_id}`\n\nSend any message to chat with OpenCode."
        
        return f"📁 Opened *{project_name}*\n\nPath: `{path}`\nInstance: `{instance.short_id}` on port {instance.port}\n\nSend any message to chat with OpenCode."
    
    async def _get_or_spawn_instance(self, path: Path) -> Union[OpenCodeInstance, str]:
        """Get existing instance for directory or spawn a new one.
        
        Returns the instance on success, or an error message string on failure.
        """
        # Check if instance already exists for this directory
        existing = self.process_manager.get_instance_by_directory(path)
        if existing and existing.is_alive:
            return existing
        
        # Spawn new instance
        try:
            instance = await self.process_manager.spawn_instance(
                directory=path,
                name=path.name,
                provider_id=self.default_provider,
                model_id=self.default_model,
            )
            
            if instance.state != InstanceState.RUNNING:
                return f"Failed to start OpenCode instance: {instance.error_message or 'Unknown error'}"
            
            return instance
            
        except Exception as e:
            return f"Failed to spawn instance: {str(e)[:200]}"
    
    async def _cmd_list(self, args: str, chat_id: int, topic_id: Optional[int] = None) -> Union[str, CommandResponse]:
        """List all running instances (also aliased as /projects).
        
        Verifies each instance is still running and removes dead ones.
        """
        # Get all instances and verify their status
        all_instances = self.process_manager.list_instances()
        
        # Check each instance and remove dead ones
        instances_to_remove = []
        running_instances = []
        
        for inst in all_instances:
            if inst.state in (InstanceState.STOPPED, InstanceState.CRASHED):
                # Already marked as dead, remove from list
                instances_to_remove.append(inst.id)
            elif inst.process and inst.process.returncode is not None:
                # Process has exited but state not updated
                instances_to_remove.append(inst.id)
            elif inst.is_alive:
                # Verify it's actually responding
                try:
                    client = self._get_instance_client(inst)
                    await asyncio.wait_for(
                        client.health_check(),
                        timeout=2.0
                    )
                    running_instances.append(inst)
                except Exception:
                    # Instance not responding, mark for removal
                    logger.warning(f"Instance {inst.short_id} not responding, removing")
                    instances_to_remove.append(inst.id)
            else:
                instances_to_remove.append(inst.id)
        
        # Remove dead instances
        for inst_id in instances_to_remove:
            await self.process_manager.remove_instance(inst_id)
            self.session_router.remove_instance_references(inst_id)
            # Clean up client if exists
            if inst_id in self.instance_clients:
                try:
                    await self.instance_clients[inst_id].close()
                except Exception:
                    pass
                del self.instance_clients[inst_id]
            # Clean up handlers
            keys_to_remove = [k for k in self.instance_handlers if k.startswith(f"{inst_id}:")]
            for k in keys_to_remove:
                del self.instance_handlers[k]
        
        if instances_to_remove:
            logger.info(f"Cleaned up {len(instances_to_remove)} dead instance(s)")
        
        if not running_instances:
            return "No running instances.\n\nUse `/open <path>` to start a new OpenCode instance."
        
        # Get current instance for this chat/topic
        current_id = self.session_router.get_current_instance_id(chat_id, topic_id)
        
        # Build keyboard with instance buttons
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
                # Current instance is dead, clear it
                self.session_router.clear_current_instance(chat_id)
        
        text = f"*Projects* ({len(running_instances)}){current_text}\n\nTap to switch:"
        return CommandResponse(text=text, keyboard=keyboard)
    
    async def _cmd_switch(self, args: str, chat_id: int, topic_id: Optional[int] = None) -> Union[str, CommandResponse]:
        """Switch to a different instance."""
        if not args:
            # Show list to select from
            return await self._cmd_list(args, chat_id, topic_id)
        
        # Find instance by ID
        instance = self.process_manager.get_instance(args)
        if not instance:
            return f"Instance `{args}` not found.\n\nUse `/list` to see available instances."
        
        if not instance.is_alive:
            return f"Instance `{instance.short_id}` is not running ({instance.state.value}).\n\nUse `/restart {instance.short_id}` to restart it."
        
        self.session_router.set_current_instance(chat_id, instance, topic_id=topic_id)
        
        # If this is a topic, map it to the instance
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
        """Close the current instance - stop it and disconnect from chat."""
        instance_id = self.session_router.get_current_instance_id(chat_id, topic_id)
        
        if not instance_id:
            return "No instance selected.\n\nUse `/list` to see running instances."
        
        instance = self.process_manager.get_instance(instance_id)
        if not instance:
            # Instance no longer exists, just clear the reference
            self.session_router.clear_current_instance(chat_id, topic_id)
            return "Instance not found. Cleared reference."
        
        display_name = instance.display_name
        short_id = instance.short_id
        
        if instance.is_alive:
            # Stop the instance
            success = await self.process_manager.stop_instance(instance_id)
            if not success:
                return f"Failed to stop instance `{short_id}` ({display_name})"
        
        # Clear from router
        self.session_router.clear_current_instance(chat_id, topic_id)
        
        # Clear topic mapping if applicable
        if topic_id is not None:
            self.session_router.clear_topic_instance(chat_id, topic_id)
        
        # Clean up clients/handlers for this chat
        key = f"{instance_id}:{chat_id}"
        if key in self.instance_handlers:
            del self.instance_handlers[key]
        
        return f"Closed instance `{short_id}` ({display_name})\n\nUse `/open <path>` to start a new instance or `/list` to see running instances."
    
    async def _cmd_kill(self, args: str, chat_id: int, topic_id: Optional[int] = None) -> Union[str, CommandResponse]:
        """Stop an instance."""
        if not args:
            # Show list with kill buttons
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
            # Clear from router if this chat/topic was connected
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
        
        running = 0
        stopped = 0
        crashed = 0
        
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
    
    async def _cmd_threads(self, args: str, chat_id: int, topic_id: Optional[int] = None) -> Union[str, CommandResponse]:
        """List all thread-instance mappings for this chat.
        
        Shows which threads are connected to which instances.
        
        Usage: /threads
        """
        # Get all topics/threads for this chat
        topics = self.session_router.get_topics_for_chat(chat_id)
        
        if not topics:
            return "No threads mapped to instances yet.\n\nStart a reply thread and send a message to see the instance picker."
        
        # Build the response with thread info
        lines = ["*Thread Mappings*\n"]
        
        # Mark current thread if we're in one
        current_topic_id = topic_id
        
        for tid, instance_id in sorted(topics, key=lambda x: x[0]):
            instance = self.process_manager.get_instance(instance_id)
            
            if instance:
                status = "🟢" if instance.is_alive else "⚫"
                name = instance.name or instance.directory.name
                short_id = instance_id[:8]
                
                # Mark current thread
                marker = " ← you are here" if tid == current_topic_id else ""
                
                lines.append(f"{status} Thread `{tid}`: *{name}*{marker}")
                lines.append(f"   Instance: `{short_id}` | `{instance.directory.name}`")
            else:
                # Instance no longer exists
                lines.append(f"⚪ Thread `{tid}`: _(instance removed)_")
            
            lines.append("")  # Blank line between threads
        
        return "\n".join(lines).strip()
    
    async def _switch_to_instance(
        self,
        chat_id: int,
        instance_id: str,
        callback_id: str,
        original_msg_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        """Switch chat to instance (callback handler)."""
        instance = self.process_manager.get_instance(instance_id)
        if not instance:
            await self._safe_answer_callback(callback_id, text="Instance not found", show_alert=True)
            return
        
        self.session_router.set_current_instance(chat_id, instance, topic_id=topic_id)
        
        # Map topic to instance if applicable
        if topic_id is not None:
            self.session_router.set_topic_instance(chat_id, topic_id, instance.id)
        
        await self._safe_answer_callback(callback_id, text=f"Switched to {instance.display_name}")
        
        # Edit original message to remove keyboard
        try:
            await self.telegram.edit_message_with_keyboard(
                chat_id=str(chat_id),
                message_id=original_msg_id,
                text=f"Switched to `{instance.short_id}` ({instance.display_name})",
                inline_keyboard=[],  # Empty keyboard removes it
            )
        except Exception:
            pass
    
    async def _kill_instance(
        self,
        chat_id: int,
        instance_id: str,
        callback_id: str,
        original_msg_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        """Kill instance (callback handler)."""
        instance = self.process_manager.get_instance(instance_id)
        if not instance:
            await self._safe_answer_callback(callback_id, text="Instance not found", show_alert=True)
            return
        
        success = await self.process_manager.stop_instance(instance_id)
        
        await self._safe_answer_callback(callback_id, text="Instance stopped" if success else "Failed to stop")
        
        # Edit original message to remove keyboard
        try:
            await self.telegram.edit_message_with_keyboard(
                chat_id=str(chat_id),
                message_id=original_msg_id,
                text=f"Stopped `{instance.short_id}` ({instance.display_name})",
                inline_keyboard=[],  # Empty keyboard removes it
            )
        except Exception:
            pass
    
    async def _switch_to_session(
        self,
        chat_id: int,
        session_id: str,
        callback_id: str,
        original_msg_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        """Switch to a session within the current instance (callback handler)."""
        instance_id = self.session_router.get_current_instance_id(chat_id, topic_id)
        if not instance_id:
            await self._safe_answer_callback(callback_id, text="No instance selected", show_alert=True)
            return
        
        instance = self.process_manager.get_instance(instance_id)
        if not instance:
            await self._safe_answer_callback(callback_id, text="Instance not found", show_alert=True)
            return
        
        # Update session tracking
        self._update_handler_session(chat_id, session_id)
        
        await self._safe_answer_callback(callback_id, text=f"Switched to session {session_id[:8]}")
        
        # Edit original message
        try:
            await self.telegram.edit_message_with_keyboard(
                chat_id=str(chat_id),
                message_id=original_msg_id,
                text=f"Switched to session `{session_id[:8]}`",
                inline_keyboard=[],
            )
        except Exception:
            pass
    
    async def _handle_model_selection(
        self,
        chat_id: int,
        data: str,
        callback_id: str,
        original_msg_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        """Handle model selection from inline keyboard."""
        instance_id = self.session_router.get_current_instance_id(chat_id, topic_id)
        if not instance_id:
            await self._safe_answer_callback(callback_id, text="No instance selected", show_alert=True)
            return
        
        instance = self.process_manager.get_instance(instance_id)
        if not instance:
            await self._safe_answer_callback(callback_id, text="Instance not found", show_alert=True)
            return
        
        # Get handler to look up model from callback data
        handler = self._get_instance_handler(instance, chat_id)
        model_info = handler.lookup_model_callback(data)
        
        if not model_info:
            await self._safe_answer_callback(callback_id, text="Model not found", show_alert=True)
            return
        
        provider_id, model_id = model_info
        
        # Set model preference
        self.session_router.set_model_preference(chat_id, provider_id, model_id)
        
        await self._safe_answer_callback(callback_id, text=f"Model set to {provider_id}/{model_id}")
        
        # Edit original message
        try:
            await self.telegram.edit_message_with_keyboard(
                chat_id=str(chat_id),
                message_id=original_msg_id,
                text=f"Model set to `{provider_id}/{model_id}`",
                inline_keyboard=[],
            )
        except Exception:
            pass
    
    async def _handle_session_delete(
        self,
        chat_id: int,
        session_id: str,
        callback_id: str,
        original_msg_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        """Handle session deletion from inline keyboard."""
        instance_id = self.session_router.get_current_instance_id(chat_id, topic_id)
        if not instance_id:
            await self._safe_answer_callback(callback_id, text="No instance selected", show_alert=True)
            return
        
        instance = self.process_manager.get_instance(instance_id)
        if not instance:
            await self._safe_answer_callback(callback_id, text="Instance not found", show_alert=True)
            return
        
        try:
            client = self._get_instance_client(instance)
            await client.delete_session(session_id)
            
            # Clear if this was the active session
            if self.chat_sessions.get(chat_id) == session_id:
                self._update_handler_session(chat_id, None)
            
            await self._safe_answer_callback(callback_id, text=f"Deleted session {session_id[:8]}")
            
            # Edit original message
            try:
                await self.telegram.edit_message_with_keyboard(
                    chat_id=str(chat_id),
                    message_id=original_msg_id,
                    text=f"Deleted session `{session_id[:8]}`",
                    inline_keyboard=[],
                )
            except Exception:
                pass
                
        except Exception as e:
            await self._safe_answer_callback(callback_id, text=f"Error: {str(e)[:50]}", show_alert=True)
    
    async def _handle_permission_callback(
        self,
        chat_id: int,
        data: str,
        callback_id: str,
        original_msg_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        """Handle permission response from inline keyboard.
        
        Format: perm:<y|a|n>:<request_id>
        y = allow once, a = allow always, n = reject
        """
        parts = data.split(":", 2)
        if len(parts) != 3:
            await self._safe_answer_callback(callback_id, text="Invalid callback")
            return
        
        action = parts[1]
        request_id = parts[2]
        
        instance_id = self.session_router.get_current_instance_id(chat_id, topic_id)
        if not instance_id:
            await self._safe_answer_callback(callback_id, text="No instance", show_alert=True)
            return
        
        instance = self.process_manager.get_instance(instance_id)
        if not instance:
            await self._safe_answer_callback(callback_id, text="Instance not found", show_alert=True)
            return
        
        try:
            client = self._get_instance_client(instance)
            
            # Map action to reply
            reply_map = {"y": "once", "a": "always", "n": "reject"}
            reply = reply_map.get(action, "reject")
            
            success = await client.reply_to_permission(request_id, reply)
            
            if success:
                action_text = {"y": "Allowed", "a": "Always allowed", "n": "Rejected"}.get(action, "Responded")
                await self._safe_answer_callback(callback_id, text=action_text)
                
                # Clean up notification tracking
                if request_id in self._notified_pending:
                    del self._notified_pending[request_id]
                
                try:
                    await self.telegram.edit_message_with_keyboard(
                        chat_id=str(chat_id),
                        message_id=original_msg_id,
                        text=f"Permission: {action_text}",
                        inline_keyboard=[],
                    )
                except Exception:
                    pass
            else:
                await self._safe_answer_callback(callback_id, text="Failed", show_alert=True)
                
        except Exception as e:
            await self._safe_answer_callback(callback_id, text=f"Error: {str(e)[:50]}", show_alert=True)
    
    async def _handle_question_callback(
        self,
        chat_id: int,
        data: str,
        callback_id: str,
        original_msg_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        """Handle question response from inline keyboard.
        
        Format: q:<request_id>:<option_idx>
        """
        parts = data.split(":", 2)
        if len(parts) != 3:
            await self._safe_answer_callback(callback_id, text="Invalid callback")
            return
        
        request_id = parts[1]
        try:
            option_idx = int(parts[2])
        except ValueError:
            await self._safe_answer_callback(callback_id, text="Invalid option")
            return
        
        instance_id = self.session_router.get_current_instance_id(chat_id, topic_id)
        if not instance_id:
            await self._safe_answer_callback(callback_id, text="No instance", show_alert=True)
            return
        
        instance = self.process_manager.get_instance(instance_id)
        if not instance:
            await self._safe_answer_callback(callback_id, text="Instance not found", show_alert=True)
            return
        
        try:
            client = self._get_instance_client(instance)
            
            # Get the question to find the option label
            questions = await client.list_pending_questions()
            question = next((q for q in questions if q.get("id") == request_id), None)
            
            if not question:
                await self._safe_answer_callback(callback_id, text="Question expired", show_alert=True)
                return
            
            # Get the option label
            q_list = question.get("questions", [])
            if not q_list:
                await self._safe_answer_callback(callback_id, text="No questions", show_alert=True)
                return
            
            options = q_list[0].get("options", [])
            if option_idx >= len(options):
                await self._safe_answer_callback(callback_id, text="Invalid option", show_alert=True)
                return
            
            selected_label = options[option_idx].get("label", "")
            
            # Respond with the selected option
            logger.info(f"Responding to question {request_id} with: {selected_label}")
            success = await client.respond_to_question(request_id, [[selected_label]])
            logger.info(f"Question response success: {success}")
            
            if success:
                await self._safe_answer_callback(callback_id, text=f"Selected: {selected_label[:30]}")
                
                # Clean up notification tracking
                if request_id in self._notified_pending:
                    del self._notified_pending[request_id]
                
                try:
                    await self.telegram.edit_message_with_keyboard(
                        chat_id=str(chat_id),
                        message_id=original_msg_id,
                        text=f"Selected: {selected_label}",
                        inline_keyboard=[],
                    )
                except Exception:
                    pass
                
                # After answering a question, OpenCode may continue processing
                # We need to poll for the response and forward it
                logger.info(f"Starting to poll for response after question in chat {chat_id} topic {topic_id}")
                await self._poll_and_forward_response(instance, chat_id, topic_id)
            else:
                logger.error(f"Failed to respond to question {request_id}")
                await self._safe_answer_callback(callback_id, text="Failed", show_alert=True)
                
        except Exception as e:
            await self._safe_answer_callback(callback_id, text=f"Error: {str(e)[:50]}", show_alert=True)

    async def _poll_and_forward_response(
        self,
        instance: OpenCodeInstance,
        chat_id: int,
        topic_id: Optional[int] = None,
        timeout: float = 300.0,
    ) -> None:
        """Poll for OpenCode response after answering a question and forward to Telegram.
        
        After a question is answered, OpenCode continues processing. This method
        polls the session status until idle, then gets any new response and forwards it.
        """
        # Get session ID for this chat/topic
        session_id = self.session_router.get_session_id(chat_id, topic_id)
        if not session_id:
            logger.warning(f"No session found for chat {chat_id} topic {topic_id}")
            return
        
        logger.info(f"Polling for response in session {session_id[:8]}")
        client = self._get_instance_client(instance)
        start_time = asyncio.get_event_loop().time()
        
        # Capture the current message IDs before polling
        # Any new messages after this are responses to the question we just answered
        try:
            initial_messages = await client.get_messages(session_id, limit=20)
            known_message_ids = {msg.get("id") for msg in initial_messages if msg.get("id")}
            logger.debug(f"Captured {len(known_message_ids)} existing message IDs")
        except Exception as e:
            logger.error(f"Failed to get initial messages: {e}")
            known_message_ids = set()
        
        # Send initial typing indicator
        if topic_id is not None:
            await self.telegram.set_typing_in_topic(str(chat_id), topic_id)
        else:
            await self.telegram.set_typing(str(chat_id))
        
        # Poll until session is idle or we timeout
        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                logger.warning(f"Timeout waiting for response in session {session_id[:8]}")
                break
            
            try:
                status = await client.get_session_status()
                session_status = status.get(session_id, {})
                status_type = session_status.get("type", "idle")
                logger.debug(f"Session {session_id[:8]} status: {status_type}")
                
                if status_type == "idle":
                    # Session is idle, get messages and find new ones
                    logger.info(f"Session {session_id[:8]} is idle, fetching messages")
                    messages = await client.get_messages(session_id, limit=20)
                    logger.debug(f"Got {len(messages)} messages")
                    
                    # Find new assistant messages (not in our initial snapshot)
                    for msg in reversed(messages):
                        msg_id = msg.get("id", "")
                        if msg.get("role") == "assistant" and msg_id and msg_id not in known_message_ids:
                            parts = msg.get("parts", [])
                            text_parts = []
                            for part in parts:
                                if part.get("type") == "text":
                                    text_parts.append(part.get("text", ""))
                            
                            if text_parts:
                                response_text = "\n".join(text_parts)
                                logger.info(f"Forwarding new response {msg_id[:8]} ({len(response_text)} chars) to chat {chat_id}")
                                await self._send_text(chat_id, response_text, topic_id)
                            break  # Only forward the most recent new message
                    
                    # Also check for any new pending questions/permissions
                    await self._check_pending_for_instance(instance, chat_id, topic_id)
                    break
                    
                elif status_type == "question":
                    # There's a new question - let the pending check handle it
                    logger.info(f"Session {session_id[:8]} has a question, checking pending")
                    await self._check_pending_for_instance(instance, chat_id, topic_id)
                    break
                
                # Still busy, send typing and wait
                logger.debug(f"Session {session_id[:8]} still busy, waiting...")
                if topic_id is not None:
                    await self.telegram.set_typing_in_topic(str(chat_id), topic_id)
                else:
                    await self.telegram.set_typing(str(chat_id))
                
                await asyncio.sleep(TYPING_INTERVAL)
                
            except Exception as e:
                logger.error(f"Error polling session status: {e}")
                await asyncio.sleep(1.0)
    
    def _open_browser_for_instance(self, instance: OpenCodeInstance) -> None:
        """Open browser for an instance if not already opened.
        
        Opens the OpenCode web UI in the user's default browser.
        Only opens once per instance lifetime.
        """
        if instance.browser_opened:
            return
        
        url = instance.url
        logger.info(f"Opening browser for instance {instance.short_id} at {url}")
        
        try:
            # Try to open in background without blocking
            if os.name == 'posix' and os.uname().sysname == 'Darwin':
                # macOS - use 'open' command in background
                subprocess.Popen(
                    ['open', url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                # Cross-platform fallback
                webbrowser.open(url, new=2)  # new=2 means new tab if possible
            
            instance.browser_opened = True
            self.process_manager._save_state()
            logger.info(f"Browser opened for instance {instance.short_id}")
            
        except Exception as e:
            logger.warning(f"Failed to open browser for instance {instance.short_id}: {e}")
    
    async def _show_thread_instance_picker(
        self,
        chat_id: int,
        topic_id: int,
        original_message: str,
    ) -> None:
        """Show instance picker for a new thread without mapping.
        
        Displays inline buttons for each running instance, plus instructions
        on how to create a new instance by providing a path.
        """
        # Get all running instances
        running_instances = self.process_manager.get_running_instances()
        
        # Build keyboard with instance buttons
        keyboard: list[list[dict[str, str]]] = []
        
        for inst in running_instances:
            keyboard.append([{
                "text": f"📁 {inst.display_name}",
                "callback_data": f"thread_inst:{topic_id}:{inst.id[:20]}",
            }])
        
        # Build message text
        if running_instances:
            text = (
                "*New Thread*\n\n"
                "This thread is not connected to any project.\n\n"
                "*Select an existing instance:*\n"
            )
            for inst in running_instances:
                text += f"• `{inst.short_id}` - {inst.display_name}\n"
            text += (
                "\n*Or create a new instance:*\n"
                "Send a directory path like `/open ~/projects/myapp`"
            )
        else:
            text = (
                "*New Thread*\n\n"
                "No running instances. Create one by sending a path:\n"
                "`/open ~/projects/myapp`"
            )
        
        # Send picker message
        if keyboard:
            await self.telegram.send_message_with_keyboard_to_topic(
                chat_id=str(chat_id),
                message_thread_id=topic_id,
                text=text,
                inline_keyboard=keyboard,
            )
        else:
            await self.telegram.send_message_to_topic(
                chat_id=str(chat_id),
                message_thread_id=topic_id,
                text=text,
            )
    
    async def _handle_thread_instance_selection(
        self,
        chat_id: int,
        topic_id: int,
        instance_id: str,
        callback_id: str,
        original_msg_id: int,
    ) -> None:
        """Handle when user selects an instance for a thread."""
        # Find the full instance ID (callback data is truncated)
        instance = None
        for inst in self.process_manager.list_instances():
            if inst.id.startswith(instance_id):
                instance = inst
                break
        
        if not instance:
            await self._safe_answer_callback(callback_id, text="Instance not found", show_alert=True)
            return
        
        if not instance.is_alive:
            # Try to restart the instance
            try:
                new_instance = await self.process_manager.restart_instance(instance.id)
                if new_instance and new_instance.is_alive:
                    instance = new_instance
                else:
                    await self._safe_answer_callback(callback_id, text="Failed to start instance", show_alert=True)
                    return
            except Exception as e:
                await self._safe_answer_callback(callback_id, text=f"Error: {str(e)[:50]}", show_alert=True)
                return
        
        # Map the thread to the instance (1:1 mapping)
        self.session_router.set_topic_instance(chat_id, topic_id, instance.id)
        self.session_router.set_current_instance(chat_id, instance, topic_id=topic_id)
        
        # Persist the mapping
        self.session_router._save_state()
        
        await self._safe_answer_callback(callback_id, text=f"Connected to {instance.display_name}")
        
        # Auto-rename the thread to the project name
        await self._rename_topic(chat_id, topic_id, instance.display_name)
        
        # Edit the picker message to show connection status
        try:
            await self.telegram.edit_message_with_keyboard(
                chat_id=str(chat_id),
                message_id=original_msg_id,
                text=f"📁 Connected to *{instance.display_name}*\n\nPath: `{instance.directory}`\nInstance: `{instance.short_id}`\n\nSend any message to chat with OpenCode.",
                inline_keyboard=[],
            )
        except Exception:
            pass
    
    async def _rename_topic(self, chat_id: int, topic_id: int, name: str) -> bool:
        """Rename a topic to the given name.
        
        Works with both forum topics in groups and reply threads in private chats.
        
        Args:
            chat_id: Telegram chat ID
            topic_id: Topic ID (message_thread_id)
            name: New name for the topic
            
        Returns:
            True if renamed successfully, False otherwise
        """
        try:
            await self.telegram.edit_forum_topic(
                chat_id=str(chat_id),
                message_thread_id=topic_id,
                name=name[:128],  # Telegram limit
            )
            logger.info(f"Renamed thread {topic_id} in chat {chat_id} to '{name}'")
            return True
        except Exception as e:
            # Renaming may fail if:
            # - Bot doesn't have permission
            # - Not a forum/topic chat
            # - Topic doesn't exist
            # This is non-critical, so just log and continue
            logger.debug(f"Could not rename thread {topic_id}: {e}")
            return False

    async def _forward_to_instance(
        self,
        chat_id: int,
        text: str,
        username: str,
        topic_id: Optional[int] = None,
    ) -> None:
        """Forward a message to the appropriate OpenCode instance.
        
        If the instance is stopped but we have its directory, automatically
        restart it to resume the conversation.
        
        For new threads without instance mapping, show an instance picker.
        """
        # Get current instance for this chat/topic
        instance_id = self.session_router.get_current_instance_id(chat_id, topic_id)
        
        if not instance_id:
            # No instance mapped to this chat/thread
            # Check if this is a thread (topic_id present) - show instance picker
            if topic_id is not None:
                await self._show_thread_instance_picker(chat_id, topic_id, text)
                return
            
            # Main chat without instance - show standard message
            await self._send_text(
                chat_id,
                "No instance selected.\n\nUse `/open <path>` to open a project or `/list` to see available instances.",
                topic_id,
            )
            return
        
        instance = self.process_manager.get_instance(instance_id)
        
        if not instance:
            # Instance completely removed - clear the mapping
            self.session_router.clear_current_instance(chat_id, topic_id)
            if topic_id is not None:
                self.session_router.clear_topic_instance(chat_id, topic_id)
            await self._send_text(
                chat_id,
                "Instance no longer exists.\n\nUse `/open <path>` to open a project.",
                topic_id,
            )
            return
        
        # Auto-resume stopped instances
        if not instance.is_alive:
            logger.info(f"Auto-resuming stopped instance {instance.short_id} for chat {chat_id} topic {topic_id}")
            
            # Notify user that we're restarting
            await self._send_text(
                chat_id,
                f"Resuming `{instance.display_name}`...",
                topic_id,
            )
            
            try:
                # Restart the instance
                new_instance = await self.process_manager.restart_instance(instance_id)
                
                if new_instance and new_instance.is_alive:
                    instance = new_instance
                    # Update mappings to new instance
                    self.session_router.set_current_instance(chat_id, instance, topic_id=topic_id)
                    if topic_id is not None:
                        self.session_router.set_topic_instance(chat_id, topic_id, instance.id)
                    logger.info(f"Successfully resumed instance {instance.short_id}")
                else:
                    await self._send_text(
                        chat_id,
                        f"Failed to resume instance.\n\nUse `/open {instance.directory}` to manually restart.",
                        topic_id,
                    )
                    return
            except Exception as e:
                logger.error(f"Error resuming instance: {e}")
                await self._send_text(
                    chat_id,
                    f"Failed to resume: {str(e)[:100]}\n\nUse `/open {instance.directory}` to manually restart.",
                    topic_id,
                )
                return
        
        # Open browser on first request to this instance
        if not instance.browser_opened:
            self._open_browser_for_instance(instance)
        
        # Get or create HTTP client for this instance
        if instance.id not in self.http_clients:
            self.http_clients[instance.id] = httpx.AsyncClient(
                timeout=600.0,
                limits=httpx.Limits(max_keepalive_connections=5),
            )
        
        client = self.http_clients[instance.id]
        
        # Get or create session in this instance
        session_id = self.session_router.get_session_id(chat_id, topic_id)
        
        try:
            if not session_id:
                # Create a new session
                resp = await client.post(f"{instance.url}/session", json={})
                resp.raise_for_status()
                session_data = resp.json()
                session_id = session_data["id"]
                self.session_router.set_session_id(chat_id, session_id, topic_id)
                logger.info(f"Created session {session_id[:8]} in instance {instance.short_id}")
            
            # Send typing indicator
            if topic_id is not None:
                await self.telegram.set_typing_in_topic(str(chat_id), topic_id)
            else:
                await self.telegram.set_typing(str(chat_id))
            
            # Get model preference
            provider, model = self.session_router.get_model_preference(chat_id)
            provider = provider or instance.provider_id
            model = model or instance.model_id
            
            # Format prompt with context
            prompt = f"[Telegram from @{username}]: {text}"
            
            # Send message to OpenCode (with typing updates)
            response_text = await self._send_with_typing(
                client=client,
                instance=instance,
                session_id=session_id,
                prompt=prompt,
                chat_id=chat_id,
                provider_id=provider,
                model_id=model,
                topic_id=topic_id,
            )
            
            if response_text:
                await self._send_text(chat_id, response_text, topic_id)
            else:
                await self._send_text(chat_id, "(Empty response from OpenCode)", topic_id)
            
            # Immediately check for pending questions/permissions after message
            # This provides faster feedback than waiting for the background loop
            await self._check_pending_for_instance(instance, chat_id, topic_id)
                
        except httpx.HTTPStatusError as e:
            error_msg = f"OpenCode error {e.response.status_code}"
            try:
                error_data = e.response.json()
                error_detail = error_data.get("error", {}).get("message", str(e))
                error_msg = f"{error_msg}: {error_detail[:200]}"
            except Exception:
                pass
            
            # If session is invalid, clear it
            if e.response.status_code in (400, 404):
                self.session_router.set_session_id(chat_id, None, topic_id)
            
            await self._send_text(chat_id, error_msg, topic_id)
            
        except Exception as e:
            logger.error(f"Error forwarding to instance: {e}")
            await self._send_text(chat_id, f"Error: {str(e)[:200]}", topic_id)
    
    async def _send_with_typing(
        self,
        client: httpx.AsyncClient,
        instance: OpenCodeInstance,
        session_id: str,
        prompt: str,
        chat_id: int,
        provider_id: str,
        model_id: str,
        topic_id: Optional[int] = None,
    ) -> str:
        """Send message to OpenCode with typing indicator updates."""
        
        async def send_message() -> str:
            resp = await client.post(
                f"{instance.url}/session/{session_id}/message",
                json={
                    "parts": [{"type": "text", "text": prompt}],
                    "model": {"providerID": provider_id, "modelID": model_id},
                },
                timeout=600.0,  # 10 minute timeout for long responses
            )
            resp.raise_for_status()
            
            # Handle empty responses (204 or 200 with empty body)
            if resp.status_code == 204 or not resp.content:
                return ""
            
            try:
                data = resp.json()
            except Exception:
                # Empty or invalid JSON
                return ""
            
            # Check for errors
            info = data.get("info", {})
            if info.get("error"):
                error_msg = info["error"].get("data", {}).get("message", "Unknown error")
                logger.error(f"OpenCode error: {error_msg}")
                return f"Error: {error_msg[:200]}"
            
            # Extract text from response parts
            parts = data.get("parts", [])
            text_parts = []
            for part in parts:
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
            
            return "\n".join(text_parts)
        
        # Start the message send task
        send_task = asyncio.create_task(send_message())
        
        # Keep sending typing indicator while waiting
        try:
            while not send_task.done():
                if topic_id is not None:
                    await self.telegram.set_typing_in_topic(str(chat_id), topic_id)
                else:
                    await self.telegram.set_typing(str(chat_id))
                
                try:
                    result = await asyncio.wait_for(
                        asyncio.shield(send_task),
                        timeout=TYPING_INTERVAL,
                    )
                    return result
                except asyncio.TimeoutError:
                    continue
            
            return await send_task
            
        except asyncio.CancelledError:
            send_task.cancel()
            raise


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
