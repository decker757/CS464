# CS464

LMSR prediction market. Users trade shares in binary outcome markets against an
automated market maker, using mock credits.

Three people: Ernest and Ihsan on backend, Michelle on frontend. Planning lives
in GitHub Project v2 #6.

## Layout

```
backend/auth_service/   registration, login, logout, sessions   [A-1..A-3]
backend/market_service/ drafting, submitting, publishing markets [1.1] [1.3]
backend/audit_service/  reading the shared admin action log     [4.3]
sql/                    roles, schemas and grants for the shared Postgres
sql/migrations/         hand-applied ALTERs, until Alembic ([F-1] #41)
docs/adr/               decisions that were expensive to make
docs/api/               endpoint contracts for the frontend
scripts/                sprint digest to Telegram
.github/workflows/      path-filtered CI, one workflow per area
```

More services are coming: a ledger ([F-1] #41), a stateless trading composite
(the [T-*] epic), and a websocket server ([F-2] #42). The LMSR pricing engine
([F-3] #43) is a module rather than a service, and lives wherever `q` lives;
ADR 0005 says why.

## Running things

```bash
cp .env.example .env          # fill in every blank; compose refuses to start otherwise
docker compose up --build     # auth :8000/docs, market :8001/docs, audit :8002/docs
```

```bash
cd backend/auth_service       # or market_service, or audit_service
.venv/bin/pytest              # needs `docker compose up -d db`
.venv/bin/pytest unit_test/core unit_test/model   # no database needed
```

## Things that will waste your time if you do not know them

**`DATABASE_URL` and `JWT_SECRET` are required and have no defaults.** That is
deliberate. A default database URL is a credential in the repo, and a default
signing key is a published key that would silently sign real sessions. The
service refuses to boot without them.

**Changing anything in `sql/` needs `docker compose down -v`.** Postgres runs
init scripts only on first initialisation of the data volume. Without the `-v`
your changes appear to do nothing. It destroys local data.

Every statement in `sql/02-schemas.sql` is idempotent, so re-running that file
against a live database is a safe way to pick up a schema or grant change
without the wipe. That is all `sql/migrations/0002-audit-admin-actions.sql`
does, plus creating a login role, which is the one thing that file cannot do
for itself because the password is not in the repository. Roles are
cluster-wide; schemas and grants are per database, so apply it to `cs464` and
to `cs464_test` if yours predates the change.

**Adding a column does not reach a database that already has the table.**
The auth and market services call `create_all` at startup (the audit service
does not; it owns no table), and that only ever issues CREATE TABLE IF NOT
EXISTS, so a new column in `model/entities.py` reaches a fresh database
automatically and an existing one never. The service then dies on
every request with `column ... does not exist`, which reads like a code bug and
is not one. [1.2] #2 hit this on both the dev and the test database.

Write an idempotent `ALTER TABLE` in `sql/migrations/` and apply it by hand:

```bash
docker compose exec -T db psql -U cs464 -d cs464 -v ON_ERROR_STOP=1 \
  -f /sql/migrations/0001-market-lmsr-parameters.sql
```

These do **not** belong in `sql/` itself. `00-init.sh` names its two files
explicitly and runs only on first initialisation of the volume, so a file added
there would never run on the database that needs it — and on a fresh volume it
runs before any service exists, when there is no table to alter.

The test databases handle themselves: `unit_test/conftest.py` drops and
recreates the schema per test, so a model change is picked up automatically
there. It is the long-lived `cs464` database that drifts. [F-1] #41 replaces
all of this with Alembic.

**Tests run against Postgres, not SQLite**, each suite as its own service role
under production grants, from its own `<SERVICE>_TEST_DATABASE_URL`. The two
engines disagree about naive versus aware timestamps and about functional
unique indexes, and both differences have already caused bugs here. Do not
"simplify" this to SQLite, and do not point a suite at the superuser: the
cross-schema denial tests would pass while proving nothing.

**A published market is frozen, and the autosave has to be told.** Publication
([1.3] #3) is one way: `_save_once` refuses every write to an OPEN market,
whatever status the request asks for. Remove that branch and the create form —
which may still be open behind the publish button — reverts a live market to a
draft on its next three-second tick. Publishing also re-runs every submission
rule against the clock at publish time, because a market that sat submitted
past its own close time would otherwise go live already closed. ADR 0008.

**An admin is made by hand, and needs a fresh login.** Registration always
creates a trader. Promotion is `UPDATE auth.users SET role = 'admin' WHERE
lower(username) = '...'`, and the user must log in again, because authority
rides in the access token rather than being looked up.

The *first* admin stays a manual UPDATE permanently: ADR 0007 declines the
bootstrap account and the SUPER_ADMIN tier both. Every admin after the first is
promoted through the role-change endpoint by an existing one, and the fresh
login is still required either way.

**A market's status is a Python enum and needs no migration; a new column
does.** `market.markets.status` is a non-native `Enum`, and SQLAlchemy has
defaulted `create_constraint` to False since 1.4, so the column is a plain
`varchar(24)` with no CHECK. Adding a member to `MarketStatus` is a Python
change and nothing else. Verify before trusting that:

```bash
docker compose exec -T db psql -U cs464 -d cs464 \
  -c "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint \
      WHERE conrelid = 'market.markets'::regclass;"
```

A new *column* is the usual story and still needs a hand-applied `ALTER TABLE`,
as below. [1.3] #3 added one member and one column, and only the column needed
`sql/migrations/0003-market-published-at.sql`.

**Replacing a child collection in SQLAlchemy needs its own flush.** Within one
flush the INSERTs for the new rows are issued before the DELETEs for the
orphans, so a unique constraint on the child sees both. `market_service`'s
`_clear_children` exists for exactly this; remove it and every autosave after
the first returns a 500.

**The audit log is written by the acting service, not posted to the audit
service.** There is no write endpoint on :8002 and there will not be one. An
admin action appends its own entry through `service/audit.py`, on the request's
own session, and does not commit — the action's transaction commits both or
neither. That single fact is the whole durability argument, and it is why there
is no queue, no retry and no broker anywhere near this. ADR 0006.

If you add an admin action, log the decision and never the keystrokes. Draft
autosave fires every three seconds and is deliberately not logged; logging it
would bury every real action within one sitting. Submission and publication are
logged separately, because a market can sit submitted for a week and only the
second of the two put anything in front of a trader.

**`audit.admin_actions` must never be added to a service's `Base.metadata`.**
Everything mapped there is created by `create_all` at startup and dropped by
`unit_test/conftest.py` per test. Either against this table fails — no service
has CREATE or DROP on the audit schema — and the service dies at boot. Writers
declare it as a standalone `Table` on its own `MetaData` (see
`market_service/model/audit.py`); the audit service maps it but has no
`create_all` at all.

**A writing service cannot read the log it writes to, including in its own
tests.** It holds INSERT and no SELECT, so that no service can read another's
actions. `market_service`'s audit tests open a second engine as `audit_svc`
from `AUDIT_TEST_DATABASE_URL`, which is why that variable is set for its CI
job too. Nothing anywhere holds UPDATE, DELETE or TRUNCATE, so no suite can
clean the table: tests scope themselves to a fresh actor id instead.

**Never commit a credential, including in an example file.** GitGuardian runs
on every pull request and it is usually right. Use angle-bracket placeholders:
`postgresql+asyncpg://user:<PASSWORD>@host:<PORT>/db`. A real-looking username
beside a real-looking password will be flagged even when both are fake.

**A squash merge kills the branch.** Squash, merge commit and rebase are all
enabled on this repo; #65 was squashed. A squash rewrites the content into one
new commit with no shared ancestry, so pushing more work to that branch and
re-opening a pull request collides on every file. After any squash merge, start
a fresh branch from `dev`.

**A workflow skipped by a paths filter reports no status at all.** If you add
branch protection, do not mark one required, or pull requests touching the
other side will wait forever. Require the job, not the workflow. Also never
mark `notify` required; it only runs on failure by design.

## Backend conventions

Imports point one way only: `controller` may use `service`, `service` may use
`core` and `model`, and nothing below reaches back up. `main.py` is the only
file allowed to know about everything. That is why the error classes live in
`core` while the handler that turns them into HTTP responses lives in
`controller`.

Business rules belong in `service` and are tested there without HTTP. Status
codes, cookies and response shapes belong in `controller` and are tested there.
A new test that goes through a route to assert a business rule is in the wrong
layer.

Each service owns one Postgres schema and connects as its own role. There are
no cross grants, so a join across a service boundary fails with a permission
error rather than quietly working. Do not add a grant to make something
convenient; that is a decision to couple two services.

There is exactly one exception, and it is narrow on purpose: every service
holds `INSERT` on `audit.admin_actions` and nothing else — no SELECT, so it
still cannot read another service's data. That schema is owned by the superuser
rather than by any service, and `audit_svc` is the only role that may read it.
`market_service`'s `test_the_audit_grant_is_exactly_insert` asserts the shape
of the exception, so widening it turns a test red. ADR 0006 argues why it is a
different kind of thing from the grants above.

The auth service does not know that credits exist. There are tests that fail if
the word appears in a response or on the `register` signature. If you need a
balance, that is the ledger's job. The same rule runs the other way: the market
service holds no user table and authorises entirely from the `role` claim in a
signed token, so it never learns that an account was suspended or deleted until
that token expires. See `docs/adr/0003-market-service-boundary.md`.

## Frontend and backend split

Most user stories are full stack. The story issue belongs to whoever owns the
dominant side; the minority side becomes a linked sub-issue tagged `[FE]` or
`[BE]`. Never assign backend-heavy work to Michelle.

The OpenAPI page at `/docs` is the contract between the two. Change a schema
and you have changed the contract.

## Branches

`dev` is where work lands. `staging` and `main` are promotion targets. Branch
from `dev`, name it `<issue>-<slug>`, open a pull request into `dev`.

Use `Refs #N`, not `Closes #N`, when a ticket has open sub-issues. Auto-closing
a parent whose frontend half is unbuilt hides work.

## Decisions already made

Do not relitigate these without reading them: `docs/adr/`.

- **0001** self-hosting auth rather than a managed provider
- **0002** cookies for browsers, bearer tokens for services
- **0003** a separate market service, and admin authority carried in the token
- **0004** one idempotent endpoint for both draft autosave and submission
- **0005** a composite trading service, and positions with the ledger
- **0006** one shared audit log, written in the acting service's transaction
- **0007** a flat admin tier, with role changes audited rather than approved
- **0008** publishing as its own endpoint, from SUBMITTED only, and one way

Three known constraints recorded there. Logout cannot revoke an already-issued
access token, so the 15-minute lifetime bounds the window. A `SameSite=Lax`
cookie is not sent cross-site, so the frontend and API must share a registrable
domain or the scheme changes before [5.3] #19. And the audit log's atomicity
holds only while every service shares one database: split them and the service
that moves needs an outbox and a relay, which is the machinery ADR 0006 exists
to avoid paying for while it is unnecessary.
