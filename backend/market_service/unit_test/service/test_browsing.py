"""The trader-facing read of a market, through the service layer. [BE][X] #62.

Browse [X-1] #34, search and filter [X-2] #35, detail [X-3] #36, and [2.1]
#5's counts. The tests that matter most put a market in ADR 0011's gap: past
its close time, not yet swept. Response shapes are in the controller and model
suites; prices are the ledger's (ADR 0005).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import MarketNotFound
from model.entities import (
    Market,
    MarketCard,
    MarketOutcome,
    MarketPage,
    MarketStatus,
)
from core.database import get_session_factory
from service import browsing, market_service
from unit_test.conftest import approved_market as _approved_market
from unit_test.conftest import (
    actor,
    close_request,
    closed_market,
    draft_request,
    overdue_market,
    proposed_market,
    published_market,
    published_market_closing_at,
    recorded_statements,
)


def _closing_says_open(market: Market) -> bool:
    """The trade path's own predicate, called rather than restated."""
    from service import closing  # noqa: PLC0415

    return closing.is_open_for_trading(market)


# --- helpers --------------------------------------------------------------
async def _open_market(
    session: AsyncSession,
    closes_in: timedelta = timedelta(days=30),
    **overrides: object,
) -> Market:
    """A published market, tradeable, closing `closes_in` from now."""
    now = datetime.now(UTC)
    return await published_market(
        session,
        actor(),
        close_time=now + closes_in,
        resolution_time=now + closes_in + timedelta(days=15),
        **overrides,
    )


def _ids(cards: list[object]) -> list[uuid.UUID]:
    """The ids of `browse`'s cards, in order."""
    return [card.id for card in cards]


# More than any test creates, so a test about order or filters reads one page.
_ONE_PAGE = 1000

# A walk longer than this means a cursor that never advances.
_MOST_PAGES = 50


async def _browse(session: AsyncSession, **filters: object) -> list[MarketCard]:
    """`browse`'s markets, all on one page."""
    page = await browsing.browse(session, limit=_ONE_PAGE, **filters)
    return page.markets


async def _walk(
    session: AsyncSession,
    *,
    limit: int,
    cursor: str | None = None,
    **filters: object,
) -> list[MarketCard]:
    """Every card `browse` returns from `cursor` on, following `next_cursor` to the end.

    Bounded, so a cursor that never advances fails the test instead of hanging it.
    """
    cards: list[MarketCard] = []
    for _ in range(_MOST_PAGES):
        page = await browsing.browse(session, limit=limit, cursor=cursor, **filters)
        cards.extend(page.markets)
        cursor = page.next_cursor
        if cursor is None:
            return cards
    pytest.fail(f"next_cursor was still set after {_MOST_PAGES} pages")


def _outcomes_named(*labels: str) -> list[dict[str, str]]:
    """A market's outcomes, as the create form sends them, in this order."""
    return [{"label": label} for label in labels]


def _labels(card: MarketCard) -> list[str]:
    """A card's outcome labels, in the order the card carries them."""
    return [outcome.label for outcome in card.outcomes]


async def _move_outcome(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    from_position: int,
    to_position: int,
) -> None:
    """Give one outcome a new position, which also rewrites its row."""
    await session.execute(
        update(MarketOutcome)
        .where(
            MarketOutcome.market_id == market_id,
            MarketOutcome.position == from_position,
        )
        .values(position=to_position)
    )


async def _outcome_ids(session: AsyncSession, market_id: uuid.UUID) -> list[uuid.UUID]:
    """A market's outcome ids as stored, in position order."""
    stmt = (
        select(MarketOutcome.id)
        .where(MarketOutcome.market_id == market_id)
        .order_by(MarketOutcome.position)
    )
    return list((await session.scalars(stmt)).all())


