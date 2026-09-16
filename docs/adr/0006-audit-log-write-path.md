# ADR 0006: One shared audit log, written in the acting service's transaction

- **Status:** Accepted
- **Date:** 2026-09-15
- **Affects:** [4.3] #15, [1.3] #3, [1.4] #4, [2.3] #7, [3.1] #9, [3.2] #10, [3.4] #12, [4.1] #13, [4.2] #14, [4.4] #16, [F-1] #41
- **Implemented in:** `sql/01-roles.sql`, `sql/02-schemas.sql`, `backend/audit_service/`, `backend/market_service/service/audit.py`

## Context

[4.3] #15 wants an immutable log of every admin action, and its notes say
"foundational, several stories write to this". That is the whole difficulty.
The writers are not in one place.

| Action | Written by |
| --- | --- |
| [1.3] #3 publish, [1.4] #4 edit, [2.3] #7 close early | market_service |
| [3.1] #9 propose outcome, [3.2] #10 second-admin approval | market_service |
| [4.2] #14 suspend an account, [4.4] #16 change a role | auth_service |
| [3.4] #12 settle payouts | the ledger ([F-1] #41) |

So the log is cross-service by nature, and `sql/02-schemas.sql` grants nothing
between service schemas on purpose. The question was never "what shape is the
table". It was how an action in one service becomes a durable record in a log
that is not that service's to own, without either losing records or making the
admin action depend on something that can be down.

Three constraints were already fixed before this was asked.

**An action that takes effect with no record of who took it is the one failure
that matters.** Everything else — a slow read, a missing filter, an entry that
arrives late — is an inconvenience. "Nothing is untraceable" is the story.

**The services share one Postgres instance.** ADR 0003 put them in separate
schemas in the same database, and ADR 0005 kept them there.

**Nothing in this repository runs a message broker**, and nothing on the board
requires one except [F-2] #42.

## Decision

**The acting service appends the entry itself, in the same database
transaction as the action.** `market_service`'s submission does its UPDATE and
its INSERT into `audit.admin_actions` in one transaction. They commit together
or roll back together.

**One shared table, owned by nobody who uses it.** `audit.admin_actions` lives
in an `audit` schema owned by the superuser and created by
`sql/02-schemas.sql`, not by any service's `create_all`.

**The grants are the design.** Writers hold `INSERT` and nothing else — no
`SELECT`, so no service can read another's actions. `audit_svc` holds `SELECT`
(and `INSERT`, so its own suite can seed rows). Nobody holds `UPDATE`, `DELETE`
or `TRUNCATE`, and a statement-level trigger refuses all three even for the
table's owner.

**A separate read-only service serves it.** `backend/audit_service/` owns no
schema, issues no write, and exposes one filterable GET.

## Why the transaction is the whole durability story

The failure this design has to prevent is an admin action that took effect with
no record of who took it. Every alternative below produces it under some crash;
this one cannot, because there is no second write to lose.

```
BEGIN;
  UPDATE market.markets SET status = 'submitted' WHERE ...;
  INSERT INTO audit.admin_actions (...);
COMMIT;
```

Crash before the COMMIT and there is no action and no entry. Crash after and
there is both. There is no third outcome. No retry logic, no dead-letter queue,
no reconciliation job, and nothing to monitor — because the property is
enforced by the same mechanism that already guarantees the market's own rows
are consistent with each other.

`unit_test/service/test_audit.py::test_a_refused_submission_records_nothing` is
that claim as a test: a submission refused by validation leaves no entry, from
a second connection that can only see committed rows.

## Why not Kafka

This was the question the ticket arrived with, and the answer is that it does
not address the problem.

**A broker does not make two writes atomic.** Committing the market change and
then producing to a topic is a dual write. A crash between them loses the event
exactly as a failed HTTP call would, and the market is now submitted with
nothing recording who did it. The standard fix is a transactional outbox —
write the event to a table in the same transaction, relay it afterwards — at
which point the atomicity is coming from the transaction, and the broker is
transport for something already durable.

