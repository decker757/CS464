"""`posting.post_all`: a batch of movements, one commit. [3.4] #12

The spec is DECISIONS.md's 2026-10-09 entries from "`posting.post_all` takes
a list of `Movement`s, commits once, and keeps `post`'s SAVEPOINT" to "`post`
is not `post_all` of one movement", and ADR 0019's amendment of the same date.

Every "nothing was written" is counted from a second session, because the
caller's own session would report its own state. An in-session count is
taken before any rollback of that session, never after.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core import errors
from core.database import get_engine, get_session_factory, has_pending_writes
from model.entities import (
    Account,
    AccountKind,
    Entry,
    MarketResult,
    ResultKind,
    Transaction,
    TransactionKind,
)
from service import accounts, posting
from unit_test.market_fixtures import market_with_outcomes
from unit_test.trade_fixtures import assert_ledger_balances, capture_sql

TICK = timedelta(microseconds=1)


# --- building batches -----------------------------------------------------
def _key(label: str = "batch") -> str:
    """A key nothing else in this database has used."""
    return f"{label}:{uuid.uuid4()}"


def _leg(account: Account, amount: str):
    return posting.Leg(account=account, amount=Decimal(amount))


def _movement(
    key: str,
    legs: list,
    *,
    kind: TransactionKind = TransactionKind.SETTLEMENT,
    context: dict[str, Any] | None = None,
):
    return posting.Movement(idempotency_key=key, kind=kind, legs=legs, context=context)


def _transfer(key: str, source: Account, target: Account, amount: str, **kwargs):
    """A two-leg movement: `amount` from `source` to `target`."""
    return _movement(
        key, [_leg(source, f"-{amount}"), _leg(target, amount)], **kwargs
    )


async def _new_accounts(
    session: AsyncSession, kind: AccountKind, count: int
) -> list[Account]:
    """`count` fresh accounts of one kind, committed, so the session is clean."""
    created = [Account(kind=kind, owner_id=uuid.uuid4()) for _ in range(count)]
    session.add_all(created)
    await session.commit()
    return created


async def _platform(session: AsyncSession) -> Account:
    platform = await accounts.ensure_platform(session)
    await session.commit()
    return platform


async def _fund(
    session: AsyncSession,
    source: Account,
    target: Account,
    amount: str,
    *,
    now: datetime | None = None,
) -> Transaction:
    """One committed movement through `post`, which `post_all` must sit after."""
    return await posting.post(
        session,
        idempotency_key=_key("fund"),
        kind=TransactionKind.SIGNUP_GRANT,
        legs=[_leg(source, f"-{amount}"), _leg(target, amount)],
        now=now,
    )


def _stage_stowaway(session: AsyncSession) -> uuid.UUID:
    """A caller's own pending ORM write, the shape settlement's record has.
    Returns its owner id, so a second session can look for it."""
    owner_id = uuid.uuid4()
    session.add(Account(kind=AccountKind.USER, owner_id=owner_id))
    assert has_pending_writes(session)
    return owner_id


# --- reading back ---------------------------------------------------------
async def _entries_of(session: AsyncSession, transaction_id: uuid.UUID) -> list[Entry]:
    stmt = select(Entry).where(Entry.transaction_id == transaction_id)
    return list((await session.execute(stmt)).scalars())


async def _ledger_counts(session: AsyncSession) -> tuple[int, int]:
    """Transactions and entries in the whole ledger, as this session sees them."""
    transactions = (
        await session.execute(select(func.count()).select_from(Transaction))
    ).scalar_one()
    entries = (
        await session.execute(select(func.count()).select_from(Entry))
    ).scalar_one()
    return transactions, entries


async def _keys_written(session: AsyncSession, keys: list[str]) -> int:
    stmt = (
        select(func.count())
        .select_from(Transaction)
        .where(Transaction.idempotency_key.in_(keys))
    )
    return (await session.execute(stmt)).scalar_one()


async def _stowaway_count(session: AsyncSession, owner_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(Account)
        .where(Account.kind == AccountKind.USER, Account.owner_id == owner_id)
    )
    return (await session.execute(stmt)).scalar_one()


async def _newest_entry_at(
    session: AsyncSession, account_ids: list[uuid.UUID]
) -> datetime | None:
    stmt = select(func.max(Entry.created_at)).where(Entry.account_id.in_(account_ids))
    return (await session.execute(stmt)).scalar_one()


@contextmanager
def _capture_sql_with_parameters() -> Iterator[list[tuple[str, tuple]]]:
    """Every statement the block sends, with its parameters, in order.

    `trade_fixtures.capture_sql` keeps only the text, and the lock order is
    in the parameters. The twin is market_service's `recorded_statements`,
    which this service cannot import (`test_import_boundary.py`).
    """
    captured: list[tuple[str, tuple]] = []
    engine = get_engine().sync_engine

    def before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        captured.append((statement, tuple(parameters or ())))

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield captured
    finally:
        event.remove(engine, "before_cursor_execute", before)


def _is_lock(statement: str) -> bool:
    return "for update" in statement.lower()


def _is_select_from(statement: str, table: str) -> bool:
    lowered = statement.lstrip().lower()
    return lowered.startswith("select") and f"from ledger.{table}" in lowered


def _is_insert_into(statement: str, table: str) -> bool:
    return statement.lstrip().lower().startswith(f"insert into ledger.{table}")


# =========================================================================
# Writing
# =========================================================================
async def test_a_batch_writes_every_movement_balanced_in_input_order(
    session: AsyncSession,
) -> None:
    """Each movement is its own transaction, with its own kind and context,
    and the results pair with the movements by position.

    The keys are passed out of sorted order, so a result list sorted by key
    is caught. The second movement balances only after rounding half up:
    1.00005 and 1.00006 both store as 1.0001, where banker's rounding would
    make the first 1.0000.
    """
    platform = await _platform(session)
    (pool,) = await _new_accounts(session, AccountKind.MARKET_POOL, 1)
    first_winner, second_winner = await _new_accounts(session, AccountKind.USER, 2)
    movements = [
        _transfer(
            _key("c"), pool, first_winner, "12.3457",
            kind=TransactionKind.SETTLEMENT, context={"winner": "first"},
        ),
        _movement(
            _key("a"),
            [_leg(platform, "-1.00005"), _leg(second_winner, "1.00006")],
            kind=TransactionKind.SIGNUP_GRANT,
        ),
        _transfer(
            _key("b"), pool, platform, "7.0001",
            kind=TransactionKind.SETTLEMENT_RESIDUE, context={"residue": True},
        ),
    ]
    expected_legs = [
        {(pool.id, Decimal("-12.3457")), (first_winner.id, Decimal("12.3457"))},
        {(platform.id, Decimal("-1.0001")), (second_winner.id, Decimal("1.0001"))},
        {(pool.id, Decimal("-7.0001")), (platform.id, Decimal("7.0001"))},
    ]

    results = await posting.post_all(session, movements)

    assert [r.idempotency_key for r in results] == [
        m.idempotency_key for m in movements
    ]
    assert [r.kind for r in results] == [m.kind for m in movements]
    assert [r.context for r in results] == [m.context for m in movements]
    for result, legs in zip(results, expected_legs, strict=True):
        written = await _entries_of(session, result.id)
        assert {(e.account_id, e.amount) for e in written} == legs
    await assert_ledger_balances(session)


async def test_a_batch_that_balances_only_in_total_is_refused(
    session: AsyncSession,
) -> None:
    """Each movement balances on its own, not the batch as a whole.

    Refused by the balance check: the batch is non-empty with distinct keys,
    so `MalformedBatch` cannot answer first.
    """
    (pool,) = await _new_accounts(session, AccountKind.MARKET_POOL, 1)
    first_winner, second_winner = await _new_accounts(session, AccountKind.USER, 2)
    keys = [_key(), _key()]
    movements = [
        _movement(keys[0], [_leg(pool, "-10.0001"), _leg(first_winner, "10.0000")]),
        _movement(keys[1], [_leg(pool, "-5.1234"), _leg(second_winner, "5.1235")]),
    ]

    with pytest.raises(errors.UnbalancedTransaction):
        await posting.post_all(session, movements)

    async with get_session_factory()() as other:
        assert await _keys_written(other, keys) == 0


async def test_a_zero_leg_in_one_movement_is_refused(session: AsyncSession) -> None:
    """Three legs that sum to zero, one of them zero: only `_require_balanced`'s
    zero-leg rule refuses this. A per-movement `sum != 0` would let it through
    to `ck_entries_amount_nonzero`, a bare `IntegrityError`."""
    (pool,) = await _new_accounts(session, AccountKind.MARKET_POOL, 1)
    first_winner, second_winner = await _new_accounts(session, AccountKind.USER, 2)
    keys = [_key(), _key()]
    movements = [
        _transfer(keys[0], pool, first_winner, "2.2222"),
        _movement(
            keys[1],
            [
                _leg(pool, "-5.4321"),
                _leg(first_winner, "5.4321"),
                _leg(second_winner, "0"),
            ],
        ),
    ]

    with pytest.raises(errors.UnbalancedTransaction):
        await posting.post_all(session, movements)

    async with get_session_factory()() as other:
        assert await _keys_written(other, keys) == 0


@pytest.mark.parametrize("shape", ["empty", "repeated_key"])
async def test_an_empty_batch_or_a_repeated_key_is_malformed(
    session: AsyncSession, shape: str
) -> None:
    """`500 malformed_batch`, refused before any SQL is sent: the first check.

    The repeated key is two identical, balanced movements, so it is the
    duplicate and nothing else that refuses. Without the check it reaches the
    locks, then the unique index. The empty batch would otherwise commit the
    caller's pending write with no money moved, which is the staged row below.
    """
    platform = await _platform(session)
    (winner,) = await _new_accounts(session, AccountKind.USER, 1)
    key = _key()
    movements = (
        []
        if shape == "empty"
        else [
            _transfer(key, platform, winner, "3.1415"),
            _transfer(key, platform, winner, "3.1415"),
        ]
    )
    stowaway = _stage_stowaway(session)

    with capture_sql() as statements:
        with pytest.raises(errors.MalformedBatch) as refusal:
            await posting.post_all(session, movements)

    assert statements == []
    assert isinstance(refusal.value, errors.LedgerError)
    assert refusal.value.status_code == 500
    assert refusal.value.code == "malformed_batch"
    async with get_session_factory()() as other:
        assert await _stowaway_count(other, stowaway) == 0
        assert await _keys_written(other, [key]) == 0


# =========================================================================
# Replays and reused keys
# =========================================================================
async def _committed_batch(session: AsyncSession) -> tuple[list, list[Transaction]]:
    """Three movements from one pool, posted and committed. Returns the
    movements, to post again, and what the first post returned."""
    (pool,) = await _new_accounts(session, AccountKind.MARKET_POOL, 1)
    winners = await _new_accounts(session, AccountKind.USER, 3)
    movements = [
        _transfer(_key(), pool, winner, amount)
        for winner, amount in zip(winners, ["4.0001", "5.0002", "6.0003"], strict=True)
    ]
    originals = await posting.post_all(session, movements)
    return movements, originals


async def test_a_full_replay_returns_the_originals_and_releases_the_locks(
    session: AsyncSession,
) -> None:
    """Every key exists, every fingerprint matches, the session is clean: the
    batch replays, writes nothing, and commits to release the account locks
    (ADR 0015). The second session's NOWAIT fails if they are still held."""
    movements, originals = await _committed_batch(session)
    async with get_session_factory()() as other:
        before = await _ledger_counts(other)
    assert not has_pending_writes(session)

    replayed = await posting.post_all(session, movements)

    assert [r.id for r in replayed] == [o.id for o in originals]
    account_ids = {leg.account.id for m in movements for leg in m.legs}
    async with get_session_factory()() as other:
        assert await _ledger_counts(other) == before
        for account_id in account_ids:
            try:
                await other.execute(
                    select(Account.id)
                    .where(Account.id == account_id)
                    .with_for_update(nowait=True)
                )
            except DBAPIError:
                pytest.fail("the replay left an account lock held")
        await other.rollback()


