"""What a trade will cost, before anybody commits to it. [T-1] #21

The arithmetic, the reads it makes and the writes it does not, driven through
`service/preview.py`; status codes and the wire shape are
`test_preview_routes.py`'s. The outbound call is stubbed at the transport.
Not tested here: whether the market is open (ADR 0017 keeps that off the
preview) and the caller's own holdings (the trade's check, D-012).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from decimal import ROUND_HALF_UP, Decimal

import httpx
import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_engine
from core.lmsr import cost_to_trade
from model.entities import Entry
from unit_test.book_fixtures import (
    B,
    Q,
    QUANTUM,
    SUBSIDY,
    Upstream,
    book_row,
    books,
    capture_sql,
    entities,
    errors,
    expected_prices,
    fresh_token,
    mentioning,
    set_q,
    warm,
    writes,
)
from unit_test.conftest import strip_outcomes


def _preview():
    """Imported inside each test, so a missing name fails one test rather than
    collection (D-007)."""
    from service import preview  # noqa: PLC0415

    return preview


def _pricing():
    """`core/pricing.py`, reached lazily like `_preview`."""
    from core import pricing  # noqa: PLC0415

    return pricing


ZERO = Decimal(0)

# Chosen so the raw cost lands off a tick boundary, which
# `_assert_rounding_is_load_bearing` checks.
_QUANTITY = Decimal("13.3333")


# --- driving the thing under test ----------------------------------------
async def _quote(
    session: AsyncSession,
    upstream: Upstream,
    *,
    outcome: int = 0,
    side: str = "buy",
    quantity: Decimal = _QUANTITY,
    access_token: str | None = None,
    transport: httpx.MockTransport | None = None,
):
    """One preview, with `side` converted to `Side` as the route does."""
    return await _preview().quote(
        session,
        upstream.market_id,
        outcome_id=upstream.outcomes[outcome],
        side=_pricing().Side(side),
        quantity=quantity,
        access_token=access_token if access_token is not None else fresh_token(),
        transport=upstream.transport if transport is None else transport,
    )


# --- the expected arithmetic ---------------------------------------------
#
# Recomputed from `core/lmsr.py` and `core/pricing.py` rather than pinned as
# literals; `test_lmsr.py` already pins the engine against a float reference.
def _delta(q: Sequence[Decimal], outcome: int, side: str, quantity: Decimal):
    delta = [ZERO] * len(q)
    delta[outcome] = quantity if side == "buy" else -quantity
    return delta


def _raw_cost(q: Sequence[Decimal], outcome: int, side: str, quantity: Decimal):
    return cost_to_trade(list(q), B, _delta(q, outcome, side, quantity))


def _expected_total(q: Sequence[Decimal], outcome: int, side: str, quantity: Decimal):
    """The signed total: magnitude from the engine, quantized by side, then
    the sign applied, negative on a buy (D-039)."""
    magnitude = _pricing().quantize_cost(
        _raw_cost(q, outcome, side, quantity).copy_abs(), side=_pricing().Side(side)
    )
    return -magnitude if side == "buy" else magnitude


def _assert_rounding_is_load_bearing(
    q: Sequence[Decimal], outcome: int, side: str, quantity: Decimal
) -> None:
    """Guard on the fixture, not the code: rounding assertions are worthless
    on a cost exact at scale 4. If it fires, change `_QUANTITY`."""
    raw = _raw_cost(q, outcome, side, quantity).copy_abs()
    assert raw != raw.quantize(QUANTUM), (
        "this fixture's raw cost is already exact at scale 4, so the "
        "quantization tests below prove nothing; pick another quantity"
    )


# =========================================================================
# What the preview returns
# =========================================================================
async def test_the_previewed_total_is_what_the_same_quantization_would_charge(
    session: AsyncSession,
) -> None:
    """D-014: the preview quantizes with `quantize_cost`, the function the
    trade charges with. The fixture guard makes a wrong rounding miss by a
    tick rather than agree by luck.
    """
    upstream = Upstream()
    await warm(session, upstream)
    _assert_rounding_is_load_bearing(Q, 0, "buy", _QUANTITY)

    quote = await _quote(session, upstream)

    magnitude = _pricing().quantize_cost(
        _raw_cost(Q, 0, "buy", _QUANTITY).copy_abs(), side=_pricing().Side.BUY
    )
    assert quote.total == -magnitude
    assert abs(quote.total) != _raw_cost(Q, 0, "buy", _QUANTITY).copy_abs()


@pytest.mark.parametrize("outcome", [0, 1])
@pytest.mark.parametrize("side", ["buy", "sell"])
async def test_it_works_for_buy_and_sell_on_every_outcome(
    session: AsyncSession, side: str, outcome: int
) -> None:
    """The sixth criterion, over both sides and every outcome."""
    upstream = Upstream()
    await warm(session, upstream)

    quote = await _quote(session, upstream, outcome=outcome, side=side)

    assert quote.total == _expected_total(Q, outcome, side, _QUANTITY)
    assert quote.outcome_id == upstream.outcomes[outcome]
    assert quote.side is _pricing().Side(side)


async def test_the_q_vector_is_ordered_by_position(session: AsyncSession) -> None:
    """`position`, not insertion order: an unordered read is stable enough to
    pass other tests and free to change. Asserted by reversing `q`.
    """
    upstream = Upstream()
    await warm(session, upstream)
    straight = await _quote(session, upstream, outcome=0)

    await set_q(session, upstream, list(reversed(Q)))
    reversed_q = await _quote(session, upstream, outcome=0)

    assert straight.total != reversed_q.total
    assert reversed_q.total == _expected_total(
        list(reversed(Q)), 0, "buy", _QUANTITY
    )


# --- average price --------------------------------------------------------
async def test_average_price_is_positive_on_both_sides(
    session: AsyncSession,
) -> None:
    """A price, not a direction: the sign already lives on `total`."""
    upstream = Upstream()
    await warm(session, upstream)

    bought = await _quote(session, upstream, side="buy")
    sold = await _quote(session, upstream, side="sell")

    assert bought.average_price > ZERO
    assert sold.average_price > ZERO


async def test_average_price_rounds_half_up_because_it_is_derived_not_charged(
    session: AsyncSession,
) -> None:
    """Half up, not directional: the trade charges `total`, never
    `quantity * average_price`, so there is no residue to send anywhere.
    """
    upstream = Upstream()
    await warm(session, upstream)

    quote = await _quote(session, upstream)
    exact = abs(quote.total) / _QUANTITY

    assert quote.average_price == exact.quantize(QUANTUM, rounding=ROUND_HALF_UP)
    assert quote.average_price.as_tuple().exponent == -4


# --- prices ---------------------------------------------------------------
async def test_prices_carry_every_outcome_in_position_order(
    session: AsyncSession,
) -> None:
    """"the market's current prices": every outcome, ordered and carrying
    `position` the way `PriceEvent` does."""
    upstream = Upstream(outcomes=3)
    q = [Decimal("137.5000"), Decimal("42.2500"), Decimal("88.0000")]
    await warm(session, upstream, q)

    quote = await _quote(session, upstream)

    assert [p.position for p in quote.prices] == [0, 1, 2]
    assert [p.outcome_id for p in quote.prices] == upstream.outcomes
    assert [p.price for p in quote.prices] == expected_prices(q)


async def test_post_trade_prices_are_the_prices_the_trade_would_leave(
    session: AsyncSession,
) -> None:
    """"the post-trade price of every outcome": moving one `q_i` moves the
    whole softmax."""
    upstream = Upstream()
    await warm(session, upstream)

    quote = await _quote(session, upstream)

    after = [a + d for a, d in zip(Q, _delta(Q, 0, "buy", _QUANTITY))]
    assert [p.outcome_id for p in quote.post_trade_prices] == upstream.outcomes
    assert [p.price for p in quote.post_trade_prices] == expected_prices(after)


async def test_a_buy_raises_the_traded_outcome_s_post_trade_price(
    session: AsyncSession,
) -> None:
    """The direction, separately from the value: a sign error shared by the
    code and the oracle would pass the equality test above."""
    upstream = Upstream()
    await warm(session, upstream)

    quote = await _quote(session, upstream, outcome=0, side="buy")

    assert quote.post_trade_prices[0].price > quote.prices[0].price
    assert quote.post_trade_prices[1].price < quote.prices[1].price


async def test_post_trade_prices_need_not_sum_to_exactly_one_at_scale_four(
    session: AsyncSession,
) -> None:
    """Quantized prices sum to 1 only within a tolerance, and the engine does
    not normalise them. The loose bound catches a wrong price vector, not the
    rounding mode.
    """
    upstream = Upstream(outcomes=3)
    q = [Decimal("137.5000"), Decimal("42.2500"), Decimal("88.0000")]
    await warm(session, upstream, q)

    quote = await _quote(session, upstream)

    for vector in (quote.prices, quote.post_trade_prices):
        total = sum((p.price for p in vector), ZERO)
        tolerance = QUANTUM * len(vector)
        assert abs(total - Decimal(1)) <= tolerance, f"{total} is not near 1"


# --- the round trip -------------------------------------------------------
async def test_buying_then_selling_the_same_quantity_never_profits(
    session: AsyncSession,
) -> None:
    """[F-3] #43's round-trip property, in quantized credits: the sell is
    priced against the `q` the buy would have left."""
    upstream = Upstream()
    await warm(session, upstream)

    bought = await _quote(session, upstream, side="buy")

    after = [a + d for a, d in zip(Q, _delta(Q, 0, "buy", _QUANTITY))]
    await set_q(session, upstream, after)
    sold = await _quote(session, upstream, side="sell")

    assert sold.total <= abs(bought.total), (
        "selling back what you just bought must never return more credits "
        f"than it cost: paid {abs(bought.total)}, received {sold.total}"
    )


# =========================================================================
# The quote reference
# =========================================================================
async def test_state_version_is_the_book_s_own_counter(
    session: AsyncSession,
) -> None:
    """D-011: the book's own counter, the one the trade's staleness check
    compares."""
    upstream = Upstream()
    book = await warm(session, upstream)

    quote = await _quote(session, upstream)

    assert quote.state_version == book.state_version
    assert isinstance(quote.state_version, int)


async def test_a_preview_after_an_intervening_version_bump_returns_the_newer_number(
    session: AsyncSession,
) -> None:
    """The reference tracks the book rather than being cached. The `UPDATE`
    stands in for a committed trade."""
    upstream = Upstream()
    book = entities().MarketBook
    await warm(session, upstream)

    before = await _quote(session, upstream)

    await session.execute(
        update(book)
        .where(book.market_id == upstream.market_id)
        .values(state_version=book.state_version + 1)
    )
    await session.commit()

    after = await _quote(session, upstream)

    assert after.state_version == before.state_version + 1


# =========================================================================
# The no-shorting rule
# =========================================================================
async def test_a_sell_larger_than_that_outcome_s_q_is_refused(
    session: AsyncSession,
) -> None:
    """The seventh criterion, at 409: against shares outstanding, not the
    caller's holding. Refused rather than clamped to a trade nobody asked for.
    """
    upstream = Upstream()
    await warm(session, upstream)

    with pytest.raises(errors().LedgerError) as raised:
        await _quote(
            session, upstream, outcome=1, side="sell", quantity=Q[1] + QUANTUM
        )

    assert raised.value.code == "insufficient_shares_outstanding"
    assert raised.value.status_code == 409


async def test_a_sell_of_exactly_that_outcome_s_q_is_allowed(
    session: AsyncSession,
) -> None:
    """The boundary, on the permitted side: a `>=` would refuse the last sale."""
    upstream = Upstream()
    await warm(session, upstream)

    quote = await _quote(session, upstream, outcome=1, side="sell", quantity=Q[1])

    assert quote.total == _expected_total(Q, 1, "sell", Q[1])


async def test_a_buy_is_never_refused_for_size(session: AsyncSession) -> None:
    """The rule is about shares outstanding and applies to sells only;
    affordability is the trade's question (D-014)."""
    upstream = Upstream()
    await warm(session, upstream)

    quote = await _quote(session, upstream, side="buy", quantity=Decimal("100000"))

    assert quote.total < ZERO


