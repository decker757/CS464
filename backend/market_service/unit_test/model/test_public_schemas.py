"""The trader-facing projections. [BE][X] #62. Pure shape, no database.

The ledger needs `liquidity_b` exact and the admin form needs it as a number,
so there are two schemas; the guards below pin both halves. D-016, D-021.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from model.entities import MarketStatus, displayed_status
from model.schemas import PublicMarketOut


class _FakeOutcome:
    def __init__(self, position: int, label: str) -> None:
        self.id = uuid.uuid4()
        self.position = position
        self.label = label


class _FakeSource:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.position = 0
        self.url = "https://www.mas.gov.sg/statistics"
        self.label = "MAS official statistics"


class _FakePublishedMarket:
    """A published market as the ORM hands one over, with naive timestamps.

    Not `test_schemas.py`'s `_FakeMarket`, which is a draft this projection
    never receives.
    """

    def __init__(
        self,
        *,
        status: MarketStatus = MarketStatus.OPEN,
        closes_in: timedelta = timedelta(days=30),
        liquidity_b: Decimal | None = Decimal("100.0000"),
        proposed: bool | None = None,
        settleable: bool = False,
    ) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)

        self.id = uuid.uuid4()
        self.draft_key = uuid.uuid4()
        self.creator_id = uuid.uuid4()
        self.status = status

        self.question = "Will Singapore core inflation be below 2% in December 2026?"
        self.description = "Core inflation as first published by MAS."
        self.outcomes = [_FakeOutcome(0, "Yes"), _FakeOutcome(1, "No")]

        self.close_time = now + closes_in
        self.resolution_time = now + closes_in + timedelta(days=15)

        self.resolution_criteria = (
            "Resolves YES on the first published MAS print below 2.0%."
        )
        self.resolution_sources = [_FakeSource()]

        self.liquidity_b = liquidity_b
        self.seed_subsidy = Decimal("250.0000")

        self.created_at = now - timedelta(days=2)
        self.updated_at = now - timedelta(days=1)
        self.submitted_at = now - timedelta(days=1)
        self.published_at = now - timedelta(days=1)
        self.closed_at = None

        # `proposed` defaults to what the status implies on a real row. Force it
        # to test the gate at OPEN or CLOSED, or there is nothing to hide.
        if proposed is None:
            proposed = status in (
                MarketStatus.PENDING_RESOLUTION,
                MarketStatus.APPROVED,
                MarketStatus.SETTLED,
            )
        approved = status in (MarketStatus.APPROVED, MarketStatus.SETTLED)

        self.proposal_id = uuid.uuid4() if proposed else None
        self.proposed_outcome_id = self.outcomes[0].id if proposed else None
        self.proposed_by_id = uuid.uuid4() if proposed else None
        self.proposed_by_username = "ernest_t" if proposed else None
        self.proposed_at = now - timedelta(hours=2) if proposed else None
        self.proposal_evidence_url = (
            "https://www.mas.gov.sg/statistics" if proposed else None
        )
        self.proposal_evidence_note = "First print was 1.8%." if proposed else None

        self.approved_by_id = uuid.uuid4() if approved else None
        self.approved_by_username = "ihsan_b" if approved else None
        self.approved_at = now - timedelta(hours=1) if approved else None

        # What `get_published` stamps before projection (D-027), from the real
        # `displayed_status` so the derivation itself is exercised.
        self.trader_facing_status = displayed_status(self.status, self.close_time)
        # What `get_published` stamps beside it. [3.4] #12.
        self.settleable = settleable


def _detail_json(**kwargs: object) -> dict[str, Any]:
    model = PublicMarketOut.model_validate(_FakePublishedMarket(**kwargs))
    return json.loads(model.model_dump_json())


# --- the ledger's requirement: exact numbers on the wire ------------------
def test_liquidity_b_serialises_as_a_decimal_string_not_a_json_number() -> None:
    """The ledger prices every trade from this `b`; a JSON number is a double. D-016."""
    payload = _detail_json()

    assert isinstance(payload["liquidity_b"], str), (
        f"liquidity_b is {type(payload['liquidity_b'])}; "
        "a JSON number here is an IEEE double at the root of every price"
    )
    assert Decimal(payload["liquidity_b"]) == Decimal("100")


def test_seed_subsidy_serialises_as_a_decimal_string_too() -> None:
    """The ledger funds the market pool from it, and a float subsidy is a float credit."""
    payload = _detail_json()

    assert isinstance(payload["seed_subsidy"], str)
    assert Decimal(payload["seed_subsidy"]) == Decimal("250")


def test_the_administrators_market_out_still_serialises_them_as_floats() -> None:
    """Guard: if this fails, the ledger's exactness was bought by breaking the
    admin form. The fix is the second schema, not a changed first one. D-021."""
    from model.schemas import MarketOut  # noqa: PLC0415

    payload = json.loads(
        MarketOut.model_validate(_FakePublishedMarket()).model_dump_json()
    )

    assert isinstance(payload["liquidity_b"], float)
    assert isinstance(payload["seed_subsidy"], float)


def test_every_outcome_carries_its_id_and_position() -> None:
    """The ledger opens its book from these: one `q` per outcome id, in position order."""
    payload = _detail_json()

    assert [outcome["position"] for outcome in payload["outcomes"]] == [0, 1]
    for outcome in payload["outcomes"]:
        assert uuid.UUID(outcome["id"])
        assert outcome["label"]


# --- what a trader must not be handed -------------------------------------
@pytest.mark.parametrize("field", ["creator_id", "draft_key"])
def test_the_public_projection_does_not_carry_internal_fields(field: str) -> None:
    """Neither is a trader's business, and neither is harmless. D-019."""
    assert field not in _detail_json()


