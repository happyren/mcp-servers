"""Configuration management for Telegram MCP Server."""

import os
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    bot_token: str = Field(
        ...,
        description="Telegram Bot API token from @BotFather",
    )
    chat_id: str = Field(
        ...,
        description="Default chat ID to send messages to (your user ID or group ID)",
    )
    api_base_url: str = Field(
        default="https://api.telegram.org",
        description="Telegram Bot API base URL",
    )
    polling_timeout: int = Field(
        default=30,
        description="Long polling timeout in seconds",
    )
    queue_dir: str = Field(
        default="~/.local/share/telegram_mcp_server",
        description="Directory for queue file storage",
    )
    commands_set: bool = Field(
        default=False,
        description="Whether bot commands have been registered with Telegram. Set to true after running set_bot_commands.",
    )
    favourite_models: str = Field(
        default="github-copilot/claude-sonnet-4,github-copilot/gpt-4.1,deepseek/deepseek-reasoner,deepseek/deepseek-chat,anthropic/claude-sonnet-4-20250514,github-copilot/claude-opus-4.5,github-copilot/claude-sonnet-4.5,moonshotai-cn/kimi-k2.5,minimax/minimax-m2.1,zhipuai-coding-plan/GLM-4.7",
        description="Comma-separated list of favourite models in 'provider/model' format. These are shown in /models and model picker.",
    )

    def get_favourite_models(self) -> list[tuple[str, str]]:
        """Parse favourite_models into a list of (provider, model) tuples."""
        if not self.favourite_models:
            return []
        result = []
        for item in self.favourite_models.split(","):
            item = item.strip()
            if "/" in item:
                parts = item.split("/", 1)
                result.append((parts[0].strip(), parts[1].strip()))
        return result

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        env_prefix = "TELEGRAM_"
        
    def __init__(self, **kwargs):
        # Load .env from the project root if not specified
        import os
        if not os.path.exists(self.__class__.Config.env_file):
            # Try to find .env in parent directories
            current_dir = os.path.dirname(os.path.abspath(__file__))
            # Go up: telegram_mcp_server/ -> src/ -> telegram_mcp_server/ (root)
            project_root = os.path.abspath(os.path.join(current_dir, "../.."))
            env_path = os.path.join(project_root, ".env")
            if os.path.exists(env_path):
                self.__class__.Config.env_file = env_path
        super().__init__(**kwargs)


def get_settings() -> Settings:
    """Get application settings, loading from environment."""
    return Settings()
