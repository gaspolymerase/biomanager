"""Plate layouts: a plate reader's or qPCR machine's export, read onto an
experiment's animals.

An instrument gives one value per well (A1…H12, or 384 wells); a layout
says which of the experiment's animals is in which well, which wells are
the blank and the standards (each with its concentration), and leaves the
rest empty. Several wells for one animal are its replicates. A layout is
kept with the experiment (PlateLayout) to read the next plate onto.

Reading a plate (the experiment page's **Plate** dialog, or the
`plate_readout` tool over POST /api/v1/experiments/<id>/plate):

1. The export, pasted or as a CSV, TSV or Excel file, is read in the shapes
   instruments write (`parse`): a grid with row letters and column numbers
   (one grid per read, several reads in one file), or a list with a Well
   column and a value column (OD, Ct, Cq…), with a Target column when a
   qPCR run has several genes.
2. `analyse` lays the values on the layout, changing nothing: each animal's
   replicates averaged, the blank taken off or the standard curve read
   (a line, a log–log line or a 4PL: app/standard_curves.py), or for a
   readout BioManager works out
   (app/derived.py) the inputs it takes (OD and blank; the Cts of the target
   and reference genes). Replicates far from the others are flagged (more
   than half a cycle for a Ct, a fifth of the value otherwise), and wells
   past what an instrument reads (derived.CT_MAX, derived.OD_MAX) or that
   never came up are left out, saying so. Any well can be left out by hand.
3. Saving (`save`, the person's button) writes each animal's value as the
   experiment's readout for the day (or another measure of it, chosen
   under Readout: experiments.measures_of), as one batch that Batch
   history's Undo takes back whole, through experiments.set_reading, the
   way the sheet does, and keeps the plate as a PlateRead: the layout as it
   was, every well's value, the choices made, each animal's value, and the
   file it came from (an upload, served to whoever may see the experiment).
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from datetime import date, datetime

from flask import Blueprint, abort, g, jsonify, make_response, redirect, request, url_for
from sqlalchemy import func, select

from . import access, derived, i18n, standard_curves
from .db import SessionLocal
from .i18n import gettext

bp = Blueprint("plates", __name__, url_prefix="/experiments")

LETTERS = "ABCDEFGHIJKLMNOP"
FORMATS = {24: (4, 6), 48: (6, 8), 96: (8, 12), 384: (16, 24)}
ROLES = ("sample", "blank", "standard")
CT_TOLERANCE = 0.5          # cycles a replicate may be from the others' median
VALUE_TOLERANCE = 0.2       # or, for any other value, this fraction of it
MAX_TEXT = 2_000_000
_WELL = re.compile(r"^\s*([A-Pa-p])\s*0*(\d{1,2})\s*$")
NO_VALUE = ("undetermined", "undet", "n/a", "na", "nan", "no ct", "no cq", "-", "--", "invalid", "error")
OVER = ("over", "ovrflw", "overflow", "sat", "saturated", "high")


class PlateError(ValueError):
    """A plate or layout that can't be read or used, in words for the person."""


# ---------------------------------------------------------------- wells

def well_name(text) -> str | None:
    """"a01", "A1", " A 1" → "A1"; None for anything that isn't a well."""
    m = _WELL.match(str(text or ""))
    if not m:
        return None
    n = int(m.group(2))
    return f"{m.group(1).upper()}{n}" if 1 <= n <= 24 else None


def position(well: str) -> tuple[int, int]:
    return LETTERS.index(well[0]), int(well[1:]) - 1


def fits(well: str, fmt: int) -> bool:
    rows, cols = FORMATS[fmt]
    r, c = position(well)
    return r < rows and c < cols


def wells_of(fmt: int) -> list[str]:
    rows, cols = FORMATS[fmt]
    return [f"{LETTERS[r]}{c + 1}" for r in range(rows) for c in range(cols)]


def smallest_format(wells) -> int:
    """The smallest plate the wells fit on (an export's own size)."""
    for fmt in sorted(FORMATS):
        if all(fits(w, fmt) for w in wells):
            return fmt
    return 384


def check_format(raw) -> int:
    try:
        fmt = int(raw)
    except (TypeError, ValueError):
        fmt = 0
    if fmt not in FORMATS:
        raise PlateError(gettext("A plate has 24, 48, 96 or 384 wells."))
    return fmt


