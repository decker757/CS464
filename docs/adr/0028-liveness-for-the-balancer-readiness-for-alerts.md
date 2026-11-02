# ADR 0028: /health is liveness for the load balancer, /ready is readiness for dashboards and alerts

- **Status:** Proposed, 2026-11-02.
- **Date:** 2026-11-02
- **Decided by:** Ernest, on 2026-11-02, while designing #599.
- **Affects:** #599, [5.3] #19, [5.4] #20, ADR 0026, ADR 0010, ADR 0011
- **Implemented in:** #599's first PR: `backend/shared/readiness.py`, each
  service's `core/observability.py` and `main.py`, and
  `deploy/production/gateway/`.

## Context

The load balancer asks the ledger's `/health` on every server, and nothing
else. So an unhealthy auth or market service on one server is not noticed by
anything, and the three alerts first planned for #599 would not have caught
the outage of 1 November at low traffic.

ADR 0026 left that open. It says `/lb-health` asks the ledger only, and that
#599 "should either check every service behind it or alert on each". The
gateway's `/lb-health` comment from #19 leaned the other way: "a server that
cannot trade should not get traffic. #599 makes /health check the database."
This record settles which.

The fact that decides it: the database is one managed instance, shared by every
server. Taking a server out of the load balancer cannot fix a database that is
down. Every other server sees the same problem.

## Decision

### Two endpoints on every service

- `/health` stays **liveness**: is the process up. It checks nothing else. The
  load balancer (through the gateway's `/lb-health`, which asks the ledger),
  each image's Docker health check, `roll.sh` and `release.sh` keep using it,
  unchanged.
- `/ready` is new, and is **readiness**: can the service reach what it needs.
  It runs one cheap query under a short timeout and answers 200, or 503 naming
  the dependency that failed.

What each one checks:

| Service | `/ready` checks |
| --- | --- |
| auth | the database |
| market | the database |
| ledger | the database |
| audit | the database |
| realtime | Redis (a `PING`) |

Auth checks only the database. Its use of Redis for login limits fails open
(#600), so a Redis outage does not stop auth serving. The ledger also checks
only the database. Its Redis publish is allowed to fail (ADR 0010).

### The gateway hides /ready

`/ready` names the dependency that is down, and only the monitoring needs it,
over the private network. So each service's server block in the gateway
returns 404 for it, through `private-paths.inc`. The local scale stack's
`nginx.conf` does the same. The stack test checks both halves: 404 from the
internet side, and "ready" from inside.

### Five alerts, one of them for this

The alerts are committed with the dashboard in #599's last PR. The fourth is
"a service is down or not ready, per service per server". It answers ADR
0026's "alert on each": the balancer asks only the ledger, so this alert is
what asks the other four.

The other four are a spike of 5xx errors, a slow 95th percentile, the close
sweep falling behind, and the login limiter being off.

## Consequences

- A server whose auth is broken keeps its traffic. The alert is what notices
  it, and a person acts on it.
- A database blip shows as errors on the pages that need it, and the homepage
  stays up. A deploy during the blip does not roll back, because `/health`
  does not look at the database.
- Readiness is only measured when Alloy scrapes. A service can be down for up
  to one scrape interval before anything shows.
- `/ready` is now a path no public caller reaches. A new service has to be
  added to the gateway's server blocks with the same include, or its `/ready`
  is public.

## Alternatives rejected

- **A readiness check on `/lb-health`.** It would take every server out at once
  when the database blips, and the balancer's bare 503 would cover the whole
  site, homepage included. Deploys would roll back too.
- **An nginx check of all five services on `/lb-health`.** Same problem for the
  database, and it makes a deploy depend on the slowest service. It still
  could not fix a shared dependency.
- **A database check inside `/health`.** Docker's health check and `roll.sh`
  use `/health` too. A blip would restart containers that are fine, and fail
  releases that are fine.
