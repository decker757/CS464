# ADR 0003: A separate market service, and admin authority carried in the token

- **Status:** Accepted
- **Date:** 2026-09-13
- **Affects:** [1.1] #1, [1.2] #2, [1.3] #3, [1.4] #4, [4.4] #16, [BE][X] #62
- **Implemented in:** `backend/market_service/`, `sql/01-roles.sql`, `sql/02-schemas.sql`

## Context

[1.1] #1 needs an endpoint that only an administrator may call, and a draft
that only its own creator may read. Two questions had to be answered before a
line of it could be written, and neither had an obvious default.

**Where does the code live?** The auth service already exists and already has
a working route guard. Putting markets in it would have been the shortest path.

**Where does "this user is an admin" come from?** `auth.users` had no role
column at all. [4.4] #16, which defines MARKET_CREATOR, RESOLVER and
SUPER_ADMIN, is unstarted and is marked foundational. Meanwhile `market_svc`
cannot read `auth.users`: `sql/02-schemas.sql` grants nothing across schemas,
on purpose.

## Decision

**A separate service.** `backend/market_service/` owns the `market` schema and
connects as `market_svc`, with the same four-layer structure as the auth
service and no shared code between them.

**Authority travels in the access token.** `auth.users` gains a `role` column,
`core/security.py` copies it into a `role` claim, and the market service reads
that claim and nothing else. It performs no database lookup to authorise, and
it holds no copy of the user table.

This is a deliberately thin slice of [4.4] #16. Only two values exist, `trader`
and `admin`. The creator/resolver separation that ticket is actually about is
untouched, and adding those members later is a widened CHECK constraint plus
two lines in each service's `roles.py`.

## Why not put markets in the auth service

The README already says the auth service does not know that credits exist, and
there is a test that fails if the word appears in a response. Markets are the
same kind of knowledge. The moment `auth` owns a markets table, "auth" stops
meaning anything and becomes the service where things go.

The cost of the split is real and is paid in `sql/`: one more role, one more
schema, and a `docker compose down -v` for everybody. It was paid once, now,
rather than during the trading epic when the database holds something.

## Why the token and not a lookup

The alternatives were an allowlist table in the market schema, or an
environment variable of admin user ids.

Both create a second place where identity lives. An id in
`market.market_admins` names a row this service cannot see, cannot validate,
and cannot keep in step: delete the user in `auth` and the allowlist still says
they are an administrator. The environment-variable version is worse again,
because changing who is an admin becomes a redeploy.

The token already crosses this boundary on every request, ADR 0002 already
established it as the cross-service contract, and its signature is already
checked. Adding one claim to something already trusted beats inventing a second
channel.

## Consequences

**A role change takes effect on the next token, not immediately.** Promote
someone and they keep a trader's token until it expires or they log in again —
at most 15 minutes. Demote someone and they keep admin authority for the same
window. This is the same property ADR 0002 already accepted for logout, bounded
by the same TTL, and the same fix applies if it ever stops being acceptable: a
denylist keyed on `jti`.

**Suspension does not reach this service.** `[4.2] #14` sets `is_suspended` on
`auth.users`. The market service cannot see it and will honour a suspended
admin's unexpired token. The auth service refuses to issue or rotate tokens for
a suspended account, so the window is again one access-token lifetime, but it
is a window, and #14 should note it.

**`creator_id` is not a foreign key.** It names a row in `auth.users` that
`market_svc` has no grant to read. Nothing stops a market outliving its
creator's account, and no cascade will clean it up. That is the price of the
schema boundary rather than an oversight; `unit_test/service/test_schema_boundary.py`
asserts the denial holds, so the day somebody adds a convenience grant, a test
goes red instead of the services quietly welding together.

**Under HS256 this service could mint tokens.** It holds the same
`JWT_SECRET`. `core/security.py` has no encode path and a test asserts that it
does not grow one, but that is an architectural restriction, not a
cryptographic one. ADR 0002 already records RS256 as the upgrade; this is a
second service's worth of reason to take it.

**Roughly 200 lines of plumbing are now duplicated, deliberately.**
`core/database.py`, the CORS-parsing block in `core/config.py`,
`extract_access_token`, the JWT decode and `_read_role`, the error handler, the
`UserRole` enum and `_load_repo_env` all exist twice.

This is the second occurrence, which is normally the point at which to extract.
It was not extracted, because a `backend/shared/` package is not free here: it
changes both Dockerfile build contexts, adds a packaging and versioning story
to a three-person project, and — the part that actually matters — makes a
change to one service able to break another at import time, which is the exact
coupling `sql/02-schemas.sql` spends its whole existence preventing.

The duplication is also not symmetric, which is the tell that a shared module
would have been the wrong shape: the auth service's `security.py` signs and
hashes, this one only verifies; its `transport.py` writes cookies, this one
only reads. A shared module would carry both halves into a service that must
not have one of them.

Revisit when the ledger ([F-1] #41) lands and makes it three. At three
occurrences the argument flips, and the thing to extract is narrow — token
verification and the settings base — not "core".

**Two services now issue `create_all` against one database.** They touch
disjoint schemas, so they do not race today. This is the last change that gets
away with it. `[F-1] #41` should bring Alembic.

**Promoting an administrator is a manual `UPDATE`.** There is no bootstrap
environment variable and no self-service endpoint, because either would be a
path to granting authority that is not reviewed. Until [4.4] #16:

```sql
UPDATE auth.users SET role = 'admin' WHERE lower(username) = 'ernest_t';
```

The user must log in again afterwards to be issued a token carrying the new
role.

## Alternatives rejected

**Markets inside the auth service.** Fastest, and it dissolves the one boundary
in this project that is actually enforced by the database rather than by
agreement.

**An allowlist table in the market schema.** No change to auth, but identity
now lives in two places with no way to reconcile them.

**Waiting for [4.4] #16.** It is foundational and unstarted, and [1.1] #1 is in
progress with its frontend half (#45) already assigned. Blocking a sprint story
on an unscheduled one is worse than shipping the two values that story needs,
in a shape #16 extends rather than replaces.

**An API gateway terminating authentication.** The right answer eventually, and
`controller/dependencies.py` is written so that it is the only file that
changes: the gateway would inject a trusted signed header and `get_claims`
would read that instead. Nothing in `service/` knows where the caller's id came
from. Not worth standing up a gateway for one service.
