/**
 * Telegram Message Watcher Plugin for OpenCode
 * 
 * Watches the Telegram polling service queue file and triggers
 * a notification when new messages arrive.
 */

import type { Plugin } from "@opencode-ai/plugin"
import { watch, existsSync, readFileSync } from "fs"
import { homedir } from "os"
import { join } from "path"

// Configuration
const QUEUE_DIR = process.env.TELEGRAM_QUEUE_DIR || join(homedir(), ".local/share/telegram_mcp_server")
const QUEUE_FILE = join(QUEUE_DIR, "message_queue.json")
const DEBOUNCE_MS = 1000 // Debounce file changes

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

  // Read current message count on startup
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

  // Get new messages since last check
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

  // Initialize last count
  lastMessageCount = getMessageCount()

  await client.app.log({
    service: "telegram-watcher",
    level: "info",
    message: `Telegram watcher initialized. Queue file: ${QUEUE_FILE}. Current messages: ${lastMessageCount}`,
  })

  // Start watching the queue file
  if (existsSync(QUEUE_FILE)) {
    watch(QUEUE_FILE, (eventType) => {
      if (eventType !== "change" || isProcessing) return

      // Debounce rapid file changes
      if (debounceTimer) clearTimeout(debounceTimer)
      
      debounceTimer = setTimeout(async () => {
        const currentCount = getMessageCount()
        
        if (currentCount > lastMessageCount) {
          isProcessing = true
          const newMessages = getNewMessages(lastMessageCount)
          
          if (newMessages.length > 0) {
            // Format message summary
            const summary = newMessages
              .map(m => `@${m.from_username || m.from_user_id}: ${m.text?.substring(0, 100) || "[no text]"}`)
              .join("\n")

            await client.app.log({
              service: "telegram-watcher",
              level: "info",
              message: `New Telegram messages received (${newMessages.length})`,
              extra: { count: newMessages.length, messages: newMessages },
            })

            // Show toast notification in TUI
            // Note: This uses the tui.toast.show pattern if available
            try {
              // Trigger a system notification on macOS
              const { exec } = await import("child_process")
              const notificationText = `${newMessages.length} new Telegram message(s)`
              exec(`osascript -e 'display notification "${summary.replace(/"/g, '\\"').substring(0, 200)}" with title "Telegram" subtitle "${notificationText}"'`)
            } catch {
              // Notification failed, continue silently
            }
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
    // React to session events
    event: async ({ event }) => {
      // When session becomes idle, check for pending messages
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
