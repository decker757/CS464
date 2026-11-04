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
#   - a good release replaces the one before, which is kept, and nothing older
#   - going back from a release that added a service runs the old release
#     from its own files, without that service, and with today's secrets: a
#     database password changed since the old release was deployed
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

status_becomes() {
  # status_becomes <host> <path> <status>: within ten seconds. A file the
  # test just created or removed can take a moment to reach the container.
  local got
  for _ in $(seq 1 20); do
    got=$(status_of "$1" "$2")
    if [ "$got" = "$3" ]; then
      return 0
    fi
    sleep 0.5
  done
  echo "        $1$2 answered $got, not $3" >&2
  return 1
}

has_starting_grant() {
  curl -fsS -H "Host: ledger.$domain" -H "Authorization: Bearer $token" \
    "$gateway/ledger/portfolio/me" | grep -q starting_credits
}

install_release() {
  # install_release <tag> [compose file]: lay a release out as the deploy
  # does, in releases/<tag>/ with its own files and env.
  local directory="$work/releases/$1"
  mkdir -p "$directory/sql"
  cp "$repo"/deploy/production/{roll.sh,migrate.sh,ensure-database.sh} "$directory/"
  cp "${2:-$repo/deploy/production/compose.yml}" "$directory/compose.yml"
  cp "$repo/deploy/production/sql/ensure-roles.sql" "$repo/sql/02-schemas.sql" "$directory/sql/"
  cp "$repo/deploy/production/test/backing.yml" "$directory/"
  echo "IMAGE_TAG=$1" > "$directory/release.env"
}

current_release() {
  basename "$(cd -P "$work/current" && pwd)"
}

roll_is_refused() {
  # The script must exist, or "refused" would pass for the wrong reason.
  test -x "$work/releases/$1/roll.sh" && ! "$work/releases/$1/roll.sh"
}

compose() {
  # Compose, as the release the server is on.
  (cd -P "$work/current" && docker compose --env-file "$work/app.env" --env-file release.env "$@")
}

is_ready_inside() {
  # Asked from the gateway container, on the private network, where /ready is
  # not hidden. ADR 0028.
  compose exec -T gateway wget -qO- "http://$1:8000/ready" | grep -q '"status":"ready"'
}

every_image_is_at() {
  # Every image this stack pulled from the registry carries tag $1. The
  # stand-in Postgres and Redis come from Docker Hub and are not counted.
  local others
  others=$(compose ps --format '{{.Image}}' | grep "^$registry/" | grep -vc ":$1\$" || true)
  test "$others" = 0
}

no_container_runs_for() {
  test -z "$(docker ps -q --filter "label=com.docker.compose.project=$project" \
    --filter "label=com.docker.compose.service=$1")"
}

