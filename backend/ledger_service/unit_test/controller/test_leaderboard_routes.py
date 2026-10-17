"""Who may read the leaderboard, and its shape on the wire. [L-1] #38, [L-3] #40,
[BE][L-2] #269

`GET /ledger/leaderboard`, docs/api/ledger-service.md. Which generation is
served, the keyset walk and `stale`'s edge are `service/test_leaderboard_read.py`'s.
What this file owns: the token, the exact fields and their JSON types, the
request's own clock, the page size and its clamp, each error's status and
code, `me` and `is_me` following the token's caller, and a read that mints
nothing (DECISIONS.md, "The leaderboard read
writes nothing, and mints no grant").

Every query goes through `params=`, so a `+` in a cursor reaches the server
as `+` and not as a space.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient

from core.roles import UserRole
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

_PAGE_FIELDS = {
    "computed_at",
    "stale",
    "ranked_users",
    "me",
    "rows",
    "next_cursor",
    "has_more",
}
_ROW_FIELDS = {"rank", "username", "net_worth", "is_me"}
_ME_FIELDS = {"rank", "username", "net_worth"}
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


def _rows_without_is_me(rows: list[dict]) -> list[dict]:
    """The rows as every caller sees them: `is_me` is the one per-caller field."""
    stripped = []
    for row in rows:
        stripped.append({key: value for key, value in row.items() if key != "is_me"})
    return stripped


def _is_me_flags(body: dict) -> list:
    return [row["is_me"] for row in body["rows"]]


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


async def test_a_trader_and_an_administrator_see_the_same_ranking(
    client: AsyncClient, user_id: uuid.UUID, trader_headers: dict[str, str]
) -> None:
    """"Traders and administrators see the same board." Both are ranked, so
    `me` and `is_me` differ by caller and are left out of the comparison;
    whose they are is the next test's. Every other field is compared, so a
    field added later is too. Catches: `CurrentAdmin` on the route (the
    trader's 403 fails `_board`); anything else in the body that depends on
    the caller, such as `has_more`.

    `limit=1`, so there is a next page: `has_more` is true and `next_cursor`
    is set. On one page holding the whole board both are false and null for
    every caller, and a `has_more` that depended on the caller would pass."""
    admin_id = uuid.uuid4()
    await store_generation(
        computed_at=datetime.now(UTC),
        rows=[("ann", Decimal("12.3400")), ("root", Decimal("5.0000"))],
        user_ids=[user_id, admin_id],
    )

    as_trader = await _board(client, trader_headers, limit=1)
    as_admin = await _board(client, bearer(admin_id, UserRole.ADMIN), limit=1)

    for field in _PAGE_FIELDS - {"me", "rows"}:
        assert as_trader[field] == as_admin[field], field
    assert as_trader["has_more"] is True, "the page holds the whole board"
    assert _rows_without_is_me(as_trader["rows"]) == _rows_without_is_me(as_admin["rows"])
    assert [row["username"] for row in as_trader["rows"]] == ["ann"]


async def test_each_caller_gets_their_own_me_and_their_own_is_me_row(
    client: AsyncClient,
) -> None:
    """"The caller is matched by the token's `sub`." Two ranked callers, read
    one after the other. Catches: the route passing a fixed or fresh caller id
    instead of the token's."""
    first_caller, second_caller = uuid.uuid4(), uuid.uuid4()
    await store_generation(
        computed_at=datetime.now(UTC),
        rows=[
            ("ann", Decimal("12.3400")),
            ("bob", Decimal("5.0000")),
            ("cat", Decimal("1.0000")),
        ],
        user_ids=[first_caller, second_caller, uuid.uuid4()],
    )

    as_first = await _board(client, bearer(first_caller, UserRole.TRADER))
    as_second = await _board(client, bearer(second_caller, UserRole.TRADER))

    assert as_first["me"] == {"rank": 1, "username": "ann", "net_worth": "12.3400"}
    assert _is_me_flags(as_first) == [True, False, False]
    assert as_second["me"] == {"rank": 2, "username": "bob", "net_worth": "5.0000"}
    assert _is_me_flags(as_second) == [False, True, False]


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
        "me": None,
        "rows": [],
        "next_cursor": None,
        "has_more": False,
    }


async def test_a_row_and_me_are_rank_username_and_net_worth_as_a_scale_four_string(
    client: AsyncClient, user_id: uuid.UUID, trader_headers: dict[str, str]
) -> None:
    """"No user id, email or account id appears on this route", amounts are
    decimal strings, and `me` "has the same three fields as a row". The
    caller is `Amy_b`. Catches: `user_id` leaking into a row or `me`; a
    float, a number or a Decimal re-rendered without its trailing zeros; a
    rounded amount; `computed_at` losing its microseconds; `is_me` sent as
    0/1 rather than a boolean."""
    await store_generation(
        computed_at=_COMPUTED_AT,
        rows=[
            ("zed", Decimal("1234.5678")),
            ("Amy_b", Decimal("1000.0000")),
            ("x-1", Decimal("0.0001")),
        ],
        user_ids=[uuid.uuid4(), user_id, uuid.uuid4()],
    )

    body = await _board(client, trader_headers)

    assert set(body) == _PAGE_FIELDS
    assert _instant(body["computed_at"]) == _COMPUTED_AT
    assert body["ranked_users"] == 3
    for row in body["rows"]:
        assert set(row) == _ROW_FIELDS
        assert type(row["net_worth"]) is str
        assert type(row["rank"]) is int
        assert type(row["is_me"]) is bool
        assert _SCALE_FOUR.match(row["net_worth"]), row["net_worth"]
    assert set(body["me"]) == _ME_FIELDS
    assert type(body["me"]["rank"]) is int
    assert type(body["me"]["net_worth"]) is str
    assert _SCALE_FOUR.match(body["me"]["net_worth"]), body["me"]["net_worth"]
    assert body["me"] == {"rank": 2, "username": "Amy_b", "net_worth": "1000.0000"}
    assert _is_me_flags(body) == [False, True, False]
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
_MALFORMED = {
    "garbage": lambda generation_id: "nonsense",
    "rank-above-the-column": lambda generation_id: rank_cursor(
        generation_id, RANK_OVERFLOW
    ),
}


@pytest.mark.parametrize("cursor_of", list(_MALFORMED.values()), ids=list(_MALFORMED))
async def test_a_cursor_we_did_not_issue_is_malformed_cursor(
    client: AsyncClient, trader_headers: dict[str, str], cursor_of
) -> None:
    """`400 malformed_cursor`, never a 500. Two cases only: that the error is
    mapped, and that a rank beyond the column, on a cursor naming a stored
    generation, is refused here and not by Postgres. Every other shape is
    `core/test_leaderboard_read_rules.py`'s. Catches: the error not mapped;
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
    grant." A caller with no account, read back from fresh sessions, and
    "A caller with no account gets `me: null`, and still has no account
    afterwards." Catches: `ensure_granted` on this route."""
    caller = uuid.uuid4()
    await store_generation(
        computed_at=datetime.now(UTC), rows=[("ann", Decimal("1.0000"))]
    )
    counts_before = await board_counts()

    body = await _board(client, bearer(caller, UserRole.TRADER))

    assert [row["username"] for row in body["rows"]] == ["ann"]
    assert body["me"] is None
    assert _is_me_flags(body) == [False]
    async with session_factory()() as fresh:
        assert (
            await accounts_module().find(fresh, entities().AccountKind.USER, caller)
            is None
        )
        assert await transaction_count(fresh, key=grants().grant_key(caller)) == 0
    assert await board_counts() == counts_before
