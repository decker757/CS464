"""`service/bus.py::publish`, and the rule around it. [F-9] #112, D-049.

No database. The Redis client is a recorder, which captures the exact channel
and payload. A recorder cannot prove the channel matches the subscriber's, so
that is pinned by reading `realtime_service/service/bus.py` as source text.
"""

from __future__ import annotations

import ast
import json
import logging
import pathlib
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


def _module_constant(path: pathlib.Path, name: str) -> str:
    """A module-level string constant, read out of a source file with `ast`:
    a regex would match prose and break on reformatting (D-048).
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
    """A Redis client that remembers, and optionally throws. Its subscriber
    count is never asserted on: nothing acknowledges (ADR 0010).
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
def test_the_channel_is_the_one_realtime_service_subscribes_to() -> None:
    """The two services agree on the channel, read from the other's source.

    Relies on CI running this job for a PR that touches only
    `realtime_service`; per-service path filtering would retire it (D-048).
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
    """What goes on the channel is what the consumer validates: decoded, with
    the exact key set, since the consumer forbids extras."""
    client = _Recorder()
    event = _event()

    await _bus().publish(client, event, transaction_id=_TRANSACTION_ID)

    _, payload = client.calls[0]
    decoded = json.loads(payload)

    assert set(decoded) == {"market_id", "state_version", "prices", "occurred_at"}
    assert decoded["market_id"] == str(_MARKET_ID)
    assert decoded["state_version"] == 42


async def test_the_payload_is_text_rather_than_bytes() -> None:
    """The consumer discards anything that is not a `str`."""
    client = _Recorder()

    await _bus().publish(client, _event(), transaction_id=_TRANSACTION_ID)

    assert isinstance(client.calls[0][1], str)


async def test_one_publish_sends_exactly_one_message() -> None:
    """No retry inside `publish` (ADR 0010): recovery is the client's
    reconnect-and-snapshot."""
    client = _Recorder()

    await _bus().publish(client, _event(), transaction_id=_TRANSACTION_ID)

    assert len(client.calls) == 1


# =========================================================================
# The failure that must not propagate
# =========================================================================
async def test_a_publish_that_raises_does_not_reach_the_caller() -> None:
    """The trade has already committed, so a failed publish returns normally
    (D-049)."""
    client = _Recorder(raises=ConnectionError("redis is unreachable"))

    await _bus().publish(client, _event(), transaction_id=_TRANSACTION_ID)


async def test_a_failure_is_logged_with_the_transaction_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Swallowed is not silent: the log is the only record, and the transaction
    id the only field leading back to the trade (D-049).
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
    """No warning on the happy path, which runs once per trade."""
    client = _Recorder()

    with caplog.at_level(logging.WARNING):
        await _bus().publish(client, _event(), transaction_id=_TRANSACTION_ID)

    assert caplog.records == []


async def test_the_failure_that_is_swallowed_is_the_bus_one_only() -> None:
    """A `CancelledError` is shutdown, not a Redis blip, and must propagate: an
    `except BaseException` would swallow it."""
    import asyncio  # noqa: PLC0415

    client = _Recorder(raises=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await _bus().publish(client, _event(), transaction_id=_TRANSACTION_ID)


# The caller's side (once per trade, after the commit, never failing the
# trade) is `unit_test/service/test_trade_publish.py`.
