#!/usr/bin/env bash
# [5.3] #19. Move this server onto the release this script belongs to, or leave
# it on the release it was running. The deploy runs this on one server at a
# time, so the others carry the traffic while this one changes.
#
#   /opt/cs464/releases/<tag>/roll.sh
#
# Each release has its own directory: its compose.yml, its scripts and its
# release.env (IMAGE_TAG). `current` links to the release the server runs. A
# release is always started from its own directory, so going back runs the
# old release exactly as it was, even if the new one added a service.
#
# The secrets are not the release's. /opt/cs464/app.env is shared by every
# release and rewritten by every deploy, so a release gone back to gets
# today's passwords and addresses, not the ones it was deployed with.
#
# 1. Pull the new images. If that fails, nothing has changed: stop.
# 2. Drain: /lb-health answers 503, and the load balancer stops sending
#    requests here. Wait long enough for it to notice.
# 3. Start the new release and wait for every health check.
# 4. If that fails, start the old release again from its own directory,
#    undrain, and exit 1. If the old release will not start either, the
#    server stays drained, because it cannot serve, and exits 2.
# 5. Point `current` at the new release. From here the server is on it, so
#    nothing after this step can fail the roll.
# 6. Undrain, wait for the load balancer to send traffic again, and tidy up.
#
# Exit status: 0 on the new release; 1 failed, and on the old release (or
# none, on a first deploy); 2 failed, and serving nothing.
set -euo pipefail

release_dir=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$release_dir/../.." && pwd)
new_tag=$(basename "$release_dir")

previous_dir=""
if [ -L "$root/current" ]; then
  previous_dir=$(cd -P "$root/current" && pwd)
fi
previous_tag=""
if [ -n "$previous_dir" ]; then
  previous_tag=$(basename "$previous_dir")
fi

# The load balancer checks every 3 seconds and acts on 3 failures or 2
# successes (infra/terraform/load_balancer.tf), so 15 seconds covers either.
drain_seconds=${DRAIN_SECONDS:-15}
wait_seconds=${WAIT_SECONDS:-180}
gateway=http://127.0.0.1:${GATEWAY_PORT:-80}

# One folder for every release, and always absolute: compose.yml mounts it,
# and a relative path would resolve inside the release's own directory. The
# shell's value wins over any in an env file.
export DRAIN_DIR="$root/drain"

compose_in() {
  # compose_in <release directory> <compose arguments...>
  (cd "$1" && docker compose --env-file "$root/app.env" --env-file release.env "${@:2}")
}

wait_for_lb_health() {
  for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null "$gateway/lb-health"; then
      return 0
    fi
    sleep 1
  done
  echo "roll: /lb-health never answered 200" >&2
  return 1
}

remove_other_releases() {
  # Keeps the new release and the one before it, images and directories
  # both: the deploy may still send this server back to the one before.
  local image directory
  docker image ls --format '{{.Repository}}:{{.Tag}}' | grep '/cs464-' | while read -r image; do
    case "${image##*:}" in
      "$new_tag" | "$previous_tag") ;;
      *) docker image rm "$image" > /dev/null 2>&1 || true ;;
    esac
  done
  for directory in "$root"/releases/*/; do
    case "$(basename "$directory")" in
      "$new_tag" | "$previous_tag") ;;
      *) rm -rf "$directory" ;;
    esac
  done
}

echo "roll: pulling $new_tag"
if ! compose_in "$release_dir" pull --quiet; then
  echo "roll: FAILED to pull $new_tag; this server is unchanged" >&2
  exit 1
fi

mkdir -p "$DRAIN_DIR"
if [ -n "$previous_dir" ]; then
  echo "roll: draining for ${drain_seconds}s"
  touch "$DRAIN_DIR/on"
  sleep "$drain_seconds"
fi

echo "roll: starting $new_tag"
if ! compose_in "$release_dir" up -d --wait --wait-timeout "$wait_seconds" --remove-orphans; then
  echo "roll: FAILED to start $new_tag" >&2
  compose_in "$release_dir" ps >&2 || true
  if [ -n "$previous_dir" ]; then
    echo "roll: going back to $previous_tag" >&2
    if ! compose_in "$previous_dir" up -d --wait --wait-timeout "$wait_seconds" --remove-orphans; then
      echo "roll: COULD NOT go back to $previous_tag; this server is serving nothing, and stays drained" >&2
      exit 2
    fi
    rm -f "$DRAIN_DIR/on"
    wait_for_lb_health || echo "roll: WARNING: back on $previous_tag, but /lb-health is not answering" >&2
  fi
  exit 1
fi

ln -sfn "releases/$new_tag" "$root/current"
echo "roll: this server is on $new_tag"

# Warnings from here on, not failures: the server is already on the new
# release, and saying otherwise would send the deploy the wrong way.
rm -f "$DRAIN_DIR/on"
if wait_for_lb_health; then
  echo "roll: waiting ${drain_seconds}s for the load balancer to send traffic here"
  sleep "$drain_seconds"
else
  echo "roll: WARNING: $new_tag is running, but /lb-health is not answering" >&2
fi
# Rolling the release it was already on leaves nothing to tidy, and tidying
# would remove the release before, which a deploy may still go back to.
if [ "$new_tag" != "$previous_tag" ]; then
  remove_other_releases || echo "roll: WARNING: could not remove old releases" >&2
fi
