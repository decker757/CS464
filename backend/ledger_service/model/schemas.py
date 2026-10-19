"""Response contracts.

These generate the OpenAPI schema at /docs, which is the contract Michelle's
balance display codes against for [B-2] #33.

Three request models, for the three routes that write: `TradeIn` for [T-2]
#22's `POST /ledger/markets/{market_id}/trades`, `OrderIn` for [T-6] #26's
`POST /ledger/markets/{market_id}/orders`, and `SettlementIn` for [3.4] #12's
`POST /ledger/markets/{market_id}/settlement`. None carries an account, an
amount or a leg — `extra="forbid"` refuses a body naming one — which is the
answer ADR 0009's amendments give to how a caller authenticates to a write
path.

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
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_serializer

from core.clock import as_utc
from core.pricing import Side
from model.entities import Entry, ResultKind, TransactionKind

# `as_utc` stamps a naive value UTC on the way out.
UtcDatetime = Annotated[datetime, AfterValidator(as_utc)]


class BalanceOut(BaseModel):
    """What one user holds, as at this request."""

    user_id: uuid.UUID
    account_id: uuid.UUID | None = Field(
        description=(
            "Never null on `/balances/me`. Null on the admin route for an id "
            "the ledger has never opened an account for, which that route "
            "does not do on its behalf."
        ),
    )

    balance: Decimal = Field(
        description=(
            "The account's credits, including those resting buy orders "
            "reserve, as an exact decimal string. The sum of every entry on "
            "this account and nothing else — there is no stored balance for "
            "this to disagree with."
        ),
        examples=["1000.0000"],
    )
    reserved: Decimal = Field(
        description=(
            "What this user's live buy orders could still spend, summed over "
            "every market: each order's budget less what it has spent. Part "
            "of `balance`, and not an entry (ADR 0024)."
        ),
        examples=["60.0000"],
    )
    available: Decimal = Field(
        description=(
            "`balance - reserved`: what a new buy, a new buy order or the "
            "trade route can spend."
        ),
        examples=["940.0000"],
    )

    @field_serializer("balance", "reserved", "available")
    def _as_string(self, value: Decimal) -> str:
        """Exact on the wire. See the note at the top of this module."""
        return str(value)


class PositionResult(StrEnum):
    """What a settled market did to one held outcome. [3.4] #12.

    The wire's own values. A row the ledger has not settled has no result,
    which is `None` and not a third member.
    """

    PAID_OUT = "paid_out"
    WORTHLESS = "worthless"


class PortfolioPositionOut(BaseModel):
    """One outcome the caller still holds. [T-4] #24, [3.4] #12

    On a market the ledger has not settled, `value` is what selling the whole
    position now would credit — never `quantity * price` (ADR 0018) — and
    `result` and `payout` are null. On a settled market it is the other way
    round: `result` and `payout` are set, and `price`, `value` and
    `unrealized_pnl` are null, because the payout is already in the balance.
    Every key is always present. See `GET /ledger/portfolio/me` in
    `docs/api/ledger-service.md`.
    """

    market_id: uuid.UUID
    outcome_id: uuid.UUID
    outcome_position: int = Field(
        ge=0, description="The outcome's order within its market."
    )

    quantity: Decimal
    cost_basis: Decimal = Field(
        description="What this position cost to build, at average cost."
    )
    average_entry_price: Decimal = Field(
        description="cost_basis / quantity, ROUND_HALF_UP at scale 4."
    )
    price: Decimal | None = Field(
        description=(
            "The outcome's marginal price — informational only, and equal "
            "to the snapshot's for the same state_version. Not what `value` "
            "is computed from. Null on a settled row."
        )
    )
    value: Decimal | None = Field(
        description=(
            "What selling the whole position now would credit (ADR 0018), "
            "not quantity * price. Null on a settled row, whose payout is "
            "already in the balance."
        )
    )
    unrealized_pnl: Decimal | None = Field(
        description="value - cost_basis. Null on a settled row."
    )
    result: PositionResult | None = Field(
        description=(
            "`paid_out` for the winning outcome of a settled market, "
            "`worthless` for any other. Null until the market is settled."
        )
    )
    payout: Decimal | None = Field(
        description=(
            "What this row was paid: its quantity on a `paid_out` row, "
            "`0.0000` on a `worthless` one. Null until the market is settled."
        )
    )
    state_version: int = Field(
        description="This market's book version this row was valued at."
    )

    @field_serializer("quantity", "cost_basis", "average_entry_price")
    def _as_string(self, value: Decimal) -> str:
        return str(value)

    @field_serializer("price", "value", "unrealized_pnl", "payout")
    def _as_optional_string(self, value: Decimal | None) -> str | None:
        return str(value) if value is not None else None


class PortfolioOut(BaseModel):
    """The caller's balance, every position they still hold, and their profit
    and loss. [T-4] #24, [B-1] #359

    `positions_value` is the sum of the positions' `value`, and `net_worth`
    is `balance + positions_value`, exactly — nothing is rounded after the
    valuation (ADR 0018). `total_pnl` is `net_worth - starting_credits`,
    exactly, and is also `realized_pnl + unrealized_pnl`.
    """

    user_id: uuid.UUID
    account_id: uuid.UUID

    balance: Decimal
    positions_value: Decimal = Field(description="The sum of the positions' values.")
    net_worth: Decimal = Field(description="balance + positions_value, exactly.")

    starting_credits: Decimal = Field(
        description=(
            "What this user's signup grant actually paid them, read from "
            "that transaction's entry. Not the STARTING_CREDITS setting, "
            "which can change without rewriting an entry."
        )
    )
    realized_pnl: Decimal = Field(
        description=(
            "Profit and loss already banked in the balance: what the sells "
            "paid less the cost basis they released, plus what settlement "
            "paid less the basis of the holdings it settled."
        )
    )
    unrealized_pnl: Decimal = Field(
        description=(
            "The sum of the positions' own `unrealized_pnl`. A settled row "
            "contributes nothing: its payout is realised."
        )
    )
    total_pnl: Decimal = Field(
        description="realized_pnl + unrealized_pnl, and net_worth - starting_credits."
    )

    positions: list[PortfolioPositionOut] = Field(
        description=(
            "Only outcomes with quantity > 0, ordered by market_id then "
            "outcome_position. A position sold to zero is not shown."
        )
    )

    @field_serializer(
        "balance",
        "positions_value",
        "net_worth",
        "starting_credits",
        "realized_pnl",
        "unrealized_pnl",
        "total_pnl",
    )
    def _as_string(self, value: Decimal) -> str:
        return str(value)


@dataclass(frozen=True)
class TradeFields:
    """The five fields a history row carries for a trade or a payout. [T-5] #25,
    [3.4] #12.

    All null by default: that is the grant's row, and any kind the history
    does not recognise. `service/ledger_service.py` fills them in; they live
    here so `LedgerEntryOut.of` can take them without importing `service`.
    """

    market_id: uuid.UUID | None = None
    outcome_id: uuid.UUID | None = None
    side: Side | None = None
    quantity: Decimal | None = None
    average_price: Decimal | None = None


class LedgerEntryOut(BaseModel):
    """One side of one movement, as it was written.

    Nothing here can have changed since: `ledger.entries` is append-only, no
    code path updates a row, and a trigger refuses the attempt.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: UtcDatetime

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
            "on the screen. Entries are listed in the order they committed, "
            "so this is always a balance the account actually held (#187)."
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

    market_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Set on a `trade_buy`, `trade_sell` or `settlement` row; `null` "
            "on every other kind, the grant included. [T-5] #25, [3.4] #12."
        ),
    )
    outcome_id: uuid.UUID | None = Field(
        default=None,
        description="Set alongside `market_id`, and null under the same rule.",
    )
    side: Side | None = Field(
        default=None,
        description=(
            "`buy` or `sell`, taken from `kind` rather than from `context`. "
            "Null on every row that is not a trade, a payout included."
        ),
    )
    quantity: Decimal | None = Field(
        default=None,
        description=(
            "The trade's quantity, or on a `settlement` row the winning "
            "shares paid out, at scale 4 whatever the trader sent. Null on "
            "every other row."
        ),
    )
    average_price: Decimal | None = Field(
        default=None,
        description=(
            "The average fill price, `|amount| ÷ quantity` rounded half-up "
            "at scale 4 — not the marginal price the portfolio and the "
            "snapshot report. Null on every row that is not a trade, a "
            "payout included."
        ),
    )

    @field_serializer("amount", "balance_after")
    def _as_string(self, value: Decimal) -> str:
        return str(value)

    @field_serializer("quantity", "average_price")
    def _as_optional_string(self, value: Decimal | None) -> str | None:
        return str(value) if value is not None else None

    @classmethod
    def of(
        cls,
        entry: Entry,
        *,
        balance_after: Decimal,
        trade: TradeFields = TradeFields(),
    ) -> LedgerEntryOut:
        """Flatten an entry and the transaction it belongs to into one row.

        The nesting is real — two entries share one transaction — but nobody
        reading their own history wants it. They want a statement: a date, an
        amount, what it was for, and what was left. `kind` and `context` are
        lifted out of the transaction so that a client renders one list rather
        than walking a tree to find the word "grant".

        `balance_after` and `trade` arrive as arguments rather
        than being read off the entry, because there is nothing on the entry to
        read them from: they are derived a page at a time by
        `service/ledger_service.py`, which is the only layer that knows where
        in the feed this row sits and what its transaction's `kind` and
        `context` mean. A `HistoryRow` parameter would read better and would
        have this module import `service`, which is the one direction the
        layering forbids. [T-5] #25.
        """
        return cls(
            id=entry.id,
            created_at=entry.created_at,
            amount=entry.amount,
            balance_after=balance_after,
            transaction_id=entry.transaction_id,
            kind=entry.transaction.kind,
            context=entry.transaction.context,
            market_id=trade.market_id,
            outcome_id=trade.outcome_id,
            side=trade.side,
            quantity=trade.quantity,
            average_price=trade.average_price,
        )


