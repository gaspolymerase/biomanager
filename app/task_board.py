"""The calendar's Board: the to-dos as cards in lists, as on a kanban board.

Inbox holds every open to-do not in a list; then each list the person has
made, in their order; then Done, the to-dos ticked off in the last two
weeks. Dragging a card between lists moves it (into Done ticks it off, out
of Done opens it again), and + Add a card makes one in that list with no
day. The same to-dos are on the calendar, by their day; one with no day
shows there on today, unless it is kept in a list here.

A day can be written in a new card, as one would say it: "Order primers
Fri", "Call the vendor tomorrow", "Book the scope by Nov 8", "明天订抗体",
"周五交报告" (when_in). Inbox also shows, under the cards, what the
calendar has to do in the coming week and what is overdue: the colony's
weanings and genotyping, flips, supplies running low (home_layouts.build_agenda).
Those are done where they are kept, so a click opens them there.

Which list a to-do is in, and its place, is the to-do's own (tasks.board_list
and board_position), so a lab to-do sits in the same list for everyone who
sees it. The lists themselves, their names and order, are each person's
(app_settings "board:<username>"); a list someone else put a shared to-do
in is shown after one's own.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, g, jsonify, request
from sqlalchemy import func, or_, select

from . import groups as project_groups, inventory_service
from .db import SessionLocal
from .i18n import gettext
from .models import TaskItem

bp = Blueprint("board", __name__, url_prefix="/calendar/board")

DONE = "__done__"
MAX_LISTS = 30
MAX_NAME = 80
DONE_DAYS = 14


def _me() -> str:
    if g.get("user") is None:
        abort(401)
    return g.user.username


def _key(username: str) -> str:
    return f"board:{username}"


def lists_of(session, username: str) -> list[str]:
    try:
        names = json.loads(inventory_service.get_setting(session, _key(username), "[]") or "[]")
    except ValueError:
        names = []
    return clean_names(names)


def clean_names(names) -> list[str]:
    out: list[str] = []
    for name in names if isinstance(names, list) else []:
        name = " ".join(str(name or "").split())[:MAX_NAME]
        if name and name != DONE and name not in out:
            out.append(name)
    return out[:MAX_LISTS]


def _save_lists(session, username: str, names: list[str]) -> list[str]:
    names = clean_names(names)
    inventory_service.set_setting(session, _key(username), json.dumps(names, ensure_ascii=False))
    return names


def _is_done(t: TaskItem) -> bool:
    return bool(t.done_at) or (t.status or "").lower() in ("done", "completed", "closed")


def _card(t: TaskItem, today: date) -> dict:
    from .calendar_routes import task_can_edit
    done = _is_done(t)
    return {
        "id": t.id, "key": f"task-{t.id}", "title": t.title or "", "notes": t.notes or "",
        "due": t.due_date.isoformat() if t.due_date else "",
        "overdue": bool(t.due_date and t.due_date < today and not done),
        "done": done, "color": t.color or "", "priority": t.priority or "medium",
        "audience": project_groups.label(t.is_shared, t.share_group_id, personal="", lab="Lab"),
        "owner": t.owner or "", "readOnly": not task_can_edit(t), "list": t.board_list or "",
    }


def _visible(session):
    from .calendar_routes import task_visible_clause
    recent = datetime.utcnow() - timedelta(days=DONE_DAYS)
    return session.scalars(
        select(TaskItem).where(task_visible_clause())
        .where(or_(TaskItem.done_at.is_(None), TaskItem.done_at >= recent))
        .order_by(TaskItem.board_position, TaskItem.id)).all()


# ------------------------------------------------------------ a day written in a card

_WEEKDAYS = {"mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "weds": 2, "wednesday": 2,
             "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5,
             "sun": 6, "sunday": 6}
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
           "oct": 10, "nov": 11, "dec": 12}
_ZH_DAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_EN_DAY = (r"(?P<rel>today|tod|tonight|tomorrow|tmrw?|next week|in (?P<n>\d{1,3}) (?P<unit>days?|weeks?))"
           r"|(?P<next>next )?(?P<wd>mon(?:day)?|tue(?:s(?:day)?)?|wed(?:s|nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)"
           r"|(?P<iso>\d{4}-\d{2}-\d{2})"
           r"|(?P<m>\d{1,2})/(?P<d>\d{1,2})"
           r"|(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\.? (?P<md>\d{1,2})(?:st|nd|rd|th)?"
           r"|(?P<dm>\d{1,2})(?:st|nd|rd|th)? (?P<mon2>jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\.?")
_EN_END = re.compile(rf"(?:^|\s+)(?:(?:on|by|due|for|before)\s+)?(?:{_EN_DAY})\s*$", re.I)
_ZH_DAY = (r"(?P<zrel>今天|今日|明天|明日|后天|大后天|下周)"
           r"|(?P<znext>下个?)?(?:周|星期|礼拜)(?P<zwd>[一二三四五六日天])"
           r"|(?P<zm>\d{1,2})月(?P<zd>\d{1,2})[日号]?")
_ZH_START = re.compile(rf"^\s*(?:{_ZH_DAY})\s*(?:前|之前)?\s*")
_ZH_END = re.compile(rf"\s*(?:在|于)?(?:{_ZH_DAY})\s*(?:前|之前)?\s*$")


def _coming(month: int, day: int, today: date) -> date | None:
    """That day this year, or next year once this year's is well past."""
    try:
        d = date(today.year, month, day)
    except ValueError:
        return None
    if d < today - timedelta(days=30):
        d = date(today.year + 1, month, day)
    return d


