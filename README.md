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
| Trading | [T-1]–[T-8] #21–#28, UI #49, #50, #53, #61, #429, #495 | shipped, both sides: the price is the control that picks an outcome, a live price chart, and limit orders, on two-outcome markets, that fill inside the trade that moves the price (ADR 0024) |
| Discovery | [X-1]–[X-4] #34–#37, #446, #447, UI #54, #126, #209, #441, #444, #448, #450, #564 | shipped: search and filters, live prices, the trader's own holdings, bands for closing soon, trending and biggest movers, and cards that show the odds as a split bar and each outcome's change over the week |
| Leaderboard | [L-1]–[L-3] #38–#40, UI #58 | shipped, both sides |
| Layout and accessibility | #380, #437, #438, #452 | every signed-in page fits a phone, with the navigation behind a menu button, and every text colour pair passes WCAG AA |
| LMSR pricing | [F-3] #43 | shipped in `ledger_service/core/lmsr.py` |
| Realtime | [F-2] #42, [X-4] #37 | shipped: socket, pub/sub, auth, the snapshot, the ledger's price producer and its order frames |
| Migrations | [F-5] #75 | Alembic per service; no app creates a table |
| Deployment | [5.1] #17, [5.2] #18, [5.4] #440 | containers, CI and the replicated stack shipped; the deploy pipeline [5.3] #19 and the load test [5.4] #20 are open |

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
docker compose up -d --build --wait   # from the repo root, then:
npm run e2e                   # end to end
```

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
docs/architecture.md      the three diagrams above, each citing its ADR
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
  market_service whether a market is still trading, once per trade
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
  auth whether a trader is still active, once per trade
- [0023](docs/adr/0023-sending-an-approved-outcome-back.md) sending an approved
  outcome back inside its stored dispute window, by any admin
- [0024](docs/adr/0024-resting-orders-fill-inside-the-trade.md) resting orders
  fill against the AMM inside the trade that moved the price
- [0025](docs/adr/0025-voiding-a-flawed-market.md) voiding a flawed market, by
  any admin without a stake, refunded at cost
