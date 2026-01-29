"""Tests for validation module."""

import pytest
import asyncio

from telegram_mcp_server.validation import (
    validate_id,
    validate_single_id,
    validate_message_text,
    validate_parse_mode,
)


class TestValidateSingleId:
    """Tests for validate_single_id function."""

    def test_valid_integer_id(self):
        """Test valid integer IDs."""
        result, error = validate_single_id(123456, "chat_id")
        assert result == 123456
        assert error is None

        result, error = validate_single_id(-1001234567890, "chat_id")
        assert result == -1001234567890
        assert error is None

    def test_valid_string_integer_id(self):
        """Test string representations of integer IDs."""
        result, error = validate_single_id("123456", "chat_id")
        assert result == 123456
        assert error is None

    def test_valid_username(self):
        """Test valid usernames."""
        result, error = validate_single_id("@testuser", "chat_id")
        assert result == "@testuser"
        assert error is None

        result, error = validate_single_id("testuser", "chat_id")
        assert result == "@testuser"
        assert error is None

    def test_invalid_username_too_short(self):
        """Test username that's too short."""
        result, error = validate_single_id("usr", "chat_id")
        assert result is None
        assert "Must be an integer ID or valid username" in error

    def test_invalid_username_invalid_chars(self):
        """Test username with invalid characters."""
        result, error = validate_single_id("test-user!", "chat_id")
        assert result is None
        assert "Must be an integer ID or valid username" in error

    def test_invalid_type(self):
        """Test invalid types."""
        result, error = validate_single_id(None, "chat_id")
        assert result is None
        assert "Type must be int or string" in error

    def test_empty_string(self):
        """Test empty string."""
        result, error = validate_single_id("", "chat_id")
        assert result is None
        assert "Empty string" in error


class TestValidateIdDecorator:
    """Tests for @validate_id decorator."""

    @pytest.mark.asyncio
    @validate_id("chat_id", "user_id")
    async def dummy_function(self, chat_id, user_id, extra_param):
        """Dummy function for testing decorator."""
        return chat_id, user_id, extra_param

    @pytest.mark.asyncio
    @validate_id("chat_id", "user_ids")
    async def dummy_function_with_list(self, chat_id, user_ids, extra_param):
        """Dummy function for testing decorator with list parameter."""
        return chat_id, user_ids, extra_param

    async def test_validates_single_ids(self):
        """Test decorator validates single IDs."""
        result = await self.dummy_function(chat_id="123", user_id=456, extra_param="test")
        assert result == (123, 456, "test")

    async def test_validates_list_of_ids(self):
        """Test decorator validates lists of IDs."""
        result = await self.dummy_function_with_list(
            chat_id="123", user_ids=["456", "789"], extra_param="test"
        )
        assert result == (123, [456, 789], "test")

    async def test_returns_error_on_invalid_id(self):
        """Test decorator returns error on invalid ID."""
        result = await self.dummy_function(chat_id="invalid", user_id=456, extra_param="test")
        assert "Must be an integer ID or valid username" in result


class TestValidateMessageText:
    """Tests for validate_message_text function."""

    def test_valid_message(self):
        """Test valid message text."""
        result, error = validate_message_text("Hello, world!")
        assert result == "Hello, world!"
        assert error is None

    def test_empty_message(self):
        """Test empty message."""
        result, error = validate_message_text("")
        assert result == ""
        assert error == "Message text cannot be empty"

    def test_message_too_long(self):
        """Test message exceeding maximum length."""
        long_text = "a" * 5000
        result, error = validate_message_text(long_text, max_length=100)
        assert result == ""
        assert "exceeds maximum length" in error


class TestValidateParseMode:
    """Tests for validate_parse_mode function."""

    def test_valid_parse_modes(self):
        """Test valid parse modes."""
        for mode in ["Markdown", "MarkdownV2", "HTML"]:
            result, error = validate_parse_mode(mode)
            assert result == mode
            assert error is None

    def test_none_string(self):
        """Test 'None' string."""
        result, error = validate_parse_mode("None")
        assert result is None
        assert error is None

    def test_invalid_parse_mode(self):
        """Test invalid parse mode."""
        result, error = validate_parse_mode("InvalidMode")
        assert result is None
        assert "Invalid parse_mode" in error
