"""Whether a market, a proposed outcome or a reason is complete enough to act on.

Pure functions: no session, no HTTP, and `now` is injected. Each returns every
problem, addressed to a form field, so the administrator fixes them in one
pass. Publishing re-runs the submission rules rather than restating them (ADR
0004, ADR 0008). Nothing here runs on an autosave; a draft may break every rule.
[1.1] #1, [2.3] #7, [3.1] #9, [3.2] #10.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import HttpUrl, TypeAdapter, ValidationError

from core.clock import as_utc
from core.errors import ValidationProblem
from model.entities import Market
from model.schemas import (
    MarketCloseRequest,
    OutcomeProposalRequest,
    OutcomeRejectionRequest,
)

# Below two outcomes it is not a market, and b·ln(n) means nothing. The
# ceiling, MAX_OUTCOMES, is a shape rule in model/schemas.py.
MIN_OUTCOMES = 2

# Every floor below rules out "test", "x" or a one-word note reaching a trader
# or the log. They are separate constants that agree today: different rules
# about different fields, so raising one must not raise the others. ADR 0014,
# ADR 0016.
MIN_QUESTION_LENGTH = 10
MIN_CRITERIA_LENGTH = 10
MIN_EVIDENCE_NOTE_LENGTH = 10  # [3.1] #9
MIN_CLOSE_REASON_LENGTH = 10  # [2.3] #7
MIN_REJECTION_REASON_LENGTH = 10  # [3.2] #10

# pydantic's parser, which already restricts the scheme to http and https.
_URL = TypeAdapter(HttpUrl)

# One wording for a resolution source and an evidence URL: the same complaint.
_UNREACHABLE_URL = "Enter a full http or https address a trader can open."


def _is_reachable_url(raw: str) -> bool:
    try:
        _URL.validate_python(raw)
    except ValidationError:
        return False
    return True


def problems_blocking_submission(
    market: Market, *, now: datetime | None = None
) -> list[ValidationProblem]:
    """Every reason `market` cannot leave DRAFT. Empty means it can."""
    now = now or datetime.now(UTC)
    problems: list[ValidationProblem] = []

    problems += _question_problems(market)
    problems += _outcome_problems(market)
    problems += _timing_problems(market, now)
    problems += _resolution_problems(market)
    problems += _liquidity_problems(market)

    return problems


def _question_problems(market: Market) -> list[ValidationProblem]:
    question = (market.question or "").strip()
    if not question:
        return [ValidationProblem("question", "A question is required.")]
    if len(question) < MIN_QUESTION_LENGTH:
        return [
            ValidationProblem(
                "question",
                f"The question must be at least {MIN_QUESTION_LENGTH} characters, "
                "so traders know precisely what they are betting on.",
            )
        ]
    return []


def _outcome_problems(market: Market) -> list[ValidationProblem]:
    problems: list[ValidationProblem] = []
    labels = [(o.position, (o.label or "").strip()) for o in market.outcomes]

    for position, label in labels:
        if not label:
            problems.append(
                ValidationProblem(f"outcomes[{position}].label", "An outcome cannot be blank.")
            )

    named = [label for _, label in labels if label]
    if len(named) < MIN_OUTCOMES:
        problems.append(
            ValidationProblem(
                "outcomes",
                f"A market needs at least {MIN_OUTCOMES} named outcomes.",
            )
        )

    # Case-insensitive, because "Yes" and "yes" are the same bet and a trader
    # who picks the wrong one has been misled rather than outvoted.
    seen: set[str] = set()
    for position, label in labels:
        folded = label.casefold()
        if not folded:
            continue
        if folded in seen:
            problems.append(
                ValidationProblem(
                    f"outcomes[{position}].label",
                    f"Outcome '{label}' is listed more than once.",
                )
            )
        seen.add(folded)

    return problems


def _future_problems(
    field: str, moment: datetime | None, now: datetime, *, missing: str, past: str
) -> list[ValidationProblem]:
    """Required, and after `now`.

    Takes an already-UTC value, so the caller is the one place that decides
    what a naive timestamp means. The messages are arguments because the admin
    reads them beside the input.
    """
    if moment is None:
        return [ValidationProblem(field, missing)]
    if moment <= now:
        return [ValidationProblem(field, past)]
    return []


def _timing_problems(market: Market, now: datetime) -> list[ValidationProblem]:
    problems: list[ValidationProblem] = []
    close = as_utc(market.close_time) if market.close_time else None
    resolve = as_utc(market.resolution_time) if market.resolution_time else None

    problems += _future_problems(
        "close_time",
        close,
        now,
        missing="A close time is required.",
        past="Close time must be in the future.",
    )
    problems += _future_problems(
        "resolution_time",
        resolve,
        now,
        missing="A resolution time is required.",
        past="Resolution time must be in the future.",
    )

    # Reported separately: all three can be wrong at once.
    if close is not None and resolve is not None and close >= resolve:
        problems.append(
            ValidationProblem(
                "close_time",
                "Close time must be before the resolution time. Trading has to "
                "stop before the outcome is known, or someone can bet on a "
                "result they already know.",
            )
        )

    return problems


def _resolution_problems(market: Market) -> list[ValidationProblem]:
    problems: list[ValidationProblem] = []

    criteria = (market.resolution_criteria or "").strip()
    if not criteria:
        problems.append(
            ValidationProblem(
                "resolution_criteria",
                "Describe the rule that decides the winning outcome. A source "
                "on its own says where to look, not what settles it.",
            )
        )
    elif len(criteria) < MIN_CRITERIA_LENGTH:
        problems.append(
            ValidationProblem(
                "resolution_criteria",
                f"The resolution rule must be at least {MIN_CRITERIA_LENGTH} characters.",
            )
        )

    sources = [(s.position, (s.url or "").strip()) for s in market.resolution_sources]
    usable = 0
    for position, url in sources:
        if not url:
            problems.append(
                ValidationProblem(
                    f"resolution_sources[{position}].url", "A source URL cannot be blank."
                )
            )
        elif not _is_reachable_url(url):
            problems.append(
                ValidationProblem(
                    f"resolution_sources[{position}].url", _UNREACHABLE_URL
                )
            )
        else:
            usable += 1

    if usable == 0:
        problems.append(
            ValidationProblem(
                "resolution_sources",
                "At least one resolution source is required, so traders can "
                "verify the outcome themselves.",
            )
        )

    return problems


def _positive_problems(
    field: str, value: Decimal | None, *, missing: str, nonpositive: str
) -> list[ValidationProblem]:
    """Required, and greater than zero. Messages are arguments, as in `_future_problems`."""
    if value is None:
        return [ValidationProblem(field, missing)]
    if value <= 0:
        return [ValidationProblem(field, nonpositive)]
    return []


def _liquidity_problems(market: Market) -> list[ValidationProblem]:
    """A market cannot go live without knowing how it is priced. [1.2] #2.

    Deliberately does not check that the subsidy covers b·ln(n): an admin may
    knowingly under-seed, and sees that number on every save. The `<= 0`
    branches are unreachable through the API and kept because this checks an
    entity, not a request.
    """
    return _positive_problems(
        "liquidity_b",
        market.liquidity_b,
        missing="A liquidity parameter is required. It decides how far each "
        "trade moves the price.",
        nonpositive="The liquidity parameter must be greater than zero. At zero "
        "the market has no liquidity and cannot be priced.",
    ) + _positive_problems(
        "seed_subsidy",
        market.seed_subsidy,
        missing="A seed subsidy is required. It is the credits the platform "
        "puts up to cover the market maker's losses.",
        nonpositive="The seed subsidy must be greater than zero.",
    )


# --- proposing an outcome. [3.1] #9 ---------------------------------------
def problems_blocking_proposal(
    market: Market, proposal: OutcomeProposalRequest
) -> list[ValidationProblem]:
    """Every reason this outcome cannot be proposed for `market`. Empty means it can.

    Does not check the market's status: that is `propose_outcome`'s 409, not a
    field to fix. Takes no clock, because nothing here depends on time.
    """
    return _winner_problems(market, proposal) + _evidence_problems(proposal)


def _winner_problems(
    market: Market, proposal: OutcomeProposalRequest
) -> list[ValidationProblem]:
    """The proposed winner has to be one of this market's own outcomes.

    A foreign key cannot express this (ADR 0013). The form cannot send another
    market's outcome; a copied id, a stale tab or a script can, and [3.4] #12
    pays out against this column.
    """
    if any(outcome.id == proposal.winning_outcome_id for outcome in market.outcomes):
        return []
    return [
        ValidationProblem(
            "winning_outcome_id",
            "Choose one of this market's own outcomes as the winner.",
        )
    ]


def _evidence_problems(proposal: OutcomeProposalRequest) -> list[ValidationProblem]:
    """A URL, or a note, or both, and whichever is given has to be usable.

    An unusable value and the "at least one" rule are reported separately, so
    an admin with only a broken URL learns that fixing it is enough.
    """
    problems: list[ValidationProblem] = []
    url = (proposal.evidence_url or "").strip()
    note = (proposal.evidence_note or "").strip()

    usable = 0

    if url:
        if _is_reachable_url(url):
            usable += 1
        else:
            problems.append(ValidationProblem("evidence_url", _UNREACHABLE_URL))

    if note:
        if len(note) >= MIN_EVIDENCE_NOTE_LENGTH:
            usable += 1
        else:
            problems.append(
                ValidationProblem(
                    "evidence_note",
                    f"The note must be at least {MIN_EVIDENCE_NOTE_LENGTH} characters, "
                    "or leave it empty and give a URL instead.",
                )
            )

    if usable == 0:
        problems.append(
            ValidationProblem(
                "evidence",
                "Give a source URL or a written note, so the decision can be "
                "checked by somebody who was not in the room.",
            )
        )

    return problems


# --- closing a market early. [2.3] #7 -------------------------------------
def problems_blocking_close(request: MarketCloseRequest) -> list[ValidationProblem]:
    """Every reason this market cannot be closed early. Empty means it can.

    Checks only the reason; the market's state is `close_early`'s 409. Takes
    the request, not the string, so a caller cannot pass the wrong string.
    """
    return _reason_problems(
        request.reason,
        minimum=MIN_CLOSE_REASON_LENGTH,
        record_of="why this market was stopped before its closing time",
    )


# --- rejecting a proposed outcome. [3.2] #10 ------------------------------
def problems_blocking_rejection(
    request: OutcomeRejectionRequest,
) -> list[ValidationProblem]:
    """Every reason this proposal cannot be rejected. Empty means it can.

    Checks only the reason. State and identity are `reject_outcome`'s 409 and
    403, and are checked first. ADR 0016.
    """
    return _reason_problems(
        request.reason,
        minimum=MIN_REJECTION_REASON_LENGTH,
        record_of="why this proposal was sent back",
    )


def _reason_problems(
    raw: str, *, minimum: int, record_of: str
) -> list[ValidationProblem]:
    """The check behind both reason rules: trimmed, not blank, at least `minimum`.

    The floor is an argument so the two rules still move independently.
    """
    reason = raw.strip()

    if not reason:
        return [
            ValidationProblem(
                "reason",
                f"A reason is required. It is the only record of {record_of}.",
            )
        ]

    if len(reason) < minimum:
        return [
            ValidationProblem(
                "reason",
                f"The reason must be at least {minimum} characters, "
                "so that somebody reading the log later can tell what happened.",
            )
        ]

    return []