async def test_a_full_replay_into_a_dirty_session_is_refused(
    session: AsyncSession,
) -> None:
    """The pending check refuses: non-empty, distinct keys and balanced, so no
    earlier check can. Without it the replay's commit lands the caller's row."""
    movements, _ = await _committed_batch(session)
    in_session_before = await _ledger_counts(session)
    stowaway = _stage_stowaway(session)

    with pytest.raises(errors.PendingWritesOnReplay):
        await posting.post_all(session, movements)

    assert await _ledger_counts(session) == in_session_before
    async with get_session_factory()() as other:
        assert await _stowaway_count(other, stowaway) == 0


async def test_a_partial_match_is_a_reused_key_and_writes_nothing(
    session: AsyncSession,
) -> None:
    """One key found, one missing, on a clean session: the classification
    refuses, since the pending check passes and PLATFORM cannot be overdrawn.
    Replaying the match and writing the rest would be one batch in two
    commits."""
    platform = await _platform(session)
    first_winner, second_winner = await _new_accounts(session, AccountKind.USER, 2)
    found = _transfer(_key(), platform, first_winner, "8.0008")
    await posting.post_all(session, [found])
    missing = _transfer(_key(), platform, second_winner, "9.0009")
    assert not has_pending_writes(session)

    with pytest.raises(errors.IdempotencyKeyReused):
        await posting.post_all(session, [found, missing])

    async with get_session_factory()() as other:
        assert await _keys_written(other, [missing.idempotency_key]) == 0
        assert await _keys_written(other, [found.idempotency_key]) == 1


