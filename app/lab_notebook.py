"""The lab notebook's own features, beside the tabs, pages and templates in
app.py:

- Kinds of page: notes, experiments (planned, running, done, with a start
  and finish stamp), protocols (numbered versions), meeting and seminar
  notes, and a daily log page made for each day.
- Sharing: a page is its owner's; they can let a lab mate (or the whole
  lab) view or edit it. Everyone who may edit can type in it at the same
  time: the editors exchange Yjs updates through this server (kept in
  notebook_sync_updates, fetched by polling), so no extra server is needed.
- Version history: edits by one person within a few minutes fold into one
  version; any version can be viewed, compared and restored.
- Comments on a page or a passage of it, with @people who get notified.
- Tags and search across every page someone may open.
- Protocols: an experiment started from one copies its steps as a
  checklist and records which version it followed.
- Meetings: a rotation of who presents next, notes made for the next
  meeting and shared with everyone in it, and action items (tasks naming
  @someone) sent to the calendar as their to-dos.
- A lab library of buffer recipes, with a set of common ones built in.
- A protocol library: the lab's protocol pages and common protocols built
  in (app/notebook_protocols.py), to insert into a page or copy and edit.

A page's rows in these tables go when the page does (delete_page_rows).
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, Response, abort, g, jsonify, redirect, request, url_for
from sqlalchemy import delete, func, or_, select

from .formutil import like_pattern
from . import access, groups, i18n, notebook_protocols, notify
from .db import SessionLocal
from .i18n import gettext
from .models import (CalendarEvent, NotebookComment, NotebookMeetingSeries, NotebookPage, NotebookPageInfo,
                     NotebookPresence, NotebookRecipe, NotebookShare, NotebookSyncUpdate, NotebookTab,
                     NotebookTemplate, NotebookVersion, TaskItem, UserAccount)

bp = Blueprint("nb", __name__, url_prefix="/notebook")

KINDS = {
    "note": "Note",
    "experiment": "Experiment",
    "protocol": "Protocol",
    "meeting": "Meeting",
    "seminar": "Seminar",
    "daily": "Daily log",
}
STATUSES = {"": "", "planned": "Planned", "running": "Running", "done": "Done", "failed": "Failed"}
KIND_ICONS = {"note": "file", "experiment": "flask", "protocol": "protocol", "meeting": "users",
              "seminar": "note", "daily": "calendar-clock"}
ROLES = ("view", "edit")
EVERYONE = "*"

# Edits by the same person this close together fold into one version, up to
# an hour of editing.
VERSION_FOLD = timedelta(minutes=10)
VERSION_SPAN = timedelta(hours=1)
# Someone whose editor has not been heard from for this long has left.
PRESENCE_TTL = timedelta(seconds=45)
SYNC_BATCH = 400
MAX_UPDATE_BYTES = 4 * 1024 * 1024
MAX_TAGS = 20

DAILY_TAB = "Daily log"
EXPERIMENTS_TAB = "Experiments"
MEETINGS_TAB = "Meetings"
PROTOCOLS_TAB = "Protocols"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9][A-Za-z0-9_.-]{0,79})")
TASK_RE = re.compile(r"^\s*[-*+]\s+\[ \]\s+(.+?)\s*$")
DUE_RE = re.compile(r"\b(?:due|by)\s*:?\s*(\d{4}-\d{2}-\d{2})\b", re.I)


@bp.before_request
def require_login():
    if g.get("user") is None:
        if request.path.startswith("/notebook/api/"):
            return jsonify({"ok": False, "error": gettext("Sign in first.")}), 401
        return redirect(url_for("login", next=request.path))
    return None


# ---------------------------------------------------------------- helpers

def _me() -> str:
    return access.username()


def _now() -> datetime:
    return datetime.utcnow()


def _iso(value) -> str:
    return value.isoformat() if value else ""


def _json_body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _fail(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def _int(raw) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _date(raw) -> date | None:
    try:
        return date.fromisoformat(str(raw or "").strip()[:10]) if raw else None
    except ValueError:
        return None


def display_names(session, usernames) -> dict[str, str]:
    names = {u for u in usernames if u and u != EVERYONE}
    if not names:
        return {}
    rows = session.execute(select(UserAccount.username, UserAccount.display_name)
                           .where(UserAccount.username.in_(names))).all()
    found = {u: (d or u) for u, d in rows}
    return {u: found.get(u, u) for u in names}


def people(session) -> list[dict]:
    """Everyone in the lab who can be shared with or mentioned."""
    rows = session.scalars(select(UserAccount).where(UserAccount.disabled.is_(False),
                                                     UserAccount.role != "pending")
                           .order_by(UserAccount.display_name, UserAccount.username)).all()
    return [{"username": u.username, "name": u.display_name or u.username,
             "guest": u.expires_at is not None} for u in rows]


def _is_guest(user) -> bool:
    return getattr(user, "expires_at", None) is not None


def owner_of(page: NotebookPage) -> str:
    return page.tab.owner_username if page.tab else ""


def role_for(session, page: NotebookPage, user=None) -> str | None:
    """owner, edit, view or None. "*" shares (the whole lab) leave guests out."""
    user = user or access.current_user()
    me = access.username(user)
    if not me or page is None:
        return None
    if owner_of(page) == me:
        return "owner"
    names = ([me] if _is_guest(user) else [me, EVERYONE]) + groups.page_share_names(user)
    roles = set(session.scalars(select(NotebookShare.role).where(NotebookShare.page_id_fk == page.id,
                                                                 NotebookShare.username.in_(names))).all())
    if "edit" in roles:
        return "edit"
    return "view" if roles else None


def can_edit_role(role: str | None) -> bool:
    return role in ("owner", "edit")


def shared_page_ids(session, user=None):
    """A select of the ids of pages shared with this person (not their own)."""
    user = user or access.current_user()
    me = access.username(user)
    names = ([me] if _is_guest(user) else [me, EVERYONE]) + groups.page_share_names(user)
    return select(NotebookShare.page_id_fk).where(NotebookShare.username.in_(names))


def accessible_filter(stmt, user=None):
    """Narrow a select that already joins NotebookTab to pages this person
    may open: their own and those shared with them."""
    me = access.username(user)
    return stmt.where(or_(NotebookTab.owner_username == me,
                          NotebookPage.id.in_(shared_page_ids(None, user))))


def load_page(session, page_id: int, need: str = "view"):
    """(page, role), or aborts: 404 when it cannot be seen, 403 when it can
    be seen but not changed."""
    page = session.get(NotebookPage, page_id)
    role = role_for(session, page) if page is not None else None
    if role is None:
        abort(404)
    if need == "edit" and not can_edit_role(role):
        abort(403)
    if need == "edit":
        from .signatures import is_locked
        if is_locked(session, page.id):
            abort(423)          # signed: its owner amends it to change it (app/signatures.py)
    if need == "owner" and role != "owner":
        abort(403)
    return page, role


def info_for(session, page_id: int, create: bool = False) -> NotebookPageInfo | None:
    info = session.scalar(select(NotebookPageInfo).where(NotebookPageInfo.page_id_fk == page_id))
    if info is None and create:
        info = NotebookPageInfo(page_id_fk=page_id, kind="note", status="", tags="", presenter="",
                                edited_by="", collab_generation=0)
        session.add(info)
        session.flush()
    return info


def normalize_tags(raw) -> str:
    if isinstance(raw, str):
        raw = raw.split(",")
    tags: list[str] = []
    for item in raw or []:
        tag = re.sub(r"\s+", " ", str(item).replace(",", " ").strip().lstrip("#").lower())[:40]
        if tag and tag not in tags:
            tags.append(tag)
    return ("," + ",".join(tags[:MAX_TAGS]) + ",") if tags else ""


def tag_list(stored: str) -> list[str]:
    return [t for t in (stored or "").split(",") if t]


def delete_page_rows(session, page_ids) -> None:
    """Everything here that belongs to these pages, before the pages go."""
    ids = list(page_ids)
    if not ids:
        return
    for model in (NotebookPageInfo, NotebookShare, NotebookVersion, NotebookSyncUpdate, NotebookPresence,
                  NotebookComment):
        session.execute(delete(model).where(model.page_id_fk.in_(ids)))


def _next_position(session, tab_id: int) -> int:
    return (session.scalar(select(func.max(NotebookPage.position)).where(NotebookPage.tab_id_fk == tab_id)) or 0) + 1


def tab_named(session, owner: str, title: str) -> NotebookTab:
    tab = session.scalar(select(NotebookTab).where(NotebookTab.owner_username == owner, NotebookTab.title == title)
                         .order_by(NotebookTab.id).limit(1))
    if tab is None:
        top = session.scalar(select(func.max(NotebookTab.position)).where(NotebookTab.owner_username == owner)) or 0
        tab = NotebookTab(owner_username=owner, title=title, position=top + 1)
        session.add(tab)
        session.flush()
    return tab


def first_tab(session, owner: str) -> NotebookTab:
    tab = session.scalar(select(NotebookTab).where(NotebookTab.owner_username == owner)
                         .order_by(NotebookTab.position, NotebookTab.id).limit(1))
    return tab or tab_named(session, owner, "Inbox")


def new_page(session, tab: NotebookTab, title: str, body: str = "", kind: str = "note", **info_fields) -> NotebookPage:
    page = NotebookPage(tab_id_fk=tab.id, title=(title or "Untitled page")[:200], body=body or "",
                        position=_next_position(session, tab.id), entry_date=date.today(),
                        properties="", created_at=_now(), updated_at=_now())
    session.add(page)
    session.flush()
    info = info_for(session, page.id, create=True)
    info.kind = kind if kind in KINDS else "note"
    info.edited_by = _me()
    for key, value in info_fields.items():
        setattr(info, key, value)
    return page


def page_url(page_id: int) -> str:
    return url_for("notebook", page=page_id)


def _clock(moment: datetime | None = None) -> str:
    return (moment or datetime.now()).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- versions and live editing

def record_edit(session, page: NotebookPage, who: str | None = None) -> None:
    """Keep the page's current state as a version (folding quick edits by
    the same person into the version they are already in)."""
    who = who or _me()
    now = _now()
    latest = session.scalar(select(NotebookVersion).where(NotebookVersion.page_id_fk == page.id)
                            .order_by(NotebookVersion.saved_at.desc(), NotebookVersion.id.desc()).limit(1))
    if latest is not None and latest.body == (page.body or "") and latest.title == (page.title or ""):
        return
    if (latest is not None and latest.kind == "auto" and latest.saved_by == who
            and now - latest.saved_at < VERSION_FOLD and now - (latest.opened_at or latest.saved_at) < VERSION_SPAN):
        latest.body, latest.title, latest.saved_at = page.body or "", page.title or "", now
        return
    session.add(NotebookVersion(page_id_fk=page.id, title=page.title or "", body=page.body or "", kind="auto",
                                label="", saved_by=who, saved_at=now, opened_at=now))
    info = info_for(session, page.id, create=True)
    info.edited_by = who


def last_editor(session, page_id: int) -> str | None:
    """Who sent the latest live-editing change: the author of a save that
    an open editor makes on their behalf."""
    return session.scalar(select(NotebookSyncUpdate.username).where(NotebookSyncUpdate.page_id_fk == page_id)
                          .order_by(NotebookSyncUpdate.id.desc()).limit(1)) or None


def reset_collab(session, page_id: int) -> int:
    """The body was replaced from outside the live editor: drop the editing
    state so every editor starts again from the saved text."""
    info = info_for(session, page_id, create=True)
    session.execute(delete(NotebookSyncUpdate).where(NotebookSyncUpdate.page_id_fk == page_id))
    info.collab_generation = (info.collab_generation or 0) + 1
    info.body_state = ""
    return info.collab_generation


def _state_vector(raw: str) -> dict[int, int] | None:
    """A Yjs state vector (base64): {client id: clock}; None if unreadable."""
    try:
        data = base64.b64decode(raw or "", validate=True)
    except (ValueError, TypeError):
        return None
    pos = 0

    def varuint() -> int:
        nonlocal pos
        value, shift = 0, 0
        while True:
            if pos >= len(data) or shift > 63:
                raise ValueError("truncated")
            byte = data[pos]
            pos += 1
            value |= (byte & 0x7F) << shift
            if byte < 0x80:
                return value
            shift += 7

    try:
        count = varuint()
        if count > 100_000:
            return None
        vector = {}
        for _ in range(count):
            client = varuint()
            vector[client] = varuint()
        return vector if pos == len(data) else None
    except ValueError:
        return None


def behind(saved_state: str, new_state: str) -> bool:
    """Whether a save from an editor at `new_state` holds less than the body
    already saved at `saved_state`: every edit it has, the saved one has, and
    more. Saves that each hold something the other lacks both go through;
    the editors meet, and the next save holds everything."""
    saved, new = _state_vector(saved_state), _state_vector(new_state)
    if not saved or new is None:
        return False
    return all(saved.get(c, 0) >= clock for c, clock in new.items()) and any(
        clock > new.get(c, 0) for c, clock in saved.items())


def collab_generation(session, page_id: int) -> int:
    info = info_for(session, page_id)
    return info.collab_generation if info else 0


def _lock_page_info(session, page_id: int) -> NotebookPageInfo:
    """Take the write lock for this page's editing state: an UPDATE on
    SQLite (one writer at a time), a row lock on PostgreSQL."""
    info = info_for(session, page_id, create=True)
    session.execute(NotebookPageInfo.__table__.update().where(NotebookPageInfo.page_id_fk == page_id)
                    .values(collab_generation=NotebookPageInfo.collab_generation))
    if session.bind.dialect.name == "postgresql":
        session.execute(select(NotebookPageInfo.id).where(NotebookPageInfo.page_id_fk == page_id).with_for_update())
    return info


def _b64_ok(data) -> bool:
    if not isinstance(data, str) or not data:
        return False
    try:
        base64.b64decode(data, validate=True)
        return True
    except (binascii.Error, ValueError):
        return False


@bp.get("/api/pages/<int:page_id>/sync")
def sync_pull(page_id: int):
    since = request.args.get("since", default=0, type=int)
    gen = request.args.get("gen", default=-1, type=int)
    client = (request.args.get("client") or "")[:40]
    with SessionLocal() as s:
        page, role = load_page(s, page_id)
        current = collab_generation(s, page_id)
        reset = gen != current
        start = 0 if reset else since
        rows = s.execute(select(NotebookSyncUpdate.id, NotebookSyncUpdate.client_id, NotebookSyncUpdate.data)
                         .where(NotebookSyncUpdate.page_id_fk == page_id,
                                NotebookSyncUpdate.generation == current,
                                NotebookSyncUpdate.id > start)
                         .order_by(NotebookSyncUpdate.id).limit(SYNC_BATCH)).all()
        empty = start == 0 and not rows
        updates = [{"id": r.id, "data": r.data} for r in rows if reset or r.client_id != client]
        last = rows[-1].id if rows else start
        cutoff = _now() - PRESENCE_TTL
        peers = s.scalars(select(NotebookPresence).where(NotebookPresence.page_id_fk == page_id,
                                                         NotebookPresence.updated_at >= cutoff,
                                                         NotebookPresence.client_id != client)).all()
        names = display_names(s, [p.username for p in peers])
        return jsonify({
            "ok": True, "gen": current, "reset": reset, "empty": empty, "updates": updates, "last": last,
            "more": len(rows) == SYNC_BATCH, "role": role,
            "peers": [{"client": p.client_id, "username": p.username, "name": names.get(p.username, p.username),
                       "state": p.state} for p in peers],
            "title": page.title, "updated_at": _iso(page.updated_at),
        })


@bp.post("/api/pages/<int:page_id>/sync")
def sync_push(page_id: int):
    data = _json_body()
    client = str(data.get("client") or "")[:40]
    if not client:
        return _fail("client required")
    updates = data.get("updates") or []
    if not isinstance(updates, list) or not all(_b64_ok(u) for u in updates):
        return _fail("updates must be base64 strings")
    if sum(len(u) for u in updates) > MAX_UPDATE_BYTES:
        return _fail("too large", 413)
    with SessionLocal() as s:
        page, role = load_page(s, page_id)
        me = _me()
        last = None
        if updates or data.get("init"):
            if not can_edit_role(role):
                return _fail(gettext("This page is view only."), 403)
            from .signatures import refuse_if_locked
            refused = refuse_if_locked(s, page_id)
            if refused:
                return refused
            info = _lock_page_info(s, page_id)
            if data.get("gen") != info.collab_generation:
                s.rollback()
                return jsonify({"ok": False, "reset": True, "gen": collab_generation(s, page_id)}), 409
            if data.get("init"):
                exists = s.scalar(select(NotebookSyncUpdate.id).where(
                    NotebookSyncUpdate.page_id_fk == page_id,
                    NotebookSyncUpdate.generation == info.collab_generation).limit(1))
                if exists is not None:
                    s.rollback()
                    return jsonify({"ok": False, "initialised": True}), 409
            for chunk in updates:
                row = NotebookSyncUpdate(page_id_fk=page_id, generation=info.collab_generation, client_id=client,
                                         username=me, data=chunk, created_at=_now())
                s.add(row)
                s.flush()
                last = row.id
        awareness = data.get("awareness")
        presence = s.scalar(select(NotebookPresence).where(NotebookPresence.page_id_fk == page_id,
                                                           NotebookPresence.client_id == client))
        if data.get("leaving"):
            if presence is not None:
                s.delete(presence)
        elif awareness is not None or presence is not None or updates:
            if presence is None:
                presence = NotebookPresence(page_id_fk=page_id, client_id=client, username=me, state="")
                s.add(presence)
            if isinstance(awareness, str) and _b64_ok(awareness) and len(awareness) < 64 * 1024:
                presence.state = awareness
            presence.updated_at = _now()
        s.execute(delete(NotebookPresence).where(NotebookPresence.updated_at < _now() - timedelta(minutes=10)))
        s.commit()
        return jsonify({"ok": True, "last": last})


@bp.post("/api/pages/<int:page_id>/sync/compact")
def sync_compact(page_id: int):
    """Replace the updates up to `upto` with one update holding the whole
    state (sent by an editor that has them all), so the log stays short."""
    data = _json_body()
    state, upto = data.get("state"), data.get("upto")
    if not _b64_ok(state) or not isinstance(upto, int):
        return _fail("state and upto required")
    with SessionLocal() as s:
        load_page(s, page_id, need="edit")
        info = _lock_page_info(s, page_id)
        if data.get("gen") != info.collab_generation:
            s.rollback()
            return jsonify({"ok": False, "reset": True}), 409
        s.execute(delete(NotebookSyncUpdate).where(NotebookSyncUpdate.page_id_fk == page_id,
                                                   NotebookSyncUpdate.generation == info.collab_generation,
                                                   NotebookSyncUpdate.id <= upto))
        s.add(NotebookSyncUpdate(page_id_fk=page_id, generation=info.collab_generation,
                                 client_id=str(data.get("client") or "")[:40], username=_me(), data=state))
        s.commit()
        return jsonify({"ok": True})


# ---------------------------------------------------------------- one page

def page_payload(session, page: NotebookPage, role: str) -> dict:
    info = info_for(session, page.id)
    owner = owner_of(page)
    names = display_names(session, [owner, info.presenter if info else "", info.edited_by if info else ""])
    shares = session.scalars(select(NotebookShare).where(NotebookShare.page_id_fk == page.id)).all()
    open_comments = session.scalar(select(func.count(NotebookComment.id)).where(
        NotebookComment.page_id_fk == page.id, NotebookComment.parent_id.is_(None),
        NotebookComment.resolved.is_(False))) or 0
    release = session.scalar(select(func.max(NotebookVersion.number)).where(
        NotebookVersion.page_id_fk == page.id, NotebookVersion.kind == "release"))
    protocol = None
    if info and info.protocol_page_id:
        source = session.get(NotebookPage, info.protocol_page_id)
        if source is not None and role_for(session, source) is not None:
            protocol = {"id": source.id, "title": source.title, "version": info.protocol_version}
    try:
        props = json.loads(page.properties) if page.properties else []
    except ValueError:
        props = []
    from . import signatures
    locked = signatures.is_locked(session, page.id)
    last_sign = next((r for r in reversed(signatures.history(session, page.id)) if r.action == "sign"), None)
    return {
        "signed_by": {"name": last_sign.name or last_sign.username, "at": _iso(last_sign.signed_at),
                      "local": last_sign.signed_at.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")}
        if last_sign else None,
        # Signed and locked: everyone reads it as a viewer; "real_role" keeps
        # what the person may do otherwise (the owner still shares it).
        "locked": locked, "signed": signatures.is_signed(session, page.id), "real_role": role,
        "id": page.id, "tab_id": page.tab_id_fk, "title": page.title, "body": page.body or "",
        "entry_date": _iso(page.entry_date), "properties": props if isinstance(props, list) else [],
        "created_at": _iso(page.created_at), "updated_at": _iso(page.updated_at),
        "role": "view" if locked else role, "owner": owner, "owner_name": names.get(owner, owner),
        "kind": info.kind if info else "note", "status": info.status if info else "",
        "tags": tag_list(info.tags if info else ""),
        "started_at": _iso(info.started_at if info else None),
        "finished_at": _iso(info.finished_at if info else None),
        "presenter": info.presenter if info else "",
        "presenter_name": names.get(info.presenter, "") if info and info.presenter else "",
        "series_id": info.series_id if info else None,
        "day": _iso(info.day if info else None),
        "edited_by": info.edited_by if info else "",
        "edited_by_name": names.get(info.edited_by, "") if info and info.edited_by else "",
        "gen": info.collab_generation if info else 0,
        "shared": bool(shares), "share_count": len(shares),
        "shared_with_lab": any(sh.username == EVERYONE for sh in shares),
        "open_comments": open_comments,
        "release": release,
        "protocol": protocol,
    }


def sidebar(session, user=None) -> dict:
    """What the notebook's sidebar lists beside the person's own topics."""
    me = access.username(user)
    shared_rows = session.execute(
        select(NotebookPage.id, NotebookPage.title, NotebookPage.updated_at, NotebookTab.owner_username)
        .join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id)
        .where(NotebookPage.id.in_(shared_page_ids(session, user)), NotebookTab.owner_username != me)
        .order_by(NotebookPage.updated_at.desc()).limit(40)).all()
    names = display_names(session, [r.owner_username for r in shared_rows])
    kinds = dict(session.execute(accessible_filter(
        select(NotebookPageInfo.page_id_fk, NotebookPageInfo.kind)
        .join(NotebookPage, NotebookPageInfo.page_id_fk == NotebookPage.id)
        .join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id), user)).all())
    base = accessible_filter(select(NotebookPage.id, NotebookPage.title, NotebookPage.updated_at,
                                    NotebookTab.owner_username, NotebookPageInfo.kind, NotebookPageInfo.status)
                             .join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id)
                             .join(NotebookPageInfo, NotebookPageInfo.page_id_fk == NotebookPage.id), user)
    protocols = session.execute(base.where(NotebookPageInfo.kind == "protocol")
                                .order_by(NotebookPage.title).limit(60)).all()
    running = session.execute(base.where(NotebookPageInfo.kind == "experiment",
                                         NotebookPageInfo.status.in_(["planned", "running"]))
                              .order_by(NotebookPage.updated_at.desc()).limit(20)).all()
    names.update(display_names(session, [r.owner_username for r in protocols]))
    return {
        "shared": [{"id": r.id, "title": r.title, "owner": r.owner_username,
                    "owner_name": names.get(r.owner_username, r.owner_username), "kind": kinds.get(r.id, "note"),
                    "updated_at": _iso(r.updated_at)} for r in shared_rows],
        "protocols": [{"id": r.id, "title": r.title, "owner": r.owner_username,
                       "owner_name": names.get(r.owner_username, r.owner_username)} for r in protocols],
        "running": [{"id": r.id, "title": r.title, "status": r.status} for r in running],
        "tags": tag_counts(session, user)[:30],
        "kinds_by_page": kinds,
    }


def tag_counts(session, user=None) -> list[dict]:
    stmt = accessible_filter(select(NotebookPageInfo.tags)
                             .join(NotebookPage, NotebookPageInfo.page_id_fk == NotebookPage.id)
                             .join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id)
                             .where(NotebookPageInfo.tags != ""), user)
    counts: dict[str, int] = {}
    for stored in session.scalars(stmt).all():
        for tag in tag_list(stored):
            counts[tag] = counts.get(tag, 0) + 1
    return [{"tag": t, "count": c} for t, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


@bp.get("/api/pages/<int:page_id>")
def page_get(page_id: int):
    with SessionLocal() as s:
        page, role = load_page(s, page_id)
        return jsonify({"ok": True, "page": page_payload(s, page, role)})


@bp.post("/api/pages/<int:page_id>/meta")
def page_meta(page_id: int):
    data = _json_body()
    with SessionLocal() as s:
        page, _role = load_page(s, page_id, need="edit")
        info = info_for(s, page.id, create=True)
        if "kind" in data and data["kind"] in KINDS:
            info.kind = data["kind"]
        if "status" in data and data["status"] in STATUSES:
            info.status = data["status"]
            if info.status == "running" and info.started_at is None:
                info.started_at = _now()
            if info.status in ("done", "failed") and info.finished_at is None:
                info.finished_at = _now()
            if info.status in ("", "planned"):
                info.finished_at = None
        if "tags" in data:
            info.tags = normalize_tags(data["tags"])
        if "presenter" in data:
            info.presenter = str(data["presenter"] or "")[:80]
        action = data.get("action")
        if action == "start":
            info.started_at, info.status, info.finished_at = _now(), "running", None
            if info.kind == "note":
                info.kind = "experiment"
        elif action == "finish":
            info.finished_at = _now()
            info.status = data.get("outcome") if data.get("outcome") in ("done", "failed") else "done"
            if info.started_at is None:
                info.started_at = info.finished_at
        page.updated_at = _now()
        s.commit()
        return jsonify({"ok": True, "page": page_payload(s, page, role_for(s, page))})


@bp.get("/api/pages/<int:page_id>/export.md")
def page_export(page_id: int):
    with SessionLocal() as s:
        page, _role = load_page(s, page_id)
        info = info_for(s, page.id)
        front = ["---", f"title: {json.dumps(page.title or 'Untitled page', ensure_ascii=False)}"]
        if page.entry_date:
            front.append(f"date: {page.entry_date.isoformat()}")
        if info and info.kind != "note":
            front.append(f"kind: {info.kind}")
        if info and info.status:
            front.append(f"status: {info.status}")
        if info and tag_list(info.tags):
            front.append("tags: [" + ", ".join(json.dumps(t, ensure_ascii=False) for t in tag_list(info.tags)) + "]")
        front.append(f"author: {owner_of(page)}")
        front.append("---")
        text = "\n".join(front) + "\n\n" + (page.body or "")
        name = re.sub(r"[^\w.-]+", "-", page.title or "page").strip("-")[:60] or "page"
        return Response(text, mimetype="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}.md"'})


FRONT_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


@bp.post("/api/import")
def page_import():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return _fail(gettext("Pick a Markdown file."))
    raw = upload.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        return _fail(gettext("That file is over 2 MB."), 413)
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
    title, tags, kind = "", [], "note"
    match = FRONT_RE.match(text)
    if match:
        for line in match.group(1).splitlines():
            key, _, value = line.partition(":")
            value = value.strip().strip('"').strip("'")
            if key.strip() == "title":
                title = value
            elif key.strip() == "tags":
                tags = [t.strip().strip('"\'') for t in value.strip("[]").split(",")]
            elif key.strip() == "kind" and value in KINDS:
                kind = value
        text = text[match.end():]
    if not title:
        heading = re.search(r"^#\s+(.+)$", text, re.M)
        title = heading.group(1).strip() if heading else re.sub(r"\.(md|markdown|txt)$", "", upload.filename, flags=re.I)
    with SessionLocal() as s:
        tab_id = request.form.get("tab_id", type=int)
        tab = s.get(NotebookTab, tab_id) if tab_id else None
        if tab is None or tab.owner_username != _me():
            tab = first_tab(s, _me())
        page = new_page(s, tab, title, text.lstrip("\n"), kind=kind)
        info_for(s, page.id).tags = normalize_tags(tags)
        record_edit(s, page)
        s.commit()
        return jsonify({"ok": True, "page_id": page.id, "tab_id": tab.id, "url": page_url(page.id)})


# ---------------------------------------------------------------- templates

_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}")
# Sections whose writing is that run's own: kept as headings only.
_RESULT_HEADING = re.compile(r"^#{1,6}\s+(results?|observations?|conclusions?|outcomes?|findings|discussion|"
                             r"interpretation|summary)\b", re.I)
_UPLOAD_LINE = re.compile(r"^\s*!?\[[^\]]*\]\(/static/uploads/[^)]*\)\s*$")


def _empty_block(kind: str, raw: str) -> str:
    """A block with its setup kept and its results gone: a data sheet keeps
    its columns and each row's first cell; a plate reader its layout of
    standards and blanks; a qPCR block its reference gene and control."""
    try:
        data = json.loads(raw)
    except ValueError:
        return raw
    if not isinstance(data, dict):
        return raw
    if kind == "sheet" and isinstance(data.get("rows"), list):
        data["rows"] = [[row[0]] + [""] * (len(row) - 1) if isinstance(row, list) and row else row
                        for row in data["rows"]]
    elif kind == "plate":
        data["values"] = {}
    elif kind == "qpcr":
        data["rows"] = []
    elif kind == "experiment":
        data["id"] = None
    else:
        return raw
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


_FENCE_OPEN = re.compile(r"(`{3,})([a-z]+)[ \t]*")
_FENCE_CLOSE = re.compile(r"(`{3,})[ \t]*")


def _swap_fences(body: str, keep) -> str:
    """Each ```kind … ``` block handed to `keep` (a match-like object with
    the fence, the kind and the text inside), in one pass over the lines:
    a page of unclosed fences costs no more than its length (a regex that
    looked ahead from each one grew with its square)."""
    import bisect

    class Found:
        def __init__(self, *groups):
            self._groups = groups

        def group(self, n):
            return self._groups[n - 1]

    lines = body.split("\n")
    closes: dict[str, list[int]] = {}
    for i, line in enumerate(lines):
        m = _FENCE_CLOSE.fullmatch(line)
        if m:
            closes.setdefault(m.group(1), []).append(i)
    out, i = [], 0
    while i < len(lines):
        m = _FENCE_OPEN.fullmatch(lines[i])
        if m:
            ends = closes.get(m.group(1), [])
            k = bisect.bisect_right(ends, i)
            if k < len(ends):
                j = ends[k]
                out.append(keep(Found(m.group(1), m.group(2), "\n".join(lines[i + 1:j]))))
                i = j + 1
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def structure_only(body: str) -> str:
    """A page as a template for the next run: its headings, text, steps and
    table headers stay; ticks are cleared, each table row keeps only its
    first cell, results in data blocks go, the writing under Results,
    Observations, Conclusion and the like goes, and uploaded pictures and
    files are left out."""
    blocks: list[str] = []

    def keep(match):
        blocks.append(f"{match.group(1)}{match.group(2)}\n{_empty_block(match.group(2), match.group(3))}\n{match.group(1)}")
        return f"\x00{len(blocks) - 1}\x00"

    text = _swap_fences(body or "", keep)
    out, in_table, results_level = [], 0, 0
    for line in text.split("\n"):
        stripped = line.strip()
        if _UPLOAD_LINE.match(line):
            continue
        heading = re.match(r"^(#{1,6})\s", stripped)
        if heading:
            level = len(heading.group(1))
            if results_level and level <= results_level:
                results_level = 0
            if _RESULT_HEADING.match(stripped):
                results_level = level
        elif results_level and stripped and not stripped.startswith(("|", "\x00")):
            continue                       # what was seen and concluded that time
        line = re.sub(r"^(\s*[-*+] )\[[xX]\]", r"\1[ ]", line)
        if stripped.startswith("|"):
            in_table += 1
            if in_table > 2 and not _TABLE_SEP.match(line):        # a body row: its first cell only
                cells = stripped.strip("|").split("|")
                line = "| " + cells[0].strip() + " |" + "  |" * (len(cells) - 1)
        else:
            in_table = 0
        out.append(line)
    text = "\n".join(out)
    return re.sub(r"\x00(\d+)\x00", lambda m: blocks[int(m.group(1))], text)


# ---------------------------------------------------------------- new pages

def _today_label(day: date) -> str:
    return i18n.strftime(day, "%a %d %b %Y")


def _fill(body: str, **values) -> str:
    for key, value in values.items():
        body = body.replace("{" + key + "}", value)
    return body


@bp.get("/api/starters")
def starters_list():
    return jsonify({"ok": True, "starters": [
        {"key": key, "title": s["title"], "kind": s["kind"], "icon": s["icon"], "hint": s["hint"]}
        for key, s in STARTERS.items()]})


@bp.post("/api/pages/new")
def page_new():
    """A new page: blank, from a built-in starter, or from a saved template."""
    data = _json_body() or request.form.to_dict()
    with SessionLocal() as s:
        me = _me()
        tab = None
        if data.get("tab_id"):
            tab = s.get(NotebookTab, _int(data["tab_id"]) or 0)
            if tab is None or tab.owner_username != me:
                return _fail(gettext("That topic is not yours."), 404)
        title, body, kind = (data.get("title") or "").strip(), "", "note"
        starter = STARTERS.get(data.get("starter") or "")
        if starter:
            kind = starter["kind"]
            body = _fill(starter["body"], date=date.today().isoformat(), presenter="", now=_clock())
            title = title or starter["title"]
        elif data.get("template_id"):
            template = s.get(NotebookTemplate, _int(data["template_id"]) or 0)
            if template is None or not (template.owner_username == me or template.lab):
                return _fail(gettext("Template not found."), 404)
            title, body = title or template.title, template.body or ""
            kind = template.kind if template.kind in KINDS and template.kind != "daily" else "note"
        if tab is None:
            # The topic open in the notebook, for a plain page: started from
            # the SOPs topic, it belongs there. Experiments and meetings go to
            # their own topics wherever they're started.
            open_tab = s.get(NotebookTab, _int(data.get("open_tab_id")) or 0) if data.get("open_tab_id") else None
            if kind == "experiment":
                tab = tab_named(s, me, EXPERIMENTS_TAB)
            elif kind in ("meeting", "seminar"):
                tab = tab_named(s, me, MEETINGS_TAB)
            elif open_tab is not None and open_tab.owner_username == me:
                tab = open_tab
            else:
                tab = tab_named(s, me, "Inbox")      # not whichever topic happens to be first
        extra = {"status": "planned"} if kind == "experiment" else {}
        page = new_page(s, tab, title or "Untitled page", body, kind=kind, **extra)
        if body:
            record_edit(s, page)
        s.commit()
        return jsonify({"ok": True, "page_id": page.id, "tab_id": tab.id, "url": page_url(page.id)})


@bp.get("/today")
def today():
    """Today's daily log page, made the first time it is opened."""
    day = date.today()
    with SessionLocal() as s:
        me = _me()
        page_id = s.scalar(select(NotebookPage.id)
                           .join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id)
                           .join(NotebookPageInfo, NotebookPageInfo.page_id_fk == NotebookPage.id)
                           .where(NotebookTab.owner_username == me, NotebookPageInfo.kind == "daily",
                                  NotebookPageInfo.day == day).limit(1))
        if page_id is None:
            tab = tab_named(s, me, DAILY_TAB)
            page = new_page(s, tab, _today_label(day), STARTERS["daily"]["body"], kind="daily", day=day)
            page.entry_date = day
            s.commit()
            page_id = page.id
        return redirect(url_for("notebook", page=page_id))


