"""The lab's feature library (app/feature_library.py): elements kept by
sequence from annotated plasmids, and Detect features, which marks them on
another plasmid's map."""
from __future__ import annotations

import json
import random

from tests.base import AppTestCase, one, row, rows, uniq

from app import feature_library as fl
from app.primer_records import reverse_complement


def bases(n: int, seed) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice("ACGT") for _ in range(n))


ITR = bases(40, 1)
WPRE = bases(60, 2)
FILLER = bases(200, 3)


def features(rid: int) -> list[dict]:
    return json.loads(one("select features_json from plasmids where id=?", rid) or "[]")


class CategoryTests(AppTestCase):

    def test_elements_sort_by_what_they_are(self):
        for name, ftype, want in (("5' ITR", "repeat_region", "viral"), ("WPRE", "misc_feature", "viral"),
                                  ("3' LTR (ΔU3)", "LTR", "viral"), ("RRE", "misc_feature", "viral"),
                                  ("AmpR", "CDS", "selection"), ("AmpR promoter", "promoter", "promoter"), ("CMV enhancer", "enhancer", "promoter"),
                                  ("hSyn promoter", "misc_feature", "promoter"), ("ori", "rep_origin", "origin"),
                                  ("bGH poly(A) signal", "polyA_signal", "terminator"), ("EGFP", "CDS", "coding"),
                                  ("loxP", "protein_bind", "other")):
            with self.subTest(name=name):
                self.assertEqual(fl.category_of(name, ftype), want)

    def test_detect_finds_both_strands_and_across_the_origin(self):
        class E:  # an entry, without the database
            def __init__(self, name, sequence):
                self.name, self.sequence, self.type, self.color, self.notes_json = name, sequence, "misc_feature", "", "{}"
        seq = WPRE[30:] + FILLER + reverse_complement(ITR) + FILLER[:50] + WPRE[:30]
        found = fl.detect([E("WPRE", WPRE), E("ITR", ITR)], seq, True, [])
        by_name = {f["name"]: f for f in found}
        self.assertEqual((by_name["WPRE"]["start"], by_name["WPRE"]["end"], by_name["WPRE"]["direction"]),
                         (len(seq) - 30, 29, 1))
        self.assertEqual((by_name["ITR"]["start"], by_name["ITR"]["direction"]), (230, -1))
        # Linear, the one across the end isn't there; one already marked isn't marked again.
        self.assertEqual([f["name"] for f in fl.detect([E("WPRE", WPRE)], seq, False, [])], [])
        marked = [{"name": "ITR", "start": 232, "end": 260, "direction": 1}]
        self.assertEqual([f["name"] for f in fl.detect([E("ITR", ITR)], seq, True, marked)], [])


class LibraryRouteTests(AppTestCase):

    def setUp(self):
        # Elements of their own: the library keeps one entry per sequence.
        name = uniq("pAAV-src-")
        self.itr, self.wpre = bases(40, name + "itr"), bases(60, name + "wpre")
        gb = self.genbank(name, FILLER[:20] + self.itr + FILLER[20:80] + self.wpre + FILLER[80:120],
                          [("repeat_region", 21, 60, uniq("AAV2 ITR "), False), ("misc_feature", 121, 180, uniq("WPRE "), False),
                           ("misc_feature", 1, 20, "Feature 1", False)])
        self.source = self.make_plasmid(self.m, name=name)
        self.post(self.m, f"/plasmids/{self.source}/upload-sequence", {"sequence_text": gb})
        self.itr_name, self.wpre_name = [f["name"] for f in features(self.source)][:2]

    @staticmethod
    def genbank(name, sequence, feats):
        lines = [f"LOCUS       {name[:16]} {len(sequence)} bp    DNA     circular SYN 01-JAN-2026",
                 "FEATURES             Location/Qualifiers"]
        for ftype, start, end, label, rev in feats:
            loc = f"complement({start}..{end})" if rev else f"{start}..{end}"
            lines += [f"     {ftype:<16}{loc}", f'                     /label="{label}"']
        lines.append("ORIGIN")
        for i in range(0, len(sequence), 60):
            lines.append(f"{i + 1:>9} " + " ".join(sequence[i:i + 60][j:j + 10].lower() for j in range(0, 60, 10)).strip())
        lines.append("//")
        return "\n".join(lines) + "\n"

    def library(self):
        return {r[0]: r[1] for r in rows("select name, category from feature_library")}

    def test_add_to_library_keeps_named_features_once(self):
        r = self.post(self.m, f"/plasmids/{self.source}/features-to-library")
        self.assertFlash(r, "Added 2 elements to the feature library.", "success")
        self.assertEqual(self.library().get(self.itr_name), "viral")
        self.assertEqual(self.library().get(self.wpre_name), "viral")
        self.assertNotIn("Feature 1", self.library())
        self.assertFlash(self.post(self.m, f"/plasmids/{self.source}/features-to-library"),
                         "already has every named feature", "info")

    def test_detect_features_marks_them_on_another_plasmid_and_keeps_a_version(self):
        self.post(self.m, f"/plasmids/{self.source}/features-to-library")
        target = self.make_plasmid(self.m, sequence_text=FILLER[:90] + reverse_complement(self.wpre) + FILLER[90:] + self.itr)
        r = self.post(self.m, f"/plasmids/{target}/detect-features")
        self.assertFlash(r, "Marked 2 features from the library", "success")
        marked = {f["name"]: f for f in features(target)}
        self.assertEqual((marked[self.wpre_name]["start"], marked[self.wpre_name]["direction"]), (90, -1))
        self.assertEqual(marked[self.itr_name]["start"], 90 + 60 + 110)
        self.assertEqual(one("select how from plasmid_sequence_versions where plasmid_row_id=? order by id desc limit 1",
                             target), "detect")
        # Again: each is already marked.
        self.assertFlash(self.post(self.m, f"/plasmids/{target}/detect-features"), "already marked", "info")
        # Someone who may not edit it can't.
        self.assertFlash(self.post(self.o, f"/plasmids/{target}/detect-features"), "belongs to", "error")

    def test_the_library_page_groups_elements_and_only_admins_collect(self):
        self.post(self.m, f"/plasmids/{self.source}/features-to-library")
        html = self.get_ok(self.m, "/plasmids/features")
        self.assertIn("Viral elements", html)
        self.assertIn(self.itr_name, html)
        self.assertNotIn("Collect from every plasmid", html)
        self.assertIn("Collect from every plasmid", self.get_ok(self.a, "/plasmids/features"))
        self.assertIn(self.m.post("/plasmids/features/collect").status_code, (302, 403))
        entry = one("select id from feature_library where name=?", self.itr_name)
        self.assertFlash(self.post(self.o, f"/plasmids/features/{entry}/delete"), "Only an admin", "error")
        self.assertFlash(self.post(self.m, f"/plasmids/features/{entry}/delete"), "Took", "success")
        self.assertIsNone(one("select id from feature_library where id=?", entry))

    def test_the_plasmid_page_offers_detect_and_add(self):
        html = self.get_ok(self.m, f"/plasmid/{one('select plasmid_id from plasmids where id=?', self.source)}")
        self.assertIn("Detect features", html)
        self.assertIn("Add to library", html)
