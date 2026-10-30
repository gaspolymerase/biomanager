"""Virtual digests: where the lab's enzymes cut a plasmid, the pieces they
leave, how those run on an agarose gel, and which enzymes would tell a right
clone from a wrong one in a single lane.

Pure, like `cloning.py` whose enzymes it uses: a sequence in, numbers and
names out. The plasmid page's Digest tab (`/plasmids/<id>/digest.json`,
app/app.py) puts words to them.

- **Cuts.** Each enzyme's sites on both strands, across the origin of a
  circular plasmid; a cut is written as the top-strand base it follows
  (1-based), as a supplier's map shows it.
- **Methylation.** DNA grown in an ordinary *E. coli* (DH5α, TOP10) is
  Dam-methylated at GATC and Dcm-methylated at CCWGG. A few enzymes can't
  cut a site that one of those overlaps (XbaI's TCTAGA followed by TC, say):
  such a site is listed but not cut while `methylated` is on.
- **The gel.** A band runs a distance that falls with the log of its size
  over the range the agarose separates, and bunches up beyond it. Two bands
  closer than `ONE_BAND` run as one; a band's brightness follows its mass,
  so a large piece glows more than a small one.
- **Suggestions.** Single enzymes and pairs that leave two to four pieces,
  all within the gel's range and well apart, preferring enzymes the lab has
  in its freezer, and, when the plasmid was made from a backbone with a
  sequence, a pattern that differs from the backbone's.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from itertools import combinations

from .cloning import ENZYMES, Enzyme
from .primer_records import reverse_complement

# Enzymes of ours that a Dam or Dcm methyl in their site stops (NEB's
# tables): which methylation, and the word it sits in.
DAM_BLOCKED = {"XbaI", "ClaI", "BspEI"}
DCM_BLOCKED = {"StuI", "ApaI"}

# The agarose percentages offered, each with the sizes it separates well.
AGAROSE = {
    "0.7": (800, 12000),
    "0.8": (700, 10000),
    "1": (500, 10000),
    "1.2": (400, 7000),
    "1.5": (200, 3000),
    "2": (100, 2000),
}
DEFAULT_AGAROSE = "1"

# Ladders, largest first, with the brighter reference bands.
LADDERS = {
    "1kb_plus": ("1 kb Plus", (10002, 8001, 6001, 5001, 4001, 3001, 2000, 1500, 1200, 1000, 900, 800, 700,
                               600, 500, 400, 300, 200, 100), (3001, 1000, 500)),
    "1kb": ("1 kb", (10002, 8001, 6001, 5001, 4001, 3001, 2000, 1500, 1000, 500), (3001,)),
    "100bp": ("100 bp", (1517, 1200, 1000, 900, 800, 700, 600, 500, 400, 300, 200, 100), (1000, 500)),
}
DEFAULT_LADDER = "1kb_plus"

# On the gel's 0–1 scale (top to bottom): closer than this runs as one band;
# a check wants its bands at least WELL_APART.
ONE_BAND = 0.018
WELL_APART = 0.04
# An uncut plasmid: supercoiled runs ahead of its size, nicked (open circle) behind.
SUPERCOILED, NICKED = 0.68, 1.7


@dataclass
class Site:
    enzyme: str
    cut: int                 # the top-strand base the cut follows, 1-based
    start: int               # where the recognition site starts, 0-based
    blocked: str = ""        # "dam" or "dcm" when methylation stops it


@dataclass
class Piece:
    start: int               # 1-based, the first base of the piece
    end: int                 # 1-based, its last base (it may run across the origin)
    length: int
    left: str = ""           # the enzyme at each end
    right: str = ""
    holds: list = field(default_factory=list)   # names of the features wholly inside


def _clean(sequence: str) -> str:
    return re.sub(r"[^ACGTN]", "N", (sequence or "").upper())


def _methyl_words(seq: str, circular: bool) -> tuple[set[int], set[int]]:
    """The 0-based positions of methylated bases on either strand: Dam's
    adenine in GATC (top A, and the bottom strand's A across from the T) and
    Dcm's inner cytosine in CCWGG (top, and the bottom's across from the G)."""
    n = len(seq)
    search = seq + (seq[:4] if circular else "")
    dam, dcm = set(), set()
    for m in re.finditer(r"(?=GATC)", search):
        if m.start() < n:
            dam.update({(m.start() + 1) % n, (m.start() + 2) % n})
    for m in re.finditer(r"(?=CC[AT]GG)", search):
        if m.start() < n:
            dcm.update({(m.start() + 1) % n, (m.start() + 3) % n})
    return dam, dcm


def sites_of(sequence: str, circular: bool, enzyme: Enzyme, methyl=None) -> list[Site]:
    """Every place this enzyme's site is, on either strand, with its cut.
    `methyl` is `_methyl_words`' answer, to mark the sites methylation blocks."""
    seq = _clean(sequence)
    n, size = len(seq), len(enzyme.site)
    if not n or size > n:
        return []
    search = seq + (seq[:size - 1] if circular else "")
    found: dict[int, Site] = {}
    for probe, reverse in ((enzyme.site, False), (reverse_complement(enzyme.site), True)):
        if reverse and probe == enzyme.site:
            continue
        at = search.find(probe)
        while at != -1 and at < n:
            top = (at + size - enzyme.bottom) if reverse else (at + enzyme.top)
            if circular or (0 <= top <= n and 0 <= top + enzyme.overhang <= n):
                cut = top % n if circular else top
                blocked = ""
                if methyl is not None:
                    span = {(at + i) % n for i in range(size)}
                    if enzyme.name in DAM_BLOCKED and span & methyl[0]:
                        blocked = "dam"
                    elif enzyme.name in DCM_BLOCKED and span & methyl[1]:
                        blocked = "dcm"
                # A cut at the very origin is written as after the last base.
                found.setdefault(cut, Site(enzyme.name, cut or n, at, blocked))
            at = search.find(probe, at + 1)
    return sorted(found.values(), key=lambda s: s.cut)


