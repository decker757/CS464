"""Pins that span two services' files. [F-9] #112

These read the other service's files as text and never import it
(`test_import_boundary.py`), so they fail when the other side moves.
"""

from __future__ import annotations

import pathlib
import re

# `unit_test/` -> `ledger_service/` -> `backend/`.
_BACKEND = pathlib.Path(__file__).resolve().parents[2]

_THIS_REQUIREMENTS = _BACKEND / "ledger_service" / "requirements.txt"
_REALTIME_REQUIREMENTS = _BACKEND / "realtime_service" / "requirements.txt"

_PIN = re.compile(r"^redis==(\S+)\s*$", re.MULTILINE)

# A claim that only one service uses Redis, in the obvious wordings. Narrow on
# purpose: "no other service touches Redis" can be true and must not match.
_STALE_CLAIM = re.compile(
    r"only\s+(one\s+)?service\s+(that\s+)?"
    r"(talks\s+to|uses|knows|touches|needs|connects\s+to)\s+Redis",
    re.IGNORECASE,
)


def _pinned_redis(path: pathlib.Path) -> str | None:
    match = _PIN.search(path.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def test_the_redis_pin_matches_the_one_realtime_service_carries() -> None:
    """One Redis library across the backend, not two. The only assertion that
    sees both files, so it fails when either moves.
    """
    ours = _pinned_redis(_THIS_REQUIREMENTS)
    theirs = _pinned_redis(_REALTIME_REQUIREMENTS)

    assert theirs is not None, (
        f"{_REALTIME_REQUIREMENTS} no longer pins redis at all, so there is "
        "nothing for this service to agree with"
    )
    assert ours == theirs, (
        f"ledger_service pins redis=={ours} and realtime_service pins "
        f"redis=={theirs}; one Redis library, pinned once, in both files"
    )


def test_realtime_service_no_longer_claims_to_be_the_only_redis_client() -> None:
    """`realtime_service/requirements.txt` must not claim to be the only Redis
    client: someone debugging a missing price frame would rule out the
    producer.
    """
    text = _REALTIME_REQUIREMENTS.read_text(encoding="utf-8")

    assert _STALE_CLAIM.search(text) is None, (
        "realtime_service/requirements.txt still claims to be the only "
        "service that talks to Redis; [F-9] #112 makes the ledger the second"
    )


# =========================================================================
# The deployment's wiring, which is where the bus is actually chosen
# =========================================================================
_REPO = _BACKEND.parent
_COMPOSE = _REPO / "docker-compose.yml"


def _compose_service(name: str) -> dict:
    """One service's block from `docker-compose.yml`, parsed rather than
    pattern-matched. PyYAML arrives with `uvicorn[standard]`.
    """
    import yaml  # noqa: PLC0415

    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["services"][name]


def test_compose_hands_the_ledger_the_bus_the_realtime_service_listens_on() -> None:
    """The producer and the consumer, pointed at one Redis by compose.

    On the compiled-in default instead, moving the bus would leave every
    publish "succeeding" into a Redis nobody subscribes to.
    """
    ledger = _compose_service("ledger").get("environment", {})
    realtime = _compose_service("realtime").get("environment", {})

    assert "REDIS_URL" in ledger, (
        "docker-compose.yml's ledger service sets no REDIS_URL, so the "
        "producer's bus is whatever the compiled-in default says"
    )
    assert ledger["REDIS_URL"] == realtime["REDIS_URL"], (
        f"the ledger publishes to {ledger['REDIS_URL']!r} and the realtime "
        f"service subscribes on {realtime['REDIS_URL']!r}"
    )


def test_compose_does_not_hold_the_ledger_back_until_redis_is_up() -> None:
    """The ledger starts without the bus, and compose must not undo that.

    A `depends_on: redis` would make a Redis that never comes up a ledger that
    never comes up, to protect a broadcast `service/bus.py` is written to lose.
    """
    depends_on = _compose_service("ledger").get("depends_on", {})

    assert "redis" not in depends_on, (
        "docker-compose.yml makes the ledger wait on Redis; an unreachable bus "
        "costs broadcasts, not a ledger that will not start"
    )
