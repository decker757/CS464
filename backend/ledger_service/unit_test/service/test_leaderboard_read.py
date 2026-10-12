"""Reading the leaderboard: which generation, which rows, stale, and the
cursor. [L-1] #38, [L-3] #40

The spec is `docs/api/ledger-service.md`'s `GET /ledger/leaderboard` and
DECISIONS.md's "The leaderboard is stored as generations …", "A refresh's
`computed_at` is Python's clock … `stale` is derived on read against the same
clock" and "A leaderboard cursor names its generation …". The wire shape, the
page size, the token and the grant are `controller/test_leaderboard_routes.py`'s.

Every generation here has a twin, so a read that forgets which generation it
is in returns rows it should not. Expected values are literals.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.errors import LeaderboardRefreshed, MalformedCursor
from core.paging import encode_rank_cursor
from unit_test.leaderboard_fixtures import (
    T0,
    Directory,
    at,
    fixed_clock,
    granted,
    refresh_with,
)
from unit_test.leaderboard_read_fixtures import (
    numbered_rows,
    rank_cursor,
    read_page,
    store_generation,
)

_LOW_ID = uuid.UUID("00000000-0000-4000-8000-000000000000")
_HIGH_ID = uuid.UUID("ffffffff-ffff-4fff-bfff-ffffffffffff")


def _listed(page) -> list[tuple[int, str, Decimal]]:
    return [(row.rank, row.username, row.net_worth) for row in page.rows]


async def _walk(session: AsyncSession, *, limit: int, now) -> list:
    """Every page from page one, following `next_cursor` until it is None."""
    pages = [await read_page(session, now=now, limit=limit)]
    while pages[-1].next_cursor is not None:
        pages.append(
            await read_page(session, now=now, limit=limit, cursor=pages[-1].next_cursor)
        )
        assert len(pages) < 100, "next_cursor never ran out"
    return pages


# =========================================================================
# Which generation
# =========================================================================
async def test_with_no_generation_the_board_is_empty_and_stale(
    session: AsyncSession,
) -> None:
    """"Before the ledger has ever completed a refresh, the answer is
    `computed_at: null`, `stale: true`, `rows: []`, `ranked_users: 0`."
    Catches: `stale` false with nothing to judge; raising on an empty board."""
    page = await read_page(session, now=T0)

    assert page.computed_at is None
    assert page.stale is True
    assert page.rows == []
    assert page.ranked_users == 0
    assert page.next_cursor is None


@pytest.mark.parametrize(
    ("newer_id", "older_id", "newer_inserted_first"),
    [
        # Kills ORDER BY id DESC (fff is the older) and "last inserted".
        (_LOW_ID, _HIGH_ID, True),
        # Kills ORDER BY id ASC (000 is the older) and "first inserted".
        (_HIGH_ID, _LOW_ID, False),
    ],
    ids=["newer-low-id-inserted-first", "newer-high-id-inserted-second"],
)
async def test_the_newest_computed_at_is_served_not_the_last_inserted_or_an_id_order(
    session: AsyncSession,
    newer_id: uuid.UUID,
    older_id: uuid.UUID,
    newer_inserted_first: bool,
) -> None:
    """"The newest generation, by `computed_at`, is the current ranking."
    Both cases kill `computed_at ASC`; the per-case mutations are above."""
    newer = {
        "computed_at": at(60),
        "rows": [("new_one", Decimal("7.0000"))],
        "generation_id": newer_id,
    }
    older = {
        "computed_at": at(0),
        "rows": [("old_one", Decimal("5.0000"))],
        "generation_id": older_id,
    }
    for generation in (newer, older) if newer_inserted_first else (older, newer):
        await store_generation(**generation)

    page = await read_page(session, now=at(60))

    assert page.computed_at == at(60)
    assert _listed(page) == [(1, "new_one", Decimal("7.0000"))]
    assert page.ranked_users == 1


async def test_an_empty_generation_is_served_with_its_own_computed_at(
    session: AsyncSession,
) -> None:
    """A generation with no entries is what a refresh writes when every
    holder is suspended, and it is still the current ranking. Catches: the
    page read inner-joining the generation to its entries, so the empty
    generation yields no row and reads as no generation at all (null
    `computed_at`); "the newest generation that has entries", which serves
    the older one."""
    await store_generation(
        computed_at=T0,
        rows=[
            ("ann", Decimal("3.0000")),
            ("bob", Decimal("2.0000")),
            ("cat", Decimal("1.0000")),
        ],
    )
    await store_generation(computed_at=at(200), rows=[])

    page = await read_page(session, now=at(200))
    stale_page = await read_page(session, now=at(200) + timedelta(seconds=121))

    assert page.computed_at == at(200)
    assert page.stale is False
    assert stale_page.stale is True
    assert page.ranked_users == 0
    assert page.rows == []
    assert page.next_cursor is None


async def test_rows_come_in_rank_order_exactly_as_stored(session: AsyncSession) -> None:
    """Rank, username and net worth are the stored values, unrounded, in rank
    order. The rows are stored in rank order but in neither username nor net
    worth order. Catches: ordering by username or net worth; a quantize or
    float on the way out (`0.0001` and `1234.5678`)."""
    await store_generation(
        computed_at=T0,
        rows=[
            ("zed", Decimal("1234.5678")),
            ("Amy_b", Decimal("1000.0000")),
            ("x-1", Decimal("0.0001")),
        ],
    )

    page = await read_page(session, now=T0)

    assert _listed(page) == [
        (1, "zed", Decimal("1234.5678")),
        (2, "Amy_b", Decimal("1000.0000")),
        (3, "x-1", Decimal("0.0001")),
    ]
    assert page.ranked_users == 3


# =========================================================================
# stale
# =========================================================================
async def test_stale_follows_the_configured_interval(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Tying the threshold to the interval keeps it right when the interval
    is changed." At 10 seconds the edge is 20; both reads would be fresh at
    the default 60. Catches: a hard-coded 60 or 120."""
    monkeypatch.setattr(get_settings(), "leaderboard_refresh_seconds", 10)
    await store_generation(computed_at=T0, rows=[("solo", Decimal("1.0000"))])

    at_the_edge = await read_page(session, now=T0 + timedelta(seconds=20))
    past_the_edge = await read_page(
        session, now=T0 + timedelta(seconds=20, microseconds=1)
    )

    assert at_the_edge.stale is False
    assert past_the_edge.stale is True


