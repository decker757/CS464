"""Buying and selling shares. [T-2] #22, [T-3] #23

The trade path: one trade, priced from `q` and `b` under the book row's lock,
written into `ledger.entries`, `ledger.positions`, `market_outcomes.q` and
`market_books.state_version` in one transaction, and announced after it
commits.

**The order, fixed by the issue, ADR 0017 and ADR 0022, is the whole design here.**

1. The idempotency lookup, unlocked. A hit is compared against the request
   and returned — no status check, no HTTP call, no lock. "The replay lookup
   is unlocked, and that is safe because it can only ever short-circuit": a
   hit names a committed, append-only transaction, and a miss is trusted for
   nothing except declining to skip the checks below. The account check
   rolls back the transaction the miss opened, so neither HTTP call holds a
   pooled connection.
1b. The account check, `service/account_status.py::refuse_if_suspended` —
   one call to auth's `GET /auth/me`, forwarding the caller's own token,
   refusing `403 account_suspended` and failing closed (ADR 0022). After the
   replay, so a charged trade's retry still answers; before the gate, so a
   suspended trader hears about themselves; before the first touch and the
   grant, so a suspended account funds and mints nothing. It releases its
   connection before calling out, as the gate does.
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
   lock, taken before `posting.post_all` takes the account locks inside it
   ("Lock order: book row before account rows", ADR 0015).
6. The idempotency key, looked up again, now under the lock. "A caller
   holding pending writes must establish under its own lock that the
   idempotency key is absent": this is that establishment, and it runs
   before this function writes anything of its own, so a hit here still
   costs nothing to return. Then the latch ([3.4] #12): a
   `ledger.market_results` row for the market is `409 market_closed`,
   whatever the gate said, before anything below is read.
7. `q`, `b` and the outcomes, read fresh — cheap, because the book row's lock
   makes this session's own prior read of `state_version` already correct,
   but read again anyway so the statement log shows a read after the lock
   rather than one that predates it. A book that cannot be priced is
   `MarketBookIncomplete`, through `book_prices.refuse_unpriceable` — the
   guard the preview and the snapshot use.
8. Staleness: strict equality between the quoted `state_version` and the
   book's current one, either direction ("The trade's staleness check is
   strict `state_version` equality, and the field is required").
9. The outcome, the holding check on a sell (the caller's own position less
   what their live sell orders in that outcome reserve, ADR 0024, read here
   and taking no lock of its own — the book lock holds both still), then
   `core/pricing.py::trade_cost_of`: the outstanding check as a backstop, the
   price, D-040's bound on both the cost and the resulting `q`, and
   `quantize_cost` — which refuses a zero result itself (D-041). The preview
   prices through the same function, so the two code paths cannot disagree
   about what a trade costs.
10. The writes: `state_version` up by one, `state_changed_at` to the time
    taken here under the book lock (which the snapshot and the price frame
    report; the ledger entries may land a little later, #187), the traded
    outcome's `q`, and the position —
    added to on a buy, released at average cost on a sell — all as pending ORM
    changes, none of them committed yet.
10b. The cascade ([T-7] #27, ADR 0024): every resting order the new price
    has made executable, most aggressive first, each filled as a trade of its
    size by its owner, as pending writes after the trade's own, and each
    rolled into its order row. The session is flushed before every fill's
    position read and again before `post_all`, because `autoflush` is off.
11. `posting.post_all`, the trade's movement first and then each fill's,
    last, because it is the one statement in this whole path that commits.
    The trade has to be affordable before any resting order fills, and a
    refusal anywhere else is `CascadeFillRefused`. "The stored key is derived
    by the server; the client's value is one component of it" is why it is
    not handed the client's raw string.
12. The response, built by `result_of` from the transaction `post_all`
    returned — the same function every replay path uses too ("The trade
    response is reconstructed from `Transaction.context`, and carries no
    prices"). Its `book_state_version` is the counter after the cascade.
13. The publish, one price frame at that counter, then one order frame per
    fill for its order's owner ([T-7] #27), after the commit and never before
    it (ADR 0010), and never on a rejection or a replay.

**One path for both sides.** The side picks the sign of the delta, the
rounding direction, the transaction kind and the shape of the position write,
and nothing else: the order above is the same for a buy and a sell, which is
what lets the sell inherit the replay, the gate, the staleness check and the
publish without restating them.
"""

from __future__ import annotations

