"""Code sandbox configuration, loaded from environment variables / .env."""

import sys
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    # Shared with the chat service; when set, every run must carry it as a bearer token.
    sandbox_token: str = ""
    sandbox_timeout_seconds: float = Field(default=10.0, gt=0.0, le=120.0)  # wall clock per run
    sandbox_memory_mb: int = Field(default=256, ge=32)  # address space per run
    sandbox_max_output: int = Field(default=65_536, ge=1_024)  # bytes kept of stdout and of stderr
    sandbox_python: str = sys.executable
    # Run without the operating system limits (memory, processes, file size), which only exist on
    # Linux and macOS. Only for tests and development: the Docker container is the real sandbox.
    sandbox_allow_unlimited: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
