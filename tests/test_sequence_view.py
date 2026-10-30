"""The plasmid's own sequence viewer, Primers tab and Overview
(app/sequence_routes.py, app/protein.py, app/primer_design.py)."""
from __future__ import annotations

import json
import random
import unittest

from tests.base import one, uniq
from tests.test_inventory import InventoryCase

from app import primer_design, protein
from app.primer_records import reverse_complement

EGFP = ("ATGGTGAGCAAGGGCGAGGAGCTGTTCACCGGGGTGGTGCCCATCCTGGTCGAGCTGGACGGCGACGTAAACGGCCACAAGTTCAGCGTGTCCGGCGAGGGCGAGGGCGATGCCACC"
        "TACGGCAAGCTGACCCTGAAGTTCATCTGCACCACCGGCAAGCTGCCCGTGCCCTGGCCCACCCTCGTGACCACCCTGACCTACGGCGTGCAGTGCTTCAGCCGCTACCCCGACCAC"
        "ATGAAGCAGCACGACTTCTTCAAGTCCGCCATGCCCGAAGGCTACGTCCAGGAGCGCACCATCTTCTTCAAGGACGACGGCAACTACAAGACCCGCGCCGAGGTGAAGTTCGAGGGC"
        "GACACCCTGGTGAACCGCATCGAGCTGAAGGGCATCGACTTCAAGGAGGACGGCAACATCCTGGGGCACAAGCTGGAGTACAACTACAACAGCCACAACGTCTATATCATGGCCGAC"
        "AAGCAGAAGAACGGCATCAAGGTGAACTTCAAGATCCGCCACAACATCGAGGACGGCAGCGTGCAGCTCGCCGACCACTACCAGCAGAACACCCCCATCGGCGACGGCCCCGTGCTG"
        "CTGCCCGACAACCACTACCTGAGCACCCAGTCCGCCCTGAGCAAAGACCCCAACGAGAAGCGCGATCACATGGTCCTGCTGGAGTTCGTGACCGCCGCCGGGATCACTCTCGGCATG"
        "GACGAGCTGTACAAGTAA")
_rng = random.Random(42)
FILL = "".join(_rng.choice("ACGT") for _ in range(4000))
# EGFP at 1,001–1,720 (1-based) in a 4,720 bp circle.
PLASMID = FILL[:1000] + EGFP + FILL[1000:]


class TheProtein(unittest.TestCase):
    def test_egfp_reads_as_protparam_has_it(self):
        about = protein.about(EGFP)
        self.assertEqual(about["length"], 239)
        self.assertTrue(about["protein"].startswith("MVSKGEELFTGVVPILVELD"))
        self.assertEqual(about["mass_kda"], 26.9)
        self.assertAlmostEqual(about["pi"], 5.58, delta=0.05)
        self.assertEqual((about["extinction"], about["extinction_reduced"]), (22015, 21890))
        self.assertEqual(about["stop"], "TAA")
        self.assertFalse(about["internal_stop"])

    def test_open_reading_frames_on_both_strands(self):
        found = protein.orfs(PLASMID, True)
        # EGFP's frame, from the first ATG in it (the filler may hold one just before EGFP's own).
        self.assertTrue(any(o["end"] == 1720 and o["strand"] == 1 and o["codons"] >= 239 for o in found), found)
        flipped = protein.orfs(reverse_complement(PLASMID), True)
        self.assertTrue(any(o["strand"] == -1 and o["codons"] >= 239 and o["start"] == len(PLASMID) - 1720 + 1
                            for o in flipped), flipped)


class ThePrimer(unittest.TestCase):
    def test_a_primer_and_its_checks(self):
        checked = primer_design.check(PLASMID[1100:1120], PLASMID)
        self.assertEqual(checked["length"], 20)
        self.assertEqual(checked["binds"], 1)
        self.assertIsNotNone(checked["tm"])
        self.assertTrue(primer_design.clamp("ACGTACGTACGTTTGAC"))
        self.assertFalse(primer_design.clamp("ACGTACGTACGTGGCCG"))       # too many G/C at the end
        self.assertLess(primer_design.self_dimer("GAATTCGAATTCGAATTC"), primer_design.DIMER_OK)
        self.assertLess(primer_design.hairpin("GGGGCCAAATTGGCCCC"), primer_design.HAIRPIN_OK)

    def test_binding_with_a_tail_and_with_mismatches(self):
        forward = PLASMID[600:620]
        site = primer_design.binding(PLASMID, True, "GAATTC" + forward)[0]
        self.assertEqual((site["start"], site["end"], site["tail"]), (600, 619, 6))
        bent = list(forward)
        bent[2] = "A" if bent[2] != "A" else "C"
        bent[5] = "A" if bent[5] != "A" else "C"
        self.assertEqual(primer_design.binding(PLASMID, True, "".join(bent), mismatches=2)[0]["mismatches"], 2)
        # A mismatch at the 3′ end is never a site.
        three = list(forward)
        three[-1] = "A" if three[-1] != "A" else "C"
        self.assertEqual(primer_design.binding(PLASMID, True, "".join(three), mismatches=2), [])

    def test_a_pcr_product(self):
        product = primer_design.pcr(PLASMID, True, PLASMID[900:920], reverse_complement(PLASMID[1800:1820]))
        self.assertEqual((product["start"], product["end"], product["length"]), (901, 1820, 920))
        self.assertIsNone(primer_design.pcr(PLASMID, True, PLASMID[900:920], PLASMID[1800:1820]))
        across = primer_design.pcr(PLASMID, True, PLASMID[-100:-80], reverse_complement(PLASMID[50:70]))
        self.assertEqual(across["length"], 170)


