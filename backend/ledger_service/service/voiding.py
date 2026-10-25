"""Voiding a flawed market: refund every holder, then tell market_service.
[BE] #221, ADR 0025.

One request, in settlement's order (ADR 0025, "The ledger goes first, in
settlement's order"):

1. The reason, before any read: at least ten characters once trimmed.
2. The public detail, through `market_status.read_terms`, holding no
   connection. A failure refuses here, even for a market already voided.
3. The record, looked up without a lock. A voided hit is a repeat: the
   figures are summed from the void's own entries, `total_owed` is worked out
   again from the lot replay alone, with no guard and no stake check, the
   session rolls back, and market_service is told again with the recorded
   reason. A settled hit is
   `409 market_already_settled`.
4. Only with no record, the status: `settled` is `market_already_settled`,
   and anything else not `voidable` is `market_not_voidable`. A refusal
   writes nothing and opens no book.
5. `books.ensure_open`, `books.lock_book`, and the record again, under the
   lock, answered as in step 3.
6. The lot replay and its guard (`void_replay_mismatch`), then the stake
   check (`admin_has_a_stake`). Either refusal rolls back, which releases
   the lock.
7. One refund per position above zero, in holder order, then the residue.
   The record (an ORM add) and the `market.voided` entry are staged, and
   `posting.post_all` commits all of it once.
8. `market_terms.mark_voided`, with no statement between the commit and it.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy import ColumnElement, Row, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    AdminHasAStake,
    MarketAlreadySettled,
    MarketNotVoidable,
    VoidIncomplete,
    VoidReplayMismatch,
)
from core.pricing import QUANTUM
from core.voiding import (
    Cut,
    HolderOutcome,
    LotReplay,
    VoidTrade,
    cut_of,
    lot_replay_of,
    refuse_if_replay_disagrees,
)
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
from service import (
    accounts,
    audit,
    books,
    ledger_service,
    market_status,
    market_terms,
    posting,
    settlement,
    trading,
)
from service.audit import Actor
from service.market_terms import MarketTerms
from service.posting import Leg, Movement

ZERO = Decimal(0)

# market_service's floor for every reason it stores, where this one lands
# (DECISIONS.md, "A void's reason is at most 5,000 characters and at least 10
# once trimmed"). Its twin is market_service's reason rule, not the order
# cancel's, which copies auth's.
_MIN_REASON_LENGTH = 10

# Restated rather than imported, as settlement's statuses are: the one status
# the void refuses by name. Every other is `voidable`'s to decide.
_SETTLED = "settled"

# The start of every refund's key, shared by `refund_key` and its SQL twin
# `refund_key_column`, so the portfolio's join and the void build one string.
_REFUND_KEY_PREFIX = "void-refund"


@dataclass(frozen=True)
class VoidResult:
    """What one void refunded, the same on the first request and on every
    repeat. `residue` is signed: positive went to PLATFORM, negative came
    from it."""

    market_id: uuid.UUID
    reason: str
    voided_by: uuid.UUID
    recorded_at: datetime
    positions_refunded: int
    total_owed: Decimal
    total_refunded: Decimal
    refunds_cut: bool
    residue: Decimal


def refund_key(market_id: uuid.UUID, *, user_id: uuid.UUID, outcome_id: uuid.UUID) -> str:
    """One position's refund's key, which the portfolio joins on (DECISIONS.md,
    "A voided row's `payout` is its refund")."""
    return f"{_REFUND_KEY_PREFIX}:{market_id}:{user_id}:{outcome_id}"


def refund_key_column(
    market_id: ColumnElement[uuid.UUID],
    *,
    user_id: uuid.UUID,
    outcome_id: ColumnElement[uuid.UUID],
) -> ColumnElement[str]:
    """`refund_key` in SQL, for a statement that joins a held outcome's row to
    its refund. Postgres writes a uuid as text exactly as `str(uuid)` does,
    lower case with hyphens, so the two build the same key."""
    return func.concat(
        f"{_REFUND_KEY_PREFIX}:", market_id, ":", str(user_id), ":", outcome_id
    )


def _result_of(
    record: MarketResult,
    *,
    positions_refunded: int,
    total_owed: Decimal,
    total_refunded: Decimal,
    refunds_cut: bool,
    residue: Decimal,
) -> VoidResult:
    """The one builder, for the fresh path and the repeat, so the two cannot
    drift. Money at the column's scale, so a zero summed from no entries reads
    as the fresh path's `0.0000`."""
    assert record.reason is not None and record.voided_by is not None  # by CHECK
    return VoidResult(
        market_id=record.market_id,
        reason=record.reason,
        voided_by=record.voided_by,
        recorded_at=record.recorded_at,
        positions_refunded=positions_refunded,
        total_owed=total_owed.quantize(QUANTUM),
        total_refunded=total_refunded.quantize(QUANTUM),
        refunds_cut=refunds_cut,
        residue=residue.quantize(QUANTUM),
    )


