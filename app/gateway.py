"""Gateway recombination: BP and LR, between two plasmids that carry a pair
of att sites.

A Gateway reaction swaps the stretch between two molecules' att sites:

- **BP** (BP Clonase): an **attB** molecule (attB1–gene–attB2) and an
  **attP** donor (attP1–ccdB–attP2, a pDONR) make an **entry clone**
  (attL1–gene–attL2, the gene on the donor's backbone) and a byproduct
  (attR1–ccdB–attR2).
- **LR** (LR Clonase): an **attL** entry clone (attL1–gene–attL2) and an
  **attR** destination (attR1–ccdB–attR2) make an **expression clone**
  (attB1–gene–attB2, the gene on the destination's backbone) and a
  byproduct (attP1–ccdB–attP2).

So each reaction takes the **gene** between the insert donor's two att
sites onto the **backbone** of the other molecule, converting the att sites
as it goes (B+P→L on the gene's new home and R on the byproduct; L+R→B and
P). Site 1 recombines with site 1 and site 2 with site 2, so the gene keeps
its direction and a ccdB cassette the reaction removes is named.

att sites are read from the plasmids' own feature annotations — the lab's
Feature library holds them (`feature_library`), named `attB1`, `attP2` and
so on. The junction is spliced at the core the two att sites of a number
share (as a Gibson overlap is), so the product reads through without a seam
whatever the exact arms of the sites in hand.

Pure: sequences in, sequences out. One pair of sites per molecule (the
standard reaction), both sites forward with site 1 before site 2 on the top
strand. app/gateway_routes.py turns a problem into one sentence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .cloning import carry_features
from .primer_records import reverse_complement

# att site → what it becomes, by reaction. BP joins B and P; LR joins L and R.
REACTIONS = {
    "BP": {"insert": "B", "backbone": "P", "product": "L", "byproduct": "R"},
    "LR": {"insert": "L", "backbone": "R", "product": "B", "byproduct": "P"},
}
ATT_RE = re.compile(r"^att([BPLR])([12])$", re.IGNORECASE)
MIN_CORE = 7           # the att overlap is a 7 bp core; a shorter match is chance


class Problem(ValueError):
    def __init__(self, code: str, **details):
        super().__init__(code)
        self.code, self.details = code, details


@dataclass
class Site:
    kind: str          # "B", "P", "L" or "R"
    number: int        # 1 or 2
    start: int         # 0-based, inclusive
    end: int
    name: str

    @property
    def seq_slice(self):
        return self.start, self.end


def att_sites(features) -> list[Site]:
    """The att sites among a plasmid's annotations, by their names."""
    out = []
    for f in features or []:
        if not isinstance(f, dict):
            continue
        m = ATT_RE.match((f.get("name") or "").strip())
        if not m:
            continue
        try:
            start, end = int(f["start"]), int(f["end"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append(Site(m.group(1).upper(), int(m.group(2)), start, end, f.get("name", "")))
    return out


def _pair(sites: list[Site], kind: str) -> tuple[Site, Site] | None:
    """The site 1 and site 2 of one kind, each once."""
    ones = [s for s in sites if s.kind == kind and s.number == 1]
    twos = [s for s in sites if s.kind == kind and s.number == 2]
    if len(ones) == 1 and len(twos) == 1:
        return ones[0], twos[0]
    return None


def reaction_of(a_sites: list[Site], b_sites: list[Site]) -> str | None:
    """Which reaction the two plasmids' sites allow, if any."""
    for name, r in REACTIONS.items():
        if (_pair(a_sites, r["insert"]) and _pair(b_sites, r["backbone"])) or \
           (_pair(b_sites, r["insert"]) and _pair(a_sites, r["backbone"])):
            return name
    return None


def _longest_common(a: str, b: str) -> tuple[int, int, int]:
    """The longest run both share: (start in a, start in b, length)."""
    best = (0, 0, 0)
    bidx: dict[str, list[int]] = {}
    for j, ch in enumerate(b):
        bidx.setdefault(ch, []).append(j)
    for i in range(len(a)):
        for j in bidx.get(a[i], ()):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            if k > best[2]:
                best = (i, j, k)
    return best


@dataclass
class Molecule:
    sequence: str
    circular: bool
    features: list
    name: str = ""


def _between(seq: str, first: Site, second: Site) -> tuple[str, int]:
    """The stretch strictly between site 1's end and site 2's start, and
    where it begins. Both sites forward, site 1 before site 2."""
    if not (first.start <= first.end < second.start <= second.end):
        raise Problem("layout", name=first.name)
    return seq[first.end + 1:second.start], first.end + 1


def _hybrid(left_att: str, right_att: str) -> str:
    """One att site spliced from two parents' att sites of the same number:
    the left parent up to the shared core, then the right parent from it.
    Seamless, because the core is common to both."""
    i, j, length = _longest_common(left_att, right_att)
    if length < MIN_CORE:
        raise Problem("no_core")
    return left_att[:i + length] + right_att[j + length:]


def _carry(features, parent_len: int, circular: bool, start: int, length: int, shift: int) -> list[dict]:
    """A parent's features on a piece of it, moved to where the piece now
    sits in the product (its own att sites left out — the product writes
    fresh ones)."""
    out = []
    for f in carry_features(features, parent_len, circular, start, length):
        if ATT_RE.match((f.get("name") or "").strip()):
            continue
        out.append(dict(f, start=f["start"] + shift, end=f["end"] + shift))
    return out


def _att_feature(name: str, start: int, length: int) -> dict:
    return {"name": name, "type": "protein_bind", "start": start, "end": start + length - 1,
            "direction": 1, "color": "#f59e0b", "kind": "feature"}


def _cassette_names(features, parent_len, circular, start, length) -> list[str]:
    """The named features on the stretch a reaction replaces (its ccdB and
    any selection marker), so the routes can say what is removed."""
    names = []
    for f in carry_features(features, parent_len, circular, start, length):
        name = (f.get("name") or "").strip()
        if name and not ATT_RE.match(name) and name not in names:
            names.append(name)
    return names


def _build(insert: Molecule, backbone: Molecule, r: dict) -> dict:
    """One product: the insert donor's gene between its att sites, on the
    backbone donor's backbone, with the two att sites converted."""
    i1, i2 = _pair(att_sites(insert.features), r["insert"])
    b1, b2 = _pair(att_sites(backbone.features), r["backbone"])
    gene, gene_at = _between(insert.sequence, i1, i2)
    # The backbone is the stretch from backbone site 2's end, round the
    # origin, to backbone site 1's start.
    bl = len(backbone.sequence)
    back_start = (b2.end + 1) % bl
    back_len = (b1.start - back_start) % bl
    backbone_seq = (backbone.sequence + backbone.sequence)[back_start:back_start + back_len]

    att1 = _hybrid(backbone.sequence[b1.start:b1.end + 1], insert.sequence[i1.start:i1.end + 1])
    att2 = _hybrid(insert.sequence[i2.start:i2.end + 1], backbone.sequence[b2.start:b2.end + 1])
    product = att1 + gene + att2 + backbone_seq

    pos1 = 0
    gene_pos = len(att1)
    pos2 = gene_pos + len(gene)
    back_pos = pos2 + len(att2)
    features = [_att_feature(f"att{r['product']}1", pos1, len(att1))]
    features += _carry(insert.features, len(insert.sequence), insert.circular, gene_at, len(gene), gene_pos)
    features.append(_att_feature(f"att{r['product']}2", pos2, len(att2)))
    features += _carry(backbone.features, bl, backbone.circular, back_start, back_len, back_pos)
    removed = _cassette_names(backbone.features, bl, backbone.circular,
                              (b1.end + 1) % bl, (b2.start - (b1.end + 1)) % bl)
    return {"sequence": product, "length": len(product), "features": sorted(features, key=lambda a: a["start"]),
            "removed": removed, "kind": r["product"]}


def recombine(a: Molecule, b: Molecule) -> dict:
    """The Gateway reaction between two plasmids: the product (the entry or
    expression clone), the byproduct, and what the reaction removed. Raises
    Problem when the sites don't make a reaction."""
    for m in (a, b):
        if not m.sequence:
            raise Problem("no_sequence", name=m.name)
    a_sites, b_sites = att_sites(a.features), att_sites(b.features)
    reaction = reaction_of(a_sites, b_sites)
    if reaction is None:
        raise Problem("no_reaction")
    r = REACTIONS[reaction]
    # The insert donor is whichever molecule carries the reaction's insert sites.
    if _pair(a_sites, r["insert"]) and _pair(b_sites, r["backbone"]):
        insert, backbone = a, b
    else:
        insert, backbone = b, a
    product = _build(insert, backbone, r)
    byproduct = _build(backbone, insert, {"insert": r["backbone"], "backbone": r["insert"],
                                          "product": r["byproduct"], "byproduct": r["product"]})
    return {"reaction": reaction, "insert": insert.name, "backbone": backbone.name,
            "product": product, "byproduct": byproduct, "removed": product["removed"]}


# The canonical att site sequences, for the Feature library and the demo lab.
# Short cores are enough: the reaction splices at the part two sites share,
# and a lab's own plasmids carry the full sites.
ATT_SEQUENCES = {
    "attB1": "ACAAGTTTGTACAAAAAAGCAGGCT",
    "attB2": "ACCCAGCTTTCTTGTACAAAGTGGT",
    "attP1": "ACAAGTTTGTACAAAAAAGCAGGCTCCGCGGCCGC",
    "attP2": "GCGGCCGCCCACCCAGCTTTCTTGTACAAAGTGGT",
    "attL1": "CAAATAATGATTTTATTTTGACTGATAGTGACCTGTTCGTTGCAACAAATTGATAAGCAATGCTTTTTTATAATGCCAACTTTGTACAAAAAAGCAGGCT",
    "attL2": "ACCCAGCTTTCTTGTACAAAGTGGTCCCC",
    "attR1": "ACAAGTTTGTACAAAAAAGCAGGCTCCGCGGCCGC",
    "attR2": "GCGGCCGCCCACCCAGCTTTCTTGTACAAAGTGGT",
}