# ---------------------------------------------------------------- sharing

@bp.get("/api/pages/<int:page_id>/shares")
def shares_list(page_id: int):
    with SessionLocal() as s:
        page, role = load_page(s, page_id)
        rows = s.scalars(select(NotebookShare).where(NotebookShare.page_id_fk == page_id)
                         .order_by(NotebookShare.created_at)).all()
        names = display_names(s, [r.username for r in rows])
        owner = owner_of(page)
        def name_of(username):
            if username == EVERYONE:
                return gettext("Everyone in the lab")
            group_id = groups.page_share_group(username)
            if group_id is not None:
                return gettext("%(group)s (project group)", group=groups.name_of(group_id) or gettext("A deleted group"))
            return names.get(username, username)
        return jsonify({"ok": True, "role": role, "owner": owner,
                        "owner_name": display_names(s, [owner]).get(owner, owner),
                        "shares": [{"username": r.username, "role": r.role, "name": name_of(r.username),
                                    "group": groups.page_share_group(r.username) is not None} for r in rows],
                        "people": [p for p in people(s) if p["username"] != owner],
                        # The project groups the owner may share with (app/groups.py).
                        "groups": [{"username": f"{groups.PAGE_SHARE_PREFIX}{gid}", "name": name}
                                   for gid, name in groups.choices()]})


