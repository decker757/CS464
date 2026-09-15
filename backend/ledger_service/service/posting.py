"""The write path. [F-1] #41

One function, `post`, and everything the issue asks for is in it: matching
debit and credit rows, an idempotency key, and enough serialization that
concurrent trades cannot overdraw an account.

HTTP is not mentioned here, and there is no route that reaches this function
yet. [T-2] #22 is the ticket with a caller — a trading service that prices a
trade and then issues one transactional write — and it is also the ticket that
has to decide how such a caller authenticates, because a trader's own token
reaching a write endpoint would let anyone post themselves credits. That
decision belongs with the caller. What lands here is the primitive, the rules,
and the tests that hold them, so the endpoint is a route rather than a second
opinion about what a transaction is.

Its first real caller is already in this repository: `service/grants.py` mints
the starting grant through exactly this path, so the idempotency and the
double entry are exercised end to end rather than only in the suite.
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

from core.errors import (
    IdempotencyKeyReused,
    InsufficientFunds,
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

# The unit every amount is rounded to before it is compared, checked or
# hashed. Numeric(18, 4) rounds on the way in regardless, and a check performed
# against an unrounded value is a check against a number the database is about
# to change: 0.00005 would pass a "the legs balance" test in Python and arrive
# in Postgres as 0.0001, one hundredth of a cent that no later sum can account
# for. Same trap `model/schemas.py` in the market service documents for its
# pricing columns.
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


async def post(
    session: AsyncSession,
    *,
    idempotency_key: str,
    kind: TransactionKind,
    legs: list[Leg],
    context: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Transaction:
    """Record one movement of credits, or return the one this key already named.

    Committed by the time it returns, like every other write in this
    repository's service layers.

    The order below is the design:

    1. **Round first.** Everything after this compares stored values.
    2. **Refuse legs that do not sum to zero.** Credits move; they are not
       created. A caller whose legs do not balance has a bug, and letting it
       through would make the ledger unable to prove anything about itself.
    3. **Replay an existing key.** Identical request, identical answer, nothing
       written. A different request under the same key is refused rather than
       answered with somebody else's transaction.
    4. **Lock every account named, ascending by id**, before reading a single
       balance. This is the step that makes the check meaningful: without it,
       two concurrent buys both read the same balance and both pass.
    5. **Check for overdrafts against the balance derived under that lock.**
    6. **Write the transaction and its entries, and commit.**

    `now` is injectable so a test can assert on a timestamp without freezing
    the system clock, the same as `service/validation.py` in the market service.
    """
    now = now or datetime.now(UTC)
    legs = [Leg(account=leg.account, amount=_quantize(leg.amount)) for leg in legs]

    _require_balanced(legs)
    fingerprint = _fingerprint(kind, legs)

    existing = await _by_idempotency_key(session, idempotency_key)
    if existing is not None:
        return _replay(existing, fingerprint)

    await accounts.lock(session, [leg.account.id for leg in legs])
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
                # created_at copied from the transaction rather than defaulted
                # per row, so both halves of one movement carry the identical
                # timestamp and a history page cannot split them.
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
        # Two callers raced on one key. Both looked it up and found nothing,
        # and the unique index caught the loser. The winner's row is the answer
        # the loser wanted, so this is a replay rather than a failure — the
        # same reasoning as the insert race in `market_service.save`, with a
        # savepoint so the caller's earlier work survives.
        existing = await _by_idempotency_key(session, idempotency_key)
        if existing is None:
            raise
        transaction = _replay(existing, fingerprint)

    await session.commit()
    return transaction


async def _by_idempotency_key(
    session: AsyncSession, idempotency_key: str
) -> Transaction | None:
    stmt = select(Transaction).where(Transaction.idempotency_key == idempotency_key)
    return (await session.execute(stmt)).scalar_one_or_none()


def _replay(existing: Transaction, fingerprint: str) -> Transaction:
    """Hand back the transaction this key already named, if it is the same one.

    The fingerprint is what separates a retry from a mistake. A caller that
    resends an identical request wants the original answer and gets it. A
    caller that reuses a key for different money is told so, because the
    alternative is reporting that their new trade succeeded and showing them an
    old one as evidence.
    """
    if existing.request_fingerprint != fingerprint:
        raise IdempotencyKeyReused
    return existing


def _require_balanced(legs: list[Leg]) -> None:
    """Every transaction's entries sum to zero. The invariant, enforced once.

    Two legs at minimum, because one leg that sums to zero is an amount of
    zero, which `ck_entries_amount_nonzero` refuses anyway and which says
    nothing when it appears on somebody's statement.
    """
    if len(legs) < 2 or any(leg.amount == ZERO for leg in legs):
        raise UnbalancedTransaction
    if sum((leg.amount for leg in legs), ZERO) != ZERO:
        raise UnbalancedTransaction


async def _refuse_overdrafts(session: AsyncSession, legs: list[Leg]) -> None:
    """No USER account may end below zero. [T-2] #22.

    Netted per account before the check, so a transaction that debits and
    credits the same account is judged on what it actually does to it.

    Only accounts losing credits are read. An account that only gains cannot
    be overdrawn by this transaction, and a balance query per leg would double
    the work of every trade for an answer nobody uses.

    PLATFORM is exempt, and that is the point of it: its balance is minus the
    credits in circulation, so the house being negative is the ledger working.
    Only a USER account holds money somebody could spend twice.
    """
    for account, delta in _net_by_account(legs).values():
        if delta >= ZERO or account.kind is not AccountKind.USER:
            continue

        balance = await accounts.balance_of(session, account.id)
        if balance + delta < ZERO:
            raise InsufficientFunds(balance=balance, required=-delta)


def _net_by_account(legs: list[Leg]) -> dict[uuid.UUID, tuple[Account, Decimal]]:
    """Keyed on the account id rather than the object.

    One session hands out one object per row, so keying on identity would
    usually work. It would stop working the day a caller assembles its legs
    from two queries, and it would stop working silently, by checking one half
    of an account's movement against a balance and ignoring the other.
    """
    netted: dict[uuid.UUID, tuple[Account, Decimal]] = {}
    for leg in legs:
        _, running = netted.get(leg.account.id, (leg.account, ZERO))
        netted[leg.account.id] = (leg.account, running + leg.amount)
    return netted


def _fingerprint(kind: TransactionKind, legs: list[Leg]) -> str:
    """A stable hash of what this transaction does.

    Sorted by account id so that the same movement described in a different leg
    order hashes the same — a retry should not depend on a caller building its
    list identically twice. The amounts are already quantized, so two Decimals
    that Postgres would store as one value hash as one value.
    """
    parts = sorted(f"{leg.account.id}:{leg.amount}" for leg in legs)
    raw = "|".join([kind.value, *parts])
    return hashlib.sha256(raw.encode()).hexdigest()


def _quantize(amount: Decimal) -> Decimal:
    """Round the way the column will, not the way Python prefers.

    Decimal's default is banker's rounding and Postgres numeric rounds ties
    away from zero. The difference is a hundredth of a cent on a value nobody
    should be sending anyway, but it would land in the fingerprint, so a retry
    that rounded one way would not match a transaction stored the other.
    """
    return amount.quantize(_QUANTUM, rounding=ROUND_HALF_UP)
