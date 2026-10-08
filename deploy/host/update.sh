#!/usr/bin/env bash
# Update this server to BioManager's newest release. Run when an admin
# presses Update now in Settings → Devices & copies, or by hand:
#
#   sudo host/update.sh
#
# The app runs in a container and cannot restart its own server, so it asks:
# it writes control/update-request (mounted in the app at /control), and
# biomanager-update.path (host/install.sh) starts this. Nothing in the request
# is used: this always installs the latest release GitHub lists for
# gaspolymerase/biomanager, and only when it is newer than VERSION here.
#
#   1. a backup, as before any update;
#   2. the release's server bundle and the app image for this machine, each
#      checked against the SHA-256 GitHub publishes for it;
#   3. the image loaded into Docker (the running app is not touched yet);
#   4. the bundle unpacked over this one (.env, backups and certificates
#      are not in it) and the stack restarted, --build for the backup image;
#   5. the app checked healthy. Start-up brings the database up to date.
#
# How far it got goes to control/update-status.json, which the page shows,
# and the result to ntfy like the watchdog's alerts. If it stops, see
# "Updating went wrong" in RUNBOOK.md.
set -uo pipefail

# Bash reads a script as it runs, and step 4 replaces this file: run a copy.
if [ -z "${BIOMANAGER_UPDATE_COPY:-}" ]; then
  # (BIOMANAGER_UPDATE_TEST: tests/test_server_updates.py, against stand-ins.)
  [ "$(id -u)" = 0 ] || [ -n "${BIOMANAGER_UPDATE_TEST:-}" ] || { echo "run with sudo"; exit 1; }
  copy=$(mktemp /tmp/biomanager-update.XXXXXX) && cp "$0" "$copy" || exit 1
  BIOMANAGER_UPDATE_COPY=$copy BIOMANAGER_DEPLOY_DIR=$(cd "$(dirname "$0")/.." && pwd) exec bash "$copy" "$@"
fi

DEPLOY_DIR=$BIOMANAGER_DEPLOY_DIR
CONTROL=$DEPLOY_DIR/control
API=https://api.github.com/repos/gaspolymerase/biomanager/releases/latest
CONF=/etc/biomanager/watchdog.env
[ -f "$CONF" ] && . "$CONF"
: "${NTFY_SERVER:=https://ntfy.sh}"
: "${NTFY_TOPIC:=}"

work=$(mktemp -d /tmp/biomanager-update.XXXXXX)
cleanup() { rm -rf "$work" "$BIOMANAGER_UPDATE_COPY"; }
trap cleanup EXIT
# Taken now, so the path unit does not start this again for the same request.
rm -f "$CONTROL/update-request"

current=$(cat "$DEPLOY_DIR/VERSION" 2>/dev/null || true)
target=""
step=check

# control/ is written by the app (host/install.sh makes it root's, sticky, so
# the app can add a request but not touch what root writes there).
status() {   # state [detail]
  [ -d "$CONTROL" ] || return 0
  local tmp detail=${2:-}
  detail=${detail//\"/\'}
  tmp=$(mktemp "$CONTROL/.status.XXXXXX") || return 0
  printf '{"state": "%s", "step": "%s", "version": "%s", "from": "%s", "detail": "%s", "at": "%s"}\n' \
    "$1" "$step" "$target" "$current" "$detail" "$(date -u +%Y-%m-%dT%H:%M:%S)" > "$tmp"
  chmod 644 "$tmp" && mv -f "$tmp" "$CONTROL/update-status.json"
}
notify() {
  echo "[update] $1: $2"
  [ -z "$NTFY_TOPIC" ] && return 0
  curl -fsS -m 15 -o /dev/null -H "Title: $1" -H "Priority: ${3:-low}" -H "Tags: ${4:-arrow_up}" \
    -d "$2" "$NTFY_SERVER/$NTFY_TOPIC" || true
}
fail() {
  status failed "$1"
  notify "BioManager: update failed" "$(hostname): $1" high rotating_light
  exit 1
}
running() { step=$1; status running; echo "[update] $1"; }

cd "$DEPLOY_DIR" || fail "no $DEPLOY_DIR"
[ -n "$current" ] || fail "this server runs from a checkout, not a server bundle; update it with git pull"
if grep -q '^BIOMANAGER_IMAGE=.' .env 2>/dev/null; then
  fail "BIOMANAGER_IMAGE in .env picks the app's version; remove it to update from here"
fi
command -v python3 > /dev/null || fail "python3 is needed to read GitHub's answer"

# Never at the same time as the weekly maintenance (maintenance.sh).
exec 9> /run/lock/biomanager-stack.lock 2> /dev/null || exec 9> "${TMPDIR:-/tmp}/biomanager-stack.lock"
running check
flock -w 2700 9 || fail "the weekly maintenance is still running; try again later"

case "$(uname -m)" in
  x86_64 | amd64) arch=amd64 ;;
  aarch64 | arm64) arch=arm64 ;;
  *) fail "no BioManager image for $(uname -m)" ;;
