"""Persistence models for the market service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.closing import trading_is_open
from core.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MarketStatus(StrEnum):
    """The part of the lifecycle [1.1] #1 and [1.3] #3 own.

    DRAFT is a scratchpad the creator's browser writes to on an idle timer.
    SUBMITTED means the creator pressed the button and every rule passed, so
    the terms are complete and internally consistent. Neither is visible to a
    trader.

    OPEN is the one traders see, and reaching it is the whole of [1.3] #3. It
    is also the one value outside this service's own walls, and [BE][X] #62 is
    built in this service so it imports the member below rather than retyping
    the string.

    What it is not, since [F-4] #44, is the browse query on its own. A market
    that is OPEN past its `close_time` is not tradeable and must not be listed
    as though it were; `service/closing.py::open_for_trading` is the predicate
    #62 filters on, and it is this member AND a close time still in the
    future.

    CLOSED arrives with [F-4] #44: trading has stopped, because the clock
    passed `close_time`. [2.3] #7 reaches the same status early and by hand,
    and it is the same state in every respect — one market stopped when it said
    it would and the other was stopped, and only the audit log knows which.
    It is what [3.1] #9 gates on — an outcome may only be proposed for a market
    nobody can still trade — and one of the buckets [2.1] #5 counts.

    PENDING_RESOLUTION arrives with [3.1] #9, APPROVED with [3.2] #10, and
    SETTLED with [3.4] #12.
    Members are added here as those land, which is why this is stored as a
    VARCHAR rather than a native Postgres enum: a new member is a Python change
    and never an ALTER TYPE against a live database. SQLAlchemy writes no CHECK
    constraint for it either (`create_constraint` has defaulted to False since
    1.4), so the value set is enforced here and at the API edge, where a bad
    value is a 422 rather than an IntegrityError surfacing as a 500.
    """

    DRAFT = "draft"
    SUBMITTED = "submitted"
    OPEN = "open"

    # [F-4] #44. Trading has stopped, by the clock or by hand ([2.3] #7), and
    # never restarts. NOT the authority on whether a trade may execute: that
    # is `close_time`, and this column lags it by a sweep. ADR 0011.
    CLOSED = "closed"

    # [3.1] #9. A winner is proposed and waits on a second administrator. The
    # terms stay frozen, because the proposal is about them. ADR 0013.
    PENDING_RESOLUTION = "pending_resolution"

    # [3.2] #10. A second administrator agreed; nothing is paid out yet. A
    # status rather than PENDING_RESOLUTION with an approver set, because the
    # market's legal moves change here. ADR 0016.
    APPROVED = "approved"


# The statuses a trader may see, defined once. [1.1] #1.
#
# Read by `service/browsing.py::_visible` and by the public route's `status`
# parameter, so the SQL filter, the accepted values and /docs cannot disagree
# when SETTLED lands. DRAFT and SUBMITTED must stay absent.
class PublicMarketStatus(StrEnum):
    OPEN = MarketStatus.OPEN.value
    CLOSED = MarketStatus.CLOSED.value
    PENDING_RESOLUTION = MarketStatus.PENDING_RESOLUTION.value
    APPROVED = MarketStatus.APPROVED.value


PUBLIC_STATUSES: tuple[MarketStatus, ...] = tuple(
    MarketStatus(member.value) for member in PublicMarketStatus
)

# The statuses in which a proposed winner is decided and may be shown to
# traders. PENDING_RESOLUTION is absent on purpose: it can still be rejected.
# D-026.
DECIDED_STATUSES: tuple[MarketStatus, ...] = (MarketStatus.APPROVED,)


# The unmapped attribute `get_published` stamps the derived status onto. Do not
# "simplify" this to `market.status = ...`: that marks the row dirty, a later
# commit would write the derived CLOSED into the column, and this derivation
# would become a writer, which ADR 0011 reserves for the sweep. D-027.
TRADER_FACING_STATUS = "trader_facing_status"


@dataclass(frozen=True)
class CardOutcome:
    """One outcome on a browse card: what a trader needs to name it. #214."""

    id: uuid.UUID
    position: int
    label: str


@dataclass(frozen=True)
class MarketCard:
    """One row of the trader-facing browse list. [X-1] #34, D-027.

    Built from column selects, so it loads no children, never enters the
    identity map and cannot be flushed. `status` is the derived value, with
    deliberately no raw one beside it. Frozen: it is an answer computed
    against one clock. `outcomes` is in position order. #214.
    """

    id: uuid.UUID
    status: MarketStatus
    question: str | None
    close_time: datetime | None
    outcomes: tuple[CardOutcome, ...]


@dataclass(frozen=True)
class AdminMarketCard:
    """One row of the administrator's overview. [2.1] #5.

    `MarketCard` plus `creator_id`, which the trader projection deliberately
    lacks (D-019). `status` is the derived value with no raw one beside it.
    """

    id: uuid.UUID
    creator_id: uuid.UUID
    status: MarketStatus
    question: str | None
    close_time: datetime | None


@dataclass(frozen=True)
class MarketOverview:
    """The overview's rows and per-status counts, read against one clock."""

    markets: list[AdminMarketCard]
    counts: dict[MarketStatus, int]


