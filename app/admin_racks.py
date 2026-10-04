"""Admin: who looks after each rack, box and incubator.

Racks, boxes and incubators can be resized, renamed or deleted only by an
admin or whoever made them (access.can_edit_rack). Ones made before
creators were recorded have none and are admin-only; this page lets an
admin hand any of them to a lab member, one at a time or all the
unassigned ones of a database at once. Every change is one undoable batch.

Adding a container kind is one entry in CONTAINERS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select

from . import access, audit, positions
from .db import SessionLocal
from .i18n import gettext
from .models import (
    CageRecord, FishRack, InventoryItem, InventoryModule, InventoryRack, MouseRack, OrganismModule, OrgHousing,
    OrgLocation, PlasmidBox, PlasmidRecord, StockIncubator, StockModule, StockRack, StockUnit, TankRecord,
    UserAccount,
)

bp = Blueprint("admin_racks", __name__, url_prefix="/admin/racks")


@dataclass(frozen=True)
class Kind:
    key: str
    label: str            # "Mouse racks"
    noun: str             # "rack"
    model: type
    table: str
    database: Callable    # (session, row) -> database name
    where: Callable       # (session, row) -> location text
    count: Callable       # (session, row) -> how much is in it
    count_noun: str


def _builtin_label(session, key: str, default: str) -> str:
    from .inventory_service import builtin_labels
    return builtin_labels(session).get(key, default)


def _colony_label(session, row) -> str:
    return _builtin_label(session, "colony", "Mouse colony")


def _module_label(model):
    def label(session, row):
        module = session.get(model, row.module_id_fk)
        return module.label if module else "—"
    return label


CONTAINERS: tuple[Kind, ...] = (
    Kind("mouse_rack", "Mouse racks", "rack", MouseRack, "mouse_racks", _colony_label,
         lambda s, r: r.room or "",
         lambda s, r: s.scalar(select(func.count(CageRecord.id)).where(CageRecord.rack_id_fk == r.id)) or 0,
         "cages"),
    Kind("fish_rack", "Fish racks", "rack", FishRack, "fish_racks",
         lambda s, r: _builtin_label(s, "zebrafish", "Zebrafish"),
         lambda s, r: r.system.name if r.system else "",
         lambda s, r: s.scalar(select(func.count(TankRecord.id)).where(TankRecord.rack_id_fk == r.id)) or 0,
         "tanks"),
    Kind("org_location", "Racks and rooms of other databases", "rack", OrgLocation, "organism_locations",
         _module_label(OrganismModule),
         lambda s, r: r.kind or "",
         lambda s, r: s.scalar(select(func.count(OrgHousing.id)).where(OrgHousing.location_id_fk == r.id)) or 0,
         "units"),
    Kind("plasmid_box", "Plasmid boxes", "box", PlasmidBox, "plasmid_boxes",
         lambda s, r: _builtin_label(s, "plasmids", "Plasmids"),
         lambda s, r: r.location or "",
         lambda s, r: s.scalar(select(func.count(PlasmidRecord.id)).where(PlasmidRecord.box_id_fk == r.id)) or 0,
         "plasmids"),
    Kind("inventory_box", "Inventory boxes", "box", InventoryRack, "inventory_racks", _module_label(InventoryModule),
         lambda s, r: r.kind or "",
         lambda s, r: s.scalar(select(func.count(InventoryItem.id)).where(InventoryItem.rack_id_fk == r.id)) or 0,
         "items"),
    Kind("stock_rack", "Fly & worm racks", "rack", StockRack, "stock_racks", _module_label(StockModule),
         lambda s, r: r.incubator.name if r.incubator else "",
         lambda s, r: s.scalar(select(func.count(StockUnit.id)).where(
             StockUnit.rack_id_fk == r.id, StockUnit.active.is_(True))) or 0,
         "vials / plates"),
    Kind("stock_incubator", "Fly & worm incubators", "incubator", StockIncubator, "stock_incubators",
         _module_label(StockModule),
         lambda s, r: f"{r.temperature} °C" if r.temperature else "",
         lambda s, r: s.scalar(select(func.count(StockRack.id)).where(StockRack.incubator_id_fk == r.id)) or 0,
         "racks"),
)
KIND_BY_KEY = {k.key: k for k in CONTAINERS}


@bp.before_request
def admins_only():
    if g.get("user") is None:
        return redirect(url_for("login", next=request.path))
    if not access.is_admin():
        abort(403)


def _usernames(session) -> list[str]:
    """Active (not disabled) lab members."""
    return [u.username for u in session.scalars(
        select(UserAccount).where(UserAccount.disabled.is_not(True)).order_by(UserAccount.username))]


@bp.route("/")
def index():
    with SessionLocal() as session:
        groups = []
        for kind in CONTAINERS:
            rows = []
            for row in sorted(session.scalars(select(kind.model)), key=lambda r: positions.place_order(r.name)):
                rows.append({"id": row.id, "name": row.name, "database": kind.database(session, row),
                             "where": kind.where(session, row), "count": kind.count(session, row),
                             "creator": (row.created_by or "").strip()})
            groups.append({"kind": kind, "rows": rows,
                           "unassigned": sum(1 for r in rows if not r["creator"]),
                           "databases": sorted({r["database"] for r in rows if not r["creator"]})})
        return render_template("admin_racks.html", groups=groups, usernames=_usernames(session),
                               total_unassigned=sum(g_["unassigned"] for g_ in groups))


@bp.route("/assign", methods=["POST"])
def assign():
    """One container, or every unassigned one of a kind (optionally in one
    database), gets a creator. An empty name makes it admin-only again."""
    kind = KIND_BY_KEY.get(request.form.get("kind", ""))
    if kind is None:
        abort(404)
    creator = (request.form.get("creator") or "").strip()
    with SessionLocal() as session:
        if creator and creator not in _usernames(session):
            flash(gettext("“%(who)s” is not an active lab member.", who=creator), "error")
            return redirect(url_for("admin_racks.index"))
        row_id = request.form.get("id", "").strip()
        if row_id.isdigit():
            rows = [session.get(kind.model, int(row_id))]
            if rows[0] is None:
                abort(404)
        else:
            database = request.form.get("database", "")
            rows = [r for r in session.scalars(select(kind.model))
                    if not (r.created_by or "").strip() and (not database or kind.database(session, r) == database)]
        rows = [r for r in rows if (r.created_by or "") != creator]
        if not rows:
            flash(gettext("Nothing to change."), "info")
            return redirect(url_for("admin_racks.index"))
        who = creator or "admins only"
        with audit.batch(session, "update", f"{kind.label}: looked after by {who} ×{len(rows)}", kind.table):
            for r in rows:
                r.created_by = creator
        session.commit()
        names = ", ".join(r.name for r in rows[:4])
        if len(rows) > 4:
            names = gettext("%(names)s and %(n)s more", names=names, n=len(rows) - 4)
        if creator:
            flash(gettext("%(names)s: now looked after by %(who)s. Undo from Batch history if that was a mistake.",
                          names=names, who=creator), "success")
        else:
            flash(gettext("%(names)s: now admin-only. Undo from Batch history if that was a mistake.", names=names),
                  "success")
    return redirect(url_for("admin_racks.index"))
