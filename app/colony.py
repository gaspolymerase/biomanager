"""How a mouse is changed: the rules every change to a mouse has, wherever
it comes from (a row of the mouse sheet, a dialog, a cage card, the API and
so an assistant, a spreadsheet import), and the moves a change makes (into
a cage, a litter, a rack position, to another lab member).

change_mouse is the whole change, rules and all; the colony's pages
(app/colony_routes.py), the API (app/api.py) and the import
(app/sheet_import.py) all call it, or populate_mouse_from_form where a
change has already been checked. A change reads a form, as a sheet row
sends it: each field with a "<name>_was" copy of what the row showed
(formutil.form_changed), so a stale row doesn't undo a colleague's edit.
"""
from __future__ import annotations

import dataclasses
import json
import re
from datetime import date

from flask import flash, g, url_for
from sqlalchemy import func, select

from . import access, custom_fields, positions, rack_layout, stewardship
from .days import fmt_day
from .formutil import form_changed
from .i18n import gettext, ngettext
from .models import CageRecord, LitterRecord, MouseRack, MouseRecord
from .services import (add_notification, apply_status_rules, cage_is_active, current_lab_usernames, get_or_create_cage,
                       get_or_create_litter, next_litter_id, next_mouse_id, parse_date, slot_order,
                       sync_mouse_transgenes, transgene_values_from_form)
from .web import stamp_updated


def set_mouse_cage(mouse: MouseRecord, cage: CageRecord | None) -> None:
    """Put `mouse` in `cage` through the foreign-key column as well as the
    relationship. The audit listener records column changes only, so a
    move made through the relationship alone would be missing from /audit
    and batch undo could not put the mouse back."""
    mouse.cage = cage
    mouse.cage_id_fk = cage.id if cage is not None else None


def set_mouse_litter(mouse: MouseRecord, litter: LitterRecord | None) -> None:
    """As set_mouse_cage, for the litter (and so the date of birth)."""
    mouse.litter = litter
    mouse.litter_id_fk = litter.id if litter is not None else None


def automatic_litter(db_session, dob: date) -> LitterRecord:
    """A litter for mice given a date of birth and no litter: one per date
    per request, numbered like any other."""
    made = g.setdefault("_automatic_litters", {})
    litter = made.get(dob)
    if litter is None or litter not in db_session:
        litter = get_or_create_litter(db_session, next_litter_id(db_session), dob)
        made[dob] = litter
    return litter


def future_birth(form, field: str = "date_of_birth", what: str = "A date of birth") -> str | None:
    """The refusal for a birth date after today, or None. Nothing is born
    tomorrow; a future date is a typo (2062 for 2026, a month and day
    swapped) that would put the weaning, genotyping and age reminders on
    the wrong days."""
    born = parse_date(form.get(field)) if field in form else None
    if born is not None and born > date.today():
        if what == "A litter's birth date":
            return gettext("A litter's birth date can't be in the future (%(day)s). Nothing was saved.",
                           day=fmt_day(born))
        return gettext("A date of birth can't be in the future (%(day)s). Nothing was saved.", day=fmt_day(born))
    return None


def _keep_lines(stored: str | None, sent: str) -> str:
    """What a one-line cell sends back for a value with line breaks is the
    value without them (browsers drop them from an <input>): that is the
    stored value unchanged, not an edit. Saving another cell of the row
    must not squash a multi-line note into one line."""
    if stored and ("\n" in stored or "\r" in stored) and sent == re.sub(r"[\r\n]", "", stored).strip():
        return stored
    return sent


