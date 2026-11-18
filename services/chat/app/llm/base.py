"""Provider-agnostic interface for streaming chat completions."""

import base64
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any, Literal
from uuid import uuid4


@dataclass(frozen=True)
class ImageData:
    media_type: str  # image/jpeg | image/png
    data: bytes

    def base64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


@dataclass(frozen=True)
class ChatTurn:
    role: Literal["user", "assistant"]
    content: str
    images: tuple[ImageData, ...] = ()


@dataclass(frozen=True)
class GenerationParams:
    model: str
    system: str
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class ToolSpec:
    """A tool an agent may call. `parameters` is a JSON schema for the arguments object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AgentTurn:
    """One message in an agent's tool loop.

    `assistant` turns may carry tool calls; each `tool` turn answers one call by id.
    `provider_data` is opaque content a provider needs echoed back on the next step
    (Claude's content blocks, for example); other providers ignore it.
    """

    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str = ""
    tool_name: str = ""
    provider_data: Any = field(default=None, compare=False)


@dataclass(frozen=True)
class AgentStep:
    """What the model did in one step: text, tool calls, or both. No calls means it finished."""

    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    truncated: bool = False
    provider_data: Any = field(default=None, compare=False)


def new_call_id() -> str:
    return f"call_{uuid4().hex[:12]}"


class LLMError(Exception):
    """A provider failure with a message that is safe to show to the user."""


class LLMAuthError(LLMError):
    """The provider refused the API key or token."""


# Yielded as the last fragment when a reply stops at max_tokens; the chat service watches for it.
TRUNCATED_NOTE = "\n\n(Response truncated: output token limit reached.)"


class LLMProvider(ABC):
    name: str
    default_model: str

    @abstractmethod
    def stream(self, turns: list[ChatTurn], params: GenerationParams) -> AsyncIterator[str]:
        """Yield the assistant reply as text fragments."""

    @abstractmethod
    async def check(self, model: str) -> tuple[bool, str]:
        """Return (ok, human-readable status) for the given model."""

    async def complete_json(
        self, turns: list[ChatTurn], params: GenerationParams, schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Return one JSON object matching `schema`. Providers with native structured
        output override this; the default asks for JSON and parses the reply."""
        instruction = (
            "Reply with only a JSON object that matches this JSON schema, and no other text:\n"
            + json.dumps(schema)
        )
        params = replace(params, system=f"{params.system}\n\n{instruction}".strip())
        text = "".join([part async for part in self.stream(turns, params)])
        return parse_json_object(text)

    async def run_tools_step(
        self, turns: list[AgentTurn], params: GenerationParams, tools: list[ToolSpec]
    ) -> AgentStep:
        """One step of a tool loop: the model either calls tools or gives its answer.

        Providers with native tool calling override this. The default asks for a JSON
        choice ({"tool", "arguments", "answer"}) through `complete_json`, which works with
        any model that can follow a schema, one tool call per step.
        """
        names = [t.name for t in tools]
        schema = fallback_tool_schema(names)
        system = f"{params.system}\n\n{fallback_tool_instructions(tools)}".strip()
        data = await self.complete_json(render_agent_turns(turns), replace(params, system=system), schema)
        return parse_fallback_choice(data, set(names))

    async def warm_up(self, model: str) -> None:
        """Load the model ahead of the first request, where the provider supports it."""
        return None

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None


def normalize_turns(turns: list[ChatTurn]) -> list[ChatTurn]:
    """Drop empty turns, start on a user turn, and merge consecutive same-role turns.

    Interrupted replies or failed requests can leave gaps in stored history; most
    providers require a clean user/assistant alternation that starts with the user.
    """
    out: list[ChatTurn] = []
    for turn in turns:
        if not turn.content.strip() and not turn.images:
            continue
        if not out and turn.role != "user":
            continue
        if out and out[-1].role == turn.role:
            prev = out[-1]
            content = "\n\n".join(c for c in (prev.content, turn.content) if c.strip())
            out[-1] = ChatTurn(turn.role, content, prev.images + turn.images)
        else:
            out.append(turn)
    return out


def parse_json_object(text: str) -> dict[str, Any]:
    """The first {...} object in a model reply, tolerating code fences and chatter around it."""
    start, end = text.find("{"), text.rfind("}")
    try:
        value = json.loads(text[start : end + 1]) if start != -1 and end > start else None
    except json.JSONDecodeError:
        value = None
    if not isinstance(value, dict):
        raise LLMError("The model did not return a usable draft. Try rephrasing the request.")
    return value


def last_user_images(turns: list[ChatTurn]) -> tuple[ImageData, ...]:
    return next((t.images for t in reversed(turns) if t.role == "user"), ())


# --- JSON fallback for models without native tool calling -----------------------------

FINISH = "finish"


def fallback_tool_schema(names: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": [*names, FINISH]},
            "arguments": {"type": "object"},
            "answer": {"type": "string"},
        },
        "required": ["tool", "arguments", "answer"],
    }


def fallback_tool_instructions(tools: list[ToolSpec]) -> str:
    lines = [
        "You can use these tools, one per reply:",
        *(f"- {t.name}: {t.description} Arguments (JSON schema): {json.dumps(t.parameters)}" for t in tools),
        f'To use a tool, set "tool" to its name and "arguments" to its arguments. When you have '
        f'what you need, set "tool" to "{FINISH}", "arguments" to {{}}, and write your result '
        'in "answer".',
    ]
    return "\n".join(lines)


def render_agent_turns(turns: list[AgentTurn]) -> list[ChatTurn]:
    """Agent turns as plain chat turns: tool calls and results written out as text."""
    out: list[ChatTurn] = []
    for t in turns:
        if t.role == "tool":
            out.append(ChatTurn("user", tool_result_text(t.tool_name, t.content)))
        elif t.role == "assistant":
            calls = "\n".join(
                f"(I used the tool {c.name} with {json.dumps(c.arguments)}.)" for c in t.tool_calls
            )
            out.append(ChatTurn("assistant", "\n".join(x for x in (t.content, calls) if x)))
        else:
            out.append(ChatTurn("user", t.content))
    return normalize_turns(out)


def tool_result_text(name: str, content: str) -> str:
    return f'<tool_result name="{name}">\n{content}\n</tool_result>'


def parse_fallback_choice(data: dict[str, Any], names: set[str]) -> AgentStep:
    tool = data.get("tool")
    answer = data.get("answer") if isinstance(data.get("answer"), str) else ""
    if isinstance(tool, str) and tool in names:
        args = data.get("arguments")
        call = ToolCall(new_call_id(), tool, args if isinstance(args, dict) else {})
        return AgentStep("", (call,))
    return AgentStep(answer.strip())
