#!/usr/bin/env bash
# [5.3] #19. Bring the database up to a release: ensure the roles and grants,
# then run the three migrate steps at that release's images. Once per deploy,
# on one server, before any server is rolled. Two at once would both fail,
# because the migrate step takes no lock (ADR 0020).
#
#   ./migrate.sh <image-tag>
#
# Needs ensure-database.sh's environment, and app.env beside it.
set -euo pipefail

tag=${1:?usage: migrate.sh <image-tag>}
cd "$(dirname "$0")"

./ensure-database.sh

# The new release's images, whatever release.env says this server runs now.
# A variable set in the shell wins over the same one in an env file.
export IMAGE_TAG="$tag"
for service in auth market ledger; do
  echo "migrate: $service at $tag"
  docker compose --env-file app.env pull --quiet "$service-migrate"
  docker compose --env-file app.env run --rm "$service-migrate"
done
echo "migrate: done"
