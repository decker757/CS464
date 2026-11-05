# ADR 0031: A per-market trading engine: one owner per book, a fenced lease, group commits

- **Status:** Proposed, 2026-11-05. Not accepted and not built. It is
  accepted when the trigger under "When to take it" is met and the work is
  planned, and not before.
- **Date:** 2026-11-05
- **Decided by:** Ernest, on 2026-11-05, approving #640's design: build the
  trade-path fixes, and write this step down rather than take it.
- **Affects:** [5.4] #640, #641, #642, #643, #645, [5.4] #20, [T-2] #22,
  [T-6] #26, [T-8] #28, [3.4] #12, ADR 0005, ADR 0009, ADR 0010, ADR 0015,
  ADR 0019, ADR 0024, ADR 0025, ADR 0026, ADR 0027
- **Implemented in:** nothing. This record describes the step after #640,
  #641, #642 and #643, and the trigger that would make it worth taking.

## Context

#20's production load test on 2026-11-04 measured a trade p95 of **5.45 s**
with 50 traders on one market and **4.52 s** with the same 50 spread across
five markets. Both 1-vCPU app servers sat at 100% CPU and the database at 45%.
On the one-market run about 35 attempts were refused `quote_stale` for every
trade that committed (4,427 against 126); on the five-market run, about 7
(5,091 against 738). `docs/load-test.md`, "Results".

#640 and the three tickets beside it remove the causes that measurement
found. Price limits replace exact-version quotes, so a queued trade is no
longer refused because another trade landed first. The ledger stops calling
auth and market_service on every trade. Tracing drops to 10%. And #641 takes
the locked section of a warm trade from **21 statements to 13**, the floor
the ORM allows without a second write path for money.

What is left after those is the ceiling this record is about. It has two
parts:

- **CPU per trade.** About 40 to 50 ms of pricing, pydantic, SQLAlchemy and
  asyncpg on one vCPU. #20's script has each of 50 traders buy and sell back
  about once a second, which is about 75 trades a second. At 40 to 50 ms
  that is 3 to 3.75 vCPU of ledger for the trades alone. The 2026-11-05
  analysis's figure of 4 to 5 also counts the snapshot read the script makes
  before each buy: about 38 a second at an estimated 15 ms each (not yet
  measured on its own), about 0.6 vCPU, plus headroom so the ledger is not
  run at 100%. Each app server has one vCPU, shared by five services and the
  gateway.
- **One row lock per market.** Every trade on a market takes that market's
  `ledger.market_books` row `FOR UPDATE` (ADR 0015) and holds it for 13 round
  trips to Postgres and the Python between them. One market's throughput is
  one over that hold time, whatever the number of servers. On 2026-11-04,
  before any of these fixes and under exact-version quotes, a laptop running
  two of every service committed 16 to 19 trades a second on one market, and
  with 50 traders more than half the attempts gave up as stale. No post-fix
  figure exists yet.

The first part is bought with vCPU or a leaner write path. The second is not
bought at all while the lock is a database row lock taken once per trade.

A queue in front of the trade does not help with either. Nothing on the trade
path can be deferred: the price publish already runs after the commit
(ADR 0010), the cascade of resting orders has to run inside the trade that
moved the price (ADR 0024), and the trader is waiting for the answer. A queue
would only add a hop to work that has to happen in the same order anyway.

## Decision (proposed)

**Each market's book lives in memory in exactly one ledger process,** and that
process sequences every trade on it. Postgres stays the source of truth: the
memory is a cache of committed state plus the trades not yet committed.

