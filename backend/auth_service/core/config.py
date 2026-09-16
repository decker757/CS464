"""Runtime configuration, read once from the environment at import time.

Extends `shared.config.ServiceSettings`, which carries the settings every
service reads identically — the JWT trio, the access cookie name and CORS.
[F-6] #76. Those are the ones where drift does not fail loudly: a service
verifying against a different issuer fails as "you are not logged in" on a
request carrying a perfectly good session. What stays here is what only this
service has.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field

from shared.config import ServiceSettings


class Settings(ServiceSettings):
    # --- Database -------------------------------------------------------
    # Required, with no default on purpose. A default would put a credential in
    # the repository and would let a misconfigured deploy start quietly against
    # the wrong database instead of failing at boot.
    # Form: postgresql+asyncpg://USER:PASSWORD@HOST:PORT/NAME
    database_url: str = Field(min_length=1)

    # --- Token signing --------------------------------------------------
    # Also required. There is no such thing as a safe default signing key: a
    # fallback here would be a published key that silently signs real sessions.
    # HS256 means every service that can verify a token can also mint one.
    # See docs/adr/0002-auth-token-transport.md for the RS256 upgrade path.
    access_token_ttl_seconds: int = 900          # 15 minutes
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14   # 14 days

    refresh_cookie_name: str = "refresh_token"
    # Narrower than "/" so the long-lived token never rides along on ordinary
    # API calls, but wide enough to reach both /auth/refresh and /auth/logout.
    refresh_cookie_path: str = "/auth"
    cookie_secure: bool = True       # set False only for plain-http local dev
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None

    # --- Registration policy --------------------------------------------
    password_min_length: int = 12


@lru_cache
def get_settings() -> Settings:
    return Settings()
