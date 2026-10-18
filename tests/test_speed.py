"""What keeps a long sheet quick to open, and the calendar quick to fill.

A sheet sends its long lists of choices once (a fish row's Tank, an
inventory row's menus), its row forms inside a <template>
(static/row-forms.js), and the browser keeps the scripts and styles a page
names with ?v=. A page's icons point to symbols at its end. The Breeders
tab leaves out what ended long ago. The calendar's sac reminders are looked
up for the window it shows, in one query.
"""
from __future__ import annotations

import html as htmllib
import re
import unittest
from datetime import date, timedelta

from tests.base import *  # noqa: F401,F403


def templates(html: str) -> str:
    """Everything a page sends inside <template data-row-forms>."""
    return "".join(re.findall(r"<template data-row-forms>(.*?)</template>", html, re.S))


class SacRemindersTests(AppTestCase):
    """A living mouse is due a sac reminder at 30 weeks from its litter's
    birth; the calendar lists those that fall in the dates it shows."""

    def calendar_sacs(self, start: str, end: str) -> dict[str, str]:
        r = self.a.get("/calendar/events.json", query_string={"start": start, "end": end})
        return {i["id"]: i["title"] for i in r.get_json()["items"] if i["id"].endswith("-sac")}

    def mouse_at(self, days_to_30_weeks: int, **fields) -> tuple[int, int]:
        row = self.make_mouse(self.m, self.member, litter=uniq("L"),
                              date_of_birth=days_ago(30 * 7 - days_to_30_weeks), **fields)
        return row, one("select mouse_id from mice where id=?", row)

    def test_only_mice_reaching_30_weeks_in_the_window_are_listed(self):
        inside, inside_no = self.mouse_at(5)
        before, _ = self.mouse_at(-40)
        after, _ = self.mouse_at(40)
        sacs = self.calendar_sacs(days_ago(10), days_ahead(10))
        self.assertEqual(sacs.get(f"auto-mouse-{inside}-sac"), f"Sac reminder · M{inside_no}")
        self.assertNotIn(f"auto-mouse-{before}-sac", sacs)
        self.assertNotIn(f"auto-mouse-{after}-sac", sacs)
        # Each on its day, linked to the mouse.
        r = self.a.get("/calendar/events.json", query_string={"start": days_ago(10), "end": days_ahead(10)})
        item = next(i for i in r.get_json()["items"] if i["id"] == f"auto-mouse-{inside}-sac")
        self.assertTrue(item["start"].startswith(days_ahead(5)))
        self.assertEqual(item["raw"]["href"], f"/colony?view=mice&scope=all&q={inside_no}")

    def test_the_window_ends_are_included(self):
        first, _ = self.mouse_at(-10)
        last, _ = self.mouse_at(10)
        sacs = self.calendar_sacs(days_ago(10), days_ahead(10))
        self.assertIn(f"auto-mouse-{first}-sac", sacs)
        self.assertIn(f"auto-mouse-{last}-sac", sacs)

    def test_a_dead_mouse_has_none(self):
        dead, _ = self.mouse_at(3, status="sac", date_of_death=days_ago(1))
        self.assertNotIn(f"auto-mouse-{dead}-sac", self.calendar_sacs(days_ago(10), days_ahead(10)))

    def test_without_a_window_every_living_mouse_is_listed(self):
        from app.db import SessionLocal
        from app.services import derive_auto_calendar_items
        far, _ = self.mouse_at(150)
        with SessionLocal() as s:
            ids = {i["id"] for i in derive_auto_calendar_items(s)}
        self.assertIn(f"auto-mouse-{far}-sac", ids)


class FishTankChoicesTests(AppTestCase):

    def test_each_fish_row_holds_its_own_tank_and_the_list_is_sent_once(self):
        a, b = uniq("TKA"), uniq("TKB")
        tank_a = self.make_tank(self.a, a)
        tank_b = self.make_tank(self.a, b)
        self.a.post("/zebrafish/fish/create", data={"tank_id_fk": str(tank_a), "count": "4"})
        fish = one("select max(id) from fish where tank_id_fk=?", tank_a)
        html = self.get_ok(self.a, "/zebrafish?view=fish")
        select = re.search(rf'<select[^>]*form="ufish-{fish}" name="tank_id_fk"[^>]*>(.*?)</select>', html, re.S)
        self.assertIsNotNone(select)
        self.assertIn('data-choices="zf-tanks"', select.group(0))
        self.assertEqual(re.findall(r'<option value="(\d+)" selected>([^<]*)</option>', select.group(1)),
                         [(str(tank_a), a)])
        choices = re.search(r'<template id="choices-zf-tanks">(.*?)</template>', html, re.S).group(1)
        self.assertIn(f'<option value="{tank_a}" >{a}</option>', choices)
        self.assertIn(f'<option value="{tank_b}" >{b}</option>', choices)
        self.assertEqual(html.count('id="choices-zf-tanks"'), 1)

    def test_a_fish_tank_change_still_saves(self):
        tank_a, tank_b = self.make_tank(self.a), self.make_tank(self.a)
        self.a.post("/zebrafish/fish/create", data={"tank_id_fk": str(tank_a), "count": "2"})
        fish = one("select max(id) from fish where tank_id_fk=?", tank_a)
        self.assertSaved(self.autosave(self.a, f"/zebrafish/fish/{fish}/update",
                                       {"tank_id_fk": str(tank_b), "count": "2", "sex": "mixed", "status": "alive"}))
        self.assertEqual(one("select tank_id_fk from fish where id=?", fish), tank_b)


