"""The market service's business metrics. #599

The close sweep is the one background job whose silence nobody would notice,
so it reports two numbers: how late each sweep woke, and when one last
finished. Not in `shared/`: each service owns the metrics of what it owns.
"""

from __future__ import annotations

from prometheus_client import Gauge

CLOSE_SWEEP_LAG = Gauge(
    "market_close_sweep_lag_seconds",
    "Seconds the oldest due open market had waited when the last sweep began; 0 when none was due",
)
CLOSE_SWEEP_LAST_SUCCESS = Gauge(
    "market_close_sweep_last_success_timestamp_seconds",
    "Unix time the last close sweep that did not fail finished",
)
