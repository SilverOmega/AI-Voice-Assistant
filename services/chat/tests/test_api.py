import json
from collections.abc import AsyncIterator

import httpx
import pytest

from app.config import Settings
from app.llm import ChatTurn, GenerationParams, LLMError, LLMProvider, ProviderRegistry
from app.llm.base import normalize_turns
from app.llm.echo import EchoProvider
from app.main import create_app


class RecordingProvider(LLMProvider):
    name = "ollama"
    default_model = "fake-model"

    def __init__(self, reply: str = "Hello there.", fail_after: int | None = None):
        self.reply = reply
        self.fail_after = fail_after
        self.calls: list[tuple[list[ChatTurn], GenerationParams]] = []

    async def stream(self, turns, params) -> AsyncIterator[str]:
        self.calls.append((turns, params))
        for i, word in enumerate(self.reply.split(" ")):
            if self.fail_after is not None and i >= self.fail_after:
                raise LLMError("model crashed")
            yield word if i == 0 else " " + word

    async def check(self, model):
        return True, "ok"


@pytest.fixture
def provider():
    return RecordingProvider()


@pytest.fixture
async def client(provider, tmp_path):
    settings = Settings(database_path=str(tmp_path / "test.db"), llm_provider="ollama")
    registry = ProviderRegistry({"ollama": provider, "echo": EchoProvider(delay=0)})
    app = create_app(settings, registry)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


async def new_session(client) -> str:
    resp = await client.post("/api/sessions", json={})
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_health_and_config(client):
    health = (await client.get("/health")).json()
    assert health["status"] == "ok"
    assert health["model"] == "fake-model"

    cfg = (await client.patch("/api/config", json={"provider": "echo"})).json()
    assert cfg["provider"] == "echo"
    assert cfg["model"] == "echo"
    assert set(cfg["available_providers"]) == {"ollama", "echo"}

    bad = await client.patch("/api/config", json={"temperature": 5})
    assert bad.status_code == 422


async def test_session_crud(client):
    sid = await new_session(client)
    assert (await client.get("/api/sessions")).json()[0]["id"] == sid

    renamed = await client.patch(f"/api/sessions/{sid}", json={"title": "Trip planning"})
    assert renamed.json()["title"] == "Trip planning"

    assert (await client.delete(f"/api/sessions/{sid}")).status_code == 204
    assert (await client.get(f"/api/sessions/{sid}")).status_code == 404


async def test_streaming_turn_persists_messages_and_titles_session(client, provider):
    sid = await new_session(client)
    resp = await client.post(
        f"/api/sessions/{sid}/messages/stream", json={"content": "What's the weather like?"}
    )
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "start" and kinds[-1] == "done"
    assert "".join(d["text"] for e, d in events if e == "token") == "Hello there."

    detail = (await client.get(f"/api/sessions/{sid}")).json()
    assert detail["title"] == "What's the weather like?"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["content"] == "Hello there."

    # second turn receives the full history
    await client.post(f"/api/sessions/{sid}/messages", json={"content": "And tomorrow?"})
    turns, params = provider.calls[-1]
    assert [t.role for t in turns] == ["user", "assistant", "user"]
    assert params.model == "fake-model"


async def test_provider_error_keeps_partial_reply(client, provider):
    provider.fail_after = 1
    sid = await new_session(client)
    resp = await client.post(f"/api/sessions/{sid}/messages/stream", json={"content": "hi"})
    events = parse_sse(resp.text)
    assert events[-1] == ("error", {"message": "model crashed"})

    messages = (await client.get(f"/api/sessions/{sid}")).json()["messages"]
    assert [m["content"] for m in messages] == ["hi", "Hello"]


async def test_non_streaming_error_maps_to_502(client, provider):
    provider.fail_after = 0
    sid = await new_session(client)
    resp = await client.post(f"/api/sessions/{sid}/messages", json={"content": "hi"})
    assert resp.status_code == 502


async def test_unknown_session_and_validation(client):
    assert (await client.post("/api/sessions/nope/messages", json={"content": "x"})).status_code == 404
    sid = await new_session(client)
    assert (await client.post(f"/api/sessions/{sid}/messages", json={"content": ""})).status_code == 422


