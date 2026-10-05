# ADR 0020: Migrations by Alembic, per service, run as a one-off step

- **Status:** Accepted
- **Date:** 2026-10-04
- **Affects:** [F-5] #75, [5.3] #19, ADR 0006, ADR 0009, ADR 0012, and every service that owns a schema
- **Implemented in:** `backend/shared/migrating.py`, `backend/shared/testing.py`, `backend/{auth,market,ledger}_service/{alembic.ini,migrations/,migrate.py}`, `docker-compose.yml`, `.github/workflows/ci-backend.yml`

## Context

Before #75, auth, market and ledger each created their tables with
`create_all` when they started. `create_all` only ever issues
`CREATE TABLE IF NOT EXISTS`. So a new column reached a fresh database and
never an existing one, and the service then failed every request with
`column ... does not exist`. That reads like a code bug and is not one.

`sql/migrations/` filled the gap with hand-applied, idempotent `ALTER`s, and
nothing recorded which of them a database had been given. By #75 there were six
of them across two services (0001 and 0003–0006 for market, 0007 for the
ledger). A development database could be missing any of them, with nothing to
say so. Three services were issuing DDL at startup against one Postgres, and
one of them holds money.

## Decision

### Alembic per service, inside the service's own boundary

auth, market and ledger each have an `alembic.ini`, a `migrations/` directory
and one history, starting from a baseline revision `0001`. The audit service
owns no table and has no migrations. The realtime service has no database
(ADR 0010).

A migration connects with the service's own `DATABASE_URL`, so it runs as the
service's own role: never the superuser, and with no cross-schema grant. Alembic
keeps its version table in the service's own schema (`auth.alembic_version`,
`market.alembic_version`, `ledger.alembic_version`). Each role owns its schema
(`sql/02-schemas.sql`), so it can create that table there.

That gives three separate histories in one database. Nothing else would work
under this repository's grants: no role can write to another service's schema,
so no role could keep a single version table for all three. And three
independent services should have three independent histories.

Every comparison is limited to the service's own schema. `audit.admin_actions`
belongs to the superuser (ADR 0006), and no service's migrations ever see it,
so none of them can offer to create or drop it.

`sql/01-roles.sql`, `sql/02-schemas.sql` and the audit table stay in `sql/`.
Roles, grants and the shared audit table are the database's business, not any
one service's history.

### A separate one-off step, before the app

Compose has a `<svc>-migrate` service for each of the three: the app's own image,
running `python migrate.py`, with `restart: "no"`. The app service waits for it
with `condition: service_completed_successfully`, so the app starts only if the
step exited 0. The app itself issues no DDL at all.

This means a migration that fails stops the deploy before the new code serves a
single request. It is also the same step #19's deploy pipeline will run.

CI runs the same `migrate.py` against its empty test database, as the service's
own role, before the suite starts. So a revision that cannot apply under the
real grants fails the job.

**The migrate step gets `DATABASE_URL` and nothing else.** It does not read the
service's settings, which also require `JWT_SECRET`. A step that only issues
DDL has no use for a signing key, and every container that holds one is one
more place it can leak from. DECISIONS.md, "The migrate step reads
DATABASE_URL alone, not the service's settings".

### Run the step once per deploy, never once per replica

This is a known constraint, and #19 inherits it. The migrate step takes no
lock. Two copies running at the same time against one database are not
coordinated.

What happens if they do: where two runs collide, the one that loses fails,
either on a duplicate object (a table the other run has just created) or on
Alembic's check that it updated exactly one version row. Either way it exits
non-zero and its transaction is rolled back. No
interleaving stamps a schema that did not match the models, because a stamp
only ever follows a comparison that found no differences. So the failure is
loud and safe, but it is still a failure: compose starts an app only after its
migrate step succeeds, so a replica whose step lost the race does not start.

Compose already runs it correctly. There is one `<svc>-migrate` per service,
whatever the app's replica count. #19's pipeline must keep that shape: run the
step once, as its own job, before rolling out the app.

### The suites rebuild from the models, and guard tests hold the migrations to them

Each `unit_test/conftest.py` still builds its schema from `Base.metadata`, as it
did before #75. It does not run the migrations per test.

Instead, each of the three services has a `unit_test/test_migrations.py`. It
migrates an empty schema, runs the step a second time (compose runs it on every
`up`, so a second run must change nothing), and checks that the result is
exactly what the models build. It checks twice over: with Alembic's own
comparison, and by comparing the Postgres catalog of both schemas. It also
proves that the app starts without creating a table, and that compose starts
the app only after its migrate step succeeds.

