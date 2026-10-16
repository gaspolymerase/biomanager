"""Fly and worm stock databases: vials and plates, crosses, egg collection,
flips, temperature shifts, genotypes, frozen stocks, settings and who may
change what."""
from __future__ import annotations

import json
import re
import unittest
from datetime import timedelta

from tests.base import *  # noqa: F401,F403
from tests.base import AppTestCase, GRID_NAMING, T, TODAY, app, count, days_ago, days_ahead, one, row, rows, uniq

UCOLS = ("id", "number", "genotype", "purpose", "female_genotype", "male_genotype", "rack_id_fk", "rack_row",
         "rack_col", "active", "ready_on", "last_collected_on", "parent_id_fk", "owner", "set_up_on", "shift_on",
         "shift_to", "shifted_on", "notes", "attrs", "discarded_on")


def unit(unit_id):
    """A vial as a dict, or None once deleted."""
    found = row(f"select {','.join(UCOLS)} from stock_units where id=?", unit_id)
    return dict(zip(UCOLS, found)) if found else None


def cell(unit_id):
    u = unit(unit_id)
    return (u["rack_row"], u["rack_col"])


def top(mid) -> int:
    return one("select coalesce(max(number), 0) from stock_units where module_id_fk=?", mid)


def ids_after(mid, number):
    """Ids of the vials numbered above `number`, in number order."""
    return [i for (i,) in rows("select id from stock_units where module_id_fk=? and number>? order by number",
                               mid, number)]


def rack_cols(rack_id):
    return dict(zip(("incubator_id_fk", "rows", "cols", "last_flipped_on", "name"),
                    row("select incubator_id_fk, rows, cols, last_flipped_on, name from stock_racks where id=?",
                        rack_id)))


def fmt(d, pattern="%d %b"):
    return d.strftime(pattern)


class StockCase(AppTestCase):
    """Shared helpers: vials through the real routes."""

    def url(self, key, path=""):
        return f"/stocks/{key}{path}"

    def create(self, client, key, **fields):
        """POST the new-vial dialog; returns (response, ids of the new vials)."""
        mid = self.stock_module_id(key)
        before = top(mid)
        data = {"id": "", "genotype": uniq("w; g"), "purpose": "stock", "count": 1, "rack_id": "", "position": ""}
        data.update(fields)
        data = {k: v for k, v in data.items() if v is not None}  # None: leave the field out
        r = self.post(client, self.url(key, "/units/save"), data)
        return r, ids_after(mid, before)

    def make_cross(self, client, key, rack_id=None, position="", female=None, male=None, **fields):
        return self.make_vial(client, key, genotype="", purpose="cross", rack_id=rack_id, position=position,
                              female_genotype=female or uniq("w; UAS-"), male_genotype=male or uniq("Gal4-"),
                              **fields)

    def inline(self, client, key, unit_id, **fields):
        return self.autosave(client, self.url(key, f"/units/{unit_id}/update"), fields)

    def action(self, client, key, unit_id, action, **fields):
        return self.post(client, self.url(key, f"/units/{unit_id}/{action}"), fields)

    def bulk(self, client, key, action, ids, **fields):
        return self.post(client, self.url(key, "/units/bulk"), {"action": action, "selected_ids": ids, **fields})


# ---------------------------------------------------------------------------
# Creating vials
# ---------------------------------------------------------------------------


