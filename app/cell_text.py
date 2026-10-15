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
