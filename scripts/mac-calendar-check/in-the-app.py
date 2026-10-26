"""Calendar → Mac Calendar in a built Mac app, through its own pages as its
dialog uses them: the app (already running on PORT, on a demo lab made by
scripts/demo-data.py in DATA) starts a BioManager calendar in On My Mac, the
Mac's calendars then hold what it said, and Stop takes the calendar away.
The release workflow runs it on each Mac build; this process needs the
calendars too (grant.py), to look.

    PORT=5098 DATA=/tmp/demo python scripts/mac-calendar-check/in-the-app.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.mac_calendar import EventKitStore, _window  # noqa: E402

BASE = f"http://127.0.0.1:{os.environ['PORT']}"
web = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def call(path, form=None, body=None):
    headers = {"Origin": BASE}
    data = None
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode("utf-8"), "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
    try:
        with web.open(urllib.request.Request(BASE + path, data=data, headers=headers), timeout=120) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def fail(why: str) -> None:
    sys.exit(f"Mac Calendar: {why}")


password = Path(os.environ["DATA"], "demo-password").read_text(encoding="utf-8").strip()
call("/login", form={"username": "alex", "password": password})
status, page = call("/calendar")
if 'id="cal-mac-btn"' not in page:
    fail(f"the Calendar page ({status}) has no Mac Calendar button")
status, text = call("/calendar/mac")
if status != 200:
    fail(f"not offered ({status}): {text[:300]}")
j = json.loads(text)
print("access:", j["access"], "accounts:", j["accounts"])
if j["access"] != "granted":
    fail(f"the app may not use the calendars ({j['access']})")
mine = next((a for a in j["accounts"] if a["title"] == "On My Mac"), None)
if mine is None:
    fail(f"no On My Mac among {j['accounts']}")

status, text = call("/calendar/mac", body={"action": "start", "scope": "lab", "target": f"new:{mine['id']}"})
setup = json.loads(text).get("setup") or {}
print("start:", status, setup)
if not setup or setup.get("error") or not setup.get("count"):
    fail(f"start: {text[:600]}")

# What the Mac's calendars now hold, seen from outside the app.
store = EventKitStore()
with store.pool():
    made = [c for c in store.calendars() if c["title"] == "BioManager" and c["source"] == mine["id"]]
    if len(made) != 1:
        fail(f"{len(made)} BioManager calendars in On My Mac")
    first, last = _window()
    held = store.events(made[0]["id"], first, last)
print(f"the Mac's calendar holds {len(held)} events; the app sent {setup['count']}")
if len(held) != setup["count"]:
    fail("the Mac's calendar doesn't hold what the app sent")

status, text = call("/calendar/mac", body={"action": "stop"})
print("stop:", status, json.loads(text).get("setup"))
# A store opened before Stop still has the calendar cached: ask a new one,
# and give the Mac's calendar service a moment.
for _ in range(10):
    fresh = EventKitStore()
    with fresh.pool():
        if fresh.find(made[0]["id"], mine["id"], "BioManager") is None:
            break
    time.sleep(1)
else:
    fail("Stop left the calendar on the Mac")
print("Mac Calendar: OK")
