"""A colony keeps everything it ever had: the sheets list what is current,
and what ended long ago behind "Show them" (colony_routes.colony_context,
inventory_routes._recent_items, stock_routes)."""
from __future__ import annotations

import json
from datetime import date, timedelta

# tests.base first: it points the app at a throwaway database before app is imported.
from tests.base import AppTestCase, execute, one, uniq


class LongEnded(AppTestCase):
    def test_mice_that_ended_long_ago_wait_behind_a_link(self):
        recent = self.make_mouse(self.a, self.admin, cage=uniq("C"))
        old = self.make_mouse(self.a, self.admin, cage=uniq("C"))
        execute("update mice set status='sac', date_of_death=? where id=?", (date.today() - timedelta(days=400)).isoformat(), old)
        old_id = one("select mouse_id from mice where id=?", old)
        recent_id = one("select mouse_id from mice where id=?", recent)
        html = self.get_ok(self.a, "/colony?view=mice&scope=all")
        self.assertIn(f'data-mouse_id="{recent_id}"', html)
        self.assertNotIn(f'data-mouse_id="{old_id}"', html)
        self.assertIn("data-hidden-ended", html)
        self.assertIn(f'data-mouse_id="{old_id}"', self.get_ok(self.a, "/colony?view=mice&scope=all&ended=all"))

    def test_a_used_up_reagent_from_long_ago_too(self):
        item = self.make_item(self.a, "reagents", name=uniq("Old buffer "))
        execute("update inventory_items set status='used up', attrs=? where id=?",
                json.dumps({"used_up_on": (date.today() - timedelta(days=200)).isoformat()}), item)
        name = one("select name from inventory_items where id=?", item)
        self.get_ok(self.a, "/home")                  # past the "Saved" message
        self.assertNotIn(name, self.get_ok(self.a, "/inventory/reagents"))
        self.assertIn(name, self.get_ok(self.a, "/inventory/reagents?ended=all"))

    def test_the_other_tabs_build_no_mouse_rows(self):
        html = self.get_ok(self.a, "/colony?view=experiments")
        self.assertNotIn("data-mouse_id=", html)
