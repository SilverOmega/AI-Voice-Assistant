"""Claude bridge: lets the chat service use Claude through Claude Code and your Claude plan.

Runs on the computer where Claude Code is installed and signed in (not in Docker), listening
on 127.0.0.1 only. Docker Desktop containers reach it at host.docker.internal.

POST /v1/messages {model, system, messages: [{role, content, images}], max_tokens, json_schema}
  -> application/x-ndjson stream of {"type": "text" | "done" | "error", ...} (see cli.py)
GET /health -> is Claude Code installed and signed in (never the account's email)
Both need `Authorization: Bearer <CLAUDE_BRIDGE_TOKEN>`.
"""

import hmac
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import cli
from .config import Settings, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


class Runner(Protocol):
    async def status(self) -> dict[str, Any]: ...

    def stream(self, req: cli.Request) -> AsyncIterator[dict[str, Any]]: ...


class ImageIn(BaseModel):
    media_type: Literal["image/jpeg", "image/png", "image/gif", "image/webp"]
    data: str = Field(max_length=15_000_000)


class MessageIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(default="", max_length=2_000_000)
    images: list[ImageIn] = Field(default_factory=list, max_length=20)


class MessagesRequest(BaseModel):
    model: str = Field(pattern=cli.MODEL_PATTERN.pattern)
    system: str = Field(default="", max_length=400_000)
    messages: list[MessageIn] = Field(min_length=1, max_length=1_000)
    max_tokens: int = Field(default=4096, ge=16, le=128_000)
    json_schema: dict[str, Any] | None = None

    def to_cli(self) -> cli.Request:
        return cli.Request(
            model=self.model,
            system=self.system,
            messages=[
                cli.Message(m.role, m.content, [cli.Image(i.media_type, i.data) for i in m.images])
                for m in self.messages
            ],
            max_tokens=self.max_tokens,
            json_schema=self.json_schema,
        )


def create_app(settings: Settings | None = None, runner: Runner | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Assistant Claude Bridge", version="1.0.0")
    app.state.runner = runner or cli.CliRunner(settings)
    if not settings.claude_bridge_token:
        log.warning("CLAUDE_BRIDGE_TOKEN is not set: the bridge will refuse every request")

    def require_token(authorization: str | None = Header(default=None)) -> None:
        if not settings.claude_bridge_token:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "The Claude bridge has no CLAUDE_BRIDGE_TOKEN. Start it with scripts/bridge.ps1 or bridge.sh.",
            )
        expected = f"Bearer {settings.claude_bridge_token}".encode()
        if not authorization or not hmac.compare_digest(authorization.encode(), expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong or missing CLAUDE_BRIDGE_TOKEN")

    @app.get("/health", dependencies=[Depends(require_token)])
    async def health(request: Request) -> dict:
        info = await request.app.state.runner.status()
        return {"status": "ok" if info.get("signed_in") else "degraded", "service": "bridge", **info}

    @app.post("/v1/messages", dependencies=[Depends(require_token)])
    async def messages(body: MessagesRequest, request: Request) -> StreamingResponse:
        stream = request.app.state.runner.stream(body.to_cli())

        async def lines() -> AsyncIterator[bytes]:
            async for event in stream:
                yield (json.dumps(event) + "\n").encode("utf-8")

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    return app


app = create_app()
