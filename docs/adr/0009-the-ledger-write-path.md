# ADR 0009: Double-entry with derived balances, and a lazily minted grant

- **Status:** Accepted
- **Date:** 2026-09-15
- **Affects:** [F-1] #41, [A-1] #29, [B-1] #32, [B-2] #33, [T-2] #22, [T-3] #23, [3.4] #12, [4.1] #13
- **Implemented in:** `backend/ledger_service/`

## Context

[F-1] #41 asks for an append-only double-entry ledger with derived balances, an
idempotency key on writes, and enough serialization that concurrent trades
cannot overdraw. Six stories are blocked on it and it has to land before
trading.

Three questions had to be settled to build it, and each has an obvious answer
that is wrong.

**Where do credits come from?** [B-1] #32 says every new account receives a
fixed starting grant, exactly once, atomically with account creation. The auth
service does not know that credits exist — there is a test that fails if the
word appears in a response — and there is no event bus between the two
services.

**What stops a balance being wrong under concurrency?** [T-2] #22's hardest
acceptance criterion is that N concurrent buys can never overdraw.

**What stops a retry executing twice?** Both #22 and #32 require it, from
opposite directions: a resent trade and a resubmitted registration.

## Decision

### Balances are derived, and there is no balance column

A balance is `SUM(amount)` over an account's entries, computed on every read.
`ledger.accounts` has no balance column and will not get one.

Two sources of truth for money is the failure this whole design exists to
avoid. The moment a column exists beside the entries, something has to
reconcile them, and the reconciliation is wrong in exactly the cases that
matter: a crash mid-write, a concurrent trade, a bug in one of the two paths.
[B-1] #32, [B-2] #33 and [4.1] #13 each independently specify that the
displayed balance equals the sum of the entries. Deriving it is how that
becomes true by construction rather than by vigilance.

The cost is an aggregate per read, over an index built for it. If that ever
bites, the answer is a materialised snapshot with a delta — which is a cache
with an invalidation story, and should be paid for when it is needed rather
than in advance.

### Entries are signed, and every transaction sums to zero

Negative is a debit, positive a credit. The alternative — a positive amount
beside a direction column — makes every balance query a CASE expression and
every balance check a place to get a sign wrong.

Signed means the balance is `SUM(amount)`, the invariant is `SUM(amount) = 0`,
and both are statements you hand to Postgres rather than arithmetic you write
down twice. `unit_test/service/test_concurrency.py` asserts both: the whole
ledger sums to zero, and so does every transaction on its own. The second is
not redundant — the first would also hold if two unrelated mistakes cancelled.

### Credits are minted by the platform account going negative

There is one `PLATFORM` account, and it is the only one allowed below zero. Its
balance is minus the credits in circulation.

This is what makes a starting grant a *movement* rather than an invention, and
it is what lets the sum-to-zero invariant hold over the entire table rather
than over some carefully chosen subset of it. Without it, the grant would be a
transaction with one leg, and the one assertion worth making about a ledger
would have to be qualified.

### The grant is minted here, lazily, on first read

A user with no entries is by definition a user who has not been granted yet. So
the first read of a balance or a history writes the genesis transaction, keyed
`signup-grant:<user_id>`, and carries on.

The alternatives were considered in `backend/auth_service/README.md` before any
of this was built:

- **A balance column topped up at signup.** Breaks derived balances, and puts
  credits back inside the auth service.
- **An outbox, or an event.** Real plumbing — a table, a relay, a consumer —
  and it leaves a window in which a new account's balance is observably wrong.
- **One transaction across both services.** Satisfies "atomically" literally,
  by deleting the boundary that makes these separate services.

Lazily minting has no plumbing and no wrong window, because the read that would
have observed the window is the read that closes it. The oddity is that a read
performs a write; that is normal for a welcome grant and costs one insert per
user, ever.

### Idempotency is a unique key plus a fingerprint of the legs

`transactions.idempotency_key` is unique across the ledger. A caller that
resends an identical request gets the original transaction back and nothing is
written.

The fingerprint — a hash of the legs — is what separates a *replay* from a
*reuse*. Without it, a caller that reuses a key for genuinely different money is
told their new movement succeeded and handed the old one as proof. That is the
classic idempotency-key bug, and in a ledger it means showing somebody a trade
they did not make. A reused key is refused; an identical one replays.

