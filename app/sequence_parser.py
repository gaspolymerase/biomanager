"""Parse FASTA + GenBank into a normalized form for the plasmid DB.

We don't pull in BioPython for this — a single-purpose ~150-line parser
covers our needs (display in SeqViz, store features), and avoids a 50+ MB
dep on numpy + biopython.

A file holding several records keeps the first; "records" says how many
there were, so the caller can say so.

Output shape (also what gets handed to SeqViz):
{
  "sequence": "ATGCGTGCAT...",
  "is_circular": True,
  "format": "genbank",
  "name": "pCAG-GFP",
  "features": [
    {"name": "EGFP", "start": 0, "end": 720, "type": "CDS",
     "direction": 1, "color": "#a4d4a4", "notes": ""},
    ...
  ]
}
"""
from __future__ import annotations

import re

# Every IUPAC nucleotide letter: the four bases, U, and the ambiguity codes
# (R = A/G, Y = C/T, … N = any). A sequence keeps all of them; dropping the
# ambiguity codes shifts every downstream coordinate.
IUPAC_BASES = "ACGTURYKMSWBDHVN"
_NOT_IUPAC = re.compile(f"[^{IUPAC_BASES}{IUPAC_BASES.lower()}]")
# What raw pasted text may contain besides bases: whitespace and the
# position numbers of a GenBank ORIGIN block or a numbered listing.
_RAW_NOISE = re.compile(r"[\s\d]")


def clean_bases(text: str) -> str:
    """Keep the IUPAC nucleotide letters, uppercased; drop everything else."""
    return _NOT_IUPAC.sub("", text or "").upper()


def is_iupac(sequence: str) -> bool:
    """True when every character is an IUPAC nucleotide letter: no digits,
    spaces or punctuation. For a sequence about to be stored as it is."""
    return bool(sequence) and not _NOT_IUPAC.search(sequence)


def looks_like_bases(text: str) -> bool:
    """True when text is only bases, whitespace and position numbers.

    The raw-sequence fallback uses this so a binary file or a Word document
    is refused rather than mined for the few letters that happen to be
    A, C, G or T."""
    body = _RAW_NOISE.sub("", text or "")
    return bool(body) and not _NOT_IUPAC.search(body)


# How much of a feature's qualifiers is kept. Generous: an Addgene note with
# its citation, or a whole /translation, must survive the round trip.
NOTE_LIMIT = 10000
QUALIFIER_LIMIT = 60


def qualifier_notes(pairs) -> dict:
    """(key, value) pairs as the editor's notes shape, {key: [values]}: a
    key may repeat (two /note lines), and nothing is cut short."""
    notes: dict[str, list[str]] = {}
    for key, value in pairs:
        key = str(key).strip()[:60]
        if not key or (key not in notes and len(notes) >= QUALIFIER_LIMIT):
            continue
        notes.setdefault(key, []).append(str(value)[:NOTE_LIMIT])
    return notes


def _first(notes: dict, *keys) -> str:
    for key in keys:
        for value in notes.get(key) or []:
            if value.strip():
                return value.strip()
    return ""


# Pastel colors used to tint features by type. Same palette SnapGene-ish
# uses; SeqViz will apply these via feature.color directly.
FEATURE_TYPE_COLORS = {
    "CDS": "#a4d4a4",
    "gene": "#a4d4a4",
    "promoter": "#ffd86b",
    "terminator": "#ff9b6b",
    "rep_origin": "#c8a4d4",
    "polyA_signal": "#ffb8d4",
    "primer_bind": "#9bb8e0",
    "regulatory": "#ffd86b",
    "misc_feature": "#d0d0d0",
    "5'UTR": "#e0c89b",
    "3'UTR": "#e0c89b",
    "exon": "#a4d4a4",
    "intron": "#d4c8a4",
    "source": "#e0e0e0",
    "tag": "#ff6b6b",
    "tRNA": "#9bb8e0",
    "rRNA": "#9bb8e0",
    "enhancer": "#ffd86b",
}


