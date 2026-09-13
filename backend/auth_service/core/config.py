"""Runtime configuration, read once from the environment at import time."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
    jwt_secret: str = Field(min_length=16)
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "cs464-auth"
    access_token_ttl_seconds: int = 900          # 15 minutes
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14   # 14 days

    # --- Cookie transport (browser clients) -----------------------------
    access_cookie_name: str = "access_token"
    refresh_cookie_name: str = "refresh_token"
    # Narrower than "/" so the long-lived token never rides along on ordinary
    # API calls, but wide enough to reach both /auth/refresh and /auth/logout.
    refresh_cookie_path: str = "/auth"
    cookie_secure: bool = True       # set False only for plain-http local dev
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None

    # --- Registration policy --------------------------------------------
    password_min_length: int = 12

    # --- CORS -------------------------------------------------------------
    # Cookie auth cannot use "*". The frontend origin must be listed exactly.
    #
    # NoDecode is load-bearing. Without it pydantic-settings tries to JSON-parse
    # any complex type coming from the environment BEFORE field validators run,
    # so a comma-separated CORS_ORIGINS raises SettingsError at import time and
    # the container never starts.
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
