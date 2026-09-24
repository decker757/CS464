"""Response contracts.

These generate the OpenAPI schema at /docs, which is the contract Michelle's
balance display codes against for [B-2] #33.

There is no request model in this file, because there is no route that writes.
The write path is `service/posting.py` and the endpoint that will call it
arrives with [T-2] #22, along with the decision about how a service
authenticates to it.

**Amounts are strings, and that is the one deliberate departure from the market
service**, whose `MarketOut` serialises `liquidity_b` as a JSON number. That was
right there: the form compares `b` against `max_platform_loss` arithmetically,
and `"250" + 10` is `"25010"` in a browser. It is wrong here. These are
balances. A JSON number is an IEEE double by the time any frontend has parsed
it, `0.1 + 0.2` is famously not `0.3`, and a credit total that is off by a
floating-point epsilon is a bug report about money. A decimal string is exact,
and [B-2] #33's "integers with consistent formatting" is something the browser
can then produce rather than something it has to recover.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from core.pricing import Side
from model.entities import Entry, TransactionKind


class BalanceOut(BaseModel):
    """What one user holds, as at this request."""

    user_id: uuid.UUID
    account_id: uuid.UUID

    balance: Decimal = Field(
        description=(
            "Available credits, as an exact decimal string. The sum of every "
            "entry on this account and nothing else — there is no stored "
            "balance for this to disagree with."
        ),
        examples=["1000.0000"],
    )

    @field_serializer("balance")
    def _as_string(self, value: Decimal) -> str:
        """Exact on the wire. See the note at the top of this module."""
        return str(value)


class LedgerEntryOut(BaseModel):
    """One side of one movement, as it was written.

    Nothing here can have changed since: `ledger.entries` is append-only, no
    code path updates a row, and a trigger refuses the attempt.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime

    amount: Decimal = Field(
        description=(
            "Signed, as an exact decimal string. Negative took credits out of "
            "this account, positive put them in."
        ),
        examples=["1000.0000"],
    )

    balance_after: Decimal = Field(
        description=(
            "What the account held once this entry had landed, as an exact "
            "decimal string. [4.1] #13's running balance.\n\n"
            "Derived per read by summing every entry up to and including this "
            "one — there is no stored column for it to disagree with, and the "
            "newest entry's value is the same number `/balance` returns. "
            "Anchored to the entry rather than accumulated from today, so a "
            "movement arriving while you page cannot change a figure already "
            "on the screen."
        ),
        examples=["1000.0000"],
    )

    transaction_id: uuid.UUID
    kind: TransactionKind = Field(
        description=(
            "Why the credits moved. New values appear as stories land; treat "
            "an unrecognised one as opaque rather than as an error."
        ),
        examples=[TransactionKind.SIGNUP_GRANT],
    )
    context: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Movement-specific detail, shaped by `kind`. Render what you "
            "recognise and ignore the rest."
        ),
    )

    @field_serializer("amount", "balance_after")
    def _as_string(self, value: Decimal) -> str:
        return str(value)

    @field_validator("created_at")
    @classmethod
    def _always_utc(cls, v: datetime) -> datetime:
        """Guarantee an explicit offset on the way out.

        The same guard the other three services carry. A driver handing back a
        naive datetime would make one entry serialise with a trailing Z and
        another without, leaving the frontend to special-case which.
        """
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v

    @classmethod
    def of(cls, entry: Entry, *, balance_after: Decimal) -> LedgerEntryOut:
        """Flatten an entry and the transaction it belongs to into one row.

        The nesting is real — two entries share one transaction — but nobody
        reading their own history wants it. They want a statement: a date, an
        amount, what it was for, and what was left. `kind` and `context` are
        lifted out of the transaction so that a client renders one list rather
        than walking a tree to find the word "grant".

        `balance_after` arrives as an argument rather than being read off the
        entry, because there is nothing on the entry to read: it is derived a
        page at a time by `service/ledger_service.py`, which is the only layer
        that knows where in the feed this row sits. A `HistoryRow` parameter
        would read better and would have this module import `service`, which is
        the one direction the layering forbids.
        """
        return cls(
            id=entry.id,
            created_at=entry.created_at,
            amount=entry.amount,
            balance_after=balance_after,
            transaction_id=entry.transaction_id,
            kind=entry.transaction.kind,
            context=entry.transaction.context,
        )


class OutcomePriceOut(BaseModel):
    """One outcome's price. [T-1] #21.

    Field for field what `realtime_service`'s `OutcomePrice` puts on the
    socket — `outcome_id`, `position`, `price` — so a client renders a
    snapshot, a price frame and this preview with one function rather than
    three.
    """

    outcome_id: uuid.UUID
    position: int = Field(
        ge=0,
        description=(
            "The outcome's order within the market, so a categorical market "
            "renders the same way twice without a second lookup."
        ),
    )
    price: Decimal = Field(
        description="The marginal price of one share, as an exact decimal string.",
        examples=["0.6234"],
    )

    @field_serializer("price")
    def _as_string(self, value: Decimal) -> str:
        return str(value)


