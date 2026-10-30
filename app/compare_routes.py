"""Compare one plasmid with another (app/plasmid_compare.py), from the
Overview tab's **Compare with…**.

- `compare/others.json`: the other plasmids that have a sequence, for the
  picker.
- `compare.json?other=<plasmid number>`: the differences between this
  plasmid and that one, with the features each falls in and what it does to
  a protein, and both maps' features so the page can draw one over the other.
"""
from __future__ import annotations

import json

from flask import Blueprint, abort, g, jsonify, request, url_for
from sqlalchemy import select

from . import plasmid_compare
from .db import SessionLocal
from .i18n import gettext
from .models import PlasmidRecord

bp = Blueprint("compare", __name__)


@bp.before_request
def require_login():
    if g.get("user") is None:
        abort(401)
    return None


def _plasmid(session, row_id: int) -> PlasmidRecord:
    p = session.get(PlasmidRecord, row_id)
    if p is None:
        abort(404)
    return p


def _features(p: PlasmidRecord) -> list:
    try:
        return json.loads(p.features_json) if p.features_json else []
    except json.JSONDecodeError:
        return []


def _label(p: PlasmidRecord) -> str:
    return f"#{p.plasmid_id} {p.name}".strip() if p.name else f"#{p.plasmid_id}"


@bp.route("/plasmids/<int:row_id>/compare/others.json")
def others(row_id: int):
    with SessionLocal() as session:
        p = _plasmid(session, row_id)
        rows = session.scalars(select(PlasmidRecord).where(PlasmidRecord.full_sequence != "",
                                                           PlasmidRecord.id != p.id)
                               .order_by(PlasmidRecord.plasmid_id)).all()
        return jsonify({"ok": True, "plasmids": [{"number": r.plasmid_id, "label": _label(r),
                                                  "length": len(r.full_sequence or "")} for r in rows]})


@bp.route("/plasmids/<int:row_id>/compare.json")
def compare(row_id: int):
    with SessionLocal() as session:
        a = _plasmid(session, row_id)
        try:
            number = int(request.args.get("other", ""))
        except ValueError:
            return jsonify({"ok": False, "error": gettext("Pick a plasmid to compare with.")}), 400
        b = session.scalar(select(PlasmidRecord).where(PlasmidRecord.plasmid_id == number))
        if b is None:
            return jsonify({"ok": False, "error": gettext("That plasmid is not in the lab any more.")}), 404
        if b.id == a.id:
            return jsonify({"ok": False, "error": gettext("Pick a different plasmid.")}), 400
        result = plasmid_compare.compare(
            a.full_sequence or "", bool(a.is_circular), _features(a), _label(a),
            b.full_sequence or "", bool(b.is_circular), _features(b), _label(b))
        if not result.get("ok"):
            return jsonify({"ok": False, "error": gettext("One of the plasmids has no sequence yet.")}), 400
        result["b"]["url"] = url_for("plasmid_page", number=b.plasmid_id)
        return jsonify(result)
