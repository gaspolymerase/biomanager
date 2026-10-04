"""Undoing a batch.

Reverses the audit entries belonging to a BatchRecord, newest first, using
the structured before/after values the flush listener stored:

  * a **create** is undone by deleting the row
  * an **update** is undone by putting each column back to its "before"
  * a **delete** is undone by re-inserting the row from its snapshot

Two things it refuses to do. It will not touch a record that has been
changed again since the batch — reverting then would silently discard
whoever's later edit — and it will not undo a batch twice. Both are
reported rather than skipped quietly, because "undo did less than you
think" is worse than "undo declined".

The undo is itself audited, as its own batch, so the history stays honest.
"""
from __future__ import annotations

import re

import json
from datetime import datetime

from sqlalchemy import or_, select

from . import audit
from .i18n import gettext, ngettext
from .models import AuditEntry, BatchRecord

# Only tables the app knows how to rebuild a row for.
UNDOABLE_TABLES = set(audit.TRACKED_TABLES) - {"users"}


def _model_for(table: str):
    """The mapped class behind a table name."""
    from .db import Base

    for mapper in Base.registry.mappers:
        if mapper.class_.__tablename__ == table:
            return mapper.class_
    return None


def describe(session, batch: BatchRecord) -> dict:
    """What undoing this batch would do, without doing it."""
    entries = session.scalars(
        select(AuditEntry).where(AuditEntry.batch_id_fk == batch.id)
    ).all()
    counts = {"create": 0, "update": 0, "delete": 0}
    for entry in entries:
        if entry.action in counts:
            counts[entry.action] += 1
    return {
        "entries": len(entries),
        "creates": counts["create"],
        "updates": counts["update"],
        "deletes": counts["delete"],
        "blocked": blockers(session, batch),
    }


def blockers(session, batch: BatchRecord) -> list[str]:
    """Reasons this batch cannot be cleanly reversed."""
    problems: list[str] = []
    if batch.is_undone:
        problems.append(gettext("Already undone by %(who)s on %(when)s.", who=batch.undone_by,
                                when=f"{batch.undone_at:%Y-%m-%d %H:%M}"))
        return problems

    entries = session.scalars(
        select(AuditEntry).where(AuditEntry.batch_id_fk == batch.id)
    ).all()
    if not entries:
        problems.append(gettext("No recorded changes to reverse."))
        return problems

    for entry in entries:
        if entry.table_name not in UNDOABLE_TABLES:
            problems.append(gettext("Table %(table)s cannot be reversed automatically.", table=entry.table_name))
            break
    late = _changed_since(session, batch, entries)
    if late:
        problems.append(late)
    return problems


def _changed_since(session, batch: BatchRecord, entries=None) -> str:
    """Why reverting would throw away a later edit, or ""."""
    if entries is None:
        entries = session.scalars(select(AuditEntry).where(AuditEntry.batch_id_fk == batch.id)).all()
    # Its own undos and redos ("undo of batch #N", and undos of those) are
    # not someone's later edit: a redone batch can be undone again.
    chain, frontier = {batch.id}, {batch.id}
    while frontier:
        found = set(session.scalars(select(BatchRecord.id).where(
            BatchRecord.description.in_([f"undo of batch #{n}" for n in frontier])))) - chain
        chain |= found
        frontier = found
    touched_since = 0
    for entry in entries:
        if entry.table_name not in UNDOABLE_TABLES:
            continue
        later = session.scalar(
            select(AuditEntry.id).where(
                AuditEntry.table_name == entry.table_name,
                AuditEntry.record_id == entry.record_id,
                AuditEntry.id > entry.id,
                or_(AuditEntry.batch_id_fk.is_(None),
                    AuditEntry.batch_id_fk.not_in(chain)),
            ).limit(1)
        )
        if later is not None:
            touched_since += 1
    if touched_since:
        return ngettext("%(num)s record changed after this batch — reverting would throw that later edit away.",
                        "%(num)s records changed after this batch — reverting would throw those later edits away.",
                        touched_since)
    return ""


