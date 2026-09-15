"""Reading a user's money. [B-2] #33, [4.1] #13

The public face of the ledger, and the only part of it with routes today.
HTTP is not mentioned in this file; failures are raised as domain errors.

Both functions below start by making sure the caller's starting grant exists.
That is not a convenience — it is where [B-1] #32's grant actually happens. See
`service/grants.py` for why a read is the right place for it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Select, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from core.paging import decode_cursor, encode_cursor
from model.entities import Account, Entry
from service import accounts, grants


@dataclass(frozen=True)
class UserBalance:
    """What a user holds, and the account it was derived from.

    Both, because every caller that wants one wants the other: the account id
    is what makes a balance traceable to the entries it came from, and looking
    it up separately would mean deriving the same account twice per request.
    """

    account_id: uuid.UUID
    amount: Decimal


@dataclass(frozen=True)
class EntryPage:
    """One page of a user's history, newest first."""

    entries: list[Entry]
    next_cursor: str | None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


async def balance_of_user(session: AsyncSession, user_id: uuid.UUID) -> UserBalance:
    """What this user holds right now. [B-2] #33.

    Derived from their entries every time, never read from a column. "The
    displayed balance is derived from or reconciled against the ledger" is
    therefore not something this service has to remember to do; it is the only
    thing it can do.
    """
    account = await _granted_account(session, user_id)
    amount = await accounts.balance_of(session, account.id)
    return UserBalance(account_id=account.id, amount=amount)


async def history_for_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    limit: int,
    cursor: str | None = None,
) -> EntryPage:
    """One page of this user's entries, newest first. [4.1] #13.

    `limit + 1` rows are fetched and the extra one is dropped, which is what
    makes `has_more` exact without a second COUNT over a table that only grows
    — and a count is the wrong question anyway, because the answer is stale by
    the time it renders. Same approach as the audit feed.

    What is deliberately not here: a running balance. [4.1] #13 asks for one and
    it is one aggregate away — the balance as at the newest entry on the page,
    then walk down subtracting each amount — but it belongs to the story that
    renders it, next to the user search it appears beside.
    """
    account = await _granted_account(session, user_id)

    stmt = _ordered_query(account.id)

    if cursor is not None:
        # Raises MalformedCursor, which the controller turns into a 400. Decoded
        # before the query rather than let through to the database, where it
        # would surface as a driver error and a 500.
        created_at, row_id = decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(Entry.created_at, Entry.id) < tuple_(created_at, row_id)
        )

    rows = list((await session.execute(stmt.limit(limit + 1))).scalars())

    if len(rows) <= limit:
        return EntryPage(entries=rows, next_cursor=None)

    page = rows[:limit]
    last = page[-1]
    return EntryPage(entries=page, next_cursor=encode_cursor(last.created_at, last.id))


async def _granted_account(session: AsyncSession, user_id: uuid.UUID) -> Account:
    """The user's account, with their starting credits in it. [B-1] #32."""
    return await grants.ensure_granted(session, user_id)


def _ordered_query(account_id: uuid.UUID) -> Select[tuple[Entry]]:
    """Newest first, with the tie broken by id.

    The ordering is half of the keyset contract and matches
    `ix_ledger_entries_account_feed`. Ordering by `created_at` alone would be
    worse here than in the audit log: both entries of a movement carry the
    identical timestamp by construction, so without the id the order between
    them is whatever Postgres feels like today, and a page boundary falling
    between them would drop one side of somebody's trade.
    """
    return (
        select(Entry)
        .where(Entry.account_id == account_id)
        .order_by(Entry.created_at.desc(), Entry.id.desc())
    )
