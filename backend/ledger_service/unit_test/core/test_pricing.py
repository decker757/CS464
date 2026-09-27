"""Rounding a cost to what the ledger can store. [T-1] #21

`quantize_cost` takes an unsigned magnitude and the side, rounds so the residue
goes to the pool (D-039), and refuses a result of `0.0000` (D-041). The caller
applies the sign.

The tests assert the invariant, not the mode: a buy is never charged less and
a sell never paid more, by under a tick. Two probes are also pinned exactly,
because the invariant alone passes under `ROUND_HALF_UP` on most values.
Pure, so no database.
"""

from __future__ import annotations

from decimal import Decimal

import pytest


def _pricing():
    """Imported inside each test, so a missing name fails one test rather than
    collection (D-007)."""
    from core import pricing  # noqa: PLC0415

    return pricing


def _errors():
    """`core/errors.py`, reached lazily for the same reason as `_pricing`."""
    from core import errors  # noqa: PLC0415

    return errors


# The tick `Numeric(18, 4)` stores. Spelled from the scale rather than as the
# literal 0.0001, so widening the column moves this with it.
_SCALE = 4
_QUANTUM = Decimal(1).scaleb(-_SCALE)

# Values where ROUND_HALF_UP gives a different answer from the one required,
# so each probe is red under the obvious wrong implementation:
#   12.34561 -> half-up 12.3456, ceiling 12.3457   (a buy must round up)
#   12.34567 -> half-up 12.3457, floor   12.3456   (a sell must round down)
_BUY_PROBE = Decimal("12.34561")
_BUY_EXPECTED = Decimal("12.3457")

_SELL_PROBE = Decimal("12.34567")
_SELL_EXPECTED = Decimal("12.3456")

