"""The orders page's Spending view (app/order_spending.py): what an order
costs, how the months and groupings add up, the limits and the warning a
save gives when it takes someone past one."""
from __future__ import annotations

import unittest
from datetime import date, datetime

from tests.base import execute, flash_text, one, uniq
from tests.test_inventory import InventoryCase

from app import order_spending as spending  # noqa: E402  (after tests.base sets up the test database)


def order(owner, cost, day, state="spent", vendor="Acme", category="reagent", oid=1):
    return spending.Order(oid, oid, "x", owner, vendor, category, state, cost, day)


PEOPLE = spending.People(names={"sam": "Sam Okafor", "priya": "Priya Nair", "jo": "Jo"},
                         groups={1: "Photometry", 2: "Anatomy"},
                         members={1: {"sam", "priya"}, 2: {"sam"}})
SETTINGS = {"currency": "$", "price_is": "unit", "period": "year", "year_starts": 1, "lab": None,
            "people": {}, "groups": {}}


class WhatAnOrderCosts(unittest.TestCase):

    def test_price_times_quantity_or_the_price_alone(self):
        self.assertEqual(spending.cost({"price": "96"}, "2"), 192)
        self.assertEqual(spending.cost({"price": "96"}, "2", "total"), 96)
        # A quantity that isn't a number ("1 box") leaves the price as it is.
        self.assertEqual(spending.cost({"price": "96"}, "1 box"), 96)
        self.assertIsNone(spending.cost({}, "2"))

    def test_prices_as_people_write_them(self):
        for text, value in (("$1,250.50", 1250.5), ("¥ 1200", 1200), ("12,5", 12.5), ("€96", 96), ("", None),
                            ("call us", None)):
            self.assertEqual(spending.money(text), value, text)

    def test_cancelled_counts_nowhere_and_requested_is_asked_for(self):
        statuses = ["requested", "ordered", "received", "cancelled"]
        self.assertEqual(spending.state_of("requested", statuses), "asked")
        self.assertEqual(spending.state_of("Received", statuses), "spent")
        self.assertIsNone(spending.state_of("cancelled", statuses))


class HowItAddsUp(unittest.TestCase):
    FIRST, LAST = date(2026, 1, 1), date(2026, 3, 1)

    def test_by_person_per_month(self):
        data = spending.summary([order("sam", 100, date(2026, 1, 5)), order("sam", 50, date(2026, 3, 2), "asked"),
                                 order("priya", 30, date(2026, 2, 9)),
                                 order("priya", 999, date(2025, 12, 31))],    # before the months asked for
                                "person", self.FIRST, self.LAST, PEOPLE, SETTINGS, today=date(2026, 3, 9))
        self.assertEqual(data["months"], ["2026-01", "2026-02", "2026-03"])
        self.assertEqual(data["month_totals"], [100, 30, 50])
        sam = data["series"][0]
        self.assertEqual((sam["label"], sam["spent"], sam["asked"], sam["total"], sam["orders"]),
                         ("Sam Okafor", 100, 50, 150, 2))
        self.assertEqual(data["totals"]["spent"] + data["totals"]["asked"], 180)

    def test_by_project_group_counts_an_order_in_each_of_its_askers_groups(self):
        data = spending.summary([order("sam", 100, date(2026, 1, 5)), order("jo", 10, date(2026, 1, 5))],
                                "group", self.FIRST, self.LAST, PEOPLE, SETTINGS)
        totals = {s["label"]: s["total"] for s in data["series"]}
        self.assertEqual(totals, {"Photometry": 100, "Anatomy": 100, "Not in a group": 10})

    def test_by_vendor_whatever_the_case(self):
        data = spending.summary([order("sam", 100, date(2026, 1, 5), vendor="Sigma"),
                                 order("sam", 20, date(2026, 1, 6), vendor="sigma"),
                                 order("sam", 5, date(2026, 1, 6), vendor="")],
                                "vendor", self.FIRST, self.LAST, PEOPLE, SETTINGS)
        self.assertEqual([(s["label"], s["total"]) for s in data["series"]], [("Sigma", 120), ("No vendor", 5)])

    def test_the_chart_stacks_the_five_that_cost_most_and_the_rest_as_other(self):
        orders = [order(f"p{i}", 10 * (i + 1), date(2026, 1, 5), oid=i) for i in range(8)]
        data = spending.summary(orders, "person", self.FIRST, self.LAST, PEOPLE, SETTINGS)
        self.assertEqual([s["key"] for s in data["stack"]], ["p7", "p6", "p5", "p4", "p3", "__other"])
        self.assertEqual(data["stack"][-1]["months"][0], 10 + 20 + 30)
        self.assertEqual(data["stack"][-1]["count"], 3)
        self.assertEqual(len(data["series"]), 8)

    def test_orders_without_a_price_are_counted_but_cost_nothing(self):
        data = spending.summary([order("sam", None, date(2026, 1, 5))], "person", self.FIRST, self.LAST, PEOPLE,
                                SETTINGS)
        self.assertEqual((data["totals"]["orders"], data["totals"]["unpriced"], data["totals"]["spent"]), (1, 1, 0))


