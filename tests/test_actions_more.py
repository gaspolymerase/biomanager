"""app/actions.py beyond the animals: inventories, orders and samples,
plasmids, glycerol stocks and primers, notebook pages and the calendar,
each previewed (nothing kept) or applied through the pages' own code."""
from __future__ import annotations

import json
import unittest
from datetime import date, timedelta

from tests.base import *  # noqa: F401,F403
from tests.base import count, days_ahead, execute, one, row, rows, uniq
from tests import test_inventory as inv_tests
from tests.test_api import Base as ApiBase
from tests.test_notebook import Notebook, b64

from app import actions
from app.app import app


def run(who, *changes, apply=False, source=""):
    return actions.run(app, who, list(changes), apply=apply, description=uniq("P") if apply else "", source=source)


def errors_of(out) -> str:
    return " ".join(e for c in out.changes for e in c.errors)


def item_ref(item_id: int) -> dict:
    return {"kind": "inventory_item", "id": item_id}


def number_of(item_id: int) -> int:
    return one("select number from inventory_items where id=?", item_id)


class Applies:
    def apply(self, *changes, who=None, source=""):
        out = run(who or self.member, *changes, apply=True, source=source)
        self.assertTrue(out.ok, [c.as_dict() for c in out.changes])
        return out


class InventoryActions(Applies, inv_tests.InventoryCase):
    def test_a_new_item_changed_then_used_down_past_low_at(self):
        box = uniq("Shelf ")
        self.make_rack(self.m, "reagents", name=box)
        name = uniq("Trizol ")
        self.apply({"action": "inventory_item_new", "fields": {
            "database": "Reagents", "name": name, "quantity": "5", "unit": "mL", "low_at": 2, "vendor": "Acme",
            "box": box, "position": "A1", "shared": True}})
        item = one("select id from inventory_items where name=?", name)
        self.assertEqual(row("select quantity, low_at, status, owner, rack_row, rack_col from inventory_items where id=?",
                             item), ("5", 2.0, "in stock", self.member, 1, 1))
        self.apply({"action": "inventory_item_change", "target": f"reagents #{number_of(item)}",
                    "fields": {"lot": "L-7", "notes": "opened"}})
        self.assertEqual(row("select lot, notes, vendor from inventory_items where id=?", item), ("L-7", "opened", "Acme"))
        out = self.apply({"action": "inventory_quantity_used", "target": item_ref(item), "fields": {"amount": 3}})
        self.assertIn("(5 → 2)", out.changes[0].summary)
        self.assertEqual(row("select quantity, status from inventory_items where id=?", item), ("2", "low"))
        self.apply({"action": "inventory_quantity_used", "target": name, "fields": {"amount": 2}})
        self.assertEqual(row("select quantity, status from inventory_items where id=?", item), ("0", "empty"))
        out = run(self.member, {"action": "inventory_quantity_used", "target": item_ref(item), "fields": {"amount": 1}})
        self.assertIn("Only 0 mL", errors_of(out))

    def test_a_preview_keeps_nothing_and_shows_the_record(self):
        name, before = uniq("Tube "), count("inventory_items")
        out = run(self.member, {"action": "inventory_item_new", "fields": {"database": "samples", "name": name}})
        self.assertTrue(out.ok, out.errors)
        self.assertTrue(any(r["table"] == "inventory_items" and r["action"] == "create" for r in out.changes[0].records),
                        out.changes[0].records)
        self.assertEqual(count("inventory_items"), before)

    def test_someone_elses_personal_item_is_refused(self):
        item = self.make_item(self.m, "reagents", is_shared="0")
        out = run(self.other, {"action": "inventory_item_change", "target": item_ref(item), "fields": {"notes": "mine"}})
        self.assertFalse(out.ok)
        self.assertTrue(out.changes[0].errors)
        self.assertEqual(one("select notes from inventory_items where id=?", item), "")

    def test_what_is_not_there_is_said_plainly(self):
        twin = uniq("Twin ")
        first = self.make_item(self.m, "reagents", twin)
        self.make_item(self.m, "antibodies", twin)
        cases = [({"action": "inventory_item_change", "target": item_ref(10 ** 9), "fields": {"notes": "x"}},
                  "There is no inventory item"),
                 ({"action": "inventory_item_change", "target": "reagents #999999", "fields": {"notes": "x"}},
                  "There is no inventory item"),
                 ({"action": "inventory_item_change", "target": twin, "fields": {"notes": "x"}}, "could be several"),
                 ({"action": "inventory_item_new", "fields": {"database": "nowhere", "name": "x"}},
                  "There is no inventory"),
                 ({"action": "inventory_quantity_used", "target": item_ref(first), "fields": {}}, "needs amount")]
        for change, words in cases:
            out = run(self.member, change)
            self.assertFalse(out.ok, change)
            self.assertIn(words, errors_of(out), change)
        half = self.make_item(self.m, "reagents", quantity="half a box")
        out = run(self.member, {"action": "inventory_quantity_used", "target": item_ref(half), "fields": {"amount": 1}})
        self.assertIn("not a number", errors_of(out))

    def test_an_order_received_and_added_to_stock(self):
        name = uniq("Lipofectamine ")
        order = self.make_item(self.m, "orders", name, quantity="2", unit="vials")
        box = uniq("Fridge ")
        self.make_rack(self.m, "reagents", name=box)
        out = self.apply({"action": "order_received", "target": item_ref(order), "fields": {
            "lot": "L123", "to_stock": "reagents", "expires_on": days_ahead(300), "box": box, "position": "B2"}})
        self.assertIn("added to Reagents", out.changes[0].summary)
        status, received, lot, attrs = row("select status, received_on, lot, attrs from inventory_items where id=?", order)
        self.assertEqual((status, str(received)[:10], lot), ("received", date.today().isoformat(), "L123"))
        stock = one("select i.id from inventory_items i join inventory_modules m on m.id=i.module_id_fk "
                    "where m.key='reagents' and i.name=?", name)
        self.assertEqual(json.loads(attrs)["stocked_as"], f"reagents:{number_of(stock)}")
        self.assertEqual(row("select vendor, lot, quantity, cast(expires_on as text), rack_row, rack_col from "
                             "inventory_items where id=?", stock), ("Acme", "L123", "2", days_ahead(300), 2, 2))
        out = run(self.member, {"action": "order_received", "target": item_ref(order), "fields": {"to_stock": "reagents"}})
        self.assertIn("already in stock", errors_of(out))

    def test_only_an_order_is_received_and_stock_fields_need_to_stock(self):
        reagent = self.make_item(self.m, "reagents")
        out = run(self.member, {"action": "order_received", "target": item_ref(reagent), "fields": {}})
        self.assertIn("is not an order", errors_of(out))
        order = self.make_item(self.m, "orders")
        out = run(self.member, {"action": "order_received", "target": item_ref(order), "fields": {"box": "B"}})
        self.assertIn("only go with to_stock", errors_of(out))
        out = run(self.member, {"action": "order_received", "target": item_ref(order),
                                "fields": {"to_stock": "orders"}})
        self.assertIn("Pick a reagents", errors_of(out))
        self.apply({"action": "order_received", "target": item_ref(order), "fields": {"received_on": "2026-01-05"}})
        self.assertEqual(row("select status, cast(received_on as text) from inventory_items where id=?", order),
                         ("received", "2026-01-05"))

    def test_a_sample_from_a_mouse(self):
        mouse = self.make_mouse(self.m, self.member)
        number = one("select mouse_id from mice where id=?", mouse)
        name = uniq("S-")
        out = self.apply({"action": "sample_from_animal", "fields": {
            "mouse": number, "name": name, "category": "tissue", "collected_on": days_ahead(0), "amount": "20 mg"}})
        self.assertIn(f"mouse #{number}", out.changes[0].summary)
        attrs = json.loads(one("select attrs from inventory_items where name=?", name))
        self.assertEqual(attrs["source"], {"kind": "mouse", "ref": str(number)})
        self.assertEqual((attrs["collected_on"], attrs["amount"]), (days_ahead(0), "20 mg"))
        out = run(self.member, {"action": "sample_from_animal", "fields": {"name": "x"}})
        self.assertIn("Say which one animal", errors_of(out))
        out = run(self.member, {"action": "sample_from_animal", "fields": {"mouse": 10 ** 8}})
        self.assertIn("There is no mouse", errors_of(out))


