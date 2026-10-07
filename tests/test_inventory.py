"""Lab inventories: who may change what, statuses, orders to stock, expiry,
Configure (renaming statuses, undo), Add many, CSV import of orders, bulk
actions, samples traced to a mouse, and creating / deleting inventories.

Every test works in inventories it creates itself (from a preset or the
custom list), so the seeded Samples / Orders / Reagents / Antibodies stay as
they are for other modules."""
from __future__ import annotations

import io
import json
import re
import unittest
from html import escape as html_escape

from tests.base import *  # noqa: F401,F403
from tests.base import (AppTestCase, GRID_NAMING, T, client_for, count, days_ago, days_ahead, errors,
                        flash_text, flashes, location, make_user, one, row, rows, uniq)

ITEM_COLS = ("name", "status", "owner", "is_shared", "category", "received_on", "expires_on",
             "rack_id_fk", "rack_row", "rack_col", "number", "notes", "vendor", "catalog_number",
             "quantity", "unit", "lot", "attrs", "module_id_fk")


def item(item_id: int) -> dict | None:
    found = row(f"select {', '.join(ITEM_COLS)} from inventory_items where id=?", item_id)
    return dict(zip(ITEM_COLS, found)) if found else None


def attrs_of(item_id: int) -> dict:
    return json.loads(one("select attrs from inventory_items where id=?", item_id) or "{}")


def module_id(key: str) -> int:
    return one("select id from inventory_modules where key=?", key)


def settings_of(key: str) -> dict:
    return json.loads(one("select settings from inventory_modules where key=?", key))


def items_named(key: str, name: str) -> list[int]:
    return [r[0] for r in rows("select id from inventory_items where module_id_fk=? and name=? order by number",
                               module_id(key), name)]


def status_count(key: str, status: str) -> int:
    return count("inventory_items", "module_id_fk=? and status=?", module_id(key), status)


def newest_batch(actor: str):
    """(id, description, action) of the newest batch this user ran."""
    return row("select id, description, action from batches where actor=? order by id desc limit 1", actor)


def row_tag(html: str, item_id: int) -> str:
    """The opening <tr> of an item's sheet row (its data-* flags)."""
    m = re.search(rf'<tr data-id="{item_id}"[^>]*>', html)
    return m.group(0) if m else ""


def choice_rows(name: str, spec) -> dict:
    """Configure rows for a choice list; spec is [(value, was, remove, replace)]."""
    data = {f"{name}_count": str(len(spec))}
    for i, (value, was, remove, replace) in enumerate(spec):
        data[f"{name}_{i}_value"] = value
        data[f"{name}_{i}_was"] = was
        if remove:
            data[f"{name}_{i}_remove"] = "1"
        if replace:
            data[f"{name}_{i}_replace"] = replace
    return data


class InventoryCase(AppTestCase):
    """Helpers shared by the inventory tests."""

    @staticmethod
    def new_module(client, preset: str = "custom", label: str | None = None) -> str:
        """Create an inventory through /inventory/new; returns its key."""
        label = label or uniq(f"{preset.title()} ")
        r = client.post("/inventory/new", data={"preset": preset, "label": label, "audience": "lab"})
        path = location(r)
        assert path.startswith("/inventory/") and "/new" not in path, (r.status_code, path)
        return path.split("?")[0].rsplit("/", 1)[1]

    @staticmethod
    def make_rack(client, key: str, name: str | None = None, rows: int = 9, cols: int = 9, **fields) -> int:
        name = name or uniq("Box")
        client.post(f"/inventory/{key}/racks/save", data={
            "id": "", "name": name, "rows": str(rows), "cols": str(cols), "kind": "box", **GRID_NAMING, **fields})
        found = one("select id from inventory_racks where module_id_fk=? and name=?", module_id(key), name)
        assert found, f"box {name} was not created"
        return found

    def configure_form(self, key: str, statuses=None, categories=None, **extra) -> dict:
        """A full Configure form that keeps the inventory as it is, except
        for the status / category rows given."""
        s = settings_of(key)
        label, noun, plural, blurb, icon = row(
            "select label, item_noun, item_noun_plural, blurb, icon from inventory_modules where key=?", key)
        data = {"label": label, "item_noun": noun, "item_noun_plural": plural, "blurb": blurb, "icon": icon,
                "enabled": ["0", "1"], "features": s["features"],
                "category_label": s["category_label"], "field_count": str(len(s["fields"]))}
        for i, f in enumerate(s["fields"]):
            data.update({f"field_{i}_key": f["key"], f"field_{i}_label": f["label"], f"field_{i}_type": f["type"],
                         f"field_{i}_options": ", ".join(f["options"]), f"field_{i}_icon": f["icon"],
                         f"field_{i}_width": str(f["width"])})
            if f["in_table"]:
                data[f"field_{i}_in_table"] = "1"
        data.update(choice_rows("statuses", statuses if statuses is not None
                                else [(v, v, False, "") for v in s["statuses"]]))
        data.update(choice_rows("categories", categories if categories is not None
                                else [(v, v, False, "") for v in s["categories"]]))
        data.update(extra)
        return data


# ======================================================================= permissions

