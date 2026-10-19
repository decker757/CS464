"""`PriceEvent` and `OrderEvent`: the copies, and what holds them to their
originals. [F-9] #112, [T-7] #27, D-048.

The consumer forbids extra fields, so a drift drops every event silently. The
copy is pinned against `realtime_service/model/schemas.py`, read as source
text with `ast` (never imported), and against the documented shape, which
catches both models drifting together. The JSON on the wire is pinned too.
"""

from __future__ import annotations

import ast
import json
import pathlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError


def _schemas():
    """Imported inside each test rather than at module scope, so a missing name
    fails the test that needs it instead of collecting the whole file (D-007)."""
    from model import schemas  # noqa: PLC0415

    return schemas


# `unit_test/model/` -> `unit_test/` -> `ledger_service/` -> `backend/`.
_BACKEND = pathlib.Path(__file__).resolve().parents[3]
_REALTIME_SCHEMAS = _BACKEND / "realtime_service" / "model" / "schemas.py"

# The shape `docs/api/realtime-service.md` pins, written out rather than
# derived, so it survives both models drifting together.
_DOCUMENTED_EVENT_FIELDS = {"market_id", "state_version", "prices", "occurred_at"}
_DOCUMENTED_PRICE_FIELDS = {"outcome_id", "position", "price"}
_DOCUMENTED_ORDER_FIELDS = {
    "order_id",
    "user_id",
    "market_id",
    "outcome_id",
    "side",
    "status",
    "quantity",
    "filled_quantity",
    "fill",
    "occurred_at",
}
_DOCUMENTED_FILL_FIELDS = {"transaction_id", "quantity", "total", "state_version"}


def _annotated_fields(source: str, class_name: str) -> set[str]:
    """The annotated attribute names of one class in a source file: what
    pydantic turns into fields. `ast`, because a regex would match prose."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
    raise AssertionError(
        f"`{class_name}` is no longer a class in {_REALTIME_SCHEMAS}, so the "
        "contract this pins against has moved and this test cannot see it"
    )


def _event(**overrides: object):
    schemas = _schemas()
    base: dict[str, object] = {
        "market_id": uuid.uuid4(),
        "state_version": 42,
        "prices": [
            {"outcome_id": uuid.uuid4(), "position": 0, "price": Decimal("0.6234")},
            {"outcome_id": uuid.uuid4(), "position": 1, "price": Decimal("0.3766")},
        ],
        "occurred_at": datetime(2026, 9, 15, 9, 12, 44, 318000, tzinfo=UTC),
    }
    return schemas.PriceEvent(**{**base, **overrides})  # type: ignore[arg-type]


# =========================================================================
# The pin
# =========================================================================
def test_the_price_event_matches_realtime_services_model_field_for_field() -> None:
    """Fails if either side gains, loses or renames a field.

    Relies on CI running this job for a PR that touches only
    `realtime_service`; per-service path filtering would retire it (D-048).
    """
    theirs = _annotated_fields(
        _REALTIME_SCHEMAS.read_text(encoding="utf-8"), "PriceEvent"
    )
    ours = set(_schemas().PriceEvent.model_fields)

    assert ours == theirs, (
        "ledger_service.PriceEvent and realtime_service.PriceEvent have "
        f"drifted.\n  only here:  {sorted(ours - theirs)}\n"
        f"  only there: {sorted(theirs - ours)}"
    )


def test_the_outcome_price_matches_realtime_services_model_field_for_field() -> None:
    """The nested half, which the check above cannot see."""
    theirs = _annotated_fields(
        _REALTIME_SCHEMAS.read_text(encoding="utf-8"), "OutcomePrice"
    )
    ours = set(_schemas().PriceEvent.model_fields["prices"].annotation.__args__[0].model_fields)

    assert ours == theirs, (
        "the nested price model has drifted.\n"
        f"  only here:  {sorted(ours - theirs)}\n  only there: {sorted(theirs - ours)}"
    )


def test_the_price_event_matches_the_documented_shape() -> None:
    """Against the page rather than the other model: catches both models
    moving together."""
    assert set(_schemas().PriceEvent.model_fields) == _DOCUMENTED_EVENT_FIELDS


def test_the_outcome_price_matches_the_documented_shape() -> None:
    nested = _schemas().PriceEvent.model_fields["prices"].annotation.__args__[0]

    assert set(nested.model_fields) == _DOCUMENTED_PRICE_FIELDS


# =========================================================================
# What the model refuses
# =========================================================================
def test_an_extra_field_is_refused() -> None:
    """`extra="forbid"`, matching the consumer, which would drop the whole
    event over an extra field."""
    with pytest.raises(ValidationError):
        _event(transaction_id=uuid.uuid4())


def test_a_market_with_one_outcome_is_refused() -> None:
    """`min_length=2`, like the consumer: a one-outcome event would be dropped."""
    with pytest.raises(ValidationError):
        _event(
            prices=[
                {"outcome_id": uuid.uuid4(), "position": 0, "price": Decimal("1.0000")}
            ]
        )


def test_a_negative_state_version_is_refused() -> None:
    """`ge=0`. The counter opens at zero on a never-traded book (D-011) and
    only ever increases, under the book row's lock. A negative one means
    something wrote to a column no code path in this service decrements."""
    with pytest.raises(ValidationError):
        _event(state_version=-1)


# =========================================================================
# What reaches the wire
# =========================================================================
def test_a_price_serialises_as_a_decimal_string() -> None:
    """Asserted on the JSON, which is what the consumer receives: without a
    serialiser a `Decimal` leaves as a JSON number and loses exactness.
    """
    payload = json.loads(_event().model_dump_json())

    for entry in payload["prices"]:
        assert isinstance(entry["price"], str), entry


def test_a_price_keeps_its_scale_on_the_wire() -> None:
    """`0.6` and `0.6000` are the same number and different strings; the
    frontend formats one of them."""
    payload = json.loads(
        _event(
            prices=[
                {"outcome_id": uuid.uuid4(), "position": 0, "price": Decimal("0.6000")},
                {"outcome_id": uuid.uuid4(), "position": 1, "price": Decimal("0.4000")},
            ]
        ).model_dump_json()
    )

    assert [p["price"] for p in payload["prices"]] == ["0.6000", "0.4000"]


def test_state_version_is_a_json_number() -> None:
    """Deliberately not a string: a count a client compares with `>`, where
    `"9"` would order after `"10"`."""
    assert isinstance(json.loads(_event().model_dump_json())["state_version"], int)


def test_occurred_at_reaches_the_wire_with_an_offset() -> None:
    """Timezone-aware, always UTC.

    Fed a naive value, the only input that tests the coercion: an aware one
    keeps its offset whatever the model does.
    """
    naive = datetime(2026, 9, 15, 9, 12, 44, 318000)

    serialised = json.loads(_event(occurred_at=naive).model_dump_json())["occurred_at"]

    parsed = datetime.fromisoformat(serialised)
    assert parsed.tzinfo is not None, serialised
    assert parsed.utcoffset() == timedelta(0), serialised
    assert parsed.replace(tzinfo=None) == naive, (
        "a naive value is read as UTC, not converted from local time"
    )


def test_the_json_carries_exactly_the_documented_keys() -> None:
    """What actually goes on the channel, which an alias or serialiser could
    make differ from the model's fields."""
    assert set(json.loads(_event().model_dump_json())) == _DOCUMENTED_EVENT_FIELDS


