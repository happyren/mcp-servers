"""Command handler for Telegram-OpenCode bridge."""

import asyncio
import os
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple, Union

import httpx

from .opencode_client import OpenCodeClient

logger = logging.getLogger(__name__)


@dataclass
class CommandResponse:
    """Response from a command that can include text and/or keyboard."""
    text: str
    keyboard: list[list[dict[str, str]]] | None = None
    
    def __str__(self) -> str:
        return self.text


class CommandHandler:
    """Handle Telegram slash commands and forward to OpenCode API."""

    # Default favourite models if none configured
    DEFAULT_FAVOURITE_MODELS = [
        ("github-copilot", "gemini-3-pro-preview"),
        ("deepseek", "deepseek-reasoner"),
        ("deepseek", "deepseek-chat"),
        ("github-copilot", "claude-opus-4.5"),
        ("github-copilot", "claude-sonnet-4.5"),
        ("moonshotai-cn", "kimi-k2.5"),
        ("minimax-cn", "MiniMax-M2.1"),
        ("zhipuai", "glm-4.7"),
    ]

    def __init__(
        self,
        opencode: OpenCodeClient,
        current_session_id: Optional[str] = None,
        set_model_callback: Optional[Callable[[str, str], None]] = None,
        get_model_callback: Optional[Callable[[], Optional[Tuple[str, str]]]] = None,
        favourite_models: Optional[list[Tuple[str, str]]] = None,
    ):
        self.opencode = opencode
        self.current_session_id = current_session_id
        self._command_cache = {}
        # Callbacks for model management (set by BridgeService)
        self._set_model_callback = set_model_callback
        self._get_model_callback = get_model_callback
        # Cache for model lookup by short ID (to handle Telegram's 64-byte callback_data limit)
        self._model_cache: dict[str, tuple[str, str]] = {}
        # Favourite models list: [(provider_id, model_id), ...]
        self._favourite_models = favourite_models if favourite_models is not None else self.DEFAULT_FAVOURITE_MODELS

    def _make_model_callback(self, provider_id: str, model_id: str) -> str:
        """Create a callback_data string for a model, ensuring it fits within 64 bytes.
        
        Telegram limits callback_data to 1-64 bytes. For long model names, we use
        a hash-based short ID and store the mapping in _model_cache.
        """
        full_data = f"setmodel:{provider_id}:{model_id}"
        if len(full_data.encode('utf-8')) <= 64:
            return full_data
        
        # Generate a short hash-based ID
        import hashlib
        hash_input = f"{provider_id}:{model_id}"
        short_hash = hashlib.md5(hash_input.encode()).hexdigest()[:8]
        short_data = f"sm:{short_hash}"
        
        # Store in cache for lookup
        self._model_cache[short_hash] = (provider_id, model_id)
        return short_data

    def lookup_model_callback(self, callback_data: str) -> Optional[Tuple[str, str]]:
        """Look up a model from callback_data.
        
        Returns (provider_id, model_id) or None if not found.
        """
        if callback_data.startswith("setmodel:"):
            parts = callback_data.split(":", 2)
            if len(parts) == 3:
                return (parts[1], parts[2])
        elif callback_data.startswith("sm:"):
            short_hash = callback_data[3:]
            return self._model_cache.get(short_hash)
        return None

    def get_command_help(self) -> str:
        """Get help text for all available commands."""
        return """
📚 *Available Commands*

🗂️ *Project & Files*
`/directory [path]` - Set working directory
`/files [path]` - List files in directory
`/read <path>` - Read file content
`/find <pattern>` - Search for text in files
`/findfile <query>` - Find files by name
 `/projects` - List all projects
 `/project` - Get current project info
 `/open <path>` - Open project at path (changes directory + creates session)

📝 *Sessions*
`/session [model]` - Create new session (optional: with model)
`/sessions` - List all sessions (tap to switch)
`/status` - Get session status
`/prompt <message>` - Send prompt to current session
`/shell <command>` - Execute shell command
`/diff [session_id]` - Get session diff
`/todo [session_id]` - Get todo list

🛠️ *Session Management*
`/fork <session_id>` - Fork session
`/abort <session_id>` - Abort running session
`/delete` - Delete session (tap to select)
`/share <session_id>` - Share session
`/unshare <session_id>` - Unshare session
`/revert <message_id>` - Revert a message
`/unrevert [session_id]` - Restore reverted messages
`/summarize [session_id]` - Summarize session

⚙️ *Configuration*
`/config` - Get current config
`/models [model]` - List models (tap to select) or set model
`/agents` - List available agents
`/login <provider>` - Authenticate with provider (interactive)
`/commands` - List all OpenCode commands

🔧 *System*
`/health` - Check server health
`/vcs` - Get VCS info
`/lsp` - Get LSP status
`/formatter` - Get formatter status
`/mcp` - Get MCP server status
`/dispose` - Dispose current instance

💬 *Chat & Interaction*
`/help` - Show this help message
`/info [session_id]` - Get session details
`/messages [session_id]` - List messages in session
`/pending` - Show pending questions & permissions

*Use any other text to send as a prompt to the current session.*
        """.strip()

    def parse_command(self, text: str) -> Tuple[Optional[str], str]:
        """Parse a command from text.

        Returns: (command_name, arguments)
        """
        text = text.strip()
        if not text.startswith("/"):
            return None, ""

        # Remove leading slash and split
        parts = text[1:].split(None, 1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        return command, args

    async def handle_command(self, text: str, chat_id: Optional[int] = None) -> Optional[str]:
        """Handle a command and return response.

        Args:
            text: The command text
            chat_id: Optional chat ID for formatting

        Returns:
            Response message to send back
        """
        command, args = self.parse_command(text)

        if not command:
            # Not a command, return None to indicate regular prompt
            return None

        # Map commands to handler methods
        handlers = {
            "help": self.cmd_help,
            "commands": self.cmd_commands,
            "health": self.cmd_health,
            "projects": self.cmd_projects,
            "project": self.cmd_project,
            "directory": self.cmd_directory,
            "open": self.cmd_open,
            "files": self.cmd_files,
            "read": self.cmd_read,
            "find": self.cmd_find,
            "findfile": self.cmd_findfile,
            "find-symbol": self.cmd_find_symbol,
            "find_symbol": self.cmd_find_symbol,
            "sessions": self.cmd_sessions,
            "session": self.cmd_session,
            "status": self.cmd_status,
            "prompt": self.cmd_prompt,
            "shell": self.cmd_shell,
            "diff": self.cmd_diff,
            "todo": self.cmd_todo,
            "fork": self.cmd_fork,
            "abort": self.cmd_abort,
            "delete": self.cmd_delete,
            "share": self.cmd_share,
            "unshare": self.cmd_unshare,
            "revert": self.cmd_revert,
            "unrevert": self.cmd_unrevert,
            "summarize": self.cmd_summarize,
            "config": self.cmd_config,
            "models": self.cmd_models,
            "agents": self.cmd_agents,
            "login": self.cmd_login,
            "vcs": self.cmd_vcs,
            "lsp": self.cmd_lsp,
            "formatter": self.cmd_formatter,
            "mcp": self.cmd_mcp,
            "dispose": self.cmd_dispose,
            "info": self.cmd_info,
            "messages": self.cmd_messages,
            "init": self.cmd_init,
            "pending": self.cmd_pending,
        }

        handler = handlers.get(command)
        if not handler:
            return f"❌ Unknown command: /{command}\n\nUse /help to see available commands."

        try:
            return await handler(args)
        except Exception as e:
            logger.error(f"Error handling command /{command}: {e}")
            return f"❌ Error executing /{command}: {str(e)[:200]}"

    # Command handlers

    async def cmd_help(self, args: str) -> str:
        return self.get_command_help()

    async def cmd_commands(self, args: str) -> str:
        commands = await self.opencode.list_commands()
        lines = ["📋 *Available OpenCode Commands*"]
        for cmd in commands:
            name = cmd.get("name", "unknown")
            desc = cmd.get("description", "")
            lines.append(f"/{name} - {desc}")
        return "\n".join(lines)

    async def cmd_health(self, args: str) -> str:
        health = await self.opencode.health_check()
        version = health.get("version", "unknown")
        return f"✅ OpenCode Server is healthy\n\nVersion: `{version}`"

    async def cmd_projects(self, args: str) -> str:
        projects = await self.opencode.list_projects()
        if not projects:
            return "No projects found."
        lines = ["📁 *Projects*"]
        for i, p in enumerate(projects, 1):
            name = p.get("name", "unnamed")
            path = p.get("path", "unknown")
            lines.append(f"{i}. **{name}**\n   `{path}`")
        return "\n".join(lines)

    async def cmd_project(self, args: str) -> str:
        project = await self.opencode.get_current_project()
        name = project.get("name", "unnamed")
        path = project.get("path", "unknown")
        return f"📁 *Current Project*\n\n**Name:** {name}\n**Path:** `{path}`"

    async def cmd_open(self, args: str) -> str:
        if not args:
            return "❌ Usage: `/open <project_path>`\n\nExample: `/open ~/dev/my-app`\n\nThis will:\n1. Change to the directory\n2. Create a new session for the project"
        
        # Need an active session or create one
        if not self.current_session_id:
            # Create a session first
            try:
                session = await self.opencode.create_session(title="Open project")
                self.current_session_id = session["id"]
            except Exception as e:
                return f"❌ Could not create session: {str(e)[:200]}"
        
        # Check if session is idle, wait up to 10 seconds if busy
        max_wait_seconds = 10
        check_interval = 0.5
        waited = 0
        
        while waited < max_wait_seconds:
            if await self.opencode.is_session_idle(self.current_session_id):
                break
            await asyncio.sleep(check_interval)
            waited += check_interval
        
        if not await self.opencode.is_session_idle(self.current_session_id):
            return "❌ Session is busy. Please wait for current operations to complete and try again."

        # Send cd command via shell
        import shlex
        # Expand tilde to home directory to avoid quoting issues
        expanded_path = os.path.expanduser(args)
        quoted_path = shlex.quote(expanded_path)
        shell_cmd = f"cd {quoted_path}"
        
        try:
            # Get current directory before change
            old_path_info = await self.opencode.get_path()
            old_path = old_path_info.get("path", "unknown")
            
            result: dict[str, Any] = await self.opencode.send_shell(self.current_session_id, shell_cmd)
            
            # Check for errors in response
            parts = result.get("parts", [])
            error_text = ""
            
            for part in parts:
                if part.get("type") == "text":
                    text = part.get("text", "")
                    if "error" in text.lower() or "no such" in text.lower() or "not found" in text.lower():
                        error_text += text + "\n"
            
            if error_text:
                return f"❌ Failed to open project:\n\n```\n{error_text.strip()}\n```"
            
            # Get new directory and project info
            new_path_info = await self.opencode.get_path()
            new_path = new_path_info.get("path", "unknown")
            project = await self.opencode.get_current_project()
            name = project.get("name", "unnamed")
            
            # Update session title to reflect project
            try:
                title = name if name != "unnamed" else os.path.basename(new_path.rstrip('/'))
                await self.opencode.update_session(self.current_session_id, title=f"Project: {title}")
            except:
                pass  # Optional, non-critical
            
            if new_path != old_path:
                return f"✅ Opened project in `{new_path}`\n📁 **Project:** {name}\n📝 **Session:** `{self.current_session_id}`\n\nUse `/files` to explore or `/prompt` to start working."
            else:
                return f"✅ Opened project in `{new_path}`\n📁 **Project:** {name}\n📝 **Session:** `{self.current_session_id}`"
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                error_detail = ""
                try:
                    error_data = e.response.json()
                    error_detail = str(error_data).replace("{", "{{").replace("}", "}}")[:150]
                except:
                    error_detail = str(e)[:150]
                return f"❌ Bad request to OpenCode (400):\n\n`{error_detail}`\n\nThis may be due to invalid session state or API changes."
            elif e.response.status_code == 429:
                return "❌ Too many requests. Please wait before trying again."
            elif e.response.status_code == 500:
                error_detail = ""
                try:
                    error_data = e.response.json()
                    error_msg = error_data.get("data", {}).get("message", str(error_data))
                    if "is busy" in error_msg:
                        session_short = self.current_session_id if self.current_session_id else "unknown"
                        return f"❌ Session is busy. Please wait or use:\n`/abort {session_short}`\n\nOr create a new session with `/session`."
                    elif "agent.model" in error_msg or "undefined is not an object" in error_msg:
                        # Try to get list of available agents to suggest valid ones
                        try:
                            agents = await self.opencode.list_agents()
                            agent_names = [a.get("name", "") for a in agents if a.get("name")]
                            valid_agents = ", ".join([f"`{name}`" for name in agent_names[:5]])
                            return f"❌ Invalid agent specified. Available agents:\n\n{valid_agents}\n\nTry using a valid agent like `/shell --agent explore <command>`"
                        except:
                            return f"❌ Invalid agent configuration. Try using `explore` agent:\n\n`/shell --agent explore <command>`"
                    error_detail = error_msg[:150]
                except:
                    error_detail = str(e)[:150]
                return f"❌ OpenCode internal server error (500):\n\n`{error_detail}`"
            else:
                logger.error(f"HTTP error opening project: {e}")
                return f"❌ OpenCode server error {e.response.status_code}: {str(e)[:200]}"
        except Exception as e:
            logger.error(f"Error opening project: {e}")
            return f"❌ Error opening project: {str(e)[:200]}"

    async def cmd_directory(self, args: str) -> str:
        if not args:
            path_info = await self.opencode.get_path()
            path = path_info.get("path", "unknown")
            return f"📂 *Current Directory*\n\n`{path}`\n\nUsage: `/directory <path>` to set a new working directory"

        # Need an active session to change directory
        if not self.current_session_id:
            return "❌ No active session. Use `/session` to create one or `/sessions` to select one first."

        # Check if session is idle, wait up to 10 seconds if busy
        max_wait_seconds = 10
        check_interval = 0.5
        waited = 0
        
        while waited < max_wait_seconds:
            if await self.opencode.is_session_idle(self.current_session_id):
                break
            await asyncio.sleep(check_interval)
            waited += check_interval
        
        if not await self.opencode.is_session_idle(self.current_session_id):
            return "❌ Session is busy. Please wait for current operations to complete and try again."

        # Send cd command via shell
        import shlex
        # Expand tilde to home directory to avoid quoting issues
        expanded_path = os.path.expanduser(args)
        quoted_path = shlex.quote(expanded_path)
        shell_cmd = f"cd {quoted_path}"
        
        try:
            # Get current directory before change
            old_path_info = await self.opencode.get_path()
            old_path = old_path_info.get("path", "unknown")
            
            result: dict[str, Any] = await self.opencode.send_shell(self.current_session_id, shell_cmd)
            
            # Check for errors in response
            parts = result.get("parts", [])
            error_text = ""
            output_text = ""
            
            for part in parts:
                if part.get("type") == "text":
                    text = part.get("text", "")
                    if "error" in text.lower() or "no such" in text.lower() or "not found" in text.lower():
                        error_text += text + "\n"
                    else:
                        output_text += text + "\n"
            
            if error_text:
                return f"❌ Failed to change directory:\n\n```\n{error_text.strip()}\n```"
            
            # Get new directory to confirm
            new_path_info = await self.opencode.get_path()
            new_path = new_path_info.get("path", "unknown")
            
            if new_path != old_path:
                return f"✅ Changed directory from:\n`{old_path}`\n\nto:\n`{new_path}`"
            else:
                # Path unchanged (may be project root or same directory)
                return f"✅ Directory change command executed.\n\nCurrent directory: `{new_path}`"
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                error_detail = ""
                try:
                    error_data = e.response.json()
                    error_detail = str(error_data).replace("{", "{{").replace("}", "}}")[:150]
                except:
                    error_detail = str(e)[:150]
                return f"❌ Bad request to OpenCode (400):\n\n`{error_detail}`\n\nThis may be due to invalid session state or API changes."
            elif e.response.status_code == 429:
                return "❌ Too many requests. Please wait before trying again."
            elif e.response.status_code == 500:
                error_detail = ""
                try:
                    error_data = e.response.json()
                    error_msg = error_data.get("data", {}).get("message", str(error_data))
                    if "is busy" in error_msg:
                        session_short = self.current_session_id[:8] if self.current_session_id else "unknown"
                        return f"❌ Session is busy. Please wait or use:\n`/abort {session_short}`\n\nOr create a new session with `/session`."
                    elif "agent.model" in error_msg or "undefined is not an object" in error_msg:
                        # Try to get list of available agents to suggest valid ones
                        try:
                            agents = await self.opencode.list_agents()
                            agent_names = [a.get("name", "") for a in agents if a.get("name")]
                            valid_agents = ", ".join([f"`{name}`" for name in agent_names[:5]])
                            return f"❌ Invalid agent specified. Available agents:\n\n{valid_agents}\n\nTry using a valid agent like `/shell --agent explore <command>`"
                        except:
                            return f"❌ Invalid agent configuration. Try using `explore` agent:\n\n`/shell --agent explore <command>`"
                    error_detail = error_msg[:150]
                except:
                    error_detail = str(e)[:150]
                return f"❌ OpenCode internal server error (500):\n\n`{error_detail}`"
            else:
                logger.error(f"HTTP error changing directory: {e}")
                return f"❌ OpenCode server error {e.response.status_code}: {str(e)[:200]}"
        except Exception as e:
            logger.error(f"Error changing directory: {e}")
            return f"❌ Error changing directory: {str(e)[:200]}"

    async def cmd_files(self, args: str) -> str:
        path = args if args else None
        files = await self.opencode.list_files(path=path)
        if not files:
            return f"No files found in `{args if args else 'root'}`."

        lines = [f"📄 *Files in `{args if args else 'root'}`*"]
        for f in files[:50]:  # Limit to 50 files
            name = f.get("name", "")
            is_dir = f.get("isDirectory", False)
            icon = "📁" if is_dir else "📄"
            lines.append(f"{icon} `{name}`")
        if len(files) > 50:
            lines.append(f"\n... and {len(files) - 50} more files")
        return "\n".join(lines)

    async def cmd_read(self, args: str) -> str:
        if not args:
            return "❌ Usage: `/read <file_path>`"

        file_info = await self.opencode.read_file(args)
        content = file_info.get("content", "")

        # Truncate if too long
        max_length = 3500
        if len(content) > max_length:
            content = content[:max_length] + "\n\n... (truncated, file too long)"

        return f"📖 *Content of `{args}`*\n\n```\n{content}\n```"

    async def cmd_find(self, args: str) -> str:
        if not args:
            return "❌ Usage: `/find <pattern>`"

        results = await self.opencode.search_text(args)
        if not results:
            return f"No matches found for `{args}`."

        lines = [f"🔍 *Search Results for `{args}`*"]
        for r in results[:20]:  # Limit to 20 results
            path = r.get("path", "")
            line = r.get("lineNumber", 0)
            lines.append(f"`{path}:{line}`")
        if len(results) > 20:
            lines.append(f"\n... and {len(results) - 20} more matches")
        return "\n".join(lines)

    async def cmd_findfile(self, args: str) -> str:
        if not args:
            return "❌ Usage: `/findfile <query>`"

        files = await self.opencode.find_files(args)
        if not files:
            return f"No files found matching `{args}`."

        lines = [f"📁 *Files matching `{args}`*"]
        for f in files[:30]:  # Limit to 30 results
            lines.append(f"`{f}`")
        if len(files) > 30:
            lines.append(f"\n... and {len(files) - 30} more files")
        return "\n".join(lines)

    async def cmd_find_symbol(self, args: str) -> str:
        if not args:
            return "❌ Usage: `/find-symbol <query>`"

        symbols = await self.opencode.find_symbols(args)
        if not symbols:
            return f"No symbols found matching `{args}`."

        lines = [f"🔣 *Symbols matching `{args}`*"]
        for s in symbols[:20]:
            name = s.get("name", "")
            kind = s.get("kind", "")
            path = s.get("path", "")
            lines.append(f"{kind} `{name}`\n   in `{path}`")
        if len(symbols) > 20:
            lines.append(f"\n... and {len(symbols) - 20} more symbols")
        return "\n".join(lines)

    async def cmd_sessions(self, args: str) -> Union[str, CommandResponse]:
        sessions, status_dict = await asyncio.gather(
            self.opencode.list_sessions(),
            self.opencode.get_session_status()
        )
        if not sessions:
            return "No sessions found. Use `/session` to create one."

        keyboard: list[list[dict[str, str]]] = []
        seen_ids: set[str] = set()  # Deduplicate sessions
        
        for s in sessions[:15]:  # Limit to 15 sessions
            session_id = s.get("id", "unknown")
            
            # Skip duplicates
            if session_id in seen_ids:
                continue
            seen_ids.add(session_id)
            
            title = s.get("title", "Untitled")
            parent_id = s.get("parentID")
            status = status_dict.get(s.get("id", ""), {}).get("type", "unknown")
            is_current = s.get("id") == self.current_session_id

            status_emoji = {"busy": "🔴", "idle": "🟢", "unknown": "⚪"}.get(status, "⚪")
            current_marker = " 👈" if is_current else ""
            parent_note = " [sub]" if parent_id else ""
            
            # Truncate title for button display
            short_title = title[:25] + "..." if len(title) > 28 else title
            short_id = session_id[:8]
            
            # Add button for each session
            keyboard.append([{
                "text": f"{status_emoji} {short_id}{parent_note} - {short_title}{current_marker}",
                "callback_data": f"session:{session_id}"
            }])
        
        # Minimal text - just header and current session
        current = self.current_session_id[:8] if self.current_session_id else "None"
        text = f"📝 *Sessions* ({len(seen_ids)})\n\nCurrent: `{current}`\nTap to switch:"
        
        return CommandResponse(text=text, keyboard=keyboard)

    async def cmd_session(self, args: str) -> str:
        # Parse optional model
        model = None
        provider = None
        if args:
            # Check if args contains a model specification (provider/model)
            if "/" in args:
                parts = args.split("/", 1)
                provider = parts[0].strip()
                model = parts[1].strip()
            else:
                model = args.strip()

        try:
            session = await self.opencode.create_session(title=model or "New Session")
            self.current_session_id = session["id"]
            short_id = session["id"][:8]
            
            # If model specified with provider, set it via callback
            if provider and model and self._set_model_callback:
                self._set_model_callback(provider, model)
                return f"✅ Created new session `{short_id}` using model `{provider}/{model}`"
            elif model and not provider:
                return f"✅ Created new session `{short_id}`\n\n⚠️ Model `{model}` specified without provider.\nUse `/set-model <provider>/{model}` to set the model."
            else:
                return f"✅ Created new session `{short_id}`"
        except Exception as e:
            return f"❌ Failed to create session: {str(e)}"

    async def cmd_status(self, args: str) -> str:
        status_dict = await self.opencode.get_session_status()

        if not args:
            # Show status for all sessions
            lines = ["📊 *Session Status*"]
            busy_count = 0
            idle_count = 0
            for session_id, status in status_dict.items():
                session_short = session_id
                status_type = status.get("type", "unknown")
                if status_type == "busy":
                    busy_count += 1
                    emoji = "🔴"
                else:
                    idle_count += 1
                    emoji = "🟢"
                lines.append(f"{emoji} `{session_short}` - {status_type}")
            lines.append(f"\n🔴 Busy: {busy_count} | 🟢 Idle: {idle_count}")
            return "\n".join(lines)
        else:
            # Show status for specific session
            session_short = args
            status = status_dict.get(args, {})
            if not status:
                return f"❌ Session `{session_short}` not found."

            status_type = status.get("type", "unknown")
            return f"📊 *Session `{session_short}` Status*\n\nStatus: **{status_type}**"

    async def cmd_prompt(self, args: str) -> str:
        if not args:
            return "❌ Usage: `/prompt <message>`"

        if not self.current_session_id:
            return "❌ No active session. Use `/session` to create one or `/sessions` to select one."

        try:
            result: dict[str, Any] = await self.opencode.send_message(self.current_session_id, args)
            # Extract text from response
            parts = result.get("parts", [])
            text_parts = []
            for part in parts:
                if part.get("type") == "text":
                    text = part.get("text", "")
                    # Truncate if too long
                    if len(text) > 3500:
                        text = text[:3500] + "\n\n... (truncated)"
                    text_parts.append(text)

            if text_parts:
                return "\n".join(text_parts)
            return "✅ Prompt sent (no response returned)"
        except Exception as e:
            return f"❌ Error sending prompt: {str(e)}"

    async def cmd_shell(self, args: str) -> str:
        if not args:
            return "❌ Usage: `/shell <command>`"

        if not self.current_session_id:
            return "❌ No active session. Use `/session` to create one or `/sessions` to select one."

        # Check if session is idle, wait up to 10 seconds if busy
        max_wait_seconds = 10
        check_interval = 0.5
        waited = 0
        
        while waited < max_wait_seconds:
            if await self.opencode.is_session_idle(self.current_session_id):
                break
            await asyncio.sleep(check_interval)
            waited += check_interval
        
        if not await self.opencode.is_session_idle(self.current_session_id):
            return "❌ Session is busy. Please wait for current operations to complete and try again."

        try:
            result: dict[str, Any] = await self.opencode.send_shell(self.current_session_id, args)
            # Extract output from response
            parts = result.get("parts", [])
            text_parts = []
            for part in parts:
                if part.get("type") == "text":
                    text = part.get("text", "")
                    if len(text) > 3500:
                        text = text[:3500] + "\n\n... (truncated)"
                    text_parts.append(text)

            if text_parts:
                return f"💻 *Shell Output*\n\n```bash\n{args}\n```\n\n" + "\n".join(text_parts)
            return f"✅ Shell command executed: `{args}`"
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                error_detail = ""
                try:
                    error_data = e.response.json()
                    error_detail = str(error_data).replace("{", "{{").replace("}", "}}")[:150]
                except:
                    error_detail = str(e)[:150]
                return f"❌ Bad request to OpenCode (400):\n\n`{error_detail}`\n\nThis may be due to invalid session state or API changes."
            elif e.response.status_code == 429:
                return "❌ Too many requests. Please wait before trying again."
            elif e.response.status_code == 500:
                error_detail = ""
                try:
                    error_data = e.response.json()
                    error_msg = error_data.get("data", {}).get("message", str(error_data))
                    if "is busy" in error_msg:
                        session_short = self.current_session_id[:8] if self.current_session_id else "unknown"
                        return f"❌ Session is busy. Please wait or use:\n`/abort {session_short}`\n\nOr create a new session with `/session`."
                    elif "agent.model" in error_msg or "undefined is not an object" in error_msg:
                        # Try to get list of available agents to suggest valid ones
                        try:
                            agents = await self.opencode.list_agents()
                            agent_names = [a.get("name", "") for a in agents if a.get("name")]
                            valid_agents = ", ".join([f"`{name}`" for name in agent_names[:5]])
                            return f"❌ Invalid agent specified. Available agents:\n\n{valid_agents}\n\nTry using a valid agent like `/shell --agent explore <command>`"
                        except:
                            return f"❌ Invalid agent configuration. Try using `explore` agent:\n\n`/shell --agent explore <command>`"
                    error_detail = error_msg[:150]
                except:
                    error_detail = str(e)[:150]
                return f"❌ OpenCode internal server error (500):\n\n`{error_detail}`"
            else:
                logger.error(f"HTTP error running shell: {e}")
                return f"❌ OpenCode server error {e.response.status_code}: {str(e)[:200]}"
        except Exception as e:
            logger.error(f"Error running shell: {e}")
            return f"❌ Error running shell: {str(e)[:200]}"

    async def cmd_diff(self, args: str) -> str:
        session_id = args if args else self.current_session_id
        if not session_id:
            return "❌ Usage: `/diff <session_id>` or have an active session"

        try:
            diffs = await self.opencode.get_session_diff(session_id)
            if not diffs:
                return f"No diff for session `{session_id[:8]}`."

            lines = [f"📝 *Diff for session `{session_id[:8]}`*"]
            for d in diffs[:20]:  # Limit to 20 files
                path = d.get("path", "")
                status = d.get("status", "unknown")
                lines.append(f"{status} `{path}`")
            if len(diffs) > 20:
                lines.append(f"\n... and {len(diffs) - 20} more files")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Error getting diff: {str(e)}"

    async def cmd_todo(self, args: str) -> str:
        session_id = args if args else self.current_session_id
        if not session_id:
            return "❌ Usage: `/todo <session_id>` or have an active session"

        try:
            todos = await self.opencode.get_session_todo(session_id)
            if not todos:
                return f"No todo list for session `{session_id[:8]}`."

            lines = [f"✅ *Todo for session `{session_id[:8]}`*"]
            for i, todo in enumerate(todos, 1):
                content = todo.get("content", "")
                completed = todo.get("completed", False)
                status = "✓" if completed else "○"
                lines.append(f"{i}. {status} {content}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Error getting todo: {str(e)}"

    async def cmd_fork(self, args: str) -> str:
        if not args:
            return "❌ Usage: `/fork <session_id>`"

        try:
            new_session = await self.opencode.fork_session(args)
            new_id = new_session.get("id", "unknown")
            self.current_session_id = new_id
            return f"✅ Forked session `{args[:8]}` into `{new_id[:8]}`\n\nNew session is now active."
        except Exception as e:
            return f"❌ Error forking session: {str(e)}"

    async def cmd_abort(self, args: str) -> str:
        if not args:
            return "❌ Usage: `/abort <session_id>`"

        try:
            await self.opencode.abort_session(args)
            return f"✅ Aborted session `{args[:8]}`"
        except Exception as e:
            return f"❌ Error aborting session: {str(e)}"

    async def cmd_delete(self, args: str) -> Union[str, CommandResponse]:
        # If no args, show session list with delete buttons
        if not args:
            sessions = await self.opencode.list_sessions()
            if not sessions:
                return "No sessions to delete."
            
            keyboard: list[list[dict[str, str]]] = []
            seen_ids: set[str] = set()
            
            for s in sessions[:15]:
                session_id = s.get("id", "unknown")
                if session_id in seen_ids:
                    continue
                seen_ids.add(session_id)
                
                title = s.get("title", "Untitled")
                short_title = title[:25] + "..." if len(title) > 28 else title
                short_id = session_id[:8]
                
                # Skip current session - don't allow deleting it
                if session_id == self.current_session_id:
                    continue
                
                keyboard.append([{
                    "text": f"🗑️ {short_id} - {short_title}",
                    "callback_data": f"delete:{session_id}"
                }])
            
            if not keyboard:
                return "No sessions available to delete (current session cannot be deleted)."
            
            text = f"🗑️ *Delete Session*\n\nTap to delete:"
            return CommandResponse(text=text, keyboard=keyboard)

        # Direct delete with session ID
        try:
            await self.opencode.delete_session(args)
            if self.current_session_id == args:
                self.current_session_id = None
            return f"✅ Deleted session `{args[:8]}`"
        except Exception as e:
            return f"❌ Error deleting session: {str(e)}"

    async def cmd_share(self, args: str) -> str:
        if not args:
            return "❌ Usage: `/share <session_id>`"

        try:
            session = await self.opencode.share_session(args)
            share_url = session.get("shareUrl", "unknown")
            return f"✅ Shared session `{args[:8]}`\n\nURL: {share_url}"
        except Exception as e:
            return f"❌ Error sharing session: {str(e)}"

    async def cmd_unshare(self, args: str) -> str:
        if not args:
            return "❌ Usage: `/unshare <session_id>`"

        try:
            await self.opencode.unshare_session(args)
            return f"✅ Unshared session `{args[:8]}`"
        except Exception as e:
            return f"❌ Error unsharing session: {str(e)}"

    async def cmd_revert(self, args: str) -> str:
        if not args:
            return "❌ Usage: `/revert <message_id> [--part <part_id>]`"

        # Parse optional part ID
        part_id = None
        if "--part" in args:
            parts = args.split("--part", 1)
            args = parts[0].strip()
            part_id = parts[1].strip() if len(parts) > 1 else None

        if not self.current_session_id:
            return "❌ No active session. Use `/sessions` to select one."

        try:
            await self.opencode.revert_message(self.current_session_id, args, part_id)
            return f"✅ Reverted message `{args[:8]}` in session `{self.current_session_id[:8]}`"
        except Exception as e:
            return f"❌ Error reverting message: {str(e)}"

    async def cmd_unrevert(self, args: str) -> str:
        session_id = args if args else self.current_session_id
        if not session_id:
            return "❌ Usage: `/unrevert <session_id>` or have an active session"

        try:
            await self.opencode.unrevert_messages(session_id)
            return f"✅ Restored all reverted messages in session `{session_id[:8]}`"
        except Exception as e:
            return f"❌ Error unreverting: {str(e)}"

    async def cmd_summarize(self, args: str) -> str:
        session_id = args if args else self.current_session_id
        if not session_id:
            return "❌ Usage: `/summarize <session_id>` or have an active session"

        # Use default model for now
        try:
            await self.opencode.summarize_session(session_id, "deepseek", "deepseek-reasoner")
            return f"✅ Summarizing session `{session_id[:8]}`...\n\nThis may take a moment. Check the session for the summary."
        except Exception as e:
            return f"❌ Error summarizing: {str(e)}"

    async def cmd_config(self, args: str) -> str:
        try:
            config = await self.opencode.get_config()
            model = config.get("model", "unknown")
            agent = config.get("agent", "none")
            return f"⚙️ *Current Config*\n\n**Model:** `{model}`\n**Agent:** `{agent}`"
        except Exception as e:
            return f"❌ Error getting config: {str(e)}"

    async def cmd_models(self, args: str) -> Union[str, CommandResponse]:
        """Show favourite models with selection buttons, or set model directly."""
        
        # If args provided, set model directly (like old set_model behavior)
        if args:
            args = args.strip()
            
            # Check for provider/model format
            if "/" in args:
                parts = args.split("/", 1)
                provider_id = parts[0].strip()
                model_id = parts[1].strip()
            else:
                # Try to infer provider from model name
                model_id = args
                provider_id = None
                
                # First, check favourite models for exact match
                for fav_provider, fav_model in self._favourite_models:
                    if fav_model.lower() == model_id.lower():
                        provider_id = fav_provider
                        break
                
                # If not found in favourites, try pattern matching
                if not provider_id:
                    if model_id.lower().startswith("gpt") or model_id.startswith("o1") or model_id.startswith("o3"):
                        provider_id = "openai"
                    elif model_id.lower().startswith("claude"):
                        provider_id = "anthropic"
                    elif model_id.lower().startswith("deepseek"):
                        provider_id = "deepseek"
                    elif model_id.lower().startswith("gemini"):
                        provider_id = "google"
                    elif model_id.lower().startswith("minimax"):
                        provider_id = "minimax"
                    elif model_id.lower().startswith("kimi"):
                        provider_id = "moonshotai-cn"
                    elif model_id.lower().startswith("glm"):
                        provider_id = "zhipuai-coding-plan"
                
                if not provider_id:
                    return f"❌ Could not infer provider for `{model_id}`\n\nUse format: `/models <provider>/<model>`"
            
            if self._set_model_callback:
                self._set_model_callback(provider_id, model_id)
                return f"✅ Model set to `{provider_id}/{model_id}`\n\nAll subsequent prompts will use this model."
            else:
                return f"⚠️ Model callback not available."
        
        # No args - show current model and favourite models picker
        if not self._favourite_models:
            return "❌ No favourite models configured.\n\nSet TELEGRAM_FAVOURITE_MODELS environment variable."
        
        # Get current model info
        current_info = ""
        if self._get_model_callback:
            current = self._get_model_callback()
            if current:
                current_info = f"*Current:* `{current[0]}/{current[1]}`\n\n"
        
        # Group favourite models by provider
        providers: dict[str, list[str]] = {}
        for provider_id, model_id in self._favourite_models:
            if provider_id not in providers:
                providers[provider_id] = []
            providers[provider_id].append(model_id)
        
        # Create inline keyboard: single column with model buttons, grouped by provider
        keyboard: list[list[dict[str, str]]] = []
        for provider_id in sorted(providers.keys()):
            models = providers[provider_id]
            # Add a header with provider name for each provider group
            keyboard.append([{"text": f"─── {provider_id} ───", "callback_data": "ignore"}])
            # Add model buttons (single column)
            for model_id in models:
                keyboard.append([{
                    "text": f"• {model_id}",
                    "callback_data": self._make_model_callback(provider_id, model_id)
                }])
        
        text = f"🤖 *Models*\n\n{current_info}Tap to select, or use:\n`/models <provider>/<model>`"
        return CommandResponse(text=text, keyboard=keyboard)

    async def cmd_agents(self, args: str) -> str:
        try:
            agents = await self.opencode.list_agents()
            if not agents:
                return "No agents available."

            lines = ["🤖 *Available Agents*"]
            for agent in agents[:20]:  # Limit to 20 agents
                name = agent.get("name", "unknown")
                desc = agent.get("description", "")
                lines.append(f"**{name}**\n   {desc}")
            if len(agents) > 20:
                lines.append(f"\n... and {len(agents) - 20} more agents")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Error getting agents: {str(e)}"

    async def cmd_login(self, args: str) -> str:
        if not args:
            return "❌ Usage: `/login <provider_id>`\n\nExample: `/login anthropic`"

        try:
            auth_methods = await self.opencode.get_provider_auth()
            methods = auth_methods.get(args, [])

            if not methods:
                return f"❌ Provider `{args}` not found or has no authentication methods."

            lines = [f"🔐 *Authentication for `{args}`*\n\nAvailable methods:"]
            for method in methods:
                method_type = method.get("type", "unknown")
                lines.append(f"- {method_type}")

            lines.append("\nPlease use the OpenCode TUI to complete authentication.")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Error: {str(e)}"

    async def cmd_vcs(self, args: str) -> str:
        try:
            vcs = await self.opencode.get_vcs_info()
            branch = vcs.get("branch", "unknown")
            remote = vcs.get("remote", "none")
            commit = vcs.get("commit", "unknown")[:8]
            return f"🔀 *VCS Info*\n\n**Branch:** `{branch}`\n**Remote:** `{remote}`\n**Commit:** `{commit}`"
        except Exception as e:
            return f"❌ Error getting VCS info: {str(e)}"

    async def cmd_lsp(self, args: str) -> str:
        try:
            lsps = await self.opencode.get_lsp_status()
            if not lsps:
                return "No LSP servers running."

            lines = ["🔌 *LSP Status*"]
            for lsp in lsps:
                language = lsp.get("language", "unknown")
                status = lsp.get("status", "unknown")
                status_emoji = "🟢" if status == "running" else "🔴"
                lines.append(f"{status_emoji} `{language}` - {status}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Error getting LSP status: {str(e)}"

    async def cmd_formatter(self, args: str) -> str:
        try:
            formatters = await self.opencode.get_formatter_status()
            if not formatters:
                return "No formatters configured."

            lines = ["✨ *Formatter Status*"]
            for fmt in formatters:
                language = fmt.get("language", "unknown")
                status = fmt.get("status", "unknown")
                status_emoji = "🟢" if status == "running" else "🔴"
                lines.append(f"{status_emoji} `{language}` - {status}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Error getting formatter status: {str(e)}"

    async def cmd_mcp(self, args: str) -> str:
        try:
            mcps = await self.opencode.get_mcp_status()
            if not mcps:
                return "No MCP servers running."

            lines = ["🔌 *MCP Servers*"]
            for name, status in mcps.items():
                state = status.get("state", "unknown")
                status_emoji = "🟢" if state == "connected" else "🔴"
                lines.append(f"{status_emoji} `{name}` - {state}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Error getting MCP status: {str(e)}"

    async def cmd_dispose(self, args: str) -> str:
        try:
            await self.opencode.dispose_instance()
            self.current_session_id = None
            return "✅ Disposed current instance"
        except Exception as e:
            return f"❌ Error disposing instance: {str(e)}"

    async def cmd_info(self, args: str) -> str:
        session_id = args if args else self.current_session_id
        if not session_id:
            return "❌ Usage: `/info <session_id>` or have an active session"

        try:
            session = await self.opencode.get_session(session_id)
            session_short = session.get("id", "")[:8]
            title = session.get("title", "Untitled")
            parent_id = session.get("parentID")
            created_at = session.get("createdAt", "unknown")

            lines = [f"📝 *Session Info `{session_short}`*"]
            lines.append(f"**Title:** {title}")
            lines.append(f"**Parent:** `{parent_id[:8] if parent_id else 'none'}`")
            lines.append(f"**Created:** {created_at}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Error getting session info: {str(e)}"

    async def cmd_messages(self, args: str) -> str:
        session_id = args if args else self.current_session_id
        if not session_id:
            return "❌ Usage: `/messages <session_id>` or have an active session"

        try:
            messages = await self.opencode.get_messages(session_id, limit=10)
            if not messages:
                return f"No messages in session `{session_id[:8]}`."

            lines = [f"💬 *Recent Messages in `{session_id[:8]}`*"]
            for i, msg in enumerate(messages, 1):
                info = msg.get("info", {})
                role = info.get("role", "unknown")
                created_at = info.get("createdAt", "")[:19]
                preview = ""
                parts = msg.get("parts", [])
                for part in parts:
                    if part.get("type") == "text":
                        preview = part.get("text", "")[:100]
                        break
                lines.append(f"{i}. *{role}* ({created_at})\n   `{preview}...`")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Error getting messages: {str(e)}"

    async def cmd_init(self, args: str) -> str:
        if not self.current_session_id:
            return "❌ No active session. Use `/sessions` to select one."

        # Need a message ID to init, we'll use the last message
        try:
            messages = await self.opencode.get_messages(self.current_session_id, limit=1)
            if not messages:
                return "❌ No messages in current session to use for init."

            message_id = messages[0].get("info", {}).get("id")
            await self.opencode.init_session(
                self.current_session_id, message_id, "deepseek", "deepseek-reasoner"
            )
            return f"✅ Analyzing app and creating AGENTS.md...\n\nThis may take a moment."
        except Exception as e:
            return f"❌ Error initializing session: {str(e)}"

    async def cmd_pending(self, args: str) -> str:
        """Show pending questions and permissions."""
        session_id = self.current_session_id
        
        lines = ["📋 *Pending Requests*\n"]
        
        # Get pending questions
        try:
            questions = await self.opencode.list_pending_questions()
            if session_id:
                questions = [q for q in questions if q.get("sessionID") == session_id]
            
            if questions:
                lines.append(f"*Questions ({len(questions)}):*")
                for q in questions[:5]:  # Limit to 5
                    q_id = q.get("id", "unknown")
                    q_texts = q.get("questions", [])
                    header = q_texts[0].get("header", "Question") if q_texts else "Question"
                    lines.append(f"  • `{q_id[:20]}...` - {header}")
                if len(questions) > 5:
                    lines.append(f"  ... and {len(questions) - 5} more")
                lines.append("")
            else:
                lines.append("*Questions:* None pending\n")
        except Exception as e:
            lines.append(f"*Questions:* Error fetching: {str(e)[:50]}\n")
        
        # Get pending permissions
        try:
            permissions = await self.opencode.list_pending_permissions()
            if session_id:
                permissions = [p for p in permissions if p.get("sessionID") == session_id]
            
            if permissions:
                lines.append(f"*Permissions ({len(permissions)}):*")
                for p in permissions[:5]:  # Limit to 5
                    p_id = p.get("id", "unknown")
                    permission = p.get("permission", "unknown")
                    patterns = p.get("patterns", [])
                    pattern_preview = patterns[0][:30] if patterns else ""
                    lines.append(f"  • `{p_id[:20]}...` - {permission}: {pattern_preview}")
                if len(permissions) > 5:
                    lines.append(f"  ... and {len(permissions) - 5} more")
            else:
                lines.append("*Permissions:* None pending")
        except Exception as e:
            lines.append(f"*Permissions:* Error fetching: {str(e)[:50]}")
        
        return "\n".join(lines)