class Resolving(ApiBase):
    def test_items_plasmids_and_instruments_with_references_proposals_take(self):
        tok = self.token(self.m, scope="read")
        name = uniq("Anti-GFP ")
        item = self.make_item(self.m, "antibodies", name)
        hits = self.call("get", f"/resolve?q=antibody {name}", tok).get_json()["data"]
        self.assertEqual([h["ref"] for h in hits], [item_ref(item)])
        hits = self.call("get", f"/resolve?q=%23{number_of(item)}&kinds=inventory_item", tok).get_json()["data"]
        self.assertIn(item_ref(item), [h["ref"] for h in hits])
        plasmid = self.make_plasmid(self.m, uniq("pAAV-"))
        number = one("select plasmid_id from plasmids where id=?", plasmid)
        hits = self.call("get", f"/resolve?q=plasmid {number}", tok).get_json()["data"]
        self.assertEqual(hits[0]["ref"], {"kind": "plasmid", "id": plasmid})
        out = actions.run(app, self.member, [{"action": "plasmid_change", "target": hits[0]["ref"],
                                              "fields": {"notes": "ok"}}])
        self.assertTrue(out.ok, out.errors)
        vocabulary = self.call("get", "/vocabulary", tok).get_json()
        reagents = [d for d in vocabulary["databases"] if d["key"] == "reagents"][0]
        self.assertIn("in stock", reagents["statuses"])
        self.assertEqual(reagents["inventory_kind"], "reagents")


