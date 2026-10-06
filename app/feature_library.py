"""The lab's feature library: named elements (a promoter, an ITR, WPRE, a
resistance gene) kept by sequence, so Detect features can find them on any
plasmid's map.

Entries come from the lab's own annotated plasmids, a SnapGene or Addgene
file's features being the usual source: Add to library on a plasmid's
Sequence tab takes that plasmid's named features, and Collect from every
plasmid takes the whole lab's. One entry per sequence (`seq_hash`); the
first name it was given is kept. No sequences are built in: an element
typed in by hand and wrong by a base would label every map wrongly.

Detection is an exact match on either strand, across the origin of a
circular plasmid, of entries at least MIN_LENGTH long; a place already
annotated with the same name, or the same span and strand, is left alone.
"""
from __future__ import annotations

import hashlib
import json
import re

from sqlalchemy import select

from .models import FeatureLibraryEntry, PlasmidRecord
from .primer_records import reverse_complement

MIN_LENGTH = 15            # shorter matches turn up by chance
MAX_LENGTH = 10000
MAX_HITS = 20              # places per entry on one plasmid
CATEGORIES = ("viral", "promoter", "coding", "selection", "origin", "terminator", "other")

# Names that say nothing about what a feature is.
_GENERIC = re.compile(r"^(?:|feature|misc[_ ]feature|region|source|untitled|new feature|\d+|feature\s*\d+)$", re.I)
_VIRAL = re.compile(
    r"\b(?:ITRs?|LTRs?|WPRE|RRE|cPPT(?:/CTS)?|CTS|psi|packaging|VSV-?G|gag|pol|rev|tat|env|HBV|HIV|SIV|MLV|MSCV"
    r"|AAV\d*|PRE)\b|Ψ", re.I)
_SELECTION = re.compile(r"(?:AmpR|KanR|NeoR|PuroR|HygR|ZeoR|BSD|BlastR|CmR|TetR|SpecR|GentR|bla|aph|nptII|pac|hph"
                        r"|resistance|ccdB)\b", re.I)


def category_of(name: str, ftype: str) -> str:
    """Where an element sits in the library, from its name and GenBank type."""
    name, ftype = name or "", (ftype or "").lower()
    if _VIRAL.search(name) or ftype in ("ltr", "repeat_region") and re.search(r"ITR|LTR", name, re.I):
        return "viral"
    if ftype in ("promoter", "enhancer") or re.search(r"promoter|enhancer", name, re.I):
        return "promoter"           # an AmpR promoter is a promoter
    if _SELECTION.search(name):
        return "selection"
    if ftype == "rep_origin" or re.search(r"\bori\b|origin", name, re.I):
        return "origin"
    if ftype in ("terminator", "polya_signal") or re.search(r"poly\s*\(?A\)?|\bpA\b|terminator", name, re.I):
        return "terminator"
    if ftype in ("cds", "gene"):
        return "coding"
    return "other"


def seq_hash(sequence: str) -> str:
    return hashlib.sha256(sequence.encode()).hexdigest()


def region_of(template: str, a: dict) -> str:
    """The bases an annotation covers, read along its own strand (5′→3′)."""
    start, end = int(a["start"]), int(a["end"])
    if a.get("locations"):
        bases = "".join(template[loc["start"]:loc["end"] + 1] for loc in a["locations"])
    elif start <= end:
        bases = template[start:end + 1]
    else:
        bases = template[start:] + template[:end + 1]
    bases = bases.upper()
    return bases if int(a.get("direction", 1) or 1) >= 0 else reverse_complement(bases)


def usable(a: dict) -> bool:
    """A feature (not a primer, translation or part) worth reusing: named."""
    return (isinstance(a, dict) and (a.get("kind") or "feature") == "feature"
            and not _GENERIC.match((a.get("name") or "").strip()))


def entries(session, category: str = "") -> list[FeatureLibraryEntry]:
    stmt = select(FeatureLibraryEntry).order_by(FeatureLibraryEntry.category, FeatureLibraryEntry.name)
    if category:
        stmt = stmt.where(FeatureLibraryEntry.category == category)
    return list(session.scalars(stmt))


def hashes(session) -> set[str]:
    """Every sequence the library already holds, for adding many at once."""
    return set(session.scalars(select(FeatureLibraryEntry.seq_hash)))