class RowFormsTests(AppTestCase):
    """Each sheet's row forms come inside <template data-row-forms>, and
    each row's cells still name their form."""

    def assertFormsWait(self, url: str, form_id: str):
        html = self.get_ok(self.a, url)
        self.assertIn(f'form="{form_id}"', html, url)
        self.assertIn(f'<form id="{form_id}"', templates(html), url)
        self.assertEqual(html.count(f'<form id="{form_id}"'), 1, url)

    def test_colony_sheets(self):
        col = self.make_colony(self.a, self.admin, n_mice=1)
        mouse = col["mice"][0]
        self.assertFormsWait("/colony?view=mice&scope=all", f"mouse-update-{mouse}")
        self.assertFormsWait("/colony?view=cages&scope=all", f"cage-update-{col['cage_id']}")
        self.assertFormsWait("/colony?view=cages&scope=all", f"cage-mouse-update-{mouse}")
        self.assertFormsWait("/colony?view=litters&scope=all", f"litter-update-{col['litter_id']}")

    def test_zebrafish_sheets(self):
        tank = self.make_tank(self.a)
        self.a.post("/zebrafish/fish/create", data={"tank_id_fk": str(tank), "count": "3"})
        fish = one("select max(id) from fish where tank_id_fk=?", tank)
        self.assertFormsWait("/zebrafish?view=tanks", f"utank-{tank}")
        self.assertFormsWait("/zebrafish?view=fish", f"ufish-{fish}")

    def test_inventory_plasmids_stocks_and_organisms(self):
        item = self.make_item(self.a, "reagents")
        self.assertFormsWait("/inventory/reagents", f"inv-{item}")
        plasmid = self.make_plasmid(self.a)
        self.assertFormsWait("/plasmids", f"plasmid-{plasmid}")
        key = self.make_stock_module(self.a)
        vial = self.make_vial(self.a, key)
        self.assertFormsWait(f"/stocks/{key}", f"unit-{vial}")
        org = self.make_organism_module(self.a, capabilities=["housing"])
        animal = self.make_animal(self.a, org)
        self.assertFormsWait(f"/organisms/{org}", f"row-a-{animal}")

    def test_the_cage_sheet_keeps_every_row_on_the_page(self):
        # Its cards, Open all and a scanned cage look for its rows there,
        # so a long one does not take unseen rows off it (data-table.js).
        html = self.get_ok(self.a, "/colony?view=cages&scope=all")
        self.assertRegex(html, r'data-table-id="cages-v3" data-dt-keep-rows')
        self.assertNotIn("data-dt-keep-rows", self.get_ok(self.a, "/colony?view=mice&scope=all"))

    def test_the_script_that_brings_them_is_on_every_page(self):
        self.assertIn("/static/row-forms.js?v=", self.get_ok(self.a, "/home"))


class LittersTabTests(AppTestCase):

    def test_each_pup_links_to_the_mouse_sheet(self):
        col = self.make_colony(self.a, self.admin, n_mice=2)
        html = self.get_ok(self.a, "/colony?view=litters&scope=all")
        for mouse in col["mice"]:
            number = one("select mouse_id from mice where id=?", mouse)
            self.assertIn(f'href="/colony?view=mice&amp;scope=all&amp;q={number}"', html)
        row = re.search(rf'<tr data-id="{col["litter_id"]}".*?</tr>', html, re.S).group(0)
        self.assertIn('title="2 of 2 alive"', row)


class BreedersTabSpeedTests(AppTestCase):
    """The Breeders tab leaves out what the cage sheet does: a Breeder cage
    long empty, and a mouse that ended long ago, behind "Show them"."""

    def test_a_long_empty_breeder_cage_waits_behind_show_them(self):
        old, new = uniq("C"), uniq("C")
        old_row = self.make_cage(self.a, old, purpose="Breeder")
        execute("update mouse_cages set created_at=? where id=?", days_ago(400) + " 00:00:00", old_row)
        self.make_cage(self.a, new, purpose="Breeder")
        html = self.get_ok(self.a, "/colony?view=breeders&scope=all")
        self.assertNotIn(f">{old}</a></strong>", html)
        self.assertIn(f">{new}</a></strong>", html)          # made today: still listed
        self.assertIn("/colony?view=breeders&amp;scope=all&amp;ended=all", html)
        self.assertIn(f">{old}</a></strong>", self.get_ok(self.a, "/colony?view=breeders&scope=all&ended=all"))

    def test_a_mouse_that_ended_long_ago_is_left_off_its_card(self):
        cage = uniq("C")
        self.make_cage(self.a, cage, purpose="Breeder")
        living = self.make_mouse(self.a, self.admin, cage=cage, date_of_birth=days_ago(90))
        gone = self.make_mouse(self.a, self.admin, cage=cage, date_of_birth=days_ago(400),
                               status="sac", date_of_death=days_ago(200))
        numbers = {m: one("select mouse_id from mice where id=?", m) for m in (living, gone)}
        card = lambda html: re.search(rf">{cage}</a></strong>.*?</article>", html, re.S).group(0)
        html = self.get_ok(self.a, "/colony?view=breeders&scope=all")
        self.assertIn(f"<td>{numbers[living]}</td>", card(html))
        self.assertNotIn(f"<td>{numbers[gone]}</td>", card(html))
        self.assertIn("mice that ended over 90 days ago", html)
        self.assertIn(f"<td>{numbers[gone]}</td>", card(self.get_ok(self.a, "/colony?view=breeders&scope=all&ended=all")))

    def test_pick_buttons_post_through_one_form(self):
        cage = uniq("C")
        self.make_cage(self.a, cage, purpose="Breeder")
        mouse = self.make_mouse(self.a, self.admin, cage=cage, date_of_birth=days_ago(60))
        html = self.get_ok(self.m, "/colony?view=breeders&scope=all")
        self.assertIn(f'data-pick-action="/colony/mice/{mouse}/pick"', html)
        self.assertEqual(html.count('<form id="breeder-pick"'), 1)
        self.assertNotIn('form="breeder-pick"', html)


