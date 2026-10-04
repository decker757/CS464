# ADR 0019: Settlement is the ledger's request, paid in one transaction, and market_service is told afterwards

- **Status:** Accepted
- **Date:** 2026-10-04
- **Affects:** [3.4] #12, [FE][3.4] #216, [3.3] #11, [4.4] #16, [T-4] #24, [T-5] #25, [2.1] #5, [L-1] #38, [L-3] #40, ADR 0006, ADR 0007, ADR 0009, ADR 0011, ADR 0015, ADR 0016, ADR 0017, ADR 0018
- **Implemented in:** nothing yet. This record precedes [3.4] #12, which is the first code that has to obey it.

## Context

[3.4] #12 pays out an approved market. Settlement is the only operation in the
system that needs facts from both of the services that hold state:

- **market_service knows the result.** Only it holds the status and the
  approved outcome (ADR 0016). The ledger has no grant on `market.markets`.
- **The ledger holds the money.** Only it can read the pool, the positions and
  the accounts, and only it can write entries (ADR 0009). market_service has no
  grant on `ledger.*`.

So no single transaction can both pay the winners and mark the market SETTLED.
Some request has to cross from one service to the other, and the order in which
the two writes happen decides what a failure between them leaves behind.

Five constraints were already fixed before this record:

- ADR 0009: a write route must take no money from its caller.
- ADR 0006: the audit entry commits with the action it records.
- ADR 0015: a read that decides a write is locked, and the wider lock goes first.
- ADR 0017: no outbound call is made while holding a connection or a row lock.
- "The book's writes share `posting.post`'s commit, and nothing may follow it":
  `posting.post` commits, so it must be the last call on a path that writes.

## Decision

### The request goes to the ledger, and market_service is told after the money moves

An administrator calls `POST /ledger/markets/{id}/settlement` with an empty
body, authenticated as `CurrentAdmin`. The ledger then:

1. Reads `status` and `proposed_outcome_id` from
   `GET /public/markets/{id}`, forwarding the administrator's token. It
   accepts `approved` or `settled` and refuses anything else with
   `409 market_not_approved`.
2. Opens the book if it is cold (`books.ensure_open`, its own earlier commit).
3. Takes the book row lock and looks for a settlement record. If one exists,
   nothing is written, and the recorded outcome governs: what market_service
   reports now is not read for the payout.
4. Otherwise it pays every winner, posts the residue, writes the settlement
   record and the audit entry, and commits all of them once.
5. Calls `POST /markets/{id}/settle` on market_service with the
   administrator's token. That route moves APPROVED to SETTLED, answers `200`
   without writing on a market already SETTLED, and refuses anything else
   with `409 market_not_approved`.

`SETTLED → SETTLED` is `200` with no write on both services. That makes the
whole request safe to repeat, and a repeat is how every partial failure is
repaired:

| What failed | What is left | What a repeat does |
| --- | --- | --- |
| step 1, or anything before the commit | nothing | settles normally |
| step 5 | payouts committed, market still APPROVED | finds the record, writes nothing, retries step 5 |

