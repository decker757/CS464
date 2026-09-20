"""The trader-facing read of a market. [BE][X] #62.

Business rules only, driven through the service layer with no HTTP. What a
response *looks like* — the decimal strings the ledger needs, the status
codes, the query parameters — is asserted in
`unit_test/controller/test_public_market_routes.py` and
`unit_test/model/test_public_schemas.py`. A test that went through a route to
check one of the rules below would be in the wrong layer.

This file serves three stories at once, which is what #62 is:

- [X-1] #34 browse, whose backend half is a default view of open markets
  ordered by soonest close
- [X-2] #35 search and filter, whose backend half is a question search and a
  status filter that compose
- [X-3] #36 market detail, whose backend half is an unscoped read of one
  published market

and it inherits [2.1] #5's per-status counts, because #62 says to build that
query once and share it.

**The rule running underneath all of it is ADR 0011.** A market stops trading
when `close_time` passes, not when the sweeper gets around to writing CLOSED.
Every filter, every count and every projection here asks
`closing.open_for_trading()` rather than `status == OPEN`, and the tests that
matter most in this file are the ones that put a market in the gap between
those two answers.

Nothing here asserts a *price*. [X-1] and [X-3] both want live YES/NO prices,
and they are not this service's to serve: `q` lives with the ledger (ADR 0005)
and the authoritative read is the snapshot endpoint in
`docs/api/realtime-service.md`, which lands with [F-3] #43 and [T-2] #22.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import ModuleType

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import MarketNotFound
from model.entities import Market, MarketStatus
from service import market_service
from service.audit import Actor
from unit_test.conftest import (
    actor,
    approval_request,
    closed_market,
    draft_request,
    proposed_market,
    published_market,
)


def _browsing() -> ModuleType:
    """Imported inside each test rather than at module scope.

    `service/browsing.py` does not exist yet, and a top-level import of it
    would be a collection error that takes the whole file down as one red
    line. Imported here, every test below fails on its own, named after the
    criterion it is holding — which is the point of writing them first.
    """
    from service import browsing  # noqa: PLC0415

    return browsing


def _closing_says_open(market: Market) -> bool:
    """The one predicate every trade path already asks.

    Called through `service.closing` rather than reimplemented here, because a
    test that restated the rule would pass while the service disagreed with
    the trade path — which is the exact failure ADR 0011 was written to
    prevent.
    """
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


async def _stopped_but_unswept(session: AsyncSession, creator: Actor) -> Market:
    """A market whose closing time has passed, with CLOSED not yet written.

    The gap ADR 0011 is about, and the reason `closed_market` in conftest is
    not the right fixture for it: that one runs the sweep, which is the state
    *after* this one. `publish` refuses to create a market that is already due
    — it re-runs the rule that `close_time` must be in the future — so the
    column is moved afterwards, exactly as `closed_market` does before it
    sweeps.

    The status column still reads `open` when this returns. Every assertion
    made against the market it hands back is an assertion about a market that
    has stopped trading and does not say so.
    """
    market = await published_market(session, creator)
    market_id = market.id
    market.close_time = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    session.expire_all()

    market = await market_service.get(session, creator.id, market_id)
    assert market.status is MarketStatus.OPEN, "the sweep must not have run yet"
    return market


async def _approved_market(session: AsyncSession, creator: Actor) -> Market:
    """A market whose proposed outcome a second administrator has agreed with.

    APPROVED stands in for [X-3] #36's "settled" throughout this file. SETTLED
    arrives with [3.4] #12 and does not exist yet; APPROVED is the furthest a
    market gets today, and it is the state in which a winning outcome is
    decided and renderable.
    """
    market = await proposed_market(session, creator)
    return await market_service.approve_outcome(
        session, actor(), market.id, approval_request(market.proposal_id)
    )


def _ids(markets: list[Market]) -> list[uuid.UUID]:
    return [market.id for market in markets]


# --- [X-1] #34: browse ----------------------------------------------------
async def test_the_default_view_shows_open_markets_ordered_by_soonest_close(
    session: AsyncSession,
) -> None:
    """[X-1] #34: "The default view shows open markets ordered by soonest
    closing time."

    Both halves in one assertion, because they are one query. Created out of
    order on purpose: a browse that returned insertion order, or `updated_at`
    order like the administrator's own list does, would pass a test that built
    them in the order it expected back.
    """
    middle = await _open_market(session, timedelta(days=10))
    soonest = await _open_market(session, timedelta(days=1))
    latest = await _open_market(session, timedelta(days=90))

    listed = await _browsing().browse(session)

    assert _ids(listed) == [soonest.id, middle.id, latest.id]


async def test_open_closed_and_pending_markets_are_distinguishable(
    session: AsyncSession,
) -> None:
    """[X-1] #34: "Open markets are clearly distinguishable from closed,
    pending-resolution, and settled markets."

    The backend half of a presentation criterion: every published market a
    trader can reach carries the status that tells them which it is, and the
    four are distinct values rather than one tradeable flag.
    """
    open_market = await _open_market(session)
    closed = await closed_market(session, actor())
    pending = await proposed_market(session, actor())
    approved = await _approved_market(session, actor())

    listed = {market.id: market for market in await _browsing().browse(session)}

    assert listed[open_market.id].status is MarketStatus.OPEN
    assert listed[closed.id].status is MarketStatus.CLOSED
    assert listed[pending.id].status is MarketStatus.PENDING_RESOLUTION
    assert listed[approved.id].status is MarketStatus.APPROVED


async def test_a_view_that_matches_nothing_is_an_empty_list_not_an_error(
    session: AsyncSession,
) -> None:
    """[X-1] #34: "An appropriate empty state is shown when no markets match
    the selected view."

    The backend half. A frontend cannot render an empty state if the call it
    made raised — a 404 for "no results" would have the browse page show an
    error where it should show "nothing here yet", which is the ordinary state
    of this platform on its first day.
    """
    await _open_market(session)

    assert await _browsing().browse(session, query="nothing matches this") == []


# --- [X-2] #35: search and filter -----------------------------------------
async def test_markets_can_be_searched_by_words_from_the_question(
    session: AsyncSession,
) -> None:
    """[X-2] #35: "Users can search markets using words from the market
    question."

    Words, plural and in any position — so this is a containment search on the
    question and not a prefix match on the whole string. The market that must
    not come back is a real published market, so a search that ignored its
    argument entirely would fail here rather than quietly returning everything.
    """
    inflation = await _open_market(
        session,
        question="Will Singapore core inflation be below 2% in December 2026?",
    )
    await _open_market(
        session,
        question="Will the MRT Cross Island Line open before June 2027?",
    )

    assert _ids(await _browsing().browse(session, query="inflation")) == [inflation.id]


async def test_the_question_search_ignores_case(session: AsyncSession) -> None:
    """[X-2] #35, same criterion. Nobody types a market's capitalisation.

    Split from the test above rather than folded into it, because a
    case-sensitive search passes that one and fails this one, and the two
    failures mean different things to whoever is fixing it.
    """
    market = await _open_market(
        session,
        question="Will Singapore core inflation be below 2% in December 2026?",
    )

    assert _ids(await _browsing().browse(session, query="INFLATION")) == [market.id]


async def test_markets_can_be_filtered_by_status(session: AsyncSession) -> None:
    """[X-2] #35: "Users can filter markets by status"."""
    open_market = await _open_market(session)
    closed = await closed_market(session, actor())

    assert _ids(await _browsing().browse(session, status=MarketStatus.CLOSED)) == [
        closed.id
    ]
    assert _ids(await _browsing().browse(session, status=MarketStatus.OPEN)) == [
        open_market.id
    ]