class InventorySheetSpeedTests(AppTestCase):
    """An inventory row holds its own choice in each menu, the lists come
    once below the table; its icons point within the page; the colonies'
    IDs come as data, made a list when an ID box is used."""

    def test_each_menu_holds_its_own_choice_and_the_lists_are_sent_once(self):
        item = self.make_item(self.a, "reagents", category="buffer", status="in stock")
        html = self.get_ok(self.a, "/inventory/reagents")
        row = re.search(rf'<tr data-id="{item}".*?</tr>', html, re.S).group(0)
        category = re.search(r'<select name="category"[^>]*>(.*?)</select>', row, re.S)
        self.assertIn('data-choices="inv-category"', category.group(0))
        self.assertEqual(re.findall(r"<option ", category.group(1)), ["<option "])
        self.assertIn('value="buffer" selected', category.group(1))
        status = re.search(r'<select class="status-pill" name="status"[^>]*>(.*?)</select>', row, re.S)
        self.assertIn('data-choices="inv-status"', status.group(0))
        self.assertEqual(html.count('<template id="choices-inv-category">'), 1)
        self.assertIn('value="buffer"', re.search(r'<template id="choices-inv-category">(.*?)</template>', html, re.S).group(1))
        self.assertEqual(html.count('<template id="choices-inv-status">'), 1)
        # Between a row's tags, nothing (app.py, tight).
        self.assertNotRegex(row, r">\s+<")

    def test_a_changed_choice_still_saves(self):
        item = self.make_item(self.a, "reagents", category="buffer")
        self.assertSaved(self.autosave(self.a, f"/inventory/reagents/items/{item}/update",
                                       {"name": one("select name from inventory_items where id=?", item),
                                        "category": "enzyme", "status": "in stock", "is_shared": "0"}))
        self.assertEqual(one("select category from inventory_items where id=?", item), "enzyme")

    def test_icons_point_to_the_pages_own_symbols(self):
        self.make_item(self.a, "reagents")
        html = self.get_ok(self.a, "/inventory/reagents")
        used = set(re.findall(r'<use href="#i-([\w-]+)">', html))
        self.assertIn("trash", used)
        self.assertNotIn('<use href="/static/icons.svg#', html)
        sprite = re.search(r'<svg aria-hidden="true" style="display:none">(.*?)</svg>\s*</body>', html, re.S).group(1)
        self.assertEqual(set(re.findall(r'<symbol id="i-([\w-]+)"', sprite)), used)

    def test_an_icon_drawn_outside_a_page_points_to_the_shared_file(self):
        from flask import render_template_string
        from app.app import app
        with app.test_request_context("/"):
            self.assertIn('<use href="/static/icons.svg#trash">', render_template_string("{{ icon('trash') }}"))

    def test_the_colonies_ids_come_when_asked(self):
        self.make_item(self.a, "samples")
        html = self.get_ok(self.a, "/inventory/samples")
        self.assertNotIn('<datalist id="inv-src-', html)
        self.assertNotIn('id="inv-src-data"', html)
        self.assertIn('data-list="inv-src-0"', html)
        self.assertIn('"sourcesUrl": "/inventory/samples/sources"', html)
        refs = self.a.get("/inventory/samples/sources").get_json()["refs"]
        self.assertEqual(refs[0], [str(m) for m in sorted(self.mouse_ids())])   # the mice, by number
        self.assertEqual(refs[-1], [])   # Other
        # A sheet with no source column has none to give.
        self.assertEqual(self.a.get("/inventory/reagents/sources").status_code, 404)

    def mouse_ids(self):
        from sqlalchemy import select
        from app.db import SessionLocal
        from app.models import MouseRecord
        with SessionLocal() as session:
            return list(session.scalars(select(MouseRecord.mouse_id)))


