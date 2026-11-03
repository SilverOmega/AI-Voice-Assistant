"""Orchestrates a chat turn: persist the user message, stream the model reply, persist the reply."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any

from .automation import clean_draft, draft_schema, draft_system_prompt
from .config import Settings
from .llm import (
    TRUNCATED_NOTE,
    ChatTurn,
    GenerationParams,
    ImageData,
    LLMAuthError,
    LLMError,
    ProviderRegistry,
    normalize_turns,
)
from .notifications import NotificationCenter
from .repository import Repository
from .schemas import ActionOut, MessageOut, RuntimeConfig, Source

log = logging.getLogger(__name__)


class SessionNotFound(Exception):
    pass


class SessionBusy(Exception):
    pass


@dataclass
class ChatEvent:
    event: str  # start | token | done | error
    data: dict[str, Any] = field(default_factory=dict)


class ChatService:
    def __init__(
        self,
        repo: Repository,
        registry: ProviderRegistry,
        settings: Settings,
        runtime: RuntimeConfig,
        notifications: NotificationCenter | None = None,
    ):
        self.repo = repo
        self.notifications = notifications
        self.registry = registry
        self.settings = settings
        self.runtime = runtime
        self._locks: dict[str, asyncio.Lock] = {}
        self._warm_up_task: asyncio.Task | None = None

    def resolved_model(self) -> str:
        return self.runtime.model or self.registry.get(self.runtime.provider).default_model

    def start_warm_up(self) -> None:
        """Load the active model in the background so the first reply doesn't wait for it."""
        if self._warm_up_task and not self._warm_up_task.done():
            self._warm_up_task.cancel()
        self._warm_up_task = asyncio.create_task(
            self._warm_up(self.runtime.provider, self.resolved_model())
        )

    async def _warm_up(self, provider_name: str, model: str) -> None:
        started = time.perf_counter()
        try:
            await self.registry.get(provider_name).warm_up(model)
        except Exception as exc:  # best effort: the first real request loads it anyway
            log.warning("warm-up of %s/%s failed: %s", provider_name, model, exc)
            return
        log.info("warm-up of %s/%s took %.1fs", provider_name, model, time.perf_counter() - started)

    async def aclose(self) -> None:
        if self._warm_up_task and not self._warm_up_task.done():
            self._warm_up_task.cancel()

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    async def stream_reply(
        self,
        session_id: str,
        content: str,
        source: Source = "text",
        images: Sequence[ImageData] = (),
    ) -> AsyncIterator[ChatEvent]:
        """Run one turn. Raises SessionNotFound / SessionBusy before the first event."""
        if await self.repo.get_session(session_id) is None:
            raise SessionNotFound(session_id)
        lock = self._lock_for(session_id)
        if lock.locked():
            raise SessionBusy(session_id)
        return self._run_turn(lock, session_id, content.strip(), source, images)

    async def _run_turn(
        self,
        lock: asyncio.Lock,
        session_id: str,
        content: str,
        source: Source,
        images: Sequence[ImageData],
    ) -> AsyncIterator[ChatEvent]:
        parts: list[str] = []
        saved: MessageOut | None = None
        # Acquired inside the generator so an un-iterated generator can never leak the lock.
        await lock.acquire()
        try:
            user_msg = await self.repo.add_message(session_id, "user", content, source, images)
            history = await self.repo.list_messages(
                session_id, limit=self.settings.max_history_messages
            )
            stored = await self.repo.image_data([m.id for m in history if m.images])
            turns = normalize_turns(
                [
                    ChatTurn(m.role, m.content, tuple(stored.get(m.id, ())))
                    for m in history
                    if m.role in ("user", "assistant")
                ]
            )
            provider = self.registry.get(self.runtime.provider)
            params = GenerationParams(
                model=self.resolved_model(),
                system=self.runtime.system_prompt,
                temperature=self.runtime.temperature,
                max_tokens=self.settings.max_output_tokens,
            )
            yield ChatEvent(
                "start",
                {
                    "user_message": user_msg.model_dump(mode="json"),
                    "provider": provider.name,
                    "model": params.model,
                },
            )
            started = time.perf_counter()
            # aclosing: if our consumer goes away mid-reply, close the provider stream now
            # (stopping generation) instead of whenever it is garbage collected.
            async with aclosing(provider.stream(turns, params)) as stream:
                async for text in stream:
                    parts.append(text)
                    yield ChatEvent("token", {"text": text})
                    if text == TRUNCATED_NOTE:
                        await self._notify_truncated(session_id, params.max_tokens)
            saved = await self._save(session_id, parts, source)
            log.info(
                "turn complete session=%s provider=%s chars=%d secs=%.2f",
                session_id, provider.name, len("".join(parts)), time.perf_counter() - started,
            )
            yield ChatEvent("done", {"message": saved.model_dump(mode="json") if saved else None})
        except LLMError as exc:
            saved = await self._save(session_id, parts, source)
            if isinstance(exc, LLMAuthError):
                await self._notify_rejected(exc, session_id)
            yield ChatEvent("error", {"message": str(exc)})
        except Exception:
            log.exception("unexpected error during chat turn")
            saved = await self._save(session_id, parts, source)
            yield ChatEvent("error", {"message": "Unexpected server error while generating a reply."})
        finally:
            try:
                # Client disconnected or the turn was cancelled: keep whatever was generated.
                # Under anyio (Starlette) cancellation is level-triggered, so this await can
                # itself raise CancelledError; the shield lets the save finish regardless.
                if saved is None and parts:
                    await asyncio.shield(self._save(session_id, parts, source))
            finally:
                lock.release()

    async def _save(self, session_id: str, parts: list[str], source: Source) -> MessageOut | None:
        text = "".join(parts).strip()
        if not text:
            return None
        msg = await self.repo.add_message(session_id, "assistant", text, source)
        parts.clear()
        return msg

    async def draft_action(
        self, session_id: str, kind: str, instruction: str, now: str
    ) -> ActionOut:
        """Have the model draft a task and store it for review. Nothing runs here."""
        if await self.repo.get_session(session_id) is None:
            raise SessionNotFound(session_id)
        lock = self._lock_for(session_id)
        if lock.locked():
            raise SessionBusy(session_id)
        async with lock:
            history = await self.repo.list_messages(
                session_id, limit=self.settings.max_history_messages
            )
            turns = normalize_turns(
                [ChatTurn(m.role, m.content) for m in history if m.role in ("user", "assistant")]
                + [ChatTurn("user", instruction)]
            )
            workflows = list(self.settings.workflows)
            params = GenerationParams(
                model=self.resolved_model(),
                system=draft_system_prompt(kind, now, workflows),
                temperature=0.2,
                max_tokens=self.settings.max_output_tokens,
            )
            provider = self.registry.get(self.runtime.provider)
            started = time.perf_counter()
            try:
                data = await provider.complete_json(turns, params, draft_schema(kind, workflows))
            except LLMAuthError as exc:
                await self._notify_rejected(exc, session_id)
                raise
            log.info("drafted %s action in %.1fs", kind, time.perf_counter() - started)
            return await self.repo.add_action(session_id, kind, instruction, clean_draft(kind, data))

    async def _notify_truncated(self, session_id: str, max_tokens: int) -> None:
        if self.notifications:
            await self.notifications.emit(
                "token_limit",
                "Reply cut off at the token limit",
                f"The reply stopped at {max_tokens} tokens. Ask it to continue, or raise "
                "MAX_OUTPUT_TOKENS in .env.",
                level="warning",
                session_id=session_id,
            )

    async def _notify_rejected(self, exc: LLMAuthError, session_id: str) -> None:
        if self.notifications:
            await self.notifications.emit(
                "credentials", "API key rejected", str(exc), level="error", session_id=session_id
            )

    async def reply(
        self,
        session_id: str,
        content: str,
        source: Source = "text",
        images: Sequence[ImageData] = (),
    ) -> dict:
        """Non-streaming variant: collect the whole turn."""
        user_message = assistant_message = None
        events = await self.stream_reply(session_id, content, source, images)
        async with aclosing(events):
            async for event in events:
                if event.event == "start":
                    user_message = event.data["user_message"]
                elif event.event == "done":
                    assistant_message = event.data["message"]
                elif event.event == "error":
                    raise LLMError(event.data["message"])
        return {"user_message": user_message, "assistant_message": assistant_message}
