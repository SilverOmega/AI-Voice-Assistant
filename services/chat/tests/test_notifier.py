import asyncio
from urllib.parse import parse_qs

import httpx
import pytest

from app.config import Settings
from app.llm import LLMProvider, ProviderRegistry
from app.main import create_app
from app.notifications import NotificationCenter
from app.notifier import Notifier, masked_phone

NOW = "2026-11-02T09:30"
PHONE = "+84901234567"
MASKED = "+84" + "•" * 6 + "567"


class DraftingProvider(LLMProvider):
    name = "ollama"
    default_model = "fake-model"
    draft = {"to": ["anna@example.com"], "cc": [], "subject": "Running late", "body": "Sorry!"}

    async def stream(self, turns, params):
        yield ""

    async def complete_json(self, turns, params, schema):
        return self.draft

    async def check(self, model):
        return True, "ok"


def phone_settings(tmp_path, **extra) -> Settings:
    return Settings(
        database_path=str(tmp_path / "test.db"),
        llm_provider="ollama",
        notify_phone=PHONE,
        twilio_account_sid="AC123",
        twilio_auth_token="secret",
        twilio_from="+15550001111",
        ntfy_topic="my-review-topic",
        app_url="http://192.168.1.10:8088",
        **extra,
    )


@pytest.fixture
async def env(tmp_path):
    """(client, app, outgoing notification requests)"""
    app = create_app(phone_settings(tmp_path), ProviderRegistry({"ollama": DraftingProvider()}))
    sent: list[httpx.Request] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(201, json={"sid": "SM1"})

    async with app.router.lifespan_context(app):
        notifier = app.state.notifier
        await notifier.aclose()
        notifier._http = httpx.AsyncClient(transport=httpx.MockTransport(gateway))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, app, sent


async def test_draft_sends_review_reminder_to_phone(env):
    client, app, sent = env
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    resp = await client.post(
        f"/api/sessions/{sid}/actions",
        json={"kind": "email", "instruction": "tell anna I'm late", "now": NOW},
    )
    assert resp.status_code == 201
    await app.state.notifications.idle()

    sms, push = sorted(sent, key=lambda r: r.url.host)  # api.twilio.com, ntfy.sh
    assert str(sms.url) == "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    form = parse_qs(sms.content.decode())
    assert form["To"] == [PHONE] and form["From"] == ["+15550001111"]
    text = form["Body"][0]
    assert text.startswith("Review before it's sent: Email to anna@example.com")
    assert '"Running late"' in text and "Nothing is sent until you approve it" in text
    assert text.endswith("http://192.168.1.10:8088")
    assert sms.headers["authorization"].startswith("Basic ")

    assert push.url.path == "/my-review-topic"
    assert push.url.params["title"] == "Review before it's sent"
    assert push.url.params["click"] == "http://192.168.1.10:8088"

    # It's in the web app's list too, linked to the chat.
    listed = (await client.get("/api/notifications")).json()
    assert [(n["category"], n["session_id"]) for n in listed["items"]] == [("review", sid)]

    # A reminder never approves anything.
    pending = (await client.get("/api/actions", params={"status": "pending"})).json()
    assert len(pending) == 1


async def test_test_notification_and_channels(env):
    client, _, sent = env
    info = (await client.get("/api/automation")).json()
    assert info["notify_channels"] == ["SMS to " + MASKED, "ntfy push"]
    resp = await client.post("/api/notify/test")
    assert resp.json() == {"channels": info["notify_channels"], "errors": []}
    assert len(sent) == 2


async def test_gateway_errors_are_reported_not_raised(tmp_path):
    def failing(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.twilio.com":
            return httpx.Response(400, json={"message": "The 'To' number is not verified"})
        return httpx.Response(200)

    notifier = Notifier(phone_settings(tmp_path), httpx.AsyncClient(transport=httpx.MockTransport(failing)))
    errors = await notifier.send("t", "b")
    assert errors == ["SMS failed (HTTP 400): The 'To' number is not verified"]
    await notifier.aclose()


async def test_rejected_phone_credentials_show_in_the_browser_only(tmp_path):
    from app.repository import Repository

    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Authenticate"})

    repo = Repository(":memory:")
    await repo.connect()
    notifier = Notifier(phone_settings(tmp_path), httpx.AsyncClient(transport=httpx.MockTransport(unauthorized)))
    center = NotificationCenter(repo, notifier)
    await center.emit("sign_in", "New device", "Chrome on Android")
    await center.idle()
    items, _ = await repo.list_notifications()
    assert [n.title for n in items] == ["Phone notifications are failing", "New device"]
    assert "SMS failed (HTTP 401): Authenticate" in items[0].body
    await notifier.aclose()
    await repo.close()


async def test_reminds_once_about_drafts_still_waiting(tmp_path):
    from app.repository import Repository

    repo = Repository(":memory:")
    await repo.connect()
    session = await repo.create_session()
    await repo.add_action(session.id, "email", "x", DraftingProvider.draft)
    center = NotificationCenter(repo, Notifier(phone_settings(tmp_path)))
    calls: list = []

    async def fake_emit(category, title, body, **kw):
        calls.append(title)

    center.emit = fake_emit
    # A minute later (cutoff in the future), the draft is old enough for one reminder.
    import app.notifications as mod

    real_timedelta = mod.timedelta
    mod.timedelta = lambda minutes: real_timedelta(minutes=-minutes)
    try:
        task = asyncio.create_task(center.remind_loop(1, interval=0.01))
        await asyncio.sleep(0.1)
        task.cancel()
    finally:
        mod.timedelta = real_timedelta
    assert calls == ["Reminder: review before it's sent"]
    await repo.close()
    await center.notifier.aclose()


def test_phone_must_be_international_format(tmp_path):
    with pytest.raises(ValueError):
        Settings(database_path=str(tmp_path / "x.db"), notify_phone="0901234567")
    assert masked_phone(PHONE) == MASKED