def populate_mouse_from_form(db_session, mouse: MouseRecord, form, preserve_owner_on_transfer: bool = True,
                             warnings: list[str] | None = None) -> tuple[str | None, str | None]:
    """Copy what the form says onto the mouse. What it could not do (a move
    into someone's private cage, a rack position taken) goes to `warnings`,
    or is flashed; the rest is saved. change_mouse is the whole change,
    rules and all: use it to change a mouse a person asked to change."""
    warn = warnings.append if warnings is not None else (lambda text: flash(text, "error"))
    # A sheet row sends every cell, and with each a "<name>_was" copy of
    # what the row showed (form_changed): a cell it didn't change is left as
    # the database has it, so saving one cell of a row open since before a
    # colleague's edit doesn't quietly undo that edit. Dialogs send no
    # copies, so everything they show counts.
    original_owner = mouse.owner
    transfer_recipient = None
    litter_code = form.get("litter_id", "").strip()
    dob = parse_date(form.get("date_of_birth"))
    if not form_changed(form, "litter_id", "date_of_birth"):
        pass
    elif litter_code:
        set_mouse_litter(mouse, get_or_create_litter(db_session, litter_code, dob))
    elif dob is not None:
        # A mouse's date of birth lives on its litter. Given a date and no
        # litter (Add many, a spreadsheet with a dob column but no litter),
        # the mouse gets an automatic litter of that date, shared by the
        # mice born that day in this one save, so the date is never lost.
        set_mouse_litter(mouse, automatic_litter(db_session, dob))
    elif "litter_id" in form or "date_of_birth" in form:
        set_mouse_litter(mouse, None)

    # A form that does not carry the cage (a cage card's mouse row, which
    # only shows the mouse's own fields) leaves the mouse where it is.
    if ("cage_id" in form and form_changed(form, "cage_id")) or form.get("auto_new_cage") == "1":
        cage_input = form.get("cage_id", "").strip()
        existing_cage = db_session.scalar(select(CageRecord).where(CageRecord.cage_id == cage_input)) if cage_input else None
        if existing_cage is not None and existing_cage is not mouse.cage and not access.can_edit_cage(existing_cage):
            # The same rule as "Add existing mouse" on the cage: a private
            # cage takes mice only from its owner (or an admin).
            warn(gettext("Cage %(cage)s is %(owner)s’s private cage, so the mouse stays where it is. Ask them, or have the cage marked shared.", cage=existing_cage.cage_id,
                         owner=existing_cage.owner or gettext("someone else")))
        elif cage_input:
            set_mouse_cage(mouse, get_or_create_cage(db_session, cage_input))
        elif form.get("auto_new_cage") == "1":
            set_mouse_cage(mouse, get_or_create_cage(db_session, "new"))
        else:
            set_mouse_cage(mouse, None)

    if mouse.cage is not None:
        # Rack, position and location note belong to the cage; see form_changed.
        if form_changed(form, "cage_location"):
            mouse.cage.cage_location = form.get("cage_location", "").strip()
        if "cage_rack" in form and form_changed(form, "cage_rack", "cage_position"):
            error = apply_cage_position(db_session, mouse.cage, form.get("cage_rack"), form.get("cage_position"))
            if error:
                warn(error)

    stored = [mouse.transgene_1, mouse.transgene_2, mouse.transgene_3, mouse.transgene_4]
    if form_changed(form, *(f"transgene_{n}" for n in range(1, 5))):
        # One the form does not send (a cell still drawn as text, which
        # sends nothing) stays as it is.
        transgenes = [_keep_lines(old, new) if f"transgene_{n}" in form else (old or "")
                      for n, (old, new) in enumerate(zip(stored, transgene_values_from_form(form)), 1)]
        sync_mouse_transgenes(mouse, transgenes)
    if form_changed(form, "gender"):
        mouse.gender = form.get("gender", "").strip()
    if form_changed(form, "ear_tag"):
        mouse.ear_tag = form.get("ear_tag", "").strip()[:40]
    if form_changed(form, "strain"):
        mouse.strain = form.get("strain", "").strip()[:120]
    # Columns this lab added to the colony: only the ones the form sent.
    custom_fields.apply_form(mouse, form, custom_fields.fields(db_session, "colony"))
    previous_status = mouse.status
    if form_changed(form, "status"):
        mouse.status = form.get("status", "").strip()
    status_normalized = (mouse.status or "").lower()
    requested_owner = form.get("owner", "").strip() if form_changed(form, "owner") else (mouse.owner or "")
    if form_changed(form, "note"):
        mouse.note = _keep_lines(mouse.note, form.get("note", "").strip())
    # Absent (a cage card does not show it) or unchanged from its `_was`
    # copy, the stored date stands; the status rules below still stamp or
    # clear it when the status crosses into or out of an end status.
    if form_changed(form, "date_of_death"):
        mouse.date_of_death = parse_date(form.get("date_of_death"))

    lab_users = set(current_lab_usernames(db_session))
    apply_status_rules(mouse, previous_status)

    if status_normalized == "transfer" and requested_owner and requested_owner in lab_users and requested_owner != original_owner:
        transfer_recipient = requested_owner
        if preserve_owner_on_transfer:
            mouse.owner = original_owner
            transfer_note = f"Transferred to {requested_owner} on {date.today().isoformat()}."
            mouse.note = f"{mouse.note} {transfer_note}".strip()
        else:
            mouse.owner = requested_owner
    else:
        mouse.owner = requested_owner

    return transfer_recipient, original_owner


