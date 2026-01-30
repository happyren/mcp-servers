"""Command handler for Telegram-OpenCode bridge."""

import logging
import re
from typing import Any, Optional, Tuple

from .opencode_client import OpenCodeClient

logger = logging.getLogger(__name__)


class CommandHandler:
    """Handle Telegram slash commands and forward to OpenCode API."""

    def __init__(self, opencode: OpenCodeClient, current_session_id: Optional[str] = None):
        self.opencode = opencode
        self.current_session_id = current_session_id
        self._command_cache = {}

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

📝 *Sessions*
`/session [model]` - Create new session (optional: with model)
`/sessions` - List all sessions
`/status` - Get session status
`/use <id>` - Switch to session
`/prompt <message>` - Send prompt to current session
`/shell <command>` - Execute shell command
`/diff [session_id]` - Get session diff
`/todo [session_id]` - Get todo list

🛠️ *Session Management*
`/fork <session_id>` - Fork session
`/abort <session_id>` - Abort running session
`/delete <session_id>` - Delete session
`/share <session_id>` - Share session
`/unshare <session_id>` - Unshare session
`/revert <message_id>` - Revert a message
`/unrevert [session_id]` - Restore reverted messages
`/summarize [session_id]` - Summarize session

⚙️ *Configuration*
`/config` - Get current config
`/models` - List available models/providers
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
            "files": self.cmd_files,
            "read": self.cmd_read,
            "find": self.cmd_find,
            "findfile": self.cmd_findfile,
            "find-symbol": self.cmd_find_symbol,
            "sessions": self.cmd_sessions,
            "session": self.cmd_session,
            "status": self.cmd_status,
            "use": self.cmd_use,
            "switch": self.cmd_use,  # Alias
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
            "set-model": self.cmd_set_model,
            "use-model": self.cmd_set_model,  # Alias
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

    async def cmd_directory(self, args: str) -> str:
        if not args:
            path_info = await self.opencode.get_path()
            path = path_info.get("path", "unknown")
            return f"📂 *Current Directory*\n\n`{path}`\n\nUsage: `/directory <path>` to set a new working directory"

        # Need an active session to change directory
        if not self.current_session_id:
            return "❌ No active session. Use `/session` to create one or `/use <id>` to select one first."

        # Send cd command via shell
        import shlex
        quoted_path = shlex.quote(args)
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

    async def cmd_sessions(self, args: str) -> str:
        sessions = await self.opencode.list_sessions()
        if not sessions:
            return "No sessions found."

        status_dict = await self.opencode.get_session_status()

        lines = ["📝 *Sessions*"]
        for i, s in enumerate(sessions[:15], 1):  # Limit to 15 sessions
            session_id = s.get("id", "unknown")[:8]
            title = s.get("title", "Untitled")
            parent_id = s.get("parentID")
            status = status_dict.get(s.get("id", ""), {}).get("type", "unknown")
            is_current = s.get("id") == self.current_session_id

            prefix = "👉" if is_current else f"{i}."
            parent_note = " [subagent]" if parent_id else ""
            status_emoji = {"busy": "🔴", "idle": "🟢", "unknown": "⚪"}.get(status, "⚪")

            lines.append(f"{prefix} `{session_id}`{parent_note} {status_emoji}\n   {title}")
        if len(sessions) > 15:
            lines.append(f"\n... and {len(sessions) - 15} more sessions")

        lines.append(f"\n*Current:* `{self.current_session_id[:8] if self.current_session_id else 'None'}`")
        lines.append("Use `/use <id>` to switch sessions")
        return "\n".join(lines)

    async def cmd_session(self, args: str) -> str:
        # Parse optional model
        model = None
        provider = None
        if args:
            # Check if args contains a model specification
            if "/" in args:
                parts = args.split("/", 1)
                provider = parts[0]
                model = parts[1]
            else:
                model = args

        try:
            session = await self.opencode.create_session(title=model or "New Session")
            self.current_session_id = session["id"]
            short_id = session["id"][:8]
            model_info = f" using model {provider}/{model}" if model else ""
            return f"✅ Created new session `{short_id}`{model_info}\n\nUse `/use <id>` to switch sessions later."
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
                session_short = session_id[:8]
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
            session_short = args[:8]
            status = status_dict.get(args, {})
            if not status:
                return f"❌ Session `{session_short}` not found."

            status_type = status.get("type", "unknown")
            return f"📊 *Session `{session_short}` Status*\n\nStatus: **{status_type}**"

    async def cmd_use(self, args: str) -> str:
        if not args:
            return "❌ Usage: `/use <session_id>`"

        sessions = await self.opencode.list_sessions()
        session_ids = [sid for s in sessions if (sid := s.get("id"))]

        # Try exact match first
        if args in session_ids:
            self.current_session_id = args
            return f"✅ Switched to session `{args[:8]}`"

        # Try partial match
        matching = [sid for sid in session_ids if sid.startswith(args)]
        if len(matching) == 1:
            self.current_session_id = matching[0]
            return f"✅ Switched to session `{matching[0][:8]}`"
        elif len(matching) > 1:
            return f"⚠️ Multiple sessions match. Please use full session ID:\n" + "\n".join([f"- `{s[:8]}`" for s in matching[:5]])
        else:
            return f"❌ Session not found: `{args}`"

    async def cmd_prompt(self, args: str) -> str:
        if not args:
            return "❌ Usage: `/prompt <message>`"

        if not self.current_session_id:
            return "❌ No active session. Use `/session` to create one or `/use <id>` to select one."

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
            return "❌ No active session. Use `/session` to create one or `/use <id>` to select one."

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
        except Exception as e:
            return f"❌ Error running shell: {str(e)}"

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

    async def cmd_delete(self, args: str) -> str:
        if not args:
            return "❌ Usage: `/delete <session_id>`"

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
            return "❌ No active session. Use `/use <id>` to select one."

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

    async def cmd_models(self, args: str) -> str:
        try:
            providers = await self.opencode.get_providers()
            lines = ["🤖 *Available Models*"]

            for provider in providers.get("providers", []):
                provider_id = provider.get("id", "unknown")
                provider_name = provider.get("name", "Unknown")
                models = provider.get("models", [])
                if models:
                    lines.append(f"\n**{provider_name}** (`{provider_id}`)")
                    for model in models[:5]:  # Limit to 5 models per provider
                        model_id = model.get("id", "unknown")
                        lines.append(f"  - `{model_id}`")
                    if len(models) > 5:
                        lines.append(f"  ... and {len(models) - 5} more models")

            return "\n".join(lines)
        except Exception as e:
            return f"❌ Error getting models: {str(e)}"

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
            return "❌ No active session. Use `/use <id>` to select one."

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

    async def cmd_set_model(self, args: str) -> str:
        if not args:
            return "❌ Usage: `/set-model <provider/model>`\n\nExample: `/set-model deepseek/deepseek-reasoner`"

        # This is informational - the model is set per message
        return f"ℹ️ To use a specific model, include it when sending prompts:\n\n`/prompt <message> --model {args}`\n\nOr create a new session:\n`/session {args}`"
