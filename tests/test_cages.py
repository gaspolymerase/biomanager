"""Cages: creating (one or "How many"), the next free cage ID, the cage
sheet and its per-cage mouse rows, cage edits and who may make them, batch
actions and their undo, racks and placing cages in them."""
from __future__ import annotations

import re
import unittest
from datetime import timedelta

from tests.base import *  # noqa: F401,F403
from tests.base import AppTestCase, SessionLocal, TODAY, T, count, execute, one, row, rows, services, uniq


# ---------------------------------------------------------------- helpers

def cage(cage_row: int) -> dict:
    r = row("""select cage_id, owner, purpose, notes, cage_location, rack_id_fk, rack_row, rack_col,
                      is_shared, card_id, room, date_give_birth from mouse_cages where id=?""", cage_row)
    keys = ["cage_id", "owner", "purpose", "notes", "cage_location", "rack_id_fk", "rack_row", "rack_col",
            "is_shared", "card_id", "room", "date_give_birth"]
    return dict(zip(keys, r)) if r else {}


def cage_id_of(code: str):
    return one("select id from mouse_cages where cage_id=?", code)


def highest_cage_number() -> int:
    return max((services._cage_number(code) for (code,) in rows("select cage_id from mouse_cages")), default=0)


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


class Case(AppTestCase):
    """Starts each test with no flashes left over from an earlier one on
    the class's shared clients."""

    def setUp(self):
        super().setUp()
        for client in (self.a, self.m, self.o):
            take_flashes(client)


def update_url(cage_row: int) -> str:
    return f"/colony/cages/{cage_row}/update"


def between(html: str, start_marker: str, end_marker: str) -> str:
    start = html.index(start_marker)
    return html[start:html.index(end_marker, start)]


class RackMixin:
    @staticmethod
    def make_rack(client, name: str | None = None, rows_: int = 3, cols: int = 3) -> int:
        name = name or uniq("Rack")
        client.post("/colony/racks/save", data={"id": "", "name": name, "rows": str(rows_), "cols": str(cols)})
        found = one("select id from mouse_racks where name=?", name)
        assert found, f"rack {name} was not created"
        return found


# ---------------------------------------------------------------- creating

class CageCreateTests(RackMixin, Case):
    """Creating never edits an existing cage; a blank ID (or "new") takes
    the next number above the highest numbered cage."""

    def test_blank_id_takes_the_number_after_the_highest_cage(self):
        expected = str(highest_cage_number() + 1)
        self.m.post("/colony/cages/create", data={"cage_id": "", "purpose": "Experiments"})
        self.assertEqual(cage(cage_id_of(expected))["owner"], self.member)
        self.assertIn(f"Created cage {expected}", flash_texts(self.m, "success"))

    def test_new_takes_the_next_number_too(self):
        expected = str(highest_cage_number() + 1)
        self.m.post("/colony/cages/create", data={"cage_id": "new"})
        self.assertIsNotNone(cage_id_of(expected))

    def test_a_low_numbered_latest_cage_does_not_pull_the_next_id_down(self):
        high = highest_cage_number()
        # Typed on a mouse, the latest cage is "2A…": its number is 2.
        self.make_mouse(self.m, self.member, cage="2A" + uniq("z"))
        with SessionLocal() as s:
            self.assertEqual(services.next_cage_id(s), str(high + 1))
        before = count("mice", "cage_id_fk=(select id from mouse_cages where cage_id='3')")
        mid = self.make_mouse(self.m, self.member, cage="new")
        self.assertEqual(one("select c.cage_id from mice m join mouse_cages c on c.id=m.cage_id_fk where m.id=?", mid),
                         str(high + 1))
        self.assertEqual(count("mice", "cage_id_fk=(select id from mouse_cages where cage_id='3')"), before)

    def test_reserve_cage_ids_gives_consecutive_unused_ids(self):
        high = highest_cage_number()
        with SessionLocal() as s:
            self.assertEqual(services.reserve_cage_ids(s, 3), [str(high + i) for i in (1, 2, 3)])
            self.assertEqual(services.reserve_cage_ids(s, 0), [])

    def test_cage_number_reads_the_first_run_of_digits(self):
        self.assertEqual([services._cage_number(c) for c in ("12", "2A", "B-12", "B12-3", "x", "", None)],
                         [12, 2, 12, 12, 0, 0, 0])

    def test_an_existing_id_is_refused_and_the_cage_left_alone(self):
        code = uniq("C")
        row_id = self.make_cage(self.m, code, purpose="Breeding", notes="keep")
        take_flashes(self.m)
        self.m.post("/colony/cages/create", data={"cage_id": code, "purpose": "Experiments", "notes": "stale form"})
        self.assertIn("already exists", flash_texts(self.m, "error"))
        self.assertEqual((cage(row_id)["purpose"], cage(row_id)["notes"]), ("Breeding", "keep"))
        self.assertEqual(count("mouse_cages", "cage_id=?", code), 1)

    def test_autosave_create_of_an_existing_id_answers_409(self):
        code = uniq("C")
        self.make_cage(self.m, code)
        r = self.autosave(self.m, "/colony/cages/create", {"cage_id": code})
        self.assertRefused(r)
        self.assertIn("already exists", r.get_json()["error"])

    def test_member_cannot_take_over_someone_elses_cage_by_creating_it(self):
        code = uniq("C")
        row_id = self.make_cage(self.o, code, purpose="Experiments")
        self.m.post("/colony/cages/create", data={"cage_id": code, "purpose": "mine now"})
        self.assertEqual((cage(row_id)["owner"], cage(row_id)["purpose"]), (self.other, "Experiments"))

    def test_count_one_autosave_answers_ok(self):
        high = highest_cage_number()
        self.assertSaved(self.autosave(self.m, "/colony/cages/create", {"cage_id": "", "count": "1"}))
        self.assertIsNotNone(cage_id_of(str(high + 1)))

    def test_a_single_cage_at_a_taken_position_is_created_but_not_placed(self):
        rack = self.make_rack(self.m)
        self.make_cage(self.m, rack_id=str(rack), position="A1")
        code = uniq("C")
        take_flashes(self.m)
        self.m.post("/colony/cages/create", data={"cage_id": code, "rack_id": str(rack), "position": "A1"})
        self.assertIn("not placed", flash_texts(self.m, "error"))
        self.assertIsNone(cage(cage_id_of(code))["rack_row"])


