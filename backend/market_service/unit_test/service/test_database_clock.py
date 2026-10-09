"""Every decision a market records is stamped with Postgres's clock. #179.

The sweep stamps `closed_at` with Postgres's clock, so the decisions must too,
or a container clock running behind the database's stamps a proposal before
the close it follows. Each test puts the container's clock an hour behind just
before the action, so a stamp read from Python lands an hour early and fails.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from service import market_service
from unit_test.conftest import actor as _actor
from unit_test.conftest import (
    approval_request,
    close_request,
    closed_market,
    database_now,
    draft_request,
    proposal_request,
    proposed_market,
    published_market,
    rejection_request,
)

# Read back as audit_svc: market_svc holds INSERT on this table and no SELECT.
_REJECTION_ENTRY = text(
    "SELECT occurred_at FROM audit.admin_actions "
    "WHERE actor_id = :actor AND action_type = 'market.outcome_rejected'"
)


class _AnHourBehind(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return datetime.now(tz) - timedelta(hours=1)


def _put_the_container_clock_an_hour_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    # raising=False: once the module reads only Postgres's clock it may stop
    # importing `datetime`, and the test should still hold.
    monkeypatch.setattr(market_service, "datetime", _AnHourBehind, raising=False)


async def test_a_proposal_right_after_the_sweep_is_stamped_after_the_close(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#179's criterion: `proposed_at >= closed_at` whatever the container says."""
    actor = _actor()
    market = await closed_market(session, actor)
    _put_the_container_clock_an_hour_behind(monkeypatch)

    proposed = await market_service.propose_outcome(
        session, actor, market.id, proposal_request(market.outcomes[0].id)
    )

    assert proposed.proposed_at >= proposed.closed_at


async def test_a_submission_is_stamped_by_the_database(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = await database_now(session)
    _put_the_container_clock_an_hour_behind(monkeypatch)

    market, _, _ = await market_service.save(
        session, _actor(), draft_request(status="submitted")
    )

    assert market.submitted_at >= before


async def test_a_publication_is_stamped_by_the_database(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = _actor()
    market, _, _ = await market_service.save(
        session, actor, draft_request(status="submitted")
    )
    before = await database_now(session)
    _put_the_container_clock_an_hour_behind(monkeypatch)

    published = await market_service.publish(session, actor, market.id)

    assert published.published_at >= before


async def test_an_early_close_is_stamped_by_the_database(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = _actor()
    market = await published_market(session, actor)
    before = await database_now(session)
    _put_the_container_clock_an_hour_behind(monkeypatch)

    closed = await market_service.close_early(session, actor, market.id, close_request())

    assert closed.closed_at >= before


async def test_an_approval_is_stamped_by_the_database(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    market = await proposed_market(session, _actor())
    before = await database_now(session)
    _put_the_container_clock_an_hour_behind(monkeypatch)

    approved = await market_service.approve_outcome(
        session, _actor(username="second_admin"), market.id,
        approval_request(market.proposal_id),
    )

    assert approved.approved_at >= before


async def test_a_rejection_is_logged_at_the_database_time(
    session: AsyncSession, audit_reader: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejection clears its stamps, so the audit entry's is the only one."""
    market = await proposed_market(session, _actor())
    rejecter = _actor(username="second_admin")
    before = await database_now(session)
    _put_the_container_clock_an_hour_behind(monkeypatch)

    await market_service.reject_outcome(
        session, rejecter, market.id, rejection_request(market.proposal_id)
    )

    occurred_at = (
        await audit_reader.execute(_REJECTION_ENTRY, {"actor": rejecter.id})
    ).scalar_one()
    assert occurred_at >= before
