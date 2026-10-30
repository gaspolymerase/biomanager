"""Compare two plasmids: align one against the other and list every
difference, with the feature it falls in and what it does to a protein.

The use is the everyday one: two near-identical constructs, and the
question of which is in the tube, or what a colleague's "same plasmid"
actually changed. The alignment is the banded local one `app/seqverify.py`
already uses to place a sequencing read on a plasmid — plasmid A is read
against plasmid B — so the two are matched however their origins sit or
whichever strand B was saved on, and a run of differences is one indel
rather than many. It follows that very large rearrangements (a net indel
wider than the read aligner's band) show as the aligned stretch ending
early rather than as one big difference; for near-identical plasmids, which
is what this is for, every change is caught.

Pure: sequences and features in, a comparison out. app/compare_routes.py
turns it into the Overview's **Compare with…**.
"""
from __future__ import annotations

from . import seqverify


def _read(sequence: str, label: str) -> seqverify.Read:
    return seqverify.Read(name=label, bases=(sequence or "").upper().replace("U", "T"),
                          quality=[], source="compare")


def compare(a_sequence: str, a_circular: bool, a_features: list, a_label: str,
            b_sequence: str, b_circular: bool, b_features: list, b_label: str) -> dict:
    """Plasmid A against plasmid B. Differences are B→A: `plasmid` is B's
    base (or bases), `read` is A's, at B's coordinate."""
    a_seq = (a_sequence or "").upper().replace("U", "T")
    b_seq = (b_sequence or "").upper().replace("U", "T")
    out = {"a": {"name": a_label, "length": len(a_seq)}, "b": {"name": b_label, "length": len(b_seq)}}
    if not a_seq or not b_seq:
        return {**out, "ok": False, "error": "no_sequence"}

    target = seqverify.Target(b_seq, b_circular, longest_read=len(a_seq))
    features = seqverify.map_features(b_features, target.length)
    r = seqverify.check_read(_read(a_seq, a_label), target, features)

    if not r.get("placed"):
        return {**out, "ok": True, "matched": False,
                "identity": r.get("identity"), "status": r.get("status"), "verdict": r.get("verdict")}

    differences = []
    for d in r["differences"]:
        differences.append({
            "kind": d["kind"],                       # mismatch, insertion or deletion
            "at_b": d["position"], "at_a": d.get("read_position"),
            "b": d.get("plasmid", ""), "a": d.get("read", ""), "length": d["length"],
            "features": d.get("features", []),
            "effect": d["effect"]["text"] if d.get("effect") else "",
            "effect_kind": d["effect"]["kind"] if d.get("effect") else "",
            "text": d["text"],
        })
    # A's aligned span (as A reads), and the part of A that did not line up.
    a_from, a_to = r.get("read_span", [None, None])
    aligned = (a_to - a_from + 1) if a_from and a_to else 0
    return {
        **out, "ok": True, "matched": True,
        "strand": r.get("strand"), "identity": r.get("identity"),
        "same": not differences and aligned >= 0.99 * len(a_seq) and len(a_seq) == len(b_seq),
        "b_span": [r.get("start"), r.get("end")], "across_origin": r.get("across_origin"),
        "a_span": [a_from, a_to], "a_covered": aligned, "a_outside": max(0, len(a_seq) - aligned),
        "differences": differences, "count": len(differences),
        "features_a": _marks(a_features, len(a_seq)), "features_b": _marks(b_features, len(b_seq)),
    }


def _marks(features, length: int) -> list[dict]:
    """The named features, for the two rings the page draws."""
    out = []
    for f in seqverify.map_features(features, length):
        ranges = f["ranges"]
        out.append({"name": f["name"], "type": f["type"], "direction": f["direction"],
                    "start": ranges[0][0], "end": ranges[-1][1]})
    return out
