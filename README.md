# CS464

A prediction market. Users trade shares in markets with two to ten outcomes
against an automated market maker priced by LMSR, using mock credits rather
than real money. Built for CS464 by Ernest, Ihsan and Michelle.

Planning lives in [Project v2 #6](https://github.com/users/decker757/projects/6).

## Status

| Area | Issues | State |
| --- | --- | --- |
| Authentication | [A-1]–[A-3] #29–#31, UI #46–#48 | shipped, both sides |
| Market creation | [1.1]–[1.4] #1–#4, UI #45, #354 | shipped, both sides: drafts autosave, and only a draft or a submitted market can be edited |
| Admin market tools | [2.1]–[2.4] #5–#8, UI #51, #55, #56, #418, #434 | shipped, both sides: overview, early close, exposure, price history, and a trade log and user history that name people rather than ids (#273) |
| Resolution | [3.1]–[3.4] #9–#12, UI #52, #59, #87, #216 | shipped, both sides: propose, a second admin approves, the dispute window and send-back, settlement |
| Voiding | #221, UI #526 | shipped, both sides: an admin voids a flawed market and every holder is refunded at cost (ADR 0025) |
| Admin and audit | [4.1]–[4.4] #13–#16, UI #57, #60, #479 | shipped, both sides, including the suspend control and a users list that filters by role and by status; promoting an admin is an API call and has no screen yet |
| Credits | [B-1] #32, [B-2] #33, [F-1] #41, UI #376, #443, #410 | shipped; every balance is derived from append-only entries, and the portfolio shows profit and loss and how many shares a live sell order holds back |
| Trading | [T-1]–[T-8] #21–#28, UI #49, #50, #53, #61, #429, #495, #566, #580, #592 | shipped, both sides: the price is the control that picks an outcome, a live price chart, and limit orders, on two-outcome markets, that fill inside the trade that moves the price (ADR 0024). A trade's confirmation, or the warning that it could not be confirmed, shows even after the trader picks another outcome, and a trade sent again after no answer is made once and says so. A trade may name a price limit and executes within it at the current price (ADR 0029); the panel sends one of its own in #652 |
| Discovery | [X-1]–[X-4] #34–#37, #446, #447, UI #54, #126, #209, #441, #444, #448, #450, #564 | shipped: search and filters, live prices, the trader's own holdings, bands for closing soon, trending and biggest movers, and cards that show the odds as a split bar and each outcome's change over the week |
| Leaderboard | [L-1]–[L-3] #38–#40, UI #58 | shipped, both sides |
| Layout and accessibility | #380, #437, #438, #452, #574 | every signed-in page fits a phone, with the navigation behind a menu button, and every text colour pair passes WCAG AA, including a browse chip's label while its price flashes |
| LMSR pricing | [F-3] #43 | shipped in `ledger_service/core/lmsr.py` |
| Realtime | [F-2] #42, [X-4] #37 | shipped: socket, pub/sub, auth, the snapshot, the ledger's price producer and its order frames |
| Migrations | [F-5] #75 | Alembic per service; no app creates a table |
| Deployment | [5.1] #17, [5.2] #18, [5.3] #19, [5.4] #440, #609, #20, #640, #667 | live at **https://predictsmu.xyz**: every push to `main` deploys to DigitalOcean, two app servers behind a load balancer (ADR 0026), and `npm run seed:production` seeds it with the demo markets; monitoring (#599) ships to Grafana Cloud from each server; the load test [5.4] #20 ran on 2026-11-04 (a trade's p95 was 5.45 s at 50 users, both servers at 100% CPU) and #640's answer shipped on 2026-11-07: a trade names a price limit (ADR 0029), the ledger keeps a one-second copy of who and what may trade and calls no other service on a trade (ADR 0030), a trade holds the book lock for 13 statements instead of 22 (#641), and DigitalOcean's connection pooler lets a third server start (#660); the after figures, measured on 2026-11-07, are under "Load testing and scaling" below; this release ships #667, so that one hot market's lock queue no longer holds the ledger's connections away from its reads and readiness check (the ledger's pool holds all its clients, and only two trades per market hold a connection; a burst of cancels can still use up the rest while it lasts); staging is dropped from scope (ADR 0026) |

## Running it

You need Docker for the backend, and Node 20.19 or later for the frontend.

```bash
cp .env.example .env
```

Fill in every blank in `.env`. Compose refuses to start if any is missing,
which is deliberate: there are no default passwords or signing keys anywhere in
this repository. Generate values with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Then:

```bash
docker compose up --build
```

The auth service comes up on http://localhost:8000, the market service on
http://localhost:8001, the audit service on http://localhost:8002 and the
ledger on http://localhost:8003, each with interactive API docs at `/docs`. Those pages are the contract the frontend
codes against, alongside [`docs/api/`](docs/api/).

Before auth, market and ledger start, compose runs `auth-migrate`,
`market-migrate` and `ledger-migrate`. Each brings its service's tables up to
date and exits. If one fails, its service does not start, and
`docker compose logs market-migrate` (or `auth-migrate`, `ledger-migrate`) says
why.

The realtime service comes up on http://localhost:8004 and is the exception:
its `/docs` describes only `/health`, because OpenAPI has no vocabulary for a
WebSocket. Its contract is
[`docs/api/realtime-service.md`](docs/api/realtime-service.md).

Then the frontend, in a second terminal:

```bash
cd frontend
npm ci        # once
npm run dev
```

Open http://localhost:5173. It talks to the services on the ports above with
no configuration; `VITE_API_URL` and its siblings override them. The services
accept browser requests from ports 5173 and 3000 unless `CORS_ORIGINS` says
otherwise.

Creating a market needs an administrator, and registration never grants one.
The first administrator is made by hand: register normally, promote, and log
in again:

```bash
docker compose exec db psql -U cs464 -d cs464 \
  -c "UPDATE auth.users SET role = 'admin' WHERE lower(username) = 'your_username';"
```

Every administrator after that is promoted by an existing one through
`PATCH /admin/users/{user_id}/role`, which writes an audit entry, and also has
to log in again: authority rides in the access token (ADR 0003, ADR 0007).

Already running Postgres on 5432? Set `POSTGRES_PORT` to something else in
`.env`, and use the same port in each `<SERVICE>_TEST_DATABASE_URL`. Same for
Redis on 6379 and `REDIS_PORT`.

### A demo database

For a showcase, seed a database of its own with markets worth looking at
(#454). Give the demo its own compose project, so it gets its own empty
volume and your dev data is left alone:

```bash
docker compose stop                                              # your dev stack; keeps its data
COMPOSE_PROJECT_NAME=cs464-demo docker compose up -d --build --wait
cd frontend && npm ci                                            # once
COMPOSE_PROJECT_NAME=cs464-demo npm run seed:demo                # about three minutes
```

The seed needs the same `COMPOSE_PROJECT_NAME` as the `up`, or its SQL steps
reach another project's database; it checks, and stops if they did. It also
refuses to start when that project is `cs464`, your dev stack, which is what
compose picks when the variable is unset: nothing it writes there could be
taken out again without wiping your dev data.

It makes two administrators, `admin_rachel` and `admin_kumar`, nine traders
(`wei_ling`, `arjun_k`, `marcus_tan`, `priya_s`, `jun_hao`, `sarah_lim`,
`daniel_ng`, `aisyah_r`, `nur_h`) and fourteen SMU markets. Every account's
password is `PASSWORD` in `frontend/e2e/support/backend.ts`. Nine markets stay
open: seven traded until their prices sit well apart (the six Yes/No ones
between about 13% and 90% Yes, and a five-way one), and two left untraded. The
other five are traded and then taken one stage further each: closed, pending
resolution, approved, settled with its winners paid, and voided with every
holder refunded (#570). The seed prints every market's final prices when it
finishes.

Three things are there to show live (#570):

- **A limit order filling.** `nur_h` has three resting limit buys, each a few
  points under its market's price: Yes at 55% on the CS464 showcase market
  (61% now), No at 20% on the IS111 bidding market (25% now) and Yes at 85% on
  the library market (90% now). Log in as another trader and buy the other
  side: about 35 shares on the showcase market, 40 on IS111 or 60 on the
  library. The trade pulls the price past the limit, fills part of `nur_h`'s
  order inside that same trade, and leaves the price sitting at the limit
  (ADR 0024). Buy more and the order fills further. A trade that only just
  reaches the limit fills nothing: the sliver it could fill costs a tick more
  than the limit once the cost is rounded, so the order waits.
- **A voided market.** The campus café market was closed early and voided.
  Its page and card say so, and `marcus_tan`, `jun_hao` and `priya_s` see the
  refund in their history (ADR 0025).
- **A suspended trader.** `aisyah_r` is suspended, with a reason, once the
  trading is done. Admin → Users shows it, and the account cannot log in
  until an administrator lifts it.

The four liveliest open markets, the Yes/No ones that end furthest from 50/50
and the five-way one, close soonest, so the full browse list, which shows the
soonest-closing market first, opens on them below the featured market and the
bands.

The trades go in eight rounds, twenty seconds apart, so each market's price
history has a shape. That is why it takes minutes: a trade's time is the
ledger's own clock, and no API backdates one. The open markets close three
weeks or more after seeding; if you seeded long before the showcase, seed
again.

The browse list's "Biggest movers" band measures from the last trade at least
seven days old, so a database seeded on the day shows none. Seed one to two
weeks before the showcase, then trade during it, and it appears. #564 adds the
same change to each card, measured the same way.

It goes through the services' own APIs, using the end-to-end helpers in
`frontend/e2e/support/backend.ts`, and takes the same two SQL steps they do,
through `docker compose exec db`: promoting the first administrator,
`admin_rachel`, and moving the settled market's dispute window into the past.
`admin_rachel` then promotes `admin_kumar` through the role-change endpoint,
as every administrator after the first is made, so the audit log shows it as
`user.role_changed`.

**It is one-shot.** Its accounts have fixed names, so a second run stops at
the first registration and changes nothing. A run that fails partway also
needs a fresh database. Seeding again means
`COMPOSE_PROJECT_NAME=cs464-demo docker compose down -v`, which destroys that
project's data and only that project's.

**Don't run `npm run e2e` against the demo stack afterwards.** Its tests add
their own markets and users, which then sit beside the demo's.

**It only runs against this machine.** It refuses to start unless every
service URL (`VITE_API_URL`, `VITE_MARKET_API_URL`, `VITE_LEDGER_API_URL`,
defaulting to the ports above) is `localhost`, `127.0.0.1` or `[::1]`, and the
Docker engine its SQL steps reach is a local socket or pipe. There is no
override.

**The production site has its own command** (#609): `npm run seed:production`,
from `frontend/`, on the machine that ran `infra/tf apply`. Same markets,
traders and scenarios, with three differences:
- Every account gets a generated password, written to
  `~/cs464-production-accounts.txt`. The password above is in this
  repository, so on a public site anyone could log in as a seeded admin.
- The two SQL steps run over SSH on an app server.
- You type the domain to confirm. It refuses a site that already has a
  market, any of its accounts, or an administrator: it makes the first one.

Run it one to two weeks before the showcase, for the movers band, and before
anyone makes an administrator.
[`docs/deployment.md`](docs/deployment.md), "Seeding the demo markets".

### Two of everything, behind a load balancer

```bash
docker compose -f docker-compose.yml -f docker-compose.scale.yml \
  up -d --build --wait
```

Two replicas of each stateless service behind one nginx on ports 18000-18004,
as its own `cs464scale` compose project, so it runs beside the stack above
rather than instead of it. The three `*-migrate` steps stay one container each,
which is the only thing in that file that is a rule rather than a preference:
ADR 0020's step runs once per deploy, never once per replica.

[`docs/horizontal-scaling.md`](docs/horizontal-scaling.md) has the whole run —
what was executed, which replica answered which request, and what [5.3] #19's
pipeline has to get right.

### Production

Every push to `main` deploys to DigitalOcean, in Singapore: two app servers
behind a load balancer, with managed Postgres and Valkey, all described in
Terraform under `infra/terraform/`. The servers are updated one at a time,
and a release that fails on any server is taken back off every server. It
costs about US$81 a month at list prices.

[`docs/deployment.md`](docs/deployment.md) covers first-time setup, what a
deploy does, rollback, scaling and the cost breakdown.
[ADR 0026](docs/adr/0026-production-on-digitalocean.md) is why.

```bash
deploy/production/test/stack-test.sh   # the production stack on this machine; needs only Docker
```

## Load testing and scaling

[5.4] #20 and #640. The production stack is built to scale sideways: every
service is stateless, so the load balancer can send any request to any
server; one managed Postgres holds the data, behind DigitalOcean's
connection pooler so a third server fits inside the database's connection
limit (#660); live prices travel over Redis rather than between servers
(ADR 0010). `loadtest/run.sh` drives it with k6: 50 traders, each logging
in, quoting and trading on real markets through the public API for five
minutes. Every run is recorded in
[`docs/load-test.md`](docs/load-test.md).

| Production, 50 traders, 5 min | 4 November | 7 November, after #640 |
|---|---|---|
| One market: trades committed, p95, errors | 126, 5.45 s, 0% | 3,568, 4.72 s, 0% |
| Five markets: trades committed, p95, errors | 738, 4.52 s, 0.8% | 6,394, 2.60 s, 0% |

What changed between the columns, all measured first and acted on second:

- **Price limits instead of exact quotes** ([ADR 0029](docs/adr/0029-price-limits-replace-exact-quotes.md)).
  A trade names the most it will pay and executes within that at the current
  price. Before, about 35 attempts were refused as stale for every trade that
  committed; after, none.
- **The ledger calls nobody on a trade** ([ADR 0030](docs/adr/0030-the-ledger-keeps-a-local-copy.md)).
  It keeps a one-second copy of who may trade and what may be traded, and a
  copy older than five seconds refuses rather than trusts. Before it, #640
  put each trade attempt at about 130 ms of CPU across the ledger and its
  two calls; the calls' share was not measured on its own.
- **The locked section of a trade is 13 statements, from 22** (#641), guarded
  by a test that fails above the budget.
- **Two unbounded reads bounded:** the balance sum reads only an index (#642),
  and the browse list's week-ago price is stored, not replayed (#643).

What the numbers say. Across markets, throughput grows with servers, and the
pooler is what lets the next server start. On one market it does not: every
trade on a market takes the same lock, on purpose, because the money has to
serialise ([ADR 0015](docs/adr/0015-locking-the-read-that-decides-a-write.md)), and
3,568 trades in 300 s is 12 a second, about 84 ms per hold. That is the
documented ceiling. The trade p95 on one market was 4.72 s, measured. How
much of it was the lock queue, and how much the saturated CPU, was not
measured: the dashboard's lock-wait panel was not recorded for that run.
That run also showed one server's ledger reporting its database down while
trades still committed, because trades waiting for the lock held its
connections. #667 fixed that after the run, and #668's runs are the first to
measure it.
The step past it, a per-market engine, is written up as a proposed ADR,
[ADR 0031](docs/adr/0031-a-per-market-trading-engine.md), and the one, two
and three server runs that put a number on the sideways scaling are the
next measurement (#668). What each fix changed, the ceilings and the next
steps are in
[`docs/load-test.md`, "Capacity and its limits"](docs/load-test.md#capacity-and-its-limits).

## Monitoring

[5.5] #599. Each production server runs Grafana Alloy, which sends metrics,
logs and traces to a free Grafana Cloud stack. The dashboard is in your
Grafana Cloud stack (`https://<stack>.grafana.net`), under *Dashboards*,
*CS464 / Overview*. Its file and the alert rules are in `deploy/grafana/`.

What it shows:

- **Traffic:** requests per second, the share that failed with a 5xx, and
  p50 and p95 latency, by route.
- **Health:** whether each service is up and ready, on each server.
- **Trading:** trades and fills per minute, the wait for a market's book
  lock, settlements, voids, and price frames that failed to publish.
- **The close sweep:** how far behind it is, on each server.
- **Live prices and logins:** open websockets, login-limiter refusals, and
  any time the limiter was off.
- **Logs and traces:** paste a response's `X-Request-ID` into the
  `request_id` box to see that request's log lines from every service, and
  open its trace from there.

Traces keep the client's address and user agent, by decision, and the
gateway's access log starts with the client's address. The services' own log
lines carry no IP, with one exception: realtime logs each websocket handshake
as `<client ip>:<port> - "WebSocket <path>" [accepted]` (or `403`), with the
path as requested, query string included. The socket never takes a token in
its query string, by design (docs/api/realtime-service.md), so nothing secret is in that line. No
trace carries a query string, cookie or Authorization header.

Five alerts, each emailed to the `cs464-email` contact point:

| Alert | Fires when | For |
| --- | --- | --- |
| `cs464-5xx-ratio` | more than 5% of a service's requests answer 5xx, while it serves more than 0.1 requests a second | 5 min |
| `cs464-p95-latency` | a service's p95 latency is over 1 s, while it serves more than 0.1 requests a second | 5 min |
| `cs464-close-sweep-behind` | no server has finished a close sweep in 60 s, or a live sweeper's lag is over 60 s | 1 min |
| `cs464-service-down` | a service on a server cannot be scraped, is not ready, or has stopped sending metrics; or no server is sending metrics at all | 3 min |
| `cs464-login-limiter-off` | the login limiter failed open even once in 5 minutes, because Redis was unreachable | at once |

It costs nothing. The free tier allows 10,000 active series, 50 GB of logs,
50 GB of traces, 14 days' retention and 3 users, and two servers need about
1,900 series. Alloy used 51–169 MB of memory on each server (2 GB droplets),
minutes after its first deploy.
[`docs/deployment.md`](docs/deployment.md) has the setup, the import steps and
the series estimate.

## Tests

```bash
docker compose up -d db redis
cd backend/auth_service       # or market_service, audit_service, ledger_service
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

`realtime_service` is the odd one out: it opens no database at all and wants
Redis instead, from `REDIS_URL`.

They run against Postgres rather than SQLite, as the same restricted role the
service uses in production, so engine and permission differences surface here
instead of on deploy. The pure unit layers need no database:

```bash
.venv/bin/pytest unit_test/core unit_test/model
```

Each service reads its own `<SERVICE>_TEST_DATABASE_URL` from the root `.env`,
so a suite always connects as its own restricted role rather than as the
superuser. That is what makes the cross-schema denial tests mean something.

The frontend has unit tests that need nothing running, and a Playwright suite
that drives the real frontend against the real stack:

```bash
cd frontend && npm ci
npm run test:run              # unit tests
npm run lint                  # oxlint, type-aware, as CI runs it
docker compose up -d --build --wait   # from the repo root, then:
npm run e2e                   # end to end
```

## Guarantees, and the tests that hold them

Each line is a rule the code keeps and the test that goes red if it stops
keeping it. Paths are under `backend/ledger_service/unit_test/` unless
another service is named.

- **Every trade on a market serialises on that market's book row, and every
  read that decides a write is issued after the lock returns**
  ([ADR 0015](docs/adr/0015-locking-the-read-that-decides-a-write.md)).
  `service/test_concurrency.py::test_two_batches_racing_for_one_balance_debit_it_once`,
  `service/test_cascade_concurrency.py::test_a_trade_queued_behind_a_placement_fills_the_order_it_rested`,
  and `market_service/unit_test/service/test_publishing.py::test_publish_reads_the_clock_after_it_has_the_lock`.
  A race test holds both sessions on a barrier, and is kept only if it fails
  with the lock removed.
- **A trade sent twice is made once.** The idempotency key is re-checked
  under the lock; a retry returns the original and writes nothing.
  `service/test_trade_replay.py::test_a_retry_returns_the_original_trade`,
  `::test_a_retry_executes_nothing`,
  `service/test_trade_concurrency.py::test_a_duplicate_arriving_after_the_pre_gate_lookup_is_replayed_under_the_lock`,
  `service/test_settlement_concurrency.py::test_a_trade_retried_behind_a_settlement_replays_its_original`.
- **The books balance.** No balance is stored; every movement writes legs that
  sum to zero; the platform account's negative balance is the credits in
  circulation ([ADR 0009](docs/adr/0009-the-ledger-write-path.md)).
  `service/test_concurrency.py::test_every_transaction_balances_on_its_own`,
  `::test_the_ledger_still_balances_afterwards`,
  `::test_no_balance_ever_goes_negative`.
- **Entries are append-only, by a database trigger.**
  `service/test_append_only.py::test_an_entry_cannot_be_updated`,
  `::test_an_entry_cannot_be_deleted`.
- **A trade names its slippage tolerance and executes within it at the
  current price, or is refused with the figures**
  ([ADR 0029](docs/adr/0029-price-limits-replace-exact-quotes.md)).
  `service/test_trade_limits.py::test_a_buy_within_its_limit_executes_at_the_current_price_after_the_book_moved`,
  `::test_a_buy_beyond_its_limit_is_refused_with_the_current_total_and_nothing_moves`.
- **When a dependency is unknown, the ledger refuses rather than guesses.**
  Its one-second copy of who may trade and what may be traded fails closed
  past five seconds ([ADR 0030](docs/adr/0030-the-ledger-keeps-a-local-copy.md)).
  `service/test_local_copy.py::test_a_copy_older_than_the_max_age_refuses_with_the_dependency_s_unavailable_code`,
  `::test_a_copy_never_loaded_refuses_with_the_dependency_s_unavailable_code`.
- **The database's clock decides a close, read after the lock, so every
  replica agrees.**
  `service/test_trade.py::test_a_market_whose_close_time_passed_under_the_lock_is_refused_by_the_database_clock`.
- **A warm trade holds the lock for at most 13 statements.**
  `service/test_trade.py::test_a_warm_trade_stays_within_its_statement_budget`.
- **The starting grant is minted once, and changing the setting re-grants
  nobody.**
  `service/test_portfolio_reads.py::test_the_first_read_mints_the_grant_once_and_the_second_does_not`,
  `service/test_portfolio.py::test_starting_credits_is_what_the_grant_minted_not_the_current_setting`.
- **Each service owns one schema and connects as its own role; the only
  cross grant is INSERT on the audit log**
  ([ADR 0006](docs/adr/0006-audit-log-write-path.md)). In market_service:
  `service/test_schema_boundary.py::test_it_cannot_read_the_auth_schema`,
  `::test_the_audit_grant_is_exactly_insert`. The auth service does not know
  that credits exist:
  `auth_service/unit_test/service/test_registration.py::test_register_takes_no_ledger_dependency`.
- **A model change without its migration fails the suite.** `test_migrations.py`
  in auth, market and ledger compares the Alembic head with the models and
  with the Postgres catalogue ([ADR 0020](docs/adr/0020-migrations-by-alembic.md)).
- **A websocket from an unlisted origin is refused**, because CORS does not
  apply to sockets.
  `realtime_service/unit_test/controller/test_ws_routes.py::test_a_socket_from_an_unlisted_origin_is_refused`.

## Known limits

Measured or decided, with the record that says so.

- **One market is one lock.** About 84 ms per hold under load, so about 12
  trades a second on one market, and a trade p95 of 4.72 s with 50 traders
  on it, part lock queue and part CPU queue
  ([`docs/load-test.md`](docs/load-test.md), 7 November). That is the
  cost of serialising money through Postgres. The step past it, a single
  writer per market, is the proposed engine,
  [ADR 0031](docs/adr/0031-a-per-market-trading-engine.md).
- **A hot market's lock queue no longer starves the ledger's reads, except
  during a burst of cancels (#667).** During the one-market run, one
  server's readiness check reported its database down while trades still
  committed: trades waiting for the book lock held the ledger's 14 pooled
  connections. The pool now holds every
  ledger client (24, for 6 + 2 on up to three servers;
  [`docs/deployment.md`](docs/deployment.md), "Connection budget"), so the
  pooler never queues the ledger. Inside each ledger, only two trades per
  market, and six in all, hold a connection at once. The rest wait in
  memory, so two of its 8 connections are kept from trades and placements.
  Reads and the readiness check share those two with everything else that
  takes no slot: cancels, settlement, void and the leaderboard refresher. A
  burst of cancels on a hot market can still use them up while it lasts.
- **A price is published after the trade commits, and a crash between the
  two loses that frame** ([ADR 0010](docs/adr/0010-realtime-price-broadcast.md)).
  A client that reconnects fetches a snapshot, which is the recovery path it
  needs anyway.
- **Logout cannot revoke an issued access token.** The 15-minute lifetime
  bounds the window ([ADR 0002](docs/adr/0002-auth-token-transport.md)).
- **The audit log is atomic with the action only while every service shares
  one database** ([ADR 0006](docs/adr/0006-audit-log-write-path.md)).
- **One managed Postgres, one region, and no staging environment.** Staging
  is dropped from scope (ADR 0026, amended 2026-11-08).
- **The trade panel does not yet send its own slippage tolerance.** The
  backend accepts one; #652 is the frontend half.

## Architecture

Three diagrams, in [`docs/architecture.md`](docs/architecture.md). They are
mermaid, so GitHub renders them and there is nothing to install or regenerate,
and each one names the ADR or `DECISIONS.md` entry it was drawn from — a
diagram with no provenance drifts away from the decision it describes and
nobody notices.

- [**The services and what they own**](docs/architecture.md#the-services-and-what-they-own):
  the five services, the four Postgres schemas, Redis, who may read what, and
  the two surprises — `realtime` has no database at all, and the one shared
  table is appended to by three services and readable by one.
- [**A trade, end to end**](docs/architecture.md#a-trade-end-to-end): the path
  that crosses three services. Where the two upstream checks go, where the
  single commit is, and why the price frame is published after it and can
  never fail the trade.
- [**The schemas**](docs/architecture.md#the-schemas): `market` and `ledger` as
  ERDs, including the balance column `ledger.accounts` does not have.

After them, [**the diagram set**](docs/architecture.md#the-diagram-set):
twelve more drawn for the final proposal, with shorter labels that print at
A4 width, and the parts the three do not draw — production, CI/CD, security,
the market lifecycle, settlement, scaling, the live-price fan-out and a
delivery timeline. Their sources are in `docs/diagrams/`, rendered to SVG and
PNG by `docs/diagrams/render.py`.

They live in their own file rather than inline so that each exists once.

## How it is put together

```
backend/auth_service/     registration, login, logout, sessions
backend/market_service/   markets from draft to publication, closing them,
                          the outcome a second administrator approves, and
                          sending it back or voiding the market
backend/audit_service/    reading the shared admin action log
backend/ledger_service/   credits, trades, limit orders, positions,
                          settlement, void refunds and the leaderboard:
                          append-only entries, derived balances
backend/realtime_service/ live prices over a websocket; owns no data
backend/shared/           the only code the services import from each other
backend/*/migrations/     auth, market, ledger: each schema's Alembic history,
                          run by compose's <service>-migrate step before the app
frontend/                 React, Vite and Tailwind; the Playwright end-to-end
                          suite lives in frontend/e2e/
sql/                      roles, schemas and grants for the shared Postgres
sql/migrations/           0002 only: a login role, and 02-schemas.sql re-run
deploy/                   nginx.conf for the replicated stack below
docs/adr/                 decisions and why they were made
docs/api/                 endpoint contracts for the frontend
docs/architecture.md      the three diagrams above, each citing its ADR,
                          and the diagram set
docs/diagrams/            the diagram set's sources, renders and render.py
docs/horizontal-scaling.md  two of every service behind a proxy, and the run
                          that showed it works
scripts/                  weekly sprint digest to Telegram
.github/workflows/        CI, one path-filtered workflow per area
```

Python 3.13, FastAPI, SQLAlchemy 2 on async Postgres. Each backend service owns
one schema and connects as its own login role, with no grants between them, so
a query across a service boundary fails rather than quietly working.

Inside a service, imports point one way: `controller` uses `service`, `service`
uses `core` and `model`, nothing below reaches up. Tests mirror those layers.

## Contributing

Branch from `dev`, named `<issue>-<slug>`. Open a pull request into `dev`.
`staging` and `main` are promotion targets.

Two rules worth knowing before your first pull request. If you squash a merge,
which is what we did for the first one, that branch is finished: a squash has
no shared ancestry with the branch, so pushing more work there and re-opening a
pull request collides on every file. Start a fresh branch off `dev` instead.
And use `Refs #N` rather than `Closes #N` when a ticket still has open
sub-issues, so a parent is not auto-closed while half of it is unbuilt.

`CLAUDE.md` holds the longer list of things that will otherwise waste an hour.

## Decisions

Recorded in [`docs/adr/`](docs/adr/), one file per decision that was expensive
to make and would be expensive to reverse.

- [0001](docs/adr/0001-self-host-authentication.md) self-hosting auth rather
  than adopting a managed provider
- [0002](docs/adr/0002-auth-token-transport.md) cookies for browsers, bearer
  tokens for services
- [0003](docs/adr/0003-market-service-boundary.md) a separate market service,
  and admin authority carried in the token
- [0004](docs/adr/0004-draft-autosave-and-submission.md) one idempotent
  endpoint for both autosave and submission
- [0005](docs/adr/0005-trading-service-boundary.md) a composite trading
  service, and positions with the ledger
- [0006](docs/adr/0006-audit-log-write-path.md) one shared audit log, written
  in the acting service's transaction
- [0007](docs/adr/0007-admin-tiers-and-role-changes.md) a flat admin tier, and
  role changes audited rather than approved
- [0008](docs/adr/0008-publishing-a-market.md) publishing as its own endpoint,
  from SUBMITTED only, and one way
- [0009](docs/adr/0009-the-ledger-write-path.md) double-entry with derived
  balances, and a lazily minted starting grant
- [0010](docs/adr/0010-realtime-price-broadcast.md) a relay that owns nothing,
  and a bus that is not the database
- [0011](docs/adr/0011-market-auto-close.md) the clock closes a market, and
  the sweep only writes it down
- [0012](docs/adr/0012-the-shared-package.md) a shared package, and the build
  contexts that had to move first
- [0013](docs/adr/0013-proposing-an-outcome.md) proposing an outcome, from
  CLOSED only, with evidence, one at a time
- [0014](docs/adr/0014-closing-a-market-early.md) closing a market early, by
  any admin, with the reason in the log
- [0015](docs/adr/0015-locking-the-read-that-decides-a-write.md) every read
  that decides a write is locked, and the wider lock goes first
- [0016](docs/adr/0016-deciding-a-proposal.md) deciding a proposal, by any
  admin but the proposer, with APPROVED as a status
- [0017](docs/adr/0017-the-ledger-and-a-stopped-market.md) the ledger asks
  market_service whether a market is still trading, once per trade (amended
  by 0030: the answer now comes from the ledger's own one-second copy)
- [0018](docs/adr/0018-positions-are-valued-at-liquidation.md) positions are
  valued at liquidation, not at the marginal price
- [0019](docs/adr/0019-settlement-across-services.md) settlement is the
  ledger's request, paid in one transaction, and market_service is told
  afterwards
- [0020](docs/adr/0020-migrations-by-alembic.md) migrations by Alembic, per
  service, run as a one-off step
- [0021](docs/adr/0021-the-leaderboard-and-the-first-service-credential.md) the
  leaderboard is a snapshot on the ledger, and auth names its users through
  the first service credential
- [0022](docs/adr/0022-the-ledger-and-a-suspended-trader.md) the ledger asks
  auth whether a trader is still active, once per trade (amended by 0030: the
  answer now comes from the ledger's own one-second copy)
- [0023](docs/adr/0023-sending-an-approved-outcome-back.md) sending an approved
  outcome back inside its stored dispute window, by any admin
- [0024](docs/adr/0024-resting-orders-fill-inside-the-trade.md) resting orders
  fill against the AMM inside the trade that moved the price
- [0025](docs/adr/0025-voiding-a-flawed-market.md) voiding a flawed market, by
  any admin without a stake, refunded at cost
- [0026](docs/adr/0026-production-on-digitalocean.md) production on
  DigitalOcean, two or more app servers behind a load balancer, deployed from
  `main`
- [0027](docs/adr/0027-login-rate-limits-in-redis-failing-open.md) login and
  registration limits in Redis, per username or email and address, failing
  open
- [0028](docs/adr/0028-liveness-for-the-balancer-readiness-for-alerts.md)
  `/health` is liveness for the balancer, `/ready` is readiness for dashboards
  and alerts
- [0029](docs/adr/0029-price-limits-replace-exact-quotes.md) price limits
  replace exact quotes on the trade route, shipped expand then contract
- [0030](docs/adr/0030-the-ledger-keeps-a-local-copy.md) the ledger keeps a
  one-second local copy of suspensions and trading terms, and calls nobody on
  a trade; a stale copy refuses
- [0031](docs/adr/0031-a-per-market-trading-engine.md) (Proposed) a
  per-market trading engine: one owner per book, a fenced lease, group
  commits; written, not built
