"""The calendar's own features (app/lab_calendar.py): repeats, stocks and
supplies on the calendar, protocol timelines, equipment booking, time away
with cover, and the private feed a phone subscribes to."""
from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace

# tests.base first: it points the app at a throwaway database before app is imported.
from tests.base import AppTestCase, TODAY, client_for, days_ahead, make_user, one, uniq
from app.lab_calendar import occurrences  # noqa: E402


def iso(d: date) -> str:
    return d.isoformat()


class Calendar(AppTestCase):
    def post_json(self, client, url, body=None):
        return client.post(url, data=json.dumps(body or {}), content_type="application/json")

    def items(self, client, start=None, end=None):
        start = start or TODAY - timedelta(days=10)
        end = end or TODAY + timedelta(days=60)
        r = client.get(f"/calendar/events.json?start={iso(start)}&end={iso(end)}")
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def titled(self, client, title, **kw):
        return [i for i in self.items(client, **kw)["items"] if i["title"] == title]

    def new_event(self, client, title, day, repeat=None):
        body = {"kind": "event", "title": title, "start": f"{iso(day)}T00:00:00",
                "end": f"{iso(day)}T23:59:59", "isAllday": True}
        if repeat is not None:
            body["repeat"] = repeat
        r = self.post_json(client, "/calendar/items", body)
        self.assertTrue(r.get_json()["ok"])
        return r.get_json()["item"]["raw"]["rowId"]


