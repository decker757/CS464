# Ledger service API

Base URL `http://localhost:8003` in development. Interactive docs, generated
from the code and authoritative if this page ever disagrees, at
[`/docs`](http://localhost:8003/docs).

Covers [F-1] #41, the backend half of [B-1] #32, [B-2] #33 and [4.1] #13,
[T-1] #21's cost preview, the trade route that buys ([T-2] #22) and sells
([T-3] #23), [T-5] #25's trade history, [3.4] #12's settlement route, [2.2]
#6's market exposure, [L-1] #38's leaderboard, which also covers [L-3] #40,
[BE][2.4] #63's price history and trade feed, and [BE][X-1] #432's batch
snapshot with #447's activity figure.
The void route ([BE] #221) is decided by ADR 0025 and not built yet; every
part of this page that it changes says so where it does.

Why balances are derived rather than stored and why a read can write:
[ADR 0009](../adr/0009-the-ledger-write-path.md), amended by [T-2] #22 to
answer how a write route authenticates a caller. Why positions live here
rather than with markets: [ADR 0005](../adr/0005-trading-service-boundary.md).
Why the ledger asks market_service whether a market is still open, once per
trade: [ADR 0017](../adr/0017-the-ledger-and-a-stopped-market.md).
Why settlement is the ledger's request and market_service is told
afterwards: [ADR 0019](../adr/0019-settlement-across-services.md).
Why the leaderboard is a snapshot the ledger refreshes, and how it learns
usernames: [ADR 0021](../adr/0021-the-leaderboard-and-the-first-service-credential.md).
Why a flawed market is voided on the ledger first, and why a refund follows
what each share cost: [ADR 0025](../adr/0025-voiding-a-flawed-market.md).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/ledger/balances/me` | My balance, what my orders reserve, and what is available |
| GET | `/ledger/entries/me` | My ledger history |
| GET | `/ledger/portfolio/me` | My positions and their current value |
| GET | `/ledger/leaderboard` | Every ranked user's net worth, from the latest ranking |
| GET | `/ledger/users/{user_id}/balance` | Any user's balance (admin) |
| GET | `/ledger/users/{user_id}/entries` | Any user's history (admin) |
| GET | `/ledger/markets/{market_id}/preview` | What a trade would cost |
| GET | `/ledger/markets/{market_id}/snapshot` | The market's authoritative current price |
| POST | `/ledger/markets/snapshots` | The current price of many markets at once (writes nothing) |
| GET | `/ledger/markets/{market_id}/exposure` | What resolving the market would pay out (admin) |
| GET | `/ledger/markets/{market_id}/price-history` | Every price the market has had, one point per trade |
| GET | `/ledger/markets/{market_id}/trades` | The market's trades, newest first (admin) |
| POST | `/ledger/markets/{market_id}/trades` | Buy or sell shares |
| POST | `/ledger/markets/{market_id}/settlement` | Pay out an approved market (admin) |
| POST | `/ledger/markets/{market_id}/void` | Void a flawed market and refund every holder (admin; ADR 0025, not built yet) |
| POST | `/ledger/markets/{market_id}/orders` | Place a limit order (ADR 0024) |
| GET | `/ledger/orders/me` | My live limit orders (ADR 0024) |
| POST | `/ledger/orders/{order_id}/cancel` | Cancel one of my limit orders (ADR 0024) |
| POST | `/ledger/admin/orders/{order_id}/cancel` | Cancel any trader's limit order, with a reason (admin) |
| GET | `/health` | Liveness and readiness probe |

**Five routes write, and none takes money.** `POST .../trades` takes no
account, no amount and no leg, so a trader's own token is a safe credential
for it in a way it could never be for a route that accepted one. That is
[T-2] #22's answer to ADR 0009's deferred question. `POST .../orders` is the
same shape: the body names a price and a quantity, the budget is computed and
the account is the token's (ADR 0024). `POST .../cancel` takes an empty
body: the order is the caller's, and what it releases is derived from the
order row. `POST /ledger/admin/orders/.../cancel` is the same cancel for an
administrator, and its body is only a reason. `POST .../settlement` takes an
administrator's token and an empty body: the winner comes from market_service,
and the holders and amounts come from this service's own positions (ADR 0009's
second amendment). Every other
route reads, `POST /ledger/markets/snapshots` included, which is a POST only
because its input is a list of ids, and no route ever edits or removes an
entry. The ledger is
append-only, enforced by a database trigger rather than by the absence of a
route.

**ADR 0025: not built yet. A sixth write route.**
`POST .../void` takes an administrator's token and a body of one field,
`reason`. Which market may be voided comes from market_service; the holders
and the amounts come from this service's own trades and positions. It takes
no account and no amount (ADR 0009's 2026-10-20 amendment).

The balance route, the history route, the portfolio route, the preview route
and the snapshot route are each an exception to "reads don't write," and for
the same reason: a balance, a history or a portfolio mints a user's starting
grant on first read (below and [T-4] #24), and the preview and the snapshot
each open and fund a market's book on that market's first touch (D-008,
D-037, and [F-9] #112 for the snapshot). All five are once-per-subject,
idempotent, and invisible to every caller after the first. The batch snapshot
is not among them: it opens no book.

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
  "balance": "1000.0000",
  "reserved": "60.0000",
  "available": "940.0000"
}
```

`balance` is the sum of every entry on this account. There is no stored balance
for it to disagree with, which is what [B-2] #33's "derived from or reconciled
against the ledger" means here: there is nothing to reconcile.

`reserved` and `available` are ADR 0024's. `balance` keeps its meaning: the sum
of every entry, including credits that resting buy orders reserve. `reserved`
is what this user's live buy orders could still spend, summed over every
market: each order's budget less what it has spent. An order stops reserving
once it is filled or cancelled, or once its market has a result. `available` is
`balance - reserved`, and it is what a new buy, a new buy order or the trade
route can spend. All three are decimal strings at scale 4, read in one
statement. A reservation is not an entry, so the history shows no line for
placing or cancelling an order. Net worth on the portfolio and the leaderboard
still counts `balance`, reserved credits included. The admin route
`GET /ledger/users/{user_id}/balance` returns the same two fields, both
`"0.0000"` for an id with no account. `GET /ledger/portfolio/me` gains no
`reserved` credits figure: the balance carries it. Its rows carry
`reserved_shares`, the shares a live sell order holds back (#410).

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
      "id": "e41a...",
      "created_at": "2026-10-10T08:12:44.501200Z",
      "amount": "10.0000",
      "balance_after": "986.4477",
      "transaction_id": "b90f...",
      "kind": "settlement",
      "context": {
        "user_id": "5f3e...", "market_id": "2b7e...",
        "outcome_id": "c03d...", "quantity": "10.0000"
      },
      "market_id": "2b7e...",
      "outcome_id": "c03d...",
      "side": null,
      "quantity": "10.0000",
      "average_price": null
    },
    {
      "id": "0c9d...",
      "created_at": "2026-09-15T09:41:02.118000Z",
      "amount": "5.8905",
      "balance_after": "976.4477",
      "transaction_id": "8a7e...",
      "kind": "trade_sell",
      "context": {
        "market_id": "9d1c...", "outcome_id": "4f2a...", "side": "sell",
        "quantity": "10", "total": "5.8905", "state_version": 43
      },
      "market_id": "9d1c...",
      "outcome_id": "4f2a...",
      "side": "sell",
      "quantity": "10.0000",
      "average_price": "0.5891"
    },
    {
      "id": "3f61...",
      "created_at": "2026-09-15T04:21:09.412883Z",
      "amount": "1000.0000",
      "balance_after": "1000.0000",
      "transaction_id": "36fd...",
      "kind": "signup_grant",
      "context": { "user_id": "5f3e..." },
      "market_id": null,
      "outcome_id": null,
      "side": null,
      "quantity": null,
      "average_price": null
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

That holds for a user trading in two markets at once too. A movement's
timestamp is fixed after it holds the locks on its accounts, at one microsecond
past the newest entry on any of them if the clock says earlier, so on every
account the order listed is the order committed, and no two entries share a
time. #187; DECISIONS.md, "A movement's timestamp is fixed under its
account locks".

**Only this account's side appears.** Every movement writes at least two
entries sharing one `transaction_id`, and the other side belongs to the platform
or to a market pool. `transaction_id` is there so two entries of one movement can
be recognised as one event, not so you can fetch the other half; there is no
route that returns it.

`kind` is the vocabulary of why credits moved. The values that reach a user's
own history today are `signup_grant`, `trade_buy`, `trade_sell` and
`settlement`, a winning holder's payout, which the settlement route below
writes ([3.4] #12). A holder of only losing shares is paid nothing, and gets
no row.
`market_seed` and
`settlement_residue` exist as kinds too, but never reach here: each moves
credits between the platform and a market's pool, touching no USER account.
**Treat an unrecognised value as opaque
rather than as an error** — new ones will appear without a version bump. Same
rule for `context`: render what you recognise, ignore the rest.

**`market_id`, `outcome_id` and `quantity` are set on a `trade_buy`,
`trade_sell` or `settlement` row. `side` and `average_price` are set on the two
trade kinds only. All five are `null` on every other kind**, which means the
grant and any kind this service does not recognise. `side` comes from
`kind` rather than from `context`. `quantity` is at scale 4 whatever the
trader sent: `context.quantity` keeps the value exactly as it arrived (a sell
of `10` stays `"10"` there), and this field is that value quantized.
`average_price` is the average fill price — `|amount| ÷ quantity`,
`ROUND_HALF_UP` at scale 4 — and equals the preview's `average_price` for the
same trade at the same `state_version`. **It is not the marginal price** the
portfolio and the snapshot report; it is what this trade actually averaged.

On a `settlement` row, `outcome_id` is the winning outcome and `quantity` is
the number of winning shares paid out, at one credit each, so `amount` equals
`quantity`. `side` and `average_price` are `null` there: a payout is not a
trade, and its price is always one.

**ADR 0025: not built yet. `void_refund` rows.** A void writes one
`void_refund` row for each position it refunds: `amount` is the refund,
positive, and `market_id`, `outcome_id` and `quantity` (the shares refunded)
are set, as on a `settlement` row. `side` and `average_price` are `null`: a
refund is not a trade, and it pays back what the shares cost, not a price per
share. A position whose refund rounded down to `0.0000` gets no row.
`void_residue` is a kind too, and never reaches a user's history: it moves
credits between the platform and the market's pool. `context` carries
`user_id`, `market_id`, `outcome_id` and `quantity`.

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

## GET /ledger/portfolio/me

[T-4] #24, [3.4] #12 for settled markets, and [B-1] #359 for the profit and
loss figures. The caller's own positions and
their current value: what they
hold in every market they have traded, each valued the way
[ADR 0018](../adr/0018-positions-are-valued-at-liquidation.md) requires
rather than at the marginal price, and what the lot comes to against the
credits they started with.

Any valid access token — a trader reads their own portfolio and nothing
else's; there is no admin variant.

```jsonc
{
  "user_id": "5f3e...",
  "account_id": "a71c...",
  "balance": "572.5121",
  "positions_value": "431.3568",
  "net_worth": "1003.8689",
  "starting_credits": "1000.0000",
  "realized_pnl": "3.8690",
  "unrealized_pnl": "-0.0001",
  "total_pnl": "3.8689",
  "positions": [
    {
      "market_id": "2b7e...",
      "outcome_id": "c03d...",
      "outcome_position": 0,
      "quantity": "10.0000",
      "reserved_shares": "0.0000",
      "cost_basis": "6.1310",
      "average_entry_price": "0.6131",
      "price": null,
      "value": null,
      "unrealized_pnl": null,
      "result": "paid_out",
      "payout": "10.0000",
      "state_version": 57
    },
    {
      "market_id": "9d1c...",
      "outcome_id": "4f2a...",
      "outcome_position": 0,
      "quantity": "500.0000",
      "reserved_shares": "120.0000",
      "cost_basis": "431.3569",
      "average_entry_price": "0.8627",
      "price": "0.9933",
      "value": "431.3568",
      "unrealized_pnl": "-0.0001",
      "result": null,
      "payout": null,
      "state_version": 1
    }
  ]
}
```

Flat, not paginated: `positions_value` and `net_worth` are sums over every
row, and paging the list either pages the totals or defeats its own point.
Only rows with `quantity > 0` are shown, ordered by `market_id` then
`outcome_position`; a position sold to zero keeps its row in the database but
is left out here.

**`value` is not `quantity × price`.** `price` is the outcome's marginal
price — informational only, and equal to the snapshot's price for the same
`state_version` — but nobody can sell at it: a position of any real size
moves the book as it sells, and there is no other buyer for it. `value` is
what selling the whole position now would actually credit, computed by
`core/pricing.py::liquidation_values_of` against this market's own book. See
ADR 0018 for the worked example where the two rules disagree by 65 credits
on a single trade.

**Where a caller holds more than one outcome of one market, the outcomes are
sold in one sequence, not each against the current book.** They are sold in
`outcome_position` order, each against the book the previous sale left —
ADR 0018's "holdings in one market are sold together". A market's only held
position equals the trade route's proceeds for that sell at the same
`state_version`; a second held outcome in the same market is priced on a book
no preview can quote, because the first sale would have to happen first.

**A settled market's rows show what it paid, not a value.** Once this service
has settled a market (`POST /ledger/markets/{id}/settlement`, below), each of
its rows carries a `result` and a `payout`. `result` is `paid_out` for the
winning outcome and `worthless` for any other. `payout` is the row's
`quantity` on a `paid_out` row, at one credit per share, and `"0.0000"` on a
`worthless` one. `price`, `value` and `unrealized_pnl` are `null`, and the row
adds nothing to `positions_value`: the payout is already in `balance`, so
`net_worth` counts it once. `quantity`, `cost_basis`, `average_entry_price` and
`state_version` are unchanged, because settlement writes no position and no
`q`.

On every other row, `result` and `payout` are `null`. Every row carries all the
keys, so branch on `result`, not on which keys are present. **`price`, `value`
and `unrealized_pnl` can be `null`.** Guard them before formatting.

**ADR 0025: not built yet. A voided market's rows show the refund.**
Once this service has voided a market (`POST /ledger/markets/{id}/void`,
below), each of its rows carries `result: "refunded"`, and `payout` is what
the void refunded for that position, read from its `void_refund` entry. It is
`"0.0000"` for a position whose refund rounded down to zero. It is not one
credit per share, and it can differ from `cost_basis`: a refund follows what
each share still held cost, newest shares sold first, and `cost_basis` is the
average. `price`, `value` and `unrealized_pnl` are `null` and the row adds
nothing to `positions_value`, exactly as on a settled row. `realized_pnl`
counts the refund like any credit, through `balance`.

```jsonc
{
  "market_id": "7a1b...",
  "outcome_id": "e2c4...",
  "outcome_position": 0,
  "quantity": "2.0000",
  "reserved_shares": "0.0000",
  "cost_basis": "1.6626",
  "average_entry_price": "0.8313",
  "price": null,
  "value": null,
  "unrealized_pnl": null,
  "result": "refunded",
  "payout": "1.4337",
  "state_version": 2
}
```

**`quantity` is the whole position; `reserved_shares` is the part of it that
cannot be sold.** #410. `quantity` counts every share held, including the ones
a resting sell order is waiting to sell, because those are still the caller's
until a fill takes them. `reserved_shares` is what the caller's live sell
orders of that outcome still hold back: the sum of their
`quantity - filled_quantity`, which is the order object's own
`reserved_shares` summed per outcome. A sell, instant or a new sell order, can
take `quantity - reserved_shares`, and the trade route refuses anything more
with `insufficient_shares_held`, whose `details` carry the same `reserved`. So a
sell control's maximum is `quantity - reserved_shares`, never the whole
position. The figure is a decimal string at scale 4, `"0.0000"` where no live
sell order holds any, and never null. A settled row always reports
`"0.0000"`, because no order on a market with a result is live. It is derived
from the order rows on every read, in the same statement as everything else,
and never stored. The rest of the row is unchanged by it: `value`,
`cost_basis` and `average_entry_price` count the whole `quantity`, since a
resting sell has not sold anything yet (ADR 0018's 2026-10-17 amendment).

`unrealized_pnl` is `value - cost_basis`. `average_entry_price` is
`cost_basis / quantity`, `ROUND_HALF_UP` at scale 4, and is display only, the
same rule the preview's `average_price` follows.

**The four portfolio-level figures answer "am I up or down".** Every amount
here crosses the wire as a decimal string, and a browser must not do
arithmetic on those, so the endpoint does the sums:

| Field | What it is |
| --- | --- |
| `starting_credits` | What this user's signup grant actually paid them, read from that transaction's own entry. |
| `realized_pnl` | Profit and loss already banked in `balance`: what the sells paid less the cost basis they released, plus what settlement paid less the basis of the holdings it settled. |
| `unrealized_pnl` | The sum of the rows' own `unrealized_pnl` — what is still riding on positions nobody has closed. A settled row contributes nothing. |
| `total_pnl` | `realized_pnl + unrealized_pnl`. |

**`total_pnl` is `net_worth - starting_credits`, exactly.** That identity is
how the four figures reconcile with the three above them, and it holds at
scale 4 with no rounding: `realized_pnl` is
`balance - starting_credits + the cost basis of every open position`, and
`unrealized_pnl` is `positions_value` less that same basis, so the basis
cancels. Rendering `total_pnl` beside `net_worth` and `starting_credits` can
never show a figure that does not add up.

**Nothing here is stored.** There is no P&L column and no balance column:
every figure is derived from `ledger.entries` and the positions on each read,
the same way `balance` is. A settled market's payout is in `balance` and its
basis is closed, which is why its shares are banked rather than valued —
counting them in `unrealized_pnl` as well would count the market twice.

**`starting_credits` is the grant that was minted, not the `STARTING_CREDITS`
setting.** Changing that setting never rewrites an entry (ADR 0009), so a user
granted under the old value keeps reporting what they were actually given, and
their P&L does not move when the deployment is reconfigured.

**The balance, every position, every settlement record and every live sell
order are read together, in one statement, with no lock.** So a trade
committing mid-read cannot show its debit without its shares, or its shares
without its debit, and a fill cannot show its position on one side of the read
and its order's reservation on the other. A settlement
committing mid-read cannot put its payout in `balance` while its shares are
still valued. The read makes no call to market_service. Valuation needs only
`q`, `b` and the holding, and a settled row needs only this service's own
settlement record. So a closed or approved market is valued at its last traded
book until this service settles it, and market_service's status changes
nothing here.

**The first call for a user also mints their starting grant**, exactly as
`/balances/me` does (above).

Errors:

| Status | `code` | When |
| --- | --- | --- |
| 401 | `invalid_token` | Missing, malformed or expired access token. |
| 500 | `market_book_incomplete` | A held market this service has not settled has a book that cannot be priced, or a position in it exceeds its outcome's shares outstanding. A settled market's book is not valued, so it cannot cause this. The whole read fails, and no partial portfolio is returned. |

## GET /ledger/leaderboard

[L-1] #38, which also covers [L-3] #40. Every ranked user's rank, username
and net worth, read from the most recent ranking the ledger has computed.
Why it is a stored snapshot, and how the ledger learns usernames:
[ADR 0021](../adr/0021-the-leaderboard-and-the-first-service-credential.md).

Any valid access token. Traders and administrators see the same board.

| Query | Default | Notes |
| --- | --- | --- |
| `limit` | 50 | Clamped at 200. Asking for more is not an error. |
| `cursor` | — | The `next_cursor` from the previous page, unmodified. |

```jsonc
{
  "computed_at": "2026-10-11T09:14:00.123456Z",
  "stale": false,
  "ranked_users": 214,
  "me": { "rank": 3, "username": "Ihsan", "net_worth": "1003.8689" },
  "rows": [
    { "rank": 1, "username": "michelle_l", "net_worth": "1431.3568", "is_me": false },
    { "rank": 2, "username": "ernest_t",   "net_worth": "1003.8689", "is_me": false },
    { "rank": 3, "username": "Ihsan",      "net_worth": "1003.8689", "is_me": true }
  ],
  "next_cursor": "MmY0ZS4uLnwz",
  "has_more": true
}
```

**`net_worth` is the portfolio's `net_worth`, computed the same way.** It is
balance plus the liquidation value of every unsettled position, from the same
code as `GET /ledger/portfolio/me` (ADR 0018), so a settled market counts
once, through its payout. It is a decimal string, like every amount here. It
can differ from the user's live portfolio by whatever has traded since
`computed_at`.

**The board is recomputed every 60 seconds, not on every trade.**
`computed_at` is when the ranking that this page came from began. Show it as
"Updated …". Every user on every page of one ranking was valued from the same
instant, so a trade that commits mid-refresh is in the whole ranking or in
none of it. The ranking this route serves is refreshed at startup and then
every `LEADERBOARD_REFRESH_SECONDS` (default 60); a failed refresh writes
nothing and the previous ranking stays, marked stale once it is more than two
intervals old.

**`stale: true` means the board has not been refreshed recently.** It is
true when `computed_at` is more than two refresh intervals old (120 seconds
by default), which means no refresh has completed recently. The ranking shown
is still the last complete one. Nothing partial is ever served. Show the
board with a "may be out of date" note, not an error. Before the ledger has
ever completed a refresh, the answer is `computed_at: null`,
`stale: true`, `rows: []`, `ranked_users: 0`, `me: null`.

**`ranked_users` is how many users the whole ranking holds**, not how many
are on this page. It is the same on every page of one ranking. Use it for
"of 214" and for #39's not-enough-data state.

**Who is ranked.**
- Every user who has a ledger account: anyone who has read their own
  balance, history or portfolio, or traded. That includes administrators.
- A user with no trades is ranked at their balance.
- A user who has never been granted credits is not on the board yet. They
  appear on the first refresh after their account exists.
- A suspended account is left out, and the ranks close up: there is no gap
  where it was. It reappears on the first refresh after the suspension is
  lifted.

**Ranks run 1..N with no ties.** The order is `net_worth` descending, then
username ascending ignoring case, so equal net worths get consecutive ranks
in alphabetical order. That is why `ernest_t` is above `Ihsan` above. Do not
re-sort the rows. `rank` is the order.

**`me` is the caller's own row in the ranking this page came from, or
`null`.** It has the same three fields as a row: `rank`, `username` and
`net_worth`. Their values are the caller's row in that ranking, character
for character, whether or not that row is on this page. `me` comes from the
ranking the page is served from, not the newest one. So every page of one
walk carries the same `me`, including a page reached by a cursor into the
previous ranking.

**`me` is `null` when the caller is not in that ranking.** That happens when:
- no ranking exists yet;
- the caller has no ledger account yet (see "Who is ranked"). Reading the
  leaderboard does not create one;
- the caller's account was created after the ranking was computed;
- the caller was suspended when the ranking was computed.

The response does not say which. `null` is not an error, and the answer is
still `200`. "Suspended" means suspended when the ranking was computed. A
suspension after that leaves `me` set until the next refresh drops the
caller from the ranking.

**"No ranking yet" and "not ranked yet" are different answers.**

| `computed_at` | `me` | Show |
| --- | --- | --- |
| `null` | `null` | No ranking yet. Nothing is ranked, so show #39's insufficient-data state for the whole board. |
| set | `null` | Not ranked yet. Show the board, with no rank for the caller. |
| set | an object | The caller's rank. |

**`is_me` is on every row.** It is `true` on the caller's own row and
`false` on every other row. A page has at most one `true`, and none when the
caller's row is on another page or in no ranking. Use it to highlight the
row. Do not compare usernames. When a row has `is_me: true`, its `rank`,
`username` and `net_worth` equal `me`'s.

**No user id is sent.** No user id, email or account id appears on this
route. The server matches the caller to their row by the access token. `me`
and the one row with `is_me: true` identify the caller only to the caller.

**A cursor holds you to one ranking.** Every page reached through
`next_cursor` comes from the same ranking as the first page, even if a
refresh has happened since. `stale` is recalculated on every request against
that request's clock, so two pages of one walk can differ in `stale` even
though they share `computed_at`. The ledger keeps the ranking before the
current one. A cursor survives one refresh; after the second it is
`409 leaderboard_refreshed`. Drop the cursor and load page one again. Do not
show the older pages beside the new one.

```js
async function loadPage(cursor) {
  const url = `/ledger/leaderboard?limit=50${cursor ? `&cursor=${cursor}` : ""}`;
  const res = await fetch(url, { credentials: "include" });
  if (res.status === 409) {
    // leaderboard_refreshed: this is page one of a new ranking.
    // Replace the whole list with it; do not append it to what is shown.
    return { ...(await loadPage(null)), restarted: true };
  }
  return res.json();
}
```

**This route writes nothing.** Unlike the balance, the history and the
portfolio, reading the leaderboard does not mint the caller's starting
grant.

Errors:

| Status | `code` | When |
| --- | --- | --- |
| 400 | `malformed_cursor` | `cursor` was not one this service issued. |
| 401 | `invalid_token` | Missing, malformed or expired access token. |
| 409 | `leaderboard_refreshed` | The cursor's ranking has been replaced twice since its first page. A cursor whose ranking no longer exists, or never did, is also `409 leaderboard_refreshed`. Start again from page one and replace the list. |
| 422 | `invalid_request` | For example `limit=0`. |

## GET /ledger/users/{user_id}/balance and /entries

[4.1] #13. The same responses, for an administrator investigating an anomaly,
except for an id the ledger has no account for (below). `403` for a non-admin.

**These two routes mint nothing.** #188.
This service holds no user table, so it cannot tell a user's id from a
market's, from the platform owner's `00000000-0000-0000-0000-000000000000`, or
from a typo — and a grant made to any of them would be real, permanent credit
that nobody owns. So an id the ledger has never opened an account for reads as
a zero balance with a **null `account_id`**, and an empty history, with `200`:

```jsonc
{
  "user_id": "5f3e...",
  "account_id": null,
  "balance": "0.0000"
}
```

A brand new user's starting credits appear here after the user's own first
read of `/balances/me`, `/entries/me` or `/portfolio/me`, which is where the
grant is minted. `account_id` is never null on `/balances/me`. A malformed
`cursor` is still `400 malformed_cursor`, account or no account.

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
  "payout_if_wins": "10.0000",
  "profit_if_wins": "2.6848",
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

**`payout_if_wins` and `profit_if_wins` answer "what do I get if I am
right"**, for this trade alone and never the position it adds to. On a buy,
`payout_if_wins` is the quantity at scale 4, because a winning share pays one
credit, and `profit_if_wins` is that payout minus the credits the buy costs:
`payout_if_wins + total`, exact, with no rounding of its own. In the example,
`10.0000 - 7.3152 = 2.6848`. Render both as they arrive; do not recompute
either from `quantity` or `average_price`. On a buy charged its whole
quantity, such as the smallest buys or a buy of an outcome priced near one,
the profit can be `0.0000` or a tick below it.

On a **sell** both are `null`: a sell is paid its `total` now and gives up the
shares that would have paid out, so it has nothing to receive if the outcome
wins.

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

**ADR 0024 (Accepted). `post_trade_prices` ignores resting orders.** The
preview runs no cascade. `total`, `average_price` and `prices` are what the
trade will be charged against, because the trade is priced before any resting
order fills. `post_trade_prices` is the price after this trade
alone. When resting orders fill in the trade's cascade, the market ends closer
to where it started than `post_trade_prices` says. Render the end price from
the `price` frame at the trade response's `book_state_version`, not from the
preview.

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
moved `q` took its price, under the book lock. Its ledger entries can carry a
slightly later `created_at`, because they are stamped under the account locks
(#187). On a market nobody has traded that equals `opened_at`
(D-029) — creating the book was the last state change there has been.

**The first request on a market writes, and can take a moment.** A market
nobody has previewed, traded or snapshotted yet has no book on this service.
This route opens one exactly as the preview does: it fetches the market's
terms from `market_service`, funds the pool from the platform account, and
only then prices the market — once per market, ever. That request can take
up to the market-terms timeout. Every request after it, for that market, is a
single indexed read that writes nothing. Debouncing is not this route's
concern the way it is the preview's — a client fetches it when a page opens and
again on every `subscribed` acknowledgement from the realtime socket (so once
more per reconnect), not on every keystroke — but the cold-path cost is
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

## POST /ledger/markets/snapshots

[X-1] #432. The current price of many markets in one request, for a list of
cards: the browse page's first paint and its refresh, with how much each market
has traded ([X-1] #447). One call replaces a snapshot request per card. A POST
because its input is a list, not because it writes: it writes nothing.

Any valid access token, any role, exactly like the single snapshot above.

```jsonc
// request
{ "market_ids": ["9d1c...", "a50a...", "77e0..."] }

// response: "77e0..." has no book here, so it is absent
{
  "snapshots": [
    {
      "market_id": "9d1c...",
      "state_version": 42,
      "prices": [
        { "outcome_id": "4f2a...", "position": 0, "price": "0.6236" },
        { "outcome_id": "b7e1...", "position": 1, "price": "0.3764" }
      ],
      "occurred_at": "2026-09-15T09:12:44.318000+00:00",
      "prices_7d_ago": [
        { "outcome_id": "4f2a...", "position": 0, "price": "0.5200" },
        { "outcome_id": "b7e1...", "position": 1, "price": "0.4800" }
      ],
      "credits_traded": "1287.4410"
    },
    {
      "market_id": "a50a...",
      "state_version": 0,
      "prices": [
        { "outcome_id": "0c33...", "position": 0, "price": "0.5000" },
        { "outcome_id": "e9d4...", "position": 1, "price": "0.5000" }
      ],
      "occurred_at": "2026-10-20T08:00:03.112000+00:00",
      "prices_7d_ago": null,
      "credits_traded": "0.0000"
    }
  ]
}
```

**Each item is the single snapshot's body** for that market, field for field,
**plus `prices_7d_ago` and `credits_traded`**, so it renders with the same
function as the single snapshot and the `price` socket frame. Every other
field means what it means there. Neither of those carries the two extra
fields.

**`prices_7d_ago`** ([X-1] #446) is each outcome's price as it stood seven
days before the request, in the shape and order of `prices`: the price after
the market's last trade at or before that moment. A card's change is `price`
minus its `prices_7d_ago` entry with the same `outcome_id`. The window is
seven days, fixed, and the moment is the ledger's clock at the request.

- **Null when the market has no trade that old**: never traded, or traded
  only within the last seven days. A book that was open then stood at its seed
  price, which nobody chose, so no change is measured from it. Show no delta.
- **Equal to `prices` when the market has not traded since**: a real change
  of zero over the window, not missing data.
- Null too, and logged, when the ledger cannot rebuild the price: a market
  whose trades skip or reorder a `state_version` (price history answers it
  with `500 price_history_inconsistent`), or whose book has lost an outcome
  row a trade was made on. Its `prices` are unaffected, and so is every other
  market in the answer.

**`credits_traded`** ([X-1] #447) **is how much the market has traded**:
every credit its trades have moved, a buy's cost and a sell's proceeds each
counted as a positive amount, a resting order's fills included, whether on
arrival or in another trade's cascade. A settlement payout is not a trade and
does not count. It is an exact decimal string at scale 4, like every amount
here, and only ever rises. A market whose book nobody has traded is answered
with `"0.0000"`, never left out: absent means only that this service holds no
book for it, as below. To tell how many trades a market has had, read
`state_version`, which rises by one per trade and per fill.

**At most 200 ids.** 200 is `GET /public/markets`' largest page
(`docs/api/market-service.md`), so any page of the browse list fits in one
request. The cap counts ids as sent, repeats included. A 201st is
`422 invalid_request`, with `error.details` naming `["body", "market_ids"]`:
split the request. An empty list is a `200` with `"snapshots": []`.

**A market is answered only if this service already holds its book.** An id
with no book is absent from `snapshots`, not an error: an id that names no
market, a draft, and a published market nobody has previewed, traded or
snapshotted all read the same way, because a book is only ever opened for a
published market. Unlike the single snapshot, **this route opens no book**: it
writes nothing and never calls market_service, so it costs two indexed reads
however many of its markets are cold. To price a still-trading market the
batch left out, ask the single snapshot for it, which opens its book once;
from then on the batch answers it.

A book this service holds but cannot price, which the single snapshot refuses
with `500 market_book_incomplete`, is absent here too and logged, so one
damaged market costs only its own card. A book that can be priced but whose
trades cannot be replayed keeps its card and loses only `prices_7d_ago`
(above).

**One statement reads every market's book**, with no lock, so each
snapshot's `state_version` is the version its `prices` were computed from, as
on the single route, and its `credits_traded` is the figure at that same
version. **One more reads their trades** up to seven days ago, for
`prices_7d_ago`; however many markets are asked about, the route sends those
two. Each market is answered once, in the order its id first appears
in `market_ids`. Like the single snapshot, it never checks whether a market is
still open (ADR 0017): a closed or settled market is answered with its last
price.

**ADR 0025: not built yet. A voided market is answered the same way**, with
its last `prices`, its `prices_7d_ago` and its `credits_traded`. A void writes
no trade, so `credits_traded` still counts every trade, refunded or not,
and never falls. A card shows a voided market as "Voided", with no
probability and no change ("Notes for the void screen" in
`market-service.md`).

The answer is an object around a list of objects so that fields can be added
to it, and to each item, without breaking a client that ignores fields it does
not know.

Errors:

| Status | `code` | When |
| --- | --- | --- |
| 401 | `invalid_token` | Missing, malformed or expired access token. |
| 422 | `invalid_request` | The body has no `market_ids`, an id in it is not a UUID, or it holds more than 200 ids. `error.details` names each one. |

## GET /ledger/markets/{market_id}/exposure

[2.2] #6. What resolving a market would cost: for each outcome, the shares
outstanding, what the market would pay if that outcome won, and what the pool
would have left over. Read before resolving, by any administrator — not only
the market's creator. `403` for a trader.

```jsonc
{
  "market_id": "9d1c...",
  "opened_at": "2026-10-08T02:15:09.441000+00:00",
  "state_version": 1,
  "seed_subsidy": "50.0000",
  "pool": "481.3569",
  "payout_exceeds_pool": true,
  "result": null,
  "winning_outcome_id": null,
  "outcomes": [
    {
      "outcome_id": "4f2a...",
      "position": 0,
      "shares_outstanding": "500.0000",
      "payout_if_wins": "500.0000",
      "residue_if_wins": "-18.6431"
    },
    {
      "outcome_id": "b7e1...",
      "position": 1,
      "shares_outstanding": "0.0000",
      "payout_if_wins": "0.0000",
      "residue_if_wins": "481.3569"
    }
  ]
}
```

That is a two-outcome market with `b = 100`, seeded with 50, after one buy of
500 shares of the first outcome, which charged 431.3569. If the first outcome
wins, the pool pays 500 and is 18.6431 short, which the platform covers; so
the flag is up. If the second wins, nobody holds it, nothing is paid, and the
whole pool goes back to the platform.

| Field | Meaning |
| --- | --- |
| `opened_at` | When this service opened the market's book. `null` means it never has (below). |
| `state_version` | The same counter the snapshot and every price frame report: which trade this read reflects. `null` when there is no book. |
| `seed_subsidy` | What the platform put up at book creation. |
| `pool` | What the market pool holds now: the seed plus every buy, minus every sell. Derived from the pool's entries, like every balance. `null` once the market is settled. |
| `payout_exceeds_pool` | True when any outcome's payout is strictly greater than `pool`, i.e. any `residue_if_wins` is negative. Always `false` once the market is settled. |
| `result` | `null` until the ledger records how the market ended; then that record's kind, today always `"settled"`. |
| `winning_outcome_id` | `null` until the market is settled; then the outcome that paid. |
| `shares_outstanding` | Every trader's shares in this outcome, summed. |
| `payout_if_wins` | One credit per share outstanding. `null` once the market is settled. |
| `residue_if_wins` | `pool − payout_if_wins`. Positive goes to the platform at settlement; negative is what the platform tops up. `null` once the market is settled. |

Money and shares are decimal strings at scale 4, as everywhere in this service.
`outcomes` carries every outcome, ordered by `position`. The largest payout is
the largest `payout_if_wins`; there is no separate field for it.

**The flag is against the pool, not the seed.** The seed is the least a pool
can hold before settlement, so comparing against it would flag almost every
traded market (DECISIONS.md, "Exposure flags a payout the pool cannot cover,
not one above the seed").

**A market with no book reads as empty, with `200`.** A published market
nobody has previewed, traded or snapshotted has no book here yet, and this
route does not open one — an admin read writes nothing:

```jsonc
{
  "market_id": "9d1c...",
  "opened_at": null,
  "state_version": null,
  "seed_subsidy": null,
  "pool": null,
  "payout_exceeds_pool": false,
  "result": null,
  "winning_outcome_id": null,
  "outcomes": []
}
```

This service cannot tell that market from an id that names no market at all,
so both read this way. Show "no trading yet" and take the seed from
market_service's detail if the panel wants it.

**It does not check the market's status**, like the snapshot. An open, closed,
pending or approved market reads the same way, which is what lets an admin
watch exposure grow while it is still trading. One statement reads `q` and the
pool together, so `residue_if_wins` is never off by a trade that committed
between two reads.

**A settled market reads its result, and no payouts.** Settlement pays the
winners from the pool and empties it, but leaves the shares where they were.
Figures computed against that empty pool would show every outcome short by its
whole payout, so once the ledger has recorded a result, `pool`, every
`payout_if_wins` and every `residue_if_wins` are `null` and the flag is
`false`. `shares_outstanding`, `seed_subsidy`, `opened_at` and `state_version`
are still reported:

```jsonc
{
  "market_id": "9d1c...",
  "opened_at": "2026-10-08T02:15:09.441000+00:00",
  "state_version": 1,
  "seed_subsidy": "50.0000",
  "pool": null,
  "payout_exceeds_pool": false,
  "result": "settled",
  "winning_outcome_id": "b7e1...",
  "outcomes": [
    {
      "outcome_id": "4f2a...",
      "position": 0,
      "shares_outstanding": "500.0000",
      "payout_if_wins": null,
      "residue_if_wins": null
    },
    {
      "outcome_id": "b7e1...",
      "position": 1,
      "shares_outstanding": "0.0000",
      "payout_if_wins": null,
      "residue_if_wins": null
    }
  ]
}
```

That is the first example's market, settled for its second outcome. Branch on
`result`, not on whether `pool` is null: a market with no book has a null
`pool` too. What the settlement actually paid is in its own response and its
audit entry (DECISIONS.md, "A settled market's exposure reads its result, and
reports no payouts against the drained pool").

**ADR 0025: not built yet. A voided market reads the same way.**
`result` is `"voided"` and `winning_outcome_id` is `null`. `pool`, every
`payout_if_wins` and every `residue_if_wins` are `null`, and the flag is
`false`: a void empties the pool as settlement does. What it refunded is in
the void's own response and its audit entry.

| Status | `code` | When |
| --- | --- | --- |
| 401 | `invalid_token` | Missing, malformed or expired access token. |
| 403 | `not_an_administrator` | Valid token, not an administrator. |
| 422 | `invalid_request` | `market_id` is not a UUID. `error.details` names the field; see [Errors](#errors). |

No `404`, no `503`: the route asks market_service nothing.

## POST /ledger/markets/{market_id}/trades

[T-2] #22, [T-3] #23. Buy or sell shares in an open market. This service's first write route.

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
`legs` array in the body is `422`, not silently dropped. The trader's
account is `accounts.ensure(USER, claims.sub)`, read from the signed token,
never from the request, and the other leg is always this market's pool. On
a buy the trader is debited and the pool credited; on a sell, the reverse.
That is the whole answer ADR 0009's amendment gives to "how does a service
prove it is a service": this route accepts nothing a trader could use to move
money that is not their own. `side` is `"buy"` or `"sell"`, and anything else is
`422`.

**A sell is checked against the caller's own position** in that outcome, under
the same book lock, and refused `409 insufficient_shares_held` if it is larger
— including a caller with no position at all, who has `held: "0.0000"`. It is
not checked against the market's shares outstanding, and a position in one
outcome cannot fund a sell of another. A sell releases cost basis at average
cost and leaves a position sold to zero as a `0.0000` row. A trader with a zero
balance can still sell, since a sell credits them.

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
  "state_version": 43,
  "book_state_version": 43
}
```

**`total` is exactly what `GET .../preview` quoted for the same trade** —
signed negative on a buy, quantized `ROUND_CEILING` against the unsigned
cost, and positive on a sell, quantized `ROUND_FLOOR` against the unsigned
proceeds, the same function both routes call. `state_version` is the book's
counter **after** this trade, not before it. **The response carries no
prices.** A replayed price was true once and is a lie afterwards; `total` is
what the trader was charged, for ever. Render a price from the `price` frame
or from `GET .../snapshot` instead.

**ADR 0024 (Accepted). Three changes to this route, all built.**

- The response gains `book_state_version`, a JSON number: the book's counter
  after this trade **and** every resting order it filled. `state_version` is
  still the counter after this trade alone. With no resting orders filled, the
  two are equal, and a trade recorded before this field existed returns its
  `state_version` there. Wait for the `price` frame at `book_state_version`:
  a trade that fills resting orders publishes one price frame, at that
  version, and none for the versions in between. Each fill also sends its
  order's owner an `order` frame (`docs/api/realtime-service.md`, "Order
  updates"). The response still carries no prices.
  Built.
- `insufficient_funds` is checked against `available`, not `balance`: credits
  reserved by your own buy orders cannot pay for a trade. It is `available`
  before the trade, even when the trade's cascade then fills one of your buy
  orders and frees what it held. `error.details` gains `reserved` and
  `available` beside `balance` and `required`. Built.
- `insufficient_shares_held` is checked against your position less the shares
  your live sell orders in that outcome reserve. `error.details` gains
  `reserved` beside `held` and `requested`. Built.

The errors table below describes the route as built today. Its
`insufficient_funds` and `insufficient_shares_held` rows both count
reservations.

A trade can fill your own resting orders. That is allowed, at each order's
own limit, and none of it pays for the trade that triggered it: the trade has
to be affordable before any resting order fills.

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
2. Then: is this account still allowed to trade? Asked of the auth
   service's `GET /auth/me`, once, forwarding this request's own token
   ([ADR 0022](../adr/0022-the-ledger-and-a-suspended-trader.md)). A
   suspended account is refused `403 account_suspended` here, before the
   market is looked at, so it hears about itself rather than about the
   market.
3. Only then the gate: is this market still open? Read off
   `market_service`'s public detail endpoint, once, forwarding this
   request's own token.
4. The book row's lock, and a second idempotency check under it, before this
   request writes anything of its own.

**Why the lookup runs before the gate, not after.** A trade that committed
and whose response was lost, retried after the market closed, must not come
back `409 market_closed` — the trade already happened and charged the
trader, and the only question a retry asks is what its answer was. With the
gate first, that retry would be told its money was never spent.

**The preview and this route deliberately disagree about holdings too.** The
preview does not check them, so it can quote a sell that this route then
refuses `409 insufficient_shares_held`.

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
| 401 | `invalid_token` | Missing, malformed or expired access token. |
| 403 | `account_suspended` | An administrator has suspended this account. The same code and message the auth service uses, so one handler covers both: end the session and show the message. Not retryable. A refused trade writes nothing, and the account's balance and positions stay as they were. |
| 404 | `market_not_found` | No such market. |
| 409 | `market_closed` | The market's derived status is not `"open"`. Spelled the same way market_service spells its own version of this refusal. |
| 409 | `quote_stale` | The quoted `state_version` no longer names the book, either direction. `error.details` carries `quoted` and `current`. |
| 409 | `insufficient_funds` | A buy this account's `available` credits, its balance less what its buy orders reserve, cannot cover. `error.details` carries `balance`, `required`, `reserved` and `available`, decimal strings at scale 4: `available` is `balance - reserved`, and the trade needed `required`. |
| 409 | `insufficient_shares_held` | A sell larger than this caller's position in this outcome less the shares their live sell orders in it reserve. `error.details` carries `held`, the whole position, `reserved` and `requested`, decimal strings at scale 4. |
| 409 | `idempotency_key_reused` | This idempotency key already names a different trade — a different `outcome_id`, `side` or `quantity`. |
| 422 | `unknown_outcome` | `outcome_id` does not name one of this market's outcomes. |
| 422 | `quantity_too_large` | A buy whose cost, or whose resulting shares outstanding, is above `99999999999999.9999` — the same two bounds the preview refuses at (D-040). |
| 422 | `cost_below_tick` | A buy whose cost rounds to `0.0000` at the ledger's scale, so real shares would be charged nothing (D-041). |
| 422 | `proceeds_below_tick` | A sell whose proceeds round to `0.0000`. Ask for more. |
| 422 | `invalid_request` | A malformed body, an extra field, `side` other than `"buy"` or `"sell"`, or a quantity at five decimal places, `<= 0`, or wider than 18 digits. `error.details` names each field; see [Errors](#errors). |
| 500 | `market_book_incomplete` | This service holds a book for the market that cannot be priced — no outcome rows, one of them, or a `liquidity_b` the engine cannot use. The same guard the preview and the snapshot use. A server fault; not worth retrying. |
| 500 | `cascade_fill_refused` | The overdraft check refused one of the resting orders this trade filled, which ADR 0024 makes impossible. Nothing was written and the ledger logged it. A server fault; not worth retrying. |
| 503 | `market_terms_unavailable` | `market_service` could not be reached, or — on a market's first trade — answered with terms no book can be opened from. |
| 503 | `auth_unavailable` | The auth service could not be reached, or did not answer whether this account may trade. Nothing was written. Worth retrying. |

**A suspended account is refused on this route only.** The preview, the
snapshot, the balance, the history and the portfolio decide nothing, and keep
answering until the access token expires (ADR 0003's window, which ADR 0022
narrows for trades alone).

## Limit orders — ADR 0024 (Accepted): built

[T-6] #26, [T-7] #27, [T-8] #28. **Placing an order, listing your orders, the
cascade, cancelling and the `order` frame on the realtime feed are built.** A
trade that moves the price past a resting order's limit fills
that order inside the trade's own transaction, and a placement that is
marketable when it arrives fills at once, as below. This section is the
contract [FE][T-6] #61 builds against. Why the design looks like this:
[ADR 0024](../adr/0024-resting-orders-fill-inside-the-trade.md), which is
Accepted.

A limit order buys or sells at its limit price or better, against the market
maker. It fills at once as far as the price allows, and the rest waits. When
a later trade moves the price past the limit, the order fills inside that
trade's transaction. Traders never trade with each other.

**Binary markets only.** A market with more than two outcomes refuses an
order with `409 market_not_binary`. Hide the form when the market read lists
more than two outcomes.

**Good till cancelled.** An order rests until it is filled, cancelled, or its
market has a result. A closed market's orders do not fill, and they stay
cancellable.

### The order object

Every route below returns orders in this shape.

```jsonc
{
  "id": "0b6e...",
  "market_id": "9d1c...",
  "outcome_id": "4f2a...",
  "outcome_position": 0,
  "side": "buy",
  "limit_price": "0.6000",
  "quantity": "100.0000",
  "filled_quantity": "20.5465",
  "average_fill_price": "0.5750",
  "reserved_credits": "48.1848",
  "reserved_shares": null,
  "status": "open",
  "created_at": "2026-10-20T09:12:44.318000+00:00",
  "cancelled_at": null
}
```

| Field | Notes |
| --- | --- |
| `outcome_position` | 0 or 1. The ledger holds no labels: take them from market_service, as the portfolio does. |
| `limit_price` | The price on this order's own outcome, `0.0001` to `0.9999`. A buy fills at or below it, a sell at or above. |
| `filled_quantity` | Shares filled so far, across every fill. |
| `average_fill_price` | What the fills cost or paid per share, half-up at scale 4. `null` until the first fill. |
| `reserved_credits` | A buy only: what the order can still spend, its budget less what it has spent. The budget is `limit_price × quantity`, rounded up to the tick. `null` on a sell. `"0.0000"` once the order is not live. |
| `reserved_shares` | A sell only: the shares the order still holds back, `quantity - filled_quantity`. `null` on a buy. `"0.0000"` once the order is not live. |
| `status` | `open`, `filled`, `cancelled`, or `released`: the market has a result, and the remainder is released. `released` is derived on every read. |

Amounts are decimal strings, as everywhere on this service.

**A remainder can stay open with the price at its limit.** Shares come in
steps of `0.0001`, and each fill must average no worse than the limit. So a
partly filled order rests with the price at its limit to within one step, and
a remainder too small to fill at the limit rests until you cancel it or the
market ends.

### POST /ledger/markets/{market_id}/orders

```jsonc
// request
{
  "outcome_id": "4f2a...",
  "side": "buy",
  "limit_price": "0.6000",
  "quantity": "100.0000",
  "idempotency_key": "a client-generated string, unique to this caller"
}
```

`extra="forbid"` over exactly these five fields, like the trade route: the
body names no account and no amount. The budget is computed, and the account
is the token's. `side` is `"buy"` or `"sell"`. `quantity` follows the trade
route's rules: `> 0`, at most four decimal places, at most 18 digits.
`limit_price` is a decimal string, `> 0` and `< 1`, at most four decimal
places. There is **no `state_version`**: the limit protects you, and a quote
check would refuse an order because the price moved.

```jsonc
// response, 201: an order that filled in part on arrival
{
  "order": { /* the order object above */ },
  "fills": [
    { "transaction_id": "36fd...", "quantity": "20.5465", "total": "-11.8152", "state_version": 43 }
  ],
  "book_state_version": 43
}
```

That example is a buy of outcome 0 at `0.6000` on a market with `b = 100` at
`q = [20, 0]`, where outcome 0 is priced `0.5498`. It fills `20.5465` shares
at once, for `11.8152`, taking the price to `0.6000`. The other `79.4535`
rest, holding `48.1848` of a `60.0000` budget.

- `fills` is what this request filled. Each item is a trade in your history,
  with `total` signed as the trade route signs it. It is empty when nothing
  filled on arrival.
- `book_state_version` is the book's counter after this whole request,
  including any resting orders the placement's cascade filled, so it can be
  higher than the highest `state_version` in `fills`. When nothing filled, it
  is the counter the order was placed against. A placement that filled
  publishes one `price` frame, at `book_state_version`; one that only rested
  publishes none. The ledger stores it on the order.
- An order that filled completely on arrival comes back `filled`, with no
  reservation.
- **A buy's whole budget has to fit in `available`**, even when it would fill
  completely on arrival for less. Such a buy is refused
  `409 insufficient_funds` with `shortfall`, how much more you need, and
  nothing fills. Its `reserved` and `available` are your balance route's
  figures, without the new order, whether it would have rested or filled, so
  `shortfall` is always the budget less `available`.

**A retry with the same key** returns `201` with the order as it stands now,
which may have filled further since, the `book_state_version` stored when it
was placed, and as `fills` only this order's fills with a `state_version` at
or below that, which are the fills the first request made. Fills a later
trade's cascade made are not in `fills`, but they are in the order's
`filled_quantity` and `reserved_credits` or `reserved_shares`. A key already
used for a different order, by outcome, side, limit price or quantity, is
`409 idempotency_key_reused`. Like the trade route, the stored key is
`order:<user_id>:<market_id>:<idempotency_key>`, so your key only has to be
unique to you.

**The order of checks** is the trade route's: the retry lookup, then your
account (ADR 0022), then the market gate, then opening the market's book on
its first touch, then minting your starting credits if you have never been
granted them, then everything under the book lock. So a limit order can be
your first action on the platform: the grant is minted before the budget is
checked.

**What a placement locks.** Under the book lock, a buy that rests entirely
also locks your account until it commits, so a trade of yours in another
market cannot spend credits this order is reserving at the same moment. A
resting sell locks only the book. A placement that fills on arrival takes no
lock of its own: the fills' single commit locks your account along with every
other account involved, in ascending order. Neither path is visible in the
response. A fill refused inside a cascade is impossible by construction. If
one ever happens it is a ledger bug, the whole commit rolls back and you get a
500.

Errors:

| Status | `code` | When |
| --- | --- | --- |
| 401 | `invalid_token` | Missing, malformed or expired access token. |
| 403 | `account_suspended` | An administrator has suspended this account. As on the trade route. |
| 404 | `market_not_found` | No such market. |
| 409 | `market_closed` | The market's derived status is not `"open"`, or it has a result. |
| 409 | `market_not_binary` | The market has more than two outcomes. |
| 409 | `order_crosses_book` | The order would fire, or be fired by, a resting order that pushes the price the other way. `error.details` carries `side` and the bound: `max_limit_price` for a buy, which the limit must be strictly below, or `min_limit_price` for a sell, which it must be strictly above. Show it as "an order is in the way at this price". Your own orders count. |
| 409 | `insufficient_funds` | A buy whose budget does not fit in `available`, including one that would fill completely on arrival for less than its budget. `error.details` carries `balance`, `reserved`, `available` and `shortfall`, how much more credit you need for the budget to fit, and no `required`. A resting placement's `shortfall` is the budget less `available`. A placement that fills in the same batch is refused by the start-of-batch check, and its `shortfall` is the reservation at the start of the batch, including the new order's budget, less `balance`. The trade route's `insufficient_funds` keeps `required`, unchanged. |
| 409 | `insufficient_shares_held` | A sell larger than your position less the shares your other sell orders in that outcome reserve. `error.details` carries `held`, `reserved` and `requested`. |
| 409 | `idempotency_key_reused` | The key already names a different order. |
| 422 | `unknown_outcome` | `outcome_id` does not name one of this market's outcomes. |
| 422 | `quantity_too_large` | The fill on arrival would exceed what the ledger can store, as on the trade route. |
| 422 | `invalid_request` | A malformed body, an extra field, a `side` other than `"buy"` or `"sell"`, a `quantity` breaking the rules above, or a `limit_price` at or outside `0` and `1`, or with five decimal places. |
| 500 | `market_book_incomplete` | As on the trade route. |
| 500 | `cascade_fill_refused` | The overdraft check refused another trader's resting order that this placement's fill set off, which ADR 0024 makes impossible. Nothing was written and the ledger logged it. The response carries no figures: they would be the other trader's. A refusal of your own account is `409 insufficient_funds`. A server fault; not worth retrying. |
| 503 | `market_terms_unavailable` | As on the trade route. |
| 503 | `auth_unavailable` | As on the trade route. Nothing was written. |

A placement never returns `quote_stale`, `cost_below_tick` or
`proceeds_below_tick`. A fill that would round to nothing is not taken, and
the order rests instead.

### GET /ledger/orders/me

Your live orders, newest first: every order that is `open` on a market that
has no result, closed markets included. No query parameters and no paging.
Filled, cancelled and released orders are not listed.

```jsonc
{
  "orders": [ { /* the order object */ } ]
}
```

Any valid access token. This is the snapshot for order frames: fetch it when
the page opens and again after every socket reconnect
(`docs/api/realtime-service.md`, "Order updates").

It is unpaged on purpose, so replace your whole list with each response
rather than merging into it: DECISIONS.md, "`GET /ledger/orders/me` returns
every live order, unpaged".

| Status | `code` | When |
| --- | --- | --- |
| 401 | `invalid_token` | Missing, malformed or expired access token. |

### POST /ledger/orders/{order_id}/cancel

An empty body, `extra="forbid"`. Your own orders only, any role. No account
check and no market gate: a suspended account can still cancel while its
token is valid, and a closed market's orders can be cancelled.

```jsonc
// response, 200
{
  "order": { /* the order object, status "cancelled" */ },
  "released_credits": "48.1848",
  "released_shares": null
}
```

`released_credits` or `released_shares` is what the cancel released, the
other `null`. `filled_quantity` is what had filled before the cancel. Fills
stay: a cancel releases only the remainder.

**A cancel and a fill never both win.** Both take the market's book lock, and
whichever takes it first commits first. If a fill got there first, the cancel
releases the smaller remainder, or answers `409 order_already_filled`. If the
cancel got there first, the order does not fill.

| Status | `code` | When |
| --- | --- | --- |
| 401 | `invalid_token` | Missing, malformed or expired access token. |
| 404 | `order_not_found` | No such order, or not yours. The two look the same on purpose. |
| 409 | `order_already_filled` | It filled completely before the cancel. Refresh the order. |
| 409 | `order_already_cancelled` | It was already cancelled. A retried cancel lands here: treat it as done. |
| 409 | `order_released` | Its market has a result, and the remainder is already released. |
| 422 | `invalid_request` | `order_id` is not a UUID, or the body is not empty. |

### POST /ledger/admin/orders/{order_id}/cancel

[T-8] #326. An administrator cancels any trader's live order, as its owner
would. It exists for an order its owner cannot cancel: a suspended account's,
once its last access token has expired, which would otherwise hold its
reservation until the market has a result (ADR 0024, "A suspended owner's
orders are checked at placement only").

```jsonc
// request
{ "reason": "The owner's account is suspended; releasing what the order holds." }
```

`reason` is required, `extra="forbid"`. It must be at least 10 characters once
trimmed and at most 500, the same rule as auth's suspension reason. It is
recorded in the audit log as `order.cancelled_by_admin`, with the order's
`user_id` and `market_id` in `context`, in the cancel's own transaction, and
kept nowhere else. The owner's own cancel is not audited, so every entry of
that type is an administrator's.

The response is the owner's cancel's, above. Everything else is the owner's
cancel too: the same orders (open, on a market with no result), the same book
lock, so it never races a fill or the owner's own cancel, and the same 409s.
It is not limited to the caller's orders, so a missing order is the only
`404`. It posts no ledger entry, and no frame is published: the owner sees the
order leave `GET /ledger/orders/me`.

| Status | `code` | When |
| --- | --- | --- |
| 401 | `invalid_token` | Missing, malformed or expired access token. |
| 403 | `not_an_administrator` | A trader, including the order's owner. Their own cancel is the route above. |
| 404 | `order_not_found` | No such order. |
| 409 | `order_already_filled` | It filled completely before the cancel. |
| 409 | `order_already_cancelled` | It was already cancelled, by its owner or another administrator. |
| 409 | `order_released` | Its market has a result, and the remainder is already released. |
| 422 | `invalid_request` | `order_id` is not a UUID, `reason` is missing or over 500 characters, or the body has another field. |
| 422 | `cancellation_incomplete` | `reason` is under 10 characters once trimmed. |

### What a limit order does to the rest of this page

- **Balance:** `reserved` and `available` (above).
- **History:** each fill is a `trade_buy` or `trade_sell` row with its own
  `average_price`. Placing and cancelling write none.
- **Portfolio and leaderboard:** valued as before. Reserved credits are still
  in `balance`, and reserved shares are still positions, valued as today. Each
  portfolio row also says how many of its shares are reserved,
  `reserved_shares` (#410).
- **Price history and the trade feed:** one point and one row per fill.
- **Trade route:** `book_state_version`, and the two refusals that now count
  reservations (above).
- **A void** (ADR 0025, not built yet) releases every open order on its market,
  as a settlement does, and refunds every fill like any other trade.

## POST /ledger/markets/{market_id}/settlement

[3.4] #12. Pay every winning share of an approved market once, then tell
market_service that the market is settled. Administrators only.
[ADR 0019](../adr/0019-settlement-across-services.md).

**This is the route a client calls.** market_service's
`POST /markets/{id}/settle` is this route's last step and never a client's.
Called directly, it marks the market `settled` and pays nobody.

Any administrator may settle, including the market's creator, its proposer
and its approver. The two-person rule was spent at approval (ADR 0016), and
settlement carries out a decision that is already made.

### The request

```http
POST /ledger/markets/9d1c.../settlement
```

No body, `{}` or `null`, and nothing else. **Any field is
`422 invalid_request`**, not ignored. The request chooses nothing:

- **The winner** is the outcome market_service approved, which one
  administrator proposed and a second approved.
- **The holders and the amounts** come from this service's positions, read
  under the market's book lock. Each winning share pays one credit.

A field that was silently dropped would let a client believe it had picked
the winner.

A market can be settled when market_service reports it `approved` and
`settleable`, five minutes after its dispute window ends (see `settleable`
in [the market service's docs](market-service.md)). A market that reports
`settled` with nothing recorded here can also be settled. That state comes
from a direct call to market_service's step, and this route pays it.

### The response

```jsonc
// response, 200
{
  "market_id": "9d1c...",
  "outcome_id": "4f2a...",
  "recorded_at": "2026-10-10T09:41:02.118000Z",
  "holders_paid": 3,
  "total_paid": "140.0000",
  "residue": "-12.3456"
}
```

| Field | Meaning |
| --- | --- |
| `market_id` | The market. |
| `outcome_id` | The winning outcome. On a repeat, it is the outcome this service recorded, whatever market_service reports now. |
| `recorded_at` | When the settlement was recorded: read after the book lock, and the same instant as the audit entry's. The payout entries' `created_at` may be a few microseconds later. |
| `holders_paid` | The number of holders paid: one payout for each holder with more than zero winning shares. `0` if nobody held the winner. |
| `total_paid` | What the payouts add up to, as a decimal string at scale 4: one credit per winning share. |
| `residue` | What was left in the market's pool after the payouts, signed. Positive went from the pool to the platform. Negative came from the platform to cover a short pool. `0.0000` means nothing was posted. Either way the pool ends at exactly `0.0000`. |

The status is `200`, not `201`, on the request that pays and on every repeat
alike. Nothing is created at an address.

Each payout is a `settlement` entry in the winner's own history. The
settlement appends `market.settled` to the audit log, in the same commit as
the payouts. market_service's step appends `market.marked_settled`.

After a settlement, a trade on the market is `409 market_closed`. The
preview and the snapshot keep quoting the frozen book. **No price frame is
published**, because settlement moves neither `q` nor any position, so the
prices and `state_version` do not change.

### A repeat writes nothing here

A repeat answers the same `200` with the same six fields, rebuilt from the
entries the first request wrote. The response does not say which kind of
answer it is, and a client should not need to know: a repeat is how a
settlement whose last step failed is finished.

A repeat checks the ledger's record before market_service's status, so a
market that has been paid is never refused for its status. Every repeat
calls market_service's step again, and that step answers `200` without
writing for a market that is already `settled`.

### `503 settlement_unconfirmed` means the money has moved

Every winner has been paid and the settlement is recorded. What failed is
the last step, telling market_service, so the market still reads `approved`
and stays in the overview's approved queue. **Repeat the request.** It writes
nothing here and retries the last step, and its `200` confirms the
settlement. Do not tell the administrator that the settlement failed.

This is the opposite of `503 market_terms_unavailable`, which on this route
means no payout was written.

Near the end of the dispute window the two services can disagree, because
they judge `settleable` on different clocks. The public detail this service
reads derives it from market_service's request clock. market_service's step
judges it on Postgres's `clock_timestamp()`, read after it locks the market
row. So this service can read `settleable: true` and pay while the step still
answers `dispute_window_open`. That too is `settlement_unconfirmed`, and a
repeat once the edge has passed completes it.

### Errors

Checked in this order. The first that applies answers. A recorded settlement
skips the status, book and winner steps: after the read, it goes straight to
the settle step.

| Step | Status | `code` | When |
| --- | --- | --- | --- |
| body parse | 422 | `invalid_request` | The body is not valid JSON. FastAPI refuses it before anything else runs, the token check included. |
| token | 401 | `invalid_token` | Missing, malformed or expired access token. |
| token | 403 | `not_an_administrator` | A trader. |
| validation | 422 | `invalid_request` | A body with any field, a body that is not an object, or a `market_id` that is not a UUID. `error.details` names each one; see [Errors](#errors). |
| read | 401 | `invalid_token` | market_service refused the forwarded token. |
| read | 404 | `market_not_found` | No such market, including a draft or a submitted one, which market_service's public detail refuses with the same 404. |
| read | 503 | `market_terms_unavailable` | market_service could not be reached, answered with unusable terms, or answered `404` for a market this service holds a book for. |
| status | 409 | `dispute_window_open` | Nothing is recorded here, and the market is `approved` but not yet `settleable`. Retry once it is. |
| status | 409 | `market_not_approved` | Nothing is recorded here, and the market is in any status other than `approved` or `settled`. |
| book | 503 | `market_terms_unavailable` | On a market nobody has touched, opening its book reads market_service again, and that read failed or answered terms no book can be opened from. If market_service's answer has changed since the read step, this read can also answer that step's `401` or `404`. |
| winner | 503 | `market_terms_unavailable` | market_service reported a winner that is null or not one of this market's outcomes. |
| settle step | 503 | `settlement_unconfirmed` | The payouts are committed, by this request or by an earlier one, and market_service did not answer `200`. Repeat the request; see above. |

Every refusal but the last writes no payout. Everything before the book step
writes nothing at all. A refusal at the winner step on a market nobody had
touched comes after the book was opened, as a preview's first request would
open it. That happens once per market, and it is the only write such a
refusal leaves.

**ADR 0025: not built yet. A voided market is
`409 market_already_voided`.** At the lookup step, before the status check,
and again under the book lock: a market this service has voided is refused
with nothing written, never treated as a settlement to replay. At the status
step, a market market_service reports `voided` with nothing recorded here
gets the same code. Today a voided status would be `market_not_approved`.

## POST /ledger/markets/{market_id}/void

**ADR 0025: not built yet.** [BE] #221. End a flawed market with no
winner, refund every share still held at what it cost, then tell
market_service that the market is voided. Administrators only, and not one
who has traded the market.
[ADR 0025](../adr/0025-voiding-a-flawed-market.md).

**This is the route a client calls.** market_service's
`POST /markets/{id}/void` is this route's last step and never a client's.
Called directly, it marks the market `voided` and refunds nobody.

A void is for a market with no correct answer: the question was ambiguous,
the event was cancelled, or it was published by mistake. A wrong winner is a
dispute instead. **A void is final.** There is no un-void.

### The request

```http
POST /ledger/markets/9d1c.../void
```

```jsonc
{ "reason": "The match was abandoned after ten overs; there is no result." }
```

`reason` is required, and nothing else is accepted (`extra="forbid"`). It must
be at least 10 characters once trimmed and at most 5,000, the same rule as
every reason market_service stores. It is stored trimmed, on this service's
record and on the market, where traders read it, and logged. **Any other
field is `422 invalid_request`**: the request chooses no outcome, no holder
and no amount.

A market can be voided when market_service reports `voidable` (see
[the market service's docs](market-service.md)): `closed`,
`pending_resolution`, or `approved` while its dispute window is open. An
`open` market is closed early first. A market market_service reports `voided`
with nothing recorded here can also be voided: that comes from a direct call
to market_service's step, and this route refunds it.

### What a refund is

Each share still held is refunded at what its holder paid for it. Sales that
already happened stand. Where a holder sold part of what they bought, a sale
counts as selling their **newest** shares first, so the shares refunded are
the oldest ones, at what they cost. A filled limit order counts as the trade
it was. A trade none of whose shares were sold is refunded exactly what it was
charged. A trade some of whose shares were sold is refunded what its remaining
shares cost at the price they were bought at, the cheapest of that trade's
shares, rounded down to the tick; if that rounds to nothing, that trade adds
nothing to the position's refund. A position whose refund comes to `0.0000`
writes no entry.

If the market's pool cannot cover the refunds, the platform tops it up by at
most one more seed (the market's `seed_subsidy`). Beyond that, **every refund
is cut by the same fraction**, rounded down. A refund the cut takes to
`0.0000` writes no entry and is not counted in `positions_refunded`. Whatever
the pool holds after the refunds goes to the platform, so the pool ends at
`0.0000`.

### The response

```jsonc
// response, 200
{
  "market_id": "9d1c...",
  "reason": "The match was abandoned after ten overs; there is no result.",
  "voided_by": "0b8e...",
  "recorded_at": "2026-10-20T09:41:02.118000Z",
  "positions_refunded": 3,
  "total_owed": "140.0000",
  "total_refunded": "140.0000",
  "refunds_cut": false,
  "residue": "12.3456"
}
```

| Field | Meaning |
| --- | --- |
| `market_id` | The market. |
| `reason` | The reason recorded by the void that refunded. On a repeat, it is that one, not the repeating administrator's. |
| `voided_by` | The administrator whose request refunded. On a repeat, the same. |
| `recorded_at` | When the void was recorded: read after the book lock, the same instant as the audit entry's. |
| `positions_refunded` | The number of positions refunded more than zero. One holder of two outcomes counts twice. |
| `total_owed` | What the refunds add up to before any cut. |
| `total_refunded` | What was refunded. Equal to `total_owed` unless refunds were cut. |
| `refunds_cut` | `true` when the pool plus one seed could not cover `total_owed`, so every refund was cut by the same fraction. |
| `residue` | What moved between the pool and the platform after the refunds, signed as settlement's is. Positive went to the platform; negative came from it. |

The status is `200` on the request that refunds and on every repeat alike.
Each refund is a `void_refund` entry in the holder's history. The void appends
`market.voided` to the audit log in the same commit, and market_service's step
appends `market.marked_voided`.

After a void:

- A trade or a limit-order placement on the market is `409 market_closed`.
- **Every open limit order on the market is released**, in the void's commit.
  Its reservation goes, it reads `released`, it leaves
  `GET /ledger/orders/me`, and a cancel is `409 order_released`. No order
  frame is published.
- The portfolio shows the market's rows as `refunded`, and the exposure and
  the price history report `result: "voided"` with no winner (above and
  below).
- **No price frame is published.** A void moves neither `q` nor any position.

### A repeat writes nothing here

A repeat answers the same `200` with the same nine fields. A repeat checks
this service's record before market_service's status, so a market that has
been refunded is never refused for its status. It sums the figures from the
first request's entries, and re-runs only the replay of the market's trades,
to work out `total_owed`, which no entry records. It runs neither the
consistency check nor the stake check, so an administrator who traded the
market can still finish another administrator's void. Every repeat calls
market_service's step again, which answers `200` without writing on a market
already `voided`.

### `503 void_unconfirmed` means the money has moved

Every holder has been refunded and the void is recorded. What failed is the
last step, telling market_service, so the market still reads its old status.
**Repeat the request.** It writes nothing here and retries the last step.
Do not tell the administrator that the void failed.

`503 market_terms_unavailable` is different: it means *this request*
refunded nothing, because it stopped at the read, before it looked for a
recorded void. On a first void nobody has been refunded. On a repeat after
`void_unconfirmed` the earlier refunds still stand. Repeat again once
market_service answers.

A repeat works after the dispute window has ended: market_service's step
accepts an `approved` market until five minutes after the window's end, which
is when settlement could start. After that its step refuses, and the market
is left `approved` with its money refunded. That needs fixing by hand.

### Errors

Checked in this order. The first that applies answers. A recorded void skips
the status, book, replay and stake steps, re-running only the part of the
replay that works out `total_owed` (above), and after the read it goes
straight to the void step.

| Step | Status | `code` | When |
| --- | --- | --- | --- |
| body parse | 422 | `invalid_request` | The body is not valid JSON. |
| token | 401 | `invalid_token` | Missing, malformed or expired access token. |
| token | 403 | `not_an_administrator` | A trader. |
| validation | 422 | `invalid_request` | `reason` missing, not a string or over 5,000 characters, any other field, or a `market_id` that is not a UUID. |
| validation | 422 | `void_incomplete` | `reason` is under 10 characters once trimmed. No `error.details`, like the administrator's cancel's `cancellation_incomplete`. |
| read | 401 | `invalid_token` | market_service refused the forwarded token. |
| read | 404 | `market_not_found` | No such market, including a draft or a submitted one. |
| read | 503 | `market_terms_unavailable` | market_service could not be reached, answered with unusable terms, or answered `404` for a market this service holds a book for. |
| lookup | 409 | `market_already_settled` | This service has settled the market. |
| status | 409 | `market_already_settled` | Nothing is recorded here, and market_service reports the market `settled`. |
| status | 409 | `market_not_voidable` | Nothing is recorded here, and the market is not `voidable`: `open`, or `approved` with its dispute window ended. |
| book | 503 | `market_terms_unavailable` | On a market nobody has touched, opening its book reads market_service again, and that failed. |
| replay | 500 | `void_replay_mismatch` | The market's trades do not agree with its positions or its book. A server fault; nothing is written, and retrying will not help. |
| stake | 403 | `admin_has_a_stake` | The caller has traded this market, including through a limit order that filled, or a position since sold to zero. Another administrator has to void it. |
| void step | 503 | `void_unconfirmed` | The refunds are committed, by this request or by an earlier one, and market_service did not answer `200`. Repeat the request; see above. |

Every refusal but the last writes no refund. Everything before the book step
writes nothing at all.

## GET /ledger/markets/{market_id}/price-history

[BE][2.4] #63, for [2.4] #8's price chart. Every price the market has had, one
point per trade, oldest first. Rebuilt from the market's trades on every read
(DECISIONS.md, "Price history is replayed from the trades on the pool's feed,
and nothing stores it").

Any valid access token, any role. Every point has already been broadcast to
every subscriber of the market's price frames, so an admin gate would protect
nothing (DECISIONS.md, "Price history takes any valid token; the trade feed
takes an administrator's").

| Query | Default | Notes |
| --- | --- | --- |
| `from` | — | ISO-8601 time with an offset. Points at or after it, plus one carry-in point (below). |
| `to` | — | ISO-8601 time with an offset. Points at or before it. |

Both must carry an offset: `Z` or `+08:00`. In a query string, send `+` as
`%2B`. A raw `+` decodes to a space and the time is refused.

```jsonc
// GET /ledger/markets/9d1c.../price-history
{
  "market_id": "9d1c...",
  "points": [
    {
      "state_version": 0,
      "occurred_at": "2026-10-08T02:15:09.441000+00:00",
      "prices": [
        { "outcome_id": "4f2a...", "position": 0, "price": "0.5000" },
        { "outcome_id": "b7e1...", "position": 1, "price": "0.5000" }
      ]
    },
    {
      "state_version": 1,
      "occurred_at": "2026-10-08T02:16:40.102311+00:00",
      "prices": [
        { "outcome_id": "4f2a...", "position": 0, "price": "0.6225" },
        { "outcome_id": "b7e1...", "position": 1, "price": "0.3775" }
      ]
    },
    {
      "state_version": 2,
      "occurred_at": "2026-10-08T03:02:11.870954+00:00",
      "prices": [
        { "outcome_id": "4f2a...", "position": 0, "price": "0.5744" },
        { "outcome_id": "b7e1...", "position": 1, "price": "0.4256" }
      ]
    }
  ],
  "result": null,
  "winning_outcome_id": null,
  "result_recorded_at": null
}
```

**A point is a `price` socket frame without `type` and `market_id`.** It has
the same `state_version`, the same `prices` in `position` order, and the same
decimal strings from the same quantizer (`docs/api/realtime-service.md`). So a
chart draws the history, then appends live frames and drops any frame whose
`state_version` it already holds. One function renders both.

**The first point is the book's opening.** `state_version` 0, every outcome at
the same price, at the book's `opened_at`. After that there is one point per
trade, in the order the trades committed, which is `state_version` order. A
point's `occurred_at` is when that trade's entries landed. That can be later
than the `occurred_at` on the frame for the same `state_version`, which is when
the trade took its price under the book lock (#187). Across replicas the gap is
clock skew. Dedupe on `state_version`, never on time.

### `from` and `to`

Points after `to` are left out. When `from` is given, the last point before
`from` is included as the first point, **at its own `occurred_at`**: it is not
moved to `from`. A chart drawn from `from` therefore starts at the price the
market actually had then, and not at whatever its first trade inside the
window moved it to:

```jsonc
// GET /ledger/markets/9d1c.../price-history?from=2026-10-09T00:00:00Z
{
  "market_id": "9d1c...",
  "points": [
    { "state_version": 41, "occurred_at": "2026-10-08T22:51:37.004118+00:00", "prices": [ ... ] },
    { "state_version": 42, "occurred_at": "2026-10-09T08:12:44.501200+00:00", "prices": [ ... ] }
  ],
  "result": null,
  "winning_outcome_id": null,
  "result_recorded_at": null
}
```

If nothing comes before `from`, nothing is carried in. That happens when
`from` is at or before `opened_at`, and then the opening point is inside the
window anyway. There is no bucketing and no paging: every trade in the window
is a point, because #8 exists to spot manipulation, and a per-interval figure
hides a spike inside one interval (DECISIONS.md, "Price history returns raw
points…").

### A settled market

The points end at the last trade. **Settlement adds no point**, because no
price frame announced one and the snapshot still shows the last traded price
(DECISIONS.md, "Settlement adds no point to the price history"). The result is
reported beside the points:

```jsonc
{
  "market_id": "9d1c...",
  "points": [ /* ... ending at the last trade ... */ ],
  "result": "settled",
  "winning_outcome_id": "b7e1...",
  "result_recorded_at": "2026-10-10T09:41:02.118000+00:00"
}
```

| Field | Meaning |
| --- | --- |
| `result` | `null` until the ledger records how the market ended. Then that record's kind, today always `"settled"`. The same values as the exposure read's `result`. Treat an unrecognised value as opaque. |
| `winning_outcome_id` | `null` until the market is settled. Then the outcome that paid. |
| `result_recorded_at` | `null` until the market is settled. Then the settlement's `recorded_at`, the same instant the settlement route reports. |

**ADR 0025: not built yet. A voided market** ends its points at the
last trade too, since a void moves no price. `result` is `"voided"`,
`winning_outcome_id` is `null`, and `result_recorded_at` is the void's
`recorded_at`.

### A market with no book

A published market that nobody has previewed, traded or snapshotted has no
book here yet. This route does not open one: it writes nothing and never calls
market_service. So it answers `200` with nothing in it:

```jsonc
{
  "market_id": "9d1c...",
  "points": [],
  "result": null,
  "winning_outcome_id": null,
  "result_recorded_at": null
}
```

An id that names no market at all reads the same way. This service cannot tell
the two apart without asking market_service. Like the snapshot, the route does
not check the market's status: an open, closed or settled market reads the
same way.

Errors:

| Status | `code` | When |
| --- | --- | --- |
| 401 | `invalid_token` | Missing, malformed or expired access token. |
| 422 | `invalid_request` | `market_id` is not a UUID, `from` or `to` is not an ISO-8601 time with an offset, or `from` is later than `to`. `error.details` names each one. When `from` is later than `to`, `error.details` has an item for `from` and one for `to`. |
| 500 | `market_book_incomplete` | This service holds a book for the market that cannot be priced: no outcome rows, only one, or a `liquidity_b` the engine cannot use. The same guard the snapshot uses. A server fault; not worth retrying. |
| 500 | `price_history_inconsistent` | The market's trades on the pool's feed do not run `state_version` 1, 2, 3, … in order, so the history cannot be rebuilt. A server fault; not worth retrying. |

## GET /ledger/markets/{market_id}/trades

[BE][2.4] #63, for [2.4] #8's trade table. Every trade in one market, newest
first, for an administrator investigating it. `403` for a trader. The same
path with `POST` is the trade route above; this one only reads.

```jsonc
{
  "trades": [
    {
      "transaction_id": "8a7e...",
      "created_at": "2026-10-08T03:02:11.870954+00:00",
      "user_id": "77c0...",
      "outcome_id": "4f2a...",
      "side": "sell",
      "quantity": "20.0000",
      "average_price": "0.5986",
      "total": "11.9720",
      "state_version": 2
    },
    {
      "transaction_id": "36fd...",
      "created_at": "2026-10-08T02:16:40.102311+00:00",
      "user_id": "5f3e...",
      "outcome_id": "4f2a...",
      "side": "buy",
      "quantity": "50.0000",
      "average_price": "0.5619",
      "total": "-28.0945",
      "state_version": 1
    }
  ],
  "next_cursor": "MjAyNi0xMC0wOFQwMjoxNjo0MC4xMDIzMTEr...",
  "has_more": true
}
```

| Field | Meaning |
| --- | --- |
| `transaction_id` | The trade's transaction. The same id is on the trader's own history row. |
| `created_at` | When the trade's entries landed, named as on `/ledger/entries/me`. It equals the matching price-history point's `occurred_at`. |
| `user_id` | The trader. An id, not a name (below). |
| `outcome_id` | The outcome traded. One of this market's outcomes. |
| `side` | `"buy"` or `"sell"`. |
| `quantity` | Shares, at scale 4. |
| `average_price` | `\|total\| ÷ quantity`, `ROUND_HALF_UP` at scale 4: what this trade averaged, not a marginal price. The same function as the trader's own history row, so the two always agree. |
| `total` | Signed for the trader, as on `TradeOut.total`: negative on a buy, positive on a sell. |
| `state_version` | The book's counter after this trade. It names the price-history point the trade made. |

**Every row is a trade.** The market's pool account also holds the seed,
settlement payouts and the settlement residue, and none of them appears here.
There is no `balance_after`: a running balance on a pool account means nothing
to someone reading trades (DECISIONS.md, "The market's trade feed reads the
pool account's feed…").

**`user_id`, not a username.** This service holds no user table and knows a
user only by the id in a signed token (ADR 0003). Resolve names in the browser
through auth's batch lookup on port 8000, `POST /admin/users/lookup` with
`{"user_ids": [...]}`, 1 to 200 ids per request, which answers
`{"users": [{"id", "username"}]}` (#273). That route is built; swapping the
short id that links to the user page for the username it returns is #259's
remaining work. DECISIONS.md, "A market's trades carry `user_id`, not
`username`…".

**A market with no book is an empty `200`**: `trades: []`, `next_cursor:
null`, `has_more: false`. An id that names no market reads the same way. Like
the price history, this route writes nothing, never calls market_service and
does not check the market's status.

### Paging

| Query | Default | Notes |
| --- | --- | --- |
| `limit` | 50 | Clamped at 200. Asking for more is not an error. |
| `cursor` | — | The `next_cursor` from the previous page, unmodified. |

Keyset, the same scheme and cursor format as `/ledger/entries/me` (above). A
trade committed while you page is newer than every row on screen, so it cannot
shift a later page. Echo `next_cursor` back as it came; do not build one.

Errors:

| Status | `code` | When |
| --- | --- | --- |
| 400 | `malformed_cursor` | `cursor` was not one this service issued. |
| 401 | `invalid_token` | Missing, malformed or expired access token. |
| 403 | `not_an_administrator` | A trader. |
| 422 | `invalid_request` | `market_id` is not a UUID, or `limit` is below 1. `error.details` names each one. |

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
| 422 | `invalid_request` | The request does not match the route's parameters or body, e.g. `limit=0`, a path id that is not a UUID, or an extra field in a trade or settlement body |

**Every 422 is in this envelope**, whichever layer refused the request — each
route's own codes and `invalid_request` alike — so a client parses one
shape. `invalid_request` alone adds `error.details`, one item per field that
was wrong:

```jsonc
// GET /ledger/markets/{market_id}/preview?outcome_id=...&side=buy&quantity=0
{
  "error": {
    "code": "invalid_request",
    "message": "Some of the details in this request aren't valid.",
    "details": [
      { "loc": ["query", "quantity"], "msg": "Input should be greater than 0", "type": "greater_than" }
    ]
  }
}
```

`loc` is where the problem is — `query`, `path` or `body` first, then the
field. Branch on `type`, which is pydantic's stable name for the problem; `msg`
is for a person. Nothing else from pydantic's error is passed on.

The preview route above adds eight more of its own — `market_not_found`
(404), `insufficient_shares_outstanding` (409), `unknown_outcome` (422),
`quantity_too_large` (422), `proceeds_below_tick` (422), `cost_below_tick`
(422), `market_book_incomplete` (500) and `market_terms_unavailable` (503)
— documented there rather than repeated here. The snapshot route reuses
three of them —
`market_not_found`, `market_book_incomplete` and `market_terms_unavailable`
— for the reason its own section gives: both routes share the same cold
path.

**A `404 market_not_found` is remembered for ten seconds** (DECISIONS.md,
"A market_service 404 is remembered for ten seconds, per process"), on the
preview, the snapshot, the trade and the settlement alike. Inside that window
the ledger answers 404 again without asking market_service. So a market
published a few seconds after somebody asked about it can still read as not
found until the window passes. Retrying after ten seconds is enough. Nothing
else is remembered: a `503` or a `401` is asked again on the next request.

`market_not_published` (409) is defined in `core/errors.py` and mapped like
every other domain error, and no request can currently reach it.
`books.ensure_open` raises it only for terms whose `published_at` is null, and
the public detail endpoint those terms come from answers `404` for every
market that has not been published — so a draft arrives here as
`market_not_found`, not as this. It is left in place, and out of every route's
declared responses, because it becomes reachable the day the ledger reads
terms from somewhere that serves unpublished markets. Do not write a handler
for it today.

`account_suspended` (403) and `auth_unavailable` (503), both new in [4.2]
#14, `insufficient_funds` (409), `insufficient_shares_held` (409, new in [T-3]
#23), `idempotency_key_reused` (409), `market_closed` (409) and `quote_stale`
(409, new in [T-2] #22) are the trade route's own, documented in its section
above. `insufficient_shares_outstanding` (409) is unreachable on the trade
route: each outcome's shares outstanding equal the sum of its positions, so
`insufficient_shares_held` always refuses first. It stays in the code as a
backstop and is out of the trade route's table on purpose; only the preview
can return it. `market_not_approved` (409), `dispute_window_open` (409) and
`settlement_unconfirmed` (503, new in [3.4] #12) are the settlement route's own,
documented in its section above. `leaderboard_refreshed` (409, new in [L-1]
#38) is the leaderboard's own, documented in its section above.
`unbalanced_transaction` (422) is
`core/errors.py`'s and no route can return it: `service/trading.py` always
builds two balanced legs, so it is a guard against a bug in this service rather
than a response any request can provoke. `pending_writes_on_replay` (500) is the
same shape one layer down, in `service/posting.py` — a caller of the write
primitive reaching its replay branch with work still pending, which every caller
this service has is built not to do.
`malformed_batch` (500) is the same again, in the batch form of that
primitive (`posting.post_all`, new in [3.4] #12), whose one caller is the
settlement route: a batch with no movements, or two movements sharing an
idempotency key. The route builds every batch from this service's own
positions, with one payout per winner keyed by user id and at most one
residue, so no request can provoke it.
`cascade_fill_refused` (500, new in [T-7] #27) is the trade route's and the
placement route's guard on their cascades: the overdraft check refused a
batch anywhere but at the trade's own movement, or, on a placement, refused
an account other than the placer's, which ADR 0024 makes impossible while
every fill is bounded by its own order's reservation. Nothing is written, the
ledger logs the refusal, and the response names no other account's figures.

`market_not_binary` and `order_crosses_book` (409, new in [T-6] #26) are the
placement's own. `order_not_found`, `order_already_filled`,
`order_already_cancelled` and `order_released` (404 and 409, new in [T-8]
#28) are the cancel route's own. All six are documented in "Limit orders"
above. `cancellation_incomplete` (422, new in [T-8] #326) is the
administrator's cancel's own, documented there too.

**ADR 0025: not built yet.** `admin_has_a_stake` (403),
`market_already_settled` and `market_not_voidable` (409), `void_incomplete`
(422), `void_replay_mismatch` (500) and `void_unconfirmed` (503) are the void
route's own, documented in its section above. `market_already_voided` (409)
is the settlement route's, for a market this service or market_service has
voided.
