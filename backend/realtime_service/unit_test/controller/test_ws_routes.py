"""The socket: who may open one, what it accepts, and how it ends.

Delivery is driven by publishing to Redis, not by calling `hub.broadcast`: the
app runs its own event loop in another thread, and `asyncio.Queue` is not
thread safe, so a broadcast from the test thread might never wake the pump.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest
import redis
from starlette.websockets import WebSocketDisconnect

from core.errors import (
    InvalidMarketId,
    MalformedCommand,
    NotAuthenticated,
    SessionExpired,
    UnknownAction,
)
from service.subscriptions import get_hub
from unit_test.conftest import REDIS_UNREACHABLE, bearer, mint_token

_ALLOWED_ORIGIN = "http://localhost:5173"


@pytest.fixture
def connected_client(client, redis_url: str):
    """A client whose bus has actually subscribed, plus a publisher.

    Waits on `/health` because Redis discards a message published before the
    subscription lands.
    """
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if client.get("/health").json()["bus"] == "connected":
            break
        time.sleep(0.02)
    else:
        pytest.fail(REDIS_UNREACHABLE)

    publisher = redis.Redis.from_url(redis_url, decode_responses=True)
    try:
        yield client, publisher
    finally:
        publisher.close()


def _publish(publisher, market_id: uuid.UUID, version: int = 1) -> None:
    from unit_test.conftest import make_event

    publisher.publish("market.price", make_event(market_id, version).model_dump_json())


def _subscribe(websocket, market_id: uuid.UUID) -> dict:
    websocket.send_json({"action": "subscribe", "market_id": str(market_id)})
    return websocket.receive_json()


# ---------------------------------------------------------------------------
# The handshake
# ---------------------------------------------------------------------------


def test_a_socket_with_no_token_is_refused(client) -> None:
    """Closed with a code rather than refused at the handshake, so the client
    can tell an expired session from a dead network. [X-4] #37's last three
    acceptance criteria all depend on that distinction."""
    with client.websocket_connect("/ws/prices") as websocket:
        with pytest.raises(WebSocketDisconnect) as refused:
            websocket.receive_json()

    assert refused.value.code == NotAuthenticated.close_code


@pytest.mark.parametrize(
    ("description", "headers"),
    [
        ("garbage", {"Authorization": "Bearer not-a-token"}),
        ("an empty bearer", {"Authorization": "Bearer "}),
        ("another system's issuer", bearer(issuer="somebody-elses-auth")),
        ("another key", bearer(secret="a-different-secret-that-is-long-enough")),
        ("an expired token", bearer(expires_in=-1)),
    ],
)
def test_a_bad_token_is_refused(client, description: str, headers: dict) -> None:
    with client.websocket_connect("/ws/prices", headers=headers) as websocket:
        with pytest.raises(WebSocketDisconnect) as refused:
            websocket.receive_json()

    assert refused.value.code == NotAuthenticated.close_code, description


def test_a_service_may_authenticate_with_a_bearer_header(
    client, market_id: uuid.UUID
) -> None:
    with client.websocket_connect("/ws/prices", headers=bearer()) as websocket:
        assert _subscribe(websocket, market_id)["type"] == "subscribed"


def test_a_browser_may_authenticate_with_the_cookie(
    client, market_id: uuid.UUID
) -> None:
    """The only transport a browser has. The JavaScript WebSocket API has no
    parameter for request headers, which is why ADR 0002's same-registrable-
    domain constraint is load-bearing for this service specifically."""
    client.cookies.set("access_token", mint_token())

    with client.websocket_connect("/ws/prices") as websocket:
        assert _subscribe(websocket, market_id)["type"] == "subscribed"


def test_the_header_wins_over_the_cookie(client, market_id: uuid.UUID) -> None:
    """ADR 0002's precedence rule, which every service has to agree on. An
    explicitly attached credential beats one the browser sent ambiently, so a
    service call carrying its own token is never reinterpreted as whoever
    happens to be logged in."""
    client.cookies.set("access_token", "a-cookie-that-would-not-verify")

    with client.websocket_connect("/ws/prices", headers=bearer()) as websocket:
        assert _subscribe(websocket, market_id)["type"] == "subscribed"


def test_a_socket_from_an_unlisted_origin_is_refused(client) -> None:
    """The guard on the hand-written origin check: CORS does not apply to
    WebSockets, so this is the only thing refusing another site's page.
    ADR 0010."""
    headers = {**bearer(), "origin": "http://evil.test"}

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/prices", headers=headers) as websocket:
            websocket.receive_json()


def test_a_socket_from_a_listed_origin_is_allowed(
    client, market_id: uuid.UUID
) -> None:
    headers = {**bearer(), "origin": _ALLOWED_ORIGIN}

    with client.websocket_connect("/ws/prices", headers=headers) as websocket:
        assert _subscribe(websocket, market_id)["type"] == "subscribed"


def test_a_socket_with_no_origin_is_allowed(client, market_id: uuid.UUID) -> None:
    """A service-to-service caller sends none, and the attack being refused is a
    browser attack, which by definition sends one."""
    with client.websocket_connect("/ws/prices", headers=bearer()) as websocket:
        assert _subscribe(websocket, market_id)["type"] == "subscribed"


# ---------------------------------------------------------------------------
# The client protocol
# ---------------------------------------------------------------------------


def test_subscribing_is_acknowledged(client, market_id: uuid.UUID) -> None:
    """Worth the round trip: without it a client cannot tell "subscribed to a
    market nobody has traded yet" from "my command was dropped"."""
    with client.websocket_connect("/ws/prices", headers=bearer()) as websocket:
        assert _subscribe(websocket, market_id) == {
            "type": "subscribed",
            "market_id": str(market_id),
        }


