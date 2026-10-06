"""The assembly wizard: the pages and writes behind app/cloning.py.

A tray of fragments taken from the lab's own plasmids — a whole plasmid, one
of its features, a stretch of it, or a piece a digest leaves — in the order
they will go together. Every junction between them is worked out on the
server (app/cloning.py has all the biology; the page's script only draws
it), so the status line can name the end that is wrong rather than say that
something did not assemble.

**Create** makes an ordinary plasmid: its sequence, the features each
fragment brought with it at their new places, a part annotation per
fragment so the map shows where the joins are, a version (`how="assembly"`)
and a **Made from** link per fragment through app/plasmid_lineage.py, whose
details_json keeps what the assembly used — the enzymes, the coordinates
and the primers. A Gibson's designed primers are saved in the lab's Primers
database against the plasmid each one amplifies, and drawn on that
plasmid's map, so Copy for ordering picks them up with the rest.
"""
from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from . import access, audit, cloning, feature_library, plasmid_lineage, plasmid_versions, primer_records
from .db import SessionLocal
from .i18n import gettext, ngettext
from .models import PlasmidRecord

bp = Blueprint("cloning", __name__, url_prefix="/plasmids/assembly")

MAX_FRAGMENTS = 20
PART_COLOURS = ("#bfdbfe", "#bbf7d0", "#fde68a", "#fbcfe8", "#ddd6fe", "#fed7aa", "#a5f3fc", "#e9d5ff")

ROLES = ("backbone", "insert", "template", "donor", "other")


def method_label(method: str) -> str:
    return {"digest_ligate": gettext("Digest and ligate"), "gibson": gettext("Gibson / HiFi assembly"),
            "golden_gate": gettext("Golden Gate")}.get(method, "")


def role_labels() -> dict[str, str]:
    """What a fragment is to the product, in the words Made from uses."""
    return {"backbone": gettext("backbone"), "insert": gettext("insert"), "template": gettext("template"),
            "donor": gettext("donor"), "other": gettext("other")}


def method_blurb(method: str) -> str:
    return {
        "digest_ligate": gettext("Cut each plasmid with the same enzymes and let the sticky ends find each other."),
        "gibson": gettext("Give neighbouring fragments the same bases where they meet, by PCR, and one enzyme mix joins them."),
        "golden_gate": gettext("A Type IIS enzyme cuts outside its own site, so every fragment gets the overhang you choose."),
    }.get(method, "")


# ---------------------------------------------------------------------------
# Reading the tray
# ---------------------------------------------------------------------------


def _features_of(p: PlasmidRecord) -> list[dict]:
    try:
        found = json.loads(p.features_json or "[]")
    except json.JSONDecodeError:
        return []
    return [f for f in found if isinstance(f, dict)]


def _named_features(p: PlasmidRecord) -> list[dict]:
    """The features a fragment can be taken from, in map order."""
    length = len(p.full_sequence or "")
    out = []
    for i, f in enumerate(_features_of(p)):
        if (f.get("kind") or "feature") != "feature" or not (f.get("name") or "").strip():
            continue
        try:
            start, end = int(f["start"]), int(f["end"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"index": i, "name": f["name"], "type": f.get("type") or "misc_feature",
                    "start": start, "end": end,
                    "length": cloning.span_length(start, end, length, bool(p.is_circular)),
                    "direction": 1 if int(f.get("direction", 1) or 1) >= 0 else -1})
    return sorted(out, key=lambda f: (f["start"], f["end"]))


def _plasmid_label(p: PlasmidRecord) -> str:
    return f"#{p.plasmid_id}" + (f" {p.name}" if p.name else "")


def _source_plasmid(db_session, number) -> PlasmidRecord | None:
    try:
        number = int(number)
    except (TypeError, ValueError):
        return None
    return db_session.scalar(select(PlasmidRecord).where(PlasmidRecord.plasmid_id == number))


