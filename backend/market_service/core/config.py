"""Runtime configuration, read once from the environment.

Extends `shared.config.ServiceSettings`, which holds the settings every service
must read identically (the JWT trio, the cookie name, CORS). [F-6] #76. Only
what this service alone needs is declared here.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic import Field

from shared.config import ServiceSettings


class Settings(ServiceSettings):
    # --- Database -------------------------------------------------------
    # Required, with no default: a default is a credential in the repository,
    # and would let a misconfigured deploy start against the wrong database.
    # Form: postgresql+asyncpg://market_svc:PASSWORD@HOST:PORT/NAME
    database_url: str = Field(min_length=1)

    # --- Market pricing ---------------------------------------------------
    # [1.2] #2. The `b` a market gets when the administrator gives none. It
    # has a default, unlike the URL, because it is not a credential: a wrong
    # `b` is visible, harmless in mock credits and fixable per market. Decimal
    # because it shares arithmetic with credits.
    default_liquidity_b: Decimal = Field(default=Decimal("100"), gt=0)

    # --- Automatic close [F-4] #44 ----------------------------------------
    # The interval is NOT how long a closed market can still trade; that is
    # zero, because trading derives from `close_time` (ADR 0011). It only
    # bounds how stale a status count is. The batch caps the rows one
    # transaction locks; the sweeper drains batches back to back.
    close_sweep_seconds: float = Field(default=10.0, gt=0)
    close_sweep_batch: int = Field(default=100, gt=0)

    # Stops this replica sweeping without a code change. Statuses go stale;
    # trades are still refused on time. With no replica sweeping, no market
    # ever reaches CLOSED, so `propose_outcome` (which gates on CLOSED,
    # ADR 0013) never succeeds.
    close_sweep_enabled: bool = True

    # --- Dispute window [3.3] #11, [3.4] #12 --------------------------------
    # How long after approval any administrator may send a result back. Whole
    # seconds, so a demo can use a short one; the ceiling (30 days) turns a
    # mistyped unit into a boot failure, and `gt=0` because an empty window
    # decides nothing. The five-minute gap that follows it is a constant in
    # `core/settling.py`, not a setting. ADR 0019.
    dispute_window_seconds: int = Field(default=86400, gt=0, le=2592000)

    # --- Paging [X-1] #104, #210 ------------------------------------------
    # The public browse and the admin overview are bounded, with the audit
    # feed's and the user list's defaults and for their reason. DECISIONS.md,
    # "The audit feed's page sizes are settings, not constants".
    default_page_size: int = Field(default=50, gt=0)
    max_page_size: int = Field(default=200, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
