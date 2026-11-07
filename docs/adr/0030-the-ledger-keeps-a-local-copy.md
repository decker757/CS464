# ADR 0030: The ledger keeps a local copy of who may trade and what may be traded, and a stale copy refuses

- **Status:** Accepted, 2026-11-06. Amends ADR 0017, ADR 0022 and ADR 0021.
- **Date:** 2026-11-06
- **Decided by:** Ernest, on 2026-11-05, approving #640's design: the copy,
  its one-second refresh and its five-second refusal. Amended on #656,
  2026-11-07, after its first e2e run: the miss floor does not apply to a
  market with a book.
- **Affects:** #640, [5.4] #20, #599, [4.2] #14, [2.3] #7, [T-2] #22,
  [T-6] #26, ADR 0006, ADR 0010, ADR 0011, ADR 0014, ADR 0015, ADR 0028
- **Implemented in:** auth's `GET /internal/users/suspended` and
  market_service's `GET /internal/markets/published` (PR #650);
  `backend/ledger_service/service/local_copy.py`, `service/books.py`
  (`clock_after_lock`, `refuse_if_closed_under_lock`), `service/trading.py`,
  `service/orders.py` and `main.py`, on `640-perf-4-local-copy`.

## Context

Since ADR 0017 and ADR 0022, every trade that is not a replay makes two HTTP
calls before the book lock: `GET /auth/me` with the trader's token, then
`GET /public/markets/{id}` with the same token. Each is a JWT check, a
database read and a response model in another service.

#20's production runs on 2026-11-04 (`docs/load-test.md`) put 50 traders on
two 1-vCPU app servers for five minutes. Run A, one market, committed 126
trades at a p95 of 5.45 s. Run B, five markets, committed 738 at 4.52 s, and
0.8% of its trades ended in an error. Both app servers sat at 100% CPU, the
database at 45% (ADR 0029). auth and market_service run on the same servers
as the ledger, so the two calls spend the same CPU the trade is waiting for.
Run B's 0.8% errors (`docs/load-test.md`, Run B row), six of 744 trades,
were three `503 auth_unavailable` and three `503 market_terms_unavailable`:
the two per-trade calls failing under load, whether by timeout or refused
connection. The breakdown is from the saved k6 summary of that run
(`loadtest/results/20261104T133155Z.txt`, not committed). Price limits
(ADR 0029) remove the retry storm. They do not remove the two calls, which
every trade still pays.

ADR 0022's reversal note saw this coming and ruled out the obvious fix:

> the cheap answer for auth is not a cache, which would make the TTL a
> correctness parameter, but a shorter path to the same row.

ADR 0017 rejected "caching 'open' for a few seconds" in ADR 0011's words: the
TTL becomes a correctness parameter, and an outage widens it silently. Both
objections are about a cache that answers yes when it should not. This record
keeps a copy that cannot do that.

## Decision

### The ledger keeps a copy, and a trade calls nobody

Two background tasks per ledger replica refresh, about every second
(`LOCAL_COPY_REFRESH_SECONDS`, default 1.0):

- every suspended user id, from auth's `GET /internal/users/suspended`;
- every published market's **stored** `status` and `close_time`, from
  market_service's `GET /internal/markets/published`. Every published market,
  not only the open ones, so a closed market is still `409 market_closed` and
  only an unknown one is `404`.

Each refresh builds a frozen snapshot and replaces the old one whole. A trade
reads the snapshot in memory: `refuse_if_suspended`, then `ensure_trading`,
in ADR 0022's and ADR 0017's order, after the replay lookup and before the
book lock. A trade on a market that already has a book makes **no HTTP
call**. The first trade on a market still fetches its terms through
`books.ensure_open`, once per market, ever.

### The TTL is a refusal parameter, and a stale copy never says yes

A copy older than `LOCAL_COPY_MAX_AGE_SECONDS` (default 5.0), or never
loaded, refuses every trade with the code the old call used when its service
did not answer: `503 auth_unavailable` or `503 market_terms_unavailable`.
Past the max age trades stop. They never go ahead on an old yes.

