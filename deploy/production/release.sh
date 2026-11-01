#!/usr/bin/env bash
# [5.3] #19. One production deploy, run on GitHub's runner by
# .github/workflows/deploy.yml once the release's images are pushed:
#
#   1. Send every app server the files it runs and its app.env, and log it in
#      to the image registry.
#   2. Migrate the database once, from the first server (ADR 0020).
#   3. Roll the servers one at a time with roll.sh. If one fails, roll.sh has
#      already put it back on its old release, and every server rolled before
#      it is sent back too, so the servers never run different releases.
#   4. Check the public site answers on every name.
#
#   release.sh <image-tag>
#
# A migration stays applied when its release is sent back, so every
# migration must also work with the release before it. docs/deployment.md.
#
# Reads, from the GitHub "production" environment:
#   APP_HOSTS PRODUCTION_DOMAIN IMAGE_REGISTRY REGISTRY_USER REGISTRY_TOKEN
#   DEPLOY_SSH_KEY DEPLOY_KNOWN_HOSTS DATABASE_ADMIN_URL REDIS_URL
#   JWT_SECRET LEDGER_SERVICE_KEY, and for each of AUTH MARKET LEDGER AUDIT:
#   <NAME>_DATABASE_URL and <NAME>_DB_PASSWORD
# test/release-test.sh also sets DEPLOY_SSH, APP_DIR and SKIP_PUBLIC_CHECK.
set -euo pipefail

tag=${1:?usage: release.sh <image-tag>}
repo=$(cd "$(dirname "$0")/../.." && pwd)
app_dir=${APP_DIR:-/opt/cs464}
domain=${PRODUCTION_DOMAIN:?}
read -r -a hosts <<< "${APP_HOSTS:?}"

migrate_secrets=(DATABASE_ADMIN_URL AUTH_DB_PASSWORD MARKET_DB_PASSWORD LEDGER_DB_PASSWORD AUDIT_DB_PASSWORD)
for name in IMAGE_REGISTRY REGISTRY_USER REGISTRY_TOKEN DEPLOY_SSH_KEY DEPLOY_KNOWN_HOSTS \
  REDIS_URL JWT_SECRET LEDGER_SERVICE_KEY AUTH_DATABASE_URL MARKET_DATABASE_URL \
  LEDGER_DATABASE_URL AUDIT_DATABASE_URL "${migrate_secrets[@]}"; do
  : "${!name:?$name is required}"
done

work=$(mktemp -d)

# --- Talking to a server -----------------------------------------------------

printf '%s\n' "$DEPLOY_SSH_KEY" > "$work/key"
chmod 600 "$work/key"
printf '%s\n' "$DEPLOY_KNOWN_HOSTS" > "$work/known_hosts"

on_host() {
  # on_host <host> <command...>: run a command on an app server as `deploy`.
  # ssh passes its stdin to the command, so a call that sends nothing says
  # `< /dev/null`, or it would wait on whatever stdin the deploy has.
  # StrictHostKeyChecking=yes: the host keys come from Terraform, so a server
  # that presents any other key is refused rather than trusted.
  local host=$1
  shift
  "${DEPLOY_SSH:-ssh}" -i "$work/key" \
    -o UserKnownHostsFile="$work/known_hosts" -o StrictHostKeyChecking=yes \
    -o BatchMode=yes -o ConnectTimeout=15 \
    "deploy@$host" "$@"
}

cleanup() {
  for host in "${hosts[@]}"; do
    on_host "$host" "docker logout ${IMAGE_REGISTRY%%/*}" < /dev/null > /dev/null 2>&1 || true
  done
  rm -rf "$work"
}
trap cleanup EXIT

# --- What each server is sent ------------------------------------------------

make_bundle() {
  mkdir -p "$work/bundle/sql"
  cp "$repo"/deploy/production/{compose.yml,roll.sh,migrate.sh,ensure-database.sh} "$work/bundle/"
  cp "$repo/deploy/production/sql/ensure-roles.sql" "$repo/sql/02-schemas.sql" "$work/bundle/sql/"
  tar -czf "$work/bundle.tgz" -C "$work/bundle" .
}

