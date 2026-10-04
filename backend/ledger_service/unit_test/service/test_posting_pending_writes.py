"""`posting.post` refuses to replay into a dirty session. [T-2] #22

The alarm for a caller that breaks the rule in DECISIONS.md, "A caller holding
pending writes must establish under its own lock that the idempotency key is
absent"; the guard itself is "`posting.post` refuses to replay into a dirty
session". The existing callers must not change behaviour, which is driven
below rather than argued.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from unit_test.conftest import terms_client_over
from unit_test.trade_fixtures import (
    TIMEOUT,
    Upstream,
    accounts_module,
    assert_ledger_balances,
    books,
    entities,
    errors,
    grants,
    posting,
    session_factory,
    token,
    transaction_count,
)

_AMOUNT = Decimal("137.4242")
_KEY = "pending-writes:one"


async def _seed(session: AsyncSession):
    """One committed transaction, and the two accounts it moved credits
    between, so the calls below land on `post`'s replay branch rather than
    writing a new transaction."""
    ents = entities()
    accounts = accounts_module()
    user = await accounts.ensure(session, ents.AccountKind.USER, uuid.uuid4())
    platform = await accounts.ensure_platform(session)

    await posting().post(
        session,
        idempotency_key=_KEY,
        kind=ents.TransactionKind.SIGNUP_GRANT,
        legs=[
            posting().Leg(account=platform, amount=-_AMOUNT),
            posting().Leg(account=user, amount=_AMOUNT),
        ],
    )
    return user, platform


async def _seed_with_a_dirty_outcome(session: AsyncSession):
    """`_seed`, plus the trade path's own pending write: `q` assigned on a
    loaded `MarketOutcome`, left in `session.dirty` by `autoflush=False`.

    The book is opened before `_seed` runs, not after: `ensure_open` rolls
    back on a cold market, which would expire the accounts `_seed` returns.
    """
    upstream = Upstream()
    await books().ensure_open(
        session,
        upstream.market_id,
        access_token=token(),
        terms_client=terms_client_over(upstream.transport),
    )
    user, platform = await _seed(session)
    outcome = (
        await session.execute(
            select(entities().MarketOutcome).where(
                entities().MarketOutcome.market_id == upstream.market_id,
                entities().MarketOutcome.position == 0,
            )
        )
    ).scalar_one()
    outcome.q = Decimal("41.0000")
    assert session.dirty
    return user, platform


async def _replay(session: AsyncSession, user, platform):
    """The identical movement again, under the same key."""
    return await posting().post(
        session,
        idempotency_key=_KEY,
        kind=entities().TransactionKind.SIGNUP_GRANT,
        legs=[
            posting().Leg(account=platform, amount=-_AMOUNT),
            posting().Leg(account=user, amount=_AMOUNT),
        ],
    )


# =========================================================================
# The guard
# =========================================================================
async def test_a_clean_session_still_replays(session: AsyncSession) -> None:
    """The behaviour that must not change: a replay with nothing pending.
    A check at the top of `post` would forbid every caller's shape (D-032).
    """
    user, platform = await _seed(session)
    assert not (session.new or session.dirty or session.deleted)

    replayed = await _replay(session, user, platform)

    assert replayed.idempotency_key == _KEY
    assert await transaction_count(session, key=_KEY) == 1


async def test_a_replay_with_a_pending_insert_is_refused(
    session: AsyncSession,
) -> None:
    """`session.new` is non-empty, so this caller has work `post`'s commit
    would land for it — against a transaction that wrote no entries."""
    user, platform = await _seed(session)

    ents = entities()
    stowaway = ents.Account(kind=ents.AccountKind.USER, owner_id=uuid.uuid4())
    session.add(stowaway)
    assert session.new

    with pytest.raises(errors().PendingWritesOnReplay):
        await _replay(session, user, platform)


async def test_a_replay_with_a_pending_delete_is_refused(
    session: AsyncSession,
) -> None:
    """`session.deleted` counts as well: the replay's commit would carry out a
    delete the caller had not finished deciding on."""
    user, platform = await _seed(session)

    ents = entities()
    spare = ents.Account(kind=ents.AccountKind.USER, owner_id=uuid.uuid4())
    session.add(spare)
    await session.commit()
    await session.delete(spare)
    assert session.deleted
    assert not (session.new or session.dirty)

    with pytest.raises(errors().PendingWritesOnReplay):
        await _replay(session, user, platform)


async def test_a_replay_with_a_pending_update_is_refused(
    session: AsyncSession,
) -> None:
    """`session.dirty`, the trade path's own shape: attribute assignments on
    loaded rows, unflushed because of `autoflush=False`. Driven with exactly
    that write.
    """
    user, platform = await _seed_with_a_dirty_outcome(session)

    with pytest.raises(errors().PendingWritesOnReplay):
        await _replay(session, user, platform)


async def test_a_replay_found_by_the_insert_race_is_refused_too(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`post`'s other replay: the lookup under the lock misses, the INSERT
    hits the unique index, and the second lookup finds the key.

    Driven by hiding the first lookup; real sessions reach it only when their
    legs share no account. The rollback expires the pending write, which is
    why `post` records it on entry.
    """
    user, platform = await _seed_with_a_dirty_outcome(session)

    real = posting().find_by_idempotency_key
    lookups = 0

    async def miss_the_first(own: AsyncSession, key: str):
        nonlocal lookups
        lookups += 1
        return None if lookups == 1 else await real(own, key)

    monkeypatch.setattr(posting(), "find_by_idempotency_key", miss_the_first)

    with pytest.raises(errors().PendingWritesOnReplay):
        await _replay(session, user, platform)
    assert lookups == 2, "the replay was not reached through the INSERT race"


