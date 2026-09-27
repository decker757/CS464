"""Domain errors, and the close code each one ends a socket with. [F-2] #42

No framework imports, so `service/` can raise these without knowing about
WebSockets. The close codes, and what a client should do about each, are the
table in `docs/api/realtime-service.md`. `OriginNotAllowed` is the exception:
it is refused before the upgrade, so the browser sees an HTTP 403 and never a
4403 close frame. A `ClientProtocolError` is answered in band and leaves the
socket open.
"""

from __future__ import annotations

# RFC 6455's limit on a close reason. A longer one is a protocol error and the
# client loses the reason entirely, so `controller/routes.py` cuts to this.
MAX_CLOSE_REASON_BYTES = 123


class RealtimeError(Exception):
    """Base class for every realtime domain error."""

    close_code: int = 1008  # policy violation
    code: str = "realtime_error"
    message: str = "Realtime error."


class NotAuthenticated(RealtimeError):
    """Missing, malformed, expired or foreign access token at the handshake.

    Sent by accepting and then closing, not by refusing the upgrade: a browser
    cannot read why a handshake was refused. docs/api/realtime-service.md.
    """

    close_code = 4401
    code = "invalid_token"
    message = "Not authenticated."


class OriginNotAllowed(RealtimeError):
    """The handshake carried a browser Origin that is not in CORS_ORIGINS.

    CORS does not apply to WebSockets, so this check is hand-written, and on
    the day ADR 0002's `SameSite=None` arrives it is the only defence against a
    feed opened by another site. ADR 0010.
    """

    close_code = 4403
    code = "origin_not_allowed"
    message = "This origin may not open a socket."


class SessionExpired(RealtimeError):
    """The access token ran out while the connection was open. ADR 0010."""

    close_code = 4408
    code = "session_expired"
    message = "Access token expired. Reconnect with a fresh one."


class SlowConsumer(RealtimeError):
    """The connection banked more undelivered frames than it may.

    Dropped, not buffered: queued prices are already stale. ADR 0010.
    """

    close_code = 4409
    code = "slow_consumer"
    message = "Too far behind. Reconnect and fetch a snapshot."


class ClientProtocolError(RealtimeError):
    """The client sent something this server does not understand.

    Answered in band and the socket stays open: one bad frame is a bug in one
    frame, and closing would turn it into a reconnect storm.
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

    Never "no such market": this service holds no market table and cannot
    tell. docs/api/realtime-service.md.
    """

    code = "invalid_market_id"
    message = "`market_id` must be a UUID."


class TooManySubscriptions(ClientProtocolError):
    """This connection is already watching as many markets as it may.

    Refused rather than evicting the oldest, which would silently freeze a
    price the client believes it is still watching.
    """

    code = "too_many_subscriptions"
    message = "This connection is watching as many markets as it may."
