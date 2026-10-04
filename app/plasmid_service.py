"""Plasmid boxes: where a plasmid tube sits, and what that place is called.

A plasmid is in a box (PlasmidBox, `box_id_fk`) at a 0-based `box_row` /
`box_col`, or in a box without a cell, or in no box. `storage_box` mirrors
the box's name so older code, and a rollback, can still read it.

Every position shown or typed goes through the box's naming scheme
(app/positions.py, 1-based), so the sheet, the dialog, the detail page, the
search results and the rack grid all call a cell the same thing ("D7").
The routes live in app/app.py; this module holds the rules they share.
"""
from __future__ import annotations

import json

from sqlalchemy import func, select

from . import access, positions
from .i18n import gettext
from .models import PlasmidBox, PlasmidRecord

DEFAULT_ROWS = DEFAULT_COLS = 9
MAX_ROWS, MAX_COLS = 26, 40   # what the rack dialog allows
MAX_BATCH = 50                # "How many" on the New plasmid dialog


def all_boxes(session) -> list[PlasmidBox]:
    """Boxes grouped by freezer (location), then by name."""
    return sorted(session.scalars(select(PlasmidBox)),
                  key=lambda b: positions.place_order(b.location, b.name))


def label(p, box) -> str:
    """The name of p's cell in its box ("D7"); empty when not placed."""
    if box is None or p.box_row is None or p.box_col is None:
        return ""
    return positions.label(p.box_row + 1, p.box_col + 1, box.naming, box.cols)


def cell_label(box, row: int, col: int) -> str:
    """Name of a 0-based cell."""
    return positions.label(row + 1, col + 1, box.naming, box.cols)


def span(box) -> str:
    """"A1–I9": the first and last position of a box."""
    return f"{cell_label(box, 0, 0)}–{cell_label(box, box.rows - 1, box.cols - 1)}"


def parse(text: str, box) -> tuple[int | None, int | None]:
    """"D7" → (3, 6) under the box's scheme; blank → (None, None). Raises
    ValueError with a message worth showing for anything else."""
    text = (text or "").strip()
    if not text:
        return None, None
    cell = positions.parse(text, box.naming, box.rows, box.cols)
    if cell is not None:
        return cell[0] - 1, cell[1] - 1
    # Well formed but past the edge reads better as "outside the box".
    if positions.parse(text, box.naming, 999, 999) is not None:
        raise ValueError(f"{text.upper()} is outside the box {box.name} ({span(box)}).")
    raise ValueError(f"“{text}” is not a position in {box.name}. Positions there run {span(box)}.")


def is_stored(p) -> bool:
    """Physically stored: it has a cell in a box."""
    return p.box_id_fk is not None and p.box_row is not None and p.box_col is not None


def put_in(p, box) -> None:
    """In `box` (or none), without touching the cell."""
    p.box_id_fk = box.id if box is not None else None
    p.storage_box = box.name if box is not None else ""
    if box is None:
        p.box_row = p.box_col = None


def by_name(session, name: str):
    name = (name or "").strip()[:80]
    if not name:
        return None
    return session.scalar(select(PlasmidBox).where(func.lower(PlasmidBox.name) == name.lower()))


def box_for_name(session, name: str, user: str):
    """The box of that name; a typed name that is new makes a 9 × 9 box
    (the older forms, the API and CSV-style posts name boxes by text)."""
    box = by_name(session, name)
    if box is None and (name or "").strip():
        box = PlasmidBox(name=name.strip()[:80], rows=DEFAULT_ROWS, cols=DEFAULT_COLS,
                         naming=json.dumps(positions.scheme({})), created_by=user)
        session.add(box)
        session.flush()
    return box


def box_from_form(session, form, user: str, current=None):
    """(given, box, problem) for the box a form names: `box_id` (the
    select), or `storage_box` (a name, from older forms). `given` is False
    when the form says nothing about the box."""
    if "box_id" in form:
        raw = (form.get("box_id") or "").strip()
        if not raw:
            return True, None, None
        box = session.get(PlasmidBox, int(raw)) if raw.isdigit() else None
        if box is None:
            return True, current, "That box no longer exists. Reload the page."
        return True, box, None
    if "storage_box" in form:
        return True, box_for_name(session, form.get("storage_box") or "", user), None
    return False, current, None


