"""MCP Server for Telegram Bot API integration using FastMCP."""

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .config import get_settings, Settings
from .errors import ErrorCategory, format_telegram_error, log_and_format_error
from .telegram_client import TelegramClient
from .validation import (
    validate_id,
    validate_message_text,
    validate_parse_mode,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global state
_telegram_client: TelegramClient | None = None
_settings: Settings | None = None


def get_queue_file_path() -> Path:
    """Get queue file path from settings.
    
    This returns the inbox file path where the polling service stores messages.
    """
    settings = get_settings()
    queue_dir = Path(settings.queue_dir).expanduser()
    return queue_dir / "message_inbox.json"


# Don't initialize at module import time - will be initialized in main()
QUEUE_FILE_PATH: Path | None = None


def _ensure_queue_path_initialized() -> Path:
    """Ensure QUEUE_FILE_PATH is initialized (lazy initialization)."""
    global QUEUE_FILE_PATH
    if QUEUE_FILE_PATH is None:
        try:
            QUEUE_FILE_PATH = get_queue_file_path()
        except Exception as e:
            logger.warning(f"Failed to initialize queue path: {e}. Using default.")
            QUEUE_FILE_PATH = Path.home() / ".local" / "share" / "telegram_mcp_server" / "message_inbox.json"
    return QUEUE_FILE_PATH


def get_client() -> TelegramClient:
    """Get the Telegram client instance."""
    global _telegram_client, _settings
    if _telegram_client is None:
        _settings = get_settings()
        _telegram_client = TelegramClient(
            bot_token=_settings.bot_token,
            base_url=_settings.api_base_url,
        )
    return _telegram_client


def get_default_chat_id() -> str:
    """Get the default chat ID from settings."""
    global _settings
    if _settings is None:
        _settings = get_settings()
    return _settings.chat_id


def get_queued_messages(clear_after_read: bool = False) -> list[dict[str, Any]]:
    """Get messages from polling service queue file.

    Args:
        clear_after_read: If True, clear the queue file after reading.

    Returns:
        List of queued messages as dictionaries.
    """
    queue_path = _ensure_queue_path_initialized()

    if not queue_path.exists():
        return []

    try:
        with open(queue_path, "r", encoding="utf-8") as f:
            messages = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        messages = []

    if clear_after_read and messages:
        try:
            queue_path.unlink()
        except OSError:
            with open(queue_path, "w", encoding="utf-8") as f:
                f.write("[]")

    return messages


# Create the MCP server with FastMCP
mcp = FastMCP("telegram-mcp-server")


@mcp.tool(
    annotations=ToolAnnotations(
        title="Send Message", destructiveHint=True, openWorldHint=True
    )
)
@validate_id("chat_id")
async def telegram_send_message(
    chat_id: str,
    message: str,
    parse_mode: str = "Markdown",
) -> str:
    """Send a message to a Telegram chat.

    Args:
        chat_id: The chat ID to send to (integer or username).
        message: The message text to send. Supports Markdown formatting.
        parse_mode: Format mode: Markdown, HTML, or None. Defaults to Markdown.

    Returns:
        Success message with message ID and chat ID.
    """
    try:
        validated_text, text_error = validate_message_text(message)
        if text_error:
            return text_error

        validated_parse_mode, parse_error = validate_parse_mode(parse_mode)
        if parse_error:
            return parse_error

        client = get_client()
        result = await client.send_message(
            chat_id=chat_id,
            text=validated_text,
            parse_mode=validated_parse_mode,
        )

        return f"Message sent successfully!\nMessage ID: {result.get('message_id')}\nChat ID: {result.get('chat', {}).get('id')}"
    except Exception as e:
        return log_and_format_error(
            "telegram_send_message",
            e,
            category=ErrorCategory.MSG,
            user_message=format_telegram_error(e),
            chat_id=chat_id,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Send Summary", destructiveHint=True, openWorldHint=True)
)
@validate_id("chat_id")
async def telegram_send_summary(
    title: str,
    summary: str,
    status: str = "info",
    chat_id: str | None = None,
) -> str:
    """Send a work summary to Telegram with formatted header.

    Args:
        title: Title for the summary (e.g., 'Task Completed', 'Build Status').
        summary: The summary content. Can include bullet points and details.
        status: Status indicator: success, warning, error, or info. Defaults to info.
        chat_id: Optional chat ID. Uses default if not provided.

    Returns:
        Success message with message ID.
    """
    try:
        if chat_id is None:
            chat_id = get_default_chat_id()

        status_emoji = {
            "success": "✅",
            "warning": "⚠️",
            "error": "❌",
            "info": "ℹ️",
        }

        emoji = status_emoji.get(status, "ℹ️")
        formatted_message = f"{emoji} *{title}*\n\n{summary}"

        client = get_client()
        result = await client.send_message(
            chat_id=chat_id,
            text=formatted_message,
            parse_mode="Markdown",
        )

        return f"Summary sent successfully!\nMessage ID: {result.get('message_id')}"
    except Exception as e:
        return log_and_format_error(
            "telegram_send_summary",
            e,
            category=ErrorCategory.MSG,
            user_message=format_telegram_error(e),
            chat_id=chat_id or get_default_chat_id(),
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Receive Messages", readOnlyHint=True, openWorldHint=True)
)
async def telegram_receive_messages(
    timeout: int = 2,
    from_user_id: str | None = None,
) -> str:
    """Check for and receive new messages from Telegram.

    Args:
        timeout: Timeout in seconds for long polling. Defaults to 5 seconds.
        from_user_id: Optional user ID to filter messages from.

    Returns:
        JSON-formatted list of new messages or message indicating no new messages.
    """
    try:
        client = get_client()
        messages = await client.get_new_messages(timeout=timeout)

        if from_user_id:
            from_user_id_int = int(from_user_id)
            messages = [m for m in messages if m.from_user_id == from_user_id_int]

        if not messages:
            return "No new messages received."

        formatted = []
        for msg in messages:
            formatted.append(
                {
                    "message_id": msg.message_id,
                    "chat_id": msg.chat_id,
                    "from_user_id": msg.from_user_id,
                    "from_username": msg.from_username,
                    "text": msg.text,
                    "date": msg.date,
                }
            )

        return json.dumps(formatted, indent=2)
    except Exception as e:
        return log_and_format_error(
            "telegram_receive_messages",
            e,
            category=ErrorCategory.MSG,
            user_message=format_telegram_error(e),
            timeout=timeout,
            from_user_id=from_user_id,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Reply Message", destructiveHint=True, openWorldHint=True)
)
@validate_id("chat_id")
async def telegram_reply_message(
    chat_id: str,
    message_id: int,
    text: str,
) -> str:
    """Reply to a specific Telegram message.

    Args:
        chat_id: The chat ID where the original message was sent.
        message_id: The ID of the message to reply to.
        text: The reply text.

    Returns:
        Success message with message ID.
    """
    try:
        validated_text, text_error = validate_message_text(text)
        if text_error:
            return text_error

        client = get_client()
        result = await client.reply_to_message(
            chat_id=chat_id,
            message_id=message_id,
            text=validated_text,
        )

        return f"Reply sent successfully!\nMessage ID: {result.get('message_id')}"
    except Exception as e:
        return log_and_format_error(
            "telegram_reply_message",
            e,
            category=ErrorCategory.MSG,
            user_message=format_telegram_error(e),
            chat_id=chat_id,
            message_id=message_id,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Edit Message", destructiveHint=True, openWorldHint=True)
)
@validate_id("chat_id")
async def telegram_edit_message(
    chat_id: str,
    message_id: int,
    new_text: str,
) -> str:
    """Edit a previously sent message.

    Args:
        chat_id: The chat ID containing the message.
        message_id: The ID of the message to edit.
        new_text: The new message text.

    Returns:
        Success message with message ID.
    """
    try:
        validated_text, text_error = validate_message_text(new_text)
        if text_error:
            return text_error

        client = get_client()
        result = await client.edit_message(
            chat_id=chat_id,
            message_id=message_id,
            text=validated_text,
        )

        return f"Message edited successfully!\nMessage ID: {result.get('message_id')}"
    except Exception as e:
        return log_and_format_error(
            "telegram_edit_message",
            e,
            category=ErrorCategory.MSG,
            user_message=format_telegram_error(e),
            chat_id=chat_id,
            message_id=message_id,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Delete Message", destructiveHint=True, openWorldHint=True)
)
@validate_id("chat_id")
async def telegram_delete_message(
    chat_id: str,
    message_id: int,
) -> str:
    """Delete a message from a chat.

    Args:
        chat_id: The chat ID containing the message.
        message_id: The ID of the message to delete.

    Returns:
        Success message.
    """
    try:
        client = get_client()
        await client.delete_message(
            chat_id=chat_id,
            message_id=message_id,
        )

        return "Message deleted successfully!"
    except Exception as e:
        return log_and_format_error(
            "telegram_delete_message",
            e,
            category=ErrorCategory.MSG,
            user_message=format_telegram_error(e),
            chat_id=chat_id,
            message_id=message_id,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Forward Message", destructiveHint=True, openWorldHint=True)
)
@validate_id("chat_id", "from_chat_id")
async def telegram_forward_message(
    chat_id: str,
    from_chat_id: str,
    message_id: int,
) -> str:
    """Forward a message to another chat.

    Args:
        chat_id: Target chat ID to forward to.
        from_chat_id: Source chat ID containing the message.
        message_id: The ID of the message to forward.

    Returns:
        Success message with new message ID.
    """
    try:
        client = get_client()
        result = await client.forward_message(
            chat_id=chat_id,
            from_chat_id=from_chat_id,
            message_id=message_id,
        )

        return f"Message forwarded successfully!\nMessage ID: {result.get('message_id')}"
    except Exception as e:
        return log_and_format_error(
            "telegram_forward_message",
            e,
            category=ErrorCategory.MSG,
            user_message=format_telegram_error(e),
            chat_id=chat_id,
            from_chat_id=from_chat_id,
            message_id=message_id,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Pin Message", destructiveHint=True, openWorldHint=True)
)
@validate_id("chat_id")
async def telegram_pin_message(
    chat_id: str,
    message_id: int,
    disable_notification: bool = False,
) -> str:
    """Pin a message in a chat.

    Args:
        chat_id: The chat ID.
        message_id: The ID of the message to pin.
        disable_notification: Send silently without notification. Defaults to False.

    Returns:
        Success message.
    """
    try:
        client = get_client()
        await client.pin_message(
            chat_id=chat_id,
            message_id=message_id,
            disable_notification=disable_notification,
        )

        return "Message pinned successfully!"
    except Exception as e:
        return log_and_format_error(
            "telegram_pin_message",
            e,
            category=ErrorCategory.MSG,
            user_message=format_telegram_error(e),
            chat_id=chat_id,
            message_id=message_id,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Unpin Message", destructiveHint=True, openWorldHint=True)
)
@validate_id("chat_id")
async def telegram_unpin_message(
    chat_id: str,
    message_id: int,
) -> str:
    """Unpin a message from a chat.

    Args:
        chat_id: The chat ID.
        message_id: The ID of the message to unpin.

    Returns:
        Success message.
    """
    try:
        client = get_client()
        await client.unpin_message(
            chat_id=chat_id,
            message_id=message_id,
        )

        return "Message unpinned successfully!"
    except Exception as e:
        return log_and_format_error(
            "telegram_unpin_message",
            e,
            category=ErrorCategory.MSG,
            user_message=format_telegram_error(e),
            chat_id=chat_id,
            message_id=message_id,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Send Reaction", destructiveHint=True, openWorldHint=True)
)
@validate_id("chat_id")
async def telegram_send_reaction(
    chat_id: str,
    message_id: int,
    emoji: str,
) -> str:
    """Add a reaction emoji to a message.

    Args:
        chat_id: The chat ID.
        message_id: The ID of the message to react to.
        emoji: The emoji reaction (e.g., 👍, ❤️, 😂).

    Returns:
        Success message.
    """
    try:
        client = get_client()
        await client.send_reaction(
            chat_id=chat_id,
            message_id=message_id,
            emoji=emoji,
        )

        return f"Reaction '{emoji}' added successfully!"
    except Exception as e:
        return log_and_format_error(
            "telegram_send_reaction",
            e,
            category=ErrorCategory.MSG,
            user_message=format_telegram_error(e),
            chat_id=chat_id,
            message_id=message_id,
            emoji=emoji,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Send Poll", destructiveHint=True, openWorldHint=True)
)
@validate_id("chat_id")
async def telegram_send_poll(
    chat_id: str,
    question: str,
    options: list[str],
    is_anonymous: bool = True,
    allows_multiple_answers: bool = False,
) -> str:
    """Send a poll to a chat.

    Args:
        chat_id: The chat ID to send the poll to.
        question: The poll question.
        options: List of poll option strings (2-10 options).
        is_anonymous: Whether the poll is anonymous. Defaults to True.
        allows_multiple_answers: Allow users to select multiple options. Defaults to False.

    Returns:
        Success message with poll ID.
    """
    try:
        if not question:
            return "Poll question cannot be empty."

        if not options or len(options) < 2:
            return "Poll must have at least 2 options."

        if len(options) > 10:
            return "Poll cannot have more than 10 options."

        for i, opt in enumerate(options):
            if not opt:
                return f"Option {i + 1} cannot be empty."

        client = get_client()
        result = await client.send_poll(
            chat_id=chat_id,
            question=question,
            options=options,
            is_anonymous=is_anonymous,
            allows_multiple_answers=allows_multiple_answers,
        )

        return f"Poll sent successfully!\nMessage ID: {result.get('message_id')}"
    except Exception as e:
        return log_and_format_error(
            "telegram_send_poll",
            e,
            category=ErrorCategory.POLL,
            user_message=format_telegram_error(e),
            chat_id=chat_id,
            options=len(options),
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Get Chat Info", readOnlyHint=True, openWorldHint=True)
)
@validate_id("chat_id")
async def telegram_get_chat_info(
    chat_id: str,
) -> str:
    """Get detailed information about a chat.

    Args:
        chat_id: The chat ID to get information about.

    Returns:
        JSON-formatted chat information.
    """
    try:
        client = get_client()
        chat_info = await client.get_chat(chat_id=chat_id)

        return json.dumps(chat_info, indent=2)
    except Exception as e:
        return log_and_format_error(
            "telegram_get_chat_info",
            e,
            category=ErrorCategory.CHAT,
            user_message=format_telegram_error(e),
            chat_id=chat_id,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Get Chat Member", readOnlyHint=True, openWorldHint=True)
)
@validate_id("chat_id")
async def telegram_get_chat_member(
    chat_id: str,
    user_id: int,
) -> str:
    """Get information about a chat member.

    Args:
        chat_id: The chat ID.
        user_id: The user ID.

    Returns:
        JSON-formatted member information.
    """
    try:
        client = get_client()
        member_info = await client.get_chat_member(
            chat_id=chat_id,
            user_id=user_id,
        )

        return json.dumps(member_info, indent=2)
    except Exception as e:
        return log_and_format_error(
            "telegram_get_chat_member",
            e,
            category=ErrorCategory.CHAT,
            user_message=format_telegram_error(e),
            chat_id=chat_id,
            user_id=user_id,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Get Chat Member Count", readOnlyHint=True, openWorldHint=True)
)
@validate_id("chat_id")
async def telegram_get_chat_member_count(
    chat_id: str,
) -> str:
    """Get the number of members in a chat.

    Args:
        chat_id: The chat ID.

    Returns:
        Member count as a string.
    """
    try:
        client = get_client()
        count = await client.get_chat_member_count(chat_id=chat_id)

        return f"Chat has {count} member(s)."
    except Exception as e:
        return log_and_format_error(
            "telegram_get_chat_member_count",
            e,
            category=ErrorCategory.CHAT,
            user_message=format_telegram_error(e),
            chat_id=chat_id,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Set Typing", destructiveHint=True, openWorldHint=True)
)
@validate_id("chat_id")
async def telegram_set_typing(
    chat_id: str,
) -> str:
    """Send a typing indicator to a chat.

    Args:
        chat_id: The chat ID.

    Returns:
        Success message.
    """
    try:
        client = get_client()
        await client.set_typing(chat_id=chat_id)

        return "Typing indicator sent successfully!"
    except Exception as e:
        return log_and_format_error(
            "telegram_set_typing",
            e,
            category=ErrorCategory.CHAT,
            user_message=format_telegram_error(e),
            chat_id=chat_id,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Get Bot Info", readOnlyHint=True, openWorldHint=True)
)
async def telegram_get_bot_info() -> str:
    """Get information about the Telegram bot.

    Returns:
        JSON-formatted bot information.
    """
    try:
        client = get_client()
        bot_info = await client.get_me()

        return json.dumps(bot_info, indent=2)
    except Exception as e:
        return log_and_format_error(
            "telegram_get_bot_info",
            e,
            category=ErrorCategory.AUTH,
            user_message=format_telegram_error(e),
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Set Bot Commands", destructiveHint=True, openWorldHint=True)
)
async def telegram_set_bot_commands(
    scope_type: str = "default",
    language_code: str = "",
    chat_id: str | None = None,
    user_id: int | None = None,
) -> str:
    """Set bot commands in Telegram from the commands tracked in /help.

    This registers all available bot commands with Telegram so they appear
    in the command menu when users type '/'. Commands are sourced from
    the commands.py module which mirrors the /help command list.

    Args:
        scope_type: Scope for commands. Options:
            - "default": Default commands for all users (Recommended)
            - "all_private_chats": Commands for all private chats
            - "all_group_chats": Commands for all group/supergroup chats
            - "all_chat_administrators": Commands for all group admins
            - "chat": Commands for a specific chat (requires chat_id)
            - "chat_administrators": Commands for admins of specific chat (requires chat_id)
            - "chat_member": Commands for specific user in chat (requires chat_id and user_id)
        language_code: Two-letter ISO 639-1 language code (e.g., "en", "es", "zh").
            Empty string means commands apply to all languages.
        chat_id: Chat ID for chat-specific scopes. Required for "chat", "chat_administrators", "chat_member".
        user_id: User ID for "chat_member" scope.

    Returns:
        Success message with number of commands set, or error message.
    """
    try:
        from .commands import get_bot_commands

        # Get commands from the tracked list
        commands = get_bot_commands()

        # Build scope object based on scope_type
        scope: dict[str, Any] | None = None
        valid_scopes = [
            "default",
            "all_private_chats",
            "all_group_chats",
            "all_chat_administrators",
            "chat",
            "chat_administrators",
            "chat_member",
        ]

        if scope_type not in valid_scopes:
            return f"Invalid scope_type '{scope_type}'. Valid options: {', '.join(valid_scopes)}"

        if scope_type == "default":
            scope = {"type": "default"}
        elif scope_type == "all_private_chats":
            scope = {"type": "all_private_chats"}
        elif scope_type == "all_group_chats":
            scope = {"type": "all_group_chats"}
        elif scope_type == "all_chat_administrators":
            scope = {"type": "all_chat_administrators"}
        elif scope_type == "chat":
            if not chat_id:
                return "chat_id is required for 'chat' scope"
            scope = {"type": "chat", "chat_id": chat_id}
        elif scope_type == "chat_administrators":
            if not chat_id:
                return "chat_id is required for 'chat_administrators' scope"
            scope = {"type": "chat_administrators", "chat_id": chat_id}
        elif scope_type == "chat_member":
            if not chat_id or not user_id:
                return "Both chat_id and user_id are required for 'chat_member' scope"
            scope = {"type": "chat_member", "chat_id": chat_id, "user_id": user_id}

        client = get_client()
        lang_code = language_code if language_code else None

        await client.set_my_commands(
            commands=commands,
            scope=scope,
            language_code=lang_code,
        )

        scope_desc = f"scope={scope_type}"
        if chat_id:
            scope_desc += f", chat_id={chat_id}"
        if user_id:
            scope_desc += f", user_id={user_id}"
        if language_code:
            scope_desc += f", language={language_code}"

        return (
            f"Successfully set {len(commands)} bot commands!\n"
            f"Scope: {scope_desc}\n\n"
            f"Commands are now visible in Telegram's command menu.\n"
            f"Note: Set TELEGRAM_COMMANDS_SET=true in your .env to track this status."
        )
    except Exception as e:
        return log_and_format_error(
            "telegram_set_bot_commands",
            e,
            category=ErrorCategory.AUTH,
            user_message=format_telegram_error(e),
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Delete Bot Commands", destructiveHint=True, openWorldHint=True)
)
async def telegram_delete_bot_commands(
    scope_type: str = "default",
    language_code: str = "",
    chat_id: str | None = None,
) -> str:
    """Delete bot commands for the given scope and language.

    Args:
        scope_type: Scope for commands to delete. Same options as set_bot_commands.
        language_code: Two-letter ISO 639-1 language code. Empty means all languages.
        chat_id: Chat ID for chat-specific scopes.

    Returns:
        Success message or error message.
    """
    try:
        scope: dict[str, Any] | None = None

        if scope_type == "default":
            scope = {"type": "default"}
        elif scope_type == "all_private_chats":
            scope = {"type": "all_private_chats"}
        elif scope_type == "all_group_chats":
            scope = {"type": "all_group_chats"}
        elif scope_type == "all_chat_administrators":
            scope = {"type": "all_chat_administrators"}
        elif scope_type == "chat":
            if not chat_id:
                return "chat_id is required for 'chat' scope"
            scope = {"type": "chat", "chat_id": chat_id}
        elif scope_type == "chat_administrators":
            if not chat_id:
                return "chat_id is required for 'chat_administrators' scope"
            scope = {"type": "chat_administrators", "chat_id": chat_id}

        client = get_client()
        lang_code = language_code if language_code else None

        await client.delete_my_commands(
            scope=scope,
            language_code=lang_code,
        )

        return f"Successfully deleted bot commands for scope={scope_type}"
    except Exception as e:
        return log_and_format_error(
            "telegram_delete_bot_commands",
            e,
            category=ErrorCategory.AUTH,
            user_message=format_telegram_error(e),
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Get Bot Commands", readOnlyHint=True, openWorldHint=True)
)
async def telegram_get_bot_commands(
    scope_type: str = "default",
    language_code: str = "",
    chat_id: str | None = None,
) -> str:
    """Get the currently registered bot commands from Telegram.

    Args:
        scope_type: Scope to query. Same options as set_bot_commands.
        language_code: Two-letter ISO 639-1 language code.
        chat_id: Chat ID for chat-specific scopes.

    Returns:
        JSON-formatted list of registered commands, or message if none set.
    """
    try:
        client = get_client()
        commands = await client.get_my_commands()

        if not commands:
            return "No bot commands currently registered."

        return json.dumps(commands, indent=2)
    except Exception as e:
        return log_and_format_error(
            "telegram_get_bot_commands",
            e,
            category=ErrorCategory.AUTH,
            user_message=format_telegram_error(e),
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Get Queued Messages", readOnlyHint=True)
)
async def telegram_get_queued_messages(
    clear_after_read: bool = False,
) -> str:
    """Get messages from the polling service queue.

    Use this to retrieve commands sent while OpenCode wasn't running.

    Args:
        clear_after_read: Clear the queue after reading messages (default: false).

    Returns:
        JSON-formatted list of queued messages or message indicating no messages found.
    """
    try:
        messages = get_queued_messages(clear_after_read=clear_after_read)

        if not messages:
            return "No queued messages found."

        return json.dumps(messages, indent=2)
    except Exception as e:
        return log_and_format_error(
            "telegram_get_queued_messages",
            e,
            category=ErrorCategory.GENERAL,
            user_message=f"Error reading queue file at {_ensure_queue_path_initialized()}",
        )


# Global state for background services
_polling_thread: threading.Thread | None = None
_bridge_thread: threading.Thread | None = None
_shutdown_event = threading.Event()


def run_polling_service():
    """Run the polling service in a background thread."""
    from telegram_polling_service.polling_service import TelegramPollingService
    
    async def async_polling():
        service = TelegramPollingService()
        service.running = True
        
        logger.info("Background polling service started")
        try:
            while not _shutdown_event.is_set():
                await service.poll_once()
                # Short sleep - long polling already waits for messages
                # Just a brief pause to avoid tight loop if Telegram returns immediately
                await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Polling service error: {e}")
        finally:
            await service.client.close()
            if hasattr(service, '_save_offset'):
                service._save_offset(service.last_offset)
            logger.info("Background polling service stopped")
    
    # Create a new event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(async_polling())
    finally:
        loop.close()


def run_bridge_service(
    opencode_url: str,
    reply_to_telegram: bool,
    provider_id: str,
    model_id: str,
):
    """Run the bridge service in a background thread."""
    from telegram_bridge.bridge_service import TelegramOpenCodeBridge
    
    async def async_bridge():
        settings = get_settings()
        bridge = TelegramOpenCodeBridge(
            opencode_url=opencode_url,
            queue_dir=settings.queue_dir,
            poll_interval=0.5,  # Fast polling - queue caching handles efficiency
            reply_to_telegram=reply_to_telegram,
            bot_token=settings.bot_token,
            provider_id=provider_id,
            model_id=model_id,
            favourite_models=settings.get_favourite_models(),
        )
        bridge.running = True
        
        logger.info(f"Background bridge service started (OpenCode: {opencode_url})")
        
        # Wait for OpenCode to be available
        retry_count = 0
        max_retries = 30  # Wait up to 60 seconds
        while not _shutdown_event.is_set() and retry_count < max_retries:
            if await bridge.opencode.health_check():
                logger.info("Connected to OpenCode server")
                break
            retry_count += 1
            logger.debug(f"Waiting for OpenCode server... ({retry_count}/{max_retries})")
            await asyncio.sleep(2)
        else:
            if _shutdown_event.is_set():
                return
            logger.warning(f"Could not connect to OpenCode at {opencode_url}, bridge will retry")
        
        try:
            while not _shutdown_event.is_set():
                try:
                    await bridge.process_queue()
                except Exception as e:
                    logger.error(f"Bridge process error: {e}")
                await asyncio.sleep(bridge.poll_interval)
        except Exception as e:
            logger.error(f"Bridge service error: {e}")
        finally:
            await bridge.opencode.close()
            if bridge.telegram:
                await bridge.telegram.close()
            bridge._save_state()
            logger.info("Background bridge service stopped")
    
    # Create a new event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(async_bridge())
    finally:
        loop.close()


def start_background_services(
    enable_polling: bool = False,
    enable_bridge: bool = False,
    opencode_url: str = "http://localhost:4096",
    reply_to_telegram: bool = True,
    provider_id: str = "deepseek",
    model_id: str = "deepseek-reasoner",
):
    """Start background services in daemon threads."""
    global _polling_thread, _bridge_thread
    
    _shutdown_event.clear()
    
    if enable_polling:
        _polling_thread = threading.Thread(
            target=run_polling_service,
            daemon=True,
            name="telegram-polling",
        )
        _polling_thread.start()
        logger.info("Started background polling thread")
    
    if enable_bridge:
        _bridge_thread = threading.Thread(
            target=run_bridge_service,
            args=(opencode_url, reply_to_telegram, provider_id, model_id),
            daemon=True,
            name="telegram-bridge",
        )
        _bridge_thread.start()
        logger.info("Started background bridge thread")


def stop_background_services():
    """Stop background services gracefully."""
    global _polling_thread, _bridge_thread
    
    logger.info("Stopping background services...")
    _shutdown_event.set()
    
    if _polling_thread and _polling_thread.is_alive():
        _polling_thread.join(timeout=5)
        logger.info("Polling thread stopped")
    
    if _bridge_thread and _bridge_thread.is_alive():
        _bridge_thread.join(timeout=5)
        logger.info("Bridge thread stopped")
    
    _polling_thread = None
    _bridge_thread = None


def run_server():
    """Run the MCP server."""
    # Ensure queue directory exists
    queue_path = _ensure_queue_path_initialized()
    queue_path.parent.mkdir(parents=True, exist_ok=True)

    # FastMCP.run() is synchronous and manages its own event loop
    mcp.run(transport="stdio")


def cleanup_resources():
    """Clean up global resources."""
    global _telegram_client
    
    # Stop background services
    stop_background_services()
    
    if _telegram_client:
        # Run async cleanup in a new event loop
        try:
            asyncio.run(_telegram_client.close())
        except Exception:
            pass
        _telegram_client = None


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Telegram MCP Server with integrated polling and bridge services",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run MCP server only (default)
  telegram-mcp-server

  # Run with background polling (captures messages when idle)
  telegram-mcp-server --enable-polling

  # Run with polling and bridge (full two-way Telegram integration)
  telegram-mcp-server --enable-polling --enable-bridge

  # Specify OpenCode URL for bridge
  telegram-mcp-server --enable-polling --enable-bridge --opencode-url http://localhost:8080

Environment variables:
  TELEGRAM_BOT_TOKEN   - Bot token (required)
  TELEGRAM_CHAT_ID     - Default chat ID (required)
  TELEGRAM_QUEUE_DIR   - Queue directory (default: ~/.local/share/telegram_mcp_server)
        """,
    )
    parser.add_argument(
        "--enable-polling",
        action="store_true",
        default=os.environ.get("TELEGRAM_ENABLE_POLLING", "").lower() in ("true", "1", "yes"),
        help="Enable background polling service to capture messages",
    )
    parser.add_argument(
        "--enable-bridge",
        action="store_true",
        default=os.environ.get("TELEGRAM_ENABLE_BRIDGE", "").lower() in ("true", "1", "yes"),
        help="Enable bridge service to forward messages to OpenCode",
    )
    parser.add_argument(
        "--opencode-url",
        default=os.environ.get("TELEGRAM_OPENCODE_URL", "http://localhost:4096"),
        help="OpenCode HTTP API URL (default: http://localhost:4096)",
    )
    parser.add_argument(
        "--no-reply",
        action="store_true",
        default=os.environ.get("TELEGRAM_NO_REPLY", "").lower() in ("true", "1", "yes"),
        help="Disable sending OpenCode responses back to Telegram",
    )
    parser.add_argument(
        "--provider",
        default=os.environ.get("TELEGRAM_PROVIDER", "deepseek"),
        help="OpenCode provider ID (default: deepseek)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("TELEGRAM_MODEL", "deepseek-reasoner"),
        help="OpenCode model ID (default: deepseek-reasoner)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Log configuration
    if args.enable_polling or args.enable_bridge:
        logger.info("Telegram MCP Server starting with integrated services")
        if args.enable_polling:
            logger.info("  - Background polling: ENABLED")
        if args.enable_bridge:
            logger.info(f"  - Bridge to OpenCode: ENABLED ({args.opencode_url})")
            logger.info(f"  - Reply to Telegram: {'DISABLED' if args.no_reply else 'ENABLED'}")
            logger.info(f"  - Model: {args.provider}/{args.model}")
    
    try:
        # Start background services before MCP server
        start_background_services(
            enable_polling=args.enable_polling,
            enable_bridge=args.enable_bridge,
            opencode_url=args.opencode_url,
            reply_to_telegram=not args.no_reply,
            provider_id=args.provider,
            model_id=args.model,
        )
        
        run_server()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    finally:
        cleanup_resources()


if __name__ == "__main__":
    main()
