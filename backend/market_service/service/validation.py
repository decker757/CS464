"""When is a market complete enough to move on?

Pure functions with no session, no HTTP and no clock of their own.
`problems_blocking_submission` asks whether the terms are ready to leave DRAFT,
and `problems_blocking_proposal` asks whether a proposed outcome and its
evidence are ready to leave CLOSED ([3.1] #9). They share this module because
they share a shape and a caller's contract — a list of problems addressed to
form fields, every one of them, so the administrator fixes them in one pass —
and because they share the URL check.

Two smaller rules follow them, about the free-text reason an administrator
gives for stopping a market early ([2.3] #7) and for rejecting a proposal
([3.2] #10). They take the request rather than the market, and share one
private check, because a reason is a reason wherever it is typed.

The first was written this way for three reasons:

- [1.3] #3 has to answer the same question at publish time ("publish blocked
  unless all required fields are present") and [1.4] #4 again when it decides
  what may still be edited. Neither should re-derive these rules.
- Injecting `now` is what makes "must be in the future" testable without
  sleeping or freezing the system clock.
- It reports every problem rather than the first, so the form can mark all the
  offending fields at once instead of making the admin find them one save at a
  time.

Nothing here runs on an autosave. A draft is a scratchpad and is allowed to
break every rule below.
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

# A market with one outcome is not a market. Two is binary, more is
# categorical, and [1.2] #2's max platform loss of b·ln(n) needs n ≥ 2 to mean
# anything. This is the floor; the ceiling is MAX_OUTCOMES in model/schemas.py,
# where it is enforced as a shape constraint.
MIN_OUTCOMES = 2

# Enough to rule out "test" and "asdf" reaching a trader, and low enough not to
# argue with a legitimately terse question.
MIN_QUESTION_LENGTH = 10
MIN_CRITERIA_LENGTH = 10

# [3.1] #9. The same floor, for the same reason: a one-word note satisfies
# "evidence was given" and documents nothing, and documenting the decision is
# the entire point of the story. An administrator with nothing to add can give
# a URL instead and leave this empty.
MIN_EVIDENCE_NOTE_LENGTH = 10

# [2.3] #7. And again for the reason an administrator gives for stopping a
# market early, where there is no URL to fall back on: this text is the whole
# of the explanation, and "x" is not one.
#
# Its own constant rather than a share of the one above, exactly as
# MIN_QUESTION_LENGTH and MIN_CRITERIA_LENGTH are of each other. They are
# different rules about different fields that happen to agree on a number
# today, and folding them together would make raising one raise all of them.
MIN_CLOSE_REASON_LENGTH = 10

# [3.2] #10. And once more for the reason a second administrator gives for
# sending a proposed outcome back. The rejection clears the proposal from the
# market, so this text and the audit entry's copy of what it cleared are the
# whole of the record, and the proposer is owed more than "no".
#
# Its own constant for the reason the one above gives, though the field has the
# same name: a close and a rejection are different decisions, and a reason to
# raise the floor on one is not a reason to raise it on the other.
MIN_REJECTION_REASON_LENGTH = 10

# Reuses pydantic's parser rather than a hand-rolled regex, and it already
# restricts the scheme to http and https.
_URL = TypeAdapter(HttpUrl)

# Said to the administrator about a resolution source and about a proposal's
# evidence URL alike. One string, because the two are the same complaint about
# the same kind of value, and two copies would drift into being worded
# differently for no reason a reader could act on.
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
    """Required, and after `now`. The shape both timestamps share.

    Takes an already-UTC value rather than converting one. The caller has to
    normalise both timestamps anyway for the ordering check below, and a second
    `as_utc` here would be a second place that decides what a naive value
    means — which is the naive-versus-aware split that has already caused bugs
    in this repository.

    The two messages stay arguments rather than being generated from `field`,
    because "Close time must be in the future" and "Resolution time must be in
    the future" are what the admin reads next to the offending input, and a
    message assembled from a column name reads like one.
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

    # Reported separately from the two checks above rather than inferred from
    # them. All three can be wrong at once, and each names a different field
    # the admin has to go and fix.
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
    """Required, and greater than zero. The shape both pricing columns share.

    Like `_future_problems`, the messages are arguments: each one explains what
    that particular number does to the market, which is the part an admin
    needs and the part a generated string cannot supply.
    """
    if value is None:
        return [ValidationProblem(field, missing)]
    if value <= 0:
        return [ValidationProblem(field, nonpositive)]
    return []


