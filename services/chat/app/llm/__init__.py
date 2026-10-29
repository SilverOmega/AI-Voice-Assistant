"""LLM provider registry."""

from ..config import ProviderName, Settings
from .anthropic_provider import AnthropicProvider
from .base import ChatTurn, GenerationParams, ImageData, LLMError, LLMProvider, normalize_turns
from .echo import EchoProvider
from .ollama import OllamaProvider

__all__ = [
    "ChatTurn",
    "GenerationParams",
    "ImageData",
    "LLMError",
    "LLMProvider",
    "ProviderRegistry",
    "normalize_turns",
]


class ProviderRegistry:
    """Holds one instance of each provider so they can be switched at runtime."""

    def __init__(self, providers: dict[str, LLMProvider]):
        self._providers = providers

    @classmethod
    def from_settings(cls, settings: Settings) -> "ProviderRegistry":
        return cls(
            {
                "ollama": OllamaProvider(
                    settings.ollama_base_url,
                    settings.ollama_model,
                    keep_alive=settings.ollama_keep_alive,
                    num_thread=settings.ollama_num_thread,
                ),
                "anthropic": AnthropicProvider(
                    settings.anthropic_model,
                    effort=settings.anthropic_effort,
                    fallbacks=settings.anthropic_fallbacks,
                ),
                "echo": EchoProvider(),
            }
        )

    @property
    def names(self) -> list[ProviderName]:
        return list(self._providers)  # type: ignore[return-value]

    def get(self, name: str) -> LLMProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise LLMError(f"Unknown provider '{name}'") from exc

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()
