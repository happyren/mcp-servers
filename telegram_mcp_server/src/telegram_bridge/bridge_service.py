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


class OpenCodeClient:
    """Client for OpenCode's HTTP API."""

    def __init__(self, base_url: str = "http://localhost:4096"):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=60.0)

    async def close(self):
        await self.client.aclose()

    async def health_check(self) -> bool:
        """Check if OpenCode server is running."""
        try:
            response = await self.client.get(f"{self.base_url}/session")
            return response.status_code == 200
        except httpx.ConnectError:
            return False

    async def list_sessions(self) -> list[dict[str, Any]]:
        """List all available sessions."""
        response = await self.client.get(f"{self.base_url}/session")
        response.raise_for_status()
        return response.json()

    async def create_session(self) -> dict[str, Any]:
        """Create a new session."""
        response = await self.client.post(f"{self.base_url}/session")
        response.raise_for_status()
        return response.json()

    async def get_existing_session(self) -> str | None:
        """Get an existing session ID without creating a new one.
        
        Returns the first available main session (not subagent) or None if no sessions exist.
        """
        try:
            sessions = await self.list_sessions()
            logger.debug(f"Sessions response: {sessions}")
            if sessions and isinstance(sessions, list) and len(sessions) > 0:
                # Filter to sessions without parentID (main sessions, not subagents)
                main_sessions = [s for s in sessions if not s.get("parentID")]
                if main_sessions:
                    session_id = main_sessions[0]["id"]
                    logger.info(f"Found existing session: {session_id}")
                    return session_id
                # Fall back to first session if all are subagents
                session_id = sessions[0]["id"]
                logger.info(f"Found existing session (subagent): {session_id}")
                return session_id
            return None
        except Exception as e:
            logger.error(f"Failed to get existing session: {e}")
            return None

    async def get_or_create_session(self) -> str | None:
        """Get existing session ID or create a new one."""
        try:
            session_id = await self.get_existing_session()
            if session_id:
                return session_id
            # Create new session
            logger.info("No sessions found, creating new one")
            session = await self.create_session()
            return session["id"]
        except Exception as e:
            logger.error(f"Failed to get/create session: {e}")
            return None

    async def get_session_status(self) -> dict[str, Any]:
        """Get status of all sessions."""
        response = await self.client.get(f"{self.base_url}/session/status")
        response.raise_for_status()
        return response.json()

    async def is_session_idle(self, session_id: str) -> bool:
        """Check if a session is idle (not busy)."""
        try:
            status = await self.get_session_status()
            session_status = status.get(session_id, {})
            return session_status.get("type") != "busy"
        except Exception:
            return False

    async def get_or_create_telegram_session(self) -> str | None:
        """Get or create a dedicated session for Telegram messages."""
        try:
            sessions = await self.list_sessions()
            status = await self.get_session_status()
            
            # Look for an idle main session
            for session in sessions:
                if session.get("parentID"):
                    continue  # Skip subagent sessions
                session_id = session["id"]
                session_status = status.get(session_id, {})
                if session_status.get("type") != "busy":
                    logger.info(f"Found idle session: {session_id}")
                    return session_id
            
            # All sessions are busy, create a new one
            logger.info("All sessions busy, creating new session for Telegram")
            session = await self.create_session()
            return session["id"]
        except Exception as e:
            logger.error(f"Failed to get/create telegram session: {e}")
            return None

    async def send_prompt_async(self, session_id: str, prompt: str) -> None:
        """Send a prompt to a session asynchronously (non-blocking)."""
        response = await self.client.post(
            f"{self.base_url}/session/{session_id}/prompt_async",
            json={"parts": [{"type": "text", "text": prompt}]},
        )
        response.raise_for_status()
        # Note: Returns 204 No Content on success

    async def send_message(
        self, 
        session_id: str, 
        message: str,
        provider_id: str = "github-copilot",
        model_id: str = "claude-opus-4.5",
    ) -> str:
        """Send a message and wait for response (blocking). Returns response text."""
        response = await self.client.post(
            f"{self.base_url}/session/{session_id}/message",
            json={
                "parts": [{"type": "text", "text": message}],
                "model": {"providerID": provider_id, "modelID": model_id},
            },
            timeout=300.0,  # 5 minute timeout for long responses
        )
        response.raise_for_status()
        # May return empty on 204
        if response.status_code == 204:
            return ""
        
        data = response.json()
        
        # Check for errors
        info = data.get("info", {})
        if info.get("error"):
            error_msg = info["error"].get("data", {}).get("message", "Unknown error")
            logger.error(f"OpenCode error: {error_msg}")
            return f"Error: {error_msg[:200]}"
        
        # Extract text from response parts
        # Response format: { info: Message, parts: Part[] }
        parts = data.get("parts", [])
        text_parts = []
        for part in parts:
            if part.get("type") == "text":
                text_parts.append(part.get("text", ""))
        
        return "\n".join(text_parts) if text_parts else ""


