# ADR 0022: The ledger asks auth whether a trader is still active, once per trade

- **Status:** Accepted
- **Date:** 2026-10-11
- **Affects:** [4.2] #14, [FE][4.2] #60, [T-2] #22, [T-3] #23, [5.4] #20, [T-7] #27, [L-1] #38, ADR 0002, ADR 0003, ADR 0009, ADR 0015, ADR 0017, ADR 0021
- **Implemented in:** nothing yet. This record precedes [4.2] #14's ledger PR, which is the first code that has to obey it.

## Context

[4.2] #14's acceptance criteria say "suspension blocks login and trading,
existing positions retained". The auth service can block login on its own: it
owns `auth.users.is_suspended`, it refuses a suspended account at login and at
refresh, and #14 revokes every refresh token the account holds. Nothing it does
reaches a trade.

**The ledger authorises from the signed token alone.** ADR 0003 put authority
in the access token so that no other service needs to read `auth.users`, and
ADR 0002 accepted the cost: a token stays valid until it expires, 15 minutes at
most. `ledger_service/controller/dependencies.py` says it in one line: "a
suspended user keeps it for one access-token lifetime". So without anything
new, a trader suspended at 10:00 holding a token issued at 9:58 can keep buying
and selling until 10:13.

ADR 0003 named that window for market_service and said "#14 should note it".
For market_service's routes it stays accepted. For the trade route it is not:
"blocks trading" is an acceptance criterion, and a quarter of an hour of
trading after the suspension is the opposite of it.

## Decision

### The trade path calls `GET /auth/me` with the trader's own token

One call, on every trade that is not a replay, forwarding the caller's access
token unchanged as a bearer header — ADR 0017's pattern exactly, pointed at a
different service. `/auth/me` reads the user's row through
`get_current_user`, which refuses a suspended account with
`403 account_suspended`. So:

| auth answers | the ledger raises | the trade route answers |
| --- | --- | --- |
| `200` | nothing | the trade continues |
| `403` with code `account_suspended` | `AccountSuspended` | `403 account_suspended`, auth's own code and message |
| `401` | `NotAuthenticated` | `401 invalid_token` |
| a connect error, a timeout, a `5xx`, any other status, or a `403` with any other code | `AuthUnavailable` | `503 auth_unavailable` |

The `200` body is not read. auth answers `200` only after `get_current_user`
has passed, and that is the whole question.

**It fails closed.** ADR 0017's rule for market_service: a sick dependency is
never read as a yes. A `503` is worth retrying in a moment; a trade waved
through on an auth service that did not answer is a trade a suspended account
may have made.

**`auth_unavailable`, not `market_terms_unavailable`.** The remedy is the same,
but the name would lie about which service is down.

### Its place in ADR 0017's order: after the replay, before the market gate

1. The idempotency lookup, unlocked. A hit replays and returns.
2. **This check.**
3. ADR 0017's market gate.
4. `books.ensure_open`, `grants.ensure_granted`, the book lock and everything
   after it, unchanged.

**After the replay**, for ADR 0017's own reason: a trade that committed before
the suspension, whose response was lost, still answers with what it did. It
writes nothing and asks nobody.

**Before the gate**, so a suspended trader on a closed market is told about
themselves, which is the thing they cannot fix by picking another market. The
two HTTP calls run one after the other, never together, so that order is fixed.

**Before `books.ensure_open` and `grants.ensure_granted`**, so a suspended
account's attempt cannot open and fund a cold market's pool or mint its own
starting grant.

**Before the book lock, never under it.** ADR 0017's convoy argument applies
word for word: no lock in the ledger holds anything in auth's database still,
so taking the call under the lock closes no window and serialises a market's
trades behind a remote round trip. Like the gate, the check rolls back the
transaction the replay lookup opened before it calls out, so a slow auth
service holds no pooled connection (D-043).

### The trader's token, and not ADR 0021's service key

ADR 0021 gives the ledger a key for `POST /internal/users/lookup`, which also
returns `is_suspended`. It is not used here, for four reasons:

