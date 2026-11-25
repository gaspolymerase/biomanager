"""Batch history: every database's batch actions land in one batch that
Batch history can undo, and the undo rules themselves."""
from tests.base import *  # noqa: F401,F403
from tests.base import AppTestCase, batch_of, client_for, last_batch, make_user, one, uniq

import unittest


class EachDatabaseBatchCanBeUndone(AppTestCase):
    """One batch action per database, then its undo from Batch history."""

    def undo(self, batch, client=None):
        self.assertIsNotNone(batch, "no batch was recorded")
        return self.post(client or self.a, f"/batches/{batch}/undo")

    def test_mice_bulk_status(self):
        colony = self.make_colony(self.a, self.admin, n_mice=3)
        self.a.post("/colony/mice/bulk-update", data={"field": "status", "value": "breeder",
                                                      "selected_ids": colony["mice"]})
        self.assertEqual({one("select status from mice where id=?", m) for m in colony["mice"]}, {"breeder"})
        self.undo(batch_of("mice", colony["mice"][0], "update"))
        self.assertEqual({one("select status from mice where id=?", m) for m in colony["mice"]}, {"experiment"})

    def test_undo_redo_undo_puts_mice_back_in_their_cages(self):
        colony = self.make_colony(self.a, self.admin, n_mice=2)
        cage = lambda: [one("select cage_id_fk from mice where id=?", m) for m in colony["mice"]]
        start = cage()
        self.a.post("/colony/mice/bulk-update", data={"field": "cage_id", "value": "new",
                                                      "selected_ids": colony["mice"]})
        moved = cage()
        self.assertNotEqual(moved, start)
        for expected in (start, moved, start):          # undo, undo the undo, undo again
            self.undo(last_batch()[0])
            self.assertEqual(cage(), expected)

    def test_undoing_an_undo_puts_the_batch_back_in_force(self):
        colony = self.make_colony(self.a, self.admin, n_mice=1)
        self.a.post("/colony/mice/bulk-update", data={"field": "status", "value": "breeder",
                                                      "selected_ids": colony["mice"]})
        original = last_batch()[0]
        self.undo(original)
        self.assertIsNotNone(one("select undone_at from batches where id=?", original))
        self.undo(last_batch()[0])                       # the redo
        self.assertIsNone(one("select undone_at from batches where id=?", original))
        self.undo(original)                              # and it can be undone again
        self.assertEqual(one("select status from mice where id=?", colony["mice"][0]), "experiment")

    def test_cages_bulk_purpose(self):
        cages = [self.make_cage(self.a, purpose="Holding") for _ in range(2)]
        self.a.post("/colony/cages/bulk", data={"action": "purpose", "value": "Breeding", "selected_ids": cages})
        self.assertEqual({one("select purpose from mouse_cages where id=?", c) for c in cages}, {"Breeding"})
        self.undo(batch_of("mouse_cages", cages[0], "update"))
        self.assertEqual({one("select purpose from mouse_cages where id=?", c) for c in cages}, {"Holding"})

    def test_several_new_cages_at_once(self):
        start = one("select coalesce(max(id), 0) from mouse_cages")
        self.a.post("/colony/cages/create", data={"cage_id": "", "count": "3"})
        made = [i for (i,) in rows("select id from mouse_cages where id>? and owner=?", start, self.admin)]
        self.assertEqual(len(made), 3)
        self.undo(batch_of("mouse_cages", made[0], "create"))
        self.assertEqual(one(f"select count(*) from mouse_cages where id in ({','.join('?' * 3)})", *made), 0)

    def test_zebrafish_tanks_bulk_purpose(self):
        tanks = [self.make_tank(self.a) for _ in range(2)]
        before = {one("select purpose from tanks where id=?", t) for t in tanks}
        self.a.post("/zebrafish/tanks/bulk", data={"action": "purpose", "value": "quarantine", "selected_ids": tanks})
        self.assertEqual({one("select purpose from tanks where id=?", t) for t in tanks}, {"quarantine"})
        self.undo(batch_of("tanks", tanks[0], "update"))
        self.assertEqual({one("select purpose from tanks where id=?", t) for t in tanks}, before)

    def test_plasmids_bulk_resistance(self):
        plasmids = [self.make_plasmid(self.a, resistance="Amp") for _ in range(2)]
        self.a.post("/plasmids/bulk", data={"action": "resistance", "value": "Kan", "selected_ids": plasmids})
        self.assertEqual({one("select resistance from plasmids where id=?", p) for p in plasmids}, {"Kan"})
        self.undo(batch_of("plasmids", plasmids[0], "update"))
        self.assertEqual({one("select resistance from plasmids where id=?", p) for p in plasmids}, {"Amp"})

    def test_inventory_bulk_status(self):
        items = [self.make_item(self.a, "reagents", status="in stock") for _ in range(2)]
        self.a.post("/inventory/reagents/items/bulk", data={"action": "status", "value": "low", "selected_ids": items})
        self.assertEqual({one("select status from inventory_items where id=?", i) for i in items}, {"low"})
        self.undo(batch_of("inventory_items", items[0], "update"))
        self.assertEqual({one("select status from inventory_items where id=?", i) for i in items}, {"in stock"})

    def test_stocks_bulk_set_notes(self):
        key = self.make_stock_module(self.a)
        vials = [self.make_vial(self.a, key, notes="before") for _ in range(2)]
        self.a.post(f"/stocks/{key}/units/bulk", data={"action": "set", "field": "notes", "value": "after",
                                                        "selected_ids": vials})
        self.assertEqual({one("select notes from stock_units where id=?", v) for v in vials}, {"after"})
        self.undo(batch_of("stock_units", vials[0], "update"))
        self.assertEqual({one("select notes from stock_units where id=?", v) for v in vials}, {"before"})

    def test_organism_animals_bulk_delete(self):
        key = self.make_organism_module(self.a)
        animals = [self.make_animal(self.a, key, owner=self.admin) for _ in range(2)]
        self.a.post(f"/organisms/{key}/animals/bulk", data={"action": "delete", "selected_ids": animals})
        self.assertEqual(one(f"select count(*) from organisms where id in (?, ?)", *animals), 0)
        self.undo(batch_of("organisms", animals[0], "delete"))
        self.assertEqual(one(f"select count(*) from organisms where id in (?, ?)", *animals), 2)


