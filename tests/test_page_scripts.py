"""The page scripts in app/static that need a DOM to check, checked in Node
with jsdom (tests/js/package.json: `npm ci --prefix tests/js`). Skipped
where Node or jsdom isn't there, except in CI, which installs them: there
a missing jsdom is a failure, not a quiet skip."""
from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSDOM = ROOT / "tests" / "js" / "node_modules" / "jsdom"
IN_CI = os.environ.get("CI") == "true"


@unittest.skipUnless(IN_CI or (shutil.which("node") and JSDOM.exists()),
                     "needs Node and jsdom: npm ci --prefix tests/js")
class PageScripts(unittest.TestCase):
    def check(self, name: str) -> None:
        self.assertTrue(JSDOM.exists(), "tests/js has no jsdom: npm ci --prefix tests/js")
        r = subprocess.run(["node", str(ROOT / "tests" / "js" / name)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

    def test_a_row_save_keeps_the_copies_that_guard_a_colleagues_edit(self):
        self.check("sheet.check.mjs")


class OneEscape(unittest.TestCase):
    def test_page_scripts_escape_html_with_the_one_helper(self):
        """BioHtml.escape (static/html.js) is the one way a page script puts
        text into HTML; a copy of it in another file is how a variant that
        forgets a quote or prints "null" comes back."""
        copies = [f.name for f in (ROOT / "app" / "static").glob("*.js")
                  if f.name != "html.js" and "'&lt;'" in f.read_text(encoding="utf-8")]
        self.assertEqual(copies, [], "use BioHtml.escape (static/html.js)")


if __name__ == "__main__":
    unittest.main()
