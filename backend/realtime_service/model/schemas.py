"""The wire contract: `PriceEvent` in from Redis, frames out to the socket. [F-2] #42 [X-4] #37

OpenAPI cannot describe a socket, so `docs/api/realtime-service.md` is the
other half and nothing checks that the two agree: change a field here, change
it there. The ledger's copy of `PriceEvent` is pinned to these field names (D-048).

Do not add a "prices sum to one" check. This service holds no `q` or `b`,
cannot recompute the number, and relays what the owner said; it checks shape only.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class OutcomePrice(BaseModel):
    """What one outcome of a market currently costs."""

    outcome_id: uuid.UUID = Field(
        description="`market.market_outcomes.id`. Stable across the market's life."
    )

    position: int = Field(
        ge=0,
        description=(
            "The outcome's order within the market, server-assigned at drafting "
            "and stable afterwards. Carried so a client can render outcomes in "
            "the order the administrator arranged without holding a second "
            "lookup, and so a categorical market renders the same way twice."
        ),
    )

    price: Decimal = Field(
        ge=0,
        le=1,
        description=(
            "The marginal price of one share, as an exact decimal string. Also "
            "the market's implied probability of this outcome, which is the "
            "same number read a different way — [X-3] #36 displays both."
        ),
        examples=["0.6234"],
    )

    @field_serializer("price")
    def _price_as_string(self, value: Decimal) -> str:
        """A decimal string, never a JSON number. docs/api/realtime-service.md."""
        return str(value)


class PriceEvent(BaseModel):
    """A market's prices after a trade moved them. The Redis payload.

    The only shape this service relays; `service/bus.py` logs and drops
    anything else on the channel.
    """

    # A producer that adds a field is making a contract change, so it fails
    # here rather than being relayed silently. docs/api/realtime-service.md.
    model_config = ConfigDict(extra="forbid")

    market_id: uuid.UUID

    state_version: int = Field(
        ge=0,
        description=(
            "A counter that increases by one every time this market's state "
            "changes, incremented in the SAME transaction as the trade that "
            "moved it. This is the whole anti-staleness mechanism and it is "
            "the one field a producer cannot get away with approximating.\n\n"
            "A wall-clock timestamp will not do the job. Two trades can commit "
            "inside the same clock tick, and server clocks disagree with each "
            "other by more than the interval between trades. A counter that "
            "only the row lock can advance is ordered by construction.\n\n"
            "Consumed by [T-1] #21 as the quote reference a confirm step checks "
            "for staleness, and by [X-4] #37 to discard an event older than the "
            "price already on screen."
        ),
    )

    prices: list[OutcomePrice] = Field(
        min_length=2,
        description=(
            "Every outcome of the market, not only the one that was traded — a "
            "trade moves all of them, and a client that received one would "
            "render a market whose prices no longer sum to one.\n\n"
            "Two at minimum, because a one-outcome market is not a market; "
            "`market_service.core.opening_prices` refuses to price one for the "
            "same reason."
        ),
    )

    occurred_at: datetime = Field(
        description=(
            "When the trade committed, from the producer's clock. Timezone-aware, "
            "always UTC.\n\n"
            "For display and debugging only. It must never be used to order two "
            "events — that is `state_version`'s job, and CLAUDE.md records that "
            "naive-versus-aware timestamps have already caused bugs in this "
            "repository."
        )
    )

    @field_serializer("occurred_at")
    def _occurred_at_as_string(self, value: datetime) -> str:
        return value.isoformat()

    def frame(self) -> dict[str, Any]:
        """This event as the server-to-client frame.

        Flat, so it is the snapshot body plus a `type`.
        docs/api/realtime-service.md.
        """
        return {"type": "price", **self.model_dump(mode="json")}


# ---------------------------------------------------------------------------
# Client to server
# ---------------------------------------------------------------------------


class ClientCommand(BaseModel):
    """What a connected client may ask for.

    Deliberately no "current price" command: the snapshot belongs to the ledger,
    which owns `q`, not to this relay's last-seen event. ADR 0010. Unknown
    fields are ignored so a newer client does not break.
    """

    model_config = ConfigDict(extra="ignore")

    action: Literal["subscribe", "unsubscribe"]
    market_id: uuid.UUID


# ---------------------------------------------------------------------------
# Server to client
# ---------------------------------------------------------------------------
# Plain dicts, because nothing validates an outbound frame. Kept here so every
# shape on the wire is in one file.


def subscribed_frame(market_id: uuid.UUID) -> dict[str, Any]:
    """Acknowledges a subscription, so it is not mistaken for a dropped command."""
    return {"type": "subscribed", "market_id": str(market_id)}


def unsubscribed_frame(market_id: uuid.UUID) -> dict[str, Any]:
    """Acknowledges an unsubscription."""
    return {"type": "unsubscribed", "market_id": str(market_id)}


def error_frame(code: str, message: str) -> dict[str, Any]:
    """An error, in the same `{"error": {"code", "message"}}` envelope the HTTP services use."""
    return {"type": "error", "error": {"code": code, "message": message}}
