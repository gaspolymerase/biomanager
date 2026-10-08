#!/usr/bin/env bash
# Weekly (biomanager-maintenance.timer, Sunday 03:30): pick up security fixes
# in the images the stack is built on (Python, PostgreSQL 16, Caddy, the
# backup image's Debian). The operating system itself is patched nightly by
# unattended-upgrades; this is the same for the containers.
#
# A backup first, then pull and rebuild on the same tags (PostgreSQL stays
# on 16; only its patch releases arrive), restart what changed, and check
# the app is healthy. The result goes to ntfy like the watchdog's alerts.
# The app's own code only changes when someone pushes it; not here.
set -uo pipefail

CONF=/etc/biomanager/watchdog.env
[ -f "$CONF" ] && . "$CONF"
: "${DEPLOY_DIR:=/opt/biomanager/Biomanager/deploy}"
: "${NTFY_SERVER:=https://ntfy.sh}"
: "${NTFY_TOPIC:=}"

notify() {
  echo "[maintenance] $1: $2"
  [ -z "$NTFY_TOPIC" ] && return 0
  curl -fsS -m 15 -o /dev/null -H "Title: $1" -H "Priority: ${3:-low}" -H "Tags: ${4:-wrench}" \
    -d "$2" "$NTFY_SERVER/$NTFY_TOPIC" || true
}
fail() { notify "BioManager: weekly update failed" "$(hostname): $1" high rotating_light; exit 1; }

cd "$DEPLOY_DIR" || fail "no $DEPLOY_DIR"
# Never at the same time as an update (update.sh).
exec 9> /run/lock/biomanager-stack.lock 2> /dev/null || exec 9> "${TMPDIR:-/tmp}/biomanager-stack.lock"
flock -w 2700 9 || fail "an update is still running"
docker compose exec -T backup backup.sh < /dev/null || fail "the backup before updating failed, so nothing was updated"
docker compose pull --quiet db caddy < /dev/null || fail "could not pull images"
docker compose build --pull --quiet < /dev/null || fail "could not rebuild the app or backup image"
docker compose up -d < /dev/null || fail "could not restart the services"

for i in $(seq 1 60); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q app)")" = healthy ] && break
  sleep 5
done
[ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q app)")" = healthy ] \
  || fail "the app is not healthy after the update. See: docker compose logs app"

docker image prune -f > /dev/null
notify "BioManager: weekly update done" "$(hostname): images refreshed, app healthy." low white_check_mark
