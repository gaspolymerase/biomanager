"""The Glycerol stocks inventory (app/inventory.py PRESETS["glycerol_stocks"]):
bacteria carrying a plasmid, in −80 °C boxes. The plasmid's page lists its
stocks with the box each is in, apart from what was made from it."""
from tests.base import *  # noqa: F401,F403
from tests.base import one, uniq
from tests import test_inventory as inv_tests

from app import inventory, lab


class GlycerolStockTests(inv_tests.InventoryCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stocks = cls.new_module(cls.a, "glycerol_stocks")

    def plasmid(self):
        """A plasmid; returns its number."""
        return one("select plasmid_id from plasmids where id=?", self.make_plasmid(self.a, uniq("pLenti-")))

    def test_it_is_a_ready_made_database_and_a_survey_choice(self):
        self.assertIn("glycerol_stocks", inventory.PRESETS)
        self.assertIn("glycerol_stocks", lab.INVENTORY_CHOICES)
        self.assertIn("Glycerol stocks", self.get_ok(self.a, "/inventory/new"))
        html = self.get_ok(self.a, f"/inventory/{self.stocks}")
        for column in ("Plasmid", "Strain", "Colony / clone", "Resistance", "Checked by", "Frozen"):
            self.assertIn(column, html)

    def test_the_plasmid_page_lists_its_stocks_with_their_box(self):
        number = self.plasmid()
        box = self.make_rack(self.a, self.stocks, name=uniq("Gly box "))
        stock = uniq("Stbl3 clone ")
        sid = self.make_item(self.a, self.stocks, stock, attr_plasmid=str(number), category="Stbl3",
                             status="in stock", rack_id=str(box), position="A1")
        html = self.get_ok(self.a, f"/plasmid/{number}")
        card = html.split('id="glycerol-stocks"', 1)[1].split("</article>", 1)[0]
        self.assertIn(stock, card)
        self.assertIn(f"/inventory/{self.stocks}?open={sid}", card)
        self.assertIn("Gly box", card)
        # A stock holds the plasmid; it isn't something made from it.
        made = html.split('id="made-from"', 1)[1].split("</article>", 1)[0]
        self.assertNotIn(stock, made)
        self.assertNotIn("Used to make", html)

    def test_a_plasmid_without_stocks_says_where_they_will_show(self):
        html = self.get_ok(self.a, f"/plasmid/{self.plasmid()}")
        self.assertIn("Nothing yet. A glycerol stock whose", html)