def _liquidity_problems(market: Market) -> list[ValidationProblem]:
    """[1.2] #2. A market cannot go live without knowing how it is priced.

    Note what is deliberately NOT checked: whether the subsidy actually covers
    `b*ln(n)`. An administrator may knowingly seed a market for less than its
    worst case, and the response tells them the number every time they save, so
    this is an informed choice rather than an accident. [2.2] #6 flags the
    related but different quantity — realised exposure once traders hold shares
    — at the point where it can actually be acted on.

    The `<= 0` branches are unreachable through the API, because
    `MarketDraftRequest` refuses those with a 422 before a row is written. They
    are here because this function is the single definition of "ready" that
    [1.3] #3 and [1.4] #4 also call, and it takes an entity, not a request.
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

    Deliberately does not check the market's status, which is the other half of
    the ticket's first acceptance criterion. That is a fact about the market
    rather than about the proposal, it has its own 409 with its own remedy, and
    an administrator whose market has not closed yet is not being told to fix a
    field. `service/market_service.py::propose_outcome` gates on the status
    before it calls this.

    Nor does it take a clock. Nothing here is time-dependent: the market's own
    close time is what decided whether a proposal is allowed at all, and it was
    already checked.
    """
    return _winner_problems(market, proposal) + _evidence_problems(proposal)


def _winner_problems(
    market: Market, proposal: OutcomeProposalRequest
) -> list[ValidationProblem]:
    """The proposed winner has to be one of this market's own outcomes.

    Checked here rather than left to a foreign key, because a foreign key
    cannot express it: `market_outcomes.id` is unique across every market, so
    an FK would accept another market's "Yes" without complaint. See the note
    on `Market.proposed_outcome_id`.

    Unreachable from a form that renders the market's outcomes as radio
    buttons, which is the only way [FE][3.1] #52 will call this. It is here for
    the request that was not built that way — a copied id, a stale tab, a
    script — because the column it guards is what [3.4] #12 pays out against.
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
    """A URL, or a note, or both — and whichever is given has to be usable.

    The same shape as `_resolution_problems` above, for the same reason: a
    value that was supplied and is unusable gets its own problem naming the
    field it came from, and the "at least one" rule is reported separately, on
    the pair. An administrator who typed a broken URL and nothing else sees
    both, which is what tells them that fixing the URL is enough and they do
    not also have to write a note.
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

    Deliberately does not check the market's status, for the reason
    `problems_blocking_proposal` gives: that is a fact about the market rather
    than about the request, it has its own 409 with its own remedy, and an
    administrator whose market has already stopped is not being told to fix a
    field. `service/market_service.py::close_early` gates on the state before
    it calls this.

    Takes the request rather than the bare string, so that this reads like the
    other two rules in this module and a caller cannot pass it the wrong string.

    Only ever one problem, unlike the other two, because there is only one
    field. The list is the shape the envelope is built from, not a prediction
    that there will be more.
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

    The same rule as `problems_blocking_close`, about a different decision, and
    it leaves the same things out for the same reasons. It does not check the
    market's status, and it does not check who is asking: whether there is a
    proposal to reject, and whether this administrator may reject it, are facts
    about the market and the caller with their own 409 and 403.
    `service/market_service.py::reject_outcome` settles both before it calls
    this, so a proposer sending "x" is told they may not decide rather than
    that their reason is too short.
    """
    return _reason_problems(
        request.reason,
        minimum=MIN_REJECTION_REASON_LENGTH,
        record_of="why this proposal was sent back",
    )


def _reason_problems(
    raw: str, *, minimum: int, record_of: str
) -> list[ValidationProblem]:
    """The one check behind both reason rules above.

    Shared because the two rules are the same check today — trimmed, not blank,
    at least a floor — and a second copy would be a second place for "blank"
    to start meaning something different. The floor is an argument rather than
    a constant here, so the two rules still move independently. Addressed to
    `reason` because that is the field on both requests; a third caller whose
    field is named otherwise is a sign this has stopped being the same rule.
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
