# ADR 0016: Deciding a proposal, by any administrator but the proposer, with APPROVED as a status

- **Status:** Accepted
- **Date:** 2026-09-17
- **Affects:** [3.2] #10, [3.1] #9, [3.3] #11, [3.4] #12, [2.1] #5, [BE][X] #62, [4.3] #15, ADR 0007, ADR 0013, ADR 0014
- **Implemented in:** `backend/market_service/service/market_service.py` (`approve_outcome`, `reject_outcome`, `_proposal_to_decide`), `service/validation.py`, `model/entities.py`, `model/schemas.py`, `controller/routes.py`, `sql/migrations/0006-market-outcome-approval.sql`

## Context

[3.2] #10 is the two-person rule the resolution epic is built around — "so no
single person controls payouts" — and its three acceptance criteria are:

- approve control disabled for the proposing admin
- approver can reject with a reason, returning status to CLOSED
- both proposer and approver identities stored on the market

Each of them leaves a choice the issue text does not make.

**What an approved market is.** The criteria say where a rejection goes and say
nothing about where an approval goes. [3.3] #11 says "approval starts a
configurable window", and [3.4] #12 says settlement moves the market to
SETTLED. Something has to hold the market between those two moments, and it
can be a status or a column.

**Who may decide.** Every route in the market service but one answers 404 to
anybody other than the creator. ADR 0013 deliberately kept proposing with the
creator and named this ticket as the one that would have to widen the read,
because its own criterion requires a *different* administrator to act.

**What "disabled for the proposing admin" means on the server.** A disabled
button is a frontend fact. The backend has to refuse the request the button
would have sent, and has to decide whether that refusal covers rejecting as
well as approving, what status code it is, and what it compares.

**Where a rejection's reason and the rejected proposal go.** The market's
proposal columns are about to be cleared. ADR 0013 promised that the audit log
would be the durable record of a proposal; this is the ticket that has to make
that true.

## Decision

### Two endpoints, each with a body

`POST /markets/{id}/approve-outcome` and `POST /markets/{id}/reject-outcome`.
One endpoint per transition, as ADR 0008 decided and ADR 0013 and 0014
followed: the route list is the list of legal moves.

**Approve carries only which proposal it approves.** `OutcomeApprovalRequest`
has one required field, `proposal_id`, and nothing about the proposal itself:
the approver is agreeing with a winner and evidence somebody else supplied, not
adding any. An optional note was considered and declined below. Why the
proposal has to be named at all is its own section.

**Reject carries the same `proposal_id`, and one required `reason`**, the shape ADR 0014 reserved for this
ticket: `OutcomeRejectionRequest` requires the key, so a body without it is
FastAPI's own 422, and what it says — non-blank, at least ten characters after
trimming — is a content rule in `service/validation.py` that comes back as
`422 rejection_incomplete` with the `details` array every other refusal here
uses. `RejectionIncomplete` is the fourth `IncompleteError`, and
`MIN_REJECTION_REASON_LENGTH` is its own constant beside
`MIN_CLOSE_REASON_LENGTH`, for the reason those constants give each other:
different rules that agree on a number today. The one check behind both is a
shared private function whose floor is an argument.

### APPROVED is a status

`MarketStatus.APPROVED = "approved"`, reached only from PENDING_RESOLUTION.

The alternative was to leave the market PENDING_RESOLUTION and let a non-null
`approved_at` say it had been approved. It was rejected because the market's
legal moves change at approval, and a status is how this service says that:

- A second approval, a rejection, a new proposal and an early close are all
  refused from here, and each is told `market_already_approved`. Without the
  status, every one of those gates would read `approved_at IS NULL` beside
  `status`, and `market_pending_resolution`'s message — "waiting on a second
  administrator" — would be false for half the markets it fired on.
- [3.3] #11 and [3.4] #12 have a working set: approved markets whose window has
  run out. `status = 'approved'` with `approved_at` is exactly the shape
  `ix_markets_due_close` gave the close sweep on `status = 'open'`, and a
  partial index on it is theirs to add beside the query that needs it.
- [2.1] #5's filters are UI buckets rather than the status set — they already
  omit `submitted` — so whether `approved` folds into "pending resolution" or
  gets a bucket of its own is that ticket's call, not a cost of this one.

Not RESOLVED: nothing is resolved while [3.3] #11 lets a trader dispute it, and
[3.4] #12 names its terminal state SETTLED. Like PENDING_RESOLUTION before it,
the member is a Python change only — the column is a non-native `Enum` with no
CHECK — and APPROVED joins `_FROZEN_STATUS_ERRORS`, so every save and publish
on an approved market is refused.

