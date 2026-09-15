"""Shared fixtures.

Unlike the other four suites there is no database here, because there is no
database anywhere in this service. What this one needs instead is Redis, and
only for `service/test_bus.py` and the end-to-end test in
`controller/test_ws_routes.py` — everything else drives the hub directly and
runs with nothing else on the machine at all.

Start it with `docker compose up -d redis` from the repo root.

Tokens are minted with PyJWT directly rather than by importing anything from
the auth service. That is deliberate: this service has no minting code and
never will, so a test that signs its own token exercises the same path a real
handshake takes, and it stays honest about the fact that the two services agree
on a wire format rather than on an implementation.
"""

from __future__ import annotations

import os
import pathlib
import secrets
import uuid


def _load_repo_env() -> None:
    """Read the repo-root .env, the same file docker compose reads.

    Real connection details live there and it is gitignored, so nothing in the
    repository carries a credential. Anything already exported wins.
    """
    root_env = pathlib.Path(__file__).resolve().parents[3] / ".env"
    if not root_env.is_file():
        return
    for line in root_env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_repo_env()

_test_redis = os.environ.get("REALTIME_TEST_REDIS_URL") or os.environ.get("REDIS_URL")
if not _test_redis:
    raise RuntimeError(
        "No test Redis configured.\n"
        "Add REDIS_URL to the repo-root .env, or export it:\n"
        "  export REALTIME_TEST_REDIS_URL=redis://localhost:6379/0\n"
        "Start it first with:  docker compose up -d redis"
    )

# Set before any project module is imported: core.config.get_settings is cached
# on first call, and importing main.py triggers it.
os.environ["REDIS_URL"] = _test_redis
# Fresh per run. Nothing signed here outlives the process, and no key-shaped
# string needs to sit in the repository.
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

    Lives here rather than in the file that happens to need it first, because
    three suites assert on what the fan-out delivered and a double that drifted
    between them would have them testing subtly different contracts.
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

    Defaults to TRADER because that is the only caller this service has: a
    price is public to every signed-in user and no route here branches on the
    role.

    The overridable issuer, secret and lifetime are what let a test prove this
    service rejects a token from a system it does not trust, and that it closes
    a socket whose token has run out.
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
    """A well-formed price event for a binary market.

    The two outcome ids are generated per call rather than fixed, because
    nothing in this service joins on them and a test that shared them across
    markets would be asserting a coincidence.
    """
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

    Autouse and unconditional, unlike the database fixtures in the other
    suites, because these are process-wide dictionaries rather than a
    connection somebody has to ask for. A gate that survived a test would
    silently drop the next test's first event for having a lower version than
    one that no longer exists, and it would do it in whichever test happened to
    run second.
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
    """A TestClient with the app's lifespan running.

    The `with` block is what starts the bus, so a test that uses this fixture is
    a test with a live subscription. Imported inside the fixture rather than at
    module scope so that running only the pure layers never constructs the
    application, in keeping with the rule that nothing below the controller
    knows the transport exists.
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
