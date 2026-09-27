"""Where the access token is read from. ADR 0002.

Read-only: this service never issues a session. Copied from the other services
on purpose, not shared. ADR 0012.
"""

from __future__ import annotations

from fastapi import Request

from core.config import get_settings

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
