"""The write path: `post` moves credits. [F-1] #41, ADR 0009.

Every movement is legs that sum to zero, keyed for idempotency and
fingerprinted so a reused key is told apart from a retry, and serialised on
account row locks so concurrent trades cannot overdraw an account.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import has_pending_writes
from core.errors import (
    IdempotencyKeyReused,
    InsufficientFunds,
    PendingWritesOnReplay,
    UnbalancedTransaction,
)
from model.entities import (
    AMOUNT_SCALE,
    Account,
    AccountKind,
    Entry,
    Transaction,
    TransactionKind,
)
from service import accounts

# Every amount is rounded to the column's scale before it is compared,
# checked or hashed. A check on an unrounded value checks a number Postgres is
# about to change: 0.00005 balances in Python and lands as 0.0001.
_QUANTUM = Decimal(1).scaleb(-AMOUNT_SCALE)

ZERO = Decimal(0)


@dataclass(frozen=True)
class Leg:
    """One side of a movement: an account, and what happens to it.

    Negative is a debit, positive a credit. A transaction is a list of these
    that sums to zero.
    """

    account: Account
    amount: Decimal


def _quantize(amount: Decimal) -> Decimal:
    """Round half up, as Postgres numeric does, not banker's rounding: a
    retry rounded the other way would not match the stored fingerprint.
    """
    return amount.quantize(_QUANTUM, rounding=ROUND_HALF_UP)


def _require_balanced(legs: list[Leg]) -> None:
    """Refuse legs that do not sum to zero, fewer than two legs, or a zero leg
    (which `ck_entries_amount_nonzero` refuses anyway). ADR 0009.
    """
    if len(legs) < 2 or any(leg.amount == ZERO for leg in legs):
        raise UnbalancedTransaction
    if sum((leg.amount for leg in legs), ZERO) != ZERO:
        raise UnbalancedTransaction


def _fingerprint(kind: TransactionKind, legs: list[Leg]) -> str:
    """A stable hash of what this transaction does.

    Sorted by account id so leg order does not matter to a retry. Amounts are
    already quantized, so values Postgres stores as one hash as one.
    """
    parts = sorted(f"{leg.account.id}:{leg.amount}" for leg in legs)
    raw = "|".join([kind.value, *parts])
    return hashlib.sha256(raw.encode()).hexdigest()


def _replay(existing: Transaction, fingerprint: str) -> Transaction:
    """The existing transaction if the fingerprint matches, else raise
    `IdempotencyKeyReused`: a retry replays, a reused key is refused (ADR 0009).
    """
    if existing.request_fingerprint != fingerprint:
        raise IdempotencyKeyReused
    return existing


def _net_by_account(legs: list[Leg]) -> dict[uuid.UUID, tuple[Account, Decimal]]:
    """Each account's net movement, keyed on the account id, not the object:
    legs assembled from two queries would otherwise be netted in halves.
    """
    netted: dict[uuid.UUID, tuple[Account, Decimal]] = {}
    for leg in legs:
        _, running = netted.get(leg.account.id, (leg.account, ZERO))
        netted[leg.account.id] = (leg.account, running + leg.amount)
    return netted


async def _refuse_overdrafts(session: AsyncSession, legs: list[Leg]) -> None:
    """Raise `InsufficientFunds` if any USER account would end below zero.

    Netted per account, and only accounts losing credits are read. An
    allowlist on USER, not a list of exempt kinds: PLATFORM and MARKET_POOL
    are meant to go negative (ADR 0009, D-009). Adding a kind here means
    deciding it holds spendable money.
    """
    for account, delta in _net_by_account(legs).values():
        if delta >= ZERO or account.kind is not AccountKind.USER:
            continue

        balance = await accounts.balance_of(session, account.id)
        if balance + delta < ZERO:
            raise InsufficientFunds(balance=balance, required=-delta)


async def find_by_idempotency_key(
    session: AsyncSession, idempotency_key: str
) -> Transaction | None:
    """The transaction this key already named, or None.

    Public for `grants.ensure_granted`, which must ask whether a movement
    happened without building its legs; its docstring has the case.
    """
    stmt = select(Transaction).where(Transaction.idempotency_key == idempotency_key)
    return (await session.execute(stmt)).scalar_one_or_none()


async def post(
    session: AsyncSession,
    *,
    idempotency_key: str,
    kind: TransactionKind,
    legs: list[Leg],
    context: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Transaction:
    """Record one movement of credits, or replay the one this key already named.

    Commits before it returns, on success and on a replay, so a caller may
    write nothing after it (D-032). Otherwise it raises before any commit:
    `UnbalancedTransaction`, `IdempotencyKeyReused`, `InsufficientFunds`, or
    `PendingWritesOnReplay` when a replay would commit the caller's pending
    writes.

    The order is the design: round, refuse unbalanced legs, lock every account
    ascending by id (ADR 0009), then look the key up and check overdrafts under
    that lock. Looked up before the lock, a retry racing its original is
    refused as an overdraft for a trade that succeeded (ADR 0015).

    `now` is injectable so a test can assert on a timestamp.
    """
    now = now or datetime.now(UTC)
    legs = [Leg(account=leg.account, amount=_quantize(leg.amount)) for leg in legs]

    _require_balanced(legs)
    fingerprint = _fingerprint(kind, legs)

    # Recorded on entry, not asked at each replay branch: the second is reached
    # after the SAVEPOINT's rollback has expired the caller's writes, so the
    # session looks clean there. DECISIONS.md, "`posting.post` refuses to
    # replay into a dirty session".
    caller_pending = has_pending_writes(session)

    await accounts.lock(session, [leg.account.id for leg in legs])

    existing = await find_by_idempotency_key(session, idempotency_key)
    if existing is not None:
        # The commit below would flush the caller's pending writes (a trade's
        # `q` and position) beside a transaction that wrote nothing. Raised
        # before it: nothing can discard only the caller's writes.
        if caller_pending:
            raise PendingWritesOnReplay

        # Committed although nothing was written, to release the account
        # locks rather than hold them for the rest of the request. ADR 0015.
        transaction = _replay(existing, fingerprint)
        await session.commit()
        return transaction

    await _refuse_overdrafts(session, legs)

    transaction = Transaction(
        kind=kind,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        occurred_at=now,
        context=context,
    )

    try:
        async with session.begin_nested():
            session.add(transaction)
            session.add_all(
                # created_at copied from the transaction, not defaulted per
                # row, so both legs share one timestamp and a history page
                # cannot split them.
                Entry(
                    transaction=transaction,
                    account_id=leg.account.id,
                    amount=leg.amount,
                    created_at=now,
                )
                for leg in legs
            )
            await session.flush()
    except IntegrityError:
        # A race on one key that the account locks did not queue, so the legs
        # share no account: a key reused for different money. Re-read so
        # `_replay` refuses it; the savepoint keeps the caller's earlier work.
        existing = await find_by_idempotency_key(session, idempotency_key)
        if existing is None:
            raise
        # Same refusal: the rollback discarded the caller's writes, but it
        # would still get another request's transaction back as its own.
        if caller_pending:
            raise PendingWritesOnReplay
        transaction = _replay(existing, fingerprint)

    await session.commit()
    return transaction
