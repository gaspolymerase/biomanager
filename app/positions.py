"""How a rack names its positions, and turning names into cells and back.

Labs label racks differently: "D7" (row letter, column number), "4-7",
"7D", "G12" on a 96-well-style box, or plain numbering 1…80 along the rows.
Each rack carries a naming scheme, and every place a position is typed or
shown goes through `label()` and `parse()` here, so the mouse sheet, the
dialogs, the tables and the rack grid always agree. rack-grid.js mirrors
`label()` for the grid headers.

Rows and columns are 1-based everywhere in this module.
"""

from __future__ import annotations

import functools
import json
import re

ROW_STYLES = ("letters", "numbers")
COL_STYLES = ("numbers", "letters")
ORDERS = ("row_col", "col_row")
SEPARATORS = ("", "-", ".", ":", "/")
MODES = ("grid", "sequential")

DEFAULT = {
    "mode": "grid",        # grid: a row label and a column label; sequential: 1…rows×cols
    "rows": "letters",     # how rows are labelled
    "cols": "numbers",     # how columns are labelled
    "order": "row_col",    # "D7" (row first) or "7D" (column first)
    "separator": "",       # between the two labels: "D7", "D-7", "D.7"
    "start": 1,            # first number: 1, or 0 for zero-based labs
}

SEPARATOR_NAMES = {"": "none", "-": "hyphen", ".": "dot", ":": "colon", "/": "slash"}


