#!/usr/bin/env bash
# [5.3] #19. Checks release.sh's order of work and what it does when a step
# fails, without any servers. A fake `ssh` runs each command in a local
# directory standing in for one server, where roll.sh and migrate.sh are stubs
# that record what they were asked and fail when told to. stack-test.sh is
# what proves the real roll.sh and migrate.sh; this proves the deploy that
# drives them across servers:
#
#   - every server gets the release's files in the release's own directory,
#     its own app.env shared by every release, and a registry login
#   - only the first server rebuilds the leaderboard
#   - every app.env carries the direct database addresses beside the pooled
#     ones, and a deploy without them touches no server
#   - every server is sent Alloy's config, is labelled for the dashboard, and
#     has Alloy switched on only when Grafana Cloud is configured, whole and
#     with https addresses, or not at all
#   - the database is migrated once, from the first server, before any roll
#   - a failed migration rolls no server
#   - a server that fails to roll sends every server rolled before it back,
#     each with its old release's own roll.sh
#   - a server that switched to the release and then failed is sent back too
#   - a server that cannot be sent back is reported, loudly
#   - a server that could not go back stops the deploy without draining the
#     others to send them back
#   - a first deploy that fails says there was nothing to go back to
#
#   deploy/production/test/release-test.sh
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
release="$here/../release.sh"
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
failures=0

pass() { echo "  ok    $1"; }
fail() { echo "  FAIL  $1" >&2; failures=$((failures + 1)); }
expect() {
  local description=$1
  shift
  if "$@"; then pass "$description"; else fail "$description"; fi
}

# --- The fakes ---------------------------------------------------------------

mkdir -p "$root/bin" "$root/stubs"