@bp.post("/api/pages/<int:page_id>/shares")
def shares_set(page_id: int):
    data = _json_body()
    username, share_role = str(data.get("username") or "").strip(), data.get("role") or "view"
    if share_role not in ROLES:
        return _fail(gettext("Role is view or edit."))
    with SessionLocal() as s:
        page, _role = load_page(s, page_id, need="owner")
        group_id = groups.page_share_group(username)
        if group_id is not None:
            if not groups.may_share_with(group_id):
                return _fail(groups.refusal(group_id), 403)
        elif username != EVERYONE:
            user = s.scalar(select(UserAccount).where(UserAccount.username == username))
            if user is None or user.disabled:
                return _fail(gettext("No one in the lab by that name."), 404)
            if username == owner_of(page):
                return _fail(gettext("It is already theirs."))
        row = s.scalar(select(NotebookShare).where(NotebookShare.page_id_fk == page_id,
                                                   NotebookShare.username == username))
        is_new = row is None
        if is_new:
            row = NotebookShare(page_id_fk=page_id, username=username, shared_by=_me())
            s.add(row)
        row.role = share_role
        if is_new and username != EVERYONE:
            told = sorted(groups.members_of(group_id) - {_me(), owner_of(page)}) if group_id is not None \
                else [username]
            title = ("%(who)s shared “%(title)s” with you (%(group)s)" if group_id is not None
                     else "%(who)s shared “%(title)s” with you")
            message = ("You can edit it in your notebook, under Shared with me." if share_role == "edit"
                       else "You can read it in your notebook, under Shared with me.")
            values = {"who": g.user.display_name or _me(), "title": page.title,
                      "group": groups.name_of(group_id) if group_id is not None else ""}
            for person in told:
                notify.send(s, person, title, message, category="notebook", link=page_url(page_id), actor=_me(),
                            values=values, message_values={})
        s.commit()
        return jsonify({"ok": True})


