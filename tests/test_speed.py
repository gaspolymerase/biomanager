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

    def test_the_colonies_ids_come_as_data(self):
        self.make_item(self.a, "samples")
        html = self.get_ok(self.a, "/inventory/samples")
        self.assertNotIn('<datalist id="inv-src-', html)
        self.assertIn('<script type="application/json" id="inv-src-data">', html)
        self.assertIn('data-list="inv-src-0"', html)


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
