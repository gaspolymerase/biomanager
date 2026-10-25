"""Send feedback and the usage report (app/feedback.py): kept in the lab,
sent on only by the person, and counts without names."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

# tests.base first: it points the app at a throwaway database before app is imported.
from tests.base import AppTestCase, count, location, one, uniq


class Feedback(AppTestCase):
    def send(self, client, text, **extra):
        return client.post("/feedback", data={"text": text, "kind": "idea", **extra})

    def test_kept_for_the_lab_and_offered_as_an_issue(self):
        text = uniq("It would help to print tube labels from the grid ")
        r = self.send(self.m, text, **{"from": "http://localhost/inventory/samples?view=grid"})
        self.assertEqual(r.status_code, 302)
        row = one("select id from feedback where text=?", text)
        self.assertEqual(one("select page from feedback where id=?", row), "/inventory/samples?view=grid")
        self.assertTrue(one("select app_version from feedback where id=?", row))
        page = self.get_ok(self.m, location(r))
        self.assertIn("Open as a GitHub issue", page)
        link = next(part.split('"')[0] for part in page.split('href="')
                    if part.startswith("https://github.com/gaspolymerase/biomanager/issues/new"))
        query = parse_qs(urlparse(link.replace("&amp;", "&")).query)
        self.assertTrue(query["title"][0].startswith("An idea: It would help"))
        self.assertIn("/inventory/samples?view=grid", query["body"][0])
        self.assertNotIn(self.member, query["body"][0])               # not who sent it
        self.assertNotIn("localhost", query["body"][0])               # nor the server's address

    def test_admins_read_it_and_mark_it_done(self):
        text = uniq("Sac button unclear ")
        self.send(self.m, text)
        other = uniq("Admin's own note ")
        self.send(self.a, other)
        self.assertIn(text, self.get_ok(self.a, "/feedback"))
        self.assertNotIn(other, self.get_ok(self.m, "/feedback"))    # a member sees only their own
        self.assertGreaterEqual(count("notifications", "recipient_username=? and title like ?",
                                      self.admin, "%sent feedback"), 1)
        row = one("select id from feedback where text=?", text)
        self.assertEqual(self.m.post(f"/feedback/{row}/status", data={"status": "done"}).status_code, 403)
        self.a.post(f"/feedback/{row}/status", data={"status": "done"})
        self.assertEqual(one("select status from feedback where id=?", row), "done")

    def test_a_link_from_elsewhere_is_not_kept(self):
        text = uniq("From elsewhere ")
        self.send(self.m, text, **{"from": "https://example.org/secret"})
        self.assertEqual(one("select page from feedback where text=?", text), "")


class ToTheMakers(AppTestCase):
    """Also send this to BioManager's makers: the note, and only the note,
    goes to them when its sender leaves the box ticked."""

    def setUp(self):
        super().setUp()
        import os
        from unittest import mock
        patches = [mock.patch.dict(os.environ, {"BIOMANAGER_TELEMETRY_KEY": "phc_test"}),
                   mock.patch("app.feedback.app_version", return_value="9.9.9"),   # a released build
                   mock.patch("app.telemetry.urlopen", return_value=self._answer())]
        for name in ("BIOMANAGER_TELEMETRY", "DO_NOT_TRACK", "CI"):
            patches.append(mock.patch.dict(os.environ, {name: ""}))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        from app import telemetry
        self.sent = mock.patch.object(telemetry, "_post", wraps=telemetry._post)
        self.posted = self.sent.start()
        self.addCleanup(self.sent.stop)

    @staticmethod
    def _answer(status: int = 200):
        from unittest import mock
        response = mock.MagicMock()
        response.__enter__.return_value.status = status
        return response

    def bodies(self):
        return [call.args[0] for call in self.posted.call_args_list if call.args[0].get("event") == "feedback"]

    def test_on_a_server_the_box_is_offered_unticked(self):
        page = self.get_ok(self.m, "/feedback")
        self.assertIn('name="tell_makers"', page)
        self.assertNotIn('name="tell_makers" value="1" class="mt-0.5" checked', page)
        self.send(self.m, uniq("Kept in the lab only "))
        self.assertEqual(self.bodies(), [])

    def test_ticked_it_sends_the_note_and_nothing_about_who(self):
        text = uniq("Weaning dates are off by a day ")
        r = self.send(self.m, text, tell_makers="1", contact="someone@example.org",
                      **{"from": "http://localhost/colony?view=cages"})
        [body] = self.bodies()
        props = body["properties"]
        self.assertEqual((props["kind"], props["text"], props["page"]), ("idea", text, "/colony?view=cages"))
        self.assertEqual(props["contact"], "someone@example.org")
        self.assertEqual(props["version"], "9.9.9")
        self.assertTrue(props["$geoip_disable"])
        flat = repr(body)
        self.assertNotIn(self.member, flat)                       # not who sent it
        self.assertNotIn("localhost", flat)                       # nor the server's address
        page = self.get_ok(self.m, location(r))
        self.assertIn("have it too", page)
        row = one("select id from feedback where text=?", text)
        from app import feedback
        from app.db import SessionLocal
        with SessionLocal() as s:
            self.assertIn(row, feedback.sent_ids(s))
        self.assertIn("Sent to BioManager", self.get_ok(self.a, "/feedback"))   # the lab's admins see it went

    def test_no_internet_keeps_it_and_says_so(self):
        from unittest import mock
        text = uniq("Offline note ")
        with mock.patch("app.telemetry.urlopen", side_effect=OSError("no route")):
            r = self.send(self.m, text, tell_makers="1")
        self.assertIn("told=failed", location(r))
        self.assertIn("couldn", self.get_ok(self.m, location(r)))
        row = one("select id from feedback where text=?", text)
        from app import feedback
        from app.db import SessionLocal
        with SessionLocal() as s:
            self.assertNotIn(row, feedback.sent_ids(s))

    def test_a_server_that_switched_counts_off_never_offers_it(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"DO_NOT_TRACK": "1"}):
            self.assertNotIn('name="tell_makers"', self.get_ok(self.m, "/feedback"))
            self.send(self.m, uniq("Not sent "), tell_makers="1")
        self.assertEqual(self.bodies(), [])

    def test_ticked_on_a_desktop(self):
        from unittest import mock
        from app import feedback
        from app.app import app
        with app.test_request_context(), mock.patch.dict(app.config, {"LOCAL_SETUP": True}):
            self.assertTrue(feedback.tell_makers_by_default())
        with app.test_request_context(), mock.patch.dict(app.config, {"LOCAL_SETUP": False}):
            self.assertFalse(feedback.tell_makers_by_default())

    def send(self, client, text, **extra):
        return client.post("/feedback", data={"text": text, "kind": "idea", **extra})


class Usage(AppTestCase):
    def test_counts_without_names(self):
        self.make_mouse(self.a, self.admin, cage=uniq("C"))
        self.assertEqual(self.m.get("/feedback/usage").status_code, 403)
        html = self.get_ok(self.a, "/feedback/usage")
        self.assertIn("Mouse colony", html)
        from app import feedback
        from app.db import SessionLocal
        with SessionLocal() as s:
            report = feedback.usage(s)
        this_week = report["weeks"][-1]
        self.assertGreaterEqual(this_week["areas"]["Mouse colony"], 1)
        self.assertGreaterEqual(this_week["people"], 1)
        text = feedback.usage_text(report)
        self.assertNotIn(self.admin, text)
        self.assertIn("Mice alive", text)
