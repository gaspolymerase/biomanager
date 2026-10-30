"""Virtual digests (app/digest.py) and the plasmid page's Digest tab
(app/digest_routes.py): where enzymes cut, what pieces they leave, how they
run on a gel, methylation, and the check it suggests."""
from __future__ import annotations

import random
import unittest

from tests.base import AppTestCase, app, one, uniq

from app import digest


def filler(n: int, seed: int = 7) -> str:
    """Bases with none of the sites the tests count."""
    rnd = random.Random(seed)
    text = "".join(rnd.choice("ACGT") for _ in range(n))
    for site in ("GAATTC", "GCGGCCGC", "TCTAGA", "CTCGAG", "GGATCC", "AAGCTT", "GATC", "CCAGG", "CCTGG"):
        text = text.replace(site, site[:2] + ("A" if site[2] != "A" else "C") + site[3:])
    return text


INSERT = filler(700, seed=3)
# EcoRI at 1,001 (cuts after G, base 1,001), NotI after the insert, then
# an XbaI site Dam blocks (TCTAGATC); 5,000 bp in all.
PLASMID = filler(1000) + "GAATTC" + INSERT + "GCGGCCGC" + "TCTAGATC"
PLASMID += filler(5000 - len(PLASMID), seed=11)
BACKBONE = filler(1000) + "GAATTC" + "GCGGCCGC" + "TCTAGATC"
BACKBONE += filler(5000 - 700 - len(BACKBONE), seed=11)


class Cutting(unittest.TestCase):
    def test_a_cut_is_the_base_it_follows(self):
        cuts = digest.cut_positions(PLASMID, True, ["EcoRI"])
        self.assertEqual(cuts, [(1001, "EcoRI")])        # G^AATTC: after the G at 1,001

    def test_two_enzymes_leave_two_pieces_that_add_up(self):
        cuts = digest.cut_positions(PLASMID, True, ["EcoRI", "NotI"])
        pieces = digest.pieces(len(PLASMID), True, cuts)
        self.assertEqual(len(pieces), 2)
        self.assertEqual(sum(p.length for p in pieces), len(PLASMID))
        self.assertEqual(pieces[0].length, 5 + 700 + 2)   # AATTC…GC, EcoRI to NotI
        self.assertEqual((pieces[0].left, pieces[0].right), ("EcoRI", "NotI"))

    def test_one_cut_in_a_circle_is_one_piece_its_length(self):
        pieces = digest.pieces(len(PLASMID), True, digest.cut_positions(PLASMID, True, ["EcoRI"]))
        self.assertEqual([p.length for p in pieces], [len(PLASMID)])

    def test_a_linear_molecule_keeps_its_ends(self):
        pieces = digest.pieces(len(PLASMID), False, digest.cut_positions(PLASMID, False, ["EcoRI"]))
        self.assertEqual([p.length for p in pieces], [1001, len(PLASMID) - 1001])

    def test_a_site_across_the_origin_is_found(self):
        seq = "ATTC" + filler(992) + "GA"                  # GA|ATTC across the join
        self.assertEqual(len(digest.cut_positions(seq, True, ["EcoRI"])), 1)
        self.assertEqual(digest.cut_positions(seq, False, ["EcoRI"]), [])

    def test_a_piece_holds_the_features_wholly_inside_it(self):
        features = [{"name": "Insert", "start": 1006, "end": 1705}, {"name": "Across", "start": 900, "end": 1100}]
        pieces = digest.pieces(len(PLASMID), True, digest.cut_positions(PLASMID, True, ["EcoRI", "NotI"]), features)
        self.assertEqual(pieces[0].holds, ["Insert"])


class Methylation(unittest.TestCase):
    def test_dam_blocks_an_xbai_site_next_to_gatc(self):
        rows = {r["name"]: r for r in digest.enzyme_table(PLASMID, True)}
        self.assertEqual(rows["XbaI"]["blocked"], ["dam"])
        self.assertEqual(rows["XbaI"]["cuts"], 0)
        self.assertEqual(digest.cut_positions(PLASMID, True, ["XbaI"]), [])

    def test_from_a_dam_minus_strain_it_cuts(self):
        rows = {r["name"]: r for r in digest.enzyme_table(PLASMID, True, methylated=False)}
        self.assertEqual(rows["XbaI"]["cuts"], 1)
        self.assertEqual(len(digest.cut_positions(PLASMID, True, ["XbaI"], methylated=False)), 1)

    def test_an_xbai_site_away_from_gatc_cuts(self):
        seq = filler(500) + "TCTAGA" + filler(500, seed=2)
        self.assertEqual(len(digest.cut_positions(seq, True, ["XbaI"])), 1)


