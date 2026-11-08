# Load test of the trade endpoint

[5.4] #20. Backs the scaling claims in `docs/horizontal-scaling.md` and ADR
0026 with numbers: the p95 latency of `POST /ledger/markets/{id}/trades` with
50 traders at once.

## What it does

`loadtest/trade.js` is a [k6](https://grafana.com/docs/k6/) script, and
`loadtest/run.sh` runs it in k6's own Docker image, so nothing needs
installing.

- **50 traders, for 5 minutes.** Each one, once a second, reads the market's
  snapshot, buys one share of an outcome at that price, and sells it back.
- **Each trade names a 5% price limit.** A buy's `max_cost` is the snapshot's
  price plus 5%, rounded up to the tick, and a sell's `min_proceeds` the same
  price less 5%, rounded down (ADR 0029). The bots accept 5% so that the test
  measures the server, not the traders' patience.
- **On one market, or spread across several.** `MARKET_IDS` takes one id or
  several separated by commas, and the traders are dealt across them in turn.
  One market is the hardest case for the ledger: every trade locks the same
  book row (ADR 0015), so trades on one market queue behind each other however
  many servers there are. Spread across markets, they mostly do not wait for
  each other. Run both: the first finds one market's ceiling, the second what
  the servers carry.
- **A refused limit is retried, as a person would.** When other traders have
  moved the price past the limit, the trade is refused
  `409 price_beyond_limit` and nothing is written. The trader reads a fresh
  snapshot and retries at that price, up to five times. That is the limit
  working, so it is counted (`price_limit_refusals`) and not as an error.
- **What it records:**
  - `http_req_duration{name:trade}`: every trade request's latency, the
    retries included. Its p95 is the number the ticket asks for.
  - `trade_errors`: the share of trades refused for anything but the price.
    A 5xx, or a 4xx that is not `price_beyond_limit`, is the server's fault
    or the script's.
  - `trades_committed`, `price_limit_refusals` and `trades_given_up`
    (still refused at the limit after five retries): counts. A trade given up is contention on the book, not an error,
    so it is not in `trade_errors`. Read the two together. A refusal means
    the price moved about 5% between the snapshot and the trade.
  - `quote_stale_retries`: should be 0. Only a trade that names no limit is
    refused `quote_stale` now (`docs/api/ledger-service.md`, "Rolling out"),
    and every trade here names one. A count above 0 is a bug in the script or
    the server.
  - `http_req_duration{name:snapshot}`: the price read before each trade,
    and before each retry.
- **Three thresholds,** so a run says pass or fail by itself:
  - a trade's p95 is under 1 s, the same line as the latency alert
    (`deploy/grafana/alerts.yaml`)
  - a snapshot's p95 is under 1 s too
  - fewer than 1% of trades end in an error
- **It measures from the machine that runs it.** Against production, every
  number includes the trip from that machine to the load balancer and back.

**The traders are the same 50 accounts every run,** `loadtest_01` to
`loadtest_50`, sharing one password. Before each run the script lifts their
suspension and logs each one in. After it, it suspends them again, with a
reason in the audit log, because a suspended account is left off the
leaderboard (`docs/api/ledger-service.md`, "Who is ranked"). They are reused
because one address may register only 20 accounts an hour, and log in 30
times a minute (ADR 0027). So every login is spaced 2.5 s apart, and setup
takes about two minutes. The script touches only `loadtest_01` up to
`USERS`, by exact name, so a real account whose name starts `loadtest_` is
never unsuspended.

**The test trades only a market whose question starts "Load test".** Given
any other market id, setup refuses and nothing is traded.

## Running it

**Before it:**
1. **Make the throwaway markets** as an administrator, one or several, each
   with two outcomes and a closing time after the run. Name it so nobody trades it by mistake
   ("Load test: ignore"). Publish it, and copy its id from the address bar.
   Make it as the admin who will run the test: setup reads the market's
   question, and the market service shows it only to its creator.
   The test never touches any other market's prices.
2. **Put your admin password in a file,** without typing it on a command
   line: copy it, then run
   `(umask 077; pbpaste > ~/.cs464-loadtest-admin-password)`. Delete the file
   after the run. The traders' password is made for you on the first run, in
   `~/.cs464-loadtest-trader-password`. Keep that one: the accounts need it.
   Run the test from the machine that holds it.

**The first run** registers the 50 accounts. One address may register only 20
an hour, so on a site with the limits on, register them over three hours:
`USERS=20 DURATION=10s`, then `USERS=40 DURATION=10s` a little over an hour
later, then `USERS=50 DURATION=10s` after another. Every registration attempt
counts towards the 20, refused ones too. A register refused with 429 stops
setup and says so.

**The run:**

```bash
DOMAIN=<domain> MARKET_IDS=<id>[,<id>...] ADMIN_IDENTIFIER=<your admin username> \
  loadtest/run.sh
```

Against a local stack, give the three services' addresses instead of
`DOMAIN`. For example, for the scaled stack (`docs/horizontal-scaling.md`):
`AUTH_URL=http://host.docker.internal:18000 MARKET_URL=http://host.docker.internal:18001 LEDGER_URL=http://host.docker.internal:18003`.
The local stack raises the per-address limits, so `LOGIN_SPACING_SECONDS=0.2`
is fine there.
A local stack needs an admin and a market first. Promote yourself the way the
root README says ("Creating a market needs an administrator"), adding
`-p cs464scale -f docker-compose.yml -f docker-compose.scale.yml` to its
`docker compose` for the scaled stack.

`USERS=10 DURATION=30s` makes a quick trial run.

The summary is printed and saved in `loadtest/results/`, which git ignores.
Copy the numbers into the table below.

**If you stop it with Ctrl-C once while it trades,** k6 stops the traders and
still suspends the accounts. If setup fails partway, it suspends every
account it had unsuspended before it stops. **A Ctrl-C during setup, the
first two minutes, or a second Ctrl-C, skips all of that,** and the accounts
it had reached stay unsuspended, so on the leaderboard. `run.sh` says so when
k6 stops early. Suspend them again with a one-second run
(`USERS=50 DURATION=1s loadtest/run.sh`), or by hand from Admin → Users:
search `loadtest_`.

**After it:** close each throwaway market early, then void it from its admin
page (ADR 0025). Voiding refunds, at cost, the shares still held when the run
stopped. Each trader sells back what it buys, so few are held. What a round
trip gained or lost stays, a few credits at most.

## Results

| When | Where | Traders, for | Trades committed | Trade p95 | Trade p99 | Errors | Stale quotes retried | Trades given up |
|---|---|---|---|---|---|---|---|---|
| 2026-11-04 | a laptop, the scaled stack (two of each service) | 10, 30 s | 469 (16 a second) | 78 ms | 210 ms | 0% | 609 | 41 |
| 2026-11-04 | a laptop, the scaled stack (two of each service) | 50, 1 min | 1,137 (19 a second) | 258 ms | 431 ms | 0% | 9,603 | 1,346 |
| 2026-11-04 | a laptop, the scaled stack, 1 market | 20, 20 s | 354 (18 a second) | 122 ms | 231 ms | 0% | — | 151 |
| 2026-11-04 | a laptop, the scaled stack, 5 markets | 20, 20 s | 702 (35 a second) | 196 ms | 263 ms | 0% | — | 3 |
| 2026-11-04 | production, two 1-vCPU app servers behind the load balancer, 1 market | 50, 5 min | 126 (0.4 a second) | 5.45 s | 6.59 s | 0% | 4,427 | 692 |
| 2026-11-04 | production, two 1-vCPU app servers behind the load balancer, 5 markets | 50, 5 min | 738 (2.5 a second) | 4.52 s | 5.93 s | 0.8% | 5,091 | 633 |
| 2026-11-07 | production after #640's release (price limits, the ledger's local copy, 13 statements under the lock, DigitalOcean's pooler), two 1-vCPU app servers, 1 market | 50, 5 min | 3,568 (12 a second) | 4.72 s | 5.71 s | 0% | — (38 limit refusals, all retried) | 0 |
| 2026-11-07 | the same, 5 markets | 50, 5 min | 6,394 (21 a second) | 2.60 s | 3.59 s | 0% | — (no limit refusals) | 0 |

"A second" is over the trading time alone. k6's own `/s` figure divides by a
time that includes setup.

Means for the two 7 November runs, which ADR 0031's arithmetic uses. They are
from k6's saved summaries, `loadtest/results/20261107T151930Z.txt` (one
market) and `loadtest/results/20261107T151101Z.txt` (five markets), which git
ignores and which are kept on the machine that ran them:

