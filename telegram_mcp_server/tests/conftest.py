"""Test configuration for pytest."""

import pytest


@pytest.fixture
def mock_settings():
    """Mock environment variables for settings."""
    import os

    os.environ["TELEGRAM_BOT_TOKEN"] = "test_bot_token"
    os.environ["TELEGRAM_CHAT_ID"] = "123456789"
    os.environ["TELEGRAM_API_BASE_URL"] = "https://api.telegram.org"
    return None


@pytest.fixture
def sample_message_data():
    """Sample message data for testing."""
    return {
        "message_id": 1,
        "chat": {"id": 123456789, "type": "private"},
        "from": {"id": 987654321, "username": "testuser"},
        "text": "Hello, world!",
        "date": 1234567890,
    }
