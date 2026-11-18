import sys

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.runner import RunResult, SubprocessRunner, limits_supported


def settings(**kw) -> Settings:
    base = {"sandbox_allow_unlimited": True, "sandbox_timeout_seconds": 5.0}
    return Settings(**{**base, **kw})


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def run(code: str, stdin: str = "", **kw) -> RunResult:
    return await SubprocessRunner(settings(**kw)).run("python", code, stdin)


async def test_runs_python_and_captures_output():
    result = await run("import sys\nprint('hi', 6 * 7)\nprint('oops', file=sys.stderr)\nsys.exit(3)")
    assert result.stdout.strip() == "hi 42"
    assert result.stderr.strip() == "oops"
    assert result.exit_code == 3
    assert not result.timed_out and not result.truncated


async def test_passes_stdin():
    result = await run("print(input().upper())", stdin="hello\n")
    assert result.stdout.strip() == "HELLO"


async def test_programs_that_ignore_their_input():
    assert (await run("print('quick')")).stdout.strip() == "quick"
    result = await run("import sys\nsys.exit(0)", stdin="x" * 2_000_000)
    assert result.exit_code == 0


async def test_stops_programs_that_run_too_long():
    result = await run("import time\nwhile True: time.sleep(0.05)", sandbox_timeout_seconds=1.0)
    assert result.timed_out
    assert result.duration_ms < 4000


async def test_caps_output():
    result = await run("print('x' * 100000)", sandbox_max_output=2048)
    assert result.truncated
    assert len(result.stdout) <= 2048


async def test_runs_in_an_empty_folder_without_the_environment(monkeypatch):
    monkeypatch.setenv("SECRET_FOR_TEST", "do-not-leak")
    result = await run("import os\nprint(sorted(os.listdir('.')), os.environ.get('SECRET_FOR_TEST'))")
    assert result.stdout.strip() == "['main.py'] None"


@pytest.mark.skipif(not limits_supported(), reason="resource limits exist only on Linux and macOS")
async def test_memory_limit():
    result = await run("x = bytearray(1024 * 1024 * 1024)\nprint('allocated')", sandbox_memory_mb=128)
    assert "allocated" not in result.stdout
    assert result.exit_code != 0


@pytest.mark.skipif(limits_supported(), reason="only where limits are missing")
async def test_refuses_without_limits_unless_allowed():
    app = create_app(Settings(sandbox_allow_unlimited=False))
    async with client_for(app) as client:
        resp = await client.post("/run", json={"code": "print(1)"})
    assert resp.status_code == 503


async def test_token_required_when_set():
    app = create_app(settings(sandbox_token="s3cret"))
    async with client_for(app) as client:
        assert (await client.post("/run", json={"code": "print(1)"})).status_code == 401
        wrong = {"Authorization": "Bearer nope"}
        assert (await client.post("/run", json={"code": "print(1)"}, headers=wrong)).status_code == 401
        good = {"Authorization": "Bearer s3cret"}
        resp = await client.post("/run", json={"code": "print(1)"}, headers=good)
    assert resp.status_code == 200
    assert resp.json()["stdout"].strip() == "1"


async def test_only_python():
    app = create_app(settings())
    async with client_for(app) as client:
        resp = await client.post("/run", json={"language": "bash", "code": "ls"})
    assert resp.status_code == 422


async def test_health():
    async with client_for(create_app(settings())) as client:
        body = (await client.get("/health")).json()
    assert body["status"] == "ok"
    assert body["limits"] == (sys.platform != "win32")