class HowManyCagesTests(RackMixin, Case):
    """The New cage dialog's "How many" (1–20)."""

    def test_several_cages_get_consecutive_ids_in_one_batch(self):
        high = highest_cage_number()
        self.m.post("/colony/cages/create", data={"cage_id": "", "count": "3", "purpose": "Experiments"})
        codes = [str(high + i) for i in (1, 2, 3)]
        self.assertEqual([cage(cage_id_of(c))["owner"] for c in codes], [self.member] * 3)
        batch = newest_batch(self.member)
        self.assertEqual((batch[1], batch[2]), ("add 3 cages", 3))
        self.assertIn("3 cages", flash_texts(self.m, "success"))

    def test_undo_removes_every_cage_of_the_run(self):
        high = highest_cage_number()
        self.m.post("/colony/cages/create", data={"cage_id": "", "count": "3"})
        self.m.post(f"/batches/{newest_batch(self.member)[0]}/undo")
        self.assertEqual(count("mouse_cages", "cage_id in (?,?,?)", *[str(high + i) for i in (1, 2, 3)]), 0)

    def test_a_count_outside_1_to_20_is_refused(self):
        for raw in ("21", "0", "-1", "abc"):
            with self.subTest(count=raw):
                before = count("mouse_cages")
                self.m.post("/colony/cages/create", data={"cage_id": "", "count": raw})
                self.assertIn("between 1 and 20", flash_texts(self.m, "error"))
                self.assertEqual(count("mouse_cages"), before)

    def test_a_typed_id_starts_the_run(self):
        start = highest_cage_number() + 50
        self.m.post("/colony/cages/create", data={"cage_id": str(start), "count": "2"})
        self.assertEqual(count("mouse_cages", "cage_id in (?,?)", str(start), str(start + 1)), 2)

    def test_a_run_that_hits_an_existing_cage_is_refused_whole(self):
        start = highest_cage_number() + 50
        self.make_cage(self.m, str(start + 1))
        take_flashes(self.m)
        self.m.post("/colony/cages/create", data={"cage_id": str(start), "count": "3"})
        self.assertIn("already exists", flash_texts(self.m, "error"))
        self.assertEqual(count("mouse_cages", "cage_id in (?,?)", str(start), str(start + 2)), 0)

    def test_a_non_numeric_id_with_several_is_refused(self):
        code = uniq("X")
        self.m.post("/colony/cages/create", data={"cage_id": code, "count": "2"})
        self.assertIn("first number", flash_texts(self.m, "error"))
        self.assertEqual(count("mouse_cages", "cage_id=?", code), 0)

    def test_several_into_a_rack_take_the_free_positions_from_the_one_given(self):
        rack = self.make_rack(self.m, rows_=3, cols=3)
        self.make_cage(self.m, rack_id=str(rack), position="C1")  # taken: skipped over
        high = highest_cage_number()
        self.m.post("/colony/cages/create", data={"cage_id": "", "count": "3", "rack_id": str(rack), "position": "B3"})
        placed = [(cage(cage_id_of(str(high + i)))["rack_row"], cage(cage_id_of(str(high + i)))["rack_col"])
                  for i in (1, 2, 3)]
        self.assertEqual(placed, [(2, 3), (3, 2), (3, 3)])

    def test_several_into_a_full_rack_are_made_and_the_shortfall_reported(self):
        rack = self.make_rack(self.m, rows_=1, cols=2)
        high = highest_cage_number()
        self.m.post("/colony/cages/create", data={"cage_id": "", "count": "3", "rack_id": str(rack), "position": "A1"})
        self.assertIn("had room for 2 of 3", flash_texts(self.m, "error"))
        made = [cage(cage_id_of(str(high + i))) for i in (1, 2, 3)]
        self.assertEqual([c["rack_id_fk"] for c in made], [rack] * 3)
        self.assertEqual([c["rack_col"] for c in made], [1, 2, None])

    def test_several_into_a_rack_without_a_position_are_unplaced_in_it(self):
        rack = self.make_rack(self.m)
        high = highest_cage_number()
        self.m.post("/colony/cages/create", data={"cage_id": "", "count": "2", "rack_id": str(rack)})
        made = [cage(cage_id_of(str(high + i))) for i in (1, 2)]
        self.assertEqual([(c["rack_id_fk"], c["rack_row"]) for c in made], [(rack, None), (rack, None)])


# ---------------------------------------------------------------- editing

