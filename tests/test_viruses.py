"""The Viruses inventory (app/inventory.py PRESETS["viruses"]) and the
plasmid column type it uses: a virus names the plasmid it was made from,
the sheet links to it, and the plasmid's page lists what was made from it."""
from tests.base import *  # noqa: F401,F403
from tests.base import AppTestCase, count, flash_text, one, row, rows, uniq
from tests import test_inventory as inv_tests
from tests import test_sheet_import as import_tests
from tests.test_inventory import attrs_of, items_named

import io
import re
import unittest

from app import inventory, lab


class VirusCase(inv_tests.InventoryCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.viruses = cls.new_module(cls.a, "viruses")

    def plasmid(self, name=None):
        """A plasmid; returns (its number, its name, its row id)."""
        name = name or uniq("pAAV-hSyn-")
        row_id = self.make_plasmid(self.a, name)
        return one("select plasmid_id from plasmids where id=?", row_id), name, row_id


class VirusDatabaseTests(VirusCase):

    def test_it_is_a_ready_made_database_and_a_survey_choice(self):
        self.assertIn("viruses", inventory.PRESETS)
        self.assertIn("viruses", lab.INVENTORY_CHOICES)
        self.assertIn("Viruses", self.get_ok(self.a, "/inventory/new"))

    def test_the_sheet_has_the_virus_columns(self):
        html = self.get_ok(self.a, f"/inventory/{self.viruses}")
        for column in ("Serotype", "Made from", "Promoter", "Payload", "Titer", "Biosafety", "Vector"):
            self.assertIn(column, html)
        self.assertEqual(one("select kind from inventory_modules where key=?", self.viruses), "viruses")

    def test_in_stock_and_low_are_usable_and_used_up_ends_it(self):
        vid = self.make_item(self.a, self.viruses, uniq("AAV9-"), status="in stock")
        self.autosave(self.a, f"/inventory/{self.viruses}/items/{vid}/update", {"status": "used up"})
        self.assertTrue(attrs_of(vid).get("used_up_on"))


class PlasmidTabsTests(VirusCase):
    """Plasmids, Primers, Glycerol stocks and Viruses are one area with a tab
    each (PLASMID_TAB_KINDS), not four entries in the sidebar."""

    @staticmethod
    def rail(html: str) -> str:
        return html.split('<nav class="rail"', 1)[-1].split("</nav>", 1)[0]

    @staticmethod
    def tabs(html: str) -> str:
        return html.split('aria-label="Plasmids and what goes with them"', 1)[-1].split("</nav>", 1)[0]

    def test_the_tab_strip_is_on_both_pages_and_the_rail_lists_plasmids_only(self):
        plasmids = self.get_ok(self.a, "/plasmids")
        self.assertIn(f"/inventory/{self.viruses}", self.tabs(plasmids))
        self.assertIn('class="seg-item is-active"', self.tabs(plasmids))
        self.assertNotIn(f"/inventory/{self.viruses}", self.rail(plasmids))
        self.assertIn("/plasmids", self.rail(plasmids))

        sheet = self.get_ok(self.a, f"/inventory/{self.viruses}")
        self.assertIn("/plasmids", self.tabs(sheet))
        self.assertIn(f'class="seg-item is-active" href="/inventory/{self.viruses}"', self.tabs(sheet))
        # Plasmids is the database you are in, so the rail marks it.
        self.assertIn('rail-item is-active', self.rail(sheet).split('href="/plasmids"')[0][-120:])

    def test_another_inventory_stays_in_the_rail_with_no_tab_strip(self):
        key = self.new_module(self.a, "reagents")
        sheet = self.get_ok(self.a, f"/inventory/{key}")
        self.assertIn(f"/inventory/{key}", self.rail(sheet))
        self.assertNotIn("Plasmids and what goes with them", sheet)


class SequenceViewTests(VirusCase):
    """A virus (or any record with a plasmid column) shows that plasmid's
    sequence and map, to read but not edit."""

    def test_the_sheet_links_the_sequence_and_the_page_is_read_only(self):
        number, name, _rid = self.plasmid()
        self.post(self.a, f"/plasmids/{one('select id from plasmids where plasmid_id=?', number)}/edit-sequence",
                  {"sequence_text": "ATGCGTACGTTAGCATGCATCGATCGATCG", "is_circular": "1"})
        vid = self.make_item(self.a, self.viruses, uniq("AAV9-"), attr_plasmid=str(number))
        sheet = self.get_ok(self.a, f"/inventory/{self.viruses}")
        self.assertIn(f"/inventory/{self.viruses}/items/{vid}/sequence", sheet)
        page = self.get_ok(self.a, f"/inventory/{self.viruses}/items/{vid}/sequence")
        self.assertIn("read only", page)
        self.assertIn(f"/plasmid/{number}", page)
        self.assertIn("readOnly: true", page)
        self.assertNotIn("sequence-save", page)

    def test_without_a_sequence_it_says_so_instead(self):
        number, _name, _rid = self.plasmid()
        vid = self.make_item(self.a, self.viruses, uniq("AAV9-"), attr_plasmid=str(number))
        self.assertNotIn(f"items/{vid}/sequence", self.get_ok(self.a, f"/inventory/{self.viruses}"))
        r = self.a.get(f"/inventory/{self.viruses}/items/{vid}/sequence", follow_redirects=True)
        self.assertIn("doesn’t name a plasmid with a sequence", r.get_data(as_text=True))


class PlasmidColumnTests(VirusCase):

    def test_a_number_a_hash_or_a_name_is_kept_as_the_plasmids_number(self):
        number, name, _ = self.plasmid()
        for typed in (str(number), f"#{number}", name.upper(), f"{number} · {name}"):
            with self.subTest(typed=typed):
                vid = self.make_item(self.a, self.viruses, uniq("AAV-"), attr_plasmid=typed)
                self.assertEqual(attrs_of(vid)["plasmid"], str(number))

    def test_an_unknown_plasmid_is_kept_as_typed_and_said(self):
        name = uniq("AAV-")
        r = self.post(self.a, f"/inventory/{self.viruses}/items/save",
                      {"id": "", "name": name, "attr_plasmid": "pNothing-999"})
        self.assertIn("There is no plasmid “pNothing-999”", flash_text(r))
        [vid] = items_named(self.viruses, name)
        self.assertEqual(attrs_of(vid)["plasmid"], "pNothing-999")

    def test_the_sheet_links_the_plasmid_and_offers_the_lab_s(self):
        number, name, row_id = self.plasmid()
        self.make_item(self.a, self.viruses, uniq("AAV-"), attr_plasmid=str(number))
        html = self.get_ok(self.a, f"/inventory/{self.viruses}")
        self.assertIn(f'href="/plasmid/{number}"', html)
        self.assertIn(f'<option value="{number}">#{number} · {name}</option>', html)

    def test_the_plasmid_page_lists_what_was_made_from_it(self):
        number, _name, row_id = self.plasmid()
        virus = uniq("AAV-PHP.eB-")
        vid = self.make_item(self.a, self.viruses, virus, attr_plasmid=str(number))
        html = self.get_ok(self.a, f"/plasmid/{number}")
        self.assertIn("Made from this plasmid", html)
        self.assertIn(virus, html)
        self.assertIn(f"/inventory/{self.viruses}?open={vid}", html)
        other_number, _n, other_row = self.plasmid()
        self.assertNotIn(virus, self.get_ok(self.a, f"/plasmid/{other_number}"))

    def test_any_inventory_can_have_a_plasmid_column(self):
        self.assertIn("plasmid", inventory.FIELD_TYPES)
        key = self.new_module(self.a, "custom")
        self.assertIn(">A plasmid</option>", self.get_ok(self.a, f"/inventory/{key}/configure"))


class VirusStockTests(VirusCase):

    def test_a_received_order_can_become_a_virus(self):
        orders = one("select key from inventory_modules where kind='orders' and private_to='' order by id")
        name = uniq("AAV1-Syn-GCaMP8m ")
        oid = self.make_item(self.a, orders, name, status="received", category="virus")
        r = self.a.post(f"/inventory/{orders}/items/{oid}/to-reagents", data={"target": self.viruses, "shared": "1"})
        [vid] = items_named(self.viruses, name)
        self.assertIn(f"?open={vid}", r.headers["Location"])
        self.assertEqual(attrs_of(oid)["stocked_as"], f"{self.viruses}:{one('select number from inventory_items where id=?', vid)}")

    def test_expiring_viruses_are_on_home(self):
        from tests.base import days_ahead
        name = uniq("LV-")
        self.make_item(self.a, self.viruses, name, status="in stock", expires_on=days_ahead(5))
        self.assertIn(name, self.get_ok(self.a, "/home"))

    def test_search_finds_a_virus(self):
        name = uniq("AAVretro-")
        self.make_item(self.a, self.viruses, name)
        found = self.a.get("/search", query_string={"q": name}).get_json()["results"]
        self.assertTrue(any(r["type"] == "virus" and name in r["label"] for r in found))


class VirusImportTests(VirusCase):
    # The import tests' helpers, without running their tests again here.
    upload = import_tests.Importing.upload
    chosen = staticmethod(import_tests.Importing.chosen)

    def test_a_virus_sheet_matches_its_columns(self):
        number, pname, _ = self.plasmid()
        virus = uniq("AAV9-CAG-")
        data = (f"Virus,Capsid,Transfer plasmid,Titre (vg/ml),BSL,Transgene\n"
                f"{virus},AAV9,{pname},2.1e13,BSL-1,tdTomato\n").encode()
        token, html = self.upload(self.a, f"inventory:{self.viruses}", "viruses.csv", data)
        chosen = self.chosen(html)
        self.assertEqual([chosen[f"map-{i}"] for i in range(6)],
                         ["name", "attr_serotype", "attr_plasmid", "attr_titer", "attr_biosafety", "attr_payload"])
        self.post(self.a, f"/import-sheet/file/{token}/run", data={**chosen, "sheet": "Sheet 1", "fill-owner": "me"})
        [vid] = items_named(self.viruses, virus)
        got = attrs_of(vid)
        self.assertEqual((got["plasmid"], got["serotype"], got["titer"]), (str(number), "AAV9", "2.1e13"))


if __name__ == "__main__":
    unittest.main()
