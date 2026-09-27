"""The LMSR pricing engine. [F-3] #43

A pure function of `b` and `q`, so tested without a database. Decimal in,
unquantized Decimal out (D-002).

The tolerance is for `_reference_cost`, a deliberately separate float
implementation; the engine itself is exact Decimal. Do not read it as a claim
that the engine is noisy, or "simplify" the engine to floats because of it.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

import pytest

from core.lmsr import cost, cost_to_trade, prices


# The repo's default `b`, so these are the numbers a real market produces.
B = Decimal("100")

# Deterministic, and each test builds its own generator from it: a shared one
# draws different vectors under `-k`, `--lf` or `-n auto` than CI did.
_SEED = 20260920


def _rng(stream: str) -> random.Random:
    """A fresh generator, seeded so the caller draws the same vectors every run
    and a different set from every other caller.

    `stream` is required and named after the test: with a default or integer
    offsets, callers silently drew identical samples. A string seeds
    deterministically across runs and platforms.
    """
    return random.Random(f"{_SEED}:{stream}")


_TOL = Decimal("1e-9")


def _d(values: Sequence[float | int | str]) -> list[Decimal]:
    return [Decimal(str(v)) for v in values]


def _reference_cost(q: Sequence[Decimal], b: Decimal) -> Decimal:
    """`b·ln(Σ e^(q_i/b))` by hand, in float: a second implementation, since
    one computed the engine's way would agree with a wrong formula."""
    scaled = [float(x) / float(b) for x in q]
    top = max(scaled)
    total = math.fsum(math.exp(v - top) for v in scaled)
    return Decimal(str(float(b) * (top + math.log(total))))


def _assert_close(actual: Decimal, expected: Decimal, tol: Decimal = _TOL) -> None:
    scale = max(Decimal(1), abs(expected))
    assert abs(actual - expected) <= tol * scale, f"{actual} != {expected} (tol {tol})"


def _random_q(
    rng: random.Random, outcome_count: int, scale: float = 400.0
) -> list[Decimal]:
    """Shares outstanding. Non-negative: `q_i` is how many shares of outcome
    `i` the market maker has sold, and it cannot go below zero in aggregate."""
    return _d([round(rng.uniform(0.0, scale), 4) for _ in range(outcome_count)])


# --- the cost function --------------------------------------------------------


@pytest.mark.parametrize(
    "q",
    [
        _d([0, 0]),
        _d([10, 0]),
        _d([123.4567, 89.0123]),
        _d([50, 50, 50]),
        _d([300, 12.5, 0, 7]),
    ],
)
def test_cost_is_b_times_the_log_sum_exp_of_q_over_b(q: list[Decimal]) -> None:
    """The acceptance criterion, stated as the formula the issue states:
    `C(q) = b·ln(Σ e^(q_i/b))`."""
    _assert_close(cost(q, B), _reference_cost(q, B))


@pytest.mark.parametrize("outcome_count", [2, 3, 5])
def test_cost_with_nothing_outstanding_is_b_ln_n(outcome_count: int) -> None:
    """`C(0) = b·ln(n)`, which is also the platform's worst-case loss — the
    number `max_platform_loss` shows an administrator at creation time. The
    engine and that display must not disagree about it."""
    _assert_close(
        cost(_d([0] * outcome_count), B),
        Decimal(str(float(B) * math.log(outcome_count))),
    )


@pytest.mark.parametrize("index", [0, 1, 2])
def test_cost_increases_in_every_outcome(index: int) -> None:
    """Monotonicity. Selling a share of any outcome moves the market maker's
    liability up, never down — if it can go down for some `q`, there is a
    sequence of trades that takes money out of the platform for free."""
    rng = _rng(f"cost_increases_in_every_outcome:{index}")
    for _ in range(50):
        q = _random_q(rng, 3)
        more = list(q)
        more[index] += Decimal("1")

        assert cost(more, B) > cost(q, B)


def test_a_share_of_every_outcome_costs_exactly_one_credit() -> None:
    """`C(q + k·1) = C(q) + k`, for any `k` and any `q`.

    What makes the payout solvent: a complete set pays exactly 1 credit
    whichever outcome wins, so it must cost exactly 1.
    """
    rng = _rng("a_share_of_every_outcome_costs_exactly_one_credit")
    for _ in range(50):
        q = _random_q(rng, 3)
        k = Decimal(str(round(rng.uniform(0.1, 25.0), 4)))

        _assert_close(cost_to_trade(q, B, [k] * 3), k)


