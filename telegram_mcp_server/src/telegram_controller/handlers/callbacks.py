"""Callback query handlers for button clicks.

Handles inline keyboard button callbacks.
"""

import logging
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..controller import TelegramController

logger = logging.getLogger("telegram_controller.callbacks")


class CallbackHandler:
    """Handles Telegram callback queries (button clicks)."""
    
    def __init__(self, controller: "TelegramController"):
        """Initialize callback handler.
        
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
    
    async def handle(self, callback: dict[str, Any]) -> None:
        """Handle a callback query.
        
        Args:
            callback: Telegram callback_query object
        """
        callback_id = callback.get("id", "")
        data = callback.get("data", "")
        from_user = callback.get("from", {})
        message = callback.get("message", {})
        chat = message.get("chat", {})
        chat_id = chat.get("id", 0)
        original_msg_id = message.get("message_id", 0)
        
        # Extract topic_id
        topic_id: Optional[int] = None
        is_forum = chat.get("is_forum", False)
        is_known_forum = self.session_router.is_forum_chat(chat_id)
        has_thread_id = message.get("message_thread_id") is not None
        
        if message.get("is_topic_message") or is_forum or has_thread_id or is_known_forum:
            topic_id = message.get("message_thread_id")
            if is_forum:
                self.session_router.mark_chat_as_forum(chat_id)
        
        logger.info(f"Callback from {from_user.get('username')}: {data} (topic={topic_id})")
        
        try:
            # Ignore placeholder callbacks
            if data == "ignore":
                await self._safe_answer(callback_id)
                return
            
            # Route to appropriate handler based on callback data prefix
            if data.startswith("instance:"):
                await self._handle_instance_switch(
                    data[9:], chat_id, callback_id, original_msg_id, topic_id
                )
            elif data.startswith("kill:"):
                await self._handle_instance_kill(
                    data[5:], chat_id, callback_id, original_msg_id, topic_id
                )
            elif data.startswith("session:"):
                await self._handle_session_switch(
                    data[8:], chat_id, callback_id, original_msg_id, topic_id
                )
            elif data.startswith("setmodel:") or data.startswith("sm:"):
                await self._handle_model_selection(
                    data, chat_id, callback_id, original_msg_id, topic_id
                )
            elif data.startswith("delete:"):
                await self._handle_session_delete(
                    data[7:], chat_id, callback_id, original_msg_id, topic_id
                )
            elif data.startswith("perm:"):
                await self._handle_permission(
                    data, chat_id, callback_id, original_msg_id, topic_id
                )
            elif data.startswith("q:"):
                await self._handle_question(
                    data, chat_id, callback_id, original_msg_id, topic_id
                )
            elif data.startswith("thread_inst:"):
                parts = data[12:].split(":", 1)
                if len(parts) == 2:
                    thread_id = int(parts[0])
                    instance_id = parts[1]
                    await self._handle_thread_instance_selection(
                        chat_id, thread_id, instance_id, callback_id, original_msg_id
                    )
            else:
                await self._safe_answer(callback_id)
                
        except Exception as e:
            logger.error(f"Error handling callback: {e}")
            await self._safe_answer(callback_id, text=f"Error: {str(e)[:100]}", show_alert=True)
    
    async def _safe_answer(
        self,
        callback_id: str,
        text: str = "",
        show_alert: bool = False,
    ) -> bool:
        """Safely answer a callback query."""
        try:
            await self.telegram.answer_callback_query(callback_id, text=text, show_alert=show_alert)
            return True
        except Exception as e:
            logger.debug(f"Could not answer callback (likely expired): {e}")
            return False
    
    async def _handle_instance_switch(
        self,
        instance_id: str,
        chat_id: int,
        callback_id: str,
        original_msg_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        """Switch chat to instance."""
        instance = self.process_manager.get_instance(instance_id)
        if not instance:
            await self._safe_answer(callback_id, text="Instance not found", show_alert=True)
            return
        
        self.session_router.set_current_instance(chat_id, instance, topic_id=topic_id)
        
        if topic_id is not None:
            self.session_router.set_topic_instance(chat_id, topic_id, instance.id)
        
        await self._safe_answer(callback_id, text=f"Switched to {instance.display_name}")
        
        try:
            await self.telegram.edit_message_with_keyboard(
                chat_id=str(chat_id),
                message_id=original_msg_id,
                text=f"Switched to `{instance.short_id}` ({instance.display_name})",
                inline_keyboard=[],
            )
        except Exception:
            pass
    
    async def _handle_instance_kill(
        self,
        instance_id: str,
        chat_id: int,
        callback_id: str,
        original_msg_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        """Kill an instance."""
        instance = self.process_manager.get_instance(instance_id)
        if not instance:
            await self._safe_answer(callback_id, text="Instance not found", show_alert=True)
            return
        
        success = await self.process_manager.stop_instance(instance_id)
        
        await self._safe_answer(callback_id, text="Instance stopped" if success else "Failed to stop")
        
        try:
            await self.telegram.edit_message_with_keyboard(
                chat_id=str(chat_id),
                message_id=original_msg_id,
                text=f"Stopped `{instance.short_id}` ({instance.display_name})",
                inline_keyboard=[],
            )
        except Exception:
            pass
    
    async def _handle_session_switch(
        self,
        session_id: str,
        chat_id: int,
        callback_id: str,
        original_msg_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        """Switch to a session within the current instance."""
        instance_id = self.session_router.get_current_instance_id(chat_id, topic_id)
        if not instance_id:
            await self._safe_answer(callback_id, text="No instance selected", show_alert=True)
            return
        
        instance = self.process_manager.get_instance(instance_id)
        if not instance:
            await self._safe_answer(callback_id, text="Instance not found", show_alert=True)
            return
        
        self.controller._update_handler_session(chat_id, session_id)
        
        await self._safe_answer(callback_id, text=f"Switched to session {session_id[:8]}")
        
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
        data: str,
        chat_id: int,
        callback_id: str,
        original_msg_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        """Handle model selection from inline keyboard."""
        instance_id = self.session_router.get_current_instance_id(chat_id, topic_id)
        if not instance_id:
            await self._safe_answer(callback_id, text="No instance selected", show_alert=True)
            return
        
        instance = self.process_manager.get_instance(instance_id)
        if not instance:
            await self._safe_answer(callback_id, text="Instance not found", show_alert=True)
            return
        
        handler = self.controller._get_instance_handler(instance, chat_id)
        model_info = handler.lookup_model_callback(data)
        
        if not model_info:
            await self._safe_answer(callback_id, text="Model not found", show_alert=True)
            return
        
        provider_id, model_id = model_info
        self.session_router.set_model_preference(chat_id, provider_id, model_id)
        
        await self._safe_answer(callback_id, text=f"Model set to {provider_id}/{model_id}")
        
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
        session_id: str,
        chat_id: int,
        callback_id: str,
        original_msg_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        """Handle session deletion from inline keyboard."""
        instance_id = self.session_router.get_current_instance_id(chat_id, topic_id)
        if not instance_id:
            await self._safe_answer(callback_id, text="No instance selected", show_alert=True)
            return
        
        instance = self.process_manager.get_instance(instance_id)
        if not instance:
            await self._safe_answer(callback_id, text="Instance not found", show_alert=True)
            return
        
        try:
            client = self.controller._get_instance_client(instance)
            await client.delete_session(session_id)
            
            if self.controller.chat_sessions.get(chat_id) == session_id:
                self.controller._update_handler_session(chat_id, None)
            
            await self._safe_answer(callback_id, text=f"Deleted session {session_id[:8]}")
            
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
            await self._safe_answer(callback_id, text=f"Error: {str(e)[:50]}", show_alert=True)
    
    async def _handle_permission(
        self,
        data: str,
        chat_id: int,
        callback_id: str,
        original_msg_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        """Handle permission response from inline keyboard."""
        parts = data.split(":", 2)
        if len(parts) != 3:
            await self._safe_answer(callback_id, text="Invalid callback")
            return
        
        action = parts[1]
        request_id = parts[2]
        
        instance_id = self.session_router.get_current_instance_id(chat_id, topic_id)
        if not instance_id:
            await self._safe_answer(callback_id, text="No instance", show_alert=True)
            return
        
        instance = self.process_manager.get_instance(instance_id)
        if not instance:
            await self._safe_answer(callback_id, text="Instance not found", show_alert=True)
            return
        
        try:
            client = self.controller._get_instance_client(instance)
            
            reply_map = {"y": "once", "a": "always", "n": "reject"}
            reply = reply_map.get(action, "reject")
            
            success = await client.reply_to_permission(request_id, reply)
            
            if success:
                action_text = {"y": "Allowed", "a": "Always allowed", "n": "Rejected"}.get(action, "Responded")
                await self._safe_answer(callback_id, text=action_text)
                
                if request_id in self.controller._notified_pending:
                    del self.controller._notified_pending[request_id]
                
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
                await self._safe_answer(callback_id, text="Failed", show_alert=True)
                
        except Exception as e:
            await self._safe_answer(callback_id, text=f"Error: {str(e)[:50]}", show_alert=True)
    
    async def _handle_question(
        self,
        data: str,
        chat_id: int,
        callback_id: str,
        original_msg_id: int,
        topic_id: Optional[int] = None,
    ) -> None:
        """Handle question response from inline keyboard."""
        parts = data.split(":", 2)
        if len(parts) != 3:
            await self._safe_answer(callback_id, text="Invalid callback")
            return
        
        request_id = parts[1]
        try:
            option_idx = int(parts[2])
        except ValueError:
            await self._safe_answer(callback_id, text="Invalid option")
            return
        
        instance_id = self.session_router.get_current_instance_id(chat_id, topic_id)
        if not instance_id:
            await self._safe_answer(callback_id, text="No instance", show_alert=True)
            return
        
        instance = self.process_manager.get_instance(instance_id)
        if not instance:
            await self._safe_answer(callback_id, text="Instance not found", show_alert=True)
            return
        
        try:
            client = self.controller._get_instance_client(instance)
            
            questions = await client.list_pending_questions()
            question = next((q for q in questions if q.get("id") == request_id), None)
            
            if not question:
                await self._safe_answer(callback_id, text="Question expired", show_alert=True)
                return
            
            q_list = question.get("questions", [])
            if not q_list:
                await self._safe_answer(callback_id, text="No questions", show_alert=True)
                return
            
            options = q_list[0].get("options", [])
            if option_idx >= len(options):
                await self._safe_answer(callback_id, text="Invalid option", show_alert=True)
                return
            
            selected_label = options[option_idx].get("label", "")
            
            logger.info(f"Responding to question {request_id} with: {selected_label}")
            success = await client.respond_to_question(request_id, [[selected_label]])
            logger.info(f"Question response success: {success}")
            
            if success:
                await self._safe_answer(callback_id, text=f"Selected: {selected_label[:30]}")
                
                if request_id in self.controller._notified_pending:
                    del self.controller._notified_pending[request_id]
                
                try:
                    await self.telegram.edit_message_with_keyboard(
                        chat_id=str(chat_id),
                        message_id=original_msg_id,
                        text=f"Selected: {selected_label}",
                        inline_keyboard=[],
                    )
                except Exception:
                    pass
                
                # Poll for response after answering question
                logger.info(f"Starting to poll for response after question in chat {chat_id} topic {topic_id}")
                await self.controller._poll_and_forward_response(instance, chat_id, topic_id)
            else:
                logger.error(f"Failed to respond to question {request_id}")
                await self._safe_answer(callback_id, text="Failed", show_alert=True)
                
        except Exception as e:
            await self._safe_answer(callback_id, text=f"Error: {str(e)[:50]}", show_alert=True)
    
    async def _handle_thread_instance_selection(
        self,
        chat_id: int,
        topic_id: int,
        instance_id: str,
        callback_id: str,
        original_msg_id: int,
    ) -> None:
        """Handle when user selects an instance for a thread."""
        # Find the full instance ID
        instance = None
        for inst in self.process_manager.list_instances():
            if inst.id.startswith(instance_id):
                instance = inst
                break
        
        if not instance:
            await self._safe_answer(callback_id, text="Instance not found", show_alert=True)
            return
        
        if not instance.is_alive:
            try:
                new_instance = await self.process_manager.restart_instance(instance.id)
                if new_instance and new_instance.is_alive:
                    instance = new_instance
                else:
                    await self._safe_answer(callback_id, text="Failed to start instance", show_alert=True)
                    return
            except Exception as e:
                await self._safe_answer(callback_id, text=f"Error: {str(e)[:50]}", show_alert=True)
                return
        
        # Map the thread to the instance
        self.session_router.set_topic_instance(chat_id, topic_id, instance.id)
        self.session_router.set_current_instance(chat_id, instance, topic_id=topic_id)
        self.session_router._save_state()
        
        await self._safe_answer(callback_id, text=f"Connected to {instance.display_name}")
        
        # Auto-rename the thread
        await self.controller._rename_topic(chat_id, topic_id, instance.display_name)
        
        try:
            await self.telegram.edit_message_with_keyboard(
                chat_id=str(chat_id),
                message_id=original_msg_id,
                text=(
                    f"📁 Connected to *{instance.display_name}*\n\n"
                    f"Path: `{instance.directory}`\n"
                    f"Instance: `{instance.short_id}`\n\n"
                    "Send any message to chat with OpenCode."
                ),
                inline_keyboard=[],
            )
        except Exception:
            pass
