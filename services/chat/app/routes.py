"""HTTP routes for the chat service."""

import asyncio
import ipaddress
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import aclosing

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from .automation import ActionInvalid, Automation, CredentialsRejected, event_ics, validate_payload
from .chat_service import ChatEvent, ChatService, SessionBusy, SessionNotFound
from .llm import LLMError
from .notifications import NotificationCenter, device_label
from .notifier import Notifier
from .schemas import (
    ActionApprove,
    ActionDraftRequest,
    ActionOut,
    ActionStatus,
    AutomationInfo,
    ChannelPrefs,
    ChatRequest,
    ChatResponse,
    ConfigOut,
    EventOut,
    MAX_IMAGES_PER_MESSAGE,
    DeviceHello,
    HealthOut,
    NotificationCategory,
    NotificationList,
    NotificationRead,
    NotificationReport,
    NotificationSettings,
    NotifyTestOut,
    RuntimeConfigUpdate,
    SessionCreate,
    SessionDetail,
    SessionOut,
    SessionUpdate,
)

router = APIRouter()
log = logging.getLogger(__name__)

HEALTH_CACHE_SECONDS = 15.0


def _svc(request: Request) -> ChatService:
    return request.app.state.chat


def _automation(request: Request) -> Automation:
    return request.app.state.automation


def _notifier(request: Request) -> Notifier:
    return request.app.state.notifier


def _notifications(request: Request) -> NotificationCenter:
    return request.app.state.notifications


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


# ---- task automation ----------------------------------------------------------
# The model only drafts. An action runs when, and only when, the user approves it.


@router.get("/api/automation", response_model=AutomationInfo)
async def automation_info(request: Request) -> AutomationInfo:
    auto = _automation(request)
    return AutomationInfo(
        email_delivery=auto.email_delivery,
        workflows=list(auto.settings.workflows),
        notify_channels=_notifier(request).channels,
        notify_remind_minutes=auto.settings.notify_remind_minutes,
    )


@router.post("/api/notify/test", response_model=NotifyTestOut)
async def test_notification(request: Request) -> NotifyTestOut:
    notifier = _notifier(request)
    if not notifier.channels:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Phone notifications aren't set up. Add NOTIFY_PHONE and the Twilio settings, "
            "or NTFY_TOPIC, to .env (see the README).",
        )
    errors = await notifier.send(
        "Test from your assistant", "Phone notifications work. You'll get one when a task needs review."
    )
    return NotifyTestOut(channels=notifier.channels, errors=errors)


@router.post(
    "/api/sessions/{session_id}/actions",
    response_model=ActionOut,
    status_code=status.HTTP_201_CREATED,
)
async def draft_action(
    session_id: str, body: ActionDraftRequest, request: Request, background: BackgroundTasks
) -> ActionOut:
    if body.kind == "workflow" and not _automation(request).settings.workflows:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No workflows are set up. Add WORKFLOWS to .env (see the README).",
        )
    try:
        action = await _svc(request).draft_action(
            session_id, body.kind, body.instruction.strip(), body.now
        )
    except SessionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found") from None
    except SessionBusy:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Wait for the current reply to finish first"
        ) from None
    except LLMError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    # After the response, so a slow SMS gateway never delays the review card.
    background.add_task(_notifications(request).review_needed, action)
    return action


@router.get("/api/actions", response_model=list[ActionOut])
async def list_actions(request: Request, status: ActionStatus | None = None) -> list[ActionOut]:
    return await _svc(request).repo.list_actions(status=status)


async def _pending_action(request: Request, action_id: str) -> ActionOut:
    action = await _svc(request).repo.get_action(action_id)
    if action is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Action not found")
    if action.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This action is already {action.status}")
    return action


