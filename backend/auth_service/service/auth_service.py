"""Registration, login, session refresh and logout. [A-1] #29, [A-2] #30, [A-3] #31

Works on the session it is handed and never commits halfway through a use case.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core import security
from core.config import get_settings
from core.errors import AccountSuspended, DuplicateUser, InvalidCredentials, InvalidToken
from model.entities import RefreshToken, User
from model.schemas import RegisterRequest, TokenPair


def _users_matching(*, username: str, email: str) -> Select[tuple[User]]:
    """Select users with this username or this email, ignoring case."""
    return select(User).where(
        or_(
            func.lower(User.username) == username.lower(),
            func.lower(User.email) == email.lower(),
        )
    )


async def _find_by_identifier(session: AsyncSession, identifier: str) -> User | None:
    """Look up by username or email, case-insensitively."""
    needle = identifier.strip()
    stmt = _users_matching(username=needle, email=needle)
    return (await session.execute(stmt)).scalar_one_or_none()


async def _taken_fields(session: AsyncSession, username: str, email: str) -> list[str]:
    """Return every field already registered, in form order: at most two rows."""
    stmt = _users_matching(username=username, email=email)
    existing = (await session.execute(stmt)).scalars().all()

    # Compared ignoring case, as `_users_matching` matched them.
    wanted_username = username.lower()
    wanted_email = email.lower()
    taken = []
    if any(user.username.lower() == wanted_username for user in existing):
        taken.append("username")
    if any(user.email.lower() == wanted_email for user in existing):
        taken.append("email")
    return taken


async def _find_token_owner(session: AsyncSession, raw: str) -> uuid.UUID | None:
    """Return the id of the user this raw token was issued to, or None.

    Unlocked: a token's owner never changes. Only the column is selected, so no
    token instance lands in the identity map before its row is locked.
    """
    stmt = select(RefreshToken.user_id).where(
        RefreshToken.token_hash == security.hash_refresh_token(raw)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _lock_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    """Lock and return the user's row, or None.

    The lock every path that rotates or revokes all of a user's tokens shares,
    so a replay's revoke-all cannot miss a token a rotation is inserting.
    `populate_existing` so `is_suspended` comes from the locked row.
    """
    return await session.get(
        User, user_id, with_for_update=True, populate_existing=True
    )


async def _lock_refresh_token(session: AsyncSession, raw: str) -> RefreshToken | None:
    """Lock and return the row for this raw token, or None.

    Locked because both callers write `revoked_at` from what they read; unlocked,
    two concurrent refreshes of one token would both succeed. ADR 0015.
    `populate_existing` because loading the user already put its tokens in the
    identity map, and a logout may have revoked this one since.
    """
    stmt = (
        select(RefreshToken)
        .where(RefreshToken.token_hash == security.hash_refresh_token(raw))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def issue_tokens(session: AsyncSession, user: User) -> TokenPair:
    """Mint an access token and add a fresh refresh token to the session.

    The raw refresh token is returned in the pair; only its hash is stored.
    Does not commit.
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
    """Create the account, open its first session, and commit. [A-1] #29

    Raises DuplicateUser. Grants no starting credits: this service does not
    know credits exist, and the ledger mints the grant. ADR 0009.
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
        # Assigns user.id, and surfaces a race the pre-check could not see.
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        # The winner is committed now, so the lookup can name the field. Not
        # from the constraint name, which would couple this to asyncpg.
        raise DuplicateUser(await _taken_fields(session, data.username, data.email)) from exc

    pair = await issue_tokens(session, user)
    await session.commit()
    return user, pair


async def authenticate(session: AsyncSession, identifier: str, password: str) -> User:
    """Return the user these credentials prove. [A-2] #30

    Raises InvalidCredentials for a wrong password and an unknown account
    alike, and AccountSuspended only after the password is proven. May rehash
    `user.password_hash`, leaving that write pending for the caller to commit.
    """
    user = await _find_by_identifier(session, identifier)

    if user is None:
        # Equalise timing so response latency does not confirm the account exists.
        security.dummy_verify()
        raise InvalidCredentials

    if not security.verify_password(user.password_hash, password):
        raise InvalidCredentials

    # After the password, or suspended accounts could be enumerated.
    if user.is_suspended:
        raise AccountSuspended

    if security.needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)

    return user


async def revoke_all_for_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Revoke every live refresh token the user holds. Does not commit.

    The caller must hold the user's row lock (`_lock_user`), or a rotation
    committing meanwhile keeps the token it is inserting.
    """
    stmt = select(RefreshToken).where(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
    )
    now = datetime.now(UTC)
    for record in (await session.execute(stmt)).scalars():
        record.revoked_at = now


async def rotate_refresh_token(session: AsyncSession, raw: str) -> tuple[User, TokenPair]:
    """Exchange a refresh token for a new pair, single use, and commit. [A-3] #31

    Raises InvalidToken. A revoked token is a replay of a leaked cookie, so it
    revokes every session for that user first. ADR 0002. Locks the user, then
    the token.
    """
    owner_id = await _find_token_owner(session, raw)
    if owner_id is None:
        raise InvalidToken

    # The user row before the token, on the rotation and the replay alike: a
    # replay's revoke-all cannot see a token another rotation has inserted but
    # not committed, so it must wait for that commit. ADR 0015; DECISIONS.md,
    # "The refresh path locks the user row before the token".
    user = await _lock_user(session, owner_id)
    record = await _lock_refresh_token(session, raw)
    if user is None or record is None:
        raise InvalidToken

    if not record.is_active():
        if record.revoked_at is not None:
            await revoke_all_for_user(session, user.id)
            await session.commit()
        raise InvalidToken

    if user.is_suspended:
        raise InvalidToken

    record.revoked_at = datetime.now(UTC)
    pair = await issue_tokens(session, user)
    await session.commit()
    return user, pair


async def revoke_refresh_token(session: AsyncSession, raw: str) -> None:
    """Revoke a refresh token and commit; silent if unknown or dead. [A-3] #31"""
    record = await _lock_refresh_token(session, raw)
    if record is not None and record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)
    await session.commit()
