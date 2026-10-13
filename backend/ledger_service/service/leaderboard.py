"""The leaderboard refresh: value every holder, name them through auth, and
store the ranking as one generation. [L-1] #38, [L-3] #40, ADR 0021.

Nothing calls `refresh` yet; the loop that runs it every
`LEADERBOARD_REFRESH_SECONDS` is the next PR. DECISIONS.md, "The refresh runs
every 60 seconds in a loop on each replica, behind
`LEADERBOARD_REFRESH_ENABLED`, and a failed refresh writes nothing": any
failure raises before the write transaction, so nothing is written.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from itertools import groupby
from typing import Any

import httpx
from sqlalchemy import (
    Row,
    ScalarSelect,
    Select,
    and_,
    delete,
    func,
    insert,
    select,
    true,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from core.config import get_settings
from core.database import LEADERBOARD_REFRESH_LOCK_KEY, release_before_calling_out
from core.errors import LeaderboardRefreshed
from core.leaderboard import is_stale
from core.paging import decode_rank_cursor, encode_rank_cursor, page_cut_of
from model.entities import (
    Account,
    AccountKind,
    Entry,
    LeaderboardEntry,
    LeaderboardGeneration,
    MarketOutcome,
    Position,
)
from model.schemas import LeaderboardRowOut, LeaderboardStandingOut
from service import user_directory, valuation

logger = logging.getLogger(__name__)

# The new generation and the one before it. DECISIONS.md, "The leaderboard is
# stored as generations, and a refresh swaps the whole ranking in one commit".
_GENERATIONS_KEPT = 2


@dataclass(frozen=True)
class LeaderboardPage:
    """One page of one stored ranking. `computed_at` is None, and `stale`
    true, when no refresh has completed. `me` is the caller's own row in that
    ranking, None when the caller is not in it."""

    computed_at: datetime | None
    stale: bool
    ranked_users: int
    me: LeaderboardStandingOut | None
    rows: list[LeaderboardRowOut]
    next_cursor: str | None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


@dataclass(frozen=True)
class _RankedUser:
    rank: int
    user_id: uuid.UUID
    username: str
    net_worth: Decimal


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _list_net_worths(session: AsyncSession) -> dict[uuid.UUID, Decimal]:
    """Every USER account holder's net worth, from one statement with no lock.

    DECISIONS.md, "The refresh values holders through the portfolio's
    valuation, and the portfolio keeps its one-statement read": the
    portfolio's row shape for every holder, ordered by
    `(owner_id, market_id, position)`, valued by `valuation.valuation_of`.
    `held_markets` is lateral, so each holder joins only their own markets,
    and correlated to the anchor alone: `held_outcomes_query` joins
    `Position` too, and auto-correlation would take that one away.
    """
    balance = (
        select(func.coalesce(func.sum(Entry.amount), valuation.ZERO))
        .where(Entry.account_id == Account.id)
        .scalar_subquery()
    )
    anchor = (
        select(Account.owner_id.label("holder_id"), balance.label("balance"))
        .where(Account.kind == AccountKind.USER)
        .subquery()
    )
    held_markets = (
        select(Position.market_id)
        .where(Position.user_id == anchor.c.holder_id, Position.quantity > 0)
        .distinct()
        .correlate(anchor)
        .lateral()
    )

    anchored = (
        select(anchor.c.holder_id, anchor.c.balance)
        .select_from(anchor)
        .outerjoin(held_markets, true())
    )
    stmt = valuation.held_outcomes_query(
        anchored, held_markets, holder_id=anchor.c.holder_id
    ).order_by(anchor.c.holder_id, MarketOutcome.market_id, MarketOutcome.position)

    rows = (await session.execute(stmt)).all()
    net_worths: dict[uuid.UUID, Decimal] = {}
    for holder_id, group in groupby(rows, key=lambda row: row.holder_id):
        holder_rows = list(group)
        net_worths[holder_id] = valuation.valuation_of(
            holder_rows[0].balance, holder_rows
        ).net_worth
    return net_worths


def _rank_order(entry: tuple[user_directory.DirectoryUser, Decimal]) -> tuple[Decimal, str]:
    """Net worth descending, then `lower(username)` ascending."""
    user, net_worth = entry
    return (-net_worth, user.username.lower())


def _ranking_of(
    net_worths: dict[uuid.UUID, Decimal],
    users: list[user_directory.DirectoryUser],
) -> list[_RankedUser]:
    """The eligible holders, ranked 1..N with no gap.

    A holder auth did not name is skipped with a WARNING: DECISIONS.md, "Every
    ledger `USER` account holder is ranked, administrators included; nobody
    else is". A suspended one is dropped before numbering: "A suspended
    account is left out of the ranking, and ranks close up with no gap".
    """
    user_by_id = {user.id: user for user in users}
    eligible: list[tuple[user_directory.DirectoryUser, Decimal]] = []
    for holder_id, net_worth in net_worths.items():
        user = user_by_id.get(holder_id)
        if user is None:
            logger.warning(
                "Leaderboard refresh: auth did not name account holder %s; "
                "left out of the ranking.",
                holder_id,
            )
            continue
        if user.is_suspended:
            continue
        eligible.append((user, net_worth))

    # "Ranks run 1..N by net worth descending, then `lower(username)`
    # ascending, decided in Python at refresh": never the database's collation.
    eligible.sort(key=_rank_order)
    return [
        _RankedUser(rank=rank, user_id=user.id, username=user.username, net_worth=net_worth)
        for rank, (user, net_worth) in enumerate(eligible, start=1)
    ]


async def _has_generation_at_least_as_new(
    session: AsyncSession, computed_at: datetime
) -> bool:
    newer = await session.execute(
        select(LeaderboardGeneration.id)
        .where(LeaderboardGeneration.computed_at >= computed_at)
        .limit(1)
    )
    return newer.first() is not None


async def _insert_generation(
    session: AsyncSession, computed_at: datetime, ranking: list[_RankedUser]
) -> None:
    """The generation and every one of its entries, uncommitted."""
    generation_id = uuid.uuid4()
    await session.execute(
        insert(LeaderboardGeneration).values(
            id=generation_id, computed_at=computed_at, ranked_users=len(ranking)
        )
    )
    if not ranking:
        return
    await session.execute(
        insert(LeaderboardEntry),
        [
            {
                "generation_id": generation_id,
                "rank": ranked.rank,
                "user_id": ranked.user_id,
                "username": ranked.username,
                "net_worth": ranked.net_worth,
            }
            for ranked in ranking
        ],
    )


async def _prune_older_generations(session: AsyncSession) -> None:
    """Delete every generation but the newest `_GENERATIONS_KEPT` by
    `computed_at`. Their entries go with them, by the foreign key's cascade."""
    kept = (
        select(LeaderboardGeneration.id)
        .order_by(LeaderboardGeneration.computed_at.desc())
        .limit(_GENERATIONS_KEPT)
    )
    await session.execute(
        delete(LeaderboardGeneration).where(LeaderboardGeneration.id.not_in(kept))
    )