def _refuse_if_reason_unusable(reason: str) -> None:
    """Raise `VoidIncomplete` unless the reason is at least ten characters
    once trimmed."""
    if len(reason.strip()) < _MIN_REASON_LENGTH:
        raise VoidIncomplete


def _refuse_if_not_voidable(terms: MarketTerms) -> None:
    """Raise unless market_service reports the market `voidable`. Asked only
    when the ledger holds no record: a recorded void repeats whatever the
    status says. `settled` is refused by name first."""
    if terms.status == _SETTLED:
        raise MarketAlreadySettled
    if not terms.voidable:
        raise MarketNotVoidable


def _void_trade_of(row: Row, position_of: Mapping[uuid.UUID, int]) -> VoidTrade:
    """One trade entry on the pool's feed as the lot replay needs it, read by
    `trade_fields_of` and `trading.result_of` as the price history reads it,
    so a trade means the same thing to both replays. `total` is the pool's
    own leg, unsigned: what the buy was charged or the sale paid."""
    transaction = Transaction(id=row.transaction_id, kind=row.kind, context=row.context)
    fields = ledger_service.trade_fields_of(transaction, row.amount)
    trade = trading.result_of(transaction)
    return VoidTrade(
        user_id=trade.user_id,
        position=position_of[fields.outcome_id],
        side=fields.side,
        quantity=fields.quantity,
        total=row.amount.copy_abs(),
        state_version=trade.state_version,
    )


async def _list_trades(
    session: AsyncSession, book: MarketBook, position_of: Mapping[uuid.UUID, int]
) -> list[VoidTrade]:
    """Every trade and fill on the market's pool feed, oldest first, in the
    price history's order (ruling B). Raises `LookupError` for a trade on an
    outcome the book does not hold."""
    query = ledger_service.pool_trades_oldest_first(book.pool_account_id)
    rows = (await session.execute(query)).all()
    return [_void_trade_of(row, position_of) for row in rows]


async def _positions_of(
    session: AsyncSession, market_id: uuid.UUID, position_of: Mapping[uuid.UUID, int]
) -> dict[HolderOutcome, Decimal]:
    """Every position row in the market, by holder and outcome position."""
    stmt = select(Position.user_id, Position.outcome_id, Position.quantity).where(
        Position.market_id == market_id
    )
    return {
        (user_id, position_of[outcome_id]): quantity
        for user_id, outcome_id, quantity in (await session.execute(stmt)).all()
    }


def _position_by_outcome(outcomes: Sequence[MarketOutcome]) -> dict[uuid.UUID, int]:
    """Each outcome's position, by its id."""
    return {outcome.outcome_id: outcome.position for outcome in outcomes}


async def _replay_lots(
    session: AsyncSession, book: MarketBook, position_of: Mapping[uuid.UUID, int]
) -> tuple[LotReplay, list[VoidTrade]]:
    """The market's trades, and the lots they leave. Raises `ValueError` or
    `LookupError` for trades that cannot be replayed."""
    trades = await _list_trades(session, book, position_of)
    replay = lot_replay_of(trades, outcome_count=len(position_of), b=book.liquidity_b)
    return replay, trades


async def _guarded_replay(
    session: AsyncSession, book: MarketBook, outcomes: Sequence[MarketOutcome]
) -> LotReplay:
    """The lot replay, held to the book under its lock. Any disagreement rolls
    back, which releases the lock, and raises `VoidReplayMismatch`: a bug,
    with nothing written. ADR 0025, "The guard"."""
    position_of = _position_by_outcome(outcomes)
    try:
        replay, trades = await _replay_lots(session, book, position_of)
        positions = await _positions_of(session, book.market_id, position_of)
        refuse_if_replay_disagrees(
            replay,
            trades,
            positions=positions,
            q=[outcome.q for outcome in outcomes],
            state_version=book.state_version,
        )
    except (ValueError, LookupError) as error:
        await session.rollback()
        raise VoidReplayMismatch from error
    return replay


async def _refuse_if_staked(
    session: AsyncSession, replay: LotReplay, actor: Actor
) -> None:
    """Roll back, which releases the book lock, and raise `AdminHasAStake` if
    the administrator traded this market, a fill or a position since sold to
    zero included. After the guard: a replay the guard refuses does not name
    traders reliably either (DECISIONS.md, "The void checks a stake after the
    replay's guard, not before")."""
    if actor.id in replay.traders:
        await session.rollback()
        raise AdminHasAStake