class RepeatTests(Calendar):
    def test_occurrences_step_by_interval_and_stop_at_until(self):
        rep = SimpleNamespace(freq="weekly", interval=2, until=date(2026, 3, 1), skip="2026-01-15")
        got = occurrences(date(2026, 1, 1), rep, date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual(got, [date(2026, 1, 1), date(2026, 1, 29), date(2026, 2, 12), date(2026, 2, 26)])

    def test_monthly_repeats_keep_to_the_end_of_short_months(self):
        rep = SimpleNamespace(freq="monthly", interval=1, until=None, skip="")
        got = occurrences(date(2026, 1, 31), rep, date(2026, 1, 1), date(2026, 4, 30))
        self.assertEqual(got, [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)])

    def test_the_first_monday_of_every_month_and_the_last_friday(self):
        rep = SimpleNamespace(freq="nthweekday", interval=1, until=None, skip="")
        got = occurrences(date(2026, 1, 5), rep, date(2026, 1, 1), date(2026, 5, 31))    # a first Monday
        self.assertEqual(got, [date(2026, 1, 5), date(2026, 2, 2), date(2026, 3, 2), date(2026, 4, 6), date(2026, 5, 4)])
        got = occurrences(date(2026, 1, 30), rep, date(2026, 1, 1), date(2026, 4, 30))   # a fifth, so the last Friday
        self.assertEqual(got, [date(2026, 1, 30), date(2026, 2, 27), date(2026, 3, 27), date(2026, 4, 24)])
        from app.lab_calendar import repeat_summary
        self.assertEqual(repeat_summary(rep, date(2026, 1, 5))["text"], "Every month on the first Monday")

    def test_change_this_one_only_moves_one_date_and_leaves_the_series(self):
        title = uniq("Group meeting")
        row = self.new_event(self.m, title, TODAY, {"freq": "weekly", "interval": 1, "until": ""})
        moved_from = TODAY + timedelta(weeks=1)
        moved_to = moved_from + timedelta(days=1)
        r = self.post_json(self.m, "/calendar/items", {
            "kind": "event", "title": title + " (moved)", "start": f"{iso(moved_to)}T14:00:00",
            "end": f"{iso(moved_to)}T15:00:00", "isAllday": False,
            "split_from": {"event_id": row, "date": iso(moved_from)}})
        self.assertTrue(r.get_json()["ok"], r.get_data(as_text=True))
        days = [i["start"][:10] for i in self.titled(self.m, title, start=TODAY, end=TODAY + timedelta(days=20))]
        self.assertNotIn(iso(moved_from), days)
        self.assertEqual(len(days), 2)
        moved = self.titled(self.m, title + " (moved)", start=TODAY, end=TODAY + timedelta(days=20))
        self.assertEqual([(i["start"][:16], i["raw"].get("repeat")) for i in moved], [(f"{iso(moved_to)}T14:00", None)])
        # Someone else can't split your series.
        r = self.post_json(self.o, "/calendar/items", {"kind": "event", "title": "x", "start": f"{iso(TODAY)}T09:00:00",
                                                        "split_from": {"event_id": row, "date": iso(TODAY)}})
        self.assertEqual(r.status_code, 403)

    def test_a_long_running_daily_repeat_still_reaches_the_window(self):
        rep = SimpleNamespace(freq="daily", interval=1, until=None, skip="")
        got = occurrences(date(2020, 1, 1), rep, date(2026, 6, 1), date(2026, 6, 3))
        self.assertEqual(got, [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)])

    def test_a_weekly_event_appears_every_week_and_one_date_can_be_skipped(self):
        title = uniq("Lab meeting")
        row = self.new_event(self.m, title, TODAY, {"freq": "weekly", "interval": 1, "until": ""})
        found = self.titled(self.m, title, start=TODAY, end=TODAY + timedelta(days=27))
        self.assertEqual([i["start"][:10] for i in found], [iso(TODAY + timedelta(weeks=k)) for k in range(4)])
        self.assertEqual(found[0]["raw"]["repeat"]["text"], "Every week")
        skip = iso(TODAY + timedelta(weeks=1))
        self.assertTrue(self.post_json(self.m, f"/calendar/events/{row}/skip", {"date": skip}).get_json()["ok"])
        found = self.titled(self.m, title, start=TODAY, end=TODAY + timedelta(days=27))
        self.assertNotIn(skip, [i["start"][:10] for i in found])
        self.assertEqual(len(found), 3)

    def test_a_repeat_that_started_before_the_window_still_shows(self):
        title = uniq("Cage change")
        self.new_event(self.m, title, TODAY - timedelta(days=70), {"freq": "weekly", "interval": 1})
        found = self.titled(self.m, title, start=TODAY, end=TODAY + timedelta(days=6))
        self.assertEqual(len(found), 1)

    def test_removing_the_repeat_leaves_one_event(self):
        title = uniq("Journal club")
        row = self.new_event(self.m, title, TODAY, {"freq": "daily", "interval": 1})
        self.post_json(self.m, f"/calendar/items/event-{row}", {"repeat": None})
        self.assertEqual(len(self.titled(self.m, title)), 1)

    def test_deleting_a_repeating_event_removes_its_repeat(self):
        row = self.new_event(self.m, uniq("Seminar"), TODAY, {"freq": "weekly"})
        self.post_json(self.m, f"/calendar/items/event-{row}@{iso(TODAY)}/delete")
        self.assertIsNone(one("select id from calendar_repeats where event_id_fk=?", row))

    def test_someone_else_cannot_skip_your_repeat(self):
        row = self.new_event(self.m, uniq("Mine"), TODAY, {"freq": "weekly"})
        r = self.post_json(self.o, f"/calendar/events/{row}/skip", {"date": iso(TODAY)})
        self.assertEqual(r.status_code, 403)


class EverythingWithADateTests(Calendar):
    def test_an_expiring_reagent_is_on_the_calendar(self):
        name = uniq("Serum")
        self.make_item(self.m, "reagents", name, expires_on=days_ahead(5))
        found = [i for i in self.items(self.m)["items"] if i["title"] == f"Expires: {name}"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["calendarId"], "supplies")
        self.assertEqual(found[0]["start"][:10], days_ahead(5))


