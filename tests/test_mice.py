"""Mice: status and date-of-death rules, the cohort DOB rule, audited
cage/litter moves and their undo, who may change a mouse, batch edits,
duplicate, delete, weights and transfers."""
from __future__ import annotations

import json
import unittest

from tests.base import *  # noqa: F401,F403
from tests.base import AppTestCase, T, count, days_ago, last_batch, one, row, rows, uniq


# ---------------------------------------------------------------- helpers

def update_url(mid: int) -> str:
    return f"/colony/mice/{mid}/update"


def mouse(mid: int) -> dict:
    """The mouse as the sheet sees it (codes, not row ids), '' for NULL."""
    r = row("""select m.mouse_id, m.gender, m.status, m.transgene_1, m.transgene_2, m.transgene_3, m.transgene_4,
                      m.owner, m.note, m.date_of_death, c.cage_id, c.cage_location, l.litter_id, l.date_of_birth,
                      m.cage_id_fk, m.litter_id_fk, m.genotype
               from mice m left join mouse_cages c on c.id=m.cage_id_fk
                           left join litters l on l.id=m.litter_id_fk where m.id=?""", mid)
    keys = ["mouse_id", "gender", "status", "transgene_1", "transgene_2", "transgene_3", "transgene_4",
            "owner", "note", "date_of_death", "cage_id", "cage_location", "litter_id", "date_of_birth",
            "cage_id_fk", "litter_id_fk", "genotype"]
    return {k: ("" if v is None else v) for k, v in zip(keys, r)}


def sheet_form(mid: int, **changes) -> dict:
    """What the mouse sheet's row form posts for this mouse, with `changes`."""
    m = mouse(mid)
    data = {k: m[k] for k in ("gender", "status", "transgene_1", "transgene_2", "transgene_3", "transgene_4",
                              "owner", "note", "date_of_death", "cage_id", "cage_location", "litter_id",
                              "date_of_birth")}
    data.update(confirm_cohort_dob_change="0", cage_location_was=m["cage_location"],
                cage_rack_was="", cage_position_was="")
    data.update(changes)
    return data


def card_form(mid: int, **changes) -> dict:
    """What a cage card's mouse row posts: only the mouse's own fields (no
    cage, location, or dates)."""
    m = mouse(mid)
    data = {k: m[k] for k in ("gender", "status", "transgene_1", "transgene_2", "transgene_3", "transgene_4",
                              "owner", "litter_id", "note")}
    data.update(changes)
    return data


def last_audit(table: str, record_id: int):
    """(action, changes dict or snapshot payload) of the newest audit row."""
    r = row("select action, changes_json from audit_log where table_name=? and record_id=? order by id desc limit 1",
            table, record_id)
    if not r:
        return None, {}
    return r[0], (json.loads(r[1]) if r[1] else {})


def newest_batch(actor: str):
    """(id, description, record_count, undone_at) of `actor`'s newest batch."""
    return row("select id, description, record_count, undone_at from batches where actor=? order by id desc limit 1",
               actor)


def take_flashes(client) -> list[tuple[str, str]]:
    """Pop the flashes a redirecting POST left in the session (cheaper than
    following the redirect to a full colony page)."""
    with client.session_transaction() as sess:
        found = sess.pop("_flashes", [])
    return [(kind, text) for kind, text in found]


def flash_texts(client, kind: str | None = None) -> str:
    return " | ".join(text for k, text in take_flashes(client) if kind is None or k == kind)


