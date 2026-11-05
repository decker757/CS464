"""Every published market's trading terms, for the ledger's local copy. #640

ADR 0030, the ledger's local copy. Stored values only: the ledger derives
whether a market still trades from them, so nothing here asks the clock.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

from model.entities import Market


async def list_published_terms(session: AsyncSession) -> Sequence[Row]:
    """List `id`, `status` and `close_time` of every market ever published, by `id`."""
    # Not `browsing._visible()`, a status allowlist: a status added later
    # would fall out of the ledger's copy unnoticed. Publication is one way
    # (ADR 0008), so `published_at` names exactly what a trader ever saw.
    result = await session.execute(
        select(Market.id, Market.status, Market.close_time)
        .where(Market.published_at.is_not(None))
        .order_by(Market.id)
    )
    return result.all()
