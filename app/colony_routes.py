"""The mouse colony: mice, cages, litters, strains, racks, the colony's
experiments, its batch actions and its dropdown choices, and the helpers
behind them (who may edit a mouse, how a mouse's change is made, what a
cage's row shows).

These pages hang on the application object from app/flask_app.py and keep
the endpoint names every page links to ("colony", "create_mouse", ...);
tests/test_url_map.py holds them to it. They take what every page needs
from app/web.py and never import app.py, which imports them.
"""
from __future__ import annotations

import dataclasses
import json
import re
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from flask import Response, flash, g, jsonify, redirect, render_template, request, url_for
from markupsafe import Markup
from werkzeug.datastructures import ImmutableMultiDict
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .db import SessionLocal
from . import access, cell_text, i18n, lab, plain_records, positions
from .i18n import gettext, ngettext
from .formutil import form_changed
from . import audit
from . import choices
from . import custom_fields
from .models import (
    COLONY_VIEWS,
    CageRecord,
    DropdownOption,
    Experiment,
    ExperimentMouse,
    LitterRecord,
    CageLitter,
    MouseRack,
    MouseWeight,
    MOUSE_STATUS_OPTIONS,
    MouseRecord,
    StrainRecord,
)
from .services import (
    calculate_age_fields,
    add_notification,
    breeder_mice,
    is_breeding_cage,
    weaning_offset,
    MIN_WEAN_AGE_DAYS,
    mark_weaned,
    open_litters,
    waiting_litters,
    genotyping_offset,
    split_genotype,
    breeder_summary,
    cage_derived_dates,
    cage_is_active,
    placed_when,
    slot_order,
    current_lab_usernames,
    mouse_is_active,
    apply_status_rules,
    END_STATUSES,
    mouse_racks,
    normalize_status,
    dropdown_options_map,
    dropdown_records_map,
    export_mouse_rows,
    generate_litter_id,
    get_or_create_cage,
    get_or_create_litter,
    mouse_display_row,
    transgene_columns_used,
    next_cage_id,
    next_litter_id,
    next_mouse_id,
    reserve_cage_ids,
    reserve_mouse_ids,
    parse_date,
    recent_notifications,
    sync_mouse_transgenes,
    transgene_values_from_form,
)
from . import bulk, experiment_plan, inventory_routes, litters, rack_layout, sheet_paging, stewardship
from . import experiments as experiment_pages
from . import groups as project_groups
from .days import fmt_day
from .litters import pup_litter as cage_pup_litter
from .flask_app import app
from .web import (
    autosave_response,
    log_delete,
    login_required,
    next_number_retried,
    owner_badge_filter,
    stamp_updated,
)


# Colony sub-views: label, icon and one-line description for the segmented
# tab row on the colony page. Keyed by the view keys in models.COLONY_VIEWS.
COLONY_VIEW_META: dict[str, dict[str, str]] = {
    "mice":        {"label": "Mice", "icon": "mouse",
                    "blurb": "Every mouse record, editable in place"},
    "cages":       {"label": "Cages", "icon": "cage",
                    "blurb": "Every cage, editable in place, with its mice and breeding actions"},
    "litters":     {"label": "Litters", "icon": "baby",
                    "blurb": "Cohorts and the dates derived from their DOB"},
    "breeders":    {"label": "Breeders", "icon": "heart",
                    "blurb": "Breeding cages and their productivity"},
    "experiments": {"label": "Experiments", "icon": "flask",
                    "blurb": "Cohorts assembled for a specific experiment"},
    "strains":     {"label": "Strains", "icon": "sitemap",
                    "blurb": "Strain and allele reference list"},
    "settings":    {"label": "Dropdowns", "icon": "sliders",
                    "blurb": "The choices offered in the colony's dropdowns (statuses, purposes, strains…)"},
}


def can_edit_mouse(mouse: MouseRecord) -> bool:
    """Delegates to the single access policy in app/access.py."""
    return access.can_edit_mouse(mouse)


def can_edit_cage(cage: CageRecord) -> bool:
    return access.can_edit_cage(cage)


def deny(record, view: str = "mice"):
    """Return a response when the user may not edit `record`, else None.

    Routes call this right after loading the record:

        blocked = deny(cage, "cages")
        if blocked:
            return blocked

    Keeping it a returned response rather than an abort lets the colony's
    autosave endpoints answer in the shape their caller expects.
    """
    if record is None:
        return None
    if isinstance(record, MouseRecord):
        allowed = access.can_edit_mouse(record)
    elif isinstance(record, CageRecord):
        allowed = access.can_edit_cage(record)
    else:
        allowed = access.can_edit(record)
    if allowed:
        return None
    flash(access.reason_denied(record), "error")
    return autosave_response(view)


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


def _merged_choices(*groups) -> list[str]:
    """Unique non-empty values in first-seen order, compared without case,
    so the built-in values lead and a lab's own presets follow."""
    seen, out = set(), []
    for group in groups:
        for value in group:
            value = (value or "").strip()
            if value and value.lower() not in seen:
                seen.add(value.lower())
                out.append(value)
    return out


def _in_word_order(values) -> list[str]:
    """Values the mice hold, A to Z: the same order however the rows came
    (a sheet's page, or its rows fetched later, /colony/mice/rows)."""
    return sorted({(v or "").strip() for v in values} - {""}, key=lambda v: (v.lower(), v))


def mouse_sheet_meta(mouse_rows: list[dict], dropdowns: dict, racks=()) -> dict:
    """What the mouse sheet needs beyond the rows themselves: the choices
    for its dropdown cells, and which transgene columns hold anything (an
    empty TG3/TG4 starts hidden, and one click brings it back)."""
    return {
        "status_choices": _merged_choices(
            MOUSE_STATUS_OPTIONS, dropdowns.get("status", []),
            _in_word_order(r["status"] for r in mouse_rows)),
        "gender_choices": _merged_choices(
            ["F", "M", "Unknown"], dropdowns.get("gender", []),
            _in_word_order(r["gender"] for r in mouse_rows)),
        "tg_used": [any(r[f"transgene_{n}"] for r in mouse_rows) for n in range(1, 5)],
        "active_count": sum(1 for r in mouse_rows if r["active"]),
        "racks": [{"id": r.id, "name": r.name} for r in racks],
        "location_notes_used": any(r["cage_location"] for r in mouse_rows),
    }


# A new lab's cage purposes (CLAUDE.md, "The mouse colony's words"). The
# lab's own list is the colony's purpose choices (Configure → Dropdown
# choices), which start as these.
CAGE_PURPOSE_CHOICES = ["Breeder", "Breeding", "Experiment"]


def cage_purposes(dropdowns: dict) -> list[str]:
    """The lab's cage purposes, as set in the colony's dropdown choices."""
    return _merged_choices(dropdowns.get("purpose") or CAGE_PURPOSE_CHOICES)
# A litter still counts as the cage's pups up to this age (days).
def _sex_label(females: int, males: int, other: int) -> str:
    """"2♀ 1♂" for the living mice in a cage; unknown sex shown as "?"."""
    parts = [f"{n}{sign}" for n, sign in ((females, "♀"), (males, "♂"), (other, "?")) if n]
    return " ".join(parts)


def cage_wean_due(cage) -> tuple[str, str]:
    """(date, state): the lab's weaning day from the cage's litter-born date,
    else from its pups' litter; state is "overdue", "soon" (within 3 days)
    or ""."""
    born = cage.date_give_birth
    if born is None:
        litter = cage_pup_litter(cage)
        born = litter.date_of_birth if litter else None
    if born is None:
        return "", ""
    due = born + timedelta(days=weaning_offset())
    left = (due - date.today()).days
    return due.isoformat(), ("overdue" if left < 0 else "soon" if left <= 3 else "")


def cage_genotype_auto(cage) -> str:
    """What the living mice carry, most common first: "Ai14; Cre ×2 · Ai14"."""
    counts: dict[str, int] = {}
    for mouse in cage.mice:
        if mouse_is_active(mouse):
            key = (mouse.genotype or "").strip()
            if key:
                counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return " · ".join(f"{name} ×{n}" if n > 1 else name for name, n in ordered)


def share_lock(cage) -> str:
    """Why this person can't make the cage shared or personal ("" if they
    can): its owner or an admin decides."""
    if not access.can_set_sharing(cage):
        return gettext("Only %(owner)s or an admin can change this", owner=cage.owner or gettext("its owner"))
    return ""


def cage_sheet_values(cage) -> dict:
    """The cage's cells as the sheet shows them. The update route returns
    these after a save; static/sheet.js writes them back into the row and
    refreshes each `<name>_was` copy."""
    wean, wean_state = cage_wean_due(cage)
    return {
        "cage_id": cage.cage_id,
        "rack_id": cage.rack_id_fk or "",
        "position": cage_position_label(cage),
        "purpose": cage.purpose or "",
        "owner": cage.owner or "",
        "is_shared": project_groups.record_value(cage) if access.is_shared_cage(cage) else "0",
        "cage_location": cage.cage_location or "",
        "genotype_summary": cage.genotype_summary or "",
        "notes": cage.notes or "",
        "date_give_birth": cage.date_give_birth.isoformat() if cage.date_give_birth else "",
        "wean_due": wean,
        "wean_state": wean_state,
        # Why the Shared cell can't be changed here, if it can't: only its
        # owner or an admin shares a cage.
        "share_lock": share_lock(cage),
        "breeding": "1" if is_breeding_cage(cage.purpose) else "0",
    }


def cage_purpose_chips(cage_rows, purposes: list[str]) -> list[tuple[str, str, int]]:
    """(key, label, how many) for each of the lab's cage purposes, in the
    order its dropdown choices list them; the key is the purpose in lower
    case, as rows carry it. Only active cages count: a purpose's chip shows
    its cages with living mice, and past cages stay under All."""
    counts: dict[str, int] = {}
    for r in cage_rows:
        if not r.get("active"):
            continue
        key = (r.get("purpose") or "").strip().lower()
        counts[key] = counts.get(key, 0) + 1
    return [(label.lower(), label, counts.get(label.lower(), 0)) for label in purposes]


def cage_stub_row(cage) -> dict:
    """What cage_stubs (and the sheet's chips and counts) read of a cage the
    sheet does not draw: cage_sheet_row's cells, without each mouse's row,
    the litters to wean or who may edit what."""
    living = [m for m in cage.mice if mouse_is_active(m)]
    females = sum(1 for m in living if m.gender == "F")
    males = sum(1 for m in living if m.gender == "M")
    pups = cage_pup_litter(cage)
    shared = access.is_shared_cage(cage)
    return {
        **cage_sheet_values(cage),
        "id": cage.id,
        "active": bool(living),
        "rack_name": cage.rack.name if cage.rack else "",
        "card_id": cage.card_id,
        "genotype_auto": cage_genotype_auto(cage),
        "share_value": project_groups.record_value(cage) if shared else "0",
        "mine": access.owns(cage),
        "live_count": len(living),
        "total_count": len(cage.mice),
        "sex_label": _sex_label(females, males, len(living) - females - males),
        "pup_litter": pups.litter_id if pups else "",
        "mice": [{"mouse_id": m.mouse_id} for m in sorted(cage.mice, key=lambda item: item.mouse_id)],
    }


def cage_sheet_row(cage) -> dict:
    """One row of the cage sheet, with the cage's mice for its sub-row."""
    derived = cage_derived_dates(cage)
    living = [m for m in cage.mice if mouse_is_active(m)]
    females = sum(1 for m in living if m.gender == "F")
    males = sum(1 for m in living if m.gender == "M")
    ordered = sorted(cage.mice, key=lambda item: item.mouse_id)
    pups = cage_pup_litter(cage)
    values = cage_sheet_values(cage)
    # What the Wean dialog starts from: the pups' birth date (it asks
    # before weaning under P18) and the pups themselves, by sex. A cage
    # with two litters waiting has them for each (`litters`), and the
    # dialogs ask which; the oldest comes first.
    waiting = waiting_litters(cage)
    wean_born = (waiting[0].born if waiting else cage.date_give_birth) or (pups.date_of_birth if pups else None)

    def pups_by_sex(born) -> dict[str, str]:
        out: dict[str, list[str]] = {"F": [], "M": [], "": []}
        for m in ordered:
            if (mouse_is_active(m) and born and m.litter is not None
                    and m.litter.date_of_birth == born and m.litter.weaned_on is None):
                out[m.gender if m.gender in ("F", "M") else ""].append(str(m.mouse_id))
        return {sex: ", ".join(ids) for sex, ids in out.items()}

    today = date.today()

    def litter_row(cl) -> dict:
        due = cl.born + timedelta(days=weaning_offset())
        left = (due - today).days
        return {"born": cl.born.isoformat(), "label": fmt_day(cl.born), "age": (today - cl.born).days,
                "pups": cl.pups or "", "dam": cl.dam or "", "sire": cl.sire or "", "litter": cl.litter.litter_id if cl.litter else "",
                "genotyped": cl.litter_id_fk is not None,
                "wean_due": fmt_day(due), "wean_state": "overdue" if left < 0 else "soon" if left <= 3 else "",
                "genotyping_due": fmt_day(cl.born + timedelta(days=genotyping_offset())),
                "wean_pups": pups_by_sex(cl.born)}
    role = getattr(g.user, "role", None) if g.user else None
    return {
        **values,
        "id": cage.id,
        "cage_id": cage.cage_id,
        "active": cage_is_active(cage),
        "rack_name": cage.rack.name if cage.rack else "",
        "genotyping_date": derived["genotyping_date"],
        "weaning_date": derived["weaning_date"],
        "card_id": cage.card_id,
        "genotype_auto": cage_genotype_auto(cage),
        "location_detail": cage.location_detail,
        "room": cage.room,
        "shared": access.is_shared_cage(cage),
        "share_value": project_groups.record_value(cage) if access.is_shared_cage(cage) else "0",
        "share_group_id": cage.share_group_id,
        "mine": access.owns(cage),
        "can_edit": access.can_edit_cage(cage),
        # Giving it to someone else is its owner's, an admin's or animal care's.
        "can_reassign": access.can_manage(cage) or access.is_care(),
        # Litter born, Genotyping and Wean: a mating (Breeding) cage's only.
        "can_breed": is_breeding_cage(cage.purpose),
        "live_count": len(living),
        "total_count": len(cage.mice),
        "sex_label": _sex_label(females, males, len(living) - females - males),
        "pup_litter": pups.litter_id if pups else "",
        "pup_dob": pups.date_of_birth.isoformat() if pups else "",
        "wean_born": wean_born.isoformat() if wean_born else "",
        "wean_age": (date.today() - wean_born).days if wean_born else "",
        "wean_pups": pups_by_sex(wean_born),
        # Each litter born in the cage still to wean, oldest first: the card
        # lists them when there are two, and Genotyping and Wean ask which;
        # Litter born asks about the ones not weaned (`open_litters`).
        "litters": [litter_row(cl) for cl in waiting],
        "open_litters": [{"born": cl.born.isoformat(), "label": fmt_day(cl.born)} for cl in open_litters(cage)],
        "default_father": next((str(m.mouse_id) for m in ordered if m.gender == "M"), ""),
        "default_mother": next((str(m.mouse_id) for m in ordered if m.gender == "F"), ""),
        "mice": [dict(mouse_display_row(m, access.username(), role), editable=can_edit_mouse(m))
                 for m in ordered],
        "payload": {
            "id": cage.id, "_label": cage.cage_id, "_locked": not access.can_edit_cage(cage),
            "rack_id": cage.rack_id_fk or "", "position": values["position"],
            "cage_location": cage.cage_location, "purpose": cage.purpose, "room": cage.room,
            "card_id": cage.card_id, "genotype_summary": cage.genotype_summary,
            "location_detail": cage.location_detail, "notes": cage.notes,
            "date_give_birth": values["date_give_birth"],
        },
    }


RECENT_DAYS = 90   # ended mice, empty cages and old litters shown without "Show all"