def pieces_of(p: PlasmidRecord, enzyme_names: list[str]) -> list[cloning.Fragment]:
    return cloning.digest(p.full_sequence or "", bool(p.is_circular), enzyme_names, _features_of(p),
                          name=_plasmid_label(p))


def fragment_from(db_session, entry: dict) -> tuple[cloning.Fragment | None, str]:
    """One tray entry as a fragment, or the reason it could not be read."""
    p = _source_plasmid(db_session, entry.get("plasmid"))
    if p is None:
        return None, gettext("That plasmid is not in the lab any more.")
    sequence = p.full_sequence or ""
    if not sequence:
        return None, gettext("Plasmid %(label)s has no sequence yet.", label=_plasmid_label(p))
    circular, length = bool(p.is_circular), len(sequence)
    kind = entry.get("kind") or "whole"
    where = {"plasmid": p.plasmid_id, "row": p.id, "kind": kind}
    made = None
    if kind == "digest":
        enzymes = [e for e in (entry.get("enzymes") or []) if e in cloning.ENZYMES]
        if not enzymes:
            return None, gettext("Choose an enzyme to cut %(label)s with.", label=_plasmid_label(p))
        pieces = pieces_of(p, enzymes)
        if not pieces:
            return None, gettext("%(enzymes)s does not cut %(label)s.", enzymes=" + ".join(enzymes),
                                 label=_plasmid_label(p))
        try:
            made = pieces[int(entry.get("piece", 0))]
        except (IndexError, TypeError, ValueError):
            return None, gettext("That piece of the %(label)s digest is no longer there.", label=_plasmid_label(p))
        where["enzymes"] = made.source.get("enzymes") or enzymes
        where["start"], where["length"] = made.source["start"], made.source["length"]
        last = (where["start"] + where["length"] - 1) % len(sequence) + 1
        made.name = f"{_plasmid_label(p)} {where['start'] + 1}–{last}"
    elif kind == "feature":
        found = next((f for f in _named_features(p) if f["index"] == entry.get("feature")), None)
        if found is None:
            return None, gettext("That feature is no longer on %(label)s's map.", label=_plasmid_label(p))
        made = cloning.region(sequence, circular, found["start"], found["length"], _features_of(p),
                              name=f"{_plasmid_label(p)} {found['name']}")
        where["feature"], where["start"], where["length"] = found["name"], found["start"], found["length"]
    elif kind == "region":
        start, end = _coordinate(entry.get("start"), length), _coordinate(entry.get("end"), length)
        if start is None or end is None:
            return None, gettext("A region of %(label)s needs a first and a last base, between 1 and %(bp)s.",
                                 label=_plasmid_label(p), bp=length)
        size = cloning.span_length(start, end, length, circular)
        if size <= 0:
            return None, gettext("%(first)s–%(last)s runs backwards on a plasmid that is not circular.",
                                 first=start + 1, last=end + 1)
        made = cloning.region(sequence, circular, start, size, _features_of(p),
                              name=f"{_plasmid_label(p)} {start + 1}–{end + 1}")
        where["start"], where["length"] = start, size
    else:
        made = cloning.region(sequence, circular, 0, length, _features_of(p), name=_plasmid_label(p))
        where["start"], where["length"] = 0, length
    made.source = {**made.source, **where}
    if entry.get("label"):
        made.name = str(entry["label"])[:120]
    if entry.get("flip"):
        made = cloning.flip(made)
    made.source["role"] = entry.get("role") if entry.get("role") in ROLES else ""
    return made, ""


def _coordinate(raw, length: int) -> int | None:
    """A base the page typed, 1-based, as a 0-based index."""
    try:
        at = int(raw)
    except (TypeError, ValueError):
        return None
    return at - 1 if 1 <= at <= length else None