def parse_fasta(raw: str) -> dict | None:
    raw = raw.strip()
    if not raw.startswith(">"):
        return None
    lines = raw.splitlines()
    name = lines[0][1:].strip().split()[0] if lines[0][1:].strip() else "sequence"
    # A file of several records (primers, contigs) is not one sequence:
    # keep the first and say so, rather than gluing them into a chimera.
    headers = [i for i, line in enumerate(lines) if line.startswith(">")]
    if len(headers) > 1:
        lines = lines[:headers[1]]
    body = "".join(l.strip() for l in lines[1:] if not l.startswith(">"))
    # Alignment gaps and a stop mark are tolerated; any other letter means
    # this is not a nucleotide FASTA (a protein, or prose).
    if not looks_like_bases(re.sub(r"[-*.]", "", body)):
        return None
    seq = clean_bases(body)
    return {
        "sequence": seq,
        "is_circular": False,  # FASTA gives no topology hint; default linear
        "format": "fasta",
        "name": name,
        "features": [],
        "records": len(headers),
    }


# GenBank LOCUS line: "LOCUS       name      length bp    DNA     circular ..."
# A long name can run into the length ("…-long5563 bp"); the topology is read
# from the line whatever the name does, and such a name is split off later,
# once the sequence's length is known.
_LOCUS_NAME_RE = re.compile(r"^LOCUS\s+(\S+)\s+\d+\s+bp", re.IGNORECASE)
_LOCUS_JOINED_RE = re.compile(r"^LOCUS\s+(\S+?)(\d+)\s+bp", re.IGNORECASE)
_LOCUS_TOPO_RE = re.compile(r"\b(linear|circular)\b", re.IGNORECASE)


