# Auth service

Registration, login, logout and session refresh for the CS464 prediction
market. Covers [A-1] #29, [A-2] #30, [A-3] #31 and the grant half of [B-1] #32.

Why it is ours rather than Supabase: [ADR 0001](../../docs/adr/0001-self-host-authentication.md).
How tokens reach the client: [ADR 0002](../../docs/adr/0002-auth-token-transport.md).

## Running it

The whole stack, from the repo root:

```bash
cp .env.example .env          # then set JWT_SECRET
docker compose up --build
```

Or just the database, with the service on the host for a faster reload loop:

```bash
docker compose up -d db                    # from the repo root
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env          # the copy in this directory, not the root one
.venv/bin/uvicorn app:app --reload
```

Interactive API docs at http://localhost:8000/docs. That page is the contract
for the frontend sub-issues #46, #47 and #48.

## Tests

```bash
docker compose up -d db          # from the repo root
.venv/bin/pytest                 # 97 tests
.venv/bin/pytest unit_test/core  # no database needed
```

The suite reads `AUTH_TEST_DATABASE_URL` from the repo-root `.env`, the same
gitignored file docker compose reads, so no connection string lives in the
repository. Copy `.env.example` and fill it in once. Exporting the variable
overrides the file.

| Layer | Tests | Needs Postgres |
| --- | --- | --- |
| `core/` | 21 | no |
| `model/` | 20 | no |
| `service/` | 27 | yes |
| `controller/` | 29 | yes |

Database access is opt-in: only the `session` and `client` fixtures pull it in,
so `core/` and `model/` run in under a second with nothing else started.

The database-backed tests use Postgres rather than SQLite, in the separate
`cs464_test` database that `sql/00-init.sh` creates, truncating
between tests rather than recreating the schema. Testing on the engine we
deploy is deliberate: SQLite and Postgres disagree about naive versus aware
timestamps and about functional unique indexes, and both differences have
already bitten this service.

A rule for where a new test goes: if it asserts a business rule, it belongs in
`service/` and should not go through HTTP. If it asserts a status code, a
cookie, or a response shape, it belongs in `controller/`.

Already running Postgres on 5432? Set `POSTGRES_PORT=5433` in the root `.env`
and point `AUTH_TEST_DATABASE_URL` at the same port.

## Configuration

`DATABASE_URL` and `JWT_SECRET` are required and have no defaults. The service
refuses to start without them rather than falling back, because a default
database URL is a credential in the repository and a default signing key is a
published key that would silently sign real sessions.

## Endpoints

| Method | Path | Story | Notes |
| --- | --- | --- | --- |
| POST | `/auth/register` | [A-1] #29 | 201, sets both cookies, always creates a trader |
| POST | `/auth/login` | [A-2] #30 | `identifier` takes a username or an email |
| POST | `/auth/refresh` | [A-3] #31 | Single use, rotates the refresh token |
| POST | `/auth/logout` | [A-3] #31 | Unauthenticated on purpose, always 200 |
| GET | `/auth/me` | [A-3] #31 | Reference protected route |
| GET | `/admin/users` | [4.1] #13 | Admin only, not audited, `q` matches a username or an email |
| PATCH | `/admin/users/{user_id}/role` | [4.4] #16 | Admin only, audited, cannot target self or the last admin |
| GET | `/health` | | Liveness and readiness probe |

`UserOut` carries a `role`, either `trader` or `admin`, and the access token
carries the same value as a `role` claim. That claim is the only way another
service can tell an administrator from a trader, because no other service can
read `auth.users`. See [ADR 0003](../../docs/adr/0003-market-service-boundary.md).

There are two roles and no tier above them, so any administrator may promote or
demote any other user and the control against misuse is that the audit log
records every one. The first administrator is still a manual `UPDATE`,
permanently: the account that may grant administrative authority cannot itself
be granted it. See
[ADR 0007](../../docs/adr/0007-admin-tiers-and-role-changes.md), which also
explains why MARKET_CREATOR, RESOLVER and SUPER_ADMIN are not being built and
what would change that.

Note the asymmetry a role change creates. This service authorises from the row,
so a demotion binds `/admin/*` on the very next request. The market service
authorises from the token claim and cannot read `auth.users`, so it honours the
target's existing token until it expires. The endpoint reports that bound as
`takes_effect_within_seconds`.

Errors share one shape, so the frontend parses a single case:

