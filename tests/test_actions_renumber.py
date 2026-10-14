"""Renumbering an inventory's items (app/inventory_renumber.py): on its
page, with a preview, and as an assistant's proposal (inventory_renumber),
in one batch that undo puts back, links in the notebook and orders with it."""
from __future__ import annotations

import json

from tests.base import *  # noqa: F401,F403
from tests.base import count, execute, one, rows, uniq
from tests import test_inventory as inv_tests
from tests.test_actions_more import Applies, errors_of, run
from tests.test_inventory import module_id
from tests.test_notebook import Notebook

SHUFFLED = ("B3", "B1", "B5", "B2", "B4")


class Renumbering(Applies, inv_tests.InventoryCase, Notebook):
    def inventory(self, names=SHUFFLED, client=None):
        """A new inventory with these items, numbered 1, 2, 3… in this order."""
        key = self.new_module(client or self.m, "custom")
        for name in names:
            self.make_item(client or self.m, key, name)
        return key

    def numbers(self, key) -> dict:
        return dict(rows("select name, number from inventory_items where module_id_fk=?", module_id(key)))

    def test_on_its_page_a_preview_then_one_batch_that_undo_puts_back(self):
        key = self.inventory()
        self.assertEqual(self.numbers(key), {"B3": 1, "B1": 2, "B5": 3, "B2": 4, "B4": 5})
        # What stores an item's number: a link in the notebook, an order put
        # into stock as it, an order made with Order again.
        page = self.new_page(self.m, title=uniq("Plan "))
        self.save(self.m, page, body=f"Use @{key} 1 and @{key} 2, not @{key} 12.")
        label = one("select label from inventory_modules where key=?", key)
        order = self.make_item(self.m, "orders", uniq("Antibody "), notes=f"Reorder of {label} #4")
        execute("update inventory_items set attrs=? where id=?", json.dumps({"stocked_as": f"{key}:3"}), order)

        html = self.get_ok(self.m, f"/inventory/{key}/renumber?by=name_number")
        self.assertIn("data-renumber-preview", html)
        self.assertEqual(html.count("data-renumber-row"), 5)
        self.assertEqual(self.numbers(key)["B3"], 1)                          # a preview changes nothing

        r = self.post(self.m, f"/inventory/{key}/renumber", data={"by": "name_number"})
        self.assertFlash(r, "Renumbered 5", "success")
        self.assertEqual(self.numbers(key), {"B1": 1, "B2": 2, "B3": 3, "B4": 4, "B5": 5})
        self.assertEqual(one("select body from notebook_pages where id=?", page),
                         f"Use @{key} 3 and @{key} 1, not @{key} 12.")
        self.assertEqual(json.loads(one("select attrs from inventory_items where id=?", order))["stocked_as"], f"{key}:5")
        self.assertEqual(one("select notes from inventory_items where id=?", order), f"Reorder of {label} #2")
        # The next one follows the highest.
        self.make_item(self.m, key, "B6")
        self.assertEqual(self.numbers(key)["B6"], 6)

        batch_id = one("select id from batches where actor=? and description like 'renumber %' order by id desc",
                       self.member)
        execute("delete from inventory_items where module_id_fk=? and name='B6'", module_id(key))
        self.post(self.a, f"/batches/{batch_id}/undo")
        self.assertEqual(self.numbers(key), {"B3": 1, "B1": 2, "B5": 3, "B2": 4, "B4": 5})
        self.assertEqual(one("select body from notebook_pages where id=?", page),
                         f"Use @{key} 1 and @{key} 2, not @{key} 12.")
        self.assertEqual(json.loads(one("select attrs from inventory_items where id=?", order))["stocked_as"], f"{key}:3")
        self.assertEqual(one("select notes from inventory_items where id=?", order), f"Reorder of {label} #4")

    def test_by_a_column_in_natural_order(self):
        key = self.inventory(("B10", "B2", "a", "B1"))
        self.post(self.m, f"/inventory/{key}/renumber", data={"by": "name", "order": "ascending"})
        self.assertEqual(self.numbers(key), {"a": 1, "B1": 2, "B2": 3, "B10": 4})
        self.post(self.m, f"/inventory/{key}/renumber", data={"by": "name", "order": "descending"})
        self.assertEqual(self.numbers(key), {"B10": 1, "B2": 2, "B1": 3, "a": 4})
        # An empty value goes last either way.
        execute("update inventory_items set lot='L2' where module_id_fk=? and name='B2'", module_id(key))
        execute("update inventory_items set lot='L10' where module_id_fk=? and name='B10'", module_id(key))
        self.post(self.m, f"/inventory/{key}/renumber", data={"by": "lot", "order": "descending"})
        self.assertEqual([self.numbers(key)[n] for n in ("B10", "B2")], [1, 2])
        self.assertEqual({self.numbers(key)[n] for n in ("a", "B1")}, {3, 4})

    def test_refused_with_a_number_twice_missing_or_none(self):
        for names, said in ((("B1", "B2", "B2b", "B3"), "2 is in more than one name: B2, B2b"),
                            (("B1", "B3", "B4"), "No name has 2"),
                            (("B1", "Spare"), "has no number in its name: Spare")):
            key = self.inventory(names)
            before = self.numbers(key)
            html = self.get_ok(self.m, f"/inventory/{key}/renumber?by=name_number")
            self.assertIn("data-renumber-problems", html)
            self.assertIn(said, html.replace("&#39;", "'"))
            r = self.post(self.m, f"/inventory/{key}/renumber", data={"by": "name_number"})
            self.assertFlash(r, said, "error")
            self.assertEqual(self.numbers(key), before)
            out = run(self.member, {"action": "inventory_renumber", "target": key, "fields": {}})
            self.assertIn(said, errors_of(out))

    def test_only_an_admin_or_its_maker(self):
        key = self.inventory()
        r = self.post(self.o, f"/inventory/{key}/renumber", data={"by": "name_number"})
        self.assertFlash(r, "can renumber it", "error")
        self.assertEqual(self.numbers(key)["B3"], 1)
        out = run(self.other, {"action": "inventory_renumber", "target": key, "fields": {"by": "name_number"}})
        self.assertIn("can renumber it", errors_of(out))
        self.assertNotIn("Renumber", self.get_ok(self.o, f"/inventory/{key}").split("dt-toolbar")[1][:4000])

    def test_proposed_by_an_assistant_with_every_change_listed(self):
        key = self.inventory()
        label = one("select label from inventory_modules where key=?", key)
        change = {"action": "inventory_renumber", "target": label, "fields": {"by": "name_number"}}
        out = run(self.member, change)
        self.assertTrue(out.ok, out.errors)
        self.assertEqual(self.numbers(key)["B3"], 1)                             # a preview
        self.assertIn("5 of 5 get a new #", out.changes[0].summary)
        self.assertIn("#1 → #3 B3", out.changes[0].summary)
        moved = {r["label"]: r["changes"]["changes"]["number"] for r in out.changes[0].records
                 if r["table"] == "inventory_items" and "number" in (r["changes"] or {}).get("changes", {})}
        self.assertEqual(moved, {"B3": [1, 3], "B1": [2, 1], "B5": [3, 5], "B2": [4, 2], "B4": [5, 4]})
        self.assertTrue(any("Labels printed before" in w for w in out.changes[0].warnings), out.changes[0].warnings)
        self.apply(change)
        self.assertEqual(self.numbers(key), {"B1": 1, "B2": 2, "B3": 3, "B4": 4, "B5": 5})
        out = run(self.member, change)
        self.assertIn("nothing would change", errors_of(out))
