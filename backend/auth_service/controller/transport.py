"""How tokens travel: httpOnly cookies for browsers, bearer headers for services.

Both reach the one signature check in `dependencies.get_current_user`. ADR 0002.
"""

from __future__ import annotations

from fastapi import Request, Response

from core.config import get_settings
from model.schemas import TokenPair

_BEARER_PREFIX = "bearer "


def extract_access_token(request: Request) -> str | None:
    """Return the access token from the Authorization header, else the cookie.

    ADR 0002: the header wins. A bearer header with an empty token returns
    None without falling back to the cookie.
    """
    header = request.headers.get("Authorization", "")
    if header.lower().startswith(_BEARER_PREFIX):
        token = header[len(_BEARER_PREFIX):].strip()
        return token or None

    return request.cookies.get(get_settings().access_cookie_name)


def extract_refresh_token(request: Request) -> str | None:
    """Return the X-Refresh-Token header, else the refresh cookie, else None.

    Header first, as for the access token; non-browser clients have no cookie jar.
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
    # Path-scoped so the long-lived credential is not sent on every call. It
    # must still reach /auth/logout, which revokes it.
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
