"""A sheet's cells drawn as text until used, in Chromium (cell_text.text_cells_on;
static/text-cells.js turns one back into its field). Each <td> holding just
one field: the field's own markup goes in data-field, and the text it shows
is drawn instead, as the field would show it (a menu's chosen label, else
its first; an input's placeholder when empty). Gzipped, a page grows by
about a tenth.

app.py registers as_text_cells as the template filter of that name, which
the zebrafish's pages call too; the colony's sheets draw their rows with
text_cell directly (app/mouse_rows.py, app/cage_rows.py).
"""
from __future__ import annotations

import re

from markupsafe import Markup, escape


_ONE_FIELD = re.compile(r'<td((?:\s[^>]*)?)>\s*(<input\b[^>]*>|<select\b[^>]*>.*?</select>)\s*</td>', re.S)
# A field's attributes, each with its value: a word in a value ("see the
# form") is part of it, not an attribute of its own.
_ATTR = re.compile(r'\s([\w:-]+)(?:="([^"]*)")?')
_CHOSEN = re.compile(r'<option\b(?=[^>]*\bselected\b)[^>]*?\bvalue="([^"]*)"[^>]*>([^<]*)</option>')
_FIRST = re.compile(r'<option\b[^>]*?\bvalue="([^"]*)"[^>]*>([^<]*)</option>')
# Copied onto the text: what the page's styles and scripts read from the field.
_KEPT = ("title", "data-status", "data-tone", "data-scope", "data-sex", "data-alive-values", "data-dead-values", "data-dead-if-set")
_DISABLED = re.compile(r"\sdisabled\b")
_READONLY = re.compile(r"\sreadonly\b")


def text_cell(td_attrs: str, field: str, attrs: dict | None = None, locked: bool | None = None,
              chosen: tuple[str, str] | None = None) -> str | None:
    """A field's cell (<td{td_attrs}>{field}</td>) drawn as text, as
    as_text_cells draws it; None for a field that stays one (a tick box, a
    hidden field, one without a name). What the caller knows of the field
    it says, rather than have it read back from the tag (app/mouse_rows.py):
    `attrs`, its tag's attributes as written there; `locked`, whether it is
    disabled (then it is not read-only either); `chosen`, a menu's chosen
    option's value and words, as written."""
    tag = "select" if field.startswith("<select") else "input"
    head = field[:field.index(">") + 1] if attrs is None or locked is None else ""
    if attrs is None:
        attrs = dict(_ATTR.findall(head))
    if attrs.get("type") in ("checkbox", "hidden", "radio") or "name" not in attrs:
        return None
    if tag == "select":
        if chosen is None:
            # As the browser shows it: the chosen option, else the first.
            found = _CHOSEN.search(field) or _FIRST.search(field)
            chosen = (found.group(1), found.group(2)) if found else ("", "")
        value, shown = chosen
    else:
        value = shown = attrs.get("value", "")
    empty = not value
    if not shown and "note-cell" not in attrs.get("class", ""):
        shown = attrs.get("placeholder", "")
    readonly = False
    if locked is None:
        locked = _DISABLED.search(head) is not None
        readonly = _READONLY.search(head) is not None
    classes = " ".join(c for c in ["cell-text", attrs.get("class", ""), "is-empty" if empty else "",
                                   "is-date" if attrs.get("type") == "date" else "",
                                   "is-disabled" if locked else "",
                                   # an input's placeholder, shown for an empty one: not its value
                                   "is-placeholder" if tag != "select" and not value and shown else ""] if c)
    extra = "".join(f' {k}="{attrs[k]}"' for k in _KEPT if k in attrs)
    form = f' data-form="{attrs["form"]}"' if attrs.get("form") else ""
    # A field that can't be changed (or only shows what is worked out) stays text.
    reach = "" if locked or readonly else ' tabindex="0"'
    return (f'<td{td_attrs}><span class="{classes}"{reach} data-field="{escape(field)}" '
            f'data-sort-for="{attrs["name"]}" data-sort-value="{value}"{form}'
            f'{extra}>{shown}</span></td>')


def as_text_cells(html, on: bool = True) -> Markup:
    html = str(html)
    if not on:
        return Markup(html)
    return Markup(_ONE_FIELD.sub(lambda m: text_cell(m.group(1), m.group(2)) or m.group(0), html))
