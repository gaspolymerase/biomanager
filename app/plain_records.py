"""Records for a long sheet, read as plain rows rather than ORM objects.

A colony's mouse sheet lists thousands of mice, each with its cage, rack and
litter. Loaded through the ORM, building those objects took most of the
page's time; read as rows they cost a few milliseconds. These objects are
for reading only: they carry the same columns under the same names, and the
same `cage`, `rack` and `litter`, so the sheet's rows
(services.mouse_display_row) and the access rules (access.in_scope,
access.can_edit_mouse) read them as they read the models. Only the columns
those read are loaded (COLUMNS): another is an AttributeError, not a quiet
blank, so a row that comes to need one says so. Their classes have the
models' names, as access.can_edit tells records apart by class name
(access.CARE_RECORDS). Change a record through the ORM, never these.
"""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from . import models


class MouseRecord(SimpleNamespace):
    pass


class CageRecord(SimpleNamespace):
    pass


class MouseRack(SimpleNamespace):
    pass


class LitterRecord(SimpleNamespace):
    pass


# What each record carries: what the mouse sheet's rows and the access rules
# read (a timestamp, which the database driver must parse, only if used).
COLUMNS = {
    "mice": ("id", "mouse_id", "ear_tag", "strain", "attrs", "date_of_death", "gender", "transgene_1",
             "transgene_2", "transgene_3", "transgene_4", "genotype", "status", "owner", "note",
             "cage_id_fk", "litter_id_fk"),
    "mouse_cages": ("id", "cage_id", "cage_location", "purpose", "owner", "is_shared", "share_group_id",
                    "rack_id_fk", "rack_row", "rack_col"),
    "mouse_racks": ("id", "name", "rows", "cols", "naming"),
    "litters": ("id", "litter_id", "date_of_birth"),
}


def mice(session, *where) -> list[MouseRecord]:
    """The mice matching `where` (conditions on models.MouseRecord and the
    tables it joins), by mouse number, each with its cage (and the cage's
    rack) and litter, or None. Mice in one cage share its object."""
    m, c, r, l = (models.MouseRecord.__table__, models.CageRecord.__table__,
                  models.MouseRack.__table__, models.LitterRecord.__table__)
    columns, spans, at = [], [], 0
    for table in (m, c, r, l):
        names = COLUMNS[table.name]
        columns += [table.c[name].label(f"{table.name}__{name}") for name in names]
        spans.append((at, at + len(names), names))
        at += len(names)
    stmt = (select(*columns)
            .select_from(m.outerjoin(c, c.c.id == m.c.cage_id_fk)
                         .outerjoin(r, r.c.id == c.c.rack_id_fk)
                         .outerjoin(l, l.c.id == m.c.litter_id_fk))
            .where(*where))
    (m0, m1, m_names), (c0, c1, c_names), (r0, r1, r_names), (l0, l1, l_names) = spans
    cages: dict[int, CageRecord] = {}
    racks: dict[int, MouseRack] = {}
    litters: dict[int, LitterRecord] = {}
    out = []
    for row in session.execute(stmt):
        mouse = MouseRecord(**dict(zip(m_names, row[m0:m1])))
        cage = None
        if row[c0] is not None:
            cage = cages.get(row[c0])
            if cage is None:
                cage = cages[row[c0]] = CageRecord(**dict(zip(c_names, row[c0:c1])))
                rack = None
                if row[r0] is not None:
                    rack = racks.get(row[r0])
                    if rack is None:
                        rack = racks[row[r0]] = MouseRack(**dict(zip(r_names, row[r0:r1])))
                cage.rack = rack
        mouse.cage = cage
        litter = None
        if row[l0] is not None:
            litter = litters.get(row[l0])
            if litter is None:
                litter = litters[row[l0]] = LitterRecord(**dict(zip(l_names, row[l0:l1])))
        mouse.litter = litter
        out.append(mouse)
    # Sorted here, not by the database: asked to give them in order, SQLite
    # walks every mouse by its number rather than the few it needs by date
    # of death (ix_mice_date_of_death), a hundred thousand in a large lab.
    out.sort(key=lambda mouse: mouse.mouse_id)
    return out
