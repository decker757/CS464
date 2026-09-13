"""Request and response contracts. Pure validation, no database.

These are the rules Michelle's form sees as 422s, so they are worth asserting
directly rather than only through a route.

The line this file defends: shape is validated here, completeness is not. A
draft that breaks every business rule must still parse, or the autosave cannot
save a half-typed form.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from model.entities import MarketStatus
from model.schemas import (
    MAX_OUTCOMES,
    MAX_QUESTION_LENGTH,
    MarketDraftRequest,
    MarketOut,
)


def _payload(**overrides: object) -> dict[str, object]:
    return {"draft_key": str(uuid.uuid4()), **overrides}


def test_an_almost_empty_draft_parses() -> None:
    """The autosave's worst case: the admin opened the form and typed nothing."""
    parsed = MarketDraftRequest(**_payload())

    assert parsed.status is MarketStatus.DRAFT
    assert parsed.question is None
    assert parsed.outcomes == []
    assert parsed.resolution_sources == []


def test_a_draft_may_break_every_business_rule_and_still_parse() -> None:
    """Close after resolution, both in the past, one blank outcome.

    All of these are submission failures and none of them is a parse failure,
    because the admin is mid-thought and the timer fired.
    """
    parsed = MarketDraftRequest(
        **_payload(
            question="?",
            outcomes=[{"label": ""}],
            close_time="2020-01-02T00:00:00Z",
            resolution_time="2020-01-01T00:00:00Z",
            resolution_sources=[{"url": "htt"}],
        )
    )

    assert parsed.close_time > parsed.resolution_time


def test_draft_key_is_required() -> None:
    """Without it the endpoint has no identity to upsert on and every autosave
    would create a new market."""
    with pytest.raises(ValidationError):
        MarketDraftRequest(question="Will it rain?")  # type: ignore[call-arg]


def test_a_non_uuid_draft_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        MarketDraftRequest(**_payload(draft_key="not-a-uuid"))


@pytest.mark.parametrize("field", ["close_time", "resolution_time"])
def test_a_timestamp_without_an_offset_is_refused(field: str) -> None:
    """The most expensive small bug available here.

    Assuming UTC for a naive timestamp puts a Singapore admin's close time
    eight hours out, and a market that closes at the wrong hour settles on the
    wrong facts. Better a 422 the frontend can fix than a silent guess.
    """
    with pytest.raises(ValidationError):
        MarketDraftRequest(**_payload(**{field: "2027-01-01T00:00:00"}))


@pytest.mark.parametrize(
    "raw", ["2027-01-01T00:00:00Z", "2027-01-01T08:00:00+08:00"]
)
def test_a_timestamp_with_an_offset_is_accepted(raw: str) -> None:
    assert MarketDraftRequest(**_payload(close_time=raw)).close_time is not None


def test_too_many_outcomes_are_refused() -> None:
    """A shape ceiling, not a business rule: b·ln(n) means a 200-outcome market
    would price fine and be unusable."""
    too_many = [{"label": f"Outcome {i}"} for i in range(MAX_OUTCOMES + 1)]

    with pytest.raises(ValidationError):
        MarketDraftRequest(**_payload(outcomes=too_many))


def test_an_overlong_question_is_refused() -> None:
    with pytest.raises(ValidationError):
        MarketDraftRequest(**_payload(question="x" * (MAX_QUESTION_LENGTH + 1)))


@pytest.mark.parametrize("status", ["draft", "submitted"])
def test_both_statuses_are_accepted_on_the_way_in(status: str) -> None:
    assert MarketDraftRequest(**_payload(status=status)).status == status


@pytest.mark.parametrize("status", ["open", "closed", "resolved", "published"])
def test_a_status_this_ticket_does_not_own_is_refused(status: str) -> None:
    """OPEN arrives with [1.3] #3 and the rest with epic 3. Accepting one now
    would let a market skip the publish step entirely."""
    with pytest.raises(ValidationError):
        MarketDraftRequest(**_payload(status=status))


class _FakeMarket:
    """Stands in for an entity carrying naive timestamps, as a driver might."""

    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.draft_key = uuid.uuid4()
        self.creator_id = uuid.uuid4()
        self.status = MarketStatus.DRAFT
        self.question = "Will it rain in Singapore tomorrow?"
        self.description = None
        self.outcomes = []
        self.close_time = datetime(2027, 1, 1, 8, 0, 0)
        self.resolution_time = None
        self.resolution_criteria = None
        self.resolution_sources = []
        self.created_at = datetime(2026, 9, 13, 8, 0, 0)
        self.updated_at = datetime(2026, 9, 13, 8, 0, 0)
        self.submitted_at = None


def test_naive_timestamps_are_stamped_as_utc_on_the_way_out() -> None:
    """Guards the contract that every timestamp we emit ends in Z.

    Without it one market serialises with an offset and another without, and
    the frontend has to special-case which.
    """
    out = MarketOut.model_validate(_FakeMarket())

    assert out.close_time is not None and out.close_time.tzinfo is not None
    assert out.created_at.tzinfo is not None
    assert out.submitted_at is None


def test_an_aware_timestamp_is_left_alone() -> None:
    entity = _FakeMarket()
    entity.close_time = datetime(2027, 1, 1, 8, 0, 0, tzinfo=UTC)

    out = MarketOut.model_validate(entity)

    assert out.close_time == datetime(2027, 1, 1, 8, 0, 0, tzinfo=UTC)
