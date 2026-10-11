# ADR 0023: Sending an approved outcome back, and the window's end stored at approval

- **Status:** Accepted
- **Date:** 2026-10-11
- **Affects:** [3.3] #11, [FE][3.3] #59, [3.4] #12, [FE][3.4] #216, [BE] #221, ADR 0006, ADR 0007, ADR 0011, ADR 0013, ADR 0015, ADR 0016, ADR 0019, ADR 0020
- **Implemented in:** nothing yet. This record precedes #11's code PRs, which will touch `backend/market_service/service/market_service.py` (`send_back_outcome`, `_refuse_if_superseded`, `_clear_proposal`, `approve_outcome`), `service/settling.py`, `core/settling.py`, `service/market_audit.py`, `service/validation.py`, `core/errors.py`, `model/entities.py`, `model/schemas.py`, `controller/routes.py` and `migrations/versions/0002_dispute_window_ends_at.py`

## Context

[3.3] #11 gives every approval a dispute window. #227 and #237 already built
half of it: the window's length (`DISPUTE_WINDOW_SECONDS`, 24 hours by
default), the `settleable` flag, and a settle step that waits for the window
to end plus five minutes (ADR 0019). Three things are still owed:

- **Send-back.** While the window is open, any administrator can send the
  approved result back. The market returns to CLOSED, the result is redone
  through propose and approve, and that new approval starts a fresh window.
- **The edge.** After the window, send-back is refused. The check reads
  `clock_timestamp()` after the row lock, and allows only `now` before the
  window's end.
- **The countdown.** Admins and traders can see how long is left.

Two earlier records left choices open for this ticket:

- ADR 0016 made APPROVED "frozen in every direction this service offers" and
  said "[3.3] #11 decides what, if anything, a dispute can do to it". It also
  had no deadline column, and called snapshotting the window's end "an
  additive decision for #11".
- ADR 0019 had market_service decide both edges "from `approved_at`": the end
  was worked out on every read as `approved_at` plus the setting as it is now.

**Naming.** The ticket and ADR 0019 say `window_ends_at`. In code, in the API
and in the audit log it is `dispute_window_ends_at` everywhere: the column,
the field on the read models and the key in the audit entry. One name, so
nobody has to map one to the other.

## Decision

### One endpoint, naming the approval and giving a reason

`POST /markets/{id}/send-back-outcome`, with an administrator's token. One
endpoint per move, as ADR 0008, 0013 and 0016 did: the route list is the list
of legal moves.

```json
{
  "proposal_id": "6f1c9a2e-...",
  "reason": "The source cited was revised; the first print says No, not Yes."
}
```

- **`proposal_id`** is a required key with a nullable value, exactly as on
  approve and reject. It is the id of the approved proposal the administrator
  is looking at, read from the latest `market.outcome_approved` audit entry
  for the market. `null` is only for an approval whose proposal had no id. A
  body without the key is FastAPI's `422`.
- **`reason`** is required, at most 5000 characters. A missing key is
  FastAPI's `422`. Its content (at least 10 characters after trimming) is a
  service rule, `422 send_back_incomplete` with `details`. That is the same
  split `reject-outcome` makes. `MIN_SEND_BACK_REASON_LENGTH` is its own
  constant, for the reason the other reason constants give each other.

The response is `200` with the whole market (`MarketOut`), as reject returns
it: `status: "closed"`, every proposal and approval field null, `closed_at`
unchanged.

### The checks, in order

1. `401 invalid_token`, then `403 not_an_administrator`, in the controller.
2. **Lock.** `get_any` with the row locked. An unknown id is
   `404 market_not_found`. It is unscoped, because the administrator sending
   back is usually not the creator. That is safe because step 3 refuses a
   draft before anything is touched (ADR 0014, 0016).
3. **State**, on the locked row:
   - SETTLED: `409 market_already_settled`.
   - PENDING_RESOLUTION: `409 market_pending_resolution`. It keeps its own
     code because the remedy differs: review the proposal and reject it.
   - anything else that is not APPROVED: `409 market_not_approved`.
4. **Clock**, on the APPROVED branch only: `clock_timestamp()`, read after the
   lock.
