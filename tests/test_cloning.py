"""The assembly wizard: cutting plasmids up (app/cloning.py), the three ways
of putting the pieces together, and the pages and writes behind them
(app/cloning_routes.py).

The restriction tests run on pUC19 itself, so the numbers are the ones a
catalogue prints rather than ones this code worked out for itself.
"""
from __future__ import annotations

import json
import random
import unittest

from tests.base import AppTestCase, count, one, rows, uniq

from app import cloning
from app.db import SessionLocal
from app.models import PlasmidRecord
from app.primer_records import reverse_complement as rc

# pUC19 (GenBank L09137.2, 2686 bp). Its numbers are the ones every
# catalogue prints: EcoRI at 396, BamHI at 417, PstI at 435, HindIII at 447,
# one ScaI site in AmpR, and no EcoRV site at all.
PUC19 = (
    "TCGCGCGTTTCGGTGATGACGGTGAAAACCTCTGACACATGCAGCTCCCGGAGACGGTCACAGCTTGTCT"
    "GTAAGCGGATGCCGGGAGCAGACAAGCCCGTCAGGGCGCGTCAGCGGGTGTTGGCGGGTGTCGGGGCTGG"
    "CTTAACTATGCGGCATCAGAGCAGATTGTACTGAGAGTGCACCATATGCGGTGTGAAATACCGCACAGAT"
    "GCGTAAGGAGAAAATACCGCATCAGGCGCCATTCGCCATTCAGGCTGCGCAACTGTTGGGAAGGGCGATC"
    "GGTGCGGGCCTCTTCGCTATTACGCCAGCTGGCGAAAGGGGGATGTGCTGCAAGGCGATTAAGTTGGGTA"
    "ACGCCAGGGTTTTCCCAGTCACGACGTTGTAAAACGACGGCCAGTGAATTCGAGCTCGGTACCCGGGGAT"
    "CCTCTAGAGTCGACCTGCAGGCATGCAAGCTTGGCGTAATCATGGTCATAGCTGTTTCCTGTGTGAAATT"
    "GTTATCCGCTCACAATTCCACACAACATACGAGCCGGAAGCATAAAGTGTAAAGCCTGGGGTGCCTAATG"
    "AGTGAGCTAACTCACATTAATTGCGTTGCGCTCACTGCCCGCTTTCCAGTCGGGAAACCTGTCGTGCCAG"
    "CTGCATTAATGAATCGGCCAACGCGCGGGGAGAGGCGGTTTGCGTATTGGGCGCTCTTCCGCTTCCTCGC"
    "TCACTGACTCGCTGCGCTCGGTCGTTCGGCTGCGGCGAGCGGTATCAGCTCACTCAAAGGCGGTAATACG"
    "GTTATCCACAGAATCAGGGGATAACGCAGGAAAGAACATGTGAGCAAAAGGCCAGCAAAAGGCCAGGAAC"
    "CGTAAAAAGGCCGCGTTGCTGGCGTTTTTCCATAGGCTCCGCCCCCCTGACGAGCATCACAAAAATCGAC"
    "GCTCAAGTCAGAGGTGGCGAAACCCGACAGGACTATAAAGATACCAGGCGTTTCCCCCTGGAAGCTCCCT"
    "CGTGCGCTCTCCTGTTCCGACCCTGCCGCTTACCGGATACCTGTCCGCCTTTCTCCCTTCGGGAAGCGTG"
    "GCGCTTTCTCATAGCTCACGCTGTAGGTATCTCAGTTCGGTGTAGGTCGTTCGCTCCAAGCTGGGCTGTG"
    "TGCACGAACCCCCCGTTCAGCCCGACCGCTGCGCCTTATCCGGTAACTATCGTCTTGAGTCCAACCCGGT"
    "AAGACACGACTTATCGCCACTGGCAGCAGCCACTGGTAACAGGATTAGCAGAGCGAGGTATGTAGGCGGT"
    "GCTACAGAGTTCTTGAAGTGGTGGCCTAACTACGGCTACACTAGAAGAACAGTATTTGGTATCTGCGCTC"
    "TGCTGAAGCCAGTTACCTTCGGAAAAAGAGTTGGTAGCTCTTGATCCGGCAAACAAACCACCGCTGGTAG"
    "CGGTGGTTTTTTTGTTTGCAAGCAGCAGATTACGCGCAGAAAAAAAGGATCTCAAGAAGATCCTTTGATC"
    "TTTTCTACGGGGTCTGACGCTCAGTGGAACGAAAACTCACGTTAAGGGATTTTGGTCATGAGATTATCAA"
    "AAAGGATCTTCACCTAGATCCTTTTAAATTAAAAATGAAGTTTTAAATCAATCTAAAGTATATATGAGTA"
    "AACTTGGTCTGACAGTTACCAATGCTTAATCAGTGAGGCACCTATCTCAGCGATCTGTCTATTTCGTTCA"
    "TCCATAGTTGCCTGACTCCCCGTCGTGTAGATAACTACGATACGGGAGGGCTTACCATCTGGCCCCAGTG"
    "CTGCAATGATACCGCGAGACCCACGCTCACCGGCTCCAGATTTATCAGCAATAAACCAGCCAGCCGGAAG"
    "GGCCGAGCGCAGAAGTGGTCCTGCAACTTTATCCGCCTCCATCCAGTCTATTAATTGTTGCCGGGAAGCT"
    "AGAGTAAGTAGTTCGCCAGTTAATAGTTTGCGCAACGTTGTTGCCATTGCTACAGGCATCGTGGTGTCAC"
    "GCTCGTCGTTTGGTATGGCTTCATTCAGCTCCGGTTCCCAACGATCAAGGCGAGTTACATGATCCCCCAT"
    "GTTGTGCAAAAAAGCGGTTAGCTCCTTCGGTCCTCCGATCGTTGTCAGAAGTAAGTTGGCCGCAGTGTTA"
    "TCACTCATGGTTATGGCAGCACTGCATAATTCTCTTACTGTCATGCCATCCGTAAGATGCTTTTCTGTGA"
    "CTGGTGAGTACTCAACCAAGTCATTCTGAGAATAGTGTATGCGGCGACCGAGTTGCTCTTGCCCGGCGTC"
    "AATACGGGATAATACCGCGCCACATAGCAGAACTTTAAAAGTGCTCATCATTGGAAAACGTTCTTCGGGG"
    "CGAAAACTCTCAAGGATCTTACCGCTGTTGAGATCCAGTTCGATGTAACCCACTCGTGCACCCAACTGAT"
    "CTTCAGCATCTTTTACTTTCACCAGCGTTTCTGGGTGAGCAAAAACAGGAAGGCAAAATGCCGCAAAAAA"
    "GGGAATAAGGGCGACACGGAAATGTTGAATACTCATACTCTTCCTTTTTCAATATTATTGAAGCATTTAT"
    "CAGGGTTATTGTCTCATGAGCGGATACATATTTGAATGTATTTAGAAAAATAAACAAATAGGGGTTCCGC"
    "GCACATTTCCCCGAAAAGTGCCACCTGACGTCTAAGAAACCATTATTATCATGACATTAACCTATAAAAA"
    "TAGGCGTATCACGAGGCCCTTTCGTC"
)


