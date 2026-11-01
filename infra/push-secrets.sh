#!/usr/bin/env bash
# [5.3] #19. Copy what Terraform generated into the GitHub "production"
# environment, which is where the deploy workflow reads it from. Run once
# after the first `infra/tf apply`, and again after any apply that changes a
# server or a secret. Safe to repeat.
#
#   infra/push-secrets.sh
#
# Needs `gh` logged in as an admin of the repository, and the same
# DIGITALOCEAN_TOKEN and TF_VAR_domain as `infra/tf`. Every value is piped
# into `gh` on stdin, so none appears on a command line or in the terminal.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
environment=production

output() {
  "$here/tf" output -raw "$1"
}

set_secret() {
  # set_secret <GitHub secret name> <Terraform output name>
  output "$2" | gh secret set "$1" --env "$environment"
  echo "  secret    $1"
}

set_variable() {
  gh variable set "$1" --env "$environment" --body "$2"
  echo "  variable  $1=$2"
}

echo "ensuring the GitHub environment \"$environment\" exists"
gh api --silent -X PUT "repos/{owner}/{repo}/environments/$environment"

set_secret DEPLOY_SSH_KEY deploy_private_key
set_secret DEPLOY_KNOWN_HOSTS known_hosts
set_secret DATABASE_ADMIN_URL database_admin_url
set_secret AUTH_DATABASE_URL auth_database_url
set_secret MARKET_DATABASE_URL market_database_url
set_secret LEDGER_DATABASE_URL ledger_database_url
set_secret AUDIT_DATABASE_URL audit_database_url
set_secret AUTH_DB_PASSWORD auth_db_password
set_secret MARKET_DB_PASSWORD market_db_password
set_secret LEDGER_DB_PASSWORD ledger_db_password
set_secret AUDIT_DB_PASSWORD audit_db_password
set_secret REDIS_URL redis_url
set_secret JWT_SECRET jwt_secret
set_secret LEDGER_SERVICE_KEY ledger_service_key

set_variable APP_HOSTS "$(output app_hosts)"
set_variable PRODUCTION_DOMAIN "$(output domain)"
echo "done"
