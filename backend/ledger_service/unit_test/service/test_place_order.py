"""Placing a limit order. [T-6] #26, ADR 0024.

Business rules, driven through `service/orders.py::place` with no HTTP. The
route is `unit_test/controller/test_order_routes.py`'s; the race between a
resting placement and a trade in another market is
`test_place_order_concurrency.py`'s.

**The figures are literals, worked outside the code.** The market is the
contract's example, `b = 100` at `q = [20, 0]`, where a buy of outcome 0 at
0.6000 fills 20.5465 shares for 11.8152 (`docs/api/ledger-service.md`, "POST
/ledger/markets/{market_id}/orders"). The others were computed with stdlib
`Decimal` at 60 digits from `C(q) = b·ln Σ exp(q_i / b)`, sharing nothing with
`core/`, and each is chosen so its rounding direction changes the number.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import (
    AccountSuspended,
    IdempotencyKeyReused,
    InsufficientFunds,
    InsufficientSharesHeld,
    MarketClosed,
    MarketNotBinary,
    OrderCrossesBook,
    UnknownOutcome,
)
from core.limit_orders import RestingOrder, executable_fill_of
from core.pricing import Side
from model.entities import (
    Account,
    AccountKind,
    MarketBook,
    Order,
    OrderStatus,
    Transaction,
    TransactionKind,
)
from service import accounts, grants, orders, posting, reservations
from service.posting import Leg
from unit_test.conftest import mint_token
from unit_test.order_fixtures import add_order
from unit_test.placement_fixtures import (
    EXAMPLE_Q,
    limit_upstream,
    open_at,
    order_count,
    order_row,
    place,
    record_result,
    reserved_credits_of,
)
from unit_test.portfolio_fixtures import read_portfolio
from unit_test.sell_fixtures import hold
from unit_test.service.test_account_status import _Auth
from unit_test.trade_fixtures import (
    Recorder,
    assert_ledger_balances,
    balance_of_user,
    book_row,
    capture_sql_with_parameters,
    fund,
    position_of,
    q_of,
    session_factory,
    transaction_count,
)

ZERO_Q = (Decimal("0.0000"), Decimal("0.0000"))

# A resting buy whose budget rounds: 0.4321 × 13.3333 = 5.76131893, which the
# ceiling makes 5.7614 and half-up or a floor would make 5.7613.
RESTING_LIMIT = Decimal("0.4321")
RESTING_QUANTITY = Decimal("13.3333")
RESTING_BUDGET = Decimal("5.7614")

# The contract's example.
EXAMPLE_LIMIT = Decimal("0.6000")
EXAMPLE_QUANTITY = Decimal("100.0000")
EXAMPLE_FILL = Decimal("20.5465")
EXAMPLE_COST = Decimal("11.8152")

# Five shares at 0.6000 fill completely: C([25, 0]) - C([20, 0]) = 2.78005…,
# charged 2.7801, under the 3.0000 budget.
SMALL_QUANTITY = Decimal("5.0000")
SMALL_COST = Decimal("2.7801")

# Holdings for the sells: 54.3333 bought on a book at zero takes outcome 0 to
# 0.6326.
HELD = Decimal("54.3333")

# A holding bought in two lots on a b = 100 book at zero: 20 shares for
# C(20, 0) − C(0, 0) = 10.49916…, charged 10.4992 (0.5250 a share), then 30
# for C(50, 0) − C(20, 0) = 17.59381…, charged 17.5939 (0.5865). The basis is
# 28.0931, 0.5619 a share. A sell of 13 at 0.5500 fills whole on arrival for
# C(50, 0) − C(37, 0) = 7.89140…, floored to 7.8914, and keeps
# 28.0931 · 37/50 = 20.788894 of the basis, half-up 20.7889 (a floor gives
# 20.7888). It releases 7.3042 and banks 7.8914 − 7.3042 = 0.5872. Released at
# the first lot's price it would keep 21.2686, at the fill's 20.2017.
LOTS = (Decimal("20.0000"), Decimal("30.0000"))
LOTS_BASIS = Decimal("28.0931")
LOTS_SOLD = Decimal("13.0000")
LOTS_SELL_LIMIT = Decimal("0.5500")
LOTS_PROCEEDS = Decimal("7.8914")
LOTS_REMAINING_BASIS = Decimal("20.7889")
LOTS_REALIZED = Decimal("0.5872")


async def _example_market(session: AsyncSession):
    upstream = limit_upstream()
    await open_at(session, upstream)
    return upstream


async def _funded(session: AsyncSession) -> uuid.UUID:
    user_id = uuid.uuid4()
    await fund(session, user_id)
    return user_id


async def _committed_counts(upstream) -> tuple[int, int, list[Decimal], int]:
    """Orders, transactions, `q` and the version, from a session that sees only
    what was committed."""
    async with session_factory()() as other:
        return (
            await order_count(other),
            await transaction_count(other),
            await q_of(other, upstream.market_id),
            (await book_row(other, upstream.market_id)).state_version,
        )


async def _fill_transactions(session: AsyncSession, order_id: uuid.UUID) -> list[Transaction]:
    session.expire_all()
    stmt = select(Transaction).where(Transaction.context["order_id"].astext == str(order_id))
    return list((await session.execute(stmt)).scalars())


# =========================================================================
# An order that rests
# =========================================================================
async def test_a_buy_below_the_price_rests_and_reserves_its_budget_rounded_up(
    session: AsyncSession, starting_credits: Decimal
) -> None:
    """#26: "Resting order reserves the maximum credits it could spend". The
    budget is `limit × quantity` rounded up to the tick, and resting writes no
    transaction: the credits stay in the balance and are held back by the
    order row (ADR 0024)."""
    upstream = await _example_market(session)
    user_id = await _funded(session)
    transactions = await transaction_count(session)

    placement = await place(
        session,
        upstream,
        user_id=user_id,
        limit_price=RESTING_LIMIT,
        quantity=RESTING_QUANTITY,
    )

    order = placement.order
    assert placement.fills == []
    assert placement.book_state_version == 0
    assert order.status == "open"
    assert order.filled_quantity == Decimal("0.0000")
    assert order.average_fill_price is None
    assert order.reserved_credits == RESTING_BUDGET
    assert order.reserved_shares is None
    assert order.outcome_position == 0
    assert (await order_row(session, order.id)).budget == RESTING_BUDGET
    assert await reserved_credits_of(session, user_id) == RESTING_BUDGET
    assert await balance_of_user(session, user_id) == starting_credits
    assert await transaction_count(session) == transactions
    assert await q_of(session, upstream.market_id) == list(EXAMPLE_Q)


# =========================================================================
# An order that fills on arrival
# =========================================================================
async def test_a_marketable_buy_fills_up_to_its_limit_and_rests_the_remainder(
    session: AsyncSession, starting_credits: Decimal
) -> None:
    """#26: "if already marketable ... it executes immediately against the
    AMM". The contract's example, number for number: the fill is an ordinary
    `trade_buy` keyed `order-fill:<order_id>:<state_version>` whose `context`
    names the order, and the remainder rests holding its budget less the
    cost."""
    upstream = await _example_market(session)
    user_id = await _funded(session)

    placement = await place(
        session,
        upstream,
        user_id=user_id,
        limit_price=EXAMPLE_LIMIT,
        quantity=EXAMPLE_QUANTITY,
    )

    order = placement.order
    (fill,) = placement.fills
    assert (fill.quantity, fill.total, fill.state_version) == (
        EXAMPLE_FILL,
        -EXAMPLE_COST,
        1,
    )
    assert placement.book_state_version == 1
    assert order.status == "open"
    assert order.filled_quantity == EXAMPLE_FILL
    assert order.average_fill_price == Decimal("0.5750")
    assert order.reserved_credits == Decimal("48.1848")

    (transaction,) = await _fill_transactions(session, order.id)
    assert transaction.id == fill.transaction_id
    assert transaction.kind == TransactionKind.TRADE_BUY
    assert transaction.idempotency_key == f"order-fill:{order.id}:1"
    assert transaction.context["user_id"] == str(user_id)

    assert await balance_of_user(session, user_id) == starting_credits - EXAMPLE_COST
    assert await reserved_credits_of(session, user_id) == Decimal("48.1848")
    position = await position_of(session, user_id, upstream.market_id, upstream.outcomes[0])
    assert (position.quantity, position.cost_basis) == (EXAMPLE_FILL, EXAMPLE_COST)
    assert await q_of(session, upstream.market_id) == [Decimal("40.5465"), Decimal("0.0000")]
    assert (await book_row(session, upstream.market_id)).state_version == 1
    await assert_ledger_balances(session)


async def test_a_buy_that_fills_completely_is_filled_and_reserves_nothing(
    session: AsyncSession, starting_credits: Decimal
) -> None:
    """A buy that fills completely spends less than its budget, and the rest of
    the budget goes with the order: nothing stays reserved (ADR 0024)."""
    upstream = await _example_market(session)
    user_id = await _funded(session)

    placement = await place(
        session, upstream, user_id=user_id, limit_price=EXAMPLE_LIMIT, quantity=SMALL_QUANTITY
    )

    assert placement.order.status == "filled"
    assert placement.order.filled_quantity == SMALL_QUANTITY
    assert placement.order.reserved_credits == Decimal("0.0000")
    assert [fill.total for fill in placement.fills] == [-SMALL_COST]
    assert await reserved_credits_of(session, user_id) == Decimal(0)
    assert await balance_of_user(session, user_id) == starting_credits - SMALL_COST


async def test_a_marketable_sell_fills_down_to_its_limit_and_reserves_the_rest(
    session: AsyncSession,
) -> None:
    """A sell at 0.6000 against a holding that has taken outcome 0 to 0.6326
    sells 13.7867 shares for 8.4978, floored, and its remaining 16.2133 shares
    stay reserved in the seller's position."""
    upstream = limit_upstream()
    await open_at(session, upstream, ZERO_Q)
    user_id = await _funded(session)
    await hold(session, upstream, user_id=user_id, quantity=HELD)
    balance = await balance_of_user(session, user_id)

    placement = await place(
        session,
        upstream,
        user_id=user_id,
        side="sell",
        limit_price=EXAMPLE_LIMIT,
        quantity=Decimal("30.0000"),
    )

    order = placement.order
    assert [(f.quantity, f.total) for f in placement.fills] == [
        (Decimal("13.7867"), Decimal("8.4978"))
    ]
    assert order.status == "open"
    assert order.average_fill_price == Decimal("0.6164")
    assert order.reserved_shares == Decimal("16.2133")
    assert order.reserved_credits is None
    assert (await order_row(session, order.id)).filled_value == Decimal("8.4978")
    assert await balance_of_user(session, user_id) == balance + Decimal("8.4978")
    position = await position_of(session, user_id, upstream.market_id, upstream.outcomes[0])
    assert position.quantity == HELD - Decimal("13.7867")
    assert await reservations.reserved_shares_of(
        session, user_id, market_id=upstream.market_id, outcome_id=upstream.outcomes[0]
    ) == Decimal("16.2133")


