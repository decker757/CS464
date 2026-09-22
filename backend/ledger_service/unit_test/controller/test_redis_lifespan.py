"""One Redis client for the process. [F-9] #112

The criterion: "opened on the app lifespan in `main.py` and closed with it,
injected into the route/service that publishes. Not one per publish."

**Why this is worth a file of its own.** `service/market_terms.py` builds a
client per call and spends a paragraph saying why that is affordable — it runs
once per market, ever, and the cost is one handshake against a request that is
already doing a round trip and three inserts. That argument does not survive
being copied here. A publish runs once per *trade*, so a client per call is a
DNS lookup, a TCP handshake and a pool teardown on the hot path, for a call
whose entire purpose is to be cheap enough that failing it silently is
acceptable.

That same paragraph names the two costs of a held client, and both are paid
here rather than dodged: it needs closing in a lifespan, and it fixes the
transport at construction. The closing is below. The transport is why
`publish(client, event, ...)` takes the client as an argument — the seam moves
from construction to the call, which is what
`unit_test/service/test_price_publish.py` drives through.

**Nothing in this ticket publishes**, so there is no end-to-end path to
observe. What can be observed is the lifecycle: one client built at startup,
the same object served to every request, and closed on shutdown. Asserted by
replacing the factory rather than by reading an attribute, so the test says
"one client was built" rather than "one client was stored somewhere I
happened to look".
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient


def _redis():
    """`redis.asyncio`, imported inside each user rather than at module scope.

    The pin lands with this ticket, so a top-level import is a
    `ModuleNotFoundError` that takes the whole file down as one red line before
    any of these criteria has been read. Reached through here, each test fails
    on its own, named after the criterion it holds (D-007).
    """
    import redis.asyncio as redis  # noqa: PLC0415

    return redis


class _FakeClient:
    """Stands in for `redis.asyncio.Redis`. Records only its own closing.

    Deliberately not a `Mock`: the assertions below are about how many of these
    exist and whether each was closed, and a `Mock` would answer any attribute
    access truthfully enough to hide a wiring mistake.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.closed = False

    async def publish(self, channel: str, payload: object) -> int:
        return 0

    async def aclose(self) -> None:
        # `aclose`, not `close`: the sync name exists on the async client too
        # and does something else. `realtime_service/service/bus.py` carries
        # the same note on its own teardown.
        self.closed = True


class _Factory:
    """A counting stand-in for `redis.asyncio.from_url`."""

    def __init__(self) -> None:
        self.clients: list[_FakeClient] = []

    def __call__(self, url: str, *args: Any, **kwargs: Any) -> _FakeClient:
        client = _FakeClient(url)
        self.clients.append(client)
        return client


@pytest.fixture
def factory(monkeypatch: pytest.MonkeyPatch) -> _Factory:
    """Replace the constructor every reasonable implementation reaches for.

    Patched on `redis.asyncio` itself rather than on `main`, so it catches both
    `redis.from_url(...)` after `import redis.asyncio as redis` and a direct
    `from redis.asyncio import from_url`. If an implementation builds its
    client some third way, this fixture sees nothing and the counts below read
    zero — which fails loudly rather than passing vacuously.
    """
    made = _Factory()
    monkeypatch.setattr(_redis(), "from_url", made)
    return made


def _app():
    """Imported inside the tests, so a missing name fails the test that needs
    it rather than collecting the whole file (D-007)."""
    from main import create_app  # noqa: PLC0415

    return create_app()


async def test_one_client_is_opened_for_the_process(
    clean_database: None, factory: _Factory
) -> None:
    """Built once, on startup, before any request has arrived.

    "Not one per publish" is the criterion's wording, and the cheapest way to
    satisfy it wrongly is to build the client lazily on first use and memoise
    it. That would pass a count taken after two requests and would still put a
    connection handshake inside the first trade that reaches the publish —
    which is a trade that has already committed and is now waiting on DNS.
    """
    app = _app()

    async with app.router.lifespan_context(app):
        assert len(factory.clients) == 1, (
            f"{len(factory.clients)} Redis client(s) built by startup; the "
            "criterion is one for the process, opened on the lifespan"
        )


async def test_the_same_client_serves_every_request(
    clean_database: None, factory: _Factory, trader_headers: dict[str, str]
) -> None:
    """Two requests, still one client.

    Driven through real requests rather than by calling the dependency
    directly, because what is being asserted is that the wiring — lifespan to
    `app.state` to the injected dependency — hands out the held object rather
    than building one on the way past.
    """
    app = _app()

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get("/health", headers=trader_headers)
            await client.get("/health", headers=trader_headers)

    assert len(factory.clients) == 1, (
        f"{len(factory.clients)} clients after two requests; a client built "
        "per request or per publish is the thing this criterion excludes"
    )


async def test_the_client_is_closed_when_the_app_shuts_down(
    clean_database: None, factory: _Factory
) -> None:
    """Closed with the lifespan, not left to the garbage collector.

    A connection pool that outlives its application is the reason
    `market_terms.fetch` uses `async with` for its per-call client, and the
    reason the paragraph there lists "it needs closing in `main.py`'s
    lifespan" as a real cost of holding one. Leaking it here would show up as
    a warning in the suite and as an idle connection per replica restart in
    production.
    """
    app = _app()

    async with app.router.lifespan_context(app):
        pass

    assert factory.clients, "no Redis client was built at all"
    assert factory.clients[0].closed, (
        "the process-wide Redis client was not closed on shutdown"
    )


async def test_the_client_is_built_from_the_configured_url(
    clean_database: None, factory: _Factory
) -> None:
    """From `settings.redis_url`, not from a literal.

    The default exists so a checkout starts without the variable; a hardcoded
    `redis://redis:6379/0` would make the setting decorative and would point
    every deployment at compose's hostname.
    """
    from core.config import get_settings  # noqa: PLC0415

    app = _app()

    async with app.router.lifespan_context(app):
        pass

    assert factory.clients[0].url == get_settings().redis_url


async def test_the_app_still_starts_when_redis_is_unreachable(
    clean_database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup must not depend on the bus being up.

    `redis.from_url` does not connect — it builds a pool that dials lazily — so
    this passes for free against the obvious implementation and fails against
    the tempting addition: a `PING` on startup to "fail fast". That would make
    an unreachable Redis a ledger that will not boot, which inverts the whole
    argument for swallowing a publish failure. `realtime_service` made the same
    call from the other side, and its `/health` reports the bus separately for
    exactly this reason.
    """

    def unreachable(url: str, *args: object, **kwargs: object) -> _FakeClient:
        client = _FakeClient(url)

        async def refuse(*a: object, **k: object) -> int:
            raise ConnectionError("redis is unreachable")

        client.publish = refuse  # type: ignore[method-assign]
        return client

    monkeypatch.setattr(_redis(), "from_url", unreachable)
    app = _app()

    async with app.router.lifespan_context(app):
        pass
