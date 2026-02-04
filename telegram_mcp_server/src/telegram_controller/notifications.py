"""Pending permission and question notification management.

Handles checking for pending permissions/questions in OpenCode instances
and notifying connected Telegram chats.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Optional

from .instance import OpenCodeInstance

if TYPE_CHECKING:
    from .controller import TelegramController

logger = logging.getLogger("telegram_controller.notifications")

# Constants
PENDING_CHECK_INTERVAL = 10.0  # Check for pending permissions/questions every N seconds


class NotificationManager:
    """Manages pending permission and question notifications."""
    
    def __init__(self, controller: "TelegramController"):
        """Initialize notification manager.
        
        Args:
            controller: Parent controller instance
        """
        self.controller = controller
        
        # Pending notifications tracking (request_id -> set of chat_ids already notified)
        self._notified_pending: dict[str, set[Any]] = {}
        
        # Background task for pending checks
        self._check_task: Optional[asyncio.Task] = None
    
    @property
    def process_manager(self):
        return self.controller.process_manager
    
    @property
    def session_router(self):
        return self.controller.session_router
    
    @property
    def telegram(self):
        return self.controller.telegram
    
    def get_notified_pending(self) -> dict[str, set[Any]]:
        """Get the notified pending dict for external access."""
        return self._notified_pending
    
    def clear_notified(self, request_id: str) -> None:
        """Clear notification tracking for a request."""
        if request_id in self._notified_pending:
            del self._notified_pending[request_id]
    
    async def start(self) -> None:
        """Start the background notification check loop."""
        self._check_task = asyncio.create_task(self._pending_check_loop())
    
    async def stop(self) -> None:
        """Stop the background notification check loop."""
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
    
    async def _pending_check_loop(self) -> None:
        """Background task to check for pending permissions and questions."""
        while self.controller.running:
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
                # Get chats connected to this instance
                chat_ids = self.session_router.get_chats_for_instance(instance.id)
                topic_mappings = self.session_router.get_topics_for_instance(instance.id)
                
                if not chat_ids and not topic_mappings:
                    continue
                
                client = self.controller._get_instance_client(instance)
                
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
    
    async def check_pending_for_instance(
        self,
        instance: OpenCodeInstance,
        chat_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        """Check for pending questions/permissions for a specific instance and notify.
        
        This is called immediately after sending a message for faster feedback.
        """
        try:
            client = self.controller._get_instance_client(instance)
            
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
        
        # Build keyboard
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
        chats_with_topics = {chat_id for chat_id, _ in topic_mappings}
        
        for chat_id in chat_ids:
            if chat_id in notified:
                continue
            if chat_id in chats_with_topics:
                logger.debug(f"Skipping chat {chat_id} - already notified via topic")
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
        chats_with_topics = {chat_id for chat_id, _ in topic_mappings}
        
        for chat_id in chat_ids:
            if chat_id in notified:
                continue
            if chat_id in chats_with_topics:
                logger.debug(f"Skipping chat {chat_id} - already notified via topic")
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