```json
{ "error": { "code": "duplicate_user", "message": "That email is already registered." } }
```

## Layout

```
main.py                     create_app(), and nothing else

controller/                 the HTTP boundary. No business rules live here.
    routes.py               the five endpoints
    dependencies.py         DbSession and this service's own route guard
    transport.py            cookie and bearer extraction, cookie writing
    errors.py               the one mapping from domain error to status code

service/                    business rules. Raises domain errors, knows no HTTP.
    auth_service.py

core/                       this service's own plumbing
    config.py               settings, read from the environment once
    database.py             engine, session factory, session dependency
    security.py             the only file that touches argon2 or jwt
    errors.py               domain exception classes, no framework imports

model/                      data shapes
    entities.py             SQLAlchemy tables
    schemas.py              request and response contracts, drives OpenAPI

unit_test/                  mirrors the layers above
    core/                   pure units, no database
    model/                  schema validation, no database
    service/                business rules, real session, no HTTP
    controller/             status codes, cookies, error envelope
```

Imports only ever point down: `controller` may use `service`, `service` may use
`core` and `model`, and nothing below reaches back up. That is why the error
classes sit in `core` while the handler that turns them into responses sits in
`controller`.

## Promoting an administrator

A deliberately manual step. There is no bootstrap environment variable and no
self-service endpoint, because either would be a way to grant authority that
nobody reviewed.

```bash
docker compose exec db psql -U cs464 -d cs464 \
  -c "UPDATE auth.users SET role = 'admin' WHERE lower(username) = 'ernest_t';"
```

The user must log in again afterwards: the token already in their browser still
says `trader` until it is reissued. [4.4] #16 replaces this with real role
management, splitting `admin` into MARKET_CREATOR, RESOLVER and SUPER_ADMIN.
The column is a VARCHAR with a CHECK rather than a Postgres ENUM precisely so
that widening it is an ordinary migration.

## Two things worth knowing

**Starting credits are not granted here, by design.** This service owns users
and credentials and nothing else. It does not know that credits exist, and
`test_registration.py` has a guard asserting the word never appears in a
response. [B-1] #32 is the ledger's job, and nothing about it needs auth.

[F-1] #41 has landed and does exactly what this section planned: the ledger
mints the grant itself, lazily. A user with no entries is by definition a user
who has not been granted yet, so the first time anything reads their balance,
the ledger writes the genesis transaction keyed on `signup-grant:<user_id>` and
carries on. A unique constraint on that key makes it idempotent, so concurrent
first requests race safely and the loser simply re-reads. See
`backend/ledger_service/service/grants.py` and
[ADR 0009](../../docs/adr/0009-the-ledger-write-path.md).

Nothing in this service changed to make that work, which was the point. There
is no call to the ledger, no event, and no new dependency: registration commits
a user row and the ledger finds out the first time somebody asks what that user
holds.

Why this and not an event, an outbox, or a shared transaction:

- The grant is a real ledger entry, so [B-1]'s rule that a balance equals the
  sum of a user's entries still holds. A `balance` column would break it, and
  a prediction market cannot afford two sources of truth for money.
- No cross-service transaction, no event bus, no outbox table, no polling job.
- Retrying registration cannot double-grant, because the key is the user id.
- There is no observable window where a balance is wrong. An outbox has one;
  this does not. That satisfies the intent of [B-1]'s "atomically" better than
  a distributed transaction would, without pretending to be one.

The only oddity is that the first read performs a write. That is normal for a
welcome grant and costs one insert per user, ever.

## Database boundary

This service connects as `auth_svc` and owns the `auth` schema. It cannot read
or write any other service's schema, and no other service can read ours. That
is enforced by ownership and the absence of cross grants, not by convention, so
a join across the boundary fails with a permission error while someone is
writing it rather than succeeding quietly.

The roles, schemas and grants live in `sql/` at the repo root, applied by
compose on first initialisation of the data volume and by CI before the suite
runs, so both exercise the same files.

One Postgres instance keeps the hosting cost to a single instance. Schemas
rather than separate databases is a portability choice: several managed
providers bill per database or give one per project, and schemas survive a move
to any of them.

Changing the boundary means editing `sql/02-schemas.sql`, and
`docker compose down -v` to re-run it, which destroys development data.

**Schema creation is `create_all`, not migrations.** Fine while this service
owns its database alone. Move to Alembic when #41 shares it, because two
services issuing `create_all` against one database will race.
