# CS464

A prediction market. Users trade shares in binary outcome markets against an
automated market maker priced by LMSR, using mock credits rather than real
money. Built for CS464 by Ernest, Ihsan and Michelle.

Planning lives in [Project v2 #6](https://github.com/users/decker757/projects/6).

## Status

| Area | Issue | State |
| --- | --- | --- |
| Authentication | #29, #30, #31 closed | backend shipped; UI is #46, #47, #48 |
| Market creation | [1.1] #1 | draft and submit shipped; UI is #45 |
| Credit balance | [B-1] #32, [B-2] #33 | backend shipped with the ledger; UI is unbuilt |
| Ledger | [F-1] #41 | shipped; the write path has no endpoint until [T-2] #22 |
| LMSR pricing | [F-3] #43 | engine shipped in `ledger_service/core/`; no caller until [T-1] #21 |
| Trading | epic, 8 issues | not started |
| Realtime | [F-2] #42 | socket, pub/sub and auth shipped; the snapshot lands with #43 and #22 |

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
backend/market_service/   drafting and submitting markets
backend/audit_service/    reading the shared admin action log
backend/ledger_service/   credits: append-only entries, derived balances
backend/realtime_service/ live prices over a websocket; owns no data
sql/                      roles, schemas and grants for the shared Postgres
sql/migrations/           hand-applied ALTERs, until Alembic ([F-5] #75)
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
