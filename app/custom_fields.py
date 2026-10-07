"""Columns a lab adds to a built-in database.

An inventory, a stock collection and any organism database have always let
a lab add its own columns. The three built-in ones — the mouse colony,
zebrafish and plasmids — could not, because their columns are real ones, so
a lab that weighs organs or records a tail-fin clip had nowhere to put it
but the notes (issue #39).

A column is a definition in `app_settings` under `custom_fields:<database>`
and a value in the record's own `attrs` JSON, which is exactly how an
inventory item has always carried its extras — the same field shapes
(`app/inventory.py`'s FIELD_TYPES), the same `attr_<key>` form names, and
the same `field_cell` macro draws them. Adding a column is a setting, not a
migration.

Nothing here knows which database it is working on beyond its key, so the
colony, zebrafish and plasmids share one path.
"""
from __future__ import annotations

import json

# Which built-in databases take custom columns, and the record each one
# hangs them on (the table a sheet's rows come from).
DATABASES = {
    "colony": "mice",
    "zebrafish": "tanks",
    "plasmids": "plasmids",
}
# What each one calls the record the columns hang on.
NOUNS = {"colony": "mouse", "zebrafish": "tank", "plasmids": "plasmid"}
SETTING = "custom_fields:%s"
# The shapes a built-in's own column takes. An inventory also offers
# "source" (which animal a sample came from) and "plasmid", which belong to
# an inventory's own records rather than to a mouse or a plasmid itself.
FIELD_TYPES = ("text", "textarea", "number", "date", "select", "user", "url")
MAX_FIELDS = 30
# A lab's column may not be called the same as one the record already has,
# or the sheet would have two columns answering to one name.
RESERVED = {
    "colony": {"id", "mouse_id", "ear_tag", "gender", "genotype", "status", "owner", "note", "notes",
               "cage", "cage_id", "litter", "litter_id", "age", "date_of_birth", "date_of_death", "dob", "dod"},
    "zebrafish": {"id", "tank_id", "line", "status", "owner", "note", "notes", "age", "date_of_birth",
                  "fish", "room", "rack", "position"},
    "plasmids": {"id", "plasmid_id", "name", "backbone", "insert_seq", "insert", "resistance", "owner",
                 "location", "notes", "note", "concentration", "a260_280", "box", "position", "sequence"},
}


def setting_key(database: str) -> str:
    return SETTING % database


def fields(session, database: str) -> list[dict]:
    """The columns this lab has added to that database, in order."""
    from . import inventory_service as inventories

    if database not in DATABASES:
        return []
    raw = inventories.get_setting(session, setting_key(database), "")
    try:
        found = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return []
    return normalise(database, found)


def normalise(database: str, raw) -> list[dict]:
    """Definitions with every key present and junk dropped, the way an
    inventory's settings are normalised."""
    out, seen = [], set()
    for f in raw if isinstance(raw, list) else []:
        if not isinstance(f, dict):
            continue
        key = str(f.get("key") or "").strip()[:60]
        label = str(f.get("label") or "").strip()[:60]
        if not key or not label or key in seen or key in RESERVED.get(database, set()):
            continue
        seen.add(key)
        ftype = f.get("type") if f.get("type") in FIELD_TYPES else "text"
        try:
            width = min(max(int(f.get("width") or 130), 60), 400)
        except (TypeError, ValueError):
            width = 130
        out.append({
            "key": key, "label": label, "type": ftype,
            "options": [str(o).strip()[:60] for o in (f.get("options") or []) if str(o).strip()][:40],
            "icon": str(f.get("icon") or "").strip()[:40],
            "width": width,
            "in_table": bool(f.get("in_table", True)),
        })
    return out[:MAX_FIELDS]


def set_fields(session, database: str, raw) -> list[dict]:
    """Keep this lab's columns for that database. Returns what was kept."""
    from . import inventory_service as inventories

    kept = normalise(database, raw)
    inventories.set_setting(session, setting_key(database), json.dumps(kept))
    return kept


def values(record) -> dict:
    """What a record carries in its own columns."""
    try:
        found = json.loads(getattr(record, "attrs", "") or "{}")
    except (TypeError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def apply_form(record, form, defined: list[dict]) -> bool:
    """Copy `attr_<key>` from a submitted form onto the record, for the
    columns that are defined. Only the ones the form sent are touched, so a
    sheet row that shows one cell never blanks the rest. Returns whether
    anything changed."""
    held = values(record)
    before = json.dumps(held, sort_keys=True)
    for field in defined:
        name = f"attr_{field['key']}"
        if name not in form:
            continue
        held[field["key"]] = str(form.get(name) or "").strip()[:2000]
    after = json.dumps(held, sort_keys=True)
    if after == before:
        return False
    record.attrs = after
    return True


def in_table(defined: list[dict]) -> list[dict]:
    return [f for f in defined if f.get("in_table", True)]