| 2026-11-07 run | Trade avg, med | Snapshot avg, med | Iterations, avg |
|---|---|---|---|
| 1 market | 2.85 s, 2.69 s | 1.68 s, 1.6 s | 1,784, 8.49 s |
| 5 markets | 1.42 s, 1.28 s | 897 ms, 799 ms | 3,197, 4.75 s |

An iteration is one trader's loop: a snapshot, a buy, a sell and a
one-second sleep.

**What the numbers say.**
- **Latency holds.** With 50 traders the p95 is a quarter of the alert's
  line, and no trade failed with an error.
- **One market's throughput barely grows with traders.** Five times the
  traders committed 16 trades a second, then 19. With 50, more than half the
  attempts gave up after five stale quotes.
- **That is two rules doing their jobs.**
  - Every trade moves the price, so it makes every other trader's quote stale.
    A trade must name the exact `state_version` it was quoted at ([T-3] #23),
    so each of those traders has to quote again.
  - Trades on one market commit one at a time, because each locks the same
    book row (ADR 0015).
- **Spread across five markets, the same 20 traders committed twice the
  trades, and almost none gave up.** So a second server adds capacity across
  many markets, and none for one busy market.
- **After #640's release, the same script committed 28 times the trades on
  one market and 9 times on five, with no errors.** Price limits ended the
  retry storm: a trade that would have been refused as stale now executes at
  the current price inside its limit. Only 38 of 3,606 attempts on one market
  fell outside the 5% limit, and each was retried; on five markets none did.
  The p95 moved less than
  the throughput: 5.45 s to 4.72 s on one market, 4.52 s to 2.60 s on five.
- **On one market the book lock is now the ceiling.** 3,568 trades in 300 s
  is 12 a second, so each trade held the lock about 84 ms (derived: one over
  the throughput). The script loops a snapshot, a buy, a sell and a
  one-second sleep, so not all 50 traders queue at once: by Little's law
  about 34 trades were in flight on average (12 attempts a second times the
  measured 2.85 s mean trade latency). 50 × 84 ms = 4.2 s is the wait if all
  50 queued at once, an upper bound, not the measured 4.72 s p95. The
  lock-free snapshot read had a p95 of 2.81 s on one market and 1.77 s on
  five, so the servers' CPU was saturated too, and part of the trade's tail
  is that queue, not the lock. ADR 0031's trigger for a per-market engine is
  a one-market lock wait above 200 ms at the p95, or more than about 20
  trades a second on one market. The lock-wait criterion is very likely met,
  but not yet measured: the dashboard's lock-wait panel was not written down
  for this run, and #668 records it. ADR 0031, "When to take it", has the
  arithmetic.
- **Behind the pooler, the lock queue holds the ledger's connections.** During
  the one-market run, `ledger on app-1: database` showed DOWN on the dashboard:
  the ledger's pool has 14 server connections for both servers, every
  transaction queued on the book lock keeps one, and the readiness check's
  `SELECT 1` waited behind them. The five-market run never did this. The
  ledger's pool should be sized above its client pools' total (two servers at
  10 + 5 each), inside the connection budget in `docs/deployment.md`.
- **Next measurements:** the one, two and three server runs (#668), which
  say how far the five-market figure moves with CPU. What each fix changed
  is under "Capacity and its limits", below.
- **Production is much slower than a laptop.** With 50 traders for five
  minutes, the same script committed 126 trades on one market and 738 on five,
  against 1,137 in one minute on a laptop. Both app servers sat at 100% CPU,
  and most attempts were refused as stale and retried. #640 is the ticket that
  acts on it.

## Capacity and its limits

[5.4] #640, #645. What the 7 November release changed and by how much, where
the ceilings are now, and what comes next. The design past the last ceiling is
[ADR 0031](adr/0031-a-per-market-trading-engine.md), proposed and not built.
A figure from a laptop or a test database says so. Every other figure is
production's.

### Before and after

The before is production at `73566c42`, `main` after #632, deployed on
2026-11-04: the 4 November rows above. The after is production at `003e3ab1`,
`main` after #663, deployed on 2026-11-07: the 7 November rows. The same
script and shape both times: two 1-vCPU app servers, 50 traders, 5 minutes.

| Production, 50 traders, 5 min | 2026-11-04, `73566c42` | 2026-11-07, `003e3ab1` |
|---|---|---|
| One market: trades committed | 126 (0.4 a second) | 3,568 (12 a second) |
| One market: trade p95, p99 | 5.45 s, 6.59 s | 4.72 s, 5.71 s |
| One market: refused at the price, given up | 4,427 stale quotes, 692 | 38 beyond the limit, 0 |
| Five markets: trades committed | 738 (2.5 a second) | 6,394 (21 a second) |
| Five markets: trade p95, p99 | 4.52 s, 5.93 s | 2.60 s, 3.59 s |
| Five markets: errors | 0.8% | 0% |

#640's target was a five-market p95 under 500 ms. **It was not met:** the
p95 is 2.60 s.

**Every fix shipped in that one release, so production has one before and one
after, not one per fix.** Below is what each fix is measured to have changed,
and where. Each was merged into `dev` as the commit named, and reached `main`
in `003e3ab1`.

| Fix | Merged as | Before | After | Measured |
|---|---|---|---|---|
| Tracing samples one request in ten | #647, `7e275dec` | every request traced: 12–16% of self time in the profile below | one in ten | not on its own; the before is the profile |
| Price limits instead of exact quotes ([ADR 0029](adr/0029-price-limits-replace-exact-quotes.md)) | #651, `ba208cef`; the script sends limits, #654, `11b5802a` | one market: 4,427 refused as stale and 692 given up, for 126 committed, about 35 refusals per trade | one market: 38 refused beyond the 5% limit, all retried, none given up, for 3,568 | production, the rows above |
| The ledger keeps a local copy and calls nobody ([ADR 0030](adr/0030-the-ledger-keeps-a-local-copy.md)) | #650, `20ecf9eb`; #656, `345ee355` | two HTTP calls per trade, to auth and market. The five-market run's 0.8% errors were 6 of 744 trades: three `503 auth_unavailable` and three `503 market_terms_unavailable` | no call on a market with a book; 0% errors | production; the error breakdown is from that run's saved k6 summary, as ADR 0030 records |
| The locked section is budgeted at 13 statements (#641) | #661, `9147d5ce` | 22 statements under the book lock on a buy, 21 on a sell. A round trip took 20 to 75 ms under the 4 November load | 13, held by `test_trade.py::test_a_warm_trade_stays_within_its_statement_budget`. The hold is about 84 ms, about 6.5 ms per statement with the Python between them | statements counted by the suite's `capture_sql`; the hold from the one-market row; DECISIONS.md, "The locked section of a trade is budgeted at 13 statements" |
| Connection pools, one per service role | #660, `be4f6a37` | two servers' pools used about 40 of the database's 47 or so connections, so a third server could not start | the four pools hold 28 server connections, however many servers run. A 25-statement transaction through a transaction-mode pooler: median 6.8 ms, against 5.4 ms direct | the connections from #640 and `docs/deployment.md`, "Connection budget"; the timings on a laptop (DECISIONS.md, "asyncpg's statement cache is off in every engine"); the server runs are #668, below |
| The balance sum reads only the index (#642) | #648, `611fe4e7` | 1,315 buffers to sum an account of about 5,000 entries (bitmap heap scan, 1,250 heap blocks) | 45 buffers after a VACUUM (index-only scan, no heap fetches); 1,326 on entries no VACUUM has reached yet | a test database (#648) |
| The week-ago price is stored, not replayed (#643) | #655, `afb9d1c7` | every trade of every listed market read and replayed: 10,000 rows for 20 books of 500 trades | 20 index probes of one row each, 141 buffers, 0.18 ms | a test database (#655) |

### The profile, 2026-11-04

From #640: py-spy on the scaled stack, two of every service on a laptop,
during the load test. Not production. Self time:

| Where | Share |
|---|---|
| asyncio and uvicorn | 19–40% |
| SQLAlchemy | 19–28% |
| tracing | 12–16% |
| asyncpg | 10% |
| pricing, pydantic, logging | under 5% each |

The profile's raw output is not in the repository: these ranges are as #640
records them. No profile has been taken since the release.

What it says: the time went on the database round trips and the Python around
them, not on the maths. That is why the fixes cut calls and statements, and
why the next step does too.

### The ceilings now

- **One market is one lock.** Every trade on a market holds that market's
  book row `FOR UPDATE` for 13 statements, on purpose: the money has to
  serialise ([ADR 0015](adr/0015-locking-the-read-that-decides-a-write.md)).
  3,568 trades in 300 s is 12 a second, about 84 ms per hold. If all 50
  traders queued at once, the last would wait 50 × 84 ms = 4.2 s; that is an
  upper bound, since about 34 trades were in flight on average (above). More
  servers do not raise the throughput, except for whatever part of the 84 ms
  is CPU starvation, which #668's runs will show. The dashboard's book-lock
  wait p95 was 4.5 s on 4 November (#640). For 7 November it was not written
  down, and #668's runs should record it.
- **Nothing bounds that wait.** There is no `lock_timeout` or statement
  timeout on a trade, so the tail grows by about 84 ms for every trader
  queued ahead. #669 asked for one and was closed as not planned.
- **One vCPU per server, shared.** Each app server is a 1-vCPU droplet
  running auth, market, ledger, audit, realtime, the gateway and Grafana
  Alloy. The five-market run had two servers for 300 s, 600 vCPU-seconds,
  for 6,394 trades: **at most 94 ms of app-server CPU per committed trade**,
  everything included, the snapshot read before each buy among it. The
  snapshot read takes no lock, and its p95 was 1.77 s on five markets, so
  the servers were queueing for CPU. On one market it was 2.81 s, but that
  includes waiting for a pooled connection (the last item here), so it is
  not a clean CPU figure. Server CPU was not sampled during the 7 November
  runs. Before the release, #640 put each trade attempt at about 130 ms of
  app-server CPU, across the ledger and its two calls to auth and market.
  That is a production figure from 4 November, and #640 shows no working; it
  matches 600 vCPU-seconds over that one-market run's 4,553 attempts (126
  committed and 4,427 refused stale), about 132 ms. The two calls' share was
  not measured on its own. Bigger servers are the lever not pulled;
  `docs/deployment.md`, "What it costs", has the prices.
- **One primary database.** One managed Postgres node (1 vCPU, 2 GB, no
  standby), with about 47 connections, of which the pools use 28. Every
  trade, lock and balance is there, so adding app servers adds no database.
  It ran at 45% CPU on 4 November. On 7 November it was not recorded. The
  lever is a bigger plan: the app servers scale out and the database scales
  up ([ADR 0026](adr/0026-production-on-digitalocean.md)). The audit log
  needs every service on that one database
  ([ADR 0006](adr/0006-audit-log-write-path.md)).
- **The ledger's pool is smaller than its clients (#667).** The ledger's
  pool has 14 server connections for both servers, and each ledger process
  has 10 + 5 client connections, 30 in all. A trade waiting for the book lock
  keeps its server connection while it waits, so on one busy market the 14
  fill with waiting trades and every other ledger read waits behind them.
  During the one-market run, app-1's readiness check, a `SELECT 1`, timed out
  and the dashboard showed `ledger on app-1: database` DOWN while trades
  still committed. The five-market run never did. #667's fix is to make the
  two agree, for example a pool of 24 with 8 + 4 per server, inside the
  budget in `docs/deployment.md`. It is not done yet.

### Reads that grow without bound

Fixed in this release:

- **The balance sum** (#648). A balance is `SUM(amount)` over the account's
  entries, read under the account lock on every trade (ADR 0009). It now
  reads only the index. It still reads every entry the account has, so it
  still grows with the account's history, at 45 buffers for 5,000 entries
  rather than 1,315. Entries newer than the last VACUUM still cost a heap
  fetch each.
- **The week-ago price on the browse list** (#655). One index probe per
  market, for the newest trade before the mark, priced from the `after_q` it
  stored. A market whose newest trade before the mark predates `after_q` is
  still replayed, and logged at INFO, until those trades are more than a week
  old.

Not fixed:

- **The price history endpoint**, `GET /ledger/markets/{id}/price-history`.
  It replays every trade the market has ever had, oldest first, on every
  read, and cuts the window afterwards
  (`service/price_history.py::price_history_of_market`). It grows with the
  market's trades: the one-market run left a market with 3,568 of them.
- **The leaderboard refresh.** Every 60 seconds, on the first server, one
  statement values every holder from all USER accounts' entries and
  positions, and auth is asked for their names
  (`service/leaderboard.py::_list_net_worths`). It grows with users and
  their entries. It is off the trade path, but on the same vCPU and the same
  database.

### Scale-out: one, two and three servers

> **Not measured yet. This table is #668's**, and its empty cells are filled
> in by those runs. Nothing in it is an estimate.

| App servers | Five markets, 50 traders, 5 min: trades committed | Trade p95 | Errors | Server CPU |
|---|---|---|---|---|
| 1, the other drained | not run (#668) | | | |
| 2 | 6,394, on 2026-11-07, the baseline; #668 runs it again | 2.60 s | 0% | not recorded |
| 3, a temporary third droplet | not run (#668) | | | |

What it has to answer: how far the five-market figure moves with CPU, and
whether the one-market figure moves at all. It should not move much, because
the book lock is the ceiling there. If it does, that part of the 84 ms hold
was CPU starvation.

### What comes next, in order

Two things first, which change no design: #667's pool sizes (one number in
Terraform and two in the production compose file) and #668's runs.

1. **A single-statement write path.** Everything a trade writes, as one
   data-modifying statement after the account locks, so the locked section
   goes from 13 statements to about 5: the book lock, the context, one lock
   per account, and the write
   ([ADR 0031](adr/0031-a-per-market-trading-engine.md), "The step before
   it"). If the hold shrank in proportion, it would go from 84 ms to about
   32 ms, about 31 trades a second on one market. That is an estimate, and
   the real figure will be worse, because the pricing and the Python around
   the statements stay. It may not bring the one-market run under 200 ms
   of lock wait, and has to be measured: at 31 a second each trader's loop
   is 3.2 s, so the snapshot and the two trades share 2.2 s. At today's
   1.68 s snapshot the trades get about 0.5 s of that, and seven trades
   queued at 32 ms each is 224 ms, near the line. That is derived, not
   measured.
2. **The per-market engine,** [ADR 0031](adr/0031-a-per-market-trading-engine.md).
   Each market's book in memory in one ledger process, behind a fenced lease
   on the book row, with trades committed in batches and the overdraft check
   still in Postgres. Its trigger, a one-market lock wait above 200 ms at the
   p95, was very likely met on 7 November, but is not yet measured; #668
   records the book-lock wait panel. It stays proposed until step 1 is built
   and measured.
