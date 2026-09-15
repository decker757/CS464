# Audit service

The immutable record of every administrative action, across every service.
Covers [4.3] #15.

It is the smallest service in the repository and the one with the most unusual
shape, because almost everything it might have done is deliberately somewhere
else:

- It **owns no schema.** `audit.admin_actions` belongs to the superuser and is
  created by `sql/02-schemas.sql`, because several services append to it and
  none of them should be able to define it.
- It has **no `create_all`.** It could not create that table if it tried; there
  is no CREATE grant. It is the only service here without one.
- It **never writes.** An entry is appended by the service performing the
  action, in the same database transaction as the action itself.
- It **cannot edit or delete.** No role holds `UPDATE`, `DELETE` or `TRUNCATE`,
  and a trigger refuses all three even for the table's owner.

[ADR 0006](../../docs/adr/0006-audit-log-write-path.md) is the argument for all
of that, including why this is not a Kafka topic.

## Running it

The whole stack, from the repo root:

```bash
cp .env.example .env          # fill in every blank, including AUDIT_DB_PASSWORD
docker compose up --build
```

Interactive API docs at http://localhost:8002/docs, which is the contract for
the admin console.

If your database predates [4.3] #15 it has no `audit` schema and no `audit_svc`
role, and the market service will fail every submission with `relation
"audit.admin_actions" does not exist`. Either wipe the volume, or apply the
migration and keep your data:

```bash
docker compose exec -T db psql -U cs464 -d cs464 -v ON_ERROR_STOP=1 \
  -v db_name=cs464 -v audit_password="$AUDIT_DB_PASSWORD" \
  -f /sql/migrations/0002-audit-admin-actions.sql
```

## Tests

```bash
docker compose up -d db          # from the repo root
.venv/bin/pytest
.venv/bin/pytest unit_test/core  # no database needed
```

The suite reads `AUDIT_TEST_DATABASE_URL` from the repo-root `.env`, the same
gitignored file docker compose reads, so no connection string lives in the
repository.

Two things about this suite are worth knowing before you change it.

**It cannot clean up after itself, and must not be made able to.** The log is
append-only and no role holds `DELETE` or `TRUNCATE`, so rows from every
previous run are still in `cs464_test`. Each test scopes itself to an actor id
nothing else has used. A fixture that truncated the table would be a suite
running under weaker grants than production, which is exactly what
`test_it_connects_as_its_own_role` exists to prevent.

**`unit_test/model/test_entities.py` is load-bearing.** This service describes
a table it does not own, with no `create_all` to reconcile the two, so those
tests are the only thing keeping `model/entities.py` in step with
`sql/02-schemas.sql` until [F-1] #41 brings Alembic.

## Layout

The same four layers as the other services, and the same import direction:
`controller` may use `service`, `service` may use `core` and `model`, nothing
below reaches back up. `main.py` is the only file that knows about everything.

```
core/config.py      settings; required values have no defaults
core/database.py    engine and session. No create_all, on purpose
core/paging.py      keyset cursors — why not OFFSET is in the docstring
core/security.py    token verification. No encode path, and a test keeps it that way
model/entities.py   a read model of a table this service does not own
service/            one query, with filters and paging
controller/routes.py one GET, and nothing that writes
```

## Adding an action type

Nothing changes here. The vocabulary lives in the writing service, and
`action_type` is an unconstrained string in SQL on purpose — a CHECK would let
a stale audit schema abort the admin action it is meant to record.

Write it from your own service (`service/audit.py` there), and add a row to the
table in [`docs/api/audit-service.md`](../../docs/api/audit-service.md) so the
frontend knows the value exists.
