# Market service

Drafting and submitting prediction markets. Covers [1.1] #1 and the backend
half of [FE][1.1] #45.

Owns markets, their outcomes and their resolution sources. Knows nothing about
users beyond the id in a signed access token, and cannot read the `auth` schema
even if it wanted to.

- Endpoint contract, for the frontend: [`docs/api/market-service.md`](../../docs/api/market-service.md)
- Why a separate service, and where admin authority comes from: [ADR 0003](../../docs/adr/0003-market-service-boundary.md)
- Why one endpoint does both autosave and submit: [ADR 0004](../../docs/adr/0004-draft-autosave-and-submission.md)

## Running it

The whole stack, from the repo root:

```bash
cp .env.example .env          # fill in every blank
docker compose up --build
```

This service comes up on http://localhost:8001, the auth service on
http://localhost:8000. Interactive API docs at http://localhost:8001/docs.

Or just the database, with the service on the host for a faster reload loop:

```bash
docker compose up -d db                    # from the repo root
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env          # the copy in this directory, not the root one
.venv/bin/uvicorn main:app --reload --port 8001
```

## Getting an admin account

There is no bootstrap variable and no self-service endpoint, because either
would be a way to grant authority that nobody reviewed. Register normally, then
promote by hand:

```bash
docker compose exec db psql -U cs464 -d cs464 \
  -c "UPDATE auth.users SET role = 'admin' WHERE lower(username) = 'ernest_t';"
```

**Log in again afterwards.** Authority is carried in the access token, so the
one already in the browser still says `trader` until it is reissued. [4.4] #16
replaces this with real role management.

## Tests

```bash
docker compose up -d db          # from the repo root
.venv/bin/pytest                 # 130 tests
.venv/bin/pytest unit_test/core unit_test/model unit_test/service/test_validation.py
```

The suite reads `MARKET_TEST_DATABASE_URL` from the repo-root `.env`, the same
gitignored file docker compose reads, so no connection string lives in the
repository.

| Layer | Tests | Needs Postgres |
| --- | --- | --- |
| `core/` | 24 | no |
| `model/` | 18 | no |
| `service/validation.py` | 34 | no |
| `service/` drafting and boundary | 27 | yes |
| `controller/` | 27 | yes |

Database access is opt-in: only the `session` and `client` fixtures pull it in,
so the pure layers run in about a second with nothing else started. That
matters more here than in the auth service, because the submission rules are
the bulk of the logic and none of them touch a database.

Tests run on Postgres rather than SQLite, as `market_svc` under production
grants. This service is almost entirely about timestamps, and the two engines
disagree about naive versus aware ones. Running as the real role is also what
lets `unit_test/service/test_schema_boundary.py` assert that reading
`auth.users` is denied — as the superuser it would succeed, and the suite would
pass while proving nothing.

Tokens in the suite are signed with PyJWT directly rather than by importing
anything from the auth service. This service has no minting code and a test
asserts it never grows any, so a test that signs its own token exercises the
same path a real request takes, and stays honest that the two services agree on
a wire format rather than on an implementation.

## Configuration

`DATABASE_URL` and `JWT_SECRET` are required and have no defaults, for the same
reasons as the auth service. `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_ISSUER` and
`ACCESS_COOKIE_NAME` must all match the auth service, or every request is a 401.

## Endpoints

| Method | Path | Story | Notes |
| --- | --- | --- | --- |
| POST | `/markets` | [1.1] #1 | Autosave and submit. 201 first, 200 after |
| GET | `/markets` | [1.1] #1 | The caller's own markets only |
| GET | `/markets/{id}` | [1.1] #1 | 404, not 403, for someone else's |
| GET | `/health` | | Liveness and readiness probe |

Full request and response shapes: [`docs/api/market-service.md`](../../docs/api/market-service.md).

## Layout

```
main.py                     create_app(), and nothing else

controller/                 the HTTP boundary. No business rules live here.
    routes.py               the three endpoints
    dependencies.py         DbSession, the token guard, the admin guard
    transport.py            cookie and bearer extraction. Read-only.
    errors.py               the one mapping from domain error to status code

service/                    business rules. Raises domain errors, knows no HTTP.
    market_service.py       the upsert, and creator-scoped reads
    validation.py           pure: when may a market leave DRAFT?

core/                       this service's own plumbing
    config.py               settings, read from the environment once
    database.py             engine, session factory, session dependency
    security.py             the only file that touches jwt. Verify only.
    roles.py                the reader's copy of the role vocabulary
    errors.py               domain exception classes, no framework imports

model/                      data shapes
    entities.py             SQLAlchemy tables
    schemas.py              request and response contracts, drives OpenAPI

unit_test/                  mirrors the layers above
```

Imports only ever point down, as in the auth service: `controller` may use
`service`, `service` may use `core` and `model`, nothing below reaches back up.

## Two things worth knowing before changing this

**A draft is allowed to be nonsense.** Every column but the identifiers is
nullable, blank outcomes persist, and no business rule runs on an autosave. The
form saves after three seconds of inactivity, so a save that could fail on an
incomplete form would fail on almost every call it ever made. Completeness is
checked once, at submission, by `service/validation.py`.

That is also why there is no unique index on outcome labels: two blank rows in
a half-typed form would violate it, and the autosave would start failing
exactly when the admin is mid-thought. Label uniqueness is a submission rule.

**Replacing a child collection needs its own flush.** `_clear_children` empties
`outcomes` and `resolution_sources` and flushes before the replacements are
attached. Within one flush SQLAlchemy issues the INSERTs before the DELETEs, so
the new position 0 meets the old position 0 and `uq_outcome_position` fires.
Remove that flush and every autosave after the first returns a 500.

## Database boundary

This service connects as `market_svc` and owns the `market` schema. It cannot
read `auth.*`, and nothing else can read `market.*`. That is enforced by
ownership and the absence of cross grants in `sql/02-schemas.sql`, not by
convention, and `unit_test/service/test_schema_boundary.py` asserts it so that
adding a convenience grant turns a test red.

The visible consequence is that `markets.creator_id` names a row in
`auth.users` and is **not** a foreign key. A market can outlive its creator's
account and no cascade will clean it up. ADR 0003 has the argument.

**Schema creation is `create_all`, not migrations.** Two services now issue it
against one database. They touch disjoint schemas so they do not race, but this
is the last change that gets away with it. Alembic should arrive with [F-1] #41.
