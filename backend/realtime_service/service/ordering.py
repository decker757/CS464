"""The staleness guard. [F-2] #42, and [X-4] #37's fourth acceptance criterion.

"Price updates include a market identifier and state version so older events
cannot overwrite newer prices." This is the server half of that sentence.

The mechanism is one comparison: an event whose `state_version` is not strictly
greater than the newest this process has already forwarded for that market is
dropped and never reaches a subscriber. Redis pub/sub makes no ordering promise
across a reconnect, and a producer that retries after a timeout can publish the
same event twice, so "the events arrived in order" is not something a consumer
is allowed to assume.

**The client must do this too, and that is not redundant.** Two cases this gate
cannot cover:

- A client's snapshot can be *newer* than the first event it receives. It
  fetches a snapshot at version 12 while an event for version 11 is already in
  flight to a replica that has forwarded nothing yet. This gate has no idea what
  the client knows; only the client can refuse that event.
- A client that reconnects lands on whichever replica the balancer picks, and
  each replica's gate is its own. The second replica will happily forward
  version 11 because it has never seen 12.

So this gate is not the client's guarantee. It is the cheaper thing: it stops a
duplicate or a reordered redelivery from costing every connected client a
pointless render, and it means a single replica is internally consistent about
what it has said.
"""

from __future__ import annotations

import uuid


class VersionGate:
    """Per-market high-water marks, for this process only.

    Deliberately not shared through Redis. A gate that replicas agreed on would
    be a distributed lock in front of a price feed, bought to prevent a
    redundant render — and it would make every broadcast wait on a network round
    trip to a service whose entire purpose is being faster than a page refresh.
    """

    def __init__(self) -> None:
        self._latest: dict[uuid.UUID, int] = {}

    def accept(self, market_id: uuid.UUID, state_version: int) -> bool:
        """True if this event is newer than anything forwarded for this market.

        Strictly greater, so a redelivery of the version already forwarded is
        dropped. Equal is not "no change" — a genuine change always advances the
        counter, because the counter is incremented in the same transaction as
        the change. An equal version is therefore always the same event arriving
        twice.
        """
        latest = self._latest.get(market_id)
        if latest is not None and state_version <= latest:
            return False

        self._latest[market_id] = state_version
        return True

    def latest(self, market_id: uuid.UUID) -> int | None:
        """The newest version forwarded for this market, or None.

        For `/health` and for tests. Not offered to clients: it is what this
        replica has seen, which is not the same claim as what is true, and a
        client that treated it as authoritative would be using the cache this
        service refuses to be. The snapshot endpoint is the authority.
        """
        return self._latest.get(market_id)


_gate: VersionGate | None = None


def get_gate() -> VersionGate:
    global _gate
    if _gate is None:
        _gate = VersionGate()
    return _gate


def reset_gate() -> None:
    """Drop the gate. For tests, and for nothing else."""
    global _gate
    _gate = None