async def test_voice_source_is_recorded(client):
    sid = await new_session(client)
    await client.post(f"/api/sessions/{sid}/messages", json={"content": "hey", "source": "voice"})
    messages = (await client.get(f"/api/sessions/{sid}")).json()["messages"]
    assert {m["source"] for m in messages} == {"voice"}


def test_normalize_turns():
    turns = [
        ChatTurn("assistant", "orphan"),
        ChatTurn("user", "a"),
        ChatTurn("user", "b"),
        ChatTurn("assistant", "  "),
        ChatTurn("assistant", "c"),
    ]
    assert normalize_turns(turns) == [ChatTurn("user", "a\n\nb"), ChatTurn("assistant", "c")]


async def test_abandoned_stream_releases_lock_and_stops_provider(tmp_path):
    class SlowProvider(RecordingProvider):
        closed = False

        async def stream(self, turns, params):
            try:
                for word in ["one", " two", " three", " four"]:
                    yield word
            finally:
                SlowProvider.closed = True

    provider = SlowProvider()
    settings = Settings(database_path=str(tmp_path / "t.db"), llm_provider="ollama")
    app = create_app(settings, ProviderRegistry({"ollama": provider}))
    async with app.router.lifespan_context(app):
        svc = app.state.chat
        session = await svc.repo.create_session()
        events = await svc.stream_reply(session.id, "hi")
        assert (await anext(events)).event == "start"
        assert (await anext(events)).event == "token"
        await events.aclose()  # what the HTTP layer does when the client disconnects

        assert SlowProvider.closed
        assert not svc._lock_for(session.id).locked()
        messages = await svc.repo.list_messages(session.id)
        assert [(m.role, m.content) for m in messages] == [("user", "hi"), ("assistant", "one")]


async def test_cancelled_turn_releases_lock_under_anyio(tmp_path):
    """Starlette cancels a disconnected stream with an anyio cancel scope (level-triggered)."""
    import asyncio

    import anyio

    class SlowProvider(RecordingProvider):
        async def stream(self, turns, params):
            for word in ["one", " two", " three", " four"]:
                yield word
                await asyncio.sleep(0.05)

    settings = Settings(database_path=str(tmp_path / "t.db"), llm_provider="ollama")
    app = create_app(settings, ProviderRegistry({"ollama": SlowProvider()}))
    async with app.router.lifespan_context(app):
        svc = app.state.chat
        session = await svc.repo.create_session()
        events = await svc.stream_reply(session.id, "hi")
        with anyio.move_on_after(0.08):
            async for _ in events:
                pass
        await events.aclose()
        await asyncio.sleep(0.05)  # let the shielded save finish

        assert not svc._lock_for(session.id).locked()
        messages = await svc.repo.list_messages(session.id)
        assert messages[-1].role == "assistant" and messages[-1].content.startswith("one two")


async def test_model_is_warmed_up_at_startup_and_on_model_switch(tmp_path):
    import asyncio

    class WarmingProvider(RecordingProvider):
        def __init__(self):
            super().__init__()
            self.warmed: list[str] = []

        async def warm_up(self, model):
            self.warmed.append(model)

    provider = WarmingProvider()
    settings = Settings(database_path=str(tmp_path / "t.db"), llm_provider="ollama")
    app = create_app(settings, ProviderRegistry({"ollama": provider}))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            await asyncio.sleep(0)
            assert provider.warmed == ["fake-model"]

            await c.patch("/api/config", json={"temperature": 0.2})
            await asyncio.sleep(0)
            assert provider.warmed == ["fake-model"]  # same model: nothing to load

            await c.patch("/api/config", json={"model": "other-model"})
            await asyncio.sleep(0)
            assert provider.warmed == ["fake-model", "other-model"]


async def test_ollama_requests_carry_keep_alive():
    from app.llm.ollama import OllamaProvider

    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if request.url.path == "/api/chat":
            done = {"message": {"content": "hi"}, "done": True}
            return httpx.Response(200, text=json.dumps(done) + "\n")
        return httpx.Response(200, json={"done": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaProvider("http://ollama", "m", keep_alive="1h", client=client)
    await provider.warm_up("m")
    params = GenerationParams(model="m", system="", temperature=0.5, max_tokens=10)
    assert [t async for t in provider.stream([ChatTurn("user", "hey")], params)] == ["hi"]

    assert bodies[0] == {"model": "m", "keep_alive": "1h"}
    assert bodies[1]["keep_alive"] == "1h"
    await provider.aclose()
