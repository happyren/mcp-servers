# Telegram MCP Server - Complete Setup Guide

This guide is designed to be read by an AI agent to configure the Telegram MCP Server for OpenCode. Follow each section in order.

---

## Prerequisites

Before starting, the user must provide:
1. **Telegram Bot Token** - Obtained from @BotFather on Telegram
2. **Chat ID** - The user's Telegram user ID (obtained from @userinfobot)
3. **OpenCode v1.1.56+** - Ensure OpenCode is installed and updated to at least version 1.1.56

### OpenCode Version Compatibility
- **v1.1.56+**: Uses `opencode serve` command for headless HTTP server mode
- **Older versions**: May require patching the `OpenCodeInstanceFactory` to use deprecated `--http-port` flag

Check your OpenCode version:
```bash
opencode --version
```

Update OpenCode if needed:
```bash
opencode upgrade
```

If the user hasn't created a bot yet, instruct them to:
1. Open Telegram and search for `@BotFather`
2. Send `/newbot` and follow the prompts to create a bot
3. Copy the bot token (format: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)
4. Start a chat with their new bot (send `/start`)
5. Message `@userinfobot` to get their user ID

---

## Step 1: Install the Telegram MCP Server

```bash
cd /path/to/telegram_mcp_server

# Create virtual environment with Python 3.10+
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install the package
pip install -e .
```

Verify installation:
```bash
# Should print help without errors
.venv/bin/telegram-mcp-server --help
```

---

## Step 2: Create Credential Files

Store credentials securely in separate files:

```bash
# Create config directory if needed
mkdir -p ~/.config/opencode

# Create the bot token file
echo -n "<USER_BOT_TOKEN>" > ~/.config/opencode/.telegram_bot_token
chmod 600 ~/.config/opencode/.telegram_bot_token

# Create the chat ID file  
echo -n "<USER_CHAT_ID>" > ~/.config/opencode/.telegram_chat_id
chmod 600 ~/.config/opencode/.telegram_chat_id
```

Replace `<USER_BOT_TOKEN>` and `<USER_CHAT_ID>` with the user's actual values.

---

## Step 3: Configure OpenCode

Edit `~/.config/opencode/opencode.jsonc` (create if it doesn't exist):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "telegram": {
      "type": "local",
      "command": [
        "<ABSOLUTE_PATH_TO_VENV>/bin/telegram-mcp-server",
        "--enable-polling",
        "--enable-bridge"
      ],
      "environment": {
        "TELEGRAM_BOT_TOKEN": "{file:~/.config/opencode/.telegram_bot_token}",
        "TELEGRAM_CHAT_ID": "{file:~/.config/opencode/.telegram_chat_id}"
      },
      "enabled": true
    }
  }
}
```

Replace `<ABSOLUTE_PATH_TO_VENV>` with the full path to the virtual environment (e.g., `/Users/username/mcp-servers/telegram_mcp_server/.venv`).

### Configuration Options

The `--enable-polling` and `--enable-bridge` flags enable:
- **Polling**: Automatically captures incoming Telegram messages in background
- **Bridge**: Forwards messages to OpenCode and sends AI responses back to Telegram

Additional optional flags:
- `--opencode-url http://localhost:4096` - OpenCode HTTP API URL (default: localhost:4096)
- `--no-reply` - Disable sending responses back to Telegram
- `--provider <id>` - AI provider ID (default: deepseek)
- `--model <id>` - AI model ID (default: deepseek-reasoner)

If the config file already exists with other settings, merge the `mcp` section into the existing config.

---

## Step 4: Verify Installation

1. **Restart OpenCode** to load the new MCP server

2. **Test sending a message**:
   ```
   Use the telegram_send_message tool to send "Hello from OpenCode!" to my chat
   ```

3. **Test receiving messages**:
   - Send a message to your bot from Telegram
   - The message should appear automatically in OpenCode as a prompt
   - OpenCode will respond and send the response back to Telegram

4. **Verify tools are available**:
   The agent should have access to these Telegram tools:
   - telegram_send_message
   - telegram_send_summary
   - telegram_receive_messages
   - telegram_reply_message
   - telegram_edit_message
   - telegram_delete_message
   - telegram_forward_message
   - telegram_pin_message
   - telegram_unpin_message
   - telegram_send_reaction
   - telegram_send_poll
   - telegram_get_chat_info
   - telegram_get_chat_member
    - telegram_get_chat_member_count
     - telegram_set_typing
     - telegram_get_bot_info
     - telegram_get_queued_messages
      - telegram_set_bot_commands
      - telegram_delete_bot_commands
      - telegram_get_bot_commands

## Step 5: Register Bot Commands (Automatic)

For the best user experience, register the bot commands with Telegram so they appear in the command menu when users type `/`. This can be done automatically by the AI agent:

### Option A: Using the MCP Tool (Recommended for AI Agents)
```
Use the telegram_set_bot_commands tool to register all available commands:
telegram_set_bot_commands(scope_type="default", language_code="")
```

### Option B: Using the Utility Script
```bash
# Activate the virtual environment
source .venv/bin/activate

# Run the command registration script
python set_commands.py
```

### What This Does
- Registers all 35+ slash commands (from `/help` to `/pending`) with Telegram
- Commands will appear in Telegram's command menu when users type `/`
- Uses the "default" scope (available to all users)
- Can be customized with different scopes (private chats, groups, specific chats)

