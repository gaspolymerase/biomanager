"""A primer drawn on a plasmid, checked as a lab would before ordering it,
and what two primers would amplify.

Pure: sequences in, numbers out. The Tm is the Primers database's own
(inventory_service.primer_tm, nearest neighbours); the free energies here
use the same nearest-neighbour table at 37 °C, over the best run of pairs a
primer can make with itself (a hairpin) or with a copy of itself (a
dimer). Thresholds are the common rules of thumb, not a thermodynamic
model: a primer passing them is a good bet, one failing them is worth a
second look.

`binding` finds where a primer anneals, allowing up to `mismatches` bases
that don't pair as long as its last ANCHOR bases at the 3′ end do: the end
a polymerase extends from.
"""
from __future__ import annotations

import re

from .inventory_service import _NN, _PAIR, primer_tm
from .primer_records import reverse_complement

ANCHOR = 8               # 3′ bases that must pair for a primer to be extended
MIN_LENGTH = 15
MAX_LENGTH = 60
LOOP_MIN = 3             # bases a hairpin's loop needs
LOOP_PENALTY = 3.5       # kcal/mol for closing a small loop
HAIRPIN_OK = -2.0        # kcal/mol: a hairpin weaker than this melts at annealing
DIMER_OK = -6.0          # and a self-dimer weaker than this


def clean(seq: str) -> str:
    return re.sub(r"[^ACGT]", "", (seq or "").upper())


def _stack_dg(top: str) -> float:
    """ΔG37 of a run of pairs, `top` read 5′→3′ against its complement."""
    dg = 0.0
    for i in range(len(top) - 1):
        step = top[i:i + 2]
        found = _NN.get(step) or _NN.get(step.translate(_PAIR)[::-1])
        if found:
            h, s = found
            dg += h - 310.15 * s / 1000
    return dg


def _runs(a: str, b: str) -> list[tuple[int, int]]:
    """Runs of pairs between `a` and `b` laid antiparallel at every offset:
    (start in a, length)."""
    rc = reverse_complement(b)
    out = []
    for shift in range(-len(rc) + 1, len(a)):
        start = None
        for i in range(max(0, shift), min(len(a), shift + len(rc))):
            if a[i] == rc[i - shift]:
                start = i if start is None else start
            else:
                if start is not None and i - start >= 3:
                    out.append((start, i - start))
                start = None
        end = min(len(a), shift + len(rc))
        if start is not None and end - start >= 3:
            out.append((start, end - start))
    return out


def self_dimer(seq: str) -> float:
    """The strongest run of pairs one copy of the primer makes with another."""
    seq = clean(seq)
    return round(min((_stack_dg(seq[s:s + n]) for s, n in _runs(seq, seq)), default=0.0), 1)


def hairpin(seq: str) -> float:
    """The strongest stem the primer folds into, a loop of LOOP_MIN or more."""
    seq = clean(seq)
    best = 0.0
    n = len(seq)
    for i in range(n):
        for j in range(n - 1, i + LOOP_MIN, -1):
            k = 0
            while i + k < j - k - LOOP_MIN and seq[i + k] == seq[j - k].translate(_PAIR):
                k += 1
            if k >= 3:
                best = min(best, _stack_dg(seq[i:i + k]) + LOOP_PENALTY)
    return round(best, 1)


def gc(seq: str) -> int:
    seq = clean(seq)
    return round(100 * sum(1 for b in seq if b in "GC") / len(seq)) if seq else 0


def clamp(seq: str) -> bool:
    """A G/C clamp: the 3′ end is G or C, with no more than three G/C in its last five."""
    seq = clean(seq)
    return bool(seq) and seq[-1] in "GC" and sum(1 for b in seq[-5:] if b in "GC") <= 3


