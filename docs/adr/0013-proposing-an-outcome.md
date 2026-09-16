# ADR 0013: Proposing an outcome, from CLOSED only, with evidence, and one at a time

- **Status:** Accepted
- **Date:** 2026-09-16
- **Affects:** [3.1] #9, [3.2] #10, [3.3] #11, [3.4] #12, [2.1] #5, [FE][3.1] #52
- **Implemented in:** `backend/market_service/service/market_service.py` (`propose_outcome`), `service/validation.py`, `controller/routes.py`

## Context

[3.1] #9 is the first story in the resolution epic and the first one that
writes a decision onto a market rather than a moment. Its four acceptance
criteria are short, and each of them hides a choice the issue text does not
make.

**"Selectable only for markets in CLOSED status."** ADR 0011 had just decided
that the status column is a materialisation and the clock is the authority.
Read carelessly, this criterion asks for the opposite.

**"Requires winning outcome plus evidence URL or note."** A disjunction, which
has to be refused somewhere, and this repository has two places that refuse
things and two envelopes to refuse them in.

**"Status moves to PENDING_RESOLUTION."** A fifth status, and the fourth
transition. ADR 0008 built the third and set a pattern; whether this one
follows it is a decision, because unlike a publish this request has to carry a
body.

**"Proposer identity stored on the market."** ADR 0003 says this service cannot
resolve a user id against `auth.users`, now or ever. So "identity" has to be
decided: an id, a name, or both.

## Decision

### `POST /markets/{id}/propose-outcome`, with a body

One endpoint per transition, as ADR 0008 decided, for the reasons it gives —
the legal moves are the route list, and a general status-setting `PATCH` would
have to hold a transition table anyway and would invite a body that carries
other fields.

It does carry a body, and that is the difference from `publish`. Publishing
takes no body because everything it needs was already submitted, and a body
would be a way to change terms and expose them in one call. A proposal is new
information that exists nowhere until an administrator supplies it, so there is
nothing to take from the row. The protection ADR 0008 wanted is kept a
different way: nothing in `OutcomeProposalRequest` names a term. It carries a
winner and evidence, so the market [3.2] #10 asks a second administrator to
approve is the market traders actually traded.

### It gates on `status == CLOSED`, not on the clock

ADR 0011 already named this ticket as one of the three readers that want a
value to gate on, and this is that gate. It is also, deliberately, the one
place in the repository that reads the materialised value where a derived
answer was available, so it is worth saying why that is not a contradiction.

The window is real: for up to `CLOSE_SWEEP_SECONDS` a market whose closing time
has passed still reads `open`, and `propose_outcome` refuses it with
`market_not_closed`. What matters is which way the error points. Gating on the
status is *stricter* than gating on the clock — it can only refuse a proposal
that would have been fine, never accept one that would not — so the failure
mode is a propose control that appears a few seconds late, on a market nobody
can trade in the meantime either.

Deriving it instead would buy back those seconds and cost the property ADR 0011
exists for. The trade path must derive, because there the error points the
other way: a stale answer lets a trade through. Here it does not, and a market
that is `open` in the database is a market this service has not finished
processing.

Nobody will notice the wait. `service/validation.py` requires
`close_time < resolution_time`, so a market's outcome is not even expected to
be known when it closes; an administrator with an answer to propose is never
standing on the closing bell.

### One proposal at a time, and the status is what enforces it

Proposing requires CLOSED and leaves the market PENDING_RESOLUTION, so a second
proposal is refused (`market_pending_resolution`) rather than queued or
overwriting the first. That is what makes it safe to keep the proposal in six
columns on `market.markets` instead of in a proposals table: there is at most
one live proposal per market by construction.

`PENDING_RESOLUTION` also joins `_FROZEN_STATUS_ERRORS`, so every save is
refused while a proposal is outstanding. A market awaiting a second
administrator must not have its terms edited underneath the person being asked
to agree to them.

The row is locked for the check, like `publish`, so an administrator
double-clicking cannot produce two entries in the log proposing different
winners for one market.

### Evidence is a URL, a note, or both — refused in the service layer

At least one, and whichever is given has to be usable: a URL pydantic's
`HttpUrl` can parse, a note of at least ten characters. Both rules live in
`service/validation.py` beside the submission rules, and come back as
`422 proposal_incomplete` with the same `details` array a refused submission
carries.

The shorter version — a `model_validator` and an `HttpUrl` on the request — was
rejected because FastAPI's own 422 has a different envelope from this service's.
The propose form has three inputs and one button, and splitting its refusals
across two shapes would make [FE][3.1] #52 render both to mark one field.

`ProposalIncomplete` is a distinct code from `draft_incomplete` rather than a
reuse of it, because the fields it names — `winning_outcome_id`,
`evidence_url`, `evidence_note`, `evidence` — exist only on this form. Both now
derive from `IncompleteError`, and `controller/errors.py` attaches `details` for
that base rather than for a list of names.

### Identity is the id and the username, snapshotted

