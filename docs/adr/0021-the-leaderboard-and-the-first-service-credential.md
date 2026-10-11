# ADR 0021: The leaderboard is a snapshot on the ledger, and auth names its users through the first service credential

- **Status:** Accepted. Ernest agreed to the lookup route, its key and its settings on 2026-10-11.
- **Date:** 2026-10-11
- **Affects:** [L-1] #38, [L-2] #39, [L-3] #40, [FE][L-1] #58, [4.2] #14, [BE] #221, [T-7] #27, ADR 0002, ADR 0003, ADR 0009, ADR 0010, ADR 0011, ADR 0015, ADR 0018, ADR 0019
- **Implemented in:** nothing yet. This record precedes [L-1] #38, which is the first code that has to obey it.

## Context

[L-1] #38 asks for a ranked list of every eligible user, showing a username
and a net worth. Two services hold the data, and no service holds both:

- **Net worth is the ledger's.** ADR 0018 defines it as balance plus
  liquidation value. Its 2026-10-10 amendment says #38 must reuse the
  portfolio's valuation, settled branch included, and not call
  `liquidation_values_of` on its own. Every input is in the `ledger` schema:
  entries, positions, `q`, `b` and `market_results`.
- **The username and the suspension flag are auth's.** `auth.users` holds both,
  and nothing else may read that table. The ledger knows a user only by the
  `sub` of a token it has been shown.

A token does not solve this. For the trade route ADR 0009's amendment could
rely on the caller's own token, because the trade only concerns the caller.
Settlement could rely on an administrator's token (ADR 0019), because a person
asks for it. A leaderboard concerns every user, and the caller's token names
one of them. Ranking also cannot happen per request: ADR 0018's consequences
put the cost at one engine evaluation per held position, for every ranked
user. [L-3] #40 allows "a documented refresh interval". So the work is a
background job, and a background job has nobody's token. That is the "caller
with nobody behind it" that DECISIONS.md's open entry "Service-to-service auth
for a caller with no token" said would make a real service credential a
prerequisite.

## Decision

### 1. The leaderboard lives on the ledger, as a snapshot it refreshes itself

The ledger computes the leaderboard, stores it, and serves it at
`GET /ledger/leaderboard`. Every input to net worth is the ledger's, so this
follows the rule CLAUDE.md gives for the realtime service: anything a feature
appears to need from the database belongs in the service that owns that data.

The leaderboard is a sequence of **generations**. A generation is one
`ledger.leaderboard_generations` row (`id`, `computed_at`, `ranked_users`) and its
`ledger.leaderboard_entries` rows (`rank`, `user_id`, `username`,
`net_worth`). A background loop in the ledger writes a new generation every
`LEADERBOARD_REFRESH_SECONDS` (60 by default). Readers are served the newest
generation, and a whole generation commits in one transaction. A reader
therefore sees the old ranking or the new one, never a mix, and nothing about
a refresh touches a trade's tables or locks.

The rules for eligibility, suspension, ties, paging and staleness are below
the ADR bar, and DECISIONS.md records each one. In short:

- Every ledger `USER` account holder is ranked, administrators included.
- A suspended account is left out, and the ranks close up with no gap.
- Ranks run 1..N, ordered by `net_worth` descending, then `lower(username)`
  ascending.
- A cursor names its generation. The previous generation is kept for paging,
  and an older cursor is `409 leaderboard_refreshed`.
- `stale` is derived when a request is read, never stored.

### 2. Auth answers one lookup route, and only to a key the ledger holds

Each refresh asks auth for the username and suspension flag of every account
holder it has valued:

```
POST /internal/users/lookup
X-Service-Key: <LEDGER_SERVICE_KEY>

{ "user_ids": ["5f3e...", "..."] }   // 1 to 500 ids; the ledger sends more in chunks of 500
→ 200 { "users": [ { "id": "5f3e...", "username": "michelle_l", "is_suspended": false } ] }
```

- **It is the first credential in this system that belongs to a service
  rather than a person.** ADR 0002's "bearer tokens for services" was only
  ever a person's token carried by a service, and its "one verification path,
  not two" is no longer true for auth: this key is a second path. ADR 0002
  and ADR 0003 carry an amendment saying so. The key is the narrowest version
  available.
- **One key, one caller, one route.** Auth reads `LEDGER_SERVICE_KEY` from its
  settings, and the ledger reads the same variable to send. The variable is
  required in both and has no default, for the reason CLAUDE.md gives for
  `JWT_SECRET`: a default key is a published key. Settings refuse a value
  shorter than 32 characters. Auth compares the key with
  `hmac.compare_digest`. The dependency that checks it guards
  `/internal/users/lookup` and nothing else. A user's token does not open the
  route, and the key opens no other route.
- **The answer is three fields.** It returns `id`, `username` and
  `is_suspended`, and never the email, the role or `created_at`. #38 forbids
  exposing private account information. The ledger stores only the username,
  and only in the generation's rows.
- **Auth still does not know that credits exist.** The request carries user
  ids, the response carries no amount, and auth never calls the ledger.
- **It is not audited.** It is a read, and auth's searches are not audited
  either ("Searching is not audited" in `docs/api/auth-service.md`).
- **The call holds no database connection.** The refresh reads and values the
  holders in one transaction, ends it, then calls auth. It opens a second
  transaction only to write. This is the shape of "The cold path holds no
  connection across the terms pull".

**What this does not decide.** The key is not a general service identity.
DECISIONS.md's open entry stays open for [T-7] #27's auto-execution and for
any other caller with nobody behind it. A second caller gets its own key, its
own variable and its own record. This ADR does not set the pattern for
service calls in general.

## Consequences

