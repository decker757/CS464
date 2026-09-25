"""Selling shares. [T-3] #23

Business rules, driven through `service/trading.py::execute` with
`side=Side.SELL` and no HTTP inbound. The route is
`unit_test/controller/test_sell_routes.py`'s; retries are
`test_sell_replay.py`'s; the publish is `test_sell_publish.py`'s; the races are
`test_sell_concurrency.py`'s. The pure cost-basis rule is
`unit_test/core/test_cost_basis.py`'s, and what is asserted here is that the
trade path writes what that rule says.

**Every book is opened at zero by its first touch, and every holding is made
by a real buy** — never `warm()`, never a position inserted by hand. The one
exception is `test_the_outstanding_check_is_a_backstop_when_q_is_written_below_a_holding`,
which exists to write `q` directly ("Shares outstanding equal the sum of
positions, so the outstanding check is a backstop on the trade route").

**"Writes nothing" is asked twice** — in the request's own session before any
rollback, which catches a path that writes and then refuses, and from a second
session afterwards, which catches a committed write. See
`sell_fixtures.assert_wrote_nothing`.

**The numbers are the issue's worked example**, which real trades reach at
`b = 137`: buying `54.3333` on a book at zero charges `29.8428`, selling
`10.0000` of it pays `5.8905` and leaves `24.3503`. Each of those three
changes if its rounding direction does. Everything is compared with `==` on a
`Decimal`.
"""

from __future__ import annotations

import uuid
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.sell_fixtures import (
    AT_ZERO,
    BASIS,
    HELD,
    PROCEEDS,
    REMAINING_BASIS,
    REMAINING_QUANTITY,
    SOLD,
    TICK,
    assert_basis_rounding_is_load_bearing,
    assert_q_matches_positions,
    assert_sell_rounding_is_load_bearing,
    assert_wrote_nothing,
    ceiled_proceeds,
    expected_proceeds,
    footprint,
    hold,
    holding,
    insufficient_shares_held,
    open_at_zero,
    q_now,
    remaining_basis,
    sell,
    version_of,
)
from unit_test.trade_fixtures import (
    QUANTUM,
    SMALL_QUANTITY,
    SUBSIDY,
    Recorder,
    Upstream,
    assert_ledger_balances,
    balance_of_user,
    capture_sql,
    entities,
    errors,
    expected_total,
    floored_total,
    fund,
    locks,
    pool_balance,
    preview,
    pricing,
    raw_cost,
    token,
)


async def _holder(session: AsyncSession, *, outcomes: int = 2):
    """A market opened at zero and one trader holding the worked example:
    `54.3333` of outcome 0 at a basis of `29.8428`, `q = [54.3333, 0, …]`,
    `state_version = 1`."""
    upstream = Upstream(outcomes=outcomes)
    user_id = uuid.uuid4()
    await open_at_zero(session, upstream)
    await fund(session, user_id)
    await hold(session, upstream, user_id=user_id)
    return upstream, user_id


def _sell_q(held: Decimal = HELD) -> list[Decimal]:
    return [held, Decimal(0)]


# =========================================================================
# The constants these tests are built on
# =========================================================================
def test_the_worked_example_constants_are_what_the_engine_says() -> None:
    """Not a test of the trade path: a test that the pinned literals are what
    `core/lmsr.py` and the stated rules produce, and that every rounding
    direction they rest on is observable."""
    assert expected_total(list(AT_ZERO), 0, "buy", HELD) == -BASIS
    assert floored_total(list(AT_ZERO), 0, HELD) != -BASIS

    assert expected_proceeds(_sell_q(), 0, SOLD) == PROCEEDS
    assert_sell_rounding_is_load_bearing(_sell_q(), 0, SOLD)

    assert remaining_basis(BASIS, HELD, SOLD) == REMAINING_BASIS
    assert_basis_rounding_is_load_bearing(BASIS, HELD, SOLD)


# =========================================================================
# Proceeds
# =========================================================================
async def test_a_sell_credits_exactly_the_total_the_preview_quoted(
    session: AsyncSession,
) -> None:
    """The preview is taken first against the same committed state, and the
    sell is asserted to credit that exact quote — the floored proceeds, not
    the engine's unrounded figure."""
    upstream, user_id = await _holder(session)

    quote = await preview().quote(
        session,
        upstream.market_id,
        outcome_id=upstream.outcomes[0],
        side=pricing().Side.SELL,
        quantity=SOLD,
        access_token=token(),
        transport=upstream.transport,
    )
    before = await balance_of_user(session, user_id)

    trade = await sell(
        session, upstream, user_id=user_id, state_version=quote.state_version
    )

    assert quote.total == PROCEEDS
    assert trade.total == quote.total
    assert await balance_of_user(session, user_id) == before + PROCEEDS


