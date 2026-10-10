"""Several choices in one field (#58): a chemical's hazards, ticked in the
sheet or the dialog, kept as one text in the choices' order; and the
migration that moves the Hazard field labs already have onto it."""
from __future__ import annotations

import importlib
import json
from unittest import mock

from tests.base import *  # noqa: F401,F403
from tests.base import one, uniq
from tests.test_inventory import InventoryCase, attrs_of, module_id, settings_of
from app import inventory  # noqa: E402
from app.db import engine  # noqa: E402
from app.inventory_routes import several  # noqa: E402


class SeveralTests(InventoryCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.new_module(cls.a, "chemicals")

    def test_the_choices_keep_their_order_and_an_unknown_one_stays(self):
        self.assertEqual(several(["toxic", "flammable", "toxic"], inventory.HAZARDS), "flammable, toxic")
        self.assertEqual(several(["flammable, drug precursor"], inventory.HAZARDS), "flammable, drug precursor")
        self.assertEqual(several(["kept from before", "flammable"], inventory.HAZARDS), "flammable, kept from before")
        self.assertEqual(several([], inventory.HAZARDS), "")

    def test_a_new_chemicals_database_offers_the_controlled_classes(self):
        hazard = next(f for f in settings_of(self.key)["fields"] if f["key"] == "hazard")
        self.assertEqual(hazard["type"], "multiselect")
        for name in ("flammable", "drug precursor", "explosive precursor", "highly toxic"):
            self.assertIn(name, hazard["options"])

    def test_ticked_in_the_dialog_then_unticked_in_the_sheet(self):
        url = f"/inventory/{self.key}/items"
        rid = self.make_item(self.a, self.key, uniq("Acetone "),
                             attr_hazard=["flammable", "explosive precursor"], attr_hazard__several="1")
        self.assertEqual(attrs_of(rid)["hazard"], "flammable, explosive precursor")
        # The sheet sends every box of the row; none ticked still clears it.
        self.assertSaved(self.autosave(self.a, f"{url}/{rid}/update", {"attr_hazard__several": "1"}))
        self.assertEqual(attrs_of(rid)["hazard"], "")
        # A row saved for another column leaves the hazards as they are.
        self.assertSaved(self.autosave(self.a, f"{url}/{rid}/update",
                                       {"attr_hazard": ["toxic"], "attr_hazard__several": "1"}))
        self.assertSaved(self.autosave(self.a, f"{url}/{rid}/update", {"notes": "in the cabinet"}))
        self.assertEqual(attrs_of(rid)["hazard"], "toxic")

    def test_the_sheet_shows_the_menu_in_chinese(self):
        rid = self.make_item(self.a, self.key, uniq("Ether "),
                             attr_hazard=["flammable", "drug precursor"], attr_hazard__several="1")
        self.a.post("/language", data={"language": "zh", "next": "/"})
        try:
            page = self.get_ok(self.a, f"/inventory/{self.key}")
        finally:
            self.a.post("/language", data={"language": "en", "next": "/"})
        self.assertIn("data-multi-cell", page)
        self.assertIn("易燃, 易制毒", page)
        self.assertIn('name="attr_hazard__several"', page)
        self.assertIn(str(rid), page)


class MigrationTests(InventoryCase):
    def run_migration(self):
        migration = importlib.import_module("migrations.versions.0030_several_hazards")
        with engine.begin() as conn:
            with mock.patch.object(migration, "op", mock.Mock(get_bind=lambda: conn)), \
                    mock.patch.object(migration, "has_table", lambda name: True):
                migration.upgrade()

    def as_before(self, key: str, options: list[str]) -> None:
        """Put the database's Hazard field back as it was before 0030."""
        s = settings_of(key)
        for f in s["fields"]:
            if f["key"] == "hazard":
                f.update(type="select", options=options, width=92)
        with engine.begin() as conn:
            conn.exec_driver_sql("update inventory_modules set settings=? where key=?", (json.dumps(s), key))

    def test_an_untouched_field_gets_the_full_list_and_none_goes(self):
        key = self.new_module(self.a, "chemicals")
        self.as_before(key, ["none", "flammable", "corrosive", "toxic", "oxidiser", "irritant", "biohazard"])
        rid = self.make_item(self.a, key, uniq("Salt "))
        with engine.begin() as conn:
            conn.exec_driver_sql("update inventory_items set attrs=? where id=?", (json.dumps({"hazard": "none"}), rid))
        self.run_migration()
        hazard = next(f for f in settings_of(key)["fields"] if f["key"] == "hazard")
        self.assertEqual((hazard["type"], hazard["options"]), ("multiselect", inventory.HAZARDS))
        self.assertEqual(attrs_of(rid)["hazard"], "")

    def test_a_list_the_lab_edited_is_kept(self):
        key = self.new_module(self.a, "reagents")
        self.as_before(key, ["none", "flammable", "our own class"])
        self.run_migration()
        hazard = next(f for f in settings_of(key)["fields"] if f["key"] == "hazard")
        self.assertEqual((hazard["type"], hazard["options"]), ("multiselect", ["flammable", "our own class"]))
        self.assertTrue(module_id(key))
        self.assertTrue(one("select count(*) from inventory_modules where key=?", key))
