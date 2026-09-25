"""`posting.post` refuses to replay into a dirty session. [T-2] #22

**Its own file, because `service/posting.py` is Ernest's** and this guard
lands in its own commit. Nothing else in [T-2] #22 changes that module, and
nothing in this file is about the trade path — it is about one branch of the
write primitive and the two callers that already reach it.

**What the guard is, and what it is not.** `post`'s replay branch calls
`session.commit()`, and that commit flushes the whole session — so a caller
holding unwritten work has it committed alongside a transaction that wrote no
entries of its own. On the trade path that is a duplicate's `q`,
`state_version` and position landing against a payment that was made once.
The rule that prevents it is the caller's ("A caller holding pending writes
must establish under its own lock that the idempotency key is absent"); this
is the alarm that fires when a caller gets it wrong.

It cannot be a *net*. By the time `post` reaches this point the caller's
writes are already pending and there is no way to discard only those. So it
raises before the damage instead of rolling back after it, which turns "a
caller got the ordering wrong" from shares granted twice into a refused
request and a traceback.

**The two existing callers must not change behaviour**, and both halves of
that are driven below rather than reasoned about: `grants.ensure_granted`
flushes inside `accounts.ensure`'s SAVEPOINT, so the account is persistent
rather than pending by the time `post` is called, and `books.ensure_open` on
a lost first-touch race has its book and outcomes expunged by the savepoint
rollback. A warm second touch never reaches `post` at all.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
        transport=upstream.transport,
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
    """The behaviour that must not change, asserted first.

    Every caller this service has today reaches the replay branch with
    nothing pending, so the guard has to be invisible to all of them. A check
    written at the top of `post` rather than on the replay branch would
    forbid the shape "The book's writes share `posting.post`'s commit, and
    nothing may follow it" depends on, which is every caller there is.
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


async def test_a_replay_with_a_pending_update_is_refused(
    session: AsyncSession,
) -> None:
    """`session.dirty`, which is the trade path's own shape.

    The trade's writes are attribute assignments on loaded rows — `q` on a
    `MarketOutcome`, `state_version` on a `MarketBook` — and they are still
    in `session.dirty` when `post` runs, because the session is built with
    `autoflush=False` and `accounts.lock` issues only SELECTs. That is the
    case this guard exists for, so it is driven with exactly that write
    rather than with a convenient one.
    """
    user, platform = await _seed_with_a_dirty_outcome(session)

    with pytest.raises(errors().PendingWritesOnReplay):
        await _replay(session, user, platform)


async def test_a_replay_found_by_the_insert_race_is_refused_too(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`post`'s other replay: the lookup under the lock misses, the INSERT
    hits the unique index, and the second lookup finds the key.

    Driven by hiding the first lookup, because two real sessions only get
    here when their legs share no account, and the trade path's book lock
    keeps a trade from ever doing that today. The pending write is flushed
    inside the SAVEPOINT and expired by its rollback, so a guard that asked
    the session at this branch would find it clean — which is why `post`
    records whether its caller had pending work before it does anything.
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
    """The guard raises *before* the commit, which is the whole of its value.

    Checked from a session of its own, because the caller's session is the
    one whose pending writes are in question and asking it what the database
    holds would be asking about the rollback rather than about the commit.
    """
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


async def test_it_is_a_ledger_error_at_500(session: AsyncSession) -> None:
    """500 rather than 409, and a `LedgerError` rather than a bare exception.

    Nothing the client sent is wrong and nothing it can do differently helps:
    this is the service reporting a bug in itself. A `LedgerError` keeps the
    response inside the one error envelope `controller/errors.py` exists to
    preserve, so a client parsing four services' errors with one shape still
    can.
    """
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
    """`grants.ensure_granted`'s session state at the moment it calls `post`.

    `accounts.ensure` flushes inside its own SAVEPOINT, so the account is
    persistent rather than pending and the function adds nothing else. The
    early return means the ordinary second read never reaches `post` at all;
    the branch is reached only by a racing first read, so the body is
    reproduced here without that early return — which is that race's session
    state exactly, and the only way to drive the branch deterministically.
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
    """`books.ensure_open`'s losing caller, which is the other way the replay
    branch is reached today.

    The loser added its book and outcomes inside `session.begin_nested()`, the
    `IntegrityError` rolled that SAVEPOINT back, and the rollback expunges
    them — so by the time it calls `post` with the winner's
    `market-open:<market_id>` key, its session is clean. If that were not
    true, this race would start raising `PendingWritesOnReplay` at every
    market's first touch, which is the loudest possible regression and the
    reason it is driven rather than argued.

    Barrier-synchronised per ADR 0015: without it the first caller can be
    finished before the second opens a connection, and nobody loses.
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
                    transport=upstream.transport,
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
    """The third sentence of the criterion, and the cheapest one to hold.

    `ensure_open` returns an existing book before it does anything else, so
    the warm path cannot be affected by a guard on `post` at all. Asserted by
    making `post` fail loudly if it is called, which is a stronger statement
    than counting transactions: a `post` that was reached and replayed would
    leave the count unchanged and go unnoticed.
    """
    upstream = Upstream()
    await books().ensure_open(
        session,
        upstream.market_id,
        access_token=token(),
        transport=upstream.transport,
    )

    async def explode(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("a warm second touch called posting.post")

    monkeypatch.setattr(posting(), "post", explode)

    book = await books().ensure_open(
        session,
        upstream.market_id,
        access_token=token(),
        transport=upstream.transport,
    )

    assert book.market_id == upstream.market_id
