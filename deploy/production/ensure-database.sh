#!/usr/bin/env bash
# [5.3] #19. Ensure the production database has the four service roles, their
# current passwords, and the schemas and grants of sql/02-schemas.sql. Safe to
# run on every deploy: it creates what is missing and changes nothing that is
# already right, and all of it in one transaction, so a run that fails
# partway changes nothing. migrate.sh runs it before the migrate steps.
#
# Reads from the environment, and writes none of it to disk:
#   DATABASE_ADMIN_URL   the managed database's admin connection (doadmin), as
#                        postgresql://user:password@host[:port]/database[?sslmode=...]
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

# libpq's own variables, from DATABASE_ADMIN_URL, so psql runs with no
# connection argument. Given the URL as an argument, the password would be in
# psql's command line, where `ps` on the server shows it (#611). libpq does not
# read a whole URL from one variable, so it is split here. Bash does the work
# with builtins only, so no step puts the URL in a command line either.
url_decode() {
  # %40 back to @, and so on. printf is a bash builtin.
  printf '%b' "${1//%/\\x}"
}

url=${DATABASE_ADMIN_URL#*://}        # user:password@host:port/database?query
query=
if [[ $url == *\?* ]]; then
  query=${url#*\?}
  url=${url%%\?*}
fi
credentials=${url%@*}                  # up to the last @
location=${url##*@}
host_and_port=${location%%/*}

PGUSER=$(url_decode "${credentials%%:*}")
PGPASSWORD=$(url_decode "${credentials#*:}")
PGHOST=${host_and_port%:*}
PGPORT=5432
if [[ $host_and_port == *:* ]]; then
  PGPORT=${host_and_port##*:}
fi
PGDATABASE=$(url_decode "${location#*/}")
# Only what the URL asks for. Production's says sslmode=require; the stack
# test's stand-in has no TLS, and libpq's default, prefer, is what psql used
# when it was handed the URL.
PGSSLMODE=
if [[ -n $query ]]; then
  IFS='&' read -ra parameters <<< "$query"
  for parameter in "${parameters[@]}"; do
    if [[ $parameter == sslmode=* ]]; then
      PGSSLMODE=${parameter#sslmode=}
    fi
  done
fi

if [[ $DATABASE_ADMIN_URL != *://*:*@*/* || -z $PGUSER || -z $PGPASSWORD || -z $PGHOST || -z $PGDATABASE ]]; then
  # Never echo the URL: it holds the password.
  echo "ensure-database: DATABASE_ADMIN_URL is not postgresql://user:password@host[:port]/database[?sslmode=...]" >&2
  exit 1
fi
export PGUSER PGPASSWORD PGHOST PGPORT PGDATABASE
connection=(-e PGUSER -e PGPASSWORD -e PGHOST -e PGPORT -e PGDATABASE)
if [[ -n $PGSSLMODE ]]; then
  export PGSSLMODE
  connection+=(-e PGSSLMODE)
fi

echo "ensure-database: roles, schemas and grants"
# `-e NAME` with no value passes the variable through without putting the
# secret on a command line. psql is the container's command itself, not run
# through a shell, so nothing expands a secret into its arguments.
docker run --rm \
  --network "${DOCKER_NETWORK:-bridge}" \
  "${connection[@]}" \
  -e AUTH_DB_PASSWORD -e MARKET_DB_PASSWORD -e LEDGER_DB_PASSWORD -e AUDIT_DB_PASSWORD \
  -v "$here/sql:/sql:ro" \
  postgres:17.2-alpine \
  psql --quiet --single-transaction -v ON_ERROR_STOP=1 -f /sql/ensure-roles.sql -f /sql/02-schemas.sql
