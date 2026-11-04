# Deploying to production

[5.3] #19. Every push to `main` deploys to DigitalOcean. This page covers how
it is put together, setting it up for the first time, and what to do when
something goes wrong. [ADR 0026](adr/0026-production-on-digitalocean.md) is
why it looks like this.

```mermaid
flowchart LR
  browser([Browser]) -->|https| lb[Load balancer<br/>TLS, health checks]
  lb -->|http| g1[App server 1<br/>gateway + 5 services]
  lb -->|http| g2[App server 2<br/>gateway + 5 services]
  g1 & g2 -->|private network| pg[(Managed Postgres)]
  g1 & g2 -->|private network| vk[(Managed Valkey)]
  gh[GitHub Actions] -->|images| ghcr[(GHCR)]
  gh -->|ssh: migrate, then roll| g1 & g2
  g1 & g2 -->|pull| ghcr
```

| Name | What answers |
| --- | --- |
| `https://<domain>` | the frontend |
| `https://auth.<domain>`, `market.`, `ledger.`, `audit.` | that service, with its `/docs` |
| `wss://realtime.<domain>/ws/prices` | live prices |

## What is where

| Path | What it is |
| --- | --- |
| `infra/terraform/` | everything on DigitalOcean: servers, load balancer, certificate, Postgres, Valkey, DNS, firewalls, and every generated secret |
| `infra/tf` | runs Terraform from its Docker image, so nobody installs it |
| `infra/push-secrets.sh` | copies Terraform's outputs into the GitHub `production` environment |
| `deploy/production/compose.yml` | what each app server runs |
| `deploy/production/gateway/` | the image that serves the frontend and routes each subdomain |
| `deploy/production/release.sh` | one deploy, run from GitHub: send files, migrate once, roll each server |
| `deploy/production/migrate.sh`, `ensure-database.sh` | on a server: roles and grants, then the three migrate steps |
| `deploy/production/roll.sh` | on a server: drain, start the new release, go back if it is unhealthy |
| `/opt/cs464/releases/<commit>/` | on a server: one release's own compose.yml, scripts and `release.env`; the live one and the one before are kept |
| `/opt/cs464/current` | on a server: a link to the live release's directory |
| `/opt/cs464/app.env` | on a server: the secrets and that server's settings, shared by every release and rewritten by every deploy |
| `deploy/production/test/` | the two tests below, run by `ci-deploy.yml` |
| `deploy/grafana/` | the dashboard and the five alert rules, imported into Grafana Cloud by hand |
| `.github/workflows/deploy.yml` | push to `main`: build and push the images, then run `release.sh` |

## Setting it up for the first time

You need: a DigitalOcean account, a domain, Docker, and `gh` logged in as an
admin of this repository.

1. **Credit.** The GitHub Student Developer Pack includes DigitalOcean
   credit. Claim it before anything else, so the first bill comes out of it.
2. **A domain.** Buy one (about US$10 a year), or claim a free one from the
   Student Pack.
3. **An API token.** In the DigitalOcean console go to API, then Generate New
   Token, with read and write access. Keep it in your shell only:

   ```bash
   export DIGITALOCEAN_TOKEN=<token>
   export TF_VAR_domain=<your domain>
   ```

4. **Hand the domain's DNS to DigitalOcean.** Create the domain alone first,
   then set the nameservers at your registrar to `ns1.digitalocean.com`,
   `ns2.digitalocean.com` and `ns3.digitalocean.com`:

   ```bash
   infra/tf init
   infra/tf apply -target=digitalocean_domain.cs464
   ```

   The certificate in the next step can only be issued once those
   nameservers are live. Check with `dig NS <domain>`. It usually takes
   minutes, and can take hours.
5. **Everything else.** Read the plan before you type `yes`. It is roughly 25
   resources and takes about ten minutes, most of it the databases:

   ```bash
   infra/tf apply
   ```

   `infra/terraform/terraform.tfstate` now holds every production secret. It
   is gitignored. Keep it, and do not share it.
6. **Give GitHub the secrets:**

   ```bash
   infra/push-secrets.sh
   ```

7. **Deploy.** Run the Deploy workflow by hand (Actions, Deploy, Run
   workflow, on `main`), or promote `dev` to `main`. The first run takes about
   ten minutes. When it is green, open `https://<domain>`.