class ColumnsTheLabAdds(AppTestCase):
    """The colony takes columns of a lab's own, as every configurable
    database always has (issue #39)."""

    def columns(self, *fields):
        """Set the colony's own columns through Configure."""
        data = {"field_count": str(len(fields))}
        for i, f in enumerate(fields):
            data[f"field_{i}_label"] = f["label"]
            data[f"field_{i}_type"] = f.get("type", "text")
            data[f"field_{i}_options"] = ", ".join(f.get("options", []))
            data[f"field_{i}_width"] = str(f.get("width", 130))
            if f.get("in_table", True):
                data[f"field_{i}_in_table"] = "1"
        return self.post(self.a, "/organisms/builtin/colony/fields", data)

    def setUp(self):
        super().setUp()
        self.addCleanup(self.columns)      # leave the colony as it was

    def test_a_column_shows_in_the_sheet_and_takes_a_value(self):
        self.columns({"label": "Organ weight", "type": "number"})
        mouse = self.make_mouse(self.a, self.admin)
        html = self.get_ok(self.a, "/colony?view=mice")
        self.assertIn("Organ weight", html)
        self.assertIn('name="attr_organ_weight"', html)
        self.assertSaved(self.autosave(self.a, f"/colony/mice/{mouse}/update", {"attr_organ_weight": "31.4"}))
        self.assertIn('value="31.4"', self.get_ok(self.a, "/colony?view=mice"))

    def test_a_choice_column_offers_its_choices(self):
        self.columns({"label": "Perfused", "type": "select", "options": ["yes", "no"]})
        self.make_mouse(self.a, self.admin)
        html = self.get_ok(self.a, "/colony?view=mice")
        self.assertIn('name="attr_perfused"', html)
        self.assertIn('<option value="yes"', html)

    def test_a_column_may_not_take_the_name_of_one_the_colony_has(self):
        from app import custom_fields
        kept = custom_fields.normalise("colony", [{"key": "owner", "label": "Owner"},
                                                  {"key": "genotype", "label": "Genotype"},
                                                  {"key": "tail_clip", "label": "Tail clip"}])
        self.assertEqual([f["key"] for f in kept], ["tail_clip"])

    def test_removing_a_column_keeps_what_the_records_hold(self):
        self.columns({"label": "Perfused", "type": "text"})
        mouse = self.make_mouse(self.a, self.admin)
        self.autosave(self.a, f"/colony/mice/{mouse}/update", {"attr_perfused": "yes"})
        self.columns()                                   # take the column away
        self.assertNotIn("attr_perfused", self.get_ok(self.a, "/colony?view=mice"))
        self.assertIn("yes", one("select attrs from mice where id=?", mouse) or "")
        self.columns({"label": "Perfused", "type": "text"})   # and put it back
        self.assertIn('value="yes"', self.get_ok(self.a, "/colony?view=mice"))

    def test_saving_one_cell_leaves_the_others_alone(self):
        self.columns({"label": "Perfused", "type": "text"}, {"label": "Score", "type": "number"})
        mouse = self.make_mouse(self.a, self.admin)
        self.autosave(self.a, f"/colony/mice/{mouse}/update", {"attr_perfused": "yes", "attr_score": "3"})
        self.autosave(self.a, f"/colony/mice/{mouse}/update", {"attr_score": "4"})
        import json
        held = json.loads(one("select attrs from mice where id=?", mouse))
        self.assertEqual(held, {"perfused": "yes", "score": "4"})

    def test_the_other_built_in_databases_take_them_too(self):
        """One path serves the colony, zebrafish and plasmids."""
        self.make_plasmid(self.a)
        self.make_tank(self.a)
        for key, page, label, field in (("plasmids", "/plasmids", "Supplier", "attr_supplier"),
                                        ("zebrafish", "/zebrafish", "Water system", "attr_water_system")):
            with self.subTest(database=key):
                self.post(self.a, f"/organisms/builtin/{key}/fields",
                          {"field_count": "1", "field_0_label": label, "field_0_type": "text",
                           "field_0_width": "130", "field_0_in_table": "1"})
                self.addCleanup(self.post, self.a, f"/organisms/builtin/{key}/fields", {"field_count": "0"})
                html = self.get_ok(self.a, page)
                self.assertIn(label, html)
                self.assertIn(f'name="{field}"', html)

    def test_a_column_is_set_on_many_at_once(self):
        self.columns({"label": "Perfused", "type": "text"})
        mice = [self.make_mouse(self.a, self.admin) for _ in range(3)]
        r = self.post(self.a, "/colony/mice/bulk-update",
                      {"field": "attr_perfused", "value": "yes", "selected_ids": [str(m) for m in mice]})
        self.assertNoErrors(r)
        import json
        for m in mice:
            self.assertEqual(json.loads(one("select attrs from mice where id=?", m))["perfused"], "yes")

    def test_a_spreadsheet_column_of_that_name_comes_into_it(self):
        """A column the lab added is one the importer can match a sheet to,
        and what it reads lands in it."""
        import io
        from tests.test_sheet_import import xlsx
        self.columns({"label": "Tail clip", "type": "text"})
        tag = uniq("TC")
        data = xlsx([["Mouse ID", "Strain", "Tail clip"], ["", tag, "done"]])
        r = self.a.post("/import-sheet/mice/upload", data={"file": (io.BytesIO(data), "colony.xlsx")},
                        content_type="multipart/form-data")
        token = r.headers["Location"].rsplit("/", 1)[1]
        html = self.get_ok(self.a, f"/import-sheet/file/{token}")
        self.assertIn("Tail clip", html)
        import re
        form = {}
        for name, body in re.findall(r'<select name="((?:map|kind)-\d+)"[^>]*>(.*?)</select>', html, re.S):
            picked = re.search(r'<option value="([^"]*)" selected', body)
            form[name] = picked.group(1) if picked else ""
        self.assertIn("attr_tail_clip", form.values())   # matched by its own name
        form.update({"sheet": "Sheet1", "fill-owner": "me"})
        self.post(self.a, f"/import-sheet/file/{token}/run", data=form)
        held = one("select attrs from mice where transgene_1=?", tag)
        self.assertIn("done", held or "")

    def test_searching_finds_a_record_by_what_a_column_holds(self):
        self.columns({"label": "Tail clip", "type": "text"})
        mouse = self.make_mouse(self.a, self.admin)
        mark = uniq("clip")
        self.autosave(self.a, f"/colony/mice/{mouse}/update", {"attr_tail_clip": mark})
        found = self.a.get(f"/search?q={mark}").get_json()
        self.assertIn("mouse", [r["type"] for r in found["results"]])

    def test_only_an_admin_adds_columns(self):
        r = self.post(self.m, "/organisms/builtin/colony/fields",
                      {"field_count": "1", "field_0_label": "Sneaky", "field_0_type": "text"})
        self.assertFlash(r, "Only an admin", "error")