@pytest.mark.parametrize("retry", ["a_new_winner", "a_different_residue"])
async def test_a_settlement_past_its_latch_is_refused_as_pending_writes(
    session: AsyncSession, retry: str
) -> None:
    """Settlement's own shape: its `market_results` row staged, then a batch
    whose keys already exist. Pending writes are asked about before the match
    is classified, so both a partial match and a fingerprint mismatch answer
    `pending_writes_on_replay`, never `idempotency_key_reused`.

    The market is committed before the record is staged, so the record is
    the only pending write.
    """
    market_id, outcome_ids = await market_with_outcomes(
        session, liquidity_b=Decimal("100.0000")
    )
    await session.commit()
    pool = await accounts.find(session, AccountKind.MARKET_POOL, market_id)
    platform = await _platform(session)
    await _fund(session, platform, pool, "250.0000")
    first_winner, second_winner, late_winner = await _new_accounts(
        session, AccountKind.USER, 3
    )

    def payout(winner: Account, amount: str):
        return _transfer(
            f"settlement:{market_id}:{winner.owner_id}", pool, winner, amount,
            kind=TransactionKind.SETTLEMENT,
        )

    def residue(amount: str):
        return _transfer(
            f"settlement_residue:{market_id}", pool, platform, amount,
            kind=TransactionKind.SETTLEMENT_RESIDUE,
        )

    await posting.post_all(
        session,
        [payout(first_winner, "60.1235"), payout(second_winner, "40.0001"),
         residue("149.8764")],
    )
    retries = {
        "a_new_winner": [payout(first_winner, "60.1235"), payout(late_winner, "10.0000")],
        "a_different_residue": [
            payout(first_winner, "60.1235"), payout(second_winner, "40.0001"),
            residue("149.8765"),
        ],
    }
    session.add(
        MarketResult(
            market_id=market_id,
            kind=ResultKind.SETTLED,
            outcome_id=outcome_ids[0],
            recorded_at=datetime.now(UTC),
        )
    )

    with pytest.raises(errors.PendingWritesOnReplay):
        await posting.post_all(session, retries[retry])

    async with get_session_factory()() as other:
        recorded = (
            await other.execute(
                select(func.count())
                .select_from(MarketResult)
                .where(MarketResult.market_id == market_id)
            )
        ).scalar_one()
        assert recorded == 0


