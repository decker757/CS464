"""Rounding a cost to what the ledger can store. [T-1] #21

`core/pricing.py` holds two functions and one enum. `quantize_cost` takes an
**unsigned magnitude** and the side of the trade, and returns that magnitude at
`Numeric(18, 4)`'s scale of 4. The caller applies the sign: a buy's total is
negative on the wire because credits leave the trader, and a sell's is
positive, but neither of those is this function's business.

`refuse_sub_tick_proceeds` judges what `quantize_cost` returned: a sell that
quantized to nothing is refused rather than quoted, because taking real shares
for zero credits is the surprise [T-1] #21 exists to prevent (D-041). It sits
beside the rounding rather than in `service/preview.py` because [T-2] #22 has
to make the same refusal on the write path.

**Why a magnitude rather than the engine's signed answer.** `cost_to_trade`
returns positive for a buy and negative for a sell, and the acceptance
criterion is "buy cost rounds ceiling, sell proceeds round floor — the residue
accrues to the pool, never to the trader". Hand a sell's signed output to
`ROUND_FLOOR` and `-12.34567` becomes `-12.3457`: a *larger* magnitude, so the
trader is paid more and the residue runs the wrong way. The two rounding modes
only mean what the criterion says they mean on a non-negative input, so a
negative one is refused rather than interpreted.

**The tests below assert the invariant, not the mode.** A buy's quantized cost
is never less than the raw cost and a sell's proceeds are never more, on every
value, and the gap is under one tick. That is the property [T-2] #22 depends
on and it survives a reimplementation; `ROUND_CEILING` and `ROUND_FLOOR` are
how it is achieved today. Two probes are still pinned exactly, because an
invariant test alone passes under `ROUND_HALF_UP` on most values and this whole
function exists to not be `ROUND_HALF_UP`.

Pure. No session, no clock, no configuration — so this file lives in
`unit_test/core/` and runs under `.venv/bin/pytest unit_test/core unit_test/model`
with no database, the way D-031 insists that line stays true.
"""

from __future__ import annotations

from decimal import Decimal

import pytest


def _pricing():
    """Imported inside each test rather than at module scope.

    `core/pricing.py` does not exist yet, and a top-level import would be one
    collection error taking the whole file down as a single red line. Reached
    through here, every test fails on its own, named after the criterion it
    holds (D-007). Same convention as `test_market_books.py`.

    `Side` is imported from this module too. If it ends up defined in a sibling
    under `core/`, re-export it here: one import point for the pair is the
    contract these tests hold, because `quantize_cost`'s signature names the
    enum and a caller should not need two imports to make one call.
    """
    from core import pricing  # noqa: PLC0415

    return pricing


def _errors():
    """`core/errors.py`: `ProceedsBelowTick`, which does not exist yet either.

    Reached lazily for the same reason as `_pricing`. `core/errors.py` holds no
    framework imports and no session, so naming a domain error from here does
    not cost this file its place in the no-database run.
    """
    from core import errors  # noqa: PLC0415

    return errors


# The tick `Numeric(18, 4)` stores. Spelled from the scale rather than as the
# literal 0.0001, so widening the column moves this with it.
_SCALE = 4
_QUANTUM = Decimal(1).scaleb(-_SCALE)

# Values chosen so that ROUND_HALF_UP gives a *different* answer from the one
# required. A friendly value passes under any rule and proves nothing.
#
#   12.34561 -> half-up 12.3456, ceiling 12.3457   (a buy must round up)
#   12.34567 -> half-up 12.3457, floor   12.3456   (a sell must round down)
#
# So each probe below is red under the obvious wrong implementation, which is
# the only reason to state an exact expected value at all.
_BUY_PROBE = Decimal("12.34561")
_BUY_EXPECTED = Decimal("12.3457")

_SELL_PROBE = Decimal("12.34567")
_SELL_EXPECTED = Decimal("12.3456")

# A spread of magnitudes for the invariant tests: exact at scale 4, a residue
# just above a tick boundary, one just below, a sub-tick value the engine
# genuinely produces on a saturated outcome (see `cost_to_trade`'s docstring),
# and a large one where the significant digits are all on the left.
_MAGNITUDES = [
    Decimal("0"),
    Decimal("0.0000288"),
    Decimal("0.00005"),
    Decimal("1.0000"),
    Decimal("12.34561"),
    Decimal("12.34565"),
    Decimal("12.34567"),
    Decimal("999.99999"),
    Decimal("12345678901234.56785"),
]


def _sides():
    side = _pricing().Side
    return side.BUY, side.SELL


