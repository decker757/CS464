"""Reading a user's money. [B-2] #33, [4.1] #13

Both reads first mint the caller's starting grant if it does not exist yet:
that is where [B-1] #32's grant happens (ADR 0009).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Select, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from core.paging import decode_cursor, encode_cursor
from model.entities import Entry
from service import accounts, grants


@dataclass(frozen=True)
class UserBalance:
    """What a user holds, and the account it was derived from."""

    account_id: uuid.UUID
    amount: Decimal


@dataclass(frozen=True)
class HistoryRow:
    """One entry, and what the account held once it had landed. [4.1] #13.

    `balance_after` is derived per read and stored nowhere.
    """

    entry: Entry
    balance_after: Decimal


@dataclass(frozen=True)
class EntryPage:
    """One page of a user's history, newest first."""

    rows: list[HistoryRow]
    next_cursor: str | None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


def _ordered_query(account_id: uuid.UUID) -> Select[tuple[Entry]]:
    """Newest first, tie broken by id, matching `ix_ledger_entries_account_feed`.

    Both legs of a movement share a timestamp, so without the id a page
    boundary could drop one side of a trade.
    """
    return (
        select(Entry)
        .where(Entry.account_id == account_id)
        .order_by(Entry.created_at.desc(), Entry.id.desc())
    )


async def _with_running_balance(
    session: AsyncSession, account_id: uuid.UUID, entries: list[Entry]
) -> list[HistoryRow]:
    """Pair each entry with what the account held once it had landed.

    One aggregate anchored at the page's newest row, then subtraction walking
    back. The anchor keeps a page's figures fixed while entries are appended.
    """
    if not entries:
        return []

    newest = entries[0]
    running = await accounts.balance_of(
        session, account_id, as_at=(newest.created_at, newest.id)
    )

    rows: list[HistoryRow] = []
    for entry in entries:
        rows.append(HistoryRow(entry=entry, balance_after=running))
        # Walking back in time: undo this entry to get the next one's balance.
        running -= entry.amount

    return rows


async def balance_of_user(session: AsyncSession, user_id: uuid.UUID) -> UserBalance:
    """What this user holds right now, derived from their entries. [B-2] #33."""
    # Mints the starting grant if this is the user's first read. [B-1] #32.
    account = await grants.ensure_granted(session, user_id)
    amount = await accounts.balance_of(session, account.id)
    return UserBalance(account_id=account.id, amount=amount)


async def history_for_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    limit: int,
    cursor: str | None = None,
) -> EntryPage:
    """One page of this user's entries, newest first, each with the balance it
    left behind. [4.1] #13.

    Fetches `limit + 1` rows so `has_more` is exact without a COUNT. Raises
    `MalformedCursor`.
    """
    # Mints the starting grant if this is the user's first read. [B-1] #32.
    account = await grants.ensure_granted(session, user_id)

    stmt = _ordered_query(account.id)

    if cursor is not None:
        # Decoded before the query, so a bad cursor is a 400 rather than a
        # driver error.
        created_at, row_id = decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(Entry.created_at, Entry.id) < tuple_(created_at, row_id)
        )

    entries = list((await session.execute(stmt.limit(limit + 1))).scalars())

    page = entries[:limit]
    next_cursor = (
        encode_cursor(page[-1].created_at, page[-1].id)
        if len(entries) > limit
        else None
    )

    return EntryPage(
        rows=await _with_running_balance(session, account.id, page),
        next_cursor=next_cursor,
    )