# --- marginal prices ----------------------------------------------------------


def test_prices_sum_to_one_for_arbitrary_positions() -> None:
    rng = _rng("prices_sum_to_one_for_arbitrary_positions")
    for _ in range(200):
        q = _random_q(rng, rng.choice([2, 3, 4]))

        _assert_close(sum(prices(q, B), Decimal(0)), Decimal(1))


@pytest.mark.parametrize("outcome_count", [2, 3, 4])
def test_prices_with_nothing_outstanding_are_uniform(outcome_count: int) -> None:
    """Before anyone has traded every outcome is equally likely, so each opens
    at `1/n` — the same number `uniform_initial_price` advertises on the create
    form. Two copies of that claim exist by design (ADR 0005 keeps the engine
    out of market_service); they are not allowed to differ."""
    for price in prices(_d([0] * outcome_count), B):
        _assert_close(price, Decimal(1) / Decimal(outcome_count))


@pytest.mark.parametrize("index", [0, 1, 2])
def test_price_is_the_marginal_cost_of_the_next_share(index: int) -> None:
    """Price is the gradient of cost, so a central difference of `C` has to
    land on it. This is the test that catches a price computed from a formula
    that merely looks like a softmax but is not the derivative of the cost
    function actually being charged — the failure where a trader is quoted one
    number and charged another."""
    h = Decimal("0.01")
    rng = _rng(f"price_is_the_marginal_cost_of_the_next_share:{index}")
    for _ in range(20):
        q = _random_q(rng, 3)
        up, down = list(q), list(q)
        up[index] += h
        down[index] -= h

        slope = (cost(up, B) - cost(down, B)) / (2 * h)

        _assert_close(slope, prices(q, B)[index], tol=Decimal("1e-6"))


def test_buying_an_outcome_raises_its_price_and_lowers_the_others() -> None:
    """Demand moves the price up, and because prices sum to one it moves every
    other outcome down. A market where buying YES did not raise YES would be
    free money in one direction."""
    before = prices(_d([100, 100, 100]), B)
    after = prices(_d([150, 100, 100]), B)

    assert after[0] > before[0]
    assert after[1] < before[1]
    assert after[2] < before[2]


@pytest.mark.parametrize(
    "q",
    [_d([0, 0]), _d([500, 0]), _d([0, 500]), _d([900, 20, 3])],
)
def test_every_price_is_strictly_between_zero_and_one(q: list[Decimal]) -> None:
    """No outcome is ever free and none is ever a certainty, at unsaturated
    positions. The next test covers saturation, where only `> 0` stays strict.
    """
    for price in prices(q, B):
        assert Decimal(0) < price < Decimal(1), f"{price} out of range for {q}"


@pytest.mark.parametrize("q", [_d([5000, 0]), _d([0, 5000]), _d([1000000, 0, 0])])
def test_prices_never_leave_the_unit_interval(q: list[Decimal]) -> None:
    """At saturation: strictly above 0, only weakly below 1 (D-006).

    Decimal's exponent range keeps a losing price above zero; its 50-digit
    precision rounds the winner to exactly 1. With no clamp, a price above 1
    means the normalisation broke; do not re-add a clamp to hide it.
    """
    for price in prices(q, B):
        assert Decimal(0) < price <= Decimal(1), f"{price} out of range for {q}"


# --- what a trade costs -------------------------------------------------------


@pytest.mark.parametrize(
    ("q", "delta"),
    [
        (_d([0, 0]), _d([10, 0])),
        (_d([100, 100]), _d([25, 0])),
        (_d([100, 100]), _d([0, 25])),
        (_d([80, 20, 5]), _d([3, 0, 0])),
    ],
)
def test_cost_to_trade_is_the_difference_between_two_costs(
    q: list[Decimal], delta: list[Decimal]
) -> None:
    """`C(q + Δ) − C(q)`. [T-1] #21 requires the preview to be "computed from
    the LMSR cost function, not estimated", and [T-2] #22 charges what the
    preview quoted, so this difference is the money."""
    after = [a + d for a, d in zip(q, delta)]

    _assert_close(cost_to_trade(q, B, delta), cost(after, B) - cost(q, B))


