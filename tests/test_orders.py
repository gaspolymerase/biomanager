"""Orders: what an order needs before it is placed, the status chips,
values typed before offered again, "Order again" from Reagents and
Antibodies, and the offer to add a received order to stock."""
from __future__ import annotations

import html as htmllib
import json
import re
import unittest

from tests.base import AUTOSAVE, T, days_ahead, execute, flash_text, location, one, uniq
from tests.test_inventory import InventoryCase, attrs_of, item, items_named, settings_of


REORDER = 'data-record-edit="item-dialog" data-reorder-open'


def payload_of(html: str, marker: str) -> dict:
    """The data-record-payload of the element carrying `marker`."""
    tag = re.search(rf"<button [^>]*{marker}[^>]*>", html)
    assert tag, f"no element with {marker}"
    found = re.search(r"""data-record-payload=(?:"([^"]*)"|'([^']*)')""", tag.group(0))
    return json.loads(htmllib.unescape(found.group(1) if found.group(1) is not None else found.group(2)))


def datalist(html: str, column: str) -> list[str]:
    found = re.search(rf'<datalist id="inv-rem-{column}">(.*?)</datalist>', html, re.S)
    return [htmllib.unescape(v) for v in re.findall(r'<option value="([^"]*)"', found.group(1))] if found else []


def fill_data(html: str) -> dict:
    found = re.search(r'<script type="application/json" id="inv-fill-data">(.*?)</script>', html, re.S)
    return json.loads(found.group(1))


