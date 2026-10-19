"""Placing a limit order, and listing the caller's live ones. [T-6] #26

ADR 0024, "Resting orders fill against the AMM inside the trade that moved the
price", decides the shape, and its "A placement checks in the trade's order"
the sequence: the retry lookup, the placer's account (ADR 0022), the market
gate, the book's first touch and the grant, then the book lock and everything
under it. Under the lock it refuses a
market that has ended, one that is not binary, an unknown outcome, an order
that would cross the book and a sell larger than the unreserved shares, and
then either fills on arrival, as far as the price allows, or rests.

**Two commit paths, and one account lock between them, never two.** An order
that fills writes the fill as a trade does and commits through
`posting.post_all`, whose ascending account sweep locks the placer's account
and whose running check sees the new order's flushed row. An order that rests
writes no movement, so a buy locks the placer's account itself, after the
book row, and checks its budget against `available` under that lock (ADR
0015). A resting sell checks shares, which only the book lock holds still.

**An order that fills runs the trade's cascade after its own fill** ([T-7]
#27), so there is one path. While no order crosses the book it finds nothing
(ADR 0024, "Termination"); a later trade that moves the price past this
order's limit fills it in that trade's cascade.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import redis.asyncio as redis
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    CascadeFillRefused,
    IdempotencyKeyReused,
    InsufficientFunds,
    MarketClosed,
    MarketNotBinary,
    UnknownOutcome,
)
from core.limit_orders import (
    PlannedFill,
    RestingOrder,
    executable_fill_of,
    refuse_if_crosses_book,
)
from core.pricing import QUANTUM, Side, per_share, quantize_cost
from model.entities import (
    Account,
    MarketBook,
    MarketOutcome,
    Order,
    OrderStatus,
    Transaction,
    TransactionKind,
)
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

# The trade path's own steps, shared with the placement's fill so a fill on
# arrival writes exactly what an instant trade of its size writes.
from service.trading import (
    PricedTrade,
    _fill_key,
    _list_open_orders,
    _log_refused_fill,
    _price_event_of,
    _read_outcomes,
    _read_position,
    _refuse_if_short_of_shares,
    _reservation_drop_of,
    _resting_order_of,
    _trade_movement_of,
    _write_cascade,
    _write_trade,
)

ZERO = Decimal(0)

# Exactly two, the same fact as its twin `core/limit_orders.py::_BINARY_OUTCOMES`.
_BINARY_OUTCOMES = 2

# Namespaced like `trading.trade_key`, for its reasons: a client cannot claim
# a key the server writes, and one key names one market, so the book lock is
# the right lock for the under-lock re-check. D-NEW "An order's idempotency
# key and each fill's key are derived by the server".
_KEY_PREFIX = "order"

_FILL_KINDS = (TransactionKind.TRADE_BUY, TransactionKind.TRADE_SELL)


def order_key(user_id: uuid.UUID, market_id: uuid.UUID, client_key: str) -> str:
    return f"{_KEY_PREFIX}:{user_id}:{market_id}:{client_key}"


@dataclass(frozen=True)
class OrderView:
    """One order as it stands now: the contract's order object."""

    id: uuid.UUID
    market_id: uuid.UUID
    outcome_id: uuid.UUID
    outcome_position: int
    side: Side
    limit_price: Decimal
    quantity: Decimal
    filled_quantity: Decimal
    average_fill_price: Decimal | None
    reserved_credits: Decimal | None
    reserved_shares: Decimal | None
    status: str
    created_at: datetime
    cancelled_at: datetime | None


@dataclass(frozen=True)
class Fill:
    """One fill a placement made, read from its transaction's `context`."""

    transaction_id: uuid.UUID
    quantity: Decimal
    total: Decimal
    state_version: int


@dataclass(frozen=True)
class Placement:
    """What a placement did: the order, its fills on arrival, and the book's
    counter after the request."""

    order: OrderView
    fills: list[Fill]
    book_state_version: int


def _order_view_of(order: Order, *, outcome_position: int, live: bool) -> OrderView:
    """The order object, with `released` and the reservations derived from
    `live`: an order that is not live holds nothing.

    `live` is `reservations.order_is_live()` as read, or, for an order this
    request wrote, whether it is still open: the latch has just said its
    market has no result.
    """
    status = order.status.value
    if order.status is OrderStatus.OPEN and not live:
        status = "released"

    nothing = Decimal("0.0000")
    reserved_credits = reserved_shares = None
    if order.side is Side.BUY:
        reserved_credits = order.budget - order.filled_value if live else nothing
    else:
        reserved_shares = order.quantity - order.filled_quantity if live else nothing

    average = None
    if order.filled_quantity > 0:
        average = per_share(order.filled_value, order.filled_quantity)

    return OrderView(
        id=order.id,
        market_id=order.market_id,
        outcome_id=order.outcome_id,
        outcome_position=outcome_position,
        side=order.side,
        limit_price=order.limit_price,
        quantity=order.quantity,
        filled_quantity=order.filled_quantity,
        average_fill_price=average,
        reserved_credits=reserved_credits,
        reserved_shares=reserved_shares,
        status=status,
        created_at=order.created_at,
        cancelled_at=order.cancelled_at,
    )


