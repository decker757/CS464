"""The price-history replay. [BE][2.4] #63

DECISIONS.md, "Price history is replayed from the trades on the pool's feed,
and nothing stores it": the first point is the book's opening, then one point
per trade, each priced from the `q` every trade so far has built.

The expected prices were worked out with float `math.exp`, not with
`core/lmsr.py`, so a wrong engine cannot agree with itself here. Hence the
tolerance: it is the float's, not a claim that the replay is approximate.
Raw prices only; `outcome_id` and `position` are joined on in PR 5's service.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import ModuleType

import pytest

from core.pricing import Side, quantize_price

B = Decimal("100")
_TOLERANCE = Decimal("0.000001")
_OPENED_AT = datetime(2026, 10, 8, 2, 15, 9, 441000, tzinfo=UTC)


@pytest.fixture
def replay() -> ModuleType:
    """`core.price_history`, imported per test so each test reports its own
    failure while the module does not exist."""
    from core import price_history  # noqa: PLC0415

    return price_history


def _trade(
    replay: ModuleType,
    state_version: int,
    *,
    position: int,
    side: Side,
    quantity: str,
) -> object:
    """A trade one minute per `state_version` after the opening, so every
    point's time is distinct from the opening's and from every other's."""
    return replay.ReplayedTrade(
        position=position,
        side=side,
        quantity=Decimal(quantity),
        state_version=state_version,
        occurred_at=_OPENED_AT + timedelta(minutes=state_version),
    )


def _assert_prices_near(actual: Sequence[Decimal], expected: Sequence[str]) -> None:
    assert len(actual) == len(expected)
    for got, want in zip(actual, expected):
        assert abs(got - Decimal(want)) < _TOLERANCE, (actual, expected)


def test_the_first_point_is_the_books_opening_even_when_trades_follow(replay):
    trades = [
        _trade(replay, 1, position=0, side=Side.BUY, quantity="50"),
        _trade(replay, 2, position=0, side=Side.SELL, quantity="20"),
    ]

    points = replay.price_history_of(
        trades=trades, outcome_count=2, b=B, opened_at=_OPENED_AT
    )

    assert len(points) == len(trades) + 1
    opening = points[0]
    assert opening.state_version == 0
    assert opening.occurred_at == _OPENED_AT
    _assert_prices_near(opening.prices, ["0.5", "0.5"])


def test_a_market_with_no_trades_replays_to_its_opening_alone(replay):
    points = replay.price_history_of(
        trades=[], outcome_count=3, b=B, opened_at=_OPENED_AT
    )

    assert len(points) == 1
    assert points[0].state_version == 0
    assert points[0].occurred_at == _OPENED_AT
    third = "0.333333"
    _assert_prices_near(points[0].prices, [third, third, third])


def test_the_docs_example_replays_to_the_docs_own_strings(replay):
    """docs/api/ledger-service.md's first example: a buy of 50, then a sell
    of 20, at b = 100."""
    buy = _trade(replay, 1, position=0, side=Side.BUY, quantity="50")
    sell = _trade(replay, 2, position=0, side=Side.SELL, quantity="20")

    points = replay.price_history_of(
        trades=[buy, sell], outcome_count=2, b=B, opened_at=_OPENED_AT
    )

    assert [point.state_version for point in points] == [0, 1, 2]
    assert [point.occurred_at for point in points] == [
        _OPENED_AT,
        buy.occurred_at,
        sell.occurred_at,
    ]

    _assert_prices_near(points[0].prices, ["0.5", "0.5"])
    _assert_prices_near(points[1].prices, ["0.622459", "0.377541"])
    _assert_prices_near(points[2].prices, ["0.574443", "0.425557"])

    # The wire's strings, from the quantizer the price frame uses.
    wire = [[str(quantize_price(price)) for price in point.prices] for point in points]
    assert wire == [
        ["0.5000", "0.5000"],
        ["0.6225", "0.3775"],
        ["0.5744", "0.4256"],
    ]


def test_a_sell_walks_the_price_back_along_the_same_path(replay):
    trades = [
        _trade(replay, 1, position=0, side=Side.BUY, quantity="50"),
        _trade(replay, 2, position=0, side=Side.BUY, quantity="50"),
        _trade(replay, 3, position=0, side=Side.SELL, quantity="50"),
        _trade(replay, 4, position=0, side=Side.SELL, quantity="50"),
    ]

    points = replay.price_history_of(
        trades=trades, outcome_count=2, b=B, opened_at=_OPENED_AT
    )

    _assert_prices_near(points[1].prices, ["0.622459", "0.377541"])
    _assert_prices_near(points[2].prices, ["0.731059", "0.268941"])
    assert points[3].prices == points[1].prices
    assert points[4].prices == points[0].prices


def test_three_outcomes_all_move_when_one_is_traded(replay):
    """No assertion that the prices sum to 1: quantized, these sum to 0.9999,
    and the raw ones only to within the engine's precision."""
    trades = [
        _trade(replay, 1, position=1, side=Side.BUY, quantity="100"),
        _trade(replay, 2, position=2, side=Side.BUY, quantity="50"),
    ]

    points = replay.price_history_of(
        trades=trades, outcome_count=3, b=B, opened_at=_OPENED_AT
    )

    _assert_prices_near(points[1].prices, ["0.211942", "0.576117", "0.211942"])
    _assert_prices_near(points[2].prices, ["0.186324", "0.506480", "0.307196"])


@pytest.mark.parametrize(
    "state_versions",
    [pytest.param([1, 3], id="gap"), pytest.param([2, 1], id="reordered")],
)
def test_a_trade_out_of_state_version_order_is_refused(replay, state_versions):
    """A point labelled N must carry the prices after N trades, and only a
    trade bumps `state_version`, so a healthy feed runs 1, 2, 3, ..."""
    trades = [
        _trade(replay, version, position=0, side=Side.BUY, quantity="10")
        for version in state_versions
    ]

    with pytest.raises(ValueError):
        replay.price_history_of(
            trades=trades, outcome_count=2, b=B, opened_at=_OPENED_AT
        )
