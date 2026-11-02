# ADR 0027: Login and registration limits in Redis, per username or email and address, failing open

- **Status:** Proposed, 2026-11-02. Accepted when the #600 stack merges.
- **Date:** 2026-11-02
- **Decided by:** Ernest, on 2026-11-02: fail open when Redis is down, limits
  keyed per account and IP with a looser cap per account, and this record for
  the auth service's new dependency on Redis. The details below are the #600
  stack's, and its review can reopen any of them.
- **Amended:** 2026-11-02, by Ernest, after code review: the account counters
  are keyed by the identifier the user typed, not by user id (see "The account
  key is what the user typed"). Privacy mattered more than the tighter limit.
- **Affects:** [BE][A-2] #600, [FE] #601, #599, [5.3] #19, ADR 0010,
  ADR 0026
- **Implemented in:** the #600 stack: `deploy/production/gateway/` and
  `deploy/nginx.conf` for the client address, and `backend/auth_service/` for
  the limits.

## Context

predictsmu.xyz has been public since #19. `/auth/login` and `/auth/register`
accept unlimited attempts. That does two kinds of harm.

- **Password guessing.** Argon2 and long passwords make guessing slow. Nothing
  stops it.
- **CPU exhaustion.** Argon2 is slow on purpose. A script that only sends
  logins can slow the auth service for every user without guessing anything.

While designing the fix we found that the client IP was forgeable.
`deploy/production/compose.yml` sets `FORWARDED_ALLOW_IPS: "*"` for every app.
With "trust everything", uvicorn takes the leftmost `X-Forwarded-For` entry,
which is the one the visitor wrote. The gateway passed on
`$proxy_add_x_forwarded_for`, which keeps what the visitor sent and appends to
it. So `request.client.host` was whatever an attacker typed, and a per-IP
limit built on it could be dodged with one header.

What the code already fixes:

- Every replica of auth must see one count, so counts cannot live in a
  process (ADR 0026 runs two or more app servers).
- Production already has a managed Redis-compatible store, used by realtime
  and the ledger (ADR 0010).
- All services share one Postgres, with a connection ceiling they share too.

## Decision

**The gateway resolves the real client address, once.** The gateway trusts
`X-Forwarded-For` only from the load balancer's network (the VPC, our private
network in the cloud) and takes
the address the balancer saw. It then overwrites `X-Forwarded-For` with that
single address instead of appending to it. The services keep reading
`request.client.host`, which is now the visitor's address and cannot be
forged. This was done first, in its own PR, and fixes every service, not only
auth. `deploy/nginx.conf` (the local scale stack) overwrites the header too,
and trusts nothing.

**Four counters, kept in Redis.** Each is a fixed window: it starts at the
first attempt, counts until its time is up, then resets to zero all at once.

| Counter | Counts | Default limit |
|---|---|---|
| Failed logins per identifier and IP | failures | 5 per 900 s |
| Failed logins per identifier, any IP | failures | 50 per 3600 s |
| Logins per IP | every attempt | 30 per 60 s |
| Registrations per IP | every attempt | 20 per 3600 s |

Limits and windows are settings. The per-IP limits are generous on purpose,
because a room of people shares one public IP (campus Wi-Fi, the showcase).
They stop CPU floods. The per-identifier limits stop guessing. An identifier
is what the user typed in the login box: a username or an email.

**The account key is what the user typed.** Every login, for a real account or
not, is keyed `id:<identifier.strip().lower()>`. We never look the user up to
build the key. So a username and the email of the same user do **not** share a
count, and a known identifier and an unknown one are keyed and answered the
same way. This was changed on 2026-11-02 (see Amended above). Keyed by user id,
the shared count was an oracle: lock a public username with five wrong
passwords from one IP, then try candidate emails from that IP. The email that
belongs to that user is refused with 429 and every other one gets "wrong
password". That links a public username to a person's email, and SMU emails
follow a predictable pattern.

**The per-IP login claim runs first**, before anything else, so a flood from
one address is refused before it costs a query or a hash.

