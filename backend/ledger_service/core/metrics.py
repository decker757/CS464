"""The ledger's business metrics. #599

Trades, fills, settlements and voids are counted after the commit that makes
them real, so a rollback or a replay is never counted. Not in `shared/`: each
service owns the metrics of the events it owns (the design's "Business
metrics are not shared").

Every label combination is created here, at import, so each series reads 0
before its first event instead of being missing. Label values come from fixed
sets, never from a request: one series per user or market would grow the
time-series database without limit.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

from core.pricing import Side

# A free lock answers in about a millisecond; a queue behind a settlement or
# a void can take seconds.
LOCK_WAIT_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0)

# Each exported with `_total` after it: the client appends the suffix itself.
TRADES_COMMITTED = Counter(
    "ledger_trades_committed", "Trades committed, by side", ["side"]
)
BOOK_LOCK_WAIT = Histogram(
    "ledger_book_lock_wait_seconds",
    "Time spent waiting for a market's book row lock",
    buckets=LOCK_WAIT_BUCKETS,
)
ORDER_FILLS = Counter("ledger_order_fills", "Limit-order fills committed")
SETTLEMENTS = Counter("ledger_settlements", "Markets settled")
VOIDS = Counter("ledger_voids", "Markets voided")
PUBLISH_FAILURES = Counter(
    "ledger_publish_failures",
    "Frames that failed to publish after their commit, by channel",
    ["channel"],
)

for _side in Side:
    TRADES_COMMITTED.labels(side=_side.value)
# The two values of `service/bus.py`'s `PRICE_CHANNEL` and `ORDER_CHANNEL`
# (core cannot import service); test_business_metrics.py fails if they drift.
for _channel in ("market.price", "order.update"):
    PUBLISH_FAILURES.labels(channel=_channel)
