# Telegram MCP Server - Complete Setup Guide

This guide is designed to be read by an AI agent to configure the Telegram MCP Server for OpenCode. Follow each section in order.

---

## Prerequisites

Before starting, the user must provide:
1. **Telegram Bot Token** - Obtained from @BotFather on Telegram
2. **Chat ID** - The user's Telegram user ID (obtained from @userinfobot)

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
# Should show version info without errors
.venv/bin/python -c "from telegram_mcp_server import main; print('OK')"
```

---

## Step 2: Create Environment File

Create a `.env` file in the project root:

```bash
cat > .env << 'EOF'
# Telegram MCP Server Configuration
TELEGRAM_BOT_TOKEN=<USER_BOT_TOKEN>
TELEGRAM_CHAT_ID=<USER_CHAT_ID>
TELEGRAM_API_BASE_URL=https://api.telegram.org
TELEGRAM_POLLING_TIMEOUT=30
TELEGRAM_QUEUE_DIR=~/.local/share/telegram_mcp_server
EOF
```

Replace `<USER_BOT_TOKEN>` and `<USER_CHAT_ID>` with the user's actual values.

---

## Step 3: Configure OpenCode MCP Integration

### 3.1 Create Credential Files

Store credentials securely in separate files:

```bash
# Create the bot token file
echo -n "<USER_BOT_TOKEN>" > ~/.config/opencode/.telegram_bot_token
chmod 600 ~/.config/opencode/.telegram_bot_token

# Create the chat ID file  
echo -n "<USER_CHAT_ID>" > ~/.config/opencode/.telegram_chat_id
chmod 600 ~/.config/opencode/.telegram_chat_id
```

### 3.2 Add MCP Server to OpenCode Config

Edit `~/.config/opencode/opencode.jsonc` (create if it doesn't exist):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "telegram": {
      "type": "local",
      "command": ["<ABSOLUTE_PATH_TO_VENV>/bin/telegram-mcp-server"],
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

If the config file already exists with other settings, merge the `mcp` section into the existing config.

---

## Step 4: Set Up Background Polling Service

The polling service captures messages when OpenCode isn't running.

### 4.1 Start Polling Service Manually

```bash
# In a separate terminal
source /path/to/telegram_mcp_server/.venv/bin/activate
telegram-mcp-polling
```

### 4.2 (Optional) Run as Systemd Service (Linux)

```bash
# Copy service file
sudo cp /path/to/telegram_mcp_server/telegram-mcp-polling@.service /etc/systemd/system/

# Enable and start for current user
sudo systemctl enable telegram-mcp-polling@$USER
sudo systemctl start telegram-mcp-polling@$USER
```

---

## Step 5: Install Message Watcher Plugin (Recommended)

The watcher plugin provides notifications when new Telegram messages arrive.

### 5.1 Create Plugin Directory

```bash
mkdir -p ~/.config/opencode/plugins
```

### 5.2 Create Plugin File

Create `~/.config/opencode/plugins/telegram-watcher.ts`:

```typescript
/**
 * Telegram Message Watcher Plugin for OpenCode
 */

import type { Plugin } from "@opencode-ai/plugin"
import { watch, existsSync, readFileSync } from "fs"
import { homedir } from "os"
import { join } from "path"

const QUEUE_DIR = process.env.TELEGRAM_QUEUE_DIR || join(homedir(), ".local/share/telegram_mcp_server")
const QUEUE_FILE = join(QUEUE_DIR, "message_queue.json")
const DEBOUNCE_MS = 1000

interface TelegramMessage {
  message_id: number
  chat_id: number
  from_user_id: number
  from_username: string
  text: string
  date: number
  received_at: string
}

