"""Runtime configuration, read once from the environment at import time."""

from decimal import Decimal
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database -------------------------------------------------------
    # Required, with no default, for the same reasons as the auth service: a
    # default is a credential in the repository, and it would let a
    # misconfigured deploy start quietly against the wrong database.
    # Form: postgresql+asyncpg://market_svc:PASSWORD@HOST:PORT/NAME
    database_url: str = Field(min_length=1)

    # --- Token verification ---------------------------------------------
    # This service only ever VERIFIES. It never mints, and core/security.py has
    # no encode path at all. The value must match the auth service's, and all
    # three of algorithm, issuer and secret have to agree or every request 401s.
    jwt_secret: str = Field(min_length=16)
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "cs464-auth"

    # Browsers send the access token as this cookie; services send a bearer
    # header. Must match the auth service's ACCESS_COOKIE_NAME.
    access_cookie_name: str = "access_token"

    # --- Market pricing ---------------------------------------------------
    # The liquidity parameter a new market gets when the administrator does not
    # choose one. [1.2] #2 requires a configured default that a per-market value
    # may override, and the override arrives in the request body.
    #
    # Unlike the two settings above this one HAS a default, because it is not a
    # credential. A wrong database URL connects to the wrong database and a
    # published signing key forges sessions; a wrong `b` makes prices move at
    # the wrong speed, which is visible, harmless in mock credits, and fixable
    # per market. Making it required would mean every teammate edits .env before
    # compose will start, to restate a number nobody disagrees about.
    #
    # Decimal rather than float for the same reason the columns are Numeric: it
    # shares arithmetic with the seed subsidy, which is credits.
    default_liquidity_b: Decimal = Field(default=Decimal("100"), gt=0)

    # --- CORS -------------------------------------------------------------
    # NoDecode is load-bearing: without it pydantic-settings JSON-parses a
    # complex field straight from the environment before any validator runs, so
    # a comma-separated CORS_ORIGINS raises at import time and the container
    # never starts. The auth service was bitten by exactly this.
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:5173",
        "http://localhost:3000",
    ]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Accept CORS_ORIGINS="http://a.com,http://b.com" from the environment."""
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