async def test_a_fingerprint_mismatch_on_a_later_movement_is_a_reused_key(
    session: AsyncSession,
) -> None:
    """Every key exists; only the last movement's money differs. Refused by
    the classification on a clean session. Comparing the first movement only,
    or not comparing fingerprints, would replay it."""
    movements, _ = await _committed_batch(session)
    last = movements[2]
    source, target = last.legs[0].account, last.legs[1].account
    changed = [*movements[:2], _transfer(last.idempotency_key, source, target, "6.0004")]
    async with get_session_factory()() as other:
        before = await _ledger_counts(other)
    assert not has_pending_writes(session)

    with pytest.raises(errors.IdempotencyKeyReused):
        await posting.post_all(session, changed)

    async with get_session_factory()() as other:
        assert await _ledger_counts(other) == before


# =========================================================================
# Overdrafts
# =========================================================================
async def test_a_user_is_netted_across_the_batch(session: AsyncSession) -> None:
    """Two debits each affordable alone, one tick over together. Refused by
    the overdraft check: the keys are fresh, so the lookup classifies nothing.
    The batch commits once, so only the end state is ever visible."""
    platform = await _platform(session)
    (user,) = await _new_accounts(session, AccountKind.USER, 1)
    await _fund(session, platform, user, "60.0009")
    keys = [_key(), _key()]
    movements = [
        _transfer(keys[0], user, platform, "30.0005", kind=TransactionKind.TRADE_BUY),
        _transfer(keys[1], user, platform, "30.0005", kind=TransactionKind.TRADE_BUY),
    ]

    with pytest.raises(errors.InsufficientFunds) as refusal:
        await posting.post_all(session, movements)

    assert refusal.value.balance == Decimal("60.0009")
    assert refusal.value.required == Decimal("60.0010")
    async with get_session_factory()() as other:
        assert await _keys_written(other, keys) == 0
        assert await accounts.balance_of(other, user.id) == Decimal("60.0009")


