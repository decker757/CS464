# Managed Postgres and Valkey. Neither is reachable from the internet: only
# the app servers, over the private network, may connect.

resource "digitalocean_database_cluster" "postgres" {
  name                 = "cs464-postgres"
  engine               = "pg"
  version              = "17"
  size                 = var.database_size
  region               = var.region
  node_count           = 1
  private_network_uuid = digitalocean_vpc.cs464.id
}

resource "digitalocean_database_db" "cs464" {
  cluster_id = digitalocean_database_cluster.postgres.id
  name       = "cs464"
}

resource "digitalocean_database_firewall" "postgres" {
  cluster_id = digitalocean_database_cluster.postgres.id
  rule {
    type  = "tag"
    value = digitalocean_tag.app.id
  }
}

# The bus the ledger publishes prices to and the realtime service subscribes
# on (ADR 0010). It holds nothing worth keeping, so one node is enough.
resource "digitalocean_database_cluster" "cache" {
  name                 = "cs464-valkey"
  engine               = "valkey"
  version              = "8"
  size                 = var.cache_size
  region               = var.region
  node_count           = 1
  private_network_uuid = digitalocean_vpc.cs464.id
}

resource "digitalocean_database_firewall" "cache" {
  cluster_id = digitalocean_database_cluster.cache.id
  rule {
    type  = "tag"
    value = digitalocean_tag.app.id
  }
}

# #640. One transaction-mode pool (PgBouncer) per service role, so every app
# server shares a fixed number of server connections, however many servers
# run. The 2 GB plan has about 47; these take 38, and the rest stay direct,
# for the three migrate steps, ensure-database.sh and an operator. The
# ledger's is at least every ledger client at full overflow on three servers
# (6 + 2 each, deploy/production/compose.yml), so a trade queued on one
# market's book lock never makes the pooler queue the ledger's other reads
# (#667). Each pool
# logs in as its service's own role, which ensure-database.sh creates, so the
# role must exist before its pool can. docs/deployment.md, "Connection budget".
locals {
  connection_pool_sizes = {
    auth   = 6
    market = 6
    ledger = 24
    audit  = 2
  }
}

resource "digitalocean_database_connection_pool" "service" {
  for_each   = local.connection_pool_sizes
  cluster_id = digitalocean_database_cluster.postgres.id
  name       = "cs464-${each.key}"
  mode       = "transaction"
  size       = each.value
  db_name    = digitalocean_database_db.cs464.name
  user       = "${each.key}_svc"
}
