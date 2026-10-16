"""A rack's layout changes: what happens to what is in it.

A cage, tank, vial, tube or plasmid keeps its place as a row and a column
of its rack; the name of that place ("F7") comes from the rack's size and
naming scheme (app/positions.py). So when someone changes the layout (rows
and columns, letters or numbers on each axis, which comes first, the first
number), each thing stays in its cell and the cell may get another name:
cage 226 at "F7" read "G6" once Rack B's axes were swapped, though nobody
had moved it. Which is right depends on the rack, so BioManager asks:

- ``keep_names``: each keeps its position name; it moves to the cell that
  now has that name ("F7" stays F7). One whose name the new layout has no
  cell for stays in the rack with no position (Unplaced).
- ``keep_cells``: each keeps its row and column, and its name changes
  (what happened before). One whose cell is outside a smaller grid stays
  in the rack with no position.

`plan` works out both for a save before anything changes (the rack dialog
shows it, /racks/<kind>/<id>/layout); `check` is what every save route
calls: a save that changes where something is or what its place is called
is refused unless it says which (``cages=keep_names|keep_cells``, from the
dialog or the API), and `apply` then does it in the same transaction as
the rack's own change, as one batch Batch history can undo.

Mouse racks, fish racks, inventory boxes, fly and worm racks and plasmid
boxes share this. Racks of the other databases (organism_routes) store
their naming in their settings and already unplace what falls outside a
smaller grid; they are not covered here.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Callable

from flask import Blueprint, abort, jsonify, request
from sqlalchemy import select

from . import access, audit, positions
from .db import SessionLocal
from .i18n import gettext, ngettext

CHOICES = ("keep_names", "keep_cells")
FIELD = "cages"   # the form and API field that carries the choice

bp = Blueprint("rack_layout", __name__, url_prefix="/racks")


@dataclass(frozen=True)
class Layout:
    """A rack's size and naming scheme, as stored or as a form would save it."""
    rows: int
    cols: int
    naming: dict

    @classmethod
    def of(cls, rack) -> "Layout":
        return cls(int(rack.rows or 0), int(rack.cols or 0), positions.scheme(rack.naming))

    def inside(self, row: int, col: int) -> bool:
        return 1 <= row <= self.rows and 1 <= col <= self.cols

    def name(self, row: int, col: int) -> str:
        return positions.label(row, col, self.naming, self.cols)

    def cell(self, name: str) -> tuple[int, int] | None:
        """The cell this layout calls `name`, or None when it has none. The
        separator and case don't matter ("D-7" is D7); a name that only
        parses by counting ("37" on a lettered rack) is not the same name."""
        cell = positions.parse(name, self.naming, self.rows, self.cols)
        if cell is None or _bare(self.name(*cell)) != _bare(name):
            return None
        return cell


def _bare(name: str) -> str:
    return re.sub(r"[\s\-.:/]", "", name or "").upper()


@dataclass(frozen=True)
class Kind:
    """One kind of rack and what sits in it."""
    key: str
    rack: type
    item: Callable          # () -> the model of what sits in it
    rack_fk: str
    row: str
    col: str
    base: int               # 0 when the item's row and column count from 0
    code: Callable          # item -> how the lab names it ("226")
    live: Callable          # item -> counts for a shared position
    table: str              # the items' table, for the batch

    def placed(self, session, rack) -> list:
        model = self.item()
        return list(session.scalars(select(model).where(
            getattr(model, self.rack_fk) == rack.id, getattr(model, self.row).is_not(None),
            getattr(model, self.col).is_not(None))))

    def cell_of(self, item) -> tuple[int, int]:
        return getattr(item, self.row) + 1 - self.base, getattr(item, self.col) + 1 - self.base

    def put(self, item, cell) -> None:
        row, col = (cell[0] - 1 + self.base, cell[1] - 1 + self.base) if cell else (None, None)
        setattr(item, self.row, row)
        setattr(item, self.col, col)