def binding(template: str, circular: bool, primer: str, mismatches: int = 0) -> list[dict]:
    """Where `primer` anneals on either strand: {start, end (0-based, along
    the top strand), direction, mismatches, tail}. Its 3′ ANCHOR bases pair
    exactly; the rest may have up to `mismatches` that don't. A 5′ tail
    that pairs with nothing (a site, an overlap) is allowed when no
    mismatch is: then the rest of the primer runs off the template."""
    template = clean(template) if template else ""
    primer = clean(primer)
    n = len(template)
    if n < len(primer) or len(primer) < ANCHOR + 4:
        return []
    search = template + (template[:len(primer)] if circular else "")
    found = []
    for direction in (1, -1):
        probe = primer if direction > 0 else reverse_complement(primer)
        anchor = probe[-ANCHOR:] if direction > 0 else probe[:ANCHOR]
        for m in re.finditer(f"(?={anchor})", search):
            at = m.start()
            if at >= n:
                continue
            if direction > 0:
                three = at + ANCHOR                       # one past the primer's 3′ end
                first = three - len(probe)
                if first < 0 and not circular:
                    continue
                window = "".join(template[(first + i) % n] for i in range(len(probe)))
                pairs = [window[i] == probe[i] for i in range(len(probe))]
            else:
                first = at
                window = search[first:first + len(probe)]
                if len(window) < len(probe):
                    continue
                pairs = [window[i] == probe[i] for i in range(len(probe))]
            wrong = pairs.count(False)
            # From the 3′ end, how far the primer pairs without a break.
            three_first = pairs[::-1] if direction > 0 else pairs
            run = next((i for i, ok in enumerate(three_first) if not ok), len(probe))
            if wrong <= mismatches:
                annealed, missed = len(probe), wrong
            elif run >= MIN_LENGTH:
                annealed, missed = run, 0               # the rest is a 5′ tail: a site, an overlap
            else:
                continue
            if direction > 0:
                end = (first + len(probe) - 1) % n
                start = (end - annealed + 1) % n
            else:
                start = first % n
                end = (first + annealed - 1) % n
            found.append({"start": start, "end": end, "direction": direction, "mismatches": missed,
                          "tail": len(probe) - annealed})
    return found


def check(seq: str, template: str = "", circular: bool = True) -> dict:
    """Everything the New primer panel says about a primer."""
    seq = clean(seq)
    sites = binding(template, circular, seq, mismatches=2) if template else []
    exact = [s for s in sites if s["mismatches"] == 0]
    tm = primer_tm(seq)
    dimer, fold = self_dimer(seq), hairpin(seq)
    return {
        "sequence": seq, "length": len(seq), "tm": round(tm, 1) if tm is not None else None, "gc": gc(seq),
        "clamp": clamp(seq), "hairpin": fold, "hairpin_ok": fold > HAIRPIN_OK,
        "dimer": dimer, "dimer_ok": dimer > DIMER_OK,
        "length_ok": 18 <= len(seq) <= 30, "gc_ok": 40 <= gc(seq) <= 60,
        "binds": len(exact), "near": len(sites) - len(exact),
    }


def pcr(template: str, circular: bool, forward: str, reverse: str) -> dict | None:
    """The product of two primers, each on its own strand facing the other:
    {start, end (1-based), length, sequence}, tails included. None when
    they don't face each other on this template."""
    template = clean(template)
    n = len(template)
    f = [s for s in binding(template, circular, forward) if s["direction"] > 0]
    r = [s for s in binding(template, circular, reverse) if s["direction"] < 0]
    best = None
    for a in f:
        for b in r:
            span = (b["end"] - a["start"]) % n + 1 if circular else b["end"] - a["start"] + 1
            if span <= 0 or (not circular and b["end"] < a["start"]) or span > n:
                continue
            if best is None or span < best[2]:
                best = (a, b, span)
    if best is None:
        return None
    a, b, span = best
    inner = "".join(template[(a["start"] + i) % n] for i in range(span))
    fwd, rev = clean(forward), clean(reverse)
    product = fwd[:a["tail"]] + inner + (reverse_complement(rev)[len(rev) - b["tail"]:] if b["tail"] else "")
    return {"start": a["start"] + 1, "end": b["end"] + 1, "length": len(product), "sequence": product,
            "template_length": span}
