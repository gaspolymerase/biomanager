"""Site-directed mutagenesis from a plasmid's Sequence tab
(app/mutagenesis.py does the designing).

- `mutagenesis.json`: the picked bases (?start=&end=, 1-based; an end one
  before the start is an insertion there) become ?bases=, or a coding
  feature's amino acid changes (?feature=&protein=K79A): the two primers,
  the new plasmid's length and what it does to each protein.
- `silent-sites.json`: silent changes in a coding feature that add or take
  away an enzyme's site, near the picked bases.
- `POST mutagenesis`: **Make it**. The new plasmid, its features moved and
  the change marked, Made from the template by mutagenesis, a first
  version, and the two primers in the lab's Primers database to order.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from flask import Blueprint, abort, g, jsonify, request, url_for
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from . import audit, mutagenesis, plasmid_lineage, plasmid_versions, primer_records
from .db import SessionLocal
from .i18n import gettext
from .models import PlasmidRecord

bp = Blueprint("mutagenesis", __name__)


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


def _stored(p: PlasmidRecord) -> list[dict]:
    try:
        stored = json.loads(p.features_json) if p.features_json else []
    except json.JSONDecodeError:
        stored = []
    return [f for f in stored if isinstance(f, dict)]


def _features(p: PlasmidRecord) -> list[dict]:
    """The annotations the Sequence tab numbers (primers' marks left out)."""
    return [f for f in _stored(p) if (f.get("kind") or "feature") == "feature"]


def problem_text(problem: mutagenesis.Problem) -> str:
    d = problem.details
    return {
        "outside": gettext("Pick bases on the sequence."),
        "nothing": gettext("Say what the bases become, or leave it empty to delete them."),
        "same": gettext("That is what the plasmid has already."),
        "linear": gettext("Site-directed mutagenesis copies a whole circular plasmid, and this one is linear."),
        "too_much": gettext("That would leave too little of the plasmid to copy."),
        "too_long": gettext("The change is too long for two primers of up to %(most)s bases. Assemble the plasmid with a synthesized fragment instead.", most=d.get("most", "")),
        "notation": gettext("Write the change as K79A: the amino acid, its number and the new one."),
        "codon_outside": gettext("This protein has %(n)s codons.", n=d.get("n", "")),
        "not_that": gettext("Codon %(number)s is %(aa)s.", number=d.get("number", ""), aa=d.get("aa", "")),
        "across_origin": gettext("That codon runs across the origin. Pick its bases on the sequence instead."),
    }.get(problem.code, gettext("Pick bases on the sequence."))


def _int(name: str, default: int = 0) -> int:
    try:
        return int(request.values.get(name, default))
    except (TypeError, ValueError):
        abort(400)


def _feature(p: PlasmidRecord) -> dict:
    features = _features(p)
    i = _int("feature", -1)
    if not 0 <= i < len(features) or not mutagenesis.coding(features[i]):
        abort(400)
    return features[i]


def _change(p: PlasmidRecord) -> tuple[int, int, str, dict | None]:
    """start, end and bases from the request: as given, or for ?protein=."""
    if request.values.get("protein"):
        codon = mutagenesis.codon_change((p.full_sequence or "").upper(), _feature(p), request.values["protein"])
        return codon["start"], codon["end"], codon["bases"], codon
    return _int("start"), _int("end"), request.values.get("bases", ""), None


def _label(result: dict) -> str:
    """The change's short name: ?label= when the page has one (a silent
    change says the site it makes, +KpnI), else from the change itself."""
    given = re.sub(r"[^\w+*.-]+", "", request.values.get("label", ""))[:40]
    return given or mutagenesis.label_of(result)


@bp.route("/plasmids/<int:row_id>/mutagenesis.json")
def design(row_id: int):
    with SessionLocal() as session:
        p = _plasmid(session, row_id)
        try:
            start, end, bases, codon = _change(p)
            result = mutagenesis.design(p.full_sequence or "", bool(p.is_circular), _features(p), start, end, bases)
        except mutagenesis.Problem as problem:
            return jsonify({"ok": False, "error": problem_text(problem)}), 400
        label = _label(result)
        return jsonify({"ok": True, **result, "codon": codon, "label": label,
                        "name": f"{p.name or p.plasmid_id}-{label}"[:200]})


@bp.route("/plasmids/<int:row_id>/silent-sites.json")
def silent_sites(row_id: int):
    with SessionLocal() as session:
        p = _plasmid(session, row_id)
        f = _feature(p)
        near = (_int("start"), _int("end")) if request.args.get("start") else None
        found = mutagenesis.silent_sites((p.full_sequence or "").upper(), f, near=near)
        return jsonify({"ok": True, "feature": f.get("name", ""), **found})


def _mark(result: dict, label: str) -> dict:
    """An annotation where the change is, so the new map shows it."""
    start = result["start"] - 1
    if result["new"]:
        end = start + len(result["new"]) - 1
    else:                                   # a deletion: the two bases now side by side
        start, end = max(start - 1, 0), start
    return {"name": label, "type": "misc_difference", "start": start, "end": end, "direction": 1,
            "notes": {"note": [f"site-directed mutagenesis: {label}"]}}


def _make(session, p: PlasmidRecord, form, user: str) -> tuple[PlasmidRecord, int]:
    start, end, bases, _codon = _change(p)
    result = mutagenesis.design(p.full_sequence or "", bool(p.is_circular), _features(p), start, end, bases)
    label = _label(result)
    name = (form.get("name") or "").strip()[:200] or f"{p.name or p.plasmid_id}-{label}"[:200]
    seq = (p.full_sequence or "").upper()
    change = mutagenesis.change_from(seq, start, end, bases)
    product = PlasmidRecord(
        plasmid_id=(session.scalar(select(func.max(PlasmidRecord.plasmid_id))) or 0) + 1, name=name, owner=user,
        backbone=p.backbone or "", insert_seq=p.insert_seq or "", resistance=p.resistance or "",
        notes=(form.get("notes") or "").strip(), full_sequence=mutagenesis.apply(seq, change), is_circular=True,
        sequence_uploaded_at=datetime.utcnow(),
        features_json=json.dumps(mutagenesis.shift_features(_stored(p), len(seq), change) + [_mark(result, label)]),
    )
    with audit.batch(session, "create", f"Mutagenesis of plasmid {p.name}: {name}", "plasmids") as batch_row:
        batch_row.record_count = 1
        session.add(product)
        session.flush()
        primer_ids = []
        module = primer_records.primers_module(session, user)
        if module is not None:
            for direction, primer in (("forward", result["forward"]), ("reverse", result["reverse"])):
                item, _made = primer_records.save_primer(
                    session, module, p, name=f"{name} {'F' if direction == 'forward' else 'R'}", sequence=primer["sequence"],
                    direction=direction, user=user, category="mutagenesis",
                    notes=gettext("Designed for the mutagenesis of %(template)s into %(name)s.", template=p.name or "", name=name))
                primer_ids.append(item.id)
        product.updated_at, product.updated_by = datetime.utcnow(), user
        plasmid_versions.record(session, product, "mutagenesis", user, detail=label)
        plasmid_lineage.add_parent(session, product, parent=p, role="template", method="mutagenesis", user=user,
                                   details={"change": label, "start": result["start"], "end": result["end"],
                                            "bases": result["new"], "primers": primer_ids})
    return product, len(primer_ids)


@bp.route("/plasmids/<int:row_id>/mutagenesis", methods=["POST"])
def make(row_id: int):
    for _attempt in range(3):
        with SessionLocal() as session:
            p = _plasmid(session, row_id)
            try:
                product, primers = _make(session, p, request.form, g.user.username)
                session.commit()
            except mutagenesis.Problem as problem:
                return jsonify({"ok": False, "error": problem_text(problem)}), 400
            except IntegrityError:
                session.rollback()          # someone took the next number first
                continue
            message = gettext("Plasmid #%(number)s %(name)s made.", number=product.plasmid_id, name=product.name)
            if primers:
                message += " " + gettext("Its two primers are in Primers, to order.")
            return jsonify({"ok": True, "message": message, "number": product.plasmid_id,
                            "url": url_for("plasmid_page", number=product.plasmid_id)})
    return jsonify({"ok": False, "error": gettext("Couldn’t allocate a plasmid number. Try again.")}), 409