# A spread of magnitudes for the invariant tests: exact at scale 4, a residue
# just above a tick boundary, one just below, a sub-tick value the engine
# genuinely produces on a saturated outcome (see `cost_to_trade`'s docstring),
# and a large one where the significant digits are all on the left.
_MAGNITUDES = [
    Decimal("0.00015"),
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
    """A buy is charged the next whole tick up, not the nearest one. D-039."""
    buy, _ = _sides()

    assert _pricing().quantize_cost(_BUY_PROBE, side=buy) == _BUY_EXPECTED


def test_a_sell_s_residue_goes_to_the_pool() -> None:
    """A sell is paid the whole tick below, not the nearest one. D-039."""
    _, sell = _sides()

    assert _pricing().quantize_cost(_SELL_PROBE, side=sell) == _SELL_EXPECTED


# --- the invariant --------------------------------------------------------
@pytest.mark.parametrize("magnitude", _MAGNITUDES)
def test_the_residue_always_favours_the_pool_on_both_sides(
    magnitude: Decimal,
) -> None:
    """The property the two probes are instances of: never in the trader's
    favour, and never off by a whole tick. Keep this if the modes change.
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
    """Quantization must not pull a buy and a sell of one magnitude apart in
    the trader's favour: a round trip minting credits from a rounding rule.
    """
    buy, sell = _sides()
    quantize = _pricing().quantize_cost

    assert quantize(magnitude, side=buy) >= quantize(magnitude, side=sell)


@pytest.mark.parametrize("magnitude", _MAGNITUDES)
def test_the_result_is_always_at_the_ledger_s_scale(magnitude: Decimal) -> None:
    """Scale 4 exactly: `Decimal("12.3457")` and `Decimal("12.34570000")`
    compare equal but serialise differently, and money goes out via `str()`.
    """
    buy, sell = _sides()

    for side in (buy, sell):
        result = _pricing().quantize_cost(magnitude, side=side)
        assert result == result.quantize(_QUANTUM)
        assert result.as_tuple().exponent == -_SCALE


def test_a_value_already_at_scale_four_is_unchanged_on_both_sides() -> None:
    """`ROUND_CEILING` on an exact value must not add a tick; an off-by-one
    implementation would pass both probes."""
    buy, sell = _sides()
    exact = Decimal("41.2500")

    assert _pricing().quantize_cost(exact, side=buy) == exact
    assert _pricing().quantize_cost(exact, side=sell) == exact


def test_a_magnitude_of_exactly_zero_is_refused_on_both_sides() -> None:
    """A zero magnitude is refused on both sides, under the side's own code.
    D-041."""
    buy, sell = _sides()

    with pytest.raises(_errors().CostBelowTick):
        _pricing().quantize_cost(Decimal(0), side=buy)
    with pytest.raises(_errors().ProceedsBelowTick):
        _pricing().quantize_cost(Decimal(0), side=sell)


# --- the sub-tick sell ----------------------------------------------------
#
# Real trades in a saturated outcome, both under one tick. The magnitudes come
# from the engine, and a fixture that stopped being sub-tick fails its guard.
_SATURATED = [Decimal("1560.0000"), Decimal("100.0000")]
_SATURATED_B = Decimal("100.0000")
_SATURATED_QUANTITY = Decimal("100.0000")


def _saturated_magnitude(side: str) -> Decimal:
    """What the engine says 100 shares of the saturated outcome are worth,
    guarded to be under one tick."""
    from core.lmsr import cost_to_trade  # noqa: PLC0415

    signed = _SATURATED_QUANTITY if side == "buy" else -_SATURATED_QUANTITY
    raw = abs(cost_to_trade(_SATURATED, _SATURATED_B, [Decimal(0), signed]))
    assert Decimal(0) < raw < _QUANTUM, (
        f"this fixture is meant to price a real trade at under one tick on a "
        f"{side}; got {raw}, so the assertion it feeds proves nothing"
    )
    return raw


def test_a_sell_whose_proceeds_quantize_to_zero_is_refused() -> None:
    """D-041. Zero proceeds for real shares is refused, not quoted."""
    _, sell = _sides()
    pricing = _pricing()

    with pytest.raises(_errors().ProceedsBelowTick):
        pricing.quantize_cost(_saturated_magnitude("sell"), side=sell)


def test_a_sub_tick_buy_still_charges_one_tick() -> None:
    """A sub-tick buy is charged one tick, not refused: the refusal is for a
    zero result, not a sub-tick trade."""
    buy, _ = _sides()
    pricing = _pricing()

    charged = pricing.quantize_cost(_saturated_magnitude("buy"), side=buy)

    assert charged == _QUANTUM


def test_the_worked_example_s_value_is_charged_a_tick_and_paid_nothing() -> None:
    """The worked value `0.0000288`, as a buy and as a sell."""
    buy, sell = _sides()
    pricing = _pricing()
    worked = Decimal("0.0000288")

    assert pricing.quantize_cost(worked, side=buy) == Decimal("0.0001")
    with pytest.raises(_errors().ProceedsBelowTick):
        pricing.quantize_cost(worked, side=sell)


def test_proceeds_of_exactly_one_tick_are_not_refused() -> None:
    """The boundary, because the obvious off-by-one is a `<=`."""
    _, sell = _sides()
    pricing = _pricing()

    assert pricing.quantize_cost(_QUANTUM, side=sell) == _QUANTUM


def test_the_refusal_carries_its_own_code_and_status() -> None:
    """422 `proceeds_below_tick`, pinned here because the status lives on the
    error class. D-041 has why 422 and not 409.
    """
    errors = _errors()

    assert errors.ProceedsBelowTick.status_code == 422
    assert errors.ProceedsBelowTick.code == "proceeds_below_tick"
    assert issubclass(errors.ProceedsBelowTick, errors.LedgerError)


# --- a real quantity priced at exactly nothing ----------------------------
#
# Past roughly 110·b of skew the engine returns exactly zero, and
# `ROUND_CEILING` of zero is zero, so a buy reaches `0.0000` too.
@pytest.mark.parametrize(
    ("q", "b"),
    [
        ([Decimal("12000"), Decimal("1")], Decimal("100")),
        ([Decimal("1200"), Decimal("1")], Decimal("10")),
    ],
)
def test_a_buy_the_engine_prices_at_exactly_zero_is_refused(
    q: list[Decimal], b: Decimal
) -> None:
    """100 real shares for zero credits, refused on the buy side too."""
    from core.lmsr import cost_to_trade  # noqa: PLC0415

    buy, _ = _sides()
    raw = cost_to_trade(q, b, [Decimal(0), Decimal(100)])
    assert raw == 0, f"this book is meant to price a buy at exactly zero; got {raw}"

    with pytest.raises(_errors().LedgerError) as raised:
        _pricing().quantize_cost(raw.copy_abs(), side=buy)

    assert raised.value.code == "cost_below_tick"
    assert raised.value.status_code == 422


def test_the_sell_on_the_same_book_is_refused_from_the_same_call() -> None:
    """One site raises both codes, so no caller can quantize and forget."""
    from core.lmsr import cost_to_trade  # noqa: PLC0415

    _, sell = _sides()
    raw = cost_to_trade(
        [Decimal("12000"), Decimal("1")], Decimal("100"), [Decimal(0), Decimal(-100)]
    )
    assert Decimal(0) <= raw.copy_abs() < _QUANTUM

    with pytest.raises(_errors().LedgerError) as raised:
        _pricing().quantize_cost(raw.copy_abs(), side=sell)

    assert raised.value.code == "proceeds_below_tick"


# --- a raw string for a side ----------------------------------------------
#
# `"buy" is Side.BUY` is False, and a caller other than FastAPI may not convert.
def test_a_raw_string_side_rounds_exactly_as_the_enum_does() -> None:
    """An unconverted `"buy"` must round up, not fall into the floor branch."""
    magnitude = Decimal("1.00001")

    assert _pricing().quantize_cost(magnitude, side="buy") == Decimal("1.0001")
    assert _pricing().quantize_cost(magnitude, side="sell") == Decimal("1.0000")


# --- the refusal ----------------------------------------------------------
@pytest.mark.parametrize("magnitude", [Decimal("-0.0001"), Decimal("-12.34567")])
def test_a_negative_magnitude_is_refused(magnitude: Decimal) -> None:
    """A negative means `cost_to_trade`'s signed answer was passed straight in;
    floored, a sell's proceeds would grow and pay the trader the residue.
    """
    buy, sell = _sides()

    for side in (buy, sell):
        with pytest.raises(ValueError):
            _pricing().quantize_cost(magnitude, side=side)


# --- the column this exists to fit ----------------------------------------
def test_the_scale_and_precision_match_the_column() -> None:
    """`core/pricing.py` restates `Numeric(18, 4)` rather than import `model`
    (which would point core back up a layer); this is what makes that safe.
    """
    from model.entities import AMOUNT_PRECISION, AMOUNT_SCALE  # noqa: PLC0415

    pricing = _pricing()

    assert pricing._SCALE == AMOUNT_SCALE
    assert pricing._PRECISION == AMOUNT_PRECISION


def test_max_magnitude_is_the_largest_value_the_column_holds() -> None:
    """14 integer digits and 4 fractional ones. A literal, not recomputed, so a
    wrong formula cannot pass."""
    pricing = _pricing()

    assert pricing.MAX_MAGNITUDE == Decimal("99999999999999.9999")

    # One tick more needs a 15th integer digit, which the column cannot store.
    over = pricing.MAX_MAGNITUDE + Decimal("0.0001")
    digits = len(over.as_tuple().digits)
    assert digits > 18


# =========================================================================
# An outcome's price, as the wire carries it
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
    """One rounding rule, half up, for every price this service puts on a wire
    (D-052). Asserted on the string too: a trailing zero is rendered.
    """
    quantized = _pricing().quantize_price(raw)

    assert quantized == expected
    assert str(quantized) == str(expected)


# --- D-044: a cost exactly on a tick, and the engine's last digit -----------
#
# Characterisation of accepted behaviour, so a change to the engine's precision
# or this rounding names the decision rather than moving a number silently.
def test_a_cost_exactly_on_a_tick_can_be_charged_one_tick_over() -> None:
    """`q = [a, a + d]`, buying `2d` of outcome 0, costs exactly `d`.

    The engine lands one unit of its 50th digit above it, and
    `ROUND_CEILING` turns that dust into a whole tick. Bounded at one tick,
    toward the pool.
    """
    from core.lmsr import cost_to_trade  # noqa: PLC0415

    raw = cost_to_trade(
        [Decimal(1000), Decimal(1100)], Decimal(300), [Decimal(200), Decimal(0)]
    )

    assert raw != Decimal(100), "the engine hit the tick exactly; the entry is moot"
    assert _pricing().quantize_cost(raw.copy_abs(), side=_pricing().Side.BUY) == Decimal(
        "100.0001"
    )


def test_a_sell_just_under_a_tick_can_be_paid_the_whole_tick() -> None:
    """The mirror: true proceeds a hair under `0.0001`, returned as exactly it.

    Selling `0.0001` of an outcome priced `1 - 8e-53` is worth that much
    less than a tick, which is past the engine's 50 digits, so it answers
    `-0.000100` and the sell is paid rather than refused.
    """
    from core.lmsr import cost_to_trade  # noqa: PLC0415

    raw = cost_to_trade(
        [Decimal(12000), Decimal(0)], Decimal(100), [Decimal("-0.0001"), Decimal(0)]
    )

    assert raw == Decimal("-0.0001")
    assert _pricing().quantize_cost(raw.copy_abs(), side=_pricing().Side.SELL) == _QUANTUM
