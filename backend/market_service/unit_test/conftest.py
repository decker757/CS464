"""Shared fixtures.

Tests run against Postgres, not SQLite, as `market_svc` under the same grants
the service uses in production. The two engines disagree about naive versus
aware timestamps, and this service is almost entirely about timestamps, so
testing on the engine we deploy is the only way those differences show up
before production.

Start the database with `docker compose up -d db` from the repo root. The suite
uses the separate `cs464_test` database created by `sql/00-init.sh`, so it can
truncate without touching development data.

Tokens here are minted with PyJWT directly rather than by importing anything
from the auth service. That is deliberate: the market service has no minting
code and never will, so a test that signs its own token is exercising the same
path a real request takes, and it stays honest about the fact that the two
services agree on a wire format rather than on an implementation.
"""

from __future__ import annotations

import os
import secrets
import uuid

from shared.testing import load_repo_env


# Before any project module is imported. `get_settings` is lru_cached, so the
# first call wins, and importing main.py triggers it. [F-6] #76 moved the
# reader itself to `shared/testing.py`; it was identical in all five suites.
load_repo_env()

_test_db = os.environ.get("MARKET_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not _test_db:
    raise RuntimeError(
        "No test database configured.\n"
        "Add MARKET_TEST_DATABASE_URL to the repo-root .env, or export it:\n"
        "  export MARKET_TEST_DATABASE_URL="
        "postgresql+asyncpg://market_svc:PASSWORD@localhost:PORT/cs464_test\n"
        "Start the database first with:  docker compose up -d db"
    )

# Set before any project module is imported: core.config.get_settings is cached
# on first call, and importing main.py triggers it.
os.environ["DATABASE_URL"] = _test_db
# Fresh per run. Nothing signed here outlives the process, and no key-shaped
# string needs to sit in the repository.
os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(32))

# [4.3] #15. A second connection, as the audit reader, purely so the suite can
# check what this service appended to `audit.admin_actions`.
#
# It needs its own role because `market_svc` genuinely cannot read that table —
# it holds INSERT and nothing else, which is what stops one service reading
# another's actions. A test that could read the log back through the service's
# own connection would be proving the grants are weaker than they are.
_audit_db = os.environ.get("AUDIT_TEST_DATABASE_URL")

from datetime import UTC, datetime, timedelta  # noqa: E402
from decimal import Decimal  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import NullPool  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from core.config import get_settings  # noqa: E402
from core.database import (  # noqa: E402
    Base,
    dispose_engine,
    get_engine,
    get_session_factory,
)
from core.roles import UserRole  # noqa: E402
from model.entities import Market  # noqa: E402
from model.schemas import (  # noqa: E402
    MarketCloseRequest,
    MarketDraftRequest,
    OutcomeProposalRequest,
)
from service import closing, market_service  # noqa: E402
from service.audit import Actor  # noqa: E402

_UNREACHABLE = (
    "Cannot reach the test database.\n"
    "Start it with:  docker compose up -d db\n"
    "Or point MARKET_TEST_DATABASE_URL at your own Postgres."
)

_NO_AUDIT_READER = (
    "No audit reader configured.\n"
    "The audit-log tests read back what this service appended, and they must "
    "do it as audit_svc, because market_svc holds INSERT on "
    "audit.admin_actions and no SELECT.\n"
    "Add AUDIT_TEST_DATABASE_URL to the repo-root .env, or export it:\n"
    "  export AUDIT_TEST_DATABASE_URL="
    "postgresql+asyncpg://audit_svc:PASSWORD@localhost:PORT/cs464_test"
)


