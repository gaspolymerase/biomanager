"""Batch actions: one change applied to the records ticked on a sheet.

Every database's selection bar (static/selection-bar.js) posts repeated
`selected_ids` and an action. They all share one rule: apply the change to
every ticked record the person may edit, skip the rest, and say how many
were changed and how many skipped rather than failing the whole batch. The
whole batch is one audit batch, so Batch history can undo it.

A database writes only what one record needs:

    def apply(tank):
        tank.purpose = value              # changed (return None)
        return bulk.UNCHANGED              # already so: counted nowhere
        return bulk.NOT_YOURS              # may edit it, not this change
        return "tank T1 still holds fish"  # refused, said under the report

    done = bulk.run(s, records, apply, may_edit=access.can_edit_fish,
                    action="update", what="set purpose", table="tanks")
    bulk.report(done, gettext("Set purpose"), "tank")
"""
from __future__ import annotations

from dataclasses import dataclass, field

from flask import flash, redirect
from sqlalchemy import select

from . import audit, custom_fields
from .i18n import gettext, ngettext, translate_value
from .web import stamp_updated

UNCHANGED = object()   # nothing to do for this record
NOT_YOURS = object()   # this change is for someone else (its owner, an admin)


def ids(form) -> list[int]:
    """The ticked records' row ids."""
    return [int(v) for v in form.getlist("selected_ids") if str(v).isdigit()]


def selected(session, model, form, *where) -> list:
    """The ticked records of `model` (that also match `where`)."""
    wanted = ids(form)
    if not wanted:
        return []
    return list(session.scalars(select(model).where(model.id.in_(wanted), *where)))


@dataclass
class Done:
    """What a batch did: `ticked` records, `changed` of them, `skipped`
    because they are someone else's, and the reasons it refused the rest."""
    ticked: int = 0
    changed: int = 0
    skipped: int = 0
    blocked: list[str] = field(default_factory=list)


def run(session, records, apply, *, may_edit, action: str, what: str, table: str) -> Done:
    """Apply `apply` to each record `may_edit` allows, as one audit batch
    (`action` "update" or "delete", `what` its description in Batch history)."""
    done = Done(ticked=len(records))
    with audit.batch(session, action, what, table) as batch_row:
        for record in records:
            outcome = apply(record) if may_edit(record) else NOT_YOURS
            if outcome is None:
                done.changed += 1
            elif outcome is NOT_YOURS:
                done.skipped += 1
            elif isinstance(outcome, str):
                done.blocked.append(outcome)
        batch_row.record_count = done.changed
    return done


# How many records, in the page's language: one literal per noun, so each
# has its Chinese (tests/test_i18n.py). A noun a lab named itself (an
# organism or inventory database's) is "3 newts" from its own words.
_COUNTS = {
    "mouse": lambda n: ngettext("%(num)s mouse", "%(num)s mice", n),
    "cage": lambda n: ngettext("%(num)s cage", "%(num)s cages", n),
    "tank": lambda n: ngettext("%(num)s tank", "%(num)s tanks", n),
    "fish row": lambda n: ngettext("%(num)s fish row", "%(num)s fish rows", n),
    "line": lambda n: ngettext("%(num)s line", "%(num)s lines", n),
    "clutch": lambda n: ngettext("%(num)s clutch", "%(num)s clutches", n),
    "child line": lambda n: ngettext("%(num)s child line", "%(num)s child lines", n),
    "plasmid": lambda n: ngettext("%(num)s plasmid", "%(num)s plasmids", n),
}


def count(n: int, noun: str, plural: str = "") -> str:
    """"3 tanks"."""
    if noun in _COUNTS:
        return _COUNTS[noun](n)
    return f"{n} {translate_value(noun if n == 1 else (plural or noun))}"


def report(done: Done, what: str, noun: str, plural: str = "", reasons: int = 5) -> None:
    """Flash what a batch did. `what` is already in the page's language
    ("Set owner", "Recorded sac")."""
    if done.changed and done.skipped:
        flash(gettext("%(what)s: %(things)s. %(skipped)s skipped — not yours to edit.", what=what,
                      things=count(done.changed, noun, plural), skipped=done.skipped), "success")
    elif done.changed:
        flash(gettext("%(what)s: %(things)s.", what=what, things=count(done.changed, noun, plural)), "success")
    elif done.skipped:
        flash(gettext("Nothing changed — %(things)s are not yours to edit.",
                      things=count(done.skipped, noun, plural)), "error")
    elif not done.ticked:
        flash(gettext("Nothing was selected."), "error")
    elif not done.blocked:
        flash(gettext("Nothing was changed."), "error")
    for reason in done.blocked[:reasons]:
        flash(reason, "error")


def set_custom_column(db_session, database: str, field: dict, value: str, back: str,
                      records, may_edit, table: str):
    """Set one of the lab's own columns on the ticked records, in one batch
    so Batch history can undo it. Records the person may not change are
    skipped and counted, as every other bulk action does."""
    def apply(record):
        custom_fields.apply_form(record, {f"attr_{field['key']}": value}, [field])
        stamp_updated(record)

    done = run(db_session, list(records), apply, may_edit=may_edit, action="update",
               what=f"set {field['label'].lower()} = {value or '(blank)'}", table=table)
    db_session.commit()
    report(done, gettext("Set %(column)s", column=translate_value(field["label"], "field")),
           {"mice": "mouse", "tanks": "tank", "plasmids": "plasmid"}.get(table, table))
    return redirect(back)