class InventoryPagingTests(AppTestCase):
    """A sheet of more than LAZY_OVER items draws its first FIRST_PAGE rows;
    the others come as entries for the sheet's script, and their rows from
    /<key>/rows, drawn as the page draws them."""

    def setUp(self):
        from unittest import mock
        from app import inventory_routes
        from tests.test_inventory import InventoryCase
        self.key = InventoryCase.new_module(self.a, "reagents")
        self.items = [self.make_item(self.a, self.key, name=uniq("pg"), vendor=uniq("V")) for _ in range(5)]
        self.patches = [mock.patch.object(inventory_routes, "LAZY_OVER", 3),
                        mock.patch.object(inventory_routes, "FIRST_PAGE", 2)]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()

    @staticmethod
    def drawn(html: str) -> list[str]:
        return re.findall(r'<tr data-id="(\d+)"', html)

    @staticmethod
    def stubs(html: str) -> list[dict]:
        import json
        found = re.search(r'<script type="application/json" data-dt-stubs>(.*?)</script>', html, re.S)
        return json.loads(found.group(1)) if found else []

    def test_the_page_draws_its_first_rows_and_lists_the_others(self):
        html = self.get_ok(self.a, f"/inventory/{self.key}")
        newest = [str(i) for i in reversed(self.items)]
        self.assertEqual(self.drawn(html), newest[:2])
        self.assertIn(f'data-dt-rows-url="/inventory/{self.key}/rows?rem=', html)
        stubs = self.stubs(html)
        self.assertEqual([str(s["id"]) for s in stubs], newest[2:])
        oldest = next(s for s in stubs if s["id"] == self.items[0])
        self.assertEqual(oldest["e"], f"item-{self.items[0]}")
        self.assertEqual(oldest["d"]["mine"], "true")
        self.assertEqual(oldest["d"]["number"], str(one("select number from inventory_items where id=?", self.items[0])))
        vendor = one("select vendor from inventory_items where id=?", self.items[0])
        self.assertEqual(oldest["k"]["vendor"], vendor)
        self.assertIn(vendor.lower(), oldest["s"])
        # The count, the chips and the forms: every row, or the drawn ones.
        self.assertIn("5 reagents", html)
        self.assertEqual(html.count('<form id="inv-'), 2)

    def test_rows_come_as_the_page_would_draw_them(self):
        from unittest import mock
        from app import inventory_routes
        with mock.patch.object(inventory_routes, "LAZY_OVER", 1000):
            whole = self.get_ok(self.a, f"/inventory/{self.key}")
        asked = [self.items[0], self.items[2]]
        # As the page asks: its rows URL names the columns that offer earlier values.
        lazy = self.get_ok(self.a, f"/inventory/{self.key}")
        url = htmllib.unescape(re.search(r'data-dt-rows-url="([^"]+)"', lazy).group(1))
        r = self.a.get(f"{url}&ids={asked[0]},{asked[1]},999999")
        answer = r.get_json()
        self.assertEqual(answer["ids"], asked)
        same = lambda text: re.sub(r"\s+", " ", text).replace("/static/icons.svg#", "#i-")
        for item in asked:
            row = re.search(rf'<tr data-id="{item}".*?</tr>', whole, re.S).group(0)
            self.assertEqual(same(re.search(rf'<tr data-id="{item}".*?</tr>', answer["rows"], re.S).group(0)), same(row))
            self.assertIn(f'<form id="inv-{item}"', answer["forms"])
        self.assertNotIn("dt-empty", answer["rows"])

    def test_rows_of_another_inventory_are_not_given(self):
        other = self.make_item(self.a, "reagents")
        answer = self.a.get(f"/inventory/{self.key}/rows?ids={other},{self.items[0]}").get_json()
        self.assertEqual(answer["ids"], [self.items[0]])

    def test_a_short_sheet_draws_every_row(self):
        from unittest import mock
        from app import inventory_routes
        with mock.patch.object(inventory_routes, "LAZY_OVER", 5):
            html = self.get_ok(self.a, f"/inventory/{self.key}")
        self.assertEqual(len(self.drawn(html)), 5)
        self.assertNotIn("data-dt-rows-url", html)
        self.assertNotIn("data-dt-stubs", html)

    def test_rows_need_someone_signed_in(self):
        from app.app import app
        r = app.test_client().get(f"/inventory/{self.key}/rows?ids={self.items[0]}")
        self.assertEqual(r.status_code, 302)


class InventoryGridLaterTests(AppTestCase):
    """An inventory's box grid is not in its page: its boxes and tiles come
    from /<key>/grid when the grid is first shown (rack-grid.js)."""

    def test_the_grid_comes_from_its_own_address(self):
        from tests.test_inventory import InventoryCase
        key = InventoryCase.new_module(self.a, "samples")
        self.a.post(f"/inventory/{key}/racks/save", data={"id": "", "name": "Box G", "rows": "9", "cols": "9"})
        rack = one("select max(id) from inventory_racks")
        item = self.make_item(self.a, key, name=uniq("tube"), rack_id=str(rack), position="A1")
        page = self.get_ok(self.a, f"/inventory/{key}?type=tissue")
        self.assertNotIn("data-rack-data>", page)
        src = htmllib.unescape(re.search(r'data-rack-src="([^"]+)"', page).group(1))
        self.assertIn("type=tissue", src)
        grid = self.a.get(f"/inventory/{key}/grid").get_json()
        self.assertIn(rack, [r["id"] for r in grid["racks"]])
        tile = next(i for i in grid["items"] if i["id"] == item)
        self.assertEqual((tile["rack"], tile["row"], tile["col"]), (rack, 1, 1))
        self.assertIn("data-record-payload", tile["edit"])
        # The box's own dialog, above the sheet, still opens with what it needs.
        self.assertIn('data-record-edit="inv-rack-dialog"', page)
        self.assertEqual(self.a.get("/inventory/orders/grid").status_code, 404)


