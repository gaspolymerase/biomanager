"""Site-directed mutagenesis: a substitution, an insertion or a deletion in
a circular plasmid, made by copying the whole plasmid with two primers that
sit back to back, as NEB's Q5 Site-Directed Mutagenesis kit has them. The
PCR product is the plasmid, opened at the change; a kinase, a ligase and
DpnI (KLD) close it and remove the template.

- **Deletion**: the primers anneal on either side of the bases to lose.
- **Substitution or small insertion**: the new bases ride on the 5′ end of
  the forward primer, which anneals just past them.
- **Larger insertion** (SPLIT bases or more): half on each primer's 5′ end.

Each primer's annealing part is the shortest stretch that reaches
TARGET_TM, as the assembly's primers are (`cloning.anneal_length`).

Also here: what the change does to each coding feature it falls in
(K79A, a frameshift, a deletion in frame), the codon for an amino acid
written as K79A, and the silent changes that add or remove an enzyme site.

Pure: sequences in, sequences out; the routes are app/mutagenesis_routes.py.
Positions given to and returned from this module are 1-based along the top
strand, as the Sequence tab shows them; features stay 0-based with their end
included, as the plasmid keeps them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import product

from . import cloning, primer_design
from .inventory_service import primer_tm
from .primer_records import reverse_complement
from .seqverify import CODE

SPLIT = 7                # inserted bases from which half goes on each primer
MIN_LEFT = 100           # bases the product must keep besides the change
NEAR_CODONS = 15         # a silent change is looked for this far either side of the pick
MOST_CHANGED = 3         # bases a silent change may alter
# One codon an amino acid is usually written with, in E. coli and in human
# cells alike: the tie-break when two codons are equally close.
PREFERRED = {"A": "GCC", "R": "CGC", "N": "AAC", "D": "GAC", "C": "TGC", "Q": "CAG", "E": "GAG", "G": "GGC",
             "H": "CAC", "I": "ATC", "L": "CTG", "K": "AAG", "M": "ATG", "F": "TTC", "P": "CCC", "S": "AGC",
             "T": "ACC", "W": "TGG", "Y": "TAC", "V": "GTG", "*": "TAA"}
SYNONYMS: dict[str, list[str]] = {}
for _codon, _aa in sorted(CODE.items()):
    SYNONYMS.setdefault(_aa, []).append(_codon)


class Problem(ValueError):
    """Why a change cannot be made: `code` names it, the routes say it."""

    def __init__(self, code: str, **details):
        super().__init__(code)
        self.code, self.details = code, details


@dataclass(frozen=True)
class Change:
    at: int          # 0-based: the first base replaced, or the base an insertion goes before
    cut: int         # how many bases go
    new: str         # what goes in their place

    @property
    def delta(self) -> int:
        return len(self.new) - self.cut

    @property
    def kind(self) -> str:
        if not self.new:
            return "deletion"
        if not self.cut:
            return "insertion"
        return "substitution"


def clean(bases: str) -> str:
    return re.sub(r"[^ACGT]", "", (bases or "").upper())


def change_from(sequence: str, start: int, end: int, bases: str) -> Change:
    """The bases `start`..`end` (1-based, inclusive) become `bases`; an
    `end` one before `start` picks no bases, so `bases` go in before
    `start`."""
    n = len(sequence)
    new = clean(bases)
    if not (1 <= start <= n + 1 and start - 1 <= end <= n):
        raise Problem("outside")
    change = Change(start - 1, end - start + 1, new)
    if not change.cut and not change.new:
        raise Problem("nothing")
    if change.cut == len(new) and sequence[change.at:change.at + change.cut].upper() == new:
        raise Problem("same")
    return change


def apply(sequence: str, change: Change) -> str:
    return sequence[:change.at] + change.new + sequence[change.at + change.cut:]


def _move(pos: int, change: Change, is_end: bool) -> int:
    """Where a base of the old plasmid is in the new one. A base the change
    removed moves to the edge of what replaced it."""
    if pos < change.at:
        return pos
    if pos >= change.at + change.cut:
        return pos + change.delta
    return change.at + len(change.new) - 1 if is_end else change.at


def _touches(start: int, end: int, change: Change) -> bool:
    """Whether a span (0-based, end included, not across the origin) meets
    the change: overlaps the bases it replaces, or holds an insertion."""
    if change.cut:
        return change.at <= end and change.at + change.cut - 1 >= start
    return start < change.at <= end


def _spans(f: dict, n: int) -> list[tuple[int, int]]:
    s, e = int(f["start"]), int(f["end"])
    return [(s, e)] if s <= e else [(s, n - 1), (0, e)]


def shift_features(features, length: int, change: Change) -> list[dict]:
    """The plasmid's annotations on the new plasmid. Those after the change
    move by its length; one it runs through grows or shrinks; one it removes
    whole goes, and so does a primer's mark that it changes, since that
    primer no longer binds as drawn."""
    out = []
    for f in features or []:
        if not isinstance(f, dict):
            continue
        try:
            s, e = int(f["start"]), int(f["end"])
        except (KeyError, TypeError, ValueError):
            continue
        touched = any(_touches(a, b, change) for a, b in _spans(f, length))
        if touched and f.get("kind") == "primer":
            continue
        ns, ne = _move(s, change, False), _move(e, change, True)
        if s <= e and ne < ns:
            continue
        moved = dict(f, start=ns, end=ne)
        moved.pop("locations", None)
        out.append(moved)
    return out


# ---------------------------------------------------------------------------
# The primers
# ---------------------------------------------------------------------------


def _primer(tail: str, anneal: str, template: str, circular: bool) -> dict:
    tm = primer_tm(anneal)
    whole = tail + anneal
    checked = primer_design.check(whole, template, circular)
    return {**checked, "tail": tail, "anneal": anneal, "sequence": whole, "length": len(whole),
            "tm": round(tm, 1) if tm is not None else None, "anneal_length": len(anneal)}


def design_primers(sequence: str, change: Change) -> tuple[dict, dict]:
    """The forward and reverse primer, back to back at the change, on a
    circular plasmid."""
    seq = sequence.upper()
    n = len(seq)
    downstream = cloning._span(seq, (change.at + change.cut) % n, cloning.ANNEAL_MAX, True)
    upstream = reverse_complement(cloning._span(seq, (change.at - cloning.ANNEAL_MAX) % n, cloning.ANNEAL_MAX, True))
    forward_anneal = downstream[:cloning.anneal_length(downstream)]
    reverse_anneal = upstream[:cloning.anneal_length(upstream)]
    if len(change.new) < SPLIT:
        forward_tail, reverse_tail = change.new, ""
    else:
        half = len(change.new) // 2
        forward_tail, reverse_tail = change.new[half:], reverse_complement(change.new[:half])
    for tail, anneal in ((forward_tail, forward_anneal), (reverse_tail, reverse_anneal)):
        if len(tail) + len(anneal) > primer_design.MAX_LENGTH:
            raise Problem("too_long", most=primer_design.MAX_LENGTH)
    return _primer(forward_tail, forward_anneal, seq, True), _primer(reverse_tail, reverse_anneal, seq, True)


# ---------------------------------------------------------------------------
# What it does to a protein
# ---------------------------------------------------------------------------


def coding(f: dict) -> bool:
    return str(f.get("type", "")).lower() in ("cds", "gene") and (f.get("kind") or "feature") == "feature"


def strand(f: dict) -> int:
    try:
        return -1 if int(f.get("direction", f.get("strand", 1)) or 1) < 0 else 1
    except (TypeError, ValueError):
        return 1


def bases_of(sequence: str, f: dict) -> str:
    """A feature's bases on its own strand."""
    s, e = int(f["start"]), int(f["end"])
    bases = sequence[s:e + 1] if s <= e else sequence[s:] + sequence[:e + 1]
    return reverse_complement(bases) if strand(f) < 0 else bases.upper()