1. **A trade always has a person behind it** (ADR 0009's amendment), and the
   question is about that person. Their own token is the credential that
   answers it. ADR 0021's key exists for a caller with nobody behind it; using
   it where a person's token is in hand would give the trade path a credential
   that can ask about anybody to answer a question about one person.
2. **ADR 0021 says one key, one caller, one route**, and names the
   leaderboard's refresh as that caller.
3. **It would make key rotation stop trading.** ADR 0021 says that while the
   two copies of the key disagree, the board goes stale and trading is
   unaffected. That stays true only while the trade path does not use the key.
4. `/auth/me` exists today.

The price is that auth checks the token's signature a second time and reads
one row. `get_current_user` loads that row with `raiseload` on its refresh
tokens, so the per-trade cost does not grow with the account's history.

### The window between auth's answer and the commit is accepted

A suspension can commit after auth answered "active" and before the trade
commits: half a round trip, the market gate, the book lock and the write.
Milliseconds, the same shape as ADR 0017's window between market_service's
answer and the commit, accepted for the same reason: no lock in the ledger can
hold a row in another service's database, so nothing makes it zero short of a
distributed transaction.

### What this does not cover

Only the trade route asks. Reads decide nothing: the preview, the snapshot,
the balance, the history and the portfolio keep working on the token until it
expires, and so does the live price socket. A suspended administrator's token
still works on market_service's admin routes and on the ledger's settlement
route for the same window: ADR 0003's accepted window, which this record does
not narrow. `takes_effect_within_seconds` on the suspension response stays the
access-token lifetime for that reason.

## Consequences

**auth is now a runtime dependency of every trade, beside market_service.** An
auth outage already stopped logins at once and logged everybody out within 15
minutes, as their access tokens expired. This moves "trading stops" from
"within 15 minutes" to "immediately". [5.4] #20's load test now measures auth
too, whether or not that was its subject.

**One HTTP client for auth in the ledger**, held for the process like the
terms client, with the same explicit five-second timeouts and a setting
`auth_service_url` that defaults to compose's hostname. ADR 0021's leaderboard
refresh needs the same client; whichever of #14 and [L-1] #38 lands first builds
it, and the other reuses it.

**No change to compose.** The ledger has no `depends_on` for market_service
either. An auth service that is not up yet makes a trade fail closed with
`503`, which is the right answer, not a reason to stop the ledger starting.

**No other ADR is amended.** ADR 0003's "Suspension does not reach this
service" is about market_service and stays true. ADR 0017 says market_service
is a dependency of every trade; it does not say it is the only one. ADR 0021's
"trading is unaffected" is about its key's two copies disagreeing, and stays
true because the trade path does not use the key.

## Alternatives rejected

**Accept the 15-minute window**, ADR 0003's default. It fails the acceptance
criterion for trading, the only half of the window that moves money.

**Shorter access tokens for everybody.** Shrinks the window without closing it,
and costs every user a refresh every few minutes. It would amend ADR 0002 to
fix one route.

**A `jti` denylist, or a set of suspended ids the other services read.** ADR
0002 names a denylist as the general fix, but it needs a store every service
can read. Redis may not decide anything about money (ADR 0010), and a shared
table is a cross-schema grant, which CLAUDE.md and ADR 0021 both refuse. The
per-trade call gets the half that matters without either.

**auth pushes the suspension to the ledger.** A dual write with no outbox (ADR
0006, and ADR 0017's first rejected alternative), and auth would have to know
the ledger exists.

**ADR 0021's lookup route.** See above.

---

> **This reverses with ADR 0017, on the same two triggers.** If [5.4] #20 shows
> the two calls are a material share of the trade endpoint's budget, measure
> this one separately: the cheap answer for auth is not a cache, which would
> make the TTL a correctness parameter, but a shorter path to the same row.
> And on the day a trade arrives with no caller token — [T-7] #27's
> auto-execution is the one on the board — this decision is unimplementable as
> written, exactly as ADR 0017's is, and the service credential that day needs
> answers both questions.
