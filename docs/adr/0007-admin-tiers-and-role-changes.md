# ADR 0007: A flat admin tier, and role changes audited rather than approved

- **Status:** Accepted
- **Date:** 2026-09-15
- **Affects:** [4.4] #16, [3.1] #9, [3.2] #10, [4.2] #14, [1.3] #3
- **Implemented in:** nothing yet. This record precedes the role-change endpoint in `backend/auth_service/`, which is the first code that has to obey it.

## Context

[4.4] #16 asks for three administrative roles — MARKET_CREATOR, RESOLVER and
SUPER_ADMIN — with creation and resolution kept independent, and role changes
restricted to a super admin. It is marked foundational and "blocks Epic 3".

ADR 0003 shipped a deliberately thin slice of it: `auth.users` gained a `role`
column with two values, `trader` and `admin`, and the value travels to the
market service as a claim in the access token. That decision recorded the rest
of #16 as a later widening — `native_enum=False` on the column exists precisely
so that adding members is an ordinary migration rather than a hard one — and it
left two questions open, which this record answers.

**How many administrative tiers are there?** #16 says three. Nothing has yet
been built that needs more than one.

**Who may change a role, and what stops that being a path to authority nobody
reviewed?** Today the answer is a manual `UPDATE` against the database, which
ADR 0003 accepted as temporary and which the README documents as a chore.

Two facts about this deployment bear on both answers, and they should be stated
rather than assumed.

**The administrators are not mutually untrusted.** This is a three-person
project whose demonstration runs with a small number of administrative accounts
held by the people who wrote the service. There is no adversary inside the
admin set, and no compliance rule that requires one to be modelled.

**Epic 3 needs two administrative *identities*, not two administrative
*classes*.** [3.2] #10's acceptance criterion is "approve control disabled for
the proposing admin". That is a comparison between two user ids.

## Decision

**The role vocabulary stays `trader` and `admin`.** `core/roles.py` in both
services keeps two members. MARKET_CREATOR, RESOLVER and SUPER_ADMIN are not
added.

**Separation of duties is enforced on identity, not on role.** The market
service compares the user ids already stored on the row:

```
[3.2] #10   approver_id != proposer_id
[3.1] #9    proposer_id != market.creator_id   (if we want it; see below)
```

**Role changes are a route on the auth service, gated on `admin`**, not on a
tier above it. `PATCH /users/{id}/role` moves a user between `trader` and
`admin`.

**An admin may not change their own role**, and **a demotion locks the
administrator rows and refuses to remove the last one.** Two rules, because one
is not enough: the first bounds the sequential case and the second bounds the
concurrent one. The consequence below works through why.

**Every role change writes to `audit.admin_actions` in the request's own
transaction**, by the path ADR 0006 fixed: the acting service appends its own
entry, on the session already carrying the UPDATE, and does not commit
separately. The action and the record of it commit together or neither does.

**The first administrator is still made by hand.** There is no bootstrap
environment variable, no seeded account and no self-service promotion from
`trader`. Every subsequent administrator is promoted through the endpoint, by
an existing one, on the record.

## Why no SUPER_ADMIN

A privilege tier exists to protect something from someone. SUPER_ADMIN protects
the role-change endpoint from the administrators it governs, which is a
property worth having exactly when administrators are mutually untrusted. Here
they are not, and a tier that guards nobody from nobody is not free:

- Every route guard stops being `is_admin` and becomes a set-membership test,
  in two services, against a vocabulary that has to stay in step across a wire
  contract it is not safe to get wrong. `market_service/core/roles.py` already
  warns that an unfamiliar value is read as TRADER, so drift between the two
  files costs authority rather than granting it — which is the safe direction,
  and also a silent one.
- Every protected route grows a permutation in its tests: three roles that may
  each be present or absent is a larger matrix than two, multiplied across
  every admin endpoint in Epic 1 through Epic 4.
- It does not solve the problem it appears to solve. Somebody still has to mint
  the first SUPER_ADMIN by hand, because the account that may create privileged
  accounts cannot itself be created by one. Adding the tier moves the manual
  step; it does not remove it.