@dataclasses.dataclass
class MouseChange:
    """What change_mouse did: the refusal (nothing was changed), or what
    it could not do while saving the rest."""
    refused: str | None = None
    warnings: list[str] = dataclasses.field(default_factory=list)


def change_mouse(db_session, mouse: MouseRecord, form, confirm_cohort: bool = False) -> MouseChange:
    """Change a mouse as `form` says (a sheet row, a dialog, or the API's
    JSON as one), with every rule a change to a mouse has, wherever it comes
    from: no date of birth or death in the future, a new owner only from the
    lab, and a date of birth that belongs to a cohort changed only once the
    person confirms (`confirm_cohort`), which takes the mouse out of it.
    Leaves the session to the caller to commit, or roll back on a refusal."""
    refused = future_birth(form)
    died = parse_date(form.get("date_of_death")) if form_changed(form, "date_of_death") else None
    if not refused and died is not None and died > date.today():
        refused = gettext("A date of death can't be in the future (%(day)s). Nothing was saved.", day=fmt_day(died))
    owner = (form.get("owner") or "").strip() if form_changed(form, "owner") else ""
    if not refused and owner and owner != (mouse.owner or "") \
            and owner not in stewardship.lab_members(db_session):
        refused = stewardship.not_a_member(owner)
    original_litter_id = mouse.litter.litter_id if mouse.litter else ""
    original_dob = mouse.litter.date_of_birth if mouse.litter else None
    requested_dob = parse_date(form.get("date_of_birth"))
    keeping_same_litter = bool(original_litter_id) and (form.get("litter_id") or "").strip() == original_litter_id
    moving_cohort = keeping_same_litter and requested_dob and requested_dob != original_dob
    if not refused and moving_cohort and not confirm_cohort:
        refused = gettext("DOB belongs to the cohort. Confirm to remove this mouse from the cohort before changing DOB.")
    if refused:
        return MouseChange(refused)
    done = MouseChange()
    recipient, sender = populate_mouse_from_form(db_session, mouse, form, warnings=done.warnings)
    if moving_cohort:
        if mouse.litter is not None and mouse.litter.litter_id == original_litter_id:
            mouse.litter.date_of_birth = original_dob
        set_mouse_litter(mouse, get_or_create_litter(db_session, next_litter_id(db_session), requested_dob))
    if recipient:
        create_transfer_copy(db_session, mouse, recipient, sender)
    stamp_updated(mouse)
    return done


def create_transfer_copy(db_session, source_mouse: MouseRecord, recipient_username: str, sender_username: str | None) -> MouseRecord:
    copied_mouse = MouseRecord(
        mouse_id=next_mouse_id(db_session),
        gender=source_mouse.gender,
        status="experiment",
        owner=recipient_username,
        note=f"Transferred from {sender_username or source_mouse.owner} on {date.today().isoformat()}.",
        date_of_death=None,
    )
    set_mouse_litter(copied_mouse, source_mouse.litter)
    set_mouse_cage(copied_mouse, get_or_create_cage(db_session, "new"))
    sync_mouse_transgenes(
        copied_mouse,
        [source_mouse.transgene_1, source_mouse.transgene_2, source_mouse.transgene_3, source_mouse.transgene_4],
    )
    db_session.add(copied_mouse)
    add_notification(
        db_session,
        recipient_username,
        title="Mouse transfer received",
        message="Mouse %(mouse)s was transferred to you. A new record %(copy)s was created in your colony.",
        message_values={"mouse": source_mouse.mouse_id, "copy": copied_mouse.mouse_id},
        category="transfer",
        link=url_for("colony", view="mice", scope="mine"),
        actor=sender_username or "",
    )
    return copied_mouse


