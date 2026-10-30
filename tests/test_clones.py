"""The plasmid page's Verify tab (app/clone_routes.py): reads grouped into
clones by their file names, checked against the map, kept, accepted."""
from __future__ import annotations

import io
import unittest

from tests.base import AppTestCase, count, one, uniq
from tests.test_seqverify import SEQ, SPACING, abif, traces

from app import clone_routes


def genbank(name: str, sequence: str) -> str:
    lines = [f"LOCUS       {name[:16]} {len(sequence)} bp    DNA     circular SYN 01-JAN-2026", "FEATURES             Location/Qualifiers",
             "     CDS             1001..1300", '                     /label="GFP"', "ORIGIN"]
    for i in range(0, len(sequence), 60):
        lines.append(f"{i + 1:>9} {sequence[i:i + 60].lower()}")
    return "\n".join([*lines, "//"])


def read(start: int, end: int, change: dict | None = None) -> bytes:
    """An .ab1 of the plasmid from start to end (0-based), good all along."""
    bases = list(SEQ[start:end])
    for at, base in (change or {}).items():
        bases[at - start] = base
    text = "".join(bases)
    return abif(text, [50] * len(text), trace=traces(text))


class Naming(unittest.TestCase):
    def test_the_clone_a_file_name_names(self):
        for name, clone in (("C4_CAG-F.ab1", "4"), ("clone3-T7.ab1", "3"), ("colony 2 rev.fa", "2"),
                            ("pCAG_c12_T7.ab1", "12"), ("T7_promoter.ab1", ""), ("20250101_e_read.ab1", "")):
            self.assertEqual(clone_routes.clone_of(name), clone, name)


class VerifyTab(AppTestCase):
    def setUp(self):
        self.name = uniq("pVer-")
        self.row = self.make_plasmid(self.m, name=self.name)
        self.post(self.m, f"/plasmids/{self.row}/upload-sequence", {"sequence_text": genbank(self.name, SEQ)})
        self.number = one("select plasmid_id from plasmids where id=?", self.row)

    def check(self, client, files, **form):
        data = {"reads": [(io.BytesIO(body), name) for name, body in files], **form}
        r = client.post(f"/plasmids/{self.row}/clones", data=data, content_type="multipart/form-data")
        return r.status_code, r.get_json()

    def test_reads_named_by_clone_become_clones_kept_with_the_plasmid(self):
        code, j = self.check(self.m, [("C4_F.ab1", read(900, 1500)), ("C4_R.ab1", read(950, 1550)),
                                      ("C5_F.ab1", read(900, 1500, {1100: "A" if SEQ[1100] != "A" else "C"}))])
        self.assertEqual(code, 200, j)
        clones = {c["name"]: c for c in j["clones"]}
        self.assertEqual(set(clones), {"4", "5"})
        self.assertEqual(clones["4"]["status"], "match")
        self.assertEqual(clones["5"]["status"], "differences")
        self.assertEqual(count("plasmid_files", "plasmid_row_id=?", self.row), 3)

    def test_a_clone_opens_with_its_traces(self):
        _code, j = self.check(self.m, [("clone7.ab1", read(900, 1500, {1100: "A" if SEQ[1100] != "A" else "C"}))])
        detail = self.m.get(f"/plasmids/{self.row}/clones/{j['made'][0]}.json").get_json()
        diff = detail["report"]["differences"][0]
        self.assertEqual(diff["position"], 1101)
        # The tab's wide picture: twenty bases either side.
        self.assertEqual(len(diff["trace"]["bases"]), 2 * clone_routes.TRACE_FLANK + 1)
        self.assertGreater(len(diff["trace"]["channels"]["A"]), SPACING)

    def test_reads_that_name_no_clone_are_the_next_one(self):
        self.check(self.m, [("C2_F.ab1", read(900, 1500))])
        _code, j = self.check(self.m, [("miniprep.ab1", read(900, 1500))])
        self.assertIn("3", {c["name"] for c in j["clones"]})
        _code, j = self.check(self.m, [("anything.ab1", read(900, 1500))], clone="clone 9")
        self.assertIn("9", {c["name"] for c in j["clones"]})

    def test_accept_marks_the_plasmid_verified(self):
        _code, j = self.check(self.m, [("C3_F.ab1", read(900, 1500))])
        clone = j["made"][0]
        r = self.m.post(f"/plasmids/{self.row}/clones/{clone}/accept").get_json()
        self.assertTrue(r["ok"])
        self.assertEqual(r["verified"]["label"], "Clone 3")
        self.assertIn("data-verified-pill", self.get_ok(self.m, f"/plasmid/{self.number}"))
        r = self.m.post(f"/plasmids/{self.row}/clones/{clone}/accept", data={"undo": "1"}).get_json()
        self.assertIsNone(r["verified"])

    def test_someone_who_may_only_look_checks_without_keeping(self):
        code, j = self.check(self.o, [("C8_F.ab1", read(900, 1500))])
        self.assertEqual(code, 200, j)
        self.assertFalse(j["kept"])
        self.assertEqual(j["made"], [])
        self.assertEqual(count("plasmid_clones", "plasmid_row_id=?", self.row), 0)
        _code, mine = self.check(self.m, [("C8_F.ab1", read(900, 1500))])
        r = self.o.post(f"/plasmids/{self.row}/clones/{mine['made'][0]}/accept")
        self.assertEqual(r.status_code, 403)

    def test_removing_a_clone_keeps_its_reads(self):
        _code, j = self.check(self.m, [("C6_F.ab1", read(900, 1500))])
        r = self.m.post(f"/plasmids/{self.row}/clones/{j['made'][0]}/remove").get_json()
        self.assertTrue(r["ok"])
        self.assertEqual(count("plasmid_clones", "plasmid_row_id=?", self.row), 0)
        self.assertEqual(count("plasmid_files", "plasmid_row_id=?", self.row), 1)

    def test_a_poor_read_says_so(self):
        text = SEQ[900:960]
        _code, j = self.check(self.m, [("C1_F.ab1", abif(text, [5] * len(text), trace=traces(text)))])
        self.assertEqual({c["name"]: c["status"] for c in j["clones"]}["1"], "poor")

    def test_the_tab_is_on_the_page(self):
        page = self.get_ok(self.m, f"/plasmid/{self.number}")
        self.assertIn('data-tab="verify"', page)
        self.assertIn("data-verify-tab", page)


if __name__ == "__main__":
    unittest.main()