# --- [X-1] #34: browse ----------------------------------------------------
async def test_the_default_view_shows_open_markets_ordered_by_soonest_close(
    session: AsyncSession,
) -> None:
    """[X-1] #34's default view. Created out of order, so insertion order fails."""
    middle = await _open_market(session, timedelta(days=10))
    soonest = await _open_market(session, timedelta(days=1))
    latest = await _open_market(session, timedelta(days=90))

    listed = await _browse(session)

    assert _ids(listed) == [soonest.id, middle.id, latest.id]


async def test_open_closed_and_pending_markets_are_distinguishable(
    session: AsyncSession,
) -> None:
    """[X-1] #34: each status a trader can reach is a distinct value."""
    # Ids are read as each market is created: the fixtures expire the session,
    # and a later `.id` would lazy-load outside the greenlet.
    open_id = (await _open_market(session)).id
    closed_id = (await closed_market(session, actor())).id
    pending_id = (await proposed_market(session, actor())).id
    approved_id = (await _approved_market(session, actor())).id

    listed = {card.id: card for card in await _browse(session)}

    assert listed[open_id].status is MarketStatus.OPEN
    assert listed[closed_id].status is MarketStatus.CLOSED
    assert listed[pending_id].status is MarketStatus.PENDING_RESOLUTION
    assert listed[approved_id].status is MarketStatus.APPROVED


async def test_a_view_that_matches_nothing_is_an_empty_list_not_an_error(
    session: AsyncSession,
) -> None:
    """[X-1] #34's empty state needs `[]`, not an error."""
    await _open_market(session)

    assert await _browse(session, query="nothing matches this") == []


# --- #214: each card names its outcomes -----------------------------------
async def test_a_card_carries_every_outcome_with_its_label_in_position_order(
    session: AsyncSession,
) -> None:
    """#214: a three-way market returns all three, in position order.

    Written alphabetically, then the first and last swap positions, so neither
    the order the rows were written in nor the labels' order is the answer.
    """
    market_id = (
        await _open_market(
            session, outcomes=_outcomes_named("Bucks", "Celtics", "Lakers")
        )
    ).id
    await _move_outcome(session, market_id, from_position=0, to_position=99)
    await _move_outcome(session, market_id, from_position=2, to_position=0)
    await _move_outcome(session, market_id, from_position=99, to_position=2)
    await session.commit()

    [card] = await _browse(session)

    assert [(outcome.position, outcome.label) for outcome in card.outcomes] == [
        (0, "Lakers"),
        (1, "Celtics"),
        (2, "Bucks"),
    ]


async def test_each_card_carries_only_its_own_outcomes(session: AsyncSession) -> None:
    """#214: one query serves the whole page, so its rows must be split by market."""
    basketball_id = (
        await _open_market(
            session, timedelta(days=1), outcomes=_outcomes_named("Lakers", "Celtics")
        )
    ).id
    weather_id = (
        await _open_market(
            session, timedelta(days=2), outcomes=_outcomes_named("Sun", "Rain", "Haze")
        )
    ).id

    cards = {card.id: card for card in await _browse(session)}

    assert _labels(cards[basketball_id]) == ["Lakers", "Celtics"]
    assert _labels(cards[weather_id]) == ["Sun", "Rain", "Haze"]
    assert [outcome.id for outcome in cards[basketball_id].outcomes] == (
        await _outcome_ids(session, basketball_id)
    )
    assert [outcome.id for outcome in cards[weather_id].outcomes] == (
        await _outcome_ids(session, weather_id)
    )


async def test_a_browse_reads_every_markets_outcomes_in_one_query(
    session: AsyncSession,
) -> None:
    """#214: "one extra query for the whole page", not one per market.

    Three markets, so a query per market would be four statements.
    """
    for closes_in_days in (1, 2, 3):
        await _open_market(session, timedelta(days=closes_in_days))

    with recorded_statements() as statements:
        cards = await _browse(session)

    assert len(cards) == 3
    assert len(statements) == 2, statements


