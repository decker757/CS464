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
_STALE_CLAIM = re.compile(r"only\s+service\s+that\s+talks\s+to\s+Redis", re.IGNORECASE)


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
