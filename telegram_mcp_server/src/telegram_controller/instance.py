"""OpenCode instance data model."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class InstanceState(str, Enum):
    """State of an OpenCode instance."""
    
    STARTING = "starting"      # Process spawned, waiting for HTTP API
    RUNNING = "running"        # HTTP API responding, ready for commands
    STOPPING = "stopping"      # Graceful shutdown initiated
    STOPPED = "stopped"        # Process terminated cleanly
    CRASHED = "crashed"        # Process died unexpectedly
    UNREACHABLE = "unreachable"  # Process running but HTTP API not responding


@dataclass
class OpenCodeInstance:
    """Represents a managed OpenCode instance.
    
    Each instance runs in a specific directory and listens on a unique port.
    Multiple instances can run simultaneously for different projects.
    """
    
    # Unique identifier for this instance (UUID or short hash)
    id: str
    
    # Working directory for this OpenCode instance
    directory: Path
    
    # HTTP API port (e.g., 4096, 4097, ...)
    port: int
    
    # Current state of the instance
    state: InstanceState = InstanceState.STOPPED
    
    # Process ID when running
    pid: Optional[int] = None
    
    # asyncio Process handle (not serializable)
    process: Optional[asyncio.subprocess.Process] = field(default=None, repr=False)
    
    # When the instance was started
    started_at: Optional[datetime] = None
    
    # Last successful health check
    last_health_check: Optional[datetime] = None
    
    # Number of consecutive health check failures
    health_check_failures: int = 0
    
    # Model configuration for this instance
    provider_id: str = "deepseek"
    model_id: str = "deepseek-reasoner"
    
    # Human-readable name/label for this instance
    name: Optional[str] = None
    
    # Active OpenCode session ID (if any)
    session_id: Optional[str] = None
    
    # Number of times this instance has been restarted
    restart_count: int = 0
    
    # Error message if crashed
    error_message: Optional[str] = None
    
    # Whether browser has been opened for this instance
    browser_opened: bool = False
    
    @property
    def url(self) -> str:
        """Get the HTTP API URL for this instance."""
        return f"http://localhost:{self.port}"
    
    @property
    def display_name(self) -> str:
        """Get a display name for this instance."""
        if self.name:
            return self.name
        return self.directory.name or str(self.directory)
    
    @property
    def short_id(self) -> str:
        """Get a short version of the instance ID."""
        return self.id[:8]
    
    @property
    def is_alive(self) -> bool:
        """Check if the instance is considered alive."""
        return self.state in (InstanceState.STARTING, InstanceState.RUNNING)
    
    @property
    def uptime_seconds(self) -> Optional[float]:
        """Get uptime in seconds if started."""
        if self.started_at is None:
            return None
        return (datetime.now() - self.started_at).total_seconds()
    
    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for persistence.
        
        Note: process handle is not serializable.
        """
        return {
            "id": self.id,
            "directory": str(self.directory),
            "port": self.port,
            "state": self.state.value,
            "pid": self.pid,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_health_check": self.last_health_check.isoformat() if self.last_health_check else None,
            "health_check_failures": self.health_check_failures,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "name": self.name,
            "session_id": self.session_id,
            "restart_count": self.restart_count,
            "error_message": self.error_message,
            "browser_opened": self.browser_opened,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OpenCodeInstance":
        """Deserialize from dictionary."""
        started_at = None
        if data.get("started_at"):
            started_at = datetime.fromisoformat(data["started_at"])
        
        last_health_check = None
        if data.get("last_health_check"):
            last_health_check = datetime.fromisoformat(data["last_health_check"])
        
        return cls(
            id=data["id"],
            directory=Path(data["directory"]),
            port=data["port"],
            state=InstanceState(data.get("state", "stopped")),
            pid=data.get("pid"),
            started_at=started_at,
            last_health_check=last_health_check,
            health_check_failures=data.get("health_check_failures", 0),
            provider_id=data.get("provider_id", "deepseek"),
            model_id=data.get("model_id", "deepseek-reasoner"),
            name=data.get("name"),
            session_id=data.get("session_id"),
            restart_count=data.get("restart_count", 0),
            error_message=data.get("error_message"),
            browser_opened=data.get("browser_opened", False),
        )
    
    def __hash__(self) -> int:
        """Hash by ID for use in sets/dicts."""
        return hash(self.id)
    
    def __eq__(self, other: object) -> bool:
        """Equality by ID."""
        if not isinstance(other, OpenCodeInstance):
            return False
        return self.id == other.id
