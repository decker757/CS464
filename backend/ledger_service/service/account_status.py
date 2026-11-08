"""The ledger's auth client. [4.2] #14, ADR 0022.

One client for the process (`main.py`), which the leaderboard's user directory
and the copy's refresh of suspended ids go through. The trade path asks auth
nothing: it reads the copy (`service/local_copy.py`, ADR 0030). `open_client`
takes a transport so the suite drives the real request through
`httpx.MockTransport`.
"""

from __future__ import annotations

import httpx

from core import observability
from core.config import get_settings

# Stated rather than inherited, as the terms client's is (D-030). Its own
# constant: the two dependencies' budgets may reasonably diverge.
_TIMEOUT = httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0)


def open_client(
    *, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    """The client every call to auth goes through: auth's base URL and
    D-030's timeout. The caller closes it.

    `main.py`'s lifespan opens one for the process. `transport` is the
    suite's seam.
    """
    client = httpx.AsyncClient(
        base_url=get_settings().auth_service_url,
        transport=transport,
        timeout=_TIMEOUT,
        event_hooks={"request": [observability.send_request_id]},
    )
    observability.instrument_client(client)
    return client
