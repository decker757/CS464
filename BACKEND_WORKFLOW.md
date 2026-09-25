# BACKEND_WORKFLOW.md — Ihsan's working rules

Not a CLAUDE.md. The repo already has one and it stays authoritative.
Reference this at the start of a session with `@BACKEND_WORKFLOW.md`.
Where this file and the repo's CLAUDE.md or an ADR disagree, **they win** — tell me
about the conflict rather than picking one.

---

## Project

**gembet** — play-money prediction market. CS464 capstone, 3 people.

LMSR automated market maker, no order book. Users buy YES/NO shares. Price comes
from `C(q) = b·ln(Σ e^(qᵢ/b))`; price is its gradient. Winning shares pay 1 credit
at resolution, losers pay 0. `b` is liquidity; house worst-case loss is `b·ln(n)`.

**Me:** Ihsan (`ihsankoolz`) — backend. Pricing engine, trade path, settlement,
market read APIs, leaderboard, limit orders.
**Ernest (`decker757`)** — infra, CI, auth, realtime, market lifecycle, and
the ledger's money primitives (`posting.py`, `accounts.py`, `grants.py`).
Reviews every PR, strictly. The rest of `ledger_service` is mine — see
"`ledger_service` is mine" below, which is the longer version of this line
and the one to believe if the two ever drift.
**Michelle (`michelletan2024`)** — React frontend.

## Stack — as it actually is

- Python 3.13, FastAPI, SQLAlchemy 2.0 async, asyncpg, Postgres
- Five services: `auth_service`, `market_service`, `audit_service`,
  `ledger_service`, `realtime_service`. Auth, market and ledger each own a schema
  and connect as their own login role. `audit_service` has a login role and owns
  no schema: `audit` belongs to the superuser, and `audit_svc` only reads it
  (ADR 0006). `realtime_service` has no role, no schema and no `DATABASE_URL`
  (ADR 0010).
