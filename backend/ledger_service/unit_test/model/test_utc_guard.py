"""A naive datetime on a ledger response model is stamped UTC. [#296]

Five models carry the guard: a driver or a caller that hands one a naive value
must not put a time on the wire with no offset, which every browser reads as
local time. Naive versus aware has already caused bugs here (CLAUDE.md).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import BaseModel

from core.pricing import Side
from model.entities import TransactionKind
from model.schemas import (
    ExposureOut,
    LedgerEntryOut,
    MarketTradeOut,
    PriceEvent,
    SnapshotOut,
)

_ID = uuid.UUID(int=1)
_PRICES = [
    {"outcome_id": uuid.UUID(int=2), "position": 0, "price": Decimal("0.4")},
    {"outcome_id": uuid.UUID(int=3), "position": 1, "price": Decimal("0.6")},
]


def _ledger_entry(when: datetime | None) -> LedgerEntryOut:
    return LedgerEntryOut(
        id=_ID,
        created_at=when,  # type: ignore[arg-type]
        amount=Decimal("1.0000"),
        balance_after=Decimal("1.0000"),
        transaction_id=_ID,
        kind=TransactionKind.SIGNUP_GRANT,
    )


def _price_event(when: datetime | None) -> PriceEvent:
    return PriceEvent(
        market_id=_ID,
        state_version=1,
        prices=_PRICES,  # type: ignore[arg-type]
        occurred_at=when,  # type: ignore[arg-type]
    )


def _snapshot(when: datetime | None) -> SnapshotOut:
    return SnapshotOut(
        market_id=_ID,
        state_version=1,
        prices=_PRICES,  # type: ignore[arg-type]
        occurred_at=when,  # type: ignore[arg-type]
    )


def _exposure(when: datetime | None) -> ExposureOut:
    return ExposureOut(
        market_id=_ID,
        opened_at=when,
        state_version=1,
        seed_subsidy=Decimal("1.0000"),
        pool=Decimal("1.0000"),
        payout_exceeds_pool=False,
        result=None,
        winning_outcome_id=None,
        outcomes=[],
    )


def _market_trade(when: datetime | None) -> MarketTradeOut:
    return MarketTradeOut(
        transaction_id=_ID,
        created_at=when,  # type: ignore[arg-type]
        user_id=_ID,
        outcome_id=_ID,
        side=Side.BUY,
        quantity=Decimal("1.0000"),
        average_price=Decimal("0.5000"),
        total=Decimal("-0.5000"),
        state_version=1,
    )


_GUARDED_FIELDS = [
    pytest.param(_ledger_entry, "created_at", id="LedgerEntryOut.created_at"),
    pytest.param(_price_event, "occurred_at", id="PriceEvent.occurred_at"),
    pytest.param(_snapshot, "occurred_at", id="SnapshotOut.occurred_at"),
    pytest.param(_exposure, "opened_at", id="ExposureOut.opened_at"),
    pytest.param(_market_trade, "created_at", id="MarketTradeOut.created_at"),
]


@pytest.mark.parametrize(("build", "field"), _GUARDED_FIELDS)
def test_a_naive_datetime_is_stamped_utc_with_the_same_wall_clock(
    build: Callable[[datetime | None], BaseModel], field: str
) -> None:
    out = build(datetime(2026, 3, 4, 5, 6, 7))

    value = getattr(out, field)
    assert value.tzinfo is not None
    assert value.utcoffset() == timedelta(0)
    assert value == datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)


def test_an_exposure_with_no_opened_at_keeps_it_none() -> None:
    assert _exposure(None).opened_at is None