# --- the two probes -------------------------------------------------------
def test_a_buy_s_residue_goes_to_the_pool() -> None:
    """A buy is charged the next whole tick up, not the nearest one.

    `12.34561` is `12.3456` under `ROUND_HALF_UP` and `12.3457` here. The
    hundredth of a cent between them is the residue, and it belongs to the
    market's pool: LMSR pays out up to `b*ln(n)` more than it collects, so a
    rounding rule that leaked toward the trader would compound against a pool
    that is already the side expected to lose money.
    """
    buy, _ = _sides()

    assert _pricing().quantize_cost(_BUY_PROBE, side=buy) == _BUY_EXPECTED


def test_a_sell_s_residue_goes_to_the_pool() -> None:
    """A sell is paid the whole tick below, not the nearest one.

    `12.34567` is `12.3457` under `ROUND_HALF_UP` and `12.3456` here. Same
    residue, same destination, opposite direction — which is the whole reason
    the side is a parameter rather than one rounding mode for both.
    """
    _, sell = _sides()

    assert _pricing().quantize_cost(_SELL_PROBE, side=sell) == _SELL_EXPECTED


# --- the invariant --------------------------------------------------------
@pytest.mark.parametrize("magnitude", _MAGNITUDES)
def test_the_residue_always_favours_the_pool_on_both_sides(
    magnitude: Decimal,
) -> None:
    """The property the two probes are instances of, over a spread of values.

    A buy is never charged less than the true cost and a sell is never paid
    more than the true proceeds, and neither is off by a whole tick — that last
    clause is what stops a "safe" implementation from rounding a buy up to the
    next credit and calling it conservative.

    This is the assertion to keep if the rounding modes are ever revisited.
    `ROUND_CEILING` and `ROUND_FLOOR` are an implementation of it, not the
    contract.
    """
    buy, sell = _sides()
    quantize = _pricing().quantize_cost

    charged = quantize(magnitude, side=buy)
    paid = quantize(magnitude, side=sell)

    assert charged >= magnitude, "a buy must never be charged less than it costs"
    assert paid <= magnitude, "a sell must never be paid more than it earns"
    assert charged - magnitude < _QUANTUM
    assert magnitude - paid < _QUANTUM


@pytest.mark.parametrize("magnitude", _MAGNITUDES)
def test_a_buy_and_a_sell_of_one_magnitude_cannot_round_trip_a_profit(
    magnitude: Decimal,
) -> None:
    """The sharpest form of the invariant, at this layer.

    Buy something and sell it back at an unchanged `q` and the two magnitudes
    the engine returns are identical — `test_lmsr.py` pins that. What must not
    happen is the quantization pulling them apart in the trader's favour, which
    is a round trip that mints credits out of a rounding rule. `charged` is
    therefore always at least `paid`, whatever the value.
    """
    buy, sell = _sides()
    quantize = _pricing().quantize_cost

    assert quantize(magnitude, side=buy) >= quantize(magnitude, side=sell)


@pytest.mark.parametrize("magnitude", _MAGNITUDES)
def test_the_result_is_always_at_the_ledger_s_scale(magnitude: Decimal) -> None:
    """Scale 4, exactly, and not merely a value that happens to fit.

    `Decimal("12.3457")` and `Decimal("12.34570000")` compare equal and are not
    the same thing on the wire: `str()` of the second carries four digits the
    column does not store. Every money field in this ticket is serialised with
    `str()`, so the exponent is part of the contract rather than an internal
    detail.
    """
    buy, sell = _sides()

    for side in (buy, sell):
        result = _pricing().quantize_cost(magnitude, side=side)
        assert result == result.quantize(_QUANTUM)
        assert result.as_tuple().exponent == -_SCALE


def test_a_value_already_at_scale_four_is_unchanged_on_both_sides() -> None:
    """Nothing is nudged. `ROUND_CEILING` on an exact value must not add a tick.

    Worth its own test because it is the case every real trade in whole credits
    hits, and an off-by-one-tick implementation would still pass both probes
    above.
    """
    buy, sell = _sides()
    exact = Decimal("41.2500")

    assert _pricing().quantize_cost(exact, side=buy) == exact
    assert _pricing().quantize_cost(exact, side=sell) == exact


def test_a_trade_of_nothing_costs_nothing_on_both_sides() -> None:
    """Zero stays zero, including on the side that rounds up.

    `cost_to_trade` returns exactly `Decimal(0)` for an empty delta, and a
    ceiling rule that turned that into one tick would charge a trader for a
    trade that moved nothing.
    """
    buy, sell = _sides()

    assert _pricing().quantize_cost(Decimal(0), side=buy) == Decimal(0)
    assert _pricing().quantize_cost(Decimal(0), side=sell) == Decimal(0)