def undo(session, batch: BatchRecord, actor: str, force: bool = False) -> dict:
    """Reverse a batch. Returns a summary; raises nothing on partial work."""
    problems = blockers(session, batch)
    if problems and not force:
        return {"ok": False, "problems": problems, "reverted": 0}
    if batch.is_undone:
        return {"ok": False, "problems": [gettext("Already undone.")], "reverted": 0}
    # Claim the batch first, in the database: of two people pressing Undo at
    # the same moment, only one gets it (the other's UPDATE finds it taken).
    from sqlalchemy import update
    claimed = session.execute(update(BatchRecord).where(BatchRecord.id == batch.id, BatchRecord.undone_at.is_(None))
                              .values(undone_at=datetime.utcnow(), undone_by=actor)).rowcount
    if not claimed:
        return {"ok": False, "problems": [gettext("Already undone.")], "reverted": 0}
    session.refresh(batch)
    if not force:
        # Lock what it will change (PostgreSQL; SQLite has one writer at a
        # time anyway), then look again: an edit saved between the check
        # above and now would otherwise be overwritten without a word.
        for entry in session.scalars(select(AuditEntry).where(AuditEntry.batch_id_fk == batch.id)):
            model = _model_for(entry.table_name)
            if model is not None and entry.action != "delete":
                session.get(model, entry.record_id, with_for_update=True, populate_existing=True)
        late = _changed_since(session, batch)
        if late:
            session.rollback()
            return {"ok": False, "problems": [late], "reverted": 0}

    entries = session.scalars(
        select(AuditEntry)
        .where(AuditEntry.batch_id_fk == batch.id)
        .order_by(AuditEntry.id.desc())
    ).all()
    # Newest first, but every record the batch created goes last: first the
    # records that point at it are put back as they were. In the log order
    # alone a redo's re-made cage comes after its mice's moves (inserts are
    # logged after the flush, updates before it), so undoing that redo would
    # delete the cage while its mice still pointed at it, and lose them.
    entries = sorted(entries, key=lambda e: e.action == "create")

    reverted = skipped = 0
    notes: list[str] = []
    touched: list = []       # rows this undo edited or re-inserted
    previous: dict = {}      # (id(row), field) -> value before the undo

    # The reversal is itself a batch, so the log shows both directions.
    with audit.batch(session, "update", f"undo of batch #{batch.id}",
                     batch.target_table) as undo_row:
        for entry in entries:
            model = _model_for(entry.table_name)
            if model is None:
                skipped += 1
                continue
            payload = {}
            if entry.changes_json:
                try:
                    payload = json.loads(entry.changes_json)
                except ValueError:
                    payload = {}

            if entry.action == "create":
                row = session.get(model, entry.record_id)
                if row is None:
                    skipped += 1
                    continue
                # Write the reverts made so far first. A cage created by the
                # batch still has the batch's mice pointing at it in the
                # database; deleting it before their restored cage is
                # flushed would let the ORM null that restored value.
                session.flush()
                session.expire(row)        # its collections, as the database now has them
                held = _unlink_references(session, model, row)
                if held:
                    skipped += 1
                    notes.append(f"{entry.record_label}: kept, still used by {held}")
                    continue
                session.delete(row)
                reverted += 1

            elif entry.action == "update":
                row = session.get(model, entry.record_id)
                changes = payload.get("changes") or {}
                if row is None or not changes:
                    skipped += 1
                    continue
                for field, pair in changes.items():
                    if not isinstance(pair, list) or len(pair) != 2:
                        continue
                    if hasattr(row, field):
                        previous.setdefault((id(row), field), getattr(row, field))
                        setattr(row, field, audit._decode(pair[0]))
                touched.append(row)
                reverted += 1

            elif entry.action == "delete":
                snapshot = payload.get("snapshot") or {}
                if not snapshot:
                    skipped += 1
                    notes.append(f"{entry.record_label}: no snapshot to restore from")
                    continue
                if session.get(model, snapshot.get("id")) is not None:
                    skipped += 1
                    notes.append(f"{entry.record_label}: that id is in use again")
                    continue
                values = {k: audit._decode(v) for k, v in snapshot.items()
                          if hasattr(model, k)}
                restored = model(**values)
                session.add(restored)
                touched.append(restored)
                reverted += 1

        # A restored link may name a record that is gone for good (deleted
        # later, outside this batch). Foreign keys refuse the commit then, so
        # say so instead: an optional link is cleared, a required one keeps
        # the row out (a restored row) or keeps its current value (an edit).
        session.flush()
        for obj in touched:
            for label, fixed in _missing_parents(session, obj, previous):
                notes.append(f"{audit._label(obj)}: {label}")
                if fixed is None:
                    session.delete(obj)
                    reverted -= 1
                    skipped += 1
                    break

        batch.undone_at = datetime.utcnow()
        batch.undone_by = actor
        undo_row.record_count = reverted
        # Undoing an undo is a redo: the batch it undid is in force again,
        # and Batch history says so (and offers its Undo again).
        redone = re.match(r"undo of batch #(\d+)$", batch.description or "")
        if redone:
            original = session.get(BatchRecord, int(redone.group(1)))
            if original is not None:
                original.undone_at, original.undone_by = None, ""

    return {"ok": True, "reverted": reverted, "skipped": skipped,
            "problems": problems if force else [], "notes": notes}