def bases(n: int, seed) -> str:
    rng = random.Random(f"{seed}")
    return "".join(rng.choice("ACGT") for _ in range(n))


def rotate(sequence: str, by: int) -> str:
    return sequence[by:] + sequence[:by]


def sizes(fragments) -> list[int]:
    return sorted(len(f) for f in fragments)


# ---------------------------------------------------------------- the enzymes


class TheEnzymeTable(unittest.TestCase):

    def test_each_one_is_written_as_a_catalogue_writes_it(self):
        self.assertEqual(cloning.ENZYMES["EcoRI"].label, "EcoRI G^AATTC")
        self.assertEqual(cloning.ENZYMES["PstI"].label, "PstI CTGCA^G")
        self.assertEqual(cloning.ENZYMES["SmaI"].label, "SmaI CCC^GGG")
        self.assertEqual(cloning.ENZYMES["BsaI"].label, "BsaI GGTCTC(1/5)")

    def test_the_overhang_says_which_end_it_leaves(self):
        self.assertEqual(cloning.ENZYMES["EcoRI"].overhang, 4)      # 5′ AATT
        self.assertEqual(cloning.ENZYMES["PstI"].overhang, -4)      # 3′ TGCA
        self.assertEqual(cloning.ENZYMES["SmaI"].overhang, 0)       # blunt
        self.assertEqual(cloning.ENZYMES["SapI"].overhang, 3)

    def test_a_type_iis_enzyme_cuts_outside_its_own_site(self):
        self.assertTrue(all(cloning.ENZYMES[name].outside for name in cloning.GOLDEN_GATE_ENZYMES))
        self.assertFalse(cloning.ENZYMES["EcoRI"].outside)


# ---------------------------------------------------------------- digesting


class DigestingPuc19(unittest.TestCase):

    def test_the_polylinker_comes_out_as_the_51_bp_everyone_knows(self):
        pieces = cloning.digest(PUC19, True, ["EcoRI", "HindIII"])
        self.assertEqual(sizes(pieces), [51, 2635])
        self.assertEqual(sum(len(f) for f in pieces), len(PUC19))
        polylinker, backbone = pieces
        self.assertEqual((polylinker.left.kind, polylinker.left.bases), ("5", "AATT"))
        self.assertEqual((polylinker.right.kind, polylinker.right.bases), ("5", "AGCT"))
        # The piece starts where EcoRI cuts, after the G of GAATTC at 396.
        self.assertTrue(polylinker.sequence.startswith("AATTC"))
        self.assertEqual(backbone.sequence, rotate(PUC19, 447 - 1 + 1)[:2635])

    def test_the_two_pieces_go_back_together_as_the_plasmid_they_came_from(self):
        pieces = cloning.digest(PUC19, True, ["EcoRI", "HindIII"])
        made = cloning.ligate(pieces, True)
        self.assertTrue(made["ok"], made["problem"])
        self.assertEqual(made["sequence"], rotate(PUC19, 396))
        self.assertEqual([j["bases"] for j in made["junctions"]], ["AGCT", "AATT"])

    def test_a_3_prime_overhang_is_read_and_rejoined_the_same_way(self):
        pieces = cloning.digest(PUC19, True, ["PstI", "SphI"])
        self.assertEqual(sizes(pieces), [6, 2680])
        self.assertEqual({f.left.kind for f in pieces}, {"3"})
        self.assertEqual(cloning.ligate(pieces, True)["sequence"], rotate(PUC19, 439))

    def test_one_cut_opens_the_circle_and_keeps_every_base(self):
        pieces = cloning.digest(PUC19, True, ["EcoRI"])
        self.assertEqual(len(pieces), 1)
        self.assertEqual(len(pieces[0]), len(PUC19))
        self.assertEqual(pieces[0].sequence, rotate(PUC19, 396))
        self.assertTrue(cloning.ligate(pieces, True)["ok"])      # it closes on itself again

    def test_an_enzyme_with_no_site_leaves_the_plasmid_alone(self):
        self.assertEqual(cloning.digest(PUC19, True, ["EcoRV"]), [])
        self.assertEqual(len(cloning.digest(PUC19, True, ["ScaI"])), 1)   # one site, in AmpR

    def test_a_site_across_the_origin_is_still_a_site(self):
        # Put the middle of the EcoRI site on the join.
        turned = rotate(PUC19, 398)
        self.assertEqual(sizes(cloning.digest(turned, True, ["EcoRI", "HindIII"])), [51, 2635])
        # Linear, the same sequence has no EcoRI site left to find.
        self.assertEqual(sizes(cloning.digest(turned, False, ["EcoRI", "HindIII"])), [49, 2637])

    def test_a_linear_plasmid_keeps_the_blunt_ends_it_came_with(self):
        pieces = cloning.digest(PUC19, False, ["EcoRI", "HindIII"])
        self.assertEqual(sizes(pieces), [51, 396, 2239])
        self.assertEqual(pieces[0].left, cloning.BLUNT)
        self.assertEqual(pieces[-1].right, cloning.BLUNT)


