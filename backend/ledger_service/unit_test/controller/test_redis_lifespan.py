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

from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient


def _main():
    """`main`, imported inside each user for the reason `_redis` gives."""
    import main  # noqa: PLC0415

    return main


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

    Two requests' worth of traffic, and the dependency resolved on both.

    **The requests alone proved less than this said.** It used to drive
    `GET /health` twice, and `/health` takes no dependencies — so `get_redis`
    never ran, and rewriting it to build a fresh client per request would
    have left this green. Counting `from_url` calls shows one client per
    *process*; resolving the dependency is what shows that the one held on
    `app.state` is the one a route would be handed. [T-2] #22 is the first
    route that will take `RedisClient`, so until then this is the only thing
    exercising that seam at all.
    """
    from controller.dependencies import get_redis  # noqa: PLC0415

    app = _app()

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get("/health", headers=trader_headers)
            await client.get("/health", headers=trader_headers)

        served = [
            await get_redis(SimpleNamespace(app=app)),
            await get_redis(SimpleNamespace(app=app)),
        ]

    assert len(factory.clients) == 1, (
        f"{len(factory.clients)} clients after two requests; a client built "
        "per request or per publish is the thing this criterion excludes"
    )
    assert served[0] is served[1] is factory.clients[0], (
        "the dependency did not hand out the client the lifespan holds"
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


async def test_a_redis_that_never_answers_costs_a_bounded_wait_and_no_exception(
    clean_database: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A blackholed bus is a lost broadcast, not a trade that hangs.

    The test above covers a Redis that refuses — `publish` raises, and
    `service/bus.py` swallows it. This is the other way a bus fails, and the
    worse one: a host that accepts the connection and then never answers. With
    no socket timeout, `await client.publish(...)` waits for as long as the
    socket survives, the `except Exception` in `bus.publish` never gets
    anything to catch, and [T-2] #22's already-committed trade sits holding
    its session. Same hazard `service/market_terms.py::_TIMEOUT` exists for.

    Driven through the lifespan's own client, not one built here, because what
    is under test is the construction `main.py` actually does. The server is a
    real socket that accepts and reads nothing — a recorder that sleeps would
    prove that `asyncio.wait_for` works, not that the client times out.

    **The timeout is shortened for the test, and that is not a weakening.**
    What this holds is that the wait is *bounded* and that nothing propagates,
    not that the bound is five seconds — `test_the_socket_timeouts_are_set`
    pins the value. Left at five it spent five real seconds on every run of a
    suite the README tells people to run constantly, across a five-service CI
    matrix.
    """
    monkeypatch.setattr(_main(), "_REDIS_TIMEOUT_SECONDS", 0.25)
    import asyncio  # noqa: PLC0415
    import logging  # noqa: PLC0415
    import uuid  # noqa: PLC0415
    from datetime import UTC, datetime  # noqa: PLC0415
    from decimal import Decimal  # noqa: PLC0415

    from core.config import get_settings  # noqa: PLC0415
    from model.schemas import PriceEvent  # noqa: PLC0415
    from service import bus  # noqa: PLC0415

    held: list[asyncio.StreamWriter] = []

    async def accept_and_say_nothing(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        held.append(writer)

    server = await asyncio.start_server(accept_and_say_nothing, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(get_settings(), "redis_url", f"redis://127.0.0.1:{port}/0")

    event = PriceEvent(
        market_id=uuid.uuid4(),
        state_version=1,
        prices=[
            {"outcome_id": uuid.uuid4(), "position": 0, "price": Decimal("0.5000")},
            {"outcome_id": uuid.uuid4(), "position": 1, "price": Decimal("0.5000")},
        ],
        occurred_at=datetime.now(UTC),
    )
    transaction_id = uuid.uuid4()
    app = _app()

    try:
        async with app.router.lifespan_context(app):
            with caplog.at_level(logging.WARNING):
                # Comfortably above the client's own timeout, so a pass means
                # the client gave up rather than this line cutting it off.
                await asyncio.wait_for(
                    bus.publish(
                        app.state.redis, event, transaction_id=transaction_id
                    ),
                    timeout=15,
                )
    finally:
        for writer in held:
            writer.close()
        server.close()
        await server.wait_closed()

    assert held, "the client never reached the silent server; nothing was tested"
    assert str(transaction_id) in caplog.text, (
        "the timed-out publish was not logged with its transaction id"
    )


async def test_a_redis_url_that_does_not_parse_stops_the_ledger_booting(
    clean_database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of "startup must not depend on the bus being up".

    Unreachable is swallowed; mistyped is not, on purpose. An outage ends and
    costs broadcasts while it lasts. A `REDIS_URL` of `http://redis:6379` never
    ends: caught and logged, it would drop every broadcast forever from a
    service that answers `/health` and looks fine. Failing at boot is how the
    typo gets found, and this test is what stops a well-meant `try` around
    `from_url` from turning a config error into a silent one. DECISIONS.md, "A
    mistyped `REDIS_URL` stops the ledger booting; an unreachable one does
    not".
    """
    from core.config import get_settings  # noqa: PLC0415

    monkeypatch.setattr(get_settings(), "redis_url", "http://redis:6379")
    app = _app()

    with pytest.raises(ValueError, match="redis://"):
        async with app.router.lifespan_context(app):
            pass


async def test_the_engine_is_disposed_even_when_closing_redis_fails(
    clean_database: None, factory: _Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One `finally` does not protect two cleanups from each other.

    The lifespan closes Redis and then disposes the engine. Wrapping both in a
    single `try/finally` guards the *yield* against the cleanups being skipped
    and does nothing about the first cleanup skipping the second: an `aclose()`
    that raises leaves `dispose_engine()` unreached and every asyncpg
    connection this process opened still open.

    The two are not equally important, which is why the nesting runs this way
    round. A leaked Redis pool is a socket; a leaked engine is the database.

    **Make the `finally` flat again and this goes red.**
    """
    from core import database  # noqa: PLC0415

    disposed: list[bool] = []

    async def _dispose() -> None:
        disposed.append(True)

    monkeypatch.setattr(database, "dispose_engine", _dispose)

    import main  # noqa: PLC0415

    monkeypatch.setattr(main, "dispose_engine", _dispose)

    async def _boom() -> None:
        raise RuntimeError("redis went away mid-shutdown")

    app = _app()

    with pytest.raises(RuntimeError):
        async with app.router.lifespan_context(app):
            factory.clients[0].aclose = _boom  # type: ignore[method-assign]

    assert disposed, (
        "the database engine was never disposed, because closing Redis raised "
        "first and the two cleanups shared one `finally`"
    )
