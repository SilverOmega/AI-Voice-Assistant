"""Orchestrates a chat turn: persist the user message, stream the model reply, persist the reply."""

import asyncio
import html
import logging
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .agents import Orchestrator
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
from .schemas import ActionOut, AgentTrace, FileRef, MessageOut, RuntimeConfig, Source, WebResults
from .github import GitHubClient
from .search import SearchEngine, SearchError, about_code, results_context, search_query, worth_searching
from .translate import TranslationCache, reply_language_instruction, translate
from .workspace import Workspace

log = logging.getLogger(__name__)


def with_documents(content: str, documents: Sequence[tuple[str, str]], budget: int) -> tuple[str, int]:
    """A user message with its attached documents in front of it, and the budget left.

    Documents come first and the question last: models answer long documents best that way.
    `budget` is how many characters of document text may still be sent with this request.
    """
    if not documents:
        return content, budget
    blocks = []
    for name, text in documents:
        taken = text[: max(budget, 0)]
        budget -= len(taken)
        if not taken:
            body = "[Not included: newer documents in this chat use up the room. Attach it again to ask about it.]"
        elif len(taken) < len(text):
            body = (
                f"{taken}\n[Cut here: the remaining {len(text) - len(taken):,} characters don't fit. "
                "Ask about a specific part, or attach a shorter excerpt.]"
            )
        else:
            body = taken
        blocks.append(f'<document name="{html.escape(name)}">\n{body}\n</document>')
    return "\n\n".join(blocks + [content]).strip(), budget


class SessionNotFound(Exception):
    pass


class SessionBusy(Exception):
    pass


@dataclass
class ChatEvent:
    # start | token | done | error; searching | web with web search;
    # plan | agent | tool | action while helpers work (see README, "Chat stream events")
    event: str
    data: dict[str, Any] = field(default_factory=dict)


