"""The leaderboard refresh: who is ranked, at what, in what order, and what it
leaves behind. [L-1] #38, [L-3] #40, ADR 0021.

The spec is #270's entries in DECISIONS.md, cited by title in each test. The
oracle for a net worth is `portfolio_of_user(...).net_worth` (ADR 0018's
2026-10-10 amendment: the leaderboard "reuses the portfolio's valuation, its
settled branch included"). Because both sides run `valuation_of`, each
equality test names the mistake **in the refresh's own code** that it
catches: its holder rows, its grouping, its balance, or its settled handling.

Every valuation test has at least three holders who share markets, so a
subquery that is not correlated to its holder cannot pass by having only one
holder to sum. The stand-in auth service is `leaderboard_fixtures.Directory`.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import BindParameter

from core.database import get_engine
from unit_test.conftest import idle_in_transaction, set_q
from unit_test.leaderboard_fixtures import (
    Directory,
    at,
    fixed_clock,
    generations,
    granted,
    net_worths_from_portfolio,
    newest,
    refresh_with,
    settled_by_trading,
)
from unit_test.portfolio_fixtures import market_at, row, sequence_values
from unit_test.sell_fixtures import footprint, hold, q_now
from unit_test.settlement_fixtures import B as SETTLED_B
from unit_test.settlement_fixtures import LOSER, PAID, UNPAID, WINNER
from unit_test.void_fixtures import VoidUpstream, open_book, trade, void
from unit_test.trade_fixtures import (
    SMALL_QUANTITY,
    ZERO,
    capture_sql,
    entities,
    errors,
    fund,
    ledger_counts,
    locks,
    session_factory,
    writes,
)

_LEADERBOARD_LOGGER = "service.leaderboard"


async def _assert_ranked_at_portfolio_net_worth(
    session: AsyncSession, holders: list[uuid.UUID]
) -> None:
    """Every holder's entry in the newest generation is their portfolio's
    `net_worth`, read after the refresh with nothing traded in between."""
    current = await newest()
    assert current is not None, "the refresh wrote no generation"
    ranked = current.by_user()
    expected = await net_worths_from_portfolio(session, holders)
    assert {user_id: ranked[user_id].net_worth for user_id in holders} == expected


# =========================================================================
# Net worth is the portfolio's
# =========================================================================
async def test_a_holder_of_both_outcomes_of_a_settled_market_is_ranked_at_their_portfolio_net_worth(
    session: AsyncSession,
) -> None:
    """ADR 0018's amendment: a settled market counts once, through its payout.

    One holder bought both outcomes of a market that then settled, and one
    outcome of a market that has not. Catches a refresh that values its rows
    without the settled branch (rows built without the `market_results` join,
    or valued with `liquidation_values_of` directly), which adds the settled
    shares at the frozen book on top of the payout already in the balance.
    It also catches a refresh whose statement is not ordered by holder first:
    this holder's two markets would arrive split around the others' rows.

    The last assertion is about the scenario, not the code: valued at the
    frozen book, this holder's figure would be different, so the equality
    above can tell the double count apart.
    """
    directory = Directory()
    both = await granted(session, directory, "both_sides")
    winner = await granted(session, directory, "winner_only")
    open_only = await granted(session, directory, "open_only")
    settled = await settled_by_trading(
        session,
        [(both, LOSER, UNPAID), (both, WINNER, PAID), (winner, WINNER, SMALL_QUANTITY)],
    )
    open_market = await market_at(session)
    await hold(session, open_market, user_id=both)
    await hold(session, open_market, user_id=open_only, outcome=1)

    await refresh_with(directory)

    await _assert_ranked_at_portfolio_net_worth(session, [both, winner, open_only])

    from service import portfolio  # noqa: PLC0415

    oracle = await portfolio.portfolio_of_user(session, both)
    frozen = sequence_values(
        await q_now(session, settled.market_id), SETTLED_B, [UNPAID, PAID]
    )
    double_counted = (
        oracle.balance + sum(frozen) + row(oracle, open_market.market_id, 0).value
    )
    assert double_counted != oracle.net_worth


Scenario = Callable[[AsyncSession, Directory], Awaitable[list[uuid.UUID]]]


async def _multi_outcome(session: AsyncSession, directory: Directory) -> list[uuid.UUID]:
    """A three-outcome market, held in overlapping subsets. Catches holders'
    rows grouped together: a statement not ordered by holder first, or a
    position joined to a holder that is not the row's own."""
    market = await market_at(session, outcomes=3)
    first = await granted(session, directory, "holds_0_and_2")
    second = await granted(session, directory, "holds_1")
    third = await granted(session, directory, "holds_0_and_1")
    await hold(session, market, user_id=first, outcome=0)
    await hold(session, market, user_id=first, outcome=2, quantity=Decimal("13.3333"))
    await hold(session, market, user_id=second, outcome=1, quantity=Decimal("41.0000"))
    await hold(session, market, user_id=third, outcome=0, quantity=Decimal("13.3333"))
    await hold(session, market, user_id=third, outcome=1, quantity=Decimal("13.3333"))
    return [first, second, third]


