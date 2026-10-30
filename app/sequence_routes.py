"""What the plasmid page's own sequence viewer and Primers tab ask for.

- `view.json`: the sequence with everything drawn along it: features, the
  enzymes that cut once, the lab's primers that bind (exactly, or with up
  to two mismatches away from the 3′ end), the proteins of its coding
  features (app/protein.py) and its long open reading frames.
- `primer-check`: a stretch picked on the sequence as a primer, checked
  (app/primer_design.py), with the lab's primers that are the same or
  close by, and a name for it.
- `primers/new`: save it to the lab's Primers database, linked to the
  plasmid, as the map's own primers are (app/primer_records.py).
- `pcr.json`: what two primers would amplify, what it holds, and what the
  backbone would give instead (for a colony PCR).
- `edit.json`: one edit made on the Sequence tab (app/sequence_edit.py):
  bases typed over, inserted or deleted, a feature added, changed or
  removed, the topology, or a whole earlier state (Undo). Each is saved as
  the plasmid's sequence, joined into one version per editing session, and
  refused when someone else changed the plasmid since the page loaded it.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from flask import Blueprint, abort, g, jsonify, request, url_for

from . import access, digest, plasmid_versions, primer_design, primer_records, protein, sequence_edit
from .db import SessionLocal
from .i18n import gettext, ngettext
from .models import InventoryItem, PlasmidRecord

bp = Blueprint("sequence_view", __name__)

NEAR = 60          # bp: a lab primer this close to a new one's site is worth naming


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


def _stored(p: PlasmidRecord) -> list:
    try:
        stored = json.loads(p.features_json) if p.features_json else []
    except json.JSONDecodeError:
        stored = []
    return stored if isinstance(stored, list) else []


def _features(p: PlasmidRecord) -> list[dict]:
    return [f for f in _stored(p) if isinstance(f, dict) and (f.get("kind") or "feature") == "feature"]


def _feature_places(p: PlasmidRecord) -> list[int]:
    """Where each of `_features(p)` sits in the stored list, in step with it."""
    return [i for i, f in enumerate(_stored(p)) if isinstance(f, dict) and (f.get("kind") or "feature") == "feature"]


def lab_primers(session, p: PlasmidRecord, mismatches: int = 2) -> list[dict]:
    """Every primer in the Primers databases this person can see that binds
    the plasmid, each site once, along the plasmid."""
    from . import inventory_service as inventories

    template = (p.full_sequence or "").upper()
    if not template:
        return []
    circular = bool(p.is_circular)
    out = []
    for module, item in primer_records.saved_primers(session):
        attrs = item.attrs_dict
        sequence = primer_design.clean(attrs.get("sequence", ""))
        if len(sequence) < primer_design.MIN_LENGTH:
            continue
        for site in primer_design.binding(template, circular, sequence, mismatches=mismatches):
            out.append({
                "id": item.id, "number": item.number, "name": item.name or f"#{item.number}",
                "sequence": sequence, "length": len(sequence), "status": item.status or "",
                "tm": attrs.get("tm") or "", "where": inventories.where_label(item) or item.location_note or "",
                "url": url_for("inventory.module", key=module.key, open=item.id),
                "start": site["start"] + 1, "end": site["end"] + 1, "direction": site["direction"],
                "mismatches": site["mismatches"], "tail": site["tail"],
            })
    out.sort(key=lambda h: (h["start"], h["name"].lower()))
    return out


@bp.route("/plasmids/<int:row_id>/view.json")
def view(row_id: int):
    with SessionLocal() as session:
        p = _plasmid(session, row_id)
        seq = (p.full_sequence or "").upper()
        circular = bool(p.is_circular)
        features = _features(p)
        places = _feature_places(p)
        state = sequence_edit.state_hash(seq, circular, _stored(p))
        editable = access.can_edit(p)
        primers = lab_primers(session, p)
        from .clone_routes import sequence_next
        next_reads = sequence_next(session, p) if seq else None
    if not seq:
        return jsonify({"ok": False, "error": gettext("This plasmid has no sequence yet.")}), 400
    cutters = [{"name": r["name"], "cut": r["sites"][0]["cut"]} for r in digest.enzyme_table(seq, circular)
               if r["cuts"] == 1]
    proteins = []
    for i, f in enumerate(features):
        if str(f.get("type", "")).lower() not in ("cds", "gene"):
            continue
        try:
            start, end, direction = int(f.get("start", 0)), int(f.get("end", 0)), int(f.get("direction", f.get("strand", 1)) or 1)
        except (TypeError, ValueError):
            continue
        bases = seq[start:end + 1] if end >= start else seq[start:] + seq[:end + 1]
        if direction < 0:
            bases = primer_records.reverse_complement(bases)
        proteins.append({"feature": i, "name": f.get("name", ""), "start": start + 1, "end": end + 1,
                         "direction": direction, **protein.about(bases)})
    return jsonify({
        "ok": True, "length": len(seq), "circular": circular, "sequence": seq,
        "features": [{"name": f.get("name", ""), "type": f.get("type", ""), "start": f.get("start", 0),
                      "end": f.get("end", 0), "direction": f.get("direction", f.get("strand", 1)), "at": at}
                     for f, at in zip(features, places)],
        "hash": state, "editable": editable,
        "cutters": cutters, "primers": primers, "proteins": proteins,
        "orfs": protein.orfs(seq, circular)[:20], "next": next_reads,
    })


def _suggest_name(p: PlasmidRecord, features: list[dict], start: int, direction: int) -> str:
    """"EGFP-mid-F": the feature it sits in (or its plasmid), where in it, which way."""
    inside = None
    for f in features:
        try:
            fs, fe = int(f.get("start", 0)), int(f.get("end", 0))
        except (TypeError, ValueError):
            continue
        if fs + 1 <= start <= fe + 1 and f.get("name"):
            inside = (f, fs, fe)
            break
    end = "F" if direction > 0 else "R"
    if inside is None:
        base = re.sub(r"[^\w.-]+", "", (p.name or f"p{p.plasmid_id}"))[:24] or f"p{p.plasmid_id}"
        return f"{base}-{start}-{end}"
    f, fs, fe = inside
    third = (start - fs) / max(fe - fs, 1)
    part = "N" if third < 0.33 else "mid" if third < 0.67 else "C"
    label = re.sub(r"[^\w.-]+", "", f["name"])[:20]
    return f"{label}-{part}-{end}"


@bp.route("/plasmids/<int:row_id>/primer-check")
def primer_check(row_id: int):
    """?start=&end= (1-based, along the top strand) and ?direction=1|-1, or
    ?sequence= for a primer typed in."""
    with SessionLocal() as session:
        p = _plasmid(session, row_id)
        seq = (p.full_sequence or "").upper()
        circular = bool(p.is_circular)
        direction = -1 if request.args.get("direction") == "-1" else 1
        typed = primer_design.clean(request.args.get("sequence", ""))
        if typed:
            primer = typed
            sites = primer_design.binding(seq, circular, primer)
            start = sites[0]["start"] + 1 if sites else 0
        else:
            try:
                start, end = int(request.args.get("start", 0)), int(request.args.get("end", 0))
            except ValueError:
                abort(400)
            if not (1 <= start <= len(seq) and 1 <= end <= len(seq)):
                return jsonify({"ok": False, "error": gettext("Pick a stretch of the sequence.")}), 400
            stretch = seq[start - 1:end] if start <= end else seq[start - 1:] + seq[:end]
            primer = stretch if direction > 0 else primer_records.reverse_complement(stretch)
        if not primer_design.MIN_LENGTH <= len(primer) <= primer_design.MAX_LENGTH:
            return jsonify({"ok": False, "error": gettext("A primer is %(least)s to %(most)s bases.",
                                                          least=primer_design.MIN_LENGTH, most=primer_design.MAX_LENGTH)}), 400
        checked = primer_design.check(primer, seq, circular)
        # The lab's primers: the same sequence, or near this site on the same strand.
        same, near = [], []
        for hit in lab_primers(session, p, mismatches=0):
            if hit["sequence"] == primer:
                same.append(hit)
            elif hit["direction"] == direction and abs(hit["start"] - start) <= NEAR:
                near.append(hit)
        return jsonify({"ok": True, **checked, "direction": direction, "start": start,
                        "name": _suggest_name(p, _features(p), start, direction),
                        "same": same[:3], "near": sorted(near, key=lambda h: abs(h["start"] - start))[:3]})


@bp.route("/plasmids/<int:row_id>/primers/new", methods=["POST"])
def primer_new(row_id: int):
    with SessionLocal() as session:
        p = _plasmid(session, row_id)
        sequence = primer_design.clean(request.form.get("sequence", ""))
        name = (request.form.get("name") or "").strip()[:200]
        direction = "reverse" if request.form.get("direction") in ("-1", "reverse") else "forward"
        if not primer_design.MIN_LENGTH <= len(sequence) <= primer_design.MAX_LENGTH:
            return jsonify({"ok": False, "error": gettext("A primer is %(least)s to %(most)s bases.",
                                                          least=primer_design.MIN_LENGTH, most=primer_design.MAX_LENGTH)}), 400
        module = primer_records.primers_module(session, user=g.user.username)
        if module is None:
            return jsonify({"ok": False, "error": gettext("The lab has no Primers database yet, and you can't add one. Ask whoever runs Lab setup.")}), 403
        from . import inventory_service as inventories
        item, made = primer_records.save_primer(session, module, p, name=name or _suggest_name(p, _features(p), 1, 1),
                                                sequence=sequence, direction=direction, user=g.user.username,
                                                category="PCR")
        inventories.derive_primer(session, item)
        session.commit()
        message = (gettext("%(name)s saved to %(database)s, to order.", name=item.name, database=module.label) if made
                   else gettext("%(name)s was already in %(database)s; brought up to date.", name=item.name, database=module.label))
        return jsonify({"ok": True, "made": made, "message": message, "id": item.id,
                        "url": url_for("inventory.module", key=module.key, open=item.id)})


def _primer_seq(session, raw: str) -> str:
    """A primer by its record's id (a number) or as bases."""
    raw = (raw or "").strip()
    if raw.isdigit():
        item = session.get(InventoryItem, int(raw))
        if item is None or not any(item.id == i.id for _m, i in primer_records.saved_primers(session)):
            return ""
        return primer_design.clean(item.attrs_dict.get("sequence", ""))
    return primer_design.clean(raw)


