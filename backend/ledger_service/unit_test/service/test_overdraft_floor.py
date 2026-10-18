"""`post_all`'s overdraft floor is the reservation, checked in movement order.
[T-6] #26, [T-7] #27

ADR 0024, "A reservation is derived from the order rows, and it is
`posting`'s floor", and its Tests owed. Each test plays a batch the way the
cascade will hand it over: the order rows already flushed as they stand after
the batch, and each movement carrying the drop in its account's reservation
that it causes. ADR 0024's worked cases are the figures.

Every "nothing was written" is read from a second session, because the
caller's own session would report its own state.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core import errors
from core.database import get_session_factory
from model.entities import Account, AccountKind, TransactionKind
from service import accounts, posting, reservations
from unit_test.market_fixtures import market_with_outcomes
from unit_test.order_fixtures import add_order
from unit_test.trade_fixtures import ledger_counts

LIQUIDITY_B = Decimal("100.0000")


class _Trader:
    """A funded USER account, a market pool to trade against, and a market
    for the trader's orders to rest in."""

    def __init__(self, user: Account, pool: Account, market_id, outcome_id) -> None:
        self.user = user
        self.pool = pool
        self.market_id = market_id
        self.outcome_id = outcome_id

    def pays(self, amount: str, *, drop: str = "0") -> posting.Movement:
        """A trade, or a buy-order fill, debiting the trader."""
        return posting.Movement(
            idempotency_key=f"pays:{uuid.uuid4()}",
            kind=TransactionKind.TRADE_BUY,
            legs=[
                posting.Leg(account=self.user, amount=-Decimal(amount)),
                posting.Leg(account=self.pool, amount=Decimal(amount)),
            ],
            reservation_drop=Decimal(drop),
        )

    def is_paid(self, amount: str) -> posting.Movement:
        """A sell, or a sell-order fill, crediting the trader."""
        return posting.Movement(
            idempotency_key=f"paid:{uuid.uuid4()}",
            kind=TransactionKind.TRADE_SELL,
            legs=[
                posting.Leg(account=self.pool, amount=-Decimal(amount)),
                posting.Leg(account=self.user, amount=Decimal(amount)),
            ],
        )

    async def order(self, session: AsyncSession, **fields) -> None:
        await add_order(
            session,
            user_id=self.user.owner_id,
            market_id=self.market_id,
            outcome_id=self.outcome_id,
            **fields,
        )


async def _trader_holding(session: AsyncSession, credits: str) -> _Trader:
    """A trader holding `credits`, committed, with nothing reserved yet."""
    platform = await accounts.ensure_platform(session)
    user = Account(kind=AccountKind.USER, owner_id=uuid.uuid4())
    pool = Account(kind=AccountKind.MARKET_POOL, owner_id=uuid.uuid4())
    session.add_all([user, pool])
    await session.commit()
    await posting.post(
        session,
        idempotency_key=f"fund:{uuid.uuid4()}",
        kind=TransactionKind.SIGNUP_GRANT,
        legs=[
            posting.Leg(account=platform, amount=-Decimal(credits)),
            posting.Leg(account=user, amount=Decimal(credits)),
        ],
    )
    market_id, outcome_ids = await market_with_outcomes(session, liquidity_b=LIQUIDITY_B)
    await session.commit()
    return _Trader(user, pool, market_id, outcome_ids[0])


async def _balance_elsewhere(account: Account) -> Decimal:
    async with get_session_factory()() as other:
        return await accounts.balance_of(other, account.id)


async def _counts_elsewhere() -> tuple[int, int]:
    async with get_session_factory()() as other:
        return await ledger_counts(other)


@pytest.mark.parametrize(
    ("trade", "refused"),
    [("40.0000", False), ("40.0001", True)],
    ids=["exactly_available", "one_tick_over"],
)
async def test_a_trade_beside_a_buy_order_that_fills_completely(
    session: AsyncSession, trade: str, refused: bool
) -> None:
    """ADR 0024's first worked case, at its edge. 100 held, a buy order of
    budget 60 resting, so 40 available; the cascade fills the order completely
    for 50. The read after the batch sees nothing reserved, the fill drops the
    reservation by 60, so the floor at the trade is 60. Spending exactly the
    available 40 passes; a tick more is refused, though the 10 of budget the
    fill frees would have paid for it had the fill dropped the reservation by
    its cost."""
    trader = await _trader_holding(session, "100.0000")
    before = await _counts_elsewhere()
    await trader.order(session, budget="60.0000", filled_value="50.0000", status="filled")
    batch = [trader.pays(trade), trader.pays("50.0000", drop="60.0000")]

    if not refused:
        await posting.post_all(session, batch)
        assert await _balance_elsewhere(trader.user) == Decimal("10.0000")
        return

    with pytest.raises(errors.InsufficientFunds) as refusal:
        await posting.post_all(session, batch)

    assert refusal.value.balance == Decimal("100.0000")
    assert refusal.value.required == Decimal("40.0001")
    assert refusal.value.reserved == Decimal("60.0000")
    assert refusal.value.available == Decimal("40.0000")
    assert refusal.value.shortfall is None
    assert await _counts_elsewhere() == before


