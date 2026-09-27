"""Shared fixtures and builders.

Tests run against Postgres, not SQLite, as `market_svc` under production
grants, in the separate `cs464_test` database (`docker compose up -d db`). Do
not "simplify" this to SQLite: the engines disagree about timestamps.

Tokens are minted with PyJWT directly, never imported from the auth service:
the two services share a wire format, not an implementation.
"""

from __future__ import annotations

import os
import secrets
import uuid

from shared.testing import load_repo_env


# Before any project module is imported: `get_settings` is cached, so the first
# call wins, and importing main.py makes it.
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

os.environ["DATABASE_URL"] = _test_db
# Fresh per run, so no key-shaped string sits in the repository.
os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(32))

# [4.3] #15. The audit reader's own connection: `market_svc` holds INSERT on
# the log and no SELECT, and reading it back through that role would prove the
# grants weaker than they are. ADR 0006.
_audit_db = os.environ.get("AUDIT_TEST_DATABASE_URL")

from datetime import UTC, datetime, timedelta  # noqa: E402
from decimal import Decimal  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import NullPool, update  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from core.config import get_settings  # noqa: E402
from core.database import (  # noqa: E402
    Base,
    dispose_engine,
    get_engine,
    get_session_factory,
)
from core.roles import UserRole  # noqa: E402
from model.entities import Market, MarketStatus  # noqa: E402
from model.schemas import (  # noqa: E402
    MarketCloseRequest,
    MarketDraftRequest,
    OutcomeApprovalRequest,
    OutcomeProposalRequest,
    OutcomeRejectionRequest,
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
) -> str:
    """Sign a token the way the auth service does, for tests only.

    The overridable issuer is what lets a test prove this service rejects a
    token from a system it does not trust.
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
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def bearer(user_id: uuid.UUID, role: UserRole = UserRole.ADMIN) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(user_id, role)}"}


def actor(username: str = "ernest_t", role: str = "admin") -> Actor:
    """A distinct administrator, for driving the service layer without HTTP.

    A fresh id every call: the audit log cannot be cleaned, so a test sees only
    its own entries by filtering on an id nothing else has used. ADR 0006.
    """
    return Actor(id=uuid.uuid4(), username=username, role=role)


@pytest.fixture
async def clean_database():
    """Drop and recreate this service's schema, then hand over an empty database.

    Dropped rather than created-if-absent, so a new column always reaches the
    test database. Not autouse, so the pure tests need no Postgres.
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

    `create_app` is imported here so the pure layers never build the app.
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


# The terms of one complete market, named once so a rule change is one edit.
QUESTION = "Will Singapore core inflation be below 2% in December 2026?"
CRITERIA = "Resolves YES on the first published MAS print below 2.0%."
SOURCE_URL = "https://www.mas.gov.sg/statistics"
CLOSE_IN = timedelta(days=30)
RESOLVE_IN = timedelta(days=45)
SEED_SUBSIDY = 250


def _outcomes() -> list[dict[str, str]]:
    """A fresh list per call, so a test that mutates it cannot affect the next."""
    return [{"label": "Yes"}, {"label": "No"}]


def _sources() -> list[dict[str, str]]:
    return [{"url": SOURCE_URL}]


def market_terms(**overrides: object) -> dict[str, object]:
    """A market that passes every rule in service/validation.py, as Python values.

    Overrides are applied last and unvalidated, so a test can break one rule.
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

    Not built by dumping `draft_request`: controller tests send bodies that
    must never parse, so overrides are applied raw, after serialisation.
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
    """A market that has been submitted and published, so traders can see it."""
    market, _, _ = await market_service.save(
        session, actor, draft_request(status="submitted", **overrides)
    )
    await market_service.publish(session, actor, market.id)
    return market


async def overdue_market(
    session,
    actor: Actor,
    *,
    overdue_by: timedelta = timedelta(seconds=1),
    **overrides: object,
) -> Market:
    """The same market once its close time has passed, before any sweep has run.

    ADR 0011's gap: trading has stopped, but `status` still reads `open`.
    `publish` refuses a market already past its close, so the close time is
    moved afterwards. Returns the market reloaded from the database.
    """
    market = await published_market(session, actor, **overrides)
    market_id = market.id
    market.close_time = datetime.now(UTC) - overdue_by
    await session.commit()
    session.expire_all()

    market = await market_service.get(session, actor.id, market_id)
    assert market.status is MarketStatus.OPEN, "the sweep must not have run yet"
    return market


async def closed_market(session, actor: Actor, **overrides: object) -> Market:
    """The same market, after its closing time passed and the sweep ran.

    Closed the way production closes one, by the clock and the sweep, not by
    writing `status` (ADR 0011). Expires the session, because the sweep's bulk
    UPDATE bypasses the identity map: read from the returned market only.
    """
    market_id = (await overdue_market(session, actor, **overrides)).id

    swept = await closing.close_due_markets(session, limit=10)
    assert market_id in swept

    session.expire_all()
    return await market_service.get(session, actor.id, market_id)


# [3.1] #9. One proposal's evidence, shared by the service and controller suites.
EVIDENCE_URL = "https://www.mas.gov.sg/statistics/cpi-december-2026"
EVIDENCE_NOTE = (
    "MAS published December 2026 core inflation at 1.8% on 23 January, below "
    "the 2.0% threshold named in the resolution criteria."
)


def proposal_terms(
    winning_outcome_id: uuid.UUID | str, **overrides: object
) -> dict[str, object]:
    """A proposal that passes every rule, with both kinds of evidence by default.

    A test that wants only one overrides the other to None.
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
    """The same proposal as a JSON body, unvalidated, as `market_json` is."""
    return proposal_terms(str(winning_outcome_id), **overrides)


