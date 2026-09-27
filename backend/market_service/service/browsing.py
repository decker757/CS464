"""The trader-facing read of a market. [BE][X] #62.

Browse ([X-1] #34), search and filter ([X-2] #35), detail ([X-3] #36), and the
per-status counts [2.1] #5 will reuse (D-020). Every filter, count and status
here derives from the clock through `service/closing.py`, never from the status
column alone (ADR 0011, D-022). No prices: `q` lives with the ledger (ADR 0005).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, and_, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import MarketNotFound
from model.entities import (
    PUBLIC_STATUSES,
    TRADER_FACING_STATUS,
    Market,
    MarketCard,
    MarketStatus,
    displayed_status,
)
from service.closing import open_for_trading

# The escape character handed to `ILIKE ... ESCAPE`. A single backslash; the
# doubling is Python's, not SQL's.
_LIKE_ESCAPE = "\\"

# Postgres's two LIKE metacharacters.
_LIKE_WILDCARDS = ("%", "_")


def _visible() -> ColumnElement[bool]:
    """Every market a trader may reach: published, in any later status. [1.1] #1.

    Applied by every query here, even a status filter that asks for `draft`.
    An allowlist from `PUBLIC_STATUSES`, which the route's `status` parameter
    also reads. Do not turn it into `notin_([DRAFT, SUBMITTED])`: a new status
    such as SETTLED would then be trader-visible the moment it was declared.
    """
    return Market.status.in_(PUBLIC_STATUSES)


def _question_contains(query: str) -> ColumnElement[bool]:
    """A containment search that treats the trader's input as text, not a pattern.

    Unescaped, `_` or `2%` would silently match nearly every question. The
    backslash is escaped first, or this would escape its own escapes.
    """
    escaped = query.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
    for wildcard in _LIKE_WILDCARDS:
        escaped = escaped.replace(wildcard, _LIKE_ESCAPE + wildcard)
    return Market.question.ilike(f"%{escaped}%", escape=_LIKE_ESCAPE)


def _stopped_but_unswept(now: datetime) -> ColumnElement[bool]:
    """OPEN in the column but past its close time: the gap ADR 0011 is about."""
    return and_(Market.status == MarketStatus.OPEN, not_(open_for_trading(now)))


def _status_matches(status: MarketStatus, now: datetime) -> ColumnElement[bool]:
    """What "status = X" means to a trader.

    OPEN is `open_for_trading()`. CLOSED also takes a market the sweep has not
    reached yet, or it would vanish from every filter until the sweep runs.
    Every other status matches the column as stored.
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
) -> list[MarketCard]:
    """Every market a trader may see, filtered and searched. [X-1] #34, [X-2] #35.

    With no `status`, every published market: still-trading first, then by
    soonest close (D-022). `query` is a case-insensitive search over the
    question and composes with `status`. Nothing found is `[]`, never an
    error. `now` is the request's clock, passed by the controller so the
    filter and the displayed status read one instant (D-025).
    """
    now = now or datetime.now(UTC)
    # Columns, not entities. Nothing enters the identity map, so a browse
    # cannot hand a later `get_published` in this session a market whose
    # outcomes are marked loaded and empty. Do not go back to `noload()`. D-027.
    stmt = select(
        Market.id, Market.status, Market.question, Market.close_time
    ).where(_visible())

    if status is not None:
        stmt = stmt.where(_status_matches(status, now))

    # Blank after stripping is no filter, so `?q=%20%20` means what `?q=` does.
    search = (query or "").strip()
    if search:
        stmt = stmt.where(_question_contains(search))

    # `Market.id` last makes the order total, so markets sharing a close time
    # do not reshuffle between refreshes.
    stmt = stmt.order_by(
        open_for_trading(now).desc(), Market.close_time.asc(), Market.id.asc()
    )

    # Derived here, before projection, not in a validator: FastAPI
    # re-validates the response without the request's clock. D-025, D-027.
    return [
        MarketCard(
            id=row.id,
            status=displayed_status(row.status, row.close_time, now=now),
            question=row.question,
            close_time=row.close_time,
        )
        for row in (await session.execute(stmt)).all()
    ]


async def get_published(
    session: AsyncSession, market_id: uuid.UUID, *, now: datetime | None = None
) -> Market:
    """One published market, whoever created it. [X-3] #36.

    Raises `MarketNotFound` alike for a draft, a submitted market and an
    unknown id. Do not swap in `market_service.get_any`: [1.1] #1's draft
    invisibility would go with no test failing. The derived status goes on an
    unmapped attribute, never on `status`; see `TRADER_FACING_STATUS`. D-027.
    """
    stmt = select(Market).where(Market.id == market_id, _visible())
    market = (await session.execute(stmt)).scalar_one_or_none()
    if market is None:
        raise MarketNotFound

    setattr(
        market,
        TRADER_FACING_STATUS,
        displayed_status(market.status, market.close_time, now=now),
    )
    return market


async def count_by_status(
    session: AsyncSession, *, now: datetime | None = None
) -> dict[MarketStatus, int]:
    """Markets per displayed status, over what a trader can see. [2.1] #5, D-020.

    Derived like `browse`, so a count never disagrees with the list beside it
    (D-024). DRAFT and SUBMITTED are not counted.
    """
    now = now or datetime.now(UTC)

    # Columns, not entities, for the reason `browse` gives.
    stmt = select(Market.status, Market.close_time).where(_visible())

    # Seeded, so a status with no markets reads 0 rather than a KeyError.
    counts: dict[MarketStatus, int] = dict.fromkeys(PUBLIC_STATUSES, 0)

    for status, close_time in (await session.execute(stmt)).all():
        derived = displayed_status(status, close_time, now=now)
        counts[derived] = counts.get(derived, 0) + 1
    return counts
