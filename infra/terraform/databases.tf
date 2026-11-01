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
