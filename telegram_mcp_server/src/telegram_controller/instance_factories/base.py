"""Base instance factory for multi-bot support.

This module defines the abstract factory interface for creating
different types of agent instances (OpenCode, QuantCode, custom).
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..instance import InstanceState, OpenCodeInstance


class InstanceFactory(ABC):
    """Abstract factory for creating agent instances.
    
    Subclasses implement instance creation for specific agent types
    (OpenCode, QuantCode, custom agents, etc.)
    
    Example usage:
        factory = OpenCodeInstanceFactory()
        instance = await factory.create(
            instance_id="abc123",
            directory=Path("/path/to/project"),
            port=4097,
            config={"provider_id": "deepseek"}
        )
    """
    
    @property
    @abstractmethod
    def instance_type(self) -> str:
        """Get the type identifier for this factory.
        
        Returns:
            Type identifier (e.g., 'opencode', 'quantcode')
        """
        pass
    
    @property
    @abstractmethod
    def command_prefix(self) -> str:
        """Get the command prefix for this instance type.
        
        Returns:
            Command prefix (e.g., 'opencode', 'quantcode')
        """
        pass
    
    @property
    def default_config(self) -> Dict[str, Any]:
        """Get default configuration for this instance type.
        
        Returns:
            Default configuration dictionary
        """
        return {}
    
    @abstractmethod
    async def create(
        self,
        instance_id: str,
        directory: Path,
        port: int,
        config: Dict[str, Any],
    ) -> OpenCodeInstance:
        """Create and start an agent instance.
        
        Args:
            instance_id: Unique instance identifier
            directory: Working directory
            port: HTTP API port
            config: Instance-specific configuration
            
        Returns:
            Created instance
        """
        pass
    
    @abstractmethod
    async def health_check(self, instance: OpenCodeInstance) -> bool:
        """Check if instance is healthy.
        
        Args:
            instance: Instance to check
            
        Returns:
            True if healthy
        """
        pass
    
    async def stop(self, instance: OpenCodeInstance) -> bool:
        """Stop an instance gracefully.
        
        Args:
            instance: Instance to stop
            
        Returns:
            True if stopped successfully
        """
        if instance.process:
            try:
                instance.process.terminate()
                await instance.process.wait()
                instance.state = InstanceState.STOPPED
                return True
            except Exception:
                return False
        return False
    
    def get_spawn_command(self, instance: OpenCodeInstance, config: Dict[str, Any]) -> List[str]:
        """Get the command to spawn this instance type.
        
        Args:
            instance: Instance being created
            config: Instance configuration
            
        Returns:
            Command as list of strings
        """
        raise NotImplementedError("Subclass must implement get_spawn_command")
    
    def get_env_vars(self, config: Dict[str, Any]) -> Dict[str, str]:
        """Get environment variables for spawning.
        
        Args:
            config: Instance configuration
            
        Returns:
            Environment variables dictionary
        """
        return {}
