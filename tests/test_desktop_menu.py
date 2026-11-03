"""The desktop app's version, update check and menus (desktop_updates.py,
desktop_menu.py). Nothing here reaches GitHub or opens a window."""
from __future__ import annotations

import sys
import unittest
from unittest import mock

# tests.base first: it points the app at a throwaway database (and data folder).
from tests.base import AppTestCase  # noqa: F401
import desktop_menu  # noqa: E402
import desktop_updates as du  # noqa: E402

RELEASE = {"version": "0.6.0", "notes": "New things", "page": "https://example.org/r", "download": "https://example.org/d.zip"}


class Versions(unittest.TestCase):
    def test_comparing(self):
        self.assertTrue(du.is_newer("0.6.0", "0.5.0"))
        self.assertTrue(du.is_newer("v0.5.1", "0.5"))
        self.assertTrue(du.is_newer("0.10.0", "0.9.9"))
        self.assertFalse(du.is_newer("0.5.0", "0.5.0+dev"))
        self.assertFalse(du.is_newer("0.4.2", "0.5.0"))
        self.assertFalse(du.is_newer("nonsense", "0.5.0"))

    def test_a_release_candidate_comes_before_its_release(self):
        self.assertTrue(du.is_newer("1.0.0", "1.0.0-rc.1"))       # an app on the candidate is offered 1.0.0
        self.assertTrue(du.is_newer("1.0.0-rc.2", "1.0.0-rc.1"))
        self.assertFalse(du.is_newer("1.0.0-rc.1", "1.0.0"))
        self.assertTrue(du.is_newer("1.0.0-rc.1", "0.10.2"))
        self.assertFalse(du.is_newer("1.0.0", "1.0.0+dev"))

    def test_a_build_reads_its_version_file(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "VERSION").write_text("0.7.1\n")
            with mock.patch.object(sys, "_MEIPASS", tmp, create=True):
                self.assertEqual(du.version(), "0.7.1")

    def test_notes_become_plain_text(self):
        text = du.summary("## What's new\n\n- **Import** from [Excel](https://x)\n<!-- hidden -->\n`code`")
        self.assertEqual(text, "What's new\n\n- Import from Excel\n\ncode")
        self.assertTrue(du.summary("word " * 400).endswith("…"))

    def test_notes_show_what_is_new_not_the_downloads(self):
        notes = ("**Download** the file below.\n\n| File | For |\n| --- | --- |\n| `a.zip` | Macs |\n\n"
                 "**New in 0.6.0: sheets**\n\n- **Import from Excel** in every database.\n\n"
                 "Update a lab server: unpack it.\n\n**First launch:** right-click.")
        self.assertEqual(du.summary(notes), "New in 0.6.0: sheets\n\n- Import from Excel in every database.")

    def test_the_file_for_this_computer(self):
        names = ["BioManager-macOS-AppleSilicon.zip", "BioManager-macOS-Intel.zip", "BioManager-Windows.zip",
                 "BioManager-Linux.AppImage"]
        with mock.patch.object(du.sys, "platform", "darwin"), mock.patch.object(du.platform, "machine", return_value="arm64"):
            self.assertEqual(du.asset_for_this_computer(names), "BioManager-macOS-AppleSilicon.zip")
        with mock.patch.object(du.sys, "platform", "win32"):
            self.assertEqual(du.asset_for_this_computer(names), "BioManager-Windows.zip")
        with mock.patch.object(du.sys, "platform", "linux"):
            self.assertEqual(du.asset_for_this_computer(names[:3]), None)


