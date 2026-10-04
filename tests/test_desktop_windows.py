"""Windows 10 as well as 11 (desktop_windows.py): finding what the window
needs, unblocking the app's downloaded files, and the web browser instead.
Nothing here reads a real registry or shows a message."""
from __future__ import annotations

import ast
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# tests.base first: it points the app at a throwaway database (and data folder).
from tests.base import AppTestCase  # noqa: F401
import desktop_windows as dw  # noqa: E402

NET = r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full"
RUNTIME = dw.WEBVIEW2_CLIENTS[0]


def registry(values: dict):
    return lambda root, path, name: values.get((root, path, name))


def webview2(root: str, build: str, wow: bool = True, client: str = RUNTIME) -> dict:
    prefix = "WOW6432Node\\" if wow else ""
    return {(root, rf"SOFTWARE\{prefix}Microsoft\EdgeUpdate\Clients\{client}", "pv"): build}


NET_OK = {("HKEY_LOCAL_MACHINE", NET, "Release"): 528040}    # .NET 4.8


class Missing(unittest.TestCase):
    def test_a_windows_11_pc_has_everything(self):
        found = {**NET_OK, **webview2("HKEY_LOCAL_MACHINE", "129.0.2792.65")}
        self.assertIsNone(dw.missing(registry(found), is_64bit=True))

    def test_a_windows_10_pc_without_webview2(self):
        self.assertEqual(dw.missing(registry(NET_OK), is_64bit=True), "webview2")

    def test_an_old_or_removed_webview2_does_not_count(self):
        for build in ("85.0.564.0", "0.0.0.0", "", "unknown"):
            found = {**NET_OK, **webview2("HKEY_LOCAL_MACHINE", build)}
            self.assertEqual(dw.missing(registry(found), is_64bit=True), "webview2", build)

    def test_a_per_user_install_or_an_edge_preview_counts(self):
        per_user = {**NET_OK, **webview2("HKEY_CURRENT_USER", "120.0.2210.91", wow=False)}
        self.assertIsNone(dw.missing(registry(per_user), is_64bit=True))
        beta = {**NET_OK, **webview2("HKEY_LOCAL_MACHINE", "130.0.1", client=dw.WEBVIEW2_CLIENTS[1])}
        self.assertIsNone(dw.missing(registry(beta), is_64bit=True))

    def test_32_bit_windows_has_no_wow6432node(self):
        found = {**NET_OK, **webview2("HKEY_LOCAL_MACHINE", "120.0.1", wow=False)}
        self.assertIsNone(dw.missing(registry(found), is_64bit=False))
        self.assertEqual(dw.missing(registry(found), is_64bit=True), "webview2")

    def test_too_old_a_net_comes_first(self):
        found = {("HKEY_LOCAL_MACHINE", NET, "Release"): 394254,   # 4.6.1
                 **webview2("HKEY_LOCAL_MACHINE", "129.0.1")}
        self.assertEqual(dw.missing(registry(found), is_64bit=True), "net")
        self.assertEqual(dw.missing(registry({}), is_64bit=True), "net")


class Unblock(unittest.TestCase):
    def test_only_the_dlls_lose_the_internet_mark(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "webview" / "lib").mkdir(parents=True)
            dll = root / "webview" / "lib" / "Microsoft.Web.WebView2.Core.dll"
            dll.write_bytes(b"MZ")
            # On Windows this is the file's alternate data stream; elsewhere a
            # file of that name, which stands in for it.
            Path(f"{dll}:Zone.Identifier").write_text("[ZoneTransfer]\nZoneId=3\n")
            (root / "notes.txt").write_text("x")
            Path(f"{root / 'notes.txt'}:Zone.Identifier").write_text("[ZoneTransfer]\nZoneId=3\n")
            (root / "Plain.dll").write_bytes(b"MZ")
            self.assertEqual(dw.unblock(root), 1)
            self.assertTrue(dll.exists())
            self.assertFalse(os.path.exists(f"{dll}:Zone.Identifier"))
            self.assertTrue(os.path.exists(f"{root / 'notes.txt'}:Zone.Identifier"))
            self.assertEqual(dw.unblock(root), 0)


class InTheBrowser(unittest.TestCase):
    URL = "http://127.0.0.1:5870/"

    def run_it(self, reason, answer, error=None):
        shown = []

        def message(text, buttons):
            shown.append((text, buttons))
            return answer if buttons == dw.MB_YESNO else 1
        with mock.patch.object(dw.webbrowser, "open") as opened:
            dw.run_in_browser(self.URL, reason, error, message=message)
        return [c.args[0] for c in opened.call_args_list], shown

    def test_yes_downloads_webview2_and_opens_the_app(self):
        opened, shown = self.run_it("webview2", dw.IDYES)
        self.assertEqual(opened, [dw.WEBVIEW2_DOWNLOAD, self.URL])
        self.assertEqual(shown[0][1], dw.MB_YESNO)
        self.assertIn("OK closes BioManager", shown[-1][0])
        self.assertIn(self.URL, shown[-1][0])

    def test_no_opens_only_the_app(self):
        opened, _ = self.run_it("webview2", 7)    # IDNO
        self.assertEqual(opened, [self.URL])

    def test_a_window_that_failed_says_why(self):
        opened, shown = self.run_it("failed", None, error=RuntimeError("Python.Runtime.dll could not load"))
        self.assertEqual(opened, [self.URL])
        self.assertIn("Python.Runtime.dll could not load", shown[0][0])
        self.assertIn("Windows Update", self.run_it("net", None)[1][0][0])


ROOT = Path(__file__).resolve().parent.parent


class ChineseWindows(unittest.TestCase):
    """On a Chinese, Japanese or Korean Windows a file opened without an
    encoding is read in GBK, Shift-JIS or the Korean code page, so reading the
    app's own UTF-8 files (icons.svg) stopped it starting at all."""

    def test_the_built_app_runs_in_utf8_mode(self):
        self.assertIn('[("X utf8", None, "OPTION")]', (ROOT / "Biomanager.spec").read_text(encoding="utf-8"))

    def test_every_text_file_names_its_encoding(self):
        unnamed = []
        for path in [*ROOT.glob("*.py"), *(ROOT / "app").rglob("*.py")]:
            for call in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(call, ast.Call) or any(k.arg == "encoding" for k in call.keywords):
                    continue
                func = call.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name in ("read_text", "write_text"):
                    unnamed.append(f"{path.relative_to(ROOT)}:{call.lineno}")
                elif name == "fdopen" or (name == "open" and isinstance(func, ast.Name)):
                    mode = call.args[1] if len(call.args) > 1 else next(
                        (k.value for k in call.keywords if k.arg == "mode"), ast.Constant("r"))
                    if not (isinstance(mode, ast.Constant) and "b" in str(mode.value)):
                        unnamed.append(f"{path.relative_to(ROOT)}:{call.lineno}")
        self.assertEqual(unnamed, [], "open text files with encoding=\"utf-8\"")


if __name__ == "__main__":
    unittest.main()