class TextCellsTests(AppTestCase):
    """In Chromium an inventory's cells come as text, each with its field's
    own markup to become when used (app.py as_text_cells, static/text-cells.js);
    other browsers get the fields, and ?cells= chooses either."""

    CHROME = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"}
    SAFARI = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                            "(KHTML, like Gecko) Version/18.0 Safari/605.1.15"}

    def setUp(self):
        from tests.test_inventory import InventoryCase
        self.key = InventoryCase.new_module(self.a, "reagents")
        self.vendor = uniq("Vendor")
        self.item = self.make_item(self.a, self.key, name=uniq("tc"), vendor=self.vendor, status="in stock",
                                   received_on="2026-03-04")

    def page(self, headers, query=""):
        r = self.a.get(f"/inventory/{self.key}{query}", headers=headers)
        self.assertEqual(r.status_code, 200)
        return r.get_data(as_text=True)

    @staticmethod
    def cell(html: str, name: str):
        found = re.search(r'<span class="cell-text[^"]*"[^>]*data-sort-for="%s"[^>]*>[^<]*</span>' % name, html)
        return found.group(0) if found else None

    def test_chromium_gets_text_other_browsers_fields(self):
        chrome = self.page(self.CHROME)
        self.assertIsNotNone(self.cell(chrome, "vendor"))
        self.assertNotIn(f'name="vendor" value="{self.vendor}"', chrome)
        for headers in (self.SAFARI, {}):
            fields = self.page(headers)
            self.assertIsNone(self.cell(fields, "vendor"))
            self.assertRegex(fields, rf'<input[^>]*name="vendor"[^>]*value="{self.vendor}"')

    def test_the_address_chooses_either(self):
        self.assertIsNotNone(self.cell(self.page(self.SAFARI, "?cells=text"), "vendor"))
        self.assertIsNone(self.cell(self.page(self.CHROME, "?cells=fields"), "vendor"))

    def test_a_text_cell_is_its_field_until_used(self):
        fields = self.page(self.SAFARI)
        text = self.page(self.CHROME)
        span = self.cell(text, "vendor")
        self.assertIn(f">{self.vendor}</span>", span)
        self.assertIn(f'data-sort-value="{self.vendor}"', span)
        self.assertIn('tabindex="0"', span)
        field = htmllib.unescape(re.search(r'data-field="([^"]*)"', span).group(1))
        self.assertIn(field, fields)
        # A menu shows its chosen label, coloured as its pill; a date its value.
        status = self.cell(text, "status")
        self.assertIn('data-status="in stock"', status)
        self.assertRegex(status, r">\s*[Ii]n stock</span>")
        received = self.cell(text, "received_on")
        self.assertIn("is-date", received)
        self.assertIn('data-sort-value="2026-03-04"', received)
        # The row's checkbox stays as it is.
        self.assertRegex(text, rf'<input[^>]*type="checkbox"[^>]*value="{self.item}"|<input[^>]*value="{self.item}"[^>]*type="checkbox"')

    def test_a_save_of_one_cell_leaves_the_others(self):
        r = self.a.post(f"/inventory/{self.key}/items/{self.item}/update", data={"notes": "opened one cell"},
                        headers={"X-Autosave": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(one("select notes from inventory_items where id=?", self.item), "opened one cell")
        self.assertEqual(one("select vendor from inventory_items where id=?", self.item), self.vendor)
        self.assertEqual(one("select status from inventory_items where id=?", self.item), "in stock")

    def test_rows_fetched_later_come_as_the_page_drew_them(self):
        from unittest import mock
        from app import inventory_routes
        for _ in range(3):
            self.make_item(self.a, self.key, name=uniq("tc"))
        with mock.patch.object(inventory_routes, "LAZY_OVER", 2), mock.patch.object(inventory_routes, "FIRST_PAGE", 1):
            for headers, query, text in ((self.CHROME, "", True), (self.SAFARI, "", False),
                                         (self.CHROME, "?cells=fields", False)):
                html = self.page(headers, query)
                url = htmllib.unescape(re.search(r'data-dt-rows-url="([^"]+)"', html).group(1))
                self.assertEqual("cells=fields" in url, query == "?cells=fields")
                rows = self.a.get(f"{url}&ids={self.item}", headers=headers).get_json()["rows"]
                self.assertEqual(self.cell(rows, "vendor") is not None, text, (headers, query))


class MouseSheetPagingTests(AppTestCase):
    """A mouse sheet of more than LAZY_OVER mice draws its first FIRST_PAGE
    rows; the others come as entries for the sheet's script, and their rows
    from /colony/mice/rows, drawn as the page draws them."""

    def setUp(self):
        from unittest import mock
        from app import inventory_routes
        self.cage = uniq("PG")
        self.mice = [self.make_mouse(self.a, "alex", cage=self.cage, note=uniq("n")) for _ in range(5)]
        self.patches = [mock.patch.object(inventory_routes, "LAZY_OVER", 3),
                        mock.patch.object(inventory_routes, "FIRST_PAGE", 2)]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()

    @staticmethod
    def stubs(html: str) -> list[dict]:
        import json
        found = re.search(r'<script type="application/json" data-dt-stubs>(.*?)</script>', html, re.S)
        return json.loads(found.group(1)) if found else []

    def page(self, url="/colony?view=mice&scope=all&ended=all"):
        return self.get_ok(self.a, url)

    def test_the_page_draws_its_first_rows_and_lists_the_others(self):
        html = self.page()
        drawn = re.findall(r'<tr data-id="(\d+)" data-row-form="mouse-update-\d+"', html)
        stubs = self.stubs(html)
        self.assertEqual(len(drawn), 2)
        self.assertIn('data-dt-rows-url="/colony/mice/rows"', html)
        self.assertEqual(len(drawn) + len(stubs), one("select count(*) from mice"))
        mine = next(s for s in stubs if s["id"] == self.mice[-1])
        self.assertEqual(mine["d"]["rowForm"], f"mouse-update-{self.mice[-1]}")
        self.assertEqual(mine["d"]["active"], "true")
        self.assertEqual(mine["k"]["cage_id"], self.cage)
        note = one("select note from mice where id=?", self.mice[-1])
        self.assertEqual(mine["k"]["note"], note)
        self.assertIn(note.lower(), mine["s"])
        self.assertIn(self.cage.lower(), mine["s"])
        # Its row form comes with its row, not in the page.
        self.assertNotIn(f'id="mouse-update-{self.mice[-1]}"', html)

    def test_rows_come_as_the_page_would_draw_them(self):
        from unittest import mock
        from app import inventory_routes
        with mock.patch.object(inventory_routes, "LAZY_OVER", 100000):
            whole = self.page()
        answer = self.a.get(f"/colony/mice/rows?ids={self.mice[-1]},{self.mice[0]},999999999").get_json()
        self.assertEqual(answer["ids"], [self.mice[-1], self.mice[0]])
        # A fragment's icons come from the sprite file, the page's from its inline sprite.
        same = lambda text: re.sub(r"\s+", " ", text).replace("/static/icons.svg#", "#i-").strip()
        for mid in (self.mice[-1], self.mice[0]):
            pattern = r'<tr data-id="%d".*?</tr>' % mid
            self.assertEqual(same(re.search(pattern, answer["rows"], re.S).group(0)),
                             same(re.search(pattern, whole, re.S).group(0)))
            self.assertIn(f'id="mouse-update-{mid}"', answer["forms"])
        words = self.a.get(f"/colony/mice/rows?ids={self.mice[0]}&as=text").get_json()["texts"][str(self.mice[0])]
        self.assertIn(self.cage, words)

    def test_a_short_sheet_is_drawn_whole(self):
        from unittest import mock
        from app import inventory_routes
        with mock.patch.object(inventory_routes, "LAZY_OVER", 100000):
            html = self.page()
        self.assertNotIn("data-dt-rows-url", html)
        self.assertNotIn("data-dt-stubs", html)


class SheetPagingTests(AppTestCase):
    """Tanks, litters and vials are drawn by their page and paged by
    app/sheet_paging.py: past LAZY_OVER rows the page keeps its first page,
    the rest standing as stubs read from the rows as drawn, and the sheet's
    /rows address answers with rows drawn as the page draws them."""

    def setUp(self):
        from unittest import mock
        from app import inventory_routes
        self.patches = [mock.patch.object(inventory_routes, "LAZY_OVER", 2),
                        mock.patch.object(inventory_routes, "FIRST_PAGE", 1)]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()

    @staticmethod
    def stubs(html: str) -> list[dict]:
        import json
        found = re.search(r'<script type="application/json" data-dt-stubs>(.*?)</script>', html, re.S)
        return json.loads(found.group(1)) if found else []

    def check_sheet(self, url, ids, rows_path, row_pattern, form_of):
        from unittest import mock
        from app import inventory_routes
        with mock.patch.object(inventory_routes, "LAZY_OVER", 100000):
            whole = self.get_ok(self.a, url)
        html = self.get_ok(self.a, url)
        self.assertIn(f'data-dt-rows-url="{rows_path}"', html)
        stubs = self.stubs(html)
        listed = re.findall(r'<tr\b[^>]*\bdata-id="(\d+)"', whole[whole.index("<tbody"):])
        drawn = re.findall(r'<tr\b[^>]*\bdata-id="(\d+)"', html[html.index("<tbody"):html.index("</tbody>")])
        self.assertEqual(len(drawn), 1)
        self.assertEqual(sorted(drawn + [str(s["id"]) for s in stubs]), sorted(set(listed)))
        stub = next(s for s in stubs if s["id"] == ids[-1]) if any(s["id"] == ids[-1] for s in stubs) else stubs[0]
        self.assertEqual(stub["f"], form_of(stub["id"]))
        # A stub's form is not on the page; its row brings it.
        self.assertNotIn(f'id="{stub["f"]}"', html)
        sep = "&" if "?" in rows_path else "?"
        query = url.split("?", 1)[1] if "?" in url else ""
        answer = self.a.get(f"{rows_path}{sep}{query}&ids={stub['id']},999999").get_json()
        self.assertEqual(answer["ids"], [stub["id"]])
        self.assertIn(f'id="{stub["f"]}"', answer["forms"])
        same = lambda text: re.sub(r"\s+", " ", text).replace("/static/icons.svg#", "#i-").strip()
        pattern = row_pattern % stub["id"]
        self.assertEqual(same(re.search(pattern, answer["rows"], re.S).group(0)),
                         same(re.search(pattern, whole, re.S).group(0)))
        words = self.a.get(f"{rows_path}{sep}{query}&ids={stub['id']}&as=text").get_json()["texts"][str(stub["id"])]
        self.assertTrue(any(words))

    def test_tanks(self):
        ids = [self.make_tank(self.a, notes=uniq("tank note")) for _ in range(4)]
        self.check_sheet("/zebrafish?view=tanks", ids, "/zebrafish/tanks/rows",
                         r'<tr id="tank-%d".*?</tr>', lambda i: f"utank-{i}")

    def test_a_tank_not_drawn_reads_as_its_row_would(self):
        """app.zf_tank_stubs builds an undrawn tank from its data; it must
        be what data-table.js would read from its row (cell_text.row_stub),
        in either way of drawing cells, for its owner and someone else."""
        from unittest import mock
        from app import custom_fields, inventory_routes
        from app.cell_text import row_stub
        from app.db import SessionLocal
        with SessionLocal() as session:
            custom_fields.set_fields(session, "zebrafish", [
                {"key": "diet", "label": "Diet", "type": "select", "options": ["rotifers", "flakes"]},
                {"key": "room", "label": "Room"}])
            session.commit()
        rack = self.make_rack_for_tanks()
        line = self.make_line(self.a)
        ids = [self.make_tank(self.a, notes='said "hi" & left', card_id="C-7", rack_id_fk=str(rack), position="A2",
                              line_id_fk=str(line), purpose="breeding", attr_diet="flakes", attr_room="B 12"),
               self.make_tank(self.a, purpose="mating"),
               self.make_tank(self.m),   # someone else's: read only to alex
               self.make_tank(self.a, purpose="quarantine"),
               self.make_tank(self.a, rack_id_fk=str(rack), position="A3", line_id_fk=str(line))]
        execute("update tanks set row=NULL, col=NULL where id=?", ids[4])   # in a rack, at no place: Pos shows "–"
        execute("update tanks set needs_genotyping=?, mating_return_at=? where id=?", True, days_ahead(3), ids[1])
        execute("update tanks set purpose='TANK OF OLD' where id=?", ids[3])   # from before the list
        execute("update tanks set active=? where id=?", False, ids[2])
        from tests.test_zebrafish import make_fish
        make_fish(self.a, ids[0], 4, sex="M")
        make_fish(self.a, ids[0], 2, sex="F", status="sac")
        url = "/zebrafish?view=tanks"

        def check():
            for client in (self.a, self.m):
                for ua in ({"User-Agent": "Mozilla/5.0 Chrome/141.0 Safari/537.36"}, {"User-Agent": "Firefox"},
                           {"User-Agent": "Firefox", "Accept-Language": "zh-CN"}):
                    with mock.patch.object(inventory_routes, "LAZY_OVER", 100000):
                        whole = client.get(url, headers=ua).get_data(as_text=True)
                    stubs = {s["id"]: s for s in self.stubs(client.get(url, headers=ua).get_data(as_text=True))}
                    read = {}
                    for attrs, body in re.findall(r'<tr (id="tank-\d+"[^>]*)>(.*?)</tr>', whole, re.S):
                        drawn = row_stub(attrs, body)
                        read[drawn["id"]] = drawn
                    self.assertTrue(set(ids) & set(stubs), ua)
                    for tid in ids:
                        if tid not in stubs:
                            continue
                        want, got = read[tid], stubs[tid]
                        for key in ("d", "k", "f", "e"):
                            self.assertEqual(got[key], want[key], (tid, key, ua))
                        self.assertEqual(set(got["s"].split()), set(want["s"].split()), (tid, ua))

        check()
        # And with none of the lab's own columns (whose cells show words of their own).
        with SessionLocal() as session:
            custom_fields.set_fields(session, "zebrafish", [])
            session.commit()
        check()

    def make_rack_for_tanks(self):
        from tests.test_zebrafish import make_rack
        return make_rack(self.a)

    def test_litters(self):
        ids = [self.make_litter(self.a, date_of_birth=days_ago(10)) for _ in range(4)]
        self.check_sheet("/colony?view=litters", ids, "/colony/litters/rows",
                         r'<tr data-id="%d".*?</tr>', lambda i: f"litter-update-{i}")

    def test_a_litter_not_drawn_reads_as_its_row_would(self):
        """app.litter_stub builds an undrawn litter from its data; it must
        be what data-table.js would read from the row (cell_text.row_stub
        reads it as the browser does), in either way of drawing cells."""
        from unittest import mock
        from app import inventory_routes
        from app.cell_text import _ROW_FULL, row_stub
        code = uniq("L")
        self.make_mouse(self.a, self.admin, litter=code, date_of_birth=days_ago(20), gender="M")
        self.make_mouse(self.a, self.admin, litter=code, date_of_birth=days_ago(20), gender="Unknown",
                        status="sacrificed")
        self.make_litter(self.a, date_of_birth=days_ago(5), cohort_name="Cohort & co", father_info="M-7",
                         mother_info="F-9", total_pups="6", notes='said "hello"')
        theirs = uniq("L")
        self.make_mouse(self.m, self.member, litter=theirs, date_of_birth=days_ago(15))
        url = "/colony?view=litters&scope=all"
        for headers in ({"User-Agent": "Mozilla/5.0 Chrome/141.0 Safari/537.36"}, {"User-Agent": "Firefox"},
                        {"User-Agent": "Firefox", "Accept-Language": "zh-CN"}):
            with mock.patch.object(inventory_routes, "LAZY_OVER", 100000):
                whole = self.a.get(url, headers=headers).get_data(as_text=True)
            stubs = {s["id"]: s for s in self.stubs(self.a.get(url, headers=headers).get_data(as_text=True))}
            if "Accept-Language" in headers:
                self.assertIn('<html lang="zh', whole)   # a pup's sex in Chinese, its words too
            body = whole[whole.index("<tbody"):whole.index("</tbody>")]
            read = {}
            for m in _ROW_FULL.finditer(body):
                drawn = row_stub(m.group(1), m.group(2))
                read[drawn["id"]] = drawn
            mine = [one("select id from litters where litter_id=?", c) for c in (code, theirs)]
            for lid in [*mine, *list(stubs)[:20]]:
                if lid not in stubs:
                    continue
                want, got = read[lid], stubs[lid]
                self.assertEqual(got["d"], want["d"], headers)
                self.assertEqual(got["k"], want["k"], headers)
                self.assertEqual(got["f"], want["f"])
                self.assertEqual(set(got["s"].split()), set(want["s"].split()), (lid, headers))

    def test_a_litter_of_ended_mice_comes_when_the_page_shows_them(self):
        old = self.make_litter(self.a, date_of_birth=days_ago(800))
        for _ in range(3):
            self.make_litter(self.a, date_of_birth=days_ago(800))
        html = self.get_ok(self.a, "/colony?view=litters&scope=all&ended=all")
        self.assertIn('data-dt-rows-url="/colony/litters/rows?scope=all&amp;ended=all"', html)
        answer = self.a.get(f"/colony/litters/rows?scope=all&ended=all&ids={old}").get_json()
        self.assertEqual(answer["ids"], [old])

    def test_cages(self):
        cols = [self.make_colony(self.a, self.admin, n_mice=1) for _ in range(3)]
        ids = [c["cage_id"] for c in cols]
        url = "/colony?view=cages&scope=all"
        html = self.get_ok(self.a, url)
        self.assertIn('data-dt-rows-url="/colony/cages/rows?scope=all"', html)
        stubs = self.stubs(html)
        stub = next(s for s in stubs if s["id"] == ids[-1])
        self.assertEqual((stub["e"], stub["f"]), (f"cage-{ids[-1]}", f"cage-update-{ids[-1]}"))
        mouse = cols[-1]["mice"][0]
        self.assertNotIn(f'id="cage-update-{ids[-1]}"', html)
        self.assertNotIn(f'id="cage-mouse-update-{mouse}"', html)
        answer = self.a.get(f"/colony/cages/rows?scope=all&ids={ids[-1]},999999").get_json()
        self.assertEqual(answer["ids"], [ids[-1]])
        # The cage's row, its detail row (its mice), and both kinds of form.
        self.assertIn(f'<tr id="cage-{ids[-1]}"', answer["rows"])
        self.assertIn(f'id="cage-detail-{ids[-1]}" data-detail-for="{ids[-1]}"', answer["rows"])
        self.assertIn(f'id="cage-update-{ids[-1]}"', answer["forms"])
        self.assertIn(f'id="cage-mouse-update-{mouse}"', answer["forms"])
        from unittest import mock
        from app import inventory_routes
        with mock.patch.object(inventory_routes, "LAZY_OVER", 100000):
            whole = self.get_ok(self.a, url)
        same = lambda text: re.sub(r"\s+", " ", text).replace("/static/icons.svg#", "#i-").strip()
        for pattern in (r'<tr id="cage-%d".*?</tr>' % ids[-1], r'<tr class="cage-detail" id="cage-detail-%d".*?</article>' % ids[-1]):
            self.assertEqual(same(re.search(pattern, answer["rows"], re.S).group(0)),
                             same(re.search(pattern, whole, re.S).group(0)))
        words = self.a.get(f"/colony/cages/rows?scope=all&ids={ids[-1]}&as=text").get_json()["texts"][str(ids[-1])]
        self.assertIn(cols[-1]["cage"], words)

    def test_a_cage_not_drawn_reads_as_its_row_would(self):
        """app.cage_stub builds an undrawn cage from its data; it must be
        what data-table.js would read from its row (cell_text.row_stub),
        for its owner and for someone who may not edit it."""
        from unittest import mock
        from app import inventory_routes
        from app.cell_text import row_stub
        self.make_colony(self.a, self.admin, n_mice=2, purpose="Breeding", card_id="F-1", notes="a note")
        self.make_colony(self.m, self.member, n_mice=1, purpose="Experiment")
        self.make_cage(self.a, uniq("C"))   # empty
        shouting = self.make_cage(self.a, uniq("C"))
        execute("update mouse_cages set purpose='BREEDER' where id=?", shouting)   # as an import may write it
        url = "/colony?view=cages&scope=all"
        with mock.patch.object(inventory_routes, "LAZY_OVER", 100000):
            whole = self.get_ok(self.a, url)
        menu = re.search(r'<select class="status-pill" name="purpose" form="cage-update-%d".*?</select>' % shouting, whole, re.S).group(0)
        self.assertRegex(menu, r'<option value="Breeder" selected>')   # its own word, whatever its case
        for client in (self.a, self.m):
            for ua in ("Mozilla/5.0 Chrome/141.0 Safari/537.36", "Firefox"):
                with mock.patch.object(inventory_routes, "LAZY_OVER", 100000):
                    whole = client.get(url, headers={"User-Agent": ua}).get_data(as_text=True)
                stubs = {s["id"]: s for s in self.stubs(client.get(url, headers={"User-Agent": ua}).get_data(as_text=True))}
                read = {}
                for attrs, body in re.findall(r'<tr (id="cage-\d+"[^>]*)>(.*?)</tr>', whole, re.S):
                    drawn = row_stub(attrs, body)
                    read[drawn["id"]] = drawn
                checked = 0
                for cid, want in read.items():
                    if cid not in stubs:
                        continue
                    got = stubs[cid]
                    self.assertEqual((got["d"], got["k"], got["e"], got["f"]), (want["d"], want["k"], want["e"], want["f"]), ua)
                    self.assertEqual(set(got["s"].split()), set(want["s"].split()), (cid, ua))
                    checked += 1
                self.assertEqual(sorted([*stubs, *(set(read) - set(stubs))]), sorted(read))
                self.assertGreaterEqual(checked, 2)

    def test_a_cage_of_ended_mice_comes_when_the_page_shows_them(self):
        cages = [self.make_cage(self.a, uniq("C")) for _ in range(4)]
        html = self.get_ok(self.a, "/colony?view=cages&scope=all&ended=all")
        self.assertIn('data-dt-rows-url="/colony/cages/rows?scope=all&amp;ended=all"', html)
        answer = self.a.get(f"/colony/cages/rows?scope=all&ended=all&ids={cages[0]}").get_json()
        self.assertEqual(answer["ids"], [cages[0]])

    def test_vials(self):
        key = self.make_stock_module(self.a, "fly")
        ids = [self.make_vial(self.a, key) for _ in range(4)]
        self.check_sheet(f"/stocks/{key}", ids, f"/stocks/{key}/units/rows",
                         r'<tr\b[^>]*\bdata-id="%d".*?</tr>', lambda i: f"unit-{i}")


class VersionedAssetsTests(AppTestCase):

    def test_a_file_named_with_its_version_is_kept_by_the_browser(self):
        r = self.a.get("/static/shell.js?v=123")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["Cache-Control"], "public, max-age=31536000, immutable")
        # The session cookie is renewed on every reply: varying by it, the
        # file would be asked for again on the next page.
        self.assertNotIn("Cookie", r.headers.get("Vary", ""))
        r.close()

    def test_without_a_version_it_is_asked_for_again(self):
        r = self.a.get("/static/shell.js")
        self.assertNotIn("immutable", r.headers.get("Cache-Control", ""))
        r.close()


if __name__ == "__main__":
    unittest.main()