def genbank(name: str) -> str:
    lines = [f"LOCUS       {name[:16]} {len(PLASMID)} bp    DNA     circular SYN 01-JAN-2026",
             "FEATURES             Location/Qualifiers", "     CDS             1001..1720", '                     /label="EGFP"',
             "ORIGIN"]
    for i in range(0, len(PLASMID), 60):
        lines.append(f"{i + 1:>9} {PLASMID[i:i + 60].lower()}")
    return "\n".join([*lines, "//"])


class TheTabs(InventoryCase):
    def setUp(self):
        self.name = uniq("pView-")
        self.row = self.make_plasmid(self.m, name=self.name)
        self.post(self.m, f"/plasmids/{self.row}/upload-sequence", {"sequence_text": genbank(self.name)})
        self.number = one("select plasmid_id from plasmids where id=?", self.row)
        self.forward = uniq("vF-")
        self.reverse = uniq("vR-")
        key = one("select key from inventory_modules where kind='primers' and private_to='' order by id limit 1") \
            or self.new_module(self.a, "primers")
        self.f_id = self.make_item(self.a, key, name=self.forward, attr_sequence=PLASMID[900:920], is_shared="1")
        self.r_id = self.make_item(self.a, key, name=self.reverse, attr_sequence=reverse_complement(PLASMID[1800:1820]),
                                   is_shared="1")

    def test_the_page_has_its_tabs(self):
        page = self.get_ok(self.m, f"/plasmid/{self.number}")
        for tab in ("overview", "sequence", "digest", "verify", "primers", "storage", "properties"):
            self.assertIn(f'data-tab="{tab}"', page)
        self.assertIn("data-overview", page)
        self.assertIn("data-seq-viewer", page)
        self.assertIn("data-primers-tab", page)

    def test_view_has_the_protein_and_the_labs_primers(self):
        data = self.m.get(f"/plasmids/{self.row}/view.json").get_json()
        self.assertTrue(data["ok"])
        egfp = data["proteins"][0]
        self.assertEqual((egfp["name"], egfp["start"], egfp["end"], egfp["length"]), ("EGFP", 1001, 1720, 239))
        names = {p["name"]: p for p in data["primers"]}
        self.assertEqual((names[self.forward]["start"], names[self.forward]["direction"]), (901, 1))
        self.assertEqual((names[self.reverse]["end"], names[self.reverse]["direction"]), (1820, -1))

    def test_a_picked_stretch_checked_as_a_primer_and_saved(self):
        j = self.m.get(f"/plasmids/{self.row}/primer-check?start=1100&end=1119&direction=1").get_json()
        self.assertTrue(j["ok"])
        self.assertEqual(j["sequence"], PLASMID[1099:1119])
        self.assertTrue(j["name"].startswith("EGFP-"))
        self.assertTrue(j["name"].endswith("-F"))
        rev = self.m.get(f"/plasmids/{self.row}/primer-check?start=1100&end=1119&direction=-1").get_json()
        self.assertEqual(rev["sequence"], reverse_complement(PLASMID[1099:1119]))
        # The lab has it already: said.
        same = self.m.get(f"/plasmids/{self.row}/primer-check?start=901&end=920&direction=1").get_json()
        self.assertEqual(same["same"][0]["name"], self.forward)
        saved = self.m.post(f"/plasmids/{self.row}/primers/new",
                            data={"name": uniq("EGFP-mid-F-"), "sequence": j["sequence"], "direction": "1"}).get_json()
        self.assertTrue(saved["ok"], saved)
        self.assertTrue(saved["made"])
        attrs = json.loads(one("select attrs from inventory_items where id=?", saved["id"]))
        self.assertEqual(attrs["template"], str(self.number))

    def test_too_short_is_not_a_primer(self):
        r = self.m.get(f"/plasmids/{self.row}/primer-check?start=1100&end=1105&direction=1")
        self.assertEqual(r.status_code, 400)

    def test_pcr_from_two_lab_primers(self):
        j = self.m.get(f"/plasmids/{self.row}/pcr.json?f={self.f_id}&r={self.r_id}").get_json()
        self.assertTrue(j["ok"], j)
        self.assertEqual((j["start"], j["end"], j["length"]), (901, 1820, 920))
        self.assertIn("EGFP", j["holds"])
        bad = self.m.get(f"/plasmids/{self.row}/pcr.json?f={self.r_id}&r={self.f_id}")
        self.assertEqual(bad.status_code, 400)

    def test_the_list_draws_small_maps(self):
        page = self.get_ok(self.m, "/plasmids")
        self.assertIn("data-mini=", page)
        self.assertIn("plasmid-map.js", page)


if __name__ == "__main__":
    unittest.main()
