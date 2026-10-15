"""Lists of what people number come in number order (app/positions.py
natural): cage 2 before cage 10, not after it as text would put it."""
from __future__ import annotations

import json
import re
import unittest

from tests.base import *  # noqa: F401,F403


class NaturalKey(unittest.TestCase):
    def test_numbers_as_numbers_and_the_rest_without_case(self):
        from app.positions import natural_sorted
        self.assertEqual(natural_sorted(["10", "1", "101", "2", "C-104", "c-99", "A10", "a2", "B"]),
                         ["1", "2", "10", "101", "a2", "A10", "B", "c-99", "C-104"])

    def test_by_an_attribute_or_several(self):
        from types import SimpleNamespace as NS
        from app.positions import natural_sorted
        cages = [NS(cage_id="M-12"), NS(cage_id="M-3"), NS(cage_id=None)]
        self.assertEqual([c.cage_id for c in natural_sorted(cages, key=lambda c: c.cage_id)], [None, "M-3", "M-12"])
        self.assertEqual(natural_sorted([("R10", "1"), ("R2", "10"), ("R2", "9")], key=lambda r: r),
                         [("R2", "9"), ("R2", "10"), ("R10", "1")])


class CageSheet(AppTestCase):
    def test_cages_in_number_order(self):
        # The cage sheet, and with it the rack's Unplaced tray (the tray
        # keeps the order the sheet's cages come in).
        prefix = uniq("N")
        ids = {code: self.make_cage(self.m, f"{prefix}-{code}") for code in ("10", "2", "101", "1")}
        html = self.get_ok(self.m, "/colony?view=cages&scope=mine")
        at = {code: html.index(f'<tr id="cage-{row}"') for code, row in ids.items()}
        self.assertEqual(sorted(at, key=at.get), ["1", "2", "10", "101"])
        payload = json.loads(re.search(r"data-rack-data>(.*?)</script>", html, re.S).group(1))
        tray = [i["label"] for i in payload["items"] if i["label"].startswith(prefix + "-")]
        self.assertEqual(tray, [f"{prefix}-{code}" for code in ("1", "2", "10", "101")])