import logging
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
    CascadeFillRefused,
    IdempotencyKeyReused,
    InsufficientFunds,
    InsufficientSharesHeld,
    MarketClosed,
    QuoteStale,
    UnknownOutcome,
)
from core.limit_orders import PlannedFill, RestingOrder, cascade_of
from core.lmsr import prices as lmsr_prices
from core.pricing import QUANTUM, Side, TradeCost, release_basis, trade_cost_of
from model.entities import (
    Account,
    AccountKind,
    MarketBook,
    MarketOutcome,
    Order,
    OrderStatus,
    Position,
    Transaction,
    TransactionKind,
)
from model.schemas import OrderEvent, OrderFill, OutcomePrice, PriceEvent
from service import (
    account_status,
    accounts,
    book_prices,
    books,
    bus,
    grants,
    market_status,
    posting,
    reservations,
)
from service.posting import Leg, Movement

logger = logging.getLogger(__name__)

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


# D-NEW "An order's idempotency key and each fill's key are derived by the
# server": a fill on arrival and a cascade's fill alike.
_FILL_KEY_PREFIX = "order-fill"


def fill_key(order_id: uuid.UUID, state_version: int) -> str:
    return f"{_FILL_KEY_PREFIX}:{order_id}:{state_version}"


@dataclass(frozen=True)
class PricedTrade:
    """One trade of `quantity` shares, priced: whose, where, which way, and
    what it costs. A trade and every fill of a resting order are one of these,
    so writing it and building its movement take the same six figures."""

    user_id: uuid.UUID
    market_id: uuid.UUID
    outcome_id: uuid.UUID
    side: Side
    quantity: Decimal
    cost: TradeCost


@dataclass(frozen=True)
class WrittenFill:
    """One resting order's fill as the cascade wrote it: the order row, with
    the fill already rolled in, and the fill's movement."""

    order: Order
    movement: Movement


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
    book_state_version: int