def colony_context(active_view: str, scope: str = access.DEFAULT_SCOPE, show_ended: bool = False,
                   only: set[int] | None = None, rack_tiles: bool = False) -> dict[str, object]:
    """Build the colony page context.

    `scope` filters which slice of the colony is listed — your own animals,
    the shared cages, or everything. It is a view filter only: what
    you may *edit* is decided per record by app/access.py, and is the same
    whichever scope you are looking at.

    Only the open tab's rows are built, with their cages, racks and litters
    loaded in a few queries rather than one per row. A colony keeps every
    mouse it ever had, so the Mice, Cages and Litters tabs show the living
    and what ended in the last RECENT_DAYS days; `show_ended` shows it all.
    `only`: the cages or litters to draw, by id (a long sheet's rows,
    fetched as they are shown: colony_cage_rows, colony_litter_rows).
    `rack_tiles`: the rack grid's tiles too (colony_cage_grid); else only
    its racks, which the sheet's menus and buttons need.
    """
    from sqlalchemy.orm import selectinload
    me = g.user.username if g.user else ""
    recent = date.today() - timedelta(days=RECENT_DAYS)
    with SessionLocal() as db_session:
        # The IDs the New dialogs offer, first: each flushes the session,
        # which once it holds a sheet's thousands of records reads them all.
        next_mouse_id_value = next_mouse_id(db_session)
        # Each reads every cage or litter ID: only the tab whose New dialog says it.
        next_cage_id_value = next_cage_id(db_session) if active_view == "cages" else ""
        next_litter_id_value = next_litter_id(db_session) if active_view == "litters" else ""
        count_of = lambda stmt: db_session.scalar(stmt) or 0  # noqa: E731
        # count(*), not count(id): SQLite answers it without reading every row.
        totals = {
            "all_mice": count_of(select(func.count()).select_from(MouseRecord)),
            "all_cages": count_of(select(func.count()).select_from(CageRecord)),
            "my_mice": count_of(select(func.count(MouseRecord.id)).where(MouseRecord.owner == me)),
            "my_cages": count_of(select(func.count(CageRecord.id)).where(CageRecord.owner == me)),
            "shared_cages": count_of(select(func.count(CageRecord.id)).where(CageRecord.is_shared.is_(True))),
        }
        my_groups = project_groups.ids_of()
        if my_groups:
            group_cages = select(CageRecord.id).where(
                CageRecord.is_shared.is_(True), CageRecord.share_group_id.in_(sorted(my_groups)))
            totals["group_mice"] = count_of(select(func.count(MouseRecord.id)).where(
                MouseRecord.owner.in_(sorted(project_groups.colleagues())) | MouseRecord.cage_id_fk.in_(group_cages)))
        def sheet_mice(where=None):
            """The mice the mouse sheet lists: in scope and, unless asked
            for, none that ended long ago (`where`: of these only). Read as
            plain rows (app/plain_records.py), each with its cage, rack and
            litter: the ORM took most of the page's time over them."""
            conditions = [] if where is None else [where]
            # Only the mice the scope can list are loaded (a big colony keeps
            # thousands of everyone's): a superset, which access.in_scope
            # below then decides on exactly.
            conditions.append(access.scope_superset(
                MouseRecord.owner, MouseRecord.cage_id_fk.in_(select(CageRecord.id).where(CageRecord.is_shared.is_(True))),
                scope))
            if not show_ended:
                conditions.append(MouseRecord.date_of_death.is_(None) | (MouseRecord.date_of_death >= recent))
            # A cage's sharing is the same for each of its mice: asked once.
            shared_with: dict[int, tuple] = {}

            def sharing(cage) -> tuple:
                key = cage.id if cage is not None else None
                if key not in shared_with:
                    shared_with[key] = (access.cage_shared_with(cage), access.cage_group(cage))
                return shared_with[key]

            listed = []
            for m in plain_records.mice(db_session, *conditions):
                shared, group_id = sharing(m.cage)
                if access.in_scope(m, scope, shared=shared, group_id=group_id):
                    listed.append(m)
            return listed

        mice, hidden_mice = [], 0
        if active_view == "mice":
            if not show_ended:
                # Every mouse but the few alive or ended lately (ix_mice_date_of_death).
                hidden_mice = totals["all_mice"] - count_of(select(func.count()).select_from(MouseRecord).where(
                    MouseRecord.date_of_death.is_(None) | (MouseRecord.date_of_death >= recent)))
            mice = sheet_mice()
        cages, hidden_cages = [], 0
        if active_view == "cages":
            # Read as plain rows (app/plain_records.py), each with its rack
            # and its mice: the ORM took a third of the sheet's time. The
            # cages drawn are read through the ORM below, with their litters.
            cage_where = []
            if not show_ended:
                # Only cages that may be shown are loaded, with their mice: a
                # mouse alive or died lately, or made lately (a colony keeps
                # every cage it had). The rule itself is below.
                cage_where.append(
                    CageRecord.id.in_(select(MouseRecord.cage_id_fk).where(
                        MouseRecord.date_of_death.is_(None) | (MouseRecord.date_of_death >= recent)))
                    | (CageRecord.created_at >= datetime.combine(recent, datetime.min.time())))
            if only is not None:
                cage_where.append(CageRecord.id.in_(sorted(only)))
            # Only the cages the scope can list are loaded, with their mice
            # and litters: a superset, which access.in_scope below decides on.
            cage_where.append(access.scope_superset(CageRecord.owner, CageRecord.is_shared.is_(True), scope))
            all_cages = plain_records.cages(db_session, *cage_where, order_by=[CageRecord.cage_id])
            if not show_ended:
                def shown(c) -> bool:
                    return (cage_is_active(c) or bool(c.created_at and c.created_at.date() >= recent)
                            or any(m.date_of_death and m.date_of_death >= recent for m in c.mice))
                all_cages = [c for c in all_cages if shown(c)]
                # How many the rule leaves out of the whole colony, whatever
                # the scope: from each cage's mice's dates and statuses only.
                kept = {cid for cid, in db_session.execute(select(CageRecord.id).where(
                    CageRecord.created_at >= datetime.combine(recent, datetime.min.time())))}
                for cid, died, status in db_session.execute(
                        select(MouseRecord.cage_id_fk, MouseRecord.date_of_death, MouseRecord.status)
                        .where(MouseRecord.cage_id_fk.is_not(None),
                               MouseRecord.date_of_death.is_(None) | (MouseRecord.date_of_death >= recent))):
                    if cid not in kept and (died is not None or mouse_is_active(
                            SimpleNamespace(date_of_death=died, status=status))):
                        kept.add(cid)
                hidden_cages = totals["all_cages"] - len(kept)
            # Cage 2 before cage 10: by number, not letter by letter.
            cages = positions.natural_sorted(
                [c for c in all_cages if access.in_scope(c, scope, shared=access.cage_shared_with(c),
                                                         group_id=access.cage_group(c))], key=lambda c: c.cage_id)
        litters, drawn_litters, hidden_litters = [], [], 0
        if active_view == "litters":
            # Of each pup, only what its cell shows and who may edit it, read
            # as plain rows (app/plain_records.py): a long colony's litters
            # hold tens of thousands of mice.
            litter_where = []
            if not show_ended:
                year_ago = date.today() - timedelta(days=365)
                with_living = select(MouseRecord.litter_id_fk).where(MouseRecord.date_of_death.is_(None))
                visible = (LitterRecord.date_of_birth.is_(None) | (LitterRecord.date_of_birth >= year_ago)
                           | LitterRecord.id.in_(with_living))
                hidden_litters = count_of(select(func.count()).select_from(LitterRecord).where(~visible))
                litter_where.append(visible)
            if only is not None:
                litter_where.append(LitterRecord.id.in_(sorted(only)))
            litters = plain_records.litters(db_session, *litter_where)
            # L-2 before L-10: by number, not letter by letter (and two that
            # number alike, by their IDs).
            litters.sort(key=lambda l: l.litter_id)
            litters = positions.natural_sorted(litters, key=lambda l: l.litter_id)
            # A long sheet draws its first page (_colony_page_context); the
            # others are stubs (litter_stubs), which read a pup's number and
            # sex only. Each pup in full, and who may edit the litter, only
            # for the rows drawn. Who may edit a litter is who may edit all
            # its pups, which asks each pup's cage (access.can_edit_litter):
            # not for those who may edit any mouse.
            drawn_litters = litters if len(litters) <= inventory_routes.LAZY_OVER \
                else litters[:inventory_routes.FIRST_PAGE]
            plain_records.full_pups(db_session, drawn_litters, cages=not (access.is_admin() or access.is_care()))
        strains = positions.natural_sorted(db_session.scalars(select(StrainRecord)).all(), key=lambda s: s.strain_name)
        dropdowns = dropdown_options_map(db_session)
        dropdown_records = dropdown_records_map(db_session)
        # The Dropdowns tab: each column's choices and where they are used (app/choices.py).
        choice_columns = choices.columns(db_session) if active_view == "settings" else []
        usernames = current_lab_usernames(db_session)
        notifications = recent_notifications(db_session, g.user.username if g.user else "", limit=10) if g.user else []

        me_name, my_role = (g.user.username, g.user.role) if g.user else (None, None)
        cage_cells, today = {}, date.today()
        mouse_rows = [mouse_display_row(mouse, me_name, my_role, cage_cells, today) for mouse in mice]
        cage_racks = mouse_rack_payload(db_session, cages, tiles=rack_tiles) if active_view == "cages" else None
        sheet_racks = mouse_racks(db_session)
        # The sheet renders rows you may not edit as read-only, using the
        # same rule the update routes enforce, rather than letting an edit
        # appear to save and then be refused. Only the rows drawn: a long
        # sheet draws its first page (_colony_page_context), and the others
        # come from /colony/mice/rows, which asks the same.
        drawn = mice if len(mice) <= inventory_routes.LAZY_OVER else mice[:inventory_routes.FIRST_PAGE]
        for mouse, row in zip(drawn, mouse_rows):
            row["editable"] = can_edit_mouse(mouse)
        # A long sheet draws its first page (_colony_page_context); each other
        # cage is a stub (cage_stubs), which reads only what cage_stub_row
        # gives: each of its mice's rows, its litters to wean and who may
        # edit what, only for the rows drawn.
        drawn = len(cages) if only is not None or len(cages) <= inventory_routes.LAZY_OVER \
            else inventory_routes.FIRST_PAGE
        # The cages drawn, through the ORM: their rows ask their litters born
        # in them, the Wean dialog's pups and who may edit what.
        in_full = {c.id: c for c in db_session.scalars(
            select(CageRecord).where(CageRecord.id.in_([c.id for c in cages[:drawn]])).options(
                selectinload(CageRecord.mice).selectinload(MouseRecord.litter), selectinload(CageRecord.rack),
                selectinload(CageRecord.cage_litters).selectinload(CageLitter.litter).selectinload(LitterRecord.mice))
        )} if cages else {}
        cage_rows = [cage_sheet_row(in_full[cage.id]) if n < drawn else cage_stub_row(cage)
                     for n, cage in enumerate(cages)]
        # The Experiments tab's "start with a cage" only lists cage numbers.
        cage_ids = positions.natural_sorted(db_session.scalars(select(CageRecord.cage_id).where(
            CageRecord.id.in_(select(MouseRecord.cage_id_fk).where(MouseRecord.date_of_death.is_(None)))))
        ) if active_view == "experiments" else []
        litter_rows = []
        drawn = len(drawn_litters)
        for n, litter in enumerate(litters):
            row = {
                "id": litter.id,
                "litter_id": litter.litter_id,
                "date_of_birth": litter.date_of_birth.isoformat() if litter.date_of_birth else "",
                "cohort_name": litter.cohort_name,
                "father_info": litter.father_info,
                "mother_info": litter.mother_info,
                "total_pups": litter.total_pups,
                "notes": litter.notes,
                "mice": litter.mice,   # by number (plain_records.PupName): .mouse_id, .gender
            }
            if n < drawn:
                # Each pup as its cell shows it (number, sex, alive, age).
                pup_age = calculate_age_fields(litter.date_of_birth)["age_days"]
                row["editable"] = access.can_edit_litter(litter)
                row["mice"] = [{"mouse_id": mouse.mouse_id, "gender": mouse.gender, "active": mouse_is_active(mouse),
                                "age_days": pup_age} for mouse in litter.mice]
            litter_rows.append(row)
        strain_rows = [
            {
                "id": strain.id,
                "strain_number": strain.strain_number,
                "strain_name": strain.strain_name,
                "strain_background": strain.strain_background,
                "supplier": strain.supplier,
                "description": strain.description,
                "created_by": strain.created_by,
                "editable": access.can_edit_strain(strain),
            }
            for strain in strains
        ]
        breeder_rows, breeder_summary_rows = [], []
        hidden_breeders = {"cages": 0, "mice": 0}
        if active_view == "breeders":
            breeder_rows, hidden_breeders = breeder_mice(db_session, g.user.username if g.user else None,
                                                         g.user.role if g.user else None,
                                                         recent=None if show_ended else recent)
            breeder_summary_rows = breeder_summary(db_session)
        held_purposes = _in_word_order(db_session.scalars(select(CageRecord.purpose).distinct())) \
            if active_view == "cages" else []
        # The mice view needs the open experiments too: the selection bar
        # offers "add to experiment", so the list has to be there even when
        # you are not on the experiments tab.
        open_experiments = []
        if active_view in ("mice", "cages", "breeders"):
            open_experiments = [
                # one started with a plan asks why its groups change (experiment_plan)
                {"id": e.id, "name": e.name, "fixed": experiment_plan.fixed(e)}
                for e in db_session.scalars(
                    select(Experiment)
                    .where(Experiment.status.in_(["planned", "active", "paused"]), Experiment.db == "colony")
                    .order_by(Experiment.name)
                ).all()
            ]

        experiments_list = []
        if active_view == "experiments":
            experiments = db_session.scalars(
                select(Experiment).where(Experiment.db == "colony")
                .order_by(Experiment.status, Experiment.created_at.desc())
            ).all()
            for exp in experiments:
                experiments_list.append({
                    "id": exp.id,
                    "name": exp.name,
                    "description": exp.description,
                    "status": exp.status,
                    "owner": exp.owner_username,
                    "editable": access.can_edit_experiment(exp),
                    "member_count": len(exp.memberships),
                    "start_date": i18n.strftime(exp.start_date, "%b %d, %Y") if exp.start_date else "",
                    "end_date": i18n.strftime(exp.end_date, "%b %d, %Y") if exp.end_date else "",
                })
        mouse_sheet = mouse_sheet_meta(mouse_rows, dropdowns, sheet_racks)
        if active_view == "cages":
            # A cage's mice show the transgene columns the mouse sheet shows,
            # which go by the mice it lists, not just this page's cages. Only
            # Transgene 3 and 4 start hidden when empty, and only a mouse
            # with one of them, or with a genotype instead of transgenes,
            # can fill them (services.transgene_parts): those mice only.
            blank = lambda column: func.coalesce(column, "") == ""  # noqa: E731
            could = sheet_mice(where=~blank(MouseRecord.transgene_3) | ~blank(MouseRecord.transgene_4)
                               | (blank(MouseRecord.transgene_1) & blank(MouseRecord.transgene_2)
                                  & ~blank(MouseRecord.genotype)))
            mouse_sheet["tg_used"] = [True, True, *transgene_columns_used(could)[2:]]

    return {
        "active_view": active_view,
        "colony_views": COLONY_VIEWS,
        "mouse_rows": mouse_rows,
        "mouse_sheet": mouse_sheet,
        "cage_racks": cage_racks,
        "cage_rows": cage_rows,
        "cage_sheet": {
            # The lab's purposes, then any other word a cage still carries,
            # A to Z: the same for a cage fetched later (colony_cage_rows).
            "purpose_choices": _merged_choices(cage_purposes(dropdowns), held_purposes),
            "active_count": sum(1 for r in cage_rows if r["active"]),
            # All · Active · a chip for each of the lab's purposes (its active cages).
            "purposes": cage_purpose_chips(cage_rows, cage_purposes(dropdowns)),
            "location_notes_used": any(r["cage_location"] for r in cage_rows),
        },
        "can_edit_presets": access.can_edit_presets(),
        "litter_rows": litter_rows,
        "cage_ids": cage_ids,
        "hidden": {"mice": hidden_mice, "cages": hidden_cages, "litters": hidden_litters,
                   "breeder_cages": hidden_breeders["cages"], "breeder_mice": hidden_breeders["mice"],
                   "show_ended": show_ended, "days": RECENT_DAYS},
        "strain_rows": strain_rows,
        "dropdowns": dropdowns,
        "dropdown_records": dropdown_records,
        "choice_columns": choice_columns,
        "next_mouse_id_value": next_mouse_id_value,
        "next_cage_id_value": next_cage_id_value,
        "next_litter_id_value": next_litter_id_value,
        "usernames": usernames,
        "notifications": notifications,
        "status_options": MOUSE_STATUS_OPTIONS,
        "breeder_rows": breeder_rows,
        "breeder_summary": breeder_summary_rows,
        "experiments_list": experiments_list,
        "open_experiments": open_experiments,
        "totals": totals,
        "wean_offset_days": weaning_offset(),
        "min_wean_age_days": min(MIN_WEAN_AGE_DAYS, weaning_offset()),
        "cage_geno_offset_days": genotyping_offset(),
    }


@app.route("/colony")
@login_required
def colony():
    active_view = request.args.get("view", "mice")
    if active_view not in COLONY_VIEW_META:
        # An unknown or stale tab name lands on the mouse sheet rather than
        # on whichever tab happens to be the template's fallback.
        active_view = "mice"
    return render_template("colony.html", **_colony_page_context(active_view))


@app.route("/colony/litters/rows")
@login_required
def colony_litter_rows():
    """Rows of a long litter sheet by id (?ids=…, with the page's own
    arguments), as the page draws them; ?as=text for their words."""
    ids = sheet_paging.ids_asked(request)
    context = _colony_page_context("litters", only=set(ids))
    found = {row["id"]: row for row in context["litter_rows"]}
    rows = [found[i] for i in dict.fromkeys(ids) if i in found]
    drawn = render_template("_litter_rows.html", **{**context, "sheet_rows": rows, "fragment": True})
    if request.args.get("as") == "text":
        texts = cell_text.row_texts(drawn)
        return jsonify({"texts": {str(i): texts[i] for i in texts}, "ids": list(texts)})
    return jsonify({"rows": drawn, "forms": render_template("_litter_row_forms.html", sheet_rows=rows),
                    "ids": [row["id"] for row in rows]})


def _colony_page_context(active_view: str, only: set[int] | None = None) -> dict:
    scope = access.resolve_scope(request.args.get("scope"))
    context = colony_context(active_view, scope, show_ended=request.args.get("ended") == "all", only=only)
    context["scope"] = scope
    context["scopes"] = access.scopes_for()
    context["scope_hints"] = access.SCOPE_HINTS
    context["end_statuses"] = sorted(END_STATUSES)
    # Columns this lab added to the colony (app/custom_fields.py).
    with SessionLocal() as db_session:
        defined = custom_fields.fields(db_session, "colony")
    context["custom_columns"] = custom_fields.in_table(defined)
    context["custom_columns_all"] = defined
    # A long mouse sheet is paged by the server, as a long inventory is
    # (inventory_routes.LAZY_OVER): its first page drawn, the other mice as
    # entries data-table.js sorts, searches and counts, their rows fetched
    # from /colony/mice/rows when shown.
    rows = context["mouse_rows"]
    context["mouse_sheet_rows"], context["mouse_stubs"], context["mouse_rows_url"] = rows, [], ""
    if active_view == "mice" and len(rows) > inventory_routes.LAZY_OVER:
        context["mouse_sheet_rows"] = rows[:inventory_routes.FIRST_PAGE]
        context["mouse_stubs"] = mouse_stubs(rows[inventory_routes.FIRST_PAGE:], context["custom_columns"])
        context["mouse_rows_url"] = url_for("colony_mouse_rows")
    if active_view == "cages":
        # The rack grid's tiles come when it is first shown (colony_cage_grid).
        context["cage_grid_url"] = url_for("colony_cage_grid", **{
            k: request.args[k] for k in ("scope", "ended") if request.args.get(k)})
    # A long cage sheet the same way (_cage_rows.html, /colony/cages/rows):
    # the cards view and Open all fetch what they need (colony.html).
    rows = context["cage_rows"]
    context["cage_sheet_rows"], context["cage_stubs"], context["cage_rows_url"] = rows, [], ""
    if active_view == "cages" and only is None and len(rows) > inventory_routes.LAZY_OVER:
        context["cage_sheet_rows"] = rows[:inventory_routes.FIRST_PAGE]
        groups = project_groups.names()
        purposes = context["cage_sheet"]["purpose_choices"]
        context["cage_stubs"] = cage_stubs(rows[inventory_routes.FIRST_PAGE:], groups, purposes)
        context["cage_rows_url"] = url_for("colony_cage_rows", **{
            k: request.args[k] for k in ("scope", "ended") if request.args.get(k)})
    # A long litter sheet the same way (_litter_rows.html, /colony/litters/rows).
    rows = context["litter_rows"]
    context["litter_sheet_rows"], context["litter_stubs"], context["litter_rows_url"] = rows, [], ""
    if active_view == "litters" and only is None and len(rows) > inventory_routes.LAZY_OVER:
        context["litter_sheet_rows"] = rows[:inventory_routes.FIRST_PAGE]
        context["litter_stubs"] = litter_stubs(rows[inventory_routes.FIRST_PAGE:])
        # With the page's scope and ended=all: a litter it lists is one the address can fetch.
        context["litter_rows_url"] = url_for("colony_litter_rows", **{
            k: request.args[k] for k in ("scope", "ended") if request.args.get(k)})
    return context


