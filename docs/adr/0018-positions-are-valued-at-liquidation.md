# ADR 0018: Positions are valued at liquidation

- **Status:** Accepted
- **Date:** 2026-09-28
- **Affects:** [T-4] #24, [FE][T-4] #50, [L-1] #38, [L-3] #40, [3.4] #12, ADR 0009, ADR 0017
- **Implemented in:** nothing yet. This record precedes [T-4] #24, which is the first code that has to obey it.

## Context

[T-4] #24 asks for each position's "current price" and "unrealized P&L", and
its original note gave the formula: `quantity × current price − cost_basis`.
[L-1] #38 then asks for net worth as "available credits plus the
mark-to-market value of open positions", computed by "the same
position-valuation rules as the portfolio page". So whatever #24 decides a
position is worth, the leaderboard ranks on it.

"Current price" has one natural reading in this system and it is the wrong one
for this job. The price the snapshot and the preview report is LMSR's
**marginal** price, the gradient of `C(q)`: what the next infinitesimal share
costs. No trader can sell a position at that price. A position of any real
size moves the book as it is sold, and every share after the first is sold for
less.

Nothing else will buy the shares either. There is no order book and no other
trader to sell to. The only way to turn shares into credits before resolution
is a sell through `POST /ledger/markets/{id}/trades`, priced by `C(q)` and
rounded down to the tick, per "`quantize_cost` takes an unsigned magnitude;
the caller applies the sign". A valuation that is not that sale is a number no
action on this platform can realise.

## Decision

**A position's value is what selling the whole of it now would credit.** For a
holding of `held` shares of outcome `i`, it is the magnitude
`core/pricing.py::trade_cost_of` returns for a sell of `held` at the book's
current `q` and `b`. That is the function the trade route calls, with the same
rounding, so for the same `state_version` the portfolio's `value` and the
proceeds of that sell are the same string, by construction.

Unrealized P&L is `value − cost_basis`. `positions_value` is the sum of the
values, and `net_worth` is `balance + positions_value`. Every term is already
on the scale-4 grid, so these are exact sums, and nothing is rounded after the
valuation.

**The marginal price is still reported, as `price`, and decides nothing.** It
equals the snapshot's for the same `state_version`, because it goes through
the same quantizer ("The price read exists once, and the price quantizer is in
`core/pricing.py`"). It answers "where is the market", not "what is this
worth".

**A sale that pays less than one tick is worth zero.** `trade_cost_of` raises
`ProceedsBelowTick` for it, because the trade route refuses to take shares for
zero credits ("A sell whose proceeds quantize to zero is refused, not
quoted"). The position cannot be sold, so its value is `0.0000`, and its
unrealized P&L is minus its whole basis. That is the dust case "A sell
releases cost basis at average cost" warned about, stated honestly.

## Worked example

An empty two-outcome book, `b = 100`. A trader buys 500 YES.

- The buy is charged `C(500, 0) − C(0, 0) = 431.35681679…`, rounded up to
  **431.3569**. The book is now `q = (500, 0)`.
- YES's marginal price there is `e⁵ / (e⁵ + 1) = 0.99331…`, shown as `0.9933`.

**Marginal rule:** `500 × 0.99331 − 431.3569 = +65.2967`. The portfolio says
the trader is up 65.30 credits.

**Liquidation rule:** selling 500 YES returns the book to `(0, 0)`. Because
the cost function is path-independent, the proceeds are the same
`431.35681679…`, rounded down to **431.3568**. Unrealized P&L is **−0.0001**.

Nobody else has traded, so the trader cannot be up 65 credits. The only thing
they can do with the position is sell it, and a sale pays back one tick less
than they paid. The marginal rule reports a profit that is entirely the
trader's own price impact, counted as if it were somebody else's money, and it
grows with the size of the position. On a leaderboard that ranks on it, the
winning strategy would be to buy as much as possible of anything.

The general shape: a position bought and not yet moved by anyone else shows a
loss of at most two ticks, and never a gain. The buy's ceiling lands at most a
tick above the true cost and the sale's floor at most a tick below it, and the
engine's last-digit residue is far too small to turn either into a gain ("A
cost exactly on a tick can round one tick against the trader, or toward them
on a sell" has the edge cases). That is correct and the frontend should expect
it.

## Consequences

**`value` is not `quantity × price`, and the frontend must not recompute it.**
[FE][T-4] #50 renders `value` and `unrealized_pnl` as sent. Showing the
marginal `price` beside them is fine; deriving a value from it rebuilds the
rule this record rejects. `docs/api/ledger-service.md` says so in one
sentence, for the same reason ADR 0017's preview asymmetry got one.

**Net worth on the leaderboard is this computation.** [L-1] #38 calls the same
function, not a copy of it. Each holder's value answers "what would *I* get if
I alone sold now"; the values of different holders of one outcome do not add
up to anything real, because each assumes it is the only sale. Ranking on them
is sound, since every user is asked the same question of the same book. A
platform-wide total built from them is not sound.

**Any trade moves every holder's value in that market.** A leaderboard that is
recalculated "after committed trades" ([L-3] #40) must mean after any trade in
any market a ranked user holds, not only that user's own trades. The cost is
one engine evaluation per held position per read. #38 and #40 decide whether to
pay it per read or once per refresh. They do not decide the rule.

**A closed market is valued at its last book, and nobody can make that sale.**
After close, the trade route refuses a sell with `market_closed` (ADR 0017),
so the value of a position in a CLOSED, PENDING_RESOLUTION or APPROVED market
is what a sale *would* have paid at the last traded state. `q` cannot move
after close, so the number holds still. Settlement ([3.4] #12) is what replaces
it with a realized outcome, and it owns that criterion.

**The portfolio needs no market status.** Valuation depends only on `q`, `b`
and the holding, which are all the ledger's. Labels and status are the
frontend's to join from market_service.

## Alternatives rejected

**Marginal mark-to-market, `quantity × price`.** The formula the issue
started with and what most prediction-market UIs show. Rejected by the worked
example: it overstates every position by the price impact of selling it, and
the error grows with the size of the position, which is exactly the variable
a leaderboard should not reward.

**The average of the marginal price before and after the sale.** Closer, and
still a number nobody is paid. The only exact answer is the cost function
itself, and it is already computed by the function the trade route uses.

**Value at payout: one credit per share if the outcome wins.** Unknown before
resolution, and it is the realized outcome [3.4] #12 now owns.

**A stored value per position.** It would be wrong the moment anyone traded in
that market. It is a second source of truth, rejected for the reason in
"`cost_basis` is stored; average entry price is derived".

---

> **This reverses on the day a sale can be filled by anything other than the
> LMSR book.** A second liquidity mechanism, such as limit orders or any
> resting order a sell could match against, means `trade_cost_of` no longer
> defines what a sale pays, and liquidation value against the book stops being
> the best price available. The test is concrete: the trade route's proceeds
> for a sell stop being `trade_cost_of`'s magnitude. At that point valuation
> becomes a question about the whole venue, and this record is superseded
> rather than amended.
>
> What does **not** reverse it is a UI that would prefer the bigger number, or
> a leaderboard that looks flat. Both are arguments for the rule this record
> rejects.
