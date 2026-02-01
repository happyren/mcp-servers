"""Telegram Bot API client with retry logic."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx

from .errors import logger

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 60.0


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
        self._client = httpx.AsyncClient(timeout=60.0)
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
        from .errors import logger
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
        timeout: int = 30,
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