# =========================================================================
# Paging
# =========================================================================
@pytest.mark.parametrize("ranked", [20, 21], ids=["partial-last-page", "exact-multiple"])
async def test_following_next_cursor_returns_every_row_once_in_rank_order(
    session: AsyncSession, ranked: int
) -> None:
    """Keyset by rank inside one generation. 21 rows at 7 a page ends exactly
    on a page boundary, so the last full page must not offer a cursor to an
    empty one. Catches: `rank >= last_rank` (a repeat); a cursor set whenever
    the page is full; `ranked_users` counted per page."""
    rows = numbered_rows(ranked)
    await store_generation(computed_at=T0, rows=rows)

    pages = await _walk(session, limit=7, now=T0)

    walked = [row for page in pages for row in _listed(page)]
    expected = [
        (rank, username, net_worth)
        for rank, (username, net_worth) in enumerate(rows, start=1)
    ]
    assert walked == expected
    assert len(pages) == (ranked + 6) // 7
    assert pages[-1].next_cursor is None
    assert {page.computed_at for page in pages} == {T0}
    assert {page.ranked_users for page in pages} == {ranked}


async def test_a_cursor_keeps_reading_its_generation_after_one_newer_exists(
    session: AsyncSession,
) -> None:
    """"Every page reached through `next_cursor` comes from the same ranking
    as the first page", and "a page read through a cursor into the previous
    generation reports that generation's `computed_at` and its own `stale`".
    Catches: serving the newest generation for every cursor; `stale` or
    `computed_at` taken from the newest rather than the one served."""
    old_rows = [(f"a{number}", Decimal(100 - number)) for number in range(1, 6)]
    new_rows = [(f"b{number}", Decimal(200 - number)) for number in range(1, 6)]
    await store_generation(computed_at=T0, rows=old_rows)
    first = await read_page(session, now=T0, limit=2)
    await store_generation(computed_at=at(200), rows=new_rows)

    second = await read_page(session, now=at(200), limit=2, cursor=first.next_cursor)
    fresh = await read_page(session, now=at(200), limit=2)

    assert [row.username for row in second.rows] == ["a3", "a4"]
    assert [row.rank for row in second.rows] == [3, 4]
    assert second.computed_at == T0
    assert second.stale is True
    assert [row.username for row in fresh.rows] == ["b1", "b2"]
    assert fresh.computed_at == at(200)
    assert fresh.stale is False


