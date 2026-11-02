import asyncio
import time

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from shared.readiness import add_ready_route, is_database_reachable, readiness_of


def _answers(value: bool):
    async def check() -> bool:
        return value

    return check


async def _hangs() -> bool:
    await asyncio.sleep(10)
    return True


async def _get_ready(checks) -> httpx.Response:
    app = FastAPI()
    add_ready_route(app, checks)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/ready")


async def test_every_dependency_that_answers_is_ok():
    result = await readiness_of({"database": _answers(True), "redis": _answers(True)})
    assert result == {"database": True, "redis": True}


async def test_a_dependency_that_hangs_is_down_within_the_timeout():
    started = time.monotonic()
    result = await readiness_of({"database": _hangs}, timeout_seconds=0.2)
    assert result == {"database": False}
    assert time.monotonic() - started < 1.0


async def test_an_unreachable_postgres_is_not_reachable():
    engine = create_async_engine("postgresql+asyncpg://127.0.0.1:1/none")
    try:
        assert await is_database_reachable(engine) is False
    finally:
        await engine.dispose()


async def test_ready_is_503_and_names_the_dependency_that_failed():
    response = await _get_ready({"database": _answers(False), "other": _answers(True)})
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": {"database": "down", "other": "ok"},
    }


async def test_ready_is_200_when_every_dependency_answers():
    response = await _get_ready({"database": _answers(True)})
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "dependencies": {"database": "ok"}}


async def test_ready_is_503_when_a_dependency_hangs_past_the_default_timeout():
    async def hangs_for_three_seconds() -> bool:
        await asyncio.sleep(3)
        return True

    started = time.monotonic()
    response = await _get_ready({"database": hangs_for_three_seconds})
    assert response.status_code == 503
    assert response.json()["dependencies"] == {"database": "down"}
    assert time.monotonic() - started < 2.6
