"""The lab calendar's own features, beside the events and to-dos in app.py:

- Repeats: an event every N days, weeks or months, until a date, with single
  dates skipped. Expanded into occurrences for whatever range is on screen.
- Everything with a date: fly and worm schedules, organism schedules,
  zebrafish tanks and reagent expiry, from the same agenda as the Home
  layouts (app/home_layouts.py). Mouse colony dates keep coming from
  services.derive_auto_calendar_items.
- Protocol timelines: a template of steps counted in days from day 0,
  applied to an experiment or a named group from one start date. Moving the
  start moves every step.
- Equipment booking: time on a shared instrument; overlapping bookings of
  one instrument are refused, naming who has it.
- Away days: leave and conferences. Work due while its owner is away is
  listed, and whoever is chosen to cover it is told.
- A private link per person that Apple, Google or Outlook Calendar can
  subscribe to (an iCalendar feed), for the whole lab calendar or only
  their own things.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from calendar import monthrange
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, abort, g, jsonify, request, url_for
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from . import access, home_layouts, i18n, notify
from .db import SessionLocal
from .i18n import gettext, ngettext, pgettext
from .models import (Absence, CalendarEvent, CalendarFeed, CalendarRepeat, Equipment, EquipmentBooking,
                     Experiment, ProtocolRun, ProtocolTemplate, TaskItem, UserAccount)

bp = Blueprint("labcal", __name__, url_prefix="/calendar")

# nthweekday: every month on the same weekday of the month as the first
# date ("the first Monday", or "the last Friday" when it is the fifth).
FREQS = {"daily": "day", "weekly": "week", "monthly": "month", "nthweekday": "month"}
ORDINALS = ("first", "second", "third", "fourth", "last")
# The kinds of away time (how each reads on the calendar: absence_items).
ABSENCE_KINDS = {"leave": "away", "conference": "at a conference", "other": "away"}
# Colours of the calendar's layers; items may carry their own.
COLORS = {"stocks": "#af52de", "supplies": "#ff2d55", "protocols": "#5856d6",
          "bookings": "#30b0c7", "away": "#8e8e93"}
AGENDA_ICONS = {"stock": "vials", "org": "paw", "zebrafish": "fish", "supplies": "flask"}
# How far ahead "needs cover" looks, and how far the phone feed reaches.
COVER_DAYS = 60
FEED_BACK_DAYS, FEED_AHEAD_DAYS = 60, 365
# The agenda is built forward from today; this caps how far.
AGENDA_MAX_DAYS = 120
_EXPAND_LIMIT = 1000


@bp.before_request
def require_login():
    if request.endpoint == "labcal.feed_ics":
        return None
    if g.get("user") is None:
        return jsonify({"ok": False, "error": gettext("Sign in first.")}), 401


# ---------------------------------------------------------------- helpers

def _me() -> str:
    return access.username()


def _can_edit(owner: str) -> bool:
    return access.is_admin() or (owner or "") == _me()


# ---------------------------------------------------------------- whose events and to-dos

def event_visible_clause(user=None):
    """The events a person sees: their own, the lab's (shared), and their
    project groups' (app/groups.py). Someone's own unshared event is theirs
    alone, an admin's too."""
    from . import groups
    me = access.username(user)
    mine = sorted(groups.ids_of(user))
    clause = (CalendarEvent.owner == me) | (CalendarEvent.is_shared.is_(True) & CalendarEvent.share_group_id.is_(None))
    if mine:
        clause = clause | (CalendarEvent.is_shared.is_(True) & CalendarEvent.share_group_id.in_(mine))
    return clause


def event_can_edit(event, user=None) -> bool:
    """A shared event is changed by its owner or an admin; a personal one by
    its owner only."""
    if event is None:
        return False
    me = access.username(user)
    if not event.is_shared:
        return bool(me) and (event.owner or "") == me
    return access.is_admin(user) or (bool(me) and (event.owner or "") == me)


def task_is_personal(task) -> bool:
    return not task.is_shared and bool((task.owner or "").strip())


def _date(raw) -> date | None:
    try:
        return date.fromisoformat(str(raw or "").strip()[:10]) if raw else None
    except ValueError:
        return None


def _dt(raw) -> datetime | None:
    raw = str(raw or "").strip().replace("Z", "")
    try:
        return datetime.fromisoformat(raw[:19]) if raw else None
    except ValueError:
        return None


def _int(raw, default=0, lo=None, hi=None) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


_HEX_COLOR = re.compile(r"#(?:[0-9A-Fa-f]{3}){1,2}")


def _color(raw, default="") -> str:
    """"#30b0c7" or "#3bc", nothing else: it is written into a style."""
    raw = str(raw or "").strip()
    return raw if _HEX_COLOR.fullmatch(raw) else default


