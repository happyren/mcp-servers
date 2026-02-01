#!/usr/bin/env python3
"""
Telegram-OpenCode Bridge Service

Watches the Telegram message queue file and forwards messages to OpenCode's HTTP API.
This allows Telegram messages to become prompts in OpenCode sessions.
Supports two-way communication: sends AI responses back to Telegram.

Usage:
    telegram-opencode-bridge --opencode-url http://localhost:4096 --reply
"""

import argparse
import asyncio
import json
import logging
import os
import signal
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .opencode_client import OpenCodeClient
from .command_handler import CommandHandler
from telegram_mcp_server.commands import get_bot_commands

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("telegram_opencode_bridge")


class TelegramClient:
    """Simple Telegram client for sending replies."""

    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self.client.aclose()

    async def send_message(self, chat_id: str | int, text: str) -> dict[str, Any]:
        """Send a message to a Telegram chat."""
        # Telegram has a 4096 character limit per message
        MAX_LENGTH = 4000
        if len(text) > MAX_LENGTH:
            text = text[:MAX_LENGTH] + "\n\n... (truncated)"

        response = await self.client.post(
            f"{self.base_url}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
            },
        )
        if response.status_code != 200:
            # Try without markdown if it fails
            response = await self.client.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                },
            )
        response.raise_for_status()
        return response.json()

    async def get_my_commands(self) -> list[dict[str, Any]]:
        """Get the current list of bot commands."""
        response = await self.client.post(
            f"{self.base_url}/getMyCommands"
        )
        response.raise_for_status()
        result = response.json()
        if result.get("ok"):
            return result.get("result", [])
        return []

    async def set_my_commands(self, commands: list[dict[str, str]]) -> dict[str, Any]:
        """Set the list of bot commands."""
        response = await self.client.post(
            f"{self.base_url}/setMyCommands",
            json={"commands": commands}
        )
        response.raise_for_status()
        return response.json()

    async def ensure_commands_set(self, commands: list[dict[str, str]], force: bool = False) -> bool:
        """Ensure bot commands are set, only setting if none exist or force=True."""
        try:
            existing = await self.get_my_commands()
            if not existing or force:
                logger.info(f"Setting bot commands ({len(commands)} commands)")
                await self.set_my_commands(commands)
                return True
            else:
                logger.debug(f"Bot commands already set ({len(existing)} commands)")
                return False
        except Exception as e:
            logger.warning(f"Failed to ensure bot commands: {e}")
            return False

    async def send_typing(self, chat_id: str | int) -> bool:
        """Send a typing indicator to a chat.
        
        Typing indicator lasts about 5 seconds or until a message is sent.
        Call this repeatedly for long-running operations.
        """
        try:
            response = await self.client.post(
                f"{self.base_url}/sendChatAction",
                json={
                    "chat_id": chat_id,
                    "action": "typing",
                },
            )
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"Failed to send typing indicator: {e}")
            return False