def litter_stubs(rows: list[dict]) -> list[dict]:
    """Litters the sheet does not draw, as data-table.js reads a drawn row
    (_litter_rows.html): each one's data-*, each sortable cell's value
    ("k") and the words a search looks in ("s", lower-cased). Their `mice`
    are the pups as read (.mouse_id, .gender; colony_context). A pup's sex
    in the page's words is worked out once: a year's litters have tens of
    thousands of pups."""
    text = lambda value: "" if value is None else str(value)  # noqa: E731

    class Sexes(dict):
        def __missing__(self, sex):
            self[sex] = i18n.translate_value(sex)
            return self[sex]

    sexes = Sexes()
    none_yet = [gettext("none yet")]
    out = []
    for row in rows:
        mice = row["mice"]
        mouse_ids = [str(m.mouse_id) for m in mice]   # a mouse always has its number
        data = {"id": str(row["id"]), "litter_id": text(row["litter_id"]), "mice": str(len(mouse_ids)),
                "mouseIds": " ".join(mouse_ids)}
        cells = {"date_of_birth": text(row["date_of_birth"]), "cohort_name": text(row["cohort_name"]),
                 "father_info": text(row["father_info"]), "mother_info": text(row["mother_info"]),
                 "total_pups": text(row["total_pups"] or ""), "notes": text(row["notes"])}
        pups = [f"{number} {sexes[m.gender]}" if m.gender else number for m, number in zip(mice, mouse_ids)]
        words = [data["litter_id"], *(pups or none_yet), *data.values(), *cells.values()]
        out.append({"id": row["id"], "f": f"litter-update-{row['id']}", "d": data, "k": cells,
                    "s": " ".join(filter(None, words)).lower()})
    return out


def cage_stubs(rows: list[dict], groups: dict[int, str], purposes: list[str]) -> list[dict]:
    """Cages the sheet does not draw, as data-table.js reads a drawn row
    (_cage_rows.html): each one's data-*, each named cell's value ("k") and
    the words a search looks in ("s": what the row shows, its data-* and
    each field's value and the choice it shows, lower-cased). `groups`:
    project_groups.names(), for a cage shared with a group; `purposes`: the
    Purpose menu's choices, one of which is its purpose whatever its case.
    What the rows share (the words, the owners' badges) is worked out once."""
    text = lambda value: "" if value is None else str(value)  # noqa: E731
    by_word = {p.lower(): p for p in reversed(purposes)}   # the first of any two alike, as next() found
    shared, personal, empty = i18n.translate_value("Shared"), i18n.translate_value("Personal"), gettext("empty")
    purpose_words: dict[str, str] = {}
    badges: dict[str, str] = {}
    # Drawn as text (cell_text.text_cells_on), an empty Card ID, Pos or
    # Strain / genotype shows its placeholder, "–".
    as_text = cell_text.text_cells_on()
    out = []
    for row in rows:
        purpose = by_word.get(text(row["purpose"]).lower(), "")
        data = {"id": str(row["id"]), "cageRowId": str(row["id"]), "cage_id": text(row["cage_id"]),
                "active": "true" if row["active"] else "false", "breeding": text(row["breeding"]),
                "mine": "true" if row["mine"] else "false", "purpose": text(row["purpose"]).strip().lower(),
                "card_id": text(row["card_id"]), "rack_name": text(row["rack_name"]), "mice": text(row["live_count"]),
                "wean_due": text(row["wean_due"]), "mouseIds": " ".join(text(m["mouse_id"]) for m in row["mice"]),
                "genotypes": text(row["genotype_auto"])}
        cells = {"cage_id": text(row["cage_id"]), "card_id": text(row["card_id"]), "rack_id": text(row["rack_id"]),
                 "position": text(row["position"]), "purpose": purpose,
                 "date_give_birth": text(row["date_give_birth"]), "genotype_summary": text(row["genotype_summary"]),
                 "owner": text(row["owner"]), "is_shared": text(row["share_value"]),
                 "cage_location": text(row["cage_location"]), "notes": text(row["notes"])}
        share = row["share_value"]
        shown_share = (shared if share == "1"
                       else groups.get(int(share[1:]), "") if share.startswith("g") and share[1:].isdigit() else "")
        if not shown_share:
            shown_share = personal
        if row["live_count"]:
            count = [str(row["live_count"]), row["sex_label"]]
        elif row["total_count"]:
            count = [gettext("0 of %(total)s", total=row["total_count"])]
        else:
            count = [empty]
        owner = row["owner"] or ""
        if owner not in badges:
            badges[owner] = re.sub(r"<[^>]+>", "", str(owner_badge_filter(row["owner"])))
        if purpose and purpose not in purpose_words:
            purpose_words[purpose] = i18n.translate_value(purpose, "cage purpose")
        words = [*count, row["pup_litter"], row["wean_due"] or "–", badges[owner],
                 *data.values(), *cells.values(),
                 row["rack_name"] if row["rack_id"] else "–",
                 purpose_words[purpose] if purpose else "–",
                 shown_share]
        if as_text and not (row["card_id"] and row["position"] and (row["genotype_summary"] or row["genotype_auto"])):
            words.append("–")
        out.append({"id": row["id"], "e": f"cage-{row['id']}", "f": f"cage-update-{row['id']}", "d": data,
                    "k": cells, "s": " ".join(text(w) for w in words if w).lower()})
    return out


@app.route("/colony/cages/grid")
@login_required
def colony_cage_grid():
    """The cage rack grid's racks and tiles (rack-grid.js), for the page's
    scope and ended, asked for when the grid is first shown."""
    scope = access.resolve_scope(request.args.get("scope"))
    context = colony_context("cages", scope, show_ended=request.args.get("ended") == "all", rack_tiles=True)
    return jsonify(context["cage_racks"])


@app.route("/colony/cages/rows")
@login_required
def colony_cage_rows():
    """Rows of a long cage sheet by id (?ids=…, with the page's scope and
    ended), each with its detail row, as the page draws them, and their
    forms (the cage's and its mice's); ?as=text for their words."""
    ids = sheet_paging.ids_asked(request)
    context = _colony_page_context("cages", only=set(ids))
    found = {row["id"]: row for row in context["cage_rows"]}
    rows = [found[i] for i in dict.fromkeys(ids) if i in found]
    drawn = render_template("_cage_rows.html", **{**context, "sheet_rows": rows, "fragment": True})
    if request.args.get("as") == "text":
        texts = cell_text.row_texts(drawn)
        return jsonify({"texts": {str(i): texts[i] for i in texts}, "ids": list(texts)})
    return jsonify({"rows": drawn, "forms": render_template("_cage_row_forms.html", **{**context, "sheet_rows": rows}),
                    "ids": [row["id"] for row in rows]})


def mouse_stubs(rows: list[dict], custom_columns: list[dict]) -> list[dict]:
    """Mice the sheet does not draw, as data-table.js reads a drawn row
    (_mouse_rows.html): each one's data-* (the chips), each sortable cell's
    value ("k", as the cell holds it: a rack by its id) and the words a
    search looks in ("s": what the row shows and holds, lower-cased). What
    the rows share (the owners' badges, the words for a sex or a status) is
    worked out once: a long sheet has thousands."""
    text = lambda value: "" if value is None else str(value)  # noqa: E731
    named = ("ear_tag", "strain", "gender", "status", "transgene_1", "transgene_2", "transgene_3", "transgene_4",
             "cage_id", "cage_position", "owner", "litter_id", "date_of_birth", "date_of_death", "cage_location", "note")
    weeks_unit = gettext("w")
    sexes = {"Unknown": gettext("U")}
    words_of: dict[str, str] = {}   # a value's words as the page shows it (its translation)
    badges: dict[str, str] = {}

    def shown(value: str) -> str:
        if value not in words_of:
            words_of[value] = i18n.translate_value(value)
        return words_of[value]

    def badge(owner) -> str:
        key = owner or ""
        if key not in badges:
            badges[key] = re.sub(r"<[^>]+>", "", str(owner_badge_filter(owner)))
        return badges[key]

    out = []
    for row in rows:
        row_id, days = row["id"], row["age_days"]
        data = {"active": "true" if row["active"] else "false", "mouse_id": text(row["mouse_id"]),
                "ear_tag": row["ear_tag"] or "", "age": "" if days is None else str(days),
                "rowForm": f"mouse-update-{row_id}"}
        # Each of these is text or None (services.mouse_display_row).
        cells = {name: row[name] or "" for name in named}
        cells["cage_rack"] = text(row["cage_rack_id"])
        for fd in custom_columns:
            cells[f"attr_{fd['key']}"] = text(row["attrs"].get(fd["key"], ""))
        gender = row["gender"] or ""
        words = [str(row_id), *data.values(), *cells.values(), row["cage_rack"] or "",
                 sexes[gender] if gender in sexes else shown(gender),
                 shown(row["status"] or ""),
                 f"{row['age_weeks']}{weeks_unit}" if row["age_weeks"] is not None else "",
                 badge(row["owner"])]
        for fd in custom_columns:
            value = text(row["attrs"].get(fd["key"], ""))
            if value and fd.get("type") in ("select", "multiselect"):
                words.append(shown(value))
        out.append({"id": row_id, "f": data["rowForm"], "d": data,
                    "k": {k: v for k, v in cells.items() if v},
                    "s": " ".join(filter(None, words)).lower()})
    return out


@app.route("/colony/mice/rows")
@login_required
def colony_mouse_rows():
    """Rows of the mouse sheet by id (?ids=3,1,2), as its page would have
    drawn them, and their row forms; with ?as=text each row's cells' words
    only (an export, cell_text)."""
    ids = [int(x) for x in request.args.get("ids", "").split(",") if x.strip().isdigit()][:inventory_routes.ROWS_AT_ONCE]
    from sqlalchemy.orm import selectinload
    with SessionLocal() as db_session:
        found = {m.id: m for m in db_session.scalars(select(MouseRecord).where(MouseRecord.id.in_(ids)).options(
            selectinload(MouseRecord.cage).selectinload(CageRecord.rack), selectinload(MouseRecord.litter)))}
        mice = [found[i] for i in dict.fromkeys(ids) if i in found]
        rows = [mouse_display_row(m, g.user.username, g.user.role) for m in mice]
        for mouse, row in zip(mice, rows):
            row["editable"] = can_edit_mouse(mouse)
        # The choices every row offers: the lab's, and any its mice hold.
        held = db_session.execute(select(MouseRecord.status, MouseRecord.gender).distinct()).all()
        dropdowns = dropdown_options_map(db_session)
        sheet = mouse_sheet_meta(rows, dropdowns, mouse_racks(db_session))
        sheet["status_choices"] = _merged_choices(MOUSE_STATUS_OPTIONS, dropdowns.get("status", []),
                                                  _in_word_order(st for st, _ in held))
        sheet["gender_choices"] = _merged_choices(["F", "M", "Unknown"], dropdowns.get("gender", []),
                                                  _in_word_order(gd for _, gd in held))
        defined = custom_fields.fields(db_session, "colony")
    context = {"sheet_rows": rows, "sheet": sheet, "fragment": True,
               "custom_columns": custom_fields.in_table(defined), "custom_columns_all": defined}
    if request.args.get("as") == "text":
        drawn = render_template("_mouse_rows.html", **dict(context))
        texts = cell_text.row_texts(drawn)
        return jsonify({"texts": {str(i): texts[i] for i in texts}, "ids": list(texts)})
    return jsonify({"rows": render_template("_mouse_rows.html", **context),
                    "forms": render_template("_mouse_row_forms.html", **context),
                    "ids": [r["id"] for r in rows]})


# ---------------------------------------------------------------------------
# Experiments: a cohort of mice under a shared treatment plan + timeline,
# with longitudinal body-weight tracking per mouse.
# ---------------------------------------------------------------------------


EXPERIMENT_STATUSES = ("planned", "active", "paused", "done", "cancelled")


def _experiment_groups_refusal(db_session, exp, before, reason):
    """After a change to a colony experiment's mice: None, or the page's
    answer asking why when it was started with a plan (app/experiment_plan.py)."""
    place = experiment_pages.place_for(db_session, exp.db or "colony")
    problem = experiment_plan.groups_changed(db_session, exp, place, before, reason) if place else None
    if problem is None:
        return None
    db_session.rollback()
    if request.headers.get("X-Autosave") == "1" or request.is_json:
        return experiment_plan.ask_response(problem)
    flash(problem, "error")
    return redirect(url_for("experiment_detail", experiment_id=exp.id))


def _experiment_members_before(db_session, exp):
    if not experiment_plan.fixed(exp):
        return {}
    place = experiment_pages.place_for(db_session, exp.db or "colony")
    return experiment_plan.members(db_session, exp, place) if place else {}


def _experiment_refusal(exp):
    """A response when the current user may not change `exp`, else None.
    Background saves get JSON; form posts go back to the experiment."""
    if access.can_edit_experiment(exp):
        return None
    message = access.denied_message("experiment", exp.owner_username)
    if request.headers.get("X-Autosave") == "1":
        return jsonify({"ok": False, "error": message}), 403
    flash(message, "error")
    return redirect(url_for("experiment_detail", experiment_id=exp.id))


@app.route("/colony/experiments/create", methods=["POST"])
@login_required
def create_experiment():
    name = (request.form.get("name") or "").strip() or "Untitled experiment"
    description = (request.form.get("description") or "").strip()
    treatment = (request.form.get("treatment_plan") or "").strip()
    start_date = parse_date(request.form.get("start_date"))
    cage_id_raw = (request.form.get("from_cage_id") or "").strip()

    with SessionLocal() as db_session:
        exp = Experiment(
            name=name,
            description=description,
            treatment_plan=treatment,
            status="active",
            owner_username=g.user.username,
            start_date=start_date,
            readout=experiment_pages.readout_json(request.form.get("readout") or "",
                                                   request.form.get("readout_control") or "") or "",
        )
        db_session.add(exp)
        db_session.flush()

        # Optional: seed members from a cage's living mice that you may edit.
        if cage_id_raw:
            cage = db_session.scalar(select(CageRecord).where(CageRecord.cage_id == cage_id_raw))
            if cage is None:
                flash(gettext("There is no cage %(cage)s, so the experiment starts with no mice. Add a cage or single mice below.", cage=cage_id_raw), "error")
            else:
                added = skipped = 0
                for mouse in cage.mice:
                    if not mouse_is_active(mouse):
                        continue
                    if not can_edit_mouse(mouse):
                        skipped += 1
                        continue
                    db_session.add(ExperimentMouse(
                        experiment_id_fk=exp.id,
                        mouse_id_fk=mouse.id,
                        treatment_group="",
                    ))
                    added += 1
                if skipped:
                    flash(gettext("Added %(added)s mice from cage %(cage)s; %(skipped)s skipped — not yours to edit.",
                                  added=added, cage=cage.cage_id, skipped=skipped), "error")
        db_session.commit()
        return redirect(url_for("experiment_detail", experiment_id=exp.id))


@app.route("/colony/experiments/<int:experiment_id>")
@login_required
def experiment_detail(experiment_id: int):
    """An experiment's page: the same for every database's animals
    (app/experiments.py)."""
    return experiment_pages.render_page(experiment_id)


@app.route("/colony/experiments/<int:experiment_id>/update", methods=["POST"])
@login_required
def update_experiment(experiment_id: int):
    autosave = request.headers.get("X-Autosave") == "1"
    with SessionLocal() as db_session:
        exp = db_session.get(Experiment, experiment_id)
        if exp is None:
            return jsonify({"ok": False, "error": gettext("That experiment no longer exists.")}), 404
        refused = _experiment_refusal(exp)
        if refused:
            return refused
        form = request.form
        start = parse_date(form.get("start_date")) if "start_date" in form else exp.start_date
        end = parse_date(form.get("end_date")) if "end_date" in form else exp.end_date
        status = (form.get("status") or "").strip().lower() if "status" in form else exp.status
        error = None
        if start and end and end < start:
            error = gettext("The end date (%(end)s) is before the start date (%(start)s).",
                            end=end.isoformat(), start=start.isoformat())
        elif status and status not in EXPERIMENT_STATUSES:
            error = gettext("“%(status)s” is not an experiment status.", status=status)
        else:
            error = experiment_pages.status_refusal(exp, status)
        if error:
            if autosave:
                return jsonify({"ok": False, "error": error}), 409
            flash(error, "error")
            return redirect(url_for("experiment_detail", experiment_id=experiment_id))
        if "name" in form:
            exp.name = (form.get("name") or "").strip() or exp.name
        if "description" in form:
            exp.description = form.get("description", exp.description)
        if "treatment_plan" in form:
            exp.treatment_plan = form.get("treatment_plan", exp.treatment_plan)
        exp.status = status or "active"
        exp.start_date, exp.end_date = start, end
        exp.updated_at = datetime.utcnow()
        db_session.commit()
    if autosave:
        return jsonify({"ok": True})
    return redirect(url_for("experiment_detail", experiment_id=experiment_id))


