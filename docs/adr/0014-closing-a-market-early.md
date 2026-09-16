# ADR 0014: Closing a market early, by any administrator, with the reason in the log

- **Status:** Accepted
- **Date:** 2026-09-16
- **Affects:** [2.3] #7, [FE][2.3] #56, [F-4] #44, [3.1] #9, [2.1] #5, [T-2] #22, [4.3] #15
- **Implemented in:** `backend/market_service/service/market_service.py` (`close_early`, `get_any`), `service/validation.py`, `model/schemas.py`, `controller/routes.py`

## Context

[2.3] #7 is one sentence — an administrator stops a broken or ambiguous market
early, and says why — and every clause of it lands on a decision this
repository has already taken for a different reason.

**It writes CLOSED by hand.** ADR 0011 had just finished arguing that the clock
closes a market and the status column is only a materialisation of that fact.
A request that writes the column directly is either a hole in that argument or
a case it does not cover, and which one it is has to be settled before the
endpoint exists.

**It requires a free-text reason.** This repository has two ways to refuse a
request and two envelopes to refuse it in, and one column in the audit log that
nothing has ever written.

**It is the first admin action against something somebody else made.** Every
route in the market service is scoped to the creator and answers 404 to anybody
else. ADR 0008 deferred widening that to "a ticket that asks for it", and ADR
0013 deferred it again. This is the ticket that asks.

**"Positions retained"** is an acceptance criterion about a table this service
does not own and cannot see.

## Decision

### `POST /markets/{id}/close`, with a body carrying one required `reason`

One endpoint per transition, as ADR 0008 decided and ADR 0013 followed. It
carries a body for the reason ADR 0013 gives for `propose-outcome`: the
justification is new information that exists nowhere until an administrator
types it, so there is nothing to take from the row. And as there, the body
cannot touch the market's terms — `MarketCloseRequest` has one field, and it is
not one of them.

`reason` is required by the request model, so a body without the key at all is
FastAPI's own 422. What the field *says* — non-blank, at least ten characters
after stripping — is a content rule, so it lives in `service/validation.py`
with the others and comes back as `422 close_incomplete` with the
`details` array every other refusal in this service uses. `CloseIncomplete` is
the third subclass of `IncompleteError`, which is what that base class was
written for: `controller/errors.py` attaches `details` for the base, so the
envelope cost a code and a sentence.

Ten characters is `MIN_EVIDENCE_NOTE_LENGTH`'s argument again — "broken"
satisfies "a reason was given" and documents nothing — and it is a separate
constant for the reason `MIN_QUESTION_LENGTH` and `MIN_CRITERIA_LENGTH` are
separate from each other: different rules that agree on a number today.

### The gate derives from the clock, and this is not a reversal of ADR 0013

`close_early` refuses anything `service/closing.py::is_open_for_trading` calls
False — the same predicate the trade path uses, against the row it has just
locked. So a market whose `close_time` has passed cannot be closed early, even
in the few seconds before the sweep writes `CLOSED` and the status still reads
`open`.

ADR 0013 gates on the status column instead, and both are the same decision:
pick the direction whose failure is a control appearing or disappearing a beat
late, rather than a wrong write.

- Proposing an outcome: deriving could accept a proposal on a market this
  service has not finished processing. Reading the status can only refuse one
  that would have been fine. So it reads the status.
- Closing early: reading the status would accept a close in the window after
  `close_time`, and append an entry saying an administrator stopped trading
  that the clock had already stopped — at a `closed_at` with no relation to
  when trading actually ended. Deriving can only refuse a close that the clock
  is about to perform anyway. So it derives.

The administrator gets `market_closed` either side of the sweep, which is the
property one predicate buys over two comparisons.

### `close_time` is not rewritten

Only `status` and `closed_at` move. `close_time` is a term the administrator
published and traders read, and the `market.published` audit entry recorded it;
rewriting it would make the row disagree with the log about what was approved.

This gives a derived fact worth having: a market whose `closed_at` precedes its
`close_time` is exactly a market that was stopped by hand.

### Any administrator may close any market — the one unscoped read here

`close_early` calls `get_any` rather than `get`, so the market does not have to
be the caller's.

ADR 0007 made the admin tier flat, and this story is in the oversight epic: a
broken market that can only be stopped by the administrator who created it —
who may be asleep, or may be the reason it is broken — is not oversight.

The 404-rather-than-403 rule everywhere else in this service protects *drafts*,
which is [1.1] #1's requirement. This route only ever acts on a market that is
open to traders, so there is nothing left for that rule to hide: the
confidentiality argument does not apply, and what is left is purely a question
of who may act. The answer is every administrator, and the audit entry — which
names whoever reached into somebody else's market, and why — is what makes that
accountable rather than anonymous.