class TelegramOpenCodeBridge:
    """Bridge service that watches Telegram queue and sends to OpenCode."""

    def __init__(
        self,
        opencode_url: str = "http://localhost:4096",
        queue_dir: str | None = None,
        poll_interval: int = 2,
        reply_to_telegram: bool = True,
        bot_token: str | None = None,
        provider_id: str = "deepseek",
        model_id: str = "deepseek-reasoner",
    ):
        self.opencode = OpenCodeClient(opencode_url)
        self.command_handler = CommandHandler(self.opencode)
        self.poll_interval = poll_interval
        self.reply_to_telegram = reply_to_telegram
        self.running = False
        self.session_id: str | None = None
        self.session_model: tuple[str, str] | None = None  # (provider_id, model_id) of current session
        self.default_provider_id = provider_id
        self.default_model_id = model_id
        # Track all sessions created: {session_id: (provider_id, model_id)}
        self.sessions: dict[str, tuple[str, str]] = {}

        # Telegram client for replies
        self.telegram: TelegramClient | None = None
        if reply_to_telegram:
            token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
            if token:
                self.telegram = TelegramClient(token)
            else:
                logger.warning("No bot token provided, replies disabled")
                self.reply_to_telegram = False

        # Queue file location
        if queue_dir:
            self.queue_dir = Path(queue_dir).expanduser()
        else:
            self.queue_dir = Path("~/.local/share/telegram_mcp_server").expanduser()

        self.queue_file = self.queue_dir / "message_queue.json"
        self.bridge_state_file = self.queue_dir / "bridge_state.json"

        # Track which messages we've already forwarded
        self.forwarded_ids: set[int] = set()
        
        # Track pending questions awaiting Telegram response
        # Format: {telegram_msg_id: {"request_id": str, "session_id": str, "questions": list, "options": list}}
        self.pending_questions: dict[int, dict[str, Any]] = {}
        
        # Track pending permissions awaiting Telegram response  
        # Format: {telegram_msg_id: {"request_id": str, "session_id": str, "permission": str, "patterns": list}}
        self.pending_permissions: dict[int, dict[str, Any]] = {}
        
        # Track the chat_id for the current session (for question/permission forwarding)
        self.current_chat_id: int | None = None
        
        # Load state (forwarded_ids and session info)
        self._load_state()

    def _load_state(self) -> None:
        """Load bridge state from file (forwarded IDs and session info)."""
        if not self.bridge_state_file.exists():
            return
        try:
            with open(self.bridge_state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.forwarded_ids = set(data.get("forwarded_ids", []))
                # Load session info
                self.session_id = data.get("session_id")
                session_model = data.get("session_model")
                if session_model and len(session_model) == 2:
                    self.session_model = tuple(session_model)
                # Load all tracked sessions
                sessions = data.get("sessions", {})
                self.sessions = {k: tuple(v) for k, v in sessions.items() if len(v) == 2}
                # Load pending questions
                pending = data.get("pending_questions", {})
                self.pending_questions = {int(k): v for k, v in pending.items()}
                # Load pending permissions
                pending_perms = data.get("pending_permissions", {})
                self.pending_permissions = {int(k): v for k, v in pending_perms.items()}
                # Load current chat_id
                self.current_chat_id = data.get("current_chat_id")
                if self.session_id:
                    logger.info(f"Loaded saved session: {self.session_id[:8]}... with model {self.session_model}")
                    self.command_handler.current_session_id = self.session_id
        except (json.JSONDecodeError, FileNotFoundError):
            pass

    def _save_state(self) -> None:
        """Save bridge state to file (forwarded IDs and session info)."""
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        # Convert tuples to lists for JSON serialization
        sessions_serializable = {k: list(v) for k, v in self.sessions.items()}
        session_model_list = list(self.session_model) if self.session_model else None
        # Convert pending questions/permissions keys to strings for JSON
        pending_q_serializable = {str(k): v for k, v in self.pending_questions.items()}
        pending_p_serializable = {str(k): v for k, v in self.pending_permissions.items()}
        data = {
            "forwarded_ids": list(self.forwarded_ids),
            "session_id": self.session_id,
            "session_model": session_model_list,
            "sessions": sessions_serializable,
            "pending_questions": pending_q_serializable,
            "pending_permissions": pending_p_serializable,
            "current_chat_id": self.current_chat_id,
            "last_updated": datetime.now().isoformat(),
        }
        with open(self.bridge_state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _read_queue(self) -> list[dict[str, Any]]:
        """Read messages from queue file."""
        if not self.queue_file.exists():
            return []
        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _remove_from_queue(self, message_ids: set[int]) -> None:
        """Remove processed messages from queue."""
        queue = self._read_queue()
        remaining = [msg for msg in queue if msg.get("message_id") not in message_ids]
        with open(self.queue_file, "w", encoding="utf-8") as f:
            json.dump(remaining, f, indent=2, ensure_ascii=False)

    async def process_queue(self) -> None:
        """Process any new messages in the queue."""
        queue = self._read_queue()

        if not queue:
            return

        # Filter to messages we haven't forwarded yet
        new_messages = [
            msg for msg in queue
            if msg.get("message_id") not in self.forwarded_ids
        ]

        if not new_messages:
            return

        # Process each message
        processed_ids: set[int] = set()
        for msg in new_messages:
            msg_id: int | None = msg.get("message_id")
            text = msg.get("text", "")
            username = msg.get("from_username", "Unknown")
            chat_id: int | None = msg.get("chat_id")

            if msg_id is None:
                logger.warning("Message has no message_id, skipping")
                continue

            if not text:
                logger.debug(f"Skipping message {msg_id} (no text)")
                self.forwarded_ids.add(msg_id)
                processed_ids.add(msg_id)
                continue

            # Sync command handler's session_id with bridge's session_id
            self.command_handler.current_session_id = self.session_id

            # Check if this is a command (starts with /)
            response = await self.command_handler.handle_command(text, chat_id)

            if response is not None:
                # It was a command, send the response back
                logger.info(f"Handled command: {text[:50]}...")
                if self.telegram and chat_id:
                    await self.telegram.send_message(chat_id, response)
                # Update session_id in case it was changed by command
                self.session_id = self.command_handler.current_session_id
                self.forwarded_ids.add(msg_id)
                processed_ids.add(msg_id)
                continue

            # Check if this is a response to a pending question
            if chat_id and self.pending_questions:
                is_question_response = await self._check_for_question_response(text, chat_id)
                if is_question_response:
                    logger.info(f"Handled as question response: {text[:50]}...")
                    self.forwarded_ids.add(msg_id)
                    processed_ids.add(msg_id)
                    continue

            # Check if this is a response to a pending permission
            if chat_id and self.pending_permissions:
                is_permission_response = await self._check_for_permission_response(text, chat_id)
                if is_permission_response:
                    logger.info(f"Handled as permission response: {text[:50]}...")
                    self.forwarded_ids.add(msg_id)
                    processed_ids.add(msg_id)
                    continue

            # Not a command, question response, or permission response - process as regular prompt
            logger.info(f"Processing regular prompt: {text[:50]}...")

            # Use requested model or fall back to default
            provider_id = self.default_provider_id
            model_id = self.default_model_id

            # Handle session management - get or create session
            need_new_session = False

            if not self.session_id:
                # No current session, try to find an existing one from OpenCode
                logger.info("No current session, checking for existing sessions...")
                existing_session_id = await self.opencode.get_existing_session()
                if existing_session_id:
                    self.session_id = existing_session_id
                    # Check if we have model info for this session from persistence
                    if existing_session_id in self.sessions:
                        self.session_model = self.sessions[existing_session_id]
                        logger.info(f"Reusing existing session from OpenCode: {self.session_id[:8]}... with model {self.session_model[0]}/{self.session_model[1]}")
                    else:
                        # No model info known, use requested/default model for this session
                        self.session_model = (provider_id, model_id)
                        self.sessions[existing_session_id] = (provider_id, model_id)
                        logger.info(f"Reusing existing session from OpenCode: {self.session_id[:8]}... (model set to {provider_id}/{model_id})")
                else:
                    logger.info("No existing sessions found, will create new one")
                    need_new_session = True
            else:
                # We have a session_id loaded from state - validate it still exists in OpenCode
                # This handles the case when OpenCode restarts and old sessions are gone
                logger.info(f"Validating saved session {self.session_id[:8]}... against OpenCode...")
                all_sessions = await self.opencode.list_sessions()
                session_ids = [str(s.get("id")) for s in all_sessions if s.get("id")]

                if self.session_id not in session_ids:
                    logger.warning(f"Saved session {self.session_id[:8]}... no longer exists in OpenCode (likely due to restart)")
                    self.session_id = None
                    self.session_model = None
                    # Try to find any existing session
                    if session_ids:
                        self.session_id = session_ids[0]
                        if self.session_id in self.sessions:
                            self.session_model = self.sessions[self.session_id]
                            logger.info(f"Using existing OpenCode session: {self.session_id[:8]}... with model {self.session_model[0]}/{self.session_model[1]}")
                        else:
                            self.session_model = (provider_id, model_id)
                            self.sessions[self.session_id] = (provider_id, model_id)
                            logger.info(f"Using existing OpenCode session: {self.session_id[:8]}... (model set to {provider_id}/{model_id})")
                    else:
                        logger.info("No sessions found in OpenCode, will create new one")
                        need_new_session = True
                elif self.session_model:
                    # Session exists and we have model info
                    current_provider, current_model = self.session_model
                    logger.info(f"Reusing saved session {self.session_id[:8]}... with model {current_provider}/{current_model}")
                else:
                    # Session exists but no model info - use default
                    self.session_model = (provider_id, model_id)
                    self.sessions[self.session_id] = (provider_id, model_id)
                    logger.info(f"Reusing saved session {self.session_id[:8]}... (model set to {provider_id}/{model_id})")

            if need_new_session:
                logger.info(f"Creating new session for model {provider_id}/{model_id}...")
                new_session = await self.opencode.create_session()
                if new_session:
                    self.session_id = new_session["id"]
                    self.session_model = (provider_id, model_id)
                    assert self.session_id is not None  # Type safety
                    self.sessions[self.session_id] = (provider_id, model_id)
                    logger.info(f"Created new session: {self.session_id[:8]}... with model {provider_id}/{model_id}")
                else:
                    logger.error("Failed to create new session")
                    continue

            session_id = self.session_id
            if not session_id:
                logger.error("No session available")
                continue

            # Format the prompt with context
            prompt = f"[Telegram from @{username}]: {text}"

            try:
                if self.reply_to_telegram and self.telegram and chat_id:
                    # Use non-blocking mode with typing indicator
                    logger.info(f"Sending to OpenCode: {text[:50]}...")
                    
                    # Track current chat_id for question forwarding
                    self.current_chat_id = chat_id
                    
                    response_text = await self._send_with_typing(
                        session_id=session_id,
                        prompt=prompt,
                        chat_id=chat_id,
                        provider_id=provider_id,
                        model_id=model_id,
                        current_msg_id=msg_id,
                    )
                    logger.info(f"OpenCode response received for message {msg_id}")

                    # Check for any pending questions or permissions after response
                    await self._handle_pending_questions(session_id, chat_id)
                    await self._handle_pending_permissions(session_id, chat_id)

                    # Always send response back to Telegram, even if empty
                    if not response_text:
                        response_text = "AI returned empty response (execution may have been aborted)"
                        logger.warning(f"Empty response from OpenCode for message {msg_id}, sending default message")

                    logger.info(f"Sending reply to Telegram: {response_text[:50]}...")
                    await self.telegram.send_message(chat_id, response_text)
                    logger.info(f"Reply sent to Telegram for message {msg_id}")
                    # Persist this session as the last interacted one
                    self._save_state()
                    if self.session_id and self.session_model:
                        logger.info(f"Persisted last interacted session: {self.session_id[:8]}... with model {self.session_model[0]}/{self.session_model[1]}")
                    elif self.session_id:
                        logger.info(f"Persisted last interacted session: {self.session_id[:8]}...")
                else:
                    # Send asynchronously (no reply)
                    logger.info(f"Sending to OpenCode (async): {text[:50]}...")
                    await self.opencode.send_prompt_async(session_id, prompt)
                    logger.info(f"Message {msg_id} queued in OpenCode")

                self.forwarded_ids.add(msg_id)
                processed_ids.add(msg_id)

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error sending message {msg_id}: {e.response.status_code}")
                # If session is invalid, clear it so we get a new one
                if e.response.status_code in (404, 400):
                    logger.info(f"Clearing session due to HTTP {e.response.status_code} error")
                    self.session_id = None
                    self.session_model = None

                # Send error to Telegram if reply is enabled
                if self.reply_to_telegram and self.telegram and chat_id:
                    try:
                        error_msg = f"OpenCode error {e.response.status_code}"
                        # Try to extract error details from response
                        try:
                            error_data = e.response.json()
                            error_detail = error_data.get('error', {}).get('message', str(e))
                            error_msg = f"OpenCode error {e.response.status_code}: {error_detail[:200]}"
                        except:
                            error_msg = f"OpenCode error {e.response.status_code}: {str(e)[:200]}"

                        await self.telegram.send_message(chat_id, error_msg)
                        logger.info(f"Sent error to Telegram for message {msg_id}")
                    except Exception as send_error:
                        logger.error(f"Failed to send error to Telegram: {send_error}")

                # Mark as forwarded to avoid resending
                self.forwarded_ids.add(msg_id)
                processed_ids.add(msg_id)
            except Exception as e:
                logger.error(f"Error sending message {msg_id} to OpenCode: {e}")

                # Send error to Telegram if reply is enabled
                if self.reply_to_telegram and self.telegram and chat_id:
                    try:
                        error_msg = f"Error processing message: {str(e)[:200]}"
                        await self.telegram.send_message(chat_id, error_msg)
                        logger.info(f"Sent error to Telegram for message {msg_id}")
                    except Exception as send_error:
                        logger.error(f"Failed to send error to Telegram: {send_error}")

                # Mark as forwarded to avoid resending
                self.forwarded_ids.add(msg_id)
                processed_ids.add(msg_id)

        # Save state and clean up queue
        if processed_ids:
            self._save_state()
            self._remove_from_queue(processed_ids)

    async def _send_with_typing(
        self,
        session_id: str,
        prompt: str,
        chat_id: int,
        provider_id: str,
        model_id: str,
        timeout: float = 600.0,
        current_msg_id: int | None = None,
    ) -> str:
        """Send a message to OpenCode while showing typing indicator.
        
        This method:
        1. Sends the prompt to OpenCode (blocking API call)
        2. Shows typing indicator every 4 seconds while waiting
        3. Polls for and forwards questions/permissions to Telegram during the wait
        4. When user responds via Telegram, sends the response back to OpenCode
        
        Args:
            session_id: The OpenCode session ID
            prompt: The prompt to send
            chat_id: Telegram chat ID for typing indicator
            provider_id: AI provider ID
            model_id: AI model ID
            timeout: Maximum time to wait for response
            current_msg_id: The message ID currently being processed (to skip in response checking)
            
        Returns:
            The response text from OpenCode
        """
        import time
        
        # Create a task for the actual API call
        async def send_message():
            return await self.opencode.send_message_text(
                session_id,
                prompt,
                provider_id=provider_id,
                model_id=model_id,
            )
        
        # Create the send task
        send_task = asyncio.create_task(send_message())
        
        # Keep sending typing indicator while waiting
        typing_interval = 4.0  # Telegram typing indicator lasts ~5 seconds
        start_time = time.time()
        
        try:
            while not send_task.done():
                # Send typing indicator
                if self.telegram:
                    await self.telegram.send_typing(chat_id)
                
                # Poll for pending questions/permissions and forward to Telegram
                # This is crucial: OpenCode blocks on permission requests, so we need
                # to poll and handle them while the API call is pending
                await self._poll_and_forward_pending(session_id, chat_id)
                
                # Check if there are any pending questions/permissions that need responses
                # and process incoming Telegram messages to respond to them
                await self._check_telegram_for_responses(chat_id, current_msg_id)
                
                # Wait for task or next typing interval
                try:
                    # Wait with a short timeout to allow periodic typing updates
                    result = await asyncio.wait_for(
                        asyncio.shield(send_task),
                        timeout=typing_interval
                    )
                    return result
                except asyncio.TimeoutError:
                    # Task not done yet, continue loop to send another typing indicator
                    elapsed = time.time() - start_time
                    if elapsed >= timeout:
                        send_task.cancel()
                        raise asyncio.TimeoutError(f"OpenCode response timed out after {timeout}s")
                    continue
            
            # Task completed
            return await send_task
            
        except asyncio.CancelledError:
            send_task.cancel()
            raise
        except Exception as e:
            if not send_task.done():
                send_task.cancel()
            raise

    async def _poll_and_forward_pending(self, session_id: str, chat_id: int) -> None:
        """Poll for pending questions/permissions and forward them to Telegram.
        
        This is called during the typing loop to check if OpenCode is waiting
        for user input (questions or permissions) and forward those to Telegram.
        """
        if not self.telegram:
            return
        
        try:
            # Check for pending questions
            questions = await self.opencode.get_pending_questions(session_id)
            for qr in questions:
                request_id = qr.get("id")
                # Check if we've already forwarded this question
                already_forwarded = any(
                    pq.get("request_id") == request_id 
                    for pq in self.pending_questions.values()
                )
                if already_forwarded:
                    continue
                    
                formatted = await self._format_question_for_telegram(qr)
                result = await self.telegram.send_message(chat_id, formatted)
                
                if result.get("ok"):
                    sent_msg = result.get("result", {})
                    sent_msg_id = sent_msg.get("message_id")
                    if sent_msg_id:
                        questions_list = qr.get("questions", [])
                        first_q = questions_list[0] if questions_list else {}
                        
                        self.pending_questions[sent_msg_id] = {
                            "request_id": request_id,
                            "session_id": qr.get("sessionID"),
                            "questions": questions_list,
                            "options": first_q.get("options", []),
                            "multiple": first_q.get("multiple", False),
                            "custom": first_q.get("custom", True) if first_q.get("custom") is not None else True,
                        }
                        self._save_state()
                        logger.info(f"Forwarded question to Telegram during wait (msg_id={sent_msg_id}): {request_id}")
            
            # Check for pending permissions
            permissions = await self.opencode.get_pending_permissions(session_id)
            for pr in permissions:
                request_id = pr.get("id")
                # Check if we've already forwarded this permission
                already_forwarded = any(
                    pp.get("request_id") == request_id 
                    for pp in self.pending_permissions.values()
                )
                if already_forwarded:
                    continue
                    
                formatted = await self._format_permission_for_telegram(pr)
                result = await self.telegram.send_message(chat_id, formatted)
                
                if result.get("ok"):
                    sent_msg = result.get("result", {})
                    sent_msg_id = sent_msg.get("message_id")
                    if sent_msg_id:
                        self.pending_permissions[sent_msg_id] = {
                            "request_id": request_id,
                            "session_id": pr.get("sessionID"),
                            "permission": pr.get("permission"),
                            "patterns": pr.get("patterns", []),
                        }
                        self._save_state()
                        logger.info(f"Forwarded permission to Telegram during wait (msg_id={sent_msg_id}): {request_id}")
                        
        except Exception as e:
            logger.debug(f"Error polling for pending requests: {e}")

    async def _check_telegram_for_responses(self, chat_id: int, skip_msg_id: int | None = None) -> None:
        """Check for new Telegram messages that might be responses to pending questions/permissions.
        
        This reads from the message queue and processes any responses.
        
        Args:
            chat_id: The Telegram chat ID to check messages for
            skip_msg_id: A message ID to skip (the message currently being processed)
        """
        if not self.pending_questions and not self.pending_permissions:
            return
            
        # Read the queue for new messages
        queue = self._read_queue()
        if not queue:
            return
        
        # Look for new messages we haven't processed yet
        for msg in queue:
            msg_id = msg.get("message_id")
            if msg_id is None or msg_id in self.forwarded_ids:
                continue
            
            # Skip the message that triggered the current processing
            if skip_msg_id is not None and msg_id == skip_msg_id:
                continue
            
            msg_chat_id = msg.get("chat_id")
            if msg_chat_id != chat_id:
                continue
                
            text = msg.get("text", "")
            if not text:
                continue
            
            # Skip commands - they'll be processed in main loop
            if text.startswith("/"):
                continue
            
            # Try to handle as question response
            if self.pending_questions:
                is_question_response = await self._check_for_question_response(text, chat_id)
                if is_question_response:
                    logger.info(f"Handled question response during wait: {text[:50]}...")
                    self.forwarded_ids.add(msg_id)
                    self._save_state()
                    # Remove from queue
                    self._remove_from_queue({msg_id})
                    continue
            
            # Try to handle as permission response
            if self.pending_permissions:
                is_permission_response = await self._check_for_permission_response(text, chat_id)
                if is_permission_response:
                    logger.info(f"Handled permission response during wait: {text[:50]}...")
                    self.forwarded_ids.add(msg_id)
                    self._save_state()
                    # Remove from queue
                    self._remove_from_queue({msg_id})
                    continue

    async def _format_question_for_telegram(self, question_request: dict[str, Any]) -> str:
        """Format an OpenCode QuestionRequest for Telegram display.
        
        Args:
            question_request: QuestionRequest with id, sessionID, questions array
            
        Returns:
            Formatted question string for Telegram
        """
        questions = question_request.get("questions", [])
        if not questions:
            return "*No questions in request*"
        
        all_lines = []
        
        for i, q in enumerate(questions):
            header = q.get("header", "Question")
            question_text = q.get("question", "Please respond:")
            options = q.get("options", [])
            multiple = q.get("multiple", False)
            custom = q.get("custom", True)
            
            if len(questions) > 1:
                all_lines.append(f"*{i+1}. {header}*")
            else:
                all_lines.append(f"*{header}*")
            all_lines.append("")
            all_lines.append(question_text)
            all_lines.append("")
            
            if options:
                all_lines.append("Options:")
                for j, opt in enumerate(options, 1):
                    label = opt.get("label", f"Option {j}")
                    desc = opt.get("description", "")
                    if desc:
                        all_lines.append(f"  {j}. {label} - {desc}")
                    else:
                        all_lines.append(f"  {j}. {label}")
            
            if multiple:
                all_lines.append("\n(You can select multiple options)")
            
            if custom:
                all_lines.append("\nReply with option number(s) or type your answer:")
            elif options:
                all_lines.append("\nReply with option number(s):")
            
            if i < len(questions) - 1:
                all_lines.append("\n---\n")
        
        return "\n".join(all_lines)

    async def _handle_pending_questions(self, session_id: str, chat_id: int) -> bool:
        """Check for and handle pending questions from OpenCode.
        
        Args:
            session_id: The OpenCode session ID
            chat_id: Telegram chat ID to send questions to
            
        Returns:
            True if there are pending questions, False otherwise
        """
        if not self.telegram:
            return False
            
        question_requests = await self.opencode.get_pending_questions(session_id)
        
        if not question_requests:
            return False
        
        for qr in question_requests:
            formatted = await self._format_question_for_telegram(qr)
            result = await self.telegram.send_message(chat_id, formatted)
            
            # Get the sent message ID to track the question
            if result.get("ok"):
                sent_msg = result.get("result", {})
                sent_msg_id = sent_msg.get("message_id")
                if sent_msg_id:
                    # Store the question context for response handling
                    # Get the first question's options for parsing (simplified for single-question requests)
                    questions = qr.get("questions", [])
                    first_q = questions[0] if questions else {}
                    
                    self.pending_questions[sent_msg_id] = {
                        "request_id": qr.get("id"),
                        "session_id": qr.get("sessionID"),
                        "questions": questions,
                        # For single-question requests, store first question's options for easy parsing
                        "options": first_q.get("options", []),
                        "multiple": first_q.get("multiple", False),
                        "custom": first_q.get("custom", True) if first_q.get("custom") is not None else True,
                    }
                    self._save_state()
                    request_id = qr.get("id", "unknown")
                    logger.info(f"Forwarded question to Telegram (msg_id={sent_msg_id}): {request_id[:30] if len(request_id) > 30 else request_id}")
            else:
                logger.warning(f"Failed to send question to Telegram: {result}")
        
        return True

    async def _check_for_question_response(self, text: str, chat_id: int) -> bool:
        """Check if an incoming message is a response to a pending question.
        
        Args:
            text: The incoming message text
            chat_id: The Telegram chat ID
            
        Returns:
            True if the message was handled as a question response, False otherwise
        """
        if not self.pending_questions:
            return False
        
        # Get the oldest pending question (FIFO)
        oldest_q_msg_id = min(self.pending_questions.keys())
        q_context = self.pending_questions[oldest_q_msg_id]
        
        # Parse the user's response
        options = q_context.get("options", [])
        custom_allowed = q_context.get("custom", True)
        
        answer: list[str] = []
        
        # Try to parse as option number(s)
        text_stripped = text.strip()
        
        # Check if user typed a number or comma-separated numbers
        parts = [p.strip() for p in text_stripped.replace(",", " ").split()]
        all_numbers = True
        selected_indices = []
        
        for part in parts:
            try:
                idx = int(part)
                if 1 <= idx <= len(options):
                    selected_indices.append(idx - 1)  # Convert to 0-based
                else:
                    all_numbers = False
                    break
            except ValueError:
                all_numbers = False
                break
        
        if all_numbers and selected_indices:
            # User selected option(s) by number
            for idx in selected_indices:
                answer.append(options[idx].get("label", f"Option {idx + 1}"))
        elif custom_allowed:
            # User typed a custom response
            answer = [text_stripped]
        else:
            # Not a valid response, don't consume as answer
            return False
        
        # Send the response to OpenCode using the Question API
        request_id = q_context.get("request_id")
        
        if request_id:
            # The Question API expects answers as list of list of strings
            # One answer array per question in the request
            # For now we assume single-question requests
            success = await self.opencode.respond_to_question(
                request_id=request_id,
                answers=[answer],  # Wrap in list for the answers array
            )
            
            if success:
                logger.info(f"Sent question response to OpenCode: {answer}")
                # Remove from pending questions
                del self.pending_questions[oldest_q_msg_id]
                self._save_state()
                
                # Send confirmation to Telegram
                if self.telegram:
                    await self.telegram.send_message(
                        chat_id,
                        f"✓ Response recorded: {', '.join(answer)}"
                    )
                return True
            else:
                logger.error("Failed to send question response to OpenCode")
                if self.telegram:
                    await self.telegram.send_message(
                        chat_id,
                        "❌ Failed to send response to OpenCode. Please try again."
                    )
                return False
        
        return False

    async def _format_permission_for_telegram(self, permission_request: dict[str, Any]) -> str:
        """Format an OpenCode PermissionRequest for Telegram display.
        
        Args:
            permission_request: PermissionRequest with id, sessionID, permission, patterns, metadata
            
        Returns:
            Formatted permission request string for Telegram
        """
        permission = permission_request.get("permission", "unknown")
        patterns = permission_request.get("patterns", [])
        metadata = permission_request.get("metadata", {})
        
        lines = ["*🔐 Permission Request*", ""]
        lines.append(f"Tool: `{permission}`")
        
        if patterns:
            if len(patterns) == 1:
                lines.append(f"Pattern: `{patterns[0]}`")
            else:
                lines.append("Patterns:")
                for p in patterns[:5]:  # Limit to first 5
                    lines.append(f"  • `{p}`")
                if len(patterns) > 5:
                    lines.append(f"  ... and {len(patterns) - 5} more")
        
        # Add relevant metadata (like command for bash)
        if metadata:
            if "command" in metadata:
                cmd = metadata["command"]
                if len(cmd) > 200:
                    cmd = cmd[:200] + "..."
                lines.append(f"\nCommand: `{cmd}`")
            if "path" in metadata:
                lines.append(f"Path: `{metadata['path']}`")
        
        lines.append("\n*Reply with:*")
        lines.append("  `y` or `yes` - Allow once")
        lines.append("  `a` or `always` - Always allow")
        lines.append("  `n` or `no` - Reject")
        lines.append("  Any other text - Reject with feedback")
        
        return "\n".join(lines)

    async def _handle_pending_permissions(self, session_id: str, chat_id: int) -> bool:
        """Check for and handle pending permissions from OpenCode.
        
        Args:
            session_id: The OpenCode session ID
            chat_id: Telegram chat ID to send permissions to
            
        Returns:
            True if there are pending permissions, False otherwise
        """
        if not self.telegram:
            return False
            
        permission_requests = await self.opencode.get_pending_permissions(session_id)
        
        if not permission_requests:
            return False
        
        for pr in permission_requests:
            formatted = await self._format_permission_for_telegram(pr)
            result = await self.telegram.send_message(chat_id, formatted)
            
            # Get the sent message ID to track the permission
            if result.get("ok"):
                sent_msg = result.get("result", {})
                sent_msg_id = sent_msg.get("message_id")
                if sent_msg_id:
                    self.pending_permissions[sent_msg_id] = {
                        "request_id": pr.get("id"),
                        "session_id": pr.get("sessionID"),
                        "permission": pr.get("permission"),
                        "patterns": pr.get("patterns", []),
                    }
                    self._save_state()
                    request_id = pr.get("id", "unknown")
                    logger.info(f"Forwarded permission to Telegram (msg_id={sent_msg_id}): {request_id}")
            else:
                logger.warning(f"Failed to send permission to Telegram: {result}")
        
        return True

    async def _check_for_permission_response(self, text: str, chat_id: int) -> bool:
        """Check if an incoming message is a response to a pending permission.
        
        Args:
            text: The incoming message text
            chat_id: The Telegram chat ID
            
        Returns:
            True if the message was handled as a permission response, False otherwise
        """
        if not self.pending_permissions:
            return False
        
        # Get the oldest pending permission (FIFO)
        oldest_p_msg_id = min(self.pending_permissions.keys())
        p_context = self.pending_permissions[oldest_p_msg_id]
        
        text_lower = text.strip().lower()
        
        # Parse the response
        reply: str | None = None
        message: str | None = None
        
        if text_lower in ("y", "yes", "1", "ok", "allow"):
            reply = "once"
        elif text_lower in ("a", "always", "remember"):
            reply = "always"
        elif text_lower in ("n", "no", "0", "deny", "reject"):
            reply = "reject"
        else:
            # Treat as rejection with feedback message
            reply = "reject"
            message = text.strip()
        
        # Send the response to OpenCode
        request_id = p_context.get("request_id")
        
        if request_id:
            success = await self.opencode.reply_to_permission(
                request_id=request_id,
                reply=reply,
                message=message,
            )
            
            if success:
                logger.info(f"Sent permission response to OpenCode: {reply}" + (f" with message: {message}" if message else ""))
                # Remove from pending permissions
                del self.pending_permissions[oldest_p_msg_id]
                self._save_state()
                
                # Send confirmation to Telegram
                if self.telegram:
                    if reply == "once":
                        await self.telegram.send_message(chat_id, "✓ Permission granted (once)")
                    elif reply == "always":
                        await self.telegram.send_message(chat_id, "✓ Permission granted (always)")
                    elif message:
                        await self.telegram.send_message(chat_id, f"✓ Permission rejected with feedback")
                    else:
                        await self.telegram.send_message(chat_id, "✓ Permission rejected")
                return True
            else:
                logger.error("Failed to send permission response to OpenCode")
                if self.telegram:
                    await self.telegram.send_message(
                        chat_id,
                        "❌ Failed to send response to OpenCode. Please try again."
                    )
                return False
        
        return False

    async def run(self) -> None:
        """Run the bridge service continuously."""
        self.running = True

        # Handle graceful shutdown
        def signal_handler(signum, frame):
            logger.info("Received shutdown signal")
            self.running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        logger.info("Starting Telegram-OpenCode Bridge")
        logger.info(f"OpenCode URL: {self.opencode.base_url}")
        logger.info(f"Queue file: {self.queue_file}")
        logger.info(f"Poll interval: {self.poll_interval}s")
        logger.info(f"Reply to Telegram: {self.reply_to_telegram}")
        logger.info(f"Default Provider: {self.default_provider_id}, Default Model: {self.default_model_id}")

        # Check OpenCode connection
        if not await self.opencode.is_server_running():
            logger.error(f"Cannot connect to OpenCode at {self.opencode.base_url}")
            logger.error("Make sure OpenCode is running with: opencode --port 4096")
            return

        logger.info("Connected to OpenCode server")

        # Ensure bot commands are set if Telegram client is available
        if self.telegram:
            try:
                commands = get_bot_commands()
                await self.telegram.ensure_commands_set(commands)
                logger.info(f"Ensured bot commands are set ({len(commands)} commands)")
            except Exception as e:
                logger.warning(f"Failed to set bot commands: {e}")

        try:
            while self.running:
                await self.process_queue()
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            logger.info("Bridge cancelled")
        finally:
            logger.info("Shutting down bridge service")
            await self.opencode.close()
            if self.telegram:
                await self.telegram.close()
            self._save_state()


async def async_main(args: argparse.Namespace) -> None:
    """Async entry point."""
    bridge = TelegramOpenCodeBridge(
        opencode_url=args.opencode_url,
        queue_dir=args.queue_dir,
        poll_interval=args.interval,
        reply_to_telegram=args.reply,
        bot_token=args.bot_token,
        provider_id=args.provider,
        model_id=args.model,
    )
    await bridge.run()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Bridge Telegram messages to OpenCode with two-way communication",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start bridge with replies enabled (default)
  telegram-opencode-bridge

  # Connect to OpenCode on custom port
  telegram-opencode-bridge --opencode-url http://localhost:8080

  # Disable replies (one-way: Telegram -> OpenCode only)
  telegram-opencode-bridge --no-reply

  # Custom poll interval
  telegram-opencode-bridge --interval 5

Environment variables:
  TELEGRAM_BOT_TOKEN  - Bot token for sending replies (or use --bot-token)
        """,
    )
    parser.add_argument(
        "--opencode-url",
        default="http://localhost:4096",
        help="OpenCode HTTP API URL (default: http://localhost:4096)",
    )
    parser.add_argument(
        "--queue-dir",
        default=None,
        help="Directory containing message_queue.json (default: ~/.local/share/telegram_mcp_server)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=2,
        help="Poll interval in seconds (default: 2)",
    )
    parser.add_argument(
        "--reply",
        action="store_true",
        default=True,
        help="Send OpenCode responses back to Telegram (default: enabled)",
    )
    parser.add_argument(
        "--no-reply",
        action="store_true",
        help="Disable sending responses back to Telegram",
    )
    parser.add_argument(
        "--bot-token",
        default=None,
        help="Telegram bot token (or set TELEGRAM_BOT_TOKEN env var)",
    )
    parser.add_argument(
        "--provider",
        default="deepseek",
        help="OpenCode provider ID (default: deepseek)",
    )
    parser.add_argument(
        "--model",
        default="deepseek-reasoner",
        help="OpenCode model ID (default: deepseek-reasoner)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()
    
    # Handle --no-reply flag
    if args.no_reply:
        args.reply = False

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
