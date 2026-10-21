# Running the stack replicated

[5.4] #440. `docker-compose.yml` runs exactly one of everything, which proves
nothing about scaling sideways. This page is the other half of #20, which
measures one instance under load: two copies of every stateless service behind
one load balancer, actually run, with what came back from the run.

The architecture was already built for it, so most of the work is
configuration — `docker-compose.scale.yml` and `deploy/nginx.conf`, about 120
lines between them. One thing in it is a correctness rule rather than a
convenience, and it is the reason this page exists for [5.3] #19 to read:
**the migrate step runs once per deploy, never once per replica.**

## What runs

```bash
docker compose -f docker-compose.yml -f docker-compose.scale.yml \
  up -d --build --wait
```

The override sets its own project name, `cs464scale`, so this cannot scale,
restart or tear down a `cs464` stack somebody is using: separate containers,
separate network, separate database volume. It also publishes no host port
except the proxy's, on **18000-18004** rather than 8000-8004, so the two
stacks run side by side. Tear it down with
`docker compose -p cs464scale -f docker-compose.yml -f docker-compose.scale.yml down -v`.

| | Containers | Host port |
| --- | --- | --- |
| `auth`, `market`, `audit`, `ledger`, `realtime` | 2 each | none; the proxy's |
| `auth-migrate`, `market-migrate`, `ledger-migrate` | 1 each, exits 0 | none |
| `db`, `redis` | 1 each | none; reach them with `exec` |
| `proxy` (nginx) | 1 | 18000 auth, 18001 market, 18002 audit, 18003 ledger, 18004 realtime |

`audit` is replicated too. It owns no schema and has no migrate step, so it is
the one service with nothing to prove here; leaving exactly one unreplicated
would have been arbitrary.

**nginx, and one listener per service.** The ticket left the choice to
whichever was less work, and with a port per service there is no service
discovery to configure: it is a dozen lines of `nginx.conf` against a label on
every service. Path-prefix routing was the alternative and would have needed
rewriting, because the services do not namespace their paths — `/health` and
`/docs` are on all five and both `auth` and `market` answer under `/admin` —
while the frontend already addresses each service by its own URL
(`VITE_*_API_URL`). So the proxy stays a pure load balancer and neither side's
contract changes.

### Point the frontend at it

```bash
VITE_API_URL=http://localhost:18000 \
VITE_MARKET_API_URL=http://localhost:18001 \
VITE_AUDIT_API_URL=http://localhost:18002 \
VITE_LEDGER_API_URL=http://localhost:18003 \
VITE_REALTIME_WS_URL=ws://localhost:18004 \
E2E_BASE_URL=http://localhost:15173 \
COMPOSE_PROJECT_NAME=cs464scale \
CI=1 npm run e2e
```

`CORS_ORIGINS` has to carry whatever origin serves the frontend — in that run
`http://localhost:15173` — because in the realtime service that variable is
also the socket's origin allowlist, and CORS does not apply to a WebSocket
handshake (ADR 0010). `COMPOSE_PROJECT_NAME` is what makes the suite's admin
promotions reach **this** project's database rather than a dev stack's.

**The stack has to be started with `STARTING_CREDITS=1000`.** Seven tests
assert the starting grant as a figure on screen — "1,000 credits" — and that
is `.env.example`'s value, which the e2e workflow's throwaway `.env` keeps. A
`.env` that sets anything else fails those seven against this stack and
against a dev one equally, so it is not a replication problem; it is worth
knowing before reading the failures as one.

## The migrate step, which is the interesting part

ADR 0020 decided the step runs once per deploy and takes no lock. So scaling
`auth`, `market` or `ledger` must not scale `auth-migrate`, `market-migrate` or
`ledger-migrate`. They are pinned to `replicas: 1` in the override, written out
rather than left to the default so that the one number that must not change
sits beside the ones that do. Each app's
`depends_on: service_completed_successfully` is untouched, and it gates **every**
replica rather than the first one:

```
cs464scale-auth-migrate-1    started=07:24:53.538  finished=07:24:55.115  exit=0
cs464scale-auth-1            started=07:24:55.223
cs464scale-auth-2            started=07:24:55.323
cs464scale-market-migrate-1  started=07:24:53.539  finished=07:24:55.140  exit=0
cs464scale-market-1          started=07:24:55.251
cs464scale-market-2          started=07:24:55.381
cs464scale-ledger-migrate-1  started=07:24:53.539  finished=07:24:55.172  exit=0
cs464scale-ledger-1          started=07:24:55.683
cs464scale-ledger-2          started=07:24:55.839
```

