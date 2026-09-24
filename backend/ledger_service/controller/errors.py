"""The one place a domain error becomes an HTTP response.

Keeping this mapping in a single handler is what lets the routes stay free of
status codes and lets the service layer raise plain exceptions.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.errors import InsufficientFunds, LedgerError

_log = logging.getLogger(__name__)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(LedgerError)
    async def _handle(_: Request, exc: LedgerError) -> JSONResponse:
        # The same envelope the other three services use, so the frontend
        # parses one shape across the whole backend.
        error: dict[str, object] = {"code": exc.code, "message": exc.message}

        # The only addition, and it exists for a caller that does not have one
        # yet: a refused trade has to tell somebody how short they were, and
        # re-deriving that from the prose of `message` is not an API. Additive,
        # so a client that ignores it still reads the envelope.
        if isinstance(exc, InsufficientFunds):
            error["details"] = {
                "balance": str(exc.balance),
                "required": str(exc.required),
            }

        # A 5xx in this envelope is the ledger saying its own data is wrong,
        # and before this it was the only 500 in the service that reached the
        # client with nothing in the logs — an access-log line and no cause.
        # The unmapped exceptions it replaced at least got a traceback out of
        # Starlette on the way past. Anything below 500 is the caller's to
        # fix and is already described by the response.
        if exc.status_code >= 500:
            _log.error(
                "%s: %s", exc.code, exc.message, exc_info=exc, stack_info=False
            )

        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code, content={"error": error}, headers=headers
        )
