"""How an inventory item is changed: the rules every change to a reagent,
an antibody, a sample or any item has, wherever it comes from (a row of
the sheet, the dialog, Add many, a batch, the spreadsheet import, an
experiment's step, the API). item_from_form reads a form as the pages send
it; change_item_values takes the API's plain values and builds that form.
"""
from __future__ import annotations

import json
import re
from datetime import date

from sqlalchemy import select
from werkzeug.datastructures import MultiDict

from . import access, i18n, stewardship
from . import groups as project_groups
from . import inventory as presets
from . import inventory_service as svc
from .formutil import MAX_ID, form_changed
from .i18n import gettext
from .models import InventoryItem, InventoryRack


class Refused(Exception):
    """A save that must not happen at all: nothing is written."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _date(raw) -> date | None:
    """A date from YYYY-MM-DD; blank is None; anything else is refused
    rather than silently clearing what was stored."""
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise Refused(gettext("“%(value)s” is not a date. Use YYYY-MM-DD.", value=raw)) from None


def _tr(value) -> str:
    """A database's own word (its noun, a column's or status's name) in the
    page's language; what a lab typed comes back as typed."""
    return i18n.translate_value(value, "inventory")


MOUSE_SOURCE_KINDS = ("mouse", "colony")


def _mouse_number(ref: str) -> int | None:
    """The mouse ID a typed source names, or None: not a number, or one
    beyond what the database holds (no colony mouse, not an error)."""
    ref = str(ref or "").strip()
    if ref.isascii() and ref.isdigit() and int(ref) <= MAX_ID:
        return int(ref)
    return None


def _mouse_exists(session, ref: str) -> bool:
    from .models import MouseRecord

    number = _mouse_number(ref)
    return number is not None and session.scalar(
        select(MouseRecord.id).where(MouseRecord.mouse_id == number)) is not None


def _manage_denied(mv, item: InventoryItem, what: str) -> str:
    """`what`: "owner", "personal" or "delete"."""
    if not access.can_edit_item(item):
        return access.reason_denied(item, noun=mv.item_noun)
    values = {"noun": _tr(mv.item_noun), "who": item.owner or gettext("its owner")}
    if what == "owner":
        return gettext("Anyone can edit a lab-common %(noun)s, but only %(who)s or an admin can change who it belongs to.", **values)
    if what == "personal":
        return gettext("Anyone can edit a lab-common %(noun)s, but only %(who)s or an admin can make it personal.",
                       **values)
    return gettext("Anyone can edit a lab-common %(noun)s, but only %(who)s or an admin can delete it.", **values)


def several(values, options) -> str:
    """The ticked choices of a multiselect field as one text, in the order of
    its choices ("flammable, explosive precursor"); a value that isn't one of
    them any more (from before, or an import) is kept after them. A box
    unticked leaves it out: the form also sends attr_<key>__several, so no box
    ticked still saves as empty."""
    picked = [part.strip() for v in values for part in str(v).split(",") if part.strip()]
    return ", ".join([o for o in options if o in picked] + [v for v in dict.fromkeys(picked) if v not in options])


def _column_value(item: InventoryItem, key: str) -> str:
    """A column as text, by its form name (attr_<key> for custom fields)."""
    value = item.attrs_dict.get(key[5:]) if key.startswith("attr_") else getattr(item, key, "")
    return "" if value is None else str(value).strip()


def _and(words: list[str]) -> str:
    if len(words) == 1:
        return words[0]
    return gettext("%(first)s and %(last)s", first=gettext(", ").join(words[:-1]), last=words[-1])


