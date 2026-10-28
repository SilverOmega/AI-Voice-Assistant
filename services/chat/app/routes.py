"""HTTP routes for the chat service."""

import json
import time
from collections.abc import AsyncIterator
from contextlib import aclosing

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from .chat_service import ChatEvent, ChatService, SessionBusy, SessionNotFound
from .llm import LLMError
from .schemas import (
    ChatRequest,
    ChatResponse,
    ConfigOut,
    HealthOut,
    RuntimeConfigUpdate,
    SessionCreate,
    SessionDetail,
    SessionOut,
    SessionUpdate,
)

router = APIRouter()

HEALTH_CACHE_SECONDS = 15.0


def _svc(request: Request) -> ChatService:
    return request.app.state.chat


async def _provider_status(request: Request, force: bool = False) -> tuple[bool, str]:
    """Provider checks hit the network, so cache them briefly."""
    svc = _svc(request)
    key = (svc.runtime.provider, svc.resolved_model())
    cache = request.app.state.health_cache
    hit = cache.get(key)
    if hit and not force and time.monotonic() - hit[0] < HEALTH_CACHE_SECONDS:
        return hit[1]
    result = await svc.registry.get(svc.runtime.provider).check(svc.resolved_model())
    cache[key] = (time.monotonic(), result)
    return result


# ---- health & config ----------------------------------------------------------


@router.get("/health", response_model=HealthOut)
async def health(request: Request) -> HealthOut:
    svc = _svc(request)
    ok, detail = await _provider_status(request)
    return HealthOut(
        status="ok" if ok else "degraded",
        provider=svc.runtime.provider,
        model=svc.resolved_model(),
        provider_ok=ok,
        detail=detail,
    )


async def _config_out(request: Request) -> ConfigOut:
    svc = _svc(request)
    _, detail = await _provider_status(request)
    return ConfigOut(
        **svc.runtime.model_dump(exclude={"model"}),
        model=svc.resolved_model(),
        available_providers=svc.registry.names,
        provider_status={svc.runtime.provider: detail},
    )


@router.get("/api/config", response_model=ConfigOut)
async def get_config(request: Request) -> ConfigOut:
    return await _config_out(request)


@router.patch("/api/config", response_model=ConfigOut)
async def update_config(body: RuntimeConfigUpdate, request: Request) -> ConfigOut:
    svc = _svc(request)
    changes = body.model_dump(exclude_unset=True)
    if "provider" in changes and changes["provider"] != svc.runtime.provider and "model" not in changes:
        changes["model"] = ""  # switching provider without a model -> use its default
    previous = (svc.runtime.provider, svc.resolved_model())
    svc.runtime = svc.runtime.model_copy(update=changes)
    if (svc.runtime.provider, svc.resolved_model()) != previous:
        svc.start_warm_up()
    return await _config_out(request)


@router.get("/api/models")
async def list_models(request: Request) -> dict:
    """Models the active provider can serve (Ollama lists pulled models)."""
    svc = _svc(request)
    provider = svc.registry.get(svc.runtime.provider)
    models: list[str] = []
    if hasattr(provider, "list_models"):
        try:
            models = await provider.list_models()
        except Exception:
            models = []
    return {"provider": provider.name, "default": provider.default_model, "models": models}


# ---- sessions -----------------------------------------------------------------


@router.get("/api/sessions", response_model=list[SessionOut])
async def list_sessions(request: Request) -> list[SessionOut]:
    return await _svc(request).repo.list_sessions()


@router.post("/api/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(body: SessionCreate, request: Request) -> SessionOut:
    return await _svc(request).repo.create_session(body.title)


@router.get("/api/sessions/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str, request: Request) -> SessionDetail:
    detail = await _svc(request).repo.get_session_detail(session_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return detail


@router.patch("/api/sessions/{session_id}", response_model=SessionOut)
async def rename_session(session_id: str, body: SessionUpdate, request: Request) -> SessionOut:
    session = await _svc(request).repo.rename_session(session_id, body.title)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return session


@router.delete("/api/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: str, request: Request) -> Response:
    if not await _svc(request).repo.delete_session(session_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- chat ---------------------------------------------------------------------


async def _start_turn(request: Request, session_id: str, body: ChatRequest):
    images = [img.to_image_data() for img in body.images]
    try:
        return await _svc(request).stream_reply(session_id, body.content, body.source, images)
    except SessionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found") from None
    except SessionBusy:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A reply is already being generated for this session"
        ) from None


async def _sse(events: AsyncIterator[ChatEvent]) -> AsyncIterator[str]:
    async with aclosing(events):
        async for event in events:
            yield f"event: {event.event}\ndata: {json.dumps(event.data)}\n\n"


class ClosingStreamingResponse(StreamingResponse):
    """Closes the body generator as soon as the response ends, including on client disconnect.

    Otherwise a generator suspended at `yield` is only finalized by the garbage collector,
    keeping the session lock held and the model generating for nobody.
    """

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.body_iterator.aclose()


@router.post("/api/sessions/{session_id}/messages/stream")
async def stream_message(session_id: str, body: ChatRequest, request: Request) -> StreamingResponse:
    """Send a message and stream the reply as Server-Sent Events.

    Events: `start` {user_message, provider, model}, `token` {text}, `done` {message}, `error` {message}.
    """
    events = await _start_turn(request, session_id, body)
    return ClosingStreamingResponse(
        _sse(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/sessions/{session_id}/messages", response_model=ChatResponse)
async def send_message(session_id: str, body: ChatRequest, request: Request) -> ChatResponse:
    images = [img.to_image_data() for img in body.images]
    try:
        result = await _svc(request).reply(session_id, body.content, body.source, images)
    except SessionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found") from None
    except SessionBusy:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A reply is already being generated for this session"
        ) from None
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return ChatResponse(**result)


@router.get("/api/images/{image_id}")
async def get_image(image_id: str, request: Request) -> Response:
    image = await _svc(request).repo.get_image(image_id)
    if image is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found")
    # Image ids are random and never reused, so the bytes behind a URL never change.
    return Response(
        image.data,
        media_type=image.media_type,
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