async def _repeat(
    session: AsyncSession,
    record: MarketResult,
    *,
    access_token: str,
    terms_client: httpx.AsyncClient,
) -> VoidResult:
    """Answer a recorded void from its entries, write nothing, and tell
    market_service again with the recorded reason.

    One `GROUP BY` kind over the void's entries on the market's own pool
    account gives the count and sum of the refunds and the residue. That
    finds the same entries as ADR 0025's step 3, "found by their derived
    keys": the pool belongs to this market alone, and only the void writes
    those two kinds. `total_owed`, which no entry records, and `refunds_cut`
    are `cut_of`'s, over the lot replay run again and the pool those entries
    emptied, so they come from the one place the first request took them.
    The replay runs with no guard and no stake check: the record refuses
    every later trade, so it reads what the first request read. The rollback
    expires the record, so its values are read first, and releases the book
    lock when a locked lookup found it. Raises `VoidUnconfirmed` from
    `mark_voided`.
    """
    market_id = record.market_id
    book = await books.find(session, market_id)
    assert book is not None  # the record's foreign key names it
    outcomes = await trading.list_outcomes(session, market_id)
    replay, _ = await _replay_lots(session, book, _position_by_outcome(outcomes))

    stmt = (
        select(Transaction.kind, func.count(Entry.id), func.sum(Entry.amount))
        .join(Entry, Entry.transaction_id == Transaction.id)
        .where(
            Entry.account_id == book.pool_account_id,
            Transaction.kind.in_([TransactionKind.VOID_REFUND, TransactionKind.VOID_RESIDUE]),
        )
        .group_by(Transaction.kind)
    )
    by_kind = {
        kind: (count, total) for kind, count, total in (await session.execute(stmt)).all()
    }
    positions_refunded, refunded_from_pool = by_kind.get(TransactionKind.VOID_REFUND, (0, ZERO))
    _, residue_from_pool = by_kind.get(TransactionKind.VOID_RESIDUE, (0, ZERO))
    total_refunded = -refunded_from_pool
    residue = -residue_from_pool
    # The pool held what the refunds and the residue took out of it.
    cut = cut_of(replay.owed, pool=total_refunded + residue, seed=book.seed_subsidy)

    result = _result_of(
        record,
        positions_refunded=positions_refunded,
        total_owed=cut.total_owed,
        total_refunded=total_refunded,
        refunds_cut=cut.refunds_cut,
        residue=residue,
    )

    await session.rollback()
    await market_terms.mark_voided(
        market_id,
        reason=result.reason,
        access_token=access_token,
        terms_client=terms_client,
    )
    return result


async def _answer_recorded(
    session: AsyncSession,
    record: MarketResult,
    *,
    access_token: str,
    terms_client: httpx.AsyncClient,
) -> VoidResult:
    """A market this ledger has ended: a void repeats, and a settlement rolls
    back and raises `MarketAlreadySettled`."""
    if record.kind == ResultKind.SETTLED:
        await session.rollback()
        raise MarketAlreadySettled
    return await _repeat(
        session, record, access_token=access_token, terms_client=terms_client
    )


async def _holder_accounts(
    session: AsyncSession, user_ids: set[uuid.UUID]
) -> dict[uuid.UUID, Account]:
    """The USER accounts of these holders, which their trades opened."""
    stmt = select(Account).where(
        Account.kind == AccountKind.USER, Account.owner_id.in_(user_ids)
    )
    return {account.owner_id: account for account in (await session.execute(stmt)).scalars()}


def _refund_of(
    market_id: uuid.UUID,
    outcome: MarketOutcome,
    *,
    refund: Decimal,
    quantity: Decimal,
    holder: Account,
    pool: Account,
) -> Movement:
    """One position's refund, from the pool: one leg on the holder, one on the
    pool, so it is one history row naming the outcome and the shares."""
    return Movement(
        idempotency_key=refund_key(
            market_id, user_id=holder.owner_id, outcome_id=outcome.outcome_id
        ),
        kind=TransactionKind.VOID_REFUND,
        legs=[Leg(account=pool, amount=-refund), Leg(account=holder, amount=refund)],
        context={
            "user_id": str(holder.owner_id),
            "market_id": str(market_id),
            "outcome_id": str(outcome.outcome_id),
            "quantity": str(quantity),
        },
    )


async def _movements_of(
    session: AsyncSession,
    market_id: uuid.UUID,
    cut: Cut,
    *,
    replay: LotReplay,
    outcomes: Sequence[MarketOutcome],
    pool: Account,
) -> list[Movement]:
    """One refund per position above zero, ordered by holder and outcome,
    then the residue unless it is exactly zero."""
    holders = await _holder_accounts(session, {user_id for user_id, _ in cut.refunds})
    movements = [
        _refund_of(
            market_id,
            outcomes[position],
            refund=cut.refunds[(user_id, position)],
            quantity=replay.shares_left[(user_id, position)],
            holder=holders[user_id],
            pool=pool,
        )
        for user_id, position in sorted(cut.refunds)
    ]
    if cut.residue != ZERO:
        platform = await accounts.ensure_platform(session)
        movements.append(
            settlement.residue_of(
                market_id,
                cut.residue,
                pool=pool,
                platform=platform,
                kind=TransactionKind.VOID_RESIDUE,
                key_prefix="void-residue",
            )
        )
    return movements


