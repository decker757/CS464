#!/usr/bin/env bash
# [5.4] #20. Runs loadtest/trade.js in k6's own image, so nothing needs
# installing. docs/load-test.md is the walkthrough.
#
#   DOMAIN=<domain> MARKET_ID=<id> ADMIN_IDENTIFIER=<admin username> loadtest/run.sh
#
# Or AUTH_URL, MARKET_URL and LEDGER_URL instead of DOMAIN, for a local stack. USERS,
# DURATION and LOGIN_SPACING_SECONDS are optional (trade.js has the defaults).
#
# Reads two password files, which never become arguments:
#   ~/.cs464-loadtest-admin-password   the admin's; you write it
#   ~/.cs464-loadtest-trader-password  the 50 traders' one shared password;
#                                      made here on the first run, then reused
# Prints k6's summary and keeps a copy in loadtest/results/.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
admin_password_file=${ADMIN_PASSWORD_FILE:-$HOME/.cs464-loadtest-admin-password}
trader_password_file=${TRADER_PASSWORD_FILE:-$HOME/.cs464-loadtest-trader-password}

if [[ ! -s $admin_password_file ]]; then
  echo "run.sh: put the admin's password in $admin_password_file first" >&2
  exit 1
fi
if [[ ! -s $trader_password_file ]]; then
  (umask 077 && openssl rand -hex 24 > "$trader_password_file")
  echo "run.sh: made the traders' password, in $trader_password_file"
fi

mkdir -p "$here/results"
result="$here/results/$(date -u +%Y%m%dT%H%M%SZ).txt"
# `-e NAME` with no value passes the variable through if it is set. tee -i
# ignores Ctrl-C, so k6's summary and teardown still reach the terminal.
docker run --rm -i \
  -e DOMAIN -e AUTH_URL -e MARKET_URL -e LEDGER_URL -e MARKET_ID -e ADMIN_IDENTIFIER \
  -e USERS -e DURATION -e LOGIN_SPACING_SECONDS \
  -v "$here/trade.js:/scripts/trade.js:ro" \
  -v "$admin_password_file:/secrets/admin-password:ro" \
  -v "$trader_password_file:/secrets/trader-password:ro" \
  --add-host host.docker.internal:host-gateway \
  grafana/k6:2.3.0 run /scripts/trade.js 2>&1 | tee -i "$result"
echo "run.sh: saved to $result"
