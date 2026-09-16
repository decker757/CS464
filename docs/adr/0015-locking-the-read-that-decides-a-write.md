# ADR 0015: Every read that decides a write is locked, and the wider lock goes first

- **Status:** Accepted
- **Date:** 2026-09-16
- **Affects:** [1.3] #3, [1.4] #4, [A-3] #31, [4.4] #16, [F-1] #41, [T-2] #22, [4.3] #15, PR #85
- **Implemented in:** `backend/market_service/service/market_service.py` (`_find_by_draft_key`, `_terms_snapshot`), `backend/auth_service/service/auth_service.py` (`_load_refresh`), `backend/auth_service/service/user_admin.py` (`change_role`), `backend/ledger_service/service/posting.py` (`post`)

## Context

ADR 0008 locked the row for `publish`, because an administrator double-clicking
the button sent two requests and both reported having published the same
market. ADR 0013 did the same for `propose_outcome` and ADR 0014 for
`close_early`. Each of those is a one-way transition, and "it must still be in
state X" was the tell that a check-then-act needed the row held between the
check and the act. That rule was written down, applied to the three
transitions, and believed to be finished.

A review of `dev` against `main` found four more, in three services, none of
them a transition:

**A resubmission racing a publish.** `publish` held the market row and still
lost, because a lock only queues *other lockers*. The save path read the
status without one, saw SUBMITTED, passed the frozen-state check, and went on
to replace the outcomes. Its INSERTs took the foreign key's `KEY SHARE` on the
market row, which does conflict with `FOR UPDATE`, so it waited — for the
publication to commit — and then carried on: new terms under a market traders
were already pricing, and SUBMITTED written back over OPEN, while the
`market.published` entry in the log swore to the old terms.

**A refresh token consumed twice.** `rotate_refresh_token` read `revoked_at`,
found it null, and revoked the token. Two requests presenting the same token
— the legitimate client and whoever copied its cookie — both read null, both
left with a fresh session, and the replay rule that kills every session for
that user never fired, because each request's write landed after the other's
check.

**Two promotions of one trader.** `change_role` locked the administrator rows
on a demotion and nothing on a promotion, because a promotion cannot empty the
set. Two administrators promoting the same trader both read TRADER, both wrote
ADMIN, and both appended a `trader → admin` — one transition attributed to two
people, in a log whose purpose is to say who did what.

**A retry racing its own original.** `posting.post` looked the idempotency key
up before taking the account locks, so a client resending a trade whose
response was lost — while the original was still in flight — found nothing,
queued at the lock behind the original, and then judged its overdraft against
the balance the original had just spent. A trade that had succeeded was
answered `InsufficientFunds`.

One shape, four times: a read that decides whether or what to write, made
without the lock, so that under READ COMMITTED two transactions both read the
old state and both decide yes. The one-way transitions were the visible cases.
These are the rest of the same class, and they are the reason the rule is
being restated here as a rule about *reads* rather than about transitions.

## Decision

### The lock is on the reader, not on the transition

Any read whose answer decides a write to the same row is `with_for_update()`.
That is the whole rule, and the four sites above are its first application
outside a transition:

- `market_service._find_by_draft_key` — every save, so `_refuse_if_frozen`
  decides from a row nobody else is changing. The loser of a save-versus-publish
  race now blocks on the SELECT, re-reads OPEN, and is refused exactly as a
  second publish is.
- `auth_service._load_refresh` — both callers write `revoked_at` from what
  they read. The second of two concurrent refreshes re-reads the revocation
  the first wrote, and is treated as the replay it is.
- `user_admin.change_role` — the target's row, before its role is read. The
  second of two promotions finds ADMIN and takes the idempotent path: no write,
  no entry.
- `posting.post` — the account rows already were locked; the key lookup moves
  under them (next section).

Reads that feed no write stay unlocked: `get`, `list_for_creator`, every
browse, every balance and history read. A lock on a read that decides nothing
is contention for nothing.

The cost worth naming is the autosave. Every three-second tick now takes a row
lock it did not take before. It is one row, held for one short transaction,
and it is the same lock `publish`, `close_early` and `propose_outcome` already
take on that row. Two autosaves for one form now queue rather than interleave,
which is what last-write-wins meant in the first place.

### A check that must hold under the lock is issued after the lock statement

`posting.post` made its idempotency lookup before `accounts.lock` and its
overdraft check after, and the ordering was the bug: the lookup's answer could
change until the lock was held, so it was not a check at all for the one
caller that needed it — a retry racing its original. The lookup now follows the
lock. One lookup, not a pre-check plus a recheck: a lookup made before the lock
can only ever be stale, so keeping it would be keeping a read whose answer is
never trusted.

Two consequences inside that function. The replay path now commits, although
it wrote nothing, because the account locks are held until the transaction
ends and a replay should not hold them for the rest of the caller's request.
And the `IntegrityError` handler on the unique key now only ever catches a key
reused with legs that share no account — the lock queues any two callers whose
legs overlap on even one — which is a key reused for different money, and
`_replay` says so.

The general form: an idempotency key, a balance, a status — anything whose
value the lock exists to hold still — is read after the lock statement, not
merely before the write.