def mint_token(
    user_id: uuid.UUID,
    role: UserRole = UserRole.ADMIN,
    *,
    username: str = "ernest_t",
    expires_in: int = 900,
    issuer: str | None = None,
    secret: str | None = None,
) -> str:
    """Sign a token the way the auth service does, for tests only.

    The overridable issuer and secret are what let a test prove this service
    rejects a token from a system it does not trust.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
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


def bearer(user_id: uuid.UUID, role: UserRole = UserRole.ADMIN) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(user_id, role)}"}


def actor(username: str = "ernest_t", role: str = "admin") -> Actor:
    """A distinct administrator, for driving the service layer without HTTP.

    A fresh id every call, which is what keeps the audit assertions
    independent: `audit.admin_actions` is append-only, no role holds DELETE or
    TRUNCATE, and `clean_database` rebuilds only this service's own schema — so
    rows from every earlier test in this database are still there and always
    will be. Filtering on an id nothing else has used is how a test sees only
    its own entries, and it is a more honest assertion than a truncated table.

    A plain function rather than a fixture because several tests need more than
    one, and because it is called from helpers that take no fixtures.
    """
    return Actor(id=uuid.uuid4(), username=username, role=role)


@pytest.fixture
async def clean_database():
    """Rebuild the schema, then hand over an empty database.

    Dropped and recreated rather than created-if-absent. `create_all` only ever
    issues CREATE TABLE IF NOT EXISTS, so a column added to `model/entities.py`
    never reaches a test database that already has the table, and the suite
    fails with "column ... does not exist" on a model change that is perfectly
    correct. [1.2] #2 added two columns and hit exactly that.

    The cost is a drop and create per test, which is a few milliseconds for
    three small tables and buys a suite that always matches the models. When
    [F-5] #75 brings Alembic, this becomes "migrate to head" instead.

    Deliberately not autouse. Only `session` and `client` depend on it, so the
    pure unit tests under core/, model/ and the validation rules never need
    Postgres running.
    """
    from model import entities  # noqa: F401  - registers the mappers

    engine = get_engine()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except SQLAlchemyError as exc:
        pytest.fail(f"{_UNREACHABLE}\n\n{exc}")

    yield

    await dispose_engine()


@pytest.fixture
async def session(clean_database):
    """A session for driving the service layer directly, without HTTP."""
    async with get_session_factory()() as s:
        yield s


@pytest.fixture
async def client(clean_database):
    """An HTTP client bound to the app, for controller-layer tests.

    `create_app` is imported here rather than at module scope so that running
    only the pure layers never constructs the application, in keeping with the
    rule that nothing below the controller knows HTTP exists.
    """
    from main import create_app  # noqa: PLC0415

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
def admin_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def admin_headers(admin_id: uuid.UUID) -> dict[str, str]:
    return bearer(admin_id, UserRole.ADMIN)


@pytest.fixture
def other_admin_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def other_admin_headers(other_admin_id: uuid.UUID) -> dict[str, str]:
    """A second, equally legitimate administrator. Must not see the first's drafts."""
    return bearer(other_admin_id, UserRole.ADMIN)


@pytest.fixture
def trader_headers() -> dict[str, str]:
    return bearer(uuid.uuid4(), UserRole.TRADER)


# The terms of one complete market, named once. Every suite in this service
# builds its markets from these, so a rule change that needs the fixture to move
# — a longer minimum question, a second required source, a different default
# subsidy — is one edit rather than a hunt through five files that had drifted
# into agreeing by coincidence.
QUESTION = "Will Singapore core inflation be below 2% in December 2026?"
CRITERIA = "Resolves YES on the first published MAS print below 2.0%."
SOURCE_URL = "https://www.mas.gov.sg/statistics"
CLOSE_IN = timedelta(days=30)
RESOLVE_IN = timedelta(days=45)
SEED_SUBSIDY = 250


def _outcomes() -> list[dict[str, str]]:
    """A fresh list per call.

    Returned rather than held as a constant because a test that appends to what
    it is given would otherwise edit the fixture for everything after it, and
    that failure arrives in an unrelated test.
    """
    return [{"label": "Yes"}, {"label": "No"}]


def _sources() -> list[dict[str, str]]:
    return [{"url": SOURCE_URL}]


def market_terms(**overrides: object) -> dict[str, object]:
    """A market that passes every rule in service/validation.py.

    In the shape the service layer takes: real `datetime` and `Decimal`
    objects, not their wire forms. `market_json` is the same market as a
    request body, built from the same values.

    Overrides are applied last and are not validated, so a test can ask for
    terms that break exactly one rule.
    """
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "draft_key": uuid.uuid4(),
        "status": "draft",
        "question": QUESTION,
        "outcomes": _outcomes(),
        "close_time": now + CLOSE_IN,
        "resolution_time": now + RESOLVE_IN,
        "resolution_criteria": CRITERIA,
        "resolution_sources": _sources(),
        # [1.2] #2. No liquidity_b: omitting it is the common case from the
        # form, and it exercises the configured default on every submission.
        "seed_subsidy": Decimal(SEED_SUBSIDY),
    }
    base.update(overrides)
    return base


def draft_request(**overrides: object) -> MarketDraftRequest:
    """`market_terms` parsed, for driving the service layer without HTTP."""
    return MarketDraftRequest(**market_terms(**overrides))  # type: ignore[arg-type]


def market_json(**overrides: object) -> dict[str, object]:
    """The same market as a JSON request body, for driving routes.

    Shares every value with `market_terms` and differs only in serialisation,
    so a rule change moves both. It is not built by dumping `draft_request`,
    because the controller suite's whole job is what happens to a body that
    never parses: it sends `status="open"`, a naive `close_time` and unknown
    fields on purpose, and a builder that validated first could not express any
    of those tests. Overrides are applied raw, after serialisation, for the
    same reason.
    """
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "draft_key": str(uuid.uuid4()),
        "status": "draft",
        "question": QUESTION,
        "outcomes": _outcomes(),
        "close_time": (now + CLOSE_IN).isoformat(),
        "resolution_time": (now + RESOLVE_IN).isoformat(),
        "resolution_criteria": CRITERIA,
        "resolution_sources": _sources(),
        "seed_subsidy": SEED_SUBSIDY,
    }
    base.update(overrides)
    return base


