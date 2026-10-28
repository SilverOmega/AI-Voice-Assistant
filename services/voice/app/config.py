"""Voice service configuration, loaded from environment variables / .env."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SAMPLE_RATE = 16_000  # PCM16 mono, used for all streamed microphone audio


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    chat_service_url: str = "http://localhost:8001"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]

    # Speech-to-text (faster-whisper)
    stt_model: str = "base.en"
    stt_device: Literal["cpu", "cuda", "auto"] = "cpu"
    stt_compute_type: str = "int8"  # "float16" is faster on CUDA GPUs
    stt_language: str = "en"  # empty string -> auto-detect
    stt_beam_size: int = Field(default=1, ge=1, le=10)
    stt_preload: bool = True
    stt_max_upload_mb: int = 25

    # Text-to-speech
    tts_engine: Literal["piper", "browser", "none"] = "piper"
    piper_voice: str = "en_US-lessac-medium"
    piper_data_dir: str = "models/piper"
    piper_auto_download: bool = True
    piper_length_scale: float = Field(default=1.0, gt=0.2, lt=3.0)  # >1 = slower speech

    # End-of-speech detection (energy based, adaptive to background noise)
    vad_min_threshold: float = 0.012  # RMS of float audio in [-1, 1]
    vad_noise_ratio: float = 3.0  # speech must be this many times louder than the noise floor
    vad_start_ms: int = 120  # loud audio needed to start an utterance
    vad_silence_ms: int = 800  # trailing silence that ends an utterance
    vad_preroll_ms: int = 300
    vad_min_utterance_ms: int = 300
    vad_max_utterance_s: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