# ssh: skip the options, take the host from deploy@<host>, and run the
# command in that host's directory. A release upload is kept as bundle.tgz,
# and the stubs go where the release's real scripts would be, instead of
# unpacking it.
cat > "$root/bin/ssh" <<'EOF'
#!/usr/bin/env bash
while [ $# -gt 0 ] && [[ $1 != deploy@* ]]; do shift; done
host=${1#deploy@}
shift
cd "$FAKE_ROOT/$host"
if [[ $* == *"tar -xzf -"* ]]; then
  cat > bundle.tgz
  directory=${*##*-C }
  mkdir -p "$directory"
  cp "$FAKE_ROOT/stubs/"* "$directory/"
  exit 0
fi
exec bash -c "$*"
EOF

# docker: only `login` and `logout` reach it; record them. Login reads the
# token from stdin, as the real one does.
cat > "$root/bin/docker" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = login ]; then cat > /dev/null; fi
echo "$1" >> docker-calls
EOF

# roll.sh: the release is the directory it sits in. Record it, then fail
# before switching, switch, or switch and then fail, as the server's
# fail-roll-on and fail-after-switch-on files say. down-on: fail, and could
# not go back either, as the real roll.sh reports with exit status 2.
cat > "$root/stubs/roll.sh" <<'EOF'
#!/usr/bin/env bash
release=$(basename "$(cd "$(dirname "$0")" && pwd)")
cd "$(dirname "$0")/../.."
echo "$release" >> roll-calls
if [ "$release" = "$(cat down-on 2>/dev/null)" ]; then exit 2; fi
if [ "$release" = "$(cat fail-roll-on 2>/dev/null)" ]; then exit 1; fi
ln -sfn "releases/$release" current
if [ "$release" = "$(cat fail-after-switch-on 2>/dev/null)" ]; then exit 1; fi
EOF

# migrate.sh: record the release and whether the admin URL arrived.
cat > "$root/stubs/migrate.sh" <<'EOF'
#!/usr/bin/env bash
release=$(basename "$(cd "$(dirname "$0")" && pwd)")
cd "$(dirname "$0")/../.."
echo "$release ${DATABASE_ADMIN_URL:+with-admin-url}" >> migrate-calls
if [ "$release" = "$(cat fail-migrate-on 2>/dev/null)" ]; then exit 1; fi
EOF
chmod +x "$root/bin/"* "$root/stubs/"*

servers=(server-a server-b)

reset_servers() {
  # Every server on release `old`, with no record of anything else.
  local server
  for server in "${servers[@]}"; do
    rm -rf "${root:?}/$server"
    mkdir -p "$root/$server/releases/old"
    cp "$root/stubs/"* "$root/$server/releases/old/"
    ln -s releases/old "$root/$server/current"
  done
}

new_servers() {
  # Servers that have never had a release.
  local server
  for server in "${servers[@]}"; do
    rm -rf "${root:?}/$server"
    mkdir -p "$root/$server"
  done
}

# Extra settings for the next deploy, as NAME=value words. Empty by default.
extra_env=()

deploy() {
  env \
    FAKE_ROOT="$root" PATH="$root/bin:$PATH" DEPLOY_SSH="$root/bin/ssh" \
    APP_DIR=. SKIP_PUBLIC_CHECK=1 APP_HOSTS="${servers[*]}" \
    PRODUCTION_DOMAIN=cs464.test IMAGE_REGISTRY=ghcr.io/example \
    REGISTRY_USER=bot REGISTRY_TOKEN=token DEPLOY_SSH_KEY=key DEPLOY_KNOWN_HOSTS=hosts \
    DATABASE_ADMIN_URL=postgresql://admin REDIS_URL=rediss://cache \
    JWT_SECRET=jwt LEDGER_SERVICE_KEY=key AUTH_DATABASE_URL=a MARKET_DATABASE_URL=m \
    LEDGER_DATABASE_URL=l AUDIT_DATABASE_URL=u AUTH_DB_PASSWORD=1 MARKET_DB_PASSWORD=2 \
    LEDGER_DB_PASSWORD=3 AUDIT_DB_PASSWORD=4 \
    AUTH_DIRECT_DATABASE_URL=da MARKET_DIRECT_DATABASE_URL=dm \
    LEDGER_DIRECT_DATABASE_URL=dl AUDIT_DIRECT_DATABASE_URL=du \
    ${extra_env[@]+"${extra_env[@]}"} \
    "$release" "$1" > "$root/deploy.log" 2>&1
}

deploy_fails() { ! deploy "$1"; }

release_of() { basename "$(readlink "$root/$1/current")"; }
calls() { tr '\n' ' ' < "$root/$1/$2" 2>/dev/null | sed 's/ $//'; }
never_called() { test ! -e "$root/$1/$2"; }
mode_of() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"; }
app_env_of() { echo "$root/$1/app.env"; }
log_says() { grep -q "$1" "$root/deploy.log"; }

bundle_has() {
  # bundle_has <server> <path>: the release that server was sent holds it.
  local listing
  listing=$(tar -tzf "$root/$1/bundle.tgz") || return 1
  grep -qxE "(\./)?$2" <<< "$listing"
}

app_env_lacks() { ! grep -q "$1" "$(app_env_of "$2")"; }

admin_url_in_no_app_env() {
  ! grep -q DATABASE_ADMIN_URL "$(app_env_of server-a)" "$(app_env_of server-b)"
}

# --- A good release ------------------------------------------------------------

echo "a good release"
reset_servers
expect "the deploy succeeds" deploy new
for server in "${servers[@]}"; do
  expect "$server is on new" test "$(release_of "$server")" = new
  expect "$server has new's files in new's own directory" test -x "$root/$server/releases/new/roll.sh"
  expect "$server was sent Alloy's config" bundle_has "$server" alloy/config.alloy
  expect "$server logged in to the registry and out again" test "$(calls "$server" docker-calls)" = "login logout"
  expect "$server's app.env is readable by its owner only" test "$(mode_of "$(app_env_of "$server")")" = 600
done
expect "only the first server rebuilds the leaderboard" \
  grep -qx LEADERBOARD_REFRESH_ENABLED=true "$(app_env_of server-a)"
expect "the second does not" grep -qx LEADERBOARD_REFRESH_ENABLED=false "$(app_env_of server-b)"
expect "the database is migrated once, from the first server, with the admin URL" \
  test "$(calls server-a migrate-calls)" = "new with-admin-url"
expect "and not from the second" never_called server-b migrate-calls
expect "the admin URL is in no server's app.env" admin_url_in_no_app_env
# #640: the apps reach Postgres through the pooler, and the migrate steps
# directly, so app.env carries both addresses for every role.
for pair in AUTH=da MARKET=dm LEDGER=dl AUDIT=du; do
  expect "app.env carries ${pair%%=*}'s direct address" \
    grep -qx "${pair%%=*}_DIRECT_DATABASE_URL=${pair#*=}" "$(app_env_of server-b)"
done
expect "beside its pooled one" grep -qx LEDGER_DATABASE_URL=l "$(app_env_of server-b)"

# --- Grafana Cloud, optional ---------------------------------------------------

echo "a deploy with no Grafana settings"
expect "app.env names no profile, so Alloy stays off" app_env_lacks '^COMPOSE_PROFILES=' server-a
expect "app.env sends no tracing endpoint" app_env_lacks '^TRACING_ENDPOINT=' server-a
expect "nor a sample ratio" app_env_lacks '^TRACING_SAMPLE_RATIO=' server-a
expect "every server is labelled for the dashboard" grep -qx SERVER_LABEL=app-2 "$(app_env_of server-b)"
expect "the first is app-1" grep -qx SERVER_LABEL=app-1 "$(app_env_of server-a)"

echo "a deploy with every Grafana setting"
reset_servers
grafana_env=(GRAFANA_CLOUD_TOKEN=token-placeholder GRAFANA_PROMETHEUS_URL=https://p.invalid
  GRAFANA_PROMETHEUS_USER=1 GRAFANA_LOKI_URL=https://l.invalid GRAFANA_LOKI_USER=2
  GRAFANA_OTLP_URL=https://o.invalid GRAFANA_OTLP_USER=3)
extra_env=("${grafana_env[@]}")
expect "the deploy succeeds" deploy grafana
expect "Alloy is switched on" grep -qx COMPOSE_PROFILES=observability "$(app_env_of server-a)"
expect "and on the second server too" grep -qx COMPOSE_PROFILES=observability "$(app_env_of server-b)"
expect "services send their traces to Alloy" \
  grep -qx TRACING_ENDPOINT=http://alloy:4318/v1/traces "$(app_env_of server-a)"
expect "one trace in ten" grep -qx TRACING_SAMPLE_RATIO=0.1 "$(app_env_of server-a)"
expect "Alloy gets the token" grep -qx GRAFANA_CLOUD_TOKEN=token-placeholder "$(app_env_of server-a)"
expect "and the Loki address" grep -qx GRAFANA_LOKI_URL=https://l.invalid "$(app_env_of server-a)"

echo "a deploy with the Grafana token and nothing else"
reset_servers
extra_env=(GRAFANA_CLOUD_TOKEN=token-placeholder)
expect "the deploy fails" deploy_fails halfset
expect "before any server is touched" never_called server-a roll-calls
expect "or migrated" never_called server-a migrate-calls
expect "and says which setting is missing" log_says "GRAFANA_PROMETHEUS_URL"

# Later settings win in `env`, so each of these replaces one good address.
echo "a deploy with a Grafana address that is not https"
reset_servers
extra_env=("${grafana_env[@]}" GRAFANA_LOKI_URL=http://l.invalid)
expect "the deploy fails" deploy_fails plainhttp
expect "before any server is sent the release" never_called server-a releases/plainhttp
expect "or rolled" never_called server-a roll-calls
expect "or migrated" never_called server-a migrate-calls
expect "and names the bad setting" log_says "GRAFANA_LOKI_URL must be https://"

echo "a deploy with a space in a Grafana address"
reset_servers
extra_env=("${grafana_env[@]}" "GRAFANA_OTLP_URL=https://o.invalid /otlp")
expect "the deploy fails" deploy_fails spaced
expect "before any server is sent the release" never_called server-a releases/spaced
expect "and names the bad setting" log_says "GRAFANA_OTLP_URL must be https://"
extra_env=()

# --- The direct addresses, required --------------------------------------------

# Later settings win in `env`, so this empties one of the good addresses.
echo "a deploy before the direct addresses were pushed"
reset_servers
extra_env=(LEDGER_DIRECT_DATABASE_URL=)
expect "the deploy fails" deploy_fails nodirect
expect "before any server is sent the release" never_called server-a releases/nodirect
expect "and names the missing setting" log_says "LEDGER_DIRECT_DATABASE_URL is required"
extra_env=()

# --- A migration that fails ---------------------------------------------------

echo "a release whose migration fails"
reset_servers
echo bad > "$root/server-a/fail-migrate-on"
expect "the deploy fails" deploy_fails bad
for server in "${servers[@]}"; do
  expect "$server was never rolled" never_called "$server" roll-calls
  expect "$server is still on old" test "$(release_of "$server")" = old
done

# --- A server that fails to roll ----------------------------------------------

echo "a release the second server cannot start"
reset_servers
echo broken > "$root/server-b/fail-roll-on"
expect "the deploy fails" deploy_fails broken
expect "the first server was rolled, then sent back" test "$(calls server-a roll-calls)" = "broken old"
expect "so it is on old again" test "$(release_of server-a)" = old
expect "the second server stayed on old" test "$(release_of server-b)" = old
expect "and was not rolled again" test "$(calls server-b roll-calls)" = "broken"

# --- A server that switches, then fails ---------------------------------------

echo "a release the second server switches to, then fails"
reset_servers
echo half > "$root/server-b/fail-after-switch-on"
expect "the deploy fails" deploy_fails half
expect "the second server is sent back as well" test "$(calls server-b roll-calls)" = "half old"
expect "so both are on old" test "$(release_of server-a) $(release_of server-b)" = "old old"

# --- A server that cannot be sent back ----------------------------------------

echo "a release whose first server then cannot go back"
reset_servers
echo broken > "$root/server-b/fail-roll-on"
echo old > "$root/server-a/fail-roll-on"
expect "the deploy fails" deploy_fails broken
expect "and says the first server could not be sent back" \
  log_says "COULD NOT send server-a back to old"

# --- A server that could not go back -------------------------------------------

echo "a release the second server cannot start, nor go back from"
reset_servers
echo dead > "$root/server-b/down-on"
expect "the deploy fails" deploy_fails dead
expect "and says the second server is serving nothing" log_says "serving nothing"
expect "the first server is left serving, not drained to be sent back" \
  test "$(calls server-a roll-calls)" = "dead"

# --- A first deploy that fails --------------------------------------------------

echo "a first deploy the second server cannot start"
new_servers
echo first > "$root/server-b/fail-roll-on"
expect "the deploy fails" deploy_fails first
expect "and says there was no release to go back to" \
  log_says "server-b, which had no release before this one"

echo
if [ "$failures" -ne 0 ]; then
  echo "$failures check(s) failed; the last deploy's log:"
  cat "$root/deploy.log"
  exit 1
fi
echo "every check passed"
