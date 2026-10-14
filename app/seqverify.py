"""Sequencing verification: Sanger reads of a miniprep or clone checked
against a plasmid's map.

Pure functions, no database and no Flask: the plasmid page
(`/plasmids/<id>/verify`, app/app.py), the API
(`POST /api/v1/plasmids/<n>/verify`) and the assistant's
`verify_sequencing` tool all call `verify`.

- **Reads.** An ABI chromatogram (.ab1, the ABIF format: a directory of
  tagged entries, read here for PBAS2/PCON2, the called bases and their
  quality, falling back to PBAS1/PCON1) or text: FASTA (one record or
  several) or plain bases. Any other binary is refused in words
  (`ReadError`). A read with quality is trimmed with Mott's algorithm
  (`mott_trim`); a text read loses only the Ns at its ends.
- **Placing a read.** Every 12-base word of the plasmid (`K`), the start
  repeated after the end when it is circular so a read across the origin
  is one stretch, is indexed once per call. Each strand of the read votes
  with its own words for a diagonal (plasmid position minus read
  position); the densest window of diagonals (`SPAN` wide) says where and
  which way round the read lies.
- **Aligning it.** Smith–Waterman, local, but only in a band around those
  diagonals (`_banded_local`), so a 1,000-base read against a 15 kb
  plasmid fills about 1,000 × 100 cells rather than 15 million: a tenth of a
  second or so in plain Python.
- **What it says.** Each read: the strand, the plasmid coordinates it
  covers (1-based, inclusive), its identity, and each difference
  (mismatch, insertion, deletion) with its position, the base change, the
  read's quality there, the map features it falls in and, in a CDS, what
  happens to the amino acid (`_coding_effect`, the standard code). A
  difference where the quality is under `GOOD_QUALITY` is listed but
  marked a likely sequencing error. Then the clone as a whole: what the
  reads cover together, the distinct differences, and one sentence.
- **The trace.** An .ab1 keeps its four dye channels (DATA9–12, in the
  order FWO_ names) and where each called base's peak is (PLOC). Each
  difference carries the stretch of trace around it (`trace_window`), for
  the page to draw. A read whose good bases often have a second peak at
  least `MIXED_RATIO` of the called one (`second_peaks`) is said to look
  mixed: two templates at once, from two clones (or a heterozygous
  edit). When the second peaks start at one place and go on (`mixed_from`),
  the two differ by an insertion or deletion near there.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from .i18n import gettext, ngettext

K = 12                 # word length that places a read
SPAN = 60              # the widest net indel a read's diagonals may drift by
BAND_PAD = 24          # bases either side of the diagonals the alignment may wander
MAX_HITS = 8           # a word found more often than this (a repeat) doesn't vote
MIN_VOTES = 4          # words that must agree before a read is aligned at all
MIN_ALIGNED = 30       # read bases that must align for the read to count as placed
MIN_IDENTITY = 80.0    # % identity under which a read doesn't match this plasmid
GOOD_QUALITY = 20      # Phred: a difference below this is probably the read's
MOTT_CUTOFF = 0.05     # Mott's error-rate cutoff (about Q13)
MIN_TRIMMED = 30       # good bases a read needs to be checked at all
MAX_READS = 24
MAX_READ_BASES = 20_000
MAX_FILE = 5 * 1024 * 1024
MIXED_RATIO = 0.35     # a second peak at least this share of the called base's
MIXED_SHARE = 0.05     # good bases with one, for a read to look mixed
MIXED_MIN = 8          # and at least this many
ONSET_RUN = 20         # bases after a point, most of them with a second peak: two sequences from there
TRACE_FLANK = 8        # called bases either side of a difference in its picture
TRACE_POINTS = 160     # trace samples per channel in a picture, at most
READ_SUFFIXES = (".ab1", ".abi", ".ab", ".seq", ".fa", ".fasta", ".fas", ".fna", ".txt")

MATCH, MISMATCH, GAP_OPEN, GAP_EXTEND = 2, -4, -5, -2

# IUPAC: which bases each letter may be.
IUPAC = {"A": "A", "C": "C", "G": "G", "T": "T", "U": "T", "R": "AG", "Y": "CT", "S": "CG", "W": "AT",
         "K": "GT", "M": "AC", "B": "CGT", "D": "AGT", "H": "ACT", "V": "ACG", "N": "ACGT"}
_COMPLEMENT = str.maketrans("ACGTURYKMSWBDHVN", "TGCAAYRMKSWVHDBN")
_LETTERS = re.compile(r"[^A-Za-z]")

# The standard genetic code (NCBI table 1; table 11, bacteria and plasmids,
# codes the same amino acids).
_BASES = "TCAG"
_AMINO = "FFLLSSSSYY**CC*WLLLLPPPPHHQQRRRRIIIMTTTTNNKKSSRRVVVVAAAADDEEGGGG"
CODE = {a + b + c: _AMINO[16 * i + 4 * j + k]
        for i, a in enumerate(_BASES) for j, b in enumerate(_BASES) for k, c in enumerate(_BASES)}


class ReadError(ValueError):
    """Why a file can't be read as a sequencing read, in words for the person."""


@dataclass
class Read:
    name: str
    bases: str
    quality: list[int] | None = None
    source: str = "text"          # "ab1" or "text"
    extra: dict = field(default_factory=dict)


def reverse_complement(seq: str) -> str:
    return seq.translate(_COMPLEMENT)[::-1]


def translate_codon(codon: str) -> str:
    return CODE.get(codon.upper().replace("U", "T"), "X")


