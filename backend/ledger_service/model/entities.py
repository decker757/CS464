"""Persistence models for the ledger service. [F-1] #41

Three tables and one invariant: **every transaction's entries sum to zero.**
Credits are never created or destroyed by a movement, only moved, and the
platform account going negative is what "minted" means here. A balance is
therefore `SUM(amount)` over an account's entries and is never a column, never
cached, and never mutated in place.

`ledger.entries` is append-only. There is no code path that updates or deletes
one, and a statement-level trigger at the bottom of this file refuses both at
the database. Correcting a mistake means appending the transaction that
reverses it, exactly as the audit log's hint says.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DDL,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import SCHEMA, Base

# Money, everywhere in this service. Matches `market.markets.seed_subsidy`,
# which is the same credits seen from the other side of a service boundary:
# a subsidy the admin promised there is a balance somebody spends here, and the
# two arithmetics have to agree down to the last place.
AMOUNT_PRECISION = 18
AMOUNT_SCALE = 4


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AccountKind(StrEnum):
    """Who can hold credits.

    Stored as a non-native `Enum`, so the column is a plain `varchar(24)` with
    no CHECK — SQLAlchemy has defaulted `create_constraint` to False since 1.4.
    Adding a member is therefore a Python change and never an ALTER TYPE
    against a live database, which is the whole reason [T-2] #22 can add
    MARKET_POOL without a migration. CLAUDE.md says the same thing about
    `MarketStatus`, and the verification query there works here too.
    """

    # One per registered user, created on the first read of their balance.
    USER = "user"

    # The house. Exactly one row, and the only account allowed to go negative:
    # its balance is minus the credits in circulation, which is what makes the
    # starting grant a movement rather than an invention. See service/grants.py.
    PLATFORM = "platform"

    # One per market, owner_id equal to the market id (D-028). Funded by the
    # seed subsidy at book creation and exempt from the overdraft check like
    # PLATFORM, because LMSR pays out up to b*ln(n) more than it collects. See
    # service/books.py.
    MARKET_POOL = "market_pool"


class TransactionKind(StrEnum):
    """Why credits moved.

    SIGNUP_GRANT is the oldest member. TRADE_BUY and TRADE_SELL arrived with
    [T-2] #22 and [T-3] #23, and SETTLEMENT arrives with [3.4] #12. Each is a
    Python-only addition for the same reason as `AccountKind`.
    """

    SIGNUP_GRANT = "signup_grant"

    # PLATFORM -> MARKET_POOL, posted once at book creation (D-009). A kind of
    # its own rather than SIGNUP_GRANT, so a market's subsidy does not render on
    # somebody's statement as a welcome bonus.
    MARKET_SEED = "market_seed"

    # USER -> MARKET_POOL, posted once per trade. [T-2] #22. A Python-only
    # addition like the two above: the column is a non-native `Enum`, so this
    # needs no migration against a database that already has the table.
    TRADE_BUY = "trade_buy"

    # MARKET_POOL -> USER, posted once per sell. [T-3] #23. Non-native like the
    # rest, so it needs no migration either.
    TRADE_SELL = "trade_sell"


_ACCOUNT_KIND_COLUMN = Enum(
    AccountKind,
    name="ledger_account_kind",
    native_enum=False,
    length=24,
    values_callable=lambda enum: [member.value for member in enum],
)

_TRANSACTION_KIND_COLUMN = Enum(
    TransactionKind,
    name="ledger_transaction_kind",
    native_enum=False,
    length=32,
    values_callable=lambda enum: [member.value for member in enum],
)


class Account(Base):
    """A party that can hold credits.

    Note how little is here. There is no balance column and there will not be
    one: a balance is derived by summing this account's entries, which is
    [B-1] #32's third acceptance criterion and [4.1] #13's third. Two sources
    of truth for money is the failure mode this whole design exists to avoid.

    So this table has exactly two jobs. It gives an account an identity that a
    foreign key can point at, and it gives the write path a single row to take
    `SELECT ... FOR UPDATE` on, which is what serialises two concurrent trades
    from one user instead of letting both read the same balance and overdraw
    it. Without this table there would be nothing to lock.
    """

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    kind: Mapped[AccountKind] = mapped_column(_ACCOUNT_KIND_COLUMN, nullable=False)

    # For a USER account this names a row in `auth.users`, and it cannot be a
    # foreign key: ledger_svc has no grant on the auth schema, by design. The
    # value comes from the `sub` claim of a signature-checked token, never from
    # a request body. Same trade as `market.markets.creator_id`; ADR 0003.
    #
    # NOT NULL rather than nullable-for-the-platform-account, which is the
    # obvious shape and the wrong one. Postgres treats every NULL in a unique
    # key as distinct from every other, so `UNIQUE (kind, owner_id)` would
    # happily admit two platform accounts and the ledger would start summing to
    # zero across a house that was two houses. PLATFORM_OWNER_ID below is a
    # fixed sentinel instead, and the constraint means what it looks like it
    # means.
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    __table_args__ = (
        # The account identity. `service/accounts.py` leans on this: two
        # concurrent first reads for one user both find nothing and both
        # insert, and this is what turns the loser into an IntegrityError it
        # can retry rather than a second account nobody notices.
        UniqueConstraint("kind", "owner_id", name="uq_accounts_kind_owner"),
    )


# The one PLATFORM account's owner_id. A constant rather than a NULL, for the
# reason on `owner_id` above, and the all-zero UUID rather than a random one so
# that it is obviously not somebody's user id and is the same value in every
# database this service has ever run against.
PLATFORM_OWNER_ID = uuid.UUID(int=0)


class Transaction(Base):
    """One movement of credits, whose entries sum to zero.

    A transaction is the unit a caller retries and the unit a reader
    understands; an entry on its own is half a sentence.
    """

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    kind: Mapped[TransactionKind] = mapped_column(
        _TRANSACTION_KIND_COLUMN, nullable=False
    )

    # The issue's "idempotency key on writes", and the reason retrying a trade
    # cannot execute it twice ([T-2] #22) and retrying registration cannot
    # double-grant ([B-1] #32). Unique across the whole ledger rather than per
    # account, because the caller that generates it does not always know which
    # accounts the transaction will touch, and a key that means different
    # things in different scopes is not a key.
    #
    # Callers namespace it: `signup-grant:<user_id>` is the one this service
    # generates for itself.
    #
    # 255 rather than the original 120. [T-2] #22's derived trade key is
    # `trade:<user_id>:<market_id>:<client key>` — 80 characters of prefix
    # before the client's own string even starts — and
    # `test_a_client_key_naming_the_grant_namespace_cannot_touch_it` drives a
    # client key that is itself a namespaced key
    # (`signup-grant:<uuid>`, 49 characters) to prove the derivation confines
    # a collision to one caller. 120 truncates that combination; 255 leaves
    # room for a client key longer than any of this service's own namespaced
    # keys with margin to spare. Needs `sql/migrations/0007-ledger-trade-idempotency-key-width.sql`
    # against a database that already has this column.
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    # A hash of the legs, so a replayed key can be told from a reused one.
    #
    # Without it, a caller that reuses a key for a genuinely different
    # transaction is told their new movement succeeded, and handed the old one
    # as proof. That is the classic idempotency-key bug and in a ledger it
    # means somebody is shown a trade they did not make. With it, the same
    # request replays and a different request is refused.
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    # Supplied by the service rather than defaulted by the database, so that a
    # test can inject a clock without freezing the system one. Same reason as
    # `audit.admin_actions.occurred_at`.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    # Whatever else this kind of movement needs to be reconstructible later:
    # the market and outcome of a trade, the resolution a payout came from.
    # jsonb rather than more columns for the same reason
    # `audit.admin_actions.context` is: this table is append-only by design, so
    # every future movement shape would otherwise be an ALTER on a table that
    # is meant never to change.
    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Lazy, unlike its inverse below, and unlike every other collection in this
    # repository. Nothing reads it from the database: `posting.post` builds it
    # in memory on a transaction it is creating, and the history feed travels
    # the other way. Making it eager would put this relationship and
    # `Entry.transaction` in a cycle, so reading one page of entries would fetch
    # their transactions and then those transactions' entries again.
    entries: Mapped[list[Entry]] = relationship(
        back_populates="transaction", order_by="Entry.id"
    )


class Entry(Base):
    """One side of one movement. Append-only.

    `amount` is signed: negative is a debit, positive a credit, and the entries
    of a transaction sum to zero. The alternative — a positive amount beside a
    direction column — makes every balance query a CASE expression and every
    balance check a place to get a sign wrong. Signed means a balance is
    `SUM(amount)` and the invariant is `SUM(amount) = 0`, both of which are
    statements you can hand to Postgres without writing any arithmetic down
    twice.

    There is deliberately no `balance_after` column. It would be a second
    source of truth that a concurrent write could make wrong, and it is one
    aggregate away at read time for [4.1] #13, which is the only story that
    wants it.
    """

    __tablename__ = "entries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        # No ondelete. Every other parent/child pair in this repository
        # cascades; this one must not, because there is no delete to cascade
        # from. A cascade here would be a working mechanism for removing
        # ledger rows, sitting one careless `session.delete` away from the
        # thing this table exists to make impossible.
        ForeignKey("transactions.id"),
        nullable=False,
        index=True,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id"), nullable=False
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(AMOUNT_PRECISION, AMOUNT_SCALE), nullable=False
    )

    # Copied from the transaction rather than defaulted per row, so that both
    # halves of one movement carry the identical timestamp. The history feed
    # orders on it, and two sides of one trade appearing a microsecond apart
    # would let a page boundary fall between them.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    # Eager, because the history feed renders every entry beside the kind and
    # context of the movement it belongs to, and a lazy load reached from a
    # response model raises MissingGreenlet under asyncio rather than quietly
    # issuing a query.
    #
    # There is deliberately no `account` relationship to match. Nothing needs
    # one — balances and history both filter on `account_id` — and an unused
    # lazy relationship on an async model is a trap rather than documentation.
    transaction: Mapped[Transaction] = relationship(
        back_populates="entries", lazy="selectin"
    )

    __table_args__ = (
        # A zero-amount entry is not a movement. It would pass the sum-to-zero
        # invariant while saying nothing, and it would appear on somebody's
        # statement as a transaction that did not happen.
        CheckConstraint("amount <> 0", name="ck_entries_amount_nonzero"),
        # Serves both reads this service performs: the balance sum for one
        # account, and its history page newest-first. Spelled DESC to match the
        # keyset ordering in core/paging.py exactly, and to read the same way
        # as the audit feed indexes in sql/02-schemas.sql — Postgres would scan
        # an ascending index backwards just as happily.
        Index(
            "ix_ledger_entries_account_feed",
            "account_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
    )


class MarketBook(Base):
    """A market's LMSR state, as this service knows it. [F-7] #96, D-008.

    Created lazily, on the first request that needs it: `service/books.py`
    reads the terms from market_service's public detail endpoint and copies
    them here, once. ADR 0005 calls this duplication without coupling — [1.4]
    #4 forbids editing a published market's terms, so a copy of data that
    cannot change cannot drift from its source.

    `state_version` is D-011's quote reference: one counter, incremented under
    this row's lock by whichever trade last moved `q` on one of this market's
    `MarketOutcome` rows. Nothing in this ticket increments it — that is [T-2]
    #22 — so it opens at zero and stays there.
    """

    __tablename__ = "market_books"

    # Not a ForeignKey. This names a row in `market.markets`, and ledger_svc
    # holds no grant on that schema — the same trade `Account.owner_id`
    # already makes for a user id. ADR 0003. The primary key is also what
    # D-010's race depends on: two concurrent first-touches both insert, and
    # this is what turns the loser into an `IntegrityError` it can recover
    # from rather than a second book nobody notices.
    market_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)

    # A snapshot, not a live read. See the class docstring.
    liquidity_b: Mapped[Decimal] = mapped_column(
        Numeric(AMOUNT_PRECISION, AMOUNT_SCALE), nullable=False
    )
    seed_subsidy: Mapped[Decimal] = mapped_column(
        Numeric(AMOUNT_PRECISION, AMOUNT_SCALE), nullable=False
    )

    # A ForeignKey, unlike market_id above — D-035. ADR 0003's rule is about a
    # join across a *service* boundary this schema has no grant for;
    # `ledger.accounts` is this service's own table, and the row it names is
    # created in the same transaction as this one. An FK here catches a real
    # mistake — a book pointing at an account that does not exist — that the
    # service-boundary argument was never about.
    pool_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id"), nullable=False
    )

    # D-011. Starts at zero (a never-traded market) and only ever increases,
    # one per trade, under this row's lock — never negative by any code path
    # this service has, which is exactly why it is worth a CHECK: if one ever
    # appears, something wrote to this column that should not have.
    state_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )

    # D-029: equal to `opened_at` at creation, and equal only then. For a
    # market nobody has traded, the book's creation *is* its last state
    # change, so any other value here would invent a moment that did not
    # happen.
    state_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "state_version >= 0", name="ck_market_books_state_version_nonneg"
        ),
    )


class MarketOutcome(Base):
    """One outcome's state within a market's book. [F-7] #96.

    `outcome_id` and `position` are copied from market_service's response at
    book creation, exactly once — the same snapshot argument as `MarketBook`.
    No label: that is display prose market_service owns, edited on a draft and
    read by nobody on this side, so copying it here would be a second source
    of truth for a string.
    """

    __tablename__ = "market_outcomes"

    # `market_id` IS a ForeignKey, and `outcome_id` is not — D-035, whose rule
    # is about which *table* a column references rather than which service
    # generated the value in it. Both values come from market_service, but
    # `market_id` names a row in `ledger.market_books`, this service's own
    # table in its own schema, created in the same transaction as these rows.
    # That is exactly the case D-035 says is worth a constraint: an outcome
    # row orphaned from its book is a market whose `q` vector exists with
    # nothing to price it against, and it is the kind of mistake [T-2] #22
    # could introduce without anything going red. `outcome_id` names a row in
    # `market.outcomes`, which ledger_svc holds no grant on, so it stays bare
    # for the reason ADR 0003 gives.
    #
    # Composite primary key rather than a surrogate id plus a separate unique
    # constraint: nothing ever references one of these rows by an id of its
    # own, and the pair is the ticket's "unique on (market_id, outcome_id)" by
    # construction.
    market_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_books.market_id"), primary_key=True
    )
    outcome_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)

    # Server-assigned by market_service from the order it sent, and what
    # orders the `q` vector handed to the pricing engine. Duplicated within one
    # market it would make YES and NO swap between two reads with nothing
    # written in between, which is what the second unique constraint below
    # refuses.
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    # Shares outstanding for this outcome. Zero for every outcome at book
    # creation, which is what makes the opening price uniform: C(q) at q = 0
    # gives every outcome 1/n. Whether share quantities share money's scale of
    # 4 is unresolved (DECISIONS.md, Open) — this ticket only ever writes zero
    # into this column, so it borrows the money scale without deciding that
    # question.
    q: Mapped[Decimal] = mapped_column(
        Numeric(AMOUNT_PRECISION, AMOUNT_SCALE),
        nullable=False,
        default=Decimal(0),
        server_default=text("0"),
    )

    __table_args__ = (
        UniqueConstraint(
            "market_id", "position", name="uq_market_outcomes_market_position"
        ),
    )


class Position(Base):
    """A trader's net holding in one outcome of one market. [T-2] #22.

    Primary key is the triple `(user_id, market_id, outcome_id)` rather than a
    surrogate id — the same argument `MarketOutcome`'s pair makes: nothing
    references a position by an identity of its own, and [T-4] #24 reaches
    them by the triple.

    **This table is not append-only, and must not carry `ledger.entries`'
    trigger.** It is a rollup, UPDATEd on every second buy into the same
    outcome, and reconstructible from the entries — which stay the record.
    `cost_basis` is stored and the average entry price is derived at read
    time ("`cost_basis` is stored; average entry price is derived"), the same
    reason there is no `balance_after` column on `Entry`.

    `#22 only ever adds to a position` — a partial sell's effect on
    `cost_basis` is [T-3] #23's to decide, not this ticket's.
    """

    __tablename__ = "positions"

    # `user_id` names a row in `auth.users` and cannot be a foreign key —
    # `ledger_svc` holds no grant on that schema (ADR 0003), the same trade
    # `Account.owner_id` already makes.
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)

    # `(market_id, outcome_id)` IS a composite foreign key, below — this
    # service's own table, in its own schema, named the same way
    # `market_outcomes.market_id` references `market_books.market_id`
    # ("`pool_account_id` is a foreign key; `market_id` still is not").
    market_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    outcome_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)

    # "Shares keep money's scale of 4, in `q` and in positions": a quantity
    # arrives at scale 4 and addition at scale 4 is closed, so nothing a
    # trader was quoted for is rounded between the quote and this row.
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(AMOUNT_PRECISION, AMOUNT_SCALE), nullable=False
    )
    cost_basis: Mapped[Decimal] = mapped_column(
        Numeric(AMOUNT_PRECISION, AMOUNT_SCALE), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )
    # The one column on this table with no counterpart anywhere else in this
    # file: every other table here is append-only or write-once, and this is
    # the only one whose rows change after they are written.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["market_id", "outcome_id"],
            ["market_outcomes.market_id", "market_outcomes.outcome_id"],
            name="fk_positions_market_outcome",
        ),
        # The no-shorting rule per user, the counterpart to
        # `InsufficientSharesOutstanding`'s per-outcome one. [T-3] #23
        # enforces it under the book lock; this is the backstop, not the
        # check.
        CheckConstraint("quantity >= 0", name="ck_positions_quantity_nonneg"),
        CheckConstraint("cost_basis >= 0", name="ck_positions_cost_basis_nonneg"),
    )


# ---------------------------------------------------------------------------
# Append-only, at the database.
# ---------------------------------------------------------------------------
#
# The same statement-level trigger `sql/02-schemas.sql` puts on
# `audit.admin_actions`, and for the same reason: a grant protects against an
# application role, a trigger protects against the table's owner too, so a
# stray UPDATE from a psql session fails as loudly as one from this service
# would. Statement-level rather than row-level, which is what lets one trigger
# cover TRUNCATE alongside UPDATE and DELETE, and what makes it fire even when
# the statement matches no rows.
#
# Attached as an `after_create` DDL event rather than written into `sql/`,
# because these tables are this service's own: `create_all` makes them at
# startup and `unit_test/conftest.py` rebuilds them per test, and a trigger
# that lived in `sql/` would be dropped by the first rebuild and never come
# back. Here it ships with the table, everywhere the table ships.
#
# Be honest about what this is worth. It is one step weaker than the audit
# log's, because ledger_svc owns `ledger.entries` and could therefore drop its
# own trigger, where no writer can touch `audit.admin_actions` at all. What it
# buys is the audit log's actual argument: a guarantee somebody has to
# deliberately delete a line to break is much harder to break by accident than
# one that was merely never written down. If the ledger ever needs the stronger
# version, the move is to put these tables in `sql/` under the superuser and
# grant ledger_svc INSERT and SELECT and nothing else — at the cost of every
# future column becoming hand-applied SQL. README.md records that trade.

# The message is assembled with `||` rather than RAISE's `%` placeholder,
# which is what `sql/02-schemas.sql` uses. Not a style preference: SQLAlchemy
# runs every DDL string through Python's `%` interpolation before sending it,
# so a literal percent sign in the body raises at create_all time. Doubling it
# would work and would also be the kind of thing somebody quietly un-doubles
# while editing the SQL.
_REJECT_MUTATION = DDL(
    f"""
    CREATE OR REPLACE FUNCTION {SCHEMA}.reject_mutation() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION
            USING ERRCODE = 'restrict_violation',
                  MESSAGE = '{SCHEMA}.entries is append-only; '
                            || TG_OP || ' is not permitted',
                  HINT = 'Reverse a wrong entry by appending the '
                         'transaction that undoes it.';
    END;
    $$;
    """
)

_INSTALL_TRIGGER = DDL(
    f"""
    CREATE TRIGGER entries_append_only
        BEFORE UPDATE OR DELETE OR TRUNCATE ON {SCHEMA}.entries
        FOR EACH STATEMENT EXECUTE FUNCTION {SCHEMA}.reject_mutation();
    """
)

event.listen(Entry.__table__, "after_create", _REJECT_MUTATION)
event.listen(Entry.__table__, "after_create", _INSTALL_TRIGGER)
