"""`PriceEvent`: the copy, and what holds it to its original. [F-9] #112

`realtime_service` validates what arrives with `extra="forbid"`, so a producer
that gains a field does not get an error — the consumer drops the whole event
and the symptom, three services away, is prices that quietly stop updating on a
screen. That asymmetry is the reason this file exists: nothing else in either
repository directory can notice the two models disagreeing.

**Copied, not imported**, per ADR 0012 and `docs/api/realtime-service.md`. So
the pin below reads `realtime_service/model/schemas.py` **as source text** and
parses it with `ast`. That is a file read, not an import:
`test_import_boundary.py` matches `from`/`import realtime_service` at the start
of a line, and this never resolves a module, never executes the other service's
code, and never runs anywhere but a checkout.

Pinned twice, against two different things, because they fail differently:

- against the **documented shape** in `docs/api/realtime-service.md`, which is
  the contract Michelle's client is written to and the thing that is true even
  if both models drift together;
- against the **other model's own fields**, which is what catches a rename on
  either side.

And the pin is on what reaches the wire, not only on the field names. A model
matching field for field still breaks the contract if `price` leaves as a JSON
number: the consumer's `Decimal` would validate it happily, and the exactness
`docs/api/ledger-service.md` and `docs/api/realtime-service.md` both insist on
would have been thrown away one hop before anybody could notice.
"""

from __future__ import annotations

import ast
import json
import pathlib
import uuid
from datetime import UTC, datetime
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
# derived, so this half of the assertion survives both models drifting
# together. The page's `price` frame is these four fields plus `type`, and the
# snapshot is these four alone.
_DOCUMENTED_EVENT_FIELDS = {"market_id", "state_version", "prices", "occurred_at"}
_DOCUMENTED_PRICE_FIELDS = {"outcome_id", "position", "price"}


