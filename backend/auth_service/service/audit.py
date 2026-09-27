"""Appending to the audit log. [4.3] #15, [4.4] #16

`record` INSERTs on the caller's session and does not commit, so the entry and
the admin action commit together or not at all. That is the whole durability
story. ADR 0006. A copy of `market_service/service/audit.py`; keep them in step.
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
    """Who is acting, as they were at that moment. Built in the controller.

    Written to the log as values, not a foreign key, so an entry survives a
    later rename, demotion or deletion.
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
    the row back. `now` is injectable for tests.
    """
    await session.execute(
        insert(admin_actions).values(
            # Generated here, not by the database: reading back a server default
            # needs RETURNING, which needs the SELECT this role must not have.
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
