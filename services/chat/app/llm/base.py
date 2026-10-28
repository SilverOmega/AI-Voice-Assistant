"""Provider-agnostic interface for streaming chat completions."""

import base64
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal


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


class LLMProvider(ABC):
    name: str
    default_model: str

    @abstractmethod
    def stream(self, turns: list[ChatTurn], params: GenerationParams) -> AsyncIterator[str]:
        """Yield the assistant reply as text fragments."""

    @abstractmethod
    async def check(self, model: str) -> tuple[bool, str]:
        """Return (ok, human-readable status) for the given model."""

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


def last_user_images(turns: list[ChatTurn]) -> tuple[ImageData, ...]:
    return next((t.images for t in reversed(turns) if t.role == "user"), ())
