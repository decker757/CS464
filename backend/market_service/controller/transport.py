"""How the access token reaches this service.

Read-only by design. Unlike the auth service's transport, there is no
set_auth_cookies here: this service consumes a session it did not create and
has no business issuing one.

The precedence rule is copied deliberately, not shared. It is part of ADR
0002's contract and both services have to agree on it.
"""

from __future__ import annotations

from fastapi import Request

from core.config import get_settings

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
