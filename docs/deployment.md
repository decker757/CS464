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
`docker compose --env-file ../../app.env --env-file release.env logs -f ledger`.
The logs rotate at 50 MB per container. #599 adds a dashboard.

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
request: `curl -s "https://auth.<domain>/health?probe=600"`. Then, on an app
server in `/opt/cs464/current`, find that request's line:
`docker compose --env-file ../../app.env --env-file release.env logs --tail 200 gateway | grep probe=600`.
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
keeps its traffic, and the alert on "a service is down or not ready, per
service per server" is what tells you. ADR 0028 has the reasoning.

The gateway returns 404 for `/ready` on every service's subdomain. It names
the failing dependency, and only the monitoring on the private network needs
it. To look yourself:
`docker compose --env-file ../../app.env --env-file release.env exec gateway wget -qO- http://ledger:8000/ready`.

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
| **Total** | | **about 81**, plus the domain |

Images on GHCR are free for a public repository. To stop paying, run
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
- **Monitoring and alerts:** #599 builds them. Until it lands, DigitalOcean's server and
  database graphs are the only view.
- **Login rate limiting:** #600.
- **Load test numbers:** #20, against this deployment.