class OrdersCase(InventoryCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.orders = cls.new_module(cls.a, "orders")
        cls.reagents = cls.new_module(cls.a, "reagents")
        cls.antibodies = cls.new_module(cls.a, "antibodies")

    def place(self, client=None, **fields):
        """A new order from the dialog, with only the fields given."""
        return (client or self.a).post(f"/inventory/{self.orders}/items/save", data={"id": "", **fields})

    def order(self, client=None, **fields) -> int:
        return self.make_item(client or self.a, self.orders, fields.pop("name", None) or uniq("Order"),
                              **{"status": "requested", **fields})


# ======================================================================= required

class RequiredTests(OrdersCase):

    def test_an_order_without_vendor_catalog_or_quantity_is_not_placed(self):
        name = uniq("Taq")
        r = self.place(name=name, vendor="NEB", catalog_number="", quantity="")
        self.assertIn("Fill in Catalog # and Quantity to add an order.", flash_text(self.a.get(location(r))))
        self.assertEqual(items_named(self.orders, name), [])

    def test_an_order_with_everything_needed_is_placed(self):
        name = uniq("Taq")
        self.place(name=name, vendor="NEB", catalog_number="M0273S", quantity="2")
        self.assertEqual(len(items_named(self.orders, name)), 1)

    def test_several_orders_at_once_need_it_too(self):
        name = uniq("Tips")
        self.place(name=name, count="3", vendor="Rainin")
        self.assertEqual(items_named(self.orders, name), [])

    def test_a_required_value_cannot_be_emptied_later(self):
        oid = self.order(vendor="NEB")
        r = self.autosave(self.a, f"/inventory/{self.orders}/items/{oid}/update", {"vendor": "", "notes": "x"})
        self.assertRefused(r)
        self.assertIn("Vendor", r.get_json()["error"])
        self.assertEqual(item(oid)["vendor"], "NEB")

    def test_an_old_order_missing_a_required_value_can_still_be_edited(self):
        oid = self.order()
        execute("update inventory_items set catalog_number='' where id=?", oid)
        self.assertSaved(self.autosave(self.a, f"/inventory/{self.orders}/items/{oid}/update",
                                       {"catalog_number": "", "notes": "still fine"}))
        self.assertEqual(item(oid)["notes"], "still fine")

    def test_an_order_made_before_required_existed_follows_the_preset(self):
        key = self.new_module(self.a, "orders")
        settings = settings_of(key)
        settings.pop("required")
        execute("update inventory_modules set settings=? where key=?", json.dumps(settings), key)
        name = uniq("Old")
        self.a.post(f"/inventory/{key}/items/save", data={"id": "", "name": name})
        self.assertEqual(items_named(key, name), [])

    def test_configure_chooses_what_is_required_but_an_order_always_needs_a_name(self):
        key = self.new_module(self.a, "orders")
        self.post(self.a, f"/inventory/{key}/configure",
                  data=self.configure_form(key, required_sent="1", required=["vendor", "attr_account"]))
        self.assertEqual(settings_of(key)["required"], ["vendor", "attr_account"])
        name = uniq("Gloves")
        self.a.post(f"/inventory/{key}/items/save", data={"id": "", "name": name, "vendor": "Kimtech"})
        self.assertEqual(items_named(key, name), [])  # no account
        self.a.post(f"/inventory/{key}/items/save",
                    data={"id": "", "name": name, "vendor": "Kimtech", "attr_account": "R01"})
        self.assertEqual(len(items_named(key, name)), 1)  # catalog # and quantity no longer needed
        self.a.post(f"/inventory/{key}/items/save", data={"id": "", "name": "", "vendor": "K", "attr_account": "R01"})
        self.assertEqual(one("select count(*) from inventory_items i join inventory_modules m on m.id=i.module_id_fk "
                             "where m.key=? and i.name=''", key), 0)

    def test_a_configure_form_without_the_required_card_keeps_the_choice(self):
        key = self.new_module(self.a, "orders")
        self.post(self.a, f"/inventory/{key}/configure", data=self.configure_form(key))
        self.assertEqual(settings_of(key)["required"], ["name", "vendor", "catalog_number", "quantity"])

    def test_any_inventory_can_require_a_column(self):
        key = self.new_module(self.a, "reagents")
        self.post(self.a, f"/inventory/{key}/configure",
                  data=self.configure_form(key, required_sent="1", required=["expires_on"]))
        name = uniq("NaCl")
        self.a.post(f"/inventory/{key}/items/save", data={"id": "", "name": name})
        self.assertEqual(items_named(key, name), [])
        self.a.post(f"/inventory/{key}/items/save", data={"id": "", "name": name, "expires_on": days_ahead(90)})
        self.assertEqual(len(items_named(key, name)), 1)

    def test_the_dialog_marks_what_is_required(self):
        html = self.get_ok(self.m, f"/inventory/{self.orders}")
        for name in ("name", "vendor", "catalog_number", "quantity"):
            self.assertRegex(html, rf'<input name="{name}" data-required="1"')
        self.assertNotRegex(html, r'<input name="lot" data-required')
        self.assertIn('class="req-star"', html)
        configure = self.get_ok(self.a, f"/inventory/{self.orders}/configure")
        self.assertIn('name="required" value="vendor" checked', configure)


# ======================================================================= status chips

class StatusChipTests(OrdersCase):

    def test_the_orders_page_filters_by_each_status(self):
        oid = self.order(status="ordered")
        html = self.get_ok(self.m, f"/inventory/{self.orders}")
        for status in ("requested", "ordered", "received", "cancelled"):
            self.assertIn(f'data-dt-filter="status:{status}"', html)
        self.assertRegex(html, rf'<tr data-id="{oid}"[^>]*data-status="ordered"')

    def test_each_chip_counts_its_orders(self):
        key = self.new_module(self.a, "orders")
        for status in ("requested", "requested", "cancelled"):
            self.make_item(self.a, key, uniq("O"), status=status)
        html = self.get_ok(self.a, f"/inventory/{key}")
        self.assertRegex(html, r'data-count-status="requested">Requested <span class="dt-chip-count">2<')
        self.assertRegex(html, r'data-count-status="cancelled">Cancelled <span class="dt-chip-count">1<')
        self.assertRegex(html, r'data-count-status="received">Received <span class="dt-chip-count">0<')

    def test_inventories_without_a_board_have_no_status_chips(self):
        html = self.get_ok(self.a, f"/inventory/{self.reagents}")
        self.assertNotIn('data-dt-filter="status:', html)


# ======================================================================= remembered

class RememberedTests(OrdersCase):

    def test_values_typed_before_are_offered_for_each_column(self):
        vendor, unit, account = uniq("Vendor "), uniq("pack"), uniq("R01-")
        self.order(vendor=vendor, unit=unit, attr_account=account)
        html = self.get_ok(self.m, f"/inventory/{self.orders}")
        self.assertIn(vendor, datalist(html, "vendor"))
        self.assertIn(unit, datalist(html, "unit"))
        self.assertIn(account, datalist(html, "attr_account"))
        self.assertIn('list="inv-rem-vendor"', html)

    def test_suggestions_are_listed_once_whatever_the_case(self):
        vendor = uniq("Zymo ")
        self.order(vendor=vendor)
        self.order(vendor=vendor.upper())
        self.assertEqual(sum(1 for v in datalist(self.get_ok(self.a, f"/inventory/{self.orders}"), "vendor")
                             if v.lower() == vendor.lower()), 1)

    def test_orders_suggest_what_is_in_stock_and_stock_what_was_ordered(self):
        reagent, ordered = uniq("Agarose "), uniq("Ethidium ")
        self.make_item(self.a, self.reagents, reagent, vendor="Sigma", catalog_number="A9539")
        self.order(name=ordered)
        self.assertIn(reagent, datalist(self.get_ok(self.a, f"/inventory/{self.orders}"), "name"))
        self.assertIn(ordered, datalist(self.get_ok(self.a, f"/inventory/{self.reagents}"), "name"))

    def test_picking_an_earlier_name_or_catalog_number_fills_the_rest(self):
        name = uniq("Q5 ")
        self.order(name=name, vendor="NEB", catalog_number=uniq("M0491"), quantity="3", unit="kit",
                   category="reagent", attr_price="310", attr_account="R01")
        fills = fill_data(self.get_ok(self.a, f"/inventory/{self.orders}"))
        by_name = fills["name"][name.lower()]
        self.assertEqual((by_name["vendor"], by_name["quantity"], by_name["attr_price"], by_name["attr_account"]),
                         ("NEB", "3", "310", "R01"))
        self.assertEqual(fills["catalog_number"][by_name["catalog_number"].lower()]["name"], name)
        self.assertTrue(by_name["_from"].startswith("order #"))

    def test_the_newest_entry_is_the_one_filled_from(self):
        name = uniq("Tris ")
        self.order(name=name, attr_price="10")
        self.order(name=name, attr_price="12")
        fills = fill_data(self.get_ok(self.a, f"/inventory/{self.orders}"))
        self.assertEqual(fills["name"][name.lower()]["attr_price"], "12")

    def test_stock_lends_what_it_is_but_not_how_much_is_on_the_shelf(self):
        name = uniq("DAPI ")
        self.make_item(self.a, self.reagents, name, vendor="Thermo", catalog_number="D1306", quantity="5", unit="mg")
        filled = fill_data(self.get_ok(self.a, f"/inventory/{self.orders}"))["name"][name.lower()]
        self.assertEqual((filled["vendor"], filled["catalog_number"]), ("Thermo", "D1306"))
        self.assertNotIn("quantity", filled)
        self.assertNotIn("unit", filled)

    def test_someone_elses_personal_inventory_lends_nothing(self):
        r = self.m.post("/inventory/new", data={"preset": "reagents", "label": uniq("My reagents "), "audience": "me"})
        key = location(r).split("?")[0].rsplit("/", 1)[1]
        secret = uniq("Secret ")
        self.make_item(self.m, key, secret, vendor="Hidden")
        self.assertNotIn(secret, datalist(self.get_ok(self.a, f"/inventory/{self.orders}"), "name"))
        self.assertIn(secret, datalist(self.get_ok(self.m, f"/inventory/{self.orders}"), "name"))


# ======================================================================= order again

class OrderAgainTests(OrdersCase):

    def test_reagents_and_antibodies_have_order_again(self):
        rid = self.make_item(self.a, self.reagents, uniq("PBS "))
        html = self.get_ok(self.m, f"/inventory/{self.reagents}")
        # A button in a form that only opens the page, like the row's other actions.
        self.assertIn(f'<form method="get" action="/inventory/{self.first_orders()}">', html)
        self.assertIn(f'<input type="hidden" name="reorder" value="{self.reagents}:{rid}">', html)
        aid = self.make_item(self.a, self.antibodies, uniq("GFP "))
        self.assertIn(f'name="reorder" value="{self.antibodies}:{aid}"', self.get_ok(self.m, f"/inventory/{self.antibodies}"))

    def first_orders(self) -> str:
        return one("select key from inventory_modules where kind='orders' and private_to='' and enabled "
                   "order by position, id limit 1")

    def test_order_again_opens_a_new_order_with_the_reagents_details(self):
        name = uniq("Agar ")
        rid = self.make_item(self.a, self.reagents, name, vendor="BD", catalog_number="214010", quantity="400", unit="g")
        html = self.get_ok(self.m, f"/inventory/{self.orders}?reorder={self.reagents}:{rid}")
        payload = payload_of(html, "data-reorder-open")
        number = one("select number from inventory_items where id=?", rid)
        self.assertEqual((payload["name"], payload["vendor"], payload["catalog_number"], payload["category"],
                          payload["owner"], payload["status"]), (name, "BD", "214010", "reagent", self.member, "requested"))
        self.assertNotIn("id", payload)
        self.assertNotIn("quantity", payload)  # how many to order is asked, not guessed from the shelf
        self.assertIn(f"#{number}", payload["notes"])
        self.assertIn("Say how many", payload["_hint"])

    def test_order_again_takes_quantity_price_and_grant_from_the_last_order(self):
        name = uniq("Anti-GFP ")
        oid = self.order(name=name, vendor="Abcam", catalog_number=uniq("ab"), quantity="1", unit="vial",
                         category="antibody", attr_price="420", attr_account="R01-99", status="received")
        self.post(self.a, f"/inventory/{self.orders}/items/{oid}/to-reagents", data={"target": self.antibodies})
        [aid] = items_named(self.antibodies, name)
        payload = payload_of(self.get_ok(self.a, f"/inventory/{self.orders}?reorder={self.antibodies}:{aid}"),
                             "data-reorder-open")
        self.assertEqual((payload["category"], payload["quantity"], payload["unit"], payload["attr_price"],
                          payload["attr_account"]), ("antibody", "1", "vial", "420", "R01-99"))
        self.assertIn(f"order #{item(oid)['number']}", payload["_hint"])

    def test_order_again_warns_when_it_is_already_on_order(self):
        name, cat = uniq("ECL "), uniq("RPN")
        rid = self.make_item(self.a, self.reagents, name, vendor="Cytiva", catalog_number=cat)
        page = f"/inventory/{self.orders}?reorder={self.reagents}:{rid}"
        self.assertNotIn("_warn", payload_of(self.get_ok(self.m, page), "data-reorder-open"))
        oid = self.order(name=name, vendor="Cytiva", catalog_number=cat, quantity="2", status="ordered")
        payload = payload_of(self.get_ok(self.m, page), "data-reorder-open")
        self.assertTrue(payload["_warn"])
        self.assertIn(f"Already on order: #{item(oid)['number']}, ordered", payload["_hint"])
        # Received: no longer on order.
        self.post(self.a, f"/inventory/{self.orders}/items/save",
                  data={"id": str(oid), "name": name, "vendor": "Cytiva", "catalog_number": cat, "quantity": "2",
                        "status": "received"})
        self.assertNotIn("_warn", payload_of(self.get_ok(self.m, page), "data-reorder-open"))

    def test_the_reagent_shows_it_is_on_order(self):
        name = uniq("Glycine ")
        rid = self.make_item(self.a, self.reagents, name, vendor="Sigma", catalog_number=uniq("G"))
        self.a.post(f"/inventory/{self.first_orders()}/items/save", data={
            "id": "", "name": name, "vendor": "Sigma", "catalog_number": "X-1", "quantity": "1",
            "notes": f"Reorder of {one('select label from inventory_modules where key=?', self.reagents)} "
                     f"#{item(rid)['number']}"})
        html = self.get_ok(self.m, f"/inventory/{self.reagents}")
        row = html.split(f'value="{self.reagents}:{rid}"', 1)[1][:600]
        self.assertIn("On order", row)
        self.assertIn("Already on order: #", row)

    def test_order_again_is_placed_like_any_order(self):
        name = uniq("EDTA ")
        rid = self.make_item(self.a, self.reagents, name, vendor="Sigma", catalog_number="E9884")
        self.place(self.m, name=name, vendor="Sigma", catalog_number="E9884", quantity="1",
                   notes=f"Reorder of Reagents #{rid}", status="requested")
        [oid] = items_named(self.orders, name)
        self.assertEqual((item(oid)["owner"], item(oid)["status"]), (self.member, "requested"))

    def test_a_missing_or_hidden_record_opens_nothing(self):
        html = self.get_ok(self.a, f"/inventory/{self.orders}?reorder={self.reagents}:999999")
        self.assertNotIn(REORDER, html)
        r = self.a.post("/inventory/new", data={"preset": "reagents", "label": uniq("Mine "), "audience": "me"})
        key = location(r).split("?")[0].rsplit("/", 1)[1]
        rid = self.make_item(self.a, key, uniq("Private "))
        self.assertNotIn(REORDER, self.get_ok(self.m, f"/inventory/{self.orders}?reorder={key}:{rid}"))
        oid = self.order()
        self.assertNotIn(REORDER, self.get_ok(self.a, f"/inventory/{self.orders}?reorder={self.orders}:{oid}"))

    def test_saving_the_dialog_does_not_reopen_it(self):
        name = uniq("Tween ")
        r = self.a.post(f"/inventory/{self.orders}/items/save",
                        data={"id": "", "name": name, "vendor": "Sigma", "catalog_number": "P1379", "quantity": "1"},
                        headers={"Referer": f"http://localhost/inventory/{self.orders}?reorder={self.reagents}:1&view=x"})
        self.assertEqual(location(r), f"/inventory/{self.orders}?view=x")


# ======================================================================= received → stock

class OfferStockTests(OrdersCase):

    def test_marking_an_order_received_offers_to_add_it_to_stock(self):
        oid = self.order(status="ordered")
        r = self.autosave(self.a, f"/inventory/{self.orders}/items/{oid}/update", {"status": "received"})
        self.assertSaved(r)
        self.assertIs(r.get_json().get("offer_stock"), True)
        again = self.autosave(self.a, f"/inventory/{self.orders}/items/{oid}/update", {"notes": "on the shelf"})
        self.assertNotIn("offer_stock", again.get_json())

    def test_a_board_move_to_received_offers_too(self):
        oid = self.order(status="ordered")
        r = self.a.post(f"/inventory/{self.orders}/items/{oid}/status", data={"status": "received"}, headers=AUTOSAVE)
        self.assertIs(r.get_json().get("offer_stock"), True)
        cancelled = self.order()
        r = self.a.post(f"/inventory/{self.orders}/items/{cancelled}/status", data={"status": "cancelled"})
        self.assertNotIn("offer_stock", r.get_json())

    def test_the_dialog_comes_back_to_the_page_with_the_offer(self):
        oid = self.order(status="ordered")
        r = self.a.post(f"/inventory/{self.orders}/items/save", data={"id": str(oid), "status": "received"},
                        headers={"Referer": f"http://localhost/inventory/{self.orders}"})
        self.assertEqual(location(r), f"/inventory/{self.orders}?offer={oid}")

    def test_an_order_already_in_stock_is_not_offered_again(self):
        oid = self.order(status="received")
        self.post(self.a, f"/inventory/{self.orders}/items/{oid}/to-reagents", data={"target": self.reagents})
        self.a.post(f"/inventory/{self.orders}/items/{oid}/status", data={"status": "ordered"})
        r = self.a.post(f"/inventory/{self.orders}/items/{oid}/status", data={"status": "received"})
        self.assertNotIn("offer_stock", r.get_json())

    def test_the_page_has_the_offer_dialog_with_every_stock_inventory(self):
        self.order(self.m, status="received")
        html = self.get_ok(self.m, f"/inventory/{self.orders}")
        self.assertIn('id="stock-offer-dialog"', html)
        self.assertIn(f'<option value="{self.reagents}" data-kind="reagents">', html)
        self.assertIn(f'<option value="{self.antibodies}" data-kind="antibodies">', html)
        self.assertIn('data-on-click="offer-stock"', html)
        self.assertIn("To stock</button>", html)

    def test_stock_made_from_an_order_keeps_its_lot_and_expiry_and_opens(self):
        name = uniq("RNase ")
        oid = self.order(name=name, lot="L42", expires_on=days_ahead(200), status="received")
        r = self.a.post(f"/inventory/{self.orders}/items/{oid}/to-reagents", data={"target": self.reagents, "shared": "1"})
        [rid] = items_named(self.reagents, name)
        self.assertEqual(location(r), f"/inventory/{self.reagents}?open={rid}")
        got = item(rid)
        self.assertEqual((got["lot"], got["expires_on"], bool(got["is_shared"])), ("L42", days_ahead(200), True))
        self.assertEqual(attrs_of(oid)["stocked_as"], f"{self.reagents}:{got['number']}")
        self.assertIn("Add where it is kept and when it expires", flash_text(self.a.get(location(r))))


    def test_an_order_and_the_stock_it_became_link_to_each_other(self):
        name = uniq("DNase ")
        oid = self.order(name=name, status="received")
        self.a.post(f"/inventory/{self.orders}/items/{oid}/to-reagents", data={"target": self.reagents, "shared": "1"})
        [rid] = items_named(self.reagents, name)
        number = item(rid)["number"]
        orders_page = self.get_ok(self.a, f"/inventory/{self.orders}")
        self.assertIn(f'href="/inventory/{self.reagents}?open={rid}"', orders_page)
        self.assertIn(f"#{number}</a>", orders_page)
        stock_page = self.get_ok(self.a, f"/inventory/{self.reagents}")
        self.assertIn(f'href="/inventory/{self.orders}?open={oid}"', stock_page)


class StockRequiredTests(OrdersCase):
    """Add to stock respects what the stock inventory requires."""

    def test_missing_required_fields_open_a_prefilled_dialog_and_link_on_save(self):
        key = self.new_module(self.a, "reagents")
        self.post(self.a, f"/inventory/{key}/configure",
                  data=self.configure_form(key, required_sent="1", required=["name", "attr_cas"]))
        name = uniq("Tris ")
        oid = self.order(name=name, status="received")
        r = self.a.post(f"/inventory/{self.orders}/items/{oid}/to-reagents", data={"target": key, "shared": "1"})
        self.assertEqual(items_named(key, name), [])                       # nothing made yet
        self.assertIn(f"from_order={self.orders}", location(r))
        page = self.get_ok(self.a, location(r))
        payload = payload_of(page, "data-reorder-open")
        self.assertEqual((payload["name"], payload["from_order"], payload["is_shared"]), (name, f"{self.orders}:{oid}", "1"))
        self.a.post(f"/inventory/{key}/items/save", data={
            "id": "", "name": name, "attr_cas": "77-86-1", "from_order": payload["from_order"]})
        [rid] = items_named(key, name)
        self.assertEqual(attrs_of(oid)["stocked_as"], f"{key}:{item(rid)['number']}")


if __name__ == "__main__":
    unittest.main()
