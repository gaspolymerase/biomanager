"""A rack's new layout and what is in it (app/rack_layout.py): a save that
renames the places of cages, or leaves some out, says whether each keeps
its position name or its cell, and is refused without; the rack dialog asks
first, from /racks/<kind>/<id>/layout."""
from __future__ import annotations

from tests.base import *  # noqa: F401,F403
from tests.base import AppTestCase, flashes, last_batch, one, row, uniq

from tests.test_api import Base as ApiBase

LETTERS_FIRST = {"naming_mode": "grid", "naming_rows": "letters", "naming_cols": "numbers",
                 "naming_order": "row_col", "naming_separator": "", "naming_start": "1"}
# The axes swapped: columns are letters, written first. The cage in row F,
# column 7 ("F7") is then in column G, row 6: "G6".
SWAPPED = {**LETTERS_FIRST, "naming_rows": "numbers", "naming_cols": "letters", "naming_order": "col_row"}


class Colony(AppTestCase):
    def make_rack(self, rows=10, cols=10, naming=LETTERS_FIRST) -> int:
        name = uniq("Rack")
        self.m.post("/colony/racks/save", data={"id": "", "name": name, "rows": str(rows), "cols": str(cols), **naming})
        return one("select id from mouse_racks where name=?", name)

    def save(self, rack, rows=10, cols=10, naming=SWAPPED, client=None, **extra):
        name = one("select name from mouse_racks where id=?", rack)
        return self.post(client or self.m, "/colony/racks/save",
                         {"id": str(rack), "name": extra.pop("name", name), "rows": str(rows), "cols": str(cols),
                          **naming, **extra})

    def place(self, rack, position) -> int:
        return self.make_cage(self.m, rack_id=str(rack), position=position)

    def where(self, cage) -> tuple:
        """(rack, row, col, the name of its place now)."""
        from app.app import cage_position_label
        from app.db import SessionLocal
        from app.models import CageRecord
        with SessionLocal() as s:
            c = s.get(CageRecord, cage)
            return c.rack_id_fk, c.rack_row, c.rack_col, cage_position_label(c)

    def preview(self, rack, rows=10, cols=10, naming=SWAPPED, name=None):
        r = self.m.post(f"/racks/mouse_rack/{rack}/layout",
                        data={"name": name or "x", "rows": str(rows), "cols": str(cols), **naming})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        return r.get_json()


class Transposing(Colony):
    """Rack B's axes swapped: cage 226 was at F7 and read G6 afterwards,
    though nobody had moved it."""

    def setUp(self):
        super().setUp()
        self.rack = self.make_rack()
        self.f7, self.j4, self.e1 = self.place(self.rack, "F7"), self.place(self.rack, "J4"), self.place(self.rack, "E1")
        self.a5 = self.place(self.rack, "A5")   # A5 swapped is E1: where E1's cage goes, if it keeps its name

    def test_the_dialog_is_shown_every_cage_and_both_names(self):
        p = self.preview(self.rack)
        rows = {a["now"]: (a["keep_names"], a["keep_cells"]) for a in p["affected"]}
        self.assertEqual(rows["F7"], ("F7", "G6"))
        self.assertEqual(rows["J4"], ("J4", "D10"))
        self.assertEqual(rows["E1"], ("E1", "A5"))
        self.assertEqual(p["default"], "keep_names")      # the naming changed
        self.assertEqual(p["off"], {"keep_names": 0, "keep_cells": 0})
        self.assertIn("4 cages", p["things"])

    def test_a_save_without_a_choice_is_refused_and_changes_nothing(self):
        r = self.save(self.rack)
        self.assertFlash(r, "Not saved", "error")
        self.assertIn('"rows": "letters"', one("select naming from mouse_racks where id=?", self.rack))
        self.assertEqual(self.where(self.f7)[3], "F7")

    def test_keep_names_moves_each_cage_to_the_cell_with_its_name(self):
        r = self.save(self.rack, cages="keep_names")
        self.assertFlash(r, "kept its position name", "success")
        self.assertEqual(self.where(self.f7), (self.rack, 7, 6, "F7"))
        self.assertEqual(self.where(self.j4), (self.rack, 4, 10, "J4"))
        self.assertEqual(self.where(self.e1), (self.rack, 1, 5, "E1"))
        self.assertEqual(self.where(self.a5)[3], "A5")

    def test_keep_cells_keeps_rows_and_columns_and_the_names_change(self):
        self.save(self.rack, cages="keep_cells")
        self.assertEqual(self.where(self.f7), (self.rack, 6, 7, "G6"))
        self.assertEqual(self.where(self.j4), (self.rack, 10, 4, "D10"))
        self.assertEqual(self.where(self.e1), (self.rack, 5, 1, "A5"))

    def test_undo_puts_the_layout_and_the_cages_back(self):
        self.save(self.rack, cages="keep_names")
        batch, description, _action, _table = last_batch()
        self.assertIn("kept its position name", description)
        self.post(self.a, f"/batches/{batch}/undo")
        self.assertIn('"rows": "letters"', one("select naming from mouse_racks where id=?", self.rack))
        self.assertEqual(self.where(self.f7), (self.rack, 6, 7, "F7"))
        self.assertEqual(self.where(self.j4), (self.rack, 10, 4, "J4"))