# --- ADR 0011: the status a trader is shown is derived --------------------
def test_a_market_within_its_close_time_serialises_as_open() -> None:
    """The control: without it, a hard-coded `closed` would pass every test below."""
    assert _detail_json()["status"] == MarketStatus.OPEN.value


def test_a_market_past_its_close_time_serialises_as_closed() -> None:
    """Column `open`, close time passed, sweep not yet run: a trader sees `closed`.

    ADR 0011's amendment, D-022.
    """
    payload = _detail_json(closes_in=timedelta(seconds=-1))

    assert payload["status"] == MarketStatus.CLOSED.value


def test_the_administrators_projections_still_report_the_raw_status_column() -> None:
    """Guard for the admin half of ADR 0011's amendment: the same stopped market
    still reads `open` to an administrator, whose only sign of a stalled sweep
    it is. Do not edit this assertion to make a refactor pass. D-022."""
    from model.schemas import MarketOut, MarketSummaryOut  # noqa: PLC0415

    stopped = _FakePublishedMarket(closes_in=timedelta(seconds=-1))

    detail = json.loads(MarketOut.model_validate(stopped).model_dump_json())
    summary = json.loads(MarketSummaryOut.model_validate(stopped).model_dump_json())

    assert detail["status"] == MarketStatus.OPEN.value
    assert summary["status"] == MarketStatus.OPEN.value

    # And the public projection derives on the same object.
    assert _detail_json(closes_in=timedelta(seconds=-1))["status"] == (
        MarketStatus.CLOSED.value
    )


def test_an_early_closed_market_is_not_reopened_by_the_derivation() -> None:
    """An early close keeps a future `close_time` (ADR 0014); the derivation must
    only ever make a market less tradeable."""
    payload = _detail_json(status=MarketStatus.CLOSED, closes_in=timedelta(days=30))

    assert payload["status"] == MarketStatus.CLOSED.value


@pytest.mark.parametrize(
    "status",
    [MarketStatus.PENDING_RESOLUTION, MarketStatus.APPROVED, MarketStatus.SETTLED],
)
def test_a_resolving_status_is_reported_as_it_stands(status: MarketStatus) -> None:
    """[X-3] #36 renders these apart from a plain closed market; do not flatten them."""
    payload = _detail_json(status=status, closes_in=timedelta(days=30))

    assert payload["status"] == status.value


# --- timestamps -----------------------------------------------------------
def test_timestamps_carry_an_offset() -> None:
    """The `_UtcTimestamps` guard; [X-1] #34 counts down from `close_time`."""
    model = PublicMarketOut.model_validate(_FakePublishedMarket())

    assert model.close_time is not None and model.close_time.tzinfo is not None
    assert model.resolution_time is not None
    assert model.resolution_time.tzinfo is not None


# --- a proposed winner is not a decided one -------------------------------
def test_a_pending_proposal_is_not_shown_to_a_trader() -> None:
    """[X-3] #36 shows *settled* winners; a pending one may still be rejected. D-026."""
    payload = _detail_json(status=MarketStatus.PENDING_RESOLUTION)

    assert payload["status"] == MarketStatus.PENDING_RESOLUTION.value
    assert payload["proposed_outcome_id"] is None


@pytest.mark.parametrize("status", [MarketStatus.APPROVED, MarketStatus.SETTLED])
def test_a_decided_outcome_is_shown(status: MarketStatus) -> None:
    """The other half: once a second administrator agrees, the winner is public,
    and stays public once settled ([3.4] #12)."""
    payload = _detail_json(status=status)

    assert payload["status"] == status.value
    assert payload["proposed_outcome_id"] is not None


@pytest.mark.parametrize(
    "status", [MarketStatus.OPEN, MarketStatus.CLOSED, MarketStatus.PENDING_RESOLUTION]
)
def test_no_status_before_approval_exposes_a_winner(status: MarketStatus) -> None:
    """The rule as a property. `proposed=True` on every case, or OPEN and CLOSED
    would pass with the gate deleted."""
    payload = _detail_json(status=status, proposed=True)

    assert payload["proposed_outcome_id"] is None
