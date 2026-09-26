# ADR 0012: A shared package, and the build contexts that had to move first

- **Status:** Accepted
- **Date:** 2026-09-16
- **Affects:** [F-6] #76, [F-3] #43, [T-2] #22, and every service's `core/`
- **Implemented in:** `backend/shared/`, the five `Dockerfile`s, `docker-compose.yml`, `.github/workflows/ci-backend.yml`

## Context

ADR 0003 declined to extract `backend/shared/` at two occurrences of duplicated
`core` code. ADR 0005 said the deferral had expired at four, named what should
move — "token verification, the settings base, and the LMSR engine. Not
`core`" — and named the blocker:

> **Docker build contexts are the binding constraint on that.** Each service
> builds from its own directory with `COPY . .`, and a build context cannot
> reach outside itself.

So every file that wanted sharing was copied instead, and each copy carried a
comment explaining that the copy was not a choice. By the time [F-2] #42 landed
there were five copies of the role enum, four of the token verifier, four of
the settings preamble and five of the test-suite environment loader.

The verifier is the one that mattered. Four byte-identical implementations of
"who is this request from", each free to drift, in a system where drifting
means a service disagrees with the rest about somebody's authority. Nothing
made them agree. `_read_role` fails closed to TRADER, so the failure mode was
not an error — it was a privileged caller silently treated as an ordinary one.

## Decision

**The build contexts move to `backend/`.** Each service sets
`context: ./backend` and `dockerfile: <service>/Dockerfile`, and its image
mirrors the repository layout: `WORKDIR /app/<service>` with `shared/` beside it
at `/app/shared`, and `PYTHONPATH=/app`.

That last part is the point. An import that works in a checkout works in the
container, because the two layouts are the same shape rather than two
arrangements that happen to agree. `pytest.ini` says the same thing a third way
with `pythonpath = . ..`.

**`backend/shared/` holds exactly what ADR 0005 authorised, and no more:**

| Module | Was | Why it qualifies |
| --- | --- | --- |
| `shared/security.py` | 4 copies, byte-identical | Divergence silently misreads authority |
| `shared/config.py` | 5 copies of one preamble | A wrong issuer fails as "not logged in" |
| `shared/roles.py` | 5 copies | A wire contract, not a design choice |
| `shared/paging.py` | 2 copies | One cursor format, two readers |
| `shared/testing.py` | 5 copies, byte-identical | Test plumbing, no runtime surface |

The LMSR engine is the third thing ADR 0005 named. It does not exist yet and
lands with [F-3] #43, into this package.

> **Amended by [F-3] #43.** It landed in `ledger_service/core/lmsr.py`
> instead, and this paragraph is the claim that reverses. What changed is not
> the bar but the caller count: ADR 0010 settled *after* this record that the
> ledger owns the write path, because the trading composite owns no role and no
> schema. A service holding no cross-schema grant cannot read `q`, so it cannot
> evaluate the cost function — it calls the ledger, which can. That leaves one
> caller, and the bar in the table above is that *every* caller needs identical
> behaviour and a divergence between two copies would be a bug. One caller does
> not clear it, and a shared module with one caller is indirection with nothing
> on the other end.
>
> This is a deferral, not a refusal. Move the engine here on the day a second
> caller can read `q` — the test for that is a cross-schema grant, not a new
> service. No issue currently planned adds one. The engine's own module
> docstring carries the same argument, so the next reader finds it from either
> direction.

**Each service keeps a `core/` module as the seam.** `core/security.py` binds
this service's settings to the shared verifier; `core/config.py` subclasses
`ServiceSettings`; `core/roles.py` re-exports; `core/paging.py` turns a None
from the shared decoder into this service's own `MalformedCursor`. Nothing
under `service/` or `model/` imports `shared` directly, so CLAUDE.md's layering
rule — `service` and `model` reach into `core` and no further — still holds,
and a call site that has always said `from core.security import ...` did not
change.