def _annotated_fields(source: str, class_name: str) -> set[str]:
    """The annotated attribute names of one class in a source file.

    `ast` rather than a regex because a regex over a file this heavily
    commented matches prose — `realtime_service/model/schemas.py` is the
    longest module in that service and says so in its own docstring. Only
    `AnnAssign` targets count, which is exactly what pydantic turns into a
    field and excludes the `model_config`, the serialisers and `frame()`.
    """
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
    """The criterion's own test: fails if either side gains, loses or renames.

    Both directions are asserted as one set comparison rather than two
    subset checks, so the failure message names the difference rather than the
    direction. A field gained here is an event the consumer silently drops; a
    field gained there is a field this producer never sends, which validates
    only while it stays optional.

    **This fires on a PR that touches only `realtime_service`, and that is
    load-bearing.** `ci-backend.yml`'s path filter is at workflow level and
    matches `backend/**`, and its matrix runs all five services on every run —
    so a rename over there starts this job too. A future move to per-service
    path filtering would silently retire this pin and the channel pin in
    `unit_test/service/test_price_publish.py` along with it.
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
    """The nested half, which the check above cannot see.

    `prices` being a list of the right length says nothing about what is in it,
    and an `OutcomePrice` that lost `position` would pass every assertion
    above while making a categorical market render in a different order on the
    socket than in the snapshot.
    """
    theirs = _annotated_fields(
        _REALTIME_SCHEMAS.read_text(encoding="utf-8"), "OutcomePrice"
    )
    ours = set(_schemas().PriceEvent.model_fields["prices"].annotation.__args__[0].model_fields)

    assert ours == theirs, (
        "the nested price model has drifted.\n"
        f"  only here:  {sorted(ours - theirs)}\n  only there: {sorted(theirs - ours)}"
    )


def test_the_price_event_matches_the_documented_shape() -> None:
    """The second pin, against the page rather than against the other model.

    `docs/api/realtime-service.md` is the contract — the page says so, and says
    there is no generated schema that will catch it drifting. Both models
    moving together is a change nothing else here would notice, and it is the
    one that breaks a client that was already written.
    """
    assert set(_schemas().PriceEvent.model_fields) == _DOCUMENTED_EVENT_FIELDS


def test_the_outcome_price_matches_the_documented_shape() -> None:
    nested = _schemas().PriceEvent.model_fields["prices"].annotation.__args__[0]

    assert set(nested.model_fields) == _DOCUMENTED_PRICE_FIELDS


# =========================================================================
# What the model refuses
# =========================================================================
def test_an_extra_field_is_refused() -> None:
    """`extra="forbid"`, matching the consumer.

    The consumer's copy is what protects the socket; this one protects the
    producer from thinking it sent something. Without it, a caller adding
    `transaction_id=...` to the event constructor gets a model that serialises
    a field the other end drops the whole message over, and the only symptom is
    a price that never arrives.
    """
    with pytest.raises(ValidationError):
        _event(transaction_id=uuid.uuid4())


def test_a_market_with_one_outcome_is_refused() -> None:
    """`min_length=2`. A one-outcome market is not a market.

    Three copies of this rule have to agree and `core/lmsr.py::_MIN_OUTCOMES`
    is the one the other two defer to: `C(q)` over one outcome prices a
    certainty at 1, and a share that costs 1 credit and pays 1 credit is not
    something anybody can trade. `market_service.core.opening_prices` refuses
    it at creation and the consumer refuses it on the wire, so a producer that
    allowed it would build an event guaranteed to be dropped.
    """
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
    """The fourth criterion of "The event", asserted on the JSON rather than on
    the model.

    `model_dump()` would pass against a `Decimal` that has never been
    serialised. What the consumer receives is `model_dump_json()`, and a
    `Decimal` field with no serialiser reaches it as a JSON **number** — which
    validates fine on the other side and is an IEEE double by the time the
    browser has parsed it. The exactness this service keeps from `q` all the
    way through `core/lmsr.py` would be thrown away on the last hop, silently,
    with every test on field names still green.
    """
    payload = json.loads(_event().model_dump_json())

    for entry in payload["prices"]:
        assert isinstance(entry["price"], str), entry


def test_a_price_keeps_its_scale_on_the_wire() -> None:
    """Scale 4, `ROUND_HALF_UP`, quantized by `core/pricing.py::QUANTUM` — the
    same helper `service/preview.py` already uses, so the number in a snapshot,
    a price frame and a preview is the same string.

    `0.6` and `0.6000` are the same number and different strings, and the
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
    """The one field that is deliberately not a string.

    It is a count, not money: nothing is lost through a double, and a client
    compares it with `>` against the version it last rendered. Making it a
    string would have every consumer parse before comparing, and the first one
    that forgot would order `"9"` after `"10"`.
    """
    assert isinstance(json.loads(_event().model_dump_json())["state_version"], int)


def test_occurred_at_reaches_the_wire_with_an_offset() -> None:
    """Timezone-aware, always UTC.

    CLAUDE.md records that naive-versus-aware timestamps have already caused
    bugs in this repository, and the consumer's own field documents the
    requirement. A naive value here serialises without the offset and a client
    parsing it gets local time, which is a display bug on a field the contract
    says is for display.
    """
    serialised = json.loads(_event().model_dump_json())["occurred_at"]

    assert datetime.fromisoformat(serialised).tzinfo is not None, serialised


def test_the_json_carries_exactly_the_documented_keys() -> None:
    """The whole payload, as a key set.

    The field-for-field pins above compare model fields; this compares what is
    actually put on the channel. They can disagree — an aliased field, a
    serialiser that emits a nested object — and the consumer only ever sees
    this one.
    """
    assert set(json.loads(_event().model_dump_json())) == _DOCUMENTED_EVENT_FIELDS


def test_the_event_carries_no_type_field() -> None:
    """`type` belongs to the socket frame, not to the model.

    `realtime_service`'s `PriceEvent.frame()` adds it on the way out, which is
    why the snapshot body and the model are the same four fields and why the
    snapshot route has nothing to strip. A `type` here would be an extra field
    the consumer forbids.
    """
    assert "type" not in _schemas().PriceEvent.model_fields
