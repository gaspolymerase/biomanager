"""Plasmids: the sheet, box grid and list (/plasmids), the detail page with
the sequence editor (/plasmid/<number>), and the writes behind them
(/plasmids/<row id>/…, where the page's own script sends them).

Every write checks access.can_edit: a plasmid is its owner's (or anyone's
while unowned); admins can change anything. Boxes are PlasmidBox rows
(app/plasmid_service.py): each has a size and a naming scheme, and every
position is shown and typed in it ("D7"). Resizing, renaming or deleting a
box is access.can_edit_rack (its creator or an admin); putting a plasmid
in any box is open to whoever may edit the plasmid.

These pages hang on the application object from app/flask_app.py and keep
the endpoint names every page links to ("plasmids", "plasmid_page",
"plasmid_detail", ...); tests/test_url_map.py holds them to it. They take
what every page needs from app/web.py and never import app.py, which
imports them.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from flask import Response, abort, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .db import SessionLocal
from . import access, i18n, positions
from .i18n import gettext, ngettext
from .formutil import form_changed
from . import audit
from . import custom_fields, feature_library, feature_pack, plannotate, plasmid_lineage, plasmid_versions, primer_records
from .models import PlasmidBox, PlasmidFile, PlasmidRecord, PlasmidSequenceVersion
from .services import current_lab_usernames, save_uploaded_file
from . import bulk, rack_layout, stewardship
from . import groups as project_groups
from .days import fmt_day
from . import plasmid_service as pbox
from .flask_app import app
from .web import admin_required, local_time, login_required, stamp_updated


def _plasmid_denied(p) -> str:
    owner = (p.owner or "").strip() or gettext("someone else")
    return gettext("Plasmid #%(id)s belongs to %(owner)s. Ask them, or an admin, to change it.", id=p.plasmid_id,
                   owner=owner)


def _plasmid_label(p) -> str:
    return f"#{p.plasmid_id}" + (f" {p.name}" if p.name else "")


def _next_plasmid_id(db_session) -> int:
    return (db_session.scalar(select(func.max(PlasmidRecord.plasmid_id))) or 0) + 1


def _box_of(db_session, p):
    return db_session.get(PlasmidBox, p.box_id_fk) if p.box_id_fk else None


def _plasmid_where(p, box) -> str:
    """"Box A · D7", "Box A" (no cell yet), or ""."""
    if box is None:
        return ""
    position = pbox.label(p, box)
    return f"{box.name} · {position}" if position else box.name


def _submitted_sequence(file_field: str) -> tuple[dict | None, str]:
    """The sequence a form sent, as (parsed, problem). Both are empty when
    nothing was sent; a file or text that is not a sequence gives a problem
    instead of being quietly ignored."""
    from .sequence_parser import parse_sequence_bytes, parse_sequence_text

    upload = request.files.get(file_field)
    if upload is not None and upload.filename:
        parsed = parse_sequence_bytes(upload.read(), upload.filename)
        if not parsed or not parsed.get("sequence"):
            return None, gettext("%(file)s is not a sequence file this app can read (SnapGene .dna, GenBank or FASTA).",
                                 file=upload.filename)
        return parsed, ""
    raw_text = (request.form.get("sequence_text") or "").strip()
    if raw_text:
        parsed = parse_sequence_text(raw_text)
        if not parsed or not parsed.get("sequence"):
            return None, gettext("The pasted text is not a sequence. Paste FASTA, GenBank, or bases only (IUPAC letters).")
        return parsed, ""
    return None, ""


def _plasmid_primers(db_session, p) -> dict:
    """The Primers card: records in the Primers database that name this
    plasmid, and where each binds on it (primer_records.binding_sites)."""
    module = primer_records.primers_module(db_session, create=False)
    rows = []
    for item in primer_records.primers_for(db_session, p):
        attrs = item.attrs_dict
        sites = primer_records.binding_sites(p.full_sequence or "", bool(p.is_circular), attrs.get("sequence", ""))
        where = ""
        if sites:
            site = sites[0]
            where = f"{site['start'] + 1}–{site['end'] + 1} {'→' if site['direction'] > 0 else '←'}"
        rows.append({"id": item.id, "name": item.name or f"#{item.number}", "sequence": attrs.get("sequence", ""),
                     "tm": attrs.get("tm", ""), "status": item.status, "where": where, "more_sites": max(len(sites) - 1, 0),
                     "tail": sites[0]["tail"] if sites else 0, "binds": bool(sites),
                     "url": url_for("inventory.module", key=module.key, open=item.id) if module else ""})
    # Find saved primers: a sequence to look along, and primers to look through.
    can_scan = bool(p.full_sequence) and bool(primer_records.visible_primer_databases(db_session))
    return {"rows": rows, "database": module.label if module else "",
            "url": url_for("inventory.module", key=module.key) if module else "",
            "can_scan": can_scan, "scan_url": url_for("plasmid_primer_sites", row_id=p.id)}


FOUND_PRIMER_ID = "found-primer-"   # Find saved primers' marks on the map: shown, never saved


def primer_sites(db_session, p, with_listed: bool = False) -> dict:
    """Every saved primer the person may see that binds plasmid `p`, wherever
    its Plasmid column points (GitHub #62: don't order a primer the lab
    already has). The ones the plasmid's Primers card lists already are
    counted, not repeated, unless `with_listed` (a page without that card).
    Positions are 1-based, as the page shows them; a reverse primer's site
    runs start→end along the top strand."""
    template = p.full_sequence or ""
    index = primer_records.TemplateIndex(template, bool(p.is_circular))
    listed = set() if with_listed else {i.id for i in primer_records.primers_for(db_session, p)}
    number = str(p.plasmid_id)
    hits, scanned, already, found = [], 0, 0, set()
    for module, item in primer_records.saved_primers(db_session):
        attrs = item.attrs_dict
        sequence = re.sub(r"[^A-Za-z]", "", str(attrs.get("sequence") or "")).upper()
        if not sequence:
            continue
        scanned += 1
        sites = index.sites(sequence)
        if not sites:
            continue
        if item.id in listed:
            already += 1
            continue
        found.add(item.id)
        for s in sites:
            forward = s["direction"] > 0
            hits.append({
                "id": item.id, "number": item.number, "name": item.name or f"#{item.number}",
                "database": i18n.translate_value(module.label, "inventory"),
                "url": url_for("inventory.module", key=module.key, open=item.id),
                "sequence": sequence, "length": len(sequence),
                "status": i18n.translate_value(item.status or "", "inventory"),
                "start": s["start"] + 1, "end": s["end"] + 1,
                "strand": 1 if forward else -1, "direction": "forward" if forward else "reverse",
                "where": f"{s['start'] + 1}–{s['end'] + 1} {'→' if forward else '←'}",
                "annealed": s["annealed"], "tail": s["tail"], "exact": s["tail"] == 0,
                # Its Plasmid column names this one (in a database the card doesn't list, unless with_listed).
                "linked": str(attrs.get(primer_records.TEMPLATE_KEY, "")).strip() == number,
            })
    hits.sort(key=lambda h: (h["start"], h["name"].lower()))
    return {"ok": True, "length": len(template), "circular": bool(p.is_circular), "scanned": scanned,
            "primers": len(found), "sites": len(hits), "listed": already, "hits": hits}


def _parent_role_label(role: str) -> str:
    return {"backbone": gettext("backbone"), "insert": gettext("insert"), "template": gettext("template"),
            "donor": gettext("donor"), "other": gettext("parent")}.get(role, role)


def _parent_method_label(method: str) -> str:
    return {"digest_ligate": gettext("Digest and ligate"), "gibson": gettext("Gibson / HiFi assembly"),
            "infusion": gettext("In-Fusion"),
            "golden_gate": gettext("Golden Gate"), "pcr": gettext("PCR"), "mutagenesis": gettext("Mutagenesis"),
            "gateway": gettext("Gateway"), "synthesis": gettext("Synthesised"), "other": gettext("Other")}.get(method, "")


def _tree_rows(family: dict) -> dict:
    """The family tree as the page lists it: each node with its number, link
    and role, children nested."""
    def node(n: dict) -> dict:
        plasmid = n["plasmid"]
        return {"label": n["label"], "number": plasmid.plasmid_id if plasmid else None,
                "url": url_for("plasmid_page", number=plasmid.plasmid_id) if plasmid else "",
                "role": _parent_role_label(n["role"]) if n["role"] else "",
                "children": [node(c) for c in n["children"]]}
    return {"up": [node(n) for n in family["up"]], "down": [node(n) for n in family["down"]]}


def _version_row(v, current: bool) -> dict:
    """One line of a plasmid's version history, as its Sequence tab lists it."""
    if v.how == "upload":
        how = gettext("Loaded from a %(format)s file", format=(v.detail or "sequence").upper())
    elif v.how == "restore":
        try:
            restored = fmt_day(local_time(datetime.fromisoformat(v.detail)), with_time=True)
        except ValueError:
            restored = v.detail
        how = gettext("Restored the version of %(when)s", when=restored)
    else:
        how = {"editor": gettext("Edited on the Sequence tab"), "hand": gettext("Edited by hand"),
               "detect": gettext("Features found from the library"),
               "annotate": gettext("Features found by pLannotate"),
               "assembly": gettext("Assembled from fragments"),
               "mutagenesis": gettext("Made by site-directed mutagenesis"),
               "primers": gettext("Primers the wizard designed, drawn on the map"),
               "clear": gettext("Cleared"), "baseline": gettext("As it was before version history")}.get(v.how, v.how)
    try:
        features = len([f for f in json.loads(v.features_json or "[]") if (f.get("kind") or "feature") == "feature"])
    except (json.JSONDecodeError, AttributeError):
        features = 0
    return {"id": v.id, "how": how, "who": v.saved_by, "when": fmt_day(local_time(v.saved_at), with_time=True),
            "bp": len(v.full_sequence or ""), "features": features, "circular": bool(v.is_circular),
            "current": current}


def _several_records(parsed: dict) -> str:
    """A file of several sequences keeps its first: say so."""
    return gettext("The file holds %(n)s sequences; only the first, %(name)s, was kept.",
                   n=parsed["records"], name=parsed.get("name") or gettext("unnamed"))


def _apply_parsed_sequence(p, parsed: dict) -> None:
    p.full_sequence = parsed["sequence"]
    p.is_circular = bool(parsed.get("is_circular"))
    p.features_json = json.dumps(parsed.get("features") or [])
    p.sequence_format = parsed.get("format", "")
    p.sequence_uploaded_at = datetime.utcnow()


def _plasmid_values(p, box) -> dict:
    """What the sheet shows for a row, as the server now has it."""
    return {
        "name": p.name, "backbone": p.backbone, "insert_seq": p.insert_seq,
        "resistance": p.resistance, "owner": p.owner, "location": p.location,
        "concentration": p.concentration, "a260_280": p.a260_280, "is_shared": project_groups.record_value(p),
        "notes": p.notes, "box_id": str(p.box_id_fk or ""), "storage_box": box.name if box else "",
        "position": pbox.label(p, box),
    }


