"""Site-directed mutagenesis (app/mutagenesis.py, app/mutagenesis_routes.py)."""
from __future__ import annotations

import json
import unittest

from tests.base import one, rows, uniq
from tests.test_sequence_view import EGFP, PLASMID, genbank
from tests.test_inventory import InventoryCase

from app import mutagenesis, primer_design
from app.primer_records import reverse_complement

GFP = {"name": "EGFP", "type": "CDS", "start": 1000, "end": 1719, "direction": 1}
GFP_BACK = {"name": "EGFP", "type": "CDS", "start": 1000, "end": 1719, "direction": -1}
FLIPPED = reverse_complement(PLASMID)


def made(sequence: str, result: dict, change: mutagenesis.Change) -> None:
    """The two primers, run as a PCR on the new plasmid, give all of it."""
    after = mutagenesis.apply(sequence, change)
    product = primer_design.pcr(after, True, result["forward"]["sequence"], result["reverse"]["sequence"])
    assert product is not None and product["length"] == len(after), (product, len(after))


class TheDesign(unittest.TestCase):
    def test_a_codon_by_its_amino_acid(self):
        codon = mutagenesis.codon_change(PLASMID, GFP, "K80A")
        self.assertEqual((codon["start"], codon["end"], codon["codon"]), (1238, 1240, "AAG"))
        self.assertEqual(codon["to"], "GCG")                 # one base fewer changed than GCC
        result = mutagenesis.design(PLASMID, True, [GFP], codon["start"], codon["end"], codon["bases"])
        self.assertEqual(result["effects"][0]["label"], "K80A")
        self.assertEqual(result["effects"][0]["kind"], "missense")
        self.assertEqual(result["forward"]["tail"], "GCG")
        self.assertEqual(result["reverse"]["tail"], "")
        for primer in (result["forward"], result["reverse"]):
            self.assertGreaterEqual(primer["tm"], 58)
        made(PLASMID, result, mutagenesis.change_from(PLASMID, 1238, 1240, "GCG"))
        with self.assertRaises(mutagenesis.Problem) as wrong:
            mutagenesis.codon_change(PLASMID, GFP, "L80A")
        self.assertEqual((wrong.exception.code, wrong.exception.details["aa"]), ("not_that", "K"))

    def test_the_same_on_the_other_strand(self):
        # EGFP read backwards: the codon's bases are on the bottom strand.
        n = len(FLIPPED)
        back = dict(GFP_BACK, start=n - 1720, end=n - 1001)
        codon = mutagenesis.codon_change(FLIPPED, back, "K80*")
        result = mutagenesis.design(FLIPPED, True, [back], codon["start"], codon["end"], codon["bases"])
        self.assertEqual(result["effects"][0]["label"], "K80*")
        self.assertEqual(result["effects"][0]["kind"], "nonsense")

    def test_deletions_in_frame_and_out(self):
        in_frame = mutagenesis.design(PLASMID, True, [GFP], 1300, 1302, "")
        self.assertEqual((in_frame["kind"], in_frame["length"]), ("deletion", len(PLASMID) - 3))
        self.assertEqual(in_frame["effects"][0]["kind"], "deletion")
        self.assertEqual((in_frame["forward"]["tail"], in_frame["reverse"]["tail"]), ("", ""))
        made(PLASMID, in_frame, mutagenesis.change_from(PLASMID, 1300, 1302, ""))
        shifted = mutagenesis.design(PLASMID, True, [GFP], 1300, 1301, "")
        effect = shifted["effects"][0]
        self.assertEqual(effect["kind"], "frameshift")
        self.assertRegex(effect["label"], r"^F101\wfs\*\d+$")      # read on past EGFP to its new stop

    def test_a_long_insertion_rides_on_both_primers(self):
        linker = "GGCGGCAGCGGCGGCAGC"
        result = mutagenesis.design(PLASMID, True, [GFP], 1301, 1300, linker)       # after base 1,300
        self.assertEqual(result["kind"], "insertion")
        self.assertEqual(result["forward"]["tail"] and reverse_complement(result["reverse"]["tail"]) + result["forward"]["tail"],
                         linker)
        made(PLASMID, result, mutagenesis.change_from(PLASMID, 1301, 1300, linker))
        self.assertEqual(result["effects"][0]["length_after"], 239 + 6)

    def test_what_cannot_be_made(self):
        for args, code in (((1, 5000, "A"), "outside"), ((1238, 1240, "AAG"), "same"), ((1300, 1299, ""), "nothing"),
                           ((1300, 1299, "A" * 80), "too_long")):
            with self.assertRaises(mutagenesis.Problem) as caught:
                mutagenesis.design(PLASMID, True, [GFP], *args)
            self.assertEqual(caught.exception.code, code)
        with self.assertRaises(mutagenesis.Problem) as caught:
            mutagenesis.design(PLASMID, False, [GFP], 1238, 1240, "GCG")
        self.assertEqual(caught.exception.code, "linear")

    def test_features_move_with_the_change(self):
        change = mutagenesis.change_from(PLASMID, 1301, 1300, "GGCGGC")
        features = [GFP, {"name": "after", "start": 2000, "end": 2100},
                    {"name": "before", "start": 100, "end": 200},
                    {"name": "a primer", "kind": "primer", "start": 1290, "end": 1310}]
        moved = {f["name"]: (f["start"], f["end"]) for f in mutagenesis.shift_features(features, len(PLASMID), change)}
        self.assertEqual(moved, {"EGFP": (1000, 1725), "after": (2006, 2106), "before": (100, 200)})
        gone = mutagenesis.shift_features([{"name": "inside", "start": 1300, "end": 1302}], len(PLASMID),
                                          mutagenesis.change_from(PLASMID, 1300, 1304, ""))
        self.assertEqual(gone, [])

    def test_how_a_protein_change_is_written(self):
        self.assertEqual(mutagenesis.describe("MKLV*", "MKLV*", False), ("silent", ""))
        self.assertEqual(mutagenesis.describe("MKLV*", "MKV*", False), ("deletion", "L3del"))
        self.assertEqual(mutagenesis.describe("MKLV*", "MKGSLV*", False), ("insertion", "K2_L3insGS"))
        self.assertEqual(mutagenesis.describe("MKLV*", "MK*V*", False), ("nonsense", "L3*"))

    def test_silent_changes_that_add_or_remove_a_site(self):
        found = mutagenesis.silent_sites(PLASMID, GFP)
        self.assertTrue(found["add"])
        protein = mutagenesis.translate(mutagenesis.bases_of(PLASMID, GFP))
        for row in found["add"][:5] + found["remove"][:5]:
            change = mutagenesis.Change(row["start"] - 1, row["end"] - row["start"] + 1, row["bases"])
            after = mutagenesis.apply(PLASMID, change)
            self.assertEqual(mutagenesis.translate(mutagenesis.bases_of(after, GFP)), protein, row)
        best = found["add"][0]
        self.assertEqual(best["cuts_now"], 0)
        self.assertEqual(best["cuts_after"], 1)
        near = mutagenesis.silent_sites(PLASMID, GFP, near=(1238, 1240))
        self.assertTrue(all(abs(r["codon"] - 80) <= mutagenesis.NEAR_CODONS + 1 for r in near["add"] + near["remove"]))