async def test_an_empty_browse_does_not_query_for_outcomes(
    session: AsyncSession,
) -> None:
    """#214: with no markets on the page there is nothing to fetch outcomes for."""
    with recorded_statements() as statements:
        cards = await _browse(session)

    assert cards == []
    assert len(statements) == 1, statements


# --- [X-2] #35: search and filter -----------------------------------------
async def test_markets_can_be_searched_by_words_from_the_question(
    session: AsyncSession,
) -> None:
    """[X-2] #35: a containment search, and one that does not ignore its argument."""
    inflation = await _open_market(
        session,
        question="Will Singapore core inflation be below 2% in December 2026?",
    )
    await _open_market(
        session,
        question="Will the MRT Cross Island Line open before June 2027?",
    )

    assert _ids(await _browse(session, query="inflation")) == [inflation.id]


async def test_the_question_search_ignores_case(session: AsyncSession) -> None:
    """[X-2] #35: nobody types a market's capitalisation."""
    market = await _open_market(
        session,
        question="Will Singapore core inflation be below 2% in December 2026?",
    )

    assert _ids(await _browse(session, query="INFLATION")) == [market.id]


async def test_markets_can_be_filtered_by_status(session: AsyncSession) -> None:
    """[X-2] #35: "Users can filter markets by status"."""
    # Ids held as they are created; `closed_market` expires the session.
    open_id = (await _open_market(session)).id
    closed_id = (await closed_market(session, actor())).id

    assert _ids(await _browse(session, status=MarketStatus.CLOSED)) == [
        closed_id
    ]
    assert _ids(await _browse(session, status=MarketStatus.OPEN)) == [
        open_id
    ]


async def test_search_and_a_status_filter_can_be_used_together(
    session: AsyncSession,
) -> None:
    """[X-2] #35: each decoy matches one half only, so dropping either half fails."""
    question = "Will Singapore core inflation be below 2% in December 2026?"
    # Held as it is created; the second `closed_market` expires the session.
    wanted_id = (await closed_market(session, actor(), question=question)).id
    await _open_market(session, question=question)
    await closed_market(
        session, actor(), question="Will the MRT Cross Island Line open in 2027?"
    )

    listed = await _browse(
        session, status=MarketStatus.CLOSED, query="inflation"
    )

    assert _ids(listed) == [wanted_id]


async def test_the_question_search_treats_like_wildcards_as_text(
    session: AsyncSession,
) -> None:
    """[X-2] #35: `%`, `_` and `\\` are text, not pattern. Unescaped, each gives a
    plausible but bigger list, so the wrong answer fails loudly."""
    percent = await _open_market(
        session, question="Will core inflation be below 2% in December 2026?"
    )
    # Contains a 2, does not contain "2%". This is the row the unescaped
    # pattern `%2%%` wrongly picks up, because it collapses to `%2%`.
    await _open_market(
        session, question="Will the MRT Cross Island Line open before June 2027?"
    )
    underscored = await _open_market(
        session, question="Will the CPI_SG series be published before March 2027?"
    )
    backslashed = await _open_market(
        session, question="Will the C:\\Temp retention policy be published in 2027?"
    )

    # `%` is any run of characters. Unescaped this matches every question with
    # a 2 in it, which is all four.
    assert _ids(await _browse(session, query="2%")) == [percent.id]

    # `_` is exactly one character. Unescaped this matches every question that
    # is not empty, which is again all four.
    assert _ids(await _browse(session, query="_")) == [underscored.id]

    # The escape character itself. Left as-is it becomes a dangling escape in
    # the pattern — `\T` is not a valid sequence — so this one does not return
    # the wrong rows, it raises.
    assert _ids(await _browse(session, query="C:\\Temp")) == [
        backslashed.id
    ]


# --- [X-3] #36: one market's detail ---------------------------------------
async def test_the_detail_read_is_not_scoped_to_the_creator(
    session: AsyncSession,
) -> None:
    """[X-3] #36: a published market belongs to every trader; no actor at all."""
    market = await published_market(session, actor())

    assert (await browsing.get_published(session, market.id)).id == market.id


