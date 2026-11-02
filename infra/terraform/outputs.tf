# What push-secrets.sh copies into GitHub. Each is a string, so
# `terraform output -raw <name>` prints it as is.

locals {
  postgres          = digitalocean_database_cluster.postgres
  postgres_location = "${local.postgres.private_host}:${local.postgres.port}/${digitalocean_database_db.cs464.name}"
  database_urls = {
    for service, password in random_password.database :
    service => "postgresql+asyncpg://${service}_svc:${password.result}@${local.postgres_location}?ssl=require"
  }
}

output "app_hosts" {
  description = "The app servers' public addresses, space-separated, in the order the deploy rolls them."
  value       = join(" ", digitalocean_droplet.app[*].ipv4_address)
}

output "known_hosts" {
  description = "An SSH known_hosts file naming every app server's host key."
  value = join("\n", [
    for index, server in digitalocean_droplet.app :
    "${server.ipv4_address} ${trimspace(tls_private_key.host[index].public_key_openssh)}"
  ])
}

output "domain" {
  value = var.domain
}

output "load_balancer_ip" {
  value = digitalocean_loadbalancer.cs464.ip
}

output "nameservers" {
  description = "Set these at the registrar where the domain was bought."
  value       = "ns1.digitalocean.com ns2.digitalocean.com ns3.digitalocean.com"
}

output "deploy_private_key" {
  value     = tls_private_key.deploy.private_key_openssh
  sensitive = true
}

output "database_admin_url" {
  description = "doadmin, for ensure-database.sh only. The services never get it."
  value       = "postgresql://${local.postgres.user}:${local.postgres.password}@${local.postgres_location}?sslmode=require"
  sensitive   = true
}

output "auth_database_url" {
  value     = local.database_urls["auth"]
  sensitive = true
}

output "market_database_url" {
  value     = local.database_urls["market"]
  sensitive = true
}

output "ledger_database_url" {
  value     = local.database_urls["ledger"]
  sensitive = true
}

output "audit_database_url" {
  value     = local.database_urls["audit"]
  sensitive = true
}

output "auth_db_password" {
  value     = random_password.database["auth"].result
  sensitive = true
}

output "market_db_password" {
  value     = random_password.database["market"].result
  sensitive = true
}

output "ledger_db_password" {
  value     = random_password.database["ledger"].result
  sensitive = true
}

output "audit_db_password" {
  value     = random_password.database["audit"].result
  sensitive = true
}

output "redis_url" {
  value     = digitalocean_database_cluster.cache.private_uri
  sensitive = true
}

output "jwt_secret" {
  value     = random_password.jwt_secret.result
  sensitive = true
}

output "ledger_service_key" {
  value     = random_password.ledger_service_key.result
  sensitive = true
}
