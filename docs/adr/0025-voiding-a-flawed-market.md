# ADR 0025: Voiding a flawed market, by any administrator without a stake, refunded at what was paid, ledger first

- **Status:** Proposed. Accepted when both sign-offs below are ticked.
  - [ ] 1. Ernest, who drafted the design in #221 (revision 2), accepts the
    changes this record makes to it. Each is listed under "What changed from
    revision 2", with the code that forced it.
  - [ ] 2. The team accepts that one administrator can end an approved market
    without a winner inside its dispute window, overturning a result two
    administrators agreed ("Who may void").
- **Date:** 2026-10-20
- **Decided by:** Ihsan, who owns #221, on 2026-10-20: rulings A to D
  below, and the earlier ruling in #221's thread that the void gets its own
  record rather than an amendment to ADR 0019.
- **Affects:** [BE] #221, [3.4] #12, [3.3] #11, [3.2] #10, [2.3] #7, [2.1] #5,
  #210, [2.2] #6, [BE][2.4] #63, [T-4] #24, [T-5] #25, [T-6] #26, [T-7] #27,
  [T-8] #28, [L-1] #38, [B-1] #359, ADR 0006, ADR 0007, ADR 0009, ADR 0011,
  ADR 0013, ADR 0015, ADR 0016, ADR 0017, ADR 0018, ADR 0019, ADR 0021,
  ADR 0023, ADR 0024
- **Implemented in:** nothing yet. This record precedes the void stack for
  #221, which is the first code that has to obey it.
- **Citations:** every file:line is pinned to `dev` at 22e8e1e, after #412
  made the trading helpers public. A bare path is
  `backend/ledger_service/`'s; market_service's paths say so.

## Context