def read_tray(db_session, entries) -> tuple[list[cloning.Fragment], str]:
    fragments: list[cloning.Fragment] = []
    if not isinstance(entries, list):
        return [], gettext("Add a fragment to start.")
    for entry in entries[:MAX_FRAGMENTS]:
        if not isinstance(entry, dict):
            continue
        made, problem = fragment_from(db_session, entry)
        if problem:
            return [], problem
        fragments.append(made)
    return fragments, ""


# ---------------------------------------------------------------------------
# What the status line says
# ---------------------------------------------------------------------------


def _end_words(text: str) -> str:
    return text or gettext("blunt")


def status_text(problem: dict | None, result: dict, method: str) -> str:
    """One sentence: what is wrong and which end it is, or what will be made."""
    if problem is None:
        return gettext("%(n)s fragments · %(bp)s bp · ready.", n=len(result["parts"]), bp=result["length"])
    kind = problem.get("kind")
    if kind == "too_few":
        return ngettext("Add %(least)s fragment to start.", "Add %(least)s fragments to start.",
                        problem["least"], least=problem["least"])
    if kind == "ends":
        return gettext("Fragment %(at)s's 3′ end (%(left)s) does not fit fragment %(next)s's 5′ end (%(right)s).",
                       at=problem["at"], next=problem["next"], left=_end_words(problem["left"]),
                       right=_end_words(problem["right"]))
    if kind == "no_overlap":
        return gettext("Fragment %(at)s's 3′ end has no overlap with fragment %(next)s.", at=problem["at"],
                       next=problem["next"])
    if kind == "repeated":
        return gettext("Fragments %(at)s and %(next)s are joined by the same bases, %(bases)s: what lies between them could swap round or drop out.", at=problem["at"], next=problem["next"], bases=problem["bases"])
    if kind == "mirrored":
        return gettext("The overhang after fragment %(at)s, %(bases)s, is the reverse complement of the one after fragment %(next)s: a fragment could go in backwards.", at=problem["at"], next=problem["next"], bases=problem["bases"])
    if kind == "palindrome":
        return gettext("The overhang after fragment %(at)s, %(bases)s, reads the same on both strands, so that end fits itself.", at=problem["at"], bases=problem["bases"])
    if kind == "blunt":
        return gettext("Fragment %(at)s's 3′ end is blunt: cut it with %(enzyme)s so it leaves an overhang.",
                       at=problem["at"], enzyme=problem["enzyme"])
    if kind == "uneven":
        return gettext("The overhangs are %(sizes)s bases long: a Golden Gate needs them all the same.",
                       sizes=problem["sizes"])
    return gettext("These fragments do not go together.")


# ---------------------------------------------------------------------------
# Working the tray out
# ---------------------------------------------------------------------------


def _amplified(fragments: list[cloning.Fragment]) -> set[int]:
    """Which fragments a PCR makes, and so can be given a homology arm: a
    piece lifted straight out of a digest is not one of them."""
    return {i for i, f in enumerate(fragments) if f.source.get("kind") != "digest"}


def plan(fragments: list[cloning.Fragment], method: str, circular: bool, enzyme: str = "BsaI",
         design: bool = True) -> dict:
    """The assembly as the page shows it: the product, every junction, and
    the one sentence that says whether it goes together."""
    amplify = _amplified(fragments)
    if method == "gibson":
        result = cloning.gibson(fragments, circular, design=design, amplify=amplify)
    elif method == "golden_gate":
        result = cloning.golden_gate(fragments, circular, enzyme=enzyme)
    else:
        result = cloning.ligate(fragments, circular)
    result["primers"] = (cloning.design_gibson_primers(fragments, circular, amplify=amplify)
                         if method == "gibson" and design else [])
    result["status"] = status_text(result["problem"], result, method)
    result["junction_rows"] = [_junction_row(j, method) for j in result["junctions"]]
    return result


