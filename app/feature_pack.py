"""The common-features pack: GenoLIB, fetched when a lab asks for it.

GenoLIB (Adames NR, Wilson ML, Fang G, Lux MW, Glick BS, Peccoud J,
*GenoLIB: a database of biological parts derived from a library of common
plasmid features*, Nucleic Acids Research 2015;43(10):4823-4832,
doi:10.1093/nar/gkv272) curated 1,943 named elements, each with its DNA
sequence, from about 2,000 widely used plasmids. The article is CC BY 4.0
and its supplement holds the library as SBOL.

BioManager does not carry the file: the lab downloads it from Europe PMC's
open archive at the moment it asks for it, exactly as it would download a
map from a repository, and the elements go into that lab's own library.
That keeps the app free of redistributing a dataset whose provenance is
worth stating plainly: the paper says the authors exported SnapGene's
published library of annotated files to build it, and nothing records
SnapGene agreeing to that. The elements are good -- every one spot-checked
against an independent source matched to the base -- but a lab should
check anything it relies on, which is why `SOURCE_NAME` is kept on each
entry the pack adds.

The supplement is a zip of zips: the outer one holds the article's
supplemental files, one of which (S4) is a zip of SBOL v1 RDF/XML, one
file per lab host plus `labhost_All.xml` with all of them.
"""
from __future__ import annotations

import io
import json
import re
import threading
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

SOURCE_NAME = "GenoLIB"
CITATION = ("Adames NR, Wilson ML, Fang G, Lux MW, Glick BS, Peccoud J. GenoLIB: a database of biological "
            "parts derived from a library of common plasmid features. Nucleic Acids Res. 2015;43(10):4823-4832. "
            "doi:10.1093/nar/gkv272")
LICENCE = "CC BY 4.0"
ARTICLE_URL = "https://doi.org/10.1093/nar/gkv272"
# Europe PMC's open archive: the article's supplementary files, as a zip.
SUPPLEMENT_URL = ("https://www.ebi.ac.uk/europepmc/webservices/rest/PMC4446419/"
                  "supplementaryFiles?includeInlineImage=false")
TIMEOUT = 300                      # seconds; the archive sends 5.5 MB slowly
STATUS_KEY = "feature_pack_status"  # app_settings, so every worker sees it
STALE = timedelta(minutes=30)       # a run that left no answer is not still going
MAX_BYTES = 64 * 1024 * 1024       # a ceiling, in case the archive ever answers something else
WANTED = "labhost_All.xml"

_SBOL = "{http://sbols.org/v1#}"
_RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
# The Sequence Ontology terms the library uses, where the GenBank feature
# key is unambiguous. Anything else is a misc_feature, and the element's
# name decides its shelf in the library (feature_library.category_of).
SO_TYPES = {
    "0000167": "promoter",
    "0000316": "CDS",
    "0000141": "terminator",
    "0000296": "rep_origin",
    "0000552": "RBS",
    "0000057": "protein_bind",
    "0000165": "enhancer",
    "0005850": "primer_bind",
    "0000724": "oriT",
    "0000627": "insulator",
    "0000188": "intron",
}


class PackUnavailable(Exception):
    """The archive could not be reached, or did not answer with the pack."""


def fetch_bytes(url: str = SUPPLEMENT_URL) -> bytes:
    """The supplement zip from Europe PMC."""
    request = Request(url, headers={"User-Agent": "BioManager (feature library)"})
    try:
        with urlopen(request, timeout=TIMEOUT) as response:   # noqa: S310 (a fixed https address)
            return response.read(MAX_BYTES + 1)
    except Exception as problem:  # noqa: BLE001 - urllib raises many kinds; the caller only needs the text
        raise PackUnavailable(str(problem)) from problem


def sbol_from_supplement(raw: bytes) -> bytes:
    """The SBOL file inside the supplement's zip of zips."""
    if len(raw) > MAX_BYTES:
        raise PackUnavailable("the download was larger than expected")
    try:
        outer = zipfile.ZipFile(io.BytesIO(raw))
        for name in outer.namelist():
            if not name.lower().endswith(".zip"):
                continue
            inner = zipfile.ZipFile(io.BytesIO(outer.read(name)))
            for member in inner.namelist():
                if member.endswith(WANTED):
                    return inner.read(member)
    except (zipfile.BadZipFile, KeyError, OSError) as problem:
        raise PackUnavailable(str(problem)) from problem
    raise PackUnavailable(f"the supplement held no {WANTED}")


