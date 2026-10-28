import base64
import json

import httpx
import pytest

from app.config import Settings
from app.llm import ChatTurn, GenerationParams, ImageData, LLMError, ProviderRegistry
from app.llm.anthropic_provider import _content
from app.llm.base import normalize_turns
from app.llm.echo import EchoProvider
from app.llm.ollama import OllamaProvider
from app.main import create_app
from tests.test_api import RecordingProvider, new_session, parse_sse

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def image_json(raw: bytes = PNG, media_type: str = "image/png") -> dict:
    return {"media_type": media_type, "data": base64.b64encode(raw).decode()}


@pytest.fixture
def provider():
    return RecordingProvider()


@pytest.fixture
async def client(provider, tmp_path):
    settings = Settings(database_path=str(tmp_path / "test.db"), llm_provider="ollama")
    registry = ProviderRegistry({"ollama": provider, "echo": EchoProvider(delay=0)})
    app = create_app(settings, registry)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_image_is_stored_served_and_sent_to_the_model(client, provider):
    sid = await new_session(client)
    resp = await client.post(
        f"/api/sessions/{sid}/messages/stream",
        json={"content": "What is this?", "images": [image_json(), image_json(JPEG, "image/jpeg")]},
    )
    start = parse_sse(resp.text)[0][1]
    assert [i["media_type"] for i in start["user_message"]["images"]] == ["image/png", "image/jpeg"]

    turns, _ = provider.calls[-1]
    assert turns[-1].images == (ImageData("image/png", PNG), ImageData("image/jpeg", JPEG))

    messages = (await client.get(f"/api/sessions/{sid}")).json()["messages"]
    image_id = messages[0]["images"][0]["id"]
    assert messages[1]["images"] == []
    served = await client.get(f"/api/images/{image_id}")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == PNG

    # later turns still carry the earlier image
    await client.post(f"/api/sessions/{sid}/messages", json={"content": "And the colors?"})
    turns, _ = provider.calls[-1]
    assert [len(t.images) for t in turns] == [2, 0, 0]

    # deleting the session deletes its images
    await client.delete(f"/api/sessions/{sid}")
    assert (await client.get(f"/api/images/{image_id}")).status_code == 404


async def test_image_only_message(client):
    await client.patch("/api/config", json={"provider": "echo"})
    sid = await new_session(client)
    resp = await client.post(f"/api/sessions/{sid}/messages", json={"images": [image_json()]})
    assert resp.status_code == 200
    assert resp.json()["assistant_message"]["content"].startswith("You sent 1 image.")
    assert (await client.get(f"/api/sessions/{sid}")).json()["title"] == "Image"


@pytest.mark.parametrize(
    "body",
    [
        {"content": "", "images": []},
        {"content": "x", "images": [{"media_type": "image/png", "data": "not base64!"}]},
        {"content": "x", "images": [image_json(JPEG, "image/png")]},  # wrong type for the bytes
        {"content": "x", "images": [image_json(PNG, "image/gif")]},
        {"content": "x", "images": [image_json()] * 5},
    ],
)
async def test_invalid_images_are_rejected(client, body):
    sid = await new_session(client)
    assert (await client.post(f"/api/sessions/{sid}/messages", json=body)).status_code == 422


def test_normalize_turns_keeps_images():
    img = ImageData("image/png", PNG)
    turns = normalize_turns(
        [ChatTurn("user", "", (img,)), ChatTurn("user", "what is it?"), ChatTurn("assistant", "a cat")]
    )
    assert turns == [ChatTurn("user", "what is it?", (img,)), ChatTurn("assistant", "a cat")]


def test_anthropic_image_blocks():
    img = ImageData("image/png", PNG)
    assert _content(ChatTurn("user", "hi")) == "hi"
    blocks = _content(ChatTurn("user", "", (img,)))
    assert blocks == [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": img.base64()},
        }
    ]
    assert _content(ChatTurn("user", "what?", (img,)))[-1] == {"type": "text", "text": "what?"}


def ollama_with(capabilities: list[str]) -> tuple[OllamaProvider, list[dict]]:
    chats: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": capabilities})
        chats.append(json.loads(request.content))
        return httpx.Response(200, text=json.dumps({"message": {"content": "ok"}, "done": True}))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OllamaProvider("http://ollama", "m", client=client), chats


PARAMS = GenerationParams(model="m", system="", temperature=0.5, max_tokens=10)
IMG = ImageData("image/png", PNG)


async def test_ollama_sends_images_to_vision_models():
    provider, chats = ollama_with(["completion", "vision"])
    assert [t async for t in provider.stream([ChatTurn("user", "what?", (IMG,))], PARAMS)] == ["ok"]
    assert chats[0]["messages"][0]["images"] == [IMG.base64()]


async def test_ollama_text_model_rejects_new_image_but_ignores_old_ones():
    provider, chats = ollama_with(["completion"])
    with pytest.raises(LLMError, match="can't read images"):
        async for _ in provider.stream([ChatTurn("user", "what?", (IMG,))], PARAMS):
            pass
    assert chats == []

    history = [ChatTurn("user", "what?", (IMG,)), ChatTurn("assistant", "?"), ChatTurn("user", "hi")]
    assert [t async for t in provider.stream(history, PARAMS)] == ["ok"]
    assert all("images" not in m for m in chats[0]["messages"])
