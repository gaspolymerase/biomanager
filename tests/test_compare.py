"""Compare two plasmids (app/plasmid_compare.py, app/compare_routes.py)."""
from __future__ import annotations

import json
import random
import unittest

from tests.base import uniq
from tests.test_inventory import InventoryCase

from app import plasmid_compare as pc
from app.seqverify import reverse_complement as rc

_rng = random.Random(11)
SEQ = "".join(_rng.choice("ACGT") for _ in range(4000))
CDS = [{"name": "GENE", "type": "CDS", "start": 1000, "end": 1599, "direction": 1}]


class TheComparison(unittest.TestCase):
    def test_identical_is_the_same(self):
        r = pc.compare(SEQ, True, CDS, "A", SEQ, True, CDS, "B")
        self.assertTrue(r["matched"] and r["same"])
        self.assertEqual((r["count"], r["identity"]), (0, 100.0))

    def test_a_point_change_names_its_feature_and_codon(self):
        m = list(SEQ)
        m[1200] = "A" if m[1200] != "A" else "C"
        r = pc.compare("".join(m), True, CDS, "A", SEQ, True, CDS, "B")
        self.assertEqual(r["count"], 1)
        d = r["differences"][0]
        self.assertEqual((d["kind"], d["at_b"]), ("mismatch", 1201))
        self.assertIn("GENE", d["features"])
        self.assertTrue(d["effect"])            # silent/missense named

    def test_a_deletion_is_one_difference(self):
        r = pc.compare(SEQ[:1300] + SEQ[1303:], True, CDS, "A", SEQ, True, CDS, "B")
        self.assertEqual(r["count"], 1)
        self.assertEqual((r["differences"][0]["kind"], r["differences"][0]["length"]), ("deletion", 3))

    def test_the_other_strand_and_a_shifted_origin_still_match(self):
        self.assertEqual(pc.compare(SEQ, True, CDS, "A", rc(SEQ), True, [], "B")["strand"], "reverse")
        rot = pc.compare(SEQ, True, CDS, "A", SEQ[500:] + SEQ[:500], True, [], "B")
        self.assertTrue(rot["same"])

    def test_different_plasmids_do_not_line_up(self):
        other = "".join(random.Random(99).choice("ACGT") for _ in range(4000))
        self.assertFalse(pc.compare(SEQ, True, [], "A", other, True, [], "B")["matched"])

    def test_no_sequence(self):
        self.assertFalse(pc.compare("", True, [], "A", SEQ, True, [], "B")["ok"])


class ThroughThePages(InventoryCase):
    def setUp(self):
        self.a = self._plasmid("cmp-A-", SEQ)
        m = list(SEQ)
        m[1200] = "A" if m[1200] != "A" else "C"
        self.b = self._plasmid("cmp-B-", "".join(m))

    def _plasmid(self, prefix, sequence):
        row = self.make_plasmid(self.m, name=uniq(prefix))
        from app.db import SessionLocal
        from app.models import PlasmidRecord
        with SessionLocal() as s:
            p = s.get(PlasmidRecord, row)
            p.full_sequence, p.is_circular = sequence, True
            p.features_json = json.dumps(CDS)
            s.commit()
            return p.plasmid_id, row

    def test_others_and_compare(self):
        (a_num, a_row), (b_num, _b_row) = self.a, self.b
        others = self.m.get(f"/plasmids/{a_row}/compare/others.json").get_json()
        self.assertTrue(others["ok"])
        self.assertIn(b_num, [p["number"] for p in others["plasmids"]])
        j = self.m.get(f"/plasmids/{a_row}/compare.json?other={b_num}").get_json()
        self.assertTrue(j["ok"] and j["matched"])
        self.assertEqual(j["count"], 1)
        self.assertEqual(j["differences"][0]["kind"], "mismatch")
        self.assertIn("/plasmid/", j["b"]["url"])
        same = self.m.get(f"/plasmids/{a_row}/compare.json?other={a_num}")
        self.assertEqual(same.status_code, 400)             # not with itself


if __name__ == "__main__":
    unittest.main()