async def test_the_refusal_commits_nothing(session: AsyncSession) -> None:
    """The guard raises before the commit. Checked from a session of its own,
    since the caller's would report its own rollback."""
    user, platform = await _seed(session)

    ents = entities()
    orphan_owner = uuid.uuid4()
    session.add(ents.Account(kind=ents.AccountKind.USER, owner_id=orphan_owner))

    with pytest.raises(errors().PendingWritesOnReplay):
        await _replay(session, user, platform)
    await session.rollback()

    async with session_factory()() as other:
        found = await accounts_module().find(
            other, ents.AccountKind.USER, orphan_owner
        )
        assert found is None, "the pending write was committed by the replay"
        assert await transaction_count(other, key=_KEY) == 1
        await assert_ledger_balances(other)


async def test_a_replay_after_the_caller_flushed_is_refused_and_commits_nothing(
    session: AsyncSession,
) -> None:
    """A write already flushed is out of `new`, `dirty` and `deleted`, and
    `post`'s replay commit would still land it. `accounts.ensure` flushes
    inside a SAVEPOINT it releases, the shape any caller of it has.
    """
    user, platform = await _seed(session)

    ents = entities()
    flushed_owner = uuid.uuid4()
    await accounts_module().ensure(session, ents.AccountKind.USER, flushed_owner)
    assert not (session.new or session.dirty or session.deleted), (
        "the flushed account is still pending, so this is not the flushed case"
    )

    with pytest.raises(errors().PendingWritesOnReplay):
        await _replay(session, user, platform)
    await session.rollback()

    async with session_factory()() as other:
        found = await accounts_module().find(
            other, ents.AccountKind.USER, flushed_owner
        )
        assert found is None, "the flushed write was committed by the replay"
        assert await transaction_count(other, key=_KEY) == 1


async def test_a_rolled_back_flush_no_longer_counts(session: AsyncSession) -> None:
    """A flush counts only until its transaction ends: once rolled back there
    is nothing left for a replay's commit to land.
    """
    from core.database import has_pending_writes  # noqa: PLC0415

    ents = entities()
    session.add(ents.Account(kind=ents.AccountKind.USER, owner_id=uuid.uuid4()))
    await session.flush()
    assert has_pending_writes(session)

    await session.rollback()

    assert not has_pending_writes(session)


async def test_it_is_a_ledger_error_at_500(session: AsyncSession) -> None:
    """500, not 409: the service reporting a bug in itself. A `LedgerError`,
    so the response keeps the one error envelope."""
    exc = errors().PendingWritesOnReplay

    assert issubclass(exc, errors().LedgerError)
    assert exc.status_code == 500
    assert exc.code == "pending_writes_on_replay"


# =========================================================================
# Neither existing caller changes behaviour
# =========================================================================
async def test_the_signup_grant_reaches_the_replay_branch_clean(
    session: AsyncSession,
) -> None:
    """`grants.ensure_granted`'s session state when it calls `post`.

    Only a racing first read reaches the replay branch, so the body is
    reproduced without the early return: that race's session state exactly.
    """
    from core.config import get_settings  # noqa: PLC0415

    user_id = uuid.uuid4()
    await grants().ensure_granted(session, user_id)
    await session.commit()

    ents = entities()
    accounts = accounts_module()
    user = await accounts.ensure(session, ents.AccountKind.USER, user_id)
    platform = await accounts.ensure_platform(session)
    amount = get_settings().starting_credits

    assert not (session.new or session.dirty or session.deleted), (
        "ensure_granted would reach post with pending writes, which the "
        "guard now refuses — this caller's behaviour has changed"
    )

    replayed = await posting().post(
        session,
        idempotency_key=grants().grant_key(user_id),
        kind=ents.TransactionKind.SIGNUP_GRANT,
        legs=[
            posting().Leg(account=platform, amount=-amount),
            posting().Leg(account=user, amount=amount),
        ],
        context={"user_id": str(user_id)},
    )

    assert replayed.idempotency_key == grants().grant_key(user_id)
    assert await transaction_count(session, key=grants().grant_key(user_id)) == 1


async def test_a_lost_first_touch_race_still_opens_one_book(
    session: AsyncSession,
) -> None:
    """`books.ensure_open`'s losing caller, the other way the replay branch is
    reached. The savepoint rollback expunges its book and outcomes, so it
    reaches `post` clean.

    Barrier-synchronised per ADR 0015, or nobody loses.
    """
    upstream = Upstream()
    start = asyncio.Barrier(4)
    factory = session_factory()

    async def attempt():
        async with factory() as own:
            await own.connection()
            await start.wait()
            try:
                await books().ensure_open(
                    own,
                    upstream.market_id,
                    access_token=token(),
                    terms_client=terms_client_over(upstream.transport),
                )
            except Exception as exc:  # noqa: BLE001 - the assertion is below
                return exc
            return None

    async with asyncio.timeout(TIMEOUT):
        results = await asyncio.gather(*(attempt() for _ in range(4)))

    assert all(r is None for r in results), results
    assert (
        await transaction_count(
            session, key=books().market_open_key(upstream.market_id)
        )
        == 1
    )
    await assert_ledger_balances(session)


async def test_a_warm_second_touch_never_reaches_post(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A warm touch returns before `post`. Asserted by making `post` fail if
    called: a replay would leave a transaction count unchanged.
    """
    upstream = Upstream()
    await books().ensure_open(
        session,
        upstream.market_id,
        access_token=token(),
        terms_client=terms_client_over(upstream.transport),
    )

    async def explode(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("a warm second touch called posting.post")

    monkeypatch.setattr(posting(), "post", explode)

    book = await books().ensure_open(
        session,
        upstream.market_id,
        access_token=token(),
        terms_client=terms_client_over(upstream.transport),
    )

    assert book.market_id == upstream.market_id
