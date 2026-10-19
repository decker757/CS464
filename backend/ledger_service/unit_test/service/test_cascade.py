"""A trade fills the resting orders it makes executable. [T-7] #27, ADR 0024.

Business rules, driven through `service/trading.py::execute` with no HTTP: the
fill order, the final price, the reservation each fill releases, the batch's
order, its stamps, and what a fill the overdraft check refuses becomes. Two
trades racing over one book are `test_cascade_concurrency.py`'s; a placement's
own cascade is `test_place_order.py`'s.

**The figures are literals, worked outside the code**, with stdlib `Decimal`
at 60 digits from `C(q) = b·ln Σ exp(q_i / b)`, the closed-form fill
`k = b·logit(L) - (q_i - q_j)` floored to the tick, a buy's cost rounded up and
a sell's proceeds down, sharing nothing with `core/`. Every market here is the
placement suite's, `b = 100`.

Every "nothing was written" is read from a second session: a refused cascade
leaves its flushed writes in the request's session for its rollback, as a
refused fill on arrival does.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import InsufficientFunds, LedgerError
from model.entities import OrderStatus, Transaction, TransactionKind
from service import reservations, trading
from unit_test.market_fixtures import market_with_outcomes
from unit_test.order_fixtures import add_order
from unit_test.placement_fixtures import (
    LIMIT_B,
    limit_upstream,
    open_at,
    order_row,
    place,
    reserved_credits_of,
)
from unit_test.sell_fixtures import (
    assert_q_matches_positions,
    footprint,
    hold,
    holding,
    version_of,
)
from unit_test.trade_fixtures import (
    Recorder,
    assert_ledger_balances,
    balance_of_user,
    book_row,
    buy,
    fund,
    q_of,
    session_factory,
)

ZERO_Q = (Decimal("0.0000"), Decimal("0.0000"))

# The three orders: two buys of outcome 1 at 0.4500, then 0.4000. Outcome 1's
# limit L is outcome 0's 1 - L, so 0.4500 is the more aggressive (0.5500).
TIED_LIMIT = Decimal("0.4500")
LOWER_LIMIT = Decimal("0.4000")
FIRST_TIED = Decimal("10.0000")
SECOND_TIED = Decimal("7.5000")
LARGE = Decimal("100.0000")
LARGE_BUDGET = Decimal("40.0000")

# A buy of 90 outcome-0 shares on a flat book, then the three fills in turn.
BIG_TRADE = Decimal("90.0000")
BIG_TRADE_COST = Decimal("54.8007")
FIRST_TIED_COST = Decimal("2.9947")
SECOND_TIED_COST = Decimal("2.3860")
LARGE_FILL = Decimal("31.9534")
LARGE_FILL_COST = Decimal("11.5866")

# ADR 0024's first worked case. The trader has 60 to trade with and a buy of
# 20 outcome-1 shares at 0.4500 resting, budget 9.0000, so 51 is available.
OWN_QUANTITY = Decimal("20.0000")
OWN_BUDGET = Decimal("9.0000")
TRADING_ROOM = Decimal("60.0000")
# 87.3333 costs 52.9122, above the 51 available, and the fill it sets off
# completes the order for 6.3179: 52.9122 + 6.3179 is within the 60.
UNAFFORDABLE = Decimal("87.3333")
UNAFFORDABLE_COST = Decimal("52.9122")
# 77.7777 costs 46.2674, within the 51, and completes the order for 6.7376.
AFFORDABLE = Decimal("77.7777")
AFFORDABLE_COST = Decimal("46.2674")
AFFORDABLE_FILL_COST = Decimal("6.7376")

# One owner's resting sell and buy, filled by another trader's buy of 54
# outcome-0 shares (the last test's docstring works them).
SOLD_FROM = Decimal("20.0000")
SOLD_FROM_COST = Decimal("10.4992")
RESTING_SELL = Decimal("15.0000")
RESTING_SELL_LIMIT = Decimal("0.6500")
RESTING_BUY = Decimal("5.0000")
RESTING_BUY_LIMIT = Decimal("0.4000")
RESTING_BUY_BUDGET = Decimal("2.0000")
SELL_TRIGGER = Decimal("54.0000")
RESTING_BUY_COST = Decimal("1.6426")
SELL_FILL = Decimal("7.0960")
SELL_FILL_PROCEEDS = Decimal("4.6692")
SOLD_FROM_REMAINING_BASIS = Decimal("6.7741")


async def _flat_market(session: AsyncSession):
    upstream = limit_upstream()
    await open_at(session, upstream, ZERO_Q)
    return upstream


async def _funded(session: AsyncSession) -> uuid.UUID:
    user_id = uuid.uuid4()
    await fund(session, user_id)
    return user_id


async def _reserve_elsewhere(
    session: AsyncSession, user_id: uuid.UUID, budget: Decimal
) -> None:
    """A resting buy in another market holding `budget`, so the user's room
    in this one is a figure the test chooses. Nothing moves when an order
    rests, so there is no money to post behind it."""
    elsewhere, outcome_ids = await market_with_outcomes(session, liquidity_b=LIMIT_B)
    await add_order(
        session,
        user_id=user_id,
        market_id=elsewhere,
        outcome_id=outcome_ids[0],
        budget=str(budget),
    )
    await session.commit()


async def _fills_in(session: AsyncSession, market_id: uuid.UUID) -> list[tuple]:
    """Every fill of a resting order in the market, as `(order_id, quantity,
    total, state_version)`, in the order the fills happened."""
    session.expire_all()
    stmt = (
        select(Transaction)
        .where(
            Transaction.kind.in_((TransactionKind.TRADE_BUY, TransactionKind.TRADE_SELL)),
            Transaction.context["market_id"].astext == str(market_id),
            Transaction.context.has_key("order_id"),
        )
        .order_by(Transaction.context["state_version"].as_integer())
    )
    return [
        (
            uuid.UUID(transaction.context["order_id"]),
            Decimal(transaction.context["quantity"]),
            Decimal(transaction.context["total"]),
            transaction.context["state_version"],
        )
        for transaction in (await session.execute(stmt)).scalars()
    ]


async def _committed(market_id: uuid.UUID):
    async with session_factory()() as other:
        return await footprint(other, market_id)


# =========================================================================
# One big trade crosses three resting orders
# =========================================================================
async def test_one_big_trade_fills_three_resting_orders_by_price_then_seq(
    session: AsyncSession, starting_credits: Decimal
) -> None:
    """ADR 0024's Tests owed: "The 'big trade crosses 3 resting orders'
    scenario, asserting the final price and the fill order (price, then
    `seq`)", and "An order fills at most once per commit".

    The 0.4000 order is placed first and fills last: price before time. The
    two at 0.4500 tie, and the one placed first fills first. Both of those
    complete, and the last fills in part, which leaves the price on its limit:
    outcome 0 at 0.6000. One price frame, at the last fill's version.

    The two tied orders are one owner's, who held nothing in outcome 1, so the
    second fill finds the row the first one added (the flush before each
    fill's read). The large order's owner has exactly its budget available
    and a little over, so a partial fill that released more than its own cost
    would refuse the batch at its start.
    """
    upstream = await _flat_market(session)
    large_owner, tied_owner, trader = [await _funded(session) for _ in range(3)]
    await _reserve_elsewhere(
        session, large_owner, starting_credits - LARGE_BUDGET - Decimal("0.5000")
    )
    large = await place(
        session, upstream, user_id=large_owner, outcome=1, limit_price=LOWER_LIMIT, quantity=LARGE
    )
    first = await place(
        session, upstream, user_id=tied_owner, outcome=1, limit_price=TIED_LIMIT, quantity=FIRST_TIED
    )
    second = await place(
        session, upstream, user_id=tied_owner, outcome=1, limit_price=TIED_LIMIT, quantity=SECOND_TIED
    )
    recorder = Recorder()

    trade = await buy(
        session, upstream, user_id=trader, quantity=BIG_TRADE, redis_client=recorder
    )

    assert await _fills_in(session, upstream.market_id) == [
        (first.order.id, FIRST_TIED, -FIRST_TIED_COST, 2),
        (second.order.id, SECOND_TIED, -SECOND_TIED_COST, 3),
        (large.order.id, LARGE_FILL, -LARGE_FILL_COST, 4),
    ]
    assert trade.total == -BIG_TRADE_COST
    assert (trade.state_version, trade.book_state_version) == (1, 4)
    assert await q_of(session, upstream.market_id) == [
        Decimal("90.0000"),
        FIRST_TIED + SECOND_TIED + LARGE_FILL,
    ]

    (channel, payload) = recorder.calls[0]
    assert len(recorder.calls) == 1
    frame = json.loads(payload)
    assert channel == "market.price"
    assert frame["state_version"] == 4
    assert [price["price"] for price in frame["prices"]] == ["0.6000", "0.4000"]

    assert (await order_row(session, first.order.id)).status is OrderStatus.FILLED
    assert (await order_row(session, second.order.id)).status is OrderStatus.FILLED
    large_row = await order_row(session, large.order.id)
    assert large_row.status is OrderStatus.OPEN
    assert (large_row.filled_quantity, large_row.filled_value) == (LARGE_FILL, LARGE_FILL_COST)
    assert await holding(session, tied_owner, upstream.market_id, upstream.outcomes[1]) == (
        FIRST_TIED + SECOND_TIED,
        FIRST_TIED_COST + SECOND_TIED_COST,
    )
    assert await balance_of_user(session, large_owner) == starting_credits - LARGE_FILL_COST
    assert await reserved_credits_of(session, large_owner) == (
        starting_credits - Decimal("0.5000") - LARGE_FILL_COST
    )
    await assert_q_matches_positions(session, upstream.market_id)
    await assert_ledger_balances(session)


# =========================================================================
# What a fill releases, and the trade's place in the batch
# =========================================================================
async def test_a_trade_is_refused_when_only_its_own_orders_fill_would_have_paid_for_it(
    session: AsyncSession, starting_credits: Decimal
) -> None:
    """ADR 0024's first worked case, through a real cascade: the trade costs
    more than is available, and the buy order it then completes would have
    released enough. The trade has to be affordable before any resting order
    fills, so it is refused, `insufficient_funds` with the trade's `required`.

    Two bugs let it through: a completed order releasing only its fill's cost
    (6.3179, not the 9.0000 budget), and the trade placed after the fills in
    the batch.
    """
    upstream = await _flat_market(session)
    trader = await _funded(session)
    await _reserve_elsewhere(session, trader, starting_credits - TRADING_ROOM)
    await place(
        session, upstream, user_id=trader, outcome=1, limit_price=TIED_LIMIT, quantity=OWN_QUANTITY
    )
    before = await _committed(upstream.market_id)

    with pytest.raises(InsufficientFunds) as refusal:
        await buy(session, upstream, user_id=trader, quantity=UNAFFORDABLE)
    await session.rollback()

    assert refusal.value.required == UNAFFORDABLE_COST
    assert refusal.value.balance == starting_credits
    assert refusal.value.available == TRADING_ROOM - OWN_BUDGET
    assert await _committed(upstream.market_id) == before


async def test_a_trade_affordable_before_its_own_order_fills_completes_the_order(
    session: AsyncSession, starting_credits: Decimal
) -> None:
    """The other edge of the worked case: a trade within the 51 available goes
    through, the cascade completes the trader's own order, and the order's
    whole budget leaves the reservation, its unspent 2.2624 with it."""
    upstream = await _flat_market(session)
    trader = await _funded(session)
    await _reserve_elsewhere(session, trader, starting_credits - TRADING_ROOM)
    own = await place(
        session, upstream, user_id=trader, outcome=1, limit_price=TIED_LIMIT, quantity=OWN_QUANTITY
    )

    trade = await buy(session, upstream, user_id=trader, quantity=AFFORDABLE)

    assert await _fills_in(session, upstream.market_id) == [
        (own.order.id, OWN_QUANTITY, -AFFORDABLE_FILL_COST, 2),
    ]
    assert trade.total == -AFFORDABLE_COST
    assert (await order_row(session, own.order.id)).status is OrderStatus.FILLED
    assert await balance_of_user(session, trader) == (
        starting_credits - AFFORDABLE_COST - AFFORDABLE_FILL_COST
    )
    assert await reserved_credits_of(session, trader) == starting_credits - TRADING_ROOM
    await assert_ledger_balances(session)


async def test_an_order_part_filled_on_arrival_releases_only_what_it_still_holds(
    session: AsyncSession, starting_credits: Decimal
) -> None:
    """A fill that exhausts an order drops the reservation by the budget less
    what the order had already spent, not by the whole budget (ADR 0024).

    The contract's example order, buy 100 outcome-0 shares at 0.6000, fills
    20.5465 for 11.8152 on arrival and rests holding 48.1848. Its owner has 65
    to spare beside their other order. Another trader's buy of 85 outcome-1
    shares pushes the price down, and the cascade fills the remaining 79.4535
    for 38.8002. Releasing the whole 60 would put the figure before the batch
    at 5 over the owner's balance and refuse the other trader's trade.
    """
    upstream = limit_upstream()
    await open_at(session, upstream)
    owner, trader = await _funded(session), await _funded(session)
    placed = await place(
        session, upstream, user_id=owner, limit_price=Decimal("0.6000"), quantity=LARGE
    )
    await _reserve_elsewhere(session, owner, starting_credits - Decimal("65.0000"))

    trade = await buy(
        session, upstream, user_id=trader, outcome=1, quantity=Decimal("85.0000"), state_version=1
    )

    row = await order_row(session, placed.order.id)
    assert row.status is OrderStatus.FILLED
    assert row.filled_value == Decimal("11.8152") + Decimal("38.8002")
    assert trade.total == Decimal("-42.9090")
    assert (trade.state_version, trade.book_state_version) == (2, 3)
    # The trade's context stores the counter after its cascade, so a retry
    # answers it too.
    assert await buy(
        session, upstream, user_id=trader, outcome=1, quantity=Decimal("85.0000"), state_version=1
    ) == trade
    assert await balance_of_user(session, owner) == (
        starting_credits - Decimal("11.8152") - Decimal("38.8002")
    )
    assert await reserved_credits_of(session, owner) == starting_credits - Decimal("65.0000")
    await assert_ledger_balances(session)


# =========================================================================
# A fill the overdraft check refuses
# =========================================================================
async def test_a_fill_the_overdraft_check_refuses_is_a_500_and_writes_nothing(
    session: AsyncSession, starting_credits: Decimal, caplog: pytest.LogCaptureFixture
) -> None:
    """ADR 0024: "A fill refused by the running check inside a cascade is
    impossible by construction ... If one happens it is a bug: the commit
    rolls back with a 500, and the ledger logs it." D-NEW "A fill the running
    check refuses inside a cascade is `500 cascade_fill_refused`".

    Made possible here by breaking the invariant by hand: an order whose
    budget is one credit more than its owner holds. The trader's own trade is
    affordable, and they are not told the owner's figures.
    """
    upstream = await _flat_market(session)
    owner, trader = await _funded(session), await _funded(session)
    await add_order(
        session,
        user_id=owner,
        market_id=upstream.market_id,
        outcome_id=upstream.outcomes[1],
        budget=str(starting_credits + 1),
    )
    await session.commit()
    before = await _committed(upstream.market_id)
    recorder = Recorder()

    with caplog.at_level(logging.ERROR, logger="service.trading"):
        with pytest.raises(LedgerError) as refusal:
            await buy(
                session,
                upstream,
                user_id=trader,
                quantity=Decimal("41.0000"),
                redis_client=recorder,
            )
    await session.rollback()

    assert (refusal.value.status_code, refusal.value.code) == (500, "cascade_fill_refused")
    assert not isinstance(refusal.value, InsufficientFunds)
    assert [record.levelno for record in caplog.records if record.name == "service.trading"] == [
        logging.ERROR
    ]
    assert await _committed(upstream.market_id) == before
    assert recorder.calls == []


# =========================================================================
# The batch's clock, and the response
# =========================================================================
async def test_the_trade_and_its_fills_are_stamped_from_the_trades_own_clock(
    session: AsyncSession,
) -> None:
    """`now` is one floor for the whole batch (`post_all`): the trade lands at
    the time `execute` took under the book lock, which the book also records,
    and each fill one microsecond after the movement before it. Posted
    without that `now`, the batch would take the clock again, later."""
    upstream = await _flat_market(session)
    owner, trader = await _funded(session), await _funded(session)
    await place(
        session, upstream, user_id=owner, outcome=1, limit_price=TIED_LIMIT, quantity=FIRST_TIED
    )

    trade = await buy(session, upstream, user_id=trader, quantity=BIG_TRADE)

    assert [version for *_, version in await _fills_in(session, upstream.market_id)] == [2]
    stamps = {
        transaction.context["state_version"]: transaction.occurred_at
        for transaction in (
            await session.execute(
                select(Transaction).where(
                    Transaction.kind == TransactionKind.TRADE_BUY,
                    Transaction.context["market_id"].astext == str(upstream.market_id),
                )
            )
        ).scalars()
    }
    clock = (await book_row(session, upstream.market_id)).state_changed_at
    assert stamps[trade.state_version] == clock
    assert stamps[2] == clock + timedelta(microseconds=1)


def test_a_trade_recorded_before_book_state_version_existed_answers_its_state_version() -> None:
    """D-NEW "The trade response carries `book_state_version`, and still no
    prices", Notes: a trade's `context` from before the field has no
    `book_state_version`, and `result_of` returns its `state_version`, the
    same number, since no cascade ran then."""
    transaction = Transaction(
        id=uuid.uuid4(),
        kind=TransactionKind.TRADE_BUY,
        context={
            "user_id": str(uuid.uuid4()),
            "market_id": str(uuid.uuid4()),
            "outcome_id": str(uuid.uuid4()),
            "side": "buy",
            "quantity": "10",
            "total": "-5.0000",
            "state_version": 7,
        },
    )

    assert trading.result_of(transaction).book_state_version == 7



# =========================================================================
# A resting sell filled by somebody else's trade
# =========================================================================
async def test_a_cascade_fill_of_a_resting_sell_credits_its_owner_and_releases_basis_at_average_cost(
    session: AsyncSession, starting_credits: Decimal
) -> None:
    """ADR 0024: a fill is a trade by the order's owner, and only a buy-order
    fill drops a reservation of credits. One owner's two orders fill in one
    trade's cascade: their buy of outcome 1, then their sell of outcome 0 in
    part, which leaves the sell open at its limit and its remaining shares
    reserved.

    The owner holds 20 outcome-0 shares, bought on a flat book for 10.4992,
    and rests a sell of 15 at 0.6500 and a buy of 5 outcome-1 shares at
    0.4000, budget 2.0000. A buy of 54 outcome-0 shares takes `q` to
    [74, 0] for 33.1952. The buy order fills whole for 1.6426, leaving
    [74, 5]; the sell then sells 7.0960 for 4.6692, floored, leaving outcome
    0 at 0.6500. The sale releases 10.4992 · 7.0960/20 of the basis and
    keeps 10.4992 · 12.9040/20 = 6.77408384, half-up 6.7741: a floor keeps
    6.7740, and releasing what the fill paid keeps 5.8300.

    The owner's credit reservation loses the buy's budget and nothing for
    the sell. Their `available` before the trade is 1.0000, under the sale's
    4.6692, so a sell fill that dropped its proceeds would refuse the batch
    at its start.
    """
    upstream = await _flat_market(session)
    owner, trader = await _funded(session), await _funded(session)
    await hold(session, upstream, user_id=owner, quantity=SOLD_FROM)
    sell_order = await place(
        session,
        upstream,
        user_id=owner,
        side="sell",
        limit_price=RESTING_SELL_LIMIT,
        quantity=RESTING_SELL,
    )
    buy_order = await place(
        session,
        upstream,
        user_id=owner,
        outcome=1,
        limit_price=RESTING_BUY_LIMIT,
        quantity=RESTING_BUY,
    )
    elsewhere = starting_credits - SOLD_FROM_COST - RESTING_BUY_BUDGET - Decimal("1.0000")
    await _reserve_elsewhere(session, owner, elsewhere)

    await buy(
        session,
        upstream,
        user_id=trader,
        quantity=SELL_TRIGGER,
        state_version=await version_of(session, upstream.market_id),
        client_key="sell-trigger",
    )

    assert await _fills_in(session, upstream.market_id) == [
        (buy_order.order.id, RESTING_BUY, -RESTING_BUY_COST, 3),
        (sell_order.order.id, SELL_FILL, SELL_FILL_PROCEEDS, 4),
    ]
    sell_row = await order_row(session, sell_order.order.id)
    assert sell_row.status is OrderStatus.OPEN
    assert (sell_row.filled_quantity, sell_row.filled_value) == (SELL_FILL, SELL_FILL_PROCEEDS)
    assert await reservations.reserved_shares_of(
        session, owner, market_id=upstream.market_id, outcome_id=upstream.outcomes[0]
    ) == RESTING_SELL - SELL_FILL
    sold_from = await holding(session, owner, upstream.market_id, upstream.outcomes[0])
    assert sold_from is not None
    assert sold_from[0] == SOLD_FROM - SELL_FILL
    assert str(sold_from[1]) == str(SOLD_FROM_REMAINING_BASIS)
    assert await balance_of_user(session, owner) == (
        starting_credits - SOLD_FROM_COST - RESTING_BUY_COST + SELL_FILL_PROCEEDS
    )
    assert await reserved_credits_of(session, owner) == elsewhere
    await assert_q_matches_positions(session, upstream.market_id)
    await assert_ledger_balances(session)
