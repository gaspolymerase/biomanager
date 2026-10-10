#!/usr/bin/env python3
"""How well Import from Excel copes with the sheets labs really keep.

    .venv/bin/python benchmarks/import_sheets.py                    # 12 sheets per mess, seed 1
    .venv/bin/python benchmarks/import_sheets.py --per-mess 30 --seed 7
    .venv/bin/python benchmarks/import_sheets.py --out /tmp/sheets  # keep every sheet and its answers

Each sheet is made from records whose true values are known, then spoilt in
one of the ways a lab's sheet is spoilt (a "mess": DOB instead of Date of
birth, a title line above the header, 14.03.26, ♂/♀, a cage number written
once and merged down the cage's mice, a CSV saved in Windows' encoding…), or
in several at once (the "kitchen sink"), or not at all (the control). It is
uploaded to a throwaway lab exactly as a person would, through the app's own
pages, and imported with every column matched as the match page suggests:
what someone gets who clicks Import without changing anything.

Then every value that went in is compared with the truth, and counted as
  correct    stored as it should be;
  kept       not stored as it should be, but the sheet's text is in the
             record's notes, or the preview warned about that row or column:
             nothing is lost, and the person was told;
  blank      left empty, silently;
  wrong      a different value, silently (the worst).
A row is imported, or refused (the preview says why), or lost (neither).
A column the database doesn't have counts as kept when its value is in the
record's notes.

It runs on its own throwaway database (as the tests do; tests/base.py), so
the lab's data is never opened. The same seed makes the same sheets, so a
result can be checked by running it again. Results go to
benchmarks/results/import-<version>.json and .md.
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import json
import os
import random
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BIOMANAGER_TELEMETRY", "0")

# tests.base first: it points the app at a throwaway database before app is imported.
from tests.base import client_for, make_user  # noqa: E402
from app import sheet_import as si  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import MouseRecord, PlasmidRecord, UserAccount  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

RESULTS = ROOT / "benchmarks" / "results"
TODAY = date(2026, 10, 1)          # dates are made before this, so none is in the future

# The lab the sheets come from: the person importing, and four members a sheet
# may name by user name, full name or first name.
IMPORTER = "bench"
MEMBERS = [("akim", "Alice Kim"), ("bchen", "Bo Chen"), ("cdiaz", "Carla Díaz"), ("dpatel", "Dev Patel")]


# ---------------------------------------------------------------- the truth

@dataclass
class Column:
    key: str            # the truth's field
    header: str         # as a clean sheet names it
    synonyms: tuple     # as other labs name it
    chinese: str = ""


@dataclass
class Sheet:
    target: str                       # "mice" or "plasmids"
    mess: str
    messes: list[str]
    columns: list[Column]
    headers: list[str]
    records: list[dict]               # the truth, one per data row
    cells: list[list]                 # what the sheet shows, one row per record
    extras: list[tuple[str, list]] = field(default_factory=list)   # (header, values) the database lacks
    title_rows: list[list] = field(default_factory=list)
    blank_after: set[int] = field(default_factory=set)
    total_row: bool = False
    merge_key: str = ""               # merge this column's runs of one value (xlsx)
    filldown_key: str = ""            # write this column's value once per run, blank below
    blanked: set = field(default_factory=set)            # (record, key) left blank by filling down
    fmt: str = "xlsx"                 # xlsx | csv | csv-semicolon | tsv | csv-cp1252 | csv-bom
    row_numbers: list[int] = field(default_factory=list)   # each record's row in the sheet


MOUSE_COLUMNS = [
    Column("mouse_id", "Mouse ID", ("ID", "Animal ID", "Mouse #", "Mouse no."), "小鼠编号"),
    Column("ear_tag", "Ear tag", ("Tag", "Ear punch", "Notch", "Eartag"), "耳标"),
    Column("sex", "Sex", ("Gender", "M/F", "sex"), "性别"),
    Column("genotype", "Genotype", ("Strain", "Line", "Allele", "GT"), "基因型"),
    Column("dob", "Date of birth", ("DOB", "Birth date", "Born", "D.O.B."), "出生日期"),
    Column("cage", "Cage", ("Cage #", "Cage No.", "Cage number", "Cage card"), "笼号"),
    Column("status", "Status", ("Use", "Purpose", "State"), "状态"),
    Column("owner", "Owner", ("User", "Researcher", "Who", "Responsible"), "负责人"),
    Column("note", "Notes", ("Comments", "Remarks", "Comment"), "备注"),
]
PLASMID_COLUMNS = [
    Column("plasmid_id", "Plasmid number", ("Plasmid #", "ID", "Stock number", "pl #"), "质粒编号"),
    Column("name", "Name", ("Plasmid name", "Plasmid", "Construct"), "质粒名称"),
    Column("backbone", "Backbone", ("Vector", "Parent vector", "Parent"), "载体"),
    Column("insert", "Insert", ("Gene", "Insert gene", "ORF"), "插入片段"),
    Column("resistance", "Resistance", ("Antibiotic", "Selection", "Marker"), "抗性"),
    Column("box", "Box", ("Freezer box", "Storage box", "Plasmid box"), "盒子"),
    Column("position", "Position in the box", ("Position", "Well", "Slot", "Pos"), "位置"),
    Column("location", "Location", ("Freezer", "Storage", "Fridge"), "存放位置"),
    Column("concentration", "Concentration (ng/µL)", ("Conc", "Conc.", "ng/ul", "DNA concentration"), "浓度"),
    Column("a260_280", "260/280", ("A260/280", "Purity"), "260/280"),
    Column("owner", "Owner", ("Made by", "Maker", "Depositor", "User"), "负责人"),
    Column("note", "Notes", ("Comments", "Source", "Remarks"), "备注"),
]

GENOTYPES = ["Ai14/+", "Pvalb-Cre/+", "WT", "fl/fl", "Sst-Cre; Ai14", "Emx1-Cre/+; fl/+", "Cre/+", "+/-"]
STATUSES = ["breeder", "experiment", "geno", "sac"]
STATUS_WORDS = {"breeder": ["Breeding", "breeder", "Breed"], "experiment": ["exp", "Experimental", "Experiment"],
                "geno": ["to genotype", "Genotyping", "geno"], "sac": ["sacrificed", "culled", "Euthanized"]}
EAR_TAGS = ["R1", "L1", "R2", "L2", "RL", "RR", "LL", "N", "R1L1", "L2R1"]
NOTES = ["", "", "", "founder", "check genotype", "from JAX", "small litter", "retired breeder", "café stock"]
BACKBONES = ["pcDNA3.1", "pAAV-hSyn", "pLenti-CMV", "pUC19", "pET-28a", "pCAG", "pX330"]
INSERTS = ["EGFP", "mCherry", "GCaMP6s", "ChR2-EYFP", "Cas9", "tdTomato", "jRGECO1a", "Arch-GFP"]
RESISTANCE = ["Amp", "Kan", "AmpR", "Ampicillin", "Kanamycin", "Spec", "Chlor"]
LOCATIONS = ["-80 freezer 2", "-20 door", "Cold room shelf 3", "-80 rack B"]


def mouse_records(rng: random.Random, sheet_no: int, n: int) -> list[dict]:
    records, cage_no = [], 0
    while len(records) < n:
        cage_no += 1
        cage = f"{sheet_no}{cage_no:02d}"
        for _ in range(min(rng.randint(1, 5), n - len(records))):
            i = len(records)
            records.append({
                "mouse_id": sheet_no * 1000 + i + 1,
                "ear_tag": rng.choice(EAR_TAGS),
                "sex": rng.choice("MF"),
                "genotype": rng.choice(GENOTYPES),
                "dob": TODAY - timedelta(days=rng.randint(21, 700)),
                "cage": cage,
                "status": rng.choice(STATUSES),
                "owner": rng.choice(MEMBERS)[0],
                "note": rng.choice(NOTES),
            })
    return records


def plasmid_records(rng: random.Random, sheet_no: int, n: int) -> list[dict]:
    records, box = [], 0
    for i in range(n):
        if i % 20 == 0:
            box += 1
        backbone, insert = rng.choice(BACKBONES), rng.choice(INSERTS)
        records.append({
            "plasmid_id": sheet_no * 1000 + i + 1,
            "name": f"{backbone}-{insert}-{sheet_no}.{i + 1}",
            "backbone": backbone, "insert": insert,
            "resistance": rng.choice(RESISTANCE),
            "box": f"Box {sheet_no}-{box}",
            "position": (i % 20 // 9, i % 20 % 9),           # (row, column), from 0: A1, A2…
            "location": rng.choice(LOCATIONS),
            "concentration": round(rng.uniform(80, 2500), 1),
            "a260_280": round(rng.uniform(1.7, 2.0), 2),
            "owner": rng.choice(MEMBERS)[0],
            "note": rng.choice(NOTES),
        })
    return records


# ---------------------------------------------------------------- writing a value

def iso(d: date) -> str:
    return d.isoformat()


DATE_FORMATS = {
    "iso": iso,
    "us": lambda d: f"{d.month}/{d.day}/{d.year}",
    "eu": lambda d: f"{d.day:02d}/{d.month:02d}/{d.year}",
    "eu-dots": lambda d: f"{d.day:02d}.{d.month:02d}.{d.year % 100:02d}",
    "named": lambda d: d.strftime("%d-%b-%y"),
    "serial": lambda d: str((d - date(1899, 12, 30)).days),
}


def position_text(rc: tuple[int, int], style: str = "A1") -> str:
    letter, number = "ABCDEFGHI"[rc[0]], rc[1] + 1
    return {"A1": f"{letter}{number}", "a1": f"{letter.lower()}{number}", "A-1": f"{letter}-{number}",
            "A 1": f"{letter} {number}"}[style]


def owner_text(rng: random.Random, username: str, style: str) -> str:
    full = dict(MEMBERS)[username]
    return {"username": username, "display": full, "first": full.split()[0],
            "first-lower": full.split()[0].lower()}[style]


# ---------------------------------------------------------------- the messes

MESSES = {
    "mice": ["control", "header-synonyms", "header-typos", "headers-chinese", "column-order", "title-rows",
             "blank-rows", "total-row", "extra-columns", "merged-cages", "filled-down-cages", "dates-us",
             "dates-eu", "dates-eu-dots", "dates-named", "dates-excel-cells", "dates-excel-serial",
             "dates-mixed", "date-typo-future", "sex-words", "sex-symbols", "status-words", "owner-full-names",
             "owner-first-names", "owner-unknown", "padding-and-case", "duplicate-ids", "numbers-as-floats",
             "csv", "csv-semicolon", "tsv", "csv-windows-encoding", "csv-bom"],
    "plasmids": ["control", "header-synonyms", "header-typos", "headers-chinese", "column-order", "title-rows",
                 "blank-rows", "total-row", "extra-columns", "filled-down-boxes", "positions-styles",
                 "concentration-comma-decimal", "concentration-with-unit", "owner-full-names",
                 "owner-first-names", "owner-unknown", "padding", "csv", "csv-semicolon", "tsv",
                 "csv-windows-encoding", "csv-bom"],
}
SINK = 4          # messes in one kitchen-sink sheet


def typo(word: str, rng: random.Random) -> str:
    """Two neighbouring letters swapped, as a hurried hand does."""
    spots = [i for i in range(len(word) - 1) if word[i].isalpha() and word[i + 1].isalpha()
             and word[i] != word[i + 1]]
    if len(word) < 5 or not spots:
        return word
    i = rng.choice(spots[1:] or spots)
    return word[:i] + word[i + 1] + word[i] + word[i + 2:]


def make_sheet(target: str, sheet_no: int, messes: list[str], rng: random.Random, label: str) -> Sheet:
    columns = list(MOUSE_COLUMNS if target == "mice" else PLASMID_COLUMNS)
    n = rng.randint(4, 40)
    records = (mouse_records if target == "mice" else plasmid_records)(rng, sheet_no, n)
    # Not every sheet has every column: an ID or ear tag column is often missing.
    optional = {"mice": ("mouse_id", "ear_tag", "note"), "plasmids": ("plasmid_id", "a260_280", "note")}[target]
    columns = [c for c in columns if c.key not in optional or rng.random() < 0.7]
    sh = Sheet(target, label, messes, columns, [c.header for c in columns], records, [])
    m = set(messes)

    if "header-synonyms" in m:
        sh.headers = [rng.choice(c.synonyms) for c in columns]
    if "header-typos" in m:
        for i in rng.sample(range(len(columns)), k=min(2, len(columns))):
            sh.headers[i] = typo(sh.headers[i], rng)
    if "headers-chinese" in m:
        sh.headers = [c.chinese or c.header for c in columns]

    date_style = next((s for s in ("us", "eu", "eu-dots", "named", "serial") if f"dates-{s}" in m
                       or (s == "serial" and "dates-excel-serial" in m)), "iso")
    sex_style = "words" if "sex-words" in m else "symbols" if "sex-symbols" in m else "letters"
    owner_style = "display" if "owner-full-names" in m else "first" if "owner-first-names" in m else "username"
    pos_style = "A1"
    future_row = rng.randrange(n) if "date-typo-future" in m else -1
    unknown_rows = set(rng.sample(range(n), k=max(1, n // 5))) if "owner-unknown" in m else set()
    dup_row = rng.randrange(1, n) if "duplicate-ids" in m and n > 1 else -1

    for r, rec in enumerate(records):
        row = []
        for c in columns:
            v = rec[c.key]
            if c.key == "dob":
                if "dates-mixed" in m:
                    v = DATE_FORMATS[rng.choice(["iso", "us", "named", "serial"])](v)
                elif "dates-excel-cells" in m:
                    v = datetime(v.year, v.month, v.day)            # a real Excel date cell
                else:
                    v = DATE_FORMATS[date_style](v)
                if r == future_row:
                    v = str(v).replace(str(rec["dob"].year), str(rec["dob"].year + 36))   # 2026 → 2062
                    rec["dob_typed"] = v
            elif c.key == "sex":
                v = {"letters": v, "words": {"M": "male", "F": "female"}[v],
                     "symbols": {"M": "♂", "F": "♀"}[v]}[sex_style]
            elif c.key == "status" and "status-words" in m:
                v = rng.choice(STATUS_WORDS[v])
            elif c.key == "owner":
                if r in unknown_rows:
                    v = "Dr. Smith"
                    rec["owner"] = "?Dr. Smith"           # not in the lab: kept in the notes at best
                else:
                    v = owner_text(rng, v, owner_style)
            elif c.key == "position":
                v = position_text(v, rng.choice(["A1", "a1", "A-1", "A 1"]) if "positions-styles" in m else pos_style)
            elif c.key == "concentration":
                if "concentration-comma-decimal" in m:
                    v = str(v).replace(".", ",")
                elif "concentration-with-unit" in m:
                    v = f"{v} ng/µl"
                else:
                    v = str(v)
            elif c.key == "mouse_id" and r == dup_row:
                v = records[r - 1]["mouse_id"]           # the same number twice
                rec["id_typed"] = v
            elif c.key in ("mouse_id", "plasmid_id", "cage") and "numbers-as-floats" in m:
                v = float(v)
            elif c.key == "a260_280":
                v = str(v)
            if isinstance(v, str) and ("padding-and-case" in m or "padding" in m) and v and rng.random() < 0.4:
                v = f"  {v} " if "padding" in m or c.key not in ("sex", "status") else f" {v.upper()} "
            row.append(v)
        sh.cells.append(row)

    if "extra-columns" in m:
        if target == "mice":
            sh.extras = [("Weight (g)", [f"{rng.uniform(18, 32):.1f}" for _ in records]),
                         ("Initials", [rng.choice(["AK", "BC", "CD"]) for _ in records])]
        else:
            sh.extras = [("Sequenced?", [rng.choice(["yes", "no", "Sanger ok"]) for _ in records]),
                         ("Made on", [DATE_FORMATS["iso"](TODAY - timedelta(days=rng.randint(1, 900)))
                                      for _ in records])]
    if "column-order" in m:
        order = list(range(len(columns)))
        rng.shuffle(order)
        sh.columns = [columns[i] for i in order]
        sh.headers = [sh.headers[i] for i in order]
        sh.cells = [[row[i] for i in order] for row in sh.cells]
    if "title-rows" in m:
        sh.title_rows = [["Smith lab colony" if target == "mice" else "Plasmid list"], ["updated 3/2026 by AK"]]
    if "blank-rows" in m:
        sh.blank_after = set(rng.sample(range(n), k=min(3, n)))
    if "total-row" in m:
        sh.total_row = True
    if "merged-cages" in m:
        sh.merge_key = "cage"
    if "filled-down-cages" in m:
        sh.filldown_key = "cage"
    if "filled-down-boxes" in m:
        sh.filldown_key = "box"
    for f in ("csv-semicolon", "tsv", "csv-windows-encoding", "csv-bom", "csv", "dates-excel-serial"):
        if f in m:
            sh.fmt = {"csv-windows-encoding": "csv-cp1252", "dates-excel-serial": "csv"}.get(f, f)
            break
    if "merged-cages" in m or "dates-excel-cells" in m or "numbers-as-floats" in m:
        sh.fmt = "xlsx"
    return sh


def grid(sh: Sheet) -> tuple[list[list], list[tuple[int, int, int]]]:
    """The sheet as rows of cells, and the merges as (column, first row, last row), from 1."""
    rows = [list(t) for t in sh.title_rows]
    rows.append(sh.headers + [h for h, _ in sh.extras])
    merges, keys = [], [c.key for c in sh.columns]
    run_key = sh.merge_key or sh.filldown_key
    run_col = keys.index(run_key) if run_key in keys else -1
    first = last = 0                       # the current run of one value, as sheet rows from 1
    for r, cells in enumerate(sh.cells):
        cells = cells + [values[r] for _h, values in sh.extras]
        if run_col >= 0:
            if r > 0 and sh.records[r][run_key] == sh.records[r - 1][run_key] and (r - 1) not in sh.blank_after:
                cells = list(cells)
                cells[run_col] = ""            # written once, on the run's first row
                sh.blanked.add((r, run_key))
            else:
                if sh.merge_key and last > first:
                    merges.append((run_col + 1, first, last))
                first = len(rows) + 1
        rows.append(cells)
        last = len(rows)
        sh.row_numbers.append(len(rows))
        if r in sh.blank_after:
            rows.append([])
    if run_col >= 0 and sh.merge_key and last > first:
        merges.append((run_col + 1, first, last))
    if sh.total_row:
        rows.append(["TOTAL", str(len(sh.records))])
    return rows, merges


def render(sh: Sheet) -> tuple[str, bytes]:
    rows, merges = grid(sh)
    if sh.fmt == "xlsx":
        from openpyxl import Workbook
        book = Workbook()
        ws = book.active
        ws.title = "Colony" if sh.target == "mice" else "Plasmids"
        for r in rows:
            ws.append(r)
        for col, first, last in merges:
            letter = chr(ord("A") + col - 1)
            ws.merge_cells(f"{letter}{first}:{letter}{last}")
        out = io.BytesIO()
        book.save(out)
        return "sheet.xlsx", out.getvalue()
    delimiter = {"csv-semicolon": ";", "tsv": "\t"}.get(sh.fmt, ",")
    width = max(len(r) for r in rows)
    text = io.StringIO()
    # Excel writes every row as wide as the sheet ("Smith lab colony;;;;"), title lines too.
    csv.writer(text, delimiter=delimiter, lineterminator="\r\n").writerows(
        [[str(c) for c in r] + [""] * (width - len(r)) for r in rows])
    # "Windows' encoding" is the one a Windows in that language saves: GBK for a Chinese sheet.
    chinese = any(re.search(r"[\u3400-\u9fff]", h) for h in sh.headers)
    encoding = {"csv-cp1252": "gbk" if chinese else "cp1252", "csv-bom": "utf-8-sig"}.get(sh.fmt, "utf-8")
    name = "sheet.tsv" if sh.fmt == "tsv" else "sheet.csv"
    return name, text.getvalue().encode(encoding, errors="replace")


# ---------------------------------------------------------------- importing it

_SELECT = re.compile(r'<select name="((?:map|kind)-\d+)"[^>]*>(.*?)</select>', re.S)


def chosen(page: str) -> dict[str, str]:
    """What the match page's form sends untouched: each select's picked
    option, as tests/test_sheet_import.py reads it, and each ticked box."""
    out = {}
    for name, body in _SELECT.findall(page):
        picked = re.search(r'<option value="([^"]*)" selected', body)
        out[name] = picked.group(1) if picked else ""
    for name in re.findall(r'<input type="checkbox" name="([\w-]+)" value="1" checked', page):
        out[name] = "1"
    hidden = re.search(r'<input type="hidden" name="down-seen" value="1">', page)
    if hidden:
        out["down-seen"] = "1"
    return out


def text_of(page: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def import_sheet(client, sh: Sheet) -> dict:
    filename, data = render(sh)
    r = client.post(f"/import-sheet/{sh.target}/upload", data={"file": (io.BytesIO(data), filename)},
                    content_type="multipart/form-data")
    if r.status_code != 302 or "/import-sheet/file/" not in r.headers.get("Location", ""):
        return {"refused_file": text_of(r.get_data(as_text=True))[:300], "filename": filename, "data": data}
    token = r.headers["Location"].rsplit("/", 1)[1]
    page = client.get(f"/import-sheet/file/{token}").get_data(as_text=True)
    form = {**chosen(page), "fill-owner": "me",
            "sheet": "Colony" if sh.fmt == "xlsx" and sh.target == "mice" else
            "Plasmids" if sh.fmt == "xlsx" else "Sheet 1"}
    # An import refused as a whole goes back to the match page and says why: follow it.
    preview = text_of(client.post(f"/import-sheet/file/{token}/preview", data=form,
                                  follow_redirects=True).get_data(as_text=True))
    model = MouseRecord if sh.target == "mice" else PlasmidRecord
    with SessionLocal() as s:
        before = s.scalar(select(func.max(model.id))) or 0
    client.post(f"/import-sheet/file/{token}/run", data=form)
    return {"form": form, "preview": preview, "before": before, "filename": filename, "data": data}


# ---------------------------------------------------------------- scoring

def stored(rec_model, key: str):
    m = rec_model
    if isinstance(m, MouseRecord):
        return {"mouse_id": m.mouse_id, "ear_tag": m.ear_tag, "sex": m.gender, "genotype": m.transgene_1,
                "dob": m.litter.date_of_birth if m.litter else None,
                "cage": m.cage.cage_id if m.cage else "", "status": m.status, "owner": m.owner,
                "note": m.note}[key]
    return {"plasmid_id": m.plasmid_id, "name": m.name, "backbone": m.backbone, "insert": m.insert_seq,
            "resistance": m.resistance, "box": m.storage_box,
            "position": (m.box_row, m.box_col) if m.box_row is not None else None,
            "location": m.location, "concentration": m.concentration, "a260_280": m.a260_280,
            "owner": m.owner, "note": m.notes}[key]


def same(key: str, truth, got) -> bool:
    if key in ("concentration", "a260_280"):
        try:
            return abs(float(got) - float(truth)) < 1e-6
        except (TypeError, ValueError):
            return False
    if key in ("mouse_id", "plasmid_id", "position", "dob"):
        return got == truth
    return str(got or "").strip() == str(truth or "").strip()


def told(preview: str, sheet_row: int, header: str, typed: str) -> bool:
    """Did the preview warn about this value: its row's warning naming it,
    its column's dates read one way for the whole column, or the value
    named as not one the app knows?"""
    if re.search(re.escape(f"{header}: ") + r"Its dates", preview):
        return True
    if not typed:
        return False
    value = re.escape(typed)
    return bool(re.search(rf"Row {sheet_row} \([^)]*\): (?:(?!Row \d+ \().){{0,300}}?{value}", preview, re.I)
                or re.search(re.escape(f"{header}: “{typed}”"), preview)
                or re.search(rf"{value}[^.]{{0,200}}kept as typed", preview))


def score(sh: Sheet, run: dict) -> dict:
    """Outcomes for this sheet: rows and fields, counted."""
    out = {"rows": Counter(), "fields": Counter(), "by_field": defaultdict(Counter), "extras": Counter(),
           "examples": []}
    if "refused_file" in run:
        out["rows"]["refused"] += len(sh.records)
        out["examples"].append(f"file refused: {run['refused_file'][:160]}")
        return out
    preview = run["preview"]
    if "Match a column, or give a value" in preview:     # the whole import refused, and why said
        out["rows"]["refused"] += len(sh.records)
        out["examples"].append("import refused: " + preview[preview.index("Match a column"):][:120])
        return out
    refused_rows = {int(n) for n in re.findall(r"Row (\d+): ", preview)}
    model = MouseRecord if sh.target == "mice" else PlasmidRecord
    keys = [c.key for c in sh.columns]
    headers = dict(zip(keys, sh.headers))
    with SessionLocal() as s:
        made = list(s.scalars(select(model).where(model.id > run["before"]).order_by(model.id)))
        expected = [i for i, n in enumerate(sh.row_numbers) if n not in refused_rows]
        if len(made) != len(expected):          # can't pair them up: count every row as lost
            out["rows"]["lost"] += len(sh.records)
            out["examples"].append(f"{len(made)} records made for {len(expected)} rows")
            return out
        out["rows"]["refused"] += len(sh.records) - len(expected)
        out["rows"]["imported"] += len(expected)
        for i, m in zip(expected, made):
            rec, sheet_row = sh.records[i], sh.row_numbers[i]
            notes = (stored(m, "note") or "").lower()
            for key in keys:
                truth = rec[key]
                if key == "note" and not truth:
                    continue
                if key == "owner" and str(truth).startswith("?"):
                    truth = None                            # nobody in the lab: can only be kept
                got = stored(m, key)
                if key == "note":
                    ok = truth.lower() in notes
                else:
                    ok = truth is not None and same(key, truth, got)
                if ok:
                    outcome = "correct"
                else:
                    typed = "" if (i, key) in sh.blanked else sh.cells[i][keys.index(key)]
                    typed = typed.isoformat()[:10] if isinstance(typed, datetime) else str(typed).strip()
                    # A date may be kept as the app read it (26/12/2061 as 2061-12-26).
                    seen = {typed, *(si.tidy_dates([typed])[0] if key in ("dob",) and typed else [])} - {""}
                    if any(t.lower() in notes or told(preview, sheet_row, headers[key], t) for t in seen):
                        outcome = "kept"
                    elif got in (None, "", 0):
                        outcome = "blank"
                    else:
                        outcome = "wrong"
                    if len(out["examples"]) < 4:
                        out["examples"].append(f"row {sheet_row} {key}: sheet {typed!r} → stored {got!r} ({outcome})")
                out["fields"][outcome] += 1
                out["by_field"][key][outcome] += 1
            for header, values in sh.extras:
                out["extras"]["kept" if str(values[i]).lower() in notes else "lost"] += 1
    return out


# ---------------------------------------------------------------- the run

def set_up_lab() -> object:
    make_user(IMPORTER, role="admin")
    with SessionLocal() as s:
        for username, full in MEMBERS:
            s.add(UserAccount(username=username, display_name=full, password_hash="x", role="member"))
        s.commit()
    return client_for(IMPORTER)


def plan(per_mess: int, seed: int, sink: int) -> list[tuple[str, str, list[str], int]]:
    """(target, label, messes, seed) for every sheet."""
    rng, sheets = random.Random(seed), []
    for target, messes in MESSES.items():
        for mess in messes:
            for _ in range(per_mess):
                sheets.append((target, mess, [] if mess == "control" else [mess], rng.randrange(1 << 30)))
        pool = [x for x in messes if x != "control"]
        for _ in range(sink):
            sheets.append((target, "kitchen-sink", rng.sample(pool, SINK), rng.randrange(1 << 30)))
    return sheets


def rate(c: Counter, *keys) -> float:
    total = sum(c.values())
    return 100.0 * sum(c[k] for k in keys) / total if total else 0.0


def table(groups: dict[str, dict]) -> list[str]:
    lines = ["| Mess | Sheets | Rows imported | Fields correct | Kept (told) | Blank (silent) | Wrong (silent) | Extra columns kept |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, g in groups.items():
        extras = f"{rate(g['extras'], 'kept'):.0f}%" if sum(g["extras"].values()) else "–"
        lines.append(f"| {name} | {g['sheets']} | {rate(g['rows'], 'imported'):.1f}% | "
                     f"{rate(g['fields'], 'correct'):.1f}% | {rate(g['fields'], 'kept'):.1f}% | "
                     f"{rate(g['fields'], 'blank'):.1f}% | {rate(g['fields'], 'wrong'):.1f}% | {extras} |")
    return lines


def version() -> tuple[str, str]:
    from app.feedback import app_version
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, timeout=5).stdout.strip()
        # The app's code changed since that commit: say so, as a result of it would mislead.
        if subprocess.run(["git", "status", "--porcelain", "--", "app"], cwd=ROOT, capture_output=True,
                          text=True, timeout=5).stdout.strip():
            commit += "-dirty"
    except (OSError, subprocess.SubprocessError):
        commit = ""
    return app_version(), commit


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--per-mess", type=int, default=12, help="sheets for each mess (default 12)")
    ap.add_argument("--sink", type=int, default=40, help="kitchen-sink sheets per database (default 40)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, help="also keep every sheet, with its truth, in this folder")
    args = ap.parse_args()

    client = set_up_lab()
    sheets = plan(args.per_mess, args.seed, args.sink)
    groups: dict[str, dict] = {}
    started = time.monotonic()
    for no, (target, label, messes, sheet_seed) in enumerate(sheets, start=1):
        rng = random.Random(sheet_seed)
        sh = make_sheet(target, no, messes, rng, label)
        run = import_sheet(client, sh)
        result = score(sh, run)
        for name in (f"{target}: {label}", f"{target}: all"):
            g = groups.setdefault(name, {"sheets": 0, "rows": Counter(), "fields": Counter(), "extras": Counter(),
                                         "by_field": defaultdict(Counter), "examples": []})
            g["sheets"] += 1
            for k in ("rows", "fields", "extras"):
                g[k].update(result[k])
            for key, c in result["by_field"].items():
                g["by_field"][key].update(c)
            if len(g["examples"]) < 6:
                g["examples"] += result["examples"][: 6 - len(g["examples"])]
        if args.out:
            folder = args.out / f"{no:04d}-{target}-{label}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / run["filename"]).write_bytes(run["data"])
            truth = [{k: (v.isoformat() if isinstance(v, date) else v) for k, v in r.items()} for r in sh.records]
            (folder / "truth.json").write_text(json.dumps({"target": target, "messes": messes,
                                                           "row_numbers": sh.row_numbers, "records": truth},
                                                          indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"\r{no}/{len(sheets)} sheets", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)

    ver, commit = version()
    ordered = {k: groups[k] for t in MESSES for k in
               [f"{t}: all", *[f"{t}: {m}" for m in MESSES[t]], f"{t}: kitchen-sink"] if k in groups}
    md = [f"# Import from Excel: messy-sheet benchmark", "",
          f"BioManager {ver} ({commit}), seed {args.seed}, {args.per_mess} sheets per mess and "
          f"{args.sink} kitchen-sink sheets ({SINK} messes each) per database; {len(sheets)} sheets, "
          f"{time.monotonic() - started:.0f} s.", "",
          "Imported with every column as the match page suggests. *Kept*: not stored as it should be, but "
          "the sheet's text is in the notes or the preview warned; *blank* and *wrong*: silently.", ""]
    for t in MESSES:
        md += [f"## {t.capitalize()}", "", *table({k.split(": ", 1)[1]: v for k, v in ordered.items()
                                                    if k.startswith(f"{t}: ")}), ""]
        g = ordered[f"{t}: all"]
        md += ["By column (all sheets):", "", "| Column | Correct | Kept | Blank | Wrong |", "|---|---:|---:|---:|---:|"]
        for key, c in sorted(g["by_field"].items()):
            md.append(f"| {key} | {rate(c, 'correct'):.1f}% | {rate(c, 'kept'):.1f}% | {rate(c, 'blank'):.1f}% | "
                      f"{rate(c, 'wrong'):.1f}% |")
        md.append("")
    md += ["## Examples of what went wrong", ""]
    for name, g in ordered.items():
        if g["examples"] and not name.endswith(": all"):
            md += [f"- **{name}**", *[f"  - {e}" for e in g["examples"]]]

    RESULTS.mkdir(parents=True, exist_ok=True)
    stem = f"import-{ver}-{commit or 'unknown'}-seed{args.seed}"
    (RESULTS / f"{stem}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (RESULTS / f"{stem}.json").write_text(json.dumps({
        "version": ver, "commit": commit, "seed": args.seed, "per_mess": args.per_mess, "sink": args.sink,
        "groups": {k: {"sheets": g["sheets"], "rows": dict(g["rows"]), "fields": dict(g["fields"]),
                       "extras": dict(g["extras"]), "by_field": {f: dict(c) for f, c in g["by_field"].items()},
                       "examples": g["examples"]} for k, g in ordered.items()}}, indent=1, ensure_ascii=False),
        encoding="utf-8")
    print("\n".join(md[:2] + md[6:]))
    print(f"\nWritten: {RESULTS / stem}.md and .json")


if __name__ == "__main__":
    main()
