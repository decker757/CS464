"""The admin market overview, through the service layer. [2.1] #5, #210.

Every published market plus the caller's own drafts and submissions, with
per-status counts beside the list. Status, filter and counts all derive from
the clock (ADR 0011, as amended by #5). #210 pages the list by keyset, settled
markets last, and reads the counts in the same REPEATABLE READ snapshot
("The admin overview pages by keyset, settled markets last, and reads its
counts in the same REPEATABLE READ snapshot"). The clock is always injected:
`now` sits ten days ahead of the real clock, so any half that reads its own
clock disagrees with it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import ColumnElement, select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session_factory
from core.errors import MalformedCursor
from model.entities import Market, MarketOverview, MarketStatus
from service import browsing, market_service
from service.audit import Actor
from unit_test.conftest import (
    actor,
    approved_market,
    closed_market,
    draft_request,
    proposed_market,
    published_market_closing_at,
)

# More than any test here creates, so a test about order or counts reads one page.
_ONE_PAGE = 1000

# A walk longer than this means a cursor that never advances.
_MOST_PAGES = 50


# --- the one place the assumed signature is written ------------------------
async def _page(
    session: AsyncSession,
    caller: Actor,
    *,
    limit: int = _ONE_PAGE,
    status: MarketStatus | None = None,
    now: datetime,
    cursor: str | None = None,
) -> MarketOverview:
    """One overview page as `caller` sees it at `now`.

    Commits first. A request's session arrives with no transaction open, and
    `overview` refuses one that has, where REPEATABLE READ could no longer be
    set; the fixtures here leave one open after their last read.
    """
    await session.commit()
    return await browsing.overview(
        session,
        caller_id=caller.id,
        limit=limit,
        status=status,
        now=now,
        cursor=cursor,
    )


async def _overview(
    session: AsyncSession,
    caller: Actor,
    *,
    status: MarketStatus | None = None,
    now: datetime,
) -> tuple[list, dict[MarketStatus, int]]:
    """The overview's rows and counts on one page, as `caller` sees them at `now`."""
    page = await _page(session, caller, status=status, now=now)
    return page.markets, page.counts


async def _walk(
    session: AsyncSession,
    caller: Actor,
    *,
    limit: int,
    now: datetime,
    status: MarketStatus | None = None,
    cursor: str | None = None,
) -> list:
    """Every row from `cursor` on, following `next_cursor` to the end.

    Bounded, so a cursor that never advances fails the test instead of hanging it.
    """
    rows: list = []
    for _ in range(_MOST_PAGES):
        page = await _page(
            session, caller, limit=limit, status=status, now=now, cursor=cursor
        )
        rows.extend(page.markets)
        cursor = page.next_cursor
        if cursor is None:
            return rows
    pytest.fail(f"next_cursor was still set after {_MOST_PAGES} pages")


# --- helpers --------------------------------------------------------------
def _injected_now() -> datetime:
    """The test's clock: far enough ahead that the real clock disagrees with it."""
    return datetime.now(UTC) + timedelta(days=10)


async def _open_until(
    session: AsyncSession, creator: Actor, closes: datetime
) -> uuid.UUID:
    return (await published_market_closing_at(session, creator, closes)).id


async def _draft(
    session: AsyncSession, creator: Actor, **overrides: object
) -> uuid.UUID:
    market, _, _ = await market_service.save(
        session, creator, draft_request(**overrides)
    )
    return market.id


def _ids(rows: list) -> list[uuid.UUID]:
    return [row.id for row in rows]


@dataclass(frozen=True)
class _Population:
    """One market in every status the caller can see, and two the caller cannot."""

    caller: Actor
    now: datetime
    ids: dict[str, uuid.UUID]


@pytest.fixture
async def population(session: AsyncSession) -> _Population:
    caller = actor()
    other = actor(username="michelle_t")
    now = _injected_now()

    # Ids are read as each market is created: `closed_market` expires the
    # session, and a later `.id` would lazy-load outside the greenlet.
    ids = {
        "my_draft": await _draft(session, caller),
        "my_submitted": await _draft(session, caller, status="submitted"),
        "other_draft": await _draft(session, other),
        "other_submitted": await _draft(session, other, status="submitted"),
        "other_open": await _open_until(session, other, now + timedelta(days=2)),
        "open": await _open_until(session, caller, now + timedelta(days=1)),
        "past_close": await _open_until(session, caller, now - timedelta(hours=1)),
        "swept_closed": (await closed_market(session, caller)).id,
        "pending": (await proposed_market(session, caller)).id,
        "approved": (await approved_market(session, caller)).id,
    }
    return _Population(caller=caller, now=now, ids=ids)


