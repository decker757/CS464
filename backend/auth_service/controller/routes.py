"""HTTP routes for the auth service.

Thin by design: parse, delegate to the service layer, attach cookies. No
business rule lives here, and no route builds an error response by hand.
Domain errors raised below the controller are turned into JSON by
`errors.register_error_handlers`.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from controller import transport
from controller.dependencies import CurrentUser, DbSession
from core.errors import InvalidToken
from model.schemas import (
    AuthResponse,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    UserOut,
)
from service import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    description="[A-1] #29. Creates the user and opens their first session.",
)
async def register(
    payload: RegisterRequest,
    session: DbSession,
    response: Response,
) -> AuthResponse:
    user, tokens = await auth_service.register(session, payload)
    transport.set_auth_cookies(response, tokens)
    return AuthResponse(user=UserOut.model_validate(user), tokens=tokens)


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Log in",
    description="[A-2] #30. Accepts a username or an email in `identifier`.",
)
async def login(
    payload: LoginRequest,
    session: DbSession,
    response: Response,
) -> AuthResponse:
    user = await auth_service.authenticate(session, payload.identifier, payload.password)
    tokens = await auth_service.issue_tokens(session, user)
    await session.commit()
    transport.set_auth_cookies(response, tokens)
    return AuthResponse(user=UserOut.model_validate(user), tokens=tokens)


@router.post(
    "/refresh",
    response_model=AuthResponse,
    summary="Exchange a refresh token for a new session",
    description="[A-3] #31. Single use: the presented token is revoked as the new one is issued.",
)
async def refresh(request: Request, session: DbSession, response: Response) -> AuthResponse:
    raw = transport.extract_refresh_token(request)
    if raw is None:
        raise InvalidToken

    user, tokens = await auth_service.rotate_refresh_token(session, raw)
    transport.set_auth_cookies(response, tokens)
    return AuthResponse(user=UserOut.model_validate(user), tokens=tokens)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Log out",
    description="[A-3] #31. Revokes the refresh token and clears both cookies.",
)
async def logout(request: Request, session: DbSession, response: Response) -> MessageResponse:
    # Deliberately unauthenticated. Logging out must still work when the access
    # token has already expired, and it always reports success so a caller
    # cannot probe which refresh tokens are live.
    raw = transport.extract_refresh_token(request)
    if raw is not None:
        await auth_service.revoke_refresh_token(session, raw)

    transport.clear_auth_cookies(response)
    return MessageResponse(message="Logged out.")


@router.get(
    "/me",
    response_model=UserOut,
    summary="Current user",
    description="[A-3] #31. Reference protected route: 401 without a valid token.",
)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
