"""The trader-facing read of a market. [BE][X] #62.

Browse ([X-1] #34), search and filter ([X-2] #35), detail ([X-3] #36), and the
per-status counts [2.1] #5 will reuse (D-020). Every filter, count and status
here derives from the clock through `service/closing.py`, never from the status
column alone (ADR 0011, D-022). No prices: `q` lives with the ledger (ADR 0005).
Each browse card names its outcomes (#214), so a card never guesses a label.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from sqlalchemy import (
    ColumnElement,
    Row,
    and_,
    case,
    false,
    func,
    not_,
    null,
    or_,
    select,
    tuple_,
)
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import MarketNotFound
from core.paging import (
    BrowsePosition,
    OverviewPosition,
    decode_browse_cursor,
    decode_overview_cursor,
    encode_browse_cursor,
    encode_overview_cursor,
)
from model.entities import (
    PUBLIC_STATUSES,
    AdminMarketCard,
    TRADER_FACING_STATUS,
    CardOutcome,
    Market,
    MarketCard,
    MarketOutcome,
    MarketOverview,
    MarketPage,
    MarketStatus,
    displayed_status,
)
from service.closing import open_for_trading, was_open_for_trading_at

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


def _close_time_while_trading(
    still_trading: ColumnElement[bool],
) -> ColumnElement[datetime | None]:
    """A still-trading market's sort key: when it will close. NULL for the rest."""
    return case((still_trading, Market.close_time))


def _stopped_at() -> ColumnElement[datetime]:
    """When trading stopped: the earlier of `close_time` and `closed_at`.

    Postgres's LEAST skips a NULL. Swept, that is `close_time`, since the sweep
    writes `closed_at` a little later. Unswept, `closed_at` is NULL, so
    `close_time`. Closed early, it is `closed_at`, which comes before its
    `close_time` (ADR 0014).
    """
    return func.least(
        Market.close_time, Market.closed_at, type_=Market.close_time.type
    )


def _when_trading_stopped(
    still_trading: ColumnElement[bool],
) -> ColumnElement[datetime | None]:
    """A stopped market's sort key: when trading stopped. NULL while trading."""
    return case((still_trading, null()), else_=_stopped_at())


def _browse_order(
    still_trading: ColumnElement[bool],
) -> tuple[ColumnElement[object], ...]:
    """Still-trading first, soonest close first; then most recently stopped first.

    `still_trading` is `was_open_for_trading_at(as_of)`, built once by `browse`:
    the group comes from the clock, not the status column (D-022). Each group's
    key is NULL in the other, so it sorts only its own markets. `Market.id`
    last makes the order total, so ties do not reshuffle between refreshes.
    DECISIONS.md, "Stopped markets sort by when trading stopped".
    """
    return (
        still_trading.desc(),
        _close_time_while_trading(still_trading).asc(),
        _when_trading_stopped(still_trading).desc(),
        Market.id.asc(),
    )


def _sort_key(still_trading: ColumnElement[bool]) -> ColumnElement[datetime]:
    """A market's key in its own group, the one a cursor records.

    Exactly one of the two group keys is not NULL for any market.
    """
    return func.coalesce(
        _close_time_while_trading(still_trading), _when_trading_stopped(still_trading)
    )


def _after_a_trading_market(
    position: BrowsePosition, still_trading: ColumnElement[bool]
) -> ColumnElement[bool]:
    """Every market `_browse_order` puts after `position`, one that was trading:
    the rest of the trading group, then every stopped market."""
    later_in_group = tuple_(Market.close_time, Market.id) > tuple_(
        position.sort_at, position.market_id
    )
    return or_(and_(still_trading, later_in_group), not_(still_trading))


def _after_a_stopped_market(
    position: BrowsePosition, still_trading: ColumnElement[bool]
) -> ColumnElement[bool]:
    """Every market `_browse_order` puts after `position`, one that had stopped:
    the rest of the stopped group.

    Not a row comparison: the key sorts descending and `id` ascending.
    """
    stopped_at = _stopped_at()
    later_in_group = or_(
        stopped_at < position.sort_at,
        and_(stopped_at == position.sort_at, Market.id > position.market_id),
    )
    return and_(not_(still_trading), later_in_group)


