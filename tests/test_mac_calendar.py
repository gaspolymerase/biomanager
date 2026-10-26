"""The calendar in the Mac's own Calendar app (app/mac_calendar.py), with a
stand-in for EventKit: what goes in, what comes out, who may set it up, and
what the packaged Mac app needs for macOS to let it ask."""
from __future__ import annotations

import json
import unittest
from unittest import mock
from datetime import date, datetime, timedelta
from pathlib import Path

# tests.base first: it points the app at a throwaway database before app is imported.
from tests.base import AppTestCase, SessionLocal, TODAY, client_for, make_user, uniq
from app import mac_calendar  # noqa: E402
from app.app import app  # noqa: E402
from app.mac_calendar import Entry, StoreError, entry, plan  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class FakeStore:
    """EventKit as the module uses it, its events in `held`. An account
    called Google can't be given a calendar, as on a real Mac."""

    def __init__(self, access="granted"):
        self.state = access
        self.sources = {"icloud": "iCloud", "google": "sam@gmail.com"}
        self.cals = {"home": {"title": "Home", "source": "icloud"}}
        self.held: dict[str, list] = {"home": []}
        self.asked = 0
        self.commits = 0
        self.serial = 0

    def pool(self):
        from contextlib import nullcontext
        return nullcontext()

    def access(self):
        return self.state

    def request_access(self, timeout=0):
        self.asked += 1
        self.state = "granted"
        return True

    def accounts(self):
        return [{"id": k, "title": v} for k, v in self.sources.items()]

    def calendars(self):
        return [{"id": k, "title": c["title"], "account": self.sources[c["source"]], "source": c["source"]}
                for k, c in self.cals.items()]

    def find(self, calendar, source, title):
        if calendar in self.cals:
            return calendar
        return next((k for k, c in self.cals.items() if c["source"] == source and c["title"] == title), None)

    def create(self, source, title):
        if source == "google":
            raise StoreError("This account doesn't allow calendars to be added.")
        self.serial += 1
        key = f"made-{self.serial}"
        self.cals[key] = {"title": title, "source": source}
        self.held[key] = []
        return key

    def remove_calendar(self, calendar):
        self.cals.pop(calendar, None)
        self.held.pop(calendar, None)

    def events(self, calendar, first, last):
        return [((calendar, i), e) for i, e in enumerate(self.held.get(calendar, [])) if e is not None]

    def remove(self, handle):
        calendar, i = handle
        self.held[calendar][i] = None

    def add(self, calendar, e):
        self.held[calendar].append(e)

    def commit(self):
        self.commits += 1
        for key in self.held:
            self.held[key] = [e for e in self.held[key] if e is not None]


class WhatGoesIn(unittest.TestCase):
    def test_an_all_day_item_keeps_its_days_and_a_timed_one_its_moments(self):
        day = entry({"title": "Wean C12", "start": "2026-11-02T00:00:00", "end": "2026-11-03T23:59:59",
                     "isAllday": True, "body": "Cage C12  \r\nRack 4"})
        self.assertEqual(day, Entry("Wean C12", True, date(2026, 11, 2), date(2026, 11, 3), "Cage C12\nRack 4"))
        timed = entry({"title": "Confocal", "start": "2026-11-02T09:30:00", "end": "2026-11-02T11:00:00"})
        self.assertEqual(timed.end - timed.start, 90 * 60)
        self.assertEqual(timed.start, int(datetime(2026, 11, 2, 9, 30).timestamp()))
        self.assertIsNone(entry({"title": "Someday", "start": "", "end": ""}))

    def test_a_to_do_says_it_is_one(self):
        with app.test_request_context(headers={"Accept-Language": "en"}):
            todo = entry({"title": "Order tips", "kind": "task", "raw": {"done": False},
                          "start": "2026-11-02T00:00:00", "end": "2026-11-02T23:59:59", "isAllday": True})
            done = entry({"title": "Order tips", "kind": "task", "raw": {"done": True},
                          "start": "2026-11-02T00:00:00", "end": "2026-11-02T23:59:59", "isAllday": True})
        self.assertEqual((todo.title, done.title), ("To-do: Order tips", "Done: Order tips"))

    def test_only_what_changed_goes_out_and_in(self):
        a, b, c = (Entry(t, True, date(2026, 11, 2), date(2026, 11, 2), "") for t in "abc")
        add, remove = plan([a, b, b, c], [("h1", a), ("h2", b), ("h3", Entry("old", True, date(2026, 11, 1),
                                                                             date(2026, 11, 1), ""))])
        self.assertEqual(add, [b, c])   # two items alike are two events
        self.assertEqual(remove, ["h3"])
        self.assertEqual(plan([a], [("h1", a), ("h2", a)]), ([], ["h2"]))

    def test_only_a_calendar_called_biomanager_may_be_chosen(self):
        self.assertTrue(mac_calendar.dedicated("BioManager"))
        self.assertTrue(mac_calendar.dedicated(" biomanager · lab"))
        self.assertFalse(mac_calendar.dedicated("Home"))
        self.assertFalse(mac_calendar.dedicated("Work (BioManager)"))


