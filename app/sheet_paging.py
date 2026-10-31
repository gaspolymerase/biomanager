"""The rows addresses of long sheets the server pages.

The inventories, the mouse, cage and litter sheets, the fish tanks and the
fly vials draw their first page through their own row templates and make
the other rows' stubs from data (inventory_routes, colony_routes.colony_mouse_rows,
zebrafish_routes.zf_tank_stubs, stock_routes.unit_stubs). A sheet's rows
address draws the page again with only the rows asked for, and rows_of
lifts them and their forms out of it; answer() is what it sends.

Drawing every row and reading them back as stubs was most of such a
page's time on the server (3,337 tanks took 2.2 s of 2.8), and nearly all
of it in the browser, which no longer builds rows it does not show.
"""
from __future__ import annotations

import re

from . import cell_text
from . import inventory_routes


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