# --- the sub-tick sell ----------------------------------------------------
#
# `cost_to_trade`'s own docstring example, and the reason this refusal is not
# theoretical: 100 shares of the second outcome against `q = [1560, 100]` at
# `b = 100` are worth 0.0000288 — a real trade, in a saturated outcome, under
# one tick. The magnitudes below come out of the engine rather than being
# pinned as literals, so a fixture that stopped being sub-tick fails its own
# guard instead of quietly asserting nothing.
_SATURATED = [Decimal("1560.0000"), Decimal("100.0000")]
_SATURATED_B = Decimal("100.0000")
_SATURATED_QUANTITY = Decimal("100.0000")


def _saturated_magnitude(side: str) -> Decimal:
    """What the engine says 100 shares of the saturated outcome are worth.

    `core/lmsr.py` is pure and imports nothing but the standard library, so
    calling it from this file keeps `unit_test/core` runnable with no database.
    Guarded rather than trusted: if this stops being a sub-tick magnitude the
    tests below prove nothing, and that is a property of the fixture.
    """
    from core.lmsr import cost_to_trade  # noqa: PLC0415

    signed = _SATURATED_QUANTITY if side == "buy" else -_SATURATED_QUANTITY
    raw = abs(cost_to_trade(_SATURATED, _SATURATED_B, [Decimal(0), signed]))
    assert Decimal(0) < raw < _QUANTUM, (
        f"this fixture is meant to price a real trade at under one tick on a "
        f"{side}; got {raw}, so the assertion it feeds proves nothing"
    )
    return raw


def test_a_sell_whose_proceeds_quantize_to_zero_is_refused() -> None:
    """D-041. Zero proceeds for real shares is refused, not quoted.

    The floor itself is correct — D-039 requires it and the residue has to go
    to the pool — but on a sub-tick magnitude the residue *is* the whole trade,
    so the quantized answer is `0.0000`: a quote that takes shares for nothing.
    Both alternatives break something already decided. Quoting the zero is the
    surprise #21 exists to prevent. Paying a minimum tick pays the trader more
    than the shares are worth, which is the residue running toward the trader,
    the one outcome the criterion rules out.

    So refusing is the only answer left, and it is the answer D-040 already
    gives at the other edge: a magnitude the column cannot honestly represent
    is refused rather than quoted, in either direction.
    """
    _, sell = _sides()
    pricing = _pricing()

    paid = pricing.quantize_cost(_saturated_magnitude("sell"), side=sell)
    assert paid == Decimal(0), "the floor is what makes this refusal necessary"

    with pytest.raises(_errors().ProceedsBelowTick):
        pricing.refuse_sub_tick_proceeds(paid, side=sell)


def test_a_sub_tick_buy_still_charges_one_tick() -> None:
    """The buy side needs no refusal and must not get one.

    `ROUND_CEILING` already charges the whole tick on a sub-tick buy, which is
    the house's favour and consistent with D-039: the trader pays slightly more
    than the shares are worth, which is the direction the pool is allowed to
    win in. An implementation that refused a sub-tick *trade* rather than
    sub-tick *proceeds* would fail here, and it would refuse exactly the buys
    that are already priced correctly.
    """
    buy, _ = _sides()
    pricing = _pricing()

    charged = pricing.quantize_cost(_saturated_magnitude("buy"), side=buy)

    assert charged == _QUANTUM
    pricing.refuse_sub_tick_proceeds(charged, side=buy)


def test_the_refusal_is_keyed_on_the_side_and_fires_only_on_a_sell() -> None:
    """A zero magnitude is refused on a sell and returned on a buy.

    Stated on the bare value rather than through the engine, because this is
    the whole condition: `side is Side.SELL` and a quantized magnitude of zero.
    A guard that read the magnitude alone would refuse a buy of nothing, which
    `test_a_trade_of_nothing_costs_nothing_on_both_sides` says costs nothing
    and raises nothing — the two rules have to coexist.
    """
    buy, sell = _sides()
    pricing = _pricing()

    with pytest.raises(_errors().ProceedsBelowTick):
        pricing.refuse_sub_tick_proceeds(Decimal("0.0000"), side=sell)

    pricing.refuse_sub_tick_proceeds(Decimal("0.0000"), side=buy)


def test_proceeds_of_exactly_one_tick_are_not_refused() -> None:
    """The boundary, because the obvious off-by-one is a `<=`.

    One tick is the smallest amount the ledger can pay and it is an honest
    number: the trader is paid something for their shares. A guard written as
    `magnitude <= QUANTUM` would refuse it and would pass every other test in
    this section.
    """
    _, sell = _sides()
    pricing = _pricing()

    pricing.refuse_sub_tick_proceeds(_QUANTUM, side=sell)


