import asyncio
import json

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.chat_client import ChatClient
from app.config import SAMPLE_RATE, Settings
from app.engines import EngineStatus, Transcript
from app.main import create_app


class FakeSTT:
    def __init__(self, text: str = "what time is it"):
        self.text = text
        self.status = EngineStatus("fake-stt", ready=True)
        self.calls = 0

    def load(self) -> None:
        pass

    def transcribe(self, audio) -> Transcript:
        self.calls += 1
        return Transcript(text=self.text, language="en", duration=1.0, elapsed=0.01)


class FakeTTS:
    def __init__(self):
        self.status = EngineStatus("fake-tts", ready=True)
        self.spoken: list[str] = []

    def load(self) -> None:
        pass

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        self.spoken.append(text)
        return b"RIFF" + text.encode()

    def list_voices(self) -> list[str]:
        return ["en_US-test-medium"]


class FakeChatBackend:
    """Mimics the chat service's REST + SSE API via httpx.MockTransport."""

    def __init__(self, reply: str = "It is noon. Have a great lunch!", delay: float = 0.0):
        self.reply = reply
        self.delay = delay
        self.received: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/api/sessions" and request.method == "POST":
            return httpx.Response(201, json={"id": "sess-1"})
        if path.startswith("/api/sessions/") and request.method == "GET":
            return httpx.Response(200 if path.endswith("sess-1") else 404, json={})
        if path.endswith("/messages/stream"):
            self.received.append(json.loads(request.content))
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=self._sse()
            )
        return httpx.Response(404)

    async def _sse(self):
        def ev(name, data):
            return f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()

        yield ev("start", {"model": "fake"})
        for word in self.reply.split(" "):
            if self.delay:
                await asyncio.sleep(self.delay)
            yield ev("token", {"text": word + " "})
        yield ev("done", {"message": {"id": "m-1"}})


def tone(seconds: float, amplitude: float = 0.3, freq: float = 220.0) -> bytes:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2").tobytes()


def silence(seconds: float, noise: float = 0.001) -> bytes:
    rng = np.random.default_rng(0)
    samples = rng.normal(0, noise, int(seconds * SAMPLE_RATE))
    return (samples * 32767).astype("<i2").tobytes()


def chunks(data: bytes, size: int = 1280 * 2):
    for i in range(0, len(data), size):
        yield data[i : i + size]


@pytest.fixture
def backend():
    return FakeChatBackend()


@pytest.fixture
def fakes():
    return FakeSTT(), FakeTTS()


def make_client(backend: FakeChatBackend, stt, tts, **overrides) -> TestClient:
    settings = Settings(tts_engine=overrides.pop("tts_engine", "piper"), **overrides)
    chat = ChatClient(
        "http://chat",
        client=httpx.AsyncClient(base_url="http://chat", transport=httpx.MockTransport(backend.handler)),
    )
    return TestClient(create_app(settings, stt=stt, tts=tts, chat=chat))


@pytest.fixture
def client(backend, fakes):
    with make_client(backend, *fakes) as c:
        yield c
