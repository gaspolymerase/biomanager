"""Settings → Lab: what its panes show (templates/settings.html).

Statistics (the lab's animals, who keeps them, how full the racks are and
what is coming up), General, Databases and What members may do (the lab's
setup, app/lab.py), People & access (accounts, guests, project groups and
what each group's members may do, app/groups.py) and History (bulk changes
and every change). Everyone sees them; only admins (and a group's leads,
for their group) change them.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import g
from sqlalchemy import func, select

from . import access, groups, lab


def _builtin_label(session, key: str, default: str) -> str:
    from .inventory_service import builtin_labels
    return builtin_labels(session).get(key, default)


# ---------------------------------------------------------------- Statistics

def statistics(session) -> dict:
    """The lab in numbers: one total per animal database that is on, each
    split by person; the racks and boxes, how full; what is coming up."""
    from .models import CageRecord, InventoryItem, MouseRecord, PlasmidRecord, StockModule, StockUnit, TankRecord
    on = lab.features_on(session)
    animals: list[dict] = []          # {key, label, unit, total, sub, people: [(username, n)]}

    if on.get("colony"):
        alive = MouseRecord.date_of_death.is_(None)
        people = session.execute(select(MouseRecord.owner, func.count(MouseRecord.id)).where(alive)
                                 .group_by(MouseRecord.owner)).all()
        cages = session.scalar(select(func.count(func.distinct(MouseRecord.cage_id_fk)))
                               .where(alive, MouseRecord.cage_id_fk.is_not(None))) or 0
        animals.append({"key": "mice", "label": "Mice", "unit": "living mice",
                        "total": sum(n for _w, n in people), "cages": cages, "people": people})
    if on.get("zebrafish"):
        people = session.execute(select(TankRecord.owner, func.count(TankRecord.id))
                                 .where(TankRecord.active.is_(True)).group_by(TankRecord.owner)).all()
        animals.append({"key": "fish", "label": "Fish tanks", "unit": "fish tanks",
                        "total": sum(n for _w, n in people), "people": people})
    for kind, label, unit in (("fly", "Fly vials", "fly vials"), ("worm", "Worm plates", "worm plates")):
        modules = [m.id for m in session.scalars(select(StockModule).where(
            StockModule.kind == kind, StockModule.enabled.is_(True)))]
        if not modules:
            continue
        people = session.execute(select(StockUnit.owner, func.count(StockUnit.id)).where(
            StockUnit.module_id_fk.in_(modules), StockUnit.active.is_(True)).group_by(StockUnit.owner)).all()
        animals.append({"key": kind, "label": label, "unit": unit,
                        "total": sum(n for _w, n in people), "people": people})
    from .models import UserAccount
    shown = {u.username: u.display_name or u.username for u in session.scalars(select(UserAccount))}
    for a in animals:
        a["people"] = sorted(((who or "", shown.get(who or "", who or ""), n) for who, n in a["people"] if n),
                             key=lambda p: (-p[2], p[1].lower()))
        a["max"] = max([n for _w, _name, n in a["people"]] or [1])

    coming: list[tuple[str, int]] = []
    today = date.today()
    if on.get("colony"):
        from .services import weaning_due
        coming.append(("Litters to wean in the next 7 days", len(weaning_due(session, today, today + timedelta(days=7)))))
        coming.append(("Pups waiting to be genotyped", session.scalar(select(func.count(MouseRecord.id)).where(
            MouseRecord.status == "geno", MouseRecord.date_of_death.is_(None))) or 0))
    expiring = session.scalar(select(func.count(InventoryItem.id)).where(
        InventoryItem.expires_on.is_not(None), InventoryItem.expires_on >= today,
        InventoryItem.expires_on <= today + timedelta(days=30))) or 0
    if expiring:
        coming.append(("Inventory items that expire in the next 30 days", expiring))
    records = []
    if on.get("plasmids"):
        records.append((_builtin_label(session, "plasmids", "Plasmids"),
                        session.scalar(select(func.count(PlasmidRecord.id))) or 0))
    if on.get("colony"):
        records.append(("Cages", session.scalar(select(func.count(CageRecord.id))) or 0))
    return {"animals": animals, "racks": racks(session), "coming": coming, "records": records}


def racks(session) -> list[dict]:
    """Every rack, box and incubator by kind: how full, and who may change
    it (its creator; nobody: admins only), as Racks & boxes listed them."""
    from . import positions
    from .admin_racks import CONTAINERS
    out = []
    for kind in CONTAINERS:
        rows = []
        for row in sorted(session.scalars(select(kind.model)), key=lambda r: positions.place_order(r.name)):
            places = (getattr(row, "rows", None) or 0) * (getattr(row, "cols", None) or 0)
            used = kind.count(session, row)
            rows.append({"id": row.id, "name": row.name, "database": kind.database(session, row),
                         "where": kind.where(session, row), "count": used, "places": places,
                         "full": min(100, round(100 * used / places)) if places else None,
                         "creator": (row.created_by or "").strip()})
        if rows:
            out.append({"kind": kind, "rows": rows, "unassigned": sum(1 for r in rows if not r["creator"])})
    return out


# ---------------------------------------------------------------- People & access

def people(session) -> dict:
    """Accounts (waiting, members, guests) for People & access."""
    from .guests import is_active
    from .models import GuestPass, UserAccount
    now = datetime.utcnow()
    users = list(session.scalars(select(UserAccount).order_by(UserAccount.display_name, UserAccount.username)))
    passes = {p.user_id_fk: p for p in session.scalars(select(GuestPass).order_by(GuestPass.created_at))}
    waiting, members, guests = [], [], []
    for u in users:
        row = {"id": u.id, "username": u.username, "name": u.display_name or u.username, "job": u.role_title or "",
               "role": u.role, "disabled": bool(u.disabled), "email": u.email or "",
               "me": u.id == g.user.id}
        if u.role == "pending":
            row["since"] = u.created_at
            waiting.append(row)
        elif u.expires_at is not None:
            gp = passes.get(u.id)
            row.update({"pass_id": gp.id if gp else None, "until": u.expires_at,
                        "active": bool(gp and is_active(gp, now)) and u.expires_at > now})
            guests.append(row)
        else:
            members.append(row)
    members.sort(key=lambda r: (r["disabled"], r["role"] != "admin", r["name"].lower()))
    guests.sort(key=lambda r: (not r["active"], r["name"].lower()))
    return {"waiting": waiting, "members": members, "guests": guests,
            "admins": sum(1 for m in members if m["role"] == "admin" and not m["disabled"])}


def project_groups(session) -> list[dict]:
    """Each group: its people and their part, its switches, what is shared
    with it, and whether this person may change it."""
    from . import models
    from .models import LabGroup, NotebookShare, UserAccount
    shown = {u.username: u.display_name or u.username for u in session.scalars(select(UserAccount))}
    out = []
    for group in session.scalars(select(LabGroup).order_by(LabGroup.name)):
        people = []
        for m in group.members:
            part = "lead" if m.lead else ("member" if m.can_edit is not False else "viewer")
            people.append({"username": m.username, "name": shown.get(m.username, m.username), "part": part})
        people.sort(key=lambda p: (p["part"] != "lead", p["name"].lower()))
        shared = []
        for table, noun in (("CageRecord", "cages"), ("TankRecord", "tanks"), ("StockUnit", "vials and plates"),
                            ("PlasmidRecord", "plasmids"), ("InventoryItem", "stock items"), ("TaskItem", "to-dos")):
            model = getattr(models, table)
            n = session.scalar(select(func.count(model.id)).where(
                model.share_group_id == group.id, model.is_shared.is_(True))) if hasattr(model, "is_shared") \
                else session.scalar(select(func.count(model.id)).where(model.share_group_id == group.id))
            if n:
                shared.append((n, noun))
        pages = session.scalar(select(func.count(NotebookShare.id)).where(
            NotebookShare.username == f"{groups.PAGE_SHARE_PREFIX}{group.id}")) or 0
        if pages:
            shared.append((pages, "notebook pages"))
        databases = [m["label"] for m in lab.custom_databases(session)
                     if m.get("share_group_id") == group.id and not m.get("private_to")]
        out.append({"id": group.id, "name": group.name, "description": group.description or "",
                    "people": people, "leads": [p["name"] for p in people if p["part"] == "lead"],
                    "switches": {key: bool(getattr(group, column)) for key, (column, *_r) in groups.SWITCHES.items()},
                    "shared": shared, "databases": databases,
                    "mine": groups.in_group(group.id), "manage": groups.can_manage(group.id),
                    "my_part": groups.part_of(group.id)})
    return out


# ---------------------------------------------------------------- History

def history(session) -> dict:
    """The latest bulk changes (yours; an admin's: everyone's) with Undo,
    and for an admin the latest tracked changes."""
    from . import undo as undo_service
    from .models import AuditEntry
    admin = access.is_admin()
    batches = []
    for row in undo_service.recent(session, limit=12, actor=None if admin else g.user.username):
        summary = undo_service.describe(session, row)
        batches.append({"id": row.id, "description": row.description, "actor": row.actor,
                        "count": row.record_count or summary["entries"], "created_at": row.created_at,
                        "undone_at": row.undone_at, "blocked": summary.get("blocked"),
                        "can_undo": not row.is_undone and (row.actor == g.user.username or admin)})
    changes = []
    if admin:
        for e in session.scalars(select(AuditEntry).order_by(AuditEntry.changed_at.desc()).limit(15)):
            changes.append({"action": e.action, "table": e.table_name, "label": e.record_label or f"#{e.record_id}",
                            "by": e.changed_by, "at": e.changed_at})
    return {"batches": batches, "changes": changes}


def panes(session) -> dict:
    """Everything the Lab panes show."""
    state = lab.survey_state(session)
    return {
        "stats": statistics(session),
        "state": state,
        "custom": lab.custom_databases(session),
        "people": people(session),
        "groups": project_groups(session),
        "history": history(session),
    }
