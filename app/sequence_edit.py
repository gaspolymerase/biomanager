"""Editing a plasmid on its own Sequence tab: the bases, the features and
the topology, one operation at a time.

- **Bases**: the picked bases become others (typing over them), bases go in
  after a base, or the picked ones go. Every annotation moves with the
  change as a site-directed mutagenesis moves them (`mutagenesis.
  shift_features`): one after it shifts, one the change runs through grows
  or shrinks, one it removes whole goes, and a primer's mark it touches goes
  too (that primer no longer binds as drawn). IUPAC letters are allowed, as
  in any stored sequence.
- **Features**: added on picked bases, renamed, retyped, turned round or
  removed. A feature is named by its place in the stored list (`at`).
- **Topology**: circular or linear.
- **A whole state**: what Undo sends back — the plasmid as it was before an
  edit, which the routes clean as any saved map is cleaned.

Pure: (sequence, circular, annotations) in, the same out, or an EditError.
app/sequence_routes.py saves each one as a version and checks nobody else
changed the plasmid in between (`state_hash`).
"""
from __future__ import annotations

import hashlib
import json

from . import mutagenesis
from .sequence_parser import clean_bases

FEATURE_TYPES = ("misc_feature", "CDS", "promoter", "terminator", "rep_origin", "polyA_signal", "enhancer",
                 "primer_bind", "protein_bind", "LTR", "sig_peptide", "5'UTR", "3'UTR", "intron", "exon",
                 "misc_RNA", "ncRNA", "regulatory", "RBS", "tag")


class EditError(ValueError):
    def __init__(self, code: str, **details):
        super().__init__(code)
        self.code, self.details = code, details


def state_hash(sequence: str, circular: bool, features) -> str:
    """A short fingerprint of what the page edited, so an edit made on a
    page someone else has since changed is refused rather than merged."""
    text = json.dumps([sequence or "", bool(circular), features or []], sort_keys=True)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def bases(sequence: str, features: list, start: int, end: int, new: str) -> tuple[str, list, dict]:
    """Bases `start`..`end` (1-based, inclusive) become `new`; an `end` one
    before `start` picks none, so `new` goes in before `start`; an empty
    `new` deletes. Returns (sequence, features, what happened)."""
    seq = (sequence or "").upper()
    n = len(seq)
    put = clean_bases(new or "")
    if (new or "").strip() and not put:
        raise EditError("not_bases")
    if not (1 <= start <= n + 1 and start - 1 <= end <= n):
        raise EditError("outside")
    cut = end - start + 1
    if not cut and not put:
        raise EditError("nothing")
    if cut and seq[start - 1:end] == put:
        raise EditError("same")
    after = seq[:start - 1] + put + seq[end:]
    if not after:
        raise EditError("empty")
    change = mutagenesis.Change(start - 1, cut, put)
    moved = mutagenesis.shift_features(features, n, change)
    kind = "deletion" if not put else "insertion" if not cut else "substitution"
    return after, moved, {"kind": kind, "start": start, "end": end, "removed": cut, "added": len(put),
                          "dropped": len([f for f in features or [] if isinstance(f, dict)]) - len(moved)}


def _direction(raw) -> int:
    try:
        return -1 if int(raw) < 0 else 1
    except (TypeError, ValueError):
        return 1


def _type(raw: str) -> str:
    text = (raw or "").strip()[:40]
    return text or "misc_feature"


def add_feature(features: list, length: int, start: int, end: int, name: str, ftype: str = "misc_feature",
                direction: int = 1) -> list:
    """A feature on bases `start`..`end` (1-based)."""
    if not (1 <= start <= end <= length):
        raise EditError("outside")
    name = (name or "").strip()[:120]
    if not name:
        raise EditError("no_name")
    return [*(features or []), {"name": name, "type": _type(ftype), "start": start - 1, "end": end - 1,
                                "direction": _direction(direction), "color": "#cbd5e1", "notes": ""}]


def _feature_at(features: list, at) -> int:
    try:
        i = int(at)
    except (TypeError, ValueError):
        raise EditError("no_feature") from None
    if not (0 <= i < len(features or [])) or not isinstance(features[i], dict):
        raise EditError("no_feature")
    return i


def edit_feature(features: list, at, name: str | None = None, ftype: str | None = None,
                 direction=None) -> list:
    """The feature at `at` renamed, retyped or turned round."""
    i = _feature_at(features, at)
    f = dict(features[i])
    if name is not None:
        name = name.strip()[:120]
        if not name:
            raise EditError("no_name")
        f["name"] = name
    if ftype is not None:
        f["type"] = _type(ftype)
    if direction is not None:
        f["direction"] = _direction(direction)
    return [*features[:i], f, *features[i + 1:]]


def delete_feature(features: list, at) -> list:
    i = _feature_at(features, at)
    return [*features[:i], *features[i + 1:]]