### Where a row lock sits beside a multi-row lock, the wider one goes first, on every path

`change_role` already locked the administrator set on a demotion. Adding the
target-row lock the promotion needed had an obvious shape — lock the target,
read its role, and take the set only if that read says demotion — and that
shape deadlocks: two administrators demoting each other each hold their own
target and each wait for the other's inside the set query. Postgres would
abort one with a deadlock error, and the route would answer 500 for a request
that was entirely valid.

So every request takes the set first and its target second, promotion
included, even though a promotion needs the set for nothing. Two requests can
then only ever wait on each other in one direction. The cost is that role
changes queue behind one another globally, and there are a handful of those a
week.

The rule this generalises to: when a path takes more than one lock, every path
takes them in the same order, and the widest goes first. `accounts.lock`
already does this within one statement — ascending by id — and this is the
same discipline one level up.

### A race test is evidence only once it fails reliably on the broken code

Every new race test in PR #85 was run with its lock removed and had to fail
before it counted. Two of the four did not, at first: the refresh test failed
two runs in five and the promotion test one in five, on code that was
definitely broken. Two sessions under `asyncio.gather` are not yet a race —
the second party is still opening its database connection while the first
runs to commit, and the interleaving the test exists to produce never happens.
A test like that passes CI on the bug and proves nothing.

The fix is two lines per party, before the call under test:

```python
await own.connection()   # connected before the start line
await barrier.wait()     # asyncio.Barrier(n), one per test
```

With it, all four fail five runs in five on the broken code, and the race-test
files for all three services were then soaked twenty runs each with no failure.
That pair of numbers — fails reliably without the fix, never with it — is what
a concurrency test has to show before it is believed, and it is now the
standard for any test in this repository that asserts something about two
transactions.

### The audit snapshot carries every term the submission rules require

`_terms_snapshot` had every field `problems_blocking_submission` insists on
except `resolution_criteria`, so a resubmission that changed only the
settlement rule produced two entries with identical snapshots: the old rule was
overwritten on the market and recorded nowhere. It is in the snapshot now.
`description` is deliberately still out — it is prose about the market, not a
rule that decides it, and nothing in validation requires it. The field list of
the snapshot is the field list of the submission rules; when one grows, so does
the other.

## Consequences

**Two refreshes from one browser in the same instant are now a replay.** The
contract [A-3] #31 stated — a revoked token presented again kills every session
for that user — was, before this change, only enforced sequentially. Two tabs
sharing a cookie jar that both fired a refresh at once used to both succeed by
accident of the race; they now produce one winner and one refusal, and the
refusal revokes the winner's new token too. That is the contract behaving as
written, and it is also a sharper edge for the frontend: the refresh call must
be single-flight per browser, one in flight at a time with the others waiting
on it. If that proves insufficient in practice, the answer is a short grace
window during which the just-rotated token still answers with the same new pair
— a common design, and a decision for a ticket that has the evidence, not for
this one.

**Every autosave takes a row lock.** Argued above; noted here so nobody goes
looking for the reason `FOR UPDATE` appears in a query that runs every three
seconds.

**Role changes serialise globally.** Argued above.

**A replayed ledger post commits.** `posting.post`'s replay path ends the
transaction rather than returning with locks held. Callers that do their own
work after a replay — none today — start a new transaction to do it in.

**Race tests have a shape.** Own session per party, `await session.connection()`
then a barrier before the call, and a run with the lock removed that fails.
The existing race tests predate the barrier and pass without it because their
parties do enough setup work to be connected by the time it matters; new ones
should not rely on that.

## Alternatives rejected

**Lock the target first in `change_role`, and the set only on a demotion.**
The minimal change, and the one that keeps the old docstring true. Rejected
because it deadlocks two mutual demotions, which is the exact case the set
lock exists for.

**Keep the pre-lock idempotency lookup and add a second one under the lock.**
The reviewer's suggestion, and it would have worked. Rejected because the first
lookup's answer is never trusted — it cannot be, that is the bug — so it is a
read that exists to be ignored. One lookup, after the lock.

**`SERIALIZABLE` isolation instead of row locks.** Solves every case above at
once, and every case not yet found. Rejected because it turns each of these
races into a serialisation failure the caller has to catch and retry, in every
route, forever — machinery this repository does not have and would have to
build correctly under asyncio. Row locks are targeted: they cost only the
callers that actually contend, and they fail by waiting rather than by error.

**An optimistic version column on `market.markets`.** Rejected because it is a
second thing to keep true, a hand-applied migration against the long-lived
database, and a 409 the autosave would have to handle every three seconds
whenever a publish landed between two ticks.

**A conditional `UPDATE ... WHERE revoked_at IS NULL` for the token.** Also
suggested, also correct for that one site. Rejected because it does not hand
back the row the rest of the function needs, and because four sites fixed with
one idiom are greppable and four fixed with three are not. `with_for_update()`
is the shape everywhere.

**Snapshotting `description` too.** Rejected above: prose, not a rule, and not
something the submission rules require. The line is drawn where validation
draws it, so it moves only when validation does.
