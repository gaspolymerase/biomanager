"""A look inside a database from the sidebar, without leaving the page.

Hovering a database in the sidebar (static/rail-peek.js) asks
/rail/peek?key=<its sidebar key> for a small card: its count as Home's
Counts tile has it, the next few things due in it from Home's agenda, the
person's own latest records, and its tabs. Only what Home already shows
this person, so nothing here is new to them.
"""
from __future__ import annotations

from datetime import date

from flask import Blueprint, abort, g, jsonify, request, url_for
from sqlalchemy import func, select

from . import home_layouts, inventory_service, lab
from .db import SessionLocal
from .i18n import gettext, translate_value
from .models import MouseRecord, PlasmidRecord, TankRecord

bp = Blueprint("rail_peek", __name__)

DUE_ROWS = 4
RECENT_ROWS = 3


def _tabs(kind: str) -> list[dict]:
    """The database's own tabs, for going straight to one."""
    if kind == "colony":
        return [{"label": gettext(label), "url": url_for("colony", view=view)} for view, label in (
            ("mice", "Mice"), ("cages", "Cages"), ("litters", "Litters"), ("breeders", "Breeders"),
            ("experiments", "Experiments"), ("strains", "Strains"))]
    if kind == "zebrafish":
        return [{"label": gettext(label), "url": url_for("zebrafish", view=view)} for view, label in (
            ("tanks", "Tanks"), ("fish", "Fish"), ("clutches", "Clutches"),
            ("experiments", "Experiments"), ("lines", "Lines"))]
    if kind == "plasmids":
        with SessionLocal() as s:
            tabs = sorted((m for m in inventory_service.list_modules(s)
                           if m.kind in inventory_service.PLASMID_TAB_KINDS and lab.in_sidebar(m)),
                          key=lambda m: (inventory_service.PLASMID_TAB_KINDS.index(m.kind), m.position))
            return ([{"label": gettext("Plasmids"), "url": url_for("plasmids")}]
                    + [{"label": translate_value(m.label), "url": url_for("inventory.module", key=m.key)}
                       for m in tabs])
    return []


def _recent(session, kind: str, username: str) -> list[dict]:
    """This person's own latest records: theirs, so never one they may not see."""
    if kind == "colony":
        mice = session.scalars(select(MouseRecord).where(MouseRecord.owner == username)
                               .order_by(MouseRecord.id.desc()).limit(RECENT_ROWS)).all()
        return [{"title": f"#{m.mouse_id}",
                 "detail": " · ".join(x for x in (m.gender or "", m.genotype or "",
                                                   m.cage.cage_id if m.cage else "") if x)}
                for m in mice]
    if kind == "zebrafish":
        tanks = session.scalars(select(TankRecord).where(TankRecord.owner == username, TankRecord.active.is_(True))
                                .order_by(TankRecord.id.desc()).limit(RECENT_ROWS)).all()
        return [{"title": t.tank_id, "detail": t.line.name if t.line else ""} for t in tanks]
    if kind == "plasmids":
        plasmids = session.scalars(select(PlasmidRecord).where(PlasmidRecord.owner == username)
                                   .order_by(PlasmidRecord.id.desc()).limit(RECENT_ROWS)).all()
        return [{"title": f"#{p.plasmid_id}", "detail": p.name or ""} for p in plasmids]
    return []


def _tracks(db: dict) -> tuple[str, ...]:
    """The agenda's tracks (home_layouts.build_agenda) that are this database's."""
    kind, module = db["kind"], db["module"]
    if kind == "colony":
        return ("colony:age", "colony:wean", "colony:geno")
    if kind == "zebrafish":
        return ("zebrafish",)
    if kind == "stock":
        from . import stock_service
        return (f"stock:{stock_service.view(module).key}",)
    if kind == "organism":
        return (f"org:{module.key}",)
    return ()


@bp.route("/rail/peek")
def peek():
    if g.get("user") is None:
        abort(401)
    key = request.args.get("key", "")
    today = date.today()
    features = lab.request_features()
    with SessionLocal() as session:
        db = next((d for d in home_layouts.lab_databases(session, features) if d["key"] == key), None)
        if db is None:
            abort(404)
        username = g.user.username
        counts = {"total_mice": 0, "active_mice": 0, "my_mice": 0, "notebook_pages": 0}
        if db["kind"] == "colony":
            from .services import mouse_is_active
            counts["total_mice"] = session.scalar(select(func.count(MouseRecord.id))) or 0
            counts["active_mice"] = sum(1 for m in session.scalars(
                select(MouseRecord).where(MouseRecord.date_of_death.is_(None))) if mouse_is_active(m))
            counts["my_mice"] = session.scalar(
                select(func.count(MouseRecord.id)).where(MouseRecord.owner == username)) or 0

        tracks = _tracks(db)
        due = []
        if tracks:
            zebrafish = None
            if db["kind"] == "zebrafish":
                from .zebrafish_routes import zebrafish_home_summary
                zebrafish = zebrafish_home_summary()
            agenda, _meta = home_layouts.build_agenda(session, today, home_layouts.PULL_DAYS, features, zebrafish)
            due = [{"title": item["title"], "when": home_layouts._when(item["due"], today),
                    "overdue": item["status"] == "overdue", "url": item["url"] or ""}
                   for item in agenda if item["track"] in tracks][:DUE_ROWS]
        tile = home_layouts.count_tiles(session, [db], username, _labels(session), counts,
                                        {key: len(due)}, False)[0]
        recent = _recent(session, db["kind"], username)
        session.commit()
    return jsonify({
        "key": key, "label": tile["label"], "icon": tile["icon"], "url": tile["url"],
        "value": tile["value"], "sub": tile["sub"],
        "due": due, "recent": recent, "tabs": _tabs(db["kind"]),
        "words": {"due": gettext("Due soon"), "recent": gettext("Yours, lately"),
                  "nothing_due": gettext("Nothing due soon"), "open": gettext("Open")},
    })


def _labels(session) -> dict:
    return inventory_service.builtin_labels(session)
