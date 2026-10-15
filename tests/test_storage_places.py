"""Where a box is kept (#58): an inventory box, shelf or drawer says which
fridge, freezer or cabinet it is in ("Kept in"), and its items are found
by it: the sheet and every place an item's whereabouts are written read
"Fridge 2 › Box A · B3", the grid picks the cabinet first, and search
finds what a cabinet holds. Plus the migration that adds the column."""
from __future__ import annotations

import importlib
import json
import re

import sqlalchemy as sa

from tests.base import *  # noqa: F401,F403
from tests.base import execute, one, uniq
from tests.test_inventory import InventoryCase, module_id
from app import api, inventory_service as svc  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import InventoryItem  # noqa: E402


def place_of(rack_id: int) -> str:
    return one("select place from inventory_racks where id=?", rack_id)


class PlaceTests(InventoryCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.new_module(cls.a, "chemicals")
        cls.cabinet = uniq("Explosion-proof cabinet ")
        cls.fridge = uniq("Fridge ")
        cls.box = cls.make_rack(cls.a, cls.key, uniq("Solvents "), place=cls.cabinet)
        cls.other = cls.make_rack(cls.a, cls.key, uniq("Buffers "), place=cls.fridge)
        cls.loose = cls.make_rack(cls.a, cls.key, uniq("Spare "))

    def put(self, rack_id: int, position: str = "", name: str | None = None) -> int:
        return self.make_item(self.a, self.key, name or uniq("Ether "), rack_id=str(rack_id), position=position)

    def test_a_box_says_where_it_is_kept_and_its_items_stay(self):
        self.assertEqual(place_of(self.box), self.cabinet)
        self.assertEqual(place_of(self.loose), "")
        rack = self.make_rack(self.a, self.key, uniq("Moving "))
        rid = self.put(rack, "B3")
        r = self.post(self.a, f"/inventory/{self.key}/racks/save", data={
            "id": str(rack), "name": one("select name from inventory_racks where id=?", rack), "rows": "9", "cols": "9",
            "kind": "box", "place": "  Fridge 7  ", "naming_mode": "grid", "naming_rows": "letters",
            "naming_cols": "numbers", "naming_order": "row_col", "naming_separator": "", "naming_start": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(place_of(rack), "Fridge 7")
        self.assertEqual(one("select rack_id_fk || ':' || rack_row || ':' || rack_col from inventory_items where id=?",
                             rid), f"{rack}:2:3")

    def test_several_boxes_at_once_are_all_kept_there(self):
        base = uniq("Acids ")
        self.post(self.a, f"/inventory/{self.key}/racks/save", data={
            "id": "", "name": base, "rows": "5", "cols": "5", "kind": "shelf", "count": "3", "place": self.cabinet,
            "naming_mode": "grid", "naming_rows": "letters", "naming_cols": "numbers", "naming_order": "row_col",
            "naming_separator": "", "naming_start": "1"})
        made = [r for r in rows_of("select place from inventory_racks where module_id_fk=? and name like ?",
                                   module_id(self.key), base + " %")]
        self.assertEqual(made, [self.cabinet] * 3)

    def test_the_dialog_offers_the_places_of_other_inventories(self):
        other = self.new_module(self.a, "reagents")
        page = self.get_ok(self.a, f"/inventory/{other}")
        offered = re.search(r'<datalist id="inv-box-places">(.*?)</datalist>', page, re.S).group(1)
        self.assertIn(f'<option value="{self.cabinet}">', offered)
        self.assertIn('name="place" list="inv-box-places"', page)

    def test_the_sheet_and_the_grid_say_where_the_box_is(self):
        rid = self.put(self.box, "F6")
        page = self.get_ok(self.a, f"/inventory/{self.key}")
        box = one("select name from inventory_racks where id=?", self.box)
        self.assertIn(f'<option value="{self.box}" selected>{self.cabinet} › {box}</option>', page)
        # The grid's tiles come when it is first shown, from its own address.
        self.assertNotIn("data-rack-data>", page)
        src = re.search(r'data-rack-src="([^"]+)"', page).group(1)
        grid = self.a.get(src.replace("&amp;", "&")).get_json()
        racks = {r["id"]: r for r in grid["racks"]}
        self.assertEqual(racks[self.box]["group"], self.cabinet)
        self.assertEqual(racks[self.loose]["group"], "")
        self.assertEqual(json.loads(racks[self.box]["edit"]["data-record-payload"])["place"], self.cabinet)
        # Boxes with no place come after those kept somewhere.
        order = [r["id"] for r in grid["racks"]]
        self.assertLess(order.index(self.box), order.index(self.loose))
        self.assertIn('data-rack-group', page)
        # The Box column is wide enough to read the box after its place.
        self.assertNotIn('data-sort-key="rack_id" style="width: 104px;"', page)
        tile = next(i for i in grid["items"] if i["id"] == rid)
        self.assertIn(self.cabinet.lower(), tile["search"])

    def test_no_place_picker_until_a_box_has_a_place(self):
        key = self.new_module(self.a, "chemicals")
        self.make_rack(self.a, key, uniq("Plain "))
        page = self.get_ok(self.a, f"/inventory/{key}")
        self.assertNotIn("data-rack-group", page)
        self.assertIn('data-sort-key="rack_id" style="width: 104px;"', page)

    def test_the_items_whereabouts_read_place_box_position(self):
        rid = self.put(self.box, "B3")
        box = one("select name from inventory_racks where id=?", self.box)
        with SessionLocal() as session:
            item = session.get(InventoryItem, rid)
            self.assertEqual(svc.where_label(item), f"{self.cabinet} › {box} · B3")
        loose = self.put(self.loose, "A1")
        with SessionLocal() as session:
            self.assertEqual(svc.where_label(session.get(InventoryItem, loose)),
                             one("select name from inventory_racks where id=?", self.loose) + " · A1")
        # Printed on its label.
        card = self.get_ok(self.a, f"/labels/cards/inventory/{self.key}?selected_ids={rid}")
        self.assertIn(f"{self.cabinet} › {box} · B3", card)

    def test_the_api_says_where_the_box_is_kept(self):
        rid = self.put(self.box, "C1")
        with SessionLocal() as session:
            item = session.get(InventoryItem, rid)
            from app.models import InventoryModule
            mv = svc.view(session.get(InventoryModule, item.module_id_fk))
            found = api.item_json(mv, item)
        self.assertEqual((found["kept_in"], found["position"]), (self.cabinet, "C1"))

    def test_used_up_it_remembers_the_place_too(self):
        rid = self.put(self.box, "D4")
        self.assertSaved(self.autosave(self.a, f"/inventory/{self.key}/items/{rid}/update", {"status": "empty"}))
        box = one("select name from inventory_racks where id=?", self.box)
        self.assertEqual(one("select location_note from inventory_items where id=?", rid),
                         f"was in {self.cabinet} › {box} · D4")

    def test_search_finds_what_a_cabinet_holds(self):
        inside = self.put(self.box, "E5", name=uniq("Diethyl ether "))
        elsewhere = self.put(self.other, "E5", name=uniq("Diethyl ether "))
        found = self.a.get("/search", query_string={"q": self.cabinet}).get_json()["results"]
        labels = " ".join(r["label"] for r in found)
        self.assertIn(one("select name from inventory_items where id=?", inside), labels)
        self.assertNotIn(one("select name from inventory_items where id=?", elsewhere), labels)
        sub = next(r["sublabel"] for r in found if one("select name from inventory_items where id=?", inside) in r["label"])
        self.assertIn(self.cabinet, sub)

    def test_an_exported_box_column_imports_back_into_its_box(self):
        from app.sheet_import import InventoryTarget
        box = one("select name from inventory_racks where id=?", self.box)
        with SessionLocal() as session:
            from app.models import InventoryModule
            target = InventoryTarget.__new__(InventoryTarget)
            target.module = session.get(InventoryModule, module_id(self.key))
            racks = target.prepare(session)["racks"]
        self.assertEqual(racks[f"{self.cabinet} › {box}".lower()].id, self.box)
        self.assertEqual(racks[box.lower()].id, self.box)


def rows_of(sql: str, *args) -> list:
    from tests.base import rows
    return [r[0] for r in rows(sql, *args)]


class MigrationTests(InventoryCase):
    def test_the_column_is_added_where_it_is_missing_and_left_where_it_is(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        migration = importlib.import_module("migrations.versions.0035_box_places")
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.exec_driver_sql("create table inventory_racks (id integer primary key, name varchar(120))")
            conn.exec_driver_sql("insert into inventory_racks (name) values ('Box A')")
            for _ in range(2):
                with Operations.context(MigrationContext.configure(conn)):
                    migration.upgrade()
            columns = {c["name"] for c in sa.inspect(conn).get_columns("inventory_racks")}
            self.assertIn("place", columns)
            self.assertEqual(conn.exec_driver_sql("select name, place from inventory_racks").fetchall(),
                             [("Box A", "")])

    def test_the_lab_database_has_it(self):
        rack = self.make_rack(self.a, self.new_module(self.a, "chemicals"), uniq("Box "))
        execute("update inventory_racks set place=? where id=?", "Cabinet 3", rack)
        self.assertEqual(place_of(rack), "Cabinet 3")