async def test_the_detail_read_refuses_a_draft(session: AsyncSession) -> None:
    """[1.1] #1: #62 must not be the hole in draft invisibility."""
    market, _, _ = await market_service.save(session, actor(), draft_request())

    with pytest.raises(MarketNotFound):
        await browsing.get_published(session, market.id)


async def test_the_detail_read_refuses_a_submitted_market(
    session: AsyncSession,
) -> None:
    """[1.3] #3: publication, not submission, makes a market visible. The status
    a filter written as "not a draft" would let through."""
    market, _, _ = await market_service.save(
        session, actor(), draft_request(status="submitted")
    )

    with pytest.raises(MarketNotFound):
        await browsing.get_published(session, market.id)


async def test_an_unknown_market_is_refused(session: AsyncSession) -> None:
    """The same error for a market that never existed, so the two are
    indistinguishable from outside."""
    with pytest.raises(MarketNotFound):
        await browsing.get_published(session, uuid.uuid4())


async def test_an_approved_market_carries_its_winning_outcome(
    session: AsyncSession,
) -> None:
    """[X-3] #36: the winner, by id into the market's own `outcomes`."""
    market = await _approved_market(session, actor())

    detail = await browsing.get_published(session, market.id)

    assert detail.proposed_outcome_id is not None
    assert detail.proposed_outcome_id in {outcome.id for outcome in detail.outcomes}


# --- neither a draft nor a submission is ever public ----------------------
async def test_a_draft_is_never_listed(session: AsyncSession) -> None:
    """[1.1] #1, against the list, the easier read to get wrong."""
    await market_service.save(session, actor(), draft_request())
    visible = await _open_market(session)

    assert _ids(await _browse(session)) == [visible.id]


async def test_a_submitted_market_is_never_listed(session: AsyncSession) -> None:
    """[1.3] #3. Submitted is complete, correct and still not public."""
    await market_service.save(session, actor(), draft_request(status="submitted"))
    visible = await _open_market(session)

    assert _ids(await _browse(session)) == [visible.id]


async def test_a_draft_is_not_a_status_a_trader_may_filter_on(
    session: AsyncSession,
) -> None:
    """[X-2] #35's filter must apply beside the visibility rule, not instead of it."""
    await market_service.save(session, actor(), draft_request())

    assert await _browse(session, status=MarketStatus.DRAFT) == []


# --- ADR 0011: the clock closes a market, not the sweeper -----------------
async def test_a_market_past_its_close_time_is_not_listed_as_open(
    session: AsyncSession,
) -> None:
    """ADR 0011: filtered on the column, a stopped market would list as tradeable."""
    stopped = await overdue_market(session, actor())
    still_open = await _open_market(session)

    listed = await _browse(session, status=MarketStatus.OPEN)

    assert _ids(listed) == [still_open.id]
    assert stopped.id not in _ids(listed)


async def test_a_market_past_its_close_time_reads_as_closed_before_the_sweep(
    session: AsyncSession,
) -> None:
    """The other side: not merely absent from `open`, but present under `closed`,
    or it vanishes from every filter. ADR 0011's amendment, D-022."""
    stopped = await overdue_market(session, actor())
    await _open_market(session)

    listed = await _browse(session, status=MarketStatus.CLOSED)

    assert _ids(listed) == [stopped.id]


async def test_the_injected_clock_reaches_the_sql_filter_not_only_the_derivation(
    session: AsyncSession,
) -> None:
    """D-025: the SQL filter and the card's label read the same handed-in clock.

    Pushed forward past an open market; a filter that asked Postgres for its
    own clock would invert both assertions.
    """
    market = await _open_market(session, timedelta(hours=1))
    later = datetime.now(UTC) + timedelta(hours=2)

    as_open = await _browse(session, status=MarketStatus.OPEN, now=later)
    as_closed = await _browse(session, status=MarketStatus.CLOSED, now=later)

    assert market.id not in _ids(as_open), (
        "the SQL filter ignored the clock it was handed and asked Postgres "
        "for its own"
    )
    assert _ids(as_closed) == [market.id]
    assert as_closed[0].status is MarketStatus.CLOSED


