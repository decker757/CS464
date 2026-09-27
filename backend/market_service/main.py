"""Composition root: wires configuration, the database, the routes and the sweeper."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from controller.errors import register_error_handlers
from controller.public_routes import router as public_market_router
from controller.routes import router as market_router
from core.config import get_settings
from core.database import create_all, dispose_engine, get_session_factory

# Imported for its side effect: registering the mappers on Base before
# create_all runs. Do not rely on another module pulling it in transitively.
from model import entities  # noqa: F401
from service.sweeper import run_close_sweeper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Creates missing tables only; a new column needs a hand-applied migration.
    await create_all()

    # [F-4] #44. Started here, the composition root, so the sweeper is handed
    # its session factory and a test can drive it without the app. Switching
    # it off makes statuses stale, never a closed market tradeable. ADR 0011.
    settings = get_settings()
    sweeper: asyncio.Task[None] | None = None
    if settings.close_sweep_enabled:
        sweeper = asyncio.create_task(
            run_close_sweeper(
                get_session_factory(),
                interval_seconds=settings.close_sweep_seconds,
                batch_limit=settings.close_sweep_batch,
            ),
            name="close-sweeper",
        )
    else:
        logger.warning(
            "close sweeper disabled by CLOSE_SWEEP_ENABLED; market statuses "
            "will not be maintained on this replica"
        )

    try:
        yield
    finally:
        # Stop the sweeper before disposing the engine, or a sweep in flight
        # loses its pool and shutdown ends in a stack trace.
        if sweeper is not None:
            sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await sweeper
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="CS464 Market Service",
        version="0.1.0",
        description=(
            "Drafting and submitting prediction markets. Owns markets, their "
            "outcomes and their resolution sources, and knows nothing about "
            "users beyond the id in a signed access token."
        ),
        lifespan=lifespan,
    )

    # allow_credentials with an exact origin list, never "*", or the browser
    # silently drops the auth cookie it needs to send here.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    app.include_router(market_router)
    app.include_router(public_market_router)

    @app.get("/health", tags=["ops"], summary="Liveness and readiness probe")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
