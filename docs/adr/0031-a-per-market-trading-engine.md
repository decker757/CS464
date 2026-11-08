# ADR 0031: A per-market trading engine: one owner per book, a fenced lease, group commits

- **Status:** Proposed, 2026-11-05. Not accepted and not built. Its trigger
  (under "When to take it") was met on 2026-11-07. It stays Proposed until
  the single-statement write path below has been built and measured, and the
  engine's work is planned.
- **Date:** 2026-11-05. The 2026-11-07 measurements and the trigger's
  reading added 2026-11-08.
- **Decided by:** Ernest, on 2026-11-05, approving #640's design: build the
  trade-path fixes, and write this step down rather than take it.
- **Affects:** [5.4] #640, #641, #642, #643, #645, #667, #668, [5.4] #20,
  [T-2] #22, [T-6] #26, [T-8] #28, [3.4] #12, ADR 0005, ADR 0009, ADR 0010,
  ADR 0015, ADR 0019, ADR 0024, ADR 0025, ADR 0026, ADR 0027, ADR 0029,
  ADR 0030
- **Implemented in:** nothing. This record describes the step after #640,
  #641, #642 and #643, and the trigger that makes it worth taking.

## Context

#20's production load test on 2026-11-04 measured a trade p95 of **5.45 s**
with 50 traders on one market and **4.52 s** with the same 50 spread across
five markets. Both 1-vCPU app servers sat at 100% CPU and the database at 45%.
On the one-market run about 35 attempts were refused `quote_stale` for every
trade that committed (4,427 against 126); on the five-market run, about 7
(5,091 against 738). The dashboard's book-lock wait p95 was 4.5 s.
`docs/load-test.md`, "Results", and #640.