# --- the clock closes a market, not the column ------------------------------
async def test_a_market_past_its_close_time_is_listed_filtered_and_counted_as_closed(
    session: AsyncSession,
) -> None:
    """[2.1] #5: "listed, filtered and counted as closed, even while its stored
    status still reads open". ADR 0011 as amended by #5."""
    caller = actor()
    now = _injected_now()
    still_open = await _open_until(session, caller, now + timedelta(days=1))
    past_close = await _open_until(session, caller, now - timedelta(hours=1))

    rows, counts = await _overview(session, caller, now=now)
    as_open, _ = await _overview(session, caller, status=MarketStatus.OPEN, now=now)
    as_closed, _ = await _overview(session, caller, status=MarketStatus.CLOSED, now=now)

    listed = {row.id: row for row in rows}
    assert listed[past_close].status == MarketStatus.CLOSED
    assert _ids(as_open) == [still_open]
    assert _ids(as_closed) == [past_close]
    assert counts[MarketStatus.OPEN] == 1
    assert counts[MarketStatus.CLOSED] == 1


# --- counts ----------------------------------------------------------------
async def test_on_an_empty_database_every_status_counts_zero(
    session: AsyncSession,
) -> None:
    """[2.1] #5: "Counts are shown for every status, zero when none".

    Exact equality, so a count seeded from the trader-visible statuses
    alone, as `count_by_status` is with no caller, fails for lacking draft
    and submitted.
    """
    rows, counts = await _overview(session, actor(), now=_injected_now())

    assert rows == []
    assert counts == {status: 0 for status in MarketStatus}


@pytest.mark.parametrize("status", list(MarketStatus))
async def test_each_status_count_equals_the_length_of_its_filtered_list(
    session: AsyncSession, population: _Population, status: MarketStatus
) -> None:
    """[2.1] #5: "each status's count equals the number of markets that
    status's filter returns". The population puts at least one market in
    every status, so no case passes as 0 == 0."""
    filtered, counts = await _overview(
        session, population.caller, status=status, now=population.now
    )

    assert counts[status] >= 1
    assert counts[status] == len(filtered)


async def test_the_counts_sum_to_the_length_of_the_unfiltered_list(
    session: AsyncSession, population: _Population
) -> None:
    """[2.1] #5: "the counts sum to the number of markets the caller can see"."""
    rows, counts = await _overview(session, population.caller, now=population.now)

    assert sum(counts.values()) == len(rows)


async def test_another_administrators_drafts_and_submissions_are_neither_listed_nor_counted(
    session: AsyncSession, population: _Population
) -> None:
    """[2.1] #5's scoping, and ADR 0003's draft invisibility. The other
    administrator's published market is listed: published is everyone's."""
    ids = population.ids

    rows, counts = await _overview(session, population.caller, now=population.now)
    listed = set(_ids(rows))

    assert ids["my_draft"] in listed
    assert ids["my_submitted"] in listed
    assert ids["other_open"] in listed
    assert ids["other_draft"] not in listed
    assert ids["other_submitted"] not in listed
    assert counts[MarketStatus.DRAFT] == 1
    assert counts[MarketStatus.SUBMITTED] == 1


@pytest.mark.parametrize("status", list(MarketStatus))
async def test_the_counts_are_the_same_whichever_status_filter_is_applied(
    session: AsyncSession, population: _Population, status: MarketStatus
) -> None:
    """[2.1] #5: counts "whichever status filter is applied". "Counts cover
    every status the caller can see, zero-filled, and ignore the status
    filter"."""
    _, unfiltered = await _overview(session, population.caller, now=population.now)
    _, filtered = await _overview(
        session, population.caller, status=status, now=population.now
    )

    assert filtered == unfiltered


