# Realtime service API

`ws://localhost:8004/ws/prices` in development. `http://localhost:8004/docs`
describes `/health` and nothing else — OpenAPI has no vocabulary for a
WebSocket, so **this page is the contract for the socket** and there is no
generated schema that will catch it drifting from the code. The shapes are
pinned by `backend/realtime_service/model/schemas.py` and its tests; if the two
disagree, the code is right and this page is a bug.

Covers [F-2] #42, and the transport half of [X-4] #37.

Why it is a separate service, why Redis, and why the snapshot lives elsewhere:
[ADR 0010](../adr/0010-realtime-price-broadcast.md).

## What this service does and does not do

It holds WebSocket connections, and it relays. It owns no database, computes no
price, and is authoritative about nothing. Every number it sends was published
by the service that owns `q`.

Two consequences matter to a client:

- **It cannot answer "what is the price now".** There is no `snapshot` command
  and there will not be one. A snapshot is an authoritative read; answering it
  from the last event a replica happened to see would give a client that
  reconnected to a freshly started process an empty answer in the same shape as
  a true one.
- **It cannot tell you a market does not exist.** It holds no market table.
  Subscribing to an id that names nothing is legal, silent, and never delivers.

## Not all of [F-2] #42 has landed

The socket, the bus and the ordering rules below are built and tested. The
**snapshot endpoint is specified here and implemented on the ledger**, per
ADR 0005, because it needs `q`, `b` and the LMSR cost function. Both landed:
the engine with [F-3] #43, the route itself with [F-9] #112.

What has not landed is the producer's caller: nothing on the ledger publishes
a `PriceEvent` yet. `service/bus.py::publish` exists and is tested, but
[T-2] #22 is the trade path that calls it after a commit. Until then, a
client can connect, snapshot and subscribe, but will see no `price` frame
arrive on a market that has already been open.

## Connecting

```js
const socket = new WebSocket("ws://localhost:8004/ws/prices");
```

**Authentication is the same access token as everywhere else**, and it is
checked at the handshake. There are two transports and the precedence rule is
ADR 0002's: the `Authorization` header wins over the cookie.

| Client | Transport |
| --- | --- |
| Browser | the `access_token` cookie, sent automatically |
| Service | `Authorization: Bearer <jwt>` |

**A browser has no choice.** The JavaScript `WebSocket` constructor takes no
headers, so the cookie is the only option — which means ADR 0002's deployment
constraint is load-bearing here specifically. A `SameSite=Lax` cookie is not
sent cross-site, so if the frontend and this API are not on one registrable
domain, the handshake is refused and live prices do not work at all. There is
no query-string token as a workaround: a URL is written down by every proxy and
access log there is.

**The origin must be in `CORS_ORIGINS`.** CORS itself does not apply to
WebSockets — no preflight, no browser-side origin rule — so this service checks
the `Origin` header by hand. A request with no `Origin` (any non-browser
caller) is allowed.

## How a connection ends

Close codes are in the application range, because the browser cannot read an
HTTP status off a failed handshake and #37 has to tell an expired session apart
from a dead network.

| Code | Meaning | What the client should do |
| --- | --- | --- |
| `4401` | No, malformed, expired or foreign token | Refresh the session, reconnect. |
| `4403` | Origin not allowed | Nothing. Check `CORS_ORIGINS`. |
| `4408` | Token expired **while connected** | Refresh the session, reconnect. |
| `4409` | Too far behind to catch up | Reconnect, snapshot, resume. |
| `1000` | Ordinary close | Reconnect if the page is still open. |

`4408` is the one that surprises people. An access token lives fifteen minutes
and a socket can be open for hours, so a connection that was authorised does
stop being authorised, and this service acts on that rather than streaming to
an expired session indefinitely.

`4403` arrives as a failed handshake rather than a close frame, because the
connection is refused before the upgrade.

## Client to server

Two commands. Anything else is answered with an error and the connection stays
open.

```jsonc
{ "action": "subscribe",   "market_id": "9d1c…" }
{ "action": "unsubscribe", "market_id": "9d1c…" }
```

