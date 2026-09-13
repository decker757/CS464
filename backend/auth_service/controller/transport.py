"""How tokens travel between the client and this service.

Two ways in, one way to verify. Browsers get httpOnly cookies so a refresh
survives F5 and no token is reachable from JavaScript. Internal callers such
as the trading engine and the websocket server send `Authorization: Bearer`.
Both funnel into the same signature check in `dependencies.get_current_user`.

Rationale and the rejected alternatives: docs/adr/0002-auth-token-transport.md
"""

from __future__ import annotations

from fastapi import Request, Response

from core.config import get_settings
from model.schemas import TokenPair

_BEARER_PREFIX = "bearer "


def extract_access_token(request: Request) -> str | None:
    """Authorization header wins over the cookie.

    An explicitly attached credential should beat one the browser sent
    ambiently, so a service call carrying its own token is never silently
    reinterpreted as whoever happens to be logged in.
    """
    header = request.headers.get("Authorization", "")
    if header.lower().startswith(_BEARER_PREFIX):
        token = header[len(_BEARER_PREFIX):].strip()
        return token or None

    return request.cookies.get(get_settings().access_cookie_name)


def extract_refresh_token(request: Request) -> str | None:
    """Same precedence rule as the access token: explicit header beats cookie.

    Non-browser clients hold no cookie jar and send X-Refresh-Token instead.
    """
    header = request.headers.get("X-Refresh-Token", "").strip()
    if header:
        return header

    return request.cookies.get(get_settings().refresh_cookie_name)


def set_auth_cookies(response: Response, tokens: TokenPair) -> None:
    settings = get_settings()
    common = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "domain": settings.cookie_domain,
    }
    response.set_cookie(
        settings.access_cookie_name,
        tokens.access_token,
        max_age=settings.access_token_ttl_seconds,
        path="/",
        **common,
    )
    # Path-scoped so the long-lived credential is not attached to every API
    # call. It must still reach /auth/logout, which is what revokes it.
    response.set_cookie(
        settings.refresh_cookie_name,
        tokens.refresh_token,
        max_age=settings.refresh_token_ttl_seconds,
        path=settings.refresh_cookie_path,
        **common,
    )


def clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        settings.access_cookie_name, path="/", domain=settings.cookie_domain
    )
    response.delete_cookie(
        settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        domain=settings.cookie_domain,
    )