class MakeIt(InventoryCase):
    def setUp(self):
        self.name = uniq("pMut-")
        self.row = self.make_plasmid(self.m, name=self.name)
        self.post(self.m, f"/plasmids/{self.row}/upload-sequence", {"sequence_text": genbank(self.name)})

    def test_design_by_protein_and_make_it(self):
        j = self.m.get(f"/plasmids/{self.row}/mutagenesis.json?feature=0&protein=K80A").get_json()
        self.assertTrue(j["ok"], j)
        self.assertEqual(j["label"], "K80A")
        self.assertEqual(j["name"], f"{self.name}-K80A")
        bad = self.m.get(f"/plasmids/{self.row}/mutagenesis.json?feature=0&protein=L80A").get_json()
        self.assertEqual(bad["error"], "Codon 80 is K.")
        r = self.m.post(f"/plasmids/{self.row}/mutagenesis",
                        data={"feature": "0", "protein": "K80A", "name": f"{self.name}-K80A"}).get_json()
        self.assertTrue(r["ok"], r)
        new = one("select id from plasmids where plasmid_id=?", r["number"])
        sequence, features = one("select full_sequence from plasmids where id=?", new), \
            json.loads(one("select features_json from plasmids where id=?", new))
        self.assertEqual(sequence[1237:1240], "GCG")
        self.assertEqual(len(sequence), len(PLASMID))
        self.assertIn("K80A", [f["name"] for f in features])
        self.assertEqual(one("select method from plasmid_parents where child_row_id=?", new), "mutagenesis")
        self.assertEqual(one("select parent_row_id from plasmid_parents where child_row_id=?", new), self.row)
        self.assertEqual(one("select how from plasmid_sequence_versions where plasmid_row_id=?", new), "mutagenesis")
        primers = [json.loads(a) for (a,) in rows("select attrs from inventory_items where name like ?",
                                                       f"{self.name}-K80A %")]
        self.assertEqual(len(primers), 2)
        self.assertTrue(any(p["sequence"].startswith("GCG") for p in primers))
        page = self.get_ok(self.m, f"/plasmid/{r['number']}")
        self.assertIn("Mutagenesis", page)

    def test_a_deletion_by_bases_and_the_silent_sites(self):
        j = self.m.get(f"/plasmids/{self.row}/mutagenesis.json?start=1300&end=1302&bases=").get_json()
        self.assertEqual((j["kind"], j["delta"]), ("deletion", -3))
        sites = self.m.get(f"/plasmids/{self.row}/silent-sites.json?feature=0&start=1238&end=1240").get_json()
        self.assertTrue(sites["ok"])
        self.assertTrue(sites["add"])
        self.assertEqual(self.m.get(f"/plasmids/{self.row}/silent-sites.json?feature=9").status_code, 400)


if __name__ == "__main__":
    unittest.main()