5. **Window.** Allowed only while `now < dispute_window_ends_at`. At or after
   the end it is `409 dispute_window_closed`. An APPROVED market with a null
   `dispute_window_ends_at` is refused the same way, as `settleable` refuses
   it: a null is damage, and the safe answer is to do nothing.
6. **Identity.** None. Any administrator may send back (below).
7. **Which approval.** A `proposal_id` that is not the row's is
   `409 proposal_superseded`, through `_refuse_if_superseded`, the helper
   approve and reject already share.
8. **Content.** The reason, `422 send_back_incomplete`.

Then the snapshot, the clear, a flush, the audit entry, and one commit.

**Why this order.**

- *State before the clock*, as the settle step does it. A settled or
  already-sent-back market writes nothing, so it needs no clock.
- *Window before the proposal id.* If the window has closed on whatever is
  approved now, the useful answer is "the result is final". A page left open
  from an earlier approval cycle is told that too, and it is true.
- *Proposal id before the reason*, so nobody is asked to improve a reason
  about an approval that no longer exists. This is ADR 0016's order.

**Why the request names the approval.** CLAUDE.md: "A decision names the
proposal, because the lock cannot". The approved result can be replaced on
the same market row. A worked example:

1. Carol opens the dispute screen for proposal P1, whose winner is Yes.
2. While she reads, Bob sends P1 back. The creator proposes P2 (winner No) and
   Dave approves it. All of this commits.
3. Carol clicks. Her request takes the lock and finds an APPROVED market
   inside a fresh window, so every state and window check passes.

Without the id, Carol sends back P2, an approval she never saw, with a reason
written about P1, and the log says P2 was disputed for that reason. With the
id, she gets `409 proposal_superseded` and nothing is written.
`proposal_id` stays on the row while the market is APPROVED, and every new
proposal gets a new one, so it names the approval exactly.

### What a send-back clears, and what it keeps

| Column | After send-back |
| --- | --- |
| `status` | `CLOSED` |
| the seven proposal columns: `proposal_id`, `proposed_outcome_id`, `proposed_by_id`, `proposed_by_username`, `proposed_at`, `proposal_evidence_url`, `proposal_evidence_note` | null, as a rejection clears them |
| `approved_by_id`, `approved_by_username`, `approved_at` | null |
| `dispute_window_ends_at` | null |
| `closed_at` | **untouched**: trading stopped when it stopped |
| terms, outcomes, sources, `published_at` | untouched |

Eleven columns cleared in all. The seven-column clear is the one reject
already does, extracted as `_clear_proposal` now that it has a second caller.

The approval columns go too because ADR 0016 and `MarketOut` say they are
null unless the market is APPROVED. Leaving them would show a CLOSED market
with an approver, and the next approval would overwrite them anyway.

The market is then exactly what a rejected market is: CLOSED, with no
proposal. The creator proposes again through the existing route (ADR 0013,
creator only). A different administrator approves through the existing route
(ADR 0016). That approval stamps a new `approved_at` and a new
`dispute_window_ends_at`, so the window starts again, with the setting as it
is then.

Positions, `q` and the ledger are not touched. Nothing has been paid, because
`settleable` turns true only five minutes after the window ends.

### The audit entry is the record

`market.outcome_sent_back` is written under the administrator who sent it
back, on the request's own session, before the commit (ADR 0006). It carries:

- `reason`: the trimmed reason. It is the only copy (D3, below).
- `context`: a twelve-key `approval_snapshot`. These are the eight keys approve
  and reject already log (`proposal_id`, `winning_outcome_id`,
  `winning_outcome`, `evidence_url`, `evidence_note`, `proposed_by_id`,
  `proposed_by_username`, `proposed_at`), plus `approved_by_id`,
  `approved_by_username`, `approved_at` and `dispute_window_ends_at`.
- `occurred_at`: the `clock_timestamp()` read in step 4.

**The snapshot is taken before the clear**, after every check has passed and
before the first column is nulled, in the same place reject takes its own.
After the clear the entry is the only copy of what was approved, by whom, and
when its window would have ended, and `audit_svc` cannot read
`market.markets`. Take the snapshot after the clear and the log loses it.