async def test_the_counts_read_the_clock_they_are_handed(
    session: AsyncSession,
) -> None:
    """The same guarantee for [2.1] #5's counts, which derive separately. D-025."""
    await _open_market(session, timedelta(hours=1))
    later = datetime.now(UTC) + timedelta(hours=2)

    counts = await browsing.count_by_status(session, now=later)

    assert counts[MarketStatus.OPEN] == 0
    assert counts[MarketStatus.CLOSED] == 1


async def test_the_default_view_sorts_a_market_past_its_close_time_behind_the_open_ones(
    session: AsyncSession,
) -> None:
    """The unfiltered view derives too: the stopped market stays, behind the open one."""
    stopped = await overdue_market(session, actor())
    still_open = await _open_market(session)

    listed = await _browse(session)
    ids = _ids(listed)

    assert stopped.id in ids
    assert not _closing_says_open(stopped)
    assert ids.index(still_open.id) < ids.index(stopped.id)


# --- #105: stopped markets, most recently stopped first ---------------------
async def test_stopped_markets_list_most_recently_stopped_first_whatever_their_status(
    session: AsyncSession,
) -> None:
    """#105: one rule for every status that is not trading, newest first.

    The approved market stopped longest ago and sorts last: it gets no place
    of its own. Created oldest first, so `close_time ASC` and insertion order
    both give the reverse. DECISIONS.md, "Stopped markets sort by when trading
    stopped".
    """
    creator = actor()
    # Ids held as they are created; each sweep expires the session.
    approved_id = (
        await _approved_market(session, creator, overdue_by=timedelta(days=3))
    ).id
    closed_id = (
        await closed_market(session, creator, overdue_by=timedelta(days=1))
    ).id
    pending_id = (
        await proposed_market(session, creator, overdue_by=timedelta(hours=2))
    ).id
    unswept_id = (await overdue_market(session, creator)).id

    listed = await _browse(session)

    assert _ids(listed) == [unswept_id, pending_id, closed_id, approved_id]


async def test_an_early_closed_market_sorts_by_when_it_was_closed_not_its_close_time(
    session: AsyncSession,
) -> None:
    """#105, ADR 0014: closed by hand a day ago, with 30 days left on its
    `close_time`, it sorts between markets that stopped an hour and two days ago.

    Sorting on `close_time` alone puts it first. Sorting on `closed_at` alone
    puts the two-days-ago market above it, because the sweep stamped that one's
    `closed_at` just now; so both columns are needed.
    """
    creator = actor()
    a_day_ago = datetime.now(UTC) - timedelta(days=1)
    two_days_ago_id = (
        await closed_market(session, creator, overdue_by=timedelta(days=2))
    ).id
    early_id = (await _open_market(session, timedelta(days=30))).id
    await market_service.close_early(
        session, actor(), early_id, close_request(), now=a_day_ago
    )
    an_hour_ago_id = (
        await overdue_market(session, creator, overdue_by=timedelta(hours=1))
    ).id

    listed = await _browse(session)

    assert _ids(listed) == [an_hour_ago_id, early_id, two_days_ago_id]


@pytest.mark.parametrize(
    "viewed_after_the_close", [False, True], ids=["still_trading", "stopped"]
)
async def test_markets_sharing_a_close_time_list_lower_id_first_in_either_group(
    session: AsyncSession, viewed_after_the_close: bool
) -> None:
    """#105: "the `id` tiebreaker is preserved in both groups".

    Six markets closing at one instant, viewed before it and after it.
    Probabilistic on purpose, as the overview's tie test is: drop `id` from
    the ORDER BY and Postgres tends to return the tie in insertion order,
    which matches sorted order about 1 time in 720.
    """
    shared_close = datetime.now(UTC) + timedelta(days=2)
    tied = [
        (await published_market_closing_at(session, actor(), shared_close)).id
        for _ in range(6)
    ]
    now = shared_close + timedelta(days=1 if viewed_after_the_close else -1)

    listed = await _browse(session, now=now)

    assert _ids(listed) == sorted(tied)