def _things(kind: str, n: int) -> str:
    """"3 cages", in the kind's own word."""
    if kind == "fish_rack":
        return ngettext("%(num)s tank", "%(num)s tanks", n)
    if kind == "inventory_box":
        return ngettext("%(num)s item", "%(num)s items", n)
    if kind == "stock_rack":
        return ngettext("%(num)s vial or plate", "%(num)s vials or plates", n)
    if kind == "plasmid_box":
        return ngettext("%(num)s plasmid", "%(num)s plasmids", n)
    return ngettext("%(num)s cage", "%(num)s cages", n)


def _kinds() -> dict[str, Kind]:
    from . import models as m
    from .services import cage_is_active
    return {k.key: k for k in (
        Kind("mouse_rack", m.MouseRack, lambda: m.CageRecord, "rack_id_fk", "rack_row", "rack_col", 1,
             lambda c: c.cage_id, cage_is_active, "mouse_cages"),
        Kind("fish_rack", m.FishRack, lambda: m.TankRecord, "rack_id_fk", "row", "col", 0,
             lambda t: t.tank_id, lambda t: bool(t.active), "tanks"),
        Kind("inventory_box", m.InventoryRack, lambda: m.InventoryItem, "rack_id_fk", "rack_row", "rack_col", 1,
             lambda i: i.name or f"#{i.number}", lambda i: True, "inventory_items"),
        Kind("stock_rack", m.StockRack, lambda: m.StockUnit, "rack_id_fk", "rack_row", "rack_col", 1,
             lambda u: f"No. {u.number}", lambda u: bool(u.active), "stock_units"),
        Kind("plasmid_box", m.PlasmidBox, lambda: m.PlasmidRecord, "box_id_fk", "box_row", "box_col", 0,
             lambda p: f"#{p.plasmid_id}", lambda p: True, "plasmids"),
    )}


def kind(key: str) -> Kind:
    return _kinds()[key]


def form_naming(form, rack) -> dict:
    """The naming a save would store: the form's, or the rack's own when the
    form says nothing about naming (a client that only renames or resizes)."""
    if "naming_mode" not in form and rack is not None and rack.id is not None:
        return positions.scheme(rack.naming)
    return positions.scheme_from_form(form)


def plan(session, k: Kind, rack, new: Layout) -> dict:
    """What each option does to what is in `rack` if it takes `new`."""
    old = Layout.of(rack)
    items = sorted(k.placed(session, rack), key=lambda i: positions.natural(k.code(i)))
    affected, final = [], {"keep_names": {}, "keep_cells": {}}
    for item in items:
        cell = k.cell_of(item)
        if not old.inside(*cell):
            # Left outside an earlier, smaller grid: shown as unplaced, and
            # where it was, whatever is chosen.
            final["keep_names"][item.id] = final["keep_cells"][item.id] = cell if new.inside(*cell) else None
            continue
        now = old.name(*cell)
        by_name = new.cell(now)
        by_cell = cell if new.inside(*cell) else None
        final["keep_names"][item.id] = by_name
        final["keep_cells"][item.id] = by_cell
        if by_name == cell and by_cell == cell:
            continue
        affected.append({"id": item.id, "code": k.code(item), "now": now, "live": bool(k.live(item)),
                         "keep_names": new.name(*by_name) if by_name else "",
                         "keep_cells": new.name(*by_cell) if by_cell else ""})
    live = {item.id for item in items if k.live(item)}

    def shared(choice):
        counts = Counter(cell for item_id, cell in final[choice].items() if cell and item_id in live)
        return sum(1 for n in counts.values() if n > 1)

    off = {c: sum(1 for a in affected if not a[c]) for c in CHOICES}
    together = {c: shared(c) for c in CHOICES}
    things = _things(k.key, len(affected))
    return {
        "kind": k.key, "rack": rack.name, "affected": affected,
        "default": "keep_names" if old.naming != new.naming else "keep_cells",
        "off": off, "shared": together, "things": things,
        # What the dialog says: how many, and what each choice leaves behind.
        "summary": gettext("%(things)s in %(rack)s would get another position name, or none.",
                           things=things, rack=rack.name),
        "notes": {c: _note(k.key, off[c], together[c]) for c in CHOICES},
        "_cells": final,
    }


