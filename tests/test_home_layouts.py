"""Home layouts: Classic, Tracks and Freezer (app/home_layouts.py).

Each person picks one; it is kept for them alone. Tracks and Freezer read
the same agenda the Classic cards show."""
from __future__ import annotations

import re

from tests.base import AppTestCase, client_for, days_ago, make_user, uniq


class HomeLayoutTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.post(self.m, "/home/layout", {"layout": "classic"})

    def home(self, client=None, query=""):
        return self.get_ok(client or self.m, "/home" + query)

    def test_classic_is_the_default(self):
        fresh = client_for(make_user(uniq("fresh")))
        html = self.get_ok(fresh, "/home")
        self.assertIn("Mice older than 30 weeks", html)
        self.assertRegex(html, re.compile(r'value="classic"[^>]*aria-pressed="true"'))
        self.assertNotIn('class="trk"', html)

    def test_switching_is_kept_per_person(self):
        self.post(self.m, "/home/layout", {"layout": "tracks"})
        self.assertIn('class="trk"', self.home())
        self.assertIn('class="trk"', self.home())
        self.assertNotIn('class="trk"', self.home(self.o))

    def test_an_unknown_layout_falls_back_to_classic(self):
        self.post(self.m, "/home/layout", {"layout": "tracks"})
        self.post(self.m, "/home/layout", {"layout": "nonsense"})
        self.assertIn("Mice older than 30 weeks", self.home())

    def test_settings_saves_the_layout_too(self):
        self.post(self.m, "/settings", {"action": "profile", "display_name": "", "home_layout": "freezer"})
        self.assertIn('class="frz"', self.home())
        self.assertIn('<option value="freezer" selected>Freezer</option>', self.get_ok(self.m, "/settings"))

    def test_settings_without_the_field_leaves_the_layout_alone(self):
        self.post(self.m, "/home/layout", {"layout": "tracks"})
        self.post(self.m, "/settings", {"action": "profile", "display_name": ""})
        self.assertIn('class="trk"', self.home())


class TracksTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.post(self.m, "/home/layout", {"layout": "tracks"})

    def test_a_weaning_lands_on_its_day(self):
        colony = self.make_colony(self.m, self.member, n_mice=1, dob=days_ago(19))
        html = self.get_ok(self.m, "/home")
        self.assertIn(f"Wean Litter {colony['litter']} · cage {colony['cage']}", html)
        self.assertIn("Weaning · P21", html)

    def test_mice_past_30_weeks_are_overdue(self):
        colony = self.make_colony(self.m, self.member, n_mice=1, dob=days_ago(30 * 7 + 10))
        html = self.get_ok(self.m, "/home")
        self.assertIn("Past 30 w:", html)
        self.assertIn(f"cage {colony['cage']}", html)

    def test_span_chooses_the_days_and_ignores_odd_values(self):
        self.assertIn("7 d", self.get_ok(self.m, "/home?span=7"))
        self.assertIn("28 d", self.get_ok(self.m, "/home?span=28"))
        self.assertIn("14 d", self.get_ok(self.m, "/home?span=999"))
        self.assertIn("14 d", self.get_ok(self.m, "/home?span=abc"))


class FreezerTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.post(self.m, "/home/layout", {"layout": "freezer"})

    def test_work_in_a_placed_cage_is_on_the_pull_list_and_ringed_on_the_rack(self):
        rack = uniq("Rack")
        self.m.post("/colony/racks/save", data={"id": "", "name": rack, "rows": "3", "cols": "3"})
        rack_id = self.get_rack(rack)
        colony = self.make_colony(self.m, self.member, n_mice=1, dob=days_ago(30 * 7 + 10),
                                  rack_id=str(rack_id), position="B2")
        html = self.get_ok(self.m, "/home")
        self.assertIn(f"cage {colony['cage']} · {rack} · B2", html)
        box = html[html.index(f">{rack}<"):]
        self.assertIn("frz-hole is-full is-red", box[:box.index("</article>")])
        self.assertIn(f"<span>{colony['cage']}</span>", box)

    @staticmethod
    def get_rack(name: str) -> int:
        from tests.base import one
        return one("select id from mouse_racks where name=?", name)