class CageUpdateTests(RackMixin, Case):
    """A cage form only changes what it sends; rack, position, location
    and owner only when they differ from the row's `_was` copy."""

    def setUp(self):
        super().setUp()
        self.cage = self.make_cage(self.m, purpose="Experiments", cage_location="Room 1")

    def test_notes_only_save_answers_the_row_and_leaves_the_rest(self):
        r = self.autosave(self.m, update_url(self.cage), {"notes": "sheet note"})
        self.assertSaved(r)
        j = r.get_json()
        self.assertEqual(j["row"]["values"]["notes"], "sheet note")
        self.assertIsInstance(j["row"]["active"], bool)
        self.assertEqual(j["cage"]["cage_id"], cage(self.cage)["cage_id"])
        self.assertEqual((cage(self.cage)["purpose"], cage(self.cage)["cage_location"], cage(self.cage)["owner"]),
                         ("Experiments", "Room 1", self.member))

    def test_a_stale_location_does_not_undo_a_change_made_elsewhere(self):
        self.autosave(self.m, update_url(self.cage), {"cage_location": "Room 2", "cage_location_was": "Room 1"})
        r = self.autosave(self.m, update_url(self.cage),
                          {"cage_location": "Room 1", "cage_location_was": "Room 1", "notes": "n2"})
        self.assertEqual((cage(self.cage)["cage_location"], cage(self.cage)["notes"]), ("Room 2", "n2"))
        self.assertEqual(r.get_json()["row"]["values"]["cage_location"], "Room 2")

    def test_a_stale_rack_and_position_do_not_unplace_the_cage(self):
        rack = self.make_rack(self.m)
        self.autosave(self.m, update_url(self.cage), {"rack_id": str(rack), "position": "B2"})
        self.autosave(self.m, update_url(self.cage),
                      {"rack_id": "", "rack_id_was": "", "position": "", "position_was": "", "notes": "n3"})
        self.assertEqual((cage(self.cage)["rack_id_fk"], cage(self.cage)["rack_row"], cage(self.cage)["rack_col"]),
                         (rack, 2, 2))

    def test_a_position_edit_places_the_cage_and_answers_its_label(self):
        rack = self.make_rack(self.m)
        r = self.autosave(self.m, update_url(self.cage),
                          {"rack_id": str(rack), "rack_id_was": str(rack), "position": "c3", "position_was": ""})
        self.assertSaved(r)
        self.assertEqual((cage(self.cage)["rack_row"], cage(self.cage)["rack_col"]), (3, 3))
        self.assertEqual((r.get_json()["row"]["values"]["position"], r.get_json()["cage"]["position"]), ("C3", "C3"))

    def test_a_taken_position_is_refused(self):
        rack = self.make_rack(self.m)
        holder = self.make_cage(self.m, rack_id=str(rack), position="A1")
        r = self.autosave(self.m, update_url(self.cage), {"rack_id": str(rack), "position": "A1"})
        self.assertRefused(r)
        self.assertIn(f"already holds cage {cage(holder)['cage_id']}", r.get_json()["error"])
        self.assertIsNone(cage(self.cage)["rack_id_fk"])

    def test_a_position_outside_the_rack_is_refused(self):
        rack = self.make_rack(self.m, rows_=3, cols=3)
        r = self.autosave(self.m, update_url(self.cage), {"rack_id": str(rack), "position": "D9"})
        self.assertRefused(r)
        self.assertIn("not a position", r.get_json()["error"])

    def test_the_owner_must_be_a_lab_member(self):
        r = self.autosave(self.m, update_url(self.cage), {"owner": uniq("nobody"), "owner_was": self.member})
        self.assertRefused(r)
        self.assertIn("not a lab member", r.get_json()["error"])
        self.assertEqual(cage(self.cage)["owner"], self.member)

    def test_the_owner_can_hand_the_cage_over(self):
        r = self.autosave(self.m, update_url(self.cage), {"owner": self.other, "owner_was": self.member})
        self.assertEqual(r.get_json()["row"]["values"]["owner"], self.other)
        self.assertEqual(cage(self.cage)["owner"], self.other)

    def test_a_breeder_cage_starts_shared_and_its_owner_can_make_it_personal(self):
        # Breeder: the lab's breeding stock, shared; not a mating cage.
        values = self.autosave(self.m, update_url(self.cage), {"purpose": "Breeder"}).get_json()["row"]["values"]
        self.assertEqual((values["breeding"], values["is_shared"], values["share_lock"]), ("0", "1", ""))
        values = self.autosave(self.m, update_url(self.cage), {"is_shared": "0"}).get_json()["row"]["values"]
        self.assertEqual((values["is_shared"], cage(self.cage)["is_shared"]), ("0", 0))

    def test_only_a_breeding_cage_is_a_mating_cage(self):
        # Breeding: mating now, the one cage with Litter born, Genotyping and Wean.
        for purpose, mating in (("Breeding", "1"), ("breeding ", "1"), ("Breeder", "0"), ("Experiment", "0"), ("", "0")):
            with self.subTest(purpose=purpose):
                values = self.autosave(self.m, update_url(self.cage), {"purpose": purpose}).get_json()["row"]["values"]
                self.assertEqual(values["breeding"], mating)

    def test_any_cage_starts_personal_and_its_owner_can_share_it(self):
        # Only a breeder cage starts shared; any other its owner may share.
        for purpose in ("Breeding", "Stock", "Experiments"):
            with self.subTest(purpose=purpose):
                values = self.autosave(self.m, update_url(self.cage), {"purpose": purpose}).get_json()["row"]["values"]
                self.assertEqual((values["is_shared"], values["share_lock"]), ("0", ""))
                values = self.autosave(self.m, update_url(self.cage), {"is_shared": "1"}).get_json()["row"]["values"]
                self.assertEqual(values["is_shared"], "1")
                self.autosave(self.m, update_url(self.cage), {"is_shared": "0"})
        self.assertEqual(values["breeding"], "0")

    def test_a_cage_that_stops_being_a_breeder_starts_personal_again(self):
        self.autosave(self.m, update_url(self.cage), {"purpose": "Breeder"})
        self.assertEqual(cage(self.cage)["is_shared"], 1)
        self.autosave(self.m, update_url(self.cage), {"purpose": "Experiments"})
        self.assertEqual(cage(self.cage)["is_shared"], 0)

    def test_someone_else_cannot_share_your_cage(self):
        r = self.autosave(self.o, update_url(self.cage), {"is_shared": "1"})
        self.assertNotEqual(r.status_code, 200)
        self.assertEqual(cage(self.cage)["is_shared"], 0)

    def test_a_litter_born_today_is_due_to_wean_in_21_days(self):
        values = self.autosave(self.m, update_url(self.cage), {"date_give_birth": T}).get_json()["row"]["values"]
        self.assertEqual(values["wean_due"], (TODAY + timedelta(days=21)).isoformat())

    def test_a_dialog_save_writes_every_field_it_carries(self):
        rack = self.make_rack(self.m)
        self.autosave(self.m, update_url(self.cage), {"rack_id": str(rack), "position": "A2"})
        self.m.post(update_url(self.cage), data={
            "rack_id": "", "position": "", "cage_location": "Dialog room", "purpose": "Stock", "notes": "",
            "date_give_birth": "", "card_id": "K1", "genotype_summary": "", "location_detail": "", "room": "204"})
        c = cage(self.cage)
        self.assertEqual((c["rack_id_fk"], c["cage_location"], c["purpose"], c["card_id"], c["room"]),
                         (None, "Dialog room", "Stock", "K1", "204"))

    def test_member_cannot_edit_someone_elses_cage(self):
        theirs = self.make_cage(self.o, purpose="Experiments")
        r = self.autosave(self.m, update_url(theirs), {"notes": "member"})
        self.assertRefused(r)
        self.assertIn(self.other, r.get_json()["error"])
        self.assertEqual(cage(theirs)["notes"], "")

    def test_member_can_edit_a_shared_breeder_cage(self):
        theirs = self.make_cage(self.o, purpose="Breeder")
        self.assertSaved(self.autosave(self.m, update_url(theirs), {"notes": "fed"}))
        self.assertEqual(cage(theirs)["notes"], "fed")

    def test_only_its_owner_or_an_admin_makes_a_breeder_cage_personal_or_gives_it_away(self):
        theirs = self.make_cage(self.o, purpose="Breeder")
        r = self.autosave(self.m, update_url(theirs), {"is_shared": "0"})
        self.assertRefused(r)
        self.assertIn("shared or personal", r.get_json()["error"])
        r = self.autosave(self.m, update_url(theirs), {"owner": self.member, "owner_was": self.other})
        self.assertRefused(r)
        self.assertEqual((cage(theirs)["is_shared"], cage(theirs)["owner"]), (1, self.other))
        row = between(self.get_ok(self.m, "/colony?view=cages&scope=all"), f'<tr id="cage-{theirs}"', "</tr>")
        self.assertRegex(row, r'name="is_shared"[^>]*data-share-lock="Only [^"]+ or an admin can change this"[^>]*disabled')
        self.assertSaved(self.autosave(self.o, update_url(theirs), {"is_shared": "0"}))        # its owner may
        self.assertEqual(cage(theirs)["is_shared"], 0)
        # Personal now, so it is the owner's alone again.
        self.assertRefused(self.autosave(self.m, update_url(theirs), {"notes": "member"}))
        self.assertSaved(self.autosave(self.a, update_url(theirs), {"is_shared": "1"}))        # and an admin
        self.assertEqual(cage(theirs)["is_shared"], 1)

    def test_admin_can_edit_anyones_cage(self):
        theirs = self.make_cage(self.o, purpose="Experiments")
        self.assertSaved(self.autosave(self.a, update_url(theirs), {"notes": "admin"}))

    def test_saving_a_cage_that_no_longer_exists_is_refused(self):
        missing = (one("select max(id) from mouse_cages") or 0) + 1000
        r = self.autosave(self.m, update_url(missing), {"notes": "x"})
        self.assertRefused(r)
        self.assertIn("no longer exists", r.get_json()["error"])

    def test_member_cannot_add_a_mouse_to_someone_elses_cage(self):
        theirs = self.make_cage(self.o, purpose="Experiments")
        mid = self.make_mouse(self.m, self.member)
        self.m.post(f"/colony/cages/{theirs}/add-mouse",
                    data={"mouse_id": str(one("select mouse_id from mice where id=?", mid))})
        self.assertNotEqual(one("select cage_id_fk from mice where id=?", mid), theirs)

    def test_adding_a_mouse_that_does_not_exist_is_a_message(self):
        missing = (one("select max(mouse_id) from mice") or 0) + 1000
        self.m.post(f"/colony/cages/{self.cage}/add-mouse", data={"mouse_id": str(missing)})
        self.assertIn("was not found", flash_texts(self.m, "error"))


