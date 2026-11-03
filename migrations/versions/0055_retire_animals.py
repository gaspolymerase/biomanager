"""The old `animals` table goes (docs/adr/0007).

A generic table from the app's first versions that nothing has filled or
shown since: samples name their source by kind and number, calendar
events and notebook pages never linked to it from any page. Before it
goes, a sample still linked to an animal there keeps that animal's number
as its source (as start-up has done for older tables since samples got
sources). Then the link columns (`samples`, `calendar_events` and
`notebook_entries`.animal_id_fk) and the table are dropped. On SQLite the
three tables are rebuilt without the column, since a column declared to
reference a table that no longer exists refuses every insert while foreign
keys are enforced; the rebuild is app/integrity.py's (AUTOINCREMENT,
foreign keys checked at commit, every row copied and counted, indexes and
the next id kept), not Alembic's batch mode, which loses the foreign keys
checked at commit and the next id.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from migrations.helpers import has_column, has_table

revision = "0055_retire_animals"
down_revision = "0054_breeder_tanks"
branch_labels = None
depends_on = None

LINKED = ("samples", "calendar_events", "notebook_entries")


def upgrade() -> None:
    bind = op.get_bind()
    if has_table("animals") and has_column("samples", "animal_id_fk") and has_column("samples", "source_kind"):
        bind.execute(sa.text(
            "UPDATE samples SET source_kind = 'other', source_ref = "
            "(SELECT animal_id FROM animals WHERE animals.id = samples.animal_id_fk) "
            "WHERE animal_id_fk IS NOT NULL AND source_kind = '' "
            "AND EXISTS (SELECT 1 FROM animals WHERE animals.id = samples.animal_id_fk)"))
    linked = [table for table in LINKED if has_column(table, "animal_id_fk")]
    if bind.dialect.name == "sqlite":
        _rebuild_without_link(linked)
    else:
        for table in linked:
            op.drop_column(table, "animal_id_fk")        # and its foreign key with it
    if has_table("animals"):
        op.drop_table("animals")


def _rebuild_without_link(names: list[str]) -> None:
    from sqlalchemy import MetaData

    from app import integrity, models  # noqa: F401  (models registers the tables)
    from app.db import Base
    scratch = MetaData()            # a copy: the rebuild may loosen columns
    for table in Base.metadata.sorted_tables:
        table.to_metadata(scratch)
    conn = op.get_bind().connection.driver_connection
    if conn.execute("PRAGMA foreign_keys").fetchone()[0]:
        # Alembic's own engine leaves them off. With them on, dropping a table
        # to rebuild it would delete what points at it (calendar_repeats is
        # ON DELETE CASCADE), and they can't be turned off inside this transaction.
        raise RuntimeError("0055 rebuilds tables on SQLite and needs foreign keys off")
    audit_ok = "record_id" in {row[1] for row in conn.execute("PRAGMA table_info(audit_log)")}
    for name in names:
        integrity._rebuild_one(conn, scratch.tables[name], drop=frozenset({"animal_id_fk"}))
        integrity._restart_sequence(conn, name, audit_ok)


def downgrade() -> None:
    pass
