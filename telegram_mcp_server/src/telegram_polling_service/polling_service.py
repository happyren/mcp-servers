#!/usr/bin/env python3
"""
Telegram Polling Service
Continuously polls Telegram for new messages and stores them in a JSON file.
Run as a systemd service to ensure messages are captured even when OpenCode isn't running.
"""

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from telegram_mcp_server.config import get_settings
from telegram_mcp_server.telegram_client import TelegramClient
from telegram_mcp_server.commands import get_bot_commands
from telegram_mcp_server.errors import logger

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
polling_logger = logging.getLogger("telegram_polling_service")


class TelegramPollingService:
    """Service that continuously polls Telegram for new messages."""

    def __init__(self):
        self.settings = get_settings()
        self.client = TelegramClient(
            bot_token=self.settings.bot_token,
            base_url=self.settings.api_base_url,
        )

        # Use configurable queue directory
        queue_dir = Path(self.settings.queue_dir).expanduser()
        self.data_dir = queue_dir
        self.queue_file = queue_dir / "message_queue.json"
        self.processed_file = queue_dir / "processed_messages.json"
        self.running = False

        # Ensure data directory exists
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Initialize queue file if it doesn't exist
        if not self.queue_file.exists():
            self._write_queue([])

        # Load processed message IDs to avoid duplicates
        self.processed_ids = self._load_processed_ids()

    def _write_queue(self, messages: list[dict[str, Any]]) -> None:
        """Write messages to queue file."""
        with open(self.queue_file, "w", encoding="utf-8") as f:
            json.dump(messages, f, indent=2, ensure_ascii=False)

    def _read_queue(self) -> list[dict[str, Any]]:
        """Read messages from queue file."""
        if not self.queue_file.exists():
            return []
        try:
            with open(self.queue_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _load_processed_ids(self) -> set[int]:
        """Load processed message IDs from file."""
        if not self.processed_file.exists():
            return set()
        try:
            with open(self.processed_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("processed_ids", []))
        except (json.JSONDecodeError, FileNotFoundError):
            return set()

    def _save_processed_ids(self) -> None:
        """Save processed message IDs to file."""
        data = {"processed_ids": list(self.processed_ids)}
        with open(self.processed_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    async def poll_once(self) -> None:
        """Poll Telegram once for new messages."""
        try:
            messages = await self.client.get_new_messages(timeout=30)

            if not messages:
                polling_logger.debug("No new messages")
                return

            polling_logger.info(f"Received {len(messages)} new message(s)")

            # Read existing queue
            queue = self._read_queue()

            # Add new messages to queue if not already processed
            for msg in messages:
                if msg.message_id not in self.processed_ids:
                    msg_dict = {
                        "message_id": msg.message_id,
                        "chat_id": msg.chat_id,
                        "from_user_id": msg.from_user_id,
                        "from_username": msg.from_username,
                        "text": msg.text,
                        "date": msg.date,
                        "received_at": datetime.now().isoformat(),
                        "raw": msg.raw,
                    }
                    queue.append(msg_dict)
                    self.processed_ids.add(msg.message_id)
                    polling_logger.info(
                        f"Queued message {msg.message_id} from {msg.from_username}: "
                        f"{msg.text[:50] if msg.text else 'No text'}"
                    )

            # Write updated queue
            self._write_queue(queue)

            # Save processed IDs periodically
            self._save_processed_ids()

        except Exception as e:
            polling_logger.error(f"Error polling Telegram: {e}")

    async def run(self, poll_interval: int = 10) -> None:
        """Run polling service continuously."""
        self.running = True

        # Handle graceful shutdown
        def signal_handler(signum, frame):
            polling_logger.info("Received shutdown signal")
            self.running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        polling_logger.info(f"Starting Telegram polling service (interval: {poll_interval}s)")
        polling_logger.info(f"Queue file: {self.queue_file}")
        polling_logger.info(f"Data directory: {self.data_dir}")

        # Ensure bot commands are set
        try:
            commands = get_bot_commands()
            await self.client.ensure_commands_set(commands)
            polling_logger.info(f"Ensured bot commands are set ({len(commands)} commands)")
        except Exception as e:
            polling_logger.warning(f"Failed to set bot commands: {e}")

        try:
            while self.running:
                await self.poll_once()
                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            polling_logger.info("Polling cancelled")
        finally:
            polling_logger.info("Shutting down polling service")
            await self.client.close()
            self._save_processed_ids()


async def async_main():
    """Async entry point."""
    service = TelegramPollingService()
    await service.run(poll_interval=10)


def main():
    """Sync entry point for console script."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
