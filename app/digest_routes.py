"""The plasmid page's Digest tab: app/digest.py's numbers, in words.

`GET /plasmids/<id>/digest.json` answers for the enzymes ticked, the gel's
agarose and ladder, whether the DNA is methylated (grown in an ordinary
E. coli), and the lanes kept on the gel: each a set of enzymes, or a PCR
product's size from the Primers tab (`pcr:<bp>:<label>`). The page's script
(static/plasmid-digest.js) only draws what comes back.

Enzymes "in the freezer" are those named in the lab's Reagents databases
this person can see, with a status that still has some (EcoRI-HF counts as
EcoRI). The backbone to tell a clone from is the plasmid's **Made from**
parent with the backbone role, else its first parent with a sequence.
"""
from __future__ import annotations

import json
import re

from flask import Blueprint, abort, g, jsonify, request, url_for

from . import digest, plasmid_lineage
from .db import SessionLocal
from .i18n import gettext, ngettext
from .models import InventoryItem, PlasmidRecord

bp = Blueprint("digest", __name__)

MAX_LANES = 8
REAGENT_KINDS = ("reagents",)


def lab_enzymes(session, names) -> dict[str, dict]:
    """The enzymes among `names` the lab has: {enzyme: {item, where, url}}."""
    from sqlalchemy import select

    from . import inventory_service as inventories
    from . import lab

    found: dict[str, dict] = {}
    for module in inventories.list_modules(session):
        if module.kind not in REAGENT_KINDS or not lab.can_see(module):
            continue
        mv = inventories.view(module)
        items = [i for i in session.scalars(select(InventoryItem).where(InventoryItem.module_id_fk == module.id))
                 if inventories.is_available(mv, i.status) is not False]
        by_name = {i.name: i for i in items}
        for enzyme, item_name in digest.in_freezer([n for n in names if n not in found], list(by_name)).items():
            item = by_name[item_name]
            found[enzyme] = {"item": item.name, "where": inventories.where_label(item) or item.location_note or "",
                             "url": url_for("inventory.module", key=module.key, open=item.id)}
    return found


def _backbone(session, p: PlasmidRecord):
    """The parent to tell a clone from: (name, sequence, circular) or None."""
    best = None
    for entry in plasmid_lineage.parents(session, p):
        parent = entry["plasmid"]
        if parent is None or not parent.full_sequence:
            continue
        if entry["role"] == "backbone":
            return parent
        best = best or parent
    return best


def _bp(sizes) -> str:
    return " + ".join(f"{s:,}" for s in sizes)


def _enzymes_text(names) -> str:
    return " + ".join(names)


def _suggestion_text(s: dict, backbone_name: str, agarose: str) -> str:
    enzymes, sizes = _enzymes_text(s["enzymes"]), _bp(s["sizes"])
    if s.get("differs") and s.get("parent_sizes"):
        return gettext("%(enzymes)s gives %(sizes)s bp. %(backbone)s gives %(theirs)s bp, so one lane tells a right clone from the backbone.",
                       enzymes=enzymes, sizes=sizes, backbone=backbone_name, theirs=_bp(s["parent_sizes"]))
    if s.get("drops"):
        return gettext("%(enzymes)s drops %(feature)s out: %(sizes)s bp, well apart on the gel.",
                       enzymes=enzymes, feature=", ".join(s["drops"]), sizes=sizes)
    return gettext("%(enzymes)s gives %(sizes)s bp, well apart on a %(agarose)s%% gel.",
                   enzymes=enzymes, sizes=sizes, agarose=agarose)


def _lane(seq: str, circular: bool, spec: str, agarose: str, methylated: bool, features) -> dict | None:
    """One kept lane: enzymes joined by +, or pcr:<bp>:<label>."""
    spec = (spec or "").strip()
    if spec.startswith("pcr:"):
        parts = spec.split(":", 2)
        try:
            size = int(parts[1])
        except (IndexError, ValueError):
            return None
        if not 1 <= size <= 100000:
            return None
        label = (parts[2] if len(parts) > 2 else "").strip()[:40] or gettext("PCR")
        return {"key": spec, "label": label, "kind": "pcr", "bands": digest.bands([size], agarose), "sizes": [size]}
    names = [n for n in re.split(r"[+,\s]+", spec) if n in digest.ENZYMES]
    if not names:
        return None
    cuts = digest.cut_positions(seq, circular, names, methylated)
    made = digest.pieces(len(seq), circular, cuts, features)
    sizes = [m.length for m in made]
    return {"key": "+".join(names), "label": _enzymes_text(names), "kind": "digest",
            "bands": digest.bands(sizes, agarose) if sizes else digest.uncut(len(seq), circular, agarose),
            "sizes": sorted(sizes, reverse=True)}


@bp.before_request
def require_login():
    if g.get("user") is None:
        abort(401)
    return None