class TheEarTag(AppTestCase):
    """What is written on the animal, beside the number BioManager gives it
    (issue #39): the number stays the identity, the tag is how you find the
    mouse in your hand."""

    def test_it_is_typed_in_the_sheet_and_kept(self):
        mouse = self.make_mouse(self.m, self.member)
        self.assertSaved(self.autosave(self.m, f"/colony/mice/{mouse}/update", {"ear_tag": "RF"}))
        self.assertEqual(one("select ear_tag from mice where id=?", mouse), "RF")
        # A tag number does just as well, and it need not be unique: two
        # mice in different cages are often punched the same.
        mark = uniq("RF")
        self.autosave(self.m, f"/colony/mice/{mouse}/update", {"ear_tag": mark})
        other = self.make_mouse(self.m, self.member)
        self.assertSaved(self.autosave(self.m, f"/colony/mice/{other}/update", {"ear_tag": mark}))
        self.assertEqual(count("mice", "ear_tag=?", mark), 2)

    def test_the_sheet_shows_the_column_after_the_number(self):
        mouse = self.make_mouse(self.m, self.member)
        self.autosave(self.m, f"/colony/mice/{mouse}/update", {"ear_tag": "LB-7"})
        html = self.get_ok(self.m, "/colony?view=mice")
        self.assertIn('data-sort-key="ear_tag"', html)
        self.assertIn('value="LB-7"', html)
        self.assertLess(html.index('data-sort-key="ear_tag"'), html.index('data-sort-key="gender"'))

    def test_the_number_carries_the_tag_on_hover_too(self):
        """Someone who hides the column still sees the mark when they point
        at the number."""
        mouse = self.make_mouse(self.m, self.member)
        self.autosave(self.m, f"/colony/mice/{mouse}/update", {"ear_tag": "RF"})
        self.assertIn('title="Custom tag RF"', self.get_ok(self.m, "/colony?view=mice"))

    def test_searching_for_the_tag_finds_the_mouse(self):
        mouse = self.make_mouse(self.m, self.member)
        tag = uniq("ET")
        self.autosave(self.m, f"/colony/mice/{mouse}/update", {"ear_tag": tag})
        found = self.m.get(f"/search?q={tag}").get_json()
        self.assertIn("mouse", [r["type"] for r in found["results"]])

    def test_the_number_is_still_the_identity(self):
        """Changing the tag never changes what everything else links by."""
        mouse = self.make_mouse(self.m, self.member)
        was = one("select mouse_id from mice where id=?", mouse)
        self.autosave(self.m, f"/colony/mice/{mouse}/update", {"ear_tag": "999"})
        self.assertEqual(one("select mouse_id from mice where id=?", mouse), was)


class Case(AppTestCase):
    """Starts each test with no flashes left over from an earlier one on
    the class's shared clients."""

    def setUp(self):
        super().setUp()
        for client in (self.a, self.m, self.o):
            take_flashes(client)


# ---------------------------------------------------------------- status

class MouseStatusTests(Case):
    """Choosing an end status stamps today as the date of death; leaving
    one clears it (services.apply_status_rules)."""

    def setUp(self):
        super().setUp()
        self.mid = self.make_mouse(self.m, self.member, cage=uniq("C"))

    def save(self, **changes):
        r = self.autosave(self.m, update_url(self.mid), sheet_form(self.mid, **changes))
        self.assertSaved(r)
        return r.get_json()["mouse"]

    def test_choosing_sac_stamps_today_and_ends_the_mouse(self):
        state = self.save(status="sac", date_of_death="")
        self.assertEqual(state["date_of_death"], T)
        self.assertFalse(state["active"])
        self.assertEqual(mouse(self.mid)["date_of_death"], T)

    def test_explicit_date_of_death_with_end_status_is_kept(self):
        state = self.save(status="sac", date_of_death=days_ago(5))
        self.assertEqual(state["date_of_death"], days_ago(5))
        self.save(date_of_death=days_ago(9))  # still sac, the date stays editable
        self.assertEqual(mouse(self.mid)["date_of_death"], days_ago(9))

    def test_reviving_from_sac_clears_the_date_even_when_the_row_posts_it(self):
        self.save(status="sac", date_of_death=days_ago(3))
        state = self.save(status="experiment", date_of_death=days_ago(3))
        self.assertEqual(state["date_of_death"], "")
        self.assertTrue(state["active"])
        self.assertEqual(mouse(self.mid)["date_of_death"], "")

    def test_other_end_statuses_stamp_whatever_their_case(self):
        for status in ("dead", "Found dead", "EUTHANIZED", "died"):
            with self.subTest(status=status):
                self.save(status="experiment", date_of_death="")
                state = self.save(status=status, date_of_death="")
                self.assertEqual(state["date_of_death"], T)
                self.assertFalse(state["active"])

    def test_moving_between_end_statuses_keeps_the_original_date(self):
        self.save(status="sac", date_of_death=days_ago(10))
        state = self.save(status="dead")
        self.assertEqual(state["date_of_death"], days_ago(10))

    def test_date_of_death_on_a_living_status_is_kept_and_ends_the_mouse(self):
        state = self.save(status="breeder", date_of_death=days_ago(2))
        self.assertEqual(state["date_of_death"], days_ago(2))
        self.assertFalse(state["active"])

    def test_clearing_the_date_of_death_revives_the_mouse(self):
        self.save(status="breeder", date_of_death=days_ago(2))
        state = self.save(date_of_death="")
        self.assertTrue(state["active"])
        self.assertEqual(mouse(self.mid)["date_of_death"], "")

    def test_card_edit_after_a_revive_does_not_kill_the_mouse_again(self):
        self.save(status="sac", date_of_death="")
        r = self.autosave(self.m, update_url(self.mid), card_form(self.mid, status="experiment"))
        self.assertTrue(r.get_json()["mouse"]["active"])
        r = self.autosave(self.m, update_url(self.mid), card_form(self.mid, note="later note"))
        self.assertSaved(r)
        self.assertTrue(r.get_json()["mouse"]["active"])
        self.assertEqual(mouse(self.mid)["date_of_death"], "")
        self.assertEqual(mouse(self.mid)["note"], "later note")

    def test_card_edit_leaves_a_dead_mouses_date_alone(self):
        self.save(status="sac", date_of_death=days_ago(4))
        self.assertSaved(self.autosave(self.m, update_url(self.mid), card_form(self.mid, note="post mortem")))
        self.assertEqual(mouse(self.mid)["date_of_death"], days_ago(4))

    def test_dialog_save_applies_the_same_rule(self):
        self.m.post(update_url(self.mid), data=sheet_form(self.mid, status="sac", date_of_death=""))
        self.assertEqual(mouse(self.mid)["date_of_death"], T)

    def test_a_mouse_created_as_sac_is_stamped_today(self):
        mid = self.make_mouse(self.m, self.member, status="sac")
        self.assertEqual(mouse(mid)["date_of_death"], T)

    def test_transfer_status_is_not_alive_but_has_no_date_of_death(self):
        state = self.save(status="transfer")
        self.assertFalse(state["active"])
        self.assertEqual(state["date_of_death"], "")


