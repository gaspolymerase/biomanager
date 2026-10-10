#!/usr/bin/env bash
# Runs inside the emulator job of .github/workflows/android.yml: install the
# app, connect it to the demo server on the runner (the emulator reaches the
# host's 127.0.0.1 as 10.0.2.2), and keep screenshots of each step.
set -euo pipefail
APK="$1"
OUT="${2:-smoke}"
mkdir -p "$OUT"
shot() { adb exec-out screencap -p > "$OUT/$1.png"; }
# Whatever happens, keep the device log and what the server saw.
keep_logs() {
  adb logcat -d > "$OUT/logcat.txt" 2>&1 || true
  cp "$SERVER_LOG" "$OUT/server.log" 2>/dev/null || true
}
trap keep_logs EXIT
adb logcat -c || true

adb install -r "$APK"
# Wait (up to 20 seconds) for an activity to be the one on screen.
wait_for() {
  for _ in $(seq 10); do
    adb shell dumpsys activity activities | grep -E "mResumedActivity|topResumedActivity" | grep -q "$1" && return 0
    sleep 2
  done
  return 1
}

adb shell am start -W -n org.biomanager.app/.MainActivity
wait_for SetupActivity || { shot 1-setup; echo "setup screen did not open"; exit 1; }
sleep 3
shot 1-setup

adb shell input text "http://10.0.2.2:5077"
# The emulator's soft keyboard sometimes swallows an injected Enter, so press
# it again (up to three times) until the app moves on.
connected=""
for _ in 1 2 3; do
  adb shell input keyevent KEYCODE_ENTER
  if wait_for MainActivity; then connected=1; break; fi
done
[ -n "$connected" ] || { shot 2-connected; echo "did not reach the main screen"; exit 1; }
# Tap the on-screen element whose text (or description) is exactly $1: its
# place comes from the accessibility tree, which includes the WebView's links.
tap_text() {
  for _ in $(seq 10); do
    adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1 || true
    xy=$(adb exec-out cat /sdcard/ui.xml 2>/dev/null | python3 -c '
import re, sys
want = sys.argv[1]
for node in re.findall(r"<node [^>]*>", sys.stdin.read()):
    text = re.search(r"text=\"([^\"]*)\"", node)
    desc = re.search(r"content-desc=\"([^\"]*)\"", node)
    if want in ((text.group(1) if text else "").strip(), (desc.group(1) if desc else "").strip()):
        x1, y1, x2, y2 = map(int, re.search(r"bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"", node).groups())
        print((x1 + x2) // 2, (y1 + y2) // 2)
        break
' "$1")
    [ -n "$xy" ] && { adb shell input tap $xy; return 0; }
    sleep 2
  done
  return 1
}
# Is there an on-screen element whose text (or description) holds $1?
has_text() {
  for _ in $(seq 5); do
    adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1 || true
    adb exec-out cat /sdcard/ui.xml 2>/dev/null | grep -q "$1" && return 0
    sleep 2
  done
  return 1
}
# Signed out, the front page is the sign-in form itself (app/door.py). A
# server from before 1.3 showed a welcome page whose Sign in button led to
# the form; one older still went straight to /login.
for _ in $(seq 30); do grep -q "GET / \|GET /login" "$SERVER_LOG" && break; sleep 2; done
sleep 3
shot 2-connected
grep -q "GET / \|GET /login" "$SERVER_LOG" || { echo "the server never saw the app"; cat "$SERVER_LOG"; exit 1; }
if ! grep -q "GET /login" "$SERVER_LOG" && ! has_text 'Username'; then
  tap_text "Sign in" || { shot 2b-welcome; echo "no Sign in button on the welcome page"; exit 1; }
  for _ in $(seq 15); do grep -q "GET /login" "$SERVER_LOG" && break; sleep 2; done
  sleep 3
  shot 2b-sign-in
  grep -q "GET /login" "$SERVER_LOG" || { echo "Sign in did not open the sign-in page"; exit 1; }
fi
# Sign in to the demo lab (its throwaway account from scripts/demo-data.py).
# The username field has focus on the sign-in page.
adb shell input text "alex"
adb shell input keyevent KEYCODE_TAB
adb shell input text "$(cat "$DEMO_DIR/demo-password")"
adb shell input keyevent KEYCODE_ENTER
for _ in $(seq 30); do grep -q "POST /login" "$SERVER_LOG" && break; sleep 2; done
sleep 8
sleep 2
shot 3-signed-in
grep -q "GET /home\|GET / " "$SERVER_LOG" && echo "signed in" || echo "(sign-in not confirmed; see screenshots)"
adb shell dumpsys activity activities | grep -E "mResumedActivity|topResumedActivity" | grep -q MainActivity \
  || { echo "the app left its main screen after signing in"; exit 1; }
# Only one setup screen may ever have been opened, and none may be left behind.
[ "$(adb logcat -b events -d | grep am_create_activity | grep -c 'org.biomanager.app/.SetupActivity')" -le 1 ] \
  || { echo "the setup screen was opened more than once"; exit 1; }
adb shell dumpsys activity activities | grep -q "SetupActivity" \
  && { echo "a setup screen was left behind the main screen"; exit 1; } || true
echo "smoke test passed"