def parse_genbank(raw: str) -> dict | None:
    if "LOCUS" not in raw or "ORIGIN" not in raw:
        return None

    name = ""
    locus_line = ""
    is_circular = False
    seq = ""
    features: list[dict] = []

    records = len(re.findall(r"^LOCUS\b", raw, re.MULTILINE))
    lines = raw.splitlines()
    state = "header"
    current_feature: dict | None = None
    pending_qualifier_key: str | None = None
    pending_qualifier_value: list[str] = []

    def flush_qualifier():
        nonlocal pending_qualifier_key, pending_qualifier_value
        if current_feature and pending_qualifier_key:
            # A translation wraps without spaces; text wraps at word breaks.
            joiner = "" if pending_qualifier_key == "translation" else " "
            val = joiner.join(pending_qualifier_value).strip()
            if len(val) >= 2 and val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            current_feature.setdefault("qualifiers", []).append((pending_qualifier_key, val.replace('""', '"')))
        pending_qualifier_key = None
        pending_qualifier_value = []

    def flush_feature():
        if current_feature:
            features.append(current_feature)

    for raw_line in lines:
        if state == "header":
            if raw_line[:5].upper() == "LOCUS" and not locus_line:
                locus_line = raw_line
                m = _LOCUS_NAME_RE.match(raw_line)
                if m:
                    name = m.group(1)
                topo = _LOCUS_TOPO_RE.search(raw_line)
                if topo:
                    is_circular = topo.group(1).lower() == "circular"
            if raw_line.startswith("FEATURES"):
                state = "features"
                continue
            if raw_line.startswith("ORIGIN"):
                state = "origin"
                continue

        elif state == "features":
            if raw_line.startswith("ORIGIN"):
                flush_qualifier()
                flush_feature()
                current_feature = None
                state = "origin"
                continue
            # Feature line: 5-char indent, type starting at col 5
            if raw_line.startswith("     ") and len(raw_line) >= 22 and raw_line[5] != " ":
                # New feature row.
                flush_qualifier()
                flush_feature()
                current_feature = {}
                ftype = raw_line[5:20].strip()
                location = raw_line[21:].strip()
                current_feature["type"] = ftype
                current_feature["location_raw"] = location
                # Continue reading location across continuation lines.
                pending_qualifier_key = None
            elif raw_line.startswith("                     "):
                # Continuation (col 21+). Either qualifier (/key=val) or
                # continuation of location.
                content = raw_line[21:].rstrip()
                if content.startswith("/"):
                    flush_qualifier()
                    if "=" in content:
                        k, v = content[1:].split("=", 1)
                    else:
                        k, v = content[1:], ""
                    pending_qualifier_key = k.strip()
                    pending_qualifier_value = [v.strip()]
                elif pending_qualifier_key:
                    pending_qualifier_value.append(content.strip())
                elif current_feature is not None:
                    # Continuation of the location string.
                    current_feature["location_raw"] += content.strip()

        elif state == "origin":
            # Sequence lines: " 1 atgc gtgc atcg ..."
            if raw_line.startswith("//"):
                break
            # Strip leading position number + whitespace.
            cleaned = re.sub(r"\d+", "", raw_line).replace(" ", "").strip()
            seq += cleaned

    seq = clean_bases(seq)
    if not seq:
        return None
    if not name and locus_line:
        joined = _LOCUS_JOINED_RE.match(locus_line)
        token = (joined.group(1) + joined.group(2)) if joined else ""
        if token.endswith(str(len(seq))) and len(token) > len(str(len(seq))):
            name = token[: -len(str(len(seq)))]

    # Normalize features: parse the location into start/end + direction.
    normalized = []
    for feat in features:
        if feat.get("type") in ("source", ""):
            # Source covers the whole sequence — skip in feature display.
            continue
        start, end, direction = _parse_location(feat.get("location_raw", ""), len(seq))
        if start is None or end is None:
            continue
        # Pick a feature name in priority order.
        q = qualifier_notes(feat.get("qualifiers", []))
        name_val = (
            _first(q, "label", "gene", "product", "note")
            or feat.get("type")
            or "feature"
        ).split(";")[0][:80]
        normalized.append({
            "name": name_val,
            "start": start,
            "end": end,
            "type": feat.get("type", "misc_feature"),
            "direction": direction,
            "color": FEATURE_TYPE_COLORS.get(feat.get("type", ""), "#d0d0d0"),
            "notes": q,
        })

    return {
        "sequence": seq,
        "is_circular": is_circular,
        "format": "genbank",
        "name": name,
        "features": normalized,
        "records": max(records, 1),
    }


_LOC_NUM = re.compile(r"(\d+)\s*\.\.\s*(\d+)")


def span_of_parts(parts: list[tuple[int, int]]) -> tuple[int, int]:
    """One (start, end) for a feature made of several 0-based parts.

    Parts listed in order that step backwards cross the origin of a
    circular sequence: join(55..60,1..5) runs 55→60 then 1→5. That span is
    kept as start > end, which is how the map (Open Vector Editor) draws a
    feature wrapping the origin; folding it to min..max would paint the
    whole plasmid instead. Parts that run forwards give the outer bounds."""
    if len(parts) == 1:
        return parts[0]
    wraps = any(parts[i + 1][0] < parts[i][0] for i in range(len(parts) - 1))
    if wraps:
        return parts[0][0], parts[-1][1]
    return min(s for s, _ in parts), max(e for _, e in parts)


def _parse_location(loc: str, length: int = 0) -> tuple[int | None, int | None, int]:
    """Parse a GenBank location string. Returns (start, end, direction) where
    indices are 0-based inclusive ends as in SeqViz expectations. A feature
    crossing the origin comes back with start > end (see span_of_parts)."""
    if not loc:
        return None, None, 1
    direction = -1 if "complement" in loc else 1
    # Partial-end markers (<1..>20) do not change the span.
    loc = loc.replace("<", "").replace(">", "")
    matches = _LOC_NUM.findall(loc)
    if not matches:
        # Single position like "123"
        m = re.search(r"\d+", loc)
        if not m:
            return None, None, direction
        pos = int(m.group(0)) - 1
        return pos, pos, direction
    parts = [(int(a) - 1, int(b) - 1) for a, b in matches]
    # join(complement(60..55), complement(5..1)) style lists the minus-strand
    # parts last-first; put them back in sequence order.
    if direction == -1 and not loc.lstrip().startswith("complement") and len(parts) > 1:
        parts.reverse()
    start, end = span_of_parts(parts)
    if length and (start >= length or end >= length):
        return None, None, direction
    return start, end, direction


