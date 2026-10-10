"""Paying out an approved market. [3.4] #12, ADR 0019.

One request, in this order, and the order is the design (ADR 0019 as amended
2026-10-10, DECISIONS.md's 2026-10-10 entries):

1. The public detail, through `market_status.read_terms`, holding no
   connection. A failure refuses here, even for a market already paid.
2. The record, looked up without a lock. A hit is a replay: the figures are
   rebuilt from the entries, the session rolls back, and market_service is
   told again. Whatever status step 1 reported.
3. Only with no record, the status: `approved` and `settleable`, or `settled`.
   A refusal writes nothing and opens no book.
4. `books.ensure_open`, `books.lock_book`, and the record again, under the
   lock. A hit is a replay, and its rollback releases the lock.
5. The winner, against the book's own outcomes. Then the clock, read after
   the lock.
6. One payout per winner in `user_id` order, then the residue. The record (an
   ORM add) and the audit entry are staged, and `posting.post_all` commits
   all of it once.
7. `market_terms.mark_settled`, with no statement between the commit and it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import DisputeWindowOpen, MarketNotApproved, MarketTermsUnavailable
from core.pricing import QUANTUM
from model.audit import AdminAction
from model.entities import (
    Account,
    AccountKind,
    Entry,
    MarketBook,
    MarketOutcome,
    MarketResult,
    Position,
    ResultKind,
    Transaction,
    TransactionKind,
)
from service import accounts, audit, books, market_status, market_terms, posting
from service.audit import Actor
from service.market_terms import MarketTerms
from service.posting import Leg, Movement

ZERO = Decimal(0)

# Restated rather than imported, as `market_status._OPEN` is (see
# `books.MIN_OUTCOMES`): the two statuses a settlement may proceed against.
_APPROVED = "approved"
_SETTLED = "settled"


@dataclass(frozen=True)
class SettlementResult:
    """What one settlement paid, the same on the first request and on every
    repeat. `residue` is signed: positive went to PLATFORM, negative came
    from it."""

    market_id: uuid.UUID
    outcome_id: uuid.UUID
    recorded_at: datetime
    holders_paid: int
    total_paid: Decimal
    residue: Decimal


@dataclass(frozen=True)
class _Winner:
    """One holder of the winning outcome, and their USER account."""

    user_id: uuid.UUID
    quantity: Decimal
    account: Account


def _payout_key(market_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return f"settlement:{market_id}:{user_id}"


def _residue_key(market_id: uuid.UUID) -> str:
    return f"settlement_residue:{market_id}"


def _result_of(
    market_id: uuid.UUID,
    *,
    outcome_id: uuid.UUID,
    recorded_at: datetime,
    holders_paid: int,
    total_paid: Decimal,
    residue: Decimal,
) -> SettlementResult:
    """The one builder, for the fresh path and both replays, so the two cannot
    drift. Money at the column's scale, so a zero rebuilt from no entries
    reads as the fresh path's `0.0000`."""
    return SettlementResult(
        market_id=market_id,
        outcome_id=outcome_id,
        recorded_at=recorded_at,
        holders_paid=holders_paid,
        total_paid=total_paid.quantize(QUANTUM),
        residue=residue.quantize(QUANTUM),
    )


