# ADR 0004: One idempotent endpoint for both autosave and submission

- **Status:** Accepted
- **Date:** 2026-09-13
- **Affects:** [1.1] #1, [1.3] #3, [1.4] #4, [FE][1.1] #45
- **Implemented in:** `backend/market_service/service/`, `controller/routes.py`

## Context

The market creation form autosaves after roughly three seconds of inactivity,
and also has a submit button. Both go to `POST /markets`. That raises three
problems that have nothing to do with each other.

**A POST that creates would create constantly.** An idle timer on an open form
fires dozens of times. Naively, that is dozens of abandoned markets.

**A half-typed form fails every rule.** Close time missing, one outcome named,
no sources yet. If the autosave validated, it would reject almost every call it
ever made, and the admin's work would not be saved.

**"Submitted" is not "published".** [1.3] #3 already owns DRAFT → OPEN, with
its own acceptance criteria including an audit-log entry.

## Decision

**Three states, of which this ticket builds two.**

| Status | Set by | Validated | Visible to traders |
| --- | --- | --- | --- |
| `draft` | the three-second autosave | no | no |
| `submitted` | the submit button | fully | no |
| `open` | [1.3] #3's publish | — | yes |

`submitted` means complete and internally consistent, not live. Publishing
stays [1.3] #3's job, and the audit-log requirement stays with it.

**Idempotency comes from a client-generated `draft_key`.** The browser
generates one UUID when the create form opens and sends it on every save for
that form. The server upserts on `(creator_id, draft_key)`, answering 201 the
first time and 200 after.

**Validation runs on both, but only blocks one.** Every save computes the full
list of problems. An autosave returns them in `blocking_submission` and saves
anyway. A submission with a non-empty list is refused with 422 and writes
nothing at all.

## Why a client key rather than POST-then-PUT

The conventional REST shape is POST to create, then PUT to the returned id.
It needs two endpoints and it still has a hole: if the first POST's response is
lost in transit, the browser has no id, retries, and creates a second market.

A client-generated key closes that. The retry carries the same key and updates
the row the lost response was about. The identity of the thing being edited is
decided by the party that knows when editing started, which is the browser.

The cost is that the frontend must generate and hold a UUID. That is one line
of `crypto.randomUUID()` in #45.

## Consequences

**A draft may be nonsense, and that is the point.** Every market column except
the identifiers is nullable. Blank outcomes persist. `MAX_OUTCOMES` and the
field lengths are still enforced, because those are shape, not completeness,
and a 50 kB question is not a half-typed form.

**Autosave never surprises the admin, but it does tell them.** Nothing is ever
rejected for being incomplete, yet every response carries exactly what is
missing, keyed by form field including array positions such as
`outcomes[1].label`. Michelle can mark the form live without duplicating these
rules in the browser, and there is one definition of "ready" rather than two
that can drift.

**A refused submission is atomic.** It persists nothing, including edits that
arrived in the same request. Losing them would be unacceptable if the autosave
did not exist; it does, and it fires three seconds later. The alternative — a
422 that had also written half the change — is a worse contract than one that
simply does nothing.

**There is no unique index on outcome labels.** A half-typed form routinely
holds two blank rows, and a database constraint would make the autosave start
failing exactly when the admin is mid-thought. Label uniqueness, case-folded,
is a submission rule in `service/validation.py` instead. The positional
constraint stays, because the server assigns positions and a violation there
can only mean a bug.

**A submitted market cannot be autosaved back to a draft.** The form may still
be open when the timer next fires, and letting that through would silently
un-submit a finished market. Changing one means submitting again, which re-runs
every rule. The limitation is that intermediate work on a submitted market
cannot be parked half-broken. Reopening properly is [1.4] #4's problem.

**Last write wins, per draft key.** Two browser tabs on one draft overwrite
each other with no warning. Not solved: a draft has one author by definition,
and optimistic concurrency would cost the frontend a version token on every
autosave to protect against a case that should not arise.

**Timestamps must carry an offset.** `2027-01-01T00:00:00` is a 422, not a
guess. Assuming UTC would put a Singapore admin's close time eight hours out,
and a market that closes at the wrong hour settles on the wrong facts.

**Validation is a pure function over the entity.** `problems_blocking_submission`
takes a `Market` and an injected `now`, touching no session and no request, so
[1.3] #3's "publish blocked unless all required fields are present" and [1.4]
#4's edit rules reuse it rather than re-deriving it.

## Resolution sources

Required at submission: at least one openable `http`/`https` URL, plus a
free-text `resolution_criteria`.

The criteria field is the part a bare URL misses. "Resolves from the MAS
website" says where to look, not what settles it — first print or revised, what
happens if publication slips past the resolution time, which figure exactly.
That gap is what [3.3] #11's dispute window exists to absorb, and making the
creator write the rule while they still have the market in their head is the
cheapest place to close it.

Sources are stored ordered, with an optional label, so more can be added
without a schema change. Non-URL evidence — a screenshot, a PDF, a snapshot of
a page that later changes — is deliberately out of scope here: it is evidence
for [3.1] #9's outcome proposal, which is a different moment with a different
author.