async def test_a_sell_filled_on_arrival_releases_basis_at_average_cost(
    session: AsyncSession,
) -> None:
    """[T-6] #26 and "A sell releases cost basis at average cost": a sell
    order's fill on arrival is a sale like any other, so it releases the
    average cost of the shares it sold, neither the first lot's price nor what
    the fill paid, and the portfolio banks the proceeds less that release."""
    upstream = limit_upstream()
    await open_at(session, upstream, ZERO_Q)
    user_id = await _funded(session)
    for lot in LOTS:
        await hold(session, upstream, user_id=user_id, quantity=lot)
    outcome_id = upstream.outcomes[0]
    bought = await position_of(session, user_id, upstream.market_id, outcome_id)
    assert str(bought.cost_basis) == str(LOTS_BASIS)

    placement = await place(
        session,
        upstream,
        user_id=user_id,
        side="sell",
        limit_price=LOTS_SELL_LIMIT,
        quantity=LOTS_SOLD,
    )

    assert placement.order.status == "filled"
    assert placement.order.reserved_shares == Decimal("0.0000")
    assert [(f.quantity, f.total) for f in placement.fills] == [(LOTS_SOLD, LOTS_PROCEEDS)]
    position = await position_of(session, user_id, upstream.market_id, outcome_id)
    assert position.quantity == sum(LOTS) - LOTS_SOLD
    assert str(position.cost_basis) == str(LOTS_REMAINING_BASIS)
    assert str((await read_portfolio(session, user_id)).realized_pnl) == str(LOTS_REALIZED)