def _note(kind_key: str, off: int, together: int) -> str:
    parts = []
    if off:
        parts.append(gettext("%(num)s would have no position (Unplaced)", num=off))
    if together and kind_key == "mouse_rack":
        parts.append(ngettext("%(num)s position would hold more than one active cage (marked !)",
                              "%(num)s positions would hold more than one active cage (marked !)", together))
    elif together:
        parts.append(ngettext("%(num)s position would hold more than one", "%(num)s positions would hold more than one",
                              together))
    return " · ".join(parts)


def check(session, k: Kind, rack, new: Layout, choice) -> tuple[dict | None, str | None]:
    """Before a save: (the plan to apply, None), (None, None) when nothing
    in the rack changes place or name, or (None, why it is refused)."""
    if rack is None or rack.id is None:
        return None, None
    p = plan(session, k, rack, new)
    if not p["affected"]:
        return None, None
    choice = (choice or "").strip()
    if choice not in CHOICES:
        return None, refusal(p)
    p["choice"] = choice
    return p, None


def refusal(p: dict) -> str:
    return gettext("Not saved: the new layout changes the positions of %(things)s in %(rack)s. Choose whether each keeps its position name (cages=keep_names) or its row and column (cages=keep_cells).",
                   things=p["things"], rack=p["rack"])


def batch(session, p: dict | None):
    """The batch a save that moves things runs in, so Batch history can undo
    the rack's change and the moves together; nothing when none move."""
    if not p:
        return nullcontext()
    return audit.batch(session, "update", describe(p), kind(p["kind"]).table)


def apply(session, p: dict | None) -> int:
    """Move what `check` said would change, as the plan's choice says, in
    the same `batch` as the rack's own change."""
    if not p:
        return 0
    k = kind(p["kind"])
    cells = p["_cells"][p["choice"]]
    model = k.item()
    moved = 0
    for a in p["affected"]:
        item = session.get(model, a["id"])
        if item is not None:
            k.put(item, cells[a["id"]])
            moved += 1
    return moved


def describe(p: dict) -> str:
    """The batch's name: "Rack B: new layout, kept position names (22 cages)"."""
    if p["choice"] == "keep_names":
        return gettext("%(rack)s: new layout, each kept its position name (%(things)s)", rack=p["rack"], things=p["things"])
    return gettext("%(rack)s: new layout, each kept its row and column (%(things)s)", rack=p["rack"], things=p["things"])


def outcome(p: dict | None) -> str:
    """After the save, what happened, to follow "Saved rack B." in its flash."""
    if not p:
        return ""
    off = p["off"][p["choice"]]
    text = " " + describe(p) + "."
    if off:
        text += " " + ngettext("%(num)s is now in the rack with no position (Unplaced).",
                               "%(num)s are now in the rack with no position (Unplaced).", off)
    return text + " " + gettext("Undo from Batch history if that was a mistake.")


def public(p: dict) -> dict:
    return {key: value for key, value in p.items() if not key.startswith("_")}


# ---------------------------------------------------------------- the preview

def _form_layout(k: Kind, rack, form) -> Layout:
    def number(name, default, top):
        raw = (form.get(name) or "").strip()
        return max(1, min(top, int(raw))) if raw.isdigit() else default
    return Layout(number("rows", rack.rows, 26), number("cols", rack.cols, 40), form_naming(form, rack))


@bp.route("/<key>/<int:rack_id>/layout", methods=["POST"])
def preview(key: str, rack_id: int):
    """What saving the rack dialog's form would do to what is in the rack:
    the dialog asks before it saves when anything would change."""
    from flask import g
    if g.get("user") is None:
        abort(401)
    if key not in _kinds():
        abort(404)
    k = kind(key)
    with SessionLocal() as session:
        rack = session.get(k.rack, rack_id)
        if rack is None:
            abort(404)
        if not access.can_edit_rack(rack):
            abort(403)
        p = plan(session, k, rack, _form_layout(k, rack, request.form))
        return jsonify(public(p))


def json_naming(data: dict, rack) -> dict:
    """An API body's naming ({mode, rows, cols, order, separator, start}),
    over the rack's own."""
    raw = data.get("naming")
    if raw is None:
        return positions.scheme(rack.naming)
    if isinstance(raw, str):
        raw = json.loads(raw)
    return positions.scheme({**positions.scheme(rack.naming), **(raw or {})})
