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
from typing import Literal

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

# Every free-text field an administrator types into a textarea: `description`,
# `resolution_criteria` and [3.1] #9's `evidence_note`. All three are unbounded
# `Text` columns, so this is here to stop a request carrying a megabyte rather
# than to have an opinion about how much explaining any of them needs — which
# is why one number covers all three. It was two bare literals until the third
# use arrived.
MAX_PROSE_LENGTH = 5000

# Mirrors Numeric(18, 4) on the pricing columns in model/entities.py: fourteen
# digits before the point and four after. Both halves are load-bearing, and
# neither is a domain opinion about a sensible liquidity — they are what the
# column can hold.
#
# Without the ceiling, Postgres raises `numeric field overflow` and the driver
# error escapes as a 500 on a value the admin typed. Without the scale, a fifth
# decimal place is rounded away silently, so `0.00001` is accepted, stored as
# `0.0000`, and reloads as a market that fails its own `b > 0` rule — while the
# response reports the value as sent, because it is the in-memory object.
PRICING_DECIMAL_PLACES = 4
MAX_PRICING_VALUE = Decimal("99999999999999.9999")


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

    # Deliberately narrower than MarketStatus. `open` is a real member of that
    # enum and this field must not accept it: publishing is POST
    # /markets/{id}/publish, a request that carries no terms at all, precisely
    # so that nothing can change a market and expose it to traders in one call.
    # Typed as a Literal rather than guarded by a validator so that /docs shows
    # Michelle the two values she may send, and so a third status arriving with
    # [F-4] #44 does not silently become settable from the form. ADR 0008.
    status: Literal[MarketStatus.DRAFT, MarketStatus.SUBMITTED] = Field(
        default=MarketStatus.DRAFT,
        description=(
            "`draft` for the autosave, which never validates completeness. "
            "`submitted` for the form's submit button, which is refused with "
            "422 unless every rule passes. Publishing is a separate endpoint "
            "and `open` is not accepted here."
        ),
    )

    question: str | None = Field(
        default=None,
        max_length=MAX_QUESTION_LENGTH,
        examples=["Will Singapore's core inflation be below 2% for December 2026?"],
    )
    description: str | None = Field(default=None, max_length=MAX_PROSE_LENGTH)

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
        max_length=MAX_PROSE_LENGTH,
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
        le=MAX_PRICING_VALUE,
        decimal_places=PRICING_DECIMAL_PLACES,
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
        le=MAX_PRICING_VALUE,
        decimal_places=PRICING_DECIMAL_PLACES,
        description=(
            "Mock credits put up to cover the market maker's worst case. "
            "Compare against `max_platform_loss` in the response."
        ),
        examples=[100],
    )


