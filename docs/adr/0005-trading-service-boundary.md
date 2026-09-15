# ADR 0005: A composite trading service, and positions with the ledger

- **Status:** Accepted
- **Date:** 2026-09-15
- **Affects:** [F-1] #41, [F-3] #43, [F-2] #42, [T-1] #21 through [T-8] #28, [2.2] #6, [X-3] #36, [5.4] #20, [1.2] #2, [1.3] #3
- **Implemented in:** nothing yet. This record precedes [F-1] #41 and [F-3] #43, which are the first code that has to obey it.

## Context

[1.2] #2 asked a small question — where does a liquidity parameter live — and
turned up the fact that nothing had ever decided where trading lives. Three
things were in conflict.

**The repository contradicted itself about the pricing engine.** `CLAUDE.md`
called it a service. `sql/01-roles.sql` reserved no role for it, while
reserving `ledger_svc` for [F-1] #41 a sprint ahead of need. Someone had
thought about which of these are services, and the prose and the schema
disagreed about the answer.

**[T-2] #22 requires something the schema boundary forbids.** Its acceptance
criteria say ledger rows are written "in the same DB transaction as position
updates". Ledger rows live in `ledger.*`, owned by `ledger_svc`. If positions
live in `market.*`, one transaction spanning both needs a role holding grants
on both schemas, and `sql/02-schemas.sql` exists to refuse exactly that. Both
rules were written down; they cannot both hold.

**market_service is entirely administrative and the trading epic is entirely
not.** [1.1] #1 through [1.4] #4 and [2.1] #5 through [2.3] #7 are admin
stories. [T-1] #21 through [T-8] #28 are trader stories. Nothing said whether
that is one service or two, and the trading epic is the largest block of
unstarted work on the board.

## Decision

**Trading is a composite service that owns no data.**
`backend/trading_service/` orchestrates: it reads market terms, evaluates the
LMSR cost function, and issues one transactional write to the ledger. It gets
no role in `sql/01-roles.sql` and no schema in `sql/02-schemas.sql`, so it
costs a Dockerfile, a compose entry and a CI job — not a `docker compose
down -v` for everybody.

**Positions live with the ledger.** `q`, the shares outstanding per outcome, is
`ledger.*` state, written in the same transaction as the debit and credit rows
it has to balance against. This is what makes [T-2] #22's acceptance criteria
implementable without either a cross-schema grant or a saga.

**market_service keeps definition and lifecycle, and nothing else.** The
question, outcomes, status, `b`, the seed subsidy, the resolution criteria and
sources. It never learns `q`, and it never executes a trade.

**`b` and the subsidy cross once, at publish, as an immutable snapshot.** [1.4]
#4 already forbids editing a published market's terms. A copy of data that
cannot change cannot drift from its source, so this is duplication without
coupling — unlike a live read, which would put market_service in the hot path
of every quote.

**The LMSR engine is a module, not a service.** `C(q) = b·ln(Σ e^(q_i/b))` is a
pure function of `b` and `q`. It owns no state, so there is no table for it to
own, no role for it to be, and nothing for `sql/02-schemas.sql` to enforce
about it. It lives wherever `q` lives.

## Why split trading out of market_service

**The naming argument from ADR 0003, applied to its author.** That record
rejected putting markets in the auth service because "the moment `auth` owns a
markets table, 'auth' stops meaning anything and becomes the service where
things go." A market service that also executes trades, holds positions and
prices an order book is the same failure with a different noun.

**The hot path cannot be scaled while welded to admin CRUD.** [5.4] #20 load
tests the trade endpoint specifically. Market creation runs a handful of times
a day by three administrators; [T-1] #21's cost preview fires while a trader is
typing a quantity. Those belong in separately scalable processes.

**Different change cadence and different blast radius.** Market administration
is finished, in the sense that [1.1] through [1.4] close out an epic. Trading
is eight issues of concurrency work that will be redeployed constantly. A bad
trading deploy should not take the admin console down with it.

## Why not for debuggability

This was considered as a reason and rejected, recorded here so it is not
re-derived as a benefit later.

The argument was that separate services localise a failure: a broken trade
points at one service rather than leaving you to find which module of a larger
one misbehaved. It is backwards. Inside one service a failure is a Python
traceback naming a file and a line. Across two it is a 500 in one container's
log and a caller-side error in another's, correlated by eye against timestamps
— unless correlation IDs, trace propagation and aggregated logs exist, and none
of those are in this repository or on the board.

The isolation that argument wants is already enforced at module level:
`controller` uses `service`, `service` uses `core` and `model`, nothing below
reaches up. A trading module inside market_service could not reach into
drafting internals any more than a separate service could, and it would produce
a better stack trace when it broke.

Distributed systems are harder to debug. That is a cost of this decision, paid
for the three reasons above.

## Why the admin/trader line is not the seam