def _n(value: int) -> str:
    return f"{value:,}"


# ---------------------------------------------------------------- reading the files

def _abif_entries(data: bytes) -> dict[tuple[str, int], tuple[int, int, int, bytes]]:
    """The ABIF directory: (tag, number) → (element type, element size,
    count, raw bytes). Raises ReadError when the file isn't one."""
    if len(data) < 34 or data[:4] != b"ABIF":
        raise ReadError("")
    try:
        _tag, _num, _etype, esize, count, _dsize, offset, _handle = struct.unpack(">4sihhiiii", data[6:34])
    except struct.error:
        raise ReadError("") from None
    if esize != 28 or count <= 0 or count > 100_000 or offset < 0 or offset + 28 * count > len(data):
        raise ReadError("")
    out = {}
    for n in range(count):
        at = offset + 28 * n
        raw_entry = data[at:at + 28]
        tag, number, etype, esize, elements, dsize, doffset, _handle = struct.unpack(">4sihhiiii", raw_entry)
        if dsize < 0:
            continue
        if dsize <= 4:
            raw = raw_entry[20:20 + dsize]          # small data lives in the offset field itself
        elif 0 <= doffset and doffset + dsize <= len(data):
            raw = data[doffset:doffset + dsize]
        else:
            continue
        out[(tag.decode("latin-1"), number)] = (etype, esize, elements, raw)
    return out


def read_abif(data: bytes, name: str = "") -> Read:
    """An ABI chromatogram's called bases and their quality (PBAS2 and
    PCON2, the base caller's; else PBAS1 and PCON1, a person's edit)."""
    if data[128:132] == b"ABIF" and data[:4] != b"ABIF":
        data = data[128:]                            # a MacBinary header from an old Mac
    try:
        entries = _abif_entries(data)
    except ReadError:
        raise ReadError(gettext("%(name)s isn't a readable ABI chromatogram: its directory is damaged.",
                                name=name or "?")) from None
    bases_entry = entries.get(("PBAS", 2)) or entries.get(("PBAS", 1))
    if bases_entry is None:
        raise ReadError(gettext("%(name)s is an ABI file without called bases (PBAS): base-call it first.",
                                name=name or "?"))
    bases = _LETTERS.sub("", bases_entry[3].decode("latin-1")).upper()
    quality = None
    quality_entry = entries.get(("PCON", 2)) if ("PBAS", 2) in entries else entries.get(("PCON", 1))
    quality_entry = quality_entry or entries.get(("PCON", 2)) or entries.get(("PCON", 1))
    if quality_entry is not None and len(quality_entry[3]) == len(bases_entry[3]):
        raw_quality = quality_entry[3]
        quality = [q for q, b in zip(raw_quality, bases_entry[3]) if chr(b).isalpha()]
    sample = ""
    smpl = entries.get(("SMPL", 1))
    if smpl is not None and smpl[3]:
        raw = smpl[3]
        sample = raw[1:1 + raw[0]].decode("latin-1", "replace").strip() if smpl[0] == 18 else \
            raw.decode("latin-1", "replace").strip("\x00 ")
    extra = {"sample": sample}
    trace = _abif_trace(entries, bases_entry, ("PBAS", 2) in entries)
    if trace is not None:
        extra["trace"] = trace
    return Read(name=name or sample or "read", bases=bases, quality=quality, source="ab1", extra=extra)


def _abif_trace(entries: dict, bases_entry, base_caller: bool) -> dict | None:
    """The four analysed dye channels in A, C, G, T order, and each called
    base's peak (an index into them); None when the file hasn't them or
    they don't line up with the bases."""
    order = entries.get(("FWO_", 1))
    if order is None or len(order[3]) < 4:
        return None
    letters = order[3][:4].decode("latin-1").upper()
    if sorted(letters) != list("ACGT"):
        return None
    channels = {}
    for n, base in zip(range(9, 13), letters):
        entry = entries.get(("DATA", n))
        if entry is None or entry[1] != 2 or not entry[2]:
            return None
        channels[base] = list(struct.unpack(f">{entry[2]}h", entry[3][:2 * entry[2]]))
    if len({len(v) for v in channels.values()}) != 1:
        return None
    ploc = entries.get(("PLOC", 2 if base_caller else 1)) or entries.get(("PLOC", 2)) or entries.get(("PLOC", 1))
    if ploc is None or ploc[1] != 2:
        return None
    peaks = list(struct.unpack(f">{ploc[2]}h", ploc[3][:2 * ploc[2]]))
    raw = bases_entry[3]
    if len(peaks) != len(raw):
        return None
    peaks = [p for p, b in zip(peaks, raw) if chr(b).isalpha()]       # as the bases were kept
    size = len(channels["A"])
    if not peaks or any(not 0 <= p < size for p in peaks):
        return None
    return {"channels": channels, "peaks": peaks}


def second_peaks(read: Read, start: int, end: int) -> list[int]:
    """The read's bases in [start, end) under which another dye's peak is at
    least MIXED_RATIO of the called base's."""
    trace = (read.extra or {}).get("trace")
    if not trace:
        return []
    ch, peaks, out = trace["channels"], trace["peaks"], []
    for i in range(max(0, start), min(end, len(peaks), len(read.bases))):
        heights = {b: ch[b][peaks[i]] for b in "ACGT"}
        called = read.bases[i] if read.bases[i] in heights else max(heights, key=heights.get)
        main = heights[called]
        if main <= 0:
            continue
        other = max(v for b, v in heights.items() if b != called)
        if other >= MIXED_RATIO * main:
            out.append(i)
    return out


