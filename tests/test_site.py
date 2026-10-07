"""The website (site/): every page links the shared files by their content,
and the Chinese pages space their bold labels."""
from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "site-stamp.py"


class EveryPageIsStamped(unittest.TestCase):
    """A page that changed must not arrive with a browser's old copy of
    style.css, site.js or the icons: run scripts/site-stamp.py after
    changing any of them."""

    def test_nothing_is_stale(self):
        spec = importlib.util.spec_from_file_location("site_stamp", SCRIPT)
        stamp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stamp)
        self.assertEqual(stamp.run(check=True), [], "run python scripts/site-stamp.py")


class ChineseLabelsAreSpaced(unittest.TestCase):
    """Chinese has no word spacing, so a bold UI label runs into the
    sentence unless a half-width space separates it: 在 笼 > 产仔 中点击,
    not 在笼>产仔中点击. Chinese punctuation separates on its own, so no
    space is wanted there."""

    HAN = r"\u4e00-\u9fff"
    PUNCT = r"\u3001\u3002\uff0c\uff1b\uff1a\uff01\uff1f\uff09\u300b\u300d\u201d"

    def test_every_bold_label_is_spaced_off_the_chinese_around_it(self):
        unspaced = []
        for page in sorted((ROOT / "site" / "zh").rglob("*.html")):
            body = re.search(r"<!-- page -->([\s\S]*?)<!-- /page -->", page.read_text(encoding="utf-8"))
            if body is None:
                continue
            text = body.group(1)
            for pattern in (rf"[{self.HAN}]<(?:b|strong)>", rf"</(?:b|strong)>[{self.HAN}]",
                            rf"[{self.PUNCT}] <(?:b|strong)>", r"  <(?:b|strong)>", r"</(?:b|strong)>  "):
                for hit in re.finditer(pattern, text):
                    unspaced.append(f"{page.relative_to(ROOT)}: …{text[max(0, hit.start() - 18):hit.end() + 18]}…")
        self.assertEqual(unspaced, [], "put a half-width space between a bold label and the Chinese beside it")


if __name__ == "__main__":
    unittest.main()