async def test_the_proceeds_round_floor_and_not_ceiling(session: AsyncSession) -> None:
    """A sell rounds down, so the residue stays with the pool. Asserted as a
    direction: the credited total is the floor and is not the ceiling."""
    upstream, user_id = await _holder(session)

    trade = await sell(session, upstream, user_id=user_id)

    assert trade.total == PROCEEDS
    assert trade.total != ceiled_proceeds(_sell_q(), 0, SOLD)
    assert trade.total < ceiled_proceeds(_sell_q(), 0, SOLD)


async def test_the_total_is_positive_and_the_result_carries_no_prices(
    session: AsyncSession,
) -> None:
    """Credits arrive, so `total` is positive — the preview's sign
    convention. No price and no `average_price` on the result, checked over
    the attribute names."""
    upstream, user_id = await _holder(session)

    trade = await sell(session, upstream, user_id=user_id)

    assert trade.total > 0
    assert trade.side == "sell"
    assert trade.quantity == SOLD
    assert [name for name in vars(trade) if "price" in name] == []


async def test_a_sell_writes_two_entries_crediting_the_trader_and_debiting_the_pool(
    session: AsyncSession,
) -> None:
    from service import accounts  # noqa: PLC0415

    upstream, user_id = await _holder(session)
    ents = entities()

    trade = await sell(session, upstream, user_id=user_id)

    user = await accounts.find(session, ents.AccountKind.USER, user_id)
    pool = await accounts.find(session, ents.AccountKind.MARKET_POOL, upstream.market_id)
    rows = (
        await session.execute(
            select(ents.Entry.account_id, ents.Entry.amount).where(
                ents.Entry.transaction_id == trade.transaction_id
            )
        )
    ).all()

    assert sorted((str(a), amt) for a, amt in rows) == sorted(
        [(str(user.id), PROCEEDS), (str(pool.id), -PROCEEDS)]
    )
    await assert_ledger_balances(session)


async def test_the_transaction_kind_is_trade_sell(session: AsyncSession) -> None:
    upstream, user_id = await _holder(session)
    ents = entities()

    trade = await sell(session, upstream, user_id=user_id)

    kind = (
        await session.execute(
            select(ents.Transaction.kind).where(
                ents.Transaction.id == trade.transaction_id
            )
        )
    ).scalar_one()
    assert kind is ents.TransactionKind.TRADE_SELL
    assert ents.TransactionKind.TRADE_SELL.value == "trade_sell"


async def test_a_sell_moves_q_down_and_the_version_up_by_one(
    session: AsyncSession,
) -> None:
    upstream, user_id = await _holder(session)

    trade = await sell(session, upstream, user_id=user_id)

    assert await q_now(session, upstream.market_id) == [REMAINING_QUANTITY, Decimal(0)]
    assert await version_of(session, upstream.market_id) == 2
    assert trade.state_version == 2


async def test_proceeds_below_a_tick_are_refused_and_write_nothing(
    session: AsyncSession,
) -> None:
    """`422 proceeds_below_tick`, from holdings made by a real buy.

    `13.3333` of outcome 0 bought on a book at zero; one tick of it sold back.
    The raw proceeds are `0.0000524311…` — a tick's worth of shares at a price
    of about `0.5243`, and a price is always below 1 — so the floor is exactly
    `0.0000`. The ceiling and half-up would both pay `0.0001`, which is what
    makes this a statement about the rounding direction.
    """
    upstream = Upstream()
    user_id = uuid.uuid4()
    await open_at_zero(session, upstream)
    await fund(session, user_id)
    await hold(session, upstream, user_id=user_id, quantity=SMALL_QUANTITY)

    held_q = [SMALL_QUANTITY, Decimal(0)]
    magnitude = raw_cost(held_q, 0, "sell", TICK).copy_abs()
    assert 0 < magnitude < TICK
    assert magnitude.quantize(QUANTUM, rounding=ROUND_CEILING) == TICK
    assert magnitude.quantize(QUANTUM, rounding=ROUND_HALF_UP) == TICK

    before = await footprint(session, upstream.market_id)
    assert before.q == (SMALL_QUANTITY, Decimal(0))
    assert before.state_version == 1
    assert await holding(
        session, user_id, upstream.market_id, upstream.outcomes[0]
    ) == (SMALL_QUANTITY, Decimal("6.8288"))

    recorder = Recorder()
    with pytest.raises(errors().ProceedsBelowTick):
        await sell(
            session, upstream, user_id=user_id, quantity=TICK, redis_client=recorder
        )

    await assert_wrote_nothing(session, upstream.market_id, before)
    assert recorder.calls == []


