"""Anonymous daily counts for BioManager's makers (app/telemetry.py): no
names in them, nothing sent unless everything allows it, once a day, and
an admin's switch on the Usage report."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from unittest import mock

# tests.base first: it points the app at a throwaway database before app is imported.
from tests.base import AppTestCase, app, execute, make_user, one, uniq

from app import telemetry
from app.db import SessionLocal

KEY = "phc_testkey"


def _answer(status: int = 200):
    """What urlopen hands back, as a context manager."""
    response = mock.MagicMock()
    response.__enter__.return_value.status = status
    return response


class Heartbeat(AppTestCase):
    def setUp(self):
        # A key, and none of the off switches a developer's shell might have set.
        env = mock.patch.dict(os.environ, {"BIOMANAGER_TELEMETRY_KEY": KEY})
        env.start()
        self.addCleanup(env.stop)
        for name in ("BIOMANAGER_TELEMETRY", "DO_NOT_TRACK", "CI"):   # GitHub Actions sets CI
            os.environ.pop(name, None)
        set_up = mock.patch("app.lab.setup_done", return_value=True)
        set_up.start()
        self.addCleanup(set_up.stop)
        # A checkout has no VERSION file, so it looks like a build being
        # worked on, which never sends (see `released`). The tests are about
        # what a released build does, so they run as one.
        shipped = mock.patch("app.feedback.app_version", return_value="1.1.0")
        shipped.start()
        self.addCleanup(shipped.stop)
        self.post_mock = mock.patch("app.telemetry.urlopen", return_value=_answer())
        self.urlopen = self.post_mock.start()
        self.addCleanup(self.post_mock.stop)
        self.addCleanup(self._forget)
        self._forget()

    @staticmethod
    def _forget():
        execute("delete from app_settings where key like 'telemetry:%'")

    def send(self, now=None) -> bool:
        with app.app_context(), SessionLocal() as s:
            return telemetry.send_if_due(s, now)

    def sent_body(self) -> dict:
        request = self.urlopen.call_args[0][0]
        return json.loads(request.data)

    # ---------------------------------------------------------------- what is sent

    def test_counts_without_names(self):
        person = uniq("zanzibarperson")
        make_user(person)
        execute("update users set display_name=?, email=? where username=?",
                "Quetzalcoatl Smith", f"{person}@example.edu", person)
        organisms = uniq("Axolotlhouse ")
        flies = uniq("Secretflyroom ")
        self.make_organism_module(self.a, organisms)
        self.make_stock_module(self.a, "fly", flies)
        from app import inventory_service
        shelf = uniq("Freezershelfnine ")
        with SessionLocal() as s:
            inventory_service.create_module(s, "custom", shelf, self.admin)
            s.commit()
        self.make_mouse(self.a, self.admin, cage=uniq("C"))    # a change in the history, by name

        self.assertTrue(self.send())
        request = self.urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://us.i.posthog.com/i/v0/e/")
        self.assertEqual(self.urlopen.call_args[1]["timeout"], 5)
        body = self.sent_body()
        text = json.dumps(body)
        for secret in (person, "Quetzalcoatl", "@example.edu", organisms.strip(), flies.strip(), shelf.strip(),
                       self.admin, self.member, "localhost", os.uname().nodename.split(".")[0]):
            self.assertNotIn(secret, text)
        self.assertEqual(body["api_key"], KEY)
        self.assertEqual(body["event"], "heartbeat")
        self.assertEqual(body["distinct_id"], one("select value from app_settings where key='telemetry:install_id'"))
        props = body["properties"]
        self.assertEqual(set(props), {"version", "kind", "os", "database", "members", "active_7_days",
                                      "functions_on", "databases_on", "$geoip_disable", "$process_person_profile"})
        self.assertIs(props["$geoip_disable"], True)
        self.assertIs(props["$process_person_profile"], False)
        self.assertEqual(props["kind"], "server")
        self.assertIn(props["members"], {"16-50", "51+", "6-15", "2-5"})
        self.assertNotEqual(props["active_7_days"], "0")
        self.assertGreaterEqual(props["databases_on"]["organisms"], 1)
        self.assertGreaterEqual(props["databases_on"]["stocks"]["fly"], 1)
        self.assertGreaterEqual(props["databases_on"]["inventories"]["custom"], 1)
        self.assertTrue(set(props["functions_on"]) <= {"colony", "zebrafish", "plasmids", "calendar", "notebook"})

    def test_ranges(self):
        self.assertEqual([telemetry.bucket(n) for n in (0, 1, 2, 5, 6, 15, 16, 50, 51, 400)],
                         ["0", "1", "2-5", "2-5", "6-15", "6-15", "16-50", "16-50", "51+", "51+"])

    def test_the_id_is_made_once(self):
        self.send()
        first = self.sent_body()["distinct_id"]
        self.send(datetime.utcnow() + timedelta(days=2))
        self.assertEqual(self.sent_body()["distinct_id"], first)

    # ---------------------------------------------------------------- when nothing is sent

    def test_nothing_without_a_key(self):
        os.environ["BIOMANAGER_TELEMETRY_KEY"] = ""
        with mock.patch.object(telemetry, "PROJECT_KEY", ""):
            self.assertFalse(self.send())
        self.urlopen.assert_not_called()

    def test_nothing_when_the_server_turned_it_off(self):
        for name, value in (("BIOMANAGER_TELEMETRY", "0"), ("DO_NOT_TRACK", "1"), ("CI", "true")):
            with mock.patch.dict(os.environ, {name: value}):
                self.assertFalse(self.send())
        self.urlopen.assert_not_called()

    def test_nothing_when_an_admin_switched_it_off(self):
        with SessionLocal() as s:
            telemetry.set_lab_on(s, False)
            s.commit()
        self.assertFalse(self.send())
        self.urlopen.assert_not_called()

    def test_nothing_before_the_lab_is_set_up(self):
        with mock.patch("app.lab.setup_done", return_value=False):
            self.assertFalse(self.send())
        self.urlopen.assert_not_called()

    def test_once_a_day(self):
        now = datetime.utcnow()
        self.assertTrue(self.send(now))
        self.assertFalse(self.send(now + timedelta(hours=23)))
        self.assertEqual(self.urlopen.call_count, 1)
        self.assertTrue(self.send(now + timedelta(hours=24, minutes=1)))
        self.assertEqual(self.urlopen.call_count, 2)

    def test_a_failed_send_is_tried_again(self):
        now = datetime.utcnow()
        self.urlopen.side_effect = OSError("offline")
        self.assertFalse(self.send(now))                     # raises nothing
        self.assertEqual(one("select value from app_settings where key='telemetry:last_sent'"), "")
        self.urlopen.side_effect = None
        self.assertTrue(self.send(now + timedelta(hours=1)))

    def test_the_request_hook(self):
        started = []
        with mock.patch.object(telemetry, "_start", side_effect=lambda work: started.append(work)):
            telemetry._next_check = 0.0
            self.a.get("/")                                   # under TESTING: never
            self.assertEqual(started, [])
            with mock.patch.dict(app.config, {"TESTING": False}):
                telemetry._next_check = 0.0
                self.a.get("/")
                self.a.get("/")                               # once an hour per process
                self.assertEqual(len(started), 1)
                started[0]()                                  # the background work, run here
                self.assertEqual(self.urlopen.call_count, 1)
                telemetry._next_check = 0.0
                with mock.patch.dict(os.environ, {"DO_NOT_TRACK": "1"}):
                    self.a.get("/")
                self.assertEqual(len(started), 1)
        telemetry._next_check = 0.0

    # ---------------------------------------------------------------- what admins see

    def test_a_build_being_worked_on_is_not_a_lab(self):
        """Every dev run and every upgrade check makes a fresh database, so
        a development build that sent would count as a new lab each time."""
        for version, sends in (("1.1.0", True), ("1.1.0+dev", False), ("server", False), ("", False)):
            with self.subTest(version=version):
                with mock.patch("app.feedback.app_version", return_value=version):
                    self.assertEqual(telemetry.released(), sends)

    def test_the_switch_on_the_usage_report(self):
        html = self.get_ok(self.a, "/feedback/usage")
        self.assertIn("Anonymous counts for BioManager's makers", html)
        self.assertIn("Not sent yet", html)
        self.assertIn("&#34;event&#34;: &#34;heartbeat&#34;", html)
        self.assertNotIn(self.admin, html.split("data-heartbeat-json")[1])
        self.assertEqual(self.m.post("/feedback/usage/heartbeat", data={"enabled": "0"}).status_code, 403)
        r = self.post(self.a, "/feedback/usage/heartbeat", data={"enabled": "0"})
        self.assertFlash(r, "switched off")
        self.assertEqual(one("select value from app_settings where key='telemetry:enabled'"), "off")
        self.assertIn("Off: nothing is sent", r.get_data(as_text=True))
        self.assertFalse(self.send())
        self.post(self.a, "/feedback/usage/heartbeat", data={"enabled": "1"})
        self.assertTrue(self.send())
        self.assertIn("Last sent", self.get_ok(self.a, "/feedback/usage"))
        with mock.patch.dict(os.environ, {"BIOMANAGER_TELEMETRY": "0"}):
            html = self.get_ok(self.a, "/feedback/usage")
        self.assertIn("Off (set by the server)", html)
        self.assertNotIn('action="/feedback/usage/heartbeat"', html)
        with mock.patch.dict(os.environ, {"BIOMANAGER_TELEMETRY_KEY": ""}), \
                mock.patch.object(telemetry, "PROJECT_KEY", ""):
            self.assertIn("Not set up in this build", self.get_ok(self.a, "/feedback/usage"))

    def test_asked_in_the_first_survey(self):
        with mock.patch("app.lab.setup_done", return_value=False):
            html = self.get_ok(self.a, "/setup")
            self.assertIn("Send BioManager's makers anonymous counts once a day", html)
            self.assertIn('name="heartbeat" value="1" checked', html)
            with mock.patch("app.lab.apply_survey", return_value=[]):
                self.a.post("/setup", data={})               # unticked
        self.assertEqual(one("select value from app_settings where key='telemetry:enabled'"), "off")
        self.assertFalse(self.send())
