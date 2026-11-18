"""Claude API keys added in the app: checked, stored, never returned, this computer only."""

import httpx
import pytest

from app.config import Settings
from app.llm import ProviderRegistry
from app.llm.anthropic_provider import AnthropicProvider
from app.main import create_app

from .test_api import RecordingProvider

GOOD = "sk-ant-api03-" + "x" * 40
REMOTE = {"X-Remote-Access": "1"}


class FakeClaude(AnthropicProvider):
    """Accepts GOOD without calling Anthropic."""

    def __init__(self):
        super().__init__("claude-sonnet-5-5")
        self.checked: list[str] = []

    async def check_key(self, key):
        self.checked.append(key)
        return (True, "Key accepted.") if key == GOOD else (False, "Anthropic rejected this key.")


@pytest.fixture(autouse=True)
def no_env_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)


def make_app(tmp_path, claude):
    settings = Settings(database_path=str(tmp_path / "k.db"), llm_provider="ollama", app_passcode="")
    return create_app(settings, ProviderRegistry({"ollama": RecordingProvider(), "anthropic": claude}))


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_cannot_switch_to_claude_without_a_key(tmp_path):
    app = make_app(tmp_path, FakeClaude())
    async with app.router.lifespan_context(app), client(app) as c:
        resp = await c.patch("/api/config", json={"provider": "anthropic", "model": "claude-sonnet-5-5"})
        assert resp.status_code == 409
        assert "API key" in resp.json()["detail"]
        assert (await c.get("/api/config")).json()["provider"] == "ollama"
        models = (await c.get("/api/models", params={"provider": "anthropic"})).json()
        assert models["key_source"] is None and models["can_add_key"] is True


async def test_add_key_then_switch_and_it_survives_a_restart(tmp_path):
    claude = FakeClaude()
    app = make_app(tmp_path, claude)
    async with app.router.lifespan_context(app), client(app) as c:
        bad = await c.put("/api/providers/anthropic/key", json={"api_key": "sk-ant-wrong-" + "y" * 30})
        assert bad.status_code == 422 and "rejected" in bad.json()["detail"]
        ok = await c.put("/api/providers/anthropic/key", json={"api_key": f"  {GOOD}  "})
        assert ok.status_code == 200
        assert ok.json()["key_source"] == "app"
        assert GOOD not in ok.text
        models = await c.get("/api/models", params={"provider": "anthropic"})
        assert models.json()["key_source"] == "app" and GOOD not in models.text
        assert (await c.patch("/api/config", json={"provider": "anthropic", "model": "claude-sonnet-5-5"})).status_code == 200
    assert claude.checked[-1] == GOOD

    again = FakeClaude()
    app2 = make_app(tmp_path, again)
    async with app2.router.lifespan_context(app2):
        assert again.key_source == "app"


async def test_remove_key(tmp_path):
    claude = FakeClaude()
    app = make_app(tmp_path, claude)
    async with app.router.lifespan_context(app), client(app) as c:
        await c.put("/api/providers/anthropic/key", json={"api_key": GOOD})
        await c.patch("/api/config", json={"provider": "anthropic", "model": "claude-sonnet-5-5"})
        assert (await c.delete("/api/providers/anthropic/key")).status_code == 409  # still in use
        await c.patch("/api/config", json={"provider": "ollama", "model": ""})
        resp = await c.delete("/api/providers/anthropic/key")
        assert resp.json()["key_source"] is None
    assert claude.key_source is None


async def test_other_devices_cannot_add_or_remove_keys(tmp_path):
    settings = Settings(database_path=str(tmp_path / "r.db"), llm_provider="ollama", app_passcode="long-passcode")
    app = create_app(settings, ProviderRegistry({"ollama": RecordingProvider(), "anthropic": FakeClaude()}))
    async with app.router.lifespan_context(app), client(app) as c:
        token = await app.state.auth.issue()
        c.cookies.set("assistant_session", token)
        resp = await c.put("/api/providers/anthropic/key", json={"api_key": GOOD}, headers=REMOTE)
        assert resp.status_code == 403, resp.text
        assert (await c.delete("/api/providers/anthropic/key", headers=REMOTE)).status_code == 403
        models = await c.get("/api/models", params={"provider": "anthropic"}, headers=REMOTE)
        assert models.status_code == 200
        assert models.json()["can_add_key"] is False


async def test_env_key_counts_as_set(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", GOOD)
    assert FakeClaude().key_source == "env"
