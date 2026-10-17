# CS464

A prediction market. Users trade shares in binary outcome markets against an
automated market maker priced by LMSR, using mock credits rather than real
money. Built for CS464 by Ernest, Ihsan and Michelle.

Planning lives in [Project v2 #6](https://github.com/users/decker757/projects/6).

## Status

| Area | Issues | State |
| --- | --- | --- |
| Authentication | [A-1]–[A-3] #29–#31, UI #46–#48 | shipped, both sides |
| Market creation | [1.1]–[1.3] #1–#3, UI #45 | shipped; restricting edits to drafts, [1.4] #4, is open |
| Admin market tools | [2.1]–[2.4] #5–#8, UI #51, #55, #56 | shipped; the trade feed's username lookup is #273 |
| Resolution | [3.1]–[3.4] #9–#12, UI #52, #87 | shipped through settlement; the dispute countdown UI #59 and the settled-market UI #216 are open |
| Admin and audit | [4.1]–[4.4] #13–#16, UI #57 | shipped; the suspend control UI is #60 |
| Credits | [B-1] #32, [B-2] #33, [F-1] #41 | shipped; every balance is derived from append-only entries |
| Trading | [T-1]–[T-5] #21–#25, UI #49, #50, #53 | shipped; limit orders [T-6]–[T-8] #26–#28 are designed in ADR 0024 and not built |
| Discovery | [X-1]–[X-4] #34–#37, UI #54, #126, #209 | shipped |
| Leaderboard | [L-1] #38, [L-3] #40 | shipped; [L-2] #39 has its backend and waits on its UI #58 |
| LMSR pricing | [F-3] #43 | shipped in `ledger_service/core/lmsr.py` |
| Realtime | [F-2] #42, [X-4] #37 | shipped: socket, pub/sub, auth, the snapshot and the ledger's price producer |
| Migrations | [F-5] #75 | Alembic per service; no app creates a table |
| Deployment | [5.1] #17, [5.2] #18 | containers and CI shipped; the deploy pipeline [5.3] #19 and the load test [5.4] #20 are open |

## Running it

You need Docker. Nothing else.

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

Creating a market needs an administrator, and registration never grants one.
Register normally, then promote by hand and log in again:

```bash
docker compose exec db psql -U cs464 -d cs464 \
  -c "UPDATE auth.users SET role = 'admin' WHERE lower(username) = 'your_username';"
```

Already running Postgres on 5432? Set `POSTGRES_PORT` to something else in
`.env` and point `TEST_DATABASE_URL` at the same port. Same for Redis on 6379
and `REDIS_PORT`.

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

## How it is put together

```
backend/auth_service/     registration, login, logout, sessions
backend/market_service/   markets from draft to publication, closing them,
                          and the outcome a second administrator approves
backend/audit_service/    reading the shared admin action log
backend/ledger_service/   credits, trades, positions, settlement and the
                          leaderboard: append-only entries, derived balances
backend/realtime_service/ live prices over a websocket; owns no data
backend/shared/           the only code the services import from each other
backend/*/migrations/     auth, market, ledger: each schema's Alembic history,
                          run by compose's <service>-migrate step before the app
frontend/                 React, Vite and Tailwind; the Playwright end-to-end
                          suite lives in frontend/e2e/
sql/                      roles, schemas and grants for the shared Postgres
sql/migrations/           0002 only: a login role, and 02-schemas.sql re-run
docs/adr/                 decisions and why they were made
docs/api/                 endpoint contracts for the frontend
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
  fill against the AMM inside the trade that moved the price — Proposed, and
  not built
