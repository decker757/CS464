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
there: it goes back to one shape on the day an administrator reads the public
projection, because one payload serving both audiences needs a field rather than
a substitution. The trigger is a shared reader, not a new endpoint.
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

**Notes.** ADR 0011's rule is now expressed in five places and four of them
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

**Notes.** The reversal trigger: **if a path that decides or writes ever reads
this same projection, it needs the transaction's clock.** At that point the
`now` handed in stops being a display convenience and becomes the value an
action is judged against, and `open_for_trading()` is still how it is obtained.
`service/closing.py::open_for_trading` carries a pointer back to this entry, so
somebody reading the argument for the transaction clock finds out in place that
one caller deliberately does not follow it.

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

## Open — decided by nobody yet

Move these into the log above when they're settled.

- **`posting.post()` commits internally — settled for a caller with nothing to
  write afterward, still open for one that does.** [F-7] #96 (D-032) answered
  this for `books.ensure_open`: order every write through
  `session.begin_nested()` and call `post()` last, so its own commit lands
  everything together. That works whenever the call into `post()` is the
  caller's last write. [T-2] #22 is not guaranteed to be that shape — a trade's
  `state_version` bump and its position update on `MarketOutcome` would have to
  precede the call into `post()`, in the same transaction, under D-032's rule,
  never after it. Whether that ordering is workable for the trade path, or
  whether #22 needs to check its write against a quote taken *after* the trade
  executes — in which case `post()` gains a variant that stops short of commit,
  or the trade accepts the ledger write as its own boundary and builds
  compensation around it — is still #22's to decide.
- **Whether share quantities share money's scale of 4.** Nothing in the repo takes
  a position on fractional shares.
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
- **Service-to-service auth for ledger writes.** Deferred by ADR 0009 to #22.
  Currently avoided by making #62 public, but #22 is a write and will have to
  answer it.
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
- **How long the terms pull may block, given it runs inside a transaction
  holding row locks.** `service/market_terms.py::_TIMEOUT` is five seconds on
  every phase, which is exactly `httpx.DEFAULT_TIMEOUT_CONFIG` — so the budget
  is currently inherited in substance even though it is written out in the
  source, and no test can tell the line's deletion from its presence (D-030,
  corrected). The argument in D-030 is an argument for a *shorter* read
  timeout than a browse page would use: this call is made on the trade path
  with a database session and, once [T-2] #22 lands, row locks held. Against
  that, a first touch is the one request that does real work upstream, and too
  short a ceiling turns a slow-but-healthy market service into spurious 503s
  on a trader's first trade in a market. Deciding it needs a measurement of
  what a first touch actually costs, which nobody has taken.