- **A new secret, in two services.** `.env.example` gains `LEDGER_SERVICE_KEY=`
  blank, and compose passes it to `auth` and `ledger` as
  `${LEDGER_SERVICE_KEY:?}`. Rotation means restarting both. While the two
  disagree, every refresh fails `401`, the board goes stale, and trading is
  unaffected.
- **CI needs the key too, and that change is Ernest's (infra).** Compose's
  `${LEDGER_SERVICE_KEY:?}` refuses to start the stack while the key is
  unset. `.github/workflows/e2e.yml` writes its throwaway `.env` by filling
  every blank line in `.env.example` with a random 64-character value, so the
  blank `LEDGER_SERVICE_KEY=` line is all the e2e job needs. Auth's and the
  ledger's settings will also require the key, and
  `.github/workflows/ci-backend.yml` generates `JWT_SECRET` in a step of its
  own. That workflow's test and boot steps need a `LEDGER_SERVICE_KEY` step
  beside it.
- **An auth outage costs freshness, not correctness.** A refresh that cannot
  name its holders writes nothing. The previous generation stays, and reads
  report `stale: true` once it is old enough. [L-3] #40 asks for exactly
  this.
- **Usernames and suspension are current within one interval.** Both are
  looked up again on every refresh. A rename route, if one is ever added,
  shows up on the next refresh with nothing extra to build. A suspension
  written by [4.2] #14 takes the account off the board on the next refresh,
  and lifting it puts the account back.
- **The ledger now stores usernames.** It holds them only as display data in
  a snapshot. Each refresh replaces them, and they are deleted two generations
  later. Nothing in the ledger reads them to decide anything. ADR 0003's rule
  that a service authorises from the token, never from a user table, still
  holds.
- **The valuation moves out of the portfolio's read.** Both callers need the
  pure part: grouping rows by market, the settled branch, the liquidation
  branch, and the sums. It becomes one function, and the query's columns
  and joins are shared too (`held_outcomes_query`). The portfolio keeps its
  one-statement read of balance and positions together. The refresh gets a
  second statement with the same shape for every account holder. The move is
  its own refactor PR ahead of #38's service PR.
- **[BE] #221 reaches the leaderboard through the portfolio.** A voided
  market's rows need a branch in the portfolio's valuation (DECISIONS.md, "A
  settled row's payout is its quantity", Notes). Because the refresh reuses
  that valuation, the void needs no separate leaderboard change.

## Alternatives rejected

**Copying the username from the token at grant time. This was the fallback
had the route been declined.** `ensure_granted` already runs on the user's own token,
and that token carries `username`. A `username` column on `ledger.accounts`,
written at grant, would name every account holder with no new credential. It
works today, because no route renames a user. It has three costs:

1. Accounts granted before the column exists have no name. Filling them needs
   a write on some later authenticated read: a second write-on-read beside the
   grant.
2. A rename route, if one is ever added, never reaches the board.
3. Suspension has no source at all. The rule would become "deferred to
   [4.2] #14", and #14 would then need a channel from auth to the ledger,
   which brings back the question this record answers.

So the fallback postpones the credential and does not avoid it. Ernest
accepted the route on 2026-10-11, so the fallback is not built.

**A cross-schema `SELECT` grant on `auth.users (id, username, is_suspended)`
to `ledger_svc`.** CLAUDE.md: "Do not add a grant to make something
convenient; that is a decision to couple two services." The audit grant is
the one exception, and ADR 0006 argues it is a different kind of thing. A
column-level grant would also be the first grant that lets one service read
another's rows, so "a join across a service boundary fails" would stop being
true for the ledger.

**A service JWT signed with `JWT_SECRET`, carrying a `service` role.** Five
services hold `JWT_SECRET`: auth, market, ledger, audit and realtime. Each
reads it through `shared.config.ServiceSettings`, and compose passes it to
each of those five. The three migrate containers do not hold it. Any of the
five could mint such a token. Every verifier
on every route would then have to refuse the new role, and a route that
forgot would let a service token in. A separate header checked by one
dependency on one route cannot leak that way.

**The leaderboard somewhere else.**

- *Auth* does not know that credits exist, and tests enforce it.
- *market_service* holds no positions.
- *The realtime service* must not grow a database (ADR 0010).
- *The trading composite* cannot read `q` (ADR 0005, and CLAUDE.md on the
  engine's single caller).

Each of those would have to ask the ledger for every user's net worth, which
is the same problem with one more hop.

**The frontend joins usernames.** This needs a public route mapping an id to
a username, which anyone could enumerate. Suspension would then be filtered
in the browser, so a suspended user would still reach every client. Paging
by rank also breaks if the client drops rows.

**Auth pushes changes to the ledger.** Auth knows the ledger exists either
way, since it holds `LEDGER_SERVICE_KEY`. What differs is the direction:
pushing needs a credential the ledger checks, a call from inside every
transaction that renames or suspends a user, and an outbox so a change made
during a ledger outage is not lost. That costs more than the problem it
solves.

**Ranking on every read.** It costs the full valuation of every user per
request, as ADR 0018's consequences predicted. Two pages read a second apart
would also come from two different rankings.

---

> **This reverses when a second service needs a credential of its own.**
> [T-7] #27's auto-execution is the likely first. The choice then is between
> one key per caller with a registry, and a signed service token whose
> verifier is shared. Either supersedes section 2 of this record. Section 1
> stays.
>
> **It is revisited when auth and the ledger stop sharing a private network.**
> The key travels in plain HTTP inside compose. Across hosts it needs TLS.
>
> **Section 1 is revisited when a refresh stops fitting in its interval**,
> measured, not guessed. The answer then is an incremental refresh. It is not
> a live ranking.
>
> What does **not** reverse it: a wish for the board to move on every trade.
> [L-3] #40 accepts an interval, and 60 seconds is that interval.
