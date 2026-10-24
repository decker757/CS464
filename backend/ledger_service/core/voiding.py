"""A void's refunds, worked out from the market's trades. [BE] #221, ADR 0025

Three steps, each a function here, in the order the void calls them:

1. `lot_replay_of` rebuilds `q` from the trades' quantities and keeps a stack
   of lots per holder and outcome. A buy pushes a lot; a sale takes shares off
   the newest lot first. Each lot left is refunded what its remaining shares
   cost: an untouched lot exactly its charge, a partly-sold one the cost of
   its bottom shares at the `q` it was bought at, floored to the tick.
2. `refuse_if_replay_disagrees` is the guard: the shares left must be the
   positions, the replayed `q` the book's, and the trades' versions must run
   1, 2, 3, ... and end at the book's `state_version`.
3. `cut_of` is the cap: PLATFORM covers a shortfall of up to one seed, and
   beyond that every refund is cut by the same fraction.

Pure. No session, no clock. The guard raises `ValueError`, as the price
history's replay does, and the service maps it to its own code.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from core.errors import ProceedsBelowTick
from core.lmsr import engine_context
from core.state_versions import require_consecutive_versions
from core.pricing import QUANTUM, Side, trade_cost_of

ZERO = Decimal(0)
_ZERO_AMOUNT = Decimal("0.0000")

# One holder's shares in one outcome: `(user_id, outcome position)`.
HolderOutcome = tuple[uuid.UUID, int]


@dataclass(frozen=True)
class VoidTrade:
    """One trade or fill as the lot replay needs it. `total` is unsigned: what
    a buy was charged, or what a sale paid."""

    user_id: uuid.UUID
    position: int
    side: Side
    quantity: Decimal
    total: Decimal
    state_version: int


@dataclass
class _Lot:
    """One buy's shares still held, and what it takes to price them."""

    bought: Decimal
    charged: Decimal
    q_before: tuple[Decimal, ...]
    left: Decimal


@dataclass(frozen=True)
class LotReplay:
    """What the trades say each holder is owed and still holds.

    `owed` and `shares_left` name every position with shares left, `owed` at
    the tick and possibly `0.0000`. `traders` is everyone who traded, a
    position sold to zero included.
    """

    owed: dict[HolderOutcome, Decimal]
    shares_left: dict[HolderOutcome, Decimal]
    q: tuple[Decimal, ...]
    traders: frozenset[uuid.UUID]

    @property
    def total_owed(self) -> Decimal:
        return sum(self.owed.values(), _ZERO_AMOUNT)


@dataclass(frozen=True)
class Cut:
    """The refunds to pay, after the cap. `refunds` holds only those above
    zero. `residue` is what moves from the pool to PLATFORM once they are
    paid, negative when PLATFORM tops the pool up: the pool ends at zero."""

    refunds: dict[HolderOutcome, Decimal]
    total_owed: Decimal
    total_refunded: Decimal
    refunds_cut: bool
    residue: Decimal


def _sell_newest_first(lots: list[_Lot], quantity: Decimal) -> None:
    """Take `quantity` off the top of `lots`, newest lot first, dropping each
    lot it empties. Raises `ValueError` for a sale of more than the lots hold:
    the trades disagree with themselves."""
    to_sell = quantity
    while to_sell > 0:
        if not lots:
            raise ValueError("a sale takes more shares than its holder bought")
        newest = lots[-1]
        taken = min(newest.left, to_sell)
        newest.left -= taken
        to_sell -= taken
        if newest.left == 0:
            lots.pop()


def _refund_of(lot: _Lot, position: int, b: Decimal) -> Decimal:
    """What one lot's remaining shares cost.

    An untouched lot is refunded exactly its charge. A partly-sold one is
    refunded `C(q_lot + left) - C(q_lot)`, priced as a sale of its bottom
    `left` shares so it is floored to the tick as every sale is; one whose
    price floors to nothing is `0.0000`, as `liquidation_values_of` reads it.
    """
    if lot.left == lot.bought:
        return lot.charged
    with engine_context():
        top = list(lot.q_before)
        top[position] += lot.left
    try:
        sale = trade_cost_of(top, b, index=position, side=Side.SELL, quantity=lot.left)
    except ProceedsBelowTick:
        return _ZERO_AMOUNT
    return sale.magnitude