export const TelegramWatcherPlugin: Plugin = async ({ client }) => {
  let lastMessageCount = 0
  let debounceTimer: ReturnType<typeof setTimeout> | null = null
  let isProcessing = false

  const getMessageCount = (): number => {
    try {
      if (!existsSync(QUEUE_FILE)) return 0
      const content = readFileSync(QUEUE_FILE, "utf-8")
      const messages = JSON.parse(content) as TelegramMessage[]
      return messages.length
    } catch {
      return 0
    }
  }

  const getNewMessages = (previousCount: number): TelegramMessage[] => {
    try {
      if (!existsSync(QUEUE_FILE)) return []
      const content = readFileSync(QUEUE_FILE, "utf-8")
      const messages = JSON.parse(content) as TelegramMessage[]
      if (messages.length > previousCount) {
        return messages.slice(previousCount)
      }
      return []
    } catch {
      return []
    }
  }

  lastMessageCount = getMessageCount()

  await client.app.log({
    service: "telegram-watcher",
    level: "info",
    message: `Telegram watcher initialized. Queue file: ${QUEUE_FILE}. Current messages: ${lastMessageCount}`,
  })

  if (existsSync(QUEUE_FILE)) {
    watch(QUEUE_FILE, (eventType) => {
      if (eventType !== "change" || isProcessing) return

      if (debounceTimer) clearTimeout(debounceTimer)
      
      debounceTimer = setTimeout(async () => {
        const currentCount = getMessageCount()
        
        if (currentCount > lastMessageCount) {
          isProcessing = true
          const newMessages = getNewMessages(lastMessageCount)
          
          if (newMessages.length > 0) {
            const summary = newMessages
              .map(m => `@${m.from_username || m.from_user_id}: ${m.text?.substring(0, 100) || "[no text]"}`)
              .join("\n")

            await client.app.log({
              service: "telegram-watcher",
              level: "info",
              message: `New Telegram messages received (${newMessages.length})`,
              extra: { count: newMessages.length, messages: newMessages },
            })

            try {
              const { exec } = await import("child_process")
              const notificationText = `${newMessages.length} new Telegram message(s)`
              exec(`osascript -e 'display notification "${summary.replace(/"/g, '\\"').substring(0, 200)}" with title "Telegram" subtitle "${notificationText}"'`)
            } catch {}
          }
          
          lastMessageCount = currentCount
          isProcessing = false
        }
      }, DEBOUNCE_MS)
    })

    await client.app.log({
      service: "telegram-watcher",
      level: "info", 
      message: "File watcher started for Telegram queue",
    })
  } else {
    await client.app.log({
      service: "telegram-watcher",
      level: "warn",
      message: `Queue file not found: ${QUEUE_FILE}. Start the polling service first.`,
    })
  }

  return {
    event: async ({ event }) => {
      if (event.type === "session.idle") {
        const currentCount = getMessageCount()
        if (currentCount > 0) {
          await client.app.log({
            service: "telegram-watcher",
            level: "info",
            message: `Session idle. ${currentCount} message(s) in Telegram queue.`,
          })
        }
      }
    },
  }
}
```

### 5.3 Ensure Plugin Dependencies

Check that `~/.config/opencode/package.json` exists with:

```json
{
  "dependencies": {
    "@opencode-ai/plugin": "^1.1.0"
  }
}
```

If it doesn't exist, create it. OpenCode will install dependencies automatically.

---

## Step 6: Verify Installation

1. **Restart OpenCode** to load the new MCP server and plugin

2. **Test sending a message**:
   ```
   Use the telegram_send_message tool to send "Hello from OpenCode!" to my chat
   ```

3. **Test receiving messages**:
   - Send a message to your bot from Telegram
   - Check with: `Use telegram_get_queued_messages to see pending messages`

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

---

## Troubleshooting

### "Bot token invalid"
- Verify the token is correct in the credential files
- Ensure no extra whitespace or newlines in the token file

### "Chat not found"
- Make sure the user has sent `/start` to the bot
- Verify the chat ID is correct

### Messages not being received
- Ensure the polling service is running: `ps aux | grep telegram-mcp-polling`
- Check the queue file: `cat ~/.local/share/telegram_mcp_server/message_queue.json`

### Plugin not loading
- Check OpenCode logs for plugin errors
- Verify the plugin file has no syntax errors
- Ensure dependencies are installed: check `~/.config/opencode/package.json`

### "Already running asyncio" error
- This indicates an outdated version of the server
- Pull the latest code and reinstall: `pip install -e .`

---

## Configuration Summary

After setup, you should have:

| File | Purpose |
|------|---------|
| `/path/to/telegram_mcp_server/.env` | Local development credentials |
| `~/.config/opencode/.telegram_bot_token` | Secure bot token storage |
| `~/.config/opencode/.telegram_chat_id` | Secure chat ID storage |
| `~/.config/opencode/opencode.jsonc` | MCP server configuration |
| `~/.config/opencode/plugins/telegram-watcher.ts` | Message notification plugin |
| `~/.local/share/telegram_mcp_server/message_queue.json` | Queued messages (created by polling service) |

---

## Quick Reference

### Start polling service
```bash
telegram-mcp-polling
```

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

## Step 7: Telegram-to-OpenCode Bridge (Advanced)

The bridge service allows Telegram messages to automatically become prompts in OpenCode sessions. This enables remote control of OpenCode via Telegram.

### 7.1 Prerequisites

1. OpenCode must be running with the HTTP server enabled
2. The polling service must be running (or messages in the queue)

### 7.2 Start OpenCode with HTTP Server

```bash
# Start OpenCode with HTTP API on default port 4096
opencode --port 4096

# Or specify a custom port
opencode --port 8080
```

### 7.3 Start the Bridge Service

In a separate terminal:

```bash
source /path/to/telegram_mcp_server/.venv/bin/activate

# Start bridge with defaults (connects to localhost:4096)
telegram-opencode-bridge

# Or with custom OpenCode URL
telegram-opencode-bridge --opencode-url http://localhost:8080

# Verbose mode for debugging
telegram-opencode-bridge -v
```

### 7.4 Bridge Options

| Option | Default | Description |
|--------|---------|-------------|
| `--opencode-url` | `http://localhost:4096` | OpenCode HTTP API URL |
| `--queue-dir` | `~/.local/share/telegram_mcp_server` | Queue file directory |
| `--interval` | `2` | Poll interval in seconds |
| `--blocking` | `false` | Wait for response before next message |
| `-v, --verbose` | `false` | Enable debug logging |

### 7.5 Usage Flow

1. **Terminal 1**: Start OpenCode with HTTP server
   ```bash
   opencode --port 4096
   ```

2. **Terminal 2**: Start the polling service
   ```bash
   telegram-mcp-polling
   ```

3. **Terminal 3**: Start the bridge
   ```bash
   telegram-opencode-bridge
   ```

4. **Telegram**: Send a message to your bot
   - The message will appear in OpenCode as a prompt: `[Telegram from @username]: your message`

### 7.6 How It Works

```
Telegram → Bot API → Polling Service → Queue File → Bridge → OpenCode HTTP API
```

1. User sends message to Telegram bot
2. Polling service fetches and queues the message
3. Bridge watches the queue file
4. Bridge sends new messages to OpenCode's `/session/:id/prompt_async` endpoint
5. OpenCode processes the message as a normal prompt

### 7.7 Bridge State

The bridge tracks which messages it has forwarded in:
```
~/.local/share/telegram_mcp_server/bridge_state.json
```

Messages are removed from the queue after being forwarded to prevent duplicates.
