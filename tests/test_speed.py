"""What keeps a long sheet quick to open, and the calendar quick to fill.

A sheet sends its long lists of choices once (a fish row's Tank), its row
forms inside a <template> (static/row-forms.js), and the browser keeps the
scripts and styles a page names with ?v=. The calendar's sac reminders
are looked up for the window it shows, in one query.
"""
from __future__ import annotations

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
