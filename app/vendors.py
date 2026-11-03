"""The lab's vendors: one page listing every vendor its records name, what
the lab keeps about each (website, contact, customer number) and, for an
admin or a facility manager, renaming one or merging several spellings
into one, everywhere they are written.

A record names its vendor as text (inventory_items.vendor). Spellings that
differ only in case or spacing are one vendor here; "Sigma" and
"Sigma-Aldrich" are two until someone merges them. A rename or merge
rewrites every record's vendor in one batch of the change history, so
Undo takes it back, and keeps one Vendor row with the details of all.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select

from . import access, audit
from . import inventory_service as svc
from . import order_spending as spending
from .db import SessionLocal
from .i18n import gettext, ngettext
from .models import InventoryItem, InventoryModule, Vendor

bp = Blueprint("vendors", __name__, url_prefix="/vendors")

DETAILS = ("website", "contact", "email", "phone", "customer_number", "notes")
LIMITS = {"name": 120, "website": 300, "contact": 200, "email": 200, "phone": 80, "customer_number": 120,
          "notes": 4000}


@bp.before_request
def require_login():
    if g.get("user") is None:
        return redirect(url_for("login", next=request.path))
    return None


def key_of(name) -> str:
    """One vendor however it is written: case and spacing aside."""
    return " ".join(str(name or "").split()).casefold()


def clean(name) -> str:
    return " ".join(str(name or "").split())[:LIMITS["name"]]


def can_rename() -> bool:
    """Renaming rewrites everyone's records: an admin's or a facility
    manager's to do."""
    return access.is_facility()