class PreviewOut(BaseModel):
    """What one trade would cost, and how it would move the market. [T-1] #21.

    `state_version` is the quote reference (D-011) and the only one — nothing
    else here is a second answer to "has this market moved". [T-2] #22
    compares it, under its own lock, against the version current when a trade
    is confirmed.
    """

    market_id: uuid.UUID
    state_version: int = Field(
        description=(
            "The book's own counter, the same one `PriceEvent` and the "
            "snapshot report. Not a JSON string: it is a count, not money."
        )
    )

    side: Side
    outcome_id: uuid.UUID

    quantity: Decimal = Field(
        description=(
            "Echoed back exactly as it arrived, trailing zeros and all "
            "(D-038) — `10` comes back as `10`, `10.0000` as `10.0000`. It is "
            "not normalised to scale 4: this field is how a client matches a "
            "quote to the keystroke that asked for it, so it has to be the "
            "string it sent."
        ),
        examples=["10.0000"],
    )
    total: Decimal = Field(
        description=(
            "Signed: negative on a buy, because credits leave the trader; "
            "positive on a sell, because they arrive. Quantized by the same "
            "function [T-2] #22 uses to build its legs, buy rounding the "
            "ceiling and sell the floor, so this is the number that would be "
            "charged."
        ),
        examples=["-133.7042"],
    )
    average_price: Decimal = Field(
        description=(
            "abs(total) / quantity, from the quantized total, ROUND_HALF_UP "
            "at scale 4. Display only — [T-2] #22 charges `total`, never "
            "quantity * average_price."
        ),
        examples=["10.0278"],
    )

    prices: list[OutcomePriceOut] = Field(
        description="The market's current price, every outcome, ordered by position."
    )
    post_trade_prices: list[OutcomePriceOut] = Field(
        description=(
            "The price of every outcome this trade would leave behind, "
            "computed from `core/lmsr.py` and not estimated."
        )
    )

    @field_serializer("quantity", "total", "average_price")
    def _as_string(self, value: Decimal) -> str:
        return str(value)


class OutcomePrice(BaseModel):
    """What one outcome of a market currently costs. [F-9] #112.

    Copied from `realtime_service/model/schemas.py`, field for field, per ADR
    0012 and `docs/api/realtime-service.md`: below that package's bar for
    `shared/`, and `unit_test/model/test_price_event.py` pins the copy against
    the original with `ast` so a drift between the two fails loudly instead of
    silently dropping every event on the consumer's `extra="forbid"`.
    """

    outcome_id: uuid.UUID
    position: int = Field(ge=0)
    price: Decimal = Field(ge=0, le=1)

    @field_serializer("price")
    def _price_as_string(self, value: Decimal) -> str:
        """Exact on the wire, for the reason every other amount in this
        service is a string: a JSON number is an IEEE double by the time a
        browser has parsed it."""
        return str(value)


class PriceEvent(BaseModel):
    """A market's price after something moved it. The Redis payload. [F-9] #112.

    The producer's half of the contract `realtime_service/model/schemas.py`
    defines and validates on the way in with `extra="forbid"`. Copied rather
    than imported — see `OutcomePrice`'s docstring — and pinned the same way.
    """

    model_config = ConfigDict(extra="forbid")

    market_id: uuid.UUID
    state_version: int = Field(ge=0)
    prices: list[OutcomePrice] = Field(min_length=2)
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def _always_utc(cls, v: datetime) -> datetime:
        """A naive value is UTC, and leaves with the offset that says so.

        `SnapshotOut` below does the same to its copy of this field. Without
        it a naive datetime from [T-2] #22 would reach the socket with no
        offset, and every browser would read it as local time. Naive versus
        aware has already caused bugs in this repository (CLAUDE.md).
        """
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v

    @field_serializer("occurred_at")
    def _occurred_at_as_string(self, value: datetime) -> str:
        return value.isoformat()


class SnapshotOut(BaseModel):
    """The authoritative price read. [F-9] #112.

    Byte-for-byte the `price` frame `docs/api/realtime-service.md` pins,
    without its `type` — `market_id`, `state_version`, `prices`,
    `occurred_at` — so a client renders a snapshot and a price frame with one
    function.
    """

    market_id: uuid.UUID
    state_version: int
    prices: list[OutcomePriceOut]
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def _always_utc(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v


class LedgerEntryListResponse(BaseModel):
    """One page of a user's history, newest first."""

    entries: list[LedgerEntryOut]

    next_cursor: str | None = Field(
        default=None,
        description=(
            "Pass back as `cursor` for the next page. Null means this page is "
            "the end of the history. Opaque: echo it unmodified rather than "
            "constructing one."
        ),
    )

    has_more: bool = Field(
        description=(
            "Whether another page exists. Equivalent to `next_cursor` being "
            "non-null, and stated so a client can drive a 'load more' control "
            "without reasoning about the cursor at all."
        ),
    )