A dispute can then be read from the log alone:
`outcome_proposed (P1)` → `outcome_approved (P1)` → `outcome_sent_back (P1,
reason)` → `outcome_proposed (P2)` → `outcome_approved (P2)` →
`marked_settled`.

There are no `sent_back_by_*` columns, for ADR 0016's reason that there are no
`rejected_by_*` ones: after the clear, there is no approval left on the row
for them to describe.

### The window's end is stored at approval

`market.markets.dispute_window_ends_at`, `timestamptz`, nullable, no default.

- **Written** by `approve_outcome`, in the same transaction and from the same
  clock read as `approved_at`: `approved_at` plus `DISPUTE_WINDOW_SECONDS` as
  the setting is **at that moment**.
- **Cleared** by send-back, with the other approval columns.
- **Kept** by settle, like `approved_at`, and later by [BE] #221's void.

So it is set exactly when `approved_at` is set. `settleable` and send-back both
read the column, and neither reads the setting. The only reader of
`dispute_window_seconds` left is the helper approve calls.

A change to `DISPUTE_WINDOW_SECONDS` now affects only approvals made after it.
A running countdown never jumps, and a market already paid can never look
"not settleable" again because of a config change. It is the same shape as
`close_time`: a stored instant the clock is compared against (ADR 0011).

**No CHECK constraint.** The three approval columns have none. The tests that
fake damage need to be able to write a null. And a CHECK naming `'approved'`
would be the only database rule that reads a status string, when `status` is
deliberately a plain varchar with no CHECK. The code fails closed on a null
instead.

**No index.** Nothing scans for approved markets by window end. ADR 0016 says
a partial index on the approved working set is added beside the query that
needs it, and no such query exists.

**The migration is market revision `0002`**, `down_revision = "0001"`. It adds
the column, and a hand-written `UPDATE` backfills every row that has an
`approved_at` with `approved_at + interval '86400 seconds'`. Autogenerate
writes the column and cannot see the `UPDATE`, since data is not schema. On
real data the backfill reaches exactly the APPROVED and SETTLED markets:
nothing else ever keeps an `approved_at`. Only the APPROVED rows' value is
ever read. The SETTLED rows get one so that "set exactly when `approved_at`
is set" holds on every row from the start.