@pytest.mark.parametrize(
    ("other_budget", "shortfall"),
    [("70.0000", Decimal("10.0000")), ("60.0000", None)],
    ids=["the_adr_case", "exactly_fits"],
)
async def test_a_placement_whose_budget_does_not_fit_is_refused_at_the_start(
    session: AsyncSession, other_budget: str, shortfall: Decimal | None
) -> None:
    """ADR 0024's second worked case. 100 held, another buy order reserving
    70, so 30 available; a new buy of budget 40 fills completely on arrival
    for 28. The read after the batch sees the other order's 70, the fill drops
    the new one's whole 40, so the batch starts at 110 against 100 and is
    refused there with `shortfall` 10, though the fill alone would leave 72
    against 70. With the other order at 60 the budget fits exactly and the
    placement passes."""
    trader = await _trader_holding(session, "100.0000")
    before = await _counts_elsewhere()
    await trader.order(session, budget=other_budget)
    await trader.order(session, budget="40.0000", filled_value="28.0000", status="filled")
    batch = [trader.pays("28.0000", drop="40.0000")]

    if shortfall is None:
        await posting.post_all(session, batch)
        assert await _balance_elsewhere(trader.user) == Decimal("72.0000")
        return

    with pytest.raises(errors.InsufficientFunds) as refusal:
        await posting.post_all(session, batch)

    assert refusal.value.shortfall == shortfall
    assert refusal.value.required is None
    assert refusal.value.balance == Decimal("100.0000")
    assert refusal.value.reserved == Decimal("110.0000")
    assert refusal.value.available == Decimal("-10.0000")
    assert await _counts_elsewhere() == before


async def test_a_self_fill_cannot_pay_for_the_trade_that_triggered_it(
    session: AsyncSession,
) -> None:
    """ADR 0024's self-fill: 100 held, 60 reserved by a buy order. A trade of
    50, then the cascade sells 30 of the trader's shares and fills 20 of that
    buy order. Netted, or checked against the reservation after the batch
    (40), the trade passes. Against the running reservation it is checked at
    60, and refused: it has to be affordable before any resting order fills."""
    trader = await _trader_holding(session, "100.0000")
    before = await _counts_elsewhere()
    await trader.order(session, budget="60.0000", filled_value="20.0000")
    batch = [
        trader.pays("50.0000"),
        trader.is_paid("30.0000"),
        trader.pays("20.0000", drop="20.0000"),
    ]

    with pytest.raises(errors.InsufficientFunds) as refusal:
        await posting.post_all(session, batch)

    assert refusal.value.required == Decimal("50.0000")
    assert refusal.value.available == Decimal("40.0000")
    assert await _counts_elsewhere() == before


async def test_two_buy_orders_fill_in_one_batch_one_exhausted_and_one_partial(
    session: AsyncSession,
) -> None:
    """ADR 0024's Tests owed: 100 held, order X budget 60 and order Y budget
    30, both unspent. A trade of 5, then X fills completely for 50 and Y in
    part for 10. The batch starts at 90 reserved, and every step passes: 95
    against 90, 45 against 30, 35 against 20."""
    trader = await _trader_holding(session, "100.0000")
    await trader.order(session, budget="60.0000", filled_value="50.0000", status="filled")
    await trader.order(session, budget="30.0000", filled_value="10.0000")
    batch = [
        trader.pays("5.0000"),
        trader.pays("50.0000", drop="60.0000"),
        trader.pays("10.0000", drop="10.0000"),
    ]

    await posting.post_all(session, batch)

    assert await _balance_elsewhere(trader.user) == Decimal("35.0000")
    async with get_session_factory()() as other:
        assert await reservations.reserved_of(other, trader.user.owner_id) == Decimal(
            "20.0000"
        )


async def test_a_self_fill_sale_before_a_buy_order_fill_is_allowed(
    session: AsyncSession,
) -> None:
    """ADR 0024's Tests owed. 100 held, 60 reserved. A trade of exactly the 40
    available, a sell-order fill crediting 10, then the buy order fills for 25
    of its budget. The fill is checked against the reservation after its own
    drop, 35, not the 60 before it: a buy-order fill lowers the floor by at
    least what it debits."""
    trader = await _trader_holding(session, "100.0000")
    await trader.order(session, budget="60.0000", filled_value="25.0000")
    batch = [
        trader.pays("40.0000"),
        trader.is_paid("10.0000"),
        trader.pays("25.0000", drop="25.0000"),
    ]

    await posting.post_all(session, batch)

    assert await _balance_elsewhere(trader.user) == Decimal("45.0000")


@pytest.mark.parametrize("shape", ["negative", "two_users"])
async def test_a_malformed_reservation_drop_is_refused(
    session: AsyncSession, shape: str
) -> None:
    """A drop names one USER account's reservation and only ever lowers it. A
    negative one would lower the floor at the start of the batch; one on a
    movement between two USER accounts has no single account to lower.
    Refused before any SQL, as `MalformedBatch`."""
    trader = await _trader_holding(session, "100.0000")
    other = await _trader_holding(session, "100.0000")
    before = await _counts_elsewhere()
    if shape == "negative":
        movement = trader.pays("10.0000", drop="-10.0000")
    else:
        movement = posting.Movement(
            idempotency_key=f"between:{uuid.uuid4()}",
            kind=TransactionKind.TRADE_BUY,
            legs=[
                posting.Leg(account=trader.user, amount=Decimal("-10.0000")),
                posting.Leg(account=other.user, amount=Decimal("10.0000")),
            ],
            reservation_drop=Decimal("10.0000"),
        )

    with pytest.raises(errors.MalformedBatch):
        await posting.post_all(session, [movement])

    assert await _counts_elsewhere() == before