class AutoItemLinkTests(Calendar):
    def test_every_item_the_colony_puts_on_the_calendar_opens_a_page(self):
        from tests.base import execute
        # A Breeding cage: only a mating cage has a litter to wean on the calendar.
        colony = self.make_colony(self.a, self.admin, n_mice=1, dob=(TODAY - timedelta(days=30 * 7 - 5)).isoformat(),
                                  purpose="Breeding")
        # Its litter born 20 days ago: weaning and genotyping are due (Litter born, as on the sheet).
        self.a.post(f"/colony/cages/{colony['cage_id']}/give-birth", data={"date_give_birth": iso(TODAY - timedelta(days=20))})
        execute("update litters set date_of_birth=? where id=?", TODAY - timedelta(days=30 * 7 - 5), colony["litter_id"])
        name = uniq("Exp ")
        self.a.post("/colony/experiments/create", data={"name": name, "from_cage_id": colony["cage"]})
        execute("update experiments set start_date=? where name=?", TODAY + timedelta(days=2), name)
        auto = [i for i in self.items(self.a)["items"] if i["kind"] == "auto" and i["raw"].get("href")]
        sources = {i["raw"]["source"] for i in auto}
        self.assertTrue({"cage", "mouse", "experiment"} <= sources, sources)
        broken = [(i["title"], i["raw"]["href"], self.a.get(i["raw"]["href"]).status_code) for i in auto]
        self.assertEqual([b for b in broken if b[2] != 200], [])


class ProtocolTests(Calendar):
    def template(self, client, steps=None):
        r = self.post_json(client, "/calendar/protocols/templates", {
            "name": uniq("Tamoxifen"), "steps": steps or [
                {"from": 0, "to": 4, "title": "Tamoxifen"}, {"from": 14, "to": 14, "title": "Surgery"}]})
        self.assertTrue(r.get_json()["ok"], r.get_json())
        return r.get_json()["template"]

    def test_starting_a_protocol_puts_each_step_on_its_day(self):
        tpl = self.template(self.m)
        label = uniq("Cohort")
        r = self.post_json(self.m, "/calendar/protocols/runs",
                           {"template_id": tpl["id"], "start_date": iso(TODAY), "label": label})
        run = r.get_json()["run"]
        steps = {i["title"]: i for i in self.items(self.m)["items"] if i["kind"] == "protocol" and label in i["title"]}
        self.assertEqual(steps[f"Tamoxifen · {label}"]["start"][:10], iso(TODAY))
        self.assertEqual(steps[f"Tamoxifen · {label}"]["end"][:10], iso(TODAY + timedelta(days=4)))
        self.assertEqual(steps[f"Surgery · {label}"]["start"][:10], iso(TODAY + timedelta(days=14)))
        # Moving day 0 moves every step.
        self.post_json(self.m, "/calendar/protocols/runs", {"id": run["id"], "start_date": iso(TODAY + timedelta(days=7)),
                                                             "label": label})
        steps = {i["title"]: i for i in self.items(self.m)["items"] if i["kind"] == "protocol" and label in i["title"]}
        self.assertEqual(steps[f"Surgery · {label}"]["start"][:10], iso(TODAY + timedelta(days=21)))

    def test_a_run_keeps_its_steps_when_the_template_changes(self):
        tpl = self.template(self.m)
        label = uniq("Cohort")
        self.post_json(self.m, "/calendar/protocols/runs", {"template_id": tpl["id"], "start_date": iso(TODAY), "label": label})
        self.post_json(self.m, "/calendar/protocols/templates",
                       {"id": tpl["id"], "name": tpl["name"], "steps": [{"from": 1, "to": 1, "title": "Something else"}]})
        titles = [i["title"] for i in self.items(self.m)["items"] if label in i["title"]]
        self.assertIn(f"Surgery · {label}", titles)

    def test_a_protocol_needs_a_name_and_a_step(self):
        r = self.post_json(self.m, "/calendar/protocols/templates", {"name": "", "steps": []})
        self.assertEqual(r.status_code, 400)
        r = self.post_json(self.m, "/calendar/protocols/templates", {"name": "X", "steps": [{"from": 0, "title": ""}]})
        self.assertIn("step", r.get_json()["error"])

    def test_only_the_owner_changes_a_run(self):
        tpl = self.template(self.m)
        run = self.post_json(self.m, "/calendar/protocols/runs",
                             {"template_id": tpl["id"], "start_date": iso(TODAY)}).get_json()["run"]
        r = self.post_json(self.o, "/calendar/protocols/runs", {"id": run["id"], "start_date": iso(TODAY)})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.post_json(self.o, f"/calendar/protocols/runs/{run['id']}/delete").status_code, 403)