class PlasmidActions(Applies, inv_tests.InventoryCase):
    SEQUENCE = ">pTest\n" + "ATGACCATGATTACGCCAAGCTTGCATGCCTGCAGGTCGACTCTAGAGGATCCCCGGGTACCGAGCTCGAATTC" * 2

    def test_new_with_its_sequence_in_a_box_then_changed(self):
        box = self.make_box(self.m)
        box_name = one("select name from plasmid_boxes where id=?", box)
        name = uniq("pLKO-")
        out = self.apply({"action": "plasmid_new", "fields": {"name": name, "backbone": "pLKO.1", "resistance": "Amp",
                                                              "box": box_name, "position": "B3",
                                                              "sequence": self.SEQUENCE}})
        self.assertIn("letters", out.changes[0].summary)
        plasmid, number, seq, row_, col = row("select id, plasmid_id, full_sequence, box_row, box_col from plasmids "
                                              "where name=?", name)
        self.assertEqual((len(seq), row_, col), (148, 1, 2))
        self.apply({"action": "plasmid_change", "target": f"#{number}",
                    "fields": {"insert": "shGFP", "sequence": "ATGC" * 10}})
        self.assertEqual(row("select insert_seq, full_sequence, backbone from plasmids where id=?", plasmid),
                         ("shGFP", "ATGC" * 10, "pLKO.1"))
        out = run(self.other, {"action": "plasmid_change", "target": f"#{number}", "fields": {"notes": "x"}})
        self.assertFalse(out.ok)
        out = run(self.member, {"action": "plasmid_new", "fields": {"name": "x", "box": uniq("No box ")}})
        self.assertIn("There is no plasmid box", errors_of(out))

    def test_a_glycerol_stock_of_a_plasmid(self):
        stocks = self.new_module(self.a, "glycerol_stocks")
        plasmid = self.make_plasmid(self.m, uniq("pX-"))
        number = one("select plasmid_id from plasmids where id=?", plasmid)
        name = uniq("Stbl3 clone ")
        out = self.apply({"action": "glycerol_stock_new", "fields": {
            "database": stocks, "plasmid": f"#{number}", "name": name, "strain": "Stbl3", "colony": "3",
            "verified": "Sanger", "glycerol": 15}})
        self.assertIn(f"#{number}", out.changes[0].summary)
        category, attrs = row("select category, attrs from inventory_items where name=?", name)
        attrs = json.loads(attrs)
        self.assertEqual((category, attrs["plasmid"], attrs["colony"], attrs["verified"], attrs["glycerol"]),
                         ("Stbl3", str(number), "3", "Sanger", "15"))

    def test_a_primer_and_a_primer_pair(self):
        primers = self.new_module(self.a, "primers")
        plasmid = self.make_plasmid(self.m, uniq("pY-"))
        number = one("select plasmid_id from plasmids where id=?", plasmid)
        name = uniq("GAPDH-qF-")
        self.apply({"action": "primer_new", "fields": {"database": primers, "name": name,
                                                       "sequence": "GAAGGTGAAGGTCGGAGTCA", "direction": "forward",
                                                       "use": "qPCR", "plasmid": number}})
        status, category, attrs = row("select status, category, attrs from inventory_items where name=?", name)
        attrs = json.loads(attrs)
        self.assertEqual((status, category, attrs["sequence"], attrs["template"], attrs["length"]),
                         ("to order", "qPCR", "GAAGGTGAAGGTCGGAGTCA", str(number), "20"))
        pair = uniq("ACTB ")
        out = self.apply({"action": "primer_new", "fields": {"database": primers, "name": pair,
                                                             "sequence": "CATGTACGTTGCTATCCAGGC",
                                                             "reverse": "CTCCTTAATGTCACGCACGAT"}})
        self.assertIn(f"{pair}-F / {pair}-R", out.changes[0].summary)
        made = {n: json.loads(a) for n, a in rows("select name, attrs from inventory_items where name like ?", pair + "-%")}
        self.assertEqual((made[f"{pair}-F"]["pair"], made[f"{pair}-R"]["direction"]), (f"{pair}-R", "reverse"))


