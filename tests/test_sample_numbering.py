"""Samples numbered by type: a new Samples starts with no types and asks
which the lab keeps, each with a prefix; a new sample left without a
Sample ID is named after its type's prefix, each type counting on its own
(tissue T1, T2…; blood BL1…), whichever way it was made. The # stays the
database's own row number."""
from __future__ import annotations

import json
import re

from tests.base import *  # noqa: F401,F403
from tests.base import execute, one, rows, uniq
from tests.test_api import Base as ApiBase
from tests.test_inventory import InventoryCase, choice_rows, module_id, settings_of


def names(key: str) -> list[str]:
    """The Sample IDs, in # order."""
    return [r[0] for r in rows("select name from inventory_items where module_id_fk=? order by number",
                               module_id(key))]


class ChoosingTheFirstTypesTest(InventoryCase):
    def test_a_new_samples_asks_which_types_the_lab_keeps(self):
        key = self.new_module(self.a, "samples", types={})
        self.assertEqual(settings_of(key)["categories"], [])
        html = self.get_ok(self.m, f"/inventory/{key}")
        self.assertIn("data-type-picker", html)
        self.assertNotIn("data-type-tabs", html)
        for offered in ("tissue", "blood", "serum", "DNA", "RNA", "protein", "organoid", "cells", "other"):
            self.assertIn(f'value="{offered}"', html)
        self.assertIn('value="BL"', html)          # a prefix suggested, to change

    def test_ticked_types_and_ones_of_its_own(self):
        key = self.new_module(self.a, "samples", types={})
        r = self.post(self.m, f"/inventory/{key}/types/choose", data={
            "types_count": "5",
            "types_0_name": "tissue", "types_0_on": "1", "types_0_prefix": " B ",
            "types_1_name": "blood", "types_1_prefix": "BL",                      # not ticked
            "types_2_name": "DNA", "types_2_on": "1", "types_2_prefix": "D-",
            "types_3_name": " plasma ", "types_3_on": "1", "types_3_prefix": "",  # one of its own, named by hand
            "types_4_name": "", "types_4_on": "1", "types_4_prefix": "X"})       # an empty row
        self.assertFlash(r, "tissue, DNA, plasma", "success")
        s = settings_of(key)
        self.assertEqual(s["categories"], ["tissue", "DNA", "plasma"])
        self.assertEqual(s["prefixes"], {"tissue": "B", "DNA": "D-"})
        html = self.get_ok(self.m, f"/inventory/{key}")
        self.assertNotIn("data-type-picker", html)
        self.assertEqual(re.findall(r'data-type-tab="([^"]+)"', html), ["tissue", "DNA", "plasma"])
        self.assertIn("Numbered B1, B2…", html)
        # Once it has types, + and Configure change them.
        r = self.post(self.m, f"/inventory/{key}/types/choose", data={
            "types_count": "1", "types_0_name": "blood", "types_0_on": "1"})
        self.assertFlash(r, "has its types already", "error")
        self.assertEqual(settings_of(key)["categories"], ["tissue", "DNA", "plasma"])

    def test_refused_with_none_or_prefixes_that_clash(self):
        key = self.new_module(self.a, "samples", types={})
        r = self.post(self.m, f"/inventory/{key}/types/choose", data={"types_count": "1", "types_0_name": "tissue"})
        self.assertFlash(r, "Tick at least one", "error")
        r = self.post(self.m, f"/inventory/{key}/types/choose", data={
            "types_count": "2", "types_0_name": "tissue", "types_0_on": "1", "types_0_prefix": "S",
            "types_1_name": "serum", "types_1_on": "1", "types_1_prefix": "s"})
        self.assertFlash(r, "both have the prefix", "error")
        r = self.post(self.m, f"/inventory/{key}/types/choose", data={
            "types_count": "1", "types_0_name": "tissue", "types_0_on": "1", "types_0_prefix": "T2"})
        self.assertFlash(r, "ends in a digit", "error")
        self.assertEqual(settings_of(key)["categories"], [])

    def test_the_lab_seeds_its_samples_without_types(self):
        from app import inventory_service as svc
        from app.db import SessionLocal
        with SessionLocal() as s:
            module = svc.create_module(s, "samples", uniq("Seeded "))
            self.assertEqual(svc.view(module).categories, [])
            self.assertTrue(svc.view(module).choosing_types)
            s.rollback()


