"""A batch made of a sheet's own row saves (static/selection-bar.js, Set a
field…).

A column the server has no batch route for is set on the ticked rows one
by one, each through its row's save, as editing that cell would: the same
checks, the same rules for who may change it, and the sheet is not
reloaded. So that the rows still make one change in Batch history, to read
and undo as one, the page opens a batch first and sends its number with
each row's save (X-Batch-Id); the audit rows those saves write join it
(app/audit.py), and the page closes it with how many rows it changed.

  POST /batch/start   {"what": "Set Status", "table": "mice"}  -> {"ok", "id"}
  POST /batch/<id>/finish  {"count": 12}
"""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, abort, g, jsonify, request

from .db import SessionLocal
from .models import BatchRecord

bp = Blueprint("batch_edit", __name__)

OPEN_FOR = timedelta(minutes=30)


def _mine(session, batch_id) -> BatchRecord | None:
    """A batch this person opened in the last half hour, not undone."""
    try:
        row = session.get(BatchRecord, int(batch_id))
    except (TypeError, ValueError):
        return None
    user = g.get("user")
    if row is None or user is None or row.actor != user.username or row.undone_at is not None:
        return None
    if row.created_at is None or datetime.utcnow() - row.created_at > OPEN_FOR:
        return None
    return row


@bp.before_app_request
def join_batch():
    """A row's save sent as part of a batch writes its changes into it."""
    batch_id = request.headers.get("X-Batch-Id")
    if not batch_id or request.method != "POST" or g.get("user") is None:
        return
    with SessionLocal() as session:
        row = _mine(session, batch_id)
        if row is not None:
            g.audit_batch_id = row.id
            g.audit_batch = row.description


@bp.post("/batch/start")
def start():
    if g.get("user") is None:
        abort(401)
    data = request.get_json(silent=True) or {}
    what = " ".join(str(data.get("what") or "").split())[:200] or "set a field"
    table = str(data.get("table") or "")[:40]
    with SessionLocal() as session:
        row = BatchRecord(action="update", description=what, target_table=table, actor=g.user.username)
        session.add(row)
        session.commit()
        return jsonify({"ok": True, "id": row.id})


@bp.post("/batch/<int:batch_id>/finish")
def finish(batch_id: int):
    data = request.get_json(silent=True) or {}
    with SessionLocal() as session:
        row = _mine(session, batch_id)
        if row is None:
            return jsonify({"ok": False}), 404
        try:
            row.record_count = max(0, int(data.get("count") or 0))
        except (TypeError, ValueError):
            row.record_count = 0
        session.commit()
    return jsonify({"ok": True})
