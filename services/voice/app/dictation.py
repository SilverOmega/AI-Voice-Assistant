"""Live dictation: speech in, text out while the user is still talking (/ws/dictate).

The browser streams 16 kHz PCM16 frames. The endpointer splits them into phrases at short
pauses. While a phrase goes on, what has been said so far is transcribed every so often and
sent as a `partial` (the browser shows it in the message box, still changing). When the phrase
ends, the whole of it is transcribed once more and sent as a `final`, which doesn't change.

Whisper is the slow part, so there is never more than one partial in progress: audio that
arrives meanwhile simply makes the next partial longer. Partials wait while a final is being
written, so the text in the box never jumps backwards. Nothing here talks to the chat service:
the text stays in the message box until the user sends it.

Messages from the browser: {"type": "start", "language": "vi"} (optional), PCM16 binary
frames, {"type": "stop"}. To the browser: {"type": "ready"}, {"type": "partial", "text"},
{"type": "final", "text"} (text may be empty: drop the partial), {"type": "done"} after the
last final once "stop" was sent, and {"type": "error", "message"}.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import numpy as np

from .config import SAMPLE_RATE, Settings
from .engines import LANGUAGE_CODE, STTEngine
from .vad import Endpointer

log = logging.getLogger(__name__)

SendJson = Callable[[dict[str, Any]], Awaitable[None]]

# A partial is only worth redoing once this much more speech has arrived.
PARTIAL_MIN_NEW_SAMPLES = SAMPLE_RATE * 400 // 1000
# A partial slower than this (seconds) is logged: the text in the box lags noticeably.
SLOW_PARTIAL_S = 2.0

_STOP = None  # marks "stop" in the queue of phrases


class DictationSession:
    def __init__(self, settings: Settings, stt: STTEngine, send_json: SendJson):
        self.settings = settings
        self.stt = stt
        self._send = send_json
        self.language: str | None = None
        self.endpointer = Endpointer(
            min_threshold=settings.vad_min_threshold,
            noise_ratio=settings.vad_noise_ratio,
            start_ms=settings.vad_start_ms,
            silence_ms=settings.dictation_silence_ms,
            preroll_ms=settings.vad_preroll_ms,
            min_utterance_ms=settings.vad_min_utterance_ms,
            max_utterance_s=settings.dictation_max_phrase_s,
        )
        # One Whisper run at a time: two at once only slow each other down on a CPU.
        self._whisper = asyncio.Lock()
        self._phrases: asyncio.Queue[np.ndarray | None] = asyncio.Queue()
        self._finals_pending = 0
        self._phrase = 0  # counts finished phrases; a partial for an older one is dropped
        self._partial: asyncio.Task | None = None
        self._partial_samples = 0
        self._partial_at = 0.0
        self._writer = asyncio.create_task(self._write_finals())

    # ---- from the browser ----------------------------------------------------------

    async def handle_control(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "start":
            language = msg.get("language")
            if language in (None, ""):
                self.language = None
            elif isinstance(language, str) and LANGUAGE_CODE.match(language):
                self.language = language
            else:
                await self._send({"type": "error", "message": "language must be a language code such as en or vi"})
                return
            if self.language and self.language != "en" and not getattr(self.stt, "multilingual", True):
                await self._send(
                    {
                        "type": "error",
                        "message": f"Speech recognition ({self.settings.stt_model}) only understands "
                        "English. Set STT_MODEL to a multilingual model such as base or small in "
                        ".env and restart to dictate in other languages.",
                    }
                )
            await self._send({"type": "ready", "sample_rate": SAMPLE_RATE})
            # Wake Whisper up while the user draws breath; partials wait for it like for any run.
            if hasattr(self.stt, "warm_up") and not (self._partial and not self._partial.done()):
                self._partial = asyncio.create_task(self._warm_up())
        elif kind == "stop":
            event = self.endpointer.flush()
            if event and event.kind == "speech_end":
                self._end_phrase(event.audio)
            elif event:  # too short to count: clear what the box shows for it
                await self._drop_phrase()
            await self._phrases.put(_STOP)
        else:
            await self._send({"type": "error", "message": f"Unknown message type: {kind!r}"})

    async def handle_audio(self, pcm16: bytes) -> None:
        for event in self.endpointer.feed(pcm16):
            if event.kind == "speech_end":
                self._end_phrase(event.audio)
            elif event.kind == "discarded":
                await self._drop_phrase()
        self._maybe_partial()

    async def close(self) -> None:
        for task in (self._writer, self._partial):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    # ---- partials ---------------------------------------------------------------

    def _maybe_partial(self) -> None:
        if not self.endpointer.in_speech or self._finals_pending:
            return
        if self._partial and not self._partial.done():
            return  # Whisper is still busy: the next partial will include this audio
        audio = self.endpointer.current_audio()
        if len(audio) - self._partial_samples < PARTIAL_MIN_NEW_SAMPLES:
            return
        if (time.monotonic() - self._partial_at) * 1000 < self.settings.dictation_partial_ms:
            return
        self._partial_samples = len(audio)
        self._partial_at = time.monotonic()
        self._partial = asyncio.create_task(self._send_partial(audio, self._phrase))

    async def _warm_up(self) -> None:
        try:
            async with self._whisper:
                await asyncio.to_thread(self.stt.warm_up)
        except Exception:
            log.exception("dictation: warming up the speech model failed")

    async def _transcribe(self, audio: np.ndarray, quick: bool) -> str:
        async with self._whisper:
            started = time.perf_counter()
            text = (await asyncio.to_thread(self.stt.transcribe, audio, self.language, quick)).text
        took = time.perf_counter() - started
        # Finals always; partials only when slow enough to make the box lag behind the voice.
        if not quick or took > SLOW_PARTIAL_S:
            log.info(
                "dictation %s: %.1fs of speech in %.1fs",
                "partial" if quick else "final", len(audio) / SAMPLE_RATE, took,
            )
        return text

    async def _send_partial(self, audio: np.ndarray, phrase: int) -> None:
        try:
            text = await self._transcribe(audio, quick=True)
        except Exception:
            log.exception("dictation: partial transcription failed")
            return
        # The phrase ended meanwhile: its final is on the way and replaces this.
        if phrase == self._phrase and not self._finals_pending:
            await self._send({"type": "partial", "text": text})

    # ---- finals ---------------------------------------------------------------

    def _end_phrase(self, audio: np.ndarray | None) -> None:
        self._phrase += 1
        self._partial_samples = 0
        self._finals_pending += 1
        self._phrases.put_nowait(audio if audio is not None else np.zeros(0, np.float32))

    async def _drop_phrase(self) -> None:
        self._phrase += 1
        self._partial_samples = 0
        if not self._finals_pending:
            await self._send({"type": "partial", "text": ""})

    async def _write_finals(self) -> None:
        while True:
            audio = await self._phrases.get()
            if audio is _STOP:
                await self._send({"type": "done"})
                continue
            text = ""
            try:
                if len(audio):
                    text = await self._transcribe(audio, quick=False)
            except Exception:
                log.exception("dictation: transcription failed")
                await self._send({"type": "error", "message": "Part of what you said couldn't be transcribed."})
            try:
                await self._send({"type": "final", "text": text})
            finally:
                self._finals_pending -= 1
