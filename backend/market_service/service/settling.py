"""Whether a market may be settled, bound to this service's statuses and settings.
[3.4] #12, ADR 0019.

The rule itself is `core/settling.py`; this maps a `MarketStatus` onto it and
reads the dispute window from the settings, so every caller asks one function.
Same shape as `service/closing.py` over `core/closing.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from core.config import get_settings
from core.settling import is_settleable
from model.entities import MarketStatus


def settleable(
    status: MarketStatus, approved_at: datetime | None, *, now: datetime
) -> bool:
    """The `settleable` flag for one market, against the caller's clock. [3.4] #12.

    Stamped before projection and never a computed field, for the reason
    `browsing.get_published` gives. Reads the stored status: only OPEN ever
    displays as something else, and OPEN is neither approved nor settled.
    On a read, `now` is the request's clock ("`settleable` reads the
    request's one Python clock"); the settle step passes Postgres's, read
    after its row lock.
    """
    return is_settleable(
        approved=status is MarketStatus.APPROVED,
        settled=status is MarketStatus.SETTLED,
        approved_at=approved_at,
        window=timedelta(seconds=get_settings().dispute_window_seconds),
        now=now,
    )
