# Backend code conventions

How backend code is *written*. What it must *do* — the invariants, locks,
schemas and service boundaries — is in the root `CLAUDE.md` and `docs/adr/`.
Where this file and either of those disagree, they win.

These apply to code you write or substantially rewrite. Do not reorder or
rename untouched code in a feature PR; that belongs in its own refactor PR.

## Naming

A name says what the function achieves, so a reader never has to open it to
know what it does. If you need "and" to describe it, it is two functions.

The verbs below are already in use across the services. Use the same one for
the same job, so `ensure_` means the same thing in every file:

| Prefix | Meaning | Existing example |
|---|---|---|
| `get` | fetch by id, raise `...NotFound` if missing | `market_service.get` |
| `find_` | fetch, return `None` if missing | `_find_by_draft_key` |
| `list_` | fetch many | `list_for_creator` |
| `lock_` | `SELECT ... FOR UPDATE` and return the row | `trading._lock_book` |
| `ensure_` | idempotent: create or check if needed, no-op otherwise | `grants.ensure_granted` |
| `refuse_if_` | raise a domain error, return nothing | `_refuse_if_frozen` |
| `is_` / `has_` / `can_` | return a bool, no side effects | `is_open_for_trading` |
| `record_` | append an audit entry | `_record_publication` |
| `<noun>_of` / `to_<noun>` | pure conversion | `result_of` |

Never: `handle_`, `process_`, `do_`, `manage_`, `helper`, `util`, `data`,
`info`, `tmp`, `res`, `obj`, single letters (except `q` and `b` in LMSR code,
where they are the maths), or numbered names like `e2`.

Tests are named as a sentence stating the behaviour:
`test_a_closed_market_refuses_a_trade`, not `test_trade_3`.

## File layout, top to bottom

1. Module docstring: what this file is for and its ticket tags. A few lines.
2. Imports.
3. Constants (`UPPER_SNAKE`; module-private ones `_UPPER_SNAKE`).
4. Types: dataclasses, enums, `TypedDict`s.
5. Private helpers (`_name`), each above the first function that calls it.
6. Public functions — the file's API — last, in the order a request uses them.

Reading top to bottom, you never meet a name that has not been defined yet.
Do not scatter helpers between public functions or park them at the end.

## Functions

- One job each. Aim for under ~40 lines. A long function is fine only when it
  is a straight sequence of named steps (see `trading.execute`), each step a
  call to a helper whose name says what it does.
- Type hints on every parameter and return. `from __future__ import annotations`
  at the top of every module.
- Arguments after the first two are keyword-only (`*,`) when two of them share
  a type — two `uuid.UUID`s passed positionally is a swap waiting to happen.
- Fail by raising a domain error from `core/errors.py`. Never return `None` or
  an error string to mean "it failed".

## Reuse

Before writing a function, search this service and `backend/shared/` for one
that already does it. The ones people most often rewrite by accident:

- errors: `core/errors.py` in each service
- paging cursors: `shared/paging.py`
- "is this market still trading": `market_service/service/closing.py`
- money rounding and pricing: `ledger_service/core/pricing.py`, `core/lmsr.py`
- moving credits: `ledger_service/service/posting.py::post`
- audit entries: `service/audit.py` in each writing service

When your PR would add the **second** copy of some logic, extracting it is part
of your PR — do not ship the copy with a TODO. Within a service, it goes in
`core/` (pure) or `service/` (touches the DB). Across services it stays
copied unless it meets the `shared/` bar in the root `CLAUDE.md` (ADR 0012);
leave a one-line comment naming the twin so the next person finds both.

Do not build for callers that do not exist yet: no base classes, generic
factories, plugin registries or config flags with one user.

## Comments and docstrings

- Code says **what**. A comment says **why**, and only when the why is not
  obvious. A comment that restates the line below it gets deleted.
- Every public function has a docstring: one line saying what it does, then
  only what a caller cannot see from the signature — what it raises, what it
  locks, whether it commits.
- Keep a comment to about five lines. Longer reasoning belongs in
  `DECISIONS.md` or an ADR, and the comment cites it: `# D-041` or
  `# ADR 0015: the wider lock goes first`.
- Do not narrate history ("used to", "changed in #77") — that is `git log`.
  The one exception is a warning against a tempting wrong change:
  `# Do not gate on status == OPEN; the clock decides. ADR 0011.`

## Errors

One error class per distinct thing the client can do about it, each with a
stable `snake_case` code. `service/` raises them; only `controller/` turns
them into HTTP. No `HTTPException` below `controller/`.

Do not catch bare `Exception`. The one sanctioned place is the price publish
after a trade commits (root `CLAUDE.md`, ADR 0010).

## Tests

What counts as a test worth keeping is in the root `CLAUDE.md` under
**Tests earn their place**. Backend specifics:

- The floor for every PR: a unit test for each new helper or validator, one
  integration test of the main flow, and at least one failure case asserting
  the exact error code.
- Anything that moves money also gets a rollback test (force a mid-operation
  failure, assert no rows written) and a concurrency test (see ADR 0015 for
  what makes a race test real).
- Pure logic tests go in `unit_test/core/` or `unit_test/model/`, which need
  no database. Put them there whenever you can; they are fast.
- Several tests differing only in input data become one
  `@pytest.mark.parametrize`. Shared setup goes in a fixture (`conftest.py`,
  `trade_fixtures.py`), not copied into each test.

## Before calling it done

1. `.venv/bin/pytest` passes for every service you touched.
2. Re-read your own diff for a second copy of something that already exists.
3. Run `/pr-review` on your branch and fix what it finds before asking a person.
