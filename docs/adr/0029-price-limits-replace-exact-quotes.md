# ADR 0029: Price limits replace exact quotes on the trade route, shipped expand then contract

- **Status:** Accepted, 2026-11-05. Supersedes DECISIONS.md, "The trade's
  staleness check is strict `state_version` equality, and the field is
  required".
- **Date:** 2026-11-05
- **Decided by:** Ernest, on 2026-11-05, approving #640's design: price
  limits, the two-step rollout, and a default tolerance of 1% in the trade
  panel.
- **Affects:** #640, [5.4] #20, #644, [T-2] #22, [T-3] #23, ADR 0015,
  ADR 0017, ADR 0024
- **Implemented in:** the expand step, on `640-perf-2-price-limits`:
  `backend/ledger_service/core/errors.py` (`PriceBeyondLimit`),
  `core/pricing.py` (`refuse_if_beyond_limit`), `model/schemas.py`
  (`TradeIn`), `service/trading.py` (`execute`), `controller/routes.py` and
  `controller/errors.py`. The contract step, `640-perf-2b-limits-required`,
  is not built yet.

## Context

Since [T-2] #22, a trade names the `state_version` its preview returned.
Under the book lock the ledger compares it with the book's own, and any
difference, older or newer, is `409 quote_stale`. The DECISIONS entry
"The trade's staleness check is strict `state_version` equality, and the
field is required" chose that rule, and named its own successor:

> If [FE][T-2/T-3] #49 measures a material share of trades refused
> `quote_stale` under ordinary use, the successor is **not** a widened
> comparison but a `max_cost` bound on a buy and a `min_proceeds` bound on a
> sell, checked under the same lock.

The measurement came from #20's load test rather than from #49, on a harder
case than ordinary use. `docs/load-test.md` records two production runs on
2026-11-04, each with 50 traders for five minutes on two 1-vCPU app servers:

| Run | Trades committed | Stale quotes retried | Trades given up | Trade p95 |
| --- | --- | --- | --- | --- |
| A: one market | 126 | 4,427 | 692 | 5.45 s |
| B: five markets | 738 | 5,091 | 633 | 4.52 s |

In Run A that is about 35 refusals for every trade that committed. Both app
servers sat at 100% CPU and the database at 45%.

The rule causes this by itself. Every trade moves the price, so it raises
`state_version`, so every quote taken before it is stale. Trades on one
market queue on one book row (ADR 0015). The first trade through the lock
makes every quote waiting behind it stale, so most lock holders are
refusals. Each retry pays the whole trip again: the auth call (ADR 0022),
the market call (ADR 0017), and the lock. In Run A, about 35 of every 36
trips through that path ended in a refusal.

A version number says that the price moved, not how far. A one-share trade
and a thousand-share trade each raise it by one. So the rule refused a
trader whose price moved by a tick, and a trader whose price moved in their
favour, the same way it refused one whose price got much worse.

Limit orders already work the other way. ADR 0024's "A placement takes no
`state_version`" says the limit is what protects the trader, and that a
quoted version would refuse an order because the price moved.

## Decision

### The contract, in its final state

`POST /ledger/markets/{market_id}/trades`:

- A buy names `max_cost`, the most it will pay: a decimal string, greater
  than 0, at most four decimal places. A sell names `min_proceeds`, the
  least it will accept: a decimal string, 0 or more, at most four decimal
  places. Each is required for its side. The other side's field in the body
  is `422 invalid_request`, because a client that sent it believes it is
  protected. `extra="forbid"` stays.
- `state_version` becomes optional. It no longer decides anything.
- Under the book lock, the ledger prices the trade at the current `q`. A
  buy whose cost is above `max_cost`, or a sell whose proceeds are below
  `min_proceeds`, is refused `409 price_beyond_limit`. Nothing is written.
  `error.details` carries `limit` (as the client sent it), `current_total`
  (the cost or proceeds at the current price, four places) and the book's
  current `state_version`, so the client can re-preview, and retry once the
  book is at that version or later.
- Within the limit, the trade executes at the current price. A price
  exactly at the limit executes. The response is unchanged in shape:
  `total` is what was actually paid or received.
- `409 quote_stale` leaves the route.
- The preview is unchanged.

The rule is one-sided. It refuses only a price that got worse for the
trader by more than they allowed. A price that moved in their favour, by any
amount, executes at the better price.

`min_proceeds` of `0` is a limit too: it accepts any price. That is not the
silent opt-out the superseded entry was worried about. The field is
required, so a client that accepts any price has to say so.

