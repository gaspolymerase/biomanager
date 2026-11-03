"""The lab's count of changes to the records a device keeps offline.

Every record a phone or desktop keeps (SYNCED_TABLES) carries sync_version:
each change, in any transaction, gives it the next number of one lab-wide
count (the sync_clock row). Two questions then have one answer:

  What changed since I last looked?   rows whose sync_version is above my cursor,
                                      and sync_deletions above it
  Did this record change since I      its sync_version differs from the one
  read it?                            the device saw (app/sync.py)

The count is advanced inside the transaction that makes the change, by an
UPDATE of its one row, which PostgreSQL holds locked until that transaction
ends. So numbers are handed out in the order transactions commit, and a
device reading "above my cursor" never misses a change that committed later
with a lower number. SQLite has one writer at a time anyway.
"""
from __future__ import annotations

from sqlalchemy import event, inspect as sa_inspect, text
from sqlalchemy.orm import Session

from .models import CageRecord, SyncDeletion

# Off while the start-up steps before Alembic save records (startup.init_database).
enabled = True

SYNCED_TABLES = frozenset({"mice", "mouse_cages", "litters", "mouse_weights", "tasks", "mouse_racks",
                           "stock_units", "tanks"})


def _table(obj) -> str | None:
    name = getattr(getattr(obj, "__table__", None), "name", None)
    return name if name in SYNCED_TABLES else None


def advance(session, n: int) -> int:
    """Take n numbers from the count; returns the last of them."""
    conn = session.connection()
    if not conn.execute(text("UPDATE sync_clock SET value = value + :n WHERE id = 1"), {"n": n}).rowcount:
        # A database made new by create_all() has no row yet.
        conn.execute(text("INSERT INTO sync_clock (id, value) VALUES (1, 0) ON CONFLICT (id) DO NOTHING"))
        conn.execute(text("UPDATE sync_clock SET value = value + :n WHERE id = 1"), {"n": n})
    return int(conn.execute(text("SELECT value FROM sync_clock WHERE id = 1")).scalar_one())


def current(session) -> int:
    """The count now: a device that has read everything up to here is current."""
    value = session.execute(text("SELECT value FROM sync_clock WHERE id = 1")).scalar()
    return int(value or 0)


@event.listens_for(Session, "before_flush")
def _stamp(session, flush_context, instances):
    if not enabled:
        return
    changed = [o for o in session.new if _table(o)]
    changed += [o for o in session.dirty
                if _table(o) and session.is_modified(o, include_collections=False)]
    deleted = [o for o in session.deleted if _table(o) and sa_inspect(o).identity is not None]
    # A cage's litters travel in its row (sync.cage_row), so a litter born,
    # corrected or weaned is a change to its cage.
    for o in list(session.new) + list(session.dirty) + list(session.deleted):
        if getattr(getattr(o, "__table__", None), "name", None) != "cage_litters":
            continue
        if o in session.dirty and not session.is_modified(o, include_collections=False):
            continue
        cage = o.__dict__.get("cage")
        if cage is None and o.cage_id_fk is not None:
            cage = session.get(CageRecord, o.cage_id_fk)
        if cage is not None and cage not in changed and cage not in session.deleted and cage not in session.new:
            changed.append(cage)
    if not changed and not deleted:
        return
    last = advance(session, len(changed) + len(deleted))
    number = last - len(changed) - len(deleted) + 1
    for obj in changed:
        obj.sync_version = number
        number += 1
    for obj in deleted:
        session.add(SyncDeletion(table_name=_table(obj), record_id=sa_inspect(obj).identity[0], sync_version=number))
        number += 1
