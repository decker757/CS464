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
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, and_, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from core.errors import MarketNotFound
from model.entities import Market, MarketStatus, displayed_status
from service.closing import open_for_trading


def _visible() -> ColumnElement[bool]:
    """Every market a trader may reach at all. [1.1] #1.

    A market is public from the moment it is published and stays public
    forever after — OPEN, CLOSED, PENDING_RESOLUTION and APPROVED are all
    reachable. DRAFT and SUBMITTED are not: publication is what puts a market
    in front of a trader ([1.3] #3), and neither the list nor the detail read
    below may be the hole in that. This is the one filter every query in this
    module applies, including a status filter that explicitly asks for
    `draft` — asking does not widen what a trader may see.

    **An allowlist, not a denylist, and the difference is a future bug.**
    `notin_([DRAFT, SUBMITTED])` says the same thing about the six members
    that exist today and the opposite thing about the seventh. Adding a
    `MarketStatus` member is a Python change and nothing else — the column is
    a non-native `Enum`, so there is no migration to review and no CHECK
    constraint to fail — and SETTLED is already named as arriving with [3.4]
    #12. Under a denylist it would become trader-visible the moment it was
    declared, before any code decided it should be, and nothing anywhere
    would go red. Under this, a new member is invisible until somebody adds
    it here on purpose, which is the failure that gets noticed.
    """
    return Market.status.in_(
        [
            MarketStatus.OPEN,
            MarketStatus.CLOSED,
            MarketStatus.PENDING_RESOLUTION,
            MarketStatus.APPROVED,
        ]
    )


# The escape character handed to `ILIKE ... ESCAPE`. A single backslash; the
# doubling below is Python's, not SQL's.
_LIKE_ESCAPE = "\\"

# Postgres's two LIKE metacharacters. `%` is any run of characters and `_` is
# exactly one.
_LIKE_WILDCARDS = ("%", "_")


def _question_contains(query: str) -> ColumnElement[bool]:
    """A containment search that treats the trader's input as text, not pattern.

    The wildcards wrapping `query` are ours and mean "anything either side".
    The ones *inside* it came from somebody typing, and have to match
    themselves. Without this they are the same character, so the pattern
    language leaks to the caller:

    - `2%` builds `%2%%`, which collapses to `%2%` and matches every question
      containing a 2
    - `_` builds `%_%`, which matches every question with at least one
      character in it — that is, all of them
    - `100%` finds markets that have nothing to do with a hundred per cent

    All three are silent. There is no error and no empty result to notice;
    the trader gets a plausible-looking list that is simply the wrong one.

    The backslash is escaped first, before it is used to escape anything else.
    Doing it last would escape the escapes this function had just added and
    turn `%` back into a wildcard.
    """
    escaped = query.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
    for wildcard in _LIKE_WILDCARDS:
        escaped = escaped.replace(wildcard, _LIKE_ESCAPE + wildcard)
    return Market.question.ilike(f"%{escaped}%", escape=_LIKE_ESCAPE)


def _stopped_but_unswept(now: datetime) -> ColumnElement[bool]:
    """OPEN in the column, but past its close time. The gap ADR 0011 is about.

    Built from `open_for_trading()` rather than restated, so the browse query
    and the trade path cannot come to different conclusions about the same
    market.
    """
    return and_(Market.status == MarketStatus.OPEN, not_(open_for_trading(now)))


def _status_matches(status: MarketStatus, now: datetime) -> ColumnElement[bool]:
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
        return open_for_trading(now)
    if status is MarketStatus.CLOSED:
        return or_(Market.status == MarketStatus.CLOSED, _stopped_but_unswept(now))
    return Market.status == status


async def browse(
    session: AsyncSession,
    *,
    query: str | None = None,
    status: MarketStatus | None = None,
    now: datetime | None = None,
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

    **`now` is the request's clock and the controller passes it** (D-025), so
    that the filter here and the derivation in `model/schemas.py` read one
    instant. They used to read two: `func.now()` is `transaction_timestamp()`
    and is frozen when the transaction opens, while the schema's derivation
    ran at serialisation, strictly later. A market whose `close_time` fell in
    the gap passed `status=open` here and then serialised as `closed` — an
    open tab with a market labelled closed in it, with zero clock skew.

    Defaulted rather than required, so the service layer stays callable from
    a test and from anything holding no request.
    """
    now = now or datetime.now(UTC)
    # `noload` on both children, because `PublicMarketSummaryOut` reads four
    # scalar columns and neither collection is one of them. `Market` declares
    # `outcomes` and `resolution_sources` as `lazy="selectin"`, which is right
    # for the detail read below and for the admin projection, and which here
    # costs two extra round trips per browse pulling every outcome and every
    # resolution source of every market listed — for rows that are then
    # dropped on the floor by the projection.
    #
    # `noload` rather than a column select, so this still returns `Market`
    # entities: the caller, `count_by_status` and every test in the suite
    # treat a browse result as a market, and a list of row tuples would be a
    # wider change than the one being made. The cost is that `.outcomes` on a
    # market from this function reads `[]` rather than raising — acceptable
    # because the one caller is the list projection, and worth knowing if a
    # second caller ever wants the children back.
    stmt = (
        select(Market)
        .where(_visible())
        .options(noload(Market.outcomes), noload(Market.resolution_sources))
    )

    if status is not None:
        stmt = stmt.where(_status_matches(status, now))

    if query:
        stmt = stmt.where(_question_contains(query))

    # `Market.id` last, as the tiebreaker that makes this order total.
    # "Closes end of quarter" is a thing several markets say at once, and
    # without it two markets sharing a `close_time` come back in whatever
    # order the plan happens to produce — so the list reshuffles between two
    # refreshes that returned the same rows. It is also what keyset paging
    # needs to exist later: a cursor cannot name a position in an order that
    # does not uniquely determine one.
    stmt = stmt.order_by(
        open_for_trading(now).desc(), Market.close_time.asc(), Market.id.asc()
    )

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


async def count_by_status(
    session: AsyncSession, *, now: datetime | None = None
) -> dict[MarketStatus, int]:
    """Markets per status, over what a trader can see. [2.1] #5, built once here.

    Grouped in Python rather than in a SQL `CASE`, on a table sized for a
    handful of administrators' markets: the one thing worth being careful
    about is reusing the same derivation the browse query and the trade path
    already use, not shaving a round trip off a count nobody is polling.

    Derives for the same reason the browse query does — the one tolerance ADR
    0011 grants a sweep running behind is a stale *count*, not two readers of
    the same database disagreeing, and a trader who sees "2 open" above a
    list of one open market has found a bug with no visible cause.

    `displayed_status` is the shared wrapper in `model/entities.py` (D-024).
    This function held its own copy of those three lines until then, and
    `model/schemas.py` held the other — one rule, two spellings, and only one
    of them with tests pointed at it.

    Excludes DRAFT and SUBMITTED, the same visibility rule `browse` and
    `get_published` apply: a count is an aggregate over what a trader can see,
    not over the table, and counting drafts would tell a trader how many
    markets three administrators have half-written.
    """
    now = now or datetime.now(UTC)

    stmt = select(Market).where(_visible())
    counts: dict[MarketStatus, int] = {}
    for market in (await session.execute(stmt)).scalars():
        derived = displayed_status(market.status, market.close_time, now=now)
        counts[derived] = counts.get(derived, 0) + 1
    return counts
