"""Cutting plasmids up, and putting the pieces back together.

Three ways a lab makes a new plasmid out of the ones it already has:

- **restriction digest and ligation** — cut backbone and insert with the
  same enzymes and let the sticky ends find each other;
- **Gibson / NEBuilder HiFi** — give neighbouring fragments the same
  twenty or thirty bases where they meet, by PCR, and the enzyme mix joins
  them;
- **Golden Gate** — a Type IIS enzyme cuts outside its own recognition
  site, so each fragment can be given the four-base overhang you choose and
  the whole assembly goes together in one tube.

Everything in this module is pure: sequences in, sequences out. No
database, no request, no gettext — a problem comes back as a small dict the
routes turn into one sentence in the reader's language.

**How a cut is written down.** A cut is two staggered nicks: `at` is the
bond the enzyme breaks on the top strand (0-based: the bond before that
base) and `overhang` is how far the other strand's nick sits from it,
signed — positive leaves a 5′ overhang, negative a 3′ one, zero is blunt.
The overhang belongs to the enzyme, not to which strand its site was found
on, so a site read backwards cuts with the same stagger.

**How a fragment is written down.** `sequence` is the fragment's top strand
between the two top-strand nicks, and each end carries the bases of its
overhang *as the top strand reads them* (5′→3′), which is the four-base
word people mean by "the overhang" in a Golden Gate. For a 5′ overhang
those bases sit at the start of the downstream fragment's `sequence`; for a
3′ overhang, at the end of the upstream one's. Either way a product's top
strand is the fragments' top strands written one after another, with
nothing counted twice and nothing lost — which is what makes assembling
them a `"".join`.

Two ends go together when they are the same kind and their bases are the
same word: two fragments from the same enzyme share a cut, so they always
agree, and two from different enzymes agree exactly when the enzymes leave
compatible ends (BamHI and BglII both leave GATC).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .inventory_service import primer_tm
from .primer_records import reverse_complement

# ---------------------------------------------------------------------------
# The enzymes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Enzyme:
    """`top` and `bottom` are how many bases from the start of the
    recognition site each strand is cut, as a supplier writes it: EcoRI is
    G^AATTC, so 1 and 5; BsaI is GGTCTC(1/5), so 7 and 11."""
    name: str
    site: str
    top: int
    bottom: int

    @property
    def overhang(self) -> int:
        """Signed: 4 for EcoRI's 5′ AATT, −4 for PstI's 3′ TGCA, 0 blunt."""
        return self.bottom - self.top

    @property
    def outside(self) -> bool:
        """A Type IIS enzyme: it cuts beyond its own site, so the overhang
        is whatever sequence you put there."""
        return self.top > len(self.site) or self.top < 0

    @property
    def label(self) -> str:
        """"EcoRI G^AATTC" or "BsaI GGTCTC(1/5)", as a catalogue prints it."""
        if self.outside:
            return f"{self.name} {self.site}({self.top - len(self.site)}/{self.bottom - len(self.site)})"
        return f"{self.name} {self.site[:self.top]}^{self.site[self.top:]}"


def _enzymes(*rows: tuple[str, str, int, int]) -> dict[str, Enzyme]:
    return {name: Enzyme(name, site, top, bottom) for name, site, top, bottom in rows}


# The ones a molecular biology lab has in the door of its freezer, plus the
# Type IIS enzymes Golden Gate is done with. Sites are 5′→3'; an enzyme is
# found on both strands, so the palindromic ones need listing only once.
ENZYMES = _enzymes(
    # Six-cutters leaving 5′ overhangs
    ("AgeI", "ACCGGT", 1, 5), ("AscI", "GGCGCGCC", 2, 6), ("AvrII", "CCTAGG", 1, 5),
    ("BamHI", "GGATCC", 1, 5), ("BglII", "AGATCT", 1, 5), ("BsrGI", "TGTACA", 1, 5),
    ("ClaI", "ATCGAT", 2, 4), ("EcoRI", "GAATTC", 1, 5), ("HindIII", "AAGCTT", 1, 5),
    ("MluI", "ACGCGT", 1, 5), ("NcoI", "CCATGG", 1, 5), ("NdeI", "CATATG", 2, 4),
    ("NheI", "GCTAGC", 1, 5), ("NotI", "GCGGCCGC", 2, 6), ("PacI", "TTAATTAA", 5, 3),
    ("SalI", "GTCGAC", 1, 5), ("SpeI", "ACTAGT", 1, 5), ("XbaI", "TCTAGA", 1, 5),
    ("XhoI", "CTCGAG", 1, 5), ("BspEI", "TCCGGA", 1, 5), ("AflII", "CTTAAG", 1, 5),
    # Leaving 3′ overhangs
    ("ApaI", "GGGCCC", 5, 1), ("KpnI", "GGTACC", 5, 1), ("PstI", "CTGCAG", 5, 1),
    ("SacI", "GAGCTC", 5, 1), ("SbfI", "CCTGCAGG", 6, 2), ("SphI", "GCATGC", 5, 1),
    # Blunt
    ("EcoRV", "GATATC", 3, 3), ("PmeI", "GTTTAAAC", 4, 4), ("PvuII", "CAGCTG", 3, 3),
    ("ScaI", "AGTACT", 3, 3), ("SmaI", "CCCGGG", 3, 3), ("SnaBI", "TACGTA", 3, 3),
    ("StuI", "AGGCCT", 3, 3),
    # Type IIS: the site stays outside the fragment, the overhang is yours
    ("BbsI", "GAAGAC", 8, 12), ("BsaI", "GGTCTC", 7, 11), ("BsmBI", "CGTCTC", 7, 11),
    ("SapI", "GCTCTTC", 8, 11), ("Esp3I", "CGTCTC", 7, 11), ("AarI", "CACCTGC", 11, 15),
)

# What Golden Gate is done with here. BsaI for parts, BsmBI and BbsI for the
# level above, so a part's own BsaI sites survive being moved.
GOLDEN_GATE_ENZYMES = ("BsaI", "BsmBI", "BbsI", "SapI")

# ---------------------------------------------------------------------------
# Ends, cuts and fragments
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class End:
    """One end of a fragment: `kind` is "5", "3" or "blunt", and `bases` is
    the overhang as the top strand reads it (empty when blunt)."""
    kind: str = "blunt"
    bases: str = ""

    def fits(self, other: "End") -> bool:
        return self.kind == other.kind and self.bases == other.bases

    @property
    def text(self) -> str:
        """"AATT (5′)", "TGCA (3′)" or "" — what a junction shows."""
        if self.kind == "blunt":
            return ""
        return f"{self.bases} ({self.kind}′)"


BLUNT = End()


@dataclass(frozen=True)
class Cut:
    at: int             # the bond the top strand breaks at, 0-based
    overhang: int       # the other strand's nick, relative to it
    enzyme: str = ""

    def end(self, sequence: str, circular: bool) -> End:
        if not self.overhang:
            return BLUNT
        start = min(self.at, self.at + self.overhang)
        return End("5" if self.overhang > 0 else "3",
                   _span(sequence, start, abs(self.overhang), circular))


@dataclass
class Fragment:
    """A piece of DNA on the bench: its top strand, the two ends it offers,
    the features it brought with it, and where it came from."""
    sequence: str
    left: End = BLUNT
    right: End = BLUNT
    name: str = ""
    features: list[dict] = field(default_factory=list)
    source: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.sequence)


def _span(sequence: str, start: int, length: int, circular: bool) -> str:
    """`length` bases from `start`, round the origin when it is circular."""
    n = len(sequence)
    if not n:
        return ""
    start %= n
    if start + length <= n:
        return sequence[start:start + length]
    if not circular:
        return sequence[start:]
    whole = sequence * (length // n + 2)
    return whole[start:start + length]


def flip(fragment: Fragment) -> Fragment:
    """The same piece of DNA the other way round — its bottom strand read
    5′→3′. The ends swap and keep their kind; a 5′ overhang is still a 5′
    overhang seen from the other side."""
    seq = fragment.sequence
    # The bottom strand runs from the left end's other nick to the right
    # end's, so it drops a 5′ overhang this strand carries and picks up the
    # one the neighbour was carrying.
    front = fragment.left.bases if fragment.left.kind == "3" else ""
    back = fragment.right.bases if fragment.right.kind == "5" else ""
    cut_front = len(fragment.left.bases) if fragment.left.kind == "5" else 0
    cut_back = len(fragment.right.bases) if fragment.right.kind == "3" else 0
    middle = front + seq[cut_front:len(seq) - cut_back] + back
    turned = reverse_complement(middle)
    return Fragment(
        sequence=turned,
        left=End(fragment.right.kind, reverse_complement(fragment.right.bases)),
        right=End(fragment.left.kind, reverse_complement(fragment.left.bases)),
        name=fragment.name,
        features=_flip_features(fragment.features, len(turned)),
        source={**fragment.source, "flipped": not fragment.source.get("flipped")},
    )


def _flip_features(features: list[dict], length: int) -> list[dict]:
    out = []
    for f in features:
        start, end = int(f["start"]), int(f["end"])
        turned = dict(f)
        turned["start"], turned["end"] = length - 1 - end, length - 1 - start
        turned["direction"] = -1 if int(f.get("direction", 1) or 1) >= 0 else 1
        if f.get("locations"):
            turned["locations"] = [{"start": length - 1 - int(p["end"]), "end": length - 1 - int(p["start"])}
                                   for p in reversed(f["locations"])]
        out.append(turned)
    return sorted(out, key=lambda a: a["start"])


# ---------------------------------------------------------------------------
# Carrying features from a plasmid onto a piece of it
# ---------------------------------------------------------------------------

PARTIAL = "cut by the assembly"


def carry_features(features, parent_length: int, circular: bool, start: int, length: int) -> list[dict]:
    """The annotations of a plasmid, on the piece of it that runs `length`
    bases from `start`, at their new coordinates. One the piece cuts through
    is kept as the part that is there, with a note saying so; one that would
    come in two pieces (only possible when the piece is the whole circle)
    keeps its longer half."""
    out = []
    for f in features or []:
        if not isinstance(f, dict):
            continue
        try:
            fs, fe = int(f["start"]), int(f["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if parent_length <= 0:
            continue
        size = (fe - fs) % parent_length + 1 if circular else fe - fs + 1
        if size <= 0:
            continue
        begin = (fs - start) % parent_length if circular else fs - start
        piece = _longest_inside(begin, size, length, parent_length if circular else 0)
        if piece is None:
            continue
        at, kept = piece
        moved = dict(f)
        moved["start"], moved["end"] = at, at + kept - 1
        moved.pop("locations", None)        # a join no longer holds after a cut
        if kept < size:
            moved["notes"] = _note(f.get("notes"), PARTIAL)
        out.append(moved)
    return sorted(out, key=lambda a: (a["start"], a["end"]))


def _longest_inside(begin: int, size: int, length: int, parent_length: int):
    """The longest run of [begin, begin+size) that lies in [0, length), as
    (start, length), or None. `parent_length` wraps it; 0 does not."""
    runs = []
    for offset in ((0, -parent_length) if parent_length else (0,)):
        lo, hi = max(begin + offset, 0), min(begin + offset + size, length)
        if hi > lo:
            runs.append((lo, hi - lo))
    return max(runs, key=lambda r: r[1]) if runs else None


def _note(notes, text: str):
    """Add a /note qualifier, keeping whatever the annotation already had."""
    if isinstance(notes, dict):
        joined = dict(notes)
        joined["note"] = [*joined.get("note", []), text]
        return joined
    if isinstance(notes, str) and notes.strip():
        return f"{notes} · {text}"
    return text


# ---------------------------------------------------------------------------
# Digesting
# ---------------------------------------------------------------------------


def sites(sequence: str, circular: bool, enzyme: Enzyme) -> list[Cut]:
    """Every cut this enzyme makes, from its site on either strand. A cut
    whose stagger falls off the end of a linear molecule is not made: the
    enzyme has nothing to hold on the far side."""
    seq = (sequence or "").upper()
    n = len(seq)
    size = len(enzyme.site)
    if not n or size > n:
        return []
    # A circular molecule has its origin in the middle of nothing: read the
    # first bases again so a site sitting across it is found.
    search = seq + (seq[:size - 1] if circular else "")
    found = []
    for probe, reverse in ((enzyme.site, False), (reverse_complement(enzyme.site), True)):
        if reverse and probe == enzyme.site:
            continue                        # palindromic: the same cut twice
        at = search.find(probe)
        while at != -1 and at < n:
            top = (at + size - enzyme.bottom) if reverse else (at + enzyme.top)
            if circular:
                found.append(Cut(top % n, enzyme.overhang, enzyme.name))
            elif 0 <= top <= n and 0 <= top + enzyme.overhang <= n:
                found.append(Cut(top, enzyme.overhang, enzyme.name))
            at = search.find(probe, at + 1)
    return found


def cuts(sequence: str, circular: bool, enzyme_names) -> list[Cut]:
    """Every cut the chosen enzymes make, in order along the top strand.
    Two enzymes cutting the same bond count once."""
    found = []
    for name in enzyme_names:
        enzyme = ENZYMES.get(name)
        if enzyme is not None:
            found += sites(sequence, circular, enzyme)
    seen, out = set(), []
    for cut in sorted(found, key=lambda c: (c.at, c.enzyme)):
        if cut.at not in seen:
            seen.add(cut.at)
            out.append(cut)
    return out


def digest(sequence: str, circular: bool, enzyme_names, features=None, name: str = "") -> list[Fragment]:
    """The pieces these enzymes leave, in the order they sit on the plasmid.
    A circular plasmid cut once comes back as one linear fragment; an uncut
    one comes back empty, because nothing was done to it."""
    seq = (sequence or "").upper()
    n = len(seq)
    made = cuts(seq, circular, enzyme_names)
    if not n or not made:
        return []
    pieces = []
    if circular:
        pairs = [(made[i], made[(i + 1) % len(made)]) for i in range(len(made))]
    else:
        ends = [Cut(0, 0), *made, Cut(n, 0)]
        pairs = list(zip(ends, ends[1:]))
    for left, right in pairs:
        length = (right.at - left.at) % n if circular else right.at - left.at
        if circular and len(made) == 1:
            length = n
        if length <= 0:
            continue
        pieces.append(Fragment(
            sequence=_span(seq, left.at, length, circular),
            left=left.end(seq, circular), right=right.end(seq, circular),
            name=name,
            features=carry_features(features, n, circular, left.at, length),
            source={"start": left.at, "length": length,
                    "enzymes": [e for e in (left.enzyme, right.enzyme) if e]},
        ))
    return pieces


def released(fragments: list[Fragment], enzyme_name: str) -> list[Fragment]:
    """The pieces of a digest that no longer carry the enzyme's site — in a
    Golden Gate, the part itself. Its two sites point outwards, so they
    leave with the rest of the part plasmid and the product cannot be cut
    again, which is what lets the whole assembly run in one tube."""
    enzyme = ENZYMES.get(enzyme_name)
    if enzyme is None:
        return list(fragments)
    probes = {enzyme.site, reverse_complement(enzyme.site)}
    return [f for f in fragments if not any(probe in f.sequence for probe in probes)]


# ---------------------------------------------------------------------------
# Putting the pieces together
# ---------------------------------------------------------------------------

METHODS = ("digest_ligate", "gibson", "golden_gate")

MIN_OVERLAP = 15           # shorter than this and a match is chance, not homology
MAX_OVERLAP = 60
ARM = 25                   # the homology a designed Gibson primer carries
ANNEAL_MIN, ANNEAL_MAX = 18, 32
TARGET_TM = 60.0


def is_palindrome(bases: str) -> bool:
    """An overhang that anneals to itself: two fragments bearing it join
    either way round, and a fragment can close on itself."""
    return bool(bases) and bases == reverse_complement(bases)


def _join(pieces: list[tuple[Fragment, int, int]]) -> tuple[str, list[dict], list[dict]]:
    """The product's top strand, its features and where each fragment sits
    in it. Each piece is (fragment, bases to drop from its head, from its
    tail) — Gibson drops the copy of the overlap its neighbour already
    carries."""
    sequence, features, parts = "", [], []
    for fragment, head, tail in pieces:
        length = len(fragment.sequence) - head - tail
        if length <= 0:
            continue
        at = len(sequence)
        sequence += fragment.sequence[head:head + length]
        for f in carry_features(fragment.features, len(fragment.sequence), False, head, length):
            moved = dict(f)
            moved["start"], moved["end"] = f["start"] + at, f["end"] + at
            features.append(moved)
        parts.append({"name": fragment.name, "start": at, "end": at + length - 1, "length": length,
                      "source": fragment.source})
    return sequence, features, parts


def _result(sequence: str, circular: bool, junctions: list[dict], features: list[dict],
            parts: list[dict], problem: dict | None = None) -> dict:
    return {"ok": problem is None, "problem": problem, "sequence": sequence, "circular": circular,
            "length": len(sequence), "junctions": junctions, "features": features, "parts": parts}


def _too_few(fragments, least: int) -> dict | None:
    return {"kind": "too_few", "least": least} if len(fragments) < least else None


def ligate(fragments: list[Fragment], circular: bool = True) -> dict:
    """Join the fragments in the order given: each one's 3′ end must offer
    the end the next one's 5′ end is looking for. One fragment closing on
    itself is a self-ligation, which is a real thing to want."""
    junctions = [{"at": left + 1, "next": right + 1, "kind": fragments[left].right.kind,
                  "bases": fragments[left].right.bases, "wants": fragments[right].left.bases,
                  "text": fragments[left].right.text,
                  "ok": fragments[left].right.fits(fragments[right].left)}
                 for left, right in _pairs(fragments, circular)]
    problem = _too_few(fragments, 1) or _mismatched(fragments, junctions)
    sequence, features, parts = _join([(f, 0, 0) for f in fragments])
    return _result(sequence, circular, junctions, features, parts, problem)


def _mismatched(fragments, junctions) -> dict | None:
    """The first junction whose two ends do not go together, named by the
    end that is wrong rather than as "this does not assemble"."""
    wrong = next((j for j in junctions if not j["ok"]), None)
    if wrong is None:
        return None
    return {"kind": "ends", "at": wrong["at"], "next": wrong["next"],
            "left": fragments[wrong["at"] - 1].right.text,
            "right": fragments[wrong["next"] - 1].left.text}


def _pairs(fragments, circular: bool) -> list[tuple[int, int]]:
    """Which ends meet: (left fragment, right fragment) per junction. A
    circular product closes the last onto the first."""
    n = len(fragments)
    if n == 0:
        return []
    return [(i % n, (i + 1) % n) for i in range(n if circular else n - 1)]


def overlap_between(left: str, right: str, minimum: int = MIN_OVERLAP, maximum: int = MAX_OVERLAP) -> str:
    """The homology two fragments already share where they meet: the longest
    tail of the left that is the head of the right."""
    most = min(maximum, len(left), len(right))
    for size in range(most, minimum - 1, -1):
        if left[-size:] == right[:size]:
            return right[:size]
    return ""


def gibson(fragments: list[Fragment], circular: bool = True, design: bool = False,
           minimum: int = MIN_OVERLAP, amplify=None) -> dict:
    """Overlap assembly. Fragments that already share their ends (a region
    taken to overlap its neighbour) are joined on that homology; with
    `design`, a junction with none is taken to be one the wizard's primers
    will add, which costs the product no bases.

    `amplify` is which fragments are made by PCR and so can be given a
    homology arm — all of them unless the tray holds a piece straight out of
    a digest. A junction between two such pieces has nowhere to put an arm,
    so it still needs the homology to be there already."""
    junctions, overlaps = [], {}
    for left, right in _pairs(fragments, circular):
        shared = overlap_between(fragments[left].sequence, fragments[right].sequence, minimum)
        overlaps[(left, right)] = shared
        tm = primer_tm(shared) if shared else None
        arm = design and (_amplified(left, amplify, fragments) or _amplified(right, amplify, fragments))
        junctions.append({"at": left + 1, "next": right + 1, "kind": "overlap", "bases": shared,
                          "length": len(shared), "designed": not shared and arm,
                          "tm": round(tm, 1) if tm is not None else None,
                          "text": shared, "ok": bool(shared) or arm})
    problem = _too_few(fragments, 2)
    if problem is None:
        empty = next((j for j in junctions if not j["ok"]), None)
        if empty is not None:
            problem = {"kind": "no_overlap", "at": empty["at"], "next": empty["next"]}
    if problem is None:
        problem = _repeated(junctions)
    # Each overlap is written once: the fragment on the right of a junction
    # drops the copy its neighbour already carries, and a circular product
    # drops the last one from its tail instead, so it keeps its origin.
    head = {right: len(shared) for (left, right), shared in overlaps.items() if right}
    tail = len(overlaps.get((len(fragments) - 1, 0), "")) if circular and len(fragments) > 1 else 0
    pieces = [(f, head.get(i, 0), tail if i == len(fragments) - 1 else 0) for i, f in enumerate(fragments)]
    sequence, features, parts = _join(pieces)
    return _result(sequence, circular, junctions, features, parts, problem)


def _amplified(at: int, amplify, fragments) -> bool:
    """Whether this fragment is one a PCR makes, so a primer can carry its
    neighbour's bases as a 5′ tail."""
    return at in amplify if amplify is not None else at < len(fragments)