# =========================================================================
# OrderEvent: the `order.update` payload (ADR 0024)
# =========================================================================
def _realtime_fields(class_name: str) -> set[str]:
    return _annotated_fields(_REALTIME_SCHEMAS.read_text(encoding="utf-8"), class_name)


def test_the_order_event_and_its_fill_match_realtime_services_models() -> None:
    """D-048's pin for the second channel: the consumer forbids extra fields
    on both, so a drift in either drops every order frame silently."""
    schemas = _schemas()

    assert set(schemas.OrderEvent.model_fields) == _realtime_fields("OrderEvent")
    assert set(schemas.OrderFill.model_fields) == _realtime_fields("OrderFill")


def test_the_order_event_and_its_fill_match_the_documented_shape() -> None:
    """Against docs/api/realtime-service.md, "Order updates", which catches
    both copies moving together. The fill is the `fill` field's own model."""
    order_event = _schemas().OrderEvent

    assert set(order_event.model_fields) == _DOCUMENTED_ORDER_FIELDS
    assert order_event.model_fields["fill"].annotation is _schemas().OrderFill
    assert set(_schemas().OrderFill.model_fields) == _DOCUMENTED_FILL_FIELDS


def test_an_order_event_reaches_the_wire_as_the_documented_example() -> None:
    """The JSON the relay validates: amounts as decimal strings at scale 4,
    the total signed as the trade route signs it, the version a number, and
    `occurred_at` with its `+00:00` offset, as the price frame writes it."""
    schemas = _schemas()
    ids = [uuid.uuid4() for _ in range(5)]
    event = schemas.OrderEvent(
        order_id=ids[0],
        user_id=ids[1],
        market_id=ids[2],
        outcome_id=ids[3],
        side="buy",
        status="open",
        quantity=Decimal("100.0000"),
        filled_quantity=Decimal("20.5465"),
        fill=schemas.OrderFill(
            transaction_id=ids[4],
            quantity=Decimal("20.5465"),
            total=Decimal("-11.8152"),
            state_version=43,
        ),
        occurred_at=datetime(2026, 10, 20, 9, 12, 44, 318000, tzinfo=UTC),
    )

    assert json.loads(event.model_dump_json()) == {
        "order_id": str(ids[0]),
        "user_id": str(ids[1]),
        "market_id": str(ids[2]),
        "outcome_id": str(ids[3]),
        "side": "buy",
        "status": "open",
        "quantity": "100.0000",
        "filled_quantity": "20.5465",
        "fill": {
            "transaction_id": str(ids[4]),
            "quantity": "20.5465",
            "total": "-11.8152",
            "state_version": 43,
        },
        "occurred_at": "2026-10-20T09:12:44.318000+00:00",
    }