@bp.post("/api/pages/<int:page_id>/shares/remove")
def shares_remove(page_id: int):
    username = str(_json_body().get("username") or "")
    with SessionLocal() as s:
        load_page(s, page_id, need="owner")
        s.execute(delete(NotebookShare).where(NotebookShare.page_id_fk == page_id,
                                              NotebookShare.username == username))
        s.commit()
        return jsonify({"ok": True})


# ---------------------------------------------------------------- versions

@bp.get("/api/pages/<int:page_id>/versions")
def versions_list(page_id: int):
    with SessionLocal() as s:
        load_page(s, page_id)
        rows = s.scalars(select(NotebookVersion).where(NotebookVersion.page_id_fk == page_id)
                         .order_by(NotebookVersion.saved_at.desc(), NotebookVersion.id.desc()).limit(300)).all()
        names = display_names(s, [r.saved_by for r in rows])
        return jsonify({"ok": True, "versions": [{
            "id": r.id, "kind": r.kind, "number": r.number, "saved_by": r.saved_by,
            "saved_by_name": names.get(r.saved_by, r.saved_by), "saved_at": _iso(r.saved_at),
            "label": r.label, "title": r.title,
            "chars": len(r.body or ""),
        } for r in rows]})


@bp.get("/api/versions/<int:version_id>")
def version_get(version_id: int):
    with SessionLocal() as s:
        version = s.get(NotebookVersion, version_id)
        if version is None:
            abort(404)
        load_page(s, version.page_id_fk)
        return jsonify({"ok": True, "version": {"id": version.id, "title": version.title, "body": version.body,
                                                "saved_at": _iso(version.saved_at), "number": version.number}})


@bp.post("/api/pages/<int:page_id>/versions")
def version_save(page_id: int):
    """Save the page as it is now as a named version; for a protocol,
    `release` numbers it (v1, v2, ...) for experiments to follow."""
    data = _json_body()
    with SessionLocal() as s:
        page, _role = load_page(s, page_id, need="edit")
        release = bool(data.get("release"))
        number = None
        if release:
            number = (s.scalar(select(func.max(NotebookVersion.number)).where(
                NotebookVersion.page_id_fk == page_id, NotebookVersion.kind == "release")) or 0) + 1
        label = str(data.get("label") or "").strip()[:160] or (f"v{number}" if release else "Saved version")
        version = NotebookVersion(page_id_fk=page_id, title=page.title, body=page.body or "",
                                  kind="release" if release else "manual", label=label, number=number,
                                  saved_by=_me(), saved_at=_now())
        s.add(version)
        s.commit()
        return jsonify({"ok": True, "id": version.id, "number": number})


@bp.post("/api/versions/<int:version_id>/restore")
def version_restore(version_id: int):
    with SessionLocal() as s:
        version = s.get(NotebookVersion, version_id)
        if version is None:
            abort(404)
        page, _role = load_page(s, version.page_id_fk, need="edit")
        record_edit(s, page)  # what is there now stays in the history
        page.body, page.title, page.updated_at = version.body or "", version.title or page.title, _now()
        s.add(NotebookVersion(page_id_fk=page.id, title=page.title, body=page.body, kind="restore",
                              label=f"Restored the version of {version.saved_at:%d %b %Y %H:%M}",
                              saved_by=_me(), saved_at=_now()))
        gen = reset_collab(s, page.id)
        s.commit()
        return jsonify({"ok": True, "gen": gen})