def _junction_row(junction: dict, method: str) -> dict:
    """A junction as the tray draws it: its bases, and what they are."""
    if method == "gibson":
        if junction["bases"]:
            words = gettext("%(n)s bp overlap", n=junction["length"])
        elif junction["designed"]:
            words = gettext("the primers will add the overlap")
        else:
            words = gettext("no overlap")
    elif junction["kind"] == "blunt":
        words = gettext("blunt")
    else:
        words = gettext("%(kind)s′ overhang", kind=junction["kind"])
    return {"at": junction["at"], "next": junction["next"], "bases": junction["bases"], "words": words,
            "ok": junction["ok"], "tm": junction.get("tm")}


def _fragment_rows(fragments: list[cloning.Fragment]) -> list[dict]:
    """The tray, as the page lists it."""
    roles = role_labels()
    return [{"at": i + 1, "name": f.name, "length": len(f), "plasmid": f.source.get("plasmid"),
             "kind": f.source.get("kind"), "flipped": bool(f.source.get("flipped")),
             "left": f.left.text, "right": f.right.text, "features": len(f.features),
             "role": roles.get(f.source.get("role") or "", "")}
            for i, f in enumerate(fragments)]


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@bp.before_request
def require_login():
    if g.get("user") is None:
        return redirect(url_for("login", next=request.path))
    return None


def _sources(db_session) -> list[dict]:
    """Every plasmid with a sequence, for the fragment picker."""
    return [{"number": p.plasmid_id, "name": p.name or "", "length": len(p.full_sequence or ""),
             "circular": bool(p.is_circular), "label": _plasmid_label(p)}
            for p in db_session.scalars(select(PlasmidRecord).where(PlasmidRecord.full_sequence != "")
                                        .order_by(PlasmidRecord.plasmid_id))]


def _enzyme_rows(names=None) -> list[dict]:
    chosen = names if names is not None else sorted(cloning.ENZYMES)
    return [{"name": name, "label": cloning.ENZYMES[name].label,
             "overhang": cloning.ENZYMES[name].overhang, "outside": cloning.ENZYMES[name].outside}
            for name in chosen if name in cloning.ENZYMES]


def cutters_of(p: PlasmidRecord) -> list[dict]:
    """The enzymes worth offering for this plasmid: the ones that have a site
    in it, how many, and where they cut. Forty enzymes none of which cuts is
    a list to read rather than a choice to make, and the one that cuts once
    is usually the one you want, so the single cutters come first."""
    sequence, circular = p.full_sequence or "", bool(p.is_circular)
    found = []
    for name, enzyme in cloning.ENZYMES.items():
        sites = cloning.sites(sequence, circular, enzyme)
        if not sites:
            continue
        found.append({"name": name, "label": enzyme.label, "sites": len(sites),
                      "at": sorted((cut.at % len(sequence)) + 1 for cut in sites),
                      "overhang": enzyme.overhang, "outside": enzyme.outside,
                      "where": ngettext("%(num)s site", "%(num)s sites", len(sites))})
    return sorted(found, key=lambda e: (e["sites"], e["name"]))


@bp.route("/")
def wizard():
    method = request.args.get("method", "digest_ligate")
    if method not in cloning.METHODS:
        method = "digest_ligate"
    with SessionLocal() as db_session:
        sources = _sources(db_session)
        preload = _source_plasmid(db_session, request.args.get("from"))
        start = preload.plasmid_id if preload is not None and preload.full_sequence else None
        library = len(feature_library.entries(db_session))
    return render_template("plasmid_assembly.html", method=method, methods=cloning.METHODS, sources=sources,
                           golden_gate_enzymes=_enzyme_rows(cloning.GOLDEN_GATE_ENZYMES),
                           role_labels=role_labels(), start=start, library=library,
                           max_fragments=MAX_FRAGMENTS,
                           method_labels={m: method_label(m) for m in cloning.METHODS},
                           method_blurbs={m: method_blurb(m) for m in cloning.METHODS})


