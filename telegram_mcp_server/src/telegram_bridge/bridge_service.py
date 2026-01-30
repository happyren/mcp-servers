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
        data = {
            "forwarded_ids": list(self.forwarded_ids),
            "session_id": self.session_id,
            "session_model": session_model_list,
            "sessions": sessions_serializable,
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

            # Not a command, process as regular prompt
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
                    # Use blocking mode - wait for response and send back to Telegram
                    logger.info(f"Sending to OpenCode: {text[:50]}...")
                    response_text = await self.opencode.send_message(
                        session_id,
                        prompt,
                        provider_id=provider_id,
                        model_id=model_id,
                    )
                    logger.info(f"OpenCode response received for message {msg_id}")

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
        if not await self.opencode.health_check():
            logger.error(f"Cannot connect to OpenCode at {self.opencode.base_url}")
            logger.error("Make sure OpenCode is running with: opencode --port 4096")
            return

        logger.info("Connected to OpenCode server")

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
