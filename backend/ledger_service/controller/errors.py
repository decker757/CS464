"""The one place a domain error becomes an HTTP response.

Keeping this mapping in a single handler is what lets the routes stay free of
status codes and lets the service layer raise plain exceptions.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.errors import InsufficientFunds, LedgerError


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

        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code, content={"error": error}, headers=headers
        )
