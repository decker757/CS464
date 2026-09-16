"""The settings every service reads identically. [F-6] #76

ADR 0005's "settings base". Each service subclasses `ServiceSettings` and adds
what only it needs — `database_url` for the four that have a database,
`redis_url` for the one that does not, page sizes, sweep intervals, starting
credits.

What lives here is the part that must not diverge: a service verifying tokens
against a different issuer, or reading a different cookie name, does not fail
loudly. It fails as "you are not logged in" on a request carrying a perfectly
good session, which is among the least debuggable symptoms this system can
produce.

`database_url` is deliberately *not* here, even though four of the five declare
it. The realtime service has no database and must not grow one (ADR 0010), and
a base class that handed it the field would make that a matter of restraint
rather than of structure.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class ServiceSettings(BaseSettings):
    """Configuration common to every backend service.

    `extra="ignore"` matters more here than it looks: one `.env` at the repo
    root feeds every service, so each one is always reading a file that is
    mostly somebody else's variables.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # No default, on purpose. A default signing key is a published signing key,
    # and it would sign real sessions in any deployment that forgot to set one.
    jwt_secret: str = Field(min_length=16)
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "cs464-auth"

    # Browsers send the access token as this cookie; services send a bearer
    # header. Every service has to agree with the auth service's value, which
    # is most of why it is here rather than declared five times.
    access_cookie_name: str = "access_token"

    # --- CORS -------------------------------------------------------------
    # `NoDecode` is load-bearing: without it pydantic-settings JSON-parses a
    # complex field straight from the environment *before* any validator runs,
    # so a comma-separated CORS_ORIGINS raises at import time and the container
    # never starts. Every service in this repository has been bitten by exactly
    # this, which is now one annotation protecting all five rather than five
    # copies that had to stay correct independently. Guarded by a test in each
    # suite that sets the environment variable — a constructor keyword argument
    # comes from the init source, never JSON-decodes, and proves nothing.
    #
    # **This list does two unrelated jobs, and the second one is not CORS.**
    # CORS does not police WebSockets: the browser sends no preflight and
    # enforces no origin rule on a handshake, so for the realtime service this
    # list guards `/health` and `/docs` and nothing else. That service's socket
    # checks the origin itself, by hand, in
    # `realtime_service/controller/transport.py::origin_allowed`, reading this
    # same setting — and on the day ADR 0002's `SameSite=None` possibility
    # arrives it is the only thing standing between a logged-in user and a feed
    # opened by another site. Narrowing or reworking the default here changes
    # who may open a price socket.
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
