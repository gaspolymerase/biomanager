"""All databases, and the buttons by every database's name.

The mouse colony, zebrafish and plasmid pages are databases like any other:
listed only when the lab has them, added from New database, configured and
taken out by an admin (nothing is deleted)."""
from __future__ import annotations

import re

from tests.base import AppTestCase, one, uniq


class DatabasesPageTests(AppTestCase):
    def switch(self, key, on):
        return self.a.post(f"/organisms/builtin/{key}/switch", data={"on": "1" if on else "0"})

    def tearDown(self):
        for key in ("colony", "zebrafish", "plasmids"):
            self.switch(key, True)
        super().tearDown()

    def test_there_is_no_built_in_section(self):
        html = self.get_ok(self.a, "/organisms/")
        self.assertNotIn("Built in", html)
        self.assertIn("Animals", html)
        self.assertIn("Molecular &amp; supplies", html)

    def test_a_database_the_lab_took_out_is_not_listed_even_for_admins(self):
        self.switch("zebrafish", False)
        html = self.get_ok(self.a, "/organisms/")
        self.assertNotIn('href="/zebrafish"', html)
        self.assertEqual(self.a.get("/zebrafish").status_code in (302, 403, 404), True)

    def test_new_database_offers_it_back_and_adding_it_restores_it(self):
        self.switch("plasmids", False)
        html = self.get_ok(self.a, "/organisms/new")
        self.assertIn("Ready-made databases", html)
        self.assertIn('action="/organisms/builtin/plasmids/switch"', html)
        r = self.switch("plasmids", True)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/plasmids", r.headers["Location"])
        self.assertIn('href="/plasmids"', self.get_ok(self.a, "/organisms/"))
        self.assertNotIn('action="/organisms/builtin/plasmids/switch"', self.get_ok(self.a, "/organisms/new"))

    def test_members_are_not_offered_the_ready_made_ones(self):
        self.switch("plasmids", False)
        self.assertNotIn("Ready-made databases", self.get_ok(self.m, "/organisms/new"))

    def test_only_admins_configure_or_take_out(self):
        r = self.m.get("/organisms/builtin/colony/configure")
        self.assertEqual(r.status_code, 302)
        self.m.post("/organisms/builtin/colony/switch", data={"on": "0"})
        self.assertIn('href="/colony?view=mice"', self.get_ok(self.a, "/organisms/"))
        html = self.get_ok(self.a, "/organisms/builtin/colony/configure")
        self.assertIn("Take out of the lab", html)

    def test_renaming_comes_back_to_the_configure_page(self):
        r = self.a.post("/organisms/builtin/zebrafish/rename", data={"label": uniq("Fish room")})
        self.assertIn("/organisms/builtin/zebrafish/configure", r.headers["Location"])
        self.a.post("/organisms/builtin/zebrafish/rename", data={"label": ""})


class DatabaseButtonsTests(AppTestCase):
    """Every database page: Configure (for whoever may) and All databases."""

    def buttons(self, client, path):
        html = self.get_ok(client, path)
        bar = html[html.index('class="toolbar-actions"'):]
        bar = bar[:bar.index("</div>")]
        return "All databases" in bar, "Configure" in bar

    def test_every_kind_of_database_has_both_for_an_admin(self):
        for path in ("/colony?view=mice", "/zebrafish", "/plasmids", "/stocks/drosophila",
                     "/inventory/reagents", "/inventory/orders"):
            self.assertEqual(self.buttons(self.a, path), (True, True), path)

    def test_members_see_all_databases_but_not_configure_the_colony(self):
        self.assertEqual(self.buttons(self.m, "/colony?view=mice"), (True, False))


class IconPickerTests(AppTestCase):
    def test_configuring_an_inventory_picks_the_icon_by_looking_at_it(self):
        html = self.get_ok(self.a, "/inventory/reagents/configure")
        picker = html[html.index('class="icon-picker"'):]
        picker = picker[:picker.index("</fieldset>")]
        radios = re.findall(r'<input type="radio" name="icon" value="([\w-]+)"', picker)
        self.assertIn("flask", radios)
        self.assertIn('<use href="/static/icons.svg#flask">', picker)
        self.assertNotIn("<select", picker)


class TheSidebarOrder(AppTestCase):
    """The lab arranges its databases by the work, not by what it added
    first (issue #39)."""

    def order_on_page(self, client):
        import re
        html = self.get_ok(client, "/organisms/")
        block = html.split('id="database-order"')[-1]
        return re.findall(r'name="key" value="([^"]+)"', block)[::2] or re.findall(r'name="key" value="([^"]+)"', block)

    def test_moving_one_down_changes_the_sidebar(self):
        from app.db import SessionLocal
        from app import lab
        before = self.order_on_page(self.a)
        self.assertIn("colony", before)
        first, second = before[0], before[1]
        self.post(self.a, "/organisms/order", {"key": first, "by": "down", "keys": before})
        after = self.order_on_page(self.a)
        self.assertEqual(after[:2], [second, first])
        # The rail follows it, not just this page.
        rail = self.get_ok(self.a, "/home")
        self.assertLess(rail.index(f'data-label="{self._label(second)}"'),
                        rail.index(f'data-label="{self._label(first)}"'))
        with SessionLocal() as s:
            self.assertEqual(lab.database_order(s)[:2], [second, first])

    def _label(self, key):
        return {"colony": "Mouse colony", "zebrafish": "Zebrafish", "plasmids": "Plasmids"}[key]

    def test_the_ends_do_not_move_past_themselves(self):
        before = self.order_on_page(self.a)
        self.post(self.a, "/organisms/order", {"key": before[0], "by": "up", "keys": before})
        self.assertEqual(self.order_on_page(self.a), before)
        self.post(self.a, "/organisms/order", {"key": before[-1], "by": "down", "keys": before})
        self.assertEqual(self.order_on_page(self.a), before)

    def test_a_database_nobody_arranged_joins_the_end(self):
        from app import lab
        from app.db import SessionLocal
        with SessionLocal() as s:
            lab.set_database_order(s, ["plasmids", "colony"])
            s.commit()
        order = self.order_on_page(self.a)
        self.assertEqual(order[:2], ["plasmids", "colony"])
        self.assertIn("inventory:samples", order[2:])