The trigger for revisiting is named rather than left to taste: **when the
administrator set grows beyond the people who wrote the service, or when one
administrator has reason not to trust another.** At that point the widening is
what ADR 0003 said it would be — a widened CHECK constraint and members added
to two `roles.py` files — and this record should be superseded, not edited.

## Why identity rather than role is the stronger check

This is the part of #16 that looked load-bearing and is not.

The roles enforce a property between *classes*: someone holding MARKET_CREATOR
is not the person who resolves. The identity comparison enforces it between
*individuals*: this market's creator is not this market's proposer, and this
proposal's approver is not the person who proposed it.

The second is strictly stronger on the rows that matter, and the first cannot
replace it. Give two people RESOLVER and the role system is satisfied when one
of them both proposes and approves the same market — [3.2] #10's criterion is
still violated, and it is still an identity comparison that catches it. So the
identity check has to be written whatever the role vocabulary looks like, and
once it is written the class-level version is a weaker restatement of
something already enforced.

It also costs nothing to store. `market.markets.creator_id` exists today, and
[3.1] #9 already has "proposer identity stored on the market" in its
acceptance criteria for reasons of its own.

`proposer_id != creator_id` **is** enforced, by [3.1] #9. It was a real
question, because with a single administrator it makes the story unusable: the
only person who can propose an outcome is the person who created the market.
The answer is to run the demonstration with two administrators rather than to
weaken the rule — which the role-change endpoint above is what makes cheap,
and which [3.2] #10 requires anyway.

So the full separation of duties this project enforces is three identity
comparisons on columns that already exist, and no role vocabulary at all:

```
market.creator_id != proposer_id     [3.1] #9
approver_id       != proposer_id     [3.2] #10
actor_id          != target_id       this record, for role changes
```

## Why a role change is audited and not approved

The obvious alternative is to require a second administrator to approve a
promotion, reusing the machinery [3.2] #10 builds for settlement. It was
rejected for two reasons, and the first is fatal on its own.

**It deadlocks the bootstrap.** With one administrator there is no second one
to approve the creation of the second one. The approval requirement makes the
administrator set unable to grow from one, which is the state every fresh
database and every demonstration starts in.

**It spends a two-person rule on the wrong action.** Approval exists in this
project to stop one person controlling payouts; [3.2] #10 says so in its story.
A role change moves no credits and settles no market. Requiring the same
ceremony for both makes the ceremony routine, which is precisely how a control
that matters stops being read.

What replaces it is detection rather than prevention. Every promotion and every
demotion is a row in `audit.admin_actions` naming the actor, the target and the
time, written in the same transaction as the UPDATE, readable only through
`audit_svc` and deletable by nobody. That is a weaker guarantee than approval
and a deliberate one: the action is reversible, the record of it is not.

## Consequences

**Any administrator can create another administrator, and we accept that.**
This is the escalation surface the decision buys and it should be stated
plainly rather than discovered: an administrator who wants a confederate can
have one, and the control against it is that they cannot do it unobserved. It
is the right trade while the administrators are the authors of the service.
It stops being the right trade at the moment named above.

**The administrator set can never become empty, and it takes two rules to say
so.** The first draft of this record claimed one was enough, and it was wrong
in a way worth recording, because the argument is seductive.

It went: a change must come from an administrator and may not target
themselves, so the last remaining administrator has no legal target whose
demotion would leave zero. That is true, and it is true only one request at a
time. Two administrators demoting each other at the same instant each target
somebody else, so each passes the self-change rule; both transactions read the
other as an administrator before either commits, and under READ COMMITTED
neither blocks the other, because they are writing different rows. Both
succeed. The database is then left with no administrator at all — no route can
make one, because every route that could requires an administrator to call it,
and the only way back is the manual `UPDATE` that made the first one.

So a demotion takes `SELECT id FROM auth.users WHERE role = 'admin' FOR UPDATE`
before it decides. Postgres re-evaluates a locked row against the current
committed state once it stops waiting for it, so the second of the two
transactions sees the first's work and finds itself looking at the last
administrator, which it refuses with a 409. Exactly one wins. The lock is taken
only on the demotion path; a promotion cannot empty the set and is not worth
serialising.