### A replay ignores the limit

A retry with the same idempotency key returns the stored trade, whatever
limit it carries. The replay comparison (`_matches`) leaves out the limit
for the reason it already leaves out `state_version`: a client that lost
its response re-previews, and resends the same trade with a fresh limit. A
trade that committed at 12.40, retried with a `max_cost` of 12.00, still
answers 12.40. The trade already happened.

### Two steps, because the frontend and the backend ship separately

The e2e workflow runs the real `TradingPanel` against every backend pull
request. A browser tab that loaded today's bundle sends `state_version` and
no limit until it reloads. A one-step change would break both. So the
contract changes the way this repository changes a column: add it as
optional, move the readers, then require it.

1. **Expand** (`640-perf-2-price-limits`, built). `max_cost` and
   `min_proceeds` are accepted and optional. A body with a limit is decided
   by the limit, and its `state_version` is not compared. A body with no
   limit is decided as before: `state_version` is required, compared for
   strict equality, and refused `409 quote_stale`. No client loses
   protection at any point.
2. **The clients move.** The trade panel sends limits (#644). The k6 script
   sends them too (#20).
3. **Contract** (`640-perf-2b-limits-required`, after #644 is released to
   `main`). The limit becomes required for its side, `state_version` stays
   optional, and `quote_stale` leaves the route and `core/errors.py`.

`docs/api/ledger-service.md` describes the final state. Its "Rolling out"
paragraph, and a `quote_stale` row in its errors table marked as lasting
until every client sends a limit, cover what the expand release still
accepts. The contract step deletes both.

### The tolerance is the client's

The trade panel's default is 1%, decided by Ernest on 2026-11-05. On a buy,
`max_cost` is the quoted total times 1.01, rounded up to the tick. On a
sell, `min_proceeds` is the quoted total times 0.99, rounded down. The panel
shows it on the confirm line. On a fresh market at the default liquidity
(b = 100), 1% absorbs about two other one-share trades landing first. The
k6 bots use a fixed 5%, because they are not traders.

The ledger holds no tolerance setting.

## Consequences

- A trade can now cost more than its preview showed, up to the trader's own
  limit. `total` is what the trade cost at execution, not a copy of the
  preview's number.
- A refusal now means the price really got worse by more than the trader
  allowed. A trade that only followed another one executes.
- #644 changes the trade panel: it sends the limit, shows it ("Pay at most
  12.46", "Receive at least 7.81"), and on `price_beyond_limit` re-previews
  and asks the trader to confirm again. That is what it does for
  `quote_stale` today, at `TradingPanel.tsx:324`. `postTrade`'s
  `TradeRequest` type changes.
- #20's `loadtest/trade.js` sends a limit computed from the snapshot price
  at 5%, and counts `price_beyond_limit` where it counted `quote_stale`. #640's
  production runs use that script, so they measure the final rule whichever
  step has shipped.
- Until the contract step ships, the route runs two rules: one for a body
  with a limit and one for a body without. The contract step deletes the
  second, and `QuoteStale` with it.
- After the contract step, a client that sends no limit gets `422`. A tab
  still open on a bundle from before #644 is that client, and it has to
  reload.

## Alternatives rejected

- **A tolerance on the server, in versions or ticks.** For example, accept a
  quote up to N versions old. The superseded entry already rejected this,
  for ADR 0017's reason: the number becomes a correctness setting nobody
  can derive. It is also the wrong unit. A version count does not say how
  far the price moved, and how much worse a price a trader accepts is that
  trader's call, not the ledger's. A limit is a tolerance in credits, and
  the client supplies it.
- **Keeping strict equality.** Run A is the evidence against it: about 35
  refusals per committed trade, with both servers at 100% CPU.
- **Keeping both rules for good**, the version check as a second gate
  beside the limit. Two gates for one decision. The version gate would
  refuse trades the limit accepts, which are exactly Run A's refusals, and
  a client would have to handle two refusals that ask for the same retry.
- **One step**, the backend requiring limits in the same release as the
  frontend change. It breaks the e2e suite on every backend pull request
  until the frontend lands, and every open tab on the old bundle at
  release.
- **Reusing `quote_stale` for the new refusal.** Its meaning changed, and
  so do its details: `limit` and `current_total`, not `quoted` and
  `current`.
- **Removing `state_version` from the body.** The trade panel and the k6
  script already send it. Removing it is a second contract change, and
  gains nothing.
