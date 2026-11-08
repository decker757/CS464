# §1.2 and §2.2 Infrastructure facts — draft

Facts for the team to write from, each with the file it comes from. Paths are relative to the repository root. The "Checked live" lines are read from the running site on the date they give, not from a file. ADR 0026 is `docs/adr/0026-production-on-digitalocean.md`. Re-checked against the tree on 2026-11-08.

## Where it runs

| Fact | Value | Source |
|---|---|---|
| Live URL | https://predictsmu.xyz | repository variable `PRODUCTION_DOMAIN` (`infra/push-secrets.sh:66-70`); `infra/terraform/variables.tf:1-4` |
| Provider and region | DigitalOcean, `sgp1` (Singapore) | ADR 0026:35-37; `infra/terraform/variables.tf:6-10` |
| Described in | Terraform, under `infra/terraform/` | ADR 0026:35-36 |
| Private network | VPC `10.64.0.0/20` | `infra/terraform/network.tf:3-7` |

## Monthly cost

DigitalOcean's list prices when `docs/deployment.md` was written. That page says to check the console for today's. It is not a bill.

| Part | Plan | US$ a month | Source |
|---|---|---|---|
| 2 app servers | `s-1vcpu-2gb` | 24 | `docs/deployment.md:682` |
| Load balancer | 1 node | 12 | `docs/deployment.md:683` |
| Postgres | `db-s-1vcpu-2gb`, chosen for its connection limit | 30 | `docs/deployment.md:684` |
| Valkey | `db-s-1vcpu-1gb` | 15 | `docs/deployment.md:685` |
| Grafana Cloud | free tier | 0 | `docs/deployment.md:686,689-691` |
| **Total** | | **about 81**, plus the domain | `docs/deployment.md:687`; ADR 0026:116-118 |

| Other cost | Fact | Source |
|---|---|---|
| Domain | About US$10 a year, or a free one from the Student Pack | `docs/deployment.md:52-53` |
| Images on GHCR | Free for a public repository | `docs/deployment.md:692-693` |
| A database standby | Double the database's cost; not bought | ADR 0026:107-109 |

## Student Pack credit

| Fact | Source |
|---|---|
| The GitHub Student Developer Pack includes DigitalOcean credit | `docs/deployment.md:50-51`; ADR 0026:36-37 |
| Claim it first, so the first bill comes out of it | `docs/deployment.md:50-51` |
| The amount, and whether it was claimed, are not recorded in the repository | a search of `docs/` and `infra/` finds no figure |

## Server and database sizes

| Part | Setting | Value | Source |
|---|---|---|---|
| App servers | `app_server_count` | 2 | `infra/terraform/variables.tf:12-21` |
| App servers | `app_server_size` | `s-1vcpu-2gb`: 1 vCPU, 2 GB | `infra/terraform/variables.tf:23-27` |
| App servers | image | `ubuntu-24-04-x64` | `infra/terraform/servers.tf:9` |
| Postgres | `database_size` | `db-s-1vcpu-2gb`: 2 GB, "for its connection limit rather than its memory" | `infra/terraform/variables.tf:29-33` |
| Postgres | engine, version, nodes | `pg`, 17, 1 | `infra/terraform/databases.tf:4-12` |
| Valkey | `cache_size` | `db-s-1vcpu-1gb` | `infra/terraform/variables.tf:35-39` |
| Valkey | engine, version, nodes | `valkey`, 8, 1 | `infra/terraform/databases.tf:29-37` |
| Load balancer | health check | `/lb-health` on port 80, every 3 s, 3 failures or 2 successes. The gateway answers it with the ledger's `/health` | `infra/terraform/load_balancer.tf:49-57`; `deploy/production/gateway/default.conf.template:54-68` |
| Load balancer | idle timeout | 600 s | `infra/terraform/load_balancer.tf:59-61` |
| Connections | ceiling on the 2 GB plan | about 47 (25 a GB, less 3 DigitalOcean keeps); DigitalOcean's figure, which the repository cannot check | `docs/deployment.md:519-521`; ADR 0026:85-87 |
| Connections | to Postgres | Four PgBouncer pools in transaction mode, one per service role: auth 6, market 6, ledger 14, audit 2 = 28, however many servers run | `infra/terraform/databases.tf:47-70`; `docs/deployment.md:525-538` |
| Connections | to the pools, per server | auth 3+1, market 3+2, ledger 10+5, audit 1+1 = 26 client connections (pool + overflow) | `deploy/production/compose.yml:84-90,97-98,113-114,126-127,141-142` |
| Connections | left over | about 19, for what connects to Postgres directly: the three migrate steps, one at a time, `ensure-database.sh` and an operator | `docs/deployment.md:536-538`; `infra/terraform/outputs.tf:15-21`; `deploy/production/compose.yml:32-38,66-82` |