class CustomizeHomeTests(AppTestCase):
    """Each person chooses Home's cards, their order and which are wide."""

    def cards(self, html):
        return re.findall(r'data-home-card="([a-z_]+)"', html)

    def save(self, client, order, show, wide=()):
        return self.post(client, "/home/cards", {"order": order, "show": show, "wide": list(wide)})

    def test_a_new_person_gets_the_usual_cards_and_the_optional_ones_off(self):
        fresh = client_for(make_user(uniq("fresh")))
        got = self.cards(self.get_ok(fresh, "/home"))
        self.assertLess(got.index("stats"), got.index("sac"))
        for off in ("todos", "bookings", "notebook", "utilities"):
            self.assertNotIn(off, got)
        self.assertIn('id="home-customize"', self.get_ok(fresh, "/home"))

    def test_hiding_reordering_and_widening_is_kept_for_that_person_only(self):
        who = client_for(make_user(uniq("picky")))
        order = ["utilities", "calendar", "sac", "stats", "databases", "weanings", "geno", "orders"]
        self.save(who, order, show=["utilities", "calendar", "sac", "stats"], wide=["calendar"])
        html = self.get_ok(who, "/home")
        self.assertEqual([k for k in self.cards(html) if k in order], ["utilities", "calendar", "sac", "stats"])
        self.assertIn('class="home-card is-wide" data-home-card="calendar"', html)
        self.assertIn("All utilities", html)                     # the Calculators card is on
        self.assertIn("weanings", self.cards(self.get_ok(self.m, "/home")))
        # Back to the usual.
        self.post(who, "/home/cards", {"reset": "1"})
        self.assertIn("weanings", self.cards(self.get_ok(who, "/home")))

    def test_the_optional_cards_show_their_own_things(self):
        who_name = make_user(uniq("busy"))
        who = client_for(who_name)
        title = uniq("Order primers ")
        who.post("/calendar/items", data='{"kind": "task", "title": "%s", "start": "%sT00:00:00", "isAllday": true}' % (title, days_ago(0)),
                 content_type="application/json")
        order = ["todos", "notebook", "bookings"]
        self.save(who, order, show=order)
        html = self.get_ok(who, "/home")
        self.assertIn(title, html)
        self.assertIn("Recent notebook pages", html)
        self.assertIn("No instrument booked in the next week.", html)