# ---------------------------------------------------------------- cohort DOB

class CohortDobTests(Case):
    """A date of birth belongs to the litter (the cohort). Changing one
    mouse's DOB while keeping its litter must be confirmed, and then moves
    that mouse into a litter of its own."""

    def setUp(self):
        super().setUp()
        self.colony = self.make_colony(self.m, self.member, n_mice=2, dob=days_ago(60))
        self.mid, self.mate = self.colony["mice"]
        self.old_litter = self.colony["litter_id"]

    def test_dob_change_without_confirm_is_refused_and_changes_nothing(self):
        r = self.autosave(self.m, update_url(self.mid),
                          sheet_form(self.mid, date_of_birth=days_ago(50), note="should not save"))
        self.assertRefused(r)
        self.assertIn("cohort", r.get_json()["error"])
        m = mouse(self.mid)
        self.assertEqual((m["litter_id_fk"], m["date_of_birth"], m["note"]), (self.old_litter, days_ago(60), ""))

    def test_dialog_dob_change_without_confirm_is_refused_too(self):
        self.m.post(update_url(self.mid), data=sheet_form(self.mid, date_of_birth=days_ago(50)))
        self.assertIn("Confirm", flash_texts(self.m, "error"))
        self.assertEqual(one("select date_of_birth from litters where id=?", self.old_litter), days_ago(60))

    def test_confirmed_dob_change_detaches_the_mouse_into_a_new_litter(self):
        r = self.autosave(self.m, update_url(self.mid),
                          sheet_form(self.mid, date_of_birth=days_ago(50), confirm_cohort_dob_change="1"))
        self.assertSaved(r)
        reply = r.get_json()["mouse"]
        m = mouse(self.mid)
        self.assertNotEqual(m["litter_id_fk"], self.old_litter)
        self.assertEqual(m["date_of_birth"], days_ago(50))
        self.assertEqual((reply["litter_id"], reply["date_of_birth"]), (m["litter_id"], days_ago(50)))
        # The cohort keeps its date and its other mouse.
        self.assertEqual(one("select date_of_birth from litters where id=?", self.old_litter), days_ago(60))
        self.assertEqual(mouse(self.mate)["litter_id_fk"], self.old_litter)

    def test_the_detached_mouse_stays_in_its_new_litter_on_the_next_save(self):
        r = self.autosave(self.m, update_url(self.mid),
                          sheet_form(self.mid, date_of_birth=days_ago(50), confirm_cohort_dob_change="1"))
        new_litter = mouse(self.mid)["litter_id_fk"]
        # The sheet writes the reply's litter into the row, then saves a note.
        r = self.autosave(self.m, update_url(self.mid), sheet_form(self.mid, note="x"))
        self.assertSaved(r)
        self.assertEqual((mouse(self.mid)["litter_id_fk"], mouse(self.mid)["date_of_birth"]), (new_litter, days_ago(50)))

    def test_saving_the_same_dob_needs_no_confirm(self):
        r = self.autosave(self.m, update_url(self.mid), sheet_form(self.mid, note="same dob"))
        self.assertSaved(r)
        self.assertEqual(mouse(self.mid)["note"], "same dob")

    def test_moving_to_a_new_litter_with_its_own_dob_needs_no_confirm(self):
        code = uniq("L")
        r = self.autosave(self.m, update_url(self.mid), sheet_form(self.mid, litter_id=code, date_of_birth=days_ago(40)))
        self.assertSaved(r)
        self.assertEqual((mouse(self.mid)["litter_id"], mouse(self.mid)["date_of_birth"]), (code, days_ago(40)))
        self.assertEqual(one("select date_of_birth from litters where id=?", self.old_litter), days_ago(60))

    # A litter's DOB is every member's age: joining someone else's litter
    # with a different date keeps the litter's date (services.get_or_create_litter).
    def test_joining_someone_elses_litter_does_not_redate_it(self):
        theirs = self.make_colony(self.o, self.other, n_mice=1, dob=days_ago(90))
        mine = self.make_mouse(self.m, self.member)
        self.autosave(self.m, update_url(mine), sheet_form(mine, litter_id=theirs["litter"], date_of_birth=days_ago(10)))
        self.assertEqual(one("select date_of_birth from litters where id=?", theirs["litter_id"]), days_ago(90))


# ---------------------------------------------------------------- audited moves

