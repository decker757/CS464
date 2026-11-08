# ADR 0031: A per-market trading engine: one owner per book, a fenced lease, group commits

- **Status:** Proposed, 2026-11-05. Not accepted and not built. Its trigger
  (under "When to take it") was met on 2026-11-07, on a derived figure.
  It stays Proposed until
  the single-statement write path below has been built and measured, and the
  engine's work is planned.
- **Date:** 2026-11-05. The 2026-11-07 measurements and the trigger's
  reading added 2026-11-08. Edited in place the same day after its review:
  the fence also checks `state_version`, the rollout is expand then
  contract, idempotency keeps today's request match, and every capacity
  figure says whether it is measured, derived or estimated.
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
  statement with the Python between them. That is derived, not measured: it
  is one over the throughput, which assumes the lock was never idle. One
  market's throughput is one over the hold time, whatever the number of
  servers.
- **How many trades queue on it.** Not 50. #20's script loops a snapshot, a
  buy, a sell and a one-second sleep, so each trader has at most one trade
  in flight, and none while it sleeps or reads. By Little's law about **34
  trades were in flight on average**: 3,606 attempts in 300 s (3,568
  committed and 38 refused at their limit) is 12 a second, times the
  measured mean trade latency of 2.85 s (the run's k6 summary). 50 × 84 ms =
  4.2 s is what the last trader would wait if all 50 queued at once, so it is
  an upper bound, not the p95. The measured p95 was 4.72 s, and it holds
  more than the lock queue: the snapshot read, which takes no lock, had a
  p95 of 2.81 s on the same run.
- **CPU per trade.** #645's body, from the 2026-11-05 design, estimates
  about 40 to 50 ms of pricing, pydantic, SQLAlchemy and asyncpg in the
  ledger per trade after the fixes, on one vCPU. It shows no working, and
  nothing has measured the ledger's CPU per trade on its own, so it is an
  estimate. The five-market run puts a measured upper bound on everything
  together: two servers for 300 s is 600 vCPU-seconds, over 6,394 trades, so
  **at most 94 ms of app-server CPU per committed trade**, counting the
  snapshot read before each buy, the gateway and the other four services.
  Each server has one vCPU, shared by five services, the gateway and Grafana
  Alloy. The snapshot read takes no lock, and its p95 was 1.77 s on five
  markets: the servers were queueing for CPU. #20's script asks for **at
  most 100 trades a second**: 50 traders, a buy and a sell per loop, and
  each loop at least its one-second sleep. At 40 to 50 ms that would be up
  to 4 to 5 vCPU of ledger for the trades alone. At the 21 a second the
  five-market run reached, it is 0.85 to 1.07 vCPU of the two, which fits
  under the measured bound.

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

**Every batch commit carries `WHERE lease_epoch = mine AND state_version =
last_committed`. That is the fence.** `last_committed` is the `state_version`
the owner read when it loaded the book, or wrote in its last batch that
committed. The batch's first statement is the UPDATE of the book row, with
the fence in its WHERE clause, and its row count is checked before
`posting.post_all` is called, in the same transaction. That order also keeps
ADR 0015's lock order: the book row, then the accounts `post_all` locks.

The epoch catches a process that lost the lease. A lease alone does not stop
a process that paused for longer than its expiry, through a long garbage
collection or a network partition, from waking up and writing as if it still
owned the book. The fence does: the UPDATE matches no row, the process knows
it has lost the book, and it rolls back before `post_all` writes anything.

The version catches a writer that does not touch the epoch. Every write that
moves `q` already moves `state_version`: `trading.write_trade` adds one, for
a trade and for each fill. So a book whose `q` changed under the owner fails
the fence, whoever changed it. Two cases need this, and an epoch-only fence
lets both through:

- *A rolling deploy.* ADR 0026 rolls one server at a time, so for a while a
  server on the release before runs today's per-trade path. It trades market
  M under `books.lock_book` while a replica on the new release owns M in
  memory, and it touches no lease column. The owner's next batch would pass
  an epoch-only fence and write `q` and `state_version` priced from a stale
  book, so the pool would pay at the wrong price.
- *A commit whose outcome is unknown.* The connection drops during COMMIT,
  after Postgres has committed. An owner that rolled its memory back to the
  `q` from before the batch, and sequenced the next batch from it, would
  pass an epoch-only fence and overwrite the committed `q`.

Under READ COMMITTED an UPDATE that waited for the row checks its WHERE again
against the newest version of that row, so a writer that moved either column
while the batch waited still fails the batch. That re-check covers only the
locked row's own columns. The fence must stay on the book row's columns and
never move into a subquery or a join, which Postgres answers from the
statement's snapshot after the wait. It is the same reason the trade path
reads its context in a statement after the lock, never inside it (ADR 0015,
"A check that must hold under the lock is issued after the lock statement";
`trading.read_trade_context`).

**A failed fence, or any batch error that is not a named refusal, drops the
memory.** The named refusals are the ones in "The hard part", below, which
`post_all` raises before it commits anything. Anything else, a lost
connection, a timeout, an error during COMMIT or a bug, leaves the owner
unsure what committed. It answers the batch's trades with an error, drops
the book, and reloads it from Postgres before it sequences anything else.
The clients retry, and their keys make each retry a replay or a first
execution (below, "Recovery is a reload", and the idempotency rules under
"The hard part").

**The rollout is expand then contract, as ADR 0029 shipped.** The version
does not catch every writer. A resting order placement (`orders.place` when
nothing fills) and a cancel (`cancellation.cancel`) change the open orders
and the reservations without moving `state_version`. Settlement and void
write the latch without moving it either. So:

1. *Expand.* One release adds the lease columns and makes all five paths
   that write under `books.lock_book` bump `lease_epoch`: trading
   (`trading.execute`), order placement (`orders.place`), cancellation
   (`cancellation.cancel`), settlement (`settlement.pay_out`) and voiding
   (`voiding.void_market`). On a trade, and on a placement that fills, that
   is one more column in the book row's existing UPDATE, so #641's budget of
   13 statements holds. The others only lock the book row, with
   `SELECT ... FOR UPDATE`, and write nothing to it, so for them it is one
   new UPDATE statement. The engine ships in this release, switched off.
2. *Switch on* only once every replica runs that release. The switch itself
   rolls one server at a time. A server still on the per-trade path bumps
   the epoch, which evicts any owner on a switched server: reloads, not a
   wrong price.
3. *Contract.* Once the engine is on everywhere, trades, order placements
   and cancels go only through the actor. Settlement and void keep their
   bump (below).

A release from before the expand step must never run beside the engine.
The version in the fence is the second guard if one ever does.

**One asyncio actor per owned market sequences trades.** It takes each trade,
buy, sell, order placement or cancel, in arrival order, runs the cascade
(ADR 0024), and prices it against the `q` in memory. A batch writer commits
what the actor has sequenced every few milliseconds, in one transaction,
through `posting.post_all`, which already takes a list of movements and
commits them once. The book row is written once per batch rather than once
per trade, and a hot market holds one database connection, not one per
waiting trader.

The batch is not a few round trips, though. `accounts.lock` takes one
statement per account, as ADR 0009 requires, because one `FOR UPDATE` over
several rows can deadlock. So a batch of n trades by n different traders
costs at least n + a few round trips under the book lock: the fenced
UPDATE, the n account locks, the key lookup, the floors, the writes and the
commit. Today the same n trades cost 13n. Those account locks are held while
the book row is. Two markets whose batches share a trader therefore queue
on that trader's row: the second batch waits for the first to commit,
holding its own book row while it waits. Both lock accounts in ascending id
order, so they queue rather than deadlock.

**The checks that decide still run under the book lock, at commit.** The
fenced UPDATE returns `clock_timestamp()`, and a batch whose fenced statement
runs after the market's `close_time` refuses its trades: ADR 0030's close
check, moved from the context statement to the batch's first statement, and
still on the database's clock. That reads the clock at the start of the
batch, before `post_all` waits on the account locks. Today's path does the
same: `trading.read_trade_context` reads it after the book lock and before
the account locks. The suspension and early-close checks from ADR 0030's
local copy run before a trade is sequenced, as they run before the lock
today.

**A response is held until its batch commits.** No trader is told a price
that a rollback could still undo. The price frame is published once per batch,
after the commit, as ADR 0010 already requires of every trade.

**A replica that does not own a market forwards the trade to the one that
does,** over an internal route on the owner's `lease_owner` address. The
public never reaches it: the gateway answers 404 for `/internal/` (ADR 0030).
That protects the route from the internet, not from the private network: a
forward goes from replica to replica and never passes the gateway. So the
forward carries the trader's own bearer token, and the owner verifies it
again exactly as the public route does. The debited account still comes from
the token's `sub`, and the body is still `TradeIn`. The service key alone is
not enough: the forwarder would then name the account, and a write route
that takes the account from the request is what root CLAUDE.md forbids ("The
ledger has four write routes, and none takes money"). A forward that lands
on a process that has since lost the lease is refused 503 rather than
forwarded again, so a request makes at most one hop.

**Settlement and void keep writing under the book lock, and bump the epoch.**
They are rare, they write the latch (the `market_results` row), and they stay
exactly as ADR 0019 and ADR 0025 describe them. The only change is a new
statement under their lock, `UPDATE ... SET lease_epoch = lease_epoch + 1`
on the book row. There is no existing UPDATE to add the column to: they lock
the book row with `SELECT ... FOR UPDATE` (`books.lock_book`) and write the
`market_results` row and the entries, never the book row. The engine's next
batch fails the fence, drops its memory, and reloads, and the reload reads the
latch and refuses every later trade. That is eviction: nothing has to message
the owner. If the batch took the book lock first, it commits first, and the
settlement then pays positions that include it, as today.

**Recovery is a reload.** The next replica to receive a trade for the market
takes the expired or released lease, as above, and reads `q`, `b`,
`state_version`, the positions and the open orders from Postgres, as it would
after a restart. A crash loses nothing that committed. The trades in flight
get an error: a 503 if the owner is alive to refuse them, but after a crash
more likely a 502 from the forwarder or a reset connection. So the promise
is to retry on any failure, and the idempotency keys make the retry a replay
of a trade that committed or a clean first execution of one that did not.

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
kept: every one of them was priced on a book that included it. So the actor
restores the whole book from before the batch, not only `q`: the
`state_version`, the positions, the open orders and their reservations, and
the fills the cascade made (ADR 0024), since a fill that followed the refused
trade may now not happen, or happen at another price. It sequences the rest
again, cascade included, checks each against its trader's own price limit
(ADR 0029's `max_cost` or `min_proceeds`), refuses any that no longer fit,
and commits again. Because no response left before the commit, nobody was
told the first price.

`post_all` commits a batch whole or not at all, and that is what the engine
needs. The refusals it can raise, and what each one means:

- **At a trade's own movement.** `InsufficientFunds` carries `account_id`,
  and `movement_key` for a refusal after a debit, so the actor knows which
  trade to drop. This is the overdraft the check exists for.
- **At the start of the batch,** an account already below its reservation,
  with no `movement_key`. A placement can cause one, because its new buy
  order counts at its whole budget (DECISIONS.md, the notes to D-NEW "A fill
  the running check refuses inside a cascade is `500 cascade_fill_refused`").
  If the account named placed an order in the batch, that placement is
  dropped and answered `409 insufficient_funds`, as today.
- **Anything else is a bug.** A start-of-batch refusal of an account that
  placed nothing, or a refusal at a fill's movement, cannot happen today by
  construction, by the same D-NEW: a trade drops no reservation, and every
  fill is bounded by its own. Dropping every trade that debits the account
  would hide a broken money invariant. So the batch aborts, the refusal is
  logged at ERROR with its figures, its trades are answered
  `500 cascade_fill_refused` as today, and the owner drops the book and
  reloads.

**Repeated refusals are bounded.** `_refuse_overdrafts` raises at the first
overdraft only, so a batch with k overdrafts would roll back k times, and
one user spending in other markets could keep a hot market's batches
rolling back. Either `post_all` walks the whole batch and reports every
refusal at once, so one rollback drops them all, or after a fixed number of
rollbacks, two say, the actor commits that batch's trades one per
transaction, as today, where an overdraft costs only its own trade. The
choice is made with the step before it, below, which rewrites `post_all`
anyway.

**Idempotency keeps today's two checks, and classifies by the request, not
the legs.** Under the engine a retry is priced at the current `q`, so its
legs rarely match the original's, and `posting._fingerprint` hashes the legs
(`account_id:amount`). Classified by fingerprint, a legitimate retry after a
failure would be refused `409 idempotency_key_reused`, which turns the
recovery promise into an error. So:

1. **Before sequencing,** the replica receiving the trade does today's
   unlocked lookup (`trading.py`, step 1). A key that has committed is
   compared with the request by `trading._matches` (outcome, side and
   quantity), and answered as a replay or refused as reused, without
   reaching the actor.
2. **In flight, the actor de-duplicates keys.** The same key can arrive twice
   before either commits, for example when a forwarder times out and the
   client retries at the owner. Both copies in one batch would make
   `post_all` raise `MalformedBatch` (`_refuse_if_malformed_batch` refuses a
   key named twice) and fail every trade in the batch. So the second request
   waits on the first one's result, and is then compared by `_matches` too.
3. **At commit,** a key can still be found, committed by a previous owner.
   Today's replay path cannot answer it. It raises `IdempotencyKeyReused`,
   naming no key. Or it raises `PendingWritesOnReplay`, but only if the
   session holds ORM writes: `has_pending_writes` (`core/database.py`) does
   not see a Core statement. If the fenced UPDATE is a Core statement,
   `_replay_batch` would commit it alone, a book row moved with no entries
   behind it. **Under the engine the replay path must never commit.**
   `post_all` names the keys it found and raises, the batch rolls back, and
   the actor answers each found key by `_matches`, as a replay with the
   original result or refused as reused, and re-sequences the rest. A found
   fill key is a bug, because it names a `state_version` the fence has just
   checked, and is handled as one.

This and the fence are where a bug is a double spend. A batch that commits
past a refused overdraft pays out credits that do not exist; an owner that
writes without the fence prices on a `q` another writer has already moved.
Each needs a race test that fails with its guard removed (ADR 0015's rule
for a race test) before anything else in the engine is written: a takeover,
an old-release writer during a roll, a commit with an unknown outcome, a
refused overdraft, and the same key twice in flight.

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
  the same shape of cost: a refusal that every caller has to retry. The
  fence's `state_version` check is the same comparison, but it does not
  serialise trades. The owner is the only writer, so the check fails only
  when something has gone wrong, a writer from outside the engine or a
  commit with an unknown outcome, and then costs one reload, not a retry
  per trade.
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
  trade's CPU, the per-trade SQLAlchemy and asyncpg work, and from a bigger
  server. That work was 19 to 28% and 10% of self time in #640's py-spy
  profile of 2026-11-04, which ran on a laptop, two of every service, not in
  production. A market that needs more than one process's CPU is the next
  ceiling, and this design does not lift it.
- **Latency gains a batch interval.** A trade waits up to a few milliseconds
  for its batch. Against a lock wait of seconds that is cheap; on an idle
  market it is pure cost.
- **More failure modes to watch:** forwards refused for a stale owner,
  batches that fail the fence, reloads after an unknown batch error, and
  rollbacks per batch. Each is counted and alerted on, the way the close
  sweep falling behind is.

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
31 trades a second on one market. That is an estimate: the pricing and the
Python around the statements do not shrink, so the real figure will be worse.
It should be built and measured first, because it is cheaper than this
record, and a leaner `post_all` is what the engine's batch writer would call
anyway.

It is not expected to clear the trigger on #20's one-market shape. At 31
trades a second, each of the 50 traders gets 0.62, one loop every 3.2 s.
The loop's sleep is 1 s of that, so unless the snapshot and the two trades
take more than 2.2 s between them, the traders ask for more than the lock
can serve. Either way the requests wait. Seven trades queued ahead at 32 ms
each is already 224 ms, above the 200 ms line. All of this is derived; none
of it is measured.

## When to take it

When either of these is measured under #20's script, after #641 has landed:

- one market sustaining more than about **20 trades a second**, from the
  script's k6 summary, or
- the one-market lock-wait p95 above **200 ms**, from the dashboard's
  "Book-lock wait p95" panel (`ledger_book_lock_wait_seconds`). The script
  does not report it. #668's runs should write that panel down for every
  run.

Below both, more vCPU and the single-statement write path are cheaper answers
to the same load.

**The second has fired, on a derived figure.** #641 landed on 2026-11-07.
The panel was not written down for the one-market run after it, so the lock
wait is not measured. The run before the fixes measured 4.5 s on it.

The trigger needs only a weak claim. A p95 lock wait above 200 ms is three
trades queued ahead at 84 ms each, 252 ms. On that run about 34 trades were
in flight on average, all on one book (Little's law, under Context). The
mean trade took 2.85 s and the mean snapshot read, which takes no lock,
1.68 s, both measured, on the same servers. So the average trade spent about
1.2 s more than a lock-free read. Its own extra work cannot explain that: 600
vCPU-seconds over 3,568 trades is at most 168 ms of app-server CPU per
trade, even if every cycle on both servers went to trades. For the p95 lock
wait to be under 200 ms, fewer than three of those 34 trades would have to
be waiting for the lock at the p95, and most of that 1.2 s would have to be
something else.

The stronger claim, that almost all of the 4.72 s p95 is the lock queue, is
not made. The lock-free snapshot's own p95 was 2.81 s on that run, so much
of the tail is the CPU queue and the connection pool (#667).

The first criterion cannot be read off the same run: the lock caps one
market at 12 trades a second, so demand above 20 shows up as wait, not as
throughput, which is why the second criterion exists.

It stays Proposed. The single-statement write path is cheaper and comes
first, and #668's one-, two- and three-server runs will say how much of the
84 ms hold is CPU starvation rather than round trips.

## Size

Two to three weeks for one backend person. Five to eight pull requests, about
two thousand lines with the race tests. One migration, the lease columns,
shipped in the expand release with the epoch bumps.

## What it amends when accepted

- **ADR 0005:** the fast side of the seam, "slow-changing definition against
  fast-changing money", is still the ledger alone, but its processes are no
  longer interchangeable: one per market holds `q` and the positions in
  memory.
- **ADR 0009:** serialisation on the accounts is unchanged: one lock
  statement per account, ascending by id, taken once per batch over every
  account in it. One transaction carries many trades, and its account locks
  are held while the book row is.
- **ADR 0015:** the read of the book that decides a trade is sequenced in
  memory and fenced at commit, rather than locked per trade. The fence is a
  conditional `UPDATE ... WHERE`, the idiom ADR 0015 rejected for the
  refresh token ("Alternatives rejected"), for two reasons: it does not hand
  back the row the rest of the function needs, and four sites fixed with
  one idiom are greppable where four fixed with three are not. Neither holds
  here. The fence needs nothing back but its row count and
  `clock_timestamp()`, which `RETURNING` gives, and it is one site with its
  own name, beside `with_for_update()` everywhere else. It stays on the
  book row's own columns, never in a subquery, because Postgres re-checks
  only the locked row after a wait. Every read that decides a balance is
  still locked.
- **ADR 0024:** the cascade still runs inside the trade that moved the price,
  in the actor, and commits in the same batch. A fill still commits if and
  only if the trade that caused it does.
- **ADR 0026:** a server holds leases, and the roll releases them before it
  stops one. The engine ships expand then contract, and no release from
  before its epoch bump runs beside it.

Unchanged in what they decide, and touched in how: ADR 0019 and ADR 0025,
whose book-lock UPDATE also bumps the epoch; ADR 0030, whose deciding close
check moves into the batch's fenced statement, still under the book lock and
on `clock_timestamp()`; and ADR 0029, whose limit is checked again for a
re-sequenced trade, and whose expand-then-contract rollout the engine
follows.
