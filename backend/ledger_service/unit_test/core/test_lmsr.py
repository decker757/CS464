"""The LMSR pricing engine. [F-3] #43

`C(q) = b·ln(Σ e^(q_i/b))`, and the marginal prices that are its gradient.
The engine is a pure function of `b` and `q` — no session, no clock, no
configuration — so it is tested here without a database, in `unit_test/core/`
where the fast suite lives.

The module under test is `ledger_service/core/lmsr.py`. ADR 0005 puts the
engine "wherever `q` lives", and `q` is ledger state; ADR 0012 anticipated it
landing in `backend/shared/` instead, and it does not, because that package's
bar is that *every* caller needs identical behaviour and a divergence between
two copies would be a bug. There is one caller. The trading composite owns no
schema and holds no cross-schema grant, so it cannot read `q` and therefore
cannot evaluate this function — it calls the ledger instead. One caller fails
the bar, so this is `core/`, not `shared/`.

The numeric contract is Decimal in, **unquantized** Decimal out, and the
caller quantizes to `Numeric(18, 4)` at the ledger write. Quantizing here
would round a round trip into a profit, which is the one thing an automated
market maker may never do.

Why the assertions are approximate, given that the engine itself is not. It
computes in `Decimal` throughout — `Decimal.exp()` and `Decimal.ln()` at 50
significant digits, no float anywhere on any path — so its answers are exactly
reproducible and an `==` would be a legitimate assertion against a value
pinned by hand. The tolerance is not for the engine. It is for
`_reference_cost` below, which is deliberately written in `float` so that the
comparison is against a second implementation rather than against a restating
of the first; 1e-9 is what a double can honestly support. Do not read a
tolerance here as a claim that the engine is noisy, and do not "simplify" it
to floats on the strength of one.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

import pytest

from core.lmsr import cost, cost_to_trade, prices


# A market's `b`. The repo's configured default, so the numbers in these tests
# are the numbers a real market would produce.
B = Decimal("100")

# Deterministic. A property test that picks fresh vectors on every run fails on
# somebody else's machine and passes on the author's, which is worse than not
# having one.
#
# Every test builds its own generator from this seed rather than sharing one
# module-level instance, because a shared one is only deterministic for a whole
# ordered run: under `-k`, `--lf` or `-n auto` a test starts at a different
# position in the stream and exercises different vectors than CI did. That is
# the same failure the seed is here to prevent, arriving by a different door —
# and it arrives at the worst moment, when somebody is re-running one red test.
_SEED = 20260920


def _rng(stream: str) -> random.Random:
    """A fresh generator, seeded so the caller draws the same vectors every run
    and a different set from every other caller.

    `stream` names the draw, and two properties follow. The first took two
    passes to get right.

    **Different callers must draw different markets.** The module-level
    generator this replaced got that by accident — one ordered stream hands
    each reader a disjoint slice — and lost reproducibility, which is why it
    went. Seeding on `_SEED + index` restored reproducibility and kept the
    accident only *within* one parametrize: every bare call and every
    `index == 0` case rebuilt the same generator and drew the identical 50
    markets, so six call sites across four theorems were checking all four
    against one sample. Every test passed, and between them they covered
    rather less than six times fifty vectors.

    **`stream` is required.** A default is what let five of those six collide
    without anybody choosing to, and it is invisible at the call site — the
    collision could only be found by reading every caller at once. A new test
    now has to name its stream, and naming it after the test is what makes a
    collision something you can see.

    A string rather than an int, because `random.Random` seeds from one
    deterministically (SHA-512 over the encoded value, stable across runs and
    platforms), and because arithmetic on stream ids is how adjacent integers
    collided here in the first place.
    """
    return random.Random(f"{_SEED}:{stream}")


_TOL = Decimal("1e-9")


def _d(values: Sequence[float | int | str]) -> list[Decimal]:
    return [Decimal(str(v)) for v in values]


def _reference_cost(q: Sequence[Decimal], b: Decimal) -> Decimal:
    """`b·ln(Σ e^(q_i/b))`, written out by hand in log-sum-exp form.

    Deliberately a second implementation rather than a call into the engine:
    a test that computes the expected value the way the code does agrees with
    the code about a wrong formula.
    """
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

    This is the invariant that makes the payout rule solvent: a complete set
    of shares pays exactly 1 credit at resolution whichever outcome wins, so
    it must cost exactly 1 credit to acquire. It is also exact in real
    arithmetic — the `b·ln` cancels — so the tolerance here is float noise
    only.
    """
    rng = _rng("a_share_of_every_outcome_costs_exactly_one_credit")
    for _ in range(50):
        q = _random_q(rng, 3)
        k = Decimal(str(round(rng.uniform(0.1, 25.0), 4)))

        _assert_close(cost_to_trade(q, B, [k] * 3), k)


