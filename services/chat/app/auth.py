"""Sign-in for phones, tablets and other computers.

nginx serves the app twice: on the local port, which only this computer can reach, and over
HTTPS for every other device. Requests through the HTTPS port carry `X-Remote-Access: 1` and
need a session cookie, which a device gets by entering APP_PASSCODE once. nginx checks the
cookie for the voice and wake-word services through /api/auth/check; the chat API checks it
itself as well (require_sign_in).
"""

import hashlib
import hmac
import math
import secrets
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status

from .notifications import NotificationCenter, shown_ip, visitor
from .repository import Repository
from .schemas import AuthStatus, SignIn

COOKIE = "assistant_session"
SESSION_DAYS = 90
MIN_PASSCODE = 6
MAX_FAILURES = 5  # wrong passcodes in a row ...
LOCKOUT_SECONDS = 15 * 60  # ... pause sign-in for everyone this long
SECRET_KEY = "auth_secret"

router = APIRouter(prefix="/api/auth")


class Auth:
    """Issues and checks session cookies: `<expiry>.<nonce>.<signature>`."""

    def __init__(self, repo: Repository, passcode: str):
        self.repo = repo
        self.passcode = passcode
        self._secret: bytes | None = None
        self._failures: list[float] = []
        self._locked_until = 0.0

    @property
    def configured(self) -> bool:
        return len(self.passcode) >= MIN_PASSCODE

    async def _key(self) -> bytes:
        if self._secret is None:
            stored = await self.repo.get_preference(SECRET_KEY)
            if not stored:
                stored = secrets.token_hex(32)
                await self.repo.set_preference(SECRET_KEY, stored)
            self._secret = bytes.fromhex(stored)
        # The passcode is part of the key, so changing it signs every device out.
        return hmac.new(self._secret, self.passcode.encode(), hashlib.sha256).digest()

    async def _sign(self, body: str) -> str:
        return hmac.new(await self._key(), body.encode(), hashlib.sha256).hexdigest()

    async def issue(self, now: float | None = None) -> str:
        expires = int((now or time.time()) + SESSION_DAYS * 86400)
        body = f"{expires}.{secrets.token_hex(8)}"
        return f"{body}.{await self._sign(body)}"

    async def valid(self, token: str | None, now: float | None = None) -> bool:
        if not token or not self.configured:
            return False
        try:
            expires, nonce, signature = token.split(".")
            expired = int(expires) <= (now or time.time())
        except ValueError:
            return False
        expected = await self._sign(f"{expires}.{nonce}")
        return hmac.compare_digest(signature, expected) and not expired

    def attempt(self, passcode: str, now: float | None = None) -> Literal["ok", "wrong", "locked"]:
        """Checks a passcode. Too many wrong ones in a row lock sign-in for a while."""
        now = time.monotonic() if now is None else now
        if now < self._locked_until:
            return "locked"
        if self.configured and hmac.compare_digest(passcode.encode(), self.passcode.encode()):
            self._failures.clear()
            return "ok"
        self._failures = [t for t in self._failures if now - t < LOCKOUT_SECONDS] + [now]
        if len(self._failures) >= MAX_FAILURES:
            self._failures.clear()
            self._locked_until = now + LOCKOUT_SECONDS
            return "locked"
        return "wrong"

    def retry_minutes(self, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        return max(1, math.ceil((self._locked_until - now) / 60))

    async def sign_out_everywhere(self) -> None:
        """A new secret: every cookie issued so far stops working."""
        fresh = secrets.token_hex(32)
        await self.repo.set_preference(SECRET_KEY, fresh)
        self._secret = bytes.fromhex(fresh)


def is_remote(request: Request) -> bool:
    """Set by nginx on the HTTPS port only (and cleared on the local one)."""
    return request.headers.get("x-remote-access") == "1"


def _auth(request: Request) -> Auth:
    return request.app.state.auth


def _notifications(request: Request) -> NotificationCenter:
    return request.app.state.notifications


async def signed_in(request: Request) -> bool:
    return not is_remote(request) or await _auth(request).valid(request.cookies.get(COOKIE))


async def require_sign_in(request: Request) -> None:
    """Dependency for every chat API route except the ones below."""
    if not await signed_in(request):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in on this device first.")


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE, token, max_age=SESSION_DAYS * 86400, path="/", secure=True, httponly=True, samesite="strict"
    )


@router.get("/status", response_model=AuthStatus)
async def auth_status(request: Request) -> AuthStatus:
    auth = _auth(request)
    return AuthStatus(
        remote=is_remote(request),
        configured=auth.configured,
        signed_in=await signed_in(request),
        app_url=request.app.state.settings.app_url,
    )


@router.get("/check", status_code=status.HTTP_204_NO_CONTENT)
async def auth_check(request: Request) -> Response:
    """nginx asks this before passing a request on to the voice and wake-word services."""
    await require_sign_in(request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
async def login(body: SignIn, request: Request) -> Response:
    auth = _auth(request)
    if not is_remote(request):
        return Response(status_code=status.HTTP_204_NO_CONTENT)  # this computer needs no passcode
    if not auth.configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Other devices can't sign in yet: set APP_PASSCODE (at least {MIN_PASSCODE} characters) "
            "in .env on the computer running the assistant, then restart it.",
        )
    label, ip = visitor(request)
    where = shown_ip(ip)
    result = auth.attempt(body.passcode)
    if result == "locked":
        minutes = auth.retry_minutes()
        await _notifications(request).emit(
            "sign_in",
            "Sign-in paused",
            f"{MAX_FAILURES} wrong passcodes in a row, the last from {label}{where}. "
            f"Nobody can sign in for {LOCKOUT_SECONDS // 60} minutes.",
            level="error",
        )
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many wrong passcodes. Try again in {minutes} minute{'s' if minutes != 1 else ''}.",
            headers={"Retry-After": str(minutes * 60)},
        )
    if result == "wrong":
        await _notifications(request).emit(
            "sign_in",
            "Wrong passcode",
            f"Someone entered a wrong passcode on {label}{where}.",
            level="warning",
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That passcode isn't right.")

    new = await request.app.state.chat.repo.touch_device(body.device_id, label, ip)
    if new:
        await _notifications(request).emit(
            "sign_in",
            "New device signed in",
            f"{label}{where}. If this wasn't you, change APP_PASSCODE in .env and restart: "
            "that signs every device out.",
            level="warning",
        )
    else:
        await _notifications(request).emit("sign_in", "Signed in", f"{label}{where} signed in again.")
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    _set_cookie(resp, await auth.issue())
    return resp


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> Response:
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    resp.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return resp


@router.post("/sign-out-all", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out_all(request: Request) -> Response:
    """Signs out every other device. The one asking stays signed in."""
    await require_sign_in(request)
    auth = _auth(request)
    await auth.sign_out_everywhere()
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    if is_remote(request):
        _set_cookie(resp, await auth.issue())
    return resp