@bp.route("/plasmids/<int:row_id>/digest.json")
def digest_json(row_id: int):
    agarose = request.args.get("agarose") or digest.DEFAULT_AGAROSE
    if agarose not in digest.AGAROSE:
        agarose = digest.DEFAULT_AGAROSE
    ladder_key = request.args.get("ladder") or digest.DEFAULT_LADDER
    methylated = request.args.get("methylated", "1") != "0"
    chosen = [n for n in (request.args.get("enzymes") or "").split(",") if n in digest.ENZYMES][:4]
    with SessionLocal() as session:
        p = session.get(PlasmidRecord, row_id)
        if p is None:
            abort(404)
        seq = p.full_sequence or ""
        circular = bool(p.is_circular)
        if not seq:
            return jsonify({"ok": False, "error": gettext("This plasmid has no sequence yet, so there is nothing to cut.")}), 400
        try:
            stored = json.loads(p.features_json) if p.features_json else []
        except json.JSONDecodeError:
            stored = []
        features = [f for f in stored if isinstance(f, dict) and (f.get("kind") or "feature") == "feature"]
        table = digest.enzyme_table(seq, circular, methylated)
        freezer = lab_enzymes(session, [r["name"] for r in table])
        backbone = _backbone(session, p)
        suggestions = digest.suggest(seq, circular, features, freezer=list(freezer),
                                     parent=backbone.full_sequence if backbone else None,
                                     parent_circular=bool(backbone.is_circular) if backbone else True,
                                     methylated=methylated, agarose=agarose)
        backbone_name = (backbone.name or f"#{backbone.plasmid_id}") if backbone else ""
        lanes_spec = request.args.getlist("lane")[:MAX_LANES]

    cuts = digest.cut_positions(seq, circular, chosen, methylated)
    made = digest.pieces(len(seq), circular, cuts, features)
    sizes = [m.length for m in made]

    notes = []
    for row in table:
        for site in row["sites"]:
            if site["blocked"] and (row["name"] in chosen or not chosen):
                word = gettext("Dam (GATC)") if site["blocked"] == "dam" else gettext("Dcm (CCWGG)")
                notes.append({"tone": "warn", "label": row["name"], "text": gettext(
                    "Its site at %(at)s overlaps %(word)s methylation, so it won't cut DNA grown in an ordinary E. coli such as DH5α. Drawn uncut; grow the plasmid in a dam⁻ dcm⁻ strain to cut it.",
                    at=f"{site['cut']:,}", word=word)})
    for group in digest.too_close(sizes, agarose):
        notes.append({"tone": "warn", "label": gettext("One band"), "text": gettext(
            "%(sizes)s bp run as one band on a %(agarose)s%% gel: try another agarose or other enzymes.",
            sizes=" / ".join(f"{s:,}" for s in group),
            agarose=agarose)})
    for size in digest.off_the_gel(sizes, agarose):
        notes.append({"tone": "warn", "label": gettext("Off the scale"), "text": gettext(
            "A %(size)s bp piece is outside what a %(agarose)s%% gel separates well.", size=f"{size:,}", agarose=agarose)})

    lanes = [{"key": "uncut", "label": gettext("Uncut"), "kind": "uncut",
              "bands": digest.uncut(len(seq), circular, agarose), "sizes": [len(seq)]}]
    if chosen:
        lanes.append({"key": "+".join(chosen), "label": _enzymes_text(chosen), "kind": "digest", "current": True,
                      "bands": digest.bands(sizes, agarose) if sizes else digest.uncut(len(seq), circular, agarose),
                      "sizes": sorted(sizes, reverse=True)})
    for spec in lanes_spec:
        lane = _lane(seq, circular, spec, agarose, methylated, features)
        if lane and lane["key"] not in {x["key"] for x in lanes}:
            lane["kept"] = True
            lanes.append(lane)

    enzymes = []
    for row in table:
        have = freezer.get(row["name"])
        enzymes.append({**row, "freezer": have})
    return jsonify({
        "ok": True,
        "length": len(seq), "circular": circular,
        "features": [{"name": f.get("name", ""), "type": f.get("type", ""), "start": f.get("start", 0),
                      "end": f.get("end", 0), "direction": f.get("direction", f.get("strand", 1)),
                      "color": f.get("color", "")} for f in features],
        "enzymes": enzymes,
        "chosen": chosen,
        "cuts": [{"at": at, "enzyme": e} for at, e in cuts],
        "pieces": [{"start": m.start, "end": m.end, "length": m.length, "left": m.left, "right": m.right,
                    "holds": m.holds} for m in made],
        "notes": notes,
        "suggestions": [{**s, "text": _suggestion_text(s, backbone_name, agarose),
                         "freezer": all(e in freezer for e in s["enzymes"])} for s in suggestions],
        "backbone": backbone_name,
        "gel": {"agarose": agarose, "ladder": digest.ladder(ladder_key, agarose), "lanes": lanes},
        "agaroses": list(digest.AGAROSE),
        "ladders": [{"key": k, "name": v[0]} for k, v in digest.LADDERS.items()],
        "methylated": methylated,
        "summary": ngettext("%(num)s piece", "%(num)s pieces", len(made)) if made else "",
    })