### Approval adds three columns and keeps the proposal

`approved_by_id`, `approved_by_username` and `approved_at`, snapshotted as the
proposer's are because this service cannot resolve an id to a name (ADR 0003).
All three are null unless the market is APPROVED and are written in the one
transaction that sets it.

The seven proposal columns — `proposal_id` and the six [3.1] #9 added — stay
exactly as they were. An approval agrees with a
proposal rather than replacing it, so `proposed_outcome_id` is still the winner
[3.4] #12 settles against, and the row carries both identities side by side —
which is the third criterion, met on the market rather than only in the log.

No deadline column. [3.3] #11's window is `approved_at` plus a setting, derived
the way the close sweep derives from `close_time`, and whether #11 wants to
snapshot the end of it is an additive decision for #11.

### Any administrator but the proposer, and the proposer is refused both ways

Both endpoints read through `get_any`, so neither is scoped to the creator. The
proposer is necessarily the creator — `propose_outcome` still uses the
creator-scoped `get` — and the approver must differ from the proposer, so every
approver there will ever be is someone who did not create the market. A
creator-scoped decision route would be a route nobody could use.

The rule is `actor.id == market.proposed_by_id`, checked on the locked row:

- **On the id, never the username.** A username can be changed and reissued;
  "a different administrator" is a claim about the account. ADR 0013 said
  this ticket would compare the id, and it does.
- **Against the proposer, not the creator.** They are the same person today.
  The rule this ticket enforces is about who proposed, and writing it against
  `creator_id` would silently change meaning the day proposing is widened.
- **On rejection as well as approval.** ADR 0013 built no un-propose, because
  a self-service withdrawal would be "a second path to the same state with no
  second administrator in it". A proposer rejecting their own proposal is that
  path under a different name.

The refusal is **`403 second_administrator_required`**. `core/errors.py`
already defines this repository's 403 as "the session is fine and this account
will never be allowed in; retrying cannot help", which is precisely the
proposer's position with respect to this proposal. It is not a 409 — the market
is in exactly the right state, for somebody else — and not a 404, because there
is no draft here to hide.

### A decision names the proposal it was made about

Every proposal gets a fresh `proposal_id` when `propose_outcome` writes it, and
a rejection clears it with the rest. Approve and reject must both send back the
id of the proposal the administrator reviewed, and the service compares it with
the row's under the lock. A mismatch is **`409 proposal_superseded`**, and
nothing is written.

Without it, the market id is the only thing a decision names, and that is not
enough. Reviewer A opens proposal 1. Reviewer B rejects it, and the creator
proposes 2, naming the other outcome. A, still reading 1, presses approve — and
approves 2, a winner and evidence A never saw. The same stale page pressing
reject clears 2 with a reason written about 1, and the log then says 2 was sent
back for it. This was found in review of the first cut of this ticket.

**The row lock cannot catch it, and that is the point to take away.** ADR 0015's
lock serialises decisions that *overlap*. Here nothing overlaps: B's rejection
and the creator's new proposal have both committed before A's request arrives.
A's request takes the lock, finds a market that is PENDING_RESOLUTION with a
proposer who is not A, and every check passes — because every check is about
the market's state, and the state is identical before and after the cycle. Only
something that identifies the *proposal* can tell them apart.

It is **required**, not optional. An optional precondition is one a client can
forget, and the request it lets through is precisely the stale one it exists to
refuse.

It is also **nullable**, for one kind of proposal: one already pending when
`proposal_id` was added. Such a proposal has no id — see the migration below
for why it is not given one — and is decided by quoting `null`, which is what
its row and its audit entry both say. The key must still be present; only its
value may be null. A null cannot match anything else. Every proposal made since
is minted an id, and a rejection returns the market to CLOSED rather than
leaving it pending with a null, so a market is never pending with a null id
again. A stale null quoted against a replacement is `proposal_superseded` like
any other stale id.

A 409 rather than a 412. This service puts preconditions in bodies rather than
in `If-Match` headers, and its 409 already means "the market is not what you
think it is; reload" — which is exactly the remedy.

The id also goes into `_proposal_snapshot`, so the `market.outcome_proposed`,
`market.outcome_approved` and `market.outcome_rejected` entries all carry it.
A market proposed for twice has two proposal entries in the log, and each
decision names exactly which one it decided instead of leaving a reader to
match timestamps.