async def void_market(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    actor: Actor,
    reason: str,
    access_token: str,
    terms_client: httpx.AsyncClient,
    now: datetime | None = None,
) -> VoidResult:
    """Refund every share of `market_id` still held, once, record the void,
    and tell market_service; or answer the void already recorded.

    `access_token` is the administrator's own, forwarded to both calls.
    Commits once, through `posting.post_all`, after `books.ensure_open`'s own
    commit on a cold book. Raises `VoidIncomplete`, what
    `market_status.read_terms` raises, `MarketAlreadySettled`,
    `MarketNotVoidable`, `MarketTermsUnavailable` from `books.ensure_open`'s
    cold path, `VoidReplayMismatch`, `AdminHasAStake`, and `VoidUnconfirmed`
    once the refunds have committed. **Call this with
    nothing pending on the session.** `now` is injectable for tests.
    """
    # 1. Before any read, as the administrator's order cancel checks its own.
    _refuse_if_reason_unusable(reason)

    # 2. The read, before any statement.
    terms = await market_status.read_terms(
        session, market_id, access_token=access_token, terms_client=terms_client
    )

    # 3. The unlocked lookup can only short-circuit: a hit names a committed
    # row nothing updates or deletes, and a miss is trusted for nothing.
    record = await books.find_result(session, market_id)
    if record is not None:
        return await _answer_recorded(
            session, record, access_token=access_token, terms_client=terms_client
        )

    # 4. Before `ensure_open`, whose cold path commits a seed.
    _refuse_if_not_voidable(terms)

    # 5. The book, opened on a first touch, then locked, and the record looked
    # for again under the lock (ADR 0015).
    await books.ensure_open(
        session, market_id, access_token=access_token, terms_client=terms_client
    )
    book = await books.lock_book(session, market_id)
    record = await books.find_result(session, market_id)
    if record is not None:
        return await _answer_recorded(
            session, record, access_token=access_token, terms_client=terms_client
        )

    # 6. The replay, its guard, then the stake.
    outcomes = await trading.list_outcomes(session, market_id)
    replay = await _guarded_replay(session, book, outcomes)
    await _refuse_if_staked(session, replay, actor)

    # After the lock, as settlement's is. A floor for `post_all`, not its stamp.
    now = now if now is not None else datetime.now(UTC)

    # 7. The money: the pool's derived balance, read under the book lock.
    pool = await session.get(Account, book.pool_account_id)
    assert pool is not None
    cut = cut_of(
        replay.owed,
        pool=await accounts.balance_of(session, pool.id),
        seed=book.seed_subsidy,
    )
    movements = await _movements_of(
        session, market_id, cut, replay=replay, outcomes=outcomes, pool=pool
    )
    # ADR 0025, "The batch is never empty": the pool never holds less than the
    # seed, so with no refund the residue is posted. `post_all`'s own refusal
    # stays the backstop.
    assert movements, "a void with no refund and no residue"

    # The record must be an ORM write pending when `post_all` is entered: it
    # is the replay guard if the latch is ever bypassed. ADR 0019's first trap.
    record = MarketResult(
        market_id=market_id,
        kind=ResultKind.VOIDED,
        outcome_id=None,
        reason=reason.strip(),
        voided_by=actor.id,
        recorded_at=now,
    )
    session.add(record)
    result = _result_of(
        record,
        positions_refunded=len(cut.refunds),
        total_owed=cut.total_owed,
        total_refunded=cut.total_refunded,
        refunds_cut=cut.refunds_cut,
        residue=cut.residue,
    )
    await audit.record(
        session,
        actor=actor,
        action=AdminAction.MARKET_VOIDED,
        target_type="market",
        target_id=market_id,
        reason=result.reason,
        context={
            "positions_refunded": result.positions_refunded,
            "total_owed": str(result.total_owed),
            "total_refunded": str(result.total_refunded),
            "refunds_cut": result.refunds_cut,
            "residue": str(result.residue),
        },
        now=now,
    )

    # Commits everything above once, or nothing. No statement may follow it
    # before the call below.
    await posting.post_all(session, movements, now=now)

    # 8. After the commit, never before: the money moves first (ADR 0025).
    await market_terms.mark_voided(
        market_id,
        reason=result.reason,
        access_token=access_token,
        terms_client=terms_client,
    )
    return result
