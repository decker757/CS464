"""The price socket: authenticate, turn frames into hub calls, choose the close. [F-2] #42

Who receives what is `service/subscriptions.py`; what is too old to send is
`service/ordering.py`. Do not add a snapshot endpoint here: it lives on the
ledger, which owns `q`. ADR 0010.
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

# What an unexpected exception closes with (RFC 6455's "internal error"), so a
# bug is distinguishable on the client from the four ordinary endings.
_INTERNAL_ERROR = (1011, "Internal error.")


@router.websocket("/ws/prices")
async def prices(websocket: WebSocket) -> None:
    """Live market prices, for any signed-in user.

    No role check: a price is the same number for everybody.
    """
    # Refused before the upgrade, unlike the token: accepting a cross-origin
    # socket even briefly is what this check prevents. Logged, because a
    # misconfigured CORS_ORIGINS looks identical from the browser.
    if not transport.origin_allowed(websocket):
        logger.warning(
            "refused a socket from origin %r; add it to CORS_ORIGINS if it is ours",
            websocket.headers.get("origin"),
        )
        await websocket.close(code=OriginNotAllowed.close_code)
        return

    claims = _authenticate(websocket)
    if claims is None:
        # Accept, then close, so the browser can read the code: it cannot read
        # why a handshake was refused. docs/api/realtime-service.md.
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
        # In a finally and before the close: a connection left in the hub is
        # one the next broadcast writes to. Every ending passes through here.
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

    The client left, the token expired, the queue overflowed, or the socket
    broke; `asyncio.wait` on all four makes that list exhaustive. Returns how
    to close, or None when the client already closed.
    """
    tasks = {
        asyncio.create_task(_read_commands(websocket, connection, hub), name="reader"),
        asyncio.create_task(connection.pump(), name="writer"),
        asyncio.create_task(_expire(claims), name="expiry"),
        asyncio.create_task(_fail_when_dropped(connection), name="failure"),
    }

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

    # Read the outcome before cancelling, so every finished task's exception is
    # retrieved even if this coroutine is itself cancelled during the cleanup.
    ending = _ending(done)

    # Cancelled, not awaited: a cancelled task cannot touch the socket again,
    # and awaiting would only yield to the loop while the socket is torn down.
    for task in pending:
        task.cancel()

    return ending


def _ending(done: set[asyncio.Task[Any]]) -> tuple[int, str] | None:
    """Decide how to close from whichever tasks finished.

    Several can finish together and a set has no order, so every exception is
    collected first (which also retrieves it), then judged by priority in
    separate passes: a domain reason wins, an unexpected failure beats a
    disconnect, and a disconnect needs no close frame. Do not fold this into one
    loop with early returns; `test_ending.py` shows the race that brings back.
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

    Ends by raising `WebSocketDisconnect`. A `ClientProtocolError` is answered
    with an error frame and the loop carries on.
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

    The subscription ceiling is the hub's rule; its `TooManySubscriptions` is a
    `ClientProtocolError` like any other.
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

    `raw` is None for a binary frame, which gets the same answer as bad JSON.
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
    """The error naming the field that failed: `action`, else `market_id`."""
    fields = {str(error["loc"][0]) for error in exc.errors() if error.get("loc")}
    return UnknownAction() if "action" in fields else InvalidMarketId()


async def _expire(claims: TokenClaims) -> None:
    """Raise `SessionExpired` when the access token runs out.

    A socket can outlive its fifteen-minute token by hours; this is what keeps
    ADR 0002's bound true here. ADR 0010.
    """
    await asyncio.sleep(claims.seconds_until_expiry())
    raise SessionExpired


async def _fail_when_dropped(connection: Connection) -> None:
    """Raise the failure `Connection.enqueue` recorded, since it cannot raise itself."""
    raise await connection.wait_failed()


async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    """Close once, with the reason cut to `MAX_CLOSE_REASON_BYTES`."""
    if websocket.client_state is WebSocketState.DISCONNECTED:
        return

    try:
        await websocket.close(code=code, reason=_truncate(reason))
    except RuntimeError:
        # The client closed between the check above and here: a tab closing.
        logger.debug("socket already gone before close")


def _truncate(reason: str) -> str:
    encoded = reason.encode()
    if len(encoded) <= MAX_CLOSE_REASON_BYTES:
        return reason
    # errors="ignore" drops a character the cut split, rather than raising.
    return encoded[:MAX_CLOSE_REASON_BYTES].decode(errors="ignore")
