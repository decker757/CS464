"""The trader-facing read of a market. [BE][X] #62.

Three stories share one query layer here:

- [X-1] #34 browse, whose default view is open markets ordered by soonest
  close
- [X-2] #35 search and filter, a question search and a status filter that
  compose with each other and with the default
- [X-3] #36 market detail, an unscoped read of one published market

and [2.1] #5's per-status counts, because #62 says to build that query once
and share it rather than let the admin dashboard restate it.

**ADR 0011 runs underneath all of it.** A market stops trading the instant
`close_time` passes, not when the sweep gets around to writing CLOSED, and
every filter, count and detail read here asks `service.closing` rather than
the status column alone — `open_for_trading()` for a WHERE clause,
`is_open_for_trading()` for an entity already loaded, exactly as that module's
own docstring describes its two callers.

Nothing here reads or returns a price. `q` lives with the ledger (ADR 0005),
and the authoritative read is the snapshot endpoint in
`docs/api/realtime-service.md`, landing with [F-3] #43 and [T-2] #22.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, and_, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import MarketNotFound
from model.entities import Market, MarketStatus
from service.closing import is_open_for_trading, open_for_trading


def _visible() -> ColumnElement[bool]:
    """Every market a trader may reach at all. [1.1] #1.

    A market is public from the moment it is published and stays public
    forever after — OPEN, CLOSED, PENDING_RESOLUTION and APPROVED are all
    reachable. DRAFT and SUBMITTED are not: publication is what puts a market
    in front of a trader ([1.3] #3), and neither the list nor the detail read
    below may be the hole in that. This is the one filter every query in this
    module applies, including a status filter that explicitly asks for
    `draft` — asking does not widen what a trader may see.
    """
    return Market.status.notin_([MarketStatus.DRAFT, MarketStatus.SUBMITTED])


def _stopped_but_unswept() -> ColumnElement[bool]:
    """OPEN in the column, but past its close time. The gap ADR 0011 is about.

    Built from `open_for_trading()` rather than restated, so the browse query
    and the trade path cannot come to different conclusions about the same
    market.
    """
    return and_(Market.status == MarketStatus.OPEN, not_(open_for_trading()))


def _status_matches(status: MarketStatus) -> ColumnElement[bool]:
    """What "status = X" means to a trader, for one value of X.

    OPEN reuses `open_for_trading()` outright — a market past its close time
    is not open, full stop. CLOSED is the interesting case: it has to include
    both a market the sweep has already written down *and* one that is still
    reading OPEN in the column but has stopped trading, or a market vanishes
    from every filter for the few seconds between the clock and the sweep,
    which is worse than the status merely lagging. Every other value has no
    derived meaning and matches the column as stored.
    """
    if status is MarketStatus.OPEN:
        return open_for_trading()
    if status is MarketStatus.CLOSED:
        return or_(Market.status == MarketStatus.CLOSED, _stopped_but_unswept())
    return Market.status == status


async def browse(
    session: AsyncSession,
    *,
    query: str | None = None,
    status: MarketStatus | None = None,
) -> list[Market]:
    """Every market a trader may see, filtered and searched. [X-1] #34, [X-2] #35.

    No `status` at all is [X-1] #34's default view, and that ticket asks for
    two separate things from it, not one: "open markets ordered by soonest
    closing time" is about ordering and emphasis, and "open markets are
    clearly distinguishable from closed, pending-resolution, and settled
    markets" requires the other three to be *in* the response — there is
    nothing to distinguish from otherwise. So the default is every published
    market, open ones first (derived via `open_for_trading()`, D-022) and then
    by soonest close within each group. `status=open` is the narrower query
    a trader gets by choosing the first group explicitly.

    `query` is a case-insensitive containment search over the question, and
    composes with `status` by being one more `WHERE` clause: both narrow the
    same result set rather than each running its own pass.

    An empty result is an empty list, never an error — [X-1] #34's "an
    appropriate empty state is shown" is the caller's job once this returns
    `[]`, and a 404 here would give it nothing to distinguish from a market
    that does not exist.
    """
    stmt = select(Market).where(_visible())

    if status is not None:
        stmt = stmt.where(_status_matches(status))

    if query:
        stmt = stmt.where(Market.question.ilike(f"%{query}%"))

    stmt = stmt.order_by(open_for_trading().desc(), Market.close_time.asc())

    return list((await session.execute(stmt)).scalars())


async def get_published(session: AsyncSession, market_id: uuid.UUID) -> Market:
    """One published market, whoever created it. [X-3] #36.

    Unscoped on purpose — a published market belongs to every trader, and
    there is no creator id to scope it to from a caller who made nothing.
    That is the opposite of `market_service.get`, which puts `creator_id` in
    the WHERE clause so an administrator cannot read a colleague's draft.

    `MarketNotFound`, and the same error, for a draft, a submitted market and
    an id that never existed at all — so the three are indistinguishable from
    outside, which is most of what [1.1] #1's visibility rule is protecting.
    Point this at `market_service.get_any` instead and that rule is gone with
    no existing test failing.
    """
    stmt = select(Market).where(Market.id == market_id, _visible())
    market = (await session.execute(stmt)).scalar_one_or_none()
    if market is None:
        raise MarketNotFound
    return market


def _derived_status(market: Market) -> MarketStatus:
    """One market's status, the way a trader is shown it. ADR 0011.

    Calls `is_open_for_trading` rather than restating its two conditions, so
    this count and the trade path cannot disagree about the same market. Only
    ever turns a stored OPEN into a counted CLOSED; every other status is
    counted as it stands.
    """
    if market.status is MarketStatus.OPEN and not is_open_for_trading(market):
        return MarketStatus.CLOSED
    return market.status


async def count_by_status(session: AsyncSession) -> dict[MarketStatus, int]:
    """Markets per status, over what a trader can see. [2.1] #5, built once here.

    Grouped in Python rather than in a SQL `CASE`, on a table sized for a
    handful of administrators' markets: the one thing worth being careful
    about is reusing the same derivation the browse query and the trade path
    already use, not shaving a round trip off a count nobody is polling.

    Derives for the same reason the browse query does — the one tolerance ADR
    0011 grants a sweep running behind is a stale *count*, not two readers of
    the same database disagreeing, and a trader who sees "2 open" above a
    list of one open market has found a bug with no visible cause.

    Excludes DRAFT and SUBMITTED, the same visibility rule `browse` and
    `get_published` apply: a count is an aggregate over what a trader can see,
    not over the table, and counting drafts would tell a trader how many
    markets three administrators have half-written.
    """
    stmt = select(Market).where(_visible())
    counts: dict[MarketStatus, int] = {}
    for market in (await session.execute(stmt)).scalars():
        derived = _derived_status(market)
        counts[derived] = counts.get(derived, 0) + 1
    return counts