One migrate container per schema, each exited 0, and both replicas of each app
started after it. `docker compose ps -a` agrees: `auth-migrate-1`,
`market-migrate-1` and `ledger-migrate-1`, no `-2`.

### The race, raced

ADR 0020 says two concurrent copies are not coordinated and the loser fails
loudly. That was checked rather than assumed, with `--scale auth-migrate=N`
against an empty volume.

**First attempt, three copies, nothing to synchronise them: all three exited
0.** One ran the baseline and the other two found the schema already at head
and had nothing to do. Container start-up jitter — a few hundred milliseconds
of Python imports — had serialised them.

**Second attempt, six copies holding on a wall-clock barrier until the same
second: four of six exited 1,** each on

```
asyncpg.exceptions.DuplicateTableError: relation "alembic_version" already exists
[SQL: CREATE TABLE auth.alembic_version (...)]
```

The other two exited 0: one applied the baseline, the other arrived after it
had committed. Afterwards the schema held exactly `alembic_version`,
`refresh_tokens` and `users`, at revision `0001` — the losers' transactions
rolled back and left nothing, which is what ADR 0020 claims and what makes the
failure safe as well as loud.

**What #19 has to take from this.** The loud failure is real but it is not a
test you can rely on: the first attempt shows that a pipeline which runs the
step once per replica can pass repeatedly and then fail on the one deploy where
the replicas happen to start together, and the deploy it fails is the one where
a replica does not come up at all. There is nothing to detect by trying it. So
#19 runs the step as its own job, once, before the app rolls out, and the shape
to copy is this file's rather than anything about how many app replicas there
are. If the platform cannot run a one-off job before the app — the reversal
ADR 0020 names — then the advisory lock it lists as "the first thing to add"
stops being optional.

Three tests hold the shape:
`test_the_scale_override_replicates_auth_and_not_its_migration` and its market
and ledger twins, in each service's `unit_test/test_migrations.py`. They read
this override and fail if a migrate step ever grows replicas, if an app loses
them, or if the override touches `depends_on` — the one key here that could
loosen the gate, since `depends_on` merges and `!reset` would empty it.

## What the load balancer actually does

Twenty `GET /health` requests per service, counted by the `X-Served-By` header
nginx adds from `$upstream_addr`:

```
18000 auth:     10 172.20.0.4 (auth-1)      10 172.20.0.8 (auth-2)
18001 market:   10 172.20.0.6 (market-1)    10 172.20.0.11 (market-2)
18002 audit:    10 172.20.0.5 (audit-1)     10 172.20.0.9 (audit-2)
18003 ledger:   10 172.20.0.12 (ledger-1)   10 172.20.0.13 (ledger-2)
18004 realtime: 10 172.20.0.7 (realtime-1)  10 172.20.0.10 (realtime-2)
```

**That took one setting to get right, and it is the kind of thing that looks
like a broken load balancer.** With `worker_processes auto` the first run came
back 13/7, 14/6, 15/5 — and a short sequence of eight requests landed entirely
on one replica. Round-robin state in nginx is per worker process, and every
worker starts its cursor on the same upstream, so on an eight-core laptop a
handful of requests spread across fresh workers all choose the same backend.
`worker_processes 1` alternates strictly, and one worker is not a capacity
question at this traffic: the backends are what is being scaled here.

The upstreams are plain `server auth:8000;` entries. nginx resolves each name
once at startup and uses every address the resolver returns, which under
`deploy.replicas` is one per replica — which is also why the proxy waits for
every replica to be `service_healthy` before it starts, and why a replica
recreated with a new address needs a `restart proxy` after it. A long-lived
deployment wants a resolver and a re-resolving upstream; a demonstration does
not.

## A trade whose round trip crosses replicas

Every call below went through the proxy, and the replica is the one nginx named
in `X-Served-By`.

```
register admin   -> auth-2    (172.20.0.8)    201
log in again     -> auth-1    (172.20.0.4)    200
save market      -> market-2  (172.20.0.11)   201
publish market   -> market-1  (172.20.0.6)    200
register trader  -> auth-2    (172.20.0.8)    201
first balance    -> ledger-1  (172.20.0.12)   balance=1000.0000

GET  preview     -> ledger-2  (172.20.0.13)   state_version=0  total=-5.1250
POST trade       -> ledger-1  (172.20.0.12)   201  total=-5.1250  state_version=1
GET  balance     -> ledger-2  (172.20.0.13)   balance=994.8750
```

The two requests of the round trip are the preview and the write, and they are
the pair that matters: a trade **requires** the `state_version` its preview
quoted and compares it for strict equality under the book row's own lock
(ADR 0015), so a quote from one replica has to be honoured by another or the
trade is `409 quote_stale`. It was: the `total` the second replica charged is
the one the first quoted, to the last of four decimal places.