def test_the_refusal_carries_its_own_code_and_status() -> None:
    """422 `proceeds_below_tick`, decided here rather than at the route.

    The status lives on the error class and `controller/errors.py` maps
    whatever it is told, so this is where 422 is pinned;
    `test_preview_routes.py` asserts only that it reaches the wire.

    422 rather than 409: it pairs with `QuantityTooLarge` as the two edges of
    one quantization, and the trader's correction is a larger quantity, which
    is typing. D-041 records that 409 was considered and why it lost, because
    the argument is close — unlike `QuantityTooLarge`, this refusal does depend
    on the book's `q`.
    """
    errors = _errors()

    assert errors.ProceedsBelowTick.status_code == 422
    assert errors.ProceedsBelowTick.code == "proceeds_below_tick"
    assert issubclass(errors.ProceedsBelowTick, errors.LedgerError)


# --- the refusal ----------------------------------------------------------
@pytest.mark.parametrize("magnitude", [Decimal("-0.0001"), Decimal("-12.34567")])
def test_a_negative_magnitude_is_refused(magnitude: Decimal) -> None:
    """It raises rather than inferring what the caller meant.

    A negative arriving here means somebody passed `cost_to_trade`'s signed
    answer straight through. Interpreted rather than refused, that call rounds a
    sell's proceeds *up* in magnitude and pays the trader the residue — the one
    outcome the criterion rules out, arriving silently, in the fourth decimal
    place, where no balance check would ever notice it.
    """
    buy, sell = _sides()

    for side in (buy, sell):
        with pytest.raises(ValueError):
            _pricing().quantize_cost(magnitude, side=side)


# --- the enum -------------------------------------------------------------
def test_side_carries_the_two_wire_values() -> None:
    """`Side` is what the route parses into and what `service/preview.py` takes.

    The values are the query string's, because the response echoes `side` back
    and a client should get out what it put in. Parsed once at the controller
    and passed down as the enum: a bare string in the service signature would
    be a second validation, in a second place, free to drift from the first.
    """
    buy, sell = _sides()

    assert buy.value == "buy"
    assert sell.value == "sell"
    assert buy is not sell


# --- the column this exists to fit ----------------------------------------
def test_the_scale_and_precision_match_the_column() -> None:
    """`core/pricing.py` restates `Numeric(18, 4)`; this is why it may.

    The constants are duplicated rather than imported, because
    `model/entities.py` imports `core.database` and a `core` module importing
    `model` would point the dependency back up a layer and close a cycle. The
    duplication is only safe while something fails when the two disagree, and
    this is that something — so widening the column moves `core/pricing.py`
    with it rather than leaving `MAX_MAGNITUDE` quietly describing the old one.

    `model.entities` is imported inside the test, the same convention the rest
    of this file uses, so the module under test stays reachable without it.
    """
    from model.entities import AMOUNT_PRECISION, AMOUNT_SCALE  # noqa: PLC0415

    pricing = _pricing()

    assert pricing._SCALE == AMOUNT_SCALE
    assert pricing._PRECISION == AMOUNT_PRECISION


def test_max_magnitude_is_the_largest_value_the_column_holds() -> None:
    """14 integer digits and 4 fractional ones, and nothing above it.

    Spelled out as a literal here rather than recomputed from the constants,
    because a test that repeats the implementation's arithmetic passes on a
    wrong formula as happily as on a right one.
    """
    pricing = _pricing()

    assert pricing.MAX_MAGNITUDE == Decimal("99999999999999.9999")

    # One tick more needs a 15th integer digit, which the column cannot store.
    over = pricing.MAX_MAGNITUDE + Decimal("0.0001")
    digits = len(over.as_tuple().digits)
    assert digits > 18


# =========================================================================
# An outcome's price, as the wire carries it — review of #110
# =========================================================================
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (Decimal("0.62345"), Decimal("0.6235")),  # the half tick goes up
        (Decimal("0.62344999"), Decimal("0.6234")),  # just under it does not
        (Decimal("0.37655"), Decimal("0.3766")),  # up on both outcomes: no pool to favour
        (Decimal("1"), Decimal("1.0000")),  # scale 4 even on a whole number
        (Decimal("0"), Decimal("0.0000")),
    ],
)
def test_a_price_rounds_half_up_at_scale_four(raw: Decimal, expected: Decimal) -> None:
    """One rounding rule for every price this service puts on a wire.

    The preview's `prices`, the snapshot's `prices` and [T-2] #22's published
    `PriceEvent.prices` all quantize an outcome's LMSR price, and both docs
    pages promise a client the same string from each of them for the same
    state. That was held by two copies of the same one-line function in
    `service/preview.py` and `service/snapshot.py`, and #22 would have made a
    third. One function makes it true by construction.

    `ROUND_HALF_UP`, not `quantize_cost`'s directional rounding: a price is
    display, nobody is charged it, so there is no side for the residue to
    favour. Asserted on the string too, because a trailing zero is part of
    what the client renders.
    """
    quantized = _pricing().quantize_price(raw)

    assert quantized == expected
    assert str(quantized) == str(expected)
