"""The plasmid page's Verify tab: clones checked by Sanger sequencing.

Reads dropped on the tab are grouped into clones by their file names
(`C4_CAG-F.ab1`, `clone3-T7.ab1`, `colony 2 rev.fa`), or all into the
clone named on the form, checked against the map (app/seqverify.py, where
the biology is), kept with the plasmid's files and remembered as a
`PlasmidClone` with the verdict. Opening a clone checks its kept reads
again, so its traces come back. **Accept** marks the clone as the plasmid;
the newest accepted clone is the one the page calls verified.

Someone who may only view the plasmid can still check reads: they see the
result, and nothing is kept.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path

from flask import Blueprint, abort, g, jsonify, request, url_for
from sqlalchemy import select
from werkzeug.datastructures import FileStorage

from . import access, seqverify
from .db import SessionLocal
from .i18n import gettext, ngettext
from .models import PlasmidClone, PlasmidFile, PlasmidRecord

bp = Blueprint("clones", __name__)

TRACE_FLANK = 20        # called bases either side of a difference in the tab's trace
INSERT_REACH = 400      # how far before or after a feature a primer may sit to read it

# A clone named in a read's file name: C4_…, clone3-…, colony 2 …, #5 …
_CLONE = re.compile(r"(?:^|[\s_.\-])(?:clone|colony|col|cl|c|#)\s*[-_ ]?(\d{1,3})(?=$|[\s_.\-])", re.IGNORECASE)


@bp.before_request
def require_login():
    if g.get("user") is None:
        abort(401)
    return None


def clone_of(filename: str) -> str:
    """The clone a read's file name names ("4" for C4_CAG-F.ab1), or ""."""
    stem = Path(filename or "").stem
    m = _CLONE.search(stem)
    return (m.group(1).lstrip("0") or "0") if m else ""


def label(name: str) -> str:
    return gettext("Clone %(name)s", name=name) if name.isdigit() else name


def _when(value) -> str:
    from .app import local_time
    from .days import fmt_day
    return fmt_day(local_time(value)) if value else ""


def _plasmid(session, row_id: int) -> PlasmidRecord:
    p = session.get(PlasmidRecord, row_id)
    if p is None:
        abort(404)
    return p


def _annotations(p: PlasmidRecord) -> list:
    try:
        return json.loads(p.features_json) if p.features_json else []
    except json.JSONDecodeError:
        return []


def _summary(report: dict) -> dict:
    """What the clone's panel says: identity, what the reads cover, the changes."""
    placed = [r for r in report.get("reads", []) if r.get("placed")]
    aligned = sum(r.get("aligned_bases", 0) for r in placed)
    identity = (sum(r["identity"] * r.get("aligned_bases", 0) for r in placed) / aligned) if aligned else None
    confident = [d for d in report.get("differences", []) if d.get("confident")]
    kinds = [((d.get("effect") or {}).get("kind") or "") for d in confident]
    depth = []
    for f in report.get("map", []):
        if f["name"] not in report.get("features_covered", []) or f.get("type", "").lower() not in ("cds", "gene"):
            continue
        s, e = f["ranges"][0][0], f["ranges"][-1][1]
        n = sum(1 for r in placed if r["start"] <= s and r["end"] >= e)
        depth.append({"name": f["name"], "reads": n})
    return {
        "identity": round(identity, 1) if identity is not None else None,
        "reads": len(report.get("reads", [])), "placed": len(placed),
        "covered_bp": report.get("covered_bp", 0), "length": report.get("plasmid", {}).get("length", 0),
        "features": depth,
        "changes": len(confident),
        "silent": sum(1 for k in kinds if k == "silent"),
        "coding": sum(1 for k in kinds if k and k != "silent"),
    }


def _verdict(report: dict) -> tuple[str, str]:
    """The clone's status and sentence: reads too poor to check say so,
    rather than that they don't match."""
    reads = report.get("reads", [])
    if report.get("status") == "no_match" and reads and all(r.get("status") == "poor" for r in reads):
        return "poor", reads[0].get("verdict", "")
    return report.get("status", ""), report.get("verdict", "")


