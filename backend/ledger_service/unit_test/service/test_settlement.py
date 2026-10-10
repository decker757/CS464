"""`settlement.pay_out`: paying an approved market. [3.4] #12, ADR 0019.

The spec is #12's criteria and test notes, ADR 0019 as amended on 2026-10-10,
and DECISIONS.md's 2026-10-10 entries from "A recorded settlement replays
before any status check" to "A cold settlement reads the public detail
twice". The races are in `test_settlement_concurrency.py`, and the trade
path's latch in `test_trade_latch.py`.

Every "nothing was written" is counted from a second session or the audit
reader, before any rollback this test runs itself. A refusal says which check
refuses it and why no earlier check can.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_engine
from unit_test.conftest import idle_in_transaction
from unit_test.history_fixtures import read_history
from unit_test.settlement_fixtures import (
    LOSER,
    PROBE_TIMEOUT,
    SUBSIDY,
    WINNER,
    ZERO,
    SettleUpstream,
    account_id_of,
    admin,
    balance_of_account,
    basis_of,
    book_lock_is_free,
    capture_sql_with_parameters,
    committed_counts,
    count_commits,
    hold,
    legs_of,
    locked_account_ids,
    payout_key,
    platform_id,
    positions_and_q,
    residue_key,
    result_count,
    settle,
    settled_entries,
    transaction_by_key,
    warm,
)
from unit_test.trade_fixtures import (
    assert_ledger_balances,
    books,
    entities,
    errors,
    fund,
    pool_balance,
    posting,
    session_factory,
    set_q,
)

# Shares held by the holder of both outcomes, and by the pure loser. Four
# places that do not round, so a payout of the wrong outcome's quantity, or a
# rounded one, is a different number.
BOTH_WINNING = Decimal("3.1007")
BOTH_LOSING = Decimal("12.4441")
LOSER_QUANTITY = Decimal("29.9871")

# The two pure winners' holdings, and the residue they leave against a pool
# holding `SUBSIDY`: pool minus every winning share, `BOTH_WINNING` included.
RESIDUES = {
    "positive": ((Decimal("41.3337"), Decimal("17.0429")), Decimal("188.5227")),
    "negative": ((Decimal("173.4567"), Decimal("99.0001")), Decimal("-25.5575")),
    "zero": ((Decimal("173.4567"), Decimal("73.4426")), ZERO),
}


@dataclass
class Holders:
    """Who holds what in a seeded market. `paid` maps each winner to the
    quantity they must be paid."""

    paid: dict[uuid.UUID, Decimal]
    loser: uuid.UUID
    sold_out: uuid.UUID
    both: uuid.UUID
    order_inserted: list[uuid.UUID] = field(default_factory=list)


async def seed(
    session: AsyncSession, upstream: SettleUpstream, residue: str = "positive"
) -> Holders:
    """A warm book holding `SUBSIDY`, with two pure winners, a holder of both
    outcomes, a pure loser and a winner who sold to zero.

    Inserted in descending `user_id` order, so a batch built without
    `ORDER BY user_id` comes out in a different order from one built with it.
    """
    (first, second), _ = RESIDUES[residue]
    await warm(session, upstream)
    users = sorted((uuid.uuid4() for _ in range(5)), reverse=True)
    pure_a, pure_b, both, loser, sold_out = users
    await hold(
        session,
        upstream,
        [
            (pure_a, WINNER, first),
            (pure_b, WINNER, second),
            (both, WINNER, BOTH_WINNING),
            (both, LOSER, BOTH_LOSING),
            (loser, LOSER, LOSER_QUANTITY),
            (sold_out, WINNER, ZERO),
        ],
    )
    return Holders(
        paid={pure_a: first, pure_b: second, both: BOTH_WINNING},
        loser=loser,
        sold_out=sold_out,
        both=both,
        order_inserted=users,
    )


def expected_figures(residue: str) -> tuple[int, Decimal, Decimal]:
    """`holders_paid`, `total_paid` and the signed `residue` for a seed."""
    (first, second), signed_residue = RESIDUES[residue]
    return 3, first + second + BOTH_WINNING, signed_residue


def figures(result) -> tuple:  # noqa: ANN001
    return (
        result.market_id,
        result.outcome_id,
        result.recorded_at,
        result.holders_paid,
        result.total_paid,
        result.residue,
    )


# =========================================================================
# What it pays
# =========================================================================
@pytest.mark.parametrize("residue", list(RESIDUES))
async def test_settlement_pays_every_winning_share_once_and_empties_the_pool(
    session: AsyncSession, residue: str
) -> None:
    """#12's "What it pays" criteria, and DECISIONS.md's "A payout is quantity
    × 1, keyed per winner in `user_id` order, and the residue is the last
    movement".

    Each winner is paid exactly their winning quantity, in a transaction of
    their own with one leg on their account; the loser, the sold-out holder
    and the losing half of the holder of both get nothing. The residue goes
    whichever way the pool falls, and a zero one is not posted and does not
    lock PLATFORM. Positions and `q` are untouched.
    """
    ents = entities()
    upstream = SettleUpstream()
    holders = await seed(session, upstream, residue)
    before = await positions_and_q(session, upstream.market_id)
    platform = await platform_id(session)
    pool = await account_id_of(
        session, ents.AccountKind.MARKET_POOL, upstream.market_id
    )

    with capture_sql_with_parameters() as statements:
        result = await settle(session, upstream)

    holders_paid, total_paid, signed_residue = expected_figures(residue)
    assert (result.holders_paid, result.total_paid, result.residue) == (
        holders_paid,
        total_paid,
        signed_residue,
    )
    assert result.market_id == upstream.market_id
    assert result.outcome_id == upstream.winner

    stamps = {}
    for user_id, quantity in holders.paid.items():
        payout = await transaction_by_key(
            session, payout_key(upstream.market_id, user_id)
        )
        assert payout is not None, f"winner {user_id} was not paid"
        assert payout.kind is ents.TransactionKind.SETTLEMENT
        user_account = await account_id_of(session, ents.AccountKind.USER, user_id)
        assert await legs_of(session, payout.id) == {
            user_account: quantity,
            pool: -quantity,
        }
        assert payout.context == {
            "user_id": str(user_id),
            "market_id": str(upstream.market_id),
            "outcome_id": str(upstream.winner),
            "quantity": str(quantity),
        }
        stamps[user_id] = payout.occurred_at

    for unpaid in (holders.loser, holders.sold_out):
        assert (
            await transaction_by_key(session, payout_key(upstream.market_id, unpaid))
            is None
        )
        account = await account_id_of(session, ents.AccountKind.USER, unpaid)
        assert await balance_of_account(session, account) == ZERO

    in_user_id_order = [stamps[user_id] for user_id in sorted(holders.paid)]
    assert in_user_id_order == sorted(stamps.values()), (
        "payouts are not stamped in user_id order"
    )

    residue_transaction = await transaction_by_key(
        session, residue_key(upstream.market_id)
    )
    if signed_residue == ZERO:
        assert residue_transaction is None, "a zero residue was posted"
        assert platform not in locked_account_ids(statements), (
            "PLATFORM was locked by a settlement that posts no residue"
        )
    else:
        assert residue_transaction is not None
        assert residue_transaction.kind is ents.TransactionKind.SETTLEMENT_RESIDUE
        assert residue_transaction.context == {"market_id": str(upstream.market_id)}
        assert await legs_of(session, residue_transaction.id) == {
            pool: -signed_residue,
            platform: signed_residue,
        }
        assert residue_transaction.occurred_at > max(stamps.values()), (
            "the residue is not the last movement"
        )

    assert await pool_balance(session, upstream.market_id) == Decimal("0.0000")
    await assert_ledger_balances(session)
    assert await positions_and_q(session, upstream.market_id) == before, (
        "settlement wrote a position or a q"
    )


async def test_a_payout_is_the_winners_newest_row_when_their_entry_is_newer_than_now(
    session: AsyncSession,
) -> None:
    """#12's stamp criterion and test note. The winner's newest entry is an
    hour after settlement's pinned `now`, and the pool's newest is before it.

    Stamped from the pool's entries alone, or from `now`, the payout lands at
    `now` and is listed below the newer entry with a `balance_after` the
    winner never had. `recorded_at` is the pinned clock, which disagrees with
    the stamp by most of an hour.
    """
    ents = entities()
    upstream = SettleUpstream()
    await warm(session, upstream)
    winner = uuid.uuid4()
    await fund(session, winner)
    quantity = Decimal("41.3337")
    await hold(session, upstream, [(winner, WINNER, quantity)])

    real = datetime.now(UTC)
    accounts = await _accounts(session, winner)
    later = await posting().post(
        session,
        idempotency_key=f"later:{uuid.uuid4()}",
        kind=ents.TransactionKind.SIGNUP_GRANT,
        legs=[
            posting().Leg(account=accounts["platform"], amount=Decimal("-3.3333")),
            posting().Leg(account=accounts["user"], amount=Decimal("3.3333")),
        ],
        now=real + timedelta(hours=1),
    )
    later_at = later.occurred_at
    pinned = real + timedelta(minutes=1)

    result = await settle(session, upstream, now=pinned)

    payout = await transaction_by_key(session, payout_key(upstream.market_id, winner))
    assert result.recorded_at == pinned
    assert payout.occurred_at > later_at

    newest = (await read_history(session, winner)).rows[0]
    assert newest.entry.transaction_id == payout.id
    assert newest.entry.amount == quantity
    assert newest.balance_after == await balance_of_account(
        session, accounts["user"].id
    )


async def _accounts(session: AsyncSession, user_id: uuid.UUID) -> dict:
    ents = entities()
    user = await posting_accounts().find(session, ents.AccountKind.USER, user_id)
    platform = await posting_accounts().ensure_platform(session)
    await session.commit()
    return {"user": user, "platform": platform}


def posting_accounts():
    from service import accounts  # noqa: PLC0415

    return accounts


async def test_a_market_nobody_traded_opens_in_its_own_commit_and_returns_its_seed(
    session: AsyncSession,
) -> None:
    """#12: "A market nobody traded settles: its book is opened if it has
    none, its whole seed subsidy returns to the platform account, no user is
    paid, and it reaches SETTLED", and "Opening a cold book, when needed, is
    its own earlier commit".

    Two root commits: the book's, then the settlement's. The number of GETs is
    not pinned: the second is an accepted cost ("A cold settlement reads the
    public detail twice"), not a promise.
    """
    upstream = SettleUpstream()

    with count_commits() as commits:
        result = await settle(session, upstream)

    assert commits[0] == 2
    assert (result.holders_paid, result.total_paid, result.residue) == (
        0,
        ZERO,
        SUBSIDY,
    )
    assert await pool_balance(session, upstream.market_id) == Decimal("0.0000")
    platform = await platform_id(session)
    assert await balance_of_account(session, platform) == ZERO, (
        "the seed did not return to PLATFORM"
    )
    assert await result_count(session, upstream.market_id) == 1
    assert upstream.posts == 1
    await assert_ledger_balances(session)


async def test_a_thousand_winners_and_a_thousand_losers_settle_within_five_seconds(
    session: AsyncSession,
) -> None:
    """#12's speed criterion: 1,000 distinct winning holders and 1,000 losing
    positions, measured at the service from the call to the commit. The mocks
    answer at once, so the calls to market_service add nothing.

    Real USER accounts and `q` equal to the sum of the positions, inserted in
    bulk so the setup is not what is timed.
    """
    ents = entities()
    upstream = SettleUpstream()
    await warm(session, upstream)

    now = datetime.now(UTC)
    holders = [(uuid.uuid4(), WINNER, Decimal("1.0001") + i * Decimal("0.0137"))
               for i in range(1000)]
    holders += [(uuid.uuid4(), LOSER, Decimal("2.0003") + i * Decimal("0.0071"))
                for i in range(1000)]
    await session.execute(
        insert(ents.Account),
        [
            {"id": uuid.uuid4(), "kind": ents.AccountKind.USER, "owner_id": user_id}
            for user_id, _, _ in holders
        ],
    )
    await session.execute(
        insert(ents.Position),
        [
            {
                "user_id": user_id,
                "market_id": upstream.market_id,
                "outcome_id": upstream.outcomes[position],
                "quantity": quantity,
                "cost_basis": basis_of(quantity),
                "created_at": now,
                "updated_at": now,
            }
            for user_id, position, quantity in holders
        ],
    )
    q = [ZERO, ZERO]
    for _, position, quantity in holders:
        q[position] += quantity
    await set_q(session, upstream.market_id, q)

    started = time.perf_counter()
    result = await settle(session, upstream)
    elapsed = time.perf_counter() - started
    print(f"\nsettled 1,000 winners and 1,000 losers in {elapsed:.3f} s")

    assert result.holders_paid == 1000
    assert result.total_paid == q[WINNER]
    assert elapsed < 5.0, f"settlement took {elapsed:.3f} s, over the 5 s budget"
    assert await pool_balance(session, upstream.market_id) == Decimal("0.0000")


# =========================================================================
# One transaction
# =========================================================================
async def test_a_warm_settlement_commits_exactly_once(session: AsyncSession) -> None:
    """#12: the payouts, the residue, the record and the audit entry commit in
    exactly one database commit. Counted on the engine's connections, as
    `test_post_all.py` does. A record or audit entry committed on its own
    makes two."""
    upstream = SettleUpstream()
    await seed(session, upstream)

    with count_commits() as commits:
        await settle(session, upstream)

    assert commits[0] == 1


async def test_a_failure_mid_batch_commits_nothing_and_calls_nobody(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    audit_reader: AsyncSession,
) -> None:
    """#12's rollback note. The second payout's build raises, after the record
    and the audit entry are staged and the first payout is built.

    Counted before any rollback, from a second session and the audit reader:
    no payout, no record, no audit entry, and no call to `/settle`. An early
    commit of anything, or the POST before the commit, turns this red. The
    request's own session is not asked: it can see its own uncommitted rows.
    """
    upstream = SettleUpstream()
    holders = await seed(session, upstream)
    actor = admin()
    real = posting()._transaction_of
    builds = 0

    def failing(**kwargs):  # noqa: ANN003, ANN202
        nonlocal builds
        builds += 1
        if builds == 2:
            raise RuntimeError("forced failure mid-batch")
        return real(**kwargs)

    monkeypatch.setattr(posting(), "_transaction_of", failing)

    with pytest.raises(RuntimeError, match="forced failure mid-batch"):
        await settle(session, upstream, actor=actor)

    async with session_factory()() as other:
        for user_id in holders.paid:
            assert (
                await transaction_by_key(
                    other, payout_key(upstream.market_id, user_id)
                )
                is None
            )
        assert await transaction_by_key(other, residue_key(upstream.market_id)) is None
        assert await result_count(other, upstream.market_id) == 0
    assert await settled_entries(audit_reader, actor.id) == []
    assert upstream.posts == 0


# =========================================================================
# Replays
# =========================================================================
REPORTS_AFTER_PAYING = {
    "approved_not_settleable": {"status": "approved", "settleable": False},
    "closed": {"status": "closed", "settleable": False},
    "another_winner": {"proposed_outcome_id": "outcome-0"},
    "no_winner": {"proposed_outcome_id": None},
}


@pytest.mark.parametrize("report", list(REPORTS_AFTER_PAYING))
async def test_a_recorded_settlement_replays_whatever_market_service_now_reports(
    session: AsyncSession, audit_reader: AsyncSession, report: str
) -> None:
    """#12: a market already settled is answered `200` with the settlement it
    recorded the first time, whatever status market_service now reports, and
    writes nothing, but still calls `/settle`. The recorded outcome governs.

    Run twice after the first, so "any number of times" is more than one.
    Status checks ahead of the unlocked lookup refuse the first two reports
    `409`. A replay that read the winner refuses the last two `503`.
    """
    upstream = SettleUpstream()
    await seed(session, upstream)
    actor = admin()
    first = await settle(session, upstream, actor=actor)
    written = await committed_counts(upstream.market_id)

    change = dict(REPORTS_AFTER_PAYING[report])
    if change.get("proposed_outcome_id") == "outcome-0":
        change["proposed_outcome_id"] = str(upstream.outcomes[LOSER])
    upstream.body.update(change)

    second = await settle(session, upstream, actor=actor)
    third = await settle(session, upstream, actor=actor)

    assert figures(second) == figures(first)
    assert figures(third) == figures(first)
    assert await committed_counts(upstream.market_id) == written
    assert len(await settled_entries(audit_reader, actor.id)) == 1
    assert upstream.posts == 3


@pytest.mark.parametrize("residue", list(RESIDUES))
async def test_a_replay_rebuilds_the_first_settlements_figures(
    session: AsyncSession, residue: str
) -> None:
    """DECISIONS.md, "The settlement response carries its full figures, rebuilt
    from the entries on a replay, through one builder". A positive residue, a
    negative one, and a zero one with no residue entry at all, which must read
    as zero. A sign or a kind missed by the rebuild turns this red."""
    upstream = SettleUpstream()
    await seed(session, upstream, residue)

    first = await settle(session, upstream)
    replayed = await settle(session, upstream)

    assert figures(replayed) == figures(first)
    assert (first.holders_paid, first.total_paid, first.residue) == expected_figures(
        residue
    )


async def test_a_market_settled_upstream_with_no_record_is_paid_in_full(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """#12: a market market_service already reports as `settled`, with no
    ledger record, is settled exactly as if it were `approved`: the repair for
    a direct call to `/settle` (ADR 0019). The settle step answers 200 for
    SETTLED to SETTLED. Accepting `approved` alone refuses it `409`."""
    upstream = SettleUpstream(status="settled")
    holders = await seed(session, upstream)
    actor = admin()

    result = await settle(session, upstream, actor=actor)

    assert (result.holders_paid, result.total_paid, result.residue) == (
        expected_figures("positive")
    )
    for user_id in holders.paid:
        assert (
            await transaction_by_key(session, payout_key(upstream.market_id, user_id))
            is not None
        )
    assert await result_count(session, upstream.market_id) == 1
    assert len(await settled_entries(audit_reader, actor.id)) == 1
    assert upstream.posts == 1


# =========================================================================
# Refusals
# =========================================================================
@pytest.mark.parametrize("status", ["open", "approved_not_settleable"])
async def test_a_status_refusal_opens_no_book(session: AsyncSession, status: str) -> None:
    """#12: "A settlement refused on a market's status opens no book."

    Refused by the status check. The GET answers and parses, and the unlocked
    lookup cannot hit on a market with no book, so nothing earlier refuses.
    `books.ensure_open` ahead of the check would commit a book and a seed, and
    make a second GET.
    """
    ents = entities()
    if status == "open":
        upstream = SettleUpstream(status="open", settleable=False)
        refusal = errors().MarketNotApproved
    else:
        upstream = SettleUpstream(status="approved", settleable=False)
        refusal = errors().DisputeWindowOpen

    with pytest.raises(refusal):
        await settle(session, upstream)

    async with session_factory()() as other:
        assert await books().find(other, upstream.market_id) is None
        assert (
            await transaction_by_key(other, f"market-open:{upstream.market_id}")
            is None
        )
        assert (
            await posting_accounts().find(
                other, ents.AccountKind.MARKET_POOL, upstream.market_id
            )
            is None
        )
    assert upstream.gets == 1
    assert upstream.posts == 0


@pytest.mark.parametrize("settleable", [False, True])
async def test_the_dispute_window_is_refused_by_the_ledger_too(
    session: AsyncSession, audit_reader: AsyncSession, settleable: bool
) -> None:
    """#12's window-boundary note: the ledger test stubs `settleable` false and
    true, and must fail with the ledger's check removed. market_service's side
    pins the clock in its own suite.

    False is refused by the status check, the first check that reads
    `settleable`; the GET and the unlocked lookup pass. True settles.
    """
    upstream = SettleUpstream(settleable=settleable)
    await seed(session, upstream)
    actor = admin()
    before = await committed_counts(upstream.market_id)

    if not settleable:
        with pytest.raises(errors().DisputeWindowOpen):
            await settle(session, upstream, actor=actor)
        assert await committed_counts(upstream.market_id) == before
        assert await settled_entries(audit_reader, actor.id) == []
        assert upstream.posts == 0
        assert await book_lock_is_free(upstream.market_id)
    else:
        result = await settle(session, upstream, actor=actor)
        assert result.holders_paid == 3
        assert upstream.posts == 1


REFUSALS = [
    "open",
    "closed",
    "pending_resolution",
    "404_no_book",
    "404_with_a_book",
    "no_winner",
    "foreign_winner",
]


@pytest.mark.parametrize("case", REFUSALS)
async def test_a_refused_settlement_writes_nothing(
    session: AsyncSession, audit_reader: AsyncSession, case: str
) -> None:
    """#12's refusals, each with nothing written and the book lock free after.

    - `open`, `closed`, `pending_resolution`: `409 market_not_approved`, from
      the status check. The GET parses and the unlocked lookup misses.
    - 404 with no book: `404 market_not_found`, from `read_terms`, the first
      step. 404 with a book: `503`, from the same step, by the book.
    - A null winner, or one the book does not have: `503`, from the winner
      check under the book lock. Every earlier check passes: the market is
      `approved` and `settleable`, the book is warm, and nothing is recorded.
      Warm on purpose, since a cold book's seed commits before this check.
      Removed, the record's CHECK or foreign key fails inside `post_all` as a
      500. The lock must be released by the refusal's rollback.
    """
    upstream = SettleUpstream()
    actor = admin()
    cold = case == "404_no_book"
    if not cold:
        await seed(session, upstream)

    refusal = errors().MarketTermsUnavailable
    if case in ("open", "closed", "pending_resolution"):
        upstream.body.update(status=case, settleable=False)
        refusal = errors().MarketNotApproved
    elif case.startswith("404"):
        upstream.get_status_code = 404
        if cold:
            refusal = errors().MarketNotFound
    elif case == "no_winner":
        upstream.body["proposed_outcome_id"] = None
    else:
        upstream.body["proposed_outcome_id"] = str(uuid.uuid4())

    before = await committed_counts(upstream.market_id)

    with pytest.raises(refusal):
        await settle(session, upstream, actor=actor)

    assert await committed_counts(upstream.market_id) == before
    assert await settled_entries(audit_reader, actor.id) == []
    assert upstream.posts == 0
    if cold:
        async with session_factory()() as other:
            assert await books().find(other, upstream.market_id) is None
    else:
        assert await book_lock_is_free(upstream.market_id), (
            "a refusal under the book lock left it held"
        )


async def test_an_unreachable_market_service_refuses_even_a_paid_market(
    session: AsyncSession,
) -> None:
    """ADR 0019's 2026-10-10 amendment: the read comes first, and a failure
    refuses there "even for a market the ledger has already paid", with
    `503 market_terms_unavailable` and no call to `/settle`.

    Looking the record up before the read, as the trade path orders them,
    would replay and then fail at `/settle` as `settlement_unconfirmed`.
    """
    upstream = SettleUpstream()
    await seed(session, upstream)
    await settle(session, upstream)
    written = await committed_counts(upstream.market_id)
    upstream.get_raises = httpx.ConnectError("market service is down")

    with pytest.raises(errors().MarketTermsUnavailable):
        await settle(session, upstream)

    assert upstream.posts == 1
    assert await committed_counts(upstream.market_id) == written


# =========================================================================
# The last step
# =========================================================================
@pytest.mark.parametrize("path", ["fresh", "replay"])
async def test_an_unconfirmed_settlement_stands_and_a_retry_completes_it(
    session: AsyncSession, audit_reader: AsyncSession, path: str
) -> None:
    """#12: if the settle step fails, the answer is `503 settlement_unconfirmed`,
    the payouts have committed and stand, and repeating the request completes
    it without writing anything. On the fresh path and on a replay, which
    reach `/settle` by different branches. Which non-200 it was is
    `mark_settled`'s concern, tested in 5b.
    """
    upstream = SettleUpstream()
    holders = await seed(session, upstream)
    actor = admin()
    if path == "replay":
        await settle(session, upstream, actor=actor)
    upstream.post_status_code = 500

    with pytest.raises(errors().SettlementUnconfirmed):
        await settle(session, upstream, actor=actor)

    async with session_factory()() as other:
        for user_id in holders.paid:
            assert (
                await transaction_by_key(
                    other, payout_key(upstream.market_id, user_id)
                )
                is not None
            ), "the payouts did not stand"
        assert await result_count(other, upstream.market_id) == 1
    assert len(await settled_entries(audit_reader, actor.id)) == 1
    written = await committed_counts(upstream.market_id)
    posts = upstream.posts

    upstream.post_status_code = 200
    retried = await settle(session, upstream, actor=actor)

    assert (retried.holders_paid, retried.total_paid, retried.residue) == (
        expected_figures("positive")
    )
    assert await committed_counts(upstream.market_id) == written
    assert len(await settled_entries(audit_reader, actor.id)) == 1
    assert upstream.posts == posts + 1


# =========================================================================
# No connection across HTTP
# =========================================================================
STALLS = ["fresh_get", "cold_get", "fresh_post", "replay_post", "locked_replay_post"]


@pytest.mark.parametrize("stall", STALLS)
async def test_no_connection_is_held_while_market_service_answers(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, stall: str
) -> None:
    """#12: no call to market_service is made while the request holds a
    database connection or a row lock. Probed at the stalled call from the
    session, the pool and Postgres (D-043's three witnesses).

    - `fresh_get`: the first read, before any statement.
    - `cold_get`: `books.ensure_open`'s own read, on a cold book.
    - `fresh_post`: after the commit. A statement between the commit and the
      POST autobegins a transaction.
    - `replay_post`: a replay found by the unlocked lookup; without its
      rollback the lookup's transaction stays open.
    - `locked_replay_post`: a replay found under the book lock, the first
      lookup made to miss; without its rollback the lock is still held.
    """
    upstream = SettleUpstream()
    if stall != "cold_get":
        await seed(session, upstream)
    if stall in ("replay_post", "locked_replay_post"):
        await settle(session, upstream)
    if stall == "locked_replay_post":
        real = books().find_result
        misses: set[int] = set()

        async def first_lookup_misses(caller_session, market_id):  # noqa: ANN001, ANN202
            if id(caller_session) not in misses:
                misses.add(id(caller_session))
                return None
            return await real(caller_session, market_id)

        monkeypatch.setattr(books(), "find_result", first_lookup_misses)

    pool = get_engine().pool
    entered = asyncio.Event()
    release = asyncio.Event()
    seen: list[dict] = []

    async def stall_here(_call: int) -> None:
        seen.append(
            {
                "session in a transaction": session.in_transaction(),
                "pooled connections checked out": pool.checkedout(),
            }
        )
        entered.set()
        await release.wait()

    target_get = 2 if stall == "cold_get" else 1
    if stall.endswith("get"):
        async def on_get(call: int) -> None:
            if call == target_get:
                await stall_here(call)

        upstream.on_get = on_get
    else:
        upstream.on_post = stall_here
        upstream.posts = 0

    task = asyncio.create_task(settle(session, upstream))
    try:
        async with asyncio.timeout(PROBE_TIMEOUT):
            while not entered.is_set():
                if task.done():
                    task.result()
                    pytest.fail(f"timed out: settlement finished without the {stall}")
                await asyncio.sleep(0.01)
        idle = await idle_in_transaction()
    except TimeoutError:
        pytest.fail(f"timed out: the {stall} was never reached")
    finally:
        release.set()
        async with asyncio.timeout(PROBE_TIMEOUT):
            await asyncio.gather(task, return_exceptions=True)

    clean = {"session in a transaction": False, "pooled connections checked out": 0}
    assert seen == [clean]
    assert idle == 0, f"{idle} backend(s) idle in transaction across the {stall}"
    task.result()


# =========================================================================
# Audit
# =========================================================================
async def test_market_settled_carries_exactly_these_columns(
    session: AsyncSession, audit_reader: AsyncSession
) -> None:
    """#12's audit criterion and DECISIONS.md, "`market.settled` carries the
    winner and the three figures, with no target label and no reason": every
    column, and every context key, matched exactly. A negative residue, so the
    sign is checked. A repeat by another administrator appends nothing.
    """
    upstream = SettleUpstream()
    await seed(session, upstream, "negative")
    actor = admin()
    repeater = admin(username="ihsan_k")

    result = await settle(session, upstream, actor=actor)
    await settle(session, upstream, actor=repeater)

    entries = await settled_entries(audit_reader, actor.id, repeater.id)
    assert len(entries) == 1
    entry = entries[0]
    assert isinstance(entry.pop("id"), uuid.UUID)
    assert entry == {
        "occurred_at": result.recorded_at,
        "actor_id": actor.id,
        "actor_username": actor.username,
        "actor_role": "admin",
        "action_type": "market.settled",
        "target_type": "market",
        "target_id": upstream.market_id,
        "target_label": None,
        "reason": None,
        "context": {
            "winning_outcome_id": str(upstream.winner),
            "total_paid": "275.5575",
            "residue": "-25.5575",
            "holders_paid": 3,
        },
        "source_service": "ledger_service",
    }


async def test_a_settlement_past_both_lookups_is_refused_as_pending_writes(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    audit_reader: AsyncSession,
) -> None:
    """ADR 0019's first trap, as amended: the guard is the record, as an ORM
    write, pending when `post_all` is entered. Both lookups are made to miss on
    a market already paid, so the second settlement reaches `post_all` with its
    keys already written.

    The error code is what does the work. The pool is empty by then, so the
    second batch never matches the first whole, and a guard lost to a Core
    insert, or to a record committed early or never staged, answers
    `idempotency_key_reused` or an `IntegrityError` instead. No second
    `market.settled` either way.
    """
    upstream = SettleUpstream()
    await seed(session, upstream)
    first, second = admin(), admin(username="ihsan_k")
    await settle(session, upstream, actor=first)
    written = await committed_counts(upstream.market_id)

    async def always_misses(caller_session, market_id):  # noqa: ANN001, ANN202
        return None

    monkeypatch.setattr(books(), "find_result", always_misses)

    with pytest.raises(errors().PendingWritesOnReplay):
        await settle(session, upstream, actor=second)

    assert await committed_counts(upstream.market_id) == written
    assert len(await settled_entries(audit_reader, first.id, second.id)) == 1
