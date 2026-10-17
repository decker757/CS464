"""Appending to the audit log. [3.4] #12

`record` inserts on the caller's session and does not commit, so the entry and
the admin action it describes commit together or not at all. That is the whole
durability story: no queue, no retry, no outbox. ADR 0006. The insert itself is
behind `core/audit.py`, one copy for every writer; this keeps the call typed by
this service's `AdminAction`.
"""

# Twins: market_service/service/audit.py and auth_service/service/audit.py.

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core import audit as core_audit
from core.audit import Actor, Entry, actor_of
from model.audit import AdminAction

__all__ = ["Actor", "actor_of", "record"]


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
    entry = Entry(
        actor=actor,
        action_type=action.value,
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        reason=reason,
        context=context,
        occurred_at=now,
    )
    await core_audit.record(session, entry)
