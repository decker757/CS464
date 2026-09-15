# ADR 0010: A relay that owns nothing, and a bus that is not the database

- **Status:** Accepted
- **Date:** 2026-09-15
- **Affects:** [F-2] #42, [T-2] #22, [T-3] #23, [X-4] #37, [T-1] #21, [F-3] #43, [5.4] #20
- **Implemented in:** `backend/realtime_service/`, and the producer contract in `docs/api/realtime-service.md`

## Context

[F-2] #42 asks for four things: a websocket server, pub/sub, an authoritative
snapshot with a state version so stale events cannot overwrite newer prices,
and auth on the socket.

Three facts about the repository shaped how much of that could land.

**There is no price yet.** [F-3] #43 is unstarted, no `q` exists anywhere, and
`b` has never crossed into the ledger — `market_service/model/entities.py` says
"[1.3] #3 snapshots them to the trading side", and what #3 actually shipped was
an audit-log context snapshot, not a handoff. A snapshot endpoint has no data
behind it today.

**ADR 0005's producer assignment predates ADR 0009.** It says the composite
trading service is the websocket producer, because it "is the only place that
knows both that a trade committed and what the resulting price is". That was
written before ADR 0009 settled that the ledger owns the write path, and before
it was clear the composite would own no role and no schema — which means the
composite cannot open a transaction against `ledger.*` at all. The transaction
that commits a trade is the ledger's.

**Every service in this repository shares one Postgres**, and ADR 0006 leaned
hard on that: the audit log is written in the acting transaction precisely so
there is no queue, no retry and no broker anywhere near it.

## Decision

### The websocket server is its own service, and owns no data

`backend/realtime_service/` holds connections and relays frames. No login role,
no schema, no `DATABASE_URL`, nothing in `sql/`. It is the only backend service
that cannot be broken by a change to the database, and the only one that needs
no `docker compose down -v` from anybody, ever.

That is what made a fifth service affordable. ADR 0005 called four services for
three people "the real cost and it is not small", and it was right. This one
costs a Dockerfile, a compose entry and a CI leg — the same bill ADR 0005 drew
up for the composite, for the same reason: it owns nothing.

The alternative that was actually close was hanging the socket off the ledger,
which already owns `q`, the state version and the committing transaction, and
would have needed no bus at all with a single replica. It was rejected on blast
radius. The ledger is the highest-integrity service here and the only one that
holds money; a public surface where anonymous clients hold connections open for
hours does not belong in the same process as the write path, and a few thousand
idle sockets should not be able to exhaust a pool that exists to move credits.
The two workloads also scale on completely different axes, which is ADR 0005's
argument for separating trading from admin CRUD, applied one service over.

### Redis is the bus

A committed trade publishes a `PriceEvent` to the Redis channel `market.price`.
Every replica subscribes and fans out to the connections it is holding.

The honest competitor was Postgres `LISTEN/NOTIFY`, and it wins on one large
point: `NOTIFY` is transactional. It fires on commit and never on rollback, so
"broadcast on trade commit" would be the literal semantics, with no dual-write
and no window — exactly ADR 0006's argument, reused.

Redis was chosen anyway, for two reasons that only became visible once the
service was drawn out.

**It does not care where the producer runs.** `NOTIFY` has to be issued on a
Postgres session inside the trade's transaction, which welds the producer to
whichever process holds that transaction. Whether the trade write path ends up
inside the ledger service or behind an HTTP call from the composite is an open
question (see "What this does not settle" below) and Redis is indifferent to
the answer. `LISTEN/NOTIFY` would have needed that question resolved first.

**The relay needs nothing from the database.** A `LISTEN` still requires a
login role, so `sql/01-roles.sql` would have gained a `realtime_svc` — and a
`CREATE ROLE` is not idempotent, so landing this would have meant either a
volume wipe for all three of us or another hand-applied migration. Redis is the
reason the previous section can say "nothing in `sql/`" at all.

**What it costs, stated plainly.** The producer publishes *after* the commit, so
a crash in the window between them loses that broadcast. There is no outbox and
no relay, and there will not be one. The recovery path already exists and is
required by [X-4] #37 regardless: a client that reconnects fetches a snapshot
before resuming. A lost broadcast is a price that is briefly stale on a screen
that is about to reconcile, which is a different category of problem from a lost
audit entry — and it is why ADR 0006 could not have made this call and this
record can.

### The producer is whoever holds the committing transaction

Which is the ledger, not the composite. **This amends ADR 0005's "The composite
is the websocket producer."** The reasoning there was about knowledge — who
knows a trade committed and what the price became — and the better test is
ownership: whoever holds the transaction is the only party that can publish
strictly after it commits and never after it rolls back.

The composite may still be the thing that *calls* it. What it cannot be is the
thing that decides a trade has committed.

### The snapshot is specified here and implemented where `q` lives

There is no snapshot endpoint on this service and there will not be one. It
would have to answer from the last event the replica happened to see, and a
process started thirty seconds ago would hand a reconnecting client an empty
answer in the same shape as a true one. A cache that cannot tell you it is empty
is worse than no cache.

