"""Stock as a number (#58): an item's Low at level, and its status following
the quantity, from the sheet and the dialog: in stock → low → empty and
back, a free-text quantity left alone, the owner told once each time it
runs low, and the migration that adds the column."""
from __future__ import annotations

import importlib

import sqlalchemy as sa

from tests.base import *  # noqa: F401,F403
from tests.base import make_user, client_for, one, uniq
from tests.test_inventory import InventoryCase
from app import i18n, inventory_service as svc  # noqa: E402
from app.db import SessionLocal  # noqa: E402


def stock_of(item_id: int) -> tuple:
    return tuple(one(f"select {c} from inventory_items where id=?", item_id) for c in ("status", "quantity", "low_at"))


def told(username: str) -> list[str]:
    from tests.base import rows
    return [r[0] for r in rows("select title from notifications where recipient_username=? order by id", username)]


class AmountTests(InventoryCase):
    def test_a_quantity_reads_as_a_number_or_not_at_all(self):
        for raw, want in (("12", 12.0), ("12.5", 12.5), ("2,5", 2.5), (" 3 ", 3.0), ("0", 0.0), (".5", 0.5),
                          ("1,000", 1000.0), ("12,500.5", 12500.5)):
            self.assertEqual(svc.amount(raw), want, raw)
        for raw in ("", "half a box", "2 boxes", "12 mL", "-1", "1,2,3", "nan", "inf", None):
            self.assertIsNone(svc.amount(raw), raw)
        self.assertEqual((svc.number_text(2.0), svc.number_text(2.5), svc.number_text(None)), ("2", "2.5", ""))