# =========================================================================
# The holding check
# =========================================================================
async def test_a_sell_larger_than_the_position_is_refused_with_held_and_requested(
    session: AsyncSession,
) -> None:
    upstream, user_id = await _holder(session)
    over = HELD + TICK
    before = await footprint(session, upstream.market_id)
    recorder = Recorder()

    with pytest.raises(insufficient_shares_held()) as refused:
        await sell(
            session, upstream, user_id=user_id, quantity=over, redis_client=recorder
        )

    assert refused.value.held == HELD
    assert refused.value.requested == over
    await assert_wrote_nothing(session, upstream.market_id, before)
    assert recorder.calls == []


async def test_a_caller_with_no_position_is_refused_with_held_zero(
    session: AsyncSession,
) -> None:
    upstream, _ = await _holder(session)
    stranger = uuid.uuid4()
    await fund(session, stranger)
    before = await footprint(session, upstream.market_id)

    with pytest.raises(insufficient_shares_held()) as refused:
        await sell(session, upstream, user_id=stranger)

    assert refused.value.held == Decimal("0.0000")
    assert refused.value.requested == SOLD
    await assert_wrote_nothing(session, upstream.market_id, before)


async def test_a_non_holder_selling_a_tick_is_refused_held_not_proceeds_below_tick(
    session: AsyncSession,
) -> None:
    """The holding check runs before any pricing ("A sell larger than the
    caller's holding is `insufficient_shares_held`": checked after
    `unknown_outcome` and before the outstanding check and any pricing). A
    tick's proceeds floor to zero, so a path that priced first would answer
    with the wrong code."""
    upstream, _ = await _holder(session)
    stranger = uuid.uuid4()
    await fund(session, stranger)

    with pytest.raises(insufficient_shares_held()):
        await sell(session, upstream, user_id=stranger, quantity=TICK)
    await session.rollback()


async def test_a_user_cannot_sell_shares_another_user_holds(
    session: AsyncSession,
) -> None:
    """Two holders; `q` covers the smaller one's sell many times over. The
    check is against this caller's own row, not the market's total."""
    upstream, big = await _holder(session)
    small = uuid.uuid4()
    await fund(session, small)
    await hold(session, upstream, user_id=small, quantity=SMALL_QUANTITY)

    request = Decimal("41.0000")
    assert (await q_now(session, upstream.market_id))[0] == HELD + SMALL_QUANTITY
    before = await footprint(session, upstream.market_id)

    with pytest.raises(insufficient_shares_held()) as refused:
        await sell(session, upstream, user_id=small, quantity=request)

    assert refused.value.held == SMALL_QUANTITY
    assert refused.value.requested == request
    await assert_wrote_nothing(session, upstream.market_id, before)


async def test_a_position_in_one_outcome_cannot_fund_a_sell_of_another(
    session: AsyncSession,
) -> None:
    """The caller holds outcome 0 only. Someone else holds outcome 1, so
    outcome 1's `q` would cover the sell — the outstanding check would not
    refuse it. The holding check has to."""
    upstream, user_id = await _holder(session)
    other = uuid.uuid4()
    await fund(session, other)
    await hold(session, upstream, user_id=other, quantity=Decimal("41.0000"), outcome=1)
    before = await footprint(session, upstream.market_id)

    with pytest.raises(insufficient_shares_held()) as refused:
        await sell(session, upstream, user_id=user_id, outcome=1)

    assert refused.value.held == Decimal("0.0000")
    await assert_wrote_nothing(session, upstream.market_id, before)