class TelegramOpenCodeBridge:
    """Bridge service that watches Telegram queue and sends to OpenCode."""

    def __init__(
        self,
        opencode_url: str = "http://localhost:4096",
        queue_dir: str | None = None,
        poll_interval: int = 2,
        reply_to_telegram: bool = True,
        bot_token: str | None = None,
        provider_id: str = "github-copilot",
        model_id: str = "claude-opus-4.5",
    ):
        self.opencode = OpenCodeClient(opencode_url)
        self.poll_interval = poll_interval
        self.reply_to_telegram = reply_to_telegram
        self.running = False
        self.session_id: str | None = None
        self.provider_id = provider_id
        self.model_id = model_id

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
        self.forwarded_ids: set[int] = self._load_forwarded_ids()

    def _load_forwarded_ids(self) -> set[int]:
        """Load IDs of messages we've already forwarded."""
        if not self.bridge_state_file.exists():
            return set()
        try:
            with open(self.bridge_state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("forwarded_ids", []))
        except (json.JSONDecodeError, FileNotFoundError):
            return set()

    def _save_forwarded_ids(self) -> None:
        """Save forwarded message IDs."""
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "forwarded_ids": list(self.forwarded_ids),
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

    def _parse_model_request(self, text: str) -> tuple[str | None, str | None, bool, str]:
        """Parse model request from message text.
        
        Returns: (provider_id, model_id, create_new_session, cleaned_text)
        
        Examples:
        - "use glm-4.7" -> (None, "glm-4.7", False, "")
        - "use zhipuai-coding-plan/glm-4.7" -> ("zhipuai-coding-plan", "glm-4.7", False, "")
        - "with new session use kimi-k2.5" -> (None, "kimi-k2.5", True, "")
        - "new session: some question" -> (None, None, True, "some question")
        """
        import re
        
        original_text = text
        create_new_session = False
        provider_id = None
        model_id = None
        
        # Check for new session request
        new_session_patterns = [
            r'\bnew\s+session\b',
            r'\bcreate\s+(?:a\s+)?new\s+session\b',
        ]
        for pattern in new_session_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                create_new_session = True
                # Remove the new session request from text
                text = re.sub(pattern, '', text, flags=re.IGNORECASE)
        
        # Parse model request patterns
        # Pattern: "use <model>" or "with <model>" or "using <model>"
        # Model can be "model-id" or "provider/model-id"
        model_patterns = [
            r'\buse\s+(?:model\s+)?([\w\-]+(?:/[\w\-\.]+)?)\b',
            r'\bwith\s+(?:model\s+)?([\w\-]+(?:/[\w\-\.]+)?)\b',
            r'\busing\s+(?:model\s+)?([\w\-]+(?:/[\w\-\.]+)?)\b',
            r'\bswitch\s+to\s+([\w\-]+(?:/[\w\-\.]+)?)\b',
        ]
        
        for pattern in model_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                model_spec = match.group(1)
                # Check if it includes provider
                if '/' in model_spec:
                    parts = model_spec.split('/')
                    provider_id = parts[0]
                    model_id = parts[1]
                else:
                    model_id = model_spec
                # Remove the model request from text
                text = re.sub(pattern, '', text, flags=re.IGNORECASE)
                break
        
        # Clean up the text (remove extra whitespace)
        cleaned_text = ' '.join(text.split())
        
        # If no specific content remains, use original text without the commands
        if not cleaned_text:
            # Remove command phrases from original
            cleaned_text = original_text
            for pattern in new_session_patterns + model_patterns:
                cleaned_text = re.sub(pattern, '', cleaned_text, flags=re.IGNORECASE)
            cleaned_text = ' '.join(cleaned_text.split())
        
        return provider_id, model_id, create_new_session, cleaned_text

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

            if msg_id is None:
                logger.warning("Message has no message_id, skipping")
                continue

            if not text:
                logger.debug(f"Skipping message {msg_id} (no text)")
                self.forwarded_ids.add(msg_id)
                processed_ids.add(msg_id)
                continue

            # Parse model request and session creation from text
            requested_provider, requested_model, create_new_session, cleaned_text = self._parse_model_request(text)
            
            # Use requested model or fall back to default
            provider_id = requested_provider or self.provider_id
            model_id = requested_model or self.model_id
            
            # Log model change if different from default
            if requested_model:
                logger.info(f"Using requested model: {provider_id}/{model_id}")

            # Handle session management
            if create_new_session:
                logger.info("Creating new session as requested")
                new_session = await self.opencode.create_session()
                if new_session:
                    self.session_id = new_session["id"]
                    logger.info(f"Created new session: {self.session_id}")
                else:
                    logger.error("Failed to create new session")
                    continue
            elif not self.session_id:
                # Get or reuse existing session (don't create unless necessary)
                logger.debug("No current session, looking for existing session to reuse")
                session_id = await self.opencode.get_existing_session()
                if session_id:
                    self.session_id = session_id
                    logger.info(f"Reusing existing session: {self.session_id}")
                else:
                    # Only create if absolutely no sessions exist
                    logger.info("No existing sessions found, creating one")
                    new_session = await self.opencode.create_session()
                    if new_session:
                        self.session_id = new_session["id"]
                        logger.info(f"Created session: {self.session_id}")
                    else:
                        logger.error("Failed to create session")
                        continue
            else:
                logger.debug(f"Using current session: {self.session_id}")

            session_id = self.session_id
            if not session_id:
                logger.error("No session available")
                continue

            # Format the prompt with context
            prompt = f"[Telegram from @{username}]: {cleaned_text if cleaned_text else text}"

            # Get chat_id for reply
            chat_id = msg.get("chat_id")

            try:
                if self.reply_to_telegram and self.telegram and chat_id:
                    # Use blocking mode - wait for response and send back to Telegram
                    display_text = cleaned_text if cleaned_text else text
                    logger.info(f"Sending to OpenCode: {display_text[:50]}...")
                    response_text = await self.opencode.send_message(
                        session_id, 
                        prompt,
                        provider_id=provider_id,
                        model_id=model_id,
                    )
                    logger.info(f"OpenCode response received for message {msg_id}")
                    
                    if response_text:
                        # Send response back to Telegram
                        logger.info(f"Sending reply to Telegram: {response_text[:50]}...")
                        await self.telegram.send_message(chat_id, response_text)
                        logger.info(f"Reply sent to Telegram for message {msg_id}")
                    else:
                        logger.warning(f"Empty response from OpenCode for message {msg_id}")
                else:
                    # Send asynchronously (no reply)
                    display_text = cleaned_text if cleaned_text else text
                    logger.info(f"Sending to OpenCode (async): {display_text[:50]}...")
                    await self.opencode.send_prompt_async(session_id, prompt)
                    logger.info(f"Message {msg_id} queued in OpenCode")

                self.forwarded_ids.add(msg_id)
                processed_ids.add(msg_id)

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error sending message {msg_id}: {e.response.status_code}")
                # If session is invalid, clear it so we get a new one
                if e.response.status_code in (404, 400):
                    self.session_id = None
            except Exception as e:
                logger.error(f"Error sending message {msg_id} to OpenCode: {e}")

        # Save state and clean up queue
        if processed_ids:
            self._save_forwarded_ids()
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
        logger.info(f"Provider: {self.provider_id}, Model: {self.model_id}")

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
            self._save_forwarded_ids()


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
        default="github-copilot",
        help="OpenCode provider ID (default: github-copilot)",
    )
    parser.add_argument(
        "--model",
        default="claude-opus-4.5",
        help="OpenCode model ID (default: claude-opus-4.5)",
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