@bp.route("/source/<int:number>")
def source(number: int):
    """What can be taken off one plasmid: its features, and — with
    ?enzymes=EcoRI,HindIII — the pieces those enzymes leave."""
    with SessionLocal() as db_session:
        p = _source_plasmid(db_session, number)
        if p is None or not (p.full_sequence or ""):
            return jsonify({"ok": False, "error": gettext("That plasmid has no sequence.")}), 404
        names = [e for e in (request.args.get("enzymes") or "").split(",") if e in cloning.ENZYMES]
        length = len(p.full_sequence or "")
        pieces = [{"piece": i, "length": len(f), "left": f.left.text, "right": f.right.text,
                   "start": f.source["start"] + 1,
                   "end": (f.source["start"] + f.source["length"] - 1) % length + 1,
                   "features": [a["name"] for a in f.features if a.get("name")][:4],
                   "enzymes": f.source.get("enzymes") or []}
                  for i, f in enumerate(pieces_of(p, names))] if names else []
        return jsonify({"ok": True, "number": p.plasmid_id, "label": _plasmid_label(p),
                        "length": length, "circular": bool(p.is_circular),
                        "features": _named_features(p), "pieces": pieces, "cutters": cutters_of(p),
                        "uncut": bool(names) and not pieces})


@bp.route("/preview", methods=["POST"])
def preview():
    """The tray as it stands: the status line, the junctions and the map."""
    sent = request.get_json(silent=True) or {}
    method = sent.get("method") if sent.get("method") in cloning.METHODS else "digest_ligate"
    circular = bool(sent.get("circular", True))
    with SessionLocal() as db_session:
        fragments, problem = read_tray(db_session, sent.get("fragments"))
    if problem:
        return jsonify({"ok": False, "status": problem, "junctions": [], "fragments": [], "parts": []})
    result = plan(fragments, method, circular, enzyme=sent.get("enzyme") or "BsaI",
                  design=bool(sent.get("design", True)))
    return jsonify({
        "ok": result["ok"], "status": result["status"], "length": result["length"], "circular": circular,
        "junctions": result["junction_rows"], "fragments": _fragment_rows(fragments),
        "parts": result["parts"],
        "features": [{"name": f.get("name") or "", "start": f["start"], "end": f["end"],
                      "direction": f.get("direction", 1), "color": f.get("color") or "#cbd5e1"}
                     for f in result["features"]],
        "primers": [{"name": p["name"], "sequence": p["sequence"], "tm": p["tm"], "length": p["length"],
                     "tail": len(p["tail"])} for p in result["primers"]],
    })


# ---------------------------------------------------------------------------
# Making the product
# ---------------------------------------------------------------------------


def _next_number(db_session) -> int:
    return (db_session.scalar(select(func.max(PlasmidRecord.plasmid_id))) or 0) + 1


def part_annotations(parts: list[dict]) -> list[dict]:
    """One annotation a fragment, so the product's map shows where the joins
    are and which plasmid each stretch came from."""
    return [{"name": part["name"] or gettext("fragment %(n)s", n=i + 1), "type": "misc_feature",
             "start": part["start"], "end": part["end"], "direction": 1, "kind": "part",
             "color": PART_COLOURS[i % len(PART_COLOURS)],
             "notes": {"note": [f"assembled fragment {i + 1}"]}}
            for i, part in enumerate(parts)]


def _roles(fragments: list[cloning.Fragment]) -> list[str]:
    """What each fragment is, where nobody said: the longest one is the
    backbone and the rest are inserts, which is how a cloning is written up.
    Once someone has named a backbone, the others are inserts."""
    named = [f.source.get("role") or "" for f in fragments]
    longest = -1 if "backbone" in named or not fragments else max(range(len(fragments)),
                                                                 key=lambda i: len(fragments[i]))
    return [role or ("backbone" if i == longest else "insert") for i, role in enumerate(named)]


