"""Calendar → Mac Calendar against the Mac's real calendars (EventKit), where
tests/test_mac_calendar.py uses a stand-in. Run on a Mac whose calendars this
process may use (.github/workflows/mac-calendar.yml gives a CI Mac that with
grant.py), from anywhere:

    python scripts/mac-calendar-check/live.py            # both
    python scripts/mac-calendar-check/live.py TheStore   # what goes in comes out

It makes calendars on the Mac and removes them again. TZ picks the time zone
(the same answers are wanted in each).
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
# tests.base first: it points the app at a throwaway database before app is imported.
from tests.base import AppTestCase, SessionLocal, TODAY, client_for, make_user, uniq  # noqa: E402
from app import mac_calendar  # noqa: E402
from app.app import app  # noqa: E402
from app.mac_calendar import Entry, EventKitStore, _clean, plan  # noqa: E402

ZONE = os.environ.get("TZ") or "the Mac's zone"
CHECK_CALENDAR = "BioManager check"


def say(*parts) -> None:
    print(f"[{ZONE}]", *parts, flush=True)


def real_store() -> EventKitStore:
    st = EventKitStore()
    with st.pool():
        if st.access() != "granted":
            say("asked:", st.request_access(timeout=20))
        say("access:", st.access())
    return st


def own_account(st: EventKitStore) -> str:
    """The Mac's own account (On My Mac), else the first that takes calendars."""
    sources = list(st.store.sources() or [])
    say("accounts:", [(str(s.title()), int(s.sourceType())) for s in sources])
    local = [s for s in sources if int(s.sourceType()) == 0] or st._sources()
    if not local:
        raise unittest.SkipTest("no account on this Mac takes a calendar")
    return str(local[0].sourceIdentifier())


def moment(day: date, hour: int, minute: int) -> int:
    return int(datetime(day.year, day.month, day.day, hour, minute).timestamp())


def clocks_change(today: date) -> date:
    """The next day in this time zone whose clock goes forward or back, if
    one comes within the window; else any day in it."""
    def offset(day):
        return datetime(day.year, day.month, day.day, 12).astimezone().utcoffset()
    for n in range(1, 50):
        day = today + timedelta(days=n)
        if offset(day) != offset(day - timedelta(days=1)):
            return day
    return today + timedelta(days=9)


class TheStore(unittest.TestCase):
    """What goes in comes back out the same. Otherwise every update (each
    15 minutes) would take everything out and put it back."""

    def test_round_trip(self):
        st = real_store()
        self.assertEqual(st.access(), "granted", "this process may not use the calendars")
        d = date.today()
        change = clocks_change(d)
        wanted = [
            Entry("Wean cage 12", True, d + timedelta(days=1), d + timedelta(days=1), ""),
            Entry("出差 · conference", True, d + timedelta(days=2), d + timedelta(days=4), _clean("day one  \r\nday two\r\n")),
            Entry("Perfuse #4", False, moment(d + timedelta(days=3), 14, 30), moment(d + timedelta(days=3), 15, 45), ""),
            Entry("Overnight imaging", False, moment(d + timedelta(days=5), 22, 0), moment(d + timedelta(days=6), 6, 0),
                  "scope 2"),
            Entry("Clocks change", True, change, change, ""),
            Entry("Clocks change 01:30", False, moment(change, 1, 30), moment(change, 2, 30), ""),
            Entry("Same twice", True, d + timedelta(days=7), d + timedelta(days=7), ""),
            Entry("Same twice", True, d + timedelta(days=7), d + timedelta(days=7), ""),
        ]
        with st.pool():
            cal = st.create(own_account(st), CHECK_CALENDAR)
        try:
            with st.pool():
                self.assertIn(cal, [c["id"] for c in st.calendars()])
                for e in wanted:
                    st.add(cal, e)
                st.commit()
            first, last = d - timedelta(days=30), d + timedelta(days=60)
            fresh = EventKitStore()   # as after the app restarts
            with fresh.pool():
                back = fresh.events(cal, first, last)
                for _h, e in sorted(back, key=lambda x: (str(x[1].start), x[1].title)):
                    say("   ", e)
                add, remove = plan(wanted, back)
                self.assertEqual((add, len(remove)), ([], 0),
                                 f"not the same coming out: to add {add}, to take out "
                                 f"{[e for h, e in back if h in remove]}")
                fresh.remove(back[0][0])
                fresh.commit()
                self.assertEqual(len(fresh.events(cal, first, last)), len(wanted) - 1)
        finally:
            with st.pool():
                st.remove_calendar(cal)
                self.assertIsNone(st.find(cal, "", CHECK_CALENDAR))
        say("what goes in comes out:", len(wanted), "events")