class OnTheMac(AppTestCase):
    def setUp(self):
        super().setUp()
        self.store = FakeStore()
        mac_calendar.use_store(self.store)
        self.addCleanup(mac_calendar.use_store, None)
        app.config["LOCAL_SETUP"] = True
        self.addCleanup(app.config.pop, "LOCAL_SETUP", None)
        self.addCleanup(self.forget)
        self.me = make_user(uniq("mac"))
        self.c = client_for(self.me)

    @staticmethod
    def forget():
        with SessionLocal() as s:
            mac_calendar._save(s, {})

    def call(self, client=None, **body):
        return (client or self.c).post("/calendar/mac", data=json.dumps(body), content_type="application/json")

    def new_event(self, client, title, day):
        r = client.post("/calendar/items", content_type="application/json", data=json.dumps(
            {"kind": "event", "title": title, "start": f"{day.isoformat()}T00:00:00",
             "end": f"{day.isoformat()}T23:59:59", "isAllday": True}))
        self.assertTrue(r.get_json()["ok"])
        return r.get_json()["item"]["raw"]["rowId"]

    def titles(self, calendar):
        return [e.title for e in self.store.held[calendar] if e is not None]

    def test_it_asks_the_mac_once_then_offers_the_accounts(self):
        self.store.state = "ask"
        j = self.c.get("/calendar/mac").get_json()
        self.assertEqual((j["access"], j["accounts"], j["setup"]), ("ask", [], None))
        j = self.call(action="allow").get_json()
        self.assertEqual(self.store.asked, 1)
        self.assertEqual(j["access"], "granted")
        self.assertEqual({a["title"] for a in j["accounts"]}, {"iCloud", "sam@gmail.com"})
        self.assertEqual(j["calendars"], [])   # Home isn't BioManager's to fill

    def test_a_new_calendar_holds_your_things_and_follows_changes(self):
        title, gone = uniq("Perfuse"), uniq("Cancelled")
        self.new_event(self.c, title, TODAY + timedelta(days=3))
        gone_id = self.new_event(self.c, gone, TODAY + timedelta(days=4))
        j = self.call(action="start", scope="mine", target="new:icloud").get_json()
        self.assertTrue(j["ok"], j)
        self.assertEqual((j["setup"]["title"], j["setup"]["account"], j["setup"]["error"]), ("BioManager", "iCloud", ""))
        self.assertTrue(j["setup"]["mine"] and j["setup"]["created"])
        self.assertIn(title, self.titles("made-1"))
        self.assertIn(gone, self.titles("made-1"))
        # Taken out of BioManager, it goes; what someone put there by hand goes too.
        self.c.post(f"/calendar/items/event-{gone_id}/delete")
        self.store.held["made-1"].append(Entry("Dentist", True, TODAY + timedelta(days=1), TODAY + timedelta(days=1), ""))
        self.call(action="now")
        self.assertNotIn(gone, self.titles("made-1"))
        self.assertNotIn("Dentist", self.titles("made-1"))
        self.assertEqual(self.titles("made-1").count(title), 1)   # nothing doubled
        # Nothing changed, nothing done.
        commits = self.store.commits
        self.call(action="now")
        self.assertEqual(self.store.commits, commits)

    def test_what_started_before_the_window_stays(self):
        old = Entry("Last spring's weaning", True, TODAY - timedelta(days=200), TODAY - timedelta(days=200), "")
        self.call(action="start", scope="mine", target="new:icloud")
        self.store.held["made-1"].append(old)
        self.call(action="now")
        self.assertIn(old, self.store.held["made-1"])

    def test_just_mine_or_the_whole_lab(self):
        mine, theirs = uniq("Mine"), uniq("Theirs")
        self.new_event(self.c, mine, TODAY + timedelta(days=2))
        other = make_user(uniq("other"))
        oc = client_for(other)
        oc.post("/calendar/items", content_type="application/json", data=json.dumps(
            {"kind": "event", "title": theirs, "start": f"{(TODAY + timedelta(days=2)).isoformat()}T00:00:00",
             "end": f"{(TODAY + timedelta(days=2)).isoformat()}T23:59:59", "isAllday": True}))
        self.call(action="start", scope="mine", target="new:icloud")
        self.assertIn(mine, self.titles("made-1"))
        self.assertNotIn(theirs, self.titles("made-1"))
        j = self.call(action="scope", scope="lab").get_json()
        self.assertEqual(j["setup"]["scope"], "lab")
        self.assertIn(theirs, self.titles("made-1"))

    def test_google_needs_a_calendar_made_on_the_web_first(self):
        r = self.call(action="start", scope="mine", target="new:google")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Create new calendar", r.get_json()["error"])
        self.assertIsNone(self.c.get("/calendar/mac").get_json()["setup"])
        # Made on the web, it shows on the Mac, and can be chosen.
        self.store.cals["g-bm"] = {"title": "BioManager", "source": "google"}
        self.store.held["g-bm"] = []
        j = self.c.get("/calendar/mac").get_json()
        self.assertEqual([c["id"] for c in j["calendars"]], ["g-bm"])
        j = self.call(action="start", scope="mine", target="cal:g-bm").get_json()
        self.assertTrue(j["ok"], j)
        self.assertEqual(j["setup"]["account"], "sam@gmail.com")
        self.assertFalse(j["setup"]["created"])
        # Stop empties it and leaves it, since the person made it.
        self.new_event(self.c, uniq("Seminar"), TODAY + timedelta(days=1))
        self.call(action="now")
        self.assertTrue(self.titles("g-bm"))
        self.call(action="stop")
        self.assertIn("g-bm", self.store.cals)
        self.assertEqual(self.titles("g-bm"), [])
        self.assertIsNone(self.c.get("/calendar/mac").get_json()["setup"])

    def test_someone_s_own_calendar_is_never_filled(self):
        r = self.call(action="start", scope="mine", target="cal:home")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Only a calendar called BioManager", r.get_json()["error"])
        self.assertEqual(self.store.held["home"], [])

    def test_stop_removes_the_calendar_it_made(self):
        self.call(action="start", scope="mine", target="new:icloud")
        self.assertIn("made-1", self.store.cals)
        r = self.call(action="start", scope="mine", target="new:icloud")
        self.assertEqual(r.status_code, 400)   # one at a time: Stop first
        self.call(action="stop")
        self.assertNotIn("made-1", self.store.cals)

    def test_a_calendar_gone_from_the_mac_is_said(self):
        self.call(action="start", scope="mine", target="new:icloud")
        self.store.remove_calendar("made-1")
        j = self.call(action="now").get_json()
        self.assertIn("no longer on this Mac", j["setup"]["error"])

    def test_access_taken_away_is_said(self):
        self.call(action="start", scope="mine", target="new:icloud")
        self.store.state = "denied"
        j = self.call(action="now").get_json()
        self.assertIn("Privacy & Security", j["setup"]["error"])

    def test_someone_else_at_the_mac_sees_whose_it_is(self):
        self.call(action="start", scope="mine", target="new:icloud")
        j = self.m.get("/calendar/mac").get_json()
        self.assertFalse(j["setup"]["mine"])
        self.assertTrue(j["setup"]["who"])

    def test_a_change_in_the_app_wakes_the_update(self):
        mac_calendar._nudge.clear()
        mac_calendar._started.set()
        self.addCleanup(mac_calendar._started.clear)
        self.addCleanup(mac_calendar._nudge.clear)
        self.c.get("/calendar")
        self.assertFalse(mac_calendar._nudge.is_set())
        self.new_event(self.c, uniq("Nudge"), TODAY)
        self.assertTrue(mac_calendar._nudge.is_set())

    def test_the_calendar_offers_it_in_the_mac_window_only(self):
        # The button and its dialog, not the words: What's new may name them anywhere.
        offered = ('id="cal-mac-btn"', 'id="cal-mac-modal"')
        html = self.c.get("/calendar").get_data(as_text=True)
        for piece in offered:
            self.assertIn(piece, html)
        # A device reaching a desktop that shares its lab: neither the button nor the routes.
        lan = {"biomanager.lan": "1"}
        html = self.c.get("/calendar", environ_overrides=lan).get_data(as_text=True)
        for piece in offered:
            self.assertNotIn(piece, html)
        self.assertEqual(self.c.get("/calendar/mac", environ_overrides=lan).status_code, 404)
        # A lab server.
        app.config.pop("LOCAL_SETUP")
        html = self.c.get("/calendar").get_data(as_text=True)
        for piece in offered:
            self.assertNotIn(piece, html)
        self.assertEqual(self.c.get("/calendar/mac").status_code, 404)
        self.assertEqual(self.call(action="now").status_code, 404)


