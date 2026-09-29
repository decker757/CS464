"""Reading a user's money. [B-2] #33, [4.1] #13

A user's own reads first mint their starting grant if it does not exist yet:
that is where [B-1] #32's grant happens (ADR 0009). An administrator's reads
of somebody else mint nothing (#188).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Row, Select, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from core.paging import Position, decode_cursor, encode_cursor
from core.pricing import QUANTUM, Side, per_share
from model.entities import Account, AccountKind, Entry, Transaction, TransactionKind
from model.schemas import TradeFields
from service import accounts, grants, trading

# Kind -> Side for the two trade kinds. Every other kind, the dict has nothing
# to say about, which is exactly "any other kind renders with them null".
_TRADE_SIDE_OF_KIND = {
    TransactionKind.TRADE_BUY: Side.BUY,
    TransactionKind.TRADE_SELL: Side.SELL,
}


@dataclass(frozen=True)
class UserBalance:
    """What a user holds, and the account it was derived from.

    `account_id` is None only on an administrator's read of an id this ledger
    has never opened an account for.
    """

    account_id: uuid.UUID | None
    amount: Decimal


@dataclass(frozen=True)
class HistoryRow:
    """One entry, and what the account held once it had landed. [4.1] #13.

    `balance_after` is derived per read and stored nowhere. `trade` is
    [T-5] #25's: filled in on a `trade_buy` or `trade_sell` row, all null on
    every other kind.
    """

    entry: Entry
    balance_after: Decimal
    trade: TradeFields


@dataclass(frozen=True)
class EntryPage:
    """One page of a user's history, newest first."""

    rows: list[HistoryRow]
    next_cursor: str | None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


_PageRow = tuple[
    uuid.UUID, uuid.UUID, Decimal, datetime, TransactionKind, dict[str, Any] | None, Decimal
]


def _trade_fields_of(transaction: Transaction, amount: Decimal) -> TradeFields:
    """market_id, outcome_id, side, quantity and average_price for one row.

    `side` comes from `kind`, never from `context` — `kind` is the column the
    ledger indexes and validates. The rest is read from `context` by
    `trading.result_of`, the one reader of a trade's context, so the history
    and the trade response cannot disagree about it. `quantity` is quantized
    to scale 4, because `context` keeps it exactly as the trader sent it.
    `average_price` is `per_share(|amount|, quantity)`, the same function the
    preview uses, so the two agree for the same trade at the same
    `state_version`.

    Every other kind — the grant, and anything this row builder does not
    recognise, `context` included — renders with all five fields null. Not an
    error: a new kind reaches the history the day it is written.
    """
    side = _TRADE_SIDE_OF_KIND.get(transaction.kind)
    if side is None or transaction.context is None:
        return TradeFields()

    trade = trading.result_of(transaction)
    quantity = trade.quantity.quantize(QUANTUM)
    return TradeFields(
        market_id=trade.market_id,
        outcome_id=trade.outcome_id,
        side=side,
        quantity=quantity,
        average_price=per_share(amount.copy_abs(), quantity),
    )


def _page_query(
    account_id: uuid.UUID, position: Position | None, limit_plus_one: int
) -> Select[_PageRow]:
    """The page, its transactions' `kind` and `context`, and every
    `balance_after` on it, in one statement anchored at the page's newest row.

    "The history page and its running balances are read in one statement,
    anchored at the page's newest row": `page` is `entries` joined to
    `transactions`, cursor-filtered and cut to the page — the join, not
    `Entry.transaction`'s `selectin` load, is what keeps `transactions` out of
    a second statement. `balance_after` is the account's balance at or before
    the page's own newest row (one aggregate over the full feed, read once),
    minus a window sum of the page's own newer rows walking back from there —
    cheap, because the window only ever sees this page's `limit + 1` rows.

    Known limitation (#187): two transactions on this account can commit in an
    order other than their timestamps show, so a position taken from the feed
    is not yet guaranteed to be the order they actually committed in.
    """
    conditions = [Entry.account_id == account_id]
    if position is not None:
        created_at, row_id = position
        conditions.append(tuple_(Entry.created_at, Entry.id) < tuple_(created_at, row_id))

    page = (
        select(
            Entry.id,
            Entry.transaction_id,
            Entry.amount,
            Entry.created_at,
            Transaction.kind,
            Transaction.context,
        )
        .join(Transaction, Transaction.id == Entry.transaction_id)
        .where(*conditions)
        .order_by(Entry.created_at.desc(), Entry.id.desc())
        .limit(limit_plus_one)
        .cte("history_page")
    )

    anchor_position = (
        select(page.c.created_at, page.c.id)
        .order_by(page.c.created_at.desc(), page.c.id.desc())
        .limit(1)
        .subquery("history_anchor_position")
    )

    # The full-feed sum through the page's newest row, `<=` because the
    # balance after an entry includes it. Row-wise on the feed's ordering
    # pair, so it agrees with the page boundary even between the two legs of
    # one movement, which share a timestamp.
    anchor_sum = (
        select(func.coalesce(func.sum(Entry.amount), accounts.ZERO))
        .where(
            Entry.account_id == account_id,
            tuple_(Entry.created_at, Entry.id)
            <= tuple_(anchor_position.c.created_at, anchor_position.c.id),
        )
        .scalar_subquery()
    )

    # Every row on the page newer than this one, summed over the page alone —
    # "a window sum of the newer rows on the page".
    newer_on_page = func.coalesce(
        func.sum(page.c.amount).over(
            order_by=(page.c.created_at.desc(), page.c.id.desc()),
            rows=(None, -1),
        ),
        accounts.ZERO,
    )

    return select(
        page.c.id,
        page.c.transaction_id,
        page.c.amount,
        page.c.created_at,
        page.c.kind,
        page.c.context,
        (anchor_sum - newer_on_page).label("balance_after"),
    ).order_by(page.c.created_at.desc(), page.c.id.desc())