# --- #104: one page at a time, by keyset ----------------------------------
async def _three_trading_and_three_stopped(session: AsyncSession) -> list[uuid.UUID]:
    """Both groups, each with a tie on its sort key: the boundaries #104 must survive.

    Trading: one closing in a day, two sharing a close in two days. Stopped:
    two closed by hand at one instant an hour ago, and one the clock closed a
    day ago. Returns their ids in the order `browse` must list them, ties by
    lower id first (#105).
    """
    soonest_close = datetime.now(UTC) + timedelta(days=1)
    soonest_id = (
        await published_market_closing_at(session, actor(), soonest_close)
    ).id
    shared_close = datetime.now(UTC) + timedelta(days=2)
    tied_trading_ids = [
        (await published_market_closing_at(session, actor(), shared_close)).id
        for _ in range(2)
    ]

    an_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    tied_stopped_ids = []
    for _ in range(2):
        market_id = (await _open_market(session)).id
        await market_service.close_early(
            session, actor(), market_id, close_request(), now=an_hour_ago
        )
        tied_stopped_ids.append(market_id)
    swept_id = (
        await closed_market(session, actor(), overdue_by=timedelta(days=1))
    ).id

    return [
        soonest_id,
        *sorted(tied_trading_ids),
        *sorted(tied_stopped_ids),
        swept_id,
    ]


@pytest.mark.parametrize(
    "limit",
    [1, 2, 3, 5],
    ids=[
        "every_row",
        "inside_both_ties",
        "at_the_group_boundary",
        "inside_the_stopped_group",
    ],
)
async def test_pages_read_in_turn_list_every_market_once_in_the_browse_order(
    session: AsyncSession, limit: int
) -> None:
    """#104: "pages concatenate to the unpaged order", with a page boundary
    inside each group, inside each tie, and exactly between the two groups."""
    expected = await _three_trading_and_three_stopped(session)

    assert _ids(await _walk(session, limit=limit)) == expected


async def test_a_full_last_page_offers_no_cursor(session: AsyncSession) -> None:
    """#104: `next_cursor` is null on the last page. Two markets at `limit=2`
    is the case a `len(rows) == limit` check gets wrong."""
    await _open_market(session)
    await _open_market(session)

    page = await browsing.browse(session, limit=2)

    assert len(page.markets) == 2
    assert page.next_cursor is None


async def test_an_empty_result_is_an_empty_page_with_no_cursor(
    session: AsyncSession,
) -> None:
    """#104: "Empty is not an error", and there is nothing to continue from."""
    await _open_market(session)

    page = await browsing.browse(session, limit=2, query="nothing matches this")

    assert page == MarketPage(markets=[], next_cursor=None)


async def test_a_market_published_between_two_page_reads_causes_no_skip_and_no_repeat(
    session: AsyncSession,
) -> None:
    """#104: keyset, not OFFSET. A market added ahead of the cursor is not
    shown and shifts nothing; one added behind it is shown in its place."""
    first = (await _open_market(session, timedelta(days=1))).id
    second = (await _open_market(session, timedelta(days=2))).id
    third = (await _open_market(session, timedelta(days=3))).id
    fourth = (await _open_market(session, timedelta(days=4))).id

    page_one = await browsing.browse(session, limit=2)
    await _open_market(session, timedelta(hours=12))
    behind_the_cursor = (await _open_market(session, timedelta(days=2, hours=12))).id
    rest = await _walk(session, limit=2, cursor=page_one.next_cursor)

    assert _ids(page_one.markets) == [first, second]
    assert _ids(rest) == [behind_the_cursor, third, fourth]