def _plasmid_payload(p, box, editable: bool) -> dict:
    """The record dialog's view of a plasmid (see static/record-dialog.js)."""
    values = _plasmid_values(p, box)
    held = custom_fields.values(p)
    return {
        "id": p.id, "_label": _plasmid_label(p), "_locked": not editable, "_manage": access.can_manage(p),
        "plasmid_id": p.plasmid_id, **values,
        **{f"attr_{key}": value for key, value in held.items()},
        "box_id_was": values["box_id"], "position_was": values["position"],
    }


def _plasmid_grid(rows, boxes, new_payload) -> dict:
    """The rack grid's payload (static/rack-grid.js): boxes grouped by their
    freezer, and every plasmid as a tile. Rows and columns are 1-based
    here; the page posts moves 0-based (data-index-base="0")."""
    counts = {}
    for r in rows:
        if r["p"].box_id_fk:
            counts[r["p"].box_id_fk] = counts.get(r["p"].box_id_fk, 0) + 1
    items = []
    for r in rows:
        p = r["p"]
        placed = pbox.is_stored(p)
        items.append({
            "id": p.id, "label": f"#{p.plasmid_id}", "sub": p.name or "",
            "badge": (p.resistance or "")[:5], "rack": p.box_id_fk,
            "row": p.box_row + 1 if placed else None, "col": p.box_col + 1 if placed else None,
            "locked": not r["editable"],
            "title": " · ".join(filter(None, [f"#{p.plasmid_id}", p.name, p.backbone, p.resistance, p.owner,
                                              "" if r["editable"] else "read only"])),
            "search": " ".join(filter(None, [f"#{p.plasmid_id}", p.name, p.backbone, p.insert_seq,
                                             p.resistance, p.owner, r["position"]])).lower(),
            "edit": {"data-record-edit": "plasmid-dialog", "data-record-payload": json.dumps(r["payload"])},
        })
    return {
        "racks": [{"id": b.id, "name": b.name, "rows": b.rows, "cols": b.cols, "group": b.location or "",
                   "naming": positions.scheme(b.naming),
                   "edit": {"data-record-edit": "plasmid-box-dialog",
                            "data-record-payload": json.dumps(pbox.box_payload(b, counts.get(b.id, 0)))}}
                  for b in boxes],
        "items": items,
        "create": {"attrs": {"data-record-edit": "plasmid-dialog"}, "payload": new_payload,
                   "rack_field": "box_id", "row_field": None, "col_field": None, "text_field": "position"},
    }


def _plasmid_back(row_id: int | None = None):
    """Back to the page the form was on (this site only), else the plasmid."""
    referrer = request.referrer or ""
    if referrer.startswith(request.host_url):
        return redirect(referrer)
    if row_id:
        return redirect(plasmid_page_url(row_id))
    return redirect(url_for("plasmids"))


PLASMID_MEASURES = (("concentration", "Concentration (ng/µL)", 40), ("a260_280", "260/280", 20))


def _plasmid_measure(form, field: str) -> str:
    """A plasmid's concentration or 260/280 as typed, if it is a number
    ("412", "1.86"); ValueError with the message to show otherwise."""
    label, limit = next((lb, lim) for key, lb, lim in PLASMID_MEASURES if key == field)
    value = (form.get(field) or "").strip().replace(",", ".")[:limit]
    if value:
        try:
            float(value)
        except ValueError:
            raise ValueError(gettext("%(field)s is a number: “%(value)s” isn't one.", field=gettext(label),
                                     value=value)) from None
    return value


def _plasmid_int(raw) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.lstrip("-").isdigit() else None


@app.route("/plasmids", methods=["GET", "POST"])
@login_required
def plasmids():
    if request.method == "POST":
        return _create_plasmid()
    me = g.user.username
    with SessionLocal() as db_session:
        records = db_session.scalars(select(PlasmidRecord).order_by(PlasmidRecord.plasmid_id.desc())).all()
        boxes = pbox.all_boxes(db_session)
        box_by_id = {b.id: b for b in boxes}
        # Each plasmid's newest accepted clone, for the List's cards (app/clone_routes.py).
        from .models import PlasmidClone
        verified_at = dict(db_session.execute(select(PlasmidClone.plasmid_row_id, func.max(PlasmidClone.accepted_at))
                                              .where(PlasmidClone.accepted_at.is_not(None))
                                              .group_by(PlasmidClone.plasmid_row_id)).all())
        rows = []
        for p in records:
            editable = access.can_edit(p)
            box = box_by_id.get(p.box_id_fk)
            rows.append({
                "p": p, "box": box, "editable": editable, "manageable": access.can_manage(p), "mine": p.owner == me,
                "stored": pbox.is_stored(p), "position": pbox.label(p, box),
                "where": _plasmid_where(p, box),
                "length": len(p.full_sequence or ""),
                "payload": _plasmid_payload(p, box, editable),
                "mini": _mini_map(p),
                "verified": fmt_day(local_time(verified_at[p.id])) if p.id in verified_at else "",
            })
        next_id = _next_plasmid_id(db_session)
        usernames = current_lab_usernames(db_session)
        new_payload = {"owner": me, "plasmid_id": next_id, "count": 1}
        grid = _plasmid_grid(rows, boxes, new_payload)
        counts = {
            "all": len(rows),
            "mine": sum(1 for r in rows if r["mine"]),
            "sequence": sum(1 for r in rows if r["length"]),
            "stored": sum(1 for r in rows if r["stored"]),
            "lab": sum(1 for r in rows if r["p"].is_shared),
        }
        box_list = [{"id": b.id, "name": b.name, "location": b.location or ""} for b in boxes]
        defined_columns = custom_fields.fields(db_session, "plasmids")
        return render_template(
            "plasmids.html",
            rows=rows,
            counts=counts,
            usernames=usernames,
            next_plasmid_id=next_id,
            new_payload=new_payload,
            boxes=box_list,
            grid=grid,
            max_batch=pbox.MAX_BATCH,
            custom_columns=custom_fields.in_table(defined_columns),
            custom_columns_all=defined_columns,
        )


def _create_plasmid():
    """New plasmids from the dialog: one, or up to 50 alike ("How many"),
    or one per line of a pasted list of names. They get consecutive
    numbers and sit side by side in the chosen box from the given position
    (or its first free cells); what does not fit waits in the box without a
    cell, and is reported. The number comes from the server when the one
    the dialog offered was taken meanwhile. A sequence that cannot be read,
    or a cell that is taken, is reported rather than dropped in silence
    (the plasmids are still created). One batch, so it can be undone."""
    form = request.form
    user = g.user.username
    parsed, seq_problem = _submitted_sequence("sequence_file")

    raw_id = (form.get("plasmid_id") or "").strip()
    if raw_id and (not raw_id.isdigit() or int(raw_id) < 1):
        flash(gettext("“%(value)s” is not a plasmid number. Leave it blank for the next free one.", value=raw_id),
              "error")
        return redirect(url_for("plasmids"))
    requested = int(raw_id) if raw_id else None

    names = [line.strip()[:200] for line in (form.get("names") or "").splitlines() if line.strip()]
    raw_count = str(len(names)) if names else (form.get("count") or "1").strip()
    count = int(raw_count) if raw_count.isdigit() else 0
    if not 1 <= count <= pbox.MAX_BATCH:
        flash(gettext("Make between 1 and %(max)s plasmids at a time (asked for %(asked)s).", max=pbox.MAX_BATCH,
                      asked=raw_count), "error")
        return redirect(url_for("plasmids"))

    name = (form.get("name") or "").strip()[:200] or ((parsed or {}).get("name") or "")[:200]
    if not names and not name:
        flash(gettext("Give the plasmid a name."), "error")
        return redirect(url_for("plasmids"))

    for _attempt in range(3):
        with SessionLocal() as db_session:
            try:
                made = _make_plasmids(db_session, form, user, count, name, names, requested, parsed)
                db_session.commit()
            except IntegrityError:
                # Someone took a number between our check and the commit.
                db_session.rollback()
                requested = None
                continue
            break
    else:
        flash(gettext("Couldn’t allocate plasmid numbers. Try again."), "error")
        return redirect(url_for("plasmids"))

    ids = made["ids"]
    first, last = ids[0], ids[-1]
    if count == 1:
        if raw_id and first != int(raw_id):
            made["notes"].insert(0, gettext("Plasmid #%(asked)s was taken by the time you saved, so this one is #%(first)s.", asked=raw_id, first=first))
        if parsed:
            flash(gettext("Added plasmid #%(first)s with a %(format)s sequence (%(bp)s bp · %(features)s features).",
                          first=first, format=parsed["format"].upper(), bp=len(parsed["sequence"]),
                          features=len(parsed.get("features") or [])), "success")
        else:
            flash(gettext("Added plasmid #%(first)s %(name)s.", first=first, name=made["names"][0]), "success")
    else:
        if raw_id and first != int(raw_id):
            made["notes"].insert(0, gettext("Some of #%(asked)s–#%(asked_last)s were taken, so these are #%(first)s–#%(last)s.", asked=raw_id, asked_last=int(raw_id) + count - 1,
                                            first=first, last=last))
        where = ""
        cells = made["cells"]
        if made["box"] and len(cells) > 1:
            where = gettext(" in %(box)s (%(first)s–%(last)s)", box=made["box"], first=cells[0], last=cells[-1])
        elif made["box"] and cells:
            where = gettext(" in %(box)s (%(cell)s)", box=made["box"], cell=cells[0])
        elif made["box"]:
            where = gettext(" in %(box)s", box=made["box"])
        seq = gettext(" with a %(format)s sequence", format=parsed["format"].upper()) if parsed else ""
        flash(gettext("Added %(count)s plasmids, #%(first)s–#%(last)s%(seq)s%(where)s. Undo it from Batch history.",
                      count=count, first=first, last=last, seq=seq, where=where), "success")
    for note in made["notes"]:
        flash(note, "warning")
    if parsed and parsed.get("records", 1) > 1:
        flash(_several_records(parsed), "warning")
    if seq_problem:
        flash(gettext("%(problem)s The plasmids were saved without a sequence; add one on their pages.",
                      problem=seq_problem) if count > 1 else
              gettext("%(problem)s The plasmid was saved without a sequence; add one on its page.",
                      problem=seq_problem), "warning")
    return redirect(url_for("plasmids"))


