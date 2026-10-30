"""Gateway BP and LR recombination (app/gateway.py, app/gateway_routes.py)."""
from __future__ import annotations

import json
import random
import unittest

from tests.base import count, one, rows, uniq
from tests.test_inventory import InventoryCase

from app import gateway
from app.gateway import Molecule

A = gateway.ATT_SEQUENCES
_rng = random.Random(3)


def rnd(n: int) -> str:
    return "".join(_rng.choice("ACGT") for _ in range(n))


def plasmid(pre: str, s1: str, mid: str, mid_name: str, s2: str, post: str, name: str) -> Molecule:
    """A circular molecule: pre-backbone, att site 1, a cassette, att site 2, post-backbone."""
    seq = pre + A[s1] + mid + A[s2] + post
    feats, p = [], len(pre)
    feats.append({"name": s1, "type": "protein_bind", "start": p, "end": p + len(A[s1]) - 1, "direction": 1})
    p += len(A[s1])
    feats.append({"name": mid_name, "type": "CDS", "start": p, "end": p + len(mid) - 1, "direction": 1})
    p += len(mid)
    feats.append({"name": s2, "type": "protein_bind", "start": p, "end": p + len(A[s2]) - 1, "direction": 1})
    return Molecule(seq, True, feats, name)


class TheRecombination(unittest.TestCase):
    def setUp(self):
        self.gene = plasmid(rnd(400), "attB1", "ATG" + rnd(297), "GFP", "attB2", rnd(300), "entry source")
        self.donor = plasmid(rnd(600), "attP1", rnd(900), "ccdB", "attP2", rnd(500), "pDONR221")

    def test_bp_makes_an_entry_clone_and_moves_ccdb_to_the_byproduct(self):
        out = gateway.recombine(self.gene, self.donor)
        self.assertEqual(out["reaction"], "BP")
        self.assertEqual(out["insert"], "entry source")
        p = out["product"]
        names = [f["name"] for f in p["features"]]
        self.assertEqual(names, ["attL1", "GFP", "attL2"])            # gene on the donor backbone, L sites
        self.assertNotIn("ccdB", names)
        self.assertEqual(out["removed"], ["ccdB"])
        self.assertIn("ccdB", [f["name"] for f in out["byproduct"]["features"]])
        self.assertEqual(out["byproduct"]["kind"], "R")
        # The gene is kept whole, and the junction reads through with no seam.
        self.assertIn(self.gene.sequence[425:725], p["sequence"])
        self.assertEqual(len(p["sequence"]), p["length"])

    def test_which_plasmid_is_which_does_not_matter(self):
        a = gateway.recombine(self.gene, self.donor)["product"]["sequence"]
        b = gateway.recombine(self.donor, self.gene)["product"]["sequence"]
        self.assertEqual(a, b)

    def test_lr_makes_an_expression_clone(self):
        entry = plasmid(rnd(300), "attL1", "ATG" + rnd(297), "GFP", "attL2", rnd(250), "pENTR-GFP")
        dest = plasmid(rnd(800), "attR1", rnd(700), "ccdB", "attR2", rnd(1500), "pDEST")
        out = gateway.recombine(entry, dest)
        self.assertEqual(out["reaction"], "LR")
        self.assertEqual([f["name"] for f in out["product"]["features"]], ["attB1", "GFP", "attB2"])
        self.assertEqual(out["removed"], ["ccdB"])

    def test_sites_that_make_no_reaction(self):
        two_b = plasmid(rnd(200), "attB1", rnd(100), "x", "attB2", rnd(200), "also B")
        with self.assertRaises(gateway.Problem) as caught:
            gateway.recombine(self.gene, two_b)
        self.assertEqual(caught.exception.code, "no_reaction")
        with self.assertRaises(gateway.Problem):
            gateway.recombine(Molecule("", True, [], "empty"), self.donor)

    def test_the_canonical_att_sites_share_a_core(self):
        for n in ("1", "2"):
            self.assertGreaterEqual(gateway._longest_common(A[f"attB{n}"], A[f"attP{n}"])[2], gateway.MIN_CORE)
            self.assertGreaterEqual(gateway._longest_common(A[f"attL{n}"], A[f"attR{n}"])[2], gateway.MIN_CORE)


class ThroughThePages(InventoryCase):
    def setUp(self):
        self.gene_no = self._make("entry source", rnd(400), "attB1", "ATG" + rnd(297), "GFP", "attB2", rnd(300))
        self.donor_no = self._make("pDONR221", rnd(600), "attP1", rnd(900), "ccdB", "attP2", rnd(500))

    def _make(self, name, pre, s1, mid, mid_name, s2, post):
        mol = plasmid(pre, s1, mid, mid_name, s2, post, name)
        row = self.make_plasmid(self.m, name=uniq(name.replace(" ", "-") + "-"))
        from app.db import SessionLocal
        from app.models import PlasmidRecord
        with SessionLocal() as s:
            p = s.get(PlasmidRecord, row)
            p.full_sequence, p.is_circular = mol.sequence, True
            p.features_json = json.dumps(mol.features)
            s.commit()
            number = p.plasmid_id
        return number

    def test_preview_then_make(self):
        j = self.m.post("/plasmids/assembly/gateway/preview",
                        json={"a": self.gene_no, "b": self.donor_no}).get_json()
        self.assertTrue(j["ok"], j)
        self.assertEqual(j["reaction"], "BP")
        self.assertEqual(j["removed"], ["ccdB"])
        self.assertIn("GFP", [f["name"] for f in j["product"]["features"]])
        r = self.m.post("/plasmids/assembly/gateway",
                        data={"a": self.gene_no, "b": self.donor_no, "name": uniq("pENTR-GFP-")}).get_json()
        self.assertTrue(r["ok"], r)
        new = one("select id from plasmids where plasmid_id=?", r["number"])
        feats = [f["name"] for f in json.loads(one("select features_json from plasmids where id=?", new))]
        self.assertIn("attL1", feats)
        methods = {m for (m,) in rows("select method from plasmid_parents where child_row_id=?", new)}
        self.assertEqual(methods, {"gateway"})
        self.assertEqual(count("plasmid_parents", "child_row_id=?", new), 2)    # both parents kept


if __name__ == "__main__":
    unittest.main()
