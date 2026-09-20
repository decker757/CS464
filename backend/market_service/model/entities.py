"""Persistence models for the market service."""

from __future__ import annotations

import uuid
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

from core.closing import is_open_for_trading
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

    # [F-4] #44, and [2.3] #7's early close. Terminal for trading, and this
    # service never moves a market back out of it.
    #
    # Read what this member is NOT. It is not the authority on whether a trade
    # may execute. A market stops accepting trades the instant `close_time`
    # passes, which is strictly earlier than anything gets around to writing
    # this column, and the gap is however long the sweeper takes to notice.
    # `service/closing.py` holds the predicate that is exact, and the reason
    # the status is a materialisation of it rather than the source.
    CLOSED = "closed"

    # [3.1] #9. An administrator has named a winning outcome and attached the
    # evidence for it; a second administrator has not yet agreed. Nothing is
    # paid out here and nothing is final — [3.2] #10 either approves, which
    # moves this to APPROVED, or rejects with a reason, which sends it back to
    # CLOSED.
    #
    # This is the one status in the enum that names a decision in flight rather
    # than a settled fact about the market, which is why the market's terms are
    # frozen in it exactly as they are in OPEN and CLOSED: the proposal is
    # about the terms, so a write that changed them would change what the
    # second administrator is being asked to agree to.
    PENDING_RESOLUTION = "pending_resolution"

    # [3.2] #10. A second administrator — never the one who proposed — has
    # agreed with the proposal. The winner is decided; nothing is paid out yet.
    #
    # A status of its own rather than PENDING_RESOLUTION with an approver
    # column filled in, because the market's legal moves change here and every
    # reader would otherwise have to ask `approved_at IS NULL` to find out which
    # ones. A second approval, a rejection, a second proposal and an early close
    # are all refused from this state, and each is told so by name rather than
    # by an error written for a market still waiting on somebody. It is also the
    # working set [3.3] #11's window and [3.4] #12's settlement will read —
    # `status = 'approved'` and an `approved_at` far enough in the past — which
    # is the same shape `ix_markets_due_close` gives the close sweep, and a
    # partial index on it is theirs to add when there is a query to serve.
    #
    # Not RESOLVED. Nothing is resolved while [3.3] #11 lets a trader dispute
    # it, and [3.4] #12 names the terminal state SETTLED. ADR 0016.
    APPROVED = "approved"


