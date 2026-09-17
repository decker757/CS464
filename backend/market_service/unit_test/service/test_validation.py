"""The rules that decide whether a market may leave DRAFT. No database, no HTTP.

These are the acceptance criteria of [1.1] #1 stated once, in the layer that
owns them, so [1.3] #3 and [1.4] #4 can reuse the same function rather than
re-deriving it against a route.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from model.entities import Market, MarketOutcome, ResolutionSource
from service.validation import (
    MIN_CLOSE_REASON_LENGTH,
    MIN_OUTCOMES,
    MIN_REJECTION_REASON_LENGTH,
    problems_blocking_close,
    problems_blocking_rejection,
    problems_blocking_submission,
)

NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)


def _market(**overrides: object) -> Market:
    """A market that passes every rule, so each test can break exactly one."""
    defaults: dict[str, object] = {
        "creator_id": uuid.uuid4(),
        "draft_key": uuid.uuid4(),
        "question": "Will Singapore core inflation be below 2% in December 2026?",
        "close_time": NOW + timedelta(days=30),
        "resolution_time": NOW + timedelta(days=45),
        "resolution_criteria": "Resolves YES on the first published MAS print below 2.0%.",
        "liquidity_b": Decimal("100"),
        "seed_subsidy": Decimal("250"),
    }
    defaults.update(overrides)

    outcomes = defaults.pop("outcomes", ["Yes", "No"])
    sources = defaults.pop("sources", ["https://www.mas.gov.sg/statistics"])

    market = Market(**defaults)  # type: ignore[arg-type]
    market.outcomes = [
        MarketOutcome(position=i, label=label) for i, label in enumerate(outcomes)  # type: ignore[union-attr]
    ]
    market.resolution_sources = [
        ResolutionSource(position=i, url=url) for i, url in enumerate(sources)  # type: ignore[union-attr]
    ]
    return market


def _fields(market: Market) -> set[str]:
    return {p.field for p in problems_blocking_submission(market, now=NOW)}


def test_a_complete_market_has_no_problems() -> None:
    assert problems_blocking_submission(_market(), now=NOW) == []


# --- question -------------------------------------------------------------
@pytest.mark.parametrize("question", [None, "", "   ", "rain?"])
def test_the_question_is_required_and_must_be_meaningful(question: str | None) -> None:
    assert "question" in _fields(_market(question=question))


# --- outcomes -------------------------------------------------------------
@pytest.mark.parametrize("outcomes", [[], ["Yes"]])
def test_fewer_than_two_named_outcomes_is_refused(outcomes: list[str]) -> None:
    """One outcome is not a bet, and [1.2] #2's b·ln(n) needs n >= 2."""
    assert "outcomes" in _fields(_market(outcomes=outcomes))


def test_more_than_two_outcomes_is_fine() -> None:
    """Categorical markets are in scope; the ticket says binary OR categorical."""
    market = _market(outcomes=["Below 1%", "1% to 2%", "Above 2%"])

    assert problems_blocking_submission(market, now=NOW) == []


def test_a_blank_outcome_is_named_by_position() -> None:
    """The form needs to know WHICH row to mark, not just that one is wrong."""
    fields = _fields(_market(outcomes=["Yes", "  "]))

    assert "outcomes[1].label" in fields
    # And it does not count towards the minimum.
    assert "outcomes" in fields


@pytest.mark.parametrize("second", ["Yes", "yes", "YES", "  Yes  "])
def test_duplicate_outcomes_are_refused_case_insensitively(second: str) -> None:
    """"Yes" and "yes" are the same bet. A trader picking the wrong one has
    been misled rather than outvoted."""
    assert "outcomes[1].label" in _fields(_market(outcomes=["Yes", second]))


def test_the_minimum_counts_named_outcomes_only() -> None:
    market = _market(outcomes=["Yes", ""])

    problems = problems_blocking_submission(market, now=NOW)

    assert any(f"at least {MIN_OUTCOMES}" in p.message for p in problems)


# --- timing ---------------------------------------------------------------
@pytest.mark.parametrize("field", ["close_time", "resolution_time"])
def test_each_time_is_required(field: str) -> None:
    assert field in _fields(_market(**{field: None}))


def test_a_close_time_in_the_past_is_refused() -> None:
    assert "close_time" in _fields(_market(close_time=NOW - timedelta(minutes=1)))