# ---------------------------------------------------------------- comments

def _comment_json(c: NotebookComment, names: dict, me: str, page_owner: str) -> dict:
    return {"id": c.id, "parent_id": c.parent_id, "author": c.author, "author_name": names.get(c.author, c.author),
            "body": c.body, "quote": c.quote, "resolved": c.resolved, "created_at": _iso(c.created_at),
            "updated_at": _iso(c.updated_at), "mine": c.author == me,
            "can_delete": c.author == me or page_owner == me}


def mentioned_users(session, text: str) -> list[str]:
    handles = {m.group(1).rstrip(".") for m in MENTION_RE.finditer(text or "")}
    if not handles:
        return []
    return list(session.scalars(select(UserAccount.username).where(UserAccount.username.in_(handles),
                                                                   UserAccount.disabled.is_(False))).all())


@bp.get("/api/pages/<int:page_id>/comments")
def comments_list(page_id: int):
    with SessionLocal() as s:
        page, _role = load_page(s, page_id)
        rows = s.scalars(select(NotebookComment).where(NotebookComment.page_id_fk == page_id)
                         .order_by(NotebookComment.created_at, NotebookComment.id)).all()
        names = display_names(s, [c.author for c in rows])
        me, owner = _me(), owner_of(page)
        threads = []
        by_id = {}
        for c in rows:
            item = _comment_json(c, names, me, owner)
            if c.parent_id is None:
                item["replies"] = []
                threads.append(item)
                by_id[c.id] = item
            elif c.parent_id in by_id:
                by_id[c.parent_id]["replies"].append(item)
        return jsonify({"ok": True, "threads": threads,
                        "people": [{"username": p["username"], "name": p["name"]} for p in people(s)]})


@bp.post("/api/pages/<int:page_id>/comments")
def comment_add(page_id: int):
    data = _json_body()
    body = str(data.get("body") or "").strip()
    if not body:
        return _fail(gettext("Write something first."))
    with SessionLocal() as s:
        page, _role = load_page(s, page_id)
        me = _me()
        parent = None
        if data.get("parent_id"):
            parent = s.get(NotebookComment, _int(data["parent_id"]) or 0)
            if parent is None or parent.page_id_fk != page_id:
                return _fail(gettext("That thread is gone."), 404)
            if parent.parent_id is not None:
                parent = s.get(NotebookComment, parent.parent_id)
        comment = NotebookComment(page_id_fk=page_id, parent_id=parent.id if parent else None, author=me,
                                  body=body[:5000], quote=str(data.get("quote") or "")[:500] if not parent else "",
                                  created_at=_now())
        s.add(comment)
        if parent is not None and parent.resolved:
            parent.resolved = False
        s.flush()
        who = g.user.display_name or me
        link = page_url(page_id) + f"#comment-{(parent or comment).id}"
        mentioned = mentioned_users(s, body)
        told, no_access = set(), []
        for username in mentioned:
            target = s.scalar(select(UserAccount).where(UserAccount.username == username))
            if role_for(s, page, target) is None:
                no_access.append(username)
                continue
            if notify.send(s, username, "%(who)s mentioned you on “%(title)s”", body[:300],
                           category="notebook", link=link, actor=me, values={"who": who, "title": page.title}):
                told.add(username)
        followers = {owner_of(page)}
        if parent is not None:
            followers.add(parent.author)
            followers.update(s.scalars(select(NotebookComment.author).where(NotebookComment.parent_id == parent.id)))
        for username in followers - told - {me}:
            target = s.scalar(select(UserAccount).where(UserAccount.username == username))
            if target is not None and role_for(s, page, target) is not None:
                notify.send(s, username, "%(who)s commented on “%(title)s”", body[:300],
                            category="notebook", link=link, actor=me, values={"who": who, "title": page.title})
        s.commit()
        return jsonify({"ok": True, "id": comment.id, "no_access": no_access,
                        "no_access_names": list(display_names(s, no_access).values())})


def _own_comment(s, comment_id: int, need_delete: bool = False):
    comment = s.get(NotebookComment, comment_id)
    if comment is None:
        abort(404)
    page, role = load_page(s, comment.page_id_fk)
    if need_delete and comment.author != _me() and role != "owner":
        abort(403)
    return comment, page, role


@bp.post("/api/comments/<int:comment_id>/resolve")
def comment_resolve(comment_id: int):
    with SessionLocal() as s:
        comment, _page, _role = _own_comment(s, comment_id)
        comment.resolved = bool(_json_body().get("resolved", True))
        comment.resolved_by = _me() if comment.resolved else ""
        s.commit()
        return jsonify({"ok": True})


@bp.post("/api/comments/<int:comment_id>/edit")
def comment_edit(comment_id: int):
    body = str(_json_body().get("body") or "").strip()
    with SessionLocal() as s:
        comment, _page, _role = _own_comment(s, comment_id)
        if comment.author != _me():
            abort(403)
        if not body:
            return _fail(gettext("Write something first."))
        comment.body, comment.updated_at = body[:5000], _now()
        s.commit()
        return jsonify({"ok": True})


@bp.post("/api/comments/<int:comment_id>/delete")
def comment_delete(comment_id: int):
    with SessionLocal() as s:
        comment, _page, _role = _own_comment(s, comment_id, need_delete=True)
        if comment.parent_id is None:
            s.execute(delete(NotebookComment).where(NotebookComment.parent_id == comment.id))
        s.delete(comment)
        s.commit()
        return jsonify({"ok": True})


# ---------------------------------------------------------------- search and tags

def _snippet(body: str, terms: list[str], width: int = 160) -> str:
    text = re.sub(r"```[a-z]*\n.*?```", " ", body or "", flags=re.S)
    text = re.sub(r"[#>*_`|\[\]]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    lower = text.lower()
    at = min((lower.find(t) for t in terms if lower.find(t) >= 0), default=-1)
    if at < 0:
        return text[:width] + ("…" if len(text) > width else "")
    start = max(0, at - width // 3)
    piece = text[start:start + width]
    return ("…" if start else "") + piece + ("…" if start + width < len(text) else "")


@bp.get("/api/search")
def search():
    q = (request.args.get("q") or "").strip()
    terms = [t.lower() for t in q.split() if t][:8]
    tag = (request.args.get("tag") or "").strip().lower()
    kind = request.args.get("kind") or ""
    status = request.args.get("status") or ""
    whose = request.args.get("whose") or "all"  # all | mine | shared
    date_from, date_to = _date(request.args.get("from")), _date(request.args.get("to"))
    with SessionLocal() as s:
        me = _me()
        stmt = (select(NotebookPage, NotebookTab.title.label("tab_title"), NotebookTab.owner_username,
                       NotebookPageInfo.kind, NotebookPageInfo.status, NotebookPageInfo.tags)
                .join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id)
                .outerjoin(NotebookPageInfo, NotebookPageInfo.page_id_fk == NotebookPage.id))
        stmt = accessible_filter(stmt)
        if whose == "mine":
            stmt = stmt.where(NotebookTab.owner_username == me)
        elif whose == "shared":
            stmt = stmt.where(NotebookTab.owner_username != me)
        for term in terms:
            like = like_pattern(term)
            stmt = stmt.where(or_(NotebookPage.title.ilike(like, escape="\\"), NotebookPage.body.ilike(like, escape="\\")))
        if tag:
            stmt = stmt.where(NotebookPageInfo.tags.like(f"%,{tag},%"))
        if kind in KINDS:
            stmt = stmt.where(NotebookPageInfo.kind == kind) if kind != "note" else stmt.where(
                or_(NotebookPageInfo.kind.is_(None), NotebookPageInfo.kind == "note"))
        if status in STATUSES and status:
            stmt = stmt.where(NotebookPageInfo.status == status)
        if date_from:
            stmt = stmt.where(or_(NotebookPage.entry_date >= date_from,
                                  NotebookPage.updated_at >= datetime.combine(date_from, datetime.min.time())))
        if date_to:
            stmt = stmt.where(NotebookPage.entry_date <= date_to)
        rows = s.execute(stmt.order_by(NotebookPage.updated_at.desc()).limit(100)).all()
        names = display_names(s, [r.owner_username for r in rows])
        return jsonify({"ok": True, "results": [{
            "id": r.NotebookPage.id, "title": r.NotebookPage.title or "Untitled page", "tab": r.tab_title,
            "owner": r.owner_username, "owner_name": names.get(r.owner_username, r.owner_username),
            "mine": r.owner_username == me, "kind": r.kind or "note", "status": r.status or "",
            "tags": tag_list(r.tags or ""), "entry_date": _iso(r.NotebookPage.entry_date),
            "updated_at": _iso(r.NotebookPage.updated_at), "snippet": _snippet(r.NotebookPage.body, terms),
            "url": page_url(r.NotebookPage.id),
        } for r in rows]})


@bp.get("/api/tags")
def tags():
    with SessionLocal() as s:
        return jsonify({"ok": True, "tags": tag_counts(s)})


@bp.get("/api/people")
def people_list():
    with SessionLocal() as s:
        return jsonify({"ok": True, "people": people(s), "me": _me()})


# ---------------------------------------------------------------- protocols and experiments

@bp.get("/api/protocols")
def protocols():
    with SessionLocal() as s:
        stmt = accessible_filter(select(NotebookPage.id, NotebookPage.title, NotebookPage.updated_at,
                                        NotebookTab.owner_username, NotebookPageInfo.tags)
                                 .join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id)
                                 .join(NotebookPageInfo, NotebookPageInfo.page_id_fk == NotebookPage.id)
                                 .where(NotebookPageInfo.kind == "protocol"))
        rows = s.execute(stmt.order_by(NotebookPage.title)).all()
        releases = dict(s.execute(select(NotebookVersion.page_id_fk, func.max(NotebookVersion.number))
                                  .where(NotebookVersion.kind == "release")
                                  .group_by(NotebookVersion.page_id_fk)).all())
        names = display_names(s, [r.owner_username for r in rows])
        return jsonify({"ok": True, "protocols": [{
            "id": r.id, "title": r.title, "owner": r.owner_username,
            "owner_name": names.get(r.owner_username, r.owner_username), "version": releases.get(r.id),
            "tags": tag_list(r.tags or ""), "updated_at": _iso(r.updated_at)} for r in rows],
            "presets": notebook_protocols.preset_list()})


def _latest_release(session, page_id: int):
    return session.scalar(select(NotebookVersion).where(NotebookVersion.page_id_fk == page_id,
                                                        NotebookVersion.kind == "release")
                          .order_by(NotebookVersion.number.desc()).limit(1))