async def test_a_market_whose_close_passes_between_two_page_reads_is_listed_once(
    session: AsyncSession,
) -> None:
    """#104: later pages group by the first page's instant, which the cursor
    carries. Grouped by the second read's clock instead, `first` and
    `crossing` move to the stopped group and `first` is listed again.

    Each card still shows the second read's status (D-025): `crossing` keeps
    its place among the trading markets and reads `closed`.
    """
    first = (await _open_market(session, timedelta(days=1))).id
    crossing = (await _open_market(session, timedelta(days=2))).id
    later = (await _open_market(session, timedelta(days=4))).id
    stopped = (
        await closed_market(session, actor(), overdue_by=timedelta(days=1))
    ).id
    first_read = datetime.now(UTC)

    page_one = await browsing.browse(session, limit=1, now=first_read)
    rest = await _walk(
        session,
        limit=1,
        cursor=page_one.next_cursor,
        now=first_read + timedelta(days=3),
    )

    assert _ids(page_one.markets) == [first]
    assert _ids(rest) == [crossing, later, stopped]
    assert [card.status for card in rest] == [
        MarketStatus.CLOSED,
        MarketStatus.OPEN,
        MarketStatus.CLOSED,
    ]


async def test_a_market_closed_early_between_two_page_reads_is_listed_once(
    session: AsyncSession,
) -> None:
    """#104, ADR 0014: an early close writes `status` at once, so grouping by
    `open_for_trading(as_of)` would move this market to the stopped group and
    list it again. `was_open_for_trading_at` reads only the clock columns."""
    first = (await _open_market(session, timedelta(days=1))).id
    closed_by_hand = (await _open_market(session, timedelta(days=2))).id
    later = (await _open_market(session, timedelta(days=4))).id
    first_read = datetime.now(UTC)

    page_one = await browsing.browse(session, limit=2, now=first_read)
    await market_service.close_early(
        session,
        actor(),
        closed_by_hand,
        close_request(),
        now=first_read + timedelta(minutes=1),
    )
    rest = await _walk(
        session,
        limit=2,
        cursor=page_one.next_cursor,
        now=first_read + timedelta(minutes=2),
    )

    assert _ids(page_one.markets) == [first, closed_by_hand]
    assert _ids(rest) == [later]


async def test_under_status_open_a_market_whose_close_passes_mid_browse_drops_out(
    session: AsyncSession,
) -> None:
    """#104: the filter reads the request's own clock, so the market is a
    non-match on later pages, never a `closed` card in the open list."""
    first = (await _open_market(session, timedelta(days=1))).id
    await _open_market(session, timedelta(days=2))
    later = (await _open_market(session, timedelta(days=4))).id
    first_read = datetime.now(UTC)

    page_one = await browsing.browse(
        session, limit=1, status=MarketStatus.OPEN, now=first_read
    )
    rest = await _walk(
        session,
        limit=1,
        cursor=page_one.next_cursor,
        status=MarketStatus.OPEN,
        now=first_read + timedelta(days=3),
    )

    assert _ids(page_one.markets) == [first]
    assert _ids(rest) == [later]


async def test_each_page_reads_only_its_own_markets_outcomes_in_one_query(
    session: AsyncSession,
) -> None:
    """#104, #214: one outcomes query per page, naming exactly that page's
    markets. Three markets at `limit=2`, so a query asked before the page is
    trimmed to `limit` names the third market on the first page too."""
    first = (await _open_market(session, timedelta(days=1))).id
    second = (await _open_market(session, timedelta(days=2))).id
    third = (await _open_market(session, timedelta(days=3))).id

    with recorded_statements() as statements:
        await _walk(session, limit=2)

    outcome_reads = [
        statement for statement in statements if "market_outcomes" in statement
    ]
    assert [sorted(read.parameters) for read in outcome_reads] == [
        sorted([first, second]),
        [third],
    ]