def item_from_form(session, mv, item: InventoryItem, form, creating: bool = False) -> tuple[str | None, list[str]]:
    """Copy the submitted fields onto `item` (only fields the form sent).

    Raises Refused when the save must not happen (a blank order name, a
    date that is not a date, an unknown status, a change the user may not
    make); the caller rolls back. Returns (a position problem, notes):
    a position that cannot be taken leaves the rest saved."""
    notes: list[str] = []
    manage = creating or access.can_manage(item)
    me = access.username()
    stock_was = (item.status, item.quantity, item.low_at)
    was = {key: _column_value(item, key) for key in mv.required}

    def text(name, limit=200):
        if name in form:
            setattr(item, name, (form.get(name) or "").strip()[:limit])

    if "name" in form:
        name = (form.get("name") or "").strip()[:200]
        if not name and mv.row.kind == "orders":
            raise Refused(gettext("An order needs an item name."))
        item.name = name
    text("category", limit=80)

    if "owner" in form:
        owner = (form.get("owner") or "").strip()[:80]
        if creating and not access.is_admin() and owner != me:
            if owner:
                notes.append(gettext("Only an admin can add a %(noun)s for someone else, so this one is yours.",
                                     noun=_tr(mv.item_noun)))
            owner = me
        if owner != (item.owner or ""):
            if not manage:
                raise Refused(_manage_denied(mv, item, "owner"), 403)
            refused = stewardship.change_owner(session, item, owner, mv.item_noun, item.name)
            if refused:
                raise Refused(refused)
    if "is_shared" in form and project_groups.differs(item, form.get("is_shared")):
        if not manage:
            raise Refused(_manage_denied(mv, item, "personal"), 403)
        refused = project_groups.apply(item, form.get("is_shared"))
        if refused:
            raise Refused(refused, 403)

    for name, limit in (("quantity", 60), ("unit", 30), ("vendor", 120), ("catalog_number", 120),
                        ("lot", 120), ("location_note", 200)):
        text(name, limit=limit)
    if "low_at" in form and svc.tracks_low(mv):
        raw = (form.get("low_at") or "").strip()
        level = svc.amount(raw) if raw else None
        if raw and level is None:
            raise Refused(gettext("Low at is a number, in the quantity's unit: “%(value)s” isn't one.", value=raw))
        item.low_at = level
    if "pack_size" in form and svc.has_pack_size(mv):
        raw = (form.get("pack_size") or "").strip()
        if raw and svc.pack_size(raw) is None:
            raise Refused(gettext("Pack size is what one of its unit holds, a number and a unit (10 mL): “%(value)s” isn't one.", value=raw))
        item.pack_size = svc.pack_size_text(raw)[:40]
    for name in ("received_on", "expires_on"):
        if name in form and form_changed(form, name):
            setattr(item, name, _date(form.get(name)))
    if "notes" in form:
        item.notes = (form.get("notes") or "").strip()

    attrs = item.attrs_dict
    before = json.dumps(attrs, sort_keys=True)
    for field in mv.fields:
        k = field["key"]
        if field["type"] == "source":
            if f"attr_{k}_kind" in form or f"attr_{k}_ref" in form:
                value = {"kind": (form.get(f"attr_{k}_kind") or "").strip(),
                         "ref": (form.get(f"attr_{k}_ref") or "").strip()}
                old = attrs.get(k) if isinstance(attrs.get(k), dict) else {}
                if (value["kind"] in MOUSE_SOURCE_KINDS and value["ref"] and value != old
                        and not _mouse_exists(session, value["ref"])):
                    notes.append(gettext("There is no mouse %(id)s in the colony; the source was saved as typed.",
                                         id=value["ref"]))
                attrs[k] = value
        elif field["type"] == "multiselect" and (f"attr_{k}" in form or f"attr_{k}__several" in form):
            attrs[k] = several(form.getlist(f"attr_{k}"), field["options"])
        elif f"attr_{k}" in form:
            value = (form.get(f"attr_{k}") or "").strip()
            if field["type"] == "date" and value:
                value = _date(value).isoformat()
            if field["type"] == "number" and value:
                # "2,01" is a decimal comma (kept as 2.01); "1,000" and
                # "12,500" are thousands, as before.
                if re.fullmatch(r"-?\d+,\d+", value) and not re.fullmatch(r"-?\d{1,3}(,\d{3})+", value):
                    value = value.replace(",", ".")
                try:
                    float(value.replace(",", ""))
                except ValueError:
                    raise Refused(gettext("%(field)s is a number column: “%(value)s” isn't a number.",
                                          field=_tr(field["label"]), value=value))
            if field["type"] == "plasmid" and value:
                # Kept as the plasmid's number, which the sheet links to;
                # a name or "#42 · pAAV…" from the list is read the same way.
                plasmid = svc.resolve_plasmid(session, value)
                if plasmid is not None:
                    value = str(plasmid.plasmid_id)
                elif value != str(attrs.get(k, "")):
                    notes.append(gettext("There is no plasmid “%(value)s” in Plasmids; %(field)s was saved as typed.",
                                         value=value, field=_tr(field["label"])))
            attrs[k] = value
    if json.dumps(attrs, sort_keys=True) != before:
        item.attrs = json.dumps(attrs)

    # A new entry needs every required column; an old one may not lose one
    # (but one that never had it can still be saved).
    # A column not used for its type (a 260/280 on tissue) isn't asked for.
    unused = {f"attr_{f['key']}" for f in mv.fields if not presets.used_for(f, item.category)}
    missing = [_tr(label) for key, label in mv.required_labels.items()
               if not _column_value(item, key) and (creating or was[key]) and key not in unused]
    if missing:
        if creating:
            if mv.item_noun[:1] in "aeiou":
                raise Refused(gettext("Fill in %(fields)s to add an %(noun)s.", fields=_and(missing),
                                      noun=_tr(mv.item_noun)))
            raise Refused(gettext("Fill in %(fields)s to add a %(noun)s.", fields=_and(missing),
                                  noun=_tr(mv.item_noun)))
        raise Refused(gettext("%(fields)s can’t be left empty.", fields=_and(missing)))

    # Status last: it may fill the received date or stamp a used-up date.
    place_was = (str(item.rack_id_fk or ""), svc.rack_label(item))
    if "status" in form and (creating or form_changed(form, "status")):
        problem = svc.apply_status(mv, item, form.get("status"))
        if problem:
            raise Refused(problem)
    # A quantity or Low at changed, and the status was not set by hand in
    # the same save: it follows the quantity (in stock, low, empty).
    if item.status == stock_was[0] and (item.quantity, item.low_at) != stock_was[1:]:
        svc.follow_stock_level(mv, item)
    # Used up just freed its cell: the dialog still showing that box and
    # position is not a request to put it back.
    freed = place_was[0] and not item.rack_id_fk
    if freed and (form.get("rack_id", "").strip(), form.get("position", "").strip()) in (place_was, (place_was[0], "")):
        return None, notes

    if mv.has("storage") and "rack_id" in form and form_changed(form, "rack_id", "position"):
        return svc.apply_position(session, item, form.get("rack_id"), form.get("position")), notes
    return None, notes


