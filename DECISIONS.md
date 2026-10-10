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
### D-0NN — <short title>

**Date:** YYYY-MM-DD · **Ticket:** #NN · **Status:** active

**Decision.** One or two sentences. What was chosen.

**Why.** The actual reason, including the constraint that forced it.

**Rejected.** What else was considered and the cost that ruled it out.

**Notes.** Anything a future reader would otherwise have to rediscover.
Omit if there's nothing.
```

Status is `active`, `superseded by D-0NN`, or `graduated to ADR NNNN`.

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
then locks the owner's row in `auth.users` (`_lock_user`), then the token's
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

**Date:** 2026-10-09 · **Ticket:** #12 · **Status:** active

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
`unrealized_pnl` as `string` (`frontend/src/api/ledgerApi.ts:53-59`) and
formats them unguarded (`frontend/src/pages/PortfolioPage.tsx:145-147`). A
settled row hands those formatters `null`. The page needs a null guard before
this stack merges into `dev`, whether or not [FE][3.4] #216's settled
rendering is ready by then.

---

### D-NEW — `_trade_fields_of` keeps its name, and its docstring says it covers settlement

**Date:** 2026-10-10 · **Ticket:** #12 · **Status:** active

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
  do. Whether it moves into `shared/testing.py` is #<N>.