async def _replay(
    session: AsyncSession,
    market_id: uuid.UUID,
    record: MarketResult,
    *,
    access_token: str,
    terms_client: httpx.AsyncClient,
) -> SettlementResult:
    """Answer a recorded settlement from its entries, write nothing, and tell
    market_service again.

    One `GROUP BY` kind over the pool's settlement entries: the count and sum
    of the payout legs, and the residue leg. The record's own values are
    taken before the rollback, which expires it; the rollback also releases
    the book lock when a locked lookup found the record. No statement runs
    between the rollback and the call. Raises `SettlementUnconfirmed` from
    `mark_settled`.
    """
    outcome_id = record.outcome_id
    recorded_at = record.recorded_at
    assert outcome_id is not None  # a settled row names its winner, by CHECK

    stmt = (
        select(Transaction.kind, func.count(Entry.id), func.sum(Entry.amount))
        .join(Entry, Entry.transaction_id == Transaction.id)
        .join(MarketBook, MarketBook.pool_account_id == Entry.account_id)
        .where(
            MarketBook.market_id == market_id,
            Transaction.kind.in_(
                [TransactionKind.SETTLEMENT, TransactionKind.SETTLEMENT_RESIDUE]
            ),
        )
        .group_by(Transaction.kind)
    )
    by_kind = {
        kind: (count, total) for kind, count, total in (await session.execute(stmt)).all()
    }
    holders_paid, paid_from_pool = by_kind.get(TransactionKind.SETTLEMENT, (0, ZERO))
    _, residue_from_pool = by_kind.get(TransactionKind.SETTLEMENT_RESIDUE, (0, ZERO))

    result = _result_of(
        market_id,
        outcome_id=outcome_id,
        recorded_at=recorded_at,
        holders_paid=holders_paid,
        total_paid=-paid_from_pool,
        residue=-residue_from_pool,
    )

    await session.rollback()
    await market_terms.mark_settled(
        market_id, access_token=access_token, terms_client=terms_client
    )
    return result


def _refuse_if_not_settleable(terms: MarketTerms) -> None:
    """Raise unless market_service reports `approved` and `settleable`, or
    `settled`. Asked only when the ledger holds no record: a recorded
    settlement replays whatever the status says (ADR 0019, 2026-10-10)."""
    if terms.status == _APPROVED and not terms.settleable:
        raise DisputeWindowOpen
    if terms.status not in (_APPROVED, _SETTLED):
        raise MarketNotApproved


async def _read_outcome_ids(
    session: AsyncSession, market_id: uuid.UUID
) -> list[uuid.UUID]:
    stmt = select(MarketOutcome.outcome_id).where(MarketOutcome.market_id == market_id)
    return list((await session.execute(stmt)).scalars())


async def _refuse_if_foreign_winner(
    session: AsyncSession, market_id: uuid.UUID, winner: uuid.UUID | None
) -> None:
    """Unless the winner is one of the book's own outcomes, roll back, which
    releases the book lock, and raise `MarketTermsUnavailable`:
    market_service is wrong, and the record's foreign key would only fail
    later as a 500. DECISIONS.md, "The winner is checked under the book lock,
    after the latch"."""
    if winner is None or winner not in await _read_outcome_ids(session, market_id):
        await session.rollback()
        raise MarketTermsUnavailable


async def _list_winners(
    session: AsyncSession, market_id: uuid.UUID, outcome_id: uuid.UUID
) -> Sequence[_Winner]:
    """Every holder of the winning outcome with shares left, and their USER
    account, in `user_id` order. One statement; the book lock holds the
    positions still, since every writer of a position takes it first."""
    stmt = (
        select(Position.user_id, Position.quantity, Account)
        .join(
            Account,
            (Account.kind == AccountKind.USER) & (Account.owner_id == Position.user_id),
        )
        .where(
            Position.market_id == market_id,
            Position.outcome_id == outcome_id,
            Position.quantity > 0,
        )
        .order_by(Position.user_id)
    )
    return [
        _Winner(user_id=user_id, quantity=quantity, account=account)
        for user_id, quantity, account in (await session.execute(stmt)).all()
    ]


def _payout_of(
    market_id: uuid.UUID, outcome_id: uuid.UUID, winner: _Winner, pool: Account
) -> Movement:
    """One winner's payout: their quantity × 1, from the pool."""
    return Movement(
        idempotency_key=_payout_key(market_id, winner.user_id),
        kind=TransactionKind.SETTLEMENT,
        legs=[
            Leg(account=pool, amount=-winner.quantity),
            Leg(account=winner.account, amount=winner.quantity),
        ],
        context={
            "user_id": str(winner.user_id),
            "market_id": str(market_id),
            "outcome_id": str(outcome_id),
            "quantity": str(winner.quantity),
        },
    )


def _residue_of(
    market_id: uuid.UUID, residue: Decimal, *, pool: Account, platform: Account
) -> Movement:
    """What the pool still holds after the payouts, to PLATFORM, or what it
    lacks, from PLATFORM. The pool ends at zero either way."""
    return Movement(
        idempotency_key=_residue_key(market_id),
        kind=TransactionKind.SETTLEMENT_RESIDUE,
        legs=[
            Leg(account=pool, amount=-residue),
            Leg(account=platform, amount=residue),
        ],
        context={"market_id": str(market_id)},
    )