def _after(
    position: BrowsePosition, still_trading: ColumnElement[bool]
) -> ColumnElement[bool]:
    """Every market `_browse_order` puts after `position`."""
    if position.is_open:
        return _after_a_trading_market(position, still_trading)
    return _after_a_stopped_market(position, still_trading)


def _cursor_after(row: Row, as_of: datetime) -> str:
    """The cursor that continues the list after `row`, grouped as of `as_of`."""
    return encode_browse_cursor(
        BrowsePosition(
            as_of=as_of,
            is_open=row.still_trading,
            sort_at=row.sort_at,
            market_id=row.id,
        )
    )


async def _list_outcomes_by_market(
    session: AsyncSession, market_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[CardOutcome]]:
    """The outcomes of every market in `market_ids`, in position order. #214.

    One query for the whole page, not one per market. Columns, not entities,
    for the reason `browse` gives. No ids is no query.
    """
    if not market_ids:
        return {}

    stmt = (
        select(
            MarketOutcome.market_id,
            MarketOutcome.id,
            MarketOutcome.position,
            MarketOutcome.label,
        )
        .where(MarketOutcome.market_id.in_(market_ids))
        .order_by(MarketOutcome.market_id, MarketOutcome.position)
    )

    outcomes_by_market: dict[uuid.UUID, list[CardOutcome]] = {}
    for row in (await session.execute(stmt)).all():
        outcome = CardOutcome(id=row.id, position=row.position, label=row.label)
        outcomes_by_market.setdefault(row.market_id, []).append(outcome)
    return outcomes_by_market


async def browse(
    session: AsyncSession,
    *,
    limit: int,
    query: str | None = None,
    status: MarketStatus | None = None,
    now: datetime | None = None,
    cursor: str | None = None,
) -> MarketPage:
    """One page of the markets a trader may see. [X-1] #34, [X-2] #35, #104.

    With no `status`, every published market: still-trading first by soonest
    close, then the rest by most recently stopped (D-022, #105). `query` is a
    case-insensitive search over the question and composes with `status`.
    Nothing found is an empty page, never an error. At most `limit` markets;
    `next_cursor` is None on the last page. Every later page groups and
    compares as of the first page's clock, which the cursor carries. Raises
    `MalformedCursor` for a cursor this service did not issue. `now` is the
    request's clock, passed by the controller so the filter and the displayed
    status read one instant (D-025). Two statements per page whatever its
    size: the markets, then all their outcomes (#214).
    """
    now = now or datetime.now(UTC)
    # Decoded before the query, so a bad value is a 400 rather than a driver
    # error. DECISIONS.md, "A cursor is decoded before the query runs".
    after = decode_browse_cursor(cursor) if cursor is not None else None

    # Later pages group by the first page's instant, or a market that stops
    # trading mid-browse is listed twice. The filter and the displayed status
    # still read `now` (D-025). DECISIONS.md, "The public browse pages by
    # keyset, grouped by the first page's clock".
    as_of = after.as_of if after is not None else now
    still_trading = was_open_for_trading_at(as_of)

    # Columns, not entities. Nothing enters the identity map, so a browse
    # cannot hand a later `get_published` in this session a market whose
    # outcomes are marked loaded and empty. Do not go back to `noload()`. D-027.
    stmt = select(
        Market.id,
        Market.status,
        Market.question,
        Market.close_time,
        still_trading.label("still_trading"),
        _sort_key(still_trading).label("sort_at"),
    ).where(_visible())

    if status is not None:
        stmt = stmt.where(_status_matches(status, now))

    # Blank after stripping is no filter, so `?q=%20%20` means what `?q=` does.
    search = (query or "").strip()
    if search:
        stmt = stmt.where(_question_contains(search))

    if after is not None:
        stmt = stmt.where(_after(after, still_trading))

    # One row past the page says whether another exists, without a COUNT.
    stmt = stmt.order_by(*_browse_order(still_trading)).limit(limit + 1)
    rows = (await session.execute(stmt)).all()

    # Trimmed before the outcomes are read, so they are this page's only.
    shown = rows[:limit]
    next_cursor = _cursor_after(shown[-1], as_of) if len(rows) > limit else None
    outcomes_by_market = await _list_outcomes_by_market(
        session, [row.id for row in shown]
    )

    # Derived here, before projection, not in a validator: FastAPI
    # re-validates the response without the request's clock. D-025, D-027.
    cards = [
        MarketCard(
            id=row.id,
            status=displayed_status(row.status, row.close_time, now=now),
            question=row.question,
            close_time=row.close_time,
            outcomes=tuple(outcomes_by_market.get(row.id, [])),
        )
        for row in shown
    ]
    return MarketPage(markets=cards, next_cursor=next_cursor)


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