async def test_the_holding_check_is_read_after_the_book_lock_and_takes_no_lock(
    session: AsyncSession,
) -> None:
    """"The holdings check is read under the book lock, and the position
    takes no lock of its own", as statement order. Every read of
    `ledger.positions` in the sell comes after the `market_books … FOR
    UPDATE`, and none of them is itself `FOR UPDATE`."""
    from core.database import SCHEMA  # noqa: PLC0415

    upstream, user_id = await _holder(session)
    table = f"{SCHEMA}.positions"

    with capture_sql() as statements:
        with pytest.raises(insufficient_shares_held()):
            await sell(session, upstream, user_id=user_id, quantity=HELD + TICK)
    await session.rollback()

    book_lock = next(
        (
            i
            for i, s in enumerate(statements)
            if "market_books" in s and "for update" in s.lower()
        ),
        None,
    )
    assert book_lock is not None, "the sell never locked the book row"

    position_reads = [
        (i, s)
        for i, s in enumerate(statements)
        if table in s and s.lstrip().lower().startswith("select")
    ]
    assert position_reads, "the sell never read the caller's position"
    assert all(i > book_lock for i, _ in position_reads), (
        "the position was read before the book lock, so nothing held it still"
    )
    assert [s for _, s in position_reads if s in locks(statements)] == [], (
        "the position read takes a lock of its own — a lock nothing depends on"
    )


# =========================================================================
# Cost basis, through the trade path
# =========================================================================
async def test_a_partial_sell_leaves_the_half_up_remaining_basis(
    session: AsyncSession,
) -> None:
    upstream, user_id = await _holder(session)

    await sell(session, upstream, user_id=user_id)

    assert await holding(
        session, user_id, upstream.market_id, upstream.outcomes[0]
    ) == (REMAINING_QUANTITY, REMAINING_BASIS)


async def test_a_position_sold_down_in_pieces_releases_exactly_what_was_paid(
    session: AsyncSession,
) -> None:
    """Three sells whose sizes do not divide the holding. The basis after
    each is the rule's, and the released amounts sum to the `29.8428` paid."""
    upstream, user_id = await _holder(session)
    outcome = upstream.outcomes[0]

    held, basis = HELD, BASIS
    released = []
    for piece in (Decimal("10.0000"), Decimal("7.7777"), Decimal("36.5556")):
        await sell(session, upstream, user_id=user_id, quantity=piece)
        expected = remaining_basis(basis, held, piece)
        held -= piece
        assert await holding(session, user_id, upstream.market_id, outcome) == (
            held,
            expected,
        )
        released.append(basis - expected)
        basis = expected

    assert released == [Decimal("5.4925"), Decimal("4.2719"), Decimal("20.0784")]
    assert sum(released) == BASIS


async def test_a_full_exit_leaves_0_0000_and_0_0000(session: AsyncSession) -> None:
    upstream, user_id = await _holder(session)

    await sell(session, upstream, user_id=user_id, quantity=HELD)

    quantity, basis = await holding(
        session, user_id, upstream.market_id, upstream.outcomes[0]
    )
    assert (str(quantity), str(basis)) == ("0.0000", "0.0000")


async def test_selling_all_but_a_tick_leaves_0_0001_at_0_0001(
    session: AsyncSession,
) -> None:
    """The dust case in "A sell releases cost basis at average cost". [T-4]
    #24 should expect an average entry of `1.0000` here."""
    upstream, user_id = await _holder(session)

    await sell(session, upstream, user_id=user_id, quantity=HELD - TICK)

    assert await holding(
        session, user_id, upstream.market_id, upstream.outcomes[0]
    ) == (TICK, TICK)


async def test_a_profitable_and_a_losing_sell_of_one_size_leave_the_same_basis(
    session: AsyncSession,
) -> None:
    """Cost basis is net of released basis, never of proceeds.

    The same holding in two markets. In one, another trader buys the same
    outcome and the price rises; in the other they buy the opposite outcome
    and it falls. The same ten shares are sold in each: one for more than the
    `5.4925` of basis it releases, one for less. The remaining basis is
    `24.3503` in both.
    """
    results = {}
    for direction, other_outcome in (("profit", 0), ("loss", 1)):
        upstream, user_id = await _holder(session)
        other = uuid.uuid4()
        await fund(session, other)
        await hold(
            session,
            upstream,
            user_id=other,
            quantity=Decimal("41.0000"),
            outcome=other_outcome,
        )
        trade = await sell(session, upstream, user_id=user_id)
        results[direction] = (
            trade.total,
            await holding(session, user_id, upstream.market_id, upstream.outcomes[0]),
        )

    released = BASIS - REMAINING_BASIS
    assert results["profit"][0] > released, "the 'profitable' sell was not one"
    assert results["loss"][0] < released, "the 'losing' sell was not one"
    assert results["profit"][1] == results["loss"][1] == (
        REMAINING_QUANTITY,
        REMAINING_BASIS,
    )


