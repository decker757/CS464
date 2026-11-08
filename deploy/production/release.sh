#!/usr/bin/env bash
# [5.3] #19. One production deploy, run on GitHub's runner by
# .github/workflows/deploy.yml once the release's images are pushed:
#
#   1. Send every app server this release's files, into
#      /opt/cs464/releases/<tag>/, and its app.env, into /opt/cs464/, shared
#      by every release. Log it in to the image registry.
#   2. Migrate the database once, from the first server (ADR 0020).
#   3. Roll the servers one at a time with roll.sh. If one fails, roll.sh has
#      already put it back on its old release, and every server rolled before
#      it is sent back too, so the servers end on the release they had, with
#      the exceptions docs/deployment.md lists under "When a deploy fails". A
#      server is sent back with its old release's own roll.sh, from its own
#      directory, so the old release runs exactly as it was. One more case:
#      a server that could not go back is serving nothing, so the others are
#      left serving rather than drained one by one to be sent back.
#   4. Check the public site answers on every name.
#
#   release.sh <image-tag>
#
# A migration stays applied when its release is sent back, so every
# migration must also work with the release before it. docs/deployment.md.
#
# Reads, from its environment, which deploy.yml sets: IMAGE_REGISTRY, worked
# out from the repository owner; REGISTRY_USER and REGISTRY_TOKEN, the run's
# own actor and token; PRODUCTION_DOMAIN, a repository variable; and from the
# GitHub "production" environment:
#   APP_HOSTS DEPLOY_SSH_KEY DEPLOY_KNOWN_HOSTS DATABASE_ADMIN_URL REDIS_URL
#   JWT_SECRET LEDGER_SERVICE_KEY, and for each of AUTH MARKET LEDGER AUDIT:
#   <NAME>_DATABASE_URL (through the pooler), <NAME>_DIRECT_DATABASE_URL (to
#   Postgres itself, for the migrate steps) and <NAME>_DB_PASSWORD
# Optional: GRAFANA_CLOUD_TOKEN switches Alloy on, and with it set, these are
# required as well, so half a group fails before any server is touched, and
# so does a *_URL that is not https:// followed by no whitespace:
#   GRAFANA_PROMETHEUS_URL GRAFANA_PROMETHEUS_USER GRAFANA_LOKI_URL
#   GRAFANA_LOKI_USER GRAFANA_OTLP_URL GRAFANA_OTLP_USER
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
  LEDGER_DATABASE_URL AUDIT_DATABASE_URL AUTH_DIRECT_DATABASE_URL MARKET_DIRECT_DATABASE_URL \
  LEDGER_DIRECT_DATABASE_URL AUDIT_DIRECT_DATABASE_URL "${migrate_secrets[@]}"; do
  : "${!name:?$name is required}"
done

# Grafana Cloud is on if and only if the token is set. Alloy is the one part
# of the release that may be absent, so nothing else reads these.
grafana_names=(GRAFANA_PROMETHEUS_URL GRAFANA_PROMETHEUS_USER GRAFANA_LOKI_URL
  GRAFANA_LOKI_USER GRAFANA_OTLP_URL GRAFANA_OTLP_USER)
