"""This service's seam onto the shared audit writer. [4.3] #15, [4.4] #16

The table and the insert are `shared/audit.py`'s (ADR 0006); this binds them to
the `source_service` this service reports, so `service` and `model` import
`core` and never `shared`. ADR 0012.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from shared import audit as _shared
from shared.audit import Actor, Entry

__all__ = ["SOURCE_SERVICE", "Actor", "Entry", "record"]

SOURCE_SERVICE = "auth_service"


async def record(session: AsyncSession, entry: Entry) -> None:
    """Append `entry` on `session` as this service, without committing it."""
    await _shared.record(session, entry, source_service=SOURCE_SERVICE)
