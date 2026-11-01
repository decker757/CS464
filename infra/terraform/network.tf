# A private network for the servers, the database and the cache. The services
# reach Postgres and Valkey over it, never over the internet.
resource "digitalocean_vpc" "cs464" {
  name     = "cs464-${var.region}"
  region   = var.region
  ip_range = "10.64.0.0/20"
}

resource "digitalocean_tag" "app" {
  name = "cs464-app"
}

# Groups everything in the DigitalOcean console.
resource "digitalocean_project" "cs464" {
  name        = "CS464"
  description = "PredictSMU, the CS464 prediction market (#19)."
  purpose     = "Web Application"
  environment = "Production"
  resources = concat(
    digitalocean_droplet.app[*].urn,
    [
      digitalocean_loadbalancer.cs464.urn,
      digitalocean_database_cluster.postgres.urn,
      digitalocean_database_cluster.cache.urn,
      digitalocean_domain.cs464.urn,
    ],
  )
}
