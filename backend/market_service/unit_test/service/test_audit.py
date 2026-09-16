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

# The suite's one actor factory. Aliased rather than imported under its own
# name because every helper below takes an `actor` argument, which would
# shadow it.
from unit_test.conftest import EVIDENCE_NOTE, EVIDENCE_URL, closed_market
from unit_test.conftest import actor as _actor
from unit_test.conftest import market_terms as _request
from unit_test.conftest import proposal_request as _proposal

_ENTRIES = text(
    "SELECT * FROM audit.admin_actions WHERE actor_id = :actor "
    "ORDER BY occurred_at DESC, id DESC"
)


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


# --- publication [1.3] #3 -------------------------------------------------
async def _publish(session: AsyncSession, actor: Actor, **overrides: object):
    market, _, _ = await _save(session, actor, status="submitted", **overrides)
    return await market_service.publish(session, actor, market.id)


async def test_a_publication_is_recorded(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """[1.3] #3's third criterion.

    The entry that matters most in this file: the moment a set of terms was put
    in front of people who will commit credits to them.
    """
    actor = _actor()
    before = datetime.now(UTC)

    market = await _publish(session, actor)

    published = [
        e for e in await _entries(audit_reader, actor)
        if e["action_type"] == AdminAction.MARKET_PUBLISHED.value
    ]
    assert len(published) == 1

    entry = published[0]
    assert entry["actor_id"] == actor.id
    assert entry["target_type"] == "market"
    assert entry["target_id"] == market.id
    assert entry["source_service"] == "market_service"
    assert entry["occurred_at"] >= before


async def test_submitting_and_publishing_leave_two_distinct_entries(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """Two decisions, two records.

    A market can sit submitted for a week before anybody publishes it, and only
    the second of those exposed anything to a trader. Folding them into one
    would lose the question the log will actually be asked.
    """
    actor = _actor()

    await _publish(session, actor)

    entries = await _entries(audit_reader, actor)
    assert {e["action_type"] for e in entries} == {
        AdminAction.MARKET_SUBMITTED.value,
        AdminAction.MARKET_PUBLISHED.value,
    }
    assert len(entries) == 2


async def test_a_publication_records_the_terms_that_went_live(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """Carried on the entry rather than pointed at from it.

    [1.4] #4 lets a submitted market be edited by submitting it again, so the
    terms that went live are not necessarily the terms of any one earlier
    entry, and a reader should not have to replay a market's history to find
    out which set traders actually saw.
    """
    actor = _actor()

    await _publish(
        session,
        actor,
        liquidity_b=Decimal("250"),
        seed_subsidy=Decimal("500"),
        question="Will the MAS core inflation print for December 2026 be below 2%?",
    )

    entry = next(
        e for e in await _entries(audit_reader, actor)
        if e["action_type"] == AdminAction.MARKET_PUBLISHED.value
    )
    assert entry["target_label"].startswith("Will the MAS core inflation print")
    assert entry["context"]["liquidity_b"] == "250.0000"
    assert entry["context"]["seed_subsidy"] == "500.0000"
    assert entry["context"]["outcomes"] == ["Yes", "No"]


async def test_the_two_snapshots_have_the_same_shape(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """One function builds both, so an unchanged market gives identical terms.

    The one case this log is genuinely useful for is seeing whether anything
    moved between submission and going live, and that comparison is only
    possible if the two entries describe a market the same way.
    """
    actor = _actor()

    await _publish(session, actor)

    entries = await _entries(audit_reader, actor)
    submitted = next(
        e for e in entries if e["action_type"] == AdminAction.MARKET_SUBMITTED.value
    )
    published = next(
        e for e in entries if e["action_type"] == AdminAction.MARKET_PUBLISHED.value
    )
    assert published["context"] == submitted["context"]


async def test_the_publication_entry_and_the_status_commit_together(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """ADR 0006's claim, on the action this ticket adds.

    `audit_reader` is a separate connection in a separate transaction, so an
    entry visible there is genuinely committed. A market cannot become
    tradeable without a record of who made it so.
    """
    actor = _actor()

    market = await _publish(session, actor)

    published = [
        e for e in await _entries(audit_reader, actor)
        if e["action_type"] == AdminAction.MARKET_PUBLISHED.value
    ]
    assert len(published) == 1
    assert published[0]["target_id"] == market.id


# --- proposing an outcome [3.1] #9 ----------------------------------------
async def _propose(session: AsyncSession, actor: Actor, **overrides: object):
    market = await closed_market(session, actor)
    return await market_service.propose_outcome(
        session, actor, market.id, _proposal(market.outcomes[0].id, **overrides)
    )


async def test_a_proposal_is_recorded(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The half of "so the decision is documented" that survives [3.2] #10.

    A rejection clears the columns on the market and sends it back to CLOSED.
    After that this entry is the only record that the proposal was ever made.
    """
    actor = _actor()
    before = datetime.now(UTC)

    market = await _propose(session, actor)

    proposed = [
        e for e in await _entries(audit_reader, actor)
        if e["action_type"] == AdminAction.MARKET_OUTCOME_PROPOSED.value
    ]
    assert len(proposed) == 1

    entry = proposed[0]
    assert entry["actor_id"] == actor.id
    assert entry["target_type"] == "market"
    assert entry["target_id"] == market.id
    assert entry["source_service"] == "market_service"
    assert entry["occurred_at"] >= before


async def test_a_proposal_records_the_outcome_and_the_evidence(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The label as well as the id.

    `audit_svc` may read this log and nothing else, so an entry naming only a
    UUID would be unreadable to the one role that can read it at all.
    """
    actor = _actor()

    market = await _propose(session, actor)

    entry = next(
        e for e in await _entries(audit_reader, actor)
        if e["action_type"] == AdminAction.MARKET_OUTCOME_PROPOSED.value
    )
    assert entry["context"]["winning_outcome_id"] == str(market.proposed_outcome_id)
    assert entry["context"]["winning_outcome"] == "Yes"
    assert entry["context"]["evidence_url"] == EVIDENCE_URL
    assert entry["context"]["evidence_note"] == EVIDENCE_NOTE


async def test_the_evidence_is_context_rather_than_a_reason(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """Evidence is not a reason, and the two must not be conflated.

    `reason` is for the free-text justification [2.3] #7 and [3.2] #10 demand
    of an administrator. Splitting a URL into `context` and a note into
    `reason` would put one proposal's support in two columns.
    """
    actor = _actor()

    await _propose(session, actor)

    entry = next(
        e for e in await _entries(audit_reader, actor)
        if e["action_type"] == AdminAction.MARKET_OUTCOME_PROPOSED.value
    )
    assert entry["reason"] is None


async def test_a_proposal_leaves_the_earlier_entries_alone(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """Three decisions, three records, and an automatic close is not one of
    them — the clock is not an actor, and the `market.published` entry already
    recorded the `close_time` that was approved."""
    actor = _actor()

    await _propose(session, actor)

    entries = await _entries(audit_reader, actor)
    assert [e["action_type"] for e in entries] == [
        AdminAction.MARKET_OUTCOME_PROPOSED.value,
        AdminAction.MARKET_PUBLISHED.value,
        AdminAction.MARKET_SUBMITTED.value,
    ]


async def test_a_refused_proposal_records_nothing(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The entry is written before the commit, so a proposal that raises takes
    the entry down with it."""
    from core.errors import ProposalIncomplete  # noqa: PLC0415

    actor = _actor()

    with pytest.raises(ProposalIncomplete):
        await _propose(session, actor, evidence_url=None, evidence_note=None)

    await session.rollback()
    entries = await _entries(audit_reader, actor)
    assert AdminAction.MARKET_OUTCOME_PROPOSED.value not in {
        e["action_type"] for e in entries
    }


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


async def test_a_refused_publish_records_nothing(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """A market that fails the completeness gate at publish time leaves the
    submission entry alone and adds nothing of its own.

    Sixty days on, the close time this market was submitted with has passed, so
    publishing it would put an already-closed market in front of traders.
    """
    from datetime import timedelta as _timedelta  # noqa: PLC0415

    actor = _actor()
    market, _, _ = await _save(session, actor, status="submitted")

    with pytest.raises(DraftIncomplete):
        await market_service.publish(
            session, actor, market.id, now=datetime.now(UTC) + _timedelta(days=60)
        )

    await session.rollback()
    entries = await _entries(audit_reader, actor)
    assert [e["action_type"] for e in entries] == [AdminAction.MARKET_SUBMITTED.value]


async def test_publishing_someone_elses_market_records_nothing(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """The 404 must not be a refusal that also wrote an entry naming the
    administrator who tried."""
    from core.errors import MarketNotFound  # noqa: PLC0415

    owner, intruder = _actor(), _actor()
    market, _, _ = await _save(session, owner, status="submitted")

    with pytest.raises(MarketNotFound):
        await market_service.publish(session, intruder, market.id)

    await session.rollback()
    assert await _entries(audit_reader, intruder) == []


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
