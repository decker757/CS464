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

echo "ensuring the GitHub environment \"$environment\" exists, open to main only"
# Only a run on `main` may use this environment, so a workflow started from
# any other branch can neither deploy nor read these secrets. ADR 0026.
gh api --silent -X PUT "repos/{owner}/{repo}/environments/$environment" --input - <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
policies="repos/{owner}/{repo}/environments/$environment/deployment-branch-policies"
if ! gh api "$policies" --jq '.branch_policies[].name' | grep -qx main; then
  gh api --silent -X POST "$policies" -f name=main -f type=branch
fi

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

# The domain is not a secret, and the image build needs it. Set for the whole
# repository, so the build never has to enter the production environment,
# which holds the secrets.
domain=$(output domain)
gh variable set PRODUCTION_DOMAIN --body "$domain"
echo "  variable  PRODUCTION_DOMAIN=$domain (repository)"
echo "done"
