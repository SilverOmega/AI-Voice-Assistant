"""Voice service: speech-to-text, text-to-speech and the streaming voice conversation socket."""

import asyncio
import io
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .chat_client import ChatClient
from .config import Settings, get_settings
from .engines import STTEngine, TTSEngine, WhisperSTT, build_tts
from .pipeline import VoiceSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5_000)
    voice: str | None = None


def create_app(
    settings: Settings | None = None,
    stt: STTEngine | None = None,
    tts: TTSEngine | None = None,
    chat: ChatClient | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.stt = stt or WhisperSTT(settings)
        app.state.tts = tts or build_tts(settings)
        app.state.chat = chat or ChatClient(settings.chat_service_url)
        loaders = []
        if settings.stt_preload:
            # Load models in the background so the service starts answering immediately.
            loaders = [
                asyncio.create_task(asyncio.to_thread(app.state.stt.load)),
                asyncio.create_task(asyncio.to_thread(app.state.tts.load)),
            ]
        try:
            yield
        finally:
            for task in loaders:
                task.cancel()
            await app.state.chat.aclose()

    app = FastAPI(title="Assistant Voice Service", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health(request: Request) -> dict:
        stt_status = request.app.state.stt.status
        tts_status = request.app.state.tts.status
        chat_ok = await request.app.state.chat.ping()
        ok = stt_status.error is None and tts_status.error is None and chat_ok
        return {
            "status": "ok" if ok else "degraded",
            "service": "voice",
            "chat_service": chat_ok,
            "stt": vars(stt_status),
            "tts": {**vars(tts_status), "engine": settings.tts_engine},
        }

    @app.post("/api/stt")
    async def speech_to_text(request: Request, file: UploadFile = File(...)) -> dict:
        """Transcribe an uploaded clip (any format ffmpeg/PyAV can decode, e.g. webm/opus)."""
        data = await file.read(settings.stt_max_upload_mb * 1024 * 1024 + 1)
        if len(data) > settings.stt_max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"Audio exceeds {settings.stt_max_upload_mb} MB")
        if not data:
            raise HTTPException(400, "Empty audio upload")
        try:
            result = await asyncio.to_thread(request.app.state.stt.transcribe, io.BytesIO(data))
        except Exception as exc:
            log.exception("transcription failed")
            raise HTTPException(422, f"Could not transcribe audio: {exc}") from exc
        return {
            "text": result.text,
            "language": result.language,
            "duration": round(result.duration, 2),
            "elapsed": round(result.elapsed, 2),
        }

    @app.post("/api/tts", response_class=Response)
    async def text_to_speech(body: TTSRequest, request: Request) -> Response:
        if settings.tts_engine != "piper":
            raise HTTPException(503, f"Server-side TTS is disabled (TTS_ENGINE={settings.tts_engine})")
        try:
            wav = await asyncio.to_thread(request.app.state.tts.synthesize, body.text, body.voice)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            log.exception("tts failed")
            raise HTTPException(500, f"Text-to-speech failed: {exc}") from exc
        return Response(content=wav, media_type="audio/wav")

    @app.get("/api/voices")
    async def voices(request: Request) -> dict:
        return {
            "engine": settings.tts_engine,
            "default": settings.piper_voice if settings.tts_engine == "piper" else None,
            "voices": request.app.state.tts.list_voices(),
        }

    @app.websocket("/ws/voice")
    async def voice_socket(ws: WebSocket) -> None:
        await ws.accept()
        send_lock = asyncio.Lock()  # turn, TTS and receive loop all send

        async def send_json(msg: dict) -> None:
            async with send_lock:
                await ws.send_text(json.dumps(msg))

        async def send_bytes(data: bytes) -> None:
            async with send_lock:
                await ws.send_bytes(data)

        state = ws.app.state
        session = VoiceSession(settings, state.stt, state.tts, state.chat, send_json, send_bytes)
        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    await session.handle_audio(message["bytes"])
                elif message.get("text") is not None:
                    try:
                        payload = json.loads(message["text"])
                    except ValueError:
                        await send_json({"type": "error", "message": "Invalid JSON"})
                        continue
                    await session.handle_control(payload)
        except WebSocketDisconnect:
            pass
        except RuntimeError as exc:  # sending on a closed socket
            log.info("voice socket closed: %s", exc)
        finally:
            await session.close()

    return app


app = create_app()
