"""Runtime configuration, read once from the environment at import time.

Extends `shared.config.ServiceSettings`, which carries the settings every
service reads identically — the JWT trio, the access cookie name and CORS.
[F-6] #76. Those are the ones where drift does not fail loudly: a service
verifying against a different issuer fails as "you are not logged in" on a
request carrying a perfectly good session. What stays here is what only this
service has.
"""

from decimal import Decimal
from functools import lru_cache

from pydantic import Field

from shared.config import ServiceSettings


class Settings(ServiceSettings):
    # --- Database -------------------------------------------------------
    # Required, with no default, for the same reasons as the auth service: a
    # default is a credential in the repository, and it would let a
    # misconfigured deploy start quietly against the wrong database.
    # Form: postgresql+asyncpg://market_svc:PASSWORD@HOST:PORT/NAME
    database_url: str = Field(min_length=1)

    # --- Market pricing ---------------------------------------------------
    # The liquidity parameter a new market gets when the administrator does not
    # choose one. [1.2] #2 requires a configured default that a per-market value
    # may override, and the override arrives in the request body.
    #
    # Unlike `database_url` above, and unlike the JWT secret it inherits, this
    # one HAS a default, because it is not a credential. A wrong database URL
    # connects to the wrong database and a published signing key forges
    # sessions; a wrong `b` makes prices move at the wrong speed, which is
    # visible, harmless in mock credits, and fixable per market. Making it
    # required would mean every teammate edits .env before compose will start,
    # to restate a number nobody disagrees about.
    #
    # Decimal rather than float for the same reason the columns are Numeric: it
    # shares arithmetic with the seed subsidy, which is credits.
    default_liquidity_b: Decimal = Field(default=Decimal("100"), gt=0)

    # --- Automatic close [F-4] #44 ----------------------------------------
    # How often the background sweeper looks for markets whose closing time has
    # passed, and how many it moves to CLOSED in one transaction.
    #
    # Read `service/closing.py` before tuning either of these, because the
    # obvious intuition about them is wrong. This interval is NOT how long a
    # closed market can still be traded — that window is zero, because the
    # trade path derives the answer from `close_time` and never from the status
    # column. It is only how stale a status count on an admin dashboard may be.
    # Which is why ten seconds is a comfortable default rather than a nervous
    # one, and why lowering it buys very little.
    #
    # The batch is a ceiling, not a target. It bounds how many rows one
    # transaction locks, so a backlog — the service was down over a weekend and
    # a hundred markets came due — is worked through in bounded chunks instead
    # of one long transaction holding locks against live traffic. The sweeper
    # drains back-to-back batches rather than sleeping between them, so the
    # ceiling costs no wall-clock time.
    close_sweep_seconds: float = Field(default=10.0, gt=0)
    close_sweep_batch: int = Field(default=100, gt=0)

    # Lets an operator stop this replica from sweeping without a code change:
    # to run a single designated sweeper, or to hold the background writer
    # still while investigating something.
    #
    # Turning it off does not make a closed market tradeable again. It stops
    # the status column being maintained, so dashboards and status filters go
    # stale while trades stay correctly refused.
    close_sweep_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