async def test_search_and_a_status_filter_can_be_used_together(
    session: AsyncSession,
) -> None:
    """[X-2] #35: "Search and filters can be used together."

    Three markets, and only one satisfies both halves. Each of the other two
    satisfies exactly one, so a query that dropped either the search or the
    filter returns two rows rather than one and this fails.
    """
    question = "Will Singapore core inflation be below 2% in December 2026?"
    wanted = await closed_market(session, actor(), question=question)
    await _open_market(session, question=question)
    await closed_market(
        session, actor(), question="Will the MRT Cross Island Line open in 2027?"
    )

    listed = await _browsing().browse(
        session, status=MarketStatus.CLOSED, query="inflation"
    )

    assert _ids(listed) == [wanted.id]


async def test_the_question_search_treats_like_wildcards_as_text(
    session: AsyncSession,
) -> None:
    """[X-2] #35, and the bug that hides underneath it.

    `ILIKE` has two metacharacters, and a containment search built by
    interpolation hands both of them to whoever is typing. Every case below
    returns a plausible-looking list rather than an error, which is exactly
    why the two search tests above pass without noticing.

    A trader searching `2%` is looking for a market about a percentage, and
    the tests here are built so the wrong answer is a *bigger* result set than
    the right one — a search that silently ignored its argument would fail
    these as loudly as one that leaked the pattern language.
    """
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
    assert _ids(await _browsing().browse(session, query="2%")) == [percent.id]

    # `_` is exactly one character. Unescaped this matches every question that
    # is not empty, which is again all four.
    assert _ids(await _browsing().browse(session, query="_")) == [underscored.id]

    # The escape character itself. Left as-is it becomes a dangling escape in
    # the pattern — `\T` is not a valid sequence — so this one does not return
    # the wrong rows, it raises.
    assert _ids(await _browsing().browse(session, query="C:\\Temp")) == [
        backslashed.id
    ]