@bp.get("/api/protocols/text")
def protocol_text():
    """A protocol ready to insert into a page: a line saying which protocol
    (and version) it is, then its text with the steps as a checklist. A lab
    protocol gives its latest numbered version, or its current text."""
    with SessionLocal() as s:
        if request.args.get("preset"):
            preset = notebook_protocols.PRESET_PROTOCOLS.get(request.args["preset"])
            if preset is None:
                return _fail(gettext("That protocol is not built in."), 404)
            title, body = preset["title"], preset["body"]
            header = f"> **Protocol:** {title} (built in)"
        elif _int(request.args.get("page")):
            page, _role = load_page(s, _int(request.args["page"]))
            release = _latest_release(s, page.id)
            title = page.title or "Protocol"
            body = release.body if release is not None else (page.body or "")
            version = f" · v{release.number}" if release is not None else ""
            header = f"> **Protocol:** [{title}]({page_url(page.id)}){version}"
        else:
            return _fail(gettext("Choose a protocol to insert."))
        return jsonify({"ok": True, "title": title,
                        "markdown": f"\n{header}\n\n{steps_as_checklist(body, title)}\n"})


BLANK_PROTOCOL = """## Purpose

## Materials
- 

## Steps
1. 

## Notes
"""


@bp.post("/api/protocols/new")
def protocol_new():
    """A new protocol page of one's own: blank, or a copy of a built-in one
    to change. It goes in the Protocols topic."""
    data = _json_body()
    preset = notebook_protocols.PRESET_PROTOCOLS.get(str(data.get("preset") or ""))
    if data.get("preset") and preset is None:
        return _fail(gettext("That protocol is not built in."), 404)
    title = str(data.get("title") or "").strip()[:200] or (preset["title"] if preset else "New protocol")
    with SessionLocal() as s:
        tab = tab_named(s, _me(), PROTOCOLS_TAB)
        page = new_page(s, tab, title, preset["body"] if preset else BLANK_PROTOCOL, kind="protocol")
        record_edit(s, page)
        s.commit()
        return jsonify({"ok": True, "page_id": page.id, "url": page_url(page.id)})


ORDERED_RE = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(?!\[[ xX]\])(.*)$")
HEADING_RE = re.compile(r"^(#{1,5})\s+(.*)$")
STEPS_HEADING_RE = re.compile(r"step|procedure|protocol|method", re.I)


def steps_as_checklist(body: str, title: str = "") -> str:
    """A protocol's text for an experiment: numbered steps become a
    checklist (or the bullets under a Steps/Procedure heading, when it has
    no numbered ones), headings move one level down, fenced blocks are kept
    as they are."""
    lines = (body or "").split("\n")
    has_numbered = any(ORDERED_RE.match(line) for line in lines)
    out, in_fence, in_steps = [], False, False
    for line in lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        heading = HEADING_RE.match(line)
        if heading:
            if len(heading.group(1)) == 1 and heading.group(2).strip() == (title or "").strip():
                continue
            in_steps = bool(STEPS_HEADING_RE.search(heading.group(2)))
            out.append("#" + heading.group(1) + " " + heading.group(2))
            continue
        numbered = ORDERED_RE.match(line)
        if numbered:
            out.append(f"{numbered.group(1)}- [ ] {numbered.group(2)}")
            continue
        bullet = BULLET_RE.match(line)
        if bullet and not has_numbered and in_steps:
            out.append(f"{bullet.group(1)}- [ ] {bullet.group(2)}")
            continue
        out.append(line)
    return "\n".join(out).strip("\n")


@bp.post("/api/pages/<int:page_id>/start-experiment")
def start_experiment(page_id: int):
    """A new experiment page that follows this protocol, at its latest
    numbered version (numbering the current text v1 if it has none)."""
    data = _json_body()
    with SessionLocal() as s:
        protocol, role = load_page(s, page_id)
        release = s.scalar(select(NotebookVersion).where(NotebookVersion.page_id_fk == page_id,
                                                         NotebookVersion.kind == "release")
                           .order_by(NotebookVersion.number.desc()).limit(1))
        if release is None and can_edit_role(role):
            release = NotebookVersion(page_id_fk=page_id, title=protocol.title, body=protocol.body or "",
                                      kind="release", label="v1", number=1, saved_by=_me(), saved_at=_now())
            s.add(release)
            s.flush()
        source_body = release.body if release is not None else (protocol.body or "")
        number = release.number if release is not None else None
        me = _me()
        started = datetime.now()
        who = g.user.display_name or me
        version = f" · v{number}" if number else ""
        header = (f"> **Protocol:** [{protocol.title}]({page_url(protocol.id)}){version} · "
                  f"**Started:** {_clock(started)} by {who}")
        body = "\n\n".join([
            header,
            "## Aim\n\n",
            "## Samples & materials\n\n| Reagent / sample | Lot | Amount | Note |\n| --- | --- | --- | --- |\n|  |  |  |  |",
            "## Protocol\n\n" + steps_as_checklist(source_body, protocol.title),
            "## Deviations\n\n",
            "## Results\n\n",
            "## Conclusion\n\n",
        ])
        title = str(data.get("title") or "").strip() or f"{protocol.title} — {started:%d %b %Y}"
        tab = tab_named(s, me, EXPERIMENTS_TAB)
        page = new_page(s, tab, title, body, kind="experiment", status="running", started_at=_now(),
                        protocol_page_id=protocol.id, protocol_version=number)
        info = info_for(s, protocol.id)
        if info is not None and info.tags:
            info_for(s, page.id).tags = info.tags
        record_edit(s, page)
        s.commit()
        return jsonify({"ok": True, "page_id": page.id, "url": page_url(page.id)})


@bp.get("/api/pages/<int:page_id>/experiments")
def protocol_experiments(page_id: int):
    """Experiments that followed this protocol, that this person may see."""
    with SessionLocal() as s:
        load_page(s, page_id)
        stmt = accessible_filter(select(NotebookPage.id, NotebookPage.title, NotebookPageInfo.status,
                                        NotebookPageInfo.protocol_version, NotebookPageInfo.started_at,
                                        NotebookTab.owner_username)
                                 .join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id)
                                 .join(NotebookPageInfo, NotebookPageInfo.page_id_fk == NotebookPage.id)
                                 .where(NotebookPageInfo.protocol_page_id == page_id))
        rows = s.execute(stmt.order_by(NotebookPageInfo.started_at.desc()).limit(50)).all()
        names = display_names(s, [r.owner_username for r in rows])
        return jsonify({"ok": True, "experiments": [{
            "id": r.id, "title": r.title, "status": r.status, "version": r.protocol_version,
            "started_at": _iso(r.started_at), "owner_name": names.get(r.owner_username, r.owner_username)}
            for r in rows]})


# ---------------------------------------------------------------- meetings

def _members(series: NotebookMeetingSeries) -> list[str]:
    try:
        members = json.loads(series.members or "[]")
    except ValueError:
        members = []
    return [str(m) for m in members if m] if isinstance(members, list) else []


def upcoming_dates(series: NotebookMeetingSeries, count: int, start: date | None = None) -> list[date]:
    start = start or date.today()
    if series.weekday is None:
        return []
    first = start + timedelta(days=(series.weekday - start.weekday()) % 7)
    return [first + timedelta(weeks=i) for i in range(count)]


def _series_json(s, series: NotebookMeetingSeries) -> dict:
    members = _members(series)
    names = display_names(s, members + [series.owner])
    n = len(members)
    nxt = series.next_index % n if n else 0
    dates = upcoming_dates(series, max(n, 1))
    upcoming = []
    for i in range(n):
        who = members[(nxt + i) % n]
        upcoming.append({"presenter": who, "name": names.get(who, who),
                         "date": dates[i].isoformat() if i < len(dates) else ""})
    notes = s.execute(accessible_filter(
        select(NotebookPage.id, NotebookPage.title, NotebookPage.entry_date, NotebookPageInfo.presenter)
        .join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id)
        .join(NotebookPageInfo, NotebookPageInfo.page_id_fk == NotebookPage.id)
        .where(NotebookPageInfo.series_id == series.id))
        .order_by(NotebookPage.entry_date.desc(), NotebookPage.id.desc()).limit(12)).all()
    me = _me()
    return {
        "id": series.id, "name": series.name, "owner": series.owner, "owner_name": names.get(series.owner, ""),
        "members": [{"username": m, "name": names.get(m, m)} for m in members], "next_index": nxt,
        "next": upcoming[0] if upcoming else None, "upcoming": upcoming,
        "weekday": series.weekday, "weekday_name": WEEKDAYS[series.weekday] if series.weekday is not None else "",
        "time": series.time, "location": series.location,
        "can_edit": access.is_admin() or series.owner == me or me in members,
        "notes": [{"id": r.id, "title": r.title, "date": _iso(r.entry_date), "presenter": r.presenter,
                   "url": page_url(r.id)} for r in notes],
    }


def _series_or_404(s, series_id: int, need_edit: bool = True) -> NotebookMeetingSeries:
    series = s.get(NotebookMeetingSeries, series_id)
    if series is None:
        abort(404)
    if need_edit and not (access.is_admin() or series.owner == _me() or _me() in _members(series)):
        abort(403)
    return series


def _apply_series(s, series: NotebookMeetingSeries, data: dict) -> str | None:
    if "name" in data:
        name = str(data.get("name") or "").strip()[:160]
        if not name:
            return gettext("Give the meeting a name.")
        series.name = name
    if "members" in data:
        wanted = [str(m) for m in (data.get("members") or []) if m]
        known = set(s.scalars(select(UserAccount.username).where(UserAccount.username.in_(wanted))).all())
        members = []
        for m in wanted:
            if m in known and m not in members:
                members.append(m)
        series.members = json.dumps(members)
        if members:
            series.next_index = series.next_index % len(members)
    if "weekday" in data:
        wd = data.get("weekday")
        series.weekday = int(wd) if wd not in (None, "") and str(wd).isdigit() and 0 <= int(wd) <= 6 else None
    if "time" in data:
        t = str(data.get("time") or "").strip()
        series.time = t if re.fullmatch(r"\d{2}:\d{2}", t) else ""
    if "location" in data:
        series.location = str(data.get("location") or "").strip()[:160]
    if "next_index" in data and str(data["next_index"]).lstrip("-").isdigit():
        n = len(_members(series)) or 1
        series.next_index = int(data["next_index"]) % n
    return None


@bp.get("/api/meetings")
def meetings():
    with SessionLocal() as s:
        rows = s.scalars(select(NotebookMeetingSeries).order_by(NotebookMeetingSeries.name)).all()
        return jsonify({"ok": True, "series": [_series_json(s, r) for r in rows], "people": people(s),
                        "weekdays": WEEKDAYS})