# =========================================================================
# The budget has to fit
# =========================================================================
@pytest.mark.parametrize(
    ("other_budget_short_by", "fits"),
    [(RESTING_BUDGET, True), (RESTING_BUDGET - Decimal("0.0001"), False)],
    ids=["exactly_available", "a_tick_over_available"],
)
async def test_a_resting_buy_order_must_fit_in_available(
    session: AsyncSession,
    starting_credits: Decimal,
    other_budget_short_by: Decimal,
    fits: bool,
) -> None:
    """#26: resting orders "cannot overcommit a balance shared with instant
    trades". Another buy order holds all but `other_budget_short_by` of the
    balance; a budget equal to what is left rests, and one a tick over it is
    refused with the contract's figures: `shortfall` is the budget less
    `available`, and there is no `required`."""
    upstream = await _example_market(session)
    user_id = await _funded(session)
    other_budget = starting_credits - other_budget_short_by
    await add_order(
        session,
        user_id=user_id,
        market_id=upstream.market_id,
        outcome_id=upstream.outcomes[0],
        budget=str(other_budget),
    )
    await session.commit()

    if fits:
        placement = await place(
            session, upstream, user_id=user_id, limit_price=RESTING_LIMIT, quantity=RESTING_QUANTITY
        )
        assert placement.order.reserved_credits == RESTING_BUDGET
        return

    with pytest.raises(InsufficientFunds) as refused:
        await place(
            session, upstream, user_id=user_id, limit_price=RESTING_LIMIT, quantity=RESTING_QUANTITY
        )
    assert refused.value.balance == starting_credits
    assert refused.value.reserved == other_budget
    assert refused.value.available == other_budget_short_by
    assert refused.value.shortfall == Decimal("0.0001")
    assert refused.value.required is None
    await session.rollback()
    assert (await _committed_counts(upstream))[0] == 1


