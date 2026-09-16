# Ledger service

Every credit in the system. Double-entry and append-only: a movement writes
matching debit and credit rows that sum to zero, a balance is the sum of an
account's entries rather than a column, and nothing anywhere can edit or remove
an entry.

[F-1] #41. Foundational infrastructure for [A-1] #29 and [B-1] #32 (the starting
grant), [B-2] #33 (viewing a balance), [T-2] #22 and [T-3] #23 (trades), [3.4]
#12 (settlement) and [4.1] #13 (history).

Why it looks like this: [ADR 0009](../../docs/adr/0009-the-ledger-write-path.md).
Why positions live here rather than with markets:
[ADR 0005](../../docs/adr/0005-trading-service-boundary.md).

## Running it

From the repo root, as part of the stack:

```bash
docker compose up --build        # this service on http://localhost:8003/docs
```

Nothing in `sql/` needs to change and no `docker compose down -v` is required.
`ledger_svc` and the `ledger` schema have been in `sql/01-roles.sql` and
`sql/02-schemas.sql` since #67, a sprint ahead of need, and the three tables are
created by `create_all` at startup.

## Tests

```bash
cd backend/ledger_service
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

docker compose up -d db          # from the repo root
.venv/bin/pytest                 # the whole suite
.venv/bin/pytest unit_test/core  # the pure layers, no database needed
```

Against Postgres as `ledger_svc`, from `LEDGER_TEST_DATABASE_URL`, under the
same grants production uses. That matters more here than in the other suites:
this service's claims are about what the database does under concurrency — a
row lock that makes two writers take turns, a unique index that turns a race
into a replay, a trigger that refuses an UPDATE. SQLite has no opinion about
any of that, so a suite running on it would pass while proving nothing. Do not
point the suite at the superuser either; the boundary tests would pass for the
wrong reason and so would the append-only ones.

## Endpoints

| Method | Path | Who | Purpose |
| --- | --- | --- | --- |
| GET | `/ledger/balances/me` | any signed-in user | [B-2] #33 |
| GET | `/ledger/entries/me` | any signed-in user | own history, with a running balance |
| GET | `/ledger/users/{id}/balance` | admin | [4.1] #13 |
| GET | `/ledger/users/{id}/entries` | admin | [4.1] #13 |
| GET | `/health` | — | probe |

The `{id}` those two admin routes take comes from `GET /admin/users?q=...` on
the auth service, which is [4.1] #13's other half: this service holds no user
table and knows a user only by the id in a signed token.

Full contract in [`docs/api/ledger-service.md`](../../docs/api/ledger-service.md)
and, authoritatively, at `/docs`.

## Layout

```
core/       config, engine, token verification, errors, keyset cursors
model/      entities.py (the three tables), schemas.py (the wire contract)
service/    accounts.py, posting.py, grants.py, ledger_service.py
controller/ routes, dependencies, error mapping, token transport
```

Imports point one way: `controller` uses `service`, `service` uses `core` and
`model`, nothing below reaches up, and `main.py` is the only file that knows
about everything. Inside `service/` the same rule holds: `accounts` knows
nothing of the others, `posting` uses `accounts`, `grants` uses `posting`, and
`ledger_service` uses all three.

## Five things worth knowing before changing this

**A balance is never stored.** `SUM(amount)` over the account's entries, every
time. There is no column for it and adding one would create a second source of
truth for money, which is the failure this design exists to avoid. Three
separate acceptance criteria — [B-1] #32, [B-2] #33, [4.1] #13 — say the
displayed balance equals the sum of the entries; deriving it is how that
becomes true by construction rather than by vigilance.

The running balance beside each history entry is the same rule applied to a
position rather than to now: `SUM(amount)` over everything up to and including
that entry, one aggregate per page, then subtraction down the rows. A
`balance_after` column would be that second source of truth, one concurrent
write away from disagreeing with the entries above it. Anchoring on the
position rather than counting back from the live balance is also what keeps a
page stable while somebody trades underneath it.

**The platform account is supposed to be negative.** Its balance is minus the
credits in circulation, and it is the only account exempt from the overdraft
check. That exemption is what makes the starting grant a movement between two
accounts rather than credits appearing from nowhere, and it is what lets
`SELECT SUM(amount) FROM ledger.entries` be zero over the whole table rather
than over a carefully chosen subset. If that query ever returns anything else,
something is badly wrong and the suite should have caught it.