def _fill_of(transaction: Transaction) -> Fill:
    context = transaction.context or {}
    return Fill(
        transaction_id=transaction.id,
        quantity=Decimal(str(context["quantity"])),
        total=Decimal(str(context["total"])),
        state_version=int(context["state_version"]),
    )


def _views_query() -> Select[tuple[Order, int, bool]]:
    """Each order with its outcome's position and whether it is live."""
    return select(
        Order, MarketOutcome.position, reservations.order_is_live().label("live")
    ).join(
        MarketOutcome,
        (MarketOutcome.market_id == Order.market_id)
        & (MarketOutcome.outcome_id == Order.outcome_id),
    )


async def _fills_of(session: AsyncSession, order: Order) -> list[Fill]:
    """The fills the placement request made: this order's, at or below the
    `book_state_version` it stored. Every later fill, from anyone's cascade,
    has a higher version."""
    stmt = (
        select(Transaction)
        .where(
            Transaction.kind.in_(_FILL_KINDS),
            Transaction.context["order_id"].astext == str(order.id),
            Transaction.context["state_version"].as_integer() <= order.book_state_version,
        )
        .order_by(Transaction.context["state_version"].as_integer())
    )
    return [_fill_of(transaction) for transaction in (await session.execute(stmt)).scalars()]


def _matches(
    order: Order,
    *,
    outcome_id: uuid.UUID,
    side: Side,
    limit_price: Decimal,
    quantity: Decimal,
) -> bool:
    """Whether a retry names the stored order: outcome, side, and the two
    decimals compared numerically, so `100` and `100.0000` are one order."""
    return (
        order.outcome_id == outcome_id
        and order.side is side
        and order.limit_price == limit_price
        and order.quantity == quantity
    )


async def _replay_if_present(
    session: AsyncSession,
    key: str,
    *,
    outcome_id: uuid.UUID,
    side: Side,
    limit_price: Decimal,
    quantity: Decimal,
) -> Placement | None:
    """The stored placement for this key, or None. A key naming a different
    order is `IdempotencyKeyReused`.

    The order as it stands now, its stored `book_state_version`, and only the
    fills the first request made (ADR 0024, "A placement stores its own
    `book_state_version`, and a replay answers from it").
    """
    row = (
        await session.execute(_views_query().where(Order.idempotency_key == key))
    ).one_or_none()
    if row is None:
        return None
    order, position, live = row
    if not _matches(
        order, outcome_id=outcome_id, side=side, limit_price=limit_price, quantity=quantity
    ):
        raise IdempotencyKeyReused
    return Placement(
        order=_order_view_of(order, outcome_position=position, live=live),
        fills=await _fills_of(session, order),
        book_state_version=order.book_state_version,
    )


def _budget_of(side: Side, *, limit_price: Decimal, quantity: Decimal) -> Decimal | None:
    """A buy's budget, `limit × quantity` rounded up to the tick; a sell has
    none. D-NEW "A buy reserves `limit × quantity` rounded up to the tick"."""
    if side is Side.SELL:
        return None
    return quantize_cost(limit_price * quantity, side=Side.BUY)


async def _refuse_if_budget_does_not_fit(
    session: AsyncSession, account: Account, *, user_id: uuid.UUID, budget: Decimal
) -> None:
    """Lock the placer's account and raise `InsufficientFunds` unless the
    budget fits in `available`. The lock is held to the commit, so a trade of
    theirs elsewhere cannot spend the same credits meanwhile (ADR 0024)."""
    await accounts.lock(session, [account.id])
    balance = await accounts.balance_of(session, account.id)
    reserved = await reservations.reserved_of(session, user_id)
    available = balance - reserved
    if budget > available:
        raise InsufficientFunds(
            account_id=account.id,
            balance=balance,
            reserved=reserved,
            available=available,
            shortfall=budget - available,
        )


def _placement_refusal_of(
    refusal: InsufficientFunds, budget: Decimal | None
) -> InsufficientFunds:
    """`posting`'s start-of-batch refusal, with the new budget taken back out
    of `reserved`, so the figures are the resting path's. D-NEW "A
    placement's `insufficient_funds` reports the reservation without the new
    order, on both paths". Any other refusal, and any refusal of a sell,
    which has no budget, is returned as it is."""
    if refusal.shortfall is None or budget is None:
        return refusal
    reserved = refusal.reserved - budget
    return InsufficientFunds(
        account_id=refusal.account_id,
        balance=refusal.balance,
        reserved=reserved,
        available=refusal.balance - reserved,
        shortfall=refusal.shortfall,
    )


