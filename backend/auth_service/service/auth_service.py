"""Business rules for registration, login, session refresh and logout.

Everything here works on a session it is handed and never commits halfway
through a use case, so a caller can compose steps in one transaction. HTTP is
not mentioned in this file; failures are raised as domain errors.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core import security
from core.config import get_settings
from core.errors import AccountSuspended, DuplicateUser, InvalidCredentials, InvalidToken
from model.entities import RefreshToken, User
from model.schemas import RegisterRequest, TokenPair


async def _find_by_identifier(session: AsyncSession, identifier: str) -> User | None:
    """Look up by username or email, case-insensitively."""
    needle = identifier.strip().lower()
    stmt = select(User).where(
        or_(func.lower(User.username) == needle, func.lower(User.email) == needle)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _taken_field(session: AsyncSession, username: str, email: str) -> str | None:
    """Return which field is already registered, or None."""
    stmt = select(User).where(
        or_(
            func.lower(User.username) == username.lower(),
            func.lower(User.email) == email.lower(),
        )
    )
    existing = (await session.execute(stmt)).scalars().first()
    if existing is None:
        return None
    return "username" if existing.username.lower() == username.lower() else "email"


async def issue_tokens(session: AsyncSession, user: User) -> TokenPair:
    """Mint an access token and persist a fresh refresh token.

    The raw refresh token is returned inside the pair for the caller to put in
    a cookie or the response body. Only its hash is stored.
    """
    settings = get_settings()
    raw_refresh, refresh_hash = security.generate_refresh_token()

    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=refresh_hash,
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds),
        )
    )

    return TokenPair(
        access_token=security.create_access_token(user.id, user.username),
        refresh_token=raw_refresh,
        expires_in=settings.access_token_ttl_seconds,
    )


async def register(session: AsyncSession, data: RegisterRequest) -> tuple[User, TokenPair]:
    """[A-1] #29. Create the account and open its first session.

    Deliberately does NOT grant starting credits. That is [B-1] #32, and this
    service does not know that credits exist. The ledger mints the grant
    itself on a user's first balance read; see the README for why.
    """
    if (taken := await _taken_field(session, data.username, data.email)) is not None:
        raise DuplicateUser(taken)

    user = User(
        username=data.username,
        email=data.email,
        password_hash=security.hash_password(data.password),
    )
    session.add(user)

    try:
        # Assigns user.id and surfaces a uniqueness race we lost to a concurrent
        # registration, which the pre-check above cannot see.
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateUser("username or email") from exc

    pair = await issue_tokens(session, user)
    await session.commit()
    return user, pair


async def authenticate(session: AsyncSession, identifier: str, password: str) -> User:
    """[A-2] #30. Wrong password and unknown account are indistinguishable."""
    user = await _find_by_identifier(session, identifier)

    if user is None:
        # Equalise timing so response latency does not confirm the account exists.
        security.dummy_verify()
        raise InvalidCredentials

    if not security.verify_password(user.password_hash, password):
        raise InvalidCredentials

    # Checked only after the password is proven, otherwise an attacker could
    # enumerate suspended accounts without credentials.
    if user.is_suspended:
        raise AccountSuspended

    if security.needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)

    return user


async def _load_refresh(session: AsyncSession, raw: str) -> RefreshToken | None:
    stmt = select(RefreshToken).where(
        RefreshToken.token_hash == security.hash_refresh_token(raw)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def rotate_refresh_token(session: AsyncSession, raw: str) -> tuple[User, TokenPair]:
    """[A-3] #31. Exchange a refresh token for a new pair, single use.

    Presenting an already-revoked token means the cookie leaked and is being
    replayed, so every session for that user is killed.
    """
    record = await _load_refresh(session, raw)
    if record is None:
        raise InvalidToken

    if not record.is_active():
        if record.revoked_at is not None:
            await revoke_all_for_user(session, record.user_id)
            await session.commit()
        raise InvalidToken

    user = await session.get(User, record.user_id)
    if user is None or user.is_suspended:
        raise InvalidToken

    record.revoked_at = datetime.now(UTC)
    pair = await issue_tokens(session, user)
    await session.commit()
    return user, pair


async def revoke_refresh_token(session: AsyncSession, raw: str) -> None:
    """[A-3] #31. Logout. Silent when the token is unknown or already dead."""
    record = await _load_refresh(session, raw)
    if record is not None and record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)
    await session.commit()


async def revoke_all_for_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    stmt = select(RefreshToken).where(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
    )
    now = datetime.now(UTC)
    for record in (await session.execute(stmt)).scalars():
        record.revoked_at = now
