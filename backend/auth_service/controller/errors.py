"""The one place a domain error becomes an HTTP response.

Keeping this mapping in a single handler is what lets the routes stay free of
status codes and lets the service layer raise plain exceptions.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.errors import AuthError


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuthError)
    async def _handle(_: Request, exc: AuthError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        error: dict[str, object] = {"code": exc.code, "message": exc.message}

        # The same `details` the market service sends, so the frontend parses
        # one shape across both. Present only when the error is attributable to
        # specific inputs, and additive, so a client that ignores it still
        # reads the envelope.
        if exc.problems:
            error["details"] = [
                {"field": problem.field, "message": problem.message}
                for problem in exc.problems
            ]

        return JSONResponse(
            status_code=exc.status_code,
            content={"error": error},
            headers=headers,
        )
