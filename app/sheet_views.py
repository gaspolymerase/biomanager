"""Saved views of a sheet: a search, filter chips, a sort and the columns
shown, under a name, for going back to in one click (static/sheet-views.js).
And who else has the sheet open, shown beside its views.

Each person's own, per sheet, kept with the lab (app_settings) so they
follow the person to another computer. What a view holds is only how the
sheet is shown, never the records, so it is checked for shape and size and
otherwise stored as given.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from flask import Blueprint, abort, g, jsonify, request
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from . import inventory_service
from .db import SessionLocal
from .models import PagePresence, UserAccount

bp = Blueprint("sheet_views", __name__)

MAX_VIEWS = 20
MAX_NAME = 60
MAX_BYTES = 24_000
TABLE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,30}$")


def _key(table_id: str, username: str) -> str:
    return f"views:{table_id}:{username}"


def clean(views) -> list[dict]:
    """The views as they may be stored: each a name and a state of known
    parts, the list no longer than MAX_VIEWS."""
    out = []
    if not isinstance(views, list):
        return out
    for view in views[:MAX_VIEWS]:
        if not isinstance(view, dict):
            continue
        name = str(view.get("name") or "").strip()[:MAX_NAME]
        state = view.get("state") if isinstance(view.get("state"), dict) else {}
        if not name:
            continue
        fields = [{"key": str(f.get("key"))[:80], "value": str(f.get("value", ""))[:200],
                   "label": str(f.get("label", ""))[:80]}
                  for f in (state.get("fields") or [])[:12] if isinstance(f, dict) and f.get("key")]
        sort = state.get("sort")
        sort = [str(sort[0])[:80], -1 if sort[1] == -1 else 1] if isinstance(sort, list) and len(sort) == 2 else None
        hidden = state.get("hidden")
        out.append({"name": name, "state": {
            "query": str(state.get("query") or "")[:200],
            "chip": str(state.get("chip") or "")[:120],
            "fields": fields,
            "sort": sort,
            **({"hidden": [str(h)[:80] for h in hidden[:80]]} if isinstance(hidden, list) else {}),
        }})
    return out


@bp.route("/sheet-views/<table_id>", methods=["GET", "POST"])
def views(table_id: str):
    if g.get("user") is None:
        abort(401)
    if not TABLE_ID.match(table_id):
        abort(404)
    key = _key(table_id, g.user.username)
    with SessionLocal() as session:
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            kept = clean(payload.get("views"))
            text = json.dumps(kept, ensure_ascii=False)
            if len(text.encode("utf-8")) > MAX_BYTES:
                return jsonify({"ok": False, "error": "too many views"}), 413
            inventory_service.set_setting(session, key, text)
            session.commit()
            return jsonify({"ok": True, "views": kept})
        try:
            stored = json.loads(inventory_service.get_setting(session, key, "[]") or "[]")
        except ValueError:
            stored = []
    return jsonify({"views": clean(stored)})


PRESENCE_TTL = timedelta(seconds=70)
PATH = re.compile(r"^/[A-Za-z0-9_./:-]{0,120}$")


@bp.route("/presence", methods=["POST"])
def presence():
    """I have this page open (or, with left=1, no longer): who else has it."""
    if g.get("user") is None:
        abort(401)
    payload = request.get_json(silent=True) or {}
    path = str(payload.get("path") or "")
    view = str(payload.get("view") or "")[:40]
    if not PATH.match(path):
        abort(400)
    path = f"{path}?view={view}" if view else path
    me = g.user.username
    now = datetime.utcnow()
    with SessionLocal() as session:
        session.execute(delete(PagePresence).where(PagePresence.updated_at < now - PRESENCE_TTL))
        row = session.scalar(select(PagePresence).where(PagePresence.path == path, PagePresence.username == me))
        if payload.get("left"):
            if row is not None:
                session.delete(row)
        elif row is None:
            session.add(PagePresence(path=path, username=me, updated_at=now))
        else:
            row.updated_at = now
        names = session.scalars(select(PagePresence.username).where(
            PagePresence.path == path, PagePresence.username != me,
            PagePresence.updated_at >= now - PRESENCE_TTL).order_by(PagePresence.username)).all()
        people = session.execute(select(UserAccount.username, UserAccount.display_name, UserAccount.short_name)
                                 .where(UserAccount.username.in_(names))).all() if names else []
        try:
            session.commit()
        except IntegrityError:      # the same person's other window, at the same moment
            session.rollback()
    return jsonify({"here": [{"username": u, "name": full or u, "short": short or ""} for u, full, short in people]})
