"""`ledger.leaderboard_generations` and `leaderboard_entries`, as the database builds them. [L-1] #38

DECISIONS.md, "The leaderboard is stored as generations, and a refresh swaps
the whole ranking in one commit". Asserted against Postgres, for
`test_book_schema.py`'s reason. Each refusal is pinned to the constraint that
made it, so a refusal from some other rule does not pass for this one. Column
types, nullability and every constraint's definition are held by the catalog
comparison in `test_migrations.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session_factory

NET_WORTH = Decimal("1000.0000")


def _entities():
    """`model/entities.py`, reached lazily, so a missing name fails one test
    rather than collection (D-007)."""
    from model import entities  # noqa: PLC0415

    return entities


async def _generation(session: AsyncSession, *, ranked_users: int = 0):
    generation = _entities().LeaderboardGeneration(
        computed_at=datetime.now(UTC), ranked_users=ranked_users
    )
    session.add(generation)
    await session.flush()
    return generation


def _entry(generation_id: int, *, rank: int, user_id: uuid.UUID | None = None):
    return _entities().LeaderboardEntry(
        generation_id=generation_id,
        rank=rank,
        user_id=user_id or uuid.uuid4(),
        username=f"trader-{rank}",
        net_worth=NET_WORTH,
    )


def _refusing_constraint(refusal: pytest.ExceptionInfo[IntegrityError]) -> str | None:
    """The constraint Postgres named when it refused the write."""
    # SQLAlchemy's asyncpg adapter raises its own error from asyncpg's, and
    # only asyncpg's carries the constraint name.
    return refusal.value.orig.__cause__.constraint_name


async def _entries_in(session: AsyncSession, generation_id: int) -> int:
    entry = _entities().LeaderboardEntry
    return (
        await session.execute(
            select(func.count()).select_from(entry).where(entry.generation_id == generation_id)
        )
    ).scalar_one()


@pytest.mark.parametrize(
    ("rank", "accepted"),
    [(1, True), (0, False)],
    ids=["rank_1_accepted", "rank_0_refused"],
)
async def test_an_entry_rank_is_accepted_from_one_and_refused_below(
    session: AsyncSession, rank: int, accepted: bool
) -> None:
    """Ranks start at 1. Red for `rank > 1` (the first case) and for
    `rank >= 0` (the second) alike."""
    generation = await _generation(session)

    session.add(_entry(generation.id, rank=rank))

    if accepted:
        await session.flush()
        assert await _entries_in(session, generation.id) == 1
        return
    with pytest.raises(IntegrityError) as refusal:
        await session.flush()
    assert _refusing_constraint(refusal) == "ck_leaderboard_entries_rank_positive"
    await session.rollback()


async def test_the_same_rank_twice_in_one_generation_is_refused(
    session: AsyncSession,
) -> None:
    """Two different users, so only the `(generation_id, rank)` primary key
    can refuse it, not the unique on the user."""
    generation = await _generation(session)
    session.add(_entry(generation.id, rank=1))
    await session.flush()

    session.add(_entry(generation.id, rank=1))

    with pytest.raises(IntegrityError) as refusal:
        await session.flush()

    assert _refusing_constraint(refusal) == "leaderboard_entries_pkey"
    await session.rollback()


async def test_the_same_rank_in_another_generation_is_accepted(
    session: AsyncSession,
) -> None:
    """Every generation has a rank 1. Red if the key is `rank` alone."""
    first = await _generation(session)
    second = await _generation(session)

    session.add_all([_entry(first.id, rank=1), _entry(second.id, rank=1)])
    await session.flush()

    assert await _entries_in(session, first.id) == 1
    assert await _entries_in(session, second.id) == 1


async def test_the_same_user_twice_in_one_generation_is_refused(
    session: AsyncSession,
) -> None:
    """Different ranks, so only the unique on `(generation_id, user_id)` can
    refuse it. [L-2]'s own-row lookup reads one row per user through it."""
    generation = await _generation(session)
    user_id = uuid.uuid4()
    session.add(_entry(generation.id, rank=1, user_id=user_id))
    await session.flush()

    session.add(_entry(generation.id, rank=2, user_id=user_id))

    with pytest.raises(IntegrityError) as refusal:
        await session.flush()

    assert _refusing_constraint(refusal) == "uq_leaderboard_entries_generation_user"
    await session.rollback()


async def test_the_same_user_in_another_generation_is_accepted(
    session: AsyncSession,
) -> None:
    """Every refresh ranks the same users again, beside the previous
    generation it keeps. Red if the unique is on `user_id` alone."""
    first = await _generation(session)
    second = await _generation(session)
    user_id = uuid.uuid4()

    session.add_all(
        [
            _entry(first.id, rank=1, user_id=user_id),
            _entry(second.id, rank=1, user_id=user_id),
        ]
    )
    await session.flush()

    assert await _entries_in(session, first.id) == 1
    assert await _entries_in(session, second.id) == 1


async def test_deleting_a_generation_deletes_its_entries(session: AsyncSession) -> None:
    """The refresh's prune deletes generations only, and the foreign key's
    ON DELETE CASCADE takes their entries. A SQL delete, so a cascade only on
    an ORM relationship cannot stand in for it; counted from a second session,
    so only what committed is seen. The survivor is red for a cascade that
    deletes too much."""
    pruned = await _generation(session, ranked_users=2)
    kept = await _generation(session, ranked_users=1)
    pruned_id, kept_id = pruned.id, kept.id
    session.add_all(
        [_entry(pruned_id, rank=1), _entry(pruned_id, rank=2), _entry(kept_id, rank=1)]
    )
    await session.commit()

    generation = _entities().LeaderboardGeneration
    await session.execute(delete(generation).where(generation.id == pruned_id))
    await session.commit()

    async with get_session_factory()() as reader:
        assert await _entries_in(reader, pruned_id) == 0
        assert await _entries_in(reader, kept_id) == 1
