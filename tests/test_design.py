"""The interface's few rules (docs/DEVELOPMENT.md → Interface design), read
from the source so they don't drift: the app asks with its own dialog,
never the browser's; every dialog is the app's dialog and has a name; and
text sizes come from the type scale."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.base import AppTestCase

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"

# What people's eyes read in: page scripts, templates, the page stylesheets
# and the sources the built bundles come from (not the bundles themselves).
SCRIPTS = [*APP.glob("static/*.js"), *APP.glob("static/offline/*.js"),
           *(ROOT / "frontend" / "src").rglob("*.js")]
TEMPLATES = list((APP / "templates").rglob("*.html"))
STYLES = [p for p in APP.glob("static/*.css") if p.name != "tailwind.css"] + list((ROOT / "frontend" / "src").glob("*.css"))

# The type scale (tailwind.css, "Type scale"): 10 for dense grids, 11–17 for
# the interface, 22 and 28 for titles, 16 for a phone's fields, 32 and 40
# for display numbers. 0 hides a label's text and keeps its icon.
SCALE = {0, 10, 11, 12, 13, 15, 16, 17, 22, 28, 32, 40}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _where(path: Path, text: str, at: int) -> str:
    return f"{path.relative_to(ROOT)}:{text.count(chr(10), 0, at) + 1}"


class AsksInItsOwnDialog(unittest.TestCase):
    # alert( confirm( prompt( on their own or as window.…, not as a method
    # (BioDialog.confirm) or a word in a comment.
    NATIVE = re.compile(r"(?<![\w.$])(?:window\.)?(alert|confirm|prompt)\(")
    # Where the browser's is the fallback when the app's isn't on the page.
    ALLOWED = {"dialogs.js", "util.js"}

    def test_no_native_alert_confirm_or_prompt(self):
        found = []
        for path in SCRIPTS + TEMPLATES:
            if path.name in self.ALLOWED:
                continue
            text = _read(path)
            for m in self.NATIVE.finditer(text):
                line = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
                if "//" in line or line.lstrip().startswith("*"):
                    continue
                found.append(_where(path, text, m.start()))
        self.assertEqual(found, [], "use BioDialog.alert / confirm / prompt (static/dialogs.js)")


class EveryDialog(unittest.TestCase):
    def test_has_a_class_and_a_name(self):
        found = []
        for path in TEMPLATES:
            text = _read(path)
            for m in re.finditer(r"<dialog\b[^>]*>", text):
                tag = m.group(0)
                if 'class="' not in tag or not re.search(r"\baria-label(ledby)?=", tag):
                    found.append(_where(path, text, m.start()))
        self.assertEqual(found, [], "a <dialog> needs a class (record-dialog) and aria-labelledby or aria-label")


class TextSizes(unittest.TestCase):
    TAILWIND = re.compile(r"text-\[(\d+(?:\.\d+)?)px\]")
    CSS = re.compile(r"font-size:\s*(\d+(?:\.\d+)?)px")

    def test_on_the_type_scale(self):
        found = []
        for path in SCRIPTS + TEMPLATES + STYLES + list(APP.glob("*.py")):
            text = _read(path)
            for pattern in (self.TAILWIND, self.CSS):
                for m in pattern.finditer(text):
                    if float(m.group(1)) not in SCALE:
                        found.append(f"{_where(path, text, m.start())} {m.group(0)}")
        self.assertEqual(found, [], f"text sizes come from the scale: {sorted(SCALE)}")


class TextColours(unittest.TestCase):
    """Text tokens are readable on what they sit on: 4.5:1 (WCAG AA) in light
    and dark mode, read from the tokens in frontend/src/tailwind.css."""

    # (text, background): the pairs the interface uses for words.
    PAIRS = [("muted", bg) for bg in ("canvas", "surface", "recess", "sand-200")] + [
        ("ink-soft", "canvas"), ("ink-soft", "surface"),
        ("brand-700", "canvas"), ("brand-700", "surface"), ("brand-700", "brand-50"),
        ("info-700", "info-50"), ("ok-700", "ok-50"), ("warn-700", "warn-50"), ("danger-700", "danger-50"),
        ("on-brand", "brand-700"),
    ]

    @staticmethod
    def tokens(block: str) -> dict:
        return dict(re.findall(r"--color-([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", block))

    @staticmethod
    def ratio(a: str, b: str) -> float:
        def lum(h):
            c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
        hi, lo = sorted((lum(a), lum(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    def test_text_is_readable_in_light_and_dark(self):
        css = _read(ROOT / "frontend" / "src" / "tailwind.css")
        light = self.tokens(css[css.index("@theme {"):css.index("@media (prefers-color-scheme: dark)")])
        dark_block = css[css.index("@media (prefers-color-scheme: dark)"):]
        dark = {**light, **self.tokens(dark_block[:dark_block.index("\n}\n")])}
        low = [f"{mode}: {fg} on {bg} {self.ratio(colours[fg], colours[bg]):.2f}"
               for mode, colours in (("light", light), ("dark", dark)) for fg, bg in self.PAIRS
               if self.ratio(colours[fg], colours[bg]) < 4.5]
        self.assertEqual(low, [], "text needs 4.5:1 on its background")


class Gallery(AppTestCase):
    """/design shows the shared pieces to an admin, and to no one else."""

    def test_an_admin_sees_every_piece(self):
        html = self.get_ok(self.a, "/design")
        for piece in ('class="record-dialog', 'class="seg', 'class="dt-chips"', 'class="menu"', 'data-tone="brand"',
                      'class="empty-state"', 'class="dt-empty"', '<datalist id="gallery-strains">'):
            self.assertIn(piece, html)

    def test_a_member_is_turned_away(self):
        response = self.m.get("/design")
        self.assertEqual(response.status_code, 302)


if __name__ == "__main__":
    unittest.main()
