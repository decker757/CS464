"""One Redis client for the process. [F-9] #112, D-047, D-051.

"Opened on the app lifespan in `main.py` and closed with it ... Not one per
publish." The lifecycle is observed by replacing the factory: one client built
at startup, served to every request, closed on shutdown.
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
    """`redis.asyncio`, imported inside each user, so a missing module fails
    one test rather than collection (D-007)."""
    import redis.asyncio as redis  # noqa: PLC0415

    return redis


class _FakeClient:
    """Stands in for `redis.asyncio.Redis` and records only its own closing.
    Not a `Mock`, which would answer any attribute and hide a wiring mistake.
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
    """Replace `redis.asyncio.from_url` itself, so any import style is caught;
    a client built some other way leaves the counts at zero and fails.
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
    """Built once, on startup, before any request: a lazy client would put a
    handshake inside the first committed trade."""
    app = _app()

    async with app.router.lifespan_context(app):
        assert len(factory.clients) == 1, (
            f"{len(factory.clients)} Redis client(s) built by startup; the "
            "criterion is one for the process, opened on the lifespan"
        )


async def test_the_same_client_serves_every_request(
    clean_database: None, factory: _Factory, trader_headers: dict[str, str]
) -> None:
    """Two requests, still one client, with the dependency resolved on both:
    `/health` alone takes no dependency and would prove nothing.
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
    """Closed with the lifespan, not left to the garbage collector."""
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
    """From `settings.redis_url`, not a literal."""
    from core.config import get_settings  # noqa: PLC0415

    app = _app()

    async with app.router.lifespan_context(app):
        pass

    assert factory.clients[0].url == get_settings().redis_url


async def test_the_app_still_starts_when_redis_is_unreachable(
    clean_database: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup must not depend on the bus being up. Goes red if someone adds a
    startup `PING` to "fail fast" (D-047)."""

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
    """A Redis that accepts and never answers costs a bounded wait, not a hung
    trade (D-051).

    Through the lifespan's own client, against a real socket that reads
    nothing. The timeout is shortened for speed: what is held is that the wait
    is bounded and nothing propagates.
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
    """D-051: a mistyped `REDIS_URL` stops the boot, so a well-meant `try`
    around `from_url` cannot make a config error silent."""
    from core.config import get_settings  # noqa: PLC0415

    monkeypatch.setattr(get_settings(), "redis_url", "http://redis:6379")
    app = _app()

    with pytest.raises(ValueError, match="redis://"):
        async with app.router.lifespan_context(app):
            pass


async def test_the_engine_is_disposed_even_when_closing_redis_fails(
    clean_database: None, factory: _Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing `aclose()` must not skip `dispose_engine()`. Make the
    `finally` flat and this goes red."""
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
