"""Wake-word service: listens to a streamed microphone and reports wake-phrase detections.

WebSocket /ws/wakeword?model=hey_jarvis&threshold=0.5&scores=1
  client -> server: binary PCM16 LE mono 16 kHz audio; {"type": "reset"}
  server -> client: {"type": "ready", model, threshold}
                    {"type": "wake", model, score, at_seconds}
                    {"type": "score", score}   (~4/s, only when scores=1; for level meters)
                    {"type": "error", message}
"""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, get_settings
from .detector import DetectorFactory, OpenWakeWordFactory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

SCORE_INTERVAL_S = 0.25


def create_app(settings: Settings | None = None, factory: DetectorFactory | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.factory = factory or OpenWakeWordFactory(settings)
        prep = asyncio.create_task(asyncio.to_thread(app.state.factory.prepare))
        try:
            yield
        finally:
            prep.cancel()

    app = FastAPI(title="Assistant Wake-Word Service", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health(request: Request) -> dict:
        error = getattr(request.app.state.factory, "error", None)
        return {"status": "degraded" if error else "ok", "service": "wakeword", "error": error}

    @app.get("/api/models")
    async def models(request: Request) -> dict:
        return {
            "models": request.app.state.factory.available(),
            "default": settings.wakeword_default,
            "threshold": settings.wakeword_threshold,
        }

    @app.websocket("/ws/wakeword")
    async def wakeword_socket(ws: WebSocket) -> None:
        await ws.accept()
        params = ws.query_params
        model_name = params.get("model") or settings.wakeword_default
        send_scores = params.get("scores") == "1"
        try:
            threshold = float(params.get("threshold") or settings.wakeword_threshold)
            if not 0.0 < threshold < 1.0:
                raise ValueError("threshold must be between 0 and 1")
            detector = await asyncio.to_thread(ws.app.state.factory.create, model_name, threshold)
        except Exception as exc:
            await ws.send_json({"type": "error", "message": str(exc)})
            await ws.close(code=1008)
            return

        await ws.send_json({"type": "ready", "model": model_name, "threshold": threshold})
        last_score_sent, peak = 0.0, 0.0
        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    detections, chunk_peak = await asyncio.to_thread(detector.process, message["bytes"])
                    for d in detections:
                        log.info("wake word '%s' detected (score %.2f)", d.model, d.score)
                        await ws.send_json(
                            {"type": "wake", "model": d.model, "score": round(d.score, 3),
                             "at_seconds": round(d.at_seconds, 2)}
                        )
                    peak = max(peak, chunk_peak)
                    now = time.monotonic()
                    if send_scores and now - last_score_sent >= SCORE_INTERVAL_S:
                        await ws.send_json({"type": "score", "score": round(peak, 3)})
                        last_score_sent, peak = now, 0.0
                elif message.get("text") is not None:
                    try:
                        payload = json.loads(message["text"])
                    except ValueError:
                        payload = {}
                    if payload.get("type") == "reset":
                        detector.reset()
                    else:
                        await ws.send_json({"type": "error", "message": "Unknown message"})
        except WebSocketDisconnect:
            pass
        except RuntimeError as exc:
            log.info("wake-word socket closed: %s", exc)

    return app


app = create_app()
