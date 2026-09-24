"""`service/bus.py::publish` — the four lines, and the rule around them. [F-9] #112

No database. This sits under `unit_test/service/` because the function does,
and because `realtime_service`'s own `test_bus.py` sits there too; nothing in
this file needs Postgres and it runs without `docker compose up -d db`.

**The fake Redis is a recorder, not a library.** `publish(client, event, ...)`
takes the client as a parameter, which is the same seam `transport=` gives
`service/market_terms.py`: duck typing is enough, so there is no `fakeredis`
dependency to add and no server to start. It is also *better* than a live
Redis for what is asserted here — the recorder captures the exact channel
string and the exact JSON bytes, where a real round trip would prove delivery
and say nothing about either.

**What a recorder cannot prove is that the channel matches the subscriber's.**
A wrong string publishes successfully, into a channel nobody is listening on,
and no amount of local assertion notices. So the channel is pinned a second
way: by reading `realtime_service/service/bus.py` as source text and pulling
`PRICE_CHANNEL` out of it. A file read, not an import — see
`unit_test/test_dependency_pins.py`, which explains why that is allowed and
why it only ever runs in a checkout.

---

**A note on `transaction_id`, which is a spec disagreement, flagged not
resolved.** The criteria give the signature as `publish(client, event)` and
require a failure to be "logged with the committed transaction's id". Those
cannot both hold: `PriceEvent` is `extra="forbid"` over four fields and none of
them is a transaction id, so there is nowhere for the id to arrive from. These
tests assume it is a keyword-only argument — `publish(client, event, *,
transaction_id)` — because the id is the only thing that lets a human find the
trade whose broadcast was lost, and because the natural call site in [T-2] #22
has it to hand: `posting.post` returns the transaction it committed. If the
answer is meant to be something else, this is the file to change and the
decision is Ihsan's (D-007).
"""

from __future__ import annotations

import ast
import json
import logging
import pathlib
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest


def _bus():
    """Imported inside each test rather than at module scope, so a missing name
    fails the test that needs it instead of collecting the whole file (D-007)."""
    from service import bus  # noqa: PLC0415

    return bus


def _schemas():
    from model import schemas  # noqa: PLC0415

    return schemas


# `unit_test/service/` -> `unit_test/` -> `ledger_service/` -> `backend/`.
_BACKEND = pathlib.Path(__file__).resolve().parents[3]
_REALTIME_BUS = _BACKEND / "realtime_service" / "service" / "bus.py"

# The string `docs/api/realtime-service.md` documents, written out so this file
# still pins something if the source read below ever cannot run.
_DOCUMENTED_CHANNEL = "market.price"