async def _write_generation(
    session_factory: async_sessionmaker[AsyncSession],
    computed_at: datetime,
    ranking: list[_RankedUser],
) -> None:
    """Store `ranking` as a new generation and prune, in one transaction,
    unless a generation at least as new exists. Commits."""
    async with session_factory() as session:
        # "A refresh writes under a transaction-level advisory lock, and writes
        # nothing if a generation at least as new exists". The lock comes
        # before the guard's read: ADR 0015, the read that decides the write is
        # issued after the lock. Released at commit or rollback.
        await session.execute(select(func.pg_advisory_xact_lock(LEADERBOARD_REFRESH_LOCK_KEY)))
        if await _has_generation_at_least_as_new(session, computed_at):
            logger.info(
                "Leaderboard refresh stamped %s wrote nothing: a generation at "
                "least as new exists.",
                computed_at.isoformat(),
            )
            await session.commit()
            return

        # "The leaderboard is stored as generations, and a refresh swaps the
        # whole ranking in one commit": insert, then prune, then commit, so a
        # reader sees the previous ranking or this one, whole.
        await _insert_generation(session, computed_at, ranking)
        await _prune_older_generations(session)
        await session.commit()


def _page_query(
    generation_id: uuid.UUID | None,
    last_rank: int | None,
    *,
    caller_id: uuid.UUID,
    limit: int,
) -> Select[Any]:
    """The served generation joined to its entries after `last_rank`, in rank
    order, `limit + 1` of them, and to the caller's own entry in it.

    An outer join, so a generation with no entries still comes back as one row
    with null entry columns, and its `computed_at` is served. The caller's
    entry is a second outer join: DECISIONS.md, "The caller's own row is read
    in the page's statement, from the generation served". Its condition never
    mentions `last_rank`, so a page past the end still carries `me`, and the
    unique `(generation_id, user_id)` index keeps it to one row, so it adds
    columns and never rows.
    """
    served_generation: uuid.UUID | ScalarSelect[uuid.UUID]
    if generation_id is not None:
        served_generation = generation_id
    else:
        served_generation = (
            select(LeaderboardGeneration.id)
            .order_by(LeaderboardGeneration.computed_at.desc())
            .limit(1)
            .scalar_subquery()
        )

    entries_on = LeaderboardEntry.generation_id == LeaderboardGeneration.id
    if last_rank is not None:
        entries_on = and_(entries_on, LeaderboardEntry.rank > last_rank)

    callers_entry = aliased(LeaderboardEntry)
    callers_entry_on = and_(
        callers_entry.generation_id == LeaderboardGeneration.id,
        callers_entry.user_id == caller_id,
    )

    return (
        select(
            LeaderboardGeneration.id.label("generation_id"),
            LeaderboardGeneration.computed_at,
            LeaderboardGeneration.ranked_users,
            LeaderboardEntry.rank,
            LeaderboardEntry.username,
            LeaderboardEntry.net_worth,
            (LeaderboardEntry.user_id == caller_id).label("is_me"),
            callers_entry.rank.label("me_rank"),
            callers_entry.username.label("me_username"),
            callers_entry.net_worth.label("me_net_worth"),
        )
        .select_from(LeaderboardGeneration)
        .outerjoin(LeaderboardEntry, entries_on)
        .outerjoin(callers_entry, callers_entry_on)
        .where(LeaderboardGeneration.id == served_generation)
        .order_by(LeaderboardEntry.rank)
        .limit(limit + 1)
    )


