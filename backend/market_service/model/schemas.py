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
# `resolution_criteria`, [3.1] #9's `evidence_note`, [2.3] #7's close `reason`
# and [3.2] #10's rejection `reason`. All five land in unbounded `Text`
# columns, so this is here to stop a request carrying a megabyte rather than to
# have an opinion about how much explaining any of them needs — which is why
# one number covers all of them. It was two bare literals until the third use
# arrived.
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


# The field both decisions carry, declared once so the two cannot describe it
# differently. It is a precondition rather than information: the decider is not
# telling the service anything new, they are saying which proposal they read.
#
# Required, and nullable. The key must always be sent; its value is null only
# for a proposal made before [3.2] #10 gave proposals ids, which reads back
# with `proposal_id: null` and whose audit entry has no `proposal_id` at all.
# That is safe because such a proposal is the only thing a null can match:
# every proposal made since is minted an id, and a rejection returns the market
# to CLOSED, so a market cannot be pending with a null id again. A stale null
# quoted against a replacement is refused like any other stale id.
_PROPOSAL_ID_FIELD_DESCRIPTION = (
    "The `proposal_id` of the proposal the administrator reviewed, exactly as "
    "it was read — from `GET /markets/{id}`, or from the "
    "`market.outcome_proposed` audit entry's `context`. Send `null` only for "
    "a proposal that has none, which is one made before proposal ids existed. "
    "If the market has since had that proposal rejected and a new one made, "
    "the decision is refused as `409 proposal_superseded` rather than applied "
    "to a proposal nobody here has read."
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
    # cheapest test for "is there an outcome on this market", and it agrees
    # with `status` being `pending_resolution` or `approved` by construction:
    # the seven are written in the one transaction that sets the first, kept by
    # the approval that sets the second, and nulled by [3.2] #10's rejection in
    # the one transaction that sends the market back to `closed`.
    #
    # `proposal_id` is new for every proposal, and is what the approve and
    # reject bodies must send back. Read it from the same response the reviewer
    # is looking at, never from a later fetch, or the check it exists for
    # passes against a proposal the reviewer has not seen. The one exception to
    # "all set together": a proposal made before [3.2] #10 is pending with this
    # null, and is decided by sending the null back.
    #
    # `proposed_outcome_id` names a member of this response's own `outcomes`
    # array, so the frontend renders the winner's label by looking it up there
    # rather than being sent the label twice. The label on the outcome is the
    # only copy, which is what stops the two drifting.
    proposal_id: uuid.UUID | None
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

    # --- the approval. [3.2] #10 --------------------------------------------
    # All null unless `status == "approved"`, and set together in the one
    # transaction that sets it. The seven proposal fields above stay filled in
    # beside them, so an approved market answers "who proposed this" and "who
    # agreed" in one response — the ticket's third acceptance criterion.
    #
    # `approved_by_id` never equals `proposed_by_id`; the service refuses the
    # approval that would make it so. A rejection does not appear here at all:
    # it sends the market back to `closed` and nulls every `proposed_*` field,
    # and who rejected it is in the audit log.
    approved_by_id: uuid.UUID | None
    approved_by_username: str | None
    approved_at: datetime | None

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


# --- the trader-facing projection. [BE][X] #62 -----------------------------
# `PublicMarketOut` and `PublicMarketSummaryOut` beside `MarketOut` and
# `MarketSummaryOut`, not a change to either. D-021: the create form needs
# `liquidity_b` as a JSON number to compare against `max_platform_loss`, and
# the ledger that reads this endpoint needs it exact — one schema cannot be
# both, so this is a second pair rather than a flag on the first.
#
# Two things `MarketOut` carries are deliberately absent here. `creator_id`
# and `draft_key` are neither a trader's business nor harmless (D-019):
# `creator_id` invites exactly the "whose market is it" argument ADR 0016
# keeps out of who may decide an outcome, and `draft_key` would let a client
# address a market by the autosave's own idempotency key. And each outcome's
# `initial_price` is gone too — it is the q=0 opening price, simply wrong
# once a market has traded, with nothing on the response to say so.


def _derive_public_status(
    status: MarketStatus, close_time: datetime | None
) -> MarketStatus:
    """ADR 0011 / D-022: a trader is shown CLOSED before the sweep writes it.

    Restates the two conditions `service.closing.is_open_for_trading` checks
    rather than importing that function: `model/` sits below `service/` in
    this repository's import direction and may not reach up for it. This is a
    read of an object already in hand, for display — never the gate a trade
    is checked against, which stays sole in `service/closing.py`.

    Only ever turns OPEN into CLOSED. PENDING_RESOLUTION and APPROVED pass
    through exactly as they are, and a market CLOSED early ([2.3] #7) is not
    reopened by a `close_time` that ADR 0014 deliberately left in the future.
    """
    if (
        status is MarketStatus.OPEN
        and close_time is not None
        and close_time <= datetime.now(UTC)
    ):
        return MarketStatus.CLOSED
    return status


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
    status: MarketStatus

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

    # [X-3] #36: "Settled markets display the winning outcome." APPROVED is as
    # far as a market gets today; SETTLED arrives with [3.4] #12. The label is
    # read from `outcomes` above rather than repeated here, which is what
    # stops the two copies drifting — the same rule `MarketOut` follows for
    # the administrator's view. Null on every market nobody has proposed for.
    proposed_outcome_id: uuid.UUID | None

    @model_validator(mode="after")
    def _derive_status(self) -> PublicMarketOut:
        self.status = _derive_public_status(self.status, self.close_time)
        return self


class PublicMarketSummaryOut(_UtcTimestamps):
    """The trader-facing list projection. [X-1] #34. Deliberately narrow.

    Prices are not here either, for the same reason they are not on the
    detail read: a card renders them from the snapshot endpoint once [F-3]
    #43 and [T-2] #22 land.
    """

    id: uuid.UUID
    status: MarketStatus
    question: str | None
    close_time: datetime | None

    @model_validator(mode="after")
    def _derive_status(self) -> PublicMarketSummaryOut:
        self.status = _derive_public_status(self.status, self.close_time)
        return self


class PublicMarketListResponse(BaseModel):
    markets: list[PublicMarketSummaryOut]
