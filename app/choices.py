"""The colony's dropdown choices, as the Dropdowns tab shows them.

Each column with choices (Sex, Status, Cage purpose, Strain…) is a list in
the order the lab set by dragging (`DropdownOption.position`), each choice
with how many records hold it. Renaming a choice can change it on those
records too, and removing one can put another in its place there; both are
one audit batch, so Batch history can undo them. Values the records hold
that are not a choice are listed under the column, to add as one.

The words Breeding and Breeder mean something to the colony (CLAUDE.md:
"The mouse colony's words"), so those two purposes are not renamed here.
"""
from __future__ import annotations

from sqlalchemy import func, select

from . import bulk
from .i18n import gettext, translate_value
from .models import MOUSE_STATUS_OPTIONS, CageRecord, DropdownOption, MouseRecord
from .services import BREEDER_PURPOSE, BREEDING_PURPOSE

# Where each column's choices are used: the record, its column, its noun.
USES = {
    "gender": (MouseRecord, "gender", "mouse"),
    "genotype": (MouseRecord, "genotype", "mouse"),
    "owner": (MouseRecord, "owner", "mouse"),
    "status": (MouseRecord, "status", "mouse"),
    "strain": (MouseRecord, "strain", "mouse"),
    "purpose": (CageRecord, "purpose", "cage"),
}
# The order the columns are listed in, and what each is called and where it shows.
FIELDS = ("status", "purpose", "strain", "genotype", "gender", "owner")
# Choices every lab has, offered first whatever the lab adds (app.py
# _merged_choices): shown on the tab, never renamed or removed.
BUILT_IN = {"status": MOUSE_STATUS_OPTIONS, "gender": ["F", "M", "Unknown"]}


def label(field: str) -> str:
    return {
        "gender": gettext("Sex"),
        "genotype": gettext("Genotype"),
        "owner": gettext("Owner"),
        "purpose": gettext("Cage purpose"),
        "status": gettext("Status"),
        "strain": gettext("Strain"),
    }.get(field) or translate_value(field).capitalize()


def where_shown(field: str) -> str:
    return {
        "gender": gettext("The Sex column of the mouse sheet."),
        "genotype": gettext("The Genotype column of the mouse sheet."),
        "owner": gettext("The Owner column of the mouse sheet, beside the lab’s people."),
        "purpose": gettext("A cage’s Purpose, and the buttons above the cages: All, Active, then one for each."),
        "status": gettext("The Status column of the mouse sheet."),
        "strain": gettext("The Strain column of the mouse sheet; anyone may still type their own."),
    }.get(field, "")


def locked(field: str, value: str) -> bool:
    """Breeding and Breeder: the colony's litters and Breeders tab go by them."""
    return field == "purpose" and value.strip().lower() in (BREEDING_PURPOSE, BREEDER_PURPOSE)


def built_in(field: str, value: str) -> bool:
    return value.strip().lower() in {v.lower() for v in BUILT_IN.get(field, [])}


def _counts(session, field: str) -> dict[str, int]:
    """How many records hold each value of `field`, by its lower case."""
    if field not in USES:
        return {}
    model, column, _noun = USES[field]
    col = getattr(model, column)
    rows = session.execute(select(func.lower(func.trim(col)), func.count()).where(col != "").group_by(func.lower(func.trim(col))))
    return {value: n for value, n in rows if value}


def uses_text(field: str, n: int) -> str:
    if not n:
        return gettext("not used")
    return bulk.count(n, USES[field][2] if field in USES else "mouse")


def columns(session) -> list[dict]:
    """Every column with choices, in the tab's order, each with its choices
    (id, value, uses, locked) and the values in use that aren't one."""
    by_field: dict[str, list[DropdownOption]] = {f: [] for f in FIELDS}
    for option in session.scalars(select(DropdownOption).order_by(
            DropdownOption.field_name, DropdownOption.position, DropdownOption.option_value)):
        by_field.setdefault(option.field_name, []).append(option)
    out = []
    for field, options in by_field.items():
        counts = _counts(session, field)
        known = {o.option_value.strip().lower() for o in options} | {v.lower() for v in BUILT_IN.get(field, [])}
        stored = {o.option_value.strip().lower() for o in options}
        fixed = [{"id": None, "value": v, "uses": counts.get(v.lower(), 0), "uses_text": uses_text(field, counts.get(v.lower(), 0)),
                  "locked": False, "built_in": True} for v in BUILT_IN.get(field, []) if v.lower() not in stored]
        # Owners are the lab's people as well as these; a person isn't "loose".
        loose = [] if field == "owner" else sorted(
            ((value, n) for value, n in counts.items() if value not in known), key=lambda vn: -vn[1])[:8]
        out.append({
            "key": field,
            "label": label(field),
            "where": where_shown(field),
            "choices": fixed + [{"id": o.id, "value": o.option_value, "uses": counts.get(o.option_value.strip().lower(), 0),
                                 "uses_text": uses_text(field, counts.get(o.option_value.strip().lower(), 0)),
                                 "locked": locked(field, o.option_value), "built_in": built_in(field, o.option_value)}
                                for o in options],
            "loose": [{"value": _as_written(session, field, value), "uses": n, "uses_text": uses_text(field, n)}
                      for value, n in loose],
        })
    return out


def _as_written(session, field: str, lowered: str) -> str:
    """A value as the records most often write it (they may differ in case)."""
    model, column, _noun = USES[field]
    col = getattr(model, column)
    return session.scalar(select(func.trim(col)).where(func.lower(func.trim(col)) == lowered)
                          .group_by(func.trim(col)).order_by(func.count().desc()).limit(1)) or lowered


def change_records(session, field: str, old: str, new: str, what: str):
    """Write `new` where the records hold `old` (in any case), as one audit
    batch. Returns bulk.Done, or None when the column holds no records."""
    if field not in USES:
        return None
    model, column, _noun = USES[field]
    col = getattr(model, column)
    records = list(session.scalars(select(model).where(func.lower(func.trim(col)) == old.strip().lower())))
    if not records:
        return None

    def apply(record):
        if getattr(record, column) == new:
            return bulk.UNCHANGED
        setattr(record, column, new)
        return None

    table = model.__tablename__
    return bulk.run(session, records, apply, may_edit=lambda _r: True, action="update", what=what, table=table)


def reorder(session, field: str, ids: list[int]) -> int:
    """Put `field`'s choices in the order of `ids`; the rest follow, as they were."""
    options = list(session.scalars(select(DropdownOption).where(DropdownOption.field_name == field)
                                   .order_by(DropdownOption.position, DropdownOption.option_value)))
    by_id = {o.id: o for o in options}
    ordered = [by_id[i] for i in ids if i in by_id] + [o for o in options if o.id not in set(ids)]
    for place, option in enumerate(ordered, start=1):
        option.position = place
    return len(ordered)


def next_position(session, field: str) -> int:
    """A new choice goes last, once a lab has put its choices in order."""
    top = session.scalar(select(func.max(DropdownOption.position)).where(DropdownOption.field_name == field)) or 0
    return top + 1 if top else 0