def _row(c: PlasmidClone) -> dict:
    try:
        report = json.loads(c.report_json or "{}")
    except json.JSONDecodeError:
        report = {}
    summary = _summary(report) if report else {}
    if c.status == "match":
        detail = ngettext("%(num)s read, matches the map", "%(num)s reads, match the map", summary.get("reads", 0))
    elif c.status == "differences":
        detail = ngettext("%(num)s change from the map", "%(num)s changes from the map", summary.get("changes", 0))
        if summary.get("changes") and not summary.get("coding"):
            detail = ngettext("%(num)s change, silent", "%(num)s changes, all silent", summary["changes"])
    elif c.status == "poor":
        detail = gettext("Too poor to check")
    else:
        detail = gettext("Doesn't match this plasmid")
    return {"id": c.id, "name": c.name, "label": label(c.name), "status": c.status, "verdict": c.verdict,
            "detail": detail, "summary": summary, "accepted": bool(c.accepted_at),
            "when": _when(c.accepted_at or c.checked_at), "accepted_by": c.accepted_by, "checked_by": c.checked_by}


def clones_of(session, p: PlasmidRecord) -> list[PlasmidClone]:
    return list(session.scalars(select(PlasmidClone).where(PlasmidClone.plasmid_row_id == p.id)
                                .order_by(PlasmidClone.checked_at.desc(), PlasmidClone.id.desc())))


def verified(session, p: PlasmidRecord) -> dict | None:
    """The newest accepted clone, for the page's header: {label, when}."""
    c = session.scalar(select(PlasmidClone).where(PlasmidClone.plasmid_row_id == p.id,
                                                  PlasmidClone.accepted_at.is_not(None))
                       .order_by(PlasmidClone.accepted_at.desc()).limit(1))
    return {"label": label(c.name), "when": _when(c.accepted_at)} if c else None


def _next_name(session, p: PlasmidRecord, taken: set[str]) -> str:
    numbers = [int(c.name) for c in clones_of(session, p) if c.name.isdigit()] + [int(n) for n in taken if n.isdigit()]
    return str(max(numbers, default=0) + 1)


def sequence_next(session, p: PlasmidRecord) -> dict | None:
    """Primers the lab has that would read the plasmid's insert: the longest
    coding feature that isn't a resistance gene, a forward primer before it
    and a reverse one after it, each within INSERT_REACH."""
    from .app import primer_sites

    features = [f for f in _annotations(p) if isinstance(f, dict) and (f.get("kind") or "feature") == "feature"
                and str(f.get("type", "")).lower() in ("cds", "gene")]
    marker = re.compile(r"(amp|kan|neo|puro|hygro|blast|zeo|bla|cat|spec|gent)[a-z]*r?\b|resist", re.IGNORECASE)
    inserts = sorted((f for f in features if not marker.search(str(f.get("name", "")))),
                     key=lambda f: -(int(f.get("end", 0)) - int(f.get("start", 0))))
    if not inserts or not p.full_sequence:
        return None
    insert = inserts[0]
    start, end = int(insert.get("start", 0)) + 1, int(insert.get("end", 0)) + 1
    hits = primer_sites(session, p, with_listed=True).get("hits", [])
    before = [h for h in hits if h["strand"] > 0 and start - INSERT_REACH <= h["end"] < start]
    after = [h for h in hits if h["strand"] < 0 and end < h["start"] <= end + INSERT_REACH]
    if not before and not after:
        return None
    pick_f = max(before, key=lambda h: h["end"]) if before else None
    pick_r = min(after, key=lambda h: h["start"]) if after else None
    if pick_f and pick_r:
        text = gettext("%(forward)s and %(reverse)s read all of %(feature)s between them; both are in the lab's primers.",
                       forward=pick_f["name"], reverse=pick_r["name"], feature=insert.get("name", ""))
    else:
        one = pick_f or pick_r
        text = gettext("%(primer)s reads into %(feature)s; the lab has no primer for the other side yet.",
                       primer=one["name"], feature=insert.get("name", ""))
    return {"text": text, "primers": [{"name": h["name"], "url": h["url"], "where": h["where"]}
                                      for h in (pick_f, pick_r) if h]}