def _repeated(junctions: list[dict]) -> dict | None:
    """The same word at two junctions: the fragments between them can swap
    places, or drop out, and the assembly is no longer one product."""
    seen: dict[str, int] = {}
    for junction in junctions:
        word = junction["bases"]
        if not word:
            continue
        if word in seen:
            return {"kind": "repeated", "bases": word, "at": seen[word], "next": junction["at"]}
        seen[word] = junction["at"]
    return None


def golden_gate(fragments: list[Fragment], circular: bool = True, enzyme: str = "BsaI") -> dict:
    """A Type IIS assembly: every junction is a four-base overhang you chose,
    so the order is fixed by the overhangs rather than by the order of the
    tubes. Refused when two junctions take the same overhang (the fragments
    between them could swap or drop out), when one is its own reverse
    complement (a fragment could go in backwards), or when a fragment's
    ends do not meet its neighbours'."""
    junctions = []
    for left, right in _pairs(fragments, circular):
        end = fragments[left].right
        junctions.append({"at": left + 1, "next": right + 1, "kind": end.kind, "bases": end.bases,
                          "wants": fragments[right].left.bases, "length": len(end.bases),
                          "text": end.text, "ok": end.fits(fragments[right].left)})
    problem = _too_few(fragments, 2) or _mismatched(fragments, junctions)
    if problem is None:
        blunt = next((j for j in junctions if not j["bases"]), None)
        if blunt is not None:
            problem = {"kind": "blunt", "at": blunt["at"], "next": blunt["next"], "enzyme": enzyme}
    if problem is None:
        sizes = {len(j["bases"]) for j in junctions}
        if len(sizes) > 1:
            problem = {"kind": "uneven", "sizes": ", ".join(str(s) for s in sorted(sizes))}
    if problem is None:
        problem = _repeated(junctions)
    if problem is None:
        mirrored = next(((a, b) for i, a in enumerate(junctions) for b in junctions[i + 1:]
                         if a["bases"] == reverse_complement(b["bases"])), None)
        if mirrored is not None:
            problem = {"kind": "mirrored", "bases": mirrored[0]["bases"], "at": mirrored[0]["at"],
                       "next": mirrored[1]["at"]}
    if problem is None:
        self_closing = next((j for j in junctions if is_palindrome(j["bases"])), None)
        if self_closing is not None:
            problem = {"kind": "palindrome", "bases": self_closing["bases"], "at": self_closing["at"]}
    sequence, features, parts = _join([(f, 0, 0) for f in fragments])
    return _result(sequence, circular, junctions, features, parts, problem)


