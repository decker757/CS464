# §1.2 and §2.2 Infrastructure facts — draft

Facts for the team to write from, each with the file it comes from. Paths are relative to the repository root. The "Checked live" lines are read from the running site on 2026-11-02, not from a file. ADR 0026 is `docs/adr/0026-production-on-digitalocean.md`.

## Where it runs

| Fact | Value | Source |
|---|---|---|
| Live URL | https://predictsmu.xyz | repository variable `PRODUCTION_DOMAIN` (`gh variable list`); `infra/terraform/variables.tf:2` |
| Provider and region | DigitalOcean, `sgp1` (Singapore) | ADR 0026:33-35; `infra/terraform/variables.tf:6-10` |
| Described in | Terraform, under `infra/terraform/` | ADR 0026:33-34 |
| Private network | VPC `10.64.0.0/20` | `infra/terraform/network.tf:3-7` |

## Monthly cost

DigitalOcean's list prices when `docs/deployment.md` was written. That page says to check the console for today's. It is not a bill.

| Part | Plan | US$ a month | Source |
|---|---|---|---|
| 2 app servers | `s-1vcpu-2gb` | 24 | `docs/deployment.md:226` |
| Load balancer | 1 node | 12 | `docs/deployment.md:227` |
| Postgres | `db-s-1vcpu-2gb`, chosen for its connection limit | 30 | `docs/deployment.md:228` |
| Valkey | `db-s-1vcpu-1gb` | 15 | `docs/deployment.md:229` |
| **Total** | | **about 81**, plus the domain | `docs/deployment.md:230`; ADR 0026:104-106 |

| Other cost | Fact | Source |
|---|---|---|
| Domain | About US$10 a year, or a free one from the Student Pack | `docs/deployment.md:51-52` |
| Images on GHCR | Free for a public repository | `docs/deployment.md:232` |
| A database standby | Double the database's cost; not bought | ADR 0026:95-97 |

## Student Pack credit

| Fact | Source |
|---|---|
| The GitHub Student Developer Pack includes DigitalOcean credit | `docs/deployment.md:49-50`; ADR 0026:34-35 |
| Claim it first, so the first bill comes out of it | `docs/deployment.md:49-50` |
| The amount, and whether it was claimed, are not recorded in the repository | a search of `docs/` and `infra/` finds no figure |

## Server and database sizes

| Part | Setting | Value | Source |
|---|---|---|---|
| App servers | `app_server_count` | 2 | `infra/terraform/variables.tf:12-15` |
| App servers | `app_server_size` | `s-1vcpu-2gb`: 1 vCPU, 2 GB | `infra/terraform/variables.tf:23-27` |
| App servers | image | `ubuntu-24-04-x64` | `infra/terraform/servers.tf:9` |
| Postgres | `database_size` | `db-s-1vcpu-2gb`: 2 GB, "for its connection limit rather than its memory" | `infra/terraform/variables.tf:29-33` |
| Postgres | engine, version, nodes | `pg`, 17, 1 | `infra/terraform/databases.tf:6-10` |
| Valkey | `cache_size` | `db-s-1vcpu-1gb` | `infra/terraform/variables.tf:35-39` |
| Valkey | engine, version, nodes | `valkey`, 8, 1 | `infra/terraform/databases.tf:30-35` |
| Load balancer | health check | `/lb-health` on port 80, every 3 s, 3 failures or 2 successes | `infra/terraform/load_balancer.tf:49-57` |
| Load balancer | idle timeout | 600 s | `infra/terraform/load_balancer.tf:61` |
| Connections | ceiling on the 2 GB plan | about 47; DigitalOcean's figure, which the repository cannot check | ADR 0026:83-87 |
| Connections | pool + overflow per server | auth 3+1, market 3+2, ledger 6+3, audit 1+1 = 20 | `deploy/production/compose.yml:70-75` |
| Connections | at full overflow | 40 for two servers. The budget leaves 3 for the migrate steps, which run one after another | `docs/deployment.md:209-210`; `deploy/production/migrate.sh:21-25` |

## Backups