async def test_platform_may_be_net_debited_beyond_its_balance(
    session: AsyncSession,
) -> None:
    """PLATFORM is already negative and goes further: accepted, by exactly the
    amount. The allowlist is USER, not every account (ADR 0009, D-009)."""
    platform = await _platform(session)
    (user,) = await _new_accounts(session, AccountKind.USER, 1)
    (pool,) = await _new_accounts(session, AccountKind.MARKET_POOL, 1)
    await _fund(session, platform, user, "100.0000")
    platform_before = await accounts.balance_of(session, platform.id)
    assert platform_before < 0

    await posting.post_all(
        session,
        [_transfer(_key(), platform, pool, "250.4321", kind=TransactionKind.MARKET_SEED)],
    )

    async with get_session_factory()() as other:
        assert await accounts.balance_of(other, platform.id) == (
            platform_before - Decimal("250.4321")
        )
        assert await accounts.balance_of(other, pool.id) == Decimal("250.4321")


# =========================================================================
# One commit
# =========================================================================
async def test_a_failure_mid_batch_commits_nothing(session: AsyncSession) -> None:
    """Movement 550 of 600 credits an account that does not exist, so the
    entries INSERT fails on the foreign key after the transactions INSERT has
    run. Nothing of the batch survives anywhere, in this session or another.

    `Entry` has no `account` relationship, only `account_id`, so the
    transient account cannot be inserted by cascade; the account count
    confirms it. The caller's staged row was flushed before the SAVEPOINT and
    stays in its session, as with `post`: the request's own rollback is what
    discards it, and no commit ever landed it.
    """
    platform = await _platform(session)
    (winner,) = await _new_accounts(session, AccountKind.USER, 1)
    nowhere = Account(id=uuid.uuid4(), kind=AccountKind.USER, owner_id=uuid.uuid4())
    keys = [_key() for _ in range(600)]
    movements = [
        _transfer(key, platform, nowhere if index == 550 else winner, "1.2345")
        for index, key in enumerate(keys)
    ]
    async with get_session_factory()() as other:
        accounts_before = (
            await other.execute(select(func.count()).select_from(Account))
        ).scalar_one()
    stowaway = _stage_stowaway(session)

    with pytest.raises(IntegrityError) as refusal:
        await posting.post_all(session, movements)

    # SQLAlchemy's asyncpg adapter raises its own error from asyncpg's, and
    # only asyncpg's carries the constraint name.
    assert refusal.value.orig.__cause__.constraint_name == "entries_account_id_fkey"

    assert await _keys_written(session, keys) == 0
    winner_entries = select(func.count()).select_from(Entry).where(
        Entry.account_id == winner.id
    )
    assert (await session.execute(winner_entries)).scalar_one() == 0
    assert await _stowaway_count(session, stowaway) == 1

    async with get_session_factory()() as other:
        assert await _keys_written(other, keys) == 0
        assert (await other.execute(winner_entries)).scalar_one() == 0
        assert await _stowaway_count(other, stowaway) == 0
        assert (
            await other.execute(select(func.count()).select_from(Account))
        ).scalar_one() == accounts_before
        assert await other.get(Account, nowhere.id) is None


