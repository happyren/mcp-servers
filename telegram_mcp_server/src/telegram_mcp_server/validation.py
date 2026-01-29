"""Input validation utilities for Telegram MCP Server."""

import re
from functools import wraps
from typing import Any, Callable, List, Optional, Tuple, Union

from .errors import ErrorCategory, log_and_format_error


class ValidationError(Exception):
    """Raised when input validation fails."""

    pass


def validate_single_id(
    value: Any, param_name: str
) -> Tuple[Union[int, str, None], Optional[str]]:
    """Validate a single chat_id or user_id value.

    Supports:
    - Integer IDs (positive or negative for groups/channels)
    - String representations of integer IDs
    - Usernames (with or without @ prefix)

    Args:
        value: The value to validate
        param_name: Name of the parameter (for error messages)

    Returns:
        Tuple of (validated_value, error_message)
        If validation succeeds, error_message is None
    """
    # Handle integer IDs
    if isinstance(value, int):
        # Telegram IDs should be within int64 range
        if not (-(2**63) <= value <= 2**63 - 1):
            return None, f"Invalid {param_name}: ID out of valid range"
        return value, None

    # Handle string values
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None, f"Invalid {param_name}: Empty string"

        # Try to parse as integer
        try:
            int_value = int(value)
            if not (-(2**63) <= int_value <= 2**63 - 1):
                return None, f"Invalid {param_name}: ID out of valid range"
            return int_value, None
        except ValueError:
            pass

        # Check if it's a valid username (5+ chars, alphanumeric + underscore)
        username = value.lstrip("@")
        if re.match(r"^[a-zA-Z][a-zA-Z0-9_]{4,31}$", username):
            return f"@{username}" if not value.startswith("@") else value, None

        return None, f"Invalid {param_name}: Must be an integer ID or valid username"

    return None, f"Invalid {param_name}: Type must be int or string, got {type(value).__name__}"


def validate_id(*param_names: str) -> Callable:
    """Decorator to validate chat_id and user_id parameters.

    Supports validation of single IDs or lists of IDs.
    Accepts integer IDs, string representations, or usernames.

    Args:
        *param_names: Names of parameters to validate

    Returns:
        Decorated function with validated parameters

    Example:
        @validate_id("chat_id")
        async def send_message(chat_id: Union[int, str], message: str) -> str:
            ...

        @validate_id("chat_id", "user_ids")
        async def invite_users(chat_id: int, user_ids: List[int]) -> str:
            ...
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            for param_name in param_names:
                if param_name not in kwargs or kwargs[param_name] is None:
                    continue

                param_value = kwargs[param_name]

                # Handle lists of IDs
                if isinstance(param_value, list):
                    validated_list: List[Union[int, str]] = []
                    for i, item in enumerate(param_value):
                        validated_item, error_msg = validate_single_id(
                            item, f"{param_name}[{i}]"
                        )
                        if error_msg:
                            return log_and_format_error(
                                func.__name__,
                                ValidationError(error_msg),
                                category=ErrorCategory.GENERAL,
                                user_message=error_msg,
                            )
                        if validated_item is not None:
                            validated_list.append(validated_item)
                    kwargs[param_name] = validated_list
                else:
                    # Single value validation
                    validated_value, error_msg = validate_single_id(
                        param_value, param_name
                    )
                    if error_msg:
                        return log_and_format_error(
                            func.__name__,
                            ValidationError(error_msg),
                            category=ErrorCategory.GENERAL,
                            user_message=error_msg,
                        )
                    kwargs[param_name] = validated_value

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def validate_message_text(text: str, max_length: int = 4096) -> Tuple[str, Optional[str]]:
    """Validate message text.

    Args:
        text: Message text to validate
        max_length: Maximum allowed length (Telegram default is 4096)

    Returns:
        Tuple of (validated_text, error_message)
    """
    if not text:
        return "", "Message text cannot be empty"

    if len(text) > max_length:
        return "", f"Message text exceeds maximum length of {max_length} characters"

    return text, None


def validate_parse_mode(parse_mode: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Validate parse mode parameter.

    Args:
        parse_mode: Parse mode to validate

    Returns:
        Tuple of (validated_parse_mode, error_message)
    """
    valid_modes = {"Markdown", "MarkdownV2", "HTML", None, "None"}

    if parse_mode == "None":
        return None, None

    if parse_mode not in valid_modes:
        return None, f"Invalid parse_mode: {parse_mode}. Must be one of: Markdown, MarkdownV2, HTML, or None"

    return parse_mode, None