class NumberedByTypeTest(InventoryCase):
    def setUp(self):
        super().setUp()
        self.key = self.new_module(self.a, "samples", types={"tissue": "T", "blood": "BL", "DNA": "D", "other": ""})

    def new(self, client=None, **fields) -> None:
        r = self.post(client or self.m, f"/inventory/{self.key}/items/save", data={"id": "", "name": "", **fields})
        self.assertNoErrors(r)

    def test_each_type_counts_on_its_own(self):
        self.new(category="tissue")
        self.new(category="tissue")
        self.new(category="blood")
        self.new(category="DNA")
        self.new(category="tissue", name="my own")
        self.new(category="tissue")
        self.new(category="other")                 # no prefix: named by hand, here not at all
        self.new()                                 # no type
        self.assertEqual(names(self.key), ["T1", "T2", "BL1", "D1", "my own", "T3", "", ""])
        # The # is still the database's own, one after another.
        numbers = [r[0] for r in rows("select number from inventory_items where module_id_fk=? order by number",
                                      module_id(self.key))]
        self.assertEqual(numbers, list(range(numbers[0], numbers[0] + 8)))

    def test_a_typed_one_moves_the_count_on_and_none_is_given_twice(self):
        self.new(category="tissue", name="t41")
        self.new(category="tissue")
        self.assertEqual(names(self.key), ["t41", "T42"])
        # A deleted sample's name isn't given again: its tube says it.
        newest = one("select id from inventory_items where module_id_fk=? and name='T42'", module_id(self.key))
        self.post(self.m, f"/inventory/{self.key}/items/{newest}/delete")
        self.new(category="tissue")
        self.assertEqual(names(self.key), ["t41", "T43"])

    def test_leading_zeros_are_kept(self):
        self.new(category="DNA", name="D007")
        self.new(category="DNA")
        self.assertEqual(names(self.key)[-1], "D008")

    def test_add_many_and_pasted_names(self):
        self.post(self.m, f"/inventory/{self.key}/items/save", data={"id": "", "name": "", "count": "3", "category": "blood"})
        self.post(self.m, f"/inventory/{self.key}/items/save", data={"id": "", "names": "BL-a\nBL-b", "category": "blood"})
        self.assertEqual(names(self.key), ["BL1", "BL2", "BL3", "BL-a", "BL-b"])

    def test_a_required_sample_id_may_be_left_to_the_prefix(self):
        settings = settings_of(self.key)
        settings["required"] = ["name"]
        execute("update inventory_modules set settings=? where key=?", json.dumps(settings), self.key)
        self.new(category="tissue")
        r = self.post(self.m, f"/inventory/{self.key}/items/save", data={"id": "", "name": "", "category": "other"})
        self.assertFlash(r, "Fill in Sample ID", "error")
        self.assertEqual(names(self.key), ["T1"])

    def test_however_it_is_made(self):
        # An import writes the row first and fills it in after: it waits for its type.
        from app.db import SessionLocal
        from app.models import InventoryItem
        from app import inventory_service as svc
        with SessionLocal() as s:
            mid = module_id(self.key)
            item = InventoryItem(module_id_fk=mid, number=svc.next_number(s, mid), owner=self.member)
            s.add(item)
            s.flush()
            self.assertEqual(item.name or "", "")
            item.category = "DNA"
            s.flush()
            self.assertEqual(item.name, "D1")
            s.commit()
        self.new(category="DNA")
        self.assertEqual(names(self.key), ["D1", "D2"])

    def test_the_new_dialog_says_what_it_will_be_named(self):
        self.new(category="tissue", name="T9")
        html = self.get_ok(self.m, f"/inventory/{self.key}")
        codes = json.loads(re.search(r"data-type-codes='([^']+)'", html).group(1))
        self.assertEqual(codes["tissue"], {"prefix": "T", "next": 10, "width": 0})
        self.assertEqual(codes["blood"]["next"], 1)
        self.assertNotIn("other", codes)
        self.assertIn("data-code-note", html)


