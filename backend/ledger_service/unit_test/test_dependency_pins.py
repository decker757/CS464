"""Pins that span two services' files. [F-9] #112

Two of this ticket's criteria are about agreement between this service and
`realtime_service`, and neither can be asserted from inside one service:

- `redis==8.1.0`, "the exact pin `realtime_service` already carries, so one
  Redis library is in play across the backend".
- That service's `requirements.txt` comment claiming it is "the only service
  that talks to Redis, and the only one that needs it", which stops being true
  the moment this ticket lands.

**These read the other service's files as text. They do not import it.**
`test_import_boundary.py` forbids `import realtime_service` and is right to —
that import resolves under pytest and is an `ImportError` in the container,
which holds `/app/ledger_service` and `/app/shared` and nothing else. A file
read is not an import: the regex there matches `from` or `import` at the start
of a line, so nothing here trips it, and nothing here runs anywhere but a
checkout.

The direction that matters is that these fail when the **other** side moves.
A pin bumped in one service and not the other is two Redis libraries in one
backend, which is the thing the criterion is written to prevent, and the
service that gets it wrong is whichever one nobody edited.
"""

from __future__ import annotations

import pathlib
import re

# `unit_test/` -> `ledger_service/` -> `backend/`. Resolved by hops rather than
# by marker search because both services sit at a known, fixed depth under
# `backend/` and [F-6] #76's build contexts are what fix it there.
_BACKEND = pathlib.Path(__file__).resolve().parents[2]

_THIS_REQUIREMENTS = _BACKEND / "ledger_service" / "requirements.txt"
_REALTIME_REQUIREMENTS = _BACKEND / "realtime_service" / "requirements.txt"

_PIN = re.compile(r"^redis==(\S+)\s*$", re.MULTILINE)

# The sentence the criterion names, reduced to the clause that carries the
# claim. Matched loosely on whitespace so a rewrap does not fail this, and on
# the claim rather than the whole line so that correcting it by rewriting the
# comment passes while correcting it by deleting one word does not.
# A claim that *one* service uses Redis, in any of the obvious wordings.
# Narrow on purpose: "the realtime service reads it and the ledger writes
# it; no other service touches Redis" is true and must not match, so this
# looks for the singular claim rather than for the word "other".
_STALE_CLAIM = re.compile(
    r"only\s+(one\s+)?service\s+(that\s+)?"
    r"(talks\s+to|uses|knows|touches|needs|connects\s+to)\s+Redis",
    re.IGNORECASE,
)


def _pinned_redis(path: pathlib.Path) -> str | None:
    match = _PIN.search(path.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def test_this_service_pins_redis() -> None:
    """The first criterion's own half.

    Asserted separately from the agreement below so that "no redis pin at all"
    and "a redis pin that disagrees" fail as two different sentences. Without
    this, a missing pin would fail the next test with a message about
    `realtime_service`, which is not where the problem is.
    """
    assert _pinned_redis(_THIS_REQUIREMENTS) is not None, (
        "[F-9] #112 adds Redis to this service; `redis==` is not in "
        f"{_THIS_REQUIREMENTS.name}"
    )


def test_the_redis_pin_matches_the_one_realtime_service_carries() -> None:
    """One Redis library in play across the backend, not two.

    The same rule `requirements.txt` already states for the JWT and Postgres
    libraries, and the same rule that put one `pydantic` in all five services.
    Two pins is two wire encodings for a pub/sub payload and two sets of
    connection semantics, discovered on the day one of them changes.

    Fails when **either** file moves, which is the point: this is the only
    assertion in the repository that sees both.
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
    """The fourth criterion of "The client", and a one-line change.

    `realtime_service/requirements.txt` says Redis is there because "this is
    the only service that talks to Redis, and the only one that needs it".
    That sentence is the reason a reader would give for not looking anywhere
    else, and this ticket makes it false. Left standing, the next person
    debugging a missing price frame reads it and rules out the producer.

    Asserted here rather than left to review because it is in a file this
    service's PR touches for one line and nothing else — exactly the kind of
    edit that gets dropped on a rebase.
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
_ENV_EXAMPLE = _REPO / ".env.example"


def _compose_service(name: str) -> dict:
    """One service's block from `docker-compose.yml`, parsed.

    PyYAML arrives with `uvicorn[standard]`, which this service already pins;
    parsing rather than pattern-matching means a reordered or re-indented
    block cannot pass or fail this by accident.
    """
    import yaml  # noqa: PLC0415

    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))["services"][name]


def test_compose_hands_the_ledger_the_bus_the_realtime_service_listens_on() -> None:
    """The producer and the consumer, pointed at one Redis by the one file
    that decides it.

    Without a `REDIS_URL` line of its own, the ledger container ran on
    `core/config.py`'s compiled-in default, and the only place the address
    was written down for compose was the consumer's block. Correct today by
    coincidence — the default happens to be the same string — and silently
    wrong the day somebody moves the bus and edits the one line they can see:
    the ledger publishes into a Redis nobody subscribes to, and every publish
    "succeeds".
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

    `depends_on: redis: condition: service_healthy` is the line a tidy-minded
    edit adds next to a new `REDIS_URL`, and it would make a Redis that never
    comes up a ledger that never comes up — trading stopped platform-wide to
    protect a broadcast that `service/bus.py` is written to lose. Same
    argument as `test_the_app_still_starts_when_redis_is_unreachable`, one
    layer out.
    """
    depends_on = _compose_service("ledger").get("depends_on", {})

    assert "redis" not in depends_on, (
        "docker-compose.yml makes the ledger wait on Redis; an unreachable bus "
        "costs broadcasts, not a ledger that will not start"
    )


def test_env_example_no_longer_says_only_one_service_knows_redis() -> None:
    """`.env.example`'s own copy of the claim
    `test_realtime_service_no_longer_claims_to_be_the_only_redis_client`
    removes from `realtime_service/requirements.txt`.

    It is the first file anybody setting up a checkout reads, so the next
    person debugging a missing price frame reads it before any code and rules
    the producer out.

    **Asserted as a positive, and the negative version was a one-shot.** This
    used to grep for the one sentence a PR had deleted, which any rewording
    satisfies — and a broader regex is worse, because "the realtime service
    reads it and the ledger writes it; no other service touches Redis" is
    true and would match it. What has to hold is that the ledger is named as
    a client, so that is what is checked.
    """
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")

    assert _STALE_CLAIM.search(text) is None, (
        ".env.example claims a single Redis client; the ledger is a second "
        "one since [F-9] #112"
    )
    # Sliced backwards from the assignment, clamped at zero. `text[i - 1500:]`
    # silently becomes a tail-of-file slice the day the Redis block moves
    # into the first 1500 characters, and then passes or fails on prose
    # that has nothing to do with it.
    at = text.index("REDIS_URL=")
    block = text[max(0, at - 1500) : at]

    assert re.search(r"ledger", block, re.I), (
        ".env.example's Redis block does not mention the ledger; it has been "
        "the producer since [F-9] #112, and this file is where somebody "
        "debugging a missing price frame looks first"
    )
