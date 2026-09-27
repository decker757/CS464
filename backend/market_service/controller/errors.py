"""The one place a domain error becomes an HTTP response.

Keeping this mapping in a single handler is what lets the routes stay free of
status codes and lets the service layer raise plain exceptions.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.errors import IncompleteError, MarketError


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(MarketError)
    async def _handle(_: Request, exc: MarketError) -> JSONResponse:
        # The same envelope the auth service uses.
        error: dict[str, object] = {"code": exc.code, "message": exc.message}

        # A refusal by form field lists every offending field. Keyed on the
        # base class, so a new `IncompleteError` needs no change here.
        if isinstance(exc, IncompleteError):
            error["details"] = [
                {"field": problem.field, "message": problem.message}
                for problem in exc.problems
            ]

        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code, content={"error": error}, headers=headers
        )
