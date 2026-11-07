import httpx
import pytest

from app.auth import COOKIE, MAX_FAILURES, Auth
from app.config import Settings
from app.llm import ProviderRegistry
from app.llm.echo import EchoProvider
from app.main import create_app

PASSCODE = "k7mq-2hxp-9tfa"
REMOTE = {"x-remote-access": "1", "x-real-ip": "192.168.1.40"}
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Version/18.0 Mobile/15E148 Safari/604.1"


def make_app(tmp_path, passcode=PASSCODE):
    settings = Settings(
        database_path=str(tmp_path / "test.db"), llm_provider="echo", app_passcode=passcode, app_url="https://192.168.1.10:8443"
    )
    return create_app(settings, ProviderRegistry({"echo": EchoProvider()}))


@pytest.fixture
async def client(tmp_path):
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        # https, so the client keeps and sends the Secure session cookie.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as c:
            yield c


async def login(client, passcode=PASSCODE, device="a1b2c3d4-iphone"):
    return await client.post(
        "/api/auth/login", json={"passcode": passcode, "device_id": device}, headers={**REMOTE, "user-agent": IPHONE}
    )


async def test_this_computer_needs_no_passcode(client):
    status = (await client.get("/api/auth/status")).json()
    assert status == {"remote": False, "configured": True, "signed_in": True, "app_url": "https://192.168.1.10:8443"}
    assert (await client.get("/api/sessions")).status_code == 200
    assert (await client.get("/api/auth/check")).status_code == 204


async def test_other_devices_sign_in_once(client):
    assert (await client.get("/api/sessions", headers=REMOTE)).status_code == 401
    assert (await client.get("/api/auth/check", headers=REMOTE)).status_code == 401
    assert (await client.get("/api/auth/status", headers=REMOTE)).json()["signed_in"] is False

    wrong = await login(client, "guess-123")
    assert (wrong.status_code, wrong.json()["detail"]) == (401, "That passcode isn't right.")
    assert COOKIE not in client.cookies

    ok = await login(client)
    assert ok.status_code == 204
    cookie = ok.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    assert (await client.get("/api/sessions", headers=REMOTE)).status_code == 200
    assert (await client.get("/api/auth/check", headers=REMOTE)).status_code == 204
    assert (await client.get("/api/auth/status", headers=REMOTE)).json()["signed_in"] is True

    # The sign-in recorded the device, so opening the app afterwards isn't another "new device".
    hello = await client.post("/api/devices/hello", json={"device_id": "a1b2c3d4-iphone"}, headers=REMOTE)
    assert hello.json() == {"new": False}
    titles = [(n["title"], n["body"]) for n in (await client.get("/api/notifications")).json()["items"]]
    assert titles[0][0] == "New device signed in"
    assert titles[0][1].startswith("Safari on iPhone at 192.168.1.40.")
    assert titles[1] == ("Wrong passcode", "Someone entered a wrong passcode on Safari on iPhone at 192.168.1.40.")

    # Signing out removes the cookie.
    assert (await client.post("/api/auth/logout", headers=REMOTE)).status_code == 204
    assert (await client.get("/api/sessions", headers=REMOTE)).status_code == 401


def use_cookie(client, value):
    client.cookies.clear()
    client.cookies.set(COOKIE, value)


async def test_forged_or_stale_cookies_are_refused(client):
    await login(client)
    good = client.cookies[COOKIE]
    expires, nonce, signature = good.split(".")
    for forged in (f"{int(expires) + 1}.{nonce}.{signature}", f"{expires}.{nonce}.{'0' * 64}", "nonsense", ""):
        use_cookie(client, forged)
        assert (await client.get("/api/sessions", headers=REMOTE)).status_code == 401, forged

    # "Sign out all other devices": old cookies stop working, the device asking stays in.
    use_cookie(client, good)
    resp = await client.post("/api/auth/sign-out-all", headers=REMOTE)
    assert resp.status_code == 204
    fresh = resp.cookies[COOKIE]
    assert fresh != good
    use_cookie(client, fresh)
    assert (await client.get("/api/sessions", headers=REMOTE)).status_code == 200
    use_cookie(client, good)
    assert (await client.get("/api/sessions", headers=REMOTE)).status_code == 401


async def test_wrong_passcodes_pause_sign_in(client):
    for _ in range(MAX_FAILURES - 1):
        assert (await login(client, "wrong-one")).status_code == 401
    locked = await login(client, "wrong-one")
    assert locked.status_code == 429
    assert locked.json()["detail"] == "Too many wrong passcodes. Try again in 15 minutes."
    assert locked.headers["retry-after"] == "900"
    # Even the right passcode waits until the pause ends.
    assert (await login(client)).status_code == 429
    items = (await client.get("/api/notifications")).json()["items"]
    assert (items[0]["title"], items[0]["level"]) == ("Sign-in paused", "error")


async def test_without_a_passcode_other_devices_are_turned_away(tmp_path):
    app = make_app(tmp_path, passcode="12345")  # too short: counts as not set
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as c:
            assert (await c.get("/api/auth/status", headers=REMOTE)).json()["configured"] is False
            refused = await login(c, "12345")
            assert refused.status_code == 503
            assert "set APP_PASSCODE" in refused.json()["detail"]
            assert (await c.get("/api/sessions", headers=REMOTE)).status_code == 401
            assert (await c.get("/api/sessions")).status_code == 200  # this computer still works


async def test_changing_the_passcode_signs_everyone_out(tmp_path):
    app = make_app(tmp_path)
    async with app.router.lifespan_context(app):
        token = await app.state.auth.issue()
        assert await app.state.auth.valid(token)
        changed = Auth(app.state.auth.repo, "a-new-passcode")
        assert not await changed.valid(token)
        assert not await app.state.auth.valid(token, now=10**12)  # expired


def test_lockout_window():
    auth = Auth(repo=None, passcode=PASSCODE)  # type: ignore[arg-type]
    for t in range(MAX_FAILURES - 1):
        assert auth.attempt("nope", now=t * 600) == "wrong"  # 10 minutes apart: the oldest ones expire
    assert auth.attempt("nope", now=4 * 600 + 1) == "wrong"
    assert auth.attempt(PASSCODE, now=4 * 600 + 2) == "ok"
