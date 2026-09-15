"""Runtime configuration, read once from the environment at import time."""

from decimal import Decimal
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database -------------------------------------------------------
    # Required, with no default, for the same reasons as the other three
    # services: a default is a credential in the repository, and it would let a
    # misconfigured deploy start quietly against the wrong database.
    # Form: postgresql+asyncpg://ledger_svc:PASSWORD@HOST:PORT/NAME
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

    # --- The starting grant ------------------------------------------------
    # [B-1] #32's "configured fixed starting-credit amount". Minted lazily, as
    # a real ledger transaction, the first time anything reads a user's balance
    # — see service/grants.py for why that and not an event or an outbox.
    #
    # Has a default, unlike the two settings above, for the same reason the
    # market service's `default_liquidity_b` does: it is not a credential.
    # Nobody on this team disagrees about the number, and making it required
    # would mean three people editing .env before compose will start in order
    # to restate it.
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

    # --- Paging -----------------------------------------------------------
    # A ledger only grows, so an unbounded history read is a question that gets
    # slower every week it is asked. Same ceilings, and the same reasoning, as
    # the audit service's.
    default_page_size: int = Field(default=50, gt=0)
    max_page_size: int = Field(default=200, gt=0)

    # --- CORS -------------------------------------------------------------
    # NoDecode is load-bearing: without it pydantic-settings JSON-parses a
    # complex field straight from the environment before any validator runs, so
    # a comma-separated CORS_ORIGINS raises at import time and the container
    # never starts. All three existing services were bitten by exactly this.
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
