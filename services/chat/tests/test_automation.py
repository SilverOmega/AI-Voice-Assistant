import asyncio

import httpx
import pytest

from app.config import Settings
from app.llm import LLMError, LLMProvider, ProviderRegistry
from app.llm.base import parse_json_object
from app.main import create_app

NOW = "2026-11-02T09:30"


class DraftingProvider(LLMProvider):
    """Returns a fixed draft and records what it was asked."""

    name = "ollama"
    default_model = "fake-model"

    def __init__(self):
        self.draft: dict = {}
        self.calls: list = []

    async def stream(self, turns, params):
        yield "unused"

    async def complete_json(self, turns, params, schema):
        self.calls.append((turns, params, schema))
        return self.draft

    async def check(self, model):
        return True, "ok"


@pytest.fixture
def provider():
    return DraftingProvider()


@pytest.fixture
async def env(provider, tmp_path):
    """(client, app, webhook calls, sent emails)"""
    settings = Settings(
        database_path=str(tmp_path / "test.db"),
        llm_provider="ollama",
        workflows={"backup": "http://hooks.test/backup"},
    )
    app = create_app(settings, ProviderRegistry({"ollama": provider}))
    hooks: list[httpx.Request] = []

    def webhook(request: httpx.Request) -> httpx.Response:
        hooks.append(request)
        return httpx.Response(200, json={"ok": True})

    async with app.router.lifespan_context(app):
        auto = app.state.automation
        await auto.aclose()
        auto._http = httpx.AsyncClient(transport=httpx.MockTransport(webhook))
        sent: list = []
        auto.send_smtp = sent.append
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, app, hooks, sent


