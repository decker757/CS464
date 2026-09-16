"""When is a market complete enough to submit?

One pure function over a Market entity, with no session, no HTTP and no clock
of its own. Written this way for three reasons:

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

from pydantic import HttpUrl, TypeAdapter, ValidationError

from core.clock import as_utc
from core.errors import ValidationProblem
from model.entities import Market

# A market with one outcome is not a market. Two is binary, more is
# categorical, and [1.2] #2's max platform loss of b·ln(n) needs n ≥ 2 to mean
# anything. This is the floor; the ceiling is MAX_OUTCOMES in model/schemas.py,
# where it is enforced as a shape constraint.
MIN_OUTCOMES = 2

# Enough to rule out "test" and "asdf" reaching a trader, and low enough not to
# argue with a legitimately terse question.
MIN_QUESTION_LENGTH = 10
MIN_CRITERIA_LENGTH = 10

# Reuses pydantic's parser rather than a hand-rolled regex, and it already
# restricts the scheme to http and https.
_URL = TypeAdapter(HttpUrl)


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


def _timing_problems(market: Market, now: datetime) -> list[ValidationProblem]:
    problems: list[ValidationProblem] = []
    close = as_utc(market.close_time) if market.close_time else None
    resolve = as_utc(market.resolution_time) if market.resolution_time else None

    if close is None:
        problems.append(ValidationProblem("close_time", "A close time is required."))
    elif close <= now:
        problems.append(ValidationProblem("close_time", "Close time must be in the future."))

    if resolve is None:
        problems.append(
            ValidationProblem("resolution_time", "A resolution time is required.")
        )
    elif resolve <= now:
        problems.append(
            ValidationProblem("resolution_time", "Resolution time must be in the future.")
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
                    f"resolution_sources[{position}].url",
                    "Enter a full http or https address a trader can open.",
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
    problems: list[ValidationProblem] = []

    if market.liquidity_b is None:
        problems.append(
            ValidationProblem(
                "liquidity_b",
                "A liquidity parameter is required. It decides how far each "
                "trade moves the price.",
            )
        )
    elif market.liquidity_b <= 0:
        problems.append(
            ValidationProblem(
                "liquidity_b",
                "The liquidity parameter must be greater than zero. At zero "
                "the market has no liquidity and cannot be priced.",
            )
        )

    if market.seed_subsidy is None:
        problems.append(
            ValidationProblem(
                "seed_subsidy",
                "A seed subsidy is required. It is the credits the platform "
                "puts up to cover the market maker's losses.",
            )
        )
    elif market.seed_subsidy <= 0:
        problems.append(
            ValidationProblem(
                "seed_subsidy",
                "The seed subsidy must be greater than zero.",
            )
        )

    return problems