**Why 86400 is written into the revision.** The migrate step reads
`DATABASE_URL` and nothing else (DECISIONS.md, "The migrate step reads
DATABASE_URL alone, not the service's settings"), so the revision cannot see
`DISPUTE_WINDOW_SECONDS`. 86400 is the default, and the only databases holding
approved markets are the team's local `cs464` ones: nothing is deployed, and
`cs464_test` is rebuilt per test. A developer running a short demo window
will see their already-approved local markets' windows become 24 hours, once.
That can reopen a window that had closed: a market the ledger paid but whose
step 5 failed still reads approved, and becomes sendable-back and not
settleable again. Fix it by hand straight after the migrate step, with
`UPDATE market.markets SET dispute_window_ends_at = approved_at + interval
'<your window> seconds' WHERE approved_at IS NOT NULL`, or start again with
`docker compose down -v`, which destroys local data. Reading the variable in the revision
would make its result depend on where it runs, and would reverse a recorded
decision to save a local demo market.

This is the first revision to do data work. ADR 0020 says the suites switch to
migrating per test "when a revision does data work the models cannot
express". That trigger is not tripped here. ADR 0020 carries a dated note
saying why.

### The countdown is the stored column, on two reads

`dispute_window_ends_at` is added to:

- the public detail, `GET /public/markets/{id}`: the trader's market page, and
  any administrator viewing it;
- every overview row, `GET /markets/overview`: the admin markets page, where
  the settle control and the dispute control live.

It is the column, passed through as it is. Nothing derives or stamps it. It
is null on a market never approved and on one sent back. It holds the instant
on an APPROVED market, and keeps it on a SETTLED one, where it records when
disputes closed. It is a required key with a nullable value, so a reader
cannot mistake a missing field for "no window".

It is not added to `MarketOut` or to the trader browse cards. Nothing on those
screens shows a countdown, so it would be a field with no reader.

The browser shows the countdown only for an `approved` market, as time left
**and** the absolute time, because a browser's clock can be wrong. It never
decides anything from it. The server decides send-back on the database's
clock, and the settle control reads `settleable`.

### The edges never meet

Send-back is allowed while `now < E`. Settlement is allowed from
`now >= E + 5 minutes`. `E` is the stored `dispute_window_ends_at`. No instant
allows both.

Send-back and settle are the two writers that meet at the edge. Each reads
`clock_timestamp()` after the same row lock, so they agree with each other
whatever a reader's clock says, and the five-minute gap keeps them apart.
Never `func.now()`, which is the transaction's start, read before the lock
wait (#138). Never Python's clock on a write.

**Move either and the gap stops protecting settlement.** Make send-back's
edge inclusive, read the clock before the lock, or compute the end from the
setting instead of reading the column, and an instant can exist where a
send-back passes its check while a settlement is already allowed.

### Locks and races

One row lock, the market's, and no multi-row lock, so there is no lock-order
question (ADR 0015).

| Pair | Who wins | The loser gets |
| --- | --- | --- |
| send-back, then the settle step | send-back | settle: `409 market_not_approved`. Through the ledger, `503 settlement_unconfirmed`, since it accepts only a `200` from this step. |
| the settle step, then send-back | settle | send-back: `409 market_already_settled` |
| send-back against send-back (a double click, or two admins) | the first | `409 market_not_approved`, and one audit entry |
| send-back against void ([BE] #221, later) | either | void first: `409 market_already_voided`. Send-back first: void accepts CLOSED. |

In real use the first two cannot happen at all, because the gap keeps the
edges apart. The race tests pin each writer's clock so that both pass their
own window check, which leaves the status read under the lock as the only
thing that refuses the loser. Each path's lock is proven by the test in which
that path goes second.

### D1: any administrator may send back, the proposer and the approver included

This is the acceptance criterion as written ("any admin"), and it does not
weaken the two-person rule. That rule protects what gets paid, and every path
after a send-back goes through propose and a *different* administrator's
approval again.

The cost: one administrator can delay a payout by sending a result back again
and again. Each new approval still needs a second administrator, so no single
administrator can change who gets paid, and every delay is in the log under
their name. That is ADR 0007's trust model.

**It does not contradict ADR 0016's refusal of "letting the proposer reject,
as a withdrawal".** A withdrawal would remove a proposal nobody else had
agreed to yet: a path back to CLOSED with one administrator in it. A
send-back undoes a result two people have already agreed, and leads only to
another two-person decision.

### D2: the window's end is stored, not derived on every read

Without the column, raising `DISPUTE_WINDOW_SECONDS` reopens windows that had
closed. A worked example, with the old derivation:

1. A market is approved on Monday at 10:00, with a 24-hour window.
2. On Tuesday at 10:06 the ledger pays the Yes holders, and its call to mark
   the market settled fails. The market still reads `approved`.
3. Someone raises the window to 48 hours. The market is now inside its window
   again.
4. An administrator sends it back, the creator proposes No, and another
   administrator approves No.

market_service now names No as the winner, and the ledger has paid Yes for
good. With the end stored at approval, step 3 changes nothing: the market's
window ended on Tuesday at 10:00, and the send-back in step 4 is refused.

### D3: the reason is in the log only

The reason lives in the audit entry, as a rejection's does (ADR 0016: "the
audit entry is the record"). Traders see the market go back to closed and
awaiting a result, with no winner, since the public detail shows the winner
only on a decided market. [BE] #221 puts a reason on the market for a void,
because a void is final and traders need to understand their refund. A
send-back is temporary and moves no money. A note traders can see can be
added later without undoing anything.

## Consequences

**One new error code, and three wider messages.**

- `409 dispute_window_closed` (new): "This market's dispute window has ended.
  Its approved outcome is final and can no longer be sent back."
- `422 send_back_incomplete` (new): "This approved result cannot be sent back
  without a reason."
- `market_not_approved` now covers send-back as well as settle: "Only a market
  whose outcome has been approved can be settled or sent back. This one has
  not been approved." The code is unchanged. The ledger accepts only a `200`
  from the settle step, so it notices nothing.
- `market_already_approved` stopped being true as worded ("the next step is
  settlement"). Now: "A second administrator has already approved this
  market's outcome. Next it is settled, unless it is sent back during its
  dispute window." It is the answer to every write but settle and send-back.
- `proposal_superseded` said to review "the proposal that is waiting now",
  which is wrong for send-back, where the replacement is approved. Now: "The
  proposal you reviewed has been replaced by a newer one. Reload the market
  and review the current one."

**A wording trap.** Reject's own copy already says "why this proposal was
sent back". It is left alone. Send-back's copy says "approved result" every
time, so the two never read as the same action. The frontend labels the
control "Dispute result", never just "Send back".

**ADR 0016 and ADR 0019 carry dated amendments.** APPROVED now has two exits
in market_service, not one: SETTLED and, inside the window, CLOSED. And
`settleable` reads the stored end instead of `approved_at`. ADR 0019's
"APPROVED is read before the lock" is unchanged: send-back still ends at the
window's end, so its reversal trigger is not tripped. The ledger's code does
not change. It reads only `settleable`, and ignores fields it does not know on
the public detail.

**The dispute screen quotes what it rendered.** The frontend ([FE][3.3] #59)
reads `proposal_id` from the latest `market.outcome_approved` entry it showed
the administrator, and sends that, `null` included. A fresh fetch at the click
would pass the check against an approval nobody on that screen has read.

**Re-proposing stays with the creator** (ADR 0013, unchanged). If the creator
is gone, or never proposes again, the market waits, as it does after a
rejection today. #221's void is the way out.

**[BE] #221 builds on the column.** #221 makes APPROVED voidable "while the
dispute window is open", and lets its void step accept APPROVED until the
window's end plus five minutes for the ledger's retry. After this record,
that end **is** `dispute_window_ends_at`: `voidable` asks the same "is it
before the end" question send-back asks, on the column, and the void step
compares the column plus the gap. After a send-back the market is CLOSED,
which #221 already lists as voidable. #221's void keeps the approval columns,
this one included. Whichever of the two market revisions merges second is
renumbered and its `down_revision` pointed at the other.

## Alternatives rejected

**Widening `reject-outcome` to accept an APPROVED market.** One endpoint
fewer. Rejected because the route list is the list of legal moves, and the
two have different rules: reject refuses the proposer and has no window;
send-back refuses nobody and has one.

**Deciding by market id alone.** Rejected above: the worked example sends back
an approval its sender never saw.

**Quoting `approved_at` instead of `proposal_id`.** Rejected for the reason
ADR 0016 rejected `proposed_at`: a timestamp passed through two
serialisations and used as an identity.

**Refusing the proposer, or the approver.** Rejected under D1: the two-person
rule protects what gets paid, and is applied again on the way back.

**A "has traded" check, like #221's stake check.** market_service cannot see
trades (ADR 0003). Asking the ledger would be the market → ledger call ADR
0019 refused as a cycle. Approve has no stake check either, and send-back
moves no money.

**Keeping the approval columns after a send-back.** A CLOSED market would show
an approver, against ADR 0016's rule that they are null unless APPROVED.

**Deriving the end on every read, from `approved_at` plus the setting.** What
#227 built. Rejected under D2: a raised setting reopens closed windows,
including on a market the ledger has already paid.

**Reading `DISPUTE_WINDOW_SECONDS` in the backfill.** The revision's result
would depend on where it runs, and the migrate step reads `DATABASE_URL`
alone.

**A CHECK constraint, or an index, on the new column.** Rejected above.

**A reason, or `sent_back_by_*` columns, on the market.** Rejected under D3:
the log is the record, and after the clear there is nothing on the row for
them to describe.

**`dispute_window_ends_at` on `MarketOut` and the trader browse cards.** No
screen there shows a countdown.

**Switching the suites to migrating per test for the backfill.** The cost ADR
0020 refused ("every test slower forever"), for one `UPDATE` that one targeted
test already covers. See ADR 0020's dated note.

> **Reversal trigger:** a payout delayed by repeated send-backs becomes a real
> problem; a limit on send-backs per market, or a rule on who may send back,
> is then a new decision. Or a trader-visible reason is asked for, which is
> additive: a new column and a field.
