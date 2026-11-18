"""Claude bridge configuration, loaded from environment variables / .env."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    # Shared with the chat service. Required: without it the bridge answers nobody, because
    # anything that can reach it could otherwise use your Claude plan.
    claude_bridge_token: str = ""
    # The Claude Code program. Empty = find it: PATH, then ~/.local/bin, then the copy that
    # comes with the VS Code extension.
    claude_cli_path: str = ""
    claude_bridge_parallel: int = Field(default=2, ge=1, le=8)  # replies written at the same time
    claude_bridge_timeout_seconds: float = Field(default=300.0, ge=10.0)  # per reply


@lru_cache
def get_settings() -> Settings:
    return Settings()
