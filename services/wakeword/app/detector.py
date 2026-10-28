"""Streaming wake-word detection with openWakeWord."""

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from .config import CHUNK_SAMPLES, SAMPLE_RATE, Settings

log = logging.getLogger(__name__)


@dataclass
class Detection:
    model: str
    score: float
    at_seconds: float  # position in the stream


class Detector(Protocol):
    model_name: str
    threshold: float

    def process(self, pcm16: bytes) -> tuple[list[Detection], float]:
        """Consume PCM16 audio; return (detections, max score seen in this call)."""

    def reset(self) -> None: ...


class StreamingDetector:
    """Wraps a stateful scorer with 80 ms framing, thresholding and a cooldown.

    `score_fn(chunk) -> float` scores one int16 chunk of CHUNK_SAMPLES samples. The
    cooldown is measured in stream time so behaviour does not depend on network jitter.
    """

    def __init__(self, model_name, score_fn, reset_fn, threshold: float, cooldown_s: float):
        self.model_name = model_name
        self.threshold = threshold
        self._score = score_fn
        self._reset_model = reset_fn
        self._cooldown_samples = int(cooldown_s * SAMPLE_RATE)
        self._pending = np.zeros(0, dtype=np.int16)
        self._position = 0
        self._last_fire = -(10**12)

    def process(self, pcm16: bytes) -> tuple[list[Detection], float]:
        self._pending = np.concatenate([self._pending, np.frombuffer(pcm16, dtype="<i2")])
        detections: list[Detection] = []
        peak = 0.0
        while len(self._pending) >= CHUNK_SAMPLES:
            chunk, self._pending = self._pending[:CHUNK_SAMPLES], self._pending[CHUNK_SAMPLES:]
            self._position += CHUNK_SAMPLES
            score = float(self._score(chunk))
            peak = max(peak, score)
            if score >= self.threshold and self._position - self._last_fire >= self._cooldown_samples:
                self._last_fire = self._position
                detections.append(Detection(self.model_name, score, self._position / SAMPLE_RATE))
                # Clear the model's internal buffers so one utterance fires once.
                self._reset_model()
        return detections, peak

    def reset(self) -> None:
        self._pending = np.zeros(0, dtype=np.int16)
        self._reset_model()


class DetectorFactory(Protocol):
    def available(self) -> list[str]: ...
    def create(self, model_name: str, threshold: float) -> Detector: ...
    def prepare(self) -> None: ...


class OpenWakeWordFactory:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.custom_dir = Path(settings.wakeword_custom_dir)
        self._prepared = False
        self._lock = threading.Lock()
        self.error: str | None = None

    def _custom_models(self) -> dict[str, Path]:
        if not self.custom_dir.is_dir():
            return {}
        return {p.stem: p for p in sorted(self.custom_dir.glob("*.onnx"))}

    def available(self) -> list[str]:
        return list(dict.fromkeys([*self.settings.wakeword_models, *self._custom_models()]))

    def prepare(self) -> None:
        """Download pretrained feature + wake-word models if needed (idempotent)."""
        with self._lock:
            if self._prepared:
                return
            try:
                if self.settings.wakeword_download:
                    from openwakeword.utils import download_models

                    download_models(model_names=self.settings.wakeword_models)
                self._prepared, self.error = True, None
            except Exception as exc:
                self.error = f"{exc.__class__.__name__}: {exc}"
                log.exception("failed to prepare wake-word models")
                raise

    def create(self, model_name: str, threshold: float) -> Detector:
        if model_name not in self.available():
            raise ValueError(f"Unknown wake-word model '{model_name}'")
        self.prepare()
        from openwakeword.model import Model

        source = str(self._custom_models().get(model_name, model_name))
        model = Model(
            wakeword_models=[source],
            inference_framework="onnx",
            vad_threshold=self.settings.wakeword_vad_threshold,
        )

        def score(chunk: np.ndarray) -> float:
            scores = model.predict(chunk)
            return max(scores.values()) if scores else 0.0

        return StreamingDetector(
            model_name, score, model.reset, threshold, self.settings.wakeword_cooldown_s
        )
