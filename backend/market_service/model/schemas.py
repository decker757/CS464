"""Request and response contracts; they generate the OpenAPI schema at /docs.

Validation here is about shape, never completeness: a draft written by the
autosave may be empty or half-typed. "Is this market ready" belongs in
`service/validation.py`. ADR 0004.
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
from model.entities import DECIDED_STATUSES, TRADER_FACING_STATUS, MarketStatus

# Shape ceilings, not domain rules. The floor of two outcomes is a domain rule
# and lives in service/validation.py, because a draft may sit below it.
MAX_OUTCOMES = 10
MAX_RESOLUTION_SOURCES = 10
MAX_LABEL_LENGTH = 120
MAX_QUESTION_LENGTH = 500
MAX_URL_LENGTH = 2048

# Every free-text textarea field. They land in unbounded `Text` columns, so this
# only stops a request carrying a megabyte; hence one number for all of them.
MAX_PROSE_LENGTH = 5000

# Mirrors Numeric(18, 4) on the pricing columns. Without the ceiling, Postgres
# overflows and the admin gets a 500. Without the scale, `0.00001` is silently
# stored as `0.0000`, a market that then fails its own `b > 0` rule.
PRICING_DECIMAL_PLACES = 4
MAX_PRICING_VALUE = Decimal("99999999999999.9999")

# The precondition both decisions carry, described once. Required and nullable:
# null matches only a proposal made before ids existed. ADR 0016.
_PROPOSAL_ID_FIELD_DESCRIPTION = (
    "The `proposal_id` of the proposal the administrator reviewed, exactly as "
    "it was read — from `GET /markets/{id}`, or from the "
    "`market.outcome_proposed` audit entry's `context`. Send `null` only for "
    "a proposal that has none, which is one made before proposal ids existed. "
    "If the market has since had that proposal rejected and a new one made, "
    "the decision is refused as `409 proposal_superseded` rather than applied "
    "to a proposal nobody here has read."
)


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

    draft_key: uuid.UUID = Field(
        description=(
            "Client-generated identity for this form session. Generate one UUID "
            "when the create form opens and send the same value on every save; "
            "repeated calls update one market rather than creating many."
        ),
    )

    # Narrower than MarketStatus on purpose: `open` must not be accepted, or
    # one request could change a market and publish it. A Literal, so /docs
    # lists the two values and a new status is never settable by accident.
    # ADR 0008.
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

    # [1.2] #2. Optional like every term; their absence is a submission rule.
    # `gt=0` is shape, not completeness: no stage of the form should hold a
    # zero or negative value.
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


class MarketCloseRequest(BaseModel):
    """The body of POST /markets/{id}/close. [2.3] #7

    One field, and it is required. A market that stopped early with no
    explanation is the failure this story exists to prevent — traders hold
    positions in it and an administrator will have to settle it — and the audit
    entry this produces is the only place that explanation is ever kept.

    Required *here* rather than in `service/validation.py`, which means a body
    with no `reason` key at all comes back as FastAPI's own 422 rather than
    this service's envelope. That is the same split `OutcomeProposalRequest`
    makes for `winning_outcome_id` and for the same reason: there is no autosave
    behind this modal and no half-typed state for a refusal to lose. What the
    field *says* is a rule about content rather than shape, so the length floor
    lives with the other content rules and comes back as `close_incomplete`,
    keyed by field, in the one envelope the modal already renders.
    """

    reason: str = Field(
        max_length=MAX_PROSE_LENGTH,
        description=(
            "Why this market is being stopped before its closing time. At "
            "least 10 characters. Recorded in the audit log against the "
            "administrator who closed it, and kept nowhere else — it is not a "
            "field on the market."
        ),
        examples=[
            "The resolution source retracted its December print, so this "
            "question can no longer be settled as written."
        ],
    )


class OutcomeApprovalRequest(BaseModel):
    """The body of POST /markets/{id}/approve-outcome. [3.2] #10

    Only which proposal is being approved, and nothing about it. The approver
    agrees with a winner and evidence already on the row, so there is nothing
    for them to supply — but there has to be a way to say *which* winner and
    evidence, because the market id alone does not. A proposal can be rejected
    and replaced while the approver is still reading it, and an approval keyed
    by the market would then land on the replacement. ADR 0016.

    Required rather than optional, because an optional precondition is one a
    client can forget, and the request it lets through is exactly the stale
    one this exists to refuse. Nullable, for the proposals that predate ids —
    see `_PROPOSAL_ID_FIELD_DESCRIPTION` for why a null cannot match anything
    else. A key that is absent is still FastAPI's 422.
    """

    proposal_id: uuid.UUID | None = Field(description=_PROPOSAL_ID_FIELD_DESCRIPTION)


class OutcomeRejectionRequest(BaseModel):
    """The body of POST /markets/{id}/reject-outcome. [3.2] #10

    `proposal_id` is the same precondition `OutcomeApprovalRequest` carries, for
    the same reason: a rejection written about one proposal must not clear its
    replacement.

    `reason` is split exactly as `MarketCloseRequest` splits it: the key's
    presence is shape and is checked here, so a body without it is FastAPI's
    own 422; what it says is content, so a blank or one-word reason parses and
    is refused by `service/validation.py` as `rejection_incomplete`, keyed by
    field, in the envelope the modal already renders.

    An approval carries no reason, and the asymmetry is the point. An approver
    agrees with evidence that is already on the row. A rejecter is overruling
    it, and the proposal it cleared is gone from the market the moment this
    commits — so the reason, in the audit log beside a copy of that proposal,
    is the only explanation anybody will ever have of why it was sent back.
    ADR 0016.
    """

    proposal_id: uuid.UUID | None = Field(description=_PROPOSAL_ID_FIELD_DESCRIPTION)

    reason: str = Field(
        max_length=MAX_PROSE_LENGTH,
        description=(
            "Why this proposed outcome is being sent back. At least 10 "
            "characters. Recorded in the audit log against the administrator "
            "who rejected it, and kept nowhere else — it is not a field on the "
            "market, and the proposal it rejects is cleared from the market."
        ),
        examples=[
            "The MAS print cited is the headline figure, not core inflation; the "
            "core figure for December 2026 has not been published yet."
        ],
    )


class OutcomeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    label: str

    # Filled in by MarketOut, which knows n. Null below two named outcomes.
    initial_price: float | None = None


class ResolutionSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    url: str
    label: str | None


class _UtcTimestamps(BaseModel):
    """Guarantees every timestamp this service emits carries an explicit offset.

    Twin of the auth service's UserOut guard.
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

    # Floats on the wire, deliberately: the form compares these with
    # `max_platform_loss`, and `"250" + 10` is `"25010"` in a browser. The
    # public projection sends strings instead. D-021.
    liquidity_b: float | None
    seed_subsidy: float | None

    created_at: datetime
    updated_at: datetime
    submitted_at: datetime | None

    # [1.3] #3. Null until published, and never cleared.
    published_at: datetime | None

    # [F-4] #44. When the sweep recorded the close, which lags `close_time`.
    # Show traders `close_time`, not this. ADR 0011.
    closed_at: datetime | None

    # --- the proposed outcome. [3.1] #9 -------------------------------------
    # All null or all set, together with `pending_resolution` or `approved`,
    # with one exception: a proposal pending since before the `proposal_id`
    # column existed ([3.2] #10) has it null, and is decided by sending
    # the null back. `proposal_id` is what approve and reject send back: read
    # it from the response the reviewer is looking at, never a later fetch
    # (ADR 0016).
    # `proposed_outcome_id` names one of this response's own `outcomes`.
    proposal_id: uuid.UUID | None
    proposed_outcome_id: uuid.UUID | None
    proposed_by_id: uuid.UUID | None

    # Snapshotted at proposal time. Compare `proposed_by_id`, never this; a
    # username can be reissued. ADR 0013.
    proposed_by_username: str | None
    proposed_at: datetime | None

    proposal_evidence_url: str | None
    proposal_evidence_note: str | None

    # --- the approval. [3.2] #10 --------------------------------------------
    # All null unless `approved`, beside the still-filled proposal fields.
    # `approved_by_id` never equals `proposed_by_id`. A rejection leaves no
    # trace here; it is in the audit log. ADR 0016.
    approved_by_id: uuid.UUID | None
    approved_by_username: str | None
    approved_at: datetime | None

    # --- derived, read-only -------------------------------------------------
    # [1.2] #2. The q = 0 prices from core/opening_prices.py, derived on every
    # read so they cannot drift from `b` and the outcome count.

    @property
    def _named_outcomes(self) -> list[OutcomeOut]:
        """The outcomes the admin has named.

        Blank rows are skipped, or two named outcomes beside two blank rows
        would advertise b·ln(4) for a market that submits as b·ln(2).
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
        """Give every named outcome its opening price, 1/n. Unnamed rows stay null."""
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


# --- the trader-facing projection. [BE][X] #62 -----------------------------
# A second pair beside `MarketOut` and `MarketSummaryOut`, not a flag on them:
# the ledger needs `liquidity_b` exact where the form needs a number (D-021).
# `creator_id`, `draft_key` and `initial_price` are deliberately absent (D-019).
#
# Do not derive `status` in a validator here. FastAPI re-validates the response
# without the request's clock; `service/browsing.py` derives it before these
# are built, and they carry plain fields. D-025, D-027.


class PublicOutcomeOut(BaseModel):
    """An outcome as a trader sees it. No `initial_price` — see D-019 above."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    label: str


