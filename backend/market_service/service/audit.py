"""Appending to the audit log. [4.3] #15

`record` inserts on the caller's session and does not commit, so the entry and
the admin action it describes commit together or not at all. That is the whole
durability story: no queue, no retry, no outbox. ADR 0006.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from model.audit import SOURCE_SERVICE, AdminAction, admin_actions


@dataclass(frozen=True)
class Actor:
    """Who is acting, as the verified access token described them.

    A snapshot, because this service cannot resolve a user id (ADR 0003), and
    the log should say who someone was when they acted. Built in the
    controller; nothing below it knows a JWT was involved.
    """

    id: uuid.UUID
    username: str
    role: str


async def record(
    session: AsyncSession,
    *,
    actor: Actor,
    action: AdminAction,
    target_type: str,
    target_id: uuid.UUID | None = None,
    target_label: str | None = None,
    reason: str | None = None,
    context: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> None:
    """Append one entry on `session`, without committing it.

    Returns nothing: this role holds INSERT and not SELECT, so it cannot read
    back the row it wrote.
    """
    await session.execute(
        insert(admin_actions).values(
            # Generated here: a server default would need RETURNING, which
            # needs the SELECT grant this role must not have. model/audit.py.
            id=uuid.uuid4(),
            occurred_at=now or datetime.now(UTC),
            actor_id=actor.id,
            actor_username=actor.username,
            actor_role=actor.role,
            action_type=action.value,
            target_type=target_type,
            target_id=target_id,
            target_label=target_label,
            reason=reason,
            context=context,
            source_service=SOURCE_SERVICE,
        )
    )
