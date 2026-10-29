"""The calendar's Board: the to-dos as cards in lists, as on a kanban board.

Inbox holds every open to-do not in a list; then each list the person has
made, in their order; then Done, the to-dos ticked off in the last two
weeks. Dragging a card between lists moves it (into Done ticks it off, out
of Done opens it again), and + Add a card makes one in that list with no
day. The same to-dos are on the calendar, by their day; one with no day
shows there on today, unless it is kept in a list here.

Which list a to-do is in, and its place, is the to-do's own (tasks.board_list
and board_position), so a lab to-do sits in the same list for everyone who
sees it. The lists themselves, their names and order, are each person's
(app_settings "board:<username>"); a list someone else put a shared to-do
in is shown after one's own.
"""
from __future__ import annotations

import json
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
    from .app import task_can_edit
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
    from .app import task_visible_clause
    recent = datetime.utcnow() - timedelta(days=DONE_DAYS)
    return session.scalars(
        select(TaskItem).where(task_visible_clause())
        .where(or_(TaskItem.done_at.is_(None), TaskItem.done_at >= recent))
        .order_by(TaskItem.board_position, TaskItem.id)).all()


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
                    "cards": [_card(t, today) for t in open_tasks if not t.board_list]}]
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
    from .app import task_can_edit
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
    from .app import task_visible_clause
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
    list_name = (clean_names([data.get("list")]) or [""])[0]
    with SessionLocal() as s:
        top = s.scalar(select(func.max(TaskItem.board_position)).where(TaskItem.board_list == list_name)) or 0
        row = TaskItem(title=title, owner=me, status="todo", priority="medium", board_list=list_name,
                       board_position=top + 1)
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
    from .app import task_can_edit, task_denied
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
