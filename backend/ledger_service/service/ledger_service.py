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
from model.entities import Entry
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
class HistoryRow:
    """One entry, and what the account held once it had landed. [4.1] #13.

    The two together, because a statement line that says "-250" without saying
    what was left is half an answer to the question an administrator opened the
    page to ask. `balance_after` is derived per read and stored nowhere; see
    `accounts.balance_of` for why that is the design rather than a shortcut.
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


async def balance_of_user(session: AsyncSession, user_id: uuid.UUID) -> UserBalance:
    """What this user holds right now. [B-2] #33.

    Derived from their entries every time, never read from a column. "The
    displayed balance is derived from or reconciled against the ledger" is
    therefore not something this service has to remember to do; it is the only
    thing it can do.
    """
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

    `limit + 1` rows are fetched and the extra one is dropped, which is what
    makes `has_more` exact without a second COUNT over a table that only grows
    — and a count is the wrong question anyway, because the answer is stale by
    the time it renders. Same approach as the audit feed.

    The running balance costs exactly one more aggregate per page, not one per
    row: sum the account up to the newest entry on the page, then walk down
    subtracting each amount, because the balance before an entry is the balance
    after it minus what it moved. Reading it back per row would be `limit`
    round trips for arithmetic already in hand.
    """
    # Mints the starting grant if this is the user's first read. [B-1] #32.
    account = await grants.ensure_granted(session, user_id)

    stmt = _ordered_query(account.id)

    if cursor is not None:
        # Raises MalformedCursor, which the controller turns into a 400. Decoded
        # before the query rather than let through to the database, where it
        # would surface as a driver error and a 500.
        created_at, row_id = decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(Entry.created_at, Entry.id) < tuple_(created_at, row_id)
        )

    entries = list((await session.execute(stmt.limit(limit + 1))).scalars())

    # The extra row is the whole of `has_more`: fetched, counted, and dropped
    # before anything else looks at the page.
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


async def _with_running_balance(
    session: AsyncSession, account_id: uuid.UUID, entries: list[Entry]
) -> list[HistoryRow]:
    """Pair each entry with what the account held once it had landed.

    One query for the whole page, anchored at its newest row, then subtraction.
    The anchor is what makes a page's figures independent of when it was
    fetched: entries appended mid-read are strictly newer than it and so are
    outside every sum on this page. Two administrators paging through one
    account at different speeds see the same numbers beside the same entries.

    The top row's balance is also, by construction, what
    `GET /ledger/users/{id}/balance` returns when nothing has moved since —
    both are `SUM(amount)` over the same rows, which is [4.1] #13's third
    acceptance criterion holding because there is only one way to ask.
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
        # Walking backwards through time, so undo what this entry did to get
        # the balance the next one down left behind.
        running -= entry.amount

    return rows


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
