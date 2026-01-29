# Telegram MCP Server

An MCP (Model Context Protocol) server that connects to the Telegram Bot API, allowing AI agents like OpenCode to send messages, summaries, and receive commands via Telegram.

## Features

- **Send Messages**: Send text messages to Telegram chats with Markdown/HTML formatting
- **Send Summaries**: Send formatted work summaries with status indicators
- **Receive Messages**: Poll for new messages and commands from Telegram
- **Reply to Messages**: Reply to specific messages in conversations
- **Bot Info**: Get information about the configured bot

## Setup

### 1. Create a Telegram Bot

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` and follow the prompts to create your bot
3. Copy the bot token provided (looks like `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

### 2. Get Your Chat ID

1. Start a chat with your new bot (send `/start`)
2. Search for `@userinfobot` on Telegram and start a chat
3. It will reply with your user ID - this is your chat ID

### 3. Install the Server

```bash
cd telegram_mcp_server

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install the package
pip install -e .
```

### 4. Configure Environment

```bash
# Copy the example env file
cp .env.example .env

# Edit .env with your credentials
# TELEGRAM_BOT_TOKEN=your_bot_token_here
# TELEGRAM_CHAT_ID=your_chat_id_here
```

### 5. Configure OpenCode

Add the server to your OpenCode configuration. Create or edit `~/.config/opencode/config.toml`:

```toml
[mcp.servers.telegram]
command = "/path/to/telegram_mcp_server/.venv/bin/telegram-mcp-server"

[mcp.servers.telegram.env]
TELEGRAM_BOT_TOKEN = "your_bot_token_here"
TELEGRAM_CHAT_ID = "your_chat_id_here"
```

Or if using JSON config (`~/.config/opencode/config.json`):

```json
{
  "mcp": {
    "servers": {
      "telegram": {
        "command": "/path/to/telegram_mcp_server/.venv/bin/telegram-mcp-server",
        "env": {
          "TELEGRAM_BOT_TOKEN": "your_bot_token_here",
          "TELEGRAM_CHAT_ID": "your_chat_id_here"
        }
      }
    }
  }
}
```

## Available Tools

### telegram_send_message

Send a message to a Telegram chat.

**Parameters:**
- `message` (required): The message text (supports Markdown)
- `chat_id` (optional): Target chat ID (defaults to configured ID)
- `parse_mode` (optional): "Markdown", "HTML", or "None"

### telegram_send_summary

Send a formatted work summary.

**Parameters:**
- `title` (required): Summary title
- `summary` (required): Summary content
- `status` (optional): "success", "warning", "error", or "info"
- `chat_id` (optional): Target chat ID

### telegram_receive_messages

Check for new messages from Telegram.

**Parameters:**
- `timeout` (optional): Polling timeout in seconds (default: 5)
- `from_user_id` (optional): Filter messages by user ID

### telegram_reply_message

Reply to a specific message.

**Parameters:**
- `chat_id` (required): Chat ID
- `message_id` (required): Message ID to reply to
- `text` (required): Reply text

### telegram_get_bot_info

Get information about the bot (no parameters).

## Usage Examples

### Sending a Work Summary

Ask OpenCode:
> "Send a summary of what we just did to Telegram"

The agent will use `telegram_send_summary` with appropriate title and content.

### Receiving Commands

Ask OpenCode:
> "Check Telegram for any new messages or commands"

The agent will use `telegram_receive_messages` and can act on instructions received.

### Two-Way Communication

1. Send a message from Telegram to your bot: "Please run the tests"
2. Ask OpenCode: "Check Telegram for commands and execute them"
3. OpenCode will receive the message, run tests, and can send results back

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run directly
python -m telegram_mcp_server.server
```

## License

MIT