@app.route("/colony/experiments/<int:experiment_id>/delete", methods=["POST"])
@login_required
def delete_experiment(experiment_id: int):
    with SessionLocal() as db_session:
        exp = db_session.get(Experiment, experiment_id)
        if exp is not None:
            refused = _experiment_refusal(exp)
            if refused:
                return refused
            # Its readings and subjects aren't the experiment's ORM children:
            # left behind, PostgreSQL refuses the delete (as experiments.delete).
            from .models import ExperimentReading, ExperimentSubject
            for model in (ExperimentSubject, ExperimentReading):
                for row in db_session.scalars(select(model).where(model.experiment_id_fk == exp.id)):
                    db_session.delete(row)
            log_delete(
                db_session, "experiments", exp.id,
                record_label=f"Experiment: {exp.name}",
                details=f"members={len(exp.memberships)}",
            )
            name = exp.name
            db_session.delete(exp)
            db_session.commit()
            flash(gettext("Deleted experiment %(name)s. Its mice are unchanged.", name=name), "success")
    return redirect(url_for("colony", view="experiments"))


@app.route("/colony/experiments/<int:experiment_id>/add-cage", methods=["POST"])
@login_required
def experiment_add_cage(experiment_id: int):
    cage_id_raw = (request.form.get("cage_id") or "").strip()
    with SessionLocal() as db_session:
        exp = db_session.get(Experiment, experiment_id)
        if exp is None:
            return redirect(url_for("colony", view="experiments"))
        refused = _experiment_refusal(exp)
        if refused:
            return refused
        if not cage_id_raw:
            flash(gettext("Enter a cage to add its mice."), "error")
            return redirect(url_for("experiment_detail", experiment_id=experiment_id))
        cage = db_session.scalar(select(CageRecord).where(CageRecord.cage_id == cage_id_raw))
        if cage is None:
            flash(gettext("Cage '%(cage)s' not found.", cage=cage_id_raw), "error")
            return redirect(url_for("experiment_detail", experiment_id=experiment_id))
        existing = {em.mouse_id_fk for em in exp.memberships}
        before = _experiment_members_before(db_session, exp)
        added = skipped = 0
        for mouse in cage.mice:
            if mouse.id in existing or not mouse_is_active(mouse):
                continue
            if not can_edit_mouse(mouse):
                skipped += 1
                continue
            db_session.add(ExperimentMouse(
                experiment_id_fk=exp.id,
                mouse_id_fk=mouse.id,
                treatment_group="",
            ))
            added += 1
        refused = _experiment_groups_refusal(db_session, exp, before, request.form.get("reason", "")) if added else None
        if refused:
            return refused
        db_session.commit()
    if added or skipped:
        if skipped:
            message = ngettext("Added %(num)s mouse from cage %(cage)s. %(skipped)s skipped — not yours to edit.",
                               "Added %(num)s mice from cage %(cage)s. %(skipped)s skipped — not yours to edit.",
                               added, cage=cage_id_raw, skipped=skipped)
        else:
            message = ngettext("Added %(num)s mouse from cage %(cage)s.", "Added %(num)s mice from cage %(cage)s.",
                               added, cage=cage_id_raw)
        flash(message, "success" if added else "error")
    else:
        flash(gettext("Cage %(cage)s has no living mice that are not already in the experiment.", cage=cage_id_raw),
              "info")
    return redirect(url_for("experiment_detail", experiment_id=experiment_id))


@app.route("/colony/experiments/<int:experiment_id>/add-mouse", methods=["POST"])
@login_required
def experiment_add_mouse(experiment_id: int):
    mouse_row_id = request.form.get("mouse_row_id", type=int)
    treatment_group = (request.form.get("treatment_group") or "").strip()
    with SessionLocal() as db_session:
        exp = db_session.get(Experiment, experiment_id)
        if exp is None:
            return redirect(url_for("colony", view="experiments"))
        refused = _experiment_refusal(exp)
        if refused:
            return refused
        mouse = db_session.get(MouseRecord, mouse_row_id) if mouse_row_id else None
        if mouse is None:
            flash(gettext("Pick a mouse to add."), "error")
            return redirect(url_for("experiment_detail", experiment_id=experiment_id))
        if not can_edit_mouse(mouse):
            flash(access.reason_denied(mouse), "error")
            return redirect(url_for("experiment_detail", experiment_id=experiment_id))
        if not any(em.mouse_id_fk == mouse.id for em in exp.memberships):
            before = _experiment_members_before(db_session, exp)
            db_session.add(ExperimentMouse(
                experiment_id_fk=exp.id,
                mouse_id_fk=mouse.id,
                treatment_group=treatment_group,
            ))
            refused = _experiment_groups_refusal(db_session, exp, before, request.form.get("reason", ""))
            if refused:
                return refused
            db_session.commit()
    return redirect(url_for("experiment_detail", experiment_id=experiment_id))


@app.route("/colony/experiments/<int:experiment_id>/members/<int:membership_id>/update", methods=["POST"])
@login_required
def experiment_member_update(experiment_id: int, membership_id: int):
    with SessionLocal() as db_session:
        em = db_session.get(ExperimentMouse, membership_id)
        if em is None or em.experiment_id_fk != experiment_id:
            return jsonify({"ok": False, "error": gettext("That mouse is no longer in the experiment.")}), 404
        if not access.can_edit_experiment(em.experiment):
            return jsonify({"ok": False, "error": access.denied_message(
                "experiment", em.experiment.owner_username)}), 403
        exp = em.experiment
        before = _experiment_members_before(db_session, exp)
        # Present but blank clears the group; absent leaves it alone.
        if "treatment_group" in request.form:
            em.treatment_group = (request.form.get("treatment_group") or "").strip()
        if "note" in request.form:
            em.note = (request.form.get("note") or "").strip()
        place = experiment_pages.place_for(db_session, exp.db or "colony")
        problem = experiment_plan.groups_changed(db_session, exp, place, before, request.form.get("reason", "")) if place else None
        if problem:
            db_session.rollback()
            return experiment_plan.ask_response(problem)
        db_session.commit()
        return jsonify({"ok": True})


@app.route("/colony/experiments/<int:experiment_id>/members/<int:membership_id>/remove", methods=["POST"])
@login_required
def experiment_member_remove(experiment_id: int, membership_id: int):
    with SessionLocal() as db_session:
        em = db_session.get(ExperimentMouse, membership_id)
        if em is not None and em.experiment_id_fk == experiment_id:
            refused = _experiment_refusal(em.experiment)
            if refused:
                return refused
            exp = em.experiment
            before = _experiment_members_before(db_session, exp)
            db_session.delete(em)
            refused = _experiment_groups_refusal(db_session, exp, before, request.form.get("reason", ""))
            if refused:
                return refused
            db_session.commit()
    return redirect(url_for("experiment_detail", experiment_id=experiment_id))


@app.route("/colony/mice/<int:mouse_row_id>/weights/create", methods=["POST"])
@login_required
def mouse_weight_create(mouse_row_id: int):
    raw_date = request.form.get("weigh_date") or ""
    raw_grams = request.form.get("grams") or ""
    notes = (request.form.get("notes") or "").strip()
    try:
        grams = float(raw_grams)
    except ValueError:
        return jsonify({"ok": False, "error": gettext("grams must be a number")}), 400
    weigh_date = parse_date(raw_date) or date.today()
    with SessionLocal() as db_session:
        mouse = db_session.get(MouseRecord, mouse_row_id)
        if mouse is None:
            return jsonify({"ok": False, "error": gettext("mouse not found")}), 404
        if not can_edit_mouse(mouse):
            return jsonify({"ok": False, "error": access.reason_denied(mouse)}), 403
        # Upsert: if a weight for this mouse + date exists, update it.
        existing = db_session.scalar(
            select(MouseWeight)
            .where(MouseWeight.mouse_id_fk == mouse.id)
            .where(MouseWeight.weigh_date == weigh_date)
        )
        if existing is not None:
            existing.grams = grams
            if notes:
                existing.notes = notes
            existing.recorded_by = g.user.username
        else:
            db_session.add(MouseWeight(
                mouse_id_fk=mouse.id,
                weigh_date=weigh_date,
                grams=grams,
                notes=notes,
                recorded_by=g.user.username,
            ))
        db_session.commit()
    if request.headers.get("X-Autosave") == "1":
        return jsonify({"ok": True})
    return redirect(request.referrer or url_for("colony", view="experiments"))


@app.route("/colony/mice/<int:mouse_row_id>/weights/<int:weight_id>/delete", methods=["POST"])
@login_required
def mouse_weight_delete(mouse_row_id: int, weight_id: int):
    with SessionLocal() as db_session:
        w = db_session.get(MouseWeight, weight_id)
        if w is not None and w.mouse_id_fk == mouse_row_id:
            mouse = db_session.get(MouseRecord, mouse_row_id)
            if not can_edit_mouse(mouse):
                flash(access.reason_denied(mouse), "error")
                return redirect(request.referrer or url_for("colony", view="experiments"))
            db_session.delete(w)
            db_session.commit()
    return redirect(request.referrer or url_for("colony", view="experiments"))


@app.route("/colony/mice/create", methods=["POST"])
@login_required
@next_number_retried
def create_mouse():
    refused = future_birth(request.form)
    if refused:
        flash(refused, "error")
        return redirect(url_for("colony", view="mice"))
    with SessionLocal() as db_session:
        mouse = MouseRecord(mouse_id=next_mouse_id(db_session), owner=request.form.get("owner", "").strip())
        transfer_recipient, sender_username = populate_mouse_from_form(db_session, mouse, request.form, preserve_owner_on_transfer=False)
        if not mouse.owner and g.user is not None:
            mouse.owner = g.user.username
        db_session.add(mouse)
        if transfer_recipient:
            create_transfer_copy(db_session, mouse, transfer_recipient, sender_username)
        db_session.commit()
    return redirect(url_for("colony", view="mice"))


@app.route("/colony/mice/new-record", methods=["POST"])
@login_required
@next_number_retried
def create_blank_mouse():
    """An empty mouse, the sheet's bottom New mouse button: back to the
    sheet it came from (its scope kept), where it is the row to type in."""
    with SessionLocal() as db_session:
        mouse = MouseRecord(mouse_id=next_mouse_id(db_session), owner=g.user.username)
        db_session.add(mouse)
        db_session.commit()
    return autosave_response("mice")


@app.route("/colony/mice/<int:mouse_row_id>/update", methods=["POST"])
@login_required
def update_mouse(mouse_row_id: int):
    with SessionLocal() as db_session:
        mouse = db_session.get(MouseRecord, mouse_row_id)
        if mouse is None:
            return autosave_response("mice")
        if not can_edit_mouse(mouse):
            flash(access.reason_denied(mouse), "error")
            return autosave_response("mice")

        done = change_mouse(db_session, mouse, request.form,
                            confirm_cohort=request.form.get("confirm_cohort_dob_change") == "1")
        if done.refused:
            db_session.rollback()
            flash(done.refused, "error")
            return autosave_response("mice")
        for warning in done.warnings:
            flash(warning, "error")
        db_session.commit()
        state = cage_state(mouse.cage)
        litter = mouse.litter
        mouse_state = {"id": mouse.id, "active": mouse_is_active(mouse), "status": mouse.status,
                       "date_of_death": mouse.date_of_death.isoformat() if mouse.date_of_death else "",
                       "litter_id": litter.litter_id if litter else "",
                       "date_of_birth": litter.date_of_birth.isoformat() if litter and litter.date_of_birth else ""}
        mouse_owner = mouse.owner
    result = autosave_response("mice")
    if request.headers.get("X-Autosave") == "1" and not isinstance(result, tuple):
        return jsonify({"ok": True, "cage": state, "mouse": mouse_state,
                        "row": {"active": mouse_state["active"],
                                "values": {"status": mouse_state["status"], "owner": mouse_owner,
                                           "litter_id": mouse_state["litter_id"]}}})
    return result


@app.route("/colony/mice/<int:mouse_row_id>/duplicate", methods=["POST"])
@login_required
@next_number_retried
def duplicate_mouse(mouse_row_id: int):
    with SessionLocal() as db_session:
        source_mouse = db_session.get(MouseRecord, mouse_row_id)
        if source_mouse is None:
            return redirect(url_for("colony", view="mice"))
        blocked = deny(source_mouse, "mice")
        if blocked:
            return blocked
        duplicate = MouseRecord(
            mouse_id=next_mouse_id(db_session),
            gender=source_mouse.gender,
            status=source_mouse.status,
            owner=source_mouse.owner,
            note=source_mouse.note,
            date_of_death=None,
        )
        set_mouse_cage(duplicate, source_mouse.cage)
        set_mouse_litter(duplicate, source_mouse.litter)
        sync_mouse_transgenes(duplicate, [source_mouse.transgene_1, source_mouse.transgene_2, source_mouse.transgene_3, source_mouse.transgene_4])
        db_session.add(duplicate)
        db_session.commit()
    return redirect(url_for("colony", view="mice"))


@app.route("/colony/mice/<int:mouse_row_id>/delete", methods=["POST"])
@login_required
def delete_mouse(mouse_row_id: int):
    with SessionLocal() as db_session:
        mouse = db_session.get(MouseRecord, mouse_row_id)
        if mouse is not None and can_edit_mouse(mouse):
            log_delete(
                db_session, "mice", mouse.id,
                record_label=f"Mouse #{mouse.mouse_id}",
                details=f"gender={mouse.gender} genotype={mouse.genotype} owner={mouse.owner}",
            )
            # Its weights and experiment places belong to it and go with it;
            # left behind they would block the delete (or, before foreign
            # keys were enforced, attach to whichever mouse got the id next).
            for child in (MouseWeight, ExperimentMouse):
                for row in db_session.scalars(select(child).where(child.mouse_id_fk == mouse.id)):
                    db_session.delete(row)
            db_session.delete(mouse)
            db_session.commit()
    return redirect(url_for("colony", view="mice"))


@app.route("/colony/mice/<int:mouse_row_id>/pick", methods=["POST"])
@login_required
def pick_mouse(mouse_row_id: int):
    with SessionLocal() as db_session:
        mouse = db_session.get(MouseRecord, mouse_row_id)
        if mouse is None:
            return redirect(url_for("colony", view="breeders"))
        blocked = deny(mouse, "breeders")
        if blocked:
            return blocked
        # The previous owner is told by app/notify.py ("… took mouse #12 from you").
        mouse.owner = g.user.username
        db_session.commit()
    return redirect(url_for("colony", view="breeders"))


@app.route("/colony/mice/bulk-sac", methods=["POST"])
@login_required
def bulk_sac_mice():
    def sac(mouse):
        mouse.status = "sac"
        # A mouse that died earlier keeps its day; only the living die today.
        mouse.date_of_death = mouse.date_of_death or date.today()
        stamp_updated(mouse)

    with SessionLocal() as db_session:
        done = bulk.run(db_session, bulk.selected(db_session, MouseRecord, request.form), sac,
                        may_edit=can_edit_mouse, action="update", what="mark as sac", table="mice")
        db_session.commit()
    # Say what happened, like every other batch action, and go back to the
    # view (and scope) it was done from.
    bulk.report(done, gettext("Recorded sac"), "mouse")
    return _back_to_colony("mice")


@app.route("/colony/mice/bulk-weigh", methods=["POST"])
@login_required
def bulk_weigh_mice():
    """Weigh the ticked mice: one weight each (grams_<id>), on one day.
    A mouse left blank is not weighed; a second weight on the same day
    replaces the first, as weighing one mouse does."""
    weigh_date = parse_date(request.form.get("weigh_date") or "") or date.today()
    grams: dict[int, float] = {}
    for key, raw in request.form.items():
        if not key.startswith("grams_") or not raw.strip():
            continue
        try:
            grams[int(key[6:])] = float(raw.strip().replace(",", "."))
        except ValueError:
            flash(gettext("“%(value)s” is not a weight in grams, so nothing was weighed.", value=raw.strip()), "error")
            return _back_to_colony("mice")
    with SessionLocal() as db_session:
        mice = [m for m in bulk.selected(db_session, MouseRecord, request.form) if m.id in grams]

        def weigh(mouse):
            existing = db_session.scalar(select(MouseWeight).where(
                MouseWeight.mouse_id_fk == mouse.id, MouseWeight.weigh_date == weigh_date))
            if existing is not None:
                existing.grams = grams[mouse.id]
                existing.recorded_by = g.user.username
            else:
                db_session.add(MouseWeight(mouse_id_fk=mouse.id, weigh_date=weigh_date, grams=grams[mouse.id],
                                           recorded_by=g.user.username))

        done = bulk.run(db_session, mice, weigh, may_edit=can_edit_mouse, action="update",
                        what=f"weigh on {weigh_date.isoformat()}", table="mice")
        db_session.commit()
    bulk.report(done, gettext("Weighed"), "mouse")
    return _back_to_colony("mice")


def _back_to_colony(view: str):
    """The colony page an action came from, scope and all, or the view."""
    referrer = request.referrer or ""
    return redirect(referrer if referrer.startswith(request.host_url) and "/colony" in referrer
                    else url_for("colony", view=view))


# ---------------------------------------------------------------------------
# Batch actions on a selection of mice.
#
# All of these take repeated `selected_ids` fields (see
# static/selection-bar.js) and share one rule: apply to every record the
# user may edit, skip the rest, and say how many were skipped rather than
# failing the whole operation.
# ---------------------------------------------------------------------------


# Fields the bulk editor may set, and how to apply each one.
BULK_FIELDS = {
    "owner": "Owner",
    "status": "Status",
    "strain": "Strain",
    "gender": "Sex",
    "genotype": "Transgenes",
    "cage_id": "Cage",
    "litter_id": "Litter",
    "note": "Note",
    "date_of_birth": "Date of birth",
    "date_of_death": "Date of death",
}
# What _report says was done, for each field.
BULK_DONE = {
    "owner": "Set owner",
    "status": "Set status",
    "strain": "Set strain",
    "gender": "Set sex",
    "genotype": "Set transgenes",
    "cage_id": "Set cage",
    "litter_id": "Set litter",
    "note": "Set note",
    "date_of_birth": "Set date of birth",
    "date_of_death": "Set date of death",
}