app_env_for() {
  # app_env_for <server index>: that server's app.env. Only the first server
  # rebuilds the leaderboard (ADR 0021).
  local leaderboard=false
  if [ "$1" = 0 ]; then
    leaderboard=true
  fi
  cat <<EOF
IMAGE_REGISTRY=$IMAGE_REGISTRY
DOMAIN=$domain
JWT_SECRET=$JWT_SECRET
LEDGER_SERVICE_KEY=$LEDGER_SERVICE_KEY
AUTH_DATABASE_URL=$AUTH_DATABASE_URL
MARKET_DATABASE_URL=$MARKET_DATABASE_URL
LEDGER_DATABASE_URL=$LEDGER_DATABASE_URL
AUDIT_DATABASE_URL=$AUDIT_DATABASE_URL
REDIS_URL=$REDIS_URL
LEADERBOARD_REFRESH_ENABLED=$leaderboard
EOF
}

migrate_script() {
  # The migrate step, as a script for `bash -s` on the server. The secrets
  # travel on stdin, so they appear on no command line and in no file there.
  local name
  for name in "${migrate_secrets[@]}"; do
    printf 'export %s=%q\n' "$name" "${!name}"
  done
  printf 'cd %q && ./migrate.sh %q\n' "$app_dir" "$tag"
}

release_on() {
  # The release a server runs now, or nothing if it has never had one.
  on_host "$1" "sed -n 's/^IMAGE_TAG=//p' $app_dir/release.env 2>/dev/null" < /dev/null || true
}

# --- 1. Prepare every server -------------------------------------------------

make_bundle
previous=()
for index in "${!hosts[@]}"; do
  host=${hosts[$index]}
  echo "deploy: preparing $host"
  on_host "$host" "tar -xzf - -C $app_dir" < "$work/bundle.tgz"
  app_env_for "$index" | on_host "$host" "umask 077 && cat > $app_dir/app.env"
  printf '%s\n' "$REGISTRY_TOKEN" |
    on_host "$host" "docker login ${IMAGE_REGISTRY%%/*} -u $REGISTRY_USER --password-stdin" > /dev/null
  previous[index]=$(release_on "$host")
done

# --- 2. Migrate, once --------------------------------------------------------

echo "deploy: migrating to $tag, from ${hosts[0]}"
migrate_script | on_host "${hosts[0]}" bash -s

# --- 3. Roll, one server at a time --------------------------------------------

rolled=()
for index in "${!hosts[@]}"; do
  host=${hosts[$index]}
  echo "deploy: rolling $host to $tag"
  if on_host "$host" "cd $app_dir && ./roll.sh $tag" < /dev/null; then
    rolled+=("$index")
    continue
  fi

  echo "deploy: FAILED on $host, which is back on its old release" >&2
  for earlier in ${rolled[@]+"${rolled[@]}"}; do
    back_to=${previous[earlier]}
    if [ -z "$back_to" ]; then
      echo "deploy: ${hosts[earlier]} had no release before this one, so it stays on $tag" >&2
      continue
    fi
    echo "deploy: sending ${hosts[earlier]} back to $back_to" >&2
    on_host "${hosts[earlier]}" "cd $app_dir && ./roll.sh $back_to" < /dev/null ||
      echo "deploy: COULD NOT send ${hosts[earlier]} back to $back_to" >&2
  done
  exit 1
done

# --- 4. The public site -------------------------------------------------------

answers() {
  local attempt
  for attempt in $(seq 1 10); do
    if curl -fsS -o /dev/null --max-time 10 "$1"; then
      return 0
    fi
    echo "deploy: $1 did not answer (attempt $attempt)" >&2
    sleep 3
  done
  return 1
}

if [ -z "${SKIP_PUBLIC_CHECK:-}" ]; then
  unanswered=0
  for url in "https://$domain/" "https://auth.$domain/health" "https://market.$domain/health" \
    "https://ledger.$domain/health" "https://audit.$domain/health" "https://realtime.$domain/health"; do
    if answers "$url"; then
      echo "deploy: $url answers"
    else
      unanswered=$((unanswered + 1))
    fi
  done
  if [ "$unanswered" -ne 0 ]; then
    echo "deploy: every server is on $tag, but $unanswered public address(es) do not answer" >&2
    exit 1
  fi
fi

echo "deploy: every server is on $tag"
