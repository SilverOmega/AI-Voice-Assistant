"""Chat service: sessions, history and streaming LLM replies."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import auth
from .automation import Automation
from .chat_service import ChatService
from .config import Settings, get_settings
from .llm import ProviderRegistry
from .notifications import NotificationCenter
from .notifier import Notifier
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
        app.state.settings = settings
        app.state.auth = auth.Auth(repo, settings.app_passcode)
        app.state.notifier = Notifier(settings)
        app.state.notifications = NotificationCenter(repo, app.state.notifier)
        app.state.chat = ChatService(repo, providers, settings, runtime, app.state.notifications)
        app.state.chat.start_warm_up()
        app.state.automation = Automation(repo, settings)
        reminders = asyncio.create_task(
            app.state.notifications.remind_loop(settings.notify_remind_minutes)
        )
        app.state.health_cache = {}
        try:
            yield
        finally:
            reminders.cancel()
            await app.state.notifications.aclose()
            await app.state.notifier.aclose()
            await app.state.automation.aclose()
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
    app.include_router(auth.router)
    app.include_router(router, dependencies=[Depends(auth.require_sign_in)])
    return app


app = create_app()