class TheGel(unittest.TestCase):
    def test_bigger_runs_higher(self):
        self.assertLess(digest.position(5000), digest.position(1000))
        self.assertLess(digest.position(1000), digest.position(300))

    def test_close_pieces_run_as_one_band(self):
        self.assertEqual(digest.too_close([3000, 2950, 800]), [[3000, 2950]])
        self.assertEqual(len(digest.bands([3000, 2950, 800])), 2)

    def test_a_heavier_band_is_brighter(self):
        bands = digest.bands([4000, 500])
        self.assertGreater(bands[0]["mass"], bands[1]["mass"])

    def test_more_agarose_separates_small_pieces(self):
        self.assertTrue(digest.too_close([300, 280], "0.7"))
        self.assertFalse(digest.too_close([300, 280], "2"))

    def test_an_uncut_plasmid_runs_supercoiled_and_nicked(self):
        forms = [b["form"] for b in digest.uncut(5000, True)]
        self.assertEqual(forms, ["supercoiled", "nicked"])


class Suggesting(unittest.TestCase):
    def test_it_suggests_the_pair_that_tells_the_clone_from_its_backbone(self):
        suggestions = digest.suggest(PLASMID, True, freezer=["EcoRI", "NotI"], parent=BACKBONE)
        self.assertTrue(suggestions)
        self.assertEqual(suggestions[0]["enzymes"], ["EcoRI", "NotI"])
        self.assertTrue(suggestions[0]["differs"])

    def test_every_suggestion_is_well_apart_on_the_gel(self):
        for s in digest.suggest(PLASMID, True):
            self.assertEqual(digest.too_close(s["sizes"]), [])

    def test_the_freezer_reads_supplier_names(self):
        found = digest.in_freezer(["EcoRI", "EcoRV", "NotI"], ["EcoRI-HF (NEB)", "NotI-HF", "Taq polymerase"])
        self.assertEqual(found, {"EcoRI": "EcoRI-HF (NEB)", "NotI": "NotI-HF"})


def genbank(name: str, sequence: str) -> str:
    lines = [f"LOCUS       {name[:16]} {len(sequence)} bp    DNA     circular SYN 01-JAN-2026", "ORIGIN"]
    for i in range(0, len(sequence), 60):
        lines.append(f"{i + 1:>9} {sequence[i:i + 60].lower()}")
    return "\n".join([*lines, "//"])


class DigestTab(AppTestCase):
    def setUp(self):
        self.name = uniq("pDig-")
        self.row = self.make_plasmid(self.m, name=self.name)
        self.post(self.m, f"/plasmids/{self.row}/upload-sequence", {"sequence_text": genbank(self.name, PLASMID)})

    def digest(self, client, query=""):
        r = client.get(f"/plasmids/{self.row}/digest.json{query}")
        self.assertEqual(r.status_code, 200, r.data[:300])
        return r.get_json()

    def test_the_tab_is_on_the_page(self):
        page = self.get_ok(self.m, f"/plasmid/{one('select plasmid_id from plasmids where id=?', self.row)}")
        self.assertIn('data-tab="digest"', page)
        self.assertIn("data-digest", page)

    def test_the_chosen_enzymes_pieces_and_lanes(self):
        data = self.digest(self.m, "?enzymes=EcoRI,NotI&lane=pcr:951:Clone%204")
        self.assertEqual(data["chosen"], ["EcoRI", "NotI"])
        self.assertEqual(sorted(p["length"] for p in data["pieces"]), [707, len(PLASMID) - 707])
        kinds = [lane["kind"] for lane in data["gel"]["lanes"]]
        self.assertEqual(kinds, ["uncut", "digest", "pcr"])
        self.assertEqual(data["gel"]["lanes"][2]["label"], "Clone 4")

    def test_a_blocked_site_is_said(self):
        data = self.digest(self.m, "?enzymes=XbaI")
        self.assertEqual(data["pieces"], [])
        self.assertTrue(any(n["label"] == "XbaI" and "Dam" in n["text"] for n in data["notes"]))
        self.assertEqual(len(self.digest(self.m, "?enzymes=XbaI&methylated=0")["cuts"]), 1)

    def test_the_freezer_is_the_labs_reagents(self):
        enzyme = uniq("EcoRI-HF lot ")
        self.make_item(self.a, "reagents", name=enzyme, category="enzyme", status="in stock", is_shared="1")
        rows = {r["name"]: r for r in self.digest(self.m)["enzymes"]}
        self.assertIsNotNone(rows["EcoRI"]["freezer"])
        self.assertIsNone(rows["SalI"]["freezer"])

    def test_the_backbone_makes_the_suggestion(self):
        backbone = self.make_plasmid(self.m, name=uniq("pBack-"))
        self.post(self.m, f"/plasmids/{backbone}/upload-sequence", {"sequence_text": genbank("pBack", BACKBONE)})
        number = one("select plasmid_id from plasmids where id=?", backbone)
        self.post(self.m, f"/plasmids/{self.row}/parents", {"parent": str(number), "role": "backbone"})
        data = self.digest(self.m)
        self.assertTrue(data["backbone"])
        self.assertIn("backbone", data["suggestions"][0]["text"])

    def test_no_sequence_no_digest(self):
        empty = self.make_plasmid(self.m)
        self.assertEqual(self.m.get(f"/plasmids/{empty}/digest.json").status_code, 400)

    def test_signed_out_is_turned_away(self):
        self.assertEqual(app.test_client().get(f"/plasmids/{self.row}/digest.json").status_code, 401)


if __name__ == "__main__":
    unittest.main()
