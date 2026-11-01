"""Hovering a database in the sidebar (app/rail_peek.py, static/rail-peek.js).

A look inside it: its count as Home has it, what is due soon, the person's
own latest records, and its tabs. Nothing in it the person may not see."""
from __future__ import annotations

from tests.base import AppTestCase, client_for, days_ago, make_user, one  # isort: skip


class PeekTests(AppTestCase):
    def test_the_colony_shows_its_count_tabs_and_your_latest(self):
        me = make_user()
        client = client_for(me)
        mine = self.make_mouse(client, me)
        other = make_user()
        theirs = self.make_mouse(client_for(other), other)
        before = client.get("/rail/peek?key=colony").get_json()["value"]
        self.make_mouse(client, me, status="sac", date_of_death=days_ago(1))    # ended: not counted
        self.make_mouse(client, me)
        r = client.get("/rail/peek?key=colony")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data["value"] - before, 1)                    # the active mice, as Home counts them
        self.assertIn("active mice · 2 yours", data["sub"])
        self.assertIn("/colony", data["url"])
        self.assertEqual([t["label"] for t in data["tabs"]][:3], ["Mice", "Cages", "Litters"])
        titles = [row["title"] for row in data["recent"]]
        self.assertIn(f"#{one('select mouse_id from mice where id=?', mine)}", titles)
        self.assertNotIn(f"#{one('select mouse_id from mice where id=?', theirs)}", titles)
        self.assertIn("due", data)

    def test_a_database_not_in_the_sidebar_is_not_found(self):
        client = client_for(make_user())
        self.assertEqual(client.get("/rail/peek?key=inventory:nothing-here").status_code, 404)
        self.assertEqual(client.get("/rail/peek").status_code, 400)

    def test_signed_out_gets_nothing(self):
        from app.app import app
        self.assertEqual(app.test_client().get("/rail/peek?key=colony").status_code, 401)

    def test_every_page_loads_the_hover_script(self):
        html = self.get_ok(client_for(make_user()), "/home")
        self.assertIn("rail-peek.js", html)
        self.assertIn('data-db-key="colony"', html)