async def test_a_placement_that_would_fill_completely_is_refused_when_its_budget_does_not_fit(
    session: AsyncSession, starting_credits: Decimal
) -> None:
    """ADR 0024's second worked case, and its Tests owed: available is 15, and
    a buy of 20 at 0.9999 would fill completely on arrival for 11.4877. Its
    budget, 19.9980, does not fit, so it is refused before anything fills.

    The figures are the balance route's, without the new order, as on the
    resting path (D-NEW "A placement's `insufficient_funds` reports the
    reservation without the new order, on both paths"): `shortfall` is
    19.9980 - 15, and there is no `required`."""
    upstream = await _example_market(session)
    user_id = await _funded(session)
    other_budget = starting_credits - Decimal("15.0000")
    await add_order(
        session,
        user_id=user_id,
        market_id=upstream.market_id,
        outcome_id=upstream.outcomes[0],
        budget=str(other_budget),
    )
    await session.commit()
    before = await _committed_counts(upstream)

    with pytest.raises(InsufficientFunds) as refused:
        await place(
            session,
            upstream,
            user_id=user_id,
            limit_price=Decimal("0.9999"),
            quantity=Decimal("20.0000"),
        )

    assert refused.value.balance == starting_credits
    assert refused.value.reserved == other_budget
    assert refused.value.available == Decimal("15.0000")
    assert refused.value.shortfall == Decimal("4.9980")
    assert refused.value.required is None
    await session.rollback()
    assert await _committed_counts(upstream) == before


def test_a_start_of_batch_refusal_of_a_sell_placement_is_passed_on_unchanged() -> None:
    """A sell has no budget to take back out of `reserved`, so the rewrite
    hands posting's refusal on as it is rather than subtracting `None`. Not
    reachable through `place` today, because posting checks only the accounts
    a batch debits and a sell's fill credits the placer; this keeps the
    rewrite safe if that changes."""
    refusal = InsufficientFunds(
        balance=Decimal("10.0000"),
        reserved=Decimal("12.0000"),
        available=Decimal("-2.0000"),
        shortfall=Decimal("2.0000"),
    )

    assert orders._placement_refusal_of(refusal, None) is refusal


async def test_a_first_ever_placement_mints_the_grant_and_is_accepted(
    session: AsyncSession, starting_credits: Decimal
) -> None:
    """ADR 0024's Tests owed: a user with no ledger account places an order as
    their first action. The grant is minted before the budget is checked, so
    the order is accepted rather than refused against a balance of zero."""
    upstream = await _example_market(session)
    user_id = uuid.uuid4()
    assert await accounts.find(session, AccountKind.USER, user_id) is None

    placement = await place(
        session, upstream, user_id=user_id, limit_price=RESTING_LIMIT, quantity=RESTING_QUANTITY
    )

    assert placement.order.status == "open"
    assert await transaction_count(session, key=grants.grant_key(user_id)) == 1
    assert await balance_of_user(session, user_id) == starting_credits


# =========================================================================
# Shares
# =========================================================================
@pytest.mark.parametrize(
    ("quantity", "fits"),
    [(Decimal("34.3333"), True), (Decimal("34.3334"), False)],
    ids=["exactly_the_unreserved_shares", "a_tick_over"],
)
async def test_a_sell_order_cannot_reserve_shares_another_sell_order_holds(
    session: AsyncSession, quantity: Decimal, fits: bool
) -> None:
    """ADR 0024: "Reserved shares cannot be sold twice." The seller holds
    54.3333 and a resting sell at 0.9000 holds 20 of them, so a second sell
    order may take at most the other 34.3333. Both rest: 0.9000 is above the
    price."""
    upstream = limit_upstream()
    await open_at(session, upstream, ZERO_Q)
    user_id = await _funded(session)
    await hold(session, upstream, user_id=user_id, quantity=HELD)
    limit = Decimal("0.9000")
    await place(
        session, upstream, user_id=user_id, side="sell", limit_price=limit, quantity=Decimal("20.0000")
    )

    if fits:
        placement = await place(
            session, upstream, user_id=user_id, side="sell", limit_price=limit, quantity=quantity
        )
        assert placement.order.reserved_shares == quantity
        return

    with pytest.raises(InsufficientSharesHeld) as refused:
        await place(
            session, upstream, user_id=user_id, side="sell", limit_price=limit, quantity=quantity
        )
    assert (refused.value.held, refused.value.reserved, refused.value.requested) == (
        HELD,
        Decimal("20.0000"),
        quantity,
    )


