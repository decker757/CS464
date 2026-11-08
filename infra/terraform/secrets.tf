# The secrets Terraform generates, so no person types or chooses one.
# DigitalOcean makes the rest: the database admin password and the Valkey
# URL (outputs.tf). push-secrets.sh copies them all into the GitHub
# "production" environment. REGISTRY_TOKEN is the deploy run's own token.

resource "random_password" "jwt_secret" {
  length  = 64
  special = false
}

# ADR 0021: at least 32 characters.
resource "random_password" "ledger_service_key" {
  length  = 48
  special = false
}

# One login role per service, as in sql/01-roles.sql. ensure-database.sh
# creates the roles and keeps them on these passwords.
resource "random_password" "database" {
  for_each = toset(["auth", "market", "ledger", "audit"])
  length   = 40
  special  = false
}

# The key the deploy logs in to the app servers with.
resource "tls_private_key" "deploy" {
  algorithm = "ED25519"
}

# Each server's SSH host key, made here rather than on first boot, so the
# deploy can check it is talking to the right server from the very first
# connection, instead of trusting whatever answers.
resource "tls_private_key" "host" {
  count     = var.app_server_count
  algorithm = "ED25519"
}