**A lease on the book row says which process.** Three new columns on
`ledger.market_books`, all nullable or defaulted so the release before it
still runs (ADR 0026, "every migration must also work with the release before
it"):

    lease_owner       text         -- the owning replica's internal address
    lease_epoch       bigint       not null default 0
    lease_expires_at  timestamptz  -- read against clock_timestamp()

Nobody assigns markets. The replica that receives a trade for a market whose
lease is free or expired takes the lease itself, which bumps `lease_epoch`,
loads the book, and sequences the trade. It renews the lease on a timer well
inside its expiry. Every time comparison uses the database's clock, never a
container's.

**Every batch commit carries `WHERE lease_epoch = mine`. That is the fence.**
The batch's first statement is the UPDATE of the book row, with the fence in
its WHERE clause, and its row count is checked before `post_all` is called,
in the same transaction. That order also keeps ADR 0015's lock order: the
book row, then the accounts `post_all` locks. A lease alone does not stop a
process that paused for longer than its expiry, through a long garbage
collection or a network partition, from waking up and writing as if it still
owned the book. The fence does: the UPDATE matches no row, the process knows
it has lost the book, and it rolls back before `post_all` writes anything.

**One asyncio actor per owned market sequences trades.** It takes each trade,
buy, sell, order placement or cancel, in arrival order, runs the cascade
(ADR 0024), and prices it against the `q` in memory. A batch writer commits
what the actor has sequenced every few milliseconds, in one transaction,
through `posting.post_all`, which already takes a list of movements and
commits them once. The book row is written once per batch rather than once
per trade, so the lock that used to cost 13 round trips per trade costs a few
per batch.

**A response is held until its batch commits.** No trader is told a price
that a rollback could still undo. The price frame is published once per batch,
after the commit, as ADR 0010 already requires of every trade.

**A replica that does not own a market forwards the trade to the one that
does,** over an internal route on the owner's `lease_owner` address, which
the public never reaches: #640 has the gateway hide `/internal/`. A forward
that lands on a process that has since lost the lease is refused 503 rather
than forwarded again, so a request makes at most one hop.

**Settlement and void keep writing under the book lock, and bump the epoch.**
They are rare, they write the latch (the `market_results` row), and they stay
exactly as ADR 0019 and ADR 0025 describe them. The only change is one more
column in their UPDATE: `lease_epoch = lease_epoch + 1`. The engine's next
batch fails the fence, drops its memory, and reloads, and the reload reads the
latch and refuses every later trade. That is eviction: nothing has to message
the owner.

**Recovery is a reload.** The next replica to receive a trade for the market
takes the expired or released lease, as above, and reads `q`, `b`,
`state_version`, the positions and the open orders from Postgres, as it would
after a restart. A crash loses nothing that committed. The trades in flight
get 503, and their idempotency keys make the retry a replay of a trade that
committed or a clean first execution of one that did not.

## The hard part: the book is per market, the balance is not

The book is market-scoped, so it can live in one process. A balance is
user-scoped, and one user trades in markets owned by different processes. Two
owners cannot each see the other's spend, so **the overdraft check cannot move
into memory**, and stays where ADR 0009 put it: in Postgres, at batch commit,
under the account locks, taken one statement per account in ascending id order
across every account the batch names.

Before sequencing, the actor runs a cheap unlocked pre-check of the balance,
which refuses the obvious overdrafts early. It decides nothing. The check under
the lock decides.

**A refusal at commit rolls the whole batch back and re-sequences it without
the refused trade.** The usual cause is a concurrent spend by the same user in
another market. The trades sequenced after the refused one cannot simply be
kept: every one of them was priced on a `q` that included it. So the actor
restores the `q` from before the batch, prices the rest again, checks each
against the trader's own price limit (#640's `max_cost` or `min_proceeds`),
refuses any that no longer fit, and commits again. Because no response left
before the commit, nobody was told the first price.

`post_all` commits a batch whole or not at all, and that is what the engine
needs. An overdraft refusal already names its movement: `InsufficientFunds`
carries `movement_key`, so the actor knows which trade to drop. A key found
under the lock does not. When any key in the batch already exists, `post_all`
goes to its replay path, which raises `IdempotencyKeyReused` naming no key,
or `PendingWritesOnReplay` when the session holds pending writes, as it
always will under the engine because the fenced UPDATE comes first. The one
change the engine needs is for `post_all` to name the keys it found. The actor
then answers a found key whose fingerprint matches as a replay, with the
original result, refuses one whose fingerprint differs as reused, and
re-sequences the rest.

