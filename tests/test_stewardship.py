"""Who may give a record to someone else, and to whom (app/stewardship.py):
its owner, an admin, or animal care for the animals they look after, and
only to a lab member. The same rule in every database, from a row of the
sheet, a dialog or a batch action."""
from __future__ import annotations

import unittest

from tests.base import AppTestCase, client_for, make_user, one, uniq


class TankOwnerTests(AppTestCase):

    def test_member_may_edit_a_shared_breeding_tank_but_not_give_it_away(self):
        tank = self.make_tank(self.a, purpose="breeding")
        r = self.autosave(self.m, f"/zebrafish/tanks/{tank}/update", {"owner": self.member, "owner_was": self.admin})
        self.assertRefused(r)
        self.assertIn("to someone else", r.get_json()["error"])
        self.assertEqual(one("select owner from tanks where id=?", tank), self.admin)

    def test_member_batch_cannot_give_away_a_shared_breeding_tank(self):
        tank = self.make_tank(self.a, purpose="breeding")
        r = self.post(self.m, "/zebrafish/tanks/bulk", {"action": "owner", "value": self.member,
                                                       "selected_ids": [tank]})
        self.assertFlash(r, "not yours", "error")
        self.assertEqual(one("select owner from tanks where id=?", tank), self.admin)

    def test_a_tank_goes_only_to_a_lab_member(self):
        tank = self.make_tank(self.a, purpose="stock")
        self.assertRefused(self.autosave(self.a, f"/zebrafish/tanks/{tank}/update",
                                         {"owner": "nobody-here", "owner_was": self.admin}))
        r = self.post(self.a, "/zebrafish/tanks/bulk", {"action": "owner", "value": "nobody-here",
                                                       "selected_ids": [tank]})
        self.assertFlash(r, "is not a lab member", "error")
        self.assertEqual(one("select owner from tanks where id=?", tank), self.admin)

    def test_owner_gives_a_tank_to_a_lab_member(self):
        tank = self.make_tank(self.m, purpose="stock")
        self.assertSaved(self.autosave(self.m, f"/zebrafish/tanks/{tank}/update",
                                       {"owner": self.other, "owner_was": self.member}))
        self.assertEqual(one("select owner from tanks where id=?", tank), self.other)

    def test_animal_care_may_give_any_tank_away(self):
        care = make_user(uniq("care"), role="care")
        tank = self.make_tank(self.m, purpose="stock")
        self.assertSaved(self.autosave(client_for(care), f"/zebrafish/tanks/{tank}/update",
                                       {"owner": self.other, "owner_was": self.member}))
        self.assertEqual(one("select owner from tanks where id=?", tank), self.other)


class FishRecordOwnerTests(AppTestCase):

    def test_a_clutch_goes_only_to_a_lab_member(self):
        self.a.post("/zebrafish/clutches/create", data={"clutch_id": (code := uniq("CL"))})
        clutch = one("select id from clutches where clutch_id=?", code)
        self.assertRefused(self.autosave(self.a, f"/zebrafish/clutches/{clutch}/update", {"owner": "nobody-here"}))
        r = self.post(self.a, "/zebrafish/clutches/bulk", {"action": "owner", "value": "nobody-here",
                                                          "selected_ids": [clutch]})
        self.assertFlash(r, "is not a lab member", "error")
        self.assertEqual(one("select owner from clutches where id=?", clutch), self.admin)

    def test_a_line_goes_only_to_a_lab_member(self):
        line = self.make_line(self.a)
        self.assertRefused(self.autosave(self.a, f"/zebrafish/lines/{line}/update", {"owner": "nobody-here"}))
        self.assertEqual(one("select owner from fish_lines where id=?", line), self.admin)


class OrganismOwnerTests(AppTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.make_organism_module(cls.a)
        cls.url = f"/organisms/{cls.key}"

    def test_housing_goes_only_to_a_lab_member(self):
        code = uniq("H")
        self.post(self.a, f"{self.url}/housing/save", {"code": code, "owner": self.admin})
        unit = one("select id from organism_housing where code=?", code)
        self.post(self.a, f"{self.url}/housing/save", {"id": unit, "owner": "nobody-here"})
        self.assertEqual(one("select owner from organism_housing where id=?", unit), self.admin)


class InventoryOwnerTests(AppTestCase):

    def test_a_batch_gives_items_only_to_a_lab_member(self):
        item = self.make_item(self.a)
        r = self.post(self.a, "/inventory/reagents/items/bulk", {"action": "owner", "value": "nobody-here",
                                                          "selected_ids": [item]})
        self.assertFlash(r, "is not a lab member", "error")
        self.assertEqual(one("select owner from inventory_items where id=?", item), self.admin)


if __name__ == "__main__":
    unittest.main()