class ItemPermissionTests(InventoryCase):
    """A personal item: its owner or an admin. A lab-common item: anyone
    edits it, only its owner or an admin deletes it or takes it private."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.new_module(cls.a, "reagents")

    def admins(self, shared: bool, **fields) -> int:
        return self.make_item(self.a, self.key, uniq("R"), owner=self.admin, is_shared="1" if shared else "0",
                              status="in stock", **fields)

    def test_member_cannot_edit_someone_elses_private_item(self):
        rid = self.admins(shared=False, notes="orig")
        r = self.m.post(f"/inventory/{self.key}/items/{rid}/update", data={"notes": "member was here"})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(r.get_json()["error"].startswith(f"That reagent belongs to {self.admin}"), r.get_json())
        self.assertEqual(item(rid)["notes"], "orig")

    def test_member_cannot_edit_someone_elses_private_item_from_the_dialog(self):
        rid = self.admins(shared=False)
        name = item(rid)["name"]
        r = self.post(self.m, f"/inventory/{self.key}/items/save", data={"id": str(rid), "name": "hijacked"})
        self.assertTrue(errors(r), flashes(r))
        self.assertEqual(item(rid)["name"], name)

    def test_member_can_edit_a_lab_common_item(self):
        rid = self.admins(shared=True)
        self.assertSaved(self.autosave(self.m, f"/inventory/{self.key}/items/{rid}/update", {"notes": "used 5 mL"}))
        self.assertEqual(item(rid)["notes"], "used 5 mL")

    def test_member_cannot_make_a_lab_common_item_personal(self):
        rid = self.admins(shared=True)
        r = self.m.post(f"/inventory/{self.key}/items/{rid}/update", data={"is_shared": "0"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("make it personal", r.get_json()["error"])
        self.assertEqual(item(rid)["is_shared"], 1)

    def test_member_cannot_take_over_the_owner_of_a_lab_common_item(self):
        rid = self.admins(shared=True)
        r = self.m.post(f"/inventory/{self.key}/items/{rid}/update", data={"owner": self.member})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(item(rid)["owner"], self.admin)

    def test_member_cannot_delete_a_lab_common_item_they_do_not_own(self):
        rid = self.admins(shared=True)
        r = self.post(self.m, f"/inventory/{self.key}/items/{rid}/delete")
        self.assertFlash(r, "can delete it", "error")
        self.assertIsNotNone(item(rid))

    def test_owner_can_delete_their_own_private_item(self):
        rid = self.make_item(self.m, self.key, uniq("R"), is_shared="0")
        self.assertEqual(item(rid)["owner"], self.member)
        r = self.post(self.m, f"/inventory/{self.key}/items/{rid}/delete")
        self.assertFlash(r, "Deleted", "success")
        self.assertIsNone(item(rid))

    def test_owner_can_make_their_lab_common_item_personal(self):
        rid = self.admins(shared=True)
        self.assertSaved(self.autosave(self.a, f"/inventory/{self.key}/items/{rid}/update", {"is_shared": "0"}))
        self.assertEqual(item(rid)["is_shared"], 0)

    def test_admin_can_edit_and_delete_a_members_private_item(self):
        rid = self.make_item(self.m, self.key, uniq("R"), is_shared="0")
        self.assertSaved(self.autosave(self.a, f"/inventory/{self.key}/items/{rid}/update", {"notes": "admin"}))
        self.assertEqual(item(rid)["notes"], "admin")
        self.post(self.a, f"/inventory/{self.key}/items/{rid}/delete")
        self.assertIsNone(item(rid))

    def test_member_cannot_plant_an_item_owned_by_someone_else(self):
        name = uniq("Planted")
        r = self.post(self.m, f"/inventory/{self.key}/items/save",
                      data={"id": "", "name": name, "owner": self.admin, "is_shared": "0"})
        self.assertFlash(r, "Only an admin can add a reagent for someone else", "warning")
        [rid] = items_named(self.key, name)
        self.assertEqual(item(rid)["owner"], self.member)

    def test_admin_can_create_an_item_for_someone_else(self):
        rid = self.make_item(self.a, self.key, uniq("R"), owner=self.member, is_shared="0")
        self.assertEqual(item(rid)["owner"], self.member)

    def test_an_unowned_item_is_open_to_anyone(self):
        rid = self.admins(shared=False)
        execute("update inventory_items set owner='' where id=?", rid)  # predates ownership
        self.assertSaved(self.autosave(self.m, f"/inventory/{self.key}/items/{rid}/update", {"notes": "anyone"}))
        self.post(self.m, f"/inventory/{self.key}/items/{rid}/delete")
        self.assertIsNone(item(rid))

    def test_member_cannot_change_the_status_of_someone_elses_private_item(self):
        rid = self.admins(shared=False)
        r = self.m.post(f"/inventory/{self.key}/items/{rid}/status", data={"status": "low"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(item(rid)["status"], "in stock")

    def test_everyone_sees_every_item_but_others_private_rows_are_locked(self):
        rid = self.admins(shared=False)
        html = self.get_ok(self.m, f"/inventory/{self.key}")
        self.assertIn(item(rid)["name"], html)
        self.assertIn("is-locked", row_tag(html, rid))
        self.assertNotIn("is-locked", row_tag(self.get_ok(self.a, f"/inventory/{self.key}"), rid))

    def test_member_may_duplicate_someone_elses_private_item_and_the_copy_is_theirs(self):
        rid = self.admins(shared=False, lot="L-1", vendor="Sigma")
        self.post(self.m, f"/inventory/{self.key}/items/{rid}/duplicate")
        copy_id = items_named(self.key, item(rid)["name"])[-1]
        self.assertNotEqual(copy_id, rid)
        copy = item(copy_id)
        self.assertEqual((copy["owner"], copy["vendor"], copy["lot"]), (self.member, "Sigma", ""))


# ======================================================================= boxes

class SwitchedOffTests(InventoryCase):
    """A database switched off in Lab setup is closed to members, not just
    left out of the sidebar; an admin can still look, and is told."""

    def test_members_cannot_open_it_and_admins_are_told(self):
        key = self.new_module(self.a, "custom")
        execute("update inventory_modules set enabled=? where key=?", False, key)
        self.assertEqual(self.m.get(f"/inventory/{key}").status_code, 404)
        self.assertIn("is switched off", self.get_ok(self.a, f"/inventory/{key}"))
        execute("update inventory_modules set enabled=? where key=?", True, key)
        self.assertNotIn("is switched off", self.get_ok(self.m, f"/inventory/{key}"))


class BoxTests(InventoryCase):
    """Editing or deleting a box: an admin or whoever created it. Items in
    a box keep their own owner's rules."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.new_module(cls.a, "reagents")

    def test_a_new_box_records_its_creator(self):
        bid = self.make_rack(self.m, self.key)
        self.assertEqual(one("select created_by from inventory_racks where id=?", bid), self.member)

    def test_several_boxes_at_once_are_numbered_on(self):
        base = uniq("Tower ")
        self.make_rack(self.m, self.key, f"{base} 1")
        r = self.post(self.m, f"/inventory/{self.key}/racks/save", data={
            "id": "", "name": base, "rows": "9", "cols": "9", "kind": "box", "count": "3", **GRID_NAMING})
        self.assertFlash(r, f"Made 3 boxes: {base} 2 to {base} 4", "success")
        names = [n for (n,) in rows("select name from inventory_racks where name like ? order by id", f"{base}%")]
        self.assertEqual(names, [f"{base} {i}" for i in (1, 2, 3, 4)])

    def test_boxes_list_coldest_first_whichever_dash_was_typed(self):
        key = self.new_module(self.a, "reagents")
        for name in ("Box 10", "\u221280 A", "-20 B", "4 \u00b0C shelf", "Box 2", "\u221220 A"):
            self.make_rack(self.a, key, name)
        html = self.get_ok(self.a, f"/inventory/{key}")
        wanted = ["\u221280 A", "\u221220 A", "-20 B", "4 \u00b0C shelf", "Box 2", "Box 10"]
        self.assertEqual(sorted(wanted, key=lambda n: html.index(f" {html_escape(n)}</button>")), wanted)

    def test_member_cannot_rename_or_resize_someone_elses_box(self):
        name = uniq("AdminBox")
        bid = self.make_rack(self.a, self.key, name)
        r = self.post(self.m, f"/inventory/{self.key}/racks/save",
                      data={"id": str(bid), "name": "Hijacked", "rows": "3", "cols": "3", **GRID_NAMING})
        self.assertFlash(r, f"Only {self.admin} or an admin can change", "error")
        self.assertEqual(row("select name, rows, cols from inventory_racks where id=?", bid), (name, 9, 9))

    def test_member_cannot_delete_someone_elses_box(self):
        bid = self.make_rack(self.a, self.key)
        r = self.post(self.m, f"/inventory/{self.key}/racks/{bid}/delete")
        self.assertFlash(r, "can delete", "error")
        self.assertEqual(count("inventory_racks", "id=?", bid), 1)

    def test_creator_can_resize_their_own_box(self):
        name = uniq("MyBox")
        bid = self.make_rack(self.m, self.key, name)
        self.post(self.m, f"/inventory/{self.key}/racks/save",
                  data={"id": str(bid), "name": name, "rows": "5", "cols": "4", **GRID_NAMING})
        self.assertEqual(row("select rows, cols from inventory_racks where id=?", bid), (5, 4))

    def test_a_box_that_predates_the_creator_column_is_admin_only(self):
        bid = self.make_rack(self.m, self.key)
        execute("update inventory_racks set created_by='' where id=?", bid)
        self.post(self.m, f"/inventory/{self.key}/racks/{bid}/delete")
        self.assertEqual(count("inventory_racks", "id=?", bid), 1)
        self.post(self.a, f"/inventory/{self.key}/racks/{bid}/delete")
        self.assertEqual(count("inventory_racks", "id=?", bid), 0)

    def test_deleting_a_box_leaves_its_items_unplaced_in_one_undoable_batch(self):
        bid = self.make_rack(self.m, self.key)
        rid = self.make_item(self.m, self.key, uniq("R"), rack_id=str(bid), position="C3")
        self.assertEqual((item(rid)["rack_row"], item(rid)["rack_col"]), (3, 3))
        r = self.post(self.a, f"/inventory/{self.key}/racks/{bid}/delete")
        self.assertFlash(r, "now unplaced", "success")
        self.assertEqual(count("inventory_racks", "id=?", bid), 0)
        self.assertEqual((item(rid)["rack_id_fk"], item(rid)["rack_row"]), (None, None))
        batch_id, description, _ = newest_batch(self.admin)
        self.assertIn("delete box", description)
        self.post(self.a, f"/batches/{batch_id}/undo")
        self.assertEqual(count("inventory_racks", "id=?", bid), 1)
        self.assertEqual((item(rid)["rack_id_fk"], item(rid)["rack_row"], item(rid)["rack_col"]), (bid, 3, 3))

    def test_a_box_cannot_shrink_past_the_items_it_holds(self):
        name = uniq("Box")
        bid = self.make_rack(self.a, self.key, name)
        self.make_item(self.a, self.key, uniq("R"), rack_id=str(bid), position="E5")
        r = self.post(self.a, f"/inventory/{self.key}/racks/save",
                      data={"id": str(bid), "name": name, "rows": "4", "cols": "4", **GRID_NAMING})
        self.assertFlash(r, "cannot shrink to 4 × 4", "error")
        self.assertEqual(row("select rows, cols from inventory_racks where id=?", bid), (9, 9))

    def test_a_box_may_shrink_when_its_items_still_fit(self):
        name = uniq("Box")
        bid = self.make_rack(self.a, self.key, name)
        self.make_item(self.a, self.key, uniq("R"), rack_id=str(bid), position="B2")
        self.post(self.a, f"/inventory/{self.key}/racks/save",
                  data={"id": str(bid), "name": name, "rows": "4", "cols": "4", **GRID_NAMING})
        self.assertEqual(row("select rows, cols from inventory_racks where id=?", bid), (4, 4))

    def test_non_numeric_box_size_falls_back_instead_of_failing(self):
        bid = self.make_rack(self.a, self.key, rows="nine", cols="x")
        self.assertEqual(row("select rows, cols from inventory_racks where id=?", bid), (9, 9))

    def test_member_can_store_their_own_item_in_someone_elses_box(self):
        bid = self.make_rack(self.a, self.key)
        rid = self.make_item(self.m, self.key, uniq("R"))
        r = self.m.post(f"/inventory/{self.key}/items/{rid}/place", data={"rack_id": str(bid), "row": "2", "col": "3"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual((item(rid)["rack_id_fk"], item(rid)["rack_row"], item(rid)["rack_col"]), (bid, 2, 3))

    def test_member_cannot_move_someone_elses_private_item_on_the_grid(self):
        bid = self.make_rack(self.a, self.key)
        rid = self.make_item(self.a, self.key, uniq("R"), is_shared="0", rack_id=str(bid), position="A1")
        r = self.m.post(f"/inventory/{self.key}/items/{rid}/place", data={"rack_id": str(bid), "row": "5", "col": "5"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual((item(rid)["rack_row"], item(rid)["rack_col"]), (1, 1))

    def test_member_cannot_swap_out_someone_elses_private_item(self):
        bid = self.make_rack(self.a, self.key)
        theirs = self.make_item(self.a, self.key, uniq("R"), is_shared="0", rack_id=str(bid), position="A1")
        mine = self.make_item(self.m, self.key, uniq("R"), rack_id=str(bid), position="A2")
        r = self.m.post(f"/inventory/{self.key}/items/{mine}/place", data={"rack_id": str(bid), "row": "1", "col": "1"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("may not move", r.get_json()["error"])
        self.assertEqual((item(theirs)["rack_col"], item(mine)["rack_col"]), (1, 2))

    def test_dropping_a_placed_item_on_an_occupied_cell_swaps_them(self):
        bid = self.make_rack(self.a, self.key)
        a = self.make_item(self.a, self.key, uniq("R"), rack_id=str(bid), position="B1")
        b = self.make_item(self.a, self.key, uniq("R"), rack_id=str(bid), position="B2")
        r = self.a.post(f"/inventory/{self.key}/items/{a}/place", data={"rack_id": str(bid), "row": "2", "col": "2"})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual((item(a)["rack_col"], item(b)["rack_col"]), (2, 1))

    def test_an_unplaced_item_cannot_push_another_out_of_its_cell(self):
        bid = self.make_rack(self.a, self.key)
        holder = self.make_item(self.a, self.key, uniq("R"), rack_id=str(bid), position="A1")
        loose = self.make_item(self.a, self.key, uniq("R"))
        r = self.a.post(f"/inventory/{self.key}/items/{loose}/place", data={"rack_id": str(bid), "row": "1", "col": "1"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual((item(holder)["rack_row"], item(holder)["rack_col"]), (1, 1))
        self.assertIsNone(item(loose)["rack_id_fk"])

    def test_a_new_item_at_a_taken_position_is_not_saved_and_says_why(self):
        bid = self.make_rack(self.a, self.key)
        self.make_item(self.a, self.key, uniq("R"), rack_id=str(bid), position="D7")
        name = uniq("R")
        r = self.post(self.a, f"/inventory/{self.key}/items/save",
                      data={"id": "", "name": name, "rack_id": str(bid), "position": "D7"})
        self.assertFlash(r, "Not saved:", "error")
        self.assertFlash(r, "already holds", "error")
        self.assertEqual(items_named(self.key, name), [])      # no half-saved record to duplicate

    def test_into_a_box_with_no_position_takes_the_next_free_one(self):
        bid = self.make_rack(self.a, self.key)
        self.make_item(self.a, self.key, uniq("R"), rack_id=str(bid), position="A1")
        rid = self.make_item(self.a, self.key, uniq("R"), rack_id=str(bid), position="")
        self.assertEqual((item(rid)["rack_id_fk"], item(rid)["rack_row"], item(rid)["rack_col"]), (bid, 1, 2))

    def test_an_inline_position_outside_the_box_is_refused(self):
        bid = self.make_rack(self.a, self.key, rows=3, cols=3)
        rid = self.make_item(self.a, self.key, uniq("R"))
        r = self.autosave(self.a, f"/inventory/{self.key}/items/{rid}/update",
                          {"rack_id": str(bid), "position": "Z99", "rack_id_was": "", "position_was": ""})
        self.assertRefused(r)
        self.assertIsNone(item(rid)["rack_id_fk"])


# ======================================================================= statuses

class StatusTests(InventoryCase):
    """apply_status: one rule for the dialog, the sheet, the board and bulk."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.orders = cls.new_module(cls.a, "orders")
        cls.samples = cls.new_module(cls.a, "samples")

    def order(self, **fields) -> int:
        return self.make_item(self.a, self.orders, uniq("Order"), **{"status": "requested", **fields})

    def test_moving_an_order_to_received_stamps_the_received_date(self):
        oid = self.order()
        self.assertIsNone(item(oid)["received_on"])
        self.assertSaved(self.autosave(self.a, f"/inventory/{self.orders}/items/{oid}/update", {"status": "ordered"}))
        self.assertIsNone(item(oid)["received_on"])
        self.assertSaved(self.autosave(self.a, f"/inventory/{self.orders}/items/{oid}/update", {"status": "received"}))
        self.assertEqual(item(oid)["received_on"], T)

    def test_creating_an_order_as_received_stamps_the_received_date(self):
        oid = self.order(status="received")
        self.assertEqual(item(oid)["received_on"], T)

    def test_received_keeps_a_received_date_already_entered(self):
        oid = self.order(received_on=days_ago(5))
        self.a.post(f"/inventory/{self.orders}/items/{oid}/status", data={"status": "received"})
        self.assertEqual(item(oid)["received_on"], days_ago(5))

    def test_a_board_move_answers_with_the_rows_stored_values(self):
        oid = self.order()
        r = self.a.post(f"/inventory/{self.orders}/items/{oid}/status", data={"status": "received"})
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["row"]["values"]["status"], "received")
        self.assertEqual(body["payload"]["received_on"], T)
        self.assertIs(body["row"]["active"], False)  # received is no longer an open order

    def test_an_unknown_status_is_refused_inline_and_nothing_is_saved(self):
        oid = self.order(notes="before")
        r = self.autosave(self.a, f"/inventory/{self.orders}/items/{oid}/update",
                          {"status": "lost in mail", "notes": "after"})
        self.assertRefused(r)
        self.assertEqual((item(oid)["status"], item(oid)["notes"]), ("requested", "before"))

    def test_an_unknown_status_is_refused_on_the_board(self):
        oid = self.order()
        r = self.a.post(f"/inventory/{self.orders}/items/{oid}/status", data={"status": "bogus"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(item(oid)["status"], "requested")

    def test_a_status_is_matched_in_any_case(self):
        oid = self.order()
        self.a.post(f"/inventory/{self.orders}/items/{oid}/status", data={"status": "ORDERED"})
        self.assertEqual(item(oid)["status"], "ordered")

    def test_an_item_keeps_an_old_status_while_it_is_not_changed(self):
        oid = self.order()
        execute("update inventory_items set status='legacy' where id=?", oid)
        r = self.autosave(self.a, f"/inventory/{self.orders}/items/{oid}/update", {"status": "legacy", "notes": "kept"})
        self.assertSaved(r)
        self.assertEqual((item(oid)["status"], item(oid)["notes"]), ("legacy", "kept"))

    def test_a_stale_sheet_row_does_not_undo_a_board_move(self):
        oid = self.order()
        self.a.post(f"/inventory/{self.orders}/items/{oid}/status", data={"status": "cancelled"})
        r = self.autosave(self.a, f"/inventory/{self.orders}/items/{oid}/update",
                          {"status": "requested", "status_was": "requested", "notes": "urgent"})
        self.assertSaved(r)
        self.assertEqual((item(oid)["status"], item(oid)["notes"]), ("cancelled", "urgent"))

    def test_a_terminal_status_stamps_the_day_and_reviving_clears_it(self):
        sid = self.make_item(self.a, self.samples, uniq("S"), status="available")
        self.autosave(self.a, f"/inventory/{self.samples}/items/{sid}/update", {"status": "used up"})
        self.assertEqual(attrs_of(sid).get("used_up_on"), T)
        self.autosave(self.a, f"/inventory/{self.samples}/items/{sid}/update",
                      {"status": "available", "status_was": "used up"})
        self.assertNotIn("used_up_on", attrs_of(sid))

    def test_the_orders_page_shows_a_status_board(self):
        oid = self.order()
        html = self.get_ok(self.m, f"/inventory/{self.orders}")
        self.assertIn("data-board", html)
        self.assertIn(item(oid)["name"], html)


# ======================================================================= orders to stock

class OrderToStockTests(InventoryCase):
    """A received order becomes a reagent (or antibody) once."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.orders = cls.new_module(cls.a, "orders")
        cls.reagents = cls.new_module(cls.a, "reagents")
        cls.antibodies = cls.new_module(cls.a, "antibodies")
        cls.samples = cls.new_module(cls.a, "samples")

    def order(self, client=None, **fields) -> int:
        return self.make_item(client or self.a, self.orders, uniq("Order"), **{"status": "received", **fields})

    def stock(self, oid: int, target: str, client=None):
        return (client or self.a).post(f"/inventory/{self.orders}/items/{oid}/to-reagents", data={"target": target})

    def test_a_received_order_becomes_a_reagent_with_its_details(self):
        oid = self.order(vendor="NEB", catalog_number="M0273", quantity="2", unit="kit", received_on=days_ago(2))
        r = self.stock(oid, self.reagents)
        [rid] = items_named(self.reagents, item(oid)["name"])
        self.assertEqual(location(r), f"/inventory/{self.reagents}?open={rid}")  # its dialog opens there
        got = item(rid)
        self.assertEqual((got["vendor"], got["catalog_number"], got["quantity"], got["unit"], got["received_on"],
                          got["status"], got["owner"]), ("NEB", "M0273", "2", "kit", days_ago(2), "in stock", self.admin))
        self.assertEqual(attrs_of(oid)["stocked_as"], f"{self.reagents}:{got['number']}")

    def test_an_order_is_stocked_only_once(self):
        oid = self.order()
        self.stock(oid, self.reagents)
        r = self.post(self.a, f"/inventory/{self.orders}/items/{oid}/to-reagents", data={"target": self.reagents})
        self.assertFlash(r, "already in stock", "error")
        self.assertEqual(len(items_named(self.reagents, item(oid)["name"])), 1)

    def test_an_order_not_yet_received_cannot_be_stocked(self):
        oid = self.order(status="ordered")
        r = self.post(self.a, f"/inventory/{self.orders}/items/{oid}/to-reagents", data={"target": self.reagents})
        self.assertFlash(r, "received before adding it to stock", "error")
        self.assertEqual(items_named(self.reagents, item(oid)["name"]), [])

    def test_an_order_can_go_into_an_antibodies_inventory_but_not_samples(self):
        oid = self.order()
        self.stock(oid, self.samples)
        self.assertEqual(items_named(self.samples, item(oid)["name"]), [])
        self.stock(oid, self.antibodies)
        self.assertEqual(len(items_named(self.antibodies, item(oid)["name"])), 1)

    def test_member_cannot_stock_someone_elses_order_but_may_stock_their_own(self):
        theirs = self.order()
        self.stock(theirs, self.reagents, client=self.m)
        self.assertEqual(items_named(self.reagents, item(theirs)["name"]), [])
        mine = self.order(client=self.m)
        self.stock(mine, self.reagents, client=self.m)
        [rid] = items_named(self.reagents, item(mine)["name"])
        self.assertEqual(item(rid)["owner"], self.member)

    def test_stocking_is_one_undoable_batch(self):
        oid = self.order()
        self.stock(oid, self.reagents)
        batch_id, description, _ = newest_batch(self.admin)
        self.assertIn(f"order #{item(oid)['number']} to", description)
        self.post(self.a, f"/batches/{batch_id}/undo")
        self.assertEqual(items_named(self.reagents, item(oid)["name"]), [])
        self.assertNotIn("stocked_as", attrs_of(oid))

    def test_a_duplicate_of_a_stocked_order_can_be_stocked_again(self):
        oid = self.order(lot="L9")
        self.stock(oid, self.reagents)
        self.post(self.a, f"/inventory/{self.orders}/items/{oid}/duplicate")
        dup = items_named(self.orders, item(oid)["name"])[-1]
        self.assertNotEqual(dup, oid)
        self.assertNotIn("stocked_as", attrs_of(dup))
        self.assertEqual((item(dup)["status"], item(dup)["lot"], item(dup)["rack_id_fk"]), ("requested", "", None))


# ======================================================================= expiry

class ExpiryTests(InventoryCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.new_module(cls.a, "antibodies")

    def test_the_sheet_flags_expired_and_soon_expiring_items(self):
        past = self.make_item(self.a, self.key, uniq("Ab"), expires_on=days_ago(3))
        soon = self.make_item(self.a, self.key, uniq("Ab"), expires_on=days_ahead(10))
        later = self.make_item(self.a, self.key, uniq("Ab"), expires_on=days_ahead(90))
        html = self.get_ok(self.a, f"/inventory/{self.key}")
        self.assertIn('data-expired="true"', row_tag(html, past))
        self.assertIn('data-expiring="true"', row_tag(html, soon))
        self.assertIn('data-expired="false" data-expiring="false"', re.sub(r"\s+", " ", row_tag(html, later)))
        self.assertIn("expiry-expired", html)
        self.assertIn("expiry-soon", html)

    def test_an_invalid_expiry_date_is_refused_and_the_old_one_kept(self):
        aid = self.make_item(self.a, self.key, uniq("Ab"), expires_on=days_ahead(10))
        r = self.autosave(self.a, f"/inventory/{self.key}/items/{aid}/update", {"expires_on": "2026-02-30"})
        self.assertRefused(r)
        self.assertIn("not a date", r.get_json()["error"])
        self.assertEqual(item(aid)["expires_on"], days_ahead(10))

    def test_an_invalid_date_in_the_dialog_saves_nothing(self):
        aid = self.make_item(self.a, self.key, uniq("Ab"))
        name = item(aid)["name"]
        r = self.post(self.a, f"/inventory/{self.key}/items/save",
                      data={"id": str(aid), "name": "renamed", "expires_on": "2026-13-01"})
        self.assertFlash(r, "not a date", "error")
        self.assertEqual(item(aid)["name"], name)


# ======================================================================= configure

class ConfigureTests(InventoryCase):
    """Renaming a status or category in Configure moves the items with it,
    in one batch with the new settings (so it can be undone)."""

    def reagents_with_items(self):
        key = self.new_module(self.a, "reagents")
        stock = [self.make_item(self.a, key, uniq("R"), status="in stock", category="chemical") for _ in range(2)]
        empty = self.make_item(self.a, key, uniq("R"), status="empty", category="buffer")
        return key, stock, empty

    def test_the_configure_page_shows_each_status_with_how_many_use_it(self):
        key, stock, _ = self.reagents_with_items()
        html = self.get_ok(self.a, f"/inventory/{key}/configure")
        self.assertIn('name="statuses_0_was" value="in stock"', html)
        self.assertIn(f'choice-usage">{len(stock)} reagents', html)
        self.assertIn('name="categories_0_was" value="chemical"', html)

    def test_renaming_a_status_moves_its_items_to_the_new_name(self):
        key, stock, empty = self.reagents_with_items()
        received = {i: item(i)["received_on"] for i in stock}
        r = self.post(self.a, f"/inventory/{key}/configure", data=self.configure_form(key, statuses=[
            ("available", "in stock", False, ""), ("low", "low", False, ""),
            ("empty", "empty", False, ""), ("discarded", "discarded", False, "")]))
        self.assertFlash(r, "Rename status ‘in stock’ → ‘available’ (2 reagents)", "success")
        self.assertEqual(settings_of(key)["statuses"], ["available", "low", "empty", "discarded"])
        self.assertEqual([item(i)["status"] for i in stock], ["available", "available"])
        self.assertEqual(status_count(key, "in stock"), 0)
        # A relabel is not a status change: no dates stamped or cleared.
        self.assertEqual({i: item(i)["received_on"] for i in stock}, received)
        self.assertIn("used_up_on", attrs_of(empty))

    def test_undoing_a_status_rename_puts_the_list_and_the_items_back(self):
        key, stock, _ = self.reagents_with_items()
        before = settings_of(key)["statuses"]
        self.post(self.a, f"/inventory/{key}/configure", data=self.configure_form(key, statuses=[
            ("available", "in stock", False, ""), ("low", "low", False, ""),
            ("empty", "empty", False, ""), ("discarded", "discarded", False, "")]))
        batch_id, description, _ = newest_batch(self.admin)
        self.assertIn("Rename status", description)
        self.assertEqual(count("audit_log", "batch_id_fk=? and table_name='inventory_modules'", batch_id), 1)
        r = self.post(self.a, f"/batches/{batch_id}/undo")
        self.assertFlash(r, "Undid", "success")
        self.assertEqual(settings_of(key)["statuses"], before)
        self.assertEqual([item(i)["status"] for i in stock], ["in stock", "in stock"])

    def test_removing_a_used_status_without_a_replacement_is_refused(self):
        key, _, empty = self.reagents_with_items()
        before = settings_of(key)
        r = self.post(self.a, f"/inventory/{key}/configure", data=self.configure_form(key, statuses=[
            ("in stock", "in stock", False, ""), ("low", "low", False, ""),
            ("empty", "empty", True, ""), ("discarded", "discarded", False, "")]))
        self.assertFlash(r, "still has the status “empty”", "error")
        self.assertEqual(settings_of(key), before)
        self.assertEqual(item(empty)["status"], "empty")

    def test_removing_a_used_status_with_a_replacement_moves_its_items(self):
        key, _, empty = self.reagents_with_items()
        r = self.post(self.a, f"/inventory/{key}/configure", data=self.configure_form(key, statuses=[
            ("in stock", "in stock", False, ""), ("low", "low", False, ""),
            ("empty", "empty", True, "discarded"), ("discarded", "discarded", False, "")]))
        self.assertFlash(r, "Replace status ‘empty’ → ‘discarded’", "success")
        self.assertEqual(settings_of(key)["statuses"], ["in stock", "low", "discarded"])
        self.assertEqual(item(empty)["status"], "discarded")

    def test_swapping_two_status_names_swaps_their_items(self):
        key, stock, _ = self.reagents_with_items()
        low = self.make_item(self.a, key, uniq("R"), status="low")
        self.post(self.a, f"/inventory/{key}/configure", data=self.configure_form(key, statuses=[
            ("low", "in stock", False, ""), ("in stock", "low", False, ""),
            ("empty", "empty", False, ""), ("discarded", "discarded", False, "")]))
        self.assertEqual([item(i)["status"] for i in stock], ["low", "low"])
        self.assertEqual(item(low)["status"], "in stock")

    def test_renaming_a_category_relabels_its_items(self):
        key, stock, empty = self.reagents_with_items()
        cats = settings_of(key)["categories"]
        r = self.post(self.a, f"/inventory/{key}/configure", data=self.configure_form(
            key, categories=[("chemicals" if c == "chemical" else c, c, False, "") for c in cats]))
        self.assertFlash(r, "Rename kind ‘chemical’ → ‘chemicals’ (2 reagents)")
        self.assertEqual([item(i)["category"] for i in stock], ["chemicals", "chemicals"])
        self.assertEqual(item(empty)["category"], "buffer")

    def test_adding_a_status_saves_without_a_batch(self):
        key, _, _ = self.reagents_with_items()
        before = newest_batch(self.admin)
        st = settings_of(key)["statuses"]
        self.post(self.a, f"/inventory/{key}/configure", data=self.configure_form(
            key, statuses=[(v, v, False, "") for v in st] + [("on order", "", False, "")]))
        self.assertEqual(settings_of(key)["statuses"], st + ["on order"])
        self.assertEqual(newest_batch(self.admin), before)

    def test_a_plain_text_status_list_is_still_accepted(self):
        key = self.new_module(self.a, "custom")
        form = self.configure_form(key)
        for k in [k for k in form if k.startswith("statuses_")]:
            del form[k]
        form["statuses"] = "todo\ndoing, done"
        self.post(self.a, f"/inventory/{key}/configure", data=form)
        self.assertEqual(settings_of(key)["statuses"], ["todo", "doing", "done"])

    def test_two_new_columns_with_the_same_name_get_their_own_keys(self):
        key = self.new_module(self.a, "custom")
        form = self.configure_form(key, field_count="2", field_0_label="Clone", field_0_type="text",
                                   field_1_label="Clone", field_1_type="text", field_1_width="wide")
        self.post(self.a, f"/inventory/{key}/configure", data=form)
        fields = settings_of(key)["fields"]
        self.assertEqual([f["label"] for f in fields], ["Clone", "Clone"])
        self.assertEqual(len({f["key"] for f in fields}), 2)
        self.assertEqual(fields[1]["width"], 130)  # junk width falls back

    def test_a_member_cannot_configure_an_inventory_they_did_not_create(self):
        key = self.new_module(self.a, "custom")
        label = one("select label from inventory_modules where key=?", key)
        r = self.m.get(f"/inventory/{key}/configure")
        self.assertEqual(location(r), f"/inventory/{key}")
        self.post(self.m, f"/inventory/{key}/configure", data=self.configure_form(key, label="Hijacked", enabled="0"))
        self.assertEqual(row("select label, enabled from inventory_modules where key=?", key), (label, 1))
        self.assertNotIn(f"/inventory/{key}/configure", self.get_ok(self.m, f"/inventory/{key}"))

    def test_a_member_may_configure_an_inventory_they_created(self):
        key = self.new_module(self.m, "custom")
        self.get_ok(self.m, f"/inventory/{key}/configure")
        new_label = uniq("Renamed ")
        mid = one("select id from inventory_modules where key=?", key)
        self.post(self.m, f"/inventory/{key}/configure", data=self.configure_form(key, label=new_label))
        self.assertEqual(one("select label from inventory_modules where id=?", mid), new_label)

    def test_configure_can_turn_on_a_board_for_a_custom_list(self):
        key = self.new_module(self.a, "custom")
        form = self.configure_form(key, statuses=[("working", "", False, ""), ("broken", "", False, "")])
        form["features"] = ["sharing", "board"]
        self.post(self.a, f"/inventory/{key}/configure", data=form)
        iid = self.make_item(self.a, key, uniq("Centrifuge"))
        self.assertEqual(item(iid)["status"], "working")  # first status is the default
        html = self.get_ok(self.a, f"/inventory/{key}")
        self.assertIn("data-board", html)


# ======================================================================= add many

class AddManyTests(InventoryCase):
    """How many > 1, or pasted names: one batch; none created if any is refused."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.new_module(cls.a, "reagents")

    def add_many(self, client=None, **fields):
        return self.post(client or self.a, f"/inventory/{self.key}/items/save", data={"id": "", **fields})

    def numbers(self, name):
        return [item(i)["number"] for i in items_named(self.key, name)]

    def test_add_many_makes_identical_items_with_consecutive_numbers_in_one_batch(self):
        name = uniq("Aliquot")
        first = one("select coalesce(max(number), 0) + 1 from inventory_items where module_id_fk=?", module_id(self.key))
        r = self.add_many(name=name, count="5", status="low", category="buffer", vendor="Sigma", is_shared="1")
        self.assertFlash(r, f"Created #{first}–#{first + 4} (5 reagents)", "success")
        ids = items_named(self.key, name)
        self.assertEqual(self.numbers(name), list(range(first, first + 5)))
        self.assertEqual({(item(i)["status"], item(i)["category"], item(i)["vendor"], item(i)["is_shared"]) for i in ids},
                         {("low", "buffer", "Sigma", 1)})
        batch_id, description, action = newest_batch(self.admin)
        self.assertEqual((description, action), ("New reagents ×5", "create"))
        self.assertEqual(count("audit_log", "batch_id_fk=? and table_name='inventory_items'", batch_id), 5)

    def test_add_many_fills_a_box_side_by_side_skipping_taken_cells(self):
        bid = self.make_rack(self.a, self.key, rows=3, cols=4)
        self.make_item(self.a, self.key, uniq("blocker"), rack_id=str(bid), position="A3")
        name = uniq("Aliquot")
        self.add_many(name=name, count="5", rack_id=str(bid), position="A2")
        cells = [(item(i)["rack_row"], item(i)["rack_col"]) for i in items_named(self.key, name)]
        self.assertEqual(cells, [(1, 2), (1, 4), (2, 1), (2, 2), (2, 3)])

    def test_add_many_says_what_did_not_fit(self):
        bid = self.make_rack(self.a, self.key, rows=3, cols=4)
        name = uniq("Overflow")
        r = self.add_many(name=name, count="10", rack_id=str(bid), position="C1")
        self.assertFlash(r, "room for 4 of 10 from C1: 6 did not fit", "error")
        ids = items_named(self.key, name)
        self.assertEqual(len(ids), 10)
        self.assertTrue(all(item(i)["rack_id_fk"] == bid for i in ids))
        self.assertEqual(sum(1 for i in ids if item(i)["rack_row"]), 4)

    def test_undoing_add_many_removes_the_whole_batch(self):
        name = uniq("Loose")
        self.add_many(name=name, count="3")
        self.assertEqual(len(items_named(self.key, name)), 3)
        batch_id, _, _ = newest_batch(self.admin)
        self.post(self.a, f"/batches/{batch_id}/undo")
        self.assertEqual(items_named(self.key, name), [])

    def test_pasted_names_make_one_item_per_line(self):
        a, b, c = uniq("S-"), uniq("S-"), uniq("S-")
        ignored = uniq("ignored")
        self.add_many(name=ignored, count="1", names=f"{a}\n\n  {b} \n{c}\n", category="kit")
        got = rows("select name, category from inventory_items where module_id_fk=? and name in (?,?,?) order by number",
                   module_id(self.key), a, b, c)
        self.assertEqual(got, [(a, "kit"), (b, "kit"), (c, "kit")])
        self.assertEqual(items_named(self.key, ignored), [])

    def test_add_many_refuses_counts_outside_one_to_fifty(self):
        for bad in ("0", "51", "abc"):
            name = uniq("Bad")
            r = self.add_many(name=name, count=bad)
            self.assertFlash(r, "between 1 and 50", "error")
            self.assertEqual(items_named(self.key, name), [], bad)

    def test_more_than_fifty_pasted_names_are_refused(self):
        prefix = uniq("X")
        r = self.add_many(names="\n".join(f"{prefix}-{i}" for i in range(51)))
        self.assertFlash(r, "at most 50", "error")
        self.assertEqual(count("inventory_items", "name like ?", f"{prefix}-%"), 0)

    def test_a_bad_start_position_creates_nothing(self):
        bid = self.make_rack(self.a, self.key, rows=3, cols=3)
        name = uniq("Bad")
        r = self.add_many(name=name, count="3", rack_id=str(bid), position="Z9")
        self.assertFlash(r, "“Z9” is not a position", "error")
        self.assertEqual(items_named(self.key, name), [])

    def test_one_refused_field_creates_none_of_them(self):
        name = uniq("BadDate")
        r = self.add_many(name=name, count="3", expires_on="tomorrow")
        self.assertFlash(r, "“tomorrow” is not a date", "error")
        self.assertEqual(items_named(self.key, name), [])

    def test_a_members_add_many_for_someone_else_belongs_to_the_member(self):
        name = uniq("Mine")
        r = self.add_many(self.m, name=name, count="2", owner=self.admin)
        self.assertEqual({item(i)["owner"] for i in items_named(self.key, name)}, {self.member})
        self.assertEqual(sum(1 for _, text in flashes(r) if "Only an admin can add" in text), 1)

    def test_editing_an_item_ignores_how_many(self):
        rid = self.make_item(self.a, self.key, uniq("One"))
        self.add_many(id=str(rid), name=item(rid)["name"], count="5", notes="edited")
        self.assertEqual(items_named(self.key, item(rid)["name"]), [rid])
        self.assertEqual(item(rid)["notes"], "edited")


# ======================================================================= CSV import

class CsvImportTests(InventoryCase):
    """/import/order: orders CSV into an orders inventory."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.orders = cls.new_module(cls.a, "orders")

    def upload(self, client, csv_text: str, module: str | None = None, dry_run: bool = False):
        data = {"file": (io.BytesIO(csv_text.encode()), "orders.csv")}
        if dry_run:
            data["dry_run"] = "1"
        return client.post(f"/import/order?module={module or self.orders}", data=data,
                           content_type="multipart/form-data")

    def by_name(self, name):
        found = items_named(self.orders, name)
        return item(found[0]) if found else None

    def test_csv_maps_header_aliases_and_normalises_status(self):
        a, b = uniq("DNase"), uniq("RNase")
        csv = ("item,vendor_name,catalog,quantity,status,units,price,requester\n"
               f"{a},NEB,M0303,1,Received,kit,120,{self.member}\n"
               f"{b},Thermo,EN0531,,pending,vial,,\n")
        body = self.upload(self.a, csv).get_json()
        self.assertEqual((body["ok"], body["count"], body["errors"]), (True, 2, []))
        got_a, got_b = self.by_name(a), self.by_name(b)
        self.assertEqual((got_a["vendor"], got_a["catalog_number"], got_a["unit"], got_a["status"], got_a["owner"]),
                         ("NEB", "M0303", "kit", "received", self.member))
        self.assertEqual(json.loads(got_a["attrs"]), {"price": "120"})
        self.assertEqual(got_a["received_on"], T)  # received stamps the date
        self.assertEqual((got_b["status"], got_b["quantity"], got_b["owner"]), ("requested", "1", self.admin))

    def test_csv_bad_rows_are_reported_and_the_good_ones_imported(self):
        good = uniq("Good")
        csv = ("item_name,received_on\n"
               f"{good},{days_ago(3)}\n"
               ",\n"
               f"{uniq('BadDate')},01/09/2026\n")
        body = self.upload(self.a, csv).get_json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(len(body["errors"]), 2, body)
        self.assertTrue(body["errors"][0].startswith("row 3: no item name"), body["errors"])
        self.assertIn("row 4", body["errors"][1])
        self.assertIn("01/09/2026", body["errors"][1])
        self.assertEqual(self.by_name(good)["received_on"], days_ago(3))

    def test_csv_dry_run_writes_nothing(self):
        name = uniq("Dry")
        body = self.upload(self.a, f"item_name,status\n{name},ordered\n", dry_run=True).get_json()
        self.assertEqual((body["count"], body["dry_run"]), (1, True))
        self.assertEqual(body["preview"][0]["status"], "ordered")
        self.assertIsNone(self.by_name(name))

    def test_csv_orders_cannot_go_into_a_reagents_inventory(self):
        reagents = self.new_module(self.a, "reagents")
        name = uniq("Wrong")
        r = self.upload(self.a, f"item_name\n{name}\n", module=reagents)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(items_named(reagents, name), [])

    def test_csv_import_is_one_undoable_batch(self):
        a, b = uniq("U"), uniq("U")
        self.upload(self.a, f"item_name\n{a}\n{b}\n")
        batch_id, description, _ = newest_batch(self.admin)
        self.assertTrue(description.startswith("import orders into"), description)
        self.post(self.a, f"/batches/{batch_id}/undo")
        self.assertIsNone(self.by_name(a))
        self.assertIsNone(self.by_name(b))

    def test_a_members_csv_rows_are_theirs(self):
        name = uniq("Planted")
        self.upload(self.m, f"item_name,requester,status,received_on\n{name},{self.admin},RECEIVED,2026-09-01\n")
        got = self.by_name(name)
        self.assertEqual((got["owner"], got["status"], got["received_on"]), (self.member, "received", "2026-09-01"))

    def test_csv_import_refuses_unknown_entities_and_missing_files(self):
        r = self.a.post("/import/reagent", data={}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        r = self.a.post(f"/import/order?module={self.orders}", data={}, content_type="multipart/form-data")
        self.assertEqual((r.status_code, r.get_json()["error"]), (400, "missing file"))


# ======================================================================= bulk, duplicate

class SampleMeasureTests(InventoryCase):
    def test_new_samples_have_number_columns_for_what_was_measured(self):
        key = self.new_module(self.a, "samples")
        fields = {f["key"]: f["type"] for f in settings_of(key)["fields"]}
        for k in ("concentration", "a260_280", "a260_230", "volume_ul"):
            self.assertEqual(fields[k], "number", k)
        r = self.post(self.m, f"/inventory/{key}/items/save",
                      data={"id": "", "name": uniq("RNA "), "attr_concentration": "about 200"})
        self.assertFlash(r, "is a number column", "error")
        comma = self.make_item(self.m, key, uniq("RNA "), attr_a260_280="2,01")
        self.assertEqual(attrs_of(comma)["a260_280"], "2.01")
        rid = self.make_item(self.m, key, uniq("RNA "), attr_concentration="212.4", attr_conc_unit="ng/µL",
                             attr_a260_280="2.05")
        self.assertEqual((attrs_of(rid)["concentration"], attrs_of(rid)["a260_280"]), ("212.4", "2.05"))


class BulkTests(InventoryCase):
    """Ticked rows: one batch; rows the user may not change are skipped."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.new_module(cls.a, "reagents")

    def bulk(self, client, action, ids, value=""):
        return self.post(client, f"/inventory/{self.key}/items/bulk",
                         data={"action": action, "value": value, "selected_ids": [str(i) for i in ids]})

    def reagents(self, n=2, client=None, **fields):
        return [self.make_item(client or self.a, self.key, uniq("R"), **{"status": "in stock", **fields})
                for _ in range(n)]

    def test_bulk_status_is_one_batch_that_undo_reverses(self):
        ids = self.reagents(3)
        r = self.bulk(self.a, "status", ids, "low")
        self.assertFlash(r, "Set the status of 3 reagents", "success")
        self.assertEqual({item(i)["status"] for i in ids}, {"low"})
        batch_id, description, _ = newest_batch(self.admin)
        self.assertEqual(description, "status ×3 reagents")
        self.post(self.a, f"/batches/{batch_id}/undo")
        self.assertEqual({item(i)["status"] for i in ids}, {"in stock"})

    def test_bulk_status_to_a_terminal_status_stamps_the_day(self):
        [rid] = self.reagents(1)
        self.bulk(self.a, "status", [rid], "EMPTY")
        self.assertEqual(item(rid)["status"], "empty")
        self.assertEqual(attrs_of(rid).get("used_up_on"), T)

    def test_set_field_with_nothing_typed_clears_only_when_confirmed(self):
        ids = self.reagents(2, lot="L-9")
        r = self.post(self.a, f"/inventory/{self.key}/items/bulk", data={
            "action": "field", "field": "lot", "value": "", "selected_ids": [str(i) for i in ids]})
        self.assertFlash(r, "Type what to set Lot to", "error")
        self.assertEqual({item(i)["lot"] for i in ids}, {"L-9"})
        self.post(self.a, f"/inventory/{self.key}/items/bulk", data={
            "action": "field", "field": "lot", "value": "", "clear": "1", "selected_ids": [str(i) for i in ids]})
        self.assertEqual({item(i)["lot"] for i in ids}, {""})

    def test_move_to_unplace(self):
        bid = self.make_rack(self.a, self.key)
        ids = self.reagents(1, rack_id=str(bid))
        self.bulk(self.a, "rack", ids, "unplace")
        self.assertIsNone(item(ids[0])["rack_id_fk"])

    def test_used_up_frees_the_box_position_and_notes_where_it_was(self):
        bid = self.make_rack(self.a, self.key, rows=3, cols=3)
        box = one("select name from inventory_racks where id=?", bid)
        ids = self.reagents(2, rack_id=str(bid))
        self.bulk(self.a, "status", ids, "empty")
        self.assertEqual({item(i)["rack_id_fk"] for i in ids}, {None})
        self.assertEqual(one("select location_note from inventory_items where id=?", ids[0]), f"was in {box} · A1")
        # The dialog still shows the old box and position: that doesn't put it back.
        [rid] = self.reagents(1, rack_id=str(bid), position="C3")
        self.post(self.a, f"/inventory/{self.key}/items/save", data={
            "id": str(rid), "name": item(rid)["name"], "status": "discarded", "rack_id": str(bid), "position": "C3"})
        self.assertEqual(item(rid)["rack_id_fk"], None)
        # Low stock stays where it is.
        [low] = self.reagents(1, rack_id=str(bid))
        self.bulk(self.a, "status", [low], "low")
        self.assertEqual(item(low)["rack_id_fk"], bid)

    def test_what_goes_in_a_box_takes_the_box_s_stored_at(self):
        minus80 = self.make_rack(self.a, self.key, stored_at="−80 °C")
        ln2 = self.make_rack(self.a, self.key, stored_at="LN₂")
        ids = self.reagents(2, rack_id=str(minus80))
        self.assertEqual({attrs_of(i).get("storage_temp") for i in ids}, {"−80 °C"})
        self.bulk(self.a, "rack", ids, str(ln2))                         # Move to box
        self.assertEqual({attrs_of(i).get("storage_temp") for i in ids}, {"LN₂"})
        batch_id, _description, _ = newest_batch(self.admin)
        self.post(self.a, f"/batches/{batch_id}/undo")                  # undo puts both back
        self.assertEqual({(item(i)["rack_id_fk"], attrs_of(i).get("storage_temp")) for i in ids}, {(minus80, "−80 °C")})
        # Moving the box itself moves what is in it.
        name = one("select name from inventory_racks where id=?", minus80)
        self.post(self.a, f"/inventory/{self.key}/racks/save", data={
            "id": str(minus80), "name": name, "rows": "9", "cols": "9", "stored_at": "−20 °C", **GRID_NAMING})
        self.assertEqual({attrs_of(i).get("storage_temp") for i in ids}, {"−20 °C"})

    def test_set_field_sets_any_column_on_the_ticked_rows(self):
        ids = self.reagents(3)
        r = self.post(self.a, f"/inventory/{self.key}/items/bulk", data={
            "action": "field", "field": "attr_storage_temp", "value": "−80 °C", "selected_ids": [str(i) for i in ids]})
        self.assertFlash(r, "Set Stored at on 3 reagents", "success")
        self.assertEqual({attrs_of(i).get("storage_temp") for i in ids}, {"−80 °C"})
        self.post(self.a, f"/inventory/{self.key}/items/bulk", data={
            "action": "field", "field": "lot", "value": "L2231", "selected_ids": [str(i) for i in ids]})
        self.assertEqual({item(i)["lot"] for i in ids}, {"L2231"})
        batch_id, _description, _ = newest_batch(self.admin)
        self.post(self.a, f"/batches/{batch_id}/undo")                  # one batch, undoable
        self.assertEqual({item(i)["lot"] for i in ids}, {""})

    def test_set_field_refuses_a_value_the_column_doesnt_take(self):
        ids = self.reagents(1)
        r = self.post(self.a, f"/inventory/{self.key}/items/bulk", data={
            "action": "field", "field": "expires_on", "value": "someday", "selected_ids": [str(i) for i in ids]})
        self.assertIsNone(item(ids[0])["expires_on"])
        r = self.post(self.a, f"/inventory/{self.key}/items/bulk", data={
            "action": "field", "field": "no_such_column", "value": "x", "selected_ids": [str(i) for i in ids]})
        self.assertFlash(r, "Pick the column to set", "error")

    def test_bulk_refuses_an_unknown_status(self):
        ids = self.reagents(1)
        r = self.bulk(self.a, "status", ids, "vanished")
        self.assertFlash(r, "“vanished” is not a status here", "error")
        self.assertEqual(item(ids[0])["status"], "in stock")

    def test_member_bulk_edit_skips_someone_elses_private_items(self):
        private = self.reagents(1, owner=self.admin, is_shared="0")[0]
        common = self.reagents(1, owner=self.admin, is_shared="1")[0]
        r = self.bulk(self.m, "status", [private, common], "low")
        self.assertFlash(r, "1 belong to someone else and were left alone")
        self.assertEqual((item(private)["status"], item(common)["status"]), ("in stock", "low"))

    def test_member_bulk_owner_changes_only_their_own_items(self):
        private = self.reagents(1, owner=self.admin, is_shared="0")[0]
        common = self.reagents(1, owner=self.admin, is_shared="1")[0]
        mine = self.reagents(1, client=self.m)[0]
        r = self.bulk(self.m, "owner", [private, common, mine], self.other)
        self.assertFlash(r, "2 left alone: only their owner or an admin can do that")
        self.assertEqual([item(i)["owner"] for i in (private, common, mine)], [self.admin, self.admin, self.other])

    def test_member_cannot_bulk_delete_or_make_personal_a_lab_common_item(self):
        common = self.reagents(1, owner=self.admin, is_shared="1")[0]
        r = self.bulk(self.m, "shared", [common], "0")
        self.assertTrue(errors(r))
        self.bulk(self.m, "delete", [common])
        self.assertEqual(item(common)["is_shared"], 1)

    def test_bulk_move_into_a_box_takes_free_cells(self):
        bid = self.make_rack(self.a, self.key, rows=2, cols=2)
        self.make_item(self.a, self.key, uniq("R"), rack_id=str(bid), position="A1")
        ids = self.reagents(4)
        r = self.bulk(self.a, "rack", ids, str(bid))
        self.assertFlash(r, "is full")
        cells = [(item(i)["rack_id_fk"], item(i)["rack_row"], item(i)["rack_col"]) for i in ids]
        self.assertEqual(cells, [(bid, 1, 2), (bid, 2, 1), (bid, 2, 2), (bid, None, None)])

    def test_bulk_delete_is_undone_by_its_batch(self):
        ids = self.reagents(2)
        names = [item(i)["name"] for i in ids]
        self.bulk(self.a, "delete", ids)
        self.assertEqual([item(i) for i in ids], [None, None])
        batch_id, _, action = newest_batch(self.admin)
        self.assertEqual(action, "delete")
        self.post(self.a, f"/batches/{batch_id}/undo")
        self.assertEqual([item(i)["name"] for i in ids], names)

    def test_bulk_with_nothing_ticked_says_so(self):
        r = self.bulk(self.a, "status", [], "low")
        self.assertFlash(r, "No reagents selected", "error")

    def test_a_duplicate_has_no_position_the_first_status_and_a_new_number(self):
        bid = self.make_rack(self.a, self.key)
        rid = self.make_item(self.a, self.key, uniq("R"), status="low", lot="L1", vendor="Sigma",
                             rack_id=str(bid), position="A1", is_shared="1")
        r = self.post(self.a, f"/inventory/{self.key}/items/{rid}/duplicate")
        copy_id = items_named(self.key, item(rid)["name"])[-1]
        copy = item(copy_id)
        self.assertFlash(r, f"as #{copy['number']}", "success")
        self.assertEqual((copy["status"], copy["lot"], copy["vendor"], copy["is_shared"], copy["rack_id_fk"]),
                         ("in stock", "", "Sigma", 1, None))
        self.assertGreater(copy["number"], item(rid)["number"])


# ======================================================================= samples

class SampleSourceTests(InventoryCase):
    """A sample can name the animal it came from (attr_source_kind/_ref)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.new_module(cls.a, "samples")

    def test_the_source_shows_the_mouse_s_ear_tag_and_follows_it(self):
        """Type the number; what is written on that mouse comes with it, and
        keeps up when the colony changes it. The tag belongs to the mouse,
        so it is not edited from here."""
        mouse_row = self.make_mouse(self.a, self.admin)
        mouse_id = str(one("select mouse_id from mice where id=?", mouse_row))
        self.autosave(self.a, f"/colony/mice/{mouse_row}/update", {"ear_tag": "RF"})
        name = uniq("S-tagged")
        self.post(self.a, f"/inventory/{self.key}/items/save", data={
            "id": "", "name": name, "attr_source_kind": "mouse", "attr_source_ref": mouse_id})
        html = self.get_ok(self.a, f"/inventory/{self.key}")
        self.assertIn('title="Custom tag RF"', html)
        self.assertIn(f"Open mouse {mouse_id} (custom tag RF) in the colony", html)
        # Re-tag the mouse: every sample of it says the new mark, with
        # nothing to update here.
        self.autosave(self.a, f"/colony/mice/{mouse_row}/update", {"ear_tag": "LB"})
        self.assertIn('title="Custom tag LB"', self.get_ok(self.a, f"/inventory/{self.key}"))

    def test_the_custom_tag_is_a_column_after_the_source(self):
        """Read from the colony, next to the mouse it belongs to; and the
        page knows where this person's mouse-sheet Columns choice is kept,
        so hiding it there hides it here (templates/_mouse_sheet.html)."""
        mouse_row = self.make_mouse(self.a, self.admin)
        mouse_id = str(one("select mouse_id from mice where id=?", mouse_row))
        tag = uniq("TT")
        self.autosave(self.a, f"/colony/mice/{mouse_row}/update", {"ear_tag": tag})
        self.post(self.a, f"/inventory/{self.key}/items/save", data={
            "id": "", "name": uniq("S-col"), "attr_source_kind": "mouse", "attr_source_ref": mouse_id})
        html = self.get_ok(self.a, f"/inventory/{self.key}")
        head = html.split('data-sort-key="attr_source"', 1)[1]
        self.assertLess(head.index("data-mouse-tag"), head.index("</tr>"))
        self.assertIn(f'<td class="ident" data-mouse-tag>{tag}</td>', html)
        self.assertIn('localStorage.getItem("dt:mice-v4:hidden")', html)
        self.assertIn("hidden.includes(2)", html)
        # A database with no source column has none.
        reagents = self.new_module(self.a, "reagents")
        self.assertNotIn("data-mouse-tag", self.get_ok(self.a, f"/inventory/{reagents}"))

    def test_the_source_is_edited_in_the_sheet_like_any_other_cell(self):
        """Typing in the row, not opening the record: the two boxes autosave
        the way every other cell does."""
        mouse_row = self.make_mouse(self.a, self.admin)
        mouse_id = str(one("select mouse_id from mice where id=?", mouse_row))
        name = uniq("S-inline")
        self.post(self.a, f"/inventory/{self.key}/items/save", data={"id": "", "name": name, "category": "tissue"})
        [sid] = items_named(self.key, name)
        self.assertSaved(self.autosave(self.a, f"/inventory/{self.key}/items/{sid}/update",
                                       {"attr_source_kind": "mouse", "attr_source_ref": mouse_id}))
        self.assertEqual(attrs_of(sid)["source"], {"kind": "mouse", "ref": mouse_id})
        # And changing just the ID keeps the colony it was already in.
        self.assertSaved(self.autosave(self.a, f"/inventory/{self.key}/items/{sid}/update",
                                       {"attr_source_kind": "mouse", "attr_source_ref": "7"}))
        self.assertEqual(attrs_of(sid)["source"], {"kind": "mouse", "ref": "7"})

    def test_the_whole_harvest_takes_its_source_in_one_go(self):
        """One mouse gives many samples: set which mouse on all of them at
        once, rather than opening each."""
        mouse_row = self.make_mouse(self.a, self.admin)
        mouse_id = str(one("select mouse_id from mice where id=?", mouse_row))
        names = [uniq("S-organ") for _ in range(3)]
        for name in names:
            self.post(self.a, f"/inventory/{self.key}/items/save",
                      data={"id": "", "name": name, "category": "tissue"})
        ids = [items_named(self.key, name)[0] for name in names]
        r = self.post(self.a, f"/inventory/{self.key}/items/bulk", data={
            "action": "field", "field": "attr_source", "source_kind": "mouse", "value": mouse_id,
            "selected_ids": [str(i) for i in ids]})
        self.assertNoErrors(r)
        for sid in ids:
            self.assertEqual(attrs_of(sid)["source"], {"kind": "mouse", "ref": mouse_id})

    def test_the_source_column_is_one_the_bulk_bar_offers(self):
        html = self.get_ok(self.a, f"/inventory/{self.key}")
        self.assertIn('value="attr_source" data-type="source"', html)
        self.assertIn('data-bulk-source', html)

    def test_a_mouse_that_is_not_in_the_colony_is_still_said_so_in_bulk(self):
        [sid] = [items_named(self.key, n)[0] for n in [uniq("S-ghost")]
                 if self.post(self.a, f"/inventory/{self.key}/items/save",
                              data={"id": "", "name": n, "category": "tissue"}) or True]
        r = self.post(self.a, f"/inventory/{self.key}/items/bulk", data={
            "action": "field", "field": "attr_source", "source_kind": "mouse", "value": "99999999",
            "selected_ids": [str(sid)]})
        self.assertFlash(r, "no mouse 99999999 in the colony", "info")
        self.assertEqual(attrs_of(sid)["source"], {"kind": "mouse", "ref": "99999999"})

    def test_a_sample_from_a_colony_mouse_links_back_to_it(self):
        mouse_row = self.make_mouse(self.a, self.admin)
        mouse_id = one("select mouse_id from mice where id=?", mouse_row)
        name = uniq("S-liver")
        r = self.post(self.a, f"/inventory/{self.key}/items/save", data={
            "id": "", "name": name, "category": "tissue", "attr_source_kind": "mouse",
            "attr_source_ref": str(mouse_id), "attr_collected_on": days_ago(1), "attr_amount": "30 mg"})
        self.assertEqual([k for k, _ in flashes(r) if k == "warning"], [])
        [sid] = items_named(self.key, name)
        self.assertEqual(attrs_of(sid)["source"], {"kind": "mouse", "ref": str(mouse_id)})
        self.assertEqual(attrs_of(sid)["collected_on"], days_ago(1))
        html = self.get_ok(self.a, f"/inventory/{self.key}")
        # The cell is editable in place: the colony chosen, the ID beside it.
        self.assertIn('<option value="mouse" data-list="inv-src-0" selected>', html)
        self.assertIn('name="attr_source_ref"', html)
        self.assertIn(f'title="Open mouse {mouse_id} in the colony"', html)

    def test_a_mouse_number_too_big_for_the_database_is_no_mouse_not_an_error(self):
        name = uniq("S-big")
        r = self.post(self.a, f"/inventory/{self.key}/items/save", data={
            "id": "", "name": name, "attr_source_kind": "mouse", "attr_source_ref": "99999999999"})
        self.assertFlash(r, "There is no mouse 99999999999 in the colony", "warning")
        self.assertEqual(len(items_named(self.key, name)), 1)
        html = self.get_ok(self.a, f"/inventory/{self.key}")   # and the sheet still opens
        self.assertIn('title="No mouse 99999999999 in the colony"', html)

    def test_a_sample_from_a_missing_mouse_is_saved_with_a_warning(self):
        ref = "99" + uniq("").replace("x", "0")  # digits no colony mouse has
        name = uniq("S-ghost")
        r = self.post(self.a, f"/inventory/{self.key}/items/save", data={
            "id": "", "name": name, "attr_source_kind": "mouse", "attr_source_ref": ref})
        self.assertFlash(r, f"There is no mouse {ref} in the colony", "warning")
        [sid] = items_named(self.key, name)
        self.assertEqual(attrs_of(sid)["source"]["ref"], ref)
        self.assertIn(f'title="No mouse {ref} in the colony"', self.get_ok(self.a, f"/inventory/{self.key}"))

    def test_a_non_mouse_source_is_not_checked_against_the_colony(self):
        name = uniq("S-fish")
        r = self.post(self.a, f"/inventory/{self.key}/items/save", data={
            "id": "", "name": name, "attr_source_kind": "other", "attr_source_ref": "field trip"})
        self.assertEqual([k for k, _ in flashes(r) if k == "warning"], [])
        [sid] = items_named(self.key, name)
        self.assertEqual(attrs_of(sid)["source"], {"kind": "other", "ref": "field trip"})

    def test_an_invalid_date_in_a_date_column_is_refused(self):
        sid = self.make_item(self.a, self.key, uniq("S"), attr_collected_on=days_ago(2))
        r = self.autosave(self.a, f"/inventory/{self.key}/items/{sid}/update", {"attr_collected_on": "yesterday"})
        self.assertRefused(r)
        self.assertEqual(attrs_of(sid)["collected_on"], days_ago(2))


# ======================================================================= inventories

class ModuleLifecycleTests(InventoryCase):
    """Creating inventories from presets or a custom list; deleting them."""

    def test_a_new_inventory_from_a_preset_starts_with_its_settings(self):
        from app import inventory as presets
        label = uniq("Orders ")
        key = self.new_module(self.a, "orders", label)
        got = row("select label, kind, item_noun, created_by from inventory_modules where key=?", key)
        self.assertEqual(got, (label, "orders", "order", self.admin))
        self.assertEqual(settings_of(key), presets.preset_settings("orders"))
        self.assertIn("board", settings_of(key)["features"])

    def test_a_new_custom_list_is_empty_and_uses_the_generic_nouns(self):
        key = self.new_module(self.m, "custom")
        s = settings_of(key)
        self.assertEqual((s["statuses"], s["categories"], s["fields"], s["features"]), ([], [], [], ["sharing"]))
        self.assertEqual(row("select kind, item_noun, created_by from inventory_modules where key=?", key),
                         ("custom", "item", self.member))
        iid = self.make_item(self.m, key, uniq("thing"))
        self.assertEqual(item(iid)["status"], "")

    def test_a_new_inventory_needs_a_name(self):
        before = count("inventory_modules")
        r = self.post(self.a, "/inventory/new", data={"preset": "custom", "label": "  "})
        self.assertFlash(r, "Give the inventory a name.", "error")
        self.assertEqual(count("inventory_modules"), before)

    def test_two_inventories_whose_names_make_the_same_address_get_different_keys(self):
        label = uniq("Equipment ")
        k1 = self.new_module(self.a, "custom", label)
        k2 = self.new_module(self.a, "custom", label + "!")
        self.assertNotEqual(k1, k2)
        self.assertTrue(k2.startswith(k1 + "_"), (k1, k2))

    def test_a_second_inventory_may_not_have_the_same_name(self):
        label = uniq("Equipment ")
        self.new_module(self.a, "custom", label)
        r = self.post(self.a, "/inventory/new", data={"preset": "custom", "label": label, "audience": "lab"})
        self.assertFlash(r, f"already a database called {label}", "error")
        self.assertEqual(count("inventory_modules", "label=?", label), 1)

    def test_the_new_module_and_configure_pages_render(self):
        self.assertIn("antibodies", self.get_ok(self.m, "/inventory/new?preset=antibodies"))
        key = self.new_module(self.a, "antibodies")
        self.make_item(self.a, key, uniq("Ab"))
        self.get_ok(self.m, f"/inventory/{key}")
        self.assertIn('name="field_count"', self.get_ok(self.a, f"/inventory/{key}/configure"))

    def test_an_unknown_inventory_is_not_found(self):
        self.assertEqual(self.a.get(f"/inventory/{uniq('nope')}").status_code, 404)

    def test_a_member_cannot_delete_an_inventory_they_did_not_create(self):
        label = uniq("Keep ")
        key = self.new_module(self.a, "custom", label)
        r = self.post(self.m, f"/inventory/{key}/delete", data={"confirm": label})
        self.assertFlash(r, "Only an admin, or whoever created it", "error")
        self.assertIsNotNone(module_id(key))

    def test_deleting_an_inventory_needs_its_name_typed(self):
        label = uniq("Keep ")
        key = self.new_module(self.a, "custom", label)
        r = self.post(self.a, f"/inventory/{key}/delete", data={"confirm": "yes"})
        self.assertFlash(r, "Type the name", "error")
        self.assertIsNotNone(module_id(key))

    def test_the_creator_can_delete_an_inventory_with_everything_in_it(self):
        label = uniq("Gone ")
        key = self.new_module(self.m, "reagents", label)
        mid = module_id(key)
        bid = self.make_rack(self.m, key)
        self.make_item(self.m, key, uniq("R"), rack_id=str(bid), position="A1")
        r = self.post(self.m, f"/inventory/{key}/delete", data={"confirm": label})
        self.assertFlash(r, f"Deleted {label} and everything in it.", "success")
        self.assertIsNone(module_id(key))
        self.assertEqual((count("inventory_items", "module_id_fk=?", mid), count("inventory_racks", "module_id_fk=?", mid)),
                         (0, 0))


if __name__ == "__main__":
    unittest.main()