# =========================================================================
# The reads it makes
# =========================================================================
async def test_the_pricing_read_is_one_statement_joining_the_two_tables(
    session: AsyncSession,
) -> None:
    """D-013, structurally: exactly one statement touches `market_books`, and
    it joins `market_outcomes`, so the warm path is a single read.
    """
    upstream = Upstream()
    await warm(session, upstream)

    with capture_sql() as statements:
        await _quote(session, upstream)

    books = mentioning(statements, "market_books")
    assert len(books) == 1, f"the warm path must read once, sent:\n{statements}"
    assert "market_outcomes" in books[0].lower(), (
        "`q` and `state_version` must come back from one statement (D-013), "
        f"and this one does not join them:\n{books[0]}"
    )


async def test_a_warm_preview_takes_no_locks(session: AsyncSession) -> None:
    """D-012, as corrected by D-036: the pricing read is unlocked, or every
    keystroke in a market queues."""
    upstream = Upstream()
    await warm(session, upstream)

    with capture_sql() as statements:
        await _quote(session, upstream)

    locking = [s for s in statements if "for update" in s.lower()]
    assert locking == [], f"the pricing read must take no locks:\n{locking}"


async def test_a_warm_preview_writes_nothing(session: AsyncSession) -> None:
    """A warm preview writes nothing: the only write a preview may make is the
    first touch."""
    upstream = Upstream()
    await warm(session, upstream)

    with capture_sql() as statements:
        await _quote(session, upstream)

    assert writes(statements) == [], (
        f"a warm preview must not write:\n{writes(statements)}"
    )