class Shrinking(Colony):
    def setUp(self):
        super().setUp()
        self.rack = self.make_rack()
        self.b2, self.j4, self.c9 = self.place(self.rack, "B2"), self.place(self.rack, "J4"), self.place(self.rack, "C9")

    def test_cages_outside_a_smaller_rack_are_listed_and_counted(self):
        p = self.preview(self.rack, rows=5, cols=5, naming=LETTERS_FIRST)
        self.assertEqual(sorted(a["now"] for a in p["affected"]), ["C9", "J4"])
        self.assertEqual(p["off"], {"keep_names": 2, "keep_cells": 2})
        self.assertEqual(p["default"], "keep_cells")      # same naming: only the size changed

    def test_they_stay_in_the_rack_with_no_position(self):
        r = self.save(self.rack, rows=5, cols=5, naming=LETTERS_FIRST, cages="keep_cells")
        self.assertFlash(r, "2 are now in the rack with no position")
        self.assertEqual(self.where(self.j4), (self.rack, None, None, ""))
        self.assertEqual(self.where(self.c9), (self.rack, None, None, ""))
        self.assertEqual(self.where(self.b2), (self.rack, 2, 2, "B2"))

    def test_keep_names_on_a_smaller_swapped_rack(self):
        # 10 × 5 swapped: columns A–E, rows 1–10. B2 is still a name, J4
        # (column J) is not; C9 is column C, row 9.
        self.save(self.rack, rows=10, cols=5, naming=SWAPPED, cages="keep_names")
        self.assertEqual(self.where(self.b2), (self.rack, 2, 2, "B2"))
        self.assertEqual(self.where(self.j4), (self.rack, None, None, ""))
        self.assertEqual(self.where(self.c9), (self.rack, 9, 3, "C9"))


class NothingToAsk(Colony):
    def test_renaming_a_rack_with_cages_asks_nothing(self):
        rack = self.make_rack()
        cage = self.place(rack, "F7")
        name = uniq("Renamed")
        self.assertEqual(self.preview(rack, naming=LETTERS_FIRST, name=name)["affected"], [])
        r = self.save(rack, naming=LETTERS_FIRST, name=name)
        self.assertFlash(r, f"Saved rack {name}", "success")
        self.assertEqual(self.where(cage)[3], "F7")

    def test_a_save_without_naming_fields_keeps_the_naming(self):
        rack = self.make_rack(naming=SWAPPED)
        cage = self.place(rack, "G6")
        r = self.post(self.m, "/colony/racks/save", {"id": str(rack), "name": uniq("R"), "rows": "10", "cols": "10"})
        self.assertNotIn("error", [k for k, _t in flashes(r)])
        self.assertEqual(self.where(cage)[3], "G6")

    def test_an_empty_rack_changes_without_asking(self):
        rack = self.make_rack()
        r = self.save(rack)
        self.assertFlash(r, "Saved rack", "success")
        self.assertIn('"rows": "numbers"', one("select naming from mouse_racks where id=?", rack))

    def test_growing_a_rack_asks_nothing(self):
        rack = self.make_rack(rows=5, cols=5)
        cage = self.place(rack, "E5")
        self.assertEqual(self.preview(rack, rows=8, cols=12, naming=LETTERS_FIRST)["affected"], [])
        self.save(rack, rows=8, cols=12, naming=LETTERS_FIRST)
        self.assertEqual(self.where(cage), (rack, 5, 5, "E5"))

    def test_a_separator_alone_moves_nothing(self):
        rack = self.make_rack()
        self.place(rack, "F7")
        self.assertEqual(self.preview(rack, naming={**LETTERS_FIRST, "naming_separator": "-"})["affected"], [])


class SharedPositions(Colony):
    def test_two_active_cages_in_one_place_are_counted_after_either_choice(self):
        rack = self.make_rack()
        for _ in range(2):
            cage = self.place(rack, "F7")
            self.make_mouse(self.m, self.member, cage=one("select cage_id from mouse_cages where id=?", cage))
        self.place(rack, "C4")             # empty: not active, shares nothing
        self.place(rack, "C4")
        p = self.preview(rack)
        self.assertEqual(len(p["affected"]), 4)
        self.assertEqual(p["shared"], {"keep_names": 1, "keep_cells": 1})


