#!/usr/bin/env bash
# Rebuild and restart the local Lily container from the working tree.
# docker-compose sometimes leaves a half-created "<hash>_lily" container behind
# and then fails to reuse the name "lily"; this clears that, rebuilds, and checks
# the running container really uses the fresh image.
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE=(docker-compose -f docker-compose.local.yml)

# Strays: compose's temporary names, only ever "<12 hex>_lily" and never running.
docker ps -a --filter status=created --format '{{.ID}} {{.Names}}' \
  | awk '$2 ~ /^[0-9a-f]{12}_lily$/ {print $1}' | xargs -r docker rm >/dev/null

"${COMPOSE[@]}" up -d --build

if [ "$(docker inspect -f '{{.Image}}' lily)" != "$(docker image inspect -f '{{.Id}}' lily:latest)" ]; then
  echo "lily is running an old image; recreating" >&2
  "${COMPOSE[@]}" up -d --force-recreate
fi

for _ in $(seq 1 30); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' lily 2>/dev/null)" = healthy ] && { echo "lily is healthy"; exit 0; }
  sleep 2
done
echo "lily did not become healthy" >&2
exit 1