async def pay_out(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    actor: Actor,
    access_token: str,
    terms_client: httpx.AsyncClient,
    now: datetime | None = None,
) -> SettlementResult:
    """Pay every winning share of `market_id` once, or answer the settlement
    already recorded, and tell market_service.

    `access_token` is the administrator's own, forwarded to both calls.
    Commits once, through `posting.post_all`, after `books.ensure_open`'s own
    commit on a cold book. Raises `MarketNotApproved`, `DisputeWindowOpen`,
    `MarketTermsUnavailable`, what `market_status.read_terms` raises, and
    `SettlementUnconfirmed` after the payouts have committed. **Call this with
    nothing pending on the session.** `now` is injectable for tests.
    """
    # 1. The read, before any statement.
    terms = await market_status.read_terms(
        session, market_id, access_token=access_token, terms_client=terms_client
    )

    # 2. The unlocked lookup. A hit names a committed row nothing updates or
    # deletes, so it can only short-circuit; a miss is trusted for nothing.
    record = await books.find_result(session, market_id)
    if record is not None:
        return await _replay(
            session,
            market_id,
            record,
            access_token=access_token,
            terms_client=terms_client,
        )

    # 3. Before `ensure_open`, whose cold path commits a seed.
    _refuse_if_not_settleable(terms)

    # 4. The book, opened on a first touch in its own commit, then locked, and
    # the record looked for again under the lock (ADR 0015).
    await books.ensure_open(
        session, market_id, access_token=access_token, terms_client=terms_client
    )
    book = await books.lock_book(session, market_id)
    record = await books.find_result(session, market_id)
    if record is not None:
        return await _replay(
            session,
            market_id,
            record,
            access_token=access_token,
            terms_client=terms_client,
        )

    # 5. A replay never reads this: on a repeat, the recorded outcome governs.
    await _refuse_if_foreign_winner(session, market_id, terms.proposed_outcome_id)
    outcome_id = terms.proposed_outcome_id
    assert outcome_id is not None  # refused above

    # After the lock, so a settlement that queued behind a trade is not
    # stamped before it (#138). A floor for `post_all`, not its stamp.
    now = now if now is not None else datetime.now(UTC)

    # 6. The money. The residue is computed from the payouts, so it goes last.
    winners = await _list_winners(session, market_id, outcome_id)
    pool = await session.get(Account, book.pool_account_id)
    assert pool is not None
    pool_balance = await accounts.balance_of(session, pool.id)

    total_paid = sum((winner.quantity for winner in winners), ZERO)
    residue = pool_balance - total_paid

    movements = [_payout_of(market_id, outcome_id, winner, pool) for winner in winners]
    if residue != ZERO:
        platform = await accounts.ensure_platform(session)
        movements.append(_residue_of(market_id, residue, pool=pool, platform=platform))

    result = _result_of(
        market_id,
        outcome_id=outcome_id,
        recorded_at=now,
        holders_paid=len(winners),
        total_paid=total_paid,
        residue=residue,
    )

    # The record must be an ORM write pending when `post_all` is entered: it
    # is the replay guard if the latch is ever bypassed. Never a Core insert.
    # ADR 0019's first trap, as amended.
    session.add(
        MarketResult(
            market_id=market_id,
            kind=ResultKind.SETTLED,
            outcome_id=outcome_id,
            recorded_at=now,
        )
    )
    await audit.record(
        session,
        actor=actor,
        action=AdminAction.MARKET_SETTLED,
        target_type="market",
        target_id=market_id,
        context={
            "winning_outcome_id": str(outcome_id),
            "total_paid": str(result.total_paid),
            "residue": str(result.residue),
            "holders_paid": result.holders_paid,
        },
        now=now,
    )

    # Commits everything above once, or nothing. No statement may follow it
    # before the call below, and no ORM instance is read after it.
    await posting.post_all(session, movements, now=now)

    # 7. After the commit, never before: the money moves first (ADR 0019).
    await market_terms.mark_settled(
        market_id, access_token=access_token, terms_client=terms_client
    )
    return result
