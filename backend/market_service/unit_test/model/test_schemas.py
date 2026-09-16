"""Request and response contracts. Pure validation, no database.

These are the rules Michelle's form sees as 422s, so they are worth asserting
directly rather than only through a route.

The line this file defends: shape is validated here, completeness is not. A
draft that breaks every business rule must still parse, or the autosave cannot
save a half-typed form.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from model.entities import MarketStatus
from model.schemas import (
    MAX_PROSE_LENGTH,
    MAX_OUTCOMES,
    MAX_PRICING_VALUE,
    MAX_QUESTION_LENGTH,
    MAX_URL_LENGTH,
    MarketDraftRequest,
    MarketOut,
    OutcomeProposalRequest,
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


class _FakeOutcome:
    def __init__(self, position: int, label: str | None = None) -> None:
        self.id = uuid.uuid4()
        self.position = position
        self.label = f"Outcome {position}" if label is None else label


class _FakeMarket:
    """Stands in for an entity carrying naive timestamps, as a driver might."""

    def __init__(
        self,
        *,
        outcomes: int = 0,
        labels: list[str] | None = None,
        liquidity_b: Decimal | None = Decimal("100"),
    ) -> None:
        self.id = uuid.uuid4()
        self.draft_key = uuid.uuid4()
        self.creator_id = uuid.uuid4()
        self.status = MarketStatus.DRAFT
        self.question = "Will it rain in Singapore tomorrow?"
        self.description = None
        self.outcomes = (
            [_FakeOutcome(i, label) for i, label in enumerate(labels)]
            if labels is not None
            else [_FakeOutcome(i) for i in range(outcomes)]
        )
        self.liquidity_b = liquidity_b
        self.seed_subsidy = Decimal("250")
        self.close_time = datetime(2027, 1, 1, 8, 0, 0)
        self.resolution_time = None
        self.resolution_criteria = None
        self.resolution_sources = []
        self.created_at = datetime(2026, 9, 13, 8, 0, 0)
        self.updated_at = datetime(2026, 9, 13, 8, 0, 0)
        self.submitted_at = None
        self.published_at = None
        self.closed_at = None
        # [3.1] #9. Null together, which is every market that has not had an
        # outcome proposed for it — the state this stand-in is in.
        self.proposed_outcome_id = None
        self.proposed_by_id = None
        self.proposed_by_username = None
        self.proposed_at = None
        self.proposal_evidence_url = None
        self.proposal_evidence_note = None


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


# --- [1.2] #2 pricing -----------------------------------------------------
@pytest.mark.parametrize("bad", [0, -1, "-0.5"])
def test_a_non_positive_liquidity_is_refused(bad: object) -> None:
    """Shape, not completeness, so it is a 422 even on an autosave.

    Absence is fine at any point — the admin has not got there yet — but a
    market priced at b = 0 has no liquidity, and a negative b is not a
    half-finished thought. Neither is a state the form should ever hold.
    """
    with pytest.raises(ValidationError):
        MarketDraftRequest(**_payload(liquidity_b=bad))


@pytest.mark.parametrize("field", ["liquidity_b", "seed_subsidy"])
def test_pricing_may_be_omitted_from_a_draft(field: str) -> None:
    assert getattr(MarketDraftRequest(**_payload()), field) is None


def test_max_platform_loss_is_derived_from_b_and_the_outcome_count() -> None:
    """b*ln(n): the number the admin is really choosing when they pick b."""
    out = MarketOut.model_validate(_FakeMarket(outcomes=2))

    assert out.max_platform_loss == pytest.approx(69.3147180)


def test_doubling_the_liquidity_doubles_the_worst_case() -> None:
    single = MarketOut.model_validate(_FakeMarket(outcomes=2))
    double = MarketOut.model_validate(
        _FakeMarket(outcomes=2, liquidity_b=Decimal("200"))
    )

    assert single.max_platform_loss is not None
    assert double.max_platform_loss == pytest.approx(2 * single.max_platform_loss)


@pytest.mark.parametrize("count", [2, 3, 10])
def test_every_outcome_opens_at_the_same_price(count: int) -> None:
    """[1.2] #2's third criterion. Before anyone trades no outcome is more
    likely than another, so the prices are uniform and sum to one."""
    out = MarketOut.model_validate(_FakeMarket(outcomes=count))

    prices = [o.initial_price for o in out.outcomes]

    assert prices == [pytest.approx(1 / count)] * count
    assert sum(p for p in prices if p is not None) == pytest.approx(1.0)


@pytest.mark.parametrize("count", [0, 1])
def test_a_market_too_small_to_price_reports_no_prices(count: int) -> None:
    """The ordinary state of a form someone just opened.

    One outcome would advertise a certainty at 100% and a platform that cannot
    lose. Both are arithmetically true and useless, so they are reported as
    absent rather than shown.
    """
    out = MarketOut.model_validate(_FakeMarket(outcomes=count))

    assert out.max_platform_loss is None
    assert all(o.initial_price is None for o in out.outcomes)


def test_max_platform_loss_is_absent_until_b_is_known() -> None:
    out = MarketOut.model_validate(_FakeMarket(outcomes=2, liquidity_b=None))

    assert out.max_platform_loss is None
    assert out.outcomes[0].initial_price == pytest.approx(0.5)


def test_the_pricing_numbers_serialise_as_numbers_not_strings() -> None:
    """The form is meant to compare `seed_subsidy` against `max_platform_loss`.

    Pydantic renders a Decimal as a JSON string, which would put "250" beside
    69.31 and make `subsidy + x` string concatenation in a browser. It would
    also vary: "100" straight from a save, "100.0000" once the same row came
    back from a Numeric(18, 4) column. Exactness stays in the database.
    """
    payload = json.loads(
        MarketOut.model_validate(_FakeMarket(outcomes=2)).model_dump_json()
    )

    for field in ("liquidity_b", "seed_subsidy", "max_platform_loss"):
        assert isinstance(payload[field], float), f"{field} is {type(payload[field])}"

    assert isinstance(payload["outcomes"][0]["initial_price"], float)


# --- pricing bounds, mirroring Numeric(18, 4) -----------------------------
@pytest.mark.parametrize("field", ["liquidity_b", "seed_subsidy"])
def test_a_value_too_large_for_the_column_is_refused(field: str) -> None:
    """Otherwise Postgres raises `numeric field overflow` and the driver error
    escapes as a 500 on a number the admin typed into a form."""
    with pytest.raises(ValidationError):
        MarketDraftRequest(**_payload(**{field: MAX_PRICING_VALUE + 1}))


@pytest.mark.parametrize("field", ["liquidity_b", "seed_subsidy"])
def test_the_largest_storable_value_is_accepted(field: str) -> None:
    """The bound is the column's, not an opinion about a sensible liquidity, so
    the value one step inside it has to pass."""
    parsed = MarketDraftRequest(**_payload(**{field: MAX_PRICING_VALUE}))

    assert getattr(parsed, field) == MAX_PRICING_VALUE


@pytest.mark.parametrize("field", ["liquidity_b", "seed_subsidy"])
def test_a_fifth_decimal_place_is_refused_rather_than_rounded(field: str) -> None:
    """`0.00001` passes `gt=0`, and Numeric(18, 4) then rounds it to `0.0000`.

    The market reloads violating the `b > 0` rule its own validator enforces,
    while the save response reports the value as sent, because that is the
    in-memory object rather than the column. Refusing beats storing a number
    the admin did not ask for and then reporting a different one.
    """
    with pytest.raises(ValidationError):
        MarketDraftRequest(**_payload(**{field: "0.00001"}))


@pytest.mark.parametrize("field", ["liquidity_b", "seed_subsidy"])
def test_four_decimal_places_are_kept(field: str) -> None:
    parsed = MarketDraftRequest(**_payload(**{field: "100.1234"}))

    assert getattr(parsed, field) == Decimal("100.1234")


# --- blank rows are not outcomes ------------------------------------------
def test_an_unnamed_row_is_not_counted_or_priced() -> None:
    """The normal state of a form mid-edit, and it must not move the numbers.

    Counting the blanks would advertise b*ln(4) and 0.25 apiece for a market
    that `service/validation.py` will accept as b*ln(2) and 0.5, so the admin
    would be shown a worst case they are not going to get.
    """
    out = MarketOut.model_validate(_FakeMarket(labels=["Yes", "No", "", "   "]))

    assert out.max_platform_loss == pytest.approx(69.31471805599453)
    assert [o.initial_price for o in out.outcomes] == [
        pytest.approx(0.5),
        pytest.approx(0.5),
        None,
        None,
    ]


def test_rows_that_are_all_blank_price_nothing() -> None:
    out = MarketOut.model_validate(_FakeMarket(labels=["", ""]))

    assert out.max_platform_loss is None
    assert all(o.initial_price is None for o in out.outcomes)


def test_one_named_outcome_beside_a_blank_is_still_too_few() -> None:
    out = MarketOut.model_validate(_FakeMarket(labels=["Yes", ""]))

    assert out.max_platform_loss is None
    assert all(o.initial_price is None for o in out.outcomes)


# --- [3.1] #9 the proposal request ----------------------------------------
# The line this file defends, from the other side. `MarketDraftRequest` above
# accepts an almost-empty body because an autosave writes it; this one has no
# autosave behind it, so a missing winner is a 422 here rather than a rule
# somewhere else. What is still NOT here is completeness — "a URL or a note" is
# service/validation.py's, so that one refusal carries one envelope.
def test_a_proposal_needs_a_winner() -> None:
    with pytest.raises(ValidationError):
        OutcomeProposalRequest(evidence_note="MAS published 1.8% for December.")


def test_a_winner_that_is_not_a_uuid_is_refused() -> None:
    with pytest.raises(ValidationError):
        OutcomeProposalRequest(winning_outcome_id="the first one")


def test_a_proposal_with_no_evidence_still_parses() -> None:
    """Shape, not completeness. Refusing it here would return FastAPI's 422
    envelope for one of the propose form's rules and this service's for the
    rest, leaving Michelle to render two error shapes for one button."""
    parsed = OutcomeProposalRequest(winning_outcome_id=uuid.uuid4())

    assert parsed.evidence_url is None
    assert parsed.evidence_note is None


def test_an_over_long_note_is_refused() -> None:
    """A ceiling, not an opinion about how much explaining a proposal needs."""
    with pytest.raises(ValidationError):
        OutcomeProposalRequest(
            winning_outcome_id=uuid.uuid4(),
            evidence_note="x" * (MAX_PROSE_LENGTH + 1),
        )


def test_an_over_long_url_is_refused() -> None:
    """Mirrors the column, which is varchar(2048) — the practical ceiling
    browsers and proxies agree on."""
    with pytest.raises(ValidationError):
        OutcomeProposalRequest(
            winning_outcome_id=uuid.uuid4(),
            evidence_url="https://mas.gov.sg/" + "x" * MAX_URL_LENGTH,
        )