# --- order -----------------------------------------------------------------
async def test_the_unfiltered_view_orders_by_close_time_with_none_last(
    session: AsyncSession,
) -> None:
    """[2.1] #5's order, and "The admin overview orders by soonest close,
    missing close times last, then id".

    Created out of order. The two closed markets lead because they closed
    earliest, which the trader browse's still-trading-first order would not do.
    """
    caller = actor()
    now = _injected_now()
    no_close_time = await _draft(session, caller, close_time=None, resolution_time=None)
    in_three_days = await _open_until(session, caller, now + timedelta(days=3))
    past_close = await _open_until(session, caller, now - timedelta(hours=1))
    in_one_day = await _open_until(session, caller, now + timedelta(days=1))
    swept = (await closed_market(session, caller)).id

    rows, _ = await _overview(session, caller, now=now)

    assert _ids(rows) == [swept, past_close, in_one_day, in_three_days, no_close_time]


async def test_the_open_filter_orders_by_soonest_close(session: AsyncSession) -> None:
    """[2.1] #5: the same order "with or without a status filter"."""
    caller = actor()
    now = _injected_now()
    in_five_days = await _open_until(session, caller, now + timedelta(days=5))
    in_one_day = await _open_until(session, caller, now + timedelta(days=1))
    in_three_days = await _open_until(session, caller, now + timedelta(days=3))

    rows, _ = await _overview(session, caller, status=MarketStatus.OPEN, now=now)

    assert _ids(rows) == [in_one_day, in_three_days, in_five_days]


async def test_the_closed_filter_orders_by_oldest_close_first(
    session: AsyncSession,
) -> None:
    """Oldest close first: the one waiting longest for an administrator.
    "The admin overview orders by soonest close…; #105's order is the trader
    browse's"."""
    caller = actor()
    now = _injected_now()
    an_hour_ago = await _open_until(session, caller, now - timedelta(hours=1))
    swept = (await closed_market(session, caller)).id
    two_hours_ago = await _open_until(session, caller, now - timedelta(hours=2))

    rows, _ = await _overview(session, caller, status=MarketStatus.CLOSED, now=now)

    assert _ids(rows) == [swept, two_hours_ago, an_hour_ago]


async def test_markets_sharing_a_close_time_come_back_lower_id_first(
    session: AsyncSession,
) -> None:
    """[2.1] #5: "then by id".

    Probabilistic, on purpose. Ids are random uuid4 and no fixture can choose
    one, so the expected order is whatever the ids sort to. Drop `id` from the
    ORDER BY and Postgres tends to return the tie in insertion order; with six
    markets that matches sorted order by chance about 1 time in 720, which is
    how often this test would pass over the mutation.
    """
    caller = actor()
    now = _injected_now()
    shared_close = now + timedelta(days=2)
    tied = [await _open_until(session, caller, shared_close) for _ in range(6)]

    rows, _ = await _overview(session, caller, now=now)

    assert _ids(rows) == sorted(tied)


# --- one clock -------------------------------------------------------------
async def test_a_market_closing_exactly_at_the_injected_now_is_closed_in_both_list_and_counts(
    session: AsyncSession,
) -> None:
    """One clock reaches both halves ("One clock per request, read at the
    controller"). On the boundary, `close_time > now` is false, so closed.

    The real clock is ten days earlier, so a half that reads its own clock
    calls this market open while the other calls it closed.
    """
    caller = actor()
    now = _injected_now()
    on_the_boundary = await _open_until(session, caller, now)

    rows, counts = await _overview(session, caller, now=now)

    assert _ids(rows) == [on_the_boundary]
    assert rows[0].status == MarketStatus.CLOSED
    assert counts[MarketStatus.CLOSED] == 1
    assert counts[MarketStatus.OPEN] == 0


# --- #210: one page at a time, by keyset ----------------------------------
async def _every_boundary(
    session: AsyncSession, caller: Actor, now: datetime
) -> list[uuid.UUID]:
    """Every boundary a cursor must cross, in the order the overview lists them.

    A market the sweep closed (its close time is the real clock, the earliest
    here), one past its close but unswept, one closing in a day, two sharing a
    close in two days, and two drafts with no close time. Ties come back lower
    id first, in the dated run and in the undated run alike.
    """
    swept = (await closed_market(session, caller)).id
    past_close = await _open_until(session, caller, now - timedelta(hours=1))
    in_one_day = await _open_until(session, caller, now + timedelta(days=1))
    shared_close = now + timedelta(days=2)
    tied = [await _open_until(session, caller, shared_close) for _ in range(2)]
    undated = [
        await _draft(session, caller, close_time=None, resolution_time=None)
        for _ in range(2)
    ]
    return [swept, past_close, in_one_day, *sorted(tied), *sorted(undated)]


