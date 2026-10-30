"""A coding sequence's protein: its amino acids and what a lab looks up
about one (length, mass, isoelectric point, extinction coefficient), and the
open reading frames of a plasmid in all six frames.

Pure: bases in, letters and numbers out. The plasmid page's Sequence tab
(app/sequence_routes.py) shows them.
"""
from __future__ import annotations

import re

from .seqverify import CODE, reverse_complement

# Average residue masses (Da), as ExPASy's ProtParam uses; a protein adds
# one water.
RESIDUE_MASS = {
    "A": 71.0788, "R": 156.1875, "N": 114.1038, "D": 115.0886, "C": 103.1388, "E": 129.1155, "Q": 128.1307,
    "G": 57.0519, "H": 137.1411, "I": 113.1594, "L": 113.1594, "K": 128.1741, "M": 131.1926, "F": 147.1766,
    "P": 97.1167, "S": 87.0782, "T": 101.1051, "W": 186.2132, "Y": 163.1760, "V": 99.1326,
}
WATER = 18.01524
# pK values close to Bjellqvist's, which ExPASy's ProtParam uses: the ends,
# then the side chains that carry a charge.
PKA_N, PKA_C = 7.5, 3.55
PKA_POSITIVE = {"K": 10.0, "R": 12.0, "H": 5.98}
PKA_NEGATIVE = {"D": 4.05, "E": 4.45, "C": 9.0, "Y": 10.0}
MIN_ORF = 100            # codons: shorter open frames are left out


def translate(bases: str, to_stop: bool = False) -> str:
    """The amino acids of `bases` read from its first base, * for a stop."""
    seq = re.sub(r"[^ACGTU]", "N", (bases or "").upper()).replace("U", "T")
    out = []
    for i in range(0, len(seq) - 2, 3):
        aa = CODE.get(seq[i:i + 3], "X")
        if aa == "*" and to_stop:
            break
        out.append(aa)
    return "".join(out)


def mass(protein: str) -> float:
    """In daltons, from the average residue masses."""
    letters = [a for a in protein if a in RESIDUE_MASS]
    return sum(RESIDUE_MASS[a] for a in letters) + (WATER if letters else 0.0)


def _charge(protein: str, ph: float) -> float:
    positive = 1 / (1 + 10 ** (ph - PKA_N)) + sum(
        protein.count(a) / (1 + 10 ** (ph - pka)) for a, pka in PKA_POSITIVE.items())
    negative = 1 / (1 + 10 ** (PKA_C - ph)) + sum(
        protein.count(a) / (1 + 10 ** (pka - ph)) for a, pka in PKA_NEGATIVE.items())
    return positive - negative


def isoelectric_point(protein: str) -> float:
    """The pH at which the protein carries no net charge, by halving."""
    lo, hi = 0.0, 14.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if _charge(protein, mid) > 0:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 2)


def extinction(protein: str) -> tuple[int, int]:
    """Molar extinction at 280 nm (M⁻¹ cm⁻¹), Pace's values: with every
    cysteine paired in a cystine, and with none."""
    w, y, c = protein.count("W"), protein.count("Y"), protein.count("C")
    reduced = w * 5500 + y * 1490
    return reduced + (c // 2) * 125, reduced


def about(bases: str) -> dict:
    """Everything the protein panel shows for a coding sequence's bases."""
    seq = re.sub(r"[^ACGT]", "", (bases or "").upper())
    full = translate(seq)
    stop = full.find("*")
    protein = full if stop < 0 else full[:stop]
    eps, eps_reduced = extinction(protein)
    da = mass(protein)
    return {
        "protein": protein,
        "length": len(protein),
        "mass_kda": round(da / 1000, 1),
        "pi": isoelectric_point(protein) if protein else None,
        "extinction": eps, "extinction_reduced": eps_reduced,
        # Absorbance at 280 nm of 1 mg/mL.
        "a280_1mg": round(eps / da, 2) if da else None,
        "starts_with_m": protein[:1] == "M",
        "stop": seq[3 * stop:3 * stop + 3] if stop >= 0 else "",
        "internal_stop": 0 <= stop < len(full) - 1,
        "whole_codons": len(seq) % 3 == 0,
    }


def orfs(sequence: str, circular: bool, min_codons: int = MIN_ORF) -> list[dict]:
    """Open reading frames, ATG to stop, of at least `min_codons`, in all
    six frames: {start, end (1-based, inclusive, along the top strand),
    strand, frame, codons}. One reading across a circular plasmid's origin
    is found too."""
    seq = re.sub(r"[^ACGT]", "N", (sequence or "").upper())
    n = len(seq)
    if n < 3 * min_codons:
        return []
    found = []
    for strand, text in ((1, seq), (-1, reverse_complement(seq))):
        doubled = text + (text if circular else "")
        for frame in range(3):
            i = frame
            limit = n + frame if circular else n
            while i + 3 <= len(doubled) and i < limit:
                if doubled[i:i + 3] == "ATG":
                    j = i
                    while j + 3 <= len(doubled) and j - i < n and CODE.get(doubled[j:j + 3]) != "*":
                        j += 3
                    if j + 3 <= len(doubled) and j - i < n:
                        codons = (j - i) // 3
                        if codons >= min_codons:
                            a, b = i % n, (j + 2) % n            # ATG's A to the stop's last base, 0-based
                            if strand < 0:
                                a, b = n - 1 - b, n - 1 - a
                            found.append({"start": a + 1, "end": b + 1, "strand": strand, "frame": frame + 1,
                                          "codons": codons})
                        i = j + 3                                # the next ATG after this frame's stop
                        continue
                i += 3
    unique = {(o["start"], o["end"], o["strand"]): o for o in found}
    return sorted(unique.values(), key=lambda o: -o["codons"])
