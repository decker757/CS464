# Realtime service

Live market prices over a WebSocket. Subscribes to one Redis channel, fans each
price event out to the sockets watching that market, and drops anything older
than what it has already sent.

[F-2] #42. Foundational infrastructure for [T-2] #22 and [T-3] #23 (which
publish) and [X-4] #37 (which subscribes).

Why it looks like this:
[ADR 0010](../../docs/adr/0010-realtime-price-broadcast.md). The contract both
other sides code against:
[`docs/api/realtime-service.md`](../../docs/api/realtime-service.md).

## It owns nothing

That is the whole design and every other decision here follows from it.

No database. No role in `sql/01-roles.sql`, no schema in `sql/02-schemas.sql`,
no `DATABASE_URL`, and SQLAlchemy is deliberately absent from
`requirements.txt`. This is the only backend service that cannot be broken by a
change to `sql/` and the only one that never needs anyone to run
`docker compose down -v`.

It also computes no price and is authoritative about nothing. Every number it
sends was published by the service that owns `q`. Two things follow that catch
people out:

- **It cannot answer "what is the price now."** There is no snapshot command and
  there will not be one.
- **It cannot tell you a market does not exist.** It holds no market table.
  Subscribing to an id that names nothing is legal, silent, and never delivers.

## Running it

From the repo root, as part of the stack:

```bash
docker compose up --build        # this service on http://localhost:8004
```

`/docs` there describes `/health` and nothing else, because OpenAPI has no
vocabulary for a WebSocket. That is what `docs/api/realtime-service.md` is for.

Nothing in `sql/` needs to change and no `docker compose down -v` is required —
for this service, permanently, not just this time.

## Tests

```bash
cd backend/realtime_service
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

docker compose up -d redis       # from the repo root, not the database
.venv/bin/pytest
```

Against a real Redis rather than a fake, for the same reason the other four
suites run against Postgres rather than SQLite: the claims are about what the
bus actually does — that `listen` yields subscription confirmations alongside
messages, that a publish to a channel nobody has subscribed to yet is simply
gone rather than delayed. A double would agree with whatever the tests assumed.

Most of the suite needs nothing running at all. Only `service/test_bus.py` and
the delivery tests in `controller/test_ws_routes.py` open a connection.

## Driving it before trading exists

[T-2] #22 is the real producer, and until it lands nothing publishes anything —
which would leave [X-4] #37 building against a feed that is silent, where "my
subscription is broken" and "nobody has traded" look identical.

```bash
.venv/bin/python publish_test_price.py            # invents a market, prints its id
.venv/bin/python publish_test_price.py <id> 2 0.71  # move it: version 2, yes at 0.71
.venv/bin/python publish_test_price.py <id> 1 0.10  # stale; watch it get dropped
```

It imports the same model and the same `publish` the service validates against,
so anything it sends is something the real producer could have sent.

## Layout

```
core/       config, token verification, the close-code vocabulary
model/      schemas.py — the wire contract, in both directions
service/    subscriptions.py (the hub), ordering.py (the gate), bus.py (Redis)
controller/ routes (the socket), connection.py, token and origin transport
```

Imports point one way: `controller` uses `service`, `service` uses `core` and
`model`, nothing below reaches up, and `main.py` is the only file that knows
about everything.

The one placement worth explaining is `Connection`. It lives in `controller`
rather than `service` because everything in it is transport — a WebSocket, a
send buffer, frames on a wire. `service/subscriptions.py` deals only in a
`Subscriber` protocol with one `enqueue` method, which is what lets the entire
routing rule be tested without a socket.

There is also no `controller/errors.py` here, unlike the other four. Every other
service maps a domain error onto an HTTP status because every other service
answers HTTP requests. The vocabulary that matters here is close codes, and it
lives in `core/errors.py` with the table.

## Five things worth knowing before changing this

**CORS does not protect the socket.** There is no preflight on a WebSocket
handshake and the browser enforces nothing about who may open one, so the
`CORSMiddleware` in `main.py` guards `/health` and `/docs` and never sees the
socket. `controller/transport.py::origin_allowed` is the hand-written check, and
`CORS_ORIGINS` is quietly doing two unrelated jobs. Deleting it looks like
removing a duplicate of the middleware; it is removing the only thing that will
stand between a logged-in user and a feed opened by another site on the day ADR
0002's `SameSite=None` possibility arrives.

**A browser has exactly one way to authenticate here.** The JavaScript
`WebSocket` constructor takes no headers, so the cookie is it. That makes ADR
0002's same-registrable-domain constraint decide whether live prices work at
all, rather than being a deployment footnote. A token in the query string would
work and is refused: a URL is written down by every proxy and access log there
is.

**A token expires while the socket is open, and that is handled.** Every other
service checks `exp` once and is finished in milliseconds. A socket authorised
on a fifteen-minute token can be open for hours, so `_expire` closes it with
`4408` when the token runs out. Remove it and ADR 0002's "the 15-minute TTL is
what bounds that window" stops being true of this service.

**A slow client is dropped, not buffered.** Each connection has a bounded queue
and a single pump; overflow closes with `4409` and the client reconnects and
snapshots. Everything queued behind a stalled client is a price that is already
wrong, so delivering it late is worse than not delivering it. The single pump is
also what keeps delivery ordered per socket — firing a task per send would not
block either, and would reintroduce the reordering `service/ordering.py` exists
to prevent, one layer lower.

**Nothing in `PriceBus._dispatch` may raise.** It runs inside the `listen` loop,
and an exception escaping would tear down the subscription for every connected
client on this replica because one producer sent one bad frame. A malformed
payload is logged at warning and dropped; `test_bus.py` publishes five kinds of
rubbish and then a good event to prove the loop survived.

## The staleness guard, and why the client still needs its own

`service/ordering.py` drops any event not strictly newer than the last this
process forwarded for that market. That covers a duplicate from a producer retry
and a redelivery after a Redis reconnect.

It is **not** the client's guarantee, and [X-4] #37 has to do the same check.
Two cases this cannot cover: a client's snapshot can be newer than an event
already in flight to a replica that has forwarded nothing yet, and a reconnect
can land on a different replica with its own high-water mark.

## What has not landed

The **snapshot endpoint** [F-2] #42 asks for. It needs `q`, `b` and the LMSR
cost function, none of which exist yet ([F-3] #43), and all of which live with
the ledger per ADR 0005. Serving it from here would mean answering from the last
event this replica happened to see — so a process started thirty seconds ago
would hand a reconnecting client an empty answer in the same shape as a true
one.

`docs/api/realtime-service.md` pins the response body: the `price` frame without
its `type`, so that rendering a snapshot and rendering an event are the same
function on the client. It lands with [F-3] #43 and [T-2] #22.

## Configuration

| Variable | Required | Notes |
| --- | --- | --- |
| `REDIS_URL` | yes | No default. A default is a credential in the repo. |
| `JWT_SECRET` | yes | No default. Must match the auth service's. |
| `CORS_ORIGINS` | no | Comma-separated. **Also the socket's origin allowlist.** |
| `MAX_SUBSCRIPTIONS_PER_CONNECTION` | no | Defaults to 50. |
| `SEND_QUEUE_SIZE` | no | Defaults to 64. Overflow closes with `4409`. |