async def test_a_position_sold_to_zero_keeps_its_row_and_a_rebuy_is_a_fresh_basis(
    session: AsyncSession,
) -> None:
    """"A position sold to zero keeps its row." Emptied, the row is still
    there at `0/0`; a later buy lands on it with that buy's own cost as the
    whole basis — `13.3333` on a book back at zero costs `6.8288`."""
    upstream, user_id = await _holder(session)
    outcome = upstream.outcomes[0]

    await sell(session, upstream, user_id=user_id, quantity=HELD)
    assert await holding(session, user_id, upstream.market_id, outcome) == (
        Decimal("0.0000"),
        Decimal("0.0000"),
    )

    await hold(session, upstream, user_id=user_id, quantity=SMALL_QUANTITY)

    assert await holding(session, user_id, upstream.market_id, outcome) == (
        SMALL_QUANTITY,
        Decimal("6.8288"),
    )
    assert -expected_total(list(AT_ZERO), 0, "buy", SMALL_QUANTITY) == Decimal("6.8288")


async def test_realized_pnl_is_stored_nowhere(session: AsyncSession) -> None:
    """"Realized P&L is not stored": the sell's context is exactly the seven
    fields a buy's is, and `positions` has no column beyond #22's."""
    upstream, user_id = await _holder(session)
    ents = entities()

    trade = await sell(session, upstream, user_id=user_id)

    context = (
        await session.execute(
            select(ents.Transaction.context).where(
                ents.Transaction.id == trade.transaction_id
            )
        )
    ).scalar_one()
    assert set(context) == {
        "user_id",
        "market_id",
        "outcome_id",
        "side",
        "quantity",
        "total",
        "state_version",
    }
    assert context["side"] == "sell"
    assert Decimal(context["total"]) == PROCEEDS

    assert {c.name for c in ents.Position.__table__.columns} == {
        "user_id",
        "market_id",
        "outcome_id",
        "quantity",
        "cost_basis",
        "created_at",
        "updated_at",
    }


# =========================================================================
# Invariants, on books opened at zero
# =========================================================================
# (user index, side, outcome, quantity). Each sell is within the seller's
# holding at that point by construction, and three outcomes so a bug that
# mixes up which outcome moved has two ways to show itself.
_MIXED = [
    (0, "buy", 0, Decimal("54.3333")),
    (1, "buy", 1, Decimal("41.0000")),
    (2, "buy", 2, Decimal("13.3333")),
    (0, "sell", 0, Decimal("10.0000")),
    (1, "buy", 0, Decimal("29.7777")),
    (1, "sell", 1, Decimal("41.0000")),
    (2, "sell", 2, Decimal("7.7777")),
    (0, "sell", 0, Decimal("44.3333")),
    (1, "sell", 0, Decimal("13.3333")),
    (2, "buy", 1, Decimal("17.1717")),
]


async def _mixed(session: AsyncSession, after_each) -> None:
    upstream = Upstream(outcomes=3)
    users = [uuid.uuid4() for _ in range(3)]
    await open_at_zero(session, upstream)
    for user_id in users:
        await fund(session, user_id)
    await after_each(upstream)

    for who, side, outcome, quantity in _MIXED:
        driver = hold if side == "buy" else sell
        await driver(
            session, upstream, user_id=users[who], outcome=outcome, quantity=quantity
        )
        await after_each(upstream)


async def test_q_equals_the_sum_of_positions_after_mixed_buys_and_sells(
    session: AsyncSession,
) -> None:
    async def check(upstream: Upstream) -> None:
        await assert_q_matches_positions(session, upstream.market_id)

    await _mixed(session, check)
    await assert_ledger_balances(session)


async def test_the_pool_never_falls_below_its_subsidy_across_mixed_trading(
    session: AsyncSession,
) -> None:
    """"A sell cannot take a market's pool below its seed subsidy", after
    every step of an ordinary mixed sequence. Does not try to reach the
    extreme-skew exception."""
    async def check(upstream: Upstream) -> None:
        assert await pool_balance(session, upstream.market_id) >= SUBSIDY

    await _mixed(session, check)


