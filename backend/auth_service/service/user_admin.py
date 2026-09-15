"""Administering other people's accounts. [4.4] #16

Separate from `auth_service.py`, which is about a user acting on their own
session — registering, logging in, refreshing, logging out. Everything here is
one user acting on another, which is the whole reason these operations are
audited and those are not.

HTTP is not mentioned in this file; failures are raised as domain errors and
`controller/errors.py` maps them.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import CannotChangeOwnRole, LastAdministrator, UserNotFound
from core.roles import UserRole
from model.audit import AdminAction
from model.entities import User
from service import audit
from service.audit import Actor


async def _administrators_for_update(session: AsyncSession) -> set[uuid.UUID]:
    """Every administrator who could actually act, rows locked for this transaction.

    The lock is the point; the ids are a by-product. Postgres re-evaluates a
    `FOR UPDATE` row against the current committed state after waiting for it,
    so a concurrent demotion that commits while this query is blocked drops
    that user out of the result rather than being missed. That is what turns
    two administrators demoting each other from a race into a queue: whichever
    arrives second sees the first's work and finds itself looking at the last
    administrator.

    **`is_suspended` is part of the predicate, and leaving it out is a bug this
    once had.** A suspended administrator is an administrator on paper and
    nobody at all in practice: `authenticate` and `get_current_user` both
    refuse the account before the role is ever consulted, so they cannot call
    the route that would undo any of this. Counting them makes the set look
    survivable when it is already empty, and the check passes while handing
    back a database only a manual UPDATE can rescue. What matters is who can
    still act, not who the column says is privileged.

    Taken only on the demotion path. A promotion cannot empty the set, and
    there is no reason to serialise those against each other.

    [4.2] #14 has to take this same lock when it suspends, for the same
    reason in the other direction: suspending the last unsuspended
    administrator empties the set without touching `role` at all, so nothing
    here can see it coming.
    """
    rows = await session.execute(
        select(User.id)
        .where(User.role == UserRole.ADMIN, User.is_suspended.is_(False))
        .with_for_update()
    )
    return set(rows.scalars())


async def change_role(
    session: AsyncSession,
    *,
    actor: Actor,
    target_id: uuid.UUID,
    role: UserRole,
    reason: str | None = None,
) -> User:
    """Move `target_id` to `role`, recording the decision in the same transaction.

    Returns the user as they now are. Idempotent: asking for the role someone
    already holds succeeds and writes nothing, because a request that changed
    nothing is not a decision and an audit log that collects them is a log
    nobody finishes reading. ADR 0006 makes the same argument about draft
    autosave.

    The UPDATE and the audit INSERT go to the database in one transaction and
    commit together, so there is no ordering in which this account gains
    administrative authority with nothing recording who granted it.

    Refuses to remove the last administrator. Two rules do that together, and
    neither is sufficient alone: an administrator may not change their own
    role, which bounds the sequential case, and a demotion locks the
    administrator rows, which bounds the concurrent one.
    """
    # Before the lookup, not after. This is the invariant that stops the
    # administrator set from emptying — see CannotChangeOwnRole — and it should
    # not depend on the target row being readable for it to hold.
    if target_id == actor.id:
        raise CannotChangeOwnRole

    target = await session.get(User, target_id)
    if target is None:
        raise UserNotFound

    if target.role is UserRole.ADMIN and role is not UserRole.ADMIN:
        # A demotion. Lock first, then re-read: the lock is what makes the
        # re-read worth anything, because it is what stops the answer changing
        # between the question and the decision.
        remaining = await _administrators_for_update(session)
        await session.refresh(target)

        # Discard rather than check membership. The target may legitimately be
        # absent from the locked set — a suspended administrator is excluded
        # from it by design — and that is not the same fact as their having
        # been demoted by somebody else a moment ago. Conflating the two makes
        # demoting a suspended administrator silently do nothing, which is the
        # one account most likely to need it.
        remaining.discard(target.id)

        if target.role is UserRole.ADMIN and not remaining:
            raise LastAdministrator

    # Read after the guard, so the idempotent case below sees the refreshed
    # value and a demotion that somebody else already applied is a no-op here
    # rather than a second entry claiming a change that did not happen.
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
        # Snapshotted like the actor's, and for the same reason: `audit_svc`
        # holds no grant on auth.users, so an entry that named only an id would
        # be unreadable to the one role allowed to read it.
        target_label=target.username,
        reason=reason,
        context={"from": previous.value, "to": role.value},
    )

    await session.commit()
    return target
