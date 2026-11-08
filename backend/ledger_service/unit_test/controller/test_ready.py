"""`/ready` answers for the database and the local copy; `/health` does not.
#599, ADR 0028, ADR 0030. When the copy is down is `test_local_copy_lifespan.py`."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine


async def test_ready_is_200_when_the_database_answers(client: AsyncClient) -> None:
    response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json()["dependencies"] == {
        "database": "ok",
        "suspended_copy": "ok",
        "market_copy": "ok",
    }


async def test_a_database_outage_fails_ready_and_leaves_health_alone(
    client: AsyncClient, monkeypatch
) -> None:
    unreachable = create_async_engine("postgresql+asyncpg://127.0.0.1:1/none")
    monkeypatch.setattr("main.get_engine", lambda: unreachable)

    ready = await client.get("/ready")

    assert ready.status_code == 503
    assert ready.json()["dependencies"] == {
        "database": "down",
        "suspended_copy": "ok",
        "market_copy": "ok",
    }
    assert (await client.get("/health")).status_code == 200
    await unreachable.dispose()
