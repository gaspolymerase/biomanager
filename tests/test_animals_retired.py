"""Revision 0055 retires the old `animals` table (docs/adr/0007): a sample
still linked to an animal keeps its number as its source, the link columns
and the table go, and on SQLite the rebuilt tables keep what app/integrity.py
gives every table (AUTOINCREMENT, foreign keys checked at commit)."""
from __future__ import annotations

import importlib
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
        with engine.begin() as conn:
            ops = Operations(MigrationContext.configure(conn))
            inspector = lambda: sa.inspect(conn)  # noqa: E731  (fresh: the revision changes the schema)
            with mock.patch.object(migration, "op", ops), \
                    mock.patch.object(migration, "has_table", lambda name: inspector().has_table(name)), \
                    mock.patch.object(migration, "has_column",
                                      lambda t, c: inspector().has_table(t)
                                      and c in {x["name"] for x in inspector().get_columns(t)}):
                migration.upgrade()

    def test_the_table_and_its_links_go_and_a_linked_sample_keeps_its_animal(self):
        linked, plain = self.as_before()
        rows = {t: one(f"select count(*) from {t}") for t in LINKED}
        self.run_revision()
        self.assertFalse(sa.inspect(engine).has_table("animals"))
        for table in LINKED:
            self.assertNotIn("animal_id_fk", columns(table), table)
            self.assertEqual(one(f"select count(*) from {table}"), rows[table], f"{table} lost rows")
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
