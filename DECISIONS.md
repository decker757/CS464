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

**Reversal trigger.** This lapses if publishing stops being hot. If the only
remaining caller runs once per market, or rarer, the frequency argument above
inverts and `market_terms`'s per-call client is the cheaper shape again — one
fewer thing held for the life of the process, and the transport fixed where the
suite wants it. Nothing planned moves it that way; [T-2] #22 moves it the other.

**Notes.** The ledger now holds two outbound clients with opposite lifetimes and
opposite reasons, and the reasons are symmetrical rather than inconsistent: both
are decided by how often the call is made. Anyone adding a third should answer
the same question before copying either.

**Note, 2026-09-23 (review of #110).** The frequency that justified
`market_terms`'s per-call client stopped holding in the same branch that
recorded this entry. `market_status.ensure_trading` (ADR 0017) calls `fetch`
on every trade that is not a replay, so the terms client is now hot too, and
by this entry's own test it should be held for the process. It is not yet:
`market_terms.py`'s comment says so and names one lifespan-held
`httpx.AsyncClient` as the follow-up, #114, which is its own ticket because it moves
the seam two test modules drive through.

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

Whether the caller had pending work is recorded on entry to `post`, not asked at
each branch. The second branch is reached after the SAVEPOINT has flushed the
caller's writes and rolled them back, which expires them, so the session looks
clean there whatever the caller was holding. Nothing is committed on that
branch, but the caller would still get somebody else's transaction back as
though its own had landed, holding expired entities. No trade reaches it today
— the book lock queues two trades on one key before either gets to `post` —
and `test_a_replay_found_by_the_insert_race_is_refused_too` drives it by hiding
the first lookup.

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

**Date:** 2026-09-22 · **Ticket:** #22 · **Status:** active

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

## Open — decided by nobody yet

Move these into the log above when they're settled.

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
  [T-2] #22's trade path rolls back after its replay lookup misses, before
  `market_status.ensure_trading` calls the same client on every non-replay
  trade. #22 takes the book row lock only after both calls have returned, so
  it holds no row locks across either — a slow market_service costs a trade
  its own latency, not a connection out of a pool of ten, and it serialises
  nothing behind it. #115 moves the gate's release into the gate itself, for
  every caller, and #114 holds the connection-reuse half. What is still open
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