class NotebookActions(Applies, Notebook):
    def page_titled(self, title):
        return one("select id from notebook_pages where title=?", title)

    def test_a_new_page_drafted_by_the_assistant_never_signed(self):
        title, before = uniq("Western blot "), count("notebook_pages")
        out = run(self.member, {"action": "notebook_page_new", "fields": {"title": title, "body": "## Aim\n\nSee."}},
                  source="Claude")
        self.assertTrue(out.ok, out.errors)
        self.assertEqual(count("notebook_pages"), before)                    # a preview
        self.apply({"action": "notebook_page_new", "fields": {"title": title, "body": "## Aim\n\nSee.",
                                                              "starter": "experiment", "tags": ["blot"]}},
                   source="Claude")
        page = self.page_titled(title)
        body = one("select body from notebook_pages where id=?", page)
        self.assertTrue(body.startswith("_Drafted by Claude_\n\n"), body[:80])
        self.assertTrue(body.rstrip().endswith("## Aim\n\nSee."), body[-80:])
        self.assertIn("## Hypothesis", body)                              # the starter's
        self.assertEqual(row("select kind, tags from notebook_page_info where page_id_fk=?", page)[0], "experiment")
        self.assertEqual(one("select owner_username from notebook_tabs t join notebook_pages p on p.tab_id_fk=t.id "
                             "where p.id=?", page), self.member)
        self.assertEqual(count("record_signatures", "page_id_fk=?", page), 0)

    def test_from_a_template_yours_or_the_labs(self):
        template = uniq("PCR template ")
        self.m.post("/notebook/templates/create", data={"title": template, "body": "## Mix\n\n- buffer"})
        title = uniq("PCR ")
        self.apply({"action": "notebook_page_new", "fields": {"title": title, "template": template, "body": "Ran it."}})
        body = one("select body from notebook_pages where id=?", self.page_titled(title))
        self.assertIn("_Drafted by an assistant_\n\n## Mix\n\n- buffer\n\nRan it.", body)
        out = run(self.other, {"action": "notebook_page_new", "fields": {"title": "x", "template": template}})
        self.assertIn("There is no notebook template", errors_of(out))

    def test_a_section_added_to_a_page_you_may_edit(self):
        page = self.new_page(self.m, title=uniq("Cloning "))
        self.save(self.m, page, body="## Plan\n\nDigest.")
        self.apply({"action": "notebook_page_append", "target": {"kind": "notebook_page", "id": page},
                    "fields": {"heading": "Results", "text": "Two bands."}}, source="Claude")
        self.assertEqual(one("select body from notebook_pages where id=?", page),
                         "## Plan\n\nDigest.\n\n## Results\n\n_Drafted by Claude_\n\nTwo bands.\n")
        out = run(self.other, {"action": "notebook_page_append", "target": {"kind": "notebook_page", "id": page},
                               "fields": {"text": "x"}})
        self.assertIn("There is no notebook page", errors_of(out))
        out = run(self.member, {"action": "notebook_page_append", "target": {"kind": "notebook_page", "id": 10 ** 9},
                                "fields": {"text": "x"}})
        self.assertIn("There is no notebook page", errors_of(out))

    def test_not_while_the_page_is_open_in_the_editor(self):
        page = self.new_page(self.m, title=uniq("Open "))
        r = self.post_json(self.m, f"/notebook/api/pages/{page}/sync",
                           {"client": "tab-1", "gen": 0, "init": True, "updates": [b64(b"\x00\x00")]})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        out = run(self.member, {"action": "notebook_page_append", "target": {"kind": "notebook_page", "id": page},
                                "fields": {"text": "x"}})
        self.assertIn("is open in the editor", errors_of(out))
        execute("delete from notebook_presence where page_id_fk=?", page)
        self.apply({"action": "notebook_page_append", "target": {"kind": "notebook_page", "id": page},
                    "fields": {"text": "Later."}})
        self.assertIn("Later.", one("select body from notebook_pages where id=?", page))