class FeaturesOnAPiece(unittest.TestCase):
    """A fragment carries the annotations of the plasmid it came off, at the
    places they are now."""

    def features(self, marks, start, length, circular=True, parent=None):
        return cloning.carry_features(marks, len(parent or PUC19), circular, start, length)

    def test_one_inside_the_piece_moves_with_it(self):
        ampr = {"name": "AmpR", "start": 1625, "end": 2485, "direction": -1}
        moved = self.features([ampr], 447, 2635)
        self.assertEqual((moved[0]["name"], moved[0]["start"], moved[0]["end"]), ("AmpR", 1178, 2038))
        self.assertEqual(moved[0]["direction"], -1)

    def test_one_outside_it_is_left_behind(self):
        # The polylinker's own features are not on the backbone fragment.
        self.assertEqual(self.features([{"name": "MCS", "start": 400, "end": 440}], 447, 2635), [])

    def test_one_the_cut_runs_through_keeps_the_part_that_is_there_and_says_so(self):
        cut = self.features([{"name": "lacZalpha", "start": 390, "end": 460}], 396, 51)
        self.assertEqual((cut[0]["start"], cut[0]["end"]), (0, 50))
        self.assertEqual(cut[0]["notes"], cloning.PARTIAL)
        # A feature that already had qualifiers keeps them, with one more.
        kept = self.features([{"name": "lacZ\u03b1", "start": 390, "end": 460,
                               "notes": {"gene": ["lacZ"]}}], 396, 51)
        self.assertEqual(kept[0]["notes"], {"gene": ["lacZ"], "note": [cloning.PARTIAL]})

    def test_one_across_the_origin_lands_in_one_piece(self):
        wrap = {"name": "ori-spanning", "start": 2680, "end": 10}
        moved = self.features([wrap], 2670, 60)
        # 2680–10 is 17 bases round the join; 10 of them in, 7 round the corner.
        self.assertEqual((moved[0]["start"], moved[0]["end"]), (10, 26))
        self.assertFalse(moved[0].get("notes"))


class TurningAFragmentRound(unittest.TestCase):

    def setUp(self):
        self.polylinker, self.backbone = cloning.digest(PUC19, True, ["EcoRI", "HindIII"])

    def test_twice_round_is_where_it_started(self):
        there_and_back = cloning.flip(cloning.flip(self.polylinker))
        self.assertEqual(there_and_back.sequence, self.polylinker.sequence)
        self.assertEqual(there_and_back.left, self.polylinker.left)
        self.assertEqual(there_and_back.right, self.polylinker.right)

    def test_the_ends_swap_and_keep_the_kind_they_were(self):
        turned = cloning.flip(self.polylinker)
        self.assertEqual((turned.left.kind, turned.left.bases), ("5", "AGCT"))
        self.assertEqual((turned.right.kind, turned.right.bases), ("5", "AATT"))

    def test_the_other_way_round_no_longer_fits_the_backbone(self):
        self.assertTrue(cloning.ligate([self.backbone, self.polylinker], True)["ok"])
        wrong = cloning.ligate([self.backbone, cloning.flip(self.polylinker)], True)
        self.assertFalse(wrong["ok"])
        self.assertEqual(wrong["problem"]["kind"], "ends")
        self.assertEqual((wrong["problem"]["at"], wrong["problem"]["next"]), (1, 2))

    def test_its_features_turn_with_it(self):
        piece = cloning.region(PUC19, True, 400, 100, [{"name": "x", "start": 410, "end": 419, "direction": 1}])
        turned = cloning.flip(piece)
        self.assertEqual(turned.sequence, rc(piece.sequence))
        self.assertEqual((turned.features[0]["start"], turned.features[0]["end"]), (80, 89))
        self.assertEqual(turned.features[0]["direction"], -1)


# ---------------------------------------------------------------- Gibson


