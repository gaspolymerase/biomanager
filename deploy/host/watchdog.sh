#!/usr/bin/env bash
# Every few minutes (biomanager-watchdog.timer): is everything a lab member
# or a restore would need actually working? Alerts go to your phone through
# ntfy (NTFY_TOPIC in /etc/biomanager/watchdog.env), only when a check
# starts failing, again every REPEAT_HOURS while it keeps failing, and once
# when it recovers. Without a topic, results only go to the journal:
#   journalctl -u biomanager-watchdog
#
# Alerts say what is wrong, never any data: ntfy topics are public to
# anyone who knows the topic name.
set -uo pipefail

CONF=/etc/biomanager/watchdog.env
[ -f "$CONF" ] && . "$CONF"
: "${DEPLOY_DIR:=/opt/biomanager/Biomanager/deploy}"

# A setting from the lab's .env, as docker compose reads it: a trailing
# "# comment" and surrounding quotes are not part of the value.
env_value() {
  grep -E "^$1=" "$DEPLOY_DIR/.env" 2>/dev/null | tail -n 1 | cut -d= -f2- \
    | sed -E 's/[[:space:]]+#.*$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/; s/[[:space:]]+$//'
}
# Where the backup service writes: BACKUP_DIR in .env (./backups unless
# changed), relative to the deploy folder, as compose.yaml mounts it.
if [ -z "${BACKUP_ROOT:-}" ]; then
  BACKUP_ROOT=$(env_value BACKUP_DIR); BACKUP_ROOT=${BACKUP_ROOT:-./backups}
  case "$BACKUP_ROOT" in /*) ;; *) BACKUP_ROOT="$DEPLOY_DIR/${BACKUP_ROOT#./}" ;; esac
fi
TLS_MODE=$(env_value TLS)
: "${STATE_DIR:=/var/lib/biomanager-watchdog}"
: "${DISK_LIMIT_PERCENT:=85}"
: "${BACKUP_MAX_HOURS:=26}"
: "${RESTORE_TEST_MAX_DAYS:=8}"
: "${CERT_MIN_DAYS:=14}"
: "${REPEAT_HOURS:=6}"
: "${NTFY_SERVER:=https://ntfy.sh}"
: "${NTFY_TOPIC:=}"
DOMAIN=${DOMAIN:-$(env_value DOMAIN)}
# 443 unless host/ports.sh found it taken here and chose another.
HTTPS_PORT=$(env_value HTTPS_PORT); HTTPS_PORT=${HTTPS_PORT:-443}
SITE="https://$DOMAIN"; [ "$HTTPS_PORT" = 443 ] || SITE="$SITE:$HTTPS_PORT"

mkdir -p "$STATE_DIR"
now=$(date +%s)
host=$(hostname)

notify() {  # title, message, priority (default|high|low), tags
  echo "[watchdog] $1: $2"
  [ -z "$NTFY_TOPIC" ] && return 0
  curl -fsS -m 15 -o /dev/null \
    -H "Title: $1" -H "Priority: ${3:-default}" -H "Tags: ${4:-warning}" \
    -d "$2" "$NTFY_SERVER/$NTFY_TOPIC" || echo "[watchdog] could not reach $NTFY_SERVER"
}

# One check: name, then "ok" or a description of the problem.
report() {
  local name=$1 problem=$2 flag="$STATE_DIR/$1.failing"
  if [ "$problem" = "ok" ]; then
    if [ -f "$flag" ]; then
      rm -f "$flag"
      notify "BioManager: $name OK again" "$host: $name is back to normal." low white_check_mark
    fi
    return
  fi
  if [ ! -f "$flag" ] || [ $(( now - $(stat -c %Y "$flag") )) -ge $(( REPEAT_HOURS * 3600 )) ]; then
    notify "BioManager: $name" "$host: $problem" high rotating_light
    touch "$flag"
  fi
}

# --- the site answers, through Caddy, over HTTPS. With TLS=internal the
# certificate is Caddy's own, which this machine doesn't trust: check it
# against Caddy's root, as the lab's computers do once they have it.
trust=()
if [ "${TLS_MODE:-internal}" = "internal" ]; then
  root=$(mktemp)
  trap 'rm -f "$root"' EXIT
  (cd "$DEPLOY_DIR" && docker compose exec -T caddy cat /data/caddy/pki/authorities/local/root.crt) > "$root" 2>/dev/null \
    && [ -s "$root" ] && trust=(--cacert "$root")
fi
if [ -n "$DOMAIN" ]; then
  if curl -fsS -m 20 "${trust[@]}" -o /dev/null "$SITE/healthz"; then report site ok
  else report site "$SITE/healthz does not answer. Check: cd $DEPLOY_DIR && docker compose ps"; fi
fi

# --- every service is running, and healthy where it has a health check
problems=""
for svc in db app caddy backup; do
  id=$(cd "$DEPLOY_DIR" && docker compose ps -q "$svc" 2>/dev/null)
  if [ -z "$id" ]; then problems="$problems $svc:missing"; continue; fi
  state=$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$id" 2>/dev/null)
  case "$state" in
    "running healthy"|"running "|"running starting") ;;
    *) problems="$problems $svc:${state// /-}" ;;
  esac
done
[ -z "$problems" ] && report containers ok || report containers "not all services are well:$problems"

# --- room on the disk (database, images, 30 nights of local backups)
used=$(df --output=pcent / | tail -1 | tr -dc '0-9')
[ "$used" -lt "$DISK_LIMIT_PERCENT" ] && report disk ok || report disk "the disk is ${used}% full (alert at ${DISK_LIMIT_PERCENT}%)"

# --- a good backup recently, and a restore test this week
age_of() { [ -f "$1" ] && echo $(( now - $(cat "$1") )) || echo 999999999; }
b=$(age_of "$BACKUP_ROOT/last-success")
[ "$b" -lt $(( BACKUP_MAX_HOURS * 3600 )) ] && report backup ok \
  || report backup "the last good backup is $(( b / 3600 )) hours old. See: docker compose logs backup"
t=$(age_of "$BACKUP_ROOT/last-restore-test")
[ "$t" -lt $(( RESTORE_TEST_MAX_DAYS * 86400 )) ] && report restore-test ok \
  || report restore-test "no successful restore test for $(( t / 86400 )) days"

# --- off-site copies, once they are set up (RESTIC_REPOSITORY in .env)
if [ -n "$(env_value RESTIC_REPOSITORY)" ]; then
  o=$(age_of "$BACKUP_ROOT/last-offsite")
  [ "$o" -lt $(( BACKUP_MAX_HOURS * 3600 )) ] && report offsite ok \
    || report offsite "the last off-site copy is $(( o / 3600 )) hours old. See: docker compose logs backup"
  ot=$(age_of "$BACKUP_ROOT/last-offsite-test")
  [ "$ot" -lt $(( RESTORE_TEST_MAX_DAYS * 86400 )) ] && report offsite-restore-test ok \
    || report offsite-restore-test "the off-site copy has not been read back for $(( ot / 86400 )) days"
fi

# --- the HTTPS certificate is not about to lapse (Tailscale or Let's
# Encrypt renew it). Caddy's own (TLS=internal) lasts hours and Caddy
# renews it itself, so there is nothing to warn about there.
if [ -n "$DOMAIN" ] && [ "${TLS_MODE:-internal}" != "internal" ]; then
  end=$(echo | timeout 20 openssl s_client -connect "$DOMAIN:$HTTPS_PORT" -servername "$DOMAIN" 2>/dev/null \
        | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
  if [ -n "$end" ]; then
    left=$(( ($(date -d "$end" +%s) - now) / 86400 ))
    [ "$left" -ge "$CERT_MIN_DAYS" ] && report certificate ok \
      || report certificate "the HTTPS certificate expires in $left days"
  else
    report certificate "could not read the HTTPS certificate of $DOMAIN"
  fi
fi

# --- still on the Tailscale network (how the lab reaches it)
if command -v tailscale >/dev/null; then
  if tailscale status --json 2>/dev/null | jq -e '.BackendState == "Running"' >/dev/null; then report tailscale ok
  else report tailscale "Tailscale is not running: sudo tailscale up"; fi
fi
exit 0