def clean_layout(raw, fmt: int, subject_keys: set[str] | None = None) -> dict[str, dict]:
    """A layout's wells as kept: {"A1": {"role": "sample", "subject": key},
    "H1": {"role": "blank"}, "G1": {"role": "standard", "conc": 10.0}}.
    A PlateError, in words, for a well off the plate, an animal not in the
    experiment or a standard without its concentration."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            raw = None
    if not isinstance(raw, dict):
        raise PlateError(gettext("The layout is a list of wells."))
    out: dict[str, dict] = {}
    for name, spec in raw.items():
        well = well_name(name)
        if well is None or not fits(well, fmt):
            raise PlateError(gettext("%(well)s isn't a well of a %(n)s-well plate.", well=name, n=fmt))
        if not spec:
            continue
        if isinstance(spec, str):            # "blank", or an animal's key
            spec = {"role": spec} if spec in ROLES else {"role": "sample", "subject": spec}
        role = str(spec.get("role") or ("sample" if spec.get("subject") else "")).strip()
        if role not in ROLES:
            raise PlateError(gettext("%(well)s: a well is an animal's, a blank, a standard or empty.", well=well))
        if role == "sample":
            key = str(spec.get("subject") or "").strip()
            if not key or (subject_keys is not None and key not in subject_keys):
                raise PlateError(gettext("%(well)s: “%(subject)s” isn't in this experiment.", well=well, subject=key))
            out[well] = {"role": "sample", "subject": key}
        elif role == "standard":
            conc = number(spec.get("conc"))
            if conc is None or conc < 0:
                raise PlateError(gettext("%(well)s: say the standard's concentration.", well=well))
            out[well] = {"role": "standard", "conc": conc}
        else:
            out[well] = {"role": "blank"}
    return out


def number(raw) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if math.isfinite(raw) else None
    text = str(raw).strip().replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


# ---------------------------------------------------------------- reading an export

_CT_HEADERS = {"ct", "cq", "crt", "cp", "c t", "ct value", "cq value", "cт"}
_VALUE_HEADERS = {"value", "values", "od", "abs", "absorbance", "result", "results", "raw data", "raw", "signal",
                  "fluorescence", "rfu", "rlu", "luminescence", "reading", "count", "counts", "mean", "measurement"}
_WELL_HEADERS = {"well", "well position", "wellposition", "well pos", "position", "pos", "well id", "孔", "孔位"}
_TARGET_HEADERS = {"target", "target name", "detector", "detector name", "gene", "gene name", "assay", "target gene",
                   "基因", "靶基因"}


def _norm(text: str) -> str:
    return re.sub(r"[\s_\-.:()]+", " ", str(text or "").strip().lower()).strip()


def rows_of_text(text: str) -> list[list[str]]:
    """Rows of cells from text: tab-separated (from Excel, or a reader's
    .txt), semicolons (Excel where a comma is the decimal point) or CSV,
    whichever most of its lines use; a title line above doesn't decide."""
    if len(text or "") > MAX_TEXT:
        raise PlateError(gettext("That is too much text for one plate."))
    lines = [line for line in (text or "").splitlines()[:80] if line.strip()]
    def share(d):
        return sum(1 for line in lines if d in line) / max(1, len(lines))
    delimiter = "\t" if share("\t") >= 0.3 else ";" if share(";") >= 0.5 else ","
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text or ""), delimiter=delimiter)]


def read_file(filename: str, data: bytes) -> list[list[list[str]]]:
    """Each sheet's rows (one for a CSV or text file); a workbook's sheet
    named Results first (a QuantStudio export), then the others."""
    from .sheet_import import ImportProblem, _decode, read_workbook
    if not (filename or "").lower().endswith((".xlsx", ".xlsm", ".xls")):
        return [rows_of_text(_decode(data))]
    try:
        sheets = read_workbook(filename, data)
    except ImportProblem as e:
        raise PlateError(str(e)) from None
    return [rows for _name, rows in sorted(sheets.items(), key=lambda kv: 0 if _norm(kv[0]) == "results" else 1)]


def _grids(rows: list[list[str]]) -> list[tuple[str, dict[str, str], str]]:
    """Each grid in the rows: a header of column numbers 1, 2, 3… and below
    it rows starting with the row letters A, B, C…; named by the line above
    it when there is one ("Read 1:450"), with its last well (H12)."""
    found = []
    i = 0
    while i < len(rows):
        row = [c.strip() for c in rows[i]]
        start = next((j for j in range(len(row)) if row[j] == "1" and j + 2 < len(row)
                      and row[j + 1] == "2" and row[j + 2] == "3"), None)
        if start is None or start == 0:
            i += 1
            continue
        n = 0
        while start + n < len(row) and row[start + n] == str(n + 1):
            n += 1
        values: dict[str, str] = {}
        k = i + 1
        letter = 0
        while k < len(rows) and letter < len(LETTERS):
            cells = [c.strip() for c in rows[k]]
            if len(cells) <= start - 1 or cells[start - 1].upper() != LETTERS[letter]:
                break
            for c in range(n):
                cell = cells[start + c] if start + c < len(cells) else ""
                if cell:
                    values[f"{LETTERS[letter]}{c + 1}"] = cell
            letter += 1
            k += 1
        if letter:
            label = ""
            for back in (1, 2):
                if i - back >= 0:
                    above = [c.strip() for c in rows[i - back] if c.strip()]
                    if above and len(above) <= 3:
                        label = " ".join(above)[:60]
                        break
            label = label or (row[start - 1] if row[start - 1] not in ("", "<>") else "")
            found.append((label, values, f"{LETTERS[letter - 1]}{n}"))
            i = k
        else:
            i += 1
    return found