def _history_row_of(account_id: uuid.UUID, row: Row[_PageRow]) -> HistoryRow:
    """One fetched row into a `HistoryRow`.

    `entry` and its transaction are built from the row rather than loaded,
    the same way `posting.post` builds a fresh pair, so `LedgerEntryOut.of`
    and `trading.result_of` can read them without a second query. Neither is
    added to the session.
    """
    transaction = Transaction(id=row.transaction_id, kind=row.kind, context=row.context)
    entry = Entry(
        id=row.id,
        transaction_id=row.transaction_id,
        account_id=account_id,
        amount=row.amount,
        created_at=row.created_at,
        transaction=transaction,
    )
    return HistoryRow(
        entry=entry,
        balance_after=row.balance_after,
        trade=_trade_fields_of(transaction, row.amount),
    )


async def _history_of_account(
    session: AsyncSession,
    account: Account | None,
    *,
    limit: int,
    cursor: str | None,
) -> EntryPage:
    """One page of this account's entries; an empty page for no account.

    Fetches `limit + 1` rows so `has_more` is exact without a COUNT. Raises
    `MalformedCursor`.
    """
    # Decoded before the query, and before the no-account answer, so a bad
    # cursor is a 400 on either route rather than a driver error or a 200.
    position = decode_cursor(cursor) if cursor is not None else None

    if account is None:
        return EntryPage(rows=[], next_cursor=None)

    fetched = list(
        (await session.execute(_page_query(account.id, position, limit + 1))).all()
    )

    page = fetched[:limit]
    next_cursor = (
        encode_cursor(page[-1].created_at, page[-1].id)
        if len(fetched) > limit
        else None
    )

    return EntryPage(
        rows=[_history_row_of(account.id, row) for row in page],
        next_cursor=next_cursor,
    )


async def balance_of_user(session: AsyncSession, user_id: uuid.UUID) -> UserBalance:
    """What this user holds right now, derived from their entries. [B-2] #33.

    Mints the starting grant if this is the user's first read. [B-1] #32.
    """
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
    left behind and, for a trade, the market it was in. [4.1] #13, [T-5] #25.

    Mints the starting grant if this is the user's first read. [B-1] #32.
    Raises `MalformedCursor`.
    """
    account = await grants.ensure_granted(session, user_id)
    return await _history_of_account(session, account, limit=limit, cursor=cursor)


async def balance_for_admin(session: AsyncSession, user_id: uuid.UUID) -> UserBalance:
    """Any user's balance, for an administrator. [4.1] #13. Writes nothing.

    Mints no grant: this ledger holds no user table, so it cannot tell a user's
    id from a market's or a typo, and a grant is permanent. An id with no
    account reads as zero, with `account_id` None. #188; DECISIONS.md, "An
    administrator's read of somebody else's balance or history mints nothing".
    """
    account = await accounts.find(session, AccountKind.USER, user_id)
    if account is None:
        return UserBalance(account_id=None, amount=Decimal(0).quantize(QUANTUM))

    amount = await accounts.balance_of(session, account.id)
    return UserBalance(account_id=account.id, amount=amount)


async def history_for_admin(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    limit: int,
    cursor: str | None = None,
) -> EntryPage:
    """Any user's history, for an administrator. [4.1] #13. Writes nothing.

    Mints no grant, for the reason `balance_for_admin` gives: an id with no
    account reads as an empty page. Raises `MalformedCursor`.
    """
    account = await accounts.find(session, AccountKind.USER, user_id)
    return await _history_of_account(session, account, limit=limit, cursor=cursor)
