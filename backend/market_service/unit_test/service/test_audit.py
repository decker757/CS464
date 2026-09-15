"""What reaches the audit log, and what does not. [4.3] #15

These are the tests that make the design's central claim falsifiable: an admin
action and the record of it commit together, or neither does. Every one of them
reads the log back as `audit_svc`, because `market_svc` cannot — it holds
INSERT and no SELECT, which is what stops one service reading another's
actions.

The log is append-only and nothing may truncate it, so rows from earlier tests
in this database are still present. Each test scopes itself to an actor id no
other test has used.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import DraftIncomplete
from model.audit import AdminAction
from service import market_service
from service.audit import Actor

_ENTRIES = text(
    "SELECT * FROM audit.admin_actions WHERE actor_id = :actor "
    "ORDER BY occurred_at DESC, id DESC"
)


def _actor(username: str = "ernest_t", role: str = "admin") -> Actor:
    return Actor(id=uuid.uuid4(), username=username, role=role)


def _request(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "draft_key": uuid.uuid4(),
        "status": "draft",
        "question": "Will Singapore core inflation be below 2% in December 2026?",
        "outcomes": [{"label": "Yes"}, {"label": "No"}],
        "close_time": datetime.now(UTC) + timedelta(days=30),
        "resolution_time": datetime.now(UTC) + timedelta(days=45),
        "resolution_criteria": "Resolves YES on the first published MAS print below 2.0%.",
        "resolution_sources": [{"url": "https://www.mas.gov.sg/statistics"}],
        "seed_subsidy": Decimal("250"),
    }
    base.update(overrides)
    return base


async def _entries(audit_reader: AsyncSession, actor: Actor) -> list:
    return list((await audit_reader.execute(_ENTRIES, {"actor": actor.id})).mappings())


async def _save(session: AsyncSession, actor: Actor, **overrides: object):
    from model.schemas import MarketDraftRequest  # noqa: PLC0415

    return await market_service.save(
        session, actor, MarketDraftRequest(**_request(**overrides))  # type: ignore[arg-type]
    )


# --- what gets recorded ---------------------------------------------------
async def test_a_submission_is_recorded(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """[4.3] #15's first criterion: actor, action type, target and timestamp."""
    actor = _actor()
    before = datetime.now(UTC)

    market, _, _ = await _save(session, actor, status="submitted")

    entries = await _entries(audit_reader, actor)
    assert len(entries) == 1

    entry = entries[0]
    assert entry["actor_id"] == actor.id
    assert entry["action_type"] == AdminAction.MARKET_SUBMITTED.value
    assert entry["target_type"] == "market"
    assert entry["target_id"] == market.id
    assert entry["source_service"] == "market_service"
    assert entry["occurred_at"] >= before