Splitting by who calls a service rather than by what state it owns cuts the
data in both directions:

| Story | Needs market terms | Needs `q` |
| --- | --- | --- |
| [2.2] #6 market exposure (admin) | seeded subsidy | shares outstanding |
| [X-3] #36 details and price (trader) | question, outcomes, close time | current price |
| [T-1] #21 cost preview (trader) | `b` | `q`, and a state version |
| [1.4] #4 restrict edits (admin) | status | — |

Admin stories need trade data and trader stories need market terms. A boundary
drawn on the caller would put both sides of half these queries on the wrong
side of it. The seam that holds is slow-changing definition against
fast-changing money, which is market_service against the ledger, with trading
composing the two.

## Consequences

**The composite must stay stateless, or this decision gets more expensive.**
The thing most likely to break that is [T-2] #22's idempotency key, which has
to be stored somewhere durable. It belongs in the same transaction as the
ledger write it guards, not in the composite. The moment the composite needs a
table it needs a role, a schema and a volume wipe, and the cost argument above
collapses.

**Two queries now have no single service that can answer them.** [2.2] #6 needs
shares outstanding from the ledger and the seeded subsidy from market_service;
[X-3] #36 needs market terms and a current price. Both are read-only
aggregates, so composition is cheap, but each needs a decision about who
composes — the trading service growing a read endpoint, or the frontend issuing
two calls. Decide it in those tickets, not here.

**One more network hop in the hot path.** Every cost preview becomes composite
to ledger. This is the reason `b` is snapshotted at publish rather than fetched:
one hop per quote is a budget, two is a latency problem in an interaction that
fires on every keystroke.

**ADR 0003's deferral has expired.** It declined to extract `backend/shared/` at
two occurrences and said to "revisit when the ledger ([F-1] #41) lands and makes
it three." This makes four: auth, market, ledger, trading. It also adds a second
kind of sharing, because the LMSR engine is needed by the composite and by the
ledger if the ledger validates a trade rather than trusting what it is told.

The engine is the shape 0003 said would justify extraction. That record refused
to share `core` because the duplication was asymmetric — auth signs and hashes
where market only verifies, so a shared module would have carried an encode
path into a service that must not have one. The engine has no such asymmetry.
Every caller needs the identical function, and two callers computing a price
that disagrees in the last floating-point step is a trader quoted one number
and charged another.

Extract when #41 lands, and narrowly: token verification, the settings base,
and the LMSR engine. Not "core".

**Docker build contexts are the binding constraint on that.** Each service
builds from its own directory with `COPY . .`, and a build context cannot reach
outside itself. A fourth service physically cannot import the engine from
`backend/market_service/` — the file is not in its image. Sharing the engine is
therefore not a matter of taste; either the contexts are restructured or the
formula is copied.

**The composite is the websocket producer.** [F-2] #42 broadcasts a new price on
trade commit. The composite is the only place that knows both that a trade
committed and what the resulting price is, so the producer side of #42 belongs
there rather than in the ledger.

**The ledger trusts the composite's arithmetic, for now.** If the ledger does
not evaluate the cost function itself, a bug in the composite writes an
inconsistent price and the ledger records it faithfully and append-only. The
mitigation is the ledger validating with the same shared module, which is a
third reason the extraction above should be genuinely shared rather than
copied.

**Four backend services for three people.** This is the real cost and it is not
small. It is accepted because the ledger role was already reserved, the
composite needs no database of its own, and the alternative is discovering
during the trading epic that the boundary is in the wrong place — when the
database holds balances and positions rather than nothing. ADR 0003 paid this
cost early for the same reason and the argument has not changed.

**`CLAUDE.md` was wrong and is corrected.** It listed the LMSR pricing engine
among the services that were coming. It is a module.

## Alternatives rejected

**Trading inside market_service.** Cheapest by a wide margin, and the one that
`sql/01-roles.sql` was arguably already shaped for. It fails [T-2] #22's
same-transaction requirement unless positions move into `market.*` and the
ledger is reduced to a money log it cannot keep consistent with them, and it
turns market_service into the service where things go.

**Splitting by actor — an admin service and a trader service.** The seam the
question arrived with. Rejected on the table above: it splits four of the
planned queries across the boundary in both directions.

**Positions in `market.*`, the ledger kept pure, a saga between them.** Honest,
and it preserves both existing rules by relaxing [T-2] #22's wording instead.
Rejected because that ticket already calls concurrency and idempotency "the
hard parts, not the LMSR maths", and a two-phase commit across a boundary with
no cross grants makes the hard part considerably harder. Revisit only as a
deliberate amendment to #22, not by accident.

**A standalone pricing service.** What `CLAUDE.md` implied. It owns no state, so
there is nothing for the schema boundary to enforce and nothing for it to be
authoritative about. It would be a container that computes a logarithm over
HTTP, with `q` serialised into it on every keystroke of a cost preview.
