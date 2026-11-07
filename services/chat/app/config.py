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
    # Text of attached documents sent with one request, newest first. About 4 characters a token.
    document_max_chars: int = Field(default=40_000, ge=1_000)

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    ollama_keep_alive: str = "24h"  # Go duration; "-1m" never unloads
    ollama_num_thread: int = Field(default=0, ge=0)  # 0 -> Ollama default (one per core)
    # Largest context window (tokens) to ask Ollama for when a prompt outgrows its default,
    # e.g. with attached documents. More needs more memory; 0 leaves it to Ollama.
    ollama_max_ctx: int = Field(default=16384, ge=0)

    # Claude (Anthropic). Credentials are resolved by the SDK (ANTHROPIC_API_KEY etc.).
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    anthropic_fallbacks: bool = True

    # Task automation. Approved emails are sent over SMTP if it's set up; otherwise saved to the
    # mailbox's Drafts folder over IMAP if that's set up; otherwise they open ready to send in
    # Gmail or Outlook on the web, or in the computer's default mail app (mailto:).
    email_compose: Literal["gmail", "outlook", "mail_app"] = "gmail"
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""  # for Gmail, an app password
    imap_drafts_folder: str = ""  # empty -> the folder the server marks as Drafts
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""  # defaults to smtp_username
    # Workflows the assistant may trigger: JSON {"name": "webhook URL"}, e.g. n8n or Home Assistant.
    # Only these URLs are ever called, and only after the user approves.
    workflows: dict[str, str] = {}

    # Phone notifications when a draft waits for review (they only remind; approval stays in the app).
    notify_phone: str = Field(default="", pattern=r"^(\+[1-9]\d{6,14})?$")  # E.164, e.g. +84901234567
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from: str = ""  # a Twilio number (+1...) or a Messaging Service SID (MG...)
    ntfy_server: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    ntfy_token: str = ""
    notify_remind_minutes: int = Field(default=30, ge=0)  # remind again if still waiting; 0 = off
    app_url: str = ""  # link added to notifications, e.g. https://192.168.1.10:8443

    # Phones, tablets and other computers reach the app over HTTPS and must enter this passcode
    # once per device. The browser on this computer (http://localhost) never needs it.
    # Shorter than 6 characters counts as not set: other devices are then turned away.
    app_passcode: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