def displayed_status(
    status: MarketStatus,
    close_time: datetime | None,
    *,
    now: datetime | None = None,
) -> MarketStatus:
    """The status a trader is shown: OPEN past its close time reads CLOSED. D-022, D-024.

    Here rather than in `core/closing.py` because it returns a `MarketStatus`.
    Only ever turns OPEN into CLOSED, so an early-closed market is never
    reopened. `now` is the request's clock (D-025).
    """
    # Coerce first: `"open" is MarketStatus.OPEN` is False, so a plain string
    # would pass through unchanged and a closed market would read open. Do not
    # switch the check below to `==`; coercing covers every later comparison.
    status = MarketStatus(status)

    if status is not MarketStatus.OPEN:
        return status
    if trading_is_open(True, close_time, now=now):
        return status
    return MarketStatus.CLOSED


_STATUS_COLUMN = Enum(
    MarketStatus,
    name="market_status",
    native_enum=False,
    length=24,
    values_callable=lambda enum: [member.value for member in enum],
)


class Market(Base):
    __tablename__ = "markets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # A row in auth.users, and not a foreign key: there is no grant on that
    # schema. Taken from the verified token's `sub`, never the body. ADR 0003.
    creator_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)

    # Generated by the browser per form session, which makes POST /markets an
    # upsert. Unique per creator, so one admin cannot write over another's
    # market through a collision. ADR 0004.
    draft_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    status: Mapped[MarketStatus] = mapped_column(
        _STATUS_COLUMN, nullable=False, default=MarketStatus.DRAFT,
        server_default=MarketStatus.DRAFT.value, index=True,
    )

    # Every term is nullable because a draft may be incomplete; completeness
    # is checked at submission. ADR 0004.
    question: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    close_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # The rule that turns the sources into a winner. ADR 0004.
    resolution_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)

    # [1.2] #2. `b` sets how far a trade moves the price and the most the
    # market maker can lose, b·ln(n); the subsidy is the credits put up to
    # cover it. Nothing is charged here. Numeric because the subsidy is money.
    # Nullable like every term, though `_apply` always fills `b`.
    liquidity_b: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    seed_subsidy: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
        onupdate=_utcnow,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # [1.3] #3. When the market became tradeable. Separate from
    # `submitted_at`: two decisions, and the gap between them matters.
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # When trading was recorded as stopped. Not `close_time`: the sweep writes
    # this a little after the clock stops trading. A `closed_at` before
    # `close_time` marks a market stopped by hand. ADR 0011, ADR 0014.
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- the proposed outcome. [3.1] #9 -------------------------------------
    #
    # All seven are null until a proposal and written together with
    # PENDING_RESOLUTION. On the market rather than in a proposals table,
    # because at most one proposal is live and the audit log keeps history.
    # ADR 0013.

    # Minted per proposal and cleared by a rejection; a decision must quote it,
    # because the row lock cannot tell one proposal from its replacement. Null
    # only on a proposal older than the column, and deliberately not
    # backfilled: a generated id would be readable only by the creator, who may
    # not decide. ADR 0016.
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    # Not a ForeignKey: a plain FK would accept another market's outcome, so
    # validation must check it anyway, and the FK would only add a create_all
    # cycle. ADR 0013. `proposed_`, not `winning_`: nothing has won yet.
    proposed_outcome_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    # An id and a snapshotted username, because this service cannot look a
    # user up (ADR 0003). Decisions compare the id; a username can be reissued.
    proposed_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    proposed_by_username: Mapped[str | None] = mapped_column(String(32), nullable=True)

    proposed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # At least one is required, in `service/validation.py`. Two fields because
    # a URL is rendered as a link and a note is prose. ADR 0013.
    proposal_evidence_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    proposal_evidence_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- the approval. [3.2] #10 --------------------------------------------
    #
    # Null unless APPROVED, and set together. The proposal columns stay beside
    # them, so one row shows both identities. There are deliberately no
    # `rejected_by_*` columns and no deadline column. ADR 0016.
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approved_by_username: Mapped[str | None] = mapped_column(String(32), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    outcomes: Mapped[list[MarketOutcome]] = relationship(
        back_populates="market",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="MarketOutcome.position",
    )
    resolution_sources: Mapped[list[ResolutionSource]] = relationship(
        back_populates="market",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ResolutionSource.position",
    )

    __table_args__ = (
        UniqueConstraint("creator_id", "draft_key", name="uq_markets_creator_draft_key"),
        # The caller's own markets, newest activity first.
        Index("ix_markets_creator_updated", "creator_id", "updated_at"),
        # [F-4] #44. The close sweep's working set: partial on open markets and
        # keyed on `close_time`, so the sweep is an ordered scan that stops at
        # its limit. ADR 0011. The predicate is spelled out because
        # sql/migrations/0004 must match it literally; change both together.
        Index("ix_markets_due_close", "close_time", postgresql_where=text("status = 'open'")),
    )


class MarketOutcome(Base):
    """One tradeable outcome. Two of them is a binary market, more is categorical."""

    __tablename__ = "market_outcomes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    market_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("markets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Server-assigned from the client's order, so the admin's arrangement
    # survives a reload.
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    label: Mapped[str] = mapped_column(String(120), nullable=False)

    market: Mapped[Market] = relationship(back_populates="outcomes")

    __table_args__ = (
        UniqueConstraint("market_id", "position", name="uq_outcome_position"),
        # No unique index on lower(label): two blank rows in a half-typed form
        # would break the autosave. Label uniqueness is a submission rule.
        # ADR 0004.
    )


class ResolutionSource(Base):
    """Where a trader can go to check the outcome for themselves."""

    __tablename__ = "resolution_sources"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    market_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("markets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    # 2048 is the practical ceiling browsers and proxies agree on.
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)

    market: Mapped[Market] = relationship(back_populates="resolution_sources")

    __table_args__ = (
        UniqueConstraint("market_id", "position", name="uq_source_position"),
    )