class CalendarActions(Applies, Notebook):
    def test_an_event_timed_or_all_day_and_personal(self):
        day = (date.today() + timedelta(days=3)).isoformat()
        title = uniq("Lab meeting ")
        out = self.apply({"action": "calendar_event_new", "fields": {"title": title, "date": day, "time": "9:30",
                                                                     "end_time": "10:30", "description": "Room 4"}})
        self.assertEqual(out.changes[0].summary, f"Calendar: “{title}”, {day} 09:30–10:30")
        self.assertEqual(row("select cast(start_at as text), cast(end_at as text), is_all_day, is_shared, owner, "
                             "description from calendar_events where title=?", title)[2:],
                         (0, 1, self.member, "Room 4"))
        mine = uniq("Dentist ")
        self.apply({"action": "calendar_event_new", "fields": {"title": mine, "date": day, "personal": True}})
        self.assertEqual(row("select is_all_day, is_shared, cast(event_date as text) from calendar_events where title=?",
                             mine), (1, 0, day))
        for fields, words in (({"time": "25:00"}, "is a time of day"), ({"end_time": "11:00"}, "go with a start time")):
            out = run(self.member, {"action": "calendar_event_new", "fields": {"title": "x", "date": day, **fields}})
            self.assertIn(words, errors_of(out))

    def test_a_to_do_mine_or_the_labs_with_or_without_a_day(self):
        day = (date.today() + timedelta(days=4)).isoformat()
        title = uniq("Order tamoxifen ")
        out = self.apply({"action": "task_new", "fields": {"title": title, "date": day, "notes": "20 mg/mL",
                                                           "shared": True}})
        self.assertEqual(out.changes[0].summary, f"To-do: “{title}”, due {day} (the lab's)")
        self.assertEqual(row("select cast(due_date as text), start_at, is_shared, owner, notes, done_at "
                             "from tasks where title=?", title), (day, None, 1, self.member, "20 mg/mL", None))
        timed = uniq("Weigh cohort ")
        self.apply({"action": "task_new", "fields": {"title": timed, "date": day, "time": "9:00"}})
        self.assertEqual(row("select cast(start_at as text), is_shared from tasks where title=?", timed)[1], 0)
        undated = uniq("Book the surgery room ")
        out = self.apply({"action": "task_new", "fields": {"title": undated}})
        self.assertEqual(out.changes[0].summary, f"To-do: “{undated}”")
        self.assertEqual(row("select due_date from tasks where title=?", undated), (None,))
        out = run(self.member, {"action": "task_new", "fields": {"title": "x", "time": "10:00"}})
        self.assertIn("goes with a date", errors_of(out))

    def test_an_instrument_booked_and_a_clash_refused(self):
        name = uniq("Confocal ")
        self.post_json(self.m, "/calendar/equipment", {"name": name})
        day = (date.today() + timedelta(days=5)).isoformat()
        self.apply({"action": "equipment_booking_new", "target": name,
                    "fields": {"date": day, "time": "09:00", "end_time": "11:00", "purpose": "imaging"}})
        self.assertEqual(one("select count(*) from equipment_bookings b join equipment e on e.id=b.equipment_id_fk "
                             "where e.name=?", name), 1)
        out = run(self.other, {"action": "equipment_booking_new", "target": name,
                               "fields": {"date": day, "time": "10:00", "end_time": "12:00"}})
        self.assertIn("already booked", errors_of(out))
        out = run(self.member, {"action": "equipment_booking_new", "target": uniq("Nothing "),
                                "fields": {"date": day, "time": "10:00", "end_time": "12:00"}})
        self.assertIn("There is no equipment", errors_of(out))


if __name__ == "__main__":
    unittest.main()