class PerDatabaseHomeTests(AppTestCase):
    """Home is built from the lab's databases: each fly, worm and animal
    database has a card of its own, the Counts card a tile per database,
    and both follow the sidebar's order."""

    @staticmethod
    def cards(html):
        return re.findall(r'data-home-card="([^"]+)"', html)

    @staticmethod
    def tiles(html):
        return re.findall(r'data-tile="([^"]+)"', html)

    def test_each_fly_and_worm_database_has_its_own_card(self):
        flies = self.make_stock_module(self.a, "fly", label=uniq("Fly room "))
        worms = self.make_stock_module(self.a, "worm", label=uniq("Worm bench "))
        html = self.get_ok(client_for(make_user(uniq("bench"))), "/home")
        got = self.cards(html)
        self.assertIn(f"stock:{flies}", got)
        self.assertIn(f"stock:{worms}", got)
        self.assertNotIn("stocks", got)
        worm_card = html.split(f'data-home-card="stock:{worms}"', 1)[1].split('data-home-card="', 1)[0]
        self.assertIn("No chunks, picks or shifts due in the next two days.", worm_card)

    def test_the_cards_follow_the_sidebar_order(self):
        from types import SimpleNamespace
        from app import home_layouts
        flies = SimpleNamespace(kind="fly", settings="", key="flyroom", label="Fly room", icon="fly")
        dbs = [{"key": "stock:flyroom", "kind": "stock", "module": flies},
               {"key": "plasmids", "kind": "plasmids", "module": None},
               {"key": "colony", "kind": "colony", "module": None}]
        keys = [c[0] for c in home_layouts.lab_cards(dbs)]
        self.assertEqual(keys[:4], list(home_layouts.TOP_CARDS))
        self.assertEqual(keys[4:9], ["stock:flyroom", "plasmids", "sac", "weanings", "geno"])
        self.assertLess(keys.index("geno"), keys.index("calendar"))

    def test_an_arrangement_saved_with_the_shared_card_keeps_its_place_and_choice(self):
        import json
        from types import SimpleNamespace
        from app import home_layouts, inventory_service
        from app.db import SessionLocal
        fly = SimpleNamespace(kind="fly", settings="", key="a", label="A", icon="fly")
        worm = SimpleNamespace(kind="worm", settings="", key="b", label="B", icon="worm")
        cards = home_layouts.lab_cards([{"key": "stock:a", "kind": "stock", "module": fly},
                                        {"key": "stock:b", "kind": "stock", "module": worm},
                                        {"key": "colony", "kind": "colony", "module": None}])
        who = uniq("before")
        with SessionLocal() as s:
            inventory_service.set_setting(s, f"home_cards:{who}", json.dumps(
                {"order": ["stocks", "stats", "sac"], "hidden": ["stocks"], "wide": ["stocks"],
                 "seen": home_layouts.CARD_KEYS}))
            s.flush()
            got = home_layouts.get_cards(s, who, cards)
            s.rollback()
        self.assertEqual([k for k in got["order"] if k in ("stock:a", "stock:b", "stats", "sac")],
                         ["stock:a", "stock:b", "stats", "sac"])
        self.assertIn("stock:a", got["hidden"])
        self.assertIn("stock:b", got["wide"])

    def test_counts_show_the_first_five_tiles_until_you_choose(self):
        self.make_stock_module(self.a, "fly")
        who = client_for(make_user(uniq("counter")))
        tiles = self.tiles(self.get_ok(who, "/home"))
        self.assertEqual(len(tiles), 5)
        self.assertEqual(tiles[0], "colony")
        self.post(who, "/home/cards", {"order": ["stats"], "show": ["stats"], "tiles": "1", "tile": ["notebook"]})
        self.assertEqual(self.tiles(self.get_ok(who, "/home")), ["notebook"])
        # Saving the cards without the tiles leaves the choice alone.
        self.post(who, "/home/cards", {"order": ["stats"], "show": ["stats"]})
        self.assertEqual(self.tiles(self.get_ok(who, "/home")), ["notebook"])
        self.post(who, "/home/cards", {"order": ["stats"], "show": ["stats"], "tiles": "1"})
        self.assertNotIn("stats", self.cards(self.get_ok(who, "/home")))

    def test_recent_plasmids_lists_yours_and_marks_one_without_a_sequence(self):
        name = make_user(uniq("cloner"))
        who = client_for(name)
        plasmid = uniq("pHome-")
        self.make_plasmid(who, plasmid)
        html = self.get_ok(who, "/home")
        self.assertIn("plasmids", self.cards(html))
        card = html.split('data-home-card="plasmids"', 1)[1].split('data-home-card="', 1)[0]
        self.assertIn(plasmid, card.split('class="row-line"', 2)[1])  # yours first
        self.assertIn("No sequence", card)

    def test_the_button_opens_the_first_database_in_the_sidebar(self):
        html = self.get_ok(self.m, "/home")
        first = re.search(r'<div class="rail-group-label">Databases</div>.*?href="([^"]+)"', html, re.S).group(1)
        self.assertRegex(html, r'<a class="btn btn-primary" href="%s">' % re.escape(first))