async def published_market(session, actor: Actor, **overrides: object) -> Market:
    """A market that has been submitted and published, so traders can see it.

    The lifecycle as far as OPEN, in one call, for the suites whose subject
    starts somewhere past it. Anything testing the transitions themselves
    should drive `save` and `publish` directly — that is what
    unit_test/service/test_publishing.py is.
    """
    market, _, _ = await market_service.save(
        session, actor, draft_request(status="submitted", **overrides)
    )
    await market_service.publish(session, actor, market.id)
    return market


async def closed_market(session, actor: Actor, **overrides: object) -> Market:
    """The same market, after its closing time passed and the sweep ran.

    Closed the way a real market closes rather than by assigning to `status`:
    `close_time` is moved into the past and `close_due_markets` notices. ADR
    0011 is the whole reason that distinction is worth three extra lines — the
    clock is what closes a market, and a fixture that wrote the status itself
    would be asserting against a state no market in production arrives in.

    `publish` refuses to create a market that is already due — it re-runs the
    rule that a close time must be in the future — so the column is moved
    afterwards, which is the honest way to ask this question.

    Expires the session on the way out, because the sweep is a bulk UPDATE with
    `synchronize_session=False` and nothing in the identity map knows about it.
    Read anything you need off the returned market rather than off a reference
    taken before the call.
    """
    market = await published_market(session, actor, **overrides)
    market_id = market.id

    market.close_time = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()

    swept = await closing.close_due_markets(session, limit=10)
    assert market_id in swept

    session.expire_all()
    return await market_service.get(session, actor.id, market_id)


# [3.1] #9. The evidence one proposal carries, named here for the same reason
# the market's terms are: the service suite and the controller suite have to
# send the same values, or a rule change is two edits and they drift.
EVIDENCE_URL = "https://www.mas.gov.sg/statistics/cpi-december-2026"
EVIDENCE_NOTE = (
    "MAS published December 2026 core inflation at 1.8% on 23 January, below "
    "the 2.0% threshold named in the resolution criteria."
)


def proposal_terms(
    winning_outcome_id: uuid.UUID | str, **overrides: object
) -> dict[str, object]:
    """A proposal that passes every rule in service/validation.py.

    Both kinds of evidence by default, because that is what the form will
    usually send and because a test that wants only one says so by overriding
    the other to None — which reads as the case it is testing rather than as an
    accident of the fixture.
    """
    base: dict[str, object] = {
        "winning_outcome_id": winning_outcome_id,
        "evidence_url": EVIDENCE_URL,
        "evidence_note": EVIDENCE_NOTE,
    }
    base.update(overrides)
    return base


def proposal_request(
    winning_outcome_id: uuid.UUID, **overrides: object
) -> OutcomeProposalRequest:
    """`proposal_terms` parsed, for driving the service layer without HTTP."""
    return OutcomeProposalRequest(**proposal_terms(winning_outcome_id, **overrides))  # type: ignore[arg-type]


def proposal_json(winning_outcome_id: object, **overrides: object) -> dict[str, object]:
    """The same proposal as a JSON body, for driving the route.

    Not built by dumping `proposal_request`, for the reason `market_json` gives:
    the controller suite's job includes what happens to a body that never
    parses, and a builder that validated first could not express that.
    """
    return proposal_terms(str(winning_outcome_id), **overrides)


# [2.3] #7. The reason one early close carries, named here for the reason the
# market's terms and the proposal's evidence are: the service suite and the
# controller suite have to send the same value, or a rule change is two edits
# and they drift into agreeing by coincidence.
CLOSE_REASON = (
    "The resolution source retracted its December print, so this question can "
    "no longer be settled as written."
)


def close_terms(**overrides: object) -> dict[str, object]:
    """A close that passes every rule in service/validation.py.

    One builder rather than the `_terms` / `_json` pair the market and the
    proposal have, because the body is a single string: its wire form and its
    parsed form differ in type only, so there is nothing for a second builder
    to express. The controller suite sends this dict as JSON and passes
    overrides that do not parse, exactly as it does there.
    """
    base: dict[str, object] = {"reason": CLOSE_REASON}
    base.update(overrides)
    return base


def close_request(**overrides: object) -> MarketCloseRequest:
    """`close_terms` parsed, for driving the service layer without HTTP."""
    return MarketCloseRequest(**close_terms(**overrides))  # type: ignore[arg-type]


@pytest.fixture
async def audit_reader():
    """A session on `audit.admin_actions`, connected as the role that may read it.

    Separate engine, separate role, and deliberately not the one under test.

    Note what this fixture does NOT do: clean up. The log is append-only and no
    role holds DELETE or TRUNCATE, so rows from every previous test in this
    database are still present and always will be. Tests scope themselves by
    filtering on an actor id nothing else has used — see `_actor()` in
    unit_test/service/test_drafting.py — which is a more honest assertion than
    a truncated table anyway: it is what reading a real audit log looks like.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: PLC0415

    if not _audit_db:
        pytest.fail(_NO_AUDIT_READER)

    engine = create_async_engine(_audit_db, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            yield s
    finally:
        await engine.dispose()
