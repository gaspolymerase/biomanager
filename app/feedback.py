"""Send feedback, and the lab's usage report: what a pilot needs.

- **Send feedback** (the rail, under Help): anyone says what went wrong,
  an idea or a question. It is kept in the lab's own database, where the
  lab's admins read it and mark it done. Nothing leaves the lab unless
  someone presses **Open as a GitHub issue**, which opens a new issue on
  BioManager's repository with the text filled in (the page's
  path, the version and the browser, never the server's address or who
  sent it) for them to check and submit themselves.
- **Usage report** (admins): the last eight weeks in counts only — how
  many people changed something, how many changes in each area, notebook
  pages and calendar events added — and what is in the lab now. No names,
  no record text: an admin can copy it to whoever runs the pilot. The page
  also shows, and switches, the anonymous counts sent once a day to
  BioManager's makers (app/telemetry.py).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlparse

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select

from . import access
from .db import SessionLocal, engine
from .models import AuditEntry, Feedback

bp = Blueprint("feedback", __name__, url_prefix="/feedback")

ISSUES_URL = "https://github.com/gaspolymerase/biomanager/issues/new"
KINDS = {
    "problem": "Something went wrong",
    "idea": "An idea",
    "question": "A question",
}
WEEKS = 8

# The areas of the app, by the tables the change history records.
AREAS = [
    ("Mouse colony", ("mice", "mouse_cages", "litters", "strains", "mouse_racks")),
    ("Experiments", ("experiment",)),
    ("Zebrafish", ("tanks", "fish", "clutches", "water_systems")),
    ("Flies and worms", ("stock_",)),
    ("Other organisms", ("organism",)),
    ("Plasmids", ("plasmid",)),
    ("Inventories", ("inventory_", "orders", "samples")),
    ("Signatures", ("record_signatures",)),
]


@bp.before_request
def require_login():
    if g.get("user") is None:
        return redirect(url_for("login", next=request.path))
    return None


def app_version() -> str:
    try:
        from desktop_updates import version
        return version()
    except ImportError:
        try:
            return (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip() or "server"
        except OSError:
            return "server"


def _platform() -> str:
    from flask import current_app
    where = "desktop app" if current_app.config.get("LOCAL_SETUP") or engine.dialect.name == "sqlite" else "lab server"
    agent = request.user_agent.string or ""
    browser = next((name for key, name in (("BioManagerIOS", "iPhone app"), ("BioManagerAndroid", "Android app"),
                                           ("Edg/", "Edge"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                                           ("Safari/", "Safari")) if key in agent), "a browser")
    system = next((name for key, name in (("iPhone", "iOS"), ("iPad", "iPadOS"), ("Android", "Android"),
                                          ("Mac OS X", "macOS"), ("Windows", "Windows"), ("Linux", "Linux"))
                   if key in agent), "")
    return f"{where} · {browser}" + (f" on {system}" if system else "") + f" · {engine.dialect.name}"


def issue_url(item: Feedback) -> str:
    """A new GitHub issue with the report filled in, for the person to check and submit."""
    title = f"{KINDS.get(item.kind, 'Feedback')}: {' '.join(item.text.split())[:70]}"
    body = "\n".join([
        item.text.strip(),
        "",
        "---",
        f"- Kind: {KINDS.get(item.kind, item.kind)}",
        f"- Page: `{item.page or '—'}`",
        f"- Version: {item.app_version or '—'}",
        f"- Where: {item.platform or '—'}",
    ])
    return ISSUES_URL + "?" + urlencode({"title": title, "body": body})


def _from_page() -> str:
    """The page someone came from, as a path on this server (never its address)."""
    raw = request.values.get("from") or request.referrer or ""
    parts = urlparse(raw)
    if parts.netloc and parts.netloc != request.host:
        return ""
    path = parts.path or ""
    if path.startswith("/feedback"):
        return ""
    return (path + (f"?{parts.query}" if parts.query else ""))[:300]


@bp.get("")
def index():
    with SessionLocal() as s:
        admin = access.is_admin()
        stmt = select(Feedback).order_by(Feedback.created_at.desc()).limit(200)
        if not admin:
            stmt = stmt.where(Feedback.username == g.user.username)
        items = s.scalars(stmt).all()
        sent = s.get(Feedback, request.args.get("sent", type=int) or 0)
        if sent is not None and sent.username != g.user.username:
            sent = None
        return render_template("feedback.html", items=items, kinds=KINDS, admin=admin, sent=sent,
                               issue_url=issue_url, from_page=_from_page(), open_count=sum(i.status == "open" for i in items))


@bp.post("")
def send():
    text = (request.form.get("text") or "").strip()
    kind = request.form.get("kind") if request.form.get("kind") in KINDS else "problem"
    if len(text) < 3:
        flash("Say what happened, or what you'd like.", "error")
        return redirect(url_for("feedback.index"))
    with SessionLocal() as s:
        item = Feedback(username=g.user.username, kind=kind, text=text[:5000], page=_from_page(),
                        app_version=app_version(), platform=_platform()[:200])
        s.add(item)
        s.commit()
        admins = [u for u in _admin_usernames(s) if u != g.user.username]
        if admins:
            from . import notify
            for name in admins:
                notify.send(s, name, f"{g.user.display_name or g.user.username} sent feedback", text[:300],
                            category="lab", link=url_for("feedback.index"), actor=g.user.username)
            s.commit()
        return redirect(url_for("feedback.index", sent=item.id))


def _admin_usernames(session) -> list[str]:
    from .models import UserAccount
    return list(session.scalars(select(UserAccount.username).where(
        UserAccount.role == "admin", UserAccount.disabled.is_(False))))


@bp.post("/<int:item_id>/status")
def set_status(item_id: int):
    if not access.is_admin():
        abort(403)
    with SessionLocal() as s:
        item = s.get(Feedback, item_id)
        if item is None:
            abort(404)
        item.status = "done" if request.form.get("status") == "done" else "open"
        s.commit()
    return redirect(url_for("feedback.index"))


# ---- the usage report

def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _area(table: str) -> str | None:
    for name, prefixes in AREAS:
        if any(table == p or table.startswith(p) for p in prefixes):
            return name
    return None


def usage(session, today: date | None = None) -> dict:
    """Counts only: per week, the people who changed something and the
    changes in each area; and what the lab holds now."""
    from .models import (CageRecord, CalendarEvent, Experiment, InventoryItem, MouseRecord, NotebookPage,
                         RecordSignature, StockUnit, TankRecord, UserAccount)
    today = today or date.today()
    first = _week_start(today) - timedelta(weeks=WEEKS - 1)
    weeks = [first + timedelta(weeks=i) for i in range(WEEKS)]
    since = datetime.combine(first, datetime.min.time())
    people = set(session.scalars(select(UserAccount.username)))   # not "system", nor a guest
    rows = {w: {"people": set(), "areas": {name: 0 for name, _ in AREAS}, "pages": 0, "events": 0} for w in weeks}
    for table, who, when in session.execute(select(AuditEntry.table_name, AuditEntry.changed_by, AuditEntry.changed_at)
                                            .where(AuditEntry.changed_at >= since)):
        week = rows.get(_week_start(when.date()))
        area = _area(table)
        if week is None or area is None:
            continue
        week["areas"][area] += 1
        if who in people:
            week["people"].add(who)
    for (when,) in session.execute(select(NotebookPage.created_at).where(NotebookPage.created_at >= since)):
        week = rows.get(_week_start(when.date())) if when else None
        if week is not None:
            week["pages"] += 1
    for (when,) in session.execute(select(CalendarEvent.created_at).where(CalendarEvent.created_at >= since)):
        week = rows.get(_week_start(when.date())) if when else None
        if week is not None:
            week["events"] += 1
    count = lambda stmt: session.scalar(stmt) or 0  # noqa: E731
    active_users = select(func.count(UserAccount.id)).where(UserAccount.disabled.is_(False))
    now = [
        ("Members", count(active_users)),
        ("Mice alive", count(select(func.count(MouseRecord.id)).where(MouseRecord.date_of_death.is_(None)))),
        ("Cages", count(select(func.count(CageRecord.id)))),
        ("Tanks in use", count(select(func.count(TankRecord.id)).where(TankRecord.active.is_(True)))),
        ("Vials in use", count(select(func.count(StockUnit.id)).where(StockUnit.active.is_(True)))),
        ("Inventory items", count(select(func.count(InventoryItem.id)))),
        ("Experiments", count(select(func.count(Experiment.id)))),
        ("Notebook pages", count(select(func.count(NotebookPage.id)))),
        ("Signed pages", count(select(func.count(func.distinct(RecordSignature.page_id_fk))))),
        ("Feedback notes", count(select(func.count(Feedback.id)))),
    ]
    table = [{"week": w, "people": len(rows[w]["people"]), "areas": rows[w]["areas"],
              "pages": rows[w]["pages"], "events": rows[w]["events"]} for w in weeks]
    return {"weeks": table, "areas": [name for name, _ in AREAS], "now": now,
            "people_8w": len(set().union(*(rows[w]["people"] for w in weeks))),
            "version": app_version(), "database": engine.dialect.name, "made_on": today}


def usage_text(report: dict) -> str:
    """The report as plain text, to paste into an email."""
    areas = report["areas"]
    lines = [f"BioManager usage report · {report['made_on'].isoformat()}",
             f"Version {report['version']} · {report['database']}",
             f"People who changed something in the last {WEEKS} weeks: {report['people_8w']}",
             "",
             "Week of     People  " + "  ".join(areas) + "  Notebook pages  Calendar events"]
    for row in report["weeks"]:
        cells = [f"{row['areas'][a]:>{len(a)}}" for a in areas]
        lines.append(f"{row['week'].isoformat()}  {row['people']:>6}  " + "  ".join(cells)
                     + f"  {row['pages']:>14}  {row['events']:>15}")
    lines += ["", "Now: " + " · ".join(f"{name} {value:,}" for name, value in report["now"])]
    return "\n".join(lines) + "\n"


@bp.get("/usage")
def usage_report():
    if not access.is_admin():
        abort(403)
    from . import telemetry
    with SessionLocal() as s:
        report = usage(s)
        heartbeat = telemetry.status(s)
        body = telemetry.payload(s)
    body["api_key"] = body["api_key"] or "(none in this build)"
    return render_template("feedback_usage.html", report=report, text=usage_text(report), weeks=WEEKS,
                           heartbeat=heartbeat, heartbeat_json=json.dumps(body, indent=2))


@bp.post("/usage/heartbeat")
def set_heartbeat():
    """The lab-wide switch for the anonymous daily counts (app/telemetry.py)."""
    if not access.is_admin():
        abort(403)
    from . import telemetry
    on = request.form.get("enabled") == "1"
    with SessionLocal() as s:
        telemetry.set_lab_on(s, on)
        s.commit()
    flash("Anonymous counts switched on: sent once a day." if on
          else "Anonymous counts switched off: nothing is sent.", "success")
    return redirect(url_for("feedback.usage_report") + "#heartbeat")