def read_on(sequence: str, f: dict) -> str:
    """A feature's bases from its start, on its strand, on round the
    plasmid: where a frameshift's new frame finds its stop."""
    if strand(f) > 0:
        s = int(f["start"])
        return (sequence[s:] + sequence[:s]).upper()
    e = int(f["end"])
    return reverse_complement(sequence[e + 1:] + sequence[:e + 1])


def translate(bases: str) -> str:
    return "".join(CODE.get(bases[i:i + 3], "X") for i in range(0, len(bases) - 2, 3))


def describe(old: str, new: str, frameshift: bool) -> tuple[str, str]:
    """(kind, label) for a protein that was `old` and is now `new`, as a lab
    writes it: K79A, K79*, K79del, K79_L80insGS, K79Rfs*12."""
    if old == new:
        return "silent", ""
    i = 0
    while i < min(len(old), len(new)) and old[i] == new[i]:
        i += 1
    if frameshift:
        stop = new.find("*", i)
        was = old[i] if i < len(old) else "*"
        now = new[i] if i < len(new) else ""
        return "frameshift", f"{was}{i + 1}{now}fs" + (f"*{stop - i + 1}" if stop >= 0 else "")
    j = 0
    while j < min(len(old), len(new)) - i and old[-1 - j] == new[-1 - j]:
        j += 1
    was, now = old[i:len(old) - j], new[i:len(new) - j]
    stop = "*" in now
    if len(was) == 1 and len(now) == 1:
        return ("nonsense" if stop else "missense"), f"{was}{i + 1}{now}"
    where = f"{was[0]}{i + 1}" if len(was) == 1 else (f"{was[0]}{i + 1}_{was[-1]}{i + len(was)}" if was else "")
    if not now:
        return "deletion", f"{where}del"
    if not was:
        return ("nonsense" if stop else "insertion"), f"{old[i - 1]}{i}_{old[i]}{i + 1}ins{now}" if 0 < i < len(old) \
            else f"ins{now}"
    return ("nonsense" if stop else "delins"), f"{where}delins{now}"