async def test_a_preview_changes_nothing_on_a_warm_market(
    session: AsyncSession,
) -> None:
    """The same claim as state: no entries, and the book row unchanged. It
    fails differently from the statement capture."""
    upstream = Upstream()
    await warm(session, upstream)

    before = await book_row(session, upstream)
    snapshot = (
        before.market_id,
        before.liquidity_b,
        before.seed_subsidy,
        before.pool_account_id,
        before.state_version,
        before.state_changed_at,
        before.opened_at,
    )
    entries_before = (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one()

    for _ in range(3):
        await _quote(session, upstream)

    after = await book_row(session, upstream)
    assert (
        after.market_id,
        after.liquidity_b,
        after.seed_subsidy,
        after.pool_account_id,
        after.state_version,
        after.state_changed_at,
        after.opened_at,
    ) == snapshot

    entries_after = (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one()
    assert entries_after == entries_before


# =========================================================================
# The cold path
# =========================================================================
async def test_the_first_preview_on_a_cold_market_opens_the_book(
    session: AsyncSession,
) -> None:
    """The ninth criterion, and D-037: the preview is a market's first toucher."""
    upstream = Upstream()

    quote = await _quote(session, upstream)

    assert quote.market_id == upstream.market_id
    assert upstream.calls == 1

    book = await book_row(session, upstream)
    assert book.liquidity_b == B
    assert book.state_version == 0

    from service import accounts  # noqa: PLC0415

    assert await accounts.balance_of(session, book.pool_account_id) == SUBSIDY


async def test_a_cold_market_prices_from_the_book_it_just_opened(
    session: AsyncSession,
) -> None:
    """"and prices from it": a binary market opened at `q = 0` quotes 0.5
    each."""
    upstream = Upstream()

    quote = await _quote(session, upstream)

    assert [p.price for p in quote.prices] == expected_prices([ZERO, ZERO])
    assert [p.price for p in quote.prices] == [Decimal("0.5000"), Decimal("0.5000")]
    assert quote.total == _expected_total([ZERO, ZERO], 0, "buy", _QUANTITY)


async def test_the_second_preview_makes_no_http_call_at_all(
    session: AsyncSession,
) -> None:
    """"once per market ever": after the first, no call to market_service."""
    upstream = Upstream()

    await _quote(session, upstream)
    assert upstream.calls == 1

    await _quote(session, upstream)
    await _quote(session, upstream)

    assert upstream.calls == 1, "an existing book must not be re-fetched"


async def test_the_cold_path_forwards_the_caller_s_own_token(
    session: AsyncSession,
) -> None:
    """D-018: the caller's own token goes upstream, and none is minted."""
    upstream = Upstream()
    token = fresh_token()

    await _quote(session, upstream, access_token=token)

    assert upstream.tokens == [token]


async def test_the_cold_path_takes_the_handoff_s_locks(
    session: AsyncSession,
) -> None:
    """D-036: the cold path does take the handoff's locks. Do not delete one to
    make "preview takes no locks" literally true."""
    upstream = Upstream()

    with capture_sql() as statements:
        await _quote(session, upstream)

    locking = [s for s in statements if "for update" in s.lower()]
    assert locking, (
        "the first touch funds a pool, and D-015 and ADR 0015 require that "
        "write to be locked; nothing here took a lock"
    )


async def test_a_preview_on_a_warm_closed_market_makes_no_http_call(
    session: AsyncSession,
) -> None:
    """ADR 0017: a preview never asks market_service whether a market is open.
    A closed market is the input that would catch a status check being added.
    """
    upstream = Upstream(status="closed")
    await warm(session, upstream)
    calls_after_the_first_touch = upstream.calls

    quote = await _quote(session, upstream)

    assert upstream.calls == calls_after_the_first_touch, (
        "a preview on a warm market must not reach market_service, whatever "
        "that market's status is"
    )
    assert quote.total < ZERO


# =========================================================================
# Refusals
# =========================================================================
async def test_an_unknown_outcome_id_is_refused_as_unknown_outcome(
    session: AsyncSession,
) -> None:
    """The tenth criterion: 422, not 404, because the market was found and the
    parameter is wrong."""
    upstream = Upstream()
    await warm(session, upstream)

    with pytest.raises(errors().LedgerError) as raised:
        await _preview().quote(
            session,
            upstream.market_id,
            outcome_id=uuid.uuid4(),
            side=_pricing().Side.BUY,
            quantity=_QUANTITY,
            access_token=fresh_token(),
            transport=upstream.transport,
        )

    assert raised.value.code == "unknown_outcome"
    assert raised.value.status_code == 422


async def test_an_unknown_outcome_on_a_cold_market_leaves_the_funded_book_behind(
    session: AsyncSession,
) -> None:
    """Deliberate, not a leak: the book a bad outcome id opens is the one the
    next honest request would have created (D-037).
    """
    upstream = Upstream()

    with pytest.raises(errors().LedgerError) as raised:
        await _preview().quote(
            session,
            upstream.market_id,
            outcome_id=uuid.uuid4(),
            side=_pricing().Side.BUY,
            quantity=_QUANTITY,
            access_token=fresh_token(),
            transport=upstream.transport,
        )
    assert raised.value.code == "unknown_outcome"

    book = await book_row(session, upstream)
    assert book.liquidity_b == B

    from service import accounts  # noqa: PLC0415

    assert await accounts.balance_of(session, book.pool_account_id) == SUBSIDY

    later = await _quote(session, upstream)
    assert later.total < ZERO
    assert upstream.calls == 1, "the surviving book must serve the next request"


async def test_an_unreachable_market_service_leaves_nothing_behind(
    session: AsyncSession,
) -> None:
    """D-030's 503, and nothing written afterwards."""
    upstream = Upstream()

    with pytest.raises(errors().MarketTermsUnavailable):
        await _quote(session, upstream, transport=upstream.dead)

    await session.rollback()

    book = entities().MarketBook
    assert (
        await session.execute(
            select(book).where(book.market_id == upstream.market_id)
        )
    ).scalar_one_or_none() is None
    assert (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one() == 0


async def test_an_unpublished_market_gets_no_quote(session: AsyncSession) -> None:
    """The eleventh criterion, inherited from `books.ensure_open` rather than
    restated."""
    upstream = Upstream(published_at=None)

    with pytest.raises(errors().MarketNotPublished):
        await _quote(session, upstream)


# =========================================================================
# The invariant
# =========================================================================
async def test_the_ledger_still_sums_to_zero_after_a_run_of_previews(
    session: AsyncSession,
) -> None:
    """Three markets opened by preview, each funded once, and the whole ledger
    still sums to zero."""
    for _ in range(3):
        upstream = Upstream()
        await _quote(session, upstream)
        await _quote(session, upstream)

    written = (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one()
    assert written == 6, "three fundings of two legs each"

    total = (
        await session.execute(select(func.coalesce(func.sum(Entry.amount), ZERO)))
    ).scalar_one()
    assert total == ZERO


# =========================================================================
# The two edges of the quantization, both reachable
# =========================================================================
# A saturated outcome, from `core/lmsr.py::cost_to_trade`'s own docstring:
# 100 shares against `q = [1560, *]` at `b = 100` is worth about 0.0000288.
_SATURATED = [Decimal("1560.0000"), Decimal("100.0000")]
_HUNDRED = Decimal("100.0000")


async def test_a_sub_tick_sell_is_refused_rather_than_quoted_at_zero(
    session: AsyncSession,
) -> None:
    """D-041. Proceeds that quantize to zero are refused, not quoted, while the
    same sub-tick buy is charged a tick. Driven through `quote` to show the
    preview calls `quantize_cost`.
    """
    upstream = Upstream()
    await warm(session, upstream, _SATURATED)

    raw = _raw_cost(_SATURATED, 1, "sell", _HUNDRED).copy_abs()
    assert ZERO < raw < QUANTUM, (
        "this fixture is meant to price a real trade at under one tick; "
        f"got {raw}, so the assertions below prove nothing"
    )

    with pytest.raises(errors().ProceedsBelowTick):
        await _quote(session, upstream, outcome=1, side="sell", quantity=_HUNDRED)

    buy = await _quote(session, upstream, outcome=1, side="buy", quantity=_HUNDRED)

    assert buy.total == -QUANTUM


async def test_a_quantity_that_prices_above_the_column_is_refused(
    session: AsyncSession,
) -> None:
    """D-040. A cost `Numeric(18, 4)` cannot hold is refused, not quoted for a
    trade that would then fail to store."""
    upstream = Upstream()
    await warm(session, upstream)

    with pytest.raises(errors().QuantityTooLarge):
        await _quote(
            session, upstream, quantity=Decimal("1000000000000000.0000")
        )


async def test_an_ordinary_large_quantity_is_still_quoted(
    session: AsyncSession,
) -> None:
    """The ceiling is the column's: an absurd but storable cost is priced."""
    upstream = Upstream()
    await warm(session, upstream)

    result = await _quote(session, upstream, quantity=Decimal("10000000000000.0000"))

    assert abs(result.total) <= _pricing().MAX_MAGNITUDE
    assert result.total < ZERO


# =========================================================================
# Precision, zero totals and raw-string sides
# =========================================================================
async def _set_b(session: AsyncSession, upstream: Upstream, b: Decimal) -> None:
    """`b` written directly, as `set_q` writes `q`: `Upstream` serves one `b`."""
    book = entities().MarketBook
    await session.execute(
        update(book).where(book.market_id == upstream.market_id).values(liquidity_b=b)
    )
    await session.commit()


async def test_a_sell_s_magnitude_is_not_rounded_up_by_the_ambient_context(
    session: AsyncSession,
) -> None:
    """`abs()` rounds at the ambient 28 digits; `copy_abs()` does not round.

    The engine answers `-99.99999999999999999999999999993169...` at 50
    digits. At 28 that rounds up to `100.0000...`, and `ROUND_FLOOR` then
    pays the trader a tick more than the proceeds.
    """
    upstream = Upstream()
    q = [Decimal("7000"), Decimal("0")]
    await warm(session, upstream, q)

    raw = cost_to_trade(q, B, [Decimal(-100), ZERO]).copy_abs()
    assert raw < Decimal(100), f"the engine's own answer must be under 100; got {raw}"

    quote = await _quote(
        session, upstream, outcome=0, side="sell", quantity=Decimal("100")
    )

    assert quote.total == Decimal("99.9999")


@pytest.mark.parametrize(
    ("q", "b"),
    [
        ([Decimal("12000"), Decimal("1")], Decimal("100")),
        ([Decimal("1200"), Decimal("1")], Decimal("10")),
    ],
)
async def test_a_buy_priced_at_exactly_zero_is_refused(
    session: AsyncSession, q: list[Decimal], b: Decimal
) -> None:
    """100 real shares for zero credits, refused rather than quoted."""
    upstream = Upstream()
    await warm(session, upstream, q)
    await _set_b(session, upstream, b)

    with pytest.raises(errors().LedgerError) as raised:
        await _quote(session, upstream, outcome=1, side="buy", quantity=Decimal("100"))

    assert raised.value.code == "cost_below_tick"
    assert raised.value.status_code == 422


async def test_a_buy_whose_resulting_q_the_column_cannot_hold_is_refused(
    session: AsyncSession,
) -> None:
    """The cost is one tick and fits; the `q` it leaves is 15 integer digits."""
    upstream = Upstream()
    await warm(session, upstream, [Decimal("99999999999999.9999"), ZERO])

    with pytest.raises(errors().LedgerError) as raised:
        await _quote(session, upstream, outcome=0, quantity=Decimal("0.0001"))

    assert raised.value.code == "quantity_too_large"
    assert raised.value.status_code == 422


async def test_a_quantity_beyond_the_ambient_precision_is_refused_not_raised(
    session: AsyncSession,
) -> None:
    """`1E+25` quantized at 28 digits is `InvalidOperation`, which is a 500."""
    upstream = Upstream()
    await warm(session, upstream)

    with pytest.raises(errors().LedgerError) as raised:
        await _quote(session, upstream, quantity=Decimal("1E+25"))

    assert raised.value.code == "quantity_too_large"


async def test_a_raw_string_buy_is_priced_as_a_buy(session: AsyncSession) -> None:
    """`"buy" is Side.BUY` is False, so an unconverted buy priced as a sell."""
    upstream = Upstream()
    await warm(session, upstream)

    enum = await _quote(session, upstream, side="buy")
    raw = await _preview().quote(
        session,
        upstream.market_id,
        outcome_id=upstream.outcomes[0],
        side="buy",
        quantity=_QUANTITY,
        access_token=fresh_token(),
        transport=upstream.transport,
    )

    assert raw.total == enum.total


# =========================================================================
# The cold path's connection, and damaged books
# =========================================================================
class _Stalled:
    """A market service that records what the caller holds on every call, then
    stalls until `release`."""

    def __init__(
        self, upstream: Upstream, probe: Callable[[], dict[str, object]]
    ) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.seen: list[dict[str, object]] = []
        self._upstream = upstream
        self._probe = probe

    @property
    def transport(self) -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            self._upstream.calls += 1
            self.seen.append(self._probe())
            self.entered.set()
            await self.release.wait()
            return httpx.Response(200, json=self._upstream.body)

        return httpx.MockTransport(handler)


async def _idle_in_transaction() -> int:
    """Backends of this role in this database sitting `idle in transaction`,
    from Postgres's own view."""
    async with get_engine().connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND usename = current_user "
                    "AND state = 'idle in transaction' "
                    "AND pid <> pg_backend_pid()"
                )
            )
        ).scalar_one()


async def test_a_cold_preview_holds_no_connection_while_market_service_is_slow(
    session: AsyncSession,
) -> None:
    """The first touch's HTTP call runs with no transaction open (D-043).

    Checked from the session, the pool and Postgres, with exactly one call.
    Remove the rollback in `books.ensure_open` and this goes red.
    """
    upstream = Upstream()
    pool = get_engine().pool
    stalled = _Stalled(
        upstream,
        lambda: {
            "session in a transaction": session.in_transaction(),
            "pooled connections checked out": pool.checkedout(),
        },
    )

    task = asyncio.create_task(_quote(session, upstream, transport=stalled.transport))
    try:
        async with asyncio.timeout(10):
            await stalled.entered.wait()
        idle = await _idle_in_transaction()
    finally:
        stalled.release.set()
        quote = await task

    clean = {"session in a transaction": False, "pooled connections checked out": 0}
    assert stalled.seen == [clean] * len(stalled.seen)
    assert idle == 0, f"{idle} backend(s) idle in transaction across the call"
    assert upstream.calls == 1
    assert quote.total == _expected_total([ZERO, ZERO], 0, "buy", _QUANTITY)


async def test_a_book_with_no_outcome_rows_is_a_named_error_not_an_index_error(
    session: AsyncSession,
) -> None:
    """A book with no outcome rows reads as no book twice; the re-read is a
    named error, not an `IndexError`."""
    upstream = Upstream()
    await warm(session, upstream)
    await strip_outcomes(session, upstream.market_id)

    with pytest.raises(errors().LedgerError) as raised:
        await _quote(session, upstream)

    assert raised.value.code == "market_book_incomplete"
    assert raised.value.status_code == 500


async def test_every_display_figure_goes_through_one_half_up_helper(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prices, post-trade prices and `average_price` share one rounding helper
    (D-052)."""
    upstream = Upstream()
    await warm(session, upstream)
    marker = Decimal("0.5000")
    monkeypatch.setattr(
        _preview().book_prices, "quantize_price", lambda value: marker
    )
    monkeypatch.setattr(_preview(), "quantize_price", lambda value: marker)

    quote = await _quote(session, upstream)

    assert quote.average_price == marker
    assert {p.price for p in quote.prices} == {marker}
    assert {p.price for p in quote.post_trade_prices} == {marker}


async def test_a_book_left_with_one_outcome_row_is_a_named_error_too(
    session: AsyncSession,
) -> None:
    """A book cut to one outcome row reads as a book, so the guard must run on
    the warm path, not only after the cold one.
    """
    from sqlalchemy import delete  # noqa: PLC0415

    from core.errors import LedgerError  # noqa: PLC0415

    upstream = Upstream()
    await warm(session, upstream)
    outcome = entities().MarketOutcome
    await session.execute(
        delete(outcome).where(
            outcome.market_id == upstream.market_id, outcome.position == 1
        )
    )
    await session.commit()

    with pytest.raises(Exception) as raised:
        await _quote(session, upstream)

    assert isinstance(raised.value, LedgerError), (
        f"a book with one outcome row raised an unmapped "
        f"{type(raised.value).__name__}: {raised.value!r}"
    )
    assert raised.value.code == "market_book_incomplete"
    with pytest.raises(errors().LedgerError) as raised:
        await _quote(session, upstream)

    assert raised.value.code == "market_book_incomplete"
    assert raised.value.status_code == 500


# No `Infinity` here: `numeric(18, 4)` refuses to store one, so it cannot
# be sitting in a book for this path to find. `NaN` it stores happily.
@pytest.mark.parametrize("bad_b", ["0.0000", "-100.0000", "NaN"])
async def test_a_book_whose_b_cannot_price_is_a_named_error_not_a_value_error(
    session: AsyncSession, bad_b: str
) -> None:
    """The read side of the `b` rule: the ingress guard protects new books
    only, and NaN is storable and slips `_require_positive_b`.
    """
    upstream = Upstream()
    await warm(session, upstream)
    await _set_b(session, upstream, Decimal(bad_b))

    with pytest.raises(errors().LedgerError) as raised:
        await _quote(session, upstream)

    assert raised.value.code == "market_book_incomplete"
    assert raised.value.status_code == 500