### Serialization is a row lock on the accounts, taken in id order

`SELECT ... FOR UPDATE` on every account a transaction names, ascending by id,
before any balance is read. Under READ COMMITTED the loser of a race blocks,
and when it proceeds it sees the entries the winner committed, so the balance
it checks against is the real one.

One statement per account rather than one `WHERE id IN (...) ORDER BY id FOR
UPDATE`. Postgres locks rows in the order the plan produces them, which an
ORDER BY in the same statement does not reliably constrain, so the
single-statement version can still deadlock two transactions taking the same
pair of locks in opposite orders.

This is why `ledger.accounts` exists at all. It holds no balance and almost no
data; it is identity, and a row to lock. Without it there would be nothing to
serialise on.

### Append-only is enforced by a trigger that ships with the table

A statement-level `BEFORE UPDATE OR DELETE OR TRUNCATE` trigger on
`ledger.entries`, the same shape as the one `sql/02-schemas.sql` puts on
`audit.admin_actions`, attached as an `after_create` DDL event in
`model/entities.py`.

It is attached to the table rather than written into `sql/` because these are
this service's own tables: `create_all` makes them and the suite rebuilds them
per test, so a trigger living in `sql/` would be dropped by the first rebuild
and never come back — and `test_append_only.py` would pass in CI while
production had no trigger, or the reverse.

**This is one step weaker than the audit log's and the difference is worth
stating.** `ledger_svc` owns `ledger.entries`, so it could drop its own trigger;
no writer can touch `audit.admin_actions` at all. What it still buys is the
audit log's actual argument: a guarantee somebody has to deliberately delete a
line to break is much harder to break by accident than one that was never
written down.

### There is no HTTP write endpoint yet

`service/posting.py` holds the write path. No route reaches it.

A write endpoint needs a caller, and the caller is ADR 0005's trading
composite, which does not exist. More importantly it needs an answer to a
question none of the existing services have had to ask: **how does a service
prove it is a service?** Every route in this repository authenticates a person
from a signed access token. A ledger write route that accepted a trader's own
token would be a route for minting yourself credits, and the trading service
forwarding its caller's token is the obvious implementation of exactly that.

So the primitive, its rules and its tests land here, and [T-2] #22 — the ticket
that has a caller — decides how that caller authenticates. The primitive is not
untested scaffolding: the starting grant goes through it, so the double entry,
the idempotency and the lock are exercised by shipped behaviour rather than
only by the suite.

> **Amended by [T-2] #22. The question is answered for the trade route, and
> the answer is that the route takes no legs.**
>
> The fear stated above is exact and it survives: a write endpoint that
> accepted a caller-supplied transaction, authenticated by a trader's own
> token, is a route for minting yourself credits. What #22 adds is not that
> endpoint. `POST /ledger/markets/{market_id}/trades` accepts a market, an
> outcome, a side, a quantity, a quoted `state_version` and an idempotency
> key. It accepts no account, no amount and no leg. The ledger builds the
> movement itself, from `q` and `b` read under the book row's lock and from
> the `sub` claim of the signed token — so the debited account is not an
> input, and the amount is not an input.
>
> Checked input by input, that leaves nothing a trader controls that can move
> money they should not:
>
> | Input | Why it cannot |
> | --- | --- |
> | `market_id`, `outcome_id` | Validated against this service's own book; credits move between the caller and *that* market's pool and nowhere else |
> | `side`, `quantity` | `> 0`, at most four decimal places, bounded above by what `Numeric(18, 4)` can store and below by one tick; the cost is computed under the lock and never read from the request |
> | `state_version` | Can only ever refuse a trade. A wrong value cannot make one cheaper, because it is compared and never priced from |
> | the debited account | Not an input at all — `accounts.ensure(USER, claims.sub)` |
> | `idempotency_key` | Namespaced by the server before it is stored — see below, because the naive version is a real hole |
>
> **The idempotency key is derived, not stored as sent.** `idempotency_key` is
> unique across the whole ledger and every namespace in use is generated by
> this service (`signup-grant:<user_id>`, `market-open:<market_id>`). A route
> that stored a client's string verbatim would let a trader claim
> `signup-grant:<somebody else's user id>` on a trade of their own, after
> which that user's first balance read finds the key already present, takes
> `ensure_granted`'s early return, and never receives their starting credits
> — silently, permanently, and in a ledger with no path that rewrites an
> entry. The same key would also replay somebody else's trade back to whoever
> guessed it. So the stored key is
> `trade:<user_id>:<market_id>:<client key>`, which confines a collision to
> one caller in one market, which is what an idempotency key is for. The
> entry "The trade's idempotency key is derived by the server; the client's
> value is one component of it" in `DECISIONS.md` carries the detail.
>
> **What stays open is the caller with no token.** This answer works because
> every trade has a person behind it whose token the ledger forwards to
> market_service under [ADR 0017](0017-the-ledger-and-a-stopped-market.md) and
> whose `sub` names the account being debited. It does not generalise:
>
> - **[T-7] #27's auto-execution and cascade** fills a resting order with no
>   request and no caller. It has no token to forward and no `sub` to debit
>   from, so it needs a real service credential and this amendment is not it.
>   [ADR 0017](0017-the-ledger-and-a-stopped-market.md)'s reversal note names
>   the same day from the status gate's side.
> - **[3.4] #12's settlement**, *if* it is ever a background job rather than
>   an administrator's request. As specified today it is an admin action with
>   an admin token, so it does not fire this; a scheduled sweep would.
>
> Neither is on this board now. When one arrives, the question this record
> deferred reopens in full and is a prerequisite of that ticket rather than a
> detail inside it.