def parse_sequence_text(raw: str) -> dict | None:
    """Auto-detect FASTA vs GenBank vs raw-bases input and parse. A
    byte-order mark (Windows editors add one) is ignored, and a GenBank file
    may have comment or blank lines before its LOCUS line."""
    raw = (raw or "").lstrip("\ufeff")
    raw_strip = raw.strip()
    if raw_strip.startswith(">"):
        return parse_fasta(raw)
    if re.search(r"^LOCUS\b", raw_strip[:4000], re.MULTILINE | re.IGNORECASE):
        return parse_genbank(raw)
    # Treat as raw bases, but only if that is all it is.
    if not looks_like_bases(raw_strip):
        return None
    return {
        "sequence": clean_bases(raw_strip),
        "is_circular": False,
        "format": "raw",
        "name": "",
        "features": [],
        "records": 1,
    }


# ---------------------------------------------------------------------------
# SnapGene .dna binary format.
#
# Format (reverse-engineered, used by many open-source readers):
#
#   - Bytes 0..8: magic "cookie" — starts with byte 0x09, then 8 bytes
#     including the ASCII string "SnapGene".
#   - Then a series of chunks, each:
#       1 byte: type
#       4 bytes: length (big-endian uint32)
#       N bytes: payload
#
#   Types we care about:
#     0x00 — DNA sequence chunk:
#       1 byte flags (bit 0 set = circular topology)
#       N-1 bytes: sequence (ASCII)
#     0x05 — Primers (XML, optional)
#     0x06 — Notes (HTML/XML)
#     0x0A — Features (XML with <Features><Feature>... </Feature></Features>)
#
# We extract sequence + topology from 0x00 and features from 0x0A. The
# features XML is parsed with xml.etree (stdlib) — no extra dep.
# ---------------------------------------------------------------------------

import struct
import xml.etree.ElementTree as _ET


def parse_snapgene_dna(raw_bytes: bytes) -> dict | None:
    if len(raw_bytes) < 14 or raw_bytes[0] != 0x09:
        return None
    # The first chunk is the cookie chunk; spec says length = 14, payload
    # ends with "SnapGene" or the like. We just skip past it by reading the
    # standard chunk header.
    pos = 0
    sequence = ""
    is_circular = False
    features_xml: str | None = None
    name = ""
    description = ""

    while pos + 5 <= len(raw_bytes):
        chunk_type = raw_bytes[pos]
        chunk_length = struct.unpack(">I", raw_bytes[pos + 1 : pos + 5])[0]
        pos += 5
        end = pos + chunk_length
        if end > len(raw_bytes):
            break
        payload = raw_bytes[pos:end]
        pos = end

        if chunk_type == 0x00 and len(payload) >= 1:
            flags = payload[0]
            is_circular = bool(flags & 0x01)
            try:
                sequence = payload[1:].decode("ascii", errors="ignore")
            except Exception:
                sequence = ""
        elif chunk_type == 0x0A:
            try:
                features_xml = payload.decode("utf-8", errors="ignore")
            except Exception:
                features_xml = None
        elif chunk_type == 0x06:
            # Notes: the map's name (CustomMapLabel) and its description.
            try:
                notes_root = _ET.fromstring(payload.decode("utf-8", errors="ignore"))
            except _ET.ParseError:
                notes_root = None
            if notes_root is not None:
                label = (notes_root.findtext("CustomMapLabel") or "").strip()
                if label and (notes_root.findtext("UseCustomMapLabel") or "1").strip() != "0":
                    name = label[:200]
                description = re.sub(r"<[^>]+>", " ", notes_root.findtext("Description") or "")
                description = re.sub(r"\s+", " ", description).strip()[:NOTE_LIMIT]

    if not sequence:
        return None

    sequence = clean_bases(sequence)
    if not sequence:
        return None
    features = _parse_snapgene_features_xml(features_xml) if features_xml else []

    return {
        "sequence": sequence,
        "is_circular": is_circular,
        "format": "snapgene",
        "name": name,
        "description": description,
        "features": features,
        "records": 1,
    }


