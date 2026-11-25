"""Speech engines: faster-whisper for STT, Piper for TTS.

Both load lazily (models are large) and are thread-safe so blocking inference can run
in worker threads via asyncio.to_thread without stalling the event loop.
"""

import io
import logging
import re
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Protocol

import numpy as np

from .config import SAMPLE_RATE, Settings

log = logging.getLogger(__name__)

# Text Whisper may write for one clip: generous for fast speech, short enough to cut a loop.
TOKENS_PER_SECOND = 8
MAX_NEW_TOKENS = 400  # Whisper's own limit is 448 including the prompt
# A model that ran within this many seconds is still quick; warm_up() skips it.
WARM_FOR_S = 120

# Whisper tends to "hear" these in silence or noise.
_HALLUCINATIONS = {"", "you", "thank you.", "thanks for watching!", "thank you for watching.", "."}
VOICE_NAME = re.compile(r"^[a-z]{2,3}_[A-Z]{2}-[A-Za-z0-9_]+-(x_low|low|medium|high)$")
LANGUAGE_CODE = re.compile(r"^[a-z]{2,3}$")

# A Piper voice per language (https://huggingface.co/rhasspy/piper-voices), downloaded the first
# time it's needed. Languages missing here (Japanese, Korean, Thai...) have no Piper voice.
PIPER_VOICES = {
    "ar": "ar_JO-kareem-medium",
    "ca": "ca_ES-upc_ona-medium",
    "cs": "cs_CZ-jirka-medium",
    "da": "da_DK-talesyntese-medium",
    "de": "de_DE-thorsten-medium",
    "el": "el_GR-rapunzelina-low",
    "en": "en_US-lessac-medium",
    "es": "es_ES-davefx-medium",
    "fa": "fa_IR-amir-medium",
    "fi": "fi_FI-harri-medium",
    "fr": "fr_FR-siwis-medium",
    "hu": "hu_HU-anna-medium",
    "is": "is_IS-bui-medium",
    "it": "it_IT-riccardo-x_low",
    "ka": "ka_GE-natia-medium",
    "kk": "kk_KZ-issai-high",
    "lb": "lb_LU-marylux-medium",
    "ne": "ne_NP-google-medium",
    "nl": "nl_BE-nathalie-medium",
    "no": "no_NO-talesyntese-medium",
    "pl": "pl_PL-darkman-medium",
    "pt": "pt_BR-faber-medium",
    "ro": "ro_RO-mihai-medium",
    "ru": "ru_RU-irina-medium",
    "sk": "sk_SK-lili-medium",
    "sl": "sl_SI-artur-medium",
    "sr": "sr_RS-serbski_institut-medium",
    "sv": "sv_SE-nst-medium",
    "sw": "sw_CD-lanfrica-medium",
    "tr": "tr_TR-dfki-medium",
    "uk": "uk_UA-ukrainian_tts-medium",
    "vi": "vi_VN-vais1000-medium",
    "zh": "zh_CN-huayan-medium",
}


def piper_voice_for(language: str | None, settings: Settings) -> str | None:
    """The Piper voice to speak `language` with; None for PIPER_VOICE, "" if Piper has none."""
    if not language or settings.piper_voice.split("_", 1)[0] == language:
        return None
    return {**PIPER_VOICES, **settings.piper_voices}.get(language, "")


@dataclass
class Transcript:
    text: str
    language: str | None = None
    duration: float = 0.0
    elapsed: float = 0.0


@dataclass
class EngineStatus:
    name: str
    ready: bool = False
    loading: bool = False
    error: str | None = None
    detail: dict = field(default_factory=dict)


class STTEngine(Protocol):
    status: EngineStatus

    def load(self) -> None: ...
    def transcribe(
        self, audio: np.ndarray | BinaryIO, language: str | None = None, quick: bool = False
    ) -> Transcript: ...


class TTSEngine(Protocol):
    status: EngineStatus

    def load(self) -> None: ...
    def synthesize(self, text: str, voice: str | None = None) -> bytes: ...
    def list_voices(self) -> list[str]: ...


