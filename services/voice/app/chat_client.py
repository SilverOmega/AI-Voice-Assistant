"""Client for the chat service's REST + SSE API."""

import json
from collections.abc import AsyncIterator

import httpx


class ChatClientError(Exception):
    pass


class ChatClient:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0),
        )

    async def ensure_session(self, session_id: str | None) -> str:
        """Return session_id if it exists, otherwise create a new session."""
        try:
            if session_id:
                resp = await self._client.get(f"/api/sessions/{session_id}")
                if resp.status_code == 200:
                    return session_id
            resp = await self._client.post("/api/sessions", json={})
            resp.raise_for_status()
            return resp.json()["id"]
        except httpx.HTTPError as exc:
            raise ChatClientError(f"Chat service unavailable: {exc}") from exc

    async def stream(self, session_id: str, content: str) -> AsyncIterator[tuple[str, dict]]:
        """Yield (event, data) pairs from the chat service SSE stream."""
        url = f"/api/sessions/{session_id}/messages/stream"
        try:
            async with self._client.stream(
                "POST", url, json={"content": content, "source": "voice"}
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")
                    try:
                        detail = json.loads(body).get("detail", body)
                    except ValueError:
                        detail = body
                    raise ChatClientError(f"Chat service error {resp.status_code}: {detail}")
                event = "message"
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        yield event, json.loads(line[5:].strip())
                    elif not line:
                        event = "message"
        except httpx.HTTPError as exc:
            raise ChatClientError(f"Chat service unavailable: {exc}") from exc

    async def ping(self) -> bool:
        try:
            return (await self._client.get("/health", timeout=3.0)).status_code == 200
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