# --- [X-3] #36: one market's detail ---------------------------------------
async def test_the_detail_read_is_not_scoped_to_the_creator(
    session: AsyncSession,
) -> None:
    """[X-3] #36: a trader opens a market detail page.

    The opposite of `market_service.get`, which puts `creator_id` in the WHERE
    clause so an administrator cannot read a colleague's draft. A published
    market belongs to everybody, and this read takes no actor at all — there is
    no id it could be scoped to, because the caller is a trader who created
    nothing.
    """
    market = await published_market(session, actor())

    assert (await _browsing().get_published(session, market.id)).id == market.id


async def test_the_detail_read_refuses_a_draft(session: AsyncSession) -> None:
    """[1.1] #1: a draft is visible to nobody but its creator, and #62 must not
    be the hole in that.

    `MarketNotFound` rather than a permission error, for the reason that error
    already documents: a 403 would confirm the market exists, which is most of
    what the draft visibility rule hides. Point this read at
    `market_service.get_any` and the rule is gone with no existing test
    failing.
    """
    market, _, _ = await market_service.save(session, actor(), draft_request())

    with pytest.raises(MarketNotFound):
        await _browsing().get_published(session, market.id)


async def test_the_detail_read_refuses_a_submitted_market(
    session: AsyncSession,
) -> None:
    """[1.3] #3: publication is what puts a market in front of a trader.

    A submitted market has passed every rule and is waiting on the publish
    button, which is precisely the state in which it must not be tradeable or
    visible. Kept separate from the draft case because SUBMITTED is the status
    most likely to be let through by a filter written as "not a draft".
    """
    market, _, _ = await market_service.save(
        session, actor(), draft_request(status="submitted")
    )

    with pytest.raises(MarketNotFound):
        await _browsing().get_published(session, market.id)


async def test_an_unknown_market_is_refused(session: AsyncSession) -> None:
    """The same error for a market that never existed, so the two are
    indistinguishable from outside."""
    with pytest.raises(MarketNotFound):
        await _browsing().get_published(session, uuid.uuid4())


