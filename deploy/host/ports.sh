#!/usr/bin/env bash
# Chooses the ports BioManager listens on, once, before it first starts:
# 80 and 443 when they are free, or others when something on this machine
# already has them (a NAS's own web pages often do). The choice goes into
# .env as HTTP_PORT, HTTPS_PORT and FUNNEL_PORT and is kept from then on;
# the app's address then carries the port, e.g. https://nas.local:4443.
#
#   deploy/host/ports.sh      after writing .env, before `docker compose up -d`
#
# It needs no sudo. Run again, it changes nothing.
set -euo pipefail

here=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE="$here/.env"
[ -f "$ENV_FILE" ] || { echo "No $ENV_FILE: copy .env.example to .env and fill it in first."; exit 1; }

# As docker compose reads it: a trailing "# comment" and quotes are not the value.
get() { grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- \
          | sed -E 's/[[:space:]]+#.*$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/; s/[[:space:]]+$//' || true; }
put() {  # KEY VALUE: replace that line or add it; writing over the file keeps its owner and mode
  local tmp
  tmp=$(mktemp)
  KEY="$1" VALUE="$2" awk 'BEGIN { k = ENVIRON["KEY"]; v = ENVIRON["VALUE"]; done = 0 }
    index($0, k "=") == 1 { if (!done) { print k "=" v; done = 1 } ; next }
    { print }
    END { if (!done) print k "=" v }' "$ENV_FILE" > "$tmp"
  cat "$tmp" > "$ENV_FILE"
  rm -f "$tmp"
}

DOMAIN=$(get DOMAIN)
address() { if [ "$1" = 443 ]; then echo "https://$DOMAIN"; else echo "https://$DOMAIN:$1"; fi; }

if [ -n "$(get HTTPS_PORT)" ]; then
  echo "Ports already chosen: BioManager is at $(address "$(get HTTPS_PORT)")."
  exit 0
fi

listening() {  # is something on this machine listening on TCP port $1?
  if command -v ss >/dev/null 2>&1; then
    [ -n "$(ss -Hltn "sport = :$1" 2>/dev/null)" ] && return 0
  elif command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1 && return 0
  fi
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}
first_free() {
  local port
  for port in "$@"; do listening "$port" || { echo "$port"; return 0; }; done
  return 1
}

# A server set up before this script existed is already on 443 itself: keep it there.
if [ -n "$DOMAIN" ] && curl -fsk -m 5 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/healthz" 2>/dev/null | grep -qx ok; then
  https=443 http=80 funnel=8081
else
  # Not 8443: Tailscale Funnel uses it for the internet (host/internet-access.sh).
  https=$(first_free 443 4443 7443 9443 10443) || { echo "Ports 443, 4443, 7443, 9443 and 10443 are all in use here. Set HTTPS_PORT in .env to a free one."; exit 1; }
  http=$(first_free 80 8080 8880 8888 10080) || { echo "Ports 80, 8080, 8880, 8888 and 10080 are all in use here. Set HTTP_PORT in .env to a free one."; exit 1; }
  funnel=$(first_free 8081 8082 18081 28081) || { echo "Ports 8081, 8082, 18081 and 28081 are all in use here. Set FUNNEL_PORT in .env to a free one."; exit 1; }
fi

if [ "$(get TLS)" = acme ] && { [ "$https" != 443 ] || [ "$http" != 80 ]; }; then
  echo "A Let's Encrypt certificate (TLS=acme) needs ports 80 and 443, but something on this machine has them."
  echo "Free them, or use TLS=internal or TLS=tailscale, which work on any port."
  exit 1
fi

put HTTP_PORT "$http"
put HTTPS_PORT "$https"
put FUNNEL_PORT "$funnel"
[ "$https" = 443 ] || echo "Port 443 is already in use on this machine (by its own web pages, perhaps), so BioManager uses $https."
[ "$http" = 80 ] || echo "Port 80 is in use too: plain http:// goes to port $http and is sent on to HTTPS."
echo "BioManager will be at $(address "$https")."
