"""Buying and selling shares. [T-2] #22, [T-3] #23

The trade path: one trade, priced from `q` and `b` under the book row's lock,
written into `ledger.entries`, `ledger.positions`, `market_outcomes.q` and
`market_books.state_version` in one transaction, and announced after it
commits.

**The order, fixed by the issue and by ADR 0017, is the whole design here.**

1. The idempotency lookup, unlocked. A hit is compared against the request
   and returned — no status check, no HTTP call, no lock. "The replay lookup
   is unlocked, and that is safe because it can only ever short-circuit": a
   hit names a committed, append-only transaction, and a miss is trusted for
   nothing except declining to skip the gate below. A miss is then rolled
   back, so the gate's HTTP call holds no pooled connection.
2. The gate, `service/market_status.py::ensure_trading` — one call to
   market_service's public detail endpoint, forwarding the caller's own
   token, refusing `409 market_closed` on anything but a derived status of
   `"open"` (ADR 0017).
3. `service/books.py::ensure_open`, which is a no-op past a market's first
   ever touch. On a cold market it fetches terms and funds the pool, and it
   ends in `posting.post`, so it commits — which is why it runs before the
   book lock rather than inside the trade's own transaction ("The book's
   writes share `posting.post`'s commit, and nothing may follow it").
4. `service/grants.py::ensure_granted`, the same shape one layer down: a
   trader who has never been read gets their starting credits before the
   lock, for the same commit-boundary reason.
5. The book row, `SELECT ... FROM market_books ... FOR UPDATE` — the wider
   lock, taken before `posting.post` takes the account locks inside it
   ("Lock order: book row before account rows", ADR 0015).
6. The idempotency key, looked up again, now under the lock. "A caller
   holding pending writes must establish under its own lock that the
   idempotency key is absent": this is that establishment, and it runs
   before this function writes anything of its own, so a hit here still
   costs nothing to return.
7. `q`, `b` and the outcomes, read fresh — cheap, because the book row's lock
   makes this session's own prior read of `state_version` already correct,
   but read again anyway so the statement log shows a read after the lock
   rather than one that predates it. A book that cannot be priced is
   `MarketBookIncomplete`, through `book_prices.refuse_unpriceable` — the
   guard the preview and the snapshot use.
8. Staleness: strict equality between the quoted `state_version` and the
   book's current one, either direction ("The trade's staleness check is
   strict `state_version` equality, and the field is required").
9. The outcome, the holding check on a sell (the caller's own position, read
   here and taking no lock of its own — the book lock holds it still), the
   outstanding check as a backstop, the price, D-040's bound on both the
   cost and the resulting `q`, and `quantize_cost` — which refuses a zero
   result itself (D-041) — in `service/preview.py::quote`'s order and through
   the same `core/pricing.py` helpers, so the two code paths cannot disagree
   about what a trade costs.
10. The writes: `state_version` up by one, `state_changed_at` to this
    transaction's own moment, the traded outcome's `q`, and the position —
    added to on a buy, released at average cost on a sell — all as pending ORM
    changes, none of them committed yet.
11. `posting.post`, last, because it is the one statement in this whole path
    that commits. "The stored key is derived by the server; the client's
    value is one component of it" is why it is not handed the client's raw
    string.
12. The response, built by `result_of` from the transaction `post` returned —
    the same function every replay path uses too ("The trade response is
    reconstructed from `Transaction.context`, and carries no prices").
13. The publish, after the commit and never before it (ADR 0010), and never
    on a rejection or a replay.

**One path for both sides.** The side picks the sign of the delta, the
rounding direction, the transaction kind and the shape of the position write,
and nothing else: the order above is the same for a buy and a sell, which is
what lets the sell inherit the replay, the gate, the staleness check and the
publish without restating them.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    IdempotencyKeyReused,
    InsufficientSharesHeld,
    InsufficientSharesOutstanding,
    QuantityTooLarge,
    QuoteStale,
    UnknownOutcome,
)
from core.lmsr import _engine_context, cost_to_trade, prices as lmsr_prices
from core.pricing import MAX_MAGNITUDE, QUANTUM, Side, quantize_cost, release_basis
from model.entities import (
    Account,
    MarketBook,
    MarketOutcome,
    Position,
    Transaction,
    TransactionKind,
)
from model.schemas import OutcomePrice, PriceEvent
from service import book_prices, books, bus, grants, market_status, posting
from service.posting import Leg

ZERO = Decimal(0)

# Namespaced like every other idempotency key this system generates. The
# market id sits inside it: "The trade's idempotency key is derived by the
# server; the client's value is one component of it" is what stops a client
# claiming `signup-grant:<somebody else's user id>` on a trade of their own,
# and it is also what makes the book row the right lock for the under-lock
# re-check below — one key names one market.
_KEY_PREFIX = "trade"


def trade_key(user_id: uuid.UUID, market_id: uuid.UUID, client_key: str) -> str:
    return f"{_KEY_PREFIX}:{user_id}:{market_id}:{client_key}"


@dataclass(frozen=True)
class TradeResult:
    """What one trade did. Exactly the seven fields "The trade response is
    reconstructed from `Transaction.context`" lists, plus `transaction_id`.

    Deliberately carries no price. `test_the_response_carries_no_prices`
    asserts this over the attribute names rather than a fixed field list, so
    a field added here later that happens to have "price" in its name is
    refused by the test that guards this design, not merely by a reviewer.
    """

    transaction_id: uuid.UUID
    user_id: uuid.UUID
    market_id: uuid.UUID
    outcome_id: uuid.UUID
    side: str
    quantity: Decimal
    total: Decimal
    state_version: int


def result_of(transaction: Transaction) -> TradeResult:
    """The one function that builds a response from a `Transaction`.

    The fresh path and both replay paths all call this, so a retry cannot
    return a different shape from the original — the two could disagree only
    if there were two builders, and there is one.
    """
    context = transaction.context or {}
    return TradeResult(
        transaction_id=transaction.id,
        user_id=uuid.UUID(str(context["user_id"])),
        market_id=uuid.UUID(str(context["market_id"])),
        outcome_id=uuid.UUID(str(context["outcome_id"])),
        side=str(context["side"]),
        quantity=Decimal(str(context["quantity"])),
        total=Decimal(str(context["total"])),
        state_version=int(context["state_version"]),
    )


def _matches(
    transaction: Transaction,
    *,
    outcome_id: uuid.UUID,
    side: Side,
    quantity: Decimal,
) -> bool:
    """"A replay hit is compared against the request before it is returned."

    `outcome_id` and `side` compared as themselves; `quantity` compared
    numerically, because `context` stores it exactly as it arrived and `10`
    and `10.0000` are the same trade typed twice. `state_version` is
    deliberately not part of this: a client that lost its response
    re-previews before retrying, and the version it now quotes is newer —
    still the same trade.
    """
    context = transaction.context or {}
    try:
        stored_outcome = uuid.UUID(str(context["outcome_id"]))
        stored_quantity = Decimal(str(context["quantity"]))
    except (KeyError, ValueError, ArithmeticError):
        return False
    return (
        stored_outcome == outcome_id
        and context.get("side") == side.value
        and stored_quantity == quantity
    )


async def _replay_if_present(
    session: AsyncSession,
    key: str,
    *,
    outcome_id: uuid.UUID,
    side: Side,
    quantity: Decimal,
) -> TradeResult | None:
    """The one helper both lookups call — the unlocked pre-gate one and the
    under-lock re-check.

    A miss returns `None`. A hit is compared against the request and either
    returned or refused `IdempotencyKeyReused` — never returned unread, which
    is the failure "posting.post's fingerprint cannot see `outcome_id` at
    all" leaves open if this comparison is skipped.
    """
    existing = await posting.find_by_idempotency_key(session, key)
    if existing is None:
        return None
    if not _matches(existing, outcome_id=outcome_id, side=side, quantity=quantity):
        raise IdempotencyKeyReused
    return result_of(existing)


async def _lock_book(session: AsyncSession, market_id: uuid.UUID) -> MarketBook:
    """"Lock order: book row before account rows" — the wider lock, first."""
    stmt = select(MarketBook).where(MarketBook.market_id == market_id).with_for_update()
    return (await session.execute(stmt)).scalar_one()


async def _read_outcomes(
    session: AsyncSession, market_id: uuid.UUID
) -> Sequence[MarketOutcome]:
    """Read after the book lock is taken, so the statement log shows the
    price and the staleness comparison were made against a read that follows
    the lock rather than one that predates it (ADR 0015)."""
    stmt = (
        select(MarketOutcome)
        .where(MarketOutcome.market_id == market_id)
        .order_by(MarketOutcome.position)
    )
    return (await session.execute(stmt)).scalars().all()


async def _read_position(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    market_id: uuid.UUID,
    outcome_id: uuid.UUID,
) -> Position | None:
    """The triple-keyed row, or `None`. No `with_for_update()`: every writer
    of a position is this path, and this path has already queued on the book
    row, so the book lock is what holds the row still."""
    stmt = select(Position).where(
        Position.user_id == user_id,
        Position.market_id == market_id,
        Position.outcome_id == outcome_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _add_to_position(
    session: AsyncSession,
    position: Position | None,
    *,
    user_id: uuid.UUID,
    market_id: uuid.UUID,
    outcome_id: uuid.UUID,
    quantity: Decimal,
    cost: Decimal,
    now: datetime,
) -> None:
    """A buy: the shares and what they cost, added to the row, which is
    created on a first buy. A row sold to zero accumulates onto its `0/0` as
    a fresh basis."""
    if position is None:
        session.add(
            Position(
                user_id=user_id,
                market_id=market_id,
                outcome_id=outcome_id,
                quantity=quantity,
                cost_basis=cost,
                created_at=now,
                updated_at=now,
            )
        )
    else:
        position.quantity = position.quantity + quantity
        position.cost_basis = position.cost_basis + cost
        position.updated_at = now


def _release_from_position(
    position: Position, *, quantity: Decimal, now: datetime
) -> None:
    """A sell: the shares out of the row and their average-cost share of the
    basis with them ("A sell releases cost basis at average cost"). Proceeds
    never enter it. A position sold to zero keeps its row at `0.0000/0.0000`."""
    _, remaining = release_basis(position.cost_basis, position.quantity, quantity)
    position.quantity = position.quantity - quantity
    position.cost_basis = remaining
    position.updated_at = now


async def execute(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    outcome_id: uuid.UUID,
    side: Side,
    quantity: Decimal,
    state_version: int,
    idempotency_key: str,
    access_token: str,
    redis_client: redis.Redis,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TradeResult:
    """Trade `quantity` of `outcome_id` in `market_id`, or replay a trade this
    key already named.

    `access_token` is the caller's own, forwarded unchanged to the gate and
    to a cold market's first touch — this function mints nothing. `idempotency_key`
    is the client's own string; the stored key is derived from it, `user_id`
    and `market_id` (`trade_key`), never stored as sent.
    """
    key = trade_key(user_id, market_id, idempotency_key)

    # 1. The unlocked replay lookup, before anything else — no status check,
    # no HTTP call, no lock (ADR 0017).
    pre_gate = await _replay_if_present(
        session, key, outcome_id=outcome_id, side=side, quantity=quantity
    )
    if pre_gate is not None:
        return pre_gate

    # "The cold path holds no connection across the terms pull", for the
    # gate. The lookup above autobegan a transaction nothing ends, and the
    # gate's first act is an HTTP call — so without this, one pooled
    # connection sits `idle in transaction` for up to the terms timeout on
    # every trade that is not a replay. Rolling back discards nothing: a miss
    # loaded nothing, nothing is pending and no lock is held yet, and a miss
    # is trusted for nothing below. #115 carries the release inside the gate
    # itself, for every caller.
    assert not (session.new or session.dirty or session.deleted), (
        "trading.execute() rolls back before the gate: nothing may be "
        "pending on the session when it is called"
    )
    await session.rollback()

    # 2. The gate. Refuses `MarketClosed`, `MarketTermsUnavailable` or
    # `MarketNotFound` before anything below writes a thing.
    await market_status.ensure_trading(
        session, market_id, access_token=access_token, transport=transport
    )

    # 3. The book, opened and funded on a market's first touch only. Commits
    # internally when it writes, so it must finish before the lock below.
    await books.ensure_open(
        session, market_id, access_token=access_token, transport=transport
    )

    # 4. The trader's starting grant, same shape, same reason.
    user_account = await grants.ensure_granted(session, user_id)

    # 5. The book row, locked — the wider lock, ahead of the account locks
    # `posting.post` takes inside it.
    book = await _lock_book(session, market_id)

    # 6. The re-check. Nothing below this line has written anything yet, so a
    # hit here still costs nothing to return.
    under_lock = await _replay_if_present(
        session, key, outcome_id=outcome_id, side=side, quantity=quantity
    )
    if under_lock is not None:
        return under_lock

    # 7. Read fresh, after the lock. Local rather than
    # `book_prices.read_or_open`: the trade writes `q` through these entities,
    # which that projection does not return, and its empty-read fallback
    # would call `books.ensure_open` under this lock. The guard after it is
    # the shared one, so the trade refuses exactly the books the preview does.
    outcomes = await _read_outcomes(session, market_id)
    book_prices.refuse_unpriceable(len(outcomes), book.liquidity_b)
    q = [outcome.q for outcome in outcomes]
    b = book.liquidity_b
    ids = [outcome.outcome_id for outcome in outcomes]

    # 8. Staleness. Strict equality, either direction.
    if state_version != book.state_version:
        raise QuoteStale(quoted=state_version, current=book.state_version)

    if outcome_id not in ids:
        raise UnknownOutcome
    index = ids.index(outcome_id)

    # The holding check, after the book lock and before the outstanding check
    # and any pricing. This user's position in this outcome, not the market's
    # shares outstanding, and read without a lock of its own: the book lock
    # holds it still.
    position = await _read_position(
        session, user_id=user_id, market_id=market_id, outcome_id=outcome_id
    )
    if side is Side.SELL:
        held = position.quantity if position is not None else ZERO
        if quantity > held:
            raise InsufficientSharesHeld(
                held=held.quantize(QUANTUM), requested=quantity.quantize(QUANTUM)
            )

    # A backstop: `q` equals the sum of positions, so the check above always
    # refuses first. It is what stands between a hand-repaired book and a
    # negative `q`.
    if side is Side.SELL and quantity > q[index]:
        raise InsufficientSharesOutstanding

    # 9. The price. The same helpers `service/preview.py` uses, so the two
    # code paths cannot disagree about what this trade costs.
    delta = [ZERO] * len(q)
    delta[index] = quantity if side is Side.BUY else -quantity

    with _engine_context():
        after_q = [q_i + d_i for q_i, d_i in zip(q, delta)]

    # D-040, both halves, in `service/preview.py::quote`'s order: this path
    # writes the resulting `q` as well as the cost into `Numeric(18, 4)`, and
    # both are checked before anything is quantized, because quantizing a
    # value wider than the ambient 28 digits raises `InvalidOperation`.
    if after_q[index] > MAX_MAGNITUDE:
        raise QuantityTooLarge

    # `copy_abs`, never `abs` (D-042): `abs` rounds at the ambient precision.
    raw = cost_to_trade(q, b, delta).copy_abs()
    if raw > MAX_MAGNITUDE:
        raise QuantityTooLarge

    # Refuses a result of `0.0000` itself — `CostBelowTick` on a buy,
    # `ProceedsBelowTick` on a sell (D-041).
    magnitude = quantize_cost(raw, side=side)

    total = -magnitude if side is Side.BUY else magnitude

    # 10. The writes. Pending, not yet committed — `posting.post` below is
    # the one statement that commits them.
    now = datetime.now(UTC)
    book.state_version += 1
    book.state_changed_at = now
    outcomes[index].q = q[index] + delta[index]

    if side is Side.BUY:
        _add_to_position(
            session,
            position,
            user_id=user_id,
            market_id=market_id,
            outcome_id=outcome_id,
            quantity=quantity,
            cost=magnitude,
            now=now,
        )
    else:
        assert position is not None  # the holding check above
        _release_from_position(position, quantity=quantity, now=now)

    pool_account = await session.get(Account, book.pool_account_id)

    context = {
        "user_id": str(user_id),
        "market_id": str(market_id),
        "outcome_id": str(outcome_id),
        "side": side.value,
        "quantity": str(quantity),
        "total": str(total),
        "state_version": book.state_version,
    }

    # 11. `post`. Commits everything pending above, together with the two
    # legs below, or none of it if this raises.
    transaction = await posting.post(
        session,
        idempotency_key=key,
        kind=TransactionKind.TRADE_BUY if side is Side.BUY else TransactionKind.TRADE_SELL,
        legs=[
            Leg(account=user_account, amount=total),
            Leg(account=pool_account, amount=-total),
        ],
        context=context,
        now=now,
    )

    # 12. The response, from the transaction `post` actually committed.
    result = result_of(transaction)

    # 13. The publish. After the commit, never before it, and never on a
    # rejection or a replay — both of which have already returned by this
    # point. A publish failure is swallowed inside `bus.publish` and never
    # reaches here.
    # `book_prices.priced`, the snapshot's own function, so the frame and
    # `GET .../snapshot` give the same strings for the same state because
    # there is one quantizer, not because two copies agree.
    event = PriceEvent(
        market_id=market_id,
        state_version=book.state_version,
        prices=[
            OutcomePrice(outcome_id=p.outcome_id, position=p.position, price=p.price)
            for p in book_prices.priced(outcomes, lmsr_prices(after_q, b))
        ],
        occurred_at=now,
    )
    await bus.publish(redis_client, event, transaction_id=transaction.id)

    return result
