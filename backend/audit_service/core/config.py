"""Runtime configuration, read once from the environment. [F-6] #76

Settings every service shares are in `shared.config.ServiceSettings` (ADR
0012); this adds only what this service needs.
"""

from functools import lru_cache

from pydantic import Field

from shared.config import ServiceSettings


class Settings(ServiceSettings):
    # --- Database -------------------------------------------------------
    # Required, no default: a default is a credential in the repository.
    # Form: postgresql+asyncpg://audit_svc:PASSWORD@HOST:PORT/NAME
    database_url: str = Field(min_length=1)

    # --- Paging -----------------------------------------------------------
    # The log only grows, so every read is bounded. Settings rather than
    # constants so the page size can follow what the console renders.
    default_page_size: int = Field(default=50, gt=0)
    max_page_size: int = Field(default=200, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
