"""Telegram bot command definitions.

This module provides the list of commands that should be registered with the Telegram Bot API
as bot commands (shown in the Telegram menu).

Supports loading custom commands from a JSON file via TELEGRAM_CUSTOM_COMMANDS_FILE environment variable.
"""

import json
import logging
import os
from pathlib import Path
from typing import List, Dict

logger = logging.getLogger(__name__)

# Default OpenCode commands
DEFAULT_COMMANDS = [
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
    {"command": "sessions", "description": "List all sessions (tap to switch)"},
    {"command": "session", "description": "Create new session"},
    {"command": "status", "description": "Get session status"},
    {"command": "prompt", "description": "Send prompt to current session"},
    {"command": "shell", "description": "Execute shell command"},
    {"command": "diff", "description": "Get session diff"},
    {"command": "todo", "description": "Get todo list"},
    {"command": "fork", "description": "Fork session"},
    {"command": "abort", "description": "Abort running session"},
    {"command": "delete", "description": "Delete session (tap to select)"},
    {"command": "share", "description": "Share session"},
    {"command": "unshare", "description": "Unshare session"},
    {"command": "revert", "description": "Revert a message"},
    {"command": "unrevert", "description": "Restore reverted messages"},
    {"command": "summarize", "description": "Summarize session"},
    {"command": "config", "description": "Get current config"},
    {"command": "models", "description": "List models (tap to select) or set model"},
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
]


def load_custom_commands(file_path: str) -> List[Dict[str, str]] | None:
    """Load custom commands from a JSON file.
    
    The JSON file should contain an array of objects with 'command' and 'description' keys.
    Example:
    [
        {"command": "analyze", "description": "Analyze a news article"},
        {"command": "fetch", "description": "Fetch latest news"}
    ]
    
    Args:
        file_path: Path to the JSON file containing custom commands.
        
    Returns:
        List of command dictionaries, or None if file doesn't exist or is invalid.
    """
    try:
        path = Path(file_path).expanduser()
        if not path.exists():
            logger.warning(f"Custom commands file not found: {file_path}")
            return None
            
        with open(path, "r", encoding="utf-8") as f:
            commands = json.load(f)
            
        # Validate structure
        if not isinstance(commands, list):
            logger.error(f"Custom commands file must contain a JSON array: {file_path}")
            return None
            
        for i, cmd in enumerate(commands):
            if not isinstance(cmd, dict):
                logger.error(f"Command {i} must be an object: {file_path}")
                return None
            if "command" not in cmd or "description" not in cmd:
                logger.error(f"Command {i} must have 'command' and 'description' keys: {file_path}")
                return None
                
        logger.info(f"Loaded {len(commands)} custom commands from {file_path}")
        return commands
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in custom commands file {file_path}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error loading custom commands from {file_path}: {e}")
        return None


def get_bot_commands() -> List[Dict[str, str]]:
    """Return the list of bot commands with descriptions.
    
    If TELEGRAM_CUSTOM_COMMANDS_FILE environment variable is set, loads commands
    from that file. Otherwise, returns the default OpenCode commands.
    
    Commands are registered with Telegram and shown in the command menu.
    Descriptions are kept concise to fit Telegram's 256 character limit.
    """
    # Check for custom commands file
    custom_file = os.environ.get("TELEGRAM_CUSTOM_COMMANDS_FILE", "")
    
    if custom_file:
        custom_commands = load_custom_commands(custom_file)
        if custom_commands is not None:
            return custom_commands
        # Fall back to defaults if custom file couldn't be loaded
        logger.warning("Falling back to default commands")
    
    return DEFAULT_COMMANDS.copy()