# =========================================================================
# Refusals about the book and the market
# =========================================================================
@pytest.mark.parametrize(
    ("opposing", "limit", "refused"),
    [
        ("open", Decimal("0.5000"), True),
        ("open", Decimal("0.4999"), False),
        ("filled", Decimal("0.5000"), False),
        ("cancelled", Decimal("0.5000"), False),
        ("open_in_another_market", Decimal("0.5000"), False),
    ],
    ids=["equal_limit_crosses", "a_tick_below", "filled", "cancelled", "another_market"],
)
async def test_only_an_open_opposing_order_in_this_market_crosses(
    session: AsyncSession, opposing: str, limit: Decimal, refused: bool
) -> None:
    """#26: "A placement that would cross an opposing resting order is
    refused, `409 order_crosses_book`." Another trader's sell of outcome 0 at
    0.5000 pushes the price down; a new buy of outcome 0 pushes it up, and
    must be strictly below 0.5000. A filled or cancelled order is not in the
    way, nor is one on another market. The book is at 0.5000, so every
    accepted buy here rests."""
    upstream = limit_upstream()
    await open_at(session, upstream, ZERO_Q)
    elsewhere = limit_upstream()
    await open_at(session, elsewhere, ZERO_Q)
    market = elsewhere if opposing == "open_in_another_market" else upstream
    status = "open" if opposing.startswith("open") else opposing
    await add_order(
        session,
        user_id=uuid.uuid4(),
        market_id=market.market_id,
        outcome_id=market.outcomes[0],
        budget=None,
        status=status,
    )
    await session.commit()
    user_id = await _funded(session)

    if not refused:
        placement = await place(
            session, upstream, user_id=user_id, limit_price=limit, quantity=RESTING_QUANTITY
        )
        assert placement.fills == []
        return

    with pytest.raises(OrderCrossesBook) as crossed:
        await place(session, upstream, user_id=user_id, limit_price=limit, quantity=RESTING_QUANTITY)
    assert (crossed.value.side, crossed.value.bound) == (Side.BUY, Decimal("0.5000"))
    await session.rollback()
    assert (await _committed_counts(upstream))[0] == 1


async def _executable_open_orders(session: AsyncSession, upstream) -> list[RestingOrder]:
    """Every open order in the market that `executable_fill_of` would fill at
    the committed `q`."""
    session.expire_all()
    q = await q_of(session, upstream.market_id)
    b = (await book_row(session, upstream.market_id)).liquidity_b
    stmt = select(Order).where(
        Order.market_id == upstream.market_id, Order.status == OrderStatus.OPEN
    )
    executable = []
    for order in (await session.execute(stmt)).scalars():
        resting = RestingOrder(
            seq=order.seq,
            outcome_index=upstream.outcomes.index(order.outcome_id),
            side=order.side,
            limit_price=order.limit_price,
            remaining=order.quantity - order.filled_quantity,
        )
        if executable_fill_of(q, b, resting) is not None:
            executable.append(resting)
    return executable


@pytest.mark.parametrize(
    ("limit", "fills"),
    [(Decimal("0.4999"), True), (Decimal("0.5500"), False)],
    ids=["fills_up_to_a_tick_below_the_sell", "would_fill_past_the_sell"],
)
async def test_after_a_placement_no_open_order_in_the_market_is_executable(
    session: AsyncSession, limit: Decimal, fills: bool
) -> None:
    """#26: "Invariant holds: no resting order is executable, to within one
    tick", asserted as worded, over every open order in the market.

    Outcome 0 is at 0.4502, and another trader's sell of it rests at 0.5000.
    A buy at 0.4999 fills on arrival up to its limit and rests the rest. A buy
    at 0.5500 would fill past the sell; that it is refused is
    `test_only_an_open_opposing_order_in_this_market_crosses`'s to pin, so
    here only the book it leaves is checked.

    Skip the fill on arrival and the first case's own order rests executable;
    drop the crossing check and the second case's fill leaves the sell
    executable.
    """
    upstream = limit_upstream()
    await open_at(session, upstream, (Decimal("0.0000"), Decimal("20.0000")))
    await add_order(
        session,
        user_id=uuid.uuid4(),
        market_id=upstream.market_id,
        outcome_id=upstream.outcomes[0],
        budget=None,
    )
    await session.commit()
    user_id = await _funded(session)

    placement = None
    try:
        placement = await place(
            session, upstream, user_id=user_id, limit_price=limit, quantity=EXAMPLE_QUANTITY
        )
    except OrderCrossesBook:
        await session.rollback()

    assert await _executable_open_orders(session, upstream) == []
    if fills:
        assert placement is not None
        assert placement.fills != []
        assert placement.order.status == "open"


async def test_a_market_with_three_outcomes_is_refused_market_not_binary(
    session: AsyncSession,
) -> None:
    """#26: "Binary markets only: a placement on any other market is refused,
    `409 market_not_binary`." Under the book lock, after the outcomes are
    read, and before anything is written."""
    upstream = limit_upstream(outcomes=3)
    await open_at(session, upstream, (Decimal(0), Decimal(0), Decimal(0)))
    user_id = await _funded(session)

    with pytest.raises(MarketNotBinary):
        await place(session, upstream, user_id=user_id, limit_price=RESTING_LIMIT, quantity=RESTING_QUANTITY)
    await session.rollback()
    assert (await _committed_counts(upstream))[0] == 0


async def test_a_market_with_a_result_refuses_a_placement_market_closed(
    session: AsyncSession,
) -> None:
    """The trade's latch, on the placement: a `market_results` row is
    `market_closed` under the book lock, whatever the gate said."""
    upstream = await _example_market(session)
    await record_result(session, upstream)
    user_id = await _funded(session)

    with pytest.raises(MarketClosed):
        await place(session, upstream, user_id=user_id, limit_price=RESTING_LIMIT, quantity=RESTING_QUANTITY)
    await session.rollback()
    assert (await _committed_counts(upstream))[0] == 0


