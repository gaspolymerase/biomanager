"""Start a new lab, on the desktop app's sign-in page (app/paths.py, the
start_new_lab page): the lab is set aside whole at the next start, never
deleted, and only the person at the computer may ask for it."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

# tests.base first: it points the app at a throwaway database before app is imported.
from tests.base import AppTestCase
from app import paths  # noqa: E402
from app.app import app  # noqa: E402


class SetAside(AppTestCase):
    def setUp(self):
        super().setUp()
        root = Path(tempfile.mkdtemp())
        self.data, self.uploads = root / "data", root / "uploads"
        patcher = mock.patch.dict(os.environ, {"BIOMANAGER_DATA_DIR": str(self.data),
                                               "BIOMANAGER_UPLOADS_DIR": str(self.uploads)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def lab(self):
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data / "biomanager.db").write_bytes(b"the lab")
        (self.data / "secret_key").write_text("key", encoding="utf-8")
        (self.data / "backups").mkdir()
        (self.data / "desktop-prefs.json").write_text("{}", encoding="utf-8")
        (self.data / "window").mkdir()
        self.uploads.mkdir(parents=True, exist_ok=True)
        (self.uploads / "gel.png").write_bytes(b"png")

    def test_nothing_moves_unless_asked(self):
        self.lab()
        self.assertIsNone(paths.set_lab_aside())
        self.assertTrue((self.data / "biomanager.db").exists())

    def test_the_whole_lab_moves_aside_and_the_computers_own_files_stay(self):
        self.lab()
        paths.ask_for_new_lab()
        self.assertTrue(paths.new_lab_asked_for())
        kept = paths.set_lab_aside()
        self.assertEqual(kept.parent, self.data / "old-labs")
        self.assertEqual((kept / "biomanager.db").read_bytes(), b"the lab")
        self.assertTrue((kept / "secret_key").exists())
        self.assertTrue((kept / "backups").is_dir())
        self.assertEqual((kept / "uploads" / "gel.png").read_bytes(), b"png")
        self.assertEqual(sorted(p.name for p in self.data.iterdir()), ["desktop-prefs.json", "old-labs", "window"])
        self.assertFalse(paths.new_lab_asked_for())
        self.assertIsNone(paths.set_lab_aside())        # once only

    def test_changing_your_mind_keeps_the_lab(self):
        self.lab()
        paths.ask_for_new_lab()
        paths.ask_for_new_lab(False)
        self.assertIsNone(paths.set_lab_aside())
        self.assertTrue((self.data / "biomanager.db").exists())

    # --- the page

    def desktop(self, **extra):
        return mock.patch.dict(app.config, {"LOCAL_SETUP": True, **extra})

    def test_a_lab_server_has_no_such_page(self):
        anyone = app.test_client()
        self.assertEqual(anyone.get("/start-new-lab").status_code, 404)
        self.assertEqual(anyone.post("/start-new-lab").status_code, 404)
        self.assertNotIn("/start-new-lab", anyone.get("/login").get_data(as_text=True))
        self.assertFalse(paths.new_lab_asked_for())

    def test_nor_does_a_shared_desktop_seen_from_the_network(self):
        anyone = app.test_client()
        with self.desktop():
            lan = {"biomanager.lan": "1"}
            self.assertEqual(anyone.get("/start-new-lab", environ_overrides=lan).status_code, 404)
            self.assertEqual(anyone.post("/start-new-lab", environ_overrides=lan).status_code, 404)
        self.assertFalse(paths.new_lab_asked_for())

    def test_the_person_at_the_desktop_can_ask_and_change_their_mind(self):
        anyone = app.test_client()
        with self.desktop():
            self.assertIn("/start-new-lab", anyone.get("/login").get_data(as_text=True))
            page = anyone.get("/start-new-lab").get_data(as_text=True)
            self.assertIn("Set this lab aside", page)
            self.assertIn(str(self.data / "old-labs"), page)
            page = anyone.post("/start-new-lab", follow_redirects=True).get_data(as_text=True)
            self.assertTrue(paths.new_lab_asked_for())
            self.assertIn("Keep this lab instead", page)
            page = anyone.post("/start-new-lab", data={"action": "keep"}, follow_redirects=True).get_data(as_text=True)
            self.assertFalse(paths.new_lab_asked_for())
            self.assertIn("Nothing changes", page)

    def test_the_first_page_says_where_the_old_lab_went_until_the_new_one_has_an_account(self):
        anyone = app.test_client()
        with self.desktop(LAB_SET_ASIDE="/somewhere/old-labs/2026-10-08 120000"):
            # This test database has accounts, so it is not a new lab.
            self.assertNotIn("/somewhere/old-labs", anyone.get("/login").get_data(as_text=True))
            with mock.patch("app.app.no_accounts_yet", return_value=True), \
                    mock.patch("app.door.no_accounts_yet", return_value=True), \
                    mock.patch("app.devices.on_this_computer", return_value=True):
                html = anyone.get("/login", follow_redirects=True).get_data(as_text=True)
                self.assertIn("/somewhere/old-labs", html)
                self.assertIn("Does your lab already use BioManager?", html)