async def test_an_approved_market_carries_its_winning_outcome(
    session: AsyncSession,
) -> None:
    """[X-3] #36: "Settled markets display the winning outcome."

    APPROVED is as far as a market gets today; SETTLED arrives with [3.4] #12.
    The winner is named by id and the label is read from the market's own
    `outcomes`, which is what stops the two copies drifting — the same rule
    `MarketOut` already follows for the administrator's view.
    """
    market = await _approved_market(session, actor())

    detail = await _browsing().get_published(session, market.id)

    assert detail.proposed_outcome_id is not None
    assert detail.proposed_outcome_id in {outcome.id for outcome in detail.outcomes}


# --- neither a draft nor a submission is ever public ----------------------
async def test_a_draft_is_never_listed(session: AsyncSession) -> None:
    """[1.1] #1, asserted against the browse query rather than the detail read.

    The list is the easier of the two to get wrong: a detail read is written
    with one market in mind, and a filter that forgets drafts leaks every
    half-typed question three administrators have ever saved.
    """
    await market_service.save(session, actor(), draft_request())
    visible = await _open_market(session)

    assert _ids(await _browsing().browse(session)) == [visible.id]


async def test_a_submitted_market_is_never_listed(session: AsyncSession) -> None:
    """[1.3] #3. Submitted is complete, correct and still not public."""
    await market_service.save(session, actor(), draft_request(status="submitted"))
    visible = await _open_market(session)

    assert _ids(await _browsing().browse(session)) == [visible.id]


async def test_a_draft_is_not_a_status_a_trader_may_filter_on(
    session: AsyncSession,
) -> None:
    """[X-2] #35's status filter must not be the way back in.

    The list and detail reads can both be right and this still be wrong: a
    filter applied *instead of* the visibility rule rather than beside it turns
    `?status=draft` into a listing of everybody's drafts. Asked for explicitly,
    because it is the one input that makes the hidden rows the subject of the
    query.
    """
    await market_service.save(session, actor(), draft_request())

    assert await _browsing().browse(session, status=MarketStatus.DRAFT) == []


# --- ADR 0011: the clock closes a market, not the sweeper -----------------
async def test_a_market_past_its_close_time_is_not_listed_as_open(
    session: AsyncSession,
) -> None:
    """ADR 0011, and the reason `closing.open_for_trading()` exists as a WHERE
    clause at all.

    The market here has `status = 'open'` in the column and a `close_time` in
    the past, which is every market for the few seconds between the clock
    passing and the sweeper writing it down. Filtered on the column, it appears
    on the browse page as tradeable; filtered on the predicate, it does not.
    The second is the right answer, and the whole of ADR 0011 is that no sweep
    interval makes the first one safe.
    """
    stopped = await _stopped_but_unswept(session, actor())
    still_open = await _open_market(session)

    listed = await _browsing().browse(session, status=MarketStatus.OPEN)

    assert _ids(listed) == [still_open.id]
    assert stopped.id not in _ids(listed)


async def test_a_market_past_its_close_time_reads_as_closed_before_the_sweep(
    session: AsyncSession,
) -> None:
    """ADR 0011, from the other side: it is not merely absent from `open`, it
    is present under `closed`.

    A market that vanished from both filters would satisfy the test above and
    still be a bug — the trader who had it open would find it nowhere. Asked
    for as a filter rather than as a column read, because the column still says
    `open` here and will until the sweeper runs.

    **This is stricter than ADR 0011's stated consequence**, which says a
    client sees `"status": "open"` for a few seconds and leaves the frontend to
    combine the status with the close time. Deriving it here instead is that
    same record's own decision applied one reader further — "a `CASE WHEN
    status = 'open' AND close_time <= now()` restated in each of them is three
    places to get it wrong" — and it is what [X-1] #34's "open markets are
    clearly distinguishable from closed" needs in order to be true of the
    response rather than of the browser. The administrator's `MarketOut` is
    unchanged and still reports the column.
    """
    stopped = await _stopped_but_unswept(session, actor())
    await _open_market(session)

    listed = await _browsing().browse(session, status=MarketStatus.CLOSED)

    assert _ids(listed) == [stopped.id]