`get_any` is deliberately a second function rather than a `creator_id=None`
default on `get`. An unscoped read is a decision, and it should be visible at
the call site and greppable from the definition. Both delegate to one private
`_load` whose `creator_id` is keyword-only with no default, so neither can get
the wider scope by omission.

### The reason lives in the audit log and nowhere else

`market.closed_early` carries it in the entry's `reason` column — the first
writer of that column, and the shape [3.2] #10's rejection and [4.2] #14's
suspension will reuse, so that one predicate finds every action somebody had to
explain. The entry also carries the `close_time` it cut short in `context`,
because `occurred_at` says when the market stopped and only that value says
what it stopped short of, and `audit_svc` holds no grant on `market.markets` to
look it up with.

No column on the market holds the reason. The acceptance criterion asks for the
audit log, one fact in one place cannot drift from itself, and adding a column
would mean a hand-applied migration against the long-lived database for a value
this service would still have no route to display. The cost is stated plainly:
only `audit_svc` can read it, so the reason surfaces through [4.3] #15's log
view and never in a market response. If a later ticket needs to show a trader
why their market stopped, that ticket adds the column and this paragraph is
what it argues against.

The entry is written before the commit, like every other action in this
service. It matters more here than anywhere else, because this entry is the
only copy of the reason: a market that stopped early with no entry is a market
nobody can ever explain.

### "Positions retained" is true by construction

Positions are the ledger's (ADR 0005) and this service holds none. An early
close writes two columns on one row and nothing in this repository deletes a
holding. The criterion is satisfied by the boundary rather than by code, which
is the answer, and the reason it is written down here is so that nobody goes
looking for the code that implements it.

## Consequences

**No migration, and no new column.** `CLOSED` already exists and `closed_at`
already exists — [F-4] #44 added both, and `model/entities.py` already said
this ticket would land in them. `sql/migrations/` is untouched, which is the
first time a market-service story has been.

**A market stopped by hand is indistinguishable downstream, on purpose.**
[3.1] #9 proposes an outcome on it, [2.1] #5 counts it in the same bucket, the
sweeper never sees it again — `ix_markets_due_close` is partial on
`status = 'open'` — and `is_open_for_trading` refuses a trade the instant the
close commits. Only the audit log knows how a market reached CLOSED, which is
where that difference belongs.

**An administrator can close a market they cannot read.** The authority
widened; the discovery path did not. `GET /markets/{id}` is still the creator's
alone, so until [BE][X] #62's public browse or [2.1] #5's dashboard lands, an
overseer has to have the market's id from somewhere other than this service.
That is a gap in the UI story, not in this one, and widening the read is a
decision for the ticket that needs it — for the reason ADR 0013 gives.

**The close modal shares an error renderer with the create form.**
`close_incomplete` carries the same `details` array as `draft_incomplete` and
`proposal_incomplete`, so [FE][2.3] #56 paints it with the code it already has.

**Two administrators cannot both close one market.** The row is locked for the
status check and the write, as in `publish` and `propose_outcome`, so a
double-clicked confirm button cannot produce two entries closing one market
with two different reasons.

## Alternatives rejected

**Gating on `status == OPEN` like [3.1] #9 does.** Consistent-looking, and one
fewer comparison. Rejected above: in this direction a stale answer writes a
false record instead of delaying a control.

**Storing the reason on the market.** A `closed_reason` column would let the
market service show why a market stopped, which would be genuinely useful to
whoever proposes its outcome later. Rejected for this ticket: the criterion
names the audit log, a second copy is a second thing to keep true, and it costs
a hand-applied `ALTER TABLE` against `cs464` for a value no route currently
renders. The argument for adding it is above, written out, for the ticket that
wants it.

**Moving `close_time` to the moment of the close.** Makes `open_for_trading`'s
clock half agree with the status half, and makes the market self-describing
without `closed_at`. Rejected because it rewrites a published term and
contradicts the audit entry that recorded it, and because it would destroy the
one signal that says a market was stopped by hand.

**Keeping it to the creator, like every other route.** The narrowest change,
and consistent. Rejected because it makes the story's own justification —
"broken or ambiguous markets can be stopped" — false whenever the creator is
unavailable, which is one of the cases an oversight control exists for.

**A generic `PATCH /markets/{id}` that sets the status.** Rejected for ADR
0008's reason, which has only got stronger with the fourth transition: the
route list is the list of legal moves, and a general status setter would need a
transition table anyway and would invite a body carrying other fields.

**Logging the close as `market.closed` rather than `market.closed_early`.**
Rejected because the automatic close writes no entry at all — the clock is not
an actor — so every entry in this log is by definition an early one, and naming
it plainly is what stops a later reader assuming the absence of an entry means
a market never closed.
