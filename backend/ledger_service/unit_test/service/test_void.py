"""`voiding.void_market`: refunding a flawed market. [BE] #221, ADR 0025.

The spec is ADR 0025, "The ledger goes first, in settlement's order", and its
"Tests that matter". The races are in `test_void_concurrency.py`.

Every market here is traded through the real trade path, so the void replays
the pool's feed exactly as production writes it. **The figures are literals,
worked outside the code**, at precision 80 from `C(q) = b·ln Σ e^(q_i/b)`, a
buy's cost rounded up and a sale's proceeds and a partly-sold lot's refund
down, by a script that shares nothing with `core/`; the fills' come from
`test_cascade.py`'s. Every "nothing was written" is counted from a second
session or the audit reader.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.database import get_engine
from unit_test.conftest import idle_in_transaction
from unit_test.placement_fixtures import order_row, place, record_result, reserved_credits_of
from unit_test.settlement_fixtures import (
    admin,
    balance_of_account,
    book_lock_is_free,
    committed_counts,
    legs_of,
    platform_id,
    positions_and_q,
    transaction_by_key,
)
from unit_test.trade_fixtures import (
    PROBE_TIMEOUT,
    assert_ledger_balances,
    balance_of_user,
    books,
    count_commits,
    entities,
    errors,
    pool_balance,
    posting,
    session_factory,
)
from unit_test.void_fixtures import (
    REASON,
    VoidUpstream,
    figures,
    open_book,
    refund_key,
    refunds_by_holder,
    residue_key,
    trade,
    void,
    voided_entries,
)

ONE = Decimal("1.0000")
FLAT_B = Decimal("100.0000")
FLAT_SEED = Decimal("250.0000")


# =========================================================================
# What it refunds
# =========================================================================
async def test_a_lone_trader_who_bought_4_and_sold_2_ends_with_less_than_they_started(
    session: AsyncSession,
) -> None:
    """ADR 0025, "The cheat is closed", at `b = 1`: buy 4 (3.3251), sell 2
    (1.8912), void. The 2 left are the lot's bottom shares, refunded 1.4337,
    and the trader ends 0.0002 below their grant. Average cost would leave
    them 0.2287 up, and oldest first 0.4573."""
    upstream = VoidUpstream(b=ONE)
    await open_book(session, upstream)
    trader = uuid.uuid4()
    await trade(session, upstream, trader, outcome=0, side="buy", quantity="4")
    await trade(session, upstream, trader, outcome=0, side="sell", quantity="2")
    upstream.closes()

    result = await void(session, upstream)

    assert await refunds_by_holder(session, upstream.market_id) == {
        (trader, upstream.outcomes[0]): Decimal("1.4337")
    }
    assert await balance_of_user(session, trader) == get_settings().starting_credits - Decimal("0.0002")
    assert (result.positions_refunded, result.total_refunded) == (1, Decimal("1.4337"))


async def test_every_holder_is_refunded_once_and_the_pool_surplus_goes_to_platform(
    session: AsyncSession,
) -> None:
    """ADR 0025, "Money adds up", a pool surplus. At `b = 10` with a seed of 2:
    A buys 30 of outcome 0 (23.5545), B 12 of outcome 1 (1.0440), and C buys 5
    and sells them (4.4257, 4.4256). A and B are refunded exactly their
    charges; C holds nothing and gets nothing. The pool, 26.5986, keeps 2.0001
    after the refunds, which goes to PLATFORM, and ends at zero. Each refund
    is one transaction of two legs, the holder's and the pool's. Positions
    and `q` are not written (ADR 0025, step 7): every position and `q` reads
    as it did before the void."""
    upstream = VoidUpstream()
    await open_book(session, upstream)
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await trade(session, upstream, a, outcome=0, side="buy", quantity="30")
    await trade(session, upstream, b, outcome=1, side="buy", quantity="12")
    await trade(session, upstream, c, outcome=0, side="buy", quantity="5")
    await trade(session, upstream, c, outcome=0, side="sell", quantity="5")
    upstream.closes()
    platform_before = await balance_of_account(session, await platform_id(session))
    before = await positions_and_q(session, upstream.market_id)

    result = await void(session, upstream)

    assert await refunds_by_holder(session, upstream.market_id) == {
        (a, upstream.outcomes[0]): Decimal("23.5545"),
        (b, upstream.outcomes[1]): Decimal("1.0440"),
    }
    assert (
        result.positions_refunded,
        result.total_owed,
        result.total_refunded,
        result.refunds_cut,
        result.residue,
    ) == (2, Decimal("24.5985"), Decimal("24.5985"), False, Decimal("2.0001"))
    assert await pool_balance(session, upstream.market_id) == Decimal("0.0000")
    platform_after = await balance_of_account(session, await platform_id(session))
    assert platform_after - platform_before == Decimal("2.0001")
    refund = await transaction_by_key(
        session, refund_key(upstream.market_id, user_id=a, outcome_id=upstream.outcomes[0])
    )
    assert len(await legs_of(session, refund.id)) == 2
    assert await positions_and_q(session, upstream.market_id) == before
    await assert_ledger_balances(session)


async def test_a_shortfall_within_one_seed_is_covered_in_full_by_platform(
    session: AsyncSession,
) -> None:
    """ADR 0025, "short by at most one seed". At `b = 10`, seed 2: A buys 10
    (6.2012), B buys 30 (27.0489), A sells 10 (9.6956), a real profit of
    3.4944. The pool holds 25.5545 against B's 27.0489, short 1.4944, within
    the seed: B is refunded in full and PLATFORM puts in 1.4944. Red if refunds
    are cut within one seed."""
    upstream = VoidUpstream()
    await open_book(session, upstream)
    a, b = uuid.uuid4(), uuid.uuid4()
    await trade(session, upstream, a, outcome=0, side="buy", quantity="10")
    await trade(session, upstream, b, outcome=0, side="buy", quantity="30")
    await trade(session, upstream, a, outcome=0, side="sell", quantity="10")
    upstream.closes()

    result = await void(session, upstream)

    assert await refunds_by_holder(session, upstream.market_id) == {
        (b, upstream.outcomes[0]): Decimal("27.0489")
    }
    assert (result.refunds_cut, result.residue) == (False, Decimal("-1.4944"))
    assert await pool_balance(session, upstream.market_id) == Decimal("0.0000")
    await assert_ledger_balances(session)


async def test_beyond_one_seed_platform_puts_in_exactly_one_seed_and_refunds_are_cut(
    session: AsyncSession,
) -> None:
    """ADR 0025, "The cap", the two-account scheme. At `b = 10`, seed 2: A buys
    60 (53.0933), the helper B buys 200 (199.9753), and A sells at the top
    (59.9999), 6.9066 up. The pool, 195.0687, is 4.9066 short of B's refund,
    more than one seed. PLATFORM puts in exactly 2.0000 and B's refund is cut
    to 197.0687, the pool plus the seed. The pair is up 4.0000, two seeds and
    no more. Red if PLATFORM covers the whole gap."""
    upstream = VoidUpstream()
    await open_book(session, upstream)
    a, b = uuid.uuid4(), uuid.uuid4()
    await trade(session, upstream, a, outcome=0, side="buy", quantity="60")
    await trade(session, upstream, b, outcome=0, side="buy", quantity="200")
    await trade(session, upstream, a, outcome=0, side="sell", quantity="60")
    upstream.closes()

    result = await void(session, upstream)

    assert await refunds_by_holder(session, upstream.market_id) == {
        (b, upstream.outcomes[0]): Decimal("197.0687")
    }
    assert (result.total_owed, result.total_refunded, result.refunds_cut, result.residue) == (
        Decimal("199.9753"),
        Decimal("197.0687"),
        True,
        Decimal("-2.0000"),
    )
    gained = (
        await balance_of_user(session, a) + await balance_of_user(session, b)
        - 2 * get_settings().starting_credits
    )
    assert gained == Decimal("4.0000")
    assert await pool_balance(session, upstream.market_id) == Decimal("0.0000")
    await assert_ledger_balances(session)


async def test_a_market_nobody_traded_returns_its_whole_seed_and_opens_its_book(
    session: AsyncSession,
) -> None:
    """ADR 0025, "Money adds up", a market nobody traded. Its book is cold, so
    the void opens it, seeding the pool with 2.0000 in its own commit, and
    the residue sends all of it back: no refund, one movement, the pool at
    zero."""
    upstream = VoidUpstream()
    upstream.closes()

    result = await void(session, upstream)

    assert (result.positions_refunded, result.total_owed, result.residue) == (
        0,
        Decimal("0.0000"),
        Decimal("2.0000"),
    )
    assert await transaction_by_key(session, residue_key(upstream.market_id)) is not None
    assert await pool_balance(session, upstream.market_id) == Decimal("0.0000")


async def test_a_market_whose_every_refund_rounds_to_zero_posts_the_residue_alone(
    session: AsyncSession,
) -> None:
    """ADR 0025, "The batch is never empty". At `b = 1`: O buys 30 of outcome
    0 (29.3069), H buys 40 of outcome 1 (10.0001) and sells the top 39
    (10.0000), and O sells all 30 (28.6867). H's one share left is the bottom
    of a lot bought at `q = (30, 0)`, worth about 1.6e-13: refunded nothing,
    so no refund is written and the batch is the residue alone, the whole
    pool, 2.6203."""
    upstream = VoidUpstream(b=ONE)
    await open_book(session, upstream)
    o, h = uuid.uuid4(), uuid.uuid4()
    await trade(session, upstream, o, outcome=0, side="buy", quantity="30")
    await trade(session, upstream, h, outcome=1, side="buy", quantity="40")
    await trade(session, upstream, h, outcome=1, side="sell", quantity="39")
    await trade(session, upstream, o, outcome=0, side="sell", quantity="30")
    upstream.closes()

    result = await void(session, upstream)

    assert (result.positions_refunded, result.total_owed, result.residue) == (
        0,
        Decimal("0.0000"),
        Decimal("2.6203"),
    )
    assert await refunds_by_holder(session, upstream.market_id) == {}
    assert (
        await transaction_by_key(
            session, refund_key(upstream.market_id, user_id=h, outcome_id=upstream.outcomes[1])
        )
        is None
    )
    assert await pool_balance(session, upstream.market_id) == Decimal("0.0000")


async def test_a_filled_resting_order_is_refunded_what_its_fill_was_charged(
    session: AsyncSession,
) -> None:
    """ADR 0025, "A fill is a lot". On a flat book at `b = 100`, O rests a buy
    of 10 outcome-1 shares at 0.4500, and T's buy of 90 outcome-0 shares
    (54.8007) fills it for 2.9947 (`test_cascade.py`'s figures). The fill is a
    trade on the pool's feed, and its untouched lot is refunded exactly its
    charge."""
    upstream = VoidUpstream(b=FLAT_B, seed=FLAT_SEED)
    await open_book(session, upstream)
    o, t = uuid.uuid4(), uuid.uuid4()
    await place(
        session, upstream, user_id=o, outcome=1, limit_price=Decimal("0.4500"),
        quantity=Decimal("10.0000"),
    )
    await trade(session, upstream, t, outcome=0, side="buy", quantity="90")
    upstream.closes()

    await void(session, upstream)

    assert await refunds_by_holder(session, upstream.market_id) == {
        (o, upstream.outcomes[1]): Decimal("2.9947"),
        (t, upstream.outcomes[0]): Decimal("54.8007"),
    }


async def test_an_open_order_is_released_by_the_void(session: AsyncSession) -> None:
    """ADR 0025, "Orders are released": in the void's commit, an open order
    stops being live, with nothing written to it. It reads `released`,
    reserves nothing, and its cancel is `409 order_released`."""
    from service import cancellation, orders  # noqa: PLC0415

    upstream = VoidUpstream(b=FLAT_B, seed=FLAT_SEED)
    await open_book(session, upstream)
    owner = uuid.uuid4()
    placed = await place(
        session, upstream, user_id=owner, outcome=1, limit_price=Decimal("0.1000"),
        quantity=Decimal("5.0000"),
    )
    upstream.closes()

    await void(session, upstream)

    assert await reserved_credits_of(session, owner) == Decimal("0.0000")
    assert await orders.list_live(session, owner) == []
    assert (await order_row(session, placed.order.id)).status.value == "open"
    with pytest.raises(errors().OrderReleased):
        await cancellation.cancel(session, placed.order.id, user_id=owner)


# =========================================================================
# The stake
# =========================================================================
async def _stake_by_trading(session: AsyncSession, upstream: VoidUpstream, actor) -> None:  # noqa: ANN001
    await trade(session, upstream, actor.id, outcome=0, side="buy", quantity="3")


async def _stake_sold_to_zero(session: AsyncSession, upstream: VoidUpstream, actor) -> None:  # noqa: ANN001
    await trade(session, upstream, actor.id, outcome=0, side="buy", quantity="3")
    await trade(session, upstream, actor.id, outcome=0, side="sell", quantity="3")


async def _stake_by_a_partial_fill(session: AsyncSession, upstream: VoidUpstream, actor) -> None:  # noqa: ANN001
    """A resting buy of 100 at 0.4500 that another's buy of 90 fills only in
    part: the price stops at the limit after about 70 shares."""
    placed = await place(
        session,
        upstream,
        user_id=actor.id,
        outcome=1,
        limit_price=Decimal("0.4500"),
        quantity=Decimal("100.0000"),
    )
    await trade(session, upstream, uuid.uuid4(), outcome=0, side="buy", quantity="90")
    filled = await order_row(session, placed.order.id)
    assert (filled.status.value, filled.filled_quantity > 0) == ("open", True), (
        "the order was meant to fill in part"
    )


@pytest.mark.parametrize(
    "stake",
    [_stake_by_trading, _stake_sold_to_zero, _stake_by_a_partial_fill],
    ids=["holds", "sold-to-zero", "a-partial-fill"],
)
async def test_an_administrator_with_a_stake_is_refused_and_another_can_void(
    session: AsyncSession, audit_reader: AsyncSession, stake  # noqa: ANN001
) -> None:
    """ADR 0025, "Who may void": an administrator who ever traded the market,
    a position since sold to zero and a resting order filled in part
    included, is `403 admin_has_a_stake` with nothing written and the book
    lock free. Another administrator then voids it."""
    upstream = VoidUpstream(b=FLAT_B, seed=FLAT_SEED)
    await open_book(session, upstream)
    staked, other = admin(), admin(username="ihsan_k")
    await stake(session, upstream, staked)
    upstream.closes()
    before = await committed_counts(upstream.market_id)

    with pytest.raises(errors().AdminHasAStake):
        await void(session, upstream, actor=staked)

    assert await committed_counts(upstream.market_id) == before
    assert await voided_entries(audit_reader, staked.id) == []
    assert upstream.voids == 0
    assert await book_lock_is_free(upstream.market_id)

    result = await void(session, upstream, actor=other)
    assert result.voided_by == other.id


async def test_an_administrator_whose_order_never_filled_has_no_stake(
    session: AsyncSession,
) -> None:
    """ADR 0025: a resting order that never filled is no trade, so no stake.
    The void releases it like anyone else's."""
    upstream = VoidUpstream(b=FLAT_B, seed=FLAT_SEED)
    await open_book(session, upstream)
    actor = admin()
    await place(
        session,
        upstream,
        user_id=actor.id,
        outcome=1,
        limit_price=Decimal("0.1000"),
        quantity=Decimal("5.0000"),
    )
    upstream.closes()

    result = await void(session, upstream, actor=actor)

    assert result.voided_by == actor.id
    assert await reserved_credits_of(session, actor.id) == Decimal("0.0000")


# =========================================================================
# The guard
# =========================================================================
async def _a_position_off_by_a_tick(session: AsyncSession, upstream: VoidUpstream, holder) -> None:  # noqa: ANN001
    ents = entities()
    await session.execute(
        update(ents.Position)
        .where(ents.Position.user_id == holder, ents.Position.market_id == upstream.market_id)
        .values(quantity=ents.Position.quantity + Decimal("0.0001"))
    )
    await session.commit()


async def _a_version_gap(session: AsyncSession, upstream: VoidUpstream, holder) -> None:  # noqa: ANN001
    ents = entities()
    await session.execute(
        update(ents.MarketBook)
        .where(ents.MarketBook.market_id == upstream.market_id)
        .values(state_version=ents.MarketBook.state_version + 1)
    )
    await session.commit()


@pytest.mark.parametrize(
    "damage", [_a_position_off_by_a_tick, _a_version_gap], ids=["position", "state-version"]
)
async def test_a_replay_that_disagrees_with_the_book_is_refused_with_nothing_written(
    session: AsyncSession, audit_reader: AsyncSession, damage  # noqa: ANN001
) -> None:
    """ADR 0025, "The guard": a position that disagrees with the replay, and a
    book whose `state_version` is past the last trade, are each
    `500 void_replay_mismatch`, with nothing written and the book lock free.
    The caller traded nothing, so the stake check cannot refuse first."""
    upstream = VoidUpstream()
    await open_book(session, upstream)
    holder = uuid.uuid4()
    await trade(session, upstream, holder, outcome=0, side="buy", quantity="5")
    await damage(session, upstream, holder)
    upstream.closes()
    actor = admin()
    before = await committed_counts(upstream.market_id)

    with pytest.raises(errors().VoidReplayMismatch):
        await void(session, upstream, actor=actor)

    assert await committed_counts(upstream.market_id) == before
    assert await voided_entries(audit_reader, actor.id) == []
    assert upstream.voids == 0
    assert await book_lock_is_free(upstream.market_id)


async def test_a_replay_that_disagrees_is_refused_before_the_stake_is_checked(
    session: AsyncSession,
) -> None:
    """DECISIONS.md, "The void checks a stake after the replay's guard, not
    before": an administrator who traded a market whose replay the guard
    refuses gets `500 void_replay_mismatch`, not a `403` built on a list of
    traders nobody can trust."""
    upstream = VoidUpstream()
    await open_book(session, upstream)
    actor = admin()
    await trade(session, upstream, actor.id, outcome=0, side="buy", quantity="5")
    await _a_position_off_by_a_tick(session, upstream, actor.id)
    upstream.closes()

    with pytest.raises(errors().VoidReplayMismatch):
        await void(session, upstream, actor=actor)


# =========================================================================
# The reason, the read, the lookup and the status
# =========================================================================
async def test_a_reason_under_ten_characters_trimmed_is_refused_before_any_read(
    session: AsyncSession,
) -> None:
    """DECISIONS.md, "A void's reason is at most 5,000 characters and at least
    10 once trimmed": `422 void_incomplete`, before market_service is asked.
    Nine characters padded to thirteen, so only a trimmed length refuses it."""
    upstream = VoidUpstream()
    upstream.closes()

    with pytest.raises(errors().VoidIncomplete):
        await void(session, upstream, reason="  123456789  ")

    assert upstream.gets == 0


async def test_a_ten_character_reason_is_stored_and_forwarded_trimmed(
    session: AsyncSession,
) -> None:
    """The floor's other edge: exactly ten once trimmed is accepted, and the
    reason is stored and forwarded without its padding."""
    upstream = VoidUpstream()
    upstream.closes()

    result = await void(session, upstream, reason="   Ten chars!   ")

    assert result.reason == "Ten chars!"
    assert upstream.void_reasons == ["Ten chars!"]
    session.expire_all()
    record = await books().find_result(session, upstream.market_id)
    assert (record.kind.value, record.reason) == ("voided", "Ten chars!")


@pytest.mark.parametrize(
    ("report", "refusal"),
    [
        ({"status": "approved", "voidable": False}, "MarketNotVoidable"),
        ({"status": "settled", "voidable": False}, "MarketAlreadySettled"),
    ],
    ids=["approved-past-its-window", "settled"],
)
async def test_a_status_refusal_writes_nothing_and_opens_no_book(
    session: AsyncSession, report: dict, refusal: str
) -> None:
    """ADR 0025, step 4: with nothing recorded here, `settled` is
    `409 market_already_settled` and anything not `voidable` is
    `409 market_not_voidable`, on a cold book, which is not opened."""
    upstream = VoidUpstream()
    upstream.body.update(report)

    with pytest.raises(getattr(errors(), refusal)):
        await void(session, upstream)

    async with session_factory()() as other:
        assert await books().find(other, upstream.market_id) is None
    assert (upstream.gets, upstream.voids) == (1, 0)


async def test_a_market_voided_upstream_with_nothing_recorded_here_is_refunded(
    session: AsyncSession,
) -> None:
    """ADR 0025, "Why VOIDED is voidable": an administrator who called
    market_service's `/void` directly left a `voided` market and no refund.
    It is voidable, so this route refunds it as a first void. The holder's 12
    of outcome 1 at `b = 10` cost 7.7014."""
    upstream = VoidUpstream()
    await open_book(session, upstream)
    holder = uuid.uuid4()
    await trade(session, upstream, holder, outcome=1, side="buy", quantity="12")
    upstream.body.update(status="voided", voidable=True)

    result = await void(session, upstream)

    assert result.positions_refunded == 1
    assert await refunds_by_holder(session, upstream.market_id) == {
        (holder, upstream.outcomes[1]): Decimal("7.7014")
    }


async def test_a_market_this_ledger_settled_is_refused_at_the_lookup(
    session: AsyncSession,
) -> None:
    """ADR 0025, step 3: a settled record is `409 market_already_settled`,
    whatever market_service now reports. Upstream says `closed` and
    `voidable`, so only the record can refuse."""
    upstream = VoidUpstream()
    await open_book(session, upstream)
    await record_result(session, upstream)
    upstream.closes()

    with pytest.raises(errors().MarketAlreadySettled):
        await void(session, upstream)

    assert upstream.voids == 0


async def test_an_unreachable_market_service_refuses_even_a_voided_market(
    session: AsyncSession,
) -> None:
    """ADR 0025, step 2: the read comes first, so a repeat can be refused
    there, `503 market_terms_unavailable`, with no call to `/void`."""
    upstream = VoidUpstream()
    upstream.closes()
    await void(session, upstream)
    written = await committed_counts(upstream.market_id)
    upstream.reset_counts()
    upstream.get_raises = httpx.ConnectError("market service is down")

    with pytest.raises(errors().MarketTermsUnavailable):
        await void(session, upstream)

    assert upstream.voids == 0
    assert await committed_counts(upstream.market_id) == written


# =========================================================================
# The repeat, and the last step
# =========================================================================
async def _a_cut_market(session: AsyncSession, upstream: VoidUpstream, *, seller) -> uuid.UUID:  # noqa: ANN001
    """The two-account scheme above, with `seller` as A: refunds are cut."""
    await open_book(session, upstream)
    helper = uuid.uuid4()
    await trade(session, upstream, seller, outcome=0, side="buy", quantity="60")
    await trade(session, upstream, helper, outcome=0, side="buy", quantity="200")
    await trade(session, upstream, seller, outcome=0, side="sell", quantity="60")
    upstream.closes()
    return helper


async def test_a_repeat_answers_the_first_body_and_writes_nothing(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """ADR 0025, "A repeat answers the first body", on a market whose refunds
    were cut, so `total_owed` and `refunds_cut` are the figures a repeat has
    to work out again. The repeater traded the market and gives another
    reason, and market_service now says it is not voidable: still `200`, the
    same body, nothing written, no second audit entry, and `/void` told the
    first reason again. Red if the repeat skips the lot replay, and red if it
    runs the stake check."""
    first = admin()
    repeater = admin(username="ihsan_k")
    upstream = VoidUpstream()
    await _a_cut_market(session, upstream, seller=repeater.id)

    original = await void(session, upstream, actor=first)
    written = await committed_counts(upstream.market_id)
    upstream.body.update(voidable=False)

    repeated = await void(
        session, upstream, actor=repeater, reason="Another reason, never stored."
    )

    assert figures(repeated) == figures(original)
    assert (repeated.total_owed, repeated.refunds_cut) == (Decimal("199.9753"), True)
    assert await committed_counts(upstream.market_id) == written
    assert len(await voided_entries(audit_reader, first.id, repeater.id)) == 1
    assert upstream.void_reasons == [REASON, REASON]


async def test_an_unconfirmed_void_stands_and_a_retry_past_the_window_finishes_it(
    session: AsyncSession,
) -> None:
    """ADR 0025, "Retry after the window": the refunds commit, `/void` answers
    500, and the request is `503 void_unconfirmed` with the money moved. The
    window then passes, so market_service reports `voidable: false`. The retry
    finishes anyway: it writes nothing and forwards the first reason, not
    its own."""
    upstream = VoidUpstream()
    await open_book(session, upstream)
    holder = uuid.uuid4()
    await trade(session, upstream, holder, outcome=1, side="buy", quantity="12")
    upstream.body.update(status="approved", voidable=True)
    upstream.void_status_code = 500

    with pytest.raises(errors().VoidUnconfirmed):
        await void(session, upstream)

    assert await refunds_by_holder(session, upstream.market_id) == {
        (holder, upstream.outcomes[1]): Decimal("7.7014")
    }
    written = await committed_counts(upstream.market_id)
    upstream.body.update(voidable=False)
    upstream.void_status_code = 200

    await void(session, upstream, reason="The retry's own reason, not stored.")

    assert await committed_counts(upstream.market_id) == written
    assert upstream.void_reasons == [REASON, REASON]


async def test_market_service_is_told_with_the_administrators_own_token(
    session: AsyncSession,
) -> None:
    """ADR 0025, step 8: the caller's token, forwarded."""
    from unit_test.settlement_fixtures import admin_token  # noqa: PLC0415

    upstream = VoidUpstream()
    upstream.closes()
    actor = admin()
    token = admin_token(actor)

    await void(session, upstream, actor=actor, access_token=token)

    assert upstream.void_tokens == [token]


# =========================================================================
# One transaction, and no connection across the calls
# =========================================================================
async def test_a_warm_void_commits_exactly_once(session: AsyncSession) -> None:
    """ADR 0025, "One commit": the refunds, the residue, the record and the
    audit entry commit in exactly one database commit."""
    upstream = VoidUpstream()
    await open_book(session, upstream)
    await trade(session, upstream, uuid.uuid4(), outcome=0, side="buy", quantity="5")
    upstream.closes()

    with count_commits() as commits:
        await void(session, upstream)

    assert commits[0] == 1


async def test_a_failure_mid_batch_commits_nothing_and_calls_nobody(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, audit_reader: AsyncSession
) -> None:
    """Settlement's rollback test, pointed at the void: the second movement's
    build raises, after the record and the audit entry are staged. No refund,
    no residue, no record, no audit entry, and no call to `/void`."""
    upstream = VoidUpstream()
    await open_book(session, upstream)
    a, b = uuid.uuid4(), uuid.uuid4()
    await trade(session, upstream, a, outcome=0, side="buy", quantity="5")
    await trade(session, upstream, b, outcome=1, side="buy", quantity="5")
    upstream.closes()
    actor = admin()
    before = await committed_counts(upstream.market_id)
    real = posting()._transaction_of
    builds = 0

    def failing(**kwargs):  # noqa: ANN003, ANN202
        nonlocal builds
        builds += 1
        if builds == 2:
            raise RuntimeError("forced failure mid-batch")
        return real(**kwargs)

    monkeypatch.setattr(posting(), "_transaction_of", failing)

    with pytest.raises(RuntimeError, match="forced failure mid-batch"):
        await void(session, upstream, actor=actor)

    assert await committed_counts(upstream.market_id) == before
    assert await voided_entries(audit_reader, actor.id) == []
    assert upstream.voids == 0


@pytest.mark.parametrize("stall", ["fresh_void", "repeat_void", "first_get"])
async def test_no_connection_is_held_while_market_service_answers(
    session: AsyncSession, stall: str
) -> None:
    """Settlement's D-043 test, pointed at the void: probed at the stalled
    call from the session, the pool and Postgres. `fresh_void` is after the
    commit, `repeat_void` after the repeat's rollback, and `first_get` the
    read before any statement."""
    upstream = VoidUpstream()
    await open_book(session, upstream)
    await trade(session, upstream, uuid.uuid4(), outcome=0, side="buy", quantity="5")
    upstream.closes()
    if stall == "repeat_void":
        await void(session, upstream)
        upstream.reset_counts()

    pool = get_engine().pool
    entered, release = asyncio.Event(), asyncio.Event()
    seen: list[dict] = []

    async def stall_here(_call: int) -> None:
        seen.append(
            {
                "session in a transaction": session.in_transaction(),
                "pooled connections checked out": pool.checkedout(),
            }
        )
        entered.set()
        await release.wait()

    if stall == "first_get":
        real_transport = upstream.transport

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and not entered.is_set():
                await stall_here(1)
            return await real_transport.handle_async_request(request)

        transport = httpx.MockTransport(handler)
    else:
        upstream.on_void = stall_here
        transport = None

    task = asyncio.create_task(void(session, upstream, transport=transport))
    try:
        async with asyncio.timeout(PROBE_TIMEOUT):
            while not entered.is_set():
                if task.done():
                    task.result()
                    pytest.fail(f"timed out: the void finished without the {stall}")
                await asyncio.sleep(0.01)
        idle = await idle_in_transaction()
    except TimeoutError:
        pytest.fail(f"timed out: the {stall} was never reached")
    finally:
        release.set()
        async with asyncio.timeout(PROBE_TIMEOUT):
            await asyncio.gather(task, return_exceptions=True)

    clean = {"session in a transaction": False, "pooled connections checked out": 0}
    assert seen == [clean]
    assert idle == 0, f"{idle} backend(s) idle in transaction across the {stall}"
    task.result()


async def test_a_void_past_both_lookups_is_refused_as_pending_writes(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, audit_reader: AsyncSession
) -> None:
    """ADR 0019's first trap, carried over: the record is the ORM write
    pending when `post_all` is entered. Both lookups made to miss on a market
    already voided, the second void reaches `post_all` with its keys written
    and is refused `pending_writes_on_replay`. No second `market.voided`."""
    upstream = VoidUpstream()
    await open_book(session, upstream)
    await trade(session, upstream, uuid.uuid4(), outcome=0, side="buy", quantity="5")
    upstream.closes()
    first, second = admin(), admin(username="ihsan_k")
    await void(session, upstream, actor=first)
    written = await committed_counts(upstream.market_id)

    async def always_misses(caller_session, market_id):  # noqa: ANN001, ANN202
        return None

    monkeypatch.setattr(books(), "find_result", always_misses)

    with pytest.raises(errors().PendingWritesOnReplay):
        await void(session, upstream, actor=second)

    assert await committed_counts(upstream.market_id) == written
    assert len(await voided_entries(audit_reader, first.id, second.id)) == 1


# =========================================================================
# Audit and history
# =========================================================================
async def test_market_voided_carries_the_reason_and_the_figures(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """ADR 0025: one `market.voided` per void that writes, in its commit, with
    the trimmed reason and whether refunds were cut. The cut market's
    figures."""
    upstream = VoidUpstream()
    await _a_cut_market(session, upstream, seller=uuid.uuid4())
    actor = admin()

    result = await void(session, upstream, actor=actor, reason=f"  {REASON}  ")

    entries = await voided_entries(audit_reader, actor.id)
    assert len(entries) == 1
    entry = entries[0]
    assert isinstance(entry.pop("id"), uuid.UUID)
    assert entry == {
        "occurred_at": result.recorded_at,
        "actor_id": actor.id,
        "actor_username": actor.username,
        "actor_role": "admin",
        "action_type": "market.voided",
        "target_type": "market",
        "target_id": upstream.market_id,
        "target_label": None,
        "reason": REASON,
        "context": {
            "positions_refunded": 1,
            "total_owed": "199.9753",
            "total_refunded": "197.0687",
            "refunds_cut": True,
            "residue": "-2.0000",
        },
        "source_service": "ledger_service",
    }


async def test_a_refund_is_one_history_row_naming_the_outcome_and_the_shares(
    session: AsyncSession,
) -> None:
    """ADR 0025, "History": the refund's context names the holder, the
    market, the outcome and the shares refunded, which is what the history's
    `void_refund` row reads (stack PR 11)."""
    upstream = VoidUpstream()
    await open_book(session, upstream)
    holder = uuid.uuid4()
    await trade(session, upstream, holder, outcome=1, side="buy", quantity="12")
    upstream.closes()

    await void(session, upstream)

    refund = await transaction_by_key(
        session, refund_key(upstream.market_id, user_id=holder, outcome_id=upstream.outcomes[1])
    )
    assert refund.kind.value == "void_refund"
    assert refund.context == {
        "user_id": str(holder),
        "market_id": str(upstream.market_id),
        "outcome_id": str(upstream.outcomes[1]),
        "quantity": "12.0000",
    }


# =========================================================================
# Speed
# =========================================================================
def _trade_rows(
    *, market_id: uuid.UUID, pool_id: uuid.UUID, trades: list[tuple], outcome_ids: list
) -> tuple[list[dict], list[dict]]:
    """Transactions and entries for `(user_id, account_id, position, side,
    quantity, total)` trades, shaped as `write_trade` writes them, stamped a
    microsecond apart in feed order and versioned 1, 2, 3, ..."""
    start = datetime.now(UTC) - timedelta(days=1)
    ents = entities()
    kinds = {"buy": ents.TransactionKind.TRADE_BUY, "sell": ents.TransactionKind.TRADE_SELL}
    transactions, entries = [], []
    for version, (user_id, account_id, position, side, quantity, total) in enumerate(
        trades, start=1
    ):
        transaction_id = uuid.uuid4()
        stamp = start + timedelta(microseconds=version)
        signed = -total if side == "buy" else total
        transactions.append(
            {
                "id": transaction_id,
                "kind": kinds[side],
                "idempotency_key": f"speed:{transaction_id}",
                "request_fingerprint": "speed",
                "occurred_at": stamp,
                "context": {
                    "user_id": str(user_id),
                    "market_id": str(market_id),
                    "outcome_id": str(outcome_ids[position]),
                    "side": side,
                    "quantity": str(quantity),
                    "total": str(signed),
                    "state_version": version,
                },
            }
        )
        entries.append(
            {"id": uuid.uuid4(), "transaction_id": transaction_id, "account_id": account_id,
             "amount": signed, "created_at": stamp}
        )
        entries.append(
            {"id": uuid.uuid4(), "transaction_id": transaction_id, "account_id": pool_id,
             "amount": -signed, "created_at": stamp}
        )
    return transactions, entries


# Each holder's trades in the 10,000-trade case: three partial sales, each
# off the newest lot, so a third of the buys are partly-sold lots to price.
_TEN_TRADES = [
    ("buy", "1.0000", "0.5000"),
    ("buy", "2.0000", "1.0000"),
    ("sell", "1.0000", "0.4000"),
    ("buy", "0.5000", "0.2500"),
    ("buy", "1.0000", "0.5000"),
    ("sell", "0.5000", "0.2000"),
    ("buy", "1.0000", "0.5000"),
    ("buy", "1.0000", "0.5000"),
    ("sell", "1.0000", "0.4500"),
    ("buy", "0.5000", "0.2500"),
]


@pytest.mark.parametrize(
    ("holders", "pattern"),
    [(1000, [("buy", "1.0000", "0.5000")]), (1000, _TEN_TRADES)],
    ids=["1000-positions", "10000-trades"],
)
async def test_a_void_finishes_within_five_seconds(
    session: AsyncSession, holders: int, pattern: list
) -> None:
    """ADR 0025, "Speed": 1,000 positions voided in under five seconds at the
    service, settlement's bar, and the replay held to the same bar against
    10,000 trades: the same 1,000 holders trading ten times each, with three
    partly-sold lots each to price. The calls to market_service are mocks
    that answer at once. Trades, positions and `q` are inserted in bulk,
    consistent with each other, so the setup is not what is timed."""
    ents = entities()
    upstream = VoidUpstream(b=FLAT_B, seed=FLAT_SEED)
    book = await open_book(session, upstream)
    upstream.closes()

    rows, positions, q = [], [], [Decimal(0), Decimal(0)]
    accounts_rows = []
    for index in range(holders):
        user_id, account_id = uuid.uuid4(), uuid.uuid4()
        position = index % 2
        accounts_rows.append(
            {"id": account_id, "kind": ents.AccountKind.USER, "owner_id": user_id}
        )
        held = Decimal(0)
        for side, quantity, total in pattern:
            rows.append(
                (user_id, account_id, position, side, Decimal(quantity), Decimal(total))
            )
            held += Decimal(quantity) if side == "buy" else -Decimal(quantity)
        q[position] += held
        positions.append((user_id, position, held))

    await session.execute(insert(ents.Account), accounts_rows)
    transactions, entries = _trade_rows(
        market_id=upstream.market_id,
        pool_id=book.pool_account_id,
        trades=rows,
        outcome_ids=upstream.outcomes,
    )
    await session.execute(insert(ents.Transaction), transactions)
    await session.execute(insert(ents.Entry), entries)
    now = datetime.now(UTC)
    await session.execute(
        insert(ents.Position),
        [
            {
                "user_id": user_id,
                "market_id": upstream.market_id,
                "outcome_id": upstream.outcomes[position],
                "quantity": held,
                "cost_basis": held,
                "created_at": now,
                "updated_at": now,
            }
            for user_id, position, held in positions
        ],
    )
    for position, value in enumerate(q):
        await session.execute(
            update(ents.MarketOutcome)
            .where(
                ents.MarketOutcome.market_id == upstream.market_id,
                ents.MarketOutcome.position == position,
            )
            .values(q=value)
        )
    await session.execute(
        update(ents.MarketBook)
        .where(ents.MarketBook.market_id == upstream.market_id)
        .values(state_version=len(rows))
    )
    await session.commit()

    started = time.perf_counter()
    result = await void(session, upstream)
    elapsed = time.perf_counter() - started
    print(f"\nvoided {holders} positions from {len(rows)} trades in {elapsed:.3f} s")

    assert result.positions_refunded == holders
    assert elapsed < 5.0, f"the void took {elapsed:.3f} s, over the 5 s budget"
    assert await pool_balance(session, upstream.market_id) == Decimal("0.0000")
