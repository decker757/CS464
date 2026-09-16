# Audit service API

Base URL `http://localhost:8002` in development. Interactive docs, generated
from the code and authoritative if this page ever disagrees, at
[`/docs`](http://localhost:8002/docs).

Covers [4.3] #15.

Why entries are written by the acting service rather than posted here, and why
there is no write route at all:
[ADR 0006](../adr/0006-audit-log-write-path.md).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/audit/actions` | Read the admin action log |
| GET | `/health` | Liveness and readiness probe |

**There is no write endpoint, and there will not be one.** An entry is appended
by the service performing the action, inside that action's own database
transaction. If you are looking for where to POST an audit entry from a new
admin feature, the answer is `service/audit.py` in your own service — see
"Writing to the log" below.

There is no edit or delete endpoint either, and that is not enforced by this
API being small. No database role holds `UPDATE`, `DELETE` or `TRUNCATE` on
`audit.admin_actions`, and a trigger refuses all three even for the table's
owner. A route added here in a hurry would fail against the database.

## Authentication

Identical to the other two services, because it is the same token.

- **Browser:** the `access_token` cookie the auth service already set. Send
  `credentials: 'include'`, and make sure this origin is in `CORS_ORIGINS`.
- **Service:** `Authorization: Bearer <jwt>`. The header wins over the cookie.

Administrator only. The log names accounts and quotes the reasons admins gave
for acting on them, so `403` for a trader is deliberate and permanent, while
`401` means the session is gone and logging in again will help.

## GET /audit/actions

Every administrative action across every service, newest first.

### Query parameters

| Name | Type | Default | Meaning |
| --- | --- | --- | --- |
| `actor_id` | uuid | — | Only actions taken by this account |
| `action_type` | string | — | Exact match, e.g. `market.submitted` |
| `cursor` | string | — | The `next_cursor` from the previous page |
| `limit` | int ≥ 1 | 50 | Entries per page, capped at 200 |

Both filters may be combined. A `limit` above the cap is clamped rather than
rejected — a caller asking for more than the server will serve wants as much as
it can get — but `limit=0` is a 422, because a page of nothing makes no
progress.

### Response

```jsonc
{
  "actions": [
    {
      "id": "05915a8d-482f-4890-b3bd-923bd884e3f0",
      "occurred_at": "2026-09-15T03:16:14.470542Z",

      "actor_id": "ac9c5554-51e3-4f84-bd37-60e5d8a8e3ca",
      "actor_username": "ernest_t",     // who they WERE, not who they are now
      "actor_role": "admin",            // the role they held at the time

      "action_type": "market.submitted",

      "target_type": "market",
      "target_id": "8d383301-126e-42cc-b2b4-ebd92fbba290",
      "target_label": "Will Singapore core inflation be below 2% for December 2026?",

      "reason": null,                   // present for the actions that require one
      "context": {                      // shaped by action_type; render what you know
        "outcomes": ["Yes", "No"],
        "liquidity_b": "250.0000",
        "seed_subsidy": "500.0000",
        "close_time": "2026-10-15T03:16:14.388891+00:00",
        "resolution_time": "2026-10-30T03:16:14.400888+00:00",
        "resolution_sources": ["https://www.mas.gov.sg/statistics"]
      },

      "source_service": "market_service"
    }
  ],
  "next_cursor": null,
  "has_more": false
}
```

Every field is a **snapshot** taken when the action happened. Nothing is
resolved at read time, and nothing can have changed since: the row cannot be
updated, and the names in it are copies rather than references. That is why
`actor_username` is still correct after a rename, a demotion, or the account
being deleted entirely — and why this service needs no grant on `auth.users`.

Numbers inside `context` are strings. The audit record says what was approved,
exactly; nothing does arithmetic with it. (`MarketOut` sends the same values as
JSON numbers, because the create form *does* do arithmetic with them.)

### Paging

Keyset, not offset. An audit log only grows at the end you read from, so with
`OFFSET` any entry appended between page one and page two pushes the window
down — the reader sees rows twice and silently skips the ones behind them.

Send `next_cursor` back as `cursor`, unmodified. It encodes a position in one
ordering; constructing one by hand invents a position this service never
promised to honour, and is a `400`.

```js
let cursor = null;
do {
  const url = new URL('/audit/actions', base);
  if (cursor) url.searchParams.set('cursor', cursor);
  const page = await fetch(url, { credentials: 'include' }).then(r => r.json());
  render(page.actions);
  cursor = page.next_cursor;      // null when there is no more
} while (cursor);
```

### Errors

The same envelope as the auth and market services.

| Status | `error.code` | When |
| --- | --- | --- |
| 400 | `malformed_cursor` | `cursor` was not one this service issued |
| 401 | `invalid_token` | Missing, malformed or expired token |
| 403 | `not_an_administrator` | Valid session, not an admin |
| 422 | — | FastAPI's own validation, e.g. `actor_id` is not a UUID |

## Action types

Namespaced by the entity acted on. The vocabulary grows as stories land, and it
is deliberately **not** constrained in SQL — see ADR 0006 for why a CHECK here
would let a stale audit schema abort a working admin action.

| `action_type` | Written by | Story |
| --- | --- | --- |
| `market.submitted` | market_service | [1.1] #1, [1.2] #2 |
| `market.published` | market_service | [1.3] #3 |
| `market.closed_early` | market_service | [2.3] #7 |
| `market.outcome_proposed` | market_service | [3.1] #9 |

Treat an unrecognised value as opaque rather than as an error: a newer service
may be writing an action type this build has never heard of.

Note what is **not** logged, and will not be:

- **Draft autosaves.** The create form saves every three seconds. Logging that
  would bury every real decision under thousands of keystroke entries.
- **Automatic market closes.** A market that reaches its own `close_time` is
  closed by the clock, and the clock is not an actor — there is nobody to
  record, and `market.published` already carries the closing time that was
  approved. Every `market.closed_early` entry is therefore a human one.
- **Reads of this log.** Every entry here changed something.
- **Denied or failed actions.** A rolled-back transaction takes its entry with
  it — which is what makes an entry trustworthy. Security logging is a
  different feature.

## Writing to the log

From a new admin feature in an existing service, in that service's own
transaction:

```python
from model.audit import AdminAction
from service import audit

await audit.record(
    session,
    actor=actor,                      # from CurrentActor in the controller
    action=AdminAction.MARKET_CLOSED_EARLY,
    target_type="market",
    target_id=market.id,
    target_label=market.question,
    reason=payload.reason,            # the free text [2.3] #7 and [4.2] #14 demand
    context={"close_time": market.close_time.isoformat()},
)
# no commit here: the caller's transaction commits both, or neither
```

That is [2.3] #7 as it actually shipped, near enough to copy. Two details in it
are the ones worth copying: `reason` is the administrator's justification and
`context` is everything else a reader needs, because `audit_svc` holds no grant
on any other service's schema and cannot look a missing fact up.

Three rules:

1. **Never commit inside `record`.** The entire guarantee is that the entry and
   the action share one transaction.
2. **Add the new value to that service's `AdminAction` enum**, and add a row to
   the table above. No migration is needed; `action_type` is an unconstrained
   string on purpose.
3. **Log decisions, not keystrokes.** If it fires on a timer, it does not
   belong here.

A service that does not yet write to the log needs `model/audit.py` and
`service/audit.py` copied in, plus `INSERT` on `audit.admin_actions` in
`sql/02-schemas.sql`. `auth_service` needs both for [4.2] #14.
