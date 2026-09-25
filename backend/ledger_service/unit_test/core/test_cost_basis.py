"""Releasing cost basis on a sell. [T-3] #23

`core/pricing.py::release_basis(basis, held, quantity) -> (released,
remaining)`, the pure rule "A sell releases cost basis at average cost: the
remaining basis is rounded half-up and the released basis is the difference"
decides. No session, no clock, no database — these run without Postgres.

The remaining basis is `basis × (held − quantity) / held`, at the pricing
engine's precision, rounded `ROUND_HALF_UP` to scale 4. The released basis is
the old basis minus that. A full exit is an explicit branch that leaves
exactly `0.0000`.

**Every input is chosen so the rounding mode changes the answer.** The issue's
worked example is the proof: half-up leaves `24.3503`, a floor would leave
`24.3502`. `test_the_rounding_mode_is_observable_in_these_inputs` asserts that
of every other case here, so an edit to a rounder number fails with a
sentence rather than quietly turning the rest of this file into tautologies.
"""

from __future__ import annotations

from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, localcontext

import pytest


def _release_basis():
    """Lazy, so a missing function fails each test on its own criterion
    rather than taking the file down as one collection error."""
    from core import pricing  # noqa: PLC0415

    return pricing.release_basis


def _engine_context():
    from core.lmsr import _engine_context  # noqa: PLC0415

    return _engine_context()


QUANTUM = Decimal("0.0001")

# (basis, held, quantity), each one leaving a remainder where half-up and
# floor disagree. The first is the issue's worked example; the rest are
# awkward on purpose.
AWKWARD = [
    (Decimal("29.8428"), Decimal("54.3333"), Decimal("10.0000")),
    (Decimal("24.3503"), Decimal("44.3333"), Decimal("7.7777")),
    (Decimal("22.0281"), Decimal("41.0000"), Decimal("10.0000")),
    (Decimal("15.6964"), Decimal("29.7777"), Decimal("13.3333")),
]


def _exact_remaining(basis: Decimal, held: Decimal, quantity: Decimal) -> Decimal:
    with _engine_context():
        return basis * (held - quantity) / held


# =========================================================================
# The constants
# =========================================================================
def test_the_rounding_mode_is_observable_in_these_inputs() -> None:
    """A test of the inputs, not of the code. If any of them leaves a
    remainder exact at scale 4, half-up and floor agree and the tests below
    stop distinguishing the two."""
    for basis, held, quantity in AWKWARD:
        exact = _exact_remaining(basis, held, quantity)
        assert exact.quantize(QUANTUM, rounding=ROUND_HALF_UP) != exact.quantize(
            QUANTUM, rounding=ROUND_FLOOR
        ), f"{basis}/{held} selling {quantity} leaves {exact}, exact at scale 4"


# =========================================================================
# The worked examples, exactly as the issue states them
# =========================================================================
def test_the_worked_example_leaves_24_3503_half_up_not_24_3502() -> None:
    released, remaining = _release_basis()(
        Decimal("29.8428"), Decimal("54.3333"), Decimal("10.0000")
    )

    assert remaining == Decimal("24.3503")
    assert remaining != Decimal("24.3502"), "the remaining basis was floored"
    assert released == Decimal("5.4925")


def test_three_sells_of_one_release_3_3333_3_3333_3_3334_and_leave_zero() -> None:
    """`10.0000` over `3.0000`, sold a share at a time. Rounding the average
    price instead and multiplying back would release `9.9999` and strand the
    last tick — the rejected alternative in "A sell releases cost basis at
    average cost"."""
    release = _release_basis()
    basis, held = Decimal("10.0000"), Decimal("3.0000")
    released_each = []
    for _ in range(3):
        released, basis = release(basis, held, Decimal("1.0000"))
        released_each.append(released)
        held -= Decimal("1.0000")

    assert released_each == [
        Decimal("3.3333"),
        Decimal("3.3333"),
        Decimal("3.3334"),
    ]
    assert basis == Decimal("0.0000")
    assert sum(released_each) == Decimal("10.0000")


# =========================================================================
# The rule's properties
# =========================================================================
@pytest.mark.parametrize(("basis", "held", "quantity"), AWKWARD)
def test_released_plus_remaining_is_the_old_basis_on_every_sell(
    basis: Decimal, held: Decimal, quantity: Decimal
) -> None:
    released, remaining = _release_basis()(basis, held, quantity)

    assert released + remaining == basis
    assert remaining == _exact_remaining(basis, held, quantity).quantize(
        QUANTUM, rounding=ROUND_HALF_UP
    )


