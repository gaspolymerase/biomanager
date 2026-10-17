"""Records for a long sheet, read as plain rows rather than ORM objects.

A colony's mouse sheet lists thousands of mice, each with its cage, rack and
litter. Loaded through the ORM, building those objects took most of the
page's time; read as rows they cost a few milliseconds. These records are
for reading only: they carry the same columns under the same names, and the
same `cage`, `rack` and `litter` (or a litter's `mice`), so the sheet's rows
(services.mouse_display_row) and the access rules (access.in_scope,
access.can_edit_mouse) read them as they read the models. Only the columns
those read are loaded (COLUMNS): another is an AttributeError, not a quiet
blank, so a row that comes to need one says so. Their classes have the
models' names, as access.can_edit tells records apart by class name
(access.CARE_RECORDS). Most are named tuples, cheap to make by the
thousand. Change a record through the ORM, never these.
"""
from __future__ import annotations

from collections import namedtuple
from operator import attrgetter

from sqlalchemy import select

from . import models


class _Plain:
    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.__dict__!r})"


class CageRecord(_Plain):
    pass


class LitterRecord(_Plain):
    pass


def _made(kind, names, values):
    record = object.__new__(kind)
    record.__dict__ = dict(zip(names, values))
    return record


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


# The mouse sheet's records: tuples, cheap to make by the thousand.
SheetMouse = namedtuple("MouseRecord", (*COLUMNS["mice"], "cage", "litter"))
SheetCage = namedtuple("CageRecord", (*COLUMNS["mouse_cages"], "rack"))
SheetRack = namedtuple("MouseRack", COLUMNS["mouse_racks"])
SheetLitter = namedtuple("LitterRecord", COLUMNS["litters"])


def mice(session, *where) -> list[SheetMouse]:
    """The mice matching `where` (conditions on models.MouseRecord and the
    tables it joins), by mouse number, each with its cage (and the cage's
    rack) and litter, or None. Mice in one cage share its record."""
    m, c, r, l = (models.MouseRecord.__table__, models.CageRecord.__table__,
                  models.MouseRack.__table__, models.LitterRecord.__table__)
    columns, spans, at = [], [], 0
    for table in (m, c, r, l):
        names = COLUMNS[table.name]
        columns += [table.c[name].label(f"{table.name}__{name}") for name in names]
        spans.append((at, at + len(names)))
        at += len(names)
    stmt = (select(*columns)
            .select_from(m.outerjoin(c, c.c.id == m.c.cage_id_fk)
                         .outerjoin(r, r.c.id == c.c.rack_id_fk)
                         .outerjoin(l, l.c.id == m.c.litter_id_fk))
            .where(*where))
    (m0, m1), (c0, c1), (r0, r1), (l0, l1) = spans
    cages: dict[int, SheetCage] = {}
    racks: dict[int, SheetRack] = {}
    litters: dict[int, SheetLitter] = {}
    out = []
    for row in session.execute(stmt):
        cage = None
        if row[c0] is not None:
            cage = cages.get(row[c0])
            if cage is None:
                rack = None
                if row[r0] is not None:
                    rack = racks.get(row[r0])
                    if rack is None:
                        rack = racks[row[r0]] = SheetRack(*row[r0:r1])
                cage = cages[row[c0]] = SheetCage(*row[c0:c1], rack)
        litter = None
        if row[l0] is not None:
            litter = litters.get(row[l0])
            if litter is None:
                litter = litters[row[l0]] = SheetLitter(*row[l0:l1])
        out.append(SheetMouse(*row[m0:m1], cage, litter))
    # Sorted here, not by the database: asked to give them in order, SQLite
    # walks every mouse by its number rather than the few it needs by date
    # of death (ix_mice_date_of_death), a hundred thousand in a large lab.
    out.sort(key=attrgetter("mouse_id"))
    return out


LITTER_COLUMNS = ("id", "litter_id", "date_of_birth", "cohort_name", "father_info", "mother_info", "total_pups",
                  "notes")
# A pup as every litter's row knows it (number and sex: what a search finds),
# and as a drawn row shows it (alive or not) with who may edit it. The
# columns of ix_mice_litter_pups, so the database reads them from the index
# alone. Tuples: a year's litters have tens of thousands of pups.
PupName = namedtuple("PupName", ("mouse_id", "gender"))
_by_number = attrgetter("mouse_id")
PUP_COLUMNS = ("id", "mouse_id", "gender", "status", "date_of_death", "owner", "cage_id_fk", "litter_id_fk")
Pup = namedtuple("MouseRecord", (*PUP_COLUMNS, "cage"))
PUP_CAGE_COLUMNS = ("id", "owner", "is_shared", "share_group_id")