**Nothing in `shared/` imports a service.** The verifier takes its secret,
algorithm and issuer as arguments rather than reading `core.config`, and the
cursor decoder returns None rather than raising a domain error. Both are the
same rule: the dependency points one way, or the package is not shareable.

## What was deliberately left copied

This is the half of the decision that keeps `shared/` from becoming `core`.

**`core/database.py`** — 4 copies at 81% similarity, and the most tempting.
Each service's `Base.metadata` is its own, and that is load-bearing:
`unit_test/conftest.py` calls `drop_all` on it and `create_all` runs against it
at boot. One shared `Base` would enrol every service's tables in every other
service's metadata, and the first conftest rebuild would try to drop tables its
role holds no grant on. The duplication is the boundary working.

**`controller/transport.py`** — 5 copies at 37%. They agree on very little.
The realtime one is checking a WebSocket origin by hand, which no other service
does or should.

**`core/errors.py` and `controller/errors.py`** — each service raises its own
hierarchy, which is what stops a market error being caught by an `except` in
the ledger. Sharing the handler means sharing the base class, and that is
"sharing `core`" by another name.

**The audit writer** — `model/audit.py` and `service/audit.py`, 2 copies each.
The build context is no longer the reason, and this one is a genuine candidate:
the `Table` is identical and only `AdminAction` differs, by design. It belongs
to ADR 0006 to move, not to a packaging change.

**`bus.py::publish`** — four lines of `redis.publish`. Below the bar.

> **Extended by [F-9] #112.** `PriceEvent` is copied too, and it is the more
> interesting copy of the two: `publish` is four lines whose divergence is
> loud, and the model is a contract whose divergence is silent — the producer
> serialises a field the consumer forbids, the consumer drops the whole event,
> and the symptom three services away is prices that stop updating with nothing
> logged on the side that caused it. Neither service may import the other, so
> the two are held together by a test that reads
> `realtime_service/model/schemas.py` and `service/bus.py` as source and parses
> them with `ast`. A file read is not an import: `test_import_boundary.py`
> still passes, and the container, which holds no copy of the other service,
> never runs it.
>
> **Reversal trigger.** This stays a copy while there are two callers and the
> pin can see both files. Move `PriceEvent` into `shared/` if a third service
> needs it, or if the source-reading pin ever stops running — the test for the
> latter is `ci-backend.yml` gaining per-service path filtering, which would
> retire the pin silently and leave the copies unguarded, and that is the point
> at which indirection is cheaper than a contract nothing checks.

The bar, stated once: a module earns a place in `shared/` when every caller
needs the identical behaviour *and* a divergence between two copies would be a
bug rather than a design choice. Similar code is not enough. ADR 0003 refused
to share `core` at two occurrences precisely because the duplication was
asymmetric — auth signs where the others only verify — and a shared module
would have carried a minting path into services that must never have one. That
asymmetry is still real, which is why `auth_service/core/security.py` keeps
`create_access_token` and imports only the verifier.

## Consequences

**A change to `shared/` rebuilds every service.** That is the cost of the thing
being shared and it is the right trade for these five modules, all of which are
things the services must not disagree about. It is the wrong trade for anything
whose divergence is a legitimate design choice, which is the list above.

**CI needed one change and not the one expected.** The path filter already read
`backend/**`, so `backend/shared/**` was covered. What broke was the
`Verify the app boots` step: it runs a bare `python -c`, which gets only the
working directory, not `pytest.ini`'s path. It sets `PYTHONPATH: ..`, for the
same reason the container sets `PYTHONPATH=/app`.

**`.dockerignore` moved to `backend/`.** Docker reads it from the context root,
so the two per-service files stopped being honoured the moment the context
changed. Missing this would not fail a build — it would quietly ship five
`.venv` directories into the images.

**Build cache is slightly better, not worse.** `shared/` is copied before the
service source, so an ordinary edit to one service reuses both the wheel layer
and the shared layer.

**The realtime service still has no database.** `ServiceSettings` deliberately
does not declare `database_url`, even though four of the five subclasses do.
A base class that supplied it would make ADR 0010's "must not grow one" a
matter of restraint rather than of structure.