`docs/api/realtime-service.md` pins the response body, which is the `price`
frame without its `type`, so that rendering a snapshot and rendering an event
are the same function on the client. It lands with [F-3] #43 and [T-2] #22, on
the ledger, per ADR 0005.

### `state_version` is a counter, and both ends check it

A per-market integer incremented in the same transaction as the trade. Not a
timestamp: two trades can commit inside one clock tick and server clocks
disagree by more than the gap between trades.

The server drops any event not strictly newer than the last it forwarded for
that market. The client does the same against what it has rendered. Both are
necessary — a client's snapshot can be newer than an event already in flight,
and a reconnect can land on a replica with its own high-water mark — and
`service/ordering.py` carries the argument.

### Auth is checked at the handshake and again when the token expires

The same access token, the same ADR 0002 precedence (header beats cookie), the
same verification code copied a fifth time.

Two things are new here because a socket is not a request.

**Expiry is enforced.** Every other service checks `exp` once and is done in
milliseconds. A socket authorised on a fifteen-minute token can be open for
hours, so the connection is closed when the token runs out. Without that, ADR
0002's "the 15-minute TTL is what bounds that window" would simply not be true
of this service.

**The origin check is hand-written.** CORS does not apply to WebSocket
handshakes — no preflight, and the browser enforces nothing — so
`CORSMiddleware` guards `/health` and `/docs` and does not look at the socket.
Today `SameSite=Lax` keeps the cookie off a cross-site handshake and that is the
real defence; ADR 0002 records that `SameSite=None` is a live possibility before
[5.3] #19, and on that day this check is the only thing between a logged-in
victim and a feed opened by another page.

### A slow consumer is dropped, not buffered

Each connection gets a bounded queue and a single pump. Overflow closes the
socket with `4409` and the client reconnects and snapshots.

This is correct rather than expedient. Everything queued behind a stalled client
is a price that is already wrong, so delivering it late is worse than not
delivering it. The single pump is what keeps delivery ordered per socket — a
task per send would not block either, and would reintroduce the reordering that
`state_version` exists to prevent, one layer lower.

## Consequences

**Redis is a new piece of infrastructure, and the first thing here that is not
Postgres or Python.** It runs without persistence, because a price event is
worth broadcasting for about as long as it takes to reach a socket. The
container can be deleted and recreated at any time and nothing is lost.

**A publish must never fail a trade.** Nothing acknowledges and nothing
subscribes on the producer's behalf. If the publish throws, the trade has
happened and is still correct; log it and carry on.

**This service will disagree with `/docs`.** OpenAPI cannot describe a
WebSocket, so `docs/api/realtime-service.md` is the contract and no generated
schema will catch it drifting. `model/schemas.py` and its tests are the
enforcement; the page is prose beside them.

**Replicas coordinate about nothing.** Each holds its own connections, its own
map and its own high-water marks. Redis delivers every event to every replica.
That is what makes this horizontally scalable with no shared state, and it is
also why a per-replica version gate is not the client's guarantee.

**One channel carries every market.** Redis would filter with
`market.price.<id>`, at the cost of a subscribe and unsubscribe round trip per
market whose last watcher left, the reference counting to know when that
happened, and the races in between. Move when a replica is measurably spending
its time discarding other people's events.

**The verification path is now copied five times.** ADR 0005 said to extract a
narrow `backend/shared/` when the trading service lands, and named the blocker:
`COPY . .` build contexts. This makes the case one service stronger and does not
change the blocker.

## What this does not settle

**Where the trade write path runs.** All three writes a trade performs — ledger
entries, the position and the market's share counts — live in `ledger.*`, and
only `ledger_svc` can perform them, so the transaction is the ledger's. Whether
[T-2] #22's trade logic runs inside the ledger service or calls it over HTTP is
open, and it is an amendment to ADR 0005 rather than a detail of this one. It
does not block anything here: the producer publishes the same event either way,
which is most of why Redis was the right bus.

**Whether the composite or the frontend composes a market view.** ADR 0005 left
that to the tickets that need it and this record does not take it back.

## Alternatives rejected

**A websocket endpoint on the ledger service.** Cheapest by a wide margin: no
new service, no new container, and with one replica no bus at all. Rejected on
blast radius and on scaling axis, above. Worth revisiting only if the fifth
service turns out to cost more than this record expects.

**Postgres `LISTEN/NOTIFY`.** Strictly better on the one property that matters
most — transactional delivery — and rejected for the two reasons above, with
the lost-broadcast window accepted explicitly rather than overlooked. If the
producer's location settles inside the ledger and the snapshot path ever proves
insufficient in practice, this is the decision to reopen first.

**The socket in the trading composite**, which is what ADR 0005 literally says.
The composite does not exist, #42 is meant to land before trading, and a
horizontally scaled composite holds only a fraction of the subscribers, so it
would need the bus anyway.

**A snapshot served from this service's last-seen event.** Rejected above: it
answers "I have not seen anything" and "there is nothing" identically.

**An outbox and a relay to close the lost-broadcast window.** The machinery ADR
0006 exists to avoid paying for, bought here to protect a price on a screen that
reconciles on its next reconnect.