# What an item is changed by from outside the pages (the API), as plain values.
ITEM_VALUES = ("name", "category", "status", "owner", "shared", "quantity", "unit", "low_at", "pack_size", "vendor",
               "catalog_number", "lot", "box", "position", "location_note", "received_on", "expires_on", "notes",
               "fields")


def change_item_values(session, module, mv, item: InventoryItem, values: dict,
                       creating: bool = False) -> tuple[str | None, list[str]]:
    """item_from_form with plain `values` by ITEM_VALUES name; a field left
    out stays as it is. "shared" is true or false, "box" the box's name, and
    "fields" the database's own columns by key (a source as {kind, ref}).
    Raises Refused (400), in words, for a field this item hasn't; otherwise
    as item_from_form: Refused when nothing may be saved, else (a position
    problem or None, notes)."""
    unknown = sorted(set(values) - set(ITEM_VALUES))
    if unknown:
        raise Refused(f"A {mv.item_noun} has no {', '.join(unknown)}; it has {', '.join(ITEM_VALUES)}.", 400)
    form = MultiDict()
    for k, v in values.items():
        if k in ("box", "position", "fields", "shared"):
            continue
        form[k] = "" if v is None else str(v)
    if "shared" in values:
        # true keeps a project group's stock its group's (app/groups.py).
        keep = item.id is not None and values["shared"] and item.is_shared
        form["is_shared"] = project_groups.record_value(item) if keep else ("1" if values["shared"] else "0")
    known = {f["key"]: f for f in mv.fields}
    for k, v in (values.get("fields") or {}).items():
        field = known.get(k)
        if field is None:
            raise Refused(f"{module.label} has no field {k}; it has {', '.join(known) or 'none'}.", 400)
        if field["type"] == "source":
            v = v if isinstance(v, dict) else {"kind": "", "ref": str(v or "")}
            form[f"attr_{k}_kind"], form[f"attr_{k}_ref"] = str(v.get("kind") or ""), str(v.get("ref") or "")
        else:
            form[f"attr_{k}"] = "" if v is None else str(v)
    if "box" in values or "position" in values:
        box = values.get("box", item.rack.name if item.rack else "")
        rack = session.scalar(select(InventoryRack).where(InventoryRack.module_id_fk == module.id,
                                                          InventoryRack.name == box)) if box else None
        if box and rack is None:
            raise Refused(f"{module.label} has no box {box}.", 400)
        form["rack_id"] = str(rack.id) if rack else ""
        form["position"] = str(values.get("position") or "")
    return item_from_form(session, mv, item, form, creating=creating)