async def draft(client, kind: str, instruction: str = "do it") -> dict:
    sid = (await client.post("/api/sessions", json={})).json()["id"]
    resp = await client.post(
        f"/api/sessions/{sid}/actions", json={"kind": kind, "instruction": instruction, "now": NOW}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_email_draft_waits_for_approval_then_sends(env, provider):
    client, app, _, sent = env
    app.state.automation.settings.smtp_host = "smtp.test"
    provider.draft = {"to": ["bob@example.com"], "cc": [], "subject": "Lunch", "body": "Noon?"}

    action = await draft(client, "email", "email bob about lunch")
    assert action["status"] == "pending"
    assert action["payload"]["subject"] == "Lunch"
    assert sent == []  # drafting never sends
    _, params, schema = provider.calls[0]
    assert "Monday, 2026-11-02 09:30" in params.system
    assert set(schema["required"]) == {"to", "cc", "subject", "body"}

    pending = (await client.get("/api/actions", params={"status": "pending"})).json()
    assert [a["id"] for a in pending] == [action["id"]]

    edited = {**action["payload"], "subject": "Lunch tomorrow"}
    resp = await client.post(f"/api/actions/{action['id']}/approve", json={"payload": edited})
    assert resp.status_code == 200
    done = resp.json()
    assert done["status"] == "done"
    assert done["result"]["message"] == "Sent to bob@example.com."
    assert len(sent) == 1
    assert sent[0]["Subject"] == "Lunch tomorrow"  # the user's edit is what gets sent
    assert sent[0]["To"] == "bob@example.com"

    again = await client.post(f"/api/actions/{action['id']}/approve", json={"payload": edited})
    assert again.status_code == 409
    assert len(sent) == 1


async def test_concurrent_approvals_run_once(env, provider):
    client, _, hooks, _ = env
    provider.draft = {"workflow": "backup", "input": "nightly"}
    action = await draft(client, "workflow")
    url = f"/api/actions/{action['id']}/approve"
    results = await asyncio.gather(
        *[client.post(url, json={"payload": action["payload"]}) for _ in range(3)]
    )
    assert sorted(r.status_code for r in results) == [200, 409, 409]
    assert len(hooks) == 1
    assert str(hooks[0].url) == "http://hooks.test/backup"


async def test_email_without_smtp_opens_gmail_compose(env, provider):
    client, _, _, sent = env
    provider.draft = {"to": ["a@x.io"], "cc": ["c@x.io"], "subject": "Hi & bye", "body": "Line 1\nLine 2"}
    action = await draft(client, "email")
    info = (await client.get("/api/automation")).json()
    assert info["email_delivery"] == "gmail" and info["workflows"] == ["backup"]
    assert info["notify_channels"] == []  # phone notifications are off unless configured

    done = (
        await client.post(f"/api/actions/{action['id']}/approve", json={"payload": action["payload"]})
    ).json()
    assert done["status"] == "done"
    assert done["result"]["message"] == "Opened in Gmail for a@x.io, c@x.io. Check it there and press Send."
    urls = done["result"]["compose_urls"]
    assert done["result"]["open_url"] == urls["gmail"] == (
        "https://mail.google.com/mail/?view=cm&fs=1&to=a%40x.io&su=Hi%20%26%20bye"
        "&body=Line%201%0ALine%202&cc=c%40x.io"
    )
    assert urls["outlook"].startswith("https://outlook.live.com/mail/0/deeplink/compose?to=a%40x.io&subject=Hi")
    assert urls["mail_app"] == "mailto:a@x.io?cc=c%40x.io&subject=Hi%20%26%20bye&body=Line%201%0ALine%202"
    assert sent == []


async def test_email_compose_target_is_configurable(env, provider):
    client, app, _, _ = env
    app.state.automation.settings.email_compose = "mail_app"
    provider.draft = {"to": ["a@x.io"], "cc": [], "subject": "s", "body": "b"}
    action = await draft(client, "email")
    done = (
        await client.post(f"/api/actions/{action['id']}/approve", json={"payload": action["payload"]})
    ).json()
    assert done["result"]["open_url"] == "mailto:a@x.io?subject=s&body=b"
    assert done["result"]["message"].startswith("Opened in your mail app")


async def test_email_saved_to_drafts_when_imap_is_set_up(env, provider):
    client, app, _, sent = env
    auto = app.state.automation
    auto.settings.imap_username, auto.settings.imap_password = "me@gmail.com", "app-password"
    drafts: list = []
    auto.save_draft = drafts.append
    info = (await client.get("/api/automation")).json()
    assert info["email_delivery"] == "drafts"

    provider.draft = {"to": ["a@x.io"], "cc": [], "subject": "Hi", "body": "Hello"}
    action = await draft(client, "email")
    assert drafts == []  # nothing reaches the mailbox before approval
    done = (
        await client.post(f"/api/actions/{action['id']}/approve", json={"payload": action["payload"]})
    ).json()
    assert done["result"] == {
        "message": "Saved to Drafts in me@gmail.com, to a@x.io. Check it there and press Send.",
        "drafts_url": "https://mail.google.com/mail/u/0/#drafts",
    }
    assert len(drafts) == 1 and sent == []
    assert (drafts[0]["From"], drafts[0]["To"], drafts[0]["Subject"]) == ("me@gmail.com", "a@x.io", "Hi")


class FakeImap:
    def __init__(self, rows):
        self.rows = rows

    def list(self):
        return "OK", self.rows


def test_drafts_folder_is_found_by_its_flag():
    from app.automation import drafts_folder

    gmail_vi = [
        b'(\\HasNoChildren) "/" "INBOX"',
        b'(\\HasChildren \\Noselect) "/" "[Gmail]"',
        b'(\\Drafts \\HasNoChildren) "/" "[Gmail]/Th&AbA- nh&AOE-p"',
        b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"',
    ]
    assert drafts_folder(FakeImap(gmail_vi)) == '"[Gmail]/Th&AbA- nh&AOE-p"'
    assert drafts_folder(FakeImap([b'(\\HasNoChildren) "/" INBOX'])) == "Drafts"


def test_save_draft_appends_to_the_drafts_folder(monkeypatch, tmp_path):
    import imaplib
    from email.message import EmailMessage

    import app.automation as mod

    calls: list = []

    class Server(FakeImap):
        def __init__(self, host, port, ssl_context, timeout):
            super().__init__([b'(\\Drafts \\HasNoChildren) "/" "[Gmail]/Drafts"'])
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            calls.append(("logout",))

        def login(self, user, password):
            if password != "app-password":
                raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
            calls.append(("login", user))

        def append(self, folder, flags, when, message):
            calls.append(("append", folder, flags, message))
            return "OK", [b"[APPENDUID 1 2] (Success)"]

    monkeypatch.setattr(mod.imaplib, "IMAP4_SSL", Server)
    settings = Settings(database_path=str(tmp_path / "t.db"), imap_username="me@gmail.com", imap_password="app-password")
    auto = mod.Automation(None, settings)
    msg = EmailMessage()
    msg["Subject"] = "Hi"
    msg.set_content("Hello")
    auto.save_draft(msg)
    connect, login, append, logout = calls
    assert connect == ("connect", "imap.gmail.com", 993) and login == ("login", "me@gmail.com")
    assert append[1:3] == ('"[Gmail]/Drafts"', r"(\Draft \Seen)") and b"Subject: Hi" in append[3]

    settings.imap_password = "my-normal-password"
    with pytest.raises(mod.ActionInvalid, match="Invalid credentials.*app password"):
        auto.save_draft(msg)
    asyncio.run(auto.aclose())


@pytest.mark.parametrize(
    ("kind", "payload", "error"),
    [
        ("email", {"to": [], "subject": "s", "body": "b"}, "Add at least one recipient."),
        ("email", {"to": ["not-an-address"], "subject": "s"}, "Not a valid email address"),
        ("event", {"title": "", "start": NOW}, "Give the event a title."),
        ("event", {"title": "x", "start": "tomorrow"}, "'start' must be a date and time"),
        ("event", {"title": "x", "start": NOW, "end": "2026-11-02T09:00"}, "must end after"),
        ("document", {"title": "t", "content": " "}, "The document is empty."),
        ("workflow", {"workflow": "rm-rf", "input": ""}, "Unknown workflow 'rm-rf'."),
    ],
)
async def test_invalid_approval_is_rejected_and_stays_pending(env, provider, kind, payload, error):
    client, _, hooks, sent = env
    action = await draft(client, kind)
    resp = await client.post(f"/api/actions/{action['id']}/approve", json={"payload": payload})
    assert resp.status_code == 422
    assert error in resp.json()["detail"]
    pending = (await client.get("/api/actions", params={"status": "pending"})).json()
    assert [a["id"] for a in pending] == [action["id"]]
    assert hooks == [] and sent == []


async def test_failed_run_returns_to_review_with_error(env, provider):
    client, app, _, _ = env
    auto = app.state.automation
    auto.settings.smtp_host = "smtp.test"

    def broken(msg):
        from app.automation import ActionInvalid

        raise ActionInvalid("Could not send the email: connection refused")

    auto.send_smtp = broken
    provider.draft = {"to": ["bob@example.com"], "cc": [], "subject": "s", "body": "b"}
    action = await draft(client, "email")
    out = (
        await client.post(f"/api/actions/{action['id']}/approve", json={"payload": action["payload"]})
    ).json()
    assert out["status"] == "pending"
    assert "connection refused" in out["error"]


async def test_reject(env, provider):
    client, _, _, _ = env
    action = await draft(client, "document")
    resp = await client.post(f"/api/actions/{action['id']}/reject")
    assert resp.json()["status"] == "rejected"
    approve = await client.post(
        f"/api/actions/{action['id']}/approve", json={"payload": {"title": "t", "content": "c"}}
    )
    assert approve.status_code == 409


async def test_event_is_scheduled_and_exported(env, provider):
    client, _, _, _ = env
    provider.draft = {"title": "Dentist, checkup", "start": "2026-11-03T15:00", "end": "", "location": "", "notes": "Bring card"}
    action = await draft(client, "event", "dentist tomorrow at 3pm")
    done = (
        await client.post(f"/api/actions/{action['id']}/approve", json={"payload": action["payload"]})
    ).json()
    assert done["payload"]["end"] == "2026-11-03T16:00"  # one hour by default
    assert done["result"]["message"] == "Added to your schedule for Tue 03 Nov, 15:00."

    events = (await client.get("/api/events", params={"since": NOW})).json()
    assert [e["title"] for e in events] == ["Dentist, checkup"]
    assert (await client.get("/api/events", params={"since": "2026-11-04T00:00"})).json() == []

    ics = await client.get(done["result"]["download_url"])
    assert ics.headers["content-type"].startswith("text/calendar")
    assert "DTSTART:20261103T150000\r\n" in ics.text
    assert "SUMMARY:Dentist\\, checkup\r\n" in ics.text

    assert (await client.delete(f"/api/events/{events[0]['id']}")).status_code == 204
    assert (await client.get("/api/events")).json() == []


async def test_document_compiles_from_conversation(env, provider):
    client, _, _, _ = env
    provider.draft = {"title": "Trip notes", "content": "# Trip\n\n- Pack"}
    action = await draft(client, "document", "compile our chat into notes")
    turns, _, _ = provider.calls[0]
    assert turns[-1].content == "compile our chat into notes"
    done = (
        await client.post(f"/api/actions/{action['id']}/approve", json={"payload": action["payload"]})
    ).json()
    doc = await client.get(done["result"]["download_url"])
    assert doc.text == "# Trip\n\n- Pack"
    assert doc.headers["content-disposition"] == 'attachment; filename="Trip notes.md"'

    detail = (await client.get(f"/api/sessions/{action['session_id']}")).json()
    assert [a["status"] for a in detail["actions"]] == ["done"]


async def test_event_times_are_normalized_for_the_review_form(env, provider):
    client, _, _, _ = env
    provider.draft = {"title": "Dentist", "start": "2026-11-03 15:00", "end": "2026-11-03T16:00:00Z", "location": "", "notes": ""}
    action = await draft(client, "event")
    assert (action["payload"]["start"], action["payload"]["end"]) == ("2026-11-03T15:00", "2026-11-03T16:00")
    provider.draft = {"title": "x", "start": "tomorrow", "end": "", "location": "", "notes": ""}
    assert (await draft(client, "event"))["payload"]["start"] == ""  # left for the user to pick


async def test_bad_model_output_becomes_an_empty_draft(env, provider):
    client, _, _, _ = env
    provider.draft = {"to": "not a list", "subject": 5}
    action = await draft(client, "email")
    assert action["payload"] == {"to": [], "cc": [], "subject": "", "body": ""}


async def test_workflow_needs_configuration(provider, tmp_path):
    app = create_app(
        Settings(database_path=str(tmp_path / "t.db"), llm_provider="ollama"),
        ProviderRegistry({"ollama": provider}),
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            sid = (await client.post("/api/sessions", json={})).json()["id"]
            resp = await client.post(
                f"/api/sessions/{sid}/actions",
                json={"kind": "workflow", "instruction": "run backup", "now": NOW},
            )
            assert resp.status_code == 400
            assert provider.calls == []


def test_parse_json_object_tolerates_chatter():
    assert parse_json_object('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(LLMError):
        parse_json_object("no json here")
