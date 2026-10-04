"""Runtime configuration, read once from the environment at import time.

Extends `shared.config.ServiceSettings`, the settings every service must read
identically (ADR 0012). What stays here is what only this service has.
"""

from decimal import Decimal
from functools import lru_cache

from pydantic import Field

from shared.config import ServiceSettings


class Settings(ServiceSettings):
    # --- Database -------------------------------------------------------
    # Required, with no default: a default is a credential in the repository,
    # and would let a misconfigured deploy start against the wrong database.
    # Form: postgresql+asyncpg://ledger_svc:PASSWORD@HOST:PORT/NAME
    database_url: str = Field(min_length=1)

    # --- The starting grant ------------------------------------------------
    # [B-1] #32's starting amount, minted lazily on a user's first read
    # (ADR 0009). A default because it is not a credential. Changing it
    # re-grants nobody: it reaches accounts created afterwards and no others.
    starting_credits: Decimal = Field(default=Decimal("1000"), gt=0)

    # --- The market terms pull [F-7] #96, D-008, D-031 ---------------------
    # `market` is compose's hostname for market_service. A default because it
    # is not a credential, and CI's boot check does not set it.
    market_service_url: str = Field(default="http://market:8000")

    # --- The price broadcast's bus [F-9] #112, ADR 0010 --------------------
    # A default, unlike the realtime service's required one: a wrong Redis
    # here costs a broadcast, not the service (D-046). It must still parse, or
    # the ledger refuses to boot (D-051). Not validated here as well:
    # `from_url` is the parser that has to accept it.
    redis_url: str = Field(default="redis://redis:6379/0")

    # --- Paging -----------------------------------------------------------
    # Bounded because a ledger only grows. Same ceilings as the audit service.
    default_page_size: int = Field(default=50, gt=0)
    max_page_size: int = Field(default=200, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
