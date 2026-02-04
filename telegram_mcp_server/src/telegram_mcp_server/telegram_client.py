"""Telegram Bot API client with retry logic."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx

from .errors import logger

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 0.1
MAX_RETRY_DELAY = 5.0


@dataclass
class TelegramMessage:
    """Represents a Telegram message."""

    message_id: int
    chat_id: int
    from_user_id: int | None
    from_username: str | None
    text: str | None
    date: int
    raw: dict[str, Any]


class TelegramClient:
    """Client for interacting with Telegram Bot API."""

    def __init__(
        self,
        bot_token: str,
        base_url: str = "https://api.telegram.org",
        max_retries: int = MAX_RETRIES,
        retry_delay: float = INITIAL_RETRY_DELAY,
    ):
        """Initialize the Telegram client.

        Args:
            bot_token: The Telegram bot token from @BotFather
            base_url: The base URL for Telegram API
            max_retries: Maximum number of retry attempts
            retry_delay: Initial retry delay in seconds (exponential backoff)
        """
        self.bot_token = bot_token
        self.base_url = f"{base_url}/bot{bot_token}"
        self._last_update_id: int | None = None
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        # Use 60s timeout to support long polling (30s) with margin
        self._client = httpx.AsyncClient(timeout=60.0, limits=httpx.Limits(max_keepalive_connections=5, max_connections=10))
        self._bot_user_id: int | None = None

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def _request_with_retry(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        is_read: bool = True,
    ) -> dict[str, Any]:
        """Make a request to the Telegram API with retry logic.

        Args:
            method: The API method to call
            params: Optional parameters for the method
            is_read: Whether this is a read-only operation

        Returns:
            The API response as a dictionary
        """
        url = f"{self.base_url}/{method}"
        last_error: Optional[Exception] = None

        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.post(url, json=params or {})

                # Handle rate limiting with Retry-After header
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", self._retry_delay))
                    if attempt < self._max_retries:
                        logger.warning(
                            f"Rate limited. Retrying after {retry_after}s (attempt {attempt + 1}/{self._max_retries})"
                        )
                        await asyncio.sleep(retry_after)
                        continue

                response.raise_for_status()
                result = response.json()

                if not result.get("ok"):
                    error_msg = result.get("description", "Unknown error")
                    raise Exception(f"Telegram API error: {error_msg}")

                return result.get("result", {})

            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code in RETRYABLE_STATUS_CODES:
                    if attempt < self._max_retries:
                        delay = min(self._retry_delay * (2**attempt), MAX_RETRY_DELAY)
                        logger.warning(
                            f"HTTP {e.response.status_code}. Retrying after {delay:.1f}s "
                            f"(attempt {attempt + 1}/{self._max_retries})"
                        )
                        await asyncio.sleep(delay)
                        continue
                else:
                    break

            except (httpx.NetworkError, httpx.TimeoutException) as e:
                last_error = e
                if attempt < self._max_retries:
                    delay = min(self._retry_delay * (2**attempt), MAX_RETRY_DELAY)
                    logger.warning(
                        f"Network error. Retrying after {delay:.1f}s "
                        f"(attempt {attempt + 1}/{self._max_retries})"
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    break

            except Exception as e:
                last_error = e
                break

        raise last_error or Exception("Unknown error occurred")

    async def get_me(self) -> dict[str, Any]:
        """Get information about the bot.

        Returns:
            Bot information dictionary
        """
        return await self._request_with_retry("getMe")

    async def get_my_commands(self) -> list[dict[str, Any]]:
        """Get the current list of bot commands.

        Returns:
            List of bot command dictionaries with 'command' and 'description' keys
        """
        result = await self._request_with_retry("getMyCommands")
        return result if isinstance(result, list) else []

    async def set_my_commands(
        self,
        commands: list[dict[str, str]],
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> dict[str, Any]:
        """Set the list of bot commands.

        Args:
            commands: List of command dictionaries with 'command' and 'description' keys
            scope: BotCommandScope object defining the scope of commands. Options:
                   - {"type": "default"} - Default commands for all users
                   - {"type": "all_private_chats"} - All private chats
                   - {"type": "all_group_chats"} - All group/supergroup chats
                   - {"type": "all_chat_administrators"} - All group admins
                   - {"type": "chat", "chat_id": <id>} - Specific chat
                   - {"type": "chat_administrators", "chat_id": <id>} - Admins of specific chat
                   - {"type": "chat_member", "chat_id": <id>, "user_id": <id>} - Specific user in chat
            language_code: A two-letter ISO 639-1 language code. If empty, commands apply to all languages.

        Returns:
            API response (True on success)
        """
        params: dict[str, Any] = {"commands": commands}
        if scope is not None:
            params["scope"] = scope
        if language_code is not None:
            params["language_code"] = language_code
        return await self._request_with_retry("setMyCommands", params, is_read=False)

    async def delete_my_commands(
        self,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> dict[str, Any]:
        """Delete the list of bot commands for the given scope and language.

        Args:
            scope: BotCommandScope object defining the scope of commands to delete.
            language_code: A two-letter ISO 639-1 language code.

        Returns:
            API response (True on success)
        """
        params: dict[str, Any] = {}
        if scope is not None:
            params["scope"] = scope
        if language_code is not None:
            params["language_code"] = language_code
        return await self._request_with_retry("deleteMyCommands", params, is_read=False)

    async def ensure_commands_set(self, commands: list[dict[str, str]], force: bool = False) -> bool:
        """Ensure bot commands are set, only setting if none exist or force=True.

        Args:
            commands: List of command dictionaries with 'command' and 'description' keys
            force: If True, set commands even if they already exist

        Returns:
            True if commands were set, False if already set
        """
        try:
            existing = await self.get_my_commands()
            if not existing or force:
                logger.info(f"Setting bot commands ({len(commands)} commands)")
                await self.set_my_commands(commands)
                return True
            else:
                logger.debug(f"Bot commands already set ({len(existing)} commands)")
                return False
        except Exception as e:
            logger.warning(f"Failed to ensure bot commands: {e}")
            return False

    async def send_message(
        self,
        chat_id: str | int,
        text: str,
        parse_mode: str | None = "Markdown",
        disable_notification: bool = False,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        """Send a text message to a chat.

        Args:
            chat_id: The chat ID to send to
            text: The message text
            parse_mode: Parse mode (Markdown, HTML, or None)
            disable_notification: Send silently
            reply_to_message_id: Optional message ID to reply to

        Returns:
            The sent message information
        """
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_notification": disable_notification,
        }
        if parse_mode:
            params["parse_mode"] = parse_mode
        if reply_to_message_id:
            params["reply_to_message_id"] = reply_to_message_id

        return await self._request_with_retry("sendMessage", params, is_read=False)

    async def edit_message(
        self,
        chat_id: str | int,
        message_id: int,
        text: str,
        parse_mode: str | None = "Markdown",
    ) -> dict[str, Any]:
        """Edit a message that was previously sent.

        Args:
            chat_id: The chat ID
            message_id: The message ID to edit
            text: New message text
            parse_mode: Parse mode

        Returns:
            The edited message information
        """
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if parse_mode:
            params["parse_mode"] = parse_mode

        return await self._request_with_retry("editMessageText", params, is_read=False)

    async def delete_message(
        self,
        chat_id: str | int,
        message_id: int,
    ) -> dict[str, Any]:
        """Delete a message.

        Args:
            chat_id: The chat ID
            message_id: The message ID to delete

        Returns:
            Success indicator
        """
        return await self._request_with_retry(
            "deleteMessage",
            {"chat_id": chat_id, "message_id": message_id},
            is_read=False,
        )

    async def forward_message(
        self,
        chat_id: str | int,
        from_chat_id: str | int,
        message_id: int,
    ) -> dict[str, Any]:
        """Forward a message to another chat.

        Args:
            chat_id: Target chat ID
            from_chat_id: Source chat ID
            message_id: Message ID to forward

        Returns:
            The forwarded message information
        """
        return await self._request_with_retry(
            "forwardMessage",
            {
                "chat_id": chat_id,
                "from_chat_id": from_chat_id,
                "message_id": message_id,
            },
            is_read=False,
        )

    async def pin_message(
        self,
        chat_id: str | int,
        message_id: int,
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        """Pin a message in a chat.

        Args:
            chat_id: The chat ID
            message_id: The message ID to pin
            disable_notification: Send silently

        Returns:
            Success indicator
        """
        return await self._request_with_retry(
            "pinChatMessage",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "disable_notification": disable_notification,
            },
            is_read=False,
        )

    async def unpin_message(
        self,
        chat_id: str | int,
        message_id: int,
    ) -> dict[str, Any]:
        """Unpin a message in a chat.

        Args:
            chat_id: The chat ID
            message_id: The message ID to unpin

        Returns:
            Success indicator
        """
        return await self._request_with_retry(
            "unpinChatMessage",
            {"chat_id": chat_id, "message_id": message_id},
            is_read=False,
        )

    async def send_reaction(
        self,
        chat_id: str | int,
        message_id: int,
        emoji: str,
        is_big: bool = False,
    ) -> dict[str, Any]:
        """Add a reaction to a message.

        Args:
            chat_id: The chat ID
            message_id: The message ID
            emoji: Emoji reaction
            is_big: Show as big animation

        Returns:
            Success indicator
        """
        return await self._request_with_retry(
            "setMessageReaction",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reaction": [{"type": "emoji", "emoji": emoji}],
                "is_big": is_big,
            },
            is_read=False,
        )

    async def send_poll(
        self,
        chat_id: str | int,
        question: str,
        options: list[str],
        is_anonymous: bool = True,
        allows_multiple_answers: bool = False,
        correct_option_id: int | None = None,
        explanation: str | None = None,
        explanation_parse_mode: str | None = None,
    ) -> dict[str, Any]:
        """Send a poll to a chat.

        Args:
            chat_id: The chat ID
            question: Poll question
            options: List of poll options
            is_anonymous: Whether the poll is anonymous
            allows_multiple_answers: Allow multiple answers
            correct_option_id: For quizzes, the correct option index
            explanation: Explanation for correct answer (quizzes only)
            explanation_parse_mode: Parse mode for explanation

        Returns:
            The sent poll information
        """
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "question": question,
            "options": [{"text": opt} for opt in options],
            "is_anonymous": is_anonymous,
            "allows_multiple_answers": allows_multiple_answers,
        }

        if correct_option_id is not None:
            params["type"] = "quiz"
            params["correct_option_id"] = correct_option_id
            if explanation:
                params["explanation"] = explanation
                if explanation_parse_mode:
                    params["explanation_parse_mode"] = explanation_parse_mode

        return await self._request_with_retry("sendPoll", params, is_read=False)

    async def get_updates(
        self,
        offset: int | None = None,
        limit: int = 100,
        timeout: int = 2,
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get updates (new messages) from Telegram.

        Args:
            offset: Identifier of the first update to return
            limit: Maximum number of updates
            timeout: Long polling timeout in seconds
            allowed_updates: List of update types to receive

        Returns:
            List of updates
        """
        params: dict[str, Any] = {
            "limit": limit,
            "timeout": timeout,
        }
        if offset is not None:
            params["offset"] = offset
        if allowed_updates is not None:
            params["allowed_updates"] = allowed_updates

        result = await self._request_with_retry("getUpdates", params)
        return result if isinstance(result, list) else []

    async def _get_bot_user_id(self) -> int | None:
        """Get the bot's own user ID, caching it for future use."""
        if self._bot_user_id is None:
            try:
                bot_info = await self.get_me()
                self._bot_user_id = bot_info.get("id")
            except Exception as e:
                logger.warning(f"Failed to get bot user ID: {e}")
        return self._bot_user_id

    async def get_new_messages(self, timeout: int = 30) -> list[TelegramMessage]:
        """Get new messages since the last check.

        Args:
            timeout: Long polling timeout

        Returns:
            List of new messages (excluding messages sent by the bot itself)
        """
        # Get bot's user ID to filter out own messages
        bot_user_id = await self._get_bot_user_id()

        updates = await self.get_updates(
            offset=self._last_update_id,
            timeout=timeout,
            allowed_updates=["message"],
        )

        messages = []
        for update in updates:
            update_id = update.get("update_id", 0)
            if self._last_update_id is None or update_id >= self._last_update_id:
                self._last_update_id = update_id + 1

            msg_data = update.get("message")
            if msg_data:
                from_user = msg_data.get("from", {})
                from_user_id = from_user.get("id")

                # Skip messages sent by the bot itself to prevent loops
                if bot_user_id and from_user_id == bot_user_id:
                    continue

                messages.append(
                    TelegramMessage(
                        message_id=msg_data.get("message_id", 0),
                        chat_id=msg_data.get("chat", {}).get("id", 0),
                        from_user_id=from_user_id,
                        from_username=from_user.get("username"),
                        text=msg_data.get("text"),
                        date=msg_data.get("date", 0),
                        raw=msg_data,
                    )
                )

        return messages

    async def reply_to_message(
        self,
        chat_id: str | int,
        message_id: int,
        text: str,
        parse_mode: str | None = "Markdown",
    ) -> dict[str, Any]:
        """Reply to a specific message.

        Args:
            chat_id: The chat ID
            message_id: The message ID to reply to
            text: The reply text
            parse_mode: Parse mode

        Returns:
            The sent message information
        """
        return await self.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_to_message_id=message_id,
        )

    async def get_chat(self, chat_id: str | int) -> dict[str, Any]:
        """Get information about a chat.

        Args:
            chat_id: The chat ID

        Returns:
            Chat information
        """
        return await self._request_with_retry("getChat", {"chat_id": chat_id})

    async def get_chat_member(
        self,
        chat_id: str | int,
        user_id: int,
    ) -> dict[str, Any]:
        """Get information about a chat member.

        Args:
            chat_id: The chat ID
            user_id: The user ID

        Returns:
            Chat member information
        """
        return await self._request_with_retry(
            "getChatMember", {"chat_id": chat_id, "user_id": user_id}
        )

    async def get_chat_member_count(
        self,
        chat_id: str | int,
    ) -> int:
        """Get the number of members in a chat.

        Args:
            chat_id: The chat ID

        Returns:
            Number of members
        """
        result = await self._request_with_retry("getChatMemberCount", {"chat_id": chat_id})
        return result if isinstance(result, int) else 0

    async def set_typing(self, chat_id: str | int) -> bool:
        """Send a typing indicator to a chat.

        Args:
            chat_id: The chat ID

        Returns:
            True if successful
        """
        await self._request_with_retry(
            "sendChatAction", {"chat_id": chat_id, "action": "typing"}
        )
        return True

    async def send_message_with_keyboard(
        self,
        chat_id: str | int,
        text: str,
        inline_keyboard: list[list[dict[str, str]]],
        parse_mode: str | None = "Markdown",
    ) -> dict[str, Any]:
        """Send a message with an inline keyboard.

        Args:
            chat_id: The chat ID
            text: The message text
            inline_keyboard: List of button rows, each row is a list of buttons.
                            Each button is a dict with 'text' and 'callback_data'.
            parse_mode: Parse mode

        Returns:
            The sent message information
        """
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": {"inline_keyboard": inline_keyboard},
        }
        if parse_mode:
            params["parse_mode"] = parse_mode

        return await self._request_with_retry("sendMessage", params, is_read=False)

    async def edit_message_with_keyboard(
        self,
        chat_id: str | int,
        message_id: int,
        text: str,
        inline_keyboard: list[list[dict[str, str]]] | None = None,
        parse_mode: str | None = "Markdown",
    ) -> dict[str, Any]:
        """Edit a message, optionally updating or removing the inline keyboard.

        Args:
            chat_id: The chat ID
            message_id: The message ID to edit
            text: New message text
            inline_keyboard: New keyboard (None to remove keyboard)
            parse_mode: Parse mode

        Returns:
            The edited message information
        """
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if parse_mode:
            params["parse_mode"] = parse_mode
        if inline_keyboard is not None:
            params["reply_markup"] = {"inline_keyboard": inline_keyboard}

        return await self._request_with_retry("editMessageText", params, is_read=False)

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool:
        """Answer a callback query (button click).

        Args:
            callback_query_id: The callback query ID
            text: Optional text to show to the user
            show_alert: If True, show as alert instead of toast

        Returns:
            True if successful
        """
        params: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            params["text"] = text
        if show_alert:
            params["show_alert"] = show_alert

        await self._request_with_retry("answerCallbackQuery", params, is_read=False)
        return True

    async def get_updates_with_callbacks(
        self,
        offset: int | None = None,
        limit: int = 100,
        timeout: int = 2,
    ) -> list[dict[str, Any]]:
        """Get updates including callback queries (button clicks).

        Args:
            offset: Identifier of the first update to return
            limit: Maximum number of updates
            timeout: Long polling timeout in seconds

        Returns:
            List of updates
        """
        params: dict[str, Any] = {
            "limit": limit,
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            params["offset"] = offset

        result = await self._request_with_retry("getUpdates", params)
        return result if isinstance(result, list) else []
    
    # ==================== Forum Topics API ====================
    
    async def create_forum_topic(
        self,
        chat_id: str | int,
        name: str,
        icon_color: int | None = None,
        icon_custom_emoji_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a topic in a forum supergroup chat.
        
        Args:
            chat_id: The supergroup chat ID
            name: Topic name (1-128 characters)
            icon_color: RGB color (must be one of: 0x6FB9F0, 0xFFD67E, 0xCB86DB, 
                        0x8EEE98, 0xFF93B2, 0xFB6F5F)
            icon_custom_emoji_id: Custom emoji identifier for topic icon
            
        Returns:
            ForumTopic object with message_thread_id
        """
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "name": name[:128],
        }
        if icon_color is not None:
            params["icon_color"] = icon_color
        if icon_custom_emoji_id is not None:
            params["icon_custom_emoji_id"] = icon_custom_emoji_id
        
        return await self._request_with_retry("createForumTopic", params, is_read=False)
    
    async def edit_forum_topic(
        self,
        chat_id: str | int,
        message_thread_id: int,
        name: str | None = None,
        icon_custom_emoji_id: str | None = None,
    ) -> bool:
        """Edit name and/or icon of a forum topic.
        
        Args:
            chat_id: The supergroup chat ID
            message_thread_id: The topic ID
            name: New topic name (optional)
            icon_custom_emoji_id: New custom emoji ID (optional)
            
        Returns:
            True on success
        """
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "message_thread_id": message_thread_id,
        }
        if name is not None:
            params["name"] = name[:128]
        if icon_custom_emoji_id is not None:
            params["icon_custom_emoji_id"] = icon_custom_emoji_id
        
        await self._request_with_retry("editForumTopic", params, is_read=False)
        return True
    
    async def close_forum_topic(
        self,
        chat_id: str | int,
        message_thread_id: int,
    ) -> bool:
        """Close an open topic in a forum supergroup.
        
        Args:
            chat_id: The supergroup chat ID
            message_thread_id: The topic ID
            
        Returns:
            True on success
        """
        await self._request_with_retry(
            "closeForumTopic",
            {"chat_id": chat_id, "message_thread_id": message_thread_id},
            is_read=False,
        )
        return True
    
    async def reopen_forum_topic(
        self,
        chat_id: str | int,
        message_thread_id: int,
    ) -> bool:
        """Reopen a closed topic in a forum supergroup.
        
        Args:
            chat_id: The supergroup chat ID
            message_thread_id: The topic ID
            
        Returns:
            True on success
        """
        await self._request_with_retry(
            "reopenForumTopic",
            {"chat_id": chat_id, "message_thread_id": message_thread_id},
            is_read=False,
        )
        return True
    
    async def delete_forum_topic(
        self,
        chat_id: str | int,
        message_thread_id: int,
    ) -> bool:
        """Delete a forum topic along with all its messages.
        
        Args:
            chat_id: The supergroup chat ID
            message_thread_id: The topic ID
            
        Returns:
            True on success
        """
        await self._request_with_retry(
            "deleteForumTopic",
            {"chat_id": chat_id, "message_thread_id": message_thread_id},
            is_read=False,
        )
        return True
    
    async def hide_general_forum_topic(
        self,
        chat_id: str | int,
    ) -> bool:
        """Hide the 'General' topic in a forum supergroup.
        
        The bot must be an administrator with can_manage_topics rights.
        
        Args:
            chat_id: The supergroup chat ID
            
        Returns:
            True on success
        """
        await self._request_with_retry(
            "hideGeneralForumTopic",
            {"chat_id": chat_id},
            is_read=False,
        )
        return True
    
    async def unhide_general_forum_topic(
        self,
        chat_id: str | int,
    ) -> bool:
        """Unhide the 'General' topic in a forum supergroup.
        
        The bot must be an administrator with can_manage_topics rights.
        
        Args:
            chat_id: The supergroup chat ID
            
        Returns:
            True on success
        """
        await self._request_with_retry(
            "unhideGeneralForumTopic",
            {"chat_id": chat_id},
            is_read=False,
        )
        return True
    
    async def close_general_forum_topic(
        self,
        chat_id: str | int,
    ) -> bool:
        """Close the 'General' topic in a forum supergroup.
        
        The bot must be an administrator with can_manage_topics rights.
        
        Args:
            chat_id: The supergroup chat ID
            
        Returns:
            True on success
        """
        await self._request_with_retry(
            "closeGeneralForumTopic",
            {"chat_id": chat_id},
            is_read=False,
        )
        return True
    
    async def reopen_general_forum_topic(
        self,
        chat_id: str | int,
    ) -> bool:
        """Reopen the closed 'General' topic in a forum supergroup.
        
        The bot must be an administrator with can_manage_topics rights.
        
        Args:
            chat_id: The supergroup chat ID
            
        Returns:
            True on success
        """
        await self._request_with_retry(
            "reopenGeneralForumTopic",
            {"chat_id": chat_id},
            is_read=False,
        )
        return True

    async def send_message_to_topic(
        self,
        chat_id: str | int,
        message_thread_id: int,
        text: str,
        parse_mode: str | None = "Markdown",
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        """Send a text message to a specific topic in a forum.
        
        Args:
            chat_id: The supergroup chat ID
            message_thread_id: The topic ID
            text: The message text
            parse_mode: Parse mode (Markdown, HTML, or None)
            disable_notification: Send silently
            
        Returns:
            The sent message information
        """
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "message_thread_id": message_thread_id,
            "text": text,
            "disable_notification": disable_notification,
        }
        if parse_mode:
            params["parse_mode"] = parse_mode
        
        return await self._request_with_retry("sendMessage", params, is_read=False)
    
    async def send_message_with_keyboard_to_topic(
        self,
        chat_id: str | int,
        message_thread_id: int,
        text: str,
        inline_keyboard: list[list[dict[str, str]]],
        parse_mode: str | None = "Markdown",
    ) -> dict[str, Any]:
        """Send a message with an inline keyboard to a topic.
        
        Args:
            chat_id: The supergroup chat ID
            message_thread_id: The topic ID
            text: The message text
            inline_keyboard: List of button rows
            parse_mode: Parse mode
            
        Returns:
            The sent message information
        """
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "message_thread_id": message_thread_id,
            "text": text,
            "reply_markup": {"inline_keyboard": inline_keyboard},
        }
        if parse_mode:
            params["parse_mode"] = parse_mode
        
        return await self._request_with_retry("sendMessage", params, is_read=False)
    
    async def set_typing_in_topic(
        self,
        chat_id: str | int,
        message_thread_id: int,
    ) -> bool:
        """Send a typing indicator to a specific topic.
        
        Args:
            chat_id: The supergroup chat ID
            message_thread_id: The topic ID
            
        Returns:
            True if successful
        """
        await self._request_with_retry(
            "sendChatAction",
            {
                "chat_id": chat_id,
                "message_thread_id": message_thread_id,
                "action": "typing",
            },
        )
        return True
    
    async def get_forum_topic_icon_stickers(self) -> list[dict[str, Any]]:
        """Get custom emoji stickers available for forum topic icons.
        
        Returns:
            List of Sticker objects usable as forum topic icons
        """
        result = await self._request_with_retry("getForumTopicIconStickers")
        return result if isinstance(result, list) else []
