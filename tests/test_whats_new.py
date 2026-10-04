"""What's new (app/whats_new.py): after an update, each person sees a short
note about the version once; Help → What's new opens it again."""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

from tests.base import *  # noqa: F401,F403
from tests.base import AppTestCase, client_for, execute, make_user, one, uniq

from app import whats_new

RUNNING = "app.feedback.app_version"


def welcomed(username: str, seen: str = "") -> None:
    execute("update users set welcomed_at=CURRENT_TIMESTAMP, whats_new_seen=? where username=?", seen, username)


class WhichNotes(unittest.TestCase):
    def test_a_version_is_read_from_any_way_it_is_written(self):
        self.assertEqual([whats_new.parse(v) for v in ("1.0.3", "v1.0.3", "1.0.3+dev", "1.0.3-rc.1")],
                         [(1, 0, 3)] * 4)
        self.assertIsNone(whats_new.parse("server"))

    def test_someone_who_never_saw_one_gets_only_the_running_versions(self):
        with mock.patch.dict(whats_new.NOTES, {"1.0.2": {"new": ["older"]}}):
            self.assertEqual([n["version"] for n in whats_new.due("", (1, 0, 3))], ["1.0.3"])

    def test_a_skipped_version_is_shown_too_newest_first(self):
        with mock.patch.dict(whats_new.NOTES, {"1.0.2": {"new": ["older"]}}):
            self.assertEqual([n["version"] for n in whats_new.due("1.0.1", (1, 0, 3))], ["1.0.3", "1.0.2"])

    def test_nothing_once_seen_and_nothing_from_the_future(self):
        self.assertEqual(whats_new.due("1.0.3", (1, 0, 3)), [])
        with mock.patch.dict(whats_new.NOTES, {"9.0.0": {"new": ["later"]}}):
            self.assertNotIn("9.0.0", [n["version"] for n in whats_new.due("1.0.2", (1, 0, 3))])
        with mock.patch.object(whats_new, "running", return_value=None):  # a build that names no release
            self.assertEqual(whats_new.due("", None), [])

    def test_bold_is_bold_and_the_rest_is_escaped(self):
        self.assertEqual(str(whats_new.rich("**Got it** <script>")), "<b>Got it</b> &lt;script&gt;")


class TheNotesAreWritten(unittest.TestCase):
    def test_the_newest_release_has_a_note(self):
        notes = Path(__file__).resolve().parent.parent / "docs" / "release-notes"
        versions = [p.stem for p in notes.glob("*.md") if whats_new.parse(p.stem)]
        newest = max(versions, key=whats_new.parse)
        self.assertIn(newest, whats_new.NOTES, f"Write app/whats_new.py NOTES[{newest!r}] for the release")
        for version, note in whats_new.NOTES.items():
            self.assertTrue(any(note.get(key) for key in ("new", "changed", "fixed")), version)


class ThePopup(AppTestCase):
    def setUp(self):
        super().setUp()
        self.who = make_user(uniq("reader"))
        self.c = client_for(self.who)
        patcher = mock.patch(RUNNING, return_value="1.0.3")
        patcher.start()
        self.addCleanup(patcher.stop)

    def dialog(self, html):
        return re.search(r'<dialog id="whats-new"[^>]*>', html)

    def test_after_an_update_it_opens_once_and_got_it_keeps_it_closed(self):
        welcomed(self.who, "1.0.2")
        html = self.get_ok(self.c, "/home")
        self.assertIn('data-open-now="1"', self.dialog(html).group(0))
        self.assertIn("What's new in BioManager 1.0.3", html)
        self.assertIn("Project groups", html)
        self.assertIn("releases/tag/v1.0.3", html)
        self.assertEqual(self.c.post("/whats-new/seen").status_code, 200)
        self.assertEqual(one("select whats_new_seen from users where username=?", self.who), "1.0.3")
        self.assertNotIn("data-open-now", self.dialog(self.get_ok(self.c, "/home")).group(0))

    def test_help_opens_it_again(self):
        welcomed(self.who, "1.0.3")
        html = self.get_ok(self.c, "/home")
        self.assertIn("data-whats-new-open", html)
        self.assertIn("What's new in BioManager 1.0.3", html)       # the dialog is there, closed

    def test_a_new_account_gets_the_welcome_tour_not_this(self):
        fresh = make_user(uniq("fresh"))
        client = client_for(fresh)
        client.post("/welcome")
        self.assertEqual(one("select whats_new_seen from users where username=?", fresh), "1.0.3")
        self.assertNotIn("data-open-now", self.dialog(self.get_ok(client, "/home")).group(0))

    def test_a_build_that_names_no_release_never_opens_it(self):
        welcomed(self.who, "")
        with mock.patch(RUNNING, return_value="server"):
            self.assertNotIn("data-open-now", self.dialog(self.get_ok(self.c, "/home")).group(0))

    def test_signed_out_there_is_none(self):
        from app.app import app
        self.assertNotIn('id="whats-new"', app.test_client().get("/login").get_data(as_text=True))


class TheWebsiteNamesTheNewestVersion(unittest.TestCase):
    """The website's download page and opening say which version the buttons
    fetch. It's written in the page (site.js brings it up to date from GitHub
    where it can), so a release that adds its notes here must change it too."""

    def test_the_download_pages_carry_the_newest_notes_version(self):
        newest = max(whats_new.NOTES, key=whats_new.parse)
        site = Path(__file__).resolve().parent.parent / "site"
        for page in ("download.html", "zh/download.html", "index.html", "zh/index.html"):
            text = (site / page).read_text(encoding="utf-8")
            self.assertEqual(re.findall(r"<b data-version>([^<]*)</b>", text), [newest], page)
        for page in ("download.html", "zh/download.html"):
            self.assertIn(f"/releases/tag/v{newest}", (site / page).read_text(encoding="utf-8"), page)


if __name__ == "__main__":
    unittest.main()
