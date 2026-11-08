"""HTTP wiring for the published markets' terms: the key, and the shape. #640

ADR 0021 for the key, ADR 0030 for the ledger's copy that reads it. Which
markets are listed is in `unit_test/service/test_published_terms.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from unit_test.conftest import actor, published_market

PUBLISHED = "/internal/markets/published"


def _key() -> dict[str, str]:
    return {"X-Service-Key": get_settings().ledger_service_key.get_secret_value()}


async def test_the_published_list_body_is_as_of_and_each_markets_terms(
    client: AsyncClient, session: AsyncSession
) -> None:
    market = await published_market(session, actor())

    response = await client.get(PUBLISHED, headers=_key())

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"as_of", "markets"}
    [terms] = body["markets"]
    assert set(terms) == {"id", "status", "close_time"}
    assert terms["id"] == str(market.id)
    assert terms["status"] == "open"
    assert datetime.fromisoformat(terms["close_time"]) == market.close_time
    as_of = datetime.fromisoformat(body["as_of"])
    assert abs(datetime.now(UTC) - as_of) < timedelta(seconds=5)


async def test_the_published_list_needs_the_service_key(client: AsyncClient) -> None:
    response = await client.get(PUBLISHED)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_service_key"


async def test_an_access_token_does_not_open_the_published_list(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.get(PUBLISHED, headers=admin_headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_service_key"


async def test_a_wrong_key_is_the_same_401_as_a_missing_one(client: AsyncClient) -> None:
    missing = await client.get(PUBLISHED)
    wrong = await client.get(PUBLISHED, headers={"X-Service-Key": "not-the-key"})

    assert wrong.status_code == 401
    assert wrong.json() == missing.json()


async def test_the_key_does_not_open_another_route(client: AsyncClient) -> None:
    response = await client.get("/public/markets", headers=_key())

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