class Preferences(unittest.TestCase):
    """desktop-prefs.json holds the lab this computer joined (its token) as
    well as the window's settings, and the relay and the offline copy read it
    while other parts save it."""

    def setUp(self):
        du.save_prefs(**du.DEFAULT_PREFS)

    def test_a_save_that_cannot_finish_leaves_them_as_they_were(self):
        du.save_prefs(window_url="relay")
        real = du.Path.write_text

        def half_then_disk_full(path, text, *args, **kwargs):
            real(path, text[:len(text) // 2], *args, **kwargs)
            raise OSError("No space left on device")

        with mock.patch.object(du.Path, "write_text", half_then_disk_full):
            du.save_prefs(window_url="", zoom=2.0)
        self.assertEqual(du.load_prefs()["window_url"], "relay")
        self.assertEqual([p.name for p in du._prefs_path().parent.glob("desktop-prefs*")], ["desktop-prefs.json"])


class Checking(unittest.TestCase):
    def setUp(self):
        du.save_prefs(**du.DEFAULT_PREFS)

    def check(self, manual, fetch=lambda: RELEASE, now=1_000_000.0, current="0.5.0"):
        with mock.patch.object(du, "version", return_value=current):
            return du.check(manual=manual, now=now, fetch=fetch)

    def test_asking_says_newer_current_or_why_not(self):
        self.assertEqual(self.check(True)["state"], "newer")
        self.assertEqual(self.check(True, current="0.6.0"), {"state": "current", "version": "0.6.0"})

        def offline():
            raise OSError("no network")
        answer = self.check(True, fetch=offline)
        self.assertEqual(answer["state"], "error")
        self.assertIn("no network", answer["message"])

    def test_the_automatic_check_is_daily_quiet_and_can_be_off(self):
        self.assertEqual(self.check(False)["state"], "newer")
        self.assertEqual(self.check(False, now=1_000_000.0 + 3600)["state"], "quiet")      # checked an hour ago
        self.assertEqual(self.check(False, now=1_000_000.0 + 2 * 86400)["state"], "newer")

        def offline():
            raise OSError("no network")
        self.assertEqual(self.check(False, fetch=offline, now=5_000_000.0)["state"], "quiet")
        du.save_prefs(check_updates=False)
        self.assertEqual(self.check(False, now=9_000_000.0)["state"], "quiet")

    def test_a_skipped_version_is_only_offered_when_asked(self):
        du.save_prefs(skip_version="0.6.0")
        self.assertEqual(self.check(False)["state"], "quiet")
        self.assertEqual(self.check(True)["state"], "newer")


class Bridge(unittest.TestCase):
    def test_the_go_menu_keeps_only_this_app_s_pages(self):
        api = desktop_menu.DesktopApi()
        with mock.patch.dict(sys.modules, {"PyObjCTools": mock.MagicMock(), "PyObjCTools.AppHelper": mock.MagicMock()}):
            api.set_nav([
                {"label": "Databases", "links": [
                    {"label": "Mouse colony", "url": "/colony?view=mice"},
                    {"label": "Elsewhere", "url": "https://evil.example/"},
                    {"label": "Also elsewhere", "url": "//evil.example/x"},
                    {"label": "", "url": "/nameless"},
                ]},
                {"label": "Empty", "links": [{"label": "x", "url": "javascript:alert(1)"}]},
                "not a section",
            ])
        self.assertEqual(desktop_menu._state["nav"],
                         [{"label": "Databases", "links": [{"label": "Mouse colony", "url": "/colony?view=mice"}]}])

    @unittest.skipUnless(__import__("importlib").util.find_spec("webview"), "pywebview isn't installed (CI)")
    def test_windows_and_linux_get_plain_menus(self):
        titles = [m.title for m in desktop_menu.plain_menus()]
        self.assertEqual(titles, ["File", "Go", "Help"])


@unittest.skipUnless(__import__("importlib").util.find_spec("webview"), "pywebview isn't installed (CI)")
class Port(unittest.TestCase):
    def test_the_window_comes_back_on_last_time_s_port(self):
        from app.app import app
        was = app.config.get("LOCAL_SETUP")
        import desktop                      # it switches the app to desktop mode: undo that for the other tests
        if was is None:
            app.config.pop("LOCAL_SETUP", None)
        else:
            app.config["LOCAL_SETUP"] = was
        du.save_prefs(port=0)
        with mock.patch.dict("os.environ", {"BIOMANAGER_PORT": ""}):
            first = desktop._choose_port()
            self.assertEqual(desktop._choose_port(), first)
            with mock.patch.object(desktop, "_port_free", return_value=False):
                self.assertNotEqual(desktop._choose_port(), first)          # taken: another one, remembered
            self.assertEqual(du.load_prefs()["port"], desktop._choose_port())
        with mock.patch.dict("os.environ", {"BIOMANAGER_PORT": "5999"}):
            self.assertEqual(desktop._choose_port(), 5999)


class Installing(unittest.TestCase):
    def test_run_from_source_it_offers_the_download_instead(self):
        self.assertIsNone(du.installed_location())
        self.assertFalse(du.can_install({"download": "https://example.org/x.zip"}))

    def test_a_download_must_match_its_published_checksum(self):
        import hashlib
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "BioManager.zip"
            source.write_bytes(b"new version")
            good = hashlib.sha256(b"new version").hexdigest()
            du._download(source.as_uri(), Path(tmp) / "a.zip", good)
            self.assertTrue((Path(tmp) / "a.zip").exists())
            with self.assertRaises(ValueError):
                du._download(source.as_uri(), Path(tmp) / "b.zip", "0" * 64)
            self.assertFalse((Path(tmp) / "b.zip").exists())

    def test_the_swap_waits_for_this_app_then_keeps_the_old_one(self):
        from pathlib import Path
        name, script = du.swap_script("darwin", 4242, Path("/Applications/BioManager.app"),
                                      Path("/Applications/.BioManager-0.9.0.app"), Path("/tmp/keep.app"))
        self.assertEqual(name, "biomanager-update.sh")
        self.assertIn("kill -0 4242", script)
        self.assertIn('mv "/Applications/BioManager.app" "/tmp/keep.app"', script)
        self.assertIn('open "/Applications/BioManager.app"', script)
        name, script = du.swap_script("win32", 4242, Path("C:/Apps/BioManager"), Path("C:/Apps/new"), Path("C:/Apps/keep"))
        self.assertTrue(name.endswith(".cmd"))
        self.assertIn('PID eq 4242', script)
