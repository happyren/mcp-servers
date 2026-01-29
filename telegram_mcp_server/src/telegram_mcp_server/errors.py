"""Error handling utilities for Telegram MCP Server."""

import logging
import os
from enum import Enum
from typing import Any, Optional, Union

from pythonjsonlogger import jsonlogger


class ErrorCategory(str, Enum):
    """Categories for error codes."""

    CHAT = "CHAT"
    MSG = "MSG"
    CONTACT = "CONTACT"
    GROUP = "GROUP"
    MEDIA = "MEDIA"
    PROFILE = "PROFILE"
    AUTH = "AUTH"
    ADMIN = "ADMIN"
    POLL = "POLL"
    GENERAL = "GEN"


def setup_logger(name: str = "telegram_mcp") -> logging.Logger:
    """Set up and configure the logger with JSON file logging.

    Args:
        name: Logger name

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    
    # Avoid adding handlers multiple times
    if logger.handlers:
        return logger
    
    logger.setLevel(logging.DEBUG)

    # Console handler with standard format
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # File handler with JSON format for structured error logging
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_file_path = os.path.join(script_dir, "..", "..", "mcp_errors.log")
    
    try:
        file_handler = logging.FileHandler(log_file_path, mode="a")
        file_handler.setLevel(logging.ERROR)
        json_formatter = jsonlogger.JsonFormatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
        file_handler.setFormatter(json_formatter)
        logger.addHandler(file_handler)
    except (OSError, PermissionError):
        # If we can't write to the log file, just use console
        pass

    return logger


# Global logger instance
logger = setup_logger()


def log_and_format_error(
    function_name: str,
    error: Exception,
    category: Optional[Union[ErrorCategory, str]] = None,
    user_message: Optional[str] = None,
    **context: Any,
) -> str:
    """Centralized error handling function.

    Logs the error with full context and returns a user-friendly message.

    Args:
        function_name: Name of the function where error occurred
        error: The exception that was raised
        category: Error category for the error code
        user_message: Optional custom user-facing message
        **context: Additional context to log (e.g., chat_id=123)

    Returns:
        User-friendly error message with error code
    """
    # Generate consistent error code
    if category is None:
        prefix_str = ErrorCategory.GENERAL.value
    elif isinstance(category, ErrorCategory):
        prefix_str = category.value
    else:
        prefix_str = str(category)

    error_code = f"{prefix_str}-ERR-{abs(hash(function_name)) % 1000:03d}"

    # Format context parameters
    context_str = ", ".join(f"{k}={v}" for k, v in context.items())

    # Log full technical error
    log_message = f"Error in {function_name}"
    if context_str:
        log_message += f" ({context_str})"
    log_message += f" - Code: {error_code}"

    logger.error(log_message, exc_info=True)

    # Return user-friendly message
    if user_message:
        return f"{user_message} (code: {error_code})"

    return f"An error occurred (code: {error_code}). Check logs for details."


def format_telegram_error(error: Exception) -> str:
    """Format a Telegram API error into a user-friendly message.

    Args:
        error: The exception from Telegram API

    Returns:
        User-friendly error message
    """
    error_str = str(error).lower()

    # Common Telegram API errors
    if "chat not found" in error_str:
        return "Chat not found. Please verify the chat ID."
    elif "bot was blocked" in error_str:
        return "Bot was blocked by the user."
    elif "not enough rights" in error_str or "permission" in error_str:
        return "Bot doesn't have permission to perform this action."
    elif "message not found" in error_str:
        return "Message not found. It may have been deleted."
    elif "too many requests" in error_str:
        return "Rate limited by Telegram. Please try again later."
    elif "unauthorized" in error_str:
        return "Bot token is invalid or expired."
    elif "bad request" in error_str:
        return f"Invalid request: {error}"

    return str(error)