def test_a_resolution_time_in_the_past_is_refused() -> None:
    market = _market(
        close_time=NOW - timedelta(days=2), resolution_time=NOW - timedelta(days=1)
    )

    assert "resolution_time" in _fields(market)


def test_the_boundary_is_strict() -> None:
    """Exactly now is not the future. A market closing this instant is closed."""
    assert "close_time" in _fields(_market(close_time=NOW))


def test_close_must_come_before_resolution() -> None:
    market = _market(
        close_time=NOW + timedelta(days=45), resolution_time=NOW + timedelta(days=30)
    )

    problems = problems_blocking_submission(market, now=NOW)

    assert any("before the resolution time" in p.message for p in problems)


def test_equal_times_are_refused() -> None:
    """Trading has to stop strictly before the outcome is known."""
    when = NOW + timedelta(days=30)
    market = _market(close_time=when, resolution_time=when)

    assert any(
        "before the resolution time" in p.message
        for p in problems_blocking_submission(market, now=NOW)
    )


def test_naive_timestamps_do_not_crash_the_comparison() -> None:
    """Defence in depth. The request schema rejects naive datetimes, so this
    should be unreachable, but comparing a naive datetime to an aware one
    raises TypeError, and a 500 on the submit button is a far worse failure
    than an assumption written down."""
    market = _market(
        close_time=datetime(2027, 1, 1, 0, 0, 0),
        resolution_time=datetime(2027, 2, 1, 0, 0, 0),
    )

    assert problems_blocking_submission(market, now=NOW) == []


def test_a_naive_timestamp_is_read_as_utc_not_as_local_time() -> None:
    """Pins down which assumption _as_utc makes, so a future reader does not
    have to guess and does not quietly change it."""
    market = _market(
        close_time=datetime(2026, 9, 13, 11, 59, 0),   # one minute before NOW, in UTC
        resolution_time=datetime(2027, 1, 1, 0, 0, 0),
    )

    assert "close_time" in _fields(market)


# --- resolution -----------------------------------------------------------
@pytest.mark.parametrize("criteria", [None, "", "   ", "MAS"])
def test_the_resolution_rule_is_required(criteria: str | None) -> None:
    assert "resolution_criteria" in _fields(_market(resolution_criteria=criteria))


def test_at_least_one_source_is_required() -> None:
    assert "resolution_sources" in _fields(_market(sources=[]))


@pytest.mark.parametrize(
    "url", ["not-a-url", "www.mas.gov.sg", "ftp://mas.gov.sg", "javascript:alert(1)"]
)
def test_a_source_must_be_a_full_http_address(url: str) -> None:
    """A trader has to be able to open it. "www.mas.gov.sg" is not openable,
    and a javascript: URL is an invitation."""
    assert "resolution_sources[0].url" in _fields(_market(sources=[url]))


def test_one_good_source_among_bad_ones_still_leaves_the_bad_ones_flagged() -> None:
    fields = _fields(_market(sources=["https://www.mas.gov.sg/statistics", "nope"]))

    assert "resolution_sources[1].url" in fields
    assert "resolution_sources" not in fields


def test_every_problem_is_reported_not_just_the_first() -> None:
    """The admin should be able to fix the whole form in one pass."""
    market = _market(
        question=None,
        outcomes=[],
        close_time=None,
        resolution_time=None,
        resolution_criteria=None,
        sources=[],
    )

    assert _fields(market) == {
        "question",
        "outcomes",
        "close_time",
        "resolution_time",
        "resolution_criteria",
        "resolution_sources",
    }


# --- pricing [1.2] #2 -----------------------------------------------------
def test_the_liquidity_parameter_is_required() -> None:
    assert "liquidity_b" in _fields(_market(liquidity_b=None))


def test_the_seed_subsidy_is_required() -> None:
    assert "seed_subsidy" in _fields(_market(seed_subsidy=None))


@pytest.mark.parametrize("b", [Decimal("0"), Decimal("-1")])
def test_a_non_positive_liquidity_is_a_problem(b: Decimal) -> None:
    """Unreachable through the API, where the request schema refuses it first.

    Asserted anyway because this function is the single definition of "ready"
    that [1.3] #3 and [1.4] #4 also call, and it is handed an entity rather
    than a request.
    """
    assert "liquidity_b" in _fields(_market(liquidity_b=b))


