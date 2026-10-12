"""Who may read the leaderboard, and its shape on the wire. [L-1] #38, [L-3] #40

`GET /ledger/leaderboard`, docs/api/ledger-service.md. Which generation is
served, the keyset walk and `stale`'s edge are `service/test_leaderboard_read.py`'s.
What this file owns: the token, the exact fields and their JSON types, the
request's own clock, the page size and its clamp, each error's status and
code, and a read that mints nothing (DECISIONS.md, "The leaderboard read
writes nothing, and mints no grant").

Every query goes through `params=`, so a `+` in a cursor reaches the server
as `+` and not as a space.
"""

from __future__ import annotations

import base64
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient

from core.roles import UserRole
from shared.paging import encode_cursor
from unit_test.conftest import bearer
from unit_test.leaderboard_read_fixtures import (
    PATH,
    RANK_OVERFLOW,
    board_counts,
    numbered_rows,
    rank_cursor,
    store_generation,
)
from unit_test.trade_fixtures import (
    accounts_module,
    entities,
    grants,
    session_factory,
    transaction_count,
)

_PAGE_FIELDS = {"computed_at", "stale", "ranked_users", "rows", "next_cursor", "has_more"}
_ROW_FIELDS = {"rank", "username", "net_worth"}
_SCALE_FOUR = re.compile(r"^\d+\.\d{4}$")
_COMPUTED_AT = datetime(2026, 10, 11, 9, 14, 0, 123456, tzinfo=UTC)


