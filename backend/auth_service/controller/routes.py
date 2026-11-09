"""HTTP routes for a user's own session. [A-1] #29, [A-2] #30, [A-3] #31

Parse, delegate, attach cookies. Domain errors become JSON in `errors.py`.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from controller import transport
from controller.dependencies import CurrentUser, DbSession, Limiter
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
    responses={
        409: {
            "description": (
                "`duplicate_user`: the username or email is already registered. "
                "`details` names each clashing field when it can be identified."
            )
        },
        429: {
            "description": (
                "`too_many_attempts`: too many registrations from this address. "
                "`Retry-After` says when to try again."
            )
        },
    },
)
async def register(
    payload: RegisterRequest,
    request: Request,
    session: DbSession,
    limiter: Limiter,
    response: Response,
) -> AuthResponse:
    user, tokens = await auth_service.register(
        session, payload, limiter=limiter, client_ip=transport.client_ip(request)
    )
    transport.set_auth_cookies(response, tokens)
    return AuthResponse(user=UserOut.model_validate(user), tokens=tokens)


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Log in",
    description="[A-2] #30. Accepts a username or an email in `identifier`.",
    responses={
        401: {
            "description": (
                "`invalid_credentials`: no such account, or the wrong password. "
                "Deliberately the same for both."
            )
        },
        403: {
            "description": (
                "`account_suspended`: the password was right, and the account is "
                "suspended. Only a correct password learns this."
            )
        },
        429: {
            "description": (
                "`too_many_attempts`: too many attempts from this address, on this "
                "account, or on this account from this address. The same whether "
                "or not the account exists. `Retry-After` says when to try again."
            )
        },
    },
)
async def login(
    payload: LoginRequest,
    request: Request,
    session: DbSession,
    limiter: Limiter,
    response: Response,
) -> AuthResponse:
    user = await auth_service.authenticate(
        session,
        payload.identifier,
        payload.password,
        limiter=limiter,
        client_ip=transport.client_ip(request),
    )
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
    # Unauthenticated, so it works with an expired access token, and always
    # successful, so it reveals nothing about which refresh tokens are live.
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
    responses={
        401: {
            "description": (
                "`invalid_token`: missing, malformed or expired access token, or "
                "the account no longer exists."
            )
        },
        403: {
            "description": (
                "`account_suspended`: the account was suspended after this token "
                "was issued."
            )
        },
    },
)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
