"""Administering other people's accounts. [4.1] #13, [4.4] #16, [4.2] #14

One user acting on another, which is why changes here are audited and those in
`auth_service.py` are not.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import ColumnElement, Select, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, raiseload

from core.errors import (
    CannotChangeOwnRole,
    CannotSuspendSelf,
    FieldProblem,
    LastAdministrator,
    SuspensionIncomplete,
    UserNotFound,
)
from core.paging import decode_cursor, encode_cursor
from core.roles import UserRole
from model.audit import AdminAction
from model.entities import User
from service import audit, auth_service
from service.audit import Actor

# LIKE's pattern characters, escaped so a query matches as typed: `_` is legal
# in usernames, so `ernest_t` must not match `ernestXt`. The backslash goes
# first, or it would escape the escapes.
_LIKE_WILDCARDS = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})

# [4.2] #14. The floor market_service's close and reject reasons use.
_MIN_SUSPENSION_REASON_LENGTH = 10


@dataclass(frozen=True)
class UserPage:
    """One page of the user list, newest registration first."""

    users: list[User]
    next_cursor: str | None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


def _contains(column: InstrumentedAttribute[str], needle: str) -> ColumnElement[bool]:
    """Match `needle` anywhere in `column`, ignoring case, as literal text.

    A substring, because an admin investigating usually has only a fragment.
    """
    return column.ilike(f"%{needle.translate(_LIKE_WILDCARDS)}%", escape="\\")


def _ordered_query(
    query: str | None, *, role: UserRole | None, is_suspended: bool | None
) -> Select[tuple[User]]:
    """Build the search query: username or email, narrowed to a role and a
    suspension state when given, newest first, ties by id.

    `raiseload` on the sessions: `lazy="selectin"` would otherwise fetch every
    refresh token of every listed account. Not `noload`, which would pass off
    an empty collection as the truth.
    """
    stmt = select(User).options(raiseload(User.refresh_tokens))

    needle = (query or "").strip()
    if needle:
        stmt = stmt.where(
            or_(_contains(User.username, needle), _contains(User.email, needle))
        )

    if role is not None:
        stmt = stmt.where(User.role == role)

    if is_suspended is not None:
        stmt = stmt.where(User.is_suspended.is_(is_suspended))

    return stmt.order_by(User.created_at.desc(), User.id.desc())


async def _lock_administrators(session: AsyncSession) -> set[uuid.UUID]:
    """Lock and return the ids of every administrator who could act.

    The lock turns two admins demoting each other into a queue. ADR 0007.
    Keep `is_suspended` in the predicate: a suspended admin cannot log in, so
    counting one hides an empty set.

    `set_suspension` takes it too: suspending the last unsuspended admin
    empties the set without touching `role`.
    """
    rows = await session.execute(
        select(User.id)
        .where(User.role == UserRole.ADMIN, User.is_suspended.is_(False))
        .with_for_update()
    )
    return set(rows.scalars())


def _reason_problems(raw: str) -> list[FieldProblem]:
    """The reason rule: trimmed, not blank, at least ten characters.

    A copy of market_service's `service/validation.py::_reason_problems`;
    different services and forms, so the two may move apart. ADR 0012.
    """
    reason = raw.strip()

    if not reason:
        return [
            FieldProblem(
                field="reason",
                message=(
                    "A reason is required. It is the only record of why this "
                    "account was suspended or unsuspended."
                ),
            )
        ]

    if len(reason) < _MIN_SUSPENSION_REASON_LENGTH:
        return [
            FieldProblem(
                field="reason",
                message=(
                    f"The reason must be at least {_MIN_SUSPENSION_REASON_LENGTH} "
                    "characters, so that somebody reading the log later can tell "
                    "what happened."
                ),
            )
        ]

    return []


async def search_users(
    session: AsyncSession,
    *,
    query: str | None = None,
    role: UserRole | None = None,
    is_suspended: bool | None = None,
    limit: int,
    cursor: str | None = None,
) -> UserPage:
    """Return one page of accounts, newest first, narrowed to `query`, `role`
    and `is_suspended`. [4.1] #13, #479

    Raises MalformedCursor. A blank `query` lists everybody, and a None filter
    filters nothing. Not audited: it decides nothing (ADR 0006). Fetches
    `limit + 1` rows so `has_more` is exact without a COUNT. The substring
    match is a sequential scan, accepted at this scale; the upgrade is a
    pg_trgm GIN index.
    """
    stmt = _ordered_query(query, role=role, is_suspended=is_suspended)

    if cursor is not None:
        # Decoded before the query, so a bad value is a 400 rather than a
        # driver error and a 500.
        created_at, row_id = decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(User.created_at, User.id) < tuple_(created_at, row_id)
        )

    rows = list((await session.execute(stmt.limit(limit + 1))).scalars())

    if len(rows) <= limit:
        return UserPage(users=rows, next_cursor=None)

    page = rows[:limit]
    last = page[-1]
    return UserPage(users=page, next_cursor=encode_cursor(last.created_at, last.id))


async def change_role(
    session: AsyncSession,
    *,
    actor: Actor,
    target_id: uuid.UUID,
    role: UserRole,
    reason: str | None = None,
) -> User:
    """Move `target_id` to `role`, audit it, commit, and return the user.

    Raises CannotChangeOwnRole, UserNotFound or LastAdministrator (ADR 0007).
    Locks the administrator set, then the target (ADR 0015). Asking for the
    role already held writes nothing and logs nothing.
    """
    # Before the lookup, so the invariant never depends on the target existing.
    if target_id == actor.id:
        raise CannotChangeOwnRole

    # The set first and the target second, on every path, or two of these
    # deadlock. ADR 0015. `lock_user` reads the role from the locked row,
    # never from an instance this session loaded before the lock.
    remaining = await _lock_administrators(session)
    target = await auth_service.lock_user(session, target_id)
    if target is None:
        raise UserNotFound

    if target.role is UserRole.ADMIN and role is not UserRole.ADMIN:
        # A demotion. Discard, do not test membership: a suspended admin is
        # absent from the set yet must stay demotable. ADR 0007.
        remaining.discard(target.id)

        if not remaining:
            raise LastAdministrator

    # From the locked row, so a change somebody else already applied is a
    # no-op here, not a second entry.
    previous = target.role
    if previous is role:
        return target

    target.role = role

    await audit.record(
        session,
        actor=actor,
        action=AdminAction.USER_ROLE_CHANGED,
        target_type="user",
        target_id=target.id,
        # Snapshotted: `audit_svc` cannot read auth.users to resolve an id.
        target_label=target.username,
        reason=reason,
        context={"from": previous.value, "to": role.value},
    )

    await session.commit()
    return target


async def set_suspension(
    session: AsyncSession,
    *,
    actor: Actor,
    target_id: uuid.UUID,
    suspended: bool,
    reason: str,
) -> User:
    """Suspend or unsuspend `target_id`, audit it, commit, and return the user.

    Raises CannotSuspendSelf, SuspensionIncomplete, UserNotFound or
    LastAdministrator (ADR 0007). Locks the administrator set, then the target
    (ADR 0015). Suspending revokes every refresh token the target holds. Asking
    for the state already held writes nothing and logs nothing.
    """
    # Identity, then the reason, both before any lookup. ADR 0016's order.
    if target_id == actor.id:
        raise CannotSuspendSelf

    problems = _reason_problems(reason)
    if problems:
        raise SuspensionIncomplete(problems)

    # The set first on every path, unsuspend too, or this and a role change
    # deadlock. ADR 0015.
    remaining = await _lock_administrators(session)
    target = await auth_service.lock_user(session, target_id)
    if target is None:
        raise UserNotFound

    if suspended and target.role is UserRole.ADMIN:
        # Discard, do not test membership: a suspended admin is absent from
        # the set yet is still a valid target. ADR 0007.
        remaining.discard(target.id)

        if not remaining:
            raise LastAdministrator

    # From the locked row, so two admins suspending one user write one entry.
    if target.is_suspended == suspended:
        return target

    if suspended:
        # Needs the row lock taken above. DECISIONS.md, "Suspending revokes
        # every refresh token the account holds".
        await auth_service.revoke_all_for_user(session, target.id)
    target.is_suspended = suspended

    await audit.record(
        session,
        actor=actor,
        action=AdminAction.USER_SUSPENDED if suspended else AdminAction.USER_UNSUSPENDED,
        target_type="user",
        target_id=target.id,
        # Snapshotted: `audit_svc` cannot read auth.users to resolve an id.
        target_label=target.username,
        reason=reason.strip(),
        # The role at the time, which a later role change cannot rewrite.
        context={"role": target.role.value},
    )

    await session.commit()
    return target