class BookingTests(Calendar):
    def instrument(self):
        r = self.post_json(self.m, "/calendar/equipment", {"name": uniq("Confocal"), "location": "4.12"})
        return r.get_json()["equipment"]

    def book(self, client, eq, start, end, **extra):
        return self.post_json(client, "/calendar/bookings", {"equipment_id": eq["id"], "start": start, "end": end, **extra})

    def test_an_instrument_is_renamed_and_keeps_its_bookings(self):
        eq = self.instrument()
        day = days_ahead(4)
        self.assertTrue(self.book(self.m, eq, f"{day}T09:00", f"{day}T10:00").get_json()["ok"])
        name = uniq("Confocal LSM 980 ")
        r = self.post_json(self.m, "/calendar/equipment", {"id": eq["id"], "name": name, "location": "4.14"})
        self.assertEqual((r.get_json()["equipment"]["id"], r.get_json()["equipment"]["name"]), (eq["id"], name))
        titles = [i["title"] for i in self.items(self.m)["items"] if i["kind"] == "booking"]
        self.assertTrue(any(t.startswith(name) for t in titles), titles)
        # Someone else's instrument is theirs to rename.
        self.assertEqual(self.post_json(self.o, "/calendar/equipment", {"id": eq["id"], "name": "Mine"}).status_code, 403)

    def test_an_instrument_colour_is_a_colour(self):
        for bad in ('#"><b>', "#zzz", "red", "#12345"):
            r = self.post_json(self.m, "/calendar/equipment", {"name": uniq("Rig "), "color": bad})
            self.assertTrue(r.get_json()["equipment"]["color"].startswith("#"), bad)
            self.assertNotIn('"', r.get_json()["equipment"]["color"])
            self.assertNotEqual(r.get_json()["equipment"]["color"], bad)
        r = self.post_json(self.m, "/calendar/equipment", {"name": uniq("Rig "), "color": "#3bc"})
        self.assertEqual(r.get_json()["equipment"]["color"], "#3bc")

    def test_overlapping_bookings_are_refused_with_who_has_it(self):
        eq = self.instrument()
        day = days_ahead(3)
        self.assertTrue(self.book(self.m, eq, f"{day}T09:00", f"{day}T11:00").get_json()["ok"])
        r = self.book(self.o, eq, f"{day}T10:30", f"{day}T12:00")
        self.assertEqual(r.status_code, 409)
        self.assertIn("already booked", r.get_json()["error"])
        # Back to back is fine.
        self.assertTrue(self.book(self.o, eq, f"{day}T11:00", f"{day}T12:00").get_json()["ok"])
        titles = [i["title"] for i in self.items(self.m)["items"] if i["kind"] == "booking" and i["title"].startswith(eq["name"])]
        self.assertEqual(len(titles), 2)

    def test_a_booking_must_end_after_it_starts(self):
        eq = self.instrument()
        day = days_ahead(4)
        self.assertEqual(self.book(self.m, eq, f"{day}T11:00", f"{day}T10:00").status_code, 400)

    def test_someone_elses_booking_is_read_only_to_you(self):
        eq = self.instrument()
        day = days_ahead(5)
        self.book(self.m, eq, f"{day}T09:00", f"{day}T10:00")
        mine = [i for i in self.items(self.o)["items"] if i["kind"] == "booking" and i["title"].startswith(eq["name"])][0]
        self.assertTrue(mine["isReadOnly"])
        bid = mine["raw"]["bookingId"]
        self.assertEqual(self.post_json(self.o, f"/calendar/bookings/{bid}/delete").status_code, 403)
        self.assertTrue(self.post_json(self.m, f"/calendar/bookings/{bid}/delete").get_json()["ok"])

    def test_a_repeating_booking_makes_one_booking_per_day_each_its_own(self):
        eq = self.instrument()
        first = TODAY + timedelta(days=(7 - TODAY.weekday()) % 7 or 7)  # next Monday
        r = self.book(self.m, eq, f"{iso(first)}T09:00", f"{iso(first)}T10:00", purpose="His prep",
                      repeat={"freq": "weekdays", "until": iso(first + timedelta(days=13))})
        self.assertEqual(r.get_json()["count"], 10)
        count = lambda: one("select count(*) from equipment_bookings where equipment_id_fk=?", eq["id"])  # noqa: E731
        self.assertEqual(count(), 10)
        self.assertEqual(one("select count(*) from equipment_bookings where equipment_id_fk=? and purpose='His prep'",
                             eq["id"]), 10)
        weekly = self.book(self.m, eq, f"{iso(first)}T14:00", f"{iso(first)}T15:00",
                           repeat={"freq": "weekly", "until": iso(first + timedelta(days=21))})
        self.assertEqual(weekly.get_json()["count"], 4)

    def test_a_repeat_that_clashes_once_books_nothing_and_says_when(self):
        eq = self.instrument()
        day = TODAY + timedelta(days=20)
        self.book(self.o, eq, f"{iso(day + timedelta(days=2))}T09:30", f"{iso(day + timedelta(days=2))}T10:30")
        before = one("select count(*) from equipment_bookings where equipment_id_fk=?", eq["id"])
        r = self.book(self.m, eq, f"{iso(day)}T09:00", f"{iso(day)}T10:00",
                      repeat={"freq": "daily", "until": iso(day + timedelta(days=4))})
        self.assertEqual(r.status_code, 409)
        self.assertIn("Nothing was booked", r.get_json()["error"])
        self.assertEqual(one("select count(*) from equipment_bookings where equipment_id_fk=?", eq["id"]), before)

    def test_a_repeat_needs_a_last_day_and_has_a_limit(self):
        eq = self.instrument()
        day = days_ahead(6)
        self.assertIn("last day", self.book(self.m, eq, f"{day}T09:00", f"{day}T10:00",
                                            repeat={"freq": "daily"}).get_json()["error"])
        r = self.book(self.m, eq, f"{day}T09:00", f"{day}T10:00", repeat={"freq": "daily", "until": days_ahead(200)})
        self.assertIn("more than", r.get_json()["error"])
        r = self.book(self.m, eq, f"{day}T09:00", f"{days_ahead(8)}T10:00", repeat={"freq": "daily", "until": days_ahead(12)})
        self.assertIn("run into the next", r.get_json()["error"])