class OutcomeProposalRequest(BaseModel):
    """The body of POST /markets/{id}/propose-outcome. [3.1] #9

    Note how little this has in common with `MarketDraftRequest` above, and why
    none of that file's reasoning carries over. There is no autosave behind
    this request and no half-typed state to protect: an administrator fills in
    a short form and presses a button once. So `winning_outcome_id` is simply
    required, and a body without it is a 422 from FastAPI before this service
    sees it — there is no draft of a proposal for that to lose.

    What is deliberately NOT here is the rest of the rules. "At least one of a
    URL or a note", and "the URL must be one a trader can open", are checked in
    `service/validation.py` and come back as `proposal_incomplete` with a
    `details` array keyed by field. They could have been a `model_validator` and
    an `HttpUrl`, which would be fewer lines — but FastAPI's own 422 has a
    different envelope from this service's, so the propose form would have to
    render two error shapes for one submit button. One shape is worth the
    detour.
    """

    winning_outcome_id: uuid.UUID = Field(
        description=(
            "The `id` of the outcome being proposed as the winner, taken from "
            "this market's own `outcomes` array. An outcome belonging to some "
            "other market is refused."
        ),
    )

    evidence_url: str | None = Field(
        default=None,
        max_length=MAX_URL_LENGTH,
        description=(
            "Where the outcome can be verified. Required unless "
            "`evidence_note` is given, and must be a full http or https "
            "address."
        ),
        examples=["https://www.mas.gov.sg/statistics/cpi-december-2026"],
    )
    evidence_note: str | None = Field(
        default=None,
        max_length=MAX_PROSE_LENGTH,
        description=(
            "Why this outcome won, in prose. Required unless `evidence_url` "
            "is given. Use it when the source is not a link, or when the "
            "reading of it needs explaining."
        ),
        examples=[
            "MAS published core inflation of 1.8% for December 2026 on 23 Jan, "
            "below the 2.0% threshold in the resolution criteria."
        ],
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

    # [1.3] #3. Null until the market is published, and never cleared. Read
    # `status` to decide what a market is; read this to find out when it became
    # that. The two are written in the same transaction and cannot disagree.
    published_at: datetime | None

    # [F-4] #44. When trading was recorded as having stopped.
    #
    # Unlike `published_at`, this one CAN lag the thing it describes, and a
    # client must not read it as "trading stops here". `close_time` is when
    # trading stopped; this is when the background sweeper wrote it down, a
    # few seconds later. Render `close_time` to a trader and keep this for an
    # administrator asking when a market was actually processed.
    closed_at: datetime | None

    # --- the proposed outcome. [3.1] #9 -------------------------------------
    # All null together, or all set together. A non-null `proposed_at` is the
    # cheapest test for "has an outcome been proposed", and it agrees with
    # `status == "pending_resolution"` by construction: the six are written in
    # the one transaction that sets the status.
    #
    # `proposed_outcome_id` names a member of this response's own `outcomes`
    # array, so the frontend renders the winner's label by looking it up there
    # rather than being sent the label twice. The label on the outcome is the
    # only copy, which is what stops the two drifting.
    proposed_outcome_id: uuid.UUID | None
    proposed_by_id: uuid.UUID | None

    # Snapshotted at proposal time, and the only name for this administrator
    # that anybody will ever be able to render: this service cannot resolve an
    # id against `auth.users` (ADR 0003), and only `audit_svc` may read the
    # log. [3.2] #10 compares `proposed_by_id`, never this, because a username
    # can be reissued.
    proposed_by_username: str | None
    proposed_at: datetime | None

    proposal_evidence_url: str | None
    proposal_evidence_note: str | None

    # --- derived, read-only -------------------------------------------------
    # [1.2] #2's second and third acceptance criteria. Both are the q = 0 case
    # of LMSR, which is arithmetic rather than the engine; see
    # core/opening_prices.py and
    # ADR 0005 for why the engine itself is not in this service.
    #
    # Derived on every read rather than stored, so they cannot drift from the
    # `b` and outcome count they come from, and so they appear on all three
    # routes without the controller or the service layer assembling them.

    @property
    def _named_outcomes(self) -> list[OutcomeOut]:
        """The outcomes that actually exist yet.

        A blank row is a row the admin has added and not named, and it is the
        normal state of a form mid-edit. Counting it would price a market that
        `service/validation.py` will refuse, because MIN_OUTCOMES is checked
        against named outcomes: two real outcomes beside two empty rows would
        advertise b*ln(4) and 0.25 apiece for a market that submits as b*ln(2)
        and 0.5. The number on screen has to be the number the admin is going
        to get.
        """
        return [o for o in self.outcomes if (o.label or "").strip()]

    @computed_field(  # type: ignore[prop-decorator]
        description=(
            "The most the platform can lose over this market's life, b*ln(n), "
            "counting only named outcomes. Null until `liquidity_b` is set and "
            "at least two outcomes are named. Show this beside `seed_subsidy`; "
            "do not recompute it in the browser."
        ),
        examples=[69.31471805599453],
    )
    @property
    def max_platform_loss(self) -> float | None:
        return max_platform_loss(self.liquidity_b, len(self._named_outcomes))

    @model_validator(mode="after")
    def _price_the_outcomes(self) -> MarketOut:
        """Give every named outcome its opening price.

        Uniform by definition: before anybody has traded, no outcome is more
        likely than another, so each opens at 1/n. That is the whole of [1.2]
        #2's third criterion, and it depends on the outcome count rather than
        on `b`, which is why it appears as soon as a second outcome is named.

        An unnamed row keeps a null price. It is not an outcome yet, and
        quoting one would be quoting a thing that does not exist.
        """
        named = self._named_outcomes
        price = uniform_initial_price(len(named))
        for outcome in named:
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