def _weekday(wd: int, today: date, nxt: bool) -> date:
    ahead = (wd - today.weekday()) % 7 or 7
    d = today + timedelta(days=ahead)
    # "next Friday" said on a Monday is the one after this week's.
    if nxt and d.isocalendar()[1] == today.isocalendar()[1]:
        d += timedelta(days=7)
    return d


def _day_of(m: re.Match, today: date) -> date | None:
    g_ = {k: v for k, v in m.groupdict().items() if v}
    rel = (g_.get("rel") or "").lower()
    if rel in ("today", "tod", "tonight"):
        return today
    if rel.startswith("tom") or rel.startswith("tmr"):
        return today + timedelta(days=1)
    if rel == "next week":
        return today + timedelta(days=7 - today.weekday())
    if g_.get("n"):
        n = int(g_["n"])
        return today + timedelta(days=n * (7 if g_["unit"].startswith("week") else 1))
    if g_.get("wd"):
        return _weekday(_WEEKDAYS[g_["wd"].lower()[:3] if g_["wd"].lower()[:3] in _WEEKDAYS else g_["wd"].lower()],
                        today, bool(g_.get("next")))
    if g_.get("iso"):
        try:
            return date.fromisoformat(g_["iso"])
        except ValueError:
            return None
    if g_.get("m"):
        a, b = int(g_["m"]), int(g_["d"])
        return _coming(a, b, today) if a <= 12 else _coming(b, a, today)
    if g_.get("mon"):
        return _coming(_MONTHS[g_["mon"].lower()[:3]], int(g_["md"]), today)
    if g_.get("mon2"):
        return _coming(_MONTHS[g_["mon2"].lower()[:3]], int(g_["dm"]), today)
    zrel = g_.get("zrel")
    if zrel:
        return today + timedelta(days={"今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2, "大后天": 3}.get(zrel, 7 - today.weekday()))
    if g_.get("zwd"):
        return _weekday(_ZH_DAYS[g_["zwd"]], today, bool(g_.get("znext")))
    if g_.get("zm"):
        return _coming(int(g_["zm"]), int(g_["zd"]), today)
    return None


def when_in(text: str, today: date) -> tuple[str, date | None]:
    """A day written at the end of a card's title, or (in Chinese) at its
    start or end: the title without it, and the day. Nothing is taken when
    it would leave no title."""
    text = " ".join(str(text or "").split())
    for pattern in (_EN_END, _ZH_END, _ZH_START):
        m = pattern.search(text)
        if not m:
            continue
        rest = (text[:m.start()] + " " + text[m.end():]).strip(" ,;:-–—，。")
        day = _day_of(m, today)
        if day and rest:
            return rest, day
    return text, None


@bp.get("/when")
def when():
    """What a card being written would be: its title and its day (for the
    hint under the box)."""
    _me()
    title, day = when_in(request.args.get("text", ""), date.today())
    return jsonify({"ok": True, "title": title, "due": day.isoformat() if day else ""})


# ------------------------------------------------------------ what the calendar has to do

def _from_calendar(session, today: date) -> list[dict]:
    """The calendar's work in the coming week and anything overdue, not the
    to-dos (they are cards) or events: the colony's, the stocks', supplies'."""
    from . import home_layouts, lab
    from .web import builtin_labels
    from .zebrafish_routes import zebrafish_home_summary
    features = lab.request_features()
    try:
        zebra = zebrafish_home_summary() if features.get("zebrafish", True) else None
    except Exception:  # noqa: BLE001  (the board shows without them)
        zebra = None
    items, tracks = home_layouts.build_agenda(session, today, 7, features, zebra,
                                              colony_label=builtin_labels().get("colony", "Mouse colony"))
    out = []
    for n, item in enumerate(items):
        if item["track"] == "calendar":
            continue
        group = tracks.get(item["track"], ("", "", ""))[0]
        out.append({"key": f"cal-{n}", "title": item["title"], "due": item["due"].isoformat(),
                    "overdue": item["due"] < today, "from": group, "detail": item.get("detail") or "",
                    "url": item.get("url") or "", "track": item["track"].split(":")[0]})
    return out[:40]


@bp.get("")
def board():
    me = _me()
    today = date.today()
    with SessionLocal() as s:
        mine = lists_of(s, me)
        tasks = _visible(s)
        open_tasks = [t for t in tasks if not _is_done(t)]
        others = [name for name in dict.fromkeys(t.board_list for t in open_tasks if t.board_list) if name not in mine]
        columns = [{"name": "", "kind": "inbox", "label": gettext("Inbox"),
                    "cards": [_card(t, today) for t in open_tasks if not t.board_list],
                    "calendar": _from_calendar(s, today)}]
        for name in [*mine, *others]:
            columns.append({"name": name, "kind": "list", "label": name, "theirs": name not in mine,
                            "cards": [_card(t, today) for t in open_tasks if t.board_list == name]})
        done = sorted((t for t in tasks if _is_done(t)), key=lambda t: t.done_at or datetime.min, reverse=True)
        columns.append({"name": DONE, "kind": "done", "label": gettext("Done"),
                        "cards": [_card(t, today) for t in done]})
    return jsonify({"ok": True, "columns": columns, "lists": mine})


@bp.post("/lists")
def lists():
    """{"add": name}, {"rename": [old, new]}, {"remove": name} or {"order": [names]}."""
    from .calendar_routes import task_can_edit
    me = _me()
    data = request.get_json(silent=True) or {}
    with SessionLocal() as s:
        names = lists_of(s, me)
        if "add" in data:
            name = clean_names([data.get("add")])
            if not name:
                return jsonify({"ok": False, "error": gettext("A list needs a name.")}), 400
            if len(names) >= MAX_LISTS:
                return jsonify({"ok": False, "error": gettext("That is as many lists as a board holds.")}), 400
            names = names + [n for n in name if n not in names]
        elif "rename" in data:
            old, new = (list(data.get("rename") or []) + ["", ""])[:2]
            new = (clean_names([new]) or [""])[0]
            if old not in names or not new:
                return jsonify({"ok": False, "error": gettext("A list needs a name.")}), 400
            if new in names and new != old:
                return jsonify({"ok": False, "error": gettext("There is a list of that name already.")}), 400
            names = [new if n == old else n for n in names]
            for t in s.scalars(select(TaskItem).where(TaskItem.board_list == old)):
                if task_can_edit(t):
                    t.board_list = new
        elif "remove" in data:
            name = str(data.get("remove") or "")
            names = [n for n in names if n != name]
            # Its cards go back to Inbox, as far as this person may move them.
            for t in s.scalars(select(TaskItem).where(TaskItem.board_list == name)):
                if task_can_edit(t):
                    t.board_list = ""
        elif "order" in data:
            wanted = clean_names(data.get("order"))
            names = [n for n in wanted if n in names] + [n for n in names if n not in wanted]
        names = _save_lists(s, me, names)
        s.commit()
    return jsonify({"ok": True, "lists": names})


def _place(session, card: TaskItem, list_name: str, before: int | None, after: int | None) -> None:
    """Put the card in a list, before or after another card, else at its end,
    and number the list's cards afresh."""
    from .calendar_routes import task_visible_clause
    rows = [t for t in session.scalars(
        select(TaskItem).where(task_visible_clause(), TaskItem.board_list == list_name, TaskItem.done_at.is_(None))
        .order_by(TaskItem.board_position, TaskItem.id)) if t.id != card.id]
    at = len(rows)
    for i, t in enumerate(rows):
        if t.id == before:
            at = i
        elif t.id == after:
            at = i + 1
    rows.insert(at, card)
    card.board_list = list_name
    for n, t in enumerate(rows):
        t.board_position = n


@bp.post("/cards")
def card_new():
    me = _me()
    data = request.get_json(silent=True) or {}
    title = " ".join(str(data.get("title") or "").split())[:200]
    if not title:
        return jsonify({"ok": False, "error": gettext("A card needs a title.")}), 400
    # A day picked beside the box, else one written in it ("… Fri", "明天…").
    due = None
    if data.get("due"):
        try:
            due = date.fromisoformat(str(data["due"]))
        except ValueError:
            return jsonify({"ok": False, "error": gettext("That is not a date.")}), 400
    elif data.get("read_day", True):
        title, due = when_in(title, date.today())
    list_name = (clean_names([data.get("list")]) or [""])[0]
    with SessionLocal() as s:
        top = s.scalar(select(func.max(TaskItem.board_position)).where(TaskItem.board_list == list_name)) or 0
        row = TaskItem(title=title, owner=me, status="todo", priority="medium", board_list=list_name,
                       board_position=top + 1, due_date=due)
        refused = project_groups.apply(row, data.get("audience", "0"))
        if refused:
            return jsonify({"ok": False, "error": refused}), 403
        s.add(row)
        s.commit()
        return jsonify({"ok": True, "card": _card(row, date.today())})


@bp.post("/cards/<int:task_id>")
def card_update(task_id: int):
    """Move a card ({"list", "before"|"after"}; the list "__done__" ticks it
    off), or change its title, notes or day ({"due": "" clears it})."""
    from .calendar_routes import task_can_edit, task_denied
    _me()
    data = request.get_json(silent=True) or {}
    with SessionLocal() as s:
        row = s.get(TaskItem, task_id)
        if row is None:
            abort(404)
        if not task_can_edit(row):
            return jsonify({"ok": False, "error": task_denied(row)}), 403
        if "title" in data:
            title = " ".join(str(data.get("title") or "").split())[:200]
            if title:
                row.title = title
        if "notes" in data:
            row.notes = str(data.get("notes") or "")
        if "due" in data:
            raw = str(data.get("due") or "")
            try:
                row.due_date = date.fromisoformat(raw) if raw else None
            except ValueError:
                return jsonify({"ok": False, "error": gettext("That is not a date.")}), 400
            row.start_at = row.end_at = None
        if "list" in data:
            target = str(data.get("list") or "")
            if target == DONE:
                if not _is_done(row):
                    row.done_at, row.status = datetime.utcnow(), "done"
            else:
                if _is_done(row):
                    row.done_at, row.status = None, "todo"
                target = (clean_names([target]) or [""])[0]
                _place(s, row, target, _int(data.get("before")), _int(data.get("after")))
        s.commit()
        return jsonify({"ok": True, "card": _card(row, date.today())})


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
