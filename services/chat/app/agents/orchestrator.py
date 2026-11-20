"""The lead assistant: plans a message, hands the steps to helpers, and collects their reports.

The reply itself is written afterwards by the chat service's normal streaming path, with the
reports in its system prompt, so only the final answer is ever streamed as `token` events
(and read aloud in voice mode).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable
from datetime import datetime
from typing import TYPE_CHECKING, TypeVar

from ..llm import ChatTurn, GenerationParams, LLMAuthError, LLMError, LLMProvider, normalize_turns
from ..llm.base import AgentTurn
from ..schemas import ActionOut, AgentStepTrace, AgentTrace, ToolTrace, WebResults
from . import prompts
from .specialists import BY_NAME, CODE_RESEARCHER, AgentSpec, available_agents, available_tools
from .tools import ToolResult, Toolbox, clip, preview

if TYPE_CHECKING:
    from ..chat_service import ChatEvent, ChatService

log = logging.getLogger(__name__)
T = TypeVar("T")

PLANNER_TURNS = 8  # recent messages the planner sees
PLANNER_TURN_CHARS = 4_000
PLANNER_MAX_TOKENS = 400
MAX_REPORT_CHARS = 6_000
TIMED_OUT = "The helpers ran out of time, so this answer uses what they found so far."


class Orchestrator:
    def __init__(
        self,
        chat: ChatService,
        provider: LLMProvider,
        model: str,
        session_id: str,
        content: str,
        now: datetime,
        code_question: bool = False,
    ):
        self.chat = chat
        # The message is about a code repository: the code researcher works on it even when the
        # planner (a small local model, say) would answer from memory or only search the web.
        self.code_question = code_question
        self.provider = provider
        self.model = model
        self.content = content
        self.now = now
        self.settings = chat.settings
        self.box = Toolbox(chat, session_id, content)
        self.deadline = time.monotonic() + self.settings.agent_timeout_seconds
        self.out_of_time = False
        # Filled in while it runs; read by the chat service afterwards.
        self.trace = AgentTrace()
        self.findings: list[tuple[str, str]] = []
        self.actions: list[ActionOut] = []
        self.web: WebResults | None = None

    @property
    def worked(self) -> bool:
        return bool(self.trace.steps)

    def responder_context(self, spoken: bool) -> str:
        return prompts.responder_context(self.findings, self.actions, self.trace.note, spoken)

    # ---- running ------------------------------------------------------------------

    async def run(self, history: list[ChatTurn]) -> AsyncIterator[ChatEvent]:
        from ..chat_service import ChatEvent

        agents = available_agents(self.box)
        steps = await self._plan(history, agents)
        if self.code_question and any(a.name == CODE_RESEARCHER for a in agents):
            if not any(s.name == CODE_RESEARCHER for s, _ in steps):
                task = f"Look this up in the code repository it is about: {clip(self.content, 1_800)}"
                steps = [(BY_NAME[CODE_RESEARCHER], task), *steps][: self.settings.agent_max_steps]
        if not steps:
            return
        self.trace.steps = [
            AgentStepTrace(agent=s.name, title=s.title, task=task, status="waiting") for s, task in steps
        ]
        yield ChatEvent(
            "plan", {"steps": [{"agent": s.name, "title": s.title, "task": t} for s, t in steps]}
        )
        for i, (spec, task) in enumerate(steps):
            step = self.trace.steps[i]
            if self.out_of_time or self._left() <= 0:
                self.trace.note = TIMED_OUT
                for rest in self.trace.steps[i:]:
                    rest.status = "skipped"
                    yield ChatEvent("agent", self._agent_event(self.trace.steps.index(rest), rest))
                break
            step.status = "running"
            yield ChatEvent("agent", self._agent_event(i, step))
            async for event in self._work(i, spec, task, step):
                yield event
            yield ChatEvent("agent", self._agent_event(i, step))

    async def _plan(self, history: list[ChatTurn], agents: list[AgentSpec]) -> list[tuple[AgentSpec, str]]:
        if not agents:
            return []
        max_steps = self.settings.agent_max_steps
        turns = normalize_turns(
            [ChatTurn(t.role, clip(t.content, PLANNER_TURN_CHARS)) for t in history[-PLANNER_TURNS:]]
        )
        params = GenerationParams(
            model=self.model,
            system=prompts.planner_prompt(agents, max_steps, self.now),
            temperature=0.1,
            # A plan is small. A cap matters on CPU, where schema-constrained output is slow and
            # a small model that rambles would otherwise keep the user waiting for minutes.
            max_tokens=min(PLANNER_MAX_TOKENS, self.settings.max_output_tokens),
        )
        schema = prompts.plan_schema([a.name for a in agents], max_steps)
        started = time.perf_counter()
        try:
            data = await self._timed(self.provider.complete_json(turns, params, schema))
        except LLMAuthError:
            raise
        except (LLMError, TimeoutError) as exc:
            # No usable plan: the reply is written without helpers.
            log.warning("agent planning failed after %.1fs: %r", time.perf_counter() - started, exc)
            return []
        allowed = {a.name for a in agents}
        steps = []
        for item in data.get("steps") or []:
            if not isinstance(item, dict):
                continue
            name, task = item.get("agent"), str(item.get("task") or "").strip()
            if name in allowed and task:
                steps.append((BY_NAME[name], clip(task, 2_000)))
        log.info(
            "agent plan: %s in %.1fs", [s.name for s, _ in steps] or "direct", time.perf_counter() - started
        )
        return steps[:max_steps]

    async def _work(self, i: int, spec: AgentSpec, task: str, step: AgentStepTrace) -> AsyncIterator[ChatEvent]:
        """One helper's tool loop."""
        from ..chat_service import ChatEvent

        tools = [self.box.spec(name) for name in available_tools(spec, self.box)]
        params = GenerationParams(
            model=self.model,
            system=prompts.specialist_prompt(spec, self.now),
            temperature=0.2,
            max_tokens=self.settings.max_output_tokens,
        )
        turns = [AgentTurn("user", prompts.task_message(task, self.content, self.findings))]
        budget = self.settings.agent_max_tool_calls
        used = 0
        last_output = ""
        report = ""
        started = time.perf_counter()
        while True:
            wrap_up = used >= budget
            try:
                result = await self._timed(self.provider.run_tools_step(turns, params, tools))
            except LLMAuthError:
                raise
            except TimeoutError:
                self.trace.note = TIMED_OUT
                step.status, report = "failed", "Ran out of time."
                break
            except LLMError as exc:
                step.status, report = "failed", str(exc)
                break
            if not result.tool_calls or wrap_up:
                report = result.text
                step.status = "done"
                break
            turns.append(
                AgentTurn("assistant", result.text, result.tool_calls, provider_data=result.provider_data)
            )
            for call in result.tool_calls:
                trace = ToolTrace(tool=call.name, input=preview(call.arguments), status="running")
                step.tools.append(trace)
                index = len(step.tools) - 1
                yield ChatEvent("tool", self._tool_event(i, index, spec, trace))
                if call.name == "web_search" and isinstance(call.arguments.get("query"), str):
                    yield ChatEvent("searching", {"query": call.arguments["query"]})
                if used >= budget:
                    out = ToolResult("Not run: no tool calls left.", ok=False)
                else:
                    used += 1
                    try:
                        out = await self._timed(self.box.call(call))
                    except TimeoutError:
                        out = ToolResult("Error: out of time.", ok=False)
                trace.status = "done" if out.ok else "failed"
                trace.output = preview(out.text)
                if out.action:
                    trace.action_id = out.action.id
                    self.actions.append(out.action)
                    yield ChatEvent("action", {"action": out.action.model_dump(mode="json")})
                if out.web:
                    self.web = out.web
                    yield ChatEvent("web", out.web.model_dump(mode="json"))
                elif call.name == "web_search":
                    yield ChatEvent("web", {"query": call.arguments.get("query", ""), "links": [], "related": [], "error": out.text})
                yield ChatEvent("tool", self._tool_event(i, index, spec, trace))
                if out.ok:
                    last_output = out.text
                turns.append(AgentTurn("tool", out.text, tool_call_id=call.id, tool_name=call.name))
            if used >= budget:
                turns.append(AgentTurn("user", prompts.WRAP_UP))
        report = report.strip() or (f"(No written report.) Last result:\n{last_output}" if last_output else "(No report.)")
        step.summary = clip(report, MAX_REPORT_CHARS)
        self.findings.append((spec.title, step.summary))
        log.info(
            "agent %s: %s, %d tool calls in %.1fs", spec.name, step.status, used, time.perf_counter() - started
        )

    # ---- helpers --------------------------------------------------------------------

    def _left(self) -> float:
        return self.deadline - time.monotonic()

    async def _timed(self, aw: Awaitable[T]) -> T:
        left = self._left()
        if self.out_of_time or left <= 0:
            if asyncio.iscoroutine(aw):
                aw.close()
            self.out_of_time = True
            raise TimeoutError
        try:
            return await asyncio.wait_for(aw, left)
        except TimeoutError:
            self.out_of_time = True
            raise

    @staticmethod
    def _agent_event(i: int, step: AgentStepTrace) -> dict:
        return {
            "step": i,
            "agent": step.agent,
            "title": step.title,
            "task": step.task,
            "status": step.status,
            "summary": preview(step.summary) if step.summary else "",
        }

    @staticmethod
    def _tool_event(i: int, index: int, spec: AgentSpec, trace: ToolTrace) -> dict:
        return {"step": i, "index": index, "agent": spec.name, **trace.model_dump()}
