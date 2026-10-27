#!/bin/bash
# The Linux app on a bare machine: Ubuntu 22.04 with none of the graphics
# libraries Qt's window needs (libEGL, Wayland, OpenGL). The window can't
# open there, so the app must open in the web browser instead and keep
# answering (desktop_linux.py), rather than stop with a traceback.
#
#     scripts/bare-linux-check.sh BioManager-Linux.AppImage
#     scripts/bare-linux-check.sh dist/BioManager/BioManager
#
# Needs Docker. The release workflow runs it on each Linux build.
set -u
app=$(realpath "$1")
dir=$(dirname "$app")
# The release builds on 22.04, so the same C library; Google's mirror of Docker Hub, which doesn't limit anonymous pulls.
image=${BARE_IMAGE:-mirror.gcr.io/library/ubuntu:22.04}
port=5097
name=bm-bare-$$
docker run -d --name "$name" --network host -v "$dir:/app:ro" \
  -e APPIMAGE_EXTRACT_AND_RUN=1 -e BIOMANAGER_PORT=$port -e BIOMANAGER_DATA_DIR=/tmp/data -e BIOMANAGER_TELEMETRY=0 \
  "$image" "/app/$(basename "$app")" > /dev/null
answered=no
for i in $(seq 60); do
  if curl -fs -o /dev/null "http://127.0.0.1:$port/register"; then answered=yes; break; fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$name")" != true ]; then break; fi
  sleep 2
done
sleep 2
log=$(docker logs "$name" 2>&1)
docker rm -f "$name" > /dev/null
if [ $answered = yes ] && grep -q "is open in your web browser" <<< "$log"; then
  echo "bare machine: the app opened in the browser and answers"
  exit 0
fi
echo "bare machine: the app didn't answer (answered=$answered)"
echo "$log" | tail -40
exit 1