class Overlaps(unittest.TestCase):
    """Gibson: three pieces of one circle, each overlapping the next."""

    CIRCLE = bases(3000, "gibson")
    CUTS = (0, 1100, 2000)

    def pieces(self, overlap: int):
        made = []
        for i, start in enumerate(self.CUTS):
            end = self.CUTS[(i + 1) % len(self.CUTS)]
            length = (end - start) % len(self.CIRCLE) + overlap
            made.append(cloning.region(self.CIRCLE, True, start, length, name=f"f{i + 1}"))
        return made

    def test_pieces_that_already_overlap_make_the_circle_back(self):
        made = cloning.gibson(self.pieces(25), True)
        self.assertTrue(made["ok"], made["problem"])
        self.assertEqual(made["sequence"], self.CIRCLE)
        self.assertEqual([j["length"] for j in made["junctions"]], [25, 25, 25])
        self.assertTrue(all(j["tm"] for j in made["junctions"]))

    def test_an_overlap_too_short_to_mean_anything_is_not_one(self):
        made = cloning.gibson(self.pieces(8), True)
        self.assertFalse(made["ok"])
        self.assertEqual(made["problem"]["kind"], "no_overlap")

    def test_without_homology_it_names_the_end_that_has_none(self):
        made = cloning.gibson(self.pieces(0), True)
        self.assertEqual(made["problem"], {"kind": "no_overlap", "at": 1, "next": 2})

    def test_with_primers_designed_the_same_pieces_assemble_and_cost_no_bases(self):
        plain = self.pieces(0)
        made = cloning.gibson(plain, True, design=True)
        self.assertTrue(made["ok"])
        self.assertEqual(made["sequence"], self.CIRCLE)
        self.assertTrue(all(j["designed"] for j in made["junctions"]))

    def test_each_designed_primer_sits_across_the_join_it_has_to_close(self):
        plain = self.pieces(0)
        primers = cloning.design_gibson_primers(plain, True)
        self.assertEqual(len(primers), 6)
        self.assertEqual([p["name"] for p in primers[:2]], ["f1 F", "f1 R"])
        for primer in primers:
            self.assertTrue(55 <= primer["tm"] <= 68, primer)
            self.assertEqual(len(primer["tail"]), cloning.ARM)
            # Read along the circle, the whole primer is there: tail then anneal.
            doubled = self.CIRCLE + self.CIRCLE
            wanted = primer["sequence"] if primer["direction"] == "forward" else rc(primer["sequence"])
            self.assertIn(wanted, doubled, primer["name"])

    def test_a_piece_straight_out_of_a_digest_gets_no_primers_and_no_arm(self):
        plain = self.pieces(0)
        self.assertEqual([p["fragment"] for p in cloning.design_gibson_primers(plain, True, amplify={1, 2})],
                         [2, 2, 3, 3])
        # With only one of a junction's two sides amplified, the arm still fits…
        self.assertTrue(cloning.gibson(plain, True, design=True, amplify={1, 2})["ok"])
        # …but a junction between two cut pieces has nowhere to put one.
        refused = cloning.gibson(plain, True, design=True, amplify={1})
        self.assertEqual(refused["problem"], {"kind": "no_overlap", "at": 3, "next": 1})

    def test_the_same_overlap_twice_is_refused(self):
        same = bases(30, "same")
        made = cloning.gibson([cloning.region(same + bases(500, 1) + same, False, 0, 560, name="a"),
                               cloning.region(same + bases(400, 2) + same, False, 0, 460, name="b")], True)
        self.assertEqual(made["problem"]["kind"], "repeated")
        self.assertEqual(made["problem"]["bases"], same)

    def test_two_fragments_are_the_fewest_it_takes(self):
        self.assertEqual(cloning.gibson([self.pieces(25)[0]], True)["problem"], {"kind": "too_few", "least": 2})


# ---------------------------------------------------------------- Golden Gate


def clean_bases(n: int, seed, enzyme: str) -> str:
    """Filler with no site of its own: random DNA carries a six-base word
    about once in two thousand bases, which would cut the part in half."""
    site = cloning.ENZYMES[enzyme].site
    for attempt in range(50):
        made = bases(n, f"{seed}-{attempt}")
        if site not in made and rc(site) not in made:
            return made
    raise AssertionError("no clean filler")


def part_plasmid(core: str, left: str, right: str, enzyme: str = "BsaI", seed=0) -> str:
    """A part plasmid: its two Type IIS sites point outwards, so the part
    comes out carrying the overhangs and the sites stay behind. Each site
    sits its own spacer away from the overhang it chooses (BsaI one base,
    BbsI two)."""
    cutter = cloning.ENZYMES[enzyme]
    spacer = cutter.top - len(cutter.site)
    return (clean_bases(80, f"{seed}a", enzyme) + cutter.site + "A" * spacer + left + core + right
            + "T" * spacer + rc(cutter.site) + clean_bases(80, f"{seed}b", enzyme))


def part(core_length: int, left: str, right: str, enzyme: str = "BsaI", seed=0) -> cloning.Fragment:
    plasmid = part_plasmid(clean_bases(core_length, f"{seed}{left}{right}", enzyme), left, right, enzyme, seed)
    freed = cloning.released(cloning.digest(plasmid, True, [enzyme]), enzyme)
    assert len(freed) == 1, freed
    freed[0].name = f"part {left}-{right}"
    return freed[0]


