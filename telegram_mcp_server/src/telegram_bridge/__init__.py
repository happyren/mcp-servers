"""
Telegram-OpenCode Bridge Service

Watches the Telegram message queue and forwards messages to OpenCode's HTTP API.
"""

from .bridge_service import main

__all__ = ["main"]