#640 and the three tickets beside it removed the causes that measurement
found. Price limits replace exact-version quotes (ADR 0029), so a queued trade
is no longer refused because another trade landed first. The ledger stopped
calling auth and market_service on every trade (ADR 0030). Tracing dropped to
one request in ten. #641 took the locked section of a warm trade from **22
statements on a buy and 21 on a sell to 13**, the floor the ORM allows without
a second write path for money (DECISIONS.md, "The locked section of a trade is
budgeted at 13 statements, and no deciding read goes in the lock statement").
DigitalOcean's connection pools let a third server start (#660).

All of that shipped in one release, `main` at 003e3ab1, on 2026-11-07. The
same script, on the same two 1-vCPU servers, 50 traders for five minutes:

| Run | Trades committed | Trade p95 | Errors | Refused at the price |
| --- | --- | --- | --- | --- |
| One market, 2026-11-04 | 126 (0.4 a second) | 5.45 s | 0% | 4,427 stale quotes |
| One market, 2026-11-07 | 3,568 (12 a second) | 4.72 s | 0% | 38 beyond the limit |
| Five markets, 2026-11-04 | 738 (2.5 a second) | 4.52 s | 0.8% | 5,091 stale quotes |
| Five markets, 2026-11-07 | 6,394 (21 a second) | 2.60 s | 0% | none |

The throughput rose 28 times on one market and 9 times on five. The p95 did
not come down to #640's target of 500 ms. What is left is the ceiling this
record is about. It has two parts.

- **One row lock per market.** Every trade on a market takes that market's
  `ledger.market_books` row `FOR UPDATE` (ADR 0015) and holds it for 13 round
  trips to Postgres and the Python between them. 3,568 trades in 300 s is 12
  a second, so each trade held the lock about **84 ms**, about 6.5 ms per
  statement with the Python between them. With 50 traders queued on it, the
  last one waits about 50 × 84 ms = 4.2 s, which is the p95 the run measured.
  One market's throughput is one over the hold time, whatever the number of
  servers.
- **CPU per trade.** The 2026-11-05 analysis estimated about 40 to 50 ms of
  pricing, pydantic, SQLAlchemy and asyncpg in the ledger per trade, on one
  vCPU. The five-market run puts an upper bound on everything together: two
  servers for 300 s is 600 vCPU-seconds, over 6,394 trades, so **at most
  94 ms of app-server CPU per committed trade**, counting the snapshot read
  before each buy, the gateway and the other four services. Each server has
  one vCPU, shared by five services, the gateway and Grafana Alloy. The
  snapshot read takes no lock, and its p95 was 1.77 s on five markets: the
  servers were queueing for CPU. #20's script has each of 50 traders buy and
  sell back about once a second, about 75 trades a second, which at 40 to
  50 ms is 3 to 3.75 vCPU of ledger for the trades alone.

The lock queue also holds database connections. A transaction waiting for
the book lock keeps its pooled server connection, so during the one-market
run the ledger's 14 server connections filled with waiting trades and one
server's readiness check, a `SELECT 1`, timed out behind them (#667). Nothing
bounds how long a trade waits for the lock: #669 asked for a `lock_timeout`
and was closed as not planned.

The CPU is bought with vCPU or a leaner write path. The lock is not bought at
all while it is a database row lock taken once per trade.

A queue in front of the trade does not help with either. Nothing on the trade
path can be deferred: the price publish already runs after the commit
(ADR 0010), the cascade of resting orders has to run inside the trade that
moved the price (ADR 0024), and the trader is waiting for the answer. A queue
would only add a hop to work that has to happen in the same order anyway.

## Decision (proposed)

Two things must stay true before anything about speed, and each is a double
spend if it breaks. **No batch commits on a book that another process has
moved since it loaded it.** That is the fence. **No batch commits a debit
that the user's balance, across every market, cannot cover.** That is the
overdraft check, and it stays in Postgres. The rest of this section is how
the engine gets its speed without giving up either.

**Each market's book lives in memory in exactly one ledger process,** and that
process sequences every trade on it: a single writer per market. Postgres
stays the source of truth: the memory is a cache of committed state plus the
trades not yet committed.

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
inside its expiry, and the renewal is fenced too: it matches only its own
epoch. Every time comparison uses the database's clock, never a container's.
The lease is a row, not a session-level lock, so it works through
transaction-mode pools (`docs/deployment.md`, "What transaction mode rules
out").

**Every batch commit carries `WHERE lease_epoch = mine`. That is the fence.**
The batch's first statement is the UPDATE of the book row, with the fence in
its WHERE clause, and its row count is checked before `posting.post_all` is
called, in the same transaction. That order also keeps ADR 0015's lock order:
the book row, then the accounts `post_all` locks. A lease alone does not stop
a process that paused for longer than its expiry, through a long garbage
collection or a network partition, from waking up and writing as if it still
owned the book. The fence does: the UPDATE matches no row, the process knows
it has lost the book, and it rolls back before `post_all` writes anything.
Under READ COMMITTED an UPDATE that waited for the row checks its WHERE again
against the newest version, so a writer that bumped the epoch while the batch
waited still fails the batch.

**One asyncio actor per owned market sequences trades.** It takes each trade,
buy, sell, order placement or cancel, in arrival order, runs the cascade
(ADR 0024), and prices it against the `q` in memory. A batch writer commits
what the actor has sequenced every few milliseconds, in one transaction,
through `posting.post_all`, which already takes a list of movements and
commits them once. The book row is written once per batch rather than once
per trade, so the lock that costs 13 round trips per trade costs a few per
batch, and a hot market holds one database connection, not one per waiting
trader.

**The checks that decide still run under the book lock, at commit.** The
fenced UPDATE returns `clock_timestamp()`, and a batch whose commit falls
after the market's `close_time` refuses its trades: ADR 0030's close check,
moved from the context statement to the batch's first statement, and still on
the database's clock. The suspension and early-close checks from ADR 0030's
local copy run before a trade is sequenced, as they run before the lock
today.

**A response is held until its batch commits.** No trader is told a price
that a rollback could still undo. The price frame is published once per batch,
after the commit, as ADR 0010 already requires of every trade.

**A replica that does not own a market forwards the trade to the one that
does,** over an internal route on the owner's `lease_owner` address, which
the public never reaches: the gateway answers 404 for `/internal/` (ADR 0030).
A forward that lands on a process that has since lost the lease is refused 503
rather than forwarded again, so a request makes at most one hop.

**Settlement and void keep writing under the book lock, and bump the epoch.**
They are rare, they write the latch (the `market_results` row), and they stay
exactly as ADR 0019 and ADR 0025 describe them. The only change is one more
column in their UPDATE: `lease_epoch = lease_epoch + 1`. The engine's next
batch fails the fence, drops its memory, and reloads, and the reload reads the
latch and refuses every later trade. That is eviction: nothing has to message
the owner. If the batch took the book lock first, it commits first, and the
settlement then pays positions that include it, as today.

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
across every account the batch names. Two batches on different markets that
debit the same user queue on that user's account row, and the second sees the
first's committed entries before it checks.

Before sequencing, the actor runs a cheap unlocked pre-check of the balance,
which refuses the obvious overdrafts early. It decides nothing. The check under
the lock decides.

**A refusal at commit rolls the whole batch back and re-sequences it without
the refused trade.** The usual cause is a concurrent spend by the same user in
another market. The trades sequenced after the refused one cannot simply be
kept: every one of them was priced on a `q` that included it. So the actor
restores the `q` from before the batch, prices the rest again, checks each
against the trader's own price limit (ADR 0029's `max_cost` or
`min_proceeds`), refuses any that no longer fit, and commits again. Because no
response left before the commit, nobody was told the first price.

`post_all` commits a batch whole or not at all, and that is what the engine
needs. An overdraft refusal already names what it refused: `InsufficientFunds`
carries `account_id`, and `movement_key` for a refusal after a debit, so the
actor knows which trade to drop. A refusal at the start of the batch, an
account already below its reservation, has no `movement_key`, and every trade
in the batch that debits that account is dropped. A key found under the lock
does not name itself. When any key in the batch already exists, `post_all`
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
- **Optimistic concurrency on the book instead of a lock or an owner:** a
  version check at commit, and a retry on a mismatch. On one busy market
  every retry loses to the trade that just committed. 2026-11-04's
  exact-version quotes were this in all but name, and cost 35 refusals per
  committed trade. ADR 0015 rejected `SERIALIZABLE` and a version column for
  the same shape of cost: a refusal that every caller has to retry.
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
- **A market's trades go to one process, so one market's ceiling becomes that
  process's CPU.** At the estimated 40 to 50 ms of ledger CPU per trade, one
  vCPU sequences 20 to 25 trades a second: about twice the lock's 12, not
  ten times. The rest has to come from what batching removes from each
  trade's CPU, the per-trade SQLAlchemy and asyncpg work (19 to 28% and 10%
  of self time in the 2026-11-04 profile), and from a bigger server. A market
  that needs more than one process's CPU is the next ceiling, and this design
  does not lift it.