async def test_a_batch_commits_exactly_once(session: AsyncSession) -> None:
    """The batch's one commit, counted on the engine's connections.

    Not the session's `after_commit`: that also fires when the kept SAVEPOINT
    is released, so a correct batch would count two. The connection's
    `commit` fires for the root COMMIT only, and on the engine it still sees
    a commit made after the session has moved to a new connection.
    """
    platform = await _platform(session)
    winners = await _new_accounts(session, AccountKind.USER, 3)
    movements = [_transfer(_key(), platform, winner, "2.5001") for winner in winners]
    engine = get_engine().sync_engine
    commits = 0

    def count_commit(_conn) -> None:  # noqa: ANN001
        nonlocal commits
        commits += 1

    event.listen(engine, "commit", count_commit)
    try:
        await posting.post_all(session, movements)
    finally:
        event.remove(engine, "commit", count_commit)

    assert commits == 1


# =========================================================================
# Stamps
# =========================================================================
async def test_the_first_stamp_follows_the_newest_entry_on_any_locked_account(
    session: AsyncSession,
) -> None:
    """The second movement's winner has the newest entry, an hour later than
    `now`. Stamping from the pool, from the first movement's accounts, or
    from `now` all give `now`; only the union of every locked account gives
    one tick after that winner's entry.

    Funded from a pool outside the batch, so PLATFORM's entries cannot stand
    in for the winner's.
    """
    (source, pool) = await _new_accounts(session, AccountKind.MARKET_POOL, 2)
    first_winner, second_winner = await _new_accounts(session, AccountKind.USER, 2)
    await _fund(session, source, pool, "50.0000")
    await _fund(session, source, first_winner, "1.0000")
    start = await _newest_entry_at(
        session, [pool.id, first_winner.id, second_winner.id]
    )
    floor = start + timedelta(minutes=30)
    late = await _fund(
        session, source, second_winner, "1.0000", now=start + timedelta(hours=1)
    )
    newest_on_second_winner = late.occurred_at
    expected = newest_on_second_winner + TICK

    # The precondition that keeps the three wrong answers apart from the right
    # one, whatever the fixtures stamped at wall-clock time.
    assert newest_on_second_winner == start + timedelta(hours=1)
    from_the_pool = max(floor, await _newest_entry_at(session, [pool.id]) + TICK)
    from_the_first_movement = max(
        floor, await _newest_entry_at(session, [pool.id, first_winner.id]) + TICK
    )
    assert expected not in {from_the_pool, from_the_first_movement, floor}

    results = await posting.post_all(
        session,
        [
            _transfer(_key(), pool, first_winner, "5.0001"),
            _transfer(_key(), pool, second_winner, "7.0003"),
        ],
        now=floor,
    )

    assert [r.occurred_at for r in results] == [expected, expected + TICK]
    for result in results:
        for entry in await _entries_of(session, result.id):
            assert entry.created_at > newest_on_second_winner