# --- the administrator's overview. [2.1] #5, #210 --------------------------
# Its two groups, in order: every market still in play, then the settled ones.
# Settled markets are finished, and an action queue ordered by oldest close
# would otherwise put every one of them first.
_ACTIVE_GROUP = 0
_SETTLED_GROUP = 1


def _visible_to_admin(caller_id: uuid.UUID) -> ColumnElement[bool]:
    """Every market an administrator may see: published, plus their own
    drafts and submissions. [2.1] #5.

    An allowlist plus ownership, like `_visible`: a new status is not visible
    to everyone by default.
    """
    return or_(_visible(), Market.creator_id == caller_id)


def _is_settled() -> ColumnElement[bool]:
    """Is the market settled? Decides its overview group. #210.

    Constant false until [3.4] #12 adds `MarketStatus.SETTLED` (#227). Then
    the body is `return Market.status == MarketStatus.SETTLED`, and nothing
    else changes: the order, the keyset and the cursor carry the group already.
    """
    return false()


def _overview_group() -> ColumnElement[int]:
    """0 for a market still in play, 1 for a settled one, which sorts last.

    A CASE rather than the boolean: until SETTLED exists the boolean renders
    as `false`, and Postgres refuses `ORDER BY false` as a non-integer constant.
    """
    return case((_is_settled(), _SETTLED_GROUP), else_=_ACTIVE_GROUP)


def _overview_order() -> tuple[ColumnElement[object], ...]:
    """Settled last; then soonest close, none last; then id.

    "The admin overview orders by soonest close, missing close times last,
    then id", with #210's group in front. Nothing here reads the clock, so a
    market keeps its place while the clock closes it.
    """
    return (
        _overview_group().asc(),
        Market.close_time.asc().nulls_last(),
        Market.id.asc(),
    )


def _after_a_dated_market(position: OverviewPosition) -> ColumnElement[bool]:
    """In `position`'s group, every market after it when it has a close time:
    the rest of the dated markets, then every one with no close time."""
    later_dated = tuple_(Market.close_time, Market.id) > tuple_(
        position.close_time, position.market_id
    )
    return or_(later_dated, Market.close_time.is_(None))


def _after_an_undated_market(position: OverviewPosition) -> ColumnElement[bool]:
    """In `position`'s group, every market after it when it has no close time:
    the rest of the undated markets, by id."""
    return and_(Market.close_time.is_(None), Market.id > position.market_id)


def _after_in_overview(position: OverviewPosition) -> ColumnElement[bool]:
    """Every market `_overview_order` puts after `position`: the rest of its
    group, then every later group."""
    group = _overview_group()
    position_group = _SETTLED_GROUP if position.settled else _ACTIVE_GROUP
    if position.close_time is None:
        rest_of_group = _after_an_undated_market(position)
    else:
        rest_of_group = _after_a_dated_market(position)
    return or_(group > position_group, and_(group == position_group, rest_of_group))


def _overview_cursor_after(row: Row) -> str:
    """The cursor that continues the overview after `row`."""
    return encode_overview_cursor(
        OverviewPosition(
            settled=row.overview_group == _SETTLED_GROUP,
            close_time=row.close_time,
            market_id=row.id,
        )
    )


