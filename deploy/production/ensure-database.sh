#!/usr/bin/env bash
# [5.3] #19. Ensure the production database has the four service roles, their
# current passwords, and the schemas and grants of sql/02-schemas.sql. Safe to
# run on every deploy: it creates what is missing and changes nothing that is
# already right, and all of it in one transaction, so a run that fails
# partway changes nothing. migrate.sh runs it before the migrate steps.
#
# Reads from the environment, and writes none of it to disk:
#   DATABASE_ADMIN_URL   the managed database's admin connection (doadmin)
#   AUTH_DB_PASSWORD, MARKET_DB_PASSWORD, LEDGER_DB_PASSWORD, AUDIT_DB_PASSWORD
#   DOCKER_NETWORK       optional; the network psql runs on (the test sets it)
#
# Expects sql/ beside it, holding ensure-roles.sql and 02-schemas.sql (the
# deploy copies the latter from the repository's sql/).
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)

: "${DATABASE_ADMIN_URL:?DATABASE_ADMIN_URL is required}"
: "${AUTH_DB_PASSWORD:?AUTH_DB_PASSWORD is required}"
: "${MARKET_DB_PASSWORD:?MARKET_DB_PASSWORD is required}"
: "${LEDGER_DB_PASSWORD:?LEDGER_DB_PASSWORD is required}"
: "${AUDIT_DB_PASSWORD:?AUDIT_DB_PASSWORD is required}"

echo "ensure-database: roles, schemas and grants"
# `-e NAME` with no value passes the variable through without putting the
# secret on a command line, where `ps` would show it.
docker run --rm \
  --network "${DOCKER_NETWORK:-bridge}" \
  -e DATABASE_ADMIN_URL \
  -e AUTH_DB_PASSWORD -e MARKET_DB_PASSWORD -e LEDGER_DB_PASSWORD -e AUDIT_DB_PASSWORD \
  -v "$here/sql:/sql:ro" \
  postgres:17.2-alpine \
  sh -c 'psql "$DATABASE_ADMIN_URL" --quiet --single-transaction -v ON_ERROR_STOP=1 -f /sql/ensure-roles.sql -f /sql/02-schemas.sql'