def _list(rows: list[list[str]]) -> dict | None:
    """A list with a header naming the well column, and a value column (a
    Ct or Cq for qPCR); a Target column splits it by gene."""
    for at, row in enumerate(rows[:60]):
        heads = [_norm(c) for c in row]
        well_cols = [j for j, h in enumerate(heads) if h in _WELL_HEADERS]
        if not well_cols:
            continue
        body = rows[at + 1:]

        def wellish(j):
            cells = [r[j] for r in body if j < len(r) and r[j].strip()]
            return sum(1 for c in cells if well_name(c)) >= max(1, len(cells) // 2)
        well_col = next((j for j in well_cols if wellish(j)), None)
        if well_col is None:
            continue
        target_col = next((j for j, h in enumerate(heads) if h in _TARGET_HEADERS), None)
        kind = "value"
        value_col = next((j for j, h in enumerate(heads) if h in _CT_HEADERS), None)
        if value_col is not None:
            kind = "ct"
        else:
            value_col = next((j for j, h in enumerate(heads) if h in _VALUE_HEADERS), None)
        if value_col is None:
            for j in range(len(heads)):
                if j in (well_col, target_col):
                    continue
                cells = [r[j] for r in body if j < len(r) and r[j].strip()]
                if cells and sum(1 for c in cells if number(c) is not None) >= max(1, len(cells) * 3 // 4):
                    value_col = j
                    break
        if value_col is None:
            continue
        values: dict[str, dict[str, str]] = {}
        order: list[str] = []
        for r in body:
            well = well_name(r[well_col]) if well_col < len(r) else None
            if well is None:
                continue
            target = (r[target_col].strip() if target_col is not None and target_col < len(r) else "")[:60]
            raw = r[value_col].strip() if value_col < len(r) else ""
            if target not in values:
                values[target] = {}
                order.append(target)
            if raw:
                values[target][well] = raw
        if any(values.values()):
            return {"shape": "list", "kind": kind, "targets": order, "values": values}
    return None


def _headerless(rows: list[list[str]]) -> dict | None:
    """Lines of "well  value" with no header."""
    values = {}
    for r in rows:
        cells = [c.strip() for c in r if c.strip()]
        if len(cells) >= 2 and well_name(cells[0]) and number(cells[-1]) is not None:
            values[well_name(cells[0])] = cells[-1]
    return {"shape": "list", "kind": "value", "targets": [""], "values": {"": values}} if len(values) >= 2 else None


def parse(rows: list[list[str]]) -> dict | None:
    """What an export holds: {"shape": "grid" | "list", "kind": "ct" |
    "value", "targets": [names in order], "values": {target: {well: text}}};
    None when the rows are none of the shapes. A grid's reads (one per
    wavelength or time) are its targets."""
    found = _list(rows)
    if found:
        return found
    grids = _grids(rows)
    if grids:
        names, values = [], {}
        for n, (label, cells, _corner) in enumerate(grids, 1):
            name = label if len(grids) > 1 or label else ""
            if len(grids) > 1 and (not name or name in values):
                name = f"{name} ({n})" if name else f"Read {n}"
            names.append(name)
            values[name] = cells
        return {"shape": "grid", "kind": "value", "targets": names, "values": values,
                "format": smallest_format([corner for _label, _cells, corner in grids])}
    return _headerless(rows)


def read_export(text: str = "", filename: str = "", data: bytes | None = None) -> dict:
    """The export pasted (`text`) or uploaded (`filename`, `data`), parsed;
    a PlateError when it holds no plate."""
    if data:
        sheets = read_file(filename, data)
    elif (text or "").strip():
        sheets = [rows_of_text(text)]
    else:
        raise PlateError(gettext("Paste the export, or choose its file."))
    for rows in sheets:
        found = parse(rows)
        if found:
            wells = {w for v in found["values"].values() for w in v}
            found.setdefault("format", smallest_format(wells) if wells else 96)
            return found
    raise PlateError(gettext("No plate found in it: BioManager reads a grid with row letters and column numbers, or a list with a Well column and a value or Ct column."))  # noqa: E501


# ---------------------------------------------------------------- what a readout takes from a plate

def mode_for(readout: dict) -> str:
    """How a plate becomes this readout: "ct" (ΔCt, ΔΔCt, fold change from
    each gene's Ct), "od" (absorbance less the blank), or "value" (one
    number for each animal, as typed); a PlateError for one a plate can't
    give."""
    formula = readout.get("formula")
    if formula in ("delta_ct", "delta_delta_ct", "fold_change"):
        return "ct"
    if formula == "blank_subtracted":
        return "od"
    label = i18n.translate_value(readout.get("label") or "")
    if formula:
        raise PlateError(gettext("%(readout)s is worked out from what is measured on the animal, not read off a plate: choose the readout the plate gives (Readout, at the top).",  # noqa: E501
                                 readout=label))
    if readout.get("key") == "body_weight" or readout.get("kind") == "fraction":
        raise PlateError(gettext("%(readout)s isn't read off a plate: under Readout, choose Another measure… for what the plate measures, or change the experiment's readout (Readout, at the top).",  # noqa: E501
                                 readout=label))
    return "value"


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _sd(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _read(raw: str, mode: str, ct_like: bool) -> tuple[float | None, list[str]]:
    """A well's value from its text, with what is wrong with it: "no_value"
    (Undetermined, a word) or "limit" (past what the instrument reads)."""
    text = str(raw or "").strip()
    low = text.lower()
    if low in NO_VALUE:
        return None, ["no_value"]
    if low.startswith(OVER) or text.startswith(">"):
        return None, ["limit"]
    value = number(text)
    if value is None:
        return None, ["no_value"]
    if ct_like and not 0 < value <= derived.CT_MAX:
        return value, ["limit"]
    if mode == "od" and not 0 <= value <= derived.OD_MAX:
        return value, ["limit"]
    return value, []



# Genes labs usually normalise qPCR to, written as exports name them.
REFERENCE_GENES = {"gapdh", "actb", "bactin", "b2m", "hprt", "hprt1", "18s", "rn18s", "18srrna", "rplp0", "36b4",
                   "ppia", "tbp", "ubc", "ywhaz", "gusb", "pgk1", "hmbs", "sdha", "eef1a1", "rpl13a", "tubb", "polr2a"}


def _is_reference_gene(name: str) -> bool:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum()) in REFERENCE_GENES


def analyse(layout: dict[str, dict], fmt: int, export: dict, readout: dict, subjects: list[dict],
            options: dict | None = None, group_values=None) -> dict:
    """The plate laid on the layout, changing nothing. `subjects` are the
    experiment's animals in order ({"key", "label", "group"}); `options`:
    target, reference (the reference gene, for a Ct readout),
    subtract_blank, standard_curve, skip (wells left out by hand).
    `group_values({key: inputs})` gives ΔΔCt or fold change for a readout
    against the control group (experiments.derive_day). Each animal gets
    its wells, their values, the mean and its value for the readout, or
    why it has none (`error`)."""
    options = options or {}
    mode = mode_for(readout)
    targets = list(export.get("targets") or [""])
    ct_like = mode == "ct" or export.get("kind") == "ct"
    skip = {w for w in (well_name(x) for x in options.get("skip") or []) if w}
    target = options.get("target") if options.get("target") in targets else None
    reference = options.get("reference") if options.get("reference") in targets else None
    if mode == "ct":
        if len(targets) < 2:
            raise PlateError(gettext("%(readout)s needs the Ct of a target gene and of a reference gene: this export has one value per well. Export the run's results with each well's target (a Target or Detector column).",  # noqa: E501
                                     readout=i18n.translate_value(readout.get("label") or "")))
        if target is None or reference is None:
            # A housekeeping gene on the run is the reference unless chosen.
            usual = next((t for t in targets if t != target and _is_reference_gene(t)), None)
            reference = reference or (usual if usual is not None else None)
            target = target or next(t for t in targets if t != reference)
            reference = reference or next(t for t in targets if t != target)
        if target == reference:
            raise PlateError(gettext("The target gene and the reference gene are the same: choose two."))
    else:
        target = target if target is not None else targets[0]
    used = [target] + ([reference] if mode == "ct" else [])

    by_key = {s["key"]: s for s in subjects}
    wells_out: dict[str, dict] = {}
    for name in set(layout) | {w for t in used for w in (export.get("values") or {}).get(t, {})}:
        if not fits(name, fmt):
            continue
        spec = layout.get(name) or {}
        entry = {"role": spec.get("role", ""), "reads": {}, "skipped": name in skip}
        if spec.get("subject"):
            entry["subject"] = spec["subject"]
        if spec.get("role") == "standard":
            entry["conc"] = spec["conc"]
        for t in used:
            raw = (export.get("values") or {}).get(t, {}).get(name)
            if raw is None:
                continue
            value, flags = _read(raw, mode, ct_like)
            entry["reads"][t] = {"text": raw, "value": value, "flags": flags}
        wells_out[name] = entry
    off_plate = sorted({w for t in used for w in (export.get("values") or {}).get(t, {}) if not fits(w, fmt)},
                       key=position)

    def usable(well: str, t: str) -> float | None:
        e = wells_out.get(well)
        if e is None or e["skipped"]:
            return None
        r = e["reads"].get(t)
        if r is None or r["flags"]:
            return None
        return r["value"]

    warnings: list[str] = []
    if off_plate:
        warnings.append(gettext("%(wells)s are off a %(n)s-well plate and were left out.",
                                wells=", ".join(off_plate[:8]), n=fmt))
    gone = sorted({w for w, s in layout.items() if s.get("role") == "sample" and s["subject"] not in by_key},
                  key=position)
    if gone:
        warnings.append(gettext("%(wells)s: the animal is no longer in the experiment, so they were left out.",
                                wells=", ".join(gone[:8])))

    # The blank: its wells' mean (a qPCR run's no-template controls are only looked at).
    blank_wells = [w for w, s in layout.items() if s.get("role") == "blank"]
    blank_values = [v for v in (usable(w, target) for w in blank_wells) if v is not None]
    blank = derived.tidy(_mean(blank_values)) if blank_values and mode != "ct" else None
    subtract = mode == "od" or (mode == "value" and blank is not None and bool(options.get("subtract_blank", True)))
    if mode == "od" and blank is None:
        raise PlateError(gettext("Mark the blank wells: %(readout)s is each well's OD less the blank's.",
                                 readout=i18n.translate_value(readout.get("label") or "")))

    # The standards, and a curve through them (app/standard_curves.py).
    standards, fit = [], None
    if mode == "value":
        by_conc: dict[float, list[float]] = {}
        for w, s in layout.items():
            if s.get("role") == "standard":
                v = usable(w, target)
                if v is not None:
                    by_conc.setdefault(s["conc"], []).append(v - (blank if subtract else 0))
        standards = [{"conc": c, "mean": derived.tidy(_mean(vs)), "n": len(vs)} for c, vs in sorted(by_conc.items())]
        if options.get("standard_curve"):
            if not standards:
                raise PlateError(gettext("Mark the standard wells, each with its concentration, for a standard curve."))
            curve = options.get("curve") if options.get("curve") in standard_curves.MODELS else None
            try:
                fit = standard_curves.fit([(c, v) for c, vs in by_conc.items() for v in vs], curve)
            except standard_curves.CurveError as e:
                raise PlateError(str(e)) from None

    out_subjects = []
    inputs_by: dict[str, dict] = {}
    for s in subjects:
        wells = sorted((w for w, spec in layout.items() if spec.get("subject") == s["key"] and fits(w, fmt)),
                       key=position)
        if not wells:
            continue
        row = {"key": s["key"], "label": s.get("label", s["key"]), "group": s.get("group", ""), "wells": wells}
        means = {}
        for t in used:
            values = [(w, usable(w, t)) for w in wells]
            values = [(w, v) for w, v in values if v is not None]
            # Replicates far from the others: flagged, kept unless left out by hand.
            if len(values) >= 2:
                mid = _median([v for _, v in values])
                tolerance = CT_TOLERANCE if ct_like else abs(mid) * VALUE_TOLERANCE
                for w, v in values:
                    if tolerance and abs(v - mid) > tolerance:
                        wells_out[w]["reads"][t]["flags"] = ["outlier"]
            if values:
                xs = [v for _, v in values]
                sd = _sd(xs)
                means[t] = _mean(xs)
                row.setdefault("reads", {})[t] = {"n": len(xs), "mean": derived.tidy(means[t]),
                                                  "sd": derived.tidy(sd) if sd is not None else None,
                                                  "cv": round(sd / means[t] * 100, 1) if sd is not None and means[t] else None}
        missing = [t for t in used if t not in means]
        if missing:
            texts = [wells_out[w]["reads"][missing[0]]["text"] for w in wells
                     if w in wells_out and missing[0] in wells_out[w]["reads"]]
            if texts and all(str(x).strip().lower() in NO_VALUE for x in texts):
                row["error"] = gettext("%(target)s never came up in its wells (%(value)s).",
                                       target=missing[0] or gettext("The plate"), value=texts[0])
            else:
                row["error"] = gettext("No value to use in its wells%(target)s.",
                                       target=f" ({missing[0]})" if missing[0] else "")
            out_subjects.append(row)
            continue
        try:
            if mode == "ct":
                inputs = derived.check(readout["formula"], {"ct_target": means[target], "ct_reference": means[reference]})
                row["inputs"] = {k: derived.tidy(v) for k, v in inputs.items()}
                if readout.get("per_group"):
                    inputs_by[s["key"]] = row["inputs"]
                else:
                    row["value"] = derived.compute(readout["formula"], row["inputs"])
            elif mode == "od":
                row["inputs"] = derived.check("blank_subtracted", {"od": derived.tidy(means[target]), "blank": blank})
                row["value"] = derived.compute("blank_subtracted", row["inputs"])
            else:
                signal = value = means[target] - (blank if subtract else 0)
                if fit is not None:
                    row["signal"] = derived.tidy(signal)
                    lo, hi = standards[0]["mean"], standards[-1]["mean"]
                    if not min(lo, hi) <= signal <= max(lo, hi):
                        row["outside"] = True
                    value = standard_curves.concentration(fit, signal)
                    if value is None:
                        row["error"] = (gettext("Its signal, %(value)s, is below where the standard curve starts: less than the assay detects.",  # noqa: E501
                                                value=f"{signal:g}") if standard_curves.below(fit, signal) else
                                        gettext("Its signal, %(value)s, is past where the standard curve levels off, so no concentration can be read off it: dilute the sample and read it again.",  # noqa: E501
                                                value=f"{signal:g}"))
                        out_subjects.append(row)
                        continue
                row["value"] = derived.tidy(value)
                if row["value"] < 0:
                    row["error"] = (gettext("It reads as %(value)s on the standard curve, below zero: a readout can't be below zero.",
                                            value=f"{row['value']:g}") if fit is not None else
                                    gettext("%(value)s is below zero once the blank is taken off: a readout can't be below zero.",
                                            value=f"{row['value']:g}"))
                    del row["value"]
        except derived.DerivedError as e:
            row["error"] = str(e)
        out_subjects.append(row)
    if inputs_by:
        try:
            worked = group_values(inputs_by) if group_values else {}
        except derived.DerivedError as e:
            raise PlateError(str(e)) from None
        for row in out_subjects:
            if row["key"] in worked:
                row["value"] = worked[row["key"]]

    curve = None
    if fit is not None:
        # The standards and the curve through them, and each animal on it, to draw.
        curve = {**standard_curves.drawn(fit, [s["conc"] for s in standards]),
                 "standards": [[c, derived.tidy(v)] for c, vs in sorted(by_conc.items()) for v in vs],
                 "samples": [[r["value"], r["signal"]] for r in out_subjects if "value" in r and "signal" in r]}
        fit = standard_curves.public(fit)
    flagged = {flag: sorted((w for w, e in wells_out.items() if any(flag in r["flags"] for r in e["reads"].values())),
                            key=position) for flag in ("outlier", "limit", "no_value")}
    return {"format": fmt, "mode": mode, "kind": export.get("kind", "value"), "shape": export.get("shape", ""),
            "targets": targets, "target": target, "reference": reference if mode == "ct" else None,
            "blank": {"mean": blank, "n": len(blank_values), "subtracted": bool(subtract and blank is not None)},
            "standards": standards, "fit": fit, "curve": curve, "wells": wells_out, "subjects": out_subjects,
            "flagged": flagged, "skipped": sorted(skip, key=position), "warnings": warnings,
            "ready": sum(1 for r in out_subjects if "value" in r)}


# ---------------------------------------------------------------- layouts kept with an experiment

def layout_dict(row) -> dict:
    try:
        wells = json.loads(row.wells or "{}")
    except ValueError:
        wells = {}
    return {"id": row.id, "name": row.name, "format": row.format, "wells": wells,
            "used_at": row.used_at.isoformat(timespec="seconds") if row.used_at else None,
            "created_by": row.created_by}


def layouts(session, exp) -> list[dict]:
    """The experiment's layouts, the last read onto first (Same as last plate)."""
    from .models import PlateLayout
    rows = session.scalars(select(PlateLayout).where(PlateLayout.experiment_id_fk == exp.id)).all()
    rows = sorted(rows, key=lambda r: (r.used_at or r.updated_at or r.created_at or datetime.min), reverse=True)
    return [layout_dict(r) for r in rows]


def find_layout(session, exp, ref):
    """A layout of this experiment by its id or name; None when there is none."""
    from .models import PlateLayout
    rows = session.scalars(select(PlateLayout).where(PlateLayout.experiment_id_fk == exp.id)).all()
    text = str(ref if ref is not None else "").strip()
    return next((r for r in rows if str(r.id) == text), None) or next(
        (r for r in rows if r.name.strip().lower() == text.lower()), None)


def reads(session, exp, place=None, limit: int = 20) -> list[dict]:
    """The plates read onto the experiment, the newest first, each with the
    readout or measure it filled."""
    from . import experiments as xp
    from .models import PlateRead
    rows = session.scalars(select(PlateRead).where(PlateRead.experiment_id_fk == exp.id)
                           .order_by(PlateRead.id.desc()).limit(limit)).all()
    names = {m["key"]: m["label"] for m in xp.measures_of(exp)}
    if place is not None:
        main = xp.readout_of(exp, place)
        names[main["key"]] = i18n.translate_value(main["label"])
    out = []
    for r in rows:
        try:
            results = json.loads(r.results or "{}")
        except ValueError:
            results = {}
        out.append({"id": r.id, "layout": r.layout_name, "read_on": r.read_on.isoformat(), "readout_key": r.readout_key,
                    "readout": names.get(r.readout_key, ""), "count": len(results), "file_name": r.file_name, "by": r.created_by,
                    "file_url": f"/static/{r.file_path}" if r.file_path else ""})
    return out


def _subject_rows(session, exp, place) -> list[dict]:
    from . import experiments as xp
    return [{"key": x.key, "label": x.label, "group": x.group} for x in xp.subjects(session, exp, place)]


def measures(exp, place) -> dict:
    """What a plate can fill: the main readout (`plate`: whether a plate
    gives it) and the experiment's other measures."""
    from . import experiments as xp
    main = xp.readout_of(exp, place)
    try:
        mode_for(main)
        fits_plate = True
    except PlateError:
        fits_plate = False
    return {"main": {"key": main["key"], "label": main["label"], "unit": main.get("unit", ""), "plate": fits_plate},
            "others": [{"key": m["key"], "label": m["label"], "unit": m["unit"],
                        **({"formula": m["formula"], "control": m.get("control", "")} if m.get("formula") else {})}
                       for m in xp.measures_of(exp)]}


def readout_from(exp, place, form, create: bool = False) -> dict:
    """The readout a plate fills, from the dialog's choice (`measure`: blank
    or "main" for the main readout, another measure's key or name, or "new"
    with `measure_label` and `measure_unit`, and `measure_kind` for one
    worked out from two Cts, delta_ct, delta_delta_ct or fold_change, with
    `measure_control` for the last two). A new measure is kept with
    the experiment (its JSON; the caller commits or rolls back). A
    PlateError, in words, for a measure it doesn't have."""
    from . import experiments as xp
    chosen = str(form.get("measure") or "").strip()
    if chosen == "new":
        if not create and not str(form.get("measure_label") or "").strip():
            raise PlateError(gettext("Name the measure, e.g. IL-6 ELISA."))
        try:
            return xp.add_measure(exp, place, form.get("measure_label"), form.get("measure_unit") or "",
                                  form.get("measure_kind") or "", form.get("measure_control") or "")
        except ValueError as e:
            raise PlateError(str(e)) from None
    readout = xp.readout_for(exp, place, chosen)
    if readout is None:
        names = ", ".join(m["label"] for m in xp.measures_of(exp)) or gettext("none yet")
        raise PlateError(gettext("Experiment %(name)s has no measure “%(measure)s”; its other measures: %(names)s.",
                                 name=exp.name, measure=chosen, names=names))
    return readout


def work_out(session, exp, place, layout_row, export: dict, options: dict, on: date,
             readout: dict | None = None) -> dict:
    """analyse() for this experiment's layout and readout (or another
    measure), a readout against the control group worked out with the
    day's other readings."""
    from . import experiments as xp
    readout = readout or xp.readout_of(exp, place)
    subs = xp.subjects(session, exp, place)
    rows = [{"key": x.key, "label": x.label, "group": x.group} for x in subs]
    wells = clean_layout(layout_row.wells, layout_row.format)
    result = analyse(wells, layout_row.format, export, readout, rows, options,
                     group_values=lambda given: xp.derive_day(session, exp, place, on, given, subs, readout))
    keys = [r["key"] for r in result["subjects"] if "value" in r]
    from .models import ExperimentReading
    result["replaces"] = session.scalar(select(func.count(ExperimentReading.id)).where(
        ExperimentReading.experiment_id_fk == exp.id, ExperimentReading.readout_key == readout["key"],
        ExperimentReading.read_on == on, ExperimentReading.subject.in_(keys or [""]))) or 0
    result["readout"] = {"key": readout["key"], "label": readout["label"], "unit": readout.get("unit", ""),
                         "formula": readout.get("formula"), "per_group": bool(readout.get("per_group")),
                         "control": readout.get("control", ""), "measure": bool(readout.get("measure"))}
    result["layout"] = {"id": layout_row.id, "name": layout_row.name}
    result["on"] = on.isoformat()
    return result


def save(session, exp, place, layout_row, export: dict, options: dict, on: date, file_path: str = "",
         file_name: str = "", readout: dict | None = None) -> tuple[int, list[str], dict]:
    """Each animal's value written as the readout (or the measure) for the
    day, as the sheet writes it, and the plate kept as their source.
    (saved, problems, the analysis); the caller commits, or rolls back when
    nothing was saved."""
    from . import experiments as xp
    from .models import PlateRead
    readout = readout or xp.readout_of(exp, place)
    result = work_out(session, exp, place, layout_row, export, options, on, readout)
    by_key = {x.key: x for x in xp.subjects(session, exp, place)}
    given = [(r["key"], r) for r in result["subjects"] if "value" in r and r["key"] in by_key]
    saved, problems, results = 0, [], {}
    for key, row in xp.control_first(readout, given, lambda item: by_key.get(item[0])):
        raw = row["inputs"] if readout.get("formula") else repr(row["value"])
        problem = xp.set_reading(session, exp, place, by_key[key], on, raw, readout)
        if problem:
            problems.append(str(problem))
        else:
            saved += 1
            results[key] = row["value"]
    if saved:
        values = {t: (export.get("values") or {}).get(t, {}) for t in export.get("targets") or [""]}
        session.add(PlateRead(
            experiment_id_fk=exp.id, layout_id_fk=layout_row.id, layout_name=layout_row.name[:120],
            format=layout_row.format, wells=layout_row.wells, readout_key=readout["key"], read_on=on,
            options=json.dumps({"target": result["target"], "reference": result["reference"],
                                "subtract_blank": result["blank"]["subtracted"], "blank": result["blank"]["mean"],
                                "measure": readout["label"] if readout.get("measure") else "",
                                "standard_curve": result["fit"], "skip": result["skipped"]}, ensure_ascii=False),
            data=json.dumps(values, ensure_ascii=False), results=json.dumps(results, ensure_ascii=False),
            file_path=file_path[:500], file_name=file_name[:255], created_by=g.user.username))
    return saved, problems, result


# ---------------------------------------------------------------- the page's routes

def options_from(form) -> dict:
    """The choices made in the dialog (or sent by the API)."""
    def flag(name, default):
        raw = form.get(name)
        if raw is None or raw == "":
            return default
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    skip = form.get("skip") or []
    if isinstance(skip, str):
        skip = [x for x in re.split(r"[\s,;]+", skip) if x]
    curve = str(form.get("curve") or "").strip().lower().replace("–", "").replace("-", "")
    return {"target": form.get("target") or None, "reference": form.get("reference") or None,
            "subtract_blank": flag("subtract_blank", True), "standard_curve": flag("standard_curve", False),
            "curve": curve if curve in standard_curves.MODELS else None,
            "skip": skip if isinstance(skip, list) else []}


def day_of(raw) -> date:
    try:
        on = date.fromisoformat(str(raw or date.today().isoformat())[:10])
    except ValueError:
        raise PlateError(gettext("Say which day it is for.")) from None
    if on > date.today():
        raise PlateError(gettext("A readout can't be for a day still to come."))
    return on


@bp.before_request
def _signed_in():
    if g.get("user") is None:
        return redirect(url_for("login", next=request.path))
    return None


def _load(session, experiment_id: int, edit: bool = False):
    from . import experiments as xp
    from .models import Experiment
    exp = session.get(Experiment, experiment_id)
    place = xp.place_for(session, exp.db or "colony") if exp is not None else None
    if exp is None or place is None:
        abort(404)
    if edit and not access.can_edit_experiment(exp):
        abort(make_response(jsonify({"ok": False, "error": access.denied_message("experiment", exp.owner_username)}), 403))
    return exp, place


def _refused(error: Exception, status: int = 400):
    return jsonify({"ok": False, "error": str(error)}), status


@bp.get("/<int:experiment_id>/plates.json")
def listing(experiment_id: int):
    """The experiment's layouts (the last read onto first) and its plates read."""
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        return jsonify({"ok": True, "layouts": layouts(s, exp), "reads": reads(s, exp, place),
                        "formats": sorted(FORMATS), "editable": access.can_edit_experiment(exp),
                        **measures(exp, place)})


@bp.post("/<int:experiment_id>/plates/layouts")
def save_layout(experiment_id: int):
    """{id (to change one), name, format, wells}: a layout kept with the experiment."""
    from .models import PlateLayout
    data = request.get_json(silent=True) or {}
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id, edit=True)
        try:
            fmt = check_format(data.get("format"))
            keys = {r["key"] for r in _subject_rows(s, exp, place)}
            wells = clean_layout(data.get("wells") or {}, fmt, keys)
        except PlateError as e:
            return _refused(e)
        name = str(data.get("name") or "").strip()[:120]
        if not name:
            return _refused(PlateError(gettext("Give the layout a name.")))
        row = find_layout(s, exp, data.get("id")) if data.get("id") else None
        clash = find_layout(s, exp, name)
        if clash is not None and clash is not row:
            return _refused(PlateError(gettext("This experiment already has a layout called “%(name)s”.", name=name)), 409)
        if row is None:
            row = PlateLayout(experiment_id_fk=exp.id, created_by=g.user.username, created_at=datetime.utcnow())
            s.add(row)
        row.name, row.format, row.wells = name, fmt, json.dumps(wells, ensure_ascii=False)
        row.updated_at = datetime.utcnow()
        s.commit()
        return jsonify({"ok": True, "id": row.id, "layouts": layouts(s, exp)})


@bp.post("/<int:experiment_id>/plates/layouts/<int:layout_id>/delete")
def delete_layout(experiment_id: int, layout_id: int):
    """A layout taken away; the plates read onto it keep their copy of it."""
    from .models import PlateRead
    with SessionLocal() as s:
        exp, _place = _load(s, experiment_id, edit=True)
        row = find_layout(s, exp, layout_id)
        if row is None:
            abort(404)
        for r in s.scalars(select(PlateRead).where(PlateRead.layout_id_fk == row.id)):
            r.layout_id_fk = None
        s.delete(row)
        s.commit()
        return jsonify({"ok": True, "layouts": layouts(s, exp)})


def _export_from_request():
    """The export in the request (a file, or pasted text): (parsed, the file or None)."""
    upload = request.files.get("file")
    if upload is not None and upload.filename:
        from .sheet_import import MAX_BYTES
        data = upload.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise PlateError(gettext("That file is over 15 MB. Save only the sheet you need, or as CSV."))
        upload.stream.seek(0)
        return read_export(filename=upload.filename, data=data), upload
    return read_export(text=request.form.get("text") or ""), None


def _layout_or_refuse(session, exp):
    row = find_layout(session, exp, request.form.get("layout_id"))
    if row is None:
        raise PlateError(gettext("Choose a layout, or save this one first."))
    return row


@bp.post("/<int:experiment_id>/plates/preview")
def preview(experiment_id: int):
    """The export laid on a layout, changing nothing (multipart: layout_id,
    file or text, on, target, reference, subtract_blank, standard_curve, skip)."""
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id, edit=True)
        try:
            row = _layout_or_refuse(s, exp)
            export, _upload = _export_from_request()
            readout = readout_from(exp, place, request.form)       # a new measure only tried: rolled back below
            result = work_out(s, exp, place, row, export, options_from(request.form), day_of(request.form.get("on")),
                              readout)
        except PlateError as e:
            return _refused(e)
        s.rollback()
        return jsonify({"ok": True, **result})


