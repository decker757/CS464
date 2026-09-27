"""Where the access token and the browser's origin are read from. [F-2] #42

Read-only: this service consumes a session and never issues one. The token
precedence rule is copied, not shared, in each service's `transport.py`
(ADR 0012). `HTTPConnection` is the common base of `Request` and `WebSocket`.
"""

from __future__ import annotations

from starlette.requests import HTTPConnection

from core.config import get_settings

_BEARER_PREFIX = "bearer "


def extract_access_token(connection: HTTPConnection) -> str | None:
    """The bearer header if there is one, else the access cookie. ADR 0002.

    A browser always arrives on the cookie: the WebSocket API cannot set
    headers. Do not add a query-string token as a workaround; a URL is logged
    by every proxy. docs/api/realtime-service.md.
    """
    header = connection.headers.get("Authorization", "")
    if header.lower().startswith(_BEARER_PREFIX):
        token = header[len(_BEARER_PREFIX):].strip()
        return token or None

    return connection.cookies.get(get_settings().access_cookie_name)


def origin_allowed(connection: HTTPConnection) -> bool:
    """Whether a browser at this origin may open a socket here.

    Not a duplicate of `CORSMiddleware`, which never sees a WebSocket
    handshake: this is the socket's only origin check, and the only defence
    once ADR 0002's `SameSite=None` arrives. ADR 0010. A missing Origin is
    allowed: non-browser callers send none, and the attack is a browser's.
    """
    origin = connection.headers.get("origin")
    if origin is None:
        return True

    return origin in get_settings().cors_origins
