"""Configuration management for Coding Convention MCP Server."""

import os

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    storage_path: str = Field(
        default="~/.local/share/coding_convention_mcp_server",
        description="Directory for storing coding convention data",
    )
    storage_type: str = Field(
        default="sqlite",
        description="Storage type: sqlite or json",
    )
    analysis_depth: int = Field(
        default=100,
        description="Maximum number of files to analyze per repository",
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        env_prefix = "CODING_CONVENTION_"

    def __init__(self, **kwargs):
        # Load .env from the project root if not specified
        if not os.path.exists(self.__class__.Config.env_file):
            # Try to find .env in parent directories
            current_dir = os.path.dirname(os.path.abspath(__file__))
            # Go up: coding_convention_mcp_server/ -> src/ -> coding_convention_mcp_server/ (root)
            project_root = os.path.abspath(os.path.join(current_dir, "../.."))
            env_path = os.path.join(project_root, ".env")
            if os.path.exists(env_path):
                self.__class__.Config.env_file = env_path
        super().__init__(**kwargs)


def get_settings() -> Settings:
    """Get application settings, loading from environment."""
    return Settings()