# --- marginal prices ----------------------------------------------------------


@pytest.mark.parametrize(
    "q",
    [
        _d([0, 0]),
        _d([250, 10]),
        _d([0, 0, 0, 0]),
        _d([99.9999, 100.0001, 3]),
    ],
)
def test_prices_sum_to_one(q: list[Decimal]) -> None:
    """The property that makes a price a probability. It is also what the
    frontend renders: `docs/api/realtime-service.md` sends every outcome's
    price in one frame precisely so a client never shows a set that does not
    add up."""
    _assert_close(sum(prices(q, B), Decimal(0)), Decimal(1))


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
    """No outcome is ever free and none is ever a certainty. A price of exactly
    0 is a share that pays 1 for nothing.

    At `q/b = 5` the losing price is about 0.0067 and the winning one is
    nowhere near 1, so both bounds are strict here. The next test covers the
    saturated positions, where only the lower one still is.
    """
    for price in prices(q, B):
        assert Decimal(0) < price < Decimal(1), f"{price} out of range for {q}"


@pytest.mark.parametrize("q", [_d([5000, 0]), _d([0, 5000]), _d([1000000, 0, 0])])
def test_prices_never_leave_the_unit_interval(q: list[Decimal]) -> None:
    """The bound that survives saturation, and it survives asymmetrically:
    strictly above 0, but only weakly below 1. Both halves are facts about
    `Decimal`, and they are facts about different limits of it.

    **Never exactly 0, because the exponent range is enormous.** A float
    underflows `e^-10000` to 0.0 and would price the losing side at a flat
    zero. `Decimal` reaches down to about 1e-999999, so `q = [1000000, 0, 0]`
    at `b = 100` prices the losers near 1e-4343 — negligible, and not zero.
    That is worth asserting strictly: a price of exactly 0 is a share that pays
    out 1 credit for nothing.

    **Sometimes exactly 1, because the precision is only 50 digits.** The
    winning price there is `1 − 2e-4343`, and no context holding 50 significant
    digits can represent that as anything but 1. Raising `_PRECISION` does not
    help — it would need 4,343 of them. So `< 1` is unsatisfiable at this
    spread and a test demanding it would be asking the implementer to fake a
    number, which is what the weaker bound is for.

    The upper bound is load-bearing in a way it was not when this file was
    written: commit aead51a removed an explicit clamp, so nothing pins these
    numbers except softmax normalisation — every weight is divided by a total
    that includes it, and the largest weight is exactly `exp(0) == 1`. If a
    price ever exceeds 1, the normalisation is what broke; re-adding the clamp
    would hide that rather than fix it.
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
    """A `Δ` with both signs in it: sell one outcome and buy another in a
    single trade. [T-2] #22 makes one transactional write per trade, so a
    position swap arrives here as one vector rather than as two calls, and
    `q_i` can be driven down as well as up.

    Nothing in the formula distinguishes this case — which is the point of
    pinning it. Every other `delta` in this file is uniform or single-sided, so
    without this test the first caller to swap a position would be the one
    finding out, on the trade path, with money attached.
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

    A saturated outcome is worth almost nothing, so buying it is almost free —
    not as a defect but as the correct LMSR price. 100 shares of the losing
    side at `q = [1560, 0]` cost 0.0000288, which `Numeric(18, 4)` cannot hold;
    quantized `ROUND_HALF_UP` at the write it becomes 0.0000, and a real share
    transfer is charged nothing. Widen the spread and the two costs cancel
    outright and the engine returns an exact zero.

    This test pins the engine's half of that, which is to stay exact and stay
    out of it. Rounding up to a tick here would put a rounding into the round
    trip; refusing the trade here would refuse it in a pure function that has
    no request in front of it. The decision — minimum tick, round toward the
    house, or refuse — is [T-2] #22's, and this test exists so that whoever
    makes it sees the numbers rather than inheriting a zero by accident.
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
    """The round trip. Buy `Δ`, sell `Δ` back immediately, and the trader must
    not come out ahead — otherwise the loop is a money printer and the
    platform account funds it.

    In exact arithmetic the two legs are equal and the round trip is free; the
    tolerance below admits float noise and nothing else. Note what this catches
    that the formula tests above do not: an engine that quantized its own
    output to four decimal places could round both legs in the trader's favour
    and still satisfy every one of them.
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
    """`math.exp(710)` raises OverflowError, so a direct transcription of
    `Σ e^(q_i/b)` dies somewhere above `q/b ≈ 709`. The engine must subtract
    the maximum first — log-sum-exp — which makes the answer
    `max(q) + b·ln(Σ e^((q_i − max q)/b))` and every exponent negative.

    At these ratios the second term is indistinguishable from zero, so the cost
    is the dominant `q` itself: a market where one outcome is a near-certainty
    has a liability equal to the shares of it outstanding.
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
    """Invariant 5: no float in the money path — and here that is literal
    rather than a statement about the boundary. The engine takes its logarithm
    with `Decimal.ln()` and its exponential with `Decimal.exp()`, so there is
    no double anywhere inside it to promote on the way out. Passing one in is
    a caller error rather than a supported conversion; see
    `test_a_float_q_is_a_caller_error`."""
    q = _d([10, 20])

    assert isinstance(cost(q, B), Decimal)
    assert isinstance(cost_to_trade(q, B, _d([1, 0])), Decimal)
    assert all(isinstance(p, Decimal) for p in prices(q, B))