**Claim the slot first, then compare.** Each check is `INCR` plus `EXPIRE NX`
in one `MULTI` (Redis runs the commands together, with nothing in between),
then a read of the TTL (the seconds left in the window). `EXPIRE NX` sets the
window's end only when the counter has none yet, so a refused claim cannot
extend the window. Without it, hammering an account would keep it locked
forever. If the new count is over the limit, the
request is refused. "Read, hash, then add" would let 200 simultaneous logins
all read zero and all run Argon2. Claiming first means exactly `limit` of them
reach the password check. The failed-login counters therefore reserve a slot
before the password is checked. A failure leaves it in place. A success clears
both account counters for the identifier that succeeded. The per-IP counters count every
attempt and are never cleared.

**Refused before hashing.** A refused request is `429` with code
`too_many_attempts` and a `Retry-After` header, and costs no Argon2. A refused
login looks the same for a real account and an unknown one.

**Fail open.** Each Redis call has a 0.5 s timeout. On a Redis error or
timeout the limiter allows the request and logs one warning that names the
counter and not the identifier or the address. Auth's readiness does not
include Redis.

## Consequences

- **Auth depends on Redis for the first time.** That is acceptable because the
  counts are disposable: losing them only weakens the limiter for a while.
  What it is not: auth never stores anything it needs in Redis. Accounts,
  sessions and tokens stay in Postgres.
- **A Redis outage turns the limiter off, and nothing says so.** The warning
  is in the log only. #599 adds a "limiter is off" alert. Until then, an
  outage is noticed only by reading logs.
- **Residual: one network shares one IP.** Devices behind one NAT (one router
  sharing one public address, as a Wi-Fi network does) share a public IP. Someone on the owner's own network can still
  lock that account out for one 15-minute window by failing five times.
- **Cost of keying by identifier: twice the guesses.** A user who has both a
  username and an email can be guessed five times through each, per window per
  IP, so ten instead of five. The any-IP cap is per identifier too. That is
  still far too few to guess a long Argon2-hashed password, and we accept it
  to close the oracle above.
- **Residual: the any-IP cap can be used against an account.** Fifty failures
  from many addresses lock an identifier for up to an hour, for everyone,
  including its owner. We accept it: that is the price of stopping guessing
  spread across many machines.
- **A fifth sanctioned catch.** The fail-open catch handles the Redis client's
  error types and the timeout, never a bare `Exception`. This stack
  adds it to the list in `backend/CLAUDE.md`.
- **A production check before the per-IP limits ship.** `stack-test.sh` has no
  real load balancer. Before the limits are released, follow the one-off step
  in `docs/deployment.md` ("The client's address"): send one identifiable
  request from a machine with a known public IP, and the gateway's access log
  must show exactly that address. Only the load balancer's own `/lb-health`
  lines show a `10.64.x` address. Anything else means `real_ip` is not working:
  stop, because every visitor would share one per-address count and the whole
  site would be locked out. The per-IP limits must not ship until it is fixed.
- **Release timing.** Build it on `dev`, and release it after the showcase.

## Alternatives rejected

- **Limit by account only.** Usernames are public (the leaderboard, the trade
  log). Anyone could type five wrong passwords for someone else and lock them
  out, again and again. Keyed by account and IP, a stranger locks out only
  their own address.
- **Account keyed by user id.** A username and its email would share one
  count, which is tidier. But it is an oracle that links a public username to
  a private email (see "The account key is what the user typed"). Privacy
  matters more than the five extra guesses.
- **Counts in memory.** Each replica would keep its own count, so the allowance
  would multiply by the number of replicas.
- **Counts in Postgres.** Every attempt would be a write on the shared
  database, whose connection ceiling every service shares. A login flood
  would then hurt every service.
- **Fail closed.** A Redis outage would lock every user out of login. The
  limiter protects the service, and must not be the thing that takes it down.
- **Limit at the edge only.** An edge limit cannot count per account, because
  it cannot tell which account a request is about. It stays a possible
  addition alongside this, later.