async def _settled(session: AsyncSession, directory: Directory) -> list[uuid.UUID]:
    """A winner, a loser and a holder of both. Catches the refresh valuing a
    settled market at its frozen book instead of through the settled branch."""
    winner = await granted(session, directory, "winner")
    loser = await granted(session, directory, "loser")
    both = await granted(session, directory, "both")
    await settled_by_trading(
        session,
        [
            (winner, WINNER, SMALL_QUANTITY),
            (loser, LOSER, UNPAID),
            (both, LOSER, SMALL_QUANTITY),
            (both, WINNER, PAID),
        ],
    )
    return [winner, loser, both]


async def _voided(session: AsyncSession, directory: Directory) -> list[uuid.UUID]:
    """A market traded through the real trade path and then voided: a holder
    of 12 of outcome 1, a trader who bought 4 of outcome 0 and sold 2, and a
    holder of outcome 0. #221 and ADR 0025's "Tests that matter": "the
    leaderboard's net worth is unchanged by the join". The refresh calls
    `valuation_of` with no `refunds`, so its voided rows read only the
    fallback: catches a refresh that fails while anyone holds a voided market,
    and one that values that market at its frozen book."""
    upstream = VoidUpstream()
    await open_book(session, upstream)
    holder = await granted(session, directory, "voided_holder")
    seller = await granted(session, directory, "voided_seller")
    other_side = await granted(session, directory, "voided_other_side")
    await trade(session, upstream, holder, outcome=1, side="buy", quantity="12")
    await trade(session, upstream, seller, outcome=0, side="buy", quantity="4")
    await trade(session, upstream, seller, outcome=0, side="sell", quantity="2")
    await trade(session, upstream, other_side, outcome=0, side="buy", quantity="5")
    upstream.closes()
    await void(session, upstream)
    return [holder, seller, other_side]


async def _no_positions(session: AsyncSession, directory: Directory) -> list[uuid.UUID]:
    """Three holders with nothing but their grant, beside one who trades.
    Catches an inner join from the accounts to their positions, which drops a
    holder who holds nothing, and a balance subquery not correlated to its
    account, which gives every holder the sum of every USER balance."""
    market = await market_at(session)
    trader = await granted(session, directory, "the_trader")
    await hold(session, market, user_id=trader)
    idle = [await granted(session, directory, f"idle_{n}") for n in range(3)]
    return [trader, *idle]


