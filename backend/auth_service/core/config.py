"""Runtime configuration, read once from the environment. [F-6] #76

Settings every service shares are in `shared.config.ServiceSettings` (ADR
0012); this adds only what this service needs.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr

from shared.config import ServiceSettings


class Settings(ServiceSettings):
    # --- Database -------------------------------------------------------
    # Required, no default: a default is a credential in the repository.
    # Form: postgresql+asyncpg://USER:PASSWORD@HOST:PORT/NAME
    # A SecretStr for the password in it (#307).
    database_url: SecretStr = Field(min_length=1)

    # How many connections this process keeps open, and how many more it may
    # open under load. Settings, because a managed Postgres has one connection
    # ceiling shared by every replica of every service. DECISIONS.md, "Each
    # service's connection pool is a setting, sized to the database's ceiling".
    database_pool_size: int = Field(default=10, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)

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

    # --- The leaderboard's lookup key [L-1] #38, ADR 0021 -----------------
    # What the ledger presents as `X-Service-Key` to `/internal/users/lookup`.
    # Required, with no default, for `jwt_secret`'s reason: a default key is a
    # published key. ADR 0021 sets the 32-character floor.
    # A SecretStr, as `jwt_secret` is (#307).
    ledger_service_key: SecretStr = Field(min_length=32)

    # --- Registration policy --------------------------------------------
    password_min_length: int = 12

    # --- Rate limits [A-2] #600, ADR 0027 -------------------------------
    # Not a credential, so a default, like the ledger's: the compose service
    # name. Production's `rediss://` URL arrives through the environment.
    redis_url: SecretStr = Field(default=SecretStr("redis://redis:6379/0"))
    rate_limit_key_prefix: str = "auth:rl"
    # Each Redis call gives up after this long, so a hung Redis cannot stall a
    # login; the limiter then fails open.
    rate_limit_redis_timeout_seconds: float = Field(default=0.5, gt=0)

    # Failed logins for one account from one IP. Stops guessing from a machine
    # without letting a stranger lock the owner out from every other address.
    login_failures_per_account_ip: int = Field(default=5, gt=0)
    login_failures_per_account_ip_window_seconds: int = Field(default=900, gt=0)
    # Failed logins for one account from anywhere: the looser cap that still
    # stops guessing spread across many machines.
    login_failures_per_account: int = Field(default=50, gt=0)
    login_failures_per_account_window_seconds: int = Field(default=3600, gt=0)
    # Every login attempt from one IP. Generous, because a room shares one
    # public IP; it is there to stop a flood of Argon2 work, not guessing.
    login_attempts_per_ip: int = Field(default=30, gt=0)
    login_attempts_per_ip_window_seconds: int = Field(default=60, gt=0)
    registrations_per_ip: int = Field(default=20, gt=0)
    registrations_per_ip_window_seconds: int = Field(default=3600, gt=0)

    # --- Paging -----------------------------------------------------------
    # [4.1] #13's user list. The table only grows, so every read is bounded;
    # the same ceilings as the audit and ledger services'.
    default_page_size: int = Field(default=50, gt=0)
    max_page_size: int = Field(default=200, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
