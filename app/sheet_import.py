"""Bring a spreadsheet into any database: Excel (.xlsx), CSV or TSV.

1. Upload. Every sheet of a workbook is read (openpyxl); the first row with
   two or more filled cells is the header.
2. Match columns. Each spreadsheet column is matched to one of the
   database's columns by its header, loosely: case, punctuation, plurals and
   "#" don't matter, a lab's usual synonyms count ("Position", "Slot" and
   "Well" are the position; "DOB" and "Born" the date of birth), and a near
   spelling is offered with less confidence. Where a name is ambiguous the
   values decide: a "Location" column of A1, B2… is a position, one of
   "Freezer 2" is a location note. Each database column takes one
   spreadsheet column at most. What doesn't match becomes a new column,
   where the database has columns of its own (inventories, organism
   databases), or goes into the notes as "Header: value", so nothing is lost.
   A column the database must have and the sheet lacks gets one value for
   every row (the owner: you).
3. Preview. The whole import runs through each database's own save code
   (the same checks as its dialogs), each row in a savepoint, and is then
   rolled back: what it would create, and why any row can't be.
4. Import. The same again, committed as one batch, which Batch history can
   undo. Rows with a problem are skipped, and listed.

Values are tidied on the way: dates as Excel writes them (3/14/2026,
14.03.26, or a date number), day or month first decided per column; sexes
(Male, m, ♂ → M); statuses and purposes by their names; people by user
name, display name or first name.

The uploaded rows wait between steps in data_dir()/imports, one JSON file
per upload, removed after a day.
"""
from __future__ import annotations

import csv
import difflib
from html import unescape as html_unescape
import io
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from flask import session as flask_session
from markupsafe import Markup
from sqlalchemy import select, text
from werkzeug.datastructures import ImmutableMultiDict

from . import access, audit, lab
from .db import SessionLocal
from .i18n import gettext, ngettext, translate_value
from .paths import data_dir

bp = Blueprint("sheet_import", __name__, url_prefix="/import-sheet")

MAX_ROWS = 5000
MAX_COLS = 80
MAX_BYTES = 15 * 1024 * 1024
KEEP_SECONDS = 24 * 3600


class ImportProblem(Exception):
    """The file can't be read, or the import can't start."""


class Gone(Exception):
    """The upload was imported already, or is over a day old."""


@bp.errorhandler(Gone)
def _gone(_error):
    flash(gettext("That upload is finished: it was imported, or it's more than a day old. Upload the file again to import more."), "warning")
    return redirect(url_for("index"))


class RowError(Exception):
    """This row can't be created; the others go on."""


@bp.before_request
def require_login():
    if g.get("user") is None:
        return redirect(url_for("login", next=request.path))


# ---------------------------------------------------------------- reading