class PrefixesLaterTest(InventoryCase):
    def test_a_new_type_from_plus_with_its_prefix(self):
        key = self.new_module(self.a, "samples", types={"tissue": "T"})
        self.m.post(f"/inventory/{key}/types/add", data={"name": "slides", "prefix": "SL"})
        self.assertEqual(settings_of(key)["prefixes"], {"tissue": "T", "slides": "SL"})
        r = self.post(self.m, f"/inventory/{key}/types/add", data={"name": "smears", "prefix": "t"})
        self.assertFlash(r, "both have the prefix", "error")
        self.assertNotIn("smears", settings_of(key)["categories"])
        self.post(self.m, f"/inventory/{key}/items/save", data={"id": "", "name": "", "category": "slides"})
        self.assertEqual(names(key), ["SL1"])

    def test_configure_sets_renames_and_drops_prefixes(self):
        # A lab that kept tissue as B1, B2… before its types were numbered.
        key = self.new_module(self.a, "samples", types={"tissue": "", "blood": "", "serum": ""})
        for n in (1, 2, 5):
            self.make_item(self.m, key, f"B{n}", category="tissue")
        html = self.get_ok(self.a, f"/inventory/{key}/configure")
        self.assertIn('name="categories_0_prefix"', html)
        data = self.configure_form(key)
        data.update(choice_rows("categories", [("tissue", "tissue", False, ""), ("whole blood", "blood", False, ""),
                                               ("serum", "serum", True, "")]))
        data.update({"categories_0_prefix": "B", "categories_1_prefix": "WB", "categories_2_prefix": "SE"})
        self.assertNoErrors(self.post(self.a, f"/inventory/{key}/configure", data=data))
        self.assertEqual(settings_of(key)["prefixes"], {"tissue": "B", "whole blood": "WB"})
        self.post(self.m, f"/inventory/{key}/items/save", data={"id": "", "name": "", "category": "tissue"})
        self.assertEqual(names(key)[-1], "B6")
        # A rename keeps the prefix when the form has no prefix column (one without type tabs).
        data = self.configure_form(key, categories=[("tissue", "tissue", False, ""), ("blood", "whole blood", False, "")])
        self.assertNoErrors(self.post(self.a, f"/inventory/{key}/configure", data=data))
        self.assertEqual(settings_of(key)["prefixes"], {"tissue": "B", "blood": "WB"})
        # Two types with one prefix aren't saved.
        data = self.configure_form(key)
        data.update({"categories_0_prefix": "B", "categories_1_prefix": "b"})
        r = self.post(self.a, f"/inventory/{key}/configure", data=data)
        self.assertFlash(r, "both have the prefix", "error")
        self.assertEqual(settings_of(key)["prefixes"], {"tissue": "B", "blood": "WB"})

    def test_an_inventory_without_type_tabs_has_no_prefix_column(self):
        key = self.new_module(self.a, "reagents")
        self.assertNotIn("categories_0_prefix", self.get_ok(self.a, f"/inventory/{key}/configure"))


class OnePrefixAtATimeTest(InventoryCase):
    """POST /inventory/<key>/types/prefix: one type's prefix, as Configure's
    Prefix column sets it (an assistant's inventory_type sends it)."""

    def test_set_changed_and_dropped_by_an_admin_or_its_maker(self):
        key = self.new_module(self.a, "samples", types={"tissue": "", "blood": "BL"})
        for n in (1, 2, 522):
            self.make_item(self.m, key, f"B{n}", category="tissue")
        r = self.post(self.a, f"/inventory/{key}/types/prefix", data={"type": "Tissue", "prefix": " B "})
        self.assertFlash(r, "the next one left without a Sample ID is B523", "success")
        self.assertEqual(settings_of(key)["prefixes"], {"tissue": "B", "blood": "BL"})
        self.post(self.m, f"/inventory/{key}/items/save", data={"id": "", "name": "", "category": "tissue"})
        self.assertEqual(names(key)[-1], "B523")
        r = self.post(self.a, f"/inventory/{key}/types/prefix", data={"type": "blood", "prefix": ""})
        self.assertFlash(r, "blood is not numbered any more", "success")
        self.assertEqual(settings_of(key)["prefixes"], {"tissue": "B"})
        # Someone who only uses it may add a type, not change one's numbering.
        r = self.post(self.m, f"/inventory/{key}/types/prefix", data={"type": "tissue", "prefix": "T"})
        self.assertFlash(r, "can change a prefix", "error")
        self.assertEqual(settings_of(key)["prefixes"], {"tissue": "B"})

    def test_refused_for_a_type_it_lacks_or_a_prefix_that_clashes(self):
        key = self.new_module(self.a, "samples", types={"tissue": "T", "blood": ""})
        r = self.post(self.a, f"/inventory/{key}/types/prefix", data={"type": "plasma", "prefix": "PL"})
        self.assertFlash(r, "plasma is not one of the types", "error")
        r = self.post(self.a, f"/inventory/{key}/types/prefix", data={"type": "blood", "prefix": "t"})
        self.assertFlash(r, "both have the prefix", "error")
        r = self.post(self.a, f"/inventory/{key}/types/prefix", data={"type": "blood", "prefix": "B1"})
        self.assertFlash(r, "ends in a digit", "error")
        self.assertEqual(settings_of(key)["prefixes"], {"tissue": "T"})
        other = self.new_module(self.a, "reagents")
        r = self.post(self.a, f"/inventory/{other}/types/prefix", data={"type": "buffer", "prefix": "BF"})
        self.assertFlash(r, "has no type tabs", "error")


