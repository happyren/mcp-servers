"""Comprehensive OpenCode HTTP API client with all endpoints."""

import asyncio
import json
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class OpenCodeClient:
    """Comprehensive client for OpenCode's HTTP API."""

    def __init__(self, base_url: str = "http://localhost:4096"):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=300.0)

    async def close(self):
        await self.client.aclose()

    async def health_check(self) -> dict[str, Any]:
        """Check server health and version."""
        response = await self.client.get(f"{self.base_url}/global/health")
        response.raise_for_status()
        return response.json()

    # Project APIs
    async def list_projects(self) -> list[dict[str, Any]]:
        """List all projects."""
        response = await self.client.get(f"{self.base_url}/project")
        response.raise_for_status()
        return response.json()

    async def get_current_project(self) -> dict[str, Any]:
        """Get current project."""
        response = await self.client.get(f"{self.base_url}/project/current")
        response.raise_for_status()
        return response.json()

    # Path APIs
    async def get_path(self) -> dict[str, Any]:
        """Get current path information."""
        response = await self.client.get(f"{self.base_url}/path")
        response.raise_for_status()
        return response.json()

    # VCS APIs
    async def get_vcs_info(self) -> dict[str, Any]:
        """Get VCS info for current project."""
        response = await self.client.get(f"{self.base_url}/vcs")
        response.raise_for_status()
        return response.json()

    # Config APIs
    async def get_config(self) -> dict[str, Any]:
        """Get config info."""
        response = await self.client.get(f"{self.base_url}/config")
        response.raise_for_status()
        return response.json()

    async def update_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Update config."""
        response = await self.client.patch(f"{self.base_url}/config", json=config)
        response.raise_for_status()
        return response.json()

    async def get_providers(self) -> dict[str, Any]:
        """List providers and default models."""
        response = await self.client.get(f"{self.base_url}/config/providers")
        response.raise_for_status()
        return response.json()

    async def get_provider_list(self) -> dict[str, Any]:
        """List all providers."""
        response = await self.client.get(f"{self.base_url}/provider")
        response.raise_for_status()
        return response.json()

    async def get_provider_auth(self) -> dict[str, Any]:
        """Get provider authentication methods."""
        response = await self.client.get(f"{self.base_url}/provider/auth")
        response.raise_for_status()
        return response.json()

    # Session APIs
    async def list_sessions(self) -> list[dict[str, Any]]:
        """List all sessions."""
        response = await self.client.get(f"{self.base_url}/session")
        response.raise_for_status()
        return response.json()

    async def create_session(self, parent_id: Optional[str] = None, title: Optional[str] = None) -> dict[str, Any]:
        """Create a new session."""
        body: dict[str, Any] = {}
        if parent_id:
            body["parentID"] = parent_id
        if title:
            body["title"] = title
        response = await self.client.post(f"{self.base_url}/session", json=body)
        response.raise_for_status()
        return response.json()

    async def get_session_status(self) -> dict[str, Any]:
        """Get session status for all sessions."""
        response = await self.client.get(f"{self.base_url}/session/status")
        response.raise_for_status()
        return response.json()

    async def get_session(self, session_id: str) -> dict[str, Any]:
        """Get session details."""
        response = await self.client.get(f"{self.base_url}/session/{session_id}")
        response.raise_for_status()
        return response.json()

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session and all its data."""
        response = await self.client.delete(f"{self.base_url}/session/{session_id}")
        response.raise_for_status()
        return True

    async def update_session(self, session_id: str, title: Optional[str] = None) -> dict[str, Any]:
        """Update session properties."""
        body: dict[str, Any] = {}
        if title:
            body["title"] = title
        response = await self.client.patch(f"{self.base_url}/session/{session_id}", json=body)
        response.raise_for_status()
        return response.json()

    async def get_session_children(self, session_id: str) -> list[dict[str, Any]]:
        """Get a session's child sessions."""
        response = await self.client.get(f"{self.base_url}/session/{session_id}/children")
        response.raise_for_status()
        return response.json()

    async def get_session_todo(self, session_id: str) -> list[dict[str, Any]]:
        """Get the todo list for a session."""
        response = await self.client.get(f"{self.base_url}/session/{session_id}/todo")
        response.raise_for_status()
        return response.json()

    async def init_session(self, session_id: str, message_id: str, provider_id: str, model_id: str) -> bool:
        """Analyze app and create AGENTS.md."""
        response = await self.client.post(
            f"{self.base_url}/session/{session_id}/init",
            json={"messageID": message_id, "providerID": provider_id, "modelID": model_id},
        )
        response.raise_for_status()
        return True

    async def fork_session(self, session_id: str, message_id: Optional[str] = None) -> dict[str, Any]:
        """Fork an existing session at a message."""
        body: dict[str, Any] = {}
        if message_id:
            body["messageID"] = message_id
        response = await self.client.post(f"{self.base_url}/session/{session_id}/fork", json=body)
        response.raise_for_status()
        return response.json()

    async def abort_session(self, session_id: str) -> bool:
        """Abort a running session."""
        response = await self.client.post(f"{self.base_url}/session/{session_id}/abort")
        response.raise_for_status()
        return True

    async def share_session(self, session_id: str) -> dict[str, Any]:
        """Share a session."""
        response = await self.client.post(f"{self.base_url}/session/{session_id}/share")
        response.raise_for_status()
        return response.json()

    async def unshare_session(self, session_id: str) -> dict[str, Any]:
        """Unshare a session."""
        response = await self.client.delete(f"{self.base_url}/session/{session_id}/share")
        response.raise_for_status()
        return response.json()

    async def get_session_diff(self, session_id: str, message_id: Optional[str] = None) -> list[dict[str, Any]]:
        """Get the diff for this session."""
        params = {}
        if message_id:
            params["messageID"] = message_id
        response = await self.client.get(f"{self.base_url}/session/{session_id}/diff", params=params)
        response.raise_for_status()
        return response.json()

    async def summarize_session(self, session_id: str, provider_id: str, model_id: str) -> bool:
        """Summarize the session."""
        response = await self.client.post(
            f"{self.base_url}/session/{session_id}/summarize",
            json={"providerID": provider_id, "modelID": model_id},
        )
        response.raise_for_status()
        return True

    async def revert_message(self, session_id: str, message_id: str, part_id: Optional[str] = None) -> bool:
        """Revert a message."""
        body: dict[str, Any] = {"messageID": message_id}
        if part_id:
            body["partID"] = part_id
        response = await self.client.post(f"{self.base_url}/session/{session_id}/revert", json=body)
        response.raise_for_status()
        return True

    async def unrevert_messages(self, session_id: str) -> bool:
        """Restore all reverted messages."""
        response = await self.client.post(f"{self.base_url}/session/{session_id}/unrevert")
        response.raise_for_status()
        return True

    async def respond_permission(self, session_id: str, permission_id: str, response: bool, remember: Optional[bool] = None) -> bool:
        """Respond to a permission request."""
        body: dict[str, Any] = {"response": response}
        if remember is not None:
            body["remember"] = remember
        resp = await self.client.post(
            f"{self.base_url}/session/{session_id}/permissions/{permission_id}",
            json=body,
        )
        resp.raise_for_status()
        return True

    # Message APIs
    async def get_messages(self, session_id: str, limit: Optional[int] = None) -> list[dict[str, Any]]:
        """List messages in a session."""
        params = {}
        if limit:
            params["limit"] = limit
        response = await self.client.get(f"{self.base_url}/session/{session_id}/message", params=params)
        response.raise_for_status()
        return response.json()

    async def send_message(
        self,
        session_id: str,
        message: str,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
        agent: Optional[str] = None,
        no_reply: bool = False,
    ) -> dict[str, Any]:
        """Send a message and wait for response."""
        body: dict[str, Any] = {"parts": [{"type": "text", "text": message}]}
        if no_reply:
            body["noReply"] = True
        if provider_id and model_id:
            body["model"] = {"providerID": provider_id, "modelID": model_id}
        if agent:
            body["agent"] = agent

        response = await self.client.post(
            f"{self.base_url}/session/{session_id}/message",
            json=body,
            timeout=600.0,
        )
        if response.status_code == 204:
            return {"info": {}, "parts": []}
        response.raise_for_status()
        return response.json()

    async def send_message_async(self, session_id: str, message: str) -> None:
        """Send a message asynchronously (no wait)."""
        await self.client.post(
            f"{self.base_url}/session/{session_id}/prompt_async",
            json={"parts": [{"type": "text", "text": message}]},
        )

    async def get_message(self, session_id: str, message_id: str) -> dict[str, Any]:
        """Get message details."""
        response = await self.client.get(f"{self.base_url}/session/{session_id}/message/{message_id}")
        response.raise_for_status()
        return response.json()

    async def send_command(
        self,
        session_id: str,
        command: str,
        arguments: Optional[dict[str, Any]] = None,
        agent: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Execute a slash command."""
        body: dict[str, Any] = {"command": command}
        if arguments:
            body["arguments"] = arguments
        if agent:
            body["agent"] = agent
        if model_id:
            body["model"] = model_id
        response = await self.client.post(f"{self.base_url}/session/{session_id}/command", json=body)
        response.raise_for_status()
        return response.json()

    async def send_shell(self, session_id: str, command: str, agent: Optional[str] = None) -> dict[str, Any]:
        """Run a shell command."""
        body: dict[str, Any] = {"command": command}
        if agent:
            body["agent"] = agent
        response = await self.client.post(f"{self.base_url}/session/{session_id}/shell", json=body)
        response.raise_for_status()
        return response.json()

    # Command APIs
    async def list_commands(self) -> list[dict[str, Any]]:
        """List all commands."""
        response = await self.client.get(f"{self.base_url}/command")
        response.raise_for_status()
        return response.json()

    # File APIs
    async def search_text(self, pattern: str) -> list[dict[str, Any]]:
        """Search for text in files."""
        response = await self.client.get(f"{self.base_url}/find", params={"pattern": pattern})
        response.raise_for_status()
        return response.json()

    async def find_files(self, query: str, type_filter: Optional[str] = None, directory: Optional[str] = None, limit: Optional[int] = None) -> list[str]:
        """Find files and directories by name."""
        params: dict[str, Any] = {"query": query}
        if type_filter:
            params["type"] = type_filter
        if directory:
            params["directory"] = directory
        if limit:
            params["limit"] = limit
        response = await self.client.get(f"{self.base_url}/find/file", params=params)
        response.raise_for_status()
        return response.json()

    async def find_symbols(self, query: str) -> list[dict[str, Any]]:
        """Find workspace symbols."""
        response = await self.client.get(f"{self.base_url}/find/symbol", params={"query": query})
        response.raise_for_status()
        return response.json()

    async def list_files(self, path: Optional[str] = None) -> list[dict[str, Any]]:
        """List files and directories."""
        params = {}
        if path:
            params["path"] = path
        response = await self.client.get(f"{self.base_url}/file", params=params)
        response.raise_for_status()
        return response.json()

    async def read_file(self, path: str) -> dict[str, Any]:
        """Read a file."""
        response = await self.client.get(f"{self.base_url}/file/content", params={"path": path})
        response.raise_for_status()
        return response.json()

    async def get_file_status(self) -> list[dict[str, Any]]:
        """Get status for tracked files."""
        response = await self.client.get(f"{self.base_url}/file/status")
        response.raise_for_status()
        return response.json()

    # Agent APIs
    async def list_agents(self) -> list[dict[str, Any]]:
        """List all available agents."""
        response = await self.client.get(f"{self.base_url}/agent")
        response.raise_for_status()
        return response.json()

    # LSP, Formatters & MCP APIs
    async def get_lsp_status(self) -> list[dict[str, Any]]:
        """Get LSP server status."""
        response = await self.client.get(f"{self.base_url}/lsp")
        response.raise_for_status()
        return response.json()

    async def get_formatter_status(self) -> list[dict[str, Any]]:
        """Get formatter status."""
        response = await self.client.get(f"{self.base_url}/formatter")
        response.raise_for_status()
        return response.json()

    async def get_mcp_status(self) -> dict[str, Any]:
        """Get MCP server status."""
        response = await self.client.get(f"{self.base_url}/mcp")
        response.raise_for_status()
        return response.json()

    async def add_mcp_server(self, name: str, config: dict[str, Any]) -> dict[str, Any]:
        """Add MCP server dynamically."""
        response = await self.client.post(
            f"{self.base_url}/mcp",
            json={"name": name, "config": config},
        )
        response.raise_for_status()
        return response.json()

    # Logging APIs
    async def write_log(self, service: str, level: str, message: str, extra: Optional[dict[str, Any]] = None) -> bool:
        """Write log entry."""
        body: dict[str, Any] = {"service": service, "level": level, "message": message}
        if extra:
            body["extra"] = extra
        response = await self.client.post(f"{self.base_url}/log", json=body)
        response.raise_for_status()
        return True

    # Auth APIs
    async def set_auth(self, provider_id: str, credentials: dict[str, Any]) -> bool:
        """Set authentication credentials."""
        response = await self.client.put(f"{self.base_url}/auth/{provider_id}", json=credentials)
        response.raise_for_status()
        return True

    # Instance APIs
    async def dispose_instance(self) -> bool:
        """Dispose the current instance."""
        response = await self.client.post(f"{self.base_url}/instance/dispose")
        response.raise_for_status()
        return True

    # Helper methods for bridge
    async def is_server_running(self) -> bool:
        """Check if OpenCode server is running."""
        try:
            response = await self.client.get(f"{self.base_url}/session")
            return response.status_code == 200
        except Exception:
            return False

    async def get_existing_session(self) -> str | None:
        """Get an existing session ID without creating a new one.

        Returns the first available main session (not subagent) or None if no sessions exist.
        """
        try:
            sessions = await self.list_sessions()
            logger.debug(f"Sessions response: {sessions}")
            if sessions and isinstance(sessions, list) and len(sessions) > 0:
                # Filter to sessions without parentID (main sessions, not subagents)
                main_sessions = [s for s in sessions if not s.get("parentID")]
                if main_sessions:
                    session_id = main_sessions[0]["id"]
                    logger.info(f"Found existing session: {session_id}")
                    return session_id
                # Fall back to first session if all are subagents
                session_id = sessions[0]["id"]
                logger.info(f"Found existing session (subagent): {session_id}")
                return session_id
            return None
        except Exception as e:
            logger.error(f"Failed to get existing session: {e}")
            return None

    async def get_or_create_session(self) -> str | None:
        """Get existing session ID or create a new one."""
        try:
            session_id = await self.get_existing_session()
            if session_id:
                return session_id
            # Create new session
            logger.info("No sessions found, creating new one")
            session = await self.create_session()
            return session["id"]
        except Exception as e:
            logger.error(f"Failed to get/create session: {e}")
            return None

    async def is_session_idle(self, session_id: str) -> bool:
        """Check if a session is idle (not busy)."""
        try:
            status = await self.get_session_status()
            session_status = status.get(session_id, {})
            return session_status.get("type") != "busy"
        except Exception:
            return False

    async def get_or_create_telegram_session(self) -> str | None:
        """Get or create a dedicated session for Telegram messages."""
        try:
            sessions = await self.list_sessions()
            status = await self.get_session_status()

            # Look for an idle main session
            for session in sessions:
                if session.get("parentID"):
                    continue  # Skip subagent sessions
                session_id = session["id"]
                session_status = status.get(session_id, {})
                if session_status.get("type") != "busy":
                    logger.info(f"Found idle session: {session_id}")
                    return session_id

            # All sessions are busy, create a new one
            logger.info("All sessions busy, creating new session for Telegram")
            session = await self.create_session()
            return session["id"]
        except Exception as e:
            logger.error(f"Failed to get/create telegram session: {e}")
            return None

    async def send_prompt_async(self, session_id: str, prompt: str) -> None:
        """Send a prompt to a session asynchronously (non-blocking)."""
        response = await self.client.post(
            f"{self.base_url}/session/{session_id}/prompt_async",
            json={"parts": [{"type": "text", "text": prompt}]},
        )
        response.raise_for_status()
        # Note: Returns 204 No Content on success

    async def send_message_text(
        self,
        session_id: str,
        message: str,
        provider_id: str = "deepseek",
        model_id: str = "deepseek-reasoner",
    ) -> str:
        """Send a message and wait for response (blocking). Returns response text."""
        response = await self.client.post(
            f"{self.base_url}/session/{session_id}/message",
            json={
                "parts": [{"type": "text", "text": message}],
                "model": {"providerID": provider_id, "modelID": model_id},
            },
            timeout=300.0,  # 5 minute timeout for long responses
        )
        response.raise_for_status()
        # May return empty on 204
        if response.status_code == 204:
            return ""

        data = response.json()

        # Check for errors
        info = data.get("info", {})
        if info.get("error"):
            error_msg = info["error"].get("data", {}).get("message", "Unknown error")
            logger.error(f"OpenCode error: {error_msg}")
            return f"Error: {error_msg[:200]}"

        # Extract text from response parts
        # Response format: { info: Message, parts: Part[] }
        parts = data.get("parts", [])
        text_parts = []
        for part in parts:
            if part.get("type") == "text":
                text_parts.append(part.get("text", ""))

        return "\n".join(text_parts) if text_parts else ""
