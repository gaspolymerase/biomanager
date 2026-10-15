"""A long sheet paged by the server, for a page that draws its sheet whole.

The inventories and the mouse sheet draw their first page through their own
row templates (inventory_routes, app.colony_mouse_rows). Other sheets (fish
tanks, fly vials, litters) are drawn by their page as before and trimmed
here: past LAZY_OVER rows, the rows after the first page leave the page
and stand as stubs (cell_text.row_stub, read from the rows as drawn, as
data-table.js reads a row), their row forms leave its <template
data-row-forms>, and the sheet gets data-dt-rows-url. That address draws
the page again with only the rows asked for (`keep`), and rows_of lifts
them and their forms out of it.

Drawing is most of such a page's time on the server (3,337 tanks: 2.2 s of
2.8), and nearly all of it in the browser, which no longer builds rows it
does not show.
"""
from __future__ import annotations

import re

from jinja2.utils import htmlsafe_json_dumps
from markupsafe import Markup

from . import cell_text
from . import inventory_routes

_FORM = re.compile(r'<form\b[^>]*\bid="([^"]+)"[^>]*>.*?</form>\s*', re.S)


def _sheet(html: str, table_id: str) -> tuple[int, int, int, int, int] | None:
    """Where the sheet whose data-table-id starts with `table_id` is:
    (its section's start tag end, its <tbody> start tag end, </tbody>,
    its row forms' <template> start tag end, that </template>); None if
    the page does not draw it."""
    at = html.find(f'data-table-id="{table_id}')
    if at < 0:
        return None
    head_end = html.find(">", at) + 1
    body = html.find("<tbody", head_end)
    if body < 0:
        return None
    body_end = html.find(">", body) + 1
    body_close = html.find("</tbody>", body_end)
    section_close = html.find("</section>", body_close)
    forms = html.find("<template data-row-forms>", body_close, section_close if section_close >= 0 else len(html))
    if forms < 0:
        return head_end, body_end, body_close, -1, -1
    forms_end = forms + len("<template data-row-forms>")
    return head_end, body_end, body_close, forms_end, html.find("</template>", forms_end)


def page(html: str, table_id: str, rows_url: str) -> str:
    """The page with its sheet paged, when the sheet has more than
    LAZY_OVER rows; else the page as it was."""
    where = _sheet(html, table_id)
    if where is None:
        return html
    head_end, body_end, body_close, forms_end, forms_close = where
    rows = html[body_end:body_close]
    if len(cell_text._ROW_FULL.findall(rows)) <= inventory_routes.LAZY_OVER:
        return html
    drawn, stubs = cell_text.page_of_rows(rows, inventory_routes.FIRST_PAGE)
    gone = {s["f"] for s in stubs if s.get("f")}
    forms = html[forms_end:forms_close] if forms_end >= 0 else ""
    if gone:
        forms = _FORM.sub(lambda m: "" if m.group(1) in gone else m.group(0), forms)
    after_table = html.find("</table>", body_close) + len("</table>")
    script = f"<script type=\"application/json\" data-dt-stubs>{htmlsafe_json_dumps(stubs)}</script>"
    head = html[:head_end - 1] + f' data-dt-rows-url="{Markup.escape(rows_url)}">'
    out = head + html[head_end:body_end] + drawn + html[body_close:after_table] + script
    if forms_end >= 0:
        out += html[after_table:forms_end] + forms + html[forms_close:]
    else:
        out += html[after_table:]
    return out


def rows_of(html: str, table_id: str, ids: list[int]) -> dict:
    """The sheet's rows on a page drawn with only these, in the order
    asked, and their row forms: what /rows answers (data-table.js)."""
    where = _sheet(html, table_id)
    if where is None:
        return {"rows": "", "forms": "", "ids": []}
    _, body_end, body_close, forms_end, forms_close = where
    found = {}
    for m in cell_text._ROW_FULL.finditer(html[body_end:body_close]):
        row_id = re.search(r'\bdata-id="(\d+)"', m.group(1))
        if row_id:
            found[int(row_id.group(1))] = m.group(0)
    order = [i for i in dict.fromkeys(ids) if i in found]
    forms = html[forms_end:forms_close] if forms_end >= 0 else ""
    return {"rows": "".join(found[i] for i in order), "forms": forms, "ids": order}


def ids_asked(request) -> list[int]:
    return [int(x) for x in request.args.get("ids", "").split(",") if x.strip().isdigit()][:inventory_routes.ROWS_AT_ONCE]


def answer(html: str, table_id: str, ids: list[int], request):
    """What a /rows address answers: the rows and their forms, or with
    ?as=text each row's cells' words (an export, cell_text)."""
    from flask import jsonify
    found = rows_of(html, table_id, ids)
    if request.args.get("as") == "text":
        texts = cell_text.row_texts(found["rows"])
        return jsonify({"texts": {str(i): texts[i] for i in found["ids"] if i in texts}, "ids": found["ids"]})
    return jsonify(found)
