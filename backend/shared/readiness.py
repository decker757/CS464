"""Can this service reach what it needs? The `/ready` route. #599

`/ready` is readiness, for dashboards and alerts. `/health` stays liveness, for
the load balancer and Docker: a database outage cannot be fixed by pulling a
server out of rotation, so only `/health` is allowed to do that (ADR 0028).

It clears ADR 0012's bar because a divergence between five copies would be a
bug: the timeout, the 503 shape and the "ok" / "down" words are read by one
dashboard and one alert rule, and they must mean the same thing everywhere.

Each service passes its own checks, so nothing here knows which dependencies a
service has. Realtime installs no SQLAlchemy, so that import is inside the
function that needs it.
"""
import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

# A check must catch its own named errors and return False. Only a timeout is
# turned into "down" for it; any other exception escapes and /ready answers 500.
Check = Callable[[], Awaitable[bool]]

READY_TIMEOUT_SECONDS = 2.0


async def is_database_reachable(engine: "AsyncEngine") -> bool:
    """`SELECT 1` on a fresh connection. False when it cannot be done."""
    from sqlalchemy import text  # noqa: PLC0415
    from sqlalchemy.exc import SQLAlchemyError  # noqa: PLC0415

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError):
        return False
    return True


async def _run_check(check: Check, timeout_seconds: float) -> bool:
    try:
        async with asyncio.timeout(timeout_seconds):
            return await check()
    except TimeoutError:
        return False


async def readiness_of(
    checks: Mapping[str, Check],
    *,
    timeout_seconds: float = READY_TIMEOUT_SECONDS,
) -> dict[str, bool]:
    """Run every check at once, each under its own timeout. Order is kept.

    Only a timeout counts as down. A check that raises anything else is a bug
    in the check, which must catch its own named errors and return False.
    """
    answers = await asyncio.gather(
        *(_run_check(check, timeout_seconds) for check in checks.values())
    )
    return dict(zip(checks, answers))


def add_ready_route(app: FastAPI, checks: Mapping[str, Check]) -> None:
    """Add `GET /ready` to `app`, answering from `checks` on every request.

    200 with every dependency "ok", or 503 naming each one "down". The checks
    run per request, each under `READY_TIMEOUT_SECONDS`; nothing is cached.
    """

    @app.get(
        "/ready",
        tags=["ops"],
        summary="Readiness: can this service reach what it needs",
    )
    async def ready() -> JSONResponse:
        answers = await readiness_of(checks)
        everything_up = all(answers.values())
        return JSONResponse(
            status_code=200 if everything_up else 503,
            content={
                "status": "ready" if everything_up else "not_ready",
                "dependencies": {
                    name: "ok" if up else "down" for name, up in answers.items()
                },
            },
        )