A dispute ([3.3] #11) fixes a **wrong winner**: an administrator sends the
result back, and two administrators decide again. Nothing fixes a **flawed
market**, one where no outcome is correct. The question was ambiguous, the
event was cancelled, or the market was published by mistake. Today such a
market can only be resolved to some outcome, which pays somebody for a result
that did not happen. And a market whose creator never proposes again after a
rejection or a send-back waits for ever (ADR 0023: "#221's void is the way
out").

Ernest's design for #221, revision 2 of 2026-10-07, is the basis of this
record. Since it was written, five things landed under it:

- [3.4] #12 built settlement (ADR 0019) on `ledger.market_results`, one row per
  finished market with a `kind`, shaped for a void and leaving its columns to
  this record.
- [3.3] #11 stored the dispute window's end at approval, as
  `dispute_window_ends_at` (ADR 0023).
- [T-6] #26, [T-7] #27 and [T-8] #28 built limit orders (ADR 0024). A fill is a
  trade, and it raises `state_version`.
- [BE][2.4] #63 built a replay of a market's trades from the pool's feed, for
  the price history.
- #187 fixed entry stamps, so the pool's feed is in lock order.

So most of revision 2 stands, and some of it is now wrong about the code it
builds on. This record keeps the first and corrects the second.

The facts that constrain the design:

- market_service holds the status and the window. The ledger holds the money
  and every trade. No transaction can span both (ADR 0019).
- A write route takes no money from its caller (ADR 0009).
- The ledger never stores a balance or a total (ADR 0009).
- Administrators can trade. The leaderboard ranks them like anyone else
  (DECISIONS.md, "Every ledger `USER` account holder is ranked, administrators
  included").

## Decision

### A void ends a market with no winner, and refunds every share still held

A void ends the market with no winning outcome. Every sale that already
happened stands. Every share still held is refunded at what its holder paid
for it, worked out as below. The market becomes VOIDED in market_service and
its row in `ledger.market_results` has `kind = 'voided'`. A void is final:
there is no un-void, because undoing it would mean taking refunds back, and
the no-overdraft rule can refuse that.

### Who may void: any administrator who has not traded the market

Any administrator may void, with a reason of at least 10 characters once
trimmed. This is close-early's and send-back's shape (ADR 0014, ADR 0023) and
ADR 0007's flat tier: audited, not approved.

It is not quite like-for-like. A void is the first single-administrator action
that is final, moves money, and can overturn an approval two administrators
made (ADR 0016). Two things narrow it:

- **An administrator who has ever traded the market cannot void it**,
  `403 admin_has_a_stake`. Another administrator has to. Without this, an
  administrator who bet on the losing side could void inside the dispute window
  and get their stake back. "Ever traded" means the token's `sub` is the
  `context.user_id` of any trade in the market's replay, read under the book
  lock. A position sold to zero still counts. A fill counts: a fill's
  `context.user_id` is the order's owner (`service/trading.py:459`, from
  `_write_fill` at `:575-600`), so an administrator whose resting order filled,
  even in part, has a stake. An administrator whose order never filled has no
  trade and no stake, and the void releases their reservation like anyone
  else's.
- **A void pays nobody more than they paid.** No winner is paid. A refund is
  bounded by what the shares cost, and PLATFORM's top-up is capped at one seed.
  What a void can do is deny a winner a payout, inside the window in which
  send-back could do the same. That is the trust ADR 0007 already extends, and
  the void is observed twice in the log (below).

A retry that only finishes an already-recorded void moves no money, so it is
not stake-checked. A direct call to market_service's `/void` is not checked
either: market_service cannot see trades (ADR 0003, and ADR 0023's "A 'has
traded' check"), and that call moves no money. It is the direct-call gap,
below.

### When a market can be voided: the status column decides, and the window is send-back's

| Stored status | Voidable? |
| --- | --- |
| DRAFT, SUBMITTED | No |
| OPEN | No. Close it early first ([2.3] #7), then void. |
| CLOSED | Yes |
| PENDING_RESOLUTION | Yes. The pending proposal is cleared. |
| APPROVED, `now < dispute_window_ends_at` | Yes |
| APPROVED, window ended, or no `dispute_window_ends_at` | No. The result is final ([3.3] #11). |
| SETTLED | No |
| VOIDED | Yes, for a market an administrator marked voided directly (below) |

market_service decides this as `voidable`, the way it decides `settleable`: a
pure function in market_service's `core/settling.py` beside `is_settleable`,
on the public detail and on every overview row, judged on the request's
Python clock as every read is (market_service's `service/browsing.py:334`).
For APPROVED it is `is_in_dispute_window` (market_service's
`core/settling.py:21-30`), the very question send-back asks, exclusive at its
edge. The ledger reads the flag and never works it out itself.

**Why VOIDED is voidable.** Not for a retry: a retry of a void the ledger
recorded finds its own row and replays before it ever reads the status. It is
for the direct call. An administrator who calls market_service's `/void`
directly marks the market VOIDED and refunds nobody, and the ledger has no
row. Because VOIDED stays voidable, the ledger's route can still refund that
market, and its last step answers `200` without writing.

**It reads the stored status, not the derived one.** The public detail
derives "closed" from `close_time` before the sweep writes CLOSED (ADR 0011).
If `voidable` followed the derived status while `/void` checked the column,
the ledger could refund a market whose row still reads OPEN, and `/void` would
then refuse it. With the sweep off everywhere it would stay that way. Gating
both on the column is the stricter direction, the choice propose makes (ADR
0013): the worst case is a void control that appears a few seconds late, on a
market nobody can trade in the meantime.

**The void step accepts five minutes more than the flag.** market_service's
`POST /markets/{id}/void` accepts APPROVED while
`clock_timestamp() < dispute_window_ends_at + SETTLEMENT_GAP`, read after the
row lock (market_service's `core/settling.py:18`, and settle's `_database_now`
after its lock, market_service's `service/market_service.py:721` and `:736`).
The ledger checks `voidable` before it moves money, so its call can land just
after the window ends. On one stored end `E`:

| Instant | send-back | `voidable` | `/void` step | `settleable`, settle step |
| --- | --- | --- | --- | --- |
| before `E` | yes | yes | yes | no |
| from `E` to `E + 5 min` | no | no | yes | no |
| from `E + 5 min` | no | no | no | yes |

The `/void` step and the settle step never both accept. Both read
`clock_timestamp()` after the same row lock, and each edge is the stored
column plus the same constant. ADR 0023 anticipated exactly this.

### How a refund is worked out: lots, newest first

Every trade's transaction, a fill included, records in its `context` the
holder (`user_id`), the market, the outcome, the side, the quantity, the total
and the book's `state_version` after it (`service/trading.py:458-470`). The
market's book holds `b`. Under the book lock, the void:

1. **Reads the market's trades through `pool_trade_query`**
   (`service/ledger_service.py:291-312`), the price history's read: the pool
   account's entries joined to their transactions of kind `trade_buy` or
   `trade_sell`, on `ix_ledger_entries_account_feed`
   (`model/entities.py:380-385`), ordered by `(created_at, id)` as the price
   history orders it (`service/price_history.py:62-64`). Ruling B, below.
2. **Rebuilds `q` from the quantities alone.** `q` starts at zero for every
   outcome when the book opens (the seed moves money, never `q`). Nothing is
   priced in this pass.
3. **Keeps a stack of lots for each holder and outcome.** A buy pushes a lot:
   the quantity, the total it was charged, and `q` just before it.
4. **A sell takes shares off the top of the newest lot first**, moving to
   older lots when one runs out. Selling therefore undoes the holder's latest
   buys, the way the price slides back down the steps their own buying
   climbed. A self-fill (ADR 0024) is no special case: it is a sell off the
   newest lot.
5. **Each lot left is refunded what its remaining shares cost:**
   - a lot nothing was sold from: exactly the total it was charged;
   - a lot partly sold from: the cost of its bottom `rem` shares at the lot's
     own `q`, `C(q_lot + rem·e_i) − C(q_lot)`. That is computed by
     `core/pricing.py`'s `trade_cost_of` as a sale of `rem` from
     `q_lot + rem·e_i`, so it is floored to the tick as every sale's proceeds
     are (`core/pricing.py:138`). No new rounding rule exists. A sale whose
     proceeds floor to nothing raises `ProceedsBelowTick`
     (`core/pricing.py:141`). The void's core module reads that refusal as a
     refund of `0.0000`, as `liquidation_values_of` reads it as a value of
     `0.0000` (`core/pricing.py:190-222`, the catch at `:217-219`). That lot
     adds nothing to its position's refund.
6. **A holder's lots in one outcome add up to that position's refund.**

The pricing engine is called only for partly-sold lots, at most once per buy.
That is where the replay's time goes.

**The guard.** Three checks. Any failure is a bug, refused
`500 void_replay_mismatch` with nothing written:

- The shares left in each holder's lots equal `ledger.positions.quantity`,
  compared over the full set both ways, so a position with shares and no
  trades is caught too.
- The replayed final `q` equals `market_outcomes.q` for every outcome.
- The trades' `state_version`s run 1, 2, 3, … in order, with no gap or
  repeat, **and** the last one equals the book's `state_version`. A trade or a
  fill raises it, each by one in its own `write_trade`
  (`service/trading.py:416`), and nothing else does. The replay reads each
  row's `context.state_version`, **never** `book_state_version`, which an
  instant trade also carries and which is the counter after its whole cascade
  (`service/trading.py:467-468`).

The first half of the third check, that the versions run 1, 2, 3, … from the
start, is the price history's (`core/price_history.py:66-70`). **Ruling B:
that half moves into one pure function in `core/` that both replays call.**
The second half, that the run ends at the book's `state_version`, is the
void's alone. The price history does not compare its last point with the
book, and this record does not change that. The price history keeps mapping
its failure to `500 price_history_inconsistent`, and the void maps either
half's to `void_replay_mismatch`. The void does not raise `state_version`, so
the price history's reversal trigger ("Any event other than a trade starts
bumping `state_version`, for example a void") has not fired.

**Why not order by `context.state_version` in SQL**, as revision 2 did. It was
there because entry stamps could disagree with lock order (#187). #187 is
fixed: `post_all` stamps under the account locks, one tick past the newest
entry on any account it holds, movement *i* at that plus *i* µs
(`service/posting.py:239-263`, `:485`). So `(created_at, id)` on the pool's
feed is lock order, and the third check refuses it if it ever is not. One
query and one check, shared with the price history, rather than a second
ordering of the same rows.

#### The worked numbers

At `b = 1`, two outcomes, a lone trader. Every figure is the real pricing
engine's, at scale 4.

*Buy 4, sell 2.* Buying 4 of the first outcome is charged
`C(4,0) − C(0,0) = 3.32500…`, rounded up to **3.3251**. One lot of 4, bought
at `q = (0,0)`. Selling 2 gets `C(4,0) − C(2,0) = 1.89122…`, rounded down to
**1.8912**, and takes the top 2 shares of that lot. The 2 left are its bottom
shares, refunded `C(2,0) − C(0,0) = 1.43378…`, rounded down to **1.4337**.
Net: `−3.3251 + 1.8912 + 1.4337 = −0.0002`. Average cost would refund half
the basis, **1.6626**, for a net of **+0.2287**, from nothing. Oldest first,
counted share by share, the sale takes the lot's bottom 2, and the 2 left are
its top shares, refunded `C(4,0) − C(2,0)` rounded down, **1.8912**: a net of
**+0.4573**.

*The round trip.* Hold 10, bought for **9.3069**. Buy 990 more for **990.0000**
(`989.99995…` rounded up), then sell 990 for **989.9999** (rounded down).
Newest first, the sale empties the 990 lot and the first lot is untouched, so
the void refunds **9.3069**: the trader is down 0.0001 on the round trip.
Oldest first, the sale takes the first lot and the bottom 980 of the second,
and the 10 left are the 990 lot's top shares, from `q = (990,0)`, refunded
`C(1000,0) − C(990,0)`, which is **10.0000** at the engine's precision:
**+0.6930**. Revision 2's +0.6929 priced those 10 as the lot's bottom shares,
`C(20,0) − C(10,0)` rounded down to 9.9999; it is a gain either way. The
0.0001 between the two figures is precision, not which shares: exactly,
`C(1000,0) − C(990,0)` is 10 less about `e^−990`, which floors to 9.9999,
and only reads 10.0000 because precision 50 cannot hold `e^−990`.

**Why one trader alone cannot profit.** With nobody else trading in between, a
sale undoes the newest lot at the same `q` it was bought at, so it receives
what those shares cost, and the refund pays back exactly the shares kept. Any
gain left after a void comes from other traders moving the price, which is a
real profit the sale already took. A trader's own trades in other outcomes can
break the equality only against them: buying one outcome makes the others
cheaper, so it only lowers what a later sale gets. Rounding only ever costs
the trader: buys round up, sales round down, and a partly-sold lot is refunded
rounded down. A fill is priced through `trade_cost_of` with the same rounding
as an instant trade (`core/limit_orders.py:198`), so the argument covers
fills. An independent review fuzzed the rule through the real `lmsr.py` and
`pricing.py`, about 280,000 trades, 2, 3 and 5 outcomes, `b` from 0.1 to
100: the best a lone trader got from a void was 0.0000.

A held position whose refund rounds down to 0.0000 writes no transaction.

### Two accounts, and the cap of one more seed

**The scheme.** One account buys. A helper account buys after it, pushing the
price up. The first sells at the top and keeps a real profit. The helper never
sells, and is refunded in full. With full refunds and PLATFORM covering every
gap there is no ceiling: the review measured +367 credits over 20 rounds at
`b = 10`, almost all of it paid by PLATFORM. Accounts are free, each with
1,000 starting credits, and the leaderboard ranks by credits.

**The cap.** Before anything is posted, the void compares what the refunds owe
in total, `R`, the sum of the uncut refunds, with what the pool holds, `P`.
`P` is the pool account's derived balance, the sum of its entries
(`accounts.balance_of`, `service/accounts.py:98-108`), read under the book
lock, as settlement reads it (`service/settlement.py:332`). It is the seed
plus every buy minus every sale, but it is read, never added up from the
trades. With `S` the book's `seed_subsidy`:

- **`P ≥ R`, the pool covers them.** Every refund is paid in full, and
  `P − R` goes to PLATFORM.
- **`R − P ≤ S`, short by at most one seed.** Every refund is paid in full,
  and PLATFORM covers `R − P`.
- **`R − P > S`, short by more.** PLATFORM adds exactly `S`, and each refund
  `r` is cut to `r × (P + S) / R`: multiplied first, then divided, in a fresh
  context at the pricing engine's precision of 50 (`core/lmsr.py:23`, the
  context `_engine_context` builds at `:31-38`), then floored to the tick. So
  the cut refunds never add up to more than `P + S`. The rounding left over
  goes back to PLATFORM. The order of operations is what keeps two builders
  from cutting the same refund to different ticks: `3 × (1/3)` and
  `3 × 1 / 3` differ at any finite precision, 50 included. Fixing the
  precision as well makes the result deterministic, whatever context the
  caller happens to run in.

"A refund above zero" is judged **after** the cut. A refund the cut floors to
`0.0000` writes no transaction, is not counted in `positions_refunded`, and
its share stays in what goes back to PLATFORM.

An illustration of the arithmetic, not a real book: `S = 10`, `P = 40`, and two
holders owed 50 and 20, so `R = 70` and the pool is 30 short. That is more
than one seed, so PLATFORM adds 10 and every refund is cut by `50/70`: 50 to
**35.7142** and 20 to **14.2857**, 49.9999 in all. The 0.0001 left over goes
to PLATFORM, so PLATFORM's net movement to the pool is **9.9999**, and the
pool ends at `40 + 9.9999 − 49.9999 = 0.0000`.

Honest holders get everything back except in extreme cases: the pool falls
more than a seed short only when sellers took more real profit than the seed.
PLATFORM's loss on a voided market is at most two seeds, the one it put in
when the book opened and the one it tops up. Whether refunds were cut is in
the response and in the `market.voided` audit entry.

The movement between PLATFORM and the pool is one transaction of kind
`void_residue`, keyed `void-residue:{market_id}`, skipped when it is exactly
zero. Either way the pool ends at exactly `0.0000`.

**The batch is never empty.** The pool never holds less than the seed: LMSR's
cost is path-independent and increasing in every `q`, and rounding favours
the pool, so every buy less every sale is at least `C(q_now) − C(0) ≥ 0`. So
if no refund is above zero, the residue is the whole pool, at least one tick,
and is posted. `post_all` refuses an empty batch as `500 malformed_batch`
(`service/posting.py:317-327`). The void asserts that it has at least one
movement before it calls `post_all`, and that guard stays as the backstop.

### The ledger goes first, in settlement's order

An administrator calls `POST /ledger/markets/{market_id}/void` with
`{"reason": "..."}` and nothing else (`extra="forbid"`). The ledger:

1. **Checks the token and the body.** No token, `401 invalid_token`. A
   trader, `403 not_an_administrator`. `reason` at most 5,000 characters,
   market_service's ceiling for every reason it stores (`422
   invalid_request`), and at least 10 once trimmed (`422 void_incomplete`),
   checked before any read, as the administrator's order cancel checks its
   reason. `void_incomplete` carries no `details`, as the ledger's
   `cancellation_incomplete` carries none: the ledger adds `details` to
   `invalid_request` and to refusals that report figures
   (`controller/errors.py:41-93`), and a short reason has no figure to
   report. market_service's `*_incomplete` codes carry `details`, but that is
   market_service's envelope, and the code names the one field already.
2. **Reads market_service, holding no database connection.**
   `market_status.read_terms`, the public detail, forwarding the token:
   `status` and `voidable`. Unknown market and no book,
   `404 market_not_found`. market_service down, slow, 5xx, an unreadable body,
   or a 404 for a market the ledger has a book for,
   `503 market_terms_unavailable`. A failure refuses here even on a retry of a
   void already refunded, as settlement's does.
3. **Looks up the market's `ledger.market_results` row without a lock.**
   - `voided`: a repeat. Sum `positions_refunded`, `total_refunded` and the
     residue from the void's own transactions, found by their derived keys.
     Re-run **only** the lot replay, to compute `total_owed`, which no
     transaction records: no guard and no stake check, because neither can
     change what the first request did. Roll back, and go to step 8 with the
     **recorded** reason. A repeat therefore answers the first request's
     `200` body. Like any request it can be refused at step 2, the read,
     which runs before the lookup; after the lookup, it can fail only at
     step 8.
   - `settled`: `409 market_already_settled`.

   This lookup may only short-circuit, never decide a write: a hit names a
   committed row nothing updates or deletes.
4. **Checks the status, only when there is no row.** `settled`:
   `409 market_already_settled`. Not `voidable`: `409 market_not_voidable`.
   VOIDED with no row here (an administrator called `/void` directly) is
   voidable, and carries on. A refusal writes nothing and opens no book.
5. **Opens the book if it is cold** (`books.ensure_open`, its own earlier
   commit), **takes the book lock** (`books.lock_book`), and **looks for the
   row again**, under the lock (ADR 0015). The same two answers as step 3.
   Never re-run step 7 on a voided row: a second commit would add a second
   `market.voided` entry.
6. **Replays the trades** (above). A guard failure,
   `500 void_replay_mismatch`. The caller in any trade,
   `403 admin_has_a_stake`. Either writes nothing.
7. **Posts and commits once**, through `posting.post_all`:
   - one `void_refund` per position with a refund above zero, keyed
     `void-refund:{market_id}:{user_id}:{outcome_id}`, one leg on the holder's
     USER account and one on the pool, so it is one history row naming the
     outcome and the quantity;
   - the `void_residue`, if not zero, last;
   - the `ledger.market_results` row: `kind = 'voided'`, `outcome_id` null,
     `reason` (trimmed), `voided_by` (the token's `sub`) and `recorded_at`;
   - one `market.voided` audit entry.

   Positions and `q` are not written.
8. **After the commit**, with no connection held and no statement in between,
   calls market_service's `POST /markets/{id}/void` with the caller's token
   and the recorded reason, never the retrying administrator's. Anything but
   a `200` is `503 void_unconfirmed`: the refunds have committed and stand.
   Repeating the request finishes it and writes nothing in the ledger.
9. **Answers `200`** with the market, the recorded reason, administrator and
   time, and the figures. A repeat answers the same.

**Why this order.** It is settlement's, as ADR 0019's 2026-10-10 amendment
and DECISIONS.md's "A recorded settlement replays before any status check;
the lookup before the lock can only short-circuit" set it, for the same
reasons. A repeat is how a failed step 8 is finished, so a repeat has to
reach the replay whatever market_service now reports: an APPROVED market
refunded at 10:04:59 whose `/void` failed reads `voidable: false` a second
later. Revision 2 put the lookup before the read, which is the trade path's
order, and DECISIONS.md rejected it for settlement. It issues a statement
before the first HTTP call and then has to roll it back, and it saves
nothing, because a void's replay still makes step 8's call.

**Rejected:** *the stake check before the guard.* If the replay disagrees with
the positions, its list of traders is not trusted either.

### One commit, and the record before the audit insert

Step 7 is settlement's shape (`service/settlement.py:354-379`): the result row
is an ORM add, the audit entry is a Core insert through the shared writer,
and `post_all` commits both with the money. ADR 0019's first trap applies
unchanged. `has_pending_writes` cannot see the Core insert
(`core/database.py:97-98`), so the result row is the pending ORM write that
keeps a bypassed latch from replaying with only the audit entry pending.

The batch locks the pool, PLATFORM when a residue is posted, and every refunded
holder, once each, ascending, after the book lock. A refunded holder's other
trades queue behind the void, and so does a first-time grant when PLATFORM is
in the batch. That is settlement's cost, carried over.

### market_service's side

- **`MarketStatus.VOIDED`, final.** It joins the frozen and resolution tables
  in the same commit as the enum member, so no autosave can turn it back into
  a draft (the bug #220 found for SETTLED). Every later write is
  `409 market_already_voided`: save, submit, publish, close early, propose,
  approve, reject, send-back and settle. A repeat `/void` is the one
  exception. VOIDED is not in `DECIDED_STATUSES`, so no winner is shown, even
  when the approval columns are kept. It joins `PublicMarketStatus`, the
  allowlist the public reads filter on (market_service's
  `model/entities.py:102-107`), so a
  voided market stays visible to traders.
- **Ruling C: the status tables are composed first, in a refactor PR.**
  `_FROZEN_STATUS_ERRORS` and `_RESOLUTION_STATUS_ERRORS` (market_service's
  `service/market_service.py:73-87`) each list SETTLED themselves, and so
  does the decision state check in `_proposal_to_decide` (`:525-555`, its
  state checks at `:543-548`). DECISIONS.md declined composing them until "A
  second terminal status (#221's void)". This is that trigger. A refactor PR
  composes or derives the tables with no behaviour change, and VOIDED is then
  added once.
- **Two columns, `voided_at` and `void_reason`,** in market revision `0003`.
  The reason is a column because traders must see it and market_service
  cannot read the audit log. That breaks the habit of ADR 0014, ADR 0016 and
  ADR 0023 that the audit entry is the only copy of a reason, on purpose: a
  void is final and moves money, and a trader needs to understand their
  refund. No `voided_by` column: nothing would read it, and the audit entries
  name the administrator. No CHECK, for ADR 0023's reason: no database rule
  here reads a status string.
- **`POST /markets/{id}/void`, documented as the ledger's call only.** It
  takes `{"reason": "..."}` and does **not** re-check the reason. The ledger
  already did, and a stricter check here would fail the same way on every
  retry after the money moved. It locks the market row (`get_any`: any
  administrator may void), then:
  1. VOIDED: `200`, nothing written, before the clock is read.
  2. SETTLED: `409 market_already_settled`.
  3. CLOSED or PENDING_RESOLUTION: accepted.
  4. APPROVED: reads `clock_timestamp()` and accepts while
     `now < dispute_window_ends_at + SETTLEMENT_GAP`. From that instant, or
     with no stored end, `409 market_not_voidable`.
  5. Anything else: `409 market_not_voidable`.
- **What a void writes.** `status = VOIDED`, `voided_at` from the clock read
  after the lock, and `void_reason` as forwarded. From PENDING_RESOLUTION it
  clears the seven proposal columns, as a rejection does. From APPROVED it
  keeps the proposal and approval columns and `dispute_window_ends_at` as
  history, as settle does. `closed_at` is left alone.
- **`market.marked_voided`, in the same transaction.** The actor is always the
  token's user, never a value from the body, so nobody can log a void under
  another administrator's name. It carries the market, the reason, the status
  it left, and any cleared proposal, snapshotted **before** the clear (ADR
  0016). A repeat appends nothing.
- **Settle answers VOIDED with `409 market_already_voided`**, after SETTLED's
  `200` and before the clock is read. Today any status but APPROVED is
  `market_not_approved` (market_service's `service/market_service.py:730-731`).

**The direct-call gap is accepted, under ADR 0007.** Any administrator can call
`/void` directly and mark a market voided with no refunds. The ledger's route
can still refund it afterwards, because a VOIDED market stays voidable and the
ledger has no row for it. A
`market.marked_voided` with no `market.voided` before it, for the same market,
is the trace a direct call leaves, the same shape as
`market.marked_settled` (ADR 0019's 2026-10-05 amendment). It closes when
services get their own credentials.

### Reading APPROVED before the lock is still safe

ADR 0019 reads the status before the book lock, and calls that safe "because
send-back and settlement never overlap". Its trigger is "any further
transition out of APPROVED". The void is one, so the argument has to be made
again. It holds, for a different reason on each side.

**On the ledger, the latch serialises the two money paths.** Settlement and
the void are both ledger first, and both write the one `market_results` row
under the book lock before market_service hears of either. Whichever commits
its row first wins, and the other finds that row under the lock and writes
nothing: settlement answers `409 market_already_voided`, and the void
`409 market_already_settled`. The primary key stops a market ending both
ways. So a stale APPROVED read cannot pay a settlement over a void, or refund
a void over a settlement. Send-back needed the five-minute gap because it
moves the status in market_service with no ledger record. The void's ledger
record is the thing that stops it.

**In market_service, the edges never meet.** The `/void` step accepts APPROVED
until `E + 5 min`, and the settle step from `E + 5 min`, both on
`clock_timestamp()` after the same row lock. And the ledger acts on
`voidable`, which ends at `E`, and on `settleable`, which starts at
`E + 5 min`: no answer of the public detail is both. The skew condition ADR
0019 rests on, the services' clocks within five minutes of the database's,
is unchanged.

**What is left is the direct-call gap.** An administrator calling `/void`
directly before `E + 5 min` while the ledger pays a settlement that read
`settleable` on a fast clock leaves winners paid and a VOIDED market. That
needs skew near the gap or a deliberate direct call, both already accepted.

### How it meets limit orders

- **Orders are released in the void's commit, with nothing written to them.**
  An order is live only while its market has no `market_results` row, of any
  kind (`service/reservations.py:34-39`). So in the void's commit every order
  on the market stops reserving. Its `status` stays `open` and reads
  `released` (`service/orders.py:160-162`), it drops out of the live list
  (`:619`), and a cancel is `409 order_released`
  (`service/cancellation.py:113-114`). No order frame is published, as for
  settlement.
- **ADR 0024's open question is answered.** "[BE] #221's refund must decide how
  it meets the reservation floor if it debits a USER account." It debits
  none. Refunds credit USER accounts from the pool, and the residue moves
  between the pool and PLATFORM. `_refuse_overdrafts` reads only debited USER
  accounts (`_debited_users_of`, `service/posting.py:149-157`, used at `:187`;
  the walk at `:220-225` skips any account with no floor), so the floor is
  never consulted. The released reservations need no figure either: the
  reservation is derived from live orders, and none is live after the commit.
- **A placement racing the void.** A placement that passed the market gate
  and queued on the book lock behind the void finds the row under the lock
  and is refused `409 market_closed` (`service/orders.py:534-535`), whatever
  the row's kind. A placement that took the lock first commits first, and its
  fills, if any, are trades in the void's replay.
- **A cancel racing the void.** A cancel, the owner's or an administrator's,
  takes the book lock before it reads the order
  (`service/cancellation.py:157`), so it serialises with the void. Cancelled
  first, the order releases its remainder and its fills are in the replay.
  The void first, the cancel is `409 order_released`. Both are safe. Only the
  cancel that wins writes the administrator's `order.cancelled_by_admin`
  entry.
- **Fills are refunded like any trade.** A fill is a `trade_buy` or
  `trade_sell` on the pool's feed with its own `state_version`, and a buy fill
  pushes a lot.

### What readers show

- **Portfolio, ruling A: a voided row's `payout` is its refund.** `result` is
  `"refunded"`, a new `PositionResult` member (`model/schemas.py:88-96`).
  `payout` is the refund the row's `void_refund` transaction paid, read through
  a left join to `ledger.transactions` on the derived key
  `void-refund:{market_id}:{user_id}:{outcome_id}`, which the unique index on
  `idempotency_key` serves. A refund that rounded to zero wrote no
  transaction, and the join coalesces it to `0.0000`. The join is in the
  portfolio's own statement only, not in `held_outcomes_query`
  (`service/valuation.py:189-228`), so the leaderboard's refresh reads
  nothing new: net worth never reads `payout`. The portfolio reads the row's
  `kind` (`settled_kind`, `service/valuation.py:212`) only to choose
  `refunded`: a refund that rounded to zero has no transaction, so the join
  alone cannot tell a voided row from a settled one.
- **How the refund reaches the valuation.** `valuation_of` gains a
  keyword-only `refunds`, a mapping from `(market_id, outcome_id)` to the
  refund, empty by default. The portfolio builds it from its join column and
  hands it in. The voided branch of `_settled_positions_of`
  (`service/valuation.py:110-130`) reads `refunds.get(key, 0.0000)`. The
  leaderboard (`service/leaderboard.py:125-135`) passes nothing, so its rows
  never need the column, its voided rows carry a payout of `0.0000`, and it
  ignores that: it reads only `net_worth`. *Rejected:* an optional refund
  attribute on the row. A SQLAlchemy `Row` has no defaults, so the
  leaderboard's rows would raise on the missing attribute, or every read would
  need a `getattr`.
- **The rest of the portfolio row.** `price`, `value` and `unrealized_pnl`
  are `null` and the row adds nothing to `positions_value`, as on a settled
  row. Today a voided row would reach the settled branch
  (`service/valuation.py:182`) and come out `worthless` with payout `0.0000`
  (`:122-125`), which is what Ernest's note on #221 calls odd. The payout can
  differ from `cost_basis`: the refund follows the lots, and the basis is the
  average.
- **P&L needs no change.** `realized_pnl` is the balance against the grant
  plus the basis still open, and a voided row's basis is closed, like a settled
  one's (`service/valuation.py:279-288`). The refund reaches the balance as
  ordinary entries.
- **History.** One `void_refund` row per refunded position: `amount`,
  `balance_after`, `market_id`, `outcome_id` and `quantity` (the shares
  refunded); `side` and `average_price` `null`. `trade_fields_of` handles only
  SETTLEMENT today (`service/ledger_service.py:121-126`), so it gains a branch.
  `void_residue` never reaches a user's history.
- **Exposure and price history need no code.** Both already report the
  record's kind and its outcome without filtering
  (`service/exposure.py:107-120`, `:144-145`;
  `service/price_history.py:132-139`).
  A voided market reads `result: "voided"` and a null winner, and exposure's
  `pool` and per-outcome payouts are `null`. The price history adds no point:
  the void moves no price. Only `docs/api` changes. This departs from the
  wording of Ihsan's comment on #221, that "the portfolio and exposure need a
  void branch too". Exposure already branches on whether a row exists and
  nulls every payout figure for any kind, so it needs none. The portfolio
  does, because it maps a kind to a `result` and a payout.
- **The trade feed** lists trade kinds only, so a void adds no row.
- **The leaderboard needs no change.** It values holders through
  `valuation_of` (`service/leaderboard.py:125-135`, ADR 0021), so a voided
  market's rows add nothing to net worth and the refund is in the balance.
- **market_service.** `voided` is a status on the public detail and browse,
  and an accepted value of the trader browse's `status` filter, as `settled`
  is. The public detail and `MarketOut` carry `voided_at` and `void_reason`,
  required keys with nullable values. The public detail and every overview row
  carry `voidable`. The overview accepts `voided` in its filter, counts it
  zero-filled, and sinks voided markets with settled ones to the end of its
  unfiltered view. The cursor's `settled` flag (market_service's
  `core/paging.py:42-51`) is
  renamed `finished` and keeps its encoding, so a cursor issued before the
  change still decodes to the same place.

### Settlement learns the record's kind

Settlement replays on any `market_results` row (`service/settlement.py:290-298`
and `:309-317`), and `_replay` asserts the row names a winner (`:136`). Once
voided rows exist, a settlement on a voided market would reach that assert and
answer a bare 500. So both of settlement's lookups branch on `kind`: a voided
row is `409 market_already_voided`, with nothing written. Its status mapping
(`:176-179`) answers `voided` from market_service with the same code. The
trade latch (`service/trading.py:812`), the placement latch, liveness,
exposure and price history still branch on "a row exists" and never filter on
`kind`. The portfolio reads `kind` only to choose `refunded` (above).

### `voidable` is required, and market_service deploys first

Ruling D. `MarketTerms` gains `voidable: bool` with no default, read by index
and required to be a real JSON boolean, exactly as `settleable` is
(`service/market_terms.py:191`, `_to_settleable` at `:130-139`). A missing
flag read as true would refund a market whose result is final. A missing flag
read as false hides a broken deploy as a market that can never be voided.

The ledger parses the terms strictly on every pull, the trade gate included,
so a ledger with this parse against a market_service without `voidable`
answers `503 market_terms_unavailable` to every trade. market_service's
`voidable` therefore lands and deploys first, and the stack below orders it
so.

## Consequences

**A sixth write route on the ledger, and it takes no money.** The body is a
reason. The holders, amounts and accounts come from the ledger's own trades
and positions under the book lock. ADR 0009 carries the input table.

**The ledger gains its first codes for a finished market.** It has no
`market_already_settled` today; that code is market_service's alone. New on
the ledger: `market_already_settled` and `market_not_voidable` (409, the
void's), `market_already_voided` (409, settlement's, on a voided market),
`admin_has_a_stake` (403), `void_incomplete` (422), `void_replay_mismatch`
(500) and `void_unconfirmed` (503).

**Models and revisions.**

- Ledger revision `0006`: `ResultKind.VOIDED`; `reason` (text) and
  `voided_by` (uuid) on `market_results`, both nullable, each set exactly when
  `kind = 'voided'` by a hand-written CHECK. The existing
  `ck_market_results_outcome_iff_settled` already admits a voided row with no
  outcome. `TransactionKind` gains `VOID_REFUND` and `VOID_RESIDUE`, which
  need no DDL (non-native enum).
- Market revision `0003`: `voided_at` and `void_reason`. `MarketStatus.VOIDED`
  needs none (root CLAUDE.md, "A market's status is a Python enum").

**The record still stores no totals.** A repeat sums positions refunded, total
refunded and residue from the void's own transactions, which the derived keys
find. What the refunds were owed before a cut, which no transaction records,
is recomputed on a repeat by re-running only the lot replay over the same
trades, without the guard and without the stake check. Those trades cannot
change: the row refuses every later one. So a repeat answers the first
request's body.

**Two audit entries per void**, one per commit: `market.voided` from the
ledger with the money, and `market.marked_voided` from market_service with the
flip.

**Known edges, accepted.**

- If the ledger commits the refunds but `/void` cannot be reached until five
  minutes after the window ended, `/void` refuses `market_not_voidable` and the
  market stays APPROVED with its money refunded. The ledger's settlement
  cannot pay it: the row refuses. A direct `/settle` could still mark it
  SETTLED, and the two services would then disagree for good. The same
  judgement as ADR 0019's five-minute gap; fixing it is by hand.
- A void holds PLATFORM's account lock for its whole batch when it posts a
  residue, so a first-time grant waits behind it. Carried over from
  settlement.
- A voided position's `payout` is a refund, not one credit per share. A
  client that reads `payout` as "what the winning shares paid" must branch on
  `result` first, which the contract already asks.

## What changed from revision 2

Each is a correction against the code on `dev`, first checked at 0f21679 and
re-pinned at 22e8e1e, not a change of mind.

1. **The replay guard's wording.** "Only a trade raises `state_version`" is now
   "a trade or a fill", and the replay reads `context.state_version`, never
   `book_state_version`.
2. **The read order.** Revision 2 sorted by `context.state_version` because of
   #187. #187 is fixed, so the replay reuses `pool_trade_query` in
   `(created_at, id)` order and shares the price history's 1..N check.
3. **The step order** is settlement's: the read, then the unlocked lookup,
   then the status checks.
4. **Settlement on a voided row** would be a bare 500 today. It branches on
   `kind`.
5. **The window** is the stored `dispute_window_ends_at`, not `window_ends_at`,
   and `voidable` reuses `is_in_dispute_window`.
6. **The status tables** are composed in a refactor PR first, because a
   second terminal status trips DECISIONS.md's trigger.
7. **ADR 0009's route count.** The void is the sixth write route, not the
   third.
8. **Exposure and price history** need no code, only a line each in
   `docs/api`.
9. **The portfolio** needs a branch: today a voided row would read
   `worthless`, payout `0.0000`. It gets `PositionResult.REFUNDED`, and ruling
   A fixes where the payout is read: a left join on the derived key, in the
   portfolio's statement only, coalesced to `0.0000`.
10. **History** needs a `void_refund` branch in `trade_fields_of`.
11. **`MarketTerms`** requires `voidable`, so market_service deploys first.
12. **`post_all` refuses an empty batch.** The void never builds one.
13. **Revision numbers** are ledger `0006` and market `0003`.
14. **This is its own record**, not an amendment to ADR 0019, and ADR 0019's
    APPROVED-exit argument lives here, with a pointer there.

## Alternatives rejected

**Average cost** (each position's `cost_basis`). On this market every extra
share costs more, so a sale gets the top prices while average cost releases
only the average. One trader alone can farm a void: +0.2287 from buy 4,
sell 2 at `b = 1`.

**Paid in minus got back, per market, floored at zero.** It blocks the cheat,
but nets an honest trader's real profit against their refund.

**Undoing every trade with a clawback.** It needs user balances below zero,
which ADR 0009 forbids, or claws back only what is left, which a cheater beats
by spending the profit first.

**Oldest shares sold first.** A sale takes the holder's oldest shares, the
bottom of the oldest lot first, so the shares left are the top of the latest
buys. The label does not change the cash a sale received, so it refunds the
most expensive shares and pays the cheat more. Checked against the cost
function at the engine's precision, at scale 4:

| Trades | `b` | Newest first | Oldest first |
| --- | --- | --- | --- |
| buy 40 for 33.2501, sell 20 for 18.9122 | 10 | refund 14.3378, net −0.0001 | refund 18.9122, net **+4.5743** |
| buy 4 for 3.3251, sell 2 for 1.8912 | 1 | refund 1.4337, net −0.0002 | refund 1.8912, net +0.4573 |
| the round trip above | 1 | refund 9.3069, net −0.0001 | refund 10.0000, net +0.6930 |

The first row is almost certainly revision 2's "+4 in the example".

**PLATFORM always covers the gap.** No ceiling on the two-account scheme.

**Refunds capped at the pool alone.** Honest holders are cut whenever anyone
took a profit larger than the seed.

**A two-administrator void.** A void would need the proposal machinery for a
market that may have no creator left to propose. The stake check, the
window and the log are the controls instead (ADR 0007).

**A separate table for voids.** ADR 0019's 2026-10-09 amendment rejected it:
two latches for every reader, and nothing stops a market ending both ways.

**Storing each lot, or each refund, at trade time.** A second source of truth
for what the trades already record, and a change to the trade path for a
rare event. The replay costs time only at void time.

**Writing order rows on a void.** DECISIONS.md, "An open order is live only
while its market has no result", rejected it for settlement, and the void
inherits the derivation.

**A realtime frame for the void.** Trading has already stopped, the void moves
no price, and a page shows the new status on its next fetch. DECISIONS.md, "A
settlement publishes no realtime frame", for the same reasons.

## Not built

- Voiding an OPEN market in one step. Close it early first.
- A two-administrator void.
- Un-voiding.
- A clawback of profits already taken. The cap limits the two-account scheme
  instead.
- Telling traders on the market page that refunds were cut. The amount is in
  their portfolio and history; the fraction is in the audit entry.
- A live push when a market is voided.

## Tests that matter

Each must fail with the line it guards removed.

- **The cheat is closed**, at `b = 1`. Buy 4 in one trade, sell 2, void: the
  trader ends at no more than they started with. Red under average cost
  (+0.2287) and under oldest first (+0.4573). The round trip, hold 10, buy
  990, sell 990, void: the refund is what the 10 cost. Red under oldest first
  (+0.6930).
- **A partly-sold lot whose refund floors to nothing** adds nothing to its
  position's refund and fails nothing; on a position with no other lot, the
  position writes no transaction. Red if the core module lets
  `ProceedsBelowTick` escape.
- **The cut's arithmetic.** Three holders owed equal thirds of a short pool:
  each cut refund is the same tick whichever builder computes it. Red if the
  cut divides first or runs at the default precision. A refund the cut floors
  to zero writes nothing and is not counted.
- **Honest profit stands.** A holder buys, another trader's buy raises the
  price, the holder sells part: they keep the sale and are refunded exactly
  the bottom shares' cost.
- **Untouched lots refund exactly** what they were charged.
- **A fill is a lot.** A resting buy that fills, then a void: the owner is
  refunded the fill's charge. A self-fill sell takes the newest lot.
- **An administrator with a stake** who traded the market, including one who
  has since sold to zero and one whose resting order filled in part, is
  refused `403 admin_has_a_stake` and nothing is written. A different
  administrator can then void. An administrator whose order never filled is
  not refused.
- **The replay guard.** A position that disagrees with the replay, and a
  `state_version` gap, are each refused `500 void_replay_mismatch` with
  nothing written. The shared 1..N check keeps the price history's tests
  green.
- **Money adds up.** After a void the pool is `0.0000`, the whole ledger sums
  to zero, and so does every void transaction. A pool surplus, a shortfall
  within one seed, and a market nobody traded (the whole seed goes back).
- **The cap.** The two-account scheme with a shortfall above one seed:
  PLATFORM puts in exactly one seed, every refund is cut by the same
  fraction, the cut refunds add up to no more than pool plus seed, and the
  pair's gain is at most two seeds. Red if PLATFORM covers the whole gap, and
  red if refunds are cut within one seed.
- **Never an empty batch.** A market whose every refund rounds to zero posts
  the residue alone.
- **Retry after the window.** Refunds commit inside the window, `/void` fails
  (`503 void_unconfirmed`), the clock passes `E`, and the retry still finishes,
  writes nothing in the ledger, and forwards the first administrator's
  reason. With `E` and `E + 5 min` pinned, `/void` accepts until `E + 5 min`
  and refuses from it.
- **The status column, not the clock.** A market past `close_time` that the
  sweep has not written CLOSED is not voidable.
- **Void against trade**, behind an `asyncio.Barrier`, with the market gate
  stubbed open so only the latch can refuse. Red with the trade path's
  under-lock lookup removed, and red if it filters on `kind`.
- **Void against placement**, the same shape: a placement queued on the book
  lock behind the void is refused `409 market_closed`. Red with the
  placement's latch (`service/orders.py:534-535`) removed. In the other
  order, the placement's fills are in the void's replay and its rest is
  released.
- **Void against settlement** at the ledger, on a market stubbed as both
  voidable and settleable: exactly one row and one set of money movements, and
  the loser answers `409 market_already_voided` or `409 market_already_settled`.
  Red with the lookup after the book lock removed.
- **Settlement on a voided row** answers `409 market_already_voided`, not a
  500. Red with the kind branch removed.
- **Void against void.** One writes, both answer `200` with the same body.
- **A repeat answers the first body**, `total_owed` and `refunds_cut`
  included, on a market whose refunds were cut. Red if the repeat skips the
  lot replay, and red if it runs the stake check (a repeat by an
  administrator who traded the market still answers `200`).
- **Void against the administrator's cancel**, behind a barrier: either the
  order is cancelled, with one `order.cancelled_by_admin` entry and its fills
  in the replay, or the cancel is `409 order_released` and no entry is
  written. Red with the cancel's book lock removed.
- **Orders are released.** An open order on a voided market reads `released`,
  is not listed live, reserves nothing, and its cancel is
  `409 order_released`.
- **Races on the market row**, the void taking the row first: send-back,
  approve, reject and propose each answer `409 market_already_voided`. Red
  with the row lock removed from either path.
- **One commit, rollback, no connection across HTTP**: settlement's three
  tests, pointed at the void.
- **Frozen.** Every market_service write on a VOIDED market is
  `409 market_already_voided`, the autosave and the settle step included.
- **Audit.** One `market.voided` per void that writes, none on a repeat. One
  `market.marked_voided`, with a cleared proposal snapshotted before the
  clear.
- **The portfolio** shows `refunded` with the refund as `payout`, `0.0000`
  for a refund that rounded to zero, and the leaderboard's net worth is
  unchanged by the join.
- **`MarketTerms`** refuses a body without `voidable`, or with a string, as
  `503 market_terms_unavailable`.
- **Speed.** Voiding a market with 1,000 positions takes under five seconds at
  the service, excluding the two calls to market_service, settlement's bar.
  The replay is new work, so measure it against 10,000 trades too.

---

> **This reverses if a void has to be undone.** An un-void would take
> refunds back, which needs a clawback the no-overdraft rule can refuse. It is
> a new record.
>
> **It is revisited if the cap is measured to cut honest holders.** The cap is
> one seed because the two-account scheme needed a ceiling and honest
> shortfalls are rare. A real market cut by it is the evidence to size it
> again.
>
> **It is revisited when anything other than a trade or a fill raises
> `state_version`**, or when the pool's feed stops ordering by
> `(created_at, id)`. The replay and its guard assume both.
>
> **It is revisited when a service credential exists.** `/void` then requires
> it, the direct-call gap closes, and `market.marked_voided` stops being the
> only trace of one.
>
> **It is revisited if a stake has to mean more than a trade**, for example
> an administrator's resting order that never filled. Today that order has
> no transaction, and the void only releases it.
