"""The lab's vendors (app/vendors.py): the list, each vendor's details, and
renaming or merging spellings on every record."""
from __future__ import annotations

from tests.base import flash_text, one, uniq
from tests.test_inventory import InventoryCase


def vendor_of(item_id: int) -> str:
    return one("select vendor from inventory_items where id=?", item_id)


class Vendors(InventoryCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.orders = cls.new_module(cls.a, "orders")
        cls.reagents = cls.new_module(cls.a, "reagents")

    def test_every_vendor_records_name_with_its_spellings(self):
        name = uniq("Zymo")
        self.make_item(self.m, self.orders, uniq("O"), vendor=name, attr_price="10")
        self.make_item(self.m, self.orders, uniq("O"), vendor=name, attr_price="10")
        self.make_item(self.m, self.reagents, uniq("R"), vendor=name.upper())
        html = self.get_ok(self.m, "/vendors")
        self.assertIn(name, html)
        self.assertIn(f"Also written: {name.upper()}", html)

    def test_anyone_keeps_a_vendors_details(self):
        name = uniq("Abcam")
        self.make_item(self.m, self.reagents, uniq("R"), vendor=name)
        r = self.post(self.m, "/vendors/save", data={"was": name, "name": name, "website": "abcam.com",
                                                    "customer_number": "C-42"})
        self.assertIn(f"Saved {name}.", flash_text(r))
        html = self.get_ok(self.m, "/vendors")
        self.assertIn('href="https://abcam.com"', html)
        self.assertIn("C-42", html)
        # Offered as a vendor when typing one.
        self.assertIn(f'<option value="{name}">', self.get_ok(self.m, f"/inventory/{self.reagents}"))

    def test_only_an_admin_renames_and_it_is_renamed_everywhere(self):
        old, new = uniq("Sigma"), uniq("Sigma-Aldrich ")
        order = self.make_item(self.m, self.orders, uniq("O"), vendor=old)
        stock = self.make_item(self.m, self.reagents, uniq("R"), vendor=old.lower())
        r = self.post(self.m, "/vendors/save", data={"was": old, "name": new})
        self.assertIn("Only an admin or a facility manager", flash_text(r))
        self.assertEqual(vendor_of(order), old)
        r = self.post(self.a, "/vendors/save", data={"was": old, "name": new, "website": "sigmaaldrich.com"})
        self.assertIn("on 2 records", flash_text(r))
        self.assertEqual((vendor_of(order), vendor_of(stock)), (new.strip(), new.strip()))

    def test_merging_spellings_keeps_one_name_and_every_detail(self):
        a, b = uniq("Thermo"), uniq("ThermoFisher")
        x = self.make_item(self.m, self.orders, uniq("O"), vendor=a)
        y = self.make_item(self.m, self.reagents, uniq("R"), vendor=b)
        self.post(self.m, "/vendors/save", data={"was": a, "name": a, "phone": "555-0100"})
        self.post(self.m, "/vendors/save", data={"was": b, "name": b, "website": "thermofisher.com"})
        self.assertEqual(self.m.post("/vendors/merge", data={"names": [a, b], "into": b}).status_code, 403)
        r = self.post(self.a, "/vendors/merge", data={"names": [a, b], "into": b})
        self.assertIn(f"Merged 2 vendors into {b}", flash_text(r))
        self.assertEqual((vendor_of(x), vendor_of(y)), (b, b))
        self.assertEqual(one("select count(*) from vendors where name in (?, ?)", a, b), 1)
        self.assertEqual(one("select phone from vendors where name=?", b), "555-0100")
        self.assertEqual(one("select website from vendors where name=?", b), "thermofisher.com")

    def test_merging_needs_two(self):
        r = self.post(self.a, "/vendors/merge", data={"names": ["Only one"], "into": "Only one"})
        self.assertIn("Tick two or more vendors", flash_text(r))

    def test_supplier_pages_link_to_it(self):
        self.assertIn('href="/vendors"', self.get_ok(self.m, f"/inventory/{self.orders}"))