def _unlink_references(session, model, row) -> str:
    """Before undoing a create, clear optional links other records have to
    it since (as deleting it from the app would). Returns what still needs
    it through a required link, in words; the row is then left in place."""
    from sqlalchemy import func

    from .db import Base

    required, optional = [], []
    for table in Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            if fk.column.table is not model.__table__:
                continue
            child = _model_for(table.name)
            if child is None:
                continue
            key = child.__mapper__.get_property_by_column(fk.parent).key
            where = getattr(child, key) == row.id
            if fk.parent.nullable:
                optional.append((child, key, where))
                continue
            n = session.scalar(select(func.count()).select_from(child).where(where)) or 0
            if n:
                required.append(f"{n} {table.name.replace('_', ' ')} row{'s' if n != 1 else ''}")
    if required:
        return ", ".join(required)  # kept as it is, links and all
    for child, key, where in optional:
        for other in session.scalars(select(child).where(where)):
            if other is not row:
                setattr(other, key, None)
    return ""


def _missing_parents(session, obj, previous: dict):
    """(message, fixed) for each link on obj naming a record that is gone.
    fixed is None when the row cannot stand (a required link on a restored
    row); otherwise the link was cleared or put back."""
    from sqlalchemy import inspect as sa_inspect

    state = sa_inspect(obj)
    if state.deleted or state.detached or state.was_deleted:
        return  # removed again later in this undo
    mapper = state.mapper
    for fk in mapper.local_table.foreign_keys:
        attr = mapper.get_property_by_column(fk.parent).key
        value = getattr(obj, attr, None)
        if value is None:
            continue
        parent = _model_for(fk.column.table.name)
        if parent is None or session.get(parent, value) is not None:
            continue
        noun = fk.column.table.name.replace("_", " ")
        if fk.parent.nullable:
            setattr(obj, attr, None)
            yield f"its {noun} #{value} no longer exists; link cleared", True
        elif (id(obj), attr) in previous:
            setattr(obj, attr, previous[(id(obj), attr)])
            yield f"its {noun} #{value} no longer exists; {attr} left as it was", True
        else:
            yield f"not restored: its {noun} #{value} no longer exists", None
            return


def recent(session, limit: int = 50, actor: str | None = None) -> list[BatchRecord]:
    stmt = select(BatchRecord).order_by(BatchRecord.created_at.desc()).limit(limit)
    if actor:
        stmt = stmt.where(BatchRecord.actor == actor)
    return list(session.scalars(stmt).all())
