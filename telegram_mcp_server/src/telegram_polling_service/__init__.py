"""Telegram Polling Service.

Background service that polls Telegram for messages.
"""

from .polling_service import main

__version__ = "0.1.0"
__all__ = ["main"]
