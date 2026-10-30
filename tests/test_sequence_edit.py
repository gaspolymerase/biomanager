"""Editing a plasmid on its Sequence tab (app/sequence_edit.py and
sequence_routes' edit.json)."""
from __future__ import annotations

import json
import unittest

from tests.base import client_for, make_user, one, uniq
from tests.test_inventory import InventoryCase
from tests.test_sequence_view import PLASMID, genbank

from app import sequence_edit as se

FEATS = [{"name": "GENE", "type": "CDS", "start": 1000, "end": 1719, "direction": 1},
         {"name": "after", "type": "misc_feature", "start": 2000, "end": 2100, "direction": 1},
         {"name": "a primer", "kind": "primer", "start": 1300, "end": 1319, "direction": 1}]


class TheOperations(unittest.TestCase):
    def test_typing_over_bases(self):
        seq, feats, done = se.bases(PLASMID, FEATS, 1201, 1203, "GGG")
        self.assertEqual(seq[1200:1203], "GGG")
        self.assertEqual(len(seq), len(PLASMID))
        self.assertEqual(done["kind"], "substitution")
        self.assertEqual([f["name"] for f in feats], ["GENE", "after", "a primer"])     # nothing moved

    def test_inserting_moves_what_follows_and_stretches_what_it_falls_in(self):
        seq, feats, done = se.bases(PLASMID, FEATS, 1501, 1500, "ACGTAC")
        self.assertEqual((len(seq), done["kind"], done["added"]), (len(PLASMID) + 6, "insertion", 6))
        by = {f["name"]: (f["start"], f["end"]) for f in feats}
        self.assertEqual(by["GENE"], (1000, 1725))
        self.assertEqual(by["after"], (2006, 2106))

    def test_deleting_takes_a_primer_mark_it_touches(self):
        seq, feats, done = se.bases(PLASMID, FEATS, 1301, 1310, "")
        self.assertEqual((len(seq), done["kind"]), (len(PLASMID) - 10, "deletion"))
        self.assertNotIn("a primer", [f["name"] for f in feats])
        self.assertEqual(done["dropped"], 1)

    def test_iupac_letters_and_what_is_refused(self):
        seq, _f, _d = se.bases(PLASMID, [], 10, 9, "NNRY")
        self.assertEqual(seq[9:13], "NNRY")
        for args, code in (((10, 9, ""), "nothing"), ((1, len(PLASMID), ""), "empty"), ((0, 3, "A"), "outside"),
                           ((10, 9, "123"), "not_bases"), ((5, 5, PLASMID[4]), "same")):
            with self.assertRaises(se.EditError) as caught:
                se.bases(PLASMID, [], *args)
            self.assertEqual(caught.exception.code, code, args)

    def test_features(self):
        feats = se.add_feature(FEATS, len(PLASMID), 101, 200, "my tag", "tag", -1)
        new = feats[-1]
        self.assertEqual((new["start"], new["end"], new["direction"], new["type"]), (100, 199, -1, "tag"))
        feats = se.edit_feature(feats, 0, name="EGFP", direction=-1)
        self.assertEqual((feats[0]["name"], feats[0]["direction"], feats[0]["type"]), ("EGFP", -1, "CDS"))
        feats = se.delete_feature(feats, 1)
        self.assertNotIn("after", [f["name"] for f in feats])
        with self.assertRaises(se.EditError):
            se.add_feature(FEATS, len(PLASMID), 101, 200, "  ")
        with self.assertRaises(se.EditError):
            se.delete_feature(FEATS, 99)


class ThroughThePage(InventoryCase):
    def setUp(self):
        self.row = self.make_plasmid(self.m, name=uniq("pEdit-"))
        self.post(self.m, f"/plasmids/{self.row}/upload-sequence", {"sequence_text": genbank("pEdit")})

    def view(self, client=None):
        return (client or self.m).get(f"/plasmids/{self.row}/view.json").get_json()

    def edit(self, body, client=None):
        return (client or self.m).post(f"/plasmids/{self.row}/edit.json", json=body)

    def test_an_edit_then_undo(self):
        v = self.view()
        self.assertTrue(v["editable"])
        r = self.edit({"op": "bases", "base": v["hash"], "start": 1501, "end": 1500, "bases": "GATTACA"}).get_json()
        self.assertTrue(r["ok"] and r["changed"], r)
        self.assertEqual(len(one("select full_sequence from plasmids where id=?", self.row)), len(PLASMID) + 7)
        egfp = next(f for f in json.loads(one("select features_json from plasmids where id=?", self.row))
                    if f["name"] == "EGFP")
        self.assertEqual(egfp["end"], 1719 + 7)
        self.assertEqual(one("select how from plasmid_sequence_versions where plasmid_row_id=? order by id desc",
                             self.row), "editor")
        undone = self.edit({"op": "state", "base": r["hash"], "state": r["before"]}).get_json()
        self.assertTrue(undone["ok"], undone)
        self.assertEqual(one("select full_sequence from plasmids where id=?", self.row), PLASMID)

    def test_a_feature_through_the_page(self):
        v = self.view()
        r = self.edit({"op": "add_feature", "base": v["hash"], "start": 101, "end": 200, "name": "lox",
                       "type": "protein_bind"}).get_json()
        self.assertTrue(r["ok"], r)
        v = self.view()
        lox = next(f for f in v["features"] if f["name"] == "lox")
        r = self.edit({"op": "edit_feature", "base": v["hash"], "at": lox["at"], "name": "loxP"}).get_json()
        self.assertTrue(r["ok"], r)
        self.assertIn("loxP", [f["name"] for f in self.view()["features"]])
        r = self.edit({"op": "topology", "base": self.view()["hash"], "circular": False}).get_json()
        self.assertTrue(r["ok"], r)
        self.assertFalse(self.view()["circular"])

    def test_a_stale_page_is_refused(self):
        v = self.view()
        self.edit({"op": "topology", "base": v["hash"], "circular": False})
        late = self.edit({"op": "bases", "base": v["hash"], "start": 5, "end": 5, "bases": "N"})
        self.assertEqual(late.status_code, 409)
        self.assertTrue(late.get_json()["stale"])

    def test_someone_who_may_not_edit_is_refused(self):
        other = client_for(make_user(uniq("viewer")))
        v = self.view(other)
        self.assertFalse(v["editable"])
        self.assertEqual(self.edit({"op": "topology", "base": v["hash"], "circular": False}, other).status_code, 403)

if __name__ == "__main__":
    unittest.main()