class AnAssistantSetsAPrefixTest(InventoryCase):
    """inventory_type: a type's prefix, or a new type with its own, proposed
    by an assistant and approved like any other change."""

    def run_type(self, who, key, apply=False, **fields):
        from tests.test_actions_more import run
        return run(who, {"action": "inventory_type", "target": key, "fields": fields}, apply=apply)

    def test_a_lab_that_named_its_tissue_b1_b2(self):
        from tests.test_actions_more import errors_of
        key = self.new_module(self.a, "samples", types={"tissue": "", "blood": "BL"})
        for n in (1, 2, 5):
            self.make_item(self.m, key, f"B{n}", category="tissue")
        label = one("select label from inventory_modules where key=?", key)
        out = self.run_type(self.admin, label, type="tissue", prefix="B")
        self.assertTrue(out.ok, errors_of(out))
        self.assertIn("tissue numbered after B (was named by hand); the next one left without a Sample ID is B6",
                      out.changes[0].summary)
        shown = [r for r in out.changes[0].records if r["table"] == "inventory_modules"]
        self.assertEqual([r["changes"]["changes"] for r in shown], [{"Prefix of tissue": ["", "B"]}])
        self.assertEqual(settings_of(key)["prefixes"], {"blood": "BL"})          # a preview
        self.assertTrue(self.run_type(self.admin, key, apply=True, type="tissue", prefix="B").ok)
        self.assertEqual(settings_of(key)["prefixes"], {"tissue": "B", "blood": "BL"})
        self.post(self.m, f"/inventory/{key}/items/save", data={"id": "", "name": "", "category": "tissue"})
        self.assertEqual(names(key)[-1], "B6")
        self.assertIn("nothing would change", errors_of(self.run_type(self.admin, key, type="Tissue", prefix=" B")))
        out = self.run_type(self.admin, key, type="blood", prefix="")
        self.assertIn("blood no longer numbered (was BL…)", out.changes[0].summary)

    def test_only_an_admin_or_its_maker_changes_one_anyone_adds_one(self):
        from tests.test_actions_more import errors_of
        key = self.new_module(self.a, "samples", types={"tissue": "T"})
        self.assertIn("can change a prefix", errors_of(self.run_type(self.member, key, type="tissue", prefix="B")))
        out = self.run_type(self.member, key, apply=True, type=" plasma ", prefix="PL")
        self.assertTrue(out.ok, errors_of(out))
        self.assertIn("a new type, plasma, numbered PL1, PL2…", out.changes[0].summary)
        s = settings_of(key)
        self.assertEqual((s["categories"], s["prefixes"]), (["tissue", "plasma"], {"tissue": "T", "plasma": "PL"}))

    def test_what_it_needs(self):
        from tests.test_actions_more import errors_of
        key = self.new_module(self.a, "samples", types={"tissue": "T", "blood": ""})
        self.assertIn("Give the prefix for tissue", errors_of(self.run_type(self.admin, key, type="tissue")))
        self.assertIn("both have the prefix", errors_of(self.run_type(self.admin, key, type="blood", prefix="T")))
        self.assertIn("has no type tabs", errors_of(self.run_type(self.admin, self.new_module(self.a, "reagents"),
                                                                  type="buffer", prefix="BF")))
        self.assertIn("There is no inventory", errors_of(self.run_type(self.admin, uniq("Nowhere "), type="x")))


class ThePrefixesAreListedTest(ApiBase):
    def test_in_the_vocabulary(self):
        key = InventoryCase.new_module(self.a, "samples", types={"tissue": "B", "blood": ""})
        tok = self.token(self.m, scope="read")
        vocabulary = self.call("get", "/vocabulary", tok).get_json()
        samples = [d for d in vocabulary["databases"] if d["key"] == key][0]
        self.assertEqual((samples["categories"], samples["prefixes"]), (["tissue", "blood"], {"tissue": "B"}))