class AwayTests(Calendar):
    def test_work_due_while_away_is_listed_and_the_cover_is_told(self):
        task = uniq("Change water")
        self.post_json(self.m, "/calendar/items", {"kind": "task", "title": task,
                                                   "start": f"{days_ahead(3)}T00:00:00", "isAllday": True})
        self.post_json(self.m, "/calendar/away", {"start": days_ahead(2), "end": days_ahead(4), "kind": "conference"})
        report = [a for a in self.items(self.m)["cover"] if a["owner"] == self.member and a["start"] == days_ahead(2)]
        self.assertEqual(len(report), 1)
        self.assertIn(task, [j["title"] for j in report[0]["jobs"]])
        self.assertEqual(report[0]["cover"], "")
        before = one("select count(*) from notifications where recipient_username=?", self.other)
        r = self.post_json(self.m, f"/calendar/away/{report[0]['id']}/cover", {"cover": self.other})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(one("select count(*) from notifications where recipient_username=?", self.other), before + 1)
        # Only the person away (or an admin) chooses cover.
        r = self.post_json(self.o, f"/calendar/away/{report[0]['id']}/cover", {"cover": ""})
        self.assertEqual(r.status_code, 403)

    def test_last_day_cannot_be_before_the_first(self):
        r = self.post_json(self.m, "/calendar/away", {"start": days_ahead(5), "end": days_ahead(2)})
        self.assertEqual(r.status_code, 400)

    def test_a_member_cannot_mark_someone_else_away(self):
        self.post_json(self.m, "/calendar/away", {"start": days_ahead(40), "owner": self.other})
        self.assertEqual(one("select owner from absences where start_date=? order by id desc", days_ahead(40)), self.member)


