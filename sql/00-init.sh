#!/bin/bash
# Applied by the Postgres entrypoint on FIRST initialisation of the data
# volume only. Re-running it means `docker compose down -v`, which destroys
# development data.
#
# Lives in /docker-entrypoint-initdb.d; the .sql files it applies are mounted
# separately at /sql so the entrypoint does not also run them directly without
# the variables they need.
set -euo pipefail

: "${AUTH_DB_PASSWORD:?AUTH_DB_PASSWORD is required}"
: "${LEDGER_DB_PASSWORD:?LEDGER_DB_PASSWORD is required}"

SQL_DIR=/sql
MAIN_DB="${POSTGRES_DB:-cs464}"
TEST_DB="${TEST_DB_NAME:-cs464_test}"

run() { psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" "$@"; }

# Roles are cluster-wide, so once is enough.
run --dbname "$MAIN_DB" \
    -v auth_password="$AUTH_DB_PASSWORD" \
    -v ledger_password="$LEDGER_DB_PASSWORD" \
    -f "$SQL_DIR/01-roles.sql"

# A separate database for the suite, so it can truncate without touching
# development data.
run --dbname "$MAIN_DB" -c "CREATE DATABASE \"${TEST_DB}\""

# Schemas and grants are per database.
for db in "$MAIN_DB" "$TEST_DB"; do
  run --dbname "$db" -v db_name="$db" -f "$SQL_DIR/02-schemas.sql"
done

echo "sql/: roles, schemas and grants applied to ${MAIN_DB} and ${TEST_DB}"