def _report(p: PlasmidRecord, files: list[tuple[str, bytes]], text: str = "") -> tuple[dict | None, list, list]:
    reads, problems = seqverify.reads_from_uploads(files, text)
    if not reads:
        return None, reads, problems
    report = seqverify.verify(p.full_sequence, bool(p.is_circular), _annotations(p), reads, name=p.name or "",
                              flank=TRACE_FLANK)
    return report, reads, problems


def _kept_files(session, p: PlasmidRecord, ids: list[int]) -> list[tuple[str, bytes]]:
    from . import services
    out = []
    for kept in session.scalars(select(PlasmidFile).where(PlasmidFile.plasmid_row_id == p.id,
                                                          PlasmidFile.id.in_(ids or [0])).order_by(PlasmidFile.id)):
        source = services.UPLOAD_DIR / Path(kept.path).name
        if source.is_file():
            out.append((kept.name, source.read_bytes()[:seqverify.MAX_FILE + 1]))
    return out


def _ids(c: PlasmidClone) -> list[int]:
    return [int(x) for x in (c.file_ids or "").split(",") if x.strip().isdigit()]


@bp.route("/plasmids/<int:row_id>/clones.json")
def clone_list(row_id: int):
    with SessionLocal() as session:
        p = _plasmid(session, row_id)
        return jsonify({"ok": True, "clones": [_row(c) for c in clones_of(session, p)],
                        "verified": verified(session, p), "next": sequence_next(session, p),
                        "can_edit": access.can_edit(p)})


@bp.route("/plasmids/<int:row_id>/clones/<int:clone_id>.json")
def clone_detail(row_id: int, clone_id: int):
    with SessionLocal() as session:
        p = _plasmid(session, row_id)
        c = session.get(PlasmidClone, clone_id)
        if c is None or c.plasmid_row_id != p.id:
            abort(404)
        report, _reads, problems = _report(p, _kept_files(session, p, _ids(c)))
        if report is None:
            # The reads' files are gone: what was found when they were checked.
            try:
                report = json.loads(c.report_json or "{}")
            except json.JSONDecodeError:
                report = {}
        return jsonify({"ok": True, "clone": _row(c), "report": report, "summary": _summary(report) if report else {},
                        "problems": problems})


