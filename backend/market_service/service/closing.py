"""Whether a market is still trading, and the sweep that writes CLOSED. [F-4] #44.

The clock closes a market, not the job that notices: a market is untradeable
the instant `close_time` passes, and the CLOSED status is written a few seconds
later for readers that want a value to count and gate on. Do not gate trading
on `status == OPEN`; that makes the sweep interval a correctness parameter.
ADR 0011.

`is_open_for_trading` answers for an entity in memory, `open_for_trading` as a
WHERE clause. Neither depends on the sweeper having run.
`was_open_for_trading_at` answers the historical question a paged browse asks.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.closing import trading_is_open
from model.entities import Market, MarketStatus


def is_open_for_trading(market: Market, *, now: datetime | None = None) -> bool:
    """May a trade execute against this market right now? ADR 0011.

    The authority: nothing may answer this by reading `status` alone.
    `close_early` asks it against the row it holds locked. A path that decides
    and writes should read Postgres's `clock_timestamp()` after its lock and
    pass that value, not the container's clock, so replicas agree (D-025) —
    and not `func.now()`, frozen before the lock wait (#179). Passing the
    SQL expression itself would crash. The two-condition check is
    `core/closing.py` (D-023).
    """
    return trading_is_open(
        market.status is MarketStatus.OPEN, market.close_time, now=now
    )


def open_for_trading(now: datetime | None = None) -> ColumnElement[bool]:
    """The same rule as a WHERE clause over `Market`, for browse and status filters.

    Not `Market.status == MarketStatus.OPEN`. This restates `core/closing.py`
    in SQL and cannot call it, so it is the one copy of the rule that stays
    (D-024). `service/browsing.py` deliberately passes a Python `now` instead
    of the transaction clock, so its filter and its display agree (D-025).
    """
    return and_(
        Market.status == MarketStatus.OPEN,
        Market.close_time > (now or func.now()),
    )


def was_open_for_trading_at(as_of: datetime) -> ColumnElement[bool]:
    """Was a published market still trading at `as_of`? A WHERE clause. #104.

    Time only, unlike `open_for_trading`: a status written after `as_of` cannot
    move the answer, so a market closed early between two page reads keeps its
    group. Sound because every exit from OPEN stamps `closed_at` and nothing
    clears it. A draft may read True, so apply `_visible()` first. DECISIONS.md,
    "The public browse pages by keyset, grouped by the first page's clock".
    """
    return and_(
        Market.close_time > as_of,
        or_(Market.closed_at.is_(None), Market.closed_at > as_of),
    )


async def close_due_markets(session: AsyncSession, *, limit: int) -> list[uuid.UUID]:
    """Close up to `limit` due markets, commit, and return the ids this call closed.

    Uses Postgres's clock and takes no `now`, so replicas with drifting clocks
    cannot disagree about which markets are due (D-025); a test moves
    `close_time` instead. Two sweepers at once is safe: SKIP LOCKED hands them
    disjoint rows, and each market is closed, and returned, exactly once.
    ADR 0011. Oldest due first, so a backlog drains in order. `updated_at`
    moves too, which lifts the market up its creator's list for [3.1] #9.
    """
    due = (
        select(Market.id)
        .where(Market.status == MarketStatus.OPEN, Market.close_time <= func.now())
        .order_by(Market.close_time)
        .limit(limit)
        # A row another sweeper is closing needs nothing more from this one,
        # and waiting on it would serialise every replica behind the slowest.
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )

    closed = (
        await session.execute(
            update(Market)
            .where(Market.id.in_(due))
            .values(status=MarketStatus.CLOSED, closed_at=func.now())
            .returning(Market.id)
            # No ORM objects are loaded here, so there is nothing to sync.
            .execution_options(synchronize_session=False)
        )
    ).scalars().all()

    await session.commit()
    return list(closed)