class GoldenGate(unittest.TestCase):

    def setUp(self):
        self.promoter = part(200, "AATG", "GCTT")
        self.gene = part(150, "GCTT", "CGCT")
        self.backbone = part(1200, "CGCT", "AATG")

    def test_the_part_leaves_its_sites_behind_and_keeps_its_overhangs(self):
        # A 5′ overhang is written on the piece that follows it, so a part
        # carries its own left overhang and its core, and offers the right one.
        self.assertEqual(len(self.promoter), 200 + 4)
        self.assertEqual((self.promoter.left.kind, self.promoter.left.bases), ("5", "AATG"))
        self.assertEqual(self.promoter.right.bases, "GCTT")

    def test_three_parts_and_a_backbone_close_into_one_plasmid(self):
        made = cloning.golden_gate([self.promoter, self.gene, self.backbone], True)
        self.assertTrue(made["ok"], made["problem"])
        self.assertEqual(made["length"], len(self.promoter) + len(self.gene) + len(self.backbone))
        self.assertEqual([j["bases"] for j in made["junctions"]], ["GCTT", "CGCT", "AATG"])
        # Nothing the enzyme can cut is left, which is why it runs in one tube.
        self.assertNotIn(cloning.ENZYMES["BsaI"].site, made["sequence"])
        self.assertNotIn(rc(cloning.ENZYMES["BsaI"].site), made["sequence"])

    def test_the_same_overhang_at_two_junctions_is_refused(self):
        twice = part(100, "GCTT", "GCTT")
        made = cloning.golden_gate([self.promoter, twice, self.gene, self.backbone], True)
        self.assertEqual(made["problem"]["kind"], "repeated")
        self.assertEqual(made["problem"]["bases"], "GCTT")
        self.assertEqual((made["problem"]["at"], made["problem"]["next"]), (1, 2))

    def test_an_overhang_that_reads_the_same_on_both_strands_is_refused(self):
        self.assertTrue(cloning.is_palindrome("AATT"))
        made = cloning.golden_gate([part(100, "AATT", "GCTT"), self.gene, part(300, "CGCT", "AATT")], True)
        self.assertEqual(made["problem"]["kind"], "palindrome")
        self.assertEqual(made["problem"]["bases"], "AATT")

    def test_an_overhang_that_is_another_one_backwards_is_refused(self):
        # AGCT at one junction and its reverse complement at another: a part
        # could go in either way round.
        made = cloning.golden_gate([part(100, "AATG", "ACCA"), part(100, "ACCA", "TGGT"),
                                    part(100, "TGGT", "AATG")], True)
        self.assertEqual(made["problem"]["kind"], "mirrored")

    def test_ends_that_do_not_meet_are_named(self):
        made = cloning.golden_gate([self.promoter, self.backbone], True)
        self.assertEqual(made["problem"]["kind"], "ends")
        self.assertEqual((made["problem"]["at"], made["problem"]["next"]), (1, 2))

    def test_a_blunt_piece_has_no_overhang_to_offer(self):
        blunt = cloning.region(bases(300, "blunt"), False, 0, 300, name="blunt")
        made = cloning.golden_gate([self.promoter, blunt, self.backbone], True)
        self.assertIn(made["problem"]["kind"], ("ends", "blunt"))

    def test_bsmbi_and_bbsi_work_the_same_way(self):
        for enzyme in ("BsmBI", "BbsI", "SapI"):
            with self.subTest(enzyme=enzyme):
                size = len(cloning.ENZYMES[enzyme].site)
                left, right = ("AATG", "GCTT") if enzyme != "SapI" else ("ATG", "GCT")
                a = part(100, left, right, enzyme, seed=size)
                b = part(400, right, left, enzyme, seed=size + 1)
                made = cloning.golden_gate([a, b], True, enzyme=enzyme)
                self.assertTrue(made["ok"], made["problem"])


# ---------------------------------------------------------------- the wizard


def genbank(name: str, sequence: str, circular: bool = True, feats=()) -> str:
    """A GenBank file, as the wizard's sources arrive in the lab."""
    lines = [f"LOCUS       {name[:16]} {len(sequence)} bp    DNA     "
             f"{'circular' if circular else 'linear'} SYN 01-JAN-2026",
             "FEATURES             Location/Qualifiers"]
    for ftype, start, end, label in feats:
        lines += [f"     {ftype:<16}{start}..{end}", f'                     /label="{label}"']
    lines.append("ORIGIN")
    for i in range(0, len(sequence), 60):
        lines.append(f"{i + 1:>9} " + sequence[i:i + 60].lower())
    lines.append("//")
    return "\n".join(lines) + "\n"


def product(number: int) -> PlasmidRecord | None:
    with SessionLocal() as session:
        found = session.scalar(
            __import__("sqlalchemy").select(PlasmidRecord).where(PlasmidRecord.plasmid_id == number))
        if found is not None:
            session.expunge(found)
        return found


def annotations(number: int) -> list[dict]:
    return json.loads(product(number).features_json or "[]")


class TheWizardPage(AppTestCase):

    def test_each_way_of_putting_things_together_has_its_page(self):
        for method in cloning.METHODS:
            with self.subTest(method=method):
                page = self.get_ok(self.m, f"/plasmids/assembly/?method={method}")
                self.assertIn("Assemble a plasmid", page)
                self.assertIn("EcoRI G^AATTC", page)
        # An unknown method falls back rather than failing.
        self.assertIn("Assemble a plasmid", self.get_ok(self.m, "/plasmids/assembly/?method=magic"))

    def test_it_is_reached_from_the_plasmid_list_and_from_a_plasmid(self):
        row = self.make_plasmid(self.m, uniq("pEntry"), sequence_text=genbank("pEntry", PUC19))
        self.assertIn("/plasmids/assembly/", self.get_ok(self.m, "/plasmids"))
        number = one("select plasmid_id from plasmids where id=?", row)
        page = self.get_ok(self.m, f"/plasmid/{number}")
        self.assertIn(f"/plasmids/assembly/?from={number}", page)

    def test_signing_in_comes_first(self):
        self.assertEqual(self.client_out().get("/plasmids/assembly/").status_code, 302)

    @staticmethod
    def client_out():
        from tests.base import app
        return app.test_client()