def scheme(raw) -> dict:
    """A complete, valid scheme from whatever is stored (dict, JSON, None)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            raw = {}
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT)
    if raw.get("mode") in MODES:
        out["mode"] = raw["mode"]
    if raw.get("rows") in ROW_STYLES:
        out["rows"] = raw["rows"]
    if raw.get("cols") in COL_STYLES:
        out["cols"] = raw["cols"]
    if raw.get("order") in ORDERS:
        out["order"] = raw["order"]
    if raw.get("separator") in SEPARATORS:
        out["separator"] = raw["separator"]
    if str(raw.get("start")) in ("0", "1"):
        out["start"] = int(raw["start"])
    # "4" + "7" with nothing between reads back as 47: two labels of the same
    # kind need a separator to stay unambiguous.
    if out["mode"] == "grid" and out["rows"] == out["cols"] and not out["separator"]:
        out["separator"] = "-"
    return out


def scheme_from_form(form) -> dict:
    return scheme({
        "mode": form.get("naming_mode"),
        "rows": form.get("naming_rows"),
        "cols": form.get("naming_cols"),
        "order": form.get("naming_order"),
        "separator": form.get("naming_separator"),
        "start": form.get("naming_start"),
    })


def _letters(n: int) -> str:
    """1 → A, 26 → Z, 27 → AA (spreadsheet style)."""
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _from_letters(text: str) -> int:
    n = 0
    for ch in text.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def axis_label(n: int, style: str, start: int) -> str:
    """Label for row / column n (1-based) in the given style."""
    return _letters(n) if style == "letters" else str(n - 1 + start)


@functools.lru_cache(maxsize=64)
def _stored_scheme(text: str) -> dict:
    """scheme() of a rack's stored JSON, read once: a sheet names thousands
    of places in a few racks. Shared, so read it, never change it."""
    return scheme(text)


def label(row: int | None, col: int | None, raw_scheme, cols: int) -> str:
    """The name of a cell, e.g. "D7"; empty when not placed."""
    if not row or not col:
        return ""
    s = _stored_scheme(raw_scheme) if isinstance(raw_scheme, str) else scheme(raw_scheme)
    if s["mode"] == "sequential":
        return str((row - 1) * cols + col - 1 + s["start"])
    r = axis_label(row, s["rows"], s["start"])
    c = axis_label(col, s["cols"], s["start"])
    return f"{r}{s['separator']}{c}" if s["order"] == "row_col" else f"{c}{s['separator']}{r}"


def parse(text: str, raw_scheme, rows: int, cols: int) -> tuple[int, int] | None:
    """"D7" → (4, 7) under the rack's scheme; None if it is not a cell of
    this rack. Forgiving about case, spaces and the separator actually
    typed, so "d 7", "D-7" and "D7" all work on a "D7" rack."""
    text = (text or "").strip()
    if not text:
        return None
    s = scheme(raw_scheme)
    if s["mode"] == "sequential":
        if not text.isdigit():
            return None
        index = int(text) - s["start"]
        if not (0 <= index < rows * cols):
            return None
        return index // cols + 1, index % cols + 1

    if text.isdigit():
        # A plain number on a lettered box: the nth place counting along the
        # rows (box 1…81), as sheets that mix "A1" and "3" mean it.
        index = int(text) - 1
        return (index // cols + 1, index % cols + 1) if 0 <= index < rows * cols else None

    first, second = (s["rows"], s["cols"]) if s["order"] == "row_col" else (s["cols"], s["rows"])
    token = {"letters": r"([A-Za-z]{1,2})", "numbers": r"(\d{1,3})"}
    # Two letter-groups or two number-groups need a separator to split.
    sep = r"\s*[-.:/ ]\s*" if first == second else r"\s*[-.:/ ]?\s*"
    match = re.fullmatch(token[first] + sep + token[second], text)
    if not match:
        return None

    def value(part: str, style: str) -> int:
        return _from_letters(part) if style == "letters" else int(part) - s["start"] + 1

    a, b = value(match.group(1), first), value(match.group(2), second)
    row, col = (a, b) if s["order"] == "row_col" else (b, a)
    if not (1 <= row <= rows and 1 <= col <= cols):
        return None
    return row, col


def example(raw_scheme, rows: int = 8, cols: int = 10) -> str:
    """A worked example for the settings UI: first, a middle cell, last."""
    return " · ".join(label(r, c, raw_scheme, cols) for r, c in ((1, 1), (min(4, rows), min(7, cols)), (rows, cols)))


# ---------------------------------------------------------------- lists of boxes

_DASHES = str.maketrans({"−": "-", "‒": "-", "–": "-", "—": "-", " ": " "})
_PLACE_PARTS = re.compile(r"(?:^|(?<=\s))-\d+(?:\.\d+)?|\d+(?:\.\d+)?|\D+")


_DIGIT_RUNS = re.compile(r"([0-9]+)")


def natural(value) -> tuple:
    """A sort key for what people number (cages, tanks, racks, codes):
    digits as numbers, the rest without case, so 2 comes before 10, A2
    before A10 and C-99 before C-104 (as text, 10 and 101 came before 2)."""
    text = str(value if value is not None else "").strip().casefold()
    parts = _DIGIT_RUNS.split(text)
    return tuple((0, int(p), "") if i % 2 else (1, 0, p) for i, p in enumerate(parts) if p), text


def natural_sorted(rows, key=lambda row: row) -> list:
    """`rows` in natural order of `key` (a name, or a tuple of them)."""
    def order(row):
        value = key(row)
        return tuple(map(natural, value)) if isinstance(value, tuple) else natural(value)
    return sorted(rows, key=order)


def place_order(*names: str) -> tuple:
    """A sort key for freezers, boxes and racks by their names: numbers as
    numbers, a leading minus as a sign, whichever dash was typed. "−80 A",
    "-20 B", "4 °C", "Box 2", "Box 10" sort as −80, −20, 4, then Box 2 before
    Box 10; before, a hyphen and a minus sign split the −20s from the −80s."""
    key = []
    for name in names:
        parts = _PLACE_PARTS.findall((name or "").translate(_DASHES).strip().lower())
        key.append(tuple((0, float(p), "") if re.fullmatch(r"-?\d+(?:\.\d+)?", p) else (1, 0.0, p) for p in parts))
    return tuple(key)