def _make_plasmids(db_session, form, user, count, name, names, requested, parsed) -> dict:
    """The writes behind _create_plasmid, inside one batch."""
    notes: list[str] = []
    label = (f"New plasmid {name or names[0]}" if count == 1
             else f"New plasmids ×{count}" + (f" ({names[0]}…)" if names else f" ({name})"))
    with audit.batch(db_session, "create", label, "plasmids") as batch_row:
        batch_row.record_count = count
        _given, box, problem = pbox.box_from_form(db_session, form, user)
        if problem:
            notes.append(gettext("%(problem)s The plasmids are not in a box.", problem=problem) if count > 1
                         else gettext("%(problem)s The plasmid is not in a box.", problem=problem))
        # Where the first one goes: "position" (D7) from the dialog, or the
        # older 0-based box_row / box_col pair; blank takes the first free cell.
        position = (form.get("position") or "").strip()
        legacy = (form.get("box_row") or "").strip() or (form.get("box_col") or "").strip()
        start, mode = None, "next"
        try:
            if position or legacy:
                if box is None:
                    raise ValueError(gettext("A position needs a box; the plasmid was saved without one.")
                                     if count == 1 else
                                     gettext("A position needs a box; the plasmids were saved without one."))
                if position:
                    start = pbox.parse(position, box)
                else:
                    start = (_plasmid_int(form.get("box_row")), _plasmid_int(form.get("box_col")))
                    if None in start:
                        raise ValueError(gettext("A box position needs both a row and a column."))
                    if not (0 <= start[0] < box.rows and 0 <= start[1] < box.cols):
                        raise ValueError(gettext("That cell is outside the box %(box)s (%(span)s).", box=box.name,
                                                 span=pbox.span(box)))
                mode = "exact" if count == 1 else "from"
        except ValueError as exc:
            if box is None:
                notes.append(f"{exc}")
            elif count > 1:
                notes.append(gettext("%(problem)s The new plasmids are in %(box)s but not placed.", problem=exc,
                                     box=box.name))
            else:
                notes.append(gettext("%(problem)s The new plasmid is in %(box)s but not placed.", problem=exc,
                                     box=box.name))
            mode = "none"

        cells = []
        if box is not None and mode in ("next", "from"):
            cells = pbox.free_cells(db_session, box, count, start if mode == "from" else None)
            if len(cells) < count:
                missing = count - len(cells)
                values = {"box": box.name, "room": len(cells), "count": count, "num": missing}
                if mode == "from":
                    values["start"] = pbox.cell_label(box, *start)
                    notes.append(ngettext("%(box)s had room for %(room)s of %(count)s from %(start)s; the other %(num)s is in it without a position.",
                                          "%(box)s had room for %(room)s of %(count)s from %(start)s; the other %(num)s are in it without a position.", missing, **values)
                                 if cells else
                                 ngettext("%(box)s had room for %(room)s of %(count)s from %(start)s; %(num)s is in it without a position.",
                                          "%(box)s had room for %(room)s of %(count)s from %(start)s; %(num)s are in it without a position.", missing, **values))
                else:
                    notes.append(ngettext("%(box)s had room for %(room)s of %(count)s; the other %(num)s is in it without a position.",
                                          "%(box)s had room for %(room)s of %(count)s; the other %(num)s are in it without a position.", missing, **values)
                                 if cells else
                                 ngettext("%(box)s had room for %(room)s of %(count)s; %(num)s is in it without a position.",
                                          "%(box)s had room for %(room)s of %(count)s; %(num)s are in it without a position.", missing, **values))

        top = _next_plasmid_id(db_session)
        first = top
        if requested:
            clash = db_session.scalar(select(func.count(PlasmidRecord.id)).where(
                PlasmidRecord.plasmid_id.between(requested, requested + count - 1)))
            if not clash:
                first = requested
        owner = form.get("owner", user).strip()[:120]
        measures = {}
        for field, _label, _limit in PLASMID_MEASURES:
            try:
                measures[field] = _plasmid_measure(form, field)
            except ValueError as exc:
                measures[field] = ""
                notes.append(gettext("%(problem)s It was left empty.", problem=exc))
        made = []
        for i in range(count):
            record = PlasmidRecord(
                plasmid_id=first + i,
                name=names[i] if names else name,
                backbone=(form.get("backbone") or "").strip()[:200],
                insert_seq=(form.get("insert_seq") or "").strip()[:200],
                resistance=(form.get("resistance") or "").strip()[:80],
                owner=owner,
                location=(form.get("location") or "").strip()[:120],
                concentration=measures["concentration"], a260_280=measures["a260_280"],
                notes=(form.get("notes") or "").strip(),
            )
            if project_groups.apply(record, form.get("is_shared")):
                record.is_shared, record.share_group_id = False, None
            pbox.put_in(record, box)
            if mode == "exact":
                refused, _ = pbox.place(db_session, record, box, *start)
                if refused:
                    pbox.put_in(record, box)
                    notes.append(gettext("%(problem)s The new plasmid is in %(box)s but not placed.", problem=refused,
                                         box=box.name))
            elif i < len(cells):
                record.box_row, record.box_col = cells[i]
            db_session.add(record)
            if parsed:
                _apply_parsed_sequence(record, parsed)
                # A SnapGene file's description fills an empty Notes.
                if parsed.get("description") and not (record.notes or "").strip():
                    record.notes = parsed["description"][:2000]
                plasmid_versions.record(db_session, record, "upload", user, parsed["format"])
            stamp_updated(record)
            made.append(record)
    return {
        "ids": [r.plasmid_id for r in made], "names": [r.name for r in made],
        "box": box.name if box is not None else "",
        "cells": [pbox.label(r, box) for r in made if box is not None and pbox.label(r, box)],
        "notes": notes,
    }


def _plasmid_answer(db_session, p, message: str = "", error: str = "", status: int = 409):
    """Answer a plasmid save: JSON for the sheet's autosave, else a flash
    and a redirect back to the page the form was on."""
    if request.headers.get("X-Autosave") == "1":
        if error:
            return jsonify({"ok": False, "error": error}), status
        box = _box_of(db_session, p)
        return jsonify({"ok": True, "row": {"active": pbox.is_stored(p), "values": _plasmid_values(p, box)},
                        "payload": _plasmid_payload(p, box, access.can_edit(p))})
    if error:
        flash(error, "error")
    elif message:
        flash(message, "success")
    return _plasmid_back(p.id if p is not None else None)


@app.route("/plasmids/<int:row_id>/update", methods=["POST"])
@login_required
def update_plasmid(row_id: int):
    """Inline edits from the sheet (X-Autosave, JSON back) and the record
    dialog / detail page forms (redirect back). Only fields the form sent
    change; box and position follow the `_was` stale-form guard, because
    the box grid can move a plasmid while its sheet row is on screen.
    The box comes as `box_id` (or, from older forms, a `storage_box`
    name); the position is typed in the box's own naming ("D7")."""
    form = request.form
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            if request.headers.get("X-Autosave") == "1":
                return jsonify({"ok": False, "error": gettext("That plasmid no longer exists.")}), 404
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            return _plasmid_answer(db_session, p, error=_plasmid_denied(p), status=403)
        if "name" in form and not form["name"].strip() and (p.name or "").strip():
            return _plasmid_answer(db_session, p, error=gettext("A plasmid needs a name."), status=400)
        # Lab common lets anyone edit it; whose it is stays its owner's call.
        # "1": the lab's, "g<id>": a project group's (app/groups.py).
        sharing_changes = "is_shared" in form and project_groups.differs(p, form.get("is_shared"))
        owner_changes = "owner" in form and (form.get("owner") or "").strip()[:120] != (p.owner or "")
        if (owner_changes or sharing_changes) and not access.can_manage(p):
            owner = (p.owner or "").strip() or gettext("its owner")
            return _plasmid_answer(db_session, p, status=403, error=gettext(
                "Plasmid #%(id)s is lab common, so you can edit it, but only %(owner)s or an admin can change whose it is.", id=p.plasmid_id, owner=owner))
        if sharing_changes:
            refused = stewardship.change_sharing(p, form.get("is_shared"), "plasmid", f"#{p.plasmid_id}")
            if refused:
                return _plasmid_answer(db_session, p, error=refused, status=403)
        if owner_changes:
            refused = stewardship.change_owner(db_session, p, form.get("owner"), "plasmid", f"#{p.plasmid_id}")
            if refused:
                db_session.rollback()
                return _plasmid_answer(db_session, p, error=refused, status=400)
        for field, limit in (("name", 200), ("backbone", 200), ("insert_seq", 200),
                             ("resistance", 80), ("location", 120)):
            if field in form:
                setattr(p, field, (form.get(field) or "").strip()[:limit])
        for field, _label, _limit in PLASMID_MEASURES:
            if field in form:
                try:
                    setattr(p, field, _plasmid_measure(form, field))
                except ValueError as exc:
                    db_session.rollback()
                    return _plasmid_answer(db_session, p, error=str(exc), status=400)
        if "notes" in form:
            p.notes = (form.get("notes") or "").strip()
        # Columns this lab added to Plasmids: only the ones the form sent.
        custom_fields.apply_form(p, form, custom_fields.fields(db_session, "plasmids"))

        problem = None
        if (("box_id" in form or "storage_box" in form or "position" in form)
                and form_changed(form, "box_id", "storage_box", "position")):
            current = _box_of(db_session, p)
            _given, box, problem = pbox.box_from_form(db_session, form, g.user.username, current)
            if not problem:
                typed = form.get("position") if "position" in form else pbox.label(p, current)
                if box is None:
                    # Clearing the box takes the plasmid out of it; typing a
                    # position with no box is a mistake worth saying so.
                    if (typed or "").strip() and form_changed(form, "position"):
                        problem = gettext("A position needs a box. Pick the box first.")
                    else:
                        pbox.place(db_session, p, None, None, None)
                else:
                    try:
                        row, col = pbox.parse(typed, box)
                    except ValueError as exc:
                        problem = str(exc)
                    else:
                        if row is None and (current is None or current.id != box.id):
                            # Into another box with no position: its next free cell.
                            cells = pbox.free_cells(db_session, box, 1)
                            row, col = cells[0] if cells else (None, None)
                        problem, _ = pbox.place(db_session, p, box, row, col)
        if problem and request.headers.get("X-Autosave") == "1":
            db_session.rollback()
            return jsonify({"ok": False, "error": problem}), 409
        # A dialog save keeps its other fields; only the move is refused
        # (a refused move changed nothing).
        stamp_updated(p)
        db_session.commit()
        if problem:
            return _plasmid_answer(db_session, p, error=gettext(
                "Saved plasmid #%(id)s, but not the box position: %(problem)s", id=p.plasmid_id, problem=problem))
        return _plasmid_answer(db_session, p, message=gettext("Saved plasmid #%(id)s.", id=p.plasmid_id))


@app.route("/plasmids/<int:row_id>/delete", methods=["POST"])
@login_required
def delete_plasmid(row_id: int):
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_manage(p):
            flash(_plasmid_denied(p) if not access.can_edit(p) else
                  gettext("Plasmid #%(id)s is lab common: only %(owner)s or an admin can delete it.", id=p.plasmid_id,
                          owner=(p.owner or "").strip() or gettext("its owner")), "error")
            return redirect(url_for("plasmids"))
        label = _plasmid_label(p)
        # A batch of one, so Batch history can bring it back.
        with audit.batch(db_session, "delete", f"delete plasmid {label}", "plasmids"):
            db_session.delete(p)
        db_session.commit()
    flash(gettext("Deleted plasmid %(plasmid)s. Undo it from Batch history.", plasmid=label), "success")
    return redirect(url_for("plasmids"))


