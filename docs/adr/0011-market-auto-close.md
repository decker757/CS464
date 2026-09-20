# ADR 0011: The clock closes a market, and a sweep only writes it down

- **Status:** Accepted
- **Date:** 2026-09-16
- **Affects:** [F-4] #44, [2.3] #7, [3.1] #9, [2.1] #5, [BE][X] #62, [T-2] #22
- **Implemented in:** `backend/market_service/service/closing.py`, `service/sweeper.py`

## Context

[F-4] #44 asks for an automatic `OPEN → CLOSED` transition when a market's
closing time passes, and suggests a scheduler or job. Three things about this
repository shaped the answer.

**Everything downstream wants a status, not a comparison.** [2.1] #5 counts
markets per status, [3.1] #9 may only propose an outcome for a market in
CLOSED, and [3.4] #12 settles from there. A `CASE WHEN status = 'open' AND
close_time <= now()` restated in each of them is three places to get it wrong.

**Nothing downstream can tolerate the status being late.** [T-2] #22 rejects a
trade when the "market not open", and `service/validation.py` already records
why that matters in this domain: `close_time` must precede `resolution_time`
"or someone can bet on a result they already know". A market that stays
tradeable for ten seconds past its closing time is a market where that rule
has a ten-second hole in it.

**The question was raised as a scaling one.** Polling a database that is
already serving a three-second autosave felt like adding load to a hot path,
and Redis was the suspected alternative. That framing turned out to be the
less important half, but it deserved an answer and gets one below.

## Decision

### The predicate is the authority; the status is a materialisation

A market stops accepting trades the instant `close_time` passes. That is a fact
about two values. `service/closing.py` states it once, in two forms — one over
an entity in memory, one as a SQL `WHERE` clause — and everything that decides
whether a market can be traded calls one of them. Nothing reads `status` alone.

The `CLOSED` status is then written a few seconds later by a background sweep,
for the three readers above that want a value to group, count and gate on.

This is ADR 0009's shape, one service over. The ledger has no balance column
because `SUM(amount)` makes the displayed balance correct by construction
rather than by vigilance; three acceptance criteria depend on that and none of
them needs a job to have run. `close_time` is the sum here.

**What this buys is the elimination of a whole class of bug.** With the order
the other way round, the sweep interval is a correctness parameter: every
second of it is a second of trading on a closed market, and every outage
extends it. With this order, the sweeper falling behind — or being switched off
entirely with `CLOSE_SWEEP_ENABLED` — makes an admin dashboard's counts stale
and cannot let a single trade through. That is why ten seconds is a comfortable
default rather than a nervous one.

### The sweep is a partial-index scan inside the market service

An `asyncio` task in the service's lifespan, because `market_svc` is the only
role that may write `market.markets.status` and a separate container would cost
a Dockerfile, a compose entry and a CI leg to move one `UPDATE` across a
boundary.

`ix_markets_due_close` is partial on `status = 'open'` and keyed on
`close_time`, so the sweep is an ordered index scan that stops at the batch
ceiling. Measured on 20,000 settled and 2,200 open markets with 200 due: five
index buffers and 0.05 ms, against 36 buffers and 0.17 ms without the index.
The index holds 72 kB and does not grow as settled markets accumulate.

Against this application's own traffic, the scaling concern inverts: one
administrator with the create form open writes **1,200 autosave transactions an
hour**, each deleting and re-inserting child rows. A sweep every ten seconds is
360 read-only probes in the same hour. The autosave is the load; the sweep is
not measurable beside it.

Two replicas need no coordination. `SELECT ... FOR UPDATE SKIP LOCKED` hands
them disjoint rows and the outer `UPDATE` re-checks the status regardless, so a
market is closed exactly once and appears in exactly one caller's returned
list — which is what makes that list safe to act on when [2.3] #7 and a future
broadcast need it.

### No audit entry for an automatic close

`audit.admin_actions` requires an actor id, username and role, all NOT NULL,
and every row in it today names a person. A clock is not a person.

The decision that this market would close at this time was already logged, with
its actor, in the `market.published` entry: `_terms_snapshot` records
`close_time`. The clock arriving is the consequence of that decision, not a new
one — and CLAUDE.md's standing rule for this log is to record the decision and
never the keystrokes. `closed_at` on the row is the record that it happened.

[2.3] #7 is the opposite case and keeps its entry: closing a market early is a
choice a named administrator makes, with a reason, at a time of their choosing.

### No broadcast in this ticket

Publishing a `market_status` frame would give the market service a Redis
dependency it does not have, and add a frame type to a contract
(`docs/api/realtime-service.md`) whose `PriceEvent` is `extra="forbid"`.

There is nothing to go stale yet: [F-3] #43 is unstarted, so no price exists,
and a trade against a just-closed market is refused by the predicate whatever a
screen is showing. The lag is cosmetic and the next read clears it, which is
the same reconciliation path ADR 0010 already relies on. The natural home is
the snapshot endpoint, with [X-4] #37.

## Consequences

**`status` alone is no longer a correct browse filter.** [BE][X] #62 and [2.1]
#5 filter on `closing.open_for_trading()`, which is that member AND a close
time still in the future.

**This amends ADR 0008's "the browse query is `status == MarketStatus.OPEN`".**
That was true when it was written and is now half of the predicate. ADR 0008 is
otherwise untouched — its subject is how a market becomes OPEN, which this
record does not disturb. `model/entities.py` carried the same sentence and has
been corrected in place rather than left standing, because it is a comment
rather than a decision record.

**A client sees `"status": "open"` on a market that has closed, for a few
seconds.** `docs/api/market-service.md` states the rule for the frontend: a
market is tradeable when the status is `open` **and** `close_time` is in the
future. A countdown hitting zero should flip the UI immediately; the backend
already agrees with it.

> **Amended by [BE][X] #62.** A trader never sees that window, and this
> paragraph is the claim that narrows. The decision above is untouched: the
> clock still closes a market, the sweep still only writes it down, and
> `service/closing.py` is still the one place either rule is queried from —
> `is_open_for_trading()` for an entity, `open_for_trading()` for a `WHERE`
> clause. What changed is that this record had exactly one kind of reader
> when it was written, and now has two.
>
> Every route in the service was admin-only at the time, and an administrator
> genuinely wants the column. The gap between `close_time` and `closed_at` is
> operational information for them — it says whether the sweeper is running,
> and [2.1] #5's counts are read against it. Handing them a derived value
> would hide the one symptom that an outage of the sweep produces.
>
> #62 adds a public reader with no such interest. A trader wants to know
> whether they can trade, and [X-1] #34's "open markets are clearly
> distinguishable from closed" cannot hold if the payload says `open` for a
> market that stopped four seconds ago — the distinction would exist only in
> whichever clients remembered to recompute it, which is the "three places to
> get it wrong" this record's own decision section rejects.
>
> So the split is by audience, not by rule. `MarketOut` keeps reporting the
> raw column, for the administrator. `PublicMarketOut` and
> `PublicMarketSummaryOut` derive it from the same predicate, for the trader,
> and the public browse filters and counts derive it too, so the list and the
> detail cannot disagree. The derivation only ever makes a market *less*
> tradeable: an early close ([2.3] #7) leaves `close_time` in the future on
> purpose, and a market already CLOSED is never reopened by it.
>
> **The predicate itself now lives in `core/closing.py` (D-023), not in
> `service/closing.py`.** `model/schemas.py` needed the same two-condition
> check to derive `status`, and cannot import `service/closing.py` — nothing
> below `service` reaches up under this repository's layering. Restating the
> two conditions in `model/` was the first cut of #62 and was exactly the
> "three places to get it wrong" named two paragraphs up; the fix was to move
> the shared piece one layer down rather than write a second copy of it.
> `service/closing.py::is_open_for_trading` and `open_for_trading()` are
> unchanged as the entity and SQL-clause wrappers every other caller imports.
>
> **This reverses on the day an administrator reads the public projection**,
> because at that point one payload is serving both audiences again and the
> answer has to be a field rather than a substitution — a derived `status`
> beside the column, or a `tradeable` boolean, named so that neither reader
> has to guess which they are holding. The trigger is a shared reader, not a
> new endpoint: another trader-facing route that derives is this rule, not an
> exception to it. Nothing planned adds one. `docs/api/market-service.md`
> carries the same statement, so the next reader finds it from either
> direction.

**`closed_at` and `close_time` are different columns and mean different
things.** `close_time` is when trading stopped. `closed_at` is when the sweep
wrote that down. A gap is normal, and after an outage it can be large.

**The service now has background work, and one more thing to shut down
cleanly.** The task is cancelled and awaited before the engine is disposed. It
swallows every `Exception` and logs, because a task that ended on a dropped
connection is a service in which no market ever closes again and nothing says
so. `CancelledError` derives from `BaseException`, so shutdown still works.

## Alternatives rejected

**Redis as the timer** — a TTL per market, or a sorted set polled by score.
Rejected on four counts, the first being decisive. ADR 0010 runs Redis with no
persistence at all: "the container can be deleted and recreated at any time and
nothing is lost", which is precisely the wrong property for a schedule.
`close_time` is already durable and indexed in Postgres, so a Redis copy is a
second and weaker source of truth needing to be kept in step at publish, at
[2.3] #7's early close and at every edit. Keyspace expiry notifications are
fire-and-forget — nothing connected, nothing replays, the close never happens.
Redis expiry is itself lazy and probabilistic, so the precision is not bought
either. And the only service that subscribes to Redis must not have a database
(ADR 0010), so it could not write the status it was being told about; routing
it back over HTTP would reopen the "how does a service prove it is a service"
question that ADR 0009 parks for [T-2] #22. A sorted set polled by score is
also polling, against a store with fewer guarantees.

Worth reopening if this platform ever needs millions of unpredictable timers at
sub-second precision. It has one `close_time` per open market.

**`pg_cron` or an external cron container.** A cron job cannot write an audit
entry with an actor, cannot publish to a bus, and is another moving part to
deploy. `pg_cron` is an extension, so it would belong in `sql/` — where, per
CLAUDE.md, a new file never runs on the database that already exists.

**Closing lazily, on read, like ADR 0009's signup grant.** Genuinely close, and
rejected on one asymmetry. A balance is only ever wrong if somebody reads it,
so minting on read closes the window completely. A closure has observers who
are not reading that row: [2.1] #5's counts would be wrong until somebody
happened to browse, and a market nobody opens would never close at all.

**Making the sweep authoritative and shortening the interval.** The obvious
reading of the ticket. Rejected because no interval makes the window zero, and
the interval then becomes a correctness parameter that an outage silently
widens — for no gain, since deriving the answer costs a comparison.