def displayed_status(
    status: MarketStatus,
    close_time: datetime | None,
    *,
    now: datetime | None = None,
) -> MarketStatus:
    """The status a trader is shown, as opposed to the one stored. D-022, D-024.

    ADR 0011's amendment splits this by audience: an administrator reads the
    column, because the gap between `close_time` and `closed_at` is how they
    tell whether the sweeper is running, and a trader reads this, because
    [X-1] #34's "open markets are clearly distinguishable from closed" cannot
    hold if the payload says `open` for a market that stopped four seconds
    ago.

    **Here rather than in `core/closing.py`, and the reason is a hard one
    rather than a preference.** This returns a `MarketStatus`, and `core/`
    may not import `model/entities` — nothing below `service` or `model`
    reaches sideways or up, which is exactly why `core.closing` takes a
    `bool` instead of a status. So the predicate could move down and this
    could not follow it. Here it sits beside the enum it returns, and both
    callers may reach it: `model/schemas.py` derives the trader-facing
    projections with it, and `service/browsing.py` counts with it. Before
    D-024 they held one copy each.

    Takes the two values rather than a `Market`, so a Pydantic model holding
    a projection of a market can call it without reconstructing an entity.

    Only ever turns OPEN into CLOSED. PENDING_RESOLUTION and APPROVED pass
    through exactly as they are, and a market closed early ([2.3] #7) is not
    reopened by a `close_time` that ADR 0014 deliberately left in the future.

    `now` is the request's own clock, threaded down from the controller so
    that the filter and the display cannot read two different instants
    (D-025). Left to default, it reads the clock itself, which keeps this
    callable from a test and from anything that holds no request.
    """
    if status is not MarketStatus.OPEN:
        return status
    if is_open_for_trading(True, close_time, now=now):
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

    # Names a row in auth.users, and cannot be a foreign key: market_svc has no
    # grant on the auth schema, by design. The value is taken from the `sub`
    # claim of a signature-checked token, never from the request body, so it is
    # as trustworthy as the token is. The cost is that this service cannot tell
    # a deleted account from a live one; see ADR 0003.
    creator_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)

    # Generated by the browser when the create form opens and sent on every
    # autosave, which is what makes POST /markets an upsert instead of a
    # machine for producing one abandoned draft every three seconds. Unique per
    # creator rather than globally, so a collision between two clients cannot
    # let one administrator write over another's market.
    draft_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    status: Mapped[MarketStatus] = mapped_column(
        _STATUS_COLUMN, nullable=False, default=MarketStatus.DRAFT,
        server_default=MarketStatus.DRAFT.value, index=True,
    )

    # Every term below is nullable because a draft is allowed to be incomplete.
    # An autosave that fired on a half-typed form and lost the admin's work
    # would defeat the point of autosaving. The rules in service/validation.py
    # are what enforce completeness, and only at the moment of submission.
    question: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    close_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # How the sources below decide the winner. A bare URL says where to look
    # and not what to look for, and that gap is what [3.3] #11's dispute window
    # exists to absorb.
    resolution_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)

    # How this market will be priced. [1.2] #2.
    #
    # `liquidity_b` sets how far a trade moves the price, and with it the most
    # the automated market maker can lose: b*ln(n), which
    # `core/opening_prices.py`
    # derives. `seed_subsidy` is the mock credits put up to cover that loss.
    # Nothing here is charged to anybody — moving credits is the ledger's job
    # ([F-1] #41) and this service holds no balances.
    #
    # Numeric rather than double precision because the subsidy is money and
    # will share arithmetic with the ledger's entries. Four decimal places is
    # more than mock credits need and costs nothing.
    #
    # Nullable like every other term: an autosave fires on a half-typed form
    # and must never be the thing that fails. `liquidity_b` is in practice
    # always set, because `_apply` falls back to the configured default, but
    # the column stays nullable so a row written before [1.2] is still legal.
    #
    # These two are also the payload of ADR 0005's publish-time handoff: [1.3]
    # #3 snapshots them to the trading side, where they are safe to copy
    # precisely because [1.4] #4 forbids editing a published market.
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

    # When this market became tradeable. [1.3] #3.
    #
    # Separate from `submitted_at` rather than replacing it, because they
    # record two different decisions and the gap between them is the thing
    # worth being able to see: the terms were finalised here, and somebody
    # chose to put them in front of traders there. Null for every market that
    # has not been published, which is what makes it safe to read as "is this
    # live" without consulting the status, though `status` remains the
    # authority.
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # When trading actually stopped. [F-4] #44.
    #
    # Not the same thing as `close_time`, and keeping both is the point.
    # `close_time` is the promise the administrator made to traders and is the
    # instant trading really ended; this is when the sweeper got around to
    # recording it, which is a few seconds later and occasionally much later if
    # the service was down. A gap between the two is a normal observation about
    # a background job, not a market that stayed open — nothing could trade in
    # it either way.
    #
    # [2.3] #7's early close writes it too, at a time that has nothing to do
    # with `close_time` at all, and that is the one case where the two columns
    # say something a reader can act on: a `closed_at` *before* the
    # `close_time` is a market an administrator stopped by hand. The reason
    # they did is in the audit log and deliberately not here — one fact, one
    # place. ADR 0014.
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- the proposed outcome. [3.1] #9 -------------------------------------
    #
    # All seven are null until an administrator proposes, and all seven are
    # written in one transaction with `status = PENDING_RESOLUTION`, so a market that
    # is pending resolution always has a complete proposal on it and a market
    # that is not never has a partial one.
    #
    # They sit on the market rather than in a proposals table because there is
    # at most one live proposal per market, by construction: proposing requires
    # CLOSED, and proposing leaves the market PENDING_RESOLUTION, so a second
    # proposal is refused rather than queued. [3.2] #10 adds the approver
    # beside these on the same argument, and a table becomes worth its join
    # only if [3.3] #11's dispute window ever has to keep the history of a
    # rejected proposal — which it does not, because a rejection is recorded in
    # the audit log, which is the thing that keeps history here.

    # Which proposal this is. [3.2] #10.
    #
    # Minted fresh by every `propose_outcome` and cleared by a rejection, so no
    # two proposals a market ever carries share one — including two that name
    # the same winner on the same evidence. An approval or a rejection has to
    # quote it, and is refused as `proposal_superseded` when it no longer
    # matches.
    #
    # The row lock cannot do this job, and that is why the column exists. A
    # lock serialises two decisions that overlap; it cannot see a whole cycle
    # that completed while an administrator was reading. Reviewer A opens
    # proposal 1, reviewer B rejects it, the creator proposes 2 — and A's
    # approve, keyed only by the market, would then approve 2, a winner and
    # evidence A never saw. The market is PENDING_RESOLUTION both times; only
    # this tells them apart. A timestamp would too, in practice, but an
    # identifier cannot collide and says what it is for. ADR 0016.
    #
    # Null on a proposal made before this column existed, and deliberately not
    # backfilled: a generated id would live only on this row, which the
    # reviewer — never the creator — cannot read, so nobody could quote it.
    # Such a proposal is decided by quoting null, which matches nothing else:
    # every later proposal is minted an id, and a rejection leaves the market
    # CLOSED rather than pending with a null. `sql/migrations/0006`.
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    # Which outcome was named as the winner.
    #
    # Deliberately NOT a ForeignKey to market_outcomes.id, and the reason is
    # worth reading before one is added. The constraint that actually matters
    # is that the outcome belongs to *this* market, and a plain FK cannot say
    # that — it would accept another market's outcome id happily. So the check
    # has to exist in `service/validation.py` regardless, where it is a
    # field-addressed 422 rather than an IntegrityError surfacing as a 500.
    # Given that check, the FK would add nothing except a dependency cycle
    # between these two tables, which `create_all` cannot sort without
    # `use_alter`. The version that would genuinely earn its place is a
    # composite FK to (id, market_id) with a matching unique constraint; ADR
    # 0013 records why that is not worth the second index yet.
    #
    # Named `proposed_outcome_id` rather than `winning_outcome_id` on purpose.
    # Nothing has won anything: a second administrator may reject this, and
    # [3.2] #10 then clears this column and sends the market back to CLOSED.
    # The request field is `winning_outcome_id`, because that is what the
    # administrator is asserting; the column is what the platform has recorded.
    proposed_outcome_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    # Who proposed it. The ticket's fourth acceptance criterion, and the value
    # [3.2] #10 compares against to disable the approve control for the
    # proposing administrator.
    #
    # Two columns rather than one, for the reason `service/audit.py::Actor`
    # gives: this service cannot resolve a user id against `auth.users` and
    # never will be able to (ADR 0003), so a username that is not snapshotted
    # here is a username nobody can recover later — the audit log has it, and
    # no service but `audit_svc` may read that. It is also the more truthful
    # record: who this administrator was when they proposed, which survives a
    # rename, a demotion and a deleted account.
    #
    # The id is the one [3.2] #10 must compare on. A username can be reissued
    # after a deletion; an id cannot, and "a different administrator" is a
    # claim that has to hold against the account rather than against the name.
    proposed_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    proposed_by_username: Mapped[str | None] = mapped_column(String(32), nullable=True)

    proposed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # The evidence. At least one of the two is required, which is a rule in
    # `service/validation.py` rather than a constraint here, because it is a
    # rule about a request and belongs where the admin gets told which field to
    # fix.
    #
    # Both are kept, rather than one free-text field that may or may not hold a
    # URL, because they are read differently: a URL is something the frontend
    # renders as a link and [3.3] #11's disputer clicks, and a note is prose
    # for the case where the source is a screenshot, a phone call or a
    # judgement call about an ambiguous print. Collapsing them would mean
    # sniffing prose for a link.
    proposal_evidence_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    proposal_evidence_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- the approval. [3.2] #10 --------------------------------------------
    #
    # All three are null unless `status == APPROVED`, and all three are written
    # in the one transaction that sets it. The seven proposal columns above stay
    # exactly as they were: an approval agrees with a proposal rather than
    # replacing it, so `proposed_outcome_id` is still the winner [3.4] #12
    # settles against, and a reader sees both identities on one row — the
    # ticket's third acceptance criterion.
    #
    # Snapshotted as an id and a username for the reason `proposed_by_*` is.
    # The id is what the two-person rule is enforced on; the approver is never
    # the proposer, and `service/market_service.py` compares the ids to make
    # that true.
    #
    # There are no `rejected_by_*` columns, and that is deliberate. A rejection
    # clears the proposal and returns the market to CLOSED, after which the row
    # holds no proposal for a rejecter's name to describe. Who rejected it, and
    # why, is in the `market.outcome_rejected` audit entry, beside a copy of the
    # proposal it cleared. ADR 0016.
    #
    # No deadline column either. [3.3] #11's window is `approved_at` plus a
    # setting, derived the way the close sweep derives from `close_time`, and
    # whether that ticket wants to snapshot the end of it is its own additive
    # decision.
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
        # Serves the only list query this service runs: the caller's own
        # markets, newest activity first.
        Index("ix_markets_creator_updated", "creator_id", "updated_at"),
        # [F-4] #44. The close sweeper's entire working set.
        #
        # Two properties, and it is the second that earns the index. Partial,
        # so it holds only the markets still open rather than every market ever
        # created — 72 kB against 20,000 settled markets, and it does not grow
        # as they pile up. And keyed on `close_time`, so the sweep's
        # `ORDER BY close_time LIMIT n` is an ordered index scan that stops at
        # n rather than a read of every open market followed by a sort.
        #
        # Measured on 20,000 settled and 2,200 open markets, 200 of them due:
        # 5 index buffers and 0.05 ms with this index, 36 buffers and 0.17 ms
        # without, and the gap widens with the number of open markets, which is
        # the number that actually grows. On an idle tick nothing is due and
        # either index answers in about 4 buffers.
        #
        # `ix_market_markets_status` does not replace it. That one is keyed on
        # status alone, so it can find the open markets and then has to sort
        # them, and it carries an entry for every market this platform ever
        # runs.
        #
        # The predicate is written out rather than built from MarketStatus.OPEN
        # because sql/migrations/0004 has to state the same thing in SQL and
        # the two must match literally. Change one and change the other.
        Index("ix_markets_due_close", "close_time", postgresql_where=text("status = 'open'")),
    )


class MarketOutcome(Base):
    """One tradeable outcome. Two of them is a binary market, more is categorical."""

    __tablename__ = "market_outcomes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    market_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("markets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Server-assigned from the order the client sent, so it cannot collide, and
    # so the outcome order the admin arranged survives a reload. [1.2] #2 needs
    # a stable order for uniform initial prices, and epic 3 needs one to name a
    # winner without relying on a label that could be edited.
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    label: Mapped[str] = mapped_column(String(120), nullable=False)

    market: Mapped[Market] = relationship(back_populates="outcomes")

    __table_args__ = (
        UniqueConstraint("market_id", "position", name="uq_outcome_position"),
        # Note what is NOT here: a unique index on lower(label). Two blank rows
        # in a half-typed form would violate it, so autosave would start
        # failing exactly when the admin is mid-thought. Label uniqueness is a
        # submission rule instead, in service/validation.py.
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
