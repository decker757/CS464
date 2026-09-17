# CS464

LMSR prediction market. Users trade shares in binary outcome markets against an
automated market maker, using mock credits.

Three people: Ernest and Ihsan on backend, Michelle on frontend. Planning lives
in GitHub Project v2 #6.

## Layout

```
backend/auth_service/     registration, login, logout, sessions   [A-1..A-3]
backend/market_service/   drafting, submitting, publishing markets [1.1] [1.3],
                          closing them at their closing time [F-4] or early by
                          hand [2.3], proposing an outcome once they have
                          [3.1], and a second admin approving or rejecting it
                          [3.2]
backend/audit_service/    reading the shared admin action log     [4.3]
backend/ledger_service/   credits, append-only, balances derived   [F-1]
backend/realtime_service/ live prices over a websocket, owns no data [F-2]
backend/shared/           the only code services import from each other [F-6]
sql/                      roles, schemas and grants for the shared Postgres
sql/migrations/         hand-applied ALTERs, until Alembic ([F-5] #75)
docs/adr/               decisions that were expensive to make
docs/api/               endpoint contracts for the frontend
scripts/                sprint digest to Telegram
.github/workflows/      path-filtered CI, one workflow per area
```

One more service is coming: the stateless trading composite (the [T-*] epic).
The LMSR pricing engine ([F-3] #43) is a module rather than a service, and
lives wherever `q` lives; ADR 0005 says why — which means it lives with the
ledger.

The websocket server ([F-2] #42) has landed, but only the transport half: the
socket, the pub/sub relay, the auth and the staleness rules. The authoritative
snapshot endpoint and the `state_version` it reports need `q` and `b`, so they
land with [F-3] #43 and [T-2] #22, on the ledger. `docs/api/realtime-service.md`
specifies them.

## Running things

```bash
cp .env.example .env          # fill in every blank; compose refuses to start otherwise
docker compose up --build     # auth :8000, market :8001, audit :8002,
                              # ledger :8003, realtime :8004
```

```bash
cd backend/auth_service       # or market_service, audit_service, ledger_service
.venv/bin/pytest              # needs `docker compose up -d db`
.venv/bin/pytest unit_test/core unit_test/model   # no database needed

cd backend/realtime_service   # the exception: no database, wants Redis
.venv/bin/pytest              # needs `docker compose up -d redis`
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
there. It is the long-lived `cs464` database that drifts. [F-5] #75 replaces
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

**A lock on the transition is half a lock.** `publish` holds the market row
while it flips the status, and that stops a second publish. It does nothing to
a resubmission, because a lock only queues *other lockers*: the save path read
the status without one, passed the frozen check, waited on its own INSERTs for
the publication to commit, and then wrote SUBMITTED back over OPEN. So the rule
is on the reader, not the transition — every read whose answer decides a write
is `with_for_update()`, in every service, and a check that has to hold "under
the lock" (an idempotency key, a balance, a status) is issued after the lock
statement. Where a row lock sits beside a multi-row one, the wider lock goes
first on every path, or two requests deadlock — `change_role` takes the
administrator set before the target even on a promotion, for that reason
alone. A new race test is evidence only once it fails with its lock removed,
and two sessions under `asyncio.gather` are not yet a race until both have
connected and wait on an `asyncio.Barrier`. ADR 0015.

**A market closes on the clock, and the status column is not the authority.**
A market stops accepting trades the instant `close_time` passes.
`market_service/service/closing.py` states that once — as
`is_open_for_trading` for an entity and `open_for_trading()` for a WHERE
clause — and every trade path, browse query and status filter asks it. The
CLOSED status is written a few seconds later by a background sweep, for the
readers that want a value to count and gate on ([2.1] #5, [3.1] #9, [3.4] #12).

Write it the other way round — gate trading on `status == OPEN` — and the sweep
interval becomes a correctness parameter: every second of it is a second of
trading on a market that has closed, and an outage widens it silently. No
interval makes that window zero; deriving the answer does. The sweeper falling
behind, or being switched off with `CLOSE_SWEEP_ENABLED`, makes a dashboard
count stale and cannot let a trade through. Same shape as the ledger's derived
balances, and the same reason. ADR 0011.

One reader deliberately does the opposite, and it is not an inconsistency.
Proposing an outcome ([3.1] #9) gates on `status == CLOSED`, so for up to one
sweep interval a market that has stopped trading still refuses a proposal.
Check which way the error points before "fixing" it: reading the status is
*stricter* than deriving, so the worst case is a propose control that appears a
few seconds late on a market nobody can trade in the meantime. On the trade
path a stale answer lets a trade through, which is why that one derives. ADR
0013.

An administrator can also close a market early ([2.3] #7), and that one derives
again — `POST /markets/{id}/close` refuses a market whose `close_time` has
already passed, however the status column reads. Same test as everywhere else:
which way does the error point. Reading the status here would accept a close in
the window before the sweep and write an audit entry claiming an administrator
stopped trading that the clock had already stopped. It is the only request that
stops trading by hand, it does not touch `close_time`, and a `closed_at` earlier
than `close_time` is how you tell the two kinds of close apart. ADR 0014.

The sweep is not a scan and the polling cost is not the interesting question:
`ix_markets_due_close` is partial on `status = 'open'`, so it reads an ordered
index holding only the open markets and stops. One admin with the create form
open writes 1,200 autosave transactions an hour; a ten-second sweep is 360
read-only probes. An automatic close writes no audit entry — the clock is not
an actor, and the `market.published` entry already recorded the `close_time`
that was approved — so every `market.closed_early` entry in the log is by
definition a human one, which is the whole reason it is named that way.

A proposal is decided by a second administrator ([3.2] #10), and like the early
close that request is not scoped to the creator: `approve-outcome` and
`reject-outcome` read through `get_any`, because the proposer is always the
creator and the decider must be somebody else. The proposer is refused on
**both** with `403 second_administrator_required` — rejecting your own proposal
is the un-propose ADR 0013 declined — and the rule is
`actor.id == proposed_by_id`, never the username and never `creator_id`. State
is checked before identity and identity before the reason, in the one
`_proposal_to_decide` both share. APPROVED is a status rather than
PENDING_RESOLUTION with `approved_at` set, so every later write is
`market_already_approved` and [3.3] #11 and [3.4] #12 get a
`status = 'approved'` working set shaped like the close sweep's. A rejection
sends the market back to CLOSED, nulls all seven proposal columns and leaves
`closed_at` alone. There are no `rejected_by_*` columns: the
`market.outcome_rejected` entry, carrying the reason and the proposal
snapshotted *before* the clear, is the record — take that snapshot after the
clear and the log loses the only copy.

**A decision names the proposal, because the lock cannot.** Approve and reject
both require the `proposal_id` the reviewer read, and `_proposal_to_decide`
refuses any other as `409 proposal_superseded` after the identity check. The
key is required and the value nullable: a proposal pending before
`sql/migrations/0006` has no id and is decided by quoting null. Do not
"fix" that with a backfill — a generated id lives only on a row the reviewer
cannot read, so it would strand exactly the proposals it was meant to rescue. The
row lock only serialises decisions that overlap. A reject-and-repropose that
has *finished* while a reviewer is still reading leaves a market that is
PENDING_RESOLUTION, by the same proposer, locked just as happily — so a
decision keyed by the market id alone approves a winner nobody on that screen
saw, or clears it with a reason written about another. Whenever a request
decides about a thing that can be replaced under the same parent row, make it
quote that thing's identity, and test it with the whole replacement cycle
committed between the read and the write. ADR 0016.

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

**A balance is never stored, and the platform account is supposed to be
negative.** `ledger.accounts` has no balance column and must not grow one:
a balance is `SUM(amount)` over that account's entries, derived on every read.
Three separate acceptance criteria ([B-1] #32, [B-2] #33, [4.1] #13) say the
displayed balance equals the sum of the entries, and deriving it is how that is
true by construction instead of by vigilance.

Every movement writes legs that sum to zero, and credits are minted by the one
`PLATFORM` account going negative — its balance is minus the credits in
circulation, and it is the only account exempt from the overdraft check. So
`SELECT SUM(amount) FROM ledger.entries` is always exactly zero over the whole
table. If it is not, something is badly wrong; `test_concurrency.py` asserts it,
and also asserts it per transaction, because a whole-table zero would survive
two mistakes that cancelled.

**Reading a balance writes, once per user, ever.** Registration does not grant
starting credits and the auth service still does not know that credits exist.
The ledger mints the grant lazily instead, on the first read of a balance or a
history, keyed `signup-grant:<user_id>`. A user with no entries is by definition
a user who has not been granted. There is no event, no outbox and no window in
which a new account's balance is observably wrong. ADR 0009.

Changing `STARTING_CREDITS` does not re-grant anybody: the transaction has been
written and nothing rewrites an entry. That holds because `ensure_granted` asks
whether the grant exists before it reads the configured amount. Hand the amount
to `posting.post` on every read instead — which is how #77 shipped it — and the
fingerprint over the legs stops matching the stored transaction the moment the
setting changes, so every already-granted user gets `IdempotencyKeyReused` on
their own balance and history, permanently. The rule belongs in the caller: a
reused key naming different money is a bug for a trade and an edit for a grant,
and only the caller knows which.

**The ledger's write path has no HTTP endpoint, on purpose.**
`ledger_service/service/posting.py` holds the double entry, the idempotency key
and the row lock, and nothing routes to it. Adding a write route means first
answering how a *service* proves it is a service — every route in this
repository authenticates a person from a signed token, and a ledger write route
that accepted a trader's own token is a route for minting yourself credits.
That decision belongs to [T-2] #22, which has the caller. The primitive is not
untested scaffolding: the starting grant goes through it.

**`ledger.entries` is append-only via a trigger that ships with the table.**
`model/entities.py` attaches it as an `after_create` DDL event, so it is
installed by `create_all` and by the per-test schema rebuild alike. Do not move
it into `sql/`: these are the service's own tables, so the first conftest
rebuild would drop the trigger and never restore it. It is one step weaker than
the audit log's, because `ledger_svc` owns this table and could drop its own
trigger; the README says so and names the upgrade.

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

**`backend/shared/` is importable, and almost nothing belongs in it.** [F-6]
#76 moved every build context to `backend/` — compose sets `context: ./backend`
and `dockerfile: <service>/Dockerfile` — so a service's image holds `shared/`
beside it and `PYTHONPATH=/app` makes the import resolve the way it does in a
checkout. `pytest.ini` says the same with `pythonpath = . ..`.

It holds token verification, the settings base, the role enum, the cursor
format and the test env loader. That is the whole list, and ADR 0012 spends
most of its length on what was left copied and why — `core/database.py` above
all, because one shared `Base` would enrol every service's tables in every
other service's metadata and the first conftest `drop_all` would hit a table
its role cannot touch.

Two rules keep the package from turning back into `core`. **Nothing in
`shared/` imports a service** — the verifier takes its secret and issuer as
arguments, the cursor decoder returns None instead of raising a domain error.
And **every service keeps a `core/` seam** that binds shared code to its own
settings and errors, so nothing under `service/` or `model/` imports `shared`
directly and the layering rule below still holds.

The bar for adding anything: every caller needs identical behaviour *and* a
divergence between two copies would be a bug rather than a design choice.
Similar is not enough.

**CORS does not apply to a WebSocket, so the socket checks the origin itself.**
There is no preflight on a handshake and the browser enforces nothing about who
may open one, so `CORSMiddleware` in the realtime service guards `/health` and
`/docs` and never sees the socket. `controller/transport.py::origin_allowed` is
the hand-written check, and `CORS_ORIGINS` is doing two unrelated jobs in that
service. Deleting it looks like removing a duplicate of the middleware and is
actually removing the only thing that will stand between a logged-in user and a
feed opened by another site on the day ADR 0002's `SameSite=None` possibility
arrives. A test fails if it goes.

The same asymmetry explains the auth flow there. A browser cannot set request
headers on a WebSocket — the JavaScript API has no parameter for them — so the
cookie is its only transport, and ADR 0002's same-registrable-domain constraint
decides whether live prices work at all rather than being a deployment
footnote. There is deliberately no token in the query string.

**The realtime service has no database and must not grow one.** No role in
`sql/01-roles.sql`, no schema in `sql/02-schemas.sql`, no `DATABASE_URL`, and
SQLAlchemy is absent from its `requirements.txt` on purpose. It is the only
backend service that cannot be broken by anything in `sql/` and the only one
that never needs `docker compose down -v`, and that is most of why a fifth
service was affordable at all. Anything it appears to need from the database is
a sign the work belongs in the service that owns the data. ADR 0010.

**A price is published after the trade commits, and the publish must never fail
the trade.** Publishing first announces a price that a rollback then un-makes.
Nothing subscribes on the producer's behalf and nothing acknowledges, so if the
publish throws, the trade has still happened and is still correct — log it and
carry on. The crash window between the commit and the publish is real and
accepted: a client that reconnects fetches a snapshot, which is the same
recovery path [X-4] #37 already requires. That is why there is no outbox here,
and it is the one place this repository knowingly does something ADR 0006
refused to do for the audit log — because a briefly stale price on a screen
that is about to reconcile is not a lost audit entry. ADR 0010.

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
- **0009** double-entry with derived balances, and a lazily minted grant
- **0010** a websocket relay that owns nothing, and Redis rather than the database
- **0011** the clock closes a market, and a sweep only writes it down
- **0012** a shared package, and the build contexts that had to move first
- **0013** proposing an outcome, from CLOSED only, with evidence, one at a time
- **0014** closing a market early, by any admin, with the reason in the log
- **0015** every read that decides a write is locked, and the wider lock goes first
- **0016** deciding a proposal, by any admin but the proposer, with APPROVED as a status

Three known constraints recorded there. Logout cannot revoke an already-issued
access token, so the 15-minute lifetime bounds the window. A `SameSite=Lax`
cookie is not sent cross-site, so the frontend and API must share a registrable
domain or the scheme changes before [5.3] #19. And the audit log's atomicity
holds only while every service shares one database: split them and the service
that moves needs an outbox and a relay, which is the machinery ADR 0006 exists
to avoid paying for while it is unnecessary.
