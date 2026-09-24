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
    # Required, with no default, for the same reasons as the other three
    # services: a default is a credential in the repository, and it would let a
    # misconfigured deploy start quietly against the wrong database.
    # Form: postgresql+asyncpg://ledger_svc:PASSWORD@HOST:PORT/NAME
    database_url: str = Field(min_length=1)

    # --- The starting grant ------------------------------------------------
    # [B-1] #32's "configured fixed starting-credit amount". Minted lazily, as
    # a real ledger transaction, the first time anything reads a user's balance
    # — see service/grants.py for why that and not an event or an outbox.
    #
    # Has a default, unlike `database_url` above and the inherited JWT secret,
    # for the same reason the market service's `default_liquidity_b` does: it is
    # not a credential. Nobody on this team disagrees about the number, and
    # making it required would mean three people editing .env before compose
    # will start in order to restate it.
    #
    # Changing it does NOT re-grant anybody. The grant is keyed on the user id
    # and written once, so a change here reaches accounts created after it and
    # nothing else. That is the honest behaviour for an append-only ledger:
    # there is no path that rewrites a transaction somebody has already traded
    # against.
    #
    # Decimal rather than float or int because it shares arithmetic with every
    # other amount in this service, and those are Numeric columns.
    starting_credits: Decimal = Field(default=Decimal("1000"), gt=0)

    # --- The market terms pull [F-7] #96, D-008, D-031 ---------------------
    # Where `service/market_terms.py` reads a published market's terms from,
    # on the first request that touches its book. `market` is the hostname
    # compose gives that service on the shared network.
    #
    # Has a default, unlike `database_url` above, because it is not a
    # credential and because ci-backend.yml's "Verify the app boots" step
    # calls `create_app()` with only four environment variables set — this is
    # not one of them, so a required field here would fail that step rather
    # than the deploy it exists to catch.
    market_service_url: str = Field(default="http://market:8000")

    # --- The price broadcast's bus [F-9] #112, ADR 0010 --------------------
    # `redis` is the hostname compose gives the bus on the shared network, the
    # same shape `market_service_url` above uses for a service name.
    #
    # Has a default, unlike `database_url` and the inherited JWT secret,
    # because it is not a credential and because `ci-backend.yml`'s "Verify
    # the app boots" step calls `create_app()` with only four environment
    # variables set — this is not one of them, so a required field here would
    # fail that step rather than the deploy it exists to catch.
    #
    # Deliberately not required the way `realtime_service.REDIS_URL` is.
    # `docs/api/realtime-service.md` states the asymmetry: point that service
    # at the wrong Redis and it starts, reports healthy, and relays nothing —
    # the failure is the whole service and it is silent. Point this service at
    # the wrong Redis and it loses one broadcast per trade: the publish is
    # fire-and-forget (`service/bus.py`), the trade has already committed and
    # is still correct, and the client reconciles on its next snapshot. One
    # lost frame is not worth a ledger that will not start.
    #
    # **That is a promise about an unreachable Redis, not a mistyped URL.** The
    # value still has to parse: `main.py`'s lifespan hands it to
    # `redis.asyncio.from_url`, which rejects a URL that is not `redis://`,
    # `rediss://` or `unix://` on the spot, and the ledger refuses to start.
    # Deliberately — an outage ends, a typo does not, and a typo swallowed
    # would drop every broadcast forever from a service that looks healthy.
    # Not validated here as well: `from_url` is the parser that has to accept
    # it, and a second rule here could only disagree with it. See "A mistyped
    # `REDIS_URL` stops the ledger booting; an unreachable one does not" in
    # DECISIONS.md.
    redis_url: str = Field(default="redis://redis:6379/0")

    # --- Paging -----------------------------------------------------------
    # A ledger only grows, so an unbounded history read is a question that gets
    # slower every week it is asked. Same ceilings, and the same reasoning, as
    # the audit service's.
    default_page_size: int = Field(default=50, gt=0)
    max_page_size: int = Field(default=200, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
