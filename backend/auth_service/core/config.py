"""Runtime configuration, read once from the environment. [F-6] #76

Settings every service shares are in `shared.config.ServiceSettings` (ADR
0012); this adds only what this service needs.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field

from shared.config import ServiceSettings


class Settings(ServiceSettings):
    # --- Database -------------------------------------------------------
    # Required, no default: a default is a credential in the repository.
    # Form: postgresql+asyncpg://USER:PASSWORD@HOST:PORT/NAME
    database_url: str = Field(min_length=1)

    # --- Tokens and cookies ---------------------------------------------
    # The signing key itself is in ServiceSettings, required with no default.
    # HS256 means any service that can verify a token can also mint one; ADR
    # 0002 names RS256 as the upgrade path.
    access_token_ttl_seconds: int = 900          # 15 minutes
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14   # 14 days

    refresh_cookie_name: str = "refresh_token"
    # Narrower than "/", but must reach both /auth/refresh and /auth/logout.
    refresh_cookie_path: str = "/auth"
    cookie_secure: bool = True       # set False only for plain-http local dev
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None

    # --- Registration policy --------------------------------------------
    password_min_length: int = 12

    # --- Paging -----------------------------------------------------------
    # [4.1] #13's user list. The table only grows, so every read is bounded;
    # the same ceilings as the audit and ledger services'.
    default_page_size: int = Field(default=50, gt=0)
    max_page_size: int = Field(default=200, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