class AuditedMoveTests(Case):
    """Cage and litter moves go through the foreign-key columns, so the
    audit log records them with before/after and batch undo can reverse
    them."""

    def test_sheet_cage_change_is_audited_with_before_and_after(self):
        c = self.make_colony(self.m, self.member, n_mice=1)
        mid = c["mice"][0]
        dest = self.make_cage(self.m)
        dest_code = one("select cage_id from mouse_cages where id=?", dest)
        self.assertSaved(self.autosave(self.m, update_url(mid), sheet_form(mid, cage_id=dest_code)))
        action, payload = last_audit("mice", mid)
        self.assertEqual(action, "update")
        self.assertEqual(payload["changes"]["cage_id_fk"], [c["cage_id"], dest])

    def test_sheet_litter_change_is_audited(self):
        c = self.make_colony(self.m, self.member, n_mice=1)
        mid = c["mice"][0]
        code = uniq("L")
        self.assertSaved(self.autosave(self.m, update_url(mid), sheet_form(mid, litter_id=code, date_of_birth="")))
        _, payload = last_audit("mice", mid)
        self.assertEqual(payload["changes"]["litter_id_fk"],
                         [c["litter_id"], one("select id from litters where litter_id=?", code)])

    def test_typing_new_moves_the_mouse_into_a_fresh_cage(self):
        c = self.make_colony(self.m, self.member, n_mice=1)
        mid = c["mice"][0]
        before = count("mouse_cages")
        self.assertSaved(self.autosave(self.m, update_url(mid), sheet_form(mid, cage_id="new")))
        new_cage = mouse(mid)["cage_id_fk"]
        self.assertNotEqual(new_cage, c["cage_id"])
        self.assertEqual(count("mouse_cages"), before + 1)
        self.assertEqual(count("mice", "cage_id_fk=?", new_cage), 1)
        self.assertEqual(last_audit("mice", mid)[1]["changes"]["cage_id_fk"], [c["cage_id"], new_cage])

    def test_adding_an_existing_mouse_to_a_cage_is_audited(self):
        c = self.make_colony(self.m, self.member, n_mice=1)
        mid = c["mice"][0]
        dest = self.make_cage(self.m)
        self.m.post(f"/colony/cages/{dest}/add-mouse", data={"mouse_id": str(mouse(mid)["mouse_id"])})
        self.assertEqual(mouse(mid)["cage_id_fk"], dest)
        self.assertEqual(last_audit("mice", mid)[1]["changes"]["cage_id_fk"], [c["cage_id"], dest])

    def test_batch_cage_move_is_one_batch_and_undo_puts_the_mice_back(self):
        a = self.make_colony(self.m, self.member, n_mice=1)
        b = self.make_colony(self.m, self.member, n_mice=1)
        ids = a["mice"] + b["mice"]
        dest = uniq("C")
        self.make_cage(self.m, dest)
        self.m.post("/colony/mice/bulk-update", data={"field": "cage_id", "value": dest, "selected_ids": ids})
        dest_id = one("select id from mouse_cages where cage_id=?", dest)
        self.assertEqual([mouse(i)["cage_id_fk"] for i in ids], [dest_id, dest_id])
        batch = newest_batch(self.member)
        self.assertEqual(batch[2], 2)
        self.assertEqual(count("audit_log", "batch_id_fk=? and table_name='mice'", batch[0]), 2)
        self.m.post(f"/batches/{batch[0]}/undo")
        self.assertEqual([mouse(i)["cage_id_fk"] for i in ids], [a["cage_id"], b["cage_id"]])
        self.assertIsNotNone(one("select undone_at from batches where id=?", batch[0]))

    def test_batch_move_to_new_puts_the_selection_in_one_owned_cage_and_undo_removes_it(self):
        a = self.make_colony(self.m, self.member, n_mice=2)
        before = count("mouse_cages")
        self.m.post("/colony/mice/bulk-update", data={"field": "cage_id", "value": "new", "selected_ids": a["mice"]})
        cages = {mouse(i)["cage_id_fk"] for i in a["mice"]}
        self.assertEqual(len(cages), 1)
        new_cage = cages.pop()
        self.assertEqual(one("select owner from mouse_cages where id=?", new_cage), self.member)
        self.assertEqual(count("mouse_cages"), before + 1)
        self.m.post(f"/batches/{newest_batch(self.member)[0]}/undo")
        self.assertEqual({mouse(i)["cage_id_fk"] for i in a["mice"]}, {a["cage_id"]})
        self.assertEqual(count("mouse_cages", "id=?", new_cage), 0)

    def test_undo_is_refused_when_a_mouse_changed_after_the_batch(self):
        a = self.make_colony(self.m, self.member, n_mice=1)
        mid = a["mice"][0]
        self.m.post("/colony/mice/bulk-update", data={"field": "cage_id", "value": "new", "selected_ids": [mid]})
        moved_to = mouse(mid)["cage_id_fk"]
        batch = newest_batch(self.member)[0]
        self.assertSaved(self.autosave(self.m, update_url(mid), card_form(mid, note="edited later")))
        self.m.post(f"/batches/{batch}/undo")
        self.assertIn("changed after this batch", flash_texts(self.m, "error"))
        self.assertEqual(mouse(mid)["cage_id_fk"], moved_to)
        self.assertIsNone(one("select undone_at from batches where id=?", batch))

    def test_only_whoever_ran_a_batch_or_an_admin_can_undo_it(self):
        a = self.make_colony(self.m, self.member, n_mice=1)
        mid = a["mice"][0]
        self.m.post("/colony/mice/bulk-update", data={"field": "note", "value": "bulk", "selected_ids": [mid]})
        batch = newest_batch(self.member)[0]
        take_flashes(self.o)
        self.o.post(f"/batches/{batch}/undo")
        self.assertIn("Only whoever ran a batch", flash_texts(self.o, "error"))
        self.assertEqual(mouse(mid)["note"], "bulk")
        self.a.post(f"/batches/{batch}/undo")
        self.assertEqual(mouse(mid)["note"], "")


