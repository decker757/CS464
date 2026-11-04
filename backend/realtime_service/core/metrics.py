"""The realtime service's metrics. #599

Not in `shared/`: each service owns the metrics of what it owns.
"""

from __future__ import annotations

from prometheus_client import Gauge

OPEN_WEBSOCKETS = Gauge(
    "realtime_open_websockets",
    "Price sockets that passed the origin and token checks and have not yet ended",
)
