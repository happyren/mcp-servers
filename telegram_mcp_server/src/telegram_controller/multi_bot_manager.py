"""Multi-bot manager for handling multiple Telegram bots.

This module provides a manager that can:
1. Manage multiple TelegramClient instances (one per bot)
2. Route messages to the appropriate bot based on instance type
3. Handle bot handoff when spawning different instance types
4. Poll all bots and aggregate updates
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from telegram_mcp_server.telegram_client import TelegramClient

from .config_schema import BotConfig, MultiBotConfig


logger = logging.getLogger("telegram_controller.multi_bot")


@dataclass
class BotState:
    """State for a single bot."""
    config: BotConfig
    client: TelegramClient
    polling_offset: int = 0
    username: str = ""
    is_active: bool = True


@dataclass
class ThreadBotMapping:
    """Mapping of thread/topic to a specific bot."""
    chat_id: int
    topic_id: Optional[int]
    bot_name: str
    instance_type: str


class MultiBotManager:
    """Manages multiple Telegram bots for the controller.
    
    This allows:
    - Spawning a quantcode instance from OpenCode bot chat
    - Subsequent messages in that thread go through the QuantCode bot
    - Cross-bot notifications and handoffs
    """
    
    def __init__(
        self,
        config: MultiBotConfig,
        api_base_url: str = "https://api.telegram.org",
    ):
        """Initialize the multi-bot manager.
        
        Args:
            config: Multi-bot configuration
            api_base_url: Telegram API base URL
        """
        self.config = config
        self.api_base_url = api_base_url
        
        # Bot state by name
        self.bots: Dict[str, BotState] = {}
        
        # Thread-to-bot mappings
        # Key: (chat_id, topic_id or None) -> bot_name
        self._thread_bot_map: Dict[tuple[int, Optional[int]], str] = {}
        
        # Instance type to preferred bot mapping
        self._type_to_bot: Dict[str, str] = {}
        
        # Primary bot (receives all messages initially)
        self._primary_bot: Optional[str] = None
        
        # Polling state
        self._poll_tasks: Dict[str, asyncio.Task] = {}
        self._update_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
        self._running = False
        
    async def initialize(self) -> None:
        """Initialize all bots from configuration."""
        for bot_config in self.config.bots:
            try:
                client = TelegramClient(
                    bot_token=bot_config.token,
                    base_url=self.api_base_url,
                )
                
                # Get bot info
                bot_info = await client.get_me()
                username = bot_info.get("username", "")
                
                state = BotState(
                    config=bot_config,
                    client=client,
                    username=username,
                )
                
                self.bots[bot_config.name] = state
                
                # Map instance type to bot
                self._type_to_bot[bot_config.type] = bot_config.name
                
                # First bot is primary
                if self._primary_bot is None:
                    self._primary_bot = bot_config.name
                
                logger.info(f"Initialized bot '{bot_config.name}' (@{username}) for type '{bot_config.type}'")
                
            except Exception as e:
                logger.error(f"Failed to initialize bot '{bot_config.name}': {e}")
        
        if not self.bots:
            raise RuntimeError("No bots initialized successfully")
        
        logger.info(f"Initialized {len(self.bots)} bots, primary: {self._primary_bot}")
    
    async def close(self) -> None:
        """Close all bot clients."""
        self._running = False
        
        # Cancel all polling tasks
        for task in self._poll_tasks.values():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._poll_tasks.clear()
        
        # Close all clients
        for state in self.bots.values():
            try:
                await state.client.close()
            except Exception as e:
                logger.error(f"Error closing bot '{state.config.name}': {e}")
        
        self.bots.clear()
    
    def get_primary_bot(self) -> Optional[BotState]:
        """Get the primary bot."""
        if self._primary_bot:
            return self.bots.get(self._primary_bot)
        return None
    
    def get_bot_by_name(self, name: str) -> Optional[BotState]:
        """Get a bot by name."""
        return self.bots.get(name)
    
    def get_bot_for_type(self, instance_type: str) -> Optional[BotState]:
        """Get the bot assigned to handle a specific instance type."""
        bot_name = self._type_to_bot.get(instance_type)
        if bot_name:
            return self.bots.get(bot_name)
        return None
    
    def get_bot_for_thread(
        self,
        chat_id: int,
        topic_id: Optional[int] = None,
    ) -> Optional[BotState]:
        """Get the bot assigned to a specific thread/topic."""
        key = (chat_id, topic_id)
        bot_name = self._thread_bot_map.get(key)
        if bot_name:
            return self.bots.get(bot_name)
        
        # Fall back to primary bot
        return self.get_primary_bot()
    
    def get_client_for_thread(
        self,
        chat_id: int,
        topic_id: Optional[int] = None,
    ) -> TelegramClient:
        """Get the TelegramClient for a specific thread/topic."""
        bot = self.get_bot_for_thread(chat_id, topic_id)
        if bot:
            return bot.client
        
        # Fallback to primary
        primary = self.get_primary_bot()
        if primary:
            return primary.client
        
        raise RuntimeError("No bots available")
    
    def assign_thread_to_bot(
        self,
        chat_id: int,
        topic_id: Optional[int],
        bot_name: str,
        instance_type: str,
    ) -> bool:
        """Assign a thread/topic to be handled by a specific bot.
        
        Args:
            chat_id: Telegram chat ID
            topic_id: Forum topic ID (None for non-forum chats)
            bot_name: Name of the bot to assign
            instance_type: Type of instance (for logging)
            
        Returns:
            True if assignment successful
        """
        if bot_name not in self.bots:
            logger.warning(f"Bot '{bot_name}' not found, cannot assign thread")
            return False
        
        key = (chat_id, topic_id)
        old_bot = self._thread_bot_map.get(key)
        
        self._thread_bot_map[key] = bot_name
        
        if old_bot and old_bot != bot_name:
            logger.info(
                f"Handoff: thread ({chat_id}, {topic_id}) "
                f"from '{old_bot}' to '{bot_name}' for {instance_type}"
            )
        else:
            logger.info(
                f"Assigned thread ({chat_id}, {topic_id}) "
                f"to '{bot_name}' for {instance_type}"
            )
        
        return True
    
    def assign_thread_to_type(
        self,
        chat_id: int,
        topic_id: Optional[int],
        instance_type: str,
    ) -> Optional[str]:
        """Assign a thread to the bot that handles a specific instance type.
        
        Args:
            chat_id: Telegram chat ID
            topic_id: Forum topic ID
            instance_type: Instance type (opencode, quantcode, etc.)
            
        Returns:
            Bot name if successful, None otherwise
        """
        bot_name = self._type_to_bot.get(instance_type)
        if not bot_name:
            # No specific bot for this type, use primary
            bot_name = self._primary_bot
        
        if bot_name and self.assign_thread_to_bot(chat_id, topic_id, bot_name, instance_type):
            return bot_name
        return None
    
    def clear_thread_assignment(
        self,
        chat_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        """Clear the bot assignment for a thread."""
        key = (chat_id, topic_id)
        if key in self._thread_bot_map:
            old_bot = self._thread_bot_map.pop(key)
            logger.info(f"Cleared thread assignment ({chat_id}, {topic_id}) from '{old_bot}'")
    
    async def send_handoff_notification(
        self,
        chat_id: int,
        topic_id: Optional[int],
        from_bot: str,
        to_bot: str,
        instance_type: str,
    ) -> None:
        """Send a notification about bot handoff.
        
        When spawning a quantcode instance from the opencode bot,
        notify the user that subsequent messages will go through
        a different bot.
        """
        from_state = self.bots.get(from_bot)
        to_state = self.bots.get(to_bot)
        
        if not from_state or not to_state:
            return
        
        # Send notification from the original bot
        message = (
            f"Spawning `{instance_type}` instance.\n\n"
            f"Subsequent messages in this thread will be handled by "
            f"@{to_state.username}.\n\n"
            f"Please continue the conversation there."
        )
        
        try:
            if topic_id is not None:
                await from_state.client.send_message_to_topic(
                    chat_id=str(chat_id),
                    message_thread_id=topic_id,
                    text=message,
                )
            else:
                await from_state.client.send_message(
                    chat_id=str(chat_id),
                    text=message,
                )
        except Exception as e:
            logger.error(f"Failed to send handoff notification: {e}")
    
    async def start_polling(self) -> asyncio.Queue[tuple[str, dict]]:
        """Start polling all bots for updates.
        
        Returns:
            Queue that receives (bot_name, update) tuples
        """
        self._running = True
        
        for bot_name, state in self.bots.items():
            task = asyncio.create_task(
                self._poll_bot(bot_name, state),
                name=f"poll_{bot_name}",
            )
            self._poll_tasks[bot_name] = task
        
        return self._update_queue
    
    async def _poll_bot(self, bot_name: str, state: BotState) -> None:
        """Poll a single bot for updates."""
        logger.info(f"Started polling bot '{bot_name}' (@{state.username})")
        
        while self._running:
            try:
                updates = await state.client.get_updates_with_callbacks(
                    offset=state.polling_offset,
                    limit=100,
                    timeout=30,
                )
                
                if not updates:
                    continue
                
                for update in updates:
                    update_id = update.get("update_id", 0)
                    if update_id >= state.polling_offset:
                        state.polling_offset = update_id + 1
                    
                    # Put (bot_name, update) into the queue
                    await self._update_queue.put((bot_name, update))
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Polling error for bot '{bot_name}': {e}")
                await asyncio.sleep(1)
        
        logger.info(f"Stopped polling bot '{bot_name}'")
    
    def get_all_bot_names(self) -> List[str]:
        """Get names of all bots."""
        return list(self.bots.keys())
    
    def get_bot_info(self) -> Dict[str, Dict[str, Any]]:
        """Get info about all bots."""
        return {
            name: {
                "username": state.username,
                "type": state.config.type,
                "chat_id": state.config.chat_id,
                "is_active": state.is_active,
            }
            for name, state in self.bots.items()
        }