8. **Seed the demo markets, if this site is for the showcase** (#609). Do it
   before anyone makes an administrator: the seed makes the site's first one,
   `admin_rachel`, the way ADR 0007 says a first administrator is made, and
   refuses a site that already has one. From `frontend/`, on this machine:

   ```bash
   npm run seed:production
   ```

   It asks you to type the domain. It refuses a site that already has a
   market, or any of its accounts, because a seed cannot be undone: ledger
   entries are never deleted. It makes the same two admins, nine traders and
   fourteen markets as the local seed (README, "A demo database") in about
   three minutes.

   Every account gets a generated password, in
   `~/cs464-production-accounts.txt`, readable by you only. It is the only
   copy, so move the passwords into a password manager and delete the file
   once they are safe. The repository's test password is never used here,
   because anyone can read it.

   Seed **one to two weeks before the showcase,** if you can. The browse
   page's "Biggest movers" band and each card's weekly change measure from
   trades at least seven days old, and a trade's time cannot be backdated.
   Seeded the day before, everything else is there and those two are not.

   The seed registers eleven accounts from one address in a few seconds.
   Login rate limiting (#600) must allow that, or the seed runs before it.
9. **Your own administrator account.** Register on the site.
   - If you seeded, log in as `admin_rachel` and promote yourself from Admin →
     Users. That is how every administrator after the first is made, and the
     audit log records who did it.
   - If you did not seed, you are the first administrator. Run the promotion
     by hand from the first server, which can reach the database:

     ```bash
     ssh deploy@<first app server> # its address: infra/tf output app_hosts
     docker run --rm -it postgres:17.2-alpine psql "<infra/tf output -raw database_admin_url>" \
       -c "UPDATE auth.users SET role = 'admin' WHERE lower(username) = '<you>'"
     ```

   Log out and back in: the role rides in the token.

**If the seed stops partway.** Read what it printed last.
- **It stopped before "Published 14 markets":** nothing permanent was made
  except some accounts. A second run refuses, because those names exist. To
  start again, recreate the `cs464` database in the DigitalOcean console (or
  `infra/tf apply -replace=digitalocean_database_db.cs464`), then re-run the
  Deploy workflow, which rebuilds the schemas, and seed again.
- **It stopped after:** the markets, trades and ledger entries are
  permanent. Either leave the site half-seeded, or recreate the database as
  above, which also deletes anything else on the site.

Either way, keep the passwords file the first run wrote. It holds the only
copy of the passwords for the accounts that run made.

## A normal deploy

Merge to `main`. `deploy.yml` then:

1. Builds the five service images and the gateway, and pushes them to GHCR,
   tagged with the commit.
2. Sends every server its files and `app.env`, and logs it in to GHCR for
   this run only.
3. Ensures the database roles and grants, then migrates, once, from the
   first server.
4. Rolls the servers one at a time. Each one is drained, so the load
   balancer stops sending it traffic. Then its new release is started and
   must pass every health check, and it is undrained before the next server
   starts. The other servers carry the traffic meanwhile.
5. Checks every public name answers.

Two deploys never overlap. A push during a deploy waits for it to finish.

## When a deploy fails

The run goes red and says where. What state it left behind:

| Failed at | Servers are on |
| --- | --- |
| building the images | the old release; nothing was touched |
| migrating | the old release; no server was rolled |
| rolling a server | the old release, every one of them: the failed server went back by itself, and the ones before it were sent back |
| rolling a server, which "could not go back" | the failed server serves nothing and stays drained; the ones before it stay on the new release, serving. Fix forward, or `roll.sh` the failed one by hand once you know why |
| the public check | the new release, every one of them, all healthy behind the load balancer; look at DNS, the certificate and the load balancer |

**A migration is not undone when its release is sent back.** So write every
migration to work with the release before it too: add a column, and drop the
old one a release later. A migration that breaks the old release breaks the
rollback. `CLAUDE.md` says the same, where backend changes are written.

**Going back on purpose.** If the release you are undoing added no
migration, revert it on `main`, which deploys again. If it did add one, that
does not work: the deploy migrates first, and Alembic refuses a database
already newer than the code it is given. Fix forward instead, keeping the
migration, or go back on each server by hand, which does not migrate. A
server keeps the release before, so: `ssh deploy@<server>`, then
`/opt/cs464/releases/<commit>/roll.sh`, one server at a time.
Its images may have been removed, and the server is logged out of GHCR after
every deploy. So either make the `cs464-*` packages public once (each
package's settings on GitHub; the repository already is), or log in first:
`docker login ghcr.io -u <you>` with a token that can read packages.

**Changing a secret or a setting in `app.env`:** apply it, run
`infra/push-secrets.sh`, and deploy. Every release on a server reads the one
`app.env`, so the release before picks up the change too. Never rename or
remove a setting's name there, only add: the release before still reads the
old name, and going back to it would start it without one.

**Logs:** `ssh deploy@<server>`, `cd /opt/cs464/current`, then
`docker compose --env-file /opt/cs464/app.env --env-file release.env logs -f ledger`.
The logs rotate at 50 MB per container. The services log JSON, one object per
line. To follow one request, grep or filter by `request_id`: a response's
`X-Request-ID` header is the value to search for, in the service logs and in
the gateway's `rid=`. The gateway stamps an ID when the client sent none, or
one the services would not keep, so a 502 or 504 the gateway answered itself
still has a line to find. With Grafana on, the dashboard does the same
search of the services' lines across both servers at once, though not of the
gateway's: see "Following one request" below.

**The client's address:** the gateway resolves it and overwrites
`X-Forwarded-For` with that one address (`set_real_ip_from` in
`deploy/production/gateway/default.conf.template`). uvicorn trusts the
leftmost entry of that header, which a visitor can write, so passing it on
would let anyone pick the address a service sees. Only the load balancer,
which connects from inside the VPC (`10.64.0.0/20`), is trusted to have set
it. #600.

**One-off check after the release that carries this reaches production,
before the release that adds the per-IP login limits:** from a machine whose
public address you know (`curl -s https://ifconfig.me`), send one identifiable
request: `curl -s -H 'X-Request-ID: probe-600' https://auth.<domain>/health`.
Then, on an app server in `/opt/cs464/current`, find that request's line by
its ID (the gateway logs the path, never the query string):
`docker compose --env-file /opt/cs464/app.env --env-file release.env logs --tail 200 gateway | grep 'rid=probe-600'`.
Its first field, the client address, must be exactly your public address. Ignore
the `/lb-health` lines: the load balancer sends those itself every few
seconds, so they are expected to show an address inside `10.64.0.0/20`. If
your line shows anything else, a `10.64.x` address or a public address you do
not recognise, stop: the per-IP limits must not ship until `set_real_ip_from`
is fixed, or every visitor shares one rate-limit bucket.

## Health and readiness

Every service answers two paths. `/health` says the process is up, and checks
nothing else. The load balancer (through the gateway's `/lb-health`, which
asks the ledger), Docker, `roll.sh` and `release.sh` use it. `/ready` says the
service can reach its database (realtime: Redis), and answers 503 naming the
one that failed. The dashboard and the alerts use it.

The load balancer does not use `/ready` on purpose. The database is one
managed instance shared by every server, so taking a server out of rotation
cannot fix it. It would only turn "trades fail with an error" into the load
balancer's bare 503 for the whole site, homepage included, and make a deploy
during a blip roll back. So a server whose auth or market service is broken
keeps its traffic, and the `cs464-service-down` alert ("a service is down or
not ready, per service per server") is what tells you. It reads the same
checks through each service's `service_dependency_ready` metric. With Grafana
off, nothing watches `/ready` and you have to look. ADR 0028 has the
reasoning.

The gateway returns 404 for `/ready` on every service's subdomain. It names
the failing dependency, and only the monitoring on the private network needs
it. To look yourself:
`docker compose --env-file /opt/cs464/app.env --env-file release.env exec gateway wget -qO- http://ledger:8000/ready`.
Ready prints `{"status":"ready",...}`. Not ready prints no body: busybox
`wget` shows only `wget: server returned error: HTTP/1.1 503 ...`, and that
line is the answer.

Every service also exposes `/metrics`, the counters and gauges Grafana Alloy
scrapes. It is private the same way: the gateway returns 404 for it on every
subdomain, and only Alloy, on the private network, reads it. To look
yourself, use the `exec gateway wget` command above with `/metrics` in place
of `/ready`.

## Monitoring: first-time setup

Each server runs Grafana Alloy, which sends metrics, logs and traces to
Grafana Cloud. It needs six values and one token from your Grafana Cloud
stack. Alloy is optional: with no token, no Alloy runs and the deploy works
as before.

1. Make a free Grafana Cloud stack: grafana.com, *My Account*, *Add stack*.
2. *Prometheus* tile, *Details*. Copy the **remote write endpoint**
   (`https://prometheus-<...>.grafana.net/api/prom/push`) and the numeric
   **username / instance ID**.
3. *Loki* tile, *Details*. Copy the **URL** and add `/loki/api/v1/push` to
   the end of it. Copy the **user** too.
4. *OpenTelemetry* tile (*Connections*, *OpenTelemetry (OTLP)*). Copy the
   **OTLP endpoint** (`https://otlp-gateway-<...>.grafana.net/otlp`) and the
   **instance ID**. Use it exactly as shown, ending in `/otlp`. Alloy adds
   `/v1/traces` itself, so a longer value would send traces to the wrong
   address.
5. *Access policies*: make a policy named `cs464-servers` with the scopes
   `metrics:write`, `logs:write` and `traces:write`, and nothing else. It
   needs no read scope.
   Then make a token for that policy and copy it. Grafana shows it once.
6. Put them in the GitHub `production` environment. Only a run on `main` can
   use it (ADR 0026). The six values are web addresses and numbers, not
   secrets, so they are variables:

   ```bash
   gh variable set GRAFANA_PROMETHEUS_URL  --env production --body '<remote write URL>'
   gh variable set GRAFANA_PROMETHEUS_USER --env production --body '<instance id>'
   gh variable set GRAFANA_LOKI_URL        --env production --body '<loki push URL>'
   gh variable set GRAFANA_LOKI_USER       --env production --body '<loki user>'
   gh variable set GRAFANA_OTLP_URL        --env production --body '<otlp endpoint>'
   gh variable set GRAFANA_OTLP_USER       --env production --body '<instance id>'
   gh secret   set GRAFANA_CLOUD_TOKEN     --env production
   ```

   The last one is the only secret. `gh` asks for it on stdin: paste the
   token there, so it never appears on a command line.

If your stack's pages name these differently, follow what they show and fix
this list.

**The next deploy from `main` picks them up.** `release.sh` rewrites each
server's `app.env` on every deploy, so nothing on a server needs editing by
hand. Alloy starts after the roll, once the new release is serving.

**Mistakes fail early.** If the token is set, `release.sh` needs all six
values, and each URL must start with `https://`. It checks this before it
touches any server, so a missing or malformed value stops the deploy and
changes nothing. It does not check the endings: the Loki URL must end in
`/loki/api/v1/push` and the Prometheus one in `/api/prom/push`, or Grafana
receives nothing.

**Alloy cannot break a deploy.** It is started last, as best effort. If it
will not start or keeps restarting, the roll prints a warning and carries on.
The server then stops sending metrics, and if it was sending in the last
hour, `cs464-service-down` emails about it within about 8 minutes. A server
that has never sent, or has been silent for over an hour, pages nobody: the
checklist's `server` step is what catches that. A wrong token or a wrong URL ending looks the same
from Grafana: Alloy runs and the deploy is green, but no data arrives, so the
same alert fires. After an hour that alert clears by itself, even if the
server is still silent, so a "resolved" email is not proof that data is back:
check the dashboard. Alloy's log on a server says why: in
`/opt/cs464/current`, run
`docker compose --profile observability --env-file /opt/cs464/app.env --env-file release.env logs --tail 100 alloy`.

**To switch Alloy off,** delete the token and deploy:

```bash
gh secret delete GRAFANA_CLOUD_TOKEN --env production
```

With no token, the next deploy removes the Alloy container from each server.
The variables can stay. They do nothing without the token.

Until a deploy succeeds, Alloy keeps the old token and keeps shipping. To stop
it at once, also revoke the token in Grafana Cloud: *Access policies*, the
`cs464-servers` policy, its token, *Delete*.

`infra/push-secrets.sh` does not set any of these. They are not Terraform's.

## Monitoring: dashboard and alerts

`deploy/grafana/` holds the dashboard and the five alert rules. Import them
once into your Grafana Cloud stack (`https://<stack>.grafana.net`). The
README's "Monitoring" section says what they show and when each alert fires.

**The dashboard.** *Dashboards*, *New*, *Import*, then upload
`deploy/grafana/dashboard.json` into a folder named `CS464`. It is then
*CS464 / Overview*. It names no datasource of its own: the `prometheus` and
`loki` boxes at its top pick them, so it imports into any stack unchanged.

**The alerts.** `deploy/grafana/alerts.yaml` is in Grafana's alert-rule
provisioning format. It puts the rules in a `CS464` folder and sends each to
an email contact point named `cs464-email`. A new Grafana Cloud stack has no
email contact point at all, so make that one first: *Alerting*, *Contact
points*, *Add*, type *Email*, with the addresses to page. Its queries
name the datasource `grafanacloud-prom`, a Grafana Cloud stack's own
Prometheus. Check yours under *Connections*, *Data sources*: the UID is the
last part of the Prometheus one's URL. If it differs, change every
`datasourceUid: grafanacloud-prom` in alerts.yaml before importing.

`cs464-service-down` also fires for a server that has stopped sending
metrics: one seen in the last hour and silent now. If that server stays
silent, the alert clears by itself about an hour after it went quiet.
Removing a server on purpose, by lowering `app_server_count`, pages once.

**How they were imported (2026-11-04).** Through the API, with a
service-account token that had the Admin role and expired after a day. The
account was deleted afterwards. The file is YAML for file provisioning, so it
went in as JSON: the group's rules, each given `folderUID: cs464`,
`ruleGroup: cs464` and `orgID: 1`, wrapped as
`{"title": "cs464", "folderUid": "cs464", "interval": 60, "rules": [...]}`,
sent with `PUT /api/v1/provisioning/folder/cs464/rule-groups/cs464`. The
request carried the header `X-Disable-Provenance: true`, so the rules can
still be edited in the UI. The contact point went in the same way. Grafana's
export of the group matched this file, apart from defaults it fills in
itself, so the file in the repo is the commented original.

**`cs464-service-down` is paused until the first release.** Until a server
sends metrics, it has no data, and no data counts as down, so it would email
every few minutes. Unpausing it is the first step of the post-release
checklist below.

**Linking a log line to its trace.** Grafana Cloud's own Loki datasource is
read-only, so it cannot take a derived field. A *correlation* does the same
job: *Administration*, *Plugins and data*, *Correlations*. Ours is named
*Open trace*. It goes from the Loki datasource to the Tempo one, and is of
type *Query*, on the field `Line`. It has a regex transformation,
`"trace_id": "(\w+)"`, into the variable `trace_id`, and the target query is
`${trace_id}`. A log line's details then show a button that opens its trace.
The regex matches the services' JSON lines exactly: `json.dumps` writes a
space after the colon.

**Following one request.** Copy the `X-Request-ID` header from a response
(the browser's developer tools, *Network*). Paste it into the dashboard's
`request_id` box. The *Logs* panel then shows only that request's lines, from
every service it touched, and a line's `trace_id` opens the trace. The
gateway's own lines are not JSON, so they carry no `service` label and the
panel leaves them out. To find one, use *Explore* on Loki with
`{container="gateway"} |= "rid=<request id>"`.

**What a trace keeps.** Spans carry no query string, cookie or Authorization
header. They do keep the client address and the user agent, by decision, for
chasing abuse. The gateway's access lines start with the client address too.
The services' own JSON log lines carry no IP, except realtime's websocket
handshake lines, which uvicorn writes as
`<client ip>:<port> - "WebSocket <path>" [accepted]` (or `403`), with the path
as requested, query string included. The socket never takes a token in its
query string, by design (docs/api/realtime-service.md), so nothing secret is in that line.

**Things that look wrong and are not:**

- A trace makes two hops, and the two addresses end differently. Service to
  the local Alloy: `release.sh` sets `TRACING_ENDPOINT` to
  `http://alloy:4318/v1/traces`, which must be the full path, ending in
  `/v1/traces`. Alloy to Grafana Cloud: `GRAFANA_OTLP_URL` is the base
  address ending in `/otlp`, and Alloy adds `/v1/traces` itself (first-time
  setup, step 4). A wrong `TRACING_ENDPOINT` breaks nothing else: the
  service's log just shows OpenTelemetry warnings that it is dropping spans.
- The same "dropping span" warnings appear while Alloy is down, including
  the moments between a roll and Alloy starting. They are harmless.
- Background work makes small traces with no request behind them: the close
  sweep, every 10 s on each server, and the leaderboard refresh, every 60 s
  on the first server. In Tempo, `{ kind = server }` shows requests only.
- A websocket's span is named `HTTP`. Find one with
  `{ .http.target = "/ws/prices" }`.
- The *Book-lock wait* panel covers every caller of `lock_book`, not just
  trades: limit orders, cancels, settlements and voids wait on the same
  lock.

**The series budget.** The free tier allows 10,000 active series. About 45
method-route pairs × 12 histogram series ≈ 540, plus about 135
request-counter series, about 200 process/Python series, and about 40
business series. That is ≈ 950 per server, ≈ 1,900 for two, well under 10k.
Re-measure it on the *Usage* page a week after the first release, and update
this paragraph.

**Post-release checklist.** Run it once, after the first deploy from `main`
that carries this work. That deploy comes only after the showcase.

1. Unpause `cs464-service-down`: *Alerting*, *Alert rules*, *CS464*, the
   rule, *Resume evaluation*. Do it once every server has been sending for
   a few minutes, or it pages at once.
2. Every panel on *CS464 / Overview* has data. Make a trade first, or the
   trading panels have nothing to show.
3. The dashboard's `server` box lists every server, `app-1` up to the last
   `app-N`. A server missing there has never sent, and no alert covers it.
4. **Ask Ernest before you SSH.** On one server, run
   `docker stats --no-stream` and put Alloy's memory use in the README's
   cost paragraph, in place of "a little memory". If it is over about
   300 MB, consider a `mem_limit` on `alloy` in
   `deploy/production/compose.yml`.
5. Tempo shows a trade as one trace across ledger, market and auth, and its
   spans carry no query string, cookie or Authorization header (the client
   address and user agent are kept, by decision).
6. Trigger `cs464-service-down` once. **Ask Ernest before you SSH.** On one
   server, in `/opt/cs464/current`, run
   `docker compose --env-file /opt/cs464/app.env --env-file release.env stop audit`.
   Wait 6 minutes, and check that the email arrives. It can take a minute
   more. Then run the same command with `start audit` and check that the
   alert clears.

## Scaling

- **More app servers:** raise `app_server_count` in
  `infra/terraform/variables.tf`, `infra/tf apply`, `infra/push-secrets.sh`,
  then deploy. The load balancer picks the new server up by its tag.
- **The limit is Postgres's connections.** Two servers use 40 of the 2 GB
  plan's 47 or so, plus three for the migrate steps. A third server needs the
  pool sizes in `deploy/production/compose.yml` lowered, or a bigger
  `database_size`. DECISIONS.md, "Each service's connection pool is a
  setting, sized to the database's ceiling".
- **Bigger servers:** change `app_server_size`, then resize one server at a
  time, or both go down together: `infra/tf apply
  -target='digitalocean_droplet.app[0]'`, wait for it to be healthy behind
  the load balancer, then the same for `app[1]`.

## What it costs

DigitalOcean's list prices when this was written. Check the console for
today's.

| Part | Plan | US$ a month |
| --- | --- | --- |
| 2 app servers | `s-1vcpu-2gb` | 24 |
| Load balancer | 1 node | 12 |
| Postgres | `db-s-1vcpu-2gb`, chosen for its connection limit | 30 |
| Valkey | `db-s-1vcpu-1gb` | 15 |
| Grafana Cloud | free tier | 0 |
| **Total** | | **about 81**, plus the domain |

Grafana Cloud's free tier allows 10,000 active series, 50 GB of logs and
50 GB of traces, kept for 14 days, and 3 users. We expect about 1,900 series
(the series budget above). Alloy itself costs only a little memory on each
server. Images on GHCR are free for a public repository. To stop paying, run
`infra/tf destroy`. That deletes the database too, so take a backup first if
anything in it matters.

## Tests

`ci-deploy.yml` runs these on every pull request that touches the stack. Both
need only Docker.

- `deploy/production/test/stack-test.sh` runs `compose.yml`, the gateway and
  the scripts on one machine. It uses a local registry, and a Postgres whose
  admin is not a superuser, like DigitalOcean's. It checks routing, login and
  the cookie's domain, the websocket and its origin check, draining, and both
  ways a release can fail.
- `deploy/production/test/release-test.sh` runs `release.sh` against fake
  servers. It checks the order of work, that a failed migration rolls
  nothing, and that one failed server sends every other server back.

What only a real deploy proves: the DigitalOcean resources themselves, the
certificate, and TLS to the managed databases.

## Not done yet

- **Staging.** #19 also asks for a staging environment on its own database.
  `infra/terraform` would run it as a second workspace. It is not built.
  It needs a registrable domain of its own, not `staging.<domain>`: the
  login cookie reaches every subdomain of the production domain (ADR 0026).
- **Monitoring's first real check.** The dashboard and the five alerts are
  built, but have not yet seen production data. The post-release checklist
  above runs after the first deploy from `main` that carries them.
- **Re-measuring the series budget.** A week after that release, read the
  real series count off Grafana Cloud's *Usage* page and update the estimate
  above and the README.
- **Login rate limiting in production.** #600 is merged into `dev`, but
  production deploys only from `main`, after the showcase, so it is not live
  yet.
- **Load test numbers:** #20, against this deployment.
