#!/usr/bin/env python3
"""
Telegram Polling Service
Continuously polls Telegram for new messages and stores them in a JSON file.
Uses proper offset tracking to only fetch new updates since last poll.
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

        queue_dir = Path(self.settings.queue_dir).expanduser()
        self.data_dir = queue_dir
        self.queue_file = queue_dir / "message_queue.json"
        self.offset_file = queue_dir / "polling_offset.json"
        self.running = False

        self.data_dir.mkdir(parents=True, exist_ok=True)

        if not self.queue_file.exists():
            self._write_queue([])

        self.last_offset = self._load_offset()

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

    def _load_offset(self) -> int:
        """Load last offset from file for efficient polling."""
        if not self.offset_file.exists():
            return 0
        try:
            with open(self.offset_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                offset = data.get("offset", 0)
                polling_logger.info(f"Resuming from offset {offset}")
                return offset
        except (json.JSONDecodeError, FileNotFoundError):
            return 0

    def _save_offset(self, offset: int) -> None:
        """Save offset to file for persistence across restarts."""
        data = {"offset": offset, "updated_at": datetime.now().isoformat()}
        with open(self.offset_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    async def poll_once(self) -> None:
        """Poll Telegram once for new messages using proper offset."""
        try:
            updates = await self.client.get_updates_with_callbacks(
                offset=self.last_offset,
                limit=100,
                timeout=30,
            )

            if not updates:
                polling_logger.debug("No new updates")
                return

            polling_logger.info(f"Received {len(updates)} new update(s)")

            queue = self._read_queue()
            new_offset = self.last_offset

            for update in updates:
                update_id = update.get("update_id", 0)
                
                if update_id >= new_offset:
                    new_offset = update_id + 1

                msg_data = update.get("message")
                if msg_data:
                    msg_id = msg_data.get("message_id", 0)
                    from_user = msg_data.get("from", {})
                    msg_dict = {
                        "message_id": msg_id,
                        "chat_id": msg_data.get("chat", {}).get("id", 0),
                        "from_user_id": from_user.get("id"),
                        "from_username": from_user.get("username"),
                        "text": msg_data.get("text"),
                        "date": msg_data.get("date", 0),
                        "received_at": datetime.now().isoformat(),
                        "type": "message",
                        "raw": msg_data,
                    }
                    queue.append(msg_dict)
                    polling_logger.info(
                        f"Queued message {msg_id} from {from_user.get('username')}: "
                        f"{msg_dict['text'][:50] if msg_dict['text'] else 'No text'}"
                    )

                callback_data = update.get("callback_query")
                if callback_data:
                    callback_id = callback_data.get("id", "")
                    from_user = callback_data.get("from", {})
                    message = callback_data.get("message", {})
                    callback_dict = {
                        "message_id": update_id,
                        "chat_id": message.get("chat", {}).get("id", 0),
                        "from_user_id": from_user.get("id"),
                        "from_username": from_user.get("username"),
                        "text": None,
                        "callback_data": callback_data.get("data", ""),
                        "callback_query_id": callback_id,
                        "original_message_id": message.get("message_id"),
                        "date": message.get("date", 0),
                        "received_at": datetime.now().isoformat(),
                        "type": "callback_query",
                        "raw": callback_data,
                    }
                    queue.append(callback_dict)
                    polling_logger.info(
                        f"Queued callback from {from_user.get('username')}: "
                        f"{callback_data.get('data', 'No data')[:50]}"
                    )

            self._write_queue(queue)

            if new_offset != self.last_offset:
                self._save_offset(new_offset)
                self.last_offset = new_offset
                polling_logger.debug(f"Updated offset to {new_offset}")

        except Exception as e:
            polling_logger.error(f"Error polling Telegram: {e}")

    async def run(self, poll_interval: int = 2) -> None:
        """Run polling service continuously."""
        self.running = True

        def signal_handler(signum, frame):
            polling_logger.info("Received shutdown signal")
            self.running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        polling_logger.info(f"Starting Telegram polling service (interval: {poll_interval}s)")
        polling_logger.info(f"Queue file: {self.queue_file}")
        polling_logger.info(f"Data directory: {self.data_dir}")
        polling_logger.info(f"Last offset: {self.last_offset}")

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
            polling_logger.info(f"Final offset saved: {self.last_offset}")


async def async_main():
    """Async entry point."""
    service = TelegramPollingService()
    await service.run(poll_interval=2)


def main():
    """Sync entry point for console script."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
