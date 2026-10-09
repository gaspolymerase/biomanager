"""Starting over and moving a lab, and the way back from each:
- the desktop app brings back a lab set aside (app/paths.py, restore_lab);
- Open my lab only opens on Open, and a browser window can come back
  (app/door.py open_lab, elsewhere);
- the whole lab as one file, and a new server taking it in (app/lab_transfer.py,
  app/door.py bring, import_lab);
- Move this lab to a server, and Use this computer's own lab again
  (app/lab_move.py);
- Save the whole lab (app/lab_move.py save)."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path
from unittest import mock

# tests.base first: it points the app at a throwaway database before app is imported.
from tests.base import ROOT, AppTestCase, execute, one
from app import devices, door, lab_copy, lab_transfer, paths  # noqa: E402
from app.app import app  # noqa: E402

DESKTOP = mock.patch("app.devices.on_this_computer", return_value=True)
DESKTOP_APP = mock.patch.dict(app.config, {"LOCAL_SETUP": True})


def tiny_lab(folder: Path, name: str, accounts: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(folder / "biomanager.db") as conn:
        conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
        conn.execute("INSERT INTO app_settings VALUES ('lab_name', ?)", (name,))
        for i in range(accounts):
            conn.execute("INSERT INTO users (username) VALUES (?)", (f"u{i}",))


class BringingBackALabSetAside(AppTestCase):
    def setUp(self):
        super().setUp()
        root = Path(tempfile.mkdtemp())
        self.data, self.uploads = root / "data", root / "uploads"
        patcher = mock.patch.dict(os.environ, {"BIOMANAGER_DATA_DIR": str(self.data),
                                               "BIOMANAGER_UPLOADS_DIR": str(self.uploads)})
        patcher.start()
        self.addCleanup(patcher.stop)
        tiny_lab(self.data, "Rivera Lab", 3)
        self.uploads.mkdir(parents=True, exist_ok=True)
        (self.uploads / "gel.png").write_bytes(b"rivera")

    def test_a_lab_set_aside_comes_back_and_the_one_it_replaces_is_kept(self):
        paths.ask_for_new_lab()
        first = paths.set_lab_aside()
        tiny_lab(self.data, "Okafor Lab", 1)                       # the new lab, made since
        self.uploads.mkdir(parents=True, exist_ok=True)
        (self.uploads / "okafor.png").write_bytes(b"okafor")
        listed = paths.old_labs()
        self.assertEqual([(o["name"], o["accounts"]) for o in listed], [("Rivera Lab", 3)])
        self.assertTrue(paths.ask_to_restore(first.name))
        self.assertEqual(paths.restore_asked_for(), first.name)
        paths.set_lab_aside()
        with sqlite3.connect(self.data / "biomanager.db") as conn:
            self.assertEqual(conn.execute("SELECT value FROM app_settings").fetchone()[0], "Rivera Lab")
        self.assertEqual((self.uploads / "gel.png").read_bytes(), b"rivera")
        self.assertFalse((self.uploads / "okafor.png").exists())
        # The lab it replaced is set aside in turn, so this can be undone the same way.
        self.assertEqual([o["name"] for o in paths.old_labs()], ["Okafor Lab"])
        self.assertEqual(paths.restore_asked_for(), "")

    def test_only_a_folder_that_is_there_can_be_asked_for(self):
        for wrong in ("", "../data", ".hidden", "no such lab"):
            self.assertFalse(paths.ask_to_restore(wrong), wrong)

    def test_the_page_is_the_computers_own_and_can_be_cancelled(self):
        c = app.test_client()
        self.assertEqual(c.get("/restore-lab").status_code, 404)
        paths.ask_for_new_lab()
        aside = paths.set_lab_aside()
        with DESKTOP:
            html = self.get_ok(c, "/restore-lab")
            self.assertIn("Rivera Lab", html)
            c.post("/restore-lab", data={"folder": aside.name})
            self.assertEqual(paths.restore_asked_for(), aside.name)
            self.assertIn("Keep the lab I have", self.get_ok(c, "/restore-lab"))
            c.post("/restore-lab", data={"action": "keep"})
            self.assertEqual(paths.restore_asked_for(), "")


class OpeningALab(AppTestCase):
    FOUND = {"url": "https://lab.example.org", "name": "Rivera Lab", "host": "lab.example.org"}

    def test_finding_a_lab_saves_nothing_until_open(self):
        saved = {}
        c = app.test_client()
        with DESKTOP, mock.patch.object(door, "no_accounts_yet", return_value=True), \
                mock.patch.object(door, "find_lab", return_value=self.FOUND), \
                mock.patch("app.devices._save_prefs", side_effect=lambda **kw: saved.update(kw)):
            html = c.post("/open-lab", data={"address": "lab.example.org"}).get_data(as_text=True)
            self.assertIn("Found your lab", html)
            self.assertEqual(saved, {})                              # Back would change nothing
            r = c.post("/open-lab", data={"action": "open"})
            self.assertEqual(r.headers["Location"], "https://lab.example.org/")
            self.assertEqual(saved, {"window_url": "https://lab.example.org"})
            # Open without having found one: nothing to open.
            self.assertEqual(c.post("/open-lab", data={"action": "open"}).headers["Location"], "/open-lab")

    def test_in_a_browser_the_computer_offers_the_lab_or_its_own(self):
        saved = {}
        c = app.test_client()
        self.assertEqual(c.get("/lab-elsewhere").status_code, 404)
        with DESKTOP, mock.patch("app.devices.window_url", return_value="https://lab.example.org"), \
                mock.patch("app.devices._save_prefs", side_effect=lambda **kw: saved.update(kw)):
            html = self.get_ok(c, "/lab-elsewhere")
            self.assertIn('href="https://lab.example.org/"', html)
            self.assertIn("Use this computer’s BioManager", html)
            c.post("/lab-elsewhere")
        self.assertEqual(saved, {"window_url": ""})

    def test_a_labs_sign_in_offers_the_way_back_inside_the_desktop_window(self):
        html = self.get_ok(app.test_client(), "/")
        self.assertIn("data-this-computer", html)
        self.assertIn("data-desktop-only hidden", html)


class TheLabFile(AppTestCase):
    def make(self, folder: Path) -> Path:
        target = folder / "lab.biomanager"
        with app.app_context():
            lab_transfer.build(target, folder / "work")
        return target

    def test_it_holds_the_lab_but_not_the_machines_own_settings(self):
        execute("insert or replace into app_settings (key, value) values ('devices:state', '{\"phase\": \"away\"}')")
        execute("insert or replace into app_settings (key, value) values ('lab_name', 'Rivera Lab')")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                made = self.make(Path(tmp))
                manifest, database, _ = lab_transfer.open_file(made, Path(tmp) / "open")
                self.assertEqual((manifest["format"], manifest["lab"]), ("biomanager-lab", "Rivera Lab"))
                with sqlite3.connect(database) as conn:
                    keys = {r[0] for r in conn.execute("SELECT key FROM app_settings")}
                    self.assertGreater(conn.execute("SELECT count(*) FROM users").fetchone()[0], 0)
                self.assertIn("lab_name", keys)
                self.assertNotIn("devices:state", keys)
                data, problems = lab_transfer.check(manifest, database)
                self.assertEqual(problems, [])
                self.assertTrue(data["users"])
        finally:
            execute("delete from app_settings where key='devices:state'")

    def test_what_is_not_a_lab_file_is_refused_before_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "junk.biomanager").write_bytes(b"not a zip")
            with self.assertRaises(lab_transfer.LabFileError):
                lab_transfer.open_file(tmp / "junk.biomanager", tmp / "a")
            with zipfile.ZipFile(tmp / "escape.biomanager", "w") as zf:
                zf.writestr("manifest.json", json.dumps({"format": "biomanager-lab", "format_version": 1}))
                zf.writestr("lab.db", b"")
                zf.writestr("uploads/../../outside.txt", b"x")
            with self.assertRaises(lab_transfer.LabFileError):
                lab_transfer.open_file(tmp / "escape.biomanager", tmp / "b")
            made = self.make(tmp)
            with zipfile.ZipFile(made, "a") as zf:
                zf.writestr("lab.db", b"damaged")                   # a second, different lab.db
            with self.assertRaises(lab_transfer.LabFileError):
                lab_transfer.open_file(made, tmp / "c")

    def test_a_file_from_another_version_is_named_as_such(self):
        _, problems = lab_transfer.check({"revision": "0001_baseline", "version": "0.9.0"}, Path("/nonexistent"))
        self.assertIn("0.9.0", problems[0])

    def test_a_lab_with_accounts_takes_no_file(self):
        self.assertEqual(app.test_client().get("/lab/bring").status_code, 302)
        r = app.test_client().post("/lab/import", data=b"x", headers={"Content-Type": "application/zip"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("already has a lab", r.get_json()["error"])


# Two fresh labs in their own processes: one saves itself as a file, the other
# (a new server) takes it in, by its page and by the desktop's Move.
MAKE = textwrap.dedent("""
    import os, sys
    from pathlib import Path
    sys.path.insert(0, os.environ["ROOT"])
    from app.app import app
    from app import lab_transfer
    from app.db import SessionLocal
    from app.models import UserAccount
    from app.inventory_service import set_setting
    with SessionLocal() as s:
        s.add(UserAccount(username="pi", password_hash="x", role="admin"))
        s.add(UserAccount(username="sam", password_hash="x", role="member"))
        set_setting(s, "lab_name", "Rivera Lab")
        s.commit()
    Path(os.environ["UPLOADS"], "gel.png").write_bytes(b"png")
    with app.app_context():
        lab_transfer.build(Path(os.environ["OUT"]), Path(os.environ["OUT"]).parent / "work")