@pytest.mark.parametrize(
    "limit",
    [1, 3, 4, 5],
    ids=[
        "every_row",
        "inside_the_undated_run",
        "inside_the_tie",
        "between_dated_and_undated",
    ],
)
async def test_pages_read_in_turn_list_every_market_once_in_the_overview_order(
    session: AsyncSession, limit: int
) -> None:
    """#210: "The order is … close_time ASC NULLS LAST, id ASC", and "a page
    boundary inside the drafts can be named". Seven markets, so a boundary
    falls inside the tie, inside the undated drafts, and exactly between the
    dated and the undated."""
    caller = actor()
    now = _injected_now()
    expected = await _every_boundary(session, caller, now)

    assert _ids(await _walk(session, caller, limit=limit, now=now)) == expected


async def test_every_page_carries_the_counts_of_every_market_not_of_the_page(
    session: AsyncSession,
) -> None:
    """#210's spec: "`counts` still covers every market the admin can see …
    not the page". The same on page two as on page one: neither the limit nor
    the cursor reaches the counts."""
    caller = actor()
    now = _injected_now()
    for days in (1, 2, 3):
        await _open_until(session, caller, now + timedelta(days=days))
    await _draft(session, caller)

    first = await _page(session, caller, limit=1, now=now)
    second = await _page(session, caller, limit=1, now=now, cursor=first.next_cursor)

    for page in (first, second):
        assert len(page.markets) == 1
        assert page.counts[MarketStatus.OPEN] == 3
        assert page.counts[MarketStatus.DRAFT] == 1


async def test_a_status_filter_fills_each_page_with_its_own_markets(
    session: AsyncSession,
) -> None:
    """#210: the filter runs in the query, before the limit. Filtered after it,
    as the unpaged overview did in Python, page one here would be the closed
    market, dropped, and nothing."""
    caller = actor()
    now = _injected_now()
    await _open_until(session, caller, now - timedelta(hours=1))
    in_one_day = await _open_until(session, caller, now + timedelta(days=1))
    in_two_days = await _open_until(session, caller, now + timedelta(days=2))

    first = await _page(session, caller, limit=1, status=MarketStatus.OPEN, now=now)
    rest = await _walk(
        session,
        caller,
        limit=1,
        status=MarketStatus.OPEN,
        now=now,
        cursor=first.next_cursor,
    )

    assert _ids(first.markets) == [in_one_day]
    assert _ids(rest) == [in_two_days]


async def test_a_full_last_page_offers_no_cursor(session: AsyncSession) -> None:
    """#210: "`next_cursor` null on the last page". Two markets at `limit=2` is
    the case a `len(rows) == limit` check gets wrong."""
    caller = actor()
    now = _injected_now()
    await _open_until(session, caller, now + timedelta(days=1))
    await _open_until(session, caller, now + timedelta(days=2))

    page = await _page(session, caller, limit=2, now=now)

    assert len(page.markets) == 2
    assert page.next_cursor is None


async def test_a_filter_that_matches_nothing_is_an_empty_page_with_no_cursor(
    session: AsyncSession,
) -> None:
    """#210: "empty result is not an error", and there is nothing to continue from."""
    caller = actor()
    now = _injected_now()
    await _open_until(session, caller, now + timedelta(days=1))

    page = await _page(session, caller, limit=2, status=MarketStatus.APPROVED, now=now)

    assert page.markets == []
    assert page.next_cursor is None


async def test_a_cursor_this_service_did_not_issue_is_refused(
    session: AsyncSession,
) -> None:
    """#210: "malformed cursor is 400 malformed_cursor", raised here as the
    domain error the controller turns into that response."""
    with pytest.raises(MalformedCursor):
        await _page(
            session, actor(), limit=2, now=_injected_now(), cursor="made-this-up"
        )


async def test_a_market_published_between_two_page_reads_causes_no_skip_and_no_repeat(
    session: AsyncSession,
) -> None:
    """#210: keyset, not OFFSET. A market added ahead of the cursor is not shown
    and shifts nothing; one added behind it is shown in its place."""
    caller = actor()
    now = _injected_now()
    first = await _open_until(session, caller, now + timedelta(days=1))
    second = await _open_until(session, caller, now + timedelta(days=2))
    third = await _open_until(session, caller, now + timedelta(days=3))
    fourth = await _open_until(session, caller, now + timedelta(days=4))

    page_one = await _page(session, caller, limit=2, now=now)
    await _open_until(session, caller, now + timedelta(hours=12))
    behind_the_cursor = await _open_until(
        session, caller, now + timedelta(days=2, hours=12)
    )
    rest = await _walk(session, caller, limit=2, now=now, cursor=page_one.next_cursor)

    assert _ids(page_one.markets) == [first, second]
    assert _ids(rest) == [behind_the_cursor, third, fourth]


