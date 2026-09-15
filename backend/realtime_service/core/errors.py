"""Domain errors.

Plain exceptions with no framework imports, so the service layer can raise them
without knowing WebSockets exist.

**There is no `controller/errors.py` in this service, and that is deliberate.**
Every other service here maps a domain error onto an HTTP status code, because
every other service answers HTTP requests. This one's only HTTP surface is
`/health` and `/docs`, and neither can raise anything below. What this service
actually has to get right is the *socket* vocabulary: which conditions close a
connection, with what code, and which are reported in band and leave it open.
That is the table below, and it is the contract [X-4] #37's client reads.

Close codes are in the 4000-4999 range, which RFC 6455 reserves for the
application and which is the only range a browser can read back off a closed
socket together with a reason. A client has to tell "your token expired, get a
new one and reconnect" apart from "the network went away", and #37's last three
acceptance criteria are all about that distinction.

| Condition | Code | What the client should do |
| --- | --- | --- |
| No, malformed or foreign token | 4401 | Refresh the session, then reconnect. |
| Origin not in the allowlist | 4403 | Nothing. This is not your server. |
| Token expired mid-connection | 4408 | Refresh the session, then reconnect. |
| Too far behind to catch up | 4409 | Reconnect, snapshot, resume. |

Everything else is a `ClientProtocolError`: the connection stays open and the
client is told what it sent wrong. A malformed frame is a bug in one caller,
not grounds for dropping a session that is otherwise fine.
"""

from __future__ import annotations

# RFC 6455 caps a close reason at 123 bytes, and a frame that exceeds it is a
# protocol error rather than a truncated message — so the reason gets cut in
# `controller/routes.py` before it is sent. Every `message` below is written to
# fit, but the cut is there because the next one somebody adds will not be.
MAX_CLOSE_REASON_BYTES = 123


class RealtimeError(Exception):
    """Base class for every realtime domain error."""

    close_code: int = 1008  # policy violation
    code: str = "realtime_error"
    message: str = "Realtime error."


class NotAuthenticated(RealtimeError):
    """Missing, malformed, expired or foreign access token at the handshake.

    Note the shape this forces on `controller/routes.py`: the connection is
    accepted and then immediately closed, rather than refused outright. A
    handshake rejected before the upgrade completes reaches a browser as an
    opaque failure with no code and no reason — the WebSocket API gives
    JavaScript no way to read the HTTP status — so the client cannot tell an
    expired session from a service that is down, and #37's "visibly indicated"
    state becomes a guess. Accepting costs one round trip and buys the client
    an answer.
    """

    close_code = 4401
    code = "invalid_token"
    message = "Not authenticated."


class OriginNotAllowed(RealtimeError):
    """The handshake carried a browser Origin that is not in CORS_ORIGINS.

    This check has to be written out by hand, and it is the one piece of this
    service that has no counterpart in the other four. CORS does not apply to
    WebSockets: there is no preflight, and the browser enforces nothing about
    who may open one. The `CORSMiddleware` in `main.py` guards `/health` and
    `/docs` and does not look at this route.

    What stops a page on evil.com opening a socket here as a logged-in user
    today is `SameSite=Lax`, which keeps the cookie off a cross-site handshake.
    ADR 0002 records that switching to `SameSite=None` is a real possibility
    before [5.3] #19 — and on the day that happens, this check is the only
    thing standing between a logged-in victim and a live feed opened by
    somebody else's page.

    Absent Origin is allowed: a service-to-service caller sends none, and the
    attack this refuses is a browser attack, which by definition sends one.
    """

    close_code = 4403
    code = "origin_not_allowed"
    message = "This origin may not open a socket."


class SessionExpired(RealtimeError):
    """The access token ran out while the connection was open.

    Not a handshake failure — this connection was authorised, and stopped
    being. Every other service in this repository checks `exp` once per request
    and is finished in milliseconds. A socket accepted on a fifteen-minute
    token can still be open hours later, and ADR 0002's bounded-revocation
    argument only holds if something eventually acts on the bound.
    """

    close_code = 4408
    code = "session_expired"
    message = "Access token expired. Reconnect with a fresh one."


class SlowConsumer(RealtimeError):
    """The connection banked more undelivered events than it is allowed to.

    Dropped rather than buffered, and this is the right answer rather than a
    concession. Everything queued behind a stalled client is a price that is
    already stale, so delivering it late is worse than not delivering it at
    all. The client reconnects, fetches a snapshot and resumes from the truth,
    which is the protocol working exactly as [X-4] #37 specifies.
    """

    close_code = 4409
    code = "slow_consumer"
    message = "Too far behind. Reconnect and fetch a snapshot."


class ClientProtocolError(RealtimeError):
    """The client sent something this server does not understand.

    Reported in band and the connection stays open. A client that mistypes one
    frame has a bug in one frame, and dropping the socket would turn that into
    a reconnect storm against a server whose entire job is holding connections
    open.
    """

    code = "bad_command"
    message = "Unrecognised command."


class MalformedCommand(ClientProtocolError):
    """Not JSON, or not a JSON object."""

    code = "malformed_command"
    message = 'Send a JSON object, for example {"action":"subscribe","market_id":"..."}.'


class UnknownAction(ClientProtocolError):
    """A JSON object whose `action` is not one this server serves."""

    code = "unknown_action"
    message = 'Supported actions are "subscribe" and "unsubscribe".'


class InvalidMarketId(ClientProtocolError):
    """`market_id` was absent or not a UUID.

    Deliberately not "no such market". This service holds no market table and
    cannot tell a market that does not exist from one that exists and has never
    traded — it would need a grant on `market.*`, which `sql/02-schemas.sql`
    exists to refuse. Subscribing to an id that names nothing is legal and
    silent, and simply never delivers anything.
    """

    code = "invalid_market_id"
    message = "`market_id` must be a UUID."


class TooManySubscriptions(ClientProtocolError):
    """This connection is already watching as many markets as it may.

    Refused rather than evicting the oldest subscription, because a client that
    hits this has lost track of what it is watching, and silently dropping a
    market it believes it is subscribed to would show somebody a price that
    quietly stopped updating.
    """

    code = "too_many_subscriptions"
    message = "This connection is watching as many markets as it may."
