"""Offline provider that echoes the user. Used for tests and for UI work without a model."""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

from .base import ChatTurn, GenerationParams, LLMProvider


class EchoProvider(LLMProvider):
    name = "echo"
    default_model = "echo"

    def __init__(self, delay: float = 0.02):
        self._delay = delay

    async def stream(self, turns: list[ChatTurn], params: GenerationParams) -> AsyncIterator[str]:
        last_user = next((t for t in reversed(turns) if t.role == "user"), None)
        said = last_user.content.rstrip(".!?") if last_user else ""
        count = len(last_user.images) if last_user else 0
        sent = f"You sent {count} image{'s' if count != 1 else ''}. " if count else ""
        reply = f"{sent}You said: {said}. (Echo mode — configure a real model in settings.)"
        for i, word in enumerate(reply.split(" ")):
            if self._delay:
                await asyncio.sleep(self._delay)
            yield word if i == 0 else " " + word

    async def complete_json(self, turns, params, schema):
        """Fill every field from the request so drafts can be tried without a model."""
        said = turns[-1].content if turns else ""
        soon = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:00")
        out: dict = {}
        for name, prop in schema.get("properties", {}).items():
            if prop.get("type") == "array":
                out[name] = []
            elif "enum" in prop:
                out[name] = prop["enum"][0]
            elif name in ("start", "end"):
                out[name] = soon
            else:
                out[name] = said
        return out

    async def check(self, model: str) -> tuple[bool, str]:
        return True, "echo provider (no model)"
