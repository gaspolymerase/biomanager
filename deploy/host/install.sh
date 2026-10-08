#!/usr/bin/env bash
# Install the watchdog and weekly maintenance timers on the server, and the
# updater that Update now in Settings asks (update.sh), from the deploy folder:
#   sudo host/install.sh
# Creates /etc/biomanager/watchdog.env with a private ntfy topic the first
# time; subscribe to that topic in the ntfy app to get the alerts. Running it
# again is safe; update.sh does, so the units follow each new version.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
deploy=$(dirname "$here")
chmod 755 "$here/watchdog.sh" "$here/maintenance.sh" "$here/update.sh"
# The units name /opt/biomanager/Biomanager/deploy; point them at this one.
for unit in "$here"/biomanager-*.service "$here"/biomanager-*.timer "$here"/biomanager-*.path; do
  sed "s#/opt/biomanager/Biomanager/deploy#$deploy#g" "$unit" > "/etc/systemd/system/$(basename "$unit")"
  chmod 644 "/etc/systemd/system/$(basename "$unit")"
done
# Where the app asks for an update (mounted in it at /control): root's, and
# sticky, so the app can leave a request but not touch what update.sh writes.
mkdir -p "$deploy/control"
chown root:root "$deploy/control"
chmod 1733 "$deploy/control"
echo "update.sh listens through biomanager-update.path" > "$deploy/control/updater"
chmod 644 "$deploy/control/updater"
mkdir -p /etc/biomanager
if [ ! -f /etc/biomanager/watchdog.env ]; then
  umask 077
  cat > /etc/biomanager/watchdog.env <<X
# Alerts: subscribe to this topic in the ntfy app (ntfy.sh). Anyone who knows
# the name can read and post to it, so it is long and random; alerts never
# contain lab data. Empty it to send nothing.
NTFY_TOPIC=biomanager-$(openssl rand -hex 12)
#NTFY_SERVER=https://ntfy.sh
#DISK_LIMIT_PERCENT=85
#BACKUP_MAX_HOURS=26
X
fi
# Where this stack is, so the watchdog reads its .env (and BACKUP_DIR) wherever it was unpacked.
grep -q '^DEPLOY_DIR=' /etc/biomanager/watchdog.env || echo "DEPLOY_DIR=$deploy" >> /etc/biomanager/watchdog.env
systemctl daemon-reload
systemctl enable --now biomanager-watchdog.timer biomanager-maintenance.timer biomanager-update.path
. /etc/biomanager/watchdog.env
echo "Installed. Alerts go to ntfy topic: ${NTFY_TOPIC:-<none>}"
systemctl list-timers 'biomanager-*' --no-pager
echo "Update now in Settings → Devices & copies can update this server."

