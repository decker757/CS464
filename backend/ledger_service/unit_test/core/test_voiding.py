"""The void's refunds, without a database. [BE] #221, ADR 0025

The lot replay, its guard, and the cap of one more seed. Every expected
figure is a literal: ADR 0025's worked numbers, or worked out at precision 80
from `C(q) = b·ln(Σ e^(q_i/b))` by a script that shares no code with
`core/lmsr.py`, and named where it is used. Trade totals are fed as the
ledger would have recorded them, so the replay prices only the lots it has
to.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from core.pricing import Side
from core.voiding import (
    VoidTrade,
    cut_of,
    lot_replay_of,
    refuse_if_replay_disagrees,
)

HOLDER = uuid.UUID("00000000-0000-0000-0000-00000000000a")
OTHER = uuid.UUID("00000000-0000-0000-0000-00000000000b")
THIRD = uuid.UUID("00000000-0000-0000-0000-00000000000c")


def _trades(*rows: tuple) -> list[VoidTrade]:
    """`(user, position, side, quantity, total)` rows, versioned 1, 2, 3, ...
    in the order given."""
    return [
        VoidTrade(
            user_id=user,
            position=position,
            side=Side(side),
            quantity=Decimal(quantity),
            total=Decimal(total),
            state_version=version,
        )
        for version, (user, position, side, quantity, total) in enumerate(rows, start=1)
    ]


def _owed(trades: list[VoidTrade], b: str) -> dict:
    return lot_replay_of(trades, outcome_count=2, b=Decimal(b)).owed


# =========================================================================
# The cheat is closed: a sale undoes the newest lot
# =========================================================================
@pytest.mark.parametrize(
    ("b", "bought", "charged", "sold", "paid", "refund"),
    [
        # ADR 0025's "buy 4, sell 2": average cost would refund 1.6626 and
        # oldest first 1.8912.
        pytest.param("1", "4", "3.3251", "2", "1.8912", "1.4337", id="buy-4-sell-2"),
        # Its table's first row: oldest first refunds 18.9122, a net of +4.5743.
        pytest.param("10", "40", "33.2501", "20", "18.9122", "14.3378", id="buy-40-sell-20"),
    ],
)
def test_a_partly_sold_lot_refunds_its_bottom_shares_and_the_trader_gains_nothing(
    b: str, bought: str, charged: str, sold: str, paid: str, refund: str
) -> None:
    """A lone trader buys once, sells part, and is voided: the shares left are
    the lot's bottom ones, refunded at the `q` they were bought at and floored
    to the tick. Red under average cost and under oldest first, each of which
    pays the trader more than they put in."""
    trades = _trades(
        (HOLDER, 0, "buy", bought, charged),
        (HOLDER, 0, "sell", sold, paid),
    )

    owed = _owed(trades, b)

    assert owed == {(HOLDER, 0): Decimal(refund)}
    assert -Decimal(charged) + Decimal(paid) + owed[(HOLDER, 0)] <= 0


def test_the_round_trip_refunds_what_the_first_shares_cost() -> None:
    """ADR 0025's round trip at `b = 1`: hold 10, buy 990 more, sell 990. The
    sale empties the 990 lot, so the first lot is untouched and refunded
    exactly its charge, 9.3069. Oldest first refunds 10.0000, +0.6930."""
    trades = _trades(
        (HOLDER, 0, "buy", "10", "9.3069"),
        (HOLDER, 0, "buy", "990", "990.0000"),
        (HOLDER, 0, "sell", "990", "989.9999"),
    )

    assert _owed(trades, "1") == {(HOLDER, 0): Decimal("9.3069")}


def test_an_untouched_lot_refunds_exactly_its_charge_and_is_not_repriced() -> None:
    """ADR 0025: "a lot nothing was sold from: exactly the total it was
    charged". The charge fed here, 7.1234, is not what the engine would price
    a buy of 5 at, so a replay that re-priced the lot reads another figure."""
    trades = _trades((HOLDER, 1, "buy", "5", "7.1234"))

    assert _owed(trades, "1") == {(HOLDER, 1): Decimal("7.1234")}


def test_honest_profit_stands_and_the_bottom_shares_are_refunded() -> None:
    """ADR 0025's "Honest profit stands", at `b = 20`: the holder buys 30
    (20.1654), another trader's buy of 50 (46.3348) raises the price, and the
    holder sells 10 (9.7679). The holder keeps the sale and is refunded the
    cost of their bottom 20 shares from `q = (0, 0)`, 12.4022; the other
    trader, untouched, exactly their charge. Worked out by the independent
    script."""
    trades = _trades(
        (HOLDER, 0, "buy", "30", "20.1654"),
        (OTHER, 0, "buy", "50", "46.3348"),
        (HOLDER, 0, "sell", "10", "9.7679"),
    )

    assert _owed(trades, "20") == {
        (HOLDER, 0): Decimal("12.4022"),
        (OTHER, 0): Decimal("46.3348"),
    }


def test_a_partly_sold_lot_whose_refund_floors_to_nothing_adds_nothing() -> None:
    """ADR 0025: a partly-sold lot whose bottom shares cost less than a tick is
    refunded `0.0000`, as `liquidation_values_of` values such a sale, and
    fails nothing. At `b = 1`, after another trader's 30 of outcome 0, each of
    two holders buys 40 of outcome 1 and sells the top 39, leaving one bottom
    share worth about 1.6e-13 and 4.4e-13. The second holder has no other lot,
    so their position is owed `0.0000`. The first then buys 34 more (6.0025),
    and is owed exactly that. Red if `ProceedsBelowTick` escapes."""
    trades = _trades(
        (OTHER, 0, "buy", "30", "29.3069"),
        (HOLDER, 1, "buy", "40", "10.0001"),
        (HOLDER, 1, "sell", "39", "10.0000"),
        (THIRD, 1, "buy", "40", "11.0001"),
        (THIRD, 1, "sell", "39", "11.0000"),
        (HOLDER, 1, "buy", "34", "6.0025"),
    )

    owed = _owed(trades, "1")

    assert owed[(HOLDER, 1)] == Decimal("6.0025")
    assert owed[(THIRD, 1)] == Decimal("0.0000")


def test_a_sale_takes_the_newest_lot_first_across_lots() -> None:
    """A sale larger than the newest lot empties it and takes the rest off the
    top of the one below. At `b = 1`: buys of 4 (3.3251) and 2 (1.9844), then
    a sale of 3. Newest first, the 2 lot goes and the 4 lot keeps its bottom
    3 shares, refunded `C(3,0) - C(0,0)` floored, 2.3554. Oldest first
    would leave the 2 lot's top 2 and the 4 lot's untouched 1."""
    trades = _trades(
        (HOLDER, 0, "buy", "4", "3.3251"),
        (HOLDER, 0, "buy", "2", "1.9844"),
        (HOLDER, 0, "sell", "3", "2.9538"),
    )

    replay = lot_replay_of(trades, outcome_count=2, b=Decimal("1"))

    assert replay.owed == {(HOLDER, 0): Decimal("2.3554")}
    assert replay.shares_left == {(HOLDER, 0): Decimal("3")}


def test_the_replay_rebuilds_q_and_names_every_trader() -> None:
    """`q` is rebuilt from the quantities alone, and `traders` names a holder
    who sold to zero, who holds nothing and is owed nothing: the stake check
    counts them (ADR 0025, "Who may void")."""
    trades = _trades(
        (HOLDER, 0, "buy", "4", "3.3251"),
        (OTHER, 1, "buy", "2", "1.0000"),
        (HOLDER, 0, "sell", "4", "3.0000"),
    )

    replay = lot_replay_of(trades, outcome_count=2, b=Decimal("1"))

    assert replay.q == (Decimal("0"), Decimal("2"))
    assert replay.traders == frozenset({HOLDER, OTHER})
    assert (HOLDER, 0) not in replay.owed
    assert replay.shares_left == {(OTHER, 1): Decimal("2")}


def test_a_sale_of_more_than_was_bought_is_refused() -> None:
    """Trades that sell shares their holder never bought cannot be replayed:
    `ValueError`, which the void reads as `void_replay_mismatch`."""
    trades = _trades(
        (HOLDER, 0, "buy", "1", "0.5000"),
        (HOLDER, 0, "sell", "2", "1.0000"),
    )

    with pytest.raises(ValueError, match="more shares than its holder bought"):
        lot_replay_of(trades, outcome_count=2, b=Decimal("1"))


# =========================================================================
# The guard
# =========================================================================
def _guarded(trades, **book) -> None:  # noqa: ANN001, ANN003
    """Replay `trades` at `b = 1` and run the guard against `book`, which
    defaults to agreeing with them."""
    replay = lot_replay_of(trades, outcome_count=2, b=Decimal("1"))
    agreed = {
        "positions": dict(replay.shares_left),
        "q": list(replay.q),
        "state_version": len(trades),
    }
    agreed.update(book)
    refuse_if_replay_disagrees(replay, trades, **agreed)


_TWO_HOLDERS = [
    (HOLDER, 0, "buy", "4", "3.3251"),
    (OTHER, 1, "buy", "2", "1.0000"),
]


def test_a_position_sold_to_zero_is_not_a_disagreement() -> None:
    """A position sold to zero keeps its row, at `0`, and holds no lot: the
    guard compares positions with shares only. Also the agreeing book every
    refusal below changes one thing from."""
    trades = _trades(*_TWO_HOLDERS)
    _guarded(
        trades,
        positions={
            (HOLDER, 0): Decimal("4"),
            (OTHER, 1): Decimal("2"),
            (THIRD, 0): Decimal("0"),
        },
    )


@pytest.mark.parametrize(
    "book",
    [
        pytest.param(
            {"positions": {(HOLDER, 0): Decimal("4")}}, id="a-position-missing"
        ),
        pytest.param(
            {
                "positions": {
                    (HOLDER, 0): Decimal("4"),
                    (OTHER, 1): Decimal("2"),
                    (THIRD, 1): Decimal("1"),
                }
            },
            id="a-position-with-no-trades",
        ),
        pytest.param(
            {"positions": {(HOLDER, 0): Decimal("4"), (OTHER, 1): Decimal("2.0001")}},
            id="a-position-off-by-a-tick",
        ),
        pytest.param({"q": [Decimal("4"), Decimal("3")]}, id="q"),
        pytest.param({"state_version": 3}, id="the-book-past-the-last-trade"),
    ],
)
def test_a_replay_that_disagrees_with_the_book_is_refused(book: dict) -> None:
    """ADR 0025, "The guard": the shares left against the positions, compared
    both ways, so a position with shares and no trades is caught too; the
    replayed `q` against the book's; and the last trade's version against the
    book's `state_version`, the half of the version check that is the void's
    alone."""
    with pytest.raises(ValueError):
        _guarded(_trades(*_TWO_HOLDERS), **book)


def test_a_version_gap_is_refused_by_the_guard() -> None:
    """The half of the version check the price history shares: versions 1 and
    3, with the book at 3, so only the run from 1 can refuse."""
    trades = _trades(*_TWO_HOLDERS)
    gapped = [trades[0], VoidTrade(**{**trades[1].__dict__, "state_version": 3})]

    with pytest.raises(ValueError, match="expected state_version 2, got 3"):
        _guarded(gapped, state_version=3)


def test_a_market_nobody_traded_passes_the_guard_only_at_version_zero() -> None:
    """No trades end at version 0, so a book at 0 agrees and one at 1 does
    not."""
    _guarded([], state_version=0)
    with pytest.raises(ValueError):
        _guarded([], state_version=1)


# =========================================================================
# The cap of one more seed
# =========================================================================
def test_a_pool_that_covers_the_refunds_pays_them_and_returns_the_rest() -> None:
    """`P >= R`: every refund in full, and `P - R` to PLATFORM."""
    cut = cut_of(
        {(HOLDER, 0): Decimal("30.0000"), (OTHER, 1): Decimal("15.0000")},
        pool=Decimal("50.1234"),
        seed=Decimal("10.0000"),
    )

    assert cut.refunds == {(HOLDER, 0): Decimal("30.0000"), (OTHER, 1): Decimal("15.0000")}
    assert (cut.total_owed, cut.total_refunded) == (Decimal("45.0000"), Decimal("45.0000"))
    assert cut.refunds_cut is False
    assert cut.residue == Decimal("5.1234")


@pytest.mark.parametrize(
    ("pool", "refunds_cut"),
    [
        pytest.param("35.0000", False, id="short-by-exactly-one-seed"),
        pytest.param("34.9999", True, id="short-by-a-tick-more"),
    ],
)
def test_platform_covers_a_shortfall_of_up_to_one_seed_and_no_more(
    pool: str, refunds_cut: bool
) -> None:
    """`R - P <= S`: refunds in full, PLATFORM covering the gap. One tick
    beyond it, every refund is cut. Both edges, so `>` and `>=` differ."""
    owed = {(HOLDER, 0): Decimal("30.0000"), (OTHER, 1): Decimal("15.0000")}

    cut = cut_of(owed, pool=Decimal(pool), seed=Decimal("10.0000"))

    assert cut.refunds_cut is refunds_cut
    if not refunds_cut:
        assert cut.refunds == owed
        assert cut.residue == Decimal("-10.0000")
    else:
        # Each cut by 44.9999/45 and floored: 29.99993 and 14.99996.
        assert cut.refunds == {
            (HOLDER, 0): Decimal("29.9999"),
            (OTHER, 1): Decimal("14.9999"),
        }
        assert cut.residue == Decimal("-9.9999")


def test_beyond_one_seed_every_refund_is_cut_by_the_same_fraction() -> None:
    """ADR 0025's illustration: `S = 10`, `P = 40`, refunds owed 50 and 20.
    PLATFORM adds the one seed, and each refund is cut by `50/70`: 35.7142 and
    14.2857, 49.9999 in all. The tick left over goes back, so PLATFORM's net
    is 9.9999 into the pool. Red if PLATFORM covers the whole gap."""
    cut = cut_of(
        {(HOLDER, 0): Decimal("50.0000"), (OTHER, 1): Decimal("20.0000")},
        pool=Decimal("40.0000"),
        seed=Decimal("10.0000"),
    )

    assert cut.refunds == {
        (HOLDER, 0): Decimal("35.7142"),
        (OTHER, 1): Decimal("14.2857"),
    }
    assert cut.refunds_cut is True
    assert (cut.total_owed, cut.total_refunded) == (Decimal("70.0000"), Decimal("49.9999"))
    assert cut.residue == Decimal("-9.9999")


def test_equal_thirds_are_cut_to_the_same_exact_tick() -> None:
    """ADR 0025, "The cut's arithmetic": three holders each owed the same,
    with `P + S` three times a figure on a tick, so each cut refund is exactly
    that figure, 9999999999999.9999. Dividing first leaves `1/3` short at
    any finite precision, and multiplying at the default precision of 28
    rounds the product down: each reads 9999999999999.9998. Found by a search
    at both precisions; the expected value is `(P + S) / 3`, by hand."""
    each = Decimal("13936071511245.7845")
    owed = {(HOLDER, 0): each, (OTHER, 0): each, (THIRD, 1): each}

    cut = cut_of(
        owed, pool=Decimal("29999999998999.9997"), seed=Decimal("1000.0000")
    )

    assert set(cut.refunds.values()) == {Decimal("9999999999999.9999")}
    assert len(cut.refunds) == 3


def test_a_refund_the_cut_floors_to_zero_is_left_out() -> None:
    """ADR 0025: "A refund above zero" is judged after the cut. Owed 0.0001
    beside 1000, cut by about a tenth, it floors to `0.0000`: no refund, and
    its share stays in the residue."""
    cut = cut_of(
        {(HOLDER, 0): Decimal("1000.0000"), (OTHER, 1): Decimal("0.0001")},
        pool=Decimal("90.0000"),
        seed=Decimal("10.0000"),
    )

    assert cut.refunds == {(HOLDER, 0): Decimal("99.9999")}
    assert cut.total_refunded == Decimal("99.9999")
    assert cut.residue == Decimal("-9.9999")
