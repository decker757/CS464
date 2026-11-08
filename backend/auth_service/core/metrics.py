"""The auth service's rate-limiter metrics. #599

Not in `shared/`: each service owns the metrics of the events it owns.

Every label combination is created here, at import, so each series reads 0
before its first event instead of being missing. Label values come from fixed
sets, never from a request: a series per address or account would grow the
time-series database without limit and put both into it.
"""

from __future__ import annotations

from prometheus_client import Counter

# The names in `AttemptLimits.from_settings` (service/attempt_limits.py);
# `test_every_limit_the_limiter_has_is_pre_registered` keeps the two in step.
LIMIT_NAMES = ("login-account-ip", "login-account", "login-ip", "register-ip")

# Each exported with `_total` after it: the client appends the suffix itself.
LIMITER_DECISIONS = Counter(
    "auth_limiter_decisions",
    "Attempt-limiter decisions, by limit and outcome",
    ["limit", "outcome"],
)
# Not a decision: the limiter was off because Redis was unreachable.
LIMITER_FAIL_OPEN = Counter(
    "auth_limiter_fail_open",
    "Times the attempt limiter failed open because Redis was unreachable, by limit",
    ["limit"],
)

for _limit in LIMIT_NAMES:
    LIMITER_FAIL_OPEN.labels(limit=_limit)
    for _outcome in ("allowed", "refused"):
        LIMITER_DECISIONS.labels(limit=_limit, outcome=_outcome)