@bp.route("/plasmids/<int:row_id>/pcr.json")
def pcr(row_id: int):
    from .digest_routes import _backbone

    with SessionLocal() as session:
        p = _plasmid(session, row_id)
        forward, reverse = _primer_seq(session, request.args.get("f", "")), _primer_seq(session, request.args.get("r", ""))
        if not forward or not reverse:
            return jsonify({"ok": False, "error": gettext("Pick a forward and a reverse primer.")}), 400
        seq, circular = (p.full_sequence or "").upper(), bool(p.is_circular)
        product = primer_design.pcr(seq, circular, forward, reverse)
        if product is None:
            return jsonify({"ok": False, "error": gettext("These two primers don't face each other on this plasmid, so they make nothing.")}), 400
        holds = []
        for f in _features(p):
            try:
                fs, fe = int(f.get("start", 0)), int(f.get("end", 0))
            except (TypeError, ValueError):
                continue
            a = product["start"] - 1
            if (fs - a) % len(seq) + (fe - fs) % len(seq) < product["template_length"] and f.get("name"):
                holds.append(f["name"])
        backbone = _backbone(session, p)
        empty = None
        if backbone is not None:
            other = primer_design.pcr(backbone.full_sequence, bool(backbone.is_circular), forward, reverse)
            empty = {"name": backbone.name or f"#{backbone.plasmid_id}", "length": other["length"] if other else None}
        tms = [t for t in (primer_design.primer_tm(forward[-25:]), primer_design.primer_tm(reverse[-25:])) if t]
        return jsonify({"ok": True, **product, "holds": holds, "empty": empty,
                        "anneal": round(min(tms) - 3) if tms else None,
                        "extend_s": max(15, round(product["length"] / 1000 * 60 / 5) * 5)})


