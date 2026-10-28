"""Chat service configuration, loaded from environment variables / .env."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["ollama", "anthropic", "echo"]

DEFAULT_SYSTEM_PROMPT = (
    "You are a friendly, concise voice and chat assistant running on the user's own computer. "
    "Answer clearly. Prefer short paragraphs; avoid long lists unless asked. "
    "When the user is speaking to you, your reply will be read aloud, so avoid markdown tables, "
    "code blocks and URLs unless they are explicitly requested."
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    database_path: str = "data/chat.db"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]

    llm_provider: ProviderName = "ollama"
    llm_model: str = ""  # empty -> provider default
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_history_messages: int = Field(default=30, ge=2, le=500)
    max_output_tokens: int = Field(default=4096, ge=64)

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    ollama_keep_alive: str = "24h"  # Go duration; "-1m" never unloads

    # Claude (Anthropic). Credentials are resolved by the SDK (ANTHROPIC_API_KEY etc.).
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    anthropic_fallbacks: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
