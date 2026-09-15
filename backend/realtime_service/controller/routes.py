"""The socket. [F-2] #42

Thin in the same sense the other services' routes are thin: it authenticates,
translates frames into hub calls, and chooses how the connection ends. The rule
about who receives what is in `service/subscriptions.py`, and the rule about
what is too old to send is in `service/ordering.py`.

**There is no snapshot endpoint here, and there will not be one.** The ticket
asks for one and it belongs to whoever owns `q` — which ADR 0005 puts with the
ledger, and which does not exist yet. Serving it from this service would mean
answering from the last event this replica happened to see, and a client that
reconnected to a process started thirty seconds ago would receive an empty
answer in the same shape as a true one. A cache that cannot tell you it is
empty is worse than no cache. `docs/api/realtime-service.md` specifies the
endpoint; ADR 0010 records why it is specified here and implemented elsewhere.

**A connection ends for exactly four reasons**, and every one of them is a task
below finishing first: the client went away, its token expired, it fell too far
behind, or the socket broke. `asyncio.wait` on the four is what makes that list
exhaustive rather than aspirational — there is no path where one of them
finishes and the others keep running.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from starlette.websockets import WebSocketState

from controller import transport
from controller.connection import Connection
from core import security
from core.config import get_settings
from core.errors import (
    MAX_CLOSE_REASON_BYTES,
    ClientProtocolError,
    InvalidMarketId,
    MalformedCommand,
    NotAuthenticated,
    OriginNotAllowed,
    RealtimeError,
    SessionExpired,
    UnknownAction,
)
from core.security import TokenClaims
from model.schemas import (
    ClientCommand,
    error_frame,
    subscribed_frame,
    unsubscribed_frame,
)
from service.subscriptions import Hub, get_hub

logger = logging.getLogger(__name__)

router = APIRouter()

# Nothing below raises this deliberately. It is what an unexpected exception
# closes with, so that a bug here is distinguishable on the client from any of
# the four ordinary endings — 1011 is RFC 6455's "the server hit a condition
# that prevented it from fulfilling the request", which is exactly true.
_INTERNAL_ERROR = (1011, "Internal error.")


@router.websocket("/ws/prices")
async def prices(websocket: WebSocket) -> None:
    """Live market prices, for any signed-in user.

    No role check. A price is public to everybody who can see the market, and
    an admin's view of it is the same number — `core/roles.py` records why the
    claim is carried anyway.
    """
    # Refused before the upgrade, unlike the token check below: accepting a
    # cross-origin socket even briefly is the thing this check exists to
    # prevent, and the page being refused is not one we owe a readable error
    # to. The operator does get one, here, because a misconfigured
    # CORS_ORIGINS looks identical from the browser.
    if not transport.origin_allowed(websocket):
        logger.warning(
            "refused a socket from origin %r; add it to CORS_ORIGINS if it is ours",
            websocket.headers.get("origin"),
        )
        await websocket.close(code=OriginNotAllowed.close_code)
        return

    claims = _authenticate(websocket)
    if claims is None:
        # Accepted and then closed, so the client can read a code and a reason.
        # A handshake refused before the upgrade reaches JavaScript as an
        # opaque failure — the WebSocket API exposes no HTTP status — and
        # [X-4] #37 has to tell an expired session apart from a dead network.
        await websocket.accept()
        await _close(websocket, NotAuthenticated.close_code, NotAuthenticated.message)
        return

    await websocket.accept()

    settings = get_settings()
    hub = get_hub()
    connection = Connection(websocket, queue_size=settings.send_queue_size)

    try:
        ending = await _serve(websocket, connection, claims, hub)
    finally:
        # Before the close, and in a finally, because a connection left in the
        # hub is a connection the next broadcast tries to write to. This is the
        # only place it happens, and every ending passes through it.
        hub.forget(connection)

    if ending is not None:
        await _close(websocket, *ending)


def _authenticate(websocket: WebSocket) -> TokenClaims | None:
    token = transport.extract_access_token(websocket)
    if token is None:
        return None
    return security.decode_access_token(token)


async def _serve(
    websocket: WebSocket, connection: Connection, claims: TokenClaims, hub: Hub
) -> tuple[int, str] | None:
    """Run the connection until one of its four tasks finishes.

    Returns how to close, or None when the client already closed and there is
    nothing left to say to it.
    """
    tasks = {
        asyncio.create_task(_read_commands(websocket, connection, hub), name="reader"),
        asyncio.create_task(connection.pump(), name="writer"),
        asyncio.create_task(_expire(claims), name="expiry"),
        asyncio.create_task(_fail_when_dropped(connection), name="failure"),
    }

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

    # Read the outcome BEFORE unwinding. Every finished task's exception has to
    # be retrieved or asyncio logs "Task exception was never retrieved" from a
    # garbage collection somewhere unrelated, hours later — and doing it first
    # means that stays true even if this coroutine is itself cancelled during
    # the cleanup below.
    ending = _ending(done)

    # Cancelled and not awaited, which is deliberate. There is nothing to wait
    # for: a cancelled coroutine makes no further progress of its own, it only
    # resumes into its own CancelledError, so none of these can touch the socket
    # again once `cancel` has been called. Awaiting them would hand control back
    # to the event loop for no result, at the one moment a connection is most
    # likely to be torn down underneath us.
    for task in pending:
        task.cancel()

    return ending


def _ending(done: set[asyncio.Task[Any]]) -> tuple[int, str] | None:
    """Decide how to close from whichever tasks finished.

    More than one can land in `done`, so every exception is collected before any
    of them is acted on, and the priority is then applied in explicit passes.
    Both halves of that matter.

    Collecting first is what retrieves every finished task's exception. An early
    return would leave a sibling's unretrieved, and asyncio logs that from a
    garbage collection somewhere unrelated, hours later.

    Passing in priority order is what makes the result independent of iteration
    order, and a single loop with early returns does not achieve it — a set has
    no order, so which of two exceptions it yields first is down to object
    hashing. That is not hypothetical here: a client vanishing finishes the
    reader with `WebSocketDisconnect` while the pump's concurrent `send_json`
    raises a plain `RuntimeError` from Starlette's own "cannot send once a close
    message has been sent" guard. Judged in one pass, the same real event closed
    as an ordinary disconnect or as `1011` depending on which task the set
    happened to yield first.

    So: a domain reason wins outright, an unexpected failure beats a disconnect,
    and a disconnect means there is nobody left to send a close frame to.
    """
    failures = [
        exc
        for task in done
        if not task.cancelled()
        for exc in (task.exception(),)
        if exc is not None
    ]

    for exc in failures:
        if isinstance(exc, RealtimeError):
            return exc.close_code, exc.message

    for exc in failures:
        if not isinstance(exc, WebSocketDisconnect):
            logger.error("unexpected failure on a price socket", exc_info=exc)
            return _INTERNAL_ERROR

    return None if failures else (1000, "")


async def _read_commands(websocket: WebSocket, connection: Connection, hub: Hub) -> None:
    """Consume client commands until the client goes away.

    Ends by raising `WebSocketDisconnect`, which `_ending` reads as the ordinary
    close. A `ClientProtocolError` does not end anything: the client is told
    what it sent wrong and the connection carries on, because one mistyped frame
    is a bug in one frame and dropping the socket would turn it into a reconnect
    loop.
    """
    while True:
        message = await websocket.receive()

        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1000))

        try:
            _apply(message.get("text"), connection, hub)
        except ClientProtocolError as exc:
            connection.enqueue(error_frame(exc.code, exc.message))


def _apply(raw: str | None, connection: Connection, hub: Hub) -> None:
    """Turn one client frame into a hub call and an acknowledgement.

    No rule of its own. The per-connection ceiling is the hub's, and it raises
    `TooManySubscriptions` — a `ClientProtocolError` like any other, which
    `_read_commands` turns into an error frame without knowing what it was
    about.
    """
    command = _parse(raw)

    if command.action == "subscribe":
        hub.subscribe(connection, command.market_id)
        connection.enqueue(subscribed_frame(command.market_id))
        return

    hub.unsubscribe(connection, command.market_id)
    connection.enqueue(unsubscribed_frame(command.market_id))


def _parse(raw: str | None) -> ClientCommand:
    """Validate a frame onto `ClientCommand`, or say which half was wrong.

    `raw` is None for a binary frame. The protocol is JSON text and a client
    sending bytes has the same problem as one sending "hello", so it gets the
    same answer rather than an internal error.
    """
    if raw is None:
        raise MalformedCommand

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise MalformedCommand from None

    if not isinstance(payload, dict):
        raise MalformedCommand

    try:
        return ClientCommand.model_validate(payload)
    except ValidationError as exc:
        raise _which_field(exc) from None


def _which_field(exc: ValidationError) -> ClientProtocolError:
    """Pick the more useful of two errors from a validation failure.

    "Your command was invalid" is true and useless. There are exactly two
    fields, and telling somebody which one they got wrong is the difference
    between a fix and a bisect.
    """
    fields = {str(error["loc"][0]) for error in exc.errors() if error.get("loc")}
    return UnknownAction() if "action" in fields else InvalidMarketId()


async def _expire(claims: TokenClaims) -> None:
    """Close the connection when its access token runs out.

    The only place in this repository where a token's expiry is enforced by
    anything other than the next request failing. Every HTTP service checks
    `exp` once and is finished in milliseconds; a socket authorised on a
    fifteen-minute token can still be open hours later, and ADR 0002's
    "the 15-minute TTL is what bounds that window" is only true of this service
    if something acts on the bound.
    """
    await asyncio.sleep(claims.seconds_until_expiry())
    raise SessionExpired


async def _fail_when_dropped(connection: Connection) -> None:
    """Turn a failure recorded by the fan-out into an ending.

    `Connection.enqueue` cannot raise — it runs inside the broadcast loop — so a
    connection that overflows records the reason and sets an event. This is the
    task waiting on it.
    """
    raise await connection.wait_failed()


async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    """Close once, with a reason short enough to be legal.

    RFC 6455 caps a close reason at 123 bytes and treats a longer one as a
    protocol error, so the client would lose the reason entirely at exactly the
    moment it most needs it.
    """
    if websocket.client_state is WebSocketState.DISCONNECTED:
        return

    try:
        await websocket.close(code=code, reason=_truncate(reason))
    except RuntimeError:
        # The client closed between the check above and here. There is nobody
        # left to tell, and this is the ordinary shape of a browser tab closing.
        logger.debug("socket already gone before close")


def _truncate(reason: str) -> str:
    encoded = reason.encode()
    if len(encoded) <= MAX_CLOSE_REASON_BYTES:
        return reason
    # errors="ignore" drops a multi-byte character the cut landed inside,
    # rather than raising on a reason that was only ever advisory.
    return encoded[:MAX_CLOSE_REASON_BYTES].decode(errors="ignore")