def add(session, *, name: str, ftype: str, sequence: str, color: str = "", notes=None,
        source: PlasmidRecord | None = None, user: str = "", source_name: str = "",
        known: set[str] | None = None) -> tuple[FeatureLibraryEntry | None, bool]:
    """The entry for this sequence, made if the library hasn't got it.
    Returns (entry, made); (None, False) for a sequence too short or long.

    `known` is the set of sequence hashes already in the library (see
    `hashes`): pass it when adding hundreds at once and each is checked
    against it and added to it, rather than each costing a query and a
    flush. An entry already there then answers (None, False), since it is
    not fetched."""
    sequence = re.sub(r"[^A-Za-z]", "", sequence or "").upper()
    if not (MIN_LENGTH <= len(sequence) <= MAX_LENGTH):
        return None, False
    key = seq_hash(sequence)
    if known is not None:
        if key in known:
            return None, False
    else:
        found = session.scalar(select(FeatureLibraryEntry).where(FeatureLibraryEntry.seq_hash == key))
        if found is not None:
            return found, False
    entry = FeatureLibraryEntry(
        name=(name or "").strip()[:120], type=(ftype or "misc_feature")[:40], category=category_of(name, ftype),
        sequence=sequence, seq_hash=key, color=(color or "")[:20],
        notes_json=json.dumps(notes if isinstance(notes, dict) else {}),
        source_row_id=source.id if source is not None else None, source_name=source_name[:120], created_by=user)
    session.add(entry)
    if known is None:
        session.flush()
    else:
        known.add(key)
    return entry, True


def add_from_sequence(session, sequence: str, annotations, user: str,
                      source: PlasmidRecord | None = None) -> int:
    """Every named feature of an annotated sequence into the library, by the
    bases it covers. How many were new. The annotations are either a
    plasmid's stored ones or a freshly parsed file's (same shape)."""
    template = (sequence or "").upper()
    if not template:
        return 0
    made = 0
    for a in annotations or []:
        if not usable(a):
            continue
        try:
            bases = region_of(template, a)
        except (KeyError, TypeError, ValueError):
            continue
        _entry, new = add(session, name=a["name"], ftype=a.get("type", ""), sequence=bases, color=a.get("color", ""),
                          notes=a.get("notes") if isinstance(a.get("notes"), dict) else None, source=source, user=user)
        made += new
    return made


def add_from_plasmid(session, plasmid: PlasmidRecord, user: str) -> int:
    """Every named feature of this plasmid into the library. How many were new."""
    try:
        annotations = json.loads(plasmid.features_json or "[]")
    except json.JSONDecodeError:
        return 0
    return add_from_sequence(session, plasmid.full_sequence or "", annotations, user, source=plasmid)


def collect(session, user: str) -> int:
    """Every plasmid's named features into the library. How many were new."""
    return sum(add_from_plasmid(session, p, user) for p in session.scalars(
        select(PlasmidRecord).where(PlasmidRecord.full_sequence != "").order_by(PlasmidRecord.plasmid_id)))


def detect(library: list[FeatureLibraryEntry], sequence: str, circular: bool, existing: list[dict]) -> list[dict]:
    """New feature annotations for the library's elements found in this
    sequence, in the shape features_json keeps."""
    seq = (sequence or "").upper()
    n = len(seq)
    if not n:
        return []
    longest = max((len(e.sequence) for e in library), default=0)
    search = seq + (seq[:max(longest - 1, 0)] if circular else "")
    spans = {(int(a.get("start", -1)), int(a.get("end", -1)), 1 if int(a.get("direction", 1) or 1) >= 0 else -1)
             for a in existing if isinstance(a, dict)}
    named = {}
    for a in existing:
        if isinstance(a, dict) and str(a.get("start", "")).lstrip("-").isdigit():
            named.setdefault((a.get("name") or "").strip().lower(), []).append((int(a["start"]), int(a["end"])))
    found = []
    for e in library:
        size = len(e.sequence)
        if size < MIN_LENGTH or size > n:
            continue
        same_name = named.get(e.name.strip().lower(), [])
        probes = [(1, e.sequence)]
        if reverse_complement(e.sequence) != e.sequence:
            probes.append((-1, reverse_complement(e.sequence)))
        hits = 0
        for direction, probe in probes:
            at = search.find(probe)
            while at != -1 and at < n and hits < MAX_HITS:
                span = (at, (at + size - 1) % n, direction)
                if span not in spans and not any(_overlaps(span[0], span[1], s0, e0) for s0, e0 in same_name):
                    spans.add(span)
                    note = json.loads(e.notes_json or "{}")
                    found.append({"name": e.name, "type": e.type or "misc_feature", "start": span[0], "end": span[1],
                                  "direction": direction, "color": e.color or "#cbd5e1",
                                  "notes": note if isinstance(note, dict) else ""})
                    hits += 1
                at = search.find(probe, at + 1)
    return sorted(found, key=lambda a: a["start"])


def _within(pos: int, start: int, end: int) -> bool:
    return start <= pos <= end if start <= end else (pos >= start or pos <= end)


def _overlaps(s1: int, e1: int, s2: int, e2: int) -> bool:
    """Two spans (either may cross the origin) share a base."""
    return _within(s1, s2, e2) or _within(e1, s2, e2) or _within(s2, s1, e1) or _within(e2, s1, e1)