@bp.post("/<int:experiment_id>/plates/save")
def save_plate(experiment_id: int):
    """As preview, then each animal's value saved as the readout (or the
    measure chosen) for the day, the plate kept with its file as their
    source: one batch, which Batch history's Undo takes back whole."""
    from . import audit, uploads
    from . import experiments as xp
    from .models import PlateLayout
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id, edit=True)
        try:
            row = _layout_or_refuse(s, exp)
            export, upload = _export_from_request()
            on = day_of(request.form.get("on"))
            options = options_from(request.form)
            # A new measure is kept outside the batch: undoing the plate
            # leaves it, so readings another plate gave it never go unseen.
            readout = readout_from(exp, place, request.form, create=True)
            checked = work_out(s, exp, place, row, export, options, on, readout)
        except PlateError as e:
            s.rollback()
            return _refused(e)
        if not checked["ready"]:            # refused before a file is kept
            s.rollback()
            problems = [f"{r['label']}: {r['error']}" for r in checked["subjects"] if r.get("error")]
            return jsonify({"ok": False, "error": " ".join(problems[:3]) or gettext("No animal has a value to save."),
                            "problems": problems}), 400
        file_path = file_name = ""
        if upload is not None:
            stored = uploads.save_uploaded_file(upload)
            if stored:
                file_path, file_name = stored["path"], upload.filename or ""
        s.flush()
        try:
            with audit.batch(s, "create", f"Plate “{row.name}” read as {readout['label']}, {on.isoformat()}",
                             "plate_reads"):
                saved, problems, result = save(s, exp, place, row, export, options, on, file_path, file_name, readout)
        except PlateError as e:
            s.rollback()
            return _refused(e)
        if not saved:
            s.rollback()
            return jsonify({"ok": False, "error": " ".join(problems[:3]) or gettext("No animal has a value to save."),
                            "problems": problems}), 400
        # Same as last plate: when it was last read onto is bookkeeping, not
        # part of the batch (a later plate on it would stop this one's Undo).
        s.execute(PlateLayout.__table__.update().where(PlateLayout.__table__.c.id == row.id)
                  .values(used_at=datetime.utcnow()))
        s.commit()
        s.refresh(exp)
        return jsonify({"ok": True, "saved": saved, "problems": problems, "filled": readout["key"],
                        "message": gettext("Saved %(n)s readings of %(readout)s for %(day)s from the plate.", n=saved,
                                           readout=i18n.translate_value(readout["label"]),
                                           day=i18n.strftime(on, "%b %d, %Y")),
                        **xp.payload(s, exp, place)})