- `backend/shared/` — config, paging, roles, security, testing. Narrow by ADR 0012.
- pytest, `asyncio_mode = auto`, tests run against real Postgres, never SQLite
- Migrations: hand-written idempotent SQL in `sql/migrations/`. No Alembic yet (#75)
- Frontend is separate: npm, Vite, React 19, TS. Not mine.

**Read the ADRs before any structural decision.** They exist and they're binding.
ADR 0005 (where `q` and pricing live) and ADR 0012 (what may enter `shared/`) are
the two that govern my work.

### Service layout

```
backend/<name>_service/
  controller/   # routes
  service/      # orchestration
  core/         # errors, and the seams onto shared/ (ADR 0012)
  model/        # entities
  main.py       # only file allowed to see everything
unit_test/
  core/  model/        # run without a database (D-031, CLAUDE.md)
  service/  controller/ # need Postgres; realtime_service's need Redis instead (CLAUDE.md "Running things")
  test_import_boundary.py
```

Import direction is `controller → service → core/model`. Never upward. No test
enforces that direction. It's a review convention. `test_import_boundary.py`
enforces only that no service imports another. See "The terms client lives in
`service/`, not `core/`".

---

## Invariants — never break these

1. **Postgres is the only source of truth.** Price is computed from `q` and `b`,
   never stored as authoritative.
2. **Nothing that moves money reads Redis.** Redis is display and fan-out only.
3. **Balances are derived by summing ledger entries.** No cached balance column.
4. **Every transaction's legs sum to zero.** Globally the ledger sums to zero.
5. **Money is `Decimal`, `Numeric(18,4)`, and rounds directionally.** No floats in
   the money path, ever. The ledger's amounts and the public market projection
   cross the API as decimal strings, not JSON numbers (ADR 0009). `MarketOut`
   deliberately sends JSON numbers: see "Two schemas: `PublicMarketOut` beside an
   unchanged `MarketOut`".
   - Rounding a cost: "`quantize_cost` takes an unsigned magnitude; the caller
     applies the sign" in DECISIONS.md.
   - `ROUND_HALF_UP` at scale 4 is for displayed prices only: "The price read exists
     once, and the price quantizer is in `core/pricing.py`".
   - Absolute values: "An absolute value on money is `copy_abs()`, never `abs()`".
6. **The ledger is append-only, enforced by a database trigger.** Don't try to work
   around it.
7. **A charged cost is computed under the book lock. A quoted cost is not.**
   - The trade path's order (replay lookup, then status gate, then book lock) is
     ADR 0017's.
   - The preview locks nothing on a warm book: "Preview takes no locks", as scoped
     by "D-012's "no locks" is the warm path; the first touch locks twice".
   - If a design charges a cost that was priced outside the trade's lock, it's
     wrong. Stop and tell me.
8. **Websocket publish fires after commit, never inside the transaction.**

## Open questions — stop and ask, don't guess

- `posting.post()` calls `session.commit()` internally. Settled for a caller with
  nothing to write afterwards — D-032: order every write through
  `session.begin_nested()` and call `post()` last. Still open for [T-2] #22,
  whose `state_version` bump and `q` update may not fit that shape.
- Service-to-service auth for ledger writes doesn't exist. A trader's bearer token
  cannot authorize a ledger mutation — that's a self-mint hole.
- Whether an under-subsidised market should be refused. The pool *is* funded —
  `books.ensure_open` posts `seed_subsidy` from the PLATFORM account on a
  market's first touch ([F-7] #96, D-009) — but nothing compares that subsidy
  against `b·ln(n)`, so a market can be opened whose pool goes negative under
  ordinary trading. market_service's rule to make, not the ledger's.

**`auth_service` and `realtime_service` are Ernest's — don't modify them without
asking me first.** They have tests riding on current behaviour.

**`ledger_service` is mine.** The pricing engine, the book handoff, the preview
and the trade path all live there, and every trading ticket writes to it. Ernest
built `posting.py`, `accounts.py` and `grants.py` and still reviews changes to
them — treat those three as his and say so in the PR if you touch one — but the
service as a whole is no longer off limits.

---

## DECISIONS.md — read it, and append to it

`DECISIONS.md` is the running log of design decisions in my slice. Read it at the
start of every session, before the ticket. It's the layer under `docs/adr/`:
decisions that are real but sit below the ADR bar, plus working agreements not yet
written up.

`docs/adr/` is the authority. If `DECISIONS.md` contradicts an ADR, the ADR wins
and the entry is wrong — say so rather than following it.

**Append a new entry whenever a session settles any of these:**

- Where code lives, and why it isn't somewhere else
- A numeric or type contract at a boundary (precision, scale, string vs number)
- A lock, a transaction boundary, or an ordering rule
- A schema shape, or a column deliberately not added
- Something deliberately duplicated, or deliberately not abstracted
- A safety net deliberately left out
- Anything where the obvious-looking refactor would be a bug

**Do not append** for routine implementation: a function's internals, a variable
name, a test that just covers a criterion.

Use the format at the top of the file. Append only — never renumber, never delete.
A decision that changes gets a new entry, and the old one is marked superseded by
the new entry's title. The author never writes a number: a new entry stays
`D-NEW` until its PR merges (see "What makes a test evidence").

If a decision is big enough to constrain someone else's work or would be expensive
to reverse, it needs an ADR, and **writing it is part of the ticket** — a new record
in `docs/adr/`, or an amendment block on the one whose claim is changing. Two
conditions on either: it names a **reversal trigger** — the condition under which
the decision goes back, stated as something testable rather than as a feeling —
and it cites DECISIONS.md entries **by title, never by number**, because those
numbers have shifted on merges and an ADR is the permanent record.

Amend rather than supersede when the subject is unchanged and only a claim has
moved; write a new record when the decision spans more than one existing record's
subject. Tell Ernest after it lands, in the PR. Don't wait to be asked, and don't
leave the reasoning only in the PR description.

When a session ends with an unresolved question, add it to the **Open** section at
the bottom rather than guessing.

Commit log changes with the work they describe, not separately:
`docs(<service>): record <title>`. There's no number, because the entry is
`D-NEW` until merge.

---

## Before starting a ticket — read the board

The board is `github.com/users/decker757/projects/6`. Read the real issue. Don't
work from my paraphrase of it — I get details wrong.

```bash
gh issue view 43                                          # body + acceptance criteria
gh project item-list 6 --owner decker757 --format json    # status, priority, iteration, assignee
gh issue list --repo decker757/CS464 --state open --assignee ihsankoolz
gh pr list --repo decker757/CS464 --state all --search "43"
```

If `gh` lacks the scope: `gh auth refresh -s project,read:project`

Check before writing any code:

- **The acceptance criteria in the issue body are the spec.** They override anything
  I said in chat.
- **Sub-issues.** A parent showing `0/1` has a child that may be the actual unit of
  work. `gh issue view` shows them.
- **Linked PRs.** Has someone already started this.
- **Is it actually assigned to me.** If it's Ernest's or Michelle's, stop.
- **Dependencies named in the body** — "Required by", "Blocked by", "Land before".
  Follow them and read those issues too.
- **Board fields** — priority and iteration tell me whether this is even the right
  ticket to be on.

If the issue body contradicts this file or an ADR, say so and stop. Don't reconcile
it yourself.

**Read the board, don't write to it.** Status moves automatically — linking a PR
sets In progress, merging sets Done. Don't run `gh project item-edit` or
`gh issue edit` unless I ask.

The test-writing session gets its acceptance criteria from `gh issue view`, never
from the implementation.

---

## How to work

**One ticket at a time.** Don't start a second before the first is pushed. If a task
needs work from another ticket, say so and stop.

**Read before writing.** Read the real code, not what you assume the interface is.

**Small diffs.** Only what the ticket needs. No drive-by refactors, no reformatting
untouched files, no renames you don't have to make.

**Ask before adding a dependency.**

**Type hints everywhere.** No bare `Any` in the money path.

**Never put real-looking secrets in test files.** GitGuardian runs on every PR and
has already failed once on a fake password. Use `"<PASSWORD>"` placeholders.

---

## Tests — written by a different agent than the code

Non-negotiable. An agent that writes both marks its own homework: it tests what it
built rather than what the ticket asked for, and tests bend to fit bugs.

### The sequence

**Step 1 — interface.** I define the public signature and acceptance criteria.
Nothing else is agreed yet.

**Step 2 — test session.** Fresh session, `/clear` first, or a `Task` subagent.
Its brief:
- Read the ticket's acceptance criteria and the public signature only
- Do **not** read any implementation file for this ticket
- Do **not** create or modify implementation files
- Write tests that fail for the right reason (import error or assertion, not syntax)
- One test per acceptance criterion, named after it

**Step 3 — implementation session.** Fresh session, `/clear` first. Its brief:
- Read the ticket and the failing tests
- Make them pass
- **Do not edit any test file.** If a test looks wrong, stop and tell me — that's a
  spec disagreement and I decide, not the implementer
- Don't add new behaviour the tests don't cover

**Step 4 — review.** Third session or me. Are the tests testing the criteria, or
just the code that got written?

### What tests must cover

Ernest's stated standard:

- **Unit** — helpers, validators, and anything added in this PR
- **Integration** — at least one main-flow test, and at least one failure case
  asserting the correct error code

For pricing (`#43`), additionally property tests:
- Prices across outcomes sum to 1
- Cost is monotonically increasing in each `qᵢ`
- Buy-then-immediately-sell never yields a profit
- Stable at `q/b` up to 10,000 (`e^(q/b)` overflows past ~700 — use log-sum-exp)

For anything touching money, additionally:
- Rollback test: force a mid-operation failure and assert that zero ledger rows
  were written. Where to count is covered by the "asserts after
  `session.rollback()`" bullet below.
- Concurrency test: assert no overdraft and that the ledger still sums to zero.
  What makes it a race is ADR 0015's, and the race-test bullet below.

`unit_test/core/` and `unit_test/model/` need no database and run fast. Put pure
logic there.

### What makes a test evidence

- **A test that asserts after `session.rollback()` proves nothing.** The rollback
  erases the writes the test is looking for. Under READ COMMITTED, a second
  session can't see uncommitted writes either. Count in the request's own
  session, before any rollback.
- **A validator test must feed the invalid value.**
- **A race test is evidence only if it fails with its lock or re-check
  removed** (ADR 0015). Name the line you removed.
- **A connection-release test probes the session and the pool while the upstream
  call is stalled.** It must fail with the release removed. A test that only
  shows the call succeeds proves nothing. For an example, see "The cold path
  holds no connection across the terms pull".
- **A DECISIONS.md entry is `D-NEW` until its PR merges.** Never take the next
  number from the file.

---

## Git — one branch per ticket, no exceptions

Base is `dev`, never `main`. Ernest rebases `dev` often, sometimes several times a
day.

**Start**

```bash
git checkout dev
git pull origin dev
git checkout -b 43-lmsr-pricing-engine
```

Naming: `<issue>-<slug>`. No `feat/` or `fix/` prefix — CLAUDE.md sets the
convention and every branch on the remote follows it (`21-cost-preview`,
`96-market-book-handoff`, `62-market-read-api`).
One branch per ticket. Never two tickets on one branch, never reuse a merged branch.

**After a squash merge, start a fresh branch from `dev`.** A squash rewrites the
work into one new commit with no shared ancestry, so `git merge-base --is-ancestor`
reports the old branch as unmerged and pushing more work to it collides on every
file. #96 landed this way.

**When Ernest says rebase**

```bash
git checkout dev
git pull origin dev
git checkout 43-lmsr-pricing-engine
git rebase dev
# fix conflicts, then
git add .
git rebase --continue
git push --force-with-lease
```

`--force-with-lease`, never plain `--force`.

**Commits — split by layer**

Ernest asked for this explicitly, so he can follow the reasoning.

```bash
git add backend/ledger_service/core/lmsr.py
git commit -m "feat(ledger): LMSR cost and price functions"

git add backend/ledger_service/unit_test/core/test_lmsr.py
git commit -m "test(ledger): property tests for LMSR"
```

The engine lives in `ledger_service/core/lmsr.py`, not in `shared/`. See ADR 0005
and ADR 0012's amendment.

Format `type(scope): description`. Types: `feat`, `fix`, `test`, `refactor`,
`chore`, `docs`.

**PR**

```bash
.venv/Scripts/pytest    # Windows layout; CI runs a bare `pytest`
git push -u origin 43-lmsr-pricing-engine
gh pr create --base dev --title "[F-3] LMSR pricing engine" --body "<Refs|Closes> #43"
```

Pick the trailer by CLAUDE.md's Branches rule: `Refs #N` while the ticket has
open sub-issues, `Closes #N` otherwise.

**Before requesting review**
- All tests pass locally
- CI green
- No secrets or real-looking credentials in tests
- Diff contains only what the ticket needs
- Commits split by layer

---

## Environment

Everything local. No hosted services, no external APIs. Copy `.env.example` to
`.env`. Needs `POSTGRES_*`, per-service `*_DB_PASSWORD` and `*_TEST_DATABASE_URL`,
`JWT_SECRET`, `DEFAULT_LIQUIDITY_B` (100), `STARTING_CREDITS` (1000), `REDIS_URL`,
`CLOSE_SWEEP_*`, ports.

Migrations are applied by hand:
`docker compose exec -T db psql ...` against `sql/migrations/`.

**Switching from a #22-or-later branch to an earlier one:** #22's `create_all`
leaves `ledger.positions` in `cs464_test`, and that breaks the earlier branch's
`drop_all`. Drop the table:

```bash
docker compose exec -T db psql -U cs464 -d cs464_test -c "DROP TABLE ledger.positions CASCADE;"
```