"""Claude through the subscription bridge: the NDJSON protocol, readiness and switching."""

import json

import httpx
import pytest

from app.config import Settings
from app.llm import ChatTurn, GenerationParams, ImageData, LLMError, ProviderRegistry
from app.llm.base import TRUNCATED_NOTE
from app.llm.claude_cli import ClaudeCliProvider
from app.main import create_app

from .test_api import RecordingProvider, parse_sse

PARAMS = GenerationParams(model="claude-sonnet-5-5", system="Be brief.", temperature=0.7, max_tokens=500)


class FakeBridge:
    """Answers like services/bridge would."""

    def __init__(self, events=None, signed_in=True, status=200):
        self.events = events or [{"type": "text", "text": "Hi"}, {"type": "text", "text": " there"}, {"type": "done", "truncated": False}]
        self.signed_in = signed_in
        self.status = status
        self.bodies: list[dict] = []
        self.auth: list[str | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.auth.append(request.headers.get("authorization"))
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "Wrong or missing CLAUDE_BRIDGE_TOKEN"})
        if request.url.path == "/health":
            return httpx.Response(200, json={
                "signed_in": self.signed_in, "plan": "pro", "version": "2.1.326",
                "detail": "" if self.signed_in else "Claude Code isn't signed in on this computer. Run claude.",
            })
        self.bodies.append(json.loads(request.content))
        return httpx.Response(200, text="".join(json.dumps(e) + "\n" for e in self.events))


def provider(bridge: FakeBridge, url: str = "http://bridge:8005") -> ClaudeCliProvider:
    return ClaudeCliProvider(url, "claude-sonnet-5-5", token="secret", transport=httpx.MockTransport(bridge))


async def test_streams_text_and_sends_history_and_latest_images():
    bridge = FakeBridge()
    turns = [
        ChatTurn("user", "old", (ImageData("image/png", b"old"),)),
        ChatTurn("assistant", "reply"),
        ChatTurn("user", "new", (ImageData("image/png", b"png"),)),
    ]
    text = "".join([t async for t in provider(bridge).stream(turns, PARAMS)])
    assert text == "Hi there"
    body = bridge.bodies[0]
    assert body["model"] == "claude-sonnet-5-5" and body["system"] == "Be brief." and body["max_tokens"] == 500
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert [len(m["images"]) for m in body["messages"]] == [0, 0, 1]
    assert bridge.auth[0] == "Bearer secret"


async def test_truncated_and_errors():
    cut = FakeBridge([{"type": "text", "text": "a"}, {"type": "done", "truncated": True}])
    assert [t async for t in provider(cut).stream([ChatTurn("user", "x")], PARAMS)] == ["a", TRUNCATED_NOTE]
    limit = FakeBridge([{"type": "error", "code": "usage_limit", "message": "You've reached your Claude plan's usage limit for now."}])
    with pytest.raises(LLMError, match="usage limit"):
        [t async for t in provider(limit).stream([ChatTurn("user", "x")], PARAMS)]
    with pytest.raises(LLMError, match="stopped before"):
        [t async for t in provider(FakeBridge([{"type": "text", "text": "a"}])).stream([ChatTurn("user", "x")], PARAMS)]


async def test_structured_output_for_agents():
    bridge = FakeBridge([{"type": "done", "truncated": False, "structured_output": {"tool": "finish", "answer": "ok"}}])
    schema = {"type": "object", "properties": {"tool": {"type": "string"}}}
    assert await provider(bridge).complete_json([ChatTurn("user", "plan")], PARAMS, schema) == {"tool": "finish", "answer": "ok"}
    assert bridge.bodies[0]["json_schema"] == schema


async def test_check_explains_what_is_missing():
    assert (await provider(FakeBridge()).check("claude-sonnet-5-5")) == (
        True, "claude-sonnet-5-5 through Claude Pro plan (Claude Code 2.1.326)"
    )
    ok, detail = await provider(FakeBridge(signed_in=False)).check("m")
    assert not ok and "signed in" in detail
    ok, detail = await provider(FakeBridge(status=401)).check("m")
    assert not ok and "CLAUDE_BRIDGE_TOKEN" in detail
    ok, detail = await provider(FakeBridge(), url="").check("m")
    assert not ok and "CLAUDE_BRIDGE_URL" in detail

    def down(request):
        raise httpx.ConnectError("refused")

    off = ClaudeCliProvider("http://bridge:8005", "m", transport=httpx.MockTransport(down))
    ok, detail = await off.check("m")
    assert not ok and "bridge.ps1" in detail
    with pytest.raises(LLMError, match="isn't running"):
        [t async for t in off.stream([ChatTurn("user", "x")], PARAMS)]


def make_app(tmp_path, bridge: FakeBridge):
    settings = Settings(database_path=str(tmp_path / "c.db"), llm_provider="ollama")
    registry = ProviderRegistry({"ollama": RecordingProvider(), "claude_cli": provider(bridge)})
    return create_app(settings, registry)


async def test_switch_only_when_ready_then_chat(tmp_path):
    bridge = FakeBridge(signed_in=False)
    app = make_app(tmp_path, bridge)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            models = (await c.get("/api/models", params={"provider": "claude_cli"})).json()
            assert models["ready"] is False and "signed in" in models["detail"]
            assert "claude-sonnet-5-5" in models["models"]
            refused = await c.patch("/api/config", json={"provider": "claude_cli", "model": "claude-sonnet-5-5"})
            assert refused.status_code == 409 and "signed in" in refused.json()["detail"]

            bridge.signed_in = True
            switched = await c.patch("/api/config", json={"provider": "claude_cli", "model": "claude-haiku-5-5"})
            assert switched.status_code == 200 and switched.json()["provider"] == "claude_cli"
            sid = (await c.post("/api/sessions", json={})).json()["id"]
            resp = await c.post(f"/api/sessions/{sid}/messages/stream", json={"content": "hello"})
            events = parse_sse(resp.text)
            assert "".join(d["text"] for e, d in events if e == "token") == "Hi there"
            assert events[-1][0] == "done"
            assert bridge.bodies[-1]["model"] == "claude-haiku-5-5"