class LowStockTests(InventoryCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.new_module(cls.a, "reagents")
        cls.url = f"/inventory/{cls.key}/items"

    def lab_item(self, quantity="10", low_at="3", **fields) -> int:
        """Lab common stock of the member's, so the other member may use it."""
        return self.make_item(self.m, self.key, uniq("Tris "), quantity=quantity, unit="g", low_at=low_at,
                              is_shared="1", **fields)

    def sheet(self, client, rid, **cells):
        r = self.autosave(client, f"{self.url}/{rid}/update", cells)
        self.assertSaved(r)
        return r.get_json()

    def test_the_status_follows_the_quantity_both_ways(self):
        rid = self.lab_item()
        self.assertEqual(stock_of(rid), ("in stock", "10", 3.0))
        answer = self.sheet(self.o, rid, quantity="3", quantity_was="10")
        self.assertEqual(stock_of(rid)[0], "low")
        # The row's status cell is told, so the pill changes at once.
        self.assertEqual(answer["row"]["values"]["status"], "low")
        self.sheet(self.o, rid, quantity="0")
        self.assertEqual(stock_of(rid)[0], "empty")
        self.sheet(self.o, rid, quantity="1")
        self.assertEqual(stock_of(rid)[0], "low")
        self.sheet(self.o, rid, quantity="2,5 ".strip())
        self.assertEqual(stock_of(rid)[0], "low")
        self.sheet(self.o, rid, quantity="20")
        self.assertEqual(stock_of(rid)[0], "in stock")

    def test_editing_the_level_moves_it_too(self):
        rid = self.lab_item(quantity="5", low_at="")
        self.assertEqual(stock_of(rid), ("in stock", "5", None))
        answer = self.sheet(self.m, rid, low_at="5")
        self.assertEqual(stock_of(rid), ("low", "5", 5.0))
        self.assertEqual(answer["row"]["values"]["low_at"], "5")
        self.sheet(self.m, rid, low_at="4,5")
        self.assertEqual(stock_of(rid), ("in stock", "5", 4.5))
        # No level: the status is only what people set.
        self.sheet(self.m, rid, low_at="")
        self.sheet(self.m, rid, quantity="1")
        self.assertEqual(stock_of(rid), ("in stock", "1", None))

    def test_a_level_that_is_no_number_is_refused(self):
        rid = self.lab_item()
        self.assertRefused(self.autosave(self.m, f"{self.url}/{rid}/update", {"low_at": "a few"}))
        self.assertEqual(stock_of(rid), ("in stock", "10", 3.0))

    def test_a_free_text_quantity_is_left_alone(self):
        rid = self.lab_item()
        self.sheet(self.m, rid, quantity="half a bottle")
        self.assertEqual(stock_of(rid)[0], "in stock")
        self.sheet(self.m, rid, low_at="100")
        self.assertEqual(stock_of(rid)[0], "in stock")

    def test_discarded_and_a_status_set_by_hand_stay(self):
        rid = self.lab_item()
        self.sheet(self.m, rid, status="discarded", status_was="in stock")
        self.sheet(self.m, rid, quantity="1")
        self.assertEqual(stock_of(rid)[0], "discarded")
        # Low and a full quantity in the same save: the status chosen wins.
        rid = self.lab_item()
        self.sheet(self.m, rid, status="low", status_was="in stock", quantity="50")
        self.assertEqual(stock_of(rid)[0], "low")
        # Set back to in stock by hand with the quantity as it is: kept.
        self.sheet(self.m, rid, quantity="1")
        self.sheet(self.m, rid, status="in stock", status_was="low")
        self.assertEqual(stock_of(rid)[0], "in stock")

    def test_the_dialog_moves_it_like_the_sheet(self):
        rid = self.lab_item()
        self.post(self.m, f"{self.url}/save", {"id": str(rid), "name": "Tris", "status": "in stock",
                                                "quantity": "2", "unit": "g", "low_at": "3"})
        self.assertEqual(stock_of(rid), ("low", "2", 3.0))
        # A new one below its level starts low.
        new = self.make_item(self.m, self.key, uniq("NaCl "), quantity="1", low_at="2")
        self.assertEqual(stock_of(new)[0], "low")

    def test_the_owner_is_told_once_each_time_it_runs_low_in_their_language(self):
        owner = make_user(uniq("owner"))
        mine = client_for(owner)
        with SessionLocal() as s:
            svc.set_setting(s, i18n.preference_key(owner), "zh")
            s.commit()
        rid = self.make_item(mine, self.key, uniq("Agarose "), quantity="10", unit="g", low_at="3", is_shared="1")
        for quantity in ("2", "1", "0", "5", "2,5"):
            self.sheet(self.o, rid, quantity=quantity)
        titles = told(owner)
        self.assertEqual(len(titles), 2, titles)
        self.assertIn("库存不足", titles[0])
        self.assertIn("2 g", titles[0])
        self.assertIn(self.other, titles[0])
        # Their own change is not news to them.
        self.sheet(mine, rid, quantity="9")
        self.sheet(mine, rid, quantity="1")
        self.assertEqual(len(told(owner)), 2)

    def test_the_sheet_and_dialog_have_it_and_home_shows_the_quantity(self):
        rid = self.lab_item(quantity="1", low_at="4")
        page = self.get_ok(self.m, f"/inventory/{self.key}")
        self.assertIn('data-sort-key="low_at"', page)
        self.assertIn(f'name="low_at" form="inv-{rid}" value="4"', page)
        self.assertIn('<input name="low_at" inputmode="decimal"', page)
        self.assertIn('name="quantity" form="inv-%s" value="1"' % rid, page)
        restock = [r for r in svc.attention_items(SessionLocal(), limit=10_000) if r["id"] == rid]
        self.assertEqual(restock[0]["quantity"], "1 g")
        home = self.get_ok(self.m, "/home")
        self.assertIn("1 g", home)

    def test_an_inventory_without_low_has_no_column(self):
        key = self.new_module(self.a, "orders")
        self.assertFalse(svc.tracks_low(svc.view(svc.get_module(SessionLocal(), key))))
        page = self.get_ok(self.a, f"/inventory/{key}")
        self.assertNotIn('data-sort-key="low_at"', page)
        self.assertNotIn('name="low_at"', page)

    def test_used_up_is_where_a_list_without_empty_goes(self):
        key = self.new_module(self.a, "viruses")
        rid = self.make_item(self.m, key, uniq("AAV "), quantity="5", low_at="2")
        self.assertSaved(self.autosave(self.m, f"/inventory/{key}/items/{rid}/update", {"quantity": "0"}))
        self.assertEqual(stock_of(rid)[0], "used up")


class MigrationTests(InventoryCase):
    def test_the_column_is_added_where_it_is_missing_and_left_where_it_is(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        migration = importlib.import_module("migrations.versions.0031_low_stock")
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.exec_driver_sql("create table inventory_items (id integer primary key, quantity varchar(60))")
            conn.exec_driver_sql("insert into inventory_items (quantity) values ('12')")
            for _ in range(2):
                with Operations.context(MigrationContext.configure(conn)):
                    migration.upgrade()
            columns = {c["name"] for c in sa.inspect(conn).get_columns("inventory_items")}
            self.assertIn("low_at", columns)
            self.assertEqual(conn.exec_driver_sql("select quantity, low_at from inventory_items").fetchall(),
                             [("12", None)])
