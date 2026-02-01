"""Telegram bot command definitions.

This module provides the list of commands that should be registered with the Telegram Bot API
as bot commands (shown in the Telegram menu).
"""

from typing import List, Dict

def get_bot_commands() -> List[Dict[str, str]]:
    """Return the list of bot commands with descriptions.
    
    Commands are extracted from the help text in command_handler.py.
    Descriptions are kept concise to fit Telegram's 256 character limit.
    """
    return [
        {"command": "help", "description": "Show help message with all commands"},
        {"command": "commands", "description": "List all OpenCode commands"},
        {"command": "health", "description": "Check server health"},
        {"command": "projects", "description": "List all projects"},
        {"command": "project", "description": "Get current project info"},
        {"command": "directory", "description": "Set working directory"},
        {"command": "open", "description": "Open project at path"},
        {"command": "files", "description": "List files in directory"},
        {"command": "read", "description": "Read file content"},
        {"command": "find", "description": "Search for text in files"},
        {"command": "findfile", "description": "Find files by name"},
        {"command": "find_symbol", "description": "Find symbols in code"},
        {"command": "sessions", "description": "List all sessions"},
        {"command": "session", "description": "Create new session"},
        {"command": "status", "description": "Get session status"},
        {"command": "use", "description": "Switch to session"},
        {"command": "prompt", "description": "Send prompt to current session"},
        {"command": "shell", "description": "Execute shell command"},
        {"command": "diff", "description": "Get session diff"},
        {"command": "todo", "description": "Get todo list"},
        {"command": "fork", "description": "Fork session"},
        {"command": "abort", "description": "Abort running session"},
        {"command": "delete", "description": "Delete session"},
        {"command": "share", "description": "Share session"},
        {"command": "unshare", "description": "Unshare session"},
        {"command": "revert", "description": "Revert a message"},
        {"command": "unrevert", "description": "Restore reverted messages"},
        {"command": "summarize", "description": "Summarize session"},
        {"command": "config", "description": "Get current config"},
        {"command": "models", "description": "List available models/providers"},
        {"command": "agents", "description": "List available agents"},
        {"command": "login", "description": "Authenticate with provider"},
        {"command": "vcs", "description": "Get VCS info"},
        {"command": "lsp", "description": "Get LSP status"},
        {"command": "formatter", "description": "Get formatter status"},
        {"command": "mcp", "description": "Get MCP server status"},
        {"command": "dispose", "description": "Dispose current instance"},
        {"command": "info", "description": "Get session details"},
        {"command": "messages", "description": "List messages in session"},
        {"command": "init", "description": "Analyze app and create AGENTS.md"},
        {"command": "set_model", "description": "Set model for session"},
    ]