## Consequences

**Every read of a balance can write.** One insert per user, ever, and it is the
only write on the read path. A monitoring rule that alerts on writes from a GET
will fire on it; that is correct and it should be documented rather than
suppressed.

**The starting grant cannot be changed retroactively.** `STARTING_CREDITS`
reaches accounts created after the change and no others, because the grant is a
transaction that has been written and there is no path that rewrites one. This
is the honest behaviour for an append-only ledger and it will surprise somebody
at least once.

**A balance read costs an aggregate.** Indexed, and fine at this scale. [3.4]
#12 settling 1,000 positions in under five seconds is the first thing that will
test it.

**Money crosses the wire as a decimal string, not a JSON number.** `MarketOut`
serialises `liquidity_b` as a float, deliberately, because the create form does
arithmetic with it. Balances are the opposite case: a JSON number is an IEEE
double by the time a browser has parsed it, and a credit total off by an
epsilon is a bug report about money.

**Positions are not here yet.** ADR 0005 puts them with the ledger and they
belong here, but [T-2] #22 is the ticket that writes them and gets to choose
their columns. `AccountKind` and `TransactionKind` are non-native enums, so the
members that ticket needs — `MARKET_POOL`, `TRADE_BUY`, `TRADE_SELL` — are
Python-only additions with no migration.

**This is the fourth service sharing one database, and `create_all` is now
visibly on borrowed time.** ADR 0006's atomicity argument still holds, and so
does every schema boundary, but four services issuing DDL at startup against
one Postgres is the shape of problem Alembic exists for. `sql/migrations/`,
`CLAUDE.md` and the root README all promised Alembic would arrive with this
ticket; it did not, and it has its own issue instead, because retrofitting four
services' startup and conftests is a change of its own size.

## Alternatives rejected

**A balance column, reconciled against the entries.** The fastest read
available, and the one every acceptance criterion on this board explicitly
argues against. Reconciliation is a second mechanism that is wrong precisely
when the first one is under stress.

**Positive amounts plus a direction column.** Closer to how double entry is
taught. It makes the two assertions this design leans on — a balance is a sum,
a transaction sums to zero — into expressions that have to be written
identically in several places.

**SERIALIZABLE isolation instead of row locks.** Postgres would detect the
conflict and abort one transaction, which is correct and needs no `accounts`
table. It also needs every caller to implement retry-on-serialization-failure,
which pushes the hard part out to callers that have not been written yet, and
it converts a short wait into a failed request under contention.

**Advisory locks keyed on the user id.** No table needed, and they do serialise.
They are also invisible: nothing in the schema says why two writers take turns,
and a future query that forgets the convention is not refused by anything.
A row lock on a row that has to exist anyway is self-documenting.
