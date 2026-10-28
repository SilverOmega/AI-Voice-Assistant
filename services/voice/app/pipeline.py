"""Per-connection voice conversation: audio in -> STT -> chat -> TTS -> audio out.

Protocol (client -> server):
  binary frames                       PCM16 LE mono 16 kHz microphone audio
  {"type": "start", ...options}       configure: session_id, mode ("vad"|"ptt"), tts, barge_in, voice
  {"type": "ptt_start"} / {"type": "ptt_end"}   push-to-talk boundaries (mode "ptt")
  {"type": "text", "text": "..."}     run a turn from typed text (reply is spoken)
  {"type": "cancel"}                  abort the current turn

Protocol (server -> client):
  {"type": "ready" | "state" | "vad" | "session" | "transcript" | "assistant_start" | "token" |
   "assistant_done" | "speak" | "tts_done" | "turn_done" | "interrupted" | "error", ...}
  binary frames                       one WAV file per spoken sentence, in order
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from .chat_client import ChatClient, ChatClientError
from .config import SAMPLE_RATE, Settings
from .engines import STTEngine, TTSEngine
from .sentences import SentenceChunker
from .vad import Endpointer, pcm16_to_float

log = logging.getLogger(__name__)

SendJson = Callable[[dict[str, Any]], Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]


@dataclass
class SessionOptions:
    session_id: str | None = None
    mode: Literal["vad", "ptt"] = "vad"
    tts: bool = True
    barge_in: bool = False
    voice: str | None = None


class VoiceSession:
    def __init__(
        self,
        settings: Settings,
        stt: STTEngine,
        tts: TTSEngine,
        chat: ChatClient,
        send_json: SendJson,
        send_bytes: SendBytes,
    ):
        self.settings = settings
        self.stt = stt
        self.tts = tts
        self.chat = chat
        self._send_json = send_json
        self._send_bytes = send_bytes
        self.options = SessionOptions()
        self.endpointer = self._new_endpointer()
        self._ptt_buffer: list[bytes] | None = None
        self._turn: asyncio.Task | None = None
        self._state = ""

    def _new_endpointer(self) -> Endpointer:
        s = self.settings
        return Endpointer(
            min_threshold=s.vad_min_threshold,
            noise_ratio=s.vad_noise_ratio,
            start_ms=s.vad_start_ms,
            silence_ms=s.vad_silence_ms,
            preroll_ms=s.vad_preroll_ms,
            min_utterance_ms=s.vad_min_utterance_ms,
            max_utterance_s=s.vad_max_utterance_s,
        )

    @property
    def turn_active(self) -> bool:
        return self._turn is not None and not self._turn.done()

    @property
    def server_tts(self) -> bool:
        return self.options.tts and self.settings.tts_engine == "piper"

    async def send(self, msg: dict[str, Any]) -> None:
        await self._send_json(msg)

    async def set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            await self.send({"type": "state", "state": state})

    # ---- inbound -----------------------------------------------------------

    async def handle_control(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        if kind == "start":
            for key in ("session_id", "mode", "tts", "barge_in", "voice"):
                if key in msg:
                    setattr(self.options, key, msg[key])
            if self.options.mode not in ("vad", "ptt"):
                self.options.mode = "vad"
            if self.turn_active:  # option change mid-turn: don't disturb the running turn
                return
            self.endpointer.reset()
            await self.send(
                {
                    "type": "ready",
                    "session_id": self.options.session_id,
                    "mode": self.options.mode,
                    "tts_engine": self.settings.tts_engine if self.options.tts else "none",
                    "sample_rate": SAMPLE_RATE,
                }
            )
            await self.set_state("listening")
        elif kind == "ptt_start":
            if self.turn_active:
                await self.interrupt()
            self._ptt_buffer = []
            await self.set_state("recording")
        elif kind == "ptt_end":
            chunks, self._ptt_buffer = self._ptt_buffer, None
            if chunks:
                self._start_turn(audio=pcm16_to_float(b"".join(chunks)))
            else:
                await self.set_state("listening")
        elif kind == "text":
            text = str(msg.get("text", "")).strip()
            if text:
                if self.turn_active:
                    await self.interrupt()
                self._start_turn(text=text)
        elif kind == "cancel":
            await self.interrupt()
        else:
            await self.send({"type": "error", "message": f"Unknown message type: {kind!r}"})

    async def handle_audio(self, pcm16: bytes) -> None:
        if self.options.mode == "ptt":
            if self._ptt_buffer is not None:
                self._ptt_buffer.append(pcm16)
            return
        if self.turn_active and not self.options.barge_in:
            return  # half-duplex: ignore the mic while the assistant is busy
        for event in self.endpointer.feed(pcm16):
            if event.kind == "speech_start":
                if self.turn_active:  # barge-in
                    await self.interrupt()
                await self.send({"type": "vad", "speaking": True})
                await self.set_state("recording")
            elif event.kind == "discarded":
                await self.send({"type": "vad", "speaking": False})
                await self.set_state("listening")
            elif event.kind == "speech_end":
                await self.send({"type": "vad", "speaking": False})
                self._start_turn(audio=event.audio)

    # ---- turns -------------------------------------------------------------

    def _start_turn(self, audio: np.ndarray | None = None, text: str | None = None) -> None:
        self._turn = asyncio.create_task(self._run_turn(audio, text))

    async def interrupt(self) -> None:
        task, self._turn = self._turn, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await self.send({"type": "interrupted"})
        self.endpointer.reset()
        await self.set_state("listening")

    async def _run_turn(self, audio: np.ndarray | None, text: str | None) -> None:
        try:
            if audio is not None:
                await self.set_state("transcribing")
                result = await asyncio.to_thread(self.stt.transcribe, audio)
                text = result.text
                await self.send(
                    {
                        "type": "transcript",
                        "text": text,
                        "language": result.language,
                        "audio_seconds": round(len(audio) / SAMPLE_RATE, 2),
                        "stt_seconds": round(result.elapsed, 2),
                    }
                )
                if not text:
                    await self.set_state("listening")
                    return
            await self._respond(text or "")
        except asyncio.CancelledError:
            raise
        except ChatClientError as exc:
            await self.send({"type": "error", "message": str(exc)})
        except Exception as exc:
            log.exception("voice turn failed")
            await self.send({"type": "error", "message": f"Voice pipeline error: {exc}"})
        if self._turn is asyncio.current_task():
            await self.set_state("listening")

    async def _respond(self, text: str) -> None:
        await self.set_state("thinking")
        session_id = await self.chat.ensure_session(self.options.session_id)
        if session_id != self.options.session_id:
            self.options.session_id = session_id
            await self.send({"type": "session", "session_id": session_id})

        sentences: asyncio.Queue[str | None] = asyncio.Queue()
        speaker = asyncio.create_task(self._speak(sentences))
        chunker = SentenceChunker()
        reply: list[str] = []
        started = time.perf_counter()
        try:
            # aclosing: an interrupted turn must close the HTTP stream right away so the chat
            # service stops generating (and saves the partial reply).
            async with aclosing(self.chat.stream(session_id, text)) as events:
                async for event, data in events:
                    if event == "start":
                        await self.send({"type": "assistant_start", "model": data.get("model")})
                    elif event == "token":
                        reply.append(data["text"])
                        await self.send({"type": "token", "text": data["text"]})
                        for sentence in chunker.feed(data["text"]):
                            sentences.put_nowait(sentence)
                    elif event == "done":
                        message = data.get("message") or {}
                        await self.send(
                            {
                                "type": "assistant_done",
                                "text": "".join(reply),
                                "message_id": message.get("id"),
                                "llm_seconds": round(time.perf_counter() - started, 2),
                            }
                        )
                    elif event == "error":
                        await self.send({"type": "error", "message": data.get("message", "error")})
            for sentence in chunker.flush():
                sentences.put_nowait(sentence)
            sentences.put_nowait(None)
            await speaker
        finally:
            if not speaker.done():
                speaker.cancel()
        await self.send({"type": "turn_done"})

    async def _speak(self, sentences: asyncio.Queue[str | None]) -> None:
        """Synthesize sentences in order while the LLM keeps streaming."""
        spoke = False
        while (sentence := await sentences.get()) is not None:
            if not self.options.tts:
                continue
            if not self.server_tts:
                if not spoke:
                    await self.set_state("speaking")
                    spoke = True
                await self.send({"type": "speak", "text": sentence})
                continue
            try:
                wav = await asyncio.to_thread(self.tts.synthesize, sentence, self.options.voice)
            except Exception as exc:
                log.exception("tts failed")
                await self.send({"type": "error", "message": f"Text-to-speech failed: {exc}"})
                self.options.tts = False
                continue
            if not spoke:
                await self.set_state("speaking")
                spoke = True
            await self._send_bytes(wav)
        if spoke:
            await self.send({"type": "tts_done"})

    async def close(self) -> None:
        task, self._turn = self._turn, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
