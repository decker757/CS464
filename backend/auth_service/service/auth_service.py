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


async def _taken_fields(session: AsyncSession, username: str, email: str) -> list[str]:
    """Return every field already registered, in form order.

    Both are reported when both clash, so the form can mark them together
    rather than sending the user round the loop twice. Uniqueness bounds this
    to at most two rows.
    """
    wanted_username = username.lower()
    wanted_email = email.lower()

    stmt = select(User).where(
        or_(
            func.lower(User.username) == wanted_username,
            func.lower(User.email) == wanted_email,
        )
    )
    existing = (await session.execute(stmt)).scalars().all()

    taken = []
    if any(user.username.lower() == wanted_username for user in existing):
        taken.append("username")
    if any(user.email.lower() == wanted_email for user in existing):
        taken.append("email")
    return taken


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
        access_token=security.create_access_token(user.id, user.username, user.role),
        refresh_token=raw_refresh,
        expires_in=settings.access_token_ttl_seconds,
    )


async def register(session: AsyncSession, data: RegisterRequest) -> tuple[User, TokenPair]:
    """[A-1] #29. Create the account and open its first session.

    Deliberately does NOT grant starting credits. That is [B-1] #32, and this
    service does not know that credits exist. The ledger mints the grant
    itself on a user's first balance read; see the README for why.
    """
    if taken := await _taken_fields(session, data.username, data.email):
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
        # The winner of the race is committed and visible now, so the lookup
        # that came back clear a moment ago can name the field this time. The
        # error carries the constraint name too, but reading it would couple
        # this to asyncpg and to the index names in model/entities.py.
        # Still possibly empty, if the winning account was deleted in between.
        raise DuplicateUser(await _taken_fields(session, data.username, data.email)) from exc

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
    """The row for this token, locked, because both callers decide from it.

    Locked, because `rotate_refresh_token` reads `revoked_at`, decides the
    token is still live, and only then revokes it. Read unlocked, two requests
    presenting the same token — the legitimate client and whoever copied its
    cookie — both see it live and both leave with a fresh session, and the
    replay that was supposed to cut every session for that user is never
    noticed, because each request's write lands after the other's check. Under
    READ COMMITTED the second blocks here and re-reads the row the first
    committed, so it sees the revocation and is treated as the replay it is.

    A token that does not exist locks nothing.
    """
    stmt = (
        select(RefreshToken)
        .where(RefreshToken.token_hash == security.hash_refresh_token(raw))
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def rotate_refresh_token(session: AsyncSession, raw: str) -> tuple[User, TokenPair]:
    """[A-3] #31. Exchange a refresh token for a new pair, single use.

    Presenting an already-revoked token means the cookie leaked and is being
    replayed, so every session for that user is killed. Single use holds under
    concurrency too: `_load_refresh` locks the row, so two requests presenting
    one token queue, and the second finds it revoked by the first.
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