def mouse_rack_from_form(db_session, rack, form) -> tuple[str | None, dict | None]:
    """Check the rack dialog's form and set it on `rack`: (an error, None),
    or (None, the layout change that moves cages in it, if any). A new
    layout may rename the places of cages in it or leave them out; the
    form says whether each keeps its name or its cell (app/rack_layout.py),
    and they move with the rack's change, as one batch."""
    name = (form.get("name") or "").strip()
    if not name:
        return gettext("A rack needs a name."), None
    clash = db_session.scalar(select(MouseRack.id).where(
        func.lower(MouseRack.name) == name.lower(), MouseRack.id != (rack.id or 0)))
    if clash:
        return gettext("There is already a rack called %(name)s.", name=name), None
    try:
        rows = int(form.get("rows") or 8)
        cols = int(form.get("cols") or 10)
    except ValueError:
        return gettext("Rows and columns must be whole numbers."), None
    new = rack_layout.Layout(max(1, min(26, rows)), max(1, min(40, cols)), rack_layout.form_naming(form, rack))
    change, refused = rack_layout.check(db_session, rack_layout.kind("mouse_rack"), rack, new,
                                        form.get(rack_layout.FIELD))
    if refused:
        return refused, None
    with rack_layout.batch(db_session, change):
        rack.name = name
        rack.rows, rack.cols = new.rows, new.cols
        rack.room = (form.get("room") or "").strip()
        rack.naming = json.dumps(new.naming)
        rack_layout.apply(db_session, change)
    return None, change


def apply_cage_position(db_session, cage, rack_raw, position_raw) -> str | None:
    """Put `cage` in the rack and position a person typed; return an error
    message instead of guessing. An empty rack takes the cage out of racks;
    a rack with no position keeps it in that rack but unplaced. A position
    another cage holds takes this one too (a cage is often set up there
    before the old one is ended), with a warning when one there is active;
    that cage stays where it is (dragging on the grid is how you swap)."""
    rack_raw = str(rack_raw or "").strip()
    position_raw = str(position_raw or "").strip()
    if not rack_raw:
        cage.rack_id_fk = cage.rack_row = cage.rack_col = None
        return None
    rack = db_session.get(MouseRack, int(rack_raw)) if rack_raw.isdigit() else db_session.scalar(
        select(MouseRack).where(func.lower(MouseRack.name) == rack_raw.lower()))
    if rack is None:
        return gettext("There is no rack called %(rack)s.", rack=rack_raw)
    if not position_raw:
        cage.rack_id_fk, cage.rack_row, cage.rack_col = rack.id, None, None
        return None
    cell = positions.parse(position_raw, rack.naming, rack.rows, rack.cols)
    if cell is None:
        first = positions.label(1, 1, rack.naming, rack.cols)
        last = positions.label(rack.rows, rack.cols, rack.naming, rack.cols)
        return gettext("“%(position)s” is not a position in rack %(rack)s (%(first)s–%(last)s).",
                       position=position_raw, rack=rack.name, first=first, last=last)
    moved = (cage.rack_id_fk, cage.rack_row, cage.rack_col) != (rack.id, *cell)
    cage.rack_id_fk, (cage.rack_row, cage.rack_col) = rack.id, cell
    others = [c for c in slot_order(db_session.scalars(select(CageRecord).where(
        CageRecord.rack_id_fk == rack.id, CageRecord.rack_row == cell[0],
        CageRecord.rack_col == cell[1], CageRecord.id != (cage.id or 0)))) if cage_is_active(c)]
    if moved and others:
        flash(ngettext("%(rack)s · %(position)s also holds cage %(cages)s (active); the rack will show the latest and mark the position with !",
                       "%(rack)s · %(position)s also holds cages %(cages)s (active); the rack will show the latest and mark the position with !",
                       len(others), rack=rack.name, position=positions.label(*cell, rack.naming, rack.cols),
                       cages=", ".join(c.cage_id for c in others)), "warning")
    return None
