#!/usr/bin/env bash
# [5.3] #19. Checks release.sh's order of work and what it does when a step
# fails, without any servers. A fake `ssh` runs each command in a local
# directory standing in for one server, where roll.sh and migrate.sh are stubs
# that record what they were asked and fail when told to. stack-test.sh is
# what proves the real roll.sh and migrate.sh; this proves the deploy that
# drives them across servers:
#
#   - every server gets the files, its own app.env, and a registry login
#   - only the first server rebuilds the leaderboard
#   - the database is migrated once, from the first server, before any roll
#   - a failed migration rolls no server
#   - a server that fails to roll sends every server rolled before it back
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

mkdir -p "$root/bin"

# ssh: skip the options, take the host from deploy@<host>, and run the
# command in that host's directory. A bundle upload is recorded, not
# unpacked, so the stubs below stay in place of the real scripts.
cat > "$root/bin/ssh" <<'EOF'
#!/usr/bin/env bash
while [ $# -gt 0 ] && [[ $1 != deploy@* ]]; do shift; done
host=${1#deploy@}
shift
cd "$FAKE_ROOT/$host"
if [[ $* == *"tar -xzf -"* ]]; then
  cat > /dev/null
  touch bundle-received
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

# roll.sh: move to the tag, unless this server was told to fail on it.
cat > "$root/roll.sh" <<'EOF'
#!/usr/bin/env bash
echo "$1" >> roll-calls
if [ "$1" = "$(cat fail-roll-on 2>/dev/null)" ]; then exit 1; fi
echo "IMAGE_TAG=$1" > release.env
EOF

# migrate.sh: record the tag and whether the admin URL arrived.
cat > "$root/migrate.sh" <<'EOF'
#!/usr/bin/env bash
echo "$1 ${DATABASE_ADMIN_URL:+with-admin-url}" >> migrate-calls
if [ "$1" = "$(cat fail-migrate-on 2>/dev/null)" ]; then exit 1; fi
EOF
chmod +x "$root/bin/"* "$root/roll.sh" "$root/migrate.sh"

servers=(server-a server-b)

reset_servers() {
  # Every server on release `old`, with no record of anything else.
  local server
  for server in "${servers[@]}"; do
    rm -rf "${root:?}/$server"
    mkdir -p "$root/$server"
    cp "$root/roll.sh" "$root/migrate.sh" "$root/$server/"
    echo "IMAGE_TAG=old" > "$root/$server/release.env"
  done
}

deploy() {
  FAKE_ROOT=$root PATH="$root/bin:$PATH" DEPLOY_SSH="$root/bin/ssh" \
    APP_DIR=. SKIP_PUBLIC_CHECK=1 APP_HOSTS="${servers[*]}" \
    PRODUCTION_DOMAIN=cs464.test IMAGE_REGISTRY=ghcr.io/example \
    REGISTRY_USER=bot REGISTRY_TOKEN=token DEPLOY_SSH_KEY=key DEPLOY_KNOWN_HOSTS=hosts \
    DATABASE_ADMIN_URL=postgresql://admin REDIS_URL=rediss://cache \
    JWT_SECRET=jwt LEDGER_SERVICE_KEY=key AUTH_DATABASE_URL=a MARKET_DATABASE_URL=m \
    LEDGER_DATABASE_URL=l AUDIT_DATABASE_URL=u AUTH_DB_PASSWORD=1 MARKET_DB_PASSWORD=2 \
    LEDGER_DB_PASSWORD=3 AUDIT_DB_PASSWORD=4 \
    "$release" "$1" > "$root/deploy.log" 2>&1
}

deploy_fails() { ! deploy "$1"; }

admin_url_in_no_app_env() {
  ! grep -q DATABASE_ADMIN_URL "$root/server-a/app.env" "$root/server-b/app.env"
}

release_of() { sed -n 's/^IMAGE_TAG=//p' "$root/$1/release.env"; }
calls() { tr '\n' ' ' < "$root/$1/$2" 2>/dev/null | sed 's/ $//'; }
never_called() { test ! -e "$root/$1/$2"; }
mode_of() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"; }

# --- A good release ------------------------------------------------------------

echo "a good release"
reset_servers
expect "the deploy succeeds" deploy new
for server in "${servers[@]}"; do
  expect "$server is on new" test "$(release_of "$server")" = new
  expect "$server received the files" test -e "$root/$server/bundle-received"
  expect "$server logged in to the registry and out again" test "$(calls "$server" docker-calls)" = "login logout"
  expect "$server's app.env is readable by its owner only" test "$(mode_of "$root/$server/app.env")" = 600
done
expect "only the first server rebuilds the leaderboard" \
  grep -qx LEADERBOARD_REFRESH_ENABLED=true "$root/server-a/app.env"
expect "the second does not" grep -qx LEADERBOARD_REFRESH_ENABLED=false "$root/server-b/app.env"
expect "the database is migrated once, from the first server, with the admin URL" \
  test "$(calls server-a migrate-calls)" = "new with-admin-url"
expect "and not from the second" never_called server-b migrate-calls
expect "the admin URL is in no server's app.env" admin_url_in_no_app_env

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

echo
if [ "$failures" -ne 0 ]; then
  echo "$failures check(s) failed; the last deploy's log:"
  cat "$root/deploy.log"
  exit 1
fi
echo "every check passed"