def effects(sequence: str, features, change: Change) -> list[dict]:
    """For each coding feature the change falls in: the change to its
    protein, as `describe` writes it."""
    n = len(sequence)
    after = apply(sequence, change)
    out = []
    for f in features or []:
        try:
            if not isinstance(f, dict) or not coding(f) or not any(_touches(a, b, change) for a, b in _spans(f, n)):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        old = translate(bases_of(sequence, f))
        moved = shift_features([f], n, change)
        if not moved:
            out.append({"name": f.get("name", ""), "kind": "removed", "label": "",
                        "length_before": len(old.rstrip("*")), "length_after": 0})
            continue
        shifted = change.delta % 3 != 0
        new = translate(read_on(after, moved[0]) if shifted else bases_of(after, moved[0]))
        kind, label = describe(old, new, shifted)
        out.append({"name": f.get("name", ""), "kind": kind, "label": label,
                    "length_before": len(old.rstrip("*")), "length_after": len(new.split("*")[0])})
    return out


# ---------------------------------------------------------------------------
# The whole design
# ---------------------------------------------------------------------------


def design(sequence: str, circular: bool, features, start: int, end: int, bases: str) -> dict:
    """Everything the Mutate panel shows, or a Problem."""
    seq = (sequence or "").upper()
    if not circular:
        raise Problem("linear")
    change = change_from(seq, start, end, bases)
    if len(seq) - change.cut < MIN_LEFT:
        raise Problem("too_much")
    forward, reverse = design_primers(seq, change)
    after = apply(seq, change)
    tms = [t for t in (forward["tm"], reverse["tm"]) if t is not None]
    return {
        "kind": change.kind, "start": change.at + 1, "end": change.at + change.cut, "old": seq[change.at:change.at + change.cut],
        "new": change.new, "delta": change.delta, "length": len(after), "forward": forward, "reverse": reverse,
        "anneal": round(min(tms)) if tms else None, "extend_s": max(20, -(-len(after) * 30 // 1000 // 5) * 5),
        "effects": effects(seq, features, change),
    }


def label_of(result: dict) -> str:
    """A short name for the change: the first protein change, else the bases."""
    for e in result["effects"]:
        if e["label"]:
            return e["label"]
    if result["kind"] == "deletion":
        return f"d{result['start']}-{result['end']}"
    if result["kind"] == "insertion":
        return f"ins{result['start']}"
    if len(result["old"]) == 1 and len(result["new"]) == 1:
        return f"{result['old']}{result['start']}{result['new']}"
    return f"{result['start']}-{result['end']}"


# ---------------------------------------------------------------------------
# Amino acids, and silent changes
# ---------------------------------------------------------------------------


def _distance(a: str, b: str) -> int:
    return sum(x != y for x, y in zip(a, b))


def best_codon(old: str, aa: str) -> str:
    """The codon for `aa` closest to `old`: fewest bases changed, then the
    commonly used one."""
    return min(SYNONYMS[aa], key=lambda c: (_distance(c, old), c != PREFERRED.get(aa), c))


def _top(f: dict, n: int, a: int, b: int, coding_bases: str) -> tuple[int, int, str]:
    """Coding positions a..b-1 of a feature as top-strand (start, end,
    bases), 1-based."""
    s, e = int(f["start"]), int(f["end"])
    if strand(f) > 0:
        return (s + a) % n + 1, (s + b - 1) % n + 1, coding_bases
    return (e - b + 1) % n + 1, (e - a) % n + 1, reverse_complement(coding_bases)


def codon_change(sequence: str, f: dict, notation: str) -> dict:
    """"K79A" (or "79A", or "K79*") in this coding feature: the bases to
    change, as start, end and bases for `design`."""
    m = re.fullmatch(r"\s*([A-Za-z*]?)(\d+)([A-Za-z*])\s*", notation or "")
    if not m:
        raise Problem("notation")
    was, number, aa = m.group(1).upper(), int(m.group(2)), m.group(3).upper()
    if aa not in SYNONYMS:
        raise Problem("notation")
    cds = bases_of(sequence, f)
    if not 1 <= number <= len(cds) // 3:
        raise Problem("codon_outside", n=len(cds) // 3)
    codon = cds[3 * number - 3:3 * number]
    here = CODE.get(codon, "X")
    if was and was != here:
        raise Problem("not_that", number=number, aa=here)
    if here == aa:
        raise Problem("same")
    new = best_codon(codon, aa)
    start, end, top = _top(f, len(sequence), 3 * number - 3, 3 * number, new)
    if start > end:
        raise Problem("across_origin")
    return {"start": start, "end": end, "bases": top, "codon": codon, "to": new}


def _sites(enzyme) -> tuple[str, ...]:
    return tuple({enzyme.site, reverse_complement(enzyme.site)})


def _count(seq: str, enzyme) -> int:
    search = seq + seq[:len(enzyme.site) - 1]
    return sum(len(re.findall(f"(?={probe})", search)) for probe in _sites(enzyme))


def _enzymes():
    return [e for e in cloning.ENZYMES.values() if not e.outside]


def silent_sites(sequence: str, f: dict, near: tuple[int, int] | None = None, limit: int = 12) -> dict:
    """Silent changes in a coding feature that add an enzyme's site or take
    one away: changing one codon, or two side by side, to others for the
    same amino acids. `near` is a top-strand span (1-based) to look around.
    Each answer gives the bases to change, ready for `design`."""
    seq = sequence.upper()
    n = len(seq)
    cds = bases_of(seq, f)
    codons = [cds[i:i + 3] for i in range(0, len(cds) - 2, 3)]
    first, last = 0, len(codons)
    if near is not None:
        hits = []
        for k in range(len(codons)):
            a, b, _ = _top(f, n, 3 * k, 3 * k + 3, "NNN")
            if a <= near[1] and b >= near[0]:
                hits.append(k)
        if hits:
            first, last = max(0, hits[0] - NEAR_CODONS), min(len(codons), hits[-1] + NEAR_CODONS + 1)
    enzymes = _enzymes()
    counts = {e.name: _count(seq, e) for e in enzymes}
    add, remove = {}, {}
    for k in range(first, last):
        for width in (1, 2):
            if k + width > len(codons):
                continue
            here = codons[k:k + width]
            for choice in product(*[SYNONYMS.get(CODE.get(c, "X"), [c]) for c in here]):
                if list(choice) == here or (width == 2 and (choice[0] == here[0] or choice[1] == here[1])):
                    continue
                lo, hi = max(0, 3 * k - 8), min(len(cds), 3 * (k + width) + 8)
                before = cds[lo:hi]
                after = cds[lo:3 * k] + "".join(choice) + cds[3 * (k + width):hi]
                changed = sum(_distance(a, b) for a, b in zip(here, choice))
                if changed > MOST_CHANGED:
                    continue
                for enzyme in enzymes:
                    probes = _sites(enzyme)
                    had = sum(before.count(p) for p in probes)
                    has = sum(after.count(p) for p in probes)
                    if has == had:
                        continue
                    bucket = add if has > had else remove
                    start, end, top = _top(f, n, 3 * k, 3 * (k + width), "".join(choice))
                    if start > end:
                        continue
                    row = {"enzyme": enzyme.name, "site": enzyme.site, "codon": k + 1, "width": width,
                           "aa": translate("".join(here)), "from": "".join(here), "to": "".join(choice),
                           "changes": changed, "start": start, "end": end, "bases": top,
                           "cuts_now": counts[enzyme.name]}
                    best = bucket.get(enzyme.name)
                    if best is None or (changed, width) < (best["changes"], best["width"]):
                        bucket[enzyme.name] = row
    for bucket in (add, remove):
        for row in bucket.values():
            row["cuts_after"] = _count(apply(seq, Change(row["start"] - 1, row["end"] - row["start"] + 1,
                                                         row["bases"])), cloning.ENZYMES[row["enzyme"]])

    # Best first: a site that ends up cutting once (for a new one) or not at
    # all (for one taken away), then the fewest bases changed.
    def order(aim):
        return lambda row: (row["cuts_after"] != aim, row["changes"], row["enzyme"].lower())

    return {"add": sorted(add.values(), key=order(1))[:limit], "remove": sorted(remove.values(), key=order(0))[:limit]}