class OutcomePriceOut(BaseModel):
    """One outcome's price. [T-1] #21.

    Field for field what `realtime_service`'s `OutcomePrice` puts on the
    socket — `outcome_id`, `position`, `price` — so a client renders a
    snapshot, a price frame and this preview with one function rather than
    three. That includes `price`'s bounds, the one invariant among the three:
    a price outside [0, 1] is refused on the socket and must not ship here.
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
        ge=0,
        le=1,
        description="The marginal price of one share, as an exact decimal string.",
        examples=["0.7216"],
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
            "Echoed back at the scale it arrived with, trailing zeros and "
            "all (D-038) — `10` comes back as `10`, `10.0000` as `10.0000`. "
            "It is not normalised to scale 4: this field is how a client "
            "matches a quote to the keystroke that asked for it. Compare it "
            "as a decimal rather than as a string — the scale survives the "
            "round trip, the exact characters do not, so `.5` comes back as "
            "`0.5` and `10.` as `10`."
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
        examples=["-7.3152"],
    )
    average_price: Decimal = Field(
        description=(
            "abs(total) / quantity, from the quantized total, ROUND_HALF_UP "
            "at scale 4. **Do not render it as a fraction of a credit, and "
            "do not assume it lies strictly between 0 and 1.** LMSR prices "
            "are a softmax, so a share is worth under one credit and an "
            "ordinary trade averages accordingly — but this is derived from "
            "a total that has already been rounded to a tick, and both ends "
            "escape. The smallest buy there is, 0.0001 shares, costs a "
            "fraction of a tick and is charged the whole one (D-039), which "
            "divides out to exactly 1.0000; on a skewed book the engine's "
            "last digit can carry a sub-tick cost over a tick boundary "
            "(D-044) and it reads higher still. At the other end, a large "
            "buy in a saturated outcome can cost one tick in total and "
            "divide down to 0.0000. Display only — [T-2] #22 charges "
            "`total`, never quantity * average_price."
        ),
        examples=["0.7315"],
    )
    payout_if_wins: Decimal | None = Field(
        description=(
            "What this buy pays if its outcome wins: the quantity at scale 4, "
            "because a winning share pays one credit. This trade alone, not "
            "the position it adds to. Null on a sell, which is paid `total` "
            "now and has nothing left to win."
        ),
        examples=["10.0000"],
    )
    profit_if_wins: Decimal | None = Field(
        description=(
            "`payout_if_wins` less the credits this buy costs, which is "
            "`payout_if_wins + total`, exact at scale 4 with no rounding of "
            "its own. Can be zero or a tick below on a buy charged its whole "
            "quantity, such as the smallest buys or a buy of an outcome "
            "priced near one. Null on a sell."
        ),
        examples=["2.6848"],
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

    @field_serializer("payout_if_wins", "profit_if_wins")
    def _as_optional_string(self, value: Decimal | None) -> str | None:
        return str(value) if value is not None else None


class TradeIn(BaseModel):
    """A trade order, a buy or a sell. [T-2] #22, [T-3] #23.

    `extra="forbid"`, and that is the whole of ADR 0009's amendment: the
    route "takes no account, no amount and no leg", which is only true if a
    body naming one is refused rather than silently dropped — Pydantic's
    default is `extra="ignore"`. `total` is in that refusal too, because it
    is the field a client would most plausibly echo back from a preview.

    `side` is `core.pricing.Side`: anything else is a 422 here rather than a
    refusal a layer down, and the route passes the enum straight through.

    `state_version` is required, not optional with a default — an optional
    staleness field would let a client silently opt out of the only
    staleness protection a trade has.
    """

    model_config = ConfigDict(extra="forbid")

    outcome_id: uuid.UUID
    side: Side = Field(
        description="`buy` or `sell`; anything else is 422."
    )
    quantity: Decimal = Field(
        gt=0,
        max_digits=18,
        decimal_places=4,
        description=(
            "Shares to trade. At most four decimal places (D-038) — a fifth "
            "is 422 rather than rounded. At most 18 digits in all, the width "
            "of `Numeric(18, 4)` and the preview's own ceiling: a quantity "
            "wider than the column could never be written as a share count."
        ),
    )
    state_version: int = Field(
        description=(
            "The `state_version` the preview quoted. Compared, under the "
            "book's own lock, for strict equality against the market's "
            "current one; a mismatch either direction is `409 quote_stale`."
        )
    )
    idempotency_key: str = Field(
        min_length=1,
        max_length=175,
        description=(
            "One trade, once, however many times this is sent. The stored "
            "key is derived — `trade:<user_id>:<market_id>:<this value>` — "
            "so this string only has to be unique to the caller who sent it. "
            "Bounded so the derived key always fits `transactions."
            "idempotency_key`'s column width (255): 80 characters of "
            "derivation prefix, 175 left for this value."
        ),
    )


class TradeOut(BaseModel):
    """What one trade did. [T-2] #22, [T-3] #23.

    Built from `Transaction.context` by `service/trading.py::result_of`, the
    one function the fresh path and both replay paths all call — so a retry
    cannot return a different shape from the original, and a retry returns
    this byte-for-byte.

    Carries no prices. A replayed price was true once and is a lie
    afterwards, unlike `total`, which is what the trader was charged for
    ever — prices are the realtime contract's, the `price` frame or the
    snapshot route beside this one.
    """

    transaction_id: uuid.UUID
    user_id: uuid.UUID
    market_id: uuid.UUID
    outcome_id: uuid.UUID
    side: Side
    quantity: Decimal = Field(
        description=(
            "Echoed back exactly as it was sent, trailing zeros and all, the "
            "same as the preview's (D-038) — `10` stays `10`, not `10.0000`. "
            "A replay compares it numerically, so either spelling is the "
            "same trade."
        )
    )
    total: Decimal = Field(
        description=(
            "Signed and quantized exactly as `PreviewOut.total` is — "
            "negative on a buy, because credits leave the trader, and "
            "positive on a sell, because credits arrive. This is the number "
            "that was charged or paid, the same one the preview quoted."
        )
    )
    state_version: int = Field(
        description="The book's counter after this trade, not before it."
    )
    book_state_version: int = Field(
        description=(
            "The book's counter after this trade and every resting order it "
            "filled (ADR 0024): the version of the one `price` frame the trade "
            "publishes. Equal to `state_version` when it filled none."
        )
    )

    @field_serializer("quantity", "total")
    def _as_string(self, value: Decimal) -> str:
        return str(value)


class OrderIn(BaseModel):
    """A limit order, a buy or a sell. [T-6] #26, ADR 0024.

    `extra="forbid"` over five fields, `TradeIn`'s reason: the body names a
    price and a quantity, never an account or an amount. The budget is
    computed from them and the account is the token's. There is no
    `state_version`: the limit is what protects the trader (ADR 0024, "A
    placement takes no `state_version`"), so a body sending one is refused.
    """

    model_config = ConfigDict(extra="forbid")

    outcome_id: uuid.UUID
    side: Side = Field(description="`buy` or `sell`; anything else is 422.")
    limit_price: Decimal = Field(
        gt=0,
        lt=1,
        decimal_places=4,
        description=(
            "The price on this order's own outcome: a buy fills at or below "
            "it, a sell at or above. Strictly between 0 and 1, at most four "
            "decimal places; a fifth is 422 rather than rounded."
        ),
    )
    quantity: Decimal = Field(
        gt=0,
        max_digits=18,
        decimal_places=4,
        description="Shares, by the trade route's rules: `> 0`, four places, 18 digits.",
    )
    idempotency_key: str = Field(
        min_length=1,
        max_length=175,
        description=(
            "One order, once. The stored key is "
            "`order:<user_id>:<market_id>:<this value>`, so this string only "
            "has to be unique to the caller, and is bounded as the trade's is."
        ),
    )


class OrderOut(BaseModel):
    """One limit order as it stands now. [T-6] #26, ADR 0024.

    The order object of `docs/api/ledger-service.md`. Amounts are decimal
    strings at scale 4, as everywhere here, in a python-mode dump as well as
    on the wire.
    """

    id: uuid.UUID
    market_id: uuid.UUID
    outcome_id: uuid.UUID
    outcome_position: int = Field(
        description="0 or 1. Labels are market_service's, as in the portfolio."
    )
    side: Side
    limit_price: Decimal
    quantity: Decimal
    filled_quantity: Decimal = Field(description="Shares filled so far, across every fill.")
    average_fill_price: Decimal | None = Field(
        description="What the fills cost or paid per share, half-up. Null until the first fill."
    )
    reserved_credits: Decimal | None = Field(
        description=(
            "A buy only: its budget less what it has spent. Null on a sell; "
            "`0.0000` once the order is not live."
        )
    )
    reserved_shares: Decimal | None = Field(
        description=(
            "A sell only: the shares it still holds back. Null on a buy; "
            "`0.0000` once the order is not live."
        )
    )
    status: Literal["open", "filled", "cancelled", "released"] = Field(
        description="`released`: the market has a result. Derived on every read."
    )
    created_at: UtcDatetime
    cancelled_at: UtcDatetime | None

    @field_serializer("limit_price", "quantity", "filled_quantity")
    def _as_string(self, value: Decimal) -> str:
        return str(value)

    @field_serializer("average_fill_price", "reserved_credits", "reserved_shares")
    def _as_optional_string(self, value: Decimal | None) -> str | None:
        return str(value) if value is not None else None


class FillOut(BaseModel):
    """One fill a placement made: a trade in the owner's history."""

    transaction_id: uuid.UUID
    quantity: Decimal
    total: Decimal = Field(description="Signed as the trade route signs it.")
    state_version: int = Field(description="The book's counter after this fill.")

    @field_serializer("quantity", "total")
    def _as_string(self, value: Decimal) -> str:
        return str(value)


class PlacementOut(BaseModel):
    """What a placement did, or, on a retry, the order as it stands now and the
    fills the first request made. [T-6] #26."""

    order: OrderOut
    fills: list[FillOut] = Field(
        description="What this request filled on arrival. Empty when nothing did."
    )
    book_state_version: int = Field(
        description=(
            "The book's counter after this whole request, including any "
            "resting orders the placement's cascade filled, so it can be "
            "higher than the highest `state_version` in `fills`. The counter "
            "the order was placed against when nothing filled."
        )
    )


class OrderListOut(BaseModel):
    """The caller's live orders, newest first. [T-6] #26."""

    orders: list[OrderOut]


class SettlementIn(BaseModel):
    """The body of a settlement: nothing. [3.4] #12, ADR 0009's second amendment.

    No fields and `extra="forbid"`, so a body naming an outcome, an account or
    an amount is `422 invalid_request` rather than silently dropped — a
    dropped `outcome_id` would let a client believe it had chosen the winner.
    The route never reads it; it exists to refuse what it cannot hold.
    """

    model_config = ConfigDict(extra="forbid")


class SettlementOut(BaseModel):
    """What one settlement paid. [3.4] #12.

    Built from `service/settlement.py::SettlementResult`, which is the same on
    the request that pays and on every repeat, so a repeat returns this
    byte-for-byte.
    """

    market_id: uuid.UUID
    outcome_id: uuid.UUID = Field(
        description=(
            "The winning outcome. On a repeat, the one this service "
            "recorded, whatever market_service reports now."
        )
    )
    recorded_at: datetime = Field(
        description="When the settlement was recorded, with its UTC offset."
    )
    holders_paid: int = Field(
        description=(
            "One payout for each holder of more than zero winning shares; "
            "0 if nobody held the winner."
        )
    )
    total_paid: Decimal = Field(
        description="What the payouts add up to: one credit per winning share."
    )
    residue: Decimal = Field(
        description=(
            "What was left in the market's pool after the payouts, signed. "
            "Positive went to the platform, negative came from it, and the "
            "pool ends at exactly 0.0000 either way."
        )
    )


# One model, two names. `OutcomePriceOut` and `OutcomePrice` were declared
# separately and field for field the same — same three fields, same bounds,
# same `str(value)` serializer — with only `OutcomePrice` pinned against
# `realtime_service` by `test_price_event.py`. Nothing pinned the two local
# copies against each other, so a bound changed on one would have diverged
# silently from the other; and `SnapshotOut` used one while `PriceEvent` used
# the other, which is exactly the pair both docs pages promise are
# byte-for-byte identical. The alias keeps the name the pin reads and the
# name the snapshot was written against, over one definition.
OutcomePrice = OutcomePriceOut


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
    occurred_at: UtcDatetime

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
    # The same constraints `PriceEvent` puts on these two fields. They were
    # absent here, which made "byte-for-byte the `price` frame" a promise the
    # models themselves disagreed with: a client validating one shape and
    # handed the other would accept what the socket refuses. Harmless today
    # because `book_prices` enforces the floor upstream, and invisible the day
    # a second caller does not go through it.
    state_version: int = Field(ge=0)
    prices: list[OutcomePriceOut] = Field(min_length=2)
    occurred_at: UtcDatetime

    @field_serializer("occurred_at")
    def _occurred_at_as_string(self, value: datetime) -> str:
        """`.isoformat()`, the same call `PriceEvent` makes, and for the same
        reason the docstring above gives.

        Without this pydantic writes its own RFC-3339 form, which spells UTC
        as a trailing `Z` where `.isoformat()` spells it `+00:00`. Both are
        valid and they are not the same string, so "byte-for-byte the `price`
        frame" was false for this one field — and the test that guards it
        only asserted the offset was present, which is true of both.
        """
        return value.isoformat()


class PricePointOut(BaseModel):
    """One price the market has had. [BE][2.4] #63.

    A `price` socket frame without its `type` and `market_id`, so a chart
    draws the history and appends live frames with one function, and drops any
    frame whose `state_version` it already holds.
    """

    state_version: int = Field(
        ge=0,
        description="0 is the book's opening. Each trade after it adds one.",
    )
    occurred_at: datetime = Field(
        description=(
            "When the trade's entries landed, or the book's `opened_at` for "
            "`state_version` 0. Dedupe on `state_version`, never on this."
        )
    )
    prices: list[OutcomePriceOut] = Field(
        min_length=2, description="One price per outcome, ordered by position."
    )

    @field_serializer("occurred_at")
    def _occurred_at_as_string(self, value: datetime) -> str:
        """`.isoformat()`, as `SnapshotOut` does, so the string is the frame's."""
        return value.isoformat()


class PriceHistoryOut(BaseModel):
    """Every price a market has had, one point per trade. [BE][2.4] #63.

    A market this service holds no book for reads as an empty `points` and
    nulls. DECISIONS.md, "Price history takes any valid token; the trade feed
    takes an administrator's", Notes.
    """

    market_id: uuid.UUID
    points: list[PricePointOut] = Field(
        description="Oldest first. Settlement adds no point: the series ends at the last trade."
    )
    result: ResultKind | None = Field(
        description=(
            "Null until the ledger records how the market ended; then that "
            "record's kind, today always `settled`. The exposure read's values."
        )
    )
    winning_outcome_id: uuid.UUID | None = Field(
        description="Null until the market is settled; then the outcome that paid."
    )
    result_recorded_at: datetime | None = Field(
        description="Null until the market is settled; then the settlement's `recorded_at`."
    )

    @field_serializer("result_recorded_at")
    def _result_recorded_at_as_string(self, value: datetime | None) -> str | None:
        return None if value is None else value.isoformat()


class ExposureOutcomeOut(BaseModel):
    """One outcome's share of what resolving the market would cost. [2.2] #6"""

    outcome_id: uuid.UUID
    position: int = Field(ge=0, description="The outcome's order within its market.")
    shares_outstanding: Decimal = Field(
        description="Every trader's shares in this outcome, summed."
    )
    payout_if_wins: Decimal | None = Field(
        description="One credit per share outstanding. Null once the market has a result."
    )
    residue_if_wins: Decimal | None = Field(
        description=(
            "pool - payout_if_wins. Positive goes to the platform at "
            "settlement; negative is what the platform tops up. Null once "
            "the market has a result."
        )
    )

    @field_serializer("shares_outstanding")
    def _as_string(self, value: Decimal) -> str:
        return str(value)

    @field_serializer("payout_if_wins", "residue_if_wins")
    def _as_optional_string(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


class ExposureOut(BaseModel):
    """What resolving a market would pay out, for an administrator. [2.2] #6

    A market this service holds no book for reads as nulls and an empty
    `outcomes`, with `payout_exceeds_pool` false. DECISIONS.md, "Exposure on a
    market with no book is a 200 with nothing in it, and writes nothing".
    """

    market_id: uuid.UUID
    opened_at: UtcDatetime | None = Field(
        description="When this service opened the book. Null means it never has."
    )
    state_version: int | None = Field(
        description="Which trade this read reflects. Null when there is no book."
    )
    seed_subsidy: Decimal | None = Field(
        description="What the platform put up at book creation."
    )
    pool: Decimal | None = Field(
        description=(
            "What the market pool holds now, derived from its entries. Null "
            "when there is no book, and once the market has a result: "
            "settlement drained it."
        )
    )
    payout_exceeds_pool: bool = Field(
        description=(
            "True when any outcome's payout is strictly greater than `pool`. "
            "False once the market has a result."
        )
    )
    result: ResultKind | None = Field(
        description=(
            "Null until the ledger records how the market ended; then that "
            "record's kind, today always `settled`."
        )
    )
    winning_outcome_id: uuid.UUID | None = Field(
        description="Null until the market is settled; then the outcome that paid."
    )
    outcomes: list[ExposureOutcomeOut] = Field(
        description="Every outcome, ordered by position. Empty when there is no book."
    )

    @field_serializer("opened_at")
    def _opened_at_as_string(self, value: datetime | None) -> str | None:
        return None if value is None else value.isoformat()

    @field_serializer("seed_subsidy", "pool")
    def _as_string(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)


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


class MarketTradeOut(BaseModel):
    """One trade in a market's feed, for an administrator. [BE][2.4] #63.

    The trader's own history row minus `balance_after`, which means nothing on
    a pool account, plus `user_id`. DECISIONS.md, "The market's trade feed
    reads the pool account's feed, filtered to the trade kinds, and its rows
    are built by `trade_fields_of`".
    """

    transaction_id: uuid.UUID
    created_at: UtcDatetime
    user_id: uuid.UUID = Field(
        description=(
            "The trader. An id, not a name: this service holds no user table. "
            "DECISIONS.md, \"A market's trades carry `user_id`, not "
            "`username`\"."
        )
    )
    outcome_id: uuid.UUID
    side: Side
    quantity: Decimal = Field(description="Shares, at scale 4.")
    average_price: Decimal = Field(
        description=(
            "`|total| ÷ quantity` rounded half-up at scale 4, the same "
            "function as the trader's own history row."
        )
    )
    total: Decimal = Field(
        description=(
            "Signed for the trader, as `TradeOut.total` is: negative on a "
            "buy, positive on a sell."
        )
    )
    state_version: int = Field(description="The book's counter after this trade.")

    @field_serializer("quantity", "average_price", "total")
    def _as_string(self, value: Decimal) -> str:
        return str(value)


class MarketTradeListResponse(BaseModel):
    """One page of a market's trades, newest first."""

    trades: list[MarketTradeOut]

    next_cursor: str | None = Field(
        default=None,
        description=(
            "Pass back as `cursor` for the next page. Null means this page is "
            "the end. Opaque: echo it unmodified rather than constructing one."
        ),
    )

    has_more: bool = Field(
        description="Equivalent to `next_cursor` being non-null."
    )


class LeaderboardStandingOut(BaseModel):
    """A user's rank, username and net worth on one stored ranking. [L-1] #38"""

    rank: int = Field(description="1..N with no ties: the order is the rank.")
    username: str
    net_worth: Decimal = Field(
        description="The portfolio's `net_worth`, at scale 4, as of `computed_at`."
    )

    @field_serializer("net_worth")
    def _as_string(self, value: Decimal) -> str:
        return str(value)


class LeaderboardRowOut(LeaderboardStandingOut):
    """One user's place on the board. [L-1] #38, [BE][L-2] #269

    The standing's three fields and `is_me`, true on the caller's own row. The
    stored `user_id` is never sent: `is_me` is the only thing on a row that
    depends on the caller, and it identifies them only to themselves.
    """

    is_me: bool = Field(
        description="True on the caller's own row, false on every other."
    )


class LeaderboardPageOut(BaseModel):
    """One page of one stored ranking. [L-1] #38, [L-3] #40, [BE][L-2] #269"""

    computed_at: datetime | None = Field(
        description="When this ranking's valuation began. Null before any refresh."
    )
    stale: bool = Field(
        description="True when `computed_at` is more than two refresh intervals old."
    )
    ranked_users: int = Field(
        description="Users in the whole ranking, the same on every page of it."
    )
    me: LeaderboardStandingOut | None = Field(
        description=(
            "The caller's own row in the ranking this page came from, on this "
            "page or not. Null when the caller is not in that ranking, and the "
            "response does not say why."
        )
    )
    rows: list[LeaderboardRowOut]
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Pass back as `cursor` for the next page of this same ranking. "
            "Null means this page is the end. Opaque: echo it unmodified."
        ),
    )
    has_more: bool = Field(
        description="Equivalent to `next_cursor` being non-null."
    )


