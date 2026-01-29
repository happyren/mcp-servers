# Telegram MCP Server

[![Python Lint & Format Check](https://github.com/your-org/telegram-mcp-server/actions/workflows/lint.yml/badge.svg)](https://github.com/your-org/telegram-mcp-server/actions/workflows/lint.yml)
[![Python Tests](https://github.com/your-org/telegram-mcp-server/actions/workflows/test.yml/badge.svg)](https://github.com/your-org/telegram-mcp-server/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An MCP (Model Context Protocol) server that connects to the Telegram Bot API, allowing AI agents like OpenCode and Claude to send messages, summaries, and receive commands via Telegram.

## Features

- **Send & Edit Messages**: Send, edit, delete, forward, pin/unpin messages with Markdown/HTML formatting
- **Send Summaries**: Send formatted work summaries with status indicators (✅ ⚠️ ❌ ℹ️)
- **Receive Messages**: Poll for new messages and commands from Telegram
- **Reactions & Polls**: Add emoji reactions, create polls
- **Chat Info**: Get chat details, member info, member counts
- **Background Polling**: Separate polling service captures messages when the MCP server isn't running
- **Retry Logic**: Automatic retry with exponential backoff for rate limits and network errors
- **Input Validation**: Robust validation of chat IDs, user IDs, and usernames

---

## Quick Start

### 1. Create a Telegram Bot

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` and follow the prompts
3. Copy the bot token (e.g., `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

### 2. Get Your Chat ID

1. Start a chat with your new bot (send `/start`)
2. Message `@userinfobot` on Telegram
3. It will reply with your user ID - this is your chat ID

### 3. Install

```bash
cd telegram_mcp_server

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install package
pip install -e .

# Copy and configure environment
cp .env.example .env
# Edit .env with your TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
```

### 4. Configure Your MCP Client

#### OpenCode (`~/.config/opencode/config.toml`)

```toml
[mcp.servers.telegram]
command = "/path/to/telegram_mcp_server/.venv/bin/telegram-mcp-server"

[mcp.servers.telegram.env]
TELEGRAM_BOT_TOKEN = "your_bot_token_here"
TELEGRAM_CHAT_ID = "your_chat_id_here"
```

#### Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "telegram-mcp": {
      "command": "telegram-mcp-server",
      "args": [],
      "env": {
        "TELEGRAM_BOT_TOKEN": "YOUR_BOT_TOKEN_HERE",
        "TELEGRAM_CHAT_ID": "YOUR_CHAT_ID_HERE"
      }
    }
  }
}
```

---

## Available Tools (16 total)

### Messaging

| Tool | Description |
|------|-------------|
| `telegram_send_message` | Send a message with Markdown/HTML formatting |
| `telegram_send_summary` | Send formatted work summary with status emoji |
| `telegram_receive_messages` | Poll for new messages |
| `telegram_reply_message` | Reply to a specific message |
| `telegram_edit_message` | Edit a previously sent message |
| `telegram_delete_message` | Delete a message |
| `telegram_forward_message` | Forward a message to another chat |
| `telegram_pin_message` | Pin a message in a chat |
| `telegram_unpin_message` | Unpin a message |
| `telegram_send_reaction` | Add emoji reaction to a message |
| `telegram_send_poll` | Create a poll (2-10 options) |

### Chat & Info

| Tool | Description |
|------|-------------|
| `telegram_get_chat_info` | Get detailed chat information |
| `telegram_get_chat_member` | Get info about a specific chat member |
| `telegram_get_chat_member_count` | Get number of members in a chat |
| `telegram_set_typing` | Send typing indicator |
| `telegram_get_bot_info` | Get information about the bot |

### Queue (for background polling)

| Tool | Description |
|------|-------------|
| `telegram_get_queued_messages` | Get messages captured by polling service |

---

## Tool Examples

### Send a Message

```
telegram_send_message(
    message="Hello from OpenCode! 🚀",
    chat_id="123456789",
    parse_mode="Markdown"
)
```

### Send a Work Summary

```
telegram_send_summary(
    title="Build Completed",
    summary="All 42 tests passed.\nCoverage: 87%",
    status="success"
)
```

### Create a Poll

```
telegram_send_poll(
    chat_id="123456789",
    question="Should we deploy to production?",
    options=["Yes, deploy now", "Wait until tomorrow", "Need more testing"]
)
```

### Add a Reaction

```
telegram_send_reaction(
    chat_id="123456789",
    message_id=42,
    emoji="👍"
)
```

---

## Background Polling Service

The polling service runs separately to capture messages even when the MCP server isn't running.

### Run Manually

```bash
telegram-mcp-polling
```

### Run as Systemd Service

```bash
# Copy the service file
sudo cp telegram-mcp-polling@.service /etc/systemd/system/

# Enable and start
sudo systemctl enable telegram-mcp-polling@$USER
sudo systemctl start telegram-mcp-polling@$USER

# Check status
sudo systemctl status telegram-mcp-polling@$USER
```

Messages are stored in `~/.local/share/telegram_mcp_server/message_queue.json` and can be retrieved with `telegram_get_queued_messages`.

---

## Docker Deployment

### Build and Run

```bash
# Build image
docker build -t telegram-mcp-server .

# Run with environment variables
docker run -it --rm \
  -e TELEGRAM_BOT_TOKEN="your_token" \
  -e TELEGRAM_CHAT_ID="your_chat_id" \
  telegram-mcp-server
```

### Docker Compose

```bash
# Create .env file with credentials
cp .env.example .env
# Edit .env

# Run both server and polling service
docker compose up -d
```

---

## Configuration

| Environment Variable | Required | Default | Description |
|---------------------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | - | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Yes | - | Default chat ID for messages |
| `TELEGRAM_API_BASE_URL` | No | `https://api.telegram.org` | Custom API endpoint |
| `TELEGRAM_POLLING_TIMEOUT` | No | `30` | Long polling timeout (seconds) |
| `TELEGRAM_QUEUE_DIR` | No | `~/.local/share/telegram_mcp_server` | Queue file directory |

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run linter
ruff check src/ tests/

# Run formatter
ruff format src/ tests/

# Run tests
pytest tests/ -v

# Run tests with coverage
pytest tests/ -v --cov=src --cov-report=term
```

---

## Project Structure

```
telegram_mcp_server/
├── src/
│   ├── telegram_mcp_server/
│   │   ├── __init__.py
│   │   ├── server.py          # FastMCP server with all tools
│   │   ├── telegram_client.py # Telegram Bot API client with retry
│   │   ├── config.py          # Pydantic settings
│   │   ├── errors.py          # Error handling utilities
│   │   └── validation.py      # Input validation decorators
│   └── telegram_polling_service/
│       ├── __init__.py
│       └── polling_service.py # Background message polling
├── tests/
│   ├── conftest.py
│   └── test_validation.py
├── .github/workflows/
│   ├── lint.yml
│   └── test.yml
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

---

## License

MIT License - see [LICENSE](LICENSE) file.
