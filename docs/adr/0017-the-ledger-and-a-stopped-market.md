# ADR 0017: The ledger asks market_service whether a market is still trading, once per trade

- **Status:** Accepted
- **Date:** 2026-09-21
- **Affects:** [F-8] #109, [T-2] #22, [T-3] #23, [T-1] #21, [F-7] #96, [5.4] #20, [3.4] #12, [T-7] #27
- **Implemented in:** nothing yet. This record precedes [F-8] #109, which is the first code that has to obey it.

## Context

[T-2] #22 requires a trade to be "rejected with a clear error code when …
market not open". Nothing in the ledger can answer that question, and three
decisions already taken are the reason.

**ADR 0011 made the clock the authority and the status column a
materialisation.** "A market stops accepting trades the instant `close_time`
passes. That is a fact about two values." Both values live in
`market.markets`, which `ledger_svc` holds no grant on.

**ADR 0014 made `close_time` insufficient on its own.** An early close writes
`status` and `closed_at` and deliberately leaves `close_time` in the future,
because it is "a term the administrator published and traders read" and
because a `closed_at` earlier than `close_time` is the only signal that a
market was stopped by hand.

**The book carries neither.** `ledger.market_books` holds `liquidity_b`,
`seed_subsidy`, `pool_account_id`, `state_version`, `state_changed_at` and
`opened_at`. It has no status and no closing time, and ADR 0005's snapshot
argument is why: what crosses at handoff is the *terms*, which cannot change
once published. A market's *state* changes constantly and is not a term.

So the ledger prices, and could write, a trade against a market that stopped
trading a week ago. `service/preview.py` says so in its own docstring, and
[T-1] #21 shipped with the hole deliberately open, naming this record's
question as [T-2] #22's to answer.

## Decision

### The trade path reads `GET /public/markets/{id}` and gates on its status

One call, on every trade that is not a replay, through the client
`service/market_terms.py` already holds, forwarding the caller's own token
exactly as the cold path does — the rule set down in "Public market reads
require a valid token, any role" and paid for in "The preview is a market's
first toucher, and the cold path is self-extinguishing".

One call once the market has a book. The trade that creates the book makes
two: the gate reads the status and drops the terms it came with, and
`books.ensure_open` then fetches the same market again for the terms it
snapshots. That is the price of the gate returning nothing rather than
`MarketTerms` — handing fresh terms to the trade path is one refactor away from
pricing off a wire `liquidity_b` — and it is paid once per market, ever.
`test_a_market_s_first_trade_asks_twice_and_every_later_one_once` pins both
counts.

**That endpoint's `status` is not the raw column.** ADR 0011's amendment
derives it for the trader-facing projections, and `displayed_status` "only
ever turns OPEN into CLOSED" — so one field answers both kinds of close: the
clock's, before the sweep has written it, and an administrator's under [2.3]
#7, which the derivation never reopens. PENDING_RESOLUTION and APPROVED pass
through as themselves and are refused by the same comparison. There is exactly
one predicate in this system, and this reads it rather than restating it,
which is ADR 0011's own argument applied across a service boundary.

### An idempotent replay is answered before the gate, and makes no HTTP call

The order on the trade path is fixed here rather than left to [T-2] #22,
because getting it wrong is silent and expensive:

1. **Look the idempotency key up, unlocked.** A hit replays the stored
   transaction and returns. No HTTP call, no status check, no lock.
2. **Otherwise, the status check above.**
3. **Then the book lock and the locked path**, which re-checks the key under
   the lock exactly as `posting.post` already does.

The failure this prevents: a trade that executed, whose response was lost, and
whose client retried after the market closed. With the gate first, the retry
is refused `409 market_closed` and the trader is told a trade failed that in
fact committed and charged them. The trade already happened; the only question
a retry asks is what its answer was, and a market closing afterwards does not
change that answer.

**The unlocked lookup does not reopen ADR 0015's rejected alternative**, and
the difference is worth stating because it looks identical. That record
refused a pre-lock idempotency lookup *inside* `posting.post` on the grounds
that "the first lookup's answer is never trusted — it cannot be, that is the
bug — so it is a read that exists to be ignored." This one is trusted, in one
direction only. A hit names a committed row — READ COMMITTED shows nothing
else — and `ledger.entries` is append-only, so the transaction it names cannot
change afterwards and no lock would make the answer better. A miss is trusted
for nothing at all: it decides only that this request does not skip the gate,
and the decision that actually writes is still made under the lock, from a
lookup issued after the lock statement. ADR 0015's rule is about reads that
decide a write. A replay writes nothing.