### The order of the checks is part of the contract

`_proposal_to_decide` is the one function both endpoints go through, so they
cannot drift apart on it:

1. Load the market unscoped, **with the row locked**. A double-clicked approve,
   two administrators rejecting at once, and one approving while another
   rejects all end on this row; under READ COMMITTED the loser blocks and
   re-reads what the winner committed. ADR 0015.
2. **State.** APPROVED is `market_already_approved`; anything else that is not
   PENDING_RESOLUTION is `market_not_pending_resolution`.
3. **Identity.** The proposer is `second_administrator_required`.
4. **Which proposal.** A `proposal_id` that is not the row's is
   `proposal_superseded`.
5. **Content**, for a rejection only: `rejection_incomplete`.

State before identity, so a proposer looking at a market somebody else already
approved is told it is approved — the fact that matters — rather than that
they may not approve it. Identity before content, so a proposer who types "x"
is told they may not decide at all rather than to write a longer reason for a
decision they may not make. Identity before which proposal, because
`proposal_superseded` tells the caller to reload and try again, which would not
help a proposer. Which proposal before content, so nobody is asked to improve a
reason about a proposal that is no longer there. This is the ordering
`close_early` already uses for state and reason.

### A rejection clears the proposal, and the log keeps it

A rejection writes `status = CLOSED` and nulls all seven proposal columns. The
market is then exactly what it was before anybody proposed, and the creator
may propose again. `closed_at` is not touched: it says when trading stopped,
and a rejection does not change that.

There are no `rejected_by_*` columns. Once the proposal is gone from the row
there is nothing on it for a rejecter's name to describe, and a pair of columns
describing the *previous* proposal beside a *new* one would invite exactly the
misreading the clear exists to prevent.

The audit log is the record instead, and ADR 0013's promise is kept by what the
entry carries. `market.outcome_rejected` is written under the rejecting
administrator with the reason in `reason` and eight keys in `context` —
`proposal_id`, `winning_outcome_id`, `winning_outcome`, `evidence_url`, `evidence_note`,
`proposed_by_id`, `proposed_by_username`, `proposed_at` — taken **before** the
columns are cleared. `audit_svc` holds no grant on `market.markets`, so a fact
not copied onto the entry is a fact the one role that can read the log cannot
recover. With this entry and the proposer's own, a rejected proposal is
reconstructable from the log alone.

`market.outcome_approved` carries the same eight keys and a null `reason`, so
"what was decided about this proposal" is one question whichever way it went,
and `reason IS NOT NULL` keeps meaning "somebody had to explain themselves".
Both are appended after the flush and before the commit, on the request's own
session. ADR 0006.

## Consequences

**One migration, for the columns only.** `sql/migrations/0006-market-outcome-approval.sql`
adds `proposal_id` and the three approval columns to `cs464` by hand. The status
member needs no DDL.

**It does not backfill `proposal_id`, and that is deliberate.** The first draft
did, with a generated UUID for every proposal already pending, and review
caught what that did: it stranded them. The generated id existed only on the
market row, `GET /markets/{id}` shows that row to the creator alone, and the
proposal's `market.outcome_proposed` entry — the only place a reviewer can find
it — was written before the key existed. So the one id the service would accept
was one nobody allowed to decide could read. Leaving those proposals null, and
accepting null as the value to quote, makes the id every reviewer can see the
correct one. Deriving an id the reviewer could see instead — the audit entry's
own `id` — was rejected because it would have the migration read the audit
schema to write the market's, a coupling between two schemas this repository
does not otherwise have, and would leave two identity schemes for one field. `ix_markets_due_close` is unaffected: its predicate is `status = 'open'`,
and APPROVED is reached only from PENDING_RESOLUTION.

**The review screen has to quote the id from what it rendered.** The frontend
reads `proposal_id` from the same `GET /markets/{id}` response it shows the
reviewer and sends that back. Fetching it again at the moment of the click
would pass the check against a proposal nobody on that screen has read, and
defeat it.

**`market.outcome_proposed` gains a `context` key.** [3.1] #9's entry now leads
with `proposal_id`. The audit service treats `context` as opaque, so nothing
reading the log breaks; entries written before this change simply lack the key.

