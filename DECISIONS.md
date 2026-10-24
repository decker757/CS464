# DECISIONS.md

A running log of design decisions made while building the backend, in the order
they were made.

## What this is, and what it isn't

`docs/adr/` holds the repo's architecture decision records. Those are formal,
numbered, argued at length, and they're the authority. **This file does not
replace them and never overrides them.**

This is the layer underneath: decisions that are real and worth remembering but
sit below the ADR bar, plus decisions that are still working agreements and
haven't been written up yet.

A decision graduates to `docs/adr/` when it constrains more than one person's
work or would be expensive to reverse. When that happens, the entry here stays
and gains a pointer to the ADR.

**If an entry here contradicts an ADR, the ADR wins and the entry is wrong.**
Fix it rather than leaving both.

## Format for new entries

Append to the end. Never renumber, never delete — supersede.

```markdown
### D-NEW — <short title>

**Date:** YYYY-MM-DD · **Ticket:** #NN · **Status:** active

**Decision.** One or two sentences. What was chosen.

**Why.** The actual reason, including the constraint that forced it.

**Rejected.** What else was considered and the cost that ruled it out.

**Notes.** Anything a future reader would otherwise have to rediscover.
Omit if there's nothing.
```

A new entry is headed `D-NEW` and takes no number. It is cited by its title.
Both forms in use are fine: `D-NEW "<title>"`, and `DECISIONS.md, "<title>"`
where the sentence already needs the filename; inside this file the quoted
title alone is enough. What matters is the exact title, because that is what
the search finds. So a title must be unique: search for it before adding
one. The same search finds the entry for any citation:

```bash
grep -nF '### D-NEW — <title>' DECISIONS.md
```

Keep it literal (`-F`) and in single quotes. Titles hold backticks, which a
shell runs as commands inside double quotes, and `[3.4]` or `*i*`, which grep
reads as a pattern. Write an apostrophe in the title as `'\''`.

Before adding an entry, check that no title is used twice. This prints nothing
while every title is unique:

```bash
grep '^### D-NEW — ' DECISIONS.md | sed 's/^### D-NEW — //' | sort | uniq -d
```

D-001 to D-052 keep their numbers and are still cited by them (`D-041`).
Nobody numbers a D-NEW entry, at merge or later.

Why titles: two open branches that each append an entry would both take the
next number, and the later merge would renumber its entry and every citation
of it, the same way two branches that both add Alembic `0002` collide. Titles
do not collide.

Status is `active`, `superseded by "<title>"`, `closed by "<title>"` or
`graduated to ADR NNNN`. Nothing is superseded by a number: the entry doing
the superseding is always a D-NEW one.

---

## Decisions

### D-001 — The LMSR engine lives in `ledger_service/core/`, not `shared/`

**Date:** 2026-09-20 · **Ticket:** #43 · **Status:** active

**Decision.** `backend/ledger_service/core/lmsr.py`.

**Why.** ADR 0005: the engine lives wherever `q` lives, and `q` is ledger state.
ADR 0012's bar for `shared/` is that every caller needs identical behaviour and
a divergence between two copies would be a bug. There is one caller.
`trading_service` is stateless with no cross-schema grants, so it can't read `q`
and can't compute LMSR — it has to call the ledger.

**Rejected.** `backend/shared/lmsr.py`. Fails the ADR 0012 bar on caller count,
and CI has no `shared` matrix leg, so tests placed there would never run.

---

### D-002 — Decimal in, Decimal out, unquantized

**Date:** 2026-09-20 · **Ticket:** #43 · **Status:** active

**Decision.** The engine takes and returns `Decimal` and does not quantize.
Rounding happens at the database column, where money is written.

**Why.** The ledger is `Numeric(18,4)`. A float boundary would mean converting at
every call site. Quantizing inside the engine compounds rounding error across
intermediate steps.

**Rejected.** Quantizing to 4dp inside the engine. Float in/out.

---

### D-003 — Decimal precision 50, scoped with `localcontext()`

**Date:** 2026-09-20 · **Ticket:** #43 · **Status:** active

**Decision.** Every public function in `lmsr.py` runs its arithmetic inside
`localcontext()` at precision 50. Never the global context.

**Why.** `posting.py` quantizes money using the default context and rounding
mode. Changing the global context would silently alter it. 50 digits covers the
central-difference gradient check, where subtracting two costs of order hundreds
to recover a near-zero slope cancels several leading digits.

**Notes.** `cost_to_trade` originally missed the wrapper — its sub-calls ran at
50 while its own subtraction ran at the ambient 28. Fixed. If a function is
added, wrap it.

---

### D-004 — Numerical stability via log-sum-exp

**Date:** 2026-09-20 · **Ticket:** #43 · **Status:** active

**Decision.** Subtract `max(qᵢ/b)` before exponentiating.

**Why.** `e^(q/b)` overflows past roughly `q/b = 700`. The identity
`ln(Σe^xᵢ) = m + ln(Σe^(xᵢ−m))` holds for any `m`, so shifting is exact.

**Notes.** `Decimal` has `.exp()` and `.ln()` but no logsumexp. Implemented as
`_log_sum_exp`.

---

### D-005 — `cost` and `prices` are deliberately independent implementations

**Date:** 2026-09-20 · **Ticket:** #43 · **Status:** active

**Decision.** `cost` routes through `_log_sum_exp` and returns a log. `prices`
inlines its own softmax over the same shifted exponentials and returns ratios.
They share no code. **Do not merge them.**

**Why.** The central-difference gradient test proves that price is the derivative
of cost. That test only means something because the two paths are separate. Merge
them and it becomes tautological.

**Notes.** This reads as duplication to a reviewer. Docstring says so explicitly.
Flag it in any PR that touches the file.

---

### D-006 — No output clamp in `prices()`

**Date:** 2026-09-20 · **Ticket:** #43 · **Status:** active

**Decision.** `prices()` returns its computed ratios with no floor or ceiling.

**Why.** The arithmetic never produces an out-of-range value. A clamp is dead
code on the happy path, and on an unhappy path it silently converts an invariant
violation into a passing test. On a money path, fail loudly.

**Notes.** *Corrected 2026-09-20, after ac36268.* This previously said the losing
price underflows to exactly `0.0` at `q/b = 50`, so "strictly between 0 and 1"
was unsatisfiable there. That was float reasoning applied to a `Decimal` engine
and it was wrong. `Decimal`'s exponent range reaches about `1e-999999`, so
`q = [1000000, 0, 0]` at `b = 100` prices the losers near `1e-4343` — negligible,
and strictly positive. Nothing underflows. Ernest tightened both saturation
assertions from `>= 0` to `> 0` and he is right: a price of exactly `0` is a
share that pays out 1 credit for nothing, and it is worth asserting that the
engine never produces one.

The bound is therefore asymmetric, and the two halves fail for different reasons.
**Strict below**, because the exponent range is enormous. **Weak above**, because
the precision is only 50 digits: the winning price at that spread is `1 − 2e-4343`
and no context holding 50 significant digits can represent it as anything but
`1`. Raising `_PRECISION` does not help — it would need 4,343 of them — so `< 1`
is unsatisfiable at saturation and `<= 1` is the honest assertion. The tests are
still split, but on that line rather than on underflow: `0 < p < 1` at moderate
spreads, `0 < p <= 1` at saturated ones.

This makes the upper bound load-bearing in a way it was not when this entry was
written. With the clamp gone (aead51a), nothing pins these numbers except softmax
normalisation — every weight is divided by a total that includes it, and the
largest weight is exactly `exp(0) == 1`. A price above `1` means the
normalisation broke, and re-adding a clamp would hide that rather than fix it.

---

### D-007 — Tests are written by a different session than the implementation

**Date:** 2026-09-20 · **Ticket:** process · **Status:** active

**Decision.** Tests are written first, from the issue's acceptance criteria, by a
session that has not seen and will not write the implementation. The implementing
session may not edit a test file. A test that looks wrong is a spec disagreement
and goes to Ihsan, not to the implementer.

**Why.** An agent that writes both tests what it built rather than what the ticket
asked for, and bends tests to fit bugs. This has already earned its keep: the test
session caught the `q/b = 50` underflow (D-006) and the `cost_to_trade` precision
gap (D-003), both of which a self-testing implementer would have papered over.

**Notes.** Mechanically: Opus for tests, `/clear`, Sonnet for implementation.

---

### D-008 — Market terms reach the ledger by lazy pull on first touch

**Date:** 2026-09-20 · **Ticket:** #21, handoff · **Status:** active

**Decision.** The first request that needs a market's book finds none, reads the
terms from the public market read endpoint (#62), and creates `market_books` +
`market_outcomes` + the funding transaction. Keyed `market-open:<market_id>`.

**Why.** ADR 0005 says `b` and the subsidy cross at publish as an immutable
snapshot. That handoff was never built — publish writes an audit entry and
nothing else, and neither service has an HTTP client. Lazy pull is the same shape
as the signup grant (ADR 0009): no event, no outbox, and no window in which the
state is observably wrong, because the read that would observe the window is the
read that closes it.

**Rejected.** Push at publish — a dual write with no outbox; publish commits, the
HTTP call fails, and a live market has no book. That's what ADR 0006 exists to
avoid paying for. Cross-schema grant — rejected on principle, ADR 0003.

**Notes.** Costs accepted knowingly: the ledger gains an HTTP client and a runtime
dependency on `market_service`. No service currently calls another, and that was
deliberate. Ernest signed off. Needs its own issue.

---

### D-009 — Seed subsidy is posted at book creation

**Date:** 2026-09-20 · **Ticket:** handoff · **Status:** active

**Decision.** `PLATFORM → MARKET_POOL` for the seed subsidy, in the same
transaction that creates the book.

**Why.** LMSR pays out up to `b·ln(n)` more than it collects. `_refuse_overdrafts`
exempts every non-USER account, so an unfunded pool goes quietly negative instead
of failing. Funding at creation is the only moment where the amount is known and
nothing has traded yet.

---

### D-010 — The book insert race is handled by catching `IntegrityError`

**Date:** 2026-09-20 · **Ticket:** handoff · **Status:** active

**Decision.** Two concurrent first-touches both find no book and both insert. The
PK on `market_id` means one raises `IntegrityError`. Catch it, re-read, continue.

**Why.** The idempotency key covers the funding transaction, not the
`market_books` insert. Those are separate writes.

---

### D-011 — The quote reference is `state_version`, not a separate counter

**Date:** 2026-09-20 · **Ticket:** #21, #22 · **Status:** active

**Decision.** One `BigInteger` per market in `market_books`, incremented in the
trade's transaction under the book row's lock. The preview returns it, #22
re-reads it under lock and rejects on mismatch, and the realtime snapshot and
price events report the same number.

**Why.** The realtime contract already specifies `state_version` as the event
ordering field. Two counters would be two sources of truth for "has this market
moved".

---

### D-012 — Preview takes no locks

**Date:** 2026-09-20 · **Ticket:** #21 · **Status:** active

**Decision.** The cost preview is an unlocked read.

**Why.** ADR 0015's rule is about reads that decide a write, and it says
explicitly that reads feeding no write stay unlocked. A preview decides nothing.
The gap between preview and confirm is a human one — seconds to minutes — and no
lock survives it. The staleness check in #22 is ADR 0015's lock, moved to the only
place it can hold.

**Notes.** Preview fires on every keystroke. Locking it would serialise every
trader typing in the same market.

---

### D-013 — Preview reads `q` and `state_version` in one SQL statement

**Date:** 2026-09-20 · **Ticket:** #21 · **Status:** active

**Decision.** One statement joining `market_books` to `market_outcomes`.

**Why.** Under READ COMMITTED a single statement sees one snapshot. Two statements
fail in the dangerous direction: read `q` first and `state_version` second, and a
trade committing between them hands back a version newer than the `q` that was
priced — #22 then finds the version current and executes against a quote that was
already stale. The reverse order fails safe. One statement removes the choice.

---

### D-014 — The preview endpoint lives on the ledger as a read

**Date:** 2026-09-20 · **Ticket:** #21 · **Status:** active

**Decision.** `GET` on `ledger_service`, beside the snapshot endpoint the realtime
contract owes. Not deferred to `trading_service`.

**Why.** `trading_service` owns no data, so it would have to fetch `q`, `b` and
`state_version` from a ledger read endpoint anyway. Waiting is the same design
plus a hop and a container. Keeping preview and trade on one code path is what
stops a trader being quoted one number and charged another — ADR 0005's stated
fear.

**Notes.** Does not reopen ADR 0009's deferred service-auth question. That's about
a *write* route. A preview mints nothing and reveals nothing beyond the public
snapshot, so the trader's own token is the right credential. Boundary to state in
the PR: the ledger evaluates the cost function because it owns `q`; it does not
decide whether a trade may happen.

---

### D-015 — Lock order: book row before account rows

**Date:** 2026-09-20 · **Ticket:** #22 · **Status:** active

**Decision.** The trade path locks the `market_books` row first, then calls
`posting.post`, which takes account locks ascending by id internally.

**Why.** ADR 0015: the wider lock goes first on every path. The book row gates
every outcome and every position in that market. Get it backwards on one path and
two trades in different markets by two users deadlock.

---

### D-016 — `liquidity_b` serialises as a decimal string, never a float

**Date:** 2026-09-20 · **Ticket:** #62 · **Status:** active

**Decision.** The public market read sends `liquidity_b` as a decimal string.

**Why.** Under D-008 this response is the source of `b` for the pricing engine. A
float there puts an IEEE double at the root of every price in the system.

**Notes.** `MarketOut` currently sends it as a float, deliberately, for the admin
create form. If one schema can't serve both, two schemas.

---

### D-017 — `cost_basis` is stored; average entry price is derived

**Date:** 2026-09-20 · **Ticket:** #24 · **Status:** active

**Decision.** `ledger.positions` stores cumulative net `cost_basis`. Average entry
price is `cost_basis / quantity`, computed at read time.

**Why.** Same reason there's no `balance_after` column and no cached balance: a
stored average is a second source of truth, and an average is one division away.

---

### D-018 — Public market reads require a valid token, any role

**Date:** 2026-09-20 · **Ticket:** #62 · **Status:** active

**Decision.** `GET /public/markets` and `GET /public/markets/{id}` accept any
valid access token and do not check the role. Not admin-gated, not anonymous.

**Why.** Every read in this backend authenticates a person from a signed token,
and nothing in #62, [X-1] #34 or [X-3] #36 says otherwise — "as a user" is not a
statement about anonymity. Consistency is the tie-breaker when the ticket is
silent.

**Rejected.** Anonymous. It would be the first unauthenticated read in the
backend, which is a decision about the product's shape rather than a default a
backend slice gets to pick. Admin-only, which is what every existing `/markets`
route does and is wrong for a trader story.

**Notes.** The consequence lands on D-008. The ledger's terms-pull is a read it
makes on a trader's behalf, so it forwards that trader's own token — safe for a
public read, because there is nothing to mint, and unlike the write case ADR
0009 deferred. But it means the ledger now depends on the caller's credentials
for a request it issues for itself, and a pull with no caller behind it — a
retry, a sweep, any background path — has no token to forward. If that ever
happens this entry is the one to revisit, and it lands back on the
service-to-service auth question still open below.

---

### D-019 — `initial_price` is omitted from the public projection

**Date:** 2026-09-20 · **Ticket:** #62 · **Status:** active

**Decision.** `PublicMarketOut` and `PublicMarketSummaryOut` do not carry
`initial_price`. `MarketOut` keeps it unchanged.

**Why.** It is the `q = 0` opening price from `core/opening_prices.py`. On a
market that has traded it is simply not the price, and a stale `0.5` beside a
live market is worse than no number at all, because nothing on the response
tells the reader it is stale. [X-3] #36's "YES and NO prices use the
authoritative LMSR market state" is served by the snapshot endpoint, not here.

**Rejected.** Shipping it under its honest name and trusting the frontend to
stop reading it once trading exists. The field would be correct on day one and
wrong from the first trade, with no test and no type failing in between.

**Notes.** The admin create form still needs it for [1.2] #2's uniform opening
price; that is `MarketOut`, which is untouched. See D-021 for why there are two
schemas to make this distinction in.

---

### D-020 — Per-status counts stay at the service layer

**Date:** 2026-09-20 · **Ticket:** #62 · **Status:** active

**Decision.** `count_by_status` lives in `service/browsing.py` and is not on the
public list response.

**Why.** #62's instruction is to build the status query once, not to expose it
twice. [X-1] #34 asks for no counts; [2.1] #5 does, and #5 is an admin story
whose buckets include `draft`. Exposing counts on the trader response now would
either leak the draft bucket or ship a number that #5 has to replace.

**Rejected.** Putting counts on the public list payload. Trader-visible counts
and admin counts are different aggregates over different visibility rules, so
one payload cannot be both.

**Notes.** [2.1] #5 owns the admin exposure and the draft bucket. The public
counts already exclude what a trader cannot see and derive from the clock rather
than the status column, both pinned in `unit_test/service/test_browsing.py`.

---

### D-021 — Two schemas: `PublicMarketOut` beside an unchanged `MarketOut`

**Date:** 2026-09-20 · **Ticket:** #62 · **Status:** active

**Decision.** #62 adds `PublicMarketOut` and `PublicMarketSummaryOut`.
`MarketOut` and `MarketSummaryOut` are not modified. This resolves the open note
on D-016.

**Why.** The admin console and the pricing engine want opposite things from one
number. `unit_test/model/test_schemas.py::test_the_pricing_numbers_serialise_as_numbers_not_strings`
asserts `MarketOut` sends `liquidity_b`, `seed_subsidy` and `max_platform_loss`
as JSON numbers, because the create form compares the subsidy against the max
loss and `"250" + 10` is `"25010"` in a browser. D-016 requires the public read
to send `b` as an exact decimal string. Both are right; one schema cannot be
both.

**Rejected.** Changing `MarketOut` and fixing the form — it breaks a test whose
reasoning is sound, on behalf of a consumer that is not the one with the new
requirement. A single model with a serialisation flag or two dump modes — one
schema with two wire forms is harder to reason about than two schemas, and
/docs would describe only one of them.

**Notes.** A guard test asserts `MarketOut` still emits floats, so resolving
this conflict in the wrong direction later goes red with the reason attached.
Exactness was not the only blocker anyway: the public projection also has to
drop `creator_id` and `draft_key`, so `MarketOut` could not have been reused
whatever the number format.

---

### D-022 — The public projection derives `status`; `MarketOut` reports the column

**Date:** 2026-09-20 · **Ticket:** #62 · **Status:** graduated to ADR 0011
(amendment)

**Decision.** `PublicMarketOut` and `PublicMarketSummaryOut` derive `status`
from `closing.is_open_for_trading`, so a market past its `close_time` reads as
`closed` before the sweep writes it. The public browse filters and counts derive
it too. `MarketOut` and `MarketSummaryOut` are unchanged and keep reporting the
stored column.

**Why.** ADR 0011's decision is untouched — the clock closes a market and the
sweep only writes it down. What changed is the reader. Every route was
admin-only when that record was written, and an administrator wants the column:
the gap between `close_time` and `closed_at` is how you tell whether the sweeper
is running, and [2.1] #5 counts against it. #62 adds a trader, who has no such
interest, and [X-1] #34's "open markets are clearly distinguishable from closed"
cannot hold if the payload says `open` for a market that stopped four seconds
ago. Leaving it to the client puts the distinction only in whichever clients
remember to recompute it, which is the "three places to get it wrong" ADR 0011's
own decision section rejects. The split is by audience, not by rule.

**Rejected.** Following ADR 0011's Consequences literally and leaving every
reader to combine `status` with `close_time`. Correct while every reader was an
admin and one shared payload; it makes a trader-facing correctness property
depend on frontend discipline. Deriving in `MarketOut` too — an administrator
would lose the only symptom a stopped sweeper produces.

**Notes.** Written up as an amendment block on
[ADR 0011](docs/adr/0011-market-auto-close.md), with the reversal trigger stated
there: it goes back to one shape on the day an administrator needs the raw
column *from this projection*, because one payload serving both audiences needs
a field rather than a substitution. The trigger is that requirement, not a
reader and not a new endpoint — an administrator reading the public projection
is reading it as a trader, and `MarketOut` still carries the column they would
go to for sweep health. *Reworded 2026-09-21 with the ADR, which previously said
"the day an administrator reads the public projection" — a trigger #62 fires at
merge, since these routes take any valid token (D-018) and a test asserts an
admin gets a 200.*
`docs/api/market-service.md` carries a table of which projection does which, so
the rule is visible from the contract as well as from the ADR. The derivation
only ever makes a market *less* tradeable: an early close ([2.3] #7) leaves
`close_time` in the future deliberately, and a market already CLOSED is never
reopened by it — pinned by
`test_an_early_closed_market_is_not_reopened_by_the_derivation`.
*Superseded in part 2026-09-29 by [2.1] #5: "[2.1] #5 counts against it" no
longer holds. The admin overview derives its status and its counts; see "The
admin overview derives status, and its counts are read against the
derivation, not the column" below, and ADR 0011's #5 amendment.*

---

### D-023 — The ADR 0011 predicate lives in `core/closing.py`, not restated

**Date:** 2026-09-20 · **Ticket:** #62 · **Status:** active

**Decision.** The two-condition check ADR 0011 is about — status is OPEN, and
`close_time` has not passed — moved to `core/closing.py::is_open_for_trading`,
taking a `bool` and a `close_time` rather than a `Market`. `service/closing.py`
and `model/schemas.py` both import it. `service/closing.py` keeps
`is_open_for_trading()` (the entity-shaped wrapper) and `open_for_trading()`
(the SQL `WHERE`-clause form) as its own public surface; only the predicate
itself moved, not the query concern.

**Why.** #62 added a second caller: `model/schemas.py` derives the status a
trader is shown by the same rule (D-022), and restating the two conditions
there — which the first cut of this ticket did — is exactly the "three places
to get it wrong" ADR 0011 names by hand. `model/` cannot import
`service/closing.py` under this repository's layering rule, so the shared
piece had to move down to `core/`, which both are allowed to import.

**Rejected.** Leaving the restatement in `model/schemas.py`. Passed review
once already and Ihsan asked for the move on sight of the diff: two copies of
one rule is the failure mode ADR 0011 exists to prevent, not a matter of
where the second copy is convenient to write.

**Notes.** `core/closing.py` takes `status_is_open: bool` rather than
`MarketStatus`, because `core/` may not import `model/entities` either —
nothing below `service`/`model` reaches sideways or up. Each caller compares
its own status value to OPEN in one line before calling in. `core/closing.py`
keeps `as_utc`'s naive-datetime tolerance; `model/schemas.py` never needs it,
since Pydantic has already normalised `close_time` to aware by the time it
calls in, but the shared function does not assume that of every caller.

*Corrected 2026-09-20, after Ernest's review of #62.* This entry read as
though moving the predicate resolved the duplication. It did not, and it
could not have. What moved was the two-condition **predicate**, and that part
is true: every call site reaches one definition —
`service/closing.py::is_open_for_trading` wraps it for an entity,
`model/schemas.py` called it directly, and `service/browsing.py` reached it
through the service wrapper.

What did **not** move is the **substitution** built on top of it — "if the
stored status is OPEN and the predicate says no, report CLOSED" — which
`model/schemas.py` and `service/browsing.py` then held one copy of each.
That was never eligible for `core/`, for the reason stated one paragraph up:
the substitution returns a `MarketStatus`, and `core/` may not import
`model/entities`. The predicate could move down and the thing built on it
could not follow. Two copies of three lines is smaller than two copies of the
rule, but it is the same failure and this entry should not have read as
though it had been closed out. D-024 is where the substitution went
and why.

---

### D-024 — The displayed-status substitution lives in `model/entities.py`

**Date:** 2026-09-20 · **Ticket:** #62, Ernest's review · **Status:** active

**Decision.** `entities.displayed_status(status, close_time, *, now)` is the one
definition of "if the stored status is OPEN and the clock says otherwise, report
CLOSED". `model/schemas.py` derives both public projections with it and
`service/browsing.py::count_by_status` counts with it. `core/closing.py` keeps
the two-condition predicate and stays entity-free.

**Why.** D-023 moved the predicate and left this built on top of it in two
copies, one per caller. They were three lines each, in two shapes — one taking
a status and a close time, one taking a `Market` — and only the schema copy had
tests pointed at it. One rule, two spellings, and the browse copy free to drift
without anything going red.

`model/entities.py` rather than `core/closing.py` because this returns a
`MarketStatus` and `core/` may not import `model/entities`. That is not a
preference: it is the same layering rule that made `core.closing` take a `bool`
in the first place, so the predicate could move down and this could not follow
it. Beside the enum it returns, both callers may reach it —
`service` → `model` and `model` → `model` are both allowed.

**Rejected.** `core/closing.py`, which cannot name a `MarketStatus`.
`model/schemas.py` with `service/browsing.py` importing from it — legal under
the layering, but it makes a service import a display rule out of a response
schema, and the rule is about a status rather than about a payload. Deleting
`count_by_status` and deferring it to [2.1] #5, which would have removed the
second copy for free but reverses D-020.

**Notes.** *Caller list updated 2026-09-21 (D-027).* `model/schemas.py` no
longer calls this at all — the derivation moved into `service/browsing.py`,
which is now the only caller: `browse` for each `MarketCard` and
`get_published` for the `trader_facing_status` it stamps. The schemas read a
plain field. That is one caller rather than two, which weakens this entry's
own argument for the wrapper existing — it stays because the rule is still a
fact about a `MarketStatus` and both call sites in `browsing.py` would
otherwise restate it, and because `count_by_status` is a third caller.

ADR 0011's rule is now expressed in five places and four of them
reach one definition: `core/closing.py` (the predicate), the entity wrapper and
this substitution above it, plus the two callers that use it.

**The fifth can never be removed.** `service/closing.py::open_for_trading` is
the `WHERE`-clause form, and it returns a `ColumnElement`, not a `bool` — so it
cannot call `core/closing.py` and no refactor will make it able to. Somebody
will eventually notice it restates the two conditions and try to collapse it
into the others; it is written in SQL because it has to run in the database, and
that is the whole reason it exists. A pointer sits on the function saying so.

---

### D-025 — One clock per request, read at the controller, Python's not the transaction's

**Date:** 2026-09-20 · **Ticket:** #62, Ernest's review · **Status:** active

**Decision.** The public read reads `datetime.now(UTC)` once in
`controller/public_routes.py` and threads it through filtering (`browse`),
counting (`count_by_status`) and derivation (`displayed_status`, reached through
Pydantic's validation context). The service layer keeps defaulting it, so it
stays callable without a controller.

**Why.** The filter and the display were reading two different instants, and not
because of clock skew — with zero skew, always. `open_for_trading()` binds
`func.now()`, which is `transaction_timestamp()`, frozen when the transaction
opened. The schema's derivation ran at `model_dump_json()`, after the query
returned, and is therefore strictly later. A market whose `close_time` landed in
that gap passed the `status=open` filter and then serialised as `closed`: it
appeared in the open tab, labelled closed. `count_by_status` split the same way.

Pydantic's `model_validate(entity, context={"now": ...})` is the only way to
hand a `model_validator` a value, so that is the seam. `context` is `None` when
nobody passes one, and the derivation then reads the clock itself — which is
every direct `model_validate` in the suite.

**This departs from the argument in `service/closing.py`**, which tells [T-2]
#22 to pass Postgres's clock rather than the container's, because two replicas
drifting a few seconds apart would disagree about whether a market was due.
That argument is sound and it does not reach here. It is about paths that
**decide and write** — the sweep, and a trade holding a row lock. A browse is a
display read: two traders on two replicas seeing a market flip a few
milliseconds apart decides nothing and writes nothing. What a display read needs
is that its own two halves agree, which is what this buys.

**Rejected.** `SELECT transaction_timestamp()` before the query, threading
Postgres's clock into the Python derivation instead. It gives replica agreement
as well as internal agreement, and it costs an extra round trip on the hottest
read in the service to buy a property no reader can observe. Also rejected:
deriving the status in SQL as a returned column, which fixes the class of bug
outright but writes ADR 0011's rule into a sixth place and stops `browse`
returning `Market` entities.

**Notes.** *Corrected 2026-09-21, after Ernest's second review of #62.* The
reasoning above stands and the conclusion is unchanged — one clock per request,
Python's rather than the transaction's, for the reasons given. **What was wrong
is the delivery mechanism.** Threading the clock through
`model_validate(..., context={"now": ...})` does not reach the wire.

FastAPI re-validates whatever a route returns against its `response_model`, and
that second pass carries no context. `_now_from(info)` returned `None`, and
because `_derive_status` was `mode="after"` it mutated the already-correct
instance *in place*, recomputing against `datetime.now(UTC)` at serialisation.
`revalidate_instances` is at its default `never` and does not prevent it.
Wrapping a validated summary in `PublicMarketListResponse` was enough to
trigger it with no framework involved. So a market selected by the SQL
`status=open` filter shipped labelled `closed` — the exact failure this entry,
ADR 0011's amendment and three docstrings claimed was fixed.

D-027 replaces the mechanism: the derivation moved into `service/browsing.py`
and happens before either projection is built, so there is no validator left to
re-run and re-validation is harmless by construction. The clock is still read
once at the controller and still handed to the service layer.

The reversal trigger: **if a path that decides or writes ever reads
this same projection, it needs the transaction's clock.** At that point the
`now` handed in stops being a display convenience and becomes the value an
action is judged against, and `open_for_trading()` is still how it is obtained.
`service/closing.py::open_for_trading` carries a pointer back to this entry, so
somebody reading the argument for the transaction clock finds out in place that
one caller deliberately does not follow it.

---

### D-026 — A proposed winner is not public until a second administrator agrees

**Date:** 2026-09-21 · **Ticket:** #62, Ernest's second review · **Status:** active

**Decision.** `PublicMarketOut.proposed_outcome_id` is null unless the market's
status is in `DECIDED_STATUSES` — APPROVED today, joined by SETTLED with [3.4]
#12. `MarketOut` is unchanged and still reports the column whatever the status.

**Why.** It shipped ungated. [X-3] #36 asks that *settled* markets display the
winning outcome, and PENDING_RESOLUTION is not settled — it is one
administrator's proposal with a second yet to rule on it. ADR 0016 exists
because that ruling can go the other way: the reviewer rejects, all seven
proposal columns are nulled, and the proposer may re-propose a different
outcome. Every trader who loaded the detail page in between was shown a
"winning outcome" the platform then reversed, with no correction, no
notification, and before [3.3] #11's dispute window exists to contest it.

The field's name is what makes it unsafe rather than merely early. Anything
rendering `proposed_outcome_id` is rendering a result; the status is the only
thing that says it is provisional, and nothing obliges a client to check it.

**Rejected.** Gating on `approved_at IS NOT NULL`, which is the same answer
read off a different column and would have to be revisited the moment SETTLED
lands. A separate `decided_outcome_id` field alongside — more wire, and it
leaves the unsafe field in place for somebody to read. Leaving it to the
frontend, which is D-022's argument in reverse: a correctness property that
only holds in whichever clients remember to recompute it is not a property.

**Notes.** `DECIDED_STATUSES` lives in `model/entities.py` beside the enum, so
SETTLED becomes public by being added to one tuple. *Updated 2026-09-21
(D-027).* This previously said the gate runs before the ADR 0011 derivation
and reads the stored status. There is no longer a derivation validator to run
before: D-027 moved it into `service/browsing.py`, so the gate reads the
derived status off a plain field. The rules still cannot interact, because the
derivation only ever turns OPEN into CLOSED and can neither produce nor consume
a proposal status.

**Placement.** The gate is a `model_validator` on `PublicMarketOut` rather
than a step in `service/browsing.py`, and CLAUDE.md puts business rules in
`service`. Raised in review as a layering deviation; kept here deliberately,
on two grounds. It decides what a projection carries rather than what the
platform does — no state moves, nothing is written, and the same market read
through `MarketOut` is unaffected. And the rule CLAUDE.md is protecting is
that business rules are testable without HTTP, which holds:
`unit_test/model/test_public_schemas.py` asserts it with no route, no
database and no clock.

**This reverses on a second caller of `get_published`.** The entity that
function returns still carries the raw `proposed_outcome_id`, and today
exactly one thing reads it — `controller/public_routes.py`, which projects
through `PublicMarketOut` and therefore through the gate. A second caller
that used the entity directly, or projected it through anything else, would
get the ungated value with nothing going red: [3.3] #11's dispute window and
[T-2] #22's composite are both plausible ones. At that point the gate belongs
in the service, computing the public winner into a field the projection
copies, and the model test becomes a serialisation test.

---

### D-027 — The derived status is computed in `service/`, before projection

**Date:** 2026-09-21 · **Ticket:** #62, Ernest's second review · **Status:** active

**Decision.** `browse()` runs a column select and returns a frozen `MarketCard`
dataclass whose `status` is already derived. `get_published()` returns the
entity and stamps the derived value onto an **unmapped** attribute,
`trader_facing_status`. Both `model_validator`s that derived `status` are gone,
and so is the `context={"now": ...}` plumbing. The schemas carry plain fields.

**Why.** Two separate bugs, one fix.

D-025's context never reached the wire — FastAPI re-validates against
`response_model` with no context and the `mode="after"` validator recomputed in
place. And `noload()`, added to stop the list query firing two `selectin`
loaders it had no use for, does not skip a collection: it marks it **loaded and
empty**, so every browsed market sat in the identity map claiming no outcomes
and a later `get_published()` in the same session was handed that instance back.
Deriving in `service/` before projection removes the first; selecting columns
removes the second by keeping the identity map empty rather than by keeping it
correct.

It also puts the rule where CLAUDE.md says business rules go, and leaves the
projection a dumb carrier — which is what makes re-validation harmless instead
of merely survivable.

**Rejected — and the deciding argument is the last one.**

- **Stamping the derived status onto the mapped `Market.status`**, which is the
  obvious form of "stamp it on the entity". Probed: `len(session.dirty)` goes
  from 0 to 1. The instance is dirty, so any later `commit()` on that session
  writes the derived `CLOSED` into the status column — making this derivation a
  *writer* of the one column ADR 0011 reserves for the sweep. Latent today
  (`autoflush=False`, nothing commits on the read path) and live the first time
  a handler browses and then writes anything. `get_published` therefore stamps
  an unmapped attribute, which cannot be flushed at all.
- **`load_only()`** — probed: does not touch relationships. `selectin` still
  fired and both children still loaded, so it does not even solve the problem
  it was suggested for.
- **`noload()`** — the status quo, and the cause. Probed: later read in the
  same session returned 0 outcomes for a market with 2.
- **`raiseload()`** — probed: prevents the eager load and does *not* poison the
  identity map, so it is a genuine second choice. Rejected because accessing
  the collection on a browsed instance raises, which is loud but still a
  failure mode; a column select has nothing to access.
- **`populate_existing()` on `get_published`** — probed: rescues that one
  caller. Rejected because it patches the reader rather than the poisoner, so
  every future caller has to know to add it.

**Notes.** `MarketCard.status` is the derived value and there is deliberately
no raw one beside it: the browse projection has no legitimate use for the
stored column — that is `MarketOut`'s job — and offering both only lets a
reader take the wrong one. Frozen, because it is an answer computed against one
clock and mutating it reinterprets that answer without it.

`PublicMarketOut.status` reads `trader_facing_status` through a
`validation_alias`, so there is one status on the schema and no way to ask for
the administrator's by accident.

Audited while making this change: nothing else in `market_service` stamps a
derived value onto a mapped attribute. Every ORM assignment in `service/` is in
a genuine write path — submit, publish, close, propose, approve, reject, and
the autosave's `_apply`. This PR would have introduced the pattern.

---

### D-028 — The market pool account is keyed `owner_id = market_id`

**Date:** 2026-09-20 · **Ticket:** #96 · **Status:** active

**Decision.** A market's `MARKET_POOL` account is resolved with
`accounts.ensure(session, AccountKind.MARKET_POOL, market_id)`. The market id
goes in `owner_id`, so `uq_accounts_kind_owner` means "one pool per market".

**Why.** The type already fits: `Account.owner_id` is
`mapped_column(Uuid, nullable=False)` and `market.markets.id` is a `Uuid`. No
column changes, and no foreign key — the same trade `owner_id` already makes for
a `USER` account naming a row in `auth.users` it has no grant to read.

**The race depends on it, which is the real argument.** D-010 has two concurrent
first-touches both inserting a book and the PK on `market_id` turning the loser
into an `IntegrityError` it recovers from. That covers the *book*. The pool
account is a separate row written before it, and if `owner_id` were anything but
the market id — a fresh `uuid4`, a sentinel — the unique constraint would not
fire on it. Both callers would create a pool account, only one book would
survive, and the loser would hold an orphan account with no book pointing at it
and no way to find it again. Keyed this way, `accounts.ensure` handles it one
level down exactly as it already does for two tabs loading a balance at once,
and the recovery path is one that ships with tests.

**Rejected.** A sentinel `owner_id` with the market named only by
`market_books.pool_account_id`. It puts the uniqueness in a column the insert
race does not check, which is the one place it has to be.

**Notes.** `PLATFORM_OWNER_ID` is the all-zero UUID for the opposite reason —
there is exactly one house account and no natural owner to name it by. A pool
account has a natural owner and should use it.

---

### D-029 — `state_changed_at` equals `opened_at` at book creation

**Date:** 2026-09-20 · **Ticket:** #96 · **Status:** active

**Decision.** Both columns are written with the same timestamp when the book is
created. Tests assert equality, not merely that both are non-null.

**Why.** For a market nobody has traded, the book's creation *is* its last state
change. `state_version` is 0 and `q` is 0 for every outcome, and that state
began when the row was written. Any other value would be inventing a moment that
did not happen.

**Notes.** This also settles the `occurred_at` question that sat in Open. The
realtime contract defines `occurred_at` only as "the time of the event", and a
snapshot of a never-traded market has no event to name — so it reports
`state_changed_at`, which this entry now gives a defined value. That bullet is
removed from Open.

---

### D-030 — Upstream failures map to 503, 404 and 401, and the timeout is explicit

**Date:** 2026-09-20 · **Ticket:** #96 · **Status:** active

**Decision.** The terms pull maps what it gets back:

| upstream | ledger raises | status |
| --- | --- | --- |
| connect error, timeout, 5xx | `MarketTermsUnavailable` | 503 |
| 404 | `MarketNotFound` | 404 |
| 401 | `NotAuthenticated` | 401 |

and the client passes an explicit `httpx.Timeout`, never the library default.

**Why.** These are three different things and collapsing them loses the only
information the caller can act on. A 503 says the market service is down and the
trade is worth retrying. A 404 says this market does not exist, or is a draft, or
is submitted — `browsing.get_published` deliberately makes those three
indistinguishable — and retrying will never help. A 401 says the token the
ledger forwarded has expired, which is the caller's session problem and is fixed
by logging in again, not by the ledger claiming its dependency is unavailable.

**The timeout is stated rather than inherited.** This call sits in the trade
path. A market service that accepts the connection and then stops responding
holds a ledger request, its database session and its row locks open for as
long as the socket stays alive — so a hung dependency becomes a ledger that
cannot write rather than a trade that fails fast.

*Corrected 2026-09-21, review of #96.* This previously said the timeout "has
to be explicit because httpx's default is five seconds of connect and no
ceiling on read". That is wrong: httpx's `DEFAULT_TIMEOUT_CONFIG` is
`Timeout(timeout=5.0)`, which bounds **all four** phases at five seconds,
read included. `_TIMEOUT` in `service/market_terms.py` sets exactly those
values, so it is byte-for-byte the default and changes nothing at runtime —
which is why deleting the `timeout=` argument entirely leaves
`test_the_request_carries_an_explicit_timeout` green. There is no behaviour
there for a test to catch.

The line is kept as a statement of intent: five seconds is a number this
service chose, not one it inherited, and the next person to touch it has
somewhere to change it. **The open question is whether five seconds is the
right budget**, which this record does not answer. A read timeout inside a
transaction holding row locks is a different trade-off from a read timeout on
a browse page, and the argument above is the argument for a shorter one.
Deciding it needs a number for how long a first touch may reasonably take,
and nobody has measured that yet — noted under Open.

**Rejected.** Mapping everything non-2xx to 503, which tells a trader to retry a
market that does not exist. Letting `httpx.HTTPError` escape, which surfaces as
a 500 on a condition that is neither a bug nor the caller's fault.

**Notes.** `NotAuthenticated` already exists in `core/errors.py` at 401 and is
reused rather than duplicated. `MarketNotFound` is new to this service; the
market service has its own with the same name and meaning, and they are
deliberately not shared — ADR 0012's bar is not met by two error classes that
happen to agree today.

*Added by [T-2] #22:* the trade path holds no row locks across this call — the
book lock is taken after both the gate and `ensure_open` — and it rolls back
after its replay lookup misses, so the gate's call holds no connection either.

---

### D-031 — The terms client lives in `service/`, not `core/`

**Date:** 2026-09-20 · **Ticket:** #96 · **Status:** active

**Decision.** `ledger_service/service/market_terms.py`. Its errors live in
`core/errors.py` like every other domain error.

**Why.** The only outbound network adapter this repository already has is
`realtime_service/service/bus.py`, which holds the Redis client and sits in
`service/`. That is the precedent and it points away from `core/`.

`core/` is where a suite runs without infrastructure. CLAUDE.md's own run
instructions say so out loud — `.venv/bin/pytest unit_test/core unit_test/model
# no database needed` — and putting a socket there makes that line false for the
first time. CLAUDE.md's layering rule permits it (`service` may use `core`, and
`core` may not reach up), so this is not something the import graph would have
caught; it is a rule about what each layer is *for*.

**Rejected.** `core/market_terms.py`, proposed on the strength of
`core/security.py` being a settings-bound adapter. That comparison does not
hold: `core/security.py` verifies a signature in process and opens no socket.

**Notes.** The client takes an injectable `transport`, so the suite drives it
with `httpx.MockTransport` and exercises the real URL, the real headers and the
real decimal-string parsing without a market service running. A cross-service
`ASGITransport` is not an option and never will be —
`unit_test/test_import_boundary.py` fails any `import market_service` from this
suite, because that import works under pytest and is an `ImportError` in the
container.

---

### D-032 — The book's writes share `posting.post`'s commit, and nothing may follow it

**Date:** 2026-09-21 · **Ticket:** #96 · **Status:** active

**Decision.** `books.ensure_open` performs every write before the funding
call through `session.begin_nested()` — a SAVEPOINT inside the still-open
outer transaction — and never commits itself. The pool account, the book and
its outcomes land this way. `posting.post` is called last, and its own
`session.commit()` is the only commit anywhere in the path: it commits the
pool account, the book, the outcomes and the funding entries together, or
none of them if anything before it raised.

**Why.** The ticket's constraint was that the book insert, the outcomes and
the funding transaction have to land together or not at all, and
`posting.py` was off limits. Ordering every write to precede `post()`, inside
the transaction `post()` already commits, satisfies that without touching it
— the same shape `service/grants.py` already uses one layer down for the
starting grant.

**Notes.** This settles the general question for one shape of caller and
leaves another open. A caller whose last write *is* the call into `post()`
can always share its commit boundary this way — `books.ensure_open` and
`grants.ensure_granted` are both that shape. **A caller sharing post's commit
boundary must have nothing left to write after it returns**, because nothing
after it is inside the transaction that just ended — `post()`'s replay path
in particular ends the transaction even when it wrote nothing (ADR 0015).
[T-2] #22 is not necessarily this shape: its `state_version` bump and
position update have to happen *before* the call into `post()`, in the same
transaction, never after, or they are not covered by the same commit at all.
See the Open section, rewritten below, for what #22 still has to decide.

---

### D-033 — `parse_float=Decimal` reads exactly; it cannot detect loss that already happened upstream

**Date:** 2026-09-21 · **Ticket:** #96 · **Status:** active

**Decision.** `service/market_terms.py::fetch` parses the response body with
`json.loads(response.content, parse_float=Decimal)`. A bare JSON number in
`liquidity_b` or `seed_subsidy` — defence in depth against D-016 not holding
on the other side — is read into a `Decimal` from its numeral text directly,
with no `float` ever constructed, and no exception raised for arriving that
way.

**Why.** `test_market_terms.py::test_a_json_number_in_the_response_is_still_read_exactly`
requires exactly this: the value must come through exact, not be refused.
`parse_float=Decimal` is what makes "never call float() on a money value"
hold regardless of which JSON shape the field arrived in, because the
callable receives the numeral's text and nothing in between ever holds a
`float`.

**Notes.** What this cannot do is notice that the number was already wrong
before it reached this parser. If `market_service` itself routed a `Decimal`
through `float()` somewhere before serialising `liquidity_b` or
`seed_subsidy`, this client receives whatever text resulted and reads it
exactly — exact, but not correct. This side has no way to check that and is
not designed to; it trusts the wire contract, and D-016's and D-021's guard
tests on `market_service`'s side — the ones pinning `PublicMarketOut` to a
decimal string and `MarketOut` to a JSON number, deliberately — are what
prevent that value from ever being wrong on the way out in the first place.

---

### D-034 — `MarketOutcome`'s primary key is the pair, not a surrogate id

**Date:** 2026-09-21 · **Ticket:** #96 · **Status:** active

**Decision.** `market_outcomes` has no `id` column. `(market_id, outcome_id)`
is the composite primary key, and `(market_id, position)` is a separate
`UniqueConstraint`.

**Why.** Nothing anywhere references one of these rows by its own identity —
every caller reaches them by `market_id`, or by `(market_id, outcome_id)`.
The ticket's "unique on (market_id, outcome_id)" is satisfied by the primary
key itself rather than by a second index beside a surrogate one.

**Rejected.** A `uuid4` surrogate id plus the same two constraints as
ordinary `UniqueConstraint`s. Legal, and no test distinguishes it —
`test_the_outcome_table_carries_no_label` checks its columns with `<=`, not
`==` — but it would be a column with no reader anywhere in this ticket or the
two that follow it.

---

### D-035 — `pool_account_id` is a foreign key; `market_id` still is not

**Date:** 2026-09-21 · **Ticket:** #96 · **Status:** active

**Decision.** `market_books.pool_account_id` references `ledger.accounts.id`.
`market_books.market_id` remains bare, as D-008 through D-010 already have it.
`unit_test/service/test_book_schema.py::test_the_market_id_is_not_a_foreign_key`
is narrowed to assert no `market_id` foreign key specifically, rather than no
foreign key on the table at all.

**Why.** ADR 0003's rule is about a join across a *service* boundary — a
grant this service does not hold. `market_id` names a row in
`market.markets`, a schema `ledger_svc` cannot read, so a foreign key there
would either fail to create or, with a hypothetical cross-schema grant, weld
two services together on purpose. `ledger.accounts` is this service's own
table in its own schema, and the account `pool_account_id` names is created
in the same transaction as the row that names it. A foreign key there costs
nothing and catches a real mistake — a book pointing at an account that does
not exist — that the service-boundary argument was never about.

**Rejected.** The original reading of the acceptance criteria as "no foreign
key on this table at all", which conflated the service-boundary rule with
the table itself.

**`market_outcomes.market_id` is a foreign key too**, into
`market_books.market_id`, and `market_outcomes.outcome_id` is not. *Added
2026-09-21, review of #96.* The first cut left both bare and explained it as
"both are generated by market_service, across a schema boundary this service
holds no grant on" — which is the rule this decision had just replaced. Where
a value came from is not the question; which table it references is.
`market_id` on that table references `ledger.market_books`, this service's
own table, written in the same savepoint by `books.ensure_open`. `outcome_id`
references `market.outcomes`, which `ledger_svc` cannot read, so it stays
bare for the reason ADR 0003 gives.

The mistake it catches is an outcome row orphaned from its book: a market
carrying a `q` vector with nothing to price it against. Unreachable in this
ticket, since the book and its outcomes are inserted inside one savepoint —
but [T-2] #22 writes to this table on every trade, and a constraint is how
that fails loudly rather than being found later by a price that cannot be
computed. `test_an_outcome_cannot_exist_without_its_book` and
`test_the_outcome_id_is_still_not_a_foreign_key` hold both halves.

**Notes.** Checked against the first-touch race rather than assumed safe:
`accounts.ensure` fully resolves the pool account — creating it or finding
the winner's — before `books.ensure_open` ever builds a `MarketBook`, so by
the time the foreign key is checked, `pool.id` names a row already visible in
the current transaction either way. No test in `test_book_concurrency.py`
regressed when either constraint was added. Four tests in
`test_book_schema.py` did have to open a book before writing outcome rows,
which is the constraint doing its job on fixtures that had been writing
orphans.

---

### D-036 — D-012's "no locks" is the warm path; the first touch locks twice

**Date:** 2026-09-21 · **Ticket:** #21 · **Status:** active

**Decision.** D-012 stands for the pricing read and is corrected in scope. The
preview's read of `q`, `b` and `state_version` takes no locks, as D-012 says.
The path that *opens* a market's book takes two, once per market ever:
`books.ensure_open` ends in `posting.post`, and `accounts.lock` holds the
`PLATFORM` row and that market's pool row `FOR UPDATE` while the funding
transaction commits.

**Why.** D-012 was written before [F-7] #96 existed, when a preview was only a
read. D-008 then made the preview a market's first toucher, so the sentence "the
cost preview is an unlocked read" is now false for exactly one request per
market and true for every one after it. Both halves of that are in #21's
acceptance criteria, one bullet apart — "takes no locks (D-012)" and "that path
writes and takes the handoff's locks" — so the criteria already know this and
D-012's text is what is out of date.

Nothing about D-012's argument changes. The reason not to lock the pricing read
is that a preview decides no write and the gap to confirm is a human one; that
is still true, and the staleness check remains #22's under its own lock. What is
added is that the *book-opening* write is a write like any other and ADR 0015
applies to it normally.

**Reversal trigger.** This correction lapses the moment the pricing read decides
a write. If a later ticket has the preview record a quote, bump a counter, or
reserve anything, then it is no longer a read that feeds no write, ADR 0015
applies to it directly, and D-012's original scope — an unlocked read, full stop
— is no longer available to cite. Re-decide it there rather than inheriting this
entry.

**Notes.** The contention this creates is with the signup grant, not with other
previews. Both lock the same `PLATFORM` row — `grants.ensure_granted` for the
starting credits, `books.ensure_open` for the seed subsidy — so a first-ever
preview queues behind any registration whose first balance read is in flight,
and behind every other market's first touch. Bounded and rare: one grant per
user ever, one subsidy per market ever, and
`test_two_markets_opening_at_once_do_not_deadlock` already covers the ordering,
because `accounts.lock` sorts ascending by id on every path.

Two previews of a *warm* market share no lock at all and must not serialise.
That is asserted directly rather than left to inference — a preview that took
the book row `FOR UPDATE` "for consistency" would pass every single-caller test
in the suite while turning every keystroke in a busy market into a queue. The
test for it fails when a lock is *added*, which is the reverse of every other
race test here and the only honest way to assert an absence.

---

### D-037 — The preview is a market's first toucher, and the cold path is self-extinguishing

**Date:** 2026-09-21 · **Ticket:** #21 · **Status:** active

**Decision.** [T-1] #21's `GET /ledger/markets/{id}/preview` is the caller D-008
was written for. On a market with no book, this GET calls `market_service` over
HTTP, writes a pool account, a `market_books` row and one `market_outcomes` row
per outcome, and posts the seed subsidy from `PLATFORM` — then prices from what
it just wrote.

**Why.** D-008 settled that market terms arrive by lazy pull on first touch and
named no toucher, because neither #21 nor #22 existed yet. #21 lands first, so
the preview inherits it. Refusing a cold market instead, and waiting for #22 to
be the first writer, would mean a trader who opens a market before anyone has
traded in it sees an error where a price belongs — on the one read whose whole
purpose is to be safe to fire on every keystroke.

**Consequences, recorded because each one is surprising on its own.** A `GET` in
this service writes, and is not the first: reading a balance mints the signup
grant under ADR 0009, and this is the same shape one layer up. A `GET` in this
service calls another service over the network, which is new — the ledger's only
outbound dependency, and it sits in the read path, not only in the trade path. A
`GET` in this service mints credits, in the sense that `PLATFORM` goes more
negative by the seed subsidy. And a preview's latency is bimodal: the first
request on a market can take up to `market_terms._TIMEOUT`, every later one is a
single indexed read, and the issue's Notes hand the debounce to the frontend on
that basis.

**Cost.** The ledger now holds a runtime dependency on `market_service` for a
market's first touch, and whichever trader arrives first pays up to the terms
timeout for it. Both were accepted knowingly under D-008, which recorded the
dependency as the price of avoiding a dual write at publish; this entry is where
that price is actually charged, and to a trader rather than to an administrator.

**The cold path is self-extinguishing, which is what makes all of that
affordable.** It runs once per market ever. `ensure_open` reads the book before
anything else, so the second touch makes no HTTP call at all —
`test_a_second_touch_makes_no_http_call_at_all` is the assertion that keeps it
that way — and a market service outage after the first touch cannot stop anybody
pricing a market that already has a book.

It extinguishes only for an id that resolves. A well-formed id that
`market_service` answers 404 for writes nothing, so every request for it takes
the cold path again: an outbound call per request, with no bound on how many a
trader can make by looping random UUIDs. "The cold path holds no connection
across the terms pull" stops that from holding a pooled connection per call;
the outbound calls themselves remain, and negative caching of unresolvable ids
is deferred to a follow-up rather than settled here.

**A closed market's first preview funds a pool that will never trade, and that
is accepted:** settlement returns whatever the pool has left to `PLATFORM`
([3.4] #12), so a dead pool overstates credits in circulation until its market
resolves rather than permanently, and nothing in the cold path has to learn a
status it cannot read.

**Notes.** The token forwarded upstream is the caller's own, never one minted
here. A preview is therefore refused 401 by the *market* service on an expired
token, mapped through D-030, which is the same answer this service would have
given from its own verification a moment earlier. The route needs the raw token
as well as the decoded claims for that reason alone, which is why `AccessToken`
exists beside `CurrentUser` rather than `CurrentUser` growing a field.

A bad `outcome_id` on a cold market opens and funds the book before it is
refused, because the criteria check the market id before any write and the
outcome against the book once it exists. That is deliberate: the market is real
and published, the book is the same one the next honest request would have
created, and rolling it back would discard a correct once-per-market write over
a wrong query string and charge the next caller the timeout again.

---

### D-038 — A quantity takes money's scale of 4 at the API boundary

**Date:** 2026-09-21 · **Ticket:** #21 · **Status:** active

**Decision.** `quantity` on the preview route is a decimal string, greater than
zero, with at most four decimal places. A fifth is `422` rather than rounded,
and the value is echoed back in the response exactly as it arrived.

**Why.** Refusing is the whole point. Rounding `10.00005` to `10.0001` quotes a
trade for a quantity the trader did not type, and [T-2] #22 charges for the
trade it was quoted — so the preview would be perfectly accurate about a trade
nobody asked for, and the discrepancy would surface as a balance that moved by
the wrong amount with no error anywhere to explain it. A 422 puts the correction
where the typing happened.

Scale 4 rather than some other number because that is what `Numeric(18, 4)`
stores and what every money value in this service already uses. A quantity at a
finer scale than the cost derived from it cannot be represented in the legs #22
writes.

**Notes.** This decides the wire only. Whether fractional shares exist
internally at all — whether `MarketOutcome.q` should carry a scale of its own,
or shares should be whole numbers — stays in the Open section, where it has been
since [F-7] #96 borrowed the money scale for a column it only ever wrote zero
into. Nothing here forecloses that: a later decision to make shares integral
narrows this rule rather than contradicting it.

---

### D-039 — `quantize_cost` takes an unsigned magnitude; the caller applies the sign

**Date:** 2026-09-21 · **Ticket:** #21 · **Status:** active

**Decision.** `core/pricing.py::quantize_cost` takes the trade's *unsigned*
magnitude and a `Side`, and raises `ValueError` on a negative magnitude rather
than interpreting one. The caller applies the sign afterward — negative on a
buy, positive on a sell — `service/preview.py` today, [T-2] #22's trade path
next.

**Why.** The acceptance criterion is "buy cost rounds ceiling, sell proceeds
round floor — the residue accrues to the pool, never to the trader", and
`ROUND_CEILING`/`ROUND_FLOOR` only mean that on a non-negative input.
`cost_to_trade` returns a *signed* answer — positive for a buy, negative for a
sell (D-002) — so handing a sell's signed output straight to `ROUND_FLOOR`
pulls a negative number further from zero, which is a *larger* magnitude: the
trader is paid the residue instead of the pool. Refusing a negative input
turns that mistake into an immediate `ValueError` at the call site instead of a
silent one-tick overpayment nothing downstream would notice, because no
balance check watches the fourth decimal place.

**Rejected.** A single function taking the engine's signed answer directly and
choosing the rounding mode from its sign. It reads as simpler and is exactly
the bug above: correct for a buy (positive in, rounds up) and wrong for a sell
(negative in, `ROUND_FLOOR` moves it further from zero).

**Notes.** Verified against `posting._quantize`, at the implementer's request:
`Decimal("12.34565")` — a magnitude with a 5 in the fifth decimal place —
quantizes to `12.3457` on a buy and `12.3456` on a sell. Both values, and the
buy's negated total, pass through `posting._quantize`'s `ROUND_HALF_UP` at
scale 4 unchanged. A total already quantized by `quantize_cost` is therefore
safe for [T-2] #22 to hand to `posting.post`, which quantizes again on the way
in — the second pass is a no-op rather than a second opinion.

---

### D-040 — A cost above `Numeric(18, 4)` is refused, not quoted

**Date:** 2026-09-21 · **Ticket:** #21 · **Status:** active

**Decision.** `service/preview.py` raises `QuantityTooLarge` (422,
`quantity_too_large`) when either number [T-2] #22 would write exceeds
`core/pricing.py::MAX_MAGNITUDE` — `99999999999999.9999`, the largest value
`Numeric(18, 4)` holds. The two numbers are the traded outcome's `q` after the
trade, which lands in `market_outcomes.q`, and the unquantized magnitude of
the cost, which lands in the legs. Both are checked before anything is
quantized. The route also refuses a `quantity` of more than 18 digits in total,
as plain validation.

**Why.** This route's contract is that the previewed number is the charged
number, and [T-2] #22 charges by writing `total` into `Numeric(18, 4)`. A
`quantity` of 1e15 prices at fifteen integer digits, which the column cannot
store, so returning it quotes a trade whose confirm step is a
`NumericValueOutOfRange` — a 500 arriving after the trader committed to a quote
this service answered `200` to. That is the same argument D-038 makes about a
fifth decimal place, applied to magnitude instead of scale: refusing beats
quoting a trade nothing can charge.

**Why on the cost and not on the quantity.** An `le=` beside D-038's
`decimal_places=4` would be the obvious place, and it cannot work. The bound is
on the *cost*, and cost scales with `b`, which this service reads from the
market rather than choosing — `market_service` puts no ceiling on it either. No
constant ceiling on `quantity` is both safe for a small `b` and usable with a
large one, so the check has to be on the priced figure.

**Why the resulting `q` too.** The cost bound alone let through a buy of
`0.0001` shares against `q = [99999999999999.9999, 0]`: a cost of one tick,
and a `q` of `100000000000000.0000` that `market_outcomes.q` cannot store. That
is the same quote-that-cannot-be-honoured the cost bound exists to refuse,
arriving through the other column.

**Why before the quantize.** The first version checked the quantized figure,
on the ground that it is the one that would be stored. It never got that far
on the quantities that mattered. `quantize` runs in the ambient 28-digit
context, and a `quantity` of `1E+25` has no fifth decimal place, so it passed
validation and priced at a magnitude whose scale-4 form needs 30 digits, and
`quantize` raised `decimal.InvalidOperation`. That is not a `LedgerError`, so
it reached the client as a 500 and the bound never ran. Checking the raw
magnitude instead gives the same answer on every storable value, because
`MAX_MAGNITUDE` sits exactly on a tick. The one exception is a sell between
`MAX_MAGNITUDE` and the next tick, which would floor to a storable number and
is refused anyway; it cannot occur, because a sell pays less than its
quantity and its quantity is at most `q`.

**422 rather than 409.** `InsufficientSharesOutstanding` is 409 on the grounds
that nothing about the request is malformed and the same request succeeds
against a book with more shares outstanding. This one succeeds against no book
at all: it is a property of the quantity asked for, which puts it with D-038's
refusal and puts the correction where the typing happened.

**Notes.** `core/pricing.py` restates `AMOUNT_PRECISION` and `AMOUNT_SCALE`
rather than importing them, because `model/entities.py` imports `core.database`
and a `core` module importing `model` closes a dependency cycle. Same trade
`market_terms._MIN_OUTCOMES` makes against `market_service`, and the same
mitigation: `test_the_scale_and_precision_match_the_column` fails if the two
disagree, so widening the column cannot leave `MAX_MAGNITUDE` describing the
old one.

The cost bound can no longer be reached through `quote()`. A buy costs less
than its quantity, because every price is below 1, and the `q` bound already
holds the quantity under `MAX_MAGNITUDE`. A sell pays less than its quantity,
which the no-shorting rule holds at or below `q`. It stays as a backstop,
because that argument rests on the engine's prices staying below 1 and on the
checks around it keeping their order. The cost bound is the one check that
states the column's limit directly, so if either of those changes it still
refuses a cost that cannot be stored.

---

### D-041 — A sell whose proceeds quantize to zero is refused, not quoted

**Date:** 2026-09-22 · **Ticket:** #21 · **Status:** active

**Decision.** A sell whose proceeds quantize to `0.0000` is refused:
`ProceedsBelowTick`, 422, `proceeds_below_tick`. It is not quoted at zero and
it is not paid a minimum tick. A buy the engine prices at exactly zero is
refused the same way, as `CostBelowTick`, 422, `cost_below_tick`. A buy priced
above zero and below one tick is unaffected: `ROUND_CEILING` charges the whole
tick, in the pool's favour.

`core/pricing.py::quantize_cost` raises both refusals itself, because [T-2]
#22's write path has to make the same refusal, and a separate function is one
#22 can forget to call.

**Why.** Three answers were available and `cost_to_trade`'s docstring named all
three, deliberately leaving the choice to "where there is a request to refuse".
#21 is such a place, so the choice is made here rather than defaulting into #22.

*Quoting the zero* takes real shares for nothing. That is the surprise this
whole ticket exists to prevent: the route's contract is that the previewed
number is the charged number, and a previewed `0.0000` charged honestly is a
confirm step that transfers shares and moves no credits.

*Paying a minimum tick* pays the trader more than the shares are worth, which
is the residue running toward the trader — the one outcome D-039's criterion
rules out. It would also make the rounding rule direction-dependent on
magnitude, so "the residue accrues to the pool, never to the trader" would stop
being true as a sentence and start needing a footnote.

*Refusing* leaves both rules intact, and it is the answer D-040 already gives at
the other edge of the same quantization: a magnitude `Numeric(18, 4)` cannot
honestly represent is refused rather than quoted, in either direction. The
sides differ below one tick and agree at zero. `ROUND_CEILING` of zero is zero,
and past about 110·b of skew the engine returns exactly zero, so a buy can
reach `0.0000` without any flooring.

**Rejected.** The two options above, and one about the status code.

**409 rather than 422**, and this is the closest call in the entry.
`InsufficientSharesOutstanding` and `InsufficientFunds` are 409 on the grounds
that the request is well formed and it is the state that refuses it — and unlike
`QuantityTooLarge`, this refusal genuinely does depend on the book: the same
sell clears a tick against a less saturated `q`. 422 won on two counts. It pairs
with `QuantityTooLarge` as the two edges of one quantization, which is how a
client should read them. And the correction available to the trader is a larger
quantity, which puts the fix where the typing happened, the same place D-038 and
D-040 put it. The two codes stay distinct rather than being folded together,
because one is fixed by asking for less and the other by asking for more.

**Notes.** This settles the Open entry **"A sell whose proceeds fall below one
tick is quoted at zero, and nobody has chosen that"**, which is removed from
Open below. `test_a_sub_tick_sell_is_quoted_at_zero` was the pin holding that
question open; it is now
`test_a_sub_tick_sell_is_refused_rather_than_quoted_at_zero`, which is the
change of name the Open entry predicted.

`cost_to_trade`'s docstring paragraph on sub-tick trades still describes only
the rounding half and points at Open for the rest, so it needs the same
correction this entry is: the refusal half is no longer open. So does
`docs/api/ledger-service.md`, which currently warns a client that a sub-tick
sell comes back as zero.

The buy side is asserted alongside the refusal in all three layers, because an
implementation that refused a sub-tick *trade* rather than sub-tick *proceeds*
would satisfy every sentence above and stop quoting half the trades in a
saturated outcome.

This entry first refused only sells, on the stated ground that a sub-tick buy
never reaches zero because `ROUND_CEILING` charges the whole tick. The review
of PR #108 disproved that: a buy of 100 shares against `q = [12000, 1]` at
`b = 100` was quoted at `0.0000`, because the engine returned exactly zero.

---

### D-042 — An absolute value on money is `copy_abs()`, never `abs()`

**Date:** 2026-09-23 · **Ticket:** #21 · **Status:** active

**Decision.** Wherever this service takes the absolute value of an amount that
will be quantized, charged or stored, it calls `Decimal.copy_abs()`, or it
calls `abs()` inside `core/lmsr.py::_engine_context()`. A bare `abs()` in
ambient context is not used on money.

**Why.** `Decimal.__abs__` is a context operation: it rounds its result to the
*ambient* precision, which is 28 digits unless a caller has changed it, not
the engine's 50. `copy_abs()` is the only absolute value that consults no
context at all. The difference is not academic. The engine prices a sell of
100 shares against `q = [7000, 0]` at `b = 100` as
`-99.99999999999999999999999999993169…`; `abs()` at 28 digits rounds that up
to `100.0000…`, and `ROUND_FLOOR` then pays the trader `100.0000` where the
true proceeds floor to `99.9999`. The rounding step runs before the directional
rounding sees the value, so it can carry a magnitude across a tick boundary in
either direction — up on a sell pays the trader, down on a buy charges one tick
short — and undo exactly what "A sell whose proceeds quantize to zero is
refused, not quoted" and "`quantize_cost` takes an unsigned magnitude; the
caller applies the sign" exist to guarantee. No balance check watches the
fourth decimal place, so nothing downstream would notice.

`service/preview.py` shipped `abs()` on the engine's answer and was caught in
review of PR #108. The same review found the average-price division pinned to
the engine context while the line that actually cost money was not; the
average is now the quantized magnitude over the quantity, so no absolute value
remains on that path.

**The interface [T-2] #22 was written against has changed with this ticket.**
`core/pricing.py::refuse_sub_tick_proceeds` no longer exists: `quantize_cost`
raises `ProceedsBelowTick` or `CostBelowTick` itself when its result is
`0.0000`, and coerces a string `side` to `Side`. Its signature is unchanged,
but a caller that quantized and then called the refusal has one call too many,
and the refusal it made no longer exists to call.

**Reversal trigger.** If the service pins one decimal context globally at
startup — the engine's precision, set once, with nothing able to change it —
then ambient and engine context are the same thing and a bare `abs()` is
harmless. Until then, or if any code path can run under a caller's context,
this stands.

**Notes.** The same hazard applies to every other context operation on money
outside the engine context: `+x`, `-x`, and any arithmetic. `-magnitude` in
`service/preview.py` is safe only because the magnitude has already been
quantized to at most 18 significant digits, well inside 28.

---

### D-043 — The cold path holds no connection across the terms pull

**Date:** 2026-09-24 · **Ticket:** #21 · **Status:** active

**Decision.** `books.ensure_open` rolls back the transaction its own
book-lookup read began, after that read finds no book and before it calls
market_service. No database connection is checked out while the terms pull
is waited on. The rollback lives in `ensure_open` rather than in any one
caller, so every caller of it is covered; its docstring states the one
precondition — call it with nothing pending on the session.

**Why.** `get_session` does not wrap a request in `begin()`, so the first
`execute` autobegins a transaction and nothing ends it until the request
does. The read that found no book therefore left its pooled connection `idle
in transaction` across an HTTP call that can take the whole five-second
terms timeout. `pool_size` is 10, with 10 of overflow: twenty cold previews
at once, or one slow market service, took every connection, and
`/ledger/balances/me` and `/ledger/entries/me` hung behind a route documented
to fire on every keystroke. Rolling back discards nothing, because the read
found nothing and wrote nothing.

A rollback in the caller alone is not enough, which is why it is not there:
`ensure_open`'s own lookup autobegins a fresh transaction before the fetch,
so releasing the preview's read and then calling `ensure_open` still held a
connection across the call. Only the lookup nearest the fetch can release
it.

**Evidence.** `test_a_cold_preview_holds_no_connection_while_market_service_is_slow`
stalls the upstream and checks, at every call to it, that the session has no
transaction and the pool has nothing checked out, checks Postgres's
`pg_stat_activity` for a backend idle in transaction while the first call is
stalled, and asserts exactly one call. It passes with the rollback and fails
with it removed. Measured once by hand on PR #108: before the fix, 25 stalled
cold previews checked out 20 connections and an unrelated request timed out
waiting for one; after it, 25 stalled previews checked out none and the
unrelated request was served.

**Rejected.** Fetching the terms in the caller, with no transaction open,
and handing them to `ensure_open` through a new `terms` argument. It works,
but it covers only the callers that adopt it, where the rollback covers
every caller at once.

**Scope.** Every caller of `ensure_open`. It does not cover a call to
market_service made anywhere else: `service/market_status.py`'s gate (#110)
fetches the terms itself, after reads on the trade path, and the follow-up
issue carries it.

**Reversal trigger.** A caller that needs `ensure_open` with writes pending
on its session — the rollback would discard them — or `get_session` moving
to a transaction per unit of work, where the release belongs to whoever
opens it.

**Notes.** An objection was raised in the review of this fix and did not
hold. It was that a rollback inside `ensure_open` would discard the work of a
caller with writes in flight, naming [T-2] #22's trade path. Checked against
that path on the `22-buy-shares` branch: everything before its `ensure_open`
call — the unlocked replay lookup and the market-status gate — is a read, so
nothing is pending to discard, and the book row lock is taken after
`ensure_open` returns. The objection is the reversal trigger above, not a
reason against the decision today.

**Note, 2026-10-04 (#115).** The scope above now covers the gate too.
`market_status.ensure_trading` asserts nothing is pending and rolls back
before its own call to market_service, as `ensure_open` does, so the trade
path's separate rollback after the replay lookup is gone. Its evidence is
`test_the_gate_holds_no_connection_while_market_service_is_slow`, which fails
with that rollback removed. Repeated calls for the same unresolvable id are
bounded by "A market_service 404 is remembered for ten seconds, per process";
calls for distinct unknown ids are not, and are listed under Open.

---

### D-044 — A cost exactly on a tick can round one tick against the trader, or toward them on a sell

**Date:** 2026-09-24 · **Ticket:** #21 · **Status:** active

**Decision.** Accepted and documented, no code change. A trade whose true cost
lies exactly on a tick, or within the engine's last significant digit of one,
can be quantized one tick away from its true tick. The *quoted* error — the
difference between the total after rounding and the true cost — is bounded at
one tick, 0.0001 credits, **plus the engine's own last-digit residue**, in
either direction.

Not a flat tick, and the difference is worth stating because this entry names
its own reversal trigger. At `q = [1315, 1000]`, `b = 3`, a buy of `0.0001`
has a true cost of `0.0001 - 2.5065e-50`: the correct ceiling is one tick, the
quote is two, and the error is one tick *and* that residue. The overshoot is
46 orders of magnitude below the tick it overshoots, so the bound is the right
shape and the wrong arithmetic — a reason to say "plus the residue", not a
reason to change the rounding. The bound is otherwise a property of the
rounding, not of the engine: the engine's own error is relative, not a fixed
floor.

**Why it happens, and why precision cannot fix it.** `core/lmsr.py` works at
50 significant digits through `ln` and `exp`, which are transcendental: no
finite precision guarantees that an answer exactly `d` in real arithmetic
comes back as exactly `d`. It often comes back off by one unit in the last of
those 50 digits — a relative error of order `1e-49`, so its absolute size
scales with the cost — and directional rounding amplifies that dust, however
small, to a whole tick. Raising `_PRECISION` shrinks the dust relative to the
cost, it does not remove it.

**Buys, overcharged a tick — toward the pool.** On any two-outcome book
`q = [a, a + d]`, buying `2d` of outcome 0 costs exactly `d` by LMSR's shift
invariance and the symmetry of the two outcomes. `q = [1000, 1100]`, `b = 300`,
buying 200 is true cost `100`; the engine returns
`100.0000000000000000000000000000000000000000000001`, and `ROUND_CEILING`
charges `100.0001`. Not every member of the family misses — `[137, 157]` at
`b = 100` lands exactly — but enough do to be ordinary.

**Sells, paid a tick they did not earn — toward the trader.** Against
`q = [12000, 0]` at `b = 100`, outcome 0 is priced `1 - 8e-53`, so selling
`0.0001` has true proceeds a hair under one tick and "A sell whose proceeds
quantize to zero is refused, not quoted" should refuse it. The shortfall is
past the 50th digit, the engine returns exactly `-0.000100`, and the sell is
quoted at one tick. This is the one case where the residue runs toward the
trader, which D-039 says must not happen; it is bounded at one tick, needs
roughly `120·b` of skew, and cannot be repeated for profit without moving the
book back.

**Rejected.** Snapping a result within some epsilon of a tick onto the tick.
It assumes the true value *is* the tick, and a true cost a hair *above* a tick
would then be undercharged a whole tick on a buy — trading an error toward the
pool for one toward the trader, which is the direction the design forbids.
Exact rational arithmetic is not available for `ln` and `exp`.

**Reversal trigger.** Either characterisation test in
`unit_test/core/test_pricing.py` —
`test_a_cost_exactly_on_a_tick_can_be_charged_one_tick_over` and
`test_a_sell_just_under_a_tick_can_be_paid_the_whole_tick` — going red,
because the engine's precision or `quantize_cost`'s rounding changed; or any
case found where the quoted total differs from the true cost by more than one
tick. Either means the bound stated here no longer holds and this is decided
again.

### D-045 — The ledger asks market_service whether a market is still trading, and a replay is answered first

**Date:** 2026-09-21 · **Ticket:** #109 · **Status:** graduated to ADR 0017

**Decision.** The trade path reads `GET /public/markets/{id}` and refuses
`409 market_closed` when the derived status is not `open`. The call is issued
**before** the `market_books` lock, and **after** an unlocked idempotency
lookup that replays a committed trade and returns without making the call at
all. The preview is unchanged and makes no such call.

**Why.** The book carries no status and no `close_time`, so nothing local can
answer the question, and nothing local can be made to: ADR 0014 leaves
`close_time` in the future on an early close, so a snapshot of it would accept
trades against a market an administrator stopped by hand. The public detail
endpoint's `status` is already ADR 0011's predicate applied, so one field
answers both kinds of close and the rule is read rather than restated.

Before the lock, because the lock holds `q` and `state_version` still and can
hold nothing still in another service's database — there is no lock the ledger
can take that makes a remote answer fresher, and holding the book row across
the hop would serialise every trade in a market behind a round trip for
nothing.

Replay first, because a trade that committed, lost its response and was
retried after the market closed would otherwise be told `409 market_closed` —
a trader believing a trade failed that had in fact charged them. The unlocked
lookup is not the pre-lock lookup ADR 0015 rejected inside `posting.post`: a
hit names a committed, append-only row and is final, a miss is trusted for
nothing, and the write decision is still made under the lock.

**Rejected.** A push from market_service at the sweep and at the early close —
a dual write that fails *open*, and the outbox that would fix it is what ADR
0006 exists to avoid. Snapshotting `close_time` into `market_books` — answers
half the predicate, and the missing half is the one an administrator controls.
The snapshot plus a hop for early closes only — the ledger cannot know when to
make the hop, so the local check can only refuse, never accept. A few seconds
of caching "open" — ADR 0011's correctness-parameter objection, unchanged.
ADR 0017 has all of them argued, plus the one-way closed latch, which is
deferred rather than rejected and is the first move if [5.4] #20 shows the hop
matters.

**Notes.** **This fires the reversal trigger on "One clock per request, read at
the controller, Python's not the transaction's", and declines it on purpose.**
That entry says a path which decides or writes off this projection needs the
transaction's clock. This is that path. The trigger cannot be honoured as
written: the ledger's transaction cannot supply the clock for a comparison
market_service performs, so "the transaction's clock" would mean
market_service's — still remote, still stale by the round trip, and costing an
extra round trip on that service's hottest read, which the same entry already
rejected for the display case. The skew is folded into the accepted window
rather than eliminated, and the argument is in ADR 0017 rather than here so
that the next reader of that entry finds a decision rather than a silence.

Two things this adds that are not local to the ticket. market_service becomes a
runtime and availability dependency of **every trade**, not only of a market's
first touch — the cost "Market terms reach the ledger by lazy pull on first
touch" accepted, charged again and larger. And token forwarding becomes
load-bearing on the hot path, so the warning in "Public market reads require a
valid token, any role" — that a pull with no caller behind it has no token to
forward — now describes every trade rather than one read per market. [3.4] #12
does not add such a caller; [T-7] #27 would.

The refactor this needs: `fetch` gains `status` and must still not gate on it
(`test_the_close_time_is_not_what_decides_anything_here` is the assertion that
keeps it honest), and `_parse`'s terms-only refusals — a null `liquidity_b` or
`seed_subsidy`, and `_refuse_unpriceable` — move into `books.ensure_open`,
because they are rules about writing a book and would otherwise start refusing
trades on books snapshotted weeks earlier.

---

### D-046 — `REDIS_URL` has a default in the ledger and none in the realtime service

**Date:** 2026-09-22 · **Ticket:** #112 · **Status:** active

**Decision.** `core/config.py` declares `redis_url` with a default of
`redis://redis:6379/0`. `realtime_service` continues to require it with no
default. The two services now disagree about the same variable name on
purpose, and this entry is the only place that is written down.

**Why.** The same mistake costs them different things. Point the realtime
service at the wrong Redis and it starts, reports `ok`, and relays nothing —
the failure is the whole service and it is silent, which is why a default that
a forgotten variable could fall back on is refused there. Point the ledger at
the wrong Redis and it loses a broadcast: the publish is fire-and-forget by
design, the trade has already committed and is still correct, and the client
reconciles on its next snapshot, which is the recovery path [X-4] #37 requires
anyway. One lost frame is not worth a service that will not start.

`redis` is the hostname compose gives the bus on the shared network, the same
shape `market_service_url` already uses for a service name, and it is not a
credential — which is the test `database_url` and the inherited signing key
fail and this one passes.

**Rejected.** Requiring it, matching the realtime service. Consistency between
two config files, bought at the cost of a service that will not start when one
variable is missing, for a dependency whose absence costs a broadcast.

**The criterion's own justification, which is wrong and is not the reason
here.** [F-9] #112 argues for the default by claiming `ci-backend.yml`'s
"Verify the app boots" step calls `create_app()` with four environment
variables and this is not one of them. It is: `REDIS_URL:
redis://localhost:6379/0` sits in that workflow's job-level `env:` block and is
set for all five matrix legs, so a required field would pass that step and fail
only in a checkout or a deploy that omitted it. The `market_service_url`
precedent the criterion cites is genuine — that one really is absent from CI —
and this is not that case. The argument above replaces it.

**Reversal trigger.** This lapses if anything on the ledger's publish path
stops being fire-and-forget. If a caller acknowledges, retries, or fails a
trade on a publish error, then a wrong Redis there costs money rather than a
frame, and the realtime service's argument becomes the ledger's too. Re-decide
it at that ticket rather than inheriting this entry.

**Notes.** `docs/api/realtime-service.md`'s environment table said "No default.
A default is a credential in the repo", which now reads as a claim about the
variable rather than about that service. Amended in this ticket to say
"required **here**" and to carry the asymmetry and its trigger.

---

### D-047 — One Redis client for the ledger process, opened on the lifespan

**Date:** 2026-09-22 · **Ticket:** #112 · **Status:** active

**Decision.** `main.py`'s lifespan opens one client onto `app.state.redis` and
closes it on shutdown. `controller/dependencies.py` exposes it as
`RedisClient`, the way `DbSession` is exposed. Not the per-call client
`service/market_terms.py` builds.

**Why.** "The terms client lives in `service/`, not `core/`" accepted a client
per call, and its argument is explicitly about frequency: `books.ensure_open`
reaches it only when a market has no book — once per market, ever — so the cost
is one handshake against a request that is already doing a round trip to
another service and three inserts. That argument does not survive being copied
here. A publish runs once per **trade**, so a client per call is a DNS lookup,
a TCP handshake and a pool teardown on the hot path, for a call whose entire
purpose is to be cheap enough that failing it silently is acceptable.

That same paragraph in `market_terms.py` names the two costs of a held client,
and both are paid here rather than dodged. It needs closing in a lifespan: the
lifespan does the closing. And it fixes the transport at construction, which is
the seam the suite drives through: `publish` takes the client as an argument,
so the seam moves from construction to the call and the tests hand it a
recorder.

**Rejected.** *A client per publish, matching `market_terms`* — the cost above,
per trade, to avoid a lifespan hook and one line of dependency wiring. *A
module-level client in `service/bus.py`*, set and cleared by the lifespan —
fewer moving parts and no `app.state`, but it puts process lifetime in a
service module and the suite then reaches into module state to drive it;
`DbSession` already establishes the shape for "a thing the process holds and a
route receives". *A `PING` on startup to fail fast* — that makes an unreachable
Redis a ledger that will not boot, which inverts the whole argument for
swallowing a publish failure. `realtime_service` made the same call from the
other side, and its `/health` reports the bus separately for exactly this
reason.

**Reversal trigger.** This lapses for a client whose calls stop being hot. If
the only remaining caller of one runs once per market, or rarer, the frequency
argument above inverts and a client per call is the cheaper shape for it again:
one fewer thing held for the life of the process. Nothing planned moves either
client that way; [T-2] #22 moved the terms client the other.

**Notes.** The ledger holds both of its outbound clients — Redis and
market_service — for the process, opened and closed on the lifespan, for the
same reason: each is called on every trade. The test is how often the call is
made, and anyone adding a third client should answer that question before
copying this shape.

**Note, 2026-09-23 (review of #110).** The frequency that justified
`market_terms`'s per-call client stopped holding in the same branch that
recorded this entry. `market_status.ensure_trading` (ADR 0017) calls `fetch`
on every trade that is not a replay, so the terms client is now hot too, and
by this entry's own test it should be held for the process. It is not yet:
`market_terms.py`'s comment says so and names one lifespan-held
`httpx.AsyncClient` as the follow-up, #114, which is its own ticket because it moves
the seam two test modules drive through.

**Note, 2026-10-04 (#114).** Done, the same way as the Redis client. `main.py`'s
lifespan opens one terms client with `market_terms.open_client()` and closes
it on shutdown; `controller/dependencies.py` exposes it as `TermsClient`, and
each route hands it down to `market_terms.fetch(..., terms_client=...)`. The
seam moved from construction to the call, as it did for `publish`: the suite
passes `open_client(transport=httpx.MockTransport(...))`, so the real base
URL, `_TIMEOUT` and request building still run under test. Both outbound
clients are now held for the process, for the same reason.

---

### D-048 — `PriceEvent` is copied into the ledger, and held to its original by a source-reading test

**Date:** 2026-09-22 · **Ticket:** #112 · **Status:** active

**Decision.** `PriceEvent` and its nested `OutcomePrice` are copied into
`ledger_service/model/schemas.py` rather than moved to `shared/`. The copy is
held to its original by a test that reads `realtime_service/model/schemas.py`
and `realtime_service/service/bus.py` as **source text** and parses them with
`ast` — never importing either.

**Why.** ADR 0012's bar is unchanged and `bus.py::publish` is still four lines
of `redis.publish`, below it. What changes is that there are now two copies of
the *model* rather than one model and one consumer, and a drift between them is
not an error on either side: the producer serialises a field the consumer
forbids, the consumer drops the whole event, and the symptom three services
away is prices that quietly stop updating with nothing logged where the fault
is. That is a worse failure than the one `publish`'s four lines could ever
have, and it is the reason the model is the part worth guarding.

Neither service may import the other — `test_import_boundary.py` fails any such
import, and it is right to: the import resolves under pytest and is an
`ImportError` in the container, which holds `/app/<service>` and `/app/shared`
and nothing else. So the only thing that can hold the two together is a test
that reads the other's file. A file read is not an import: the boundary test
matches `from`/`import` at the start of a line, nothing resolves a module, and
the container never runs the suite.

`ast` rather than a regex, on both files. A regex over
`realtime_service/model/schemas.py` matches prose — it is the longest module in
that service and says so in its own docstring — and over `service/bus.py` it
matches the four paragraphs that discuss `PRICE_CHANNEL` by name. `ast` sees
only the annotated fields and the module-level assignment, so a reformat cannot
break the pin and only a real field or channel change can.

**Rejected.** *Moving `PriceEvent` to `shared/`.* Two callers clears ADR 0012's
caller-count bar on its face, but the second half of that bar is that a
divergence would be a bug rather than a design choice — and the whole point of
`extra="forbid"` on the consumer is that the two ends are allowed to be
versioned independently, with the consumer refusing what it does not
understand. A shared model removes the refusal along with the duplication.
*Pinning only against the documented shape in `docs/api/realtime-service.md`.*
That catches a client-visible change and misses both models drifting together,
which is the case no other check in either service would notice. Both pins are
kept, and they fail differently on purpose.

**Reversal trigger.** Move `PriceEvent` into `shared/` if a third service needs
it, or if the source-reading pin ever stops running. The test for the second is
`ci-backend.yml` gaining per-service path filtering: today its filter is at
workflow level and matches `backend/**`, and the matrix runs all five services
on every run, so a rename in `realtime_service` starts the ledger job too.
Narrow that filter and the pin is retired silently and the copies are left
unguarded — at which point indirection is cheaper than a contract nothing
checks.

**Notes.** Recorded as an extension on ADR 0012's "What was deliberately left
copied", which named `bus.py::publish` and not this. The build context is not
the reason for either copy and has not been since [F-6] #76; both stand on
their own merits now.

---

### D-049 — A publish failure is swallowed and logged, and `publish` takes the transaction id to log it with

**Date:** 2026-09-22 · **Ticket:** #112 · **Status:** active

**Decision.** `service/bus.py::publish` catches its own failure, logs it with
the committed transaction's id, and returns normally. It therefore takes that
id as a keyword-only argument: `publish(client, event, *, transaction_id)`.
`asyncio.CancelledError` is not caught.

**Why.** Nothing acknowledges and nothing subscribes on the producer's behalf,
so if the publish throws, the trade has still committed and is still correct.
The caller is [T-2] #22, which has already committed by the time it reaches
this — the exception has nowhere useful to go, and turning a committed trade
into a 500 would tell a trader their trade failed when it had charged them.
ADR 0010 accepts the crash window between the commit and the publish for the
same reason it refuses an outbox: a briefly stale price on a screen that is
about to reconcile is not a lost audit entry.

**Swallowed is not silent, and the transaction id is the whole difference.** A
lost broadcast recovers on its own, so nothing will ever page anybody about it.
That makes the log the only record it happened, and the transaction id the only
field in it that leads back to the trade — the market id names a market with
thousands of trades, and `state_version` means nothing without one.

**The criterion contradicts itself here, and this is the resolution.** [F-9]
#112 gives the signature as `publish(client, event)` and requires the failure
to be "logged with the committed transaction's id". Those cannot both hold:
`PriceEvent` is `extra="forbid"` over four fields and none of them is a
transaction id, so there is nowhere for the id to arrive from. The id stays and
the signature gains it. The natural call site already has it to hand —
`posting.post` returns the transaction it committed.

**Rejected.** *Putting `transaction_id` on `PriceEvent`.* It is not on the wire
contract, and `extra="forbid"` on the consumer would drop every event carrying
it — the exact failure the copy's pin exists to prevent, introduced
deliberately. *Re-raising from `publish` and swallowing in [T-2] #22.* Leaves
the rule in the caller that does not exist yet, so this ticket would ship a
primitive whose most important property is untestable, and #22 would inherit
the rule by copying it or by forgetting to. That is the argument the sub-tick
refusal inside `core/pricing.py::quantize_cost` already won — it lived in a
`refuse_sub_tick_proceeds` of its own when this was written, and #108 folded
it into the quantizer for exactly this reason — applied to a rule about a
broadcast instead of a rule about money. *Catching `BaseException`.*
`CancelledError` is the process going away, not a Redis blip, and swallowing it
would make a shutdown hang on a producer that will not stop.

**Reversal trigger.** This lapses the moment a caller needs to know that a
publish failed — a retry, an acknowledgement, a metric that gates anything, or
an outbox. At that point the failure is information rather than noise,
`publish` must re-raise, and the swallow moves to whichever layer is making the
decision. That is also the trigger on "`REDIS_URL` has a default in the ledger
and none in the realtime service", and the two have to move together.

**Notes.** `test_nothing_in_this_service_calls_publish` asserts that nothing in
this service calls it, which is the "primitive before caller" shape [F-7] #96
and [F-8] #109 already shipped in. **[T-2] #22 deletes that test**; it is noted
in the test's own docstring and on #22's issue, so whoever hits the red does
not go looking for the bug.

---

### D-050 — The realtime snapshot is a second first-toucher, and never gates on status

**Date:** 2026-09-22 · **Ticket:** #112 · **Status:** active

**Decision.** `GET /ledger/markets/{market_id}/snapshot` opens a market's book
via `books.ensure_open` when there is none, forwarding the caller's own token,
exactly as the preview does — and it never gates on whether the market is open.
A closed, pending or approved market is priced and returned.

**Why.** ADR 0017 states the second half outright: "the realtime snapshot
serves a closed market's prices". A closed market still has a price to render —
the last one anybody traded at — a settled one still has a last price, and
`docs/api/realtime-service.md`'s reconnect sequence makes this `GET` step 2 of
recovering from a dropped socket. It has to return a number or the client has
nothing to resume its version check from. Gating would put an error exactly
where a price belongs, on the one read [X-4] #37 depends on. The market page
gates its trade controls on #62's derived status; ADR 0017's hop belongs to the
trade path, which fires once per trade, not to a read that fires on every page
open and every reconnect.

The first half follows "The preview is a market's first toucher, and the cold
path is self-extinguishing" without changing it. Refusing a cold market here
would show a client an error where a price belongs, on a market nobody has
traded — which is the case a fresh market is always in.

**Rejected.** *Refusing a cold market and waiting for a trade to open the
book.* The snapshot precedes everything else a client does, so this would make
a never-traded market unrenderable. *Gating on status and returning
`market_closed`.* That code is 409 and belongs to the trade path alone; on this
route it would break the reconnect sequence for every market that has closed
while a client was watching it.

**Reversal trigger.** The no-gate lapses if the snapshot body ever gains a
field whose value depends on whether the market is open — anything a client
would act on rather than render. At that point the route is answering a
question about state rather than about price, and the gate has to be re-decided
rather than inherited. The first-toucher half lapses if a ticket ever makes the
handoff eager — a push from market_service at publish — which would leave no
cold path for either caller to be first on.

**Notes.** **This makes the snapshot a second first-toucher**, which is the one
consequence "The preview is a market's first toucher" did not anticipate by
name. Everything in that entry survives unchanged: the cold path still runs
once per market ever, still forwards the caller's own token, still funds a pool
that a closed market will never trade against, and is still self-extinguishing.
What is new is that a client **opening a page** — not typing a quantity — can
now be the request that pays the terms timeout, and on a market nobody has
traded it reliably is, because the snapshot precedes the preview in the
realtime contract's "Opening a page" sequence.

"No lock (D-012)" is the *pricing read* only, and this ticket's criteria cite
D-012 without D-036's correction. The warm read takes no lock; opening a book
takes the handoff's locks, once per market — `books.ensure_open` ends in
`posting.post`, and `accounts.lock` holds the `PLATFORM` row and the market's
pool row `FOR UPDATE` while the funding transaction commits. Removing those to
satisfy the sentence would reintroduce the first-touch race on the one write
this route performs. #112's criterion is being reworded to match, the way
#21's was.

**These five entries cite each other by title rather than by number**, because
they land unnumbered and get their numbers when the PR merges — which is also
the rule `docs/adr/` already follows when citing this file.

*Amended 2026-10-20 in #395: no entry gets a number at merge any more.* An
entry after D-052 is headed `D-NEW`, is never numbered, and is cited by its
title, as "Format for new entries" at the top of this file says. These five
did get their numbers, and D-001 to D-052 keep theirs.

---

### D-051 — A mistyped `REDIS_URL` stops the ledger booting; an unreachable one does not

**Date:** 2026-09-23 · **Ticket:** #112 (review of #110) · **Status:** active

**Decision.** `main.py`'s lifespan passes `REDIS_URL` to
`redis.asyncio.from_url` uncaught. A value that does not parse as a Redis URL —
`http://redis:6379`, a mangled scheme — raises `ValueError` there and the
ledger refuses to start. A value that parses and points at a Redis that is
down, or that accepts and never answers, starts normally and costs one lost
broadcast per trade, bounded by a five-second socket timeout.

**Why.** Unreachable and mistyped are different failures, and "`REDIS_URL` has
a default in the ledger and none in the realtime service" only argued about
the first. An unreachable bus is an outage: it ends, the broadcasts lost while
it lasts are reconciled by the next snapshot, and refusing to boot over it
would stop trading to protect a frame. A mistyped URL is a configuration error:
it never ends. Caught and logged, it would drop every broadcast from the first
trade onward, from a ledger whose `/health` says `ok` and whose trades all
succeed. The one log line would sit at startup, where nobody debugging a price
display is looking. Failing at boot is the only point where a typo gets found
by the person who made it.

The five-second timeout is what makes "unreachable costs a broadcast" true for
the silent case as well as the refused one. Without it a blackholed host makes
`publish` wait rather than raise, `service/bus.py`'s `except Exception` never
fires, and [T-2] #22's committed trade hangs holding its session. Same budget,
and the same reason, as "Upstream failures map to 503, 404 and 401, and the
timeout is explicit". redis-py 8.1.0 already defaults both timeouts to five
seconds, so stating them changes nothing today. They are stated because older
redis-py defaulted both to None, and a pin bump should not quietly decide how
long a committed trade can wait.

**Rejected.** *Catching the `ValueError` and logging it*, which the comments
beside `from_url` and on `config.py::redis_url` promised, by lumping both cases
together as "the ledger will not refuse to boot over Redis". It turns a loud
error into a silent one that lasts forever. *Validating the URL in
`core/config.py` as well*: `from_url` is the parser that has to accept it, and
a second rule could only disagree with that one.

**Reversal trigger.** Revisit if the ledger ever learns `REDIS_URL` from
something other than its own environment at boot — a config service, a
runtime reload, a value an operator can change without a restart. A bad value
would then arrive mid-life, where refusing to boot is not an option, and the
choice becomes "catch and alert" rather than "fail at boot". The test is
whether a new value can reach `from_url` without a process restart.

**Notes.** `test_a_redis_url_that_does_not_parse_stops_the_ledger_booting`
holds the boot failure, and
`test_a_redis_that_never_answers_costs_a_bounded_wait_and_no_exception` holds
the timeout. The CI boot check calls `create_app()` and does not run the
lifespan, so a malformed URL passes CI and fails at `uvicorn` startup. That is
fine: the deploy is the thing it should fail.

---

### D-052 — The price read exists once, and the price quantizer is in `core/pricing.py`

**Date:** 2026-09-23 · **Ticket:** #112 (review of #110) · **Status:** active

**Decision.** The joined `market_books`/`market_outcomes` read, its cold path
through `books.ensure_open` and the zip back onto outcome ids live in
`service/book_prices.py`, which the preview and the snapshot both call.
Rounding an outcome's price to the wire is `core/pricing.py::quantize_price`,
`ROUND_HALF_UP` at scale 4. A book that reads empty after the cold path raises
`MarketBookIncomplete` (500).

**Why.** Both docs pages promise a client that the preview's `prices` and the
snapshot's `prices` are the same strings for the same state. The snapshot
shipped with its own copy of the read, the quantizer and the dataclass, so the
promise held only while two copies stayed in step. With one copy it holds by
construction. The quantizer goes in `core/` rather than beside the read
because [T-2] #22 has to round the prices it publishes in a `PriceEvent`, and
a rounding rule that only a service module held would be one #22 copies or
forgets. It sits beside `quantize_cost`, which is in `core/` for the same
reason.

**Rejected.** *Keeping two copies and a test that they agree* — the test would
only catch drift after somebody had written it. *Moving the read into
`service/books.py`* — that module is the handoff, and its fast path returns a
`MarketBook` entity, not priced rows. Merging them would put a pricing read
inside the write path's module for no caller that needs both.
*`MarketTermsUnavailable` for an empty book* — 503 blames market_service and
invites a retry, when the fault is this service's own data.

**Reversal trigger.** Split the read again if the preview and the snapshot
ever need different snapshots of the book: different columns under different
isolation, or one of them taking a lock. The test is whether one statement can
still serve both callers without either reading something it does not use to
decide its answer.

**Notes.** `preview.py`'s `average_price` rounded inline with `ROUND_HALF_UP`
when this was written, and folding it into `quantize_price` was left until
#108 had landed, because #108 rewrites those lines. #108 has landed and the
review of #110 did the fold: `average_price` goes through `quantize_price`
like every other price this service publishes. It is still a price per share
derived from a charged total rather than an outcome's price, which is why it
was ever a question.

### D-NEW — A caller holding pending writes must establish under its own lock that the idempotency key is absent

**Date:** 2026-09-22 · **Ticket:** #22 · **Status:** active

**Decision.** Any caller that still has unwritten work in the session when it
calls `posting.post` must have looked the idempotency key up **under a lock
that serialises every other request carrying that key**, and found nothing,
before it made any of those writes. `post`'s own lookup does not discharge
this obligation.

**Why.** `post`'s replay branch calls `session.commit()`. That commit flushes
the whole session, so a caller with pending writes has them committed
alongside a transaction that wrote no entries. On the trade path that is the
duplicate's `q`, `state_version` and position writes committing while the
money moves once. Measured against the test database: a duplicated buy of 10
shares leaves `q = 20.0000` and `state_version = 2` against one payment of 50
credits. The ledger still sums to zero and every invariant
`test_concurrency.py` asserts still holds, because none of them are about `q`.

"The book's writes share `posting.post`'s commit, and nothing may follow it"
settled the other half of this — nothing may come *after* `post` — and its
Notes flagged that [T-2] #22's shape was undecided. This is that decision. The
two together are the whole contract for sharing `post`'s commit: nothing after
it, and the key proven absent under your own lock before anything before it.

**Why a lock of the caller's own, rather than `post`'s.** `post` takes account
locks, which serialise two requests only when their legs share an account. The
trade path's book-row lock serialises two requests only when they name the same
market. Neither alone covers one key sent twice against two markets by one
user: different book rows, so no queue at the book; the shared USER account
queues them inside `post`, where the loser takes the replay branch and commits
its writes for the other market. What closes it is the key derivation in "The
trade's idempotency key is derived by the server; the client's value is one
component of it" — with the market id inside the stored key, one key names one
market, and the book lock *is* the lock this entry requires.

**Rejected.** Doing the writes after `post` returns: it puts them outside the
transaction that commits the entries, which is exactly what [T-2] #22's second
acceptance criterion forbids. Flushing them first — a flush is not a commit, so
they still land on `post`'s.

**Notes.** There is no in-process safety net here and there cannot be: by the
time `post` could notice, it has already committed. The ordering *is* the
safety, the same way it is for the entry this one extends. "`posting.post`
refuses to replay into a dirty session" is the alarm, not the net.

---

### D-NEW — `posting.post` refuses to replay into a dirty session

**Date:** 2026-09-22 · **Ticket:** #22 · **Status:** active

**Decision.** Both of `posting.post`'s replay branches — the lookup under the
account locks, and the second lookup after an `IntegrityError` on the key —
raise `PendingWritesOnReplay` instead of returning when the caller entered
`post` with `session.new or session.dirty or session.deleted` non-empty. Its
own commit, ahead of the trade path, and Ernest is told before it lands —
`posting.py` is his.

**Why.** The entry above is a discipline, and a discipline that fails silently
and corrupts `q` for ever is worth an assertion. This turns "a caller got the
ordering wrong" from shares granted twice into a refused request and a
traceback. It cannot be a *net* — `post` reaches this point with the caller's
writes already pending and no way to discard only those — so it is an alarm
that fires before the damage rather than a rollback after it.

**Verified against both existing callers before proposing it.** Both reach the
replay branch with `(new, dirty, deleted) = (0, 0, 0)`, so neither changes
behaviour:

- `grants.ensure_granted` — `accounts.ensure` flushes inside its own SAVEPOINT,
  so the account is persistent rather than pending by the time `post` is
  called, and the function adds nothing else.
- `books.ensure_open` on a lost first-touch race (D-010) — the book and its
  outcomes were added inside `session.begin_nested()`, and the `IntegrityError`
  rolls that SAVEPOINT back, which expunges them. The pool account it carries
  forward is the winner's committed row, re-read. A warm second touch returns
  before `post` is reached at all.

Confirmed by instrumenting `_replay` and driving all three paths, the race one
barrier-synchronised per ADR 0015.

**Rejected.** Raising at the top of `post` rather than on the replay branches:
it would forbid the shape "The book's writes share `posting.post`'s commit,
and nothing may follow it" depends on, which is every caller this service has.
Not raising at all and documenting the rule instead — the rule was already
documented, and the trade path is the first caller that can break it.

**Notes.** The check is on SQLAlchemy's unit of work, so it catches pending ORM
changes and not a write already flushed to the connection. That is the right
scope for the bug it exists to catch — the trade path's writes are attribute
assignments on loaded rows and are still in `session.dirty` when `post` runs,
because `autoflush=False` and `accounts.lock` issues only SELECTs — but it is a
detection, not a proof. A future caller that flushes before calling `post` sits
outside it, and the entry above is still the rule that protects that caller.

*Amended 2026-09-27 in #139: a flushing caller no longer sits outside it.* A
flushed row leaves `session.new` and a replay's commit still lands it, and
`accounts.ensure` — which every caller uses — flushes inside a SAVEPOINT it
releases. `core/database.py::has_pending_writes` now also reports a
`session.info` flag that an `after_flush` listener sets and the end of the
outermost transaction clears. It still sees ORM writes only, not a Core
`insert()` executed directly, so it remains a detection and the entry above is
still the rule. It errs toward True: a flush rolled back to a SAVEPOINT counts
until the transaction ends, which fails closed where this is the alarm. In the
ledger that rollback follows only a *failed* flush (`accounts.ensure`,
`books.ensure_open`, `posting.post`), and a failed flush sets nothing.

That reopens the first bullet above, whose "persistent rather than pending" is
now a flush the check counts. It still holds, for a different reason: two first
reads racing on one new account cannot both flush it. The second INSERT waits
on the first's uncommitted row and fails once that commits, so the request that
flushed is always the one that writes, and the one that replays flushed
nothing. The pool account on a lost first-touch race is the same shape. The
tests under "Neither existing caller changes behaviour" still pass unchanged.
`books.ensure_open`'s entry assertion shares the function, so it now refuses a
caller that flushed first too — which its rollback would otherwise discard.

Whether the caller had pending work is recorded on entry to `post`, not asked at
each branch. The second branch is reached after the SAVEPOINT has flushed the
caller's writes and rolled them back, which expires them, so the session looks
clean there whatever the caller was holding. Nothing is committed on that
branch, but the caller would still get somebody else's transaction back as
though its own had landed, holding expired entities. No trade reaches it today
— the book lock queues two trades on one key before either gets to `post` —
and `test_a_replay_found_by_the_insert_race_is_refused_too` drives it by hiding
the first lookup.

*Amended 2026-09-27 in #139, correcting the paragraph above.* Two of its claims
are wrong, checked by driving the second branch with a caller's `q = 41`
pending. `begin_nested()` flushes the caller's pending writes *before* it
issues the SAVEPOINT, so rolling the SAVEPOINT back after the failed INSERT
does not undo them. The caller's `q` is still in the outer transaction when the
second lookup runs, and the rollback only expires the objects. Without the
refusal, the commit at the end of `post` would land the caller's writes beside
another request's transaction — the first branch's harm, not only a
misreported result — which is what makes the refusal there matter. And the
session looks clean there only to `new`, `dirty` and `deleted`:
`has_pending_writes` now reports True, because that flush set its flag.
`caller_pending` is still taken on entry anyway. An answer taken before
anything runs does not depend on the flag, or on what the SAVEPOINT did to the
session.

`PendingWritesOnReplay` is a `LedgerError` at 500 rather than a bare exception,
so the response keeps the one error envelope `controller/errors.py` exists to
preserve. 500 and not 409: nothing the client sent is wrong and nothing it can
do differently helps. It is this service reporting a bug in itself.

*Amended 2026-10-13 by #315: `post` is now `post_all` of one movement, so
both replay branches above are `post_all`'s, and the refusal is
`_replay_batch`'s first check. The rule, and the answer recorded on entry,
are unchanged. See "`post` is `post_all` of one movement".*

---

### D-NEW — The trade's idempotency key is derived by the server; the client's value is one component of it

**Date:** 2026-09-22 · **Ticket:** #22 · **Status:** active

**Decision.** The trade route stores `trade:<user_id>:<market_id>:<client
key>`. The client's string is never the stored key. The unlocked replay lookup,
the under-lock re-check and `post` all use the derived form.

**Why.** Two reasons, either sufficient.

`transactions.idempotency_key` is unique across the whole ledger and every
namespace in it is server-generated. A client that writes an arbitrary key can
submit a trade keyed `signup-grant:<another user's id>`; that user's first
balance read then finds the key present, takes `ensure_granted`'s early return,
and never receives their starting credits — silently, permanently, and in a
table nothing rewrites. The same trick replays another user's trade body back
to whoever guesses their key.

And the market id inside the key is what makes the book-row lock the right lock
for "A caller holding pending writes must establish under its own lock that the
idempotency key is absent". Without it, one key can name two markets and the
book lock serialises nothing.

**Rejected.** A `CHECK` or a regex refusing reserved prefixes: it enumerates
today's namespaces and silently stops covering tomorrow's. Keying on the user
alone, which leaves the two-market hole open. A separate per-caller key table,
which is a second source of truth for a uniqueness the column already has.

**Notes.** A client that reuses its own key for a genuinely different trade in
the same market still gets `IdempotencyKeyReused`, unchanged — the fingerprint
does that work and this entry does not touch it. The derivation is documented
on the route, so a client knows its key only has to be unique *to itself*,
which is a weaker and more honest requirement than global uniqueness.

---

### D-NEW — The trade's staleness check is strict `state_version` equality, and the field is required

**Date:** 2026-09-22 · **Ticket:** #22 · **Status:** active

**Decision.** The trade body carries the `state_version` the preview returned.
Under the book lock the trade re-reads it and refuses `409 quote_stale` unless
it is exactly equal. The field is required, and the error carries `quoted` and
`current` in `error.details`.

**Why.** "The quote reference is `state_version`, not a separate counter"
already decided the mechanism — "#22 re-reads it under lock and rejects on
mismatch". #22's criterion says "price moved beyond quoted preview", which is
looser prose written before that entry existed and does not reopen it. A
tolerance needs a number, and nobody has one; picking a tick count here would
be a product decision taken by a backend slice.

Required rather than optional, because an optional staleness field means a
client that omits it silently opts out of the only staleness protection the
trade has.

**Rejected.** A tolerance in ticks or in price. Same objection ADR 0017 makes
to caching a status: the number becomes a correctness parameter nobody can
derive, and it is wrong in exactly the market that moves fastest.

**Reversal trigger.** If [FE][T-2/T-3] #49 measures a material share of trades
refused `quote_stale` under ordinary use, the successor is **not** a widened
comparison but a `max_cost` bound on a buy and a `min_proceeds` bound on a
sell, checked under the same lock. That is a tolerance denominated in money and
supplied by the trader, so it needs no number chosen here, and it refuses
exactly the trades that actually got worse rather than every trade that
happened to follow another one.

*Amended 2026-10-17. Decided by ADR 0024 (Accepted 2026-10-17), not built yet.*
This rule covers trades, not limit-order placements. A placement takes no
`state_version`: its limit is what protects the trader, and a quoted version
would refuse an order because the price moved, which is what a limit order
exists to tolerate. The trade route keeps the rule unchanged. See "A
placement takes no `state_version`" in ADR 0024.

---

### D-NEW — `ledger.positions` stores quantity and cost basis, keyed on the triple

**Date:** 2026-09-22 · **Ticket:** #22 · **Status:** active

**Decision.** `ledger.positions`, primary key `(user_id, market_id,
outcome_id)`, columns `quantity` and `cost_basis` at `Numeric(18, 4)`, plus
`created_at` and `updated_at`. `(market_id, outcome_id)` is a composite foreign
key to `market_outcomes`; `user_id` is bare. `CHECK (quantity >= 0)` and
`CHECK (cost_basis >= 0)`.

**Why.** ADR 0009 hands the columns to this ticket. `cost_basis` stored and the
average entry price derived is "`cost_basis` is stored; average entry price is
derived", unchanged. The composite primary key rather than a surrogate id is
"`MarketOutcome`'s primary key is the pair, not a surrogate id", same argument:
nothing references a position by an identity of its own, and [T-4] #24 reaches
them by the triple.

The foreign key follows "`pool_account_id` is a foreign key; `market_id` still
is not". `(market_id, outcome_id)` names a row in `ledger.market_outcomes` —
this service's own table, that exact pair being its primary key — so it gets a
constraint. `user_id` names a row in `auth.users`, which `ledger_svc` holds no
grant on, so it stays bare under ADR 0003.

`quantity >= 0` is the no-shorting rule per user, the counterpart to
`InsufficientSharesOutstanding`'s per-outcome one. [T-3] #23 enforces it under
the book lock; the CHECK is the backstop, not the check.

**Notes.** This table is **not** append-only and must not grow the trigger
`ledger.entries` carries. It is a rollup that gets UPDATEd, reconstructible
from the entries, and the entries remain the record. Worth saying in the
docstring, because it will sit next to `Entry`.

A new *table* reaches the long-lived `cs464` through `create_all` at startup,
which issues `CREATE TABLE IF NOT EXISTS` — the trap CLAUDE.md documents is a
new *column* on a table that already exists. So this needs no file in
`sql/migrations/`, and neither does `TransactionKind.TRADE_BUY`, which is a
non-native enum member. Verify rather than trust it, with CLAUDE.md's
`pg_constraint` query pointed at `ledger.positions`.

What `cost_basis` does on a **partial sell** is left to [T-3] #23. #22 only
ever adds to a position, so it cannot settle a rule it never exercises.

The scale of 4 on `quantity` is settled by "Shares keep money's scale of 4, in
`q` and in positions".

---

### D-NEW — The trade response is reconstructed from `Transaction.context`, and carries no prices

**Date:** 2026-09-22 · **Ticket:** #22 · **Status:** active

**Decision.** The trade writes `user_id`, `market_id`, `outcome_id`, `side`,
`quantity`, `total` and the post-trade `state_version` into
`Transaction.context`. One function builds the response from a `Transaction`,
and the fresh path and both replay paths all call it. The response carries no
prices.

**Why.** A replay has to return the same body as the original, and `context` is
the only thing about a trade that is durable, written inside `post`'s commit,
and cannot drift. The column's own docstring names this use — "the market and
outcome of a trade". Building the body in one function is "The preview endpoint
lives on the ledger as a read"'s argument at a smaller scale: two builders
would eventually disagree, and the case where they disagree is the retry.

**Why no prices.** They are not derivable from a stored trade, and storing them
would be storing a lie with a timestamp. A replay an hour later would hand back
prices that were true once, and unlike `total` — which really is what the
trader was charged, for ever — a stale price has no correct reading. Prices are
the realtime contract's: the client renders the `price` frame, or fetches the
snapshot, which is the recovery path ADR 0010 already requires it to have.

**Rejected.** `balance_after`, for the same reason and because the frontend
re-reads the balance anyway. A separate `trades` table to hold the response — a
second source of truth for something `context` already holds inside the right
commit.

*Amended 2026-10-17. Decided by ADR 0024 (Accepted 2026-10-17), not built yet.*
`context` gains an eighth key, `book_state_version`, the book's counter after
the trade and its cascade, and the response returns it. A fill's `context` also
carries `order_id`. The response still carries no prices. See "The trade
response carries `book_state_version`, and still no prices".

---

### D-NEW — The trade path is side-generic in shape and buy-only at the route

**Date:** 2026-09-22 · **Ticket:** #22 · **Status:** superseded by "The trade route accepts both sides"

**Decision.** The trade service function takes `side: Side` and prices,
quantizes, signs its legs and refuses a sub-tick result through the helpers
that already take one. The route accepts `buy` only. [T-3] #23 widens it.

**Why.** `core/pricing.py` was written for both sides on purpose — its module
docstring says "`service/preview.py` today, [T-2] #22's trade path tomorrow",
and `quantize_cost` refuses a zero result itself, rather than leaving it to a
separate function, because "[T-2] #22's write path has to make the same
refusal, and a separate function is one a caller can forget to call". Writing a buy-shaped
path and having #23 generalise it would mean #23 rewriting the money path,
which is a second opinion about what a trade is.

The route stays buy-only because a sell is unsafe without #23's work: the
per-user holdings check has to be read under the book lock, and there are no
positions to check against until #22 has written some. A sell route that
skipped it is a route for selling shares you do not hold.

**Notes.** #23 is then the holdings read under the lock, the position
decrement, `cost_basis` on a partial sell, widening the route's `side`, and the
tests. Say this in #22's pull request, because a reviewer will see `side`
threaded through a function only ever called with `BUY` and reasonably ask why.

---

### D-NEW — Shares keep money's scale of 4, in `q` and in positions

**Date:** 2026-09-22 · **Ticket:** #22 · **Status:** active

**Decision.** Share quantities carry money's scale of 4 inside the service as
well as at its boundary. `market_outcomes.q` and `positions.quantity` are
`Numeric(18, 4)` because that is the right scale, not because it was the
nearest one to hand. Fractional shares exist, down to `0.0001`.

**Why.** A quantity arrives at scale 4 — "A quantity takes money's scale of 4
at the API boundary" refuses a fifth decimal place rather than rounding it —
and the only arithmetic the write path performs on it is addition into `q` and
into a position. Addition of two values at scale 4 is exact at scale 4. So the
quantity a trader was quoted for is the quantity that is written, with no
rounding anywhere between the quote and the row.

Any other scale breaks that. A coarser one — whole shares — has to round a
quantity the trader typed and the preview already priced, which puts a
rounding step on the one path this service has spent four entries keeping free
of them. A finer one buys nothing, because the input cannot be finer than
scale 4, and it is not free: `core/lmsr.py`'s `_PRECISION = 50` is derived
from `Numeric(18, 4)` and its comment says "Widen `Numeric`'s scale and this
number has to be revisited with it."

**Rejected.** Whole shares with an integer `q`. It is the more familiar model
and it is a product decision, not a storage one — and taking it here would
mean this entry deciding, from the inside of the ledger, that a trader may not
buy half a share. If that is wanted, it is decided where quantities are typed
and this entry reverses.

**Reversal trigger.** A product decision that a trader may buy whole shares
only. That is testable as a ticket saying so, not as an opinion about
tidiness, and it reverses this entry rather than qualifying it: the scale
follows the input, so the day the input is integral the column should be too.

**Notes.** This closes the Open question "Whether share quantities share
money's scale of 4", which "A quantity takes money's scale of 4 at the API
boundary" had settled only at the boundary. [F-7] #96 wrote nothing but zero
into `q` and borrowed the scale explicitly without deciding it; [T-2] #22 is
the first ticket to write a non-zero one, which is where the borrowing had to
stop.

---

### D-NEW — A replay hit is compared against the request before it is returned

**Date:** 2026-09-22 · **Ticket:** #22 · **Status:** active

**Decision.** A trade whose derived idempotency key names an existing
transaction is returned only if the request matches the stored one.
`outcome_id`, `side` and `quantity` are read out of `Transaction.context` and
compared with what was sent; a mismatch is `IdempotencyKeyReused` (409,
`idempotency_key_reused`). `quantity` is compared **numerically**, so `10` and
`10.0000` are one trade. `state_version` is **not** compared.

The comparison is one helper, called from **both** lookups — the unlocked
pre-gate replay and the under-lock re-check. Two copies of a rule about
whether two trades are the same trade is two opinions about it, and the case
where they disagree is the retry.

**Why.** Without it the rule "a caller that reuses a key for different money
is told so" — `posting._replay`'s own docstring — does not hold on this route,
for two independent reasons.

The pre-gate replay returns *before* `posting.post` is ever called, so
`post`'s fingerprint check never runs on a retry that finds a hit. ADR 0017
put that lookup first on purpose and was right to; the consequence nobody had
followed through is that it also short-circuits the only comparison the write
path had. `idempotency_key_reused` is listed on this route's criteria as
inherited, and as specified it was unreachable.

And the fingerprint could not do the work even where it runs.
`posting._fingerprint` hashes the kind and `account_id:amount` per leg. On
this route both legs' accounts are fixed by `(user_id, market_id)`, which are
already inside the derived key, so the fingerprint sees nothing but the
quantized total. It cannot see `outcome_id` **at all**: a retry naming the
other outcome of a binary market, at a quantity whose cost quantizes to the
same total, hashes identically and replays clean — the trader is handed a
position in YES as proof that their NO trade succeeded. Two adjacent
quantities in a deep book quantize to one total routinely, so the quantity
case is not exotic either.

`context` is what makes the check affordable, and this is the first ticket
that has it. The lookup has already loaded the `Transaction`; the three
fields are on it; the comparison costs no statement. Before [T-2] #22 there
was nothing durable to compare against, which is why "The trade's idempotency
key is derived by the server; the client's value is one component of it"
could say the fingerprint did this work and be right about every caller that
existed.

**Why `quantity` numerically.** "A quantity takes money's scale of 4 at the
API boundary" echoes the quantity back exactly as it arrived, trailing zeros
and all, and `context` stores what was sent. A string comparison would refuse
a client that retried `10.0000` after sending `10` — the same trade, typed
twice — with an error telling them to generate a new key. The rule is about
what the trade *is*, not about how it was spelled.

**Why `state_version` is excluded.** A client that lost its response is
expected to re-preview before retrying, and the market may have moved in
between. The version it now quotes is newer and the trade is still the same
trade: the original's quote was valid at the instant it executed, and that
instant is over. Comparing it would refuse exactly the retry this whole
ordering exists to serve. It also cannot be a staleness check in disguise —
staleness is decided under the book lock against a trade that is about to
execute, and a replay executes nothing.

**Rejected.** *Returning the stored trade blind.* Honest to "a retry only asks
what its answer was", and it answers a different question than the one asked,
with a `201` and somebody else's body. *Leaving it to `post`'s fingerprint.*
It does not run on the pre-gate path and is blind to `outcome_id` where it
does. *Widening the fingerprint to cover `context`.* It is `posting.py`'s,
which is Ernest's, and it would change the meaning of a hash two shipped
callers already depend on to answer a question only this caller is asking.

**Reversal trigger.** This narrows the day `Transaction.context` stops being
the trade's durable description — if a later ticket moves any of
`outcome_id`, `side` or `quantity` out of it, the comparison has to follow the
data rather than be quietly dropped. It widens the day a second write route
reaches `post` through a pre-`post` replay lookup of its own: at that point
the helper is a rule about replays rather than a rule about trades, and it
belongs beside `find_by_idempotency_key` rather than in the trade path.

**Notes.** This does not touch `posting.post`. A caller that reaches `post`
with a genuinely different movement under one key still gets
`IdempotencyKeyReused` from the fingerprint, unchanged; this check fires
earlier and on dimensions the fingerprint cannot see. Both can raise the same
error because a client's remedy is identical either way: generate a new key.

Two concurrent requests carrying one client key and different quantities is
the case worth testing rather than reasoning about — one of them executes and
the other must be refused, at whichever of the two lookups sees it. That it is
the *same* helper at both is what makes the answer independent of which one
wins.

---

### D-NEW — `transactions.idempotency_key` widens from `varchar(120)` to `varchar(255)`

**Date:** 2026-09-22 · **Ticket:** #22 · **Status:** active

**Decision.** `model/entities.py::Transaction.idempotency_key` is
`String(255)`, not the original `String(120)`. `TradeIn.idempotency_key` is
bounded `max_length=175` at the API layer, so the derived key can never
exceed the wider column regardless of what a client sends.
`sql/migrations/0007-ledger-trade-idempotency-key-width.sql` carries the
`ALTER COLUMN ... TYPE` for `cs464`.

**Why.** "The trade's idempotency key is derived by the server" fixed the
format as `trade:<user_id>:<market_id>:<client key>` — 80 characters of
prefix before the client's own string starts. 120 was sized for this
service's own two namespaced keys (`signup-grant:<user_id>`,
`market-open:<market_id>`, 49 and 48 characters) and never budgeted for a
second namespace wrapped around an arbitrary client string.
`test_a_client_key_naming_the_grant_namespace_cannot_touch_it` drives exactly
that: a client key that is itself `signup-grant:<uuid>` (49 characters),
which is the scenario the derivation exists to make safe. `80 + 49 = 129`
overflows 120 and the insert fails `StringDataRightTruncationError` before
the derivation's own guarantee is ever reached.

**Rejected.** Bounding `idempotency_key` more tightly at the API layer
instead of widening the column — considered, and it does not remove the
need to widen: the column has to hold `80 + max_length` regardless of where
the ceiling is enforced, and refusing a legitimately-sized client key with a
422 because this service's own prefix is long is a cost paid by every
caller for a column nobody had a reason to keep narrow.

**Notes.** This is a widen on a column already present on `cs464`, so
`create_all` cannot reach it — the same shape as a new column, but even a
new migration file's `ADD COLUMN IF NOT EXISTS` idiom does not apply to an
`ALTER COLUMN ... TYPE`. `unit_test/conftest.py`'s drop-and-recreate picks
it up automatically, which is why this went unnoticed until a test drove
the specific 49-character client key.

---

### D-NEW — An oversized `limit` is clamped, not refused

**Date:** 2026-09-27 · **Ticket:** #15, #13 · **Status:** active

**Decision.** On the audit feed (`GET /audit/actions`, #15) and the admin user
list (`GET /admin/users`, #13), a `limit` above `max_page_size` is lowered to
the ceiling. The request still succeeds.

**Why.** A caller that asks for more than the ceiling wants as much as it can
get. A 422 on `limit=1000` is a worse answer than the 200 rows the server is
willing to serve. The ledger history does the same.

**Rejected.** Refusing an oversized `limit` with a 422.

**Notes.** Recorded in #127 from the comments at
`audit_service/controller/routes.py` and `auth_service/controller/admin_routes.py`,
which the refactor shortened.

---

### D-NEW — The audit feed and the user list page by keyset, not OFFSET

**Date:** 2026-09-27 · **Ticket:** #15, #13 · **Status:** active

**Decision.** Both lists are read newest first, and a page is asked for with a
cursor that names a position: "everything strictly older than
(`occurred_at`, `id`)" for the audit feed, and (`created_at`, `id`) for the
user list. Neither uses OFFSET.

**Why.** Both lists only grow at the newest end, which is the end being read
from. With OFFSET, a row added between page 1 and page 2 pushes the window
down: the last row of page 1 comes back as the first row of page 2, and the
row behind it is never shown. In the audit log that means one record shown
twice and another skipped. In the user list, the admin paging through to find
one account is exactly the reader who would never notice. A cursor stays put
whatever is added. For the audit log it is also the query the indexes in
`sql/02-schemas.sql` are built for: jump to the cursor and read on, rather than
counting and throwing away OFFSET rows on every page.

`auth.users` is not append-only (roles and suspensions change), but that does
not matter. A row only appears by registration, and neither `created_at` nor
`id` is ever written twice, so an update cannot move a row in this order.

The id is in the cursor because the timestamp is not unique. Two rows can land
in the same microsecond, and two users registered in one transaction share
`now()` exactly. Ordered by the timestamp alone, Postgres may return tied rows
in a different order between queries, and a cursor cannot survive that.

**Rejected.** OFFSET paging.

**Notes.** The cursor format lives in `shared/paging.py` (ADR 0012); each
service's `core/paging.py` only turns a bad one into its own error. Recorded in
#127 from the comments at `audit_service/core/paging.py` and
`auth_service/core/paging.py`, which the refactor shortened.

---

### D-NEW — Every kind of bad cursor gets the same error

**Date:** 2026-09-27 · **Ticket:** #15, #13 · **Status:** active

**Decision.** In the audit and auth services, `core/paging.decode_cursor`
turns every way a cursor can be broken — bad base64, a missing separator, a
UUID that will not parse — into one `MalformedCursor` (400
`malformed_cursor`).

**Why.** A client cannot do anything different about any of them. Describing
the failure precisely only helps someone probing what the value is made of.

**Rejected.** A separate error for each way a cursor can be broken.

**Notes.** Recorded in #127 from the comments at `audit_service/core/paging.py`
and `auth_service/core/paging.py`, which the refactor shortened.

---

### D-NEW — A cursor the service did not issue is refused, not treated as page one

**Date:** 2026-09-27 · **Ticket:** #15, #13 · **Status:** active

**Decision.** A cursor that did not come from a previous response is a 400
`malformed_cursor`, with the message "Pass back the `next_cursor` from the
previous response, unmodified."

**Why.** What is inside a cursor is the service's business, so a client should
only ever send back what `next_cursor` gave it. Saying so plainly beats
quietly starting again from the newest page, which would loop forever.

**Rejected.** Ignoring a bad cursor and starting again from the newest page.

**Notes.** The auth service uses the same code and message as the audit and
ledger services on purpose. It is the same cursor format, and the frontend
should not have to learn three spellings of one mistake. Recorded in #127 from
the comments at `audit_service/core/errors.py` and `auth_service/core/errors.py`,
which the refactor shortened.

---

### D-NEW — The writing service makes the audit entry's id, not the database

**Date:** 2026-09-27 · **Ticket:** #15, #16 · **Status:** active

**Decision.** `audit.admin_actions.id` is a `uuid4()` made in the writing
service's own code, in `service/audit.py::record`. There is no server-side
default for it.

**Why.** A writing service holds INSERT on the audit table and no SELECT
(ADR 0006). A server-side default would have to be read back with RETURNING,
and RETURNING is a read: it needs SELECT. That grant must not be given, because
SELECT is what would let one service read another service's actions. Making the
id in Python lets a writer insert without it. It is also why `record` returns
nothing: the service cannot read back the row it just wrote.

**Rejected.** A server-side default on the id column.

**Notes.** `market_service` writes the same way, and its `model/audit.py` gives
the same reason. Recorded in #127 from the comments at
`audit_service/model/entities.py` and `auth_service/service/audit.py`, which
the refactor shortened.

---

### D-NEW — The audit service checks tokens with dependencies, not middleware

**Date:** 2026-09-27 · **Ticket:** #1, reused in #15 · **Status:** active

**Decision.** The audit service checks the token and the admin role with
FastAPI dependencies (`get_claims`, `require_admin`), the same shape as the
market service.

**Why.** Three reasons, first given in the market service ([1.1] #1):

- A dependency defaults to closed; middleware defaults to open. Middleware
  runs on every request, including `/health` and `/docs`, so it needs a list of
  paths to skip, and a route added later is unprotected unless someone
  remembers.
- FastAPI shows a dependency in `/docs`, so the contract says which routes
  need a token. Michelle codes against that page.
- It hands the route typed claims. Middleware would have to pass them through
  `request.state`, untyped.

**Rejected.** Middleware.

**Notes.** The full version of this reasoning is the docstring of
`market_service/controller/dependencies.py`. Recorded in #127 from the comment
at `audit_service/controller/dependencies.py`, which the refactor shortened.

---

### D-NEW — The audit feed's page sizes are settings, not constants

**Date:** 2026-09-27 · **Ticket:** #15 · **Status:** active

**Decision.** `default_page_size` (50) and `max_page_size` (200) live in
`core/config.py` as settings.

**Why.** An audit log only grows, so an unbounded read gets slower every week
it is asked; the route needs a ceiling. They are settings only because the
right page size depends on what the admin console shows, which is Michelle's
call, and it may differ between a laptop and a deploy.

**Rejected.** Hard-coded constants.

**Notes.** The auth service's user list (#13) has the same two settings with the
same defaults, for the same reason. Recorded in #127 from the comment at
`audit_service/core/config.py`, which the refactor shortened.

---

### D-NEW — The audit feed matches an action type exactly, not by prefix

**Date:** 2026-09-27 · **Ticket:** #15 · **Status:** active

**Decision.** `?action_type=` filters with `=`, not with a prefix match.

**Why.** `market.submitted` and `market.submitted.reverted` are different
actions. A prefix match would quietly fold a future action type into an
existing filter.

**Rejected.** Prefix matching.

**Notes.** Recorded in #127 from the comment at
`audit_service/service/audit_service.py`, which the refactor shortened.

---

### D-NEW — A cursor is decoded before the query runs

**Date:** 2026-09-27 · **Ticket:** #15, #13 · **Status:** active

**Decision.** `list_actions` (audit) and `search_users` (auth) decode the
cursor before building the query. A bad one raises `MalformedCursor`, which the
controller turns into a 400.

**Why.** A bad value that reached the database would come back as a driver
error, and that becomes a 500.

**Rejected.** Passing the cursor into the query and letting the database
reject it.

**Notes.** Recorded in #127 from the comments at
`audit_service/service/audit_service.py` and `auth_service/service/user_admin.py`,
which the refactor shortened.

---

### D-NEW — A non-admin reading the audit log gets 403, not 401

**Date:** 2026-09-27 · **Ticket:** #1, reused in #15 · **Status:** active

**Decision.** A caller with a valid token but no admin role gets 403
`not_an_administrator`. 401 is kept for a token that is missing, malformed or
expired.

**Why.** Reading the log is an admin's privilege: it records what admins did
to other people's accounts and markets. The two codes mean different things to
the frontend, as in the market service ([1.1] #1). 401 means the session is
gone, and logging in again helps. 403 means the session is fine and this
account will never be let in, so retrying cannot help.

**Rejected.** Answering both cases with 401.

**Notes.** Recorded in #127 from the comment at `audit_service/core/errors.py`,
which the refactor shortened.

---

### D-NEW — The username is trimmed in a before-validator

**Date:** 2026-09-27 · **Ticket:** #89 · **Status:** active

**Decision.** `RegisterRequest._trim_username` runs with `mode="before"`, so the
username is trimmed before `Field` checks its length.

**Why.** An after-validator only runs once `min_length` and `max_length` have
already passed. So `" ab"` was accepted as three characters and stored as two.

**Rejected.** An after-validator, which was the bug. `str_strip_whitespace` on
the whole model, which would trim the password too.

**Notes.** Recorded in #127 from the comment at `auth_service/model/schemas.py`,
which the refactor shortened.

---

### D-NEW — Login checks a dummy hash when the account does not exist

**Date:** 2026-09-27 · **Ticket:** #30 · **Status:** active

**Decision.** When no account matches, `authenticate` calls
`security.dummy_verify()`, which checks the password against a throwaway
Argon2 hash, before refusing with `InvalidCredentials`.

**Why.** Checking a throwaway hash takes as long as checking a real one. So the
response time does not reveal whether the account exists, just as the error
message does not.

**Rejected.** Refusing an unknown account straight away, which would answer
faster and so confirm that the account does not exist.

**Notes.** Recorded in #127 from the comment at `auth_service/core/security.py`,
which the refactor shortened.

---

### D-NEW — Suspension is checked only after the password is proven

**Date:** 2026-09-27 · **Ticket:** #30 · **Status:** active

**Decision.** `authenticate` checks `is_suspended` only after the password has
been verified.

**Why.** A suspended account gets its own message (403 `account_suspended`).
Checked first, that message would let an attacker find out which accounts are
suspended without knowing any password.

**Rejected.** Checking suspension before the password.

**Notes.** Recorded in #127 from the comment at
`auth_service/service/auth_service.py`, which the refactor shortened.

---

### D-NEW — A lost registration race names the field by looking again, not from the constraint name

**Date:** 2026-09-27 · **Ticket:** #29 · **Status:** active

**Decision.** When two people register the same username or email at once, the
loser's flush fails with `IntegrityError`. `register` then rolls back and runs
the `_taken_fields` lookup again to say which field clashed.

**Why.** The winner has committed by then, so the lookup that came back clear a
moment ago can now name the field. The database error carries the constraint
name too, but reading it would tie this code to asyncpg and to the index names
in `model/entities.py`. The answer can still be empty if the winning account
was deleted in between; `DuplicateUser` then gives its generic message.

**Rejected.** Reading the field from the constraint name in the error.

**Notes.** Recorded in #127 from the comment at
`auth_service/service/auth_service.py`, which the refactor shortened.

---

### D-NEW — The user search escapes LIKE's wildcards

**Date:** 2026-09-27 · **Ticket:** #13 · **Status:** active

**Decision.** Before the search text goes into `ilike`, `\`, `%` and `_` are
escaped, so it matches as the literal text the admin typed. The backslash is
escaped first.

**Why.** `_` is what makes this necessary rather than tidy. Usernames may
contain `_`, and half the team has one, so an unescaped search for `ernest_t`
would also find `ernestXt`. The backslash goes first, or it would escape the
escapes. The match is a case-insensitive substring rather than an exact one,
because an admin looking into a problem usually has part of a name from a
support message, not all of it.

**Rejected.** Passing the search text to LIKE unescaped.

**Notes.** `/docs` states it too: in `q`, "`%` and `_` are not wildcards".
Recorded in #127 from the comment at `auth_service/service/user_admin.py`,
which the refactor shortened.

---

### D-NEW — The user list uses `raiseload`, not `noload`, for refresh tokens

**Date:** 2026-09-27 · **Ticket:** #13 · **Status:** active

**Decision.** The user search query adds `raiseload(User.refresh_tokens)`.

**Why.** `User.refresh_tokens` is `lazy="selectin"`. Without this, every page
of users would also load every refresh token those accounts hold: a second
query, with no limit on its rows, for data no response here contains. This is
the one place users are loaded in bulk.

`raiseload` and `noload` fail differently. `noload` would hand back an empty
list for an account that has three live sessions, which is a wrong answer that
looks like a right one. `raiseload` says plainly that the attribute was not
loaded. Nothing in this story needs the tokens, and whatever does should ask
for them in its own query.

**Rejected.** The default `selectin` load. `noload`.

**Notes.** D-027 met the same `noload` problem in `market_service`. Recorded in
#127 from the comment at `auth_service/service/user_admin.py`, which the
refactor shortened.

---

### D-NEW — The user search is a sequential scan, with `pg_trgm` as the upgrade

**Date:** 2026-09-27 · **Ticket:** #13 · **Status:** active

**Decision.** The `%fragment%` search reads the whole table, and that cost is
accepted.

**Why.** A pattern that can match anywhere in the string cannot use the
`lower(username)` indexes, which exist for uniqueness and not for this. Only a
trigram index helps a match like that. At this project's size the whole table
is a page or two of memory.

**Rejected.** Adding a trigram index now, while it is not needed.

**Notes.** The upgrade, once the table outgrows that, is
`CREATE EXTENSION pg_trgm` and a GIN index on each column. It changes nothing
above `search_users`. Recorded in #127 from the comment at
`auth_service/service/user_admin.py`, which the refactor shortened.

---

### D-NEW — A role change reloads its locked target with `populate_existing`

**Date:** 2026-09-27 · **Ticket:** #16 · **Status:** active

**Decision.** `change_role` loads the target with
`session.get(User, target_id, with_for_update=True, populate_existing=True)`.

**Why.** So the role it reads comes from the locked row, never from a copy of
the user this session had already loaded before it held the lock. The lock is
what stops the answer changing between the question and the decision, and only
a read taken under the lock gets that protection.

**Rejected.** Locking without `populate_existing`, which can hand back the copy
the session already had.

**Notes.** ADR 0015 (PR #85, where this was added) records the wider rule: a
read that decides a write is locked, and the administrator set is locked before
the target. This entry records only the `populate_existing` part. Recorded in
#127 from the comment at `auth_service/service/user_admin.py`, which the
refactor shortened.

---

### D-NEW — `UserNotFound` says plainly that the id does not exist

**Date:** 2026-09-27 · **Ticket:** #16 · **Status:** active

**Decision.** A role change for an unknown user id is a 404 `user_not_found`,
"No user with that id."

**Why.** Unlike login, there is nothing to hide. The caller has already proven
they are an admin, and saying an id does not exist tells them nothing they
could not learn from the user list they are allowed to read.

**Rejected.** A deliberately vague error, like `InvalidCredentials` at login.

**Notes.** Recorded in #127 from the comment at `auth_service/core/errors.py`,
which the refactor shortened.

---

### D-NEW — `LastAdministrator` is a 409, not a 403

**Date:** 2026-09-27 · **Ticket:** #16 · **Status:** active

**Decision.** Refusing a demotion that would leave no administrator is a 409
`last_administrator`.

**Why.** It is a conflict, not a forbidden action. The caller is allowed to do
this and the request is well formed; it is the current state of the system
that refuses. A moment later, once somebody else is promoted, the same request
would succeed.

**Rejected.** 403.

**Notes.** ADR 0007 records how this case arises (two admins demoting each
other at the same instant) and the lock that catches it. This entry records
only the status code. Recorded in #127 from the comment at
`auth_service/core/errors.py`, which the refactor shortened.

---

### D-NEW — Logout needs no access token and always reports success

**Date:** 2026-09-27 · **Ticket:** #31 · **Status:** active

**Decision.** `POST /auth/logout` does not require authentication. It revokes
the refresh token if one is sent, clears both cookies, and always answers
"Logged out."

**Why.** Logging out must still work when the access token has already
expired. It always reports success so that a caller cannot use it to find out
which refresh tokens are live.

**Rejected.** Requiring a valid access token. Reporting an unknown or already
revoked refresh token as an error.

**Notes.** ADR 0002 records that logout works by revoking the refresh token.
Recorded in #127 from the comment at `auth_service/controller/routes.py`, which
the refactor shortened.

---

### D-NEW — The refresh cookie is scoped to `/auth`

**Date:** 2026-09-27 · **Ticket:** #31 · **Status:** active

**Decision.** The access cookie has path `/`. The refresh cookie has path
`/auth` (`refresh_cookie_path`).

**Why.** Narrower than `/`, so the long-lived refresh token is not sent along
on every API call. Wide enough to reach both `/auth/refresh` and
`/auth/logout`. Logout has to receive it, because logout is what revokes it.

**Rejected.** Path `/`, which sends it on every call. Any path narrower than
`/auth`, which would miss one of the two routes that need it.

**Notes.** Recorded in #127 from the comments at
`auth_service/controller/transport.py` and `auth_service/core/config.py`, which
the refactor shortened.

---

### D-NEW — One socket may watch at most 50 markets

**Date:** 2026-09-27 · **Ticket:** #42 · **Status:** active

**Decision.** `max_subscriptions_per_connection`, default 50.

**Why.** Every subscription costs a set entry, and a loop step on every event
sent out. A client that subscribes in a loop is the cheapest denial of service
there is against a server whose whole job is holding connections open. 50 is
generous for real use: the [X-4] #37 client watches the market on screen, and a
market list page watches the markets it has shown.

**Rejected.** No limit.

**Notes.** Recorded in #127 from the comment at `realtime_service/core/config.py`,
which the refactor shortened.

---

### D-NEW — Over the limit, a new subscription is refused; the oldest is not dropped

**Date:** 2026-09-27 · **Ticket:** #42 · **Status:** active

**Decision.** A subscribe past the limit gets a `too_many_subscriptions` error
frame, and the connection stays open. Re-sending a subscription the socket
already holds is never refused: the limit counts markets, not commands.

**Why.** A client that hits the limit has lost track of what it is watching.
Quietly dropping a market it believes it is subscribed to would show someone a
price that has silently stopped updating.

**Rejected.** Dropping the oldest subscription to make room.

**Notes.** Recorded in #127 from the comment at `realtime_service/core/errors.py`,
which the refactor shortened.

---

### D-NEW — When a connection fails for several reasons, the first one is reported

**Date:** 2026-09-27 · **Ticket:** #42 · **Status:** active

**Decision.** `Connection.fail` keeps the first reason it is given and ignores
the rest.

**Why.** The first reason is the cause; the rest follow from it. A socket
dropped for an expired token also stops draining its queue. Reporting
`slow_consumer` to a client whose real problem is an expired session would send
it off to debug its network instead of refreshing its token.

**Rejected.** Keeping the last reason.

**Notes.** Recorded in #127 from the comment at
`realtime_service/controller/connection.py`, which the refactor shortened.

---

### D-NEW — Acknowledgements go through the same send queue as prices

**Date:** 2026-09-27 · **Ticket:** #42 · **Status:** active

**Decision.** Every frame sent to a client, including the `subscribed` and
`unsubscribed` replies and error frames, goes through `Connection.enqueue`.

**Why.** So a `subscribed` reply can never overtake a price that was already
queued. A client that saw the price first would have good reason to throw it
away, as belonging to a market it had not finished subscribing to.

**Rejected.** Sending acknowledgements straight to the socket, around the
queue.

**Notes.** ADR 0010 records why each connection has a queue and a single
sender; this entry adds that replies use it too. Recorded in #127 from the
comment at `realtime_service/controller/connection.py`, which the refactor
shortened.

---

### D-NEW — The close reason is picked in priority passes, after every result is collected

**Date:** 2026-09-27 · **Ticket:** #42 · **Status:** active

**Decision.** When a connection's tasks finish, `_ending` first collects the
exception of every finished task, then decides in order: a domain error
(`RealtimeError`) wins; otherwise an unexpected failure closes with 1011 and is
logged; otherwise a disconnect means there is nobody left to send a close
frame to.

**Why.** More than one task can finish at the same moment.

- Collecting first reads every finished task's exception. Returning early would
  leave one unread, and asyncio then logs "Task exception was never retrieved"
  hours later from somewhere unrelated.
- Separate passes make the answer the same whatever order the tasks come out
  in. A set has no order. This really happens: when a client vanishes, the
  reader ends with `WebSocketDisconnect` while the sender's `send_json` raises
  a plain `RuntimeError` from Starlette. Judged in one pass, the same event
  closed either as a normal disconnect or as 1011, depending on which task the
  set happened to give first.

**Rejected.** A single loop that returns on the first exception it meets.

**Notes.** Recorded in #127 from the comment at
`realtime_service/controller/routes.py`, which the refactor shortened.

---

### D-NEW — The outcome is read before the other tasks are cancelled, and they are not awaited

**Date:** 2026-09-27 · **Ticket:** #42 · **Status:** active

**Decision.** `_serve` works out the ending from the finished tasks first, then
cancels the rest and does not await them.

**Why.** Reading first makes sure every finished task's exception is read
(see the entry above), and it stays true even if `_serve` is itself cancelled
during the cleanup. Not awaiting is safe: a cancelled task makes no more
progress of its own, it only wakes up into its own `CancelledError`, so none of
them can touch the socket again once `cancel` has been called. Awaiting them
would hand control back to the event loop for nothing, at the moment the
connection is most likely to be torn down underneath us.

**Rejected.** Cancelling before reading the outcome. Awaiting the cancelled
tasks.

**Notes.** Recorded in #127 from the comments at
`realtime_service/controller/routes.py`, which the refactor shortened.

---

### D-NEW — A bad origin is refused before the upgrade, and logged for the operator

**Date:** 2026-09-27 · **Ticket:** #42 · **Status:** active

**Decision.** A socket from an origin not in `CORS_ORIGINS` is closed with
4403 without being accepted, and a warning naming the origin is logged. A bad
token is handled the other way: the socket is accepted, then closed with 4401.

**Why.** Accepting a cross-origin socket even briefly is the very thing the
origin check exists to prevent, and the refused page is not owed a readable
error. The operator does get one, in the log, because a mistake in
`CORS_ORIGINS` looks exactly the same from the browser. A bad token is
different: a socket refused before the upgrade reaches JavaScript as a failure
with no code and no reason, and [X-4] #37 has to tell an expired session apart
from a dead network, so that one is accepted in order to send a reason.

**Rejected.** Accepting and then closing for a bad origin too, like a bad
token.

**Notes.** ADR 0010 records why the origin check is written by hand at all.
Recorded in #127 from the comment at `realtime_service/controller/routes.py`,
which the refactor shortened.

---

### D-NEW — A malformed message from Redis is logged as a warning

**Date:** 2026-09-27 · **Ticket:** #42 · **Status:** active

**Decision.** A price message that is not text, or does not match
`PriceEvent`, is dropped and logged at warning level. A stale duplicate is
dropped and logged only at debug level.

**Why.** A message that fails to decode is a bug in the producer, and what
users see is prices that silently stop updating. The log line is the only thing
that will tell anyone which of the two services is at fault. A duplicate is
different: it is the expected result of a producer retrying, and dropping it is
the guard doing its job. Nothing in this path may raise, because an exception
would end the Redis subscription for every connected client over one bad
message.

**Rejected.** Dropping malformed messages silently.

**Notes.** Recorded in #127 from the comment at `realtime_service/service/bus.py`,
which the refactor shortened.

---

### D-NEW — The bus waits between retries outside the error branch, and a connection that came up resets the wait

**Date:** 2026-09-27 · **Ticket:** #42 · **Status:** active

**Decision.** `PriceBus.run` sleeps after `_consume` has returned or raised,
not inside the `except` branch. The wait starts at 0.5 s and doubles up to
10 s, and it goes back to 0.5 s whenever the subscription actually came up.

**Why.** The sleep comes after `_consume` has closed its Redis client. Inside
the failure branch, a `finally` would only run once that branch had finished,
so a dead connection would be held open for the whole wait, up to ten seconds
of a socket nobody reads. Resetting after a real connection means a long,
healthy subscription that drops reconnects quickly, and only a Redis that
really cannot be reached gets backed off from. The loop never gives up:
dropping every client because the bus blinked would turn a blip into a
reconnect storm.

**Rejected.** Sleeping inside the `except` branch. Never resetting the wait.

**Notes.** Recorded in #127 from the comment at `realtime_service/service/bus.py`,
which the refactor shortened.

*Amended 2026-10-20 by #394: the class is now `FrameBus`. The rule is
unchanged.*

---

### D-NEW — The subscription hub keeps two indexes, and prunes both through one helper

**Date:** 2026-09-27 · **Ticket:** #42 · **Status:** active

**Decision.** `Hub` keeps `_by_market` (market to connections) and
`_by_subscriber` (connection to markets). An entry whose set becomes empty is
deleted, and both indexes are cleaned up by the one `_discard` helper.

**Why.**

- *Two indexes.* `_by_market` answers "who wants this event", on every
  broadcast. `_by_subscriber` answers "what was this connection watching", on
  every disconnect. The second costs a few bytes per subscription and stops a
  disconnect from being a scan of every market. Connections drop all the time,
  and a feed that got slower the more markets existed would be slowing down for
  a reason that has nothing to do with load.
- *Pruning.* Both maps live as long as the process and would otherwise only
  ever gain keys. Markets pile up for the life of the platform, so a map that
  never pruned would be a slow leak in the one process meant to stay up for
  days. It would also make `len(index)` mean something other than what its
  callers think.
- *One helper.* Writing the cleanup out twice by hand is how the two maps came
  to disagree: the market side pruned empty sets and the subscriber side did
  not, so a client that unsubscribed from its last market and stayed connected
  was still counted by `connection_count`.

**Rejected.** One index, with a scan on disconnect. Leaving empty entries in
place. Separate hand-written cleanup for each map.

**Notes.** Recorded in #127 from the comments at
`realtime_service/service/subscriptions.py`, which the refactor shortened.

---

### D-NEW — The subscription limit is enforced in the hub, not the route

**Date:** 2026-09-27 · **Ticket:** #42 · **Status:** active

**Decision.** `Hub.subscribe` raises `TooManySubscriptions`. The route only
turns it into an error frame. The count comes from the hub, not from a counter
on the connection.

**Why.** It is a rule about the hub's own state. A rule enforced by its only
caller is a rule the second caller will not enforce: a bulk-subscribe command
or an admin tool would quietly skip a limit held in the controller. It also
means the limit is tested without a socket, which is where the backend
conventions put a business rule. Asking the hub for the count means there is
one answer to the question rather than two that can disagree.

**Rejected.** Checking the limit in the route. Counting subscriptions on the
connection.

**Notes.** Recorded in #127 from the comment at
`realtime_service/service/subscriptions.py`, which the refactor shortened.

---

### D-NEW — The version gate is per process, not shared through Redis

**Date:** 2026-09-27 · **Ticket:** #42 · **Status:** active

**Decision.** `VersionGate` keeps each market's newest forwarded
`state_version` in the process's own memory. Replicas do not share it.

**Why.** A gate that all replicas agreed on would be a distributed lock in
front of a price feed, bought only to save a redundant re-render. It would make
every broadcast wait on a network round trip, in a service whose whole purpose
is being faster than a page refresh. The gate is not the client's guarantee
anyway: the client has to drop older events itself, because its snapshot can
be newer than an event in flight, and a reconnect can land on a replica with
its own high-water mark.

**Rejected.** Sharing the gate through Redis.

**Notes.** ADR 0010 already records that replicas share no state and that the
client must check versions too. This entry adds the cost reason for not sharing
the gate. Recorded in #127 from the comment at
`realtime_service/service/ordering.py`, which the refactor shortened.

---

### D-NEW — The refresh path locks the user row before the token

**Date:** 2026-09-27 · **Ticket:** #137 · **Status:** active

**Decision.** `rotate_refresh_token` looks up the token's owner without a lock,
then locks the owner's row in `auth.users` (`lock_user`), then the token's
row. The rotation and the replay take the same two locks in the same order.

**Why.** A replay revokes every live token the user holds. It finds them with
a SELECT, and a SELECT cannot see a token another request has inserted but not
yet committed. So a replay that ran while the attacker's rotation was midway
through missed the attacker's new token, and that session survived. Adding
`FOR UPDATE` to that SELECT does not help: it only locks rows it can already
see, and the new token is not one of them. Both requests have to wait on
something they share, and the only thing they share is the user.

The user row goes first on the rotation too, not just on the replay. Locking
it only on the replay deadlocks: the rotation holds the old token and, when it
inserts the new one, its foreign key takes a `KEY SHARE` on the user row. That
waits for the replay's user lock, while the replay waits for the rotation's
token so it can revoke it. One order everywhere, the wider lock first. ADR 0015.

The owner lookup is unlocked because a token's owner never changes. It selects
only the `user_id` column, so no token object lands in the session before its
row is locked. The token lock re-reads its row (`populate_existing`) anyway,
because locking the user loads all of the user's tokens through the
`refresh_tokens` relationship, and a logout may have revoked this one in
between.

**Rejected.** Locking the token rows in `revoke_all_for_user` (sees nothing it
did not already see). Locking the user on the replay path only (deadlocks, as
above). `SERIALIZABLE` isolation, for the reasons ADR 0015 gives.

**Notes.** `revoke_all_for_user` now requires its caller to hold the user's row
lock. [4.2] #14's suspend must take it too before it revokes a user's sessions,
or a rotation committing at the same moment keeps its new token. Logout locks
only its own token and waits on nothing else, so it cannot join a deadlock
with the rotation.

---

### D-NEW — A sell releases cost basis at average cost: the remaining basis is rounded half-up and the released basis is the difference

**Date:** 2026-09-25 · **Ticket:** #23 · **Status:** active

**Decision.** On a sell of `x` from a position of `held` shares at `basis`,
the remaining basis is `basis × (held − x) / held`, computed inside
`core/lmsr.py`'s engine context and quantized `ROUND_HALF_UP` to scale 4. The
released basis is `basis − remaining`, an exact subtraction at scale 4. A sell
of the whole position is an explicit branch that writes `0.0000` for both
quantity and basis. The arithmetic is a pure function in `core/`.

This clarifies "`cost_basis` is stored; average entry price is derived".
Its "cumulative net `cost_basis`" means net of **released basis**, never net
of proceeds.

**Why average cost.** It is the only method `(quantity, cost_basis)` can
support. "`ledger.positions` stores quantity and cost basis, keyed on the
triple" gives one row per holding and no lots, and FIFO or specific-lot
accounting needs lots. It is also the method under which "average entry
price", which [T-4] #24 displays, means what a trader expects: a sell leaves
it where it was, to within rounding.

**Why round the remainder and derive the release.** Rounding one side and
deriving the other makes `released + remaining == basis` hold on every sell.
Over a full sell-down, the released amounts therefore add up to exactly what
was paid. Rounding drift on an early sell is absorbed by a later one, and a
full exit leaves nothing behind. At a basis of `10.0000` over `3.0000` shares,
three sells of one share release `3.3333`, `3.3333`, `3.3334` and leave
`0.0000`.

**Why half-up and not directional.** The directional rounding in
"`quantize_cost` takes an unsigned magnitude; the caller applies the sign"
exists so that the residue of money *charged* goes to the pool. Cost basis
charges nobody and moves no credits, so there is no side for a residue to
favour. It takes the rounding every other non-charged figure in this service
takes. The choice is observable: a basis of `29.8428` over `54.3333` shares,
selling `10.0000`, leaves `24.3503` half-up and `24.3502` floored.

**Why engine precision.** `basis × remaining_quantity` can reach 36
significant digits, and the ambient 28-digit context would round the product
before the quantize ran. That is the hazard in "An absolute value on money is
`copy_abs()`, never `abs()`", arriving through a multiplication. Multiplying
before dividing keeps the product exact whenever the division is.

**Rejected.** *Net cash*, `cost_basis -= proceeds`: a profitable sell drives
it negative, which `ck_positions_cost_basis_nonneg` refuses, and a full exit
leaves minus the realized P&L instead of zero, so `basis / quantity` stops
being a price. *Rounding the average entry price and multiplying back*: at
`10.0000` over `3.0000` the average rounds to `3.3333`, three sells release
`9.9999`, and the last `0.0001` is either stranded on a zero-quantity row or
lost. *Floor or ceiling on the remainder*: a directional mode with no
direction to favour.

**Reversal trigger.** A ticket that requires lot-level accounting (FIFO,
tax lots, per-fill P&L). That needs a lots table, and this entry and the
single-row schema reverse together.

**Notes.** The positions table's claim to be "reconstructible from the
entries" now depends on this rule. Replaying a user's trade contexts in order
through it reproduces `cost_basis` exactly, and any other rule reproduces a
different number. `TransactionKind.TRADE_SELL` arrives with this ticket and,
like `TRADE_BUY`, is a non-native enum member that needs no migration.

Selling all but `0.0001` of a position leaves dust: at `29.8428` over
`54.3333`, selling `54.3332` leaves `0.0001` shares at a basis of `0.0001`,
an average entry of `1.0000`. That is correct arithmetic, and [T-4] #24
should expect it.

---

### D-NEW — A position sold to zero keeps its row

**Date:** 2026-09-25 · **Ticket:** #23 · **Status:** active

**Decision.** A sell that empties a position UPDATEs it to quantity
`0.0000`, basis `0.0000`. The row is never deleted. A later buy accumulates
onto it as a fresh basis.

**Why.** The holding check guarantees a sell's row exists, so a sell is
always an UPDATE, and the trade path keeps exactly two write shapes: INSERT
on a first buy, UPDATE otherwise. Deleting would add a third shape for no
reader that needs it. "`ledger.positions` stores quantity and cost basis,
keyed on the triple" already allowed `quantity >= 0` rather than `> 0` for
this case, and `test_a_zero_position_is_allowed` pins it.

**Rejected.** Deleting at zero: a DELETE on the hot path, and
delete-then-insert churn for a trader who exits and re-enters, in exchange
for sparing readers a `WHERE quantity > 0`.

**Reversal trigger.** Zero rows measurably costing a reader. [3.4] #12's
1,000-positions-in-five-seconds criterion is the first place that could show
it, and only if zero rows turn out to be a material share of the table.

**Notes.** Every reader filters. [T-4] #24 shows `quantity > 0` only, and
[3.4] #12 must skip zero rows: a zero payout leg is refused by `posting.post`
as `UnbalancedTransaction`, so settling one fails the whole settlement.

---

### D-NEW — The holdings check is read under the book lock, and the position takes no lock of its own

**Date:** 2026-09-25 · **Ticket:** #23 · **Status:** active

**Decision.** The sell's position read runs after the `market_books` row
lock, with no `with_for_update()` of its own. The same is true of the buy's
read of the row it adds to.

**Why.** Every writer of `ledger.positions` is the trade path, and the trade
path takes the book lock before it reads a position. So every trade that
could change a given `(user, market, outcome)` row has already queued on that
market's book row. The book lock is what holds the position still, and the
holding check is issued after that lock statement, which is ADR 0015's
purpose: "anything whose value the lock exists to hold still" is read after
the lock.

ADR 0015 also sets the evidence standard for a lock: a race test that fails
with the lock removed. The two-sells race fails five in five with the book
row's `FOR UPDATE` removed. A lock on the position row alone could never be
shown to matter, because nothing can reach that row without holding the book
lock first. A lock no test can show is needed is not ADR 0015 evidence.

**Rejected.** `with_for_update()` on the position read. It is free — book,
then position, then accounts is a consistent order everywhere — and it
matches ADR 0015's opening sentence word for word. It is rejected because it
would be a lock nothing depends on, sitting beside the one that does, and the
next reader would reasonably assume it is load-bearing.

**Reversal trigger.** A second writer of positions that does not take the
book lock first. [3.4] #12 is the candidate. If settlement takes the book lock
before writing positions, as its issue now says, this entry stands. If it
does not, the position read gets its own `FOR UPDATE`, in both writers.

**Notes.** "Never goes negative" does not make a race test on this check.
Without the book lock the losing sell writes a stale absolute quantity: a lost
update, proceeds paid twice, and a position that is wrong but non-negative.
The test asserts exactly one fill, the position, the balance, `q` against the
positions, and the ledger. It also needs a re-quote loop, because two sells
quoting one `state_version` are separated by `quote_stale` before either
reaches this check.

*Amended 2026-10-17. Decided by ADR 0024 (Accepted 2026-10-17), not built yet.*
Two changes. The holding check subtracts the shares the caller's live sell
orders in that outcome reserve, so an instant sell is limited to
`position - reserved`, and a sell placement is checked the same way. And
`ledger.orders` rows share this entry's argument: every writer of an order row,
placement, the cascade and cancel, takes the book lock first, so the book lock
holds them still and the order read takes no lock of its own. The reversal
trigger extends to them: a writer of order rows that does not take the book lock
first.

---

### D-NEW — A sell larger than the caller's holding is `insufficient_shares_held`, distinct from `insufficient_shares_outstanding`

**Date:** 2026-09-25 · **Ticket:** #23 · **Status:** active

**Decision.** `InsufficientSharesHeld`, `409 insufficient_shares_held`, with
`held` and `requested` as decimal strings in `error.details`. A caller with no
position is refused with `held: "0.0000"`. Checked after `unknown_outcome`
and before the outstanding check and any pricing.

**Why a code of its own.** The two refusals have different remedies. *Held*
is about the caller, and the fix is to sell at most `held`, which the details
supply. *Outstanding* is about the whole market and, on the trade route, can
only mean this service's own data is wrong (see "Shares outstanding equal the
sum of positions"). A client that cannot tell them apart cannot offer the
first fix, and would treat a server fault as a typing mistake.

**Why 409.** `InsufficientFunds`' reason: the request is well formed, and the
state refuses it. The same sell succeeds for a caller holding more.

**Why details.** `InsufficientFunds` carries `balance` and `required`
because "insufficient" alone is the least useful true sentence available. The
same argument applies here, and a sell form can offer "sell all `held`"
without a second request.

**Rejected.** Reusing `insufficient_shares_outstanding`, for the reason
above. A 422: nothing about the quantity is malformed on its own.

**Reversal trigger.** Positions that stop being per user — a shared or
team account holding shares — at which point "held" has to name whose
holding, and the details with it.

**Notes.** The preview does not check holdings and still should not: it has
no lock, so its answer could be stale before anyone acted on it. A preview
can therefore quote a sell the trade refuses with this code, which is the same
deliberate asymmetry as `market_closed`.

---

### D-NEW — Shares outstanding equal the sum of positions, so the outstanding check is a backstop on the trade route

**Date:** 2026-09-25 · **Ticket:** #23 · **Status:** active

**Decision.** For every outcome, `market_outcomes.q` equals the sum of
`positions.quantity` in it. `InsufficientSharesOutstanding` stays in the
trade path as a backstop, off the route's error table and declared
responses, and is mentioned in the Errors prose as unreachable.

**Why it holds.** `q` has exactly two writers. Book creation writes it as
zero, when no position can exist yet because the positions foreign key needs
the outcome row. The trade path moves it by the traded quantity, in the same
transaction and by the same amount as the position. By induction the two
agree. So a sell that passes the holding check has `x ≤ held ≤ q`, and the
outstanding check cannot fire.

**Why keep it anyway.** `market_outcomes.q` has no `CHECK (q >= 0)` (see
Open). If a hand-run repair ever leaves `q` below a holding, this check is the
only thing between that book and a negative `q` that `C(q)` has no answer
for. It costs one comparison.

**Rejected.** Deleting the check as dead code: it is dead only while the
invariant holds, and nothing in the database enforces the invariant.
Listing it in the error table: #22 removed unreachable rows, and a client
should not write a handler for a response that means this service is broken.

**Reversal trigger.** Any second writer of `q` or of positions, beyond book
creation and the trade path. [3.4] #12 is the candidate. When one lands, it
either keeps each outcome's `q` equal to the sum of its positions, or it
records here that this no longer holds — and the unreachability claimed
above goes with it.

**Notes.** It does **not** hold on test fixtures that write `q` directly,
which leave `q` above the positions. Every test of this invariant starts from
a book opened at zero. The one test that writes `q` directly on purpose is the
backstop's own.

---

### D-NEW — Realized P&L is not stored

**Date:** 2026-09-25 · **Ticket:** #23 · **Status:** active

**Decision.** No column and no `Transaction.context` field for realized P&L
or released basis.

**Why.** Nothing asks for it. #23's criteria never mention it. [T-4] #24 asks
for *unrealized* P&L, and its "realized outcome (paid out / worthless)" is
the settlement result, not P&L from selling. It can also be derived: replaying
a user's trade contexts in order through "A sell releases cost basis at
average cost" reproduces the released basis of every sell, and proceeds are
in `context.total` already.

**Rejected.** *A column on positions*: a hand-applied migration against
`cs464`, and a second running total beside one that is already a rollup.
*`released_basis` in `context`*: the cheapest version and the one to reach
for first if this reverses, but it adds a field nothing reads.

**Reversal trigger.** A ticket that displays realized P&L per trade or per
position, such as a trade history or a leaderboard ranking on it. Add
`released_basis` to the sell's `context` then, not a column.

**Notes.** [T-5] #25 (2026-09-28) is a trade history and does not fire the
reversal trigger above. Its rows show each trade's signed amount and average
fill price, and no realized P&L.

---

### D-NEW — A sell cannot take a market's pool below its seed subsidy

**Date:** 2026-09-25 · **Ticket:** #23 · **Status:** active

**Decision.** No overdraft check on the pool for a sell. `_refuse_overdrafts`
exempting non-USER accounts is safe here, and this is the bound that makes it
safe: before settlement, a pool opened at `q = 0` never holds less than its
seed subsidy.

**Why.** The pool holds `seed + Σ charged − Σ paid`. Buys round up and sells
round down, so that is at least `seed + C(q) − C(0)`: the true costs
telescope, because the LMSR cost function is path-independent. `C` increases
in every `qᵢ`, and every `qᵢ ≥ 0` (the holding check, backstopped by the
outstanding check), so `C(q) ≥ C(0)` and the pool is at least `seed`. A sell
moves the pool back toward `C(0)` and can never pass it.

**Rejected.** An overdraft check on MARKET_POOL for sells. By the argument
above it could never fire, and "the check is an allowlist on USER" in
`posting.py` says why adding a kind there means deciding it holds spendable
money.

**Reversal trigger.** Any case where a pool ends below its seed before
settlement, which the pool-bound test asserts after mixed trading. Also a
change that lets `q` start non-zero at book creation, or that lets `q` go
negative.

**Notes.** The one exception is the one-tick case in "A cost exactly on a
tick can round one tick against the trader, or toward them on a sell". It
needs about 120·b of skew and moves one tick. The pool's real loss happens at
settlement, bounded by `b·ln(n)`, which is the existing Open question about
subsidies below that. The bound does not hold on test books whose `q` was
written directly, because their pool was never paid for that `q`.

---

### D-NEW — The trade route accepts both sides

**Date:** 2026-09-27 · **Ticket:** #23 · **Status:** active

**Decision.** `POST /ledger/markets/{market_id}/trades` accepts `side: "sell"`
as well as `"buy"`. This supersedes "The trade path is side-generic in shape
and buy-only at the route". The side-generic service shape that entry chose
stands unchanged: one `trading.execute` taking `side: Side`, with the side
picking the sign of the delta, the rounding direction, the transaction kind
and the shape of the position write, and nothing else.

**Why.** The route was buy-only for one reason: a sell was unsafe until the
per-user holding check existed and was read under the book lock. #23 adds
that check ("The holdings check is read under the book lock, and the position
takes no lock of its own"), the average-cost release of basis, and
`insufficient_shares_held`. With the reason gone, keeping the route closed
would leave sells implemented and untested at the boundary the frontend
actually calls.

Keeping one path for both sides is what let the sell inherit the replay, the
gate, the staleness check and the publish without restating them, which is
the whole return on the earlier entry's choice.

**Rejected.** A separate sell route or a separate sell function. Either one
is a second copy of the order `trading.py` fixes, and the two would first
disagree on the path nobody tests, most likely the retry.

**Reversal trigger.** A sell that has to differ from a buy in the order of
the path, not just in sign, rounding, kind or position write: for example a
check that must run before the gate, or a lock only one side takes. Then the
single function stops being one path and the sides split. Settlement ([3.4]
#12) is not that trigger. It is not a trade and does not go through this
route.

---

### D-NEW — The portfolio is one route, `GET /ledger/portfolio/me`, and is not paginated

**Date:** 2026-09-28 · **Ticket:** #24 · **Status:** active

**Decision.** `GET /ledger/portfolio/me` returns `user_id`, `account_id`,
`balance`, `positions_value`, `net_worth`, and a flat list of `positions`, each
carrying `market_id`, `outcome_id`, `outcome_position`, `quantity`,
`cost_basis`, `average_entry_price`, `price`, `value`, `unrealized_pnl` and
`state_version`. Every amount, quantity and price is a decimal string at
scale 4. No cursor, no `limit`, no admin variant.

**Why no pagination.** `positions_value` and `net_worth` are sums over every
position. Paging the list either pages the totals, which makes them wrong, or
computes them over every row while returning a page of them, which is an
unpaginated read that also has a cursor. The list is bounded by the markets a
user has traded times their outcomes, not by time, so it does not grow the way
the entry history does.

**Why flat.** A position is the unit the ledger stores, keyed on the triple
("`ledger.positions` stores quantity and cost basis, keyed on the triple").
One user can hold two outcomes of one market, and grouping them is
presentation. Each row carries its own `state_version`, so a client can match
a row against the realtime feed without a parent object. Rows are ordered by
`market_id`, then `outcome_position`, so two reads of one state list the same
rows in the same order.

**Why `/me` only.** No criterion asks to read another user's portfolio. The
admin balance route exists for [4.1] #13's investigation case. Nothing asks
for the portfolio equivalent.

**Rejected.** *Keyset pagination as on `/entries/me`*: see above. *Nested by
market*: a shape for a screen, fixed into the API. *An admin
`/users/{id}/portfolio`*: a route for a caller that does not exist.

**Reversal trigger.** A measured portfolio read that is a material share of
its latency budget because of its row count ([5.4] #20 is where that would
show), or a ticket that displays another user's positions. Either changes
the shape, and the first one also forces a decision on where the totals come
from.

---

### D-NEW — The portfolio reads its balance and positions in one statement, after the grant, with no lock

**Date:** 2026-09-28 · **Ticket:** #24 · **Status:** active

**Decision.** `grants.ensure_granted` runs first. It may post and commit on a
user's first read. Then one SQL statement, with no `FOR UPDATE`: an anchor row
carrying the balance as a scalar subquery over the user's entries, outer-joined
to every position with `quantity > 0`, joined to every outcome of each held
market (for `q`) and to its book (for `b` and `state_version`). No
`books.ensure_open`.

**Why the grant first.** It is the one step that can write, and `posting.post`
commits. Running it before the read means the read sees the grant it just
minted, and nothing follows a commit on a path that wrote ("The book's writes
share `posting.post`'s commit, and nothing may follow it").

**Why one statement.** The argument in "Preview reads `q` and `state_version`
in one SQL statement", widened to the balance. Under READ COMMITTED each
statement sees its own snapshot. Read the balance and then the positions, and
a trade committing in between shows the credits already spent and not the
shares they bought: net worth drops by the whole trade, then comes back on
refresh. The same is true between two markets' books. One statement sees one
snapshot, so every figure in the response describes the same moment.

**Why an anchor row.** An inner join from positions returns nothing for a user
with none, and the balance with it. Hanging the balance off a one-row anchor
keeps an empty portfolio inside the same statement.

**Why no lock.** ADR 0015's rule covers reads that decide a write. This read
decides nothing, for the reason "Preview takes no locks" gave, and a lock here
would queue every portfolio page load behind every trade in every market the
user holds.

**Why no `ensure_open`.** `positions (market_id, outcome_id)` is a foreign key
to `market_outcomes`, whose `market_id` is a foreign key to `market_books`. A
position implies a book, so the cold path cannot be needed, and calling it
would bring a market_service dependency into a read that has no use for one.

**Rejected.** *`balance_of_user` plus `book_prices.read_or_open` per market*:
N + 1 statements, N + 1 snapshots, and a cold path that cannot be reached.
*One REPEATABLE READ transaction over several statements*: correct, and it
changes isolation for one reader where every other reader in this service
gets its consistency from one statement.

**Reversal trigger.** A portfolio figure that decides a write, such as a
margin rule or a prize paid on net worth. At that point ADR 0015 applies and
the read takes locks. Also split it if one statement can no longer serve,
which the test in "The price read exists once, and the price quantizer is in
`core/pricing.py`" states: a reader needing something it does not use to
decide its answer.

*Noted 2026-10-10 by [3.4] #12, in PR 7: the statement also outer-joins
`ledger.market_results`, so the one snapshot covers settlement too. A
settlement committing mid-read is either seen whole (the payout in the
balance, the rows settled) or not at all (no payout, the rows valued at the
frozen book). Read separately, a payout could reach the balance while its
shares were still valued: the double count "A settled position shows `result`
and `payout`, and its value fields are null" exists to prevent. See "A settled
row's payout is its quantity, and its result comes from
`ledger.market_results`, joined in the portfolio's one statement".*

**Amended 2026-10-21.** #410 adds `ledger.orders` to the statement: each
outcome row carries the caller's live sell orders' reserved shares as a
correlated subquery. The argument above is why it is not a second read: a
fill committing between two reads could show the position before it and the
reservation after it. See "A portfolio row carries the shares its owner's live
sells hold back, read in the portfolio's one statement".

---

### D-NEW — The portfolio carries ids only, and makes no call to market_service

**Date:** 2026-09-28 · **Ticket:** #24 · **Status:** active

**Decision.** Each position names its market and outcome by `market_id`,
`outcome_id` and `outcome_position`. No question, no label, no status. The
read makes no HTTP call.

**Why.** Valuation needs only `q`, `b` and the holding, all of them the
ledger's (ADR 0018). Labels and status belong to market_service, and the
frontend already fetches market detail for any market it shows. A call per
held market would make a read depend on market_service's availability, which
ADR 0017 accepted for a trade because a trade must know whether the market is
open. A portfolio does not need to know that to value a position.

**Rejected.** *A call per market*: N round trips and a new outage mode for a
read. *A batch endpoint on market_service*: a new route in another service for
one caller. *Snapshotting labels into the book at handoff*: a migration and a
second copy of display data the ledger never reads.

**Reversal trigger.** A portfolio criterion whose answer depends on a
market's state and that the ledger cannot answer from its own data. [3.4]
#12's realized outcome is the candidate, and it should come from the ledger's
own settlement record rather than from a call. Also a measured N + 1 cost on
the frontend's joins, which makes the batch endpoint worth building.

---

### D-NEW — A damaged book, or a position above `q`, fails the whole portfolio with `market_book_incomplete`

**Date:** 2026-09-28 · **Ticket:** #24 · **Status:** active

**Decision.** If any held market's book fails `book_prices.refuse_unpriceable`,
or any holding exceeds its outcome's `q` (`trade_cost_of` raises
`InsufficientSharesOutstanding`), the whole read is `500
market_book_incomplete`. `ProceedsBelowTick` is not damage: it values the
position at `0.0000` (ADR 0018). `QuantityTooLarge` is not caught.

**Why the whole read.** `net_worth` is a sum, and [L-1] #38 ranks on it.
Leaving a row out makes the total silently wrong, and a partial answer that
looks complete is worse than no answer. A per-row error field would add to the
contract a state that means this service's own data is wrong.

**Why this code.** It is the one the preview and the snapshot already return
for a book this service holds and cannot price. It is not 503, which blames
market_service and invites a retry ("The price read exists once, and the price
quantizer is in `core/pricing.py`"). A position above `q` is that same fault:
"Shares outstanding equal the sum of positions, so the outstanding check is a
backstop on the trade route" says it cannot happen while the invariant holds,
and when it does, the engine cannot price the sale. It is not surfaced as
`409 insufficient_shares_outstanding`, because a 409 says the caller's request
was refused by state, and this caller asked for nothing. The code's meaning
widens from "cannot be priced" to "cannot be priced *at this holding*", and
its docs line says so.

**Why `QuantityTooLarge` is unreachable.** Selling `x ≤ q` leaves `q − x ≥ 0`,
and the proceeds are under `x` because every price is below one. `x` was read
from a `Numeric(18, 4)` column, so neither can pass `MAX_MAGNITUDE`.

**Rejected.** *Skipping the damaged row*: a wrong total with no signal.
*Valuing it at zero*: the same, disguised as a real valuation. *503*: see
above.

**Reversal trigger.** A second writer of `q` or of positions that breaks the
outstanding invariant by design ([3.4] #12 is the candidate, and it is
already told so), or a ticket that asks for a partial portfolio to be shown
when one market is broken.

---

### D-NEW — A resolved market's realized outcome is [3.4] #12's criterion, not [T-4] #24's

**Date:** 2026-09-28 · **Ticket:** #24 · **Status:** active

**Decision.** #24's criterion "Resolved markets show realized outcome (paid
out / worthless) rather than a live price" moves to #12. #24 ships no
placeholder field for it. Until #12 lands, an approved market that has not
been settled is valued at its frozen book, like any other closed market.

**Why.** Before #12 there is no settlement, no SETTLED status and no record in
the ledger of which outcome won, and the portfolio holds ids only ("The
portfolio carries ids only, and makes no call to market_service"). What #24
could show depends on what settlement does to the position rows (zero them,
keep them, or write something beside them), and #12 has not decided that. A
criterion whose answer depends on another ticket's schema belongs to that
ticket.

**Rejected.** *A `resolved: false` or `outcome: null` field now*: a promise
in the contract with no implementation, and the frontend writes a handler for
a value that is always the same. *Calling market_service for the resolution*:
reverses the entry above for a criterion that settlement answers better from
the ledger's own data.

**Reversal trigger.** #12 lands, or a ticket needs a resolved market shown
before settlement exists. The first is expected. The second would mean
reading the approved outcome from market_service, and reopens the ids-only
entry with it.

**Notes.** #12's note carries the trap: a settlement that pays out and leaves
positions at `quantity > 0` has the portfolio count those shares twice.

*Fulfilled 2026-10-10 by [3.4] #12, in PR 7. This entry is history. A settled
row carries `result` and `payout`, and its value fields are null: see "A
settled position shows `result` and `payout`, and its value fields are null"
and "A settled row's payout is its quantity, and its result comes from
`ledger.market_results`, joined in the portfolio's one statement". The Notes'
trap is closed: positions keep `quantity > 0` after the payout, and the settled
branch values them at nothing.*

---

### D-NEW — A user's holdings in one market are valued as one sequence of sales

**Date:** 2026-09-28 · **Ticket:** #24 · **Status:** active

**Decision.** `core/pricing.py::liquidation_values_of(q, b, holdings)` returns
one value per outcome, `0.0000` where nothing is held. It sells the holdings
in `outcome_position` order, each through `trade_cost_of` on the `q` the
previous sale left. A sale whose proceeds are below a tick is valued `0.0000`
and leaves `q` unchanged for the next. `InsufficientSharesOutstanding`
propagates, and the portfolio remaps it ("A damaged book, or a position above
`q`, fails the whole portfolio with `market_book_incomplete`"). [L-1] #38
calls the same function.

**Why.** ADR 0018 values a position at what selling it would pay, and a
user's holdings in one market share one book: selling one moves the price of
the next. At `b = 100`, a complete set of 500 YES and 500 NO at
`q = (500, 500)` is worth exactly 500. In sequence it is valued 499.9999;
one position at a time, 137.2862 — ADR 0018's rejected error in reverse.

**Why a fixed order.** Row values depend on it, and so can the rounded total,
by less than a tick per sale, because each sale floors on its own. The
unrounded total does not. `outcome_position` is already the order of the `q`
vector and of every price list, and fixing it makes every read of one state
agree.

**Why a skipped sale leaves `q` alone.** The trade route refuses that sale
(`proceeds_below_tick`), so a book it had moved is one no sequence of real
sales could reach.

**Rejected.** *Each position valued alone at the current `q`*: every row
quotable, and a complete set shown at 137.29. *One unrounded
`C(q) − C(q − h)` per market, split across rows*: no sale pays that figure,
and the split would be a rule nobody could check against a trade.

**Reversal trigger.** A ticket that needs order-independent per-row values,
or a trade route that sells several outcomes in one transaction — which makes
the combined sale real, and the portfolio should then value that.

*Noted 2026-10-10 by [3.4] #12, in PR 7: "[L-1] #38 calls the same function"
now means through the portfolio's valuation, which leaves settled markets out
before it calls `liquidation_values_of`. See ADR 0018's 2026-10-10 amendment.*

---

### D-NEW — The trade history is `/entries/me` extended, not a second route

**Date:** 2026-09-28 · **Ticket:** #25 · **Status:** active

**Decision.** [T-5] #25's history is `GET /ledger/entries/me`, with typed trade
fields added to its rows. Nothing it returns today is removed or renamed,
`context` included. `ledger_service.history_for_user` stays the one function
behind it and behind the admin `GET /ledger/users/{user_id}/entries` ([4.1]
#13), so both routes get the new fields.

**Why.** #13 already built the query #25 needs: newest first, keyset-paged, a
derived `balance_after`, and the grant minted on first read. #25's note says
"Same query, different scope. Build once." A second route over the same rows is
the second copy CLAUDE.md forbids. An additive change breaks no client: no
frontend code calls either route yet (#53 and #57 are unbuilt), and the backend
tests assert fields that stay.

**Rejected.** *A new `GET /ledger/history/me`*: two routes and two response
shapes over the same rows, and they would first disagree about a row nobody
tests. *Dropping `context` now that its trade fields are typed*: a documented
contract change that buys nothing, and `context` is still the only place an
unrecognised kind's detail can reach a client.

**Reversal trigger.** A history row that is not one entry of the user's
account, for example a row that merges two transactions. Then the history is a
different query from #13's and gets its own route.

---

### D-NEW — A transaction puts at most one leg on any USER account

**Date:** 2026-09-28 · **Ticket:** #25 · **Status:** active

**Decision.** No transaction has two entries on one USER account. [T-5] #25's
history shows one entry of the user's account as one row, and this invariant is
what makes that row one transaction. A guard test over the whole ledger fails
if any transaction breaks it.

**Why.** "What the user sees for a trade is one row." Every writer already
complies: the grant puts one leg on the user, and a trade puts one leg on the
user and one on the pool. `posting.post` does not enforce it, because a list of
legs may name an account twice. Written down and tested, it becomes something
the next writer has to keep rather than something that happens to be true.

**Rejected.** *Grouping the history by `transaction_id`*: a GROUP BY and a
netted amount on every page, for a case no writer produces. *Netting legs per
account inside `posting.post`*: a change to the write primitive for a reader's
convenience, and it would hide a writer's bug instead of failing on it.

**Reversal trigger.** A movement that has to put two legs on one user, for
example a settlement that pays and charges one user in one transaction. [3.4]
#12 is told to keep one leg per USER account. If it cannot, the history groups
by transaction instead.

---

### D-NEW — The history page and its running balances are read in one statement, anchored at the page's newest row

**Date:** 2026-09-28 · **Ticket:** #25 · **Status:** active

**Decision.** One SQL statement returns a page's entries, their transaction's
`kind` and `context`, and every `balance_after` on the page. `balance_after` is
the sum of the account's entries at or before the page's newest row, minus a
window sum of the newer rows on the page. The anchor is the page's own newest
row. It is not today's balance counted back.

**Why the anchor.** A figure anchored to a position in the feed does not depend
on when the page was fetched. A page 2 read after new trades still continues
page 1, as long as every new entry is newer than every anchor. #187 is what
makes that true for a user trading in two markets at once; until it lands, that
case is a documented limitation. Counting back from the current balance is also
more expensive, not less: the current balance is itself a sum over the whole
history, so it costs every entry plus the newer ones. No derived approach
avoids a sum over O(entries) per page without storing something, and storing is
ruled out (ADR 0009). Page k sums about N − (k − 1)·limit rows of
`ix_ledger_entries_account_feed`.

**Why one statement.** "Preview reads `q` and `state_version` in one SQL
statement", widened to a page. As two statements, the page and its anchor sum
can see different snapshots. A transaction committing between them can be
counted in the anchor and missing from the rows, which shifts every figure
below it. One statement sees one snapshot, and a transaction's legs commit
together.

**Rejected.** *The page then `accounts.balance_of(as_at=...)`*: the #13 shape,
and two snapshots. *Current balance minus the newer entries*: see above.
*`INCLUDE (amount)` on the index for index-only scans*: a hand-applied
migration for a cost nobody has measured.

**Reversal trigger.** A measured history page whose anchor sum is a material
share of its latency ([5.4] #20 is where that would show). The first answer is
`INCLUDE (amount)`, and only after that a stored snapshot with a delta, which
ADR 0009 names as the upgrade.

---

### D-NEW — The history's price is the average fill price, and the trade response's "no prices" excluded only marginal prices

**Date:** 2026-09-28 · **Ticket:** #25 · **Status:** active

**Decision.** A trade row carries `average_price = per_share(|amount|,
quantity)`: half-up at scale 4, the preview's field name and its function. "The
trade response is reconstructed from `Transaction.context`, and carries no
prices" excluded **marginal** prices, not a price derived from what was
charged. The trade response itself stays price-free.

**Why.** That entry's argument is that a marginal price "is not derivable from
a stored trade" and is "a lie with a timestamp" when replayed. An average fill
price is derived from `amount` and `quantity`, both stored in the transaction's
own commit, and it stays true for ever, as `total` does. It equals the
preview's `average_price` for the same trade at the same `state_version`,
because the preview divides the same quantized magnitude by the same quantity
through the same function. The name `average_price` keeps it apart from the
marginal `price` the portfolio and the snapshot report.

**Rejected.** *`price`*: the issue's word, and the portfolio's name for the
marginal price. One name for two numbers is the confusion ADR 0018 spent a
record on. *No price at all*: #25's criterion asks for one. *Storing it in
`context`*: a second copy of a quotient of two stored values.

**Reversal trigger.** A trade that can fill at more than one price, for example
against a resting order. Then `|amount| ÷ quantity` is still the average, but
it stops being the only price the trader saw, and the history may need fills.

*Amended 2026-10-17. Decided by ADR 0024 (Accepted 2026-10-17), not built yet.*
The reversal trigger is not tripped for a trade row. Every transaction is still
one trade along one path of the market maker's book, so `|amount| ÷ quantity` is
still the only price that transaction filled at. A limit order filled in parts
has one transaction per fill, each with its own average price, and those rows
are the "fills" this trigger says the history may need. The order's own average,
`spent ÷ filled_quantity`, appears only on the order reads.

---

### D-NEW — A trade row's typed fields come from its kind and `context`; any other kind renders with them null

**Date:** 2026-09-28 · **Ticket:** #25 · **Status:** active

**Decision.** For `trade_buy` and `trade_sell`, the row builder reads
`market_id`, `outcome_id` and `quantity` from `context` (with `quantity`
quantized to scale 4) and `side` from `kind`. Every other kind, the grant
included, carries `market_id`, `outcome_id`, `side`, `quantity` and
`average_price` as `null`. `amount` always comes from the entry, never from
`context.total`. An unrecognised kind is not an error.

**Why.** Every trade since [T-2] #22 has written those keys, because
`result_of` requires them. `side` is taken from the column rather than the
jsonb because the column is what the ledger indexes and validates. `quantity`
is quantized because `context` stores it exactly as it arrived ("A replay hit
is compared against the request before it is returned"), so `"10"` and
`"10.0000"` both exist. Rendering unknown kinds generically means a new kind
appears in the history the day it is written, with its amount and running
balance correct, and needs a mapping only if it wants more.

**Rejected.** *Reading any kind's `context` for a `market_id`*: it guesses a
shape nobody has promised. *Refusing an unknown kind*: a history that fails on
the day settlement ships.

**Notes.** The one test that fabricates a kind posts a `market_seed`
transaction against a user account. No real writer does that; it stands in for
a kind the builder does not recognise, and its docstring says so.

---

### D-NEW — A resolution payout's history row is [3.4] #12's criterion, not [T-5] #25's

**Date:** 2026-09-28 · **Ticket:** #25 · **Status:** active

**Decision.** #25's "Every balance-affecting event appears: …resolution
payouts…" moves to #12. #25 ships the generic row, which a payout renders
through unchanged. If the payout's market should show, #12 adds its kind to the
row builder's mapping.

**Why.** No settlement kind and no settlement `context` exist. A test here
would invent both, and #12 would then either inherit an invented shape or break
the test. The part #25 can promise, that an unknown kind appears with a correct
amount and `balance_after`, it tests with the stand-in above. Same shape as "A
resolved market's realized outcome is [3.4] #12's criterion, not [T-4] #24's".

**Rejected.** *Keeping the criterion and testing it with a fabricated
settlement*: a test pinned to a shape #12 has not chosen.

**Reversal trigger.** #12 lands. Its row either renders through the generic
path or adds a mapping, and this entry is then history.

*Fulfilled 2026-10-10 by [3.4] #12, in PR 7. This entry is history. The payout
took the mapping branch, not the generic one: the row builder maps
`settlement`, per "A payout's history row is kind `settlement`, with market,
outcome and quantity". The stand-in test for an unrecognised kind still pins
"any other kind renders with them null", and stays.*

---

### D-NEW — Every decision a market records is stamped with Postgres's `clock_timestamp()`, read after the row lock

**Date:** 2026-09-29 · **Ticket:** #179 · **Status:** active

**Decision.** `submitted_at`, `published_at`, `closed_at` (early close),
`proposed_at`, `approved_at`, and the audit entry's `occurred_at` on each of
those paths, come from `market_service._database_now`: `SELECT
clock_timestamp()`, run after the path holds the market row. Display reads
(`browsing.py`) keep Python's clock, as D-025 allows for reads that decide
nothing.

**Why.** The sweep stamps `closed_at` with Postgres's clock. A proposal stamped
by the container could land before that close if the container ran behind the
database, so a market's history could show a proposal made before the market
had closed. One clock for every stamp makes the order the history shows the
order things happened, however far a replica drifts. Submission moves too,
although #179 did not list it: `submitted_at` sits beside `published_at` on the
same row, and two clocks there could invert those two instead.

**Rejected.** *`func.now()`*: it is `transaction_timestamp()`, frozen when the
transaction opened and so before the lock wait. That brings back the bug #138
fixed, a decision stamped before the state it decided on existed, and the #173
race tests fail under it. *Moving the sweep to Python's clock instead*: replicas
would then disagree about which markets are due (D-025).

**What stays on another clock, and why.** `created_at` and `updated_at` are
bookkeeping, not decisions, and keep Python's clock through their ORM defaults.
Moving them means server defaults and an `onupdate` on an existing table for
two columns nothing orders against. The one pair that can invert is
`created_at` after `submitted_at`, on a first save that submits straight away
from a container running ahead, by that clock drift. The sweep keeps
`func.now()`: its transaction starts before it commits the close, and a
proposal can only follow that commit, so its `closed_at` is still earlier.

**Notes.** One extra round trip per write, including each draft autosave. The
`now` parameters stay, so tests can still pin a time. #179's last criterion
says to skew the container clock ahead; the tests skew it behind, because the
sweep's database stamp is earlier than any later proposal in real time, so
only a container running behind can stamp a proposal before it. The tests in
`unit_test/service/test_database_clock.py` put the container's clock an hour
behind and fail against a Python stamp.

---

### D-NEW — An administrator's read of somebody else's balance or history mints nothing

**Date:** 2026-09-29 · **Ticket:** #188 · **Status:** active

**Decision.** `GET /ledger/users/{id}/balance` and `/entries` read through
`ledger_service.balance_for_admin` and `history_for_admin`, which find the
USER account and never create one or mint a grant. An id with no account reads
as a zero balance with a null `account_id`, and an empty history. Only a
user's own reads (`/balances/me`, `/entries/me`, `/portfolio/me`) and the trade
path mint, as ADR 0009 describes.

**Why.** This service holds no user table (ADR 0003), so every id looks like a
user id to it. Before this, an administrator who pasted a market's id, the
platform owner's id or a typo minted `STARTING_CREDITS` into an account nobody
owns. That grant is an append-only transaction, so undoing it takes a
compensating one written by hand. An admin read is investigating, and should
not be able to change what it investigates.

**Rejected.** *Refusing only the ids this service can recognise*, the platform
owner and any MARKET_POOL owner: cheap, but a typo still mints, and a typo is
the likeliest of the three. *A 404 for an id with no account*: a user who has
registered and never opened the app is a real user with nothing yet, which is a
zero, not an error.

**Notes.** [4.1] #13's "sees their starting credits" now holds after the
user's own first read, not before it. `BalanceOut.account_id` became nullable,
and is null only on the admin route. The frontend does not read it.

---

### D-NEW — A movement's timestamp is fixed under its account locks, not trusted from the clock

**Date:** 2026-09-29 · **Ticket:** #187 · **Status:** active

**Decision.** `posting.post` fixes `created_at` after `accounts.lock`, as
`max(now, newest created_at on any locked account + 1µs)`. Both legs and
`Transaction.occurred_at` carry it. `now` is a floor, not the stamp. A trade's
`state_changed_at` and its price frame's `occurred_at` keep the trade's own
`now`, because they describe the book, not the ledger feed.

**Why.** A trade took its time under its market's book lock, before it queued
for the user's account lock. Two trades by one user in two markets hold two
different book locks, so the earlier-stamped one could commit second. The
history orders by `(created_at, id)`, so it listed the two out of commit order,
and a running balance the user never had appeared: a buy costing more than the
user held, paid for by a sale that committed first, showed a negative
`balance_after`. Every writer of an account's entries holds that account's lock
from the stamp to the commit, so reading the newest entry under the lock makes
feed order equal commit order on every account. That holds across replicas and
when the clock steps back, because it does not rely on the clock being in
order. It also means no two entries on one account share a time. It restores
the guarantee [T-5] #25 had to withdraw, "The history page and its running
balances are read in one statement, anchored at the page's newest row": every
new entry is newer than every anchor.

**Rejected.** *Taking the time from Python after the lock and trusting it*: two
replicas' clocks, or one clock stepped back by NTP, still disagree about order.
*Postgres's `clock_timestamp()` after the lock*: one clock for every replica,
but a clock that steps back still breaks it, and it is a round trip that buys
no more than the probe does. *Ordering the feed by a sequence instead of
`created_at`*: a new column on an append-only table, and a cursor format
change for every client.

**Notes.** One indexed probe of `ix_ledger_entries_account_feed` per locked
account, two on a trade. PLATFORM is touched by every grant, so a test that
injects a fixed past `now` after another grant gets a later stamp than it
injected. Assert against the returned transaction's `occurred_at`.

A clock that runs *fast* is an accepted cost. Order stays right, but the shown
time does not: once a replica ten minutes ahead has written a grant, every
later grant lands after PLATFORM's newest entry, a little past that future
time, until the real clock catches up. The same happens to a market's trades
through its pool account. Keep the replicas on NTP.

---

### D-NEW — The admin market overview is its own route: published markets plus the caller's own

**Date:** 2026-09-29 · **Ticket:** #5 · **Status:** active

**Decision.** `GET /markets/overview`, admin-only, lists every published
market and the caller's own draft and submitted markets. It is declared
before `GET /markets/{market_id}`. A trader gets `403 not_an_administrator`.
Each row carries `creator_id`.

**Why.** Neither existing list serves #5. `GET /markets` is scoped to the
creator, reports the raw column and orders by `updated_at`; the frontend's
create and propose flows depend on all three. `GET /public/markets` never
returns a draft and orders still-trading first. A draft stays visible only to
its creator (ADR 0003, [1.1] #1), so "all markets" for an administrator is
exactly published-or-mine, written as an allowlist like `_visible` so a new
status is not visible by default. `creator_id` rather than a computed
`is_mine`: it is the fact, the frontend already knows its own id, and a flag
computed against the caller would make one row mean different things to two
readers.

**Rejected.** *`/admin/markets`*: auth owns the `/admin` prefix, and a
gateway routing on the first path segment, which ADR 0002's shared-domain
constraint makes likely, would send it there. *A scope parameter on
`GET /markets`*: a flag with one user that changes a route others depend on.
*Widening `GET /markets` to every admin's markets*: exposes other admins'
drafts, which ADR 0016 declined for the same reason.

**Notes.** Declared before `/{market_id}`, or FastAPI parses `overview` as a
UUID and answers 422; a route test pins it. Reversal trigger: [4.4] #16's
finer admin roles, if any of them should see less than published-or-mine.

---

### D-NEW — The admin overview derives status, and its counts are read against the derivation, not the column

**Date:** 2026-09-29 · **Ticket:** #5 · **Status:** active

**Decision.** The overview's `status`, its filter and its counts all derive
from ADR 0011's predicate, through `displayed_status` and one clock. There is
no raw field beside it. `MarketOut` keeps the column, and is now where the
sweeper-health signal is read. This supersedes the claim in "The public
projection derives `status`; `MarketOut` reports the column", and in ADR
0011's #62 amendment, that #5 counts against the column. Amended in ADR 0011.

**Why.** Counts beside a filtered list must agree with it, and with ADR
0011's rule at the same instant. Against the column, a market past its close
time counts as open while every trade is refused. Deriving also makes the
sweep invisible to the counts, since writing CLOSED moves nothing between
buckets.

**Rejected.** *The raw column*, which the #62 amendment anticipated: the
counts disagree with the trade gate for a whole sweep interval, and
indefinitely if the sweep is off. *Derived plus a raw field*: removes the
early-Propose 409 below, and costs a second status on every row for a window
of seconds; declined by ruling.

**Notes.** Accepted cost: proposing gates on the column (ADR 0013), so for up
to one sweep interval a market shown as closed refuses a proposal with
`409 market_not_closed`, which the frontend handles. It errs the safe way.
Reversal trigger: an overview control whose server gate reads the column and
whose early refusal is no longer acceptable. Then the raw column comes back
as a separately named field, as ADR 0011's amendment describes.

---

### D-NEW — Counts cover every status the caller can see, zero-filled, and ignore the status filter

**Date:** 2026-09-29 · **Ticket:** #5 · **Status:** active

**Decision.** The overview response carries `counts`, an object keyed by
every `MarketStatus` value and zero-filled, over every market the caller can
see. Every filter except `status` applies to the counts. With no other filter
on the route today, they are totals.

**Why.** "What needs action" includes `submitted` (waiting to be published)
and `approved` (waiting for settlement), and leaving them out breaks the
invariant that the counts sum to the markets visible. Zero-filling means a
tab never has to guess whether a missing key is 0 or an error. Keyed by value
rather than a list, so SETTLED is an additive key. Ignoring only `status`
makes each count equal the length of that status's filtered list, whatever
else is added later, `q` included.

**Rejected.** *The criterion's five statuses only*: "settled" is unreachable
until #12 and the other two are real work queues. *Counts that apply the
status filter*: every count but one would be zero. *A separate counts route*:
two requests cannot agree by construction, and nothing reads counts alone.

**Notes.** Draft and submitted counts include only the caller's own, so two
administrators see different numbers, and that is correct. Reversal trigger: a
consumer that needs counts without the list, such as a dashboard tile, which
earns a counts-only route.

---

### D-NEW — The overview's list and counts share one clock and one statement

**Date:** 2026-09-29 · **Ticket:** #5 · **Status:** active

**Decision.** The controller reads the clock once. The service selects every
market the caller can see in one statement, ordered, derives each status in
Python with that clock, then counts all of the rows and filters the same rows
for the list. This is `count_by_status`'s existing approach, widened to the
caller's scope.

**Why.** Two statements under READ COMMITTED each take their own snapshot, so
a publish, proposal, approval, rejection, early close or autosave committing
between them splits the counts from the list. One statement has one snapshot
by construction. The clock follows "One clock per request, read at the
controller, Python's not the transaction's": this read decides nothing and
writes nothing.

**Rejected.** *Two statements at REPEATABLE READ*: while the route is
unpaged, one statement gives one snapshot with no isolation setting and one
round trip fewer. REPEATABLE READ is the move once paging splits the read, as
the reversal trigger below says. *Grouping by status in SQL*: restates the
substitution in SQL, the sixth copy "One clock per request…" refused. *Two
statements at READ COMMITTED*: the split above.

**Notes.** It reads every visible row on every request, with no paging.
Reversal trigger: the route gains paging (#104). The page is then no longer
every row, so the counts need their own statement and the read moves to
REPEATABLE READ to keep one snapshot. The test's mutation is "read the counts
in a separate statement", and it must fail under that mutation.

*Superseded in part 2026-10-05 by #210: "The admin overview pages by keyset,
settled markets last, and reads its counts in the same REPEATABLE READ
snapshot". The reversal trigger fired; the clock is still read once.*

---

### D-NEW — The admin overview orders by soonest close, missing close times last, then id; #105's order is the trader browse's

**Date:** 2026-09-29 · **Ticket:** #5, #105 · **Status:** active

**Decision.** `close_time ASC NULLS LAST, id ASC`, for the unfiltered view and
every status filter alike. #105's most-recently-closed-first applies to
`GET /public/markets` only.

**Why.** For open markets it is the criterion as written. For closed,
pending-resolution and approved markets, oldest close first is the one waiting
longest for an administrator, so each tab reads as a work queue in arrival
order, which is the story's "what needs action". A trader scrolling closed
markets wants recent results, a different reader with a different goal, so
the two views disagree on purpose. `NULLS LAST` is Postgres's ASC default and
is written out anyway; it puts close-time-less drafts at the end. `id` makes
the order total, which a cursor will need.

**Rejected.** *#105's order here*: pushes the longest-waiting items to the
bottom of an action queue. *An order per status*: more rules for a reader to
learn, and none of them earned yet. *Still-trading first, like the public
browse*: the criterion asks for close time, not grouping.

**Notes.** Reversal trigger: SETTLED exists. Settled markets are terminal and
would pile up at the top of the unfiltered view, so [3.4] #12 decides whether
that view sinks or excludes terminal statuses.

*Reversal trigger decided 2026-10-05 by #210: "The admin overview pages by
keyset, settled markets last, and reads its counts in the same REPEATABLE READ
snapshot". Settled markets sort last, built before SETTLED exists.*

---

### D-NEW — Settled is #12's filter and count, not #5's

**Date:** 2026-09-29 · **Ticket:** #5, #12 · **Status:** active

**Decision.** [2.1] #5 ships the six statuses that exist. "Settled" moved to
[3.4] #12, which adds SETTLED to `MarketStatus` and `PublicMarketStatus`, and
carries the criterion that it appears in the overview's filter and
zero-filled counts and on the trader browse.

**Why.** No `MarketStatus` member exists, so no fixture can create a settled
market and the column cannot hold one. A settled filter in #5 would be
untestable, and a `settled: 0` count could only be asserted as a constant: a
test that passes with the feature broken. #12 is the first ticket that can
create one to count.

**Rejected.** *Adding the member in #5 with nothing able to reach it*: a
status no path writes, and a filter tab that is always empty. *A hard-coded
`settled: 0`*: decorative, and wrong the day #12 lands if nobody remembers it.

**Notes.** The overview zero-fills over `MarketStatus`, so the count appears
once the member exists. The trader browse reads `PublicMarketStatus`, so #12
must add it there too, or settled markets vanish from `/public/markets`: that
is `_visible`'s allowlist working as designed. Reversal trigger: none. It
reverts by #12 landing.

*Fulfilled 2026-10-10 by [3.4] #12. This entry is history. SETTLED reached
`MarketStatus`, `PublicMarketStatus`, the overview's filter and zero-filled
counts, and the trader browse in PR 1 (#227). It is marked here in PR 7, with
the stack's other two.*

---

### D-NEW — Stopped markets sort by when trading stopped

**Date:** 2026-10-04 · **Ticket:** #105 · **Status:** active

**Decision.** `GET /public/markets` keeps still-trading markets first, by
`close_time ASC`. Every other market — closed, pending resolution, approved,
and stopped-but-unswept — follows, by `LEAST(close_time, closed_at) DESC`:
most recently stopped first. `id ASC` breaks ties in both groups. Which group a
market is in comes from `open_for_trading(now)` (D-022), never from the status
column. The same order applies under every `status` filter and search.

**Why.** A trader scrolling past the open markets wants recent results, not
ones from six months ago. `LEAST` is when trading actually stopped, read from
the two columns that record it. Swept, that is `close_time`, because the sweep
writes `closed_at` a little later. Unswept, `closed_at` is NULL and Postgres's
`LEAST` skips it. Closed early ([2.3] #7, ADR 0014), it is `closed_at`, which
is before a `close_time` that is still in the future. Sorting on `close_time`
alone would put an early-closed market at the top of the stopped group for
weeks. Sorting on `closed_at` alone would be wrong the other way. The sweep
stamps `closed_at` when it runs, so a market whose `close_time` passed days ago
and was swept just now would sort as if it had only just stopped. An unswept
market has no `closed_at` at all, and Postgres sorts NULLs first under `DESC`.

**Rejected.** *Approved markets at the top of the stopped group*, because they
are about to pay out. That is a second rule rather than a special case of
this one, and a trader who wants them already has `status=approved`, which
isolates them. *An order per status*: more rules to learn and nothing yet that
earns one. *This order on the admin overview*: see "The admin overview orders
by soonest close, missing close times last, then id; #105's order is the
trader browse's".

**Notes.** The two sort keys are `CASE` expressions in
`service/browsing.py`, each NULL outside its own group, so each sorts only its
own markets. The public card carries `close_time` and not `closed_at`, so a
client that re-sorts by `close_time` misplaces an early-closed market;
`docs/api/market-service.md` tells the frontend to keep the server's order.
Keyset pagination ([X-1] #104) must encode this order: the group, the group's
own key (`close_time` while trading, the stop time after), and `id`, with the
key compared ascending in the first group and descending in the second. #104's
criterion lists `close_time` as the middle key, which no longer holds for the
second group. How the groups hold under paging, with the clock moving between
reads: "The public browse pages by keyset, grouped by the first page's clock".

---

### D-NEW — A market_service 404 is remembered for ten seconds, per process

**Date:** 2026-10-04 · **Ticket:** #115 · **Status:** active

**Decision.** `market_terms.fetch` remembers every market id market_service
answered `404` for, and a repeat inside the window raises `MarketNotFound`
with no HTTP call. The window is **10 seconds**
(`_NOT_FOUND_TTL_SECONDS`). At most **10,000** ids are held at once
(`_NOT_FOUND_MAX_ENTRIES`), and the oldest is forgotten first. Only a 404 goes
in. A 503, a timeout or a 401 is never remembered. The memory is a
`core/not_found_cache.py::NotFoundCache` held by `service/market_terms.py`,
one per process.

**Why.** A well-formed id that market_service does not know never gets a
book, so every request for it took the cold path again: one outbound call per
request, however often the same id was repeated ("The cold path holds no
connection across the terms pull" left this open). Since ADR 0017 the gate
makes the same call on every non-replay trade. This bounds repeats of the
**same** unknown id to one call per window. It does nothing for **distinct**
ids: each new random UUID is still one call to market_service, and only a
rate limit closes that (see Open). The memory sits in `fetch` rather
than in either caller, so the gate and `books.ensure_open` are both covered,
and a 404 one of them saw spares the other its call.

*The window* is the price, and it is paid by exactly one person: whoever asks
about a market in the ten seconds after it is published, having asked about
it once just before. A draft or submitted market is a 404 because
`get_published` makes it indistinguishable from a missing one. So a permanent
entry would 404 a real market for the life of the process. Ten seconds is
short enough that nobody waits for it in practice, and long enough to turn a
tight loop into one call per ten seconds per id.

*The bound* is about memory, not correctness. An entry is a UUID and a float,
so 10,000 of them is about two megabytes. Forgetting one early costs one
more call to market_service, never a wrong answer. Every entry gets the same
window, so insertion order is also expiry order, and an `OrderedDict` gives
both expiry and eviction from one end.

*Only a 404* because the other failures are facts about the dependency or
the caller, not about the market (D-030). Remembering a 503 would keep a
recovered market_service refused for ten seconds. Remembering a 401 would
refuse a different trader's valid token.

**Rejected.** *Redis.* Shared memory across replicas would save at most one
call per replica per window, and it would put a network round trip and a new
failure mode in front of the call it exists to save. The bus is also
deliberately optional in this service (D-047): a Redis outage must not change
what a trade is told. *Postgres.* A table of ids that are not markets, written
on the request path by the one service that holds no grant to know what a
market is, and cleaned by something. It would also take a connection, which
is the resource "The cold path holds no connection across the terms pull"
and #115 exist to stop holding across this call.
*Holding it on `app.state` beside the terms client* (D-047's shape). That
means another argument through six functions for state that, unlike a client,
needs no closing. The suite empties the production instance per test through
an autouse fixture instead (`unit_test/conftest.py::not_found_clock`), which
also puts it on a clock the test moves, so its real window and bound are the
ones tested. *A third-party TTL cache.* A dependency for
twenty lines of the standard library.

**Accepted.** A 404 for a market that *has* a book is market_service
answering wrongly, and the gate turns it into `503 market_terms_unavailable`
(ADR 0017). It is remembered like any other 404, so the gate keeps answering
503 for up to ten seconds after market_service recovers. That is the same
window, on a state that is already a market_service fault. The gate does not
forget the entry when it finds a book, because that would need a second way
into the memory, written from a branch outside `fetch`, for a rare incident
whose whole cost is ten seconds of a 503 the client is told to retry.

**Reversal trigger.** Enough replicas that a looped id reaches market_service
`replicas / 10` times a second and that matters. Or a publish that has to be
tradable the instant it lands, which would need market_service to tell the
ledger rather than the ledger remembering.

---

### D-NEW — The public browse pages by keyset, grouped by the first page's clock

**Date:** 2026-10-04 · **Ticket:** #104 · **Status:** active

**Decision.** `browse` returns at most `limit` markets and a keyset cursor
for the next page. `GET /public/markets` takes `limit` and `cursor` and returns
`next_cursor`. The cursor carries four fields: `as_of`, the first page's
instant; which group the last market was in at `as_of`; its key in that group
(`close_time` while trading, `LEAST(close_time, closed_at)` once stopped); and
its id. Every later page groups and compares as of that `as_of`, through
`service/closing.py::was_open_for_trading_at`, which reads only `close_time`
and `closed_at`. The `status` filter and the status on each card still read the
request's own clock (D-025). The cursor does not bind `q` or `status`.

**Why.** Keyset, not OFFSET, for the audit feed's reason: a market published
between two reads would shift an OFFSET window, showing one market twice and
hiding another. The order has two groups and the clock moves markets between
them, so a cursor read against a moving clock would regroup a market whose
close passed between two reads: listed once while trading and again among the
stopped. Freezing the grouping instant ends that.

The grouping predicate is time-only because an early close ([2.3] #7, ADR 0014)
writes `status = closed` at once, and `open_for_trading(as_of)` reads the
status: under a frozen `as_of` it would still regroup a market closed by hand
between reads. The time-only test is stable. Every exit from OPEN stamps
`closed_at` (the sweep and `close_early`) and nothing clears it (a rejection
leaves it, ADR 0016). Both group keys are fixed once a market is in its group:
`close_time` is frozen at publication (ADR 0008), and a stop time cannot move
once trading has stopped, since a sweep's `closed_at` is never before
`close_time` and an early close after `close_time` is refused (ADR 0014).

The filter and displayed status stay on the real clock so a market that closed
mid-browse shows its true status. Under `status=open` it drops out of later
pages: a correct non-match, never a repeat.

Under `status=closed` the same move runs the other way. A market that stops
trading mid-browse, whether its `close_time` passes or an administrator closes
it early, is still in the trading group at the frozen `as_of`, but the filter
now matches it, so it is absent from every later page. It is never shown twice
and nothing shifts: it is missing from the walk, as a market inserted ahead of
the cursor is. A fresh first page lists it.

**Rejected.** *OFFSET*: repeats and skips under inserts. *Grouping by
`open_for_trading(as_of)`*: an early close between reads lists a market twice.
*Grouping by each request's own clock*: a market whose `close_time` passes
between reads is listed twice. *Binding `q` and `status` into the cursor*: the
order is the same under every filter, so a position means the same under all
of them; refusing an old cursor under a new filter would only add an error.
*The four-field shape in `shared/paging.py`*: one caller, below ADR 0012's bar;
`shared/` gained only the N-field primitives its two-field cursor is now built
on.

**Supersedes** the grouping sentence of "Stopped markets sort by when trading
stopped" (#105): group membership comes from `was_open_for_trading_at(as_of)`,
not `open_for_trading(now)`.

**Notes.** At `as_of = now` the two predicates agree on every published market,
so a first page is exactly #105's order; `test_closing.py` asserts it. A draft
may read True under the time-only predicate, which is why `browse` applies
`_visible()` first. `as_of` is the container's clock and an early close stamps
Postgres's (#179), so a close in the milliseconds of skew between them could
regroup one market for a reader whose first page fell in that window; accepted
for a display read, as D-025 accepts the same skew. Page sizes are the audit
feed's settings and defaults, 50 and 200, clamped rather than refused; the
route applies them, clamped rather than refused. A bad
cursor is the same `400 malformed_cursor` the audit, auth and ledger services
answer ("A cursor is decoded before the query runs"). The admin overview is
paged by "The admin overview pages by keyset, settled markets last, and reads
its counts in the same REPEATABLE READ snapshot" (#210).

---

### D-NEW — The suites rebuild from the models per test, and guard tests hold the migrations to them

**Date:** 2026-10-04 · **Ticket:** #75 · **Status:** active

**Decision.** Each `unit_test/conftest.py` still builds its schema from
`Base.metadata`, as before #75: market and ledger drop and recreate it, auth
creates what is missing and empties the tables. None of them runs the
migrations. Instead, each of auth, market and ledger has a
`unit_test/test_migrations.py` that migrates an empty schema and checks the
result is exactly what the models build, through Alembic's comparison and
through the Postgres catalog. Market and ledger also assert by name what
Alembic's comparison cannot see: `ix_markets_due_close`'s predicate and the
append-only trigger.

**Why.** #75's scope said "conftest migrates to head". Migrating per test runs
every revision for every database test, and that cost grows with every revision
added. A rebuild from the models costs the same forever. What a migrate-per-test
suite would catch, the migrations disagreeing with the models, the guard tests
catch directly, once per run.

**Rejected.** *Migrating per test*, which makes every test slower forever to
learn what one guard test says. *Migrating once per session and emptying the
tables per test.* The ledger's append-only trigger refuses TRUNCATE and
DELETE on `ledger.entries`, so the ledger could be emptied that way only by
disabling the trigger the suite exists to exercise (`ledger_svc` owns the
table and could). And unless the session fixture also emptied the schema
first, a test database already at head would ignore a revision edited after it
ran: the version table says it is current, so nothing runs again. The market
and ledger conftests drop and rebuild so that a new column reaches the test
database, and that would quietly stop being true.

**Notes.** CI still runs `migrate.py` against its empty test database before
the suite, as the service's own role, so a revision that cannot apply under the
real grants fails the job. ADR 0020 says when this reverses.

---

### D-NEW — The migrate step reads DATABASE_URL alone, not the service's settings

**Date:** 2026-10-04 · **Ticket:** #75 · **Status:** active

**Decision.** `shared.migrating.migrate_from_environment` reads
`DATABASE_URL` straight from the environment, and exits 2 if it is missing.
Compose gives each `<svc>-migrate` that one variable and nothing else.

**Why.** The services' `Settings` also require `JWT_SECRET`. A step that only
issues DDL has no use for a signing key, and every container that holds one is
one more place it can leak from.

**Rejected.** *Using `core.config.get_settings()`*, which puts the signing key
in one more container for the sake of one shared parse of a single URL.

**Notes.** Each service's `test_compose_starts_..._only_after_its_migration_succeeds`
asserts that `JWT_SECRET` is not in its migrate step's environment. ADR 0020.

---

### D-NEW — The admin overview pages by keyset, settled markets last, and reads its counts in the same REPEATABLE READ snapshot

**Date:** 2026-10-05 · **Ticket:** #210 · **Status:** active

**Decision.** `overview` returns at most `limit` markets and a keyset cursor.
The order is a group, then `close_time ASC NULLS LAST`, then `id ASC`: group 1
is a settled market and group 0 every other, so settled markets come last. The
cursor carries four fields on `shared/paging.py`'s format: the group, whether
the market has a close time, the close time (empty when it has none) and the
id. Nothing in the order reads the clock. The page and the counts are two
statements in one transaction at REPEATABLE READ, reading one `now`. The
counts are `count_by_status`, widened to the caller's scope, over every market
the caller can see whatever the filter and the cursor. The status filter is
the browse's `_status_matches`, in the query. The cursor does not bind
`status`. Until [3.4] #12 adds SETTLED (#227), `_is_settled` is constant false
and every market is in group 0. `GET /markets/overview` takes `limit` and
`cursor` and returns `next_cursor`, with the browse's page sizes (50 and 200,
clamped rather than refused).

**Why.** Keyset rather than OFFSET for the browse's reason ("The public browse
pages by keyset, grouped by the first page's clock"). Settled markets are
finished, and an action queue ordered by oldest close would put every one of
them first; "The admin overview orders by soonest close…" left that to #12,
and #12's plan wanted the sink, so it is built here and the cursor format does
not change when SETTLED lands. A draft's close time can be null, so the cursor
records the nulls-last group as well as the value, and a page boundary between
two undated drafts can be named. Paged, the list is no longer every row, so
the counts need their own statement; REPEATABLE READ gives both statements one
snapshot, which is the reversal trigger "The overview's list and counts share
one clock and one statement" recorded. A read-only REPEATABLE READ transaction
cannot fail with a serialization error, so nothing retries. The level is set
by `session.begin()` and then `session.connection(execution_options=...)`:
`begin()` raises on a session with a transaction already open, where
`connection(...)` alone would only warn and read at READ COMMITTED. A route's
session arrives with none open.

**Rejected.** *Tallying the page*: the counts of fifty rows, not of every
market. *One statement*, with the counts as a window function or a scalar
subquery: derives the status in SQL, the copy D-024 and "One clock per
request…" refuse. *A frozen `as_of`, as the browse has*: nothing in this order
reads the clock. *Ordering by the boolean itself*: Postgres refuses
`ORDER BY false` ("non-integer constant in ORDER BY"), and `false` is what the
boolean is until SETTLED exists. *Binding `status` into the cursor*: the
browse's reason.

**Supersedes** the one-statement half of "The overview's list and counts share
one clock and one statement" (one clock stays), and decides the reversal
trigger of "The admin overview orders by soonest close, missing close times
last, then id; #105's order is the trader browse's": settled sinks.

**Notes.** Two moves the order cannot see are accepted, not engineered around.
Autosave changes a draft's `close_time`, so a draft edited between two page
reads can be listed twice or missed until a fresh first page; freezing drafts
would break autosave. Once SETTLED exists, a market settled between two page
reads changes group in the unfiltered view the same way. The admin page shows
an id once (#235). The status filter and each row's status read the request's
clock (D-025), so under `status=open` a market whose close passes mid-scroll
drops out of later pages, and under `status=closed` one that sorts before the
cursor is missing until a fresh first page. `_status_matches` and the Python
derivation differ only for an OPEN market with no close time, which
publication makes unreachable ("A NULL `close_time` on an OPEN market means
two different things", under Open below): it is counted closed and listed
under neither filter. `count_by_status` has a production caller again, so its
dead-code allowlist entry goes. When #227 lands, `_is_settled` returns
`Market.status == MarketStatus.SETTLED`.

*Amended 2026-10-20. Decided by ADR 0025, and not built yet.* The cursor's
first field, the `settled` flag in market_service's `core/paging.py`
(`OverviewPosition.settled`), is renamed `finished`, and group 1 becomes a
settled or a voided market. The encoding does not change, `"1"` or `"0"`, so a
cursor issued before the change decodes to the same place. A market voided
between two page reads changes group the way a settled one does, and is
accepted the same way.

---

### D-NEW — Settlement is the ledger's request, and market_service is told after the payout commits

**Date:** 2026-10-04 · **Ticket:** #12 · **Status:** active

**Decision.** An administrator calls `POST /ledger/markets/{id}/settlement`
with an empty body. The ledger reads the market's status, approved outcome
and `settleable` from the public detail, opens the book if it is cold, pays
everyone under the book lock, and commits. Only then does it call
`POST /markets/{id}/settle` on market_service, forwarding the administrator's
token. Both services answer a repeat with `200` and write nothing. ADR 0019.

**Why.** No transaction can span both services, so something has to come
second. With the money first, the worst partial state is "paid, still shown as
approved". That market stays in the overview's approved queue, which is exactly
the prompt to repeat the request. The other order leaves "settled, nobody paid",
and nothing prompts a fix.

**Rejected.**
- *market_service marks SETTLED and then calls the ledger.* That is a dual
  write, and it needs a ledger route that takes the winner as input.
- *market_service checks with the ledger before it writes.* That creates an
  HTTP cycle between the two services.

**Reversal trigger.** A service credential exists. market_service's route then
requires it, and the direct-call gap below closes.

**Notes.** The direct-call gap is accepted under ADR 0007's trust model. Any
administrator can call `/markets/{id}/settle` directly. A market settled that
way pays nobody, sinks out of the overview's approved queue and looks finished.
The ledger still accepts `settled` with no record, so calling the ledger's route
pays it. The frontend calls only the ledger's route, and
`docs/api/market-service.md` documents the other as the ledger's alone.

---

### D-NEW — A settlement whose last step fails answers `503 settlement_unconfirmed`

**Date:** 2026-10-04 · **Ticket:** #12 · **Status:** active

**Decision.** If the call to market_service's settle route fails after the
payouts committed, the ledger answers `503 settlement_unconfirmed`. The
payouts stand. Repeating the request completes it and writes nothing in the
ledger.

**Why.** Every existing use of `503 market_terms_unavailable` means nothing
happened. Reusing it here would tell an administrator that a settlement failed
when it has paid everybody. The status and the remedy match ("Upstream failures
map to 503, 404 and 401, and the timeout is explicit"), but the principle
behind those codes is that a caller can act differently on each, and here an
administrator does: they need to know that money has moved.

**Rejected.**
- *`market_terms_unavailable`*: it means the opposite.
- *`200` with a warning field*: the request is not finished, and a client that
  reads only the status would never retry.
- *`502`*: a status the frontend has no handler for, and a code is the
  contract anyway.

**Reversal trigger.** None expected. It goes when the last step does, for
example if market_service learns of settlement some other way.

---

### D-NEW — Any administrator may settle, and a resolver role, if one is added, takes it over

**Date:** 2026-10-04 · **Ticket:** #12 · **Status:** active

**Decision.** `CurrentAdmin` on the ledger route, and `get_any` with an admin
guard on market_service's. The creator, the proposer and the approver may all
settle.

**Why.** The two-person rule is spent at approval (ADR 0016). Settlement carries
out a decision that is already made, and chooses nothing. [4.4] #16's split
between creator and resolver is not built: ADR 0007 declined it and #16 is
closed.

**Rejected.** *Excluding the proposer, as approval does*: that guards a choice,
and settlement makes none.

**Reversal trigger.** A resolver role is added to `UserRole`: settlement then
moves to that role on both routes. *When* any administrator may settle is a
separate entry, "Settlement waits for `settleable`, which market_service
derives from `approved_at`".

---

### D-NEW — Neither call to market_service is made holding a database connection

**Date:** 2026-10-04 · **Ticket:** #12 · **Status:** active

**Decision.** The public-detail read runs before the request's first database
statement, or after a rollback on a path that has already read. The settle call
follows the commit with no statement between them, so nothing autobegins a
transaction.

**Why.** "The cold path holds no connection across the terms pull", applied to
a request that calls out twice. A hung market_service must cost this request its
latency, not a pooled connection or a lock on the book.

**Rejected.** *Making the settle call inside the transaction so that one
failure rolls everything back*: the book lock would be held across a remote
round trip, and a hung market_service would stop trading in that market. The
rollback it buys is also wrong, because a failed call would un-pay every
winner.

**Reversal trigger.** A step that has to touch the database between the commit
and the settle call. The connection-release test fails on that change, and the
step moves rather than the test.

---

### D-NEW — APPROVED is read before the book lock, because send-back and settlement never overlap

**Date:** 2026-10-04 · **Ticket:** #12 · **Status:** active

**Decision.** The ledger trusts an `approved` and `settleable` answer read
before it locks.

**Why.** ADR 0017 already argues that the ledger's lock holds nothing still in
market_service. The read is also stable. APPROVED has two exits: SETTLED,
which the ledger accepts too, and back to CLOSED by send-back ([3.3] #11),
which ends at `window_ends_at`. Every other write path in market_service
refuses an APPROVED market: save and publish through the frozen table, close
early and propose through the resolution table, approve and reject through
`_proposal_to_decide`. The sweep touches only OPEN markets. Settlement starts
five minutes after send-back ends, so no send-back can start once `settleable`
is readable, and one that started in time has had five minutes to commit.

The gap is for one race. A send-back checks the clock just before
`window_ends_at` and commits just after. A settlement's unlocked read lands in
between, sees `approved` and `settleable`, and pays the old winner. The
send-back then commits, but the settlement record is written, and "On a
repeat, the recorded outcome governs" keeps the old winner paid and recorded
for good.

**Rejected.** *Re-reading the status after the commit*: it would close no
window, since the read is remote, and it would cost a second call.

**Reversal trigger.** Any further transition out of APPROVED, or send-back
allowed past `window_ends_at`.

*Amended 2026-10-20. Decided by ADR 0025, and not built yet.* The reversal
trigger fires: [BE] #221's void moves APPROVED to VOIDED, a further transition
out of APPROVED. The read is still safe, for a different reason on each side.
On the ledger, settlement and the void both write the one
`ledger.market_results` row under the book lock before market_service hears of
either, so whichever commits its row first wins and the other writes nothing.
In market_service, the void step accepts APPROVED until the stored end plus
five minutes and the settle step from that instant, both on
`clock_timestamp()` after the row lock. What remains is the direct-call gap,
already accepted. ADR 0025, "Reading APPROVED before the lock is still safe",
and ADR 0019's 2026-10-20 amendment carry the argument in full.

---

### D-NEW — `ledger.market_settlements` is the latch, and positions and `q` are never written

**Date:** 2026-10-04 · **Ticket:** #12 · **Status:** active

**Decision.** There is one row per settled market: `market_id` (primary key),
`outcome_id`, with `(market_id, outcome_id)` a composite foreign key into
`market_outcomes`, and `settled_at`. It is read under the book lock by settlement, which writes nothing if
the row exists, and by the trade path, which then refuses
`409 market_closed`. Positions and `q` are untouched. It is a new table, and
whichever of this stack's ledger-model PR and [F-5] #75's ledger PR merges
second adds its Alembic revision.

**Why.** It keeps a record of what was held and paid. It keeps "Shares
outstanding equal the sum of positions, so the outstanding check is a backstop
on the trade route" true without new code. It gives the portfolio and history
something of the ledger's own to join, with no call to market_service. The
trade path's status gate already refuses a SETTLED market; the latch covers a
trade that passed the gate and then queued on the book lock behind the
settlement.

**Rejected.**
- *Zeroing positions*: it loses the record, and the portfolio would have
  nothing to show.
- *A `settled_at` column on `market_books`*: a status on a table ADR 0017
  keeps status-free.
- *Relying on the status gate alone*: it refuses before the lock, not under
  it.
- *Storing what it paid (a total, a holder count, the residue)*: a stored sum
  of ledger rows, the second source of truth ADR 0009 refuses, and nothing
  reads it. The entries are the record of the money, and the audit entry
  already carries the figures for a reader of the log.

**Reversal trigger.** A reader that needs a settled market's shares to read as
zero and cannot join the record, or any path that could reopen a settled
market.

**Notes.** [BE] #221's void and refund is a later user of `post_all` and of
this record, and is not designed here. `outcome_id` is non-null, because a
settlement always has a winner, so a void may need a record shape of its own.

*Superseded in part 2026-10-09 by [3.4] #12: the table's name, `outcome_id`'s
nullability, `settled_at`, and the Notes' "a void may need a record shape of
its own". See "`ledger.market_results` is one record per finished market, with
a `kind`" below, and ADR 0019's amendment of 2026-10-09. The latch, its readers
under the book lock, the untouched positions and `q`, and the rejected
alternatives stand. `ledger.market_settlements` elsewhere in this file now
means `ledger.market_results`.*

---

### D-NEW — On a repeat, the recorded outcome governs

**Date:** 2026-10-04 · **Ticket:** #12 · **Status:** active

**Decision.** If the settlement record exists, the ledger answers from it.
What market_service reports now is not read for the payout. Nothing is paid
again, whatever market_service reports.

**Why.** Settlement is one-way, and money that has been paid cannot be
recalled. If a later answer from market_service could steer a repeat, any
inconsistency between the two services would turn into a second payout.

**Rejected.** *Comparing the two and refusing on a mismatch*: it leaves an
administrator with a market they can neither finish nor fix. The step that
remains is marking SETTLED, and that doesn't depend on which outcome is named.

**Reversal trigger.** A settlement can be reversed. Nothing planned does this.

---

### D-NEW — One settlement is one commit, through `posting.post_all`

**Date:** 2026-10-04 · **Ticket:** #12 · **Status:** active

**Decision.** Every winner's payout is its own transaction of kind
`SETTLEMENT`, keyed `settlement:{market_id}:{user_id}`, with one leg on that
user's account ("A transaction puts at most one leg on any USER account"). The
residue is one more, of kind `SETTLEMENT_RESIDUE`, keyed
`settlement_residue:{market_id}`. Both keys are derived by the server; no
request supplies any part of them. All of them, the record and the audit entry are posted
by `posting.post_all`, which commits once. `post_all` is a new function in
decker757's `posting.py`, and its shape waits on his agreement.

**Why.** Two reasons, neither of them speed:
- *Atomicity.* A settlement that is half paid cannot be labelled, and cannot be
  finished idempotently.
- *The lock.* `post` commits, so a loop of `post` calls would release the book
  lock after the first winner, and a trade could land mid-settlement.

**Rejected.**
- *A loop of `post`*: see above.
- *One transaction with a leg per winner*: it breaks the one-leg-per-user
  invariant from the other side, and turns the history into one row shared by
  a thousand users.

**Reversal trigger.** "The book's writes share `posting.post`'s commit, and
nothing may follow it" changes so that callers own the commit. A speed
measurement is **not** a trigger.

**Notes.** `_refuse_overdrafts` checks USER accounts only. On an
under-subsidised market, the pool goes negative between the payouts and the
residue, inside one uncommitted transaction. No reader can see that, and the
pool ends at `0.0000`. Do not reorder the batch to avoid it.

*Amended 2026-10-09 by [3.4] #12: `post_all`'s shape is agreed. See
"`posting.post_all` takes a list of `Movement`s, commits once, and keeps
`post`'s SAVEPOINT" and the five entries after it.*

---

### D-NEW — A settlement's first stamp follows the newest entry on every account it locks

**Date:** 2026-10-04 · **Ticket:** #12 · **Status:** active

**Decision.** The first transaction in the batch is stamped one tick after the
newest entry on the union of the locked accounts: the pool, PLATFORM and every
winner. Each later transaction is stamped one tick after the one before.

**Why.** "A movement's timestamp is fixed under its account locks, not trusted
from the clock", applied to a batch. Stamping from the pool's entries alone
lets a winner with a newer entry get a payout listed before it, with a
`balance_after` they never held. The chain keeps no two entries on one account
at the same time.

**Rejected.**
- *One stamp for the whole batch*: PLATFORM and the pool would carry several
  entries with the same time, and "no two entries on one account share a time"
  would break.
- *A per-transaction probe*: correct, but a thousand probes where one union
  query does.

**Reversal trigger.** The feed stops ordering by `created_at`.

*Corrected 2026-10-09 by [3.4] #12: the cost in the second Rejected bullet.
See "`post_all` stamps from one probe per locked account, and movement *i*
lands at the first stamp plus *i* µs" below. The decision and the chain stand.*

---

### D-NEW — The residue empties the pool, in whichever direction it falls

**Date:** 2026-10-04 · **Ticket:** #12 · **Status:** active

**Decision.** Pool minus the payouts:
- positive: posted from the pool to PLATFORM;
- negative: posted from PLATFORM to the pool;
- zero: skipped.

The pool ends at `0.0000`. A market nobody traded returns its whole seed.

**Why.** A settled pool holding credits would be money that nobody owns and
nothing reads. The direction follows ADR 0009: PLATFORM is the only account
meant to absorb the difference.

**Rejected.**
- *Leaving the residue in the pool*: credits stranded in an account that will
  never move again.
- *Paying pro rata when the pool is short*: it pays a winner less than a
  winning share is worth, which is the opposite of the first criterion.

**Reversal trigger.** Per-market fees the platform keeps, or a decision on the
open question about markets subsidised below `b·ln(n)`.

---

### D-NEW — A settled position shows `result` and `payout`, and its value fields are null

**Date:** 2026-10-04 · **Ticket:** #12 · **Status:** active

**Decision.** The row keeps `quantity`, `cost_basis` and `average_entry_price`,
and adds `result` (`paid_out` | `worthless`) and `payout`. `price`, `value` and
`unrealized_pnl` are `null`. It adds nothing to `positions_value`.

**Why.** The payout is already in the balance. Valuing the shares again at the
frozen book is the double count that #12's note warned about. `null` rather
than `0.0000`, because zero is a valuation, and a sold-out position is already
the one place zero means something. This supersedes the realized-outcome
placeholder that "A resolved market's realized outcome is [3.4] #12's
criterion, not [T-4] #24's" declined to add.

**Rejected.**
- *Dropping the row*: the trader loses the record of what they held.
- *`value = payout`*: it adds the payout into `net_worth` a second time.

**Reversal trigger.** A ticket that needs a settled position given a value.

---

### D-NEW — A payout's history row is kind `settlement`, with market, outcome and quantity

**Date:** 2026-10-04 · **Ticket:** #12 · **Status:** active

**Decision.** The row builder maps `settlement`: `market_id`, `outcome_id` (the
winner) and `quantity` (shares paid) come from `context`; `side` and
`average_price` are `null`. A holder of only losing shares has no entry, so they
have no row.

**Why.** This is #12's criterion, moved from #25, and no longer conditional. A
payout with no market on it is unreadable in a history. `average_price` stays
`null` because every payout's price is exactly one, and showing it would invite
reading a payout as a trade.

**Rejected.** *A losing row at zero*: a zero leg is refused by `posting.post`,
and inventing a row with no entry breaks "the history is the ledger presented".

**Reversal trigger.** None. "A resolution payout's history row is [3.4] #12's
criterion, not [T-5] #25's" becomes history when this lands.

---

### D-NEW — Settled markets sink in the overview's unfiltered view, and the counts are unchanged

**Date:** 2026-10-04 · **Ticket:** #12 · **Status:** active

**Decision.** In the unfiltered view the order is settled last, then #5's
`close_time ASC NULLS LAST, id ASC`. Filtered views keep #5's order. The counts
stay every status, zero-filled, summing to what the caller can see.

**Why.** "The admin overview orders by soonest close, missing close times last,
then id" names SETTLED as its reversal trigger: terminal markets would pile up
at the top of a work queue. Sinking keeps them reachable, where excluding them
would break "the counts sum to the markets visible".

**Rejected.** *Excluding settled markets from the unfiltered view*: the counts
and the list would then disagree.

**Notes.** [2.1] #210 builds this order, into its paging and its cursor, so the
cursor format does not change when SETTLED arrives. It is switched off until
SETTLED exists (#227). Whichever of #210 and #227 merges second switches it on,
in one line. If #210 records the order in an entry of its own, the two must
agree.

Switched on in #227: `_is_settled` is now
`Market.status == MarketStatus.SETTLED`.

**Reversal trigger.** A second terminal status.

*Amended 2026-10-20. Decided by ADR 0025, and not built yet.* The reversal
trigger fires: VOIDED is a second terminal status. Voided markets sink with
settled ones, in one finished group, and the order within it is unchanged. The
counts gain a zero-filled `voided` key and still sum to what the caller can
see. `_is_settled` becomes a test for either status, and the cursor's flag is
renamed `finished` (see "The admin overview pages by keyset, settled markets
last…", amended).

---

### D-NEW — SETTLED joins the frozen, resolution and decided status tables

**Date:** 2026-10-04 · **Ticket:** #12 · **Status:** active

**Decision.** `PublicMarketStatus` and `MarketStatus` gain `settled` in one
commit. SETTLED is added to `_FROZEN_STATUS_ERRORS`, to
`_RESOLUTION_STATUS_ERRORS` and to `_proposal_to_decide`'s state check, each
answering `409 market_already_settled`. It is also added to `DECIDED_STATUSES`.

**Why.** Without these, `_refuse_if_frozen`'s `.get()` lets an autosave reach
`_clear_children` and `status = DRAFT`, deleting a settled market's outcomes
and turning it back into a draft. Close early, propose and the decisions would
answer true but useless codes. And `DECIDED_STATUSES` without SETTLED would hide
the winner from traders and from the ledger's own repeat read. ADR 0017 already
requires the two enums to change in one commit.

**Rejected.** *`status not in (DRAFT, SUBMITTED)`*: the frozen table's own
comment refuses it, because each status owes its own error.

**Reversal trigger.** None. These are the fixes the new member needs.

*Amended 2026-10-20. Decided by ADR 0025, and not built yet.* VOIDED joins
`PublicMarketStatus`, `MarketStatus`, `_FROZEN_STATUS_ERRORS`,
`_RESOLUTION_STATUS_ERRORS` and `_proposal_to_decide`'s state check, each
answering `409 market_already_voided`, after the refactor that composes the
tables. It does not join `DECIDED_STATUSES`: a voided market has no winner to
show, even when its approval columns are kept. `PublicMarketStatus` is an
allowlist, so without VOIDED a voided market would vanish from every trader
read.

---

### D-NEW — The ledger writes the settlement's audit entry, through its own seam onto `shared/audit.py`

**Date:** 2026-10-04 · **Ticket:** #12 · **Status:** active

**Decision.** `market.settled` is appended on the payout session, after the
settlement record is staged and before `post_all` commits. The ledger gains
`core/audit.py`, `model/audit.py` and `service/audit.py`. market_service's settle
step writes no entry.

**Why.** ADR 0006: the entry commits with the action. Writing it only once is
what makes "one action, one entry" true. Order matters because
`has_pending_writes` sees ORM writes only, not a Core `insert()`. The latch,
checked before any write, keeps a settled market away from `post_all`'s replay
branch. The record, an ORM add staged first, means the guard still fires if the
latch is ever bypassed.

**Rejected.** *Entries in both services*: two log lines for one action.

**Rule for the implementation.** The three seam files may differ from
market_service's in constants and vocabulary only. If any would repeat
market_service's code beyond that, CLAUDE.md requires the extraction into
`shared/` in the same PR.

**Reversal trigger.** The ledger moves to its own database (ADR 0006's outbox).

*Superseded in part 2026-10-05 by [3.4] #12: "market_service's settle step
writes no entry" and the rejection of "Entries in both services" no longer
hold. See "market_service's settle step appends `market.marked_settled`, so a
direct call is observed" below, and ADR 0019's amendment. The ledger's entry,
its order against the record and the seam rule stand.*

*Corrected 2026-10-09 by [3.4] #12: the order between the record and the
audit insert does not matter. `post_all` reads the guard on entry, by which
point both have been issued. The guard needs the record as an ORM write
pending at that point. See "`post_all` asks about pending writes before it
classifies a match, and replays only a whole batch" below, and ADR 0019's
2026-10-09 amendment to its first trap.*

---

### D-NEW — Settlement lands as a stack of eight PRs, docs first

**Date:** 2026-10-04 · **Ticket:** #12 · **Status:** active

**Decision.** Each PR says `Refs #12` until the last PR that completes #12's
criteria, which says `Closes #12`. Each part targets the one below it. From
PR 2 on, each branch is named `12-settlement-<n>-<slug>`. PR 1 (#227) keeps
`12-settled-status`, because renaming a pull request's head branch closes the
pull request.

| # | Contents | On | Reviewer first |
| --- | --- | --- | --- |
| 0 | docs: ADR 0019, the ADR 0009 and 0017 amendments, these entries, CLAUDE.md, the ADR README | `dev` | decker757 |
| 1 | market: SETTLED in `MarketStatus` and `PublicMarketStatus`, `DECIDED_STATUSES`, `_FROZEN_STATUS_ERRORS`, `_RESOLUTION_STATUS_ERRORS`, `_proposal_to_decide`, `409 market_already_settled`; `settleable` on the public detail, from `approved_at`, the window length setting (24 h default) and the five-minute gap; `settleable` on each admin overview row; `docs/api/market-service.md`'s status and `settleable` edits | 0 | decker757 |
| 2 | market: `POST /markets/{id}/settle` with `409 dispute_window_open`, and the settle route's section of `docs/api/market-service.md` | 1 | decker757 |
| 3 | ledger model: `TransactionKind` `SETTLEMENT` and `SETTLEMENT_RESIDUE`, `market_settlements`, the ledger's audit seam | 2 | |
| 4 | `posting.post_all` | 3 | decker757 (`posting.py`) |
| 5 | `service/settlement.py`, `settleable` on `MarketTerms`, the settle POST on the `market_terms` client, the trade path's latch, with the 5 s, one-commit, rollback, stamp, no-connection, settle-vs-settle, settle-vs-trade and window-boundary tests | 4 | |
| 6 | controller route, error codes, `docs/api/ledger-service.md` | 5 | |
| 7 | reads: portfolio, history; mark the three entries #12 fulfils as history | 6 | |

The settled-last order is built by [2.1] #210's paging, switched off until
SETTLED exists. Whichever of #210 and #227 merges second switches it on, in one
line, with its test: a settled market sorts last in the unfiltered overview,
across a page boundary. If #210's switch lands after PR 7, PR 7 says
`Refs #12`, and #12 is closed when the switch merges.

PR 1 builds `settleable`. [3.3] #11 builds send-back and the countdown on
it, and #12 does not wait for #11.

**Why.** CLAUDE.md: one layer or one behaviour per PR, each green on its own.
Docs go first so that review of the design comes before review of the code.
The three market PRs go to decker757 first because market_service's lifecycle
is his. PR 4 touches `posting.py`, which is his file.

**Rejected.** *One PR*: a reviewer could not follow it. *Code before the ADR*:
the design would be argued in code review.

**Notes.** A PR that changes a contract carries its docs, so
`docs/api/market-service.md`'s status and `settleable` edits land in PR 1,
not PR 2. PR 1 is the one that adds `settled` to the browse, the detail and
the overview, and adds the field.

**Reversal trigger.** Review asks for a different split.

*Superseded in part 2026-10-09 by [3.4] #12: rows 3 and 5 of the table. See
"PR 3 of the settlement stack holds the table, and the audit seam lands with
its caller in PR 5" below. The other rows, the naming and the order stand.*

*Superseded in part 2026-10-09 by [3.4] #12: row 4, which now sits on a new
row 4a, making nine PRs. See "PR 4a extracts `post`'s helpers ahead of
`post_all`, as its own refactor" below. The other rows, the naming and the
order stand.*

*Superseded in part 2026-10-09 by [3.4] #12: row 5, as "PR 3 of the
settlement stack holds the table…" left it. It is now rows 5a, 5b and 5,
making eleven PRs. See "PR 5 lands as three PRs: 5a extracts, 5b extends the
terms client, 5 settles" below. The other rows, the naming and the order
stand.*

*Superseded in part 2026-10-10 by [3.4] #12: row 6, which now sits on a new
row 6a, making twelve PRs. See "PR 6a moves `actor_of` into
`shared/audit.py`, ahead of the route, as its own refactor" below. The other
rows, the naming and the order stand.*

*Amended 2026-10-10 by [3.4] #12: PR 7 says `Refs #12`, not `Closes #12`.
#12 has an open sub-issue, [FE][3.4] #216, and CLAUDE.md's Branches rule
(`Refs` for "a ticket with open sub-issues") wins over this entry's "the last
PR … says `Closes #12`". #12 closes when #216 lands. The #210 condition no
longer decides it, since the switch landed in #227. The other rows, the naming
and the order stand.*

*Merge order, added 2026-10-11 in review: PR 6 (#257) never reaches `dev`
without PR 7 (#258). PR 6 alone pays the winners while the portfolio still
values their positions at the last price, so a winner's net worth, and the
leaderboard built on it, count the win twice until PR 7 lands. Merge #258
into #257's branch first, or merge the two back to back in one sitting.
PR 7 in turn waits for the portfolio page to handle a settled position's
null `unrealized_pnl` on `dev` ([FE][3.4] #216).*

---

### D-NEW — Settlement waits for `settleable`, which market_service derives from `approved_at`

**Date:** 2026-10-05 · **Ticket:** #12, #11 · **Status:** active

**Decision.** market_service derives, on its own clock, from `approved_at`:
send-back is allowed until `window_ends_at`, and settlement from
`window_ends_at` plus five minutes. It exposes the second as `settleable` on
the public detail. The ledger reads it in its status read and refuses an
`approved` market that is not `settleable` with `409 dispute_window_open`.
`POST /markets/{id}/settle` refuses the same, so a direct call cannot settle
mid-window. ADR 0019.

**Why.** [3.3] #11's window exists so that a wrong winner can be sent back
before anyone is paid. A settlement inside it would make the window decide
nothing. One clock decides both edges, the way the clock decides closing (ADR
0011), and the ledger reads the answer rather than recomputing it, because
two clocks deciding one boundary disagree at it. `settleable` gets no default
in `MarketTerms`, for the reason `status` has none.

**Rejected.** *The ledger computing the window from `approved_at`*: a second
clock on the boundary, and a copy of #11's window length in the ledger's
settings.

**Reversal trigger.** None expected while #11's window exists.

*Amended 2026-10-11 by [3.3] #11: market_service now derives both edges from
`dispute_window_ends_at`, not from `approved_at`. Approval stores it as
`approved_at` plus the window as the setting is then. Send-back is allowed
while `now < dispute_window_ends_at`, and settlement from
`dispute_window_ends_at` plus five minutes; `window_ends_at` above means that
column. The ledger is unchanged: it still reads only `settleable`. See
"`dispute_window_ends_at` is stored at approval, and market revision 0002
backfills 24 hours" below, and ADR 0023.*

---

### D-NEW — Settlement opens five minutes after send-back closes

**Date:** 2026-10-05 · **Ticket:** #12, #11 · **Status:** active

**Decision.** `settleable` turns true five minutes after `window_ends_at`, not
at it.

**Why.** A send-back that checks the clock just before `window_ends_at` and
commits just after is invisible to a settlement's unlocked read in between.
Without a gap, that settlement pays the old winner, and the record keeps it
for good. Five minutes is a judgement call: market_service sets no statement,
transaction or request timeout, so nothing bounds how long such a send-back
takes. It is far longer than any request that completes, and short beside a
24-hour window.

**Rejected.**
- *No gap*: the race above.
- *A "settling" claim in market_service that locks the row before the
  payment*: a new status, and a write in market_service before the ledger's,
  which is a dual write, for the same result the gap gives.

**Reversal trigger.** A statement or transaction timeout is added to
market_service under [5.3] #19, the deploy pipeline. That bounds how long a
send-back can take, so the gap can shrink, as long as it still exceeds the
timeout.

---

### D-NEW — A settled market is refused after the locked read and before any write, and the status tables stay separate

**Date:** 2026-10-05 · **Ticket:** #12 · **Status:** active

**Decision.** Every write path refuses a SETTLED market with `409
market_already_settled`. The check runs after authentication and after the
locked read of the market, and before any write. Approve and reject check it
in `_proposal_to_decide`'s state check, before identity, as they already do
for APPROVED. The message says the market is settled and never claims that
payouts were made. `_FROZEN_STATUS_ERRORS`, `_RESOLUTION_STATUS_ERRORS` and
the decision state check each list SETTLED themselves.

**Why.** After the locked read because ADR 0015 puts the check on the
reader: a status read without the lock can be overtaken by the settle step
it is meant to refuse. After authentication so that a trader is told `403`,
not which state an administrator's market is in. Before identity on the
decisions because CLAUDE.md fixes that order for every decision: state, then
identity, then the reason. The message stays silent on payouts because
SETTLED in market_service does not prove the ledger paid: a direct call to
`POST /markets/{id}/settle` can reach SETTLED with no settlement recorded,
and #12 has the ledger repair exactly that case. market_service cannot know
whether credits moved, so it does not say so.

**Rejected.** *Composing the tables from each other*, for example the
resolution table spread from the frozen one. It is a refactor, and CLAUDE.md
keeps refactors out of behaviour PRs. With three tables and one terminal
status, it also saves less than it hides. "SETTLED joins the frozen,
resolution and decided status tables" already explains why each status owes
its own error.

**Reversal trigger.** A second terminal status (#221's void), or a fourth
status table. Either makes the repetition worth a refactor PR of its own.

*Superseded in part 2026-10-05 by [3.4] #12: "every write path" no longer
holds without an exception. The settle step is the one write that answers a
SETTLED market `200` rather than `409 market_already_settled`, as ADR 0019's
step 5 requires. See "The settle step checks in a fixed order, and SETTLED
answers before the clock is read" below. Every other write path is unchanged.*

*Amended 2026-10-20. Decided by ADR 0025, and not built yet.* This entry's
reversal trigger fires: VOIDED is a second terminal status. A refactor PR
composes or derives `_FROZEN_STATUS_ERRORS`, `_RESOLUTION_STATUS_ERRORS` and
the decision state check, with no behaviour change, and VOIDED is then added
once, refused `409 market_already_voided` after the locked read on every write
but a repeated void. See "The market_service status tables are composed in a
refactor PR before VOIDED is added" below.

---

### D-NEW — The dispute window is `dispute_window_seconds`, an int with a 30-day ceiling, and the five-minute gap is a constant

**Date:** 2026-10-05 · **Ticket:** #12, #11 · **Status:** active

**Decision.** market_service's `Settings` gains `dispute_window_seconds: int`,
default `86400`, `gt=0`, `le=2592000`, read from `DISPUTE_WINDOW_SECONDS`.
The five minutes between the window's end and `settleable` stay a constant in
code.

**Why.** [3.3] #11 asks for a configurable window with a 24-hour default.
Seconds in an int mean the same thing to everyone who reads `.env`, and they
allow a 60-second window for a demo. The ceiling turns a mis-typed unit (a
millisecond value, say) into a boot failure instead of a window nobody
notices for a month. `gt=0` because an empty window lets nobody send a result
back, and #11's window exists for that. The gap is not a setting because
"Settlement opens five minutes after send-back closes" depends on it: set to
zero, it reopens the race that entry closes.

**Rejected.**
- *`timedelta`*: pydantic parses `86400`, `P1D` and other spellings, so the
  same env value is easy to misread.
- *Hours*: no short window for a demo or an end-to-end test.
- *Allowing 0*: the window would decide nothing.
- *The gap as a setting*: a 0 there reopens the race.

**Reversal trigger.** A window longer than 30 days is needed.

*Amended 2026-10-11 by [3.3] #11: the setting is read once, at approval, by
`service/settling.dispute_window_ends_at`, and stored on the market as
`dispute_window_ends_at`. A change to `DISPUTE_WINDOW_SECONDS` affects only
approvals made after it: for approvals after revision 0002, a running
countdown never jumps, and a closed window never reopens. The type, the
default, `gt=0`, the ceiling and the constant gap are unchanged. Market
revision 0002 backfills existing approvals with 86400 seconds, written into
the revision, because the migrate step cannot see the setting; on a database
that ran a shorter window, that backfill can reopen a closed window once (see
the Notes of the entry below). See "`dispute_window_ends_at` is stored at approval, and market
revision 0002 backfills 24 hours" below.*

---

### D-NEW — `settleable` is a pure function in `core/`, inclusive at its edge, true once settled, false without `approved_at`

**Date:** 2026-10-05 · **Ticket:** #12, #11 · **Status:** active

**Decision.** `settleable` is always true on a SETTLED market. On an APPROVED
market it is true when `approved_at` is not null and `now >= approved_at +
window + 5 minutes`. It is false on every other status. It is a pure
function in `core/`, `is_settleable(*, approved, settled, approved_at,
window, now)`, which takes whether the status is APPROVED and whether it is
SETTLED as two bools (`core/` cannot import `model/`, as with
`trading_is_open`). `service/` stamps it onto the projection before it is
built. It is never a computed field or a validator.

**Why.**
- *Inclusive*, because #12's boundary test settles a market whose window
  ended "five minutes ago or more". Send-back's edge is exclusive, so no
  instant is both.
- *True on SETTLED, whatever the window*, so the flag is monotonic: once
  true it never goes back, even if `dispute_window_seconds` is raised later,
  and no reader sees it flicker. The overview and [FE] #216 then read one
  consistent flag. It is not what keeps the repair path open. The ledger
  reads `settleable` only on an `approved` market and accepts a `settled` one
  whatever the flag says (ADR 0019, step 1).
- *False on a null `approved_at`*, because the flag fails closed. Approval
  always stamps it, so a null is damage, and the safe answer to damage is not
  to pay.
- *In `core/`, stamped in `service/`*, for the reason "The derived status is
  computed in `service/`, before projection" gives: a computed field re-runs
  when FastAPI re-validates the response, against whatever clock it can
  reach, and that is the failure "One clock per request, read at the
  controller, Python's not the transaction's" recorded in its correction.

**Rejected.**
- *A computed field*: the re-validation bug above.
- *A stored column*: a second authority for something the clock already
  answers (ADR 0011), and a migration ADR 0019 says this needs none of.
- *False on SETTLED*: the flag would go back to false, and the overview
  would disagree with itself about a market it already paid.
- *Testing `DECIDED_STATUSES`* in place of naming APPROVED and SETTLED: a
  status added to that table for its own reasons (#221's void) would become
  settleable by accident. SETTLED also went through the time rule
  that way, so raising the window turned a settled market false.

**Reversal trigger.** A third status that should pay out.

*Amended 2026-10-11 by [3.3] #11: the signature becomes
`is_settleable(*, approved, settled, ends_at, now)`. On an APPROVED market it
is true when `ends_at` is not null and `now >= ends_at + 5 minutes`, where
`ends_at` is the stored `dispute_window_ends_at`. It is false without
`dispute_window_ends_at`, for the same fail-closed reason the null
`approved_at` gave. `service/settling.settleable(status,
dispute_window_ends_at, *, now)` no longer reads the setting. The rejected "a
stored column" is now taken, for Ernest's D2: deriving from the current
setting let a raised window reopen windows that had closed, including on a
market the ledger had already paid, and a send-back could then leave the two
services naming different winners. "True on SETTLED, whatever the window"
still holds, though a raised setting can no longer move a stored end anyway.
See "`dispute_window_ends_at` is stored at approval, and market revision 0002
backfills 24 hours" below, and ADR 0023.*

---

### D-NEW — `settleable` reads the request's one Python clock, and the database-clock trigger is declined again

**Date:** 2026-10-05 · **Ticket:** #12, #11 · **Status:** active

**Decision.** `settleable` on the public detail and on the overview is
derived against the `now` the controller reads once for that request, as
"One clock per request, read at the controller, Python's not the
transaction's" describes. That entry's trigger, for a path that decides or
writes, is declined here for ADR 0017's reason.

**Why.** The ledger acts on this flag, so that entry's trigger fires. It is
declined because the database's clock would cost a round trip on a hot
public read, and the five-minute gap absorbs the skew instead. If a replica
runs ahead by less than the gap, the flag turns true early, but still after
send-back has closed. The ledger pays. If the settle step reads the
database's clock, as the guidance below and "Every decision a market records
is stamped with Postgres's `clock_timestamp()`, read after the row lock" both
say, it finds the market not yet settleable and refuses `409
dispute_window_open`, and the ledger answers `503 settlement_unconfirmed`.
Payouts stand, and a retry once the edge passes completes the request. A
replica that runs behind only delays settlement.

**Guidance, not decided here.** PR 2's settle route and [3.3] #11's
send-back are already covered by "Every decision a market records is stamped
with Postgres's `clock_timestamp()`, read after the row lock", since settling
and sending back are both decisions a market records: each reads
`clock_timestamp()` after its row lock, so the two writers agree with each
other whatever the readers see. Send-back's edge is exclusive: allowed while
`now < window_ends_at`.

**Rejected.** *Postgres's clock for the read*: a round trip on a hot public
read, which ADR 0017 and the one-clock entry both refused, for skew the gap
already absorbs.

**Reversal trigger.** Skew between the services' clocks and the database's
approaches the five-minute gap.

---

### D-NEW — `settleable` is on the public detail and on every admin overview row; `window_ends_at` is #11's

**Date:** 2026-10-05 · **Ticket:** #12, #11 · **Status:** active

**Decision.** `settleable` is a field on the public market detail and on
each `MarketOverviewRowOut`. `window_ends_at` is not exposed by this ticket;
[3.3] #11 adds it with its countdown.

**Why.** The ledger reads the public detail. The settle control lives on the
admin overview ([FE][3.4] #216), and without the flag there it would show on
every approved market for the whole 24-hour window, and each click would be a
`409 dispute_window_open`. The overview derives it with the clock it already
reads once for its list and counts ("The overview's list and counts share one
clock and one statement"), so a row's status and its flag never disagree.

**Rejected.** *The public detail only*: the control would appear mid-window.
*`window_ends_at` now*: it is #11's countdown, and a field with no reader yet
is a contract nobody has asked for.

**Reversal trigger.** #11 adds a countdown that needs the instant on list
views.

*Amended 2026-10-11 by [3.3] #11: the reversal trigger fired. #11's countdown
needs the instant on the admin overview, a list view, and on the trader's
market page. The field is added to both the public detail and every overview
row as `dispute_window_ends_at`, the stored column, rather than as
`window_ends_at`. `settleable` stays as this entry decided. See
"`dispute_window_ends_at` is on the public detail and on every overview row,
as the raw column" below.*

---

### D-NEW — market_service's settle step appends `market.marked_settled`, so a direct call is observed

**Date:** 2026-10-05 · **Ticket:** #12 · **Status:** active

**Decision.** When `POST /markets/{id}/settle` moves APPROVED to SETTLED, it
appends `market.marked_settled` in that same transaction. The entry carries
the acting administrator, the market as target, `decision_snapshot`'s context,
a null `reason`, and as `occurred_at` the `clock_timestamp()` read for the
window check. A repeat on a market already SETTLED writes nothing and logs
nothing. ADR 0019's amendment.

**Why.** ADR 0019 accepts the direct-call gap under ADR 0007's trust model,
and ADR 0007 extends that trust only to administrators who act observed. No
role can read both `market.markets` and `ledger.market_settlements`, so the
audit log is the only place a market that is settled but unpaid can show up.
A normal settlement logs twice, one entry per commit. A
`market.marked_settled` with no `market.settled` before it is the trace the
gap leaves. The decision snapshot is the one approve and reject already log,
so the entry names the proposal and proposer it settled on. A reader can match
it against the ledger's winning outcome without reading the market.

**Rejected.**
- *No entry.* The gap is invisible.
- *An entry only on a direct call.* The route cannot tell a direct call from
  the ledger's.
- *Application logging.* It is not the audit trail.
- *A reconciliation job.* No role reads both schemas.

**Reversal trigger.** A service credential exists and the settle step
requires it. The gap closes, and the entry goes with it.

**Notes.** This supersedes in part "The ledger writes the settlement's audit
entry, through its own seam onto `shared/audit.py`": its "market_service's
settle step writes no entry" and its rejection of entries in both services.
Everything that entry says about the ledger stands.

---

### D-NEW — The settle step answers `200` with `MarketOut`, the same for the flip and for a repeat

**Date:** 2026-10-05 · **Ticket:** #12 · **Status:** active

**Decision.** `POST /markets/{id}/settle` answers `200` with `MarketOut`
whether it moved the market to SETTLED or found it SETTLED already. The two
bodies are identical, because both are read from the locked row, which reads
SETTLED in both cases.

**Why.** The ledger reads only the status code and, on a refusal, the error
code. A repeat is how ADR 0019 repairs every partial failure, so a repeat has
to look like success to its one caller. The body is the whole market, as
close, approve and reject return it.

**Rejected.**
- *`409 market_already_settled` on a repeat.* ADR 0019 requires `200`, and
  the ledger would turn every repair into `503 settlement_unconfirmed`.
- *A different code or a field that marks the repeat.* The ledger would
  branch on a difference that means nothing to it.
- *`204`.* It is the one market write route with no body, for nothing gained.

**Reversal trigger.** A caller needs to tell the flip from the repeat.

**Notes.** `MarketOut` sends JSON numbers ("Two schemas: `PublicMarketOut`
beside an unchanged `MarketOut`"). The ledger reads none of its money fields,
so no float reaches the money path.

*Corrected 2026-10-09 by [3.4] #12: the ledger reads the status code only.
Every answer but `200` is `503 settlement_unconfirmed`, whatever its error
code. See "`market_terms.mark_settled` accepts only a `200`, and every other
answer is `503 settlement_unconfirmed`, logged at ERROR" below.*

---

### D-NEW — The settle step checks in a fixed order, and SETTLED answers before the clock is read

**Date:** 2026-10-05 · **Ticket:** #12 · **Status:** active

**Decision.** The route checks in this order:

1. `401 invalid_token`, then `403 not_an_administrator`.
2. `404 market_not_found`, from `get_any` with the row locked.
3. SETTLED answers `200`, with no clock read.
4. Anything that is not APPROVED is `409 market_not_approved`. That includes
   another administrator's draft or submitted market, which is found and
   refused as close, approve and reject refuse one.
5. Only then is the clock read, and a market that is not yet settleable is
   `409 dispute_window_open`.

A request body is ignored. An APPROVED market with a null `approved_at` fails
closed as `dispute_window_open`.

**Why.**
- *Authentication first,* so a trader is told `403` and not the state of an
  administrator's market.
- *`get_any`,* because any administrator may settle ("Any administrator may
  settle, and a resolver role, if one is added, takes it over"). It is safe
  only for callers that refuse a draft before touching it, and step 4 does.
- *SETTLED before the clock,* because a repeat writes nothing, so there is
  nothing to stamp. It must also answer `200` even if
  `dispute_window_seconds` has been raised since. The check runs after the
  lock, so a repeat racing a flip waits and then answers `200`.
- *One code for every state short of APPROVED,* because ADR 0019 names
  `market_not_approved` and the ledger maps only that. The frozen and
  resolution tables' codes are true, and useless to the only caller.
- *A body is ignored,* because the route reads nothing from it. The ledger's
  own route refuses a body `422` so that a client does not believe it chose
  the winner. This route has one caller, which sends none.
- *A null `approved_at` fails closed,* for the reason given in "`settleable`
  is a pure function in `core/`, inclusive at its edge, true once settled,
  false without `approved_at`": a null is damage, and the safe answer to
  damage is not to settle.

**Rejected.**
- *The clock read before the SETTLED check.* It costs a round trip on every
  repeat, and a raised window could refuse a market already paid.
- *`market_pending_resolution`, `market_not_closed` and the other per-status
  codes.* They are a contract the ledger would have to map for no different
  action.
- *`422` on a body.* It is a schema for a request with no fields.

**Reversal trigger.** The route gains a caller other than the ledger, or a
status other than APPROVED becomes settleable (#221's void).

**Notes.** This is the one write path where SETTLED is not `409
market_already_settled`. "A settled market is refused after the locked read
and before any write, and the status tables stay separate" says "every write
path", but it predates this route, and ADR 0019's step 5 makes the exception.
That entry carries a note saying so.

*Amended 2026-10-11 by [3.3] #11: "An APPROVED market with a null
`approved_at` fails closed" now reads a null `dispute_window_ends_at`, since
step 5 judges the window against the stored end. The order is unchanged.
SETTLED still answers before the clock is read. For approvals after revision
0002, a raised `dispute_window_seconds` can no longer refuse a market already
paid. Two reasons remain: the round trip saved, and revision 0002's backfill
on a database that ran a window shorter than 24 hours, which can give a
market already settled an end in the future. Answering SETTLED first means
such a market is still `200`. See "`dispute_window_ends_at` is
stored at approval, and market revision 0002 backfills 24 hours" below.*

*Amended 2026-10-20. Decided by ADR 0025, and not built yet.* One step joins
the order, between 3 and 4: VOIDED is `409 market_already_voided`, before the
clock is read. The reversal trigger does not fire. VOIDED is never settleable,
and the route keeps its one caller. The ledger accepts only a `200` from this
step, so a VOIDED answer reaches a client as `503 settlement_unconfirmed`, and
only a direct call to market_service's void step can bring that about.

---

### D-NEW — The settle step's window check reads `clock_timestamp()` after the row lock, on the APPROVED branch only, against the stored status

**Date:** 2026-10-05 · **Ticket:** #12 · **Status:** active

**Decision.** On an APPROVED market only, the settle step reads
`_database_now` after `get_any` has locked the row. It passes that value to
`service/settling.settleable` with the stored status. The same value is the
entry's `occurred_at`.

**Why.**
- Settling is a decision a market records, so "Every decision a market
  records is stamped with Postgres's `clock_timestamp()`, read after the row
  lock" applies. The guidance in "`settleable` reads the request's one Python
  clock, and the database-clock trigger is declined again" names this route.
- Send-back reads the same clock after the same lock (#11's criterion). The
  two writers therefore agree about the edge whatever any reader's clock
  says, and the gap separates them.
- APPROVED only, because it is the only branch whose answer depends on time.
- The stored status, because the derivation differs from it only for OPEN,
  which is not APPROVED either way.

**Rejected.**
- *The request's Python clock.* A replica running ahead could settle early.
  The database clock here is the backstop that entry relies on.
- *`func.now()`.* That is the transaction's start, read before the lock wait
  (#138).
- *A clock read before the lock.* Same bug.

**Reversal trigger.** market_service's decision stamps move off
`clock_timestamp()`, or the five-minute gap is replaced by a lock-based claim.

**Notes.** The ledger may see `settleable` true on a replica's clock while
this check refuses it. The route then answers `409 dispute_window_open`, the
ledger answers `503 settlement_unconfirmed`, and a retry completes the
settlement. That is the designed outcome, not a reason to move this check to
Python's clock.

---

### D-NEW — `ledger.market_results` is one record per finished market, with a `kind`

**Date:** 2026-10-09 · **Ticket:** #12 · **Status:** active

**Decision.** The settlement record is `ledger.market_results`:

- `market_id`: the primary key, and a foreign key into `market_books`.
- `kind`: a non-native enum, `settled` only for now.
- `outcome_id`: nullable, still a composite foreign key with `market_id` into
  `market_outcomes`, with `CHECK ((kind = 'settled') = (outcome_id IS NOT NULL))`.
- `recorded_at`.

Neither key has an `ON DELETE`. The trade latch and the settlement read any
row, under the book lock, and never filter on `kind`. `MarketResult` is an ORM
class, so the record stays an ORM add staged before the audit insert (ADR
0019's first trap).

**Why.** [BE] #221's decision 3. One record per finished market is one latch
for every ending, and the primary key stops a market from being both settled
and voided. Taking the shape now means the latch, the settlement and the
portfolio's join are written against it once. A rename costs two docs edits
today, and a revision plus every reader later. The plain foreign key exists
because Postgres skips a composite key when `outcome_id` is null.

**Rejected.**
- *A separate table for voids*: two latches for every reader, and nothing in
  the database stops a market ending both ways.
- *Deferring everything but the name*: cheap DDL either way, but readers would
  be written against a shape that later changes under them.

**Reversal trigger.** #221 closes without a void, or voids get a table of
their own. A revision then drops `kind` and restores `NOT NULL` on
`outcome_id`.

**Notes.** These wait for #221's revision: `reason` and `voided_by`, their
CHECK clauses, and `409 market_already_voided`. Nothing writes them yet, and
nullable columns are cheap to add later. Until a non-settled kind exists, no
test can exercise the plain foreign key or the CHECK's non-settled half;
`test_migrations.py`'s catalog comparison is what holds them to the models.
`kind` has no CHECK on its values, like every non-native enum here; the paired
CHECK is what constrains it. `recorded_at` has no default, like
`market_books.opened_at`: the settlement supplies it.

*Amended 2026-10-20. Decided by ADR 0025, and not built yet.* What the Notes
left to #221 is decided, for ledger revision `0007`: `ResultKind.VOIDED`, and
`reason` (text) and `voided_by` (uuid), both nullable, each set exactly when
`kind = 'voided'` by a hand-written CHECK. The existing CHECK already admits a
voided row with no outcome. Readers still never filter on `kind`, with one
exception: settlement's own lookups, which must refuse a voided row with `409
market_already_voided` rather than replay it as a settlement.

---

### D-NEW — `ledger.market_results` carries no append-only trigger

**Date:** 2026-10-09 · **Ticket:** #12 · **Status:** active

**Decision.** `ledger.market_results` is written once per market and never
updated, and nothing enforces that at the database. It does not get
`ledger.entries`' trigger or one of its own.

**Why.**
- The primary key already refuses a second row for a market.
- If a row were deleted, money still could not move twice. A second
  settlement derives the same `settlement:{market_id}:{user_id}` and
  `settlement_residue:{market_id}` keys. It reaches posting's replay with the
  re-staged record pending, and is refused as `pending_writes_on_replay`. The
  trade path's status gate also still refuses a market market_service reports
  as settled.
- A trigger needs either `reject_mutation` changed, whose message names
  `ledger.entries`, or a second function. Both need SQL in a revision of their
  own ("The ledger's trigger SQL is shared, so editing it needs a new
  revision", ADR 0020), for a table nothing writes twice.

**Rejected.**
- *Reusing `reject_mutation`*: its message would name the wrong table, and
  changing it is a revision.
- *A second trigger function*: a second copy of trigger SQL, guarding against
  a write no code makes.

**Reversal trigger.** Any code path that updates or deletes a row of
`ledger.market_results`.

**Notes.** Unlike `ledger.positions`, nothing updates this table, and unlike
`ledger.entries`, it holds no money. The money is in the entries, and the
trigger there still guards it. The replay claim above depends on `posting`'s
current behaviour; recheck it when PR 4's `post_all` lands.

*Rechecked in PR 4, 2026-10-09: the claim holds. A re-staged record is a
pending ORM write when `post_all` is entered, and `post_all` checks pending
writes before it classifies the match. So the second settlement is
`pending_writes_on_replay` whether its winners match the first or not. If the
match were classified first, a different set of winners would answer
`idempotency_key_reused` instead. Nothing would be written either way, but the
error named above would be wrong. See "`post_all` asks about pending writes
before it classifies a match, and replays only a whole batch".*

*Noted 2026-10-10 by [3.4] #12: settlement's lookup before the lock depends
on this table too. "A recorded settlement replays before any status check;
the lookup before the lock can only short-circuit" treats a row found without
a lock as final. That is safe only while no row is ever updated or deleted,
and nothing at the database enforces it. This entry's reversal trigger
therefore also ends that entry's safety. A path that deletes a row must first
move settlement's lookup under the book lock.*

---

### D-NEW — PR 3 of the settlement stack holds the table, and the audit seam lands with its caller in PR 5

**Date:** 2026-10-09 · **Ticket:** #12 · **Status:** active

**Decision.** PR 3 holds:

- `TransactionKind` `SETTLEMENT` and `SETTLEMENT_RESIDUE`
- `ledger.market_results` and revision `0002`
- one `dead_code_allowlist.py` line for `MarketResult`
- the migration-test edits
- `docs/api/ledger-service.md`'s kinds line

PR 5 gains the ledger's audit seam (`core/audit.py`, `model/audit.py`,
`service/audit.py`). It also decides whether `service/audit.py` is a third
copy or an extraction, and deletes the allowlist line once the trade latch and
the settlement read `MarketResult`.

**Why.** The dead-code check matches by name. In PR 3, the seam's `record` and
`AdminAction` would count as used through market_service's identically named
ones, so untested code would pass unseen. In PR 5, the seam lands with its
caller and with the test of #12's audit criterion. `MarketResult` has no
reader until PR 5 either, and an allowlist line says so where the check can
see it.

**Rejected.**
- *Folding PR 3 into PR 5*: PR 5 is already the stack's largest.
- *The trade latch in PR 3, as `MarketResult`'s caller*: #12's test notes ask
  for the settle-against-trade race test to fail with the under-lock lookup
  removed, and for the PR to name that line. The latch and that test belong in
  one diff, and in PR 3 the latch would guard a state nothing can create.

**Reversal trigger.** Review of PR 3 refuses an allowlist entry for planned
code. The trade latch then moves into PR 3.

**Notes.** Rows 3 and 5 of the stack table now read:

| # | Contents | On | Reviewer first |
| --- | --- | --- | --- |
| 3 | ledger model: `TransactionKind` `SETTLEMENT` and `SETTLEMENT_RESIDUE`, `ledger.market_results` and revision `0002`, the allowlist line for `MarketResult`, the migration-test edits, `docs/api/ledger-service.md`'s kinds line | 2 | |
| 5 | the ledger's audit seam, `service/settlement.py`, `settleable` on `MarketTerms`, the settle POST on the `market_terms` client, the trade path's latch, the allowlist line removed, with the 5 s, one-commit, rollback, stamp, no-connection, settle-vs-settle, settle-vs-trade, window-boundary and audit tests | 4 | |

The kinds, the model and revision `0002` land in one commit, because the model
and the revision alone each fail the migration guard. That commit fails the
legacy adoption tests a second revision ends, and the history's table-set test,
until the next commit updates them, as #246's did. That commit deletes the
adoption tests and adds the kept refusal test, word for word as #246's, so the
two branches' hunks agree.

*Row 5 above is superseded 2026-10-09: see "PR 5 lands as three PRs: 5a
extracts, 5b extends the terms client, 5 settles" below.*

---

### D-NEW — `posting.post_all` takes a list of `Movement`s, commits once, and keeps `post`'s SAVEPOINT

**Date:** 2026-10-09 · **Ticket:** #12 · **Status:** active

**Decision.**

```python
@dataclass(frozen=True)
class Movement:
    idempotency_key: str
    kind: TransactionKind
    legs: list[Leg]
    context: dict[str, Any] | None = None

async def post_all(
    session: AsyncSession,
    movements: list[Movement],
    *,
    now: datetime | None = None,
) -> list[Transaction]
```

`Movement` holds the same keyword arguments `post` takes, as a frozen record.
`post_all` returns one `Transaction` per movement, in input order, whether it
wrote the transaction or replayed it. `now` is one floor for the whole batch,
the same way it is for `post`. `post_all` owns the batch's one commit, so a
caller writes nothing after it.

It stages every transaction and entry inside one SAVEPOINT. On an
`IntegrityError` it re-reads the batch's keys, as `post` does. A race on a key
that the account locks did not queue then ends as `pending_writes_on_replay`
or `idempotency_key_reused`, never as a bare 500.

**Why.** "One settlement is one commit, through `posting.post_all`" needs a
batch that commits once, after the book lock, and it left the shape to
decker757. Because a movement holds `post`'s own arguments, each one reads
like a `post` call, and the checks are visibly `post`'s checks run over a
list. Results come back in the order the caller built the movements, so the
caller can pair each result with its movement without a lookup.

The SAVEPOINT stays because the lock that serialises a race belongs to the
caller, not to `post_all`, and `post_all` cannot see whether one is held.
Without the handler, a race on a key reaches the client as the unique index's
`IntegrityError`, where `post` answers with a code.

**Rejected.**
- *A tuple per movement, or parallel lists*: both are positional, so a reader
  has to count fields to know which one is the key.
- *A `now` per movement*: the chain fixes every stamp after the first, so only
  one floor can ever count.
- *A caller-owned commit*: that is the reversal trigger of "One settlement is
  one commit, through `posting.post_all`". It is not a choice to make inside
  `posting.py`.
- *Dropping the SAVEPOINT because settlement's book lock already serialises
  two settlements*: the book lock is the caller's, and `post_all` cannot rely
  on a lock it cannot see.
- *Results keyed by idempotency key*: the caller already holds the order it
  built, and a dict would make it look its own keys up again.

*Amended 2026-10-13 by #315: `post` no longer has a body of its own. It
posts a batch of one through `post_all`, so this SAVEPOINT and its race
handler are the only copy. See "`post` is `post_all` of one movement".*

---

### D-NEW — An empty batch, or one key twice in a batch, is `MalformedBatch`, a 500

**Date:** 2026-10-09 · **Ticket:** #12 · **Status:** active

**Decision.** Before it locks anything, `post_all` refuses two kinds of batch:
one with no movements, and one where two movements share an idempotency key.
Both raise `MalformedBatch`, a `LedgerError` with code `malformed_batch` and
status 500, and nothing is written.

**Why.** No request can supply a batch, because the server builds every
movement and every key, and the settlement route's body is empty. A repeated
key is always a bug in the server. So is an empty batch, except in the one
edge the Notes accept. That makes it the same kind of error as
`pending_writes_on_replay`, an alarm one layer down, so it takes that error's
status.
- *A repeated key* would otherwise reach the unique index on
  `idempotency_key` inside the SAVEPOINT. The race handler would re-read, find
  no committed row, and re-raise the `IntegrityError`. The result is a bare
  500 from a handler that exists for races, saying nothing about the batch.
- *An empty batch* would otherwise lock nothing, write nothing and still
  commit, taking whatever the caller had pending with it. For settlement,
  that commits a `market_results` row and a `market.settled` entry for a
  payout that moved no money.

**Rejected.**
- *422, as `unbalanced_transaction` is*: a 422 tells the client to fix its
  request, and the settlement request has no body to fix.
- *Returning `[]` for an empty batch*: without a commit, the record and the
  audit entry are left uncommitted while the route answers 200. With one, a
  settlement is recorded with no money moved. Either is a decision about
  settlement, not about posting.
- *Deduplicating a repeated key*: if the two movements match, one is silently
  dropped. If they differ, one movement's money is silently lost.

**Reversal trigger.** A market seeded at exactly one tick, `0.0001`, is
created.

**Notes.** Every book's seed subsidy is positive:
- market_service's `_liquidity_problems` refuses a non-positive seed at
  submission, and `publish` runs it again.
- `books.ensure_open` refuses a non-positive or non-finite seed before it
  writes the book.

"A sell cannot take a market's pool below its seed subsidy" then keeps the
pool at or above that seed until settlement, with one exception, in its own
Notes: "The one exception is the one-tick case in "A cost exactly on a tick
can round one tick against the trader, or toward them on a sell". It needs
about 120·b of skew and moves one tick." That case is a sell of `0.0001`
against `q = [12000, 0]` at `b = 100`. Its true proceeds are a hair under one
tick, the engine returns exactly one tick, and the pool pays a tick it did
not earn.

So an empty batch is unreachable for any seed above one tick. A market with
no winners still posts its residue, and a market with winners posts their
payouts.

A market seeded at exactly one tick, with no winners, could reach a zero
pool. Its settlement would then be `500 malformed_batch` and could not
complete. Accepted as an edge. The fix is either a minimum seed in
market_service's validation (decker757's), or settlement committing its
record and audit entry alone, which would need its own decision.

**Amended 2026-10-18.** A third kind of batch is refused the same way, before
any lock: one whose `Movement.reservation_drop` is below zero, or is nonzero
on a movement without exactly one USER account, or on one that does not debit
that account. All are built by the server, like the key, so all are a bug and
take this entry's 500. See "A refusal from the running check reports the
balance before the batch".

---

### D-NEW — `post_all` asks about pending writes before it classifies a match, and replays only a whole batch

**Date:** 2026-10-09 · **Ticket:** #12 · **Status:** active

**Decision.** Holding the account locks, `post_all` looks up every key in the
batch. If any key already exists:

1. It checks the caller's pending writes first, using the answer it recorded
   on entry, as `post` does. If the session is dirty, it raises
   `PendingWritesOnReplay`, whatever the match.
2. Only then does it classify the match. It replays only when every key
   exists and every fingerprint matches. A replay writes nothing, commits to
   release the locks, and returns the existing transactions in input order.
3. Any other match is `IdempotencyKeyReused`, and nothing is written. That
   means some keys found and others missing, or a fingerprint that differs.

The re-read after the SAVEPOINT's `IntegrityError` gives the same answers.

**Why.** A batch commits all of its transactions or none, so a retry finds
either every key or none of them. Finding some keys and not others means the
same keys were used for different money: for settlement, a different set of
winners. Replaying the part that matched and writing the rest would split one
batch into two commits, which is the half-paid settlement "One settlement is
one commit, through `posting.post_all`" exists to prevent.

The pending check comes first because the caller that reaches the replay
branch is the one with the bug. Settlement always enters `post_all` with its
`market_results` row staged. A settlement that got past its latch is
therefore refused as `pending_writes_on_replay`, the alarm for exactly that
mistake, whether its winners match or not. If the match were classified
first, a partial match would answer `409 idempotency_key_reused`, which is a
client-conflict status for a server bug.

**Rejected.**
- *Replaying the matched subset and posting the rest*: one batch in two
  commits.
- *Classifying before the pending check*: see above.
- *Raising `PendingWritesOnReplay` on entry, before any lookup*: every caller
  enters with writes pending that it means to commit with the batch. That is
  the rejected option in "`posting.post` refuses to replay into a dirty
  session".

**Notes.** This restates ADR 0019's first trap. The guard is read on entry,
so the order of the record and the audit insert inside the caller does not
matter. What the guard needs is the record, as an ORM write, pending when
`post_all` is entered. It is unsafe in three cases:
- the record becomes a Core insert;
- the record is staged on some paths and not others;
- the record is committed before `post_all`.

This corrects the Why of "The ledger writes the settlement's audit entry,
through its own seam onto `shared/audit.py`", which says the order matters.

*Amended 2026-10-13 by #315: `post` is now `post_all` of one movement, so
this check is the only copy. The rule, and the answer recorded on entry,
are unchanged. See "`post` is `post_all` of one movement".*

---

### D-NEW — `post_all` nets each USER account across the whole batch, through `_refuse_overdrafts`

**Date:** 2026-10-09 · **Ticket:** #12 · **Status:** active

**Decision.** First, `_require_balanced` checks each movement on its own.
Then every leg in the batch goes into one flat list for the existing
`_refuse_overdrafts`, which nets each USER account across the batch and reads
a balance only for accounts that lose credits. This runs holding the account
locks, after the replay lookup, as in `post`.

**Why.** The batch commits once, so only an account's end state is ever
visible to another transaction. Netting across the batch applies `post`'s
per-transaction rule at the same boundary, the commit. `_refuse_overdrafts`
already nets by account id, so no new code is needed. Settlement debits no
USER account, so it reads no balance here.

**Rejected.**
- *Checking each movement against a running balance*: correct for any batch,
  but it is a new per-account running total that no caller needs.
- *No overdraft check in `post_all`*: settlement would not notice, but the
  check is what `post` guarantees every caller. Without it, `post_all` is one
  new caller away from an overdraft.

**Reversal trigger.** A batch that debits a USER account in one movement and
credits it in a later one. Netting would accept a batch whose feed shows a
`balance_after` below zero between the two, which is the history bug #187
fixed. The check then becomes a running balance in movement order.

*Amended 2026-10-17. Decided by ADR 0024 (Accepted 2026-10-17), not built yet.*
This entry's reversal trigger fires. A trader's own resting sell can fill in the
cascade their own buy triggered, so one batch debits a USER account and credits
it in a later movement. ADR 0024 replaces the netted check with a running one in
`_refuse_overdrafts`. It walks the movements in order and keeps, for each
USER account, a running balance and a running reservation. Both start at
their values before the batch, and the balance must be at least the
reservation there. After each movement that debits the account, the balance
must be at least the reservation at that point.

Each `Movement` carries the drop in its account's reservation that it
causes, not its cost. A buy-order fill that leaves the order open drops it by
its cost. A fill that exhausts the order drops it by the order's whole
remaining budget, `budget - spent` with `spent` read before the fill,
because the order stops being live. A sell-order fill and a plain trade drop
it by zero. The reservation is read after `accounts.lock` and after the
caller has flushed its order rows, which gives the figure after the batch,
and the batch's drops are added back to it.

A placement's new buy order therefore counts at its full budget from the
start of the batch, whether it fills in part or completely on arrival, and
the check at the start refuses a placement whose budget does not fit in
`available`, `409 insufficient_funds`, even one that would fill completely
for less. That refusal carries `balance`, `reserved`, `available` and
`shortfall`, how much more the user needs, and not `required`; the trade
route's `insufficient_funds` keeps `required`. So a trade has to be
affordable before any resting order fills: neither a self-fill's proceeds nor
a reservation dropped later in the batch can pay for it. ADR 0024 works both
cases through. An account the batch only credits is still not read. The owner
of `posting.py` signs this off before it is built.

*Amended 2026-10-17.* ADR 0024 was Accepted on 2026-10-17. The owner of
`posting.py` signed this off in his approving review of #324: "sign-off 3 is
a yes", and sign-off 3 is this running check in `_refuse_overdrafts`. The
running check is built in the limit-order stack's PR 4a.

---

### D-NEW — `post_all` locks the union of the batch's accounts once, ascending, after the caller's book lock

**Date:** 2026-10-09 · **Ticket:** #12 · **Status:** active

**Decision.** `post_all` collects every account named by any leg and locks
the whole set once, through `accounts.lock`: one statement per account,
ascending by id. On the settlement path the caller already holds the market's
book lock, so the order is the book, then the accounts, the same as on the
trade path.

**Why.** ADR 0009's ascending order prevents deadlock only if each writer
takes all of its account locks in one ascending sequence. Locking the full
set once also means the replay lookup, the overdraft check and the stamp
probes all run with every lock held, as they do in `post`.

**Rejected.**
- *Locking each movement's accounts as it is posted*: across the batch, locks
  would be taken out of ascending order, which is exactly what ADR 0009's
  ordering exists to rule out.
- *One `SELECT ... WHERE id IN (...) ORDER BY id FOR UPDATE`*: it can still
  deadlock (ADR 0009, and `accounts.lock`'s docstring).

**Notes.** The cost, accepted: a settlement holds every winner's account from
the lock to its commit, and PLATFORM as well when it posts a residue. A
settlement whose payouts exactly empty the pool posts no residue, names no
PLATFORM leg, and so never locks PLATFORM. For that whole time, everything
below queues behind it:
- every lazily minted grant and every cold book's seed, since both post from
  PLATFORM, but only behind a settlement that posts a residue;
- every other settlement that also takes PLATFORM;
- every trade by a winner, in any market.

At [3.4] #12's 1,000 winners that wait can last up to the 5-second budget. It
is accepted because settlements are rare and started by an administrator, and
the alternative, committing per payout, is what "One settlement is one
commit, through `posting.post_all`" refuses.

---

### D-NEW — `post_all` stamps from one probe per locked account, and movement *i* lands at the first stamp plus *i* µs

**Date:** 2026-10-09 · **Ticket:** #12 · **Status:** active

**Decision.** Holding every lock, `post_all` calls `_stamp_after_newest` once
over the full set of locked accounts, which is one indexed probe per account.
The result is the first stamp. Movement *i*, counted from 0 in input order, is
stamped at the first stamp plus *i* µs, and every leg of that movement carries
the same stamp.

**Why.** This is "A settlement's first stamp follows the newest entry on every
account it locks", built from the helper `post` already uses. The first stamp
falls after every existing entry on every account, and each movement lands one
tick after the one before. So no two entries on one account share a time, and
feed order is input order. A movement's legs share one stamp for the same
reason `post`'s do: a history page cannot split a transaction.

**Rejected.**
- *Option B, one statement over the union*: a second way to compute the same
  stamp, beside `_stamp_after_newest`, with no measurement asking for it yet.
- *A probe per movement*: it probes the pool and PLATFORM again for every
  movement.

**Reversal trigger.** PR 5's 5-second test misses its budget. Option B then
lands as its own refactor PR, with no test assertion changed.

**Notes.** This corrects the cost comparison in the second Rejected bullet of
"A settlement's first stamp follows the newest entry on every account it
locks". That bullet weighs "a thousand probes" against "one union query".
Option A is itself one probe per account, about 1,002 for 1,000 winners. So
the real gap is between a probe per movement, about 2,000, and a probe per
account, about 1,000. The single union query is option B, which is not built.

---

### D-NEW — `post` is not `post_all` of one movement

**Date:** 2026-10-09 · **Ticket:** #12 · **Status:** superseded by "`post` is `post_all` of one movement"

**Decision.** Deliberately not abstracted. `post` and `post_all` each keep
their own body. They share helpers, including `_quantized_legs` and
`_transaction_of` from PR 4a, `_require_balanced`, `_fingerprint`,
`_refuse_overdrafts`, `_stamp_after_newest` and `accounts.lock`, but not
their control flow.

**Why.**
- Making `post` call `post_all([movement])` would change `post`'s lookup from
  one key to a batch of keys. It would also put the batch's rules on the trade
  path's replay: the whole-batch match, `MalformedBatch` and netting across
  movements. The trade path is the busiest in the service, and none of those
  rules is for it.
- The merge could land only after the second copy existed, which means in a
  later refactor PR, and that refactor would change `post`'s behaviour. The
  steps that really were copies were extracted first, in PR 4a. What is left
  is two different sequences of the same steps.

**Rejected.**
- *`post` as `post_all` of one movement*: see above.
- *One private core with flags for the differences*: a flag for each
  difference, and each flag has one caller (backend/CLAUDE.md, "Reuse").

---

### D-NEW — PR 4a extracts `post`'s helpers ahead of `post_all`, as its own refactor

**Date:** 2026-10-09 · **Ticket:** #12 · **Status:** active

**Decision.** PR 4a (#249, `12-settlement-4a-posting-helpers`) is a new step
in the settlement stack, between PR 3 and PR 4. It extracts `_quantized_legs`
and `_transaction_of` from `posting.post`, with no behaviour change. PR 4 is
stacked on it.

**Why.** CLAUDE.md: the PR that would add a second copy extracts it instead,
and a refactor never shares a PR with a behaviour change. `post_all` needs
both pieces, so the extraction lands first and alone, and posting's tests
pass unchanged across it. decker757 reviews it first, as he does PR 4,
because `posting.py` is his.

**Rejected.** *The extraction inside PR 4*: a refactor in the same diff as a
new function, so a reviewer cannot tell which lines of `post` moved and which
changed.

**Notes.** It is named `4a` rather than renumbering the rest, so PRs 5 to 7
keep their numbers. The table gains one row, and row 4's "On" changes:

| # | Contents | On | Reviewer first |
| --- | --- | --- | --- |
| 4a | posting: `_quantized_legs` and `_transaction_of` extracted from `post`, no behaviour change | 3 | decker757 (`posting.py`) |
| 4 | `posting.post_all`, `Movement`, `MalformedBatch`, `docs/api/ledger-service.md`'s `malformed_batch` line | 4a | decker757 (`posting.py`) |

---

### D-NEW — PR 5 lands as three PRs: 5a extracts, 5b extends the terms client, 5 settles

**Date:** 2026-10-09 · **Ticket:** #12 · **Status:** active

**Decision.** PR 5 of the settlement stack becomes three PRs, each saying
`Refs #12`:

- **5a** (#251, `12-settlement-5a-helpers`), on PR 4 (#250): a refactor that
  extracts `books.lock_book` and `market_status.read_terms`.
- **5b** (`12-settlement-5b-terms-client`), on 5a: `proposed_outcome_id` and
  `settleable` on `MarketTerms`, and `market_terms.mark_settled`.
- **5**, on 5b: the settlement service, the trade path's latch, the audit
  seam and their tests.

**Why.** CLAUDE.md says a refactor never shares a PR with a behaviour change,
so 5a stands alone. 5b changes what every pull accepts, the trade path's
included, so a reviewer should see that change on its own rather than inside
the settlement diff. "PR 3 of the settlement stack holds the table, and the
audit seam lands with its caller in PR 5" already calls PR 5 the stack's
largest. This follows the precedent of "PR 4a extracts `post`'s helpers ahead
of `post_all`, as its own refactor".

**Rejected.**
- *5a inside PR 5*: a reviewer could not tell which lines of the trade path
  moved and which changed.
- *5b inside PR 5*: the trade path's new 503 would be reviewed among the race
  tests, and the client's parse cases would sit beside the payout's.
- *Renumbering PRs 5 to 7*: named `5a` and `5b`, as `4a` was, so PRs 6 and 7
  keep their numbers.

**Notes.** The stack now has eleven PRs. Row 5 of the table, as "PR 3 of the
settlement stack holds the table…" left it, is replaced by three rows:

| # | Contents | On | Reviewer first |
| --- | --- | --- | --- |
| 5a | ledger: `books.lock_book` and `market_status.read_terms` extracted, `ensure_trading` still returning `None`, no behaviour change | 4 | |
| 5b | ledger: `proposed_outcome_id` and `settleable` on `MarketTerms`, parsed strictly; `market_terms.mark_settled` and `SettlementUnconfirmed`; the allowlist line for `mark_settled`; their tests | 5a | |
| 5 | the ledger's audit seam, `service/settlement.py`, the trade path's latch, the three allowlist lines removed, with the 5 s, one-commit, rollback, stamp, no-connection, settle-vs-settle, settle-vs-trade, window-boundary and audit tests | 5b | |

*Amended 2026-10-10 by [3.4] #12: row 5 also adds one allowlist line, for
`pay_out`, which PR 6 deletes, and carries ADR 0019's amendment of 2026-10-10
with its DECISIONS.md entries. See "`pay_out` waits for its route on one
allowlist line, and PR 5 deletes the three lines it ends".*

---

### D-NEW — PR 5a extracts `books.lock_book` and `market_status.read_terms` ahead of settlement, as its own refactor

**Date:** 2026-10-09 · **Ticket:** #12 · **Status:** active

**Decision.** The book lock moves from `trading._lock_book` to
`books.lock_book`. The body of `ensure_trading` up to the status comparison
moves into `market_status.read_terms`: the pending-writes assert, the
rollback, the fetch, and the 404 mapped by whether a book exists.
`ensure_trading` calls `read_terms` and still returns `None`. There is no
behaviour change. The SQL of a warm buy, a cold buy, a gate refusal and both
404 branches is identical before and after.

**Why.** Settlement needs both. PR 5 would otherwise add the second copy of
the book lock, and of the rule that a 404 on a market with a book is
`MarketTermsUnavailable` (ADR 0017). CLAUDE.md says the PR that would add a
second copy extracts it instead. `ensure_trading` keeps returning `None`
because ADR 0017 says that handing fresh terms to the trade path is one
refactor away from pricing off a wire `liquidity_b`. That would break ADR
0005's rule that `b` crosses once, at publish, as an immutable snapshot.
Settlement takes its terms from `read_terms`, and the trade path never does.

**Rejected.**
- *`ensure_trading` returning `MarketTerms`, so settlement could call it*:
  the reason above, and settlement would then be asking the open-or-not
  question it never asks.
- *Settlement calling `market_terms.fetch` and mapping the 404 itself*: that
  is the second copy.
- *The extraction inside PR 5*: see "PR 5 lands as three PRs: 5a extracts,
  5b extends the terms client, 5 settles" above.

**Notes.** No test assertion changes. The sell race's docstring now names
`books.lock_book` as the line it is evidence for.

---

### D-NEW — `MarketTerms` carries `proposed_outcome_id` and `settleable`, parsed strictly on every pull

**Date:** 2026-10-09 · **Ticket:** #12 · **Status:** active

**Decision.** `MarketTerms` gains `proposed_outcome_id: uuid.UUID | None` and
`settleable: bool`, with no defaults. `_parse` reads both keys by index, so a
missing key is a malformed body:

- `settleable` must be a JSON boolean.
- `proposed_outcome_id` is null or a uuid string, read through `_to_uuid`.

Any failure is `503 market_terms_unavailable`, on every pull: the trade gate,
a first touch through `books.ensure_open`, and settlement. The client carries
both fields and decides on neither (ADR 0017).

**Why.** This is how the contract is already parsed: `published_at` is read
by index because the contract always sends the key. market_service sends
both fields on every public detail, since PR 1 (#227). A body missing one
therefore comes from a broken deploy, and the client fails closed on that,
as it does for a missing `status`.

A `settleable` that is not a real boolean is refused rather than coerced.
`"false"` is truthy, and reading it as true would pay out inside the dispute
window, which is why ADR 0019 gives `settleable` no default.

Positive reads are never cached. Only a 404 is remembered ("A market_service
404 is remembered for ten seconds, per process"), so settlement always reads
both fields fresh.

**Rejected.**
- *Lenient parsing, with settlement refusing a missing value itself*: the
  trade path would then depend on nothing new, but one client would parse in
  two styles.
- *A default on either field*: `settleable=False` hides a broken deploy as a
  window that never closes, and `settleable=True` pays out mid-window.

**Notes.**
- Deploy order matters. A ledger running 5b against a market_service from
  before PR 1 answers 503 to every trade. The stack's order prevents that,
  because PR 1 sits below 5b.
- ADR 0017 is amended: its list of what stays in `_parse` grows by two fields
  that the trade path does not read.
- The trade route's `503` row in `docs/api/ledger-service.md` needs no edit.
  A malformed body was already a 503 there, and still is.

*Amended 2026-10-20. Decided by ADR 0025, and not built yet.* `voidable:
bool` joins them, required with no default and parsed the same way. See
"`MarketTerms` carries `voidable`, required, and market_service deploys it
first" below.

---

### D-NEW — `market_terms.mark_settled` accepts only a `200`, and every other answer is `503 settlement_unconfirmed`, logged at ERROR

**Date:** 2026-10-09 · **Ticket:** #12 · **Status:** active

**Decision.** `mark_settled(market_id, *, access_token, terms_client) -> None`
sits in `service/market_terms.py`. It sends `POST /markets/{id}/settle` with
no body, forwards the administrator's token, and uses the process-wide terms
client, which carries the timeout from "Upstream failures map to 503, 404 and
401, and the timeout is explicit".

- A `200` returns `None`.
- Everything else raises `SettlementUnconfirmed` (`core/errors.py`, `503`,
  `settlement_unconfirmed`): a 401, 403, 404 or 409 (`market_not_approved`
  or `dispute_window_open`), any other 4xx, any 5xx, and any
  `httpx.RequestError`, a timeout included.
- The response body is never read, whether the call succeeded or failed.

The failure is logged at ERROR, with the market id and what came back (the
status code, or the exception's class). It is the only 503 in the service
logged above WARNING.

**Why.**
- **One failure meaning.** The call runs only after the payouts committed
  (ADR 0019, step 5), so every failure means the same thing: money has moved
  and the market is not marked. The remedy is the same too: repeat the
  request. Mapping a 401 to `invalid_token`, or a 404 to `market_not_found`,
  would tell an administrator that nothing happened. A 401 would also send
  the frontend to the login screen with no sign that anyone was paid.
- **The body is not needed.** "The settle step answers `200` with
  `MarketOut`, the same for the flip and for a repeat" makes the status code
  the whole answer. `MarketOut`'s money fields are JSON numbers, so not
  reading the body also keeps any float off the money path.
- **A timeout after sending does no harm.** If market_service committed, the
  retry gets `200` for SETTLED to SETTLED and writes nothing.
- **Why ERROR.** Every other 503 means the dependency is unwell, and the next
  request retries on its own. This one leaves a state that lasts until a
  person repeats the request. `controller/errors.py` keeps 503s at WARNING so
  that a market_service restart does not bury real errors. At WARNING, this
  line would be buried among exactly those.

**Rejected.**
- *Mapping answers as `fetch` does, by the table in "Upstream failures map to
  503, 404 and 401, and the timeout is explicit"*: it reuses codes that mean
  nothing happened.
- *Reading the error code to pick between refusals*: no refusal here changes
  what the administrator should do.
- *Retrying inside `mark_settled`*: the window-edge `409` needs minutes, not
  milliseconds, and a loop would hold the administrator's request open. A
  repeat from the administrator already retries, and writes nothing in the
  ledger.
- *Logging in the error handler*: the handler sees only the code, not what
  market_service answered.

**Notes.**
- `mark_settled` does not consult or feed the 404 cache. A 404 here comes
  from a market whose book exists, so it says nothing about whether the
  market exists.
- The handler still logs its WARNING for the 503, so a failure writes two
  lines. That is accepted, because only the ERROR line names the cause.
- `mark_settled` has no caller until PR 5. Until then
  `backend/dead_code_allowlist.py` carries one line for it, in the existing
  format, and PR 5 deletes it.

**Reversal trigger.** market_service learns of settlement some other way, and
step 5 goes. The function and the error go with it ("A settlement whose last
step fails answers `503 settlement_unconfirmed`").

---

### D-NEW — A recorded settlement replays before any status check; the lookup before the lock can only short-circuit

**Date:** 2026-10-10 · **Ticket:** #12 · **Status:** active

**Decision.** `settlement.pay_out` runs in this order:

1. It reads the public detail through `market_status.read_terms`, holding no
   connection. A failure refuses here, even for a market the ledger has
   already paid: `503`, or `401`. Nothing has been written by this request.
2. It looks up the market's `ledger.market_results` row without a lock. A row
   means a replay: one `GROUP BY` over the pool's entries rebuilds the
   figures, the session rolls back, and `POST /markets/{id}/settle` is called.
   This happens whatever status step 1 reported.
3. Only with no row are the status checks applied to step 1's answer:
   - `approved` and not `settleable`: `409 dispute_window_open`.
   - `approved` and `settleable`, or `settled`: continue.
   - anything else: `409 market_not_approved`.

   A refusal writes nothing and opens no book.
4. `books.ensure_open`, then `books.lock_book`, then the lookup again, under
   the lock. A row found there is a replay too, and its rollback releases the
   lock.

A repeat always calls `/settle`.

**Why.**
- *Replay first.* This is the reason in "The ledger asks market_service
  whether a market is still trading, and a replay is answered first", applied
  to a request whose last step can fail after the money moved. ADR 0019
  repairs a failed settle step by a repeat, so a repeat has to reach the
  replay whatever market_service now says.
- *The unlocked lookup can only short-circuit.* A hit names a committed row
  that nothing updates or deletes. The record commits in the same commit as
  every payout, so a row that is visible means all of its entries are
  visible. A miss is trusted for nothing: the status checks still run, and the
  latch is read again under the book lock.
- *A status refusal opens no book.* The checks come before
  `books.ensure_open`, whose cold path commits a `MARKET_SEED`. Without that
  order, a refused request would write.
- *A repeat always calls `/settle`.* The record proves that the money moved.
  It does not prove that market_service was told. Only a `200` from `/settle`
  proves that, and `SETTLED → SETTLED` writes nothing there.
- *A failed read refuses even for a paid market.* Accepted:
  - the request wrote nothing, so `503 market_terms_unavailable`'s "nothing
    happened" is true of this request;
  - a retry completes it;
  - the market stays in the overview's approved queue, which prompts the
    retry.

  A replay cannot finish without market_service anyway, because it ends in
  `/settle`.

**Rejected.**
- *The lookup before the read, as the trade path orders them.* It issues a
  statement before the first HTTP call and then has to roll it back. It also
  saves nothing: a trade's replay makes no call at all, but a settlement's
  replay still has to call `/settle`.
- *The status checks before the lookup, which is ADR 0019's original order.*
  Take a market that was paid, whose settle step failed, and whose
  `dispute_window_seconds` has been raised since. It still reads `approved`,
  but no longer `settleable`, so a repeat would answer `409
  dispute_window_open`. That code means nothing was written, but this market
  was already paid. In the order above it replays instead, and `/settle`'s
  answer decides the response: `200`, or `503 settlement_unconfirmed`.

**Reversal trigger.** Either of these:
- The settle step goes, because market_service learns of settlement some
  other way. A replay then makes no call, and the trade path's order becomes
  the better one.
- A code path updates or deletes a `ledger.market_results` row. The lookup
  can then no longer short-circuit without a lock.

**Notes.** This amends ADR 0019's steps 1 and 3 (amendment of 2026-10-10).

*Amended 2026-10-20. Decided by ADR 0025, and not built yet.* Both lookups,
step 2's and step 4's, branch on the row's `kind`. A `settled` row is a
replay, as above. A `voided` row is `409 market_already_voided`, and nothing
is written. Step 3 answers a `voided` status with the same code, ahead of
`market_not_approved`. Without the branch, `_replay`'s assertion that the row
names a winner (`service/settlement.py:136`) turns a settlement on a voided
market into a bare 500. The void itself follows this entry's order, read, then
lookup, then status, rather than #221 revision 2's lookup first, for the
reasons this entry gives.

---

### D-NEW — The winner is checked under the book lock, after the latch, and a replay never checks it

**Date:** 2026-10-10 · **Ticket:** #12 · **Status:** active

**Decision.** This check runs after the under-lock lookup misses. The book's
outcomes are read, and market_service's `proposed_outcome_id` must be one of
them. If it is null or names an outcome the book does not have, the answer is
`503 market_terms_unavailable`, and the rollback writes nothing and releases
the lock. A replay never reads `proposed_outcome_id`, whichever lookup found
the record.

**Why.**
- *A replay never checks it.* "On a repeat, the recorded outcome governs." If
  a replay checked market_service's current winner, a later answer from
  market_service could refuse a market that has already been paid.
- *Against the book's outcomes, under the lock.* On a cold book those rows
  exist only after `books.ensure_open`. The record's composite foreign key
  points at them, so a foreign winner is refused by this check with a code.
  It is never left to the foreign key, which would raise an `IntegrityError`
  inside `post_all` and end as a 500. The outcomes are immutable (ADR 0005),
  so the lock is not needed for freshness. The check sits here because the
  outcomes read does, and they are read only once.
- *503, not 409.* Approval always stamps the winner, so a null on an
  approved market is damage in market_service. A winner that is not in the
  book means market_service is wrong, which ADR 0017 maps to 503, as it does
  a 404 for a market with a book. An administrator can do nothing about
  either, and #12's criterion already names 503.

**Rejected.**
- *Checking during the status checks, against step 1's `terms.outcomes`.* That
  is a second list of outcomes to trust, and the foreign key would still be
  the only guard on the book's own rows.
- *`409`.* It tells an administrator to act, and no action helps.

**Reversal trigger.** A settlement that pays something other than
market_service's winner, such as [BE] #221's void.

*Amended 2026-10-20. Decided by ADR 0025, and not built yet.* The reversal
trigger does not fire. The void is not a settlement and pays no winner: it is
its own route, writes its own record kind, and never reaches this check.

---

### D-NEW — The trade latch sits after the key re-check under the book lock, and before the outcomes read and the staleness check

**Date:** 2026-10-10 · **Ticket:** #12 · **Status:** active

**Decision.** On the trade path, the `ledger.market_results` lookup runs
right after the under-lock idempotency re-check. A row is `409 market_closed`.
The lookup reads any row and never filters on `kind`. It runs before the
outcomes read, `book_prices.refuse_unpriceable` and the staleness check.

**Why.**
- *After the key re-check,* so that a trade that committed before the
  settlement still replays. A trader whose trade charged them is never told it
  was refused. That is the reason in "The ledger asks market_service whether a
  market is still trading, and a replay is answered first", applied under the
  lock.
- *Before the staleness check,* so that a settled market answers
  `409 market_closed` and not `409 quote_stale`. A fresh quote cannot help on
  a market that will never trade again. Settlement leaves `state_version`
  alone, so a stale answer would only come from a quote that was already old.
  It would still send the trader to re-preview a market they cannot trade.
- *Before the outcomes read,* because a refusal needs nothing that read
  returns.

**Rejected.**
- *The latch before the key re-check.* A retried trade that committed before
  the settlement would be refused even though it was charged.
- *The latch after the staleness check.* A stale quote on a settled market
  would answer `quote_stale`, and the trader would re-preview only to be
  refused again.

**Reversal trigger.** The trade path stops answering a replay first.

---

### D-NEW — A payout is quantity × 1, keyed per winner in `user_id` order, and the residue is the last movement

**Date:** 2026-10-10 · **Ticket:** #12 · **Status:** active

**Decision.** These reads and movements all happen under the book lock.

- **Winners.** One statement: positions joined to their USER accounts, where
  the outcome is the winner and `quantity > 0`, `ORDER BY user_id`.
- **One payout per winner.** Kind `SETTLEMENT`, keyed
  `settlement:{market_id}:{user_id}`. Its legs are the pool `−quantity` and
  the user `+quantity`. Its context is `user_id`, `market_id`, `outcome_id`
  and `quantity`, all as strings.
- **The pool's balance.** Read after the winners.
- **The residue.** `r = pool − Σ payouts`:
  - positive: posted from the pool to PLATFORM;
  - negative: posted from PLATFORM to the pool;
  - zero: skipped, and PLATFORM is not named, so it is not locked.

  It is kind `SETTLEMENT_RESIDUE`, keyed `settlement_residue:{market_id}`,
  with context `market_id`. It is the last movement in the batch.
- **One number.** The signed `r` is the same value in the audit entry and in
  the response.

**Why.**
- *× 1 needs no rounding.* "Shares keep money's scale of 4, in `q` and in
  positions" means the quantity is already a valid amount. The pool's legs
  therefore match the shares exactly.
- *`user_id` order.* The same settlement always builds the same batch. Since
  `post_all` stamps movement *i* at the first stamp plus *i* µs, the feed's
  order is that order. Account locks are taken in `post_all`'s own order
  (ascending by account id), which this order does not affect.
- *The residue last.* It is computed from the payouts. ADR 0019's second trap
  explains why reordering it is not a fix.
- *Context strings.* These are the fields PR 7's history row reads ("A
  payout's history row is kind `settlement`, with market, outcome and
  quantity"). They are strings, as a trade's `quantity` already is.
- *The pool's balance is stable under the book lock.* Every writer of a pool
  either holds the book lock (trades, settlement) or commits together with the
  book row itself (the seed, in `books.ensure_open`'s one commit). So the book
  lock is the lock ADR 0015 asks for on this read.
- *`quantity > 0`.* A zero leg is refused by posting, and a holder who sold to
  zero is paid nothing.
- *One `r`.* It is computed once, so the residue entry, the audit entry and
  the response cannot disagree.

**Rejected.**
- *No `ORDER BY`.* Postgres would be free to build the batch in a different
  order on each run, and the feed's order would follow it.
- *A query per winner for their account.* That is a thousand statements where
  one join does.

**Reversal trigger.** A share that pays something other than 1, or fees the
platform keeps from the pool.

---

### D-NEW — A settlement's `recorded_at` is Python's clock read after the book lock, and is `post_all`'s floor

**Date:** 2026-10-10 · **Ticket:** #12 · **Status:** active

**Decision.** `now` is `datetime.now(UTC)`, read after `books.lock_book` and
after the under-lock lookup misses. It becomes the record's `recorded_at`, the
audit entry's `occurred_at` and `post_all(now=now)`. Tests may pass `now` in.

**Why.** This is the trade path's clock, read at the same point. It is read
after the lock so that a settlement that queued behind a trade is not stamped
before that trade (#138). It is only a floor for `post_all`, which stamps each
entry after the newest entry on every account it locks ("A movement's
timestamp is fixed under its account locks, not trusted from the clock"). So
the order of the entries never depends on this clock. The entries may land a
little after `recorded_at`, as a trade's entries land a little after its
`state_changed_at`. The record and the audit entry take one value, so they
cannot disagree with each other.

**Rejected.**
- *`clock_timestamp()`, as in "Every decision a market records is stamped
  with Postgres's `clock_timestamp()`, read after the row lock".* That entry
  exists because market_service's sweep stamps with the database clock next
  to container stamps on the same row. Nothing in the ledger stamps
  `market_results` from the database, and the probe fixes the entries' order.
  It would be a round trip that orders nothing.
- *`func.now()`.* That is the transaction's start time, which comes before
  the lock wait (#138).
- *The first payout's stamp, from `post_all`.* The record and the audit entry
  must be staged before `post_all` is entered (ADR 0019's first trap, as
  amended), so they cannot wait for what it returns.

**Reversal trigger.** A reader orders the record or the audit entry against
something stamped with the database's clock.

---

### D-NEW — The settlement response carries its full figures, rebuilt from the entries on a replay, through one builder

**Date:** 2026-10-10 · **Ticket:** #12 · **Status:** active

**Decision.** The result is a frozen record with `market_id`, `outcome_id`,
`recorded_at`, `holders_paid`, `total_paid` and `residue`. One builder makes
it on both paths:
- *The fresh path* uses the values it computed before `post_all`.
- *A replay* takes `outcome_id` and `recorded_at` from the record, and the
  other figures from one `GROUP BY` kind over the pool account's
  `SETTLEMENT` and `SETTLEMENT_RESIDUE` entries:
  - `holders_paid` is the count of `SETTLEMENT` legs;
  - `total_paid` is minus their sum;
  - `residue` is minus the residue leg;
  - a kind with no entries reads as zero.

No ORM instance is read after the commit or the rollback.

**Why.**
- *Full figures.* #12 answers a repeat "with the settlement it recorded the
  first time".
- *Rebuilt from the entries.* The record stores no sums. Its shape is
  "`ledger.market_results` is one record per finished market, with a `kind`",
  and the rejection of stored sums, as a second source of truth, stands in
  the entry that one superseded in part. The entries are the record of the
  money, so a replay derives the figures, as a balance is derived.
- *One builder,* for the reason `trading.result_of` is the trade path's only
  builder: two builders could drift apart, and one cannot.
- *Nothing read after the commit or the rollback.* Both expire every
  instance, so a lazy load would autobegin a transaction just before
  `/settle`, which breaks "no statement between them". Under asyncio it
  raises anyway.

**Rejected.**
- *Storing the figures on the record:* refused above.
- *Answering with only the market and the winner:* the response is the
  administrator's one immediate account of what moved.

---

### D-NEW — `market.settled` carries the winner and the three figures, with no target label and no reason

**Date:** 2026-10-10 · **Ticket:** #12 · **Status:** active

**Decision.** The entry is:
- `action_type` `market.settled`;
- `target_type` `"market"`, `target_id` the market's id;
- `target_label` null and `reason` null;
- `occurred_at` equal to `recorded_at`;
- the actor taken from the administrator's token;
- `context` with `winning_outcome_id` (a uuid string), `total_paid` and
  `residue` (signed decimal strings), and `holders_paid` (an integer).

**Why.**
- *These fields:* #12's audit criterion lists the winner, the total paid, the
  number of holders paid and the residue.
- *`winning_outcome_id`* is the key market_service already uses in the
  decision snapshot that `market.outcome_approved` and
  `market.marked_settled` carry. A reader can match all three entries by that
  one key.
- *No label:* market_service labels a market with its question, and the
  ledger does not hold the question. `MarketTerms` carries no prose, just as
  `OutcomeTerms` has no label.
- *No reason:* `reason` holds an administrator's justification (ADR 0014,
  ADR 0016), and settlement chooses nothing.
- *Strings:* money crosses as decimal strings.

**Rejected.**
- *The question as the label, read from the public detail.* It would widen
  `MarketTerms` for a display field.
- *Calling the key `outcome_id`.* That would give the same thing two names in
  one log.

**Reversal trigger.** A reader of the log needs a label that market_service's
own entries for the market do not give it.

---

### D-NEW — The ledger's `service/audit.py` is a third copy, and the extraction is declined

**Date:** 2026-10-10 · **Ticket:** #12 · **Status:** active

**Decision.** The ledger gains three files:
- `core/audit.py`, with `SOURCE_SERVICE = "ledger_service"`;
- `model/audit.py`, an `AdminAction` with `MARKET_SETTLED = "market.settled"`;
- `service/audit.py`, the same as market_service's and auth_service's except
  for the `AdminAction` it types the call with.

Each file names its twins in a one-line comment.

**Why.** ADR 0006's #135 amendment moved everything identical into
`shared/audit.py`. It kept the per-service files on purpose: the vocabulary
and the source name differ by design, and `service/audit.py` keeps each call
typed by that service's own `AdminAction`. ADR 0019 lets the ledger's seam
differ in constants and vocabulary only, and the type of `action` is the
vocabulary. So the copy is what those two records already chose, not a missed
extraction.

**Rejected.** *Extracting it into `shared/`.*
- It touches auth_service, which is decker757's.
- It would refactor two services inside a behaviour PR.
- A shared builder would have to take a bare `StrEnum`, so a service could log
  an action from another service's vocabulary. The typing that the file exists
  for would be lost.

**Reversal trigger.** A fourth writing service, or a change to one copy's body
that is not about its vocabulary. The extraction then lands as its own
refactor PR ahead of that change, because a refactor never shares a PR with a
behaviour change.

**Notes.** This settles what "PR 3 of the settlement stack holds the table,
and the audit seam lands with its caller in PR 5" left open.

---

### D-NEW — `pay_out` waits for its route on one allowlist line, and PR 5 deletes the three lines it ends

**Date:** 2026-10-10 · **Ticket:** #12 · **Status:** active

**Decision.** PR 5 adds one line to `backend/dead_code_allowlist.py`, in the
existing format:

```python
# [3.4] #12 PR 6: the settlement route calls it; delete this line there
pay_out
```

It deletes the `MarketResult`, `post_all` and `mark_settled` lines.

**Why.** The dead-code check fails on anything production code does not use,
and tests do not count. `pay_out`'s caller is PR 6's route. The trade latch
uses `MarketResult` in production. `pay_out` names `post_all` and
`mark_settled`, which the check counts as use.

**Rejected.** *The route inside PR 5.* PR 6 is the controller layer, and PR 5
is already the stack's largest.

**Reversal trigger.** Review of PR 5 refuses an allowlist entry for planned
code. The route then moves into PR 5.

---

### D-NEW — A cold settlement reads the public detail twice

**Date:** 2026-10-10 · **Ticket:** #12 · **Status:** active

**Decision.** On a market whose book was never opened, settlement's first read
is followed by `books.ensure_open`'s own fetch. That makes two GETs, and it is
accepted. `ensure_open` is not handed the first read's terms.

**Why.** The trade path already makes the same two reads on a first touch,
through its gate and `ensure_open`. A cold settlement is rare. A trade, a
preview or a snapshot each opens a book ("The realtime snapshot is a second
first-toucher, and never gates on status"), so a cold settlement means nobody
traded the market or looked at it. Neither read holds a connection. The first
runs before any statement, and `ensure_open` rolls back before its fetch ("The
cold path holds no connection across the terms pull").

**Rejected.** *Passing the terms into `ensure_open`.* Every first toucher's
function would change to save one read on a rare path.

**Reversal trigger.** Settling untouched markets becomes common.

**Notes.** A cold settlement makes three calls to market_service. #12's
criteria were amended to match: "No call to market_service is made while the
request holds a database connection or a row lock", and the speed criterion
now excludes "the calls to market_service" rather than "the two calls".

---

### D-NEW — PR 6a moves `actor_of` into `shared/audit.py`, ahead of the route, as its own refactor

**Date:** 2026-10-10 · **Ticket:** #12 · **Status:** active

**Decision.** `actor_of(claims: TokenClaims) -> Actor` sits in
`shared/audit.py`, beside `Actor`. It copies `user_id`, `username` and
`role.value` from the token, so the role is the plain string. market_service's
`get_actor` calls it through its `core/audit.py` and `service/audit.py` seams.
The ledger's `get_actor`, in PR 6, calls it through the same two seams.
auth_service's `get_actor` is not changed. PR 6a (#256,
`12-settlement-6a-actor-of`) is a new step in the settlement stack, on PR 5
(#254), and PR 6 is stacked on it.

**Why.**
- *The second copy.* The ledger's route needs the same claims-to-`Actor`
  conversion as market_service's. CLAUDE.md says the PR that would add the
  second copy extracts it instead.
- *It clears ADR 0012's bar.* Every caller needs identical behaviour, and a
  difference would be a bug. If the two copies drifted, one administrator
  could appear under two roles, or two names, in one log: on
  `market.outcome_approved` from market_service and on `market.settled` from
  the ledger, for example.
- *It already belongs there.* `TokenClaims` is in `shared/security.py` and
  `Actor` is in `shared/audit.py`. The function's input and output both live
  in `shared/`, and it imports no service.
- *Its own PR.* A refactor never shares a PR with a behaviour change. This
  follows "PR 4a extracts `post`'s helpers ahead of `post_all`, as its own
  refactor" and "PR 5a extracts `books.lock_book` and
  `market_status.read_terms` ahead of settlement, as its own refactor".
- *auth_service is not a caller.* Its `get_actor` builds the `Actor` from the
  live user row, so a demotion takes effect on the next request (ADR 0007).
  Building it from the token would bring the fifteen-minute window back to
  the one service that does not have it. That is a different input, not a
  drift.

**Rejected.**
- *A second copy in the ledger's `controller/dependencies.py`, with a comment
  naming its twin.* That is the copy CLAUDE.md forbids. Unlike the ledger's
  `service/audit.py`, nothing in it differs by design.
- *The extraction inside PR 6.* A refactor would share a diff with the new
  route.
- *auth_service calling `actor_of`.* It would undo ADR 0007's live role.

**Reversal trigger.** market_service or the ledger needs an `Actor` built from
something other than the token, such as a live user lookup. That service then
builds its own, as auth_service does.

**Notes.**
- This is not the case "The ledger's `service/audit.py` is a third copy, and
  the extraction is declined" decided. That file differs by the `AdminAction`
  type it takes. `actor_of` has no vocabulary, is the same in both callers,
  and touches no auth_service file.
- The ledger's seam files gain the same re-export as market_service's, so
  they still differ in constants and vocabulary only ("The ledger writes the
  settlement's audit entry, through its own seam onto `shared/audit.py`").
- ADR 0006's #135 amendment lists what `shared/audit.py` holds, and is
  amended to name `actor_of`.
- The table gains one row, and row 6's "On" changes:

| # | Contents | On | Reviewer first |
| --- | --- | --- | --- |
| 6a | shared: `actor_of` in `shared/audit.py` beside `Actor`, market_service's `get_actor` calling it through its seam, no behaviour change; its tests in `shared/unit_test/test_audit.py` | 5 | decker757 (market_service) |
| 6 | the settlement route, `SettlementIn` and `SettlementOut`, the ledger's `get_actor` on `actor_of`, the `pay_out` allowlist line removed, `docs/api/ledger-service.md`; the route tests | 6a | |

---

### D-NEW — The settlement route answers `200`, the same for a fresh settlement and for a repeat

**Date:** 2026-10-10 · **Ticket:** #12 · **Status:** active

**Decision.** `POST /ledger/markets/{id}/settlement` answers `200` with the
six-field settlement. It does so on the request that pays and on every repeat
that replays. It never answers `201`.

**Why.**
- *One status for the first answer and the replay.* The trade route answers
  `201` to a first trade and to its replay, with byte-identical bodies.
  market_service's settle step answers `200` to the flip and to a repeat
  ("The settle step answers `200` with `MarketOut`, the same for the flip and
  for a repeat"). This route follows the same rule.
- *A split would expose what the builder hides.* "The settlement response
  carries its full figures, rebuilt from the entries on a replay, through one
  builder" makes a replay indistinguishable from the first answer. `201` then
  `200` would give a client a difference to branch on. A repeat is how a
  failed last step is repaired ("A settlement whose last step fails answers
  `503 settlement_unconfirmed`"), so the client should treat it as the same
  settlement.
- *Nothing is created at an address.* There is no settlement resource to GET
  and no `Location`. The trade's body names a new transaction by its
  `transaction_id`. This body names only the market and its winner, and
  settling is something done to a market that already exists.

**Rejected.**
- *`201` on both, as the trade route does.* The trade's `201` points at a new
  transaction, and this response has no new id to point at.
- *`201` fresh and `200` on a repeat.* That is the split described above.
- *`204`.* The body carries the figures #12 asks for, so it cannot be empty.
- *`202`.* The work is finished when the response is sent.

**Reversal trigger.** The settlement becomes a resource of its own, with an id
and a route that reads it.

**Notes.** The trade route's `201` is not changed by this entry.

---

### D-NEW — The settlement body is empty, and any field in it is `422 invalid_request`

**Date:** 2026-10-10 · **Ticket:** #12 · **Status:** active

**Decision.** The route takes `body: SettlementIn | None = None`.
`SettlementIn` has `extra="forbid"` and no fields. A request with no body,
with `{}` or with `null` is accepted. Any key, or a body that is not an
object, is `422 invalid_request`.

**Why.**
- *ADR 0009's second amendment.* Neither write route takes money, and this
  route's caller supplies nothing that decides who is paid or how much.
- *A refused field cannot be mistaken for an honoured one.* A body naming an
  outcome that was silently dropped would let a client believe it had chosen
  the winner. Refusing it makes clear that the request chooses nothing.
- *`| None = None`.* A model parameter on its own makes FastAPI require a
  body, so a bare POST, which is the documented call, would fail with "field
  required".
- *`{}` passes,* because it is what most JSON clients send for "nothing".

**Rejected.**
- *No body parameter at all.* FastAPI then ignores whatever is sent, so
  `{"outcome_id": …}` would succeed and look honoured.
- *Requiring `{}`.* A bare POST would fail over a body that holds nothing.

**Reversal trigger.** ADR 0009's: the body gains a field that names an
outcome, an account or an amount. The route then needs a service credential
first.

**Notes.** market_service's settle step ignores a body instead ("anything sent
is ignored"). That route is the ledger's call only, and the ledger sends no
body. This route is the one a client calls, so a confused client meets this
one.

---

### D-NEW — A settlement publishes no realtime frame

**Date:** 2026-10-10 · **Ticket:** #12 · **Status:** active

**Decision.** Neither a payout nor a replay publishes anything to Redis. The
trade path's price publish is not called, and no new frame type is added.

**Why.**
- *Nothing a frame carries changes.* Settlement writes neither `q` nor any
  position: "`ledger.market_settlements` is the latch, and positions and `q`
  are never written", a part that stands where "`ledger.market_results` is one
  record per finished market, with a `kind`" superseded the rest. A `price`
  frame's `state_version` and prices would be the same as the last trade's
  frame. ADR 0010's ordering rule would discard it as stale, or it would
  repeat what the client already shows.
- *No frame type for it exists.* `docs/api/realtime-service.md` defines
  `subscribed`, `unsubscribed`, `price` and `error`. A `settled` frame would
  change the contract of a service that is decker757's, for a consumer nobody
  has built.
- *Its consumer decides.* ADR 0019 gives [L-3] #40's "recalculated after
  market settlement" an event to hang on, and says it "does not decide how it
  is consumed".
- *#12 asks for none.*

**Rejected.**
- *A `price` frame anyway.* It carries no new information, and a client
  cannot read "settled" from it.
- *A `settled` frame type.* That is a realtime contract change made ahead of
  its consumer.

**Reversal trigger.** [L-3] #40, or a frontend story, needs to learn of a
settlement without reading the market's status. That story designs the frame.
Like the price publish, it is sent after the commit and must never fail the
settlement (ADR 0010).

**Notes.** A client learns that a market is settled from market_service's
status. The frontend already hides the trade control on any market that is
not open, and a trade on a settled market is `409 market_closed`.

*Amended 2026-10-20. Decided by ADR 0025, and not built yet.* A void
publishes none either, for the same reasons: it writes neither `q` nor any
position, and its consumer has not asked for one. It releases a market's
resting orders without an `order` frame, as settlement does.

---

### D-NEW — A settled row's payout is its quantity, and its result comes from `ledger.market_results`, joined in the portfolio's one statement

**Date:** 2026-10-10 · **Ticket:** #12 · **Status:** active

**Decision.** The portfolio's one statement outer-joins `ledger.market_results`
on `market_id`. Every held row of a market with a record is a settled row:

- `result` is `paid_out` where the row's outcome is the record's `outcome_id`,
  and `worthless` otherwise.
- `payout` is the row's `quantity` on a `paid_out` row, and `0.0000` on a
  `worthless` one.

A settled market's book is not valued. It skips
`book_prices.refuse_unpriceable`, `liquidation_values_of` and the above-`q`
check, and its rows add nothing to `positions_value`.

**Why.**
- *The payout is the quantity.* "A payout is quantity × 1, keyed per winner in
  `user_id` order, and the residue is the last movement" pays exactly the
  winning quantity, with no rounding. "`ledger.market_settlements` is the
  latch, and positions and `q` are never written" leaves that quantity where
  it was. So the quantity held is the amount paid. Reading it from the entry
  would be a join for a number that is already on the row.
- *The result is the ledger's own record.* "The portfolio carries ids only, and
  makes no call to market_service" named #12's realized outcome as its
  candidate trigger and said the answer should come from the settlement
  record, not from a call. It does, so that entry's trigger does not fire.
- *Settled markets skip the checks.* "A damaged book, or a position above `q`,
  fails the whole portfolio with `market_book_incomplete`" fails the whole
  read because `net_worth` is a sum, and a missing row makes it wrong. A
  settled row adds nothing to that sum, so its book cannot make the total
  wrong. Failing the read over that book would refuse a correct answer.
- *The exclusion lives in the portfolio, not in `core/`.*
  `liquidation_values_of` is a pure function of `q`, `b` and the holdings, and
  knows nothing of settlement. The portfolio's valuation decides which markets
  reach it. So [L-1] #38 must reuse the portfolio's valuation. Called alone,
  `liquidation_values_of` values a settled market's shares at the frozen book,
  on top of the payout already in the balance. ADR 0018's 2026-10-10
  amendment says so.

**Rejected.**
- *Reading `payout` from the `settlement` entry.* A join through `transactions`
  by key for every settled row, for a figure equal to `quantity` while the
  payout rule holds.
- *A `settled` flag on `liquidation_values_of`.* Market state inside core's
  pure pricing. #38 would still have to know which markets are settled, which
  is the portfolio's job.
- *Keeping `refuse_unpriceable` and the above-`q` check on settled markets.*
  The whole read would fail over a book none of whose figures reach the
  response.

**Reversal trigger.** The payout rule's: a share that pays something other
than 1, or fees the platform keeps from the pool. Then `payout` is no longer
`quantity`, and the row reads it from the entry.

**Notes.** Like the latch's readers, the join does not filter on `kind`. Today
`settled` is the only kind. When [BE] #221 adds a void, its rows reach this
branch with no `outcome_id`, so #221 must add a `result` value of its own
here. A settled row keeps `state_version`, the frozen book's last version. A
position sold to zero is still left out, settled or not. A holder of both
outcomes sees two rows, one `paid_out` and one `worthless`.

*Amended 2026-10-20. Decided by ADR 0025, and not built yet.* A voided
market's rows are not settled rows. Their `result` is `refunded`, and their
`payout` is what the position's `void_refund` transaction paid, read through a
left join on its derived key and coalesced to `0.0000`. This entry's reason
for not reading from the entry, a join for a figure already on the row, does
not hold for a refund, which is on no row. A settled row's payout is still its
quantity, and a voided market's book is still not valued. See "A voided row's
`payout` is its refund, joined in the portfolio's statement only" below.

---

### D-NEW — `result` and `payout` are on every portfolio row, null until the market is settled

**Date:** 2026-10-10 · **Ticket:** #12 · **Status:** active

**Decision.** `PortfolioPositionOut` always carries `result` and `payout`.
Both are `null` on a market the ledger has not settled. `price`, `value` and
`unrealized_pnl` become nullable, and are `null` on a settled row. Nothing is
left out with `exclude_none`.

**Why.** One row shape. A client branches on the value of `result`, not on
whether a key is present. It is the history's rule too: `LedgerEntryOut`
always sends its typed fields and nulls them where they do not apply ("A trade
row's typed fields come from its kind and `context`; any other kind renders
with them null"). `exclude_none` would also strip a settled row's `null`
`price`, `value` and `unrealized_pnl`. That would give two shapes again, from
the other side, and it would apply to the whole response.

**Rejected.**
- *Omitting `result` and `payout` until settlement*, by `exclude_none` or per
  row. See above.
- *`payout: "0.0000"` on an unsettled row.* Zero is what a `worthless` row was
  paid. On an unsettled row it would claim a payout that has not happened.

**Notes.** The shipped portfolio page types `price`, `value` and
`unrealized_pnl` as `string` (`frontend/src/api/ledgerApi.ts:53-59`), and the
`Position` type has no `result` or `payout`. The formatters at
`frontend/src/pages/PortfolioPage.tsx:145-147` already show "—" for anything
that is not a decimal string, so a null `price` or `value` renders. The crash
is the P&L cell: `PnlCell` colours the value through `isZeroCredits`, which
calls `null.startsWith` and takes the page down for anyone holding a settled
position. The page needs that guard, and the type its nullable fields, before
this stack merges into `dev`, whether or not [FE][3.4] #216's settled
rendering is ready by then.

*Amended 2026-10-20. Decided by ADR 0025, and not built yet.* `result` gains
`refunded`, on a voided market's rows, with the refund as `payout`. "Null
until the market is settled" now reads: null until the ledger records a result
for the market, settled or voided.

---

### D-NEW — `_trade_fields_of` keeps its name, and its docstring says it covers settlement

**Date:** 2026-10-10 · **Ticket:** #12 · **Status:** superseded by "The market's trade feed reads the pool account's feed, filtered to the trade kinds, and its rows are built by `trade_fields_of`"

**Decision.** In PR 7 the history's row builder fills `settlement` rows. Its
name stays `_trade_fields_of`, and its docstring says it covers the two trade
kinds and `settlement`.

**Why.** It is merged code. backend/CLAUDE.md: "Do not reorder or rename
untouched code in a feature PR". CLAUDE.md: a refactor never shares a PR with
a behaviour change. The name is now narrower than the function, but nothing it
says is false. The docstring is where a reader who opens it finds the rest.

**Rejected.**
- *Renaming it in PR 7.* A rename inside a behaviour PR.
- *A rename PR ahead of PR 7, as 4a, 5a and 6a are.* A thirteenth PR for a
  private name with one caller.

**Notes.** `TradeFields` is in the same position and stays as it is, for the
same reason. A rename of either is its own refactor PR, under an issue of its
own.

---

### D-NEW — The leaderboard is stored as generations, and a refresh swaps the whole ranking in one commit

**Date:** 2026-10-11 · **Ticket:** #38, #40 · **Status:** active

**Decision.** Two new ledger tables, added by one Alembic revision:

- `ledger.leaderboard_generations`: `id`, `computed_at`, `ranked_users`.
  `ranked_users` is the number of entries the generation holds, written in
  the same insert, so a page can report it without counting.
- `ledger.leaderboard_entries`: `generation_id`, `rank`, `user_id`,
  `username`, `net_worth`. The primary key is `(generation_id, rank)`, and
  `(generation_id, user_id)` is unique. The foreign key to the generation is
  `ON DELETE CASCADE`.

A refresh inserts one generation and all of its entries in one transaction,
then deletes every generation except the new one and the one before it.

The newest generation, by `computed_at`, is the current ranking. Nothing
points at it, so there is no pointer to update. Rows are never updated: a
generation is written once and later deleted.

**Why.** [L-3] #40: "Users never see partially calculated rankings." A reader
sees a generation only after its commit, and sees every row of it. The
swap is that commit, so no reader can observe a half-swapped ranking.

Keeping the previous generation is what lets a cursor survive one refresh
("A leaderboard cursor names its generation"). Deleting the rest bounds the
tables at two rankings.

**Rejected.**
- *One table, truncated and refilled.* A reader between the delete and the
  insert sees an empty board, unless the whole refill holds a lock that
  readers wait on. Every refresh would then stall every read for its duration.
- *Updating rank and net worth in place.* A reader mid-update sees ranks from
  two rankings, which is the partial ranking #40 forbids.
- *A `current_generation` pointer row.* Pointer and rows are a second thing
  that has to agree. "Newest `computed_at`" cannot disagree with itself, and
  "A refresh never replaces a newer generation" keeps it correct.
- *A materialized view.* `REFRESH ... CONCURRENTLY` swaps atomically, but the
  view would have to run ADR 0018's valuation in SQL, a second copy of the
  engine.

**Notes.** `user_id` is stored so that [L-2]'s own-row lookup can use the
unique index. It is never in a response. `net_worth` is
`numeric(scale 4)`, like every amount in the ledger. `rank` is an `int`.

**Reversal trigger.** A refresh that no longer fits in its interval, measured
on a real dataset. Then the refresh becomes incremental, and generations may
stop being whole copies.

---

### D-NEW — A refresh writes under a transaction-level advisory lock, and writes nothing if a generation at least as new exists

**Date:** 2026-10-11 · **Ticket:** #38, #40 · **Status:** active

**Decision.** A refresh's write transaction starts with
`SELECT pg_advisory_xact_lock(<key>)`. The key is one constant in the
ledger's `core/`, and this is the only advisory lock in the database. Under
that lock the refresh asks whether any generation has
`computed_at >= its own computed_at`. If one does, it inserts nothing,
deletes nothing, commits, and logs at INFO. Otherwise it inserts and prunes
as "The leaderboard is stored as generations" says.

**Why.** Two refreshes can overlap. The loop awaits each refresh before
sleeping, so one replica cannot overlap itself: overlap needs two replicas or
a second writer (the reversal trigger names one). Without this guard the
slower, older one commits last. It becomes the current ranking even though it
is older, and its prune deletes the newer generation's rows. ADR 0015 puts
the rule on the reader: the read that decides the write is issued after the
lock. A row lock cannot be taken on a generation that does not exist yet, so
the lock is an advisory lock. Its `xact` form releases at commit or
rollback, so a refresh that crashes cannot leave it held.

**Rejected.**
- *`SELECT ... FOR UPDATE` on the newest generation.* It locks the row that
  exists, not the one about to be inserted. Two refreshes that both found no
  generation, on an empty table, would both insert.
- *A unique constraint on `computed_at`.* It stops only exact duplicates, not
  an older generation landing after a newer one.
- *Comparing by insertion order (`id` or a sequence).* That orders by who
  committed last, which is exactly the order that is wrong here.

**Notes.** Trades never take this lock, and the refresh takes no row lock on
any trade table. A refresh cannot block a trade ([L-3] #40). The guard
compares `computed_at` values stamped on different replicas' clocks ("A
refresh's `computed_at` is Python's clock, read before the valuation"). With
skew, a replica running ahead can make another replica's genuinely newer
refresh skip. The board is then up to that skew older than it could be, and
never inconsistent.

**Reversal trigger.** The leaderboard gains a second writer that is not a
refresh, such as an admin-triggered recompute. It must take the same lock,
and this entry gets a line saying so.

---

### D-NEW — A refresh's `computed_at` is Python's clock, read before the valuation; `stale` is derived on read against the same clock

**Date:** 2026-10-11 · **Ticket:** #38, #40 · **Status:** active

**Decision.** A refresh reads `datetime.now(UTC)` once, before its valuation
statement, and stamps the generation with it. The read route reads
`datetime.now(UTC)` once per request. It answers
`stale = now - computed_at > 2 × LEADERBOARD_REFRESH_SECONDS`: 120 seconds at
the default. When no generation exists, it answers `rows: []`,
`ranked_users: 0`, `computed_at: null` and `stale: true`. Nothing stored says "stale".

**Why.**
- *Before, not after.* The valuation's snapshot starts at or after the
  stamp, so the board can claim to be older than its data and never newer.
  [L-1] #38: "shows when its values were last updated". An understatement is
  honest. An overstatement would show a fresh time on old numbers.
- *Derived.* [L-3] #40's failure case is a refresh that did not happen. Any
  stored flag would need a writer, and the missing writer is the failure.
  Deriving it from the age of the newest generation needs nobody to notice.
- *One clock.* The same service's Python clock stamps the generation and
  judges its age. With several replicas the stamping and judging clocks are
  different machines, and the skew that allows is accepted (see the
  advisory-lock entry's Notes). "One clock per request, read at the
  controller, Python's not the transaction's" gives that reason for display
  reads, and
  "A settlement's `recorded_at` is Python's clock read after the book lock"
  is the ledger's precedent for a stamp.
- *Twice the interval.* One missed refresh is noise. Two in a row is a
  failure worth showing. Tying the threshold to the interval keeps it right
  when the interval is changed.

**Rejected.**
- *Postgres's `clock_timestamp()`*, as market_service stamps decisions ("Every
  decision a market records is stamped with Postgres's `clock_timestamp()`").
  That rule orders a market's own history across replicas. Here the cost is
  the skew case in the advisory-lock entry's Notes: a skipped refresh, never
  a wrong ranking. Ruled Python's for this ticket.
- *A fixed 120-second constant.* It would read "always stale" the day the
  interval is raised past it.
- *Stamping after the valuation.* That claims freshness the data does not have.

**Notes.** `stale` is for the generation actually served. A page read
through a cursor into the previous generation reports that generation's
`computed_at` and its own `stale`.

**Reversal trigger.** A reader that compares `computed_at` with a timestamp
from another service, such as a trade's `occurred_at`. Mixed clocks start
mattering then, and the stamp moves to `clock_timestamp()`.

---

### D-NEW — Every ledger `USER` account holder is ranked, administrators included; nobody else is

**Date:** 2026-10-11 · **Ticket:** #38, #39 · **Status:** active

**Decision.** A refresh values every `ledger.accounts` row with
`kind = USER`, whatever the owner's role. `PLATFORM` and `MARKET_POOL`
accounts are not users and are never ranked.

A user with no account has nothing to rank and is not on the board. They
have never read their own balance, history or portfolio, and never traded.
Each of those mints the grant. An administrator's read of somebody else's
balance or history does not ("An administrator's read of somebody else's
balance or history mints nothing"). A user with an account and no trades is ranked at
their balance.

**Why.** The ledger cannot see roles: they live in auth and in the
token. Ranking administrators therefore needs no role in the lookup. An
administrator who trades has a net worth like anyone else. Whether they
should trade at all is a separate question, which [BE] #221's
`admin_has_a_stake` asks for one market. Leaving out users with no account
follows from "The leaderboard read writes nothing, and mints no grant": the
ledger only knows the users who have arrived.

[L-2] #39 asks that "A new user with no trades can still appear according to
the platform's eligibility rule". They do, at their starting credits, from the
first refresh after their account is minted. The navbar reads the balance
for every signed-in user (`AppNavbar.tsx`, through `BalanceContext`), so in
practice that is within one interval of the user's first visit.

**Rejected.**
- *Excluding administrators.* The lookup would have to return the role, which
  is a fourth field from auth. It also adds a rule nobody asked for.
- *Ranking every registered user.* The refresh would have to list auth's
  users and mint a grant for each. That writes credits for accounts that
  have never signed in, and puts auth's user list on the ledger's write path.

**Notes.** An account id that auth's lookup does not return is not a user
this platform knows. It is left out and logged at WARNING, and the refresh
continues. No route deletes a user today, so this should not happen.

**Reversal trigger.** A product rule that administrators are referees and
must not appear, or a minimum-activity rule (for example, "has traded at
least once"). Either changes this entry, not the ADR.

---

### D-NEW — A suspended account is left out of the ranking, and ranks close up with no gap

**Date:** 2026-10-11 · **Ticket:** #38, #14 · **Status:** active

**Decision.** A refresh drops every holder whose lookup answer has
`is_suspended: true`, then numbers the rest 1..N. The suspended user's rows
in `ledger.*` are untouched, and so are their positions. They reappear on the
first refresh after the suspension is lifted.

**Why.** [L-1] #38: "excluded or clearly handled according to one documented
platform rule". Leaving them out is the rule that needs nothing from the
frontend. A rank of 4 after a rank of 2 would make every reader wonder who
is missing, and would mark the place where somebody was suspended. [4.2] #14
keeps positions on suspension, so the account's state is kept. Only its
visibility changes.

The column `auth.users.is_suspended` exists already and defaults to false.
This rule is built and tested now, by setting the column directly in auth's
tests, and it affects real users once #14 writes the column.

**Rejected.**
- *Shown, with a "suspended" badge.* Another field in the contract, more work
  for #58's UI, and it publishes a moderation action against a named user.
- *Excluded, with ranks keeping their gaps.* See above.
- *Deferring the rule to #14.* That was the ADR's fallback, and it was not
  needed: ADR 0021 was accepted, so the refresh reads the flag itself.

**Reversal trigger.** A moderation policy that wants suspensions to be
visible.

---

### D-NEW — Ranks run 1..N by net worth descending, then `lower(username)` ascending, decided in Python at refresh

**Date:** 2026-10-11 · **Ticket:** #38 · **Status:** active

**Decision.** The refresh sorts its eligible holders in Python with the key
`(-net_worth, username.lower())` and numbers them 1..N. Equal net worths get
different, consecutive ranks. No rank is shared, and none is skipped. Paging
and reading compare `rank`, an integer, and never a username.

**Why.** [L-1] #38: "Ties use a documented deterministic tie-breaker." Two
users with the same `lower(username)` cannot exist (auth's
`uq_users_username_lower`), so the order is total. Net worth is a `Decimal`
on the scale-4 grid, so the first key is an exact comparison.

Auth accepts only `^[A-Za-z0-9_-]+$`, 3 to 32 characters
(`auth_service/model/schemas.py`, `_USERNAME_RE`), so every username is
ASCII. `str.lower()` and Python's code-point ordering are exactly ASCII
case-folding and byte order for that alphabet. Sorting in Python, once, at
refresh, keeps the comparison out of the database. There, `ORDER BY` follows
the database's collation, which is `en_US.utf8` on both `cs464` and
`cs464_test`. glibc collations have changed how they order `_` and `-`
between versions. The comparison is pinned in one place: the refresh's sort
key.

**Rejected.**
- *Shared ranks ("1, 2, 2, 4").* Ranks would no longer identify a row, and
  paging would need a second key.
- *Ties broken by `user_id`.* It is deterministic, but no reader can see why
  one user is above another.
- *Ties broken by account age.* "Who joined first" is a policy nobody asked
  for, and it needs `created_at` from auth.
- *Sorting in SQL with `COLLATE "C"`.* That is correct too, but it is a
  second place that has to stay pinned, and the ranks are assigned in Python
  anyway.

**Reversal trigger.** Auth widening the username alphabet past ASCII. Then
`lower()` stops being the right fold (`casefold()`, plus Unicode
normalisation), and this entry is revisited together with auth's uniqueness
index, which folds with Postgres's `lower()`.

---

### D-NEW — A leaderboard cursor names its generation; the current and previous generations are served, and an older one is `409 leaderboard_refreshed`

**Date:** 2026-10-11 · **Ticket:** #38 · **Status:** active

**Decision.** `next_cursor` is `shared/paging.py::encode_fields(generation_id,
last_rank)`. A page with no cursor reads the newest generation. A page with a
cursor reads that cursor's generation, from `rank > last_rank`, while the
generation still exists. Once it has been pruned the answer is `409
leaderboard_refreshed`, and the client starts again from page one. A cursor
that does not decode is `400 malformed_cursor`, like every other cursor in
the ledger. The page is read in one statement that joins the generation to
its entries. `limit` defaults to 50 and is clamped at 200.

**Why.** A walk through the ranking has to stay inside one ranking. Paging
by rank across a refresh would show one user twice, or skip one, whenever
two users swap places between pages. That is the OFFSET bug in another form,
the one "The audit feed and the user list page by keyset, not OFFSET" exists
to prevent. Keeping exactly one older generation means a cursor lives
through at least one refresh. With one replica that is between 60 and 120
seconds after its first page, which is long enough for a person clicking
"next".

**Rejected.**
- *Serving the current generation for any cursor.* This is the duplicate and
  skip bug above, without the client being told.
- *Keeping every generation for an hour.* That is sixty copies of the
  ranking, so the tables grow with users × 60, to support a pager nobody
  holds open that long.
- *`410 Gone`.* The resource still exists. Only the client's position in it
  has gone. `409` is how the ledger already reports that a client's view is
  out of date (`quote_stale`).

**Notes.** With N replicas each refreshing, a generation can be pruned
sooner: N refreshes a minute means a cursor may last only a few seconds.
Compose runs one ledger today. A second replica either turns
`LEADERBOARD_REFRESH_ENABLED` off, or this entry gains a "skip if a
generation is younger than half the interval" rule.

**Reversal trigger.** A second ledger replica with refresh enabled, or `409`s
showing up in the frontend's error reports.

---

### D-NEW — The refresh runs every 60 seconds in a loop on each replica, behind `LEADERBOARD_REFRESH_ENABLED`, and a failed refresh writes nothing

**Date:** 2026-10-11 · **Ticket:** #38, #40 · **Status:** active

**Decision.** `main.py`'s lifespan starts one refresh loop when
`LEADERBOARD_REFRESH_ENABLED` is true, which is the default. The loop runs a
refresh at once, then one every `LEADERBOARD_REFRESH_SECONDS` (default `60`,
`gt=0`). A refresh is:

1. Stamp `computed_at`.
2. Value every account holder in one statement ("The refresh values holders
   through the portfolio's valuation"), and end that transaction.
3. Call auth's lookup, in chunks of 500 ids (auth's maximum), holding no
   connection.
4. Exclude, sort and rank.
5. Write, under the advisory lock.

The loop catches every `Exception`, logs it at ERROR and waits for the next
tick. Any failure means nothing is written: a book `MarketBookIncomplete`
refuses, an auth error, a timeout, or a non-200 answer. The previous
generation stays current, and reads mark it stale once it is old enough.

**Why.** [L-3] #40 offers "after committed trades, market settlement, or
according to a documented refresh interval". An interval is the only one of
the three that needs no change to the trade or settlement path. ADR 0018
already showed that every trade moves every holder of that market, so a
per-trade refresh would be a full recompute per trade. The loop's shape is
`market_service/service/sweeper.py`'s: start at once, swallow `Exception`,
and let `CancelledError` through. ADR 0011 gives the reason: a task that has
died is a feature that has stopped with nobody told.

A damaged book fails the whole refresh, for the reason "A damaged book, or a
position above `q`, fails the whole portfolio with `market_book_incomplete`"
gives. Ranking without one holder's positions ranks them wrongly, and showing
them in the wrong place is the partial ranking #40 forbids.

**Rejected.**
- *Refresh after every committed trade.* The trade path would gain work that
  can fail, and its cost grows with every holder.
- *A cron container or Redis-scheduled job.* That is more moving parts for a
  timer, and ADR 0011 already declined the same for the close sweep.
- *Skipping a holder whose book is damaged.* That is a silent partial ranking.

**Notes.** Turning it off on every replica stops the board updating, and it
turns stale after two intervals. That is safe and recoverable, the same as
`CLOSE_SWEEP_ENABLED`. Leave it on somewhere. The auth client is held for the
process, with the terms client's five-second timeout, as #114 did for
market_service.

**Reversal trigger.** A refresh's measured duration approaching its
interval, or a product requirement that the board move within seconds of a
trade.

*Amended 2026-10-16 by #40: built as specified, with two readings made
explicit. "Then one every `LEADERBOARD_REFRESH_SECONDS`" and "waits for the
next tick" mean the loop sleeps that long after each refresh finishes. It is
not a fixed rate, so a refresh that outlasts its interval delays the next one
and cannot overlap it on one replica, which is what "A refresh writes under a
transaction-level advisory lock" assumes. And a failed refresh is logged once,
with its traceback, by `logger.exception` before the sleep. `CancelledError`
is not an `Exception`, so it passes through and shutdown still stops the loop.
The loop's module, its first refresh, its shutdown order and the disabled
warning are in the entries #40 appends at the end of this file.*

---

### D-NEW — The refresh values holders through the portfolio's valuation, and the portfolio keeps its one-statement read

**Date:** 2026-10-11 · **Ticket:** #38 · **Status:** active

**Decision.** A refactor PR, ahead of #38's service PR, moves the pure part
of `service/portfolio.py` into one function that both callers use. That part
is `_positions_of`, the two branches it chooses between, and the
`positions_value` and `net_worth` sums. It takes one user's balance and rows,
and returns their positions and totals.

The portfolio's `_read` stays one statement that reads balance and positions
together, for one user. The refresh gets its own single statement with the
same row shape, for every `USER` account, ordered by
`(owner_id, market_id, position)`. Every holder is valued from one snapshot.
`held_outcomes_query` shares the columns and the four joins, not just the row
shape; each caller brings only its anchor (balance and holder), its
`held_markets` filter and its ORDER BY. The refactor PR changes no test
assertion.

**Why.** ADR 0018's 2026-10-10 amendment: #38 "reuses the portfolio's
valuation, its settled branch included", and calling
`liquidation_values_of` directly would pay winners twice. "The portfolio
reads its balance and positions in one statement, after the grant, with no
lock" is why the read stays one statement: a trade committing mid-read
cannot show its debit without its shares. One statement for every holder
extends that guarantee to the whole board. No user is valued before a trade
and another after it.

**Rejected.**
- *Calling `portfolio_of_user` per account holder.* Its statements see
  different snapshots, it mints a grant, and it costs one round trip per user.
- *A separate leaderboard valuation.* This is the copy the amendment forbids.
- *The refresh using a `REPEATABLE READ` transaction of per-user
  statements.* That would be consistent, but it means N statements where one
  will do.

**Notes.** CLAUDE.md: "A refactor never shares a PR with a behaviour change."
The extraction is the refactor. The second statement is #38's.

---

### D-NEW — The leaderboard read writes nothing, and mints no grant

**Date:** 2026-10-11 · **Ticket:** #38 · **Status:** active

**Decision.** `GET /ledger/leaderboard` needs a valid access token and does
not call `grants.ensure_granted`. It reads one generation and returns.

The ledger's reads that write stay at five:

- `GET /ledger/balances/me`, `GET /ledger/entries/me` and
  `GET /ledger/portfolio/me` mint the caller's grant (`ensure_granted` in
  `ledger_service.balance_of_user`, `history_for_user` and
  `portfolio.portfolio_of_user`).
- The preview and the snapshot open a market's book on first touch.

**Why.** The board is not about the caller. A grant minted here would not put
the caller on the current generation anyway, because only the next refresh
can. So a write on this route buys nothing anyone can see for up to a
minute, and it would turn a cacheable read into one that can fail on a write.

**Rejected.** *Mint the caller's grant, as the portfolio does.* It is
invisible until the next refresh, and it adds a sixth write-on-read to a
list the API doc keeps short on purpose.

**Notes.** [L-2] #39's own-row field reports a caller who has no account the
same way as one who is not ranked: `me: null`. That is the [BE][L-2] issue's
to specify.

---

### D-NEW — Exposure flags a payout the pool cannot cover, not one above the seed

**Date:** 2026-10-10 · **Ticket:** #6 · **Status:** active

**Decision.** `payout_exceeds_pool` is true when any outcome's shares
outstanding are strictly greater than the market pool's balance. Each outcome
also reports `residue_if_wins`, which is the pool minus that outcome's payout.
The flag is true exactly when some residue is negative. A residue of exactly
zero pays out in full with nothing left over, so it does not raise the flag.

**Why.** Read literally, [2.2] #6's third criterion compares the payout with
the seed subsidy. The seed is the smallest amount a pool can hold before
settlement ("A sell cannot take a market's pool below its seed subsidy"), so
that reading flags almost every market that has been traded at all. At
b = 100 with a seed of 50, one buy of 51 shares charges 28.7167 and raises the
flag, even though the pool then holds 78.7167 and covers it. The question an
admin has before resolving is whether the platform will have to make up the
difference. Settlement pays winners from the pool and takes any shortfall from
PLATFORM, so the pool is the number that answers that question.

**Rejected.** *The seed, as the criterion is worded*: it ignores every credit
traders have paid in, and it flags markets that are fully covered.
*`b·ln(n)`*: that is the worst case LMSR allows, not what this market owes
right now. It also doesn't change, so the admin panel already shows it at
creation. *A top-level `max_payout`*: it is just the largest `payout_if_wins`.
Sending it would give the response two sources for one number. *A non-strict
`>=`*: it would flag a market that pays out exactly what it holds.

**Notes.** `residue_if_wins` uses the same sign as the SETTLEMENT_RESIDUE
movement in ADR 0019 (Settlement design, #220): positive goes to PLATFORM,
negative comes from PLATFORM. So the exposure read before resolution and the
settlement afterwards will agree to the last place.

**Reversal trigger.** A payout other than one credit per share (fees, scalar
markets), or one that pays winners from somewhere other than the pool.

---

### D-NEW — Exposure on a market with no book is a 200 with nothing in it, and writes nothing

**Date:** 2026-10-10 · **Ticket:** #6 · **Status:** active

**Decision.** If the ledger holds no book for `market_id`, the exposure route
answers `200`. That covers a published market nobody has previewed, traded or
snapshotted, and also an id that names no market at all. In that response
`opened_at`, `state_version`, `seed_subsidy`, `pool`, `result` and
`winning_outcome_id` are null, `outcomes` is empty, and `payout_exceeds_pool`
is false. The route never opens a book, never
funds a pool and never calls market_service.

**Why.** Without asking market_service, the ledger can't tell a cold market
from an unknown id. It doesn't ask market_service on this route (see the
amendment on ADR 0005). So a `404` would sometimes be wrong about a real market
that simply has no trades yet. That case has zero exposure, so it is an
answer, not an error. Opening the book here would post the seed from PLATFORM
because an admin looked. "An administrator's read of somebody else's balance
or history mints nothing" has already ruled out that kind of side effect: an
admin read investigates and should not change what it investigates.

**Rejected.** *`404` for an id without a book*: wrong for a published market
that hasn't been touched yet. *Opening the book the way the preview and the
snapshot do*: the read would then write, and it would put market_service on
the request path. *Fetching the seed from market_service so a cold market can
show its pool*: that is the second call the amendment on ADR 0005 removes, and
the frontend already has the seed from market_service's own detail.

**Notes.** `opened_at: null` is how a client spots this case. Because no flag
can be raised here, a typo in the id reads as a quiet market and not as an
alarm.

**Reversal trigger.** The ledger opening a book at publish time instead of on
first touch. Every published market would then have a book, an id without one
would really be unknown, and `404` would become the right answer.

---

### D-NEW — Exposure reads `q` and the pool in one statement, with no lock

**Date:** 2026-10-10 · **Ticket:** #6 · **Status:** active

**Decision.** One SQL statement under READ COMMITTED, with no `FOR UPDATE`. It
reads the book (`opened_at`, `state_version`, `seed_subsidy`), every outcome
row of the book ordered by `position`, and the pool's balance as a scalar
subquery built from `accounts.balance_query_of`, given the book's
`pool_account_id` column. That is the expression `balance_of` uses, extracted
in #262.

**Why one statement.** This follows the same reasoning as "Preview reads `q`
and `state_version` in one SQL statement" and "The portfolio reads its balance
and positions in one statement, after the grant, with no lock". Under READ
COMMITTED each statement gets its own snapshot. If `q` and the pool were read
in separate statements, a trade could commit between the two reads, and the
residue would be off by that trade's cost. That error can raise or clear the
flag. With one statement, `q`, the pool and `state_version` all describe the
same moment.

**Why no lock.** ADR 0015 asks for a lock on every read whose answer decides a
write. This read decides none. A `FOR UPDATE` on the book would queue every
trade on the market behind an admin's page load and protect nothing.

**Rejected.** *Two statements inside a REPEATABLE READ transaction*: correct,
but it only adds an isolation level to buy the same consistency one statement
already gives. The overview needed that because it pages ("The admin overview
pages by keyset, settled markets last, and reads its counts in the same
REPEATABLE READ snapshot"), and this read doesn't page. *Calling `balance_of`
after the outcome read*: that is the two-snapshot bug described above.

**Reversal trigger.** Something starts gating a write on this read, for
example settlement refusing to run while the flag is up. That read then needs
the book lock, taken before the pool's account lock.

---

### D-NEW — The settled-market check is owed by whichever of #6 and #258 lands second

**Date:** 2026-10-10 · **Ticket:** #6, #12 · **Status:** closed by "A settled market's exposure reads its result, and reports no payouts against the drained pool"

**Decision.** This PR does not handle a settled market. Whichever of this PR
and #258 merges into `dev` second adds the check that keeps the exposure read
honest about a settled market, together with its test. #220 is not edited for
this.

**Why.** Settlement empties the pool: winners are paid, and the residue moves
to or from PLATFORM, so "the pool ends at zero either way". `q` is not reset.
Once a market has settled, this read would show every outcome with shares as
`residue_if_wins = −q` and raise the flag, so a finished market would look
like the most exposed one in the system. Neither PR can fix this alone. On
`dev` today there is no settlement row to read and no way to put a market into
that state. The settlement stack, for its part, doesn't know the exposure
route exists. The PR that lands second is the first one that can write a test
that goes red.

**Rejected.** *Writing the check here against `ledger.market_results`*: that
would tie this PR to a table on an unmerged stack, and the test could not run
on `dev`. *Adding it to #220*: the ADR would end up describing a route it
never designed. *Doing nothing*: the flag would be wrong for every settled
market from the day both PRs are merged.

**Notes.** Tracked in #263. The PR that owes the check decides its shape and
records it. At minimum, a settled market must not report a flag or residues
computed against a drained pool. Done in #267.

**Reversal trigger.** None. This entry is closed by whichever PR lands second.

---

### D-NEW — A settled market's exposure reads its result, and reports no payouts against the drained pool

**Date:** 2026-10-11 · **Ticket:** #6, #263 · **Status:** active

**Decision.** The exposure read's one statement outer-joins
`ledger.market_results` on `market_id`. Every response carries two more
top-level fields:

- `result`: null, or the record's `kind` as a string (`"settled"`).
- `winning_outcome_id`: null, or the record's `outcome_id`.

When a record exists, `pool` is null, `payout_exceeds_pool` is false, and every
outcome's `payout_if_wins` and `residue_if_wins` are null. `shares_outstanding`,
`seed_subsidy`, `opened_at` and `state_version` stay as read. The read branches
on whether the record exists, never on `kind`.

**Why.**
- *The figures would be wrong, not just stale.* Settlement drains the pool and
  leaves `q` ("`ledger.market_results` is the latch, and positions and `q` are
  never written"). Computed as before, every outcome with shares reads
  `residue_if_wins = −q` against a pool of zero and raises the flag, including
  the outcome that lost. A finished market would look like the most exposed
  one in the system.
- *The same statement.* "Exposure reads `q` and the pool in one statement, with
  no lock" holds for the record too: read in a second statement, a settlement
  committing between the two would pair a drained pool with no record and
  raise the flag.
- *Nulls, not missing keys.* One shape on every response, so a client branches
  on the value of `result`. It is the portfolio's rule ("`result` and `payout`
  are on every portfolio row, null until the market is settled").
- *Not on `kind`.* Like the latch's readers and the portfolio, so [BE] #221's
  void reaches this branch too, and its pool is drained just the same.
- *Shares stay.* They are what the winners were paid on, and the admin panel
  can still show what each outcome held at the end.

**Rejected.**
- *Figures against the pool as it stood before settlement*: another read, for
  a question nobody asks once the payout is made. The settlement's own figures
  are in its response and its `market.settled` audit entry.
- *A `404` or `409` for a settled market*: the admin panel links Exposure on
  settled markets ([FE] #265), and a settled market is an answer.
- *Omitting the fields with `exclude_none`*: two shapes, and it would strip
  the cold market's nulls too.
- *Renaming the fields already on the wire*: #265 reads `payout_exceeds_pool`
  and `payout_if_wins`.

**Notes.** A void from #221 will read with a non-null `result` and a null
`winning_outcome_id`, and #221 must say so in the API doc. `state_version` on a
settled market is the frozen book's last version. Closes "The settled-market
check is owed by whichever of #6 and #258 lands second".

**Reversal trigger.** Settlement resetting `q`, or leaving the pool funded.
Then the figures mean something again after settlement.

*Amended 2026-10-20. Decided by ADR 0025, and not built yet.* The Notes' owed
line is written: `docs/api/ledger-service.md` says a voided market reads
`result: "voided"`, a null `winning_outcome_id` and the null figures. No code
changes.

---

### D-NEW — Suspending the last administrator reuses `last_administrator`

**Date:** 2026-10-11 · **Ticket:** #14 · **Status:** active

**Decision.** `set_suspension` refuses to suspend the last administrator who
can log in with the existing `409 last_administrator`, the same class and
message the role change raises. Its docstring widens from "demoting this user"
to "demoting or suspending this user".

**Why.** Same cause, same fix: make somebody else an administrator first. A
client does nothing different for the two, and this service's rule is one code
per thing a client can do about it. The message ("This is the only
administrator. Promote another one first.") reads correctly for both. Like the
demotion, it is reachable only when two administrators act at the same
moment: through the route the caller is an active administrator and not the
target, so the caller is always left.

**Rejected.** A separate `last_administrator_suspension`. A second code for one
remedy.

**Notes.** The guard discards the target from the locked set rather than
testing membership, as `change_role` does: a suspended administrator is absent
from the set and must still be a valid target (ADR 0007). Unsuspending only
grows the set and has no guard, but still takes the set lock first, so a
suspension and a role change cannot deadlock (ADR 0015).

---

### D-NEW — A suspension needs a reason both ways, checked by a copy of market_service's reason rule

**Date:** 2026-10-11 · **Ticket:** #14 · **Status:** active

**Decision.** `PATCH /admin/users/{user_id}/suspension` requires `reason` to
suspend and to unsuspend. The schema requires the key and caps it at 500
characters, as `RoleChangeRequest.reason` does. The service trims it and
refuses blank or under ten characters with `422 suspension_incomplete`, whose
`details` name `reason`. The trimmed reason is what the audit entry stores. The
rule is a copy of market_service's `service/validation.py::_reason_problems`,
with a one-line comment naming the twin.

**Why.** The acceptance criteria say "requires a reason". An unsuspension is a
decision about somebody else's account too, and an entry that says only "back
on" answers nothing (Ernest, 2026-10-11). The copy stays a copy: two services
and two forms, and the floors moving apart would be a design choice rather than
a bug, which is below ADR 0012's bar for `shared/`. Auth has no
`IncompleteError` base like market_service's, so `SuspensionIncomplete` is a
plain `AuthError` carrying `problems`, as `DuplicateUser` already is. Its
message fits both directions: "A reason is required to suspend or unsuspend an
account."

**Rejected.** A reason on suspend only. Moving `_reason_problems` into
`shared/`.

---

### D-NEW — Suspending revokes every refresh token the account holds

**Date:** 2026-10-11 · **Ticket:** #14 · **Status:** active

**Decision.** `set_suspension` calls `revoke_all_for_user` when it suspends,
under the target's row lock, before it sets the flag. Unsuspending revokes
nothing.

**Why.** Refresh already refuses a suspended account, so revoking changes what
happens *after* unsuspension. Without it a 14-day refresh cookie starts working
again the moment the account is unsuspended, and the user is silently logged
back in on every device they had. With it, unsuspension means "may log in
again". "The refresh path locks the user row before the token" assumed this
ticket would revoke, and requires the row lock it holds.

**Rejected.** Revoking nothing, because refresh refuses anyway: the sessions
come back on unsuspension.

**Notes.** The revoke is written before the flag. Under the row lock the order
does not matter, and auth's sessions do not autoflush, so neither order lets
the flag's `UPDATE auth.users` wait on a concurrent rotation and hide a missing
lock: `test_a_refresh_racing_a_suspension_keeps_no_session` goes red without
`lock_user` in both orders. Revoke-first is kept because it stays right if
autoflush is ever turned on.

---

### D-NEW — A suspension that changes nothing writes nothing, decided from the locked row

**Date:** 2026-10-11 · **Ticket:** #14 · **Status:** active

**Decision.** Asking for the state the account already has writes nothing,
revokes nothing and records nothing, and returns `200` with the user. The
check reads `is_suspended` from the row `lock_user` locked.

**Why.** The role change's rule, and the reason the body names a state rather
than an action: a double-submitted form is the same outcome. Two
administrators clicking Suspend on the same abusive account at once is the
realistic case, and deciding under the lock makes it one entry rather than two.

---

### D-NEW — The suspension's audit entry carries the target's role in `context`

**Date:** 2026-10-11 · **Ticket:** #14 · **Status:** active

**Decision.** `user.suspended` and `user.unsuspended` carry
`context = {"role": <the target's role at the time>}`, beside the trimmed
`reason` and a snapshotted `target_label`.

**Why.** A reader of the log should tell an administrator's suspension from a
trader's, and the role can change afterwards. `audit_svc` cannot read
`auth.users` to look it up, which is why the label is snapshotted too. The
reason lives only here: there are no `suspended_at` or `suspended_reason`
columns (Ernest, 2026-10-11), so the log is the record.

---

### D-NEW — Login decides suspension under the user's row lock

**Date:** 2026-10-11 · **Ticket:** #14 · **Status:** active

**Decision.** After the password is verified, `authenticate` re-reads the user
with `lock_user` and checks `is_suspended` on the locked row. The lock is held
until the login commits its new refresh token.

**Why.** Revoking every session is a promise only if no session can be created
during the suspension. `authenticate` read the row without a lock, so a login
that read "not suspended" just before an administrator's suspension committed
could insert its refresh token after the revoke. That token does nothing while
the account is suspended, and comes back to life on unsuspension: exactly what
the revoke exists to prevent. CLAUDE.md's "a lock on the transition is half a
lock": the read that decides the write must be locked. With the lock the login
either waits for the suspension and is refused, or goes first and has its token
revoked by the suspension that follows. After the password, so suspension
still cannot be probed ("Suspension is checked only after the password is
proven").

**Rejected.** Leaving it: a suspension would not end every session.

**Notes.** Lock order stays acyclic. Every path that touches a user row and a
token takes the user row first; only the two admin paths take the
administrator set, and both take it first.

---

### D-NEW — One auth client in the ledger, for #14's trade check and #38's leaderboard

**Date:** 2026-10-11 · **Ticket:** #14 · **Status:** active

**Decision.** The ledger holds one `httpx.AsyncClient` for the auth service,
built on `main.py`'s lifespan beside the terms client, with explicit
five-second timeouts and a setting `auth_service_url` defaulting to
`http://auth:8000`. Whichever of #14 and [L-1] #38 lands first builds it; the
other reuses the client and the setting.

**Why.** ADR 0022's per-trade check calls auth on every trade, so a client per
call would pay a handshake on the hottest path, as #114 found for
market_service. ADR 0021's leaderboard refresh needs a client for the same
host. The timeout is stated rather than inherited (D-030), and is its own
constant rather than the terms client's: the two dependencies' budgets may
reasonably diverge. The default matches compose's hostname, as
`market_service_url`'s does, so compose needs no entry.

**Rejected.** A client per service that calls auth. Two clients for one host.

---

### D-NEW — Send-back is `POST /markets/{id}/send-back-outcome`, quoting `proposal_id`, with a reason, by any administrator

**Date:** 2026-10-11 · **Ticket:** #11 · **Status:** active

**Decision.** An APPROVED market inside its dispute window is sent back to
CLOSED by `POST /markets/{id}/send-back-outcome`, with an administrator's
token. The body is `OutcomeSendBackRequest`:

- `proposal_id`: a required key with a nullable value, the id of the approved
  proposal the administrator is looking at, read from the latest
  `market.outcome_approved` audit entry for the market. `null` only for an
  approval whose proposal had none. Its field description is its own, not
  approve's and reject's shared one, which talks about a pending proposal.
- `reason`: a required `str`, at most `MAX_PROSE_LENGTH`. A missing key is
  FastAPI's `422`. Its content (at least `MIN_SEND_BACK_REASON_LENGTH`, 10,
  characters after trimming) is checked by `problems_blocking_send_back`,
  through `_reason_problems`.

It answers `200` with `MarketOut`, as reject does. Any administrator may call
it, including the proposer and the approver (Ernest's D1). ADR 0023.

**Why.**
- *Its own endpoint*: the route list is the list of legal moves (ADR 0008,
  0013, 0016).
- *`proposal_id` required*: the approved result can be replaced on the same
  row by a send-back, a new proposal and a new approval, all committed while
  a reviewer reads. The lock cannot see that. CLAUDE.md, "A decision names
  the proposal, because the lock cannot".
- *Any administrator*: the AC says "any admin". The two-person rule protects
  what gets paid, and every path after a send-back goes through propose and a
  different administrator's approval again.
- *A separate reason constant*, for the reason the close and reject
  constants give each other: different rules that agree on a number today.

**Rejected.**
- *Widening `reject-outcome` to APPROVED*: reject refuses the proposer and has
  no window. Two moves, two routes.
- *Quoting `approved_at`*: a timestamp used as an identity, as ADR 0016
  refused `proposed_at`.
- *Refusing the proposer or the approver*: see Why.
- *A "has traded" check like #221's*: market_service cannot see trades, and
  asking the ledger is the cycle ADR 0019 refused.

**Reversal trigger.** Repeated send-backs delaying payouts become a real
problem. A limit, or a rule on who may send back, is then a new decision.

---

### D-NEW — Send-back checks the lock, the state, the clock, the window, the proposal and then the reason

**Date:** 2026-10-11 · **Ticket:** #11 · **Status:** active

**Decision.** `send_back_outcome` checks in this order:

1. `401 invalid_token`, then `403 not_an_administrator`.
2. `get_any` with the row locked; `404 market_not_found`.
3. State: SETTLED is `409 market_already_settled`; PENDING_RESOLUTION is
   `409 market_pending_resolution`; any other status that is not APPROVED is
   `409 market_not_approved`.
4. On the APPROVED branch only, `clock_timestamp()`, read after the lock.
5. The window: allowed only while `now < dispute_window_ends_at`, through
   `core/settling.is_in_dispute_window`. Otherwise, and on a null end,
   `409 dispute_window_closed`.
6. No identity check.
7. `_refuse_if_superseded(market, proposal_id)`: `409 proposal_superseded`.
8. The reason: `422 send_back_incomplete`.

**Why.**
- *State before the clock*, as the settle step does it: a market that writes
  nothing needs no clock.
- *Window before the proposal id*: if the window has closed on whatever is
  approved now, "the result is final" is the useful answer, and it is true
  for a stale page too.
- *Proposal id before the reason*: nobody is asked to improve a reason about
  an approval that no longer exists (ADR 0016's order).
- *`clock_timestamp()` after the lock*: "Every decision a market records is
  stamped with Postgres's `clock_timestamp()`, read after the row lock". The
  settle step reads the same clock after the same lock, so the two writers
  agree about the edge whatever any reader's clock says.
- *Exclusive edge*: settlement opens at the end plus five minutes, inclusive,
  so no instant allows both.
- *PENDING_RESOLUTION keeps its own code*: its remedy differs (review the
  proposal and reject it).

**Rejected.**
- *`func.now()`*: the transaction's start, read before the lock wait (#138).
- *Python's clock*: a replica running ahead or behind would move the edge.
- *The proposal id before the window*: a stale page would be told to reload
  and review an approval it can no longer send back.

**Reversal trigger.** market_service's decision stamps move off
`clock_timestamp()`, or the five-minute gap is replaced by a lock-based claim.

---

### D-NEW — A send-back clears eleven columns, keeps `closed_at`, and logs a twelve-key snapshot taken before the clear

**Date:** 2026-10-11 · **Ticket:** #11 · **Status:** active

**Decision.** A send-back sets `status = CLOSED` and nulls eleven columns:
the seven proposal columns (`proposal_id`, `proposed_outcome_id`,
`proposed_by_id`, `proposed_by_username`, `proposed_at`,
`proposal_evidence_url`, `proposal_evidence_note`), the three approval
columns (`approved_by_id`, `approved_by_username`, `approved_at`) and
`dispute_window_ends_at`. `closed_at`, the terms, the outcomes and the
sources are untouched. Before the first column is nulled, it takes
`market_audit.approval_snapshot(market)`: `decision_snapshot`'s eight keys
plus `approved_by_id`, `approved_by_username`, `approved_at` and
`dispute_window_ends_at`. `record_send_back` appends
`market.outcome_sent_back` under the sender, with the trimmed `reason`, that
snapshot as `context`, and the step's `clock_timestamp()` as `occurred_at`,
in the request's transaction. The reason is stored nowhere else (Ernest's
D3).

**Why.**
- *The approval columns go too*: ADR 0016 and `MarketOut` say they are null
  unless the market is APPROVED. A CLOSED market with an approver would be
  wrong, and the next approval overwrites them anyway.
- *`closed_at` stays*: trading stopped when it stopped, as on a rejection.
- *Snapshot before the clear*: after it, the entry is the only copy of what
  was approved, by whom, and when its window would have ended, and
  `audit_svc` cannot read `market.markets`.
- *`record_send_back` takes the snapshot rather than building it*, for the
  reason `record_rejection` gives.
- *Log only*: it matches reject ("the audit entry is the record"). A
  send-back is temporary and moves no money, unlike #221's void.

**Rejected.**
- *Keeping the approval columns*: see Why.
- *`sent_back_by_*` columns, or a reason on the market*: after the clear
  there is no approval on the row for them to describe.
- *A trader-visible note*: not needed now, and additive later.

**Reversal trigger.** Traders need to see why a result was sent back.

---

### D-NEW — `dispute_window_closed` is a new 409, and send-back's reason refusal is `send_back_incomplete`

**Date:** 2026-10-11 · **Ticket:** #11 · **Status:** active

**Decision.** Two new errors in `core/errors.py`:

- `DisputeWindowClosed`, `409 dispute_window_closed`: "This market's dispute
  window has ended. Its approved outcome is final and can no longer be sent
  back." Also the answer for an APPROVED market with a null
  `dispute_window_ends_at`.
- `SendBackIncomplete(IncompleteError)`, `422 send_back_incomplete`, with
  `details`: "This approved result cannot be sent back without a reason."

**Why.** The window's end is a different remedy from every existing code:
nothing the caller can do will help, and the result is final. `422` with
`details` is the shape every other reason refusal here uses. A null end is
damage, and the same refusal fails closed, as `settleable` does.

**Rejected.**
- *Reusing `dispute_window_open`*: it means the opposite (too early to
  settle).
- *Reusing `rejection_incomplete`*: a different form, answering a different
  question.

**Notes.** Reject's own copy already says "why this proposal was sent back".
It is left alone. Send-back's copy says "approved result" every time, so the
two never read as the same action. The frontend label is "Dispute result",
never just "Send back".

---

### D-NEW — `market_not_approved`, `market_already_approved` and `proposal_superseded` are widened for send-back

**Date:** 2026-10-11 · **Ticket:** #11 · **Status:** active

**Decision.** The codes stay. The messages and docstrings change:

- `market_not_approved`: "Only a market whose outcome has been approved can
  be settled or sent back. This one has not been approved." The docstring
  stops saying the ledger is its only caller.
- `market_already_approved`: "A second administrator has already approved
  this market's outcome. Next it is settled, unless it is sent back during
  its dispute window." The docstring says "Any write but settle and
  send-back".
- `proposal_superseded`: "The proposal you reviewed has been replaced by a
  newer one. Reload the market and review the current one." The docstring
  names the send-back cycle beside the reject cycle.

**Why.** Each old message stops being true. `market_not_approved` now has a
second caller. "The next step is settlement" is false while a send-back is
possible. "Review the proposal that is waiting now" is wrong for send-back,
where the replacement is approved, not waiting.

**Rejected.** *New codes for send-back's copies of these refusals*: the state
and the remedy are the same, and the frontend already handles these codes.

**Notes.** The ledger never reads market_service's codes from the settle
step. It accepts only a `200`, so the wider `market_not_approved` changes
nothing there.

---

### D-NEW — `dispute_window_ends_at` is stored at approval, and market revision 0002 backfills 24 hours

**Date:** 2026-10-11 · **Ticket:** #11, #12 · **Status:** active

**Decision.** `market.markets.dispute_window_ends_at`, `timestamptz`,
nullable, no default, no CHECK, no index. `approve_outcome` writes it in the
same transaction and from the same clock read as `approved_at`, through
`service/settling.dispute_window_ends_at(approved_at)`: `approved_at` plus
`dispute_window_seconds` as the setting is then. Send-back clears it; settle
keeps it. `is_settleable(*, approved, settled, ends_at, now)` and
`settleable(status, dispute_window_ends_at, *, now)` read it and no longer
read the setting. Market revision `0002_dispute_window_ends_at` adds the
column and a hand-written `UPDATE` sets `approved_at + interval '86400
seconds'` on every row with an `approved_at`. Ernest's D2.

**Why.** Deriving the end from the current setting let a raised window reopen
windows that had closed, including on a market the ledger had already paid
and not yet marked settled. A send-back and a new approval could then leave
market_service naming one winner while the ledger had paid another, for good.
Stored, the setting affects only later approvals. It is `close_time`'s shape:
a stored instant the clock is compared against (ADR 0011).

- *86400 hard-coded*: the migrate step reads `DATABASE_URL` alone ("The
  migrate step reads DATABASE_URL alone, not the service's settings"), so it
  cannot see `DISPUTE_WINDOW_SECONDS`. Only the team's local `cs464`
  databases hold approved markets.
- *No CHECK*: the approval columns have none, the damage tests must write a
  null, and a CHECK naming `'approved'` would be the only database rule that
  reads a status string.
- *No index*: no query scans approved markets by window end.

**Rejected.**
- *Deriving the end on every read*: see Why.
- *Reading the setting in the revision*: its result would depend on where it
  runs.
- *Migrating per test for the backfill*: ADR 0020's dated note. One targeted
  test, `test_markets_approved_before_0002_get_a_window_end`, covers it.

**Notes.** A database that ran a window shorter than 24 hours gets 24 hours
for its existing approvals, once. That can reopen a window that had closed:
a market the ledger paid but whose step 5 failed still reads approved, and
becomes sendable-back and not settleable again. The fix is by hand, before
anyone acts on it: `UPDATE market.markets SET dispute_window_ends_at =
approved_at + interval '<your window> seconds' WHERE approved_at IS NOT NULL`,
or `docker compose down -v`, which destroys local data. If #221's market
revision merges first, this one is renumbered and its `down_revision`
pointed at #221's.

A pre-deploy check for [5.3] #19, which also covers every local database:
before revision 0002 runs, does the database hold approved markets under a
window other than 24 hours? If so, run the `UPDATE` above with that window
right after the migrate step.

---

### D-NEW — `dispute_window_ends_at` is on the public detail and on every overview row, as the raw column

**Date:** 2026-10-11 · **Ticket:** #11 · **Status:** active

**Decision.** `PublicMarketOut` (`GET /public/markets/{id}`) and
`MarketOverviewRowOut` (`GET /markets/overview`, through `AdminMarketCard`
and the overview's column select) gain `dispute_window_ends_at: datetime |
None`, a required key with a nullable value. It is the stored column, passed
through: null on a market never approved and after a send-back, the instant
on an APPROVED market, and kept on a SETTLED one. Not on `MarketOut` or the
trader browse cards.

**Why.** [3.3] #11's countdown, for traders on the market page and for admins
on the overview, where the settle and dispute controls live. It is stored,
not derived, so the "stamped, never a computed field" rule for `status` and
`settleable` has nothing to stamp. A SETTLED market keeps it because it
records when disputes closed. A required key so that a reader cannot mistake
a missing field for "no window".

**Rejected.**
- *Null on a SETTLED market*: it would hide a true fact to make a display
  rule simpler, and the frontend shows the countdown only on `approved`
  anyway.
- *On `MarketOut` and the browse cards*: no screen there shows a countdown.

**Notes.** Display only. The browser shows time left and the absolute time,
and decides nothing from it: the server decides send-back on the database's
clock, and the settle control reads `settleable`. The ledger's `_parse`
ignores keys it does not know, so the new field cannot break it.

---

### D-NEW — #11 lands as a stack of six PRs, docs first

**Date:** 2026-10-11 · **Ticket:** #11 · **Status:** active

**Decision.** Every PR says `Refs #11`, not `Closes`: [FE][3.3] #59 is open,
and #11 closes when #59 merges. The first targets `dev`; each later one
targets the branch below it and says `Stacked on #N`. Branches are
`11-send-back-<n>-<slug>`.

| # | Branch | Kind | Contents |
| --- | --- | --- | --- |
| 1 | `11-send-back-1-docs` | docs | ADR 0023; dated amendments to ADR 0016, 0019 and 0020; these entries and amendments; `docs/api/market-service.md` and `audit-service.md`; root CLAUDE.md |
| 2 | `11-send-back-2-superseded-refactor` | refactor | `_refuse_if_superseded` and `_clear_proposal`, extracted; no test changes |
| 3 | `11-send-back-3-stored-window` | behaviour | the column; revision 0002 and its backfill; `is_settleable` and `settleable` take the stored end; approve writes it; the three callers switch; the #227 and #237 tests that change; the three new tests |
| 4 | `11-send-back-4-countdown` | behaviour | `dispute_window_ends_at` on the public detail and the overview, and its two tests |
| 5 | `11-send-back-5-service` | behaviour | the action, the request, the errors, `is_in_dispute_window`, the reason rule, `approval_snapshot` and `record_send_back`, `send_back_outcome`, and its tests, including the races and the replacement cycle; one `dead_code_allowlist.py` line until its route lands |
| 6 | `11-send-back-6-route` | controller | the route, its OpenAPI description and responses, the controller tests, and the allowlist line removed |
| 7 | #59 | frontend | Michelle, against the merged contract, with its Playwright test |

**Why.** CLAUDE.md: one layer or one behaviour per PR, each green on its own.
Docs go first so the design is reviewed before the code, as "Settlement lands
as a stack of eight PRs, docs first" did. PR 3 changes assertions written for
#227 and #237, which is expected: it is a behaviour change, never a refactor.
PR 4 is the countdown's backend half, usable by #59 before send-back exists.

**Rejected.** *One PR*: a reviewer could not follow it. *A refactor PR for
the window's arithmetic*: under D2 `is_settleable` stops computing the end,
so the extraction would be undone in the next PR.

**Notes.** Merge the bottom of the stack with a merge commit, or move the
rest across with `git rebase --onto` (CLAUDE.md, "A squash merge kills the
branch"). The ADR 0019 amendments and the amendments below to #12's entries
wait for Ihsan's agreement, since they change his #227 code.

**Reversal trigger.** Review asks for a different split.

---

### D-NEW — PR 2 extracts `_refuse_if_superseded` and `_clear_proposal`, as its own refactor

**Date:** 2026-10-11 · **Ticket:** #11 · **Status:** active

**Decision.** PR 2 extracts two private helpers in
`service/market_service.py`, with no behaviour change and no test changes:

- `_refuse_if_superseded(market, proposal_id)`: the `proposal_id` comparison
  from `_proposal_to_decide`, with the null rule in its docstring (a null
  matches only a proposal that has no id).
- `_clear_proposal(market)`: reject's seven-column clear.

**Why.** Send-back is the second caller of each, and CLAUDE.md makes the
extraction part of the PR that adds the second copy. It goes in a refactor PR
rather than in PR 5 because a refactor never shares a PR with a behaviour
change.

**Rejected.**
- *Extracting in PR 5*: a refactor inside a behaviour PR.
- *Copying the two pieces into send-back*: two copies of the rule that keeps
  a stale page from deciding about a replacement, which would drift.

---

### D-NEW — Price history is replayed from the trades on the pool's feed, and nothing stores it

**Date:** 2026-10-11 · **Ticket:** #63 · **Status:** active

**Decision.** `GET /ledger/markets/{id}/price-history` stores nothing. On each
read it takes the market's trade entries from its pool account's feed, oldest
first, and rebuilds `q` one trade at a time from each trade's outcome, side and
quantity. Each rebuilt `q` is priced with the book's `b`. The first point is
the book's opening: `state_version` 0, every `q` at zero, so uniform prices, at
`opened_at`. There is no new table and no migration.

**Why.**
- *The order is already there.* Every trade posts a leg on its market's pool
  account (`service/trading.py:453-456`). That account's entries are stamped
  in commit order with strictly increasing times (`service/posting.py:143-167`,
  #187; "A movement's timestamp is fixed under its account locks"). So the
  pool's feed lists the market's trades in `state_version` order, and
  `ix_ledger_entries_account_feed` (`model/entities.py:348-353`) already
  serves that read.
- *The replay is exact.* `TradeIn.quantity` refuses a fifth decimal
  (`model/schemas.py:465-475`). `q` is written as `q + delta` at precision 50
  (`core/pricing.py:169-173`) into `Numeric(18, 4)`. `b` is copied once and
  never updated (`entities.py:382-385`). Settlement never writes `q`
  ("`ledger.market_settlements` is the latch, and positions and `q` are never
  written", the part that stands). So summing the signed quantities gives back
  every `q` the book has held.
- *It is cheap enough.* A price vector was measured at 12 µs, so 10,000
  trades take about 0.12 s.
- *A stored table costs more than it saves.*
  - Its insert would run under the book lock and make every trade hold that
    lock longer.
  - `books.ensure_open` would become a second writer, because it would have to
    write the opening point.
  - It needs an Alembic revision that collides with #270's planned `0003`, and
    a CHECK that autogenerate cannot see (ADR 0020).
  - It is a stored copy of something derivable, which ADR 0009 declines for
    balances.
  - Trades made before the revision would need a backfill, and that backfill
    is the replay anyway.

**Rejected.**
- *A `ledger.price_points` table written by the trade.* See above.
- *Scanning `transactions` by `context->>'market_id'`.* That needs an
  expression index and a migration. The pool's feed needs neither.

**Notes.** The replay is right only while the stored `q` equals the sum of the
signed trade quantities. A `q` repaired by hand would make the history
disagree with the snapshot, and nothing on this route detects that. The prices
come from `book_prices.priced`, the quantizer the snapshot and the price frame
use. So a point and a frame for the same `state_version` give the same strings.

**Reversal trigger.** A request measured as too slow. Then a stored table,
backfilled by this replay function, which is pure and in `core/` (the replay
PR) so the backfill can call it unchanged.

---

### D-NEW — Settlement adds no point to the price history; the series ends at the last trade

**Date:** 2026-10-11 · **Ticket:** #63 · **Status:** active

**Decision.** A settled market's price history ends at its last trade. No
point at 1 and 0 is added for the result. The result is reported beside the
points instead, as `result`, `winning_outcome_id` and `result_recorded_at`,
read from `ledger.market_results`.

**Why.**
- *It would disagree with the snapshot.* The snapshot of a settled market
  still shows the last traded price ("Settlement … the preview and the
  snapshot keep quoting the frozen book").
- *No frame announced it.* "A settlement publishes no realtime frame." A chart
  that appends live frames would never receive such a point, so a page loaded
  before settlement and one loaded after it would draw different lines.
- *The result is a fact, not a price.* The three result fields carry it
  exactly, without making it look like a trade.

**Rejected.**
- *A final point at `1.0000` and `0.0000`, at `recorded_at`.* See above.

**Notes.** `result` takes the exposure read's values: the record's `kind` as a
string, today always `"settled"`. When [BE] #221 adds a void it arrives here
with a null `winning_outcome_id`. As on every other reader of
`market_results` ("A settled row's payout is its quantity…", Notes), #221 has
to check this read.

**Reversal trigger.** A frame type for settlement. That would be a realtime
contract change, which "A settlement publishes no realtime frame" leaves to its
consumer. Once a client can receive the result live, a point for it no longer
disagrees with what a live chart shows.

*Amended 2026-10-20. Decided by ADR 0025, and not built yet.* A void adds no
point either: it moves no price. It arrives here as `result: "voided"`, a null
`winning_outcome_id` and the void's `recorded_at`, with no code change.

---

### D-NEW — Price history returns raw points, one per trade, filtered by `from` and `to` with a carry-in point; nothing is bucketed

**Date:** 2026-10-11 · **Ticket:** #63 · **Status:** active

**Decision.** One point per trade, plus the opening point. `from` and `to` are
optional ISO-8601 times with an offset, and both include a point at exactly
that time. When `from` is given, the last point before it is returned as the
first point, at its own `occurred_at` (it is not moved to `from`), so the line
starts at the right level. Points after `to` are left out. A `from` later than
`to`, or a time with no offset, is `422 invalid_request`.

**Why.**
- *#8 exists to spot manipulation.* A bucket that keeps the last price in each
  interval hides a pump-and-revert that happens inside one interval, which is
  exactly what an administrator is looking for.
- *A server-chosen interval silently decides what the admin sees.*
- *A carry-in point keeps the line honest.* Without it, a window that starts
  between two trades begins at its first trade, and the chart draws no level
  before it. The point keeps its own time, so a point's `occurred_at` is always
  when that trade happened.
- *`from` after `to` has no sensible answer.* The carry-in point could fall
  after `to`. Refusing it is plainer than defining that case.

**Rejected.**
- *Bucketing by a fixed or server-chosen interval.* See above.
- *Paging the points.* A chart wants the whole window. `from` and `to` already
  bound the payload, and the series is append-only, so a client can fetch from
  its last point onwards.

**Notes.** A point is a `price` socket frame without `type` and `market_id`.
The chart appends live frames and dedupes on `state_version`. A point's
`occurred_at` is when the trade's entries landed, which can be later than
the frame's `occurred_at` (the book's `state_changed_at`) for the same
`state_version`. Across replicas the gap is clock skew. The snapshot section
already says this about the entries (#187).

**Reversal trigger.** The payload size, measured, is a problem. Then a bucket
the client chooses from a fixed list, with high and low in each bucket so a
spike inside one stays visible.

---

### D-NEW — The market's trade feed reads the pool account's feed, filtered to the trade kinds, and its rows are built by `trade_fields_of`

**Date:** 2026-10-11 · **Ticket:** #63 · **Status:** active

**Decision.** `GET /ledger/markets/{id}/trades` reads the market's pool account
feed newest first, keeps the `trade_buy` and `trade_sell` entries, and pages
it by keyset on `(created_at, id)` with the `/entries/me` cursor. Each row is
built by `_trade_fields_of`, which the refactor PR makes public as
`trade_fields_of`, ahead of the feed. That PR changes no test assertion.
`user_id`, `total` and `state_version` come from `trading.result_of`, the
reader of a trade's `context` that `trade_fields_of` already calls.

**Why.**
- *The index is already there.* The pool's feed is
  `ix_ledger_entries_account_feed`, the same index the history pages through.
  Finding a market's trades through `transactions.context` needs an
  expression index and a migration (the replay entry above).
- *One builder, one price.* The market's feed and the trader's own history
  give the same `average_price` for the same trade, because both call one
  function: `per_share(|amount|, quantity)`. The pool's leg is the trader's
  leg with the sign flipped, so its absolute amount is the same.
- *A second caller makes the name public.* "`_trade_fields_of` keeps its name,
  and its docstring says it covers settlement" left the rename to a refactor
  PR. The second caller is the reason it happens now. The PR goes under #63,
  as 4a, 5a and 6a went under #12, rather than under an issue of its own as
  that entry's Notes said: the rename has no reason to exist apart from this
  caller.

**Rejected.**
- *A second row builder for the market's feed.* Two builders would be two
  answers to what a trade averaged.
- *Renaming it inside the feed's PR.* A refactor never shares a PR with a
  behaviour change.

**Notes.** The pool's feed also holds `market_seed`, the settlement payouts
and `settlement_residue`. The kind filter leaves them out. There is no
`balance_after`: a running balance on a pool account means nothing to someone
reading trades. `total` is signed for the trader, like `TradeOut.total`: it is
the negation of the pool's leg, which equals the trader's own leg.
This supersedes "`_trade_fields_of` keeps its name, and its docstring says it
covers settlement": the name is now public, and the rename goes under #63.

**Reversal trigger.** A trade that touches no pool account, or more than one,
such as a multi-market trade. Then the pool's feed no longer lists the market's
trades one each.

---

### D-NEW — A market's trades carry `user_id`, not `username`, and the admin's browser names them through a batch lookup on auth

**Date:** 2026-10-11 · **Ticket:** #63 · **Status:** active

**Decision.** Each row in the trade feed carries the trader's `user_id`. The
ledger stores no username and asks auth for none. The administrator's browser
resolves names through a new admin-only batch lookup on auth,
`POST /admin/users/lookup` with `{"user_ids": [...]}` (1 to 200 UUIDs),
returning `{"users": [{"id", "username"}]}`. It mirrors the lookup in ADR 0021,
`POST /internal/users/lookup`, but it takes an
administrator's token and never returns `is_suspended`. That route is #273.
Until the User column reads it, that column shows a short id that links to
#259's user page.

**Why.**
- *ADR 0021's lookup is the wrong credential for this.* It is scoped to one
  key, one caller and one route, for a background job with nobody behind it. This request has an administrator's token behind it.
  Sending the service key per request would widen that ADR beyond what it
  argues.
- *The leaderboard's snapshot names the wrong set.* It leaves suspended users
  off the board, so the traders an administrator most likely investigates
  would have no names. It would also tie #63 to the unbuilt #38.
- *ADR 0021's objection to a browser-side join does not apply here.* It
  rejects "the frontend joins usernames" for a public route that anyone could
  enumerate. This one is admin-only, and administrators can already list every
  user through `GET /admin/users` with a blank `q`.
- *No name at all fails #8.* Its trade table asks for "user". "You" means
  nothing on an admin page.
- *Privacy.* For an administrator this is data that
  `/ledger/users/{id}/entries` already gives, grouped by market. For a trader
  it would be new exposure, which is why the trade feed is admin-only (the
  next entry).
- *Its own path, and a body.* One path answers one shape, so the paged search
  on `GET /admin/users` keeps its contract. 200 UUIDs in a query string are
  about 7.4 KB, close to common URL limits.

**Rejected.**
- *ADR 0021's `POST /internal/users/lookup`, called per request with
  `LEDGER_SERVICE_KEY`.* See above.
- *Reading names from the leaderboard's generation rows.* See above.
- *A `username` column on `ledger.accounts`, copied at grant time.* ADR 0021
  rejects this as its own fallback. Accounts granted before the column existed
  would have no name.
- *A repeated `id` parameter on `GET /admin/users`.* Two response shapes on
  one path, and a URL near its length limit at 200 ids.
- *No names.* See above.

**Notes.** The ledger still knows a user only by the `sub` of a token (ADR
0003), and auth still does not know that credits exist: the lookup carries ids
and returns names. Suspended users are included, an unknown id is left out,
and an id sent twice is answered once.

**Reversal trigger.** ADR 0021, or a later record, gives the ledger a general
way to name users that includes suspended ones. Then the feed can carry
`username` itself, and the browser-side join goes.

Amended 2026-10-17 by #273: built by ihsankoolz, with Ernest's agreement, as
POST /admin/users/lookup. The contract above is unchanged.

---

### D-NEW — Price history takes any valid token; the trade feed takes an administrator's

**Date:** 2026-10-11 · **Ticket:** #63 · **Status:** active

**Decision.** `GET .../price-history` needs any valid access token, like the
snapshot. `GET .../trades` needs `role: admin` (`require_admin`), and a trader
gets `403 not_an_administrator`.

**Why.**
- *Every point has already been broadcast.* The realtime service sends each
  price frame to every subscriber of the market. An admin gate on the history
  would protect nothing, and it would stop the market page from reusing the
  route.
- *The trade feed says who traded.* That is the exposure the previous entry's
  Why describes. Administrators can already read it through
  `/ledger/users/{id}/entries`, and traders cannot.

**Rejected.**
- *Admin-only price history.* It protects nothing, as above.
- *A trader's feed with `user_id` removed.* It answers no story on the board,
  and a size and a time are still enough to recognise a trader.

**Notes.** Neither route writes or calls market_service, so neither can mint a
grant or open a book. A market with no book reads empty with `200`, the same
as the exposure read ("Exposure on a market with no book is a 200 with nothing
in it, and writes nothing").

**Reversal trigger.** A story asks for a public trade tape. It decides how to
show traders, and goes through this entry and the one above.

---

### D-NEW — The price-history replay refuses trades whose state versions do not run 1, 2, 3, … in order

**Date:** 2026-10-11 · **Ticket:** #63 · **Status:** active

**Decision.** The replay refuses trades whose `state_version`s do not run 1, 2,
3, … in order. It raises `ValueError`, naming the value it expected and the
one it got.

**Why.**
- *A point's label is a claim.* A point labelled N must carry `q` after N
  trades. A dropped or reordered trade makes every later label wrong.
- *The chart trusts the label.* It drops any live frame whose `state_version`
  it already holds, so one wrong point would make it discard correct frames in
  favour of wrong history.
- *The run is gapless on a healthy book.* Only trades bump `state_version`,
  and each does it in its own transaction, so a gap or a reorder means the
  feed read is wrong and not that the market is.

**Rejected.**
- *No guard.* The replay would return a series that looks fine and is wrong
  from the first missing trade on.
- *Strictly increasing, gaps allowed.* It would still carry a wrong `q` under
  every label after a gap, and it would accept the dropped trade silently.

**Notes.** The route PR decides how a refusal reaches the caller.

**Reversal trigger.** Any event other than a trade starts bumping
`state_version`, for example a void ([BE] #221). Then the run has legitimate
gaps, and the replay has to be told which events move `q` and which do not.

*Amended 2026-10-20. Decided by ADR 0025, and not built yet.* The reversal
trigger has not fired. A void writes no trade and does not raise
`state_version`. A limit-order fill raises it, but a fill is a `trade_buy` or
`trade_sell` on the pool's feed, so the run still has no legitimate gap. The
void's refund replay needs the same guarantee, so this check moves into one
pure function in `core/` that both replays call; see "The void's refund replay
reads `pool_trade_query`, and shares the price history's 1..N check" below.
The price history keeps its own error code.

---

### D-NEW — A price history whose trades skip or reorder a state version answers 500 `price_history_inconsistent`

**Date:** 2026-10-12 · **Ticket:** #63 · **Status:** active

**Decision.** The service catches the replay's `ValueError`, around that call
only, and raises `PriceHistoryInconsistent` (500, `price_history_inconsistent`).
The existing handler logs it at ERROR, as it does every 5xx `LedgerError` other
than a 503.

**Why.**
- *The two faults have different causes.* `market_book_incomplete` means a book
  that cannot be priced: no outcome rows, one, or a `b` the engine cannot use.
  A gap or a reorder on the pool's feed is a different fault, in the trades
  and not in the book. With two codes, the log says which one to go and look
  at.
- *Nothing the caller does changes it.* The same feed gives the same gap on
  the next read, so a client has no reason to retry.

**Rejected.**
- *Reusing `market_book_incomplete`.* It would put two causes behind one code,
  and the operator reading the log would have to open the traceback to tell
  them apart.

**Notes.** This answers "The route PR decides how a refusal reaches the
caller", the note on "The price-history replay refuses trades whose state
versions do not run 1, 2, 3, … in order". The catch wraps only the call to the
replay, so a `ValueError` raised anywhere else on the route is still a bare
500 and not this error. Not worth retrying.

**Reversal trigger.** A legitimate cause of a gap that is not corruption. Then
a gap is an event to handle and not a fault to report.

---

### D-NEW — The `from`/`to` window and the carry-in point are applied in `core`, after the replay of every trade

**Date:** 2026-10-12 · **Ticket:** #63 · **Status:** active

**Decision.** The replay always starts at `state_version` 1 and runs over every
trade the market has had. Then a pure function in `core/`, next to the replay,
cuts the points by `from` and `to` and picks the carry-in point. The query
that reads the feed has no time condition.

**Why.**
- *A price at `from` depends on every earlier trade.* `q` at that moment is
  the sum of all trades before it. Starting the replay at `from` would start
  `q` at zero and price the whole window wrong.
- *The `state_version` run needs the start.* "The price-history replay refuses
  trades whose state versions do not run 1, 2, 3, … in order" checks the run
  from 1. A feed that starts at the window would fail the check on every
  window that does not begin at the market's opening.
- *Pure logic is tested without a database.* The cut and the carry-in rule are
  the boundary cases of "Price history returns raw points, one per trade,
  filtered by `from` and `to` with a carry-in point; nothing is bucketed". That
  is the logic a unit test should be able to reach without a session.

**Rejected.**
- *Filtering the trades by time in SQL.* It drops the earlier trades the
  replay needs, and moves the window's edge cases into a query that only an
  integration test can reach.

**Notes.** The window bounds the payload and not the work: a request for the
last hour still reads and replays the whole feed. "Price history is replayed
from the trades on the pool's feed, and nothing stores it" measured that cost
at about 0.12 s for 10,000 trades.

**Reversal trigger.** The same as that entry: a request measured as too slow.
Then a stored table, which would also take over the window.

---

### D-NEW — The caller's own row is read in the page's statement, from the generation served

**Date:** 2026-10-13 · **Ticket:** #269 · **Status:** active

**Decision.** `GET /ledger/leaderboard` returns `me`, which is the caller's
`{rank, username, net_worth}` or `null`, and an `is_me` on every row. Both
are read from the generation the page is served from. That is the cursor's
generation when there is a cursor, and the newest otherwise, so `me` is the
same on every page of one walk.

Both are read in the page's one statement. `_page_query` gains a second outer
join to `ledger.leaderboard_entries` on `generation_id` = the served
generation and `user_id` = the token's `sub`. The unique
`(generation_id, user_id)` index limits that join to one row, so it adds
columns to the page and never rows. `is_me` is computed in SQL as
`user_id = <the caller>`. `me.username` is the stored username, so it matches
the caller's row character for character. No user id appears in any response.

`me` is `null` when there is no generation, when the caller has no ledger
account, when the caller was granted after the generation was computed, or
when the caller was suspended at the time it was computed. A later suspension
still shows `me` until the next refresh. The response does not say which case
applies.

**Why.** `core/database.py` sets no isolation level, so the session runs at
Postgres's default READ COMMITTED, and each statement takes its own snapshot.
Between two statements a refresh can commit, and its prune can delete the
cursor's generation. The second statement would then find no entry for the
caller and answer `me: null` beside rows the caller is in. One statement sees
one snapshot, so the page and `me` cannot disagree. That is the argument of
"Preview reads `q` and `state_version` in one SQL statement", applied to a
generation instead of a book.

**Rejected.**
- *A second query for `me`.* It has the race above.
- *`REPEATABLE READ` on this route.* It would make the two statements
  consistent. But it would be the only ledger read that sets an isolation
  level, and it would buy what one statement already gives.
- *`is_me` derived from `rank == me.rank`.* One bug in `me` would then break
  two fields. Computed in SQL from `user_id`, each field is checked on its
  own.
- *`me` from the newest generation.* On a page reached by a cursor into the
  previous generation, its rank and net worth would not match the rows
  served.

**Notes.** No test that is free of timing can tell one statement from two:
the race needs a refresh to commit between them. The PR says so under Known
gaps. This entry settles the Notes of "The leaderboard read writes nothing,
and mints no grant": a caller with no account gets `me: null` and still has
no account afterwards.

**Reversal trigger.** The read moves into a transaction that pins one
snapshot for other reasons, or pruning stops happening in the refresh's
commit. Either way, `me` could then be a second query.

---

### D-NEW — `post` is `post_all` of one movement

**Date:** 2026-10-13 · **Ticket:** #315 · **Status:** active

**Decision.** `posting.post` keeps its signature and docstring and posts a
batch of one: `(transaction,) = await post_all(session, [Movement(...)],
now=now)`. Its own copy of the write sequence is gone, so `post_all` is the one
place the steps run: round, refuse unbalanced legs, lock, look the key up, the
pending-writes check, the overdraft check, the stamp, the SAVEPOINT insert,
the race handler and the commit. `find_by_idempotency_key` stays public for
`grants.ensure_granted` and the trade path's unlocked lookup.

**Why.** One sequence of write steps, so a fix to their order reaches trades
and settlement together. With two copies, a fix could land in `post` and miss
`post_all`, and settlement would keep the bug (#250). With one movement,
every rule only a batch has is inert:
- `_refuse_if_malformed_batch` raises on an empty list or a repeated key, and
  a list of one is neither.
- `_replay_batch`'s whole-batch match cannot fire: with one key, the lookup
  returns a row only when it found that key.
- Netting across movements is `_refuse_overdrafts` over one movement's legs,
  the call `post` made.
- Movement *i* lands at the first stamp plus *i* µs, and *i* is 0.

The two behaviours "`post` is not `post_all` of one movement" protected still
hold: pending writes are refused before a match is classified, on both replay
branches, from the answer recorded on entry, and a replay commits to release
the account locks. #315 checked each against the code, and the ledger suite
passes unchanged across the refactor.

**Rejected.**
- *Keeping both bodies*: the drift risk raised on #250, the reason for this
  entry.
- *One private core with flags for the differences*: each flag would have
  one caller (backend/CLAUDE.md, "Reuse").

**Notes.**
- The lookup under the lock is `IN (key)` through `_list_by_idempotency_keys`,
  not `= key`. `idempotency_key` is unique, so both return the same row or
  nothing.
- `accounts.lock` now gets the set of account ids, so a movement naming one
  account twice locks it once. Locking a row the transaction already holds
  was a no-op.
- One test's setup changed: `test_a_replay_found_by_the_insert_race_is_refused_too`
  hides the first call of `_list_by_idempotency_keys` instead of
  `find_by_idempotency_key`, as `test_post_all.py` already does. No assertion
  changed.
- Every write step is guarded by a test that goes through `post`, checked by
  breaking each in `post_all` in turn. Two steps rest on one test each: the
  lookup under the lock on
  `test_a_retry_racing_its_original_is_answered_with_its_transaction`, and the
  SAVEPOINT on `test_a_replay_found_by_the_insert_race_is_refused_too`.

**Reversal trigger.** The trade path needs a write step that settlement must
not have, or the reverse.

---

## Open — decided by nobody yet

Move these into the log above when they're settled.

- **Calls to market_service for distinct unknown market ids are unbounded.**
  "A market_service 404 is remembered for ten seconds, per process" stops a
  repeated id from reaching market_service more than once per window, and
  nothing else. A trader looping fresh random UUIDs still makes one outbound
  call per request, through the preview, the snapshot or the trade. Only a
  rate limit closes that, per caller or per process, and nobody has decided
  where one belongs.

- **`occurred_at` for a market that has never traded.** The realtime contract
  defines it only as the time of the event. `state_changed_at` set at handoff is a
  proposal, not something the contract says.
- **Nothing refuses a market whose `seed_subsidy` is below `b·ln(n)`.**
  `core/opening_prices.py::max_platform_loss` computes the worst case and
  `MarketOut` reports it beside the subsidy, but `service/validation.py` never
  compares them — `_liquidity_problems` checks both are present and positive and
  says in its own docstring that the comparison is "deliberately NOT checked",
  on the grounds that an administrator may knowingly seed a market for less.
  That was a defensible call while the number was only displayed. Once [F-7] #96
  funds a pool from it, an undersubsidised market is one whose pool goes
  negative under ordinary trading, and `_refuse_overdrafts` exempts every
  non-USER account, so nothing anywhere will say so. Whether that stays an
  informed choice, becomes a submission rule, or becomes a warning the ledger
  records at book creation is undecided. It is market_service's rule to make
  either way, not the ledger's.
- **Service-to-service auth for a caller with no token.** Deferred by ADR 0009
  to #22 and **answered there only for the trade route**, by the amendment on
  that record: the route takes no legs, the debited account comes from the
  token's `sub`, and the idempotency key is derived by the server, so a
  trader's own token authorises debiting only themselves. What stays open is
  the caller with nobody behind it — [T-7] #27's auto-execution, and [3.4] #12's
  settlement *if* it ever becomes a background job rather than an
  administrator's request. Neither is on the board now, and either makes a real
  service credential a prerequisite rather than a detail. ADR 0017's reversal
  note names the same day from the status gate's side.

  ADR 0021 (accepted, [L-1] #38) introduces the first service credential, and
  it does not close this entry. That credential is one key, held by the
  ledger, which opens one auth route: `POST /internal/users/lookup`. It gives
  no service an identity on any other route, so #27's caller still needs a
  decision of its own.

  *Amended 2026-10-17. Decided by ADR 0024 (Accepted 2026-10-17), not built
  yet.* ADR 0024 runs [T-7] #27's cascade inside the request that moved the
  price, so #27 has a caller and needs no credential, and #27 leaves this entry.
  What stays open is a settlement that becomes a background job, and ADR 0024's
  own follow-up, a sweep that cancels a suspended owner's orders.
- **A NULL `close_time` on an OPEN market means two different things.** SQL and
  Python disagree, and neither is documented as the intended answer.
  `core/closing.py` returns False for a null, so the Python derivation labels
  such a market `closed`. The SQL form compares `Market.close_time > now`, which
  is NULL rather than false, so three-valued logic drops the row from
  `status=open` **and** from `status=closed` — invisible to every filter,
  present in the default view.

  Worse than it first looks, and this is the part Ernest's review did not have:
  the default view orders by `open_for_trading().desc()`, that expression is
  also NULL for such a market, and Postgres sorts NULLs **first** under `DESC`.
  So a NULL-`close_time` OPEN market sorts to the very **top** of the trader's
  default view while being labelled `closed`.

  Unreachable through the API today — `problems_blocking_submission` requires a
  close time and `publish` re-runs every submission rule — so this is about what
  should happen if it ever becomes reachable, not a live bug. Both call sites
  document the state as unreachable and they should at least fail the same way.
- **How long the terms pull may block.** Only the ceiling is open now. No
  call to market_service holds a database connection or a row lock any more:
  `books.ensure_open` rolls back the read that found no book before it calls
  out ("The cold path holds no connection across the terms pull"), and
  `market_status.ensure_trading` does the same before it calls the same
  client on every non-replay trade (#115). #22 takes the book row lock only
  after both calls have returned, so
  it holds no row locks across either — a slow market_service costs a trade
  its own latency, not a connection out of a pool of ten, and it serialises
  nothing behind it. The connection-reuse half has landed too: the client
  both calls go through is held for the process (#114). What is still open
  is the ceiling itself. `service/market_terms.py::_TIMEOUT` is five seconds
  on every phase, which is exactly `httpx.DEFAULT_TIMEOUT_CONFIG` — so the
  budget is currently inherited in substance even though it is written out
  in the source, and no test can tell the line's deletion from its presence
  (D-030, corrected). Now that nothing is held across the call, the case for
  a *shorter* timeout is the trader waiting, not the pool: every trade now
  waits on this call, and a first touch is the one request that does real
  work upstream, so too short a ceiling turns a slow-but-healthy market
  service into spurious 503s. Deciding it needs a measurement of what the
  gate's call and a first touch actually cost, which nobody has taken.
- **`market_outcomes.q` has no `CHECK (q >= 0)`.** `positions` has
  `quantity >= 0` and `market_books` has `state_version >= 0`, but the column
  the pricing engine reads has nothing. Today nothing can write a negative
  `q`: the holding check refuses first, and `InsufficientSharesOutstanding`
  backstops it ("Shares outstanding equal the sum of positions, so the
  outstanding check is a backstop on the trade route"). But that backstop is
  the only guard, and `C(q)` over a negative `q` returns a number rather than
  failing. Adding the CHECK is a constraint on an existing table, so it needs
  an Alembic revision in the ledger's `migrations/versions/`, written by hand,
  since autogenerate cannot see a CHECK (ADR 0020). Whether that is
  worth it, and whether [3.4] #12 should land it since it is the next writer
  of `q`, is undecided.
- **No index serves settlement's winners read.** `ledger.positions`' primary
  key leads with `user_id`, so `WHERE market_id = … AND outcome_id = … AND
  quantity > 0` scans the table while holding the market's book lock. That
  stalls that market's trades, not the others'. An index on
  `(market_id, outcome_id)` would need a revision. It becomes a decision when
  PR 5's 5-second test misses its budget, or when `positions` grows large
  enough that a measurement shows the scan.
- **A third copy of the conftest's audit-reader engine.** The ledger's
  `unit_test/conftest.py` gains an `audit_svc` engine from
  `AUDIT_TEST_DATABASE_URL`, as market_service's and auth_service's already
  do. Whether it moves into `shared/testing.py` is #253.

  *Amended 2026-10-19. Settled by #253.* The three conftests' `audit_reader`
  fixtures open their session through `shared/testing.py`'s
  `open_audit_reader`, and no copy of the engine is left. ADR 0012's
  amendment.

---

### D-NEW — The first refresh runs inside the refresher's task, and the lifespan yields without waiting for it

**Date:** 2026-10-16 · **Ticket:** #40 · **Status:** active

**Decision.** The loop is `run_leaderboard_refresher`, in
`service/leaderboard_refresher.py`, and `main.py`'s lifespan starts it as a
task named `leaderboard-refresh`. The first refresh is the task's first
iteration. The lifespan never awaits a refresh, bare or inside a `try`,
before it yields.

**Why.** [L-3] #40's fourth acceptance criterion: "The refresh process does
not block trade execution." The ledger serves no request, trades included,
until its lifespan yields, so a lifespan that waited for the first refresh
would make trade execution wait for it. A refresh values every holder and then
calls auth, with a five-second timeout on each call ("One auth client in the
ledger"), so an auth outage would hold the boot for as long as the refresh
took to give up. Until the first refresh commits, `GET /ledger/leaderboard`
answers `computed_at: null` and `stale: true` (`_empty_board`,
`service/leaderboard.py:322`; the "`stale: true` means the board has not been
refreshed recently" paragraph of `docs/api/ledger-service.md`), which is the
designed answer for a board that does not exist yet. The loop starts at once,
as `market_service`'s sweeper does.

**Rejected.**
- *Awaiting the first refresh before the yield.* Boot becomes hostage to auth.
  Wrapping the await in a `try` still waits out the timeouts before the
  service answers anything.
- *Sleeping one interval before the first refresh.* A fresh database, or a
  ledger that was down for a while, would show no ranking or an old one for a
  full interval after boot, for no gain.

**Notes.** The criterion is about trade execution, and this entry decides only
the boot. Once the loop is running, a refresh cannot block a trade for the
reason "A refresh writes under a transaction-level advisory lock" gives: it
takes no row lock on any trade table.
`test_the_app_boots_and_serves_with_auth_unreachable`
(`unit_test/controller/test_leaderboard_refresh_lifespan.py:84`) holds a
refresh open and requires `/health` and the stored ranking to answer meanwhile.
The task's name is `_TASK_NAME` in that file (`:35`), and the shutdown test
finds the task by it, so renaming the task fails that test.

---

### D-NEW — Shutdown cancels the refresher and awaits it inside the clients' block, before the auth client closes and the engine is disposed

**Date:** 2026-10-16 · **Ticket:** #40 · **Status:** active

**Decision.** In `main.py`'s lifespan, after the `yield` and still inside the
`async with` that holds the terms and auth clients, the refresher's task is
cancelled and awaited. Only then do the clients close, and after them
`dispose_engine()` runs.

**Why.** The task depends on both: a refresh in flight holds a request on the
auth client and a connection from the engine's pool. Closing the client first
makes that refresh call a closed client, and the loop's `except Exception`
logs an ERROR with a traceback during an ordinary shutdown. Disposing the
engine first loses the pool under it, and shutdown ends in a stack trace, as
`market_service/main.py:53-54` says of its sweep. Awaiting, not just
cancelling, means no task outlives the app.

**Rejected.**
- *Cancelling in the `finally`, after the `async with` has closed the
  clients.* That is the closed-client case above. The sweeper's placement
  works for `market_service` because it holds no client. The ledger has a
  second dependency, so the cancel moves inside it.
- *Cancelling without awaiting.* The cancellation lands at the task's next
  checkpoint, which can be after the engine is gone.

**Notes.** The shutdown test,
`test_shutdown_cancels_the_refresh_before_the_auth_client_closes`
(`unit_test/controller/test_leaderboard_refresh_lifespan.py:217`), pins the
placement against the auth client: its stand-in reads `auth_client.is_closed`
when it is cancelled and requires `False`, and the test requires the task to
be done and cancelled once the lifespan exits. That one assertion pins all
three positions: `dispose_engine()` runs in the outer `finally`, after the
`async with` has closed both clients, so a cancel moved anywhere past the
clients' block reads `is_closed` true and fails the test. No separate test
for the engine or the terms client is needed.

---

### D-NEW — The refresher says at startup whether this replica is running it: WARNING when it is off, INFO with the interval when it is on

**Date:** 2026-10-16 · **Ticket:** #40 · **Status:** active

**Decision.** When `LEADERBOARD_REFRESH_ENABLED` is false, the lifespan starts
no task and logs once, at WARNING, that this replica does not refresh. The
message names the setting and says that with it off on every replica the
ranking stops updating and turns stale after two intervals. With it on, the
loop logs one INFO naming the interval, as `run_close_sweeper` does
(`market_service/service/sweeper.py:76-80`), so a replica's log answers
"is this one refreshing, and how often" either way.

**Why.** The switch's cost does not show on the replica that has it. Off
everywhere, the board freezes, and the only sign is `stale: true` on a read,
two intervals later. A line at boot is where an operator reading one replica's
log would see it. `market_service` does the same for `CLOSE_SWEEP_ENABLED`
(`market_service/main.py:44-48`).

**Rejected.**
- *No log.* A frozen board with nothing in any log to explain it.
- *INFO for the disabled case.* It is the line to find when the board is
  stale, and a log filter at WARNING would hide it. The enabled case is INFO
  because a loop that is running is not news.
- *Refusing to boot when it is off.* The setting exists so that a second
  replica can turn it off ("A leaderboard cursor names its generation"), and a
  suite or a developer may want the service without the loop.

**Notes.** The warning cannot tell whether another replica has it on, and does
not try.

---

### D-NEW — The ledger's suite forces the refresher off by assignment, and a test that needs it turns it on itself

**Date:** 2026-10-16 · **Ticket:** #40 · **Status:** active

**Decision.** `unit_test/conftest.py:55` sets `LEADERBOARD_REFRESH_ENABLED` to
`false` in `os.environ` by plain assignment, before any project module is
imported, and not with `setdefault`. A test that needs the loop sets the field
on the cached settings object with
`monkeypatch.setattr(get_settings(), "leaderboard_refresh_enabled", True)`,
and `leaderboard_refresh_seconds` the same way. The lifespan tests do it
through `_refresh_enabled`
(`unit_test/controller/test_leaderboard_refresh_lifespan.py:60`). They do not
touch the environment.

**Why.** Every test that builds the app enters its lifespan. With the loop on,
each would run a real refresh against the test database while the next test's
fixture drops and recreates the schema, and the failure would show up in a
test that has nothing to do with the leaderboard. `load_repo_env()`
(`conftest.py:30`) copies the repo `.env` into `os.environ` first, and keeps
anything already exported (`shared/testing.py:76`, `setdefault`). A
`setdefault` in the conftest would therefore let a `.env` or a CI export of
`true` win. The assignment does not.

**Rejected.**
- *`setdefault`.* See above.
- *A default of false in `Settings`.* The production default stays true: a
  ledger that boots without refreshing is the silent stale board "The refresh
  runs every 60 seconds" exists to avoid.
- *A per-test fixture.* One forgotten test starts a loop on a schema that is
  about to be dropped.

---

### D-NEW — Compose and `.env.example` pass both refresh settings to the ledger, with their defaults filled in

**Date:** 2026-10-16 · **Ticket:** #40 · **Status:** active

**Decision.** `docker-compose.yml` passes `LEADERBOARD_REFRESH_SECONDS` and
`LEADERBOARD_REFRESH_ENABLED` to the `ledger` service with `:-` defaults of
`60` and `true`, the form `CLOSE_SWEEP_SECONDS`, `CLOSE_SWEEP_BATCH` and
`CLOSE_SWEEP_ENABLED` use for `market_service`. `.env.example` lists both
with those defaults as their values, under a comment that says what each does.

**Why.** Compose hands a container only the variables its entry lists, and
`ledger`'s `environment:` (`docker-compose.yml:190`) is a list with no
`env_file`, so without the pass-through a value in `.env` reaches nothing and
the setting appears to do nothing. They are optional with sensible defaults,
so they take `:-` and not the `:?` that compose uses for secrets. In
`.env.example` a blank line is worse than a missing one:
`.github/workflows/e2e.yml:55` replaces every line matching `^(\w+)=$` with a
random `secrets.token_urlsafe(48)`, and the ledger's
`leaderboard_refresh_seconds` is an `int` with `gt=0`
(`core/config.py:59`), so it would fail to parse that and the stack would not
boot in CI.

**Rejected.**
- *Leaving them to the ledger's own defaults.* Correct until somebody sets one
  in `.env` and sees no effect.
- *`:?`, required.* It would force every deployer to choose a value for a
  setting with a good default, and break an existing `.env`.
- *Blank in `.env.example`.* See above.

**Notes.** This entry is written first and the two files change later in this
PR. Compose runs one ledger today. A second replica sets
`LEADERBOARD_REFRESH_ENABLED=false` on the others ("A leaderboard cursor names
its generation").

---

### D-NEW — An order's idempotency key and each fill's key are derived by the server

**Date:** 2026-10-17 · **Ticket:** #26 · **Status:** active

**Decision.** A placement stores its key as
`order:<user_id>:<market_id>:<client key>`, on `ledger.orders`. Each fill's
transaction is keyed `order-fill:<order_id>:<state_version>`, where
`state_version` is the book's counter after that fill.

**Why.** The order key has the trade key's reasons, in "The trade's
idempotency key is derived by the server; the client's value is one component
of it": no client can claim a namespace the server writes, and one key names
one market, so the book lock is the right lock for the under-lock re-check.
The `order:` namespace is new. It lives on `orders` rather than
`transactions` because a placement that does not fill writes no transaction.

The fill key is unique because one order is in one market and a
`state_version` is used once per market. It is built from values only the
server holds, so no request can claim it.

**Rejected.** *Keying a fill on the triggering trade's key*: one trade can
fill several orders, and a placement's own fill has no trade key. *Storing a
placement's key on `transactions`*: there is no transaction to put it on.

**Notes.** A replay of a placement compares outcome, side, limit price and
quantity, numerically for the two decimals, and a mismatch is
`409 idempotency_key_reused`, as `_matches` does for a trade.

A replay answers from a `book_state_version` column on `ledger.orders`,
created with the table in ledger revision `0004`: the book's counter after
the placement request, which is the last fill's `state_version`, or the
counter the order was placed against when it rested entirely. It returns the
order as it stands now, that stored `book_state_version`, and as `fills` only
that order's fills with a `state_version` at or below it, which are the
fills the placement request made. A placement that rests writes no
transaction, so nothing else records that counter, and fills found by
`order_id` alone would include every later cascade's.

*Amended 2026-10-19.* Since [T-7] #27 a placement that fills on arrival runs
the cascade before it commits, so the stored `book_state_version`, and the
response field it answers, are the book's counter after the whole request,
cascade included: the request's last fill, which can be another trader's
order's, so the counter can be higher than the highest `state_version` in
`fills`. "The last fill's `state_version`" above means that. The replay rule
holds as written: the cascade never fills the order being placed, which is
added after it runs. ADR 0024 carries the same amendment.

---

### D-NEW — An order is executable when its floored fill passes the per-fill limit check; a resting order is one that is not

**Date:** 2026-10-17 · **Ticket:** #26, #27 · **Status:** active

**Decision.** An order is executable if `k`, the fill that moves the price
to its limit floored to `QUANTUM` (`core/pricing.py:54`) and capped at its
remainder, is at least `0.0001`, and the fill's priced cost is at most
`limit × k` for a buy, or its proceeds at least `limit × k` for a sell. A resting order is one that is not executable. This
is what #26's "resting buys sit below current price, resting sells above"
means here.

**Why.** Shares are quantised to `0.0001` and costs round toward the pool. So
after a partial fill an order rests with the price *at* its limit, within a
tick, and "strictly below" cannot be met. The per-fill check stops the last
`0.0001` of an order being charged a whole tick, `quantize_cost`'s ceiling,
which is a fill at 1.00 on a 0.60 order.

**Rejected.** *Comparing the marginal price with the limit*: true of no order
after its first partial fill. *Filling to the limit and ignoring the per-fill
check*: the overcharge above, and a buy could then spend past its budget.

**Notes.** A remainder too small to pass the check rests until it is
cancelled or its market ends. A placement never returns `cost_below_tick` or
`proceeds_below_tick` for this reason: a fill that would round to zero is not
executable, and the order rests instead.

---

### D-NEW — A buy reserves `limit × quantity` rounded up to the tick, its reservation is `budget - spent`, and it cannot go negative

**Date:** 2026-10-17 · **Ticket:** #26 · **Status:** active

**Decision.** A buy order's `budget` is
`quantize_cost(limit_price × quantity, side=Side.BUY)`
(`core/pricing.py:99`), rounded up to `QUANTUM`, fixed at placement. Its
reservation is `budget - spent`, where `spent` is the sum of its fills'
costs. A sell order has no budget. It reserves
`quantity - filled_quantity` shares of its outcome.

**Why.** Every fill passes the per-fill check, `cost ≤ limit × k`. Summed,
`spent ≤ limit × filled_quantity ≤ limit × quantity ≤ budget`. So the
reservation never goes negative and no order needs an "exhausted" state. The
ceiling makes the budget cover the limit times the quantity exactly, never a
tick short.

**Rejected.** *A stored reservation column on `accounts`*: a second source of
truth for money, which ADR 0009 refuses. *Reserving at the current price*:
the order would be under-funded the moment the price moved toward its limit.

**Notes.** `spent` and `filled_quantity` are rollups updated under the book
lock, like `Position.cost_basis`, and can be rebuilt from the fill
transactions, which carry `order_id`. How `posting` checks a balance against
the reservation, in movement order, is in the amendment to "`post_all` nets
each USER account across the whole batch, through `_refuse_overdrafts`".

*Amended 2026-10-18.* The `spent` this entry names is the column
`ledger.orders.filled_value`, one rollup for both sides, signed positive: see
"An order's `filled_value` is what its fills moved, signed positive on either
side". Revision 0004 also holds the half of the budget rule that protects
money, `budget >= limit_price * quantity`, as
`ck_orders_budget_covers_the_limit`. A budget rounded up to the tick always
passes it, so it refuses only a placement that computed its budget wrong,
before the order can rest looking funded.

---

### D-NEW — Priority is price, then an identity `seq` assigned under the book lock

**Date:** 2026-10-17 · **Ticket:** #27 · **Status:** active

**Decision.** The cascade takes the most aggressive executable order first,
ascending `d` or descending `u` in ADR 0024's terms, and breaks ties by
`ledger.orders.seq`, a `GENERATED ALWAYS AS IDENTITY` column. The row is
inserted under the book lock, so `seq` order is lock order.

**Why.** #27's first criterion is "price priority, then time". A clock would
not give time priority across replicas: two processes' clocks disagree by
more than the gap between two placements, which is the problem #187 fixed for
the feed's stamps.

**Rejected.** *`created_at`*: the clock problem above. *Least aggressive
first*: it fills more orders, and it is a different rule from the ticket's.

**Notes.** An aggressive order can take the whole move and leave every order
behind it unfilled. That is on purpose (ADR 0024).

---

### D-NEW — One price frame per commit, at the final `state_version`

**Date:** 2026-10-17 · **Ticket:** #27 · **Status:** active

**Decision.** A trade whose cascade fills orders publishes one `PriceEvent`,
after the commit, at the book's counter after the last fill. The intermediate
versions get no frame. Each order owner whose order filled gets one order
frame on `order.update`, after the same commit.

**Why.** Clients keep the highest `state_version` they hold and discard the
rest, so a frame for an intermediate version would be discarded by any client
that also received the last one. The last one is the only price that was ever
visible outside the transaction.

**Rejected.** *One price frame per fill*: several frames for a state nobody
outside the transaction could observe, published in a burst where the
intermediate ones arrive only to be dropped.

**Notes.** Both publishes follow ADR 0010's rules: after the commit, and never
failing the trade.

---

### D-NEW — A fill's `context` is a trade's, plus `order_id`

**Date:** 2026-10-17 · **Ticket:** #27 · **Status:** active

**Decision.** A fill's transaction is a `trade_buy` or `trade_sell` whose
`context` carries the seven keys `result_of` reads — `user_id` the order's
owner — plus `order_id`.

**Why.** #27 says fills write the same ledger rows as instant trades, and
every reader of a trade, the history, the price history, the trade feed and
#221's refund replay, reads those seven keys. `context` is jsonb so that a new
movement shape needs no ALTER.

**Rejected.** *A `fills` table*: a second source of truth for what the
transaction already records, in the same commit.

**Notes.** Reading the fills of one order is a query on
`context->>'order_id'`, and its index is a stretch item, not in this
iteration.

---

### D-NEW — Cancel calls neither auth nor the market gate

**Date:** 2026-10-17 · **Ticket:** #28 · **Status:** active

**Decision.** `POST /ledger/orders/{order_id}/cancel` authorises from the token
alone, any role, and only the order's owner. It makes no account check and
no market gate call. It takes the order's book lock, re-reads the order under
it, and either cancels or refuses.

**Why.** A cancel only releases. ADR 0022 scopes its check to the route that
moves money toward the caller's risk, and a cancel moves none. A closed market
must stay cancellable, or its reservations are stuck until the result. The
book lock is the same row every fill holds, which is what #28 means by "both
go through the market's serialized path".

**Rejected.** *The account check*: a suspended owner could then never cancel,
even inside their token's lifetime. *The gate*: a closed market's orders could
not be cancelled.

**Notes.** An order that is missing or not the caller's is
`404 order_not_found`, so ids cannot be probed. Nothing is posted: the
reservation is derived, so a cancel cannot double-release.

---

### D-NEW — An open order is live only while its market has no result

**Date:** 2026-10-17 · **Ticket:** #26, #28 · **Status:** active

**Decision.** An order reserves credits or shares, and can fill or be
cancelled, only while its `status` is `open` and its market has no
`ledger.market_results` row. The reads report such an order as `released`.
Nothing writes that status.

**Why.** A settlement or a void writes the result row in its own commit, so
every order on the market is released in that commit without settlement or
void writing anything new. `find_result` never filters on `kind`, so a void
releases orders the same way.

**Rejected.** *Marking the orders in the settlement commit*: settlement would
then write order rows, and #221's void would have to do the same. That is the
coupling deriving avoids.

**Notes.** The `status` column reads `open` for ever on a finished market's
orders, and every read of live orders joins `market_results`: ADR 0011's
"the status column is not the authority" again. Releasing at `close_time` is
a stretch item, and it would still miss ADR 0014's early close, which does not
move `close_time`.

---

### D-NEW — The cascade flushes before its first read and again before `post_all`

**Date:** 2026-10-17 · **Ticket:** #27 · **Status:** active

**Decision.** On the trade path and on a placement that fills, the session is
flushed after the trade's own pending writes and before the cascade reads, and
again after the fills' pending writes and before `post_all`.

**Why.** The ledger's sessions run with `autoflush=False`. Without the first
flush, the cascade's reads miss a position the trade has just added, and a
self-fill would write a second row for the same triple. Without the second,
the reservation `post_all` reads under the account lock does not match the
batch: the orders' `spent` is from before the cascade and a placement's new
order is missing. Adding back the batch's released amounts then overstates
the reservation, refusing a legitimate fill, or the missing order understates
it, letting through a placement that should be refused.

**Rejected.** *Turning `autoflush` on*: it changes every query on every path
to fix one.

**Notes.** `has_pending_writes` still reports flushed rows, so `post_all`'s
replay alarm is unaffected. A test pins the first flush: a trade and a
self-fill on the same `(user, market, outcome)` leave one position row
carrying both changes.

*Amended 2026-10-18.* Built in [T-7] #27 with the first flush repeated before
every fill's position read, not only the cascade's first. Two fills of one
owner in one outcome where the owner held nothing would otherwise each add a
position row, and the second flush would fail on the duplicate. The test
named above cannot be written: a self-fill on the trade's own
`(user, market, outcome)` is a sell order, a sell order needs shares, so the
row already exists and the identity map hands the cascade the trade's own
changes without a flush. The test that pins the flush is two buy orders of
one owner filling in one cascade, leaving one position row with both fills.
The cascade's read of the open orders comes before the first flush and needs
none: the trade writes no order row, and a placement inserts its own after
its cascade has run.

---

### D-NEW — A placement that would cross the book is refused, `order_crosses_book`

**Date:** 2026-10-17 · **Ticket:** #26 · **Status:** active

**Decision.** On a binary market, a new order whose fills push outcome 0's
price up needs its threshold below every open order's that pushes it down,
and the reverse. Otherwise the placement is `409 order_crosses_book`, with
the limit price the order would need in `error.details`.

**Why.** Without the rule two resting orders fire each other in turn, and the
cascade's one-direction termination argument fails (ADR 0024). The traders
would be buying complete sets from each other through the market maker, which
#26 and #27 rule out.

**Rejected.** *Fill the order and cancel its remainder* (immediate or cancel):
deterministic, but the trader gets less than they asked for. It can replace
the refusal later, as the frontend's call. *Allowing it and letting the
cascade run*: about 50,000 fills for 1,000 shares at limits 0.5001 and
0.5000, inside one request under the book lock.

**Notes.** The rule covers the trader's own orders too. A trader with a buy of
outcome 0 at 0.60 cannot place a sell of outcome 0 at 0.55. The bound in
`error.details` is the nearest opposing resting limit, which may be another
trader's. ADR 0024 accepts that, because the refusal is unusable without it.

---

### D-NEW — The preview stays AMM-only, and `post_trade_prices` ignores resting orders

**Date:** 2026-10-17 · **Ticket:** #27 · **Status:** active

**Decision.** `GET .../preview` runs no cascade. `post_trade_prices` is the
price after the trade alone, and the contract says it does not include
resting-order fills.

**Why.** The trader's own cost is unaffected: the trade is priced by the same
`trade_cost_of` call before its cascade runs, so `total` and `average_price`
are what the trade will charge. Only the price the market ends at differs,
and that depends on orders the trader cannot see. Running the cascade in a
read would put the order book's whole state behind every keystroke.

**Rejected.** *A cascade-aware preview*: a second cascade path, in a read,
holding no lock, which could disagree with the trade's.

---

### D-NEW — The trade response carries `book_state_version`, and still no prices

**Date:** 2026-10-17 · **Ticket:** #27 · **Status:** active

**Decision.** `TradeOut` gains `book_state_version`: the book's counter after
the trade and its cascade. `state_version` keeps its meaning, the counter
after this trade alone. The trade writes `book_state_version` into its
`context`, so `result_of` returns it on every replay. `TradeOut` still
carries no prices.

**Why.** A client needs to know which price frame or snapshot reflects its
trade, and with a cascade that is no longer `state_version`. The final
version is a durable count, fixed before the commit, because the cascade is
planned before `post_all` runs. A price is not: "The trade response is
reconstructed from `Transaction.context`, and carries no prices" still
applies, because a replayed price was true once and is a lie afterwards.

**Rejected.** *Repurposing `state_version`*: it is documented as the counter
after this trade. *Returning the final price*: it reverses that entry and
changes both of its guard tests.

**Notes.** A trade recorded before this field existed has no
`book_state_version` in its `context`. `result_of` returns its
`state_version`, which is the same number, because no cascade ran then.

---

### D-NEW — Realised P&L is the balance against the grant, plus the basis still open

**Date:** 2026-10-18 · **Ticket:** #359 · **Status:** active

**Decision.** `GET /ledger/portfolio/me` carries `starting_credits`,
`realized_pnl`, `unrealized_pnl` and `total_pnl`, all derived on every read.
`starting_credits` is the entry the signup grant wrote.
`realized_pnl` is `balance - starting_credits + the cost basis of every open
position`. `unrealized_pnl` is the sum of the rows' own, and `total_pnl` is
the two together, which is `net_worth - starting_credits` exactly.

**Why.** A balance is never stored and neither is a P&L (ADR 0009), so both
figures have to be derived, and the entries do not record what a sell's
shares originally cost. They do not have to: every credit that has left the
balance and not come back is a loss, except the part still tied up in
positions nobody has closed. Adding that basis back gives exactly (sell
proceeds − the basis those sells released) + (settlement payouts − the basis
of the holdings settlement closed), with no cost lot history and no new
column. A settled holding is closed by that definition — its payout is in
`balance` and its basis is not open cost — which is the same reason
ADR 0018's valuation skips it, so the two agree by construction rather than
by vigilance. `total_pnl` ships because money crosses the wire as decimal
strings and the frontend may not do arithmetic on them: without it, "am I up
or down" is still unanswerable.

**Rejected.** *Summing `TRADE_SELL` and `SETTLEMENT` legs against a stored
cost of goods sold*: needs a realised-cost column on `positions` or a lot
history, which is the stored figure ADR 0009 exists to avoid. *Computing
`realized_pnl` as `total_pnl - unrealized_pnl`*: the reconciliation would
then be true by construction and its test would assert nothing.

**Notes.** `starting_credits` comes from the grant transaction
`grants.grant_key` names, read in the portfolio's one statement as a second
anchor column — never from `STARTING_CREDITS`, which can change without
rewriting an entry. A seeded position (`settlement_fixtures.hold`) has a
basis no entry paid for, so no derived P&L can reconcile against it; the
#359 tests buy into their settled markets for real.

---

### D-NEW — An order's `filled_value` is what its fills moved, signed positive on either side

**Date:** 2026-10-18 · **Ticket:** #26 · **Status:** active

**Decision.** `ledger.orders.filled_value` is the sum of the credits an
order's fills have moved, signed positive on both sides: a buy's costs, a
sell's proceeds. ADR 0024 and the entries before this one call it `spent`,
which is true of a buy only. A buy's reservation is `budget - filled_value`,
the `budget - spent` of "A buy reserves `limit × quantity` rounded up to the
tick, its reservation is `budget - spent`, and it cannot go negative". A sell
reserves no credits, so its `filled_value` is read only for
`average_fill_price`, which is `filled_value / filled_quantity` on either
side.

**Why.** The order object carries `average_fill_price` for buys and sells
alike. Reading a sell's from its fills is a query on `context->>'order_id'`,
whose index is a stretch item ("A fill's `context` is a trade's, plus
`order_id`"), on every `GET /ledger/orders/me`. One rollup serves both
sides, updated under the book lock beside `filled_quantity`. The name has to
be true of both: a sum of `spent` reads as money going out, and a sell's
proceeds summed into it would make a figure about committed credits wrong in
the trader's favour.

**Rejected.** *A `proceeds` column for sells*: two rollups where one
serves, each bound to its side by a CHECK. *A sell's average from its
fills*: the unindexed query above. *Keeping the name `spent`*: false of a
sell, for the reason above. *A sell's value signed negative*: the average
would need the side to read its sign, and `ck_orders_filled_value_nonneg`
would split by side.

---

### D-NEW — A preview reports what a buy pays if its outcome wins

**Date:** 2026-10-19 · **Ticket:** #384 · **Status:** active

**Decision.** `GET /ledger/markets/{id}/preview` carries `payout_if_wins` and
`profit_if_wins`, decimal strings at the money scale of 4. On a buy,
`payout_if_wins` is the quantity, because a winning share pays one credit, and
`profit_if_wins` is that payout minus the credits the buy costs — the
magnitude of `total`, the charged figure, subtracted exactly. On a sell both
are null. Both describe this trade alone, never the trader's position.

**Why.** The trading surface (#357) shows what a buy pays and what it makes,
and the frontend may not do arithmetic on decimal strings, so the server has
to return both. Both operands are already at scale 4 — `quantity` is refused
past four decimal places and `total` is quantized to the tick — so the
subtraction is exact and adds no rounding of its own; the profit is the
payout less exactly what would be charged.

**Rejected.** *A sell's payout as its proceeds*: a sell receives its credits
now, which `total` already says, and gives up the shares that would have paid;
it has nothing to pay out if the outcome wins. *Valuing the whole position*:
needs the caller's holdings, a database read the preview does not make (it
stays AMM-only and read-only), and the portfolio already reports a
position's value. *`profit_if_wins` from `quantity * average_price`*:
`average_price` is a rounded display figure, so the profit would disagree
with the charge by up to half a tick per share: two ticks in the docs'
example, where 10 × 0.7315 is 7.3150 against a charge of 7.3152.

**Notes.** `payout_if_wins` is the quantity rescaled to four places, so a
quantity sent as `10` reports `10.0000` while `quantity` still echoes `10`,
as "A quantity takes money's scale of 4 at the API boundary" has it. On a buy
charged its whole quantity, such as the smallest buys or a buy of an outcome
priced near one, the profit can be zero or a tick below; it is reported as it
is.

---

### D-NEW — The cascade loops until no waiting order is executable, and passes over an order it cannot fill

**Date:** 2026-10-18 · **Ticket:** #27 · **Status:** active

**Decision.** `core/limit_orders.py::cascade_of` repeats one step until it
finds nothing: among the market's open orders that have not filled in this
commit, in priority order ("Priority is price, then an identity `seq`
assigned under the book lock"), take the first that is executable at the
current `q` ("An order is executable when its floored fill passes the
per-fill limit check; a resting order is one that is not"), and fill it. An
order that is not executable is passed over, not a stop. An order that has
filled is not taken again in the same commit.

**Why.** An order can fail the check while one behind it passes: a
remainder of `0.0001` is never executable, because its cost rounds up to a
whole tick or its proceeds down to nothing, and a larger order with a
slightly higher `d` can still fill. Stopping at the first order that cannot
fill would leave the order behind it, the one that could fill, executable
when the cascade ends, which breaks the invariant in "Resting orders fill
against the AMM inside the trade that moved the price". Ending only when no
waiting order is executable makes the invariant hold by construction. The
loop runs at most `m + 1` times for `m` open orders, that decision's count,
and each order fills at most once, which the realtime contract's one order
frame per fill depends on.

**Rejected.** *Stopping at the first order that cannot fill*: the case
above. *One pass in priority order*: it finds the same fills only while no
fill makes an order passed over earlier executable, and that rests on an
argument about rounding at the tick rather than on the loop's exit
condition.

**Notes.** The planner is pure: it takes `q`, `b` and the open orders, and
returns the fills in order, each priced by `trade_cost_of` against the `q`
the fill before it left. A fill that `trade_cost_of` refuses as
`cost_below_tick` or `proceeds_below_tick` is not executable. A market with
more than two outcomes holds no orders, so a cascade there plans nothing.

---

### D-NEW — A refusal from the running check reports the balance before the batch

**Date:** 2026-10-18 · **Ticket:** #26 · **Status:** active

**Decision.** `InsufficientFunds` from `_refuse_overdrafts` carries
`balance`, the account's balance before the batch, and `reserved`, the
running reservation at the refused step. A refusal after a debit carries
`required`, what the batch had taken from the account by that movement,
`balance` less the running balance there; `available` is `balance - reserved`,
and the refusal is `required > available`. A refusal at the start of the batch
carries `shortfall`, `reserved - balance`, and no `required`; its `reserved`
includes a placement's whole new budget, so its `available` is negative.

**Why.** For the trade route, the trade is the batch's first movement, so
`required` is its cost and `balance` and `available` are the figures before
it, unchanged from what [FE] #49 reads today plus the two ADR 0024 adds. For
two debits in one batch, `required` is both of them, which is what the netted
check reported. The start-of-batch check has the balance and the reservation
and nothing else (ADR 0024, "A refused placement carries `shortfall`, not
`required`").

**Rejected.** *The running balance at the refused step as `balance`*: a later
debit's refusal would show a balance the account never had once the batch
rolled back. *Leaving `reserved` and `available` out of the start-of-batch
refusal*: the contract lists them for the placement's refusal, and the check
has no other figures to give.

**Notes.** Posting cannot check a drop against the order rows, only its sign
and its account, so a drop that disagrees with them is for the limit-order
stack's PR 5 to pin, where the cascade computes it. The floor at the start of
the batch is the reservation after it plus the batch's drops, so an undersized
drop is the one that loosens the check. With 100 held and a buy order of
budget 60 that a fill exhausts, a drop of 50 instead of 60 starts the batch at
50 reserved, and a trade of 45 ahead of the fill passes against the 40
available. An over-large drop only refuses more.

`posting.Movement.reservation_drop` is refused as `MalformedBatch` when it is
below zero, nonzero on a movement without exactly one USER account, or
nonzero on one that does not debit that account: a negative drop would lower
the floor at the start of the batch, a drop on a movement that only credits
its account is not the buy-order fill ADR 0024 gives a drop to, and
a drop with no single account has nowhere to go. The placement in the
limit-order stack's PR 4b may prefer to report its own `reserved` and
`available`, excluding the new order, on the start-of-batch refusal; it would
then catch and re-raise, or hand posting the figure.

---

### D-NEW — A placement's `insufficient_funds` reports the reservation without the new order, on both paths

**Date:** 2026-10-18 · **Ticket:** #26 · **Status:** active

**Decision.** A buy order whose budget does not fit is refused
`409 insufficient_funds` with `balance`, `reserved`, `available` and
`shortfall`, and the same three figures mean the same thing whether the order
would have rested or filled on arrival. `reserved` is what the user's other
live buy orders hold, without the new order, `available` is
`balance - reserved`, and `shortfall` is the budget less `available`. On the
resting path `service/orders.py` computes them itself under the account lock.
On the path that fills, `posting`'s start-of-batch check refuses first with a
`reserved` that includes the new budget, and the placement catches that
refusal and raises it again with the new budget taken back out of `reserved`.
`shortfall` is unchanged by that, because it is the same number either way.

**Why.** The contract gives one row for both paths and tells the frontend to
show how much more credit the budget needs. A trader who sees `available: 30`
on the balance route should see `available: 30` on the refusal, not `-10`,
and which path a refusal took depends on the price at that moment, which the
trader cannot see. "A refusal from the running check reports the balance
before the batch" left the choice to this placement in its Notes.

**Rejected.** *Posting's figures as they are*: a negative `available` that no
other route ever shows, and different figures for one situation depending on
whether the order was marketable. *Handing posting the budget so that it
reports the figure itself*: a placement-only argument on the shared write
path, for a number the caller already has.

**Notes.** Only a refusal that carries `shortfall` is rewritten. A refusal
after a debit, with `required`, cannot happen on a placement's own fill,
because a budget that fits at the start of the batch covers the fill (ADR
0024, "A buy can never spend more than it reserved"); if one ever does, it
passes through unchanged.

---

### D-NEW — A fill the running check refuses inside a cascade is `500 cascade_fill_refused`, told apart by the refused movement's key

**Date:** 2026-10-18 · **Ticket:** #27 · **Status:** active

**Decision.** `InsufficientFunds` carries `movement_key`, the idempotency key
of the movement after which `posting`'s running check refused, and none for a
refusal at the start of the batch. The trade path answers
`409 insufficient_funds` only when that key is the trade's own. Any other
refusal of a trade's batch, at the start of it or after a fill, is
`500 cascade_fill_refused`: the commit rolls back, the trader is told nothing
about the account that was refused, and the ledger logs the refusal's figures
at ERROR on `service.trading`.

**Why.** ADR 0024 makes a refused fill impossible by construction, because
every fill is bounded by its own reservation, and says that if one happens it
is a bug, a 500, logged. Only `posting` knows which movement it refused at, so
it has to say. Without the key, a refusal of another owner's account would
reach the trader as their own `insufficient_funds`, with that owner's balance
and reservation in `details`. A refusal at the start of a trade's batch is a
bug too: a trade drops no reservation, so its account's figure before the
batch is the one every earlier commit already kept within the balance.

**Rejected.** *Telling the two apart by the figures*, such as `required` equal
to the trade's cost: two different movements can carry the same amount.
*Passing the refused order over*: the order would rest while executable,
which breaks the invariant the next trade's cascade relies on.

**Notes.** A placement tells the two apart by the account instead, because
its own refusal comes at the start of the batch, with no key:
`InsufficientFunds` also carries `account_id`, the account the check refused.
A refusal of the placer's account keeps its own handling, in "A placement's
`insufficient_funds` reports the reservation without the new order, on both
paths", as `409 insufficient_funds` with the placer's figures. A refusal of
any other account is `500 cascade_fill_refused`, logged the same way. Its
cascade finds nothing while the book is uncrossed (ADR 0024, "Termination"),
so only a broken book reaches that 500. Keying the placement on its own
fill's movement key, as the trade path is keyed, would turn the placer's own
start-of-batch refusal into a 500.

---

### D-NEW — A cancel takes the book lock and no account lock

**Date:** 2026-10-18 · **Ticket:** #28 · **Status:** active

**Decision.** `POST /ledger/orders/{order_id}/cancel` reads the caller's order's
`market_id` without a lock, takes that market's book row with `FOR UPDATE`,
reads the order again under it, and either refuses or writes `status =
'cancelled'` and `cancelled_at`, then commits. It takes no account lock and
writes no entry. A filled order is `order_already_filled`, a cancelled one
`order_already_cancelled`, and an open one on a market with a result
`order_released`, in that order.

**Why.** Every writer of an order row takes the book lock first (the
2026-10-17 amendment to "The holdings check is read under the book lock, and
the position takes no lock of its own"), so the cancel and a fill queue on one
row, and whichever takes it first commits first. The re-read under the lock is
what turns that into an answer: a fill that committed first has left either a
smaller remainder or none. No account lock, because a cancel only lowers a
reservation: every path that reads one under an account lock is checking that
a debit fits, and a check that read it before the cancel committed read a
higher figure, so it was stricter than it needed to be, never looser. The
unlocked read decides only which book to lock and whether the order is the
caller's, and neither can change: an order's market and owner are fixed at
placement.

**Rejected.** *An account lock after the book row*: it would serialise a
cancel behind the owner's trades in other markets for no invariant. *A `FOR
UPDATE` on the order row*: the book lock already holds it still, and the
amendment's reversal trigger has not fired. *Loading the order before the
lock*: the session's identity map would then answer the re-read with the
stale row unless it were told to refresh, so the first read selects one
column.

**Notes.** The status is read before the result because a filled or
cancelled order is never live, so reading `live` first would answer every one
of them `order_released`; and a filled order on a market with a result had
nothing left for the result to release.
`test_cancel_order_concurrency.py` races a cancel against a fill in both
orderings and the partial case; each test fails with the cancel's
`books.lock_book` removed.

### D-NEW — Order frames pass no version gate on the relay

**Date:** 2026-10-18 · **Ticket:** #27 · **Status:** active

**Decision.** After a commit that filled resting orders, the ledger publishes
the commit's one `price` frame first and then one `order.update` message per
fill, in fill order, each publish swallowing its own Redis failure
(`service/trading.py::_publish_order_updates`). The realtime service forwards
each order message to every socket of its `user_id` and runs it through no
version gate, neither the per-market one prices use nor one of its own.

**Why.** The price frame carries the book's final `state_version`, and every
fill in that commit is at or below it, so the per-market gate would drop each
order frame as stale the moment the price frame passed. A gate keyed by order
id would hold one entry per order ever filled, for the life of the process,
and still be no guarantee: replicas keep their own marks, so the client has to
order one order's frames by `fill.state_version` anyway
(docs/api/realtime-service.md, "Order updates"). The price frame keeps its
place straight after the commit, where every subscriber of the market already
expects it, and the order frames follow it.

**Rejected.** *Order frames first*: no client depends on the order between the
two, and the price frame is the one every subscriber of the market needs. *A per-order
gate*: unbounded, and redundant with the client's rule. *Publishing on a
cancel*: the contract says a cancel sends none, and its response carries the
order.

**Notes.** In the realtime suite,
`test_order_frames_are_delivered_after_their_commits_price_frame` publishes
the ledger's real sequence, a price frame at version 4 and order frames at 2
and 3, and fails if the order frames go through the gate.

**Amended 2026-10-20.** The function that publishes the order frames is now
public, `service/trading.py::publish_order_updates`, because the placement in
`service/orders.py` calls it too (#391). The decision is unchanged.

---

### D-NEW — A setting that can hold a credential is a `SecretStr`, URLs included

**Date:** 2026-10-19 · **Ticket:** #307 · **Status:** active

**Decision.** `jwt_secret`, `ledger_service_key`, every `database_url` and
both `redis_url`s are typed `pydantic.SecretStr`, so a printed `Settings`
shows `SecretStr('**********')` in their place. Each reader takes the value
with `.get_secret_value()` at the point of use and hands on a plain `str`:
the token signer and verifiers, the engine, the Redis clients and both sides
of the `X-Service-Key` check. Nothing below that point sees the wrapper.

**Why.** A `Settings` object is printed more often than anyone means it to
be: pytest puts the target's repr in an `AttributeError` from
`monkeypatch.setattr`, which is how #307 showed a developer's signing key in a
test log, and any log line or exception that formats one does the same in a
deploy. A URL counts because the form is `USER:PASSWORD@HOST`; the Redis URL
counts because a deploy may give it a password, even though the default has
none. This agrees with "`REDIS_URL` has a default in the ledger and none in
the realtime service", which allows the ledger its default because that value
is not a credential. It still is not; the type covers the value a deploy sets
in its place.

**Rejected.** *`Field(repr=False)`*, the smaller change #307 offered for the
URLs: it hides the field from `repr` and `str` and nothing else, so
`model_dump()` and anything that serialises the model still print it. One rule
for every credential is also easier to follow than two. *Masking only the
password inside the URL*: it needs a parser per scheme and keeps half of a
value nobody needs to read.

**Notes.** A new setting that can hold a credential follows the same rule,
and its service's `test_no_secret_appears_when_the_settings_are_printed`
should name it. A test that replaces such a setting with `monkeypatch.setattr`
passes a `SecretStr`, since assignment is not validated. The migrate step reads
`DATABASE_URL` from the environment, not from `Settings`, and is unaffected.

---

### D-NEW — An administrator can cancel any live order, with a reason

**Date:** 2026-10-19 · **Ticket:** #326 · **Status:** active

**Decision.** `POST /ledger/admin/orders/{order_id}/cancel` cancels any
trader's live order, an open one on a market with no result, exactly as its
owner's cancel would: the same `service/cancellation.py::cancel`, called with
an `AdminCancel` instead of the owner's id, so the same book lock, the same
re-read under it and the same refusals. It skips only the owner check. It
needs an administrator's token, through `CurrentActor`, the settlement
route's guard. Its body is one required field, `reason`, with auth's
suspension rule: at most 500 characters in the schema (`invalid_request`),
and at least 10 once trimmed in the service (`cancellation_incomplete`),
checked before any read. In the cancel's own transaction it appends
`order.cancelled_by_admin`, carrying the trimmed reason and the order's
`user_id` and `market_id` in `context`. The owner's own cancel stays
unaudited. Nothing is posted and no frame is published.

**Why.** ADR 0024, "A suspended owner's orders are checked at placement
only", leaves a suspended owner able to cancel only while their last access
token lives, and after that the reservation is held until the market has a
result, which a market that never gets one never gives. This is the release
path #326 promised as the condition of that narrowing. It takes the owner's
path rather than a copy, so the lock that keeps a cancel from racing a fill
also keeps the two cancels from both winning. The audit entry is ADR 0006's
shape for an administrator's decision; a trader withdrawing their own order
is not one, which is also why every entry of this type is an
administrator's.

**Rejected.** *Accepting an administrator's token on the owner's route*: the
owner's route takes an empty body, and an administrator cancelling their own
order there would have to be either audited without a reason or not audited
at all. *A path with no admin prefix*, as the admin trade feed has: it can
share `/ledger/markets/{market_id}/trades` with the trade because the two
differ by method, and both cancels are a `POST` on one order, so the second
needs a path of its own. `/ledger/admin/` is new to the ledger and follows
auth's `/admin/` routes. *`/ledger/users/{user_id}/orders/{order_id}/cancel`*,
the shape of the ledger's admin reads of another user's money
(`/ledger/users/{user_id}/balance` and `/entries`): an order fixes its owner,
so the user id is a second key that has to be checked against the order, and
a disagreement is one more way for an order that exists to answer
`order_not_found`. It is the closer fit if an admin list of a user's orders
lands as `GET /ledger/users/{user_id}/orders`, and the cost of moving is the
route's decorator, its docs, its two contract tests and that check; the
ledger then has two admin shapes until it does. *`order.cancelled` as the
action*: it reads as if every cancel were logged. *Market_service's
5000-character ceiling*: the suspension is the closer action, an
administrator acting on one trader's account.

**Notes.** `test_cancel_order_concurrency.py` races the administrator's
cancel against the owner's in both orderings; each fails with the cancel's
`books.lock_book` removed. The ledger keeps its own copy of the reason rule
(`_refuse_if_reason_unusable`), a twin of auth's `user_admin._reason_problems`
(ADR 0012).

---

### D-NEW — `GET /ledger/orders/me` returns every live order, unpaged

**Date:** 2026-10-20 · **Ticket:** #393 · **Status:** active

**Decision.** `GET /ledger/orders/me` keeps the contract it shipped with in
#26: every live order the caller has, newest first, in one response read by
one statement (`service/orders.py::list_live`), with no `limit` and no
`cursor`. It does not page through `shared/paging.py` as the ledger's history
routes do. A client replaces its whole list with each response.

**Why.**
- *It is a snapshot, not a feed.* docs/api/ledger-service.md calls it the
  snapshot for order frames, fetched when the page opens and again after every
  socket reconnect, and docs/api/realtime-service.md, "Order updates", says the
  same. The client replaces its state with what it reads, so the read has to
  be one point in time. A history feed need not be: a row its walk has not
  reached is one its reader can afford to miss until it reads from the top
  again, and a row it has shown stays true. This set loses members in the
  middle, under the reader: a cancel, a complete fill or a result's release
  takes an order out wherever it sits in the list. A keyset walk is not one
  point in time, so an order removed after its page was read stays on screen
  as live, and for a cancel or a release no frame comes to correct it: neither
  the owner's cancel nor an administrator's publishes one, and nor does a
  result. This route is the only way another tab learns such an order has
  gone. The routes that page are the other kind: `entries/me` and the
  administrator's `users/{user_id}/entries`, the market trade feed and the
  audit feed walk a history that only grows at the newest end, and the
  leaderboard walks one stored generation, frozen for the walk.
- *Unpaged is already the ledger's normal case.*
  `GET /ledger/portfolio/me` returns every held position in one response from
  one statement, so a trade committing mid-read cannot show its debit without
  its shares ("The portfolio reads its balance and positions in one statement,
  after the grant, with no lock").
  `GET /ledger/markets/{market_id}/price-history` returns every point
  unpaged; its `from` and `to` bound the payload, not the work.
- *The read is cheap, and the cap below bounds it better than paging.*
  `ix_ledger_orders_open_by_user` is partial on `status = 'open'`, so filled
  and cancelled orders cost it nothing. Every balance read, every debit's
  overdraft check and every placement's budget check already sum the user's
  live buy orders (`service/reservations.py::reserved_query_of`), through an
  index on `user_id` alone that holds the sells beside them. Those sums
  return one number however many rows they add up; this route builds one
  object per row, so it is the read that grows first. The cap named in the
  reversal trigger bounds it and the sums together, where paging would bound
  only this one.

**Rejected.**
- *Keyset paging with the shared cursor*, as #393 asked: an order removed
  after its page was read stays on screen as live with no frame coming to
  correct it, on the route the realtime contract names as the recovery path,
  and a contract change for [FE][T-6] #61 to buy that.
- *Paging at REPEATABLE READ*, as the admin overview does for its counts: that
  holds one snapshot inside one request, and a walk spans several.
- *A `limit` with no cursor*: a client cannot tell a short list from a cut
  one, and the orders it cannot see still hold credits its balance shows as
  reserved.

**Notes.** No change to the request or response: docs/api/ledger-service.md
already says "No query parameters and no paging". #61 builds the open-orders
list against it and replaces the whole list on each fetch. Nothing caps the
list today: placement is not rate limited, and at the 0.0001 minimum limit
and quantity a trader's starting credits buy millions of live buy orders.
That is the cap's job, not paging's.

**Reversal trigger.** A cap on live orders per trader at placement (a new
`409` on `POST /ledger/markets/{market_id}/orders`), which bounds this
response and the reservation sums together and lets the contract state the
bound; or a read of this route measured as slow, whose fix is that cap rather
than paging. Either way this entry is revisited, and paging comes back, if at
all, beside the cap.

---

### D-NEW — A portfolio row carries the shares its owner's live sells hold back, read in the portfolio's one statement

**Date:** 2026-10-21 · **Ticket:** #410 · **Status:** active

**Decision.** Every row of `GET /ledger/portfolio/me` carries
`reserved_shares`: the sum of `quantity - filled_quantity` over the caller's
live sell orders of that outcome, a decimal string at scale 4, `"0.0000"`
where there are none and never null. `quantity` keeps its meaning, the whole
position, and so do `value`, `cost_basis` and `average_entry_price`. The
figure is a correlated subquery in the portfolio's one statement, built by
`service/reservations.py::reserved_shares_query_of`, the same SELECT the sell
paths' holding check runs. `service/portfolio.py` adds it to the statement and
returns it beside the positions, keyed by `(market_id, outcome_id)`;
`service/valuation.py` does not see it.

**Why.**
- *The trade route already refuses on this figure.* ADR 0024 limits an
  instant sell to the position less what the seller's live sells reserve, and
  `insufficient_shares_held` reports `reserved` beside `held`. A client that
  offers `quantity` as the most a trader can sell offers a number that is
  refused once a sell order rests. Per `frontend/CLAUDE.md`, a figure the
  browser needs and the API does not return is a backend ticket, not a second
  call and a subtraction in the browser.
- *Derived, never stored.* For the reason a balance is not stored (ADR 0009)
  and a reservation is not (ADR 0024): every fill, cancel and result would
  have to keep a column in step, and none of them needs to now.
- *In the one statement.* "The portfolio reads its balance and positions in
  one statement, after the grant, with no lock": a fill moves the position
  and the order's `filled_quantity` in one commit, so two reads could show
  one of them without the other.
- *Beside the valuation, not inside it.* `held_outcomes_query` and
  `valuation_of` are shared with the leaderboard refresh, which values every
  holder and has no use for this figure. Putting it on `PortfolioPosition`
  would make the refresh compute it for every holder, or leave the field
  meaning nothing on the refresh's rows.
- *Liveness is `order_is_live`.* An order on a market with a result is not
  live whatever its status reads ("An open order is live only while its
  market has no result"), so a settled row reports `"0.0000"`.

**Rejected.**
- *`available_shares`, `quantity - reserved_shares`, in place of the
  reservation*: the issue asks for the reservation, and the order object
  already names that figure `reserved_shares`, so one name means one thing on
  both routes. The subtraction is exact at scale 4, with no rounding to get
  wrong.
- *Taking the reservation off `quantity`*: the shares are still held and
  still valued (ADR 0018's 2026-10-17 amendment), and `average_entry_price`
  divides by the whole holding.
- *A second statement through `reservations.reserved_shares_of` per
  position*: N statements and N snapshots, as above.

**Reversal trigger.** A portfolio read measured as slow because of this
subquery, or a sell order that holds back shares by some rule other than its
unfilled quantity.

---

### D-NEW — The batch snapshot is `POST /ledger/markets/snapshots`, a read with a body

**Date:** 2026-10-21 · **Ticket:** #432 · **Status:** active

**Decision.** The browse list's prices come from one
`POST /ledger/markets/snapshots` with a body `{"market_ids": [...]}`. It
answers `{"snapshots": [...]}`, and each item is the single snapshot's body,
`GET /ledger/markets/{market_id}/snapshot`, for that market. The route writes
nothing; it is a POST only because its input is a list.

**Why.**
- *The list does not fit a query string.* At the cap below, 200 ids as
  repeated `market_ids=<uuid>` parameters are about 9.4 KB of URL. Proxies and
  servers commonly refuse a request line past 8 KB, and the failure would
  appear only behind a deployment's proxy, never on loopback.
- *Nothing on the ledger takes a list yet*, so there is no precedent to
  follow; the body is the shape #432's contract proposed.
- *An object around a list, and an object per market,* so a field can be
  added later without breaking a client: #446's prior price and #447's
  activity figure are per-market fields that ride on this route rather than a
  second request per card, and a field for the whole answer fits beside
  `snapshots`.
- *Each item is the single snapshot's body*, which is the `price` frame
  without its `type` (docs/api/realtime-service.md), so the frontend renders
  all three with one function.

**Rejected.**
- *`GET` with repeated `market_ids`*: the URL length above, and an encoding
  every client library spells differently.
- *An answer keyed by market id* (`{"<id>": {...}}`): it drops `market_id`
  from the item, so the item stops being the single snapshot's body.

**Reversal trigger.** A second list-taking route on the ledger that chooses a
different shape, at which point the two are made to agree.

**Amended 2026-10-21.** Each item is now the single snapshot's body **plus
`prices_7d_ago`** ([X-1] #446) **and `credits_traded`** ([X-1] #447), two
fields only the batch carries, so an item is no longer exactly that body.
"The batch snapshot carries each outcome's price a week ago, not the change,
and the single snapshot does not" and "The batch snapshot carries
`credits_traded`, and the single snapshot does not" say why. The point of the
bullet above still holds: every field the single snapshot has is still in the
item and means the same, so one function still renders the single snapshot,
the `price` frame and an item, and the frontend reads the extra fields beside
it.

---

### D-NEW — The batch snapshot takes at most 200 ids, and refuses more

**Date:** 2026-10-21 · **Ticket:** #432 · **Status:** active

**Decision.** `market_ids` holds at most 200 ids, counted as sent, duplicates
included. A 201st is `422 invalid_request`, its `details` naming
`["body", "market_ids"]`. An empty list is a `200` with no snapshots.

**Why.**
- *200 is market_service's `max_page_size`*, the largest page
  `GET /public/markets` serves. Any page of the browse list, at any `limit`
  that route accepts, fits in one batch, which is #432's "`listMarkets`' page
  size and the batch cap agree". At the default page size of 50, one batch
  covers four pages.
- *Refused, not clamped.* Elsewhere on the ledger an oversized `limit` is
  clamped, because the caller can see where the page ends. Here a market left
  out by a clamp would be indistinguishable from a market with no book, and
  its card would show "—" with nothing saying why.
- *`invalid_request`, not a new code.* It is a shape refusal, like every other
  ceiling on a request body here (a quantity's digits, a reason's length), and
  the client's remedy is the same: send fewer.
- *The cost at the cap is bounded.* One indexed statement returning one row
  per outcome of at most 200 markets, with no lock and no call out.

**Rejected.** A cap of 50, the default page: the frontend's refresh re-reads
every still-trading market on every page it has loaded, and would split its
request at each page. A setting: the frontend has to know the number, and a
setting changed on one side only breaks the list.

**Reversal trigger.** market_service's `max_page_size` changing, or the
browse list asking for more than one page's prices in one call.

---

### D-NEW — The batch snapshot opens no book: a market without one is absent

**Date:** 2026-10-21 · **Ticket:** #432 · **Status:** active

**Decision.** The batch answers only markets that already have a book on this
service. An id with no book, whether it names no market or a published market
nobody has previewed, traded or snapshotted, is left out of the answer, not
an error. It makes no call to market_service and writes nothing. The single
snapshot stays the only snapshot that opens a book (D-050).

**Why.**
- *Opening a book is the expensive path, once per market.* It fetches the
  market's terms from market_service, up to the five-second timeout (D-030),
  then funds the pool under the handoff's locks, the platform account among
  them (D-036), and commits. Fifty cold markets in one request would run that
  fifty times in a row, holding the whole list behind the slowest, and queue
  every grant and trade on the platform account lock meanwhile.
- *The single snapshot already serves the paths that need a price for sure.*
  docs/api/realtime-service.md's "Opening a page" and "Reconnecting" sequences
  read the single route, and ADR 0010 makes that read the authoritative
  recovery path. The batch is a list's first paint and its refresh; a card
  with no price shows "—", which it already does for a failed request.
- *No id the caller may not read can be answered.* A book is opened only from
  market_service's public read, which refuses a draft or a submitted market,
  and publication is one way (ADR 0008). Every market with a book is one any
  signed-in role may read, so the batch needs no check per id, and #432's
  "a market the caller may not read is absent" holds by construction.
- *It never gates on status (ADR 0017),* like the single snapshot: a closed
  or settled market with a book is answered with its last price.

**Consequence.** A market nobody has opened shows no price on the browse list
until something opens its book. The frontend may ask the single snapshot for
a still-trading market the batch left out; that opens its book once, ever,
and the batch answers it from then on.

**Rejected.** Opening cold books inside the batch, for the cost above. A
seed price for a market with no book: the ledger cannot name its outcomes
without asking market_service, which is the cost again.

**Reversal trigger.** A book opened at publication, which would give every
published market a book and make this rule unobservable.

---

### D-NEW — A book the batch snapshot cannot price is left out and logged

**Date:** 2026-10-21 · **Ticket:** #432 · **Status:** active

**Decision.** A book `book_prices.refuse_unpriceable` refuses (fewer than two
outcome rows, or a `liquidity_b` the engine cannot use) is left out of the
batch's answer and logged at warning level, rather than failing the request
with `500 market_book_incomplete` as the single snapshot does. A book with no
outcome rows at all never reaches the guard, because the join drops it, and is
left out without a log line.

**Why.** One damaged book would otherwise blank every card on the page. The
fault is still reported: the single snapshot refuses that market with
`market_book_incomplete` when its page opens, and the warning names it in the
log. The guard is the same one copy the single snapshot and the trade use
(D-052), so the batch refuses exactly the books they refuse.

**Reversal trigger.** A way for a damaged book to reach the browse list that
a log line would hide, such as a migration that half-applies to many books.

---

### D-NEW — The batch snapshot reads every market in one statement, and answers in the order asked

**Date:** 2026-10-21 · **Ticket:** #432 · **Status:** active

**Decision.** `service/book_prices.py::list_book_outcomes_for_markets` reads
every requested market's book joined to its outcomes in one statement, with
no lock. The answer lists each market once, in the order its id first
appears in the request; a repeated id is not an error.

**Why.**
- *One statement for N markets*, so the cost of a page is fixed rather than a
  query per card, which is the N+1 the route exists to remove.
- *Each snapshot is consistent with itself*, D-013's rule extended: a
  market's `state_version`, `b` and `q` come from the same statement, so a
  client resuming from that `state_version` holds the prices that version
  priced. All the markets come from that one statement's view of the
  database, too.
- *No lock*, D-012's reason: the read decides no write, and a lock would
  queue a page of cards behind any one trade holding its book.
- *Request order* makes the answer deterministic, so a client can rely on it
  and a test can assert it. The frontend keys its prices by market id and does
  not need it.
- *Duplicates answered once*, because refusing them would make every caller
  deduplicate for no gain, and answering twice would hand a client two copies
  of one market to reconcile.

**Reversal trigger.** None expected.

---

### D-NEW — A market's activity is the credits it has traded, buys and sells alike

**Date:** 2026-10-21 · **Ticket:** #447 · **Status:** active

**Decision.** A market's activity figure is `credits_traded`: the credits
every trade in it has moved, a buy's cost and a sell's proceeds each counted
as a positive amount. A resting order's fill is a trade (ADR 0024) and counts,
on arrival and in a cascade alike. Nothing else the pool posts is a trade, so
the seed, a settlement payout and the settlement residue do not count. Put
exactly: the sum of the absolute amounts of the pool account's legs in
`trade_buy` and `trade_sell` transactions, which is the sum of each trade's
`TradeCost.magnitude`.

**Why.**
- *A trade count is already on the wire.* `state_version` rises by one per
  trade and per fill, and nothing else moves it (D-011), so a count would be a
  second copy of a field every snapshot carries. #445 already reads it as
  "has anything happened". What the list lacks is the magnitude.
- *Credits rather than shares.* A share priced at 0.02 and one at 0.98 are not
  the same amount of activity, and outcomes of different markets are not
  comparable share for share. Credits are one unit across every market, which
  is what a list sorted or curated by activity compares.
- *Unsigned, both sides.* Selling is as much activity as buying. A signed sum
  is the pool's net take from trading, which falls when traders sell back and
  says nothing about how busy a market is.
- *Named for what it is.* "Volume" was #447's own ambiguity; `credits_traded`
  says the unit and that both sides count.

**Rejected.** Shares traded and a trade count, for the reasons above. Two
figures: the second would be a field nothing reads.

**Notes.** #221's draft void refunds with `void_refund` movements and writes
no trade, so a voided market would keep the credits it traded: the trades
happened.

**Reversal trigger.** The browse list asking for recent activity, such as the
last 24 hours. A lifetime figure cannot answer it; that is an aggregate over
the pool's trade entries by time, beside this field or instead of it.

---

### D-NEW — `credits_traded` is a counter on the book, raised in `write_trade` under the book lock

**Date:** 2026-10-21 · **Ticket:** #447 · **Status:** active

**Decision.** `ledger.market_books.credits_traded`, `numeric(18, 4)`, zero
when the book opens. `service/trading.py::write_trade` raises it by the
trade's magnitude on the line after `state_version`, so the trade, a fill on
arrival and a cascade's fill each raise it, under the book lock, in the
transaction that posts their entries. Ledger revision 0006 adds the column and
backfills every existing book from its pool's trade legs.

**Why.**
- *Close to free on the trade path.* `write_trade` already changes the book
  row under its lock, so the figure is one more column in an UPDATE the trade
  already issues: no statement, no lock and no read is added.
- *Constant on the read path.* The batch reads it from the row it already
  reads, in its one statement, so a page of 200 markets costs what it cost
  before, and the figure and `state_version` come from one snapshot (D-013).
  An aggregate would sum every trade entry of up to 200 pools on every
  refresh of every open browse page, a cost that grows with each market's
  history.
- *Correct under concurrency for the reason `state_version` is.* Every writer
  holds the book row, so two trades raise it one after the other. The same
  rollup shape as an order's `filled_value` ("An order's `filled_value` is
  what its fills moved, signed positive on either side").
- *An exact backfill.* Every trade posts exactly one pool leg, of exactly its
  magnitude, so summing those legs reproduces the counter to the tick.
- *Not a balance.* ADR 0009 derives balances because money decisions read
  them. No decision reads this figure; it is a display statistic, like
  `state_version`.

**Rejected.** *An aggregate per read*: the read cost above, on the list's
hottest request, to avoid one column on a row the trade already writes.

**Consequence.** A path that posted a trade movement without `write_trade`
would leave the figure short. None does: the trade and both fill paths call
it, and it is where `state_version` is raised, which a missed trade would
break visibly.

**Reversal trigger.** A trade path that does not go through `write_trade`.

---

### D-NEW — The batch snapshot carries `credits_traded`, and the single snapshot does not

**Date:** 2026-10-21 · **Ticket:** #447 · **Status:** active

**Decision.** Each item of `POST /ledger/markets/snapshots` is the single
snapshot's body plus `credits_traded`, a decimal string at scale 4.
`GET /ledger/markets/{market_id}/snapshot` and the `price` frame are
unchanged. A market whose book has never traded is answered with
`"credits_traded": "0.0000"`, never left out for it. A market with no book is
still absent ("The batch snapshot opens no book: a market without one is
absent"), so absent keeps meaning only "no book here".

**Why.**
- *The single snapshot is the `price` frame without its `type`,* byte for
  byte: [F-9] #112's criterion, and docs/api/realtime-service.md's "Identical
  on purpose". On the snapshot alone the figure would be a field the frames
  that follow it never update. On the frame too, it would change
  realtime_service's `extra="forbid"` schema for a field no subscriber reads:
  the browse list re-reads the batch rather than subscribing, and the detail
  page does not show activity.
- *A superset keeps the one render function.* The item still has every field
  the single snapshot has, meaning the same thing, which is what "Each item
  is the single snapshot's body" was for.
- *Zero, not absent,* is #447's criterion: an untraded market is the case the
  figure exists to tell apart from a busy one.

**Reversal trigger.** A page that subscribes to the socket wanting the figure
live, which would put it on the frame, and then on the single snapshot with it.

---

### D-NEW — A voided row's `payout` is its refund, joined in the portfolio's statement only

**Date:** 2026-10-20 · **Ticket:** #221 · **Status:** active

**Decision.** On a voided market, a portfolio row's `result` is `refunded`, a
new `PositionResult` member, and its `payout` is the refund that position
received. It is read through a left join from the portfolio's one statement
to `ledger.transactions`, on the derived key
`void-refund:{market_id}:{user_id}:{outcome_id}`, which the unique index on
`idempotency_key` serves. A refund that rounded down to zero wrote no
transaction, and the join coalesces it to `0.0000`. The join is not in
`held_outcomes_query`. `price`, `value` and `unrealized_pnl` are `null`, and
the row adds nothing to `positions_value`, as on a settled row.

**Why.** A refund follows the lots, newest sold first, so it is neither the
quantity nor the cost basis, and no column holds it. The transaction is the
only record, and its key is derivable from the row. Ernest's note on #221
(from #369's review, re-raised in #387's) said a voided row reading `worthless`
with payout `0.0000` would look odd; showing the refund answers it. Keeping
the join out of `held_outcomes_query` keeps the leaderboard's refresh to the
reads it has: net worth never reads `payout`, and the refund is already in
the balance.

**Rejected.**
- *`payout: "0.0000"` and `result: "worthless"`*: what the code would do
  today. It says the holder got nothing, and they were refunded.
- *`payout` null on a voided row*: the portfolio would hide the one figure a
  holder of a voided market wants.
- *Storing the refund on the position or the result row*: a stored copy of a
  ledger sum, which ADR 0009 refuses.
- *The join in `held_outcomes_query`*: the leaderboard would read every
  holder's refunds on every refresh, for nothing it shows.

**Notes.** Decided by Ihsan on 2026-10-20 (ruling A of ADR 0025). P&L needs
no change: a voided row's basis is closed like a settled one's, and the
refund is in the balance. The portfolio reads the row's `kind` only to choose
`refunded`: a refund that rounded to zero has no transaction, so the join
alone cannot tell a voided row from a settled one. The refund reaches
`_settled_positions_of` as a keyword-only mapping on `valuation_of`, from
`(market_id, outcome_id)` to the refund, empty by default. The portfolio
builds it from its join column. The leaderboard passes nothing, so its rows
never need the column, its voided rows carry `0.0000`, and it reads only
`net_worth`. An optional attribute on the row was rejected: a SQLAlchemy `Row`
has no defaults, so the leaderboard's rows would raise on it.

---

### D-NEW — The void's refund replay reads `pool_trade_query`, and shares the price history's 1..N check

**Date:** 2026-10-20 · **Ticket:** #221 · **Status:** active

**Decision.** The void reads the market's trades through
`ledger_service.pool_trade_query`, ordered by `(created_at, id)` as the price
history orders them. The check that their `state_version`s run 1, 2, 3, … in
order from the start moves out of `core/price_history.py` into one pure
function in `core/` that both replays call. The void alone also checks that
the run ends at the book's `state_version`; the price history does not, and
this does not change that. The replay reads each trade's
`context.state_version`, never `book_state_version`. The price history maps a
failure to `500 price_history_inconsistent`, and the void to
`500 void_replay_mismatch`.

**Why.** #221 revision 2 ordered by `context.state_version` in SQL because
entry stamps could disagree with lock order (#187). #187 is fixed: `post_all`
stamps under the account locks, so the pool's feed is in lock order. One
query and one check, shared, rather than a second ordering of the same rows
and a second copy of the guard.

**Rejected.**
- *Ordering by `(context->>'state_version')::int`*: a sort no index serves,
  for an order the feed already has.
- *A second copy of the 1..N check in the void's core module*: two copies of
  a rule whose divergence would be a bug.

**Notes.** Decided by Ihsan on 2026-10-20 (ruling B of ADR 0025). The
function's name is the core PR's. The extraction is behaviour-free for the
price history, so it can sit in the void's core PR with the price history's
tests unchanged and green. Since #446, `list_prices_at` reaches the check
through `price_history_of` too, and it logs a failure and leaves out only that
card's `prices_7d_ago` rather than answering `500`. The extraction keeps that
mapping, as it keeps the price history's.

---

### D-NEW — The market_service status tables are composed in a refactor PR before VOIDED is added

**Date:** 2026-10-20 · **Ticket:** #221 · **Status:** active

**Decision.** A refactor PR, first in the void's market_service stack,
composes or derives `_FROZEN_STATUS_ERRORS` and `_RESOLUTION_STATUS_ERRORS`
(market_service's `service/market_service.py:73-87`) and the decision state
check in `_proposal_to_decide` (`:525-555`, the state checks at `:543-548`),
with no behaviour change and no test assertion changed. VOIDED is then added
in its own PR, to the enum and every table at once.

**Why.** "A settled market is refused after the locked read and before any
write, and the status tables stay separate" named this trigger: a second
terminal status. Root CLAUDE.md keeps a refactor out of a behaviour PR. And
the bug #220 found for SETTLED, a final status missing from the frozen table
so that an autosave turned it back into a draft, is the one a composed table
makes hard to repeat.

**Rejected.** *Adding VOIDED to each table by hand*: the repetition that
entry deferred until now, done twice over.

**Notes.** Decided by Ihsan on 2026-10-20 (ruling C of ADR 0025).

---

### D-NEW — `MarketTerms` carries `voidable`, required, and market_service deploys it first

**Date:** 2026-10-20 · **Ticket:** #221 · **Status:** active

**Decision.** `MarketTerms` gains `voidable: bool` with no default. `_parse`
reads it by index and refuses anything but a JSON boolean, as it does
`settleable`. A failure is `503 market_terms_unavailable`, on every pull.
market_service's `voidable` lands and deploys before the ledger's parse.

**Why.** A missing flag read as true would refund a market whose result is
final. A missing flag read as false hides a broken deploy as a market that
can never be voided. The client already parses strictly, so a lenient field
would be a second style in one parser. "`MarketTerms` carries
`proposed_outcome_id` and `settleable`, parsed strictly on every pull" gives
the rest.

**Rejected.** *An optional field the void alone checks*: one client parsing
two ways.

**Notes.** Decided by Ihsan on 2026-10-20 (ruling D of ADR 0025). Every
trade pulls the terms, so a ledger with this parse against a market_service
without `voidable` answers `503` to every trade. The stack's order prevents
that: market_service's `voidable` PR sits below the ledger client's.

---

### D-NEW — A void's reason is at most 5,000 characters and at least 10 once trimmed, and a short one is `void_incomplete`

**Date:** 2026-10-20 · **Ticket:** #221 · **Status:** active

**Decision.** The ledger's void route takes `reason`, at most 5,000
characters in the schema (`422 invalid_request`) and at least 10 once trimmed
in the service (`422 void_incomplete`), checked before any read. It stores the
trimmed reason on the result row and forwards it to market_service, which
stores it as sent and does not re-check it.

**Why.** The reason lands in `market.void_reason`, so its ceiling is
market_service's for every reason it stores, not the 500 the administrator's
order cancel copies from auth. The floor is close-early's and rejection's.
`*_incomplete` is the name every reason refusal in this repository has.
market_service does not re-check, because a stricter check there would fail
every retry after the money moved.

**Rejected.** *500 characters, as the administrator's cancel*: that cancel
acts on one trader's account, and its reason is logged only. A void's reason
is shown to every trader in the market.

**Notes.** A ruling of the PR that wrote ADR 0025, not of #221's thread. The
5,000 ceiling was confirmed by Ihsan on 2026-10-25 (ADR 0025, question 3).

---

### D-NEW — The void's response reports `total_owed` and `refunds_cut`, and a repeat recomputes them from the trades

**Date:** 2026-10-20 · **Ticket:** #221 · **Status:** active

**Decision.** The void's `200` carries `positions_refunded`, `total_owed`,
`total_refunded`, `refunds_cut` and `residue`. A repeat sums
`positions_refunded`, `total_refunded` and `residue` from the void's own
transactions, found by their derived keys, and recomputes `total_owed` by
re-running only the lot replay over the market's trades: without the guard
and without the stake check, so a repeat always answers the first request's
`200` body, and can fail after the lookup only at market_service's `/void`
step.

**Why.** #221 revision 2 asks that whether refunds were cut be in the
response, and that a repeat answer the same. The result row stores no totals
(ADR 0019), and no transaction records what a cut refund was owed. The trades
cannot change after the row exists, because the latch refuses every later
one, so a replay gives the same figure.

**Rejected.**
- *Storing the owed total or the fraction on the result row*: a stored figure
  derived from ledger rows.
- *Reading it from the `market.voided` entry*: the ledger cannot read the
  audit log.
- *A fraction rather than `total_owed`*: a ratio needs a scale, and two
  amounts at scale 4 need none.

**Notes.** A ruling of the PR that wrote ADR 0025. A repeat pays for the
replay again, which only the speed test will price. Accepted by Ihsan on
2026-10-25 (ADR 0025, "Decisions on the open questions", question 2): a repeat
happens only after a `503`, and the record stays free of derived totals.

---

### D-NEW — `voided_at` and `void_reason` are on the public detail and `MarketOut`; `voidable` is on the public detail and the overview rows

**Date:** 2026-10-20 · **Ticket:** #221 · **Status:** active

**Decision.** The public detail and `MarketOut` carry `voided_at` and
`void_reason`, required keys with nullable values. The public detail and every
overview row carry `voidable`. None of the three is on the trader browse cards,
and `voidable` is not on `MarketOut`.

**Why.** Each field goes where a screen reads it. The trader's market page
shows the void banner from the public detail, and the admin market page reads
`MarketOut`. The void control is offered from the overview and the public
detail, as the settle control reads `settleable` there. A browse card shows no
reason, as it shows no countdown. "`dispute_window_ends_at` is on the public
detail and on every overview row, as the raw column" placed its field by the
same rule.

**Rejected.** *Every field everywhere*: fields with no reader.

**Notes.** A ruling of the PR that wrote ADR 0025, not of #221's thread.

---

### D-NEW — The void checks a stake after the replay's guard, not before

**Date:** 2026-10-20 · **Ticket:** #221 · **Status:** active

**Decision.** Under the book lock the void replays the trades, applies the
guard (`500 void_replay_mismatch`), and only then refuses an administrator who
appears in a trade (`403 admin_has_a_stake`).

**Why.** The stake check reads the replay's list of traders. If the replay
disagrees with the positions or the book, that list is not trusted either, and
the honest answer is the fault, not a `403` built on it.

**Rejected.** *The stake check first*: it could tell an administrator they
have a stake on the strength of a replay the next line refuses.

**Notes.** A ruling of the PR that wrote ADR 0025. A repeat runs neither.

---

### D-NEW — A card's price change is measured over seven days

**Date:** 2026-10-21 · **Ticket:** #446 · **Status:** active

**Decision.** The batch snapshot reports each market's prices as they stood
seven days before the request. One window, fixed in code
(`service/snapshot.py::_PRICE_CHANGE_WINDOW`), not a setting and not a request
parameter.

**Why.**
- *Classroom markets trade in bursts.* Trading clusters around the week's
  class meetings, and a market can sit untouched for days between them. Over
  24 hours most cards would compare today's price with an identical one from
  yesterday and show no change on nearly every market, most days. Seven days
  spans one cycle of class meetings, so the change a card shows is the one
  the last session made.
- *The landing page already promises it.* Its example card has read
  "YES · 7-day trend" and "up from 52.0% a week ago" since #64. Seven days
  makes the product say what the landing page says.
- *Decided once, on the server.* #446: the window is "decided once instead of
  each caller picking". A request parameter is each caller picking; a setting
  is a number the frontend's label has to match and cannot read.
- *The length costs nothing.* The baseline replays every trade up to the
  window's start whatever its length ("The prices a week ago are replayed from
  each pool's trades up to the window's start, in one statement for every
  market").

**Rejected.** 24 hours, for the reason above. A `window` query parameter. A
setting.

**Reversal trigger.** Markets that trade many times a day, where a week
smooths away everything a trader would want to see. The field's name carries
the window, so changing it is a visible contract change.

---

### D-NEW — The batch snapshot carries each outcome's price a week ago, not the change, and the single snapshot does not

**Date:** 2026-10-21 · **Ticket:** #446 · **Status:** active

**Decision.** Each item of `POST /ledger/markets/snapshots` gains
`prices_7d_ago`: a list in the shape and order of `prices`
(`outcome_id`, `position`, `price`), or null when the market has no baseline.
The card's change is `price` minus the entry with the same `outcome_id`. The
single snapshot and the `price` socket frame do not carry it.

**Why.**
- *The prior price, not the change.* The item already holds the current
  price, so the prior price is the one new fact. A change would restate the
  current price inside a difference, and would be wrong against any newer
  price the card later holds; the prior price stays right against all of
  them. The landing copy, "up from 52.0% a week ago", needs the prior price
  itself. Both prices come from `book_prices.priced`, the one quantizer, so
  their difference is exact at four places.
- *Every outcome.* The card shows a chip per outcome, binary and multi-outcome
  alike (up to ten). On a multi-outcome market no one outcome is "the" price,
  and on a binary one the second entry is the complement, which costs nothing
  and keeps one path.
- *Null for the whole list, not per outcome.* A baseline is a state of the
  whole book; a market has one or it has not.
- *The batch only.* The detail page draws price history rather than a delta,
  and the socket frame is published per trade by a relay that owns no data
  (ADR 0010). On the single snapshot it would be an extra read on every page
  open and every reconnect for a field nobody shows. The item stays a superset
  of the single snapshot's body, which is what the one render function needs.
  "The batch snapshot is `POST /ledger/markets/snapshots`, a read with a body"
  carries an amendment saying so.

**Rejected.**
- *The change itself*, for the reasons above.
- *One outcome's price* (the first, or the leader): blank chips on every
  multi-outcome card.
- *A map beside `snapshots`*, keyed by market id: it would keep the item
  exactly the single snapshot's body, but makes the frontend join two
  structures, and #432 shaped the item so per-market fields ride on it.
- *Carrying it on the single snapshot too*, for the cost above.

**Reversal trigger.** A second screen that wants the change for one market,
such as the detail page's header.

---

### D-NEW — A card's price change is measured from the last trade at or before seven days ago, and a market with none has no change

**Date:** 2026-10-21 · **Ticket:** #446 · **Status:** active

**Decision.** `prices_7d_ago` is the market's prices after its last trade
whose `created_at` is at or before `now - 7 days`. A market with no such trade
has none: null. A market whose last trade is older than that, and which has not
traded since, has `prices_7d_ago` equal to `prices`, a change of exactly zero.
`now` is Python's clock, read once in the controller.

**Why.**
- *A market's history starts at its first trade, not at its book's opening.*
  #446: a market with "less history than the window shows no delta rather than
  ... a change measured from the seed price". The opening point is the seed,
  uniform prices nobody chose (#445). So "less history than the window" means
  precisely: no trade at or before the window's start. That covers a market
  never traded and one first traded inside the window, even when its book was
  open, at its seed, at the window's start.
- *At or before*, like price history: a chart drawn from `from` starts at the
  level a trade stamped exactly `from` set, because `from` includes that point
  ("Price history returns raw points, one per trade, filtered by `from` and
  `to` with a carry-in point; nothing is bucketed").
- *Zero, not null, when nothing traded in the window.* The baseline is a price
  traders set, and it held all week, so the change is truly zero. The ticket's
  "misleading zero" is a zero with no baseline behind it, which null already
  prevents. Null here would make a quiet market look like a new one.
- *Python's clock.* Every entry's `created_at` is stamped from Python's clock
  by `posting.post` ("A movement's timestamp is fixed under its account locks,
  not trusted from the clock"), so the cutoff is measured on the clock that
  stamped the trades. Read once, in the controller ("One clock per request,
  read at the controller, Python's not the transaction's"); the read decides
  and writes nothing, so the transaction's clock buys nothing here. Replica
  skew of seconds moves a seven-day edge by seconds.

**Rejected.** The book's opening as a baseline. Null for a market that has not
moved. The database clock.

**Reversal trigger.** None expected.

---

### D-NEW — The prices a week ago are replayed from each pool's trades up to the window's start, in one statement for every market

**Date:** 2026-10-21 · **Ticket:** #446 · **Status:** active

**Decision.** `service/price_history.py::list_prices_at` reads every trade at
or before the cutoff on the requested markets' pool accounts in one statement:
`ledger_service.trade_entries_query()` joined to `market_books` on
`pool_account_id`, `market_id IN (...)`, `created_at <= cutoff`, ordered by
market, `created_at`, `id`. Each market's trades go through the price-history
replay (`core/price_history.py::price_history_of`), and the last point is
priced with `book_prices.priced`. The batch therefore sends two statements
however many markets it is asked about: #432's book read and this one. No
lock, no new index, no migration. A market whose trades cannot be replayed gets
null, and a warning in the log.

**Why.**
- *Nothing stores a past price.* A trade's `context` records its outcome,
  side, quantity and `state_version`, not the `q` it left. So the price at the
  cutoff needs every trade up to it, which is the replay price history already
  runs ("Price history is replayed from the trades on the pool's feed, and
  nothing stores it"). A `DISTINCT ON` the last trade before the cutoff would
  name the trade, not its price.
- *The time condition belongs in SQL here,* unlike the one "The `from`/`to`
  window and the carry-in point are applied in `core`, after the replay of
  every trade" rejects. That one dropped the trades before `from`; this keeps
  every trade from the first up to the cutoff. The pool's feed is in commit
  order, which is `state_version` order, so those trades are a prefix of it:
  the replay still starts at version 1, and its 1, 2, 3, ... check still holds.
- *An existing index serves it.* `ix_ledger_entries_account_feed`
  (`account_id`, `created_at DESC`, `id DESC`) answers
  `account_id = <pool> AND created_at <= cutoff` for each pool, reached from
  `market_books` by its primary key.
- *The two statements cannot disagree about the baseline.* The current prices
  come from the book read; the baseline from this one, a moment later, under
  READ COMMITTED. A trade committing between them may be visible to the second
  and not the first. But it is stamped no earlier than its own request's clock,
  so to fall at or before `now - 7 days` it would have to have been in flight
  for a week. The baseline is settled history, and no interleaving moves it.
  The card subtracts the baseline from the `prices` in the same item, which
  carry their own `state_version`.
- *A replay that cannot run costs one card its delta,* for the reason "A book
  the batch snapshot cannot price is left out and logged" gives. Two things
  stop it: trades whose versions skip or reorder (`ValueError`, which price
  history reports as `500 price_history_inconsistent`), and a trade on an
  outcome row the book no longer holds, which still passes
  `refuse_unpriceable` when two rows remain (`KeyError` or `IndexError`).
  That market's `prices` are unaffected.

**Rejected.**
- *Subtracting the window's trades from the current `q`.* It reads fewer rows,
  but a trade committing between the two statements would be subtracted from a
  `q` that never included it, so it would need one statement or a
  repeatable-read transaction. It would also need its own reading of a trade
  row.
- *Summing the signed quantities in SQL from `context`.* A second reading of a
  trade's context beside `trade_fields_of`, which price history and the trade
  feed share so that a trade means the same thing on every route.

**Notes.** The rows read are every trade older than a week on the requested
markets. Price history measured the replay at about 0.12 s for 10,000 trades.

**Reversal trigger.** A batch read measured as slow. Then the trade records the
`q` it leaves, and the baseline becomes one `DISTINCT ON` per pool.

---

### D-NEW — The browse narrows by a half-open window on when trading stops

**Date:** 2026-10-24 · **Ticket:** #442 · **Status:** active

**Decision.** `GET /public/markets` takes two optional query parameters,
`closes_from` and `closes_before`, each a time with an offset. A market
matches when `closes_from <= LEAST(close_time, closed_at) < closes_before`:
when trading stopped, or for a market still trading, when it will stop.
Either end may be sent alone. Both together must leave the window non-empty:
`closes_before` at or before `closes_from` is a `422` naming both. A time
with no offset is a `422`. A market with neither a `close_time` nor a
`closed_at` matches no window. The order, the groups and the cursor are
unchanged, and the window composes with `status` and `q`.

**Why.** "What closes this week" asks about trading, and a market an
administrator closed early ([2.3] #7) stopped trading at its `closed_at`
while its `close_time` stays in the future (ADR 0011, ADR 0014). A stopped
card shows "Closed", not that scheduled time, so a market closed three weeks
ago would otherwise answer for the week its schedule named, with nothing on
its card to say why. `LEAST(close_time, closed_at)` is the stopped group's
own sort key ("Stopped markets sort by when trading stopped"), so the window
and the order read one moment, and for a market still trading it is just
`close_time`, because Postgres's `LEAST` skips the NULL `closed_at`.
Half-open, so consecutive windows ("today", then "tomorrow") never share a
market and a client builds each from midnight to midnight without
subtracting a microsecond. The names say which edge is in: *from* includes
its instant, *before* does not. An empty window is refused rather than
answered with `[]`, which a client cannot tell from "nothing closes then". A
naive time is a different instant in every timezone, so it is refused rather
than read as UTC, as the ledger's price history does. No close time and no
close is not "closes some time after any start": publish refuses a market
without a close time, so only a direct write leaves one, and SQL's comparison
with NULL already excludes it.

**Rejected.** *The scheduled `close_time` alone*: it lists an early-closed
market under a week in which it did nothing, and disagrees with the order the
same list is in. *`closes_after`/`closes_before`*: "after" reads as
exclusive, and an inclusive start under that name is a trap. *Presets on the
server* (`closes=today`): "today" depends on the trader's timezone, which the
server does not know.

**Notes.** The cursor does not record the window, as it does not record `q`
or `status`: send the same window with `cursor`. The window removes rows and
leaves the order alone, and the first page's grouping still holds ("The
public browse pages by keyset, grouped by the first page's clock"), so a
keyset over the filtered rows pages exactly as one over all of them. No
index: the browse already reads every visible market to order it, and
`ix_markets_due_close` is partial on `status = 'open'` for the sweep. An
index on the stopped-at expression earns its place when the browse is
measured slow, and it would serve the order before it served this filter.

---

### D-NEW — The engine's precision context is public in `core/lmsr.py`, and the void's cut calls it rather than a copy

**Date:** 2026-10-25 · **Ticket:** #221 · **Status:** active

**Decision.** The void's core PR makes the context `core/lmsr.py`'s
`_engine_context` builds public, by renaming it or by adding a public wrapper,
and the void's cut runs inside it. The void's module holds no copy of the
precision or of the context.

**Why.** ADR 0025 specifies the cut, `r × (P + S) / R`, multiplied first and
then divided, at the pricing engine's precision, so that two builders cut the
same refund to the same tick. Two copies of that precision that drifted apart
would cut refunds differently from the engine that priced the trades, which is
a money bug. That passes the bar ADR 0012 sets for sharing code: every caller
needs identical behaviour, and a divergence would be a bug rather than a
design choice.

**Rejected.** *A copy of the context in the void's module*: the divergence
above, for a saving of one import.

**Notes.** Decided by Ihsan on 2026-10-25 (ADR 0025, "Decisions on the open
questions", question 7). `core/pricing.py`, `core/limit_orders.py` and
`core/price_history.py` already import the private name, so a rename touches
all three. Under "A refactor never shares a PR with a behaviour change" a
rename lands as its own refactor PR below the core PR; a public wrapper does
not need one. The core PR chooses.

---

### D-NEW — A voided market's `credits_traded` is left as it is

**Date:** 2026-10-25 · **Ticket:** #221 · **Status:** active

**Decision.** A void does not change the book's `credits_traded`. The batch
snapshot answers a voided market with its last `prices`, its `prices_7d_ago`
and its `credits_traded`, as it answers a settled one. No backend code changes
for it.

**Why.**
- *It records trades that happened.* A void refunds what the shares still held
  cost; it does not undo the trades, and every sale stands.
- *"Only ever rises" is documented.* `docs/api/ledger-service.md` promises it,
  and "`credits_traded` is a counter on the book, raised in `write_trade` under
  the book lock" makes it true by construction. A void writes no trade, so
  nothing touches it.
- *A settlement payout does not count either* ("A market's activity is the
  credits it has traded, buys and sells alike"). A refund is the same kind of
  movement: money that is not a trade.

**Rejected.** *Unwinding it by the refunds*: it would fall, against the
contract, and the void would have to write the book row it otherwise leaves
alone.

**Notes.** Decided by Ihsan on 2026-10-25 (ADR 0025, "Decisions on the open
questions"). Found when #474 merged, after ADR 0025 was written.

---

### D-NEW — A voided card shows "Voided", with no probability and no price change

**Date:** 2026-10-25 · **Ticket:** #221 · **Status:** active

**Decision.** On the browse list, a card for a `voided` market shows
"Voided" where a trading market shows its probability, and shows no price
change, though the batch snapshot still carries `prices` and `prices_7d_ago`
for it.

**Why.** A voided market has no answer, so its last probability and its
change describe nothing a trader can act on. It is the rule #429 sets for a
resolved market: a market past PENDING_RESOLUTION does not present a
tradeable-looking probability.

**Rejected.** *Leaving out a voided market's prices from the batch*: a
backend change for what is a display rule, and the single snapshot and the
price history would still answer them.

**Notes.** Decided by Ihsan on 2026-10-25 (ADR 0025, "Decisions on the open
questions"). For the [FE] sub-issue of #221; listed in the void screen's notes
in `docs/api/market-service.md`.