async def test_another_markets_outcome_is_refused_unknown_outcome(
    session: AsyncSession,
) -> None:
    upstream = await _example_market(session)
    user_id = await _funded(session)

    with pytest.raises(UnknownOutcome):
        await place(
            session,
            upstream,
            user_id=user_id,
            outcome_id=uuid.uuid4(),
            limit_price=RESTING_LIMIT,
            quantity=RESTING_QUANTITY,
        )


@pytest.mark.parametrize(
    ("limit_price", "quantity"),
    [
        (Decimal("0"), RESTING_QUANTITY),
        (Decimal("1"), RESTING_QUANTITY),
        (RESTING_LIMIT, Decimal("0")),
    ],
    ids=["limit_zero", "limit_one", "quantity_zero"],
)
async def test_the_service_refuses_a_limit_or_quantity_out_of_range(
    session: AsyncSession, limit_price: Decimal, quantity: Decimal
) -> None:
    """The route's validation is not this function's only possible caller, and
    a limit of 1 has no finite fill: `logit(1)` divides by zero. Refused
    before anything else, as `trading.execute` refuses a quantity."""
    upstream = await _example_market(session)

    with pytest.raises(ValueError):
        await place(
            session, upstream, user_id=uuid.uuid4(), limit_price=limit_price, quantity=quantity
        )


async def test_a_suspended_placers_attempt_on_a_cold_market_opens_no_book_and_mints_nothing(
    session: AsyncSession,
) -> None:
    """ADR 0024, "A placement checks in the trade's order": the account check
    comes before the gate, the book's first touch and the grant, so a
    suspended placer on a market nobody has touched opens nothing, funds
    nothing and is minted nothing. ADR 0022's follow-up, for the placement."""
    upstream = limit_upstream()
    user_id = uuid.uuid4()

    with pytest.raises(AccountSuspended):
        await place(
            session,
            upstream,
            user_id=user_id,
            limit_price=RESTING_LIMIT,
            quantity=RESTING_QUANTITY,
            auth_transport=_Auth.suspended().transport,
        )

    async with session_factory()() as other:
        for table in (MarketBook, Account, Transaction, Order):
            count = (await other.execute(select(func.count()).select_from(table))).scalar_one()
            assert count == 0, f"a suspended attempt wrote to {table.__tablename__}"
    assert upstream.calls == 0


# =========================================================================
# Retries
# =========================================================================
async def test_a_retry_answers_first_with_the_same_placement_and_writes_nothing(
    session: AsyncSession,
) -> None:
    """ADR 0024: "A retry with the same key returns the order as it stands
    now". Answered before the account check, as a trade's replay is, so it
    holds even for a placer suspended since; nothing more is written."""
    upstream = await _example_market(session)
    user_id = await _funded(session)
    original = await place(
        session,
        upstream,
        user_id=user_id,
        limit_price=EXAMPLE_LIMIT,
        quantity=EXAMPLE_QUANTITY,
        client_key="retry-me",
    )
    before = await _committed_counts(upstream)

    retry = await place(
        session,
        upstream,
        user_id=user_id,
        limit_price=EXAMPLE_LIMIT,
        quantity=Decimal("100"),
        client_key="retry-me",
        auth_transport=_Auth.suspended().transport,
    )

    assert retry == original
    assert await _committed_counts(upstream) == before


async def test_a_retry_returns_only_the_fills_the_placement_made(
    session: AsyncSession,
) -> None:
    """ADR 0024's Tests owed: a later trade's cascade fills the order further,
    and the retry returns the order as it stands now but, as `fills`, only
    the fills at or below its stored `book_state_version`.

    The cascade is not built yet, so its fill is posted here as one would be:
    a `trade_buy` naming the order, at the next `state_version`, with the
    order's rollups moved to match."""
    upstream = await _example_market(session)
    user_id = await _funded(session)
    original = await place(
        session,
        upstream,
        user_id=user_id,
        limit_price=EXAMPLE_LIMIT,
        quantity=EXAMPLE_QUANTITY,
        client_key="filled-later",
    )
    order_id = original.order.id
    book = await book_row(session, upstream.market_id)
    pool = await session.get(Account, book.pool_account_id)
    user = await accounts.find(session, AccountKind.USER, user_id)
    later = Decimal("0.6000")
    await posting.post(
        session,
        idempotency_key=f"order-fill:{order_id}:2",
        kind=TransactionKind.TRADE_BUY,
        legs=[Leg(account=user, amount=-later), Leg(account=pool, amount=later)],
        context={
            "user_id": str(user_id),
            "market_id": str(upstream.market_id),
            "outcome_id": str(upstream.outcomes[0]),
            "side": "buy",
            "quantity": "1.0000",
            "total": str(-later),
            "state_version": 2,
            "order_id": str(order_id),
        },
    )
    await session.execute(
        update(Order)
        .where(Order.id == order_id)
        .values(
            filled_quantity=Order.filled_quantity + 1,
            filled_value=Order.filled_value + later,
        )
    )
    await session.commit()

    retry = await place(
        session,
        upstream,
        user_id=user_id,
        limit_price=EXAMPLE_LIMIT,
        quantity=EXAMPLE_QUANTITY,
        client_key="filled-later",
    )

    assert retry.fills == original.fills
    assert retry.book_state_version == 1
    assert retry.order.filled_quantity == EXAMPLE_FILL + 1