@app.route("/plasmids/<int:row_id>/duplicate", methods=["POST"])
@login_required
def duplicate_plasmid(row_id: int):
    """A copy to start a derivative from: same construct and sequence, the
    next number, owned by you, not in a box."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        copy = PlasmidRecord(
            plasmid_id=_next_plasmid_id(db_session), name=f"{p.name} (copy)"[:200],
            backbone=p.backbone, insert_seq=p.insert_seq, resistance=p.resistance,
            owner=g.user.username, location="", notes=p.notes,
            full_sequence=p.full_sequence, is_circular=p.is_circular, features_json=p.features_json,
            sequence_format=p.sequence_format, sequence_uploaded_at=p.sequence_uploaded_at,
        )
        db_session.add(copy)
        stamp_updated(copy)
        db_session.commit()
        flash(gettext("Duplicated plasmid #%(id)s as #%(copy)s.", id=p.plasmid_id, copy=copy.plasmid_id), "success")
    return redirect(url_for("plasmids"))


@app.route("/plasmids/bulk", methods=["POST"])
@login_required
def bulk_plasmids():
    """Batch actions on ticked plasmids: set owner or resistance, move to a
    box, delete. One audit batch, so /batches can undo it; plasmids the user
    may not edit are skipped and counted. Move takes `box_id` (blank takes
    them out of their boxes), or a box name in `value` from older forms."""
    action = request.form.get("action", "")
    value = (request.form.get("value") or "").strip()
    ids = [int(i) for i in request.form.getlist("selected_ids") if i.isdigit()]
    with SessionLocal() as db_session:
        records = db_session.scalars(
            select(PlasmidRecord).where(PlasmidRecord.id.in_(ids)).order_by(PlasmidRecord.plasmid_id)
        ).all() if ids else []
        if not records:
            flash(gettext("Tick the plasmids to change first."), "info")
            return redirect(url_for("plasmids"))
        # A column the lab added to Plasmids is set like any other.
        own = next((f for f in custom_fields.fields(db_session, "plasmids")
                    if f"attr_{f['key']}" == action), None)
        if own is not None:
            return bulk.set_custom_column(db_session, "plasmids", own, value, url_for("plasmids"),
                                       records, access.can_edit, "plasmids")
        if action not in ("owner", "resistance", "move", "delete", "shared"):
            flash(gettext("Unknown batch action."), "error")
            return redirect(url_for("plasmids"))
        target, target_name = None, ""
        if action == "move":
            if "box_id" in request.form:
                raw = (request.form.get("box_id") or "").strip()
                target = db_session.get(PlasmidBox, int(raw)) if raw.isdigit() else None
                if raw and target is None:
                    flash(gettext("That box no longer exists. Reload the page."), "error")
                    return redirect(url_for("plasmids"))
                target_name = target.name if target else ""
            else:
                target_name = value[:80]
                target = pbox.by_name(db_session, target_name)
                target_name = target.name if target else target_name
        # Whose it is, lab common or not, and deleting: the owner's; the rest:
        # anyone who may edit it.
        allowed = access.can_manage if action in ("owner", "shared", "delete") else access.can_edit
        share_group = project_groups.parse(value)[1] if action == "shared" else None
        if share_group is not None and not project_groups.may_share_with(share_group):
            flash(project_groups.refusal(share_group), "error")
            return redirect(url_for("plasmids"))
        members = stewardship.lab_members(db_session) if action == "owner" else set()
        refused = stewardship.batch_owner_refused(members, value) if action == "owner" else None
        if refused:
            flash(refused, "error")
            return redirect(url_for("plasmids"))
        common = (f"shared with {project_groups.name_of(share_group)}" if share_group
                  else "lab common" if value == "1" else "personal")
        editable = [p for p in records if allowed(p)]
        skipped = len(records) - len(editable)
        done, unplaced = 0, 0
        noun = lambda n: "plasmid" if n == 1 else "plasmids"  # noqa: E731
        descriptions = {
            "owner": f"set owner to {value or 'nobody'} on {len(editable)} {noun(len(editable))}",
            "resistance": f"set resistance to {value or 'none'} on {len(editable)} {noun(len(editable))}",
            "move": f"move {len(editable)} {noun(len(editable))} to {target_name or 'no box'}",
            "delete": f"delete {len(editable)} {noun(len(editable))}",
            "shared": f"make {len(editable)} {noun(len(editable))} {common}",
        }
        with audit.batch(db_session, "delete" if action == "delete" else "update",
                         descriptions[action], "plasmids") as batch_row:
            batch_row.record_count = len(editable)
            if action in ("owner", "resistance"):
                for p in editable:
                    if action == "owner":
                        stewardship.change_owner(db_session, p, value, "plasmid", f"#{p.plasmid_id}", members)
                    else:
                        p.resistance = value[:80]
                    stamp_updated(p)
                    done += 1
                message = (ngettext("Set owner on %(num)s plasmid.", "Set owner on %(num)s plasmids.", done)
                           if action == "owner" else
                           ngettext("Set resistance on %(num)s plasmid.", "Set resistance on %(num)s plasmids.", done))
            elif action == "shared":
                for p in editable:
                    project_groups.apply(p, value)
                    stamp_updated(p)
                    done += 1
                if share_group:
                    message = ngettext("Made %(num)s plasmid shared with %(group)s.",
                                       "Made %(num)s plasmids shared with %(group)s.", done,
                                       group=project_groups.name_of(share_group))
                elif value == "1":
                    message = ngettext("Made %(num)s plasmid lab common.", "Made %(num)s plasmids lab common.", done)
                else:
                    message = ngettext("Made %(num)s plasmid personal.", "Made %(num)s plasmids personal.", done)
            elif action == "move":
                box = target
                if box is None and target_name:
                    box = pbox.box_for_name(db_session, target_name, g.user.username)
                moving = [p for p in editable if not (box is not None and p.box_id_fk == box.id and pbox.is_stored(p))]
                free = (pbox.free_cells(db_session, box, len(moving),
                                        taken=pbox.occupied(db_session, box.id, [p.id for p in moving]))
                        if box is not None else [])
                for p in moving:
                    pbox.put_in(p, box)
                    if box is not None and free:
                        p.box_row, p.box_col = free.pop(0)
                    else:
                        p.box_row = p.box_col = None
                        unplaced += 1 if box is not None else 0
                    stamp_updated(p)
                done = len(editable)
                message = (ngettext("Moved %(num)s plasmid to %(box)s.", "Moved %(num)s plasmids to %(box)s.", done,
                                    box=box.name) if box is not None
                           else ngettext("Took %(num)s plasmid out of their boxes.",
                                         "Took %(num)s plasmids out of their boxes.", done))
                if unplaced:
                    message += " " + gettext("%(n)s did not fit and wait in %(box)s without a position.", n=unplaced,
                                             box=box.name)
            else:
                for p in editable:
                    db_session.delete(p)
                    done += 1
                message = ngettext("Deleted %(num)s plasmid. Undo it from Batch history.",
                                   "Deleted %(num)s plasmids. Undo it from Batch history.", done)
        db_session.commit()
    if skipped:
        message += " " + gettext("%(n)s belong to someone else and were left alone.", n=skipped)
    flash(message, "success" if done else "warning")
    return redirect(url_for("plasmids"))


# ---- Boxes ------------------------------------------------------------------


def _plasmids_page(box_id: int | None = None):
    return redirect(url_for("plasmids", box=box_id) if box_id else url_for("plasmids"))


@app.route("/plasmids/boxes/save", methods=["POST"])
@login_required
def save_plasmid_box():
    """New box (anyone), or change one: its creator or an admin. A layout
    that renames the places of plasmids in it, or leaves some out, says
    whether each keeps its name or its cell (app/rack_layout.py); a rename
    follows onto its plasmids."""
    form = request.form

    def number(name, default, top):
        raw = (form.get(name) or "").strip()
        return max(1, min(top, int(raw))) if raw.isdigit() else default

    with SessionLocal() as db_session:
        raw_id = (form.get("id") or "").strip()
        box = db_session.get(PlasmidBox, int(raw_id)) if raw_id.isdigit() else None
        if raw_id and box is None:
            flash(gettext("That box no longer exists."), "error")
            return _plasmids_page()
        if box is not None and not access.can_edit_rack(box):
            flash(pbox.denied_box(box), "error")
            return _plasmids_page(box.id)
        name = (form.get("name") or "").strip()[:80]
        if not name:
            flash(gettext("Give the box a name."), "error")
            return _plasmids_page(box.id if box else None)
        clash = pbox.by_name(db_session, name)
        if clash is not None and (box is None or clash.id != box.id):
            flash(gettext("There is already a box called %(box)s.", box=clash.name), "error")
            return _plasmids_page(clash.id)
        rows = number("rows", box.rows if box else pbox.DEFAULT_ROWS, pbox.MAX_ROWS)
        cols = number("cols", box.cols if box else pbox.DEFAULT_COLS, pbox.MAX_COLS)
        # A new layout that renames the places of plasmids in the box, or leaves
        # some out, says whether each keeps its name or its cell (app/rack_layout.py).
        change, refused = rack_layout.check(db_session, rack_layout.kind("plasmid_box"), box,
                                            rack_layout.Layout(rows, cols, rack_layout.form_naming(form, box)),
                                            form.get(rack_layout.FIELD))
        if refused:
            flash(refused, "error")
            return _plasmids_page(box.id)
        created = box is None
        if created:
            box = PlasmidBox(created_by=g.user.username)
            db_session.add(box)
        renamed = not created and box.name != name
        with rack_layout.batch(db_session, change):
            box.name, box.rows, box.cols = name, rows, cols
            box.naming = json.dumps(rack_layout.form_naming(form, box))
            box.location = (form.get("location") or "").strip()[:120]
            if "notes" in form:
                box.notes = (form.get("notes") or "").strip()
            db_session.flush()
            if renamed:
                for p in db_session.scalars(select(PlasmidRecord).where(PlasmidRecord.box_id_fk == box.id)):
                    p.storage_box = name
            rack_layout.apply(db_session, change)
        db_session.commit()
        flash(gettext("Added box %(box)s (%(rows)s × %(cols)s, positions %(span)s).", box=box.name, rows=rows,
                      cols=cols, span=pbox.span(box)) if created else
              gettext("Saved box %(box)s (%(rows)s × %(cols)s, positions %(span)s).", box=box.name, rows=rows,
                      cols=cols, span=pbox.span(box)) + rack_layout.outcome(change), "success")
        return _plasmids_page(box.id)


@app.route("/plasmids/boxes/<int:box_id>/delete", methods=["POST"])
@login_required
def delete_plasmid_box(box_id: int):
    """Delete a box: its plasmids stay, no longer in a box. One batch, so
    Batch history can bring the box back with its plasmids in place."""
    with SessionLocal() as db_session:
        box = db_session.get(PlasmidBox, box_id)
        if box is None:
            flash(gettext("That box no longer exists."), "error")
            return _plasmids_page()
        if not access.can_edit_rack(box):
            flash(pbox.denied_box(box), "error")
            return _plasmids_page(box.id)
        name = box.name
        with audit.batch(db_session, "mixed", f"delete plasmid box {name}", "plasmid_boxes") as batch_row:
            members = db_session.scalars(select(PlasmidRecord).where(PlasmidRecord.box_id_fk == box.id)).all()
            batch_row.record_count = len(members) + 1
            for p in members:
                pbox.put_in(p, None)
                stamp_updated(p)
            db_session.flush()
            db_session.delete(box)
        db_session.commit()
    n = len(members)
    if n:
        flash(ngettext("Deleted box %(box)s. Its %(num)s plasmid is no longer in a box. Undo it from Batch history.",
                       "Deleted box %(box)s. Its %(num)s plasmids are no longer in a box. Undo it from Batch history.",
                       n, box=name), "success")
    else:
        flash(gettext("Deleted box %(box)s. Undo it from Batch history.", box=name), "success")
    return _plasmids_page()


def plasmid_page_url(row_id: int) -> str:
    """The page of the plasmid with this row: /plasmid/<its number>, the
    number the list, the labels and the API use."""
    with SessionLocal() as db_session:
        number = db_session.scalar(select(PlasmidRecord.plasmid_id).where(PlasmidRecord.id == row_id))
    return url_for("plasmid_page", number=number) if number is not None else url_for("plasmids")


@app.route("/plasmids/<int:row_id>")
@login_required
def plasmid_detail(row_id: int):
    """Addresses from before the page went by the plasmid's number (saved
    tabs, bookmarks, old notifications): on to /plasmid/<number>."""
    with SessionLocal() as db_session:
        number = db_session.scalar(select(PlasmidRecord.plasmid_id).where(PlasmidRecord.id == row_id))
    if number is None:
        flash(gettext("That plasmid isn't here any more. If it was deleted, More → Batch history can undo that."), "error")
        return redirect(url_for("plasmids"))
    return redirect(url_for("plasmid_page", number=number), code=301)


@app.route("/plasmid/<int:number>")
@login_required
def plasmid_page(number: int):
    with SessionLocal() as db_session:
        p = db_session.scalar(select(PlasmidRecord).where(PlasmidRecord.plasmid_id == number))
        if p is None:
            flash(gettext("There is no plasmid #%(number)s. If it was deleted, More → Batch history can undo that.",
                          number=number), "error")
            return redirect(url_for("plasmids"))
        try:
            features = json.loads(p.features_json) if p.features_json else []
        except json.JSONDecodeError:
            features = []
        box = _box_of(db_session, p)
        boxes = [{"id": b.id, "name": b.name, "location": b.location or ""} for b in pbox.all_boxes(db_session)]
        usernames = current_lab_usernames(db_session)
        data = {
            "row_id": p.id,
            "plasmid_id": p.plasmid_id,
            "name": p.name,
            "backbone": p.backbone,
            "insert_seq": p.insert_seq,
            "resistance": p.resistance,
            "owner": p.owner,
            "location": p.location,
            "concentration": p.concentration, "a260_280": p.a260_280,
            "is_shared": bool(p.is_shared), "share_value": project_groups.record_value(p),
            "share_group_id": p.share_group_id, "can_manage": access.can_manage(p),
            "notes": p.notes,
            "box_id": p.box_id_fk or "",
            "storage_box": box.name if box else "",
            "box_location": box.location if box else "",
            "box_span": pbox.span(box) if box else "",
            "position": pbox.label(p, box),
            "where": _plasmid_where(p, box),
            "stored": pbox.is_stored(p),
            "sequence": p.full_sequence or "",
            "is_circular": bool(p.is_circular),
            "features": _features_only(features),
            "sequence_format": p.sequence_format or "",
            "sequence_uploaded_at": (i18n.strftime(local_time(p.sequence_uploaded_at), "%b %d, %Y %H:%M")
                                     if p.sequence_uploaded_at else ""),
            "length_bp": len(p.full_sequence or ""),
            "updated_at": i18n.strftime(local_time(p.updated_at), "%b %d, %Y") if p.updated_at else "",
            "updated_by": p.updated_by or "",
            "locked": not access.can_edit(p),
            "denied": _plasmid_denied(p),
            "versions": [_version_row(v, i == 0) for i, v in enumerate(plasmid_versions.versions(db_session, p))],
            "parents": [{"id": e["link"].id, "label": e["label"], "role": _parent_role_label(e["role"]),
                         "method": _parent_method_label(e["method"]),
                         "number": e["plasmid"].plasmid_id if e["plasmid"] else None,
                         "url": url_for("plasmid_page", number=e["plasmid"].plasmid_id) if e["plasmid"] else ""}
                        for e in plasmid_lineage.parents(db_session, p)],
            "children": [{"label": c.name, "number": c.plasmid_id, "url": url_for("plasmid_page", number=c.plasmid_id)}
                         for c in plasmid_lineage.children(db_session, p)],
            "tree": _tree_rows(plasmid_lineage.tree(db_session, p)),
        }
        # Viruses (or anything with a plasmid column) made from it.
        from . import inventory_service as inventories
        made_from = [{
            "label": m["item"].name or f"#{m['item'].number}", "number": m["item"].number,
            "database": m["module"].label, "status": m["item"].status, "available": m["available"],
            "url": url_for("inventory.module", key=m["module"].key, open=m["item"].id),
        } for m in inventories.made_from_plasmid(db_session, p.plasmid_id)]
        stocks_db = inventories.first_of_kind(db_session, "glycerol_stocks")
        glycerol = {
            "rows": [{"label": m["item"].name or f"#{m['item'].number}", "number": m["item"].number,
                      "status": m["item"].status, "available": m["available"],
                      "where": inventories.where_label(m["item"]) or m["item"].location_note,
                      "url": url_for("inventory.module", key=m["module"].key, open=m["item"].id)}
                     for m in inventories.made_from_plasmid(db_session, p.plasmid_id, kinds=("glycerol_stocks",))],
            "database": stocks_db.label if stocks_db else "",
            "url": url_for("inventory.module", key=stocks_db.key) if stocks_db else "",
            "new_url": url_for("inventory.new_module", preset="glycerol_stocks"),
        }
        files = _plasmid_files(db_session, p)
        kept_reads = _kept_reads(db_session, p)
        primers = _plasmid_primers(db_session, p)
        from . import clone_routes
        clones = {"count": len(clone_routes.clones_of(db_session, p)), "verified": clone_routes.verified(db_session, p),
                  "urls": clone_routes.clone_urls(p.id)}
        lineage_options = [f"{n} · {name}" if name else str(n) for n, name in db_session.execute(
            select(PlasmidRecord.plasmid_id, PlasmidRecord.name).where(PlasmidRecord.id != p.id)
            .order_by(PlasmidRecord.plasmid_id)).all()]
    return render_template("plasmid_detail.html", plasmid=data, boxes=boxes, usernames=usernames,
                           made_from=made_from, lineage_options=lineage_options, primers=primers, files=files,
                           kept_reads=kept_reads, clones=clones,
                           plannotate_here=plannotate.available(),
                           glycerol=glycerol)


@app.route("/plasmids/<int:row_id>/upload-sequence", methods=["POST"])
@login_required
def plasmid_upload_sequence(row_id: int):
    """Accept a FASTA / GenBank / SnapGene .dna file (or pasted text) and
    parse it into the plasmid's sequence + features. Replaces any existing
    sequence."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        parsed, problem = _submitted_sequence("file")
        if problem:
            # Nothing was changed, so a warning (as on create), not an error.
            flash(gettext("%(problem)s The sequence was not changed.", problem=problem), "warning")
            return redirect(plasmid_page_url(row_id))
        if not parsed:
            flash(gettext("Choose a file or paste a sequence first."), "info")
            return redirect(plasmid_page_url(row_id))
        plasmid_versions.before_change(db_session, p)
        _apply_parsed_sequence(p, parsed)
        if parsed.get("name") and not p.name:
            p.name = parsed["name"]
        stamp_updated(p)
        plasmid_versions.record(db_session, p, "upload", g.user.username, parsed["format"])
        db_session.commit()
    flash(gettext("Loaded %(format)s · %(bp)s bp · %(features)s features.", format=parsed["format"].upper(),
                  bp=len(parsed["sequence"]), features=len(parsed["features"])), "success")
    if parsed.get("records", 1) > 1:
        flash(_several_records(parsed), "warning")
    return redirect(plasmid_page_url(row_id))


