import asyncio

import httpx
import pytest

from app.config import Settings
from app.llm import TRUNCATED_NOTE, LLMAuthError, LLMProvider, ProviderRegistry
from app.main import create_app
from app.notifications import device_label

ANDROID_CHROME = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Mobile Safari/537.36"
)


class ScriptedProvider(LLMProvider):
    name = "ollama"
    default_model = "fake-model"

    def __init__(self):
        self.reply: list[str] = ["Hello"]
        self.error: Exception | None = None

    async def stream(self, turns, params):
        if self.error:
            raise self.error
        for part in self.reply:
            yield part

    async def complete_json(self, turns, params, schema):
        if self.error:
            raise self.error
        return {"title": "Notes", "content": "# Notes"}

    async def check(self, model):
        return True, "ok"


@pytest.fixture
def provider():
    return ScriptedProvider()


@pytest.fixture
async def env(provider, tmp_path):
    """(client, app). No phone channel is set up, so nothing leaves the machine."""
    settings = Settings(database_path=str(tmp_path / "test.db"), llm_provider="ollama")
    app = create_app(settings, ProviderRegistry({"ollama": provider}))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, app


async def notifications(client) -> dict:
    return (await client.get("/api/notifications")).json()


async def chat(client, text="hi") -> str:
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    resp = await client.post(f"/api/sessions/{sid}/messages/stream", json={"content": text})
    assert resp.status_code == 200
    return sid


async def test_new_device_is_a_sign_in(env):
    client, _ = env
    hello = {"device_id": "a1b2c3d4-phone"}
    headers = {"user-agent": ANDROID_CHROME, "x-real-ip": "192.168.1.23"}
    first = await client.post("/api/devices/hello", json=hello, headers=headers)
    again = await client.post("/api/devices/hello", json=hello, headers=headers)
    assert (first.json(), again.json()) == ({"new": True}, {"new": False})

    listed = await notifications(client)
    assert listed["unread"] == 1
    (note,) = listed["items"]
    assert (note["category"], note["level"], note["title"]) == ("sign_in", "warning", "New device opened your assistant")
    assert note["body"].startswith("Chrome on Android at 192.168.1.23.")
    # Behind Docker Desktop every visitor looks like the gateway: no address is better than a wrong one.
    await client.post("/api/devices/hello", json={"device_id": "e5f6a7b8-laptop"}, headers={**headers, "x-real-ip": "172.20.0.1"})
    assert (await notifications(client))["items"][0]["body"].startswith("Chrome on Android. If this wasn't you")
    bad = await client.post("/api/devices/hello", json={"device_id": "<script>"})
    assert bad.status_code == 422


def test_device_labels():
    assert device_label(ANDROID_CHROME) == "Chrome on Android"
    assert device_label("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Version/18.0 Mobile/15E148 Safari/604.1") == "Safari on iPhone"
    assert device_label("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36 Edg/130.0") == "Edge on Windows"
    assert device_label("") == "A browser on an unknown device"


async def test_photo_limit_report(env):
    client, _ = env
    resp = await client.post("/api/notifications/report", json={"category": "photo_limit", "count": 6})
    assert resp.status_code == 202
    (note,) = (await notifications(client))["items"]
    assert note["title"] == "Photo limit reached"
    assert "up to 4 pictures, and you tried to add 6" in note["body"]
    # Only known browser events can be reported, and the server writes the text.
    bad = await client.post("/api/notifications/report", json={"category": "sign_in", "count": 1})
    assert bad.status_code == 422


async def test_reply_cut_off_at_token_limit(env, provider):
    client, _ = env
    provider.reply = ["Part one", TRUNCATED_NOTE]
    sid = await chat(client)
    (note,) = (await notifications(client))["items"]
    assert (note["category"], note["session_id"]) == ("token_limit", sid)
    assert "stopped at 4096 tokens" in note["body"]


async def test_rejected_api_key_notifies_once(env, provider):
    client, _ = env
    provider.error = LLMAuthError("Claude rejected the credentials. Set ANTHROPIC_API_KEY.")
    await chat(client)
    await chat(client)  # the same problem again soon after: not repeated
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    drafted = await client.post(
        f"/api/sessions/{sid}/actions", json={"kind": "document", "instruction": "x", "now": "2026-11-02T09:30"}
    )
    assert drafted.status_code == 502
    items = (await notifications(client))["items"]
    assert [(n["category"], n["title"]) for n in items] == [("credentials", "API key rejected")]


async def test_task_result_is_notified(env):
    client, _ = env
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    action = (
        await client.post(
            f"/api/sessions/{sid}/actions", json={"kind": "document", "instruction": "x", "now": "2026-11-02T09:30"}
        )
    ).json()
    await client.post(f"/api/actions/{action['id']}/approve", json={"payload": action["payload"]})
    items = (await notifications(client))["items"]
    assert [(n["category"], n["title"]) for n in items] == [("task", "Document done"), ("review", "Review before it's saved")]


async def test_read_clear_and_preferences(env):
    client, app = env
    center = app.state.notifications
    a = await center.emit("photo_limit", "a", "x", dedupe=False)
    await center.emit("photo_limit", "b", "y", dedupe=False)
    await client.post("/api/notifications/read", json={"ids": [a.id]})
    assert (await notifications(client))["unread"] == 1
    await client.post("/api/notifications/read", json={})
    assert (await notifications(client))["unread"] == 0

    settings = (await client.get("/api/notifications/settings")).json()
    assert settings["phone_channels"] == []
    defaults = {c["id"]: (c["web"], c["phone"]) for c in settings["categories"]}
    assert defaults["sign_in"] == (True, True) and defaults["photo_limit"] == (True, False)

    updated = await client.put("/api/notifications/settings", json={"photo_limit": {"web": False, "phone": True}})
    assert {c["id"]: (c["web"], c["phone"]) for c in updated.json()["categories"]}["photo_limit"] == (False, True)
    bad = await client.put("/api/notifications/settings", json={"nope": {"web": True, "phone": True}})
    assert bad.status_code == 422

    # Live subscribers get each notification with whether to pop it up.
    queue = center.subscribe()
    await center.emit("photo_limit", "c", "z")
    note, popup = await asyncio.wait_for(queue.get(), 1)
    assert (note.title, popup) == ("c", False)
    center.unsubscribe(queue)

    assert (await client.delete("/api/notifications")).status_code == 204
    assert await notifications(client) == {"items": [], "unread": 0}
