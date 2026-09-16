"""Runtime configuration, read once from the environment at import time.

Extends `shared.config.ServiceSettings`, which carries the settings every
service reads identically — the JWT trio, the access cookie name and CORS.
[F-6] #76. Those are the ones where drift does not fail loudly: a service
verifying against a different issuer fails as "you are not logged in" on a
request carrying a perfectly good session. What stays here is what only this
service has.
"""

from functools import lru_cache

from pydantic import Field

from shared.config import ServiceSettings


class Settings(ServiceSettings):
    # --- The bus ----------------------------------------------------------
    # Required, with no default, for the same reason DATABASE_URL is required
    # in the other four services: a URL in the repository is a credential in
    # the repository the moment somebody puts a password in it, and a default
    # would let a misconfigured deploy start quietly against the wrong Redis —
    # which here means a service that looks healthy and silently broadcasts
    # nothing.
    #
    # Form: redis://[:PASSWORD@]HOST:PORT/DB
    redis_url: str = Field(min_length=1)

    # --- Connection limits ------------------------------------------------
    # How many markets one socket may watch at once. A limit rather than none,
    # because every subscription costs a set entry and a fan-out iteration on
    # every event, and a client that subscribes in a loop is the cheapest
    # denial of service available against a server whose whole job is holding
    # connections open.
    #
    # Generous against real use: [X-4] #37's client watches the market on
    # screen, and a market list page watches what it has rendered.
    max_subscriptions_per_connection: int = Field(default=50, gt=0)

    # How many undelivered events one connection may bank before it is dropped.
    #
    # Dropping is the correct answer for a price feed and not a compromise. The
    # events queued behind a stalled client are prices that are already wrong,
    # so delivering them late is worse than not delivering them: the client
    # reconnects, fetches a snapshot, and resumes from the truth. See
    # `service/subscriptions.py`, which is where this is spent.
    send_queue_size: int = Field(default=64, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
