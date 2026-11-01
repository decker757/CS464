#!/usr/bin/env bash
# [5.3] #19. Runs deploy/production on this machine the way a server runs it,
# and checks what a deploy depends on. No cloud account is needed. CI runs it
# (ci-deploy.yml), and so can you, with Docker running:
#
#   deploy/production/test/stack-test.sh
#
# It builds the six images and pushes them to a throwaway local registry, so
# the scripts pull exactly as they do from GHCR. It stands in for the managed
# database with a Postgres whose admin is NOT a superuser, as DigitalOcean's
# doadmin is not. Then it runs migrate.sh and roll.sh and checks:
#
#   - the frontend is served on the domain, and was built for it
#   - each service answers on its own subdomain
#   - a trader can register and log in, and the login cookie names the domain
#   - the ledger, market service and database work together through the gateway
#   - the price websocket upgrades through the gateway, for the site's origin only
#   - draining makes /lb-health answer 503, and undraining brings it back
#   - a release that cannot be pulled leaves the server untouched
#   - a release that starts unhealthy is rolled back to the one before
#   - a good release replaces the one before
set -euo pipefail

repo=$(cd "$(dirname "$0")/../../.." && pwd)
domain=cs464.test
project=cs464stacktest
registry_port=${REGISTRY_PORT:-15000}
registry=localhost:$registry_port
export GATEWAY_PORT=${GATEWAY_PORT:-18080}
gateway=http://127.0.0.1:$GATEWAY_PORT
work=$(mktemp -d)
failures=0

# --- Helpers -------------------------------------------------------------

random_secret() {
  openssl rand -hex 24
}

pass() { echo "  ok    $1"; }
fail() { echo "  FAIL  $1" >&2; failures=$((failures + 1)); }

expect() {
  local description=$1
  shift
  if "$@"; then pass "$description"; else fail "$description"; fi
}

status_of() {
  # The HTTP status of a GET to the gateway, addressed to one host name.
  curl -s -o /dev/null -w '%{http_code}' -H "Host: $1" "$gateway$2"
}

has_starting_grant() {
  curl -fsS -H "Host: ledger.$domain" -H "Authorization: Bearer $token" \
    "$gateway/ledger/portfolio/me" | grep -q starting_credits
}

roll_is_refused() {
  ! "$work/roll.sh" "$1"
}

every_image_is_at() {
  # Every image this stack pulled from the registry carries tag $1. The
  # stand-in Postgres and Redis come from Docker Hub and are not counted.
  local others
  others=$(compose ps --format '{{.Image}}' | grep "^$registry/" | grep -vc ":$1\$" || true)
  test "$others" = 0
}

compose() {
  (cd "$work" && docker compose --env-file app.env --env-file release.env "$@")
}

cleanup() {
  echo "cleaning up"
  (cd "$work" && IMAGE_TAG=none docker compose --env-file app.env down -v --remove-orphans) > /dev/null 2>&1 || true
  docker rm -f "$project-registry" > /dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

# --- Images, in a local registry ----------------------------------------

echo "registry on $registry"
docker run -d --rm --name "$project-registry" -p "$registry_port:5000" registry:2 > /dev/null

echo "building images (tag: one)"
for service in auth market ledger audit realtime; do
  docker build -q -t "$registry/cs464-$service:one" \
    -f "$repo/backend/${service}_service/Dockerfile" "$repo/backend" > /dev/null
done
docker build -q -t "$registry/cs464-gateway:one" --build-arg DOMAIN=$domain \
  -f "$repo/deploy/production/gateway/Dockerfile" "$repo" > /dev/null

push_as() {
  # push_as <tag> <service>: publish release one's image under another tag.
  docker tag "$registry/cs464-$2:one" "$registry/cs464-$2:$1"
  docker push -q "$registry/cs464-$2:$1" > /dev/null
}
for service in auth market ledger audit realtime gateway; do
  push_as one "$service"
  push_as two "$service"
  push_as broken "$service"
done
# "broken" is release one, except that auth's image is the realtime service,
# which cannot start without REDIS_URL. Its health check never passes.
docker tag "$registry/cs464-realtime:one" "$registry/cs464-auth:broken"
docker push -q "$registry/cs464-auth:broken" > /dev/null

# --- A server's directory, as the deploy lays it out --------------------

cp "$repo"/deploy/production/{compose.yml,roll.sh,migrate.sh,ensure-database.sh} "$work/"
mkdir -p "$work/sql"
cp "$repo/deploy/production/sql/ensure-roles.sql" "$repo/sql/02-schemas.sql" "$work/sql/"
cp "$repo/deploy/production/test/backing.yml" "$work/"

admin_password=$(random_secret)
export TEST_POSTGRES_PASSWORD
TEST_POSTGRES_PASSWORD=$(random_secret)
export AUTH_DB_PASSWORD MARKET_DB_PASSWORD LEDGER_DB_PASSWORD AUDIT_DB_PASSWORD
AUTH_DB_PASSWORD=$(random_secret)
MARKET_DB_PASSWORD=$(random_secret)
LEDGER_DB_PASSWORD=$(random_secret)
AUDIT_DB_PASSWORD=$(random_secret)

cat > "$work/app.env" <<EOF
COMPOSE_PROJECT_NAME=$project
COMPOSE_FILE=compose.yml:backing.yml
IMAGE_REGISTRY=$registry
DOMAIN=$domain
JWT_SECRET=$(random_secret)
LEDGER_SERVICE_KEY=$(random_secret)
AUTH_DATABASE_URL=postgresql+asyncpg://auth_svc:$AUTH_DB_PASSWORD@postgres:5432/cs464
MARKET_DATABASE_URL=postgresql+asyncpg://market_svc:$MARKET_DB_PASSWORD@postgres:5432/cs464
LEDGER_DATABASE_URL=postgresql+asyncpg://ledger_svc:$LEDGER_DB_PASSWORD@postgres:5432/cs464
AUDIT_DATABASE_URL=postgresql+asyncpg://audit_svc:$AUDIT_DB_PASSWORD@postgres:5432/cs464
REDIS_URL=redis://redis:6379/0
LEADERBOARD_REFRESH_ENABLED=true
TEST_POSTGRES_PASSWORD=$TEST_POSTGRES_PASSWORD
EOF
echo "IMAGE_TAG=none" > "$work/release.env"

echo "starting the stand-in database and Redis"
compose up -d --wait postgres redis > /dev/null
compose exec -T postgres psql -q -U postgres -v ON_ERROR_STOP=1 \
  -c "CREATE ROLE doadmin LOGIN CREATEROLE PASSWORD '$admin_password'" \
  -c "CREATE DATABASE cs464 OWNER doadmin" > /dev/null
rm "$work/release.env"   # a new server has no release yet

# --- Deploy release one --------------------------------------------------

export DATABASE_ADMIN_URL="postgresql://doadmin:$admin_password@postgres:5432/cs464"
export DOCKER_NETWORK="${project}_default"
export DRAIN_SECONDS=1 WAIT_SECONDS=90

echo "migrate.sh one"
"$work/migrate.sh" one
echo "migrate.sh one, again: it must be safe to repeat"
"$work/migrate.sh" one
echo "roll.sh one"
"$work/roll.sh" one

echo "checks"

expect "the frontend is served on $domain" \
  sh -c "curl -fsS -H 'Host: $domain' $gateway/ | grep -q '<div id=\"root\">'"
expect "a client-side route gets the app, not a 404" \
  test "$(status_of $domain /markets/anything)" = 200
bundle=$(curl -fsS -H "Host: $domain" "$gateway/" | grep -o '/assets/index-[^"]*\.js' | head -1)
expect "the bundle was built for https://auth.$domain" \
  sh -c "curl -fsS -H 'Host: $domain' $gateway$bundle | grep -q 'https://auth.$domain'"

for service in auth market ledger audit realtime; do
  expect "$service answers on $service.$domain" test "$(status_of "$service.$domain" /health)" = 200
done
expect "an unknown host name gets 404" test "$(status_of nobody.test /health)" = 404

username="stack$(date +%s)"
password=$(random_secret)
register_status=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: auth.$domain" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$username\",\"email\":\"$username@example.com\",\"password\":\"$password\"}" \
  "$gateway/auth/register")
