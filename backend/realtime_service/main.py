"""Composition root for the realtime service.

Wires configuration, the bus and the socket together. This is the only place
that decides which concrete implementations run.

**Note what is missing: there is no database.** No engine, no session, no
`create_all`, no schema and no login role, which makes this the only backend
service that needs nothing from `sql/` and cannot be broken by anything in it.
That is not a coincidence — it is ADR 0010's argument for why a fifth service
was affordable at all. This one holds connections and relays frames; every
durable fact it repeats belongs to somebody else.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from controller.routes import router as realtime_router
from core.config import get_settings
from service.bus import PriceBus
from service.ordering import get_gate
from service.subscriptions import get_hub

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    bus = PriceBus(redis_url=settings.redis_url, hub=get_hub(), gate=get_gate())
    app.state.bus = bus
    task = asyncio.create_task(bus.run(), name="price-bus")

    try:
        yield
    finally:
        # The bus returns only on cancellation, so this is the shutdown path and
        # not an error path. Awaiting it is what makes the Redis connection
        # close before the process exits rather than during interpreter
        # teardown, where the traceback would be unreadable.
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="CS464 Realtime Service",
        version="0.1.0",
        description=(
            "Live market prices over a WebSocket. Subscribes to one Redis "
            "channel, fans each price event out to the sockets watching that "
            "market, and drops anything older than what it has already sent. "
            "Owns no data: it holds no database, prices nothing itself, and "
            "every number it relays belongs to the service that published it.\n\n"
            "The socket is not described here — OpenAPI has no vocabulary for "
            "one. See docs/api/realtime-service.md."
        ),
        lifespan=lifespan,
    )

    # Guards /health and /docs, and nothing else: CORS does not apply to
    # WebSocket handshakes. The socket's origin check is a hand-written one in
    # `controller/transport.py`, and that split is the single easiest thing to
    # get wrong about this service.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(realtime_router)

    @app.get("/health", tags=["ops"], summary="Liveness and readiness probe")
    async def health(request: Request) -> dict[str, str]:
        """Green even when the bus is down, and says so in the same breath.

        A relay that cannot reach Redis is not doing its job, but failing the
        probe would have the orchestrator restart the container and drop every
        socket it is holding — which is the one response that reliably makes a
        Redis blip worse for users. The connections are still live, the client
        still has its last price, and the bus reconnects on its own.
        """
        bus: PriceBus | None = getattr(request.app.state, "bus", None)
        return {
            "status": "ok",
            "bus": "connected" if bus is not None and bus.connected else "disconnected",
        }

    return app


app = create_app()