# ---------------------------------------------------------------- for an assistant (POST /api/v1/experiments/<id>/plate)

def for_assistant(session, exp, place, data: dict, export: dict | None = None) -> dict:
    """An export (`text`, or one read from a capture's file) laid on one of the experiment's
    layouts (`layout`, its id or name) or on `wells` given with `format`,
    changing nothing: the analysis, and the experiment_reading change to
    propose for it. `measure` names another measure of the experiment to
    fill instead of its main readout."""
    from . import experiments as xp
    if data.get("layout") not in (None, ""):
        row = find_layout(session, exp, data.get("layout"))
        if row is None:
            names = ", ".join(f"{x['name']} ({x['id']})" for x in layouts(session, exp)) or gettext("none yet")
            raise PlateError(gettext("Experiment %(name)s has no layout “%(layout)s”; it has %(names)s.",
                                     name=exp.name, layout=data.get("layout"), names=names))
    elif data.get("wells"):
        from .models import PlateLayout
        fmt = check_format(data.get("format") or 96)
        keys = {r["key"] for r in _subject_rows(session, exp, place)}
        row = PlateLayout(id=0, experiment_id_fk=exp.id, name="", format=fmt,
                          wells=json.dumps(clean_layout(data["wells"], fmt, keys)))
    else:
        names = ", ".join(f"{x['name']} ({x['id']})" for x in layouts(session, exp))
        raise PlateError(gettext("Say which layout (layout: its id or name; this experiment has %(names)s), or give wells and format.",
                                 names=names or gettext("none yet")))
    if export is None:
        export = read_export(text=str(data.get("text") or ""))
    on = day_of(data.get("date"))
    if str(data.get("measure") or "").strip() == "new":
        raise PlateError(gettext("A new measure is made in the experiment page's Plate layout dialog; name one the experiment has, or leave measure out for its main readout."))  # noqa: E501
    readout = readout_from(exp, place, data)
    result = work_out(session, exp, place, row, export, options_from(data), on, readout)
    ready = [r for r in result["subjects"] if "value" in r]
    fields: dict = {"date": on.isoformat()}
    if readout.get("measure"):
        fields["measure"] = readout["label"]
    if readout.get("formula"):
        fields["inputs"] = {r["key"]: r["inputs"] for r in ready}
    else:
        fields["values"] = {r["key"]: r["value"] for r in ready}
    result["change"] = {"action": "experiment_reading", "target": {"kind": "experiment", "id": exp.id},
                        "fields": fields} if ready else None
    result["changes_nothing"] = True
    return result