- **Latency gains a batch interval.** A trade waits up to a few milliseconds
  for its batch. Against a lock wait of seconds that is cheap; on an idle
  market it is pure cost.
- **Two more failure modes to watch:** forwards refused for a stale owner, and
  batches that fail the fence. Both are counted and alerted on, the way the
  close sweep falling behind is.

## The step before it

**A single-statement write path:** everything a trade writes, as one
data-modifying common table expression, issued after the account locks. The
locked section becomes about 5 statements: the book lock, the context
statement, one lock per account (ADR 0009 keeps those as one statement each,
because one `FOR UPDATE` over several rows can deadlock), and the one write.
It takes the hold from 13 round trips to 5 without leaving the database or
adding a process with state. #641 stopped at 13 because going lower takes the
writes out of the ORM, so this step has to replace the ORM writes in
`post_all` rather than sit beside them as a second write path for money. The
overdraft check moves into that statement too, and has to refuse by writing
nothing and naming the movement, so `InsufficientFunds` keeps its fields.

If the hold shrinks in proportion, it goes from 84 ms to about 32 ms, about
30 trades a second on one market. That is an estimate: the pricing and the
Python around the statements do not shrink, so the real figure will be worse.
It should be built and measured first, because it is cheaper than this
record, and a leaner `post_all` is what the engine's batch writer would call
anyway.

It is not expected to clear the trigger on #20's one-market shape. Fifty
traders ask one market for about 75 trades a second. A per-trade hold above
about 13 ms cannot serve that, so the queue fills, and the wait is the
traders times the hold: about 50 × 32 ms = 1.6 s, still far above 200 ms.

## When to take it

When either of these is measured by #20's script, after #641 has landed:

- one market sustaining more than about **20 trades a second**, or
- the one-market lock-wait p95 above **200 ms**.

Below both, more vCPU and the single-statement write path are cheaper answers
to the same load.

**The second has fired.** #641 landed on 2026-11-07, and the one-market run
after it put the trade p95 at 4.72 s. By the arithmetic under Context almost
all of that is the lock queue: about 4.2 s, twenty times the threshold. The
dashboard's book-lock wait panel was not written down for that run, so this
is a derived figure; the run before the fixes measured 4.5 s on the same
panel. The first criterion cannot be read off the same run: the lock caps
one market at 12 trades a second, so demand above 20 shows up as wait, not
as throughput, which is why the second criterion exists.

It stays Proposed. The single-statement write path is cheaper and comes
first, and #668's one-, two- and three-server runs will say how much of the
84 ms hold is CPU starvation rather than round trips.

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
  in the actor, and commits in the same batch. A fill still commits if and
  only if the trade that caused it does.
- **ADR 0026:** a server holds leases, and the roll releases them before it
  stops one.

Unchanged in what they decide, and touched in how: ADR 0019 and ADR 0025,
whose book-lock UPDATE also bumps the epoch; ADR 0030, whose deciding close
check moves into the batch's fenced statement, still under the book lock and
on `clock_timestamp()`; and ADR 0029, whose limit is checked again for a
re-sequenced trade.