async def test_each_movement_has_one_stamp_and_each_account_strictly_increases(
    session: AsyncSession,
) -> None:
    """Movement *i* lands at the first stamp plus *i* µs on every leg, so a
    history page cannot split a movement and the pool, in every movement,
    never has two entries at one time."""
    platform = await _platform(session)
    (pool,) = await _new_accounts(session, AccountKind.MARKET_POOL, 1)
    first_winner, second_winner = await _new_accounts(session, AccountKind.USER, 2)

    results = await posting.post_all(
        session,
        [
            _transfer(_key(), pool, first_winner, "3.3333"),
            _transfer(_key(), pool, second_winner, "4.4444"),
            _transfer(_key(), pool, platform, "5.5555"),
        ],
    )

    first = results[0].occurred_at
    assert [r.occurred_at for r in results] == [first, first + TICK, first + 2 * TICK]
    pool_times = []
    for result in results:
        legs = await _entries_of(session, result.id)
        assert {leg.created_at for leg in legs} == {result.occurred_at}
        pool_times.extend(leg.created_at for leg in legs if leg.account_id == pool.id)
    assert pool_times == sorted(set(pool_times))
    assert len(pool_times) == 3


async def test_a_now_later_than_every_entry_is_the_first_stamp(
    session: AsyncSession,
) -> None:
    platform = await _platform(session)
    (pool,) = await _new_accounts(session, AccountKind.MARKET_POOL, 1)
    (winner,) = await _new_accounts(session, AccountKind.USER, 1)
    await _fund(session, platform, pool, "20.0000")
    later = datetime.now(UTC) + timedelta(days=1)
    newest = await _newest_entry_at(session, [pool.id, winner.id])
    assert newest + TICK < later

    results = await posting.post_all(
        session, [_transfer(_key(), pool, winner, "1.2345")], now=later
    )

    assert results[0].occurred_at == later


# =========================================================================
# Locks
# =========================================================================
async def test_the_batch_locks_each_account_once_ascending(
    session: AsyncSession,
) -> None:
    """The union of the batch's accounts, one statement each, ascending by id
    (ADR 0009). Eight winners in descending order, the pool in every
    movement: insertion order and hash order cannot pass for ascending."""
    (pool,) = await _new_accounts(session, AccountKind.MARKET_POOL, 1)
    winners = await _new_accounts(session, AccountKind.USER, 8)
    descending = sorted(winners, key=lambda account: account.id, reverse=True)
    movements = [_transfer(_key(), pool, winner, "1.0001") for winner in descending]

    with _capture_sql_with_parameters() as captured:
        await posting.post_all(session, movements)

    locked = [
        uuid.UUID(str(parameters[0]))
        for statement, parameters in captured
        if _is_lock(statement)
    ]
    assert locked == sorted([pool.id, *(w.id for w in winners)])


async def test_the_lookup_overdraft_and_stamp_reads_run_under_every_lock(
    session: AsyncSession,
) -> None:
    """Every read that decides the write comes after the last lock: the key
    lookup, the funded user's balance and the stamp probes (ADR 0015). A
    lookup before the locks refuses a retry racing its original as an
    overdraft."""
    platform = await _platform(session)
    (pool,) = await _new_accounts(session, AccountKind.MARKET_POOL, 1)
    user, winner = await _new_accounts(session, AccountKind.USER, 2)
    await _fund(session, platform, user, "10.0000")

    with capture_sql() as statements:
        await posting.post_all(
            session,
            [
                _transfer(_key(), user, pool, "3.0003", kind=TransactionKind.TRADE_BUY),
                _transfer(_key(), pool, winner, "1.0001"),
            ],
        )

    lock_positions = [i for i, s in enumerate(statements) if _is_lock(s)]
    lookup_positions = [
        i for i, s in enumerate(statements)
        if _is_select_from(s, "transactions") and "idempotency_key" in s.lower()
    ]
    entry_read_positions = [
        i for i, s in enumerate(statements) if _is_select_from(s, "entries")
    ]
    assert lock_positions and lookup_positions and entry_read_positions
    last_lock = max(lock_positions)
    assert min(lookup_positions) > last_lock
    assert min(entry_read_positions) > last_lock