**What this closes, and what it does not.** It closes the window for an
original that has *committed*. A retry arriving while its original is still in
flight finds no key, falls through to the gate, and can still be refused for a
trade that then commits. That requires the original to be blocked for as long
as the client waits before retrying — seconds, against a trade that holds the
book lock for milliseconds — and it reconciles on the next balance or history
read, both of which are derived from the entries rather than reported from
anywhere the refusal reached. Named here so it is not found later and mistaken
for a bug.

### The check is issued before the book lock, and no lock can make it fresher

The trade locks `market_books` first and account rows second, per "Lock order:
book row before account rows". This check goes **before** that lock.

ADR 0015's rule — a check that must hold under the lock is issued after the
lock statement — does not reach here, and its own wording says why: it governs
"anything whose value **the lock exists to hold still**". This lock holds `q`
and `state_version` still. It holds nothing still in another service's
database. market_service may close the market one millisecond after answering,
whether or not the ledger is holding a row lock, so moving the hop under the
lock closes no window at all.

What it would cost is exact, and "Upstream failures map to 503, 404 and 401,
and the timeout is explicit" already wrote the sentence about the cold path:
"a market service that accepts the connection and then stops responding holds
a ledger request, its database session and its row locks open for as long as
the socket stays alive". On the warm path that is every trade in a market
serialised behind a remote round trip, and a hung dependency holding the
hottest row in the system for up to the terms timeout per queued trade. The
whole cost of a convoy, for none of the benefit.

### The window between the answer and the commit is accepted, and it is not the window ADR 0011 refused

A market can close between market_service's answer and the ledger's commit.
The interval is half a round trip, plus the wait for the book lock, plus one
indexed read, the cost function, the inserts and the commit. Uncontended that
is single-digit milliseconds; under contention the lock wait dominates and is
the term worth bounding. Nobody has measured it — [5.4] #20 is where that
happens.

Accepted, on ADR 0011's own test. That record's stated purchase was the
elimination of a class of bug: "the sweep interval is a correctness parameter:
every second of it is a second of trading on a closed market, and every outage
extends it." Neither half survives here. The sweep is still irrelevant to
whether a trade executes — switching `CLOSE_SWEEP_ENABLED` off still cannot
let one through, because this derives. And an outage does not extend the
window, it closes it: an unreachable market_service refuses the trade rather
than letting it run on a stale answer.

The severity is the other half. ADR 0011's harm is a "ten-second hole" in the
`close_time < resolution_time` rule, which exists so that nobody "can bet on a
result they already know". `service/validation.py` requires `resolution_time`
strictly later than `close_time`, so a trade landing milliseconds past close is
not near a knowable result. This is the same shape of accepted bound as ADR
0010's crash window between a trade's commit and its price publish: named,
bounded by one request, and cheaper than the machinery that would close it.

### The clock stays market_service's, and that trigger is declined on purpose

The decision recorded as "One clock per request, read at the controller,
Python's not the transaction's" states its own reversal condition: if a path
that decides or writes ever reads this same projection, it needs the
transaction's clock. This decision is that path and fires that trigger. It is
declined, and declining it is a decision rather than an oversight.

The argument it defers to is `service/closing.py`'s: two replicas drifting a
few seconds apart would disagree about whether a market was due, so a path that
decides should read `func.now()` from its own transaction. That is correct and
it cannot be bought here. The ledger's transaction cannot supply the clock for a
comparison performed in another service's process, so "the transaction's clock"
would mean market_service's transaction clock — still remote, still stale by the
round trip, and costing either a second endpoint or a parameter on the hottest
read in that service. That entry already rejected an extra round trip for the
display case; the deciding case does not make it cheaper.

So the skew is folded into the window above rather than eliminated. If replica
skew ever exceeds the round trip it is an operational fault with an operational
fix, and this record is not where it gets papered over.

### The failure is closed, and it reuses 503

| upstream | ledger raises | status |
| --- | --- | --- |
| connect error, timeout, 5xx, unparseable 200 | `MarketTermsUnavailable` | 503 `market_terms_unavailable` |
| 200, derived `status` is not `open` | `MarketClosed` | 409 `market_closed` |
| 401 | `NotAuthenticated` | 401 `invalid_token` |
| 404 on a market that already has a book | `MarketTermsUnavailable` | 503 |

`MarketTermsUnavailable` is reused rather than joined by a
`market_status_unavailable` beside it: same cause, same remedy, and the
principle behind the existing three codes is that a caller can act differently
on each. Nothing acts differently on these two. Its message widens from "terms"
to what it now means.