""")
TAKE = textwrap.dedent("""
    import json, os, sys
    from pathlib import Path
    sys.path.insert(0, os.environ["ROOT"])
    from app.app import app
    from app import security
    from app.db import engine
    from sqlalchemy import text
    c = app.test_client()
    code = security.setup_code()
    out = {"no_lab": "has no lab yet" in c.get("/").get_data(as_text=True)}
    body = Path(os.environ["FILE"]).read_bytes()
    r = c.post("/lab/import", data=body, headers={"Content-Type": "application/zip", "X-BioManager-Setup-Code": "wrong"})
    out["wrong_code"] = [r.status_code, r.get_json()["error"]]
    r = c.post("/lab/import", data=body, headers={"Content-Type": "application/zip", "X-BioManager-Setup-Code": code,
                                                   "X-BioManager-Admin": "pi"})
    out["answer"] = r.get_json()
    with engine.connect() as conn:
        out["users"] = conn.execute(text("select count(*) from users")).scalar()
        out["lab"] = conn.execute(text("select value from app_settings where key='lab_name'")).scalar()
        out["keys"] = conn.execute(text("select count(*) from lab_copy_keys")).scalar()
    out["files"] = sorted(p.name for p in Path(os.environ["UPLOADS"]).rglob("*") if p.is_file())
    r = c.post("/lab/import", data=body, headers={"Content-Type": "application/zip", "X-BioManager-Setup-Code": code})
    out["again"] = r.status_code
    out["front"] = "Sign in to Rivera Lab" in c.get("/").get_data(as_text=True)
    print(json.dumps(out))