def website_url(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    return text if re.match(r"https?://", text, re.I) else f"https://{text}"


def listing(session, today: date | None = None) -> list[dict]:
    """Every vendor the lab's records name or the lab has details for, the
    most-ordered first: its spellings, how many orders and other records
    name it, what its orders cost in the last twelve months and when it was
    last ordered from."""
    today = today or date.today()
    modules = {m.id: m for m in session.scalars(select(InventoryModule))}
    rows = session.execute(select(InventoryItem.vendor, InventoryItem.module_id_fk,
                                  func.count(InventoryItem.id), func.max(InventoryItem.created_at))
                           .where(InventoryItem.vendor != "")
                           .group_by(InventoryItem.vendor, InventoryItem.module_id_fk))
    found: dict[str, dict] = {}

    def entry(key: str) -> dict:
        return found.setdefault(key, {"key": key, "spellings": Counter(), "orders": 0, "records": 0,
                                      "last_ordered": None, "spent": 0.0, "details": None})
    for vendor, module_id, count, newest in rows:
        key = key_of(vendor)
        if not key:
            continue
        e = entry(key)
        e["spellings"][" ".join(vendor.split())] += count
        module = modules.get(module_id)
        if module is not None and module.kind == "orders":
            e["orders"] += count
            if newest and (e["last_ordered"] is None or newest > e["last_ordered"]):
                e["last_ordered"] = newest
        else:
            e["records"] += count
    # What the last twelve months of orders cost, from each orders
    # database's own settings (order_spending).
    since = spending.add_months(date(today.year, today.month, 1), -11)
    for module in modules.values():
        if module.kind != "orders":
            continue
        settings = spending.settings_of(session, module)
        for o in spending.orders_of(session, module, settings):
            if o.vendor and o.cost and o.day >= since and key_of(o.vendor) in found:
                found[key_of(o.vendor)]["spent"] += o.cost
    for v in session.scalars(select(Vendor)):
        entry(key_of(v.name))["details"] = v
    out = []
    for e in found.values():
        details = e["details"]
        spellings = [s for s, _n in e["spellings"].most_common()]
        e["name"] = details.name if details else (spellings[0] if spellings else e["key"])
        e["others"] = [s for s in spellings if s != e["name"]]
        e["website"] = website_url(details.website) if details else ""
        out.append(e)
    out.sort(key=lambda e: (-e["orders"], -e["records"], e["name"].casefold()))
    return out


def details_map(session) -> dict[str, dict]:
    """What the lab keeps about each vendor, by key_of its name, for a
    record's dialog to show beside its Vendor: only vendors with something
    kept."""
    out = {}
    for v in session.scalars(select(Vendor)):
        kept = {k: (getattr(v, k) or "").strip() for k in ("website", "contact", "email", "phone", "customer_number")}
        if any(kept.values()):
            out[key_of(v.name)] = {"name": v.name, "url": website_url(kept["website"]), **kept}
    return out


def names(session) -> list[str]:
    """The names the lab keeps details for, to offer as a record's vendor."""
    return [v.name for v in session.scalars(select(Vendor).order_by(Vendor.name))]


def _vendor_row(session, name: str) -> Vendor | None:
    key = key_of(name)
    return next((v for v in session.scalars(select(Vendor)) if key_of(v.name) == key), None)


def rename(session, olds: list[str], new: str) -> int:
    """Every record whose vendor reads as one of `olds` now reads `new`, and
    one Vendor row keeps all their details (the new name's first, then each
    old one's for what it lacks). Returns how many records changed."""
    new = clean(new)
    keys = {key_of(o) for o in olds if key_of(o)}
    if not new or not keys:
        return 0
    changed = 0
    for item in session.scalars(select(InventoryItem).where(InventoryItem.vendor != "")):
        if key_of(item.vendor) in keys and item.vendor != new:
            item.vendor = new
            changed += 1
    rows = [v for v in session.scalars(select(Vendor)) if key_of(v.name) in keys | {key_of(new)}]
    if rows:
        keep = next((v for v in rows if key_of(v.name) == key_of(new)), rows[0])
        for other in rows:
            if other is keep:
                continue
            for field in DETAILS:
                if not getattr(keep, field) and getattr(other, field):
                    setattr(keep, field, getattr(other, field))
            session.delete(other)
        session.flush()
        keep.name = new
        keep.updated_at, keep.updated_by = datetime.utcnow(), access.username()
    return changed


# ------------------------------------------------------------------- pages

@bp.route("")
def page():
    with SessionLocal() as session:
        vendors = listing(session)
        modules = [m for m in svc.list_modules(session) if m.kind == "orders"]
        orders_key = modules[0].key if modules else None
        currency = spending.settings_of(session, modules[0])["currency"] if modules else ""
    return render_template("vendors.html", vendors=vendors, can_rename=can_rename(), orders_key=orders_key,
                           currency=currency, amount_text=spending.amount_text,
                           look_for=(request.args.get("q") or "").strip()[:120])


@bp.route("/save", methods=["POST"])
def save():
    """A vendor's details from its dialog; a new name renames it everywhere
    (an admin's or a facility manager's to do)."""
    form = request.form
    was, name = clean(form.get("was")), clean(form.get("name"))
    if not name:
        flash(gettext("Give the vendor a name."), "error")
        return redirect(url_for("vendors.page"))
    with SessionLocal() as session:
        renaming = bool(was) and was != name
        if renaming and not can_rename():
            flash(gettext("Only an admin or a facility manager can rename a vendor."), "error")
            return redirect(url_for("vendors.page"))
        with audit.batch(session, "update", gettext("Vendor %(name)s", name=name), "vendors"):
            changed = rename(session, [was], name) if renaming else 0
            row = _vendor_row(session, name)
            if row is None:
                row = Vendor(name=name, created_by=access.username())
                session.add(row)
            for field in DETAILS:
                setattr(row, field, str(form.get(field) or "").strip()[:LIMITS[field]])
            row.updated_at, row.updated_by = datetime.utcnow(), access.username()
        session.commit()
    if renaming:
        flash(ngettext("Renamed %(old)s to %(new)s on %(num)s record.",
                       "Renamed %(old)s to %(new)s on %(num)s records.", changed, old=was, new=name), "success")
    else:
        flash(gettext("Saved %(name)s.", name=name), "success")
    return redirect(url_for("vendors.page"))


@bp.route("/merge", methods=["POST"])
def merge():
    """Several vendors (the ticked rows' names) become one, the name chosen
    or typed, on every record."""
    if not can_rename():
        abort(403)
    olds = [clean(n) for n in request.form.getlist("names") if clean(n)]
    into = clean(request.form.get("into_other")) or clean(request.form.get("into"))
    if len({key_of(o) for o in olds}) < 2 or not into:
        flash(gettext("Tick two or more vendors and choose the name to keep."), "error")
        return redirect(url_for("vendors.page"))
    with SessionLocal() as session:
        with audit.batch(session, "update", gettext("Merged vendors into %(name)s", name=into), "inventory_items"):
            changed = rename(session, olds, into)
        session.commit()
    flash(ngettext("Merged %(vendors)s vendors into %(name)s: %(num)s record changed.",
                   "Merged %(vendors)s vendors into %(name)s: %(num)s records changed.", changed,
                   vendors=len({key_of(o) for o in olds}), name=into), "success")
    return redirect(url_for("vendors.page"))

