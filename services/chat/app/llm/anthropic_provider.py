"""Claude models via the official Anthropic Python SDK."""

import os
from collections.abc import AsyncIterator

import anthropic

from .base import ChatTurn, GenerationParams, LLMError, LLMProvider

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _supports_fallbacks(model: str) -> bool:
    # Server-side refusal fallback is not offered for Haiku models.
    return "haiku" not in model


def _content(turn: ChatTurn) -> str | list[dict]:
    if not turn.images:
        return turn.content
    blocks: list[dict] = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": img.media_type, "data": img.base64()},
        }
        for img in turn.images
    ]
    if turn.content.strip():  # the API rejects empty text blocks
        blocks.append({"type": "text", "text": turn.content})
    return blocks


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        default_model: str,
        effort: str = "low",
        fallbacks: bool = True,
        client: anthropic.AsyncAnthropic | None = None,
    ):
        self.default_model = default_model
        self._effort = effort
        self._fallbacks = fallbacks
        self._client = client
        self._client_error: str | None = None

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            # The SDK resolves credentials itself: ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN,
            # or an `ant auth login` profile.
            self._client = anthropic.AsyncAnthropic()
        return self._client

    async def stream(self, turns: list[ChatTurn], params: GenerationParams) -> AsyncIterator[str]:
        # Sampling parameters (temperature) are not accepted by current Claude models,
        # so `params.temperature` is intentionally not sent. Thinking runs adaptively by
        # default; effort keeps voice latency low.
        kwargs: dict = {
            "model": params.model,
            "max_tokens": params.max_tokens,
            "system": params.system,
            "messages": [{"role": t.role, "content": _content(t)} for t in turns],
            "output_config": {"effort": self._effort},
        }
        if self._fallbacks and _supports_fallbacks(params.model):
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        try:
            async with self.client.beta.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield text
                final = await stream.get_final_message()
            if final.stop_reason == "refusal":
                yield "\n\n(I can't help with that request.)"
            elif final.stop_reason == "max_tokens":
                yield "\n\n(Response truncated: output token limit reached.)"
        except anthropic.AuthenticationError as exc:
            raise LLMError(
                "Claude rejected the credentials. Set ANTHROPIC_API_KEY for the chat service."
            ) from exc
        except anthropic.NotFoundError as exc:
            raise LLMError(f"Claude model '{params.model}' was not found.") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Claude rate limit reached — try again shortly.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Cannot reach the Claude API. Check your network connection.") from exc
        except TypeError as exc:
            # Raised by the SDK when no credentials could be resolved at all.
            raise LLMError(f"Claude is not configured: {exc}") from exc

    async def check(self, model: str) -> tuple[bool, str]:
        if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
            return False, "ANTHROPIC_API_KEY is not set"
        try:
            await self.client.with_options(timeout=5.0, max_retries=0).models.retrieve(model)
        except anthropic.AuthenticationError:
            return False, "ANTHROPIC_API_KEY was rejected"
        except anthropic.NotFoundError:
            return False, f"model '{model}' not found"
        except anthropic.APIError as exc:
            return False, f"Claude API unavailable: {exc.__class__.__name__}"
        return True, f"claude model '{model}' ready"

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