| Fact | Source |
|---|---|
| Postgres relies on DigitalOcean's daily backups | ADR 0026:95-97 |
| Terraform sets no backup option: they are the managed service's own, and the retention period is not in the repository | `infra/terraform/databases.tf:4-12` |
| There is no standby node | `infra/terraform/databases.tf:10`; ADR 0026:96-97 |
| The repository records no restore having been tried | a search of `docs/deployment.md` and ADR 0026 for "restore" finds nothing |
| The app servers hold no data, so they have nothing to back up | `infra/terraform/servers.tf:1-2`; `deploy/production/compose.yml:3-4` |
| Valkey "holds nothing worth keeping" | `infra/terraform/databases.tf:27-28` |
| `infra/tf destroy` deletes the database too | `docs/deployment.md:233-234` |
| `terraform.tfstate` holds every generated secret. It is on the machine that ran `apply`, gitignored, and not backed up by anything in the repository | `infra/terraform/versions.tf:4-6`; `docs/deployment.md:80-81` |

## TLS

| Fact | Source |
|---|---|
| The load balancer ends TLS on 443 and sends HTTP to port 80 on each server | `infra/terraform/load_balancer.tf:32-38` |
| A Let's Encrypt certificate that DigitalOcean issues and renews | `infra/terraform/load_balancer.tf:8-13` |
| It names the domain and the five service subdomains | `infra/terraform/load_balancer.tf:4-5,13` |
| Port 80 redirects to HTTPS | `infra/terraform/load_balancer.tf:40-47` |
| Services reach Postgres with TLS required (`ssl=require`; `sslmode=require` for the admin URL) | `infra/terraform/outputs.tf:9,46` |
| The login cookie is `Secure` | `deploy/production/compose.yml:85` |
| What only a real deploy proves: the certificate and TLS to the managed databases | `docs/deployment.md:250-251` |
| Checked live: issuer Let's Encrypt, six names, expires 2027-01-30; `http://predictsmu.xyz/` answers 307 to `https://` | `openssl s_client`, `curl` |

## Domain layout

| Name | What answers | Source |
|---|---|---|
| `predictsmu.xyz` | The frontend, served by the gateway | `deploy/production/gateway/default.conf.template:54-74` |
| `auth.` `market.` `ledger.` `audit.` | That service, with its `/docs` | `deploy/production/gateway/default.conf.template:76-114`; `docs/deployment.md:23` |
| `realtime.` | Live prices at `wss://realtime.<domain>/ws/prices` | `deploy/production/gateway/default.conf.template:116-131`; `docs/deployment.md:24` |

| Fact | Source |
|---|---|
| Every name is an A record to the load balancer, TTL 300 | `infra/terraform/dns.tf:7-14` |
| DigitalOcean runs the domain's DNS, which is what lets it issue the certificate | `infra/terraform/dns.tf:1-5`; `infra/terraform/outputs.tf:34-37` |
| The login cookie names the parent domain, so every subdomain receives it | `deploy/production/compose.yml:86-88`; ADR 0026:50-52 |
| The gateway image is built for one domain, because Vite inlines the service URLs | `deploy/production/gateway/Dockerfile:6-7,14-20`; ADR 0026:102-103 |
| Checked live: the frontend and all five `/health` paths answer 200 | `curl` |

## Network and secrets

| Fact | Source |
|---|---|
| A server's firewall admits port 80 from the load balancer only | `infra/terraform/servers.tf:35-39` |
| It also admits port 22 from anywhere, keys only, because GitHub's runners have no fixed address | `infra/terraform/servers.tf:29-30,41-45`; `infra/terraform/cloud-init.yaml.tftpl:27-31` |
| Postgres and Valkey accept only servers tagged `cs464-app`, over the private network | `infra/terraform/databases.tf:1-2,19-25,39-45`; `infra/terraform/outputs.tf:6,91` |
| Terraform generates the application's secrets: the role passwords, `JWT_SECRET`, the service key, and the SSH keys | `infra/terraform/secrets.tf:5-36` |
| DigitalOcean makes the `doadmin` password and the Valkey URL, and Terraform reads them | `infra/terraform/outputs.tf:45-46,91` |
| The deploy logs in to GHCR with the run's own `GITHUB_TOKEN`, which is not in the environment | `.github/workflows/deploy.yml:110`; `deploy/production/release.sh:137` |
| `infra/push-secrets.sh` copies them to the GitHub `production` environment, which accepts `main` only | `infra/push-secrets.sh:32-56` |
| `release.sh` writes one `app.env` per server on every deploy | `deploy/production/release.sh:85-104,136` |
| Each role's password is in `app.env`, inside that role's URL, written under `umask 077` | `deploy/production/release.sh:97-100,136`; `infra/terraform/outputs.tf:9` |
| The admin URL stays out of `app.env`, and reaches a server on stdin only | `deploy/production/release.sh:37,106-114` |
