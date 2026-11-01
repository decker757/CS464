#!/usr/bin/env bash
# [5.3] #19. Move this server onto a new release, or leave it on the one it
# was running. The deploy runs this on one server at a time, so the others
# carry the traffic while this one changes.
#
#   ./roll.sh <image-tag>
#
# 1. Pull the new images. If that fails, nothing has changed: stop.
# 2. Drain: /lb-health answers 503, and the load balancer stops sending
#    requests here. Wait long enough for it to notice.
# 3. Start the new release and wait for every health check.
# 4. If that fails, start the old release again, undrain, and exit 1.
# 5. Undrain, and wait for the load balancer to send traffic again before
#    the deploy moves on to the next server.
#
# release.env records the release this server runs. app.env holds the
# secrets and this server's settings; the deploy writes it.
set -euo pipefail

new_tag=${1:?usage: roll.sh <image-tag>}
cd "$(dirname "$0")"

# The load balancer checks every 3 seconds and acts on 3 failures or 2
# successes (infra/terraform/load_balancer.tf), so 15 seconds covers either.
drain_seconds=${DRAIN_SECONDS:-15}
wait_seconds=${WAIT_SECONDS:-180}
gateway=http://127.0.0.1:${GATEWAY_PORT:-80}

compose_at() {
  docker compose --env-file app.env --env-file "$1" "${@:2}"
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

echo "IMAGE_TAG=$new_tag" > release.next.env

echo "roll: pulling $new_tag"
if ! compose_at release.next.env pull --quiet; then
  echo "roll: FAILED to pull $new_tag; this server is unchanged" >&2
  rm -f release.next.env
  exit 1
fi

mkdir -p drain
if [ -f release.env ]; then
  echo "roll: draining for ${drain_seconds}s"
  touch drain/on
  sleep "$drain_seconds"
fi

echo "roll: starting $new_tag"
if ! compose_at release.next.env up -d --wait --wait-timeout "$wait_seconds" --remove-orphans; then
  echo "roll: FAILED to start $new_tag" >&2
  compose_at release.next.env ps >&2 || true
  if [ -f release.env ]; then
    echo "roll: going back to $(cat release.env)" >&2
    compose_at release.env up -d --wait --wait-timeout "$wait_seconds" --remove-orphans
    rm -f drain/on
    wait_for_lb_health
  fi
  rm -f release.next.env
  exit 1
fi

mv release.next.env release.env
rm -f drain/on
wait_for_lb_health
echo "roll: waiting ${drain_seconds}s for the load balancer to send traffic here"
sleep "$drain_seconds"

# Images no release has used for three days. The one before this is kept
# for a while, in case it is needed again.
docker image prune -af --filter "until=72h" > /dev/null
echo "roll: this server is on $new_tag"