@bp.post("/api/meetings")
def meeting_create():
    data = _json_body()
    with SessionLocal() as s:
        series = NotebookMeetingSeries(name="", owner=_me(), members="[]", next_index=0)
        data.setdefault("name", "")
        error = _apply_series(s, series, data)
        if error:
            return _fail(error)
        s.add(series)
        s.commit()
        return jsonify({"ok": True, "series": _series_json(s, series)})


@bp.post("/api/meetings/<int:series_id>")
def meeting_update(series_id: int):
    with SessionLocal() as s:
        series = _series_or_404(s, series_id)
        error = _apply_series(s, series, _json_body())
        if error:
            return _fail(error)
        s.commit()
        return jsonify({"ok": True, "series": _series_json(s, series)})


@bp.post("/api/meetings/<int:series_id>/advance")
def meeting_advance(series_id: int):
    step = _json_body().get("step", 1)
    with SessionLocal() as s:
        series = _series_or_404(s, series_id)
        n = len(_members(series)) or 1
        series.next_index = (series.next_index + (1 if step not in (-1, "-1") else -1)) % n
        s.commit()
        return jsonify({"ok": True, "series": _series_json(s, series)})


@bp.post("/api/meetings/<int:series_id>/delete")
def meeting_delete(series_id: int):
    with SessionLocal() as s:
        series = _series_or_404(s, series_id)
        if not (access.is_admin() or series.owner == _me()):
            abort(403)
        s.delete(series)
        s.commit()
        return jsonify({"ok": True})


def create_meeting_note(s, series: NotebookMeetingSeries, when: date | None = None,
                        advance: bool = True) -> NotebookPage:
    """Notes for a meeting of the series: a page shared with everyone in the
    rotation (they can all write in it), naming who presents; the rotation
    moves on to the next person."""
    me = _me()
    members = _members(series)
    when = when or (upcoming_dates(series, 1) or [date.today()])[0]
    # A date further down the rotation (opened from its calendar event) is
    # that turn's presenter, and leaves the rotation where it is.
    ahead = upcoming_dates(series, len(members) or 1)
    turn = ahead.index(when) if when in ahead else 0
    presenter = members[(series.next_index + turn) % len(members)] if members else ""
    advance = advance and turn == 0
    names = display_names(s, members)
    attendees = ", ".join(names.get(m, m) for m in members)
    body = _fill(STARTERS["meeting"]["body"], date=when.isoformat(),
                 presenter=f"@{presenter}" if presenter else "", now=_clock())
    body = body.replace("**Attendees:** ", f"**Attendees:** {attendees}", 1)
    title = f"{series.name} — {when:%d %b %Y}" + (f" — {names.get(presenter, presenter)}" if presenter else "")
    page = new_page(s, tab_named(s, me, MEETINGS_TAB), title, body, kind="meeting",
                    series_id=series.id, presenter=presenter)
    page.entry_date = when
    for member in members:
        if member != me:
            s.add(NotebookShare(page_id_fk=page.id, username=member, role="edit", shared_by=me))
            with i18n.using(i18n.language_for(s, member)):
                day = i18n.strftime(when, "%d %b")
            notify.send(s, member, "Notes for %(meeting)s on %(day)s are open",
                        "Everyone in the meeting can write in them.", category="notebook",
                        link=page_url(page.id), actor=me, values={"meeting": series.name, "day": day},
                        message_values={})
    if members and advance:
        series.next_index = (series.next_index + 1) % len(members)
    record_edit(s, page)
    return page


@bp.post("/api/meetings/<int:series_id>/note")
def meeting_note(series_id: int):
    data = _json_body()
    with SessionLocal() as s:
        series = _series_or_404(s, series_id, need_edit=False)
        page = create_meeting_note(s, series, _date(data.get("date")), advance=data.get("advance", True) is not False)
        s.commit()
        return jsonify({"ok": True, "page_id": page.id, "url": page_url(page.id)})


@bp.post("/api/meetings/<int:series_id>/calendar")
def meeting_calendar(series_id: int):
    """Put the coming meetings on the calendar, each naming who presents."""
    count = min(max(_int(_json_body().get("count")) or 0, 1), 26)
    with SessionLocal() as s:
        series = _series_or_404(s, series_id)
        members = _members(series)
        if series.weekday is None:
            return _fail(gettext("Pick the day of the week the meeting is on first."))
        names = display_names(s, members)
        made = 0
        for i, day in enumerate(upcoming_dates(series, count)):
            presenter = members[(series.next_index + i) % len(members)] if members else ""
            title = series.name + (f" — {names.get(presenter, presenter)} presents" if presenter else "")
            exists = s.scalar(select(CalendarEvent.id).where(CalendarEvent.event_date == day,
                                                            CalendarEvent.title.like(series.name + "%"),
                                                            CalendarEvent.event_type == "meeting").limit(1))
            if exists:
                continue
            start = end = None
            if series.time:
                hh, mm = (int(x) for x in series.time.split(":"))
                start = datetime.combine(day, datetime.min.time()).replace(hour=hh, minute=mm)
                end = start + timedelta(hours=1)
            s.add(CalendarEvent(title=title[:200], event_date=day, start_at=start, end_at=end,
                                is_all_day=start is None, owner=_me(), event_type="meeting",
                                description=(series.location or "") + ("\n" if series.location else "")
                                + "Notes: " + url_for("nb.meeting_open", series_id=series.id, date=day.isoformat())))
            made += 1
        s.commit()
        return jsonify({"ok": True, "made": made})


@bp.get("/meetings/<int:series_id>/open")
def meeting_open(series_id: int):
    """From a calendar event: that day's notes, or new ones for it."""
    day = _date(request.args.get("date")) or date.today()
    with SessionLocal() as s:
        series = _series_or_404(s, series_id, need_edit=False)
        page_id = s.scalar(accessible_filter(
            select(NotebookPage.id).join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id)
            .join(NotebookPageInfo, NotebookPageInfo.page_id_fk == NotebookPage.id)
            .where(NotebookPageInfo.series_id == series.id, NotebookPage.entry_date == day)).limit(1))
        if page_id is None:
            page_id = create_meeting_note(s, series, day).id
            s.commit()
        return redirect(url_for("notebook", page=page_id))


def action_items(body: str, known: set[str]) -> list[dict]:
    items = []
    in_fence = False
    for line in (body or "").split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = TASK_RE.match(line)
        if not match:
            continue
        text = match.group(1)
        who = [m.group(1).rstrip(".") for m in MENTION_RE.finditer(text) if m.group(1).rstrip(".") in known]
        if not who:
            continue
        due = DUE_RE.search(text)
        items.append({"text": re.sub(r"\s+", " ", text).strip()[:200], "people": who,
                      "due": due.group(1) if due and _date(due.group(1)) else ""})
    return items


@bp.post("/api/pages/<int:page_id>/action-items")
def send_action_items(page_id: int):
    """Each open task naming @someone becomes their to-do on the calendar
    (once: sending again skips the ones already sent)."""
    with SessionLocal() as s:
        page, _role = load_page(s, page_id, need="edit")
        known = set(s.scalars(select(UserAccount.username).where(UserAccount.disabled.is_(False))).all())
        link = page_url(page.id)
        marker = f"From notebook page {page.id}"
        made, skipped = [], 0
        names = display_names(s, known)
        for item in action_items(page.body or "", known):
            for username in item["people"]:
                exists = s.scalar(select(TaskItem.id).where(TaskItem.owner == username,
                                                            TaskItem.title == item["text"],
                                                            TaskItem.notes.like(f"{marker}%")).limit(1))
                if exists:
                    skipped += 1
                    continue
                s.add(TaskItem(title=item["text"], due_date=_date(item["due"]), status="todo", priority="medium",
                               owner=username, notes=f"{marker}: “{page.title}” {link}"))
                notify.send(s, username, "Action item from “%(title)s”", item["text"],
                            category="notebook", link=link, actor=_me(), values={"title": page.title})
                made.append({"username": username, "name": names.get(username, username), "text": item["text"],
                             "due": item["due"]})
        s.commit()
        return jsonify({"ok": True, "made": made, "skipped": skipped})


# ---------------------------------------------------------------- recipes

def _recipe_json(r: NotebookRecipe, names: dict) -> dict:
    try:
        data = json.loads(r.data or "{}")
    except ValueError:
        data = {}
    return {"id": r.id, "name": r.name, "owner": r.owner, "owner_name": names.get(r.owner, r.owner),
            "data": data, "updated_at": _iso(r.updated_at),
            "can_edit": access.is_admin() or r.owner == _me()}


@bp.get("/api/recipes")
def recipes():
    with SessionLocal() as s:
        rows = s.scalars(select(NotebookRecipe).order_by(NotebookRecipe.name)).all()
        names = display_names(s, [r.owner for r in rows])
        return jsonify({"ok": True, "recipes": [_recipe_json(r, names) for r in rows],
                        "presets": [{"id": f"preset:{key}", "name": p["name"], "data": p} for key, p in PRESET_RECIPES.items()]})


def _clean_recipe(data) -> dict | None:
    if not isinstance(data, dict):
        return None
    components = data.get("components")
    if not isinstance(components, list):
        return None
    return {k: data[k] for k in ("name", "volume", "volumeUnit", "components", "notes", "ph") if k in data}


@bp.post("/api/recipes")
def recipe_save():
    body = _json_body()
    name = str(body.get("name") or "").strip()[:160]
    data = _clean_recipe(body.get("data"))
    if not name or data is None:
        return _fail(gettext("A recipe needs a name and components."))
    data["name"] = name
    with SessionLocal() as s:
        recipe = None
        if body.get("id"):
            recipe = s.get(NotebookRecipe, _int(body["id"]) or 0)
            if recipe is None:
                abort(404)
            if not (access.is_admin() or recipe.owner == _me()):
                abort(403)
        if recipe is None:
            recipe = NotebookRecipe(name=name, owner=_me())
            s.add(recipe)
        recipe.name, recipe.data, recipe.updated_at = name, json.dumps(data), _now()
        s.commit()
        return jsonify({"ok": True, "id": recipe.id})


@bp.post("/api/recipes/<int:recipe_id>/delete")
def recipe_delete(recipe_id: int):
    with SessionLocal() as s:
        recipe = s.get(NotebookRecipe, recipe_id)
        if recipe is None:
            abort(404)
        if not (access.is_admin() or recipe.owner == _me()):
            abort(403)
        s.delete(recipe)
        s.commit()
        return jsonify({"ok": True})


