"""The Chemicals inventory (app/inventory.py PRESETS["chemicals"]) and what
reads it: a notebook Formulation picks a chemical with its molecular weight
(lab_notebook.chemicals_search), @ finds it by abbreviation or CAS number,
and Utilities' chemical picker lists it."""
from __future__ import annotations

import json

from tests.base import *  # noqa: F401,F403
from tests.base import one, uniq
from tests import test_inventory as inv_tests

from app import inventory, lab, lab_notebook


class ChemicalTests(inv_tests.InventoryCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.chems = cls.new_module(cls.a, "chemicals")
        cls.abbr = uniq("MMA").replace("_", "")
        cls.cas = uniq("80-62-").replace("_", "")
        cls.name = uniq("Methyl methacrylate ")
        cls.item = cls.make_item(cls.a, cls.chems, cls.name, attr_abbreviation=cls.abbr, attr_cas=cls.cas,
                                 attr_mw="100.12", attr_purity="99", attr_density="0.94", lot="SHBK1234",
                                 status="in stock")
        cls.number = one("select number from inventory_items where id=?", cls.item)

    def find(self, q):
        return self.m.get(f"/notebook/api/chemicals?q={q}").get_json()

    def test_it_is_a_ready_made_database_and_a_survey_choice(self):
        self.assertIn("chemicals", inventory.PRESETS)
        self.assertIn("chemicals", lab.INVENTORY_CHOICES)
        self.assertIn("Chemicals", self.get_ok(self.a, "/inventory/new"))
        html = self.get_ok(self.a, f"/inventory/{self.chems}")
        for column in ("Abbreviation", "CAS", "MW (g/mol)", "Purity (%)"):
            self.assertIn(column, html)

    def test_a_formulation_finds_a_chemical_by_name_abbreviation_or_cas(self):
        for q in (self.name.split()[-1], self.abbr, self.cas, self.abbr.lower()[:5]):
            got = self.find(q)
            self.assertTrue(got["has_database"])
            mine = [c for c in got["items"] if c["ref"] == f"@{self.chems} {self.number}"]
            self.assertEqual(len(mine), 1, q)
            self.assertEqual((mine[0]["mw"], mine[0]["purity"], mine[0]["density"], mine[0]["lot"]),
                             (100.12, 99.0, 0.94, "SHBK1234"))
            self.assertEqual(mine[0]["abbr"], self.abbr)

    def test_an_exact_abbreviation_comes_first_and_a_used_up_bottle_last(self):
        stem = uniq("TEOS").replace("_", "")
        gone = self.make_item(self.a, self.chems, f"{stem} old bottle", attr_abbreviation=stem, attr_mw="208.33",
                              status="empty")
        longer = self.make_item(self.a, self.chems, f"{stem}-like silane", attr_mw="200")
        exact = self.make_item(self.a, self.chems, "Tetraethyl orthosilicate", attr_abbreviation=stem, attr_mw="208.33",
                               status="in stock")
        names = [c["name"] for c in self.find(stem)["items"]]
        number = {i: one("select name from inventory_items where id=?", i) for i in (gone, longer, exact)}
        self.assertEqual(names[:3], [number[exact], number[longer], number[gone]])

    def test_a_word_only_in_some_other_column_finds_nothing(self):
        self.assertEqual([c for c in self.find("storage_temp")["items"] if c["ref"]], [])

    def test_built_in_molecular_weights_follow_the_labs_own(self):
        got = self.find("HEPES")["items"]
        builtin = [c for c in got if c.get("builtin")]
        self.assertEqual(builtin[0]["name"], "HEPES")
        self.assertEqual(builtin[0]["ref"], "")
        self.assertAlmostEqual(builtin[0]["mw"], 238.30)

    def test_at_finds_it_by_abbreviation_and_the_popover_gives_its_mw(self):
        got = self.m.get(f"/notebook/search/{self.chems}?q={self.abbr}").get_json()
        self.assertEqual([i["id"] for i in got["items"]], [self.number])
        # A word that is only a column's name is not a match.
        self.assertEqual(self.m.get(f"/notebook/search/{self.chems}?q=abbrev").get_json()["items"], [])
        fields = dict(self.m.get(f"/notebook/lookup/{self.chems}/{self.number}").get_json()["fields"])
        self.assertEqual((fields["MW (g/mol)"], fields["CAS"], fields["Abbreviation"]), ("100.12", self.cas, self.abbr))

    def test_a_page_with_a_formulation_is_listed_on_the_chemical(self):
        block = {"name": "Batch 1", "basis": 0, "components": [
            {"name": self.name, "ref": f"@{self.chems} {self.number}", "mw": "100.12", "mass": "10", "unit": "g",
             "given": "mass", "lot": "SHBK1234", "done": True}]}
        body = f"## Formulation\n\n```formulation\n{json.dumps(block)}\n```\n"
        r = self.m.post("/notebook/api/pages/new", data=json.dumps({"title": uniq("Polymerisation ")}),
                        content_type="application/json")
        page = r.get_json()["page_id"]
        self.m.post(f"/notebook/pages/{page}/update", data={"body": body})
        got = self.m.get(f"/notebook/backlinks/{self.chems}/{self.number}").get_json()
        self.assertIn(page, [i["page_id"] for i in got["items"]])
        # As a template, the chemicals stay; the ticks and this bottle's lot go.
        kept = lab_notebook.structure_only(body).split("```formulation\n", 1)[1].split("\n```", 1)[0]
        component = json.loads(kept)["components"][0]
        self.assertEqual((component["ref"], component["mass"]), (f"@{self.chems} {self.number}", "10"))
        self.assertNotIn("done", component)
        self.assertNotIn("lot", component)

    def test_utilities_lists_it_by_name_and_abbreviation(self):
        html = self.get_ok(self.m, "/utilities")
        listed = json.loads(html.split('id="util-lab-chemicals">', 1)[1].split("</script>", 1)[0])
        names = {c["name"]: c["mw"] for c in listed}
        self.assertEqual(names.get(self.name), 100.12)
        self.assertEqual(names.get(self.abbr), 100.12)

    def test_a_received_order_can_go_to_chemicals_and_one_can_be_ordered_again(self):
        orders = one("select key from inventory_modules where kind='orders' and private_to='' order by id")
        html = self.get_ok(self.a, f"/inventory/{orders}")
        self.assertIn(f'value="{self.chems}" data-kind="chemicals"', html)
        r = self.a.get(f"/inventory/{orders}?reorder={self.chems}:{self.item}")
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.name, r.get_data(as_text=True))


class ExperimentPages(inv_tests.InventoryCase):
    def test_a_new_experiment_ends_with_a_summary(self):
        self.assertIn("## Summary & next steps", lab_notebook.STARTERS["experiment"]["body"])
