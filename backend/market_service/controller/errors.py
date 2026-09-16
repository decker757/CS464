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
        # The same envelope the auth service uses, so the frontend parses one
        # shape across both services.
        error: dict[str, object] = {"code": exc.code, "message": exc.message}

        # The only addition: a refusal addressed to form fields lists every
        # offending one, so the form can mark them all at once. Additive, so a
        # client that ignores it still reads the envelope.
        #
        # Keyed on the base class rather than on each error, so [3.1] #9's
        # refused proposal gets the same shape as [1.1] #1's refused submission
        # without this file learning a second name.
        if isinstance(exc, IncompleteError):
            error["details"] = [
                {"field": problem.field, "message": problem.message}
                for problem in exc.problems
            ]

        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code, content={"error": error}, headers=headers
        )
