"""Chat service configuration, loaded from environment variables / .env."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["ollama", "anthropic", "claude_cli", "echo"]

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

    # Claude through your Claude subscription (Pro/Max) instead of an API key: Claude Code on
    # this computer, reached through the Claude bridge (services/bridge, scripts/bridge.ps1).
    claude_bridge_url: str = "http://127.0.0.1:8005"  # empty = off
    claude_bridge_token: str = ""  # shared with the bridge; scripts/bridge.ps1 creates one
    claude_cli_model: str = "claude-sonnet-5-5"

    # Web search for chat turns: links and related topics under replies, and results the model
    # answers from. "searxng" uses the SearXNG container in docker-compose (no account);
    # "brave" the Brave Search API (BRAVE_API_KEY); "off" turns it off.
    web_search: Literal["searxng", "brave", "off"] = "searxng"
    searxng_url: str = "http://localhost:8888"
    brave_api_key: str = ""
    web_search_results: int = Field(default=5, ge=1, le=10)

    # Exchange rates against VND and gold prices for the sidebar's Rates panel, fetched when
    # someone opens it and kept for RATES_CACHE_MINUTES. An empty URL hides that part.
    rates_currencies: str = "USD,EUR,JPY,GBP,AUD,SGD,KRW,CNY,THB"
    rates_cache_minutes: int = Field(default=10, ge=1)
    rates_fx_url: str = "https://portal.vietcombank.com.vn/Usercontrols/TVPortal.TyGia/pXML.aspx"
    rates_gold_url: str = "https://edge-api.pnj.io/ecom-frontend/v1/get-gold-price?zone=00"
    rates_world_gold_url: str = "https://api.gold-api.com/price/XAU"

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

    # Agents: a lead assistant plans the request and hands parts of it to helpers (research,
    # documents, tasks, code) that use tools, then writes the reply. "toggle" adds an Agents
    # switch to the message box; "always" uses them for every message (small talk skips them);
    # "off" turns them off. Helpers only read and draft: anything that sends, saves, runs code or
    # changes a file waits for the user's approval like any other task.
    agent_mode: Literal["off", "toggle", "always"] = "toggle"
    agent_max_steps: int = Field(default=4, ge=1, le=10)  # helpers the lead may call per message
    agent_max_tool_calls: int = Field(default=6, ge=1, le=30)  # tool uses per helper
    agent_timeout_seconds: float = Field(default=180.0, ge=10.0)  # then it answers with what it has
    # The code researcher helper reads public GitHub repositories (commits, diffs, files), and
    # starts on its own for typed questions about one. A token (no scopes needed) raises GitHub's
    # limit from about 60 lookups an hour to 5,000.
    github_lookup: bool = True
    github_token: str = ""
    # A project folder the code helper may read, and change after approval. Empty = none.
    project_dir: str = ""
    # The code sandbox service (services/sandbox) that runs approved Python. Empty = no code runs.
    sandbox_url: str = ""
    sandbox_token: str = ""

    # Phones, tablets and other computers reach the app over HTTPS and must enter this passcode
    # once per device. The browser on this computer (http://localhost) never needs it.
    # Shorter than 6 characters counts as not set: other devices are then turned away.
    app_passcode: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
