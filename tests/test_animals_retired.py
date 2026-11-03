"""Revision 0055 retires the old `animals` table (docs/adr/0007): a sample
still linked to an animal keeps its number as its source, the link columns
and the table go, and on SQLite the rebuilt tables keep what app/integrity.py
gives every table (AUTOINCREMENT, foreign keys checked at commit)."""
from __future__ import annotations

import importlib
import unittest
from unittest import mock

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.base import *  # noqa: F401,F403
from tests.base import ON_POSTGRES, AppTestCase, execute, one, uniq
from app import integrity  # noqa: E402
from app.db import engine  # noqa: E402

LINKED = ("samples", "calendar_events", "notebook_entries")


def columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns(table)}


class RetiringAnimals(AppTestCase):

    def a_repeating_event_with_a_page(self) -> int:
        """What the rebuilt tables are pointed at from elsewhere: a repeat rule
        (ON DELETE CASCADE) and a notebook page on a calendar event."""
        execute("INSERT INTO calendar_events (title, event_date, is_all_day, color, owner, event_type, description, "
                "is_shared, created_at) VALUES (?, CURRENT_DATE, TRUE, '', '', 'experiment', '', TRUE, CURRENT_TIMESTAMP)",
                uniq("Ev "))
        event = one("select max(id) from calendar_events")
        execute("INSERT INTO calendar_repeats (event_id_fk, freq, interval, skip) VALUES (?, 'weekly', 1, '')", event)
        execute("INSERT INTO notebook_entries (title, entry_date, event_id_fk, body, attachment_path, created_at) "
                "VALUES (?, CURRENT_DATE, ?, '', '', CURRENT_TIMESTAMP)", uniq("Page "), event)
        return event

    def as_before(self) -> tuple[str, str]:
        """The shape a database had before 0055: the table, one animal, and
        each link column, with a sample linked to the animal."""
        execute("CREATE TABLE animals (id INTEGER PRIMARY KEY, animal_id VARCHAR(80), species VARCHAR(40))")
        execute("INSERT INTO animals (id, animal_id, species) VALUES (7, 'M-0042', 'mouse')")
        for table in LINKED:
            execute(f"ALTER TABLE {table} ADD COLUMN animal_id_fk INTEGER REFERENCES animals (id)")
        linked, plain = uniq("S-"), uniq("S-")
        for sample, animal in ((linked, 7), (plain, None)):
            execute("INSERT INTO samples (sample_id, sample_type, source_kind, source_ref, storage_location, amount, "
                    "owner, notes, created_at, updated_by, animal_id_fk) "
                    "VALUES (?, 'tail', '', '', '', '', '', '', CURRENT_TIMESTAMP, '', ?)", sample, animal)
        return linked, plain

    def run_revision(self):
        migration = importlib.import_module("migrations.versions.0055_retire_animals")
        with engine.connect() as conn:
            if not ON_POSTGRES:
                # As Alembic runs it: its own engine, with SQLite's foreign keys off.
                conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
                conn.commit()
            try:
                with conn.begin():
                    self._upgrade(migration, conn)
            finally:
                if not ON_POSTGRES:
                    conn.exec_driver_sql("PRAGMA foreign_keys=ON")
                    conn.commit()

    def _upgrade(self, migration, conn):
        ops = Operations(MigrationContext.configure(conn))
        inspector = lambda: sa.inspect(conn)  # noqa: E731  (fresh: the revision changes the schema)
        with mock.patch.object(migration, "op", ops), \
                mock.patch.object(migration, "has_table", lambda name: inspector().has_table(name)), \
                mock.patch.object(migration, "has_column",
                                  lambda t, c: inspector().has_table(t)
                                  and c in {x["name"] for x in inspector().get_columns(t)}):
            migration.upgrade()

    @unittest.skipIf(ON_POSTGRES, "SQLite's rebuild only")
    def test_it_refuses_to_rebuild_while_foreign_keys_are_enforced(self):
        """Dropping a table to rebuild it would delete what points at it (a
        repeat rule is ON DELETE CASCADE): with foreign keys on it stops first."""
        event = self.a_repeating_event_with_a_page()
        self.as_before()
        migration = importlib.import_module("migrations.versions.0055_retire_animals")
        try:
            with engine.connect() as conn:
                self.assertEqual(conn.exec_driver_sql("PRAGMA foreign_keys").scalar(), 1)
                conn.commit()
                with self.assertRaises(RuntimeError), conn.begin():
                    self._upgrade(migration, conn)
            self.assertEqual(one("select count(*) from calendar_repeats where event_id_fk=?", event), 1)
        finally:
            self.run_revision()      # then retire it, so the shared test lab is as the models say

    def test_the_table_and_its_links_go_and_a_linked_sample_keeps_its_animal(self):
        event = self.a_repeating_event_with_a_page()
        linked, plain = self.as_before()
        rows = {t: one(f"select count(*) from {t}") for t in LINKED + ("calendar_repeats",)}
        self.run_revision()
        self.assertFalse(sa.inspect(engine).has_table("animals"))
        for table in LINKED:
            self.assertNotIn("animal_id_fk", columns(table), table)
        for table, n in rows.items():
            self.assertEqual(one(f"select count(*) from {table}"), n, f"{table} lost rows")
        self.assertEqual(one("select count(*) from calendar_repeats where event_id_fk=?", event), 1)
        self.assertEqual(one("select count(*) from notebook_entries where event_id_fk=?", event), 1)
        self.assertEqual(one("select source_kind || ':' || source_ref from samples where sample_id=?", linked),
                         "other:M-0042")
        self.assertEqual(one("select source_kind || ':' || source_ref from samples where sample_id=?", plain), ":")
        if not ON_POSTGRES:
            with engine.connect() as conn:
                for table in LINKED:
                    sql = conn.exec_driver_sql("select sql from sqlite_master where name=?", (table,)).scalar()
                    self.assertTrue(integrity._is_current(sql), sql)
        # The app adds to them as before (on SQLite, with foreign keys enforced).
        made = uniq("S-")
        execute("INSERT INTO samples (sample_id, sample_type, source_kind, source_ref, storage_location, amount, "
                "owner, notes, created_at, updated_by) VALUES (?, 'tail', 'mouse', '12', '', '', '', '', "
                "CURRENT_TIMESTAMP, '')", made)
        self.assertEqual(one("select count(*) from samples where sample_id=?", made), 1)
