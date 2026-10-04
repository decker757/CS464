"""How the access token reaches this service. Read-only: this service issues no session.

The precedence rule is a deliberate copy of the auth service's twin, from ADR
0002's contract.
"""

from __future__ import annotations

from fastapi import Request

from core.config import get_settings

_BEARER_PREFIX = "bearer "


def extract_access_token(request: Request) -> str | None:
    """The bearer token if there is one, else the cookie, else None.

    An explicit credential beats one the browser sent ambiently. A bearer
    header with an empty token returns None without trying the cookie.
    """
    header = request.headers.get("Authorization", "")
    if header.lower().startswith(_BEARER_PREFIX):
        token = header[len(_BEARER_PREFIX):].strip()
        return token or None

    return request.cookies.get(get_settings().access_cookie_name)
