#!/usr/bin/env python3
"""Test server modifications."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from telegram_mcp_server.server import get_queued_messages, QUEUE_FILE_PATH

print(f"Queue file path: {QUEUE_FILE_PATH}")
print(f"Queue file exists: {QUEUE_FILE_PATH.exists()}")

# Test reading empty queue
messages = get_queued_messages()
print(f"Empty queue messages: {messages}")
assert messages == [], "Should return empty list for empty queue"

# Test with some dummy data
import json
test_messages = [
    {"message_id": 1, "text": "Test message 1"},
    {"message_id": 2, "text": "Test message 2"}
]
with open(QUEUE_FILE_PATH, 'w', encoding='utf-8') as f:
    json.dump(test_messages, f)

messages = get_queued_messages()
print(f"Loaded {len(messages)} messages")
assert len(messages) == 2, "Should load 2 messages"

# Test clear after read
messages = get_queued_messages(clear_after_read=True)
print(f"After clear, loaded {len(messages)} messages")
assert len(messages) == 2, "Should still return messages before clearing"

# Check if file is cleared
if QUEUE_FILE_PATH.exists():
    with open(QUEUE_FILE_PATH, 'r', encoding='utf-8') as f:
        content = f.read().strip()
    print(f"Queue file content after clear: '{content}'")
    assert content == '[]' or content == '', f"File should be empty, got: {content}"
else:
    print("Queue file deleted (also acceptable)")

print("✅ All tests passed!")