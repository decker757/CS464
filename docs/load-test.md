# Load test of the trade endpoint

[5.4] #20. Backs the scaling claims in `docs/horizontal-scaling.md` and ADR
0026 with numbers: the p95 latency of `POST /ledger/markets/{id}/trades` with
50 traders at once.

## What it does

`loadtest/trade.js` is a [k6](https://grafana.com/docs/k6/) script, and
`loadtest/run.sh` runs it in k6's own Docker image, so nothing needs
installing.

- **50 traders, for 5 minutes.** Each one, once a second, reads the market's
  snapshot, buys one share of an outcome at that `state_version`, and sells it
  back.
- **All 50 trade on one market.** That is the hardest case for the ledger:
  every trade locks the same book row (ADR 0015), so trades on one market
  queue behind each other however many servers there are. More markets
  spread the load. One busy market cannot.
- **A refused quote is retried, as a person would.** When another trader has
  just moved the price, the trade is refused `409 quote_stale` with the
  current `state_version`. The trader retries at that version, up to five
  times. That is the book working, so it is counted (`quote_stale_retries`)
  and not as an error.
- **What it records:**
  - `http_req_duration{name:trade}`: every trade request's latency, the
    retries included. Its p95 is the number the ticket asks for.
  - `trade_errors`: the share of trades that ended in anything other than a
    commit.
  - `trades_committed`, `quote_stale_retries` and `trades_given_up`
    (five stale quotes in a row): counts.
  - `http_req_duration{name:snapshot}`: the price read before each trade.
- **Two thresholds,** so a run says pass or fail by itself:
  - a trade's p95 is under 1 s, the same line as the latency alert
    (`deploy/grafana/alerts.yaml`)
  - fewer than 1% of trades fail
- **It measures from the machine that runs it.** Against production, every
  number includes the trip from that machine to the load balancer and back.

**The traders are the same 50 accounts every run,** `loadtest_01` to
`loadtest_50`, sharing one password. Before each run the script lifts their
suspension and logs each one in. After it, it suspends them again, with a
reason in the audit log, because a suspended account is left off the
leaderboard (`docs/api/ledger-service.md`, "Who is ranked"). They are reused
because one address may register only 20 accounts an hour, and log in 30
times a minute (ADR 0027). So the first run registers them, and every login
is spaced 2.1 s apart. Setup takes about two minutes.

## Running it

**Before it:**
1. **Make a throwaway market** as an administrator, with two outcomes and a
   closing time after the run. Name it so nobody trades it by mistake
   ("Load test: ignore"). Publish it, and copy its id from the address bar.
   The test never touches any other market's prices.
2. **Put your admin password in a file,** without typing it on a command
   line: copy it, then run `pbpaste > ~/.cs464-loadtest-admin-password &&
   chmod 600 ~/.cs464-loadtest-admin-password`. Delete the file after the
   run. The traders' password is made for you on the first run, in
   `~/.cs464-loadtest-trader-password`. Keep that one: the accounts need it.

**The run:**

```bash
DOMAIN=<domain> MARKET_ID=<market id> ADMIN_IDENTIFIER=<your admin username> \
  loadtest/run.sh
```

Against a local stack, give the two services' addresses instead of `DOMAIN`.
For example, for the scaled stack (`docs/horizontal-scaling.md`):
`AUTH_URL=http://host.docker.internal:18000 LEDGER_URL=http://host.docker.internal:18003`.
A local stack needs an admin and a market first. Promote yourself the way the
root README says ("Creating a market needs an administrator"), adding
`-p cs464scale -f docker-compose.yml -f docker-compose.scale.yml` to its
`docker compose` for the scaled stack.

`USERS=10 DURATION=30s` makes a quick trial run.

The summary is printed and saved in `loadtest/results/`, which git ignores.
Copy the numbers into the table below.

**If you stop it with Ctrl-C once,** k6 still suspends the accounts. Twice
skips that, and the next run lifts them anyway.

**After it:** close the throwaway market early, then void it from its admin
page. Voiding refunds every trade at cost (ADR 0025), so the 50 accounts end
where they started.

## Results

| When | Where | Traders, for | Trades committed | Trade p95 | Trade p99 | Errors | Stale quotes retried | Trades given up |
|---|---|---|---|---|---|---|---|---|
| 2026-11-04 | a laptop, the scaled stack (two of each service) | 10, 30 s | 469 (14 a second) | 78 ms | 210 ms | 0% | 609 | 41 |
| 2026-11-04 | a laptop, the scaled stack (two of each service) | 50, 1 min | 1,137 (15 a second) | 258 ms | 431 ms | 0% | 9,603 | 1,346 |

**What the numbers say.**
- **Latency holds.** With 50 traders the p95 is a quarter of the alert's
  line, and no trade failed with an error.
- **One market's throughput does not grow with traders.** It was about 15
  trades a second with 10 traders and with 50. With 50, more than half the
  attempts gave up after five stale quotes.
- **That is two rules doing their jobs.**
  - Every trade moves the price, so it makes every other trader's quote stale.
    A trade must name the exact `state_version` it was quoted at ([T-3] #23),
    so each of those traders has to quote again.
  - Trades on one market commit one at a time, because each locks the same
    book row (ADR 0015).
- **So a second server adds capacity across many markets, and none for one
  busy market.**
