"""How the access token, and the browser's origin, reach this service.

Read-only by design, like every transport module outside the auth service: this
service consumes a session it did not create and has no business issuing one.

The precedence rule is copied deliberately, not shared. It is part of ADR 0002's
contract and every service has to agree on it.

Both functions take a Starlette `HTTPConnection`, which is the common base of
`Request` and `WebSocket`. That is not a generalisation for its own sake — it is
what lets the socket handshake and `/health` run the same check, so there is one
answer in this service to "who is calling" rather than a second one that only
applies to WebSockets and only gets audited half as often.
"""

from __future__ import annotations

from starlette.requests import HTTPConnection

from core.config import get_settings

_BEARER_PREFIX = "bearer "


def extract_access_token(connection: HTTPConnection) -> str | None:
    """Authorization header wins over the cookie.

    An explicitly attached credential should beat one the browser sent
    ambiently, so a service call carrying its own token is never silently
    reinterpreted as whoever happens to be logged in.

    In practice the two halves split cleanly here, in a way they do not for the
    HTTP services: the JavaScript WebSocket API has no parameter for request
    headers, so a browser physically cannot use the first branch and always
    arrives on the second. That is why ADR 0002's `SameSite=Lax` deployment
    constraint is load-bearing for this service specifically — if the frontend
    and the API are not same-site, the cookie is never sent, the handshake is
    refused, and live prices simply do not work.

    The obvious workaround is a token in the query string. It is not taken: a
    URL is logged by every proxy, every access log and the browser's own
    history, and an access token in all three is a credential written down in
    the places designed to keep records forever.
    """
    header = connection.headers.get("Authorization", "")
    if header.lower().startswith(_BEARER_PREFIX):
        token = header[len(_BEARER_PREFIX):].strip()
        return token or None

    return connection.cookies.get(get_settings().access_cookie_name)


def origin_allowed(connection: HTTPConnection) -> bool:
    """Whether a browser at this origin may open a socket here.

    This has no counterpart in the other four services, and it exists because
    the `CORSMiddleware` in `main.py` does not apply to WebSockets. There is no
    preflight on a socket handshake and the browser enforces nothing about who
    may open one, so a page on any origin can open a socket to this service and
    the browser will attach whatever cookies the same-site rules allow.

    Today `SameSite=Lax` means it attaches none, and a cross-site handshake
    arrives unauthenticated and is refused on the token. ADR 0002 records that
    switching to `SameSite=None` before [5.3] #19 is a live possibility, and on
    that day this function is the only thing left between a logged-in victim and
    a feed opened by somebody else's page.

    **A missing Origin is allowed.** Non-browser callers — the trading service,
    a test, `websocat` — send none, and the attack being refused is a browser
    attack, which by definition sends one. Refusing an absent Origin would lock
    out every service-to-service caller to inconvenience an attacker who can
    simply not be a browser, and who in that case has no victim's cookie to
    borrow.
    """
    origin = connection.headers.get("origin")
    if origin is None:
        return True

    return origin in get_settings().cors_origins
