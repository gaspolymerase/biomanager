#!/usr/bin/env bash
# Pack what a lab server needs, without the source, for a release:
#
#   scripts/make-server-bundle.sh 0.3.0 biomanager-server.tar.gz
#
# The bundle is deploy/ as it is in the repository, placed where a checkout
# would put it (Biomanager/deploy/), so it unpacks into /opt/biomanager and
# every script, systemd unit and RUNBOOK command works unchanged:
#
#   sudo mkdir -p /opt/biomanager && sudo chown "$USER" /opt/biomanager
#   tar -xzf biomanager-server.tar.gz -C /opt/biomanager
#
# One difference: compose.yaml runs the image of this version
# (ghcr.io/gaspolymerase/biomanager:<version>) instead of building the app
# from source. The release carries that image as files, one per
# architecture, and host/load-image.sh (reading VERSION) puts the right one
# into Docker, so no registry is needed. BIOMANAGER_IMAGE in .env overrides
# the image. Unpacking a newer bundle over it updates the scripts
# and the version; .env, backups and certificates are not in the bundle,
# so they are never overwritten.
set -euo pipefail

version=${1:?usage: make-server-bundle.sh <version> <out.tar.gz>}
out=${2:?usage: make-server-bundle.sh <version> <out.tar.gz>}
version=${version#v}
image=${BIOMANAGER_IMAGE_REPO:-ghcr.io/gaspolymerase/biomanager}
root=$(cd "$(dirname "$0")/.." && pwd)
case "$out" in /*) ;; *) out="$PWD/$out" ;; esac

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/Biomanager"
# Only what git tracks: never a .env, backups or keys from this machine.
git -C "$root" archive --format=tar HEAD deploy | tar -x -C "$stage/Biomanager"

python3 - "$stage/Biomanager/deploy/compose.yaml" "$image:$version" <<'PY'
import sys
path, image = sys.argv[1], sys.argv[2]
text = open(path).read()
build = """  app:
    build:
      context: ..
      dockerfile: Dockerfile
"""
assert text.count(build) == 1, "compose.yaml's app service changed; update make-server-bundle.sh"
text = text.replace(build, f"""  app:
    # The published image of this release (the server bundle has no source
    # to build from). Set BIOMANAGER_IMAGE in .env to run another version.
    image: ${{BIOMANAGER_IMAGE:-{image}}}
""")
open(path, "w").write(text)
PY

echo "$version" > "$stage/Biomanager/deploy/VERSION"

cat > "$stage/Biomanager/deploy/BUNDLE.md" <<EOF
# BioManager server bundle, version $version

This is \`deploy/\` from the BioManager repository, for version $version, set to
run the app image \`$image:$version\` instead of building it from source.
Follow README.md from "First start", skipping \`git clone\`. Before the first
\`docker compose up -d\`, load the image from the release (it downloads the
file for this machine, Intel or ARM):

    host/load-image.sh

Update to a newer version: Update now in Settings → Devices & copies, once
host/install.sh has been run; or sudo host/update.sh. By hand: back up,
unpack the newer bundle over this one, load its image and restart (your .env
and backups are not in the bundle):

    docker compose exec backup backup.sh
    tar -xzf biomanager-server.tar.gz -C /opt/biomanager
    host/load-image.sh && docker compose up -d --build

Step-by-step guides for every way of hosting it:
https://biomanager.org/server.html
EOF

# COPYFILE_DISABLE: macOS's tar would add ._ metadata files beside each one.
COPYFILE_DISABLE=1 tar -C "$stage" -czf "$out" Biomanager
echo "wrote $out ($image:$version)"
