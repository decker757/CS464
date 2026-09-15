"""Runtime configuration, read once from the environment at import time."""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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

    # --- Token verification ----------------------------------------------
    # This service only ever VERIFIES. It never mints, core/security.py has no
    # encode path, and a test asserts one does not appear. The value must match
    # the auth service's, and all three of algorithm, issuer and secret have to
    # agree or every handshake is refused.
    jwt_secret: str = Field(min_length=16)
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "cs464-auth"

    # Browsers send the access token as this cookie; services send a bearer
    # header. Must match the auth service's ACCESS_COOKIE_NAME.
    #
    # The cookie matters more here than anywhere else in the backend. A browser
    # cannot set request headers on a WebSocket — the API has no place to put
    # them — so the cookie is the *only* transport a browser has, and ADR 0002's
    # same-registrable-domain constraint stops being a deployment footnote and
    # becomes the thing that decides whether live prices work at all.
    access_cookie_name: str = "access_token"

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

    # --- CORS -------------------------------------------------------------
    # NoDecode is load-bearing: without it pydantic-settings JSON-parses a
    # complex field straight from the environment before any validator runs, so
    # a comma-separated CORS_ORIGINS raises at import time and the container
    # never starts. All four existing services were bitten by exactly this.
    #
    # Note that CORS does not police WebSockets — the browser sends no preflight
    # and enforces no origin rule on them, so this list guards `/health` and
    # `/docs` and nothing else. The socket's own origin check is in
    # `controller/routes.py`, and it has to be written out by hand for exactly
    # this reason.
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
