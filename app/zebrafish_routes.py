"""The zebrafish database: tanks, fish, lines, racks, water systems,
clutches and the mating wizard, and the sac log. Parallel to the mouse
colony in app.py, whose Flask app these pages are registered on (their
endpoints are the ones every page links to: "zebrafish", "zebrafish_*").
app.py imports this module last, once everything it uses there exists.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from flask import flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload

from . import access, audit, bulk, cell_text, custom_fields, i18n, inventory_routes, positions, rack_layout
from . import sheet_paging, stewardship
from . import experiments as experiment_pages
from . import groups as project_groups
from .app import as_text_cells
from .colony_routes import _bulk_custom_column
from .flask_app import app
from .web import login_required, owner_badge_filter
from .days import fmt_day
from .db import SessionLocal
from .formutil import form_changed
from .i18n import gettext, ngettext, pgettext
from .models import (FISH_SEX_OPTIONS, FISH_STATUS_OPTIONS, TANK_PURPOSE_OPTIONS, ClutchRecord, FishLine, FishRack,
                     FishRecord, FishSacLog, TankRecord, WaterLog, WaterSystem)
from .services import current_lab_usernames, parse_date


ZEBRAFISH_VIEWS = ("tanks", "fish", "clutches", "experiments", "lines", "water")
# Older view names, still in bookmarks and reminders, and where they live now.
ZEBRAFISH_VIEW_ALIASES = {"racks": "grid", "genotyping": "geno", "sac": "table"}

# Statuses that mean the fish are gone. Setting one stamps the sac date and
# writes a sac-log entry; going back to a living status undoes both, the
# way a mouse's date of death follows its status.
FISH_DEAD_STATUSES = {"sac", "dead"}
# How a sac-log entry written by a status change begins; reviving the row
# removes that entry, not ones logged by hand.
FISH_STATUS_SAC_REASON = "Status set to "
# Tank purposes the whole lab works out of, like the mouse breeder cages.
FISH_SHARED_PURPOSES = {"breeding", "shared"}


def _fish_age_label(dof):
    """dpf (≤30 d) → wpf (≤12 w) → mpf (≤24 mo) → ypf. dof = date_of_fertilization."""
    if not dof:
        return ""
    days = (date.today() - dof).days
    if days < 0:
        return ""
    if days <= 30:
        return f"{days}dpf"
    weeks = days // 7
    if weeks <= 12:
        return f"{weeks}wpf"
    months = days // 30
    if months <= 24:
        return f"{months}mpf"
    years = days // 365
    return f"{years}ypf"


# ---- Rules shared by every zebrafish write ---------------------------------


def zf_tank_shared(tank) -> bool:
    return (getattr(tank, "purpose", "") or "").strip().lower() in FISH_SHARED_PURPOSES


def zf_can_edit(record) -> bool:
    """access.can_edit for fish records. A tank is editable when it is
    yours, unowned or a shared breeding tank; a fish row follows its tank.
    A line is its owner's (lines from before owners stay open). Racks carry
    no owner and stay open to the lab; deleting a water system is
    access.can_edit_rack (its creator or an admin)."""
    if isinstance(record, FishRecord):
        record = record.tank or record
    if isinstance(record, TankRecord):
        # A breeding tank shared with a project group is its members'.
        return access.can_edit(record, shared=zf_tank_shared(record)
                               and project_groups.record_editable(record, shared=True))
    return access.can_edit(record)


def zf_denied(record) -> str:
    """Why a record is read only, in words worth showing."""
    if isinstance(record, FishRecord) and record.tank is not None:
        record = record.tank
    owner = (getattr(record, "owner", "") or "").strip() or gettext("someone else")
    if isinstance(record, TankRecord) and zf_tank_shared(record) and record.share_group_id:
        return gettext("Tank %(tank)s is shared with %(group)s, which you are not in. Ask %(owner)s, or an admin, to make the change.", tank=record.tank_id,
                       group=project_groups.name_of(record.share_group_id), owner=owner)
    if isinstance(record, TankRecord):
        return gettext("Tank %(tank)s and its fish belong to %(owner)s. Ask them, or an admin, to make the change.",
                       tank=record.tank_id, owner=owner)
    if isinstance(record, ClutchRecord):
        return gettext("Clutch %(clutch)s belongs to %(owner)s. Ask them, or an admin, to make the change.",
                       clutch=record.clutch_id, owner=owner)
    if isinstance(record, FishLine):
        return gettext("Line %(line)s belongs to %(owner)s. Ask them, or an admin, to make the change.",
                       line=record.name, owner=owner)
    return gettext("That record belongs to %(owner)s. Ask them, or an admin, to make the change.", owner=owner)


def fish_alive(fish) -> bool:
    return (fish.status or "alive").strip().lower() not in FISH_DEAD_STATUSES and fish.sac_date is None


def zf_reply(view: str, error: str = "", row=None, status: int = 409, **args):
    """Answer a zebrafish write. Autosave (sheet.js, the rack grid) gets
    JSON; a form post goes back where it came from, or to the zebrafish
    view it belongs to — never the colony."""
    if request.headers.get("X-Autosave") == "1":
        if error:
            return jsonify({"ok": False, "error": error}), status
        return jsonify({"ok": True, **({"row": row} if row is not None else {})})
    if error:
        flash(error, "error")
    referrer = request.referrer or ""
    if referrer.startswith(request.host_url):
        return redirect(referrer)
    return redirect(url_for("zebrafish", view=view, **args))


class ZfInputError(ValueError):
    """A typed value that cannot be saved; the message says why."""


def zf_int(form, name: str, label: str, default=None, lo=None, hi=None):
    raw = (form.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ZfInputError(gettext("%(field)s must be a whole number, not “%(value)s”.",
                                   field=pgettext("zebrafish", label), value=raw)) from None
    if lo is not None and value < lo:
        raise ZfInputError(gettext("%(field)s can’t be less than %(n)s.", field=pgettext("zebrafish", label), n=lo))
    if hi is not None and value > hi:
        raise ZfInputError(gettext("%(field)s can’t be more than %(n)s.", field=pgettext("zebrafish", label), n=hi))
    return value


def zf_float(form, name: str, label: str):
    raw = (form.get(name) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise ZfInputError(gettext("%(field)s must be a number, not “%(value)s”.", field=pgettext("zebrafish", label),
                                   value=raw)) from None


def zf_date(form, name: str, label: str):
    raw = (form.get(name) or "").strip()
    if not raw:
        return None
    value = parse_date(raw)
    if value is None:
        raise ZfInputError(gettext("%(field)s: “%(value)s” is not a date.", field=pgettext("zebrafish", label),
                                   value=raw))
    return value


def zf_ref(s, model, raw, label: str):
    """The id of an existing row; None for blank. A missing row is an
    error rather than a dangling id."""
    raw = str(raw or "").strip()
    if not raw:
        return None
    row = s.get(model, int(raw)) if raw.isdigit() else None
    if row is None:
        raise ZfInputError(gettext("That %(what)s no longer exists. Reload the page.",
                                   what=pgettext("zebrafish", label)))
    return row.id


def zf_choice(raw, allowed, label: str, current=None) -> str:
    """One of the allowed values (any case), or the record's own existing
    value unchanged, so a custom status from before is not lost."""
    raw = (raw or "").strip()
    for option in allowed:
        if raw.lower() == option.lower():
            return option
    if current is not None and raw == (current or ""):
        return raw
    raise ZfInputError(gettext("%(field)s must be one of: %(options)s.", field=pgettext("zebrafish", label),
                               options=", ".join(allowed)))


def zf_next_code(s, column, prefix: str, width: int = 0) -> str:
    """The next code after the highest one in use, so deleting a record
    never hands its ID to the next one (a printed tank card still says it)."""
    pattern = re.compile(re.escape(prefix) + r"(\d+)$")
    numbers = [int(m.group(1)) for code in s.scalars(select(column).where(column.like(f"{prefix}%")))
               if (m := pattern.match(code or ""))]
    return f"{prefix}{max(numbers, default=0) + 1:0{width}d}"


def zf_unique(s, column, raw, noun: str, own_id=None, max_len: int = 80) -> str:
    """A required, unique identifier (tank ID, clutch ID, line name)."""
    value = (raw or "").strip()
    if not value:
        raise ZfInputError(gettext("A line needs a name.") if noun == "line" else
                           gettext("A tank needs an ID.") if noun == "tank" else
                           gettext("A clutch needs an ID.") if noun == "clutch" else f"A {noun} needs an ID.")
    if len(value) > max_len:
        raise ZfInputError(gettext("Keep the line name under %(max)s characters.", max=max_len) if noun == "line" else
                           gettext("Keep the tank ID under %(max)s characters.", max=max_len) if noun == "tank" else
                           gettext("Keep the clutch ID under %(max)s characters.", max=max_len) if noun == "clutch"
                           else f"Keep the {noun} ID under {max_len} characters.")
    model = column.class_
    clash = s.scalar(select(model.id).where(column == value, model.id != (own_id or 0)))
    if clash is not None:
        raise ZfInputError(gettext("%(value)s is already used by another line.", value=value) if noun == "line" else
                           gettext("%(value)s is already used by another tank.", value=value) if noun == "tank" else
                           gettext("%(value)s is already used by another clutch.", value=value) if noun == "clutch"
                           else f"{value} is already used by another {noun}.")
    return value


# ---- Page ------------------------------------------------------------------


def zf_tank_stubs(rows: list[dict], custom_columns: list[dict], racks: list, lines: list) -> list[dict]:
    """Tanks the sheet does not draw, as data-table.js reads a drawn row
    (zebrafish.html's tank rows, as cell_text.row_stub reads them): its
    data-*, each field's value ("k"), its row form, and the words a search
    looks in ("s": what the row shows, its data-* and each field's value
    and the choice it shows). Built from each tank's data rather than drawn:
    a lab's thousands of tanks took seconds to draw and read back.
    tests/test_speed.py holds each to the row it stands for."""
    from .cell_text import row_stub
    as_text = cell_text.text_cells_on()
    rack_name = {r.id: r.name for r in racks}
    line_name = {ln.id: ln.name for ln in lines}
    queued, no = gettext("Queued"), gettext("No")
    purposes: dict[str, str] = {}
    badges: dict[str, str] = {}
    custom_cell = app.jinja_env.get_template("_sheet.html").module.custom_cell if custom_columns else None

    def menu(chosen, names: dict) -> tuple[str, str]:
        """A rack or line menu's (value, words): the blank "–" when none is
        chosen, or one that is no longer there."""
        return (str(chosen), names[chosen]) if chosen and chosen in names else ("", "–")

    def purpose_of(purpose) -> tuple[str, str]:
        """The Purpose menu's choice: its own word, one from before the
        list as itself, else the first (the menu's macro, options())."""
        if purpose in TANK_PURPOSE_OPTIONS:
            if purpose not in purposes:
                purposes[purpose] = i18n.translate_value(purpose, "zebrafish")
            return purpose, purposes[purpose]
        if purpose:
            return purpose, purpose
        return purpose_of(TANK_PURPOSE_OPTIONS[0])

    out = []
    for tr in rows:
        t = tr["row"]
        rack, line, purpose = menu(t.rack_id_fk, rack_name), menu(t.line_id_fk, line_name), purpose_of(t.purpose)
        owner = t.owner or ""
        if owner not in badges:
            badges[owner] = re.sub(r"<[^>]+>", "", str(owner_badge_filter(owner)))
        data = {"id": str(t.id), "tank_id": str(t.tank_id), "active": "true" if t.active else "false",
                "mine": "true" if tr["mine"] else "false", "shared": "true" if tr["shared"] else "false",
                "rack": t.rack.name if t.rack else "", "line": t.line.name if t.line else "",
                "fish": str(tr["total_fish"]), "geno": "1" if t.needs_genotyping else "",
                "return": str(t.mating_return_at or "")}
        sort = {"rack_id_fk": rack[0], "position": tr["position"], "purpose": purpose[0], "line_id_fk": line[0],
                "owner": owner, "card_id": str(t.card_id), "notes": str(t.notes)}
        words = [str(t.tank_id), rack[1], purpose[1], line[1], str(tr["total_fish"]), badges[owner],
                 queued if t.needs_genotyping else no, fmt_day(t.mating_return_at), *data.values(), *sort.values()]
        if as_text and not tr["position"]:
            words.append("–")   # an empty Pos shows its placeholder
        for fd in custom_columns:
            cell = f'<td>{custom_cell(fd, tr["attrs"].get(fd["key"], ""), f"utank-{t.id}", not tr["editable"], "owner-name-options")}</td>'
            read = row_stub("", str(as_text_cells(cell, as_text)))
            sort.update({k: v for k, v in read["k"].items() if k not in sort})
            words.append(read["s"])
        out.append({"id": t.id, "e": f"tank-{t.id}", "f": f"utank-{t.id}", "d": data, "k": sort,
                    "s": " ".join(w for w in words if w).lower()})
    return out


def _zebrafish_context(active_view: str, tank_ids: set[int] | None = None):
    """What zebrafish.html draws for a tab. On the Tanks tab, `tank_ids`:
    only these tanks' rows are drawn (zebrafish_tank_rows)."""
    me = access.username()
    today = date.today()
    with SessionLocal() as s:
        systems = sorted(s.scalars(select(WaterSystem)).all(), key=lambda w: positions.place_order(w.name))
        racks = sorted(s.scalars(select(FishRack)).all(), key=lambda r: positions.place_order(r.name))
        lines = positions.natural_sorted(s.scalars(select(FishLine)).all(), key=lambda ln: ln.name)
        # Eager-load what the template reads after the session closes. Each
        # tank's row only for the Tanks tab: the other tabs took most of
        # their time over every tank's fish.
        tank_tab = active_view == "tanks"
        tanks = s.scalars(select(TankRecord).options(joinedload(TankRecord.line), joinedload(TankRecord.rack))
                          ).unique().all()
        tanks = positions.natural_sorted(tanks, key=lambda t: t.tank_id)
        fish = s.scalars(
            select(FishRecord).options(joinedload(FishRecord.line), joinedload(FishRecord.tank))
            .order_by(FishRecord.id.desc()).limit(500)
        ).all()
        clutches = s.scalars(
            select(ClutchRecord).options(joinedload(ClutchRecord.line))
            .order_by(ClutchRecord.date_of_fertilization.desc()).limit(200)
        ).all()
        tank_by_id = {t.id: t for t in tanks}

        # What refers to what, for delete confirmations that say what goes.
        parent_refs, clutch_line_refs = {}, {}
        for fa, mo, ln in s.execute(select(ClutchRecord.father_tank_id, ClutchRecord.mother_tank_id,
                                           ClutchRecord.line_id_fk)):
            for tid in {fa, mo} - {None}:
                parent_refs[tid] = parent_refs.get(tid, 0) + 1
            if ln:
                clutch_line_refs[ln] = clutch_line_refs.get(ln, 0) + 1
        sac_refs = dict(s.execute(select(FishSacLog.tank_id_fk, func.count(FishSacLog.id))
                                  .group_by(FishSacLog.tank_id_fk)).all())
        fish_line_refs = dict(s.execute(select(FishRecord.line_id_fk, func.count(FishRecord.id))
                                        .group_by(FishRecord.line_id_fk)).all())
        clutch_fish_refs = dict(s.execute(select(FishRecord.clutch_id_fk, func.count(FishRecord.id))
                                          .group_by(FishRecord.clutch_id_fk)).all())
        live_by_line = {}
        for fl, tl, n in s.execute(select(FishRecord.line_id_fk, TankRecord.line_id_fk, FishRecord.count)
                                   .join(TankRecord, FishRecord.tank_id_fk == TankRecord.id)
                                   .where(FishRecord.sac_date.is_(None))
                                   .where(func.lower(func.coalesce(FishRecord.status, "alive")).not_in(sorted(FISH_DEAD_STATUSES)))):
            key = fl or tl
            if key:
                live_by_line[key] = live_by_line.get(key, 0) + (n or 0)
        mating_refs = {}
        for t in tanks:
            for tid in {t.mating_father_tank_id, t.mating_mother_tank_id} - {None}:
                mating_refs[tid] = mating_refs.get(tid, 0) + 1

        # ---- Tanks ----
        # Each tank's fish as plain rows (how many, alive or not): a lab's
        # tanks hold tens of thousands of fish rows. A mating tank still out
        # has its fish in full, for where each group goes back to.
        fish_in: dict[int, list] = {}
        if tank_tab:
            for f in s.execute(select(FishRecord.tank_id_fk, FishRecord.count, FishRecord.status, FishRecord.sac_date)):
                fish_in.setdefault(f[0], []).append(f)
        returning: dict[int, list] = {}
        out_now = [t.id for t in tanks if tank_tab and zf_is_mating(t) and t.active]
        if out_now:
            for f in s.scalars(select(FishRecord).options(joinedload(FishRecord.line))
                               .where(FishRecord.tank_id_fk.in_(out_now)).order_by(FishRecord.id)):
                returning.setdefault(f.tank_id_fk, []).append(f)
        # A long tank sheet draws its first page; each other tank is a stub
        # (zf_tank_stubs), made from what its row needs to be found and
        # sorted. Its row in full (what deleting it would say, its dialog's
        # payload) only for the tanks drawn, a mating tank still out, and
        # the rack grid's tiles.
        tank_mode = request.args.get("mode", "table")
        paged = (tank_tab and tank_mode == "table" and tank_ids is None
                 and len(tanks) > inventory_routes.LAZY_OVER)
        tank_rows, tank_sheet_rows = [], []
        for n, t in enumerate(tanks if tank_tab else []):
            groups = fish_in.get(t.id, [])
            total = sum(f.count or 0 for f in groups if fish_alive(f))
            editable = zf_can_edit(t)
            position = fish_position_label(t)
            returnable = zf_is_mating(t) and t.active
            drawn = (tank_ids is None and (not paged or n < inventory_routes.FIRST_PAGE)) or (
                tank_ids is not None and t.id in tank_ids)
            if not (drawn or returnable or tank_mode != "table"):
                tank_rows.append({
                    "row": t, "total_fish": total, "position": position, "editable": editable,
                    "attrs": custom_fields.values(t), "mine": t.owner == me, "shared": zf_tank_shared(t),
                    "fish_rows": len(groups), "returnable": False, "return_plan": []})
                continue
            if drawn:
                tank_sheet_rows.append(len(tank_rows))   # where its row goes in tank_rows, below
            refs = []
            if parent_refs.get(t.id, 0):
                refs.append(ngettext("%(num)s clutch will no longer name it as a parent",
                                     "%(num)s clutches will no longer name it as a parent", parent_refs[t.id]))
            if mating_refs.get(t.id, 0):
                refs.append(ngettext("%(num)s mating tank will lose it as a parent",
                                     "%(num)s mating tanks will lose it as a parent", mating_refs[t.id]))
            if sac_refs.get(t.id, 0):
                refs.append(ngettext("%(num)s sac-log entry will lose its tank",
                                     "%(num)s sac-log entries will lose its tank", sac_refs[t.id]))
            affected = "; ".join(refs)
            # A mating tank still out: where each live group goes back to.
            plan = [{"fish": f, "home": home if home in tank_by_id else None,
                     "home_code": tank_by_id[home].tank_id if home in tank_by_id else ""}
                    for f in returning.get(t.id, []) if fish_alive(f)
                    for home in [zf_mating_home(f, t)]] if returnable else []
            tank_rows.append({
                "row": t, "total_fish": total, "position": position, "editable": editable,
                "attrs": custom_fields.values(t),
                "mine": t.owner == me, "shared": zf_tank_shared(t), "fish_rows": len(groups),
                "share_group": project_groups.name_of(t.share_group_id) if zf_tank_shared(t) else "",
                "returnable": returnable, "return_plan": plan,
                "return_ready": all(p["home"] for p in plan),
                "return_confirm": zf_return_confirm(t, [(p["fish"].count or 0, p["home_code"]) for p in plan]),
                "confirm": (gettext("Delete tank %(tank)s? %(affected)s.", tank=t.tank_id,
                                    affected=affected[0].upper() + affected[1:]) if affected
                            else gettext("Delete tank %(tank)s?", tank=t.tank_id)),
                "payload": {
                    "id": t.id, "_label": t.tank_id, "_locked": not editable, "tank_id": t.tank_id,
                    "purpose": t.purpose, "line_id_fk": t.line_id_fk or "", "owner": t.owner,
                    "rack_id_fk": t.rack_id_fk or "", "position": position, "card_id": t.card_id,
                    "notes": t.notes, "active": "1" if t.active else "0",
                    "share_group": project_groups.value_of(True, t.share_group_id),
                    "rack_id_fk_was": t.rack_id_fk or "", "position_was": position, "owner_was": t.owner},
            })

        drawn_at = set(tank_sheet_rows)
        tank_sheet_rows = [tank_rows[i] for i in tank_sheet_rows]
        undrawn = [tr for i, tr in enumerate(tank_rows) if i not in drawn_at] if paged else []

        # ---- Fish ----
        fish_rows = []
        for f in fish:
            tank = f.tank
            editable = zf_can_edit(f)
            days = (today - f.date_of_fertilization).days if f.date_of_fertilization else None
            fish_rows.append({
                "row": f, "tank": tank, "alive": fish_alive(f), "editable": editable,
                "mine": bool(tank and tank.owner == me), "owner": tank.owner if tank else "",
                "age": _fish_age_label(f.date_of_fertilization), "age_days": days if days is not None and days >= 0 else "",
                "payload": {
                    "id": f.id, "_label": f.individual_id or f"#{f.id}", "_locked": not editable,
                    "tank_id_fk": f.tank_id_fk, "line_id_fk": f.line_id_fk or "", "count": f.count,
                    "individual_id": f.individual_id, "sex": f.sex, "status": f.status,
                    "date_of_fertilization": f.date_of_fertilization.isoformat() if f.date_of_fertilization else "",
                    "sac_date": f.sac_date.isoformat() if f.sac_date else "",
                    "genotype": f.genotype, "notes": f.notes},
            })

        # ---- Clutches: derived dates + age ----
        clutch_rows = []
        for c in clutches:
            dof = c.date_of_fertilization
            editable = zf_can_edit(c)
            n_fish = clutch_fish_refs.get(c.id, 0)
            clutch_rows.append({
                "row": c,
                "age": _fish_age_label(dof),
                "age_days": (today - dof).days if dof else "",
                "tank_up": dof + timedelta(days=5) if dof else None,
                "fin_clip": dof + timedelta(days=30) if dof else None,
                "adult": dof + timedelta(days=90) if dof else None,
                "father": tank_by_id[c.father_tank_id].tank_id if c.father_tank_id in tank_by_id else "",
                "mother": tank_by_id[c.mother_tank_id].tank_id if c.mother_tank_id in tank_by_id else "",
                "editable": editable, "mine": c.owner == me,
                "confirm": (ngettext("Delete clutch %(clutch)s? %(num)s fish row will no longer name it.",
                                     "Delete clutch %(clutch)s? %(num)s fish rows will no longer name it.", n_fish,
                                     clutch=c.clutch_id) if n_fish
                            else gettext("Delete clutch %(clutch)s?", clutch=c.clutch_id)),
                "payload": {
                    "id": c.id, "_label": c.clutch_id, "_locked": not editable, "clutch_id": c.clutch_id,
                    "date_of_fertilization": dof.isoformat() if dof else "", "line_id_fk": c.line_id_fk or "",
                    "father_tank_id": c.father_tank_id or "", "mother_tank_id": c.mother_tank_id or "",
                    "embryo_count": c.embryo_count, "larvae_count": c.larvae_count, "adults_count": c.adults_count,
                    "owner": c.owner, "notes": c.notes},
            })

        # ---- Lines ----
        line_rows = []
        children = {}
        for ln in lines:
            if ln.parent_line_id_fk:
                children[ln.parent_line_id_fk] = children.get(ln.parent_line_id_fk, 0) + 1
        line_by_id = {ln.id: ln for ln in lines}
        for ln in lines:
            n_tanks = sum(1 for t in tanks if t.line_id_fk == ln.id)
            uses = [(n_tanks, "tank", "tanks"), (fish_line_refs.get(ln.id, 0), "fish row", "fish rows"),
                    (clutch_line_refs.get(ln.id, 0), "clutch", "clutches"),
                    (children.get(ln.id, 0), "child line", "child lines")]
            used = ", ".join(bulk.count(n, one, many) for n, one, many in uses if n)
            live = live_by_line.get(ln.id, 0)
            line_editable = zf_can_edit(ln)
            line_rows.append({
                "row": ln, "tanks": n_tanks, "live": live, "active": live > 0, "used": used,
                "editable": line_editable, "mine": ln.owner == me,
                "parent": line_by_id[ln.parent_line_id_fk].name if ln.parent_line_id_fk in line_by_id else "",
                "payload": {
                    "id": ln.id, "_label": ln.name, "_locked": not line_editable, "name": ln.name,
                    "owner": ln.owner, "zfin_name": ln.zfin_name,
                    "background": ln.background, "transgene_summary": ln.transgene_summary,
                    "allele": ln.allele, "iacuc_protocol": ln.iacuc_protocol, "founder_info": ln.founder_info,
                    "parent_line_id_fk": ln.parent_line_id_fk or "", "notes": ln.notes},
            })

        # Per-system most recent water log.
        latest_logs = {}
        for sys_ in systems:
            latest_logs[sys_.id] = s.scalar(
                select(WaterLog).where(WaterLog.system_id_fk == sys_.id)
                .order_by(WaterLog.recorded_at.desc()).limit(1))
        # What a system delete would say, or why it is refused.
        system_info = {}
        for sys_ in systems:
            n_logs = s.scalar(select(func.count(WaterLog.id)).where(WaterLog.system_id_fk == sys_.id)) or 0
            system_info[sys_.id] = {
                "can_delete": access.can_edit_rack(sys_),
                "in_use": zf_system_uses(s, sys_),
                "confirm": (ngettext("Delete water system %(system)s? Its %(num)s reading is kept, listed under deleted systems.",
                                     "Delete water system %(system)s? Its %(num)s readings are kept, listed under deleted systems.", n_logs, system=sys_.name) if n_logs
                            else gettext("Delete water system %(system)s?", system=sys_.name)),
            }
        orphan_logs = s.scalars(select(WaterLog).where(WaterLog.system_id_fk.is_(None))
                                .order_by(WaterLog.recorded_at.desc()).limit(50)).all()

        geno_queue = [tr for tr in tank_rows if tr["row"].needs_genotyping]
        sac_log = s.scalars(select(FishSacLog).order_by(FishSacLog.recorded_at.desc()).limit(100)).all()
        next_tank_id = zf_next_code(s, TankRecord.tank_id, "T", 3)
        next_clutch_id = zf_next_code(s, ClutchRecord.clutch_id, f"C{today.strftime('%y%m%d')}-")
        usernames = current_lab_usernames(s)
        census = {
            "tanks": sum(1 for t in tanks if t.active),
            "fish": (sum(tr["total_fish"] for tr in tank_rows) if tank_tab else
                     sum(f.count or 0 for f in s.execute(
                         select(FishRecord.count, FishRecord.status, FishRecord.sac_date)
                         .join(TankRecord, FishRecord.tank_id_fk == TankRecord.id)) if fish_alive(f))),
            "clutches": s.scalar(select(func.count(ClutchRecord.id))) or 0,
            "lines": len(lines),
            "experiments": experiment_pages.tab_count(s, "zebrafish"),
        }
        experiments_tab = None
        if active_view == "experiments":
            place = experiment_pages.place_for(s, "zebrafish")
            experiments_tab = experiment_pages.tab_context(s, place) if place else None

    # Rack grid: tanks store 0-based rows/columns; the grid payload is
    # 1-based and the page tells rack-grid.js to convert back.
    fish_racks = {
        "racks": [{"id": r.id, "name": r.name, "rows": r.rows, "cols": r.cols,
                   "naming": positions.scheme(r.naming),
                   "edit": {"data-record-payload": json.dumps({
                       "id": r.id, "_label": r.name, "_locked": not access.can_edit_rack(r),
                       "name": r.name, "rows": r.rows,
                       "cols": r.cols, "system_id_fk": r.system_id_fk or "",
                       **{f"naming_{k}": v for k, v in positions.scheme(r.naming).items()}})}} for r in racks],
        "items": [{
            "id": tr["row"].id, "label": tr["row"].tank_id,
            "sub": tr["row"].line.name if tr["row"].line else tr["row"].purpose,
            "badge": str(tr["total_fish"]) if tr["total_fish"] else "",
            "tone": tr["row"].purpose if tr["row"].active else "inactive",
            "flag": tr["row"].needs_genotyping,
            "rack": tr["row"].rack_id_fk,
            "row": tr["row"].row + 1 if tr["row"].row is not None else None,
            "col": tr["row"].col + 1 if tr["row"].col is not None else None,
            "title": " · ".join(filter(None, [tr["row"].tank_id, tr["row"].line.name if tr["row"].line else "",
                                             tr["row"].purpose, f'{tr["total_fish"]} fish' if tr["total_fish"] else "",
                                             "needs genotyping" if tr["row"].needs_genotyping else ""])),
            "search": " ".join(filter(None, [tr["row"].tank_id, tr["row"].purpose, tr["row"].owner, tr["row"].card_id,
                                             tr["row"].line.name if tr["row"].line else ""])).lower(),
            "edit": {"data-record-edit": "tank-dialog", "data-record-payload": json.dumps(tr["payload"])},
        } for tr in (tank_rows if tank_mode == "grid" else [])],   # the tiles: only the grid shows them
        "create": {"attrs": {"data-record-edit": "tank-dialog"},
                   "payload": {"tank_id": next_tank_id, "purpose": "stock", "owner": me, "active": "1"},
                   "rack_field": "rack_id_fk", "text_field": "position"},
    }

    with SessionLocal() as s:
        zf_columns = custom_fields.fields(s, "zebrafish")
    return {
        "active_view": active_view,
        # The Tanks tab's sheet: the rows it draws, a stub for each other
        # tank, and where its script fetches their rows.
        "tank_sheet_rows": tank_sheet_rows,
        "tank_stubs": zf_tank_stubs(undrawn, custom_fields.in_table(zf_columns), racks, lines) if undrawn else [],
        "tank_rows_url": url_for("zebrafish_tank_rows") if undrawn else "",
        "zebrafish_views": ZEBRAFISH_VIEWS,
        "custom_columns": custom_fields.in_table(zf_columns),
        "custom_columns_all": zf_columns,
        "fish_racks": fish_racks,
        "tank_purpose_options": TANK_PURPOSE_OPTIONS,
        "fish_sex_options": FISH_SEX_OPTIONS,
        "fish_status_options": FISH_STATUS_OPTIONS,
        "fish_dead_statuses": sorted(FISH_DEAD_STATUSES),
        "systems": systems,
        "racks": racks,
        "lines": lines,
        "line_rows": line_rows,
        "tanks": tanks,
        "tank_rows": tank_rows,
        "fish_rows": fish_rows,
        "clutch_rows": clutch_rows,
        "latest_logs": latest_logs,
        "system_info": system_info,
        "orphan_logs": orphan_logs,
        "geno_queue": geno_queue,
        "sac_log": sac_log,
        "census": census,
        "experiments_tab": experiments_tab,
        "usernames": usernames,
        "me": me,
        "next_tank_id": next_tank_id,
        "next_clutch_id": next_clutch_id,
        "now_date": today,
        "today_iso": today.isoformat(),
    }


@app.route("/zebrafish")
@login_required
def zebrafish():
    view = request.args.get("view", "tanks")
    if view in ZEBRAFISH_VIEW_ALIASES:
        return redirect(url_for("zebrafish", view="tanks", mode=ZEBRAFISH_VIEW_ALIASES[view]))
    if view not in ZEBRAFISH_VIEWS:
        view = "tanks"
    # A long tank sheet draws its first page, the rest fetched as shown (_zebrafish_context).
    return render_template("zebrafish.html", **_zebrafish_context(view))


@app.route("/zebrafish/tanks/rows")
@login_required
def zebrafish_tank_rows():
    """Rows of a long tank sheet by id (?ids=…, with the page's own
    arguments), as the page draws them; ?as=text for their words."""
    ids = sheet_paging.ids_asked(request)
    context = _zebrafish_context("tanks", tank_ids=set(ids))
    return sheet_paging.answer(render_template("zebrafish.html", **context), "zf-tanks-v2", ids, request)


def zebrafish_home_summary() -> dict:
    """The home page's zebrafish card: mating tanks due back, and tanks
    waiting to be genotyped."""
    horizon = date.today() + timedelta(days=1)
    with SessionLocal() as s:
        mating = s.scalars(
            select(TankRecord).options(selectinload(TankRecord.fish))
            .where(TankRecord.active.is_(True), TankRecord.purpose == "mating",
                   TankRecord.mating_return_at.is_not(None),
                   TankRecord.mating_return_at <= horizon)
            .order_by(TankRecord.mating_return_at).limit(8)).all()
        codes = dict(s.execute(select(TankRecord.id, TankRecord.tank_id)).all())
        geno = positions.natural_sorted(s.scalars(
            select(TankRecord).options(joinedload(TankRecord.line))
            .where(TankRecord.needs_genotyping.is_(True))).all(), key=lambda t: t.tank_id)
        return {
            "any": bool(s.scalar(select(func.count(TankRecord.id)))),
            "mating": [{"id": t.id, "tank_id": t.tank_id, "due": t.mating_return_at, "owner": t.owner,
                        "overdue": t.mating_return_at < date.today(), "editable": zf_can_edit(t),
                        # Returned straight from here when every group has a home tank;
                        # otherwise the tanks page asks where they go.
                        "ready": all(zf_mating_home(f, t) in codes for f in live),
                        "confirm": zf_return_confirm(t, [(f.count or 0, codes.get(zf_mating_home(f, t), ""))
                                                         for f in live])}
                       for t in mating for live in [[f for f in t.fish if fish_alive(f)]]],
            "geno": [{"id": t.id, "tank_id": t.tank_id, "line": t.line.name if t.line else "", "owner": t.owner}
                     for t in geno[:6]],
            "geno_total": len(geno),
        }


# ---- Tanks -----------------------------------------------------------------


def fish_position_label(tank) -> str:
    """A tank's position under its rack's naming scheme ("C3"). Tanks store
    0-based rows and columns."""
    rack = tank.rack
    if rack is None or tank.row is None or tank.col is None:
        return ""
    return positions.label(tank.row + 1, tank.col + 1, rack.naming, rack.cols)


def zf_free_cell(s, rack, exclude_id=None):
    """The first empty (row, col) of a rack, 0-based, or None when full."""
    taken = {(r, c) for r, c in s.execute(
        select(TankRecord.row, TankRecord.col).where(
            TankRecord.rack_id_fk == rack.id, TankRecord.id != (exclude_id or 0)))}
    for r in range(rack.rows):
        for c in range(rack.cols):
            if (r, c) not in taken:
                return r, c
    return None


def zf_place(tank, rack, row=None, col=None) -> None:
    """Put a tank in a rack cell (0-based), or no rack. The column is set
    directly as well as the relationship: the change history (and so
    batch undo) only sees columns written by hand."""
    tank.rack_id_fk = rack.id if rack is not None else None
    tank.rack = rack
    tank.row, tank.col = (row, col) if rack is not None else (None, None)


def apply_fish_position(s, tank, rack_raw, position_raw) -> str | None:
    """Place a tank from a rack and a typed position; an error message
    instead of a guess. No rack unplaces the tank whatever the position
    says. A rack with a blank position keeps the tank's cell if it is
    already there, otherwise takes the first free one."""
    rack_raw = str(rack_raw or "").strip()
    raw = str(position_raw or "").strip()
    if not rack_raw:
        zf_place(tank, None)
        return None
    rack = s.get(FishRack, int(rack_raw)) if rack_raw.isdigit() else None
    if rack is None:
        return gettext("That rack no longer exists. Reload the page.")
    if not raw:
        if tank.rack_id_fk == rack.id and tank.row is not None:
            return None
        cell = zf_free_cell(s, rack, tank.id)
        if cell is None:
            return gettext("%(rack)s is full. Pick another rack, or make room first.", rack=rack.name)
        zf_place(tank, rack, *cell)
        return None
    cell = positions.parse(raw, rack.naming, rack.rows, rack.cols)
    if cell is None:
        return gettext("“%(position)s” is not a position in %(rack)s (%(first)s–%(last)s).", position=raw,
                       rack=rack.name, first=positions.label(1, 1, rack.naming, rack.cols),
                       last=positions.label(rack.rows, rack.cols, rack.naming, rack.cols))
    row, col = cell[0] - 1, cell[1] - 1
    holder = s.scalar(select(TankRecord).where(
        TankRecord.rack_id_fk == rack.id, TankRecord.row == row, TankRecord.col == col,
        TankRecord.id != (tank.id or 0)))
    if holder is not None:
        return gettext("%(rack)s · %(position)s already holds tank %(tank)s. Drag on the rack grid to swap.",
                       rack=rack.name, position=raw, tank=holder.tank_id)
    zf_place(tank, rack, row, col)
    return None


def zf_tank_state(t) -> dict:
    """What sheet.js refreshes on a tank row after a save."""
    return {"active": bool(t.active), "values": {
        "tank_id": t.tank_id, "purpose": t.purpose, "owner": t.owner,
        "rack_id_fk": str(t.rack_id_fk or ""), "position": fish_position_label(t)}}


def zf_active_flag(raw) -> bool:
    return str(raw if raw is not None else "1").strip().lower() not in ("0", "false", "no", "off")


def zf_code_run(s, first: str, n: int) -> list[str]:
    """n consecutive tank IDs: from a typed first one ending in a number
    (T050 → T050, T051…, keeping its width), or the next free T-codes.
    Refused when any of them is taken."""
    if not first:
        start = zf_next_code(s, TankRecord.tank_id, "T", 3)
        prefix, digits = "T", start[1:]
    else:
        m = re.match(r"^(.*?)(\d+)$", first)
        if m is None:
            raise ZfInputError(gettext("To add several tanks, give a first tank ID that ends in a number (T050), or leave it blank for the next free ones."))
        prefix, digits = m.group(1), m.group(2)
    codes = [f"{prefix}{int(digits) + i:0{len(digits)}d}" for i in range(n)]
    if any(len(c) > 80 for c in codes):
        raise ZfInputError(gettext("Keep the tank ID under %(max)s characters.", max=80))
    taken = positions.natural_sorted(set(s.scalars(select(TankRecord.tank_id).where(TankRecord.tank_id.in_(codes)))))
    if taken:
        raise ZfInputError(ngettext("%(tanks)s is already used. Start from another tank ID.",
                                    "%(tanks)s are already used. Start from another tank ID.", len(taken),
                                    tanks=", ".join(taken[:5]) + ("…" if len(taken) > 5 else "")))
    return codes


def zf_free_cells_from(s, rack, start, n: int) -> list[tuple[int, int]]:
    """Up to n empty cells (0-based) of a rack, reading along its rows from
    start (0-based (row, col), or the first cell)."""
    taken = {(r, c) for r, c in s.execute(
        select(TankRecord.row, TankRecord.col).where(TankRecord.rack_id_fk == rack.id))}
    begin = start[0] * rack.cols + start[1] if start else 0
    out = []
    for index in range(begin, rack.rows * rack.cols):
        cell = (index // rack.cols, index % rack.cols)
        if cell not in taken:
            out.append(cell)
            if len(out) == n:
                break
    return out


def zf_create_tanks(s, template: dict, codes: list[str], rack_id, position: str) -> tuple[str, bool]:
    """The New tank dialog's "How many": tanks with the same fields and
    consecutive IDs, side by side in the rack from the given position
    (the next free cells). One batch. Returns the message to show, and
    whether every tank found a place."""
    rack = s.get(FishRack, rack_id) if rack_id else None
    start = None
    if rack is not None and position:
        cell = positions.parse(position, rack.naming, rack.rows, rack.cols)
        if cell is None:
            raise ZfInputError(gettext("“%(position)s” is not a position in %(rack)s.", position=position,
                                       rack=rack.name))
        start = (cell[0] - 1, cell[1] - 1)
    cells = zf_free_cells_from(s, rack, start, len(codes)) if rack is not None else []
    with audit.batch(s, "create", f"new tanks ×{len(codes)} ({codes[0]}–{codes[-1]})", "tanks") as batch_row:
        made = []
        for i, code in enumerate(codes):
            tank = TankRecord(tank_id=code, **template)
            s.add(tank)
            if i < len(cells):
                zf_place(tank, rack, *cells[i])
            made.append(tank)
        batch_row.record_count = len(made)
    values = {"count": len(codes), "first": codes[0], "last": codes[-1]}
    if rack is None:
        return gettext("Created %(count)s tanks, %(first)s–%(last)s.", **values), True
    if cells:
        message = gettext("Created %(count)s tanks, %(first)s–%(last)s, in %(rack)s from %(start)s to %(end)s.",
                          rack=rack.name, start=positions.label(cells[0][0] + 1, cells[0][1] + 1, rack.naming, rack.cols),
                          end=positions.label(cells[-1][0] + 1, cells[-1][1] + 1, rack.naming, rack.cols), **values)
    else:
        message = gettext("Created %(count)s tanks, %(first)s–%(last)s, in %(rack)s.", rack=rack.name, **values)
    if len(cells) < len(codes):
        left = codes[len(cells):]
        message += " " + ngettext("%(rack)s had room for %(room)s; %(left)s is not placed.",
                                  "%(rack)s had room for %(room)s; %(left)s are not placed.", len(left),
                                  rack=rack.name, room=len(cells),
                                  left=left[0] if len(left) == 1 else left[0] + "–" + left[-1])
        return message, False
    return message, True


@app.route("/zebrafish/tanks/create", methods=["POST"])
@login_required
def zebrafish_create_tank():
    form = request.form
    with SessionLocal() as s:
        try:
            code = (form.get("tank_id") or "").strip()
            how_many = zf_int(form, "how_many", "How many", default=1, lo=1, hi=30)
            tank = TankRecord(
                tank_id=zf_unique(s, TankRecord.tank_id, code, "tank") if code and how_many == 1
                else code or zf_next_code(s, TankRecord.tank_id, "T", 3),
                purpose=zf_choice(form.get("purpose") or "stock", TANK_PURPOSE_OPTIONS, "Purpose"),
                line_id_fk=zf_ref(s, FishLine, form.get("line_id_fk"), "line"),
                owner=(form.get("owner", access.username()) or "").strip(),
                card_id=(form.get("card_id") or "").strip(),
                notes=(form.get("notes") or "").strip(),
                active=zf_active_flag(form.get("active")),
            )
            rack_id = zf_ref(s, FishRack, form.get("rack_id_fk"), "rack")
            position = (form.get("position") or "").strip()
            # A 0-based row/col (older clients) stands in for a typed position.
            row = zf_int(form, "row", "Row", lo=0)
            col = zf_int(form, "col", "Column", lo=0)
            if rack_id and not position and row is not None and col is not None:
                rack = s.get(FishRack, rack_id)
                if row >= rack.rows or col >= rack.cols:
                    raise ZfInputError(gettext("Row %(row)s, column %(col)s is outside %(rack)s (%(rows)s × %(cols)s).",
                                               row=row, col=col, rack=rack.name, rows=rack.rows, cols=rack.cols))
                position = positions.label(row + 1, col + 1, rack.naming, rack.cols)
            if how_many > 1:
                template = {k: getattr(tank, k) for k in ("purpose", "line_id_fk", "owner", "card_id", "notes", "active")}
                message, all_placed = zf_create_tanks(s, template, zf_code_run(s, code, how_many), rack_id, position)
        except ZfInputError as error:
            s.rollback()
            return zf_reply("tanks", str(error))
        if how_many > 1:
            s.commit()
            flash(message, "success" if all_placed else "warning")
            return zf_reply("tanks")
        s.add(tank)
        if rack_id:
            error = apply_fish_position(s, tank, rack_id, position)
            if error:
                zf_place(tank, None)
                flash(gettext("Tank %(tank)s was created but not placed: %(error)s", tank=tank.tank_id, error=error),
                      "error")
        s.commit()
    return zf_reply("tanks")


@app.route("/zebrafish/tanks/<int:tank_row_id>/update", methods=["POST"])
@login_required
def zebrafish_update_tank(tank_row_id: int):
    form = request.form
    with SessionLocal() as s:
        t = s.get(TankRecord, tank_row_id)
        if t is None:
            return zf_reply("tanks", gettext("That tank no longer exists. Reload the page."), status=404)
        if not zf_can_edit(t):
            return zf_reply("tanks", zf_denied(t), status=403)
        try:
            if "tank_id" in form:
                t.tank_id = zf_unique(s, TankRecord.tank_id, form.get("tank_id"), "tank", t.id)
            if "purpose" in form:
                t.purpose = zf_choice(form.get("purpose"), TANK_PURPOSE_OPTIONS, "Purpose", t.purpose)
            if "line_id_fk" in form:
                t.line_id_fk = zf_ref(s, FishLine, form.get("line_id_fk"), "line")
            # Owner, rack and position also show on the rack grid and the
            # dialog, so a stale row only writes what it actually changed.
            if "owner" in form and form_changed(form, "owner"):
                refused = stewardship.change_owner(s, t, form.get("owner"), "tank", t.tank_id)
                if refused:
                    raise ZfInputError(refused)
            for fld in ("card_id", "notes"):
                if fld in form:
                    setattr(t, fld, (form.get(fld) or "").strip())
            # Columns this lab added to Zebrafish: only the ones it sent.
            custom_fields.apply_form(t, form, custom_fields.fields(s, "zebrafish"))
            if "active" in form:
                t.active = zf_active_flag(form.get("active"))
            # Which group a breeding or shared tank is for ("1": the lab's).
            if "share_group" in form:
                refused = stewardship.change_sharing(t, form.get("share_group") or "1", "tank", t.tank_id,
                                                     shared=True, set_shared=False)
                if refused:
                    raise ZfInputError(refused)
        except ZfInputError as error:
            s.rollback()
            return zf_reply("tanks", str(error))
        if ("rack_id_fk" in form or "position" in form) and form_changed(form, "rack_id_fk", "position"):
            rack_raw = form.get("rack_id_fk") if "rack_id_fk" in form else t.rack_id_fk
            position = form.get("position", "")
            if "position_was" in form and not form_changed(form, "position"):
                # Only the rack changed: the cell text belongs to the old rack.
                position = ""
            error = apply_fish_position(s, t, rack_raw, position)
            if error:
                s.rollback()
                return zf_reply("tanks", error)
        s.commit()
        state = zf_tank_state(t)
    return zf_reply("tanks", row=state)


@app.route("/zebrafish/tanks/<int:tank_row_id>/move", methods=["POST"])
@login_required
def zebrafish_move_tank(tank_row_id: int):
    """Drag-and-drop on the rack grid (0-based row/col). Dropping on an
    occupied cell swaps the two tanks; a blank rack unplaces."""
    form = request.form
    with SessionLocal() as s:
        t = s.get(TankRecord, tank_row_id)
        if t is None:
            return jsonify({"ok": False, "error": gettext("That tank no longer exists.")}), 404
        if not zf_can_edit(t):
            return jsonify({"ok": False, "error": zf_denied(t)}), 403
        try:
            rack_id = zf_ref(s, FishRack, form.get("rack_id_fk"), "rack")
            row = zf_int(form, "row", "Row")
            col = zf_int(form, "col", "Column")
        except ZfInputError as error:
            return jsonify({"ok": False, "error": str(error)}), 409
        if rack_id is None:
            zf_place(t, None)
            s.commit()
            return jsonify({"ok": True})
        rack = s.get(FishRack, rack_id)
        if row is None or col is None or not (0 <= row < rack.rows and 0 <= col < rack.cols):
            return jsonify({"ok": False, "error": gettext("That cell is outside %(rack)s (%(rows)s × %(cols)s).",
                                                          rack=rack.name, rows=rack.rows, cols=rack.cols)}), 409
        occupant = s.scalar(select(TankRecord).where(
            TankRecord.rack_id_fk == rack.id, TankRecord.row == row, TankRecord.col == col,
            TankRecord.id != t.id))
        if occupant is not None:
            if not zf_can_edit(occupant):
                return jsonify({"ok": False, "error": zf_denied(occupant)}), 403
            occupant.rack_id_fk, occupant.row, occupant.col = t.rack_id_fk, t.row, t.col
        t.rack_id_fk, t.row, t.col = rack.id, row, col
        s.commit()
    return jsonify({"ok": True})


def zf_delete_tank(s, t) -> str | None:
    """Delete a tank, or say why not. Its fish rows would go with it, so a
    tank holding any is refused. Clutches, mating tanks and sac-log entries
    naming it keep everything else and lose the link — left in place, the
    id would point at whichever tank reuses it."""
    if t.fish:
        n = len(t.fish)
        return ngettext("Tank %(tank)s still holds %(num)s fish row. Move or delete them first.",
                        "Tank %(tank)s still holds %(num)s fish rows. Move or delete them first.", n, tank=t.tank_id)
    for c in s.scalars(select(ClutchRecord).where(
            (ClutchRecord.father_tank_id == t.id) | (ClutchRecord.mother_tank_id == t.id))):
        if c.father_tank_id == t.id:
            c.father_tank_id = None
        if c.mother_tank_id == t.id:
            c.mother_tank_id = None
    for m in s.scalars(select(TankRecord).where(
            (TankRecord.mating_father_tank_id == t.id) | (TankRecord.mating_mother_tank_id == t.id))):
        if m.mating_father_tank_id == t.id:
            m.mating_father_tank_id = None
        if m.mating_mother_tank_id == t.id:
            m.mating_mother_tank_id = None
    for entry in s.scalars(select(FishSacLog).where(FishSacLog.tank_id_fk == t.id)):
        entry.tank_id_fk = None
    s.delete(t)
    return None


@app.route("/zebrafish/tanks/<int:tank_row_id>/delete", methods=["POST"])
@login_required
def zebrafish_delete_tank(tank_row_id: int):
    with SessionLocal() as s:
        t = s.get(TankRecord, tank_row_id)
        if t is None:
            return redirect(url_for("zebrafish", view="tanks"))
        if not zf_can_edit(t):
            flash(zf_denied(t), "error")
            return redirect(url_for("zebrafish", view="tanks"))
        code = t.tank_id
        # A batch, so the delete and the links it clears undo together.
        with audit.batch(s, "delete", f"delete tank {code}", "tanks"):
            error = zf_delete_tank(s, t)
        if error:
            s.rollback()
            flash(error, "error")
        else:
            s.commit()
            flash(gettext("Deleted tank %(tank)s. Undo it from Batch history if that was a mistake.", tank=code),
                  "success")
    return redirect(url_for("zebrafish", view="tanks"))


@app.route("/zebrafish/tanks/<int:tank_row_id>/duplicate", methods=["POST"])
@login_required
def zebrafish_duplicate_tank(tank_row_id: int):
    """A new tank like this one — same purpose, line and rack (first free
    cell) — with the next free ID, owned by whoever made the copy."""
    with SessionLocal() as s:
        t = s.get(TankRecord, tank_row_id)
        if t is None:
            return redirect(url_for("zebrafish", view="tanks"))
        copy = TankRecord(tank_id=zf_next_code(s, TankRecord.tank_id, "T", 3), purpose=t.purpose,
                          line_id_fk=t.line_id_fk, owner=access.username(), card_id="", notes=t.notes)
        s.add(copy)
        note = ""
        if t.rack is not None:
            cell = zf_free_cell(s, t.rack)
            if cell:
                zf_place(copy, t.rack, *cell)
            else:
                note = " " + gettext("%(rack)s is full, so it is not placed.", rack=t.rack.name)
        s.commit()
        flash(gettext("Copied tank %(tank)s to %(copy)s.", tank=t.tank_id, copy=copy.tank_id) + note, "success")
    return redirect(url_for("zebrafish", view="tanks"))


@app.route("/zebrafish/tanks/<int:tank_row_id>/toggle-geno", methods=["POST"])
@login_required
def zebrafish_toggle_geno(tank_row_id: int):
    with SessionLocal() as s:
        t = s.get(TankRecord, tank_row_id)
        if t is None:
            return jsonify({"ok": False, "error": gettext("That tank no longer exists.")}), 404
        if not zf_can_edit(t):
            return jsonify({"ok": False, "error": zf_denied(t)}), 403
        t.needs_genotyping = not bool(t.needs_genotyping)
        s.commit()
        return jsonify({"ok": True, "needs_genotyping": t.needs_genotyping})


@app.route("/zebrafish/tanks/bulk", methods=["POST"])
@login_required
def zebrafish_bulk_tanks():
    """Batch actions on ticked tanks: set purpose or owner, move to a rack,
    flag for genotyping, return mating tanks, delete. Applied to the tanks
    you may edit."""
    form = request.form
    action = (form.get("action") or "").strip()
    value = (form.get("value") or "").strip()
    labels = {"purpose": "Set purpose", "owner": "Set owner", "rack": "Moved",
              "geno": "Genotyping flag", "return": "Returned", "delete": "Deleted"}
    with SessionLocal() as s:
        # A column the lab added to Zebrafish is set like any other.
        own = next((f for f in custom_fields.fields(s, "zebrafish")
                    if f"attr_{f['key']}" == action), None)
        if own is not None:
            tanks = s.scalars(select(TankRecord).where(TankRecord.id.in_(
                [int(i) for i in form.getlist("selected_ids") if i.isdigit()]))).all()
            return _bulk_custom_column(s, "zebrafish", own, value, url_for("zebrafish", view="tanks"),
                                       tanks, zf_can_edit, "tanks")
    if action not in labels:
        flash(gettext("Pick an action."), "error")
        return redirect(url_for("zebrafish", view="tanks"))
    with SessionLocal() as s:
        members = stewardship.lab_members(s) if action == "owner" else set()
        try:
            if action == "purpose":
                value = zf_choice(value, TANK_PURPOSE_OPTIONS, "Purpose")
            elif action == "owner":
                refused = stewardship.batch_owner_refused(members, value)
                if refused:
                    raise ZfInputError(refused)
            rack = s.get(FishRack, zf_ref(s, FishRack, value, "rack")) if action == "rack" and value else None
        except ZfInputError as error:
            flash(str(error), "error")
            return redirect(url_for("zebrafish", view="tanks"))
        what = {"purpose": f"set purpose = {value}", "owner": f"set owner = {value or '(blank)'}",
                "rack": f"move to {rack.name if rack else '(no rack)'}", "geno": f"genotyping flag {'on' if value != '0' else 'off'}",
                "return": "return mating tanks", "delete": "delete tanks"}[action]

        def apply(t):
            if action == "purpose":
                t.purpose = value
            elif action == "owner":
                # Editing a shared breeding tank doesn't make it yours to give away.
                if not stewardship.may_give_away(t):
                    return bulk.NOT_YOURS
                return stewardship.change_owner(s, t, value, "tank", t.tank_id, members)
            elif action == "geno":
                t.needs_genotyping = value != "0"
            elif action == "rack":
                if rack is not None and t.rack_id_fk == rack.id and t.row is not None:
                    return bulk.UNCHANGED
                error = apply_fish_position(s, t, rack.id if rack else "", "")
                if error:
                    return f"{t.tank_id}: {error}"
                s.flush()
            elif action == "return":
                error, _ = zf_return_mating(s, t, {})
                if error:
                    # Messages that already begin with the tank are not prefixed with it again.
                    names_it = (gettext("Tank %(tank)s is not a mating tank.", tank=t.tank_id),
                                gettext("Mating tank %(tank)s was already returned.", tank=t.tank_id),
                                zf_denied(t))
                    return error if error in names_it else f"{t.tank_id}: {error}"
                s.flush()
            elif action == "delete":
                return zf_delete_tank(s, t)
            return None

        done = bulk.run(s, bulk.selected(s, TankRecord, form), apply, may_edit=zf_can_edit,
                        action="delete" if action == "delete" else "update", what=what, table="tanks")
        s.commit()
    bulk.report(done, gettext(labels[action]), "tank")
    return redirect(url_for("zebrafish", view="tanks"))


# ---- Fish (group rows) -----------------------------------------------------


def zf_apply_fish_status(s, f, previous_status: str | None) -> None:
    """The mouse rule for fish. Turning sac (or dead) stamps today as the
    sac date unless one was given, and logs the fish on the sac log;
    turning back to a living status clears the date and that log entry."""
    dead_now = (f.status or "").strip().lower() in FISH_DEAD_STATUSES
    dead_before = (previous_status or "").strip().lower() in FISH_DEAD_STATUSES
    if dead_now and not dead_before:
        f.sac_date = f.sac_date or date.today()
        if f.count:
            s.flush()  # a new row needs its id for the log link
            s.add(FishSacLog(tank_id_fk=f.tank_id_fk, line_id_fk=f.line_id_fk or (f.tank.line_id_fk if f.tank else None),
                             fish_id_fk=f.id, count=f.count, reason=f"{FISH_STATUS_SAC_REASON}{f.status}",
                             recorded_by=access.username()))
    elif dead_before and not dead_now:
        f.sac_date = None
        # Only the entry the status change wrote: fish logged by hand from
        # this row were sac'd and stay on the log.
        for entry in s.scalars(select(FishSacLog).where(FishSacLog.fish_id_fk == f.id)) if f.id else []:
            if (entry.reason or "").startswith(FISH_STATUS_SAC_REASON):
                s.delete(entry)


def zf_fish_state(f) -> dict:
    return {"active": fish_alive(f), "values": {
        "status": f.status, "count": f.count, "sex": f.sex, "tank_id_fk": str(f.tank_id_fk or ""),
        "sac_date": f.sac_date.isoformat() if f.sac_date else ""}}


def zf_fish_from_form(s, f, form) -> None:
    """Write the posted fields onto a fish row. Raises ZfInputError."""
    if "individual_id" in form:
        f.individual_id = (form.get("individual_id") or "").strip()
    for fld in ("genotype", "notes"):
        if fld in form:
            setattr(f, fld, (form.get(fld) or "").strip())
    if "count" in form:
        f.count = zf_int(form, "count", "Count", default=0, lo=0, hi=100000)
    if "sex" in form:
        f.sex = zf_choice(form.get("sex"), FISH_SEX_OPTIONS, "Sex", f.sex)
    if "status" in form:
        f.status = zf_choice(form.get("status"), FISH_STATUS_OPTIONS + ["dead"], "Status", f.status)
    if "line_id_fk" in form:
        f.line_id_fk = zf_ref(s, FishLine, form.get("line_id_fk"), "line")
    if "date_of_fertilization" in form:
        f.date_of_fertilization = zf_date(form, "date_of_fertilization", "Fertilised")
    if "sac_date" in form:
        f.sac_date = zf_date(form, "sac_date", "Sac date")


@app.route("/zebrafish/fish/create", methods=["POST"])
@login_required
def zebrafish_create_fish():
    form = request.form
    with SessionLocal() as s:
        try:
            tank_id = zf_ref(s, TankRecord, form.get("tank_id_fk"), "tank")
            if tank_id is None:
                raise ZfInputError(gettext("Choose the tank these fish are in."))
            tank = s.get(TankRecord, tank_id)
            if not zf_can_edit(tank):
                raise ZfInputError(zf_denied(tank))
            f = FishRecord(tank=tank, count=1, sex="mixed", status="alive")
            zf_fish_from_form(s, f, form)
        except ZfInputError as error:
            return zf_reply("fish", str(error))
        s.add(f)
        zf_apply_fish_status(s, f, "alive")
        s.commit()
    return zf_reply("fish")


@app.route("/zebrafish/fish/<int:fish_row_id>/update", methods=["POST"])
@login_required
def zebrafish_update_fish(fish_row_id: int):
    form = request.form
    with SessionLocal() as s:
        f = s.get(FishRecord, fish_row_id)
        if f is None:
            return zf_reply("fish", gettext("That fish row no longer exists. Reload the page."), status=404)
        if not zf_can_edit(f):
            return zf_reply("fish", zf_denied(f), status=403)
        previous_status = f.status
        try:
            if "tank_id_fk" in form:
                tank_id = zf_ref(s, TankRecord, form.get("tank_id_fk"), "tank")
                if tank_id is None:
                    raise ZfInputError(gettext("Fish have to be in a tank."))
                if tank_id != f.tank_id_fk:
                    tank = s.get(TankRecord, tank_id)
                    if not zf_can_edit(tank):
                        raise ZfInputError(gettext("Can’t move fish into %(tank)s: %(reason)s", tank=tank.tank_id,
                                                   reason=zf_denied(tank)))
                    f.tank_id_fk, f.tank = tank.id, tank  # the column too, for the change history
            zf_fish_from_form(s, f, form)
        except ZfInputError as error:
            s.rollback()
            return zf_reply("fish", str(error))
        zf_apply_fish_status(s, f, previous_status)
        s.commit()
        state = zf_fish_state(f)
    return zf_reply("fish", row=state)


@app.route("/zebrafish/fish/<int:fish_row_id>/duplicate", methods=["POST"])
@login_required
def zebrafish_duplicate_fish(fish_row_id: int):
    with SessionLocal() as s:
        f = s.get(FishRecord, fish_row_id)
        if f is None:
            return redirect(url_for("zebrafish", view="fish"))
        if not zf_can_edit(f):
            flash(zf_denied(f), "error")
            return redirect(url_for("zebrafish", view="fish"))
        s.add(FishRecord(tank_id_fk=f.tank_id_fk, line_id_fk=f.line_id_fk, individual_id="", count=f.count,
                         sex=f.sex, status=f.status, date_of_fertilization=f.date_of_fertilization,
                         sac_date=f.sac_date, clutch_id_fk=f.clutch_id_fk, genotype=f.genotype, notes=f.notes))
        s.commit()
        flash(gettext("Copied the fish row."), "success")
    return redirect(url_for("zebrafish", view="fish"))


@app.route("/zebrafish/fish/<int:fish_row_id>/delete", methods=["POST"])
@login_required
def zebrafish_delete_fish(fish_row_id: int):
    with SessionLocal() as s:
        f = s.get(FishRecord, fish_row_id)
        if f is not None:
            if not zf_can_edit(f):
                flash(zf_denied(f), "error")
                return redirect(url_for("zebrafish", view="fish"))
            # The sac log is history: it keeps the entry, not the link.
            for entry in s.scalars(select(FishSacLog).where(FishSacLog.fish_id_fk == f.id)):
                entry.fish_id_fk = None
            s.delete(f)
            s.commit()
    return redirect(url_for("zebrafish", view="fish"))


@app.route("/zebrafish/fish/bulk", methods=["POST"])
@login_required
def zebrafish_bulk_fish():
    """Batch actions on ticked fish rows: set status, move to a tank, sac."""
    form = request.form
    action = (form.get("action") or "").strip()
    value = (form.get("value") or "").strip()
    labels = {"status": "Set status", "tank": "Moved", "sac": "Sac’d"}
    if action not in labels:
        flash(gettext("Pick an action."), "error")
        return redirect(url_for("zebrafish", view="fish"))
    with SessionLocal() as s:
        try:
            if action == "status":
                value = zf_choice(value, FISH_STATUS_OPTIONS + ["dead"], "Status")
            tank = s.get(TankRecord, zf_ref(s, TankRecord, value, "tank") or 0) if action == "tank" else None
            if action == "tank" and tank is None:
                raise ZfInputError(gettext("Choose the tank to move them to."))
            if tank is not None and not zf_can_edit(tank):
                raise ZfInputError(gettext("Can’t move fish into %(tank)s: %(reason)s", tank=tank.tank_id,
                                           reason=zf_denied(tank)))
        except ZfInputError as error:
            flash(str(error), "error")
            return redirect(url_for("zebrafish", view="fish"))
        what = {"status": f"set status = {value}", "tank": f"move to tank {tank.tank_id if tank else ''}",
                "sac": "sac fish"}[action]

        def apply(f):
            previous = f.status
            if action == "tank":
                f.tank_id_fk, f.tank = tank.id, tank  # the column too, so undo can move them back
                return None
            if action == "sac" and (f.status or "").lower() in FISH_DEAD_STATUSES:
                return bulk.UNCHANGED
            f.status = "sac" if action == "sac" else value
            zf_apply_fish_status(s, f, previous)
            return None

        done = bulk.run(s, bulk.selected(s, FishRecord, form), apply, may_edit=zf_can_edit,
                        action="update", what=what, table="fish")
        s.commit()
    bulk.report(done, gettext(labels[action]), "fish row")
    return redirect(url_for("zebrafish", view="fish"))


# ---- Lines -----------------------------------------------------------------


def zf_line_parent(s, line_id, raw):
    """A parent line that keeps the lineage a tree: not the line itself,
    and not one of its own descendants."""
    parent_id = zf_ref(s, FishLine, raw, "parent line")
    if parent_id is None or line_id is None:
        return parent_id
    seen = set()
    cursor = parent_id
    while cursor is not None and cursor not in seen:
        if cursor == line_id:
            raise ZfInputError(gettext("A line can’t descend from itself. Pick a parent that is not this line or one of its descendants."))
        seen.add(cursor)
        cursor = s.scalar(select(FishLine.parent_line_id_fk).where(FishLine.id == cursor))
    return parent_id


LINE_TEXT_FIELDS = ("zfin_name", "background", "transgene_summary", "allele", "iacuc_protocol", "founder_info", "notes")


@app.route("/zebrafish/lines/create", methods=["POST"])
@login_required
def zebrafish_create_line():
    form = request.form
    with SessionLocal() as s:
        try:
            ln = FishLine(name=zf_unique(s, FishLine.name, form.get("name"), "line", max_len=200),
                          parent_line_id_fk=zf_line_parent(s, None, form.get("parent_line_id_fk")),
                          # Whoever makes a line owns it unless they name someone.
                          owner=(form.get("owner") or "").strip()[:80] or access.username(),
                          **{fld: (form.get(fld) or "").strip() for fld in LINE_TEXT_FIELDS})
        except ZfInputError as error:
            return zf_reply("lines", str(error))
        s.add(ln)
        s.commit()
    return zf_reply("lines")


@app.route("/zebrafish/lines/<int:line_id>/update", methods=["POST"])
@login_required
def zebrafish_update_line(line_id: int):
    form = request.form
    with SessionLocal() as s:
        ln = s.get(FishLine, line_id)
        if ln is None:
            return zf_reply("lines", gettext("That line no longer exists. Reload the page."), status=404)
        if not zf_can_edit(ln):
            return zf_reply("lines", zf_denied(ln), status=403)
        try:
            if "name" in form:
                ln.name = zf_unique(s, FishLine.name, form.get("name"), "line", ln.id, max_len=200)
            for fld in LINE_TEXT_FIELDS:
                if fld in form:
                    setattr(ln, fld, (form.get(fld) or "").strip())
            if "parent_line_id_fk" in form:
                ln.parent_line_id_fk = zf_line_parent(s, ln.id, form.get("parent_line_id_fk"))
            if "owner" in form:
                refused = stewardship.change_owner(s, ln, form.get("owner"), "line", ln.name)
                if refused:
                    raise ZfInputError(refused)
        except ZfInputError as error:
            s.rollback()
            return zf_reply("lines", str(error))
        s.commit()
        state = {"values": {"name": ln.name, "owner": ln.owner, "parent_line_id_fk": str(ln.parent_line_id_fk or "")}}
    return zf_reply("lines", row=state)


def zf_line_uses(s, ln) -> str:
    """What still uses a line, in words; empty when nothing does."""
    uses = [
        (s.scalar(select(func.count(TankRecord.id)).where(TankRecord.line_id_fk == ln.id)), "tank", "tanks"),
        (s.scalar(select(func.count(FishRecord.id)).where(FishRecord.line_id_fk == ln.id)), "fish row", "fish rows"),
        (s.scalar(select(func.count(ClutchRecord.id)).where(ClutchRecord.line_id_fk == ln.id)), "clutch", "clutches"),
        (s.scalar(select(func.count(FishLine.id)).where(FishLine.parent_line_id_fk == ln.id)), "child line", "child lines"),
    ]
    return ", ".join(bulk.count(n, one, many) for n, one, many in uses if n)


def zf_delete_line(s, ln) -> str | None:
    """Delete a line, or say what still uses it. Sac-log entries are
    history and only lose the link."""
    used = zf_line_uses(s, ln)
    if used:
        return gettext("Line %(line)s is still used by %(used)s. Reassign or delete those first.", line=ln.name,
                       used=used)
    for entry in s.scalars(select(FishSacLog).where(FishSacLog.line_id_fk == ln.id)):
        entry.line_id_fk = None
    s.delete(ln)
    return None


@app.route("/zebrafish/lines/<int:line_id>/delete", methods=["POST"])
@login_required
def zebrafish_delete_line(line_id: int):
    with SessionLocal() as s:
        ln = s.get(FishLine, line_id)
        if ln is None:
            return redirect(url_for("zebrafish", view="lines"))
        if not zf_can_edit(ln):
            flash(zf_denied(ln), "error")
            return redirect(url_for("zebrafish", view="lines"))
        name = ln.name
        with audit.batch(s, "delete", f"delete line {name}", "fish_lines"):
            error = zf_delete_line(s, ln)
        if error:
            s.rollback()
            flash(error, "error")
        else:
            s.commit()
            flash(gettext("Deleted line %(line)s.", line=name), "success")
    return redirect(url_for("zebrafish", view="lines"))


@app.route("/zebrafish/lines/<int:line_id>/duplicate", methods=["POST"])
@login_required
def zebrafish_duplicate_line(line_id: int):
    with SessionLocal() as s:
        ln = s.get(FishLine, line_id)
        if ln is None:
            return redirect(url_for("zebrafish", view="lines"))
        taken = set(s.scalars(select(FishLine.name)))
        name, n = f"{ln.name} copy", 2
        while name in taken:
            name, n = f"{ln.name} copy {n}", n + 1
        s.add(FishLine(name=name, parent_line_id_fk=ln.parent_line_id_fk, owner=access.username(),
                       **{fld: getattr(ln, fld) for fld in LINE_TEXT_FIELDS}))
        s.commit()
        flash(gettext("Copied line %(line)s to %(copy)s.", line=ln.name, copy=name), "success")
    return redirect(url_for("zebrafish", view="lines"))


@app.route("/zebrafish/lines/bulk", methods=["POST"])
@login_required
def zebrafish_bulk_lines():
    """Batch actions on ticked lines: set background, IACUC protocol or
    owner, delete (lines still in use are kept, and named). Applied to the
    lines you may edit."""
    form = request.form
    action = (form.get("action") or "").strip()
    value = (form.get("value") or "").strip()
    labels = {"background": "Set background", "iacuc_protocol": "Set IACUC #", "owner": "Set owner",
              "delete": "Deleted"}
    if action not in labels:
        flash(gettext("Pick an action."), "error")
        return redirect(url_for("zebrafish", view="lines"))
    with SessionLocal() as s:
        members = stewardship.lab_members(s) if action == "owner" else set()
        refused = stewardship.batch_owner_refused(members, value) if action == "owner" else None
        if refused:
            flash(refused, "error")
            return redirect(url_for("zebrafish", view="lines"))
        what = "delete lines" if action == "delete" else f"{labels[action].lower()} = {value or '(blank)'}"

        def apply(ln):
            if action == "delete":
                error = zf_delete_line(s, ln)
                if error:
                    return error
                s.flush()
            elif action == "owner":
                if not stewardship.may_give_away(ln):
                    return bulk.NOT_YOURS
                return stewardship.change_owner(s, ln, value, "line", ln.name, members)
            else:
                setattr(ln, action, value)
            return None

        done = bulk.run(s, bulk.selected(s, FishLine, form), apply, may_edit=zf_can_edit,
                        action="delete" if action == "delete" else "update", what=what, table="fish_lines")
        s.commit()
    bulk.report(done, gettext(labels[action]), "line")
    return redirect(url_for("zebrafish", view="lines"))


@app.route("/zebrafish/lines/<int:line_id>")
@login_required
def zebrafish_line_detail(line_id: int):
    """Per-line detail page with founder lineage tree."""
    with SessionLocal() as s:
        ln = s.get(FishLine, line_id)
        if ln is None:
            flash(gettext("Line not found."), "error")
            return redirect(url_for("zebrafish", view="lines"))

        # The ancestor chain. The seen set stops at a cycle written before
        # parents were checked, rather than looping forever.
        ancestors = []
        seen = {ln.id}
        cursor = ln.parent_line
        while cursor is not None and cursor.id not in seen and len(ancestors) < 20:
            ancestors.insert(0, cursor)
            seen.add(cursor.id)
            cursor = cursor.parent_line

        # Direct children.
        children = s.scalars(select(FishLine).where(FishLine.parent_line_id_fk == ln.id,
                                                    FishLine.id != ln.id)).all()

        # Tanks currently holding this line.
        # Racks are read after the session closes, so load them now.
        line_tanks = positions.natural_sorted(s.scalars(
            select(TankRecord).options(joinedload(TankRecord.rack))
            .where(TankRecord.line_id_fk == ln.id)).all(), key=lambda t: t.tank_id)
        tank_positions = {t.id: fish_position_label(t) for t in line_tanks}

        all_lines = positions.natural_sorted(s.scalars(select(FishLine)).all(), key=lambda line: line.name)
        editable = zf_can_edit(ln)
        denied = "" if editable else zf_denied(ln)
        usernames = current_lab_usernames(s)

    return render_template(
        "zebrafish_line_detail.html",
        line=ln, ancestors=ancestors, children=children,
        line_tanks=line_tanks, tank_positions=tank_positions, all_lines=all_lines,
        editable=editable, denied=denied, usernames=usernames,
    )


# ---- Racks -----------------------------------------------------------------


def zf_rack_fields(s, r, form) -> None:
    """Name, size, water system and naming from the rack dialog. Raises
    ZfInputError. Sizes are clamped to what the grid can draw."""
    if "name" in form or r.id is None:
        name = (form.get("name") or "").strip() or r.name or "Rack"
        clash = s.scalar(select(FishRack.id).where(FishRack.name == name, FishRack.id != (r.id or 0)))
        if clash is not None:
            raise ZfInputError(gettext("There is already a rack called %(name)s.", name=name))
        r.name = name
    r.rows = max(1, min(26, zf_int(form, "rows", "Rows", default=r.rows or 8)))
    r.cols = max(1, min(40, zf_int(form, "cols", "Columns", default=r.cols or 10)))
    if "system_id_fk" in form:
        r.system_id_fk = zf_ref(s, WaterSystem, form.get("system_id_fk"), "water system")
    if "naming_mode" in form or r.id is None:
        r.naming = json.dumps(positions.scheme_from_form(form))


@app.route("/zebrafish/racks/create", methods=["POST"])
@login_required
def zebrafish_create_rack():
    with SessionLocal() as s:
        r = FishRack(notes=(request.form.get("notes") or "").strip(), created_by=g.user.username)
        try:
            zf_rack_fields(s, r, request.form)
        except ZfInputError as error:
            flash(str(error), "error")
            return redirect(url_for("zebrafish", view="tanks", mode="grid"))
        s.add(r)
        s.commit()
    return redirect(url_for("zebrafish", view="tanks", mode="grid"))


@app.route("/zebrafish/racks/<int:rack_id>/update", methods=["POST"])
@login_required
def zebrafish_update_rack(rack_id: int):
    """Rename, resize or rename the positions of a rack. A layout that
    renames the places of tanks in it, or leaves some out, says whether each
    keeps its position name or its row and column (app/rack_layout.py)."""
    with SessionLocal() as s:
        r = s.get(FishRack, rack_id)
        if r is None:
            flash(gettext("That rack no longer exists."), "error")
            return redirect(url_for("zebrafish", view="tanks", mode="grid"))
        if not access.can_edit_rack(r):
            # Renaming or resizing a rack moves every tank in it: as for
            # mouse and fly racks, only whoever added it or an admin.
            flash(gettext("Only whoever added rack %(rack)s, or an admin, can change it.", rack=r.name), "error")
            return redirect(url_for("zebrafish", view="tanks", mode="grid"))
        form = request.form
        try:
            new = rack_layout.Layout(max(1, min(26, zf_int(form, "rows", "Rows", default=r.rows or 8))),
                                     max(1, min(40, zf_int(form, "cols", "Columns", default=r.cols or 10))),
                                     rack_layout.form_naming(form, r))
            change, refused = rack_layout.check(s, rack_layout.kind("fish_rack"), r, new, form.get(rack_layout.FIELD))
            if refused:
                raise ZfInputError(refused)
            with rack_layout.batch(s, change):
                zf_rack_fields(s, r, form)
                rack_layout.apply(s, change)
        except ZfInputError as error:
            s.rollback()
            flash(str(error), "error")
            return redirect(url_for("zebrafish", view="tanks", mode="grid"))
        s.commit()
        flash(gettext("Saved rack %(rack)s.", rack=r.name) + rack_layout.outcome(change), "success")
    return redirect(url_for("zebrafish", view="tanks", mode="grid"))


@app.route("/zebrafish/racks/<int:rack_id>/delete", methods=["POST"])
@login_required
def zebrafish_delete_rack(rack_id: int):
    """Delete a rack; its tanks stay, unplaced. Refused when that would
    move tanks you may not edit."""
    with SessionLocal() as s:
        r = s.get(FishRack, rack_id)
        if r is not None and not access.can_edit_rack(r):
            flash(gettext("Only whoever added rack %(rack)s, or an admin, can delete it.", rack=r.name), "error")
            return redirect(url_for("zebrafish", view="tanks", mode="grid"))
        if r is not None:
            theirs = [t.tank_id for t in r.tanks if not zf_can_edit(t)]
            if theirs:
                flash(gettext("%(rack)s holds tanks you can’t edit (%(tanks)s). Ask their owners, or an admin.",
                              rack=r.name, tanks=", ".join(theirs[:5]) + ("…" if len(theirs) > 5 else "")), "error")
                return redirect(url_for("zebrafish", view="tanks", mode="grid"))
            with audit.batch(s, "delete", f"delete rack {r.name}", "fish_racks"):
                for t in r.tanks:
                    t.rack_id_fk = None
                    t.row = None
                    t.col = None
                s.delete(r)
            s.commit()
    return redirect(url_for("zebrafish", view="tanks", mode="grid"))


# ---- Water systems + logs --------------------------------------------------


@app.route("/zebrafish/systems/create", methods=["POST"])
@login_required
def zebrafish_create_system():
    form = request.form
    with SessionLocal() as s:
        try:
            name = (form.get("name") or "").strip()
            if not name:
                raise ZfInputError(gettext("A water system needs a name."))
            if s.scalar(select(WaterSystem.id).where(WaterSystem.name == name)) is not None:
                raise ZfInputError(gettext("There is already a water system called %(name)s.", name=name))
            s.add(WaterSystem(
                name=name,
                room=(form.get("room") or "").strip(),
                target_temp_c=zf_float(form, "target_temp_c", "Target °C"),
                target_ph=zf_float(form, "target_ph", "Target pH"),
                target_conductivity=zf_float(form, "target_conductivity", "Target µS/cm"),
                notes=(form.get("notes") or "").strip(),
                created_by=access.username(),
            ))
        except ZfInputError as error:
            flash(str(error), "error")
            return redirect(url_for("zebrafish", view="water"))
        s.commit()
    return redirect(url_for("zebrafish", view="water"))


def zf_system_uses(s, system) -> str:
    """What still uses a water system, in words; empty when nothing does."""
    rack_ids = list(s.scalars(select(FishRack.id).where(FishRack.system_id_fk == system.id)))
    if not rack_ids:
        return ""
    n_tanks = s.scalar(select(func.count(TankRecord.id)).where(TankRecord.rack_id_fk.in_(rack_ids))) or 0
    racks = ngettext("%(num)s rack", "%(num)s racks", len(rack_ids))
    if not n_tanks:
        return racks
    return gettext("%(racks)s holding %(tanks)s", racks=racks, tanks=bulk.count(n_tanks, "tank", "tanks"))


@app.route("/zebrafish/systems/<int:system_id>/delete", methods=["POST"])
@login_required
def zebrafish_delete_system(system_id: int):
    """Delete a water system: its creator or an admin (systems from before
    creators were recorded are admin-only, like racks). Refused while racks
    use it. Its readings are history: they stay, listed under deleted
    systems."""
    from sqlalchemy import update as sa_update

    back = url_for("zebrafish", view="water")
    with SessionLocal() as s:
        system = s.get(WaterSystem, system_id)
        if system is None:
            flash(gettext("That water system no longer exists."), "error")
            return redirect(back)
        if not access.can_edit_rack(system):
            flash(access.denied_message("water system", system.created_by)
                  if system.created_by else
                  gettext("Only an admin can delete a water system added before creators were recorded."), "error")
            return redirect(back)
        used = zf_system_uses(s, system)
        if used:
            flash(gettext("%(system)s is still used by %(used)s. Move the racks to another system first (rack settings on the rack grid).", system=system.name, used=used), "error")
            return redirect(back)
        name = system.name
        with audit.batch(s, "delete", f"delete water system {name}", "water_systems"):
            # Unlink the readings in the database first: the relationship
            # would otherwise delete them along with the system.
            s.execute(sa_update(WaterLog).where(WaterLog.system_id_fk == system.id).values(system_id_fk=None))
            s.expire(system, ["water_logs"])
            s.delete(system)
        s.commit()
    flash(gettext("Deleted water system %(system)s. Its readings are kept under deleted systems.", system=name),
          "success")
    return redirect(back)


@app.route("/zebrafish/water/log", methods=["POST"])
@login_required
def zebrafish_log_water():
    form = request.form
    with SessionLocal() as s:
        try:
            system_id = zf_ref(s, WaterSystem, form.get("system_id_fk"), "water system")
            if system_id is None:
                raise ZfInputError(gettext("Pick the water system this reading is for."))
            log = WaterLog(
                system_id_fk=system_id,
                ph=zf_float(form, "ph", "pH"),
                conductivity=zf_float(form, "conductivity", "Conductivity"),
                temperature_c=zf_float(form, "temperature_c", "Temperature"),
                salinity=zf_float(form, "salinity", "Salinity"),
                alarm=form.get("alarm") in ("1", "on", "true", "yes"),
                recorded_by=access.username(),
                notes=(form.get("notes") or "").strip(),
            )
            if (all(v is None for v in (log.ph, log.conductivity, log.temperature_c, log.salinity))
                    and not log.alarm and not log.notes):
                raise ZfInputError(gettext("Enter at least one reading, a note or an alarm."))
        except ZfInputError as error:
            flash(str(error), "error")
            return redirect(url_for("zebrafish", view="water"))
        s.add(log)
        s.commit()
    return redirect(url_for("zebrafish", view="water"))


@app.route("/zebrafish/water/<int:system_id>/series.json")
@login_required
def zebrafish_water_series(system_id: int):
    """Return last 30 days of readings for one system — used by the trend chart."""
    cutoff = datetime.utcnow() - timedelta(days=30)
    with SessionLocal() as s:
        logs = s.scalars(
            select(WaterLog)
            .where(WaterLog.system_id_fk == system_id)
            .where(WaterLog.recorded_at >= cutoff)
            .order_by(WaterLog.recorded_at.asc())
        ).all()
        return jsonify({
            "ok": True,
            "series": [
                {
                    "t": log.recorded_at.isoformat(),
                    "ph": log.ph, "cond": log.conductivity,
                    "temp": log.temperature_c, "salinity": log.salinity,
                    "alarm": bool(log.alarm), "notes": log.notes or "",
                }
                for log in logs
            ],
        })


# ---- Clutches + mating wizard ---------------------------------------------


def zf_clutch_from_form(s, c, form) -> None:
    """Write the posted fields onto a clutch. Raises ZfInputError."""
    if "clutch_id" in form:
        c.clutch_id = zf_unique(s, ClutchRecord.clutch_id, form.get("clutch_id"), "clutch", c.id)
    if "date_of_fertilization" in form:
        c.date_of_fertilization = zf_date(form, "date_of_fertilization", "Fertilised") or c.date_of_fertilization
    if "line_id_fk" in form:
        c.line_id_fk = zf_ref(s, FishLine, form.get("line_id_fk"), "line")
    if "father_tank_id" in form:
        c.father_tank_id = zf_ref(s, TankRecord, form.get("father_tank_id"), "male tank")
    if "mother_tank_id" in form:
        c.mother_tank_id = zf_ref(s, TankRecord, form.get("mother_tank_id"), "female tank")
    for fld, label in (("embryo_count", "Embryos"), ("larvae_count", "Larvae"), ("adults_count", "Adults")):
        if fld in form:
            setattr(c, fld, zf_int(form, fld, label, default=0, lo=0, hi=1000000))
    if "owner" in form:
        refused = stewardship.change_owner(s, c, form.get("owner"), "clutch", c.clutch_id)
        if refused:
            raise ZfInputError(refused)
    if "notes" in form:
        c.notes = (form.get("notes") or "").strip()


@app.route("/zebrafish/clutches/create", methods=["POST"])
@login_required
def zebrafish_create_clutch():
    form = request.form
    with SessionLocal() as s:
        c = ClutchRecord(date_of_fertilization=date.today(), owner=access.username(),
                         embryo_count=0, larvae_count=0, adults_count=0, notes="")
        try:
            if not (form.get("clutch_id") or "").strip():
                c.clutch_id = zf_next_code(s, ClutchRecord.clutch_id, f"C{date.today().strftime('%y%m%d')}-")
            zf_clutch_from_form(s, c, {k: v for k, v in form.items() if k != "clutch_id" or v.strip()})
        except ZfInputError as error:
            return zf_reply("clutches", str(error))
        s.add(c)
        s.commit()
    return zf_reply("clutches")


@app.route("/zebrafish/clutches/<int:clutch_row_id>/update", methods=["POST"])
@login_required
def zebrafish_update_clutch(clutch_row_id: int):
    with SessionLocal() as s:
        c = s.get(ClutchRecord, clutch_row_id)
        if c is None:
            return zf_reply("clutches", gettext("That clutch no longer exists. Reload the page."), status=404)
        if not zf_can_edit(c):
            return zf_reply("clutches", zf_denied(c), status=403)
        try:
            zf_clutch_from_form(s, c, request.form)
        except ZfInputError as error:
            s.rollback()
            return zf_reply("clutches", str(error))
        s.commit()
        state = {"values": {"clutch_id": c.clutch_id, "owner": c.owner,
                            "date_of_fertilization": c.date_of_fertilization.isoformat()}}
    return zf_reply("clutches", row=state)


def zf_delete_clutch(s, c) -> None:
    """Fish raised from a clutch keep everything but the link."""
    for f in s.scalars(select(FishRecord).where(FishRecord.clutch_id_fk == c.id)):
        f.clutch_id_fk = None
    s.delete(c)


@app.route("/zebrafish/clutches/<int:clutch_row_id>/delete", methods=["POST"])
@login_required
def zebrafish_delete_clutch(clutch_row_id: int):
    with SessionLocal() as s:
        c = s.get(ClutchRecord, clutch_row_id)
        if c is not None:
            if not zf_can_edit(c):
                flash(zf_denied(c), "error")
                return redirect(url_for("zebrafish", view="clutches"))
            with audit.batch(s, "delete", f"delete clutch {c.clutch_id}", "clutches"):
                zf_delete_clutch(s, c)
            s.commit()
    return redirect(url_for("zebrafish", view="clutches"))


@app.route("/zebrafish/clutches/<int:clutch_row_id>/duplicate", methods=["POST"])
@login_required
def zebrafish_duplicate_clutch(clutch_row_id: int):
    """Another clutch from the same cross, dated today, owned by whoever
    made the copy."""
    with SessionLocal() as s:
        c = s.get(ClutchRecord, clutch_row_id)
        if c is None:
            return redirect(url_for("zebrafish", view="clutches"))
        copy = ClutchRecord(clutch_id=zf_next_code(s, ClutchRecord.clutch_id, f"C{date.today().strftime('%y%m%d')}-"),
                            date_of_fertilization=date.today(), line_id_fk=c.line_id_fk,
                            father_tank_id=c.father_tank_id, mother_tank_id=c.mother_tank_id,
                            embryo_count=0, larvae_count=0, adults_count=0, owner=access.username(), notes=c.notes)
        s.add(copy)
        s.commit()
        flash(gettext("Copied clutch %(clutch)s to %(copy)s.", clutch=c.clutch_id, copy=copy.clutch_id), "success")
    return redirect(url_for("zebrafish", view="clutches"))


@app.route("/zebrafish/clutches/bulk", methods=["POST"])
@login_required
def zebrafish_bulk_clutches():
    """Batch actions on ticked clutches: set owner, delete."""
    form = request.form
    action = (form.get("action") or "").strip()
    value = (form.get("value") or "").strip()
    labels = {"owner": "Set owner", "delete": "Deleted"}
    if action not in labels:
        flash(gettext("Pick an action."), "error")
        return redirect(url_for("zebrafish", view="clutches"))
    with SessionLocal() as s:
        members = stewardship.lab_members(s) if action == "owner" else set()
        refused = stewardship.batch_owner_refused(members, value) if action == "owner" else None
        if refused:
            flash(refused, "error")
            return redirect(url_for("zebrafish", view="clutches"))
        what = "delete clutches" if action == "delete" else f"set owner = {value or '(blank)'}"

        def apply(c):
            if action == "delete":
                zf_delete_clutch(s, c)
                return None
            if not stewardship.may_give_away(c):
                return bulk.NOT_YOURS
            return stewardship.change_owner(s, c, value, "clutch", c.clutch_id, members)

        done = bulk.run(s, bulk.selected(s, ClutchRecord, form), apply, may_edit=zf_can_edit,
                        action="delete" if action == "delete" else "update", what=what, table="clutches")
        s.commit()
    bulk.report(done, gettext(labels[action]), "clutch")
    return redirect(url_for("zebrafish", view="clutches"))


@app.route("/zebrafish/mate", methods=["POST"])
@login_required
def zebrafish_set_up_mating():
    """Create a mating tank from a ♂×♀ pair of tanks, with a return date
    (default tomorrow). The fish come out of both tanks, so both have to
    be yours to edit (or shared breeding tanks). Optional male and female
    counts move that many fish (rows marked M / F) into the mating tank;
    Returned on the tank puts them back."""
    form = request.form
    with SessionLocal() as s:
        try:
            father_id = zf_ref(s, TankRecord, form.get("father_tank_id"), "male tank")
            mother_id = zf_ref(s, TankRecord, form.get("mother_tank_id"), "female tank")
            if not father_id or not mother_id:
                raise ZfInputError(gettext("Pick both a male and a female tank."))
            if father_id == mother_id:
                raise ZfInputError(gettext("Pick two different tanks for the male and the female."))
            return_days = zf_int(form, "return_days", "Return in (days)", default=1, lo=1, hi=14)
            males = zf_int(form, "males", "Males", default=0, lo=0, hi=1000)
            females = zf_int(form, "females", "Females", default=0, lo=0, hi=1000)
            for tid in (father_id, mother_id):
                parent = s.get(TankRecord, tid)
                if not zf_can_edit(parent):
                    raise ZfInputError(zf_denied(parent))
            father, mother = s.get(TankRecord, father_id), s.get(TankRecord, mother_id)
            for source, sex, wanted, noun in ((father, "M", males, "male"), (mother, "F", females, "female")):
                have = zf_available(source, sex)
                if wanted > have:
                    if noun == "male":
                        message = ngettext("Tank %(tank)s has %(num)s live male (fish rows marked %(sex)s); you asked for %(wanted)s.",
                                           "Tank %(tank)s has %(num)s live males (fish rows marked %(sex)s); you asked for %(wanted)s.", have, tank=source.tank_id, sex=sex, wanted=wanted)
                    else:
                        message = ngettext("Tank %(tank)s has %(num)s live female (fish rows marked %(sex)s); you asked for %(wanted)s.",
                                           "Tank %(tank)s has %(num)s live females (fish rows marked %(sex)s); you asked for %(wanted)s.", have, tank=source.tank_id, sex=sex, wanted=wanted)
                    raise ZfInputError(message)
        except ZfInputError as error:
            flash(str(error), "error")
            return redirect(url_for("zebrafish", view="clutches"))
        tank = TankRecord(
            tank_id=zf_next_code(s, TankRecord.tank_id, "MT", 3),
            purpose="mating",
            mating_father_tank_id=father_id,
            mating_mother_tank_id=mother_id,
            mating_return_at=date.today() + timedelta(days=return_days),
            owner=access.username(),
            notes=(form.get("notes") or "").strip(),
        )
        if males or females:
            # One batch, so undo puts the fish back and removes the tank.
            with audit.batch(s, "create", f"set up mating tank {tank.tank_id}", "tanks"):
                s.add(tank)
                s.flush()
                zf_take_fish(s, father, "M", males, tank)
                zf_take_fish(s, mother, "F", females, tank)
        else:
            s.add(tank)
        s.commit()
        if males or females:
            flash(gettext("Mating tank %(tank)s set up with %(males)s ♂ and %(females)s ♀ · return by %(day)s.",
                          tank=tank.tank_id, males=males, females=females, day=fmt_day(tank.mating_return_at)),
                  "success")
        else:
            flash(gettext("Mating tank %(tank)s set up · return by %(day)s.", tank=tank.tank_id,
                          day=fmt_day(tank.mating_return_at)), "success")
    return redirect(url_for("zebrafish", view="tanks"))


def zf_available(tank, sex: str) -> int:
    """Live fish of one sex ("M"/"F") in a tank."""
    return sum(f.count or 0 for f in tank.fish if fish_alive(f) and (f.sex or "").strip().upper() == sex)


def zf_take_fish(s, source, sex: str, n: int, dest) -> None:
    """Move n live fish of one sex from a tank into another, splitting a
    group row when only part of it goes. Check zf_available first."""
    rows = sorted((f for f in source.fish if fish_alive(f) and (f.sex or "").strip().upper() == sex and (f.count or 0) > 0),
                  key=lambda f: -(f.count or 0))
    for f in rows:
        if n <= 0:
            break
        take = min(n, f.count)
        if take == f.count:
            f.tank_id_fk, f.tank = dest.id, dest  # the column too, for undo
        else:
            f.count -= take
            s.add(FishRecord(tank=dest, line_id_fk=f.line_id_fk, individual_id="", count=take, sex=f.sex,
                             status=f.status, date_of_fertilization=f.date_of_fertilization,
                             clutch_id_fk=f.clutch_id_fk, genotype=f.genotype, notes=""))
        n -= take


# ---- Mating tank returned --------------------------------------------------


def zf_is_mating(tank) -> bool:
    return (getattr(tank, "purpose", "") or "").strip().lower() == "mating"


def zf_mating_home(f, mating_tank):
    """The tank a mating-tank group goes back to, as the wizard recorded
    them: males to the male tank, females to the female tank. None for a
    mixed or unknown group, or a mating tank from before the wizard."""
    sex = (f.sex or "").strip().upper()
    return {"M": mating_tank.mating_father_tank_id, "F": mating_tank.mating_mother_tank_id}.get(sex)


def zf_return_confirm(tank, groups) -> str:
    """The Returned prompt. groups: (count, home tank code) per live group."""
    if not groups:
        return gettext("Mark mating tank %(tank)s returned? It holds no fish; it is retired and kept in the history.",
                       tank=tank.tank_id)
    homes = {}
    for n, code in groups:
        homes[code or "?"] = homes.get(code or "?", 0) + n
    where = ", ".join(gettext("%(n)s to %(tank)s", n=n, tank=code) for code, n in homes.items())
    return gettext("Return mating tank %(tank)s? Fish go back: %(where)s. %(tank)s is then retired.",
                   tank=tank.tank_id, where=where)


def zf_matching_row(s, home, f):
    """A live row in the home tank that the returning group is the same
    fish as (same line, sex, age, genotype, clutch), to fold it back into:
    the other half of a row the wizard split."""
    if (f.individual_id or "").strip():
        return None
    if s.scalar(select(FishSacLog.id).where(FishSacLog.fish_id_fk == f.id).limit(1)) is not None:
        return None
    for other in home.fish:
        if (other.id != f.id and fish_alive(other) and not (other.individual_id or "").strip()
                and other.line_id_fk == f.line_id_fk and (other.sex or "") == (f.sex or "")
                and other.date_of_fertilization == f.date_of_fertilization
                and (other.genotype or "") == (f.genotype or "") and other.clutch_id_fk == f.clutch_id_fk):
            return other
    return None


def zf_return_choices(form) -> dict:
    """home_<fish row id> = tank id, from the Returned dialog."""
    out = {}
    for key, value in form.items():
        if key.startswith("home_") and key[5:].isdigit() and str(value).strip().isdigit():
            out[int(key[5:])] = int(value)
    return out


def zf_return_mating(s, mt, choices: dict):
    """Put a mating tank's live fish back in their home tanks and retire
    it. Everything is checked before anything moves. (error, summary)."""
    if not zf_is_mating(mt):
        return gettext("Tank %(tank)s is not a mating tank.", tank=mt.tank_id), ""
    if not mt.active:
        return gettext("Mating tank %(tank)s was already returned.", tank=mt.tank_id), ""
    if not zf_can_edit(mt):
        return zf_denied(mt), ""
    moves = []
    for f in [f for f in mt.fish if fish_alive(f)]:
        home_id = choices.get(f.id) or zf_mating_home(f, mt)
        home = s.get(TankRecord, home_id) if home_id else None
        what = (gettext("the %(n)s %(sex)s fish", n=f.count or 0,
                        sex=i18n.translate_value(f.sex) if f.sex else gettext("unsexed"))
                + (f" ({f.line.name})" if f.line else ""))
        if home is None:
            return gettext("Choose which tank %(what)s in %(tank)s go back to: use Returned on the tank's row.",
                           what=what, tank=mt.tank_id), ""
        if home.id == mt.id:
            return gettext("Pick a home tank for %(what)s other than %(tank)s itself.", what=what,
                           tank=mt.tank_id), ""
        if not zf_can_edit(home):
            return gettext("Can’t move fish into %(tank)s: %(reason)s", tank=home.tank_id, reason=zf_denied(home)), ""
        moves.append((f, home))
    homes = {}
    for f, home in moves:
        n = f.count or 0
        target = zf_matching_row(s, home, f)
        if target is not None:
            target.count = (target.count or 0) + n
            s.delete(f)
        else:
            f.tank_id_fk, f.tank = home.id, home  # the column too, for undo
        homes[home.tank_id] = homes.get(home.tank_id, 0) + n
    summary = ", ".join(f"{n} fish to {code}" for code, n in homes.items())
    mt.active = False
    stamp = f"Returned {date.today().isoformat()}" + (f": {summary}" if summary else "")
    mt.notes = f"{mt.notes} · {stamp}" if (mt.notes or "").strip() else stamp
    # The note above is the record's (kept in English); what is shown now
    # is in the page's language.
    return None, ", ".join(gettext("%(n)s fish to %(tank)s", n=n, tank=code) for code, n in homes.items())


@app.route("/zebrafish/tanks/<int:tank_row_id>/return", methods=["POST"])
@login_required
def zebrafish_return_mating(tank_row_id: int):
    """Returned: the mating tank's fish go home and the tank is retired
    (kept, inactive, for the record). One batch, so it can be undone."""
    with SessionLocal() as s:
        mt = s.get(TankRecord, tank_row_id)
        if mt is None:
            return zf_reply("tanks", gettext("That tank no longer exists. Reload the page."), status=404)
        code = mt.tank_id
        with audit.batch(s, "update", f"return mating tank {code}", "tanks"):
            error, summary = zf_return_mating(s, mt, zf_return_choices(request.form))
        if error:
            s.rollback()
            return zf_reply("tanks", error)
        s.commit()
    flash(gettext("Returned %(tank)s: %(summary)s. %(tank)s is retired; undo it from Batch history if that was a mistake.", tank=code, summary=summary) if summary else
          gettext("Returned %(tank)s. %(tank)s is retired; undo it from Batch history if that was a mistake.",
                  tank=code), "success")
    return zf_reply("tanks")


# ---- Sac log ---------------------------------------------------------------


@app.route("/zebrafish/sac/create", methods=["POST"])
@login_required
def zebrafish_create_sac_log():
    """Log a sacrifice by hand. Picking the fish row (tank + line/sex
    group) takes the fish off its count, refused when more than are
    there; a row left with none turns sac on the sac date, the same rule
    as setting its status. Without a row it only logs, for fish that
    never had one. One batch, so undo puts the count back."""
    form = request.form
    back = url_for("zebrafish", view="tanks") + "#sac-log"
    with SessionLocal() as s:
        fish = None
        try:
            count = zf_int(form, "count", "Count", default=1, lo=1, hi=100000)
            when = zf_date(form, "sac_date", "Sac date") or date.today()
            if when > date.today():
                raise ZfInputError(gettext("The sac date can’t be in the future."))
            fish_id = zf_ref(s, FishRecord, form.get("fish_id_fk"), "fish row")
            if fish_id is not None:
                fish = s.get(FishRecord, fish_id)
                if not zf_can_edit(fish):
                    raise ZfInputError(zf_denied(fish))
                in_tank = fish.tank.tank_id if fish.tank else gettext("no tank")
                if not fish_alive(fish):
                    raise ZfInputError(gettext("The fish row #%(row)s in %(tank)s is already %(status)s.", row=fish.id,
                                               tank=in_tank, status=i18n.translate_value(fish.status or "sac")))
                if count > (fish.count or 0):
                    raise ZfInputError(gettext("The fish row #%(row)s in %(tank)s has %(n)s fish; you can’t sac %(count)s.", row=fish.id, tank=in_tank, n=fish.count or 0,
                                               count=count))
                tank_id = fish.tank_id_fk
                line_id = fish.line_id_fk or (fish.tank.line_id_fk if fish.tank else None)
            else:
                tank_id = zf_ref(s, TankRecord, form.get("tank_id_fk"), "tank")
                if tank_id is not None and not zf_can_edit(s.get(TankRecord, tank_id)):
                    raise ZfInputError(zf_denied(s.get(TankRecord, tank_id)))
                line_id = zf_ref(s, FishLine, form.get("line_id_fk"), "line")
            entry = FishSacLog(
                tank_id_fk=tank_id,
                line_id_fk=line_id,
                fish_id_fk=fish.id if fish is not None else None,
                count=count,
                reason=(form.get("reason") or "").strip()[:200],
                recorded_by=access.username(),
            )
            if when != date.today():
                entry.recorded_at = datetime.combine(when, datetime.utcnow().time())
        except ZfInputError as error:
            flash(str(error), "error")
            return redirect(back)
        if fish is None:
            s.add(entry)
            s.commit()
            return redirect(back)
        with audit.batch(s, "update", f"sac {count} from fish row #{fish.id}", "fish"):
            s.add(entry)
            previous = fish.status
            fish.count = (fish.count or 0) - count
            if fish.count == 0:
                # None left: the row itself is sac'd. With a count of 0
                # the status rule stamps the date and logs nothing more.
                fish.status = "sac"
                fish.sac_date = when
                zf_apply_fish_status(s, fish, previous)
        s.commit()
        left = fish.count
        code = fish.tank.tank_id if fish.tank else ""
        flash(gettext("Logged %(count)s sac’d from %(tank)s (row #%(row)s): %(left)s left.", count=count, tank=code,
                      row=fish.id, left=left) if left else
              gettext("Logged %(count)s sac’d from %(tank)s (row #%(row)s): none left, so the row is marked sac.",
                      count=count, tank=code, row=fish.id), "success")
    return redirect(back)


@app.route("/zebrafish/tanks/<int:tank_row_id>/card")
@login_required
def zebrafish_tank_card(tank_row_id: int):
    """Printable tank card — one tank, single page."""
    with SessionLocal() as s:
        t = s.get(TankRecord, tank_row_id)
        if t is None:
            return redirect(url_for("zebrafish", view="tanks"))
        return render_template("zebrafish_tank_card.html", tank=t, position=fish_position_label(t),
                               age_for_clutch=lambda dof: _fish_age_label(dof))
