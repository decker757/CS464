"""The one place a domain error becomes an HTTP response."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.errors import AuthError


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuthError)
    async def _handle(_: Request, exc: AuthError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        error: dict[str, object] = {"code": exc.code, "message": exc.message}

        # The same `details` as the market service's, present only when the
        # error names specific inputs.
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
