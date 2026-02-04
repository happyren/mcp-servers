# Telegram MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An MCP (Model Context Protocol) server that connects to the Telegram Bot API, allowing AI agents like OpenCode and Claude to send messages, summaries, and receive commands via Telegram.

## Features

- **Send & Edit Messages**: Send, edit, delete, forward, pin/unpin messages with Markdown/HTML formatting
- **Send Summaries**: Send formatted work summaries with status indicators (✅ ⚠️ ❌ ℹ️)
- **Receive Messages**: Poll for new messages and commands from Telegram
- **Reactions & Polls**: Add emoji reactions, create polls
- **Chat Info**: Get chat details, member info, member counts
- **Integrated Services**: Built-in polling and OpenCode bridge - no separate processes needed!
- **Background Polling**: Captures messages even when the AI agent isn't actively listening
- **Two-Way Bridge**: Forwards Telegram messages to OpenCode and sends responses back
- **Retry Logic**: Automatic retry with exponential backoff for rate limits and network errors
- **Input Validation**: Robust validation of chat IDs, user IDs, and usernames

---

## Agent-Friendly Setup

**For AI agents**: Read [SETUP.md](SETUP.md) for complete step-by-step configuration instructions. The setup guide is designed to be followed by an agent to configure everything automatically (except obtaining the bot token and chat ID from the user).

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

**Basic Setup (MCP server only):**
```toml
[mcp.servers.telegram]
command = "/path/to/telegram_mcp_server/.venv/bin/telegram-mcp-server"

[mcp.servers.telegram.env]
TELEGRAM_BOT_TOKEN = "your_bot_token_here"
TELEGRAM_CHAT_ID = "your_chat_id_here"
```

**Full Integration (with polling and bridge):**
```toml
[mcp.servers.telegram]
command = "/path/to/telegram_mcp_server/.venv/bin/telegram-mcp-server"
args = ["--enable-polling", "--enable-bridge"]

[mcp.servers.telegram.env]
TELEGRAM_BOT_TOKEN = "your_bot_token_here"
TELEGRAM_CHAT_ID = "your_chat_id_here"
```

This enables:
- **Polling**: Automatically captures incoming Telegram messages in background
- **Bridge**: Forwards messages to OpenCode and sends AI responses back to Telegram

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

## Available Tools (20 total)

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
| `telegram_check_new` | Check for new messages in the queue (returns new messages since last check) |

### Bot Command Management

| Tool | Description |
|------|-------------|
| `telegram_set_bot_commands` | Register bot commands with Telegram (makes commands appear in `/` menu) |
| `telegram_delete_bot_commands` | Delete bot commands for a given scope and language |
| `telegram_get_bot_commands` | Get currently registered bot commands from Telegram |

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

## Telegram Commands

When the bridge service is enabled (`--enable-bridge`), you can control OpenCode through Telegram using slash commands. Send these commands to your Telegram bot:

### Core Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all available commands |
| `/sessions` | List all OpenCode sessions |
| `/session [model]` | Create a new session (optional model) |
| `/use <id>` | Switch to a specific session |
| `/status` | Get status of all sessions |
| `/prompt <message>` | Send a prompt to current session |
| `/shell <command>` | Execute a shell command |

### File & Project Commands

| Command | Description |
|---------|-------------|
| `/directory [path]` | View or set working directory |
| `/files [path]` | List files in directory |
| `/read <path>` | Read file content |
| `/find <pattern>` | Search for text in files |
| `/findfile <query>` | Find files by name |
| `/find-symbol <query>` | Find workspace symbols (alias: `/find_symbol`) |
| `/projects` | List all projects |
| `/project` | Get current project info |
| `/vcs` | Get VCS (git) info |

### Session Management Commands