`MarketClosed` is new and is not optional. `MarketNotPublished` is explicitly
not this error — "a CLOSED, PENDING_RESOLUTION or APPROVED market is published
and does get a book" — and [T-2] #22's criterion asks for a code. 409 for
`InsufficientFunds`'s reason: the request is well formed and the state refuses
it. Spelled `market_closed` to match market_service's own code, so [FE] #49
renders it with the handler it already has.

The last row is the one that looks wrong and is not. A market holding a book
existed and was published, and nothing in this repository deletes a market, so
a 404 at that point is market_service answering incorrectly rather than a
market that is gone. Answering 404 would tell a trader a market they are
looking at does not exist. 503 says there is no usable answer, and fails
closed. On the cold path the existing 404 to `MarketNotFound` mapping is
untouched.

### The preview does not check, and that asymmetry is the point

[T-1] #21's preview keeps its behaviour: no status check, no hop on a warm
market. A preview decides nothing and writes nothing, which is why it takes no
lock, and ADR 0005 budgets the hot path explicitly — "one hop per quote is a
budget, two is a latency problem in an interaction that fires on every
keystroke". A preview fires on every keystroke; a trade fires once. The
frontend gates the button on the derived status [BE][X] #62 already serves; the
ledger gates the money.

### One client, and the terms rules move to the caller that needs them

`service/market_terms.py::fetch` gains `status` on `MarketTerms` rather than a
sibling function. The argument in "The terms client lives in `service/`, not
`core/`" holds: a second client is a second timeout, a second set of headers,
and a second place for token forwarding to drift.

**`fetch` still does not gate on what it carries.**
`test_the_close_time_is_not_what_decides_anything_here` already pins that, and
it becomes load-bearing rather than incidental: settlement reads `q` from a
book belonging to a market that stopped trading weeks earlier, and the realtime
snapshot serves a closed market's prices. The client carries the status; the
trade path decides on it.

`_parse`'s terms-only rules move up into `books.ensure_open`. Today it refuses
a null `liquidity_b` or `seed_subsidy` and, through `_refuse_unpriceable`, an
outcome list that could be stored but never priced. Those are the right rules
for *writing a book* and the wrong ones for asking whether a market is open: a
market_service that began returning a null `b` would start refusing trades on
books that have priced correctly for weeks, against a value snapshotted at
first touch and never re-read. What stays in `_parse` is what any caller
structurally needs — an object, a matching id, a parseable status — and the
arguments on the rules that move survive the move unchanged, because they were
always arguments about writing.

### `settled` is refused by the comparison and not by the path

`status != "open"` needs no list, so a member added to `MarketStatus` on the
day settlement lands is refused here without anyone remembering to add it.
That is true of the comparison and it is not the whole path.

Whether a settled market reaches this comparison at all depends on
market_service's `PublicMarketStatus`, the hand-written allowlist
`get_published`'s `_visible()` filters on. Add SETTLED to `MarketStatus` and
not to that allowlist, and the public detail endpoint answers `404` for a
settled market. This gate then takes its 404 branch, finds a book — a settled
market has certainly been traded — and raises `MarketTermsUnavailable`
**503**, not `MarketClosed` **409**.

It still fails closed, so no trade goes through and no money moves. But 503
means "the dependency is unwell, try again shortly" about a market that will
never reopen, so a client that retries on 503 retries forever. The remembering
did not disappear; it moved to the other service, where
`market_service/model/entities.py`'s comment on `PUBLIC_STATUSES` already
warns about it for its own reasons.

Whoever lands [3.4] #12 adds SETTLED to `PublicMarketStatus` in the same
commit as `MarketStatus`, and this entry is the second place that says so.

## Consequences

**market_service is now a runtime and availability dependency of every trade.**
"Market terms reach the ledger by lazy pull on first touch" accepted that
dependency for a market's first touch, and "The preview is a market's first
toucher" recorded where the price was charged. This is where it is charged
again, on every trade rather than once per market, and it is the larger half. A
market_service outage stops trading platform-wide. That is the correct
direction — the alternative is trading on an unknown status — but it is a new
sentence in this system's availability story.

**Token forwarding becomes load-bearing on the hot path.** The note on "Public
market reads require a valid token, any role" warned that "a pull with no
caller behind it — a retry, a sweep, any background path — has no token to
forward", and that sentence described a once-per-market read. It now describes
every trade. [3.4] #12 does not add such a caller — settlement runs on an
APPROVED market and never asks this question — but [T-7] #27's auto-execution
and cascade would, and ADR 0009's deferred service-auth question stops being
academic on that day.

**A new caller on a public endpoint written for a display read.** `browse` and
`get_published` were sized for a trader loading a page. They now take one
request per trade, and [5.4] #20's load test measures market_service whether or
not that was its subject.

