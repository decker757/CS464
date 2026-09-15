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
    """Every administrator, with their rows locked until this transaction ends.

    The lock is the point; the ids are a by-product. Postgres re-evaluates a
    `FOR UPDATE` row against the current committed state after waiting for it,
    so a concurrent demotion that commits while this query is blocked drops
    that user out of the result rather than being missed. That is what turns
    two administrators demoting each other from a race into a queue: whichever
    arrives second sees the first's work and finds itself looking at the last
    administrator.

    Taken only on the demotion path. A promotion cannot empty the set, and
    there is no reason to serialise those against each other.
    """
    rows = await session.execute(
        select(User.id).where(User.role == UserRole.ADMIN).with_for_update()
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

    previous = target.role
    if previous is role:
        return target

    if previous is UserRole.ADMIN and role is not UserRole.ADMIN:
        administrators = await _administrators_for_update(session)

        if target.id not in administrators:
            # Demoted by a concurrent request while this one was deciding. The
            # outcome being asked for is already the outcome, so this is the
            # idempotent case above arriving a moment later: no write, no entry.
            await session.refresh(target)
            return target

        if administrators == {target.id}:
            raise LastAdministrator

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
