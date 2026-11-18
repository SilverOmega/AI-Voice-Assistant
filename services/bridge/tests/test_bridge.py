import json
import sys
from pathlib import Path

import httpx
import pytest

from app import cli
from app.config import Settings
from app.main import create_app

FAKE = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]
TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def settings(**kw) -> Settings:
    return Settings(**{"claude_bridge_token": TOKEN, "claude_bridge_timeout_seconds": 20.0, **kw})


def runner(**kw) -> cli.CliRunner:
    return cli.CliRunner(settings(**kw), command=FAKE)


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def request(text: str, **kw) -> cli.Request:
    return cli.Request(model="claude-sonnet-5-5", system="Be kind.", messages=[cli.Message("user", text)], **kw)


async def events(text: str, r: cli.CliRunner | None = None, **kw) -> list[dict]:
    return [e async for e in (r or runner()).stream(request(text, **kw))]


async def test_streams_text_then_done():
    got = await events("hi")
    assert [e for e in got if e["type"] == "text"] == [{"type": "text", "text": "Hello"}, {"type": "text", "text": " world"}]
    assert got[-1] == {"type": "done", "truncated": False, "structured_output": None}


async def test_structured_output_for_json_schema():
    got = await events("plan", json_schema={"type": "object", "properties": {"steps": {"type": "array"}}})
    assert got == [{"type": "done", "truncated": False, "structured_output": {"steps": True}}]


async def test_reports_truncation():
    got = await events("long")
    assert got[-1]["type"] == "done" and got[-1]["truncated"] is True


@pytest.mark.parametrize(
    ("prompt", "code", "words"),
    [
        ("limit", "usage_limit", "usage limit"),
        ("signed out", "not_signed_in", "sign in"),
        ("crash", "failed", "boom"),
    ],
)
async def test_errors_are_plain(prompt, code, words):
    got = await events(prompt)
    assert got[-1]["type"] == "error" and got[-1]["code"] == code
    assert words in got[-1]["message"]


async def test_times_out_and_stops_the_process():
    quick = runner()
    quick.settings = quick.settings.model_copy(update={"claude_bridge_timeout_seconds": 1.0})  # below the minimum
    got = await events("slow", quick)
    assert got[-1]["code"] == "timeout"


async def test_runs_locked_down_with_the_subscription(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-be-used")
    got = await events("env", max_tokens=777)
    report = json.loads("".join(e["text"] for e in got if e["type"] == "text"))
    assert report == {"max_tokens": "777", "api_key": False, "folder": [], "system": "Be kind.", "tools": ""}


async def test_history_becomes_a_transcript_and_images_are_sent():
    req = cli.Request(
        model="claude-haiku-5-5",
        system="Sys.",
        messages=[
            cli.Message("user", "My name is Ana."),
            cli.Message("assistant", "Nice to meet you, Ana."),
            cli.Message("user", "echo what is my name?", [cli.Image("image/png", "aGk=")]),
        ],
    )
    got = [e async for e in runner().stream(req)]
    seen = json.loads(got[0]["text"])
    assert seen["images"] == 1
    assert "<earlier_conversation>" in seen["text"] and "Nice to meet you, Ana." in seen["text"]
    assert seen["text"].endswith("echo what is my name?")
    assert seen["system"].startswith("Sys.") and "earlier_conversation" in seen["system"]


def test_long_instructions_move_into_the_message():
    req = cli.Request(model="claude-sonnet-5-5", system="x" * (cli.MAX_INLINE_SYSTEM + 1), messages=[cli.Message("user", "hi")])
    args = cli.build_args(["claude"], req)
    assert args[args.index("--system-prompt") + 1] == cli.LONG_SYSTEM_NOTE
    text = json.loads(cli.user_line(req))["message"]["content"][-1]["text"]
    assert text.startswith("<instructions>") and text.endswith("hi")


def test_rejects_odd_model_names():
    with pytest.raises(ValueError):
        cli.build_args(["claude"], cli.Request(model="--tools default", system="", messages=[]))


def test_finds_cli_bundled_with_vscode(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: tmp_path))
    exe = "claude.exe" if sys.platform == "win32" else "claude"
    for version in ("2.1.9", "2.1.10"):
        path = tmp_path / ".vscode" / "extensions" / f"anthropic.claude-code-{version}-x" / "resources" / "native-binary"
        path.mkdir(parents=True)
        (path / exe).write_text("")
    assert "2.1.10" in cli.find_cli()[0]
    with pytest.raises(cli.CliUnavailable):
        cli.find_cli(str(tmp_path / "missing"))


def test_cmd_shims_are_not_run_through_cmd(tmp_path, monkeypatch):
    shim = tmp_path / "claude.cmd"
    shim.write_text("@echo off")
    with pytest.raises(cli.CliUnavailable):
        cli.find_cli(str(shim))
    script = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
    script.parent.mkdir(parents=True)
    script.write_text("")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None)
    assert cli.find_cli(str(shim)) == ["/usr/bin/node", str(script)]


async def test_http_stream_and_token():
    app = create_app(settings(), runner())
    async with client_for(app) as c:
        assert (await c.post("/v1/messages", json={"model": "claude-sonnet-5-5", "messages": [{"role": "user", "content": "hi"}]})).status_code == 401
        resp = await c.post(
            "/v1/messages", headers=AUTH, json={"model": "claude-sonnet-5-5", "messages": [{"role": "user", "content": "hi"}]}
        )
        assert resp.status_code == 200
        lines = [json.loads(line) for line in resp.text.splitlines()]
        assert lines[-1]["type"] == "done"
        bad = await c.post("/v1/messages", headers=AUTH, json={"model": "x y", "messages": [{"role": "user", "content": "hi"}]})
        assert bad.status_code == 422


async def test_refuses_everything_without_a_token():
    app = create_app(settings(claude_bridge_token=""), runner())
    async with client_for(app) as c:
        assert (await c.get("/health", headers=AUTH)).status_code == 503


async def test_health_never_shows_the_account(monkeypatch):
    app = create_app(settings(), runner())
    async with client_for(app) as c:
        body = (await c.get("/health", headers=AUTH)).json()
        assert body["signed_in"] is True and body["plan"] == "pro" and body["version"] == "9.9.9"
        assert "example.com" not in json.dumps(body)
    monkeypatch.setenv("FAKE_SIGNED_OUT", "1")
    app = create_app(settings(), runner())
    async with client_for(app) as c:
        body = (await c.get("/health", headers=AUTH)).json()
        assert body["status"] == "degraded" and "sign in" in body["detail"]


def test_usage_limit_message_says_when():
    assert "resets at" in cli.usage_limit_message({"resetsAt": 4_102_444_800})
    assert cli.usage_limit_message(None) == "You've reached your Claude plan's usage limit for now."
