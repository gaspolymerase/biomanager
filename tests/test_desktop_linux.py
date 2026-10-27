"""Linux without the graphics libraries Qt's window needs (desktop_linux.py):
the web browser instead, and a message whose OK closes the app. Nothing here
opens a browser or shows a dialog."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

# tests.base first: it points the app at a throwaway database (and data folder).
from tests.base import AppTestCase  # noqa: F401
import desktop_linux as dl  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
URL = "http://127.0.0.1:5870/"
ERROR = RuntimeError("You must have either QT or GTK with Python extensions installed in order to use pywebview.")


class InTheBrowser(unittest.TestCase):
    def run_it(self, dialog):
        waited = []
        with mock.patch.object(dl.webbrowser, "open") as opened, mock.patch.object(dl.sys, "stderr"):
            dl.run_in_browser(URL, ERROR, dialog=dialog, wait=lambda: waited.append(True))
        return [c.args[0] for c in opened.call_args_list], waited

    def test_it_opens_the_app_and_the_message_closes_it(self):
        shown = []
        opened, waited = self.run_it(lambda text: shown.append(text) or True)
        self.assertEqual(opened, [URL])
        self.assertEqual(waited, [])
        self.assertIn(URL, shown[0])
        self.assertIn("OK closes BioManager", shown[0])
        self.assertIn("QT or GTK", shown[0])

    def test_without_a_dialog_tool_it_keeps_running(self):
        opened, waited = self.run_it(lambda text: False)
        self.assertEqual((opened, waited), ([URL], [True]))

    def test_the_desktop_s_own_dialog_tool(self):
        ran = []
        self.assertTrue(dl._dialog("hi", which=lambda name: name == "kdialog", run=lambda cmd, check: ran.append(cmd)))
        self.assertEqual(ran[0][:2], ["kdialog", "--title"])
        self.assertFalse(dl._dialog("hi", which=lambda name: None, run=lambda cmd, check: ran.append(cmd)))

    def test_desktop_py_falls_back_on_linux(self):
        text = (ROOT / "desktop.py").read_text(encoding="utf-8")
        self.assertIn("desktop_linux.software_rendering(", text)
        self.assertIn("window.events.shown", text)

    def test_desktop_py_opens_in_the_browser_on_linux(self):
        text = (ROOT / "desktop.py").read_text(encoding="utf-8")
        self.assertIn("desktop_linux.run_in_browser(", text)


class DrawnInSoftware(unittest.TestCase):
    """A display without working OpenGL stops Qt in C++: the window is drawn
    in software where it would (desktop_linux.software_rendering)."""

    def choose(self, marker=False, prefs=None, device=True, environ=None):
        env = dict(environ or {})
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "window-starting"
            if marker:
                path.write_text("1", encoding="utf-8")
            why = dl.software_rendering(path, prefs or {}, environ=env, has_device=lambda: device)
        return why, env

    def test_a_computer_with_graphics_draws_as_usual(self):
        self.assertEqual(self.choose(), (None, {}))

    def test_no_graphics_device(self):
        why, env = self.choose(device=False)
        self.assertEqual(why, "no graphics device")
        self.assertEqual((env["QT_XCB_GL_INTEGRATION"], env["QT_QUICK_BACKEND"]), ("none", "software"))
        self.assertEqual(env["QTWEBENGINE_CHROMIUM_FLAGS"], "--disable-gpu")

    def test_a_window_that_never_appeared_last_time(self):
        why, env = self.choose(marker=True, environ={"QTWEBENGINE_CHROMIUM_FLAGS": "--no-sandbox"})
        self.assertEqual(why, "the window didn't open last time")
        self.assertEqual(env["QTWEBENGINE_CHROMIUM_FLAGS"], "--no-sandbox --disable-gpu")

    def test_and_from_then_on(self):
        self.assertEqual(self.choose(prefs={"software_window": True})[0], "chosen before")

    def test_someone_s_own_setting_is_kept(self):
        why, env = self.choose(device=False, environ={"QT_XCB_GL_INTEGRATION": "xcb_egl"})
        self.assertEqual((why, env), (None, {"QT_XCB_GL_INTEGRATION": "xcb_egl"}))


if __name__ == "__main__":
    unittest.main()
