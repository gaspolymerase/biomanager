"""Zebrafish database: lines, racks, water systems, tanks, fish rows,
clutches, the mating wizard and Returned, the sac log, bulk actions with
their undo, and who may change what."""
from __future__ import annotations

import re
import unittest

from tests.base import *  # noqa: F401,F403
from tests.base import (AppTestCase, GRID_NAMING, T, TODAY, count, days_ago, days_ahead, execute,
                        last_batch, one, row, rows, uniq)

MISSING = "999999999"  # a row id nobody has


# ---------------------------------------------------------------- helpers

def make_rack(client, name=None, rows_=3, cols=4, **fields) -> int:
    name = name or uniq("ZR")
    client.post("/zebrafish/racks/create", data={"name": name, "rows": str(rows_), "cols": str(cols),
                                                 **GRID_NAMING, **fields})
    found = one("select id from fish_racks where name=?", name)
    assert found, f"rack {name} was not created"
    return found


def make_system(client, name=None, **fields) -> int:
    name = name or uniq("Sys")
    client.post("/zebrafish/systems/create", data={"name": name, **fields})
    found = one("select id from water_systems where name=?", name)
    assert found, f"water system {name} was not created"
    return found


def make_fish(client, tank: int, n: int = 5, **fields) -> int:
    before = one("select coalesce(max(id), 0) from fish")
    client.post("/zebrafish/fish/create", data={"tank_id_fk": str(tank), "count": str(n), **fields})
    found = one("select max(id) from fish where tank_id_fk=? and id>?", tank, before)
    assert found, "fish row was not created"
    return found


def make_clutch(client, code=None, **fields) -> int:
    code = code or uniq("CL")
    client.post("/zebrafish/clutches/create", data={"clutch_id": code, **fields})
    found = one("select id from clutches where clutch_id=?", code)
    assert found, f"clutch {code} was not created"
    return found


def tank_code(tank: int) -> str:
    return one("select tank_id from tanks where id=?", tank)


def cell(tank: int):
    return row("select rack_id_fk, row, col from tanks where id=?", tank)


def undo(client, batch_id: int):
    return client.post(f"/batches/{batch_id}/undo", follow_redirects=True)


def tank_fish_total(client, tank: int) -> int:
    """The live-fish total the tanks sheet shows for a tank."""
    html = client.get("/zebrafish?view=tanks").get_data(as_text=True)
    m = re.search(r'<tr id="tank-%d"[^>]*?data-fish="(\d+)"' % tank, html, re.S)
    assert m, f"tank {tank} is not on the sheet"
    return int(m.group(1))


def sheet_row(html: str, pattern: str) -> str:
    m = re.search(pattern + r".*?</tr>", html, re.S)
    assert m, f"no row matching {pattern}"
    return m.group(0)


def mating_tank_of(father: int, mother: int):
    return one("select id from tanks where purpose='mating' and mating_father_tank_id=? and "
               "mating_mother_tank_id=? order by id desc limit 1", father, mother)


# ================================================================ pages

class ZebrafishPageTests(AppTestCase):

    def test_every_view_renders_for_a_member(self):
        for view in ("tanks", "fish", "clutches", "lines", "water"):
            with self.subTest(view=view):
                self.get_ok(self.m, f"/zebrafish?view={view}")
        for mode in ("grid", "geno", "table"):
            with self.subTest(mode=mode):
                self.get_ok(self.m, f"/zebrafish?view=tanks&mode={mode}")

    def test_old_view_names_redirect_to_the_tanks_modes(self):
        for alias, mode in (("racks", "grid"), ("genotyping", "geno"), ("sac", "table")):
            with self.subTest(alias=alias):
                r = self.m.get(f"/zebrafish?view={alias}")
                self.assertEqual(r.status_code, 302)
                self.assertIn(f"mode={mode}", r.headers["Location"])

    def test_unknown_view_falls_back_to_tanks(self):
        self.assertIn("zf-tanks-v2", self.get_ok(self.m, "/zebrafish?view=zzz"))


# ================================================================ lines