class TakingAPieceOffAPlasmid(AppTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.row = cls.make_plasmid(cls.m, uniq("pUC19-"), sequence_text=genbank(
            "pUC19", PUC19, True, [("CDS", 1626, 2486, "AmpR"), ("misc_feature", 397, 447, "MCS")]))
        cls.number = one("select plasmid_id from plasmids where id=?", cls.row)

    def source(self, **args):
        query = "&".join(f"{k}={v}" for k, v in args.items())
        answer = self.m.get(f"/plasmids/assembly/source/{self.number}?{query}")
        self.assertEqual(answer.status_code, 200)
        return answer.get_json()

    def test_it_lists_what_the_map_names(self):
        found = self.source()
        self.assertEqual(found["length"], 2686)
        self.assertTrue(found["circular"])
        self.assertEqual([f["name"] for f in found["features"]], ["MCS", "AmpR"])
        self.assertEqual(found["features"][0]["length"], 51)

    def test_it_cuts_when_asked_and_says_what_each_piece_offers(self):
        pieces = self.source(enzymes="EcoRI,HindIII")["pieces"]
        self.assertEqual([p["length"] for p in pieces], [51, 2635])
        self.assertEqual(pieces[0]["left"], "AATT (5′)")
        self.assertEqual((pieces[0]["start"], pieces[0]["end"]), (397, 447))
        self.assertIn("AmpR", pieces[1]["features"])

    def test_an_enzyme_that_does_not_cut_says_so_rather_than_nothing(self):
        self.assertTrue(self.source(enzymes="EcoRV")["uncut"])

    def test_a_plasmid_with_no_sequence_is_not_a_source(self):
        empty = self.make_plasmid(self.m, uniq("pEmpty"))
        number = one("select plasmid_id from plasmids where id=?", empty)
        self.assertEqual(self.m.get(f"/plasmids/assembly/source/{number}").status_code, 404)


class TheTray(AppTestCase):
    """What the status line says while the tray is being filled."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.row = cls.make_plasmid(cls.m, uniq("pUC19-tray-"), sequence_text=genbank(
            "pUC19", PUC19, True, [("CDS", 1626, 2486, "AmpR")]))
        cls.number = one("select plasmid_id from plasmids where id=?", cls.row)

    def cut(self, piece: int, **extra):
        return {"plasmid": self.number, "kind": "digest", "enzymes": ["EcoRI", "HindIII"], "piece": piece, **extra}

    def preview(self, fragments, method="digest_ligate", **extra):
        return self.m.post("/plasmids/assembly/preview",
                           json={"method": method, "circular": True, "fragments": fragments, **extra}).get_json()

    def test_two_pieces_that_fit_are_ready_to_make(self):
        seen = self.preview([self.cut(0), self.cut(1)])
        self.assertTrue(seen["ok"])
        self.assertEqual(seen["length"], 2686)
        self.assertIn("2686 bp", seen["status"])
        self.assertEqual([j["bases"] for j in seen["junctions"]], ["AGCT", "AATT"])
        self.assertEqual([f["length"] for f in seen["fragments"]], [51, 2635])
        self.assertEqual([p["length"] for p in seen["parts"]], [51, 2635])
        self.assertIn("AmpR", [f["name"] for f in seen["features"]])

    def test_the_end_that_is_wrong_is_the_one_it_names(self):
        seen = self.preview([self.cut(0), self.cut(0)])
        self.assertFalse(seen["ok"])
        self.assertEqual(seen["status"],
                         "Fragment 1's 3′ end (AGCT (5′)) does not fit fragment 2's 5′ end (AATT (5′)).")
        self.assertEqual([j["ok"] for j in seen["junctions"]], [False, False])

    def test_turning_a_piece_round_changes_the_ends_it_offers(self):
        self.assertFalse(self.preview([self.cut(1), self.cut(0, flip=True)])["ok"])
        self.assertTrue(self.preview([self.cut(1), self.cut(0)])["ok"])

    def test_a_gibson_without_homology_names_the_end_with_none(self):
        pieces = [{"plasmid": self.number, "kind": "region", "start": 1, "end": 1000},
                  {"plasmid": self.number, "kind": "region", "start": 1001, "end": 2686}]
        seen = self.preview(pieces, method="gibson", design=False)
        self.assertEqual(seen["status"], "Fragment 1's 3′ end has no overlap with fragment 2.")
        with_primers = self.preview(pieces, method="gibson", design=True)
        self.assertTrue(with_primers["ok"])
        self.assertEqual(len(with_primers["primers"]), 4)
        self.assertEqual(with_primers["length"], 2686)

    def test_a_whole_plasmid_and_a_feature_can_go_in_the_tray(self):
        seen = self.preview([{"plasmid": self.number, "kind": "whole"},
                             {"plasmid": self.number, "kind": "feature", "feature": 0}], method="gibson")
        self.assertEqual([f["length"] for f in seen["fragments"]], [2686, 861])

    def test_a_plasmid_that_has_gone_is_said_so_rather_than_ignored(self):
        self.assertEqual(self.preview([{"plasmid": 999999, "kind": "whole"}])["status"],
                         "That plasmid is not in the lab any more.")

    def test_a_region_that_runs_backwards_on_a_linear_plasmid_is_refused(self):
        linear = self.make_plasmid(self.m, uniq("pLin"), sequence_text=genbank("pLin", PUC19[:500], False))
        number = one("select plasmid_id from plasmids where id=?", linear)
        self.assertIn("runs backwards",
                      self.preview([{"plasmid": number, "kind": "region", "start": 400, "end": 100}])["status"])

    def test_an_empty_tray_asks_for_fragments(self):
        self.assertIn("Add", self.preview([])["status"])


class MakingTheProduct(AppTestCase):

    def setUp(self):
        super().setUp()
        self.row = self.make_plasmid(self.m, uniq("pUC19-make-"), sequence_text=genbank(
            "pUC19", PUC19, True, [("CDS", 1626, 2486, "AmpR")]))
        self.number = one("select plasmid_id from plasmids where id=?", self.row)

    def cut(self, piece: int):
        return {"plasmid": self.number, "kind": "digest", "enzymes": ["EcoRI", "HindIII"], "piece": piece}

    def create(self, tray, name, method="digest_ligate", **extra):
        return self.post(self.m, "/plasmids/assembly/create",
                         {"method": method, "circular": "1", "name": name, "tray": json.dumps(tray), **extra})

    def test_it_makes_a_plasmid_with_the_sequence_the_pieces_spell(self):
        name = uniq("pRelig")
        r = self.create([self.cut(0), self.cut(1)], name)
        self.assertFlash(r, "2686 bp from 2 fragments", "success")
        made = one("select plasmid_id from plasmids where name=?", name)
        self.assertEqual(product(made).full_sequence, rotate(PUC19, 396))
        self.assertTrue(product(made).is_circular)
        self.assertEqual(product(made).owner, self.member)

    def test_the_features_come_across_and_each_fragment_is_drawn_as_a_part(self):
        name = uniq("pParts")
        self.create([self.cut(0), self.cut(1)], name)
        made = one("select plasmid_id from plasmids where name=?", name)
        marks = annotations(made)
        ampr = next(f for f in marks if f["name"] == "AmpR")
        self.assertEqual((ampr["start"], ampr["end"]), (1229, 2089))      # 1625–2485, 396 further back
        parts = [f for f in marks if f.get("kind") == "part"]
        self.assertEqual([(p["start"], p["end"]) for p in parts], [(0, 50), (51, 2685)])
        self.assertEqual(len({p["color"] for p in parts}), 2)

    def test_it_records_what_it_was_made_from_and_how(self):
        name = uniq("pLineage")
        self.create([self.cut(0), self.cut(1)], name)
        made = one("select id from plasmids where name=?", name)
        links = rows("select parent_row_id, role, method, details_json from plasmid_parents "
                     "where child_row_id=? order by position", made)
        self.assertEqual([(l[0], l[1], l[2]) for l in links],
                         [(self.row, "insert", "digest_ligate"), (self.row, "backbone", "digest_ligate")])
        details = json.loads(links[0][3])
        self.assertEqual(details["enzymes"], ["EcoRI", "HindIII"])
        self.assertEqual((details["start"], details["length"]), (397, 51))

    def test_the_sequence_it_starts_from_is_a_version_the_page_can_name(self):
        name = uniq("pVersion")
        self.create([self.cut(0), self.cut(1)], name)
        made = one("select id from plasmids where name=?", name)
        self.assertEqual(rows("select how from plasmid_sequence_versions where plasmid_row_id=?", made),
                         [("assembly",)])
        # Edit it once more and the Versions list names where it came from.
        self.post(self.m, f"/plasmids/{made}/edit-sequence", {"sequence_text": PUC19[:800]})
        number = one("select plasmid_id from plasmids where id=?", made)
        self.assertIn("Assembled from fragments", self.get_ok(self.m, f"/plasmid/{number}"))

    def test_the_longest_piece_is_the_backbone_unless_you_say_otherwise(self):
        name = uniq("pRoles")
        tray = [dict(self.cut(0), role="backbone"), self.cut(1)]
        self.create(tray, name)
        made = one("select id from plasmids where name=?", name)
        self.assertEqual([r[0] for r in rows("select role from plasmid_parents where child_row_id=? "
                                             "order by position", made)], ["backbone", "insert"])

    def test_a_tray_that_does_not_go_together_makes_nothing(self):
        name = uniq("pNope")
        before = count("plasmids")
        r = self.create([self.cut(0), self.cut(0)], name)
        self.assertFlash(r, "does not fit", "error")
        self.assertEqual(count("plasmids"), before)
        self.assertIsNone(one("select id from plasmids where name=?", name))

    def test_it_needs_a_name(self):
        self.assertFlash(self.create([self.cut(0), self.cut(1)], ""), "Give the new plasmid a name.", "error")


class ThePrimersAGibsonNeeds(AppTestCase):
    """A designed primer is a tube to order: it goes in the lab's Primers
    database against the plasmid it amplifies, and on that plasmid's map."""

    def setUp(self):
        super().setUp()
        self.row = self.make_plasmid(self.m, uniq("pUC19-gib-"), sequence_text=genbank("pUC19", PUC19))
        self.number = one("select plasmid_id from plasmids where id=?", self.row)
        self.halves = [{"plasmid": self.number, "kind": "region", "start": 1, "end": 1343},
                       {"plasmid": self.number, "kind": "region", "start": 1344, "end": 2686}]

    def primers(self, plasmid_row: int | None = None):
        where = "" if plasmid_row is None else f" and attrs like '%\"{one('select plasmid_id from plasmids where id=?', plasmid_row)}\"%'"
        return rows("select name, status, attrs from inventory_items where module_id_fk="
                    "(select id from inventory_modules where kind='primers')" + where + " order by id")

    def test_the_primers_are_saved_to_order_against_their_template(self):
        name = uniq("pGib")
        r = self.post(self.m, "/plasmids/assembly/create",
                      {"method": "gibson", "circular": "1", "design": "1", "name": name,
                       "tray": json.dumps(self.halves)})
        self.assertFlash(r, "2686 bp from 2 fragments", "success")
        self.assertFlash(r, "4 primers saved to order", "info")
        saved = self.primers(self.row)
        self.assertEqual(len(saved), 4)
        self.assertTrue(all(status == "to order" for _n, status, _a in saved))
        self.assertTrue(all(name in n for n, _s, _a in saved))
        # Each one is the arm plus what anneals, and reads on the plasmid.
        for _n, _s, attrs in saved:
            sequence = json.loads(attrs)["sequence"]
            self.assertTrue(len(sequence) > cloning.ARM)
            self.assertTrue(sequence in PUC19 + PUC19 or rc(sequence) in PUC19 + PUC19)

    def test_each_one_is_drawn_on_the_map_it_binds_to(self):
        self.post(self.m, "/plasmids/assembly/create",
                  {"method": "gibson", "circular": "1", "design": "1", "name": uniq("pDrawn"),
                   "tray": json.dumps(self.halves)})
        number = one("select plasmid_id from plasmids where id=?", self.row)
        drawn = [f for f in annotations(number) if f.get("kind") == "primer"]
        self.assertEqual(len(drawn), 4)
        self.assertTrue(all(f["inventory_id"] for f in drawn))
        self.assertTrue(all(f["type"] == "primer_bind" for f in drawn))
        # The map it was drawn on keeps the state it was in before.
        self.assertIn("primers", [r[0] for r in rows(
            "select how from plasmid_sequence_versions where plasmid_row_id=?", self.row)])
        # And the plasmid's own Primers card lists them.
        self.assertIn("Copy for ordering", self.get_ok(self.m, f"/plasmid/{number}"))

    def test_the_fragment_keeps_the_primers_that_made_it_in_made_from(self):
        name = uniq("pMadeWith")
        self.post(self.m, "/plasmids/assembly/create",
                  {"method": "gibson", "circular": "1", "design": "1", "name": name,
                   "tray": json.dumps(self.halves)})
        made = one("select id from plasmids where name=?", name)
        details = [json.loads(r[0]) for r in rows(
            "select details_json from plasmid_parents where child_row_id=? order by position", made)]
        self.assertEqual([len(d["primers"]) for d in details], [2, 2])
        self.assertEqual(sorted(i for d in details for i in d["primers"]),
                         sorted(r[0] for r in rows("select id from inventory_items where name like ?", f"{name}%")))

    def test_without_designing_them_the_pieces_must_overlap_already(self):
        name = uniq("pNoDesign")
        r = self.post(self.m, "/plasmids/assembly/create",
                      {"method": "gibson", "circular": "1", "design": "0", "name": name,
                       "tray": json.dumps(self.halves)})
        self.assertFlash(r, "has no overlap with fragment 2", "error")
        self.assertIsNone(one("select id from plasmids where name=?", name))

    def test_someone_elses_map_is_not_drawn_on_but_the_primers_are_still_ordered(self):
        theirs = self.make_plasmid(self.o, uniq("pTheirs"), sequence_text=genbank("pTheirs", PUC19), owner=self.other)
        number = one("select plasmid_id from plasmids where id=?", theirs)
        tray = [{"plasmid": number, "kind": "region", "start": 1, "end": 1343},
                {"plasmid": number, "kind": "region", "start": 1344, "end": 2686}]
        r = self.post(self.m, "/plasmids/assembly/create",
                      {"method": "gibson", "circular": "1", "design": "1", "name": uniq("pBorrowed"),
                       "tray": json.dumps(tray)})
        self.assertFlash(r, "not yours to draw them on", "warning")
        self.assertEqual(len(self.primers(theirs)), 4)
        self.assertEqual([f for f in annotations(number) if f.get("kind") == "primer"], [])


class AGoldenGateThroughThePages(AppTestCase):

    def setUp(self):
        super().setUp()
        self.parts = {}
        for label, (left, right, size) in {"promoter": ("AATG", "GCTT", 300),
                                           "gene": ("GCTT", "CGCT", 400),
                                           "backbone": ("CGCT", "AATG", 1500)}.items():
            name = uniq(f"p{label}")
            sequence = part_plasmid(clean_bases(size, name, "BsaI"), left, right, "BsaI", seed=name)
            row = self.make_plasmid(self.m, name, sequence_text=genbank(name, sequence))
            self.parts[label] = one("select plasmid_id from plasmids where id=?", row)

    def tray(self, order=("promoter", "gene", "backbone")):
        return [{"plasmid": self.parts[k], "kind": "digest", "enzymes": ["BsaI"], "piece": self.released(k)}
                for k in order]

    def released(self, label: str) -> int:
        """Which piece of that digest is the part: the one BsaI left an
        overhang at both ends of, the sites having gone with the rest."""
        pieces = self.m.get(f"/plasmids/assembly/source/{self.parts[label]}?enzymes=BsaI").get_json()["pieces"]
        return next(p["piece"] for p in pieces if p["left"] and p["right"])

    def test_three_parts_go_together_and_the_product_cannot_be_cut_again(self):
        name = uniq("pGG")
        r = self.post(self.m, "/plasmids/assembly/create",
                      {"method": "golden_gate", "circular": "1", "enzyme": "BsaI", "name": name,
                       "tray": json.dumps(self.tray())})
        self.assertFlash(r, "from 3 fragments", "success")
        made = one("select plasmid_id from plasmids where name=?", name)
        sequence = product(made).full_sequence
        self.assertNotIn("GGTCTC", sequence)
        self.assertNotIn("GAGACC", sequence)
        self.assertEqual(len([f for f in annotations(made) if f.get("kind") == "part"]), 3)

    def test_a_part_in_the_wrong_place_names_the_overhang_that_does_not_meet(self):
        seen = self.m.post("/plasmids/assembly/preview",
                           json={"method": "golden_gate", "circular": True, "enzyme": "BsaI",
                                 "fragments": self.tray(("gene", "promoter", "backbone"))}).get_json()
        self.assertFalse(seen["ok"])
        self.assertIn("does not fit", seen["status"])

    def test_a_part_put_in_twice_is_refused_before_anything_is_made(self):
        name = uniq("pGGdup")
        r = self.post(self.m, "/plasmids/assembly/create",
                      {"method": "golden_gate", "circular": "1", "enzyme": "BsaI", "name": name,
                       "tray": json.dumps(self.tray(("promoter", "gene", "gene", "backbone")))})
        self.assertFlash(r, "does not fit", "error")
        self.assertIsNone(one("select id from plasmids where name=?", name))
