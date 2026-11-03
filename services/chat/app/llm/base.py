"""Provider-agnostic interface for streaming chat completions."""

import base64
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Any, Literal


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