@app.route("/plasmids/<int:row_id>/parents", methods=["POST"])
@login_required
def plasmid_add_parent(row_id: int):
    """Say what a plasmid was made from: a plasmid in the lab (its number,
    #42, or its name) or one from outside it (anything else, e.g.
    "Addgene #11150"), in a role, by a method."""
    from . import inventory_service as inventories

    raw = (request.form.get("parent") or "").strip()
    role = request.form.get("role") or "other"
    method = request.form.get("method") or ""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        if not raw:
            flash(gettext("Name the plasmid it was made from: its number, its name, or where it came from."), "error")
            return redirect(plasmid_page_url(row_id) + "#made-from")
        parent = inventories.resolve_plasmid(db_session, raw)
        try:
            plasmid_lineage.add_parent(db_session, p, parent=parent, label="" if parent else raw, role=role,
                                       method=method, user=g.user.username)
        except ValueError as problem:
            flash(gettext("A plasmid can't be made from itself.") if str(problem) == "self" else
                  gettext("That would make a loop: #%(n)s was made from this plasmid.", n=parent.plasmid_id),
                  "error")
            return redirect(plasmid_page_url(row_id) + "#made-from")
        # The Backbone and Insert columns name it, when they are empty.
        name = parent.name if parent is not None else raw
        if role == "backbone" and not (p.backbone or "").strip():
            p.backbone = name[:200]
        elif role == "insert" and not (p.insert_seq or "").strip():
            p.insert_seq = name[:200]
        stamp_updated(p)
        db_session.commit()
        shown = f"#{parent.plasmid_id} {parent.name}" if parent is not None else raw
    flash(gettext("Made from %(parent)s.", parent=shown), "success")
    return redirect(plasmid_page_url(row_id) + "#made-from")


@app.route("/plasmids/<int:row_id>/parents/<int:link_id>/remove", methods=["POST"])
@login_required
def plasmid_remove_parent(row_id: int, link_id: int):
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        if plasmid_lineage.remove_link(db_session, p, link_id):
            stamp_updated(p)
            db_session.commit()
            flash(gettext("Removed it from Made from."), "success")
    return redirect(plasmid_page_url(row_id) + "#made-from")