def _hide_the_first_lookup(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Make `list_by_idempotency_keys`'s first call find nothing, so the key is
    met only at the INSERT, as two sessions sharing no account meet it.
    Returns a one-item counter of the calls. Later calls, the re-read among
    them if it uses this function, go to the real one."""
    real = posting.list_by_idempotency_keys
    calls = [0]

    async def miss_the_first(own: AsyncSession, keys: list[str]):
        calls[0] += 1
        return [] if calls[0] == 1 else await real(own, keys)

    monkeypatch.setattr(posting, "list_by_idempotency_keys", miss_the_first)
    return calls


@pytest.mark.parametrize("dirty", [False, True], ids=["clean", "dirty"])
async def test_a_key_found_only_by_the_insert_race_is_refused(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, dirty: bool
) -> None:
    """`post`'s race handler, in batch form: the re-read after the SAVEPOINT's
    `IntegrityError` gives the lookup's answers. K exists on accounts outside
    the batch; the batch's other key does not. Partial, so a reused key on a
    clean session; pending writes on a dirty one. Never a bare
    `IntegrityError`."""
    platform = await _platform(session)
    (outsider,) = await _new_accounts(session, AccountKind.USER, 1)
    (pool,) = await _new_accounts(session, AccountKind.MARKET_POOL, 1)
    first_winner, second_winner = await _new_accounts(session, AccountKind.USER, 2)
    taken = _key("taken")
    await posting.post(
        session,
        idempotency_key=taken,
        kind=TransactionKind.SETTLEMENT,
        legs=[_leg(platform, "-2.2222"), _leg(outsider, "2.2222")],
    )
    fresh = _key("fresh")
    stowaway = _stage_stowaway(session) if dirty else None
    calls = _hide_the_first_lookup(monkeypatch)
    expected = errors.PendingWritesOnReplay if dirty else errors.IdempotencyKeyReused

    with capture_sql() as statements:
        with pytest.raises(expected):
            await posting.post_all(
                session,
                [
                    _transfer(fresh, pool, first_winner, "1.0001"),
                    _transfer(taken, pool, second_winner, "1.0002"),
                ],
            )

    assert calls[0] >= 1, "the lookup under the lock was not hidden"
    assert any(_is_insert_into(s, "transactions") for s in statements), (
        "the refusal came before the INSERT, so the race was not reached"
    )
    async with get_session_factory()() as other:
        assert await _keys_written(other, [fresh]) == 0
        assert await _keys_written(other, [taken]) == 1
        if stowaway is not None:
            assert await _stowaway_count(other, stowaway) == 0


async def test_a_whole_batch_found_only_by_the_insert_race_replays(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race's other answer: the re-read finds every key with matching
    fingerprints on a clean session, so the batch replays its originals in
    order and writes nothing, as the lookup under the lock would have."""
    movements, originals = await _committed_batch(session)
    async with get_session_factory()() as other:
        before = await _ledger_counts(other)
    assert not has_pending_writes(session)
    calls = _hide_the_first_lookup(monkeypatch)

    with capture_sql() as statements:
        replayed = await posting.post_all(session, movements)

    assert calls[0] >= 1, "the lookup under the lock was not hidden"
    assert any(_is_insert_into(s, "transactions") for s in statements), (
        "the replay came before the INSERT, so the race was not reached"
    )
    assert [r.id for r in replayed] == [o.id for o in originals]
    async with get_session_factory()() as other:
        assert await _ledger_counts(other) == before


# =========================================================================
# Cost
# =========================================================================
async def _statements_for_a_batch_of(session: AsyncSession, size: int) -> int:
    """Statements sent by one batch paying `size` fresh winners from one pool."""
    (pool,) = await _new_accounts(session, AccountKind.MARKET_POOL, 1)
    winners = await _new_accounts(session, AccountKind.USER, size)
    movements = [_transfer(_key(), pool, winner, "1.0001") for winner in winners]

    with capture_sql() as statements:
        await posting.post_all(session, movements)
    return len(statements)


async def test_statements_grow_by_two_per_movement(session: AsyncSession) -> None:
    """One lock and one stamp probe per new account, and nothing else per
    movement: one lookup, one INSERT per table. Both sizes stay under 500
    movements, so the entries fit in one insert page of 1,000 rows."""
    smaller = await _statements_for_a_batch_of(session, 100)
    larger = await _statements_for_a_batch_of(session, 200)

    assert larger - smaller == 2 * 100
