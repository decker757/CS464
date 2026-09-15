"""The wire contract, in both directions. [F-2] #42

Three audiences read this file, which is why it is the longest one in the
service:

- **[T-2] #22 / [T-3] #23** publish `PriceEvent` to Redis when a trade commits.
- **[X-4] #37** receives the frames below over the socket.
- This service validates the first into the second and relays nothing else.

Unlike every other `schemas.py` in this repository, almost none of this reaches
`/docs`: OpenAPI has no vocabulary for a WebSocket. `docs/api/realtime-service.md`
is the human-readable half, and it is generated from nothing — if you change a
field here, change it there too, because there is no schema check that will
catch the two disagreeing.

**This service validates shape and relays. It does not price anything.** There
is no assertion here that the prices sum to one, and there must not be: LMSR
marginal prices do sum to one, but this service does not own that invariant,
holds no `q` and no `b`, and cannot recompute the number to check it. A relay
that rejected a correct price over a rounding tolerance would be worse than one
that relays whatever the authority said. The structural checks below are the
ones a relay can honestly make — is this a UUID, is this an integer, is this a
number between zero and one — and the economics belong to the producer.
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
        """Exact on the wire, for the reason `ledger_service` sends balances as
        strings: a JSON number is an IEEE double by the time a browser has
        parsed it. The ledger is careful to keep its arithmetic exact, and
        handing the result through a double on the last hop throws that away.
        """
        return str(value)


class PriceEvent(BaseModel):
    """A market's price after something moved it. The Redis payload.

    This is the contract [T-2] #22 publishes and the only shape this service
    will relay. Anything else on the channel is logged and dropped — see
    `service/bus.py`.
    """

    # Rejects a producer that adds a field and forgets to say so, rather than
    # silently relaying something the client has no contract for. The cost is
    # that adding a field means changing this file, which is the intended cost:
    # it is a contract change and it should not be possible to make one by
    # accident from another repository directory.
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

        The envelope is the event plus a `type`, rather than a nested object,
        so a client's dispatch is one field lookup and the price fields are not
        one level deeper on the socket than they are in the snapshot response.
        """
        return {"type": "price", **self.model_dump(mode="json")}


# ---------------------------------------------------------------------------
# Client to server
# ---------------------------------------------------------------------------


class ClientCommand(BaseModel):
    """What a connected client may ask for.

    Two actions and one argument. There is deliberately no "give me the current
    price" command: a snapshot is an authoritative read of state this service
    does not hold, and answering it from the last event this process happened
    to see would hand a client that reconnected to a freshly started replica an
    empty answer with the same shape as a true one. The snapshot is an HTTP
    endpoint on the service that owns `q`. ADR 0010, and `docs/api/realtime-service.md`
    spells out the open-page and reconnect sequences.
    """

    model_config = ConfigDict(extra="ignore")

    action: Literal["subscribe", "unsubscribe"]
    market_id: uuid.UUID


# ---------------------------------------------------------------------------
# Server to client
# ---------------------------------------------------------------------------
#
# Built as plain dicts by the helpers below rather than as models, because
# nothing validates an outbound frame and a model whose only use is
# `model_dump` is a class standing where a function belongs. They live here
# rather than in the controller so that every shape on the wire is described in
# one file.


def subscribed_frame(market_id: uuid.UUID) -> dict[str, Any]:
    """Acknowledges a subscription.

    Worth the round trip: without it a client cannot tell "subscribed to a
    market nobody has traded yet" from "my command was dropped", and both look
    like silence.
    """
    return {"type": "subscribed", "market_id": str(market_id)}


def unsubscribed_frame(market_id: uuid.UUID) -> dict[str, Any]:
    return {"type": "unsubscribed", "market_id": str(market_id)}


def error_frame(code: str, message: str) -> dict[str, Any]:
    """The same `{"error": {"code", "message"}}` envelope the four HTTP services
    return, so the frontend parses one error shape across the whole backend
    rather than a second one that exists only on the socket.
    """
    return {"type": "error", "error": {"code": code, "message": message}}