@pytest.mark.parametrize("changed", ["outcome", "side", "limit_price", "quantity"])
async def test_a_reused_key_naming_a_different_order_is_refused(
    session: AsyncSession, changed: str
) -> None:
    """A key already used for a different order, by outcome, side, limit price
    or quantity, is `409 idempotency_key_reused`, and is answered before any
    check of its own: the changed sell is refused for the key, not for
    holding no shares."""
    upstream = await _example_market(session)
    user_id = await _funded(session)
    first = {
        "outcome": 0,
        "side": "buy",
        "limit_price": RESTING_LIMIT,
        "quantity": RESTING_QUANTITY,
    }
    await place(session, upstream, user_id=user_id, client_key="reused", **first)
    second = dict(first)
    second[changed] = {
        "outcome": 1,
        "side": "sell",
        "limit_price": Decimal("0.4320"),
        "quantity": Decimal("13.3332"),
    }[changed]

    with pytest.raises(IdempotencyKeyReused):
        await place(session, upstream, user_id=user_id, client_key="reused", **second)


async def test_the_stored_key_is_namespaced_per_caller_and_market(
    session: AsyncSession,
) -> None:
    """D-NEW "An order's idempotency key and each fill's key are derived by the
    server": two traders sending the same string place two orders, because
    `uq_orders_idempotency_key` sees `order:<user_id>:<market_id>:<key>`."""
    upstream = await _example_market(session)
    placed = []
    for _ in range(2):
        user_id = await _funded(session)
        placement = await place(
            session,
            upstream,
            user_id=user_id,
            limit_price=RESTING_LIMIT,
            quantity=RESTING_QUANTITY,
            client_key="everyone-sends-this",
        )
        placed.append((user_id, placement.order.id))

    for user_id, order_id in placed:
        assert (await order_row(session, order_id)).idempotency_key == (
            f"order:{user_id}:{upstream.market_id}:everyone-sends-this"
        )


async def test_an_open_order_on_a_market_with_a_result_reads_released(
    session: AsyncSession,
) -> None:
    """D-NEW "An open order is live only while its market has no result":
    `released` is derived on every read, here a retry's, and a released order
    reserves nothing. The status column still says `open`."""
    upstream = await _example_market(session)
    user_id = await _funded(session)
    await place(
        session,
        upstream,
        user_id=user_id,
        limit_price=RESTING_LIMIT,
        quantity=RESTING_QUANTITY,
        client_key="before-the-result",
    )
    await record_result(session, upstream)

    retry = await place(
        session,
        upstream,
        user_id=user_id,
        limit_price=RESTING_LIMIT,
        quantity=RESTING_QUANTITY,
        client_key="before-the-result",
    )

    assert retry.order.status == "released"
    assert retry.order.reserved_credits == Decimal("0.0000")
    assert (await order_row(session, retry.order.id)).status == "open"


# =========================================================================
# Locks, the publish and the rollback
# =========================================================================
def _account_locks(statements, account_id: uuid.UUID) -> int:
    return sum(
        1
        for text, parameters in statements
        if "for update" in text.lower() and "accounts" in text.lower() and account_id in parameters
    )


def _first_lock_on(statements, table: str) -> int:
    return next(
        i
        for i, (text, _) in enumerate(statements)
        if "for update" in text.lower() and table in text.lower()
    )


@pytest.mark.parametrize(
    ("side", "limit_price", "quantity", "account_locks"),
    [
        ("buy", RESTING_LIMIT, RESTING_QUANTITY, 1),
        ("sell", Decimal("0.9000"), Decimal("20.0000"), 0),
        ("buy", EXAMPLE_LIMIT, SMALL_QUANTITY, 1),
    ],
    ids=["resting_buy", "resting_sell", "buy_that_fills"],
)
async def test_a_placement_locks_the_placers_account_by_one_path_at_most(
    session: AsyncSession, side: str, limit_price: Decimal, quantity: Decimal, account_locks: int
) -> None:
    """ADR 0024, "A placement locks the placer's account by one of two paths,
    and never both", each after the book row. A resting buy locks it itself;
    a resting sell checks shares, which only the book lock holds still, and
    locks no account; a placement that fills leaves the lock to `post_all`'s
    one ascending sweep, so the account is locked once, not twice."""
    upstream = await _example_market(session)
    user_id = await _funded(session)
    if side == "sell":
        await hold(session, upstream, user_id=user_id, quantity=HELD)
    user = await accounts.find(session, AccountKind.USER, user_id)
    account_id = user.id

    with capture_sql_with_parameters() as statements:
        await place(
            session, upstream, user_id=user_id, side=side, limit_price=limit_price, quantity=quantity
        )

    assert _account_locks(statements, account_id) == account_locks
    if account_locks:
        assert _first_lock_on(statements, "market_books") < _first_lock_on(statements, "accounts")


