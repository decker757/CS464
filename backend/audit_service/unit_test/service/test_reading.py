"""Reading the log, driven through the service layer without HTTP. [4.3] #15

The filtering and ordering rules live here. Status codes and query-string
parsing are the controller's, and are tested there.
"""

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
    """[4.3] #15's third criterion. Also the reason every test here scopes by
    actor: the table still holds every row this suite has ever written."""
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
    """`market.submitted` and `market.submitted.reverted` are different actions.

    A prefix match would silently fold a future action type into an existing
    filter, and an audit reader would never know the difference.
    """
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
    """Two services can append in the same microsecond, and two rows written in
    one transaction usually do. Without a tiebreak the order is whatever
    Postgres feels like, and a cursor cannot survive that."""
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
    """`limit + 1` is fetched and the extra dropped, so this is exact rather
    than a guess that costs a COUNT over a table that only grows."""
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
    """The whole argument for keyset over OFFSET.

    An audit log only grows at the end it is read from. With OFFSET, four rows
    appended between page one and page two push the window down by four, so the
    reader sees rows they have already seen and silently skips the ones behind
    them. A cursor names the last row rather than a count, so it cannot move.
    """
    old = datetime.now(UTC) - timedelta(hours=1)
    rows = await seed(actor_id, 6, first_at=old)

    first = await audit_service.list_actions(
        session, filters=ActionFilter(actor_id=actor_id), limit=3
    )

    # Four more actions happen while the administrator reads page one. They are
    # newer, so under OFFSET they would occupy page one and push everything down.
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
    """Rejected rather than ignored. Restarting from the newest page would
    leave a client paging forever without ever noticing."""
    with pytest.raises(MalformedCursor):
        await audit_service.list_actions(session, limit=10, cursor="not-a-cursor")