def _record_lineage(db_session, product, fragments, roles, method, primer_ids, user) -> None:
    for i, fragment in enumerate(fragments):
        parent = db_session.get(PlasmidRecord, fragment.source.get("row")) if fragment.source.get("row") else None
        details = {"kind": fragment.source.get("kind", ""), "start": fragment.source.get("start", 0) + 1,
                   "length": fragment.source.get("length", len(fragment)), "bp": len(fragment),
                   "left": fragment.left.text, "right": fragment.right.text}
        if fragment.source.get("enzymes"):
            details["enzymes"] = fragment.source["enzymes"]
        if fragment.source.get("feature"):
            details["feature"] = fragment.source["feature"]
        if fragment.source.get("flipped"):
            details["flipped"] = True
        if primer_ids.get(i):
            details["primers"] = primer_ids[i]
        try:
            plasmid_lineage.add_parent(db_session, product, parent=parent, label=fragment.name, role=roles[i],
                                       method=method, details=details, user=user)
        except ValueError:
            continue        # a plasmid cannot be its own parent; the rest still are


def _draw_primers(db_session, fragments, primers, product_name, user) -> tuple[dict, int, list[str]]:
    """Keep each designed primer in the lab's Primers database against the
    plasmid it amplifies, and draw it on that plasmid's map where it binds,
    so Copy for ordering finds it with the rest. Returns the record ids per
    fragment, how many maps were drawn on, and the ones left alone."""
    by_fragment: dict[int, list[int]] = {}
    if not primers:
        return by_fragment, 0, []
    module = primer_records.primers_module(db_session, user)
    if module is None:          # no Primers database, and this person may not add one
        return by_fragment, 0, []
    locked, touched = [], {}
    for primer in primers:
        at = primer["fragment"] - 1
        fragment = fragments[at]
        template = db_session.get(PlasmidRecord, fragment.source.get("row")) if fragment.source.get("row") else None
        if template is None:
            continue
        name = f"{product_name} {primer['name']}"[:200] if product_name else primer["name"]
        item, _made = primer_records.save_primer(
            db_session, module, template, name=name, sequence=primer["sequence"],
            direction=primer["direction"], user=user, category="cloning",
            notes=gettext("Designed for the assembly of %(name)s.", name=product_name or ""))
        by_fragment.setdefault(at, []).append(item.id)
        if not access.can_edit(template):
            if _plasmid_label(template) not in locked:
                locked.append(_plasmid_label(template))
            continue
        marks = touched.setdefault(template.id, (template, _features_of(template)))[1]
        sites = primer_records.binding_sites(template.full_sequence or "", bool(template.is_circular),
                                             primer["sequence"])
        if not sites:
            continue
        site = sites[0]
        if any(m.get("kind") == "primer" and m.get("start") == site["start"] and m.get("end") == site["end"]
               for m in marks):
            continue
        marks.append({"name": name, "type": "primer_bind", "start": site["start"], "end": site["end"],
                      "direction": site["direction"], "kind": "primer", "color": "#f59e0b",
                      "bases": primer["sequence"], "inventory_id": item.id})
    drawn = 0
    for template, marks in touched.values():
        if marks != _features_of(template):
            plasmid_versions.before_change(db_session, template)
            template.features_json = json.dumps(marks)
            plasmid_versions.record(db_session, template, "primers", user, detail=product_name or "")
            drawn += 1
    return by_fragment, drawn, locked


class Refused(Exception):
    """Something in the tray is wrong; the sentence to show is the message."""


