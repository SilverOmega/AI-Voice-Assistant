"""Energy-based endpointing: turns a continuous PCM stream into discrete utterances.

The threshold adapts to the background noise floor, so the same settings work in a
quiet room and next to a fan. Whisper runs its own VAD filter on the result, which
cleans up any leading/trailing noise this coarse detector lets through.
"""

from collections import deque
from dataclasses import dataclass

import numpy as np

from .config import SAMPLE_RATE

FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
# Frame-energy coefficient of variation below which audio is treated as stationary noise.
STATIONARY_CV = 0.3


@dataclass
class VadEvent:
    kind: str  # "speech_start" | "speech_end" | "discarded"
    audio: np.ndarray | None = None  # float32 utterance for speech_end


class Endpointer:
    def __init__(
        self,
        min_threshold: float = 0.012,
        noise_ratio: float = 3.0,
        start_ms: int = 120,
        silence_ms: int = 800,
        preroll_ms: int = 300,
        min_utterance_ms: int = 300,
        max_utterance_s: float = 30.0,
    ):
        self.min_threshold = min_threshold
        self.noise_ratio = noise_ratio
        self.start_frames = max(1, start_ms // FRAME_MS)
        self.silence_frames = max(1, silence_ms // FRAME_MS)
        self.min_frames = max(1, min_utterance_ms // FRAME_MS)
        self.max_frames = int(max_utterance_s * 1000 // FRAME_MS)
        self._preroll: deque[np.ndarray] = deque(maxlen=max(1, preroll_ms // FRAME_MS))
        self._pending = np.zeros(0, dtype=np.float32)
        self.noise_floor = min_threshold / noise_ratio
        self.reset()

    def reset(self) -> None:
        self.in_speech = False
        self._frames: list[np.ndarray] = []
        self._loud_run = 0
        self._quiet_run = 0
        self._voiced = 0
        self._rms: list[float] = []
        self._preroll.clear()

    @property
    def threshold(self) -> float:
        return max(self.min_threshold, self.noise_floor * self.noise_ratio)

    def feed(self, pcm16: bytes) -> list[VadEvent]:
        """Feed little-endian PCM16 mono audio at 16 kHz; return any boundary events."""
        samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        self._pending = np.concatenate([self._pending, samples])
        events: list[VadEvent] = []
        while len(self._pending) >= FRAME_SAMPLES:
            frame, self._pending = self._pending[:FRAME_SAMPLES], self._pending[FRAME_SAMPLES:]
            event = self._process_frame(frame)
            if event:
                events.append(event)
        return events

    def _process_frame(self, frame: np.ndarray) -> VadEvent | None:
        rms = float(np.sqrt(np.mean(frame * frame)))
        loud = rms > self.threshold

        if not self.in_speech:
            # Learn the noise floor from quiet frames only (slow rise, fast fall), so speech
            # that starts right away can never raise the threshold above itself.
            if not loud:
                rate = 0.05 if rms > self.noise_floor else 0.3
                self.noise_floor += (rms - self.noise_floor) * rate
            self._preroll.append(frame)
            self._loud_run = self._loud_run + 1 if loud else 0
            if self._loud_run >= self.start_frames:
                self.in_speech = True
                self._frames = list(self._preroll)
                self._quiet_run = 0
                self._voiced = self._loud_run
                return VadEvent("speech_start")
            return None

        self._frames.append(frame)
        self._rms.append(rms)
        self._quiet_run = 0 if loud else self._quiet_run + 1
        self._voiced += loud
        if self._quiet_run >= self.silence_frames:
            return self._finish()
        if len(self._frames) >= self.max_frames:
            # Speech energy fluctuates syllable by syllable; a full-length "utterance" with
            # nearly constant energy is steady background noise (fan, hum). Learn it as the
            # new floor and drop it.
            levels = np.asarray(self._rms)
            if levels.std() / max(levels.mean(), 1e-9) < STATIONARY_CV:
                self.noise_floor = float(np.median(levels))
                self.reset()
                return VadEvent("discarded")
            return self._finish()
        return None

    def _finish(self) -> VadEvent:
        voiced = self._voiced
        audio = np.concatenate(self._frames) if self._frames else np.zeros(0, np.float32)
        self.reset()
        if voiced < self.min_frames:
            return VadEvent("discarded")
        return VadEvent("speech_end", audio)

    def flush(self) -> VadEvent | None:
        """Force-end an in-progress utterance (e.g. the client stopped streaming)."""
        return self._finish() if self.in_speech else None


def pcm16_to_float(pcm16: bytes) -> np.ndarray:
    return np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
