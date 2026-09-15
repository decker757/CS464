"""The q = 0 pricing arithmetic. [1.2] #2.

Pure functions, so these are plain assertions about numbers. The boundary this
file also defends: `core/opening_prices.py` must stay the degenerate case and
must not
grow into a second copy of the engine that ADR 0005 puts on the trading side.
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from core import opening_prices


# --- max platform loss ----------------------------------------------------
@pytest.mark.parametrize(
    ("b", "outcomes", "expected"),
    [
        (Decimal("100"), 2, 100 * math.log(2)),
        (Decimal("100"), 3, 100 * math.log(3)),
        (Decimal("50"), 2, 50 * math.log(2)),
        (Decimal("0.5"), 2, 0.5 * math.log(2)),
    ],
)
def test_the_worst_case_is_b_times_the_log_of_the_outcome_count(
    b: Decimal, outcomes: int, expected: float
) -> None:
    assert opening_prices.max_platform_loss(b, outcomes) == pytest.approx(expected)


def test_a_binary_market_at_b_of_one_hundred_loses_at_most_about_seventy() -> None:
    """Pinned to a concrete number, because this is the one an admin reads.

    If a refactor changes it, the form starts advertising a different worst
    case and the test should say so rather than tracking the code.
    """
    assert opening_prices.max_platform_loss(Decimal("100"), 2) == pytest.approx(69.31471805599453)


@pytest.mark.parametrize("b", [Decimal("0"), Decimal("-1")])
def test_a_non_positive_liquidity_has_no_worst_case(b: Decimal) -> None:
    assert opening_prices.max_platform_loss(b, 2) is None


def test_an_unset_liquidity_has_no_worst_case() -> None:
    assert opening_prices.max_platform_loss(None, 2) is None


# --- initial prices -------------------------------------------------------
@pytest.mark.parametrize("count", [2, 3, 5, 10])
def test_every_outcome_opens_at_one_over_n(count: int) -> None:
    assert opening_prices.uniform_initial_price(count) == pytest.approx(1 / count)


@pytest.mark.parametrize("count", [2, 3, 7])
def test_the_opening_prices_sum_to_one(count: int) -> None:
    """Prices are probabilities, so they have to add up.

    This is why `uniform_initial_price` does not round: three outcomes at
    0.3333 sum to 0.9999 and read as a bug in the maths rather than a display
    choice. Rounding is the browser's job.
    """
    price = opening_prices.uniform_initial_price(count)

    assert price is not None
    assert price * count == pytest.approx(1.0)


# --- too small to price ---------------------------------------------------
@pytest.mark.parametrize("count", [0, 1])
def test_neither_number_exists_below_two_outcomes(count: int) -> None:
    """The ordinary state of a form the admin just opened.

    One outcome is arithmetically fine — ln(1) is 0 and 1/1 is 1 — and says
    that the platform cannot lose and the result is certain. Both are useless,
    so they are reported as absent.
    """
    assert opening_prices.max_platform_loss(Decimal("100"), count) is None
    assert opening_prices.uniform_initial_price(count) is None


# --- boundary -------------------------------------------------------------
def test_this_module_does_not_grow_a_cost_function() -> None:
    """ADR 0005: the engine lives with `q`, on the trading side, not here.

    This service never learns the share quantity vector, so anything here that
    accepted one would be a second implementation of a formula that has to
    agree with the real one to the last floating-point step. Two definitions of
    a price mean a trader quoted one number and charged another.
    """
    defined_here = {
        name
        for name, value in vars(opening_prices).items()
        if not name.startswith("_")
        and callable(value)
        and getattr(value, "__module__", None) == opening_prices.__name__
    }

    assert defined_here == {"max_platform_loss", "uniform_initial_price"}