def litters(session, *where) -> list[LitterRecord]:
    """The litters matching `where` (conditions on models.LitterRecord),
    each with its `mice` by mouse number: each pup's number and sex only
    (PupName). full_pups reads the rest for the litters a page draws."""
    l, m = models.LitterRecord.__table__, models.MouseRecord.__table__
    found = {row[0]: _made(LitterRecord, LITTER_COLUMNS, row)
             for row in session.execute(select(*(l.c[name] for name in LITTER_COLUMNS)).where(*where))}
    if not found:
        return []
    pups: dict[int, list] = {litter_id: [] for litter_id in found}
    stmt = select(m.c.litter_id_fk, m.c.mouse_id, m.c.gender).where(
        m.c.litter_id_fk.in_(select(l.c.id).where(*where)))
    for litter_id, mouse_id, gender in session.execute(stmt):
        held = pups.get(litter_id)
        if held is not None:
            held.append(PupName(mouse_id, gender))
    for litter_id, litter in found.items():
        litter.mice = sorted(pups[litter_id], key=_by_number)
    return list(found.values())


def full_pups(session, litters: list[LitterRecord], cages: bool = True) -> None:
    """Gives each of `litters` its `mice` in full (Pup), by mouse number.
    `cages`: each pup with its cage (who may edit a litter is who may edit
    all its pups, which asks each pup's cage: access.can_edit_litter); else
    `cage` is None, for someone who may edit any mouse."""
    if not litters:
        return
    m, c = models.MouseRecord.__table__, models.CageRecord.__table__
    by_id = {litter.id: litter for litter in litters}
    for litter in litters:
        litter.mice = []
    pup_columns = [m.c[name] for name in PUP_COLUMNS]
    chosen = m.c.litter_id_fk.in_(sorted(by_id))
    stmt = select(*pup_columns).where(chosen)
    if cages:
        stmt = select(*pup_columns, *(c.c[name].label(f"cage__{name}") for name in PUP_CAGE_COLUMNS)).select_from(
            m.outerjoin(c, c.c.id == m.c.cage_id_fk)).where(chosen)
    held: dict[int, CageRecord] = {}
    width = len(PUP_COLUMNS)
    at_litter = PUP_COLUMNS.index("litter_id_fk")
    for row in session.execute(stmt):
        cage = None
        if cages and row[width] is not None:
            cage = held.get(row[width])
            if cage is None:
                cage = held[row[width]] = _made(CageRecord, PUP_CAGE_COLUMNS, row[width:])
        by_id[row[at_litter]].mice.append(Pup(*row[:width], cage))
    for litter in litters:
        litter.mice.sort(key=_by_number)



# What an inventory sheet reads of an item: every column but when it was
# made and last changed, which the database driver must parse.
INVENTORY_ITEM_COLUMNS = tuple(column.name for column in models.InventoryItem.__table__.columns
                               if column.name not in ("created_at", "updated_at"))


class InventoryItem(namedtuple("InventoryItem", (*INVENTORY_ITEM_COLUMNS, "rack", "decoded"))):
    """An inventory item as its sheet reads it (inventory_routes._chosen):
    the columns above, its `rack` (the ORM's, or None), and `attrs_dict`,
    decoded once rather than each of the many times a sheet asks for it
    (`decoded` holds it). Read it only: what attrs_dict gives is shared."""
    __slots__ = ()
    # Its table, as the model's: access._peer_editable reads it.
    __table__ = models.InventoryItem.__table__

    @property
    def attrs_dict(self) -> dict:
        decoded = self.decoded
        if not decoded:
            decoded.append(models.InventoryItem.attrs_dict.fget(self))
        return decoded[0]


def inventory_items(session, *where, order_by=(), racks: dict | None = None) -> list[InventoryItem]:
    """The inventory items matching `where`, in `order_by`'s order, each
    with its `rack`: from `racks` (by id) when there, else read."""
    t = models.InventoryItem.__table__
    rows = session.execute(select(*(t.c[name] for name in INVENTORY_ITEM_COLUMNS)).where(*where)
                           .order_by(*order_by)).all()
    racks = dict(racks or {})
    at_rack = INVENTORY_ITEM_COLUMNS.index("rack_id_fk")
    missing = {row[at_rack] for row in rows if row[at_rack] is not None} - set(racks)
    if missing:
        racks.update((r.id, r) for r in session.scalars(
            select(models.InventoryRack).where(models.InventoryRack.id.in_(sorted(missing)))))
    racks[None] = None
    return [InventoryItem(*row, racks[row[at_rack]], []) for row in rows]