def enzyme_table(sequence: str, circular: bool, methylated: bool = True) -> list[dict]:
    """Every enzyme we know, with its sites on this plasmid, the number it
    actually cuts (methylation aside) and whether any site is blocked."""
    seq = _clean(sequence)
    methyl = _methyl_words(seq, circular) if methylated else None
    rows = []
    for name, enzyme in sorted(ENZYMES.items(), key=lambda kv: kv[0].lower()):
        if enzyme.outside:
            continue                     # Type IIS: for Golden Gate, not a check digest
        sites = sites_of(seq, circular, enzyme, methyl)
        rows.append({
            "name": name, "site": enzyme.site, "label": enzyme.label,
            "ends": "blunt" if enzyme.overhang == 0 else ("5" if enzyme.overhang > 0 else "3"),
            "sites": [{"cut": s.cut, "blocked": s.blocked} for s in sites],
            "cuts": sum(1 for s in sites if not s.blocked),
            "blocked": sorted({s.blocked for s in sites if s.blocked}),
        })
    return rows


def cut_positions(sequence: str, circular: bool, names, methylated: bool = True) -> list[tuple[int, str]]:
    """The cuts these enzymes make, as (position, enzyme), along the plasmid."""
    seq = _clean(sequence)
    methyl = _methyl_words(seq, circular) if methylated else None
    out: dict[int, str] = {}
    for name in names:
        enzyme = ENZYMES.get(name)
        if enzyme is None or enzyme.outside:
            continue
        for s in sites_of(seq, circular, enzyme, methyl):
            if not s.blocked:
                out.setdefault(s.cut, name)
    return sorted(out.items())


def _num(feature: dict, key: str) -> int:
    try:
        return int(feature.get(key, 0))
    except (TypeError, ValueError):
        return 0


def _inside(feature: dict, start0: int, length: int, n: int) -> bool:
    """Whether a feature (0-based start, end exclusive) lies wholly in the
    piece that starts at `start0` (0-based) and runs `length` bases on."""
    try:
        fs, fe = int(feature.get("start", 0)), int(feature.get("end", 0))
    except (TypeError, ValueError):
        return False
    if fe <= fs:
        fe += n                          # a feature across the origin
    a = (fs - start0) % n
    return a + (fe - fs) <= length