async def test_a_placement_that_fills_publishes_one_price_frame_and_one_that_rests_none(
    session: AsyncSession,
) -> None:
    """ADR 0010 and D-NEW "One price frame per commit, at the final
    `state_version`": a fill on arrival moves the price, and its frame is at
    the fill's version. A resting order moves nothing."""
    upstream = await _example_market(session)
    user_id = await _funded(session)
    resting, filling = Recorder(), Recorder()

    await place(
        session,
        upstream,
        user_id=user_id,
        limit_price=RESTING_LIMIT,
        quantity=RESTING_QUANTITY,
        redis_client=resting,
    )
    await place(
        session,
        upstream,
        user_id=user_id,
        limit_price=EXAMPLE_LIMIT,
        quantity=SMALL_QUANTITY,
        redis_client=filling,
    )

    assert resting.calls == []
    (channel, payload) = filling.calls[0]
    assert len(filling.calls) == 1
    assert channel == "market.price"
    assert '"state_version":1' in payload


async def test_a_placement_whose_commit_fails_writes_nothing(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Moves money, so it gets a rollback test (backend/CLAUDE.md). The fill's
    writes are flushed before `post_all`, and a failure inside it leaves no
    order, no position, no `q` and no version behind once the request's
    session goes, and publishes nothing."""
    upstream = await _example_market(session)
    user_id = await _funded(session)
    before = await _committed_counts(upstream)
    recorder = Recorder()

    async def fail(*_args, **_kwargs):
        raise RuntimeError("the commit never happens")

    monkeypatch.setattr(posting, "_stamp_after_newest", fail)
    with pytest.raises(RuntimeError):
        await place(
            session,
            upstream,
            user_id=user_id,
            limit_price=EXAMPLE_LIMIT,
            quantity=EXAMPLE_QUANTITY,
            redis_client=recorder,
        )
    await session.rollback()

    assert await _committed_counts(upstream) == before
    async with session_factory()() as other:
        assert (
            await position_of(other, user_id, upstream.market_id, upstream.outcomes[0])
        ) is None
    assert recorder.calls == []


# =========================================================================
# My open orders
# =========================================================================
async def test_my_open_orders_are_the_live_ones_newest_first(
    session: AsyncSession,
) -> None:
    """#26: "Order visible in 'my open orders' with limit price, quantity,
    filled-so-far". Only live orders: a filled one, a cancelled one, one on a
    market with a result and another trader's are all left out, and the two
    that remain come back newest first."""
    first_market = await _example_market(session)
    second_market = await _example_market(session)
    settled_market = await _example_market(session)
    user_id = await _funded(session)

    older = await place(
        session, first_market, user_id=user_id, limit_price=RESTING_LIMIT, quantity=RESTING_QUANTITY
    )
    await place(
        session, first_market, user_id=user_id, limit_price=EXAMPLE_LIMIT, quantity=SMALL_QUANTITY
    )
    newer = await place(
        session, second_market, user_id=user_id, limit_price=EXAMPLE_LIMIT, quantity=EXAMPLE_QUANTITY
    )
    await place(
        session, settled_market, user_id=user_id, limit_price=RESTING_LIMIT, quantity=RESTING_QUANTITY
    )
    await record_result(session, settled_market)
    await add_order(
        session,
        user_id=user_id,
        market_id=first_market.market_id,
        outcome_id=first_market.outcomes[1],
        budget="1.0000",
        status="cancelled",
    )
    await session.commit()
    stranger = await _funded(session)
    await place(
        session, first_market, user_id=stranger, limit_price=RESTING_LIMIT, quantity=RESTING_QUANTITY
    )

    listed = await orders.list_live(session, user_id)

    assert [order.id for order in listed] == [newer.order.id, older.order.id]
    assert listed[0].filled_quantity == EXAMPLE_FILL
    assert listed[0].reserved_credits == Decimal("48.1848")
    assert listed[1].limit_price == RESTING_LIMIT
    assert listed[1].quantity == RESTING_QUANTITY


async def test_the_gate_is_asked_with_the_placers_own_token(
    session: AsyncSession,
) -> None:
    """The gate is asked with the placer's token, forwarded unchanged and
    nothing minted here, as on the trade path (ADR 0017)."""
    upstream = await _example_market(session)
    user_id = await _funded(session)
    token = mint_token(user_id)

    await place(
        session,
        upstream,
        user_id=user_id,
        limit_price=RESTING_LIMIT,
        quantity=RESTING_QUANTITY,
        access_token=token,
    )

    assert upstream.tokens == [token]