def _module_constant(path: pathlib.Path, name: str) -> str:
    """A module-level string constant, read out of a source file with `ast`.

    Parsed rather than matched. A regex over this file matches its prose —
    `realtime_service/service/bus.py` opens with four paragraphs about why the
    channel is one rather than per-market, and mentions `PRICE_CHANNEL` by name
    in three of them — and it also breaks on formatting that changes nothing:
    single quotes, a trailing comment, a line wrapped by a formatter, a type
    annotation. `ast` sees the assignment, so only a real change to the value
    can fail this, and a reformat cannot.

    Only module-level assignments count. A `PRICE_CHANNEL` defined inside a
    class or a function is not the constant this producer has to agree with,
    and matching one would be the pin quietly reading the wrong thing.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))

    for node in tree.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign) and node.value is not None
            else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                value = node.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    return value.value
                raise AssertionError(
                    f"`{name}` in {path} is no longer a literal string, so the "
                    "channel this producer publishes to cannot be read from it"
                )

    raise AssertionError(
        f"`{name}` is no longer a module-level constant in {path}, so the "
        "channel this producer publishes to can no longer be checked against "
        "the one the relay subscribes to"
    )

_MARKET_ID = uuid.UUID("9d1c0000-0000-4000-8000-000000000001")
_TRANSACTION_ID = uuid.UUID("36fd0000-0000-4000-8000-0000000000aa")


class _Recorder:
    """A Redis client that remembers, and optionally throws.

    `publish` on the real async client is a coroutine returning the number of
    subscribers that received the message. Returning zero is the honest value
    here and is deliberately not asserted on anywhere: nothing acknowledges,
    and a producer that started caring how many subscribers there were would be
    one step from an outbox.
    """

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, object]] = []
        self._raises = raises

    async def publish(self, channel: str, payload: object) -> int:
        self.calls.append((channel, payload))
        if self._raises is not None:
            raise self._raises
        return 0


def _event(**overrides: object):
    schemas = _schemas()
    base: dict[str, object] = {
        "market_id": _MARKET_ID,
        "state_version": 42,
        "prices": [
            {"outcome_id": uuid.uuid4(), "position": 0, "price": Decimal("0.6234")},
            {"outcome_id": uuid.uuid4(), "position": 1, "price": Decimal("0.3766")},
        ],
        "occurred_at": datetime(2026, 9, 15, 9, 12, 44, 318000, tzinfo=UTC),
    }
    return schemas.PriceEvent(**{**base, **overrides})  # type: ignore[arg-type]


# =========================================================================
# The channel
# =========================================================================
def test_the_channel_is_the_documented_one() -> None:
    """`market.price`, named once in this service and pinned here.

    A different string is the failure mode with no symptom on this side: the
    publish succeeds, returns cleanly, and goes into a channel nobody has
    subscribed to. Nothing downstream errors — prices simply stop arriving.
    """
    assert _bus().PRICE_CHANNEL == _DOCUMENTED_CHANNEL


def test_the_channel_is_the_one_realtime_service_subscribes_to() -> None:
    """The half of that claim this service cannot make on its own.

    Read out of `realtime_service/service/bus.py` with `ast`, because the copy
    cannot be an import (ADR 0012) and because the string being right *here* is
    not the property that matters — the property is that the two agree. That
    file's own comment says changing the string "is a contract change that
    breaks the producer silently", and this is the assertion that makes it
    loud.

    **This fires on a PR that touches only `realtime_service`, and that is
    load-bearing.** `ci-backend.yml`'s path filter is at workflow level and
    matches `backend/**`, and its matrix runs all five services on every run —
    so a field or channel rename over there starts this job too. A future move
    to per-service path filtering would silently retire this test and
    `test_price_event.py`'s field pin along with it; both depend on the filter
    staying coarse, and the caveat already in that workflow's header about
    required checks is the neighbouring version of the same trap.
    """
    theirs = _module_constant(_REALTIME_BUS, "PRICE_CHANNEL")

    assert _bus().PRICE_CHANNEL == theirs, (
        f"this service publishes to {_bus().PRICE_CHANNEL!r} and "
        f"realtime_service subscribes to {theirs!r}; a publish to the wrong "
        "channel succeeds and is never delivered"
    )


async def test_the_event_goes_to_that_channel_and_no_other() -> None:
    """The constant being right proves nothing about what `publish` passes."""
    client = _Recorder()

    await _bus().publish(client, _event(), transaction_id=_TRANSACTION_ID)

    assert [channel for channel, _ in client.calls] == [_bus().PRICE_CHANNEL]


# =========================================================================
# The payload
# =========================================================================
async def test_the_payload_is_the_price_event_as_json() -> None:
    """What goes on the channel is what the consumer validates.

    Asserted by decoding rather than by string equality, so the test does not
    pin key order — but the key **set** is exact, because the consumer reads it
    with `extra="forbid"` and drops the whole event over a field it has no
    contract for.
    """
    client = _Recorder()
    event = _event()

    await _bus().publish(client, event, transaction_id=_TRANSACTION_ID)

    _, payload = client.calls[0]
    decoded = json.loads(payload)

    assert set(decoded) == {"market_id", "state_version", "prices", "occurred_at"}
    assert decoded["market_id"] == str(_MARKET_ID)
    assert decoded["state_version"] == 42


async def test_the_payload_carries_every_outcome_as_a_decimal_string() -> None:
    """Every outcome, not only the one traded — a trade moves the whole softmax
    and a client rendering one would show a market whose prices no longer sum
    to one. And each price as a string, because this is the last hop before a
    browser's `JSON.parse` turns a number into an IEEE double."""
    client = _Recorder()

    await _bus().publish(client, _event(), transaction_id=_TRANSACTION_ID)

    prices = json.loads(client.calls[0][1])["prices"]

    assert len(prices) == 2
    for entry in prices:
        assert isinstance(entry["price"], str), entry
        assert Decimal(entry["price"]).as_tuple().exponent == -4, entry


async def test_the_payload_is_text_rather_than_bytes() -> None:
    """The consumer opens its client with `decode_responses=True` and drops
    anything that is not a `str` — `PriceBus._decode` logs "ignoring non-text
    message" and returns. Publishing `bytes` would be delivered and discarded.
    """
    client = _Recorder()

    await _bus().publish(client, _event(), transaction_id=_TRANSACTION_ID)

    assert isinstance(client.calls[0][1], str)


async def test_one_publish_sends_exactly_one_message() -> None:
    """A retry inside `publish` would be an outbox with one entry.

    ADR 0010 is explicit that there is no retry here and will not be one: the
    recovery path is the client's reconnect-and-snapshot, and a duplicate is
    only harmless because `service/ordering.py` drops it. Sending twice by
    default makes that guard load-bearing rather than defensive.
    """
    client = _Recorder()

    await _bus().publish(client, _event(), transaction_id=_TRANSACTION_ID)

    assert len(client.calls) == 1