@router.post("/api/actions/{action_id}/approve", response_model=ActionOut)
async def approve_action(action_id: str, body: ActionApprove, request: Request) -> ActionOut:
    repo, auto = _svc(request).repo, _automation(request)
    action = await _pending_action(request, action_id)
    try:
        payload = validate_payload(action.kind, body.payload, auto.settings.workflows)
    except ActionInvalid as exc:
        raise HTTPException(422, str(exc)) from None
    # Claim it atomically: a second approve (double click, two tabs) gets 409, not a resend.
    if not await repo.set_action_status(action_id, "running", expect="pending"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This action is already being handled")
    notes = _notifications(request)
    label = action.kind.capitalize()
    try:
        result = await auto.execute(action, payload)
    except ActionInvalid as exc:
        if isinstance(exc, CredentialsRejected):
            await notes.emit("credentials", "Mail login rejected", str(exc), level="error", session_id=action.session_id)
        else:
            await notes.emit("task", f"{label} failed", str(exc), level="error", session_id=action.session_id, dedupe=False)
        return await repo.finish_action(action_id, payload, error=str(exc))
    except Exception:
        log.exception("action %s failed", action_id)
        error = "Unexpected error while running this action."
        await notes.emit("task", f"{label} failed", error, level="error", session_id=action.session_id, dedupe=False)
        return await repo.finish_action(action_id, payload, error=error)
    log.info("action %s (%s) approved and done", action_id, action.kind)
    await notes.emit("task", f"{label} done", result["message"], session_id=action.session_id, dedupe=False)
    return await repo.finish_action(action_id, payload, result=result)


@router.post("/api/actions/{action_id}/reject", response_model=ActionOut)
async def reject_action(action_id: str, request: Request) -> ActionOut:
    repo = _svc(request).repo
    await _pending_action(request, action_id)
    if not await repo.set_action_status(action_id, "rejected", expect="pending"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This action is already being handled")
    action = await repo.get_action(action_id)
    assert action is not None
    return action


@router.get("/api/events", response_model=list[EventOut])
async def list_events(request: Request, since: str | None = None) -> list[EventOut]:
    """Scheduled events, by start time. `since` (local YYYY-MM-DDTHH:MM) skips ended ones."""
    return await _svc(request).repo.list_events(since)


@router.delete("/api/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(event_id: str, request: Request) -> Response:
    if not await _svc(request).repo.delete_event(event_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _attachment(title: str, ext: str) -> str:
    name = re.sub(r"[^A-Za-z0-9 _-]+", "", title).strip()[:60] or "download"
    return f'attachment; filename="{name}.{ext}"'


@router.get("/api/events/{event_id}/ics")
async def event_calendar_file(event_id: str, request: Request) -> Response:
    event = await _svc(request).repo.get_event(event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return Response(
        event_ics(event.model_dump()),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": _attachment(event.title, "ics")},
    )


@router.get("/api/documents/{doc_id}/download")
async def download_document(doc_id: str, request: Request) -> Response:
    doc = await _svc(request).repo.get_document(doc_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    title, content = doc
    return Response(
        content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _attachment(title, "md")},
    )


# ---- notifications --------------------------------------------------------------
# Stored for the bell list, streamed to open browsers, and sent to the phone per category.


@router.get("/api/notifications", response_model=NotificationList)
async def list_notifications(request: Request, limit: int = 50) -> NotificationList:
    items, unread = await _svc(request).repo.list_notifications(max(1, min(limit, 200)))
    return NotificationList(items=items, unread=unread)


@router.get("/api/notifications/stream")
async def notification_stream(request: Request) -> StreamingResponse:
    """New notifications as Server-Sent Events: `notification` {...NotificationOut, popup}."""
    center = _notifications(request)
    queue = center.subscribe()

    async def events() -> AsyncIterator[str]:
        try:
            yield ": connected\n\n"
            while True:
                try:
                    note, popup = await asyncio.wait_for(queue.get(), timeout=25)
                except TimeoutError:
                    yield ": keep-alive\n\n"  # proxies close idle connections
                    continue
                data = {**note.model_dump(mode="json"), "popup": popup}
                yield f"event: notification\ndata: {json.dumps(data)}\n\n"
        finally:
            center.unsubscribe(queue)

    return ClosingStreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/notifications/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_notifications_read(body: NotificationRead, request: Request) -> Response:
    await _svc(request).repo.mark_notifications_read(body.ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/api/notifications", status_code=status.HTTP_204_NO_CONTENT)
async def clear_notifications(request: Request) -> Response:
    await _svc(request).repo.clear_notifications()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/notifications/settings", response_model=NotificationSettings)
async def notification_settings(request: Request) -> NotificationSettings:
    return await _notifications(request).settings()


@router.put("/api/notifications/settings", response_model=NotificationSettings)
async def update_notification_settings(
    body: dict[NotificationCategory, ChannelPrefs], request: Request
) -> NotificationSettings:
    center = _notifications(request)
    await center.set_prefs(body)
    return await center.settings()


@router.post("/api/notifications/report", status_code=status.HTTP_202_ACCEPTED)
async def report_notification(body: NotificationReport, request: Request) -> dict:
    """Events only the browser sees, such as picking too many photos."""
    note = await _notifications(request).emit(
        "photo_limit",
        "Photo limit reached",
        f"A message can carry up to {MAX_IMAGES_PER_MESSAGE} pictures, and you tried to add "
        f"{body.count}. The extra ones were left out; send them in another message.",
        level="warning",
        dedupe=False,
    )
    return {"id": note.id if note else None}


@router.post("/api/devices/hello")
async def device_hello(body: DeviceHello, request: Request) -> dict:
    """Called when the app opens. The first visit from a browser or phone is a new sign-in."""
    label = device_label(request.headers.get("user-agent", ""))
    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    new = await _svc(request).repo.touch_device(body.device_id, label, ip)
    if new:
        where = f" at {ip}" if _real_client_ip(ip) else ""
        await _notifications(request).emit(
            "sign_in",
            "New device opened your assistant",
            f"{label}{where}. If this wasn't you, someone on your network can reach the assistant.",
            level="warning",
        )
    return {"new": new}


def _real_client_ip(ip: str) -> bool:
    """Docker Desktop shows every visitor as its own gateway (172.16.0.0/12); don't print that."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_loopback or addr in ipaddress.ip_network("172.16.0.0/12"))
