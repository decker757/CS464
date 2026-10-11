"""Who may read exposure, and its shape on the wire. [2.2] #6

`GET /ledger/markets/{market_id}/exposure`, docs/api/ledger-service.md. The
figures are the service tests'; what this file owns is the guard and the
JSON: money and shares as decimal strings at scale 4, `state_version` an
int, `opened_at` an ISO string, and the cold market's nulls.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from core.roles import UserRole
from unit_test.conftest import bearer
from unit_test.exposure_fixtures import (
    EXAMPLE_B,
    EXAMPLE_QUANTITY,
    EXAMPLE_SEED,
    book_from_another_session,
    market_with,
)
from unit_test.sell_fixtures import hold

_FIELDS = {
    "market_id",
    "opened_at",
    "state_version",
    "seed_subsidy",
    "pool",
    "payout_exceeds_pool",
    "outcomes",
}
_OUTCOME_FIELDS = {
    "outcome_id",
    "position",
    "shares_outstanding",
    "payout_if_wins",
    "residue_if_wins",
}


def _path(market_id: uuid.UUID) -> str:
    return f"/ledger/markets/{market_id}/exposure"


async def test_an_anonymous_caller_is_refused(client: AsyncClient) -> None:
    response = await client.get(_path(uuid.uuid4()))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_an_expired_token_is_refused(client: AsyncClient) -> None:
    """An admin's token, so only its expiry can be what refuses it."""
    response = await client.get(
        _path(uuid.uuid4()),
        headers=bearer(uuid.uuid4(), UserRole.ADMIN, expires_in=-1),
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_a_malformed_market_id_is_invalid_request_naming_the_field(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.get(
        "/ledger/markets/not-a-uuid/exposure", headers=admin_headers
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert ["path", "market_id"] in [item["loc"] for item in error["details"]]


async def test_a_trader_is_refused_with_not_an_administrator(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """A valid, unexpired trader token, signed with this suite's secret and
    issuer. `not_an_administrator` is issued only after the token verifies,
    so a 401 cannot be what answers. Catches: `CurrentUser` instead of
    `CurrentAdmin`."""
    response = await client.get(_path(uuid.uuid4()), headers=trader_headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_an_administrator"


async def test_the_exposure_json_is_decimal_strings_at_scale_four(
    client: AsyncClient,
    session: AsyncSession,
    admin_headers: dict[str, str],
    user_id: uuid.UUID,
) -> None:
    """The documented example, seed 50, one buy of 500. Catches: a number on
    the wire; a dropped field."""
    market = await market_with(session, b=EXAMPLE_B, seed=EXAMPLE_SEED)
    await hold(session, market, user_id=user_id, quantity=EXAMPLE_QUANTITY)
    book = await book_from_another_session(market.market_id)

    response = await client.get(_path(market.market_id), headers=admin_headers)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == _FIELDS
    assert body["market_id"] == str(market.market_id)
    assert type(body["state_version"]) is int
    assert body["state_version"] == book.state_version
    assert type(body["opened_at"]) is str
    assert datetime.fromisoformat(body["opened_at"]) == book.opened_at
    assert body["payout_exceeds_pool"] is True

    for field, expected in (("seed_subsidy", "50.0000"), ("pool", "481.3569")):
        assert type(body[field]) is str, field
        assert body[field] == expected, field

    outcomes = {o["position"]: o for o in body["outcomes"]}
    assert set(outcomes) == {0, 1}
    expected_by_position = {
        0: {
            "outcome_id": str(market.outcomes[0]),
            "shares_outstanding": "500.0000",
            "payout_if_wins": "500.0000",
            "residue_if_wins": "-18.6431",
        },
        1: {
            "outcome_id": str(market.outcomes[1]),
            "shares_outstanding": "0.0000",
            "payout_if_wins": "0.0000",
            "residue_if_wins": "481.3569",
        },
    }
    for position, expected in expected_by_position.items():
        outcome = outcomes[position]
        assert set(outcome) == _OUTCOME_FIELDS
        for field, value in expected.items():
            assert type(outcome[field]) is str, (position, field)
            assert outcome[field] == value, (position, field)


async def test_a_market_with_no_book_is_nulls_and_an_empty_list_on_the_wire(
    client: AsyncClient, session: AsyncSession, admin_headers: dict[str, str]
) -> None:
    """DECISIONS.md, "Exposure on a market with no book is a 200 with nothing
    in it, and writes nothing". Catches: a dropped field, or a zero where the
    contract says null."""
    market_id = uuid.uuid4()

    response = await client.get(_path(market_id), headers=admin_headers)

    assert response.status_code == 200
    assert response.json() == {
        "market_id": str(market_id),
        "opened_at": None,
        "state_version": None,
        "seed_subsidy": None,
        "pool": None,
        "payout_exceeds_pool": False,
        "outcomes": [],
    }