releases_kept_are() {
  local names="" directory
  for directory in "$work"/releases/*/; do
    names+="$(basename "$directory") "
  done
  test "$names" = "$1 "
}

image_is_here() {
  docker image inspect "$1" > /dev/null 2>&1
}

cleanup() {
  echo "cleaning up"
  docker compose -p "$project" down -v --remove-orphans > /dev/null 2>&1 || true
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
# "broken" is release one, except that auth's image is built from the real one
# with its command replaced by `sleep`: it keeps auth's own HEALTHCHECK, which
# never passes because nothing listens on 8000.
build_stand_in() {
  # build_stand_in <image to push>
  printf 'FROM %s/cs464-auth:one\nCMD ["sleep", "infinity"]\n' "$registry" \
    | docker build -q -t "$1" - > /dev/null
  docker push -q "$1" > /dev/null
}
build_stand_in "$registry/cs464-auth:broken"
# "three" is broken the same way, and also adds a service, `extra`, whose
# image exists only at tag three. Going back to two with three's files would
# look for cs464-extra:two, which nobody pushed.
for service in market ledger audit realtime gateway; do
  push_as three "$service"
done
build_stand_in "$registry/cs464-auth:three"
build_stand_in "$registry/cs464-extra:three"

# --- A server's directory, as the deploy lays it out --------------------

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
# roll.sh sets the same; compose needs it for this test's own calls too.
export DRAIN_DIR="$work/drain"

# Each release is installed just before it is rolled, as the deploy does:
# a successful roll removes every release but itself and the one before.
install_release one
# Three's compose.yml: release one's, plus the extra service.
cp "$repo/deploy/production/compose.yml" "$work/compose-three.yml"
cat >> "$work/compose-three.yml" <<'YAML'

  extra:
    image: ${IMAGE_REGISTRY:?}/cs464-extra:${IMAGE_TAG:?}
    environment:
      REDIS_URL: ${REDIS_URL:?}
      JWT_SECRET: ${JWT_SECRET:?}
      CORS_ORIGINS: https://${DOMAIN:?}
YAML

backing() {
  # The stand-ins, from release one's directory. They are not part of any
  # release, but they share its compose project and network.
  (cd "$work/releases/one" && docker compose --env-file "$work/app.env" --env-file release.env "$@")
}

echo "starting the stand-in database and Redis"
backing up -d --wait postgres redis > /dev/null
backing exec -T postgres psql -q -U postgres -v ON_ERROR_STOP=1 \
  -c "CREATE ROLE doadmin LOGIN CREATEROLE PASSWORD '$admin_password'" \
  -c "CREATE DATABASE cs464 OWNER doadmin" > /dev/null

# --- Deploy release one --------------------------------------------------

export DATABASE_ADMIN_URL="postgresql://doadmin:$admin_password@postgres:5432/cs464"
export DOCKER_NETWORK="${project}_default"
export DRAIN_SECONDS=1 WAIT_SECONDS=90

echo "migrate.sh, release one"
"$work/releases/one/migrate.sh"
echo "migrate.sh, release one, again: it must be safe to repeat"
"$work/releases/one/migrate.sh"
echo "roll.sh, release one"
"$work/releases/one/roll.sh"

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
  expect "$service's /ready is not public" test "$(status_of "$service.$domain" /ready)" = 404
  expect "$service is ready on the private network" is_ready_inside "$service"
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

# [BE][A-2] #600. Each registration forges a different X-Forwarded-For. If the
# gateway passed them on, every one would count against a different address
# and none would ever be refused.
refused=no
for attempt in $(seq 1 25); do
  name="forge$attempt$(date +%s)"
  status=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: auth.$domain" \
    -H "X-Forwarded-For: 198.51.100.$attempt" -H 'Content-Type: application/json' \
    -d "{\"username\":\"$name\",\"email\":\"$name@example.com\",\"password\":\"$password\"}" \
    "$gateway/auth/register")
  if [ "$status" = 429 ]; then refused=yes; break; fi
done
expect "forged addresses still share one registration limit" test "$refused" = yes

# [BE][5.5] #599. Request IDs: stamped by the gateway, kept from the client,
# and on the services' JSON log lines.
request_id_of() {
  curl -s -D - -o /dev/null -H "Host: auth.$domain" "$@" "$gateway/health" \
    | tr -d '\r' | sed -n 's/^[Xx]-[Rr]equest-[Ii][Dd]: //p'
}
gateway_logged() { test -n "$1" || return 1; compose logs --no-log-prefix gateway 2>/dev/null | grep -q "rid=$1"; }
ledger_logged_json() {
  curl -s -o /dev/null -H "Host: ledger.$domain" -H 'X-Request-ID: stack-test-0002' \
    -H "Authorization: Bearer $token" "$gateway/ledger/portfolio/me"
  compose logs --no-log-prefix ledger 2>/dev/null | grep -q '"request_id": "stack-test-0002"'
}
rid=$(request_id_of)
expect "a response carries a request ID" sh -c "echo '$rid' | grep -qE '^[0-9a-f]{32}$'"
expect "the gateway logged the same ID the service answered with" gateway_logged "$rid"
expect "a client's own request ID comes back unchanged" \
  test "$(request_id_of -H 'X-Request-ID: stack-test-0001')" = stack-test-0001
# 65 characters, one past the services' rule. The gateway must replace it
# itself, or its log line names an ID the service then threw away.
unsafe_rid=$(request_id_of -H "X-Request-ID: $(printf '%65s' '' | tr ' ' a)")
expect "an unsafe request ID is replaced, not echoed" \
  sh -c "echo '$unsafe_rid' | grep -qE '^[0-9a-f]{32}$'"
expect "the gateway logged the ID that replaced an unsafe one" gateway_logged "$unsafe_rid"
expect "the ledger's log line for a request is JSON and carries its ID" ledger_logged_json

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

expect "/lb-health is 200 while serving" status_becomes 127.0.0.1 /lb-health 200
touch "$work/drain/on"
expect "/lb-health is 503 while drained" status_becomes 127.0.0.1 /lb-health 503
rm "$work/drain/on"
expect "/lb-health is 200 again once undrained" status_becomes 127.0.0.1 /lb-health 200

# --- Releases that go wrong, and ones that go right ----------------------

echo "roll.sh, a release nobody pushed"
install_release missing
expect "roll.sh refuses a release it cannot pull" roll_is_refused missing
expect "and the server is still on one" test "$(current_release)" = one
expect "and still serving" status_becomes 127.0.0.1 /lb-health 200

echo "roll.sh, a release whose auth service never becomes healthy"
install_release broken
expect "roll.sh refuses a release that does not come up" roll_is_refused broken
expect "and the server is back on one" test "$(current_release)" = one
expect "and auth runs release one's image" \
  test "$(compose ps --format '{{.Image}}' auth)" = "$registry/cs464-auth:one"
expect "and it is undrained and serving" status_becomes 127.0.0.1 /lb-health 200

echo "roll.sh, release two"
install_release two
expect "roll.sh moves to a good release" "$work/releases/two/roll.sh"
expect "and the server is on two" test "$(current_release)" = two
expect "and every service runs release two" every_image_is_at two
expect "and only two and the release before it are kept" releases_kept_are "one two"
expect "and release one's images are kept, for going back" image_is_here "$registry/cs464-auth:one"

echo "the ledger's database password changes, as a deploy would change it"
LEDGER_DB_PASSWORD=$(random_secret)
grep -v '^LEDGER_DATABASE_URL=' "$work/app.env" > "$work/app.env.next"
echo "LEDGER_DATABASE_URL=postgresql+asyncpg://ledger_svc:$LEDGER_DB_PASSWORD@postgres:5432/cs464" >> "$work/app.env.next"
mv "$work/app.env.next" "$work/app.env"
"$work/releases/two/migrate.sh"

echo "roll.sh, a release that adds a service and never becomes healthy"
install_release three "$work/compose-three.yml"
expect "roll.sh refuses it" roll_is_refused three
expect "and the server is back on two" test "$(current_release)" = two
expect "and every service runs release two" every_image_is_at two
expect "and the service only three has is not running" no_container_runs_for extra
expect "and it is serving" status_becomes 127.0.0.1 /lb-health 200
expect "and its ledger reaches the database with today's password" has_starting_grant

echo
if [ "$failures" -ne 0 ]; then
  echo "$failures check(s) failed"
  exit 1
fi
echo "every check passed"