@app.route("/plasmids/<int:row_id>/primers.csv")
@login_required
def plasmid_primers_csv(row_id: int):
    """An order sheet (Name, Sequence, Scale, Purification) for the plasmid's
    primers, or the ones ticked (?ids=3,7)."""
    wanted = {int(x) for x in (request.args.get("ids") or "").split(",") if x.strip().isdigit()}
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            abort(404)
        items = [i for i in primer_records.primers_for(db_session, p) if not wanted or i.id in wanted]
        body = primer_records.order_sheet(items)
        safe = re.sub(r"[^\w\-.]+", "_", p.name or f"plasmid-{p.plasmid_id}").strip("_")
    return Response(body, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{safe}-primers.csv"'})


@app.route("/plasmids/<int:row_id>/primer-sites")
@login_required
def plasmid_primer_sites(row_id: int):
    """Find saved primers (the Primers card's switch): JSON of every saved
    primer that binds this plasmid (primer_sites). ?all=1 includes the ones
    the plasmid's Primers card lists, for a page without it."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            return jsonify({"ok": False, "error": gettext("That plasmid no longer exists.")}), 404
        return jsonify(primer_sites(db_session, p, with_listed=request.args.get("all") == "1"))


@app.route("/plasmids/<int:row_id>/download.<fmt>")
@login_required
def plasmid_download(row_id: int, fmt: str):
    """The plasmid's sequence as a GenBank (.gb) or FASTA (.fa) file, with
    every feature and qualifier it keeps (GenBank)."""
    from .sequence_parser import to_fasta, to_genbank

    if fmt not in ("gb", "fa"):
        abort(404)
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None or not p.full_sequence:
            abort(404)
        name = p.name or f"plasmid-{p.plasmid_id}"
        try:
            annotations = json.loads(p.features_json) if p.features_json else []
        except json.JSONDecodeError:
            annotations = []
        if fmt == "gb":
            body = to_genbank(name, p.full_sequence, bool(p.is_circular), annotations)
        else:
            body = to_fasta(name, p.full_sequence, f"plasmid #{p.plasmid_id}")
    safe = re.sub(r"[^\w\-.]+", "_", name).strip("_") or f"plasmid-{row_id}"
    return Response(body, mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{safe}.{fmt}"'})


@app.route("/plasmids/<int:row_id>/versions/<int:version_id>/restore", methods=["POST"])
@login_required
def plasmid_restore_version(row_id: int, version_id: int):
    """Put an earlier version of the sequence back (it becomes the newest
    version; nothing in between is lost)."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        version = db_session.get(PlasmidSequenceVersion, version_id)
        if version is None or version.plasmid_row_id != p.id:
            flash(gettext("That version no longer exists."), "error")
            return redirect(plasmid_page_url(row_id))
        plasmid_versions.restore(db_session, p, version, g.user.username)
        stamp_updated(p)
        db_session.commit()
        when = fmt_day(local_time(version.saved_at), with_time=True)
        bp = len(p.full_sequence or "")
    flash(gettext("Restored the version of %(when)s · %(bp)s bp.", when=when, bp=bp), "success")
    return redirect(plasmid_page_url(row_id))


FEATURE_CATEGORY_LABELS = {
    "viral": "Viral elements", "promoter": "Promoters and enhancers", "coding": "Coding sequences",
    "selection": "Selection markers", "origin": "Origins", "terminator": "PolyA signals and terminators",
    "other": "Other elements",
}


# What a file kept with a plasmid is, from its name: a sequencing read, a
# picture (a gel, a plate), or anything else.
TRACE_SUFFIXES = (".ab1", ".abi", ".scf", ".ztr", ".phd", ".seq")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp", ".heic")


def _file_kind(filename: str) -> str:
    lowered = (filename or "").lower()
    if lowered.endswith(TRACE_SUFFIXES):
        return "trace"
    if lowered.endswith(IMAGE_SUFFIXES):
        return "image"
    return "document"


def _file_kind_label(kind: str) -> str:
    return {"trace": gettext("Sequencing read"), "image": gettext("Photo")}.get(kind, gettext("File"))


def _plasmid_files(db_session, p) -> list[dict]:
    """The files kept with this plasmid, newest first."""
    rows = db_session.scalars(select(PlasmidFile).where(PlasmidFile.plasmid_row_id == p.id)
                              .order_by(PlasmidFile.uploaded_at.desc(), PlasmidFile.id.desc()))
    out = []
    for f in rows:
        size = f.size_bytes or 0
        out.append({"id": f.id, "name": f.name, "kind": f.kind, "kind_label": _file_kind_label(f.kind),
                    "url": url_for("static", filename=f.path), "notes": f.notes,
                    "size": gettext("%(n)s MB", n=f"{size / 1048576:.1f}") if size >= 1048576
                            else gettext("%(n)s KB", n=max(size // 1024, 1)),
                    "when": fmt_day(local_time(f.uploaded_at), with_time=True), "who": f.uploaded_by,
                    "may_remove": g.user.role == "admin" or f.uploaded_by == g.user.username})
    return out


@app.route("/plasmids/<int:row_id>/files", methods=["POST"])
@login_required
def plasmid_add_files(row_id: int):
    """Keep files with a plasmid: sequencing traces, a gel photo, a vendor's
    map. Anyone who may edit the plasmid may add them."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        note = (request.form.get("notes") or "").strip()[:300]
        kept = []
        for upload in request.files.getlist("files"):
            saved = save_uploaded_file(upload)
            if saved is None:
                continue
            db_session.add(PlasmidFile(plasmid_row_id=p.id, kind=_file_kind(saved["original_name"]),
                                       name=saved["original_name"][:200], path=saved["path"],
                                       size_bytes=saved["size_bytes"], notes=note,
                                       uploaded_by=g.user.username))
            kept.append(saved["original_name"])
        if not kept:
            flash(gettext("Choose a file to keep with this plasmid first."), "info")
            return redirect(plasmid_page_url(row_id))
        stamp_updated(p)
        db_session.commit()
    flash(ngettext("Kept %(num)s file with this plasmid: %(names)s.",
                   "Kept %(num)s files with this plasmid: %(names)s.", len(kept), names=", ".join(kept)), "success")
    return redirect(plasmid_page_url(row_id) + "#plasmid-files")


@app.route("/plasmids/<int:row_id>/files/<int:file_id>/remove", methods=["POST"])
@login_required
def plasmid_remove_file(row_id: int, file_id: int):
    """Take a file off a plasmid (whoever added it, or an admin). The file
    itself stays in the uploads folder, as the notebook's do."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        kept = db_session.get(PlasmidFile, file_id)
        if p is None or kept is None or kept.plasmid_row_id != p.id:
            flash(gettext("That file is no longer here."), "info")
            return redirect(plasmid_page_url(row_id))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        if g.user.role != "admin" and kept.uploaded_by != g.user.username:
            flash(gettext("Only an admin or whoever added %(name)s can take it off.", name=kept.name), "error")
            return redirect(plasmid_page_url(row_id) + "#plasmid-files")
        name = kept.name
        db_session.delete(kept)
        db_session.commit()
    flash(gettext("Took %(name)s off this plasmid.", name=name), "success")
    return redirect(plasmid_page_url(row_id) + "#plasmid-files")


def _kept_reads(db_session, p) -> list[dict]:
    """The files kept with a plasmid that Verify sequencing can read again."""
    from . import seqverify
    rows = db_session.scalars(select(PlasmidFile).where(PlasmidFile.plasmid_row_id == p.id)
                              .order_by(PlasmidFile.uploaded_at.desc(), PlasmidFile.id.desc()))
    return [{"id": f.id, "name": f.name} for f in rows if (f.name or "").lower().endswith(seqverify.READ_SUFFIXES)]


@app.route("/plasmids/<int:row_id>/verify", methods=["POST"])
@login_required
def plasmid_verify(row_id: int):
    """Verify sequencing: Sanger reads (chosen files, pasted text, or reads
    kept with the plasmid) checked against its map (app/seqverify.py), as
    JSON for the page to draw. With keep, the chosen files are kept with
    the plasmid, each noted with its read's verdict."""
    from io import BytesIO
    from pathlib import Path as _Path

    from werkzeug.datastructures import FileStorage

    from . import seqverify
    from . import services as _services

    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            return jsonify({"ok": False, "error": gettext("That plasmid no longer exists.")}), 404
        if not p.full_sequence:
            return jsonify({"ok": False, "error": gettext("This plasmid has no sequence to check reads against.")}), 400
        uploads = [u for u in request.files.getlist("reads") if u and u.filename]
        files = [(u.filename, u.stream.read(seqverify.MAX_FILE + 1)) for u in uploads]
        wanted = {int(x) for x in request.form.getlist("kept") if x.isdigit()}
        for kept in db_session.scalars(select(PlasmidFile).where(PlasmidFile.plasmid_row_id == p.id,
                                                                 PlasmidFile.id.in_(wanted or {0}))):
            source = _services.UPLOAD_DIR / _Path(kept.path).name
            if source.is_file():
                files.append((kept.name, source.read_bytes()[:seqverify.MAX_FILE + 1]))
        text = request.form.get("reads_text") or ""
        reads, problems = seqverify.reads_from_uploads(files, text)
        if not reads:
            return jsonify({"ok": False, "error": " ".join(problems) or
                            gettext("Choose read files (.ab1, FASTA or text), or paste a read.")}), 400
        try:
            annotations = json.loads(p.features_json) if p.features_json else []
        except json.JSONDecodeError:
            annotations = []
        report = seqverify.verify(p.full_sequence, bool(p.is_circular), annotations, reads, name=p.name or "")
        kept_names = []
        if request.form.get("keep") and access.can_edit(p):
            # Keep what was chosen (and pasted) with the plasmid, each with what its reads showed.
            verdicts: dict = {}
            for r, checked in zip(reads, report["reads"]):
                verdicts.setdefault(r.extra.get("file"), []).append(checked["verdict"])
            day = fmt_day(local_time(datetime.utcnow()))
            sources = [(i, name, data) for i, (name, data) in enumerate(files[:len(uploads)])]
            if text.strip():
                sources.append((None, "pasted-reads.fa", text.encode("utf-8")))
            for index, name, data in sources:
                if index not in verdicts:
                    continue
                saved = save_uploaded_file(FileStorage(BytesIO(data), filename=name))
                if saved is None:
                    continue
                note = gettext("Verified against the map %(day)s: %(verdict)s", day=day,
                               verdict=" ".join(verdicts[index]))
                db_session.add(PlasmidFile(plasmid_row_id=p.id, kind=_file_kind(name), name=name[:200],
                                           path=saved["path"], size_bytes=saved["size_bytes"],
                                           notes=note[:300], uploaded_by=g.user.username))
                kept_names.append(name)
            if kept_names:
                stamp_updated(p)
                db_session.commit()
    return jsonify({"ok": True, "report": report, "problems": problems, "kept": kept_names})


@app.route("/plasmids/<int:row_id>/detect-features", methods=["POST"])
@login_required
def plasmid_detect_features(row_id: int):
    """Annotate the library's elements wherever they are in this sequence
    (app/feature_library.py). A version is kept first, so Restore undoes it."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        library = feature_library.entries(db_session)
        if not library:
            flash(gettext("The feature library is empty. Add a plasmid's features to it first."), "info")
            return redirect(plasmid_page_url(row_id))
        current = json.loads(p.features_json or "[]")
        found = feature_library.detect(library, p.full_sequence or "", bool(p.is_circular), current)
        if not found:
            flash(gettext("None of the library's %(n)s elements is in this sequence, or each is already marked.",
                          n=len(library)), "info")
            return redirect(plasmid_page_url(row_id))
        plasmid_versions.before_change(db_session, p)
        p.features_json = json.dumps(current + found)
        stamp_updated(p)
        names = ", ".join(dict.fromkeys(f["name"] for f in found))
        plasmid_versions.record(db_session, p, "detect", g.user.username, detail=names[:200])
        db_session.commit()
    flash(ngettext("Marked %(num)s feature from the library: %(names)s.",
                   "Marked %(num)s features from the library: %(names)s.", len(found), names=names), "success")
    return redirect(plasmid_page_url(row_id))


@app.route("/plasmids/<int:row_id>/annotate", methods=["POST"])
@login_required
def plasmid_annotate(row_id: int):
    """Mark what pLannotate finds (app/plannotate.py), which unlike the
    library's exact matching also finds an element a few bases off. A
    version is kept first, so Restore undoes it."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        if not plannotate.available():
            flash(gettext("pLannotate is not set up on this server."), "info")
            return redirect(plasmid_page_url(row_id))
        current = json.loads(p.features_json or "[]")
        try:
            found = plannotate.find(p.full_sequence or "", bool(p.is_circular), current)
        except plannotate.ToolFailed as problem:
            flash(gettext("pLannotate could not annotate this plasmid: %(problem)s.", problem=problem), "error")
            return redirect(plasmid_page_url(row_id))
        if not found:
            flash(gettext("pLannotate found nothing this map does not already mark."), "info")
            return redirect(plasmid_page_url(row_id))
        plasmid_versions.before_change(db_session, p)
        p.features_json = json.dumps(current + found)
        stamp_updated(p)
        names = ", ".join(dict.fromkeys(f["name"] for f in found))
        plasmid_versions.record(db_session, p, "annotate", g.user.username, detail=names[:200])
        db_session.commit()
    flash(ngettext("pLannotate marked %(num)s feature: %(names)s.",
                   "pLannotate marked %(num)s features: %(names)s.", len(found), names=names), "success")
    return redirect(plasmid_page_url(row_id))


@app.route("/plasmids/<int:row_id>/features-to-library", methods=["POST"])
@login_required
def plasmid_features_to_library(row_id: int):
    """This plasmid's named features into the lab's feature library."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        made = feature_library.add_from_plasmid(db_session, p, g.user.username)
        db_session.commit()
    if made:
        flash(ngettext("Added %(num)s element to the feature library.",
                       "Added %(num)s elements to the feature library.", made), "success")
    else:
        flash(gettext("The feature library already has every named feature of this plasmid."), "info")
    return redirect(plasmid_page_url(row_id))


@app.route("/plasmids/features")
@login_required
def feature_library_page():
    """The lab's feature library, by kind of element."""
    category = request.args.get("category", "")
    if category not in feature_library.CATEGORIES:
        category = ""
    # With the pack in, a lab has a couple of thousand elements: show a
    # shelf-full at a time and let a search narrow it, rather than sending
    # every one of them to a phone.
    look_for = (request.args.get("q") or "").strip()[:80]
    SHOWN = 100
    with SessionLocal() as db_session:
        everything = feature_library.entries(db_session)
        sources = {p.id: p for p in db_session.scalars(select(PlasmidRecord).where(
            PlasmidRecord.id.in_({e.source_row_id for e in everything if e.source_row_id})))}
        if look_for:
            wanted = look_for.lower()
            everything = [e for e in everything
                          if wanted in e.name.lower() or wanted in e.type.lower()
                          or wanted in e.sequence.lower()]
        groups = []
        for key in feature_library.CATEGORIES:
            rows = [{"id": e.id, "name": e.name, "type": e.type, "length": len(e.sequence), "color": e.color,
                     "sequence": e.sequence[:72], "more": max(len(e.sequence) - 72, 0),
                     "by": e.created_by, "from_pack": e.source_name,
                     "may_delete": g.user.role == "admin" or e.created_by == g.user.username,
                     "source": ({"number": sources[e.source_row_id].plasmid_id, "name": sources[e.source_row_id].name,
                                 "url": url_for("plasmid_page", number=sources[e.source_row_id].plasmid_id)}
                                if e.source_row_id in sources else None)}
                    for e in everything if e.category == key]
            if rows:
                groups.append({"key": key, "label": gettext(FEATURE_CATEGORY_LABELS[key]),
                               "rows": rows[:SHOWN], "all": len(rows)})
        state = feature_pack.status(db_session)
    counts = {g_["key"]: g_["all"] for g_ in groups}
    shown = [g_ for g_ in groups if not category or g_["key"] == category]
    pack = {"name": feature_pack.SOURCE_NAME, "citation": feature_pack.CITATION, "licence": feature_pack.LICENCE,
            "url": feature_pack.ARTICLE_URL, "state": state["state"], "error": state["error"],
            "here": sum(1 for e in everything if e.source_name == feature_pack.SOURCE_NAME)}
    return render_template("feature_library.html", groups=shown, counts=counts, category=category, pack=pack,
                           total=len(everything), look_for=look_for, shown=SHOWN,
                           labels={k: gettext(v) for k, v in FEATURE_CATEGORY_LABELS.items()})


def _names(files: list[str]) -> str:
    """File names in a sentence, as inventory_routes._and words them."""
    from .inventory_routes import _and
    return _and(files) if files else ""


@app.route("/plasmids/features/pack", methods=["POST"])
@admin_required
def feature_library_pack():
    """Fetch the common-features pack (app/feature_pack.py) and put it in the
    lab's library. The file is downloaded from the open archive when asked
    for; the app carries no copy of it."""
    with SessionLocal() as db_session:
        started = feature_pack.start(db_session, g.user.username)
    if started:
        flash(gettext("Downloading the common-features pack. It takes about a minute; this page shows it when it is in."),
              "info")
    else:
        flash(gettext("The pack is already being downloaded."), "info")
    return redirect(url_for("feature_library_page"))


@app.route("/plasmids/features/import", methods=["POST"])
@login_required
def feature_library_import():
    """Fill the library straight from annotated files — the GenBank a
    repository gives you, a SnapGene .dna, a vendor's map — without making a
    plasmid for each. Several files at once; each is reported by name."""
    from .sequence_parser import parse_sequence_bytes

    uploads = [f for f in request.files.getlist("files") if f and f.filename]
    if not uploads:
        flash(gettext("Choose the annotated files to read the elements from."), "info")
        return redirect(url_for("feature_library_page"))
    made, read, unreadable, nothing = 0, 0, [], []
    with SessionLocal() as db_session:
        for upload in uploads:
            parsed = parse_sequence_bytes(upload.read(), upload.filename)
            if not parsed or not parsed.get("sequence"):
                unreadable.append(upload.filename)
                continue
            read += 1
            added = feature_library.add_from_sequence(db_session, parsed["sequence"], parsed.get("features") or [],
                                                      g.user.username)
            if not added:
                nothing.append(upload.filename)
            made += added
        db_session.commit()
    if made:
        # Two counts, each pluralised on its own, rather than one sentence
        # whose "1 files" follows the other number.
        flash(ngettext("Added %(num)s element to the feature library.",
                       "Added %(num)s elements to the feature library.", made) + " "
              + ngettext("Read from %(num)s file.", "Read from %(num)s files.", read), "success")
    elif read:
        flash(gettext("The library already has every named feature in %(files)s.",
                      files=_names(nothing)), "info")
    if unreadable:
        flash(gettext("%(files)s: not a sequence file this app can read (SnapGene .dna, GenBank or FASTA).",
                      files=_names(unreadable)), "warning")
    return redirect(url_for("feature_library_page"))


@app.route("/plasmids/features/collect", methods=["POST"])
@admin_required
def feature_library_collect():
    """Every plasmid's named features into the library."""
    with SessionLocal() as db_session:
        made = feature_library.collect(db_session, g.user.username)
        db_session.commit()
    flash(ngettext("Added %(num)s element to the feature library.",
                   "Added %(num)s elements to the feature library.", made), "success" if made else "info")
    return redirect(url_for("feature_library_page"))


@app.route("/plasmids/features/<int:entry_id>/delete", methods=["POST"])
@login_required
def feature_library_delete(entry_id: int):
    """Take an element out of the library (its admin or whoever added it).
    Maps it was marked on keep their features."""
    from .models import FeatureLibraryEntry
    with SessionLocal() as db_session:
        entry = db_session.get(FeatureLibraryEntry, entry_id)
        if entry is None:
            flash(gettext("That element is no longer in the library."), "info")
            return redirect(url_for("feature_library_page"))
        if g.user.role != "admin" and entry.created_by != g.user.username:
            flash(gettext("Only an admin or whoever added it can take %(name)s out of the library.", name=entry.name),
                  "error")
            return redirect(url_for("feature_library_page"))
        name = entry.name
        db_session.delete(entry)
        db_session.commit()
    flash(gettext("Took %(name)s out of the feature library.", name=name), "success")
    return redirect(url_for("feature_library_page"))


@app.route("/plasmids/<int:row_id>/clear-sequence", methods=["POST"])
@login_required
def plasmid_clear_sequence(row_id: int):
    """The one way to empty a sequence: an explicit, confirmed action. The
    editor's autosave refuses an empty sequence, so a stray select-all and
    delete cannot wipe a construct."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        if request.form.get("confirm") != "1":
            flash(gettext("Confirm clearing the sequence first."), "error")
            return redirect(plasmid_page_url(row_id))
        plasmid_versions.before_change(db_session, p)
        p.full_sequence = ""
        p.features_json = "[]"
        p.sequence_format = ""
        p.sequence_uploaded_at = None
        stamp_updated(p)
        plasmid_versions.record(db_session, p, "clear", g.user.username)
        db_session.commit()
        flash(gettext("Cleared the sequence of plasmid #%(id)s. Its audit history keeps the old one.", id=p.plasmid_id),
              "success")
    return redirect(plasmid_page_url(row_id))


@app.route("/plasmids/<int:row_id>/move", methods=["POST"])
@login_required
def plasmid_move_in_box(row_id: int):
    """The box grid's drag and drop (static/rack-grid.js): `box_id` with a
    0-based `box_row` / `box_col`. An occupied cell swaps, when you may
    move both plasmids; a blank box_id and cell (the Unplaced tray) keeps
    the plasmid in its box without a cell. Older callers send a box name
    as `storage_box` instead (blank: out of any box)."""
    form = request.form
    row_raw = (form.get("box_row") or "").strip()
    col_raw = (form.get("box_col") or "").strip()
    new_row, new_col = _plasmid_int(row_raw), _plasmid_int(col_raw)
    if (row_raw or col_raw) and (new_row is None or new_col is None):
        return jsonify({"ok": False, "error": gettext("A box position needs both a row and a column.")}), 400

    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            return jsonify({"ok": False, "error": gettext("That plasmid no longer exists.")}), 404
        if not access.can_edit(p):
            return jsonify({"ok": False, "error": _plasmid_denied(p)}), 403
        current = _box_of(db_session, p)
        if "box_id" in form and not (form.get("box_id") or "").strip():
            box, problem = current, None
            new_row = new_col = None
        else:
            _given, box, problem = pbox.box_from_form(db_session, form, g.user.username, current)
        if problem:
            db_session.rollback()
            return jsonify({"ok": False, "error": problem}), 404
        problem, holder = pbox.place(db_session, p, box, new_row, new_col, swap=True)
        if problem:
            db_session.rollback()
            return jsonify({"ok": False, "error": problem}), 409
        stamp_updated(p)
        if holder is not None:
            stamp_updated(holder)
        db_session.commit()
        # Where each plasmid that moved now sits, so the page can update the
        # grid and the sheet rows (and their _was guards) without a reload.
        moved = []
        for q in (p, holder):
            if q is None:
                continue
            qbox = _box_of(db_session, q)
            moved.append({"id": q.id, "box_id": q.box_id_fk or "", "storage_box": qbox.name if qbox else "",
                          "box_row": q.box_row if q.box_row is not None else -1,
                          "box_col": q.box_col if q.box_col is not None else -1,
                          "position": pbox.label(q, qbox), "stored": pbox.is_stored(q)})
        return jsonify({"ok": True, "moved": moved})


from .sequence_parser import NOTE_LIMIT, QUALIFIER_LIMIT  # noqa: E402

# What the plasmid editor annotates, as stored in features_json. Each entry
# carries its "kind"; an entry without one is a feature (how every stored
# annotation looked before primers, translations and parts were kept).
ANNOTATION_KINDS = {"features": "feature", "primers": "primer", "translations": "translation", "parts": "part"}


def _annotation_list(raw) -> list:
    """The editor sends each annotation group as an object keyed by id;
    older callers and GenBank uploads send a list. Either way, a list."""
    if isinstance(raw, dict):
        return list(raw.values())
    return raw if isinstance(raw, list) else []


def _clean_features(raw_features, length: int, kind: str = "feature") -> list[dict]:
    """Stored annotations from the editor's, dropping any outside the
    sequence. An annotation crossing the origin keeps start > end; a joined
    feature keeps its pieces in `locations`."""
    translated = []
    for f in _annotation_list(raw_features):
        if not isinstance(f, dict):
            continue
        # Find saved primers draws them on the map to look at; they stay in Primers.
        if str(f.get("id") or "").startswith(FOUND_PRIMER_ID):
            continue
        try:
            start = int(f.get("start", 0) or 0)
            end = int(f.get("end", 0) or 0)
        except (TypeError, ValueError):
            continue
        if not (0 <= start < length and 0 <= end < length):
            continue
        if "forward" in f:
            direction = 1 if f.get("forward") else -1
        else:
            try:
                direction = 1 if int(f.get("strand", f.get("direction", 1)) or 0) >= 0 else -1
            except (TypeError, ValueError):
                direction = 1
        notes = f.get("notes")
        if isinstance(notes, dict):  # the editor's {key: [values]}, every qualifier an import kept
            notes = {str(k)[:60]: [str(v)[:NOTE_LIMIT] for v in (vals if isinstance(vals, list) else [vals])][:20]
                     for k, vals in list(notes.items())[:QUALIFIER_LIMIT]}
        elif isinstance(notes, str):
            notes = notes[:NOTE_LIMIT]
        else:
            notes = ""
        item_kind = f.get("kind") if f.get("kind") in ANNOTATION_KINDS.values() else kind
        # The editor lists a translation for every CDS feature (and ORF) as
        # well; those follow their feature, so only translations a person
        # made are kept.
        if item_kind == "translation" and f.get("translationType") not in (None, "", "User Created"):
            continue
        entry = {
            "name": str(f.get("name") or "")[:120],
            "type": str(f.get("type") or ("primer_bind" if item_kind == "primer" else "misc_feature"))[:40],
            "start": start,
            "end": end,
            "direction": direction,
            "color": str(f.get("color") or "#cbd5e1")[:20],
            "notes": notes,
        }
        if item_kind != "feature":
            entry["kind"] = item_kind
        locations = []
        for loc in f.get("locations") or []:
            try:
                ls, le = int(loc.get("start")), int(loc.get("end"))
            except (AttributeError, TypeError, ValueError):
                continue
            if 0 <= ls < length and 0 <= le < length:
                locations.append({"start": ls, "end": le})
        if len(locations) > 1:
            entry["locations"] = locations
        if item_kind == "primer" and isinstance(f.get("bases"), str) and f["bases"].strip():
            entry["bases"] = re.sub(r"[^A-Za-z]", "", f["bases"])[:500]
        if item_kind == "primer" and str(f.get("inventory_id") or "").isdigit():
            entry["inventory_id"] = int(f["inventory_id"])
        if item_kind == "translation" and isinstance(f.get("translationType"), str):
            entry["translationType"] = f["translationType"][:40]
        translated.append(entry)
    return translated


def _clean_annotations(sequence_data: dict, length: int) -> list[dict]:
    """Every annotation group the editor keeps, cleaned, in one list."""
    out = []
    for group, kind in ANNOTATION_KINDS.items():
        out.extend(_clean_features(sequence_data.get(group), length, kind))
    return out


def _editor_annotations(stored: list) -> dict[str, list]:
    """Stored annotations in the editor's shape, by group: forward and
    strand for direction, and a stable id so the editor can diff renders."""
    groups = {group: [] for group in ANNOTATION_KINDS}
    for i, f in enumerate(stored if isinstance(stored, list) else []):
        if not isinstance(f, dict):
            continue
        kind = f.get("kind") or "feature"
        group = next((g for g, k in ANNOTATION_KINDS.items() if k == kind), "features")
        forward = int(f.get("direction", 1) or 1) >= 0
        item = {
            "id": f.get("id") or f"{kind}-{i}",
            "name": f.get("name") or "",
            "type": f.get("type") or "misc_feature",
            "start": int(f.get("start", 0) or 0),
            "end": int(f.get("end", 0) or 0),
            "forward": forward,
            "strand": 1 if forward else -1,
            "color": f.get("color") or "#cbd5e1",
            "notes": f.get("notes") or "",
        }
        for extra in ("locations", "bases", "translationType", "inventory_id"):
            if f.get(extra):
                item[extra] = f[extra]
        groups[group].append(item)
    return groups


def _mini_map(p) -> list:
    """A plasmid's features, as little as its small map in the List needs."""
    if not p.full_sequence or not p.features_json:
        return []
    try:
        stored = json.loads(p.features_json)
    except json.JSONDecodeError:
        return []
    return [{"start": f.get("start", 0), "end": f.get("end", 0), "type": f.get("type", ""), "name": f.get("name", ""),
             "direction": f.get("direction", 1)} for f in _features_only(stored)][:40]


def _features_only(stored: list) -> list:
    return [f for f in stored if isinstance(f, dict) and (f.get("kind") or "feature") == "feature"]


@app.route("/plasmids/<int:row_id>/edit-sequence", methods=["POST"])
@login_required
def plasmid_edit_sequence(row_id: int):
    """Save a hand-edited raw sequence. Strips whitespace/digits and FASTA
    headers; keeps the IUPAC letters. Features that no longer fit in the
    new length are dropped. An empty result is refused: clearing a
    sequence is its own confirmed action."""
    from .sequence_parser import clean_bases

    raw = request.form.get("sequence_text", "")
    # Drop FASTA header lines before flattening.
    lines = [ln for ln in raw.splitlines() if not ln.lstrip().startswith(">")]
    cleaned = clean_bases("\n".join(lines))

    is_circular_raw = (request.form.get("is_circular") or "").lower()
    new_circular = is_circular_raw in ("1", "true", "on", "yes")

    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            flash(gettext("That plasmid no longer exists."), "error")
            return redirect(url_for("plasmids"))
        if not access.can_edit(p):
            flash(_plasmid_denied(p), "error")
            return redirect(plasmid_page_url(row_id))
        if not cleaned:
            flash(gettext("That leaves no sequence, so nothing was saved. Use Clear sequence to empty it."), "error")
            return redirect(plasmid_page_url(row_id))
        plasmid_versions.before_change(db_session, p)
        p.full_sequence = cleaned
        p.is_circular = new_circular
        try:
            features = json.loads(p.features_json) if p.features_json else []
        except json.JSONDecodeError:
            features = []
        new_features = _clean_features(features, len(cleaned))
        if len(new_features) != len(features):
            flash(
                gettext("Dropped %(n)s feature(s) that fell beyond the new sequence length.",
                        n=len(features) - len(new_features)),
                "warning",
            )
        p.features_json = json.dumps(new_features)
        if not p.sequence_format:
            p.sequence_format = "manual"
        p.sequence_uploaded_at = datetime.utcnow()
        stamp_updated(p)
        plasmid_versions.record(db_session, p, "hand", g.user.username)
        db_session.commit()
    flash(gettext("Saved sequence · %(bp)s bp.", bp=len(cleaned)), "success")
    return redirect(plasmid_page_url(row_id))


@app.route("/plasmids/<int:row_id>/sequence-save", methods=["POST"])
@login_required
def plasmid_sequence_save_json(row_id: int):
    """A whole map saved as JSON, for scripts (the Sequence tab saves one
    edit at a time through sequence_routes' edit.json). The body is
    `sequenceData`: {sequence, circular, features, primers, translations,
    parts, name}, each annotation {id, name, start, end, type, color,
    forward or strand, notes, locations[]}, translated to our schema.

    An empty or malformed save is refused (400) rather than stored: the
    editor autosaves after every edit, and a blank body used to wipe the
    construct. Clearing a sequence is /clear-sequence."""
    from .sequence_parser import is_iupac

    payload = request.get_json(silent=True)
    sd = payload.get("sequenceData", payload) if isinstance(payload, dict) else None
    raw_sequence = sd.get("sequence") if isinstance(sd, dict) else None
    if not isinstance(raw_sequence, str) or not raw_sequence.strip():
        return jsonify({"ok": False, "error": gettext("Refusing to save an empty sequence. Use Clear sequence to empty it.")}), 400
    sequence = re.sub(r"\s+", "", raw_sequence).upper()
    if not is_iupac(sequence):
        return jsonify({"ok": False, "error": gettext("The sequence has characters that are not IUPAC nucleotide codes.")}), 400
    translated = _clean_annotations(sd, len(sequence))

    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            return jsonify({"ok": False, "error": gettext("That plasmid no longer exists.")}), 404
        if not access.can_edit(p):
            return jsonify({"ok": False, "error": _plasmid_denied(p)}), 403
        plasmid_versions.before_change(db_session, p)
        p.full_sequence = sequence
        p.is_circular = bool(sd.get("circular"))
        # Each primer drawn on the map gets its record in the Primers database.
        primers_added = primer_records.sync_from_map(db_session, p, translated, g.user.username)
        p.features_json = json.dumps(translated)
        if not p.sequence_format:
            p.sequence_format = "json"
        # File > Rename Sequence in the editor renames the plasmid.
        if isinstance(sd.get("name"), str) and sd["name"].strip() and sd["name"].strip() != (p.name or ""):
            p.name = sd["name"].strip()[:200]
        p.sequence_uploaded_at = datetime.utcnow()
        stamp_updated(p)
        plasmid_versions.record(db_session, p, "editor", g.user.username)
        db_session.commit()
    counts = {group: sum(1 for f in translated if (f.get("kind") or "feature") == kind)
              for group, kind in ANNOTATION_KINDS.items()}
    return jsonify({"ok": True, "length": len(sequence), "features": counts["features"], "counts": counts,
                    "primers_added": primers_added})


@app.route("/plasmids/<int:row_id>/sequence.json")
@login_required
def plasmid_sequence_json(row_id: int):
    """The plasmid's sequence and annotations as JSON, grouped as
    sequence-save takes them (for scripts)."""
    with SessionLocal() as db_session:
        p = db_session.get(PlasmidRecord, row_id)
        if p is None:
            return jsonify({"ok": False}), 404
        try:
            features = json.loads(p.features_json) if p.features_json else []
        except json.JSONDecodeError:
            features = []
        groups = _editor_annotations(features)
        return jsonify({
            "ok": True,
            "name": p.name or f"Plasmid #{p.plasmid_id}",
            "sequence": p.full_sequence or "",
            "is_circular": bool(p.is_circular),
            "circular": bool(p.is_circular),  # the key sequence-save takes
            "features": groups["features"],
            "primers": groups["primers"],
            "translations": groups["translations"],
            "parts": groups["parts"],
            "locked": not access.can_edit(p),
        })
