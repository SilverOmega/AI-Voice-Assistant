"""Claude through your Claude subscription: Claude Code on this computer, via the bridge.

The bridge (services/bridge) runs on the computer where Claude Code is installed and signed
in, and turns each request into one locked-down `claude -p` run (no tools, no files). This
provider speaks its small NDJSON protocol. Agents work through the JSON fallback, which the
bridge answers with Claude Code's structured output.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .anthropic_provider import CLAUDE_MODELS
from .base import TRUNCATED_NOTE, ChatTurn, GenerationParams, LLMError, LLMProvider, normalize_turns

NOT_SET_UP = "the Claude bridge isn't set up: set CLAUDE_BRIDGE_URL in .env"
NOT_RUNNING = (
    "the Claude bridge isn't running. Start it on this computer with scripts\\bridge.ps1 "
    "(or scripts/bridge.sh)"
)
WRONG_TOKEN = "the Claude bridge refused the connection: CLAUDE_BRIDGE_TOKEN doesn't match"


class ClaudeCliProvider(LLMProvider):
    name = "claude_cli"

    def __init__(
        self,
        bridge_url: str,
        model: str,
        token: str = "",
        timeout: float = 330.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.default_model = model
        self._url = bridge_url.rstrip("/")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.AsyncClient(
            base_url=self._url or "http://bridge.invalid",
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=5.0),
            transport=transport,
        )

    @staticmethod
    def _body(turns: list[ChatTurn], params: GenerationParams, schema: dict | None = None) -> dict[str, Any]:
        turns = normalize_turns(turns)
        messages = [{"role": t.role, "content": t.content, "images": []} for t in turns]
        if turns:
            # Only the latest message's pictures; earlier ones would resend megabytes each turn.
            messages[-1]["images"] = [{"media_type": i.media_type, "data": i.base64()} for i in turns[-1].images]
        return {
            "model": params.model,
            "system": params.system,
            "max_tokens": params.max_tokens,
            "json_schema": schema,
            "messages": messages,
        }

    async def _events(self, body: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        if not self._url:
            raise LLMError(f"Claude (subscription) isn't ready: {NOT_SET_UP}.")
        try:
            async with self._client.stream("POST", "/v1/messages", json=body) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    raise LLMError(f"Claude (subscription) isn't ready: {_http_problem(resp)}.")
                async for line in resp.aiter_lines():
                    if line.strip():
                        try:
                            yield json.loads(line)
                        except ValueError:
                            continue
        except httpx.ConnectError as exc:
            raise LLMError(f"Claude (subscription) isn't ready: {NOT_RUNNING}.") from exc
        except httpx.TimeoutException as exc:
            raise LLMError("Claude took too long to answer. Try again.") from exc
        except httpx.HTTPError as exc:
            raise LLMError("Lost the connection to the Claude bridge. Try again.") from exc

    async def stream(self, turns: list[ChatTurn], params: GenerationParams) -> AsyncIterator[str]:
        async for event in self._events(self._body(turns, params)):
            kind = event.get("type")
            if kind == "text" and event.get("text"):
                yield event["text"]
            elif kind == "error":
                raise LLMError(event.get("message") or "Claude couldn't answer.")
            elif kind == "done":
                if event.get("truncated"):
                    yield TRUNCATED_NOTE
                return
        raise LLMError("The Claude bridge stopped before the reply was finished. Try again.")

    async def complete_json(
        self, turns: list[ChatTurn], params: GenerationParams, schema: dict[str, Any]
    ) -> dict[str, Any]:
        async for event in self._events(self._body(turns, params, schema)):
            if event.get("type") == "error":
                raise LLMError(event.get("message") or "Claude couldn't answer.")
            if event.get("type") == "done":
                out = event.get("structured_output")
                if isinstance(out, dict):
                    return out
                raise LLMError("Claude didn't give the structured answer that was asked for.")
        raise LLMError("The Claude bridge stopped before the answer was finished. Try again.")

    async def list_models(self) -> list[str]:
        return list(dict.fromkeys([self.default_model, *CLAUDE_MODELS]))

    async def check(self, model: str) -> tuple[bool, str]:
        if not self._url:
            return False, NOT_SET_UP
        try:
            resp = await self._client.get("/health", timeout=40.0)
        except httpx.HTTPError:
            return False, NOT_RUNNING
        if resp.status_code != 200:
            return False, _http_problem(resp)
        info = resp.json()
        if not info.get("signed_in"):
            detail = str(info.get("detail") or "Claude Code isn't signed in on this computer")
            return False, detail[:1].lower() + detail[1:].rstrip(".")
        plan = str(info.get("plan") or "").capitalize()
        via = f"Claude {plan} plan" if plan else "your Claude account"
        return True, f"{model} through {via} (Claude Code {info.get('version') or ''})".replace(" )", ")")

    async def aclose(self) -> None:
        await self._client.aclose()


def _http_problem(resp: httpx.Response) -> str:
    if resp.status_code == 401:
        return WRONG_TOKEN
    try:
        detail = resp.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, str) and detail:
        return detail[:1].lower() + detail[1:].rstrip(".")
    return f"the Claude bridge answered with an error ({resp.status_code})"