DECISIONS.md, "The suites rebuild from the models per test, and guard tests
hold the migrations to them", has the trade.

### A database from before #75 is adopted, for now

`migrate.py` finds the service's schema in one of three states:

- **It has a version table.** Upgrade to head.
- **It has none of the service's tables.** Upgrade from nothing.
- **It has tables and no version table.** The database was built before #75.

For the third case, the step first creates any of the service's tables that are
missing. Every boot before #75 ran `create_all`, which would have done exactly
that, so a database that is only behind on whole tables is not drift. It then
compares the schema with the models.

- **If they match,** the database is stamped at the baseline and then upgraded
  as normal.
- **If they do not,** the step exits non-zero and prints every difference by
  name (for example `add_column auth.users.is_suspended`). The message points at
  the missing hand-applied file in git history, or at `docker compose down -v`,
  which destroys local data.

Creating the missing tables and comparing happen in **one transaction**. A
refusal rolls it back, so a refused database is left exactly as it was, without
even the missing tables. The stamp is written only after that transaction has
committed.

**Adoption is transitional.** Matching the models proves a database is at
*head*. Head is the baseline only until a second revision exists. After that, a
database that matches the models is not at the baseline, and stamping it there
would make the upgrade run every later revision again on a schema that already
has them. So once a service's history has a second revision, a database from
before #75 is refused with only the `down -v` message, and nothing is changed.

### One runner, in `backend/shared/migrating.py`

The body of every `env.py`, the own-schema filter and the adoption rule live in
one shared module. Without it there would be three copies, and a difference
between them would be a bug rather than a design choice. A filter that let the
`audit` schema in would offer to drop a table no service may touch. Two
adoption rules would adopt a database that the third refuses. That clears ADR
0012's bar for `shared/`, and ADR 0012 carries an amendment adding it.

The runner imports no service. Each service binds it to its own `Base.metadata`
and `SCHEMA` in two small files, `migrations/env.py` and `migrate.py`. Those two
are composition roots, like `main.py`, so they import `shared` directly. There
is no `core/` seam for them, because the binding needs `model`, and every
service's `model/entities.py` already imports `core.database`. A seam in
`core/` that imported `model` would make `core` and `model` import each other,
`model` through `core.database`.

### What Alembic's comparison cannot see

Checked against Alembic 1.20.0 and SQLAlchemy 2.0.52, the versions pinned in
each service's `requirements.txt`.

- **The `search_path` has to be `public`.** `sql/02-schemas.sql` gives each
  role its own schema as its `search_path`, and Alembic reports the
  connection's default schema as None. The filter treats None as `public` and
  leaves it out, so without an override the service's whole schema is invisible
  and every model table reads as missing. Treating None as the service's schema
  instead fails the other way: SQLAlchemy reflects a foreign key to a table on
  the `search_path` without its schema, while the models name one, so every
  foreign key comes back as dropped and re-added. So every migration connection sets
  `search_path` to `public`, and all three services then compare clean.
- **Non-native enums** compare as the plain `varchar(n)` they are,
  **functional indexes** such as `lower(username)` are compared by expression,
  and an index's **sort order** (`DESC`, `NULLS FIRST`) is compared too.
  None of them needs help.
- **CHECK constraints, partial-index predicates and triggers are not compared
  at all.** The guard tests cover them by comparing the Postgres catalog
  (`information_schema.columns`, `pg_indexes`, `pg_get_constraintdef`,
  `pg_get_triggerdef`) of a migrated schema with a model-built one, which
  checks sort order a second time as part of each index definition. Market's
  also asserts `ix_markets_due_close`'s predicate by name, and the ledger's
  asserts its append-only trigger by name.

Adoption checks the ledger's append-only trigger by hand, through the runner's
`extra_drift` hook, and refuses a ledger that has lost it. It does not check
`ix_markets_due_close`'s predicate. A database from before #75 got that index
either from `create_all`, which builds the model's, or from
`sql/migrations/0004`, which spelled the same predicate.

### The ledger's trigger SQL is shared, so editing it needs a new revision