async def test_a_market_whose_close_passes_between_two_page_reads_keeps_its_place(
    session: AsyncSession,
) -> None:
    """#210's spec: "No frozen clock: the order does not depend on 'still
    trading', so a market closing between page reads keeps its position".
    Page two is read on a later clock, at which the first two have closed: no
    market is repeated, and the later page's statuses and counts read its own
    clock (D-025)."""
    caller = actor()
    now = _injected_now()
    first = await _open_until(session, caller, now + timedelta(days=1))
    second = await _open_until(session, caller, now + timedelta(days=2))
    third = await _open_until(session, caller, now + timedelta(days=3))

    page_one = await _page(session, caller, limit=2, now=now)
    later = now + timedelta(days=2, hours=12)
    page_two = await _page(session, caller, limit=2, now=later, cursor=page_one.next_cursor)

    assert _ids(page_one.markets) == [first, second]
    assert _ids(page_two.markets) == [third]
    assert page_two.next_cursor is None
    assert page_two.counts[MarketStatus.CLOSED] == 2
    assert page_two.counts[MarketStatus.OPEN] == 1


# --- #210: settled markets sort last --------------------------------------
async def test_settled_markets_sort_last_and_a_cursor_crosses_into_their_group(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#210: "settled markets last … The cursor carries the group from day one".

    No market can be settled until [3.4] #12 adds the status (#227), so this
    swaps the one predicate the group reads for "is approved". The approved
    markets closed first (the real clock), so without the group they would
    lead. Fails if the group is missing from the ORDER BY, from the keyset, or
    from the cursor (a cursor that always says "not settled" repeats the first
    approved market until the walk gives up). When #227 lands, build two real
    settled markets here and drop the swap.
    """
    caller = actor()
    now = _injected_now()
    first_approved = (await approved_market(session, caller)).id
    second_approved = (await approved_market(session, caller)).id
    still_open = await _open_until(session, caller, now + timedelta(days=1))

    def approved_stands_in_for_settled() -> ColumnElement[bool]:
        return Market.status == MarketStatus.APPROVED

    monkeypatch.setattr(browsing, "_is_settled", approved_stands_in_for_settled)

    walked = await _walk(session, caller, limit=1, now=now)

    assert _ids(walked) == [still_open, first_approved, second_approved]


# --- #210: the page and the counts read one snapshot ----------------------
async def test_the_page_and_the_counts_read_one_snapshot(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#210: "The per-status counts get their own statement, and the read runs
    at REPEATABLE READ so the counts and the list still come from one snapshot".

    Another session publishes and commits a market between the overview's two
    statements, whichever runs first. At READ COMMITTED the second statement
    sees it, and the list and the counts disagree. Fails with the isolation
    level removed from `overview`.
    """
    caller = actor()
    now = _injected_now()
    listed = await _open_until(session, caller, now + timedelta(days=1))
    await session.commit()

    real_execute = session.execute
    published_elsewhere = False

    async def execute_then_publish_elsewhere(*args: object, **kwargs: object):
        nonlocal published_elsewhere
        result = await real_execute(*args, **kwargs)
        if not published_elsewhere:
            published_elsewhere = True
            async with get_session_factory()() as other:
                await _open_until(other, caller, now + timedelta(days=2))
        return result

    monkeypatch.setattr(session, "execute", execute_then_publish_elsewhere)

    page = await browsing.overview(session, caller_id=caller.id, limit=50, now=now)

    assert published_elsewhere
    assert _ids(page.markets) == [listed]
    assert page.counts[MarketStatus.OPEN] == 1


async def test_the_overview_refuses_a_session_with_a_transaction_already_open(
    session: AsyncSession,
) -> None:
    """REPEATABLE READ can only be set before a transaction's first statement.
    Inside one already open, `overview` raises rather than reading at READ
    COMMITTED with only a warning, which would split the counts from the list
    with nothing failing. A route's session arrives with none open."""
    await session.execute(select(1))

    with pytest.raises(InvalidRequestError):
        await browsing.overview(
            session, caller_id=uuid.uuid4(), limit=1, now=_injected_now()
        )