def assemble(method: str, fragments: list[Fragment], circular: bool = True, **kwargs) -> dict:
    """Whichever of the three the wizard is on."""
    if method == "gibson":
        return gibson(fragments, circular, design=kwargs.get("design", False))
    if method == "golden_gate":
        return golden_gate(fragments, circular, enzyme=kwargs.get("enzyme", "BsaI"))
    return ligate(fragments, circular)


# ---------------------------------------------------------------------------
# The primers a Gibson needs
# ---------------------------------------------------------------------------


def anneal_length(sequence: str, target: float = TARGET_TM) -> int:
    """How much of a fragment's end a primer should hold on to: the shortest
    stretch between ANNEAL_MIN and ANNEAL_MAX whose melting temperature
    reaches `target`, and the whole range when none does."""
    best, best_gap = ANNEAL_MIN, None
    for size in range(ANNEAL_MIN, min(ANNEAL_MAX, len(sequence)) + 1):
        tm = primer_tm(sequence[:size])
        if tm is None:
            continue
        if tm >= target:
            return size
        gap = target - tm
        if best_gap is None or gap < best_gap:
            best, best_gap = size, gap
    return min(best, len(sequence)) or len(sequence)


def design_gibson_primers(fragments: list[Fragment], circular: bool = True, arm: int = ARM,
                          amplify=None) -> list[dict]:
    """Two primers a fragment, each annealing to its own end with a 5′ tail
    copied from the neighbour it has to meet. A junction whose fragments
    already share their ends needs no tail, so it gets a plain primer, and a
    fragment that came out of a digest rather than a PCR gets none at all."""
    if len(fragments) < 2:
        return []
    before = {right: left for left, right in _pairs(fragments, circular)}
    after = {left: right for left, right in _pairs(fragments, circular)}
    primers = []
    for i, fragment in enumerate(fragments):
        if not _amplified(i, amplify, fragments):
            continue
        seq = fragment.sequence
        label = fragment.name or f"fragment {i + 1}"
        forward_tail = reverse_tail = ""
        if i in before:
            neighbour = fragments[before[i]].sequence
            if not overlap_between(neighbour, seq):
                forward_tail = neighbour[-arm:]
        if i in after:
            neighbour = fragments[after[i]].sequence
            if not overlap_between(seq, neighbour):
                reverse_tail = reverse_complement(neighbour[:arm])
        head = anneal_length(seq)
        back = reverse_complement(seq[-ANNEAL_MAX:])
        tail_anneal = anneal_length(back)
        for direction, tail, anneal in (("forward", forward_tail, seq[:head]),
                                        ("reverse", reverse_tail, back[:tail_anneal])):
            tm = primer_tm(anneal)
            primers.append({
                "fragment": i + 1, "name": f"{label} {'F' if direction == 'forward' else 'R'}",
                "direction": direction, "tail": tail, "anneal": anneal, "sequence": tail + anneal,
                "tm": round(tm, 1) if tm is not None else None, "length": len(tail + anneal),
                "source": fragment.source,
            })
    return primers


# ---------------------------------------------------------------------------
# Taking a piece off a plasmid
# ---------------------------------------------------------------------------


def region(sequence: str, circular: bool, start: int, length: int, features=None,
           name: str = "", source: dict | None = None) -> Fragment:
    """A fragment with blunt ends: a whole plasmid read from base 1, one
    feature, or the stretch between two coordinates — what a PCR gives you,
    and what a Gibson or a Golden Gate part is made of. `start` is 0-based
    and the stretch may run round the origin."""
    seq = (sequence or "").upper()
    n = len(seq)
    length = max(0, min(length, n))
    return Fragment(sequence=_span(seq, start, length, circular), name=name,
                    features=carry_features(features, n, circular, start % n if n else 0, length),
                    source={"start": start % n if n else 0, "length": length, **(source or {})})


def span_length(start: int, end: int, parent_length: int, circular: bool) -> int:
    """How many bases a 0-based inclusive span covers, the way an annotation
    writes it: start > end runs round the origin."""
    if parent_length <= 0:
        return 0
    if start <= end:
        return end - start + 1
    return (end - start) % parent_length + 1 if circular else 0
