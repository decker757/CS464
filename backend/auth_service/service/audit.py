"""Appending to the audit log. [4.3] #15, [4.4] #16

One function, and the whole design is in how it is called: `record` issues an
INSERT on the caller's session and does not commit. The transaction that
commits it is the one carrying the admin action itself, so the entry and the
action land together or neither does.

That is the entire durability story. There is no queue, no retry, no
outbox-and-relay, and no broker, because there is no second write that could
fail independently of the first. A crash before COMMIT loses the action and
the record of it; a crash after loses neither. What cannot happen — and is what
every message-based version of this has to work to prevent — is an admin action
that took effect with no trace of who did it.

A copy of `market_service/service/audit.py`, for the reason its sibling
`model/audit.py` now gives: since [F-6] #76 the build contexts no longer
prevent sharing it, and the two are kept separate deliberately rather than by
necessity. Keep them in step.

docs/adr/0006-audit-log-write-path.md records the alternatives and why this one
won here, including the condition that would end it: it holds only while every
service shares one database.
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
    """Who is performing an action, as they were at the moment they acted.

    A snapshot, and deliberately still a snapshot here. The market service
    copies these values off the access token because it has no choice — it
    cannot resolve a user id against `auth.users`. This service can, and reads
    them off the row instead, which is why a demotion binds its own routes
    immediately rather than at the end of a token lifetime.

    What does not change is what reaches the log. `actor_username` and
    `actor_role` are written as values, not as a foreign key, so the entry says
    who someone was when they acted and survives a later rename, a demotion, or
    the account being deleted entirely.

    Built in the controller. Nothing below the controller knows how the caller
    was identified.
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

    Deliberately returns nothing. There is no id to hand back, because this
    service cannot read the row it just wrote — it holds INSERT and not SELECT
    — and a caller that wanted one would be a caller trying to do something
    with the log other than append to it.

    `now` is injectable so a test can assert on a timestamp without freezing
    the system clock.
    """
    await session.execute(
        insert(admin_actions).values(
            # Generated here, not by the database. See model/audit.py: a
            # server-side default would need RETURNING to read back, and
            # RETURNING requires the SELECT grant this role must not have.
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
