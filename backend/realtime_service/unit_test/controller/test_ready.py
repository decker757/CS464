"""`/ready` answers for Redis; `/health` does not. #599, ADR 0028."""

from __future__ import annotations

import redis.asyncio as redis
from fastapi.testclient import TestClient


def test_ready_is_200_when_redis_answers(client: TestClient) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["dependencies"] == {"redis": "ok"}


def test_a_redis_outage_fails_ready_and_leaves_health_alone(
    client: TestClient,
) -> None:
    client.app.state.redis = redis.Redis.from_url(
        "redis://127.0.0.1:1/0", socket_timeout=0.5, socket_connect_timeout=0.5
    )

    ready = client.get("/ready")

    assert ready.status_code == 503
    assert ready.json()["dependencies"] == {"redis": "down"}
    assert client.get("/health").status_code == 200