def pieces(length: int, circular: bool, cuts: list[tuple[int, str]], features=()) -> list[Piece]:
    """The pieces the cuts leave, in order along the plasmid. A circular
    plasmid cut once is one piece the length of the plasmid; uncut, none."""
    if not length or not cuts:
        return []
    named = [f for f in features or () if isinstance(f, dict) and f.get("name")]
    out = []
    if circular:
        for i, (at, enzyme) in enumerate(cuts):
            nxt, other = cuts[(i + 1) % len(cuts)]
            size = (nxt - at) % length or length
            start0 = at % length
            out.append(Piece(start0 + 1, (start0 + size - 1) % length + 1, size, enzyme, other,
                             [f["name"] for f in named if _inside(f, start0, size, length)]))
    else:
        ends = [(0, ""), *cuts, (length, "")]
        for (a, left), (b, right) in zip(ends, ends[1:]):
            if b > a:
                out.append(Piece(a + 1, b, b - a, left, right,
                                 [f["name"] for f in named if a <= _num(f, "start") and _num(f, "end") <= b]))
    return out


# ---------------------------------------------------------------- the gel

def position(bp: float, agarose: str = DEFAULT_AGAROSE) -> float:
    """Where a band of `bp` runs, 0 at the wells to 1 at the gel's foot.
    Within the agarose's range the distance falls with log size; beyond it
    bands bunch up toward the wells or the dye front."""
    lo, hi = AGAROSE.get(agarose, AGAROSE[DEFAULT_AGAROSE])
    top, bottom = 0.08, 0.9
    span = math.log10(hi) - math.log10(lo)
    x = (math.log10(hi) - math.log10(max(bp, 1))) / span       # 0 at hi, 1 at lo
    if x < 0:
        x = -0.12 * (1 - math.exp(x * 3))                        # above the range: crowd under the well
    elif x > 1:
        x = 1 + 0.1 * (1 - math.exp(-(x - 1) * 3))               # below: toward the dye front
    return round(top + x * (bottom - top), 4)


def bands(sizes, agarose: str = DEFAULT_AGAROSE) -> list[dict]:
    """The bands these piece sizes make: pieces closer than ONE_BAND run as
    one, brighter for it. Brightness is relative mass (largest = 1)."""
    rows = sorted(((position(s, agarose), s) for s in sizes if s > 0))
    out: list[dict] = []
    for y, size in rows:
        if out and y - out[-1]["y"] < ONE_BAND:
            out[-1]["sizes"].append(size)
            continue
        out.append({"y": y, "sizes": [size]})
    heaviest = max((sum(b["sizes"]) for b in out), default=1)
    for b in out:
        b["mass"] = round(sum(b["sizes"]) / heaviest, 3)
        b["y"] = round(sum(position(s, agarose) for s in b["sizes"]) / len(b["sizes"]), 4)
    return out


def ladder(key: str, agarose: str = DEFAULT_AGAROSE) -> dict:
    name, sizes, bright = LADDERS.get(key, LADDERS[DEFAULT_LADDER])
    return {"key": key if key in LADDERS else DEFAULT_LADDER, "name": name,
            "bands": [{"y": position(s, agarose), "size": s, "bright": s in bright} for s in sizes]}


def uncut(length: int, circular: bool, agarose: str = DEFAULT_AGAROSE) -> list[dict]:
    """An uncut plasmid's bands: supercoiled ahead, nicked behind (circular);
    a linear molecule runs at its size."""
    if not length:
        return []
    if not circular:
        return bands([length], agarose)
    return [{"y": position(length * SUPERCOILED, agarose), "sizes": [length], "mass": 1.0, "form": "supercoiled"},
            {"y": position(length * NICKED, agarose), "sizes": [length], "mass": 0.35, "form": "nicked"}]


def too_close(sizes, agarose: str = DEFAULT_AGAROSE) -> list[list[int]]:
    """The groups of pieces that run as one band."""
    return [sorted(b["sizes"], reverse=True) for b in bands(sizes, agarose) if len(b["sizes"]) > 1]