class FeedTests(Calendar):
    def test_the_feed_needs_no_sign_in_and_a_new_link_retires_the_old(self):
        user = make_user(uniq("feed"))
        client = client_for(user)
        title = uniq("Photometry")
        self.new_event(client, title, TODAY + timedelta(days=2))
        url = self.post_json(client, "/calendar/phone-feed", {"action": "create", "scope": "mine"}).get_json()["url"]
        path = "/" + url.split("/", 3)[3]
        anon = self.app_client()
        r = anon.get(path)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "text/calendar")
        body = r.get_data(as_text=True)
        self.assertIn("BEGIN:VCALENDAR", body)
        self.assertIn(f"SUMMARY:{title}", body)
        new = self.post_json(client, "/calendar/phone-feed", {"action": "reset"}).get_json()["url"]
        self.assertNotEqual(new, url)
        self.assertEqual(anon.get(path).status_code, 404)
        self.post_json(client, "/calendar/phone-feed", {"action": "stop"})
        self.assertEqual(anon.get("/" + new.split("/", 3)[3]).status_code, 404)

    def test_a_personal_feed_leaves_out_other_peoples_events(self):
        mine, theirs = uniq("Mine"), uniq("Theirs")
        self.new_event(self.m, mine, TODAY + timedelta(days=1))
        self.new_event(self.o, theirs, TODAY + timedelta(days=1))
        url = self.post_json(self.m, "/calendar/phone-feed", {"action": "create", "scope": "mine"}).get_json()["url"]
        body = self.app_client().get("/" + url.split("/", 3)[3]).get_data(as_text=True)
        self.assertIn(mine, body)
        self.assertNotIn(theirs, body)
        self.post_json(self.m, "/calendar/phone-feed", {"action": "create", "scope": "lab"})
        body = self.app_client().get("/" + url.split("/", 3)[3]).get_data(as_text=True)
        self.assertIn(theirs, body)

    def test_the_feed_passes_the_internet_gate_but_nothing_else_does(self):
        url = self.post_json(self.m, "/calendar/phone-feed", {"action": "create"}).get_json()["url"]
        internet = {"X-BioManager-Entry": "internet"}
        r = self.app_client().get("/" + url.split("/", 3)[3], headers=internet)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertEqual(r.headers["Referrer-Policy"], "no-referrer")
        self.assertIn("private", r.headers["Cache-Control"])
        # Settings for the link, and the calendar itself, still need a session.
        for path in ("/calendar/phone-feed", "/calendar", "/calendar/events.json"):
            r = self.app_client().get(path, headers=internet)
            self.assertIn(r.status_code, (302, 401), path)
            self.assertNotIn(b"BEGIN:VCALENDAR", r.data)

    def test_an_unknown_token_is_not_found(self):
        self.assertEqual(self.app_client().get("/calendar/feed/nonsense.ics").status_code, 404)

    @staticmethod
    def app_client():
        from app.app import app
        return app.test_client()


class PageTests(Calendar):
    def test_the_calendar_sends_nothing_to_other_sites(self):
        """TOAST UI reports usage to Google Analytics unless told not to. It is
        told not to, and the policy only lets the page reach this server, so
        a future bundle that phones home is blocked (and logged) anyway."""
        from pathlib import Path
        page_js = (Path(__file__).resolve().parent.parent / "app" / "static" / "calendar-page.js").read_text()
        self.assertIn("usageStatistics: false", page_js)
        policy = self.m.get("/calendar").headers["Content-Security-Policy"]
        for directive in ("default-src 'self'", "img-src 'self' data: blob:", "connect-src 'self'"):
            self.assertIn(directive, policy)
        self.assertNotIn("google", policy)

    def test_the_calendar_page_renders_with_its_sidebar(self):
        html = self.get_ok(self.m, "/calendar")
        for text in ("New event", "Stocks &amp; organisms", "Away soon", "On your phone", "calendar-page.js"):
            self.assertIn(text, html)