class WhoMayAsk(Colony):
    def test_only_whoever_may_change_the_rack_sees_the_preview(self):
        rack = self.make_rack()
        self.assertEqual(self.o.post(f"/racks/mouse_rack/{rack}/layout", data={"rows": "5"}).status_code, 403)
        self.assertEqual(self.m.post("/racks/nothing/1/layout").status_code, 404)


class TheApi(ApiBase):
    def test_a_rack_change_that_moves_cages_needs_a_choice(self):
        name = uniq("Rack")
        self.m.post("/colony/racks/save", data={"id": "", "name": name, "rows": "10", "cols": "10", **LETTERS_FIRST})
        rack = one("select id from mouse_racks where name=?", name)
        cage = self.make_cage(self.m, rack_id=str(rack), position="F7")
        tok = self.token(self.m)
        swapped = {"rows": "numbers", "cols": "letters", "order": "col_row"}

        listed = self.call("get", "/racks", tok).get_json()["data"]
        self.assertIn({"name": name, "cages": 1}, [{"name": r["name"], "cages": r["cages"]} for r in listed])

        r = self.call("patch", f"/racks/{name}", tok, json={"naming": swapped})
        self.assertEqual(r.status_code, 409, r.get_data(as_text=True))
        self.assertIn("cages=keep_names", r.get_json()["error"])
        self.assertEqual([(a["now"], a["keep_names"], a["keep_cells"]) for a in r.get_json()["preview"]["affected"]],
                         [("F7", "F7", "G6")])
        self.assertIn('"rows": "letters"', one("select naming from mouse_racks where id=?", rack))

        r = self.call("patch", f"/racks/{name}", tok, json={"naming": swapped, "cages": "sideways"})
        self.assertEqual(r.status_code, 400)

        r = self.call("patch", f"/racks/{name}", tok, json={"naming": swapped, "cages": "keep_cells"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["naming"]["rows"], "numbers")
        self.assertEqual(one("select rack_row || ',' || rack_col from mouse_cages where id=?", cage), "6,7")

        r = self.call("patch", f"/racks/{name}", tok, json={"room": "B12"})      # nothing moves: no choice needed
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["room"], "B12")


class OtherGrids(AppTestCase):
    """Fish racks and inventory boxes ask the same, through the same dialog."""

    def test_a_fish_rack_swapped_keeps_its_tanks_names(self):
        from tests.test_zebrafish import make_rack
        rack = make_rack(self.a, rows_=10, cols=10)
        tank = self.make_tank(self.a, rack_id_fk=str(rack), position="F7")
        name = one("select name from fish_racks where id=?", rack)
        data = {"name": name, "rows": "10", "cols": "10", **SWAPPED}
        r = self.post(self.a, f"/zebrafish/racks/{rack}/update", data)
        self.assertFlash(r, "1 tank", "error")
        preview = self.a.post(f"/racks/fish_rack/{rack}/layout", data=data).get_json()
        self.assertEqual([(a["now"], a["keep_names"], a["keep_cells"]) for a in preview["affected"]],
                         [("F7", "F7", "G6")])
        self.post(self.a, f"/zebrafish/racks/{rack}/update", {**data, "cages": "keep_names"})
        self.assertEqual(row("select row, col from tanks where id=?", tank), (6, 5))   # 0-based: row 7, column F
        batch = last_batch()[0]
        self.post(self.a, f"/batches/{batch}/undo")
        self.assertEqual(row("select row, col from tanks where id=?", tank), (5, 6))

    def test_an_inventory_box_swapped_keeps_its_items_cells(self):
        from tests.test_inventory import InventoryCase
        key = "reagents"
        name = uniq("Box")
        InventoryCase.make_rack(self.a, key, name, rows=10, cols=10, **LETTERS_FIRST)
        box = one("select id from inventory_racks where name=?", name)
        item = self.make_item(self.a, key, uniq("R"), rack_id=str(box), position="J4")
        self.post(self.a, f"/inventory/{key}/racks/save",
                  {"id": str(box), "name": name, "rows": "10", "cols": "10", "kind": "box", **SWAPPED,
                   "cages": "keep_cells"})
        self.assertEqual(row("select rack_row, rack_col from inventory_items where id=?", item), (10, 4))
        from app.db import SessionLocal
        from app.inventory_service import rack_label
        from app.models import InventoryItem
        with SessionLocal() as s:
            self.assertEqual(rack_label(s.get(InventoryItem, item)), "D10")