class PublicMarketOut(_UtcTimestamps):
    """The trader-facing detail read. [X-3] #36.

    `liquidity_b` and `seed_subsidy` are left typed as `Decimal`, which
    Pydantic serialises as a JSON string by default — the opposite of
    `MarketOut`'s explicit `float`, and the point of D-016: this is the
    endpoint the ledger reads to snapshot `b` before it can price a market
    (ADR 0005's publish-time handoff), and a JSON number would put an IEEE
    double under every price the platform ever quotes.

    No prices. `q` lives with the ledger (ADR 0005), and the authoritative
    read is the snapshot endpoint in `docs/api/realtime-service.md`, landing
    with [F-3] #43 and [T-2] #22.
    """

    id: uuid.UUID

    # The derived status `get_published` stamps, never the stored column. One
    # status on this schema, so the admin's cannot be read by accident. D-027.
    status: MarketStatus = Field(validation_alias=TRADER_FACING_STATUS)

    question: str | None
    description: str | None
    outcomes: list[PublicOutcomeOut]

    close_time: datetime | None
    resolution_time: datetime | None

    resolution_criteria: str | None
    resolution_sources: list[ResolutionSourceOut]

    liquidity_b: Decimal | None
    seed_subsidy: Decimal | None

    published_at: datetime | None

    # [X-3] #36. The winner, as an id into `outcomes`. Null until a second
    # administrator has agreed. D-026.
    proposed_outcome_id: uuid.UUID | None

    @model_validator(mode="after")
    def _hide_an_undecided_proposal(self) -> PublicMarketOut:
        """Hide a proposed winner until it is decided. D-026.

        A pending proposal can still be rejected (ADR 0016), and traders must
        not be shown a result that is then reversed. `self.status` is the
        derived status, which is safe because the derivation only turns OPEN
        into CLOSED.
        """
        if self.status not in DECIDED_STATUSES:
            self.proposed_outcome_id = None
        return self


