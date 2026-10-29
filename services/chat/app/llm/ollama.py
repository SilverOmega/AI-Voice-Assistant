"""Local models served by Ollama (https://ollama.com) via its native /api/chat endpoint."""

import json
from collections.abc import AsyncIterator

import httpx

from .base import ChatTurn, GenerationParams, LLMError, LLMProvider, last_user_images


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        default_model: str,
        keep_alive: str = "24h",
        num_thread: int = 0,
        client: httpx.AsyncClient | None = None,
    ):
        self.default_model = default_model
        # How long Ollama keeps the model in memory after a request. Its own default (5m)
        # means the first message after a short break waits for a full reload.
        self.keep_alive = keep_alive
        # CPU threads for generation (0 = Ollama decides). When other programs keep some cores
        # busy, fewer threads than cores is much faster: llama.cpp threads wait on each other.
        self.num_thread = num_thread
        self._base_url = base_url.rstrip("/")
        self._vision: dict[str, bool] = {}
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0)
        )

    async def stream(self, turns: list[ChatTurn], params: GenerationParams) -> AsyncIterator[str]:
        send_images = any(t.images for t in turns) and await self._supports_vision(params.model)
        if last_user_images(turns) and not send_images:
            raise LLMError(
                f"The model '{params.model}' can't read images. Pull a vision model "
                "(for example: ollama pull gemma3:4b), select it in Settings, and send again."
            )
        messages = [{"role": "system", "content": params.system}] if params.system else []
        for t in turns:
            message: dict = {"role": t.role, "content": t.content}
            # A text-only model still gets the conversation, without earlier images.
            if t.images and send_images:
                message["images"] = [img.base64() for img in t.images]
            messages.append(message)
        body = {
            "model": params.model,
            "messages": messages,
            "stream": True,
            "keep_alive": self.keep_alive,
            "options": {
                **self._load_options(),
                "temperature": params.temperature,
                "num_predict": params.max_tokens,
            },
        }
        try:
            async with self._client.stream("POST", f"{self._base_url}/api/chat", json=body) as resp:
                if resp.status_code == 404:
                    raise LLMError(
                        f"Ollama model '{params.model}' is not available. "
                        f"Run: ollama pull {params.model}"
                    )
                if resp.status_code >= 400:
                    detail = (await resp.aread()).decode(errors="replace")[:300]
                    raise LLMError(f"Ollama error {resp.status_code}: {detail}")
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if "error" in chunk:
                        raise LLMError(f"Ollama error: {chunk['error']}")
                    text = chunk.get("message", {}).get("content", "")
                    if text:
                        yield text
                    if chunk.get("done"):
                        break
        except httpx.ConnectError as exc:
            raise LLMError(
                f"Cannot reach Ollama at {self._base_url}. Is it running? ({exc})"
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMError("Ollama timed out while generating a response.") from exc

    async def list_models(self) -> list[str]:
        resp = await self._client.get(f"{self._base_url}/api/tags", timeout=5.0)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]

    async def check(self, model: str) -> tuple[bool, str]:
        try:
            models = await self.list_models()
        except httpx.HTTPError as exc:
            return False, f"Ollama unreachable at {self._base_url}: {exc.__class__.__name__}"
        wanted = {model, f"{model}:latest"}
        if wanted & set(models):
            return True, f"ollama model '{model}' ready"
        return False, f"model '{model}' not pulled (run: ollama pull {model})"

    async def _supports_vision(self, model: str) -> bool:
        if model not in self._vision:
            try:
                resp = await self._client.post(
                    f"{self._base_url}/api/show", json={"model": model}, timeout=10.0
                )
                resp.raise_for_status()
            except httpx.HTTPError:
                return False  # not cached: the chat request reports the real problem
            self._vision[model] = "vision" in resp.json().get("capabilities", [])
        return self._vision[model]

    async def warm_up(self, model: str) -> None:
        # A generate request without a prompt only loads the model.
        # Same load options as chat requests, or the first message reloads the model.
        body = {"model": model, "keep_alive": self.keep_alive, "options": self._load_options()}
        resp = await self._client.post(f"{self._base_url}/api/generate", json=body)
        resp.raise_for_status()

    def _load_options(self) -> dict:
        return {"num_thread": self.num_thread} if self.num_thread > 0 else {}

    async def aclose(self) -> None:
        await self._client.aclose()
