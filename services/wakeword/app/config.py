"""Wake-word service configuration, loaded from environment variables / .env."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SAMPLE_RATE = 16_000
CHUNK_SAMPLES = 1280  # openWakeWord's native 80 ms frame


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]

    # Pretrained openWakeWord models to offer: alexa, hey_jarvis, hey_mycroft, hey_rhasspy, ...
    wakeword_models: list[str] = ["hey_jarvis", "alexa", "hey_mycroft"]
    wakeword_default: str = "hey_jarvis"
    # Directory scanned for custom-trained *.onnx models (file stem = model name).
    wakeword_custom_dir: str = "models/wakeword"
    wakeword_threshold: float = Field(default=0.5, gt=0.0, lt=1.0)
    wakeword_cooldown_s: float = Field(default=2.0, ge=0.0)
    # Silero VAD gate inside openWakeWord (0 disables); reduces false wakes from music/TV.
    wakeword_vad_threshold: float = Field(default=0.0, ge=0.0, lt=1.0)
    wakeword_download: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