| Command | Description |
|---------|-------------|
| `/diff [session_id]` | Get session diff |
| `/todo [session_id]` | Get todo list for session |
| `/fork <session_id>` | Fork an existing session |
| `/abort <session_id>` | Abort a running session |
| `/delete <session_id>` | Delete a session |
| `/share <session_id>` | Share a session |
| `/unshare <session_id>` | Unshare a session |
| `/revert <message_id>` | Revert a message |
| `/unrevert [session_id]` | Restore reverted messages |
| `/summarize [session_id]` | Summarize session |
| `/info [session_id]` | Get session details |
| `/messages [session_id]` | List messages in session |
| `/init` | Analyze app and create AGENTS.md |

### Configuration Commands

| Command | Description |
|---------|-------------|
| `/config` | Get current config |
| `/models [model]` | List models (tap to select) or set model |
| `/agents` | List available agents |
| `/login <provider>` | Authenticate with a provider |
| `/commands` | List all OpenCode slash commands |
| `/pending` | Show pending questions & permissions |

### System Commands

| Command | Description |
|---------|-------------|
| `/health` | Check OpenCode server health |
| `/lsp` | Get LSP server status |
| `/formatter` | Get formatter status |
| `/mcp` | Get MCP server status |
| `/dispose` | Dispose current instance |

**Note**: Any message not starting with `/` is sent as a prompt to the current session.

### Interactive Inline Keyboards

Several commands and interactions use **Telegram Inline Keyboards** for a better user experience:

| Interaction | Description |
|-------------|-------------|
| **Session Selection** | `/sessions` shows clickable buttons for each session - tap to switch |
| **Model Selection** | `/models` shows favourite models as buttons - tap to select |
| **Permission Requests** | When OpenCode needs permission, buttons appear: Allow / Always / Reject |
| **Question Prompts** | When OpenCode asks questions, options appear as clickable buttons |

**How it works:**
- Inline keyboards are sent **once** per interaction - no duplicates
- Users **must click a button** to respond (text input is not accepted for keyboard prompts)
- After clicking, the original message updates to show the result
- A brief toast notification confirms the action

### Bot Command Registration

To make commands appear in Telegram's command menu (when users type `/`), register them using:

```bash
# Using the utility script
python set_commands.py

# Or via MCP tool in OpenCode
telegram_set_bot_commands(scope_type="default", language_code="")
```

This registers all 38+ commands with Telegram. Commands can be managed per scope (private chats, groups, specific chats) and language.

---

## Background Polling Service

The MCP server now has **integrated polling and bridge services**. You can enable them with command-line flags:

### Integrated Mode (Recommended)

```bash
# Enable polling only (captures messages in background)
telegram-mcp-server --enable-polling

# Enable both polling and bridge (full two-way communication)
telegram-mcp-server --enable-polling --enable-bridge

# With custom OpenCode URL
telegram-mcp-server --enable-polling --enable-bridge --opencode-url http://localhost:8080

# Disable replies (one-way: Telegram -> OpenCode only)
telegram-mcp-server --enable-polling --enable-bridge --no-reply
```

### CLI Options

| Option | Environment Variable | Default | Description |
|--------|---------------------|---------|-------------|
| `--enable-polling` | `TELEGRAM_ENABLE_POLLING` | false | Enable background message polling |
| `--enable-bridge` | `TELEGRAM_ENABLE_BRIDGE` | false | Enable OpenCode bridge |
| `--opencode-url` | `TELEGRAM_OPENCODE_URL` | `http://localhost:4096` | OpenCode HTTP API URL |
| `--no-reply` | `TELEGRAM_NO_REPLY` | false | Disable sending responses back |
| `--provider` | `TELEGRAM_PROVIDER` | `deepseek` | AI provider ID |
| `--model` | `TELEGRAM_MODEL` | `deepseek-reasoner` | AI model ID |
| `--verbose` | - | false | Enable debug logging |

Messages are stored in `~/.local/share/telegram_mcp_server/message_queue.json` and can be retrieved with `telegram_get_queued_messages`.

---

## Telegram Controller (Multi-Instance Management)