def test_selling_returns_proceeds_as_a_negative_cost() -> None:
    """[T-3] #23: "proceeds computed from LMSR cost function (cost difference,
    sign flipped)". One function, one sign convention — positive is money out
    of the trader's balance, negative is money into it. A separate `proceeds`
    function would be a second place for the sign to be wrong."""
    charged = cost_to_trade(_d([100, 100]), B, _d([20, 0]))
    returned = cost_to_trade(_d([120, 100]), B, _d([-20, 0]))

    assert charged > 0
    assert returned < 0
    _assert_close(returned, -charged)


def test_a_trade_of_nothing_costs_nothing() -> None:
    """Exact, not approximate: a quantity of zero must not produce a dust
    charge out of two logarithms that failed to cancel."""
    assert cost_to_trade(_d([137.5, 42]), B, _d([0, 0])) == Decimal(0)


def test_a_trade_can_cost_both_outcomes_at_once() -> None:
    """A `Δ` with both signs: sell one outcome and buy another in one vector.
    Every other `delta` here is uniform or single-sided.
    """
    q = _d([100, 100, 100])
    swap = _d([20, -20, 0])
    after = [a + d for a, d in zip(q, swap)]

    _assert_close(cost_to_trade(q, B, swap), cost(after, B) - cost(q, B))

    # And the leg that drives a `q_i` down is still just a cost difference.
    _assert_close(
        cost_to_trade(q, B, _d([0, -50, 0])),
        cost(_d([100, 50, 100]), B) - cost(q, B),
    )


def test_a_sub_tick_trade_is_priced_not_rounded() -> None:
    """A real trade can cost less than the ledger can store, and the engine
    hands back the real number anyway.

    Rounding and refusing a sub-tick trade are the caller's
    (`core/pricing.py::quantize_cost`, D-039, D-041); the engine stays exact.
    """
    tick = Decimal("0.0001")

    sub_tick = cost_to_trade(_d([1560, 0]), B, _d([0, 100]))
    assert sub_tick > 0, "the engine must price it, not zero it"
    assert sub_tick < tick
    assert sub_tick.quantize(tick, rounding=ROUND_HALF_UP) == 0

    fully_cancelled = cost_to_trade(_d([326293.3525, 51.9124]), B, _d([0, 249.8866]))
    assert fully_cancelled == 0


def test_average_price_lies_between_the_price_before_and_after() -> None:
    """[T-1] #21 returns "average price per share" beside the post-trade price.
    Cost is convex, so the average paid must sit between the marginal price
    before the trade and the marginal price after it. An average outside that
    band means slippage is being applied twice or not at all."""
    rng = _rng("average_price_lies_between_the_price_before_and_after")
    for _ in range(50):
        q = _random_q(rng, 3)
        size = Decimal(str(round(rng.uniform(1.0, 60.0), 4)))
        delta = [size, Decimal(0), Decimal(0)]
        after = [a + d for a, d in zip(q, delta)]

        average = cost_to_trade(q, B, delta) / size

        assert prices(q, B)[0] <= average + _TOL
        assert average <= prices(after, B)[0] + _TOL


def test_buying_then_selling_the_same_shares_never_yields_a_profit() -> None:
    """Buy `Δ`, sell it straight back, and the trader must not come out ahead.

    Catches what the formula tests do not: an engine that quantized its own
    output could round both legs in the trader's favour and pass them all.
    """
    rng = _rng("buying_then_selling_the_same_shares_never_yields_a_profit")
    for _ in range(100):
        q = _random_q(rng, rng.choice([2, 3]))
        delta = [Decimal(0)] * len(q)
        delta[rng.randrange(len(q))] = Decimal(str(round(rng.uniform(0.5, 90.0), 4)))
        after = [a + d for a, d in zip(q, delta)]

        paid = cost_to_trade(q, B, delta)
        received = -cost_to_trade(after, B, [-d for d in delta])

        assert received <= paid + _TOL, f"round trip profited on q={q}, delta={delta}"


# --- numerical stability ------------------------------------------------------


