"""The request-ID middleware is installed on the app. #599

What the ID is and how it is accepted is `shared/`'s to test; this holds only
that this service's app carries it.
"""

from __future__ import annotations

import re

from httpx import AsyncClient


async def test_a_response_carries_a_request_id(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])