@bp.route("/plasmids/<int:row_id>/clones", methods=["POST"])
def clone_check(row_id: int):
    """Check reads: each clone they name (or the one typed) is checked and,
    for someone who may edit the plasmid, kept."""
    from . import services
    from .app import _file_kind, stamp_updated

    with SessionLocal() as session:
        p = _plasmid(session, row_id)
        if not p.full_sequence:
            return jsonify({"ok": False, "error": gettext("This plasmid has no sequence to check reads against.")}), 400
        uploads = [u for u in request.files.getlist("reads") if u and u.filename]
        files = [(u.filename, u.stream.read(seqverify.MAX_FILE + 1)) for u in uploads]
        text = request.form.get("reads_text") or ""
        typed = re.sub(r"^\s*(?:clone|colony)\s*", "", (request.form.get("clone") or "").strip(), flags=re.I)[:40]
        if not files and not text.strip():
            return jsonify({"ok": False, "error": gettext("Choose read files (.ab1, FASTA or text), or paste a read.")}), 400

        # Which clone each file is from.
        groups: dict[str, list[int]] = {}
        unnamed = []
        for i, (name, _data) in enumerate(files):
            clone = typed or clone_of(name)
            if clone:
                groups.setdefault(clone, []).append(i)
            else:
                unnamed.append(i)
        # Reads that name no clone, and pasted text, are one more clone (or the one typed).
        paste_into = None
        if unnamed or text.strip():
            paste_into = typed or (_next_name(session, p, set(groups)) if unnamed or not groups else list(groups)[0])
            groups.setdefault(paste_into, []).extend(unnamed)

        keep = access.can_edit(p)
        made, problems, first = [], [], None
        for clone, indexes in groups.items():
            chosen = [files[i] for i in indexes]
            pasted = text if clone == paste_into else ""
            report, _reads, trouble = _report(p, chosen, pasted)
            problems += trouble
            if report is None:
                continue
            first = first or {"clone": clone, "report": report}
            if not keep:
                continue
            ids = []
            sources = list(chosen) + ([("pasted-reads.fa", pasted.encode("utf-8"))] if pasted.strip() else [])
            for name, data in sources:
                saved = services.save_uploaded_file(FileStorage(BytesIO(data), filename=name))
                if saved is None:
                    continue
                f = PlasmidFile(plasmid_row_id=p.id, kind=_file_kind(name), name=name[:200], path=saved["path"],
                                size_bytes=saved["size_bytes"], notes=label(clone)[:300], uploaded_by=g.user.username)
                session.add(f)
                session.flush()
                ids.append(f.id)
            row = session.scalar(select(PlasmidClone).where(PlasmidClone.plasmid_row_id == p.id,
                                                            PlasmidClone.name == clone))
            if row is None:
                row = PlasmidClone(plasmid_row_id=p.id, name=clone)
                session.add(row)
            # A clone sequenced again: its new reads join the ones it had, checked together.
            before = _ids(row)
            if before:
                report, _reads, _trouble = _report(p, _kept_files(session, p, before) + sources)
            row.file_ids = ",".join(str(i) for i in [*before, *ids])[:400]
            row.status, row.verdict = _verdict(report)
            row.report_json = json.dumps(seqverify.without_traces(json.loads(json.dumps(report))))
            row.checked_at, row.checked_by = datetime.utcnow(), g.user.username
            session.flush()
            made.append(row.id)
        if made:
            stamp_updated(p)
            session.commit()
        if first is None:
            return jsonify({"ok": False, "error": " ".join(problems) or gettext("None of the files could be read.")}), 400
        return jsonify({"ok": True, "made": made, "problems": problems, "kept": keep,
                        "clones": [_row(c) for c in clones_of(session, p)],
                        "report": first["report"], "label": label(first["clone"]),
                        "summary": _summary(first["report"])})


def _change(row_id: int, clone_id: int, how):
    from .app import stamp_updated
    with SessionLocal() as session:
        p = _plasmid(session, row_id)
        c = session.get(PlasmidClone, clone_id)
        if c is None or c.plasmid_row_id != p.id:
            abort(404)
        if not access.can_edit(p):
            return jsonify({"ok": False, "error": gettext("Only the plasmid's owner can change its clones.")}), 403
        message = how(session, c)
        stamp_updated(p)
        session.commit()
        return jsonify({"ok": True, "message": message, "clones": [_row(x) for x in clones_of(session, p)],
                        "verified": verified(session, p)})


@bp.route("/plasmids/<int:row_id>/clones/<int:clone_id>/accept", methods=["POST"])
def clone_accept(row_id: int, clone_id: int):
    def accept(_session, c):
        if request.form.get("undo"):
            c.accepted_at, c.accepted_by = None, ""
            return gettext("%(clone)s is no longer the accepted clone.", clone=label(c.name))
        c.accepted_at, c.accepted_by = datetime.utcnow(), g.user.username
        return gettext("%(clone)s accepted: the plasmid is verified with it.", clone=label(c.name))
    return _change(row_id, clone_id, accept)


@bp.route("/plasmids/<int:row_id>/clones/<int:clone_id>/remove", methods=["POST"])
def clone_remove(row_id: int, clone_id: int):
    def remove(session, c):
        name = label(c.name)
        session.delete(c)
        return gettext("%(clone)s removed. Its reads stay in the plasmid's files.", clone=name)
    return _change(row_id, clone_id, remove)


def clone_urls(row_id: int) -> dict:
    return {"list": url_for("clones.clone_list", row_id=row_id), "check": url_for("clones.clone_check", row_id=row_id),
            "one": url_for("clones.clone_detail", row_id=row_id, clone_id=0),
            "accept": url_for("clones.clone_accept", row_id=row_id, clone_id=0),
            "remove": url_for("clones.clone_remove", row_id=row_id, clone_id=0)}
