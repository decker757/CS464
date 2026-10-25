"""The check both replays of a pool's feed make. [BE][2.4] #63, [BE] #221

The price history's replay and the void's refund replay each read a market's
trades in feed order and must find their `state_version`s running 1, 2, 3,
... with no gap or repeat. One function, so the two cannot drift: DECISIONS.md,
"The void's refund replay reads `pool_trade_query`, and shares the price
history's 1..N check". Each caller maps the failure to its own answer.

Pure. No session, no clock.
"""

from __future__ import annotations

from collections.abc import Iterable


def refuse_if_versions_not_consecutive(versions: Iterable[int]) -> None:
    """Raise `ValueError`, naming the expected and the actual value, unless
    `versions` run 1, 2, 3, ... in order. None at all is a run.

    The reason is in DECISIONS.md, "The price-history replay refuses trades
    whose state versions do not run 1, 2, 3, ... in order".
    """
    for expected, version in enumerate(versions, start=1):
        if version != expected:
            raise ValueError(f"expected state_version {expected}, got {version}")
