"""What each cell of a sheet's rows shows, read from their HTML as
data-table.js's _cellText reads a drawn row: a cell drawn as text
(static/text-cells.js) gives its text (a date its value, an empty field's
placeholder nothing), else its first field gives its value (a menu its
chosen option's text), else the cell its text, spaces closed up.

An export of a sheet the server pages asks for its rows this way
(/inventory/<key>/rows?as=text): the rows drawn by their own template,
only their words sent, not megabytes of rows for the browser to build.
The rows are this app's own markup (one line, no cell inside a cell), so
patterns read it; tests/test_cell_text.py holds them to the browser's
reading of the same rows."""
from __future__ import annotations

import html
import re

from flask import request

_ROW = re.compile(r'<tr\b[^>]*?\bdata-id="(\d+)"[^>]*>(.*?)</tr>', re.S)
_CELL = re.compile(r"<td\b[^>]*>(.*?)</td>", re.S)
_ATTR = re.compile(r'([\w:-]+)(?:="([^"]*)")?')
_FIELD = re.compile(r"<(select|textarea|input)\b([^>]*)>", re.S)
_OPTION = re.compile(r"<option\b([^>]*)>(.*?)</option>", re.S)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_HEAD = re.compile(r'^<span\b([^>]*)>(.*?)</span>$', re.S)
_SKIPPED = ("checkbox", "hidden", "radio")


def _attrs(head: str) -> dict[str, str]:
    return {name: html.unescape(value or "") for name, value in _ATTR.findall(head)}


def _text(markup: str) -> str:
    return html.unescape(_TAG.sub("", markup))


def cell_text(inner: str) -> str:
    """One cell's words, as _cellText gives them."""
    drawn = _HEAD.match(inner)
    if drawn:
        attrs = _attrs(drawn.group(1))
        classes = attrs.get("class", "").split()
        if "cell-text" in classes:
            if "is-placeholder" in classes:
                return ""
            if "is-date" in classes:
                return attrs.get("data-sort-value", "")
            return _text(drawn.group(2)).strip()
    for found in _FIELD.finditer(inner):
        tag, attrs = found.group(1), _attrs(found.group(2))
        if tag == "input":
            if attrs.get("type", "").lower() in _SKIPPED:
                continue
            return attrs.get("value", "").strip()
        end = inner.find(f"</{tag}>", found.end())
        body = inner[found.end():end if end >= 0 else len(inner)]
        if tag == "textarea":
            return html.unescape(body).strip()
        options = _OPTION.findall(body)
        if not options:
            return ""
        chosen = next((text for head, text in options if "selected" in _attrs(head)), options[0][1])
        return _text(chosen).strip()
    return _SPACE.sub(" ", _text(inner)).strip()


def row_texts(rows_html: str) -> dict[int, list[str]]:
    """{item id: each of its row's cells' words, in the row's order}."""
    return {int(row_id): [cell_text(inner) for inner in _CELL.findall(body)]
            for row_id, body in _ROW.findall(rows_html)}


def text_cells_on() -> bool:
    """Whether a sheet's cells are drawn as text until used (app.py,
    as_text_cells; static/text-cells.js). Chromium (Chrome, Edge, the
    Windows app, Android) reads every field on a page as it loads, so there
    a sheet of text opens a fifth to a third sooner. Safari and the Mac app
    keep their fields: they never had that wait. ?cells=text or
    ?cells=fields chooses either in any browser."""
    choice = request.args.get("cells")
    if choice in ("text", "fields"):
        return choice == "text"
    return "Chrome/" in request.headers.get("User-Agent", "")


# ---- A long sheet's rows as stubs ------------------------------------------
# A sheet the server pages sends its first page's rows and, for each other
# row, an entry data-table.js keeps in its place (makeStub) until the row
# is fetched: {id, e: its element id, f: its row form, d: its data-*, k: what
# each named cell holds, s: the words a search looks in}. Read here from
# the rows as drawn, as data-table.js reads a drawn row (_sortValue,
# _rowText), so a stub sorts and is found as its row would be.

_ROW_FULL = re.compile(r"<tr\b([^>]*)>(.*?)</tr>", re.S)
_START = re.compile(r"<(\w+)\b([^>]*)>", re.S)
_TEMPLATE = re.compile(r"<template\b.*?</template>", re.S)
_OPTION_TEXT = re.compile(r"<option\b[^>]*>.*?</option>", re.S)
_DATA_NAME = re.compile(r"-([a-z])")


def _dataset_key(attr: str) -> str:
    """data-row-form -> rowForm, as the browser's dataset names it."""
    return _DATA_NAME.sub(lambda m: m.group(1).upper(), attr[5:])


def _field_value(tag: str, attrs: dict, body: str, end: int) -> tuple[str, str]:
    """(value, shown) of a field whose start tag ends at `end` in body."""
    if tag == "input":
        if attrs.get("type", "").lower() == "checkbox" and "value" not in attrs:
            return "on", ""
        return attrs.get("value", ""), ""
    close = body.find(f"</{tag}>", end)
    inner = body[end:close if close >= 0 else len(body)]
    if tag == "textarea":
        return html.unescape(inner), ""
    if tag == "select":
        options = _OPTION.findall(inner)
        if not options:
            return "", ""
        head, text = next(((h, t) for h, t in options if "selected" in _attrs(h)), options[0])
        value = _attrs(head).get("value", _text(text))
        return value, _text(text).strip()
    return "", ""


def row_stub(row_attrs: str, body: str) -> dict:
    attrs = _attrs(row_attrs)
    data = {_dataset_key(k): v for k, v in attrs.items() if k.startswith("data-")}
    body = _TEMPLATE.sub("", body)
    sort: dict[str, str] = {}
    fields: list[tuple[str, str]] = []
    texts: list[str] = []
    form = ""
    for found in _START.finditer(body):
        tag, head = found.group(1).lower(), _attrs(found.group(2))
        if not form:
            form = head.get("form") or head.get("data-form") or ""
        if "name" in head and head["name"] not in sort:
            sort[head["name"]] = _field_value(tag, head, body, found.end())[0] if tag in ("input", "select", "textarea") else ""
        is_field = tag in ("select", "textarea") or (tag == "input" and head.get("type", "").lower() not in ("checkbox", "hidden"))
        if is_field:
            fields.append(_field_value(tag, head, body, found.end()))
        if "data-sort-for" in head:
            key = head["data-sort-for"]
            if key not in sort:
                sort[key] = head.get("data-sort-value", "")
            if "cell-text" in head.get("class", "").split() and "data-sort-value" in head:
                texts.append(head["data-sort-value"])
    shown = _OPTION_TEXT.sub("", body) if ("<select" in body or "<datalist" in body) else body
    blob = _text(shown).lower()
    for value in data.values():
        blob += " " + value.lower()
    for value, label in fields:
        blob += " " + value.lower() + " " + label.lower()
    for value in texts:
        blob += " " + value.lower()
    stub = {"id": int(attrs.get("data-id", 0) or 0), "d": data, "k": sort, "s": blob}
    if attrs.get("id"):
        stub["e"] = attrs["id"]
    if form:
        stub["f"] = form
    return stub


def page_of_rows(rows_html: str, first: int) -> tuple[str, list[dict]]:
    """A sheet's rows as drawn: the HTML of its first `first` rows (with
    whatever comes between them), and a stub for each other row."""
    rows = list(_ROW_FULL.finditer(rows_html))
    if len(rows) <= first:
        return rows_html, []
    cut = rows[first].start()
    return rows_html[:cut], [row_stub(m.group(1), m.group(2)) for m in rows[first:]]
