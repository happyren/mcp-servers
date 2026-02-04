"""Message handling and forwarding to OpenCode instances.

Handles incoming Telegram messages and forwards them to the appropriate
OpenCode instance.
"""

import asyncio
import logging
import os
import subprocess
import webbrowser
from typing import TYPE_CHECKING, Any, Optional

import httpx

from ..instance import OpenCodeInstance

if TYPE_CHECKING:
    from ..controller import TelegramController

logger = logging.getLogger("telegram_controller.messages")

# Constants
TYPING_INTERVAL = 4.0  # Send typing indicator every N seconds


class MessageHandler:
    """Handles message forwarding to OpenCode instances."""
    
    def __init__(self, controller: "TelegramController"):
        """Initialize message handler.
        
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
    
    async def forward_to_instance(
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
            if topic_id is not None:
                await self._show_thread_instance_picker(chat_id, topic_id, text)
                return
            
            # Main chat without instance
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
            instance = await self._auto_resume_instance(instance, chat_id, topic_id)
            if not instance:
                return
        
        # Open browser on first request
        if not instance.browser_opened:
            self._open_browser_for_instance(instance)
        
        # Get or create HTTP client
        if instance.id not in self.controller.http_clients:
            self.controller.http_clients[instance.id] = httpx.AsyncClient(
                timeout=600.0,
                limits=httpx.Limits(max_keepalive_connections=5),
            )
        
        client = self.controller.http_clients[instance.id]
        
        # Get or create session
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
            
            # Immediately check for pending questions/permissions
            await self.controller._check_pending_for_instance(instance, chat_id, topic_id)
                
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
    
    async def _auto_resume_instance(
        self,
        instance: OpenCodeInstance,
        chat_id: int,
        topic_id: Optional[int],
    ) -> Optional[OpenCodeInstance]:
        """Auto-resume a stopped instance. Returns the resumed instance or None on failure."""
        logger.info(f"Auto-resuming stopped instance {instance.short_id} for chat {chat_id} topic {topic_id}")
        
        await self._send_text(
            chat_id,
            f"Resuming `{instance.display_name}`...",
            topic_id,
        )
        
        try:
            new_instance = await self.process_manager.restart_instance(instance.id)
            
            if new_instance and new_instance.is_alive:
                # Update mappings to new instance
                self.session_router.set_current_instance(chat_id, new_instance, topic_id=topic_id)
                if topic_id is not None:
                    self.session_router.set_topic_instance(chat_id, topic_id, new_instance.id)
                logger.info(f"Successfully resumed instance {new_instance.short_id}")
                return new_instance
            else:
                await self._send_text(
                    chat_id,
                    f"Failed to resume instance.\n\nUse `/open {instance.directory}` to manually restart.",
                    topic_id,
                )
                return None
        except Exception as e:
            logger.error(f"Error resuming instance: {e}")
            await self._send_text(
                chat_id,
                f"Failed to resume: {str(e)[:100]}\n\nUse `/open {instance.directory}` to manually restart.",
                topic_id,
            )
            return None
    
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
                timeout=600.0,
            )
            resp.raise_for_status()
            
            # Handle empty responses
            if resp.status_code == 204 or not resp.content:
                return ""
            
            try:
                data = resp.json()
            except Exception:
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
    
    async def poll_and_forward_response(
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
        session_id = self.session_router.get_session_id(chat_id, topic_id)
        if not session_id:
            logger.warning(f"No session found for chat {chat_id} topic {topic_id}")
            return
        
        logger.info(f"Polling for response in session {session_id[:8]}")
        client = self.controller._get_instance_client(instance)
        start_time = asyncio.get_event_loop().time()
        
        # Capture current message IDs before polling
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
                    
                    # Find new assistant messages
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
                            break
                    
                    # Check for pending questions/permissions
                    await self.controller._check_pending_for_instance(instance, chat_id, topic_id)
                    break
                    
                elif status_type == "question":
                    # New question - let pending check handle it
                    logger.info(f"Session {session_id[:8]} has a question, checking pending")
                    await self.controller._check_pending_for_instance(instance, chat_id, topic_id)
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
    
    async def _show_thread_instance_picker(
        self,
        chat_id: int,
        topic_id: int,
        original_message: str,
    ) -> None:
        """Show instance picker for a new thread without mapping."""
        running_instances = self.process_manager.get_running_instances()
        
        # Build keyboard
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
            # Check if the error indicates the topic was deleted
            if topic_id is not None and ("thread not found" in error_msg or "message_thread_id" in error_msg):
                logger.warning(f"Topic {topic_id} in chat {chat_id} appears to be deleted, cleaning up mapping")
                self.session_router.clear_topic_instance(chat_id, topic_id)
                self.session_router.clear_current_instance(chat_id, topic_id)
            raise
    
    def _open_browser_for_instance(self, instance: OpenCodeInstance) -> None:
        """Open browser for an instance if not already opened."""
        if instance.browser_opened:
            return
        
        url = instance.url
        logger.info(f"Opening browser for instance {instance.short_id} at {url}")
        
        try:
            if os.name == 'posix' and os.uname().sysname == 'Darwin':
                # macOS
                subprocess.Popen(
                    ['open', url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                # Cross-platform fallback
                webbrowser.open(url, new=2)
            
            instance.browser_opened = True
            self.process_manager._save_state()
            logger.info(f"Browser opened for instance {instance.short_id}")
            
        except Exception as e:
            logger.warning(f"Failed to open browser for instance {instance.short_id}: {e}")