# =========================================================================
# The failure that must not propagate
# =========================================================================
async def test_a_publish_that_raises_does_not_reach_the_caller() -> None:
    """The rule the whole design rests on.

    Nothing acknowledges and nothing subscribes on the producer's behalf, so if
    the publish throws, the trade has still committed and is still correct. The
    caller here is [T-2] #22, which will have already committed by the time it
    calls this — the exception has nowhere useful to go and turning a committed
    trade into a 500 would tell a trader their trade failed when it had charged
    them.

    Asserted as "returns normally", which is the only observable form of "never
    fails its caller" available while #22 does not exist.
    """
    client = _Recorder(raises=ConnectionError("redis is unreachable"))

    await _bus().publish(client, _event(), transaction_id=_TRANSACTION_ID)


async def test_a_failure_is_logged_with_the_transaction_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Swallowed is not the same as silent, and the difference is this line.

    A lost broadcast recovers on its own — the client snapshots on its next
    reconnect — so nothing will ever page anybody about it. That makes the log
    the only record that it happened, and the transaction id the only thing in
    it that leads back to the trade: the market id names a market that has had
    thousands of trades, and `state_version` is meaningless without it.

    Matched against `caplog.text` rather than against a named logger, so the
    assertion is about what a human reading the log can find rather than about
    which module emitted it.
    """
    client = _Recorder(raises=ConnectionError("redis is unreachable"))

    with caplog.at_level(logging.WARNING):
        await _bus().publish(client, _event(), transaction_id=_TRANSACTION_ID)

    assert caplog.records, (
        "a publish failure was swallowed with no log line at all; the client "
        "recovers on its own, so this is the only record that it happened"
    )
    assert str(_TRANSACTION_ID) in caplog.text, (
        "the failure was logged without the committed transaction's id, which "
        f"is the only field that leads back to the trade:\n{caplog.text}"
    )


async def test_a_successful_publish_logs_no_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The other half, and the one that keeps the line above worth reading.

    This runs once per trade. A warning on the happy path is a log nobody
    greps, which is the same as no log at all on the day it matters.
    """
    client = _Recorder()

    with caplog.at_level(logging.WARNING):
        await _bus().publish(client, _event(), transaction_id=_TRANSACTION_ID)

    assert caplog.records == []


async def test_the_failure_that_is_swallowed_is_the_bus_one_only() -> None:
    """A `CancelledError` is shutdown, not a Redis blip.

    `except Exception` covers it in Python 3.13 only by accident of hierarchy —
    it does not, `CancelledError` inherits `BaseException` — and an
    `except BaseException` here would swallow the signal that the process is
    going away. Pinned so that a broad catch written to satisfy the tests above
    does not quietly acquire it.
    """
    import asyncio  # noqa: PLC0415

    client = _Recorder(raises=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await _bus().publish(client, _event(), transaction_id=_TRANSACTION_ID)


# =========================================================================
# What this ticket does not do
# =========================================================================
def test_nothing_in_this_service_calls_publish() -> None:
    """The last criterion of "The publish": the primitive lands before its caller.

    Same shape as [F-7] #96's `ensure_open` and [F-8] #109's `ensure_trading`,
    both of which shipped with no caller. A publish wired into a route here
    would be a price announced by something that is not a committed trade —
    and the one rule ADR 0010 will not bend is that the publish follows the
    commit.

    **[T-2] #22 deletes this test.** It is the ticket that adds the caller, so
    this assertion is false the moment #22 lands and is meant to be: it holds
    the "primitive before caller" shape for exactly one ticket. Removing it is
    part of #22's diff, not a failure to investigate — and it is noted on
    #22's issue as well as here, so whoever hits the red does not go looking
    for the bug.

    Read as source text across this service's runtime code — the suite is
    excluded, because a test calling the primitive is the point, and so is
    `service/bus.py`, which defines it.

    Matched on an awaited call rather than on the bare name, so a docstring
    that mentions `publish()` in prose — and several of them will, since this
    is the shape every record in `docs/adr/` argues about — is not a failure.
    """
    service_root = _BACKEND / "ledger_service"
    call = re.compile(r"await\s+(?:\w+\.)?publish\s*\(")

    offenders: list[str] = []
    for path in service_root.rglob("*.py"):
        if ".venv" in path.parts or "unit_test" in path.parts:
            continue
        if path.name == "bus.py":
            continue
        if call.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(service_root)))

    assert offenders == [], (
        "nothing in [F-9] #112 publishes on its own — the only caller is "
        f"[T-2] #22, which lands after:\n{offenders}"
    )