def _new_order(
    *,
    user_id: uuid.UUID,
    market_id: uuid.UUID,
    outcome_id: uuid.UUID,
    side: Side,
    limit_price: Decimal,
    quantity: Decimal,
    key: str,
    fill: PlannedFill | None,
) -> Order:
    """The order row, with the fill on arrival already rolled up into it. The
    caller sets `book_state_version`, which depends on whether it filled.

    Every figure at the column's scale, so the response a placement builds
    from this row is the one a retry reads back: `100` is stored and shown as
    `100.0000` either way.
    """
    filled_quantity = fill.quantity if fill is not None else ZERO.quantize(QUANTUM)
    return Order(
        id=uuid.uuid4(),
        user_id=user_id,
        market_id=market_id,
        outcome_id=outcome_id,
        side=side,
        limit_price=limit_price.quantize(QUANTUM),
        quantity=quantity.quantize(QUANTUM),
        filled_quantity=filled_quantity,
        budget=_budget_of(side, limit_price=limit_price, quantity=quantity),
        filled_value=fill.cost.magnitude if fill is not None else ZERO.quantize(QUANTUM),
        status=OrderStatus.FILLED if filled_quantity == quantity else OrderStatus.OPEN,
        idempotency_key=key,
    )


async def _rest(
    session: AsyncSession,
    order: Order,
    *,
    account: Account,
    outcome_position: int,
) -> Placement:
    """Insert an order nothing has filled, and commit. A buy checks its budget
    under the placer's account lock first; a sell has been checked already."""
    if order.side is Side.BUY:
        await _refuse_if_budget_does_not_fit(
            session, account, user_id=order.user_id, budget=order.budget
        )
    session.add(order)
    await session.commit()
    return Placement(
        order=_order_view_of(
            order,
            outcome_position=outcome_position,
            live=order.status is OrderStatus.OPEN,
        ),
        fills=[],
        book_state_version=order.book_state_version,
    )


async def _commit_fill(
    session: AsyncSession,
    order: Order,
    *,
    book: MarketBook,
    account: Account,
    trade: PricedTrade,
    outcomes: Sequence[MarketOutcome],
    outcome_position: int,
    now: datetime,
    redis_client: redis.Redis,
) -> Placement:
    """Run the cascade after the fill's pending writes, insert the order beside
    them, commit it all through `post_all`, then publish one price frame.

    A refusal of the placer's own account is their `insufficient_funds`; one
    of any other account is `CascadeFillRefused`, as on the trade path (D-NEW
    "A fill the running check refuses inside a cascade is `500
    cascade_fill_refused`")."""
    fill_version = book.state_version
    # Read once for the whole batch; `get_one` for the trade path's reason.
    pool_account = await session.get_one(Account, book.pool_account_id)
    # Before the order is added, so the cascade never reads the order itself.
    cascade = await _write_cascade(
        session, book, outcomes=outcomes, pool_account=pool_account, now=now
    )
    # The book's counter after this request: the last fill's.
    order.book_state_version = book.state_version

    session.add(order)
    # `post_all` reads the reservation under the account lock, and it has to
    # see this order and the cascade's (ADR 0024, "Where the figure before the
    # batch comes from").
    await session.flush()
    after_q = [outcome.q for outcome in outcomes]

    movement = _trade_movement_of(
        trade,
        user_account=account,
        pool_account=pool_account,
        idempotency_key=_fill_key(order.id, fill_version),
        state_version=fill_version,
        order_id=order.id,
        reservation_drop=_reservation_drop_of(
            order, cost=trade.cost.magnitude, value_before=ZERO
        ),
    )
    try:
        transactions = await posting.post_all(session, [movement, *cascade], now=now)
    except InsufficientFunds as refusal:
        if refusal.account_id != account.id:
            _log_refused_fill(refusal)
            raise CascadeFillRefused from refusal
        raise _placement_refusal_of(refusal, order.budget) from None

    # After the commit, never before it, as on the trade path (ADR 0010).
    event = _price_event_of(
        order.market_id,
        state_version=order.book_state_version,
        outcomes=outcomes,
        after_q=after_q,
        b=book.liquidity_b,
        now=now,
    )
    await bus.publish(redis_client, event, transaction_id=transactions[0].id)

    return Placement(
        order=_order_view_of(
            order,
            outcome_position=outcome_position,
            live=order.status is OrderStatus.OPEN,
        ),
        fills=[_fill_of(transactions[0])],
        book_state_version=order.book_state_version,
    )


