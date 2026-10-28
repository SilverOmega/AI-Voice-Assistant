"""Chat service: sessions, history and streaming LLM replies."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .chat_service import ChatService
from .config import Settings, get_settings
from .llm import ProviderRegistry
from .repository import Repository
from .routes import router
from .schemas import RuntimeConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(
    settings: Settings | None = None, registry: ProviderRegistry | None = None
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        repo = Repository(settings.database_path)
        await repo.connect()
        providers = registry or ProviderRegistry.from_settings(settings)
        runtime = RuntimeConfig(
            provider=settings.llm_provider,
            model=settings.llm_model,
            system_prompt=settings.system_prompt,
            temperature=settings.temperature,
        )
        app.state.chat = ChatService(repo, providers, settings, runtime)
        app.state.chat.start_warm_up()
        app.state.health_cache = {}
        try:
            yield
        finally:
            await app.state.chat.aclose()
            await providers.aclose()
            await repo.close()

    app = FastAPI(title="Assistant Chat Service", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