**The preview and the trade deliberately disagree about a closed market.** A
preview returns a number and the trade that follows it returns 409. That is the
honest shape — the preview is arithmetic, the trade is a decision — and
`docs/api/ledger-service.md` has to say so in one sentence so it does not read
as a bug.

**Nothing changes in market_service.** No new route, no new field, no
migration, no grant. That is most of what makes this affordable, and it is
worth stating because every alternative below changes something there.

## Alternatives rejected

**market_service pushes a close to the ledger**, at the sweep and at the early
close. Rejected by ADR 0006's own sentence: "Committing the market change and
then producing to a topic is a dual write. A crash between them loses the event
exactly as a failed HTTP call would." Here the loss fails *open*: the sweep
commits CLOSED, the call fails, and the market trades forever with nothing
recording that it should not. The fix is an outbox and a relay, which is the
machinery ADR 0006 exists to avoid while one database serves everyone, and
which "Market terms reach the ledger by lazy pull on first touch" already
refused for this same boundary. It also needs a ledger write route — reopening
ADR 0009's deferred question with a caller that holds no credential — and on
the early-close path it would put an HTTP call inside `close_early`'s
transaction, making an oversight action fail when the ledger is down. ADR 0006
rejected exactly that coupling for the audit service.

**Snapshotting `close_time` into `market_books` and comparing locally.** Free,
local, and clock-correct — it would compare against the trade's own
`func.now()`, which is the clock `service/closing.py` asks for. Rejected
because it answers half the predicate, and the missing half is the one an
administrator controls: ADR 0014 leaves `close_time` in the future on an early
close on purpose, so a market stopped by hand would keep accepting trades until
its original closing time. That is the failure [2.3] #7 exists to prevent,
reintroduced one service downstream of it.

**The snapshot for the scheduled close, the hop for the early close only.**
Rejected because the ledger cannot know when to make the hop. An early close is
defined by leaving `close_time` untouched, so nothing local distinguishes a
market that is open from one stopped by hand with days left on the clock. The
local check can therefore only ever *refuse*, never accept, and it refuses
exactly the trades the frontend already hides. It is this decision plus a
column and a migration that change no acceptance.

**Caching "open" for a few seconds.** Rejected in ADR 0011's words: the TTL
becomes a correctness parameter and an outage silently widens it, which is the
same objection that killed shortening the sweep interval.

**Redis, or a subscription on the realtime relay.** ADR 0010 runs Redis with no
persistence — "the container can be deleted and recreated at any time and
nothing is lost" — and nothing that moves money may read it. ADR 0011 already
worked through this for the timer and the four reasons have not changed.

**Gating in the trading composite instead.** ADR 0005 has it orchestrating, so
it could check before calling the ledger. Rejected because ADR 0010 left the
ledger owning the write path, so the gate would sit one hop further from the
transaction that commits the trade — the same hop, plus another, and a longer
window. "The preview endpoint lives on the ledger as a read" kept preview and
trade on one code path for the same reason.

**A one-way "closed" latch on the book — deferred, not rejected.** The status
machine has no reopen: ADR 0008 publishes one way, ADR 0014 states there is no
reopen, and `reject-outcome` returns a market to CLOSED rather than to OPEN. So
an observed not-open is true forever and could be written down and never asked
again. It would bound the hop to markets that are genuinely trading, and would
keep refusing trades during an outage that follows a close. Not taken now: it
needs a column, a hand-applied migration against `cs464`, and a commit on a
refused request, and nothing has measured that the hop is expensive. It is the
first move if [5.4] #20 says otherwise.

---

> **This reverses on the day the hop is the trade path's binding constraint,
> and the test for that is [5.4] #20's measurement rather than an opinion about
> network calls.** If market_service's p99 on `GET /public/markets/{id}`
> becomes a material share of the trade endpoint's budget under load, the
> answer is the closed latch above — cheap, one-way, and safe because the
> status machine has no reopen — and only after that a push with a real outbox,
> which is ADR 0006's machinery bought deliberately rather than arrived at by
> drift.
>
> It reverses for a second reason that is not about latency: the day a trade
> arrives with no caller token to forward. Nothing planned does. [3.4] #12 does
> not, because settlement runs on an APPROVED market and never asks this
> question; [T-7] #27's auto-execution and cascade is the one on the board that
> would. At that point this decision is not merely slower, it is
> unimplementable as written, and ADR 0009's deferred service-auth question is
> the prerequisite rather than an adjacent concern.
>
> What does **not** reverse it is a second reader of the public detail
> endpoint. Another caller gating on that derived status is this rule, not an
> exception to it — the whole reason the check reads that projection is that
> there is one predicate and everybody asks it.