## Backups

| Fact | Source |
|---|---|
| Postgres relies on DigitalOcean's daily backups | ADR 0026:107-109 |
| Terraform sets no backup option: they are the managed service's own, and the retention period is not in the repository | `infra/terraform/databases.tf:4-12` |
| There is no standby node | `infra/terraform/databases.tf:10`; ADR 0026:107-109 |
| The repository records no restore having been tried | a search of `docs/deployment.md` and ADR 0026 for "restore" finds nothing |
| The app servers hold no data, so they have nothing to back up | `infra/terraform/servers.tf:1-2`; `deploy/production/compose.yml:1-4` |
| Valkey holds nothing that needs a backup: the price bus keeps no messages, and auth's login-attempt counters are disposable, since losing them only turns the limit off for a while (it fails open). Terraform's comment still calls Valkey the bus only | `infra/terraform/databases.tf:27-28`; `deploy/production/compose.yml:100-101`; ADR 0027, Consequences |
| `infra/tf destroy` deletes the database too | `docs/deployment.md:693-694` |
| `terraform.tfstate` holds every generated secret. It is on the machine that ran `apply`, gitignored, and not backed up by anything in the repository | `infra/terraform/versions.tf:4-6`; `docs/deployment.md:82-83` |

## Monitoring

| Fact | Source |
|---|---|
| Every service serves `/health` (liveness: the process is up) and `/ready` (readiness: its database, or Redis for realtime). The ledger's `/ready` also reports whether its two local copies are fresh | ADR 0028, Decision; `backend/shared/readiness.py`; `backend/ledger_service/main.py:177-187` |
| Every service serves `/metrics` for Prometheus | `backend/shared/http_metrics.py:90-103` |
| Grafana Alloy on each server ships metrics, logs and traces to Grafana Cloud's free tier, when the deploy is given a Grafana token. It runs under a Compose profile and cannot fail a deploy | `deploy/production/compose.yml:183-208`; `docs/deployment.md`, "Alloy cannot break a deploy" |
| A dashboard and five alerts, imported into Grafana Cloud on 2026-11-04: 5xx ratio, p95 over 1 s, the close sweep behind, a service down or not ready per server, and the login limiter off. Alerts go by email | `deploy/grafana/alerts.yaml`; `deploy/grafana/dashboard.json`; `docs/deployment.md`, "Monitoring: dashboard and alerts" |
| The gateway answers 404 to the internet for `/ready`, `/metrics` and every `/internal/` path | `deploy/production/gateway/private-paths.inc` |
| A failed deploy notifies Telegram | `.github/workflows/deploy.yml:139-142` |
| Logs rotate at 50 MB per container (10 MB × 5) | `deploy/production/compose.yml:23-30` |
| Checked live, 2026-11-08: `auth.` `/ready`, `ledger.` `/metrics`, `auth.` `/internal/users/suspended` and `market.` `/internal/markets/published` each answer 404 | `curl` |

## TLS