def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, datetime):
        return value.date().isoformat() if (value.hour, value.minute, value.second) == (0, 0, 0) \
            else value.isoformat(sep=" ", timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        # Shortest text that is this number: 0.1+0.2 is 0.3, and 1.5e-07
        # stays 1.5e-07 (format "f" kept 6 decimals and made it 0).
        text = "%.15g" % value
        return text if "e" in text else text.rstrip("0").rstrip(".") if "." in text else text
    return str(value).strip()


def _unguard(text: str) -> str:
    """BioManager's own CSV exports put ' before a value starting with = + -
    or @ (so a spreadsheet doesn't run it: services.sheet_safe); reading
    one back, "'+/+" is the genotype +/+ again."""
    return text[1:] if text[:1] == "'" and text[1:2] in ("=", "+", "-", "@") else text


def _trim(rows: list[list[str]]) -> list[list[str]]:
    """Rows of one width, the empty ones at the end dropped. Empty rows
    before and between stay, so row numbers are Excel's."""
    while rows and not any(c.strip() for c in rows[-1]):
        rows.pop()
    width = max((max((i for i, c in enumerate(r) if c.strip()), default=-1) for r in rows), default=-1) + 1
    return [(r + [""] * width)[:width] for r in rows]


_MERGE = re.compile(rb'<(?:\w+:)?mergeCell\s+ref="([A-Z]+[0-9]+:[A-Z]+[0-9]+)"')


def _merged_ranges(data: bytes) -> dict[str, list[tuple[int, int, int, int]]]:
    """{sheet name: its merged cells as (min_col, min_row, max_col, max_row)}.
    openpyxl's fast reader doesn't see them, so the sheets' XML is scanned
    for them (as text: nothing in it is parsed or expanded)."""
    import zipfile
    from openpyxl.utils.cell import range_boundaries
    out: dict[str, list[tuple[int, int, int, int]]] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            book = z.read("xl/workbook.xml").decode("utf-8", "replace")
            rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8", "replace")
            targets = {m.group(1): m.group(2) for m in re.finditer(
                r'<Relationship\b[^>]*?Id="([^"]+)"[^>]*?Target="([^"]+)"', rels)}
            targets.update({m.group(2): m.group(1) for m in re.finditer(
                r'<Relationship\b[^>]*?Target="([^"]+)"[^>]*?Id="([^"]+)"', rels)})
            for m in re.finditer(r'<(?:\w+:)?sheet\b[^>]*?name="([^"]*)"[^>]*?r:id="([^"]+)"', book):
                target = targets.get(m.group(2), "")
                part = target.lstrip("/") if target.startswith("/") else "xl/" + target
                if part not in z.namelist():
                    continue
                refs: set[bytes] = set()
                with z.open(part) as f:
                    tail = b""
                    while chunk := f.read(1 << 20):
                        block = tail + chunk
                        refs.update(_MERGE.findall(block))
                        tail = block[-200:]
                name = html_unescape(m.group(1))
                out[name] = [range_boundaries(r.decode()) for r in refs]
    except Exception:           # a workbook openpyxl opened but this can't read: no merged cells
        return {}
    return out


MAX_MERGES = 20_000


def _fill_merged(rows: list[list[str]], ranges) -> None:
    """A cell merged down over several rows (a Cage # typed once for all
    its mice) belongs to each of those rows, as the sheet shows it.

    Each row of a column is filled once however the ranges overlap (a real
    sheet's merges don't; a made-up one's could, a million times over), so
    the work is the sheet's size, not the number of merges."""
    done: dict[int, int] = {}            # column → rows below this are filled
    for min_col, min_row, _max_col, max_row in sorted(ranges[:MAX_MERGES], key=lambda r: (r[0], r[1])):
        if max_row <= min_row or min_row > len(rows) or min_col > MAX_COLS:
            continue
        top = rows[min_row - 1]
        value = top[min_col - 1] if min_col - 1 < len(top) else ""
        end = min(max_row, len(rows))
        start = max(min_row, done.get(min_col, 0))
        if not value or start >= end:
            continue
        for r in range(start, end):
            row = rows[r]
            row.extend([""] * (min_col - len(row)))
            if not row[min_col - 1]:
                row[min_col - 1] = value
        done[min_col] = end


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def read_workbook(filename: str, data: bytes) -> dict[str, list[list[str]]]:
    """{sheet name: rows of text}, empty sheets left out."""
    name = (filename or "").lower()
    if len(data) > MAX_BYTES:
        raise ImportProblem(gettext("That file is over 15 MB. Save only the sheet you need, or as CSV."))
    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        try:
            book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:
            raise ImportProblem(gettext("That doesn't open as an Excel workbook (%(error)s). Save it again from Excel as .xlsx, or as CSV.",
                                        error=exc.__class__.__name__)) from exc
        sheets = {}
        merged = _merged_ranges(data)
        for sheet in book.worksheets:
            rows = []
            for values in sheet.iter_rows(min_row=1, values_only=True):
                rows.append([_cell(v) for v in values[:MAX_COLS]])
                if len(rows) > MAX_ROWS + 20:
                    break
            _fill_merged(rows, merged.get(sheet.title, ()))
            rows = _trim(rows)
            if rows:
                sheets[sheet.title] = rows
        book.close()
        return sheets
    if name.endswith(".xls"):
        raise ImportProblem(gettext("That's an old-style .xls file. In Excel choose File → Save As → Excel Workbook (.xlsx), or CSV, and upload that."))
    if not name.endswith((".csv", ".tsv", ".txt")):
        raise ImportProblem(gettext("Upload an Excel workbook (.xlsx) or a CSV file."))
    text = _decode(data)
    first = text.split("\n", 1)[0]
    if name.endswith(".tsv") or first.count("\t") > max(first.count(","), first.count(";")):
        delimiter = "\t"
    elif first.count(";") > first.count(","):
        delimiter = ";"
    else:
        delimiter = ","
    rows = [[_unguard(c.strip()) for c in r[:MAX_COLS]] for r in csv.reader(io.StringIO(text), delimiter=delimiter)]
    rows = _trim(rows[: MAX_ROWS + 20])
    return {"Sheet 1": rows} if rows else {}


def split_header(rows: list[list[str]]) -> tuple[list[str], list[list[str]], int]:
    """(headers, data rows, the sheet's row number of the first). The header
    is the first row, among the first ten, filled about as widely as the
    widest of them: a title line above it ("Colony, March 2026") is
    skipped, even one of two or three cells."""
    filled = [sum(1 for c in row if c.strip()) for row in rows[:10]]
    widest = max(filled, default=0)
    need = max(min(2, widest), widest - max(1, widest // 5))
    at = next((i for i, n in enumerate(filled) if n >= need), 0)
    headers, seen = [], {}
    for i, h in enumerate(rows[at] if rows else []):
        h = h.strip() or f"Column {i + 1}"
        seen[h] = seen.get(h, 0) + 1
        headers.append(h if seen[h] == 1 else f"{h} ({seen[h]})")
    return headers, rows[at + 1:], at + 2


TOTAL_WORDS = {"total", "totals", "grand total", "subtotal", "sub total", "sum"}


def is_total(row: list[str]) -> bool:
    """A summary line under the records: its first filled cell is only
    "TOTAL", "Grand total:" or the like ("Total RNA" is a record)."""
    first = next((c for c in row if c.strip()), "")
    return norm(first) in TOTAL_WORDS


# ---------------------------------------------------------------- matching

_WORD_NUMBER = {"no", "num", "nr", "nbr"}
# Too general to decide a column on their own: "Hazard class" isn't a category.
_GENERIC = {"class", "type", "kind", "group", "number", "date", "name", "id", "use", "state", "status", "from",
            "who", "where", "note", "comment", "description", "count", "n", "f", "gen", "stock", "line", "size",
            "amount", "value", "code", "tag", "set up", "item", "sample", "user", "person"}


def norm(text: str) -> str:
    """Lower case, punctuation and '#' spelled out, plurals dropped:
    "Cat. No." → "cat number", "Positions" → "position"."""
    t = (text or "").lower().replace("#", " number ").replace("♀", " female ").replace("♂", " male ")
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    words = []
    for w in t.split():
        if w in _WORD_NUMBER:
            w = "number"
        elif len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
            w = w[:-1]
        words.append(w)
    return " ".join(words)


_UNITS = {"g", "mg", "ug", "kg", "l", "ml", "ul", "m", "mm", "um", "nm", "cm", "mol", "mmol", "umol", "nmol",
          "pmol", "mw", "da", "kda", "bp", "kb", "c", "degc", "percent", "pct", "x", "h", "hr", "min", "s", "d",
          "wk", "mo", "yr", "iu", "u", "ng", "pg", "rpm", "od", "v", "mv"}
# Headers that may mean a place or a position: the values decide.
_EITHER = {"location", "place", "where", "loc", "storage", "storage location"}
_POSITION = re.compile(r"^\s*([A-Za-z]{1,2}\s*[-.:/ ]?\s*\d{1,3}|\d{1,3}\s*[-.:/,]\s*\d{1,3}|\d{1,3}\s*[-.:/ ]?\s*[A-Za-z]{1,2})\s*$")
_DATEISH = re.compile(r"^\s*(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})(\s.*)?$")
_NUMBER = re.compile(r"^\s*-?\d[\d,]*(\.\d+)?\s*$")


@dataclass
class Field:
    key: str
    label: str
    synonyms: tuple[str, ...] = ()
    kind: str = "text"                 # text | date | number | sex | choice | owner | position | place
    required: bool = False
    fill: str = ""                     # suggested value for every row when the sheet lacks it
    choices: dict[str, str] | None = None
    note: str = ""                     # shown beside it on the match page
    custom: bool = False               # one of the database's own columns
    options: tuple = ()                # (value, label) for the "every row gets" choice

    def also(self) -> list[str]:
        """A few other names it's known by, for the upload page."""
        own = norm(self.label)
        return [s for s in self.synonyms if norm(s) != own][:4]

    def names(self) -> set[str]:
        return {norm(self.label), norm(self.key.replace("_", " ")), *(norm(s) for s in self.synonyms)}


def _shape(values: list[str]) -> str:
    filled = [v for v in values if v.strip()][:40]
    if not filled:
        return ""
    if sum(1 for v in filled if _DATEISH.match(v) or _named_month(v)) >= 0.7 * len(filled):
        return "date"
    if sum(1 for v in filled if _NUMBER.match(v)) >= 0.8 * len(filled):
        return "number"
    if sum(1 for v in filled if _POSITION.match(v)) >= 0.7 * len(filled):
        return "position"
    return "text"


def _positions(values: list[str]) -> bool:
    """Values a position column may hold: "A1", "4-7", and plain numbers
    (a box numbered 1…81), mixed as a lab's sheet mixes them."""
    filled = [v for v in values if v.strip()][:40]
    return not filled or sum(1 for v in filled if _POSITION.match(v) or re.fullmatch(r"\s*\d{1,4}\s*", v)) \
        >= 0.7 * len(filled)


def score(header: str, values: list[str], f: Field) -> tuple[float, str]:
    """How well a spreadsheet column fits a database column, and why."""
    h = norm(header)
    if not h:
        return 0.0, ""
    names = f.names()
    shape = _shape(values)
    if h in names:
        best, why = (1.0, gettext("same name")) if h in (norm(f.label), norm(f.key.replace("_", " "))) \
            else (0.95, gettext("“%(header)s” means %(label)s", header=header, label=translate_value(f.label, "import")))
    else:
        best, why = 0.0, ""
        for n in names:
            # "Rack position" is a position and "Freezer box" a box: a
            # spreadsheet header names its thing last. "Cage colour" is not
            # a cage.
            if n and n not in _GENERIC and h.endswith(f" {n}") and 0.8 > best:
                best, why = 0.8, gettext("“%(header)s” is a kind of %(name)s", header=header, name=n)
            # "Hazard class", "Weight (g)": the name, then a generic word or a unit.
            rest = h[len(n) + 1:].split() if n and h.startswith(f"{n} ") else []
            if (rest and n not in _GENERIC and all(w in _GENERIC or w in _UNITS for w in rest)
                    and 0.75 > best):
                best, why = 0.75, gettext("“%(header)s” is %(name)s", header=header, name=n)
            ratio = difflib.SequenceMatcher(None, h, n).ratio()
            if ratio >= 0.82 and ratio * 0.85 > best:
                best, why = ratio * 0.85, gettext("spelled like “%(name)s”", name=n)
    if best and f.kind == "position" and not _positions(values):
        best *= 0.4                       # "Location: Freezer 2" is a note, not a position
    if best and f.kind == "place" and shape == "position" and h in _EITHER:
        best *= 0.5                       # and "Location: A1" is a position, not a note
    if best and f.kind == "date" and shape in ("text", "position"):
        best *= 0.6
    if best and f.kind == "number" and shape in ("text", "position"):
        best *= 0.6
    if not best and f.kind == "position" and shape == "position" and h in _EITHER:
        best, why = 0.9, gettext("“%(header)s” holds positions like %(example)s", header=header,
                                    example=next(v for v in values if v.strip()))
    return best, why


def auto_match(headers: list[str], columns: list[list[str]], fields: list[Field]) -> dict[int, tuple[str, float, str]]:
    """{column index: (field key, score, why)}, each field used at most once,
    the best fits first."""
    candidates = []
    for i, header in enumerate(headers):
        for f in fields:
            s, why = score(header, columns[i], f)
            if s >= 0.6:
                candidates.append((s, i, f.key, why))
    taken_fields, taken_cols, out = set(), set(), {}
    for s, i, key, why in sorted(candidates, key=lambda c: -c[0]):
        if i in taken_cols or key in taken_fields:
            continue
        out[i] = (key, s, why)
        taken_cols.add(i)
        taken_fields.add(key)
    return out


# ---------------------------------------------------------------- tidying values

_SLASH = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\s*$")
SEXES = {"m": "M", "male": "M", "man": "M", "boy": "M", "f": "F", "female": "F", "woman": "F", "girl": "F"}


_NAMED_MONTH = ("%d-%b-%y", "%d-%b-%Y", "%d %b %y", "%d %b %Y", "%d-%B-%Y", "%d %B %Y", "%d-%B-%y",
                "%b %d, %Y", "%b %d %Y", "%B %d, %Y", "%B %d %Y", "%d. %b %Y", "%d.%b.%Y", "%d/%b/%Y", "%d/%b/%y")


def _named_month(raw: str) -> date | None:
    """12-May-26, 12 May 2026, May 12, 2026 and the like."""
    cleaned = " ".join(raw.replace("Sept", "Sep").split())
    for fmt in _NAMED_MONTH:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def tidy_dates(values: list[str], day_first: bool = False) -> tuple[list[str], list[str]]:
    """ISO dates for a column, and what to warn about. 03/04/2026 is read
    day or month first for the whole column: a first number over 12 means
    day first, a second over 12 month first; with neither, the lab's own
    date style decides (`day_first`: Lab setup's "26 Sep 2026")."""
    from .services import parse_date

    lab_day_first = day_first
    out, notes, parsed = list(values), [], {}
    day_first = month_first = False
    for i, raw in enumerate(values):
        raw = raw.strip()
        if not raw:
            continue
        iso = raw[:10]
        if re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", iso):
            try:
                y, m, d = (int(x) for x in iso.split("-"))
                out[i] = date(y, m, d).isoformat()
                continue
            except ValueError:
                pass
        if re.match(r"^\d{5}(\.\d+)?$", raw) and 20000 < float(raw) < 80000:
            out[i] = (date(1899, 12, 30) + timedelta(days=int(float(raw)))).isoformat()   # an Excel date number
            continue
        # 15-03-26 is a day, a month and a year like 15/03/26, not 2015-03-26:
        # two-digit groups go to the day-or-month rule below before anything
        # reads them as a year first.
        match = _SLASH.match(raw)
        known = None if match else (parse_date(raw) or _named_month(raw))
        if known:
            out[i] = known.isoformat()
            continue
        if not match:
            notes.append(gettext("“%(value)s” isn't a date, so it's left blank.", value=raw))
            out[i] = ""
            continue
        a, b, y = (int(x) for x in match.groups())
        parsed[i] = (a, b, y + 2000 if y < 100 else y)
        day_first = day_first or a > 12
        month_first = month_first or b > 12
    guessed = False
    read_day_first = (day_first and not month_first) or (lab_day_first and not day_first and not month_first)
    for i, (a, b, y) in parsed.items():
        month, day = (b, a) if read_day_first else (a, b)
        guessed = guessed or (not day_first and not month_first and a != b)
        try:
            out[i] = date(y, month, day).isoformat()
        except ValueError:
            notes.append(gettext("“%(value)s” isn't a date, so it's left blank.", value=values[i]))
            out[i] = ""
    if day_first and month_first:
        notes.append(gettext("Its dates mix day-first and month-first: check them after importing."))
    elif guessed and read_day_first:
        notes.append(gettext("Its dates were read day first (03/04/2026 as 3 April), as Lab setup's date style says."))
    elif guessed:
        notes.append(gettext("Its dates were read month first (03/04/2026 as 4 March)."))
    return out, notes[:5]


def tidy_choice(raw: str, choices: dict[str, str]) -> tuple[str, bool]:
    """(stored value, recognised)."""
    key = norm(raw)
    if not key:
        return "", True
    if key in choices:
        return choices[key], True
    close = difflib.get_close_matches(key, list(choices), n=1, cutoff=0.85)
    return (choices[close[0]], True) if close else (raw.strip(), False)


def _people(session) -> dict[str, str]:
    from .models import UserAccount
    out = {}
    for u in session.scalars(select(UserAccount).where(UserAccount.disabled.is_(False))):
        for name in filter(None, (u.username, u.display_name, u.short_name,
                                  (u.display_name or "").split(" ")[0])):
            out.setdefault(name.strip().lower(), u.username)
    return out


# ---------------------------------------------------------------- targets

@dataclass
class Target:
    """One database's records, as an import sees them."""
    key: str
    title: str                  # "Mice in Mouse colony"
    noun: str
    nouns: str
    fields: list[Field]
    back_url: str
    can_add_columns: bool = False
    columns_note: str = ""
    module: object = None
    mv: object = None
    # Columns that are worked out from others (the Mice export's Age), left
    # out by default: in the notes they would go stale.
    derived: tuple[str, ...] = ()

    def prepare(self, session) -> dict:
        return {}

    def add_column(self, session, ctx, label: str, kind: str) -> str:
        raise NotImplementedError

    def create(self, session, ctx, values: dict[str, str], extras: list[tuple[str, str]]) -> tuple[str, list[str]]:
        raise NotImplementedError

    def finish(self, session, ctx) -> None:
        pass


def _extras_note(existing: str, extras: list[tuple[str, str]]) -> str:
    bits = [f"{h}: {v}" for h, v in extras if v.strip()]
    return "\n".join(filter(None, [existing.strip(), *bits]))


def _owner(ctx, raw: str, warnings: list[str], extras: list[tuple[str, str]]) -> str:
    if not raw.strip():
        return g.user.username
    found = ctx["people"].get(raw.strip().lower())
    if found:
        return found
    warnings.append(gettext("“%(name)s” isn't anyone in the lab, so it's yours; the name is kept in the notes.", name=raw))
    extras.append(("Owner in the spreadsheet", raw))
    return g.user.username


def _catch_flashes(before: int) -> list[str]:
    """Messages a reused save function flashed (it tells the dialog that
    way); they belong to this row, not the next page."""
    flashes = flask_session.get("_flashes") or []
    new = [message for _category, message in flashes[before:]]
    if new:
        del flashes[before:]
        flask_session["_flashes"] = flashes
    return new


# -- Mouse colony --------------------------------------------------------------

MOUSE_STATUSES = {"breeder": "breeder", "breeding": "breeder", "breed": "breeder", "experiment": "experiment",
                  "exp": "experiment", "experimental": "experiment", "geno": "geno", "genotyping": "geno",
                  "to genotype": "geno", "transfer": "transfer", "transferred": "transfer", "sac": "sac",
                  "sacrificed": "sac", "sacked": "sac", "euthanized": "sac", "euthanised": "sac", "dead": "sac",
                  "culled": "sac", "stock": "experiment", "holding": "experiment"}


class MiceTarget(Target):
    def prepare(self, session):
        from .models import MouseRecord
        from .models import CageRecord
        from .inventory_service import get_setting
        from .services import MOUSE_ID_HIGH
        taken = set(session.scalars(select(MouseRecord.mouse_id)))
        high = get_setting(session, MOUSE_ID_HIGH, "")
        first = max(max(taken, default=0), int(high) if high.isdigit() else 0) + 1
        return {"people": _people(session), "taken": taken, "next": first, "place_cages": {},
                "cages_before": set(session.scalars(select(CageRecord.cage_id))), "litter_dates": {}}

    def finish(self, session, ctx):
        """A cage the import made belongs to its first mouse's owner, not
        to whoever ran the import: they wean, breed and move it. Set last,
        so the rest of its mice could still be put in it."""
        from .inventory_service import get_setting, set_setting
        from .models import CageRecord
        from .services import MOUSE_ID_HIGH
        high = get_setting(session, MOUSE_ID_HIGH, "")
        top = max(ctx["taken"], default=0)
        if top > (int(high) if high.isdigit() else 0):
            set_setting(session, MOUSE_ID_HIGH, str(top))     # never handed out again
        for code, owner in ctx["carry"].get("cage_owner", {}).items():
            cage = session.scalar(select(CageRecord).where(CageRecord.cage_id == code))
            if cage is not None and owner:
                cage.owner = owner
        session.flush()

    def _new_cage(self, session, ctx) -> str:
        """A cage for a rack and position with no cage number: numbered above
        both the colony's cages and every number the sheet itself uses, so a
        later row's cage 106 is never this one."""
        from .models import CageRecord
        from .services import _cage_number, reserve_cage_ids
        in_sheet = ctx.get("sheet", {}).get("cage_id", set())
        highest = max((_cage_number(c) for c in in_sheet), default=0)
        code = reserve_cage_ids(session, 1)[0]
        taken = set(session.scalars(select(CageRecord.cage_id)))
        number = max(int(code), highest + 1)
        while str(number) in taken or str(number) in in_sheet:
            number += 1
        session.add(CageRecord(cage_id=str(number), owner=g.user.username))
        session.flush()
        return str(number)

    def create(self, session, ctx, v, extras):
        from .app import populate_mouse_from_form
        from .models import MouseRecord
        from .services import parse_date
        warnings: list[str] = []
        typed = v.get("mouse_id", "").strip()
        if typed.isdigit() and int(typed) not in ctx["taken"] and int(typed) > 0:
            mouse_id = int(typed)
        else:
            while ctx["next"] in ctx["taken"]:
                ctx["next"] += 1
            mouse_id = ctx["next"]
            if typed:
                extras.insert(0, ("ID in the spreadsheet", typed))
            if typed.isdigit() and int(typed) > 0:
                warnings.append(gettext("Mouse ID %(typed)s is taken (in the colony or by an earlier row), so this one is #%(id)s; %(typed)s is kept in its notes.",
                                        typed=typed, id=mouse_id))
        ctx["taken"].add(mouse_id)
        owner = _owner(ctx, v.get("owner", ""), warnings, extras)
        form = {k: v[k] for k in ("gender", "cage_id", "cage_location", "litter_id", "date_of_birth", "status",
                                  "date_of_death") if k in v}
        form["owner"] = owner
        # Nothing is born tomorrow: a future date is a typo (2062 for 2026).
        born = parse_date(form.get("date_of_birth", ""))
        if born is not None and born > date.today():
            warnings.append(gettext("The date of birth %(date)s is in the future, so it's left blank and kept in the notes.", date=born.isoformat()))
            extras.append(("Date of birth in the spreadsheet", v.get("date_of_birth", "")))
            form["date_of_birth"], born = "", None
        # One litter, one date of birth: a later row can't re-date the mice before it.
        litter = form.get("litter_id", "").strip()
        if litter and born is not None:
            first = ctx["litter_dates"].setdefault(litter, born)
            if first != born:
                warnings.append(gettext("Litter %(litter)s was born %(first)s in an earlier row, so this mouse is too; %(date)s is kept in its notes.",
                                        litter=litter, first=first.isoformat(), date=born.isoformat()))
                extras.append(("Date of birth in the spreadsheet", born.isoformat()))
                form["date_of_birth"] = first.isoformat()
        # A rack and position with no cage number: one new cage per place.
        if not form.get("cage_id", "").strip() and (v.get("cage_rack") or v.get("cage_position")):
            where = (v.get("cage_rack", ""), v.get("cage_position", ""))
            if where not in ctx["place_cages"]:
                ctx["place_cages"][where] = self._new_cage(session, ctx)
            form["cage_id"] = ctx["place_cages"][where]
        if v.get("cage_rack") or v.get("cage_position"):
            form["cage_rack"], form["cage_position"] = v.get("cage_rack", ""), v.get("cage_position", "")
        form["note"] = _extras_note(v.get("note", ""), extras)
        # A mouse's genotype is its transgenes; an old sheet's genotype
        # column becomes Transgene 1, as Add many does.
        form["transgene_1"] = v.get("genotype", "").strip()
        for n in (2, 3, 4):                       # the Mice export's own Transgene_2–4 columns
            form[f"transgene_{n}"] = v.get(f"transgene_{n}", "").strip()
        mouse = MouseRecord(mouse_id=mouse_id, owner=owner)
        before = len(flask_session.get("_flashes") or [])
        populate_mouse_from_form(session, mouse, ImmutableMultiDict(form), preserve_owner_on_transfer=False)
        warnings += _catch_flashes(before)
        if mouse.cage is not None and mouse.cage.cage_id not in ctx["cages_before"]:
            ctx["carry"].setdefault("cage_owner", {}).setdefault(mouse.cage.cage_id, owner)   # see finish
        if mouse.cage is None and v.get("cage_location", "").strip():
            # A room belongs to a cage; with no cage it's kept in the notes.
            mouse.note = _extras_note(mouse.note or "", [("Room", v["cage_location"])])
        session.add(mouse)
        session.flush()
        return f"#{mouse_id}", warnings


def _mouse_statuses(session) -> dict[str, str]:
    """The lab's own statuses (its dropdown, and those in use) by their
    names, then the usual synonyms for the built-in ones."""
    from .models import DropdownOption, MOUSE_STATUS_OPTIONS, MouseRecord
    lab_values = [*MOUSE_STATUS_OPTIONS,
                  *session.scalars(select(DropdownOption.option_value).where(DropdownOption.field_name == "status")),
                  *session.scalars(select(MouseRecord.status).distinct())]
    out = {k: v for k, v in MOUSE_STATUSES.items()}
    out.update({norm(v): v for v in lab_values if v and v.strip()})
    return out


def mice_target(session) -> Target:
    return MiceTarget(
        key="mice", title=gettext("Mice in %(db)s", db=translate_value(lab.FEATURES['colony'].label)),
        noun="mouse", nouns="mice",
        back_url=url_for("colony", view="mice"),
        columns_note=gettext("The mouse colony's columns are fixed, so any others go into each mouse's notes."),
        derived=("active", "age week", "age day", "age"),
        fields=[
            Field("mouse_id", "Mouse ID", ("mouse", "id", "mouse number", "ear tag", "tag", "animal id", "animal"),
                  note="Kept when it's a free number; otherwise the next ID, with yours in the notes"),
            Field("gender", "Sex", ("sex", "gender", "m f", "male female"), kind="sex"),
            Field("genotype", "Genotype", ("genotype", "strain", "line", "transgene", "allele", "cre", "gt",
                                           "transgene 1")),
            *(Field(f"transgene_{n}", f"Transgene {n}", (f"transgene {n}", f"allele {n}", f"tg {n}"))
              for n in (2, 3, 4)),
            Field("date_of_birth", "Date of birth", ("dob", "birth date", "birthdate", "born", "birthday",
                                                     "date born", "d o b", "birth"), kind="date"),
            Field("cage_id", "Cage", ("cage", "cage number", "cage id", "cage card")),
            Field("cage_rack", "Rack", ("rack", "rack name", "shelf")),
            Field("cage_position", "Position in the rack", ("position", "slot", "rack position", "cage position",
                                                            "pos"), kind="position"),
            Field("cage_location", "Cage location", ("room", "location", "cage location", "where"), kind="place"),
            Field("litter_id", "Litter", ("litter", "litter number", "litter id")),
            Field("status", "Status", ("status", "use", "purpose", "state"), kind="choice",
                  choices=_mouse_statuses(session)),
            Field("owner", "Owner", ("owner", "user", "person", "researcher", "responsible", "who", "investigator",
                                     "belongs to"), kind="owner", required=True, fill="me"),
            Field("date_of_death", "Date of death", ("death date", "date of death", "dod", "sac date",
                                                     "euthanized", "died"), kind="date"),
            Field("note", "Notes", ("note", "comment", "remark", "description", "notes")),
        ])


# -- Zebrafish -------------------------------------------------------------------

FISH_STATUSES = {"alive": "alive", "live": "alive", "active": "alive", "transfer": "transfer", "geno": "geno",
                 "genotyping": "geno", "sac": "sac", "sacrificed": "sac", "euthanized": "sac", "dead": "dead",
                 "died": "dead"}
FISH_SEXES = {**SEXES, "mixed": "mixed", "mix": "mixed", "both": "mixed", "m f": "mixed", "unknown": "unknown",
              "juvenile": "unknown", "larva": "unknown", "larvae": "unknown", "u": "unknown"}


class FishTarget(Target):
    def prepare(self, session):
        from .models import FishLine, FishRack, TankRecord
        return {"people": _people(session),
                "tanks": {t.tank_id.lower(): t for t in session.scalars(select(TankRecord))},
                "lines": {l.name.lower(): l for l in session.scalars(select(FishLine))},
                "racks": {r.name.lower(): r for r in session.scalars(select(FishRack))}}

    def create(self, session, ctx, v, extras):
        from .app import (ZfInputError, apply_fish_position, zf_apply_fish_status, zf_can_edit, zf_denied,
                          zf_fish_from_form)
        from .models import FishLine, FishRecord, TankRecord
        warnings: list[str] = []
        code = v.get("tank", "").strip()
        if not code:
            raise RowError(gettext("It has no tank."))
        owner = _owner(ctx, v.get("owner", ""), warnings, extras)
        line = None
        line_name = v.get("line", "").strip()
        if line_name:
            line = ctx["lines"].get(line_name.lower())
            if line is None:
                line = FishLine(name=line_name[:120], owner=owner)
                session.add(line)
                session.flush()
                ctx["lines"][line_name.lower()] = line
                warnings.append(gettext("There was no line “%(line)s”, so it's made new. If it's a spelling of one you have, change the name in the sheet (or merge them after).",
                                        line=line_name))
        tank = ctx["tanks"].get(code.lower())
        if tank is None:
            tank = TankRecord(tank_id=code[:80], owner=owner, purpose="stock", active=True,
                              line_id_fk=line.id if line else None)
            session.add(tank)
            session.flush()
            ctx["tanks"][code.lower()] = tank
            warnings.append(gettext("There was no tank %(tank)s, so it's made new.", tank=code))
            rack = ctx["racks"].get(v.get("rack", "").strip().lower())
            if v.get("rack", "").strip() and rack is None:
                warnings.append(gettext("There's no rack called “%(rack)s”, so tank %(tank)s isn't placed.",
                                        rack=v['rack'], tank=code))
            elif rack is not None:
                problem = apply_fish_position(session, tank, str(rack.id), v.get("position", ""))
                if problem:
                    warnings.append(problem)
        elif not zf_can_edit(tank):
            raise RowError(zf_denied(tank))
        fish = FishRecord(tank=tank, count=1, sex="mixed", status="alive")
        form = {k: v[k] for k in ("individual_id", "genotype", "count", "sex", "status", "date_of_fertilization")
                if k in v and v[k] != ""}
        form["notes"] = _extras_note(v.get("notes", ""), extras)
        if line is not None:
            form["line_id_fk"] = str(line.id)
        try:
            zf_fish_from_form(session, fish, form)
        except ZfInputError as error:
            raise RowError(str(error)) from error
        session.add(fish)
        zf_apply_fish_status(session, fish, "alive")
        session.flush()
        return gettext("%(count)s in %(tank)s", count=fish.count, tank=tank.tank_id), warnings


def fish_target(session) -> Target:
    return FishTarget(
        key="fish", title=gettext("Fish in %(db)s", db=translate_value(lab.FEATURES['zebrafish'].label)),
        noun="row of fish", nouns="rows of fish",
        back_url=url_for("zebrafish", view="fish"),
        columns_note=gettext("The zebrafish columns are fixed, so any others go into the fish's notes."),
        fields=[
            Field("tank", "Tank", ("tank", "tank number", "tank id", "aquarium"), required=True,
                  note="New tanks are made, placed from Rack and Position"),
            Field("line", "Line", ("line", "strain", "fish line", "transgenic line"), note="New lines are made"),
            Field("genotype", "Genotype", ("genotype", "allele", "transgene")),
            Field("count", "Count", ("count", "number", "n", "number of fish", "how many", "quantity"), kind="number"),
            Field("sex", "Sex", ("sex", "gender"), kind="choice", choices=FISH_SEXES),
            Field("status", "Status", ("status", "state"), kind="choice", choices=FISH_STATUSES),
            Field("date_of_fertilization", "Fertilised", ("dof", "fertilized", "date of fertilization",
                                                          "fertilisation date", "dob", "birth date", "born", "date"),
                  kind="date"),
            Field("individual_id", "Fish ID", ("fish id", "individual", "individual id", "tag", "id")),
            Field("rack", "Rack", ("rack", "system", "shelf")),
            Field("position", "Position", ("position", "slot", "pos"), kind="position"),
            Field("owner", "Owner", ("owner", "user", "person", "researcher", "responsible"), kind="owner",
                  required=True, fill="me"),
            Field("notes", "Notes", ("note", "comment", "remark", "description")),
        ])


# -- Plasmids --------------------------------------------------------------------

class PlasmidTarget(Target):
    def prepare(self, session):
        from .models import PlasmidRecord
        taken = set(session.scalars(select(PlasmidRecord.plasmid_id)))
        return {"people": _people(session), "taken": taken, "next": max(taken, default=0) + 1}

    def create(self, session, ctx, v, extras):
        from . import plasmid_service as pbox
        from .models import PlasmidRecord
        warnings: list[str] = []
        name = v.get("name", "").strip()
        if not name:
            raise RowError(gettext("It has no name."))
        typed = v.get("plasmid_id", "").strip().lstrip("#")
        if typed.isdigit() and int(typed) > 0 and int(typed) not in ctx["taken"]:
            number = int(typed)
        else:
            while ctx["next"] in ctx["taken"]:
                ctx["next"] += 1
            number = ctx["next"]
            if typed:
                extras.insert(0, ("Number in the spreadsheet", typed))
        ctx["taken"].add(number)
        p = PlasmidRecord(plasmid_id=number, name=name[:200], backbone=v.get("backbone", "")[:200],
                          insert_seq=v.get("insert_seq", "")[:200], resistance=v.get("resistance", "")[:80],
                          owner=_owner(ctx, v.get("owner", ""), warnings, extras),
                          location=v.get("location", "")[:120], is_shared=v.get("is_shared") == "1")
        for key, label, limit in (("concentration", "Concentration", 40), ("a260_280", "260/280", 20)):
            value = v.get(key, "").strip().replace(",", ".")
            try:
                float(value) if value else None
            except ValueError:
                extras.append((label, value))  # not a number: kept in the notes
            else:
                setattr(p, key, value[:limit])
        p.notes = _extras_note(v.get("notes", ""), extras)
        session.add(p)
        session.flush()
        box_name = v.get("box", "").strip()
        if box_name:
            box = pbox.box_for_name(session, box_name, g.user.username)
            row = col = None
            if v.get("position", "").strip():
                try:
                    row, col = pbox.parse(v["position"], box)
                except ValueError as error:
                    warnings.append(str(error))
            problem, _moved = pbox.place(session, p, box, row, col)
            if problem:
                warnings.append(problem)
                pbox.place(session, p, box, None, None)
        return f"#{number} {name[:40]}", warnings


def plasmid_target(session) -> Target:
    return PlasmidTarget(
        key="plasmids", title=translate_value(lab.FEATURES["plasmids"].label), noun="plasmid", nouns="plasmids",
        back_url=url_for("plasmids"),
        columns_note=gettext("The plasmid columns are fixed, so any others go into each plasmid's notes."),
        fields=[
            Field("plasmid_id", "Plasmid number", ("plasmid number", "plasmid id", "number", "id", "stock number",
                                                   "pl number", "p number")),
            Field("name", "Name", ("name", "plasmid", "plasmid name", "construct", "title"), required=True),
            Field("backbone", "Backbone", ("backbone", "vector", "parent vector", "parent")),
            Field("insert_seq", "Insert", ("insert", "gene", "cdna", "orf", "insert gene", "gene insert")),
            Field("resistance", "Resistance", ("resistance", "antibiotic", "selection", "marker",
                                               "bacterial resistance", "antibiotic resistance")),
            Field("box", "Box", ("box", "storage box", "freezer box", "plasmid box", "rack")),
            Field("position", "Position in the box", ("position", "well", "slot", "pos", "box position"),
                  kind="position"),
            Field("location", "Location", ("location", "freezer", "storage", "where", "fridge"), kind="place"),
            Field("concentration", "Concentration (ng/µL)", ("concentration", "conc", "conc.", "ng/ul", "ng/µl",
                                                            "yield", "dna concentration")),
            Field("a260_280", "260/280", ("260/280", "a260/280", "a260/a280", "purity")),
            Field("owner", "Owner", ("owner", "user", "person", "made by", "maker", "researcher", "depositor"),
                  kind="owner", required=True, fill="me"),
            shared_field(),
            Field("notes", "Notes", ("note", "comment", "remark", "description", "source", "reference")),
        ])


# -- Fly and worm stocks ------------------------------------------------------------

class StockTarget(Target):
    def prepare(self, session):
        from .models import StockRack
        from .stock_routes import _lab_users
        return {"people": _people(session), "users": set(_lab_users(session)),
                "racks": {r.name.lower(): r for r in session.scalars(
                    select(StockRack).where(StockRack.module_id_fk == self.module.id))}}

    def create(self, session, ctx, v, extras):
        from . import stock_service as svc
        from .models import StockGenotype, StockUnit
        from .stock_routes import Invalid, _unit_from_form
        mv = self.mv
        warnings: list[str] = []
        if not v.get("genotype", "").strip() and not (v.get("female_genotype") or v.get("male_genotype")):
            raise RowError(gettext("It has no genotype."))
        unit = StockUnit(module_id_fk=mv.id, number=svc.next_number(session, mv.id), owner=g.user.username,
                         purpose=mv.default_purpose)
        session.add(unit)
        session.flush()
        form = {k: v[k] for k in ("genotype", "female_genotype", "male_genotype", "set_up_on", "generation")
                if k in v}
        form["owner"] = _owner(ctx, v.get("owner", ""), warnings, extras)
        if v.get("purpose", "").strip():
            choices = {norm(p["key"]): p["key"] for p in mv.purposes}
            choices.update({norm(p.get("label", p["key"])): p["key"] for p in mv.purposes})
            purpose, known = tidy_choice(v["purpose"], choices)
            if known:
                form["purpose"] = purpose
            else:
                warnings.append(gettext("“%(purpose)s” isn't one of this database's purposes, so it's %(default)s.",
                                        purpose=v['purpose'],
                                        default=translate_value(mv.purpose_label(mv.default_purpose)).lower()))
                extras.append(("Purpose in the spreadsheet", v["purpose"]))
        form["notes"] = _extras_note(v.get("notes", ""), extras)
        rack_name = v.get("rack", "").strip()
        if rack_name:
            rack = ctx["racks"].get(rack_name.lower())
            if rack is None:
                warnings.append(gettext("There's no rack called “%(rack)s”, so it isn't placed.", rack=rack_name))
            else:
                form["rack_id"], form["position"] = str(rack.id), v.get("position", "")
        try:
            problem = _unit_from_form(session, mv, unit, form, placing=True, users=ctx["users"])
        except Invalid as error:
            raise RowError(str(error)) from error
        if problem:
            warnings.append(problem)
        stock_number = v.get("stock_number", "").strip()
        if stock_number and unit.genotype:
            session.flush()
            known = session.scalar(select(StockGenotype).where(StockGenotype.module_id_fk == mv.id,
                                                               StockGenotype.genotype == unit.genotype))
            if known is not None and not known.stock_number:
                known.stock_number = stock_number[:120]
        session.flush()
        return mv.code(unit), warnings


def stock_target(session, module) -> Target:
    from . import stock_service as svc
    mv = svc.view(module)
    unit, units = mv.unit, mv.units
    return StockTarget(
        key=f"stocks:{module.key}", title=gettext("%(things)s in %(db)s", things=translate_value(units.capitalize(), "import"),
                                                   db=translate_value(mv.label)), noun=unit, nouns=units,
        back_url=url_for("stocks.module", key=module.key), module=module, mv=mv,
        columns_note=gettext("The %(db)s columns are fixed, so any others go into each %(thing)s's notes.",
                             db=translate_value(mv.label), thing=translate_value(unit, "import")),
        fields=[
            Field("genotype", "Genotype", ("genotype", "stock", "strain", "line", "name", "stock name",
                                           "description"), required=True),
            Field("purpose", "Purpose", ("purpose", "type", "use", "category", "kind")),
            Field("female_genotype", "Female genotype", ("female", "virgin", "female genotype", "mother", "mom")),
            Field("male_genotype", "Male genotype", ("male", "male genotype", "father", "dad")),
            Field("stock_number", "Stock number", ("stock number", "bloomington", "bdsc", "vdrc", "cgc",
                                                   "stock center number", "bl", "bloomington number",
                                                   "bdsc number", "vdrc number", "cgc number", "stock id")),
            Field("set_up_on", "Set up", ("set up", "date", "date set up", "setup date", "started", "flipped",
                                          "last flip", "date flipped"), kind="date"),
            Field("generation", "Generation", ("generation", "gen", "f")),
            Field("rack", "Rack", ("rack", "box", "tray", "shelf")),
            Field("position", "Position", ("position", "slot", "pos", "rack position"), kind="position"),
            Field("owner", "Owner", ("owner", "user", "person", "researcher"), kind="owner", required=True,
                  fill="me"),
            Field("notes", "Notes", ("note", "comment", "remark", "phenotype")),
        ])


# -- Organism databases --------------------------------------------------------------

class OrganismTarget(Target):
    def prepare(self, session):
        from .models import OrgHousing, OrgLine, Organism
        mid = self.module.id
        return {"people": _people(session),
                "codes": {c.lower() for c in session.scalars(select(Organism.code).where(
                    Organism.module_id_fk == mid, Organism.code.is_not(None)))},
                "housing": {h.code.lower(): h for h in session.scalars(select(OrgHousing).where(
                    OrgHousing.module_id_fk == mid))},
                "lines": {k.lower(): l for l in session.scalars(select(OrgLine).where(OrgLine.module_id_fk == mid))
                          for k in filter(None, (l.code, l.name))}}

    def add_column(self, session, ctx, label, kind):
        from . import organism_service as svc
        made = svc.add_field(session, self.module, {"entity": "organism", "label": label, "key": label,
                                                    "field_type": {"number": "number", "date": "date"}.get(kind, "text"),
                                                    "show_in_table": True})
        session.flush()
        return f"attr_{made.key}" if made else ""

    def create(self, session, ctx, v, extras):
        from . import organism_service as svc
        from .models import OrgHousing, OrgLine, Organism
        from .organism_routes import _fill_animal
        module, mv = self.module, self.mv
        warnings: list[str] = []
        code = v.get("code", "").strip()
        if not code and mv.identity_mode == "individual":
            code = svc.next_code(session, module, "organism")
        if code and code.lower() in ctx["codes"]:
            raise RowError(gettext("%(code)s is already in %(db)s.", code=code, db=translate_value(mv.label)))
        form = {k: v[k] for k in v if k.startswith("attr_") or k in ("sex", "status", "genotype", "protocol",
                                                                     "birth_on", "death_on", "count")}
        form["owner"] = _owner(ctx, v.get("owner", ""), warnings, extras)
        form["notes"] = _extras_note(v.get("notes", ""), extras)
        housing_code = v.get("housing", "").strip()
        if housing_code:
            unit = ctx["housing"].get(housing_code.lower())
            if unit is None:
                unit = OrgHousing(module_id_fk=module.id, code=housing_code[:120], owner=form["owner"], active=True)
                session.add(unit)
                session.flush()
                ctx["housing"][housing_code.lower()] = unit
            form["housing_id_fk"] = str(unit.id)
        line_name = v.get("line", "").strip()
        if line_name:
            line = ctx["lines"].get(line_name.lower())
            if line is None:
                line = OrgLine(module_id_fk=module.id, code=line_name[:120], name=line_name[:200], owner=form["owner"])
                session.add(line)
                session.flush()
                ctx["lines"][line_name.lower()] = line
            form["line_id_fk"] = str(line.id)
        row = Organism(module_id_fk=module.id, code=code or None)
        session.add(row)
        error = _fill_animal(session, module, mv, row, form, 0)
        if error:
            raise RowError(error)
        session.flush()
        if code:
            ctx["codes"].add(code.lower())
        svc.log_event(session, module, "organism", row.id, "create", count=row.count,
                      recorded_by=g.user.username, notes="Imported from a spreadsheet")
        return code or f"{row.count} {translate_value(mv.organism_noun_plural)}", warnings

    def finish(self, session, ctx):
        from . import organism_service as svc
        svc.recompute_due(session, self.module)


def _sex_choices(sexes: list[str]) -> dict[str, str]:
    """Male, m, ♂… → the database's own word for male (and so on)."""
    out = {norm(s): s for s in sexes}
    for s in sexes:
        n = norm(s)
        if n in ("male", "m"):
            out.update({k: s for k, v in SEXES.items() if v == "M"})
        elif n in ("female", "f"):
            out.update({k: s for k, v in SEXES.items() if v == "F"})
    return out


def organism_target(session, module) -> Target:
    from . import organism_service as svc
    mv = svc.view(module)
    fields = [
        Field("code", "ID", ("id", "code", "animal id", "tag", "name", f"{mv.organism_noun} id")),
        Field("sex", "Sex", ("sex", "gender"), kind="choice", choices=_sex_choices(mv.sexes)),
        Field("status", "Status", ("status", "state"), kind="choice", choices={norm(s): s for s in mv.statuses}),
        Field("birth_on", "Born", ("dob", "birth date", "date of birth", "born", "hatched", "birthday"), kind="date"),
        Field("death_on", "Died", ("death date", "date of death", "died", "removed", "culled"), kind="date"),
        Field("genotype", "Genotype", ("genotype", "allele", "transgene")),
        Field("line", mv.line_noun.capitalize(), ("line", "strain", "stock", mv.line_noun)),
        Field("housing", mv.housing_noun.capitalize(), ("housing", "cage", "tank", "enclosure", "pen", "box",
                                                        mv.housing_noun, "location")),
        Field("count", "Count", ("count", "number", "n", "how many", "quantity"), kind="number"),
        Field("protocol", "Protocol", ("protocol", "iacuc", "license", "licence")),
        Field("owner", "Owner", ("owner", "user", "person", "researcher"), kind="owner", required=True, fill="me"),
        Field("notes", "Notes", ("note", "comment", "remark", "description")),
    ]
    for f in svc.fields_for(session, module.id, "organism"):
        kind = {"number": "number", "date": "date"}.get(f.field_type, "text")
        fields.append(Field(f"attr_{f.key}", f.label, (f.key.replace("_", " "),), kind=kind,
                            required=bool(f.required), custom=True))
    can_add = access.can_configure(module)
    return OrganismTarget(
        key=f"organisms:{module.key}", title=gettext("%(things)s in %(db)s",
                                                     things=translate_value(mv.organism_noun_plural.capitalize(), "import"),
                                                     db=translate_value(mv.label)),
        noun=mv.organism_noun, nouns=mv.organism_noun_plural, back_url=url_for("organisms.module", key=module.key),
        module=module, mv=mv, fields=fields, can_add_columns=can_add,
        columns_note="" if can_add else gettext("Only the person who configures this database can add columns, so any others go into the notes."))


# -- Inventories ----------------------------------------------------------------------

class InventoryTarget(Target):
    def prepare(self, session):
        from .models import InventoryRack
        return {"people": _people(session),
                "racks": {r.name.lower(): r for r in session.scalars(
                    select(InventoryRack).where(InventoryRack.module_id_fk == self.module.id))}}

    def add_column(self, session, ctx, label, kind):
        from . import inventory as presets
        from . import inventory_service as svc
        from .organism_service import slugify
        settings = presets.normalise_settings(self.module.settings)
        used = {f["key"] for f in settings["fields"]}
        base = slugify(label) or "column"
        key, n = base, 2
        while key in used:
            key, n = f"{base}_{n}", n + 1
        settings["fields"].append({"key": key, "label": label[:80], "type": kind if kind in ("number", "date") else "text",
                                   "options": [], "icon": "", "width": 130, "in_table": True})
        self.module.settings = json.dumps(settings)
        self.mv = svc.view(self.module)
        return f"attr_{key}"

    def create(self, session, ctx, v, extras):
        from . import inventory_service as svc
        from .inventory_routes import Refused, _item_from_form
        from .models import InventoryItem
        mv = self.mv
        warnings: list[str] = []
        item = InventoryItem(module_id_fk=mv.id, number=svc.next_number(session, mv.id), owner=g.user.username,
                             status=mv.statuses[0] if mv.statuses else "")
        session.add(item)
        session.flush()
        form = {k: v[k] for k in v if k.startswith("attr_") or k in (
            "name", "category", "quantity", "unit", "vendor", "catalog_number", "lot", "location_note",
            "received_on", "expires_on", "status")}
        if mv.has("sharing") and v.get("is_shared") in ("0", "1"):
            form["is_shared"] = v["is_shared"]
        form["owner"] = _owner(ctx, v.get("owner", ""), warnings, extras)
        form["notes"] = _extras_note(v.get("notes", ""), extras)
        if "status" in form and form["status"].strip():
            matched = svc.match_status(mv, form["status"])
            if matched:
                form["status"] = matched
            else:
                warnings.append(gettext("“%(status)s” isn't one of the statuses, so it's %(default)s.",
                                        status=form['status'], default=translate_value(mv.statuses[0])))
                extras.append(("Status in the spreadsheet", form["status"]))
                form["status"] = mv.statuses[0]
                form["notes"] = _extras_note(v.get("notes", ""), extras)
        elif "status" in form:
            del form["status"]
        rack_name = v.get("rack", "").strip()
        if rack_name and mv.has("storage"):
            rack = ctx["racks"].get(rack_name.lower())
            if rack is None:
                warnings.append(gettext("There's no box called “%(box)s”, so it isn't placed.", box=rack_name))
                form["location_note"] = " · ".join(filter(None, [form.get("location_note", ""), rack_name,
                                                                 v.get("position", "")]))
            else:
                form["rack_id"], form["position"] = str(rack.id), v.get("position", "")
        try:
            problem, notes = _item_from_form(session, mv, item, form, creating=True)
        except Refused as error:
            raise RowError(str(error)) from error
        warnings += notes
        if problem:
            warnings.append(problem)
        session.flush()
        return f"#{item.number} {(item.name or '')[:40]}", warnings


# Personal or lab common, however a sheet says it; the import page offers it
# for every row when the sheet doesn't.
SHARED_CHOICES = {"lab common": "1", "lab": "1", "common": "1", "shared": "1", "yes": "1", "y": "1", "true": "1",
                  "1": "1", "personal": "0", "mine": "0", "private": "0", "own": "0", "no": "0", "n": "0",
                  "false": "0", "0": "0"}


def shared_field() -> Field:
    return Field("is_shared", "Belongs to", ("belongs to", "lab common", "common", "shared", "lab stock",
                                             "personal or lab"),
                 kind="choice", choices=SHARED_CHOICES, required=True, fill="0",
                 options=(("0", "Personal (its owner's)"), ("1", "Lab common: anyone can edit")))


# Other names a lab's sheet uses for a preset's own columns (inventory.py
# PRESETS fields), by the column's key.
ATTR_ALIASES = {
    "serotype": ("serotype", "capsid", "pseudotype", "envelope", "coat"),
    "plasmid": ("plasmid", "made from", "transfer plasmid", "plasmid id", "plasmid number", "addgene plasmid",
                "source plasmid"),
    "promoter": ("promoter", "driver"),
    "payload": ("payload", "transgene", "insert", "gene", "cargo", "expresses"),
    "titer": ("titer", "titre", "gc/ml", "vg/ml", "tu/ml", "ifu/ml", "pfu/ml", "titer (gc/ml)", "titre (vg/ml)"),
    "biosafety": ("biosafety", "bsl", "biosafety level", "containment", "safety level"),
    "made_on": ("made", "date made", "produced", "production date", "prep date", "packaged", "made on"),
    "price": ("price", "cost", "unit price", "amount paid", "total cost"),
    "account": ("account", "grant", "fund", "funding", "cost center", "cost centre", "po", "budget"),
    "url": ("url", "link", "web", "website", "product page"),
}


def inventory_target(session, module) -> Target:
    from . import inventory_service as svc
    from .inventory_routes import _can_configure
    mv = svc.view(module)
    required = set(mv.required)
    fields = [
        Field("name", mv.name_label, ("name", "item", "item name", "product", "product name", "reagent", "antibody", "what",
                               "chemical", "sample", "sample name", "title", "compound", "target", "virus", "virus name",
                               "construct"),
              required="name" in required or mv.row.kind == "orders"),
        Field("category", mv.category_label, ("category", "type", "kind", "class", "group", "vector type", "virus type")),
        Field("status", "Status", ("status", "state", "stage")),
        Field("quantity", "Quantity", ("quantity", "qty", "amount", "volume", "count", "number of", "stock")),
        Field("unit", "Unit", ("unit", "units", "uom", "size")),
        Field("vendor", "Vendor", ("vendor", "supplier", "company", "manufacturer", "brand", "made by", "from")),
        Field("catalog_number", "Catalog number", ("catalog", "catalog number", "catalogue number", "cat",
                                                   "cat number", "product number", "part number", "sku", "ref",
                                                   "reference", "item number", "order number")),
        Field("lot", "Lot", ("lot", "lot number", "batch", "batch number")),
        Field("rack", "Box", ("box", "rack", "freezer box", "storage box", "shelf", "tray")),
        Field("position", "Position in the box", ("position", "well", "slot", "pos", "box position"),
              kind="position"),
        Field("location_note", "Location", ("location", "freezer", "fridge", "storage", "where", "stored",
                                            "storage location", "room"), kind="place"),
        Field("received_on", "Received", ("received", "date received", "arrived", "arrival", "delivered",
                                          "received date"), kind="date"),
        Field("expires_on", "Expires", ("expiry", "expiration", "expires", "exp", "exp date", "expiration date",
                                        "expiry date", "use by", "best before"), kind="date"),
        Field("owner", "Owner", ("owner", "user", "person", "requested by", "requester", "researcher", "ordered by"),
              kind="owner", required=True, fill="me"),
        Field("notes", "Notes", ("note", "comment", "remark", "description")),
    ]
    if mv.has("sharing"):
        fields.insert(-1, shared_field())
    needs = {"vendor": "supplier", "catalog_number": "supplier", "lot": "supplier", "quantity": "quantity",
             "unit": "quantity", "rack": "storage", "position": "storage", "received_on": "received",
             "expires_on": "expiry"}
    fields = [f for f in fields if f.key not in needs or mv.has(needs[f.key])]
    if not mv.statuses:
        fields = [f for f in fields if f.key != "status"]
    for f in mv.fields:
        if f["type"] == "source":
            continue
        kind = {"number": "number", "date": "date"}.get(f["type"], "text")
        names = (f["key"].replace("_", " "), *ATTR_ALIASES.get(f["key"], ()))
        fields.append(Field(f"attr_{f['key']}", f["label"], names, kind=kind,
                            required=f"attr_{f['key']}" in required, custom=True))
    for f in fields:
        if f.key in required:
            f.required = True
    can_add = _can_configure(module)
    return InventoryTarget(
        key=f"inventory:{module.key}", title=translate_value(mv.label), noun=mv.item_noun, nouns=mv.item_noun_plural,
        back_url=url_for("inventory.module", key=module.key), module=module, mv=mv, fields=fields,
        can_add_columns=can_add,
        columns_note="" if can_add else gettext("Only the person who configures this inventory can add columns, so any others go into the notes."))


def target_for(session, target_key: str) -> Target:
    """The import target named in the URL; 404 when it isn't there or isn't
    open to this person."""
    from . import inventory_service, organism_service, stock_service
    kind, _, key = target_key.partition(":")
    features = lab.features_on(session)
    if kind == "mice" and features.get("colony", True):
        return mice_target(session)
    if kind == "fish" and features.get("zebrafish", True):
        return fish_target(session)
    if kind == "plasmids" and features.get("plasmids", True):
        return plasmid_target(session)
    module = None
    if kind == "stocks":
        module = stock_service.get_module(session, key)
        make = stock_target
    elif kind == "organisms":
        module = organism_service.get_module(session, key)
        make = organism_target
    elif kind == "inventory":
        module = inventory_service.get_module(session, key)
        make = inventory_target
    if module is None or not lab.can_see(module):
        abort(404)
    return make(session, module)


# ---------------------------------------------------------------- the run

@dataclass
class Plan:
    """What the match page decided."""
    mapping: dict[int, str]              # column → field key, "_new", "_notes" or "_skip"
    fills: dict[str, str]                # field key → value for every row
    new_kinds: dict[int, str] = field(default_factory=dict)


def plan_from_form(form, headers: list[str]) -> Plan:
    mapping, kinds, fills = {}, {}, {}
    for i in range(len(headers)):
        mapping[i] = form.get(f"map-{i}", "_skip")
        kinds[i] = form.get(f"kind-{i}", "text")
    for name, value in form.items():
        if name.startswith("fill-") and value.strip():
            fills[name[5:]] = value.strip()
    return Plan(mapping, fills, kinds)


def run(target: Target, headers: list[str], rows: list[list[str]], plan: Plan, commit: bool,
        filename: str, numbers: list[int] | None = None) -> dict:
    """Create every row through the database's own code; commit, or roll
    everything back (the preview). Returns what happened."""
    fields = {f.key: f for f in target.fields}
    used = {k for k in plan.mapping.values() if k in fields}
    for f in target.fields:
        if f.required and f.fill and f.key not in used:
            plan.fills.setdefault(f.key, f.fill)          # the owner: you
    missing = [f for f in target.fields if f.required and f.key not in used and not plan.fills.get(f.key)]
    if missing:
        raise ImportProblem(gettext("Match a column, or give a value for every row, for: %(columns)s.",
                                    columns=", ".join(translate_value(f.label, "import") for f in missing)))
    where = numbers or [n + 2 for n in range(len(rows))]
    # A sheet's own summary line ("TOTAL", "Grand total") is not a record.
    totals = [n for n, r in zip(where, rows) if is_total(r)]
    if totals:
        kept = [(n, r) for n, r in zip(where, rows) if not is_total(r)]
        where, rows = [n for n, _r in kept], [r for _n, r in kept]
    # Each mapped column, tidied as a whole (dates are read per column).
    columns = {i: [r[i] if i < len(r) else "" for r in rows] for i in range(len(headers))}
    tidy_notes: list[str] = []
    with SessionLocal() as session:
        day_first = lab.date_style(session) == "day"
    unread: dict[int, dict[int, str]] = {}          # date column -> row -> what the sheet had
    for i, key in plan.mapping.items():
        f = fields.get(key)
        if f and f.kind == "date":
            raw = columns[i]
            columns[i], notes = tidy_dates(raw, day_first)
            tidy_notes += [gettext("%(column)s: %(note)s", column=headers[i], note=n) for n in notes]
            unread[i] = {n: v.strip() for n, v in enumerate(raw) if v.strip() and not columns[i][n]}
    sheet_values = {key: {v.strip() for v in columns[i] if v.strip()}
                    for i, key in plan.mapping.items() if key in fields}
    unknown_choices: dict[str, set[str]] = {}
    tidy_notes += [gettext("Row %(row)s looks like the sheet's total, so it's left out.", row=n) for n in totals]
    results = {"created": [], "problems": [], "warnings": [], "added_columns": [], "tidied": tidy_notes,
               "total": len(rows)}
    with SessionLocal() as session:
        target = target_for(session, target.key)      # its module, read in this session
        _open_transaction(session)
        carry: dict = {}                               # what outlives a failed row's ctx
        ctx = {**target.prepare(session), "sheet": sheet_values, "carry": carry}
        new_columns: dict[int, str] = {}
        if target.can_add_columns:
            for i, key in plan.mapping.items():
                if key == "_new":
                    shape = _shape(columns[i])
                    kind = plan.new_kinds.get(i) or ("date" if shape == "date" else "number" if shape == "number" else "text")
                    made = target.add_column(session, ctx, headers[i], kind)
                    if made:
                        new_columns[i] = made
                        results["added_columns"].append(headers[i])
                        if kind == "date":
                            columns[i], _notes = tidy_dates(columns[i], day_first)
        batch_cm = audit.batch(session, "create", f"import {len(rows)} {target.nouns} from {filename}"[:200],
                               target.key.split(":")[0]) if commit else None
        batch_row = batch_cm.__enter__() if batch_cm else None
        try:
            for n, _row in enumerate(rows):
                values: dict[str, str] = {k: v for k, v in plan.fills.items() if k in fields and k not in used}
                if values.get("owner") == "me":
                    values["owner"] = g.user.username
                extras: list[tuple[str, str]] = []
                for i, key in plan.mapping.items():
                    value = columns[i][n].strip()
                    if key in fields:
                        f = fields[key]
                        if f.kind == "sex":
                            value, _known = tidy_choice(value, SEXES) if value else ("", True)
                        elif f.kind == "choice" and f.choices and value:
                            value, known = tidy_choice(value, f.choices)
                            if not known:
                                unknown_choices.setdefault(f.label, set()).add(value)
                        elif f.kind == "number":
                            value = value.replace(",", "")
                        values[key] = value
                    elif key == "_new" and i in new_columns:
                        values[new_columns[i]] = value
                    elif key in ("_notes", "_new") and value:
                        extras.append((headers[i], value))
                    if n in unread.get(i, {}):
                        extras.append((headers[i], unread[i][n]))    # not a date: kept as the sheet had it
                savepoint = session.begin_nested()
                try:
                    label, warnings = target.create(session, ctx, values, extras)
                    savepoint.commit()
                    results["created"].append(label)
                    results["warnings"] += [gettext("Row %(row)s (%(label)s): %(warning)s", row=where[n], label=label,
                                                    warning=w) for w in warnings]
                except (RowError, Exception) as error:   # one bad row doesn't stop the rest
                    savepoint.rollback()
                    ctx = {**target.prepare(session), "sheet": sheet_values, "carry": carry}  # forget what the row made
                    message = str(error) if isinstance(error, RowError) else _plain(error)
                    results["problems"].append(gettext("Row %(row)s: %(problem)s", row=where[n], problem=message))
            if results["created"]:
                target.finish(session, ctx)
            if batch_row is not None:
                batch_row.record_count = len(results["created"])
        finally:
            if batch_cm is not None:
                batch_cm.__exit__(None, None, None)
        if commit and results["created"]:
            session.commit()
        else:
            session.rollback()
    for label, values in unknown_choices.items():
        shown = ", ".join(sorted(values)[:6])
        results["warnings"].insert(0, gettext("%(column)s: %(values)s kept as typed (not one of BioManager's names for it).", column=translate_value(label, "import"), values=shown))
    return results


def _open_transaction(session) -> None:
    """pysqlite starts a transaction only at the first write, so a SAVEPOINT
    before one would itself be the transaction, and releasing it would
    commit the row: the preview would write. A write that touches nothing
    starts it."""
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("DELETE FROM batches WHERE 1 = 0"))


def _plain(error: Exception) -> str:
    text = str(error).split("\n")[0]
    if "UNIQUE" in text or "unique" in text or "duplicate key" in text:
        return gettext("It would be a duplicate of a record that's already there.")
    return text[:200] or error.__class__.__name__


# ---------------------------------------------------------------- the stored upload

def _dir() -> Path:
    path = data_dir() / "imports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save(payload: dict) -> str:
    token = secrets.token_urlsafe(16)
    for old in _dir().glob("*.json"):
        try:
            if time.time() - old.stat().st_mtime > KEEP_SECONDS:
                old.unlink()
        except OSError:
            pass
    (_dir() / f"{token}.json").write_text(json.dumps(payload), encoding="utf-8")
    return token


def _load(token: str) -> dict:
    if not re.match(r"^[A-Za-z0-9_-]{10,40}$", token or ""):
        abort(404)
    try:
        payload = json.loads((_dir() / f"{token}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Gone() from None
    if payload.get("user") != g.user.username:
        abort(404)
    return payload


# ---------------------------------------------------------------- pages

@bp.route("/<target_key>")
def start(target_key: str):
    with SessionLocal() as session:
        target = target_for(session, target_key)
        return render_template("sheet_import.html", stage="upload", target=target, target_key=target_key)


@bp.route("/<target_key>/upload", methods=["POST"])
def upload(target_key: str):
    with SessionLocal() as session:
        target = target_for(session, target_key)
    file = request.files.get("file")
    if file is None or not file.filename:
        flash(gettext("Choose a file to import."), "error")
        return redirect(url_for("sheet_import.start", target_key=target_key))
    try:
        sheets = read_workbook(file.filename, file.read())
    except ImportProblem as problem:
        flash(str(problem), "error")
        return redirect(url_for("sheet_import.start", target_key=target_key))
    if not sheets:
        flash(gettext("That file has nothing in it to import."), "error")
        return redirect(url_for("sheet_import.start", target_key=target_key))
    token = _save({"user": g.user.username, "target": target_key, "filename": file.filename[:120],
                   "sheets": sheets})
    return redirect(url_for("sheet_import.match", token=token))


def _sheet(payload: dict, name: str | None) -> tuple[str, list[str], list[list[str]], list[int]]:
    """(sheet, headers, its filled rows, their row numbers in the sheet)."""
    sheets = payload["sheets"]
    name = name if name in sheets else next(iter(sheets))
    headers, data, first = split_header(sheets[name])
    kept = [(first + i, r) for i, r in enumerate(data) if any(c.strip() for c in r)][:MAX_ROWS]
    return name, headers, [r for _n, r in kept], [n for n, _r in kept]


@bp.route("/file/<token>")
def match(token: str):
    from .services import current_lab_usernames
    payload = _load(token)
    with SessionLocal() as session:
        target = target_for(session, payload["target"])
        sheet, headers, rows, _numbers = _sheet(payload, request.args.get("sheet"))
        columns = [[r[i] if i < len(r) else "" for r in rows] for i in range(len(headers))]
        matched = auto_match(headers, columns, target.fields)
        suggestions = []
        for i, header in enumerate(headers):
            key, strength, why = matched.get(i, ("", 0.0, ""))
            samples = [v for v in columns[i] if v.strip()][:3]
            if not key and norm(header) in target.derived:
                key, why = "_skip", gettext("worked out from the other columns")
            if not key:
                key = "_new" if target.can_add_columns and samples else ("_notes" if samples else "_skip")
            shape = _shape(columns[i])
            suggestions.append({"index": i, "header": header, "samples": samples, "key": key, "why": why,
                                "strength": strength, "kind": shape if shape in ("date", "number") else "text"})
        return render_template("sheet_import.html", stage="match", target=target, target_key=payload["target"],
                               token=token, payload=payload, sheet=sheet, headers=headers, rows=rows,
                               suggestions=suggestions, sheet_names=list(payload["sheets"]),
                               usernames=sorted(set(current_lab_usernames(session))))


@bp.route("/file/<token>/preview", methods=["POST"])
def preview(token: str):
    return _go(token, commit=False)


@bp.route("/file/<token>/run", methods=["POST"])
def run_import(token: str):
    return _go(token, commit=True)


def _go(token: str, commit: bool):
    payload = _load(token)
    with SessionLocal() as session:
        target = target_for(session, payload["target"])
    sheet, headers, rows, numbers = _sheet(payload, request.form.get("sheet"))
    plan = plan_from_form(request.form, headers)
    try:
        results = run(target, headers, rows, plan, commit, payload["filename"], numbers)
    except ImportProblem as problem:
        flash(str(problem), "error")
        return redirect(url_for("sheet_import.match", token=token, sheet=sheet))
    if commit:
        if results["created"]:
            try:
                (_dir() / f"{token}.json").unlink()
            except OSError:
                pass
            n = len(results["problems"])
            made = len(results["created"])
            skipped = (" " + ngettext("%(num)s row was skipped.", "%(num)s rows were skipped.", n)) if n else ""
            said = gettext("Imported %(count)s %(things)s from %(file)s.", count=made,
                           things=translate_value(target.nouns if made != 1 else target.noun, "import"),
                           file=payload["filename"])
            flash(Markup.escape(said + skipped) + Markup(' <a href="%s">%s</a>') % (url_for("batches_view"),
                                                                                    gettext("Undo")), "success")
            return redirect(target.back_url)
        flash(gettext("Nothing was imported: every row had a problem."), "error")
    labels = {f.key: f.label for f in target.fields}
    # A fill chosen from a list reads as the list said it ("Lab common", not "1").
    fill_words = {f.key: dict(f.options) for f in target.fields if f.options}
    return render_template("sheet_import.html", stage="preview", fill_words=fill_words, target=target, target_key=payload["target"],
                           token=token, payload=payload, sheet=sheet, headers=headers, results=results, plan=plan,
                           labels=labels, form=request.form)


def import_url(target_key: str) -> str:
    return url_for("sheet_import.start", target_key=target_key)


@bp.app_context_processor
def inject():
    return {"sheet_import_url": import_url}