def test_a_subsidy_below_the_worst_case_does_not_block_submission() -> None:
    """The deliberate omission, and the reason it is a test rather than a gap.

    b = 100 across two outcomes can lose about 69.31, and this market seeds 1.
    The admin is told the number on every save — MarketOut.max_platform_loss —
    so underfunding is an informed choice rather than an accident, and these
    are mock credits. [2.2] #6 flags the related but different quantity,
    realised exposure once traders hold shares, where it can be acted on.

    If this ever should block, the rule belongs in _liquidity_problems and this
    test inverts.
    """
    underfunded = _market(liquidity_b=Decimal("100"), seed_subsidy=Decimal("1"))

    assert problems_blocking_submission(underfunded, now=NOW) == []


# --- the reason for an early close [2.3] #7 -------------------------------
# A pure rule over one string: no market, no clock and no database, which is
# why the boundaries live here rather than in test_closing_early.py. That suite
# asserts the gate calls this at all.
def _close(reason: str) -> list:
    from unit_test.conftest import close_request  # noqa: PLC0415

    return problems_blocking_close(close_request(reason=reason))


def test_a_full_reason_has_no_problems() -> None:
    assert _close("The source retracted its December print.") == []


@pytest.mark.parametrize("reason", ["", "   ", "\n\t "])
def test_a_blank_reason_is_refused(reason: str) -> None:
    """Whitespace is not an explanation, and `reason` is required precisely
    because the audit entry is the only place the explanation will exist."""
    assert [p.field for p in _close(reason)] == ["reason"]


def test_a_too_short_reason_is_refused() -> None:
    """The same argument MIN_EVIDENCE_NOTE_LENGTH makes: "broken" satisfies
    "a reason was given" and documents nothing."""
    assert [p.field for p in _close("broken")] == ["reason"]


def test_the_floor_is_counted_after_stripping() -> None:
    """Otherwise ten spaces around one character passes a rule about length."""
    assert _close("  " + "x" * (MIN_CLOSE_REASON_LENGTH - 1) + "  ") != []


def test_the_boundary_is_not_strict() -> None:
    """Exactly at the floor is enough. A rule stated as "at least ten" that
    refused ten would say so in a message nobody could act on."""
    assert _close("x" * MIN_CLOSE_REASON_LENGTH) == []


def test_only_one_problem_is_ever_reported() -> None:
    """There is one field, so a blank reason is blank rather than also short.
    Two problems on one input would paint the same message twice."""
    assert len(_close("")) == 1


# --- the reason for a rejection [3.2] #10 ---------------------------------
# The same pure rule over one string, with its own floor. It agrees with the
# early close's on a number today and is a different rule: neither should move
# because the other did. test_approving.py asserts the gate calls this at all.
def _rejection(reason: str) -> list:
    from unit_test.conftest import rejection_request  # noqa: PLC0415

    return problems_blocking_rejection(rejection_request(reason=reason))


def test_a_full_rejection_reason_has_no_problems() -> None:
    assert _rejection("The print cited is the headline figure, not core.") == []


@pytest.mark.parametrize("reason", ["", "   ", "\n\t "])
def test_a_blank_rejection_reason_is_refused(reason: str) -> None:
    """The audit entry is the only place the explanation will exist, and the
    proposal it explains is cleared from the market in the same transaction."""
    assert [p.field for p in _rejection(reason)] == ["reason"]


def test_a_too_short_rejection_reason_is_refused() -> None:
    """"wrong" satisfies "a reason was given" and tells the proposer nothing
    about what to propose instead."""
    assert [p.field for p in _rejection("wrong")] == ["reason"]


def test_the_rejection_floor_is_counted_after_stripping() -> None:
    """Otherwise ten spaces around one character passes a rule about length."""
    assert _rejection("  " + "x" * (MIN_REJECTION_REASON_LENGTH - 1) + "  ") != []


def test_the_rejection_boundary_is_not_strict() -> None:
    """Exactly at the floor is enough."""
    assert _rejection("x" * MIN_REJECTION_REASON_LENGTH) == []


def test_only_one_rejection_problem_is_ever_reported() -> None:
    """One field, so a blank reason is blank rather than also short."""
    assert len(_rejection("")) == 1