expect "a trader registers through the gateway" test "$register_status" = 201

login_headers=$(mktemp)
token=$(curl -fsS -D "$login_headers" -H "Host: auth.$domain" -H 'Content-Type: application/json' \
  -d "{\"identifier\":\"$username\",\"password\":\"$password\"}" \
  "$gateway/auth/login" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tokens"]["access_token"])')
expect "the login cookie names the parent domain, so every subdomain gets it" \
  grep -qi "set-cookie: access_token=.*Domain=$domain" "$login_headers"
expect "the login cookie is Secure" grep -qi "set-cookie: access_token=.*Secure" "$login_headers"

expect "the ledger mints the starting grant on first read" has_starting_grant
expect "the market service reads its schema" \
  test "$(curl -s -o /dev/null -w '%{http_code}' -H "Host: market.$domain" -H "Authorization: Bearer $token" "$gateway/public/markets")" = 200

upgrade_status() {
  # The first status line of a websocket handshake from one origin.
  curl -s -i --max-time 3 -H "Host: realtime.$domain" \
    -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
    -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
    -H "Origin: $1" -H "Cookie: access_token=$token" \
    "$gateway/ws/prices" | head -1 | tr -d '\r' || true
}
socket_opens_from() { upgrade_status "$1" | grep -q ' 101'; }
socket_refused_from() { ! socket_opens_from "$1"; }

expect "the price websocket upgrades through the gateway" socket_opens_from "https://$domain"
expect "another site's page cannot open the websocket" socket_refused_from "https://elsewhere.test"

expect "/lb-health is 200 while serving" test "$(status_of 127.0.0.1 /lb-health)" = 200
touch "$work/drain/on"
expect "/lb-health is 503 while drained" test "$(status_of 127.0.0.1 /lb-health)" = 503
rm "$work/drain/on"
expect "/lb-health is 200 again once undrained" test "$(status_of 127.0.0.1 /lb-health)" = 200

# --- Releases that go wrong, and one that goes right ---------------------

echo "roll.sh to a tag nobody pushed"
expect "roll.sh refuses a release it cannot pull" roll_is_refused missing
expect "and the server is still on one" grep -qx 'IMAGE_TAG=one' "$work/release.env"
expect "and still serving" test "$(status_of 127.0.0.1 /lb-health)" = 200

echo "roll.sh to a release whose auth service never becomes healthy"
expect "roll.sh refuses a release that does not come up" roll_is_refused broken
expect "and the server is back on one" grep -qx 'IMAGE_TAG=one' "$work/release.env"
expect "and auth runs release one's image" \
  test "$(compose ps --format '{{.Image}}' auth)" = "$registry/cs464-auth:one"
expect "and it is undrained and serving" test "$(status_of 127.0.0.1 /lb-health)" = 200

echo "roll.sh two"
expect "roll.sh moves to a good release" "$work/roll.sh" two
expect "and the server is on two" grep -qx 'IMAGE_TAG=two' "$work/release.env"
expect "and every service runs release two" every_image_is_at two
expect "and the trader's account survived the roll" has_starting_grant

echo
if [ "$failures" -ne 0 ]; then
  echo "$failures check(s) failed"
  exit 1
fi
echo "every check passed"
