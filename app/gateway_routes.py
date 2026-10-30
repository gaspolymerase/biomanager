"""Gateway BP/LR in Assemble a plasmid (app/gateway.py does the recombining).

- `POST gateway/preview`: the two plasmids by number, as JSON; back comes
  the reaction (BP or LR), the product and byproduct with their features,
  and what the reaction removes (a ccdB cassette).
- `POST gateway`: **Recombine** makes the entry or expression clone, Made
  from both plasmids by Gateway, with the att sites and the gene marked.
"""
from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, g, jsonify, redirect, request, url_for
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from . import audit, gateway, plasmid_lineage, plasmid_versions
from .db import SessionLocal
from .i18n import gettext
from .models import PlasmidRecord

bp = Blueprint("gateway", __name__, url_prefix="/plasmids/assembly/gateway")


@bp.before_request
def require_login():
    if g.get("user") is None:
        return redirect(url_for("login", next=request.path))
    return None


def _molecule(session, number) -> gateway.Molecule | None:
    try:
        number = int(number)
    except (TypeError, ValueError):
        return None
    p = session.scalar(select(PlasmidRecord).where(PlasmidRecord.plasmid_id == number))
    if p is None or not (p.full_sequence or ""):
        return None
    try:
        features = json.loads(p.features_json) if p.features_json else []
    except json.JSONDecodeError:
        features = []
    mol = gateway.Molecule((p.full_sequence or "").upper(), bool(p.is_circular), features,
                           p.name or f"#{p.plasmid_id}")
    mol.row, mol.number = p.id, p.plasmid_id       # type: ignore[attr-defined]
    return mol


def problem_text(problem: gateway.Problem) -> str:
    return {
        "no_sequence": gettext("%(name)s has no sequence yet.", name=problem.details.get("name", "")),
        "no_reaction": gettext("These two plasmids don’t make a Gateway reaction: one needs attB sites and the other attP (for BP), or one attL and the other attR (for LR), site 1 and site 2 of each."),
        "no_core": gettext("The att sites don’t share enough sequence to recombine. Check they are real att sites."),
        "layout": gettext("%(name)s’s att sites must both read forwards, with site 1 before site 2.",
                          name=problem.details.get("name", "")),
    }.get(problem.code, gettext("These two plasmids don’t make a Gateway reaction."))


@bp.route("/preview", methods=["POST"])
def preview():
    sent = request.get_json(silent=True) or {}
    with SessionLocal() as session:
        a, b = _molecule(session, sent.get("a")), _molecule(session, sent.get("b"))
        if a is None or b is None:
            return jsonify({"ok": False, "error": gettext("Pick two plasmids, each with its sequence and att sites.")})
        if a.number == b.number:                    # type: ignore[attr-defined]
            return jsonify({"ok": False, "error": gettext("Pick two different plasmids.")})
        try:
            out = gateway.recombine(a, b)
        except gateway.Problem as problem:
            return jsonify({"ok": False, "error": problem_text(problem)})

        def shown(piece):
            return {"length": piece["length"], "kind": piece["kind"],
                    "features": [{"name": f["name"], "start": f["start"], "end": f["end"],
                                  "direction": f.get("direction", 1), "color": f.get("color") or "#cbd5e1"}
                                 for f in piece["features"]]}
        return jsonify({"ok": True, "reaction": out["reaction"], "insert": out["insert"], "backbone": out["backbone"],
                        "removed": out["removed"], "product": shown(out["product"]),
                        "byproduct": shown(out["byproduct"])})


def _make(session, user: str):
    a, b = _molecule(session, request.form.get("a")), _molecule(session, request.form.get("b"))
    if a is None or b is None or a.number == b.number:          # type: ignore[attr-defined]
        raise gateway.Problem("no_reaction")
    out = gateway.recombine(a, b)
    product = out["product"]
    insert_name = out["insert"]
    default = f"{insert_name}-{'entry' if out['reaction'] == 'BP' else 'expr'}"[:200]
    name = (request.form.get("name") or "").strip()[:200] or default
    record = PlasmidRecord(
        plasmid_id=(session.scalar(select(func.max(PlasmidRecord.plasmid_id))) or 0) + 1, name=name, owner=user,
        full_sequence=product["sequence"], is_circular=True, sequence_uploaded_at=datetime.utcnow(),
        features_json=json.dumps(product["features"]), notes=(request.form.get("notes") or "").strip())
    with audit.batch(session, "create", f"Gateway {out['reaction']}: {name}", "plasmids") as batch_row:
        batch_row.record_count = 1
        session.add(record)
        session.flush()
        for mol in (a, b):
            parent = session.get(PlasmidRecord, mol.row)       # type: ignore[attr-defined]
            plasmid_lineage.add_parent(session, record, parent=parent, role="template", method="gateway",
                                       user=user, details={"reaction": out["reaction"], "removed": out["removed"]})
        record.updated_at, record.updated_by = datetime.utcnow(), user
        plasmid_versions.record(session, record, "assembly", user, detail=f"Gateway {out['reaction']}")
    return record, out


@bp.route("", methods=["POST"])
def make():
    for _attempt in range(3):
        with SessionLocal() as session:
            try:
                record, out = _make(session, g.user.username)
                session.commit()
            except gateway.Problem as problem:
                return jsonify({"ok": False, "error": problem_text(problem)}), 400
            except IntegrityError:
                session.rollback()
                continue
            message = gettext("Plasmid #%(number)s %(name)s made by Gateway %(reaction)s.",
                              number=record.plasmid_id, name=record.name, reaction=out["reaction"])
            if out["removed"]:
                message += " " + gettext("%(what)s was removed.", what=", ".join(out["removed"]))
            return jsonify({"ok": True, "message": message, "number": record.plasmid_id,
                            "url": url_for("plasmid_page", number=record.plasmid_id)})
    return jsonify({"ok": False, "error": gettext("Couldn’t allocate a plasmid number. Try again.")}), 409