Both, for the reason `service/audit.py::Actor` already gives: this service
cannot resolve a user id to a name and never will be able to (ADR 0003), and
only `audit_svc` may read the log, so a username not written here is a username
nobody can ever render. It is also the more truthful record — who this
administrator was when they proposed — which survives a rename, a demotion and
a deleted account.

[3.2] #10 compares the **id**, never the name. "A different administrator" has
to hold against the account, and a username can be reissued after a deletion.

### The audit entry, not the row, is the record

`market.outcome_proposed` carries the winning outcome's id *and* its label, the
evidence URL and the note. The columns on the market hold the same facts today
and will not hold them tomorrow: [3.2] #10's rejection clears them and returns
the market to CLOSED. After that this entry is the only evidence that the
proposal was ever made.

It carries the proposal rather than the terms snapshot the other two market
entries carry. That snapshot exists because [1.4] #4 will let a *submitted*
market be edited by resubmitting it, so an entry about one has to keep its own
copy. Nothing can rewrite a closed market, so pointing at the row is safe here.

The evidence travels in `context`, and `reason` stays null. Evidence is not a
reason: `reason` is the free-text justification [2.3] #7 and [3.2] #10 demand,
and putting a URL in one column and a note in the other would split one
proposal's support in half.

## Why the winner is an outcome id and not a foreign key

`proposed_outcome_id` is a plain `uuid` column. The obvious objection is that
this is a money-adjacent value — [3.4] #12 pays out against it — and money
columns want database-enforced integrity.

The reason is that the constraint worth having is not the one an FK can
express. `market_outcomes.id` is unique across every market, so
`REFERENCES market_outcomes(id)` would accept another market's "Yes" without
complaint. The rule is that the outcome belongs to **this** market, so the
check has to exist in the service layer regardless — where it is also a
field-addressed 422 rather than an `IntegrityError` surfacing as a 500.

Given that check, a plain FK adds nothing and costs a dependency cycle between
`markets` and `market_outcomes` that `create_all` cannot sort without
`use_alter`.

The version that *would* enforce the real rule is a composite foreign key from
`(id, proposed_outcome_id)` to `market_outcomes (market_id, id)`, which needs a
redundant unique constraint on the child to point at. That is the right answer
for a schema under [F-5] #75's Alembic, where a constraint can be added to a
live table without the cycle being a create-order problem. It is not worth a
second index and an `use_alter` today, and this paragraph is here so that the
absence reads as a decision rather than an oversight.

## Consequences

**A fifth status, and no migration for it.** `market.markets.status` is a
non-native `Enum` with `create_constraint` defaulted to False, so
`PENDING_RESOLUTION` is a Python change. The six new columns are not:
`sql/migrations/0005-market-outcome-proposal.sql` adds them by hand, because
`create_all` reaches a fresh database and never an existing one.

**`ix_markets_due_close` is unaffected.** Its predicate is `status = 'open'`
and a market reaches `pending_resolution` only from `closed`, so nothing here
enters or leaves that partial index.

**Proposing stays with the creator.** `propose_outcome` reuses `get`, so another
administrator gets the same 404 they get reading the market — ADR 0008's
precedent, and today there is no route through which another admin could even
find the market ([BE][X] #62 has not landed). [3.2] #10 is the ticket that has
to widen this, because its own first criterion requires a second administrator
to act on a market they did not create. That widening is its decision to
record, not a side effect of this one.

**[2.1] #5 gains a bucket it already expected.** Its acceptance criteria name
"pending resolution" as one of five filters. The status now exists and
`MarketStatus` is the one definition of it.

**There is no un-propose, and there does not need to be.** A proposer who
changes their mind waits for [3.2] #10's rejection, which is the reviewed way
back to CLOSED and leaves a reason in the log. Adding a self-service withdrawal
would be a second path to the same state with no second administrator in it.

## Alternatives rejected

**Deriving the gate from `close_time` like the trade path.** Removes the
sweep-interval wait. Rejected above: the error points the safe way here, and
ADR 0011's whole argument is that deriving is what you do when a stale answer
would let something through.

**A `proposals` table.** Keeps a history of rejected proposals and generalises
to a market that is proposed for several times. Rejected because the history is
in the audit log already, which is where history in this repository lives, and
because only one proposal can be live at a time — so the table would be a join
to fetch a row that is one-to-one with the market for its whole life.

**Naming the winner by `position` instead of `id`.** `model/entities.py`
suggests it, and an `int` is friendlier in a URL. Rejected because a position is
only unique within a market: a copied or stale value silently selects *some*
outcome rather than failing, and the column [3.4] #12 settles against should
fail loudly when it is wrong.

**Folding the evidence into one free-text field.** Fewer columns. Rejected
because the two are read differently — a URL is a link the frontend renders and
[3.3] #11's disputer clicks, a note is prose for when the source is a phone
call or a judgement about an ambiguous print — and collapsing them means
sniffing prose for a link.

**Letting any administrator propose.** ADR 0007 makes the admin tier flat, and
resolution is a platform duty rather than an authorship one. Rejected for this
ticket only: it would also have to widen `GET /markets/{id}`, which is [BE][X]
#62's territory, and [3.2] #10 is where the two-administrator rule actually
forces the question.