def new_owned_cage(db_session, **fields) -> CageRecord:
    """A fresh cage with the next free ID, owned by whoever made it."""
    cage = CageRecord(cage_id=next_cage_id(db_session),
                      owner=g.user.username if g.user else "", **fields)
    db_session.add(cage)
    db_session.flush()
    return cage


@app.route("/colony/mice/bulk-update", methods=["POST"])
@login_required
def bulk_update_mice():
    """Set one field to one value across the selection."""
    field = (request.form.get("field") or "").strip()
    value = (request.form.get("value") or "").strip()
    back = request.referrer or url_for("colony", view="mice")
    with SessionLocal() as db_session:
        # A column the lab added (app/custom_fields.py) is set like any
        # other: one column, one value, across the ticked rows.
        own = next((f for f in custom_fields.fields(db_session, "colony")
                    if f"attr_{f['key']}" == field), None)
        if own is None and field not in BULK_FIELDS:
            flash(gettext("Pick a field to set."), "error")
            return redirect(back)
        if own is not None:
            return bulk.set_custom_column(db_session, "colony", own, value, back,
                                       bulk.selected(db_session, MouseRecord, request.form), can_edit_mouse, "mice")
        refused = (stewardship.batch_owner_refused(stewardship.lab_members(db_session), value)
                   if field == "owner" else None)
        if refused:
            flash(refused, "error")
            return redirect(back)
        if field == "date_of_death" and value and parse_date(value) is None:
            flash(gettext("“%(value)s” is not a date (use YYYY-MM-DD).", value=value), "error")
            return redirect(back)
        # A mouse's age is its date of birth: never blanked by a slip.
        born = parse_date(value) if field == "date_of_birth" else None
        if field == "date_of_birth" and born is None:
            flash(gettext("“%(value)s” is not a date (use YYYY-MM-DD).", value=value or gettext("(blank)")), "error")
            return redirect(back)
        refused = future_birth({"date_of_birth": value}) if born else None
        if refused:
            flash(refused, "error")
            return redirect(back)
        if field == "gender" and value:
            # The sheet's Sex cell offers these only; a typo would make a
            # third sex nobody can pick.
            choices = _merged_choices(["F", "M", "Unknown"], dropdown_options_map(db_session).get("gender", []))
            value = next((c for c in choices if c.lower() == value.lower()), "")
            if not value:
                flash(gettext("“%(value)s” is not one of the sexes here (%(choices)s), so nothing changed.",
                              value=request.form.get("value", "").strip(), choices=", ".join(choices)), "error")
                return redirect(back)
        if field == "cage_id" and value and value.lower() != "new":
            existing_cage = db_session.scalar(select(CageRecord).where(CageRecord.cage_id == value))
            if existing_cage is not None and not access.can_edit_cage(existing_cage):
                flash(gettext("Cage %(cage)s is %(owner)s’s private cage, so no mouse was moved.", cage=value,
                              owner=existing_cage.owner or gettext("someone else")), "error")
                return redirect(back)
        # "new" is one new cage for the whole selection — the mice were
        # picked together to be housed together — made only when at least
        # one selected mouse may move.
        target_cage = target_litter = None
        selected = bulk.selected(db_session, MouseRecord, request.form)
        # A date of birth belongs to the litter. A litter whose every mouse
        # is in the selection (and yours to change) is re-dated, as on the
        # Litters tab, and keeps its parents; the rest of the selection
        # leaves its litter for one new litter of that date, as one row of
        # the sheet does.
        whole_litters = set()
        if field == "date_of_birth":
            mine = {m.id for m in selected if can_edit_mouse(m)}
            whole_litters = {m.litter.id for m in selected
                             if m.id in mine and m.litter is not None
                             and all(sib.id in mine for sib in m.litter.mice)}
        def apply(mouse):
            nonlocal target_cage, target_litter
            if field == "cage_id":
                if value and target_cage is None:
                    # Made inside the batch, so undo removes it too.
                    target_cage = (new_owned_cage(db_session) if value.lower() == "new"
                                   else get_or_create_cage(db_session, value))
                # The column, not the relationship: the audit listener
                # records column changes, and undo needs this one.
                mouse.cage_id_fk = target_cage.id if target_cage else None
            elif field == "genotype":
                # The sheet shows transgene columns, so "genotype" fills
                # them ("Ai14; Cre" -> transgene 1 and 2) and keeps the
                # combined genotype string in step.
                sync_mouse_transgenes(mouse, split_genotype(value)[:4])
            elif field == "litter_id":
                # Joining a litter takes its date of birth; a blank
                # takes the mice out of theirs, as the sheet's cell does.
                if value and target_litter is None:
                    target_litter = get_or_create_litter(db_session, value[:80])
                set_mouse_litter(mouse, target_litter)
            elif field == "date_of_birth":
                litter = mouse.litter
                if litter is not None and litter.id in whole_litters:
                    litter.date_of_birth = born
                elif litter is None or litter.date_of_birth != born:
                    if target_litter is None:
                        # Made inside the batch, so undo removes it too.
                        target_litter = get_or_create_litter(db_session, next_litter_id(db_session), born)
                    set_mouse_litter(mouse, target_litter)
            elif field == "strain":
                mouse.strain = value[:120]
            elif field == "date_of_death":
                mouse.date_of_death = parse_date(value)
            elif field == "status":
                previous_status = mouse.status
                mouse.status = value
                apply_status_rules(mouse, previous_status)
            else:
                setattr(mouse, field, value)
            stamp_updated(mouse)

        done = bulk.run(db_session, selected, apply, may_edit=can_edit_mouse, action="update",
                        what=f"set {BULK_FIELDS[field].lower()} = {value or '(blank)'}", table="mice")
        db_session.commit()
    bulk.report(done, gettext(BULK_DONE[field]), "mouse")
    return redirect(back)


@app.route("/colony/mice/bulk-experiment", methods=["POST"])
@login_required
def bulk_add_to_experiment():
    """Add the selection to an experiment, all in one treatment group.

    This is the "set up an experiment with N mice under the same
    manipulation" case: pick the mice, pick the experiment, name the group.
    """
    experiment_id = request.form.get("experiment_id")
    group = (request.form.get("treatment_group") or "").strip()
    if not (experiment_id or "").isdigit():
        flash(gettext("Pick an experiment."), "error")
        return redirect(request.referrer or url_for("colony", view="mice"))

    added = skipped = already = 0
    with SessionLocal() as db_session:
        exp = db_session.get(Experiment, int(experiment_id))
        if exp is None:
            flash(gettext("That experiment no longer exists."), "error")
            return redirect(request.referrer or url_for("colony", view="mice"))
        batch_ctx = audit.batch(db_session, "create",
                                f"add to experiment {exp.name}"
                                + (f" as {group}" if group else ""),
                                "experiment_mice")
        batch_row = batch_ctx.__enter__()
        existing = {em.mouse_id_fk for em in exp.memberships}
        before = _experiment_members_before(db_session, exp)
        for mouse in bulk.selected(db_session, MouseRecord, request.form):
            if not can_edit_mouse(mouse):
                skipped += 1
                continue
            if mouse.id in existing:
                already += 1
                continue
            db_session.add(ExperimentMouse(
                experiment_id_fk=exp.id,
                mouse_id_fk=mouse.id,
                treatment_group=group,
            ))
            added += 1
        batch_row.record_count = added
        if added:
            place = experiment_pages.place_for(db_session, exp.db or "colony")
            problem = experiment_plan.groups_changed(db_session, exp, place, before,
                                                     request.form.get("reason", "")) if place else None
            if problem:
                batch_ctx.__exit__(None, None, None)
                db_session.rollback()
                flash(problem, "error")
                return redirect(request.referrer or url_for("colony", view="mice"))
        batch_ctx.__exit__(None, None, None)
        db_session.commit()
        name = exp.name
        exp_id = exp.id

    if group:
        said = ngettext("Added %(num)s mouse to %(name)s as “%(group)s”", "Added %(num)s mice to %(name)s as “%(group)s”",
                        added, name=name, group=group)
    else:
        said = ngettext("Added %(num)s mouse to %(name)s", "Added %(num)s mice to %(name)s", added, name=name)
    extras = []
    if already:
        extras.append(gettext("(%(n)s already in it)", n=already))
    if skipped:
        extras.append(gettext("— %(n)s skipped, not yours to edit", n=skipped))
    flash(gettext("%(said)s%(extras)s.", said=said, extras="".join(" " + extra for extra in extras)),
          "success" if added else "error")
    return redirect(url_for("experiment_detail", experiment_id=exp_id))


# ---------------------------------------------------------------------------
# Batch creation: one specification, many mice.
#
# Two ways in — type a prototype and a count, or upload a CSV — and both land
# in the same editable preview before anything is written. One confirm step,
# one validation path, one place where IDs are allocated.
#
# IDs are shown in the preview but allocated again at save time: the preview
# can sit on screen while someone else adds mice, and a stale block would
# collide on the unique index.
# ---------------------------------------------------------------------------

# Columns the preview grid understands, in display order.
BATCH_COLUMNS = [
    ("ear_tag", "Custom tag", 80),
    ("strain", "Strain", 110),
    ("gender", "Sex", 70),
    ("transgene_1", "Transgene 1", 130),
    ("transgene_2", "Transgene 2", 130),
    ("transgene_3", "Transgene 3", 130),
    ("transgene_4", "Transgene 4", 130),
    ("genotype", "Genotype", 150),
    ("cage_id", "Cage", 90),
    ("cage_location", "Location", 110),
    ("owner", "Owner", 110),
    ("litter_id", "Litter", 90),
    ("date_of_birth", "DOB", 130),
    ("status", "Status", 110),
    ("note", "Note", 170),
]
BATCH_FIELDS = [key for key, _label, _width in BATCH_COLUMNS]
MAX_BATCH = 200


def _blank_row() -> dict:
    return {key: "" for key in BATCH_FIELDS}


def _rows_from_prototype(form) -> list[dict]:
    """N copies of the prototype, optionally split by sex.

    The sex split exists because "6 females and 6 males" is how litters and
    orders actually arrive, and making someone retype the prototype twice to
    get it would defeat the point.
    """
    prototype = {key: (form.get(key) or "").strip() for key in BATCH_FIELDS}
    females = max(0, _batch_int(form.get("count_female")))
    males = max(0, _batch_int(form.get("count_male")))
    plain = max(0, _batch_int(form.get("count")))

    rows: list[dict] = []
    if females or males:
        # Females and males in one fresh cage breed. Unless asked to keep
        # them together, "new" becomes one new cage per sex.
        split = (females and males and _new_cage_token(prototype["cage_id"]) is not None
                 and not form.get("one_new_cage"))
        cage_f = f"{prototype['cage_id']}-F" if split else prototype["cage_id"]
        cage_m = f"{prototype['cage_id']}-M" if split else prototype["cage_id"]
        for _ in range(females):
            rows.append({**prototype, "gender": "F", "cage_id": cage_f})
        for _ in range(males):
            rows.append({**prototype, "gender": "M", "cage_id": cage_m})
    else:
        rows = [dict(prototype) for _ in range(plain or 1)]
    return rows[:MAX_BATCH]


def _rows_from_csv(upload) -> tuple[list[dict], list[str]]:
    """Parse an uploaded CSV into preview rows.

    Unknown columns are ignored rather than rejected — vendor exports carry
    all sorts of extra fields, and refusing the file over a stray column
    helps nobody.
    """
    import csv as _csv, io as _io

    raw = upload.read()
    try:
        text_data = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text_data = raw.decode("latin-1")

    reader = _csv.DictReader(_io.StringIO(text_data))
    rows, warnings = [], []
    aliases = {"sex": "gender", "dob": "date_of_birth", "cage": "cage_id",
               "litter": "litter_id", "notes": "note"}

    for index, raw_row in enumerate(reader, start=2):
        if index - 1 > MAX_BATCH:
            warnings.append(gettext("Only the first %(max)s rows were loaded.", max=MAX_BATCH))
            break
        row = _blank_row()
        for header, value in (raw_row or {}).items():
            if header is None:
                continue
            key = aliases.get(header.strip().lower(), header.strip().lower())
            if key in row:
                row[key] = (value or "").strip()
        if any(row.values()):
            rows.append(row)
    if not rows:
        warnings.append(gettext("No usable rows found in that file."))
    warnings.extend(_normalise_csv_dates(rows))
    return rows, warnings


# The ways a spreadsheet writes a date into a CSV. Excel uses the computer's
# own format: 3/14/2026 in the US, 14/03/2026 or 14.03.2026 in much of the
# world, and 3/14/26 when the year column is narrow.
_SLASH_DATE = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})$")


def _normalise_csv_dates(rows: list[dict]) -> list[str]:
    """Rewrite each row's date of birth as YYYY-MM-DD, in place.

    The preview's date field holds only that form, so anything else would
    arrive blank and be lost without a word. Whether 03/04/2026 is 3 April
    or 4 March is decided once for the whole file: a date whose first number
    is over 12 means day first, one whose second number is over 12 means
    month first, and with neither the file is read month first and says so.
    """
    warnings: list[str] = []
    parsed: dict[int, tuple[int, int, int]] = {}
    unreadable: list[int] = []
    day_first = month_first = False
    for index, row in enumerate(rows):
        raw = (row.get("date_of_birth") or "").strip()
        if not raw or parse_date(raw):
            if raw:
                row["date_of_birth"] = parse_date(raw).isoformat()
            continue
        match = _SLASH_DATE.match(raw)
        if not match:
            unreadable.append(index)
            continue
        a, b, year = (int(part) for part in match.groups())
        if year < 100:
            year += 2000
        parsed[index] = (a, b, year)
        day_first = day_first or a > 12
        month_first = month_first or b > 12
    guessed = False
    with SessionLocal() as s:
        lab_day_first = lab.date_style(s) == "day"      # Lab setup's "26 Sep 2026" decides a 03/04 file
    read_day_first = (day_first and not month_first) or (lab_day_first and not day_first and not month_first)
    for index, (a, b, year) in parsed.items():
        month, day = (b, a) if read_day_first else (a, b)
        guessed = guessed or (not day_first and not month_first and a != b)
        try:
            rows[index]["date_of_birth"] = date(year, month, day).isoformat()
        except ValueError:
            unreadable.append(index)
    for index in sorted(unreadable):
        warnings.append(gettext("Row %(row)s: “%(value)s” is not a date; that date of birth is left blank.",
                                row=index + 2, value=rows[index]["date_of_birth"]))
        rows[index]["date_of_birth"] = ""
    if day_first and month_first:
        warnings.append(gettext("The dates of birth mix day-first and month-first; check them."))
    elif guessed and read_day_first:
        warnings.append(gettext("Dates of birth were read day first (03/04/2026 as 3 April), as the lab's date style (Settings → General) says. If the file is month first, correct them here."))
    elif guessed:
        warnings.append(gettext("Dates of birth were read month first (03/04/2026 as 4 March). If the file is day first, correct them here, or save it with YYYY-MM-DD dates."))
    return warnings


def _new_cage_token(value) -> str | None:
    """The key of a "new" cage in the Cage column, or None.

    "new" is one fresh cage; "new-F", "new 2" and so on are each another, so
    a batch can be split into as many new cages as it needs.
    """
    text = (value or "").strip().lower()
    if text == "new" or re.match(r"^new[\s\-_#]+\S", text):
        return re.sub(r"[\s\-_#]+", "-", text)
    return None


def _mixed_new_cages(rows: list[dict]) -> list[str]:
    """Warnings for new cages that would hold both sexes."""
    sexes: dict[str, set[str]] = {}
    labels: dict[str, str] = {}
    for row in rows:
        token = _new_cage_token(row.get("cage_id"))
        sex = (row.get("gender") or "").strip().upper()[:1]
        if token and sex in ("F", "M"):
            sexes.setdefault(token, set()).add(sex)
            labels.setdefault(token, row["cage_id"].strip())
    return [
        gettext("Cage “%(cage)s” would get females and males together. Give the males another new cage (e.g. “%(cage)s-M”) unless they are meant to breed.", cage=labels[token])
        for token, found in sexes.items() if len(found) > 1
    ]


def _rows_from_grid(form) -> list[dict]:
    """Read back the edited preview grid (fields named rows-<i>-<key>)."""
    indexes = set()
    for key in form.keys():
        if key.startswith("rows-") and key.count("-") >= 2:
            _, index, _field = key.split("-", 2)
            if index.isdigit():
                indexes.add(int(index))

    rows = []
    for index in sorted(indexes):
        if form.get(f"rows-{index}-drop"):
            continue
        row = {key: (form.get(f"rows-{index}-{key}") or "").strip() for key in BATCH_FIELDS}
        if any(row.values()):
            rows.append(row)
    return rows[:MAX_BATCH]


def _batch_int(raw) -> int:
    try:
        return int(str(raw or "").strip())
    except ValueError:
        return 0


@app.route("/colony/mice/batch", methods=["GET"])
@login_required
def batch_mice():
    """The setup step: prototype + count, or a CSV."""
    with SessionLocal() as db_session:
        return render_template(
            "batch_mice.html",
            stage="setup",
            columns=BATCH_COLUMNS,
            rows=[],
            warnings=[],
            prototype=_blank_row() | {"owner": g.user.username, "status": "experiment"},
            template_headers=BATCH_TEMPLATE_HEADERS,
            dropdowns=dropdown_options_map(db_session),
            usernames=current_lab_usernames(db_session),
            strain_rows=db_session.scalars(
                select(StrainRecord).order_by(StrainRecord.strain_name)).all(),
            next_id=next_mouse_id(db_session),
            max_batch=MAX_BATCH,
        )


# The file "Download a template" gives: the headers the importer reads, in
# the short spelling the page shows, and two example rows to overwrite.
BATCH_TEMPLATE_HEADERS = ["sex", "strain", "transgene_1", "transgene_2", "transgene_3", "transgene_4",
                          "cage", "cage_location", "owner", "litter", "dob", "status", "note"]


