"""Naming users for another service, without HTTP. [L-1] #38, #306

Rows are inserted directly; nothing logs in.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from model.entities import User
from service import user_directory

_LOOKUP_LIMIT = 500


async def _make(
    session: AsyncSession, username: str, *, suspended: bool = False
) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash="not-a-real-hash",
        is_suspended=suspended,
    )
    session.add(user)
    await session.commit()
    return user


async def test_known_ids_are_answered_with_their_username_and_suspension(
    session: AsyncSession,
) -> None:
    active = await _make(session, "michelle_l")
    suspended = await _make(session, "bob_1", suspended=True)

    found = await user_directory.list_by_ids(session, [active.id, suspended.id])

    assert {(user.id, user.username, user.is_suspended) for user in found} == {
        (active.id, "michelle_l", False),
        (suspended.id, "bob_1", True),
    }


async def test_an_id_naming_no_user_is_left_out_rather_than_refused(
    session: AsyncSession,
) -> None:
    known = await _make(session, "michelle_l")

    found = await user_directory.list_by_ids(session, [uuid.uuid4(), known.id])

    assert [user.id for user in found] == [known.id]


async def test_a_duplicate_id_is_answered_once(session: AsyncSession) -> None:
    known = await _make(session, "michelle_l")

    found = await user_directory.list_by_ids(session, [known.id, known.id])

    assert len(found) == 1


async def test_rows_come_back_ordered_by_id(session: AsyncSession) -> None:
    users = [await _make(session, f"user_{n}") for n in range(5)]

    found = await user_directory.list_by_ids(
        session, [user.id for user in reversed(users)]
    )

    assert [user.id for user in found] == sorted(user.id for user in users)


async def test_the_most_ids_a_request_may_carry_are_all_answered(
    session: AsyncSession,
) -> None:
    known = await _make(session, "michelle_l")
    strangers = [uuid.uuid4() for _ in range(_LOOKUP_LIMIT - 1)]

    found = await user_directory.list_by_ids(session, [known.id, *strangers])

    assert [user.id for user in found] == [known.id]