async def place(
    session: AsyncSession,
    market_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    outcome_id: uuid.UUID,
    side: Side,
    limit_price: Decimal,
    quantity: Decimal,
    idempotency_key: str,
    access_token: str,
    redis_client: redis.Redis,
    terms_client: httpx.AsyncClient,
    auth_client: httpx.AsyncClient,
) -> Placement:
    """Place a limit order in `market_id`, or replay the one this key named.

    Commits. Locks the book row, then the placer's account by one of the two
    paths in the module docstring. Raises `ValueError` on a limit outside
    `(0, 1)` or a quantity that is not positive, before anything else, as
    `trading.execute` does: the route is not the only possible caller, and a
    limit of 1 has no finite fill. Every refusal raises before the commit.
    """
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")
    if not ZERO < limit_price < 1:
        raise ValueError(f"limit_price must be between 0 and 1, got {limit_price}")

    key = order_key(user_id, market_id, idempotency_key)
    request = {
        "outcome_id": outcome_id,
        "side": side,
        "limit_price": limit_price,
        "quantity": quantity,
    }

    # The unlocked retry lookup, before anything else (ADR 0017's order).
    pre_gate = await _replay_if_present(session, key, **request)
    if pre_gate is not None:
        return pre_gate

    # The trade's middle steps, in its order and for its reasons: the account
    # check and the gate call out, and the book's first touch and the grant
    # can commit, so all four run before the book lock.
    await account_status.refuse_if_suspended(
        session, access_token=access_token, auth_client=auth_client
    )
    await market_status.ensure_trading(
        session, market_id, access_token=access_token, terms_client=terms_client
    )
    await books.ensure_open(
        session, market_id, access_token=access_token, terms_client=terms_client
    )
    user_account = await grants.ensure_granted(session, user_id)

    # The book row, then the re-check and the latch, as on the trade path.
    book = await books.lock_book(session, market_id)
    under_lock = await _replay_if_present(session, key, **request)
    if under_lock is not None:
        return under_lock
    if await books.find_result(session, market_id) is not None:
        raise MarketClosed

    outcomes = await _read_outcomes(session, market_id)
    book_prices.refuse_unpriceable(len(outcomes), book.liquidity_b)
    if len(outcomes) != _BINARY_OUTCOMES:
        raise MarketNotBinary
    outcome_ids = [outcome.outcome_id for outcome in outcomes]
    if outcome_id not in outcome_ids:
        raise UnknownOutcome
    index = outcome_ids.index(outcome_id)

    open_orders = await _list_open_orders(session, market_id)
    refuse_if_crosses_book(
        [_resting_order_of(open_order, outcome_ids) for open_order in open_orders],
        outcome_index=index,
        side=side,
        limit_price=limit_price,
    )
    position = await _read_position(
        session, user_id=user_id, market_id=market_id, outcome_id=outcome_id
    )
    if side is Side.SELL:
        await _refuse_if_short_of_shares(
            session,
            position,
            user_id=user_id,
            market_id=market_id,
            outcome_id=outcome_id,
            quantity=quantity,
        )

    # The new order has no `seq` until it is inserted, and the fill on
    # arrival does not read one.
    arriving = RestingOrder(
        seq=0, outcome_index=index, side=side, limit_price=limit_price, remaining=quantity
    )
    fill = executable_fill_of([o.q for o in outcomes], book.liquidity_b, arriving)
    order = _new_order(user_id=user_id, market_id=market_id, key=key, fill=fill, **request)

    if fill is None:
        # The book's counter after this request: the one it was placed against.
        order.book_state_version = book.state_version
        return await _rest(session, order, account=user_account, outcome_position=index)

    now = datetime.now(UTC)
    trade = PricedTrade(
        user_id=user_id,
        market_id=market_id,
        outcome_id=outcome_id,
        side=side,
        quantity=fill.quantity,
        cost=fill.cost,
    )
    _write_trade(
        session,
        book,
        outcomes=outcomes,
        index=index,
        position=position,
        trade=trade,
        now=now,
    )
    return await _commit_fill(
        session,
        order,
        book=book,
        account=user_account,
        trade=trade,
        outcomes=outcomes,
        outcome_position=index,
        now=now,
        redis_client=redis_client,
    )


async def list_live(session: AsyncSession, user_id: uuid.UUID) -> list[OrderView]:
    """The user's live orders, newest first: open, on a market with no result,
    closed markets included. Takes no lock and writes nothing."""
    stmt = (
        _views_query()
        .where(Order.user_id == user_id, reservations.order_is_live())
        .order_by(Order.seq.desc())
    )
    return [
        _order_view_of(order, outcome_position=position, live=live)
        for order, position, live in (await session.execute(stmt)).all()
    ]
