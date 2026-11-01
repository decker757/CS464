#!/usr/bin/env bash
# [5.3] #19. Bring the database up to the release this script belongs to:
# ensure the roles and grants, then run the three migrate steps at this
# release's images. Once per deploy, on one server, before any server is
# rolled. Two at once would both fail, because the migrate step takes no lock
# (ADR 0020).
#
#   /opt/cs464/releases/<tag>/migrate.sh
#
# Needs ensure-database.sh's environment. Reads app.env and release.env
# beside it, as roll.sh does.
set -euo pipefail

cd "$(dirname "$0")"

./ensure-database.sh

for service in auth market ledger; do
  echo "migrate: $service"
  docker compose --env-file app.env --env-file release.env pull --quiet "$service-migrate"
  docker compose --env-file app.env --env-file release.env run --rm "$service-migrate"
done
echo "migrate: done"
