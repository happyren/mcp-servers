"""Configuration schema for multi-bot controller.

This module provides configuration loading and validation for
the multi-bot controller with support for different instance types.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, Field, field_validator


class BotConfig(BaseModel):
    """Configuration for a single bot."""
    
    name: str = Field(..., description="Human-readable bot name")
    token: str = Field(..., description="Telegram bot token")
    type: str = Field(..., description="Instance type (opencode, quantcode)")
    chat_id: Optional[int] = Field(None, description="Allowed chat ID (optional)")
    config: Dict[str, Any] = Field(default_factory=dict, description="Type-specific config")
    
    @field_validator("token")
    @classmethod
    def validate_token(cls, v: str) -> str:
        """Validate and expand bot token."""
        if v.startswith("${") and v.endswith("}"):
            env_var = v[2:-1]
            expanded = os.environ.get(env_var)
            if not expanded:
                raise ValueError(f"Environment variable {env_var} not set")
            return expanded
        return v


class ControllerSettings(BaseModel):
    """Global controller settings."""
    
    state_dir: Path = Field(
        default=Path("~/.local/share/telegram_controller"),
        description="Directory for state persistence"
    )
    port_range_start: int = Field(default=4097, description="Start of port range")
    port_range_end: int = Field(default=4200, description="End of port range")
    default_provider: str = Field(default="deepseek", description="Default AI provider")
    default_model: str = Field(default="deepseek-reasoner", description="Default AI model")
    health_check_interval: int = Field(default=30, description="Seconds between health checks")
    
    @field_validator("state_dir")
    @classmethod
    def expand_path(cls, v: Path) -> Path:
        return v.expanduser()


class InstanceTypeConfig(BaseModel):
    """Configuration for an instance type."""
    
    factory: str = Field(..., description="Factory class path")
    default_config: Dict[str, Any] = Field(default_factory=dict)


class ChatRouting(BaseModel):
    """Routing rule for a specific chat."""
    
    chat_id: int
    bot_name: str


class ForumRouting(BaseModel):
    """Routing rule for forum topics."""
    
    chat_id: int
    topic_id: int
    bot_name: str


class RoutingConfig(BaseModel):
    """Message routing configuration."""
    
    chat_routing: List[ChatRouting] = Field(default_factory=list)
    forum_routing: List[ForumRouting] = Field(default_factory=list)


class MultiBotConfig(BaseModel):
    """Complete multi-bot controller configuration."""
    
    controller: ControllerSettings = Field(default_factory=ControllerSettings)
    bots: List[BotConfig] = Field(default_factory=list)
    instance_types: Dict[str, InstanceTypeConfig] = Field(default_factory=dict)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    
    def get_bot_by_name(self, name: str) -> Optional[BotConfig]:
        """Get a bot configuration by name."""
        for bot in self.bots:
            if bot.name == name:
                return bot
        return None
    
    def get_bot_by_token(self, token: str) -> Optional[BotConfig]:
        """Get a bot configuration by token."""
        for bot in self.bots:
            if bot.token == token:
                return bot
        return None
    
    def get_bot_for_chat(self, chat_id: int) -> Optional[BotConfig]:
        """Get the bot assigned to a specific chat."""
        for rule in self.routing.chat_routing:
            if rule.chat_id == chat_id:
                return self.get_bot_by_name(rule.bot_name)
        return None
    
    def get_bot_for_topic(self, chat_id: int, topic_id: int) -> Optional[BotConfig]:
        """Get the bot assigned to a specific forum topic."""
        for rule in self.routing.forum_routing:
            if rule.chat_id == chat_id and rule.topic_id == topic_id:
                return self.get_bot_by_name(rule.bot_name)
        return None


def _expand_env_vars(obj: Any) -> Any:
    """Recursively expand environment variables in a configuration object."""
    if isinstance(obj, str):
        # Match ${VAR_NAME} pattern
        pattern = r'\$\{([^}]+)\}'
        
        def replacer(match: re.Match[str]) -> str:
            var_name = match.group(1)
            return os.environ.get(var_name, match.group(0))
        
        return re.sub(pattern, replacer, obj)
    
    elif isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    
    elif isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    
    return obj


def load_config(config_path: str) -> MultiBotConfig:
    """Load and validate configuration from YAML file.
    
    Args:
        config_path: Path to configuration file
        
    Returns:
        MultiBotConfig instance
        
    Raises:
        ValueError: If configuration is invalid
        FileNotFoundError: If config file doesn't exist
    """
    config_file = Path(config_path)
    
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(config_file, 'r') as f:
        raw_config = yaml.safe_load(f)
    
    # Expand environment variables
    expanded_config = _expand_env_vars(raw_config)
    
    return MultiBotConfig(**expanded_config)


def get_default_config() -> MultiBotConfig:
    """Get default configuration.
    
    Returns:
        Default MultiBotConfig
    """
    return MultiBotConfig(
        controller=ControllerSettings(),
        bots=[],
        instance_types={
            "opencode": InstanceTypeConfig(
                factory="telegram_controller.instance_factories.OpenCodeInstanceFactory",
                default_config={"provider_id": "deepseek", "model_id": "deepseek-reasoner"}
            ),
            "quantcode": InstanceTypeConfig(
                factory="telegram_controller.instance_factories.QuantCodeInstanceFactory",
                default_config={"server_path": "python"}
            ),
        },
        routing=RoutingConfig(),
    )