The refusal is sequentially unreachable and that is not a reason to drop it.
Through the route, the caller is an administrator and cannot be their own
target, so a demotion that gets that far always leaves at least the caller
behind. The race is the only way in.

**[4.2] #14 has the same hole and does not go through this route.** Suspending
the last administrator empties the effective set just as thoroughly as demoting
them, and two administrators suspending each other is the same race. That story
needs the same lock and the same refusal, and neither rule here will give it to
it for free.

**A role change takes effect at different moments in different services, and
the endpoint has to say which.** The asymmetry is not obvious and will be
misread as a bug in whichever half is not being looked at.

The auth service owns `auth.users`. Its own guard loads the row and reads the
live value, so a demotion binds `/admin/*` on the demoted administrator's very
next request, with no waiting and no new token. The market service holds no
grant on that table and authorises from the `role` claim, so it keeps honouring
whatever the target's current access token says until it expires — at most one
access-token lifetime, which ADR 0003 already recorded and which the response
reports as `takes_effect_within_seconds` rather than leaving the caller to
assume zero. The fix is unchanged and unscheduled: a denylist keyed on `jti`,
which ADR 0002 records as the same fix for logout.

**A promotion reaches the target on their next access token, which a refresh
supplies as well as a login.** `issue_tokens` reads `user.role` off the row
each time it mints one, so an ordinary client refresh picks the new role up —
the README's "must log in again" is the manual path, not the only one. The half
that will be reported as a bug is the interval before either happens, and [FE]
work on the admin screen should show it.

**The auth service grows an audit writer.** `model/audit.py` and
`service/audit.py` are copied from `market_service`, which ADR 0006 predicted
in its consequences and attributed to the Docker build context: each service
builds from its own directory with `COPY . .`, so a shared module is not
importable until that changes. This is that prediction coming true one story
earlier than expected — 0006 named [4.2] #14 as the trigger; this arrives
first, and #14 will then reuse it within the service rather than copying again.

**[4.4] #16 will not be delivered as written.** Two of its three acceptance
criteria are answered by something other than what they ask for, and the first
is declined outright. The ticket should be updated to say so, with a link to
this record, rather than closed against criteria that were not met — a checkbox
ticked on a property nobody built is worse than an open ticket.

**`audit.admin_actions` gains an action type this record does not constrain.**
Per ADR 0006 the table has no CHECK on `action_type` and the vocabulary lives
in each writer's `StrEnum`, so adding `role_changed` needs no migration and no
coordination with the audit service, which treats an unfamiliar value as an
opaque string.

## Alternatives rejected

**The three roles as [4.4] #16 specifies them.** Modelled an adversary inside
the administrator set that this deployment does not have, and enforced between
classes a property that has to be enforced between individuals anyway. Cheap to
add later by construction — ADR 0003 made sure of that — and expensive to carry
in every guard and every test matrix in the meantime.

**SUPER_ADMIN alone, without splitting creator and resolver.** The smaller
version of the same thing, and it still does not remove the manual first
administrator. It buys protection of the role-change route from administrators
who are not adversaries.

**Second-administrator approval on role changes.** Deadlocks the bootstrap from
one administrator, and dilutes the two-person rule that [3.2] #10 needs to mean
something.

**A bootstrap environment variable or a seeded administrator account.**
Rejected already in ADR 0003 for being authority granted without review, and
independently unusable here: a seeded account needs a password, and the README
forbids a credential in the repository including in an example file.

**A permission table, with roles as named sets of permissions.** The general
form of what #16 asks for, and the correct shape at ten routes and five roles.
At two roles it is a join, a cache and a seeding story in place of one enum
member comparison, to express a mapping with two entries.

**Looking the role up in the database on each request instead of reading the
token claim.** Would remove the fifteen-minute demotion window entirely. It is
not reopened here: ADR 0003 settled it, the market service holds no grant on
`auth.users` by design, and the window is the same one ADR 0002 already accepts
for logout.
