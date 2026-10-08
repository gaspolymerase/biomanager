#!/usr/bin/env bash
# Let guests in from the internet, or stop, on the server:
#
#   sudo /opt/biomanager/Biomanager/deploy/host/internet-access.sh on
#   sudo /opt/biomanager/Biomanager/deploy/host/internet-access.sh off
#   sudo /opt/biomanager/Biomanager/deploy/host/internet-access.sh status
#
# On: Tailscale Funnel publishes https://<DOMAIN>:8443 on the internet and
# forwards it to Caddy's internet-facing site (deploy/Caddyfile, :8081 on
# this host only). There, anyone not signed in sees only the guest-code page
# (app/guests.py): make a code with New guest pass, in Settings → People & access. The lab keeps
# using https://<DOMAIN> over Tailscale as before.
#
# The first time, Tailscale may print a link to allow Funnel for this
# machine in the tailnet's policy: open it, allow it, and run `on` again.
#
# Off: nothing is on the internet any more. Guests already signed in lose
# the way in, and codes work only for someone on the lab's network.
set -euo pipefail

DEPLOY_DIR=${DEPLOY_DIR:-/opt/biomanager/Biomanager/deploy}
ENV_FILE="$DEPLOY_DIR/.env"
PORT=8443
[ "$(id -u)" = 0 ] || { echo "Run it with sudo."; exit 1; }
[ -f "$ENV_FILE" ] || { echo "No $ENV_FILE: set the server up first (deploy/README.md)."; exit 1; }

# As docker compose reads it: a trailing "# comment" and quotes are not the value.
get() { grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- \
          | sed -E 's/[[:space:]]+#.*$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/; s/[[:space:]]+$//' || true; }
put() {  # KEY VALUE: replace that line or add it, keeping the file's owner and mode
  local tmp
  tmp=$(mktemp "$ENV_FILE.XXXXXX")
  KEY="$1" VALUE="$2" awk 'BEGIN { k = ENVIRON["KEY"]; v = ENVIRON["VALUE"]; done = 0 }
    index($0, k "=") == 1 { if (!done) { print k "=" v; done = 1 } ; next }
    { print }
    END { if (!done) print k "=" v }' "$ENV_FILE" > "$tmp"
  chown --reference="$ENV_FILE" "$tmp"
  chmod --reference="$ENV_FILE" "$tmp"
  mv "$tmp" "$ENV_FILE"
}
# The app shows the address on its Guests page; recreating it takes seconds.
tell_app() { put BIOMANAGER_PUBLIC_URL "$1"; (cd "$DEPLOY_DIR" && docker compose up -d app caddy >/dev/null); }

DOMAIN=$(get DOMAIN)
[ -n "$DOMAIN" ] || { echo "DOMAIN is not set in $ENV_FILE."; exit 1; }
URL="https://$DOMAIN:$PORT"
# Where compose.yaml publishes that site on this host: 8081 unless host/ports.sh chose another.
LOCAL=$(get FUNNEL_PORT); LOCAL="http://127.0.0.1:${LOCAL:-8081}"

case "${1:-status}" in
  on)
    curl -fsS -o /dev/null --max-time 5 "$LOCAL/healthz" || {
      echo "Caddy's internet-facing site does not answer on $LOCAL."
      echo "Update BioManager first (deploy/README.md, Updating), then run this again."
      exit 1
    }
    # Until Funnel is allowed for this machine, tailscale prints a link and
    # waits for it: give up after a minute instead of hanging.
    if ! timeout 60 tailscale funnel --bg --yes --https="$PORT" "$LOCAL"; then
      echo
      echo "Funnel is not on. If Tailscale printed a link above, open it, allow Funnel, and run this again."
      exit 1
    fi
    tell_app "$URL"
    echo
    echo "On. Guests open $URL/guest and enter the code from New guest pass (Settings → People & access)."
    echo "Switch it off when they are done: sudo $0 off"
    ;;
  off)
    tailscale funnel --https="$PORT" off 2>/dev/null || true
    tell_app ""
    echo "Off. BioManager is reachable over Tailscale only."
    ;;
  status)
    tailscale funnel status
    echo "BIOMANAGER_PUBLIC_URL=$(get BIOMANAGER_PUBLIC_URL)"
    ;;
  *)
    echo "Usage: sudo $0 on|off|status"; exit 2 ;;
esac
