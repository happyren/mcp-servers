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

# Add parent directory to path to import telegram_mcp_server modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram_mcp_server.config import get_settings
from telegram_mcp_server.telegram_client import TelegramClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Default paths
DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "telegram_mcp_server"
DEFAULT_QUEUE_FILE = DEFAULT_DATA_DIR / "message_queue.json"
DEFAULT_PROCESSED_FILE = DEFAULT_DATA_DIR / "processed_messages.json"

class TelegramPollingService:
    """Service that continuously polls Telegram for new messages."""
    
    def __init__(self):
        self.settings = get_settings()
        self.client = TelegramClient(
            bot_token=self.settings.bot_token,
            base_url=self.settings.api_base_url,
        )
        self.data_dir = DEFAULT_DATA_DIR
        self.queue_file = DEFAULT_QUEUE_FILE
        self.processed_file = DEFAULT_PROCESSED_FILE
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
        with open(self.queue_file, 'w', encoding='utf-8') as f:
            json.dump(messages, f, indent=2, ensure_ascii=False)
    
    def _read_queue(self) -> list[dict[str, Any]]:
        """Read messages from queue file."""
        if not self.queue_file.exists():
            return []
        try:
            with open(self.queue_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []
    
    def _load_processed_ids(self) -> set[int]:
        """Load processed message IDs from file."""
        if not self.processed_file.exists():
            return set()
        try:
            with open(self.processed_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return set(data.get("processed_ids", []))
        except (json.JSONDecodeError, FileNotFoundError):
            return set()
    
    def _save_processed_ids(self) -> None:
        """Save processed message IDs to file."""
        data = {"processed_ids": list(self.processed_ids)}
        with open(self.processed_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    
    async def poll_once(self) -> None:
        """Poll Telegram once for new messages."""
        try:
            messages = await self.client.get_new_messages(timeout=30)
            
            if not messages:
                logger.debug("No new messages")
                return
            
            logger.info(f"Received {len(messages)} new message(s)")
            
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
                    logger.info(f"Queued message {msg.message_id} from {msg.from_username}: {msg.text[:50] if msg.text else 'No text'}")
            
            # Write updated queue
            self._write_queue(queue)
            
            # Save processed IDs periodically
            self._save_processed_ids()
            
        except Exception as e:
            logger.error(f"Error polling Telegram: {e}")
    
    async def run(self, poll_interval: int = 10) -> None:
        """Run the polling service continuously."""
        self.running = True
        
        # Handle graceful shutdown
        def signal_handler(signum, frame):
            logger.info("Received shutdown signal")
            self.running = False
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        logger.info(f"Starting Telegram polling service (interval: {poll_interval}s)")
        logger.info(f"Queue file: {self.queue_file}")
        logger.info(f"Bot token: {self.settings.bot_token[:10]}...")
        logger.info(f"Chat ID: {self.settings.chat_id}")
        
        try:
            while self.running:
                await self.poll_once()
                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            logger.info("Polling cancelled")
        finally:
            logger.info("Shutting down polling service")
            await self.client.close()
            self._save_processed_ids()


async def main():
    """Main entry point."""
    service = TelegramPollingService()
    await service.run(poll_interval=10)


if __name__ == "__main__":
    asyncio.run(main())