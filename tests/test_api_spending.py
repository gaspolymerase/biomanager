"""What orders cost and the lab's vendors, for an assistant: /api/v1/spending
and /api/v1/vendors, and the spending and vendors tools over them."""
from __future__ import annotations

from tests.base import uniq
from tests.test_api import Base
from tests.test_inventory import InventoryCase
from tests.test_mcp import TestApi, bm


class SpendingAndVendors(Base):
    def setUp(self):
        super().setUp()
        self.api = TestApi(self.token(self.m, scope="read"))

    def test_spending_by_vendor_with_how_it_counts(self):
        key = InventoryCase.new_module(self.a, "orders")
        vendor = uniq("Zymo ")
        self.make_item(self.m, key, uniq("O"), status="ordered", vendor=vendor, quantity="2", attr_price="30")
        out = bm.call_tool(self.api, "spending", {"by": "vendor", "inventory": key})
        row = next(s for s in out["series"] if s["label"] == vendor)
        self.assertEqual((row["spent"], row["orders"]), (60, 1))
        self.assertIn("month it was ordered", out["counts"])
        self.assertEqual(out["inventory"], key)
        self.assertNotIn("stack", out)
        missing = bm.call_tool(self.api, "spending", {"inventory": "no-such-orders"})
        self.assertIn("error", missing)

    def test_vendors_with_their_details(self):
        key = InventoryCase.new_module(self.a, "orders")
        vendor = uniq("Abcam")
        self.make_item(self.m, key, uniq("O"), status="received", vendor=vendor, attr_price="10")
        self.post(self.m, "/vendors/save", data={"was": vendor, "name": vendor, "customer_number": "C-9"})
        out = bm.call_tool(self.api, "vendors", {"q": vendor.lower()})
        [row] = out["data"]
        self.assertEqual((row["name"], row["orders"], row["customer_number"]), (vendor, 1, "C-9"))
        self.assertEqual(row["spent_last_12_months"], 10)