# [2.3] #7. One early close's reason, shared by both suites.
CLOSE_REASON = (
    "The resolution source retracted its December print, so this question can "
    "no longer be settled as written."
)


def close_terms(**overrides: object) -> dict[str, object]:
    """A close that passes every rule. Also the JSON body: the wire form is the same."""
    base: dict[str, object] = {"reason": CLOSE_REASON}
    base.update(overrides)
    return base


def close_request(**overrides: object) -> MarketCloseRequest:
    """`close_terms` parsed, for driving the service layer without HTTP."""
    return MarketCloseRequest(**close_terms(**overrides))  # type: ignore[arg-type]


# [3.2] #10. One rejection's reason, shared by both suites.
REJECTION_REASON = (
    "The MAS print cited is the headline figure, not core inflation; the core "
    "figure for December 2026 has not been published yet."
)


def approval_terms(proposal_id: uuid.UUID | None = None) -> dict[str, object]:
    """An approval body quoting `proposal_id`, as the wire carries it.

    With no argument it quotes a proposal nobody made; a test expecting
    success that forgets the real id fails loudly as `proposal_superseded`.
    """
    return {"proposal_id": str(proposal_id or uuid.uuid4())}


def approval_request(proposal_id: uuid.UUID | None = None) -> OutcomeApprovalRequest:
    """`approval_terms` parsed, for driving the service layer without HTTP."""
    return OutcomeApprovalRequest(**approval_terms(proposal_id))  # type: ignore[arg-type]


def rejection_terms(
    proposal_id: uuid.UUID | None = None, **overrides: object
) -> dict[str, object]:
    """A rejection that passes every rule; `proposal_id` defaults as in `approval_terms`."""
    base: dict[str, object] = {
        "proposal_id": str(proposal_id or uuid.uuid4()),
        "reason": REJECTION_REASON,
    }
    base.update(overrides)
    return base


def rejection_request(
    proposal_id: uuid.UUID | None = None, **overrides: object
) -> OutcomeRejectionRequest:
    """`rejection_terms` parsed, for driving the service layer without HTTP."""
    return OutcomeRejectionRequest(**rejection_terms(proposal_id, **overrides))  # type: ignore[arg-type]


async def proposed_market(session, actor: Actor, **overrides: object) -> Market:
    """The same closed market, with its first outcome proposed by `actor`.

    `actor` is the creator and so the proposer; a decision in a test must come
    from a second administrator. Overrides are the market's terms.
    """
    market = await closed_market(session, actor, **overrides)
    return await market_service.propose_outcome(
        session, actor, market.id, proposal_request(market.outcomes[0].id)
    )


async def proposed_before_ids(session, actor: Actor) -> Market:
    """A pending proposal with no `proposal_id`, as one made before ids existed.

    Built by making a proposal and nulling its id. ADR 0016.
    """
    market_id = (await proposed_market(session, actor)).id
    await session.execute(
        update(Market).where(Market.id == market_id).values(proposal_id=None)
    )
    await session.commit()
    session.expire_all()
    return await market_service.get(session, actor.id, market_id)


@pytest.fixture
async def audit_reader():
    """A session on `audit.admin_actions`, connected as `audit_svc`, which may read it.

    It cannot clean up: nobody may delete from the log. Tests filter on a
    fresh `actor()` id instead.
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
