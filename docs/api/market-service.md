# Market service API

Base URL `http://localhost:8001` in development. Interactive docs, generated
from the code and authoritative if this page ever disagrees, at
[`/docs`](http://localhost:8001/docs).

Covers [1.1] #1, [1.2] #2, [1.3] #3, [F-4] #44, [2.3] #7, [3.1] #9 and the
backend half of [FE][1.1] #45, [FE][2.3] #56 and [FE][3.1] #52.

A successful submission, publication, early close or outcome proposal also
appends an entry to the shared audit log, in the same database transaction, so
the two can never disagree. An autosave does not, and neither does an automatic
close — the clock is not an actor. See [`audit-service.md`](audit-service.md)
and [ADR 0006](../adr/0006-audit-log-write-path.md).

Why it is a separate service and how it knows who is an admin:
[ADR 0003](../adr/0003-market-service-boundary.md). Why one endpoint does both
autosave and submit: [ADR 0004](../adr/0004-draft-autosave-and-submission.md).
Why the LMSR engine itself is not in this service:
[ADR 0005](../adr/0005-trading-service-boundary.md). Why publishing is a
separate endpoint rather than a third status on the save:
[ADR 0008](../adr/0008-publishing-a-market.md). Why the clock closes a market
and a sweep only writes it down:
[ADR 0011](../adr/0011-market-auto-close.md). Why proposing an outcome gates on
the status column anyway, and why the evidence is two fields:
[ADR 0013](../adr/0013-proposing-an-outcome.md). Why an early close does the
opposite and gates on the clock, and why it is the one route any administrator
may call: [ADR 0014](../adr/0014-closing-a-market-early.md).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/markets` | Save a draft, or submit a market |
| POST | `/markets/{id}/publish` | Publish a submitted market |
| POST | `/markets/{id}/close` | Close an open market early, with a reason |
| POST | `/markets/{id}/propose-outcome` | Propose the winner of a closed market |
| GET | `/markets` | List my own markets |
| GET | `/markets/{id}` | Read one of my own markets |
| GET | `/health` | Liveness and readiness probe |

Every `/markets` route requires an **administrator** token. There is no
unauthenticated read here; the public market API for traders is [BE][X] #62.

Every route but one is scoped to the administrator who created the market, and
answers `404` to any other. The exception is `POST /markets/{id}/close`: any
administrator may stop any open market, and the audit entry names who did.
[ADR 0014](../adr/0014-closing-a-market-early.md).

## The five statuses

| Status | Set by | Traders see it |
| --- | --- | --- |
| `draft` | the three-second autosave | no |
| `submitted` | the submit button | no |
| `open` | `POST /markets/{id}/publish` | yes |
| `closed` | the clock at `close_time`, or `POST /markets/{id}/close` | yes, but not tradeable |
| `pending_resolution` | `POST /markets/{id}/propose-outcome` | yes, with the proposal |

The path is `draft → submitted → open → closed → pending_resolution` and
nothing skips a step. Every save on a market past `submitted` is a `409`,
whatever the request asks for — the terms are final from `open` onwards.

Only one transition is ever reversed, and it is not in this ticket: [3.2] #10's
rejection sends a market from `pending_resolution` back to `closed`, with a
reason. There is still no unpublish and no reopen.

`closed` is the only one two different things produce. A market reaches it on
its own when `close_time` passes, and an administrator can reach it early with
`POST /markets/{id}/close` ([2.3] #7). The two are the same state in every
respect — same refusals, same next move — and the only way to tell them apart
from outside the audit log is that an early close leaves `closed_at` **before**
`close_time`.

### `close_time` is when trading stops — not `status` — [F-4] #44

**Read this before writing anything that decides whether a market can be
traded.** A market stops accepting trades the instant `close_time` passes. The
`closed` status is written a few seconds later by a background sweep, so there
is a short window in which a market whose closing time has passed still reads
`"status": "open"`.

That window is not a window in which the market is tradeable. Trading is gated
on `close_time`, which is exact and needs no job to have run, so a trade in
that window is refused whatever the status says. What the window affects is
display and counting.

For a client, the rule is:

```js
const tradeable = market.status === "open" && new Date(market.close_time) > new Date();
```

Never `market.status === "open"` on its own. A countdown that hits zero should
switch the UI to closed immediately rather than waiting for the status to catch
up on the next fetch — the backend already agrees with the countdown.

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
  "status": "draft",                                     // "draft" | "submitted" only

  "question": "Will Singapore core inflation be below 2% for December 2026?",
  "description": "Measured on the first published print.",

  "outcomes": [{ "label": "Yes" }, { "label": "No" }],

  "close_time": "2027-01-05T12:00:00Z",
  "resolution_time": "2027-01-20T12:00:00Z",

  "resolution_criteria": "Resolves YES if the MAS core inflation print for December 2026, as first published, is strictly below 2.0%. Later revisions do not change it.",
  "resolution_sources": [
    { "url": "https://www.mas.gov.sg/statistics", "label": "MAS statistics" }
  ],

  "liquidity_b": 100,        // optional; omit to take the server's default
  "seed_subsidy": 250        // no default; required to submit
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
`open` is **not accepted here** and is a `422` — publishing is
`POST /markets/{id}/publish`, which carries no terms at all, so that no single
request can change a market and expose it to traders at the same time.

**Timestamps must carry an offset.** `2027-01-05T12:00:00Z` or
`2027-01-05T20:00:00+08:00`. A value without one is a 422 rather than a guess,
because guessing UTC would put a Singapore close time eight hours out.

**`creator_id` in the body is ignored.** The creator is taken from the token.

### Pricing — [1.2] #2

`liquidity_b` is the LMSR liquidity parameter. Higher means each trade moves
the price less, and the platform's worst-case loss is larger. **Omit it and the
server applies its configured default** (`DEFAULT_LIQUIDITY_B`, 100 unless
deployed otherwise), so a market is priceable from the very first autosave and
the form can show a worst case immediately. Send a value to override it, on that
save or any later one.

`seed_subsidy` is the mock credits the platform puts up to cover that loss. It
has **no default** — there is no sensible platform-wide answer to how much a
particular market is worth underwriting — so it must be filled in before the
market can be submitted.

Both must be greater than zero if present: `0` or a negative number is a `422`
even on an autosave. Absence is fine at any point; it is a submission rule, not
a shape rule.

**Both are bounded by what the column stores** — at most `99999999999999.9999`,
and at most four decimal places. A larger value or a fifth decimal place is a
`422` naming the field. The scale matters more than it looks: without it
`0.00001` would pass, round to `0.0000` in the database, and reload as a market
that fails its own "greater than zero" rule while the save response still
reported `0.00001`.

Nothing is charged to anybody. Recording a subsidy does not move credits — that
is the ledger's job ([F-1] #41) and this service holds no balances.

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
    "outcomes": [
      { "id": "...", "position": 0, "label": "Yes", "initial_price": 0.5 },
      { "id": "...", "position": 1, "label": "No",  "initial_price": 0.5 }
    ],
    "close_time": null,
    "resolution_time": null,
    "resolution_criteria": null,
    "resolution_sources": [],
    "liquidity_b": 100.0,
    "seed_subsidy": 250.0,
    "created_at": "2026-09-13T14:06:38.907220Z",
    "updated_at": "2026-09-13T14:06:38.907222Z",
    "submitted_at": null,
    "published_at": null,
    "closed_at": null,

    // derived, read-only — see below
    "max_platform_loss": 69.31471805599453
  },
  "blocking_submission": [
    { "field": "close_time", "message": "A close time is required." },
    { "field": "outcomes",   "message": "A market needs at least 2 named outcomes." }
  ]
}
```

**`max_platform_loss` and `initial_price` are derived and read-only.** Sending
them changes nothing.

`max_platform_loss` is `b × ln(n)` — the most the platform can lose over this
market's life, whatever traders do. Show it beside `seed_subsidy`; that
comparison is the whole point of [1.2] #2's second criterion. It is `null` until
`liquidity_b` is set and at least two outcomes are named.

`initial_price` is what each outcome costs before anyone has traded: `1/n`,
identical across outcomes, which is the third criterion. It appears as soon as a
second outcome is named and does not depend on `b`. The values are unrounded so
they sum to 1 — three outcomes give `0.3333…` each, and formatting is the
browser's job.

**`n` counts only named outcomes.** A row the admin has added but not yet
labelled is not an outcome: it gets `initial_price: null` and does not move
`max_platform_loss`. Two named outcomes beside two empty rows price as `b × ln
2` and `0.5` apiece, which is the market that will actually submit — the same
count `blocking_submission` uses. Below two named outcomes both are `null`.

Both are computed server-side so there is one definition. Do not reimplement
either in the frontend.

All four pricing values come back as JSON **numbers**, not strings, so
`seed_subsidy` and `max_platform_loss` can be compared and formatted directly.

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
| `liquidity_b` | present and greater than zero (the default satisfies this) |
| `seed_subsidy` | present and greater than zero |

**A refused submission writes nothing**, including any edits that came with it.
Nothing is lost: the autosave saves them three seconds later.

`resolution_criteria` is required alongside the URLs on purpose. A source says
where to look; the criteria say what settles it — first print or revised, and
what happens if publication slips. That gap is what [3.3] #11's dispute window
exists to absorb.

## POST /markets/{id}/publish — [1.3] #3

Moves a market from `submitted` to `open`, which is the status traders browse
on. This is what makes a market tradeable.

**No request body.** The terms that go live are the terms that were submitted.
Addressed by the market's `id`, not by `draft_key`: this is not a save and it
cannot create anything.

### Response

`200`, and the whole `market` object exactly as `GET /markets/{id}` returns it
— not wrapped in `{ "market": ... }`, because there is no `blocking_submission`
to sit beside. `status` is `open` and `published_at` is set. Repaint from this
rather than issuing a second `GET`.

### When it is refused

| Status | `code` | Meaning | What the admin should do |
| --- | --- | --- | --- |
| 404 | `market_not_found` | no such market, or it is not yours | nothing; it is not there |
| 409 | `market_not_submitted` | it is still a draft | press submit first |
| 409 | `market_already_open` | it is already live | reload; hide the button |
| 409 | `market_closed` | it has passed its closing time | reload; it is finished |
| 422 | `draft_incomplete` | the terms no longer pass | fix the fields in `details` |

**Every submission rule runs again, against the clock now.** That is not
belt-and-braces. A market is validated at submission against the time it was
submitted, and `close_time` has to be in the future — so a market that sat
submitted past its own close time would otherwise go live already closed.

The `422` is the **same** `draft_incomplete` envelope the submit button
returns, with the same `details` array keyed the same way. A form that already
renders a refused submission renders a refused publish with no new branch. Only
the message differs.

**A refused publish writes nothing.** The market stays `submitted`,
`published_at` stays null, and no audit entry is appended.

### Publishing is one way

Once a market is `open` its terms are frozen. `POST /markets` on it returns
`409 market_already_open`, whether the request says `draft` or `submitted` —
which is what stops the autosave, still running behind the publish button, from
quietly reverting a live market. There is no unpublish.

Once it is `closed` the same save returns `409 market_closed`, and so does a
publish. That is the same form, still open behind the publish button, still
ticking when the market's closing time arrived.

### Trader visibility

Publishing sets the status; it does not build the trader-facing list. The
public browse and detail API is **[BE][X] #62**, and it filters on `status ==
"open"` **and** a `close_time` still in the future — both halves, for the reason
given under "`close_time` is when trading stops" above. Until it lands, a
published market is visible through the admin routes on this page and nowhere
else.

## POST /markets/{id}/close — [2.3] #7

Stops an `open` market before its `close_time` and moves it to `closed`.
Trading stops the moment this commits. Positions are untouched, and the
market's next move is the same one it would have had if it had run to its
closing time: [3.1] #9's proposed outcome.

### Request

```json
{
  "reason": "The resolution source retracted its December print, so this question can no longer be settled as written."
}
```

| Field | Rule |
| --- | --- |
| `reason` | required; at least 10 characters after trimming, at most 5000 |

The reason is recorded in the audit log against the administrator who closed
the market, in the same transaction, so a market cannot stop early without a
record of why. **It is kept nowhere else.** It is not a field on the market and
it does not come back in the response — only [4.3] #15's log view can show it.

### Response

`200`, and the whole `market` object exactly as `GET /markets/{id}` returns it:

```json
{
  "status": "closed",
  "close_time": "2027-01-05T12:00:00Z",
  "closed_at": "2026-09-16T09:14:02.118374Z"
}
```

`close_time` is **not** rewritten. It is the closing time that was published and
that traders read, and the `market.published` audit entry recorded it. A
`closed_at` earlier than `close_time` is exactly what an early close looks like
from outside; when the clock closes a market instead, `closed_at` lands a few
seconds *after* `close_time`.

### Only an open market

| Status | `code` | Meaning | What the admin should do |
| --- | --- | --- | --- |
| 404 | `market_not_found` | no such market | nothing; it is not there |
| 409 | `market_not_open` | still a draft or submitted | nothing to stop; publish it or leave it |
| 409 | `market_closed` | it has already stopped | reload; hide the control |
| 409 | `market_pending_resolution` | stopped, and an outcome is proposed | reload; show whose proposal is waiting |
| 422 | `close_incomplete` | the reason is missing or too short | fix `reason` and resend |

**A market whose closing time has just passed is a `409 market_closed`, even
while it still reads `"status": "open"`.** This is the opposite of what
`propose-outcome` does with the same window, and deliberately: that market has
already stopped trading, the clock stopped it, and accepting a close here would
file an audit entry saying an administrator did. Nothing is lost — the market
is closed either way, one sweep later at the outside.
[ADR 0014](../adr/0014-closing-a-market-early.md) has the full argument.

The `422` is this service's own envelope, the same shape a refused submission
uses, with a different `code`:

```json
{
  "error": {
    "code": "close_incomplete",
    "message": "This market cannot be closed without a reason.",
    "details": [
      { "field": "reason", "message": "The reason must be at least 10 characters, so that somebody reading the log later can tell what happened." }
    ]
  }
}
```

A body with no `reason` key at all never reaches this service and comes back as
FastAPI's `{"detail": [...]}` instead. Branch on the presence of `error`.

**A refused close writes nothing.** The market stays `open`, `closed_at` stays
null, and no audit entry is appended — so the modal can reopen with whatever
the administrator had typed.

### Who may close

**Any administrator, including one who did not create the market.** This is the
only route on this page that is not scoped to the creator, because a broken
market that only its author can stop is not oversight. ADR 0007 makes the admin
tier flat and the audit entry is what keeps that accountable: it names whoever
reached in, and why.

The read is *not* widened with it. `GET /markets/{id}` still answers `404` to
another administrator, so until [BE][X] #62's public browse lands, an overseer
needs the market's id from somewhere other than this service.

### One way

There is no reopen. Every later save on the market is a `409 market_closed`,
and a second close is the same — `closed_at` keeps saying when trading actually
stopped.

## POST /markets/{id}/propose-outcome — [3.1] #9

Moves a market from `closed` to `pending_resolution`, naming the outcome the
administrator believes won and the evidence for it. Nothing is settled here and
no credits move: [3.2] #10 asks a second administrator to approve or reject,
and [3.4] #12 pays out.

### Request

```json
{
  "winning_outcome_id": "8b4c0f21-2f7a-4a1e-8a0f-1d2c3b4a5e6f",
  "evidence_url": "https://www.mas.gov.sg/statistics/cpi-december-2026",
  "evidence_note": "MAS published December 2026 core inflation at 1.8% on 23 January, below the 2.0% threshold in the resolution criteria."
}
```

| Field | Rule |
| --- | --- |
| `winning_outcome_id` | required; must be the `id` of one of **this** market's `outcomes` |
| `evidence_url` | full `http`/`https` address, at most 2048 characters |
| `evidence_note` | at least 10 characters, at most 5000 |

**At least one of `evidence_url` and `evidence_note` is required.** Either
alone is fine. Whichever is sent has to be usable — a URL that does not parse
and a two-character note are both refused, even when the other field would have
satisfied the requirement on its own, because storing a dead link puts one in
front of [3.3] #11's disputer.

### Response

`200`, and the whole `market` object exactly as `GET /markets/{id}` returns it.
`status` is `pending_resolution` and six fields are now set:

```json
{
  "status": "pending_resolution",
  "proposed_outcome_id": "8b4c0f21-2f7a-4a1e-8a0f-1d2c3b4a5e6f",
  "proposed_by_id": "1c9e5d3a-77b2-4f0c-9a1d-5e6f7a8b9c0d",
  "proposed_by_username": "ernest_t",
  "proposed_at": "2026-09-16T09:14:02.118374Z",
  "proposal_evidence_url": "https://www.mas.gov.sg/statistics/cpi-december-2026",
  "proposal_evidence_note": "MAS published December 2026 core inflation at 1.8% …"
}
```

They are null together on every market that has not been proposed for, and set
together on every market that has. `proposed_outcome_id` names a member of the
same response's `outcomes` array — look the label up there rather than
expecting it twice.

### Only a closed market

| Status | `code` | Meaning | What the admin should do |
| --- | --- | --- | --- |
| 404 | `market_not_found` | no such market, or it is not yours | nothing; it is not there |
| 409 | `market_not_closed` | it is still running | wait; the market has not finished |
| 409 | `market_pending_resolution` | an outcome is already proposed | reload; show whose proposal is waiting |
| 422 | `proposal_incomplete` | the winner or the evidence is wrong | fix the fields in `details` |

**A market whose closing time has just passed is a `409`.** For a few seconds
after `close_time`, `status` still reads `"open"` because the sweep has not run
yet — see "`close_time` is when trading stops" above. Proposing is refused for
that window. Nothing can be traded in it either, so nothing is lost; the propose
control simply appears on the next fetch. This is the one place the backend
gates on the status column rather than on the clock, and
[ADR 0013](../adr/0013-proposing-an-outcome.md) explains why that is safe here
and not on the trade path.

The `422` is this service's own envelope, the same shape a refused submission
uses, with a different `code`:

```json
{
  "error": {
    "code": "proposal_incomplete",
    "message": "This outcome cannot be proposed yet.",
    "details": [
      { "field": "evidence_url", "message": "Enter a full http or https address a trader can open." },
      { "field": "evidence", "message": "Give a source URL or a written note, so the decision can be checked by somebody who was not in the room." }
    ]
  }
}
```

`field` is one of `winning_outcome_id`, `evidence_url`, `evidence_note`, or
`evidence` — the last of which is the pair rather than an input, and belongs
beside the two evidence fields rather than on either.

**A refused proposal writes nothing.** The market stays `closed`, every
`proposed_*` field stays null, and no audit entry is appended.

### One proposal at a time

A second proposal is a `409 market_pending_resolution`, not an overwrite. The
first proposal is what a second administrator is being asked to agree to, and
replacing it underneath them is the one thing this status exists to prevent.
The way back to `closed` is [3.2] #10's rejection, which carries a reason.

While a market is `pending_resolution` its terms are frozen exactly as they are
when it is `open`: `POST /markets` on it returns `409
market_pending_resolution`, and so does a publish.

### Who may propose

The market's creator, like every other route on this page. Another
administrator gets `404`, not `403`, for the reason a 404 is used everywhere
else here. [3.2] #10 is the ticket that widens this, because its own acceptance
criteria require a *different* administrator to approve.

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

`draft_incomplete`, `proposal_incomplete` and `close_incomplete` add a
`details` array. Nothing else does, and it is additive, so a client that ignores
it still reads `code` and `message`:

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
| 409 | `market_not_submitted` | publish asked for on a market that is still a draft |
| 409 | `market_already_open` | a publish or a save arrived for an already-published market |
| 409 | `market_closed` | a publish, save or early close arrived for a market past its closing time |
| 409 | `market_not_closed` | an outcome was proposed for a market that is still running |
| 409 | `market_not_open` | an early close arrived for a market that is not published |
| 409 | `market_pending_resolution` | a proposal, publish, save or close arrived for a market already awaiting one |
| 422 | `draft_incomplete` | submission or publication refused; see `details` |
| 422 | `proposal_incomplete` | outcome proposal refused; see `details` |
| 422 | `close_incomplete` | early close refused; see `details` |
| 422 | — | FastAPI's own body-validation error, a different shape |

Note that `market_closed` and `market_not_closed` are not opposites of each
other in any useful sense — one is a save or publish that arrived too late, the
other a proposal that arrived too early. They never appear on the same route.

Two kinds of `422` exist and they do not look alike. `draft_incomplete`,
`proposal_incomplete` and `close_incomplete` are ours and carry the envelope
above. A malformed body — a missing `draft_key`, a missing `reason`, a naive
timestamp, a non-UUID — is FastAPI's, and comes back as `{"detail": [...]}`.
Branch on the presence of `error`.

## Notes for [FE][1.1] #45

1. `crypto.randomUUID()` once when the form opens; hold it for the form's life.
2. Debounce roughly 3 s of inactivity, then POST with `status: "draft"`.
   Skip the call if nothing changed since the last one.
3. Paint `blocking_submission` as inline hints. Disable the submit button while
   it is non-empty and you will never see a 422 from `draft_incomplete`.
4. On submit, POST the same `draft_key` with `status: "submitted"`.
5. A `409 market_not_editable` means the market is already submitted — stop the
   autosave timer. A `409 market_already_open` means it is published: stop the
   timer and hide the edit controls, because nothing will be accepted again. A
   `409 market_closed` means the market stopped while the form was open —
   either its closing time passed or an administrator closed it early ([2.3]
   #7), possibly a different one; treat both the same way.
6. Send timestamps as ISO 8601 **with an offset**:
   `new Date(value).toISOString()` produces one.
7. `status: "submitted"` is not published. Show the publish control only on a
   submitted market, and POST to `/markets/{id}/publish` with no body. Sending
   `status: "open"` to `POST /markets` is a `422`; publishing has its own
   endpoint so that one call can never both change terms and expose them.
8. A publish can come back `422 draft_incomplete` even though the market
   submitted cleanly — most often because its `close_time` has passed in the
   meantime. Reuse the same `details` renderer you already have; there is no
   new error shape to handle.
9. Render `max_platform_loss` beside the subsidy input and let it update on
   every save. Do not compute `b × ln(n)` in the browser — the server is the
   one definition, and a second one will drift.
10. A subsidy smaller than `max_platform_loss` **is allowed** and submits
    normally. Warn in the UI if you like; do not disable the button for it.
11. Round pricing inputs to four decimal places before sending, and keep them
    under `99999999999999.9999`, or the save comes back `422`.

## Notes for [FE][2.3] #56

1. Show the close control only on a market that is genuinely trading:
   `status === "open"` **and** `close_time` still in the future. It is the same
   `tradeable` expression as everywhere else on this page — a market whose
   countdown has hit zero cannot be closed early, because it has already
   closed.
2. The modal has one required input. Enable its confirm button at 10 trimmed
   characters and you will never see a `422 close_incomplete`.
3. Paint `details` the way you already paint `blocking_submission`: same shape,
   same keys. There is only ever one entry, on `field: "reason"`.
4. Say in the modal that the reason goes into the admin log and cannot be
   edited afterwards. It is the only record of why the market stopped, and
   nothing in this API will ever read it back — do not build a view that
   expects `reason` on a market.
5. Repaint from the `200` response; it is the whole market. `closed_at` is set,
   `close_time` is unchanged, and the propose control appears on the next fetch
   in the ordinary way.
6. A `409 market_closed` means somebody got there first, or the clock did.
   Reload and drop the control. A `409 market_not_open` means the market was
   never published and the control should not have been shown.
7. This is the one control to show on **another** administrator's market. The
   creator's own routes still answer `404` to everybody else, so drive it from
   a list you already have rather than from `GET /markets/{id}`.
8. There is no undo. Confirm destructively — the market cannot be reopened and
   traders will see it as closed immediately.

## Notes for [FE][3.1] #52

1. Show the propose control only on a market whose `status` is `closed`. A
   market that is `open` with a `close_time` in the past is **not** ready yet,
   even though it is no longer tradeable — wait for the next fetch. Do not
   derive `closed` in the browser for this one; it is the only place on this
   page where the status column is the right thing to read.
2. Render the outcome choices from the market's own `outcomes` array and send
   the chosen `id` as `winning_outcome_id`. Never send a label or an index.
3. Enable the submit button when a winner is picked **and** at least one of the
   evidence fields is filled in. Do that and you will never see a
   `422 proposal_incomplete`.
4. Paint `details` the way you already paint `blocking_submission`: same shape,
   same keys. `field: "evidence"` has no input of its own — show it under the
   pair of evidence fields.
5. A `409 market_pending_resolution` means somebody got there first. Reload and
   show `proposed_by_username` and `proposed_at` instead of the form.
6. Repaint from the `200` response; it is the whole market. There is no need
   for a follow-up `GET`.
7. Nothing is resolved yet. Label this state "awaiting approval", not
   "resolved" — [3.2] #10 can still send it back to `closed`, which clears
   every `proposed_*` field.
