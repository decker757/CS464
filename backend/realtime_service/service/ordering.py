"""The staleness guard. [F-2] #42, [X-4] #37

Drops any event whose `state_version` is not strictly newer than the last one
this process forwarded for that market. The client must run the same check;
this one only spares every client a pointless render. ADR 0010 and
docs/api/realtime-service.md say why both are needed.
"""

from __future__ import annotations

import uuid


class VersionGate:
    """Per-market high-water marks, for this process only.

    Not shared through Redis: replicas coordinate about nothing. ADR 0010.
    Sharing it would put a round trip in front of every broadcast to save a
    redundant render.
    """

    def __init__(self) -> None:
        self._latest: dict[uuid.UUID, int] = {}

    def accept(self, market_id: uuid.UUID, state_version: int) -> bool:
        """True, and recorded, if this is newer than anything forwarded for this market.

        Strictly greater: every real change advances the counter in the same
        transaction, so an equal version is the same event arriving twice.
        """
        latest = self._latest.get(market_id)
        if latest is not None and state_version <= latest:
            return False

        self._latest[market_id] = state_version
        return True

    def latest(self, market_id: uuid.UUID) -> int | None:
        """The newest version forwarded for this market, or None. For tests.

        Not offered to clients: it is what this replica saw, not the truth.
        """
        return self._latest.get(market_id)


_gate: VersionGate | None = None


def get_gate() -> VersionGate:
    """The process-wide gate."""
    global _gate
    if _gate is None:
        _gate = VersionGate()
    return _gate


def reset_gate() -> None:
    """Drop the gate. For tests, and for nothing else."""
    global _gate
    _gate = None
