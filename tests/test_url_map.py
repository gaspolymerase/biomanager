"""The routes the app answers must not drift.

Pages, scripts and bookmarks link by a route's path and its endpoint name,
and the rest of the suite asks for URLs rather than endpoints, so a renamed
endpoint breaks pages no test opens. This compares the app's own map with
tests/url_map.txt, which `python scripts/url-map.py --write` writes.
"""
from __future__ import annotations

import difflib
import pathlib
import unittest

from . import base  # noqa: F401  (points the app at a temporary database)
from app.app import app

SAVED = pathlib.Path(__file__).resolve().parent / "url_map.txt"
UNINTERESTING = {"HEAD", "OPTIONS"}


def _now() -> list[str]:
    return sorted(f"{rule.rule}\t{rule.endpoint}\t"
                  f"{' '.join(sorted((rule.methods or set()) - UNINTERESTING))}"
                  for rule in app.url_map.iter_rules())


class TheRoutesTheAppAnswers(unittest.TestCase):
    def test_are_the_ones_that_were_saved(self):
        saved = SAVED.read_text(encoding="utf-8").splitlines()
        now = _now()
        if saved == now:
            return
        diff = "\n".join(difflib.unified_diff(saved, now, "saved", "now", lineterm="", n=1))
        self.fail("The app's routes are not the saved ones.\n"
                  "A route that only moved to another module must keep its path, its\n"
                  "endpoint name and its methods. If the change is meant to add or\n"
                  "remove a route, run python scripts/url-map.py --write and say so in\n"
                  f"the commit.\n\n{diff}")


if __name__ == "__main__":
    unittest.main()
