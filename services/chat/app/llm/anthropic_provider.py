"""Claude models via the official Anthropic Python SDK."""

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

import anthropic

from .base import (
    TRUNCATED_NOTE,
    AgentStep,
    AgentTurn,
    ChatTurn,
    GenerationParams,
    LLMAuthError,
    LLMError,
    LLMProvider,
    ToolCall,
    ToolSpec,
)

REFUSAL_NOTE = "\n\n(I can't help with that request.)"

FALLBACK_BETA = "server-side-fallback-2026-07-01"
NO_KEY = "no Claude API key yet: add one from the model menu, or set ANTHROPIC_API_KEY in .env"


def env_credentials() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


# Offered in the model menu next to the microphone.
CLAUDE_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2")


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
        # A key added in the app; ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN in .env come first.
        self._app_key: str | None = None

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            if self._app_key and not env_credentials():
                self._client = anthropic.AsyncAnthropic(api_key=self._app_key)
            else:
                # The SDK resolves credentials itself: ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN,
                # or an `ant auth login` profile.
                self._client = anthropic.AsyncAnthropic()
        return self._client

    @property
    def key_source(self) -> str | None:
        """Where the credentials come from: "env" (.env), "app" (added in the app), or None."""
        if env_credentials():
            return "env"
        return "app" if self._app_key else None

    async def set_app_key(self, key: str | None) -> None:
        """Use a key added in the app (None removes it). The next request uses it."""
        self._app_key = key or None
        if self._client is not None:
            old, self._client = self._client, None
            await old.close()

    async def check_key(self, key: str) -> tuple[bool, str]:
        """Whether Anthropic accepts this key, without using it for anything else."""
        try:
            async with anthropic.AsyncAnthropic(api_key=key, timeout=10.0, max_retries=0) as probe:
                await probe.models.retrieve(self.default_model)
        except anthropic.AuthenticationError:
            return False, "Anthropic rejected this key. Copy it again from console.anthropic.com."
        except anthropic.PermissionDeniedError:
            return False, "This key isn't allowed to use Claude models."
        except anthropic.NotFoundError:
            return True, "Key accepted."  # the key works; the default model name is the problem
        except anthropic.APIConnectionError:
            return False, "Can't reach Anthropic. Check the internet connection and try again."
        except anthropic.APIError as exc:
            return False, f"Anthropic couldn't check the key ({exc.__class__.__name__}). Try again."
        return True, "Key accepted."

    def _request(self, params: GenerationParams, messages: list[dict]) -> dict:
        # Sampling parameters (temperature) are not accepted by current Claude models,
        # so `params.temperature` is intentionally not sent. Thinking runs adaptively by
        # default; effort keeps voice latency low.
        kwargs: dict = {
            "model": params.model,
            "max_tokens": params.max_tokens,
            "system": params.system,
            "messages": messages,
            "output_config": {"effort": self._effort},
        }
        if self._fallbacks and _supports_fallbacks(params.model):
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    async def stream(self, turns: list[ChatTurn], params: GenerationParams) -> AsyncIterator[str]:
        kwargs = self._request(params, [{"role": t.role, "content": _content(t)} for t in turns])
        with _api_errors(params.model):
            async with self.client.beta.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield text
                final = await stream.get_final_message()
            if final.stop_reason == "refusal":
                yield REFUSAL_NOTE
            elif final.stop_reason == "max_tokens":
                yield TRUNCATED_NOTE

    async def run_tools_step(
        self, turns: list[AgentTurn], params: GenerationParams, tools: list[ToolSpec]
    ) -> AgentStep:
        kwargs = self._request(params, agent_messages(turns))
        kwargs["tools"] = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
        ]
        with _api_errors(params.model):
            response = await self.client.beta.messages.create(**kwargs)
        return step_from_response(response)

    async def list_models(self) -> list[str]:
        """The current Claude models to choose from, the configured one first. No API call,
        so the list shows even before a key is set."""
        return list(dict.fromkeys([self.default_model, *CLAUDE_MODELS]))

    async def check(self, model: str) -> tuple[bool, str]:
        if self.key_source is None:
            return False, NO_KEY
        try:
            await self.client.with_options(timeout=5.0, max_retries=0).models.retrieve(model)
        except anthropic.AuthenticationError:
            return False, "the Claude API key was rejected"
        except anthropic.NotFoundError:
            return False, f"model '{model}' not found"
        except anthropic.APIError as exc:
            return False, f"Claude API unavailable: {exc.__class__.__name__}"
        return True, f"claude model '{model}' ready"

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()


@contextmanager
def _api_errors(model: str) -> Iterator[None]:
    """SDK errors as LLMError, with messages fit to show users."""
    try:
        yield
    except anthropic.AuthenticationError as exc:
        raise LLMAuthError(
            "Claude rejected the API key. Add a new one from the model menu, or fix ANTHROPIC_API_KEY in .env."
        ) from exc
    except anthropic.NotFoundError as exc:
        raise LLMError(f"Claude model '{model}' was not found.") from exc
    except anthropic.RateLimitError as exc:
        raise LLMError("Claude rate limit reached — try again shortly.") from exc
    except anthropic.APIStatusError as exc:
        raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError("Cannot reach the Claude API. Check your network connection.") from exc
    except TypeError as exc:
        # Raised by the SDK when no credentials could be resolved at all.
        raise LLMAuthError("Claude has no API key yet. Add one from the model menu next to the microphone.") from exc


def agent_messages(turns: list[AgentTurn]) -> list[dict]:
    """Agent turns as Messages API messages.

    An assistant turn from an earlier step is sent back exactly as Claude produced it
    (`provider_data`), thinking blocks included, which tool use requires. Consecutive tool
    results go together in one user message.
    """
    out: list[dict] = []

    def after_results() -> bool:  # the last message holds tool results
        return bool(out) and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list)

    for t in turns:
        if t.role == "tool":
            block = {"type": "tool_result", "tool_use_id": t.tool_call_id, "content": t.content or "(empty)"}
            if after_results():
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif t.role == "user" and after_results():
            out[-1]["content"].append({"type": "text", "text": t.content})
        elif t.role == "assistant":
            if t.provider_data is not None:
                out.append({"role": "assistant", "content": t.provider_data})
                continue
            blocks: list[Any] = [{"type": "text", "text": t.content}] if t.content.strip() else []
            blocks += [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in t.tool_calls
            ]
            out.append({"role": "assistant", "content": blocks or t.content})
        else:
            out.append({"role": "user", "content": t.content})
    return out


def step_from_response(response: Any) -> AgentStep:
    text: list[str] = []
    calls: list[ToolCall] = []
    for block in response.content:
        if block.type == "text":
            text.append(block.text)
        elif block.type == "tool_use":
            args = block.input if isinstance(block.input, dict) else {}
            calls.append(ToolCall(block.id, block.name, args))
    reason = response.stop_reason
    body = "".join(text).strip()
    if reason == "refusal":
        body = (body + REFUSAL_NOTE).strip()
        calls = []
    return AgentStep(body, tuple(calls), truncated=reason == "max_tokens", provider_data=list(response.content))