async def test_the_default_view_sorts_a_market_past_its_close_time_behind_the_open_ones(
    session: AsyncSession,
) -> None:
    """ADR 0011 again, against the view with no filter on it at all.

    [X-1] #34's default view is "open markets", so the derivation has to be in
    the query that runs when nobody has chosen anything — which is the query a
    trader actually hits, and the one most likely to have been written as
    `status == OPEN` before the filter argument was added.

    The default view derives rather than reading the column: a market past its
    close_time is still in it — it is a published market and a trader should
    be able to find it — but reads as closed rather than open, and sorts
    behind whatever is still trading.
    """
    stopped = await _stopped_but_unswept(session, actor())
    still_open = await _open_market(session)

    listed = await _browsing().browse(session)
    ids = _ids(listed)

    assert stopped.id in ids
    assert not _closing_says_open(stopped)
    assert ids.index(still_open.id) < ids.index(stopped.id)


async def test_the_detail_of_an_unswept_market_is_not_open_for_trading(
    session: AsyncSession,
) -> None:
    """ADR 0011 on the detail read, which is [X-3] #36's "trading controls are
    enabled only when the market is open".

    A trader who opens the page of a market that stopped four seconds ago must
    not be offered a buy button, and `closing.is_open_for_trading` is the
    predicate that says so. The status column would say yes.
    """
    stopped = await _stopped_but_unswept(session, actor())

    detail = await _browsing().get_published(session, stopped.id)

    assert not _closing_says_open(detail)


# --- [2.1] #5's counts, which #62 says to build once ----------------------
async def test_counts_are_reported_per_status(session: AsyncSession) -> None:
    """[2.1] #5: "Counts shown per status", inherited by #62's note that the
    status query is built once and shared.

    Counted rather than `len()` of the browse list, because the point of a
    count is to be correct for the statuses the current view is filtering out.
    """
    await _open_market(session)
    await _open_market(session)
    await closed_market(session, actor())
    await proposed_market(session, actor())

    counts = await _browsing().count_by_status(session)

    assert counts[MarketStatus.OPEN] == 2
    assert counts[MarketStatus.CLOSED] == 1
    assert counts[MarketStatus.PENDING_RESOLUTION] == 1


async def test_the_counts_derive_from_the_clock_not_the_status_column(
    session: AsyncSession,
) -> None:
    """ADR 0011 applied to [2.1] #5's counts.

    The one place the ADR explicitly tolerates staleness is a dashboard count,
    and that tolerance is about the *sweeper* being behind — not about two
    readers of the same database disagreeing. If the browse list derives and
    the counts do not, a trader sees "2 open" above a list of one, which is a
    bug report with no cause anybody can find.
    """
    await _stopped_but_unswept(session, actor())
    await _open_market(session)

    counts = await _browsing().count_by_status(session)

    assert counts[MarketStatus.OPEN] == 1
    assert counts[MarketStatus.CLOSED] == 1


async def test_the_counts_exclude_what_a_trader_cannot_see(
    session: AsyncSession,
) -> None:
    """[1.1] #1 again, and the easiest of these to get wrong.

    A count is an aggregate over the same visibility rule as the list, not over
    the table. Counting drafts tells a trader how many markets three
    administrators have half-written, which is both a leak and a number that
    will never match anything on their screen.
    """
    creator = actor()
    await market_service.save(session, creator, draft_request())
    await market_service.save(session, creator, draft_request(status="submitted"))
    await _open_market(session)

    counts = await _browsing().count_by_status(session)

    assert counts.get(MarketStatus.DRAFT, 0) == 0
    assert counts.get(MarketStatus.SUBMITTED, 0) == 0
    assert counts[MarketStatus.OPEN] == 1
