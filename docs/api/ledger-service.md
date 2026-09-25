# Ledger service API

Base URL `http://localhost:8003` in development. Interactive docs, generated
from the code and authoritative if this page ever disagrees, at
[`/docs`](http://localhost:8003/docs).

Covers [F-1] #41, the backend half of [B-1] #32, [B-2] #33 and [4.1] #13,
[T-1] #21's cost preview, and [T-2] #22's buy route.

Why balances are derived rather than stored and why a read can write:
[ADR 0009](../adr/0009-the-ledger-write-path.md), amended by [T-2] #22 to
answer how a write route authenticates a caller. Why positions live here
rather than with markets: [ADR 0005](../adr/0005-trading-service-boundary.md).
Why the ledger asks market_service whether a market is still open, once per
trade: [ADR 0017](../adr/0017-the-ledger-and-a-stopped-market.md).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/ledger/balances/me` | My available balance |
| GET | `/ledger/entries/me` | My ledger history |
| GET | `/ledger/users/{user_id}/balance` | Any user's balance (admin) |
| GET | `/ledger/users/{user_id}/entries` | Any user's history (admin) |
| GET | `/ledger/markets/{market_id}/preview` | What a trade would cost |
| GET | `/ledger/markets/{market_id}/snapshot` | The market's authoritative current price |
| POST | `/ledger/markets/{market_id}/trades` | Buy shares |
| GET | `/health` | Liveness and readiness probe |

**Every other route reads, and none of them ever will edit or remove an
entry** — the ledger is append-only, enforced by a database trigger rather
than by the absence of a route. `POST .../trades` is this service's first
write, and [T-2] #22 answers ADR 0009's deferred question about it: the route
takes no account, no amount and no leg, so a trader's own token is a safe
credential for it in a way it could never be for a route that accepted one.

The balance route, the preview route and the snapshot route are each an
exception to "reads don't write," and for the same reason: a balance mints a
user's starting grant on first read (below), and the preview and the snapshot
each open and fund a market's book on that market's first touch (D-008,
D-037, and [F-9] #112 for the snapshot). All three are once-per-subject,
idempotent, and invisible to every caller after the first.

## Authentication

Identical to the other three services, because it is the same token.

- **Browser:** the `access_token` cookie the auth service already set. Send
  `credentials: 'include'` on every call, and make sure this origin is in
  `CORS_ORIGINS`.
- **Service:** `Authorization: Bearer <jwt>`. The header wins over the cookie.

The `/me` routes need only a valid token — a trader reading their own balance is
the ordinary case. The `/users/{user_id}/...` routes need `role: admin`, and
they are guarded on the role rather than on whose id is in the path, so an
administrator's own balance is available at either and a trader gets `403` at
the admin one even when asking about themselves.

- `401` means the session is gone — send the user to log in.
- `403` means the session is fine and this account is not an administrator.

## Amounts are decimal strings, not JSON numbers

```jsonc
{ "balance": "1000.0000" }     // not 1000.0
```

This is the one place the backend deliberately differs from the market service,
which sends `liquidity_b` as a number so the create form can do arithmetic with
it. A JSON number is an IEEE double by the time the browser has parsed it, and
`0.1 + 0.2` is not `0.3`. A credit total that is off by a floating-point epsilon
is a bug report about money.

Parse with a decimal library, or — since credits are whole numbers in practice —
read the integer part for display. Do not `parseFloat` and then add.

## GET /ledger/balances/me

```jsonc
{
  "user_id": "5f3e...",
  "account_id": "a71c...",
  "balance": "1000.0000"
}
```

`balance` is the sum of every entry on this account. There is no stored balance
for it to disagree with, which is what [B-2] #33's "derived from or reconciled
against the ledger" means here: there is nothing to reconcile.

**The first call for a user also mints their starting credits.** [B-1] #32. A
user with no entries has not been granted yet, so the first read writes the
grant — a real append-only transaction keyed on the user id — and returns the
result. Every later call just reads. This is why a new account never shows a
balance of zero, and why there is no window to poll through after registration.

Retrying, refreshing, or opening two tabs cannot produce a second grant, and
neither can an operator changing `STARTING_CREDITS`: a new value reaches
accounts granted after it and no others, and an account that already has its
credits keeps them and stays readable.

## GET /ledger/entries/me

```jsonc
{
  "entries": [
    {
      "id": "0c9d...",
      "created_at": "2026-09-15T04:21:09.412883Z",
      "amount": "1000.0000",
      "balance_after": "1000.0000",
      "transaction_id": "36fd...",
      "kind": "signup_grant",
      "context": { "user_id": "5f3e..." }
    }
  ],
  "next_cursor": null,
  "has_more": false
}
```

Newest first. `amount` is signed: negative took credits out of this account,
positive put them in — so a client does not have to infer direction from `kind`.

`balance_after` is [4.1] #13's running balance: what the account held once that
entry had landed. It is derived on every read by summing the entries up to and
including that one, so there is no stored column for it to disagree with, and
the newest entry's `balance_after` is the same number `/balance` returns.

It is anchored to the entry rather than counted back from today's balance,
which is what makes a page's figures independent of when it was fetched. A trade
arriving while an administrator reads is strictly newer than every row already
on screen, so nothing they have looked at moves, and page 2 fetched an hour
after page 1 still adds up against it. A column that stacks
`balance_after - amount` down the page will therefore always agree with the row
below it.

**Only this account's side appears.** Every movement writes at least two
entries sharing one `transaction_id`, and the other side belongs to the platform
or to a market pool. `transaction_id` is there so two entries of one movement can
be recognised as one event, not so you can fetch the other half; there is no
route that returns it.

`kind` is the vocabulary of why credits moved. Today the only value is
`signup_grant`; `trade_buy`, `trade_sell` and `settlement` arrive with [T-2]
#22, [T-3] #23 and [3.4] #12. **Treat an unrecognised value as opaque rather
than as an error** — new ones will appear without a version bump. Same rule for
`context`: render what you recognise, ignore the rest.

### Paging

| Query | Default | Notes |
| --- | --- | --- |
| `limit` | 50 | Clamped at 200. Asking for more is not an error. |
| `cursor` | — | The `next_cursor` from the previous page, unmodified. |

Keyset, not offset. A ledger only grows at the newest end, so with `OFFSET` any
entry appended between two pages pushes the window down and the last row of page
1 reappears at the top of page 2. For a statement of where somebody's money went
that means showing a transaction twice and hiding the one behind it.

```js
let cursor = null;
do {
  const url = `/ledger/entries/me?limit=50${cursor ? `&cursor=${cursor}` : ""}`;
  const page = await fetch(url, { credentials: "include" }).then(r => r.json());
  render(page.entries);
  cursor = page.next_cursor;
} while (cursor);
```

`has_more` is `next_cursor !== null`, stated separately so a "load more" control
can be driven without reasoning about the cursor at all. A cursor this service
did not issue is a `400 malformed_cursor`; echo the value back rather than
constructing one.

## GET /ledger/users/{user_id}/balance and /entries

[4.1] #13. Identical responses, for an administrator investigating an anomaly.
`403` for a non-admin.

The first call mints the target user's grant if they have never been read
before, exactly as the `/me` route does — so an admin looking up a brand new
account sees their starting credits rather than an empty one.

Each entry carries its `balance_after`, exactly as on the `/me` routes, which
is [4.1] #13's second criterion; that the newest one equals what `/balance`
reports is its third.

**Searching for the user is the auth service's half of this story.**
`GET /admin/users?q=...` on port 8000 takes a fragment of a username or an
email and returns accounts with their `id`; that `id` is what the two routes
above take. This service holds no user table and never will — it knows a user
id from a signed token and nothing else about who that is (ADR 0003), so a
history route that accepted a username would be this service asking another one
a question at every read.

## GET /ledger/markets/{market_id}/preview

[T-1] #21. What a trade would cost right now, and how it would move every
outcome's price — computed from `ledger_service/core/lmsr.py`, never
estimated. Fires on every keystroke of a quantity field, so it is deliberately
cheap once a market is warm; see the note below about the one request that
is not.

Query parameters, all required:

| Parameter | Type | Notes |
| --- | --- | --- |
| `outcome_id` | UUID | Must name one of this market's outcomes. |
| `side` | `"buy"` \| `"sell"` | Exactly these two strings. |
| `quantity` | decimal string | `> 0`, at most four decimal places and 18 digits in all. |

Any valid access token, any role — a preview mints nothing and reveals
nothing beyond the public market read, so there is no admin gate.

```jsonc
{
  "market_id": "9d1c...",
  "state_version": 42,
  "side": "buy",
  "outcome_id": "4f2a...",
  "quantity": "10.0000",
  "total": "-7.3152",
  "average_price": "0.7315",
  "prices": [
    { "outcome_id": "4f2a...", "position": 0, "price": "0.7216" },
    { "outcome_id": "b7e1...", "position": 1, "price": "0.2784" }
  ],
  "post_trade_prices": [
    { "outcome_id": "4f2a...", "position": 0, "price": "0.7413" },
    { "outcome_id": "b7e1...", "position": 1, "price": "0.2587" }
  ]
}
```

**The sign convention.** `total` answers "what happens to your balance", the
opposite sign from the engine's own "what does the market maker absorb":
**negative on a buy** (credits leave you), **positive on a sell** (credits
arrive). `average_price` is never negative — the direction already lives on
`total` — and is `abs(total) / quantity`, `ROUND_HALF_UP` at scale 4. It is a
display figure derived from the authoritative total, never the other way
round: [T-2] #22 charges `total`, never `quantity * average_price`.

**`average_price` can be exactly `1.0000`, and on a skewed book a little
more — do not render it as a fraction of a credit.** A share is worth less
than one credit, so the average of a real trade normally is too. The
exception is the smallest buy there is: `0.0001` shares cost a fraction of a
tick, are charged the whole tick (below), and divide out to exactly `1.0000`
in any market. On a heavily skewed book the engine's last significant digit
can carry a sub-tick cost across a tick boundary, and it reads higher still
— `2.0000` at `q = [1315, 1000]`, `b = 3`. The charge is right in both
cases; it is the per-share figure that is not a price.

**The rounding direction, and why it is not symmetric.** `total` is quantized
by the same function [T-2] #22 uses to build its ledger legs, so the number
this route quotes is the number that gets charged — that is the whole point
of keeping preview and trade on one code path (D-014). A buy rounds to the
next whole tick **up**; a sell rounds to the tick **down**. Either way the
residue — one tick at most, plus the engine's own last-digit error
(D-044) — goes to the market's pool, never to you,
because LMSR already expects the pool to be the side that can lose money.
`prices` and `post_trade_prices` carry no such bias: nobody is charged a
price, so both are `ROUND_HALF_UP`, same as everywhere else in this backend.

**Where that bites: a small sell is refused rather than quoted, in every
market.** A sell whose proceeds are under one tick (`0.0001`) would round down
to `total: "0.0000"` — you would give up the shares and be paid nothing — so
the request is refused instead: `proceeds_below_tick` (422), below. This is
not a skewed-market edge case. It fires whenever the quantity is below roughly
`0.0001 / price` of the outcome being sold. In an ordinary market with prices
of `0.7216` and `0.2784`, selling `0.0001` shares of the first is refused and
`0.0002` is paid `0.0001`; for the second, even `0.0003` is refused and the
smallest sell paid anything is `0.0004`.

For the sell form: treat `proceeds_below_tick` as "increase the quantity",
not as an error. A minimum of `0.0001 / price`, rounded up to four decimal
places, predicts it from the `prices` you already hold, but the server's
answer is the authority — prices move between your read and the request.

A buy below one tick is charged the whole tick, which is the pool's favour,
and needs no refusal. The exception is a buy the engine prices at **exactly**
zero, which happens only past about `110 × b` of skew between outcomes: that
is refused as `cost_below_tick` (422). You will essentially never see it in
practice, but handle it the same way.

**`state_version`** is the quote reference (D-011) — a JSON number, not a
string, because it is a count and not money — and the only one. [T-2] #22
compares it, under its own lock, against the version current when a trade is
confirmed, so a quote taken against a market that has since moved is caught
there rather than silently honoured here.

**`prices` and `post_trade_prices` carry every outcome**, in the order
`PriceEvent` uses (`docs/api/realtime-service.md`), not only the one traded —
a trade moves the whole softmax, and rendering one outcome would show a
market whose prices no longer sum to one. A client rendering a snapshot, a
price frame and this preview runs one function over all three shapes.

**The first request on a market writes, and can take a moment.** A market
nobody has previewed or traded in yet has no book on this service. This route
opens one: it fetches the market's terms from `market_service`, funds the
pool from the platform account, and only then prices the trade — once per
market, ever (D-008, D-037). That request can take up to the market-terms
timeout, because it makes a real call to another service; every request after
it, for that market, is a single indexed read. Debouncing this route is the
frontend's job either way, since it is meant to fire on every keystroke.

**Does not check whether the market is still open.** The book this route
reads carries no status, and `close_time` is not snapshotted into it — an
early close (ADR 0014) leaves it in the future on purpose, so there would be
nothing honest to check even if it were. A preview on a closed market still
returns a number. Gate the preview control on the market read's derived
status (`docs/api/market-service.md`) instead; [T-2] #22 is what refuses the
trade itself.

**The trade path disagrees with this on purpose, and it is not a bug.** [T-2]
#22's route makes its own call to market_service on every trade that is not a
replay of one already committed, and refuses `409 market_closed` when that
market's derived status is not `"open"` (ADR 0017). A preview is arithmetic
and fires on every keystroke; a trade is a decision and fires once — so the
same closed market that this route happily prices is the one the trade that
follows it will refuse.

**A sell has no holdings check.** It is priced arithmetically against shares
outstanding only — see the 409 below — never against what the caller holds.
The per-user holdings check belongs to [T-3] #23 and only means anything
taken under the trade's own lock; a preview that checked it here would be
quoting a refusal that could already be stale by the time anyone acted on it.

Errors specific to this route, reusing [F-7] #96's codes rather than
inventing new ones:

| Status | `code` | When |
| --- | --- | --- |
| 404 | `market_not_found` | No such market — and also a draft or a submitted one, which `market_service`'s public detail endpoint refuses with the same 404. This is what an unpublished market looks like from here. |
| 409 | `insufficient_shares_outstanding` | A sell larger than this outcome's shares outstanding — the no-shorting rule. |
| 422 | `unknown_outcome` | `outcome_id` does not name one of this market's outcomes. |
| 422 | `quantity_too_large` | The cost, or this outcome's shares outstanding after the trade, would exceed `99999999999999.9999`, the largest amount the ledger can store (D-040). Not reachable with any plausible quantity. A `quantity` of more than 18 digits in total is refused earlier, as plain validation. |
| 422 | `proceeds_below_tick` | A sell whose proceeds round down to `0.0000` — any quantity below roughly `0.0001 / price`, in any market (D-041). Ask for more. |
| 422 | `cost_below_tick` | A buy the engine prices at exactly `0.0000`, only past about `110 × b` of skew (D-041). Ask for more. |
| 500 | `market_book_incomplete` | This service holds a book for the market that cannot be priced — no outcome rows, one of them, or a `liquidity_b` the engine cannot use. Only a hand-run repair or a half-applied migration produces it. A server fault; not worth retrying. |
| 503 | `market_terms_unavailable` | `market_service` could not be reached on a market's first touch. Worth retrying. |

## GET /ledger/markets/{market_id}/snapshot

[F-9] #112. The authoritative price read: what a client renders when it opens
a market page, and what it re-fetches on every reconnect —
`docs/api/realtime-service.md`'s "Opening a page" and "Reconnecting"
sequences both start here. Computed from `ledger_service/core/lmsr.py`,
never estimated and never off the last event this service happened to
publish.

Any valid access token, any role — a snapshot mints nothing and reveals
nothing beyond the public market read, so there is no admin gate, exactly
like the preview beside it.

```jsonc
{
  "market_id": "9d1c...",
  "state_version": 42,
  "prices": [
    { "outcome_id": "4f2a...", "position": 0, "price": "0.6236" },
    { "outcome_id": "b7e1...", "position": 1, "price": "0.3764" }
  ],
  "occurred_at": "2026-09-15T09:12:44.318000+00:00"
}
```

**Byte-for-byte the `price` socket frame without its `type`.** Identical on
purpose — `docs/api/realtime-service.md` — so a client renders a snapshot and
a price frame with one function. `prices` carries every outcome, ordered by
`position`, as a decimal string; `state_version` is a JSON number, the same
counter the preview and every `PriceEvent` report.

`occurred_at` is the book's `state_changed_at`: the moment the trade that last
moved `q` committed. On a market nobody has traded that equals `opened_at`
(D-029) — creating the book was the last state change there has been.

**The first request on a market writes, and can take a moment.** A market
nobody has previewed, traded or snapshotted yet has no book on this service.
This route opens one exactly as the preview does: it fetches the market's
terms from `market_service`, funds the pool from the platform account, and
only then prices the market — once per market, ever. That request can take
up to the market-terms timeout. Every request after it, for that market, is a
single indexed read that writes nothing. Debouncing is not this route's
concern the way it is the preview's — a client fetches it once per page open
and once per reconnect, not on every keystroke — but the cold-path cost is
identical.

**It deliberately does not check whether the market is still open (ADR
0017).** The preview and this route agree with each other and disagree with
the trade path, and both agreements are on purpose. A closed market still has
a price to render — the last one anybody traded at — and a settled one still
shows a last price. `docs/api/realtime-service.md`'s reconnect sequence makes
this a `GET` that has to return a number or the client has nothing to resume
its version check from; refusing would put an error exactly where a price
belongs. Gate a market's trade controls on the public market read's derived
status (`docs/api/market-service.md`) instead — this route's job is only to
answer what the prices are.

Errors, reusing the preview's codes:

| Status | `code` | When |
| --- | --- | --- |
| 404 | `market_not_found` | No such market — and also a draft or a submitted one, which `market_service`'s public detail endpoint refuses with the same 404. |
| 500 | `market_book_incomplete` | This service holds a book for the market that cannot be priced — no outcome rows, one of them, or a `liquidity_b` the engine cannot use. Only a hand-run repair or a half-applied migration produces it. A server fault; not worth retrying. |
| 503 | `market_terms_unavailable` | `market_service` could not be reached on a market's first touch. Worth retrying. |

## POST /ledger/markets/{market_id}/trades

[T-2] #22. Buy shares in an open market. This service's first write route.

```jsonc
// request
{
  "outcome_id": "4f2a...",
  "side": "buy",
  "quantity": "10.0000",
  "state_version": 42,
  "idempotency_key": "a client-generated string, unique to this caller"
}
```

**Takes no account and no amount.** The request model is `extra="forbid"`
over exactly these five fields — an `account_id`, an `amount`, a `total` or a
`legs` array in the body is `422`, not silently dropped. The debited account
is `accounts.ensure(USER, claims.sub)`, read from the signed token, never
from the request; the credited side is always this market's pool. That is
the whole answer ADR 0009's amendment gives to "how does a service prove it
is a service": this route accepts nothing a trader could use to move money
that is not their own. Buy only — `side` is the literal `"buy"`, and anything
else, including `"sell"`, is `422`. A sell needs [T-3] #23's per-user
holdings check under this same lock, which this ticket has nothing to check
against yet.

`quantity` is D-038's rule again: `> 0`, at most four decimal places, a
fifth is `422` rather than rounded — and at most 18 digits in all, the width
of `Numeric(18, 4)`, the same ceiling the preview's query parameter carries.
Past validation, a quantity whose cost **or whose resulting shares
outstanding** would not fit that column is `422 quantity_too_large`: the
trade writes both, and the preview refuses the same request for the same
reason. `state_version` is **required** — an
optional staleness field would let a client silently opt out of the only
staleness protection a trade has — and is compared for **strict equality**,
either direction, under the book row's own lock; a mismatch is
`409 quote_stale` with `quoted` and `current` in `error.details`, so a client
can re-preview and retry without guessing which way it was wrong.

```jsonc
// response, 201
{
  "transaction_id": "36fd...",
  "user_id": "5f3e...",
  "market_id": "9d1c...",
  "outcome_id": "4f2a...",
  "side": "buy",
  "quantity": "10.0000",
  "total": "-13.3742",
  "state_version": 43
}
```

**`total` is exactly what `GET .../preview` quoted for the same trade** —
signed negative on a buy, quantized `ROUND_CEILING` against the unsigned
cost, the same function both routes call. `state_version` is the book's
counter **after** this trade, not before it. **The response carries no
prices.** A replayed price was true once and is a lie afterwards; `total` is
what the trader was charged, for ever. Render a price from the `price` frame
or from `GET .../snapshot` instead.

### The idempotency key is derived, and a retry is answered before anything
else runs

The stored key is **not** `idempotency_key` as sent. It is
`trade:<user_id>:<market_id>:<idempotency_key>` — this route's own answer to
"The trade's idempotency key is derived by the server; the client's value is
one component of it". A verbatim key would let a trader submit a trade keyed
`signup-grant:<somebody else's user id>`, after which that user's first
balance read would find the key already present and never receive their
starting credits — silently, permanently, in a table nothing rewrites. The
derivation confines a collision to one caller in one market, which is what an
idempotency key is for; a client's string only has to be unique *to itself*.

**The order on this route is fixed, and getting it backwards is the kind of
bug that has no symptom until a market closes mid-retry.**

1. The idempotency lookup, **unlocked and first** — before the market-open
   status is even checked, and before any HTTP call. A hit is compared
   against the request (`outcome_id`, `side` and `quantity` — `quantity`
   compared numerically, so `10` and `10.0000` are the same trade) and
   returned with the **same `201`** and a byte-identical body; a mismatch is
   `409 idempotency_key_reused`. `state_version` is **not** part of that
   comparison — a client that lost its response is expected to re-preview
   before retrying, and the version it now quotes is newer, for the same
   trade.
2. Only then the gate: is this market still open? Read off
   `market_service`'s public detail endpoint, once, forwarding this
   request's own token.
3. The book row's lock, and a second idempotency check under it, before this
   request writes anything of its own.

**Why the lookup runs before the gate, not after.** A trade that committed
and whose response was lost, retried after the market closed, must not come
back `409 market_closed` — the trade already happened and charged the
trader, and the only question a retry asks is what its answer was. With the
gate first, that retry would be told its money was never spent.

**The preview and this route deliberately disagree about a closed market,
and it is not a bug.** `GET .../preview` never checks whether a market is
open — it has no lock to check under and no decision to make, so a closed
market still prices a hypothetical trade. This route decides whether a real
one may happen, and refuses `409 market_closed` on the same closed market
the preview just quoted a number for. A preview is arithmetic and fires on
every keystroke; a trade is a decision and fires once (ADR 0017).

Errors:

| Status | `code` | When |
| --- | --- | --- |
| 404 | `market_not_found` | No such market. |
| 409 | `market_closed` | The market's derived status is not `"open"`. Spelled the same way market_service spells its own version of this refusal. |
| 409 | `quote_stale` | The quoted `state_version` no longer names the book, either direction. `error.details` carries `quoted` and `current`. |
| 409 | `insufficient_funds` | This account cannot afford the trade. `error.details` carries `balance` and `required`. |
| 409 | `idempotency_key_reused` | This idempotency key already names a different trade — a different `outcome_id`, `side` or `quantity`. |
| 422 | `unknown_outcome` | `outcome_id` does not name one of this market's outcomes. |
| 422 | `quantity_too_large` | The cost, or the traded outcome's resulting shares outstanding, is above `99999999999999.9999` — the same two bounds the preview refuses at (D-040). |
| 422 | `cost_below_tick` | The buy's cost rounds to `0.0000` at the ledger's scale, so real shares would be charged nothing (D-041). |
| 422 | — | A malformed body, an extra field, `side` other than `"buy"`, or a quantity at five decimal places, `<= 0`, or wider than 18 digits. FastAPI's own validation; carries `{"detail": [...]}` rather than the `{"error": {...}}` envelope, matching the preview route's query-string validation. |
| 500 | `market_book_incomplete` | This service holds a book for the market that cannot be priced — no outcome rows, one of them, or a `liquidity_b` the engine cannot use. The same guard the preview and the snapshot use. A server fault; not worth retrying. |
| 503 | `market_terms_unavailable` | `market_service` could not be reached, or — on a market's first trade — answered with terms no book can be opened from. |

## Errors

The same envelope as the other three services:

```jsonc
{ "error": { "code": "malformed_cursor", "message": "..." } }
```

| Status | `code` | When |
| --- | --- | --- |
| 400 | `malformed_cursor` | `cursor` was not one this service issued |
| 401 | `invalid_token` | Missing, malformed or expired access token |
| 403 | `not_an_administrator` | Valid token, wrong role |
| 422 | — | FastAPI's own validation, e.g. `limit=0` |

The preview route above adds eight more of its own — `market_not_found`
(404), `insufficient_shares_outstanding` (409), `unknown_outcome` (422),
`quantity_too_large` (422), `proceeds_below_tick` (422), `cost_below_tick`
(422), `market_book_incomplete` (500) and `market_terms_unavailable` (503)
— documented there rather than repeated here. The snapshot route reuses
three of them —
`market_not_found`, `market_book_incomplete` and `market_terms_unavailable`
— for the reason its own section gives: both routes share the same cold
path.

`market_not_published` (409) is defined in `core/errors.py` and mapped like
every other domain error, and no request can currently reach it.
`books.ensure_open` raises it only for terms whose `published_at` is null, and
the public detail endpoint those terms come from answers `404` for every
market that has not been published — so a draft arrives here as
`market_not_found`, not as this. It is left in place, and out of all three routes'
declared responses, because it becomes reachable the day the ledger reads
terms from somewhere that serves unpublished markets. Do not write a handler
for it today.

`insufficient_funds` (409), `idempotency_key_reused` (409), `market_closed`
(409) and `quote_stale` (409, new in [T-2] #22) are the trade route's own,
documented in its section above. `unbalanced_transaction` (422) is
`core/errors.py`'s and no route can return it: `service/trading.py` always
builds two balanced legs, so it is a guard against a bug in this service
rather than a response any request can provoke. `pending_writes_on_replay`
(500) is the same shape one layer down, in `service/posting.py` — a caller
of the write primitive reaching its replay branch with work still pending,
which every caller this service has is built not to do.
