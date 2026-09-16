# Ledger service API

Base URL `http://localhost:8003` in development. Interactive docs, generated
from the code and authoritative if this page ever disagrees, at
[`/docs`](http://localhost:8003/docs).

Covers [F-1] #41, and the backend half of [B-1] #32, [B-2] #33 and [4.1] #13.

Why balances are derived rather than stored, why a read can write, and why
there is no write endpoint yet:
[ADR 0009](../adr/0009-the-ledger-write-path.md). Why positions will live here
rather than with markets: [ADR 0005](../adr/0005-trading-service-boundary.md).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/ledger/balances/me` | My available balance |
| GET | `/ledger/entries/me` | My ledger history |
| GET | `/ledger/users/{user_id}/balance` | Any user's balance (admin) |
| GET | `/ledger/users/{user_id}/entries` | Any user's history (admin) |
| GET | `/health` | Liveness and readiness probe |

**Every route reads.** There is no POST, PUT, PATCH or DELETE, and there never
will be one that edits or removes an entry — the ledger is append-only, enforced
by a database trigger rather than by the absence of a route. The endpoint that
*writes* a movement arrives with [T-2] #22, together with the decision about how
a trading service authenticates to it.

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

**Two things [4.1] #13 still needs are not here.** Searching users by email or
username is the auth service's data and belongs on its side of the boundary;
this service holds no user table. And the running balance beside each entry is
one aggregate away — the balance as at the newest entry on the page, then walk
down subtracting each amount — and belongs with the view that renders it.

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

Three more exist in `core/errors.py` and no route can return them yet:
`insufficient_funds` (409), `idempotency_key_reused` (409) and
`unbalanced_transaction` (422). They belong to the write path and are documented
here so that [T-2] #22's endpoint is a route rather than a second opinion about
what they mean. `insufficient_funds` carries `error.details` with `balance` and
`required`, because a trading service has to tell somebody how short they were.