def mixed_from(marked: list[int], start: int, end: int) -> int | None:
    """Where two sequences start in a read, when they do: the first base
    from which most of the next ONSET_RUN have a second peak, with few
    before it. None for second peaks scattered all along."""
    if not marked:
        return None
    hits = set(marked)
    for i in range(start, max(start, end - ONSET_RUN)):
        if i in hits and sum(1 for j in range(i, i + ONSET_RUN) if j in hits) >= ONSET_RUN * 0.6:
            before = sum(1 for j in range(start, i) if j in hits)
            return i if before <= max(2, 0.1 * (i - start)) else None
    return None


def trace_window(read: Read, index: int, strand: str) -> dict | None:
    """The trace around one of the read's bases, for a picture: each dye's
    samples (at most TRACE_POINTS), the called bases at their x, and which
    one is `index`. As the read runs: a reverse read's is the other strand."""
    trace = (read.extra or {}).get("trace")
    if not trace or not 0 <= index < len(trace["peaks"]):
        return None
    peaks = trace["peaks"]
    lo, hi = max(0, index - TRACE_FLANK), min(len(peaks) - 1, index + TRACE_FLANK)
    x0 = max(0, peaks[lo] - 6)
    x1 = min(len(trace["channels"]["A"]) - 1, peaks[hi] + 6)
    step = max(1, -(-(x1 - x0 + 1) // TRACE_POINTS))
    channels = {b: trace["channels"][b][x0:x1 + 1:step] for b in "ACGT"}
    return {"channels": channels, "step": step, "x0": x0, "strand": strand,
            "bases": [{"base": read.bases[i], "x": (peaks[i] - x0) / step, "at": i == index} for i in range(lo, hi + 1)
                      if i < len(read.bases)]}


def reads_in_text(text: str, name: str = "") -> list[Read]:
    """Reads in FASTA (one record or several) or as plain bases."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip().lstrip("﻿")
    if not text:
        return []
    if text.startswith("LOCUS"):
        from .sequence_parser import parse_genbank
        parsed = parse_genbank(text)
        if parsed and parsed.get("sequence"):
            return [Read(name=parsed.get("name") or name or "read", bases=parsed["sequence"])]
    out = []
    if text.startswith(">"):
        for block in re.split(r"\n(?=>)", text):
            header, _, body = block.partition("\n")
            title = header[1:].strip().split()[0] if header[1:].strip() else (name or "read")
            out.append(_text_read(body, title))
        return out
    return [_text_read(text, name or "read")]


def _text_read(body: str, name: str) -> Read:
    letters = re.sub(r"[\s\d\-*.]", "", body)
    bad = sum(1 for c in letters if c.upper() not in IUPAC)
    if not letters or bad > max(2, len(letters) // 20):
        raise ReadError(gettext("%(name)s doesn't look like DNA bases.", name=name))
    return Read(name=name, bases="".join(c for c in letters.upper() if c in IUPAC).replace("U", "T"))


def read_file(filename: str, data: bytes) -> list[Read]:
    """The reads in one uploaded file, or ReadError saying why not."""
    name = filename or "read"
    stem = re.sub(r"\.(ab1|abi|ab|seq|fa|fasta|fas|fna|txt)$", "", name, flags=re.I) or name
    if len(data) > MAX_FILE:
        raise ReadError(gettext("%(name)s is over %(mb)s MB: that isn't a Sanger read.", name=name,
                                mb=MAX_FILE // (1024 * 1024)))
    if data[:4] == b"ABIF" or data[128:132] == b"ABIF":
        return [read_abif(data, stem)]
    if data[:4] == b".scf":
        raise ReadError(gettext("%(name)s is an SCF trace, which isn't read here: use the .ab1 file, or the read as FASTA.",
                                name=name))
    if b"\x00" in data[:4096]:
        raise ReadError(gettext("%(name)s isn't a sequencing read BioManager can open: give the .ab1 chromatogram, or the read as FASTA or plain text.", name=name))
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    reads = reads_in_text(text, stem)
    if not reads:
        raise ReadError(gettext("%(name)s is empty.", name=name))
    return reads


# ---------------------------------------------------------------- trimming

_ERROR = [10 ** (-q / 10) for q in range(101)]


def mott_trim(quality: list[int], cutoff: float = MOTT_CUTOFF) -> tuple[int, int]:
    """Mott's algorithm: the stretch [start, end) whose summed
    (cutoff − error probability) is greatest; (0, 0) when none is good."""
    best = run = 0.0
    best_start = best_end = run_start = 0
    for i, q in enumerate(quality):
        score = cutoff - _ERROR[min(max(int(q), 0), 100)]
        if run <= 0:
            run, run_start = score, i
        else:
            run += score
        if run > best:
            best, best_start, best_end = run, run_start, i + 1
    return (best_start, best_end) if best > 0 else (0, 0)


def trim(read: Read) -> tuple[int, int]:
    """Where the good part of a read is: Mott's on its quality, or (a text
    read) all but the Ns at its ends."""
    if read.quality and len(read.quality) == len(read.bases):
        return mott_trim(read.quality)
    start, end = 0, len(read.bases)
    while start < end and read.bases[start] not in "ACGT":
        start += 1
    while end > start and read.bases[end - 1] not in "ACGT":
        end -= 1
    return start, end


# ---------------------------------------------------------------- placing and aligning

def _score_table() -> dict[str, dict[str, int]]:
    """Score of a read letter against a plasmid letter: a definite base
    scores MATCH or MISMATCH; an ambiguity code that could be it scores 0
    in the read (a mixed peak isn't evidence) and 1 in the plasmid."""
    table = {}
    for q, q_can in IUPAC.items():
        row = {}
        for t, t_can in IUPAC.items():
            if len(q_can) == 1 and len(t_can) == 1:
                row[t] = MATCH if q_can == t_can else MISMATCH
            elif len(q_can) == 1:
                row[t] = 1 if q_can in t_can else MISMATCH
            else:
                row[t] = 0 if set(q_can) & set(t_can) else -1
        table[q] = row
    return table


SCORES = _score_table()


def _compatible(read_base: str, plasmid_base: str) -> bool:
    return bool(set(IUPAC.get(read_base, "ACGT")) & set(IUPAC.get(plasmid_base, "ACGT")))


class Target:
    """The plasmid as reads are placed on it: the sequence (its start
    repeated after its end when circular) and its words' positions."""

    def __init__(self, sequence: str, circular: bool, longest_read: int = 2000):
        self.sequence = (sequence or "").upper().replace("U", "T")
        self.length = len(self.sequence)
        self.circular = bool(circular)
        extra = min(self.length, longest_read + 2 * BAND_PAD + SPAN) if self.circular else 0
        self.text = self.sequence + self.sequence[:extra]
        index: dict[str, list[int]] = {}
        text = self.text
        for i in range(len(text) - K + 1):
            word = text[i:i + K]
            hits = index.get(word)
            if hits is None:
                index[word] = [i]
            elif len(hits) <= MAX_HITS:
                hits.append(i)
        self.index = index

    def place(self, query: str) -> tuple[int, int, int]:
        """(votes, lowest diagonal, highest diagonal) of the densest window
        of diagonals this query's words vote for."""
        votes: dict[int, int] = {}
        index = self.index
        for i in range(len(query) - K + 1):
            hits = index.get(query[i:i + K])
            if hits is None or len(hits) > MAX_HITS:
                continue
            for p in hits:
                d = p - i
                votes[d] = votes.get(d, 0) + 1
        if not votes:
            return 0, 0, 0
        diagonals = sorted(votes)
        best = (0, 0, 0)
        total, lo = 0, 0
        for hi, d in enumerate(diagonals):
            total += votes[d]
            while d - diagonals[lo] > SPAN:
                total -= votes[diagonals[lo]]
                lo += 1
            if total > best[0]:
                best = (total, diagonals[lo], d)
        return best


def _banded_local(query: str, target: str, dlo: int, dhi: int):
    """Smith–Waterman with affine gaps (Gotoh) of query against target,
    only in the band of diagonals dlo ≤ j − i ≤ dhi. The alignment as
    (op, query index, target index) with op M (aligned), I (a base only the
    read has, after target index) or D (a base only the plasmid has), and
    its score. A gap costs GAP_OPEN once and GAP_EXTEND a base, so three
    missing bases are one deletion rather than three."""
    m, n = len(query), len(target)
    width = dhi - dlo + 1
    first, extend = GAP_OPEN + GAP_EXTEND, GAP_EXTEND
    floor = -10 ** 9
    prev_h = [0] * (width + 1)
    prev_f = [floor] * (width + 1)
    trace = []
    best = bi = bj = 0
    for i in range(1, m + 1):
        row = SCORES.get(query[i - 1], SCORES["N"])
        cur_h = [0] * (width + 1)
        cur_f = [floor] * (width + 1)
        back = bytearray(width)
        base = i + dlo                  # j at k = 0
        k_lo = max(0, 1 - base)
        k_hi = min(width, n - base + 1)
        left_h, left_e = 0, floor
        for k in range(k_lo, k_hi):
            j = base + k
            # E: ends in a deletion (from the left); F: ends in an insertion (from above).
            e_open, e_ext = left_h + first, left_e + extend
            e, bits = (e_ext, 4) if e_ext > e_open else (e_open, 0)
            f_open, f_ext = prev_h[k + 1] + first, prev_f[k + 1] + extend
            if f_ext > f_open:
                f = f_ext
                bits |= 8
            else:
                f = f_open
            h = prev_h[k] + row.get(target[j - 1], MISMATCH)
            move = 1
            if f > h:
                h, move = f, 2
            if e > h:
                h, move = e, 3
            if h <= 0:
                h, move = 0, 0
            cur_h[k] = h
            cur_f[k] = f
            back[k] = move | bits
            left_h, left_e = h, e
            if h > best:
                best, bi, bj = h, i, j
        trace.append(back)
        prev_h, prev_f = cur_h, cur_f
    ops = []
    i, j, state = bi, bj, 0             # state 0: H, 2: in an insertion (F), 3: in a deletion (E)
    while i > 0 and j > 0:
        k = j - i - dlo
        if k < 0 or k >= width:
            break
        cell = trace[i - 1][k]
        if state == 0:
            state = cell & 3
            if state == 0:
                break
            if state == 1:
                ops.append(("M", i - 1, j - 1))
                i, j, state = i - 1, j - 1, 0
            continue
        if state == 2:
            ops.append(("I", i - 1, j - 1))   # the read's base i-1 sits after plasmid base j-1
            i -= 1
            state = 2 if cell & 8 else 0
        else:
            ops.append(("D", i - 1, j - 1))   # plasmid base j-1 is missing after read base i-1
            j -= 1
            state = 3 if cell & 4 else 0
    ops.reverse()
    return ops, best


# ---------------------------------------------------------------- features and what a change does

def _ranges(feature: dict, length: int) -> list[tuple[int, int]]:
    locs = feature.get("locations") or []
    pieces = []
    for loc in locs:
        try:
            pieces.append((int(loc["start"]), int(loc["end"])))
        except (KeyError, TypeError, ValueError):
            continue
    if not pieces:
        try:
            pieces = [(int(feature.get("start", 0)), int(feature.get("end", 0)))]
        except (TypeError, ValueError):
            return []
    return [(s, e) for s, e in pieces if 0 <= s < length and 0 <= e < length]


def _positions(ranges: list[tuple[int, int]], length: int) -> list[int]:
    out = []
    for s, e in ranges:
        out.extend(range(s, e + 1) if s <= e else [*range(s, length), *range(0, e + 1)])
    return out


def _contains(ranges: list[tuple[int, int]], pos: int) -> bool:
    return any((s <= pos <= e) if s <= e else (pos >= s or pos <= e) for s, e in ranges)


def map_features(annotations: list, length: int) -> list[dict]:
    """The map's features (not primers, translations or parts) as reads
    are checked against them: name, type, ranges, and a CDS's coding
    positions in reading order."""
    out = []
    for f in annotations or []:
        if not isinstance(f, dict) or f.get("kind") not in (None, "", "feature"):
            continue
        if str(f.get("type") or "").lower() == "source":
            continue
        ranges = _ranges(f, length)
        if not ranges:
            continue
        item = {"name": str(f.get("name") or f.get("type") or "feature"), "type": str(f.get("type") or ""),
                "ranges": ranges, "direction": -1 if int(f.get("direction", 1) or 1) < 0 else 1}
        if item["type"].upper() == "CDS":
            coding = _positions(ranges, length)
            if item["direction"] < 0:
                coding.reverse()
            notes = f.get("notes") if isinstance(f.get("notes"), dict) else {}
            start = notes.get("codon_start") or ["1"]
            try:
                frame = int(start[0] if isinstance(start, list) else start) - 1
            except (TypeError, ValueError):
                frame = 0
            item["coding"] = coding
            item["coding_index"] = {p: i for i, p in enumerate(coding)}
            item["frame"] = frame if frame in (0, 1, 2) else 0
        out.append(item)
    return out


def _effect_text(kind: str, codon: int, ref: str, alt: str) -> str:
    if kind == "silent":
        return gettext("silent, %(aa)s%(codon)s", aa=ref, codon=codon)
    if kind == "missense":
        return gettext("%(ref)s%(codon)s%(alt)s", ref=ref, codon=codon, alt=alt)
    if kind == "nonsense":
        return gettext("stop codon: %(ref)s%(codon)s*", ref=ref, codon=codon)
    if kind == "stop_lost":
        return gettext("stop codon lost: *%(codon)s%(alt)s", codon=codon, alt=alt)
    if kind == "start_lost":
        return gettext("start codon lost: M1%(alt)s", alt=alt)
    return ""


def _substitution_effect(sequence: str, cds: dict, changes: dict[int, str]) -> dict | None:
    """What these base changes (plasmid position → read base, as the top
    strand reads) do to the codon they share in this CDS."""
    index = cds["coding_index"]
    coding, frame = cds["coding"], cds["frame"]
    positions = [p for p in changes if p in index]
    if not positions:
        return None
    ci = index[positions[0]] - frame
    if ci < 0:
        return None
    codon_no = ci // 3
    first = frame + 3 * codon_no
    if first + 3 > len(coding):
        return None
    triplet = coding[first:first + 3]
    reverse = cds["direction"] < 0
    ref = "".join(sequence[p] for p in triplet)
    alt = "".join(changes.get(p, sequence[p]) for p in triplet)
    if reverse:
        ref, alt = ref.translate(_COMPLEMENT), alt.translate(_COMPLEMENT)
    ref_aa, alt_aa = translate_codon(ref), translate_codon(alt)
    number = codon_no + 1
    if ref_aa == alt_aa:
        kind = "silent"
    elif number == 1 and ref_aa == "M":
        kind = "start_lost"
    elif alt_aa == "*":
        kind = "nonsense"
    elif ref_aa == "*":
        kind = "stop_lost"
    else:
        kind = "missense"
    return {"feature": cds["name"], "kind": kind, "codon": number, "from": ref_aa, "to": alt_aa,
            "codon_from": ref, "codon_to": alt, "text": _effect_text(kind, number, ref_aa, alt_aa)}


def _indel_effect(cds: dict, pos: int, size: int) -> dict | None:
    index = cds["coding_index"]
    if pos not in index:
        return None
    ci = index[pos] - cds["frame"]
    number = max(ci, 0) // 3 + 1
    if size % 3:
        return {"feature": cds["name"], "kind": "frameshift", "codon": number,
                "text": gettext("frameshift at codon %(codon)s", codon=number)}
    return {"feature": cds["name"], "kind": "in_frame", "codon": number,
            "text": ngettext("in frame, %(num)s codon", "in frame, %(num)s codons", size // 3)}


# ---------------------------------------------------------------- one read

def _span_text(start: int, end: int) -> str:
    """1-based inclusive; a span across the origin says so."""
    if start <= end:
        return f"{_n(start)}–{_n(end)}"
    return gettext("%(start)s–%(end)s, across the origin", start=_n(start), end=_n(end))


def _diff_text(d: dict) -> str:
    where = d["position"]
    if d["kind"] == "mismatch":
        text = gettext("%(was)s→%(now)s at %(at)s", was=d["plasmid"], now=d["read"], at=_n(where))
    elif d["kind"] == "deletion":
        text = (gettext("%(base)s deleted at %(at)s", base=d["plasmid"], at=_n(where)) if d["length"] == 1 else
                gettext("%(n)s bp deleted at %(start)s–%(end)s", n=d["length"], start=_n(where), end=_n(d["end"])))
    else:
        text = gettext("%(bases)s inserted after %(at)s", bases=d["read"] if d["length"] <= 12 else
                       gettext("%(n)s bp", n=d["length"]), at=_n(where))
    if d["features"]:
        text += " " + gettext("in %(features)s", features=", ".join(d["features"]))
    if d.get("effect"):
        text += f" ({d['effect']['text']})"
    if not d["confident"]:
        text += " · " + gettext("low quality (Q%(q)s): likely a sequencing error", q=d["quality"])
    return text


def _listed(differences: list[dict], most: int) -> str:
    text = "; ".join(d["text"] for d in differences[:most])
    if len(differences) > most:
        text += "; " + gettext("and %(n)s more", n=len(differences) - most)
    return text


def check_read(read: Read, target: Target, features: list[dict]) -> dict:
    """One read against the plasmid: where it lies, and how it differs."""
    L = target.length
    out = {"name": read.name, "source": read.source, "length": len(read.bases), "placed": False,
           "differences": [], "ambiguous": 0, "low_quality": 0}
    start, end = trim(read)
    out["trimmed"] = [start + 1, end] if end > start else None
    good = read.bases[start:end]
    if len(good) < MIN_TRIMMED:
        out.update(status="poor", verdict=gettext("Too little good sequence to check (%(n)s bases after trimming).",
                                                  n=len(good)))
        return out
    if not L:
        out.update(status="no_match", verdict=gettext("The plasmid has no sequence to check against."))
        return out
    forward = target.place(good)
    rc = reverse_complement(good)
    backward = target.place(rc)
    strand, query, (votes, dlo, dhi) = ("forward", good, forward) if forward[0] >= backward[0] else \
        ("reverse", rc, backward)
    if votes < MIN_VOTES:
        out.update(status="no_match", verdict=gettext("Doesn't match this plasmid."))
        return out
    # Circular: a read whose start hangs before the plasmid's position 1 is
    # the same read placed a whole turn on, in the repeated start.
    if target.circular and dlo < 0 and dhi + L + len(query) <= len(target.text):
        dlo, dhi = dlo + L, dhi + L
    ops, _score = _banded_local(query, target.text, dlo - BAND_PAD, dhi + BAND_PAD)
    aligned = [op for op in ops if op[0] == "M"]
    if len(aligned) < MIN_ALIGNED:
        out.update(status="no_match", verdict=gettext("Doesn't match this plasmid."))
        return out

    tseq = target.text
    matches = columns = 0
    diffs = []
    for op, qi, tj in ops:
        if op == "M":
            qb, tb = query[qi], tseq[tj]
            if qb not in "ACGT":
                out["ambiguous"] += 1
                continue
            columns += 1
            if _compatible(qb, tb):
                matches += 1
            else:
                diffs.append({"kind": "mismatch", "t": tj, "q": [qi], "plasmid": tb, "read": qb})
        elif op == "I":
            columns += 1
            last = diffs[-1] if diffs else None
            if last and last["kind"] == "insertion" and last["t"] == tj and last["q"][-1] == qi - 1:
                last["q"].append(qi)
                last["read"] += query[qi]
            else:
                diffs.append({"kind": "insertion", "t": tj, "q": [qi], "plasmid": "", "read": query[qi]})
        else:
            columns += 1
            last = diffs[-1] if diffs else None
            if last and last["kind"] == "deletion" and last["t_end"] == tj - 1 and last["q"] == [qi]:
                last["t_end"] = tj
                last["plasmid"] += tseq[tj]
            else:
                diffs.append({"kind": "deletion", "t": tj, "t_end": tj, "q": [qi], "plasmid": tseq[tj], "read": ""})
    identity = 100.0 * matches / columns if columns else 0.0
    if identity < MIN_IDENTITY:
        out.update(status="no_match", verdict=gettext("Doesn't match this plasmid."),
                   identity=round(identity, 1))
        return out

    def read_index(qi: int) -> int:
        """The query's index as a position in the read as it was given."""
        return start + qi if strand == "forward" else end - 1 - qi

    def quality_at(qis: list[int]) -> int | None:
        if not read.quality:
            return None
        values = [read.quality[read_index(q)] for q in qis if 0 <= read_index(q) < len(read.quality)]
        return min(values) if values else None

    t_first, t_last = aligned[0][2], aligned[-1][2]
    q_first, q_last = aligned[0][1], aligned[-1][1]
    out.update(placed=True, strand=strand, start=t_first % L + 1, end=t_last % L + 1,
               across_origin=t_first % L > t_last % L, identity=round(identity, 1), aligned_bases=len(aligned),
               covered=min(t_last - t_first + 1, L),
               read_span=sorted([read_index(q_first) + 1, read_index(q_last) + 1]))

    # Each difference in plasmid coordinates, with the read's quality there.
    reported = []
    for d in diffs:
        pos = d["t"] % L
        if d["kind"] == "deletion":
            # The read's quality on both sides of the missing bases.
            flank = [d["q"][0], d["q"][0] + 1]
            q = quality_at([x for x in flank if 0 <= x < len(query)])
            item = {"kind": "deletion", "position": pos + 1, "end": d["t_end"] % L + 1, "plasmid": d["plasmid"],
                    "read": "", "length": len(d["plasmid"]), "quality": q}
        elif d["kind"] == "insertion":
            q = quality_at(d["q"])
            item = {"kind": "insertion", "position": pos + 1, "plasmid": "", "read": d["read"],
                    "length": len(d["read"]), "quality": q}
        else:
            q = quality_at(d["q"])
            item = {"kind": "mismatch", "position": pos + 1, "plasmid": d["plasmid"], "read": d["read"], "length": 1,
                    "quality": q}
        item["read_position"] = read_index(d["q"][0]) + 1
        item["confident"] = q is None or q >= GOOD_QUALITY
        picture = trace_window(read, read_index(d["q"][0]), strand)
        if picture is not None:
            item["trace"] = picture
        item["_pos"] = pos
        reported.append(item)

    # The features each falls in, and what it does to a CDS. Mismatches in
    # one codon are taken together.
    codon_changes: dict[tuple[str, int], dict[int, str]] = {}
    for item in reported:
        pos = item["_pos"]
        item["features"] = [f["name"] for f in features if _contains(f["ranges"], pos)]
        if item["kind"] == "mismatch":
            for f in features:
                if "coding" in f and pos in f["coding_index"]:
                    ci = f["coding_index"][pos] - f["frame"]
                    if ci >= 0:
                        codon_changes.setdefault((f["name"], id(f), ci // 3), {})[pos] = item["read"]
    for item in reported:
        pos = item["_pos"]
        effect = None
        for f in features:
            if "coding" not in f:
                continue
            if item["kind"] == "mismatch" and pos in f["coding_index"]:
                ci = f["coding_index"][pos] - f["frame"]
                if ci >= 0:
                    effect = _substitution_effect(target.sequence, f, codon_changes[(f["name"], id(f), ci // 3)])
            elif item["kind"] != "mismatch" and pos in f["coding_index"]:
                effect = _indel_effect(f, pos, item["length"])
            if effect:
                break
        item["effect"] = effect
        item.pop("_pos")
        item["text"] = _diff_text(item)
    out["differences"] = reported
    out["low_quality"] = sum(1 for d in reported if not d["confident"])
    confident = [d for d in reported if d["confident"]]
    span = _span_text(out["start"], out["end"])
    partial = (q_last - q_first + 1) < 0.7 * len(query)
    if confident:
        out["status"] = "differences"
        out["verdict"] = ngettext("%(num)s difference across %(span)s: %(list)s.",
                                  "%(num)s differences across %(span)s: %(list)s.", len(confident), span=span,
                                  list=_listed(confident, 6))
    else:
        out["status"] = "match"
        out["verdict"] = gettext("Matches across %(span)s.", span=span)
    if partial:
        out["verdict"] += " " + gettext("Only bases %(start)s–%(end)s of the read follow the map: the rest is something else (an insert, a deletion, another plasmid?).",
                                        start=_n(out["read_span"][0]), end=_n(out["read_span"][1]))
    # Two templates at once: second peaks under many good bases.
    marked = second_peaks(read, start, end)
    if len(marked) >= MIXED_MIN and len(marked) >= MIXED_SHARE * (end - start):
        onset = mixed_from(marked, start, end)
        at_plasmid = None
        if onset is not None:
            qi = onset - start if strand == "forward" else end - 1 - onset
            near = [tj for op, q2, tj in ops if op == "M" and q2 >= qi] or [tj for op, q2, tj in ops if op == "M"]
            at_plasmid = (near[0] if strand == "forward" else near[-1]) % L + 1 if near else None
        out["mixed"] = {"bases": len(marked), "share": round(len(marked) / (end - start), 3),
                        "from_read": onset + 1 if onset is not None else None, "from_plasmid": at_plasmid,
                        "trace": trace_window(read, onset if onset is not None else marked[0], strand)}
        if onset is not None:
            out["verdict"] += " " + gettext("From read base %(read)s (near %(position)s on the map) the trace shows two sequences at once: probably two clones that differ by an insertion or deletion there. Pick a single colony and sequence it again.",  # noqa: E501
                                            read=_n(onset + 1), position=_n(at_plasmid) if at_plasmid else "?")
        else:
            out["verdict"] += " " + gettext("The trace has a second peak under %(num)s of its good bases (%(share)s): it may be a mix of two clones. Pick a single colony and sequence it again.",  # noqa: E501
                                            num=_n(len(marked)), share=f"{100 * len(marked) / (end - start):.0f}%")
    return out


# ---------------------------------------------------------------- the clone

def _covered_runs(mask: bytearray, circular: bool) -> list[list[int]]:
    """Runs of covered positions, 1-based inclusive; a run across the
    origin of a circular plasmid is one, start > end."""
    runs, i, L = [], 0, len(mask)
    while i < L:
        if mask[i]:
            j = i
            while j + 1 < L and mask[j + 1]:
                j += 1
            runs.append([i + 1, j + 1])
            i = j + 1
        else:
            i += 1
    if circular and len(runs) > 1 and runs[0][0] == 1 and runs[-1][1] == L:
        runs[0] = [runs[-1][0], runs[0][1]]
        runs.pop()
    return runs


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return gettext("%(some)s and %(last)s", some=", ".join(names[:-1]), last=names[-1])


def verify(sequence: str, circular: bool, annotations: list, reads: list[Read], name: str = "") -> dict:
    """Every read checked against the plasmid, and the clone's verdict."""
    sequence = (sequence or "").upper()
    L = len(sequence)
    target = Target(sequence, circular, max((len(r.bases) for r in reads), default=0))
    features = map_features(annotations, L)
    checked = [check_read(r, target, features) for r in reads]
    placed = [r for r in checked if r["placed"]]
    mask = bytearray(L)
    for r in placed:
        s, e = r["start"] - 1, r["end"] - 1
        for p in (range(s, e + 1) if s <= e else [*range(s, L), *range(0, e + 1)]):
            mask[p] = 1
    runs = _covered_runs(mask, circular)
    covered_bp = sum(mask)
    whole = [f["name"] for f in features if all(mask[p] for p in _positions(f["ranges"], L))]
    partly = [f["name"] for f in features if f["name"] not in whole and any(mask[p] for p in _positions(f["ranges"], L))]

    # The same difference seen in several reads is one; each says which
    # reads saw it, and how many reads cover that place.
    merged: dict[tuple, dict] = {}
    for r in placed:
        for d in r["differences"]:
            key = (d["kind"], d["position"], d["plasmid"], d["read"])
            m = merged.get(key)
            if m is None:
                m = merged[key] = {k: v for k, v in d.items() if k not in ("read_position",)}
                m["reads"], m["strands"], m["confident"] = [], [], False
            m["reads"].append(r["name"])
            if r["strand"] not in m["strands"]:
                m["strands"].append(r["strand"])
            m["confident"] = m["confident"] or d["confident"]
            if d["quality"] is not None and (m["quality"] is None or d["quality"] > m["quality"]):
                m["quality"] = d["quality"]
    differences = sorted(merged.values(), key=lambda d: (d["position"], d["kind"]))
    for d in differences:
        p = d["position"] - 1
        d["reads_covering"] = sum(1 for r in placed if _contains([(r["start"] - 1, r["end"] - 1)], p))
        d["text"] = _diff_text(d)
    confident = [d for d in differences if d["confident"]]
    spans = " · ".join(_span_text(s, e) for s, e in runs)

    if not reads:
        status, verdict = "no_match", gettext("No reads to check.")
    elif not placed:
        status, verdict = "no_match", (gettext("The read doesn't match this plasmid.") if len(reads) == 1 else
                                       gettext("None of the reads matches this plasmid."))
    elif confident:
        status = "differences"
        verdict = ngettext("%(num)s difference from the map: %(list)s.", "%(num)s differences from the map: %(list)s.",
                           len(confident), list=_listed(confident, 8))
    else:
        status = "match"
        if whole:
            verdict = gettext("Matches the map across %(spans)s, covering %(features)s.", spans=spans,
                              features=_join(whole))
        else:
            verdict = gettext("Matches the map across %(spans)s.", spans=spans)
    if placed and partly:
        verdict += " " + gettext("Part of %(features)s isn't covered.", features=_join(partly))
    unplaced = [r for r in checked if not r["placed"]]
    if placed and unplaced:
        verdict += " " + ngettext("%(num)s read doesn't match or was too poor to check.",
                                  "%(num)s reads don't match or were too poor to check.", len(unplaced))
    mixed = [r["name"] for r in placed if r.get("mixed")]
    if mixed:
        verdict += " " + ngettext("%(names)s looks like a mix of two clones: see its trace.",
                                  "%(names)s look like a mix of two clones: see their traces.", len(mixed),
                                  names=_join(mixed))
    low = [d for d in differences if not d["confident"]]
    if placed and low and not confident:
        verdict += " " + ngettext("%(num)s difference at low quality is likely a sequencing error.",
                                  "%(num)s differences at low quality are likely sequencing errors.", len(low))
    return {"plasmid": {"name": name, "length": L, "circular": bool(circular)},
            "status": status, "verdict": verdict, "reads": checked, "covered": runs, "covered_bp": covered_bp,
            "features_covered": whole, "features_partly": partly, "differences": differences,
            "map": [{"name": f["name"], "type": f["type"], "ranges": [[s + 1, e + 1] for s, e in f["ranges"]],
                     "direction": f["direction"]} for f in features]}


def without_traces(report: dict) -> dict:
    """The report without the pictures' samples (for the API and the
    assistants, who read the words): `has_trace` says one was there."""
    for item in [*report.get("differences", []), *(d for r in report.get("reads", []) for d in r.get("differences", [])),
                 *(r["mixed"] for r in report.get("reads", []) if r.get("mixed"))]:
        if item.pop("trace", None) is not None:
            item["has_trace"] = True
    return report


def reads_from_uploads(files: list[tuple[str, bytes]], text: str = "") -> tuple[list[Read], list[str]]:
    """The reads in uploaded files and pasted text, and a sentence for each
    file that couldn't be read. Each read's `extra["file"]` is the index of
    the file it came from (None: pasted)."""
    reads, problems = [], []
    for n, (filename, data) in enumerate(files):
        try:
            found = read_file(filename, data)
        except ReadError as e:
            problems.append(str(e))
            continue
        for r in found:
            r.extra["file"] = n
        reads.extend(found)
    if (text or "").strip():
        try:
            found = reads_in_text(text, gettext("Pasted read"))
        except ReadError as e:
            problems.append(str(e))
        else:
            for r in found:
                r.extra["file"] = None
            reads.extend(found)
    for r in reads:
        r.bases = r.bases[:MAX_READ_BASES]
        if r.quality:
            r.quality = r.quality[:MAX_READ_BASES]
    if len(reads) > MAX_READS:
        problems.append(gettext("Only the first %(n)s reads were checked.", n=MAX_READS))
        reads = reads[:MAX_READS]
    return reads, problems