def _empty_board() -> LeaderboardPage:
    return LeaderboardPage(
        computed_at=None, stale=True, ranked_users=0, me=None, rows=[], next_cursor=None
    )


async def refresh(
    session_factory: async_sessionmaker[AsyncSession],
    auth_client: httpx.AsyncClient,
    clock: Callable[[], datetime] = _utc_now,
) -> None:
    """Value every USER account holder, name them through auth, and store the
    ranking as a new generation. [L-1] #38

    Raises `AuthUnavailable` if auth cannot name the holders, and
    `MarketBookIncomplete` for a book the valuation refuses; either way
    nothing is written. Takes no row lock, writes nothing outside the
    leaderboard's two tables, mints no grant, and commits its write.
    """
    # "A refresh's `computed_at` is Python's clock, read before the valuation;
    # `stale` is derived on read against the same clock": once, before any query.
    computed_at = clock()

    # ADR 0021: "The refresh reads and values the holders in one transaction,
    # ends it, then calls auth." The session is closed before the lookup.
    async with session_factory() as session:
        net_worths = await _list_net_worths(session)
        await release_before_calling_out(session, caller="leaderboard.refresh()")

    # The refresh-loop entry's step 3: auth's lookup, holding no connection.
    users = await user_directory.list_users(list(net_worths), auth_client)

    ranking = _ranking_of(net_worths, users)
    await _write_generation(session_factory, computed_at, ranking)


async def list_page(
    session: AsyncSession,
    *,
    caller_id: uuid.UUID,
    limit: int,
    cursor: str | None,
    now: datetime,
) -> LeaderboardPage:
    """One page of the stored ranking, in rank order, and the caller's own
    row in it. [L-1] #38, [BE][L-2] #269

    Without a cursor it reads the newest generation; with one, the generation
    the cursor names, and `me` comes from that same generation. Raises
    `MalformedCursor` before any query, and `LeaderboardRefreshed` when the
    named generation is not stored. Writes nothing, takes no lock and mints no
    grant. `now` is the request's one clock, read at the controller.
    """
    generation_id: uuid.UUID | None = None
    last_rank: int | None = None
    if cursor is not None:
        generation_id, last_rank = decode_rank_cursor(cursor)

    page_query = _page_query(generation_id, last_rank, caller_id=caller_id, limit=limit)
    fetched = (await session.execute(page_query)).all()
    if not fetched:
        if cursor is not None:
            raise LeaderboardRefreshed
        return _empty_board()

    generation = fetched[0]
    entries = [row for row in fetched if row.rank is not None]

    def cursor_of(row: Row[Any]) -> str:
        return encode_rank_cursor(generation.generation_id, row.rank)

    me = None
    if generation.me_rank is not None:
        me = LeaderboardStandingOut(
            rank=generation.me_rank,
            username=generation.me_username,
            net_worth=generation.me_net_worth,
        )

    page, next_cursor = page_cut_of(entries, limit, cursor_of=cursor_of)
    return LeaderboardPage(
        computed_at=generation.computed_at,
        stale=is_stale(
            generation.computed_at,
            now=now,
            refresh_seconds=get_settings().leaderboard_refresh_seconds,
        ),
        ranked_users=generation.ranked_users,
        me=me,
        rows=[
            LeaderboardRowOut(
                rank=row.rank,
                username=row.username,
                net_worth=row.net_worth,
                is_me=row.is_me,
            )
            for row in page
        ],
        next_cursor=next_cursor,
    )