def test_unsubscribing_is_acknowledged(client, market_id: uuid.UUID) -> None:
    with client.websocket_connect("/ws/prices", headers=bearer()) as websocket:
        _subscribe(websocket, market_id)
        websocket.send_json({"action": "unsubscribe", "market_id": str(market_id)})

        assert websocket.receive_json() == {
            "type": "unsubscribed",
            "market_id": str(market_id),
        }


def test_subscribing_twice_is_one_subscription(client, market_id: uuid.UUID) -> None:
    with client.websocket_connect("/ws/prices", headers=bearer()) as websocket:
        _subscribe(websocket, market_id)
        _subscribe(websocket, market_id)

        hub = get_hub()
        subscriber = next(iter(hub._by_subscriber))
        assert hub.subscription_count(subscriber) == 1


@pytest.mark.parametrize(
    ("description", "payload", "expected"),
    [
        ("not json", "}{", MalformedCommand),
        ("json but not an object", json.dumps([1, 2]), MalformedCommand),
        ("an action nobody serves", json.dumps({"action": "snapshot"}), UnknownAction),
        (
            "a market id that is not a uuid",
            json.dumps({"action": "subscribe", "market_id": "the-big-one"}),
            InvalidMarketId,
        ),
        (
            "no market id at all",
            json.dumps({"action": "subscribe"}),
            InvalidMarketId,
        ),
    ],
)
def test_a_bad_command_is_answered_and_the_socket_survives(
    client, market_id: uuid.UUID, description: str, payload: str, expected: type
) -> None:
    """One mistyped frame is a bug in one frame. Dropping the socket would turn
    it into a reconnect loop against a server whose job is holding connections
    open."""
    with client.websocket_connect("/ws/prices", headers=bearer()) as websocket:
        websocket.send_text(payload)
        answer = websocket.receive_json()

        assert answer["type"] == "error", description
        assert answer["error"]["code"] == expected.code, description

        # Still usable afterwards, which is the half that matters.
        assert _subscribe(websocket, market_id)["type"] == "subscribed"