def lot_replay_of(
    trades: Sequence[VoidTrade], *, outcome_count: int, b: Decimal
) -> LotReplay:
    """Replay `trades`, oldest first, into lots, and price what is left.

    Raises `ValueError` if a sale takes more than its holder's lots hold. Does
    not check the trades' versions: that is `refuse_if_replay_disagrees`'s.
    """
    q = [ZERO] * outcome_count
    stacks: dict[HolderOutcome, list[_Lot]] = {}
    traders: set[uuid.UUID] = set()

    for trade in trades:
        traders.add(trade.user_id)
        lots = stacks.setdefault((trade.user_id, trade.position), [])
        with engine_context():
            if trade.side is Side.BUY:
                lots.append(
                    _Lot(
                        bought=trade.quantity,
                        charged=trade.total,
                        q_before=tuple(q),
                        left=trade.quantity,
                    )
                )
                q[trade.position] += trade.quantity
            else:
                _sell_newest_first(lots, trade.quantity)
                q[trade.position] -= trade.quantity

    owed: dict[HolderOutcome, Decimal] = {}
    shares_left: dict[HolderOutcome, Decimal] = {}
    for key, lots in stacks.items():
        if not lots:
            continue
        _, position = key
        owed[key] = sum((_refund_of(lot, position, b) for lot in lots), _ZERO_AMOUNT)
        shares_left[key] = sum((lot.left for lot in lots), ZERO)

    return LotReplay(
        owed=owed, shares_left=shares_left, q=tuple(q), traders=frozenset(traders)
    )


def refuse_if_replay_disagrees(
    replay: LotReplay,
    trades: Sequence[VoidTrade],
    *,
    positions: Mapping[HolderOutcome, Decimal],
    q: Sequence[Decimal],
    state_version: int,
) -> None:
    """Raise `ValueError` unless the replay agrees with the book.

    The trades' versions run 1, 2, 3, ... (the price history's check) and end
    at the book's `state_version`; the shares left in the lots are exactly the
    positions with shares, compared both ways; and the replayed `q` is the
    book's. ADR 0025, "The guard".
    """
    versions = [trade.state_version for trade in trades]
    require_consecutive_versions(versions)
    last = versions[-1] if versions else 0
    if last != state_version:
        raise ValueError(f"the trades end at {last}, the book at {state_version}")

    held = {key: quantity for key, quantity in positions.items() if quantity != 0}
    if held != replay.shares_left:
        raise ValueError("the shares left in the lots are not the positions")
    if tuple(q) != replay.q:
        raise ValueError(f"the replay ends at q = {replay.q}, the book at {tuple(q)}")


def refunds_are_cut(*, total_owed: Decimal, pool: Decimal, seed: Decimal) -> bool:
    """Whether the refunds owe more than the pool plus one more seed, so each
    is cut. Exactly one seed short is covered in full. A repeat asks this
    again of the figures its entries give back, so it answers as the first
    request did."""
    return total_owed - pool > seed


def cut_of(
    owed: Mapping[HolderOutcome, Decimal], *, pool: Decimal, seed: Decimal
) -> Cut:
    """The refunds `owed`, paid from `pool`, with PLATFORM adding at most
    `seed` more.

    Short by at most one seed, every refund is paid in full. Short by more,
    each refund `r` is cut to `r * (pool + seed) / total owed`, multiplied
    first and divided second at the engine's precision, then floored to the
    tick, so the cut refunds never add up to more than `pool + seed` and two
    builders cut a refund to the same tick. A refund that comes to `0.0000`
    is left out. ADR 0025, "Two accounts, and the cap of one more seed".
    """
    total_owed = sum(owed.values(), _ZERO_AMOUNT)
    refunds_cut = refunds_are_cut(total_owed=total_owed, pool=pool, seed=seed)

    paid: dict[HolderOutcome, Decimal] = {}
    for key, refund in owed.items():
        if refunds_cut:
            with engine_context():
                refund = ((refund * (pool + seed)) / total_owed).quantize(
                    QUANTUM, rounding=ROUND_FLOOR
                )
        if refund > 0:
            paid[key] = refund

    total_refunded = sum(paid.values(), _ZERO_AMOUNT)
    return Cut(
        refunds=paid,
        total_owed=total_owed,
        total_refunded=total_refunded,
        refunds_cut=refunds_cut,
        residue=pool - total_refunded,
    )
