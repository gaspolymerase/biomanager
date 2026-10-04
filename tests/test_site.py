"""The website (site/): every page links the shared files by their content."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "site-stamp.py"


class EveryPageIsStamped(unittest.TestCase):
    """A page that changed must not arrive with a browser's old copy of
    style.css, site.js or the icons: run scripts/site-stamp.py after
    changing any of them."""

    def test_nothing_is_stale(self):
        spec = importlib.util.spec_from_file_location("site_stamp", SCRIPT)
        stamp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stamp)
        self.assertEqual(stamp.run(check=True), [], "run python scripts/site-stamp.py")


if __name__ == "__main__":
    unittest.main()