def off_the_gel(sizes, agarose: str = DEFAULT_AGAROSE) -> list[int]:
    lo, hi = AGAROSE.get(agarose, AGAROSE[DEFAULT_AGAROSE])
    return sorted((s for s in sizes if s < lo * 0.6 or s > hi * 1.3), reverse=True)


# ---------------------------------------------------------------- suggestions

def _spread(sizes, agarose) -> float:
    ys = sorted(position(s, agarose) for s in sizes)
    return min((b - a for a, b in zip(ys, ys[1:])), default=1.0)


def _pattern_differs(mine, theirs, agarose) -> bool:
    """Whether two lanes would look different: a band in one with nothing
    near it in the other."""
    a = [position(s, agarose) for s in mine]
    b = [position(s, agarose) for s in theirs]
    if len(bands(mine, agarose)) != len(bands(theirs, agarose)):
        return True
    return any(min((abs(x - y) for y in b), default=1) > WELL_APART for x in a) or \
        any(min((abs(x - y) for x in a), default=1) > WELL_APART for y in b)


def suggest(sequence: str, circular: bool, features=(), freezer=(), parent: str | None = None,
            parent_circular: bool = True, methylated: bool = True, agarose: str = DEFAULT_AGAROSE,
            limit: int = 3) -> list[dict]:
    """Enzymes, one or two, whose pieces would check this plasmid in one
    lane: two to four pieces, all on the gel and well apart. `freezer` is
    the enzymes the lab has; `parent` a backbone's sequence to tell apart."""
    seq = _clean(sequence)
    n = len(seq)
    if not n:
        return []
    lo, hi = AGAROSE.get(agarose, AGAROSE[DEFAULT_AGAROSE])
    table = {row["name"]: row for row in enzyme_table(seq, circular, methylated)}
    usable = [name for name, row in table.items() if 1 <= row["cuts"] <= 3]
    have = {e.lower() for e in freezer}
    cds = {f.get("name") for f in features or () if isinstance(f, dict)
           and str(f.get("type", "")).lower() in ("cds", "gene") and f.get("name")}
    parent_seq = _clean(parent) if parent else ""

    options = [(name,) for name in usable] + [pair for pair in combinations(usable, 2)
                                              if table[pair[0]]["cuts"] + table[pair[1]]["cuts"] <= 4]
    scored = []
    for combo in options:
        cuts = cut_positions(seq, circular, combo, methylated)
        made = pieces(n, circular, cuts, features)
        sizes = [p.length for p in made]
        if not 2 <= len(sizes) <= 4 or min(sizes) < lo * 0.5 or max(sizes) > hi:
            continue
        spread = _spread(sizes, agarose)
        if spread < WELL_APART:
            continue
        in_freezer = all(e.lower() in have for e in combo)
        differs = None
        if parent_seq:
            theirs = [p.length for p in pieces(len(parent_seq), parent_circular,
                                               cut_positions(parent_seq, parent_circular, combo, methylated))] \
                or [len(parent_seq)]
            differs = _pattern_differs(sizes, theirs, agarose)
            if not differs:
                continue
        drops = sorted({h for p in made for h in p.holds if h in cds and p.length < n * 0.6})
        score = (spread * 10 + (3 if in_freezer else 0) + (1 if len(combo) == 1 else 0)
                 + (1.5 if differs else 0) + (1 if drops else 0) - abs(len(sizes) - 2.5) * 0.3)
        scored.append((score, {
            "enzymes": list(combo), "sizes": sorted(sizes, reverse=True), "in_freezer": in_freezer,
            "differs": differs, "parent_sizes": sorted(theirs, reverse=True) if parent_seq else None,
            "drops": drops,
        }))
    scored.sort(key=lambda s: -s[0])
    return [s for _score, s in scored[:limit]]


def in_freezer(enzyme_names, item_names) -> dict[str, str]:
    """Which enzymes the lab has, from the names of its reagents: "EcoRI-HF"
    and "NotI (NEB R3189)" count as EcoRI and NotI. Answers {enzyme: the
    item's name}."""
    found = {}
    for name in enzyme_names:
        word = re.compile(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![a-z0-9])", re.IGNORECASE)
        for item in item_names:
            if word.search(item or ""):
                found[name] = item
                break
    return found