def result_of(transaction: Transaction) -> TradeResult:
    """The one function that builds a response from a `Transaction`.

    The fresh path and both replay paths all call this, so a retry cannot
    return a different shape from the original — the two could disagree only
    if there were two builders, and there is one.
    """
    context = transaction.context or {}
    state_version = int(context["state_version"])
    # A trade from before the cascade has no `book_state_version`, and no
    # cascade ran then, so its own version is the same number (D-NEW "The
    # trade response carries `book_state_version`, and still no prices").
    book_state_version = int(context.get("book_state_version", state_version))
    return TradeResult(
        transaction_id=transaction.id,
        user_id=uuid.UUID(str(context["user_id"])),
        market_id=uuid.UUID(str(context["market_id"])),
        outcome_id=uuid.UUID(str(context["outcome_id"])),
        side=str(context["side"]),
        quantity=Decimal(str(context["quantity"])),
        total=Decimal(str(context["total"])),
        state_version=state_version,
        book_state_version=book_state_version,
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


async def list_outcomes(
    session: AsyncSession, market_id: uuid.UUID
) -> Sequence[MarketOutcome]:
    """The market's outcomes, in `position` order.

    Read after the book lock is taken, so the statement log shows the
    price and the staleness comparison were made against a read that follows
    the lock rather than one that predates it (ADR 0015)."""
    stmt = (
        select(MarketOutcome)
        .where(MarketOutcome.market_id == market_id)
        .order_by(MarketOutcome.position)
    )
    return (await session.execute(stmt)).scalars().all()


async def find_position(
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


async def refuse_if_short_of_shares(
    session: AsyncSession,
    position: Position | None,
    *,
    user_id: uuid.UUID,
    market_id: uuid.UUID,
    outcome_id: uuid.UUID,
    quantity: Decimal,
) -> None:
    """Raise `InsufficientSharesHeld` if a sell of `quantity` is more than the
    position less what the seller's live sell orders in that outcome reserve.

    Call it holding the book lock, which holds both figures still. The trade
    and the placement both sell through it: "Reserved shares cannot be sold
    twice" (ADR 0024).
    """
    held = position.quantity if position is not None else ZERO
    reserved = await reservations.reserved_shares_of(
        session, user_id, market_id=market_id, outcome_id=outcome_id
    )
    if quantity > held - reserved:
        raise InsufficientSharesHeld(
            held=held.quantize(QUANTUM),
            reserved=reserved.quantize(QUANTUM),
            requested=quantity.quantize(QUANTUM),
        )


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


def write_trade(
    session: AsyncSession,
    book: MarketBook,
    *,
    outcomes: Sequence[MarketOutcome],
    index: int,
    position: Position | None,
    trade: PricedTrade,
    now: datetime,
) -> None:
    """The trade's pending writes: `state_version` up by one,
    `state_changed_at`, the traded outcome's `q`, and the position. Commits
    nothing; the caller's `posting.post_all` commits them."""
    book.state_version += 1
    book.state_changed_at = now
    outcomes[index].q = trade.cost.after_q[index]

    if trade.side is Side.BUY:
        _add_to_position(
            session,
            position,
            user_id=trade.user_id,
            market_id=trade.market_id,
            outcome_id=trade.outcome_id,
            quantity=trade.quantity,
            cost=trade.cost.magnitude,
            now=now,
        )
    else:
        assert position is not None  # the holding check in execute
        _release_from_position(position, quantity=trade.quantity, now=now)


def trade_movement_of(
    trade: PricedTrade,
    *,
    user_account: Account,
    pool_account: Account,
    idempotency_key: str,
    state_version: int,
    book_state_version: int | None = None,
    order_id: uuid.UUID | None = None,
    reservation_drop: Decimal = ZERO,
) -> Movement:
    """The trade's one movement: the trader's leg and the pool's, under the
    derived key, with the `context` the response is rebuilt from ("The trade
    response is reconstructed from `Transaction.context`"). `state_version` is
    the book's counter after the bump.

    A trade passes `book_state_version`, the counter after its cascade, which
    `context` stores as an eighth key so a replay answers it. An order's fill
    passes its `order_id` instead, beside the seven keys (D-NEW "A fill's
    `context` is a trade's, plus `order_id`"), and the drop it causes in the
    owner's reservation (ADR 0024).
    """
    context: dict[str, object] = {
        "user_id": str(trade.user_id),
        "market_id": str(trade.market_id),
        "outcome_id": str(trade.outcome_id),
        "side": trade.side.value,
        "quantity": str(trade.quantity),
        "total": str(trade.cost.total),
        "state_version": state_version,
    }
    if book_state_version is not None:
        context["book_state_version"] = book_state_version
    if order_id is not None:
        context["order_id"] = str(order_id)

    kind = TransactionKind.TRADE_BUY if trade.side is Side.BUY else TransactionKind.TRADE_SELL
    return Movement(
        idempotency_key=idempotency_key,
        kind=kind,
        legs=[
            Leg(account=user_account, amount=trade.cost.total),
            Leg(account=pool_account, amount=-trade.cost.total),
        ],
        context=context,
        reservation_drop=reservation_drop,
    )


def price_event_of(
    market_id: uuid.UUID,
    *,
    state_version: int,
    outcomes: Sequence[MarketOutcome],
    after_q: Sequence[Decimal],
    b: Decimal,
    now: datetime,
) -> PriceEvent:
    """The price frame for the book a commit left behind.

    `book_prices.priced`, the snapshot's own function, so the frame and
    `GET .../snapshot` give the same strings for the same state because there
    is one quantizer, not because two copies agree.
    """
    return PriceEvent(
        market_id=market_id,
        state_version=state_version,
        prices=[
            OutcomePrice(outcome_id=p.outcome_id, position=p.position, price=p.price)
            for p in book_prices.priced(outcomes, lmsr_prices(after_q, b))
        ],
        occurred_at=now,
    )


def _order_event_of(order: Order, fill: TradeResult, *, now: datetime) -> OrderEvent:
    """The order frame for one committed fill: the order as the fill left it,
    and the fill as `result_of` reads it back from its transaction."""
    return OrderEvent(
        order_id=order.id,
        user_id=order.user_id,
        market_id=order.market_id,
        outcome_id=order.outcome_id,
        side=order.side,
        status=order.status.value,
        quantity=order.quantity,
        filled_quantity=order.filled_quantity,
        fill=OrderFill(
            transaction_id=fill.transaction_id,
            quantity=fill.quantity,
            total=fill.total,
            state_version=fill.state_version,
        ),
        occurred_at=now,
    )


async def list_open_orders(session: AsyncSession, market_id: uuid.UUID) -> list[Order]:
    """The market's open orders, in no order: the cascade and the crossing
    check rank them themselves. Call it holding the book lock, which every
    writer of an order row takes first, so the read takes no lock of its own.
    The latch has just said the market has no result, so open is live."""
    stmt = select(Order).where(Order.market_id == market_id, Order.status == OrderStatus.OPEN)
    return list((await session.execute(stmt)).scalars())


def resting_order_of(order: Order, outcome_ids: Sequence[uuid.UUID]) -> RestingOrder:
    """The core's view of an order row. `seq` is unique, so a `PlannedFill`
    maps back to its row by it."""
    return RestingOrder(
        seq=order.seq,
        outcome_index=outcome_ids.index(order.outcome_id),
        side=order.side,
        limit_price=order.limit_price,
        remaining=order.quantity - order.filled_quantity,
    )


def reservation_drop_of(order: Order, *, cost: Decimal, value_before: Decimal) -> Decimal:
    """How far a fill already rolled into `order` lowers its owner's
    reservation: its cost while the order stays open, everything the order
    still held once it fills, `budget` less what it had spent before this
    fill, and nothing on a sell (ADR 0024)."""
    if order.side is Side.SELL:
        return ZERO
    if order.status is OrderStatus.FILLED:
        return order.budget - value_before
    return cost


def _add_fill_to(order: Order, fill: PlannedFill) -> None:
    """The fill's shares and credits rolled into its order, which is FILLED
    once nothing remains."""
    order.filled_quantity = order.filled_quantity + fill.quantity
    order.filled_value = order.filled_value + fill.cost.magnitude
    if order.filled_quantity == order.quantity:
        order.status = OrderStatus.FILLED


async def _write_fill(
    session: AsyncSession,
    book: MarketBook,
    order: Order,
    *,
    fill: PlannedFill,
    outcomes: Sequence[MarketOutcome],
    pool_account: Account,
    now: datetime,
) -> Movement:
    """One resting order's fill as pending writes, exactly a trade of its size
    by its owner's, and its movement. Commits nothing."""
    # `autoflush` is off, and the position read has to see a row the trade or
    # an earlier fill has only added. D-NEW "The cascade flushes before its
    # first read and again before `post_all`", amended: before every fill.
    await session.flush()
    position = await find_position(
        session, user_id=order.user_id, market_id=order.market_id, outcome_id=order.outcome_id
    )
    trade = PricedTrade(
        user_id=order.user_id,
        market_id=order.market_id,
        outcome_id=order.outcome_id,
        side=order.side,
        quantity=fill.quantity,
        cost=fill.cost,
    )
    write_trade(
        session,
        book,
        outcomes=outcomes,
        index=fill.order.outcome_index,
        position=position,
        trade=trade,
        now=now,
    )
    value_before = order.filled_value
    _add_fill_to(order, fill)

    owner_account = await accounts.find(session, AccountKind.USER, order.user_id)
    assert owner_account is not None  # the placement minted the owner's grant
    return trade_movement_of(
        trade,
        user_account=owner_account,
        pool_account=pool_account,
        idempotency_key=fill_key(order.id, book.state_version),
        state_version=book.state_version,
        order_id=order.id,
        reservation_drop=reservation_drop_of(
            order, cost=fill.cost.magnitude, value_before=value_before
        ),
    )


async def write_cascade(
    session: AsyncSession,
    book: MarketBook,
    *,
    outcomes: Sequence[MarketOutcome],
    pool_account: Account,
    now: datetime,
) -> list[WrittenFill]:
    """Fill every resting order the book's price now makes executable, as
    pending writes, and return each order with its fill's movement, in the
    order they fill.

    ADR 0024, "The cascade runs inside the trade that moved the price":
    `core/limit_orders.py::cascade_of` plans the fills against `q` as the
    caller's writes left it, and each is written as it was planned. Call it
    holding the book lock. Commits nothing.
    """
    open_orders = await list_open_orders(session, book.market_id)
    outcome_ids = [outcome.outcome_id for outcome in outcomes]
    by_seq = {order.seq: order for order in open_orders}
    plan = cascade_of(
        [outcome.q for outcome in outcomes],
        book.liquidity_b,
        [resting_order_of(order, outcome_ids) for order in open_orders],
    )
    written: list[WrittenFill] = []
    for fill in plan:
        order = by_seq[fill.order.seq]
        movement = await _write_fill(
            session,
            book,
            order,
            fill=fill,
            outcomes=outcomes,
            pool_account=pool_account,
            now=now,
        )
        written.append(WrittenFill(order=order, movement=movement))
    return written


def log_refused_fill(refusal: InsufficientFunds) -> None:
    """Log at ERROR, with its figures, a refusal `CascadeFillRefused` keeps
    from the caller: they are another account's (D-NEW "A fill the running
    check refuses inside a cascade is `500 cascade_fill_refused`")."""
    logger.error(
        "the overdraft check refused a cascade fill (after %s): balance %s, "
        "reserved %s, required %s, shortfall %s",
        refusal.movement_key or "nothing, at the start of the batch",
        refusal.balance,
        refusal.reserved,
        refusal.required,
        refusal.shortfall,
    )


async def _post_with_fills(
    session: AsyncSession, movements: list[Movement], *, trade_key: str, now: datetime
) -> list[Transaction]:
    """`posting.post_all` for a trade's batch, the trade's movement first.
    Commits.

    Raises `InsufficientFunds` only for a refusal at the trade's own movement.
    Any other refusal is `CascadeFillRefused`, logged at ERROR with its
    figures: ADR 0024 makes it impossible, and the trader must not be shown
    another account's balance (D-NEW "A fill the running check refuses inside
    a cascade is `500 cascade_fill_refused`").
    """
    try:
        return await posting.post_all(session, movements, now=now)
    except InsufficientFunds as refusal:
        if refusal.movement_key == trade_key:
            raise
        log_refused_fill(refusal)
        raise CascadeFillRefused from refusal


async def publish_order_updates(
    redis_client: redis.Redis,
    filled: Sequence[tuple[Order, Transaction]],
    *,
    now: datetime,
) -> None:
    """One order frame per fill, in fill order, for each order's owner: each
    pair is an order and the transaction of its fill. Call it after the
    commit, and after that commit's price frame (D-NEW "Order frames pass no
    version gate on the relay").

    An order fills at most once per commit (ADR 0024), so its row as committed
    is its state after that fill. Only the Redis call is guarded:
    `bus.publish_order` swallows a failed publish, so one stops none of the
    rest. Building a frame is not: if it raised, the request would fail after
    its commit and skip the frames after it.
    """
    for order, transaction in filled:
        event = _order_event_of(order, result_of(transaction), now=now)
        await bus.publish_order(redis_client, event, transaction_id=transaction.id)


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
    terms_client: httpx.AsyncClient,
    auth_client: httpx.AsyncClient,
) -> TradeResult:
    """Trade `quantity` of `outcome_id` in `market_id`, or replay a trade this
    key already named.

    `access_token` is the caller's own, forwarded unchanged to the account
    check, the gate and a cold market's first touch — this function mints
    nothing. `idempotency_key` is the client's own string; the stored key is
    derived from it, `user_id` and `market_id` (`trade_key`), never stored
    as sent.

    Raises `ValueError` on a `quantity` that is not positive, before anything
    else: the route's `gt=0` is not the only caller this function can have,
    and a negative sell would pass the holding check and be priced as a buy.
    """
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")

    key = trade_key(user_id, market_id, idempotency_key)

    # 1. The unlocked replay lookup, before anything else — no status check,
    # no HTTP call, no lock (ADR 0017).
    pre_gate = await _replay_if_present(
        session, key, outcome_id=outcome_id, side=side, quantity=quantity
    )
    if pre_gate is not None:
        return pre_gate

    # 1b. The account check. Raises `AccountSuspended`, `NotAuthenticated` or
    # `AuthUnavailable` before anything below runs. One call after the other,
    # never together, so a suspended trader on a closed market is told about
    # the suspension (ADR 0022). Rolls back the lookup's transaction first.
    await account_status.refuse_if_suspended(
        session, access_token=access_token, auth_client=auth_client
    )

    # 2. The gate. Refuses `MarketClosed`, `MarketTermsUnavailable` or
    # `MarketNotFound` before anything below writes a thing. It rolls back
    # before it calls out (D-043), which discards nothing: a miss loaded
    # nothing, nothing is pending, no lock is held yet, and a miss is trusted
    # for nothing below.
    await market_status.ensure_trading(
        session, market_id, access_token=access_token, terms_client=terms_client
    )

    # 3. The book, opened and funded on a market's first touch only. Commits
    # internally when it writes, so it must finish before the lock below.
    await books.ensure_open(
        session, market_id, access_token=access_token, terms_client=terms_client
    )

    # 4. The trader's starting grant, same shape, same reason.
    user_account = await grants.ensure_granted(session, user_id)

    # 5. The book row, locked — the wider lock, ahead of the account locks
    # `posting.post_all` takes inside it.
    book = await books.lock_book(session, market_id)

    # 6. The re-check. Nothing below this line has written anything yet, so a
    # hit here still costs nothing to return.
    under_lock = await _replay_if_present(
        session, key, outcome_id=outcome_id, side=side, quantity=quantity
    )
    if under_lock is not None:
        return under_lock

    # The latch: a market that has ended refuses here, under the book lock, a
    # trade the gate let through or that queued behind a settlement. After the
    # re-check, so a charged trade's retry still replays; before the staleness
    # check, so a settled market is never `quote_stale`. DECISIONS.md, "The
    # trade latch sits after the key re-check under the book lock".
    if await books.find_result(session, market_id) is not None:
        raise MarketClosed

    # 7. Read fresh, after the lock. Local rather than
    # `book_prices.read_or_open`: the trade writes `q` through these entities,
    # which that projection does not return, and its empty-read fallback
    # would call `books.ensure_open` under this lock. The guard after it is
    # the shared one, so the trade refuses exactly the books the preview does.
    outcomes = await list_outcomes(session, market_id)
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
    # and any pricing. This user's position in this outcome less what their
    # sell orders reserve, not the market's shares outstanding, and read
    # without a lock of its own: the book lock holds it still.
    position = await find_position(
        session, user_id=user_id, market_id=market_id, outcome_id=outcome_id
    )
    if side is Side.SELL:
        await refuse_if_short_of_shares(
            session,
            position,
            user_id=user_id,
            market_id=market_id,
            outcome_id=outcome_id,
            quantity=quantity,
        )

    # 9. The price, through `trade_cost_of` like the preview, so the two code
    # paths cannot disagree about what this trade costs. Its outstanding
    # check is a backstop here: `q` equals the sum of positions, so the
    # holding check above always refuses first. It is what stands between a
    # hand-repaired book and a negative `q`.
    cost = trade_cost_of(q, b, index=index, side=side, quantity=quantity)

    # 10. The writes. Pending, not yet committed — `posting.post_all` below is
    # the one statement that commits them.
    now = datetime.now(UTC)
    trade = PricedTrade(
        user_id=user_id,
        market_id=market_id,
        outcome_id=outcome_id,
        side=side,
        quantity=quantity,
        cost=cost,
    )
    write_trade(
        session,
        book,
        outcomes=outcomes,
        index=index,
        position=position,
        trade=trade,
        now=now,
    )
    trade_version = book.state_version

    # Read once for the whole batch. `get_one`: the book's foreign key keeps
    # the row there, so a miss is a broken database and raises, never None.
    pool_account = await session.get_one(Account, book.pool_account_id)

    # 10b. The cascade, after the trade's own writes, on the price they left.
    fills = await write_cascade(
        session, book, outcomes=outcomes, pool_account=pool_account, now=now
    )

    # After every write, so the context carries the trade's own version and
    # the book's after the cascade.
    movement = trade_movement_of(
        trade,
        user_account=user_account,
        pool_account=pool_account,
        idempotency_key=key,
        state_version=trade_version,
        book_state_version=book.state_version,
    )
    # `post_all` reads the reservation under the account locks, and it has to
    # see the fills' order rows (ADR 0024, "Where the figure before the batch
    # comes from"). Only a fill writes one.
    if fills:
        await session.flush()
    after_q = [outcome.q for outcome in outcomes]

    # 11. `post_all`, the trade first: it has to be affordable before any
    # resting order fills (ADR 0024). Commits everything pending above, with
    # every movement's legs, or none of it if this raises.
    transactions = await _post_with_fills(
        session, [movement, *(fill.movement for fill in fills)], trade_key=key, now=now
    )

    # 12. The response, from the trade's transaction `post_all` committed.
    result = result_of(transactions[0])

    # 13. The publish: one frame, at the counter after the cascade (D-NEW "One
    # price frame per commit, at the final `state_version`"). After the
    # commit, never before it, and never on a rejection or a replay — both of
    # which have already returned by this point. A publish failure is
    # swallowed inside `bus.publish` and never reaches here.
    event = price_event_of(
        market_id,
        state_version=result.book_state_version,
        outcomes=outcomes,
        after_q=after_q,
        b=b,
        now=now,
    )
    await bus.publish(redis_client, event, transaction_id=result.transaction_id)
    # The trade's own transaction is first; the fills' follow, in fill order.
    filled = list(zip([fill.order for fill in fills], transactions[1:], strict=True))
    await publish_order_updates(redis_client, filled, now=now)

    return result
