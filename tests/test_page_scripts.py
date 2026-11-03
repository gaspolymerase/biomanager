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


if __name__ == "__main__":
    unittest.main()
