# ADR 0024: Resting orders fill against the AMM inside the trade that moved the price

- **Status:** Accepted, 2026-10-17, with all four sign-offs below ticked.
  Answers from Ernest's review of #324.
  - [x] 1. [4.2] #14's owner accepts the narrowing in "A suspended owner's
    orders are checked at placement only". Accepted, on condition that the
    administrator's cancel stays on the board as the follow-up: #326.
  - [x] 2. The team accepts that a cascade inside a tokened trade is not "a
    caller with nobody behind it" ("The cascade runs inside the trade that
    moved the price"). Accepted.
  - [x] 3. Ernest, who owns `service/posting.py` and `service/accounts.py`,
    accepts the order-aware running check in `_refuse_overdrafts`: a floor at
    the account's reservation, checked at the start of the batch and after
    each debit in movement order, with each movement carrying the drop in
    its account's reservation that it causes ("A reservation is derived from
    the order rows, and it is `posting`'s floor"). Accepted in his approving
    review of #324 on 2026-10-17: "sign-off 3 is a yes".
  - [x] 4. The team accepts three scope changes to #26 and #27: binary
    markets only, refusing a placement that crosses the book, and "resting"
    meaning not executable to within one tick ("Binary markets only, and no
    crossed book"). Accepted. #26 and #27 were edited to match on 2026-10-17.
- **Date:** 2026-10-17
- **Affects:** [T-6] #26, [T-7] #27, [T-8] #28, [FE][T-6] #61, [4.2] #14, [T-5] #25, [4.1] #13, [X-4] #37, [BE] #221, ADR 0005, ADR 0009, ADR 0010, ADR 0015, ADR 0017, ADR 0018, ADR 0021, ADR 0022
- **Implemented in:** nothing yet. This record precedes the limit-order stack
  for #26, #27 and #28, which is the first code that has to obey it.

## Context

#26, #27 and #28 add limit orders. A trader posts "buy 100 YES at 0.55 or
less" and it rests until the price reaches it. Every fill is against the
market maker. Traders never match each other.

Four records said this day would bring something new. ADR 0005 reverses "the
day something needs the trade path without being able to hold the ledger's
transaction", and names [T-7] #27. ADR 0009's amendment for [T-2] #22 says
#27 "fills a resting order with no request and no caller" and so "needs a
real service credential". ADR 0017 and ADR 0022 each say their per-trade call
becomes "unimplementable as written" on the day a trade arrives with no
caller token, and name #27. ADR 0021 calls #27 "the likely first" second
credential. DECISIONS.md keeps the question open as "Service-to-service auth
for a caller with no token".

All of those assumed one shape: a background executor that notices a price
and fills orders on its own. This record chooses a different shape, and most
of what follows is the argument that it needs none of those things.

Two more facts constrain the design. Categorical markets exist: market_service
accepts up to ten outcomes. And the ledger's balance is a sum of entries with
nothing stored beside it (ADR 0009), which any way of holding credits for an
order has to keep true.

## Decision

### The cascade runs inside the trade that moved the price

After a trade's own pending writes and before its commit, under the book
lock the trade already holds, the ledger fills every resting order the new
price has made executable. The trade and its fills commit together through
one `posting.post_all` call, and roll back together. A placement that fills
on arrival runs the same step after its own fill.

Each fill is an ordinary `trade_buy` or `trade_sell` transaction: the order
owner's USER account against the market's pool, the legs an instant trade
writes. Each has its own `state_version`. Its `context` carries the seven keys
an instant trade's does, plus `order_id`. Nothing runs between requests:
there is no executor, no sweep and no retry.

**Why this is not a caller with nobody behind it.** Every fill runs inside a
transaction that an authenticated person's request opened. The fill commits
if and only if that request does. What the earlier records needed a token
for, each in turn:

- **Is the market still trading?** (ADR 0017.) It is a question about the
  market, not about the order's owner. The triggering request's gate has
  answered it for this market, and the latch under the book lock has
  re-checked it. Every fill in the commit is in that market, under that
  lock.
- **Whose account is debited?** (ADR 0009's amendment.) The order row's
  `user_id`. It was written from the `sub` of the owner's own token, by a
  request the owner made. The triggering trader supplies no account, no
  amount and no order id. The cascade finds the orders itself, under the
  lock.
- **Is the owner still active?** (ADR 0022.) Asked at placement, with the
  owner's own token. See "A suspended owner's orders are checked at
  placement only", which is where this answer is narrower than ADR 0022's.
- **What credential does the ledger present, and to whom?** None, to
  nobody. Nothing on the cascade calls another service on the owner's
  behalf.

The owner's authority to be debited is the order. It was given with the
owner's token, it names a price, and it is spent only at that price or
better. That is the shape of settlement under ADR 0009's second amendment:
an administrator's request pays holders found in the ledger's own positions,
and the request names none of them.

**One price frame per commit**, at the final `state_version`, published after
the commit as today. Clients already discard any frame at or below the
version they hold, so the intermediate versions need no frame.

### A reservation is derived from the order rows, and it is `posting`'s floor

A resting buy reserves credits and a resting sell reserves shares. Neither is
money that moved, so neither writes an entry:

```
reserved credits(user)          = sum of (budget - spent) over the user's live buy orders
reserved shares(user, outcome)  = sum of (quantity - filled_quantity) over live sells
```

An order is **live** while its status is `open` and its market has no
`ledger.market_results` row. The balance stays the sum of the account's
entries. `GET /ledger/balances/me` gains `reserved` and `available`, and
`balance` keeps its meaning.

**The overdraft floor is the reservation, not zero, and it is checked in
movement order.** `_refuse_overdrafts` walks the batch's movements in order
and keeps two running totals for each USER account: its balance and its
reservation. Both start at their values before the batch, and the balance
must be at least the reservation there. Then, after each movement that
debits the account, the balance must be at least the reservation at that
point.

**A movement carries the drop in its account's reservation that it causes,
not its cost.**

- A buy-order fill that leaves the order open drops it by the fill's cost.
- A buy-order fill that exhausts the order drops it by the order's whole
  remaining budget, `budget - spent` with `spent` read before the fill. The
  order stops being live, so all of what it held goes, and a buy that fills
  completely almost always spends less than its budget: it buys from the
  current price up to its limit, so it averages better than the limit.
- A sell-order fill and a plain trade drop it by zero.

A placement's own fill follows the same rule.

**Where the figure before the batch comes from.** The reservation is read
after `accounts.lock`, so it is read under the lock that serialises every
write that can raise it (ADR 0015). The caller flushes its order-row updates
before `post_all`, so that read sees the reservation after the batch. Adding
back each movement's drop for that account gives the figure before it.

**A placement's new buy order counts at its full budget from the start of
the batch.** The read sees `budget - cost` for an order that filled in part on
arrival, and its fill drops the reservation by `cost`. It sees nothing for an
order that filled completely, and its fill drops the reservation by the whole
`budget`. Either way the figure before the batch includes the whole budget.
So the check at the start of the batch refuses a placement whose budget does
not fit in `available`, `409 insufficient_funds`, even one that would fill
completely on arrival for less than `available`. That is the contract's
error table: the budget has to fit, whatever the fill comes to.

**Two worked cases.** Both are refused, and both would have passed had a
movement carried its cost.

*A trade beside a buy order that fills completely.* A trader holds 100 and
has one buy order resting, budget 60, nothing spent. Available is 40. They
trade for 45, and the cascade fills their order completely for 50. The read
after the batch sees 0, because the order is `filled`. The trade drops the
reservation by 0 and the fill by 60, so the figure before the batch is 60.

| step | balance | reservation | check |
| --- | --- | --- | --- |
| start of batch | 100 | 0 + 0 + 60 = 60 | 100 ≥ 60, passes |
| the trade, cost 45, drop 0 | 55 | 60 | 55 < 60, refused |
| the fill, cost 50, drop 60 | not reached | | |

Had the fill carried its cost, 50, the figure before the batch would have
been 50, and the trade would have passed with 55 ≥ 50, paid for by the 10 of
budget that went when the order filled.

*A placement whose budget does not fit.* A trader holds 100, with 70
reserved by another buy order. Available is 30. They place a buy with budget
40, which fills completely on arrival for 28. The read after the batch sees
70, the other order's, and the new order's fill drops the reservation by its
whole budget, 40, so the figure before the batch is 110.

| step | balance | reservation | check |
| --- | --- | --- | --- |
| start of batch | 100 | 70 + 40 = 110 | 100 < 110, refused |
| the fill, cost 28, drop 40 | not reached | | |

Had the fill carried its cost, 28, the figure before the batch would have
been 98, and the fill would have left 72 against 70 and passed. The drop of
40 alone does not refuse it either: without the check at the start, the fill
leaves 72 against 110 - 40 = 70 and passes. The check at the start is what
refuses it. `error.details.shortfall` is 110 - 100 = 10.

**A refused placement carries `shortfall`, not `required`.** The details are
`balance`, `reserved`, `available` and `shortfall`, how much more the user
needs. On the `post_all` path the start-of-batch check computes it: the
reservation at the start of the batch, including the new order's budget, less
the balance. On the resting path it is the budget less `available`. Both give
10 in the second worked case (40 - 30 on the resting path). The check has the
balance and the reservation and not the budget, so a `required` would have
needed a figure handed to it. The trade route's `insufficient_funds` keeps
`required`, unchanged, so the two refusals are not to be confused.

**A placement locks the placer's account by one of two paths, and never both.**
Both take the book lock first, then accounts, as ADR 0015 orders them.

- *A placement that rests entirely* writes no movement, so `post_all` is not
  called. Under the book lock it takes the placer's account lock
  (`accounts.lock`), reads `available`, inserts the order and commits. The
  account lock is held to the commit. A trade in another market, which reads
  this user's reservation under the same lock, therefore waits for the
  placement, or the placement waits for it. Without the lock both read the
  reservation before the other's write commits, both pass, and the balance
  ends below the reservation. A resting sell takes no account lock: it checks
  shares, and shares change only under the book lock.
- *A placement that fills on arrival, fully or in part*, takes no lock of its
  own. `post_all` locks the placer's account in its single ascending sweep
  over every account in the batch, and its running check sees the new order's
  flushed row. A pre-lock on this path would put the placer's account ahead of
  the other owners' in lock order, out of ascending order, and two such
  placements can deadlock.

A fill refused by the running check inside a cascade is impossible by
construction, because every fill is bounded by its own reservation. If one
happens it is a bug: the commit rolls back with a 500, and the ledger logs it.

**Why not the batch's net, or the reservation after it.** A trader's own
resting sell can fill in the cascade their own buy triggered. Netted across
the batch, the sale's proceeds would pay for the buy that came before it, and
the account's feed would show a balance below zero between the two. That is
the reversal trigger of the D-NEW "`post_all` nets each USER account across
the whole batch", and this record fires it. Checking in movement order
against the reservation after the batch is not enough either. Take a trader
with 100 credits, 60 of them reserved by a buy order, who trades for 50, and
whose cascade then sells 30 of their shares and fills 20 of that buy order.
The trade's movement would leave 50 against a final reservation of 40, and
pass. With the running reservation it is checked against 60, and refused. So
**the trade has to be affordable before any resting order fills.** After it,
no fill can take an account's available credits lower: a buy-order fill
lowers the reservation by at least what it lowers the balance, by exactly
that unless it exhausts the order, and a sell-order fill only credits.

**A buy can never spend more than it reserved.** Its budget is
`quantize_cost(limit × quantity, side=Side.BUY)`
(`core/pricing.py:99`), which rounds up to `QUANTUM` (`core/pricing.py:54`),
fixed at placement. A fill is taken only if its priced cost is at most
`limit × fill quantity` (a sell: proceeds at least that). Summed over the fills, the cost is at most `limit × quantity`, which is
at most the budget. So `budget - spent` never goes negative and no order
needs an "exhausted" state.

**A sell's shares stay in its owner's position.** An instant sell is limited
to the position less the shares the owner's live sells reserve, checked under
the book lock as the holding check is today. Settlement and #221's void pay
reserved shares like any other.

**Cancelling is the release, and so is a market's end.** A cancel sets the
order's status. A settlement or a void writes its `market_results` row, and
every order on that market stops being live in that commit. Settlement and
void change nothing.

### Binary markets only, and no crossed book

On a binary market every order is a threshold on outcome 0's price `p0`:

| order | executable when | its fills push `p0` | threshold |
| --- | --- | --- | --- |
| buy outcome 0 at `L` | `p0` at or below `L` | up | `u = L` |
| sell outcome 1 at `L` | `p1` at or above `L` | up | `u = 1 - L` |
| sell outcome 0 at `L` | `p0` at or above `L` | down | `d = L` |
| buy outcome 1 at `L` | `p1` at or below `L` | down | `d = 1 - L` |

Outcomes are named by `MarketOutcome.position`, 0 and 1, never by label.

**The invariant:** every `u` is below `p0`, and `p0` is below every `d`, to
within one tick. No resting order is executable.

**A placement that would break it is refused**, `409 order_crosses_book`. A
new up-pusher needs `u` below every open `d` on the market, and a new
down-pusher needs `d` above every open `u`. Otherwise two traders' orders
fire each other in turn: a buy of outcome 0 at 0.60 and a buy of outcome 1 at
0.50 swing `p0` between the two limits, 40.5465 shares a side at `b = 100`,
and the two traders end up buying complete sets from each other through the
market maker. That is trader-to-trader matching by another route. It also
terminates only because quantity is finite: limits of 0.5001 and 0.5000 swing
0.0400 shares a time, which is about 50,000 fills for 1,000 shares inside one
request under the book lock.

**Termination.** A trade moves `p0` one way, say up. Every up-pusher was
already below the old price, so none becomes executable. The candidates are
the down-pushers with `d` below the new price, taken in ascending `d`. Each
fill moves `p0` down and never below that order's `d`. Each one either
exhausts its order, or lands `p0` on its `d`, after which every remaining
`d` is at or above the price and the loop ends. So it runs at most `m + 1`
times for `m` open orders on the market, and visits each order at most once.
The price never crosses back over where it started, and the invariant holds
again at the end. A placement keeps the invariant because of the crossing
rule, so its own cascade finds nothing. The step runs anyway, so there is one
path.

**The fill quantity is in closed form.** On a binary market,
`k = b·logit(L) - (q_i - q_j)` for a buy of outcome `i` and the negation for a
sell, floored to `QUANTUM` and capped at the remainder. `k` is a quantity,
not a cost, so it is floored with `ROUND_FLOOR` directly: `quantize_cost`
refuses a result of zero, and a `k` that floors to zero means the order is
not executable, not an error. Its cost is not in closed form:
`trade_cost_of` prices the floored quantity, so a fill costs exactly what an
instant trade of that size would.

**More than two outcomes is `409 market_not_binary`.** With three or more
there is no single price to sort on. A buy of one outcome lowers every other,
which can make a third outcome's buy executable, and the one-direction
argument above fails. Even where a resting set exists, filling one constraint
at a time converges like alternating projections, geometrically rather than
in a finite number of steps. The only bound left is the tick.

### Price priority, then time

Down-pushers are taken in ascending `d` and up-pushers in descending `u`: the
most aggressive order first. Ties go to the lower `seq`, an identity column
assigned when the order row is inserted under the book lock, so the sequence
is the lock order.

**This can starve less aggressive orders, and that is on purpose.** If the
most aggressive order is large enough, its fill takes the whole move and
returns the price to its own limit, and every order behind it gets nothing,
even those that were executable when the cascade began. #27's first
criterion asks for exactly this: "the best-priced marketable resting order
(price priority, then time)". Least aggressive first would fill more orders,
and it is a different rule from the ticket's. Do not reorder the cascade to
fill more volume.

### Self-fills are allowed

A trader's own resting order can fill in the cascade their own trade
triggered. Skipping it would leave an executable order resting when the
cascade ends, which breaks the invariant, and the termination argument for
the next trade with it. Every fill is against the market maker at a price
the order accepted, so nobody else is disadvantaged.

What makes it safe:

- The overdraft check keeps a running reservation in movement order (above),
  so nothing that follows a trade in its batch can pay for it: not a
  self-fill's proceeds, and not credits the trader's own buy order releases.
- Reserved shares cannot be sold twice. The instant sell's holding check
  subtracts them.
- The cascade flushes before its first read, so it finds a position the
  trade has just created. `autoflush` is off on this service's sessions, and
  without the flush a query would not see that row. A second flush comes
  just before `post_all`, so the reservation it reads sees the fills'
  updates to the order rows and a placement's new order.

### A suspended owner's orders are checked at placement only

The account check runs once, at placement, with the placer's own token, in
ADR 0022's place: after the replay lookup, before the market gate. It does
not run on a fill. A fill runs under the book lock inside somebody else's
transaction, so it cannot release the session to call out without dropping
that lock (ADR 0015), and the owner's token is not there to send.

**This narrows [4.2] #14's "suspension blocks trading".** A suspended owner's
resting orders keep filling. The record's reading is that a fill executes an
instruction accepted while the account was active, with credits and shares
reserved at that time. Nothing is taken that was not already committed to
the order.

**What follows for the owner:**

- They can cancel only while an access token they already hold is valid,
  which is at most 15 minutes after the suspension. Cancel makes no account
  check, but it still needs a token, and auth issues no new one to a
  suspended account.
- After that, the reservation is held until the market has a result, when it
  is released by derivation. Until then the orders may still fill.
- A market that never receives a result never releases it. Nothing in this
  iteration releases it sooner. An administrator's cancel would, and it is a
  stretch item, #326.

[T-8] #28's "never a stuck reservation" is met only under the reading "held
with no path to release": for a suspended owner the path is the market's
result, and it is not in the owner's hands. If the review reads "stuck" more
strictly, the administrator's cancel moves into this iteration.

> **Amended 2026-10-19 by [T-8] #326.** The administrator's cancel is built:
> `POST /ledger/admin/orders/{order_id}/cancel`, with a required reason and an
> `order.cancelled_by_admin` audit entry. A suspended owner's reservation now
> has a release path that does not wait for the market's result, so "Nothing
> in this iteration releases it sooner" above no longer holds. The orders
> still fill until an administrator cancels them. DECISIONS.md, "An
> administrator can cancel any live order, with a reason".

### The preview stays AMM-only

`GET /ledger/markets/{market_id}/preview` is a read and runs no cascade.
`prices`, `total` and `average_price` are right, because the trade is priced
by the same `trade_cost_of` call before its cascade runs. `post_trade_prices`
is the price after the trade alone. When resting orders fill, the price the
market ends at is closer to where it started. The contract says so.

### The trade response carries `book_state_version`, and still no prices

`TradeOut.state_version` stays "the book's counter after this trade". A new
field, `book_state_version`, is the counter after the trade and its cascade:
the version of the price frame the trader should wait for. It is stored in
the trade's `context`, so every replay returns it too. A price is still not
in the response, for the reason the D-NEW "The trade response is
reconstructed from `Transaction.context`, and carries no prices" gives.

### A placement takes no `state_version`

The limit is what protects the trader. A quoted version would refuse a limit
order because the price moved, which is the thing a limit order exists to
tolerate. The D-NEW "The trade's staleness check is strict `state_version`
equality, and the field is required" covers trades, not placements.

### A placement checks in the trade's order

The retry lookup, then the placer's account (ADR 0022), then the market
gate, then `books.ensure_open`, then `grants.ensure_granted`, then the book
lock and everything under it. The two middle steps are the trade path's, in
its place (`service/trading.py`), for its reason: each can commit, so each
runs before the book lock. Without the grant, a trader whose first ever
action is a limit order has no account and a balance of zero, and is refused
`insufficient_funds` despite `STARTING_CREDITS`.

### A placement stores its own `book_state_version`, and a replay answers from it

`ledger.orders` has a `book_state_version` column, created with the table in
ledger revision `0004`: the book's counter after the placement request. It is
the last fill's `state_version` when the order filled on arrival, and the
counter the order was placed against, read under the book lock, when it
rested entirely. A placement that rests writes no transaction, so this column
is the only place that counter is recorded.

A retry with the same key returns the order as it stands now, the stored
`book_state_version`, and as `fills` only that order's fills with a
`state_version` at or below it. Those are exactly the fills the placement
request made: the order did not exist before it, and every later fill,
from anyone's cascade, has a higher `state_version`. Finding fills by
`context->>'order_id'` alone would return every fill the order has ever had.

> **Amended 2026-10-19 by [T-7] #27.** A placement that fills on arrival now
> runs the cascade before it commits, so the stored column and the response's
> `book_state_version` are both the book's counter after that whole request:
> the request's last fill, which can be a fill of another trader's order
> rather than the placed order's own. It can therefore be higher than the
> highest `state_version` in the placement's `fills`. The replay rule is
> unchanged: the cascade runs before the order row is added, so it never
> fills the order being placed, and that order's fills at or below the stored
> counter are still exactly the fills the placement request made.

## Consequences

**No service credential and no cross-service write.** The question
DECISIONS.md keeps open stays open, for a background settlement or for any
later caller with nobody behind it. #27 is no longer an instance of it.

**Every trade in a market with resting orders does more work under its book
lock.** At most `m + 1` fills for `m` open orders. That is fine at classroom
scale, tens of orders per market, and nothing measures it beyond that.

**One trade can commit several transactions, with consecutive
`state_version`s.** The price history gets one point per fill. The admin
trade feed lists fills as trades by their owners. Each fill is a `trade_buy`
or `trade_sell` entry in its owner's history, with its own average price.

**The shared write path changes.** `_refuse_overdrafts` becomes order-aware:
a floor at the reservation, checked at the start of the batch and then in
movement order, and on each movement the drop in its account's reservation
that it causes. Every caller of `post` and `post_all` gets the walk.
Settlement and the grant debit no USER account and drop no reservation, so
neither is affected.

**`ledger.orders` carries the placement's `book_state_version`**, in
revision `0004` with the table, so a replay can name its own fills.

**The order rows' status column is not the authority on liveness.** An
order on a settled market reads `open` for ever. Every read of live orders
joins `market_results`. This is ADR 0011's shape: the status column is not
the authority.

**A suspended owner's orders keep filling, and their reservation can be held
indefinitely**, as stated above.

**`order_crosses_book` reveals the nearest opposing resting limit.** Its
details carry the bound a new order's limit must clear, which is another
trader's limit price. That is accepted, because the refusal is unusable
without it.

**[BE] #221's refund must decide how it meets the reservation floor if it
debits a USER account.**

> **Amended 2026-10-20. Decided by [ADR 0025](0025-voiding-a-flawed-market.md),
> and not built yet. Answered: it debits none.** A void's refunds credit USER
> accounts from the market's pool, and its residue moves between the pool and
> PLATFORM. `_refuse_overdrafts` reads only the USER accounts a batch debits
> (`_debited_users_of`, `service/posting.py:149-157`), so the reservation floor
> is never consulted. Every order on the voided market stops being live in the
> void's commit, because liveness asks only whether a `market_results` row
> exists, of any kind. A fill is a trade on the pool's feed, so the void's
> replay refunds it like any other buy, and a self-fill is a sale off the
> owner's newest lot.

**Tests owed.** Each is a criterion the code PRs are reviewed against.

- The "big trade crosses 3 resting orders" scenario, asserting the final
  price and the fill order (price, then `seq`).
- Entries sum to zero per transaction and overall under concurrent trading
  plus cascades, with balance at least the reservation for every user and
  position at least reserved shares.
- A resting placement in one market racing an instant buy in another by the
  same user, with `asyncio.Barrier` and `pg_stat_activity` lock waits, no
  timing. It must fail with the placement's account lock removed.
- A cancel racing a fill, in both orderings, plus the partial case.
- A self-fill: the triggering trade is refused unless it is affordable before
  any resting order fills (the 100 and 60 example above).
- A buy order that fills completely for less than its budget, in the same
  batch as a trade by its owner: the trade is refused unless it was
  affordable before the fill (the first worked case above).
- A placement whose budget exceeds `available` but which would fill
  completely on arrival: refused, `409 insufficient_funds` (the second worked
  case above), with `shortfall` 10 and no `required`.
- Two of the owner's buy orders filling in one batch, one exhausted and one
  partial. Balance 100, order X budget 60 and order Y budget 30, both
  unspent. A trade of 5, then X fills completely for 50 and Y partially for
  10. The walk passes at every step and ends at balance 35 against a
  reservation of 20.
- A self-fill sell crediting the account before a buy-order fill debits it:
  allowed. It is safe because a buy-order fill lowers the reservation by at
  least its cost, so the floor rises to meet the debit.
- A first-ever placement by a user with no ledger account: the signup grant
  is minted and the order is accepted.
- A retried placement returns only the fills the original request made,
  after another trader's cascade has filled the order further.
- `order_crosses_book`, one case per row of the threshold table.
- An order fills at most once per commit. The termination argument implies
  it, and the realtime contract's one order frame per fill depends on it.

**Placement and cancel write no entries**, so neither appears in the money
history. They belong in an order history, which is not in this iteration.

**The realtime service gains a second channel**, `order.update`, and a frame
delivered to every socket of the order's owner without a subscription. It
still owns no data (ADR 0010).

## Alternatives rejected

**An escrow account per order.** Placing a buy would move its budget to an
`ORDER_RESERVE` account, and each fill would pay the pool from there. It is
fully double-entry and leaves `posting` alone. But a fill would then be
reserve to pool, not user to pool, which fails #27's "fills write the same
double-entry ledger rows as instant trades". The owner's history would show a
debit at placement and a release at cancel, and not the fills. And
`balance` would become "available", so net worth, the leaderboard and the
portfolio would all have to add the reserve accounts back.

**A background executor.** A loop that watches prices and fills orders. It is
the caller with nobody behind it that four records warned about: it needs a
service credential, and a gate and an account check it cannot ask with
anyone's token. It also leaves a window, as long as its interval, in which
an executable order rests, and the invariant fails for that long.

**Categorical markets.** No termination proof, for the reasons above. Its own
ticket, with the proof first.

**Filling a crossing order and cancelling its remainder** (immediate or
cancel), instead of refusing it. Deterministic, but the trader asked for a
resting order and gets less than they asked for, which is harder to explain
than a refusal that names the price in the way. It is the frontend's call,
and it can replace the refusal later without a migration.

**Cancelling a suspended owner's orders.** auth would push the suspension to
the ledger, or call a ledger route: a dual write with no outbox, and auth
would have to know the ledger exists. ADR 0022 rejected the same thing, and
ADR 0021 the same direction. Asking ADR 0021's lookup route about every
order owner on each trade puts that key on the trade path, which ADR 0022's
third reason refuses. A sweep that cancels them later is a second caller of
the key, which ADR 0021 says gets its own key and its own record. That sweep
is the follow-up if the narrowing above is ever refused.

---

> **This reverses on the day a fill has to run outside the request that
> moved the price.** An order expiry, a release at `close_time`, a
> suspension sweep or a categorical market's cascade run in chunks would each
> be a caller with nobody behind it. That caller needs the service
> credential DECISIONS.md's open entry describes, and this record's argument
> does not cover it.
>
> **It is revisited if a fill can be matched against anything but the
> book.** Then it is ADR 0018's second liquidity mechanism, and valuation
> changes with it.
>
> **It is revisited when one trade's cascade is measured to be too slow**,
> because a market holds more crossable orders than a book lock can carry.
> The answer then is a cap on fills per trade, which leaves executable orders
> resting and so needs a new invariant first.
>
> **It is revisited if [4.2] #14's owner refuses the narrowing.** The
> suspension sweep above becomes a prerequisite, with its own credential.