@app.route("/colony/mice/batch/template.csv", methods=["GET"])
@login_required
def batch_mice_template():
    import csv as _csv, io as _io

    buffer = _io.StringIO()
    writer = _csv.writer(buffer)
    writer.writerow(BATCH_TEMPLATE_HEADERS)
    example = {"strain": "C57BL/6J", "transgene_1": "DAT-IRES-Cre/+", "cage": "new-F", "owner": g.user.username,
               "dob": (date.today() - timedelta(days=56)).isoformat(), "status": "experiment"}
    writer.writerow([{**example, "sex": "F"}.get(h, "") for h in BATCH_TEMPLATE_HEADERS])
    writer.writerow([{**example, "sex": "M", "cage": "new-M"}.get(h, "") for h in BATCH_TEMPLATE_HEADERS])
    # A byte-order mark, so Excel opens the file as UTF-8.
    return Response("\ufeff" + buffer.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="mice-template.csv"'})


@app.route("/colony/mice/batch/preview", methods=["POST"])
@login_required
def batch_mice_preview():
    """Turn a prototype, a CSV or an edited grid into the preview."""
    warnings: list[str] = []
    upload = request.files.get("file")

    if upload is not None and upload.filename:
        rows, warnings = _rows_from_csv(upload)
    elif request.form.get("source") == "grid":
        rows = _rows_from_grid(request.form)
    else:
        rows = _rows_from_prototype(request.form)

    if not rows:
        flash(gettext("Nothing to preview — set a count or choose a file."), "error")
        return redirect(url_for("batch_mice"))
    warnings.extend(_mixed_new_cages(rows))
    warnings.extend(_future_births(rows))
    return _batch_preview(rows, warnings)


def _future_births(rows: list[dict]) -> list[str]:
    today_ = date.today()
    late = [str(index + 1) for index, row in enumerate(rows)
            if (parse_date(row.get("date_of_birth")) or today_) > today_]
    if not late:
        return []
    return [ngettext("Row %(rows)s: the date of birth is in the future. Correct it before creating.",
                     "Rows %(rows)s: the date of birth is in the future. Correct it before creating.",
                     len(late), rows=", ".join(late))]


def _batch_preview(rows: list[dict], warnings: list[str]):
    with SessionLocal() as db_session:
        # Shown so you can see what you will get; re-allocated on save.
        proposed = reserve_mouse_ids(db_session, len(rows))
        return render_template(
            "batch_mice.html",
            stage="preview",
            columns=BATCH_COLUMNS,
            rows=rows,
            proposed_ids=proposed,
            warnings=warnings,
            prototype=_blank_row(),
            dropdowns=dropdown_options_map(db_session),
            usernames=current_lab_usernames(db_session),
            strain_rows=db_session.scalars(
                select(StrainRecord).order_by(StrainRecord.strain_name)).all(),
            next_id=proposed[0] if proposed else 0,
            max_batch=MAX_BATCH,
        )


@app.route("/colony/mice/batch/create", methods=["POST"])
@login_required
@next_number_retried
def batch_mice_create():
    """Write the previewed rows, with one contiguous block of IDs."""
    rows = _rows_from_grid(request.form)
    if not rows:
        flash(gettext("Nothing to create."), "error")
        return redirect(url_for("batch_mice"))
    late = _future_births(rows)
    if late:
        # Back to the grid as it was typed, not to an empty form.
        return _batch_preview(rows, late + _mixed_new_cages(rows))

    created = 0
    with SessionLocal() as db_session, audit.batch(
            db_session, "create", f"add {len(rows)} mice in bulk", "mice") as batch_row:
        ids = reserve_mouse_ids(db_session, len(rows))

        # Resolve each "new" once for the whole batch. Six littermates
        # arriving together belong in one cage; allocating a cage per row
        # would make the common case the wrong one. "new-F", "new-M",
        # "new 2"… are each a cage of their own, and explicit numbers per
        # row still split them however you like.
        new_cages: dict[str, str] = {}
        for row, mouse_id in zip(rows, ids):
            token = _new_cage_token(row.get("cage_id"))
            if token:
                if token not in new_cages:
                    new_cages[token] = new_owned_cage(db_session).cage_id
                row = {**row, "cage_id": new_cages[token]}
            mouse = MouseRecord(mouse_id=mouse_id, owner=row.get("owner", ""))
            # Reuse the single-record path so cage allocation, litter
            # linking and DOB inheritance behave identically.
            populate_mouse_from_form(
                db_session, mouse, ImmutableMultiDict(row),
                preserve_owner_on_transfer=False)
            if not mouse.owner and g.user is not None:
                mouse.owner = g.user.username
            transgenes = [row.get(f"transgene_{i}", "") for i in (1, 2, 3, 4)]
            # A mouse's genotype is its transgenes joined ("Ai14; Cre"). An
            # old spreadsheet's genotype column, given no transgenes, becomes
            # Transgene 1, so it shows in the sheet and can be edited there.
            if not any(t.strip() for t in transgenes) and (row.get("genotype") or "").strip():
                transgenes[0] = row["genotype"].strip()
            sync_mouse_transgenes(mouse, transgenes)
            db_session.add(mouse)
            created += 1
        batch_row.record_count = created
        db_session.commit()
        first, last = (ids[0], ids[-1]) if ids else (0, 0)

    undo = Markup('<a href="{}">{}</a>').format(url_for("batches_view"), gettext("Undo"))
    flash(Markup(gettext("Created %(count)s mice — #%(first)s to #%(last)s. %(undo)s", count=created, first=first,
                         last=last, undo=undo)), "success")
    return redirect(url_for("colony", view="mice"))


@app.route("/colony/mice/export")
@login_required
def export_mice():
    export_format = request.args.get("format", "csv")
    # The page passes the scope it is showing, so the file holds the mice
    # you were looking at rather than always your own.
    context = colony_context("mice", access.resolve_scope(request.args.get("scope")), show_ended=request.args.get("ended") != "recent")
    if export_format == "pdf":
        return render_template("print_mice.html", mouse_rows=context["mouse_rows"], printed_on=date.today().isoformat())
    payload, filename, mimetype = export_mouse_rows(context["mouse_rows"], export_format)
    return Response(
        payload,
        mimetype=mimetype,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


MAX_NEW_CAGES = 20


def _free_rack_cells(db_session, rack, count: int, start: tuple[int, int] | None) -> list[tuple[int, int]]:
    """Up to `count` empty positions of `rack` in reading order, from
    `start` (row, col) on, or from the first cell."""
    taken = {(r, c) for r, c in db_session.execute(
        select(CageRecord.rack_row, CageRecord.rack_col).where(
            CageRecord.rack_id_fk == rack.id, CageRecord.rack_row.is_not(None)))}
    cells = []
    first = start or (1, 1)
    for row in range(1, rack.rows + 1):
        for col in range(1, rack.cols + 1):
            if (row, col) < first or (row, col) in taken:
                continue
            cells.append((row, col))
            if len(cells) == count:
                return cells
    return cells


@app.route("/colony/cages/create", methods=["POST"])
@login_required
@next_number_retried
def create_cage():
    """Create a cage, or several ("How many", 1–20) with consecutive IDs.
    A blank ID takes the next free one(s); a typed ID starts the run. An ID
    that is already a cage is refused — creating never edits an existing
    cage (a stale form would otherwise overwrite it)."""
    form = request.form
    requested = form.get("cage_id", "").strip()
    raw_count = (form.get("count") or "1").strip() or "1"
    if not raw_count.isdigit() or not 1 <= int(raw_count) <= MAX_NEW_CAGES:
        flash(gettext("Make between 1 and %(max)s cages at a time (asked for “%(asked)s”).", max=MAX_NEW_CAGES,
                      asked=raw_count), "error")
        return autosave_response("cages")
    count = int(raw_count)
    refused = future_birth(form, "date_give_birth", "A litter's birth date")
    if not refused and parse_date(form.get("date_give_birth")) is not None:
        # Only a Breeding cage has litters (app/litters.py).
        refused = litters.not_breeding(SimpleNamespace(purpose=form.get("purpose", ""),
                                                       cage_id=requested or gettext("new")))
    if refused:
        flash(refused, "error")
        return autosave_response("cages")
    with SessionLocal() as db_session:
        if requested and requested.lower() != "new":
            if count > 1 and not requested.isdigit():
                flash(gettext("To make %(count)s cages, leave the ID blank or type the first number of the run.",
                              count=count), "error")
                return autosave_response("cages")
            codes = [requested] if count == 1 else [str(int(requested) + i) for i in range(count)]
            taken = db_session.scalars(select(CageRecord.cage_id).where(CageRecord.cage_id.in_(codes))).all()
            if taken:
                flash(gettext("Cage %(cage)s already exists; nothing was changed. Leave the ID blank to take the next free one.", cage=", ".join(positions.natural_sorted(taken))), "error")
                return autosave_response("cages")
        else:
            codes = reserve_cage_ids(db_session, count)

        rack = cells = None
        placement_error = None
        if count > 1 and (form.get("rack_id") or "").strip():
            rack_raw = form.get("rack_id").strip()
            rack = db_session.get(MouseRack, int(rack_raw)) if rack_raw.isdigit() else None
            if rack is None:
                placement_error = gettext("There is no rack called %(rack)s.", rack=rack_raw)
            else:
                start = None
                position = (form.get("position") or "").strip()
                if position:
                    start = positions.parse(position, rack.naming, rack.rows, rack.cols)
                    if start is None:
                        placement_error = gettext("“%(position)s” is not a position in rack %(rack)s.",
                                                  position=position, rack=rack.name)
                if not placement_error and (start or position == ""):
                    cells = _free_rack_cells(db_session, rack, count, start) if start else []
                    if start and len(cells) < count:
                        placement_error = gettext("%(rack)s had room for %(room)s of %(count)s from %(position)s; the rest are in it without a position.", rack=rack.name,
                                                  room=len(cells), count=count, position=position)

        batch_ctx = audit.batch(db_session, "create", f"add {count} cages", "mouse_cages") if count > 1 else None
        batch_row = batch_ctx.__enter__() if batch_ctx is not None else None
        created = []
        for index, code in enumerate(codes):
            cage = CageRecord(cage_id=code, owner=g.user.username)
            db_session.add(cage)
            cage.cage_location = form.get("cage_location", "").strip()
            cage.purpose = form.get("purpose", "").strip()
            cage.notes = form.get("notes", "").strip()
            litters.set_born(cage, parse_date(form.get("date_give_birth")))
            cage.card_id = form.get("card_id", "").strip()
            cage.genotype_summary = form.get("genotype_summary", "").strip()
            cage.location_detail = form.get("location_detail", "").strip()
            cage.room = form.get("room", "").strip()
            if count == 1:
                if "rack_id" in form:
                    error = apply_cage_position(db_session, cage, form.get("rack_id"), form.get("position"))
                    if error:
                        placement_error = error
            elif rack is not None:
                cage.rack_id_fk = rack.id
                if cells is not None and index < len(cells):
                    cage.rack_row, cage.rack_col = cells[index]
            db_session.flush()
            created.append(code)
        if batch_ctx is not None:
            batch_row.record_count = len(created)
            batch_ctx.__exit__(None, None, None)
        db_session.commit()
        if placement_error:
            flash(gettext("Created cage %(cage)s, but not placed: %(error)s", cage=created[0], error=placement_error)
                  if count == 1 else
                  gettext("Created %(count)s cages, %(first)s to %(last)s. %(error)s", count=count, first=created[0],
                          last=created[-1], error=placement_error), "error")
        elif request.headers.get("X-Autosave") != "1":
            flash(gettext("Created cage %(cage)s.", cage=created[0]) if count == 1 else
                  gettext("Created %(count)s cages, %(first)s to %(last)s.", count=count, first=created[0],
                          last=created[-1]), "success")
    return autosave_response("cages")


# ---- Cage racks -------------------------------------------------------------
# A cage sits at (rack_row, rack_col) of its rack; the rack's naming scheme
# (app/positions.py) decides what that position is called: D7, 4-7, 37…


def rack_naming_payload(rack) -> dict:
    """What rack-grid.js needs to label a rack's rows, columns and cells."""
    return positions.scheme(rack.naming)


def _mouse_rack_from_form(db_session, rack, form) -> tuple[str | None, dict | None]:
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


def cage_state(cage) -> dict | None:
    """A cage's shared fields, returned after a save so the sheet can update
    every row of that cage at once."""
    if cage is None:
        return None
    return {"cage_id": cage.cage_id, "rack_id": cage.rack_id_fk or "",
            "rack_name": cage.rack.name if cage.rack else "",
            "position": cage_position_label(cage), "location": cage.cage_location}


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


def cage_position_label(cage) -> str:
    """"D7" under the cage's rack scheme, or "" when not placed."""
    if cage is None or cage.rack is None:
        return ""
    return positions.label(cage.rack_row, cage.rack_col, cage.rack.naming, cage.rack.cols)


@app.route("/colony/racks/save", methods=["POST"])
@login_required
def save_mouse_rack():
    with SessionLocal() as db_session:
        rack_id = request.form.get("id", "").strip()
        rack = db_session.get(MouseRack, int(rack_id)) if rack_id.isdigit() else MouseRack(name="", created_by=g.user.username)
        if rack is None:
            flash(gettext("That rack no longer exists."), "error")
            return redirect(url_for("colony", view="cages"))
        if rack.id is not None and not access.can_edit_rack(rack):
            flash(gettext("Only whoever added rack %(rack)s, or an admin, can change it.", rack=rack.name), "error")
            return redirect(url_for("colony", view="cages"))
        error, change = _mouse_rack_from_form(db_session, rack, request.form)
        if error:
            db_session.rollback()
            flash(error, "error")
            return redirect(url_for("colony", view="cages"))
        if rack.id is None:
            db_session.add(rack)
        db_session.commit()
        flash(gettext("Saved rack %(rack)s.", rack=rack.name) + rack_layout.outcome(change), "success")
    return redirect(url_for("colony", view="cages"))


@app.route("/colony/racks/<int:rack_id>/delete", methods=["POST"])
@login_required
def delete_mouse_rack(rack_id: int):
    """Delete a rack. Its cages are kept, just unplaced."""
    with SessionLocal() as db_session:
        rack = db_session.get(MouseRack, rack_id)
        if rack is not None and not access.can_edit_rack(rack):
            flash(gettext("Only whoever added rack %(rack)s, or an admin, can delete it.", rack=rack.name), "error")
            return redirect(url_for("colony", view="cages"))
        if rack is not None:
            name = rack.name
            for cage in db_session.scalars(select(CageRecord).where(CageRecord.rack_id_fk == rack.id)):
                cage.rack_id_fk = cage.rack_row = cage.rack_col = None
            db_session.delete(rack)
            db_session.commit()
            flash(gettext("Deleted rack %(rack)s. Its cages are now unplaced.", rack=name), "success")
    return redirect(url_for("colony", view="cages"))


@app.route("/colony/cages/<int:cage_row_id>/place", methods=["POST"])
@login_required
def place_cage(cage_row_id: int):
    """Move a cage on the rack grid; answers JSON. Dropping onto an occupied
    position swaps the cage with the one shown there (the others in that
    position stay); an empty rack id unplaces the cage."""
    with SessionLocal() as db_session:
        cage = db_session.get(CageRecord, cage_row_id)
        if cage is None:
            return jsonify({"ok": False, "error": gettext("That cage no longer exists.")}), 404
        if not can_edit_cage(cage):
            return jsonify({"ok": False, "error": access.reason_denied(cage)}), 403
        rack_id = request.form.get("rack_id", "").strip()
        if not rack_id:
            # Dropped on the Unplaced tray: out of the rack altogether.
            cage.rack_id_fk = cage.rack_row = cage.rack_col = None
            db_session.commit()
            return jsonify({"ok": True})
        rack = db_session.get(MouseRack, int(rack_id)) if rack_id.isdigit() else None
        try:
            row, col = int(request.form.get("row", "")), int(request.form.get("col", ""))
        except ValueError:
            return jsonify({"ok": False, "error": gettext("Missing row or column.")}), 400
        if rack is None or not (1 <= row <= rack.rows and 1 <= col <= rack.cols):
            return jsonify({"ok": False, "error": gettext("That position is not in the rack.")}), 400
        here = slot_order(db_session.scalars(select(CageRecord).where(
            CageRecord.rack_id_fk == rack.id, CageRecord.rack_row == row,
            CageRecord.rack_col == col, CageRecord.id != cage.id)))
        holder = here[0] if here else None
        if holder is not None:
            if not can_edit_cage(holder):
                return jsonify({"ok": False, "error": gettext("That position holds cage %(cage)s, which you may not move.",
                                                              cage=holder.cage_id)}), 403
            holder.rack_id_fk, holder.rack_row, holder.rack_col = cage.rack_id_fk, cage.rack_row, cage.rack_col
        cage.rack_id_fk, cage.rack_row, cage.rack_col = rack.id, row, col
        db_session.commit()
    return jsonify({"ok": True})


def mouse_rack_payload(db_session, cages, tiles: bool = True) -> dict:
    """Racks and the cages in scope, for the rack grid. Cages outside the
    scope that sit in a rack are drawn too, dimmed (and locked if you may
    not move them): a cell that looks empty must be empty, or a drop there
    would move someone else's cage without anyone seeing it. Without
    `tiles`, the racks only: the grid asks for its tiles when it is first
    shown (colony_cage_grid), each carrying its cage's dialog."""
    racks = mouse_racks(db_session)
    items = []
    in_scope = {cage.id for cage in cages}
    others = [c for c in db_session.scalars(select(CageRecord).where(CageRecord.rack_id_fk.is_not(None))
                                            .options(selectinload(CageRecord.mice), selectinload(CageRecord.rack)))
              if c.id not in in_scope and c.rack_row] if tiles else []
    for cage in [*cages, *others] if tiles else []:
        outside = cage.id not in in_scope
        live = [m for m in cage.mice if mouse_is_active(m)]
        strains = sorted({(m.transgene_1 or "").strip() for m in live if (m.transgene_1 or "").strip()})
        sexes = "".join(m.gender[:1] for m in live if m.gender in ("F", "M"))
        where = f"{cage.rack.name} · {cage_position_label(cage)}" if cage.rack and cage.rack_row else ""
        items.append({
            "id": cage.id,
            "label": cage.cage_id,
            # Of cages sharing a position, the grid shows the latest live one
            # and marks the position "!" (services.slot_order).
            "live": bool(live),
            "placed": placed_when(cage)[0].isoformat(),
            "sub": cage.genotype_summary or ", ".join(strains[:2]) or cage.purpose or "",
            "badge": f"{len(live)}" if live else "",
            "tone": "other" if outside else (normalize_status(cage.purpose) if live else "inactive"),
            "locked": outside and not can_edit_cage(cage),
            "owner": cage.owner or "",
            "rack": cage.rack_id_fk,
            "row": cage.rack_row,
            "col": cage.rack_col,
            "title": "\n".join(filter(None, [
                f"Cage {cage.cage_id}" + (f" · {where}" if where else ""),
                f"{cage.owner or 'Nobody'}'s cage, not in this view" if outside else "",
                cage.purpose, f"{len(live)} live ({sexes.count('F')}F {sexes.count('M')}M)" if live else "empty",
                ", ".join(str(m.mouse_id) for m in live[:12]),
            ])),
            "search": " ".join([cage.cage_id, cage.cage_location, cage.purpose, cage.owner,
                                cage.genotype_summary, *strains, *(str(m.mouse_id) for m in live)]).lower(),
            "edit": {"data-record-edit": "cage-dialog", "data-record-payload": json.dumps({
                "id": cage.id, "_label": cage.cage_id, "_locked": not can_edit_cage(cage),
                "rack_id": cage.rack_id_fk or "", "position": cage_position_label(cage),
                "cage_location": cage.cage_location, "purpose": cage.purpose, "room": cage.room,
                "card_id": cage.card_id, "genotype_summary": cage.genotype_summary,
                "location_detail": cage.location_detail, "notes": cage.notes,
                "date_give_birth": cage.date_give_birth.isoformat() if cage.date_give_birth else "",
            })},
        })
    return {
        "racks": [{"id": r.id, "name": r.name, "rows": r.rows, "cols": r.cols,
                   "naming": rack_naming_payload(r),
                   "can_edit": access.can_edit_rack(r),
                   "edit": {"data-record-payload": json.dumps({
                       "id": r.id, "_label": r.name, "_locked": not access.can_edit_rack(r),
                       "name": r.name, "rows": r.rows,
                       "cols": r.cols, "room": r.room,
                       **{f"naming_{k}": v for k, v in rack_naming_payload(r).items()}})}} for r in racks],
        "items": items,
        "create": {"attrs": {"data-record-edit": "cage-dialog"}, "payload": {"purpose": "Experiment"},
                   "rack_field": "rack_id", "text_field": "position"},
    }


# Plain text fields a cage form may carry. A form only changes what it
# sends: the cage sheet posts its row's cells, the dialog every field.
CAGE_TEXT_FIELDS = ("purpose", "notes", "card_id", "genotype_summary", "location_detail", "room")


def renumber_cage(db_session, cage, raw) -> str | None:
    """Give `cage` the number typed over it in the cage sheet. Its mice
    follow (they point at the cage, not at its number); a number another
    cage has, or none at all, is refused."""
    code = (raw or "").strip()
    if code == cage.cage_id:
        return None
    if not code:
        return gettext("Cage %(cage)s needs a number; it was left as it was.", cage=cage.cage_id)
    if code.lower() == "new":
        return gettext("“new” takes the next free number for a new cage; type the number you want instead.")
    if len(code) > 80:
        return gettext("A cage number can be at most 80 characters.")
    taken = db_session.scalar(select(CageRecord.id).where(CageRecord.cage_id == code, CageRecord.id != cage.id))
    if taken:
        return gettext("There is already a cage %(code)s; cage %(cage)s kept its number.", code=code, cage=cage.cage_id)
    cage.cage_id = code
    return None


def apply_cage_form(db_session, cage, form) -> str | None:
    """Write a cage form onto `cage`; return an error message instead of
    saving something wrong. Rack, position and location note also change
    from the mouse sheet and the rack grid, so they are only written when
    they differ from the row's `_was` copy (app/formutil.py)."""
    if "cage_id" in form and form_changed(form, "cage_id"):
        refused = renumber_cage(db_session, cage, form.get("cage_id"))
        if refused:
            return refused
    for field in CAGE_TEXT_FIELDS:
        if field in form:
            setattr(cage, field, (form.get(field) or "").strip())
    if "cage_location" in form and form_changed(form, "cage_location"):
        cage.cage_location = (form.get("cage_location") or "").strip()
    if "date_give_birth" in form and form_changed(form, "date_give_birth"):
        refused = future_birth(form, "date_give_birth", "A litter's birth date")
        if refused:
            return refused
        # The cell is the cage's oldest litter not yet weaned: a date there
        # corrects it (or records the first), clearing it removes it.
        refused = litters.set_born(cage, parse_date(form.get("date_give_birth")))
        if refused:
            return refused
    # Any cage may be shared, with the lab ("1") or one project group
    # ("g<id>"); whose it is and whom it is shared with are its owner's (or
    # an admin's) to decide (app/stewardship.py).
    if "is_shared" in form:
        refused = stewardship.change_sharing(cage, form.get("is_shared"), "cage", cage.cage_id)
        if refused:
            return refused
    if "owner" in form and form_changed(form, "owner"):
        refused = stewardship.change_owner(db_session, cage, form.get("owner"), "cage", cage.cage_id)
        if refused:
            return refused
    if "rack_id" in form and form_changed(form, "rack_id", "position"):
        return apply_cage_position(db_session, cage, form.get("rack_id"), form.get("position"))
    return None


@app.route("/colony/cages/<int:cage_row_id>/update", methods=["POST"])
@login_required
def update_cage(cage_row_id: int):
    with SessionLocal() as db_session:
        cage = db_session.get(CageRecord, cage_row_id)
        if cage is None:
            flash(gettext("That cage no longer exists."), "error")
            return autosave_response("cages")
        blocked = deny(cage, "cages")
        if blocked:
            return blocked
        error = apply_cage_form(db_session, cage, request.form)
        if error:
            db_session.rollback()
            flash(error, "error")
            return autosave_response("cages")
        db_session.commit()
        state = cage_state(cage)
        row = {"active": cage_is_active(cage), "values": cage_sheet_values(cage)}
    result = autosave_response("cages")
    if request.headers.get("X-Autosave") == "1" and not isinstance(result, tuple):
        # "cage" is what the mouse sheet syncs its rows from; "row" is the
        # static/sheet.js shape the cage sheet reads.
        return jsonify({"ok": True, "cage": state, "row": row})
    return result


# ---- Batch actions on ticked cages -----------------------------------------

CAGE_BULK_LABELS = {"purpose": "Set purpose", "owner": "Set owner", "rack": "Moved",
                    "shared": "Sharing set", "retire": "Retired"}


@app.route("/colony/cages/bulk", methods=["POST"])
@login_required
def bulk_cages():
    """Batch actions on ticked cages: set purpose or owner, move to a rack
    (unplaced there), mark shared or not, retire. Applied to the cages you
    may edit, skipping the rest; one batch, so /batches can undo it."""
    form = request.form
    action = (form.get("action") or "").strip()
    value = (form.get("value") or "").strip()
    back = url_for("colony", view="cages", scope=access.resolve_scope(form.get("scope")))
    if action not in CAGE_BULK_LABELS:
        flash(gettext("Pick an action."), "error")
        return redirect(back)
    with SessionLocal() as db_session:
        rack = None
        members = stewardship.lab_members(db_session) if action == "owner" else set()
        refused = stewardship.batch_owner_refused(members, value) if action == "owner" else None
        if refused:
            flash(refused, "error")
            return redirect(back)
        share_group = project_groups.parse(value)[1] if action == "shared" else None
        if share_group is not None and not project_groups.may_share_with(share_group):
            flash(project_groups.refusal(share_group), "error")
            return redirect(back)
        if action == "rack" and value:
            rack = db_session.get(MouseRack, int(value)) if value.isdigit() else None
            if rack is None:
                flash(gettext("That rack no longer exists."), "error")
                return redirect(back)
        what = {"purpose": f"set cage purpose = {value or '(blank)'}",
                "owner": f"set cage owner = {value}",
                "rack": f"move cages to {rack.name if rack else '(no rack)'}",
                "shared": (f"share cages with {project_groups.name_of(share_group)}" if share_group
                           else "mark cages shared" if value == "1" else "mark cages not shared"),
                "retire": "retire cages"}[action]

        def apply(cage):
            if action == "purpose":
                cage.purpose = value
            elif action == "owner":
                if not stewardship.may_give_away(cage):
                    return bulk.NOT_YOURS
                return stewardship.change_owner(db_session, cage, value, "cage", cage.cage_id, members)
            elif action == "shared":
                return stewardship.change_sharing(cage, value, "cage", cage.cage_id)
            elif action == "rack":
                if rack is not None and cage.rack_id_fk == rack.id:
                    return bulk.UNCHANGED
                # Into the rack without a position: dragging on the rack
                # grid places each one.
                cage.rack_id_fk = rack.id if rack else None
                cage.rack_row = cage.rack_col = None
            elif action == "retire":
                living = [m for m in cage.mice if mouse_is_active(m)]
                if living:
                    return ngettext("Not retired: cage %(cage)s still holds %(num)s living mouse. Move or end it first.",
                                    "Not retired: cage %(cage)s still holds %(num)s living mice. Move or end them first.",
                                    len(living), cage=cage.cage_id)
                # Retired: no purpose (an empty cage is simply not active),
                # not shared, and its rack position is free for the next one.
                cage.purpose = ""
                cage.is_shared = False
                cage.share_group_id = None
                cage.active_override = False
                cage.rack_id_fk = cage.rack_row = cage.rack_col = None
            return None

        done = bulk.run(db_session, bulk.selected(db_session, CageRecord, form), apply,
                        may_edit=can_edit_cage, action="update", what=what, table="mouse_cages")
        db_session.commit()
    bulk.report(done, gettext(CAGE_BULK_LABELS[action]), "cage")
    return redirect(back)


@app.route("/colony/cages/<int:cage_row_id>/add-mouse", methods=["POST"])
@login_required
def add_mouse_from_cage(cage_row_id: int):
    with SessionLocal() as db_session:
        cage = db_session.get(CageRecord, cage_row_id)
        if cage is None:
            return redirect(url_for("colony", view="cages"))
        blocked = deny(cage, "cages")
        if blocked:
            return blocked
        requested_mouse_id = request.form.get("mouse_id", "").strip()
        if not requested_mouse_id.isdigit():
            flash(gettext("Enter the number of an existing mouse."), "error")
            return redirect(url_for("colony", view="cages"))
        mouse = db_session.scalar(select(MouseRecord).where(MouseRecord.mouse_id == int(requested_mouse_id)))
        if mouse is None:
            flash(gettext("Mouse %(mouse)s was not found.", mouse=requested_mouse_id), "error")
            return redirect(url_for("colony", view="cages"))
        # Moving a mouse changes the mouse, not just the cage.
        if not can_edit_mouse(mouse):
            flash(gettext("Mouse %(mouse)s was not moved. %(reason)s", mouse=mouse.mouse_id,
                          reason=access.reason_denied(mouse)), "error")
            return redirect(url_for("colony", view="cages"))
        mouse.cage_id_fk = cage.id  # the column, so the audit log records the move
        db_session.commit()
        flash(gettext("Moved mouse %(mouse)s into cage %(cage)s.", mouse=mouse.mouse_id, cage=cage.cage_id), "success")
    return redirect(url_for("colony", view="cages"))


@app.route("/colony/cages/<int:cage_row_id>/give-birth", methods=["POST"])
@login_required
def cage_give_birth(cage_row_id: int):
    """Litter born. A cage can have more than one litter at a time (a
    trio's second female gives one a week or two after the first): on a
    cage with a litter not yet weaned, the form says whether the new date is
    another litter (`litter_choice=another`; both are kept) or corrects the
    earlier one's date (`correct`, and with two, `litter`: the birth date of
    the one to correct). Without saying, nothing is changed: guessing either
    way loses a litter's reminders. The same date again only adds the count,
    the father or the mother to that litter."""
    with SessionLocal() as db_session:
        cage = db_session.get(CageRecord, cage_row_id)
        blocked = deny(cage, "cages")
        if blocked:
            return blocked
        not_breeding = litters.not_breeding(cage)
        if not_breeding:
            flash(not_breeding, "error")
            return _back_to_colony("cages")
        if cage is not None:
            form = request.form
            refused = future_birth(form, "date_give_birth", "A litter's birth date")
            count, refused = (None, refused) if refused else litters.pups(form.get("pups"))
            said = None
            if not refused:
                refused, said = litters.record_birth(
                    cage, parse_date(form.get("date_give_birth")) or date.today(), count,
                    dam=(form.get("dam") or "").strip(), sire=(form.get("sire") or "").strip(),
                    choice=form.get("litter_choice") or "", named=parse_date(form.get("litter")))
            if refused:
                db_session.rollback()
                flash(refused, "error")
                return _back_to_colony("cages")
            db_session.commit()
            flash(said, "success")
    return _back_to_colony("cages")


@app.route("/colony/cages/<int:cage_row_id>/genotyping", methods=["POST"])
@login_required
def cage_genotyping(cage_row_id: int):
    with SessionLocal() as db_session:
        cage = db_session.get(CageRecord, cage_row_id)
        if cage is None:
            return redirect(url_for("colony", view="cages"))
        blocked = deny(cage, "cages")
        if blocked:
            return blocked
        not_breeding = litters.not_breeding(cage)
        if not_breeding:
            flash(not_breeding, "error")
            return redirect(url_for("colony", view="cages"))
        # Which litter's pups: the one the dialog names (a cage with two
        # litters asks), else the oldest whose pups aren't mice yet.
        born_in, refused = litters.choose(cage, parse_date(request.form.get("litter")), "genotyping")
        # Nothing is required: pups not counted yet make the litter without
        # its mice, which can be added to it later.
        raw_pups = (request.form.get("total_pups") or "").strip()
        if not raw_pups and born_in is not None and born_in.pups:
            raw_pups = str(born_in.pups)       # counted when the litter was recorded
        total_pups, bad_count = litters.pups(raw_pups, "genotyping")
        refused = refused or bad_count
        if refused:
            flash(refused, "error")
            return redirect(url_for("colony", view="cages"))
        total_pups = total_pups or 0
        father_info = request.form.get("father_info", "").strip() or (born_in.sire if born_in is not None else "")
        mother_info = request.form.get("mother_info", "").strip() or (born_in.dam if born_in is not None else "")
        litter = LitterRecord(litter_id=generate_litter_id(db_session, cage),
                              date_of_birth=born_in.born if born_in is not None else (cage.date_give_birth or date.today()))
        db_session.add(litter)
        litter.father_info = father_info
        litter.mother_info = mother_info
        litter.total_pups = total_pups
        litter.cohort_name = f"Cage {cage.cage_id}"
        litter.notes = f"Generated by genotyping for cage {cage.cage_id}."
        db_session.flush()  # the litter's id, for the pups' column
        if born_in is not None and born_in.litter_id_fk is None:
            born_in.litter = litter
            if not born_in.pups and total_pups:
                born_in.pups = total_pups
        for _ in range(total_pups):
            mouse = MouseRecord(
                mouse_id=next_mouse_id(db_session),
                status="geno",
                owner=g.user.username,
            )
            set_mouse_cage(mouse, cage)
            set_mouse_litter(mouse, litter)
            db_session.add(mouse)
            db_session.flush()
        db_session.commit()
        if total_pups:
            flash(gettext("Created litter %(litter)s with %(pups)s pups in cage %(cage)s.", litter=litter.litter_id,
                          pups=total_pups, cage=cage.cage_id), "success")
        else:
            flash(gettext("Created litter %(litter)s in cage %(cage)s, with no pups yet: add them to it once they are counted.", litter=litter.litter_id, cage=cage.cage_id), "success")
    return redirect(url_for("colony", view="cages"))


@app.route("/colony/cages/<int:cage_row_id>/wean", methods=["POST"])
@login_required
def cage_wean(cage_row_id: int):
    # A batch, so Batch history can undo it like any other change to many records.
    with SessionLocal() as db_session, audit.batch(db_session, "update", "wean a cage", "mouse_cages") as batch_row:
        cage = db_session.get(CageRecord, cage_row_id)
        blocked = deny(cage, "cages")
        if blocked:
            return blocked
        not_breeding = litters.not_breeding(cage)
        if not_breeding:
            flash(not_breeding, "error")
            return _back_to_colony("cages")
        if cage is not None:
            weaning, refused = litters.choose(cage, parse_date(request.form.get("litter")), "wean")
            young = refused or litters.too_young(cage, weaning, early=bool(request.form.get("early")))
            if young:
                flash(young, "error")
                return _back_to_colony("cages")
            mark_weaned(cage, cage_litter=weaning)
            batch_row.description, batch_row.record_count = f"wean cage {cage.cage_id}"[:200], 1
            db_session.commit()
            flash(litters.weaned_said(cage, weaning), "success")
    return _back_to_colony("cages")


@app.route("/colony/cages/<int:cage_row_id>/wean-distribute", methods=["POST"])
@login_required
@next_number_retried
def cage_wean_distribute(cage_row_id: int):
    """Wean the source cage and distribute its pups to new or existing cages.

    Form arrays (one entry per UI row):
      mouse_ids[]   : comma-separated mouse IDs to move
      gender[]      : M / F / Unknown — applied to those mice
      cage_id[]     : existing cage ID; if filled, mice go there (card_id ignored)
      card_id[]     : if cage_id[] empty, a fresh cage is created with this card label
    Empty rows are skipped. Mice not listed stay in the source cage.

    Only mice that are in the source cage and that you may edit move; new
    cages are yours, and an existing destination must be a cage you may
    edit. Everything else is reported, not silently done.
    """
    mouse_ids_field = request.form.getlist("mouse_ids[]")
    genders_field = request.form.getlist("gender[]")
    cage_ids_field = request.form.getlist("cage_id[]")
    card_ids_field = request.form.getlist("card_id[]")
    rows = list(zip(mouse_ids_field, genders_field, cage_ids_field, card_ids_field))

    moved_count = 0
    problems: list[str] = []
    source_cage_label = ""

    with SessionLocal() as db_session, audit.batch(db_session, "update", "wean and distribute a cage",
                                                   "mouse_cages") as batch_row:
        source_cage = db_session.get(CageRecord, cage_row_id)
        if source_cage is None:
            flash(gettext("Cage not found."), "error")
            return redirect(url_for("colony", view="cages"))
        blocked = deny(source_cage, "cages")
        if blocked:
            return blocked
        source_cage_label = source_cage.cage_id
        not_breeding = litters.not_breeding(source_cage)
        if not_breeding:
            flash(not_breeding, "error")
            return redirect(url_for("colony", view="cages"))
        weaning, refused = litters.choose(source_cage, parse_date(request.form.get("litter")), "wean")
        young = refused or litters.too_young(source_cage, weaning, early=bool(request.form.get("early")))
        if young:
            flash(young, "error")
            return redirect(url_for("colony", view="cages"))
        # Before the pups move out: which litter they are is read from the cage.
        mark_weaned(source_cage, cage_litter=weaning)

        for mouse_ids_str, gender_raw, cage_id_input, card_id_input in rows:
            mouse_ids_str = (mouse_ids_str or "").strip()
            if not mouse_ids_str:
                continue
            movers: list[MouseRecord] = []
            for token in mouse_ids_str.replace(";", ",").split(","):
                token = token.strip()
                if not token:
                    continue
                if not token.isdigit():
                    problems.append(gettext("“%(token)s” is not a mouse number", token=token))
                    continue
                mouse = db_session.scalar(select(MouseRecord).where(MouseRecord.mouse_id == int(token)))
                if mouse is None:
                    problems.append(gettext("mouse %(mouse)s does not exist", mouse=token))
                elif mouse.cage_id_fk != source_cage.id:
                    problems.append(gettext("mouse %(mouse)s is not in cage %(cage)s", mouse=token,
                                            cage=source_cage_label))
                elif not can_edit_mouse(mouse):
                    problems.append(gettext("mouse %(mouse)s is %(owner)s’s", mouse=token,
                                            owner=mouse.owner or gettext("someone else")))
                else:
                    movers.append(mouse)
            if not movers:
                continue

            cage_id_input = (cage_id_input or "").strip()
            card_id_input = (card_id_input or "").strip()
            gender_value = (gender_raw or "").strip()

            if cage_id_input:
                target_cage = db_session.scalar(select(CageRecord).where(CageRecord.cage_id == cage_id_input))
                if target_cage is None:
                    target_cage = CageRecord(cage_id=cage_id_input, owner=g.user.username, card_id=card_id_input)
                    db_session.add(target_cage)
                    db_session.flush()
                elif not can_edit_cage(target_cage):
                    problems.append(gettext("cage %(cage)s is %(owner)s’s, so %(mice)s stayed", cage=cage_id_input,
                                            owner=target_cage.owner or gettext("someone else"),
                                            mice=", ".join(str(m.mouse_id) for m in movers)))
                    continue
            else:
                target_cage = new_owned_cage(db_session, card_id=card_id_input)

            for mouse in movers:
                mouse.cage_id_fk = target_cage.id  # the column, so the audit log records the move
                if gender_value:
                    mouse.gender = gender_value
                moved_count += 1

        batch_row.description = f"wean cage {source_cage_label} ({moved_count} mice moved)"[:200]
        batch_row.record_count = moved_count + 1
        left = waiting_litters(source_cage)
        still_to_wean = (gettext("Its litter born %(born)s is still to wean (due %(due)s).", born=fmt_day(left[0].born),
                                 due=fmt_day(left[0].born + timedelta(days=weaning_offset()))) if left else "")
        db_session.commit()

    if moved_count:
        flash(gettext("Distributed %(count)s mice and weaned cage %(cage)s.", count=moved_count,
                      cage=source_cage_label), "success")
    else:
        flash(gettext("Cage %(cage)s weaned (no mice were moved).", cage=source_cage_label), "success")
    if still_to_wean:
        flash(still_to_wean, "info")
    if problems:
        flash(gettext("Not moved: %(problems)s.", problems="; ".join(problems)), "error")
    return redirect(url_for("colony", view="cages"))


def _litter_refusal(litter, view: str = "litters"):
    if access.can_edit_litter(litter):
        return None
    owners = sorted({m.owner for m in litter.mice if m.owner and not can_edit_mouse(m)})
    flash(gettext("Litter %(litter)s has mice owned by %(owners)s, so only they or an admin can change it.",
                  litter=litter.litter_id, owners=", ".join(owners) or gettext("someone else")), "error")
    return autosave_response(view)


@app.route("/colony/litters/create", methods=["POST"])
@login_required
@next_number_retried
def create_litter():
    """Create a litter. A blank ID takes the next free number; an ID that is
    already a litter is refused rather than overwriting that litter."""
    requested = (request.form.get("litter_id") or "").strip()
    refused = future_birth(request.form)
    if refused:
        flash(refused, "error")
        return redirect(url_for("colony", view="litters", scope=request.form.get("scope") or None))
    with SessionLocal() as db_session:
        if requested and db_session.scalar(select(LitterRecord.id).where(LitterRecord.litter_id == requested)):
            flash(gettext("Litter %(litter)s already exists; nothing was changed. Leave the ID blank to take the next free one.", litter=requested), "error")
            return redirect(url_for("colony", view="litters", scope=request.form.get("scope") or None))
        litter = LitterRecord(litter_id=requested or next_litter_id(db_session),
                              date_of_birth=parse_date(request.form.get("date_of_birth")))
        litter.cohort_name = request.form.get("cohort_name", "").strip()
        litter.notes = request.form.get("notes", "").strip()
        db_session.add(litter)
        db_session.commit()
        flash(gettext("Created litter %(litter)s.", litter=litter.litter_id), "success")
    return redirect(url_for("colony", view="litters", scope=request.form.get("scope") or None))


@app.route("/colony/litters/<int:litter_row_id>/update", methods=["POST"])
@login_required
def update_litter(litter_row_id: int):
    with SessionLocal() as db_session:
        litter = db_session.get(LitterRecord, litter_row_id)
        if litter is None:
            return autosave_response("litters")
        refused = _litter_refusal(litter)
        if refused:
            return refused
        form = request.form
        refused = future_birth(form)
        if refused:
            flash(refused, "error")
            return autosave_response("litters")
        if "date_of_birth" in form:
            litter.date_of_birth = parse_date(form.get("date_of_birth"))
        for field in ("cohort_name", "notes", "father_info", "mother_info"):
            if field in form:
                setattr(litter, field, (form.get(field) or "").strip())
        if "total_pups" in form:
            raw = (form.get("total_pups") or "").strip()
            if raw and not raw.isdigit():
                flash(gettext("Pups must be a whole number, not “%(value)s”.", value=raw), "error")
                return autosave_response("litters")
            litter.total_pups = int(raw or 0)
        db_session.commit()
    return autosave_response("litters")


@app.route("/colony/litters/<int:litter_row_id>/add-existing-mouse", methods=["POST"])
@login_required
def add_existing_mouse_to_litter(litter_row_id: int):
    with SessionLocal() as db_session:
        litter = db_session.get(LitterRecord, litter_row_id)
        if litter is None:
            return redirect(url_for("colony", view="litters"))
        refused = _litter_refusal(litter)
        if refused:
            return refused
        requested_mouse_id = request.form.get("mouse_id", "").strip()
        if not requested_mouse_id.isdigit():
            flash(gettext("Enter the number of an existing mouse."), "error")
            return redirect(url_for("colony", view="litters"))
        mouse = db_session.scalar(select(MouseRecord).where(MouseRecord.mouse_id == int(requested_mouse_id)))
        if mouse is None:
            flash(gettext("Mouse %(mouse)s was not found.", mouse=requested_mouse_id), "error")
            return redirect(url_for("colony", view="litters"))
        # Joining a litter changes the mouse's date of birth.
        if not can_edit_mouse(mouse):
            flash(gettext("Mouse %(mouse)s was not added. %(reason)s", mouse=mouse.mouse_id,
                          reason=access.reason_denied(mouse)), "error")
            return redirect(url_for("colony", view="litters"))
        mouse.litter_id_fk = litter.id  # the column, so the audit log records it
        db_session.commit()
        flash(gettext("Mouse %(mouse)s is now in litter %(litter)s.", mouse=mouse.mouse_id, litter=litter.litter_id),
              "success")
    return redirect(url_for("colony", view="litters"))


def strain_denied(strain) -> str:
    who = (strain.created_by or "").strip()
    if who:
        return gettext("Strain %(strain)s was added by %(who)s; only they or an admin can change or remove it.",
                       strain=strain.strain_name, who=who)
    return gettext("Strain %(strain)s predates recorded creators; only an admin can change or remove it.",
                   strain=strain.strain_name)


PRESETS_DENIED = "Only an admin can add, rename or remove dropdown choices. You can still pick them everywhere."


@app.route("/colony/strains/create", methods=["POST"])
@login_required
def create_strain():
    strain_name = (request.form.get("strain_name") or "").strip()
    with SessionLocal() as db_session:
        if not strain_name:
            flash(gettext("A strain needs a name."), "error")
            return redirect(url_for("colony", view="strains"))
        existing = db_session.scalar(select(StrainRecord).where(
            func.lower(StrainRecord.strain_name) == strain_name.lower()))
        if existing is not None:
            flash(gettext("There is already a strain called %(strain)s.", strain=existing.strain_name), "error")
            return redirect(url_for("colony", view="strains"))
        db_session.add(
            StrainRecord(
                strain_number=request.form.get("strain_number", "").strip(),
                strain_name=strain_name,
                strain_background=request.form.get("strain_background", "").strip(),
                supplier=request.form.get("supplier", "").strip(),
                description=request.form.get("description", "").strip(),
                created_by=g.user.username,
            )
        )
        db_session.commit()
        flash(gettext("Added strain %(strain)s.", strain=strain_name), "success")
    return redirect(url_for("colony", view="strains"))


@app.route("/colony/strains/<int:strain_row_id>/update", methods=["POST"])
@login_required
def update_strain(strain_row_id: int):
    with SessionLocal() as db_session:
        strain = db_session.get(StrainRecord, strain_row_id)
        if strain is None:
            flash(gettext("That strain no longer exists."), "error")
            return autosave_response("strains")
        if not access.can_edit_strain(strain):
            flash(strain_denied(strain), "error")
            return autosave_response("strains")
        new_name = request.form.get("strain_name", strain.strain_name).strip()
        if not new_name:
            flash(gettext("A strain needs a name."), "error")
            return autosave_response("strains")
        if new_name != strain.strain_name:
            conflict = db_session.scalar(select(StrainRecord).where(
                func.lower(StrainRecord.strain_name) == new_name.lower(), StrainRecord.id != strain.id))
            if conflict is not None:
                flash(gettext("There is already a strain called %(strain)s; this one was not renamed.",
                              strain=conflict.strain_name), "error")
                return autosave_response("strains")
            strain.strain_name = new_name
        for field in ("strain_number", "strain_background", "supplier", "description"):
            if field in request.form:
                setattr(strain, field, request.form.get(field, "").strip())
        db_session.commit()
    return autosave_response("strains")


@app.route("/colony/strains/<int:strain_row_id>/delete", methods=["POST"])
@login_required
def delete_strain(strain_row_id: int):
    """Remove a strain from the reference list. Mice keep their transgene
    text; the strain only stops being offered as a suggestion."""
    with SessionLocal() as db_session:
        strain = db_session.get(StrainRecord, strain_row_id)
        if strain is not None and not access.can_edit_strain(strain):
            flash(strain_denied(strain), "error")
            return redirect(url_for("colony", view="strains"))
        if strain is not None:
            name = strain.strain_name
            db_session.delete(strain)
            db_session.commit()
            flash(gettext("Removed strain %(strain)s. Mice that carry it are unchanged.", strain=name), "success")
    return redirect(url_for("colony", view="strains"))


def _presets_back():
    """Where a preset change returns: the colony's Settings tab, or its
    Configure page's Cage purposes when it was made there."""
    if request.form.get("back") == "configure":
        return redirect(url_for("organisms.configure_builtin", key="colony"))
    return redirect(url_for("colony", view="settings"))


def _choices_reply(message: str, category: str = "success", status: int = 200):
    """The Dropdowns tab asks by fetch and redraws itself; a plain form
    (Configure's Cage purposes) goes back with a flash."""
    if request.headers.get("X-Requested-With") == "fetch":
        ok = category != "error"
        return jsonify({"ok": ok, "message": message} if ok else {"ok": False, "error": message}), (status if ok else 409)
    if request.headers.get("X-Autosave") == "1":
        if category == "error":
            flash(message, "error")
        return autosave_response("settings")
    flash(message, category)
    return _presets_back()


@app.route("/colony/options/create", methods=["POST"])
@login_required
def create_option():
    """One choice, or several pasted one per line."""
    field_name = (request.form.get("field_name") or "").strip()
    values = []
    for line in (request.form.get("option_value") or "").splitlines():
        if line.strip() and line.strip().lower() not in {v.lower() for v in values}:
            values.append(line.strip())
    if not access.can_edit_presets():
        return _choices_reply(gettext(PRESETS_DENIED), "error")
    with SessionLocal() as db_session:
        if not field_name or not values:
            return _choices_reply(gettext("Pick a column and type a value."), "error")
        have = {v.lower(): v for v in db_session.scalars(
            select(DropdownOption.option_value).where(DropdownOption.field_name == field_name))}
        fresh = [v for v in values if v.lower() not in have]
        if not fresh:
            return _choices_reply(gettext("“%(value)s” is already a %(column)s preset.", value=have[values[0].lower()],
                                          column=i18n.translate_value(field_name)), "error")
        place = choices.next_position(db_session, field_name)
        for i, value in enumerate(fresh):
            db_session.add(DropdownOption(field_name=field_name, option_value=value, position=place + i if place else 0))
        db_session.commit()
    if len(fresh) == 1:
        return _choices_reply(gettext("Saved “%(value)s” as a %(column)s preset.", value=fresh[0],
                                      column=i18n.translate_value(field_name)))
    return _choices_reply(gettext("Added %(n)s choices to %(column)s.", n=len(fresh), column=choices.label(field_name)))


@app.route("/colony/options/<int:option_id>/update", methods=["POST"])
@login_required
def update_option(option_id: int):
    """Rename a choice; with also_records=1, on the records that hold it too."""
    new_value = request.form.get("option_value", "").strip()
    if not access.can_edit_presets():
        return _choices_reply(gettext(PRESETS_DENIED), "error")
    with SessionLocal() as db_session:
        option = db_session.get(DropdownOption, option_id)
        if option is None:
            return _choices_reply(gettext("That preset no longer exists."), "error")
        if not new_value:
            return _choices_reply(gettext("A preset cannot be blank; delete it instead."), "error")
        if new_value == option.option_value:
            return _choices_reply("")
        if choices.locked(option.field_name, option.option_value):
            return _choices_reply(gettext("Breeding and Breeder keep their names: the colony’s litters and its Breeders tab go by them."), "error")
        duplicate = db_session.scalar(
            select(DropdownOption).where(
                DropdownOption.field_name == option.field_name,
                func.lower(DropdownOption.option_value) == new_value.lower(),
                DropdownOption.id != option.id,
            )
        )
        if duplicate is not None:
            return _choices_reply(gettext("“%(value)s” is already a %(column)s preset.", value=duplicate.option_value,
                                          column=i18n.translate_value(option.field_name)), "error")
        old = option.option_value
        option.option_value = new_value
        done = None
        if request.form.get("also_records") == "1":
            done = choices.change_records(db_session, option.field_name, old, new_value,
                                          f"renamed {option.field_name} {old} to {new_value}")
        db_session.commit()
        if done and done.changed:
            return _choices_reply(gettext("Renamed “%(old)s” to “%(new)s”, and on %(things)s.", old=old, new=new_value,
                                          things=choices.uses_text(option.field_name, done.changed)))
    return _choices_reply(gettext("Renamed “%(old)s” to “%(new)s”.", old=old, new=new_value))


@app.route("/colony/options/<int:option_id>/delete", methods=["POST"])
@login_required
def delete_option(option_id: int):
    """Remove a choice. The records that hold it keep it, unless
    replace_with names another value to put in its place."""
    if not access.can_edit_presets():
        return _choices_reply(gettext(PRESETS_DENIED), "error")
    replace_with = (request.form.get("replace_with") or "").strip()
    with SessionLocal() as db_session:
        option = db_session.get(DropdownOption, option_id)
        if option is None:
            return _choices_reply("")
        column, value, field = i18n.translate_value(option.field_name), option.option_value, option.field_name
        done = None
        if replace_with and replace_with.lower() != value.lower():
            done = choices.change_records(db_session, field, value, replace_with, f"replaced {field} {value} with {replace_with}")
        db_session.delete(option)
        db_session.commit()
    if done and done.changed:
        return _choices_reply(gettext("Removed “%(value)s”; %(things)s now have “%(other)s”.", value=value,
                                      things=choices.uses_text(field, done.changed), other=replace_with))
    return _choices_reply(gettext("Removed the %(column)s preset “%(value)s”.", column=column, value=value))


@app.route("/colony/options/order", methods=["POST"])
@login_required
def order_options():
    """A column's choices in the order they were dragged to."""
    if not access.can_edit_presets():
        return _choices_reply(gettext(PRESETS_DENIED), "error")
    payload = request.get_json(silent=True) or {}
    field = str(payload.get("field_name") or "")
    ids = [int(i) for i in payload.get("ids") or [] if str(i).isdigit()]
    with SessionLocal() as db_session:
        choices.reorder(db_session, field, ids)
        db_session.commit()
    return jsonify({"ok": True})
