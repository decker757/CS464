"""Request and response contracts.

These generate the OpenAPI schema at /docs, which is the contract Michelle's
form codes against for [FE][1.1] #45.

The governing rule in this file: validation here is about SHAPE, not about
completeness. A draft is allowed to be empty, half-typed and self-contradictory,
because it is written by a three-second idle timer rather than by someone
pressing save. Anything that asks "is this market ready" belongs in
`service/validation.py` and runs only at submission.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from core.opening_prices import max_platform_loss, uniform_initial_price
from model.entities import MarketStatus

# Shape ceilings, not domain rules, so they live here rather than in config:
# they do not vary between a laptop and production. The floor of two outcomes
# IS a domain rule and lives in service/validation.py, because a draft is
# allowed to sit below it.
MAX_OUTCOMES = 10
MAX_RESOLUTION_SOURCES = 10
MAX_LABEL_LENGTH = 120
MAX_QUESTION_LENGTH = 500
MAX_URL_LENGTH = 2048


class OutcomeIn(BaseModel):
    """Blank is allowed. The admin may have added a row before naming it."""

    label: str = Field(default="", max_length=MAX_LABEL_LENGTH, examples=["Yes"])


class ResolutionSourceIn(BaseModel):
    """Not typed as HttpUrl, deliberately.

    HttpUrl would reject "htt" and fail the autosave that fires while the admin
    is still typing the address. The URL is checked at submission instead.
    """

    url: str = Field(
        default="", max_length=MAX_URL_LENGTH, examples=["https://www.mas.gov.sg/statistics"]
    )
    label: str | None = Field(
        default=None, max_length=MAX_LABEL_LENGTH, examples=["MAS official statistics"]
    )


class MarketDraftRequest(BaseModel):
    """The body of POST /markets, for both an autosave and a submission.

    Every timestamp is `AwareDatetime`, so "2027-01-01T00:00:00" with no offset
    is a 422 rather than a silent guess. Guessing UTC would put a Singapore
    admin's close time eight hours out, and a market that closes at the wrong
    hour is a market that settles on the wrong facts.
    """

    # Chosen by the browser when the form opens, identical on every autosave
    # for that form. This is what makes the endpoint idempotent.
    draft_key: uuid.UUID = Field(
        description=(
            "Client-generated identity for this form session. Generate one UUID "
            "when the create form opens and send the same value on every save; "
            "repeated calls update one market rather than creating many."
        ),
    )

    status: MarketStatus = Field(
        default=MarketStatus.DRAFT,
        description=(
            "`draft` for the autosave, which never validates completeness. "
            "`submitted` for the form's submit button, which is refused with "
            "422 unless every rule passes."
        ),
    )

    question: str | None = Field(
        default=None,
        max_length=MAX_QUESTION_LENGTH,
        examples=["Will Singapore's core inflation be below 2% for December 2026?"],
    )
    description: str | None = Field(default=None, max_length=5000)

    outcomes: list[OutcomeIn] = Field(
        default_factory=list,
        max_length=MAX_OUTCOMES,
        examples=[[{"label": "Yes"}, {"label": "No"}]],
    )

    close_time: AwareDatetime | None = Field(
        default=None,
        description="When trading stops. Must carry a UTC offset.",
        examples=["2027-01-05T12:00:00Z"],
    )
    resolution_time: AwareDatetime | None = Field(
        default=None,
        description="When the outcome is expected to be known. Must carry a UTC offset.",
        examples=["2027-01-20T12:00:00Z"],
    )

    resolution_criteria: str | None = Field(
        default=None,
        max_length=5000,
        description="The rule that turns the sources below into a winning outcome.",
        examples=[
            "Resolves YES if the MAS core inflation print for Dec 2026, as first "
            "published, is strictly below 2.0%. A later revision does not change it."
        ],
    )
    resolution_sources: list[ResolutionSourceIn] = Field(
        default_factory=list, max_length=MAX_RESOLUTION_SOURCES
    )

    # [1.2] #2. Both optional here, like every other term, because a draft is
    # allowed to be incomplete. Their absence is a submission rule and lives in
    # service/validation.py.
    #
    # `gt=0` is shape rather than completeness, so it belongs here: a negative
    # or zero `b` is not a half-finished thought, it is a value no stage of the
    # form should ever hold. A market priced at b = 0 has no liquidity at all.
    liquidity_b: Decimal | None = Field(
        default=None,
        gt=0,
        description=(
            "LMSR liquidity. Higher means prices move less per trade and the "
            "platform's worst-case loss is larger. Omit to accept the "
            "server's configured default."
        ),
        examples=[100],
    )
    seed_subsidy: Decimal | None = Field(
        default=None,
        gt=0,
        description=(
            "Mock credits put up to cover the market maker's worst case. "
            "Compare against `max_platform_loss` in the response."
        ),
        examples=[100],
    )


class OutcomeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    label: str

    # Filled in by MarketOut, not computed here: an outcome on its own does not
    # know how many siblings it has, and 1/n needs n. Null until there are at
    # least two outcomes to price.
    initial_price: float | None = None


class ResolutionSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    url: str
    label: str | None


class _UtcTimestamps(BaseModel):
    """Guarantees every timestamp this service emits carries an explicit offset.

    Same guard as the auth service's UserOut. A driver handing back a naive
    datetime would otherwise make one market serialise with a trailing Z and
    another without, leaving the frontend to special-case which. Shared by the
    detail and list projections so the two cannot drift.
    """

    model_config = ConfigDict(from_attributes=True)

    @field_validator("*")
    @classmethod
    def _always_utc(cls, v: object) -> object:
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v


class MarketOut(_UtcTimestamps):
    id: uuid.UUID
    draft_key: uuid.UUID
    creator_id: uuid.UUID
    status: MarketStatus

    question: str | None
    description: str | None
    outcomes: list[OutcomeOut]

    close_time: datetime | None
    resolution_time: datetime | None

    resolution_criteria: str | None
    resolution_sources: list[ResolutionSourceOut]

    # Floats on the way out, though they are Numeric in the database and
    # Decimal on the way in. Pydantic serialises a Decimal as a JSON *string*,
    # which would hand the form `"100"` from a fresh save and `"100.0000"` once
    # the same row came back from Postgres, and would sit a string next to
    # `max_platform_loss`, a number, while the form is meant to compare the two.
    # `"250" + 10` is `"25010"` in a browser. Exactness is kept where it
    # matters, in the column; the wire carries a number.
    liquidity_b: float | None
    seed_subsidy: float | None

    created_at: datetime
    updated_at: datetime
    submitted_at: datetime | None

    # --- derived, read-only -------------------------------------------------
    # [1.2] #2's second and third acceptance criteria. Both are the q = 0 case
    # of LMSR, which is arithmetic rather than the engine; see
    # core/opening_prices.py and
    # ADR 0005 for why the engine itself is not in this service.
    #
    # Derived on every read rather than stored, so they cannot drift from the
    # `b` and outcome count they come from, and so they appear on all three
    # routes without the controller or the service layer assembling them.

    @computed_field(  # type: ignore[prop-decorator]
        description=(
            "The most the platform can lose over this market's life, b*ln(n). "
            "Null until `liquidity_b` is set and there are at least two "
            "outcomes. Show this beside `seed_subsidy`; do not recompute it in "
            "the browser."
        ),
        examples=[69.31471805599453],
    )
    @property
    def max_platform_loss(self) -> float | None:
        return max_platform_loss(self.liquidity_b, len(self.outcomes))

    @model_validator(mode="after")
    def _price_the_outcomes(self) -> MarketOut:
        """Give every outcome its opening price.

        Uniform by definition: before anybody has traded, no outcome is more
        likely than another, so each opens at 1/n. That is the whole of [1.2]
        #2's third criterion, and it depends on the outcome count rather than
        on `b`, which is why it appears as soon as a second outcome is typed.
        """
        price = uniform_initial_price(len(self.outcomes))
        for outcome in self.outcomes:
            outcome.initial_price = price
        return self


class ValidationProblemOut(BaseModel):
    """One reason this draft could not be submitted, addressed to a form field."""

    field: str = Field(examples=["close_time"])
    message: str = Field(examples=["Close time must be in the future."])


class MarketSaveResponse(BaseModel):
    """What both an autosave and a submission return.

    `blocking_submission` is the part worth noticing. An autosave never fails
    for being incomplete, but it still reports exactly what is missing, so the
    form can show the admin how far off they are without a second round trip
    and without duplicating these rules in the browser. On a successful
    submission it is empty by definition.
    """

    market: MarketOut
    blocking_submission: list[ValidationProblemOut]


class MarketSummaryOut(_UtcTimestamps):
    """The list projection. Deliberately not the whole market."""

    id: uuid.UUID
    draft_key: uuid.UUID
    status: MarketStatus
    question: str | None
    close_time: datetime | None
    updated_at: datetime


class MarketListResponse(BaseModel):
    markets: list[MarketSummaryOut]
