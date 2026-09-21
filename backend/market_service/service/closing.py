"""When a market stops accepting trades, and how that reaches the status column.
[F-4] #44.

The one idea in this file, and the reason it is a file rather than a line in
the sweeper: **the clock closes a market, not the job that notices.**

A market is untradeable the instant `close_time` passes. That is a fact about
two values and needs nothing to run. The `CLOSED` status is a *materialisation*
of that fact — written a few seconds later by `close_due_markets` below, so
that [2.1] #5 can count markets per status, [3.1] #9 can gate on a value rather
than re-deriving a comparison, and [BE][X] #62 can group without a predicate in
every query.

Getting that order the wrong way round is the trap this file exists to avoid.
If the trade path asked "is `status` OPEN?", then every second between
`close_time` and the sweep is a second in which someone can trade a market that
has closed — on a platform whose `close_time < resolution_time` rule exists, in
`service/validation.py`'s own words, so that nobody "can bet on a result they
already know". No sweep interval makes that window zero. Deriving the answer
does.

So this module offers the same rule twice, and callers pick by where they are:

- `is_open_for_trading` for an entity already in memory ([T-2] #22, holding a
  locked row inside the trade's own transaction, and [2.3] #7's early close,
  which holds one for the same reason)
- `open_for_trading` for a WHERE clause ([BE][X] #62 and [2.1] #5, which must
  not list a market as open three seconds after it stopped being one)

Neither depends on the sweeper having run. The sweeper falling behind, or being
switched off entirely, makes a dashboard count stale. It cannot let a trade
through.

This is the same shape as the ledger's balances, for the same reason: ADR 0009
derives a balance from `SUM(amount)` rather than storing one, so that the
displayed number is right by construction instead of by vigilance. `close_time`
is the sum here.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ColumnElement, and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.closing import trading_is_open
from model.entities import Market, MarketStatus

def is_open_for_trading(market: Market, *, now: datetime | None = None) -> bool:
    """May a trade execute against this market right now?

    The authority. [T-2] #22 calls this inside the trade's own transaction,
    against the row it has already locked, and rejects the trade when it is
    False. Nothing else in this repository is entitled to answer the question
    by reading `status` alone.

    [2.3] #7's `close_early` is the other caller and asks the same question for
    a different reason: it may only stop a market that is still running, so
    that the audit entry it writes — an administrator stopped this — is not
    claimed over a market the clock had already stopped. It refuses exactly
    when a trade would, which is the property having one predicate buys.

    Two conditions, and both are load-bearing:

    - the market is OPEN, which is the administrator's decision that it should
      be tradeable at all, and the one thing the clock cannot supply
    - its close time has not passed, which is the clock's half and is exact

    `now` is injectable for the same reason it is in `service/validation.py`:
    so a test can put a market either side of its close time without sleeping.

    **[T-2] #22 should pass Postgres's clock, not the default.** The default is
    the calling container's, which is fine for a test and wrong for a fleet:
    `close_due_markets` below refuses a Python `datetime` outright because two
    replicas drifting a few seconds apart would disagree about whether a market
    was due. A trade already holds a transaction and a row lock when it asks
    this question, so it can read `func.now()` from that same transaction and
    get the one clock every replica already agrees to trust.

    The two-condition check itself lives in `core/closing.py`, shared with
    `model/schemas.py`'s derivation of the status a trader is shown (D-023) —
    this function is the entity-shaped wrapper around it, and stays the one
    thing the trade path and [2.3] #7 import.
    """
    return trading_is_open(
        market.status is MarketStatus.OPEN, market.close_time, now=now
    )


def open_for_trading(now: datetime | None = None) -> ColumnElement[bool]:
    """The same rule, as a WHERE clause over `Market`.

    For [BE][X] #62's browse query and [2.1] #5's status filter. Written here
    rather than in either of them so that the trader-facing list and the trade
    itself cannot come to different conclusions about the same market — a
    market that is listed as tradeable and then refuses the trade is a bug
    report, and the two rules drifting is how that happens.

    Note that this is deliberately NOT `Market.status == MarketStatus.OPEN`,
    which is what `model/entities.py` still describes as the browse predicate.
    That was true before this ticket and is now half of it.

    **One caller deliberately does not take the transaction's clock**, and the
    argument for it above is not wrong — it just does not reach that caller.
    `service/browsing.py` passes a Python `datetime` read once at the
    controller, because the trader-facing projection derives the displayed
    status in Python at serialisation time and the two have to agree with
    each other more than either has to agree with Postgres. Leaving `now` to
    default to `func.now()` there put `transaction_timestamp()` in the filter
    and a strictly later instant in the display, so a market whose
    `close_time` fell between them appeared in the open tab labelled closed.
    D-025 has the full argument, the alternative that was rejected, and the
    trigger that reverses it: a path that *decides or writes* off this same
    projection needs the transaction's clock, and this function is still how
    it gets one.

    This is also the fifth expression of ADR 0011's rule and the one copy
    nobody can remove: it returns a `ColumnElement`, so it can never call
    `core/closing.py`, which returns a `bool`. D-024 names the other four.
    """
    return and_(
        Market.status == MarketStatus.OPEN,
        Market.close_time > (now or func.now()),
    )


async def close_due_markets(session: AsyncSession, *, limit: int) -> list[uuid.UUID]:
    """Move every market whose close time has passed from OPEN to CLOSED.

    Commits, and returns the ids this call actually closed — which is not the
    same as the ids that were due. See the concurrency note below.

    `limit` has no default on purpose. It is a tuning knob, and this repository
    keeps those in `core/config.py` where an operator can find them, rather
    than as a number buried in the module that happens to use it.

    **The clock is Postgres's, not Python's.** `func.now()` is the transaction
    timestamp on the one machine every replica already agrees to trust. Sending
    a Python `datetime` instead would make the closing time depend on whichever
    container happened to run the sweep, and two replicas with a few seconds of
    drift would disagree about whether a market was due. There is therefore no
    `now` parameter here: a test moves `close_time`, which is the honest way to
    ask this question anyway.

    **Two replicas sweeping at once is safe and needs no coordination.** The
    inner `SELECT ... FOR UPDATE SKIP LOCKED` hands each sweeper a disjoint set
    of rows, so they do not queue behind each other, and the outer UPDATE
    re-checks `status = 'open'` against the committed row regardless. A market
    is therefore closed exactly once and appears in exactly one caller's return
    value, which is what makes it safe for a caller to act on that list. Same
    guarantee `publish` gets from its row lock, one statement instead of two.

    **`ORDER BY close_time` with `LIMIT` is not cosmetic.** Under a backlog it
    is what makes the oldest market close first, so the sweeper drains in the
    order things actually came due rather than in whatever order the index
    happened to return.

    **`updated_at` moves.** The column's `onupdate` fires on this statement
    like any other UPDATE, so a market that closes rises to the top of its
    creator's list, which `list_for_creator` orders by `updated_at`. That is
    wanted rather than tolerated: a market that just closed is the one now
    waiting on an administrator to propose an outcome ([3.1] #9).
    """
    due = (
        select(Market.id)
        .where(Market.status == MarketStatus.OPEN, Market.close_time <= func.now())
        .order_by(Market.close_time)
        .limit(limit)
        # SKIP LOCKED rather than waiting: a row another sweeper is already
        # closing is a row this one has nothing left to do about, and blocking
        # on it would serialise every replica behind the slowest.
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )

    closed = (
        await session.execute(
            update(Market)
            .where(Market.id.in_(due))
            .values(status=MarketStatus.CLOSED, closed_at=func.now())
            .returning(Market.id)
            # No ORM objects are loaded here, so there is no identity map to
            # keep in step and nothing to synchronise against.
            .execution_options(synchronize_session=False)
        )
    ).scalars().all()

    await session.commit()
    return list(closed)