def test_a_binary_frame_is_a_malformed_command(client) -> None:
    """The protocol is JSON text. A client sending bytes has the same problem as
    one sending "hello", so it gets the same answer rather than an internal
    error."""
    with client.websocket_connect("/ws/prices", headers=bearer()) as websocket:
        websocket.send_bytes(b"\x00\x01")

        assert websocket.receive_json()["error"]["code"] == MalformedCommand.code


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def test_a_published_price_reaches_a_subscribed_socket(
    connected_client, market_id: uuid.UUID
) -> None:
    """End to end: a producer publishes, and a browser's socket receives."""
    client, publisher = connected_client

    with client.websocket_connect("/ws/prices", headers=bearer()) as websocket:
        _subscribe(websocket, market_id)
        _publish(publisher, market_id, 4)

        frame = websocket.receive_json()

    assert frame["type"] == "price"
    assert frame["market_id"] == str(market_id)
    assert frame["state_version"] == 4
    assert frame["prices"][0]["price"] == "0.6000"


def test_a_socket_receives_only_the_markets_it_asked_for(
    connected_client, market_id: uuid.UUID, other_market_id: uuid.UUID
) -> None:
    client, publisher = connected_client

    with client.websocket_connect("/ws/prices", headers=bearer()) as websocket:
        _subscribe(websocket, market_id)
        _publish(publisher, other_market_id, 1)
        _publish(publisher, market_id, 1)

        frame = websocket.receive_json()

    assert frame["market_id"] == str(market_id)


def test_unsubscribing_stops_delivery(
    connected_client, market_id: uuid.UUID, other_market_id: uuid.UUID
) -> None:
    client, publisher = connected_client

    with client.websocket_connect("/ws/prices", headers=bearer()) as websocket:
        _subscribe(websocket, market_id)
        _subscribe(websocket, other_market_id)

        websocket.send_json({"action": "unsubscribe", "market_id": str(market_id)})
        assert websocket.receive_json()["type"] == "unsubscribed"

        _publish(publisher, market_id, 2)
        _publish(publisher, other_market_id, 2)

        # The next frame is the one that was NOT unsubscribed. If the
        # unsubscribe had not taken effect, this would be the other market's.
        frame = websocket.receive_json()

    assert frame["market_id"] == str(other_market_id)


def test_a_stale_price_never_reaches_the_socket(
    connected_client, market_id: uuid.UUID
) -> None:
    """[X-4] #37's fourth acceptance criterion, all the way through."""
    client, publisher = connected_client

    with client.websocket_connect("/ws/prices", headers=bearer()) as websocket:
        _subscribe(websocket, market_id)

        _publish(publisher, market_id, 9)
        assert websocket.receive_json()["state_version"] == 9

        _publish(publisher, market_id, 8)
        _publish(publisher, market_id, 10)

        assert websocket.receive_json()["state_version"] == 10


# ---------------------------------------------------------------------------
# How a connection ends
# ---------------------------------------------------------------------------


def test_a_socket_closes_when_its_token_expires(
    client, market_id: uuid.UUID
) -> None:
    """Without this a socket outlives its fifteen-minute token by hours.
    ADR 0010."""
    with client.websocket_connect(
        "/ws/prices", headers=bearer(expires_in=1)
    ) as websocket:
        _subscribe(websocket, market_id)

        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_json()

    assert closed.value.code == SessionExpired.close_code


def test_a_closed_socket_leaves_nothing_in_the_hub(
    client, market_id: uuid.UUID
) -> None:
    """A connection left in the hub is a connection the next broadcast tries to
    write to, forever."""
    with client.websocket_connect("/ws/prices", headers=bearer()) as websocket:
        _subscribe(websocket, market_id)
        assert get_hub().subscriber_count(market_id) == 1

    assert get_hub().subscriber_count(market_id) == 0
    assert get_hub().connection_count == 0


def test_a_socket_that_never_subscribed_leaves_nothing_behind(client) -> None:
    """`forget` has to be safe for a connection that was closed before it said
    a word — which is every connection refused for an expired token."""
    with client.websocket_connect("/ws/prices", headers=bearer()):
        pass

    assert get_hub().connection_count == 0


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def test_health_reports_the_bus_separately(connected_client) -> None:
    """Green even when the bus is down, and says so in the same breath. Failing
    the probe would have the orchestrator restart the container and drop every
    socket it holds, which is the one response that reliably makes a Redis blip
    worse."""
    client, _ = connected_client

    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["bus"] == "connected"