When step 5 fails, the response is **`503 settlement_unconfirmed`**. It is
deliberately not `market_terms_unavailable`. Every other use of that code means
nothing happened, and here the money has moved. The status and the remedy are
the same, but an administrator, and the frontend ([FE][3.4] #216), must be able
to tell the two apart.

**Why the ledger goes first.** If the market were marked SETTLED first and the
payment then failed, the market would look finished while nobody had been paid.
The overview would hide it, and nothing would prompt a retry. With the ledger
first, the worst partial state is "paid, still shown as approved". The market
then stays in the overview's approved queue, which is exactly the prompt to
repeat the request.

**The direct-call gap is accepted, under ADR 0007's trust model.** Step 5's
route is reachable by any administrator's token, so an administrator who calls
it directly marks a market SETTLED and pays nobody. Such a market sinks out of
the overview's approved queue and looks finished. Two things limit the harm:

- The ledger still accepts `settled` with no record, so calling the ledger's
  route on such a market pays it.
- The frontend calls only the ledger's route, and `docs/api/market-service.md`
  documents step 5 as the ledger's alone.

ADR 0007 already trusts every administrator not to act against the platform,
and this is the same trust.

*Rejected:* market_service checking with the ledger before it writes SETTLED.
That needs a market → ledger read, and the ledger → market read in step 1
already exists, so the two services would call each other in a cycle. Each
would then be unable to start, test or fail without the other.

> **Reversal trigger:** a service credential exists, meaning a token that
> proves a request comes from the ledger rather than from a person. Step 5 then
> requires it, and the gap closes.

### Who may settle: any administrator

Any administrator may settle, including the market's creator, proposer or
approver. The two-person rule is spent at approval (ADR 0016). Settlement
executes a decision two people have already made, and it chooses nothing.

[4.4] #16's split between creator and resolver roles is not built: ADR 0007
declined it and #16 is closed.

> **Reversal trigger:** a resolver role is added to `UserRole`. Settlement
> then moves to that role, in both services' guards. Or [3.3] #11 lands: its
> "window expired" becomes a second condition on who may settle and when. That
> condition is market_service's to state and the ledger's to read in step 1,
> not to recompute.

### Neither outbound call holds a database connection

Both calls to market_service are made outside any database transaction:

- **The read in step 1** runs before the request's first database statement.
  On a path that has already read, it runs after a rollback.
- **The call in step 5** follows the commit, and no statement runs between
  the commit and the call, so nothing autobegins a transaction.

This is the same rule as "The cold path holds no connection across the terms
pull", for the reason ADR 0017 gives: a slow or hung market_service must cost
this request its own latency, not a connection from the pool or a lock on the
book.

> **Reversal trigger:** a step that has to touch the database between the
> commit and step 5, or before step 1. The connection-release tests fail on
> that change, and the step must move rather than the test.

### APPROVED is read before the lock, and that is safe because APPROVED's only exit is SETTLED

The status is read in step 1, before the book lock. ADR 0015's rule doesn't
reach it, for the reason ADR 0017 gives: the ledger's lock holds nothing still
in market_service's database.

The read is also *stable*. In market_service, every write path refuses an
APPROVED market:

- save and publish: `_refuse_if_frozen`
- close early and propose: `_refuse_if_resolving`
- approve and reject: `_proposal_to_decide`

The sweep touches only OPEN markets. So an `approved` answer cannot become false
before the payout, except by becoming `settled`, which the ledger also accepts.

> **Reversal trigger:** any new transition out of APPROVED. [3.3] #11's
> dispute window is the likeliest: if a dispute can return an approved market
> to PENDING_RESOLUTION or CLOSED, then reading the status before the lock is
> no longer safe.

### The result is a record, and positions and `q` are never written

`ledger.market_settlements` holds one row per settled market: `market_id` as
the primary key, `outcome_id` (the winner), with `(market_id, outcome_id)` a
composite foreign key into `market_outcomes`, and `settled_at`. Positions are
not zeroed and `q` does not move.

The record is the latch. Both the settlement path and the trade path read it
**under the book lock**:

- If a settlement finds a record, it writes nothing.
- If a trade finds a record, it is refused with `409 market_closed`.

The trade path's status gate already refuses a SETTLED market. The latch is
needed for what the gate cannot see: a trade that passed the gate and then
queued on the book lock behind the settlement.

Leaving positions in place keeps three invariants true without touching them:

- "Shares outstanding equal the sum of positions, so the outstanding check is
  a backstop on the trade route" still holds.
- [T-3] #23's rule that every writer of positions holds the book lock first
  gains no new writer.
- The trade history keeps what each user held.

The portfolio knows the market is settled by joining the record, which is
data this service owns, so it still makes no call to market_service.

*Rejected:*
- *Zeroing the positions.* That destroys the only record of what was held and
  paid, and the portfolio's realized outcome would have nothing to show.
- *Writing SETTLED onto the book.* That adds a status to a table ADR 0017 keeps
  status-free on purpose, and makes it a second copy of market_service's
  column.
- *Storing what it paid on the record.* A total paid or a residue there is a
  stored sum of ledger rows: a second source of truth beside the entries,
  which ADR 0009 exists to refuse. Nothing reads it, since the audit entry and
  the entries themselves already say what was paid.

> **Reversal trigger:** a reader that needs a settled market's shares to read
> as zero and cannot join the record, or any path that would reopen a settled
> market.

### One transaction, through `posting.post_all`

Each winner's payout is its own `Transaction` of kind `SETTLEMENT`, keyed
`settlement:{market_id}:{user_id}`, so each has one leg on that user's account,
as "A transaction puts at most one leg on any USER account" requires. The
residue is one more transaction, of kind `SETTLEMENT_RESIDUE`, keyed
`settlement_residue:{market_id}`. Both keys are derived by the server. All of them, plus the
settlement record and the audit entry, are posted through `posting.post_all`
and committed together.

`posting.post_all` is a batch form of `post`. It locks every account once, in
id order, after the book lock. It runs `post`'s checks on each transaction and
commits once. It is a new function in decker757's file, and its exact shape
waits on his agreement.

**The batch exists for atomicity and the lock, not for speed.**

- *Atomicity:* a settlement that paid half its winners and then failed would
  leave a state that no reader can label and no retry can finish
  idempotently.
- *The lock:* calling `post` in a loop would commit, and so release the book
  lock, after the first winner, and a trade could then land in the middle of
  the settlement.

> **Reversal trigger:** "The book's writes share `posting.post`'s commit, and
> nothing may follow it" changes, so that callers own the commit. A loop of
> non-committing `post` calls inside one caller-owned transaction then does
> the same job. A speed measurement is **not** a reversal trigger. The 5-second
> criterion is a budget this must meet, not the reason it exists.

### Stamps continue every account's feed

The first payout is stamped one tick after the newest entry on **any** of the
accounts the batch locked: the union of the pool, PLATFORM and every winner.
Each later transaction in the batch is one tick after the one before. This is
"A movement's timestamp is fixed under its account locks, not trusted from the
clock", applied to a batch.

Stamping from the pool's entries alone would let a winner whose newest entry is
newer (a grant, or a trade in another market) receive a payout listed *before*
it. That would show a running balance the winner never had, which is the #187
bug again.

> **Reversal trigger:** the feed stops ordering by `created_at`, for example
> by switching to a sequence column.

### The residue empties the pool

After the payouts, the pool still holds `pool − payouts`:

- If that is positive, it is posted from the pool to PLATFORM.
- If it is negative, PLATFORM covers it, posting to the pool.
- If it is zero, nothing is posted.

The pool ends at exactly `0.0000`. A market nobody traded returns its whole
seed subsidy.

PLATFORM may go negative, because it is how credits are minted (ADR 0009). An
under-subsidised market is therefore paid in full, and the cost lands on the
house. That is the open question about `b·ln(n)` paying out, which this record
does not decide.

> **Reversal trigger:** a pool that has to survive settlement, for example
> per-market fees the platform keeps, or a decision that an under-subsidised
> market is refused or paid pro rata.

### Reads: a settled position shows its result, not a value

On a settled market, a portfolio row keeps `quantity`, `cost_basis` and
`average_entry_price`. It adds `result` (`paid_out` or `worthless`) and
`payout`. `price`, `value` and `unrealized_pnl` become `null`, and the row adds
nothing to `positions_value`. The payout is already in the balance, so
`net_worth` counts it once, not a second time at the frozen book. That is the
double count [3.4] #12's note warned about, and the reason ADR 0018's "a
closed market is valued at its last book" stops at settlement.

In the history, a row of kind `settlement` fills `market_id`, `outcome_id` and
`quantity`; `side` and `average_price` are `null`. A holder of only losing
shares has no entry, so they have no row.

The overview sinks settled markets to the end of its unfiltered view. Its counts
are unchanged in meaning. `PublicMarketStatus` and `MarketStatus` gain
`settled` in one commit, as ADR 0017's last section requires. `DECIDED_STATUSES`
gains SETTLED, so the public detail keeps showing the winner, and step 1 can
read it on a market already settled.

> **Reversal trigger:** a ticket that needs a settled position valued, or the
> trader browse needing to show what a market paid. That needs the record in
> market_service's projection, which is a new cross-service read.

### The audit entry is written by the ledger, in the payout transaction

The ledger appends `market.settled` through `shared/audit.py`'s writer, on the
payout's session, before `post_all` commits. ADR 0006's argument is unchanged:
the entry and the money commit together or not at all. market_service's step 5
writes no entry. Its own transaction records nothing an administrator decided,
since the ledger's entry already names the administrator, the market and the
result. Writing one there too would put two entries in the log for one action.

**The seam follows ADR 0012.** The ledger gains `core/audit.py` (its
`SOURCE_SERVICE`), `model/audit.py` (its `AdminAction` vocabulary) and
`service/audit.py` (the typed call). These may differ from market_service's in
constants and vocabulary only. If any of them would repeat market_service's
code beyond that, CLAUDE.md's rule applies: the extraction into `shared/` is
part of that PR, and not a copy with a TODO.

> **Reversal trigger:** the ledger moves to its own database. ADR 0006's
> outbox then becomes a prerequisite, as that record says.

## Traps the implementation must not fall into

**A Core audit insert is invisible to the replay guard.** `has_pending_writes`
"sees ORM writes only, not a Core `insert()` executed directly". If
`post_all` ever reached its replay branch after the audit insert, it would
commit that entry beside a replay, putting one entry in the log for a settlement
that wrote nothing. Two facts keep this from happening, and both must stay true:

- The latch is checked under the book lock before anything is written, so a
  settled market never reaches `post_all`.
- The settlement record is an ORM add, staged before the audit insert, so the
  guard does see a pending write if the latch is ever bypassed.

Move the audit insert ahead of the record, or make the record a Core insert,
and the second fact is gone.

**The pool may dip below zero partway through the batch.** `_refuse_overdrafts`
checks USER accounts only, so MARKET_POOL and PLATFORM are never refused. Paying
winners before the residue on an under-subsidised market takes the pool
negative between two transactions in the batch. This is harmless: there is one
commit, so no reader can see the intermediate state, and the pool ends at
`0.0000`. Do not "fix" it by reordering the residue first. The order is
irrelevant to correctness, and the check that would object does not exist.

## Consequences

**A second write route on the ledger.** ADR 0009's second amendment covers it,
input by input. Neither route takes money from its caller.

**market_service gains a route, a status member and no migration.** SETTLED is
a Python change (CLAUDE.md, "A market's status is a Python enum"). The
settlement record is a new table, which `create_all` creates on every database,
so it needs no hand-applied file.

**The leaderboard sees settlement for free.** [L-1] #38 reads the portfolio's
`net_worth`, which now counts a settled market once. [L-3] #40's "recalculated
after market settlement" has an event to hang on, and this record does not
decide how it is consumed.

**The preview and the snapshot keep quoting a settled market's frozen book.**
`q` is never written, and neither reader checks status (ADR 0017). That is the
existing asymmetry, and the frontend already hides the trade control on any
non-open market.

## Alternatives rejected

**market_service orchestrates: it marks SETTLED and then calls the ledger to
pay.** That would put an HTTP call inside, or after, a market_service
transaction, which is the dual write ADR 0006 and ADR 0017 refused. It also
needs a ledger route that takes the winner as an input, which ADR 0009's
amendment forbids. And if the call failed, the market would be left looking
finished with nobody paid.

**A background job that settles approved markets.** It has no caller and
therefore no token. ADR 0009's deferred question about service credentials
becomes a prerequisite, which is exactly what ADR 0009 says would happen.

**The winner in the request body.** That would be one administrator naming the
winner at payout time. It turns the two-person rule into a check that can be
bypassed by a single request, and gives the route an input that decides who is
paid.