class PublicMarketSummaryOut(_UtcTimestamps):
    """The trader-facing list projection. [X-1] #34. Deliberately narrow.

    Prices are not here either, for the same reason they are not on the
    detail read: a card renders them from the snapshot endpoint once [F-3]
    #43 and [T-2] #22 land. The outcomes are, in the detail read's own shape,
    so a card can name each price without guessing "Yes" and "No". #214.
    """

    id: uuid.UUID
    status: MarketStatus
    question: str | None
    close_time: datetime | None
    outcomes: list[PublicOutcomeOut]


class PublicMarketListResponse(BaseModel):
    """One page of the browse list. [X-1] #34, #104.

    `next_cursor` is null on the last page, an empty one included.
    """

    markets: list[PublicMarketSummaryOut]
    next_cursor: str | None


# --- the administrator's overview. [2.1] #5 --------------------------------
# `status` is derived by `service/browsing.py::overview` before these are
# built, for the reason given above. `creator_id` is here, unlike the public
# projection, so the caller can tell their own markets.


class MarketOverviewRowOut(_UtcTimestamps):
    id: uuid.UUID
    creator_id: uuid.UUID
    status: MarketStatus
    question: str | None
    close_time: datetime | None


class MarketOverviewResponse(BaseModel):
    """Rows for the chosen filter, and a count for every status regardless of it."""

    markets: list[MarketOverviewRowOut]
    counts: dict[MarketStatus, int]
