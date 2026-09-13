# Market service API

Base URL `http://localhost:8001` in development. Interactive docs, generated
from the code and authoritative if this page ever disagrees, at
[`/docs`](http://localhost:8001/docs).

Covers [1.1] #1 and the backend half of [FE][1.1] #45.

Why it is a separate service and how it knows who is an admin:
[ADR 0003](../adr/0003-market-service-boundary.md). Why one endpoint does both
autosave and submit: [ADR 0004](../adr/0004-draft-autosave-and-submission.md).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/markets` | Save a draft, or submit a market |
| GET | `/markets` | List my own markets |
| GET | `/markets/{id}` | Read one of my own markets |
| GET | `/health` | Liveness and readiness probe |

Every `/markets` route requires an **administrator** token. There is no
unauthenticated read here; the public market API for traders is [BE][X] #62.

## Authentication

Identical to the auth service, because it is the same token.

- **Browser:** the `access_token` cookie the auth service already set. Send
  `credentials: 'include'` on every call, and make sure this origin is in
  `CORS_ORIGINS`.
- **Service:** `Authorization: Bearer <jwt>`. The header wins over the cookie.

This service never queries the auth database. It verifies the signature and
reads the `role` claim, so:

- A newly promoted admin must **log in again** (or refresh) before their token
  carries `role: admin`.
- `401` means the session is gone — send the user to log in.
- `403` means the session is fine and this account is not an administrator —
  retrying will not help.

## POST /markets

One endpoint for both the autosave and the submit button. The difference is the
`status` field.

### Request

```jsonc
{
  "draft_key": "3f6b1c62-6a1e-4a1d-9f2f-2a3e4b5c6d7e",  // required
  "status": "draft",                                     // "draft" | "submitted"

  "question": "Will Singapore core inflation be below 2% for December 2026?",
  "description": "Measured on the first published print.",

  "outcomes": [{ "label": "Yes" }, { "label": "No" }],

  "close_time": "2027-01-05T12:00:00Z",
  "resolution_time": "2027-01-20T12:00:00Z",

  "resolution_criteria": "Resolves YES if the MAS core inflation print for December 2026, as first published, is strictly below 2.0%. Later revisions do not change it.",
  "resolution_sources": [
    { "url": "https://www.mas.gov.sg/statistics", "label": "MAS statistics" }
  ]
}
```

**`draft_key` is the only required field.** Generate one UUID when the create
form opens:

```js
const draftKey = crypto.randomUUID();
```

and send the same value on every save for that form. The server upserts on it,
so the three-second autosave updates one market rather than creating one per
tick. Keep it for as long as the form is open; start a new one for a new market.

**`status` defaults to `draft`.** Send `submitted` only from the submit button.

**Timestamps must carry an offset.** `2027-01-05T12:00:00Z` or
`2027-01-05T20:00:00+08:00`. A value without one is a 422 rather than a guess,
because guessing UTC would put a Singapore close time eight hours out.

**`creator_id` in the body is ignored.** The creator is taken from the token.

### Response

`201` the first time a `draft_key` is seen, `200` on every later save.

```jsonc
{
  "market": {
    "id": "410465f3-2852-4833-964b-f42e23b8227c",
    "draft_key": "3f6b1c62-6a1e-4a1d-9f2f-2a3e4b5c6d7e",
    "creator_id": "8d136846-bca7-4d19-b693-8cfce410fc8d",
    "status": "draft",
    "question": "Will Singapore core inflation be below 2% for December 2026?",
    "description": null,
    "outcomes": [{ "id": "...", "position": 0, "label": "Yes" }],
    "close_time": null,
    "resolution_time": null,
    "resolution_criteria": null,
    "resolution_sources": [],
    "created_at": "2026-09-13T14:06:38.907220Z",
    "updated_at": "2026-09-13T14:06:38.907222Z",
    "submitted_at": null
  },
  "blocking_submission": [
    { "field": "close_time", "message": "A close time is required." },
    { "field": "outcomes",   "message": "A market needs at least 2 named outcomes." }
  ]
}
```

**`blocking_submission` is the useful part.** A draft is never rejected for
being incomplete, but every response lists exactly what still stands between it
and submission, keyed by form field. Array fields are addressed by position —
`outcomes[1].label`, `resolution_sources[0].url` — so the form can mark
individual rows. Render these as live hints and there is no need to reimplement
the rules in the browser. On a successful submission the list is empty.

### Submission rules

A `submitted` request is refused with `422` unless all of these hold. They are
the acceptance criteria of [1.1] #1.

| Field | Rule |
| --- | --- |
| `question` | present, at least 10 characters |
| `outcomes` | at least 2 named; no blanks; no case-insensitive duplicates; at most 10 |
| `close_time` | present, strictly in the future |
| `resolution_time` | present, strictly in the future |
| `close_time` | strictly before `resolution_time` |
| `resolution_criteria` | present, at least 10 characters |
| `resolution_sources` | at least one openable `http`/`https` URL |

**A refused submission writes nothing**, including any edits that came with it.
Nothing is lost: the autosave saves them three seconds later.

`resolution_criteria` is required alongside the URLs on purpose. A source says
where to look; the criteria say what settles it — first print or revised, and
what happens if publication slips. That gap is what [3.3] #11's dispute window
exists to absorb.

## GET /markets

Every market belonging to the calling administrator, most recently updated
first. A summary, not the whole market:

```json
{
  "markets": [
    {
      "id": "410465f3-2852-4833-964b-f42e23b8227c",
      "draft_key": "3f6b1c62-6a1e-4a1d-9f2f-2a3e4b5c6d7e",
      "status": "draft",
      "question": "Will Singapore core inflation be below 2% for December 2026?",
      "close_time": null,
      "updated_at": "2026-09-13T14:06:38.907222Z"
    }
  ]
}
```

## GET /markets/{id}

The same `market` object POST returns, for reloading a draft after a page
refresh. Returns `404` for another administrator's market — not `403`, because
a 403 would confirm that market exists.

## Errors

The same envelope the auth service uses, so one parser covers both:

```json
{ "error": { "code": "not_an_administrator", "message": "This action requires an administrator account." } }
```

`draft_incomplete` adds a `details` array. Nothing else does, and it is
additive, so a client that ignores it still reads `code` and `message`:

```json
{
  "error": {
    "code": "draft_incomplete",
    "message": "This market cannot be submitted yet.",
    "details": [
      { "field": "close_time", "message": "Close time must be in the future." }
    ]
  }
}
```

| Status | `code` | When |
| --- | --- | --- |
| 401 | `invalid_token` | no token, or it is expired, forged or malformed |
| 403 | `not_an_administrator` | valid session, but a trader |
| 404 | `market_not_found` | no such market, or it is not yours |
| 409 | `market_not_editable` | an autosave arrived for an already-submitted market |
| 422 | `draft_incomplete` | submission refused; see `details` |
| 422 | — | FastAPI's own body-validation error, a different shape |

Two `422`s exist and they do not look alike. `draft_incomplete` is ours and
carries the envelope above. A malformed body — a missing `draft_key`, a naive
timestamp, a non-UUID — is FastAPI's, and comes back as `{"detail": [...]}`.
Branch on the presence of `error`.

## Notes for [FE][1.1] #45

1. `crypto.randomUUID()` once when the form opens; hold it for the form's life.
2. Debounce roughly 3 s of inactivity, then POST with `status: "draft"`.
   Skip the call if nothing changed since the last one.
3. Paint `blocking_submission` as inline hints. Disable the submit button while
   it is non-empty and you will never see a 422 from `draft_incomplete`.
4. On submit, POST the same `draft_key` with `status: "submitted"`.
5. A `409` means the market is already submitted — stop the autosave timer.
6. Send timestamps as ISO 8601 **with an offset**:
   `new Date(value).toISOString()` produces one.
7. `status: "submitted"` is not published. The publish control is [1.3] #3 and
   the endpoint does not exist yet.