class InvalidFieldOut(BaseModel):
    """One part of a request that failed validation, as pydantic reported it."""

    loc: list[str | int] = Field(
        description=(
            "Where the problem is: the part of the request first, then the "
            "field, e.g. `[\"query\", \"quantity\"]`, `[\"path\", "
            "\"market_id\"]` or `[\"body\", \"side\"]`."
        ),
    )
    msg: str = Field(description="A sentence for a person. Do not parse it.")
    type: str = Field(
        description=(
            "pydantic's stable name for the problem, e.g. `greater_than` or "
            "`extra_forbidden`. Branch on this rather than on `msg`."
        ),
    )


class ErrorOut(BaseModel):
    """The inside of the envelope every ledger error carries."""

    code: str = Field(
        description=(
            "Stable and `snake_case`. `invalid_request` when the request did "
            "not match the route's parameters or body; a route's own 422 "
            "codes are listed in its description."
        ),
        examples=["invalid_request"],
    )
    message: str
    details: list[InvalidFieldOut] | None = Field(
        default=None,
        description=(
            "On `invalid_request`, every part of the request that was wrong. "
            "Absent on a route's own 422 codes."
        ),
    )


class ValidationErrorOut(BaseModel):
    """Every ledger 422, whichever layer refused the request: `invalid_request`
    with `details`, or one of the route's own codes without them."""

    error: ErrorOut