class LineTests(AppTestCase):

    def test_new_line_belongs_to_its_creator_and_keeps_its_parent(self):
        parent = self.make_line(self.a, zfin_name="Tg(elavl3:EGFP)", background="AB")
        child = self.make_line(self.m, parent_line_id_fk=str(parent))
        self.assertEqual(row("select parent_line_id_fk, owner from fish_lines where id=?", child),
                         (parent, self.member))
        self.assertEqual(one("select owner from fish_lines where id=?", parent), self.admin)

    def test_new_line_can_name_another_owner(self):
        line = self.make_line(self.a, owner=self.member)
        self.assertEqual(one("select owner from fish_lines where id=?", line), self.member)

    def test_duplicate_or_blank_line_name_is_refused(self):
        name = uniq("line")
        self.make_line(self.a, name)
        r = self.post(self.a, "/zebrafish/lines/create", {"name": name})
        self.assertFlash(r, "already used", "error")
        self.assertEqual(count("fish_lines", "name=?", name), 1)
        r = self.post(self.a, "/zebrafish/lines/create", {"name": "   "})
        self.assertFlash(r, "needs a name", "error")

    def test_line_cannot_descend_from_itself_or_its_child(self):
        parent = self.make_line(self.a)
        child = self.make_line(self.a, parent_line_id_fk=str(parent))
        r = self.autosave(self.a, f"/zebrafish/lines/{parent}/update", {"parent_line_id_fk": str(child)})
        self.assertRefused(r)
        self.assertIn("descend", r.get_json()["error"])
        self.assertRefused(self.autosave(self.a, f"/zebrafish/lines/{parent}/update",
                                         {"parent_line_id_fk": str(parent)}))
        self.assertIsNone(one("select parent_line_id_fk from fish_lines where id=?", parent))

    def test_line_rename_to_blank_or_taken_name_is_refused(self):
        taken = uniq("line")
        self.make_line(self.a, taken)
        line = self.make_line(self.a)
        name = one("select name from fish_lines where id=?", line)
        self.assertRefused(self.autosave(self.a, f"/zebrafish/lines/{line}/update", {"name": ""}))
        self.assertRefused(self.autosave(self.a, f"/zebrafish/lines/{line}/update", {"name": taken}))
        self.assertEqual(one("select name from fish_lines where id=?", line), name)

    def test_line_detail_survives_a_legacy_lineage_cycle(self):
        a = self.make_line(self.a)
        b = self.make_line(self.a, parent_line_id_fk=str(a))
        execute("update fish_lines set parent_line_id_fk=? where id=?", b, a)  # written before the check
        html = self.get_ok(self.a, f"/zebrafish/lines/{a}")
        self.assertLessEqual(html.count('class="lineage-node'), 4)

    def test_line_save_redirects_to_zebrafish_or_back_to_the_detail_page(self):
        line = self.make_line(self.a)
        r = self.a.post(f"/zebrafish/lines/{line}/update", data={"notes": "via dialog"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(location(r).startswith("/zebrafish"), location(r))
        r = self.a.post(f"/zebrafish/lines/{line}/update", data={"notes": "via detail"},
                        headers={"Referer": f"http://localhost/zebrafish/lines/{line}"})
        self.assertEqual(location(r), f"/zebrafish/lines/{line}")
        self.assertEqual(one("select notes from fish_lines where id=?", line), "via detail")

    def test_duplicating_a_line_names_the_copy_and_gives_it_to_the_copier(self):
        name = uniq("line")
        line = self.make_line(self.a, name, background="TU")
        self.post(self.m, f"/zebrafish/lines/{line}/duplicate")
        self.post(self.m, f"/zebrafish/lines/{line}/duplicate")
        self.assertEqual(row("select owner, background from fish_lines where name=?", f"{name} copy"),
                         (self.member, "TU"))
        self.assertEqual(count("fish_lines", "name=?", f"{name} copy 2"), 1)

    def test_line_still_in_use_cannot_be_deleted(self):
        line = self.make_line(self.a)
        self.make_tank(self.a, line_id_fk=str(line))
        r = self.post(self.a, f"/zebrafish/lines/{line}/delete")
        self.assertFlash(r, "still used by 1 tank", "error")
        self.assertEqual(count("fish_lines", "id=?", line), 1)

    def test_deleting_an_unused_line_keeps_its_sac_log_entries_unlinked(self):
        line = self.make_line(self.a)
        reason = uniq("reason")
        self.post(self.a, "/zebrafish/sac/create", {"line_id_fk": str(line), "count": "1", "reason": reason})
        r = self.post(self.a, f"/zebrafish/lines/{line}/delete")
        self.assertFlash(r, "Deleted line", "success")
        self.assertEqual(count("fish_lines", "id=?", line), 0)
        self.assertEqual(rows("select line_id_fk from fish_sac_log where reason=?", reason), [(None,)])

    def test_member_cannot_edit_or_delete_someone_elses_line(self):
        line = self.make_line(self.a)
        r = self.autosave(self.m, f"/zebrafish/lines/{line}/update", {"notes": "member"})
        self.assertEqual(r.status_code, 403)
        r = self.post(self.m, f"/zebrafish/lines/{line}/delete")
        self.assertFlash(r, "belongs", "error")
        self.assertEqual(row("select notes, 1 from fish_lines where id=?", line), ("", 1))

    def test_unowned_legacy_line_is_open_to_everyone(self):
        line = self.make_line(self.a)
        execute("update fish_lines set owner='' where id=?", line)
        self.assertSaved(self.autosave(self.m, f"/zebrafish/lines/{line}/update", {"notes": "open"}))
        self.assertEqual(one("select notes from fish_lines where id=?", line), "open")

    def test_lines_sheet_locks_someone_elses_line(self):
        theirs = self.make_line(self.a)
        mine = self.make_line(self.m)
        html = self.get_ok(self.m, "/zebrafish?view=lines")
        locked = sheet_row(html, r'<tr data-id="%d"' % theirs)
        self.assertIn("sheet-lock", locked)
        self.assertIn("disabled", locked)
        self.assertNotIn("sheet-lock", sheet_row(html, r'<tr data-id="%d"' % mine))

    def test_member_bulk_owner_skips_lines_they_do_not_own(self):
        theirs = self.make_line(self.a)
        mine = self.make_line(self.m)
        r = self.post(self.m, "/zebrafish/lines/bulk", {"action": "owner", "value": self.other,
                                                       "selected_ids": [theirs, mine]})
        self.assertFlash(r, "1 skipped", "success")
        self.assertEqual(one("select owner from fish_lines where id=?", theirs), self.admin)
        self.assertEqual(one("select owner from fish_lines where id=?", mine), self.other)

    def test_bulk_line_owner_can_be_undone(self):
        a, b = self.make_line(self.a), self.make_line(self.a)
        execute("update fish_lines set owner='' where id=?", b)
        r = self.post(self.a, "/zebrafish/lines/bulk", {"action": "owner", "value": self.member,
                                                       "selected_ids": [a, b]})
        self.assertFlash(r, "Set owner: 2 lines", "success")
        undo(self.a, last_batch()[0])
        self.assertEqual(rows("select owner from fish_lines where id in (?, ?) order by id", a, b),
                         [(self.admin,), ("",)])

    def test_bulk_line_delete_keeps_used_lines_and_undo_restores_the_rest(self):
        used, free = self.make_line(self.a), self.make_line(self.a)
        self.make_tank(self.a, line_id_fk=str(used))
        r = self.post(self.a, "/zebrafish/lines/bulk", {"action": "delete", "selected_ids": [used, free]})
        self.assertFlash(r, "still used", "error")
        self.assertEqual((count("fish_lines", "id=?", used), count("fish_lines", "id=?", free)), (1, 0))
        undo(self.a, last_batch()[0])
        self.assertEqual(count("fish_lines", "id=?", free), 1)


# ================================================================ racks + water

class RackTests(AppTestCase):

    def test_rack_names_are_unique(self):
        name = uniq("ZR")
        make_rack(self.a, name)
        r = self.post(self.a, "/zebrafish/racks/create", {"name": name, "rows": "3", "cols": "4", **GRID_NAMING})
        self.assertFlash(r, "already a rack", "error")
        self.assertEqual(count("fish_racks", "name=?", name), 1)

    def test_rack_rows_must_be_a_whole_number(self):
        name = uniq("ZR")
        r = self.post(self.a, "/zebrafish/racks/create", {"name": name, "rows": "abc", "cols": "4", **GRID_NAMING})
        self.assertFlash(r, "whole number", "error")
        self.assertEqual(count("fish_racks", "name=?", name), 0)
        rack = make_rack(self.a, rows_=3)
        r = self.post(self.a, f"/zebrafish/racks/{rack}/update", {"rows": "x", "cols": "4"})
        self.assertFlash(r, "whole number", "error")
        self.assertEqual(one("select rows from fish_racks where id=?", rack), 3)

    def test_rack_size_is_clamped_to_what_the_grid_draws(self):
        rack = make_rack(self.a, rows_=0, cols=500)
        self.assertEqual(row("select rows, cols from fish_racks where id=?", rack), (1, 40))

    def test_deleting_a_rack_unplaces_its_tanks(self):
        rack = make_rack(self.a)
        tank = self.make_tank(self.a, rack_id_fk=str(rack), position="B2")
        self.post(self.a, f"/zebrafish/racks/{rack}/delete")
        self.assertEqual(count("fish_racks", "id=?", rack), 0)
        self.assertEqual(cell(tank), (None, None, None))

    def test_member_cannot_delete_a_rack_holding_someone_elses_tank(self):
        rack = make_rack(self.m)
        self.make_tank(self.a, rack_id_fk=str(rack), position="A1")
        r = self.post(self.m, f"/zebrafish/racks/{rack}/delete")
        self.assertFlash(r, "holds tanks you can", "error")
        self.assertEqual(count("fish_racks", "id=?", rack), 1)

    def test_a_rack_is_changed_by_whoever_added_it_or_an_admin(self):
        """As for mouse and fly racks: renaming or resizing moves every tank
        in it, so it isn't for any member."""
        rack = make_rack(self.a)
        new_name = uniq("ZR")
        r = self.post(self.m, f"/zebrafish/racks/{rack}/update", {"name": new_name, "rows": "5", "cols": "6"})
        self.assertFlash(r, "Only whoever added rack", "error")
        self.assertNotEqual(one("select name from fish_racks where id=?", rack), new_name)
        self.assertFlash(self.post(self.m, f"/zebrafish/racks/{rack}/delete"), "Only whoever added rack", "error")
        mine = make_rack(self.m)
        r = self.post(self.m, f"/zebrafish/racks/{mine}/update", {"name": new_name, "rows": "5", "cols": "6"})
        self.assertFlash(r, "Saved rack", "success")
        self.assertEqual(row("select name, rows, cols from fish_racks where id=?", mine), (new_name, 5, 6))


class WaterTests(AppTestCase):

    def test_new_water_system_records_its_creator_and_refuses_duplicates(self):
        name = uniq("Sys")
        system = make_system(self.m, name, room="B1", target_temp_c="28.5")
        self.assertEqual(row("select created_by, target_temp_c from water_systems where id=?", system),
                         (self.member, 28.5))
        r = self.post(self.a, "/zebrafish/systems/create", {"name": name})
        self.assertFlash(r, "already a water system", "error")

    def test_water_system_target_must_be_a_number(self):
        name = uniq("Sys")
        r = self.post(self.a, "/zebrafish/systems/create", {"name": name, "target_ph": "seven"})
        self.assertFlash(r, "must be a number", "error")
        self.assertEqual(count("water_systems", "name=?", name), 0)

    def test_water_reading_validation(self):
        system = make_system(self.a)
        before = count("water_logs")
        cases = [({"system_id_fk": str(system)}, "at least one"),
                 ({"system_id_fk": str(system), "ph": "abc"}, "must be a number"),
                 ({"system_id_fk": MISSING, "ph": "7"}, "no longer exists"),
                 ({"ph": "7"}, "Pick the water system")]
        for data, message in cases:
            with self.subTest(data=data):
                self.assertFlash(self.post(self.a, "/zebrafish/water/log", data), message, "error")
        self.assertEqual(count("water_logs"), before)

    def test_partial_reading_shows_on_the_system_card_and_in_the_series(self):
        name = uniq("Sys")
        system = make_system(self.a, name)
        other = make_system(self.a)
        self.post(self.a, "/zebrafish/water/log", {"system_id_fk": str(system), "ph": "7.1", "alarm": "on"})
        self.post(self.a, "/zebrafish/water/log", {"system_id_fk": str(other), "ph": "6.5"})
        html = self.get_ok(self.a, "/zebrafish?view=water")
        card = re.search(re.escape(name + " ") + r".*?</section>", html, re.S).group(0)
        self.assertIn("pH 7.1", card)
        self.assertNotIn("None", card)
        data = self.a.get(f"/zebrafish/water/{system}/series.json").get_json()
        self.assertTrue(data["ok"])
        self.assertEqual([(p["ph"], p["alarm"], p["temp"]) for p in data["series"]], [(7.1, True, None)])

    def test_water_system_used_by_a_rack_cannot_be_deleted(self):
        system = make_system(self.a)
        make_rack(self.a, system_id_fk=str(system))
        r = self.post(self.a, f"/zebrafish/systems/{system}/delete")
        self.assertFlash(r, "still used by 1 rack", "error")
        self.assertEqual(count("water_systems", "id=?", system), 1)

    def test_deleting_a_water_system_keeps_its_readings_and_can_be_undone(self):
        system = make_system(self.a)
        note = uniq("reading")
        self.post(self.a, "/zebrafish/water/log", {"system_id_fk": str(system), "ph": "7.3", "notes": note})
        r = self.post(self.a, f"/zebrafish/systems/{system}/delete")
        self.assertFlash(r, "Deleted water system", "success")
        self.assertEqual(row("select system_id_fk, ph from water_logs where notes=?", note), (None, 7.3))
        self.assertIn("Readings from deleted systems", self.get_ok(self.a, "/zebrafish?view=water"))
        undo(self.a, last_batch()[0])
        self.assertEqual(count("water_systems", "id=?", system), 1)

    def test_member_deletes_own_water_system_but_not_an_admins(self):
        theirs = make_system(self.a)
        mine = make_system(self.m)
        self.assertTrue(errors(self.post(self.m, f"/zebrafish/systems/{theirs}/delete")))
        self.assertEqual(count("water_systems", "id=?", theirs), 1)
        self.assertFlash(self.post(self.m, f"/zebrafish/systems/{mine}/delete"), "Deleted", "success")
        self.assertEqual(count("water_systems", "id=?", mine), 0)

    def test_water_system_from_before_creators_is_admin_only(self):
        system = make_system(self.m)
        execute("update water_systems set created_by='' where id=?", system)
        r = self.post(self.m, f"/zebrafish/systems/{system}/delete")
        self.assertFlash(r, "Only an admin", "error")
        self.assertEqual(count("water_systems", "id=?", system), 1)
        self.post(self.a, f"/zebrafish/systems/{system}/delete")
        self.assertEqual(count("water_systems", "id=?", system), 0)


# ================================================================ tanks

class TankCreateTests(AppTestCase):

    def test_tank_is_placed_at_the_typed_position(self):
        rack = make_rack(self.a)
        tank = self.make_tank(self.a, purpose="stock", rack_id_fk=str(rack), position="B2")
        self.assertEqual(cell(tank), (rack, 1, 1))
        self.assertEqual(one("select owner from tanks where id=?", tank), self.admin)

    def test_tank_on_an_occupied_cell_is_created_but_not_placed(self):
        rack = make_rack(self.a)
        self.make_tank(self.a, rack_id_fk=str(rack), position="A1")
        code = uniq("TK")
        r = self.post(self.a, "/zebrafish/tanks/create", {"tank_id": code, "rack_id_fk": str(rack), "position": "A1"})
        self.assertFlash(r, "not placed", "error")
        self.assertEqual(row("select rack_id_fk, row from tanks where tank_id=?", code), (None, None))

    def test_duplicate_tank_id_is_refused(self):
        code = uniq("TK")
        self.make_tank(self.a, code)
        self.assertFlash(self.post(self.a, "/zebrafish/tanks/create", {"tank_id": code}), "already used", "error")
        self.assertEqual(count("tanks", "tank_id=?", code), 1)

    def test_bad_tank_fields_are_refused_and_nothing_is_made(self):
        rack = make_rack(self.a, rows_=3, cols=4)
        cases = [({"rack_id_fk": str(rack), "row": "x", "col": "1"}, "whole number"),
                 ({"rack_id_fk": str(rack), "row": "9", "col": "0"}, "outside"),
                 ({"rack_id_fk": MISSING}, "no longer exists"),
                 ({"line_id_fk": MISSING}, "no longer exists"),
                 ({"purpose": "zzz"}, "Purpose must be one of")]
        for data, message in cases:
            with self.subTest(data=data):
                code = uniq("TK")
                r = self.post(self.a, "/zebrafish/tanks/create", {"tank_id": code, **data})
                self.assertFlash(r, message, "error")
                self.assertEqual(count("tanks", "tank_id=?", code), 0)

    def test_zero_based_row_and_column_stand_in_for_a_position(self):
        rack = make_rack(self.a)
        tank = self.make_tank(self.a, rack_id_fk=str(rack), row="0", col="2")
        self.assertEqual(cell(tank), (rack, 0, 2))

    def test_blank_tank_id_gets_the_next_t_code(self):
        before = one("select coalesce(max(id), 0) from tanks")
        self.post(self.a, "/zebrafish/tanks/create", {"tank_id": "", "purpose": "stock"})
        code = one("select tank_id from tanks where id>?", before)
        self.assertRegex(code, r"^T\d{3,}$")
        numbers = [int(c[1:]) for (c,) in rows("select tank_id from tanks where id<=?", before)
                   if re.fullmatch(r"T\d+", c)]
        self.assertGreater(int(code[1:]), max(numbers, default=0))

    def test_how_many_makes_consecutive_tanks_side_by_side_in_one_batch(self):
        rack_name = uniq("ZR")
        rack = make_rack(self.a, rack_name, rows_=2, cols=3)
        line = self.make_line(self.a)
        prefix = uniq("Q") + "n"
        r = self.post(self.a, "/zebrafish/tanks/create", {
            "tank_id": f"{prefix}010", "how_many": "4", "purpose": "stock", "line_id_fk": str(line),
            "rack_id_fk": str(rack), "position": "A2", "notes": "batch"})
        made = rows("select tank_id, row, col, line_id_fk, notes from tanks where tank_id like ? order by tank_id",
                    prefix + "%")
        self.assertEqual([m[0] for m in made], [f"{prefix}0{n}" for n in (10, 11, 12, 13)])
        self.assertEqual([(m[1], m[2]) for m in made], [(0, 1), (0, 2), (1, 0), (1, 1)])
        self.assertTrue(all(m[3] == line and m[4] == "batch" for m in made))
        self.assertFlash(r, f"Created 4 tanks, {prefix}010–{prefix}013, in {rack_name} from A2 to B2", "success")
        batch_id, description, _, _ = last_batch()
        self.assertTrue(description.startswith("new tanks ×4"), description)
        self.assertEqual(one("select record_count from batches where id=?", batch_id), 4)
        undo(self.a, batch_id)
        self.assertEqual(count("tanks", "tank_id like ?", prefix + "%"), 0)

    def test_how_many_leaves_the_tanks_that_do_not_fit_unplaced(self):
        rack = make_rack(self.a, rows_=1, cols=2)
        prefix = uniq("Q") + "n"
        r = self.post(self.a, "/zebrafish/tanks/create", {"tank_id": f"{prefix}1", "how_many": "4",
                                                          "rack_id_fk": str(rack), "position": "A1"})
        placed = rows("select tank_id, row from tanks where tank_id like ? order by tank_id", prefix + "%")
        self.assertEqual([p[1] for p in placed], [0, 0, None, None])
        self.assertFlash(r, f"had room for 2; {prefix}3–{prefix}4 are not placed", "warning")

    def test_how_many_refuses_bad_runs_and_makes_nothing(self):
        taken = uniq("Q") + "n012"
        self.make_tank(self.a, taken)
        rack = make_rack(self.a)
        before = count("tanks")
        cases = [({"tank_id": taken[:-3] + "010", "how_many": "3"}, "already used"),
                 ({"tank_id": uniq("abc") + "x", "how_many": "3"}, "ends in a number"),
                 ({"how_many": "31"}, "more than 30"),
                 ({"how_many": "0"}, "less than 1"),
                 ({"how_many": "x"}, "whole number"),
                 ({"tank_id": uniq("Q") + "n1", "how_many": "2", "rack_id_fk": str(rack), "position": "Z9"},
                  "not a position")]
        for data, message in cases:
            with self.subTest(data=data):
                self.assertFlash(self.post(self.a, "/zebrafish/tanks/create", data), message, "error")
        self.assertEqual(count("tanks"), before)

    def test_how_many_with_blank_id_takes_consecutive_t_codes(self):
        before = one("select coalesce(max(id), 0) from tanks")
        self.post(self.a, "/zebrafish/tanks/create", {"tank_id": "", "how_many": "3"})
        codes = [c for (c,) in rows("select tank_id from tanks where id>? order by id", before)]
        self.assertEqual(len(codes), 3)
        nums = [int(c[1:]) for c in codes]
        self.assertEqual(nums, list(range(nums[0], nums[0] + 3)), codes)

    def test_form_saves_without_a_referer_go_to_zebrafish_not_colony(self):
        r = self.a.post("/zebrafish/tanks/create", data={"tank_id": uniq("TK")})
        self.assertTrue(location(r).startswith("/zebrafish"), location(r))
        tank = self.make_tank(self.a)
        r = self.a.post(f"/zebrafish/tanks/{tank}/update", data={"notes": "dialog save"})
        self.assertTrue(location(r).startswith("/zebrafish"), location(r))


class TankEditTests(AppTestCase):

    def test_inline_rename_to_a_taken_or_blank_id_is_refused(self):
        taken = uniq("TK")
        self.make_tank(self.a, taken)
        code = uniq("TK")
        tank = self.make_tank(self.a, code)
        self.assertRefused(self.autosave(self.a, f"/zebrafish/tanks/{tank}/update", {"tank_id": taken}))
        self.assertRefused(self.autosave(self.a, f"/zebrafish/tanks/{tank}/update", {"tank_id": "  "}))
        self.assertEqual(tank_code(tank), code)

    def test_inline_purpose_outside_the_list_is_refused_but_a_legacy_one_is_kept(self):
        tank = self.make_tank(self.a, purpose="stock")
        self.assertRefused(self.autosave(self.a, f"/zebrafish/tanks/{tank}/update", {"purpose": "zzz"}))
        self.assertEqual(one("select purpose from tanks where id=?", tank), "stock")
        execute("update tanks set purpose='legacy-x' where id=?", tank)
        self.assertSaved(self.autosave(self.a, f"/zebrafish/tanks/{tank}/update",
                                       {"purpose": "legacy-x", "notes": "kept"}))
        self.assertEqual(row("select purpose, notes from tanks where id=?", tank), ("legacy-x", "kept"))

    def test_choosing_no_rack_unplaces_even_with_the_old_position_posted(self):
        rack = make_rack(self.a)
        tank = self.make_tank(self.a, rack_id_fk=str(rack), position="B2")
        r = self.autosave(self.a, f"/zebrafish/tanks/{tank}/update", {
            "rack_id_fk": "", "rack_id_fk_was": str(rack), "position": "B2", "position_was": "B2", "notes": ""})
        self.assertSaved(r)
        self.assertEqual(cell(tank), (None, None, None))
        self.assertEqual(r.get_json()["row"]["values"]["position"], "")

    def test_choosing_a_rack_takes_its_first_free_cell(self):
        rack = make_rack(self.a)
        self.make_tank(self.a, rack_id_fk=str(rack), position="A1")
        tank = self.make_tank(self.a)
        r = self.autosave(self.a, f"/zebrafish/tanks/{tank}/update", {
            "rack_id_fk": str(rack), "rack_id_fk_was": "", "position": "", "position_was": ""})
        self.assertSaved(r)
        self.assertEqual(cell(tank), (rack, 0, 1))
        self.assertEqual(r.get_json()["row"]["values"]["position"], "A2")

    def test_inline_position_outside_the_rack_is_refused(self):
        rack = make_rack(self.a)
        tank = self.make_tank(self.a, rack_id_fk=str(rack), position="A1")
        r = self.autosave(self.a, f"/zebrafish/tanks/{tank}/update", {"rack_id_fk": str(rack), "position": "Z9"})
        self.assertRefused(r)
        self.assertIn("not a position", r.get_json()["error"])
        self.assertEqual(cell(tank), (rack, 0, 0))

    def test_stale_sheet_row_does_not_undo_a_grid_move_or_owner_change(self):
        rack = make_rack(self.a)
        tank = self.make_tank(self.a, rack_id_fk=str(rack), position="B2")
        self.autosave(self.a, f"/zebrafish/tanks/{tank}/move", {"rack_id_fk": str(rack), "row": "2", "col": "3"})
        self.a.post(f"/zebrafish/tanks/{tank}/update", data={"owner": self.other})  # the dialog
        # The sheet row still shows the old cell and owner.
        self.assertSaved(self.autosave(self.a, f"/zebrafish/tanks/{tank}/update", {
            "rack_id_fk": str(rack), "rack_id_fk_was": str(rack), "position": "B2", "position_was": "B2",
            "owner": self.admin, "owner_was": self.admin, "notes": "stale row"}))
        self.assertEqual(row("select row, col, owner, notes from tanks where id=?", tank),
                         (2, 3, self.other, "stale row"))

    def test_real_owner_edit_from_the_sheet_saves(self):
        tank = self.make_tank(self.a)
        self.assertSaved(self.autosave(self.a, f"/zebrafish/tanks/{tank}/update",
                                       {"owner": self.member, "owner_was": self.admin}))
        self.assertEqual(one("select owner from tanks where id=?", tank), self.member)

    def test_grid_move_onto_an_occupied_cell_swaps_the_tanks(self):
        rack = make_rack(self.a)
        a = self.make_tank(self.a, rack_id_fk=str(rack), position="A1")
        b = self.make_tank(self.a, rack_id_fk=str(rack), position="C4")
        self.assertSaved(self.autosave(self.a, f"/zebrafish/tanks/{b}/move",
                                       {"rack_id_fk": str(rack), "row": "0", "col": "0"}))
        self.assertEqual((cell(a), cell(b)), ((rack, 2, 3), (rack, 0, 0)))

    def test_grid_move_to_a_bad_cell_is_refused(self):
        rack = make_rack(self.a)
        tank = self.make_tank(self.a, rack_id_fk=str(rack), position="A1")
        for data in ({"row": "99", "col": "-4"}, {"row": "-1", "col": "0"}, {"row": "a", "col": "0"},
                     {"rack_id_fk": MISSING, "row": "0", "col": "0"}):
            with self.subTest(data=data):
                self.assertRefused(self.autosave(self.a, f"/zebrafish/tanks/{tank}/move",
                                                 {"rack_id_fk": str(rack), **data}))
        self.assertEqual(cell(tank), (rack, 0, 0))

    def test_grid_move_to_the_tray_unplaces(self):
        rack = make_rack(self.a)
        tank = self.make_tank(self.a, rack_id_fk=str(rack), position="A1")
        self.assertSaved(self.autosave(self.a, f"/zebrafish/tanks/{tank}/move", {"rack_id_fk": "", "row": "", "col": ""}))
        self.assertEqual(cell(tank), (None, None, None))

    def test_genotyping_flag_toggles_and_queues_the_tank(self):
        code = uniq("TK")
        tank = self.make_tank(self.a, code)
        r = self.autosave(self.a, f"/zebrafish/tanks/{tank}/toggle-geno")
        self.assertIs(r.get_json()["needs_genotyping"], True)
        self.assertIn(code, self.get_ok(self.a, "/zebrafish?view=tanks&mode=geno"))
        self.assertIs(self.autosave(self.a, f"/zebrafish/tanks/{tank}/toggle-geno").get_json()["needs_genotyping"],
                      False)

    def test_tank_card_shows_the_racks_position_label(self):
        rack_name = uniq("ZR")
        rack = make_rack(self.a, rack_name)
        tank = self.make_tank(self.a, purpose="stock", rack_id_fk=str(rack), position="C4")
        html = self.get_ok(self.a, f"/zebrafish/tanks/{tank}/card")
        sub = re.search(r'class="sub">(.*?)</div>', html, re.S).group(1).strip()
        self.assertEqual(sub, f"STOCK · {rack_name} · C4")

    def test_duplicate_tank_takes_the_next_code_the_copier_and_a_free_cell(self):
        rack = make_rack(self.a)
        tank = self.make_tank(self.m, purpose="experiment", rack_id_fk=str(rack), position="A1")
        before = one("select max(id) from tanks")
        r = self.post(self.a, f"/zebrafish/tanks/{tank}/duplicate")
        self.assertFlash(r, "Copied tank", "success")
        code, owner, purpose, rack_id, row_, col = row(
            "select tank_id, owner, purpose, rack_id_fk, row, col from tanks where id>?", before)
        self.assertRegex(code, r"^T\d{3,}$")
        self.assertEqual((owner, purpose, rack_id, row_, col), (self.admin, "experiment", rack, 0, 1))

    def test_tank_holding_fish_cannot_be_deleted(self):
        tank = self.make_tank(self.a)
        make_fish(self.a, tank)
        r = self.post(self.a, f"/zebrafish/tanks/{tank}/delete")
        self.assertFlash(r, "still holds 1 fish row", "error")
        self.assertEqual(count("tanks", "id=?", tank), 1)

    def test_deleting_a_tank_clears_links_to_it_and_undo_restores_them(self):
        t1, t2 = self.make_tank(self.a), self.make_tank(self.a)
        clutch = make_clutch(self.a, father_tank_id=str(t1), mother_tank_id=str(t2))
        reason = uniq("reason")
        self.post(self.a, "/zebrafish/sac/create", {"tank_id_fk": str(t1), "count": "1", "reason": reason})
        self.post(self.a, "/zebrafish/mate", {"father_tank_id": str(t1), "mother_tank_id": str(t2)})
        mating = mating_tank_of(t1, t2)
        r = self.post(self.a, f"/zebrafish/tanks/{t1}/delete")
        self.assertFlash(r, "Deleted tank", "success")
        self.assertEqual(count("tanks", "id=?", t1), 0)
        self.assertIsNone(one("select father_tank_id from clutches where id=?", clutch))
        self.assertIsNone(one("select tank_id_fk from fish_sac_log where reason=?", reason))
        self.assertIsNone(one("select mating_father_tank_id from tanks where id=?", mating))
        undo(self.a, last_batch()[0])
        self.assertEqual(count("tanks", "id=?", t1), 1)
        self.assertEqual(one("select father_tank_id from clutches where id=?", clutch), t1)


# ================================================================ fish rows

class FishTests(AppTestCase):

    def test_create_fish_row(self):
        tank = self.make_tank(self.a)
        line = self.make_line(self.a)
        fish = make_fish(self.a, tank, 12, line_id_fk=str(line), sex="mixed", status="alive",
                         date_of_fertilization=days_ago(100), genotype="+/-")
        self.assertEqual(row("select count, line_id_fk, genotype, sac_date from fish where id=?", fish),
                         (12, line, "+/-", None))
        self.assertEqual(tank_fish_total(self.a, tank), 12)

    def test_bad_fish_rows_are_refused(self):
        tank = self.make_tank(self.a)
        before = count("fish")
        cases = [({"tank_id_fk": str(tank), "count": "a dozen"}, "whole number"),
                 ({"tank_id_fk": str(tank), "count": "-3"}, "less than 0"),
                 ({"tank_id_fk": str(tank), "sex": "Q"}, "Sex must be one of"),
                 ({"count": "3"}, "Choose the tank"),
                 ({"tank_id_fk": MISSING, "count": "3"}, "no longer exists")]
        for data, message in cases:
            with self.subTest(data=data):
                self.assertFlash(self.post(self.a, "/zebrafish/fish/create", data), message, "error")
        self.assertEqual(count("fish"), before)

    def test_bad_inline_fish_edits_are_refused(self):
        tank = self.make_tank(self.a)
        fish = make_fish(self.a, tank, 12, date_of_fertilization=days_ago(10))
        for data in ({"count": "-5"}, {"sex": "Q"}, {"status": "zombie"},
                     {"date_of_fertilization": "2026-13-45"}, {"tank_id_fk": MISSING}, {"tank_id_fk": ""}):
            with self.subTest(data=data):
                self.assertRefused(self.autosave(self.a, f"/zebrafish/fish/{fish}/update", data))
        self.assertEqual(row("select count, sex, status, date_of_fertilization, tank_id_fk from fish where id=?", fish),
                         (12, "mixed", "alive", days_ago(10), tank))

    def test_sex_is_matched_case_insensitively(self):
        fish = make_fish(self.a, self.make_tank(self.a))
        self.assertSaved(self.autosave(self.a, f"/zebrafish/fish/{fish}/update", {"sex": "m"}))
        self.assertEqual(one("select sex from fish where id=?", fish), "M")

    def test_status_sac_stamps_today_and_logs_the_fish(self):
        tank = self.make_tank(self.a)
        fish = make_fish(self.a, tank, 12)
        r = self.autosave(self.a, f"/zebrafish/fish/{fish}/update", {"status": "sac", "sac_date": ""})
        self.assertSaved(r)
        state = r.get_json()["row"]
        self.assertEqual((state["values"]["sac_date"], state["active"]), (T, False))
        self.assertEqual(one("select sac_date from fish where id=?", fish), T)
        self.assertEqual(rows("select count, tank_id_fk from fish_sac_log where fish_id_fk=?", fish), [(12, tank)])
        self.assertEqual(tank_fish_total(self.a, tank), 0)

    def test_back_to_alive_clears_the_sac_date_and_its_log_entry(self):
        tank = self.make_tank(self.a)
        fish = make_fish(self.a, tank, 12)
        self.autosave(self.a, f"/zebrafish/fish/{fish}/update", {"status": "sac"})
        r = self.autosave(self.a, f"/zebrafish/fish/{fish}/update", {"status": "alive", "sac_date": T})
        self.assertIs(r.get_json()["row"]["active"], True)
        self.assertIsNone(one("select sac_date from fish where id=?", fish))
        self.assertEqual(count("fish_sac_log", "fish_id_fk=?", fish), 0)
        self.assertEqual(tank_fish_total(self.a, tank), 12)

    def test_fish_row_moves_to_another_tank_inline(self):
        t1, t2 = self.make_tank(self.a), self.make_tank(self.a)
        fish = make_fish(self.a, t1)
        self.assertSaved(self.autosave(self.a, f"/zebrafish/fish/{fish}/update", {"tank_id_fk": str(t2)}))
        self.assertEqual(one("select tank_id_fk from fish where id=?", fish), t2)

    def test_dialog_edit_and_duplicate_of_a_fish_row(self):
        tank = self.make_tank(self.a)
        fish = make_fish(self.a, tank, 4)
        self.post(self.a, f"/zebrafish/fish/{fish}/update", {
            "tank_id_fk": str(tank), "count": "4", "individual_id": "F-01", "sex": "F", "status": "geno",
            "date_of_fertilization": "", "sac_date": "", "genotype": "+/+", "notes": "dialog"})
        self.assertEqual(row("select individual_id, sex, status, genotype from fish where id=?", fish),
                         ("F-01", "F", "geno", "+/+"))
        self.assertFlash(self.post(self.a, f"/zebrafish/fish/{fish}/duplicate"), "Copied the fish row", "success")
        self.assertEqual(rows("select count, sex, individual_id from fish where tank_id_fk=? order by id", tank),
                         [(4, "F", "F-01"), (4, "F", "")])

    def test_deleting_a_fish_row_keeps_its_sac_log_entries_unlinked(self):
        tank = self.make_tank(self.a)
        fish = make_fish(self.a, tank, 6)
        reason = uniq("reason")
        self.post(self.a, "/zebrafish/sac/create", {"fish_id_fk": str(fish), "count": "2", "reason": reason})
        self.post(self.a, f"/zebrafish/fish/{fish}/delete")
        self.assertEqual(count("fish", "id=?", fish), 0)
        self.assertEqual(rows("select fish_id_fk, tank_id_fk from fish_sac_log where reason=?", reason), [(None, tank)])

    def test_bulk_sac_logs_each_row_and_undo_restores_them(self):
        tank = self.make_tank(self.a)
        a, b = make_fish(self.a, tank, 7), make_fish(self.a, tank, 3)
        r = self.post(self.a, "/zebrafish/fish/bulk", {"action": "sac", "selected_ids": [a, b]})
        self.assertFlash(r, "2 fish rows", "success")
        self.assertEqual(rows("select status from fish where id in (?, ?)", a, b), [("sac",), ("sac",)])
        self.assertEqual(one("select sum(count) from fish_sac_log where fish_id_fk in (?, ?)", a, b), 10)
        undo(self.a, last_batch()[0])
        self.assertEqual(rows("select status, sac_date from fish where id in (?, ?)", a, b),
                         [("alive", None), ("alive", None)])
        self.assertEqual(count("fish_sac_log", "fish_id_fk in (?, ?)", a, b), 0)

    def test_bulk_status_must_be_one_of_the_list(self):
        fish = make_fish(self.a, self.make_tank(self.a))
        r = self.post(self.a, "/zebrafish/fish/bulk", {"action": "status", "value": "zombie", "selected_ids": [fish]})
        self.assertFlash(r, "Status must be one of", "error")
        self.post(self.a, "/zebrafish/fish/bulk", {"action": "status", "value": "geno", "selected_ids": [fish]})
        self.assertEqual(one("select status from fish where id=?", fish), "geno")

    def test_bulk_move_to_a_tank_can_be_undone(self):
        t1, t2 = self.make_tank(self.a), self.make_tank(self.a)
        a, b = make_fish(self.a, t1), make_fish(self.a, t1)
        self.post(self.a, "/zebrafish/fish/bulk", {"action": "tank", "value": str(t2), "selected_ids": [a, b]})
        self.assertEqual(rows("select tank_id_fk from fish where id in (?, ?)", a, b), [(t2,), (t2,)])
        undo(self.a, last_batch()[0])
        self.assertEqual(rows("select tank_id_fk from fish where id in (?, ?)", a, b), [(t1,), (t1,)])


# ================================================================ bulk tanks

class TankBulkTests(AppTestCase):

    def test_bulk_purpose_and_its_undo(self):
        a = self.make_tank(self.a, purpose="breeder")
        b = self.make_tank(self.a, purpose="stock")
        r = self.post(self.a, "/zebrafish/tanks/bulk", {"action": "purpose", "value": "quarantine", "selected_ids": [a, b]})
        self.assertFlash(r, "Set purpose: 2 tanks", "success")
        undo(self.a, last_batch()[0])
        self.assertEqual(rows("select purpose from tanks where id in (?, ?) order by id", a, b),
                         [("breeder",), ("stock",)])

    def test_bulk_purpose_outside_the_list_is_refused(self):
        tank = self.make_tank(self.a, purpose="stock")
        r = self.post(self.a, "/zebrafish/tanks/bulk", {"action": "purpose", "value": "zzz", "selected_ids": [tank]})
        self.assertFlash(r, "Purpose must be one of", "error")
        self.assertEqual(one("select purpose from tanks where id=?", tank), "stock")

    def test_bulk_move_places_each_tank_in_a_free_cell_and_undo_takes_them_out(self):
        rack = make_rack(self.a, rows_=2, cols=2)
        a, b = self.make_tank(self.a), self.make_tank(self.a)
        self.post(self.a, "/zebrafish/tanks/bulk", {"action": "rack", "value": str(rack), "selected_ids": [a, b]})
        cells = {cell(a), cell(b)}
        self.assertEqual(cells, {(rack, 0, 0), (rack, 0, 1)})
        undo(self.a, last_batch()[0])
        self.assertEqual(count("tanks", "rack_id_fk=?", rack), 0)

    def test_bulk_move_to_no_rack_unplaces_and_bulk_geno_flags(self):
        rack = make_rack(self.a)
        a = self.make_tank(self.a, rack_id_fk=str(rack), position="A1")
        b = self.make_tank(self.a)
        self.post(self.a, "/zebrafish/tanks/bulk", {"action": "rack", "value": "", "selected_ids": [a]})
        self.assertEqual(cell(a), (None, None, None))
        self.post(self.a, "/zebrafish/tanks/bulk", {"action": "geno", "value": "1", "selected_ids": [a, b]})
        self.assertEqual(count("tanks", "needs_genotyping=true and id in (?, ?)", a, b), 2)

    def test_bulk_delete_keeps_tanks_with_fish_and_undo_restores_the_rest(self):
        a, b, full = self.make_tank(self.a), self.make_tank(self.a), self.make_tank(self.a)
        make_fish(self.a, full)
        r = self.post(self.a, "/zebrafish/tanks/bulk", {"action": "delete", "selected_ids": [a, b, full]})
        self.assertFlash(r, "Deleted: 2 tanks", "success")
        self.assertFlash(r, "fish row", "error")
        self.assertEqual(count("tanks", "id in (?, ?, ?)", a, b, full), 1)
        undo(self.a, last_batch()[0])
        self.assertEqual(count("tanks", "id in (?, ?)", a, b), 2)

    def test_bulk_with_nothing_selected_says_so(self):
        r = self.post(self.a, "/zebrafish/tanks/bulk", {"action": "purpose", "value": "stock"})
        self.assertFlash(r, "Nothing was selected", "error")


# ================================================================ clutches

class ClutchTests(AppTestCase):

    def test_create_clutch_belongs_to_its_creator(self):
        t1, t2 = self.make_tank(self.m), self.make_tank(self.m)
        clutch = make_clutch(self.m, date_of_fertilization=T, father_tank_id=str(t1), mother_tank_id=str(t2),
                             embryo_count="150")
        self.assertEqual(row("select owner, father_tank_id, mother_tank_id, embryo_count from clutches where id=?", clutch),
                         (self.member, t1, t2, 150))

    def test_bad_clutches_are_refused(self):
        taken = uniq("CL")
        make_clutch(self.a, taken)
        cases = [({"clutch_id": taken}, "already used"),
                 ({"clutch_id": uniq("CL"), "embryo_count": "many"}, "whole number"),
                 ({"clutch_id": uniq("CL"), "father_tank_id": MISSING}, "no longer exists")]
        for data, message in cases:
            with self.subTest(data=data):
                self.assertFlash(self.post(self.a, "/zebrafish/clutches/create", data), message, "error")
                self.assertEqual(count("clutches", "clutch_id=?", data["clutch_id"]), 1 if data["clutch_id"] == taken else 0)

    def test_blank_clutch_id_gets_todays_next_code(self):
        before = one("select coalesce(max(id), 0) from clutches")
        self.post(self.a, "/zebrafish/clutches/create", {"clutch_id": "", "date_of_fertilization": T})
        code = one("select clutch_id from clutches where id>?", before)
        self.assertRegex(code, r"^C%s-\d+$" % TODAY.strftime("%y%m%d"))

    def test_bad_inline_clutch_edits_are_refused(self):
        code = uniq("CL")
        clutch = make_clutch(self.a, code)
        self.assertRefused(self.autosave(self.a, f"/zebrafish/clutches/{clutch}/update", {"larvae_count": "-10"}))
        self.assertRefused(self.autosave(self.a, f"/zebrafish/clutches/{clutch}/update", {"clutch_id": ""}))
        self.assertEqual(row("select clutch_id, larvae_count from clutches where id=?", clutch), (code, 0))

    def test_dialog_edits_the_parents(self):
        t1, t2 = self.make_tank(self.a), self.make_tank(self.a)
        clutch = make_clutch(self.a, father_tank_id=str(t1), mother_tank_id=str(t2))
        r = self.a.post(f"/zebrafish/clutches/{clutch}/update", data={"father_tank_id": str(t2), "mother_tank_id": str(t1)})
        self.assertTrue(location(r).startswith("/zebrafish"), location(r))
        self.assertEqual(row("select father_tank_id, mother_tank_id from clutches where id=?", clutch), (t2, t1))

    def test_duplicate_clutch_keeps_the_cross_under_a_new_code(self):
        t1, t2 = self.make_tank(self.a), self.make_tank(self.a)
        code = uniq("CL")
        clutch = make_clutch(self.a, code, father_tank_id=str(t1), mother_tank_id=str(t2), embryo_count="40")
        self.post(self.m, f"/zebrafish/clutches/{clutch}/duplicate")
        copy = row("select clutch_id, father_tank_id, mother_tank_id, owner, embryo_count from clutches "
                   "where id>? order by id desc limit 1", clutch)
        self.assertNotEqual(copy[0], code)
        self.assertEqual(copy[1:], (t1, t2, self.member, 0))

    def test_deleting_a_clutch_clears_fish_links(self):
        clutch = make_clutch(self.a)
        fish = make_fish(self.a, self.make_tank(self.a))
        execute("update fish set clutch_id_fk=? where id=?", clutch, fish)  # no form sets it
        self.post(self.a, f"/zebrafish/clutches/{clutch}/delete")
        self.assertEqual(count("clutches", "id=?", clutch), 0)
        self.assertIsNone(one("select clutch_id_fk from fish where id=?", fish))

    def test_bulk_clutch_owner_and_delete_with_undo(self):
        a, b = make_clutch(self.a), make_clutch(self.a)
        self.post(self.a, "/zebrafish/clutches/bulk", {"action": "owner", "value": self.member, "selected_ids": [a, b]})
        self.assertEqual(rows("select owner from clutches where id in (?, ?)", a, b), [(self.member,), (self.member,)])
        self.post(self.a, "/zebrafish/clutches/bulk", {"action": "delete", "selected_ids": [a, b]})
        self.assertEqual(count("clutches", "id in (?, ?)", a, b), 0)
        undo(self.a, last_batch()[0])
        self.assertEqual(count("clutches", "id in (?, ?)", a, b), 2)


# ================================================================ mating

class MatingTests(AppTestCase):

    def pair(self, client=None, males=10, females=8):
        """A male and a female tank of one line, with fish in them:
        (male tank, female tank, male row, female row)."""
        client = client or self.a
        line = self.make_line(client)
        tm, tf = self.make_tank(client, line_id_fk=str(line)), self.make_tank(client, line_id_fk=str(line))
        fm = make_fish(client, tm, males, line_id_fk=str(line), sex="M", status="alive")
        ff = make_fish(client, tf, females, line_id_fk=str(line), sex="F", status="alive")
        return tm, tf, fm, ff

    def test_bad_matings_are_refused_and_make_no_tank(self):
        t1, t2 = self.make_tank(self.a), self.make_tank(self.a)
        before = count("tanks")
        cases = [({"father_tank_id": t1, "mother_tank_id": t1}, "different"),
                 ({"father_tank_id": t1, "mother_tank_id": MISSING}, "no longer exists"),
                 ({"father_tank_id": t1}, "Pick both"),
                 ({"father_tank_id": t1, "mother_tank_id": t2, "return_days": "-30"}, "less than 1"),
                 ({"father_tank_id": t1, "mother_tank_id": t2, "return_days": "0"}, "less than 1"),
                 ({"father_tank_id": t1, "mother_tank_id": t2, "return_days": "x"}, "whole number"),
                 ({"father_tank_id": t1, "mother_tank_id": t2, "return_days": "99"}, "more than 14")]
        for data, message in cases:
            with self.subTest(data=data):
                self.assertFlash(self.post(self.a, "/zebrafish/mate", data), message, "error")
        self.assertEqual(count("tanks"), before)

    def test_mating_tank_records_its_parents_and_return_date(self):
        t1, t2 = self.make_tank(self.a), self.make_tank(self.a)
        r = self.post(self.a, "/zebrafish/mate", {"father_tank_id": t1, "mother_tank_id": t2, "return_days": "3"})
        self.assertFlash(r, "set up", "success")
        mt = mating_tank_of(t1, t2)
        self.assertEqual(row("select mating_return_at, owner, active from tanks where id=?", mt),
                         (days_ahead(3), self.admin, 1))

    def test_mating_tank_code_skips_past_a_taken_one(self):
        numbers = [int(m.group(1)) for (c,) in rows("select tank_id from tanks where tank_id like 'MT%'")
                   if (m := re.fullmatch(r"MT(\d+)", c))]
        top = max(numbers, default=0)
        self.make_tank(self.a, f"MT{top + 5:03d}")
        t1, t2 = self.make_tank(self.a), self.make_tank(self.a)
        self.post(self.a, "/zebrafish/mate", {"father_tank_id": t1, "mother_tank_id": t2})
        self.assertEqual(tank_code(mating_tank_of(t1, t2)), f"MT{top + 6:03d}")

    def test_wizard_refuses_more_males_than_the_tank_has(self):
        tm, tf, fm, _ = self.pair()
        before = count("tanks")
        r = self.post(self.a, "/zebrafish/mate", {"father_tank_id": tm, "mother_tank_id": tf, "males": "50"})
        self.assertFlash(r, "has 10 live males", "error")
        self.assertEqual(count("tanks"), before)

    def test_wizard_moves_the_asked_for_fish_and_undo_puts_them_back(self):
        tm, tf, fm, ff = self.pair()
        r = self.post(self.a, "/zebrafish/mate", {"father_tank_id": tm, "mother_tank_id": tf, "males": "3", "females": "2"})
        self.assertFlash(r, "with 3 ♂ and 2 ♀", "success")
        mt = mating_tank_of(tm, tf)
        self.assertEqual(rows("select sex, count from fish where tank_id_fk=? order by sex", mt), [("F", 2), ("M", 3)])
        self.assertEqual((one("select count from fish where id=?", fm), one("select count from fish where id=?", ff)), (7, 6))
        undo(self.a, last_batch()[0])
        self.assertEqual(count("tanks", "id=?", mt), 0)
        self.assertEqual((one("select count from fish where id=?", fm), one("select count from fish where id=?", ff)), (10, 8))

    def test_returned_folds_the_fish_back_and_retires_the_mating_tank(self):
        tm, tf, fm, ff = self.pair()
        self.post(self.a, "/zebrafish/mate", {"father_tank_id": tm, "mother_tank_id": tf, "males": "3", "females": "2"})
        mt = mating_tank_of(tm, tf)
        r = self.post(self.a, f"/zebrafish/tanks/{mt}/return")
        self.assertFlash(r, f"3 fish to {tank_code(tm)}, 2 fish to {tank_code(tf)}", "success")
        self.assertEqual((one("select count from fish where id=?", fm), one("select count from fish where id=?", ff)), (10, 8))
        self.assertEqual(count("fish", "tank_id_fk=?", mt), 0)
        active, notes = row("select active, notes from tanks where id=?", mt)
        self.assertEqual(active, 0)
        self.assertIn("Returned ", notes)
        self.assertTrue(last_batch()[1].startswith("return mating tank"))

    def test_returning_twice_is_refused(self):
        tm, tf, _, _ = self.pair()
        self.post(self.a, "/zebrafish/mate", {"father_tank_id": tm, "mother_tank_id": tf, "males": "1"})
        mt = mating_tank_of(tm, tf)
        self.post(self.a, f"/zebrafish/tanks/{mt}/return")
        self.assertFlash(self.post(self.a, f"/zebrafish/tanks/{mt}/return"), "already returned", "error")

    def test_undoing_returned_puts_the_fish_back_in_the_mating_tank(self):
        tm, tf, fm, _ = self.pair()
        self.post(self.a, "/zebrafish/mate", {"father_tank_id": tm, "mother_tank_id": tf, "males": "3", "females": "2"})
        mt = mating_tank_of(tm, tf)
        self.post(self.a, f"/zebrafish/tanks/{mt}/return")
        undo(self.a, last_batch()[0])
        self.assertEqual(rows("select sex, count from fish where tank_id_fk=? order by sex", mt), [("F", 2), ("M", 3)])
        self.assertEqual(one("select count from fish where id=?", fm), 7)
        self.assertEqual(one("select active from tanks where id=?", mt), 1)

    def test_bulk_returned_returns_mating_tanks_and_names_the_others(self):
        tm, tf, fm, _ = self.pair()
        self.post(self.a, "/zebrafish/mate", {"father_tank_id": tm, "mother_tank_id": tf, "males": "4"})
        mt = mating_tank_of(tm, tf)
        r = self.post(self.a, "/zebrafish/tanks/bulk", {"action": "return", "selected_ids": [mt, tm]})
        self.assertFlash(r, "Returned: 1 tank", "success")
        self.assertFlash(r, "is not a mating tank", "error")
        self.assertEqual(one("select count from fish where id=?", fm), 10)
        self.assertEqual(one("select active from tanks where id=?", mt), 0)

    def test_mating_tank_without_recorded_homes_asks_where_the_fish_go(self):
        tf = self.make_tank(self.a)
        code = uniq("MTX")
        old = self.make_tank(self.a, code, purpose="mating")  # like one set up before the wizard
        group = make_fish(self.a, old, 4, sex="mixed")
        r = self.post(self.a, f"/zebrafish/tanks/{old}/return")
        self.assertFlash(r, "Choose which tank", "error")
        r = self.post(self.a, f"/zebrafish/tanks/{old}/return", {f"home_{group}": str(old)})
        self.assertFlash(r, f"other than {code}", "error")
        self.assertEqual(row("select tank_id_fk from fish where id=?", group), (old,))
        self.post(self.a, f"/zebrafish/tanks/{old}/return", {f"home_{group}": str(tf)})
        self.assertEqual(one("select tank_id_fk from fish where id=?", group), tf)
        self.assertEqual(one("select active from tanks where id=?", old), 0)

    def test_empty_mating_tank_is_simply_retired(self):
        t1, t2 = self.make_tank(self.a), self.make_tank(self.a)
        self.post(self.a, "/zebrafish/mate", {"father_tank_id": t1, "mother_tank_id": t2})
        mt = mating_tank_of(t1, t2)
        self.assertFlash(self.post(self.a, f"/zebrafish/tanks/{mt}/return"), "is retired", "success")
        self.assertEqual(one("select active from tanks where id=?", mt), 0)

    def test_member_cannot_return_someone_elses_mating_tank(self):
        tm, tf, _, _ = self.pair()
        self.post(self.a, "/zebrafish/mate", {"father_tank_id": tm, "mother_tank_id": tf, "males": "1"})
        mt = mating_tank_of(tm, tf)
        self.assertFlash(self.post(self.m, f"/zebrafish/tanks/{mt}/return"), "belong", "error")
        self.assertEqual(one("select active from tanks where id=?", mt), 1)
        self.assertNotIn(f'<dialog id="return-{mt}"', self.get_ok(self.m, "/zebrafish?view=tanks"))

    def test_member_cannot_mate_from_someone_elses_tanks(self):
        t1, t2 = self.make_tank(self.a), self.make_tank(self.a)
        self.assertFlash(self.post(self.m, "/zebrafish/mate", {"father_tank_id": t1, "mother_tank_id": t2}),
                         "belong", "error")
        self.assertIsNone(mating_tank_of(t1, t2))


# ================================================================ sac log

class SacLogTests(AppTestCase):

    def test_sac_from_a_fish_row_takes_them_off_its_count(self):
        line = self.make_line(self.a)
        tank = self.make_tank(self.a, line_id_fk=str(line))
        fish = make_fish(self.a, tank, 10)
        reason = uniq("fin clip")
        r = self.post(self.a, "/zebrafish/sac/create", {"fish_id_fk": str(fish), "count": "3", "reason": reason})
        self.assertFlash(r, "7 left", "success")
        self.assertEqual(one("select count from fish where id=?", fish), 7)
        # The tank's line stands in for a row without its own.
        self.assertEqual(row("select tank_id_fk, line_id_fk, fish_id_fk, count from fish_sac_log where reason=?", reason),
                         (tank, line, fish, 3))
        self.assertEqual(tank_fish_total(self.a, tank), 7)
        self.assertIn(reason, self.get_ok(self.a, "/zebrafish?view=tanks"))

    def test_sac_of_more_than_the_row_holds_or_in_the_future_is_refused(self):
        fish = make_fish(self.a, self.make_tank(self.a), 7)
        before = count("fish_sac_log")
        self.assertFlash(self.post(self.a, "/zebrafish/sac/create", {"fish_id_fk": str(fish), "count": "8"}),
                         "has 7 fish", "error")
        self.assertFlash(self.post(self.a, "/zebrafish/sac/create",
                                   {"fish_id_fk": str(fish), "count": "1", "sac_date": days_ahead(1)}),
                         "future", "error")
        self.assertEqual((count("fish_sac_log"), one("select count from fish where id=?", fish)), (before, 7))

    def test_sac_of_the_last_fish_marks_the_row_sac_and_undo_restores_it(self):
        fish = make_fish(self.a, self.make_tank(self.a), 7)
        yesterday = days_ago(1)
        r = self.post(self.a, "/zebrafish/sac/create", {"fish_id_fk": str(fish), "count": "7", "sac_date": yesterday})
        self.assertFlash(r, "marked sac", "success")
        self.assertEqual(row("select count, status, sac_date from fish where id=?", fish), (0, "sac", yesterday))
        self.assertEqual([(str(at)[:10], n) for at, n in rows("select recorded_at, count from fish_sac_log where fish_id_fk=?", fish)],
                         [(yesterday, 7)])
        self.assertFlash(self.post(self.a, "/zebrafish/sac/create", {"fish_id_fk": str(fish), "count": "1"}),
                         "already sac", "error")
        undo(self.a, last_batch()[0])
        self.assertEqual(row("select count, status, sac_date from fish where id=?", fish), (7, "alive", None))
        self.assertEqual(count("fish_sac_log", "fish_id_fk=?", fish), 0)

    def test_reviving_a_row_keeps_the_hand_logged_entries(self):
        fish = make_fish(self.a, self.make_tank(self.a), 8)
        reason = uniq("all gone")
        self.post(self.a, "/zebrafish/sac/create", {"fish_id_fk": str(fish), "count": "8", "reason": reason})
        self.assertEqual(one("select status from fish where id=?", fish), "sac")
        self.autosave(self.a, f"/zebrafish/fish/{fish}/update", {"status": "alive"})
        self.assertEqual(row("select status, sac_date from fish where id=?", fish), ("alive", None))
        self.assertEqual(count("fish_sac_log", "reason=?", reason), 1)

    def test_sac_without_a_row_only_logs(self):
        tank = self.make_tank(self.a)
        fish = make_fish(self.a, tank, 5)
        reason = uniq("no row")
        self.post(self.a, "/zebrafish/sac/create", {"tank_id_fk": str(tank), "count": "2", "reason": reason})
        self.assertEqual(row("select fish_id_fk, tank_id_fk, count from fish_sac_log where reason=?", reason), (None, tank, 2))
        self.assertEqual(one("select count from fish where id=?", fish), 5)

    def test_bad_sac_entries_are_refused(self):
        before = count("fish_sac_log")
        cases = [({"count": "three"}, "whole number"),
                 ({"count": "0"}, "less than 1"),
                 ({"tank_id_fk": MISSING, "count": "1"}, "no longer exists"),
                 ({"fish_id_fk": MISSING, "count": "1"}, "no longer exists"),
                 ({"count": "1", "sac_date": "2026-02-30"}, "is not a date")]
        for data, message in cases:
            with self.subTest(data=data):
                self.assertFlash(self.post(self.a, "/zebrafish/sac/create", data), message, "error")
        self.assertEqual(count("fish_sac_log"), before)


# ================================================================ permissions

class PermissionTests(AppTestCase):

    def test_member_cannot_edit_an_admins_tank(self):
        tank = self.make_tank(self.a, purpose="stock")
        r = self.autosave(self.m, f"/zebrafish/tanks/{tank}/update", {"notes": "member was here", "owner": self.member})
        self.assertEqual(r.status_code, 403)
        self.assertIs(r.get_json()["ok"], False)
        self.assertFlash(self.post(self.m, f"/zebrafish/tanks/{tank}/update", {"notes": "dialog"}), "belong", "error")
        self.assertEqual(row("select owner, notes from tanks where id=?", tank), (self.admin, ""))

    def test_member_cannot_flag_move_or_delete_an_admins_tank(self):
        rack = make_rack(self.a)
        tank = self.make_tank(self.a, purpose="stock", rack_id_fk=str(rack), position="A1")
        self.assertEqual(self.autosave(self.m, f"/zebrafish/tanks/{tank}/toggle-geno").status_code, 403)
        self.assertEqual(self.autosave(self.m, f"/zebrafish/tanks/{tank}/move",
                                       {"rack_id_fk": str(rack), "row": "2", "col": "0"}).status_code, 403)
        self.assertFlash(self.post(self.m, f"/zebrafish/tanks/{tank}/delete"), "belong", "error")
        self.assertEqual(row("select 1, needs_genotyping, row, col from tanks where id=?", tank), (1, 0, 0, 0))

    def test_member_cannot_swap_onto_someone_elses_cell(self):
        rack = make_rack(self.a)
        theirs = self.make_tank(self.a, purpose="stock", rack_id_fk=str(rack), position="A1")
        mine = self.make_tank(self.m, rack_id_fk=str(rack), position="B1")
        r = self.autosave(self.m, f"/zebrafish/tanks/{mine}/move", {"rack_id_fk": str(rack), "row": "0", "col": "0"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual((cell(theirs), cell(mine)), ((rack, 0, 0), (rack, 1, 0)))

    def test_member_cannot_touch_the_fish_in_an_admins_tank(self):
        tank = self.make_tank(self.a, purpose="stock")
        fish = make_fish(self.a, tank, 10)
        self.assertEqual(self.autosave(self.m, f"/zebrafish/fish/{fish}/update",
                                       {"count": "0", "status": "sac"}).status_code, 403)
        self.post(self.m, f"/zebrafish/fish/{fish}/delete")
        self.assertFlash(self.post(self.m, "/zebrafish/fish/create", {"tank_id_fk": str(tank), "count": "2"}),
                         "belong", "error")
        self.assertFlash(self.post(self.m, "/zebrafish/sac/create", {"fish_id_fk": str(fish), "count": "1"}),
                         "belong", "error")
        self.assertFlash(self.post(self.m, "/zebrafish/sac/create", {"tank_id_fk": str(tank), "count": "1"}),
                         "belong", "error")
        self.assertEqual(row("select count, status from fish where id=?", fish), (10, "alive"))
        self.assertEqual(count("fish", "tank_id_fk=?", tank), 1)

    def test_member_cannot_move_their_fish_into_an_admins_tank(self):
        theirs = self.make_tank(self.a, purpose="stock")
        mine = self.make_tank(self.m)
        fish = make_fish(self.m, mine, 2)
        self.assertRefused(self.autosave(self.m, f"/zebrafish/fish/{fish}/update", {"tank_id_fk": str(theirs)}))
        r = self.post(self.m, "/zebrafish/fish/bulk", {"action": "tank", "value": str(theirs), "selected_ids": [fish]})
        self.assertFlash(r, "belong", "error")
        self.assertEqual(one("select tank_id_fk from fish where id=?", fish), mine)

    def test_member_cannot_edit_or_delete_an_admins_clutch(self):
        clutch = make_clutch(self.a)
        self.assertEqual(self.autosave(self.m, f"/zebrafish/clutches/{clutch}/update", {"notes": "x"}).status_code, 403)
        self.assertFlash(self.post(self.m, f"/zebrafish/clutches/{clutch}/delete"), "belongs", "error")
        self.post(self.m, "/zebrafish/clutches/bulk", {"action": "owner", "value": self.member, "selected_ids": [clutch]})
        self.assertEqual(row("select owner, notes from clutches where id=?", clutch), (self.admin, ""))

    def test_member_may_edit_a_shared_breeder_tank(self):
        tank = self.make_tank(self.a, purpose="breeder")
        self.assertSaved(self.autosave(self.m, f"/zebrafish/tanks/{tank}/update", {"notes": "shared tank"}))
        self.assertEqual(one("select notes from tanks where id=?", tank), "shared tank")

    def test_the_old_word_breeding_makes_a_shared_breeder_tank(self):
        # Before the rename a breeder tank was "breeding"; typed or sent
        # through the API it still means breeder, never a mating tank.
        tank = self.make_tank(self.a, purpose="Breeding")
        self.assertEqual(one("select purpose from tanks where id=?", tank), "breeder")
        self.assertSaved(self.autosave(self.m, f"/zebrafish/tanks/{tank}/update", {"notes": "still shared"}))
        mine = self.make_tank(self.a)
        self.assertSaved(self.autosave(self.a, f"/zebrafish/tanks/{mine}/update", {"purpose": "breeding"}))
        self.assertEqual(one("select purpose from tanks where id=?", mine), "breeder")

    def test_breeding_tanks_from_before_become_breeder_tanks(self):
        """Revision 0054: a tank stored as "breeding" (any case) is a breeder
        tank, still the lab's to edit; a mating tank stays a mating tank."""
        import importlib
        from unittest import mock
        from app.db import engine
        old = self.make_tank(self.a, purpose="breeder")
        execute("update tanks set purpose=? where id=?", " Breeding", old)
        mating = self.make_tank(self.a, purpose="mating")
        migration = importlib.import_module("migrations.versions.0054_breeder_tanks")
        with engine.begin() as conn:
            with mock.patch.object(migration, "op", mock.Mock(get_bind=lambda: conn)), \
                    mock.patch.object(migration, "has_table", lambda name: True):
                migration.upgrade()
        self.assertEqual(one("select purpose from tanks where id=?", old), "breeder")
        self.assertEqual(one("select purpose from tanks where id=?", mating), "mating")
        self.assertSaved(self.autosave(self.m, f"/zebrafish/tanks/{old}/update", {"notes": "fed"}))

    def test_member_owns_and_edits_the_tanks_they_make(self):
        tank = self.make_tank(self.m)
        self.assertEqual(one("select owner from tanks where id=?", tank), self.member)
        self.assertSaved(self.autosave(self.m, f"/zebrafish/tanks/{tank}/update", {"notes": "mine"}))
        self.assertRefused(self.autosave(self.m, f"/zebrafish/tanks/{tank}/update", {"purpose": "zzz"}))

    def test_admin_may_edit_a_members_tank(self):
        tank = self.make_tank(self.m, purpose="stock")
        self.assertSaved(self.autosave(self.a, f"/zebrafish/tanks/{tank}/update", {"notes": "admin"}))
        self.assertEqual(one("select notes from tanks where id=?", tank), "admin")

    def test_member_bulk_changes_only_their_own_tanks(self):
        theirs = self.make_tank(self.a, purpose="stock")
        mine = self.make_tank(self.m, purpose="stock")
        r = self.post(self.m, "/zebrafish/tanks/bulk", {"action": "purpose", "value": "experiment",
                                                       "selected_ids": [theirs, mine]})
        self.assertFlash(r, "1 skipped", "success")
        self.assertEqual(rows("select purpose from tanks where id in (?, ?) order by id", theirs, mine),
                         [("stock",), ("experiment",)])
        r = self.post(self.m, "/zebrafish/tanks/bulk", {"action": "delete", "selected_ids": [theirs]})
        self.assertFlash(r, "not yours", "error")
        self.assertEqual(count("tanks", "id=?", theirs), 1)

    def test_member_bulk_sac_skips_an_admins_fish(self):
        fish = make_fish(self.a, self.make_tank(self.a, purpose="stock"))
        self.assertFlash(self.post(self.m, "/zebrafish/fish/bulk", {"action": "sac", "selected_ids": [fish]}),
                         "not yours", "error")
        self.assertEqual(one("select status from fish where id=?", fish), "alive")

    def test_member_cannot_undo_someone_elses_batch(self):
        a, b = self.make_tank(self.a, purpose="stock"), self.make_tank(self.a, purpose="stock")
        self.post(self.a, "/zebrafish/tanks/bulk", {"action": "purpose", "value": "quarantine", "selected_ids": [a, b]})
        r = undo(self.m, last_batch()[0])
        self.assertFlash(r, "Only whoever ran a batch", "error")
        self.assertEqual(count("tanks", "purpose='quarantine' and id in (?, ?)", a, b), 2)

    def test_tanks_sheet_locks_someone_elses_tank_for_a_member(self):
        theirs = self.make_tank(self.a, purpose="stock")
        mine = self.make_tank(self.m)
        html = self.get_ok(self.m, "/zebrafish?view=tanks")
        locked = sheet_row(html, r'<tr id="tank-%d"' % theirs)
        self.assertIn("sheet-lock", locked)
        self.assertIn("disabled", locked)
        self.assertNotIn("sheet-lock", sheet_row(html, r'<tr id="tank-%d"' % mine))


if __name__ == "__main__":
    unittest.main()
