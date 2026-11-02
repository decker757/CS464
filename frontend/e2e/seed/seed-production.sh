#!/usr/bin/env bash
# #609. Seeds the production site with the demo markets: `npm run
# seed:production`, from frontend/, on the machine that ran `infra/tf apply`.
#
# Everything comes from Terraform's outputs, so nothing is typed but the
# domain, which is the confirmation. The deploy key and host keys go to a
# temporary directory that is removed afterwards; the admin URL stays in this
# process's environment. Each seeded account's password is written to
# SEED_ACCOUNTS_FILE (default ~/cs464-production-accounts.txt), which must not
# exist yet. docs/deployment.md, "Seeding the demo markets".
set -euo pipefail

frontend=$(cd "$(dirname "$0")/../.." && pwd)
tf="$frontend/../infra/tf"

domain=$("$tf" output -raw domain)
first_server=$("$tf" output -raw app_hosts | cut -d' ' -f1)
accounts=${SEED_ACCOUNTS_FILE:-$HOME/cs464-production-accounts.txt}

if [ -e "$accounts" ]; then
  echo "Refusing: $accounts already exists, so the site may have been seeded before." >&2
  echo "Move it somewhere safe first, or set SEED_ACCOUNTS_FILE." >&2
  exit 1
fi

echo "This seeds https://$domain with the demo markets, traders and two admins."
echo "It is permanent: ledger entries are never deleted. It takes about three minutes."
read -r -p "Type the domain to go on: " confirmed

secrets=$(mktemp -d)
trap 'rm -rf "$secrets"' EXIT
(umask 077 && "$tf" output -raw deploy_private_key > "$secrets/deploy_key")
"$tf" output -raw known_hosts > "$secrets/known_hosts"

export SEED_TARGET=production
export SEED_DOMAIN="$domain"
export SEED_CONFIRMED_DOMAIN="$confirmed"
export SEED_SQL_HOST="$first_server"
export SEED_SSH_KEY="$secrets/deploy_key"
export SEED_KNOWN_HOSTS="$secrets/known_hosts"
export SEED_ACCOUNTS_FILE="$accounts"
DATABASE_ADMIN_URL=$("$tf" output -raw database_admin_url)
export DATABASE_ADMIN_URL
export VITE_API_URL="https://auth.$domain"
export VITE_MARKET_API_URL="https://market.$domain"
export VITE_LEDGER_API_URL="https://ledger.$domain"

cd "$frontend"
npx playwright test --config e2e/seed/seed.config.ts