esac
curl -fsSL -m 30 --retry 2 -H "Accept: application/vnd.github+json" -H "User-Agent: BioManager/$current" \
  -o "$work/release.json" "$API" || fail "could not reach GitHub to see the newest release"

# The newest version, whether it is newer than this one (a release
# candidate comes before its release, as app/releases.py has it), and each
# file's address and SHA-256.
read -r target newer bundle_url bundle_sha image_url image_sha < <(python3 - "$work/release.json" "$current" "$arch" <<'PY'
import json, re, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
def parse(text):
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)(?:-[A-Za-z]*\.?(\d*))?", text or "")
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split(".")), (int(m.group(2) or 0),) if "-" in text else None
tag = str(data.get("tag_name") or "").lstrip("v")
a, b = parse(tag), parse(sys.argv[2])
newer = "no"
if a and b and re.fullmatch(r"\d+(\.\d+)*(-[A-Za-z]+\.?\d*)?", tag):
    width = max(len(a[0]), len(b[0]))
    x, y = a[0] + (0,) * (width - len(a[0])), b[0] + (0,) * (width - len(b[0]))
    if x != y:
        newer = "yes" if x > y else "no"
    elif a[1] is None or b[1] is None:
        newer = "yes" if a[1] is None and b[1] is not None else "no"
    else:
        newer = "yes" if a[1] > b[1] else "no"
assets = {x.get("name"): x for x in data.get("assets") or []}
def pick(name):
    asset = assets.get(name) or {}
    digest = str(asset.get("digest") or "")
    return [asset.get("browser_download_url") or "-", digest.split(":", 1)[1] if digest.startswith("sha256:") else "-"]
print(" ".join([tag or "-", newer] + pick("biomanager-server.tar.gz") + pick(f"biomanager-image-{sys.argv[3]}.tar.gz")))
PY
)
[ -n "$target" ] && [ "$target" != "-" ] || { target=""; fail "GitHub's answer had no version in it"; }
if [ "$newer" != yes ]; then
  step=check; status current
  echo "[update] BioManager $current is the newest ($target on GitHub)."
  exit 0
fi
for v in "$bundle_url" "$bundle_sha" "$image_url" "$image_sha"; do
  [ "$v" != "-" ] || fail "release $target is missing its server files or their checksums"
done

running backup
docker compose exec -T backup backup.sh < /dev/null || fail "the backup before updating failed, so nothing was updated"

running download
fetch() {   # url sha256 file
  curl -fsSL --retry 3 -o "$3" "$1" || return 1
  echo "$2  $3" | sha256sum -c --status
}
fetch "$bundle_url" "$bundle_sha" "$work/server.tar.gz" || fail "the server bundle did not download, or did not match its checksum"
fetch "$image_url" "$image_sha" "$work/image.tar.gz" || fail "the app image did not download, or did not match its checksum"
mkdir "$work/new" && tar --no-same-owner -xzf "$work/server.tar.gz" -C "$work/new" \
  || fail "the server bundle would not unpack"
new=$work/new/Biomanager/deploy
[ "$(cat "$new/VERSION" 2>/dev/null)" = "$target" ] || fail "the bundle is not version $target"

running image
bash "$new/host/load-image.sh" "$work/image.tar.gz" < /dev/null || fail "the app image would not load into Docker"

running restart
chown -R "$(stat -c %u:%g "$DEPLOY_DIR")" "$new"
cp -a "$new/." "$DEPLOY_DIR/" || fail "could not unpack the new version over this one"
docker compose up -d --build < /dev/null || fail "could not restart the services"
# This version's timers and updater, in case they changed.
bash "$DEPLOY_DIR/host/install.sh" > /dev/null || echo "[update] host/install.sh did not finish; run it by hand"

for i in $(seq 1 72); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q app)")" = healthy ] && break
  sleep 5
done
[ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q app)")" = healthy ] \
  || fail "the app is not healthy after the update. See: docker compose logs app"

docker image prune -f > /dev/null
step=done; status done
notify "BioManager: updated to $target" "$(hostname): $current → $target, app healthy." low white_check_mark