async def test_the_outstanding_check_is_a_backstop_when_q_is_written_below_a_holding(
    session: AsyncSession,
) -> None:
    """The one test that writes `q` directly, because it exists to.

    A corrupted book: the trader holds `54.3333`, `q` says `10.0000`. A sell
    of `41.0000` passes the holding check and must be refused by
    `insufficient_shares_outstanding` rather than writing a negative `q`.
    """
    upstream, user_id = await _holder(session)
    outcome = entities().MarketOutcome
    await session.execute(
        update(outcome)
        .where(outcome.market_id == upstream.market_id, outcome.position == 0)
        .values(q=Decimal("10.0000"))
    )
    await session.commit()
    before = await footprint(session, upstream.market_id)

    with pytest.raises(errors().InsufficientSharesOutstanding):
        await sell(session, upstream, user_id=user_id, quantity=Decimal("41.0000"))

    await assert_wrote_nothing(session, upstream.market_id, before)


async def test_buy_then_sell_round_trips_and_never_profits(
    session: AsyncSession,
) -> None:
    """`54.3333` bought for `29.8428` and sold straight back for `29.8427`:
    the same raw figure, ceiled one way and floored the other. `q` is back at
    zero, the position is `0/0`, and the one-tick residue is the pool's."""
    upstream = Upstream()
    user_id = uuid.uuid4()
    await open_at_zero(session, upstream)
    credits = await fund(session, user_id)

    await hold(session, upstream, user_id=user_id)
    trade = await sell(session, upstream, user_id=user_id, quantity=HELD)

    assert trade.total == Decimal("29.8427")
    assert await q_now(session, upstream.market_id) == [Decimal(0), Decimal(0)]
    assert await holding(
        session, user_id, upstream.market_id, upstream.outcomes[0]
    ) == (Decimal("0.0000"), Decimal("0.0000"))
    assert await balance_of_user(session, user_id) - credits == Decimal("-0.0001")
    assert await pool_balance(session, upstream.market_id) == SUBSIDY + Decimal("0.0001")
    await assert_ledger_balances(session)


async def test_a_trader_with_a_zero_balance_can_still_sell(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The grant is set to exactly what the holding costs, so the buy leaves
    the trader at `0.0000` through real paths only. A sell credits them; the
    overdraft check has nothing to refuse."""
    from core.config import get_settings  # noqa: PLC0415

    monkeypatch.setattr(get_settings(), "starting_credits", BASIS)
    upstream, user_id = await _holder(session)
    assert await balance_of_user(session, user_id) == Decimal("0.0000")

    trade = await sell(session, upstream, user_id=user_id)

    assert trade.total == PROCEEDS
    assert await balance_of_user(session, user_id) == PROCEEDS


# =========================================================================
# The rules a sell shares with a buy
# =========================================================================
async def test_a_stale_sell_is_refused_with_quoted_and_current(
    session: AsyncSession,
) -> None:
    upstream, user_id = await _holder(session)
    before = await footprint(session, upstream.market_id)
    recorder = Recorder()

    with pytest.raises(errors().QuoteStale) as stale:
        await sell(
            session, upstream, user_id=user_id, state_version=0, redis_client=recorder
        )

    assert (stale.value.quoted, stale.value.current) == (0, 1)
    await assert_wrote_nothing(session, upstream.market_id, before)
    assert recorder.calls == []


async def test_a_sell_on_a_closed_market_is_refused_market_closed(
    session: AsyncSession,
) -> None:
    upstream, user_id = await _holder(session)
    upstream.closes()
    before = await footprint(session, upstream.market_id)
    recorder = Recorder()

    with pytest.raises(errors().MarketClosed):
        await sell(session, upstream, user_id=user_id, redis_client=recorder)

    await assert_wrote_nothing(session, upstream.market_id, before)
    assert recorder.calls == []


async def test_the_preview_quotes_a_sell_the_trade_refuses_held(
    session: AsyncSession,
) -> None:
    """The deliberate asymmetry: the preview has no lock and checks no
    holdings, so it prices a sell by a caller who holds nothing; the trade
    refuses it."""
    upstream, _ = await _holder(session)
    stranger = uuid.uuid4()
    await fund(session, stranger)

    quote = await preview().quote(
        session,
        upstream.market_id,
        outcome_id=upstream.outcomes[0],
        side=pricing().Side.SELL,
        quantity=SOLD,
        access_token=token(),
        transport=upstream.transport,
    )
    assert quote.total == PROCEEDS

    with pytest.raises(insufficient_shares_held()):
        await sell(
            session, upstream, user_id=stranger, state_version=quote.state_version
        )
    await session.rollback()