### Verification
After registration:
1. Open Telegram and start typing `/` in the bot chat
2. You should see a list of available commands (e.g., `/help`, `/sessions`, `/files`)
3. Use `/commands` in Telegram to see the full list

## Telegram Commands

With the bridge service enabled, you can control OpenCode through Telegram using slash commands. Send these commands to your Telegram bot:

### Quick Start

1. **List sessions**: Send `/sessions` to see all OpenCode sessions
2. **Send prompts**: Just type any text (not starting with `/`) to send as a prompt
3. **Create session**: Send `/session` to create a new session
4. **Get help**: Send `/help` to see all available commands

### Common Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all available commands |
| `/sessions` | List all sessions with status |
| `/use <id>` | Switch to a specific session |
| `/prompt <message>` | Send a prompt to current session |
| `/files [path]` | List files in a directory |
| `/read <path>` | Read a file's contents |
| `/shell <command>` | Execute a shell command |

### Full Command List

See [README.md](README.md#telegram-commands) for the complete list of 35+ Telegram commands including:
- Project & file management
- Session operations (create, fork, abort, delete, share, revert, summarize)
- Configuration (models, agents, auth)
- System status (health, LSP, formatters, MCP)

### Interactive Inline Keyboards

The bridge uses **Telegram Inline Keyboards** for interactive prompts:

| Interaction | How to respond |
|-------------|----------------|
| **Session list** (`/sessions`) | Tap a session button to switch |
| **Model picker** (`/models`) | Tap a model button to select |
| **Permission requests** | Tap Allow, Always, or Reject |
| **Question prompts** | Tap one of the option buttons |

**Important:**
- When buttons are shown, you **must click a button** to respond
- Typing a text reply will NOT work for button prompts
- After clicking, the message updates to show your selection

---

## Troubleshooting

### "Bot token invalid"
- Verify the token is correct in the credential files
- Ensure no extra whitespace or newlines in the token file

### "Chat not found"
- Make sure the user has sent `/start` to the bot
- Verify the chat ID is correct

### Messages not being received
- Check OpenCode logs for polling/bridge errors
- Ensure OpenCode was started with `--port 4096` or the URL matches your config
- Check the queue file: `cat ~/.local/share/telegram_mcp_server/message_queue.json`

### "Already running asyncio" error
- This indicates an outdated version of the server
- Pull the latest code and reinstall: `pip install -e .`

---

## Configuration Summary

After setup, you should have:

| File | Purpose |
|------|---------|
| `~/.config/opencode/.telegram_bot_token` | Secure bot token storage |
| `~/.config/opencode/.telegram_chat_id` | Secure chat ID storage |
| `~/.config/opencode/opencode.jsonc` | MCP server configuration |
| `~/.local/share/telegram_mcp_server/message_queue.json` | Queued messages (auto-created) |

---

## Quick Reference

### Send a message (in OpenCode)
```
telegram_send_message(chat_id="<CHAT_ID>", message="Hello!")
```

### Check for new messages (in OpenCode)
```
telegram_get_queued_messages(clear_after_read=false)
```

### Send a summary (in OpenCode)
```
telegram_send_summary(title="Task Done", summary="Details here", status="success")
```

---

## How It Works

With `--enable-polling` and `--enable-bridge`, the MCP server runs everything in a single process:

```
┌─────────────────────────────────────────────┐
│  telegram-mcp-server                        │
│  --enable-polling --enable-bridge           │
│                                             │
│  ┌───────────────┐ ┌───────────────┐        │
│  │ polling thread│ │ bridge thread │        │
│  │  (captures    │ │  (forwards to │        │
│  │   messages)   │ │   OpenCode)   │        │
│  └───────────────┘ └───────────────┘        │
│                                             │
│  ┌──────────────────────────────────┐       │
│  │     MCP Tools (17 tools)         │       │
│  └──────────────────────────────────┘       │
└─────────────────────────────────────────────┘
         │                    │
         ▼                    ▼
   Telegram Bot           OpenCode
      API                HTTP API
```

1. **Polling thread**: Continuously polls Telegram for new messages and stores them in a queue file
2. **Bridge thread**: Watches the queue and forwards messages to OpenCode's HTTP API
3. **MCP Tools**: Provide direct Telegram functionality to the AI agent

No separate processes or plugins needed!

---

## Advanced: Telegram Controller (Multi-Instance Management)

For advanced workflows requiring **multiple OpenCode instances** (e.g., different projects per reply thread), use the **Telegram Controller**:

```bash
# Install (already included in package)
pip install -e .

# Start the controller daemon
telegram-controller
```

### Key Differences

| Feature | MCP Server | Controller |
|---------|------------|------------|
| **Instance Management** | Single OpenCode instance | Multiple instances (one per thread/project) |
| **Thread Mapping** | Not applicable | Maps each reply thread to a project |
| **Commands** | Standard MCP tools | Controller commands (`/open`, `/list`, `/switch`) |
| **Setup** | Configure in OpenCode config | Run as standalone daemon |
| **Use Case** | Simple bot integration | Team/project workflows with forum topics |

### Migration

To switch from MCP server to controller:
1. Stop any running `telegram-mcp-server` processes
2. Start `telegram-controller` with same credentials
3. Use `/open <path>` in reply threads to attach projects

The controller provides a more flexible architecture for complex workflows while maintaining all Telegram integration features.