""")


class ANewServerTakesALab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("BIOMANAGER_TEST_DATABASE_URL"):
            raise unittest.SkipTest("the fresh labs run on their own SQLite files")
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)

        def run(script, name, **env):
            folder = tmp / name
            (folder / "uploads").mkdir(parents=True)
            environ = {k: v for k, v in os.environ.items() if k not in ("BIOMANAGER_SEED_DEFAULTS",)}
            environ.update(ROOT=ROOT, BIOMANAGER_DATA_DIR=str(folder), DATABASE_URL=f"sqlite:///{folder}/lab.db",
                           BIOMANAGER_UPLOADS_DIR=str(folder / "uploads"), UPLOADS=str(folder / "uploads"),
                           SECRET_KEY="lab-move-test-key", BIOMANAGER_TELEMETRY="0", **env)
            done = subprocess.run([sys.executable, "-c", script], env=environ, capture_output=True, text=True, timeout=180)
            if done.returncode != 0:
                raise AssertionError(done.stderr[-3000:])
            return done.stdout
        lab_file = tmp / "rivera.biomanager"
        run(MAKE, "desktop", OUT=str(lab_file))
        cls.result = json.loads(run(TAKE, "server", FILE=str(lab_file)).strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_a_new_server_says_it_has_no_lab_until_it_takes_one(self):
        self.assertTrue(self.result["no_lab"])
        self.assertTrue(self.result["front"])

    def test_the_setup_code_is_needed(self):
        self.assertEqual(self.result["wrong_code"][0], 409)
        self.assertIn("setup code", self.result["wrong_code"][1])

    def test_the_whole_lab_arrives_with_its_files_and_a_key_for_the_desktop(self):
        answer = self.result["answer"]
        self.assertTrue(answer["ok"], answer)
        self.assertEqual((answer["lab"], answer["accounts"], answer["files"]), ("Rivera Lab", 2, 1))
        self.assertTrue(answer["key"].startswith("bmk_"))
        self.assertEqual((self.result["users"], self.result["lab"], self.result["keys"]), (2, "Rivera Lab", 1))
        self.assertEqual(self.result["files"], ["gel.png"])

    def test_a_second_lab_is_refused(self):
        self.assertEqual(self.result["again"], 409)


class MovingToAServer(AppTestCase):
    def tearDown(self):
        execute("delete from app_settings where key in ('devices:state', 'lab_copy_server', 'lab_copy_key')")
        super().tearDown()

    def test_after_the_move_this_computer_opens_the_server_and_can_come_back(self):
        saved = {}
        found = {"url": "https://lab.example.org", "name": "Rivera Lab", "host": "lab.example.org"}
        with DESKTOP, DESKTOP_APP, mock.patch.object(door, "find_lab", return_value=found), \
                mock.patch("app.lab_move.push", return_value={"ok": True, "lab": "Rivera Lab", "key": "bmk_test"}) as push, \
                mock.patch("app.devices._save_prefs", side_effect=lambda **kw: saved.update(kw)):
            self.assertIn("Move this lab to a server", self.get_ok(self.a, "/settings/devices"))
            r = self.a.post("/settings/devices/move", data={"address": "lab.example.org", "setup_code": "abcd"})
            self.assertEqual(r.headers["Location"], "https://lab.example.org/")
            self.assertEqual(push.call_args.args[:2], ("https://lab.example.org", "abcd"))
            self.assertEqual(saved, {"window_url": "https://lab.example.org"})
            state = json.loads(one("select value from app_settings where key='devices:state'"))
            self.assertEqual((state["phase"], state["moved"]), ("away", True))
            self.assertEqual(one("select value from app_settings where key='lab_copy_server'"), "https://lab.example.org")
            # Read only here now; the way back is offered and works.
            self.assertIn("Use this computer’s own lab again", self.get_ok(self.a, "/settings/devices"))
            self.post(self.a, "/settings/devices/move/back")
            self.assertEqual(saved, {"window_url": ""})
            self.assertEqual(json.loads(one("select value from app_settings where key='devices:state'") or "{}"), {})

    def test_a_server_that_refuses_says_why_and_nothing_changes_here(self):
        found = {"url": "https://lab.example.org", "name": "", "host": "lab.example.org"}
        with DESKTOP, mock.patch.object(door, "find_lab", return_value=found), \
                mock.patch("app.lab_move.push", return_value={"ok": False, "error": "That setup code is not right.", "problems": []}):
            r = self.post(self.a, "/settings/devices/move", data={"address": "lab.example.org", "setup_code": "x"})
        self.assertIn("That setup code is not right.", r.get_data(as_text=True))
        self.assertIsNone(one("select value from app_settings where key='devices:state'"))

    def test_only_the_desktop_apps_admin_moves_it(self):
        with DESKTOP:
            self.assertEqual(self.m.get("/settings/devices/move").status_code, 403)
        self.assertEqual(self.a.get("/settings/devices/move").status_code, 404)


class SavingTheWholeLab(AppTestCase):
    def test_an_admin_on_a_server_downloads_it(self):
        self.assertIn("Save the whole lab", self.get_ok(self.a, "/settings"))
        self.assertNotIn("Save the whole lab", self.get_ok(self.m, "/settings"))
        r = self.a.post("/settings/lab-file")
        self.assertEqual(r.mimetype, "application/zip")
        self.assertIn(".biomanager", r.headers["Content-Disposition"])
        self.assertEqual(self.m.post("/settings/lab-file").status_code, 403)

    def test_on_the_desktop_it_goes_to_downloads(self):
        with tempfile.TemporaryDirectory() as home, DESKTOP, mock.patch("pathlib.Path.home", return_value=Path(home)):
            (Path(home) / "Downloads").mkdir()
            r = self.a.post("/settings/lab-file")
            self.assertIn("saved_file=", r.headers["Location"])
            self.assertEqual(len(list((Path(home) / "Downloads").glob("*.biomanager"))), 1)