# ---------------------------------------------------------------- editing

def _edit_problem(code: str) -> str:
    return {
        "outside": gettext("Pick bases on the sequence."),
        "nothing": gettext("Type the bases to put in."),
        "same": gettext("Those are the bases already there."),
        "empty": gettext("That would leave no sequence. Use Clear sequence to empty it."),
        "not_bases": gettext("Only nucleotide letters (A, C, G, T and the IUPAC codes) can go in."),
        "no_name": gettext("Give the feature a name."),
        "no_feature": gettext("That feature is no longer there. Reload the page."),
    }.get(code, gettext("That edit couldn’t be made."))


def _int(body: dict, key: str) -> int:
    try:
        return int(body.get(key))
    except (TypeError, ValueError):
        raise sequence_edit.EditError("outside") from None


@bp.route("/plasmids/<int:row_id>/edit.json", methods=["POST"])
def edit(row_id: int):
    body = request.get_json(silent=True) or {}
    op = body.get("op")
    with SessionLocal() as session:
        p = _plasmid(session, row_id)
        if not access.can_edit(p):
            return jsonify({"ok": False, "error": gettext("This plasmid isn’t yours to edit.")}), 403
        seq, circular, stored = (p.full_sequence or "").upper(), bool(p.is_circular), _stored(p)
        if body.get("base") != sequence_edit.state_hash(seq, circular, stored):
            return jsonify({"ok": False, "stale": True,
                            "error": gettext("Someone changed this plasmid since the page loaded it. Reload to see their change.")}), 409
        before = {"sequence": seq, "circular": circular, "features": stored}
        new_seq, new_circular, new_features, message, dropped = seq, circular, stored, "", 0
        try:
            if op == "bases":
                new_seq, new_features, done = sequence_edit.bases(seq, stored, _int(body, "start"), _int(body, "end"),
                                                                  body.get("bases") or "")
                dropped = done["dropped"]
                message = (ngettext("%(num)s base deleted.", "%(num)s bases deleted.", done["removed"])
                           if done["kind"] == "deletion" else
                           ngettext("%(num)s base inserted.", "%(num)s bases inserted.", done["added"])
                           if done["kind"] == "insertion" else
                           gettext("%(was)s bp became %(now)s bp.", was=done["removed"], now=done["added"]))
            elif op == "add_feature":
                new_features = sequence_edit.add_feature(stored, len(seq), _int(body, "start"), _int(body, "end"),
                                                         body.get("name") or "", body.get("type") or "",
                                                         body.get("direction", 1))
                message = gettext("Feature added.")
            elif op == "edit_feature":
                new_features = sequence_edit.edit_feature(stored, body.get("at"), body.get("name"), body.get("type"),
                                                          body.get("direction"))
                message = gettext("Feature changed.")
            elif op == "delete_feature":
                new_features = sequence_edit.delete_feature(stored, body.get("at"))
                message = gettext("Feature removed.")
            elif op == "topology":
                new_circular = bool(body.get("circular"))
                message = gettext("Now circular.") if new_circular else gettext("Now linear.")
            elif op == "state":
                from .app import _clean_features
                from .sequence_parser import clean_bases
                state = body.get("state") or {}
                new_seq = clean_bases(state.get("sequence") or "")
                if not new_seq:
                    raise sequence_edit.EditError("empty")
                new_circular = bool(state.get("circular"))
                new_features = _clean_features(state.get("features") or [], len(new_seq))
                message = gettext("Undone.")
            else:
                abort(400)
        except sequence_edit.EditError as problem:
            return jsonify({"ok": False, "error": _edit_problem(problem.code)}), 400
        if (new_seq, new_circular, new_features) == (seq, circular, stored):
            return jsonify({"ok": True, "hash": sequence_edit.state_hash(seq, circular, stored), "length": len(seq),
                            "message": gettext("Nothing changed."), "changed": False})
        plasmid_versions.before_change(session, p)
        p.full_sequence, p.is_circular, p.features_json = new_seq, new_circular, json.dumps(new_features)
        if not p.sequence_format:
            p.sequence_format = "manual"
        now = datetime.utcnow()
        p.sequence_uploaded_at, p.updated_at, p.updated_by = now, now, g.user.username
        plasmid_versions.record(session, p, "editor", g.user.username)
        session.commit()
        if dropped:
            message += " " + ngettext("%(num)s annotation inside the changed bases was removed.",
                                      "%(num)s annotations inside the changed bases were removed.", dropped)
        return jsonify({"ok": True, "changed": True, "message": message, "length": len(new_seq),
                        "hash": sequence_edit.state_hash(new_seq, new_circular, new_features), "before": before})