class UndoRules(AppTestCase):
    def bulk_purpose(self, client, cages, value):
        client.post("/colony/cages/bulk", data={"action": "purpose", "value": value, "selected_ids": cages})
        return batch_of("mouse_cages", cages[0], "update")

    def test_only_whoever_ran_a_batch_or_an_admin_can_undo_it(self):
        cage = self.make_cage(self.m, purpose="Holding")
        batch = self.bulk_purpose(self.m, [cage], "Breeding")
        r = self.post(self.o, f"/batches/{batch}/undo")
        self.assertFlash(r, "Only whoever ran a batch", "error")
        self.assertEqual(one("select purpose from mouse_cages where id=?", cage), "Breeding")

    def test_the_member_who_ran_it_can_undo_it(self):
        cage = self.make_cage(self.m, purpose="Holding")
        batch = self.bulk_purpose(self.m, [cage], "Breeding")
        self.post(self.m, f"/batches/{batch}/undo")
        self.assertEqual(one("select purpose from mouse_cages where id=?", cage), "Holding")

    def test_an_admin_can_undo_a_members_batch(self):
        cage = self.make_cage(self.m, purpose="Holding")
        batch = self.bulk_purpose(self.m, [cage], "Breeding")
        self.post(self.a, f"/batches/{batch}/undo")
        self.assertEqual(one("select purpose from mouse_cages where id=?", cage), "Holding")

    def test_a_batch_cannot_be_undone_twice(self):
        cage = self.make_cage(self.a, purpose="Holding")
        batch = self.bulk_purpose(self.a, [cage], "Breeding")
        self.post(self.a, f"/batches/{batch}/undo")
        r = self.post(self.a, f"/batches/{batch}/undo")
        self.assertFlash(r, "Already undone", "error")

    def test_a_record_changed_since_blocks_the_undo(self):
        cage = self.make_cage(self.a, purpose="Holding")
        batch = self.bulk_purpose(self.a, [cage], "Breeding")
        self.bulk_purpose(self.a, [cage], "Retired")
        r = self.post(self.a, f"/batches/{batch}/undo")
        self.assertTrue(errors(r), flashes(r))
        self.assertEqual(one("select purpose from mouse_cages where id=?", cage), "Retired")

    def test_force_undoes_despite_a_later_change(self):
        cage = self.make_cage(self.a, purpose="Holding")
        batch = self.bulk_purpose(self.a, [cage], "Breeding")
        self.bulk_purpose(self.a, [cage], "Retired")
        self.post(self.a, f"/batches/{batch}/undo", data={"force": "1"})
        self.assertEqual(one("select purpose from mouse_cages where id=?", cage), "Holding")

    def test_the_undo_is_itself_recorded_and_the_batch_marked_undone(self):
        cage = self.make_cage(self.a, purpose="Holding")
        batch = self.bulk_purpose(self.a, [cage], "Breeding")
        self.post(self.a, f"/batches/{batch}/undo")
        self.assertEqual(one("select undone_by from batches where id=?", batch), self.admin)
        undo_batch = batch_of("mouse_cages", cage, "update")
        self.assertNotEqual(undo_batch, batch)

    def test_a_missing_batch_is_reported(self):
        r = self.post(self.a, "/batches/987654321/undo")
        self.assertFlash(r, "no longer exists", "error")

    def describe(self, batch):
        """undo.describe for this batch, and the SQL statements it ran."""
        from sqlalchemy import event
        from app import undo as undo_service
        from app.db import SessionLocal, engine as db_engine
        from app.models import BatchRecord
        seen = []

        def note(conn, cursor, statement, parameters, context, executemany):
            seen.append(statement)
        with SessionLocal() as session:
            record = session.get(BatchRecord, batch)
            event.listen(db_engine, "before_cursor_execute", note)
            try:
                summary = undo_service.describe(session, record)
            finally:
                event.remove(db_engine, "before_cursor_execute", note)
        return summary, seen

    def test_describing_a_batch_takes_as_many_queries_however_large_it_is(self):
        # Settings' History describes the latest dozen batches each time it
        # opens; one query per entry made an import of 3000 primers take
        # 10–20 seconds (#166).
        small = self.bulk_purpose(self.a, [self.make_cage(self.a, purpose="Holding")], "Breeding")
        cages = [self.make_cage(self.a, purpose="Holding") for _ in range(12)]
        large = self.bulk_purpose(self.a, cages, "Breeding")
        small_summary, small_sql = self.describe(small)
        large_summary, large_sql = self.describe(large)
        self.assertEqual((small_summary["entries"], large_summary["entries"]), (1, 12))
        self.assertEqual(large_summary["updates"], 12)
        self.assertEqual(len(large_sql), len(small_sql), large_sql)

    def test_it_counts_the_records_changed_after_the_batch(self):
        cages = [self.make_cage(self.a, purpose="Holding") for _ in range(3)]
        batch = self.bulk_purpose(self.a, cages, "Breeding")
        self.assertEqual(self.describe(batch)[0]["blocked"], [])
        self.bulk_purpose(self.a, cages[:2], "Experiment")
        blocked = self.describe(batch)[0]["blocked"]
        self.assertEqual(len(blocked), 1, blocked)
        self.assertIn("2 records changed after this batch", blocked[0])

    def test_its_own_undo_and_redo_are_not_later_edits(self):
        cages = [self.make_cage(self.a, purpose="Holding") for _ in range(2)]
        batch = self.bulk_purpose(self.a, cages, "Breeding")
        self.post(self.a, f"/batches/{batch}/undo")
        self.post(self.a, f"/batches/{last_batch()[0]}/undo")      # the redo
        self.assertEqual(self.describe(batch)[0]["blocked"], [])

    def test_batch_history_lists_a_members_own_batches(self):
        cage = self.make_cage(self.m, purpose="Holding")
        self.bulk_purpose(self.m, [cage], uniq("Purpose "))
        html = self.get_ok(self.m, "/batches")
        self.assertIn("purpose", html.lower())


if __name__ == "__main__":
    unittest.main()