async def test_a_cursor_is_refused_once_its_generation_has_been_pruned(
    session: AsyncSession,
) -> None:
    """Through the real refresh, whose prune keeps two generations. "A cursor
    survives one refresh; after the second it is `409 leaderboard_refreshed`."
    The middle step kills refusing any cursor whose generation is not the
    newest; the last kills falling back to the newest generation, or an empty
    page, when the cursor's generation is gone."""
    directory = Directory()
    await granted(session, directory, "ann")
    await granted(session, directory, "bob")
    await refresh_with(directory, clock=fixed_clock(at(0)))
    first = await read_page(session, now=at(0), limit=1)
    assert first.next_cursor is not None

    await refresh_with(directory, clock=fixed_clock(at(60)))
    survived = await read_page(session, now=at(60), limit=1, cursor=first.next_cursor)
    await refresh_with(directory, clock=fixed_clock(at(120)))

    assert [(row.rank, row.username) for row in survived.rows] == [(2, "bob")]
    assert survived.computed_at == at(0)
    with pytest.raises(LeaderboardRefreshed):
        await read_page(session, now=at(120), limit=1, cursor=first.next_cursor)


async def test_a_cursor_is_decoded_before_the_board_is_read(
    session: AsyncSession,
) -> None:
    """On an empty board, so the empty answer cannot be what replies. A
    malformed cursor is 400 and a well-formed one names a generation that is
    not there, which is 409 (a pruned and a never-existed generation cannot
    be told apart). Catches: returning the empty board before decoding the cursor."""
    with pytest.raises(MalformedCursor):
        await read_page(session, now=T0, cursor="nonsense")
    with pytest.raises(LeaderboardRefreshed):
        await read_page(session, now=T0, cursor=rank_cursor(uuid.uuid4(), 1))


async def test_a_cursor_past_the_end_of_a_stored_ranking_is_an_empty_page(
    session: AsyncSession,
) -> None:
    """A cursor can only name a rank this service issued, but a well-formed one
    past the end of a ranking that is stored is not a vanished generation: the
    ranking is still there, with nothing left in it. Two rows, a cursor after
    rank 5. Catches: the rank condition in the WHERE clause, where it drops the
    generation row along with the entries and the read raises
    `LeaderboardRefreshed`; an exception for a rank beyond the last."""
    generation_id = uuid.uuid4()
    await store_generation(
        computed_at=T0,
        rows=[("ann", Decimal("2.0000")), ("bob", Decimal("1.0000"))],
        generation_id=generation_id,
    )
    cursor = encode_rank_cursor(generation_id, 5)

    page = await read_page(session, now=T0, cursor=cursor)
    stale_page = await read_page(
        session, now=T0 + timedelta(seconds=121), cursor=cursor
    )

    assert page.rows == []
    assert page.next_cursor is None
    assert page.computed_at == T0
    assert page.stale is False
    assert stale_page.stale is True
    assert page.ranked_users == 2


async def test_a_page_never_reaches_into_the_other_generation(
    session: AsyncSession,
) -> None:
    """The previous generation holds the same two users and a third. A read
    without the generation filter doubles the rows, or takes the old rank 3
    as its look-ahead row and offers a cursor. Catches: a missing
    `generation_id` filter on the entries."""
    first_user, second_user = uuid.uuid4(), uuid.uuid4()
    await store_generation(
        computed_at=T0,
        rows=[
            ("first", Decimal("30.0000")),
            ("second", Decimal("20.0000")),
            ("third", Decimal("10.0000")),
        ],
        user_ids=[first_user, second_user, uuid.uuid4()],
    )
    await store_generation(
        computed_at=at(60),
        rows=[("first", Decimal("31.0000")), ("second", Decimal("21.0000"))],
        user_ids=[first_user, second_user],
    )

    page = await read_page(session, now=at(60), limit=2)
    whole = await read_page(session, now=at(60), limit=50)

    expected = [(1, "first", Decimal("31.0000")), (2, "second", Decimal("21.0000"))]
    assert _listed(page) == expected
    assert page.next_cursor is None
    assert page.ranked_users == 2
    assert _listed(whole) == expected