def _build(db_session, tray, form, user: str) -> dict:
    """Everything the product needs, inside one batch. Raises Refused with
    the sentence to show when the tray does not go together."""
    method = form.get("method") if form.get("method") in cloning.METHODS else "digest_ligate"
    circular = form.get("circular", "1") != "0"
    name = (form.get("name") or "").strip()[:200]
    if not name:
        raise Refused(gettext("Give the new plasmid a name."))
    fragments, problem = read_tray(db_session, tray)
    if problem:
        raise Refused(problem)
    result = plan(fragments, method, circular, enzyme=form.get("enzyme") or "BsaI",
                  design=form.get("design", "1") != "0")
    if not result["ok"]:
        raise Refused(result["status"])
    roles = _roles(fragments)
    backbone = next((f for f, role in zip(fragments, roles) if role == "backbone"), None)
    sources = [db_session.get(PlasmidRecord, f.source["row"]) for f in fragments if f.source.get("row")]
    product = PlasmidRecord(
        plasmid_id=_next_number(db_session), name=name, owner=user,
        backbone=(backbone.name if backbone is not None else "")[:200],
        insert_seq=" + ".join(f.name for f, role in zip(fragments, roles) if role != "backbone")[:200],
        # A product keeps the resistance of whichever parent carried one:
        # the backbone decides what the colonies are grown on.
        resistance=next((p.resistance for p in sources if p is not None and p.resistance), "")[:80],
        notes=(form.get("notes") or "").strip(),
        full_sequence=result["sequence"], is_circular=circular, sequence_uploaded_at=datetime.utcnow(),
    )
    with audit.batch(db_session, "create", f"Assembled plasmid {name}", "plasmids") as batch_row:
        batch_row.record_count = 1
        db_session.add(product)
        db_session.flush()
        primer_ids, drawn, locked = _draw_primers(db_session, fragments, result["primers"], name, user)
        found = []
        if form.get("detect") == "1":
            found = feature_library.detect(feature_library.entries(db_session), result["sequence"], circular,
                                           result["features"])
        product.features_json = json.dumps(result["features"] + found + part_annotations(result["parts"]))
        product.updated_at, product.updated_by = datetime.utcnow(), user
        plasmid_versions.record(db_session, product, "assembly", user,
                                detail=f"{method_label(method)} · {len(fragments)}")
        _record_lineage(db_session, product, fragments, roles, method, primer_ids, user)
    return {"number": product.plasmid_id, "name": name, "bp": result["length"], "fragments": len(fragments),
            "found": len(found), "primers": len(result["primers"]), "drawn": drawn, "locked": locked}


@bp.route("/create", methods=["POST"])
def create():
    """Make the product: its sequence and features, a part annotation per
    fragment, the version it starts from, and a Made from link for every
    plasmid it came out of."""
    method = request.form.get("method") if request.form.get("method") in cloning.METHODS else "digest_ligate"
    try:
        tray = json.loads(request.form.get("tray") or "[]")
    except json.JSONDecodeError:
        tray = []
    for _attempt in range(3):
        with SessionLocal() as db_session:
            try:
                made = _build(db_session, tray, request.form, g.user.username)
                db_session.commit()
            except Refused as refused:
                flash(f"{refused}", "error")
                return redirect(url_for("cloning.wizard", method=method))
            except IntegrityError:
                # Someone took the next number between reading it and saving.
                db_session.rollback()
                continue
            break
    else:
        flash(gettext("Couldn’t allocate a plasmid number. Try again."), "error")
        return redirect(url_for("cloning.wizard", method=method))
    flash(gettext("Plasmid #%(number)s %(name)s · %(bp)s bp from %(n)s fragments.", number=made["number"],
                  name=made["name"], bp=made["bp"], n=made["fragments"]), "success")
    if made["found"]:
        flash(ngettext("Marked %(num)s feature from the library.", "Marked %(num)s features from the library.",
                       made["found"], num=made["found"]), "info")
    if made["primers"]:
        flash(ngettext("%(num)s primer saved to order, drawn on %(drawn)s map.",
                       "%(num)s primers saved to order, drawn on %(drawn)s maps.",
                       made["primers"], num=made["primers"], drawn=made["drawn"]), "info")
    if made["locked"]:
        flash(gettext("The primers are saved, but %(plasmids)s is not yours to draw them on.",
                      plasmids=", ".join(made["locked"])), "warning")
    return redirect(url_for("plasmid_page", number=made["number"]))
