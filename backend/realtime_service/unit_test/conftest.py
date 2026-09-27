"""Shared fixtures.

No database, because this service has none. Redis is needed only by
`service/test_bus.py` and the delivery tests in `controller/test_ws_routes.py`:
`docker compose up -d redis`. Tokens are signed with PyJWT directly, because
this service has no minting code and must not import the auth service's.
"""

from __future__ import annotations

import os
import secrets
import uuid

from shared.testing import load_repo_env


# Everything down to the project imports runs first: `get_settings` is cached
# on first call, and importing main.py triggers it.
load_repo_env()

_test_redis = os.environ.get("REALTIME_TEST_REDIS_URL") or os.environ.get("REDIS_URL")
if not _test_redis:
    raise RuntimeError(
        "No test Redis configured.\n"
        "Add REDIS_URL to the repo-root .env, or export it:\n"
        "  export REALTIME_TEST_REDIS_URL=redis://localhost:6379/0\n"
        "Start it first with:  docker compose up -d redis"
    )

os.environ["REDIS_URL"] = _test_redis
# Fresh per run, so no key-shaped string sits in the repository.
os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(32))

from datetime import UTC, datetime, timedelta  # noqa: E402

from decimal import Decimal  # noqa: E402
from typing import Any  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402

from core.config import get_settings  # noqa: E402
from core.roles import UserRole  # noqa: E402
from model.schemas import OutcomePrice, PriceEvent  # noqa: E402
from service.ordering import get_gate, reset_gate  # noqa: E402
from service.subscriptions import get_hub, reset_hub  # noqa: E402

REDIS_UNREACHABLE = (
    "Cannot reach the test Redis.\n"
    "Start it with:  docker compose up -d redis\n"
    "Or point REALTIME_TEST_REDIS_URL at your own."
)


class Recorder:
    """A `service.subscriptions.Subscriber` that keeps what it was handed.

    Shared, so every suite asserting on the fan-out uses the same double.
    """

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    def enqueue(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)


def mint_token(
    user_id: uuid.UUID | None = None,
    role: UserRole = UserRole.TRADER,
    *,
    username: str = "ernest_t",
    expires_in: int = 900,
    issuer: str | None = None,
    secret: str | None = None,
) -> str:
    """Sign a token the way the auth service does, for tests only.

    Issuer, secret and lifetime are overridable so a test can present a
    foreign or expiring token.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id or uuid.uuid4()),
            "username": username,
            "role": UserRole(role).value,
            "iss": issuer if issuer is not None else settings.jwt_issuer,
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
            "jti": secrets.token_urlsafe(16),
        },
        secret if secret is not None else settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def bearer(**kwargs) -> dict[str, str]:
    """A token as a service would send it: an explicit header."""
    return {"Authorization": f"Bearer {mint_token(**kwargs)}"}


def make_event(
    market_id: uuid.UUID,
    state_version: int = 1,
    *,
    yes: str = "0.6000",
    no: str = "0.4000",
) -> "PriceEvent":
    """A well-formed price event for a binary market, with fresh outcome ids."""
    return PriceEvent(
        market_id=market_id,
        state_version=state_version,
        prices=[
            OutcomePrice(outcome_id=uuid.uuid4(), position=0, price=Decimal(yes)),
            OutcomePrice(outcome_id=uuid.uuid4(), position=1, price=Decimal(no)),
        ],
        occurred_at=datetime.now(UTC),
    )


@pytest.fixture(autouse=True)
def fresh_singletons():
    """Give every test its own hub and version gate.

    Autouse: a gate that survived one test would silently drop the next test's
    first event as stale.
    """
    reset_hub()
    reset_gate()
    yield
    reset_hub()
    reset_gate()


@pytest.fixture
def hub():
    return get_hub()


@pytest.fixture
def gate():
    return get_gate()


@pytest.fixture
def redis_url() -> str:
    return get_settings().redis_url


@pytest.fixture
def client():
    """A TestClient with the app's lifespan, and so the bus, running.

    Imported here so the pure-layer suites never construct the application.
    """
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from main import create_app  # noqa: PLC0415

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def market_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def other_market_id() -> uuid.UUID:
    return uuid.uuid4()