Both are idempotent. Unknown fields are ignored, so a client one version ahead
of the server does not break.

One connection may watch **50 markets** by default
(`MAX_SUBSCRIPTIONS_PER_CONNECTION`). Re-subscribing to a market already held
does not count against it.

## Server to client

Every frame has a `type`.

**`subscribed` / `unsubscribed`** — acknowledgements. Worth waiting for:
without them, "subscribed to a market nobody has traded yet" and "my command
was dropped" both look like silence.

```jsonc
{ "type": "subscribed", "market_id": "9d1c…" }
```

**`price`** — the event this service exists for.

```jsonc
{
  "type": "price",
  "market_id": "9d1c…",
  "state_version": 42,
  "prices": [
    { "outcome_id": "4f2a…", "position": 0, "price": "0.6234" },
    { "outcome_id": "b7e1…", "position": 1, "price": "0.3766" }
  ],
  "occurred_at": "2026-09-15T09:12:44.318000+00:00"
}
```

**`error`** — the same envelope the four HTTP services return, so the frontend
parses one error shape across the whole backend.

```jsonc
{ "type": "error", "error": { "code": "unknown_action", "message": "…" } }
```

| `code` | Cause |
| --- | --- |
| `malformed_command` | Not JSON, not an object, or a binary frame. |
| `unknown_action` | `action` is not `subscribe` or `unsubscribe`. |
| `invalid_market_id` | `market_id` absent or not a UUID. |
| `too_many_subscriptions` | This connection is at its ceiling. |

## Prices are decimal strings, not JSON numbers

```jsonc
{ "price": "0.6234" }     // not 0.6234
```

Same rule, and the same reason, as `ledger-service.md`: a JSON number is an
IEEE double by the time the browser has parsed it. The price came out of the
ledger's exact arithmetic and putting it through a double on the last hop
throws that away. Parse with a decimal library, or format the string directly
for display.

`prices` carries **every** outcome, not only the one traded — a trade moves all
of them, and rendering one would show a market whose prices no longer sum to
one. Order by `position` for the order the administrator arranged.

## `state_version`, and why stale events cannot win

`state_version` is a counter that increases by one every time a market's state
changes, incremented in the **same database transaction** as the trade that
moved it. It is not a timestamp: two trades can commit inside one clock tick,
and server clocks disagree by more than the gap between trades. `occurred_at`
is for display and must never be used to order two events.

**The server drops any event not newer than the last it forwarded for that
market.** That covers a duplicate from a producer retry and a redelivery after
a Redis reconnect.

**The client must do the same, and it is not redundant.** Two cases the server
cannot cover:

- Your snapshot can be *newer* than an event already in flight. You fetch
  version 12 while version 11 is on its way to a replica that has forwarded
  nothing yet; that replica has no idea what you know.
- After a reconnect you may land on a different replica, and each keeps its own
  high-water mark.

So: **keep the highest `state_version` you have rendered, and discard any frame
at or below it.**

## The two sequences a client needs

**Opening a page**

1. `GET` the snapshot. Render it. Remember its `state_version`.
2. Open the socket and `subscribe`.
3. Apply `price` frames with a higher `state_version`; discard the rest.

Subscribing before snapshotting is also fine, and slightly safer — an event
that arrives during step 1 is then discarded by the version check rather than
missed.

**Reconnecting**

1. Reconnect the socket and `subscribe` again.
2. `GET` the snapshot again, because events published while you were away are
   gone — Redis pub/sub has no buffer and nothing is replayed.
3. Resume the version check from the snapshot's `state_version`.

A dropped broadcast is recoverable exactly because of step 2, which is why
there is no outbox anywhere in this design. ADR 0010.

## The snapshot endpoint

**`GET /ledger/markets/{market_id}/snapshot`, on port 8003.** It belongs to
whoever owns `q` — the ledger, per ADR 0005 — and landed with [F-9] #112,
split out of [T-2] #22 so the trade is a trade. Full shape, authentication
and error codes: `docs/api/ledger-service.md`.

The body is the `price` frame without the `type`:

```jsonc
{
  "market_id": "9d1c…",
  "state_version": 42,
  "prices": [ { "outcome_id": "4f2a…", "position": 0, "price": "0.6234" }, … ],
  "occurred_at": "2026-09-15T09:12:44.318000+00:00"
}
```

Identical on purpose. A client that renders a snapshot and a client that
renders an event should be running the same function.

## Publishing an event — landed with [F-9] #112, called by [T-2] #22 and [T-3] #23

The producer lives on the ledger now: `ledger_service/service/bus.py::publish`
puts JSON matching its own `PriceEvent` on the Redis channel
**`market.price`**, **after** the trade's transaction has committed.

```python
await redis_client.publish("market.price", event.model_dump_json())
```

`backend/realtime_service/service/bus.py::publish` is the same shape on this
service's side of the subscription, and the model beside it is the contract
both copy from each other rather than import.

That copy used to be forced — a build context could not reach across service
directories — and since [F-6] #76 it is not: `backend/shared/` is importable
from every service. It stays a copy because four lines of `redis.publish` are
below the bar ADR 0012 sets for that package, and because `PriceEvent` is a
contract a shared module would stop being able to version independently on
each end. `unit_test/model/test_price_event.py` and
`unit_test/service/test_price_publish.py` on the ledger's side hold both
copies to their originals by reading this service's source as text.

**Nothing calls `publish` yet.** [F-9] #112 shipped the primitive with no
caller — the same "primitive before caller" shape [F-7] #96 and [F-8] #109
already used — so a market's price does not currently change on this socket.
[T-2] #22 is the trade path that calls it, once, right after its own commit.

Rules that are not negotiable:

- **After the commit, never before.** Publishing first announces a price a
  rollback then un-makes.
- **`state_version` comes from the same transaction**, incremented under the
  same lock that wrote `q`. A counter assigned anywhere else is not ordered.
- **Send every outcome.**
- **The publish must not fail the trade.** Nothing subscribes on the producer's
  behalf and nobody acknowledges. If the publish throws, the trade has still
  happened and is still correct; log it and carry on. Clients recover by
  snapshotting.
- **Extra fields are rejected.** The consumer validates with `extra="forbid"`,
  so adding a field means changing the model and this page in the same pull
  request.

## Operations

`GET /health` reports the bus separately from the probe:

```jsonc
{ "status": "ok", "bus": "connected" }
```

`status` stays `ok` while `bus` is `disconnected`, deliberately. Failing the
probe would have the orchestrator restart the container and drop every socket it
holds, which is the one response that reliably makes a Redis blip worse. The
subscriber reconnects on its own with backoff.

| Variable | Required | Notes |
| --- | --- | --- |
| `REDIS_URL` | yes | No default, **in this service**. See below. |
| `JWT_SECRET` | yes | No default. Must match the auth service's. |
| `CORS_ORIGINS` | no | Comma-separated. **Also the socket's origin allowlist.** |
| `MAX_SUBSCRIPTIONS_PER_CONNECTION` | no | Defaults to 50. |
| `SEND_QUEUE_SIZE` | no | Defaults to 64. Overflow closes with `4409`. |

**`REDIS_URL` is required here and has a default in the ledger, on purpose.**
Since [F-9] #112 the ledger talks to Redis too, and the two services answer the
same question differently because the same mistake costs them different
things. Point this service at the wrong Redis and it starts, reports `ok`, and
relays nothing — the failure is the whole service and it is silent, which is
why a default that a forgotten variable could fall back on is refused here.
Point the ledger at the wrong Redis and it loses a broadcast: the publish is
fire-and-forget by design, the trade has already committed and is still
correct, and the client reconciles on its next snapshot. One lost frame is not
worth a service that will not start, so the producer takes
`redis://redis:6379/0` as a default and this consumer does not.

That asymmetry holds only while the publish stays fire-and-forget. If anything
on the ledger's publish path ever acknowledges, retries, or fails a trade on a
publish error, a wrong Redis there costs money rather than a frame, and the
argument above becomes this service's for both.