class WhisperSTT:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._lock = threading.Lock()
        self._last_used = float("-inf")
        self.status = EngineStatus(
            "faster-whisper", detail={"model": settings.stt_model, "device": settings.stt_device}
        )

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            self.status.loading = True
            try:
                from faster_whisper import WhisperModel

                started = time.perf_counter()
                self._model = WhisperModel(
                    self.settings.stt_model,
                    device=self.settings.stt_device,
                    compute_type=self.settings.stt_compute_type,
                )
                self.status.ready, self.status.error = True, None
                log.info("whisper '%s' loaded in %.1fs", self.settings.stt_model,
                         time.perf_counter() - started)
            except Exception as exc:  # surface the reason via /health
                self.status.error = f"{exc.__class__.__name__}: {exc}"
                log.exception("failed to load whisper model")
                raise
            finally:
                self.status.loading = False

    def warm_up(self) -> None:
        """Run the model once on a short clip unless it ran a moment ago.

        After the computer has been idle for a while, the first run can take many seconds
        (memory and CPU wake up slowly). Live dictation calls this when the mic opens, so
        that wait is over before the user's first words instead of swallowing them.
        """
        if time.monotonic() - self._last_used < WARM_FOR_S:
            return
        self.load()
        started = time.perf_counter()
        segments, _ = self._model.transcribe(
            np.zeros(SAMPLE_RATE, dtype=np.float32),
            language="en" if not self.multilingual else (self.settings.stt_language or "en"),
            beam_size=1,
            vad_filter=False,  # with the filter, silence would skip the model altogether
            without_timestamps=True,
            temperature=0.0,
            max_new_tokens=4,
        )
        list(segments)
        self._last_used = time.monotonic()
        log.info("whisper warmed up in %.1fs", time.perf_counter() - started)

    @property
    def multilingual(self) -> bool:
        """English-only models (tiny.en, base.en...) can't recognize other languages."""
        return not self.settings.stt_model.endswith(".en")

    def transcribe(
        self, audio: np.ndarray | BinaryIO, language: str | None = None, quick: bool = False
    ) -> Transcript:
        """`language`: a code such as "vi" for this clip, else STT_LANGUAGE (empty = detect).

        `quick`: one fast pass for text that is redone anyway (live dictation while the user is
        still talking). Whisper normally retries a doubtful passage at higher temperatures, which
        on a CPU can take many times longer than the first pass.
        """
        self.load()
        started = time.perf_counter()
        if not self.multilingual:
            language = "en"
        options: dict = {}
        if isinstance(audio, np.ndarray) and len(audio):
            # Whisper sometimes loops on a phrase ("to my- to my- ..."): cap the text well above
            # what anyone can say in that time, so a loop ends quickly.
            seconds = len(audio) / SAMPLE_RATE
            options["max_new_tokens"] = min(MAX_NEW_TOKENS, int(seconds * TOKENS_PER_SECOND) + 16)
        if quick:
            options.update(temperature=0.0, without_timestamps=True)
        segments, info = self._model.transcribe(
            audio,
            language=language or self.settings.stt_language or None,
            beam_size=self.settings.stt_beam_size,
            vad_filter=True,
            condition_on_previous_text=False,
            **options,
        )
        kept = [s.text for s in segments if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)]
        self._last_used = time.monotonic()
        text = " ".join(t.strip() for t in kept).strip()
        if text.lower() in _HALLUCINATIONS:
            text = ""
        return Transcript(
            text=text,
            language=info.language,
            duration=float(info.duration),
            elapsed=time.perf_counter() - started,
        )


class PiperTTS:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.data_dir = Path(settings.piper_data_dir)
        self._voices: dict = {}
        self._lock = threading.Lock()
        self.status = EngineStatus("piper", detail={"voice": settings.piper_voice})

    def load(self) -> None:
        try:
            self._get_voice(self.settings.piper_voice)
            self.status.ready, self.status.error = True, None
        except Exception as exc:
            self.status.error = f"{exc.__class__.__name__}: {exc}"
            log.exception("failed to load piper voice")
            raise

    def _get_voice(self, name: str):
        if not VOICE_NAME.match(name):
            raise ValueError(f"Invalid Piper voice name: {name!r}")
        with self._lock:
            if name in self._voices:
                return self._voices[name]
            from piper import PiperVoice

            model_path = self.data_dir / f"{name}.onnx"
            if not model_path.exists():
                if not self.settings.piper_auto_download:
                    raise FileNotFoundError(f"Piper voice not found: {model_path}")
                from piper.download_voices import download_voice

                self.status.loading = True
                try:
                    log.info("downloading piper voice %s to %s", name, self.data_dir)
                    self.data_dir.mkdir(parents=True, exist_ok=True)
                    download_voice(name, self.data_dir)
                finally:
                    self.status.loading = False
            voice = PiperVoice.load(model_path)
            self._voices[name] = voice
            return voice

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        from piper import SynthesisConfig

        piper_voice = self._get_voice(voice or self.settings.piper_voice)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            piper_voice.synthesize_wav(
                text, wav, syn_config=SynthesisConfig(length_scale=self.settings.piper_length_scale)
            )
        return buf.getvalue()

    def list_voices(self) -> list[str]:
        installed = {p.name.removesuffix(".onnx") for p in self.data_dir.glob("*.onnx")}
        return sorted(installed | {self.settings.piper_voice})


class NullTTS:
    """Used for tts_engine=browser|none: the server never produces audio."""

    def __init__(self, name: str):
        self.status = EngineStatus(name, ready=True)

    def load(self) -> None:
        return None

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        raise RuntimeError(f"Server-side TTS is disabled (engine={self.status.name})")

    def list_voices(self) -> list[str]:
        return []


def build_tts(settings: Settings) -> TTSEngine:
    if settings.tts_engine == "piper":
        return PiperTTS(settings)
    return NullTTS(settings.tts_engine)


def float_to_wav(audio: np.ndarray, sample_rate: int) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()
