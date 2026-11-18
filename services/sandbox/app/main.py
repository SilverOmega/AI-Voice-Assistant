"""Code sandbox service: runs Python programs the user approved in the chat.

Only the chat service calls it, over an internal Docker network with no internet access.
POST /run {language: "python", code, stdin} -> {stdout, stderr, exit_code, duration_ms,
timed_out, truncated}. GET /health.
"""

import hmac
import logging
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .runner import Runner, SandboxUnavailable, SubprocessRunner, limits_supported

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


class RunRequest(BaseModel):
    language: Literal["python"] = "python"
    code: str = Field(min_length=1, max_length=100_000)
    stdin: str = Field(default="", max_length=100_000)


def create_app(settings: Settings | None = None, runner: Runner | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Assistant Code Sandbox", version="1.0.0")
    app.state.runner = runner or SubprocessRunner(settings)

    def check_token(authorization: str | None) -> None:
        if not settings.sandbox_token:
            return
        expected = f"Bearer {settings.sandbox_token}"
        if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong or missing sandbox token")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "service": "sandbox", "limits": limits_supported()}

    @app.post("/run")
    async def run(body: RunRequest, request: Request, authorization: str | None = Header(default=None)) -> dict:
        check_token(authorization)
        try:
            result = await request.app.state.runner.run(body.language, body.code, body.stdin)
        except SandboxUnavailable as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
        log.info(
            "run: exit=%s %dms timed_out=%s truncated=%s",
            result.exit_code, result.duration_ms, result.timed_out, result.truncated,
        )
        return result.as_dict()

    return app


app = create_app()
