#!/usr/bin/env bash
# Start the lab over on this server: an empty lab, as if just installed.
# Stop the app first (docker compose stop app).
#
#   start-over.sh               set the lab aside and leave an empty database
#   start-over.sh --undo STAMP  put the lab set aside at STAMP back
#
# Nothing is deleted. The database is renamed biomanager_before_start_over_<stamp>
# and the files (uploads, signing key) are moved into
# /appdata/.before-start-over-<stamp>/, so --undo brings the lab back as it was.
# When the app starts on the empty database it prints a new setup code, and
# its front page says the server has no lab yet: start one, or bring one from
# the desktop app.
set -euo pipefail
: "${APPDATA_DIR:=/appdata}" "${PGDATABASE:=biomanager}"

others=$(psql -XAtd postgres -c "SELECT count(*) FROM pg_stat_activity WHERE datname = '$PGDATABASE' AND pid <> pg_backend_pid()")
if [ "$others" != "0" ]; then
  echo "$others connection(s) to $PGDATABASE are open. Stop the app first: docker compose stop app"
  exit 1
fi

if [ "${1:-}" = "--undo" ]; then
  stamp=${2:?usage: start-over.sh --undo STAMP}
  kept="${PGDATABASE}_before_start_over_${stamp}"
  exists=$(psql -XAtd postgres -c "SELECT count(*) FROM pg_database WHERE datname = '$kept'")
  [ "$exists" = "1" ] || { echo "no lab set aside at $stamp ($kept) — nothing was changed"; exit 1; }
  aside="$APPDATA_DIR/.before-start-over-$stamp"
  # The lab made since the start over is itself kept, under its own stamp.
  now=$(date -u +%Y%m%d%H%M%S)
  psql -XAtd postgres -c "ALTER DATABASE \"$PGDATABASE\" RENAME TO \"${PGDATABASE}_after_start_over_${now}\""
  psql -XAtd postgres -c "ALTER DATABASE \"$kept\" RENAME TO \"$PGDATABASE\""
  if [ -d "$aside" ]; then
    later="$APPDATA_DIR/.after-start-over-$now"
    mkdir -p "$later"
    find "$APPDATA_DIR" -mindepth 1 -maxdepth 1 ! -name '.before-start-over-*' ! -name '.after-start-over-*' ! -name '.before-restore-*' -exec mv -t "$later" {} +
    find "$aside" -mindepth 1 -maxdepth 1 -exec mv -t "$APPDATA_DIR" {} +
    rmdir "$aside"
  fi
  echo "The lab set aside at $stamp is back. What was made since is kept as ${PGDATABASE}_after_start_over_${now}."
  echo "Start the app: docker compose start app"
  exit 0
fi

stamp=$(date -u +%Y%m%d%H%M%S)
kept="${PGDATABASE}_before_start_over_${stamp}"
psql -XAtd postgres -c "ALTER DATABASE \"$PGDATABASE\" RENAME TO \"$kept\""
createdb "$PGDATABASE"
aside="$APPDATA_DIR/.before-start-over-$stamp"
mkdir -p "$aside"
find "$APPDATA_DIR" -mindepth 1 -maxdepth 1 ! -name '.before-start-over-*' ! -name '.after-start-over-*' ! -name '.before-restore-*' -exec mv -t "$aside" {} +
echo "The lab is set aside: its database as $kept, its files in $aside."
echo "Start the app (docker compose start app): it opens on an empty lab, with a new setup code in its log."
echo "To undo: docker compose stop app, then"
echo "  docker compose --profile restore run --rm --entrypoint start-over.sh restore --undo $stamp"
