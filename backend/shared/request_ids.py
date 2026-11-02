"""Request IDs, and the one access-log line per request. #599

Every request carries an ID: taken from `X-Request-ID` when the caller sent a
safe one, made up otherwise, echoed in the response, attached to every log
line (through `current_request_id`) and passed on to the next service
(through `send_request_id`).

An inbound value is never trusted as it is. It goes into log lines, so a value
with a newline or 10,000 characters would be log injection; only a short value
of a safe character set is kept, and anything else is replaced, not cleaned.

A pure ASGI middleware, not `BaseHTTPMiddleware`: that one runs the app in a
separate task, so the ContextVar set here would not reach the route, and it
would also break the websocket handshake.

Known and accepted: an exception no handler caught is answered by Starlette's
ServerErrorMiddleware, which sits outside every `add_middleware` layer. This
middleware still logs it once, with the ID, as a 500, but that one response
carries no `X-Request-ID`.
"""

import logging
import re
import time
import uuid
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

if TYPE_CHECKING:
    import httpx

REQUEST_ID_HEADER = "X-Request-ID"
QUIET_PATHS = frozenset({"/health", "/ready", "/metrics"})
UNMATCHED_ROUTE = "unmatched"
ACCESS_LOGGER = "cs464.access"

_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_access_log = logging.getLogger(ACCESS_LOGGER)


def current_request_id() -> str | None:
    return _request_id.get()


def new_request_id() -> str:
    return uuid.uuid4().hex


def accepted_request_id(incoming: str | None) -> str:
    if incoming is not None and _SAFE_REQUEST_ID.fullmatch(incoming):
        return incoming
    return new_request_id()


@contextmanager
def background_run() -> Iterator[str]:
    """A fresh ID for work no request started, so its log lines still group."""
    request_id = new_request_id()
    token = _request_id.set(request_id)
    try:
        yield request_id
    finally:
        _request_id.reset(token)


def route_template_of(scope: Scope) -> str:
    route = scope.get("route")
    if route is None:
        return UNMATCHED_ROUTE
    return route.path


def _incoming_request_id(scope: Scope) -> str | None:
    wanted = REQUEST_ID_HEADER.lower().encode("latin-1")
    for name, value in scope["headers"]:
        if name == wanted:
            return value.decode("latin-1")
    return None


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        request_id = accepted_request_id(_incoming_request_id(scope))
        token = _request_id.set(request_id)
        try:
            if scope["type"] == "websocket":
                await self.app(scope, receive, send)
            else:
                await self._serve_http(scope, receive, send, request_id)
        finally:
            _request_id.reset(token)

    async def _serve_http(
        self, scope: Scope, receive: Receive, send: Send, request_id: str
    ) -> None:
        started = time.perf_counter()
        outcome: MutableMapping[str, Any] = {"status": 500}

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                outcome["status"] = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            if scope["path"] not in QUIET_PATHS:
                _access_log.info(
                    "request",
                    extra={
                        "method": scope["method"],
                        "route": route_template_of(scope),
                        "status": outcome["status"],
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )


async def send_request_id(request: "httpx.Request") -> None:
    """An httpx request event hook: pass the current ID to the next service."""
    request_id = current_request_id()
    if request_id is not None:
        request.headers[REQUEST_ID_HEADER] = request_id