class ChatService:
    def __init__(
        self,
        repo: Repository,
        registry: ProviderRegistry,
        settings: Settings,
        runtime: RuntimeConfig,
        notifications: NotificationCenter | None = None,
        search: SearchEngine | None = None,
        workspace: Workspace | None = None,
        github: GitHubClient | None = None,
    ):
        self.repo = repo
        self.workspace = workspace
        self.github = github
        self.notifications = notifications
        self.search = search
        self.registry = registry
        self.settings = settings
        self.runtime = runtime
        self._locks: dict[str, asyncio.Lock] = {}
        self._translations = TranslationCache()
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
        files: Sequence[FileRef] = (),
        language: str | None = None,
        search: bool = False,
        agents: bool = False,
        now: str | None = None,
    ) -> AsyncIterator[ChatEvent]:
        """Run one turn. Raises SessionNotFound / SessionBusy before the first event.

        `language`: the language to reply in; None leaves it to the model (it follows the user).
        `search`: look the message up on the web first and answer from the results.
        `agents`: let the helpers work on it first (see `use_agents`).
        `now`: the user's local time, YYYY-MM-DDTHH:MM.
        """
        if await self.repo.get_session(session_id) is None:
            raise SessionNotFound(session_id)
        lock = self._lock_for(session_id)
        if lock.locked():
            raise SessionBusy(session_id)
        content = content.strip()
        helpers = self.use_agents(agents, content, files, source)
        return self._run_turn(lock, session_id, content, source, images, files, language, search, helpers, now)

    def use_agents(
        self, asked: bool, content: str, files: Sequence[FileRef] = (), source: Source = "text"
    ) -> bool:
        """Helpers work on a message when the user turned them on for it, or on every message
        with AGENT_MODE=always, where small talk ("thanks!") skips them to stay quick.

        With AGENT_MODE=toggle they also start on their own for a typed question about a code
        repository ("explain the latest commit of ..."), which only the code researcher's tools
        can answer. Spoken turns keep the switch: helpers make a voice reply much slower.
        """
        mode = self.settings.agent_mode
        if mode == "off":
            return False
        if asked:
            return True
        if mode == "always":
            return bool(files) or worth_searching(content)
        return source == "text" and self.github is not None and about_code(content)

    async def _run_turn(
        self,
        lock: asyncio.Lock,
        session_id: str,
        content: str,
        source: Source,
        images: Sequence[ImageData],
        files: Sequence[FileRef],
        language: str | None,
        search: bool,
        helpers: bool = False,
        now: str | None = None,
    ) -> AsyncIterator[ChatEvent]:
        parts: list[str] = []
        saved: MessageOut | None = None
        web: WebResults | None = None
        team: Orchestrator | None = None
        # Acquired inside the generator so an un-iterated generator can never leak the lock.
        await lock.acquire()
        try:
            user_msg = await self.repo.add_message(session_id, "user", content, source, images, files)
            turns = await self._history_turns(session_id)
            provider = self.registry.get(self.runtime.provider)
            model = self.resolved_model()
            yield ChatEvent(
                "start",
                {"user_message": user_msg.model_dump(mode="json"), "provider": provider.name, "model": model},
            )
            system = self.runtime.system_prompt
            if helpers:
                team = Orchestrator(
                    self, provider, model, session_id, content, _local_now(now), code_question=about_code(content)
                )
                async with aclosing(team.run(turns)) as work:
                    async for event in work:
                        yield event
                web = team.web
                if team.worked:
                    system = f"{system}\n\n{team.responder_context(spoken=source == 'voice')}".strip()
            # Pictures and documents are what the message is about: the web would only add noise.
            elif search and self.search and not images and not files and worth_searching(content):
                query = search_query(content)
                yield ChatEvent("searching", {"query": query})
                web, failure = await self._web_search(query)
                if web is None:
                    yield ChatEvent("web", {"query": query, "links": [], "related": [], "error": failure})
                else:
                    yield ChatEvent("web", web.model_dump(mode="json"))
                    system = f"{system}\n\n{results_context(web, spoken=source == 'voice')}".strip()
            if language:
                system = f"{system}\n\n{reply_language_instruction(language)}".strip()
            params = GenerationParams(
                model=model,
                system=system,
                temperature=self.runtime.temperature,
                max_tokens=self.settings.max_output_tokens,
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
            saved = await self._save(session_id, parts, source, web, _trace(team))
            log.info(
                "turn complete session=%s provider=%s chars=%d secs=%.2f",
                session_id, provider.name, len("".join(parts)), time.perf_counter() - started,
            )
            yield ChatEvent("done", {"message": saved.model_dump(mode="json") if saved else None})
        except LLMError as exc:
            saved = await self._save(session_id, parts, source, web, _trace(team))
            if isinstance(exc, LLMAuthError):
                await self._notify_rejected(exc, session_id)
            yield ChatEvent("error", {"message": str(exc)})
        except Exception:
            log.exception("unexpected error during chat turn")
            saved = await self._save(session_id, parts, source, web, _trace(team))
            yield ChatEvent("error", {"message": "Unexpected server error while generating a reply."})
        finally:
            try:
                # Client disconnected or the turn was cancelled: keep whatever was generated.
                # Under anyio (Starlette) cancellation is level-triggered, so this await can
                # itself raise CancelledError; the shield lets the save finish regardless.
                if saved is None and parts:
                    await asyncio.shield(self._save(session_id, parts, source, web, _trace(team)))
            finally:
                lock.release()

    async def _history_turns(self, session_id: str, images: bool = True) -> list[ChatTurn]:
        """The recent conversation for the model, with attached pictures and documents."""
        history = [
            m
            for m in await self.repo.list_messages(session_id, limit=self.settings.max_history_messages)
            if m.role in ("user", "assistant")
        ]
        pictures = await self.repo.image_data([m.id for m in history if m.images]) if images else {}
        documents = await self.repo.file_texts([m.id for m in history if m.files])
        # The newest documents get the room first; older ones are cut or left out.
        budget = self.settings.document_max_chars
        contents: dict[str, str] = {}
        for m in reversed(history):
            contents[m.id], budget = with_documents(m.content, documents.get(m.id, ()), budget)
        return normalize_turns(
            [ChatTurn(m.role, contents[m.id], tuple(pictures.get(m.id, ()))) for m in history]
        )

    async def _save(
        self,
        session_id: str,
        parts: list[str],
        source: Source,
        web: WebResults | None = None,
        agent: AgentTrace | None = None,
    ) -> MessageOut | None:
        text = "".join(parts).strip()
        if not text:
            return None
        found = web if web and (web.links or web.related) else None
        msg = await self.repo.add_message(session_id, "assistant", text, source, web=found, agent=agent)
        parts.clear()
        return msg

    async def _web_search(self, query: str) -> tuple[WebResults | None, str]:
        """(results, "") or (None, why it failed). A failed search never stops the reply."""
        assert self.search is not None
        started = time.perf_counter()
        try:
            web = await self.search.search(query, self.settings.web_search_results)
        except SearchError as exc:
            log.warning("web search failed: %s", exc)
            return None, f"Web search didn't work: {exc}"
        except Exception:
            log.exception("web search failed")
            return None, "Web search didn't work, so this reply comes from the model alone."
        log.info(
            "web search via %s: %d links, %d related in %.1fs",
            self.search.name, len(web.links), len(web.related), time.perf_counter() - started,
        )
        return web, ""

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
            history = await self._history_turns(session_id, images=False)
            turns = normalize_turns([ChatTurn(t.role, t.content) for t in history] + [ChatTurn("user", instruction)])
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

    async def translate(self, text: str, target: str) -> str:
        """`text` in the `target` language. Not part of any conversation, so no session lock:
        someone following a chat can translate while a reply is still being written."""
        model = self.resolved_model()
        key = (self.runtime.provider, model, target.casefold(), text)
        cached = self._translations.get(key)
        if cached is not None:
            return cached
        provider = self.registry.get(self.runtime.provider)
        started = time.perf_counter()
        try:
            result = await translate(provider, model, text, target, self.settings.max_output_tokens)
        except LLMAuthError as exc:
            await self._notify_rejected(exc, None)
            raise
        if not result:
            raise LLMError("The model returned an empty translation. Try again.")
        log.info("translated %d chars into %s in %.1fs", len(text), target, time.perf_counter() - started)
        self._translations.put(key, result)
        return result

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

    async def _notify_rejected(self, exc: LLMAuthError, session_id: str | None) -> None:
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
        files: Sequence[FileRef] = (),
        language: str | None = None,
        search: bool = False,
        agents: bool = False,
        now: str | None = None,
    ) -> dict:
        """Non-streaming variant: collect the whole turn."""
        user_message = assistant_message = None
        events = await self.stream_reply(
            session_id, content, source, images, files, language, search, agents, now
        )
        async with aclosing(events):
            async for event in events:
                if event.event == "start":
                    user_message = event.data["user_message"]
                elif event.event == "done":
                    assistant_message = event.data["message"]
                elif event.event == "error":
                    raise LLMError(event.data["message"])
        return {"user_message": user_message, "assistant_message": assistant_message}


def _trace(team: Orchestrator | None) -> AgentTrace | None:
    return team.trace if team and team.worked else None


def _local_now(now: str | None) -> datetime:
    if now:
        try:
            return datetime.strptime(now[:16], "%Y-%m-%dT%H:%M")
        except ValueError:
            pass
    return datetime.now()