def elements(sbol: bytes) -> list[dict]:
    """Every element in the SBOL file, as {name, type, sequence, description}.
    An entry without a name or a sequence is skipped."""
    try:
        root = ET.fromstring(sbol)
    except ET.ParseError as problem:
        raise PackUnavailable(f"the SBOL file could not be read: {problem}") from problem
    out = []
    for component in root.iter(f"{_SBOL}DnaComponent"):
        bases = "".join(node.text or "" for node in component.iter(f"{_SBOL}nucleotides"))
        bases = re.sub(r"[^A-Za-z]", "", bases).upper()
        name = (component.findtext(f"{_SBOL}name") or component.findtext(f"{_SBOL}displayId") or "").strip()
        # The library numbers its variants ("WPRE-001", "AmpR promoter-009");
        # they differ by sequence, which is what an entry is keyed on, so the
        # plain name is what a map should show.
        name = re.sub(r"-\d{3}$", "", name).strip()
        if not name or not bases:
            continue
        so = ""
        for kind in component.iter(f"{_RDF}type"):
            match = re.search(r"(\d{7})", kind.get(f"{_RDF}resource") or "")
            if match:
                so = match.group(1)
                break
        out.append({"name": name[:120], "type": SO_TYPES.get(so, "misc_feature"), "sequence": bases,
                    "description": (component.findtext(f"{_SBOL}description") or "").strip()})
    return out


def add_to_library(session, user: str, raw: bytes | None = None, elements_in_hand=None) -> tuple[int, int]:
    """Fetch the pack (unless a supplement, or its elements, are already in
    hand) and put it in the lab's library. Returns (added, elements)."""
    from . import feature_library

    found = (elements_in_hand if elements_in_hand is not None
             else elements(sbol_from_supplement(raw if raw is not None else fetch_bytes())))
    # Two thousand elements at once: check them against one read of the
    # library's hashes rather than a query and a flush each (a minute of
    # waiting, long enough for a lab server to give up on the request).
    known = feature_library.hashes(session)
    added = 0
    for element in found:
        notes = {"note": [element["description"]]} if element["description"] else None
        _entry, made = feature_library.add(session, name=element["name"], ftype=element["type"],
                                           sequence=element["sequence"], notes=notes, user=user,
                                           source_name=SOURCE_NAME, known=known)
        added += made
    session.flush()
    return added, len(found)


# ---------------------------------------------------------------------------
# Running it: the download takes about a minute, longer than a lab server
# gives a request (gunicorn.conf.py: timeout = 60), so it runs in a thread
# and leaves its state in app_settings, where every worker can read it.
# ---------------------------------------------------------------------------


def status(session) -> dict:
    """{state: idle|running|done|failed, added, total, error, at, by}."""
    from .inventory_service import get_setting

    try:
        saved = json.loads(get_setting(session, STATUS_KEY, "") or "{}")
    except json.JSONDecodeError:
        saved = {}
    if saved.get("state") == "running":
        started = saved.get("at", "")
        try:
            if datetime.fromisoformat(started) < datetime.utcnow() - STALE:
                saved = {"state": "failed", "error": "it stopped without finishing", "at": started}
        except ValueError:
            saved = {}
    return {"state": "idle", "added": 0, "total": 0, "error": "", "at": "", "by": "", **saved}


def _save_status(session, **fields) -> None:
    from .inventory_service import set_setting

    set_setting(session, STATUS_KEY, json.dumps({"at": datetime.utcnow().isoformat(timespec="seconds"), **fields}))


def start(session, user: str) -> bool:
    """Begin the download in the background. False when one is already
    running (another admin asked a moment ago)."""
    if status(session)["state"] == "running":
        return False
    _save_status(session, state="running", by=user)
    session.commit()
    threading.Thread(target=_run, args=(user,), name="feature-pack", daemon=True).start()
    return True


def _run(user: str) -> None:
    """The download and the adding, off the request. Whatever happens, the
    answer is written where the page can show it."""
    from .db import SessionLocal

    try:
        raw = fetch_bytes()
        found = elements(sbol_from_supplement(raw))
    except PackUnavailable as problem:
        with SessionLocal() as session:
            _save_status(session, state="failed", by=user, error=str(problem))
            session.commit()
        return
    try:
        with SessionLocal() as session:
            added, total = add_to_library(session, user, elements_in_hand=found)
            _save_status(session, state="done", by=user, added=added, total=total)
            session.commit()
    except Exception as problem:  # noqa: BLE001 - the page must say what went wrong, whatever it was
        with SessionLocal() as session:
            _save_status(session, state="failed", by=user, error=str(problem))
            session.commit()