class Counting:
    """The real store, counting what it is asked to change."""

    def __init__(self, real):
        self.real, self.adds, self.removes, self.commits = real, 0, 0, 0

    def __getattr__(self, name):
        return getattr(self.real, name)

    def add(self, *args):
        self.adds += 1
        return self.real.add(*args)

    def remove(self, *args):
        self.removes += 1
        return self.real.remove(*args)

    def commit(self):
        self.commits += 1
        return self.real.commit()


class TheApp(AppTestCase):
    """Calendar → Mac Calendar as its dialog drives it, into a new calendar
    in On My Mac: start, update, the whole lab, stop."""

    def setUp(self):
        super().setUp()
        self.st = Counting(real_store())
        mac_calendar.use_store(self.st)
        self.addCleanup(mac_calendar.use_store, None)
        app.config["LOCAL_SETUP"] = True
        self.addCleanup(app.config.pop, "LOCAL_SETUP", None)
        self.c = client_for(make_user(uniq("mac")))

    def call(self, **body):
        j = self.c.post("/calendar/mac", data=json.dumps(body), content_type="application/json").get_json()
        say(body.get("action"), "->", {k: j.get(k) for k in ("ok", "error", "setup")})
        return j

    def event(self, title, day, start="00:00:00", end="23:59:59", allday=True, body=""):
        r = self.c.post("/calendar/items", content_type="application/json", data=json.dumps(
            {"kind": "event", "title": title, "start": f"{day.isoformat()}T{start}", "end": f"{day.isoformat()}T{end}",
             "isAllday": allday, "body": body})).get_json()
        self.assertTrue(r["ok"], r)
        return r["item"]["raw"]["rowId"]

    def titles(self, cal):
        first, last = mac_calendar._window()
        with self.st.pool():
            return sorted(e.title for _h, e in EventKitStore().events(cal, first, last))

    def test_start_update_stop(self):
        allday, timed, gone = uniq("Perfuse"), uniq("Surgery"), uniq("Cancelled")
        self.event(allday, TODAY + timedelta(days=3))
        self.event(timed, TODAY + timedelta(days=4), "09:15:00", "10:45:00", allday=False, body="room 4\nbring drapes")
        gone_id = self.event(gone, TODAY + timedelta(days=5))

        j = self.c.get("/calendar/mac").get_json()
        self.assertEqual(j["access"], "granted", j)
        say("offered:", j["accounts"])
        self.assertIn("On My Mac", [a["title"] for a in j["accounts"]])
        account = own_account(self.st.real)

        j = self.call(action="start", scope="mine", target=f"new:{account}")
        self.assertTrue(j["ok"], j)
        self.assertEqual((j["setup"]["error"], j["setup"]["account"]), ("", "On My Mac"), j)
        with SessionLocal() as s:
            cal = mac_calendar.config(s)["calendar"]
        on_mac = self.titles(cal)
        for title in (allday, timed, gone):
            self.assertEqual(on_mac.count(title), 1, title)

        self.st.adds = self.st.removes = self.st.commits = 0
        self.call(action="now")
        self.assertEqual((self.st.adds, self.st.removes, self.st.commits), (0, 0, 0),
                         "an update with nothing new changed the Mac's calendar")

        # Taken out in the app, it goes; one put there by hand goes too.
        self.c.post(f"/calendar/items/event-{gone_id}/delete")
        with self.st.pool():
            self.st.real.add(cal, Entry("Dentist", True, TODAY + timedelta(days=1), TODAY + timedelta(days=1), ""))
            self.st.real.commit()
        self.assertEqual(self.call(action="now")["setup"]["error"], "")
        on_mac = self.titles(cal)
        self.assertNotIn(gone, on_mac)
        self.assertNotIn("Dentist", on_mac)
        self.assertEqual(on_mac.count(allday), 1)

        self.assertEqual(self.call(action="scope", scope="lab")["setup"]["error"], "")
        self.assertIsNone(self.call(action="stop")["setup"])
        with self.st.pool():
            self.assertIsNone(EventKitStore().find(cal, account, "BioManager"))


if __name__ == "__main__":
    say("today", date.today(), "UTC", time.strftime("%z"))
    unittest.main(verbosity=2)