| Fact | Source |
|---|---|
| The load balancer ends TLS on 443 and sends HTTP to port 80 on each server | `infra/terraform/load_balancer.tf:32-38` |
| A Let's Encrypt certificate that DigitalOcean issues and renews | `infra/terraform/load_balancer.tf:8-13` |
| It names the domain and the five service subdomains | `infra/terraform/load_balancer.tf:4-5,13` |
| Port 80 redirects to HTTPS | `infra/terraform/load_balancer.tf:40-47` |
| Services reach Postgres with TLS required (`ssl=require`; `sslmode=require` for the admin URL) | `infra/terraform/outputs.tf:13,20,57` |
| The login cookie is `Secure` | `deploy/production/compose.yml:102` |
| What only a real deploy proves: the DigitalOcean resources, the certificate, TLS to the managed databases and DigitalOcean's own pools | `docs/deployment.md:710-712` |
| Checked live, 2026-11-02: issuer Let's Encrypt, six names, expires 2027-01-30; `http://predictsmu.xyz/` answers 307 to `https://` | `openssl s_client`, `curl` |

## Domain layout

| Name | What answers | Source |
|---|---|---|
| `predictsmu.xyz` | The frontend, served by the gateway | `deploy/production/gateway/default.conf.template:81-101` |
| `auth.` `market.` `ledger.` `audit.` | That service, with its `/docs` | `deploy/production/gateway/default.conf.template:103-145`; `docs/deployment.md:21-25` |
| `realtime.` | Live prices at `wss://realtime.<domain>/ws/prices` | `deploy/production/gateway/default.conf.template:147-163`; `docs/deployment.md:21-25` |

| Fact | Source |
|---|---|
| Every name is an A record to the load balancer, TTL 300 | `infra/terraform/dns.tf:7-14` |
| DigitalOcean runs the domain's DNS, which is what lets it issue the certificate | `infra/terraform/dns.tf:1-5`; `infra/terraform/outputs.tf:45-48` |
| The login cookie names the parent domain, so every subdomain receives it | `deploy/production/compose.yml:103-105`; ADR 0026:52-54 |
| The gateway image is built for one domain, because Vite inlines the service URLs | `deploy/production/gateway/Dockerfile:6-7,14-20`; ADR 0026:114-115 |
| Checked live, 2026-11-08: the frontend and `auth.` `/health` answer 200 (all five `/health` paths did on 2026-11-02) | `curl` |

## Network and secrets

| Fact | Source |
|---|---|
| A server's firewall admits port 80 from the load balancer only | `infra/terraform/servers.tf:35-39` |
| It also admits port 22 from anywhere, keys only, because GitHub's runners have no fixed address | `infra/terraform/servers.tf:29-30,41-45`; `infra/terraform/cloud-init.yaml.tftpl:27-31` |
| Postgres and Valkey accept only servers tagged `cs464-app`, over the private network | `infra/terraform/databases.tf:1-2,19-25,39-45`; `infra/terraform/outputs.tf:13,20,121` |
| Terraform generates the application's secrets: the role passwords, `JWT_SECRET`, the service key, and the SSH keys | `infra/terraform/secrets.tf:5-35` |
| DigitalOcean makes the `doadmin` password and the Valkey URL, and Terraform reads them | `infra/terraform/outputs.tf:55-59,120-123` |
| `infra/push-secrets.sh` copies them to the GitHub `production` environment, which accepts `main` only | `infra/push-secrets.sh:34-43` |
| The deploy logs in to GHCR with the run's own `GITHUB_TOKEN`, which is not in the environment | `.github/workflows/deploy.yml:110`; `deploy/production/release.sh:178-179` |
| `release.sh` writes one `app.env` per server on every deploy, under `umask 077` | `deploy/production/release.sh:111-145,177` |
| Each role's password is in `app.env`, inside that role's pooled and direct URLs. With Grafana on, so is the Grafana token | `deploy/production/release.sh:124-131,135-144`; `infra/terraform/outputs.tf:13,20` |
| The admin URL stays out of `app.env`, and reaches the first server on stdin, for the migrate step only | `deploy/production/release.sh:147-155,186` |
| `ensure-database.sh` hands the admin connection to `psql` as libpq variables, never on a command line (#611, closed) | `deploy/production/ensure-database.sh:26-31,83-92` |
