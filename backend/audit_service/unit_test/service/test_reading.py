"""Reading the log through the service layer, without HTTP. [4.3] #15"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import MalformedCursor
from service import audit_service
from service.audit_service import ActionFilter


async def test_it_returns_the_actors_entries(
    session: AsyncSession, actor_id: uuid.UUID, seed
) -> None:
    await seed(actor_id, 3)

    page = await audit_service.list_actions(
        session, filters=ActionFilter(actor_id=actor_id), limit=10
    )

    assert len(page.actions) == 3
    assert {a.actor_id for a in page.actions} == {actor_id}


async def test_it_excludes_other_actors(
    session: AsyncSession, actor_id: uuid.UUID, seed
) -> None:
    """[4.3] #15's third criterion."""
    other = uuid.uuid4()
    await seed(actor_id, 2)
    await seed(other, 5)

    page = await audit_service.list_actions(
        session, filters=ActionFilter(actor_id=actor_id), limit=10
    )

    assert len(page.actions) == 2


async def test_it_filters_by_action_type(
    session: AsyncSession, actor_id: uuid.UUID, seed
) -> None:
    await seed(actor_id, 2, action_type="market.submitted")
    await seed(actor_id, 3, action_type="user.suspended")

    page = await audit_service.list_actions(
        session,
        filters=ActionFilter(actor_id=actor_id, action_type="user.suspended"),
        limit=10,
    )

    assert len(page.actions) == 3
    assert {a.action_type for a in page.actions} == {"user.suspended"}


async def test_action_type_matches_exactly_rather_than_by_prefix(
    session: AsyncSession, actor_id: uuid.UUID, seed
) -> None:
    """`market.submitted` and `market.submitted.reverted` are different actions."""
    await seed(actor_id, 1, action_type="market.submitted")
    await seed(actor_id, 1, action_type="market.submitted.reverted")

    page = await audit_service.list_actions(
        session,
        filters=ActionFilter(actor_id=actor_id, action_type="market.submitted"),
        limit=10,
    )

    assert len(page.actions) == 1
    assert page.actions[0].action_type == "market.submitted"


async def test_both_filters_apply_together(
    session: AsyncSession, actor_id: uuid.UUID, seed
) -> None:
    other = uuid.uuid4()
    await seed(actor_id, 2, action_type="market.submitted")
    await seed(actor_id, 1, action_type="user.suspended")
    await seed(other, 4, action_type="market.submitted")

    page = await audit_service.list_actions(
        session,
        filters=ActionFilter(actor_id=actor_id, action_type="market.submitted"),
        limit=10,
    )

    assert len(page.actions) == 2


async def test_no_filter_returns_the_whole_feed(
    session: AsyncSession, actor_id: uuid.UUID, seed
) -> None:
    """Unfiltered still means bounded. The log only grows."""
    await seed(actor_id, 3)

    page = await audit_service.list_actions(session, limit=2)

    assert len(page.actions) == 2


# --- ordering -------------------------------------------------------------
async def test_entries_come_back_newest_first(
    session: AsyncSession, actor_id: uuid.UUID, seed
) -> None:
    rows = await seed(actor_id, 4)

    page = await audit_service.list_actions(
        session, filters=ActionFilter(actor_id=actor_id), limit=10
    )

    assert [a.id for a in page.actions] == [r["id"] for r in rows]


async def test_ties_on_the_timestamp_are_broken_by_id(
    session: AsyncSession, actor_id: uuid.UUID, seed
) -> None:
    """Rows in one transaction share a timestamp; without a tiebreak the order
    is unstable and a cursor cannot survive it."""
    same_instant = datetime.now(UTC)
    for _ in range(5):
        await seed(actor_id, 1, first_at=same_instant)

    first = await audit_service.list_actions(
        session, filters=ActionFilter(actor_id=actor_id), limit=10
    )
    second = await audit_service.list_actions(
        session, filters=ActionFilter(actor_id=actor_id), limit=10
    )

    assert [a.id for a in first.actions] == [a.id for a in second.actions]
    assert [a.id for a in first.actions] == sorted(
        (a.id for a in first.actions), reverse=True
    )


# --- paging ---------------------------------------------------------------
async def test_a_full_page_offers_a_cursor(
    session: AsyncSession, actor_id: uuid.UUID, seed
) -> None:
    await seed(actor_id, 5)

    page = await audit_service.list_actions(
        session, filters=ActionFilter(actor_id=actor_id), limit=2
    )

    assert len(page.actions) == 2
    assert page.has_more is True
    assert page.next_cursor is not None


async def test_the_last_page_offers_none(
    session: AsyncSession, actor_id: uuid.UUID, seed
) -> None:
    """Exact when the page is exactly full, thanks to the `limit + 1` fetch."""
    await seed(actor_id, 3)

    page = await audit_service.list_actions(
        session, filters=ActionFilter(actor_id=actor_id), limit=3
    )

    assert len(page.actions) == 3
    assert page.has_more is False
    assert page.next_cursor is None


async def test_paging_walks_the_whole_log_without_repeating(
    session: AsyncSession, actor_id: uuid.UUID, seed
) -> None:
    rows = await seed(actor_id, 7)

    seen: list = []
    cursor = None
    for _ in range(10):
        page = await audit_service.list_actions(
            session, filters=ActionFilter(actor_id=actor_id), limit=3, cursor=cursor
        )
        seen.extend(a.id for a in page.actions)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert seen == [r["id"] for r in rows]
    assert len(seen) == len(set(seen))


async def test_a_cursor_carries_the_filter_over(
    session: AsyncSession, actor_id: uuid.UUID, seed
) -> None:
    other = uuid.uuid4()
    await seed(actor_id, 4)
    await seed(other, 4)

    first = await audit_service.list_actions(
        session, filters=ActionFilter(actor_id=actor_id), limit=2
    )
    second = await audit_service.list_actions(
        session,
        filters=ActionFilter(actor_id=actor_id),
        limit=2,
        cursor=first.next_cursor,
    )

    assert {a.actor_id for a in second.actions} == {actor_id}


async def test_entries_appended_mid_read_do_not_shift_the_pages(
    session: AsyncSession, actor_id: uuid.UUID, seed
) -> None:
    """The whole argument for keyset over OFFSET: new rows cannot shift a cursor."""
    old = datetime.now(UTC) - timedelta(hours=1)
    rows = await seed(actor_id, 6, first_at=old)

    first = await audit_service.list_actions(
        session, filters=ActionFilter(actor_id=actor_id), limit=3
    )

    # Newer rows arrive between pages; under OFFSET they would shift page two.
    await seed(actor_id, 4)

    second = await audit_service.list_actions(
        session,
        filters=ActionFilter(actor_id=actor_id),
        limit=3,
        cursor=first.next_cursor,
    )

    assert [a.id for a in first.actions] == [r["id"] for r in rows[:3]]
    assert [a.id for a in second.actions] == [r["id"] for r in rows[3:6]]


async def test_a_cursor_this_service_did_not_issue_is_refused(
    session: AsyncSession,
) -> None:
    """Rejected rather than restarting from the newest page."""
    with pytest.raises(MalformedCursor):
        await audit_service.list_actions(session, limit=10, cursor="not-a-cursor")
