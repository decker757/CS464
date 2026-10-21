"""Who may call the batch price read, its cap, and its shape. [X-1] #432

What the controller owns: the route needs a signed-in caller of any role, it
refuses more ids than its documented cap, and each item in the answer is the
single snapshot's body. Which markets are answered, and in what order, is
`test_snapshot_batch.py`'s.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.book_fixtures import Upstream, warm

_PATH = "/ledger/markets/snapshots"

# The documented cap, as a literal: docs/api/ledger-service.md promises 200.
_CAP = 200


def _ids(count: int) -> list[str]:
    return [str(uuid.uuid4()) for _ in range(count)]


# =========================================================================
# Authentication and authorisation
# =========================================================================
async def test_a_trader_may_read_the_batch(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """AC2 and the DoD's "needs no admin role": a trader's token is enough."""
    market = Upstream()
    await warm(session, market)

    response = await client.post(
        _PATH, json={"market_ids": [str(market.market_id)]}, headers=trader_headers
    )

    assert response.status_code == 200, response.json()
    assert [s["market_id"] for s in response.json()["snapshots"]] == [
        str(market.market_id)
    ]


async def test_an_anonymous_caller_is_refused(
    client: AsyncClient, session: AsyncSession
) -> None:
    """AC2's "signed-in": 401 `invalid_token`, like the single snapshot."""
    market = Upstream()
    await warm(session, market)

    response = await client.post(_PATH, json={"market_ids": [str(market.market_id)]})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


# =========================================================================
# The cap
# =========================================================================
async def test_the_cap_itself_is_accepted(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """AC1's documented cap, at its edge: 200 ids is one request."""
    response = await client.post(
        _PATH, json={"market_ids": _ids(_CAP)}, headers=trader_headers
    )

    assert response.status_code == 200, response.json()


async def test_one_id_over_the_cap_is_refused(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """AC1's cap, one past its edge: `422 invalid_request`, naming
    `market_ids`. DECISIONS.md, "The batch snapshot takes at most 200 ids, and
    refuses more"."""
    response = await client.post(
        _PATH, json={"market_ids": _ids(_CAP + 1)}, headers=trader_headers
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert [detail["loc"] for detail in error["details"]] == [["body", "market_ids"]]


async def test_an_empty_list_is_answered_with_no_snapshots(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """The same entry: no ids is a 200 with nothing in it, so a page with no
    cards needs no special case."""
    response = await client.post(_PATH, json={"market_ids": []}, headers=trader_headers)

    assert response.status_code == 200, response.json()
    assert response.json() == {"snapshots": []}


# =========================================================================
# The wire
# =========================================================================
async def test_each_item_is_the_single_snapshots_body(
    client: AsyncClient, session: AsyncSession, trader_headers: dict[str, str]
) -> None:
    """docs/api/ledger-service.md: the answer is `{"snapshots": [...]}`, and
    each item is the single snapshot's body for the same market, field for
    field, so one render function serves the batch, the single read and the
    socket frame."""
    market = Upstream()
    await warm(session, market)
    single = await client.get(
        f"/ledger/markets/{market.market_id}/snapshot", headers=trader_headers
    )

    response = await client.post(
        _PATH, json={"market_ids": [str(market.market_id)]}, headers=trader_headers
    )

    body = response.json()
    assert set(body) == {"snapshots"}
    assert body["snapshots"][0] == single.json()
