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
    # --- Database -------------------------------------------------------
    # Required, with no default, for the same reasons as the other services: a
    # default is a credential in the repository, and it would let a
    # misconfigured deploy start quietly against the wrong database.
    # Form: postgresql+asyncpg://audit_svc:PASSWORD@HOST:PORT/NAME
    database_url: str = Field(min_length=1)

    # --- Paging -----------------------------------------------------------
    # An audit log only grows, so an unbounded read is a question that gets
    # slower every week it is asked. These are the ceilings the route applies;
    # they are settings rather than constants only because the right page size
    # depends on what the console renders, which is Michelle's call and may
    # differ between a laptop and a deploy.
    default_page_size: int = Field(default=50, gt=0)
    max_page_size: int = Field(default=200, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
