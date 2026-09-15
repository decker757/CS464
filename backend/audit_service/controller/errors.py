"""The one place a domain error becomes an HTTP response.

Keeping this mapping in a single handler is what lets the routes stay free of
status codes and lets the service layer raise plain exceptions.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.errors import AuditError


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuditError)
    async def _handle(_: Request, exc: AuditError) -> JSONResponse:
        # The same envelope the auth and market services use, so the frontend
        # parses one shape across all three.
        error: dict[str, object] = {"code": exc.code, "message": exc.message}

        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code, content={"error": error}, headers=headers
        )