That is the answer to ADR 0022's note. A correctness parameter is one where a
bigger value lets more wrong trades through. Here a bigger max age only
delays the refusal, and an outage does not widen anything: auth or
market_service down for a minute means the copy ages past five seconds and
trades refuse `503`, exactly as the per-trade calls did. Nothing fails open.

Age is measured on the replica's monotonic clock, stamped **when the request
is sent**, so the time an answer took is counted against it, never hidden.
The age is checked again after any refresh a trade waits for, because a
fetch can land older than the max age.

### The one window it opens is about one second, and it is accepted

A suspension, or an administrator's early close (ADR 0014), reaches the trade
path on the next successful refresh: about one second, the refresh interval
plus the fetch. If refreshes start failing, the old copy is trusted until it
is five seconds old and then refuses everything. So the worst case is five
seconds, and only while the service that made the change stops answering.

This is the same kind of window ADR 0017 accepted for the half round trip
between market_service's answer and the commit, and ADR 0022 for auth's. It
is named, it is bounded, and nothing short of a distributed transaction
closes it. It is wider: one second where those were milliseconds. Accepted,
because neither change is something a trader can time. An early close is
an administrator stopping a flawed market, and a suspension is an
administrator stopping an account.

Three things stay exact:

- **A close on `close_time`.** The copy check before the lock is only the
  early refusal. The check that decides runs under the book lock, against
  Postgres's `clock_timestamp()` read after the lock
  (`books.refuse_if_closed_under_lock`). Every service shares one Postgres,
  so this is the clock market_service decides its own closes on (D-025 and
  "Every decision a market records is stamped with Postgres's
  `clock_timestamp()`, read after the row lock"). A trade that queued past
  `close_time` is refused.
- **Settlement and void.** Both write the ledger's own `market_results`
  latch, which a trade reads under the book lock (ADR 0019, ADR 0025).
- **A replay.** It is answered before either check, as ADR 0017 fixed.

### A market the copy lacks costs a refresh or two, and is then 404 or 503

A market published since the last refresh is not in the copy. A trade on it
makes one refresh, unless the copy is younger than the floor below, and
looks again. If the market is still absent, the ledger
looks for its own book: no book is `404 market_not_found`. A book means the
market was published and nothing deletes one, so the ledger refreshes again
until the copy is stamped after it read the book (below), and only a market
still absent then is `503 market_terms_unavailable`: market_service
answering wrongly, ADR 0017's last row. A failed refresh is `503`, never
`404`. The miss path rolls the session back before it calls out, so it
holds no pooled connection (D-043).

Miss refreshes on an id with no book are floored: such a miss refreshes only
if the copy is at least 250 ms old, so a replica makes at most about four
miss refreshes a second however many unknown ids arrive. The price is a
fail-closed `404` for a market published in the last quarter second and not
yet touched. A per-id negative cache was rejected for this: it absorbs
repeats of one id and nothing else, so a client sending random ids would
still cause a fetch per request.

A market with a book is not an unknown id: `books.ensure_open` opened the
book from market_service's own terms, so the publication committed before
the book did, and the book before the ledger read it. The floor does not
apply to it, and it is refreshed until the copy is stamped at or after that
read, which is at most twice more: a miss can join a refresh sent earlier
(next section), which proves nothing about a market published since, and
runs are serialised, so the one after is stamped late enough. The copy is
read again after the book read, because a refresh that landed during it
saves the fetch, and the book read is the one await nothing else checked
the copy's age across, so that read checks it. Only then is a market with a
book `503`. This is the common
case, not an edge: the trade panel snapshots a market before it trades, and
the snapshot opens the book, so the first trade in the second after
publication arrives with a book and no copy entry. With the floor applied to
it, that trade was `503` one time in four. Nothing an unknown id can do makes
a book exist, so the cap above still holds for it. The residual cost sits
with ADR 0017's last row: while market_service wrongly omits a market the
ledger holds a book for, each trade on it drives up to three refreshes, the
floored one and two more, one in flight at a time, rather than four a second
across the replica.

### Refreshes run one at a time, and never move the copy backwards

The market copy's background task and the miss path share one coalesced
refresh. Callers
who arrive while a refresh runs wait for it and share its result. If it
fails, they all raise its error, so during an outage the upstream sees one
attempt, not one per waiting trade. A refresh replaces the copy only if it
was sent after the one it replaces, so a slow fetch can never put the copy
back in time.

### One predicate, restated once across the boundary, and pinned by a test

ADR 0017 read market_service's derived status so that there would be
"exactly one predicate in this system". The copy carries stored values, so
the ledger now derives: open means `status == "open"` and a `close_time`
strictly after now, and a null `close_time` is closed. That restates
market_service's `core/closing.py::trading_is_open` once.
`unit_test/service/test_local_copy.py`'s
`test_the_trading_predicate_restates_market_service_s_closing_rule` reads
that function's source and fails if either half of it changes.
`test_the_open_status_matches_the_one_market_service_puts_on_the_wire` pins
`"open"` against `MarketStatus.OPEN`.

### Readiness and logs

`/ready` reports `suspended_copy` and `market_copy`, each true while its copy
is within the max age (ADR 0028). `/health` does not, so a ledger whose copy
cannot load still starts, takes traffic and refuses trades `503`, which is
ADR 0022's choice. A refresher logs the first failure of a streak at ERROR
with its traceback, each repeat at WARNING with the count, and the recovery
at INFO. It catches bare `Exception`, because a refresher that died is a copy
that never refreshes, and every trade would refuse five seconds later with
nothing in the log.

## Consequences

- **A trade on a warm market makes no HTTP call.** auth and market_service
  each answer one small list per ledger replica per second instead of one
  request per trade.
- **The key now stops trading.** ADR 0022 declined ADR 0021's key partly
  because using it would make key rotation stop trading. It does now. While
  the copies of `LEDGER_SERVICE_KEY` disagree, every refresh is `401`
  (logged at WARNING) and trades refuse `503` five seconds later. Rotation
  means restarting auth, market_service and the ledger together.
- **market_service holds `LEDGER_SERVICE_KEY` too.** The gateway returns 404
  for all of `/internal/`, which also hides auth's lookup route.
- **A trade's trace no longer reaches auth or market_service.** #599's
  check that Tempo "shows a trade as one trace across ledger, market and
  auth" (`docs/deployment.md`) is not reachable from a warm trade, by
  design. Only a market's first trade still crosses to market_service.
- **Amended by name.** ADR 0017: its gate reads the copy, and its "exactly
  one predicate" becomes the restated one above. ADR 0022: its check reads
  the copy, and its "not a cache" note is answered here. ADR 0021: one key and
  one caller, the ledger, now with three routes. Each carries a note.

## Alternatives rejected

- **Keep the two calls.** Run B is the evidence against it: both servers at
  100% CPU, and the run's errors were these two calls failing (the k6
  summary named under Context).
- **A shorter path to the same row**, ADR 0022's own suggestion. It makes
  each call cheaper and keeps one call per trade per service, so it keeps
  the timeouts and the CPU that scales with trades. It also does nothing for
  the market call.
- **A denylist in Redis.** ADR 0010 runs Redis with no persistence, and
  nothing that moves money may read it. ADR 0022 rejected this for the same
  reason.
- **market_service pushes closes to the ledger, and auth pushes
  suspensions.** A dual write with no outbox (ADR 0006): a push lost during
  a ledger restart is a close the ledger never hears of, and nothing ages it
  out. A pulled copy that is too old refuses by itself. Both services would
  also have to know the ledger exists.

---

> **This reverses if the one-second window stops being acceptable**, for
> example a market whose early close must be exact to the millisecond. The
> answer then is not a shorter interval, which only narrows the window. It is
> ADR 0017's push with a real outbox, ADR 0006's machinery bought on purpose.
>
> **It is revisited when either list stops being small.** Both are fetched
> whole every second. Suspensions are rare and published markets number in
> the hundreds. If either reaches the thousands, the refresh becomes a
> changes-since query.