async def test_it_records_who_the_actor_was_rather_than_who_they_are(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The username and role are snapshots.

    This service cannot resolve an id against auth.users and neither can the
    audit service, so a name that is not stored is a name nobody can ever
    recover. Storing it also survives the rename, the demotion and the deleted
    account, which is the case an audit log exists for.
    """
    actor = _actor(username="ihsan_b", role="admin")

    await _save(session, actor, status="submitted")

    entry = (await _entries(audit_reader, actor))[0]
    assert entry["actor_username"] == "ihsan_b"
    assert entry["actor_role"] == "admin"


async def test_it_records_the_terms_that_were_submitted(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The snapshot [1.4] #4 will make necessary.

    Once a submitted market can be edited by submitting it again, the markets
    table only ever shows the latest terms. Without this, the log could say a
    market was submitted but never what was approved.
    """
    actor = _actor()

    await _save(
        session,
        actor,
        status="submitted",
        liquidity_b=Decimal("250"),
        seed_subsidy=Decimal("500"),
        question="Will the MAS core inflation print for December 2026 be below 2%?",
    )

    entry = (await _entries(audit_reader, actor))[0]
    assert entry["target_label"].startswith("Will the MAS core inflation print")
    assert entry["context"]["liquidity_b"] == "250.0000"
    assert entry["context"]["seed_subsidy"] == "500.0000"
    assert entry["context"]["outcomes"] == ["Yes", "No"]


async def test_the_subsidy_is_recorded_exactly(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """A string, not a float.

    MarketOut sends these as numbers because the form does arithmetic with
    them. Nothing does arithmetic with an audit entry, and the subsidy is
    money: the record should say what was approved, not something that once
    rounded to it.
    """
    actor = _actor()

    await _save(session, actor, status="submitted", seed_subsidy=Decimal("1234.5678"))

    entry = (await _entries(audit_reader, actor))[0]
    assert entry["context"]["seed_subsidy"] == "1234.5678"


async def test_each_submission_appends_rather_than_replacing(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """Re-submitting the same market leaves both entries, newest first."""
    actor = _actor()
    key = uuid.uuid4()

    await _save(session, actor, draft_key=key, status="submitted", question="First terms here")
    await _save(session, actor, draft_key=key, status="submitted", question="Second terms here")

    entries = await _entries(audit_reader, actor)
    assert len(entries) == 2
    assert entries[0]["target_label"] == "Second terms here"
    assert entries[1]["target_label"] == "First terms here"


# --- what does not get recorded -------------------------------------------
async def test_an_autosave_is_not_recorded(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The form saves every three seconds.

    Recording that would bury every real decision under thousands of keystroke
    entries within one sitting. The log is for decisions, not for typing.
    """
    actor = _actor()

    for word in ("Will", "Will Singapore", "Will Singapore core inflation"):
        await _save(session, actor, question=word)

    assert await _entries(audit_reader, actor) == []


async def test_a_refused_submission_records_nothing(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The atomicity claim, in the direction that matters most.

    The entry is written before the commit, so a submission that raises takes
    the entry down with it. A log that recorded submissions which never
    happened would be worse than no log, because every entry in it would have
    to be checked against the markets table before it could be believed.
    """
    actor = _actor()

    with pytest.raises(DraftIncomplete):
        await _save(session, actor, status="submitted", close_time=None)

    await session.rollback()
    assert await _entries(audit_reader, actor) == []


async def test_the_entry_and_the_market_commit_together(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The same claim in the other direction, read from a second connection.

    `audit_reader` is a different connection in a different transaction, so it
    can only see rows that are genuinely committed. One entry visible there
    means the submission's own transaction completed.
    """
    actor = _actor()

    market, _, _ = await _save(session, actor, status="submitted")

    entries = await _entries(audit_reader, actor)
    assert len(entries) == 1
    assert entries[0]["target_id"] == market.id


# --- the grants that make it append-only ----------------------------------
async def test_this_service_cannot_read_the_log_it_writes_to(
    session: AsyncSession,
) -> None:
    """INSERT without SELECT.

    This is what keeps the one cross-schema grant from being a way for this
    service to read another's actions. If it ever stops being a permission
    error, the exception in sql/02-schemas.sql has quietly widened into the
    coupling the rest of that file exists to prevent.
    """
    with pytest.raises(ProgrammingError):
        await session.execute(text("SELECT count(*) FROM audit.admin_actions"))
    await session.rollback()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit.admin_actions SET reason = 'edited'",
        "DELETE FROM audit.admin_actions",
        "TRUNCATE audit.admin_actions",
    ],
)
async def test_this_service_cannot_change_the_log(
    session: AsyncSession, statement: str
) -> None:
    """[4.3] #15's second criterion, one layer below the API.

    The route guard is what stops an edit arriving over HTTP. This is what
    stops one arriving any other way, and it is the reason the log is genuinely
    append-only rather than merely lacking an endpoint.
    """
    with pytest.raises(ProgrammingError):
        await session.execute(text(statement))
    await session.rollback()
