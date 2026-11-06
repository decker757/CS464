"""Composition root for the ledger service.

Wires configuration, the database and the routes together. This is the only
place that decides which concrete implementations run.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import httpx
import redis.asyncio as redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from controller.errors import register_error_handlers
from controller.routes import router as ledger_router
from core import observability
from core.config import Settings, get_settings
from core.database import dispose_engine, get_engine, get_session_factory
from service import account_status, local_copy, market_terms
from service.leaderboard_refresher import run_leaderboard_refresher

observability.configure_logging()
logger = logging.getLogger(__name__)

# Seconds, for connect and every read and write on the bus (D-051).
# Without it a Redis that never answers makes the publish wait instead of
# raise, hanging a committed trade. Equals redis-py 8.1's default; stated so a
# version change cannot make it None.
_REDIS_TIMEOUT_SECONDS = 5.0


def _start_copy_refreshers(
    copy: local_copy.LocalCopy,
    settings: Settings,
    *,
    auth_client: httpx.AsyncClient,
    terms_client: httpx.AsyncClient,
) -> list[asyncio.Task[None]]:
    """Start one refresher per half of the copy, and install the market
    half's refresh as the copy's miss refresh. ADR 0030.

    One coalesced callable serves both the markets refresher and a trade's
    miss, so the two never fetch the published list at once. The first
    refresh of each runs inside its task, so boot never waits for auth or
    market_service, and the copy refuses every trade until it lands.
    """
    service_key = settings.ledger_service_key.get_secret_value()
    refresh_suspended = functools.partial(
        local_copy.refresh_suspended, copy, auth_client, service_key=service_key
    )
    refresh_markets = local_copy.coalesced(
        lambda: local_copy.refresh_published_markets(
            copy, terms_client, service_key=service_key
        )
    )
    copy.refresh_markets = refresh_markets

    interval_seconds = settings.local_copy_refresh_seconds
    return [
        asyncio.create_task(
            local_copy.run_refresher(
                "suspended", refresh_suspended, interval_seconds=interval_seconds
            ),
            name="local-copy-suspended",
        ),
        asyncio.create_task(
            local_copy.run_refresher(
                "markets", refresh_markets, interval_seconds=interval_seconds
            ),
            name="local-copy-markets",
        ),
    ]


async def _cancel_and_wait(task: asyncio.Task[None]) -> None:
    """Cancel a background task and wait for it to end, so none outlives
    the app."""
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Setup sits inside the `try`, so a failure partway (a mistyped REDIS_URL)
    # still reaches the disposal below. No DDL here: `ledger-migrate`
    # (migrate.py) brought the schema to head before this process started.
    # ADR 0020.
    try:
        settings = get_settings()

        # One client for the process (D-047). `from_url` dials lazily, so an
        # unreachable Redis does not stop boot, but parses eagerly, so a
        # mistyped REDIS_URL does (D-051).
        app.state.redis = redis.from_url(
            settings.redis_url.get_secret_value(),
            socket_timeout=_REDIS_TIMEOUT_SECONDS,
            socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
        )
        observability.instrument_redis()

        # One market_service client for the process too, because the gate
        # calls it on every trade that is not a replay (ADR 0017, D-047), and
        # one auth client for the account check on the same trades (ADR
        # 0022). `async with` closes both before the `finally`.
        async with (
            market_terms.open_client() as terms_client,
            account_status.open_client() as auth_client,
        ):
            app.state.terms_client = terms_client
            app.state.auth_client = auth_client

            # The first refresh runs inside the task, so boot never waits for
            # auth (DECISIONS.md, "The first refresh runs inside the
            # refresher's task").
            refresher: asyncio.Task[None] | None = None
            if settings.leaderboard_refresh_enabled:
                refresher = asyncio.create_task(
                    run_leaderboard_refresher(
                        get_session_factory(),
                        auth_client,
                        interval_seconds=settings.leaderboard_refresh_seconds,
                    ),
                    name="leaderboard-refresh",
                )
            else:
                logger.warning(
                    "leaderboard refresh disabled by LEADERBOARD_REFRESH_ENABLED "
                    "on this replica; if it is off on every replica, the ranking "
                    "stops updating and turns stale after two intervals"
                )

            # Last before the yield, so nothing that can raise sits between
            # starting these tasks and the `finally` that cancels them.
            copy = local_copy.LocalCopy(
                max_age_seconds=settings.local_copy_max_age_seconds
            )
            app.state.local_copy = copy
            copy_refreshers = _start_copy_refreshers(
                copy, settings, auth_client=auth_client, terms_client=terms_client
            )

            try:
                yield
            finally:
                # Stop every refresher while the clients are still open and
                # before the engine goes, or a refresh in flight calls a
                # closed client. ADR 0021, ADR 0030.
                for copy_refresher in copy_refreshers:
                    await _cancel_and_wait(copy_refresher)
                if refresher is not None:
                    await _cancel_and_wait(refresher)
    finally:
        # Nested so a failing `aclose()` cannot skip the disposal: a leaked
        # engine is every asyncpg connection this process opened.
        try:
            redis_client = getattr(app.state, "redis", None)
            if redis_client is not None:
                await redis_client.aclose()
        finally:
            await dispose_engine()


def _copy_ready_checks(app: FastAPI) -> dict[str, observability.Check]:
    """`/ready`'s two checks on the local copy: each half was loaded, no
    longer ago than its max age. Down before the lifespan builds the copy and
    until each half's first refresh lands, as a trade would find it. ADR 0030.
    """

    async def is_suspended_copy_fresh() -> bool:
        copy = getattr(app.state, "local_copy", None)
        return copy is not None and copy.is_suspended_fresh(now=copy.clock())

    async def is_market_copy_fresh() -> bool:
        copy = getattr(app.state, "local_copy", None)
        return copy is not None and copy.is_markets_fresh(now=copy.clock())

    return {
        "suspended_copy": is_suspended_copy_fresh,
        "market_copy": is_market_copy_fresh,
    }


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="CS464 Ledger Service",
        version="0.1.0",
        description=(
            "The record of every credit that has moved. Double-entry and "
            "append-only: a movement writes matching debit and credit rows "
            "that sum to zero, a balance is the sum of an account's entries "
            "rather than a column, and nothing anywhere can edit or remove an "
            "entry. Knows nothing about users beyond the id in a signed "
            "access token."
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
    app.include_router(ledger_router)

    @app.get("/health", tags=["ops"], summary="Liveness: the process is up")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    observability.install(
        app,
        ready_checks={
            "database": lambda: observability.is_database_reachable(get_engine()),
            **_copy_ready_checks(app),
        },
    )

    return app


app = create_app()
