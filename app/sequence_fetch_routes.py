"""Fetch a plasmid's sequence by its Addgene number or GenBank accession
(app/sequence_fetch.py), for **New plasmid** and **Replace the sequence**.

`fetch-sequence?ref=` answers with the GenBank text and what it holds (its
name, length and features), which the page puts in the form's paste box;
creating or replacing then reads it as any pasted GenBank record.
"""
from __future__ import annotations

from flask import Blueprint, abort, g, jsonify, request

from . import sequence_fetch
from .i18n import gettext
from .sequence_parser import parse_genbank

bp = Blueprint("sequence_fetch", __name__)


@bp.before_request
def require_login():
    if g.get("user") is None:
        abort(401)
    return None


def problem_text(problem: sequence_fetch.FetchError) -> str:
    return {
        "unreadable": gettext("Type an Addgene number (Addgene #11150, or just 11150) or a GenBank accession (L09137, NC_045512.2)."),
        "not_found": gettext("Nothing was found under that number."),
        "no_file": gettext("Addgene has no full GenBank file for that plasmid. Download what it has from its page and upload it here."),
        "too_big": gettext("That record is far bigger than a plasmid, so it wasn’t loaded."),
        "offline": gettext("This server couldn’t reach NCBI or Addgene. If it has no internet, download the GenBank file there and upload it here."),
        "switched_off": gettext("Fetching sequences is switched off on this server. Download the GenBank file and upload it here."),
    }.get(problem.code, gettext("Nothing was found under that number."))


@bp.route("/plasmids/fetch-sequence")
def fetch():
    ref = (request.args.get("ref") or "").strip()
    if not ref:
        return jsonify({"ok": False, "error": problem_text(sequence_fetch.FetchError("unreadable"))}), 400
    try:
        found = sequence_fetch.fetch(ref)
    except sequence_fetch.FetchError as problem:
        status = 503 if problem.code in ("offline", "switched_off") else 404 if problem.code in ("not_found", "no_file") else 400
        return jsonify({"ok": False, "code": problem.code, "error": problem_text(problem)}), status
    parsed = parse_genbank(found["text"])
    if not parsed or not parsed.get("sequence"):
        return jsonify({"ok": False, "code": "not_found",
                        "error": problem_text(sequence_fetch.FetchError("not_found"))}), 404
    return jsonify({"ok": True, "text": found["text"], "source": found["source"], "label": found["label"],
                    "url": found["url"], "name": parsed.get("name") or "",
                    "length": len(parsed["sequence"]), "features": len(parsed.get("features") or []),
                    "circular": bool(parsed.get("is_circular"))})