def test_a_position_sold_down_in_pieces_releases_exactly_its_basis() -> None:
    """The worked example's position, sold in three pieces whose sizes do
    not divide it evenly. Drift on an early piece is absorbed by a later
    one."""
    release = _release_basis()
    basis, held = Decimal("29.8428"), Decimal("54.3333")
    total_released = Decimal("0.0000")
    for piece in (Decimal("10.0000"), Decimal("7.7777"), Decimal("36.5556")):
        released, basis = release(basis, held, piece)
        total_released += released
        held -= piece

    assert held == Decimal("0.0000")
    assert basis == Decimal("0.0000")
    assert total_released == Decimal("29.8428")


def test_a_full_exit_releases_the_whole_basis_and_leaves_zero() -> None:
    released, remaining = _release_basis()(
        Decimal("29.8428"), Decimal("54.3333"), Decimal("54.3333")
    )

    assert released == Decimal("29.8428")
    assert remaining == Decimal("0.0000")
    assert remaining.as_tuple().exponent == -4


def test_selling_all_but_a_tick_leaves_dust_at_an_average_of_one() -> None:
    """The Notes of "A sell releases cost basis at average cost": `0.0001`
    shares at `0.0001` basis. Correct arithmetic, and half-up is what makes
    it — a floor would leave `0.0000` basis on a live share."""
    released, remaining = _release_basis()(
        Decimal("29.8428"), Decimal("54.3333"), Decimal("54.3332")
    )

    assert remaining == Decimal("0.0001")
    assert released == Decimal("29.8427")


def test_the_results_are_at_scale_4() -> None:
    released, remaining = _release_basis()(
        Decimal("29.8428"), Decimal("54.3333"), Decimal("10.0000")
    )

    assert released.as_tuple().exponent == -4
    assert remaining.as_tuple().exponent == -4


def test_the_product_is_computed_at_engine_precision() -> None:
    """`basis × remaining_quantity` can reach 36 significant digits, and a
    28-digit context would round the product before the quantize ran — the
    hazard "An absolute value on money is `copy_abs()`, never `abs()`"
    describes, arriving through a multiplication.

    Constructed, not found: `held` is `2**40` and the sell is half of it, so
    the exact remainder is `basis / 2` — `9561728394506.17285`, a tie that
    half-up takes to `…1729`. At 28 digits the 30-digit product loses its
    last two digits downward, and the quotient comes back as
    `…17284999999999`, which half-up takes to `…1728`. The call is made under
    an ambient 28-digit context, because the rule has to pin its own
    arithmetic the way `core/lmsr.py` does rather than inherit the caller's.
    """
    basis = Decimal("19123456789012.3457")
    held = Decimal("1099511627776.0000")
    quantity = Decimal("549755813888.0000")

    exact = _exact_remaining(basis, held, quantity).quantize(
        QUANTUM, rounding=ROUND_HALF_UP
    )
    with localcontext() as ambient:
        ambient.prec = 28
        coarse = (basis * (held - quantity) / held).quantize(
            QUANTUM, rounding=ROUND_HALF_UP
        )
    assert exact != coarse, (
        "these inputs no longer separate engine precision from 28 digits, so "
        "this test asserts nothing"
    )

    with localcontext() as hostile:
        hostile.prec = 28
        released, remaining = _release_basis()(basis, held, quantity)

    assert remaining == exact
    assert released == basis - exact


# =========================================================================
# The precondition
# =========================================================================
@pytest.mark.parametrize(
    "quantity",
    [Decimal("0.0000"), Decimal("-0.0001"), Decimal("54.3334")],
    ids=["zero", "negative", "more-than-held"],
)
def test_a_quantity_outside_zero_to_held_is_a_value_error(quantity: Decimal) -> None:
    """`0 < quantity <= held`, or `ValueError`. The holding check refuses a
    sell over `held` long before this is reached; a caller that gets here
    with one has skipped it, and a negative remaining basis is not a thing to
    return quietly."""
    with pytest.raises(ValueError):
        _release_basis()(Decimal("29.8428"), Decimal("54.3333"), quantity)