def _parse_snapgene_features_xml(xml_text: str) -> list[dict]:
    """Parse the <Features> XML chunk from a SnapGene .dna file."""
    features: list[dict] = []
    try:
        root = _ET.fromstring(xml_text)
    except _ET.ParseError:
        return features

    # XML structure: <Features><Feature name="..." type="..." directionality="...">
    #   <Segment range="start-end" color="#rrggbb"/>
    #   <Q name="note"><V text="..."/></Q>
    # </Feature></Features>
    for f in root.iter("Feature"):
        fname = f.attrib.get("name") or f.attrib.get("type") or "feature"
        ftype = f.attrib.get("type", "misc_feature")
        # SnapGene directionality: 1 = forward, 2 = reverse, 3 = both, 0 = none.
        d_raw = f.attrib.get("directionality", "0")
        try:
            d_int = int(d_raw)
        except ValueError:
            d_int = 0
        direction = -1 if d_int == 2 else (1 if d_int == 1 else 0)

        # Collect all segments to find the overall span. SnapGene uses
        # 1-based inclusive coordinates in "start-end" format; a segment
        # across the origin reads end < start, which is kept (span_of_parts).
        parts: list[tuple[int, int]] = []
        seg_color = ""
        for seg in f.iter("Segment"):
            rng = seg.attrib.get("range", "")
            if not rng:
                continue
            try:
                s, e = rng.split("-", 1)
                parts.append((int(s) - 1, int(e) - 1))
            except ValueError:
                continue
            seg_color = seg_color or seg.attrib.get("color", "")
        if not parts:
            continue
        start, end = span_of_parts(parts)

        # Every qualifier: <Q name="note"><V text="…"/></Q>, or int= / predef=.
        pairs = []
        for q in f.iter("Q"):
            key = q.attrib.get("name") or ""
            for v in q.findall("V"):
                value = v.attrib.get("text", v.attrib.get("int", v.attrib.get("predef", "")))
                pairs.append((key, re.sub(r"<[^>]+>", "", value).strip()))

        features.append({
            "name": (fname or "feature")[:80],
            "start": start,
            "end": end,
            "type": ftype,
            "direction": direction,
            "color": seg_color or FEATURE_TYPE_COLORS.get(ftype, "#d0d0d0"),
            "notes": qualifier_notes(pairs),
        })
    return features


def parse_sequence_bytes(raw_bytes: bytes, filename: str = "") -> dict | None:
    """Top-level dispatcher that handles both text and binary uploads.

    SnapGene .dna files are binary; FASTA/GenBank are text. We detect by
    looking at the magic byte (0x09 for .dna) before falling back to text
    decode.
    """
    if not raw_bytes:
        return None
    # SnapGene .dna magic.
    if raw_bytes[:1] == b"\x09":
        parsed = parse_snapgene_dna(raw_bytes)
        if parsed:
            return parsed
    # A .dna that is not SnapGene, or any file with NUL bytes, is binary:
    # there is no sequence text to fall back to.
    if filename.lower().endswith(".dna") or b"\x00" in raw_bytes[:4096]:
        return None
    # Text fallback.
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw_bytes.decode("latin-1")
        except UnicodeDecodeError:
            return None
    return parse_sequence_text(text)