This and the fence are where a bug is a double spend. A batch that commits
past a refused overdraft pays out credits that do not exist; an owner that
writes without the fence prices on a `q` another owner has already moved.
Both need race tests that fail with the guard removed (ADR 0015's rule for a
race test) before anything else in the engine is written.

## Alternatives rejected

- **A queue in front of the trade.** It sequences trades without holding the
  book, so every trade still pays the full read of `q` and the row lock, plus
  the queue's own latency. It is the engine's ordering without the engine's
  saving.
- **Ownership per user instead of per market.** It puts the balance in one
  process and splits the book: every trade then names a user owned by one
  process and a market touched by many, so every trade spans two owners.
- **Redis as the lease store.** ADR 0010 made Redis a bus that may lose a
  message and whose failure the trade ignores, and ADR 0027 lets the login
  limits fail open when it is down. A lease decides who may write money. It
  belongs in the same database as the money, where the fence can be part of
  the commit it protects.
- **A stateful ledger without a fence.** A lease with an expiry and no fence
  is correct only while every process stops on time. One that does not stop,
  for any reason, writes over its successor's trades.

## Consequences

- **The ledger becomes stateful.** ADR 0026 was built around not saying that:
  "the servers hold no data, so any of them can be replaced or added". The
  mitigation is that the state is a cache of committed rows, and failover is
  a reload: losing a server costs its markets one lease expiry and one reload,
  and loses no committed trade.
- **The deploy's roll must release leases before stopping a replica.** A
  replica that is stopped holding leases leaves its markets refusing trades
  until each lease expires. `roll.sh` drains a server from the load balancer
  and then stops it; it would have to ask the ledger to finish its batches
  and release its leases, bumping each epoch, between the two.
- **A market's trades go to one process.** A market that alone needs more
  than one ledger process's CPU is the next ceiling, and this design does not
  lift it.
- **Latency gains a batch interval.** A trade waits up to a few milliseconds
  for its batch. Against a lock wait of seconds that is cheap; on an idle
  market it is pure cost.
- **Two more failure modes to watch:** forwards refused for a stale owner, and
  batches that fail the fence. Both are counted and alerted on, the way the
  close sweep falling behind is.

## The step before it

**A single-statement write path:** the whole trade as one data-modifying
common table expression, issued after the lock statement. It takes the locked
section from 13 statements to about 5 without leaving the database or adding
a process with state. #641 stopped at 13 because going lower takes the writes
out of the ORM, so that step has to replace the ORM writes on the trade path
rather than sit beside them as a second write path for money. It should be
taken and measured first. It is cheaper than this record, and it may move the
trigger out of reach.

## When to take it

When either of these is measured by #20's script, after #641 has landed:

- one market sustaining more than about **20 trades a second**, or
- the one-market lock-wait p95 above **200 ms**.

Below both, more vCPU and the single-statement write path are cheaper answers
to the same load.

## Size

Two to three weeks for one backend person. Five to eight pull requests, about
two thousand lines with the race tests. One migration, the lease columns.

## What it amends when accepted

- **ADR 0005:** the fast side of the seam, "slow-changing definition against
  fast-changing money", is still the ledger alone, but its processes are no
  longer interchangeable: one per market holds `q` and the positions in
  memory.
- **ADR 0009:** serialisation on the accounts is unchanged, and is taken once
  per batch over every account in it; one transaction carries many trades.
- **ADR 0015:** the read of the book that decides a trade is sequenced in
  memory and fenced at commit, rather than locked per trade. Every read that
  decides a balance is still locked.
- **ADR 0024:** the cascade still runs inside the trade that moved the price,
  in the actor, and commits in the same batch.
- **ADR 0026:** a server holds leases, and the roll releases them before it
  stops one.