**Here the transaction is available without the relay.** The outbox pattern
exists because the event has to reach a system that cannot participate in the
local transaction. `audit.admin_actions` can: it is a table in the same
database, and Postgres transactions span schemas. The relay, the retry, the
dedupe on event id, the ordering caveats and the backlog monitoring are all
machinery for crossing a boundary that is not being crossed.

**A broker is not free.** A container to run, a consumer group to manage,
offsets to reason about, and a second place for an entry to be sitting when
someone asks where it is. For a log three administrators write to a few times a
day, that is cost without a matching benefit.

**Where a broker does earn its place is [F-2] #42.** Real-time price broadcast
has many subscribers, genuine fan-out and a latency requirement. If a broker
arrives in this project, that is the ticket that justifies it — and the audit
rows are already the right shape to relay if it does.

## Why not a synchronous call to the audit service

It has the dual-write problem *and* an availability problem. If the audit
service is down, either the admin action fails — an audit log that can take
down market administration — or the entry is dropped, which is the failure the
story exists to prevent. It is strictly worse than both the option above and
the one chosen, and was rejected on that basis rather than on taste.

## Why one shared table rather than one per service

Each service could log into its own schema, with no new grant at all. It was
rejected because it does not produce an audit log, it produces four of them.

[4.3] #15's third criterion is filtering by actor. An administrator's actions
span services — they suspend an account in `auth` and close a market in
`market` — so "what has this admin done" becomes a fan-out across every
service's API with a merge and a sort on the client, and a global chronological
view becomes impossible to page correctly. Every new service would also have to
grow its own audit endpoint, its own filters and its own retention story.

## Why this grant is not the coupling `sql/02-schemas.sql` forbids

That file's rule is real and this is a deliberate exception to it, so the
distinction has to hold up.

What it prevents is a service reading another service's business data — the
convenience join that welds two services together so they can never be
separated. The specific cost it accepts, recorded in ADR 0003, is that
`market.markets.creator_id` cannot be a foreign key into `auth.users`.

This grant is `INSERT` on one shared append-only log, and nothing else. No
writer can `SELECT` it, so no service can read another's actions, or its
business data, or anything else. No writer can `UPDATE` or `DELETE`, so no
service can affect what another has written. The only thing a writer gains is
the ability to add a row to a log that is not about any one service's domain.

`market_service`'s `test_the_audit_grant_is_exactly_insert` asserts the shape
rather than the existence of the exception, so a later `SELECT` added for
convenience turns a test red instead of quietly widening it.

## Why the read service owns nothing

`audit_svc` cannot create the table, alter it or drop it, and it does not
appear as its owner. An owner may `ALTER` and `DROP` regardless of what is
granted, so a service that owned this table could rewrite its way around every
guarantee above. The table belongs to the superuser and is created by
`sql/02-schemas.sql`, which is also why `backend/audit_service/` is the one
service in this repository with no `create_all`.

## Why the table has no constraints

No foreign keys, no CHECK on `action_type`, and only the columns a writer
always knows are NOT NULL.

This follows directly from the write path. The audit INSERT commits with the
action it records, so **any constraint this table can violate is a way for the
log to abort a legitimate admin action.** A CHECK listing the known action
types would mean that deploying a story which adds one, before the constraint
is widened, breaks the feature rather than just its logging. Refusing to record
is worse than recording a value the reader does not recognise.

The vocabulary lives in each writing service's code as a `StrEnum`, and the
reader treats an unfamiliar value as an opaque string — the same fail-open
choice, in the same direction, as `security._read_role` fails closed on an
unfamiliar role.

## Consequences

**This holds only while every service shares one database.** It is the binding
assumption, and the one to check before splitting them. If `auth` moves to its
own Postgres, its audit writes can no longer be in the same transaction, and
that service — only that service — needs an outbox and a relay. The rows are
already in the right shape for it, and the write site is one function.