# --- [2.1] #5's counts, which #62 says to build once ----------------------
async def test_counts_are_reported_per_status(session: AsyncSession) -> None:
    """[2.1] #5: "Counts shown per status". D-020."""
    await _open_market(session)
    await _open_market(session)
    await closed_market(session, actor())
    await proposed_market(session, actor())

    counts = await browsing.count_by_status(session)

    assert counts[MarketStatus.OPEN] == 2
    assert counts[MarketStatus.CLOSED] == 1
    assert counts[MarketStatus.PENDING_RESOLUTION] == 1


async def test_the_counts_derive_from_the_clock_not_the_status_column(
    session: AsyncSession,
) -> None:
    """Counts derive like the list, or "2 open" sits above a list of one."""
    await overdue_market(session, actor())
    await _open_market(session)

    counts = await browsing.count_by_status(session)

    assert counts[MarketStatus.OPEN] == 1
    assert counts[MarketStatus.CLOSED] == 1


async def test_the_counts_exclude_what_a_trader_cannot_see(
    session: AsyncSession,
) -> None:
    """[1.1] #1: a count is over what a trader can see, not over the table."""
    creator = actor()
    await market_service.save(session, creator, draft_request())
    await market_service.save(session, creator, draft_request(status="submitted"))
    await _open_market(session)

    counts = await browsing.count_by_status(session)

    assert counts.get(MarketStatus.DRAFT, 0) == 0
    assert counts.get(MarketStatus.SUBMITTED, 0) == 0
    assert counts[MarketStatus.OPEN] == 1


# --- the list query must not poison the session -------------------------
async def test_browsing_does_not_empty_the_outcomes_of_a_later_detail_read(
    clean_database,
) -> None:
    """`browse` then `get_published` in one session must not lose the outcomes. D-027.

    A fresh session is the whole test: one that created the market already
    holds it fully loaded, so the poisoning could never show.
    """
    factory = get_session_factory()

    async with factory() as setup:
        created = await _open_market(setup)
        market_id = created.id
        expected_outcomes = len(created.outcomes)
        expected_sources = len(created.resolution_sources)

    assert expected_outcomes > 0, "the fixture must give this market outcomes"

    async with factory() as fresh:
        # Held on purpose: the identity map is weak, so dropping the list would
        # let a poisoned instance be collected and the test assert nothing.
        listed = await _browse(fresh)
        assert listed, "the browse must return the market for this to mean anything"

        detail = await browsing.get_published(fresh, market_id)

        assert len(detail.outcomes) == expected_outcomes
        assert len(detail.resolution_sources) == expected_sources


# --- where the derivation happens now -------------------------------------
async def test_the_detail_read_stamps_the_derived_status_without_dirtying_the_row(
    session: AsyncSession,
) -> None:
    """Guard: the derived status is stamped without dirtying the row. D-027.

    Assigning it to `Market.status` instead would let a later commit write it
    to the column ADR 0011 reserves for the sweep; `session.dirty` shows it.
    """
    from model.entities import TRADER_FACING_STATUS  # noqa: PLC0415

    stopped = await overdue_market(session, actor())
    market_id = stopped.id
    session.expire_all()

    market = await browsing.get_published(session, market_id)

    assert getattr(market, TRADER_FACING_STATUS) is MarketStatus.CLOSED
    assert market.status is MarketStatus.OPEN, (
        "the stored column must be left alone; it is the administrator's "
        "signal that the sweep has not run"
    )
    assert market not in session.dirty, (
        "stamping the derived status must not mark the row dirty — a later "
        "commit would write it to the column"
    )


async def test_the_browse_card_carries_the_derived_status_not_the_column(
    session: AsyncSession,
) -> None:
    """The list half: a card carries only the derived status. D-027."""
    stopped = await overdue_market(session, actor())
    stopped_id = stopped.id

    listed = {card.id: card for card in await _browse(session)}

    assert listed[stopped_id].status is MarketStatus.CLOSED
    assert not hasattr(listed[stopped_id], "raw_status")