class NotOnAMac(AppTestCase):
    def test_without_eventkit_nothing_is_offered_or_started(self):
        mac_calendar.use_store(None)
        if mac_calendar.available():
            self.skipTest("EventKit is here")
        self.assertIsNone(mac_calendar.start_background(app, first_delay=0))
        self.assertEqual(mac_calendar.update(app), {})


class TheAccountsAsTheMacNamesThem(AppTestCase):
    """EventKit calls the Mac's own account "Default"; the Mac's Calendar
    shows it as On My Mac. Subscribed calendars can't take one."""

    class Source:
        def __init__(self, kind, title, ident):
            self.kind, self.name, self.ident = kind, title, ident

        def sourceType(self):
            return self.kind

        def title(self):
            return self.name

        def sourceIdentifier(self):
            return self.ident

    def test_on_my_mac(self):
        st = mac_calendar.EventKitStore.__new__(mac_calendar.EventKitStore)
        st.store = mock.Mock(sources=lambda: [self.Source(0, "Default", "local"), self.Source(2, "iCloud", "icloud"),
                                              self.Source(4, "Subscribed Calendars", "subs")])
        with app.test_request_context("/"):
            self.assertEqual(st.accounts(), [{"id": "local", "title": "On My Mac"}, {"id": "icloud", "title": "iCloud"}])
        with app.test_request_context("/", headers={"Accept-Language": "zh-CN"}):
            from flask import g
            g.lang = "zh"
            self.assertEqual(st.accounts()[0]["title"], "我的 Mac")


class ThePackagedMacApp(unittest.TestCase):
    """macOS stops an app that asks for the calendars without saying why,
    and a signed app may not ask without the entitlement."""

    def test_it_says_why_it_asks(self):
        spec = (ROOT / "Biomanager.spec").read_text(encoding="utf-8")
        self.assertIn('"NSCalendarsFullAccessUsageDescription":', spec)
        self.assertIn('"NSCalendarsUsageDescription":', spec)
        self.assertIn('"EventKit"', spec)

    def test_it_may_ask(self):
        entitlements = (ROOT / "desktop" / "entitlements.plist").read_text(encoding="utf-8")
        self.assertIn("<key>com.apple.security.personal-information.calendars</key><true/>", entitlements)

    def test_eventkit_is_installed_on_a_mac_only(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(requirements, r'pyobjc-framework-EventKit[^\n]*; sys_platform == "darwin"')


if __name__ == "__main__":
    unittest.main()