**Denied and failed actions are not recorded.** A transaction that rolls back
takes its entry with it, which is exactly what makes the log trustworthy, and
it means a 403 on an admin route leaves no trace here. That is security
logging, which has a different retention story and a different volume profile,
and it should not share this table.

**Reads are not logged.** `require_admin` in the audit service deliberately
does not record that the log was read. Every entry here changed something, and
mixing views into the same table would bury the writes within a week.

**Each writing service carries its own copy of the writer.** `model/audit.py`
and `service/audit.py` will be duplicated into `auth_service` for [4.2] #14 and
into the ledger for [3.4] #12 — a fourth and fifth occurrence of the
duplication ADR 0003 accepted and ADR 0005 said to end. The reason is
unchanged and is still the Docker build context: each service builds from its
own directory with `COPY . .`, so a shared module is not importable until that
changes. When it does, this writer belongs in the extraction alongside token
verification and the LMSR engine.

> **Partly overtaken by [ADR 0012](0012-the-shared-package.md), [F-6] #76.** The
> build context is no longer the reason. `backend/shared/` exists and the
> writer could now move into it. It deliberately did not: ADR 0012 kept the
> extraction to what ADR 0005 authorised, and the audit writer is this
> record's to move. What is left to decide is narrow — the `Table` is
> identical, `AdminAction` is per service by design — and it is a decision
> about the audit log rather than about packaging.

**The audit table is not in any service's `Base.metadata`, and that is
load-bearing.** Everything mapped there is created by `create_all` at startup
and dropped by `unit_test/conftest.py` per test. Either against this table
fails — there is no CREATE or DROP grant — and the service dies at boot. The
writers declare it as a standalone `Table` on its own `MetaData`; the reader
maps it but has no `create_all` at all.

**No suite can clean up after itself.** Nothing holds DELETE or TRUNCATE, so
test rows accumulate in `cs464_test` forever. Tests scope themselves to an
actor id nothing else has used. This is a genuine cost and it was accepted
rather than worked around: a suite that could truncate the table would be a
suite running under weaker grants than production, which is the thing
`test_it_connects_as_its_own_role` exists to prevent.

**The market service cannot read the log it writes to, including in tests.**
Its audit tests connect a second engine as `audit_svc`, which is why
`AUDIT_TEST_DATABASE_URL` is set for the market service's CI job as well as the
audit service's.

**A fifth backend service.** The write path needed none of it — the grant and
one function would have satisfied the first two acceptance criteria. The read
API is what needs a home, and no existing service could host it without being
handed `SELECT` on every other service's actions.

**Adding a role and a schema costs everyone a volume wipe**, or
`sql/migrations/0002-audit-admin-actions.sql`, which exists so it does not have
to. That file creates the role and re-runs `sql/02-schemas.sql`, which is
idempotent throughout.

## Alternatives rejected

**Kafka, or an outbox with any transport.** Addressed above: the broker does
not provide the atomicity, and the outbox that does provide it is unnecessary
while one database serves everyone. Revisit if the services split databases, or
if [F-2] #42 brings a broker and relaying these rows onto it becomes useful for
something other than durability.

**A synchronous HTTP call to the audit service.** A dual write plus an
availability coupling, with nothing to show for either.

**One audit table per service schema.** No new grant, but four logs rather than
one, and [4.3] #15's actor filter — the query the story is mostly about — turns
into a cross-service fan-out with a client-side merge.

**Postgres logical replication or a trigger-based capture.** Records what
changed in the tables, not who asked for it or why. `reason` is in the
acceptance criteria and exists nowhere in the row being written; an admin's
justification for closing a market early is not derivable from the diff.

**Letting `audit_svc` own its schema, as every other service does.** Consistent
with the rest of `sql/02-schemas.sql`, and it quietly undoes the immutability:
the owner may ALTER and DROP whatever the grants say.