# ---------------------------------------------------------------- the sheet

class CageSheetTests(Case):
    """The Cages tab: one sheet row per cage, a sub-row with its mice.
    Rendered and posted with a user of its own, so the page stays small."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.colony = cls.make_colony(cls, cls.m, cls.member, n_mice=2, cage_location="Shelf 3")
        cls.theirs = cls.make_cage(cls.o, purpose="Experiments")

    def setUp(self):
        super().setUp()
        self.html = self.get_ok(self.m, "/colony?view=cages&scope=all")

    def test_the_sheet_renders_as_an_autosaving_table(self):
        self.assertIn('data-table-id="cages-v3"', self.html)
        self.assertIn("data-autosave-sheet", self.html)
        self.assertIn('data-selection-scope="cages"', self.html)
        self.assertRegex(self.html, r'<label data-new-only>How many\s*<input type="number" name="count" min="1" max="20"')

    def test_each_cage_has_a_row_a_form_and_a_detail_row_with_its_mice(self):
        cid = self.colony["cage_id"]
        self.assertIn(f'<tr id="cage-{cid}"', self.html)
        self.assertIn(f'<form id="cage-update-{cid}"', self.html)
        detail = between(self.html, f'id="cage-detail-{cid}"', "</article>")
        self.assertIn("data-add-existing-mouse", detail)
        for mid in self.colony["mice"]:
            self.assertIn(f'<form id="cage-mouse-update-{mid}"', self.html)

    def test_a_cage_opens_from_its_row_and_shows_as_a_card(self):
        cid = self.colony["cage_id"]
        tr = between(self.html, f'<tr id="cage-{cid}"', "</tr>")
        # The arrow is at the start of the row, by the cage's number; the
        # number and the mice count open it as well.
        first_cells = between(tr, 'sheet-pin-1', "</td>")
        self.assertIn(f'data-cage-expand="{cid}"', first_cells)
        self.assertIn(f'data-cage-open="{cid}"', tr)
        self.assertIn("data-cage-expand-all", self.html)                   # Close all / Open all
        # Each cage shows open, its mice beneath it, until someone chooses Close all.
        self.assertIn(f'<tr class="cage-detail" id="cage-detail-{cid}" data-detail-for="{cid}">', self.html)
        self.assertIn(f'data-cage-expand="{cid}" aria-expanded="true"', tr)
        # Cards: a layout beside Table and Rack grid; each cage's panel is a
        # card with what it is and its own buttons, and filters of its own.
        self.assertIn('data-layout="cards"', self.html)
        self.assertIn('data-layout-panel="cards"', self.html)
        self.assertIn("data-cage-cards", self.html)
        detail = between(self.html, f'id="cage-detail-{cid}"', "</article>")
        self.assertIn(f'data-cage-card="{cid}"', detail)
        self.assertIn("cage-card-head", detail)
        self.assertIn("Shelf 3", detail)                                    # where it is, with no rack
        self.assertIn('data-record-edit="cage-dialog"', detail)
        # All · Active · one per purpose in the lab's choices.
        for f in ("active", "purpose:breeder", "purpose:breeding", "purpose:experiment"):
            self.assertIn(f'data-cage-card-filter="{f}"', self.html)
        for gone in ("breeding", "mine"):
            self.assertNotIn(f'data-cage-card-filter="{gone}"', self.html)

    def test_the_card_id_column_sits_after_the_cage_and_saves(self):
        """The facility's card number, right after the cage's own number."""
        head = between(self.html, '<table class="dt sheet-table"', "</thead>")
        keys = re.findall(r'data-sort-key="(\w+)"', head)
        self.assertEqual(keys[:3], ["cage_id", "card_id", "rack_name"])
        cid = self.colony["cage_id"]
        tr = between(self.html, f'<tr id="cage-{cid}"', "</tr>")
        self.assertIn(f'name="card_id" form="cage-update-{cid}"', tr)
        # One more column: the sub-row still spans them all.
        self.assertIn('<td colspan="15">', self.html)
        r = self.autosave(self.m, update_url(cid), {"card_id": "F-20417"})
        self.assertSaved(r)
        self.assertEqual(cage(cid)["card_id"], "F-20417")
        self.assertIn('data-card_id="F-20417"', self.get_ok(self.m, "/colony?view=cages&scope=all"))

    def test_a_cages_mice_show_the_transgene_columns_the_mouse_sheet_shows(self):
        """Each panel draws all four transgenes, hiding what the mouse
        sheet hides by default; the page script then follows the person's
        own Columns choice there, which data-table.js keeps by column number
        (mouse_tg1_column in colony.html, checked here against the sheet)."""
        mice = self.get_ok(self.m, "/colony?view=mice&scope=all")
        table_id = re.search(r'data-table-id="(mice-v\d+)"', mice).group(1)
        heads = re.findall(r"<th\b[^>]*>", between(mice, '<table class="dt sheet-table"', "</thead>"))
        keys = [m.group(1) if (m := re.search(r'data-sort-key="(\w+)"', h)) else "" for h in heads]
        tg1 = keys.index("transgene_1")
        self.assertEqual(keys[tg1:tg1 + 4], [f"transgene_{n}" for n in range(1, 5)])
        mouse_sheet_hides = {n: "data-default-hidden" in heads[tg1 - 1 + n] for n in range(1, 5)}
        self.assertIn(f'"dt:{table_id}:hidden"', self.html)
        self.assertIn(f"hiddenCols.has({tg1 - 1} + Number(cell.dataset.tg))", self.html)
        panel = between(self.html, f'id="cage-detail-{self.colony["cage_id"]}"', "</table>")
        for n in range(1, 5):
            th = re.search(rf'<th data-tg="{n}"[^>]*>', panel).group(0)
            self.assertEqual("hidden" in th, mouse_sheet_hides[n], n)
        self.assertEqual(panel.count('data-tg="3"'), 1 + len(self.colony["mice"]))
        # A mouse with three transgenes shows Transgene 3 in both places.
        mid = self.colony["mice"][0]
        m = row("select gender, status, owner from mice where id=?", mid)
        self.assertSaved(self.autosave(self.m, f"/colony/mice/{mid}/update", {
            "gender": m[0], "status": m[1], "owner": m[2],
            "transgene_1": "Tg1", "transgene_2": "Tg2", "transgene_3": uniq("Tg3-")}))
        panel = between(self.get_ok(self.m, "/colony?view=cages&scope=all"), f'id="cage-detail-{self.colony["cage_id"]}"', "</table>")
        self.assertNotIn("hidden", re.search(r'<th data-tg="3"[^>]*>', panel).group(0))
        mice = self.get_ok(self.m, "/colony?view=mice&scope=all")
        self.assertNotRegex(mice, r'data-sort-key="transgene_3"[^>]*data-default-hidden')

    def test_someone_elses_cage_is_read_only(self):
        tr = between(self.html, f'<tr id="cage-{self.theirs}"', "</tr>")
        self.assertIn("is-locked", tr)
        self.assertRegex(tr, r'name="notes"[^>]*disabled')
        detail = between(self.html, f'id="cage-detail-{self.theirs}"', "</article>")
        self.assertIn("Read only", detail)
        self.assertNotIn("data-add-existing-mouse", detail)

    def test_cage_card_mouse_forms_post_only_what_they_show(self):
        """No cage, location or date fields: a card edit can neither move
        the mouse nor write a stale date or location back."""
        for mid in self.colony["mice"]:
            names = set(re.findall(rf'name="(\w+)"[^>]*form="cage-mouse-update-{mid}"', self.html))
            names |= set(re.findall(rf'form="cage-mouse-update-{mid}"[^>]*name="(\w+)"', self.html))
            names |= set(re.findall(r'name="(\w+)"',
                                    between(self.html, f'<form id="cage-mouse-update-{mid}"', "</form>")))
            self.assertIn("note", names)
            self.assertIn("status", names)
            self.assertFalse(names & {"cage_id", "cage_location", "date_of_birth", "date_of_death"}, names)

    def test_a_card_note_edit_keeps_the_mouse_and_the_cage_location(self):
        mid = self.colony["mice"][0]
        m = row("select gender, status, owner from mice where id=?", mid)
        r = self.autosave(self.m, f"/colony/mice/{mid}/update", {
            "gender": m[0], "status": m[1], "owner": m[2], "transgene_1": "", "transgene_2": "",
            "litter_id": self.colony["litter"], "note": "edited in cage card"})
        self.assertSaved(r)
        self.assertEqual(row("select note, cage_id_fk from mice where id=?", mid), ("edited in cage card", self.colony["cage_id"]))
        self.assertEqual(cage(self.colony["cage_id"])["cage_location"], "Shelf 3")
        # The card answers the cage sheet's shape too.
        self.assertIn("row", r.get_json())


# ---------------------------------------------------------------- batch actions

class CageBulkTests(RackMixin, Case):
    """/colony/cages/bulk: one batch per action, undoable from /batches."""

    def setUp(self):
        super().setUp()
        self.c1 = self.make_cage(self.m, purpose="Experiments")
        self.c2 = self.make_cage(self.m, purpose="Holding")

    def bulk(self, client, action, value="", ids=None):
        return client.post("/colony/cages/bulk", data={"action": action, "value": value,
                                                        "selected_ids": ids or [self.c1, self.c2]})

    def test_set_purpose_is_one_audited_batch_and_undo_restores_it(self):
        self.bulk(self.m, "purpose", "Stock")
        self.assertEqual([cage(c)["purpose"] for c in (self.c1, self.c2)], ["Stock", "Stock"])
        batch = newest_batch(self.member)
        self.assertEqual(batch[2], 2)
        self.assertEqual(count("audit_log", "batch_id_fk=? and table_name='mouse_cages'", batch[0]), 2)
        self.m.post(f"/batches/{batch[0]}/undo")
        self.assertEqual([cage(c)["purpose"] for c in (self.c1, self.c2)], ["Experiments", "Holding"])

    def test_undoing_twice_is_refused(self):
        self.bulk(self.m, "purpose", "Stock")
        batch = newest_batch(self.member)[0]
        self.m.post(f"/batches/{batch}/undo")
        take_flashes(self.m)
        self.m.post(f"/batches/{batch}/undo")
        self.assertIn("Already undone", flash_texts(self.m, "error"))

    def test_undoing_a_missing_batch_is_a_message(self):
        missing = (one("select max(id) from batches") or 0) + 1000
        self.m.post(f"/batches/{missing}/undo")
        self.assertIn("no longer exists", flash_texts(self.m, "error"))

    def test_set_owner_must_name_a_lab_member(self):
        self.bulk(self.m, "owner", uniq("nobody"))
        self.assertIn("not a lab member", flash_texts(self.m, "error"))
        self.assertEqual({cage(c)["owner"] for c in (self.c1, self.c2)}, {self.member})

    def test_set_owner_hands_the_cages_over(self):
        self.bulk(self.m, "owner", self.other)
        self.assertEqual({cage(c)["owner"] for c in (self.c1, self.c2)}, {self.other})

    def test_move_to_a_rack_leaves_them_unplaced_there_and_blank_takes_them_out(self):
        rack = self.make_rack(self.m)
        self.autosave(self.m, update_url(self.c1), {"rack_id": str(rack), "position": "A1"})
        other_rack = self.make_rack(self.m)
        self.bulk(self.m, "rack", str(other_rack))
        self.assertEqual([(cage(c)["rack_id_fk"], cage(c)["rack_row"]) for c in (self.c1, self.c2)],
                         [(other_rack, None), (other_rack, None)])
        self.bulk(self.m, "rack", "", [self.c1])
        self.assertIsNone(cage(self.c1)["rack_id_fk"])

    def test_move_to_a_rack_that_does_not_exist_is_a_message(self):
        missing = (one("select max(id) from mouse_racks") or 0) + 1000
        self.bulk(self.m, "rack", str(missing))
        self.assertIn("no longer exists", flash_texts(self.m, "error"))

    def test_mark_shared_and_not_shared(self):
        self.autosave(self.m, update_url(self.c1), {"purpose": "Breeder"})
        self.bulk(self.m, "shared", "0")
        self.assertEqual((cage(self.c1)["is_shared"], cage(self.c2)["is_shared"]), (0, 0))
        self.bulk(self.m, "shared", "1")           # any cage, not only a breeder one
        self.assertEqual((cage(self.c1)["is_shared"], cage(self.c2)["is_shared"]), (1, 1))

    def test_a_batch_leaves_others_cages_sharing_alone(self):
        theirs = self.make_cage(self.o, purpose="Experiments")
        self.bulk(self.m, "shared", "1", [theirs])
        self.assertEqual(cage(theirs)["is_shared"], 0)

    def test_member_batch_skips_cages_that_are_not_theirs(self):
        theirs = self.make_cage(self.o, purpose="Experiments")
        self.bulk(self.m, "purpose", "Stock", [self.c1, theirs])
        self.assertEqual((cage(self.c1)["purpose"], cage(theirs)["purpose"]), ("Stock", "Experiments"))
        self.assertIn("1 skipped", flash_texts(self.m, "success"))

    def test_retire_frees_an_empty_cages_rack_position_and_undo_puts_it_back(self):
        rack = self.make_rack(self.m)
        self.autosave(self.m, update_url(self.c1), {"rack_id": str(rack), "position": "B3", "is_shared": "1"})
        self.bulk(self.m, "retire", ids=[self.c1])
        c = cage(self.c1)
        # There is no Retired purpose: a retired cage has none (CLAUDE.md, "The mouse colony's words").
        self.assertEqual((c["purpose"], c["rack_id_fk"], c["rack_row"], c["is_shared"]), ("", None, None, 0))
        self.m.post(f"/batches/{newest_batch(self.member)[0]}/undo")
        c = cage(self.c1)
        self.assertEqual((c["purpose"], c["rack_id_fk"], c["rack_row"], c["rack_col"]), ("Experiments", rack, 2, 3))

    def test_retire_keeps_a_cage_with_living_mice_and_says_why(self):
        code = cage(self.c1)["cage_id"]
        self.make_mouse(self.m, self.member, cage=code)
        take_flashes(self.m)
        self.bulk(self.m, "retire", ids=[self.c1])
        self.assertIn("still holds 1 living mouse", flash_texts(self.m, "error"))
        self.assertEqual(cage(self.c1)["purpose"], "Experiments")

    def test_retire_ignores_mice_that_are_no_longer_alive(self):
        self.make_mouse(self.m, self.member, cage=cage(self.c1)["cage_id"], status="sac")
        self.bulk(self.m, "retire", ids=[self.c1])
        self.assertEqual(cage(self.c1)["purpose"], "")

    def test_an_unknown_action_is_a_message(self):
        self.bulk(self.m, "explode")
        self.assertIn("Pick an action", flash_texts(self.m, "error"))


# ---------------------------------------------------------------- racks

class MouseRackTests(RackMixin, Case):
    """Resizing, renaming or deleting a rack is for its creator or an admin
    (access.can_edit_rack); racks from before the creator column are
    admin-only."""

    def rack(self, rack_id):
        return row("select name, rows, cols, created_by from mouse_racks where id=?", rack_id)

    def test_a_new_rack_records_its_creator_who_may_resize_it(self):
        name = uniq("Rack")
        rack = self.make_rack(self.m, name, rows_=4, cols=5)
        self.assertEqual(self.rack(rack), (name, 4, 5, self.member))
        self.m.post("/colony/racks/save", data={"id": str(rack), "name": name, "rows": "6", "cols": "5"})
        self.assertEqual(self.rack(rack)[1], 6)

    def test_member_cannot_rename_resize_or_delete_someone_elses_rack(self):
        name = uniq("Rack")
        rack = self.make_rack(self.o, name)
        take_flashes(self.m)
        self.m.post("/colony/racks/save", data={"id": str(rack), "name": name + "x", "rows": "8", "cols": "8"})
        self.assertIn("Only whoever added rack", flash_texts(self.m, "error"))
        self.m.post(f"/colony/racks/{rack}/delete")
        self.assertEqual(self.rack(rack), (name, 3, 3, self.other))

    def test_admin_can_change_and_delete_any_rack(self):
        name = uniq("Rack")
        rack = self.make_rack(self.o, name)
        self.a.post("/colony/racks/save", data={"id": str(rack), "name": name, "rows": "3", "cols": "12"})
        self.assertEqual(self.rack(rack)[2], 12)
        self.a.post(f"/colony/racks/{rack}/delete")
        self.assertIsNone(self.rack(rack))

    def test_a_rack_without_a_creator_is_admin_only(self):
        name = uniq("Rack")
        rack = self.make_rack(self.m, name)
        execute("update mouse_racks set created_by='' where id=?", rack)  # a rack from before the column
        self.m.post(f"/colony/racks/{rack}/delete")
        self.assertIsNotNone(self.rack(rack))
        self.a.post(f"/colony/racks/{rack}/delete")
        self.assertIsNone(self.rack(rack))

    def test_deleting_a_rack_keeps_its_cages_unplaced(self):
        rack = self.make_rack(self.m)
        c = self.make_cage(self.m, rack_id=str(rack), position="B2")
        self.m.post(f"/colony/racks/{rack}/delete")
        self.assertIn("now unplaced", flash_texts(self.m, "success"))
        self.assertEqual((cage(c)["rack_id_fk"], cage(c)["rack_row"], cage(c)["rack_col"]), (None, None, None))

    def test_a_rack_name_is_required_and_unique(self):
        name = uniq("Rack")
        self.make_rack(self.m, name)
        take_flashes(self.m)
        self.m.post("/colony/racks/save", data={"id": "", "name": name.lower(), "rows": "3", "cols": "3"})
        self.assertIn("already a rack", flash_texts(self.m, "error"))
        self.m.post("/colony/racks/save", data={"id": "", "name": "  ", "rows": "3", "cols": "3"})
        self.assertIn("needs a name", flash_texts(self.m, "error"))
        self.assertEqual(count("mouse_racks", "lower(name)=lower(?)", name), 1)

    def test_rack_size_is_clamped_to_26_by_40(self):
        rack = self.make_rack(self.m, rows_=99, cols=99)
        self.assertEqual(self.rack(rack)[1:3], (26, 40))

    def test_a_non_numeric_rack_size_is_a_message_not_a_crash(self):
        r = self.m.post("/colony/racks/save", data={"id": "", "name": uniq("Rack"), "rows": "eight", "cols": "3"})
        self.assertEqual(r.status_code, 302)


class PlaceCageTests(RackMixin, Case):
    """Dragging on the rack grid (/colony/cages/<id>/place, JSON)."""

    def setUp(self):
        super().setUp()
        self.rack = self.make_rack(self.m, rows_=3, cols=3)
        self.cage = self.make_cage(self.m, rack_id=str(self.rack), position="A1")

    def place(self, client, cage_row, **data):
        return client.post(f"/colony/cages/{cage_row}/place", data=data)

    def test_dropping_on_an_empty_position_places_the_cage(self):
        r = self.place(self.m, self.cage, rack_id=str(self.rack), row="2", col="3")
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual((cage(self.cage)["rack_row"], cage(self.cage)["rack_col"]), (2, 3))

    def test_dropping_on_an_occupied_position_swaps_the_two(self):
        holder = self.make_cage(self.m, rack_id=str(self.rack), position="C3")
        self.assertTrue(self.place(self.m, self.cage, rack_id=str(self.rack), row="3", col="3").get_json()["ok"])
        self.assertEqual((cage(self.cage)["rack_row"], cage(self.cage)["rack_col"]), (3, 3))
        self.assertEqual((cage(holder)["rack_row"], cage(holder)["rack_col"]), (1, 1))

    def test_a_swap_with_a_cage_you_may_not_move_is_refused(self):
        theirs = self.make_cage(self.o, purpose="Experiments", rack_id=str(self.rack), position="B1")
        r = self.place(self.m, self.cage, rack_id=str(self.rack), row="2", col="1")
        self.assertEqual(r.status_code, 403)
        self.assertEqual((cage(self.cage)["rack_row"], cage(theirs)["rack_row"]), (1, 2))

    def test_the_grid_shows_every_taken_position_whatever_the_scope(self):
        """In "My colony", someone else's cage in the rack is still drawn
        (dimmed, and locked when you may not move it), so its cell never
        looks empty."""
        import json
        import re as _re
        theirs_code = uniq("T")
        self.make_cage(self.o, cage_id=theirs_code, purpose="Experiments", rack_id=str(self.rack), position="B2")
        html = self.m.get("/colony?view=cages&scope=mine").get_data(as_text=True)
        blob = _re.search(r'<script type="application/json" data-rack-data>(.*?)</script>', html, _re.S)
        self.assertIsNotNone(blob, "no rack grid data on the page")
        items = json.loads(blob.group(1))["items"]
        theirs = next(i for i in items if i["label"] == theirs_code)
        self.assertEqual((theirs["row"], theirs["col"], theirs["tone"], theirs["locked"]), (2, 2, "other", True))

    def test_a_position_outside_the_rack_is_a_400(self):
        r = self.place(self.m, self.cage, rack_id=str(self.rack), row="4", col="1")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(cage(self.cage)["rack_row"], 1)

    def test_dropping_on_the_unplaced_tray_takes_it_out_of_the_rack(self):
        self.assertTrue(self.place(self.m, self.cage, rack_id="").get_json()["ok"])
        self.assertIsNone(cage(self.cage)["rack_id_fk"])

    def test_member_cannot_move_someone_elses_cage(self):
        theirs = self.make_cage(self.o, purpose="Experiments")
        r = self.place(self.m, theirs, rack_id=str(self.rack), row="3", col="3")
        self.assertEqual(r.status_code, 403)
        self.assertIsNone(cage(theirs)["rack_id_fk"])


if __name__ == "__main__":
    unittest.main()


class CageCardQrTests(Case):
    """A cage card is scanned by whoever is at the rack, not only its owner."""

    def qr_targets(self, client, url):
        from unittest import mock
        from app import labels
        real = labels._qr_svg
        seen = []

        def capture(payload, scale=4, fit=False):
            seen.append(payload)
            return real(payload, scale, fit)

        with mock.patch.object(labels, "_qr_svg", side_effect=capture):
            self.assertEqual(client.get(url).status_code, 200)
        return seen

    def test_the_qr_opens_the_whole_colony_at_that_cage(self):
        from urllib.parse import urlparse
        cage = self.make_cage(self.m)          # the member's own cage
        target = next(t for t in self.qr_targets(self.m, f"/labels/cards/cages?ids={cage}")
                      if t.endswith(f"#cage-{cage}"))
        self.assertIn("scope=all", target)
        # Someone else scanning it lands on a page that lists the cage.
        parsed = urlparse(target)
        html = self.o.get(f"{parsed.path}?{parsed.query}").get_data(as_text=True)
        self.assertIn(f'id="cage-{cage}"', html)


class CagePurposeTests(Case):
    """What a cage's purpose means (CLAUDE.md, "The mouse colony's words"):
    Breeding is a mating cage, the only one with litter buttons; Breeder is
    the lab's breeding stock; the bar is All · Active · the lab's purposes."""

    def cages_page(self):
        return self.m.get("/colony?view=cages&scope=all").get_data(as_text=True)

    def detail(self, html, row_id):
        start = html.index(f'id="cage-detail-{row_id}"')
        return html[start:html.index("data-breeding-actions", start) + 40]

    def test_only_a_breeding_cage_shows_litter_born_genotyping_and_wean(self):
        mating = self.make_cage(self.m, purpose="Breeding")
        stock = self.make_cage(self.m, purpose="Breeder")
        html = self.cages_page()
        self.assertNotIn("hidden-row", self.detail(html, mating))
        self.assertIn("hidden-row", self.detail(html, stock))

    def test_the_bar_is_all_active_and_the_labs_purposes(self):
        html = self.cages_page()
        chips = re.findall(r'data-dt-filter="([^"]*)"', html)
        self.assertEqual(chips, ["", "active:true", "purpose:breeder", "purpose:breeding", "purpose:experiment"])
        for gone in ("stock", "retired", "mine:true", "breeding:1"):
            self.assertNotIn(f'data-dt-filter="{gone}"', html)
            self.assertNotIn(f'data-dt-filter="purpose:{gone}"', html)

    def test_a_purpose_added_in_configure_gets_its_button_and_removing_it_takes_it_away(self):
        r = self.a.post("/colony/options/create", data={"field_name": "purpose", "option_value": "Holding",
                                                        "back": "configure"})
        self.assertIn("/organisms/builtin/colony/configure", r.headers["Location"])
        page = self.a.get("/organisms/builtin/colony/configure").get_data(as_text=True)
        self.assertIn("Holding", page)
        self.assertIn('data-dt-filter="purpose:holding"', self.cages_page())
        option = one("select id from dropdown_options where field_name='purpose' and option_value='Holding'")
        self.a.post(f"/colony/options/{option}/delete", data={"back": "configure"})
        self.assertNotIn('data-dt-filter="purpose:holding"', self.cages_page())
        # Only an admin changes the lab's purposes.
        self.m.post("/colony/options/create", data={"field_name": "purpose", "option_value": "Mine only"})
        self.assertEqual(count("dropdown_options", "option_value=?", "Mine only"), 0)

    def test_active_means_living_mice(self):
        empty = self.make_cage(self.m, purpose="Breeder")
        execute("update mouse_cages set active_override=1 where id=?", empty)   # an older version's flag
        with SessionLocal() as s:
            self.assertFalse(services.cage_is_active(s.get(services.CageRecord, empty)))