async def _board(
    client: AsyncClient, headers: dict[str, str], **params: object
) -> dict:
    response = await client.get(PATH, headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _error_of(response) -> str:
    return response.json()["error"]["code"]


def _instant(text: str) -> datetime:
    """`Z` or `+00:00`, read as one instant."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


# =========================================================================
# The token
# =========================================================================
@pytest.mark.parametrize(
    "headers",
    [{}, bearer(uuid.uuid4(), UserRole.TRADER, expires_in=-1)],
    ids=["no-token", "expired"],
)
async def test_an_anonymous_or_expired_caller_is_refused(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    """"Any valid access token." Catches: no auth dependency; expiry not
    verified."""
    response = await client.get(PATH, headers=headers)

    assert response.status_code == 401
    assert _error_of(response) == "invalid_token"


async def test_a_trader_and_an_administrator_see_the_same_board(
    client: AsyncClient, trader_headers: dict[str, str], admin_headers: dict[str, str]
) -> None:
    """"Traders and administrators see the same board." Catches: `CurrentAdmin`
    on the route; anything in the body that depends on the caller."""
    await store_generation(
        computed_at=datetime.now(UTC), rows=[("ann", Decimal("12.3400"))]
    )

    as_trader = await _board(client, trader_headers)
    as_admin = await _board(client, admin_headers)

    assert as_trader == as_admin
    assert [row["username"] for row in as_trader["rows"]] == ["ann"]


# =========================================================================
# The shape
# =========================================================================
async def test_an_empty_board_is_null_stale_and_no_rows_on_the_wire(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """The doc's answer before any refresh, exactly. Catches: a 404 or 503
    on an empty board; `stale` false; a missing key."""
    body = await _board(client, trader_headers)

    assert body == {
        "computed_at": None,
        "stale": True,
        "ranked_users": 0,
        "rows": [],
        "next_cursor": None,
        "has_more": False,
    }


async def test_a_row_is_rank_username_and_net_worth_as_a_scale_four_string(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """"Only `rank`, `username` and `net_worth` are sent. No user id …", and
    amounts are decimal strings. Catches: `user_id` leaking; a float, a
    number or a Decimal re-rendered without its trailing zeros; a rounded
    amount; `computed_at` losing its microseconds."""
    await store_generation(
        computed_at=_COMPUTED_AT,
        rows=[
            ("zed", Decimal("1234.5678")),
            ("Amy_b", Decimal("1000.0000")),
            ("x-1", Decimal("0.0001")),
        ],
    )

    body = await _board(client, trader_headers)

    assert set(body) == _PAGE_FIELDS
    assert _instant(body["computed_at"]) == _COMPUTED_AT
    assert body["ranked_users"] == 3
    for row in body["rows"]:
        assert set(row) == _ROW_FIELDS
        assert type(row["net_worth"]) is str
        assert type(row["rank"]) is int
        assert _SCALE_FOUR.match(row["net_worth"]), row["net_worth"]
    assert [(row["rank"], row["username"], row["net_worth"]) for row in body["rows"]] == [
        (1, "zed", "1234.5678"),
        (2, "Amy_b", "1000.0000"),
        (3, "x-1", "0.0001"),
    ]


@pytest.mark.parametrize(
    ("age", "stale"),
    [(timedelta(0), False), (timedelta(hours=1), True)],
    ids=["just-refreshed", "an-hour-old"],
)
async def test_stale_is_judged_on_the_request_s_own_clock(
    client: AsyncClient, trader_headers: dict[str, str], age: timedelta, stale: bool
) -> None:
    """"The read route reads `datetime.now(UTC)` once per request." Far from
    the edge, which the service tests own. Catches: the route passing a fixed
    or missing `now`."""
    await store_generation(
        computed_at=datetime.now(UTC) - age, rows=[("ann", Decimal("1.0000"))]
    )

    body = await _board(client, trader_headers)

    assert body["stale"] is stale


# =========================================================================
# The page size
# =========================================================================
async def test_the_default_page_is_fifty(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """`limit` defaults to 50. Catches: the route not passing the server's
    default page size to the service."""
    await store_generation(computed_at=datetime.now(UTC), rows=numbered_rows(120))

    pages = [await _board(client, trader_headers)]
    while pages[-1]["next_cursor"] is not None:
        pages.append(await _board(client, trader_headers, cursor=pages[-1]["next_cursor"]))
        assert len(pages) < 10, "next_cursor never ran out"

    assert [len(page["rows"]) for page in pages] == [50, 50, 20]
    assert [page["has_more"] for page in pages] == [True, True, False]
    assert [row["rank"] for page in pages for row in page["rows"]] == list(range(1, 121))
    assert {page["ranked_users"] for page in pages} == {120}


async def test_a_limit_above_two_hundred_is_clamped_not_refused(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """"Clamped at 200. Asking for more is not an error." Five rows past the
    clamp, so an unclamped read serves 205. Catches: the route bypassing
    `_page_size`; a 422 for a large limit."""
    await store_generation(computed_at=datetime.now(UTC), rows=numbered_rows(205))

    first = await _board(client, trader_headers, limit=500)
    rest = await _board(client, trader_headers, limit=500, cursor=first["next_cursor"])

    assert [row["rank"] for row in first["rows"]] == list(range(1, 201))
    assert first["has_more"] is True
    assert [row["rank"] for row in rest["rows"]] == list(range(201, 206))
    assert rest["has_more"] is False
    assert rest["next_cursor"] is None


@pytest.mark.parametrize("limit", ["0", "-1", "abc"])
async def test_a_limit_below_one_or_not_a_number_is_invalid_request(
    client: AsyncClient, trader_headers: dict[str, str], limit: str
) -> None:
    """The doc's 422: "For example `limit=0`." Catches: `limit` without
    `ge=1`, serving a page of nothing or of everything."""
    response = await client.get(PATH, headers=trader_headers, params={"limit": limit})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert ["query", "limit"] in [item["loc"] for item in error["details"]]


# =========================================================================
# The cursor's errors
# =========================================================================
def _not_utf_8() -> str:
    return base64.urlsafe_b64encode(b"\xff\xfe|1").decode().rstrip("=")


_MALFORMED = {
    "garbage": lambda generation_id: "nonsense",
    "plus-slash-padding": lambda generation_id: "a+b/c==",
    "history-cursor": lambda generation_id: encode_cursor(_COMPUTED_AT, generation_id),
    "not-utf-8": lambda generation_id: _not_utf_8(),
    "rank-above-the-column": lambda generation_id: rank_cursor(
        generation_id, RANK_OVERFLOW
    ),
}


@pytest.mark.parametrize("cursor_of", list(_MALFORMED.values()), ids=list(_MALFORMED))
async def test_a_cursor_we_did_not_issue_is_malformed_cursor(
    client: AsyncClient, trader_headers: dict[str, str], cursor_of
) -> None:
    """`400 malformed_cursor`, never a 500. The overflowing rank names the
    generation that exists, so an unbounded decoder would send it to
    Postgres. Catches: the error not mapped; `UnicodeDecodeError` or
    asyncpg's integer overflow escaping as a 500."""
    generation_id = uuid.uuid4()
    await store_generation(
        computed_at=datetime.now(UTC),
        rows=[("ann", Decimal("2.0000")), ("bob", Decimal("1.0000"))],
        generation_id=generation_id,
    )

    response = await client.get(
        PATH, headers=trader_headers, params={"cursor": cursor_of(generation_id)}
    )

    assert response.status_code == 400
    assert _error_of(response) == "malformed_cursor"


async def test_a_cursor_naming_a_vanished_generation_is_leaderboard_refreshed(
    client: AsyncClient, trader_headers: dict[str, str]
) -> None:
    """A well-formed cursor whose generation is not stored, beside one that
    is. Catches: the 409's status or code not mapped; a fall back to the
    current generation."""
    await store_generation(
        computed_at=datetime.now(UTC), rows=[("ann", Decimal("1.0000"))]
    )

    response = await client.get(
        PATH, headers=trader_headers, params={"cursor": rank_cursor(uuid.uuid4(), 1)}
    )

    assert response.status_code == 409
    assert _error_of(response) == "leaderboard_refreshed"


# =========================================================================
# A read that writes nothing
# =========================================================================
async def test_reading_the_board_mints_no_grant_and_writes_nothing(
    client: AsyncClient,
) -> None:
    """"This route writes nothing … does not mint the caller's starting
    grant." A caller with no account, read back from fresh sessions.
    Catches: `ensure_granted` on this route."""
    caller = uuid.uuid4()
    await store_generation(
        computed_at=datetime.now(UTC), rows=[("ann", Decimal("1.0000"))]
    )
    counts_before = await board_counts()

    body = await _board(client, bearer(caller, UserRole.TRADER))

    assert [row["username"] for row in body["rows"]] == ["ann"]
    async with session_factory()() as fresh:
        assert (
            await accounts_module().find(fresh, entities().AccountKind.USER, caller)
            is None
        )
        assert await transaction_count(fresh, key=grants().grant_key(caller)) == 0
    assert await board_counts() == counts_before