@pytest.mark.parametrize(
    "scenario",
    [_multi_outcome, _settled, _voided, _no_positions],
    ids=["multi-outcome", "settled", "voided", "no-positions"],
)
async def test_every_ranked_user_s_net_worth_is_their_portfolio_s(
    session: AsyncSession, scenario: Scenario
) -> None:
    """"The refresh values holders through the portfolio's valuation, and the
    portfolio keeps its one-statement read". The refresh also asks auth about
    exactly the USER account holders, so a statement whose anchor took in the
    PLATFORM or a market's pool account is caught here too.

    The dust case is not here: every mistake it could catch lives inside
    `valuation_of`, which moves the oracle with it, and no mistake in the
    refresh's own code fails it alone. `test_portfolio.py` holds it.
    """
    directory = Directory()
    holders = await scenario(session, directory)

    await refresh_with(directory)

    await _assert_ranked_at_portfolio_net_worth(session, holders)
    assert directory.asked_ids == set(holders)
    assert set((await newest()).by_user()) == set(holders)


async def test_each_holder_is_valued_with_their_own_positions(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`held_outcomes_query` gets the holder as a column of the refresh's
    statement, not a value. Catches the refresh passing one user's id (or a
    literal of it) for everybody: all three holders would be valued with that
    user's positions. The equality half catches the same thing by its effect,
    and also a scalar subquery standing in for the column that is not
    correlated to the row."""
    from service import valuation  # noqa: PLC0415

    directory = Directory()
    market = await market_at(session)
    first = await granted(session, directory, "first_holder")
    second = await granted(session, directory, "second_holder")
    third = await granted(session, directory, "third_holder")
    await hold(session, market, user_id=first)
    await hold(session, market, user_id=second, outcome=1, quantity=Decimal("41.0000"))
    await hold(session, market, user_id=third, quantity=Decimal("13.3333"))

    real = valuation.held_outcomes_query
    holder_ids: list[object] = []

    def spy(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        holder_ids.append(kwargs["holder_id"])
        return real(*args, **kwargs)

    monkeypatch.setattr(valuation, "held_outcomes_query", spy)
    await refresh_with(directory)
    monkeypatch.setattr(valuation, "held_outcomes_query", real)

    assert holder_ids, "the refresh never called valuation.held_outcomes_query"
    for holder_id in holder_ids:
        assert isinstance(holder_id, ColumnElement), f"holder_id is {holder_id!r}"
        assert not isinstance(holder_id, BindParameter), f"holder_id is {holder_id!r}"
    await _assert_ranked_at_portfolio_net_worth(session, [first, second, third])


# =========================================================================
# Ranks
# =========================================================================
@pytest.mark.parametrize(
    ("tied", "expected"),
    [
        (["Bob", "alice"], ["alice", "Bob"]),
        # Python's code-point order. `en_US.utf8` ignores the punctuation at
        # first level and would put `abc` first.
        (["abc", "ab_d", "ab-d"], ["ab-d", "ab_d", "abc"]),
    ],
    ids=["case", "punctuation"],
)
async def test_equal_net_worths_rank_by_lower_username(
    session: AsyncSession, tied: list[str], expected: list[str]
) -> None:
    """"Ranks run 1..N by net worth descending, then `lower(username)`
    ascending, decided in Python at refresh".

    The tied users hold only their grant. Their ids run the opposite way to
    the expected order, so a tie broken by `user_id` fails. `aaron` trades
    once, is worth less, and sorts first by name, so ranking by name alone or
    by ascending net worth fails too. Catches a case-sensitive sort and an
    `ORDER BY username` left to the database's collation.
    """
    directory = Directory()
    ids = sorted((uuid.uuid4() for _ in expected), reverse=True)
    user_by_name = dict(zip(expected, ids, strict=True))
    for username in tied:
        await fund(session, user_by_name[username])
        directory.name(user_by_name[username], username)
    aaron = await granted(session, directory, "aaron")
    await hold(session, await market_at(session), user_id=aaron)

    await refresh_with(directory)

    worths = await net_worths_from_portfolio(session, [*ids, aaron])
    assert len({worths[user_id] for user_id in ids}) == 1, "the tied users are not tied"
    assert worths[aaron] < worths[ids[0]]
    current = await newest()
    assert [(entry.rank, entry.username) for entry in current.entries] == list(
        enumerate([*expected, "aaron"], start=1)
    )
    assert current.ranked_users == len(expected) + 1


async def test_a_suspended_holder_is_left_out_and_the_ranks_close_up(
    session: AsyncSession,
) -> None:
    """"A suspended account is left out of the ranking, and ranks close up
    with no gap", and "Every ledger `USER` account holder is ranked,
    administrators included; nobody else is".

    The suspended holder would be third of four. Catches no filter, ranks
    numbered before the filter (1, 2, 4), and `ranked_users` counting the
    suspended holder.

    The administrator is one of the holders, granted the way their own
    balance read grants them. The ledger cannot see roles, so nothing in
    today's refresh can leave them out: this pins "administrators included"
    against a future role filter.
    """
    from service import ledger_service  # noqa: PLC0415

    directory = Directory()
    administrator = uuid.uuid4()
    await ledger_service.balance_of_user(session, administrator)
    await session.commit()
    directory.name(administrator, "ernest_admin")
    winner = await granted(session, directory, "winner")
    loser = await granted(session, directory, "loser")
    suspended = await granted(session, directory, "suspended_user")
    directory.name(suspended, "suspended_user", suspended=True)
    await settled_by_trading(
        session, [(winner, WINNER, SMALL_QUANTITY), (loser, LOSER, SMALL_QUANTITY)]
    )
    await hold(session, await market_at(session), user_id=suspended)

    await refresh_with(directory)

    worths = await net_worths_from_portfolio(
        session, [winner, administrator, suspended, loser]
    )
    assert worths[winner] > worths[administrator] > worths[suspended] > worths[loser]
    current = await newest()
    assert [(entry.rank, entry.user_id) for entry in current.entries] == [
        (1, winner),
        (2, administrator),
        (3, loser),
    ]
    assert current.ranked_users == 3


async def test_a_holder_auth_does_not_name_is_skipped_with_a_warning(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """"An account id that auth's lookup does not return ... is left out and
    logged at WARNING, and the refresh continues." Catches a refresh that
    fails on the missing id, one that skips it silently, and one that ranks
    it under a placeholder name."""
    directory = Directory()
    market = await market_at(session)
    first = await granted(session, directory, "first_holder")
    second = await granted(session, directory, "second_holder")
    missing = await granted(session, directory, "never_named")
    del directory.users[missing]
    await hold(session, market, user_id=first)
    await hold(session, market, user_id=missing, outcome=1)
    caplog.set_level(logging.WARNING, logger=_LEADERBOARD_LOGGER)

    await refresh_with(directory)

    current = await newest()
    assert {entry.user_id for entry in current.entries} == {first, second}
    assert [entry.rank for entry in current.entries] == [1, 2]
    assert current.ranked_users == 2
    warnings = [
        record
        for record in caplog.records
        if record.name == _LEADERBOARD_LOGGER and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert str(missing) in warnings[0].getMessage()


# =========================================================================
# What a refresh writes, and when it stamps it
# =========================================================================
async def _account_count(session: AsyncSession) -> int:
    return (
        await session.execute(select(func.count()).select_from(entities().Account))
    ).scalar_one()


async def test_a_refresh_writes_nothing_outside_the_leaderboard_and_locks_no_row(
    session: AsyncSession,
) -> None:
    """[L-3] #40: a refresh cannot block a trade. "Trades never take this
    lock, and the refresh takes no row lock on any trade table." Catches a
    refresh that posts, mints, opens a book or values holders through
    `portfolio_of_user` where that writes, and one that reads with
    `FOR UPDATE`.

    Not caught: a refresh that calls `grants.ensure_granted` on holders who
    are already granted. Every USER account is created by the grant, so that
    call writes nothing and locks nothing here.
    """
    directory = Directory()
    first = await granted(session, directory, "first_holder")
    second = await granted(session, directory, "second_holder")
    settled = await settled_by_trading(
        session, [(first, WINNER, SMALL_QUANTITY), (second, LOSER, SMALL_QUANTITY)]
    )
    open_market = await market_at(session)
    await hold(session, open_market, user_id=first)
    markets = [settled.market_id, open_market.market_id]
    before = (
        [await footprint(session, market_id) for market_id in markets],
        await ledger_counts(session),
        await _account_count(session),
    )
    await session.commit()

    with capture_sql() as statements:
        await refresh_with(directory)

    async with session_factory()() as other:
        after = (
            [await footprint(other, market_id) for market_id in markets],
            await ledger_counts(other),
            await _account_count(other),
        )
    assert after == before
    written = writes(statements)
    assert written, "the refresh wrote no generation, so this test proves nothing"
    assert [s for s in written if "leaderboard_" not in s.lower()] == []
    assert locks(statements) == []


@pytest.mark.parametrize(
    "failure",
    [httpx.Response(503, text="Service Unavailable"), httpx.ReadTimeout("auth is slow")],
    ids=["503", "timeout"],
)
async def test_a_refresh_that_cannot_name_its_holders_writes_nothing(
    session: AsyncSession, failure: httpx.Response | Exception
) -> None:
    """"Any failure means nothing is written ... The previous generation stays
    current." Catches a refresh that treats a failed lookup as an empty name
    map and writes a generation with no entries, or prunes before it knows
    it can write."""
    directory = Directory()
    market = await market_at(session)
    first = await granted(session, directory, "first_holder")
    await granted(session, directory, "second_holder")
    await hold(session, market, user_id=first)
    await refresh_with(directory, clock=fixed_clock(at(0)))
    before = await generations()
    directory.fail(failure)

    with pytest.raises(errors().AuthUnavailable):
        await refresh_with(directory, clock=fixed_clock(at(60)))

    assert await generations() == before


async def test_a_damaged_book_fails_the_whole_refresh_and_writes_nothing(
    session: AsyncSession,
) -> None:
    """"Any failure means nothing is written ... The previous generation stays
    current." One holder's market has `q` set below their holding, the damage
    `test_settled_portfolio.py` uses: `strip_outcomes` cannot reach a held
    market, whose positions reference its outcome rows. Two holders of a
    healthy market are still there to rank. Catches a refresh that skips the
    holder whose book the valuation refuses and ranks the rest."""
    directory = Directory()
    healthy = await market_at(session)
    damaged = await market_at(session)
    first = await granted(session, directory, "first_healthy")
    second = await granted(session, directory, "second_healthy")
    on_damaged = await granted(session, directory, "on_damaged_book")
    await hold(session, healthy, user_id=first)
    await hold(session, healthy, user_id=second, outcome=1)
    await hold(session, damaged, user_id=on_damaged)
    await refresh_with(directory, clock=fixed_clock(at(0)))
    before = await generations()
    assert len(before) == 1
    await set_q(session, damaged.market_id, [ZERO])

    with pytest.raises(errors().MarketBookIncomplete):
        await refresh_with(directory, clock=fixed_clock(at(60)))

    assert await generations() == before


async def test_a_refresh_with_nobody_to_rank_still_writes_an_empty_generation(
    session: AsyncSession,
) -> None:
    """"A suspended account is left out of the ranking", taken to its end:
    every holder is suspended, so the ranking is empty. The generation is
    still written, stamped, and counts nobody. Catches an insert that sends
    the entries with no rows to send, which fails the refresh."""
    directory = Directory()
    market = await market_at(session)
    first = await granted(session, directory, "first_suspended")
    second = await granted(session, directory, "second_suspended")
    directory.name(first, "first_suspended", suspended=True)
    directory.name(second, "second_suspended", suspended=True)
    await hold(session, market, user_id=first)

    await refresh_with(directory, clock=fixed_clock(at(0)))

    [generation] = await generations()
    assert generation.ranked_users == 0
    assert generation.entries == ()
    assert generation.computed_at == at(0)


async def test_a_refresh_keeps_only_its_own_generation_and_the_one_before(
    session: AsyncSession,
) -> None:
    """"A refresh inserts one generation and all of its entries in one
    transaction, then deletes every generation except the new one and the one
    before it." Catches a prune that keeps only the newest, one that prunes
    nothing, one that picks "the one before" by id rather than `computed_at`,
    and one that deletes entries but leaves their generation."""
    directory = Directory()
    market = await market_at(session)
    first = await granted(session, directory, "first_holder")
    second = await granted(session, directory, "second_holder")
    await hold(session, market, user_id=first)
    await refresh_with(directory, clock=fixed_clock(at(0)))
    [oldest] = await generations()
    await hold(session, market, user_id=second, outcome=1)
    await refresh_with(directory, clock=fixed_clock(at(60)))
    await hold(session, market, user_id=first, outcome=1, quantity=Decimal("13.3333"))

    await refresh_with(directory, clock=fixed_clock(at(120)))

    remaining = await generations()
    assert [generation.computed_at for generation in remaining] == [at(60), at(120)]
    assert all(generation.is_whole for generation in remaining)
    entry = entities().LeaderboardEntry
    async with session_factory()() as other:
        orphans = (
            await other.execute(
                select(func.count())
                .select_from(entry)
                .where(entry.generation_id == oldest.id)
            )
        ).scalar_one()
    assert orphans == 0


async def test_computed_at_is_the_clock_read_once_before_the_valuation(
    session: AsyncSession,
) -> None:
    """"A refresh reads `datetime.now(UTC)` once, before its valuation
    statement, and stamps the generation with it." Catches a stamp read after
    the valuation, a stamp taken from the database (`now()`,
    `clock_timestamp()`), and a second clock read for the guard."""
    directory = Directory()
    market = await market_at(session)
    first = await granted(session, directory, "first_holder")
    await granted(session, directory, "second_holder")
    await hold(session, market, user_id=first)
    await session.commit()
    stamp = at(0)
    clock_marker = "-- the clock was read here --"
    reads: list[object] = []

    with capture_sql() as statements:

        def clock():  # noqa: ANN202
            reads.append(stamp)
            statements.append(clock_marker)
            return stamp

        await refresh_with(directory, clock=clock)

    assert len(reads) == 1
    valuation_reads = [
        index for index, s in enumerate(statements) if "ledger.positions" in s.lower()
    ]
    assert valuation_reads, "no statement read ledger.positions"
    assert statements.index(clock_marker) < valuation_reads[0]
    [generation] = await generations()
    assert generation.computed_at == stamp


async def test_the_auth_lookup_holds_no_connection_and_no_transaction(
    session: AsyncSession,
) -> None:
    """ADR 0021: "The refresh reads and values the holders in one
    transaction, ends it, then calls auth." Checked at the call, from the
    pool and from Postgres, as `test_account_status.py` does. Catches a
    lookup made inside the valuation's transaction or the write's."""
    directory = Directory()
    market = await market_at(session)
    first = await granted(session, directory, "first_holder")
    await granted(session, directory, "second_holder")
    await hold(session, market, user_id=first)
    await session.close()
    pool = get_engine().pool
    seen: list[dict[str, int]] = []

    async def probe() -> None:
        checked_out = pool.checkedout()
        seen.append(
            {
                "pooled connections checked out": checked_out,
                "backends idle in transaction": await idle_in_transaction(),
            }
        )

    directory.on_request = probe

    await refresh_with(directory)

    assert seen == [
        {"pooled connections checked out": 0, "backends idle in transaction": 0}
    ]
    assert len(await generations()) == 1