def _refuse(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def display_names(session) -> dict[str, str]:
    return {u.username: (u.display_name or u.username)
            for u in session.scalars(select(UserAccount))}


def people(session) -> list[dict]:
    """Everyone who can be picked as cover, for the calendar's menus."""
    rows = session.scalars(select(UserAccount).where(UserAccount.disabled.is_(False),
                                                     UserAccount.role != "pending")
                           .order_by(UserAccount.display_name, UserAccount.username))
    return [{"username": u.username, "name": u.display_name or u.username} for u in rows]


def _allday(item_id, calendar_id, kind, title, first: date, last: date, color, body="", raw=None):
    return {"id": item_id, "kind": kind, "calendarId": calendar_id, "title": title,
            "category": "allday", "isAllday": True,
            "start": datetime.combine(first, datetime.min.time()).isoformat(),
            "end": datetime.combine(last, datetime.max.time()).isoformat(),
            "backgroundColor": color, "borderColor": color, "body": body,
            "isReadOnly": True, "raw": raw or {}}


# ---------------------------------------------------------------- repeats

def weekday_of_month(day: date) -> int:
    """0–3 for the first to fourth such weekday of its month, 4 for a fifth
    (read as "the last")."""
    return (day.day - 1) // 7


def _nth(first: date, freq: str, k: int) -> date:
    if freq == "daily":
        return first + timedelta(days=k)
    if freq in ("monthly", "nthweekday"):
        m = first.month - 1 + k
        y, m = first.year + m // 12, m % 12 + 1
        if freq == "monthly":
            return date(y, m, min(first.day, monthrange(y, m)[1]))
        n, length = weekday_of_month(first), monthrange(y, m)[1]
        if n == 4:                                     # the last such weekday
            last = date(y, m, length)
            return last - timedelta(days=(last.weekday() - first.weekday()) % 7)
        day1 = date(y, m, 1)
        return day1 + timedelta(days=(first.weekday() - day1.weekday()) % 7 + 7 * n)
    return first + timedelta(weeks=k)


def occurrences(first: date, repeat: CalendarRepeat, start: date, end: date) -> list[date]:
    """The dates of a repeating event between start and end, inclusive."""
    step = max(1, repeat.interval or 1)
    skip = {s for s in (repeat.skip or "").split(",") if s}
    if repeat.freq in ("monthly", "nthweekday"):
        months = (start.year - first.year) * 12 + start.month - first.month
        n = max(0, months // step - 1)
    else:
        unit = 1 if repeat.freq == "daily" else 7
        n = max(0, (start - first).days // (unit * step))
    out = []
    for _ in range(_EXPAND_LIMIT):
        d = _nth(first, repeat.freq, step * n)
        if d > end or (repeat.until and d > repeat.until):
            break
        if d >= start and d.isoformat() not in skip:
            out.append(d)
        n += 1
    return out


def repeats_by_event(session, event_ids) -> dict[int, CalendarRepeat]:
    if not event_ids:
        return {}
    return {r.event_id_fk: r for r in session.scalars(
        select(CalendarRepeat).where(CalendarRepeat.event_id_fk.in_(list(event_ids))))}


def _ordinal(n: int) -> str:
    """"first" … "fourth", or "last" (ORDINALS), in the page's language."""
    return (pgettext("weekday of the month", "first"), pgettext("weekday of the month", "second"),
            pgettext("weekday of the month", "third"), pgettext("weekday of the month", "fourth"),
            pgettext("weekday of the month", "last"))[n]


def repeat_summary(repeat: CalendarRepeat | None, first: date | None = None) -> dict | None:
    """How an event repeats, in words: "Every 2 weeks", "Every month on the
    second Monday until 03 Oct 2026" (in the page's language)."""
    if repeat is None:
        return None
    n = repeat.interval if repeat.interval > 1 else 1
    unit = FREQS.get(repeat.freq, "week")
    if repeat.freq == "nthweekday" and first is not None:
        every = ngettext("Every month on the %(nth)s %(weekday)s", "Every %(num)s months on the %(nth)s %(weekday)s",
                         n, nth=_ordinal(weekday_of_month(first)), weekday=i18n.strftime(first, "%A"))
    elif unit == "day":
        every = ngettext("Every day", "Every %(num)s days", n)
    elif unit == "month":
        every = ngettext("Every month", "Every %(num)s months", n)
    else:
        every = ngettext("Every week", "Every %(num)s weeks", n)
    if repeat.until:
        every = gettext("%(every)s until %(date)s", every=every, date=i18n.strftime(repeat.until, "%d %b %Y"))
    return {"freq": repeat.freq, "interval": repeat.interval,
            "until": repeat.until.isoformat() if repeat.until else "", "text": every}


def save_repeat(session, event: CalendarEvent, data) -> None:
    """Set, change or remove how an event repeats, from the dialog's
    {"freq", "interval", "until"}; a blank freq removes it."""
    current = session.scalar(select(CalendarRepeat).where(CalendarRepeat.event_id_fk == event.id))
    freq = (data or {}).get("freq") if isinstance(data, dict) else None
    if freq not in FREQS:
        if current is not None:
            session.delete(current)
        return
    if current is None:
        current = CalendarRepeat(event_id_fk=event.id, skip="")
        session.add(current)
    current.freq = freq
    current.interval = _int(data.get("interval"), 1, 1, 99)
    until = _date(data.get("until"))
    current.until = until if until and until >= event.event_date else None


def delete_repeat(session, event_id: int) -> None:
    for row in session.scalars(select(CalendarRepeat).where(CalendarRepeat.event_id_fk == event_id)):
        session.delete(row)


@bp.route("/events/<int:event_id>/skip", methods=["POST"])
def skip_occurrence(event_id: int):
    """Take one date out of a repeating event ("Delete this one")."""
    day = _date((request.get_json(silent=True) or {}).get("date"))
    with SessionLocal() as s:
        repeat = s.scalar(select(CalendarRepeat).where(CalendarRepeat.event_id_fk == event_id))
        event = s.get(CalendarEvent, event_id)
        if repeat is None or event is None or day is None:
            return _refuse(gettext("That event does not repeat."), 404)
        if not event_can_edit(event):
            return _refuse(gettext("Only the person who added this event, or an admin, can change it."), 403)
        skip = [d for d in (repeat.skip or "").split(",") if d]
        if day.isoformat() not in skip:
            skip.append(day.isoformat())
        repeat.skip = ",".join(sorted(skip))
        s.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- everything with a date

def agenda_items(session, start: date, end: date, features: dict, zebrafish: dict | None,
                 owner: str | None = None) -> list[dict]:
    """Fly, worm, organism, zebrafish and supply dates from the Home agenda.
    It looks forward from today, with anything overdue at its own date."""
    today = date.today()
    if end < today - timedelta(days=AGENDA_MAX_DAYS):
        return []
    horizon = max(0, min((end - today).days, AGENDA_MAX_DAYS))
    agenda, tracks = home_layouts.build_agenda(session, today, horizon, features, zebrafish)
    out = []
    for n, it in enumerate(agenda):
        track = it["track"]
        if track == "calendar" or track.startswith("colony:"):
            continue
        if not start <= it["due"] <= end or (owner is not None and it["owner"] != owner):
            continue
        family = track.split(":")[0]
        layer = "supplies" if family == "supplies" else "stocks"
        body = " · ".join(b for b in (it["detail"], it["loc"]["text"] if it["loc"] else "", tracks[track][0]) if b)
        out.append(_allday(f"agenda-{n}-{it['due'].isoformat()}", layer, "agenda", it["title"], it["due"], it["due"],
                           COLORS[layer], body, {"href": it["url"], "owner": it["owner"],
                                                 "icon": AGENDA_ICONS.get(family, "calendar"),
                                                 "status": it["status"], "group": tracks[track][0]}))
    return out


# ---------------------------------------------------------------- protocols

def clean_steps(raw) -> list[dict]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except ValueError:
            raw = []
    steps = []
    for step in raw if isinstance(raw, list) else []:
        if not isinstance(step, dict):
            continue
        title = str(step.get("title", "")).strip()[:120]
        first = _int(step.get("from"), 0, -365, 3650)
        last = _int(step.get("to"), first, -365, 3650)
        if title:
            steps.append({"from": min(first, last), "to": max(first, last), "title": title})
    return sorted(steps, key=lambda s: (s["from"], s["to"]))


def _run_label(run: ProtocolRun) -> str:
    return run.label or run.name or gettext("Protocol")


def protocol_items(session, start: date, end: date, owner: str | None = None) -> list[dict]:
    query = select(ProtocolRun)
    if owner is not None:
        query = query.where(ProtocolRun.owner == owner)
    out = []
    for run in session.scalars(query):
        href = url_for("experiment_detail", experiment_id=run.experiment_id_fk) if run.experiment_id_fk else ""
        for n, step in enumerate(clean_steps(run.steps)):
            first = run.start_date + timedelta(days=step["from"])
            last = run.start_date + timedelta(days=step["to"])
            if last < start or first > end:
                continue
            days = (gettext("day %(n)s", n=step["from"]) if step["to"] == step["from"]
                    else gettext("day %(first)s–%(last)s", first=step["from"], last=step["to"]))
            out.append(_allday(f"protocol-{run.id}-{n}", "protocols", "protocol",
                               f"{step['title']} · {_run_label(run)}", first, last,
                               run.color or COLORS["protocols"], f"{run.name}, {days}",
                               {"runId": run.id, "owner": run.owner, "icon": "protocol", "href": href}))
    return out


def _template_dict(t: ProtocolTemplate) -> dict:
    steps = clean_steps(t.steps)
    return {"id": t.id, "name": t.name, "description": t.description, "color": t.color or COLORS["protocols"],
            "steps": steps, "days": (steps[-1]["to"] if steps else 0), "owner": t.owner,
            "editable": _can_edit(t.owner)}


def _run_dict(r: ProtocolRun, names: dict) -> dict:
    return {"id": r.id, "template_id": r.template_id_fk, "name": r.name, "label": r.label,
            "start_date": r.start_date.isoformat(), "experiment_id": r.experiment_id_fk,
            "color": r.color or COLORS["protocols"], "notes": r.notes, "steps": clean_steps(r.steps),
            "owner": r.owner, "owner_name": names.get(r.owner, r.owner), "editable": _can_edit(r.owner)}


@bp.route("/protocols")
def protocols():
    with SessionLocal() as s:
        names = display_names(s)
        templates = [_template_dict(t) for t in s.scalars(select(ProtocolTemplate).order_by(ProtocolTemplate.name))]
        runs = [_run_dict(r, names) for r in s.scalars(select(ProtocolRun).order_by(ProtocolRun.start_date.desc()))]
    return jsonify({"ok": True, "templates": templates, "runs": runs})


@bp.route("/protocols/templates", methods=["POST"])
def save_template():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()[:120]
    steps = clean_steps(data.get("steps"))
    if not name:
        return _refuse(gettext("Give the protocol a name."))
    if not steps:
        return _refuse(gettext("Add at least one step with a title."))
    with SessionLocal() as s:
        if data.get("id"):
            t = s.get(ProtocolTemplate, _int(data["id"]))
            if t is None:
                return _refuse(gettext("That protocol no longer exists."), 404)
            if not _can_edit(t.owner):
                return _refuse(gettext("Only the person who wrote this protocol can change it."), 403)
        else:
            t = ProtocolTemplate(owner=_me())
            s.add(t)
        t.name, t.steps = name, json.dumps(steps)
        t.description = str(data.get("description", "")).strip()[:2000]
        t.color = _color(data.get("color"), COLORS["protocols"])
        s.commit()
        return jsonify({"ok": True, "template": _template_dict(t)})


@bp.route("/protocols/templates/<int:template_id>/delete", methods=["POST"])
def delete_template(template_id: int):
    with SessionLocal() as s:
        t = s.get(ProtocolTemplate, template_id)
        if t is None:
            return _refuse(gettext("That protocol no longer exists."), 404)
        if not _can_edit(t.owner):
            return _refuse(gettext("Only the person who wrote this protocol can delete it."), 403)
        s.delete(t)  # runs keep their own copy of the steps
        s.commit()
    return jsonify({"ok": True})


@bp.route("/protocols/runs", methods=["POST"])
def save_run():
    data = request.get_json(silent=True) or {}
    start = _date(data.get("start_date"))
    if start is None:
        return _refuse(gettext("Choose a start date (day 0)."))
    with SessionLocal() as s:
        if data.get("id"):
            run = s.get(ProtocolRun, _int(data["id"]))
            if run is None:
                return _refuse(gettext("That protocol run no longer exists."), 404)
            if not _can_edit(run.owner):
                return _refuse(gettext("Only the person who started this protocol can change it."), 403)
        else:
            template = s.get(ProtocolTemplate, _int(data.get("template_id")))
            if template is None:
                return _refuse(gettext("Choose a protocol to start."))
            run = ProtocolRun(template_id_fk=template.id, name=template.name, steps=template.steps,
                              color=template.color, owner=_me())
            s.add(run)
        run.start_date = start
        run.label = str(data.get("label", "")).strip()[:160]
        run.notes = str(data.get("notes", "")).strip()[:2000]
        if "color" in data:
            run.color = _color(data.get("color"), run.color or COLORS["protocols"])
        experiment_id = _int(data.get("experiment_id"), 0)
        run.experiment_id_fk = experiment_id if experiment_id and s.get(Experiment, experiment_id) else None
        s.commit()
        return jsonify({"ok": True, "run": _run_dict(run, display_names(s))})


@bp.route("/protocols/runs/<int:run_id>/delete", methods=["POST"])
def delete_run(run_id: int):
    with SessionLocal() as s:
        run = s.get(ProtocolRun, run_id)
        if run is None:
            return _refuse(gettext("That protocol run no longer exists."), 404)
        if not _can_edit(run.owner):
            return _refuse(gettext("Only the person who started this protocol can remove it."), 403)
        s.delete(run)
        s.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- equipment

def _equipment_dict(e: Equipment) -> dict:
    return {"id": e.id, "name": e.name, "location": e.location, "color": _color(e.color, COLORS["bookings"]),
            "active": e.active, "editable": _can_edit(e.created_by) or not e.created_by}


def equipment_list(session) -> list[dict]:
    return [_equipment_dict(e) for e in session.scalars(
        select(Equipment).where(Equipment.active.is_(True)).order_by(Equipment.name))]


def booking_items(session, start: date, end: date, owner: str | None = None) -> list[dict]:
    lo = datetime.combine(start, datetime.min.time())
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time())
    query = (select(EquipmentBooking).options(selectinload(EquipmentBooking.equipment))
             .where(EquipmentBooking.start_at < hi, EquipmentBooking.end_at > lo))
    if owner is not None:
        query = query.where(EquipmentBooking.owner == owner)
    names = display_names(session)
    out = []
    for b in session.scalars(query):
        color = b.equipment.color or COLORS["bookings"]
        out.append({"id": f"booking-{b.id}", "kind": "booking", "calendarId": "bookings",
                    "title": f"{b.equipment.name} · {names.get(b.owner, b.owner)}",
                    "category": "time", "isAllday": False,
                    "start": b.start_at.isoformat(), "end": b.end_at.isoformat(),
                    "backgroundColor": color, "borderColor": color, "body": b.purpose,
                    "isReadOnly": not _can_edit(b.owner),
                    "raw": {"bookingId": b.id, "equipmentId": b.equipment_id_fk, "owner": b.owner,
                            "purpose": b.purpose, "icon": "calendar-clock",
                            "location": b.equipment.location}})
    return out


@bp.route("/equipment")
def equipment():
    with SessionLocal() as s:
        return jsonify({"ok": True, "equipment": equipment_list(s)})


@bp.route("/equipment", methods=["POST"])
def save_equipment():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()[:120]
    if not name:
        return _refuse(gettext("Give the instrument a name."))
    with SessionLocal() as s:
        same = s.scalar(select(Equipment).where(Equipment.name == name))
        if data.get("id"):
            e = s.get(Equipment, _int(data["id"]))
            if e is None:
                return _refuse(gettext("That instrument no longer exists."), 404)
            if e.created_by and not _can_edit(e.created_by):
                return _refuse(gettext("Only the person who added this instrument can change it."), 403)
        elif same is not None and not same.active:
            e = same  # adding a retired instrument again brings it back
        else:
            e = Equipment(created_by=_me())
            s.add(e)
        if same is not None and same is not e:
            return _refuse(gettext("There is already an instrument called %(name)s.", name=name))
        e.name, e.active = name, True
        e.location = str(data.get("location", "")).strip()[:120]
        e.color = _color(data.get("color"), e.color or COLORS["bookings"])
        s.commit()
        return jsonify({"ok": True, "equipment": _equipment_dict(e)})


@bp.route("/equipment/<int:equipment_id>/delete", methods=["POST"])
def retire_equipment(equipment_id: int):
    """Take an instrument off the list; its past bookings stay."""
    with SessionLocal() as s:
        e = s.get(Equipment, equipment_id)
        if e is None:
            return _refuse(gettext("That instrument no longer exists."), 404)
        if e.created_by and not _can_edit(e.created_by):
            return _refuse(gettext("Only the person who added this instrument can remove it."), 403)
        e.active = False
        s.commit()
    return jsonify({"ok": True})


BOOKING_REPEATS = ("daily", "weekdays", "weekly")
MAX_REPEATED_BOOKINGS = 60


def _repeated_slots(start: datetime, end: datetime, repeat) -> list[tuple[datetime, datetime]] | str:
    """The slots a new booking takes: itself, and with a repeat
    ({freq: daily | weekdays | weekly, until: a date}) the same time on each
    later day up to and including `until`. A message when it can't be done."""
    if not isinstance(repeat, dict) or repeat.get("freq") not in BOOKING_REPEATS:
        return [(start, end)]
    until = _date(repeat.get("until"))
    if until is None or until < start.date():
        return gettext("Choose the last day the booking repeats until.")
    step = timedelta(days=7 if repeat["freq"] == "weekly" else 1)
    if end - start > step:
        return gettext("A booking that long can't repeat that often: each one would run into the next.")
    slots, at = [], start
    while at.date() <= until:
        if repeat["freq"] != "weekdays" or at.weekday() < 5:
            slots.append((at, at + (end - start)))
        at += step
        if len(slots) > MAX_REPEATED_BOOKINGS:
            return gettext("That makes more than %(n)s bookings. Choose an earlier last day.", n=MAX_REPEATED_BOOKINGS)
    return slots or gettext("There is no weekday in those dates.")


def _clash_text(s, eq: Equipment, clash: EquipmentBooking, several: bool = False) -> str:
    who = display_names(s).get(clash.owner, clash.owner)
    when = i18n.strftime(clash.start_at, "%a %d %b %H:%M") + "–" + (
        f"{clash.end_at:%H:%M}" if clash.end_at.date() == clash.start_at.date()
        else i18n.strftime(clash.end_at, "%a %d %b %H:%M"))
    if several:
        return gettext("%(instrument)s is already booked by %(who)s, %(when)s. Nothing was booked.",
                       instrument=eq.name, who=who, when=when)
    return gettext("%(instrument)s is already booked by %(who)s, %(when)s.", instrument=eq.name, who=who, when=when)


@bp.route("/bookings", methods=["POST"])
def save_booking():
    """Make or change a booking. A new one can repeat (every day, weekday or
    week until a date): each repeat is a booking of its own, changed or
    cancelled on its own, and if any of them clashes none is made."""
    data = request.get_json(silent=True) or {}
    start, end = _dt(data.get("start")), _dt(data.get("end"))
    if start is None or end is None:
        return _refuse(gettext("Choose when the booking starts and ends."))
    if end <= start:
        return _refuse(gettext("The booking has to end after it starts."))
    if end - start > timedelta(days=14):
        return _refuse(gettext("A booking can be at most two weeks long."))
    slots = [(start, end)] if data.get("id") else _repeated_slots(start, end, data.get("repeat"))
    if isinstance(slots, str):
        return _refuse(slots)
    with SessionLocal() as s:
        if data.get("id"):
            booking = s.get(EquipmentBooking, _int(data["id"]))
            if booking is None:
                return _refuse(gettext("That booking no longer exists."), 404)
            if not _can_edit(booking.owner):
                return _refuse(gettext("Only the person who made this booking can change it."), 403)
        else:
            booking = EquipmentBooking(owner=_me())
        eq = s.get(Equipment, _int(data.get("equipment_id"), booking.equipment_id_fk or 0))
        if eq is None or not eq.active:
            return _refuse(gettext("Choose an instrument to book."))
        for slot_start, slot_end in slots:
            clash = s.scalar(select(EquipmentBooking).where(
                EquipmentBooking.equipment_id_fk == eq.id, EquipmentBooking.id != (booking.id or 0),
                EquipmentBooking.start_at < slot_end, EquipmentBooking.end_at > slot_start))
            if clash is not None:
                return _refuse(_clash_text(s, eq, clash, several=len(slots) > 1), 409)
        purpose = str(data.get("purpose", "")).strip()[:200]
        booking.equipment_id_fk, booking.start_at, booking.end_at = eq.id, start, end
        booking.purpose = purpose
        if booking.id is None:
            s.add(booking)
        for slot_start, slot_end in slots[1:]:
            s.add(EquipmentBooking(owner=booking.owner, equipment_id_fk=eq.id, start_at=slot_start,
                                   end_at=slot_end, purpose=purpose))
        s.commit()
    return jsonify({"ok": True, "count": len(slots)})


@bp.route("/bookings/<int:booking_id>/delete", methods=["POST"])
def delete_booking(booking_id: int):
    with SessionLocal() as s:
        booking = s.get(EquipmentBooking, booking_id)
        if booking is None:
            return _refuse(gettext("That booking no longer exists."), 404)
        if not _can_edit(booking.owner):
            return _refuse(gettext("Only the person who made this booking can cancel it."), 403)
        s.delete(booking)
        s.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- away days

def absence_items(session, start: date, end: date, owner: str | None = None) -> list[dict]:
    query = select(Absence).where(Absence.start_date <= end, Absence.end_date >= start)
    if owner is not None:
        query = query.where((Absence.owner == owner) | (Absence.cover == owner))
    names = display_names(session)
    out = []
    for a in session.scalars(query):
        who = names.get(a.owner, a.owner)
        cover = gettext("Covered by %(who)s", who=names.get(a.cover, a.cover)) if a.cover else gettext("No cover chosen")
        title = (gettext("%(who)s at a conference", who=who) if a.kind == "conference"
                 else gettext("%(who)s away", who=who))
        out.append(_allday(f"away-{a.id}", "away", "away", title,
                           a.start_date, a.end_date, COLORS["away"],
                           " · ".join(b for b in (a.note, cover) if b),
                           {"absenceId": a.id, "owner": a.owner, "kind": a.kind, "note": a.note,
                            "cover": a.cover, "icon": "user", "editable": _can_edit(a.owner)}))
    return out


def cover_report(session, features: dict, zebrafish: dict | None) -> list[dict]:
    """Upcoming absences, each with the work its owner has due while away."""
    today = date.today()
    absences = list(session.scalars(select(Absence).where(
        Absence.end_date >= today, Absence.start_date <= today + timedelta(days=COVER_DAYS))
        .order_by(Absence.start_date)))
    if not absences:
        return []
    agenda, _tracks = home_layouts.build_agenda(session, today, COVER_DAYS, features, zebrafish)
    names = display_names(session)
    out = []
    for a in absences:
        lo, hi = max(a.start_date, today), a.end_date
        jobs = [(i["due"], i["title"]) for i in agenda if i["owner"] == a.owner and lo <= i["due"] <= hi]
        # Their shared to-dos; personal ones only to themselves.
        jobs += [(t.due_date, t.title) for t in session.scalars(select(TaskItem).where(
            TaskItem.owner == a.owner, TaskItem.done_at.is_(None),
            TaskItem.due_date >= lo, TaskItem.due_date <= hi))
            if t.is_shared or a.owner == _me()]
        for b in session.scalars(select(EquipmentBooking).options(selectinload(EquipmentBooking.equipment)).where(
                EquipmentBooking.owner == a.owner,
                EquipmentBooking.start_at >= datetime.combine(lo, datetime.min.time()),
                EquipmentBooking.start_at < datetime.combine(hi + timedelta(days=1), datetime.min.time()))):
            jobs.append((b.start_at.date(), gettext("%(instrument)s booked", instrument=b.equipment.name)))
        for item in protocol_items(session, lo, hi, owner=a.owner):
            jobs.append((max(lo, date.fromisoformat(item["start"][:10])), item["title"]))
        jobs = sorted(set(jobs))
        out.append({"id": a.id, "owner": a.owner, "name": names.get(a.owner, a.owner),
                    "start": a.start_date.isoformat(), "end": a.end_date.isoformat(),
                    "kind": a.kind, "note": a.note, "cover": a.cover, "cover_name": names.get(a.cover, a.cover),
                    "count": len(jobs), "jobs": [{"date": d.isoformat(), "title": t} for d, t in jobs[:8]],
                    "editable": _can_edit(a.owner)})
    return out


def _tell_cover(session, absence: Absence) -> None:
    if not absence.cover:
        return
    who = display_names(session).get(absence.owner, absence.owner)
    # The dates as the person told reads them (notify.send translates the rest).
    with i18n.using(i18n.language_for(session, absence.cover)):
        span = i18n.strftime(absence.start_date, "%d %b") + (
            "–" + i18n.strftime(absence.end_date, "%d %b") if absence.end_date != absence.start_date else "")
    notify.send(session, absence.cover, "You're covering for %(who)s, %(span)s",
                "Their animals, stocks and bookings due while they are away are listed on the calendar.",
                category="lab", link=url_for("calendar"), actor=_me(), values={"who": who, "span": span},
                message_values={})


@bp.route("/away", methods=["POST"])
def save_absence():
    data = request.get_json(silent=True) or {}
    first, last = _date(data.get("start")), _date(data.get("end"))
    if first is None:
        return _refuse(gettext("Choose the first day away."))
    last = last or first
    if last < first:
        return _refuse(gettext("The last day away can't be before the first."))
    with SessionLocal() as s:
        if data.get("id"):
            a = s.get(Absence, _int(data["id"]))
            if a is None:
                return _refuse(gettext("That away time no longer exists."), 404)
            if not _can_edit(a.owner):
                return _refuse(gettext("Only the person who is away (or an admin) can change this."), 403)
        else:
            owner = str(data.get("owner", "") or _me())
            if owner != _me() and not access.is_admin():
                owner = _me()
            a = Absence(owner=owner)
            s.add(a)
        previous_cover = a.cover
        a.start_date, a.end_date = first, last
        a.kind = data.get("kind") if data.get("kind") in ABSENCE_KINDS else "leave"
        a.note = str(data.get("note", "")).strip()[:200]
        cover = str(data.get("cover", "")).strip()
        a.cover = cover if cover != a.owner and s.scalar(select(UserAccount).where(UserAccount.username == cover)) else ""
        s.flush()
        if a.cover and a.cover != previous_cover:
            _tell_cover(s, a)
        s.commit()
    return jsonify({"ok": True})


@bp.route("/away/<int:absence_id>/cover", methods=["POST"])
def set_cover(absence_id: int):
    cover = str((request.get_json(silent=True) or {}).get("cover", "")).strip()
    with SessionLocal() as s:
        a = s.get(Absence, absence_id)
        if a is None:
            return _refuse(gettext("That away time no longer exists."), 404)
        if not _can_edit(a.owner):
            return _refuse(gettext("Only the person who is away (or an admin) can choose cover."), 403)
        if cover and (cover == a.owner or s.scalar(select(UserAccount).where(UserAccount.username == cover)) is None):
            return _refuse(gettext("Choose someone else in the lab."))
        changed = cover != a.cover
        a.cover = cover
        if changed:
            _tell_cover(s, a)
        s.commit()
    return jsonify({"ok": True})


@bp.route("/away/<int:absence_id>/delete", methods=["POST"])
def delete_absence(absence_id: int):
    with SessionLocal() as s:
        a = s.get(Absence, absence_id)
        if a is None:
            return _refuse(gettext("That away time no longer exists."), 404)
        if not _can_edit(a.owner):
            return _refuse(gettext("Only the person who is away (or an admin) can remove this."), 403)
        s.delete(a)
        s.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- the phone feed

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _feed_url(token: str) -> str:
    return url_for("labcal.feed_ics", token=token, _external=True)


@bp.route("/phone-feed")
def feed_settings():
    with SessionLocal() as s:
        feed = s.scalar(select(CalendarFeed).where(CalendarFeed.owner == _me()))
        if feed is None:
            return jsonify({"ok": True, "url": "", "scope": "mine"})
        return jsonify({"ok": True, "url": _feed_url(feed.token), "scope": feed.scope,
                        "last_used": feed.last_used_at.isoformat() if feed.last_used_at else ""})


@bp.route("/phone-feed", methods=["POST"])
def change_feed():
    """create (or change the scope), reset (a new link; the old one stops
    working) or stop (no link)."""
    data = request.get_json(silent=True) or {}
    action = data.get("action", "create")
    scope = data.get("scope") if data.get("scope") in ("mine", "lab") else None
    with SessionLocal() as s:
        feed = s.scalar(select(CalendarFeed).where(CalendarFeed.owner == _me()))
        if action == "stop":
            if feed is not None:
                s.delete(feed)
                s.commit()
            return jsonify({"ok": True, "url": "", "scope": "mine"})
        if feed is None or action == "reset":
            token = secrets.token_urlsafe(32)
            if feed is None:
                feed = CalendarFeed(owner=_me(), scope=scope or "mine")
                s.add(feed)
            feed.token, feed.token_hash, feed.last_used_at = token, _hash(token), None
        if scope:
            feed.scope = scope
        s.commit()
        return jsonify({"ok": True, "url": _feed_url(feed.token), "scope": feed.scope})


def _ics_text(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Lines of at most 75 octets, continued with a leading space (RFC 5545)."""
    out, current = [], b""
    for ch in line:
        encoded = ch.encode()
        if len(current) + len(encoded) > (75 if not out else 74):
            out.append(current.decode())
            current = b""
        current += encoded
    out.append(current.decode())
    return "\r\n ".join(out)


def render_ics(items: list[dict], name: str, base_url: str) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//BioManager//Lab calendar//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_ics_text(name)}", "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
             "X-PUBLISHED-TTL:PT1H"]
    for item in items:
        start, end = _dt(item["start"]), _dt(item["end"])
        if start is None or end is None:
            continue
        title = item["title"]
        if item.get("kind") == "task":
            title = (gettext("Done: %(title)s", title=title) if (item.get("raw") or {}).get("done")
                     else gettext("To-do: %(title)s", title=title))
        lines += ["BEGIN:VEVENT", f"UID:{item['id']}@biomanager", f"DTSTAMP:{stamp}"]
        if item.get("isAllday"):
            lines += [f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
                      f"DTEND;VALUE=DATE:{end.date() + timedelta(days=1):%Y%m%d}"]
        else:
            lines += [f"DTSTART:{start:%Y%m%dT%H%M%S}", f"DTEND:{end:%Y%m%dT%H%M%S}"]
        lines.append(f"SUMMARY:{_ics_text(title)}")
        if item.get("body"):
            lines.append(f"DESCRIPTION:{_ics_text(item['body'])}")
        href = (item.get("raw") or {}).get("href")
        if href:
            lines.append(f"URL:{base_url.rstrip('/')}{href}" if href.startswith("/") else f"URL:{href}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


@bp.route("/feed/<token>.ics")
def feed_ics(token: str):
    """The subscribed calendar. The token in the link is the only key, as
    with Google's secret iCal address; resetting it stops the old link."""
    from .app import calendar_items
    with SessionLocal() as s:
        feed = s.scalar(select(CalendarFeed).where(CalendarFeed.token_hash == _hash(token)))
        user = s.scalar(select(UserAccount).where(UserAccount.username == feed.owner)) if feed else None
        if feed is None or user is None or user.disabled:
            abort(404)
        # The feed is read as its owner, so what they may see is what it
        # shows, in their language (a phone's calendar app asks for none).
        g.user = user
        lang = i18n.language_for(s, user.username)
        today = date.today()
        with i18n.using(lang):
            items = calendar_items(s, today - timedelta(days=FEED_BACK_DAYS), today + timedelta(days=FEED_AHEAD_DAYS),
                                   owner=user.username if feed.scope == "mine" else None, external=False)
            scope = "mine" if feed.scope == "mine" else "lab"
            name = f"BioManager · {user.display_name or user.username}" if scope == "mine" else gettext("BioManager · lab")
        feed.last_used_at = datetime.utcnow()
        s.commit()
    with i18n.using(lang):
        body = render_ics(items, name, request.host_url)
    # The link is the key: keep it out of search engines, shared caches and
    # the Referer header of anything the calendar app opens from it.
    return Response(body, mimetype="text/calendar", headers={
        "Cache-Control": "private, max-age=900",
        "X-Robots-Tag": "noindex, nofollow",
        "Referrer-Policy": "no-referrer",
    })