# ---------------------------------------------------------------- permissions

class MousePermissionTests(Case):
    """app/access.py: your own mice, unowned ones, and any mouse in a
    shared (breeder) cage are editable; admins edit everything."""

    def setUp(self):
        super().setUp()
        self.theirs = self.make_mouse(self.o, self.other, cage=uniq("C"))

    def test_member_cannot_edit_someone_elses_mouse(self):
        r = self.autosave(self.m, update_url(self.theirs), sheet_form(self.theirs, note="hijack"))
        self.assertRefused(r)
        self.assertIn(self.other, r.get_json()["error"])
        self.assertEqual(mouse(self.theirs)["note"], "")

    def test_member_can_edit_a_mouse_in_a_shared_breeder_cage(self):
        cage = uniq("C")
        self.make_cage(self.o, cage, purpose="Breeder")
        mid = self.make_mouse(self.o, self.other, cage=cage)
        self.assertSaved(self.autosave(self.m, update_url(mid), sheet_form(mid, note="picked up")))
        self.assertEqual(mouse(mid)["note"], "picked up")

    def test_member_can_edit_a_mouse_in_a_shared_cage_only(self):
        cage = uniq("C")
        cage_row = self.make_cage(self.o, cage, purpose="Experiments")              # starts personal
        mid = self.make_mouse(self.o, self.other, cage=cage)
        self.assertRefused(self.autosave(self.m, update_url(mid), card_form(mid, note="shared")))
        self.autosave(self.o, f"/colony/cages/{cage_row}/update", {"is_shared": "1"})   # its owner shares it
        self.assertSaved(self.autosave(self.m, update_url(mid), card_form(mid, note="shared")))

    def test_a_breeder_cage_starts_shared(self):
        cage = uniq("C")
        self.make_cage(self.o, cage, purpose="Breeder")
        mid = self.make_mouse(self.o, self.other, cage=cage)
        self.assertSaved(self.autosave(self.m, update_url(mid), card_form(mid, note="shared")))

    def test_admin_can_edit_anyones_mouse(self):
        self.assertSaved(self.autosave(self.a, update_url(self.theirs), sheet_form(self.theirs, note="admin")))
        self.assertEqual(mouse(self.theirs)["note"], "admin")

    def test_member_cannot_move_someone_elses_mouse_into_their_own_cage(self):
        mine = self.make_cage(self.m)
        before = mouse(self.theirs)["cage_id_fk"]
        self.m.post(f"/colony/cages/{mine}/add-mouse", data={"mouse_id": str(mouse(self.theirs)["mouse_id"])})
        self.assertIn("not moved", flash_texts(self.m, "error"))
        self.assertEqual(mouse(self.theirs)["cage_id_fk"], before)

    def test_member_cannot_delete_someone_elses_mouse(self):
        self.m.post(f"/colony/mice/{self.theirs}/delete")
        self.assertEqual(count("mice", "id=?", self.theirs), 1)

    def test_member_cannot_duplicate_someone_elses_mouse(self):
        before = count("mice")
        self.m.post(f"/colony/mice/{self.theirs}/duplicate")
        self.assertEqual(count("mice"), before)
        self.assertIn(self.other, flash_texts(self.m, "error"))

    def test_member_batch_edit_skips_mice_that_are_not_theirs(self):
        mine = self.make_mouse(self.m, self.member)
        self.m.post("/colony/mice/bulk-update",
                    data={"field": "note", "value": "bulk by member", "selected_ids": [mine, self.theirs]})
        self.assertEqual(mouse(mine)["note"], "bulk by member")
        self.assertEqual(mouse(self.theirs)["note"], "")
        self.assertIn("1 skipped", flash_texts(self.m, "success"))

    def test_member_batch_sac_skips_mice_that_are_not_theirs(self):
        mine = self.make_mouse(self.m, self.member)
        self.m.post("/colony/mice/bulk-sac", data={"selected_ids": [mine, self.theirs]})
        self.assertEqual((mouse(mine)["status"], mouse(mine)["date_of_death"]), ("sac", T))
        self.assertEqual((mouse(self.theirs)["status"], mouse(self.theirs)["date_of_death"]), ("experiment", ""))

    def test_member_cannot_weigh_someone_elses_mouse(self):
        r = self.m.post(f"/colony/mice/{self.theirs}/weights/create", data={"grams": "20"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(count("mouse_weights", "mouse_id_fk=?", self.theirs), 0)

    def test_sheet_shows_someone_elses_mouse_read_only(self):
        html = self.get_ok(self.m, "/colony?view=mice&scope=all")
        start = html.index(f'<tr data-id="{self.theirs}"')
        tr = html[start:html.index("</tr>", start)]
        self.assertIn("is-locked", tr)
        self.assertIn("sheet-lock", tr)
        self.assertRegex(tr, r'name="note"[^>]*disabled')


# ---------------------------------------------------------------- batch edits

class MouseBatchEditTests(Case):

    def setUp(self):
        super().setUp()
        self.ids = self.make_colony(self.m, self.member, n_mice=2)["mice"]

    def bulk(self, field, value, ids=None):
        return self.m.post("/colony/mice/bulk-update",
                           data={"field": field, "value": value, "selected_ids": ids or self.ids})

    def test_batch_sac_stamps_today_and_back_to_experiment_clears_it(self):
        self.bulk("status", "sac")
        self.assertEqual([mouse(i)["date_of_death"] for i in self.ids], [T, T])
        self.bulk("status", "experiment")
        self.assertEqual([mouse(i)["date_of_death"] for i in self.ids], ["", ""])

    def test_undoing_a_batch_sac_restores_status_and_date(self):
        self.bulk("status", "sac")
        batch = newest_batch(self.member)
        self.assertEqual(batch[1], "set status = sac")
        self.m.post(f"/batches/{batch[0]}/undo")
        self.assertEqual([(mouse(i)["status"], mouse(i)["date_of_death"]) for i in self.ids],
                         [("experiment", ""), ("experiment", "")])

    def test_batch_transgenes_fill_the_transgene_columns(self):
        self.bulk("genotype", "Ai14; Cre", self.ids[:1])
        m = mouse(self.ids[0])
        self.assertEqual((m["transgene_1"], m["transgene_2"], m["genotype"]), ("Ai14", "Cre", "Ai14; Cre"))

    def test_batch_owner_must_be_a_lab_member(self):
        batches_before = count("batches", "actor=?", self.member)
        self.bulk("owner", uniq("nobody"))
        self.assertIn("not a lab member", flash_texts(self.m, "error"))
        self.assertEqual({mouse(i)["owner"] for i in self.ids}, {self.member})
        self.assertEqual(count("batches", "actor=?", self.member), batches_before)

    def test_batch_owner_hands_the_mice_to_another_member(self):
        self.bulk("owner", self.other)
        self.assertEqual({mouse(i)["owner"] for i in self.ids}, {self.other})

    def test_batch_date_of_death_must_be_a_date(self):
        self.bulk("date_of_death", "yesterday")
        self.assertIn("not a date", flash_texts(self.m, "error"))
        self.assertEqual({mouse(i)["date_of_death"] for i in self.ids}, {""})

    def test_batch_edit_of_an_unknown_field_is_refused(self):
        self.bulk("mouse_id", "1")
        self.assertIn("Pick a field", flash_texts(self.m, "error"))

    def test_batch_sac_route_stamps_today_in_one_batch(self):
        self.m.post("/colony/mice/bulk-sac", data={"selected_ids": self.ids})
        self.assertEqual({(mouse(i)["status"], mouse(i)["date_of_death"]) for i in self.ids}, {("sac", T)})
        batch = newest_batch(self.member)
        self.assertEqual((batch[1], batch[2]), ("mark as sac", 2))

    def csv_import(self, text, dry_run):
        import io
        return self.a.post("/import/mouse", data={"file": (io.BytesIO(text.encode()), "mice.csv"),
                                                  "dry_run": "1" if dry_run else "0"},
                           content_type="multipart/form-data").get_json()

    def test_a_csv_with_a_repeated_mouse_id_is_caught_in_the_dry_run(self):
        top = one("select max(mouse_id) from mice")
        tag = uniq("CSV")
        text = (f"mouse_id,genotype\n{top + 50},{tag}\n{top + 50},{tag}\n,{tag}\n{top + 51},{tag}\n")
        dry = self.csv_import(text, True)
        self.assertEqual(dry["count"], 3)
        self.assertTrue(any(f"mouse_id {top + 50} already exists" in e for e in dry["errors"]))
        done = self.csv_import(text, False)
        self.assertEqual(done["count"], 3)
        self.assertEqual(count("mice", "genotype=?", tag), 3)
        self.assertEqual(last_batch()[1], "import mice from mice.csv")   # Batch history can undo it

    def test_new_mouse_numbers_are_never_handed_out_twice(self):
        top = one("select max(mouse_id) from mice")
        self.m.post("/colony/mice/new-record")                      # a higher number than the one below
        top = one("select max(mouse_id) from mice")
        execute("update mice set created_at=? where id=?",          # a low number, entered last
                datetime.utcnow() + timedelta(days=1), self.ids[0])
        self.m.post("/colony/mice/new-record")
        made = one("select max(mouse_id) from mice")
        self.assertEqual(made, top + 1)
        execute("delete from mice where mouse_id=?", made)          # the newest mouse deleted
        self.m.post("/colony/mice/new-record")
        self.assertEqual(one("select max(mouse_id) from mice"), top + 2)

    def test_new_mouse_saved_at_the_same_moment_as_another_gets_the_next_number(self):
        from unittest import mock
        import app.app as app_module
        taken = one("select mouse_id from mice where id=?", self.ids[0])
        real = app_module.next_mouse_id
        calls = iter([taken])            # the number another save took a moment ago
        with mock.patch.object(app_module, "next_mouse_id", lambda s: next(calls, None) or real(s)):
            r = self.m.post("/colony/mice/create", data={"owner": self.member, "status": "experiment",
                                                         "note": "same moment"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(count("mice", "note=?", "same moment"), 1)
        self.assertNotIn("already used", " ".join(flash_texts(self.m, "error")))

    def test_a_row_open_since_before_a_colleague_s_edit_does_not_undo_it(self):
        mid = self.ids[0]
        shown = sheet_form(mid)
        opened = {**shown, **{f"{k}_was": v for k, v in shown.items() if k in (
            "gender", "status", "transgene_1", "owner", "note", "cage_id", "litter_id", "date_of_death")}}
        self.autosave(self.a, update_url(mid), sheet_form(mid, status="geno"))    # a colleague, meanwhile
        self.autosave(self.m, update_url(mid), {**opened, "note": "weighed"})     # the open row: only the note
        self.assertEqual((mouse(mid)["status"], mouse(mid)["note"]), ("geno", "weighed"))

    def test_saving_another_cell_keeps_a_note_s_line_breaks(self):
        mid = self.ids[0]
        execute("update mice set note=?, transgene_1=? where id=?", "Healthy\nCoat: black", "Cre\n(het)", mid)
        # The sheet's one-line cells send the text without its line breaks.
        self.autosave(self.m, update_url(mid), sheet_form(mid, gender="F", note="HealthyCoat: black",
                                                          transgene_1="Cre(het)"))
        self.assertEqual(one("select note from mice where id=?", mid), "Healthy\nCoat: black")
        self.assertEqual(one("select transgene_1 from mice where id=?", mid), "Cre\n(het)")
        self.assertEqual(one("select gender from mice where id=?", mid), "F")
        self.autosave(self.m, update_url(mid), sheet_form(mid, note="Rewritten"))
        self.assertEqual(one("select note from mice where id=?", mid), "Rewritten")

    def test_batch_sac_keeps_the_day_a_mouse_already_died(self):
        earlier = date.today() - timedelta(days=3)
        execute("update mice set status='sac', date_of_death=? where id=?", earlier, self.ids[0])
        self.m.post("/colony/mice/bulk-sac", data={"selected_ids": self.ids})
        self.assertEqual(mouse(self.ids[0])["date_of_death"], earlier.isoformat())
        self.assertEqual(mouse(self.ids[1])["date_of_death"], T)


# ---------------------------------------------------------------- lifecycle

class MouseLifecycleTests(Case):

    def test_duplicate_copies_cage_litter_and_transgenes_as_a_living_mouse(self):
        c = self.make_colony(self.m, self.member, n_mice=1)
        mid = c["mice"][0]
        self.autosave(self.m, update_url(mid), sheet_form(mid, transgene_1="Ai14", status="sac"))
        before = one("select max(id) from mice")
        self.m.post(f"/colony/mice/{mid}/duplicate")
        dup = one("select max(id) from mice where id>?", before)
        self.assertIsNotNone(dup)
        d, src = mouse(dup), mouse(mid)
        self.assertNotEqual(d["mouse_id"], src["mouse_id"])
        self.assertEqual((d["cage_id_fk"], d["litter_id_fk"], d["transgene_1"], d["owner"]),
                         (c["cage_id"], c["litter_id"], "Ai14", self.member))
        self.assertEqual(d["date_of_death"], "")

    def test_delete_removes_the_mouse_with_its_weights_and_is_audited(self):
        mid = self.make_mouse(self.m, self.member)
        self.m.post(f"/colony/mice/{mid}/weights/create", data={"grams": "20.5"})
        self.m.post(f"/colony/mice/{mid}/delete")
        self.assertEqual(count("mice", "id=?", mid), 0)
        self.assertEqual(count("mouse_weights", "mouse_id_fk=?", mid), 0)
        action, payload = last_audit("mice", mid)
        self.assertEqual(action, "delete")
        self.assertEqual(payload["snapshot"]["id"], mid)

    def test_a_weight_per_day_is_upserted(self):
        mid = self.make_mouse(self.m, self.member)
        url = f"/colony/mice/{mid}/weights/create"
        self.m.post(url, data={"grams": "21.5", "weigh_date": days_ago(2)})
        self.m.post(url, data={"grams": "22.0", "weigh_date": days_ago(2)})
        self.m.post(url, data={"grams": "23.0", "weigh_date": days_ago(1)})
        self.assertEqual(rows("select weigh_date, grams from mouse_weights where mouse_id_fk=? order by weigh_date", mid),
                         [(days_ago(2), 22.0), (days_ago(1), 23.0)])

    def test_a_non_numeric_weight_is_a_400(self):
        mid = self.make_mouse(self.m, self.member)
        r = self.m.post(f"/colony/mice/{mid}/weights/create", data={"grams": "heavy"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()["ok"])

    def test_a_weight_can_be_deleted(self):
        mid = self.make_mouse(self.m, self.member)
        self.m.post(f"/colony/mice/{mid}/weights/create", data={"grams": "20"})
        wid = one("select id from mouse_weights where mouse_id_fk=?", mid)
        self.m.post(f"/colony/mice/{mid}/weights/{wid}/delete")
        self.assertEqual(count("mouse_weights", "id=?", wid), 0)

    def test_transfer_to_another_member_gives_them_a_copy(self):
        c = self.make_colony(self.m, self.member, n_mice=1)
        mid = c["mice"][0]
        before = one("select max(id) from mice")
        r = self.autosave(self.m, update_url(mid), sheet_form(mid, status="transfer", owner=self.other))
        self.assertSaved(r)
        src = mouse(mid)
        self.assertEqual(src["owner"], self.member)
        self.assertIn(f"Transferred to {self.other}", src["note"])
        copy = one("select max(id) from mice where id>?", before)
        self.assertIsNotNone(copy)
        dst = mouse(copy)
        self.assertEqual((dst["owner"], dst["status"], dst["litter_id_fk"]), (self.other, "experiment", c["litter_id"]))
        self.assertNotEqual(dst["cage_id_fk"], c["cage_id"])
        self.assertEqual(count("notifications", "recipient_username=? and title='Mouse transfer received'", self.other), 1)

    # The mouse sheet follows the cage's own rule ("Add existing mouse",
    # wean distribute): a private cage takes mice only from its owner or an admin.
    def test_member_cannot_move_own_mouse_into_someone_elses_private_cage(self):
        theirs = uniq("C")
        self.make_cage(self.o, theirs, purpose="Experiments")
        mid = self.make_mouse(self.m, self.member, cage=uniq("C"))
        self.autosave(self.m, update_url(mid), sheet_form(mid, cage_id=theirs))
        self.assertNotEqual(mouse(mid)["cage_id"], theirs)


if __name__ == "__main__":
    unittest.main()
