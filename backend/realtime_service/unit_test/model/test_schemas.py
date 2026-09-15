"""The wire contract, in both directions.

These are the tests [T-2] #22 and [X-4] #37 are really relying on: this file is
where the shape they code against is pinned, and `docs/api/realtime-service.md`
is prose that cannot fail a build.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from model.schemas import (
    ClientCommand,
    OutcomePrice,
    PriceEvent,
    error_frame,
    subscribed_frame,
    unsubscribed_frame,
)
from unit_test.conftest import make_event


# ---------------------------------------------------------------------------
# PriceEvent — what the producer publishes
# ---------------------------------------------------------------------------


def test_a_well_formed_event_round_trips(market_id: uuid.UUID) -> None:
    event = make_event(market_id, 7)

    restored = PriceEvent.model_validate_json(event.model_dump_json())

    assert restored.market_id == market_id
    assert restored.state_version == 7
    assert [p.price for p in restored.prices] == [Decimal("0.6000"), Decimal("0.4000")]


def test_prices_are_strings_on_the_wire(market_id: uuid.UUID) -> None:
    """Not JSON numbers, for the reason `ledger_service` sends balances as
    strings: a JSON number is an IEEE double by the time a browser has parsed
    it. The ledger keeps its arithmetic exact and handing the result through a
    double on the last hop throws that away."""
    frame = make_event(market_id).frame()

    assert frame["prices"][0]["price"] == "0.6000"
    assert isinstance(frame["prices"][0]["price"], str)


def test_the_frame_is_the_event_plus_a_type(market_id: uuid.UUID) -> None:
    """Flat rather than nested, so a client's dispatch is one field lookup and
    the price fields are not one level deeper on the socket than they are in
    the snapshot response."""
    frame = make_event(market_id, 3).frame()

    assert frame["type"] == "price"
    assert frame["market_id"] == str(market_id)
    assert frame["state_version"] == 3
    assert set(frame["prices"][0]) == {"outcome_id", "position", "price"}


def test_the_frame_is_json_serialisable(market_id: uuid.UUID) -> None:
    """`WebSocket.send_json` calls `json.dumps`, which has no idea what a UUID
    or a Decimal is. A frame that only serialised under Pydantic would fail at
    the moment of delivery and nowhere earlier."""
    import json

    json.dumps(make_event(market_id).frame())  # must not raise


def test_an_unknown_field_is_refused(market_id: uuid.UUID) -> None:
    """`extra="forbid"`. A producer that adds a field and forgets to say so is
    making a contract change, and it should not be possible to make one by
    accident from another repository directory."""
    payload = make_event(market_id).model_dump(mode="json")
    payload["surprise"] = "hello"

    with pytest.raises(ValidationError):
        PriceEvent.model_validate(payload)


def test_a_market_needs_at_least_two_outcomes(market_id: uuid.UUID) -> None:
    """A one-outcome market is not a market, and
    `market_service.core.opening_prices` refuses to price one for the same
    reason."""
    with pytest.raises(ValidationError):
        PriceEvent(
            market_id=market_id,
            state_version=1,
            prices=[OutcomePrice(outcome_id=uuid.uuid4(), position=0, price=Decimal("1"))],
            occurred_at=datetime.now(UTC),
        )


@pytest.mark.parametrize("price", ["-0.01", "1.01"])
def test_a_price_outside_zero_to_one_is_refused(price: str) -> None:
    """One of the two structural checks a relay can honestly make. It is not an
    assertion that the prices sum to one — this service holds no `q` and no `b`
    and cannot recompute the number to check it."""
    with pytest.raises(ValidationError):
        OutcomePrice(outcome_id=uuid.uuid4(), position=0, price=Decimal(price))


def test_a_negative_state_version_is_refused(market_id: uuid.UUID) -> None:
    with pytest.raises(ValidationError):
        make_event(market_id, -1)


def test_prices_that_do_not_sum_to_one_are_relayed(market_id: uuid.UUID) -> None:
    """Deliberate, and the reason is worth stating as a test rather than only a
    comment. LMSR marginal prices do sum to one, but this service does not own
    that invariant and cannot check it. A relay that rejected a correct price
    over a rounding tolerance would be worse than one that relays whatever the
    authority said."""
    event = make_event(market_id, yes="0.9000", no="0.9000")

    assert PriceEvent.model_validate_json(event.model_dump_json()) is not None


def test_occurred_at_keeps_its_timezone(market_id: uuid.UUID) -> None:
    """CLAUDE.md records that naive-versus-aware timestamps have already caused
    bugs here. This one is display-only — ordering is `state_version`'s job —
    but a naive timestamp rendered in the browser's local zone is still an hour
    of somebody's afternoon."""
    frame = make_event(market_id).frame()

    assert datetime.fromisoformat(frame["occurred_at"]).tzinfo is not None


# ---------------------------------------------------------------------------
# ClientCommand — what a client may send
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["subscribe", "unsubscribe"])
def test_the_two_actions_parse(action: str, market_id: uuid.UUID) -> None:
    command = ClientCommand.model_validate(
        {"action": action, "market_id": str(market_id)}
    )

    assert command.action == action
    assert command.market_id == market_id


def test_an_unknown_action_is_refused(market_id: uuid.UUID) -> None:
    with pytest.raises(ValidationError):
        ClientCommand.model_validate(
            {"action": "snapshot", "market_id": str(market_id)}
        )


def test_a_market_id_that_is_not_a_uuid_is_refused() -> None:
    with pytest.raises(ValidationError):
        ClientCommand.model_validate({"action": "subscribe", "market_id": "the-big-one"})


def test_an_extra_field_on_a_command_is_ignored(market_id: uuid.UUID) -> None:
    """The opposite of `PriceEvent`, on purpose. A producer is one service we
    control and a contract change there should be loud; a client is a browser
    that may be a version behind, and refusing its whole command over a field
    this build does not read would break a page to no benefit."""
    command = ClientCommand.model_validate(
        {"action": "subscribe", "market_id": str(market_id), "since": 4}
    )

    assert command.market_id == market_id


# ---------------------------------------------------------------------------
# Server frames
# ---------------------------------------------------------------------------


def test_the_acknowledgements_name_their_market(market_id: uuid.UUID) -> None:
    assert subscribed_frame(market_id) == {
        "type": "subscribed",
        "market_id": str(market_id),
    }
    assert unsubscribed_frame(market_id) == {
        "type": "unsubscribed",
        "market_id": str(market_id),
    }


def test_the_error_frame_uses_the_backend_wide_envelope() -> None:
    """The same `{"error": {"code", "message"}}` the four HTTP services return,
    so the frontend parses one error shape across the whole backend rather than
    a second one that exists only on the socket."""
    frame = error_frame("unknown_action", "Supported actions are ...")

    assert frame["type"] == "error"
    assert frame["error"] == {
        "code": "unknown_action",
        "message": "Supported actions are ...",
    }
