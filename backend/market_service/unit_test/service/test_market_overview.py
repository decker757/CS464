"""The admin market overview, through the service layer. [2.1] #5.

Every published market plus the caller's own drafts and submissions, with
per-status counts beside the list. Status, filter and counts all derive from
the clock (ADR 0011, as amended by #5), and list and counts share one clock
and one statement ("The overview's list and counts share one clock and one
statement"). The clock is always injected: `now` sits ten days ahead of the
real clock, so any half that reads its own clock disagrees with it.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from model.entities import MarketStatus
from service import browsing, market_service
from service.audit import Actor
from unit_test.conftest import (
    actor,
    approved_market,
    closed_market,
    draft_request,
    proposed_market,
    published_market_closing_at,
    recorded_statements,
)

# A statement that reads the markets table, and not `market_outcomes`.
_READS_MARKETS = re.compile(r"\bmarkets\b")


# --- the one place the assumed signature is written ------------------------
async def _overview(
    session: AsyncSession,
    caller: Actor,
    *,
    status: MarketStatus | None = None,
    now: datetime,
) -> tuple[list, dict[MarketStatus, int]]:
    """The overview's rows and counts, as `caller` sees them at `now`."""
    result = await browsing.overview(
        session, caller_id=caller.id, status=status, now=now
    )
    return result.markets, result.counts


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
    alone, as `count_by_status` is, fails for lacking draft and submitted.
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


# --- one statement, one clock ----------------------------------------------
async def test_the_list_and_the_counts_come_from_one_statement(
    session: AsyncSession, population: _Population
) -> None:
    """[2.1] #5: "The counts and the list describe the same instant".

    Under READ COMMITTED each statement takes its own snapshot, so a publish
    committing between two reads splits the counts from the list. Fails when
    the counts are read in a separate statement. "The overview's list and
    counts share one clock and one statement".
    """
    with recorded_statements() as statements:
        await _overview(session, population.caller, now=population.now)

    market_reads = [s for s in statements if _READS_MARKETS.search(s.sql)]
    assert len(market_reads) == 1, market_reads


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