async def _list_overview_rows(
    session: AsyncSession,
    *,
    caller_id: uuid.UUID,
    limit: int,
    status: MarketStatus | None,
    now: datetime,
    after: OverviewPosition | None,
) -> Sequence[Row]:
    """Up to `limit + 1` overview rows after `after`, in the overview's order.

    The status filter is the browse's (`_status_matches`), in the query and
    so before the limit. Columns, not entities, for the reason `browse` gives.
    """
    stmt = select(
        Market.id,
        Market.creator_id,
        Market.status,
        Market.question,
        Market.close_time,
        _overview_group().label("overview_group"),
    ).where(_visible_to_admin(caller_id))

    if status is not None:
        stmt = stmt.where(_status_matches(status, now))

    if after is not None:
        stmt = stmt.where(_after_in_overview(after))

    # One row past the page says whether another exists, without a COUNT.
    stmt = stmt.order_by(*_overview_order()).limit(limit + 1)
    return (await session.execute(stmt)).all()


def _tally(
    statuses: Iterable[MarketStatus], seeded: Iterable[MarketStatus]
) -> dict[MarketStatus, int]:
    """Count `statuses`, with every member of `seeded` present at 0 at least."""
    counts: dict[MarketStatus, int] = dict.fromkeys(seeded, 0)
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    return counts


async def count_by_status(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    caller_id: uuid.UUID | None = None,
) -> dict[MarketStatus, int]:
    """Markets per displayed status. [2.1] #5, D-020.

    With no `caller_id`, over what a trader can see, zero-filled over the
    statuses a trader can see; DRAFT and SUBMITTED are not counted. With one,
    over what that administrator can see, zero-filled over every status: the
    overview's counts (#210). Derived like `browse`, so a count never disagrees
    with the list beside it (D-024).
    """
    now = now or datetime.now(UTC)

    if caller_id is None:
        scope = _visible()
        seeded: Iterable[MarketStatus] = PUBLIC_STATUSES
    else:
        scope = _visible_to_admin(caller_id)
        seeded = MarketStatus

    # Columns, not entities, for the reason `browse` gives.
    stmt = select(Market.status, Market.close_time).where(scope)

    rows = (await session.execute(stmt)).all()
    return _tally(
        (displayed_status(status, close_time, now=now) for status, close_time in rows),
        seeded=seeded,
    )


async def overview(
    session: AsyncSession,
    *,
    caller_id: uuid.UUID,
    limit: int,
    status: MarketStatus | None = None,
    now: datetime,
    cursor: str | None = None,
) -> MarketOverview:
    """One page of what an administrator can see, with counts over all of it. [2.1] #5, #210.

    Published markets plus the caller's own drafts and submissions: settled
    last, then soonest close with none last, then id. At most `limit`;
    `next_cursor` is None on the last page. The counts cover every market the
    caller can see, whatever `status` and `cursor` say. The page and the
    counts are two statements in one REPEATABLE READ transaction, so they
    describe one snapshot, and read one `now` (D-025). Raises `MalformedCursor`
    for a cursor this service did not issue, and `InvalidRequestError` if the
    session already has a transaction open. Commits its own read-only
    transaction. DECISIONS.md, "The admin overview pages by keyset, settled
    markets last, and reads its counts in the same REPEATABLE READ snapshot".
    """
    # Decoded before the query, so a bad value is a 400 rather than a driver
    # error. DECISIONS.md, "A cursor is decoded before the query runs".
    after = decode_overview_cursor(cursor) if cursor is not None else None

    # `begin()` refuses a session already in a transaction, where the level can
    # no longer be set; `connection(execution_options=...)` alone would only
    # warn and read at READ COMMITTED. The level resets when the connection
    # goes back to the pool.
    async with session.begin():
        await session.connection(
            execution_options={"isolation_level": "REPEATABLE READ"}
        )
        rows = await _list_overview_rows(
            session,
            caller_id=caller_id,
            limit=limit,
            status=status,
            now=now,
            after=after,
        )
        counts = await count_by_status(session, now=now, caller_id=caller_id)

    shown = rows[:limit]
    next_cursor = _overview_cursor_after(shown[-1]) if len(rows) > limit else None

    # Derived here, before projection, not in a validator: FastAPI
    # re-validates the response without the request's clock. D-025, D-027.
    cards = [
        AdminMarketCard(
            id=row.id,
            creator_id=row.creator_id,
            status=displayed_status(row.status, row.close_time, now=now),
            question=row.question,
            close_time=row.close_time,
        )
        for row in shown
    ]
    return MarketOverview(markets=cards, counts=counts, next_cursor=next_cursor)