# ---------------------------------------------------------------- built-in starters and recipes

def _c(name, conc, unit, mw=None, stock=None, stock_unit=None, note=""):
    item = {"name": name, "conc": conc, "unit": unit}
    if mw:
        item["mw"] = mw
    if stock:
        item["stock"], item["stockUnit"] = stock, stock_unit or unit
    if note:
        item["note"] = note
    return item


PRESET_RECIPES = {
    "pbs10x": {"name": "PBS, 10×", "volume": 1, "volumeUnit": "L", "ph": "7.4", "components": [
        _c("NaCl", 1.37, "M", 58.44), _c("KCl", 27, "mM", 74.55),
        _c("Na₂HPO₄ (anhydrous)", 100, "mM", 141.96), _c("KH₂PO₄", 18, "mM", 136.09)],
        "notes": "Dilute 1:10 for 1× PBS. Autoclave or filter."},
    "tae50x": {"name": "TAE, 50×", "volume": 1, "volumeUnit": "L", "ph": "≈8.5", "components": [
        _c("Tris base", 2, "M", 121.14), _c("Glacial acetic acid", 5.71, "% v/v"),
        _c("EDTA pH 8.0", 50, "mM", stock=0.5, stock_unit="M")],
        "notes": "Do not adjust pH. Dilute 1:50 for 1× (40 mM Tris, 20 mM acetate, 1 mM EDTA)."},
    "tbe10x": {"name": "TBE, 10×", "volume": 1, "volumeUnit": "L", "ph": "≈8.3", "components": [
        _c("Tris base", 890, "mM", 121.14), _c("Boric acid", 890, "mM", 61.83),
        _c("EDTA pH 8.0", 20, "mM", stock=0.5, stock_unit="M")],
        "notes": "Precipitates on long storage; keep at room temperature."},
    "te": {"name": "TE, pH 8.0", "volume": 100, "volumeUnit": "mL", "ph": "8.0", "components": [
        _c("Tris-HCl pH 8.0", 10, "mM", stock=1, stock_unit="M"),
        _c("EDTA pH 8.0", 1, "mM", stock=0.5, stock_unit="M")], "notes": ""},
    "tbs10x": {"name": "TBS, 10×", "volume": 1, "volumeUnit": "L", "ph": "7.6", "components": [
        _c("Tris base", 200, "mM", 121.14), _c("NaCl", 1.5, "M", 58.44)],
        "notes": "Adjust to pH 7.6 with HCl."},
    "tbst": {"name": "TBST (1× TBS, 0.1% Tween-20)", "volume": 1, "volumeUnit": "L", "components": [
        _c("TBS", 1, "X", stock=10, stock_unit="X"), _c("Tween-20", 0.1, "% v/v")], "notes": ""},
    "lb": {"name": "LB broth (Miller)", "volume": 1, "volumeUnit": "L", "ph": "7.0", "components": [
        _c("Tryptone", 10, "g/L"), _c("Yeast extract", 5, "g/L"), _c("NaCl", 10, "g/L")],
        "notes": "Add 15 g/L agar for plates. Autoclave 20 min at 121 °C."},
    "sds_running10x": {"name": "SDS-PAGE running buffer, 10×", "volume": 1, "volumeUnit": "L", "components": [
        _c("Tris base", 250, "mM", 121.14), _c("Glycine", 1.92, "M", 75.07), _c("SDS", 1, "% w/v")],
        "notes": "pH ≈8.3; do not adjust."},
    "transfer1x": {"name": "Transfer buffer (Towbin), 1×", "volume": 1, "volumeUnit": "L", "components": [
        _c("Tris base", 25, "mM", 121.14), _c("Glycine", 192, "mM", 75.07), _c("Methanol", 20, "% v/v")],
        "notes": "Chill before use."},
    "laemmli4x": {"name": "Laemmli sample buffer, 4×", "volume": 10, "volumeUnit": "mL", "components": [
        _c("Tris-HCl pH 6.8", 250, "mM", stock=1, stock_unit="M"), _c("SDS", 8, "% w/v"),
        _c("Glycerol", 40, "% v/v"), _c("Bromophenol blue", 0.02, "% w/v"),
        _c("β-mercaptoethanol", 10, "% v/v", note="add fresh")], "notes": ""},
    "ripa": {"name": "RIPA lysis buffer", "volume": 50, "volumeUnit": "mL", "components": [
        _c("Tris-HCl pH 8.0", 50, "mM", stock=1, stock_unit="M"),
        _c("NaCl", 150, "mM", stock=5, stock_unit="M"), _c("NP-40", 1, "% v/v"),
        _c("Sodium deoxycholate", 0.5, "% w/v"), _c("SDS", 0.1, "% w/v"),
        _c("EDTA pH 8.0", 1, "mM", stock=0.5, stock_unit="M")],
        "notes": "Add protease and phosphatase inhibitors just before use. Keep on ice."},
    "tris1m": {"name": "Tris-HCl, 1 M", "volume": 500, "volumeUnit": "mL", "components": [
        _c("Tris base", 1, "M", 121.14)], "notes": "Adjust pH with concentrated HCl at room temperature."},
    "edta05m": {"name": "EDTA, 0.5 M, pH 8.0", "volume": 500, "volumeUnit": "mL", "ph": "8.0", "components": [
        _c("EDTA disodium dihydrate", 0.5, "M", 372.24)],
        "notes": "Dissolves only near pH 8: add NaOH pellets (about 10 g per 500 mL) while stirring."},
    "nacl5m": {"name": "NaCl, 5 M", "volume": 500, "volumeUnit": "mL", "components": [
        _c("NaCl", 5, "M", 58.44)], "notes": ""},
}


STARTERS = {
    "blank": {"title": "Untitled page", "kind": "note", "icon": "file", "hint": "An empty page", "body": ""},
    "experiment": {
        "title": "New experiment", "kind": "experiment", "icon": "flask",
        "hint": "Aim, setup, materials with lots, steps, results",
        "body": """## Aim

## Hypothesis

## Setup

- **Samples / animals:**
- **Conditions:**
- **Controls:**

## Samples & materials

| Reagent / sample | Vendor | Cat # | Lot | Amount |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |

## Procedure

- [ ] First step

## Observations

## Results

## Conclusion & next steps
"""},
    "protocol": {
        "title": "New protocol", "kind": "protocol", "icon": "list-check",
        "hint": "Reusable steps; start an experiment from it",
        "body": """## Purpose

## Materials

- Reagent (vendor, cat #)

## Solutions

## Steps

1. First step
2. Incubate 30 min at 37 °C
3. Next step

## Notes & troubleshooting

## References
"""},
    "meeting": {
        "title": "Lab meeting", "kind": "meeting", "icon": "users",
        "hint": "Agenda, notes, decisions and action items",
        "body": """**Date:** {date} · **Presenter:** {presenter}

**Attendees:**

## Agenda

1. Updates

## Notes

## Decisions

## Action items

- [ ] @name what to do, due YYYY-MM-DD
"""},
    "seminar": {
        "title": "Seminar notes", "kind": "seminar", "icon": "note",
        "hint": "Speaker, key points, ideas for our work",
        "body": """**Speaker:**  · **Affiliation:**  · **Date:** {date}

## Talk title

## Key points

- Main finding

## Methods worth trying

## Questions asked

## Ideas for our work

```mindmap
- Talk
  - Question
  - Approach
  - Findings
  - Open questions
```
"""},
    "daily": {
        "title": "Daily log", "kind": "daily", "icon": "calendar",
        "hint": "Today's plan and a timestamped log",
        "body": """## Plan

- [ ] What to get done today

## Log

"""},
    "cloning": {
        "title": "Cloning", "kind": "experiment", "icon": "dna",
        "hint": "Digest, ligation calculator, transformation",
        "body": """## Construct

- **Vector:** @plasmid
- **Insert:**
- **Enzymes:**

## Digest

| Component | Volume (µL) |
| --- | --- |
| DNA |  |
| Buffer (10×) | 5 |
| Enzyme 1 | 1 |
| Enzyme 2 | 1 |
| Water | to 50 |

- [ ] Digest 1 h at 37 °C
- [ ] Gel purify

## Ligation

```calc
{"type":"ligation","vectorNg":50,"vectorBp":5000,"insertBp":1000,"ratio":3}
```

- [ ] Ligate 10 min at room temperature

## Transformation

- [ ] Thaw competent cells 10 min on ice
- [ ] Heat shock 45 s at 42 °C
- [ ] Recover 1 h at 37 °C, plate

## Colonies & screening
"""},
    "western": {
        "title": "Western blot", "kind": "experiment", "icon": "flask",
        "hint": "Lysis, gel, transfer, antibodies, image",
        "body": """## Samples

| Lane | Sample | µg protein |
| --- | --- | --- |
| 1 | Ladder |  |
| 2 |  |  |

## Buffers

```recipe
{"name":"RIPA lysis buffer","volume":50,"volumeUnit":"mL","components":[{"name":"Tris-HCl pH 8.0","conc":50,"unit":"mM","stock":1,"stockUnit":"M"},{"name":"NaCl","conc":150,"unit":"mM","stock":5,"stockUnit":"M"},{"name":"NP-40","conc":1,"unit":"% v/v"},{"name":"Sodium deoxycholate","conc":0.5,"unit":"% w/v"},{"name":"SDS","conc":0.1,"unit":"% w/v"}],"notes":"Add inhibitors fresh."}
```

## Steps

- [ ] Lyse 30 min on ice, spin 15 min at 4 °C
- [ ] Run gel 90 min at 120 V
- [ ] Transfer 60 min at 100 V
- [ ] Block 1 h in 5% milk/TBST
- [ ] Primary antibody overnight at 4 °C
- [ ] Wash 3 × 10 min TBST
- [ ] Secondary antibody 1 h at room temperature

## Antibodies

| Antibody | Host | Dilution | Lot |
| --- | --- | --- | --- |
|  |  |  |  |

## Image
"""},
    "qpcr": {
        "title": "qPCR", "kind": "experiment", "icon": "chart",
        "hint": "Master mix, plate layout, ΔΔCt analysis",
        "body": """## Samples & targets

## Master mix

```calc
{"type":"mastermix","reactions":24,"overage":10,"components":[{"name":"2× SYBR mix","perRxn":5},{"name":"Primer mix (10 µM)","perRxn":0.5},{"name":"Water","perRxn":2.5}],"template":2}
```

## Run

- [ ] Plate, seal, spin 1 min
- [ ] Run: 95 °C 2 min; 40 × (95 °C 15 s, 60 °C 60 s); melt curve

## Analysis

```qpcr
{"rows":[],"reference":"","control":""}
```
"""},
}