@pytest.mark.parametrize(
    ("q_top", "b"),
    [
        (Decimal("10000"), Decimal("1")),
        (Decimal("1000000"), Decimal("100")),
        (Decimal("7000"), Decimal("1")),
    ],
)
def test_cost_is_stable_when_q_over_b_reaches_ten_thousand(
    q_top: Decimal, b: Decimal
) -> None:
    """A direct `Σ e^(q_i/b)` overflows above `q/b ≈ 709` (D-004). At these
    ratios the cost is the dominant `q` itself.
    """
    result = cost([q_top, Decimal(0)], b)

    assert result.is_finite()
    _assert_close(result, q_top)


def test_prices_stay_normalised_at_extreme_ratios() -> None:
    """The same overflow, on the price path. A softmax that divides by an
    overflowed denominator gives `nan`, and a `nan` price reaches the frontend
    as a blank quote rather than as an error."""
    result = prices(_d([1000000, 0]), B)

    assert all(p.is_finite() for p in result)
    _assert_close(sum(result, Decimal(0)), Decimal(1))
    _assert_close(result[0], Decimal(1))
    assert result[1] > 0


def test_a_tiny_liquidity_parameter_does_not_blow_up() -> None:
    """`b` is an administrator-supplied `Numeric(18, 4)`, so the smallest legal
    value is 0.0001 and `q/b` is then ten thousand times `q`. Low liquidity is
    a sharp market, not a crash."""
    result = cost(_d([1, 0]), Decimal("0.0001"))

    assert result.is_finite()
    _assert_close(result, Decimal(1))


# --- the boundary contract ----------------------------------------------------


def test_the_engine_returns_decimals_never_floats() -> None:
    """No float in the money path (D-002)."""
    q = _d([10, 20])

    assert isinstance(cost(q, B), Decimal)
    assert isinstance(cost_to_trade(q, B, _d([1, 0])), Decimal)
    assert all(isinstance(p, Decimal) for p in prices(q, B))


def test_the_engine_does_not_quantize_its_answer() -> None:
    """The caller quantizes (D-002). `b·ln(2)` at `b = 100` is
    69.31471805599453, so exactly 69.3147 would mean the engine rounded."""
    result = cost(_d([0, 0]), B)

    assert result != result.quantize(Decimal("0.0001"))


@pytest.mark.parametrize("bad_b", [Decimal(0), Decimal("-1"), Decimal("-0.0001")])
def test_liquidity_must_be_positive(bad_b: Decimal) -> None:
    """`b = 0` divides by zero and a negative `b` inverts the market.
    `ValueError`, not a domain error: a precondition on a pure function."""
    with pytest.raises(ValueError):
        cost(_d([1, 1]), bad_b)
    with pytest.raises(ValueError):
        prices(_d([1, 1]), bad_b)
    # `cost_to_trade` validates `b` itself rather than relying on the `cost`
    # call it makes. A short-circuit for an all-zero `delta` would be an
    # obvious optimisation and would silently drop the check on this path.
    with pytest.raises(ValueError):
        cost_to_trade(_d([1, 1]), bad_b, _d([1, 0]))


def test_a_trade_must_name_every_outcome() -> None:
    """`Δ` is indexed positionally against `q`, so a short vector is not a
    partial trade — it is a caller that has lost track of which outcome is
    which, and it must not be silently zero-filled."""
    with pytest.raises(ValueError):
        cost_to_trade(_d([10, 10, 10]), B, _d([1, 0]))
    with pytest.raises(ValueError):
        cost_to_trade(_d([10, 10]), B, _d([1, 0, 0]))


def test_a_market_with_fewer_than_two_outcomes_is_refused() -> None:
    """An empty vector has no price, and one outcome priced at 1 is not a
    market. market_service and `PriceEvent` refuse it too; the engine must not
    be the copy that lets it through.
    """
    for refused in ([], _d([42])):
        with pytest.raises(ValueError):
            cost(refused, B)
        with pytest.raises(ValueError):
            prices(refused, B)


def test_a_float_q_is_a_caller_error() -> None:
    """"Decimal in" is the contract, and the engine does not quietly coerce:
    `Decimal(0.1)` is not `Decimal("0.1")`. The error type is incidental; the
    test pins that a float is refused.
    """
    with pytest.raises((TypeError, AttributeError)):
        cost([10, 0], 100)
    with pytest.raises((TypeError, AttributeError)):
        cost([10.0, 0.0], B)
