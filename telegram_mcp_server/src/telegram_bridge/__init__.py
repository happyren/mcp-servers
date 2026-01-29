"""
Telegram-OpenCode Bridge Service

Watches the Telegram message queue and forwards messages to OpenCode's HTTP API.
"""

from telegram_bridge.bridge_service import main

__all__ = ["main"]