The **Telegram Controller** is a standalone daemon that enables **multi-instance OpenCode management** via Telegram. It allows you to:

- **Run multiple OpenCode instances** for different projects simultaneously
- **Map each reply thread** to a different project instance
- **Spawn instances on-demand** with `/open <path>` command
- **Switch between instances** with `/list` and `/switch`
- **Receive notifications** for pending permissions and questions
- **Support forum topics** in Telegram supergroups

### Installation

The controller is included in the package. After installing the Telegram MCP Server, you can run:

```bash
# Start the controller daemon
telegram-controller

# Start with custom state directory
telegram-controller --state-dir ~/.telegram-controller

# Start with specific default model
telegram-controller --provider deepseek --model deepseek-reasoner
```

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | - | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Yes | - | Default chat ID for notifications |
| `TELEGRAM_PROVIDER` | No | `deepseek` | Default AI provider for new instances |
| `TELEGRAM_MODEL` | No | `deepseek-reasoner` | Default AI model for new instances |
| `TELEGRAM_FAVOURITE_MODELS` | No | See below | Comma-separated list of favourite models |

### Controller Commands

| Command | Description |
|---------|-------------|
| `/open <path>` | Open project directory in current thread |
| `/list` | List all running instances (tap to switch) |
| `/switch [id]` | Switch to different instance |
| `/current` | Show current instance details |
| `/close` | Stop current instance |
| `/kill <id>` | Stop specific instance |
| `/restart <id>` | Restart an instance |
| `/status` | Instance status overview |
| `/threads` | List thread-instance mappings |
| `/help` | Show controller help |

### Usage Example

1. **Start the controller**: `telegram-controller`
2. **In Telegram**, reply to any message (creates a thread)
3. **Send `/open ~/projects/my-app`** - spawns OpenCode instance for that project
4. **Send any message** - forwarded to that instance
5. **Create another thread**, `/open ~/projects/another` - second instance

Each reply thread can be connected to a different project!

### State Management

The controller stores state in `~/.local/share/telegram_controller/`:
- `instances.json` - Running instance metadata
- `session_routes.json` - Thread-instance mappings
- `polling_offset.json` - Telegram polling offset

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
| `TELEGRAM_ENABLE_POLLING` | No | `false` | Enable integrated polling |
| `TELEGRAM_ENABLE_BRIDGE` | No | `false` | Enable integrated bridge |
| `TELEGRAM_OPENCODE_URL` | No | `http://localhost:4096` | OpenCode HTTP API URL |
| `TELEGRAM_NO_REPLY` | No | `false` | Disable sending replies to Telegram |
| `TELEGRAM_PROVIDER` | No | `deepseek` | AI provider for bridge |
| `TELEGRAM_MODEL` | No | `deepseek-reasoner` | AI model for bridge |

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
│   │   ├── server.py          # FastMCP server with integrated services
│   │   ├── telegram_client.py # Telegram Bot API client with retry
│   │   ├── config.py          # Pydantic settings
│   │   ├── errors.py          # Error handling utilities
│   │   └── validation.py      # Input validation decorators
│   ├── telegram_polling_service/
│   │   ├── __init__.py
│   │   └── polling_service.py # Background message polling
│   ├── telegram_bridge/
│   │   ├── __init__.py
│   │   └── bridge_service.py  # OpenCode bridge service
│   └── telegram_controller/
│       ├── __init__.py
│       ├── controller.py      # Main controller daemon
│       ├── instance.py        # OpenCode instance representation
│       ├── process_manager.py # Instance lifecycle management
│       └── session_router.py  # Thread-instance routing
├── tests/
│   ├── conftest.py
│   └── test_validation.py
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── SETUP.md                   # Agent-friendly setup guide
└── README.md
```

---

## License

MIT License - see [LICENSE](LICENSE) file.

---

## Repository

[https://github.com/happyren/mcp-servers](https://github.com/happyren/mcp-servers)