class Limits(unittest.TestCase):

    def test_a_year_from_the_month_the_lab_chooses(self):
        settings = dict(SETTINGS, year_starts=9)
        self.assertEqual(spending.limit_period(settings, date(2026, 3, 9)), (date(2025, 9, 1), date(2026, 8, 1)))
        self.assertEqual(spending.limit_period(settings, date(2026, 10, 1)), (date(2026, 9, 1), date(2027, 8, 1)))
        self.assertEqual(spending.limit_period(dict(SETTINGS, period="month"), date(2026, 3, 9)),
                         (date(2026, 3, 1), date(2026, 3, 1)))

    def test_where_each_limit_stands(self):
        settings = dict(SETTINGS, lab=1000, people={"sam": 100}, groups={"1": 200})
        found = spending.limits([order("sam", 120, date(2026, 2, 1)), order("priya", 70, date(2026, 2, 1)),
                                 order("sam", 500, date(2025, 12, 1))],    # last year
                                PEOPLE, settings, date(2026, 3, 9))["rows"]
        states = {(r["kind"], r["key"]): (r["used"], r["state"]) for r in found}
        self.assertEqual(states, {("lab", ""): (190, "ok"), ("person", "sam"): (120, "over"),
                                  ("group", "1"): (190, "near")})


class TheView(InventoryCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.orders = cls.new_module(cls.a, "orders")

    def place(self, client, price, quantity="1", **fields):
        return self.make_item(client, self.orders, uniq("Order "), status="ordered", attr_price=str(price),
                              quantity=quantity, **fields)

    def test_the_orders_page_has_it_and_a_stock_list_does_not(self):
        html = self.get_ok(self.m, f"/inventory/{self.orders}")
        self.assertIn('data-layout="spending"', html)
        self.assertIn(f'data-src="/inventory/{self.orders}/spending"', html)
        reagents = self.new_module(self.a, "reagents")
        self.assertNotIn('data-layout="spending"', self.get_ok(self.a, f"/inventory/{reagents}"))
        self.assertEqual(self.a.get(f"/inventory/{reagents}/spending").status_code, 404)

    def test_the_numbers_by_person(self):
        key = self.new_module(self.a, "orders")
        self.make_item(self.m, key, uniq("O"), status="ordered", attr_price="40", quantity="3")
        self.make_item(self.m, key, uniq("O"), status="cancelled", attr_price="999")
        data = self.m.get(f"/inventory/{key}/spending?by=person").get_json()
        me = next(s for s in data["series"] if s["key"] == self.member)
        self.assertEqual((me["spent"], me["orders"]), (120, 1))
        self.assertFalse(data["can_set_limits"])
        self.assertEqual(len(data["months"]), 12)

    def test_the_csv(self):
        key = self.new_module(self.a, "orders")
        self.make_item(self.m, key, uniq("O"), status="received", attr_price="25", quantity="2", vendor="Zymo")
        r = self.m.get(f"/inventory/{key}/spending.csv?by=vendor")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertTrue(body.startswith("﻿Vendor,Orders,Spent"))
        self.assertIn("Zymo,1,50,0,50,", body)
        self.assertIn("attachment", r.headers["Content-Disposition"])

    def test_only_an_admin_sets_limits(self):
        key = self.new_module(self.a, "orders")
        r = self.m.post(f"/inventory/{key}/spending/settings", data={"limit_lab": "100"})
        self.assertEqual(r.status_code, 403)
        r = self.a.post(f"/inventory/{key}/spending/settings",
                        data={"currency": "€", "price_is": "total", "period": "month", "limit_lab": "1,000",
                              f"limit_person_{self.member}": "50", "limit_person_nobody": ""})
        self.assertTrue(r.get_json()["ok"])
        settings = self.a.get(f"/inventory/{key}/spending").get_json()["settings"]
        self.assertEqual((settings["currency"], settings["price_is"], settings["period"], settings["lab"]),
                         ("€", "total", "month", 1000))
        self.assertEqual(settings["people"], {self.member: 50})

    def test_a_save_that_takes_someone_past_their_limit_says_so(self):
        key = self.new_module(self.a, "orders")
        self.a.post(f"/inventory/{key}/spending/settings", data={"currency": "$", f"limit_person_{self.member}": "100"})
        r = self.post(self.m, f"/inventory/{key}/items/save",
                      data={"id": "", "name": uniq("O"), "vendor": "Acme", "catalog_number": "A-1", "quantity": "1",
                            "attr_price": "80"})
        self.assertNotIn("Over the limit", flash_text(r))
        r = self.post(self.m, f"/inventory/{key}/items/save",
                      data={"id": "", "name": uniq("O"), "vendor": "Acme", "catalog_number": "A-1", "quantity": "1",
                            "attr_price": "30"})
        self.assertIn("Over the limit", flash_text(r))
        self.assertIn("$110", flash_text(r))
        # Changing something else of an order already past it says nothing more.
        oid = one("select max(id) from inventory_items where module_id_fk=(select id from inventory_modules where key=?)", key)
        r = self.autosave(self.m, f"/inventory/{key}/items/{oid}/update", {"notes": "urgent"})
        self.assertNotIn("notes", r.get_json())

    def test_an_order_counts_in_the_month_it_was_asked_for(self):
        key = self.new_module(self.a, "orders")
        oid = self.make_item(self.m, key, uniq("O"), status="received", attr_price="10")
        execute("update inventory_items set created_at=? where id=?", datetime(2025, 6, 15, 10, 0), oid)
        data = self.m.get(f"/inventory/{key}/spending?from=2025-05&to=2025-07").get_json()
        self.assertEqual(data["months"], ["2025-05", "2025-06", "2025-07"])
        self.assertEqual(data["month_totals"], [0, 10, 0])
        data = self.m.get(f"/inventory/{key}/spending?from=all&to=2025-07").get_json()
        self.assertEqual(data["first"], "2025-06")


if __name__ == "__main__":
    unittest.main()