grafana_on=false
if [ -n "${GRAFANA_CLOUD_TOKEN:-}" ]; then
  grafana_on=true
  for name in "${grafana_names[@]}"; do
    : "${!name:?$name is required when GRAFANA_CLOUD_TOKEN is set}"
    # Alloy cannot load a config with an address it cannot parse, and would
    # crash on every server. Caught here, nothing has been touched.
    if [[ $name == *_URL && ! ${!name} =~ ^https://[^[:space:]]+$ ]]; then
      echo "deploy: $name must be https:// followed by an address with no spaces" >&2
      exit 1
    fi
  done
fi

work=$(mktemp -d)
release_dir=$app_dir/releases/$tag

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
  cp -r "$repo/deploy/production/alloy" "$work/bundle/"
  cp "$repo/deploy/production/sql/ensure-roles.sql" "$repo/sql/02-schemas.sql" "$work/bundle/sql/"
  echo "IMAGE_TAG=$tag" > "$work/bundle/release.env"
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
SERVER_LABEL=app-$(($1 + 1))
IMAGE_REGISTRY=$IMAGE_REGISTRY
DOMAIN=$domain
JWT_SECRET=$JWT_SECRET
LEDGER_SERVICE_KEY=$LEDGER_SERVICE_KEY
AUTH_DATABASE_URL=$AUTH_DATABASE_URL
MARKET_DATABASE_URL=$MARKET_DATABASE_URL
LEDGER_DATABASE_URL=$LEDGER_DATABASE_URL
AUDIT_DATABASE_URL=$AUDIT_DATABASE_URL
AUTH_DIRECT_DATABASE_URL=$AUTH_DIRECT_DATABASE_URL
MARKET_DIRECT_DATABASE_URL=$MARKET_DIRECT_DATABASE_URL
LEDGER_DIRECT_DATABASE_URL=$LEDGER_DIRECT_DATABASE_URL
AUDIT_DIRECT_DATABASE_URL=$AUDIT_DIRECT_DATABASE_URL
REDIS_URL=$REDIS_URL
LEADERBOARD_REFRESH_ENABLED=$leaderboard
EOF
  if [ "$grafana_on" = true ]; then
    # COMPOSE_PROFILES is what starts Alloy; the services send it their
    # traces. Without these lines Alloy is never started.
    echo "COMPOSE_PROFILES=observability"
    echo "TRACING_ENDPOINT=http://alloy:4318/v1/traces"
    echo "TRACING_SAMPLE_RATIO=0.1"
    for name in "${grafana_names[@]}" GRAFANA_CLOUD_TOKEN; do
      echo "$name=${!name}"
    done
  fi
}

migrate_script() {
  # The migrate step, as a script for `bash -s` on the server. The secrets
  # travel on stdin, so they appear on no command line and in no file there.
  local name
  for name in "${migrate_secrets[@]}"; do
    printf 'export %s=%q\n' "$name" "${!name}"
  done
  printf '%q\n' "$release_dir/migrate.sh"
}

release_on() {
  # The release a server runs now, or nothing if it has never had one: the
  # directory that `current` links to. Fails if the server cannot be asked,
  # which is not the same answer as "none".
  on_host "$1" "{ readlink $app_dir/current || true; } | sed 's#.*/##'" < /dev/null
}

roll_to() {
  # roll_to <host> <tag>: that release's own roll.sh, on that server.
  on_host "$1" "$app_dir/releases/$2/roll.sh" < /dev/null
}

# --- 1. Prepare every server -------------------------------------------------

make_bundle
previous=()
for index in "${!hosts[@]}"; do
  host=${hosts[$index]}
  echo "deploy: preparing $host"
  on_host "$host" "mkdir -p $release_dir && tar -xzf - -C $release_dir" < "$work/bundle.tgz"
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
  roll_status=0
  roll_to "$host" "$tag" || roll_status=$?
  if [ "$roll_status" = 0 ]; then
    rolled+=("$index")
    continue
  fi

  if [ "$roll_status" = 2 ]; then
    # roll.sh could not start the old release either. Sending the others
    # back would drain each in turn while this one serves nothing.
    echo "deploy: FAILED on $host, which could not go back and is serving nothing." >&2
    echo "deploy: the servers rolled before it stay on $tag; see docs/deployment.md, \"When a deploy fails\"" >&2
    exit 1
  fi

  # roll.sh puts a server it could not start back on its old release. But a
  # failure after the switch leaves it on the new one, so ask, not assume.
  send_back=(${rolled[@]+"${rolled[@]}"})
  now_on=$(release_on "$host") || now_on="unknown"
  if [ "$now_on" = "$tag" ]; then
    echo "deploy: FAILED on $host, which is on $tag even so" >&2
    send_back+=("$index")
  elif [ "$now_on" = "unknown" ]; then
    echo "deploy: FAILED on $host, which cannot be asked which release it is on" >&2
  elif [ -z "${previous[index]}" ]; then
    echo "deploy: FAILED on $host, which had no release before this one" >&2
  else
    echo "deploy: FAILED on $host, which is back on its old release" >&2
  fi
  for earlier in ${send_back[@]+"${send_back[@]}"}; do
    back_to=${previous[earlier]}
    if [ -z "$back_to" ]; then
      echo "deploy: ${hosts[earlier]} had no release before this one, so it stays on $tag" >&2
      continue
    fi
    echo "deploy: sending ${hosts[earlier]} back to $back_to" >&2
    roll_to "${hosts[earlier]}" "$back_to" ||
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