def test_the_engine_does_not_quantize_its_answer() -> None:
    """The caller quantizes, at the ledger write, `ROUND_HALF_UP`. If the
    engine rounded to four places first, every consumer would compound two
    roundings and the round trip above would become a rounding profit.
    `b·ln(2)` at `b = 100` is 69.31471805599453, so an answer of 69.3147
    exactly is the sign that this happened."""
    result = cost(_d([0, 0]), B)

    assert result != result.quantize(Decimal("0.0001"))


@pytest.mark.parametrize("bad_b", [Decimal(0), Decimal("-1"), Decimal("-0.0001")])
def test_liquidity_must_be_positive(bad_b: Decimal) -> None:
    """`b = 0` is a division by zero and a negative `b` inverts the market, so
    that buying lowers the price. `ValueError` rather than one of this
    service's own errors because these are preconditions on a pure function,
    not domain errors: no request has been refused, a caller has passed
    arithmetic that has no answer. Turning one into an HTTP status is the
    trade path's job, where there is a request to refuse."""
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
    """`ln(0)` is not a price, and a single outcome is not a market.

    An empty vector reaches here only from a caller that built one wrongly, and
    the honest answer is a ValueError rather than a `math domain error`
    escaping from inside the engine. A one-outcome vector is the more
    interesting refusal: the formula answers it quite happily, pricing the lone
    outcome at `1/1 = 1`, and a share that costs 1 credit and pays 1 credit is
    a market with nothing to be right or wrong about.

    `market_service/core/opening_prices.py` and `realtime_service`'s
    `PriceEvent` both already refuse it, at creation and on the wire. The
    engine is the copy those two defer to, so it must not be the one copy that
    lets it through — a `PriceEvent` built from a one-outcome market would fail
    the relay's own `min_length=2` and be logged and dropped, which refuses it
    in the place least able to say why.
    """
    for refused in ([], _d([42])):
        with pytest.raises(ValueError):
            cost(refused, B)
        with pytest.raises(ValueError):
            prices(refused, B)


def test_a_float_q_is_a_caller_error() -> None:
    """"Decimal in" is the contract, and the engine does not quietly coerce.

    `cost([10, 0], 100)` is the most natural first call anyone makes and it is
    wrong: `q_i / b` becomes float division, and the failure surfaces from
    inside `_log_sum_exp` as an `AttributeError` on a float, which reads like a
    bug in the engine rather than a cast the caller forgot. This test does not
    endorse that error — it pins the behaviour so the trading composite's
    author finds it here, in a named test, rather than at a trade.

    Coercing instead would be worse: `Decimal(0.1)` is not `Decimal("0.1")`,
    and a money path that silently accepts binary floating point is how a
    balance ends up off by a fraction of a tick with nothing to point at.
    """
    with pytest.raises((TypeError, AttributeError)):
        cost([10, 0], 100)
    with pytest.raises((TypeError, AttributeError)):
        cost([10.0, 0.0], B)
