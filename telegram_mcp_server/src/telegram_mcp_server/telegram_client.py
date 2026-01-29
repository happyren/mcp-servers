"""Telegram Bot API client."""

import httpx
from typing import Any
from dataclasses import dataclass


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

    def __init__(self, bot_token: str, base_url: str = "https://api.telegram.org"):
        """Initialize the Telegram client.

        Args:
            bot_token: The Telegram bot token from @BotFather
            base_url: The base URL for Telegram API
        """
        self.bot_token = bot_token
        self.base_url = f"{base_url}/bot{bot_token}"
        self._last_update_id: int | None = None
        self._client = httpx.AsyncClient(timeout=60.0)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def _request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Make a request to the Telegram API.

        Args:
            method: The API method to call
            params: Optional parameters for the method

        Returns:
            The API response as a dictionary
        """
        url = f"{self.base_url}/{method}"
        response = await self._client.post(url, json=params or {})
        response.raise_for_status()
        result = response.json()

        if not result.get("ok"):
            raise Exception(f"Telegram API error: {result.get('description', 'Unknown error')}")

        return result.get("result", {})

    async def get_me(self) -> dict[str, Any]:
        """Get information about the bot.

        Returns:
            Bot information dictionary
        """
        return await self._request("getMe")

    async def send_message(
        self,
        chat_id: str | int,
        text: str,
        parse_mode: str | None = "Markdown",
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        """Send a text message to a chat.

        Args:
            chat_id: The chat ID to send to
            text: The message text
            parse_mode: Parse mode (Markdown, HTML, or None)
            disable_notification: Send silently

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

        return await self._request("sendMessage", params)

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

        result = await self._request("getUpdates", params)
        # getUpdates returns a list, but _request can return dict or list
        return result if isinstance(result, list) else []

    async def get_new_messages(self, timeout: int = 30) -> list[TelegramMessage]:
        """Get new messages since the last check.

        Args:
            timeout: Long polling timeout

        Returns:
            List of new messages
        """
        updates = await self.get_updates(
            offset=self._last_update_id,
            timeout=timeout,
            allowed_updates=["message"],
        )

        messages = []
        for update in updates:
            # Update the offset to acknowledge this update
            update_id = update.get("update_id", 0)
            if self._last_update_id is None or update_id >= self._last_update_id:
                self._last_update_id = update_id + 1

            # Extract message if present
            msg_data = update.get("message")
            if msg_data:
                from_user = msg_data.get("from", {})
                messages.append(
                    TelegramMessage(
                        message_id=msg_data.get("message_id", 0),
                        chat_id=msg_data.get("chat", {}).get("id", 0),
                        from_user_id=from_user.get("id"),
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
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "reply_to_message_id": message_id,
        }
        if parse_mode:
            params["parse_mode"] = parse_mode

        return await self._request("sendMessage", params)

    async def get_chat(self, chat_id: str | int) -> dict[str, Any]:
        """Get information about a chat.

        Args:
            chat_id: The chat ID

        Returns:
            Chat information
        """
        return await self._request("getChat", {"chat_id": chat_id})

    async def set_typing(self, chat_id: str | int) -> bool:
        """Send a typing indicator to a chat.

        Args:
            chat_id: The chat ID

        Returns:
            True if successful
        """
        await self._request("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        return True
