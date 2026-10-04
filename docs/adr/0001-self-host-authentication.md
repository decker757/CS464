# ADR 0001: Self-host authentication instead of using a managed provider

- **Status:** Accepted
- **Date:** 2026-09-13
- **Affects:** [A-1] #29, [A-2] #30, [A-3] #31, [B-1] #32

## Context

We had to choose between building the auth service ourselves and adopting a
managed provider such as Supabase Auth, Clerk, or Auth0.

The first argument raised against a managed provider was reliability: if
Supabase is down, we cannot fail over to another auth provider the way we could
fail over between cloud providers.

## The failover argument does not decide this

Failover between auth providers is impossible in principle, for either option.
Credentials and password hashes live in exactly one system. You cannot route a
login to a second provider that has never seen that user's hash. Real auth
redundancy means replicas of one system, and that is equally available whether
we operate it or a vendor does.

The cloud-provider analogy does not transfer. Compute can fail over because
compute is stateless. Auth is stateful in the worst possible way.

So "we cannot fail over" is true of a self-hosted service too, and the
reliability axis does not separate the options.

## Decision

Build and operate the auth service ourselves, for two reasons that are not
about uptime:

1. **A-1, A-2 and A-3 are graded user stories.** Issues #29, #30 and #31 carry
   their own acceptance criteria. The course is assessing the implementation,
   not a vendor configuration.
2. **Control of recovery time.** During a vendor outage we have no levers and
   our recovery time is whatever their status page says. Running it ourselves,
   we can restart, roll back, scale, or patch. The project is judged at
   scheduled demos rather than on continuous traffic, so a five-minute outage
   we can fix beats a rarer one we can only watch.

## Consequences

**Accepted cost.** Owning it probably raises our failure *rate* even as it
bounds our recovery time. Availability is a function of both, and we are
deliberately trading rare-but-unfixable outages for more-frequent-but-fixable
ones.

**The data tier is the real single point of failure.** Kubernetes replicas
remove the stateless tier as a SPOF, and liveness and readiness probes replace
a crashed pod. Postgres does not replicate itself. Until we run streaming
replication with automated failover, either a managed instance with a standby
or something like CloudNativePG or Patroni, the SPOF has moved one layer down
rather than gone away. Replicating stateless pods is the easy part.

**Other layers that survive a pod restart untouched:**

- Zone and cluster scope. Replicas in one availability zone still share the
  node pool, the ingress controller, and the zone. Spread across zones with pod
  anti-affinity.
- The Kubernetes control plane. Managed is fine; self-managed is one more thing
  to keep alive during exam season.
- Operational risk. For a team new to Kubernetes the realistic outage is a
  misconfigured readiness probe, a bad rollout, or an expired certificate, not
  a crashed process.

**Marginal cost, not new cost.** [5.1] #17 already containerises the backend
and [5.3] #19 builds the deploy pipeline. The ledger, pricing engine, and
websocket server need that platform regardless, so auth rides on it.

## If we ever reverse this

Supabase Auth is Postgres underneath, with bcrypt hashes in a table we can
read, so users can be exported and migrated. Auth0 and Clerk are considerably
worse on that axis. If we do adopt a hosted provider later, Supabase is the one
that keeps the exit open.