def at(session, box_id: int, row: int, col: int, other_than=None):
    """The plasmid in a cell, if any (other than `other_than`). Runs without
    autoflush, so a plasmid being created is not inserted by the lookup."""
    stmt = select(PlasmidRecord).where(PlasmidRecord.box_id_fk == box_id, PlasmidRecord.box_row == row,
                                       PlasmidRecord.box_col == col)
    if other_than is not None and other_than.id is not None:
        stmt = stmt.where(PlasmidRecord.id != other_than.id)
    with session.no_autoflush:
        return session.scalar(stmt.limit(1))


def place(session, p, box, row: int | None, col: int | None, swap: bool = False):
    """Put `p` at (box, row, col), in `box` without a cell when row/col is
    None, or in no box. Refuses cells outside the box and taken cells,
    unless `swap`: then the occupant takes p's old cell (or waits unplaced
    in the box when p had none), if the user may move it. Returns
    (problem, occupant that moved); nothing changes on a refusal."""
    if box is None or row is None or col is None:
        put_in(p, box)
        p.box_row = p.box_col = None
        return None, None
    if not (0 <= row < box.rows and 0 <= col < box.cols):
        return gettext("That cell is outside the box %(box)s (%(span)s).", box=box.name, span=span(box)), None
    holder = at(session, box.id, row, col, p)
    if holder is not None:
        cell = cell_label(box, row, col)
        if not swap:
            return gettext("%(cell)s in %(box)s already holds plasmid #%(id)s.", cell=cell, box=box.name,
                           id=holder.plasmid_id), None
        if not access.can_edit(holder):
            return gettext("%(cell)s in %(box)s holds plasmid #%(id)s, which you may not move.", cell=cell,
                           box=box.name, id=holder.plasmid_id), None
        if is_stored(p):
            old_box = session.get(PlasmidBox, p.box_id_fk)
            put_in(holder, old_box)
            holder.box_row, holder.box_col = p.box_row, p.box_col
        else:
            holder.box_row = holder.box_col = None
    put_in(p, box)
    p.box_row, p.box_col = row, col
    return None, holder


def occupied(session, box_id: int, exclude_ids=()) -> set[tuple[int, int]]:
    exclude = set(exclude_ids)
    with session.no_autoflush:
        rows = session.execute(select(PlasmidRecord.box_row, PlasmidRecord.box_col, PlasmidRecord.id).where(
            PlasmidRecord.box_id_fk == box_id, PlasmidRecord.box_row.is_not(None),
            PlasmidRecord.box_col.is_not(None))).all()
    return {(r, c) for r, c, i in rows if i not in exclude}


def free_cells(session, box, count: int, start: tuple[int, int] | None = None,
               taken: set | None = None) -> list[tuple[int, int]]:
    """Up to `count` empty 0-based cells, reading along the rows from
    `start` (inclusive), skipping taken ones."""
    taken = set(taken if taken is not None else occupied(session, box.id))
    begin = start[0] * box.cols + start[1] if start else 0
    out = []
    for index in range(begin, box.rows * box.cols):
        cell = (index // box.cols, index % box.cols)
        if cell not in taken:
            out.append(cell)
            taken.add(cell)
            if len(out) == count:
                break
    return out


def count_in(session, box_id: int) -> int:
    return session.scalar(select(func.count(PlasmidRecord.id)).where(PlasmidRecord.box_id_fk == box_id)) or 0


def box_payload(box, count: int = 0) -> dict:
    """The box dialog's view of a box (rack_dialog in _rack_grid.html)."""
    return {"id": box.id, "_label": box.name, "_locked": not access.can_edit_rack(box), "_count": count,
            "_creator": box.created_by or "", "name": box.name, "rows": box.rows, "cols": box.cols,
            "location": box.location or "", "notes": box.notes or "",
            **{f"naming_{k}": v for k, v in positions.scheme(box.naming).items()}}


def denied_box(box) -> str:
    if (box.created_by or "").strip():
        return gettext("Only %(who)s (who made it) or an admin can change or delete the box %(box)s.",
                       who=box.created_by, box=box.name)
    return gettext("Only an admin can change or delete the box %(box)s.", box=box.name)
