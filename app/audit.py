"""Automatic change history.

Previously only deletions were logged, which answered "who removed this
mouse?" but not "who changed its genotype, and what was it before?" — the
question that actually comes up, and the one a three-year retention rule
expects you to be able to answer.

Rather than adding a call to each of the hundred-odd write routes, this
hooks SQLAlchemy's flush. Any tracked row that is inserted, updated or
deleted gets an `audit_log` entry with a field-level diff, wherever in the
app the change came from.

SQLAlchemy already tracks the before/after values for us, so the diff is
free: `attributes.get_history()` knows what each column was when it was
loaded.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime
from types import SimpleNamespace

from sqlalchemy import event, inspect as sa_inspect
from sqlalchemy.orm import Session

from .models import AuditEntry, BatchRecord

# Tables worth a change history: records a person curates and would argue
# about later.
TRACKED_TABLES = {
    "mice", "mouse_cages", "litters", "strains", "plasmids", "orders",
    "samples", "animals", "experiments", "experiment_mice",
    "tanks", "fish", "fish_lines", "clutches", "water_systems", "fish_racks", "fish_sac_log",
    "organisms", "organism_housing", "organism_lines", "organism_cohorts",
    "organism_crosses", "organism_modules", "organism_module_fields",
    "organism_preservation", "organism_genotypes", "users",
    "inventory_modules", "inventory_items", "inventory_racks", "mouse_racks",
    "stock_modules", "stock_incubators", "stock_racks", "stock_genotypes", "stock_units",
    "stock_frozen",
}
TRACKED_TABLES |= {"plasmid_boxes"}
TRACKED_TABLES |= {"experiment_steps", "experiment_step_records", "experiment_subjects", "experiment_readings", "experiment_regimens", "record_signatures"}   # an experiment's manipulations, planned and done
TRACKED_TABLES |= {"user_identities"}   # who connected which Google/Microsoft account, and when   # so deleting a box (which unplaces its plasmids) can be undone

# What each table is, for the Audit log and Batch history (not "inventory_items").
TABLE_LABELS = {
    "mice": "Mice", "mouse_cages": "Cages", "litters": "Litters", "strains": "Strains", "plasmids": "Plasmids",
    "plasmid_boxes": "Plasmid boxes", "orders": "Orders", "samples": "Samples", "animals": "Animals",
    "experiments": "Experiments", "experiment_mice": "Experiment animals", "tanks": "Tanks", "fish": "Fish",
    "fish_lines": "Fish lines", "clutches": "Clutches", "water_systems": "Water systems", "fish_racks": "Fish racks",
    "fish_sac_log": "Fish sac log", "organisms": "Animals", "organism_housing": "Housing", "organism_lines": "Lines",
    "organism_cohorts": "Cohorts", "organism_crosses": "Crosses", "organism_modules": "Databases",
    "organism_module_fields": "Database fields", "organism_preservation": "Preserved stocks",
    "organism_genotypes": "Genotypes", "users": "People", "inventory_modules": "Databases",
    "inventory_items": "Records", "inventory_racks": "Boxes", "mouse_racks": "Mouse racks",
    "stock_modules": "Databases", "stock_incubators": "Incubators", "stock_racks": "Racks",
    "stock_genotypes": "Genotypes", "stock_units": "Vials and plates", "stock_frozen": "Frozen lots",
    "experiment_steps": "Experiment steps", "experiment_step_records": "Steps done",
    "experiment_subjects": "Experiment subjects", "experiment_readings": "Readings",
    "experiment_regimens": "Regimens", "record_signatures": "Signatures", "user_identities": "Sign-in accounts",
}


def table_label(name: str) -> str:
    return TABLE_LABELS.get(name or "", (name or "").replace("_", " ").capitalize())


# High-churn or derived rows: logging them would bury the signal.
IGNORED_TABLES = {
    "audit_log", "batches", "notifications", "calendar_events", "tasks",
    "notebook_pages", "notebook_entries", "notebook_tabs",
    "organism_events", "organism_due", "organism_measurements",
    "dropdown_options", "mouse_weights", "water_logs",
}

# Columns that change on every save and say nothing about intent.
NOISE_COLUMNS = {"updated_at", "updated_by", "created_at"}

# Never write a credential, or its hash, into a log.
REDACTED_COLUMNS = {"password_hash", "token", "access_token", "refresh_token",
                    "client_secret"}

MAX_DETAIL = 2000


def _label(obj) -> str:
    """The most human name this row has."""
    custom = getattr(obj, "audit_label", None)
    if isinstance(custom, str) and custom:
        return custom
    for attr in ("mouse_id", "cage_id", "litter_id", "code", "sample_id",
                 "tank_id", "clutch_id", "strain_name", "name", "title",
                 "item_name", "username", "key"):
        value = getattr(obj, attr, None)
        if value not in (None, ""):
            return str(value)
    return str(getattr(obj, "id", "") or "")


def _encode(value):
    """JSON-safe form of a column value, reversible by _decode()."""
    if isinstance(value, datetime):
        return {"__dt__": value.isoformat()}
    if isinstance(value, date):
        return {"__d__": value.isoformat()}
    return value


def _decode(value):
    if isinstance(value, dict):
        if "__dt__" in value:
            return datetime.fromisoformat(value["__dt__"])
        if "__d__" in value:
            return date.fromisoformat(value["__d__"])
    return value


def _short(value) -> str:
    if value is None:
        return "∅"
    text = str(value)
    return text if len(text) <= 80 else text[:77] + "…"


def _diff(obj) -> dict[str, tuple]:
    """Columns whose value actually changed in this flush."""
    state = sa_inspect(obj)
    changes: dict[str, tuple] = {}
    for attr in state.mapper.column_attrs:
        name = attr.key
        if name in NOISE_COLUMNS:
            continue
        history = state.attrs[name].history
        if not history.has_changes():
            continue
        before = history.deleted[0] if history.deleted else None
        after = history.added[0] if history.added else None
        if before == after:
            continue
        if name in REDACTED_COLUMNS:
            changes[name] = ("[redacted]", "[redacted]")
        else:
            changes[name] = (before, after)
    return changes


def _table_of(obj) -> str | None:
    table = getattr(obj, "__tablename__", None)
    if table is None or table in IGNORED_TABLES or table not in TRACKED_TABLES:
        return None
    return table


@contextmanager
def batch(session, action: str, description: str, target_table: str = ""):
    """Group everything written inside this block into one batch.

        with audit.batch(db_session, "update", "bulk set owner", "mice"):
            ...
        db_session.commit()

    The BatchRecord is flushed immediately so the audit rows written during
    the block can point at it, and the record count is filled in on the way
    out. A nested call joins the outer batch rather than starting its own.
    """
    from flask import g, has_request_context

    outer = None
    if has_request_context():
        outer = g.get("audit_batch_id")
    if outer is not None:
        # Inside another batch (an approved proposal runs many pages as one,
        # app/proposals.py): its changes are the outer batch's, so the page
        # gets a stand-in it can set a count or description on, and no
        # second, empty batch is written.
        yield SimpleNamespace(id=outer, action=action, description=description,
                              target_table=target_table, record_count=0)
        session.flush()
        return

    row = BatchRecord(
        action=action,
        description=description,
        target_table=target_table,
        actor=_actor(),
    )
    session.add(row)
    session.flush()

    if has_request_context():
        g.audit_batch_id = row.id
        g.audit_batch = description
    try:
        yield row
    except Exception:
        raise
    else:
        # Flush while the batch id is still set. Without this, a caller that
        # commits *after* the block would have its audit rows written with
        # no batch attached — the changes would be recorded but orphaned.
        session.flush()
    finally:
        if has_request_context():
            g.audit_batch_id = None
            g.audit_batch = ""


def _batch_id():
    try:
        from flask import g, has_request_context
        if has_request_context():
            return g.get("audit_batch_id")
    except Exception:
        pass
    return None


def _batch_label() -> str:
    """The batch a change belongs to, if a bulk route declared one.

    Bulk actions touch many rows, and without this the log is forty
    near-identical lines. Tagging them lets the audit view group — and read
    — them as the single operation they were.
    """
    try:
        from flask import g, has_request_context
        if has_request_context():
            return (g.get("audit_batch") or "").strip()
    except Exception:
        pass
    return ""


def _actor() -> str:
    """Who is making the change. Falls back to a marker for background jobs
    and management scripts, which have no request context."""
    try:
        from flask import g, has_request_context
        if has_request_context():
            user = g.get("user")
            if user is not None:
                return user.username
            return "anonymous"
    except Exception:
        pass
    return "system"


def _entry(table: str, obj, action: str, details: str, actor: str,
           payload: dict | None = None) -> AuditEntry:
    label = _batch_label()
    if label:
        details = f"[{label}] {details}".strip()
    return AuditEntry(
        table_name=table,
        record_id=getattr(obj, "id", 0) or 0,
        record_label=_label(obj),
        action=action,
        changed_by=actor,
        details=details[:MAX_DETAIL],
        batch_id_fk=_batch_id(),
        changes_json=json.dumps(payload, separators=(",", ":")) if payload else "",
    )


def _snapshot(obj) -> dict:
    """Every column of a row, for restoring it after a delete."""
    return {
        attr.key: _encode(getattr(obj, attr.key, None))
        for attr in sa_inspect(obj).mapper.column_attrs
        if attr.key not in REDACTED_COLUMNS
    }


@event.listens_for(Session, "before_flush")
def _record_changes(session, flush_context, instances):
    """Turn this flush's pending work into audit rows.

    Updates and deletes are captured here, before the flush, because this is
    the last moment the previous values exist. Inserts cannot be: their
    primary key is assigned by the INSERT itself, so recording them now
    would store record_id = 0 and undo would have nothing to find. Those are
    handed to the after_flush hook below.

    Either way the rows land in the same transaction as the change they
    describe, so an audit entry can never outlive a rolled-back edit.
    """
    actor = _actor()
    pending: list[AuditEntry] = []

    # Held for after_flush, once the database has assigned their ids.
    session.info["audit_pending_creates"] = [
        (table, obj) for obj, table in
        ((o, _table_of(o)) for o in session.new) if table
    ]

    for obj in session.dirty:
        table = _table_of(obj)
        if not table or not session.is_modified(obj, include_collections=False):
            continue
        changes = _diff(obj)
        if not changes:
            continue
        details = "; ".join(
            f"{field}: {_short(before)} → {_short(after)}"
            for field, (before, after) in sorted(changes.items())
        )
        payload = {"changes": {field: [_encode(before), _encode(after)]
                               for field, (before, after) in changes.items()}}
        pending.append(_entry(table, obj, "update", details, actor, payload))

    for obj in session.deleted:
        table = _table_of(obj)
        if not table:
            continue
        summary = ", ".join(
            f"{attr.key}={_short(getattr(obj, attr.key, None))}"
            for attr in sa_inspect(obj).mapper.column_attrs
            if attr.key not in NOISE_COLUMNS
            and attr.key not in REDACTED_COLUMNS
            and getattr(obj, attr.key, None) not in (None, "", 0)
        )
        pending.append(_entry(table, obj, "delete", summary, actor,
                              {"snapshot": _snapshot(obj)}))

    for entry in pending:
        session.add(entry)


@event.listens_for(Session, "after_flush")
def _record_inserts(session, flush_context):
    """Log inserts now that their primary keys exist.

    Written with a Core INSERT rather than session.add(): the unit of work
    for this flush has already been computed, so adding ORM objects here
    would either be ignored or force another flush.
    """
    created = session.info.pop("audit_pending_creates", None)
    if not created:
        return

    actor = _actor()
    batch_id = _batch_id()
    label = _batch_label()
    rows = []
    for table, obj in created:
        record_id = getattr(obj, "id", None)
        if not record_id:
            continue
        rows.append({
            "table_name": table,
            "record_id": record_id,
            "record_label": _label(obj),
            "action": "create",
            "changed_by": actor,
            "changed_at": datetime.utcnow(),
            "details": f"[{label}]" if label else "",
            "batch_id_fk": batch_id,
            "changes_json": "",
        })
    if rows:
        session.execute(AuditEntry.__table__.insert(), rows)


ACTION_LABELS = {"create": "created", "update": "edited", "delete": "deleted"}