class VialCreationTests(StockCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.make_stock_module(cls.a, "fly")
        cls.mid = cls.stock_module_id(cls.key)
        cls.inc25 = cls.make_incubator(cls, cls.a, cls.key, temperature="25")
        cls.inc18 = cls.make_incubator(cls, cls.a, cls.key, temperature="18")

    def rack(self, rows=4, cols=4, inc=None):
        return self.make_stock_rack(self.a, self.key, rows=rows, cols=cols, incubator_id=inc or self.inc25,
                                    last_flipped_on=T)

    def test_count_creates_consecutive_numbers_in_consecutive_cells(self):
        rack = self.rack()
        before = top(self.mid)
        r, ids = self.create(self.a, self.key, count=5, rack_id=rack, position="A1")
        self.assertNoErrors(r)
        self.assertEqual([unit(i)["number"] for i in ids], list(range(before + 1, before + 6)))
        self.assertEqual([cell(i) for i in ids], [(1, 1), (1, 2), (1, 3), (1, 4), (2, 1)])
        self.assertFlash(r, f"Created V{before + 1}–V{before + 5}")

    def test_blank_position_takes_the_next_free_cells(self):
        rack = self.rack()
        self.make_vial(self.a, self.key, rack_id=rack, position="A2")
        _, ids = self.create(self.a, self.key, count=3, rack_id=rack)
        self.assertEqual([cell(i) for i in ids], [(1, 1), (1, 3), (1, 4)])

    def test_vials_beyond_the_racks_room_stay_in_it_unplaced(self):
        rack = self.rack(rows=2, cols=2)
        r, ids = self.create(self.a, self.key, count=5, rack_id=rack)
        self.assertEqual(len(ids), 5)
        last = unit(ids[-1])
        self.assertEqual((last["rack_id_fk"], last["rack_row"]), (rack, None))
        self.assertFlash(r, "had room for 4 of 5", "error")

    def test_new_genotype_is_remembered_once(self):
        geno = uniq("w; Sp/CyO ")
        self.create(self.a, self.key, genotype=geno, count=2)
        self.create(self.a, self.key, genotype=geno)
        self.assertEqual(count("stock_genotypes", "module_id_fk=? and genotype=?", self.mid, geno), 1)

    def test_create_in_a_taken_cell_is_refused(self):
        rack = self.rack()
        self.make_vial(self.a, self.key, rack_id=rack, position="B2")
        r, ids = self.create(self.a, self.key, rack_id=rack, position="B2")
        self.assertEqual(ids, [])
        self.assertFlash(r, "already taken", "error")

    def test_position_outside_the_rack_is_refused(self):
        rack = self.rack()
        for pos in ("E1", "A5", "Z99", "??"):
            with self.subTest(pos=pos):
                r, ids = self.create(self.a, self.key, rack_id=rack, position=pos)
                self.assertEqual(ids, [])
                self.assertFlash(r, "is not a position", "error")

    def test_count_outside_1_to_60_is_refused(self):
        for n in ("0", "61", "500", "-3"):
            with self.subTest(count=n):
                r, ids = self.create(self.a, self.key, count=n)
                self.assertEqual(ids, [])
                self.assertFlash(r, "between 1 and 60", "error")

    def test_bad_date_or_unknown_owner_creates_nothing(self):
        for field, value, message in (("set_up_on", "25/09/2026", "not a date"),
                                      ("ready_on", "2026-02-31", "not a date"),
                                      ("owner", uniq("nobody"), "not a lab member"),
                                      ("purpose", "zzz", "not one of this database")):
            with self.subTest(field=field):
                r, ids = self.create(self.a, self.key, count=3, **{field: value})
                self.assertEqual(ids, [])
                self.assertFlash(r, message, "error")

    def test_new_progeny_vial_is_due_by_its_racks_temperature(self):
        rack = self.rack(inc=self.inc18)
        _, ids = self.create(self.a, self.key, purpose="progeny", rack_id=rack, set_up_on=T)
        self.assertEqual(unit(ids[0])["ready_on"], days_ahead(19))  # 18 °C: 19 days to eclose

    def test_rack_of_another_database_is_not_found(self):
        other = self.make_stock_module(self.a, "fly")
        foreign = self.make_stock_rack(self.a, other)
        r = self.a.post(self.url(self.key, "/units/save"),
                        data={"id": "", "genotype": "x", "purpose": "stock", "count": 1, "rack_id": foreign})
        self.assertEqual(r.status_code, 404)

    def test_each_database_numbers_its_vials_from_one(self):
        other = self.make_stock_module(self.a, "fly")
        vial = self.make_vial(self.a, other)
        self.assertEqual(unit(vial)["number"], 1)

    def test_new_vial_is_owned_by_its_maker_and_set_up_today(self):
        vial = self.make_vial(self.m, self.key)
        u = unit(vial)
        self.assertEqual((u["owner"], u["set_up_on"], u["active"]), (self.member, T, 1))


# ---------------------------------------------------------------------------
# Crosses and egg collection
# ---------------------------------------------------------------------------


class CrossAndCollectionTests(StockCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.make_stock_module(cls.a, "fly")
        cls.mid = cls.stock_module_id(cls.key)
        cls.incs = {t: cls.make_incubator(cls, cls.a, cls.key, temperature=t) for t in ("18", "22", "25", "29")}

    def rack(self, temp="25", rows=4, cols=4):
        return self.make_stock_rack(self.a, self.key, rows=rows, cols=cols, incubator_id=self.incs[temp],
                                    last_flipped_on=T)

    def test_cross_is_labelled_by_its_parents(self):
        female, male = uniq("w; UAS-GFP"), uniq("elav-Gal4")
        cross = self.make_cross(self.a, self.key, female=female, male=male)
        u = unit(cross)
        self.assertEqual(u["genotype"], f"{female} × {male}")
        self.assertEqual((u["female_genotype"], u["male_genotype"], u["purpose"]), (female, male, "cross"))

    def test_cross_parents_are_remembered_but_not_its_label(self):
        female, male = uniq("w; UAS-mCD8"), uniq("GMR-Gal4")
        self.make_cross(self.a, self.key, female=female, male=male)
        listed = {g for (g,) in rows("select genotype from stock_genotypes where module_id_fk=?", self.mid)}
        self.assertLessEqual({female, male}, listed)
        self.assertNotIn(f"{female} × {male}", listed)

    def test_cross_with_one_parent_shows_a_question_mark(self):
        male = uniq("Oregon-R")
        cross = self.make_vial(self.a, self.key, genotype="", purpose="cross", male_genotype=male)
        self.assertEqual(unit(cross)["genotype"], f"? × {male}")

    def test_collecting_eggs_makes_a_progeny_vial_next_to_the_cross(self):
        rack = self.rack("25")
        cross = self.make_cross(self.a, self.key, rack_id=rack, position="A1", set_up_on=days_ago(3))
        before = top(self.mid)
        r = self.action(self.a, self.key, cross, "collect")
        self.assertNoErrors(r)
        (progeny,) = ids_after(self.mid, before)
        p, c = unit(progeny), unit(cross)
        self.assertEqual(p["purpose"], "progeny")
        self.assertEqual(p["genotype"], f"F1 of {c['genotype']}")
        self.assertEqual((p["female_genotype"], p["male_genotype"]), (c["female_genotype"], c["male_genotype"]))
        self.assertEqual(p["parent_id_fk"], cross)
        self.assertEqual((p["rack_id_fk"], p["rack_row"], p["rack_col"]), (rack, 1, 2))
        self.assertEqual(p["ready_on"], days_ahead(10))  # 25 °C: 10 days
        self.assertEqual(c["last_collected_on"], T)

    def test_progeny_due_date_follows_the_incubator_temperature(self):
        for temp, days in (("18", 19), ("22", 13), ("29", 8)):
            with self.subTest(temp=temp):
                cross = self.make_cross(self.a, self.key, rack_id=self.rack(temp))
                before = top(self.mid)
                self.action(self.a, self.key, cross, "collect")
                (progeny,) = ids_after(self.mid, before)
                self.assertEqual(unit(progeny)["ready_on"], days_ahead(days))

    def test_a_temperature_not_in_the_table_takes_the_nearest(self):
        from app import stock_service as svc
        from app.db import SessionLocal
        with SessionLocal() as s:
            mv = svc.view(svc.get_module(s, self.key))
            self.assertEqual([mv.interval("develop", t) for t in ("22", "21", "30", "RT", "18.0", "abc")],
                             [13, 13, 8, 13, 19, mv.interval("develop", mv.s["default_temperature"])])

    def test_progeny_moved_to_another_temperature_emerge_on_its_time(self):
        cross = self.make_cross(self.a, self.key, rack_id=self.rack("25"))
        before = top(self.mid)
        self.action(self.a, self.key, cross, "collect")
        (progeny,) = ids_after(self.mid, before)
        self.assertEqual(unit(progeny)["ready_on"], days_ahead(10))
        cold = self.rack("18")
        r = self.a.post(f"/stocks/{self.key}/units/{progeny}/place", data={"rack_id": cold, "row": 1, "col": 1})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(unit(progeny)["ready_on"], days_ahead(19))     # all of it still to go, at 18 °C

    def test_collecting_from_a_stock_or_discarded_cross_is_refused(self):
        stock = self.make_vial(self.a, self.key, purpose="stock")
        cross = self.make_cross(self.a, self.key)
        self.action(self.a, self.key, cross, "discard")
        for vial in (stock, cross):
            with self.subTest(vial=vial):
                before = top(self.mid)
                r = self.action(self.a, self.key, vial, "collect")
                self.assertFlash(r, "is not an active cross", "error")
                self.assertEqual(top(self.mid), before)

    def test_collecting_into_a_full_rack_keeps_progeny_in_it_without_a_cell(self):
        rack = self.rack(rows=1, cols=1)
        cross = self.make_cross(self.a, self.key, rack_id=rack)
        before = top(self.mid)
        r = self.action(self.a, self.key, cross, "collect")
        (progeny,) = ids_after(self.mid, before)
        self.assertEqual((unit(progeny)["rack_id_fk"], unit(progeny)["rack_row"]), (rack, None))
        self.assertFlash(r, "is full")

    def test_bulk_collect_acts_only_on_crosses(self):
        crosses = [self.make_cross(self.a, self.key) for _ in range(2)]
        stock = self.make_vial(self.a, self.key, purpose="stock")
        before = top(self.mid)
        r = self.bulk(self.a, self.key, "collect", crosses + [stock])
        made = ids_after(self.mid, before)
        self.assertEqual(sorted(unit(i)["parent_id_fk"] for i in made), sorted(crosses))
        self.assertFlash(r, "from 2 crosses")
        self.assertIsNone(unit(stock)["last_collected_on"])

    def test_deleting_a_cross_unlinks_its_progeny(self):
        cross = self.make_cross(self.a, self.key)
        before = top(self.mid)
        self.action(self.a, self.key, cross, "collect")
        (progeny,) = ids_after(self.mid, before)
        self.action(self.a, self.key, cross, "delete")
        self.assertIsNone(unit(cross))
        self.assertIsNone(unit(progeny)["parent_id_fk"])

    def test_schedule_lists_collection_and_emerging_progeny(self):
        cross = self.make_cross(self.a, self.key, rack_id=self.rack(), set_up_on=days_ago(5))
        before = top(self.mid)
        self.action(self.a, self.key, cross, "collect")
        (progeny,) = ids_after(self.mid, before)
        html = self.get_ok(self.a, self.url(self.key, "?view=schedule"))
        self.assertIn(f"Collect eggs: V{unit(cross)['number']}</span>", html)
        self.assertIn(f"Progeny eclose: V{unit(progeny)['number']}</span>", html)
        self.assertIn(f"from V{unit(cross)['number']}", html)

    def test_becoming_progeny_starts_the_due_clock(self):
        vial = self.make_vial(self.a, self.key, purpose="stock", rack_id=self.rack("25"))
        self.assertSaved(self.inline(self.a, self.key, vial, purpose="progeny", purpose_was="stock"))
        self.assertEqual(unit(vial)["ready_on"], days_ahead(10))


# ---------------------------------------------------------------------------
# Flips, temperatures, shifts
# ---------------------------------------------------------------------------


class FlipAndShiftTests(StockCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.make_stock_module(cls.a, "fly")
        cls.mid = cls.stock_module_id(cls.key)
        cls.inc18 = cls.make_incubator(cls, cls.a, cls.key, temperature="18")
        cls.inc25 = cls.make_incubator(cls, cls.a, cls.key, temperature="25")
        cls.inc29 = cls.make_incubator(cls, cls.a, cls.key, temperature="29")

    def test_flipped_records_today_and_reports_the_next_flip(self):
        rack = self.make_stock_rack(self.a, self.key, incubator_id=self.inc25, last_flipped_on=days_ago(10))
        r = self.post(self.a, self.url(self.key, f"/racks/{rack}/flipped"))
        self.assertEqual(rack_cols(rack)["last_flipped_on"], T)
        self.assertFlash(r, "next on " + fmt(TODAY + timedelta(days=14), "%a %d %b"), "success")

    def test_flipped_on_a_given_date_uses_the_temperature_interval(self):
        rack = self.make_stock_rack(self.a, self.key, incubator_id=self.inc18)
        r = self.post(self.a, self.url(self.key, f"/racks/{rack}/flipped"), {"on": days_ago(3)})
        self.assertEqual(rack_cols(rack)["last_flipped_on"], days_ago(3))
        self.assertFlash(r, "next on " + fmt(TODAY + timedelta(days=25), "%a %d %b"))  # 18 °C: every 28 d

    def test_the_rack_grid_shows_the_last_flip_and_the_next(self):
        import json as _json
        import re as _re

        def note(rack_id):
            html = self.get_ok(self.a, self.url(self.key, ""))
            blob = _re.search(r'<script type="application/json" data-rack-data>(.*?)</script>', html, _re.S).group(1)
            return next(r["note"] for r in _json.loads(blob)["racks"] if r["id"] == rack_id)

        fresh = self.make_stock_rack(self.a, self.key, incubator_id=self.inc25, last_flipped_on=days_ago(4))
        got = note(fresh)
        self.assertEqual(got["text"], f"Flipped {fmt(TODAY - timedelta(days=4), '%a %d %b')} (4 d ago) · next "
                                      f"{fmt(TODAY + timedelta(days=10), '%a %d %b')}")
        self.assertEqual(got["tone"], "")
        self.assertEqual(got["title"], "Every 14 days at 25 °C")
        late = self.make_stock_rack(self.a, self.key, incubator_id=self.inc25, last_flipped_on=days_ago(20))
        self.assertEqual(note(late)["tone"], "overdue")
        self.assertIn("flip 6 d overdue", note(late)["text"])
        never = self.make_stock_rack(self.a, self.key, incubator_id=self.inc25)
        self.assertEqual(note(never)["text"], "No flip recorded yet")

    def test_flipped_today_from_the_grid_records_it_and_comes_back_to_the_vials(self):
        import json as _json
        import re as _re

        def action(rack_id):
            html = self.get_ok(self.m, self.url(self.key, ""))
            blob = _re.search(r'<script type="application/json" data-rack-data>(.*?)</script>', html, _re.S).group(1)
            return next(r["action"] for r in _json.loads(blob)["racks"] if r["id"] == rack_id)

        rack = self.make_stock_rack(self.a, self.key, incubator_id=self.inc25, last_flipped_on=days_ago(12))
        offered = action(rack)
        self.assertEqual(offered["label"], "Flipped today")
        self.assertFalse(offered["done"])
        r = self.m.post(offered["url"], data=offered["fields"])
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("view=schedule", r.headers["Location"])
        self.assertEqual(rack_cols(rack)["last_flipped_on"], T)
        self.assertTrue(action(rack)["done"])

    def test_flipped_with_a_bad_date_is_refused(self):
        rack = self.make_stock_rack(self.a, self.key, incubator_id=self.inc25, last_flipped_on=days_ago(4))
        r = self.post(self.a, self.url(self.key, f"/racks/{rack}/flipped"), {"on": "yesterday"})
        self.assertFlash(r, "not a date", "error")
        self.assertEqual(rack_cols(rack)["last_flipped_on"], days_ago(4))

    def test_a_racks_own_flip_days_override_the_temperature(self):
        rack = self.make_stock_rack(self.a, self.key, incubator_id=self.inc25, flip_days="5")
        r = self.post(self.a, self.url(self.key, f"/racks/{rack}/flipped"))
        self.assertFlash(r, "next on " + fmt(TODAY + timedelta(days=5), "%a %d %b"))

    def test_anyone_may_record_a_flip_on_someone_elses_rack(self):
        rack = self.make_stock_rack(self.a, self.key, incubator_id=self.inc25, last_flipped_on=days_ago(20))
        self.post(self.m, self.url(self.key, f"/racks/{rack}/flipped"))
        self.assertEqual(rack_cols(rack)["last_flipped_on"], T)

    def test_schedule_lists_overdue_and_unrecorded_flips_of_racks_with_vials(self):
        overdue = self.make_stock_rack(self.a, self.key, incubator_id=self.inc18, last_flipped_on=days_ago(30))
        unset = self.make_stock_rack(self.a, self.key, incubator_id=self.inc25)
        empty = self.make_stock_rack(self.a, self.key, incubator_id=self.inc25, last_flipped_on=days_ago(30))
        self.make_vial(self.a, self.key, rack_id=overdue)
        self.make_vial(self.a, self.key, rack_id=unset)
        html = self.get_ok(self.a, self.url(self.key, "?view=schedule"))
        self.assertIn(f"Flip {rack_cols(overdue)['name']}</span>", html)
        self.assertIn(f"Flip {rack_cols(unset)['name']}</span>", html)
        self.assertIn("no flip date recorded yet", html)
        self.assertNotIn(f"Flip {rack_cols(empty)['name']}</span>", html)

    def test_incubator_temperature_is_normalised_and_unknown_ones_noted(self):
        name = uniq("Inc")
        self.post(self.a, self.url(self.key, "/incubators/save"), {"name": name, "temperature": "25.0 °C"})
        self.assertEqual(one("select temperature from stock_incubators where name=?", name), "25")
        r = self.post(self.a, self.url(self.key, "/incubators/save"), {"name": uniq("Inc"), "temperature": "31"})
        self.assertFlash(r, "31 °C has no timings")

    def test_setup_shows_the_flip_interval_of_each_rack(self):
        rack = self.make_stock_rack(self.a, self.key, incubator_id=self.inc29)
        html = self.get_ok(self.a, self.url(self.key, "?view=setup"))
        name = rack_cols(rack)["name"]
        self.assertRegex(html, re.escape(name) + r"</td>(?s:.*?)<td>10 d")

    def test_shifted_without_a_planned_shift_is_refused(self):
        vial = self.make_vial(self.a, self.key)
        r = self.action(self.a, self.key, vial, "shifted")
        self.assertFlash(r, "no temperature shift planned", "error")
        self.assertIsNone(unit(vial)["shifted_on"])

    def test_planned_shift_is_scheduled_until_marked_shifted(self):
        vial = self.make_vial(self.a, self.key, purpose="experiment", shift_on=days_ago(1), shift_to="29.0")
        code = f"V{unit(vial)['number']}"
        self.assertEqual(unit(vial)["shift_to"], "29")
        self.assertIn(f"Shift {code} to 29 °C</span>", self.get_ok(self.a, self.url(self.key, "?view=schedule")))
        r = self.action(self.a, self.key, vial, "shifted")
        self.assertFlash(r, f"Shifted {code} to 29", "success")
        self.assertEqual(unit(vial)["shifted_on"], T)
        self.assertNotIn(f"Shift {code} to 29 °C</span>", self.get_ok(self.a, self.url(self.key, "?view=schedule")))

    def test_shifted_into_another_rack_takes_its_first_free_cell(self):
        cold = self.make_stock_rack(self.a, self.key, incubator_id=self.inc18)
        hot = self.make_stock_rack(self.a, self.key, incubator_id=self.inc29)
        self.make_vial(self.a, self.key, rack_id=hot, position="A1")
        vial = self.make_vial(self.a, self.key, purpose="experiment", rack_id=cold, shift_on=T, shift_to="29")
        self.action(self.a, self.key, vial, "shifted", rack_id=hot)
        u = unit(vial)
        self.assertEqual((u["rack_id_fk"], u["rack_row"], u["rack_col"], u["shifted_on"]), (hot, 1, 2, T))

    def test_shifted_into_a_full_rack_is_refused_and_nothing_moves(self):
        cold = self.make_stock_rack(self.a, self.key, incubator_id=self.inc18)
        full = self.make_stock_rack(self.a, self.key, rows=1, cols=1, incubator_id=self.inc29)
        self.make_vial(self.a, self.key, rack_id=full)
        vial = self.make_vial(self.a, self.key, purpose="experiment", rack_id=cold, shift_on=T, shift_to="29")
        r = self.action(self.a, self.key, vial, "shifted", rack_id=full)
        self.assertFlash(r, "Not shifted", "error")
        u = unit(vial)
        self.assertEqual((u["rack_id_fk"], u["rack_row"], u["shifted_on"]), (cold, 1, None))


# ---------------------------------------------------------------------------
# Single-vial actions and editing
# ---------------------------------------------------------------------------


class UnitActionTests(StockCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.make_stock_module(cls.a, "fly")
        cls.mid = cls.stock_module_id(cls.key)

    def rack(self, **kw):
        return self.make_stock_rack(self.a, self.key, **kw)

    def test_discard_frees_the_cell_and_restore_takes_a_free_one(self):
        rack = self.rack()
        vial = self.make_vial(self.a, self.key, rack_id=rack, position="C3")
        self.action(self.a, self.key, vial, "discard")
        u = unit(vial)
        self.assertEqual((u["active"], u["rack_row"], u["discarded_on"], u["rack_id_fk"]), (0, None, T, rack))
        self.action(self.a, self.key, vial, "restore")
        u = unit(vial)
        self.assertEqual((u["active"], u["discarded_on"], u["rack_row"], u["rack_col"]), (1, None, 1, 1))

    def test_ready_done_clears_the_due_date_and_scored_is_remembered(self):
        vial = self.make_vial(self.a, self.key, purpose="progeny", score_on=T)
        self.assertIsNotNone(unit(vial)["ready_on"])
        self.action(self.a, self.key, vial, "ready-done")
        self.assertIsNone(unit(vial)["ready_on"])
        self.action(self.a, self.key, vial, "scored")
        self.assertEqual(json.loads(unit(vial)["attrs"])["scored_on"], T)

    def test_unknown_action_is_not_found(self):
        vial = self.make_vial(self.a, self.key)
        self.assertEqual(self.a.post(self.url(self.key, f"/units/{vial}/explode")).status_code, 404)

    def test_delete_removes_the_vial(self):
        vial = self.make_vial(self.a, self.key)
        r = self.action(self.a, self.key, vial, "delete")
        self.assertIsNone(unit(vial))
        self.assertFlash(r, "Deleted V", "success")

    def test_duplicate_is_a_fresh_vial_without_the_originals_shift_history(self):
        rack = self.rack()
        vial = self.make_vial(self.a, self.key, purpose="experiment", rack_id=rack, position="A1",
                              shift_on=days_ago(3), shift_to="29", notes="keep me", set_up_on=days_ago(9))
        self.action(self.a, self.key, vial, "shifted")
        before = top(self.mid)
        self.action(self.m, self.key, vial, "duplicate")  # copying someone's vial is open: the copy is yours
        (copy,) = ids_after(self.mid, before)
        c = unit(copy)
        self.assertEqual((c["genotype"], c["purpose"], c["notes"], c["shift_to"]),
                         (unit(vial)["genotype"], "experiment", "keep me", "29"))
        self.assertEqual((c["owner"], c["set_up_on"], c["shift_on"], c["shifted_on"]), (self.member, T, None, None))
        self.assertEqual((c["rack_id_fk"], c["rack_row"], c["rack_col"]), (rack, 1, 2))

    def test_discarded_vial_cannot_be_edited_inline(self):
        vial = self.make_vial(self.a, self.key)
        self.action(self.a, self.key, vial, "discard")
        r = self.inline(self.a, self.key, vial, notes="x")
        self.assertEqual(r.status_code, 403)
        self.assertIn("discarded", r.get_json()["error"])

    def test_inline_edit_saves_and_returns_the_fresh_row(self):
        rack = self.rack()
        vial = self.make_vial(self.a, self.key, genotype="OreR", rack_id=rack, position="A1")
        r = self.inline(self.a, self.key, vial, genotype="OreR; +", genotype_was="OreR",
                        rack_id=rack, rack_id_was=rack, position="D4", position_was="A1")
        self.assertSaved(r)
        body = r.get_json()
        self.assertEqual(body["row"]["values"]["position"], "D4")
        self.assertEqual(body["payload"]["genotype_was"], "OreR; +")
        self.assertEqual(cell(vial), (4, 4))

    def test_inline_move_onto_a_taken_cell_is_refused(self):
        rack = self.rack()
        vial = self.make_vial(self.a, self.key, rack_id=rack, position="A1")
        self.make_vial(self.a, self.key, rack_id=rack, position="A2")
        r = self.inline(self.a, self.key, vial, rack_id=rack, rack_id_was=rack, position="A2", position_was="A1")
        self.assertRefused(r)
        self.assertIn("already holds", r.get_json()["error"])
        self.assertEqual(cell(vial), (1, 1))

    def test_inline_invalid_values_are_refused(self):
        vial = self.make_vial(self.a, self.key, set_up_on=days_ago(2))
        for field, value in (("set_up_on", "2026-02-31"), ("owner", uniq("nobody")), ("purpose", "zzz")):
            with self.subTest(field=field):
                self.assertRefused(self.inline(self.a, self.key, vial, **{field: value}))
        u = unit(vial)
        self.assertEqual((u["set_up_on"], u["owner"], u["purpose"]), (days_ago(2), self.admin, "stock"))

    def test_grid_drag_onto_an_occupied_cell_swaps(self):
        rack = self.rack()
        a = self.make_vial(self.a, self.key, rack_id=rack, position="A1")
        b = self.make_vial(self.a, self.key, rack_id=rack, position="A2")
        r = self.a.post(self.url(self.key, f"/units/{a}/place"), data={"rack_id": rack, "row": 1, "col": 2})
        self.assertEqual(r.get_json(), {"ok": True})
        self.assertEqual((cell(a), cell(b)), ((1, 2), (1, 1)))

    def test_grid_drop_of_an_unplaced_vial_on_a_taken_cell_is_refused(self):
        rack = self.rack()
        holder = self.make_vial(self.a, self.key, rack_id=rack, position="A1")
        loose = self.make_vial(self.a, self.key)
        r = self.a.post(self.url(self.key, f"/units/{loose}/place"), data={"rack_id": rack, "row": 1, "col": 1})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(cell(holder), (1, 1))

    def test_grid_drop_outside_the_rack_or_into_another_database_is_refused(self):
        rack = self.rack(rows=2, cols=2)
        foreign = self.make_stock_rack(self.a, self.make_stock_module(self.a, "fly"))
        vial = self.make_vial(self.a, self.key, rack_id=rack, position="A1")
        for data in ({"rack_id": rack, "row": 3, "col": 1}, {"rack_id": foreign, "row": 1, "col": 1}):
            with self.subTest(data=data):
                r = self.a.post(self.url(self.key, f"/units/{vial}/place"), data=data)
                self.assertEqual(r.status_code, 400)
        self.assertEqual(unit(vial)["rack_id_fk"], rack)

    def test_editing_a_vial_through_another_databases_url_is_not_found(self):
        vial = self.make_vial(self.a, self.key, genotype="mine")
        other = self.make_stock_module(self.a, "fly")
        r = self.inline(self.a, other, vial, genotype="hijack")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(unit(vial)["genotype"], "mine")

    def test_shrinking_a_rack_over_its_vials_asks_what_happens_to_them(self):
        rack = self.rack(rows=6, cols=8)
        self.make_vial(self.a, self.key, rack_id=rack, position="F8")
        data = {"id": rack, "name": rack_cols(rack)["name"], "rows": 3, "cols": 3, **GRID_NAMING}
        r = self.post(self.a, self.url(self.key, "/racks/save"), data)
        self.assertFlash(r, "changes the positions of 1 vial", "error")
        self.assertEqual((rack_cols(rack)["rows"], rack_cols(rack)["cols"]), (6, 8))
        r = self.post(self.a, self.url(self.key, "/racks/save"), {**data, "cages": "keep_names"})
        self.assertFlash(r, "no position", "success")
        self.assertEqual((rack_cols(rack)["rows"], rack_cols(rack)["cols"]), (3, 3))

    def test_rack_save_without_a_flip_date_field_keeps_the_old_one(self):
        rack = self.rack(last_flipped_on=days_ago(2))
        self.post(self.a, self.url(self.key, "/racks/save"),
                  {"id": rack, "name": rack_cols(rack)["name"], "rows": 4, "cols": 4, **GRID_NAMING})
        self.assertEqual(rack_cols(rack)["last_flipped_on"], days_ago(2))


# ---------------------------------------------------------------------------
# Stale forms
# ---------------------------------------------------------------------------


class StaleFormTests(StockCase):
    """Every row and dialog posts `<field>_was`: a field whose value still
    equals what the form was showing is not written, so a stale form cannot
    undo a change made elsewhere since it was opened."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.make_stock_module(cls.a, "fly")

    def setUp(self):
        self.rack = self.make_stock_rack(self.a, self.key)
        self.vial = self.make_vial(self.a, self.key, genotype="OreR", rack_id=self.rack, position="A2")
        # The dialog as it was opened, before anything below changed.
        self.stale = {"id": self.vial, "genotype": "OreR", "genotype_was": "OreR", "notes": "", "notes_was": "",
                      "rack_id": self.rack, "rack_id_was": self.rack, "position": "A2", "position_was": "A2",
                      "purpose": "stock", "purpose_was": "stock"}

    def test_stale_dialog_keeps_a_newer_inline_genotype_and_saves_its_own_note(self):
        self.assertSaved(self.inline(self.a, self.key, self.vial, genotype="OreR-new", genotype_was="OreR"))
        r = self.post(self.a, self.url(self.key, "/units/save"), dict(self.stale, notes="dialog note"))
        self.assertNoErrors(r)
        self.assertEqual((unit(self.vial)["genotype"], unit(self.vial)["notes"]), ("OreR-new", "dialog note"))

    def test_stale_dialog_does_not_move_a_dragged_vial_back(self):
        self.a.post(self.url(self.key, f"/units/{self.vial}/place"), data={"rack_id": self.rack, "row": 3, "col": 3})
        self.post(self.a, self.url(self.key, "/units/save"), dict(self.stale, notes="second"))
        self.assertEqual(cell(self.vial), (3, 3))

    def test_stale_row_does_not_move_it_back(self):
        self.inline(self.a, self.key, self.vial, rack_id=self.rack, rack_id_was=self.rack,
                    position="D4", position_was="A2")
        r = self.inline(self.a, self.key, self.vial, rack_id=self.rack, rack_id_was=self.rack,
                        position="A2", position_was="A2", notes="typed on the stale row")
        self.assertSaved(r)
        self.assertEqual(cell(self.vial), (4, 4))
        self.assertEqual(unit(self.vial)["notes"], "typed on the stale row")

    def test_a_field_changed_in_the_stale_form_still_wins(self):
        self.inline(self.a, self.key, self.vial, genotype="OreR-new", genotype_was="OreR")
        self.post(self.a, self.url(self.key, "/units/save"), dict(self.stale, genotype="Canton-S"))
        self.assertEqual(unit(self.vial)["genotype"], "Canton-S")


# ---------------------------------------------------------------------------
# Genotypes
# ---------------------------------------------------------------------------


class GenotypeTests(StockCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.make_stock_module(cls.a, "fly")
        cls.mid = cls.stock_module_id(cls.key)

    def gid(self, text, mid=None):
        return one("select id from stock_genotypes where module_id_fk=? and genotype=?", mid or self.mid, text)

    def rename(self, client, old, new, **fields):
        return self.post(client, self.url(self.key, "/genotypes/save"), {"id": self.gid(old), "genotype": new, **fields})

    def test_rename_relabels_vials_parents_and_cross_labels(self):
        female, male = uniq("w; UAS-GFP"), uniq("GMR-Gal4")
        stock = self.make_vial(self.a, self.key, genotype=male)
        cross = self.make_cross(self.a, self.key, female=female, male=male)
        before = top(self.mid)
        self.action(self.a, self.key, cross, "collect")
        (progeny,) = ids_after(self.mid, before)
        new = male + ", Kr>GFP"
        r = self.rename(self.a, male, new, alias="GMR", source="BDSC", stock_number="1104")
        self.assertFlash(r, "Relabelled", "success")
        self.assertEqual(unit(stock)["genotype"], new)
        self.assertEqual((unit(cross)["male_genotype"], unit(cross)["genotype"]), (new, f"{female} × {new}"))
        self.assertEqual(unit(progeny)["genotype"], f"F1 of {female} × {new}")
        self.assertEqual(row("select alias, source, stock_number from stock_genotypes where id=?", self.gid(new)),
                         ("GMR", "BDSC", "1104"))

    def test_rename_onto_an_existing_genotype_is_refused(self):
        a, b = uniq("FM7a"), uniq("TM3")
        va = self.make_vial(self.a, self.key, genotype=a)
        self.make_vial(self.a, self.key, genotype=b)
        r = self.rename(self.a, a, b)
        self.assertFlash(r, "already in the list", "error")
        self.assertEqual(unit(va)["genotype"], a)

    def test_blank_genotype_is_refused(self):
        a = uniq("yw")
        self.make_vial(self.a, self.key, genotype=a)
        self.assertFlash(self.rename(self.a, a, "   "), "needs text", "error")
        self.assertIsNotNone(self.gid(a))

    def test_rename_in_one_database_leaves_another_alone(self):
        geno = uniq("w; Sp/CyO")
        other = self.make_stock_module(self.a, "fly")
        theirs = self.make_vial(self.a, other, genotype=geno)
        self.make_vial(self.a, self.key, genotype=geno)
        self.rename(self.a, geno, geno + ", Sb")
        self.assertEqual(unit(theirs)["genotype"], geno)
        self.assertIsNotNone(self.gid(geno, self.stock_module_id(other)))

    def test_member_cannot_rename_a_genotype_on_someone_elses_cross(self):
        female = uniq("virgin A")
        cross = self.make_cross(self.o, self.key, female=female)
        r = self.rename(self.m, female, female + "2")
        self.assertFlash(r, "belong to someone else", "error")
        self.assertEqual(unit(cross)["female_genotype"], female)
        self.assertIsNotNone(self.gid(female))

    def test_admin_may_rename_a_genotype_on_anyones_vials(self):
        female = uniq("virgin B")
        cross = self.make_cross(self.o, self.key, female=female)
        self.rename(self.a, female, female + "2")
        self.assertEqual(unit(cross)["female_genotype"], female + "2")

    def test_genotype_in_use_cannot_be_removed_but_an_unused_one_can(self):
        used, spare = uniq("used"), uniq("spare")
        self.make_cross(self.a, self.key, female=used)
        self.post(self.a, self.url(self.key, "/genotypes/save"), {"id": "", "genotype": spare})
        r = self.post(self.a, self.url(self.key, f"/genotypes/{self.gid(used)}/delete"))
        self.assertFlash(r, "still carry", "error")
        self.assertIsNotNone(self.gid(used))
        spare_id = self.gid(spare)
        self.post(self.a, self.url(self.key, f"/genotypes/{spare_id}/delete"))
        self.assertEqual(count("stock_genotypes", "id=?", spare_id), 0)


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class PermissionTests(StockCase):
    """Crosses, experiments and progeny belong to whoever set them up;
    stocks, backups and maintenance plates are the lab's."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.make_stock_module(cls.a, "fly")
        cls.mid = cls.stock_module_id(cls.key)
        cls.inc = cls.make_incubator(cls, cls.a, cls.key, temperature="25")

    def setUp(self):
        self.rack = self.make_stock_rack(self.a, self.key, incubator_id=self.inc, last_flipped_on=T)
        # The other member's cross and experiment.
        self.cross = self.make_cross(self.o, self.key, rack_id=self.rack, position="A1")
        self.exp = self.make_vial(self.o, self.key, purpose="experiment", rack_id=self.rack, position="A2",
                                  shift_on=T, shift_to="29", score_on=T)

    def test_member_cannot_edit_someone_elses_cross_inline_or_in_the_dialog(self):
        r = self.inline(self.m, self.key, self.cross, genotype="x")
        self.assertEqual(r.status_code, 403)
        self.assertIn(self.other, r.get_json()["error"])
        r = self.post(self.m, self.url(self.key, "/units/save"), {"id": self.cross, "genotype": "x"})
        self.assertFlash(r, f"belongs to {self.other}", "error")
        self.assertNotEqual(unit(self.cross)["genotype"], "x")

    def test_member_cannot_move_someone_elses_cross(self):
        r = self.m.post(self.url(self.key, f"/units/{self.cross}/place"), data={"rack_id": self.rack, "row": 4, "col": 4})
        self.assertEqual(r.status_code, 403)
        r = self.m.post(self.url(self.key, f"/units/{self.exp}/place"), data={"rack_id": ""})
        self.assertEqual(r.status_code, 403)
        self.assertEqual((cell(self.cross), cell(self.exp)), ((1, 1), (1, 2)))

    def test_member_cannot_swap_their_vial_into_someone_elses_cell(self):
        mine = self.make_vial(self.m, self.key, purpose="experiment", rack_id=self.rack, position="B1")
        r = self.m.post(self.url(self.key, f"/units/{mine}/place"), data={"rack_id": self.rack, "row": 1, "col": 1})
        self.assertEqual(r.status_code, 403)
        self.assertEqual((cell(mine), cell(self.cross)), ((2, 1), (1, 1)))

    def test_member_cannot_act_on_someone_elses_cross(self):
        for act in ("discard", "delete", "collect", "shifted", "scored", "ready-done"):
            with self.subTest(action=act):
                before = top(self.mid)
                r = self.action(self.m, self.key, self.exp if act in ("shifted", "scored") else self.cross, act)
                self.assertFlash(r, "Ask them, or an admin", "error")
                self.assertEqual(top(self.mid), before)
        c, e = unit(self.cross), unit(self.exp)
        self.assertEqual((c["active"], c["last_collected_on"], e["shifted_on"]), (1, None, None))
        self.assertNotIn("scored_on", json.loads(e["attrs"]))

    def test_member_may_edit_a_lab_stock(self):
        stock = self.make_vial(self.o, self.key, purpose="stock")
        self.assertSaved(self.inline(self.m, self.key, stock, notes="flipped by member"))
        self.assertEqual(unit(stock)["notes"], "flipped by member")

    def test_admin_may_edit_anyones_cross(self):
        self.assertSaved(self.inline(self.a, self.key, self.cross, notes="checked"))

    def test_bulk_discard_skips_what_is_not_yours_and_the_member_can_undo_it(self):
        stock = self.make_vial(self.o, self.key, purpose="stock", rack_id=self.rack, position="C1")
        r = self.bulk(self.m, self.key, "discard", [self.cross, stock])
        self.assertFlash(r, "1 belong to someone else and were left alone")
        self.assertEqual((unit(self.cross)["active"], unit(stock)["active"]), (1, 0))
        batch = one("select max(id) from batches where actor=?", self.member)
        r = self.post(self.m, f"/batches/{batch}/undo")
        self.assertFlash(r, "Undid", "success")
        self.assertEqual(unit(stock)["active"], 1)

    def test_grid_marks_someone_elses_cross_locked(self):
        html = self.get_ok(self.m, self.url(self.key))
        grid = json.loads(re.search(r"data-rack-data>(.*?)</script>", html, re.S).group(1))
        locked = {i["id"]: i["locked"] for i in grid["items"]}
        self.assertTrue(locked[self.cross])
        grid_o = json.loads(re.search(r"data-rack-data>(.*?)</script>",
                                      self.get_ok(self.o, self.url(self.key)), re.S).group(1))
        self.assertFalse({i["id"]: i["locked"] for i in grid_o["items"]}[self.cross])

    def test_only_the_racks_maker_or_an_admin_may_change_or_delete_it(self):
        name = rack_cols(self.rack)["name"]
        r = self.post(self.m, self.url(self.key, "/racks/save"),
                      {"id": self.rack, "name": "renamed", "rows": 4, "cols": 4, **GRID_NAMING})
        self.assertFlash(r, "Only an admin or whoever made", "error")
        self.post(self.m, self.url(self.key, f"/racks/{self.rack}/delete"))
        self.assertEqual(rack_cols(self.rack)["name"], name)
        own = self.make_stock_rack(self.m, self.key)
        self.assertEqual(one("select created_by from stock_racks where id=?", own), self.member)
        self.post(self.m, self.url(self.key, "/racks/save"),
                  {"id": own, "name": "mine renamed", "rows": 4, "cols": 4, **GRID_NAMING})
        self.assertEqual(rack_cols(own)["name"], "mine renamed")
        self.post(self.a, self.url(self.key, f"/racks/{own}/delete"))
        self.assertEqual(count("stock_racks", "id=?", own), 0)

    def test_deleting_a_rack_unplaces_what_was_in_it(self):
        self.post(self.a, self.url(self.key, f"/racks/{self.rack}/delete"))
        c = unit(self.cross)
        self.assertEqual((c["rack_id_fk"], c["rack_row"], c["active"]), (None, None, 1))

    def test_only_the_incubators_maker_or_an_admin_may_change_or_delete_it(self):
        inc = self.make_incubator(self.o, self.key, temperature="18")
        r = self.post(self.m, self.url(self.key, "/incubators/save"), {"id": inc, "name": "x", "temperature": "29"})
        self.assertFlash(r, "Only an admin or whoever added", "error")
        self.post(self.m, self.url(self.key, f"/incubators/{inc}/delete"))
        self.assertEqual(one("select temperature from stock_incubators where id=?", inc), "18")
        self.post(self.o, self.url(self.key, f"/incubators/{inc}/delete"))
        self.assertEqual(count("stock_incubators", "id=?", inc), 0)

    def test_deleting_an_incubator_keeps_its_racks_at_the_default_temperature(self):
        inc = self.make_incubator(self.a, self.key, temperature="29")
        rack = self.make_stock_rack(self.a, self.key, incubator_id=inc)
        r = self.post(self.a, self.url(self.key, f"/incubators/{inc}/delete"))
        self.assertFlash(r, "default 25 °C timings")
        self.assertIsNone(rack_cols(rack)["incubator_id_fk"])

    def test_a_rack_cannot_use_another_databases_incubator(self):
        other = self.make_stock_module(self.a, "fly")
        foreign = self.make_incubator(self.a, other, temperature="18")
        r = self.post(self.a, self.url(self.key, "/racks/save"),
                      {"id": self.rack, "name": rack_cols(self.rack)["name"], "rows": 4, "cols": 4,
                       "incubator_id": foreign, **GRID_NAMING})
        self.assertFlash(r, "belongs to another database", "error")
        self.assertEqual(rack_cols(self.rack)["incubator_id_fk"], self.inc)


# ---------------------------------------------------------------------------
# Bulk actions and undo
# ---------------------------------------------------------------------------


class BulkAndUndoTests(StockCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.make_stock_module(cls.a, "fly")
        cls.mid = cls.stock_module_id(cls.key)

    def my_last_batch(self):
        return one("select max(id) from batches where actor=?", self.admin)

    def test_bulk_set_purpose(self):
        ids = [self.make_vial(self.a, self.key) for _ in range(3)]
        r = self.bulk(self.a, self.key, "set", ids, field="purpose", value="backup")
        self.assertFlash(r, "Set purpose on 3 vials")
        self.assertEqual({unit(i)["purpose"] for i in ids}, {"backup"})

    def test_bulk_move_takes_the_next_free_cells_of_the_new_rack(self):
        src = self.make_stock_rack(self.a, self.key)
        dst = self.make_stock_rack(self.a, self.key)
        self.make_vial(self.a, self.key, rack_id=dst, position="A2")
        ids = [self.make_vial(self.a, self.key, rack_id=src) for _ in range(3)]
        self.bulk(self.a, self.key, "set", ids, field="rack_id", value=dst)
        self.assertEqual([(unit(i)["rack_id_fk"],) + cell(i) for i in ids],
                         [(dst, 1, 1), (dst, 1, 3), (dst, 1, 4)])

    def test_bulk_set_with_a_bad_value_changes_nothing(self):
        ids = [self.make_vial(self.a, self.key, set_up_on=days_ago(1)) for _ in range(2)]
        for field, value, message in (("set_up_on", "25/09/2026", "not a date"),
                                      ("owner", uniq("nobody"), "not a lab member")):
            with self.subTest(field=field):
                r = self.bulk(self.a, self.key, "set", ids, field=field, value=value)
                self.assertFlash(r, "Nothing changed", "error")
                self.assertFlash(r, message, "error")
        self.assertEqual({(unit(i)["set_up_on"], unit(i)["owner"]) for i in ids}, {(days_ago(1), self.admin)})

    def test_bulk_needs_a_selection_and_a_known_field_and_action(self):
        vial = self.make_vial(self.a, self.key)
        self.assertFlash(self.bulk(self.a, self.key, "discard", []), "No vials selected", "error")
        self.assertFlash(self.bulk(self.a, self.key, "set", [vial], field="active", value="0"), "Pick what to set",
                         "error")
        self.assertFlash(self.bulk(self.a, self.key, "explode", [vial]), "Unknown action", "error")
        self.assertEqual(unit(vial)["active"], 1)

    def test_bulk_discard_frees_cells_and_restore_places_them_again(self):
        rack = self.make_stock_rack(self.a, self.key)
        ids = [self.make_vial(self.a, self.key, rack_id=rack) for _ in range(3)]
        self.bulk(self.a, self.key, "discard", ids)
        self.assertEqual({(unit(i)["active"], unit(i)["rack_row"]) for i in ids}, {(0, None)})
        self.bulk(self.a, self.key, "restore", ids)
        self.assertEqual(sorted(cell(i) for i in ids), [(1, 1), (1, 2), (1, 3)])
        self.assertEqual({unit(i)["active"] for i in ids}, {1})

    def test_bulk_copy_duplicates_each_vial(self):
        cross = self.make_cross(self.a, self.key)
        before = top(self.mid)
        r = self.bulk(self.a, self.key, "copy", [cross])
        (copy,) = ids_after(self.mid, before)
        self.assertFlash(r, "Copied 1")
        self.assertEqual((unit(copy)["purpose"], unit(copy)["female_genotype"]),
                         ("cross", unit(cross)["female_genotype"]))

    def test_undo_of_a_bulk_collect_removes_the_progeny_and_the_collection_dates(self):
        rack = self.make_stock_rack(self.a, self.key)
        crosses = [self.make_cross(self.a, self.key, rack_id=rack) for _ in range(3)]
        before = top(self.mid)
        self.bulk(self.a, self.key, "collect", crosses)
        self.assertEqual(len(ids_after(self.mid, before)), 3)
        r = self.post(self.a, f"/batches/{self.my_last_batch()}/undo")
        self.assertFlash(r, "Undid", "success")
        self.assertEqual(ids_after(self.mid, before), [])
        self.assertEqual({unit(c)["last_collected_on"] for c in crosses}, {None})

    def test_undo_of_a_bulk_discard_puts_the_vials_back_in_their_cells(self):
        rack = self.make_stock_rack(self.a, self.key)
        ids = [self.make_vial(self.a, self.key, rack_id=rack) for _ in range(3)]
        cells = [cell(i) for i in ids]
        self.bulk(self.a, self.key, "discard", ids)
        self.post(self.a, f"/batches/{self.my_last_batch()}/undo")
        self.assertEqual([cell(i) for i in ids], cells)
        self.assertEqual({unit(i)["active"] for i in ids}, {1})

    def test_undo_of_creating_many_vials_removes_them(self):
        before = top(self.mid)
        self.create(self.a, self.key, count=3)
        batch = self.my_last_batch()
        self.assertEqual(one("select description from batches where id=?", batch), "New vials ×3")
        self.post(self.a, f"/batches/{batch}/undo")
        self.assertEqual(ids_after(self.mid, before), [])

    def test_only_whoever_ran_a_batch_or_an_admin_may_undo_it(self):
        ids = [self.make_vial(self.m, self.key) for _ in range(2)]
        self.bulk(self.m, self.key, "discard", ids)
        batch = one("select max(id) from batches where actor=?", self.member)
        r = self.post(self.o, f"/batches/{batch}/undo")
        self.assertFlash(r, "Only whoever ran a batch", "error")
        self.assertEqual({unit(i)["active"] for i in ids}, {0})


# ---------------------------------------------------------------------------
# Worms
# ---------------------------------------------------------------------------


class WormTests(StockCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.make_stock_module(cls.a, "worm", label=uniq("Worms "))
        cls.mid = cls.stock_module_id(cls.key)
        cls.inc15 = cls.make_incubator(cls, cls.a, cls.key, temperature="15")
        cls.inc25 = cls.make_incubator(cls, cls.a, cls.key, temperature="25.0")

    def box(self, rows=2, cols=3, inc=None, **kw):
        return self.make_stock_rack(self.a, self.key, rows=rows, cols=cols, incubator_id=inc or self.inc15, **kw)

    def lot(self, genotype):
        return one("select id from stock_frozen where module_id_fk=? and genotype=? order by id desc", self.mid, genotype)

    def save_lot(self, client, **fields):
        data = {"id": "", "genotype": uniq("CB61 "), "frozen_on": T, "vials": 3, "vials_left": 3, "location": "LN2"}
        data.update(fields)
        return self.post(client, self.url(self.key, "/frozen/save"), data)

    def test_sequential_positions_are_numbered_along_the_rows(self):
        box = self.box()
        _, ids = self.create(self.a, self.key, purpose="rnai", count=4, rack_id=box, position="3")
        self.assertEqual([cell(i) for i in ids], [(1, 3), (2, 1), (2, 2), (2, 3)])
        html = self.get_ok(self.a, self.url(self.key))
        self.assertIn(f'name="position" form="unit-{ids[0]}" value="3"', html)

    def test_sequential_position_beyond_the_box_is_refused(self):
        box = self.box()
        for pos in ("7", "0", "A1"):
            with self.subTest(pos=pos):
                r, ids = self.create(self.a, self.key, purpose="maintenance", rack_id=box, position=pos)
                self.assertEqual(ids, [])
                self.assertFlash(r, "is not a position", "error")

    def test_plates_are_coded_with_P_and_default_to_maintenance(self):
        r, ids = self.create(self.a, self.key, purpose=None)
        self.assertFlash(r, f"Created P{unit(ids[0])['number']}")
        self.assertEqual(unit(ids[0])["purpose"], "maintenance")
        r, ids = self.create(self.a, self.key, purpose="stock")  # a fly purpose
        self.assertEqual(ids, [])
        self.assertFlash(r, "not one of this database", "error")

    def test_chunking_uses_the_worm_verb_and_intervals(self):
        box = self.box(inc=self.inc25, last_flipped_on=T)
        r = self.post(self.a, self.url(self.key, f"/racks/{box}/flipped"))
        self.assertFlash(r, "chunk recorded")
        self.assertFlash(r, "next on " + fmt(TODAY + timedelta(days=4), "%a %d %b"))  # "25.0" → 25 °C: 4 d

    def test_schedule_lists_an_overdue_chunk_and_picking_progeny(self):
        box = self.box(rows=4, cols=6, last_flipped_on=days_ago(13))  # 15 °C: every 12 d
        cross = self.make_cross(self.a, self.key, rack_id=box, set_up_on=days_ago(3))
        html = self.get_ok(self.a, self.url(self.key, "?view=schedule"))
        self.assertIn(f"Chunk {rack_cols(box)['name']}</span>", html)
        self.assertIn(f"Pick progeny: P{unit(cross)['number']}</span>", html)

    def test_picking_progeny_at_15_degrees_is_due_in_6_days(self):
        cross = self.make_cross(self.a, self.key, rack_id=self.box(rows=4, cols=6))
        before = top(self.mid)
        r = self.action(self.a, self.key, cross, "collect")
        self.assertFlash(r, "Pick progeny: P")
        (progeny,) = ids_after(self.mid, before)
        self.assertEqual(unit(progeny)["ready_on"], days_ahead(6))

    def test_maintenance_plates_are_the_labs_but_crosses_are_not(self):
        plate = self.make_vial(self.o, self.key, purpose="maintenance")
        cross = self.make_cross(self.o, self.key)
        self.assertSaved(self.inline(self.m, self.key, plate, notes="chunked"))
        self.assertEqual(self.inline(self.m, self.key, cross, notes="x").status_code, 403)

    def test_frozen_lot_validation(self):
        for fields, message in (({"vials": 3, "vials_left": 9}, "more than the 3 frozen"),
                                ({"genotype": ""}, "needs its genotype"),
                                ({"frozen_on": "last week"}, "not a date"),
                                ({"owner": uniq("nobody")}, "not a lab member")):
            with self.subTest(fields=fields):
                geno = fields.get("genotype", uniq("bad "))
                r = self.save_lot(self.a, **{"genotype": geno, **fields})
                self.assertFlash(r, message, "error")
                if geno:
                    self.assertIsNone(self.lot(geno))

    def test_thawing_takes_a_vial_onto_a_new_plate(self):
        geno = uniq("N2 ")
        self.save_lot(self.a, genotype=geno, vials=4, vials_left=4)
        lot = self.lot(geno)
        before = top(self.mid)
        r = self.post(self.m, self.url(self.key, f"/frozen/{lot}/thaw"))
        self.assertFlash(r, "3 left")
        self.assertFlash(r, "has been told")      # someone else's lot: its keeper records the result
        self.assertEqual(one("select count(*) from notifications where recipient_username=? and title like ?",
                             self.admin, f"%thawed a vial of your {geno}%"), 1)
        self.assertEqual(one("select vials_left from stock_frozen where id=?", lot), 3)
        (plate,) = ids_after(self.mid, before)
        p = unit(plate)
        self.assertEqual((p["genotype"], p["purpose"], p["owner"]), (geno, "maintenance", self.member))

    def test_thawing_an_empty_lot_is_refused(self):
        geno = uniq("empty ")
        self.save_lot(self.a, genotype=geno, vials=2, vials_left=0)
        before = top(self.mid)
        r = self.post(self.a, self.url(self.key, f"/frozen/{self.lot(geno)}/thaw"))
        self.assertFlash(r, "No vials left", "error")
        self.assertEqual(top(self.mid), before)

    def test_a_thaw_test_is_recorded_on_the_lot(self):
        geno = uniq("CB1489 ")
        self.save_lot(self.a, genotype=geno)
        lot = self.lot(geno)
        self.save_lot(self.a, id=lot, genotype=geno, thaw_tested_on=T, thaw_ok="1")
        self.assertEqual(row("select thaw_tested_on, thaw_ok from stock_frozen where id=?", lot), (T, 1))
        self.save_lot(self.a, id=lot, genotype=geno, thaw_tested_on=T, thaw_ok="0")
        self.assertEqual(one("select thaw_ok from stock_frozen where id=?", lot), 0)
        self.assertIsNotNone(one("select id from stock_genotypes where module_id_fk=? and genotype=?", self.mid, geno))

    def test_member_cannot_change_or_delete_someone_elses_frozen_lot(self):
        geno = uniq("lot ")
        self.save_lot(self.o, genotype=geno)
        lot = self.lot(geno)
        r = self.save_lot(self.m, id=lot, genotype="changed")
        self.assertFlash(r, f"belongs to {self.other}", "error")
        self.post(self.m, self.url(self.key, f"/frozen/{lot}/delete"))
        self.assertEqual(one("select genotype from stock_frozen where id=?", lot), geno)
        self.post(self.o, self.url(self.key, f"/frozen/{lot}/delete"))
        self.assertEqual(count("stock_frozen", "id=?", lot), 0)

    def test_frozen_view_renders_with_its_lots(self):
        geno = uniq("frozen view ")
        self.save_lot(self.a, genotype=geno)
        self.assertIn(geno, self.get_ok(self.a, self.url(self.key, "?view=frozen")))


# ---------------------------------------------------------------------------
# Settings, deleting a database, pages
# ---------------------------------------------------------------------------


class SettingsAndModuleTests(StockCase):
    def settings_form(self, key, **overrides):
        s = json.loads(one("select settings from stock_modules where key=?", key))
        data = {"label": one("select label from stock_modules where key=?", key), "blurb": "", "enabled": ["0", "1"],
                "purposes": "\n".join(f"{p['key']} = {p['label']}" for p in s["purposes"]),
                "temp_count": len(s["temperatures"]), "default_temperature": s["default_temperature"]}
        for i, t in enumerate(s["temperatures"]):
            data.update({f"temp_{i}": t["temp"], f"temp_{i}_flip": t["flip"], f"temp_{i}_develop": t["develop"],
                         f"temp_{i}_collect": t["collect"]})
        data.update(overrides)
        return data

    def stored(self, key):
        return json.loads(one("select settings from stock_modules where key=?", key))

    def test_creating_a_database_needs_a_name(self):
        r = self.post(self.a, "/stocks/new", {"kind": "fly", "label": "  "})
        self.assertFlash(r, "Give the database a name", "error")

    def test_new_database_takes_its_kinds_preset_and_records_its_creator(self):
        key = self.make_stock_module(self.m, "worm", label=uniq("Worms "))
        kind, created_by = row("select kind, created_by from stock_modules where key=?", key)
        self.assertEqual((kind, created_by), ("worm", self.member))
        s = self.stored(key)
        self.assertEqual((s["code_prefix"], s["flip_verb"], s["frozen"]), ("P", "Chunk", True))

    def test_settings_save_changes_name_intervals_and_purposes(self):
        key = self.make_stock_module(self.a, "fly")
        form = self.settings_form(key, label=uniq("Fly room "),
                                  purposes="stock = Stock\nbalancer stock = Balancer stock\nexperiment")
        i25 = next(i for i in range(form["temp_count"]) if form[f"temp_{i}"] == "25")
        form[f"temp_{i25}_flip"] = 12
        r = self.post(self.a, self.url(key, "/settings"), form)
        self.assertFlash(r, "Saved", "success")
        key = one("select key from stock_modules where label=?", form["label"])   # its address follows the name
        s = self.stored(key)
        self.assertEqual([p["key"] for p in s["purposes"]], ["stock", "balancer_stock", "experiment"])
        # Cross and progeny drive behaviour, so they stay usable when left out.
        cross = self.make_cross(self.a, key)
        self.assertFlash(self.action(self.a, key, cross, "collect"), "Collect eggs", "success")
        rack = self.make_stock_rack(self.a, key, incubator_id=self.make_incubator(self.a, key, temperature="25"))
        self.assertRegex(self.get_ok(self.a, self.url(key, "?view=setup")),
                         re.escape(rack_cols(rack)["name"]) + r"</td>(?s:.*?)<td>12 d")

    def test_removing_the_default_temperature_resets_the_default(self):
        key = self.make_stock_module(self.a, "fly")
        form = self.settings_form(key, default_temperature="25")
        for i in range(form["temp_count"]):
            if form[f"temp_{i}"] in ("25", "29"):
                form[f"temp_{i}_remove"] = "1"
        self.post(self.a, self.url(key, "/settings"), form)
        s = self.stored(key)
        self.assertEqual([t["temp"] for t in s["temperatures"]], ["18", "22"])
        self.assertEqual(s["default_temperature"], "18")

    def test_a_removed_purpose_stays_on_existing_vials(self):
        key = self.make_stock_module(self.a, "fly")
        vial = self.make_vial(self.a, key, purpose="backup")
        self.post(self.a, self.url(key, "/settings"), self.settings_form(key, purposes="stock\nexperiment"))
        self.assertSaved(self.inline(self.a, key, vial, purpose="backup", notes="still a backup"))
        self.assertEqual(unit(vial)["purpose"], "backup")

    def test_member_cannot_change_someone_elses_database_settings(self):
        key = self.make_stock_module(self.a, "fly")
        label = one("select label from stock_modules where key=?", key)
        r = self.post(self.m, self.url(key, "/settings"), self.settings_form(key, label="hacked"))
        self.assertFlash(r, "Only an admin, or whoever created this database", "error")
        self.assertEqual(one("select label from stock_modules where key=?", key), label)

    def test_creator_may_change_their_own_databases_settings(self):
        key = self.make_stock_module(self.m, "fly")
        mid = one("select id from stock_modules where key=?", key)
        self.post(self.m, self.url(key, "/settings"), self.settings_form(key, label="my flies", code_prefix="F"))
        self.assertEqual(one("select label from stock_modules where id=?", mid), "my flies")
        self.assertFlash(self.create(self.m, key)[0], "Created F1")

    def test_delete_needs_the_typed_name_and_the_right_person(self):
        key = self.make_stock_module(self.a, "fly")
        label = one("select label from stock_modules where key=?", key)
        r = self.post(self.a, self.url(key, "/delete"), {"confirm": "wrong"})
        self.assertFlash(r, "to confirm", "error")
        r = self.post(self.m, self.url(key, "/delete"), {"confirm": label})
        self.assertFlash(r, "Only an admin, or whoever created it", "error")
        self.assertEqual(count("stock_modules", "key=?", key), 1)

    def test_deleting_a_database_removes_everything_in_it(self):
        key = self.make_stock_module(self.m, "worm", label=uniq("Worms "))
        mid = self.stock_module_id(key)
        inc = self.make_incubator(self.m, key, temperature="20")
        box = self.make_stock_rack(self.m, key, incubator_id=inc)
        cross = self.make_cross(self.m, key, rack_id=box)
        self.action(self.m, key, cross, "collect")
        self.post(self.m, self.url(key, "/frozen/save"), {"genotype": "N2", "vials": 1, "vials_left": 1})
        r = self.post(self.m, self.url(key, "/delete"), {"confirm": one("select label from stock_modules where id=?", mid)})
        self.assertFlash(r, "and everything in it", "success")
        for table in ("stock_units", "stock_frozen", "stock_genotypes", "stock_racks", "stock_incubators"):
            with self.subTest(table=table):
                self.assertEqual(count(table, "module_id_fk=?", mid), 0)
        self.assertEqual(count("stock_modules", "id=?", mid), 0)

    def test_every_view_renders_for_fly_and_worm(self):
        for kind in ("fly", "worm"):
            key = self.make_stock_module(self.a, kind)
            inc = self.make_incubator(self.a, key)
            rack = self.make_stock_rack(self.a, key, incubator_id=inc, last_flipped_on=days_ago(40))
            cross = self.make_cross(self.a, key, rack_id=rack, set_up_on=days_ago(4))
            self.action(self.a, key, cross, "collect")
            self.make_vial(self.a, key, purpose="experiment", rack_id=rack, shift_on=T, shift_to="29", score_on=T)
            for view in ("units", "schedule", "genotypes", "setup", "frozen", "settings", "nonsense"):
                with self.subTest(kind=kind, view=view):
                    self.get_ok(self.m, self.url(key, f"?view={view}"))
        self.get_ok(self.a, "/stocks/new?kind=worm")

    def test_fly_database_has_no_frozen_view(self):
        key = self.make_stock_module(self.a, "fly")
        html = self.get_ok(self.a, self.url(key, "?view=frozen"))
        self.assertIn(" · Vials · BioManager</title>", html)  # falls back to the vials tab

    def test_unknown_database_is_not_found_and_logged_out_users_are_sent_to_login(self):
        self.assertEqual(self.a.get("/stocks/" + uniq("nope")).status_code, 404)
        r = app.test_client().get("/stocks/drosophila")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])


if __name__ == "__main__":
    unittest.main()