The baseline installs `ledger.entries`' append-only trigger (ADR 0009) by
running the same two DDL objects the model's `after_create` events run,
`REJECT_MUTATION_FUNCTION` and `INSTALL_APPEND_ONLY_TRIGGER`, imported from
`model/entities.py`. So there is one copy of that SQL, and the suite's per-test
rebuild and a migrated database install exactly the same trigger.

The price: the baseline imports live model code. Editing those two constants
changes what a *fresh* database gets, and nothing else, because the baseline
has already run on every existing database. A new trigger body therefore needs
a revision of its own, with its own copy of the SQL.

### `sql/migrations/` keeps one file

`0002` stays. It creates a login role and re-runs `sql/02-schemas.sql`, and no
service's history can own either of those. The other six files, 0001 and
0003–0007, are folded into the baselines and deleted in #75's last pull request.
They remain in git history, which is where the adoption refusal points.

## Consequences

**A model change is half a change until it has a revision.** Add one with
`alembic revision` in the service's directory (root `CLAUDE.md` has the
command), and read every line of it. `--autogenerate` is a starting point only:
it misses everything listed under "What Alembic's comparison cannot see". The
guard tests fail when the migrations and the models disagree.

**Two branches that both add the next revision give Alembic two heads**, and
the migrate step fails until one is rebased onto the other. That failure is
loud and early.

**Switching a development database between branches has a direction.** A
pre-#75 branch's `create_all` is harmless on a migrated database. A branch
whose history does not contain the database's current revision fails the
migrate step with "Can't locate revision". Downgrade from the newer branch
first, or `docker compose down -v`.

**Each app image carries Alembic**, because the migrate container is the app's
image with a different command.

**A pre-#75 database must be migrated before the second revision lands.** Once
any service has a revision after `0001`, adoption ends for that service, and a
database that was never migrated can only be rebuilt. That service's adoption
tests go with it: the pull request that adds its first revision deletes the
matches, missing-table and drift tests, and keeps one test that a schema built
like before #75 is refused as past the baseline, against the real history.

## Alternatives rejected

**Migrate at startup, in each app's lifespan.** One line instead of three
compose services. Rejected because every replica would run it at boot and race
the others, and because a failing migration would surface in an app that is
already starting rather than stopping the deploy before it. #19's pipeline
needs a step it can run once.

**One Alembic history for the whole database, run as the superuser.** One
version table and one command. Rejected because it needs a role that can write
every schema, which is the cross-schema coupling CLAUDE.md says not to add for
convenience. It would also put `audit.admin_actions` (ADR 0006) inside a
history some service runs.

**Migrating per test instead of rebuilding from the models.** What #75's scope
asked for. Rejected for cost, in DECISIONS.md, "The suites rebuild from the
models per test, and guard tests hold the migrations to them".

**Refusing every database from before #75**, so that everyone runs
`docker compose down -v` once. Simpler, and it would have thrown away every
local market and ledger entry for a schema that already matched. Adoption costs
one comparison, and ends on its own once a second revision exists.

**Stamping a database from before #75 without comparing it.** It would record
a database that missed a hand-applied file as being at the baseline. The
service would then fail with `column ... does not exist`, the symptom #75
exists to end, with a version table saying all is well.

**A lock around the migrate step** (a Postgres advisory lock). It would make
two concurrent runs wait instead of fail. Not taken because nothing runs the
step concurrently: compose runs one per service. It is the first thing to add
if #19's platform cannot run it once.

**A `core/` seam for the runner, like every other shared module.** Rejected
because the seam would have to import `model` to bind the metadata, and every
service's `model/entities.py` already imports `core.database`, so the two would
import each other. `env.py` and `migrate.py` are composition roots instead.

---

> **This reverses to migrate-at-startup if #19 deploys to a platform that
> cannot run a one-off job before the app starts.** The test is the platform's
> documentation, not preference. It would then need the lock above as well,
> because every replica would run it.
>
> **The suites switch to migrating per test** the first time a defect reaches
> a database built by migrations after the guard tests passed, because that
> shows the guard is not enough. Also when a revision does data work the
> models cannot express, since a rebuild from the models would never run it.
>
> **The adoption path is deleted** once no database from before #75 remains.
> Those databases are the team's own local `cs464` and `cs464_test`, not any
> deployed one: nothing is deployed yet, and every environment #19 creates
> will start after #75. So the test is either of two things. Every teammate's
> development database has run its migrate step once. Or every service's
> history has a second revision, at which point adoption refuses every
> database it finds and the path is dead code.