**The discovery path is still narrow, as ADR 0014 accepted for early close.**
`GET /markets/{id}` remains the creator's alone, so another administrator can
approve a market they cannot read through this service. Until [BE][X] #62 or
[2.1] #5 provides a read, pending proposals are findable through the audit
service — `GET /audit/actions?action_type=market.outcome_proposed` names the
market id and the proposer id, which is everything the approve control needs.
Widening the read is a decision for the ticket that owns the read.

**A market's existence leaks to another administrator as a 409.** Approving or
rejecting a draft that belongs to someone else is `market_not_pending_resolution`
rather than `market_not_found`. `close_early` already makes this trade and ADR
0014 recorded it: the 409 says a market with that id exists and is not awaiting
a decision, and says nothing about what it contains. The ids are random UUIDs,
and the audience is administrators.

**APPROVED is frozen in every direction this service offers.** Save, publish,
propose, close early, approve and reject all refuse it with
`market_already_approved`. The next write to an approved market is [3.4] #12's
settlement, and [3.3] #11 decides what, if anything, a dispute can do to it.

**The creator cannot approve their own market in a one-administrator
deployment, and that is the rule working.** ADR 0007 already said to run the
demonstration with two administrators, and the role-change endpoint makes that
cheap.

**ADR 0007 said something this code does not do**, and has a dated correction
under the paragraph: it claimed `proposer_id != creator_id` was enforced by
[3.1] #9, when [3.1] #9 in fact made the proposer *be* the creator. The
two-person rule this project enforces on a payout is the one in this record —
proposer against approver — and that is sufficient on its own: no single
administrator can both name a winner and make it final.

## Alternatives rejected

**Staying in PENDING_RESOLUTION with `approved_at` set.** No new status and no
new error. Rejected above: every gate and both downstream tickets would have to
disambiguate on a column, and the pending-resolution error would lie about half
the markets it fired on.

**Calling it RESOLVED.** Rejected because nothing is resolved while a dispute
window is open, and because [3.4] #12 already names the terminal state SETTLED.
A market reading "resolved" that a trader can still dispute and nobody has paid
out on would name a state it is not in.

**An optional note on approval.** Free text only `audit_svc` could read, with
no route that would ever display it, and it would blur `reason IS NOT NULL` as
the predicate for "every action somebody had to explain". Adding one later is
an additive, non-breaking body field.

**Deciding by market id alone, and trusting the row lock.** The first cut of
this ticket. Rejected above: the lock serialises overlapping decisions and
cannot see a reject-and-repropose cycle that has already committed, so a stale
page approves or clears a proposal its reviewer never read.

**Quoting `winning_outcome_id` instead of an id.** Catches a replacement that
names a different winner and misses one that names the same winner on
different evidence — which is exactly what a creator does after a rejection
for weak evidence. The reviewer agreed to the evidence as well as the winner.

**Backfilling `proposal_id` for proposals already pending.** Rejected above:
the id would be readable only by the creator, who may not decide, and a
proposal nobody else could quote could not be decided at all.

**Quoting `proposed_at`.** Distinct for every proposal in practice, and needs
no column. Rejected because it asks the frontend to echo a timestamp exactly,
through two serialisations, as an identity — a value whose job is to say when,
used to say which. An id says what it is for and cannot collide.

**A version counter on the market.** Also correct, and it would never need
clearing. Rejected as less direct: the thing being named is a proposal, and a
counter shared with every other future write to the market would make a
decision fail for changes that are not to the proposal.

**An `If-Match` header with an ETag.** The HTTP-native form of the same
precondition. Rejected because nothing else in this service uses headers for
preconditions, and a body field is visible in OpenAPI, validated by the same
schema as the rest of the request, and refused in the envelope the frontend
already renders.

**`rejected_by_*` columns on the market.** Rejected above: after the clear
there is no proposal on the row for them to describe, and the audit entry
already names the rejecter beside the full proposal.

**Refusing the proposer with 409 or 404.** A 409 says "the state is wrong,
reload", which it is not; a 404 hides a market from somebody who already
proposed on it. The 403 says the thing that is true.

**Letting the proposer reject, as a withdrawal.** Rejected above and in ADR
0013: a path back to CLOSED with one administrator in it.

**Comparing against `creator_id`.** Equivalent today, and wrong the day
proposing is widened beyond the creator. The rule is about who proposed.

**Widening `GET /markets/{id}` so the approver can read what they approve.**
The natural next step and the right one eventually. Rejected for this ticket
because it removes [1.1] #1's draft invisibility for every market, not only
pending ones, unless the read learns to scope by status — which is the design
work [BE][X] #62 and [2.1] #5 exist to do.