**Reading a balance can write.** The first read of a new user's balance or
history mints their starting grant ([B-1] #32), because nothing tells this
service that a registration happened — the auth service does not know credits
exist and there is no event between them. It is one insert per user, ever,
keyed on the user id so concurrent first requests race safely.
`backend/auth_service/README.md` and ADR 0009 both carry the argument against
an outbox, an event, and a balance column.

Changing `STARTING_CREDITS` does not re-grant anybody. The grant has been
written and nothing rewrites an entry, so a new value reaches accounts created
after it and no others.

That promise is held by `ensure_granted` asking whether the grant exists before
it reads the configured amount, and it is not free. `posting.post` fingerprints
the legs it is handed and refuses a key that already names a *different*
movement — correct for a trade, and wrong for this, where the key is the user id
and the amount is a setting an administrator may edit. Build the legs first and
every user granted under the old value gets `IdempotencyKeyReused` on their own
balance for ever. It shipped that way in #77; `test_grants.py`'s
`test_a_read_after_the_grant_does_not_touch_the_write_path` is what stops it
coming back.

**`ledger.accounts` exists to be locked.** It holds no balance and almost no
data. Its job is to give `posting.post` a row to take `SELECT ... FOR UPDATE`
on before it reads a balance, which is the whole of "concurrent trades cannot
overdraw". Remove the lock and `test_concurrency.py` ends with an account five
hundred credits in the red. The locks are taken one statement at a time,
ascending by id, because Postgres locks rows in the order the plan produces
them and a single `IN (...) ORDER BY ... FOR UPDATE` can still deadlock.

**Append-only is a trigger, and it is one step weaker than the audit log's.**
`model/entities.py` attaches a statement-level trigger to `ledger.entries` as an
`after_create` DDL event, so it ships with the table everywhere the table ships
— including `unit_test/conftest.py`, which rebuilds the schema per test. A
trigger written into `sql/` instead would be dropped by the first rebuild and
never come back.

Be honest about the difference: `ledger_svc` owns this table and could drop its
own trigger, where no writer can touch `audit.admin_actions` at all. The upgrade
is to move these tables into `sql/` under superuser ownership and grant this
service INSERT and SELECT only — at the cost of every future column becoming
hand-applied SQL. Worth doing if this ever holds anything but mock credits.

## The write path has no endpoint

`service/posting.py` is the whole of the issue's "idempotency key on writes;
serialization so concurrent trades cannot overdraw", and nothing routes to it.

That is deliberate and it is not laziness. A write endpoint needs an answer to a
question no other service here has had to ask: how does a *service* prove it is
a service? Every route in this repository authenticates a person from a signed
access token, and a ledger write route that accepted a trader's own token would
be a route for minting yourself credits — which is exactly what the trading
service forwarding its caller's token would build.

[T-2] #22 has the caller, so it makes that decision. What lands here is the
primitive, the rules and the tests, and the primitive is not scaffolding: the
starting grant goes through it, so the double entry, the idempotency and the
lock are exercised by shipped behaviour rather than only by the suite.

## Database boundary

One schema, one login role, no grants to or from anywhere else.
`ledger.accounts.owner_id` names a row in `auth.users` and cannot be a foreign
key, because `ledger_svc` has no grant on that schema and never will. The value
comes from the `sub` claim of a signature-checked token. Same trade as
`market.markets.creator_id`; ADR 0003 has the argument, and
`unit_test/service/test_schema_boundary.py` fails if anybody relaxes it.

The one exception is the one every service has: INSERT on
`audit.admin_actions`, and no SELECT. Nothing here writes an audit entry yet —
the starting grant is not an administrative action — but [3.4] #12's settlement
will, in the same transaction as the payouts it records. ADR 0006.

## Configuration

| Variable | Required | Notes |
| --- | --- | --- |
| `DATABASE_URL` | yes | No default. A default is a credential in the repo. |
| `JWT_SECRET` | yes | No default. Must match the auth service's. |
| `STARTING_CREDITS` | no | Defaults to 1000. Not a credential. |
| `CORS_ORIGINS` | no | Comma-separated. Exact origins, never `*`. |

## Schema creation is `create_all`, not migrations

Fine while this service owns its schema alone, and it is the same bet the auth
and market services make. It is a worse bet here than there, because this is the
fourth service issuing DDL at startup against one Postgres and because the data
is money.

`CLAUDE.md`, the root README and `sql/migrations/README.md` all said Alembic
would arrive with this ticket. It did not — retrofitting four services' startup
and conftests is a change of its own size — and it has its own issue instead.
Until then, a new column here needs a hand-applied `ALTER TABLE` in
`sql/migrations/`, exactly as the market service's did.
