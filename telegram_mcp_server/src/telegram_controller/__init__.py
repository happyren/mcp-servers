"""Telegram Controller - Daemon that manages OpenCode instances via Telegram."""

from .controller import TelegramController, main
from .instance import OpenCodeInstance, InstanceState
from .process_manager import ProcessManager
from .session_router import SessionRouter
from .notifications import NotificationManager
from .project_detector import detect_project_name
from .pid_manager import PIDManager
from .port_allocator import PortAllocator

__all__ = [
    "TelegramController",
    "OpenCodeInstance",
    "InstanceState",
    "ProcessManager",
    "SessionRouter",
    "NotificationManager",
    "PIDManager",
    "PortAllocator",
    "detect_project_name",
    "main",
]