Three smaller buys followed, each quoted on `ledger-1` and written on
`ledger-2`, so both replicas committed trades inside one run. Afterwards, on
the whole table:

```
entries=16 sum=0.0000
transactions_not_summing_to_zero=0
```

That is the invariant two writers could have broken and did not — because a
balance is derived rather than stored and every read that decides a write is
locked, so the second writer is safe by construction rather than by luck.

Three other crossings in that trace are worth naming. The admin **registered**
on one auth replica and **logged in again** on the other, which is ADR 0002's
whole point: authority rides in a signed token, so no replica holds the
session. The market was **saved** on one market replica and **published** from
the other, and publication is still one way (ADR 0008) across the pair. And
the trade's own upstream checks — "is this market still trading" (ADR 0017)
and "is this trader still active" (ADR 0022) — were answered by whichever
`market` and `auth` replica Docker's DNS handed the ledger's HTTP client, since
the ledger dials `http://market:8000` and `http://auth:8000` by service name.

## A price frame published by one replica, read on the other

This is the one that could genuinely have failed, so it was run twice, from
both ends.

**Pinned to a replica.** One WebSocket client inside each `realtime` container,
connected to that container's own uvicorn on `localhost:8000`, so there is no
question which replica is holding which socket. Both subscribed, then one buy
went through the proxy: quoted by `ledger-1`, written by `ledger-2`. Both
clients received the same frame:

```
realtime-1 (1e61ba654d72)  {"type":"price","state_version":5,
                            "prices":[{"price":"0.5399"},{"price":"0.4601"}], ...}
realtime-2 (5999827a071b)  {"type":"price","state_version":5,
                            "prices":[{"price":"0.5399"},{"price":"0.4601"}], ...}
```

**Through the proxy, the way a browser arrives.** Two sockets opened at
`ws://proxy:8004/ws/prices`; nginx handed one to `realtime-1` (172.20.0.7) and
the other to `realtime-2` (172.20.0.10), which each client read off
`X-Served-By` on its 101 response. Another buy, quoted by `ledger-1` and
written by `ledger-2`, reached both at `state_version=6` with identical prices
`0.5474` and `0.4526`.

So the publisher is a third process in both runs, and neither socket is on it.
That is ADR 0010 working: the ledger publishes to Redis after the trade
commits, the relay owns nothing, and a client is not tied to the replica the
price came from. A client that reconnects to a replica which has seen nothing
yet fetches `GET /ledger/markets/{id}/snapshot`, which is the recovery path
[X-4] #37 already requires — and the reason the realtime service deliberately
cannot answer "what is the price now" itself.

## The end-to-end suite, unchanged, against the replicated stack

```
38 passed (1.4m)
```

The whole Playwright suite, with `CI=1` so the route-coverage check runs too,
against the stack above and with no test edited: the same files that run
against one instance in CI. Two of them matter more than the rest here.
`browse-prices` watches a card pick up a new price without a reload, and
`live-prices` watches a market page do it — both in a real browser, through
the proxy's 18004, on whichever `realtime` replica nginx gave that socket,
fed by whichever `ledger` replica committed the trade. That is criterion four
again, from the client the service was built for.

## Two singletons that turned out not to be

`.env.example` says `LEADERBOARD_REFRESH_ENABLED=false` "is for the second and
later replicas", and `deploy.replicas` cannot do that: every replica of a
compose service gets the same environment, so there is no way to switch the
refresher off on exactly one of two. Splitting the ledger into two
differently-configured services would have been the alternative, and it was not
needed. Both replicas refresh, and a refresh takes a transaction-level advisory
lock and writes nothing if a generation at least as new exists (ADR 0021), so
the second refresher does duplicate work and never a duplicate generation.

The market close sweep is the same shape for a different reason: both replicas
sweep, and the sweep only writes down a close the clock has already made, while
the trade path derives the answer instead of reading the status column
(ADR 0011). Two sweepers cost two index probes every ten seconds.

Neither needed a leader, which is worth knowing before #19 reaches for one.

## What this does not show

- **Nothing was measured.** No latency, no throughput, no failover timing.
  That is #20's job on one instance, and nobody has run it across replicas.
- **One host.** Two replicas of a service on one Docker network share a kernel,
  a disk and one Postgres. It shows the services hold no state that a second
  copy breaks; it does not show the database scales.
- **No rolling deploy.** Replicas all start at once here. #19 is where
  "migrate once, then roll the app" becomes a sequence rather than a
  `depends_on`.
- **Nothing was killed.** A replica dying mid-request, and nginx's passive
  health checks, are untested.
