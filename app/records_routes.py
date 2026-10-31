"""Across the databases: searching every one of them at once (/search, and
search_results, which the ⌘K palette asks too), importing a CSV into one,
the batches Batch history lists and their undo, and the change log
(/audit). Also the old /orders and /samples addresses, which land on the
first inventory of that kind now that orders and samples live in the lab
inventory engine (app/inventory_routes.py).

These pages hang on the application object from app/flask_app.py and keep
the endpoint names every page links to ("global_search", "batches_view",
"audit_log_view", "orders", ...); tests/test_url_map.py holds them to it.
They take what every page needs from app/web.py and never import app.py,
which imports them.
"""
from __future__ import annotations

import json
import re
from datetime import date

from flask import flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import case, func, select
from sqlalchemy.orm import joinedload

from .db import SessionLocal
from . import access, i18n, lab, positions
from .i18n import gettext, pgettext
from . import undo as undo_service
from .models import (
    CageRecord,
    ClutchRecord,
    FishLine,
    TankRecord,
    Experiment,
    LitterRecord,
    InventoryItem,
    InventoryRack,
    MouseRecord,
    AuditEntry,
    BatchRecord,
    NotebookPage,
    NotebookTab,
    PlasmidRecord,
    StrainRecord,
)
from .formutil import _record_number
from .services import mouse_is_active
from . import audit, lab_notebook
from . import experiments as experiment_pages
from .plasmid_routes import _box_of, _plasmid_measure, _plasmid_where
from .flask_app import app
from .web import admin_required, local_time, login_required, next_number_retried


def _inventory_redirect(kind: str):
    from . import inventory_service as inventories
    with SessionLocal() as db_session:
        module = inventories.first_of_kind(db_session, kind)
        key = module.key if module else None
    if key is None:
        flash(gettext("There is no orders inventory yet. Add one from Add database.") if kind == "orders" else
              gettext("There is no samples inventory yet. Add one from Add database.") if kind == "samples" else
              f"There is no {kind} inventory yet. Add one from Add database.", "info")
        return redirect(url_for("organisms.index"))
    return redirect(url_for("inventory.module", key=key))


@app.route("/orders")
@login_required
def orders():
    return _inventory_redirect("orders")


@app.route("/samples")
@login_required
def samples():
    return _inventory_redirect("samples")


@app.route("/search")
@login_required
def global_search():
    """Cross-section search used by the Cmd+K palette.

    Returns up to 5 matches per section: mice, cages, litters, experiments,
    strains, plasmids, inventory items, vials, notebook pages. Each result has { type, id, label, sublabel, url }.
    Empty `q` returns nothing — the palette only fires on non-empty input.
    """
    q, results = search_results(request.args.get("q") or "")
    return jsonify({"ok": True, "results": results, **({"query": q} if q else {})})


def search_results(q: str, limit: int = 5) -> tuple[str, list[dict]]:
    """The search itself, as the signed-in person: (what was looked for,
    up to `limit` matches per section). Each match also carries `ref`, the
    row it is ({"kind", "id"}), which the assistants' search
    (lookup.search) hands on."""
    q = (q or "").strip()
    # "#44" is how the app writes mouse 44 everywhere; search for the number.
    if re.fullmatch(r"#\s*\d+", q):
        q = q.lstrip("#").strip()
    if not q:
        return "", []
    # % and _ are what they are, not LIKE's wildcards (they matched everything).
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    # A record's number: digits, not a 21-digit "number" and not "0012" (a
    # lot or catalogue number); those are text to look for.
    is_digit = _record_number(q) is not None
    starts = like[1:]                               # "S20%": a name that starts with it

    def by_name(column, *then):
        """A name that is the search, then one that starts with it, then one
        that has it, then the rest (a match in the notes): "S20" finds the
        S20-* tubes before a note that says "S200"."""
        return (case((func.lower(column) == q.lower(), 0), (column.ilike(starts, escape="\\"), 1),
                     (column.ilike(like, escape="\\"), 2), else_=3), *then)
    results: list[dict] = []
    with SessionLocal() as db_session:
        mouse_stmt = select(MouseRecord)
        if is_digit:
            mouse_stmt = mouse_stmt.where(MouseRecord.mouse_id == int(q))
        else:
            mouse_stmt = mouse_stmt.where(
                MouseRecord.genotype.ilike(like, escape="\\") | MouseRecord.owner.ilike(like, escape="\\") | MouseRecord.note.ilike(like, escape="\\")
                | MouseRecord.ear_tag.ilike(like, escape="\\")
                | MouseRecord.strain.ilike(like, escape="\\") | MouseRecord.attrs.ilike(like, escape="\\")
            )
        for m in db_session.scalars(mouse_stmt.order_by(MouseRecord.mouse_id.desc()).limit(limit)).all():
            results.append({
                "type": "mouse",
                "id": m.mouse_id,
                "ref": {"kind": "mouse", "id": m.id},
                "label": gettext("Mouse #%(id)s", id=m.mouse_id),
                "sublabel": " · ".join([m.gender or "?", m.genotype or gettext("(no genotype)"),
                                        m.owner or gettext("no owner")]),
                # Everyone's scope, so the mouse is on the sheet whoever
                # owns it; the sheet puts ?q= in its search box.
                "url": url_for("colony", view="mice", scope="all", q=m.mouse_id),
            })

        # The rest of the colony: cages, litters, experiments, strains.
        cage_stmt = select(CageRecord).where(
            CageRecord.cage_id.ilike(like, escape="\\") | CageRecord.purpose.ilike(like, escape="\\")
            | CageRecord.genotype_summary.ilike(like, escape="\\") | CageRecord.card_id.ilike(like, escape="\\")
            | CageRecord.notes.ilike(like, escape="\\") | CageRecord.room.ilike(like, escape="\\"))
        for cage in positions.natural_sorted(db_session.scalars(cage_stmt).all(), key=lambda c: c.cage_id)[:limit]:
            live = sum(1 for mouse in cage.mice if mouse_is_active(mouse))
            results.append({
                "type": "cage",
                "id": cage.id,
                "ref": {"kind": "cage", "id": cage.id},
                "label": gettext("Cage %(id)s", id=cage.cage_id),
                "sublabel": " · ".join(filter(None, [cage.purpose, gettext("%(n)s live", n=live), cage.owner])),
                "url": url_for("colony", view="cages", scope="all", q=cage.cage_id),
            })
        litter_stmt = select(LitterRecord).where(
            LitterRecord.litter_id.ilike(like, escape="\\") | LitterRecord.cohort_name.ilike(like, escape="\\")
            | LitterRecord.notes.ilike(like, escape="\\"))
        for litter in positions.natural_sorted(db_session.scalars(litter_stmt).all(), key=lambda l: l.litter_id)[:limit]:
            results.append({
                "type": "litter",
                "id": litter.id,
                "ref": {"kind": "litter", "id": litter.id},
                "label": gettext("Litter %(id)s", id=litter.litter_id),
                "sublabel": " · ".join(filter(None, [
                    gettext("born %(date)s", date=litter.date_of_birth.isoformat()) if litter.date_of_birth else "",
                    litter.cohort_name, gettext("%(n)s mice", n=len(litter.mice))])),
                "url": url_for("colony", view="litters", q=litter.litter_id),
            })
        exp_stmt = select(Experiment).where(
            Experiment.name.ilike(like, escape="\\") | Experiment.description.ilike(like, escape="\\")
            | Experiment.treatment_plan.ilike(like, escape="\\"))
        for exp in db_session.scalars(exp_stmt.order_by(Experiment.created_at.desc()).limit(limit)).all():
            if experiment_pages.place_for(db_session, exp.db or "colony") is None:
                continue              # on a database this person can't open (someone's own)
            results.append({
                "type": "experiment",
                "id": exp.id,
                "ref": {"kind": "experiment", "id": exp.id},
                "label": exp.name,
                "sublabel": " · ".join(filter(None, [pgettext("experiment", exp.status) if exp.status else "",
                                                     gettext("%(n)s mice", n=len(exp.memberships))
                                                     if (exp.db or "colony") == "colony" else "", exp.owner_username])),
                "url": experiment_pages.page_url(exp),
            })
        strain_stmt = select(StrainRecord).where(
            StrainRecord.strain_name.ilike(like, escape="\\") | StrainRecord.strain_number.ilike(like, escape="\\")
            | StrainRecord.strain_background.ilike(like, escape="\\") | StrainRecord.description.ilike(like, escape="\\"))
        for strain in db_session.scalars(strain_stmt.order_by(StrainRecord.strain_name).limit(limit)).all():
            results.append({
                "type": "strain",
                "id": strain.id,
                "ref": {"kind": "strain", "id": strain.id},
                "label": strain.strain_name,
                "sublabel": " · ".join(filter(None, [strain.strain_number, strain.strain_background, strain.supplier])),
                "url": url_for("colony", view="strains", q=strain.strain_name),
            })

        plasmid_stmt = select(PlasmidRecord)
        if is_digit:
            plasmid_stmt = plasmid_stmt.where(PlasmidRecord.plasmid_id == int(q))
        else:
            plasmid_stmt = plasmid_stmt.where(
PlasmidRecord.name.ilike(like, escape="\\") | PlasmidRecord.backbone.ilike(like, escape="\\")
                | PlasmidRecord.attrs.ilike(like, escape="\\")
                | PlasmidRecord.insert_seq.ilike(like, escape="\\") | PlasmidRecord.resistance.ilike(like, escape="\\")
                | PlasmidRecord.owner.ilike(like, escape="\\") | PlasmidRecord.storage_box.ilike(like, escape="\\")
                | PlasmidRecord.location.ilike(like, escape="\\") | PlasmidRecord.notes.ilike(like, escape="\\")
            )
        for p in db_session.scalars(plasmid_stmt.order_by(*by_name(PlasmidRecord.name, PlasmidRecord.plasmid_id.desc())).limit(limit)).all():
            # Where it is, in its box's own position names ("Box A · D7").
            where = _plasmid_where(p, _box_of(db_session, p))
            results.append({
                "type": "plasmid",
                "id": p.plasmid_id,
                "ref": {"kind": "plasmid", "id": p.id},
                "label": gettext("Plasmid #%(id)s · %(name)s", id=p.plasmid_id, name=p.name or gettext("(no name)")),
                "sublabel": " · ".join([p.backbone or "?", p.resistance or gettext("no resistance"),
                                        p.owner or gettext("no owner")]) + (f" · {where}" if where else ""),
                "url": url_for("plasmid_page", number=p.plasmid_id),
            })

        # Every lab inventory: samples, orders, reagents, antibodies, custom.
        kind_type = {"orders": "order", "samples": "sample", "reagents": "reagent", "chemicals": "chemical",
                     "antibodies": "antibody",
                     "viruses": "virus", "primers": "primer", "cell_lines": "cell-line",
                     "glycerol_stocks": "glycerol-stock"}
        # Only databases this person sees: the lab's and their own (app/lab.py).
        from . import inventory_service as inventories
        modules = {m.id: m for m in inventories.list_modules(db_session)}
        item_stmt = select(InventoryItem).where(InventoryItem.module_id_fk.in_(list(modules)))
        text_match = (
            InventoryItem.name.ilike(like, escape="\\") | InventoryItem.category.ilike(like, escape="\\")
            | InventoryItem.vendor.ilike(like, escape="\\") | InventoryItem.catalog_number.ilike(like, escape="\\")
            | InventoryItem.lot.ilike(like, escape="\\") | InventoryItem.notes.ilike(like, escape="\\")
            | InventoryItem.attrs.ilike(like, escape="\\")
            # What is kept in a cabinet or fridge named so ("explosion-proof").
            | InventoryItem.rack_id_fk.in_(select(InventoryRack.id).where(InventoryRack.place.ilike(like, escape="\\"))))
        # Digits are a record's number and a lot or catalogue number too
        # ("0012" is lot 0012M4817V, not record 12); a lot or catalogue
        # number that starts with it comes first.
        number = _record_number(q)
        item_stmt = item_stmt.where(text_match | (InventoryItem.number == number) if number is not None else text_match)
        code = (InventoryItem.lot.ilike(starts, escape="\\") | InventoryItem.catalog_number.ilike(starts, escape="\\"))
        order = [case((code, 0), else_=1)] if q.isdigit() else []
        if number is not None:
            order.append(InventoryItem.number != number)
        for item in db_session.scalars(item_stmt.order_by(*order, *by_name(InventoryItem.name, InventoryItem.id.desc())).limit(limit * 2)).all():
            module = modules.get(item.module_id_fk)
            if module is None:
                continue
            results.append({
                "type": kind_type.get(module.kind, "item"),
                "id": item.number,
                "ref": {"kind": "inventory_item", "id": item.id},
                "label": f"{i18n.translate_value(module.label)} #{item.number} · "
                         + (item.name or gettext("(unnamed)")),
                "sublabel": " · ".join(filter(None, [item.category, item.status, item.vendor,
                                                     gettext("lab common") if item.is_shared else item.owner,
                                                     inventories.where_label(item)])),
                # Open the inventory already searched down to this item.
                "url": url_for("inventory.module", key=module.key, q=item.name or str(item.number)),
            })

        # Fly vials and worm plates, by genotype (or either cross parent).
        from . import stock_service
        from .models import StockUnit
        stock_modules = {m.id: stock_service.view(m) for m in stock_service.list_modules(db_session)}
        unit_stmt = select(StockUnit).where(StockUnit.active.is_(True))
        if is_digit:
            unit_stmt = unit_stmt.where(StockUnit.number == int(q))
        else:
            unit_stmt = unit_stmt.where(
                StockUnit.genotype.ilike(like, escape="\\") | StockUnit.female_genotype.ilike(like, escape="\\")
                | StockUnit.male_genotype.ilike(like, escape="\\") | StockUnit.notes.ilike(like, escape="\\"))
        for unit in db_session.scalars(unit_stmt.order_by(StockUnit.id.desc()).limit(limit * 2)).all():
            mv = stock_modules.get(unit.module_id_fk)
            if mv is None:
                continue
            results.append({
                "type": "vial",
                "id": unit.number,
                "ref": {"kind": "stock_unit", "id": unit.id},
                "label": f"{mv.code(unit)} · " + (unit.genotype or gettext("(no genotype)")),
                "sublabel": " · ".join(filter(None, [i18n.translate_value(mv.label), mv.purpose_label(unit.purpose),
                                                     unit.rack.name if unit.rack else "", unit.owner])),
                "url": url_for("stocks.module", key=mv.key),
            })

        # Zebrafish tanks, lines and clutches.
        for t in positions.natural_sorted(db_session.scalars(
                select(TankRecord).options(joinedload(TankRecord.line))
                .where(TankRecord.tank_id.ilike(like, escape="\\") | TankRecord.card_id.ilike(like, escape="\\")
                       | TankRecord.attrs.ilike(like, escape="\\")
                       | TankRecord.owner.ilike(like, escape="\\") | TankRecord.notes.ilike(like, escape="\\"))
                ).all(), key=lambda t: t.tank_id)[:limit]:
            results.append({
                "type": "tank", "id": t.id, "ref": {"kind": "tank", "id": t.id}, "label": gettext("Tank %(id)s", id=t.tank_id),
                "sublabel": " · ".join(filter(None, [t.purpose, t.line.name if t.line else "", t.owner])),
                "url": url_for("zebrafish", view="tanks") + f"#tank-{t.id}",
            })
        for ln in db_session.scalars(
                select(FishLine).where(FishLine.name.ilike(like, escape="\\") | FishLine.zfin_name.ilike(like, escape="\\")
                                       | FishLine.allele.ilike(like, escape="\\") | FishLine.transgene_summary.ilike(like, escape="\\"))
                .order_by(FishLine.name).limit(limit)).all():
            results.append({
                "type": "fish-line", "id": ln.id, "ref": {"kind": "fish_line", "id": ln.id}, "label": ln.name,
                "sublabel": " · ".join(filter(None, [gettext("Zebrafish line"), ln.zfin_name, ln.background])),
                "url": url_for("zebrafish_line_detail", line_id=ln.id),
            })
        for c in db_session.scalars(
                select(ClutchRecord).where(ClutchRecord.clutch_id.ilike(like, escape="\\") | ClutchRecord.notes.ilike(like, escape="\\"))
                .order_by(ClutchRecord.date_of_fertilization.desc()).limit(limit)).all():
            results.append({
                "type": "clutch", "id": c.id, "ref": {"kind": "clutch", "id": c.id}, "label": gettext("Clutch %(id)s", id=c.clutch_id),
                "sublabel": " · ".join(filter(None, [c.date_of_fertilization.isoformat() if c.date_of_fertilization else "",
                                                     gettext("%(n)s embryos", n=c.embryo_count) if c.embryo_count else "",
                                                     c.owner])),
                "url": url_for("zebrafish", view="clutches") + f"#clutch-{c.id}",
            })
        # Animals, housing units and lines in the configurable organism databases.
        from . import organism_service
        results.extend(organism_service.search(db_session, q, limit))

        # Notebook pages — the person's own and those shared with them.
        page_stmt = (
            lab_notebook.accessible_filter(
                select(NotebookPage).join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id))
            .where(NotebookPage.title.ilike(like, escape="\\") | NotebookPage.body.ilike(like, escape="\\"))
            .order_by(*by_name(NotebookPage.title, NotebookPage.updated_at.desc()))
            .limit(limit)
        )
        for page in db_session.scalars(page_stmt).all():
            results.append({
                "type": "page",
                "id": page.id,
                "ref": {"kind": "notebook_page", "id": page.id},
                "label": page.title or gettext("Untitled page"),
                "sublabel": (gettext("Notebook · %(tab)s", tab=page.tab.title)
                             if page.tab and page.tab.owner_username == g.user.username
                             else gettext("Notebook · shared by %(owner)s",
                                          owner=page.tab.owner_username if page.tab else "")),
                "url": url_for("notebook", page=page.id),
            })

    # Nothing from a function the lab switched off (app/lab.py).
    features = lab.request_features()
    off_types = set()
    if not features.get("colony", True):
        off_types |= {"mouse", "cage", "litter", "experiment", "strain"}
    if not features.get("zebrafish", True):
        off_types |= {"tank", "clutch", "fish-line", "fish"}
    if not features.get("plasmids", True):
        off_types |= {"plasmid"}
    if not features.get("notebook", True):
        off_types |= {"notebook", "page", "notebook-page"}
    results = [r for r in results if r.get("type") not in off_types]
    return q, results


@app.route("/import/<entity>", methods=["POST"])
@login_required
@next_number_retried
def csv_import(entity: str):
    """Import a CSV into one of the data tables.

    Form fields:
      file (uploaded CSV)
      dry_run=1  → returns parsed rows without committing

    Supported entities: mouse, plasmid, order.
    Returns { ok, count, errors, preview } where preview is the first 6 rows.
    """
    if entity not in ("mouse", "plasmid", "order"):
        return jsonify({"ok": False, "error": f"unknown entity: {entity}"}), 400
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "error": gettext("missing file")}), 400
    dry_run = (request.form.get("dry_run") or "0") == "1"

    import csv as _csv, io as _io
    raw = upload.read()
    try:
        text_data = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text_data = raw.decode("latin-1")
    reader = _csv.DictReader(_io.StringIO(text_data))
    rows = [r for r in reader]
    if not rows:
        return jsonify({"ok": False, "error": gettext("empty CSV")}), 400

    errors: list[str] = []
    created = 0
    preview: list[dict] = []
    with SessionLocal() as db_session:
        if entity == "mouse":
            # Numbers come from one counter that skips every number already
            # used, in the database or earlier in this file, and the check
            # is the same in the dry run: a file whose ID repeats, or takes
            # a number a blank row was about to get, is caught row by row
            # instead of failing whole at the end.
            from .inventory_service import get_setting, set_setting
            from .services import MOUSE_ID_HIGH
            taken = set(db_session.scalars(select(MouseRecord.mouse_id)))
            high = get_setting(db_session, MOUSE_ID_HIGH, "")
            next_free = max(max(taken, default=0), int(high) if high.isdigit() else 0) + 1
            typed = {int(v) for r in rows if (v := (r.get("mouse_id") or "").strip()).isdigit()}
            if not dry_run:
                batch_ctx = audit.batch(db_session, "create", f"import mice from {upload.filename}"[:200], "mice")
                batch_ctx.__enter__()
            for idx, row in enumerate(rows, start=2):
                try:
                    mouse_id_raw = (row.get("mouse_id") or "").strip()
                    if mouse_id_raw:
                        if not mouse_id_raw.isdigit() or int(mouse_id_raw) < 1:
                            raise ValueError(f"mouse_id “{mouse_id_raw}” is not a whole number above zero")
                        mouse_id_value = int(mouse_id_raw)
                    else:
                        while next_free in taken or next_free in typed:
                            next_free += 1
                        mouse_id_value = next_free
                    if mouse_id_value in taken:
                        errors.append(f"row {idx}: mouse_id {mouse_id_value} already exists")
                        continue
                    taken.add(mouse_id_value)
                    mouse = MouseRecord(
                        mouse_id=mouse_id_value,
                        gender=(row.get("gender") or "").strip(),
                        genotype=(row.get("genotype") or "").strip(),
                        owner=(row.get("owner") or "").strip(),
                        status=(row.get("status") or "").strip(),
                        note=(row.get("note") or "").strip(),
                    )
                    if not dry_run:
                        db_session.add(mouse)
                    preview.append({"mouse_id": mouse_id_value, "genotype": mouse.genotype})
                    created += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"row {idx}: {exc}")
            if not dry_run:
                if taken:
                    set_setting(db_session, MOUSE_ID_HIGH, str(max(taken)))
                db_session.flush()
                batch_ctx.__exit__(None, None, None)
        elif entity == "plasmid":
            # Numbers for rows without one come from a counter that skips
            # numbers already used (in the database or earlier in the file);
            # re-reading max() per row double-counted rows already added.
            taken = set(db_session.scalars(select(PlasmidRecord.plasmid_id)))
            next_free = max(taken, default=0) + 1
            if not dry_run:
                batch_ctx = audit.batch(db_session, "create", f"import plasmids from {upload.filename}", "plasmids")
                batch_ctx.__enter__()
            for idx, row in enumerate(rows, start=2):
                try:
                    pid_raw = (row.get("plasmid_id") or "").strip()
                    if pid_raw:
                        if not pid_raw.isdigit() or int(pid_raw) < 1:
                            raise ValueError(f"plasmid_id “{pid_raw}” is not a whole number above zero")
                        pid_value = int(pid_raw)
                    else:
                        while next_free in taken:
                            next_free += 1
                        pid_value = next_free
                    if pid_value in taken:
                        errors.append(f"row {idx}: plasmid_id {pid_value} already exists")
                        continue
                    if not (row.get("name") or "").strip():
                        raise ValueError("no name")
                    taken.add(pid_value)
                    p = PlasmidRecord(
                        plasmid_id=pid_value,
                        name=(row.get("name") or "").strip(),
                        backbone=(row.get("backbone") or "").strip(),
                        insert_seq=(row.get("insert_seq") or row.get("insert") or "").strip(),
                        resistance=(row.get("resistance") or "").strip(),
                        owner=(row.get("owner") or g.user.username).strip(),
                        location=(row.get("location") or "").strip(),
                        concentration=_plasmid_measure(row, "concentration"),
                        a260_280=_plasmid_measure(row, "a260_280"),
                        notes=(row.get("notes") or "").strip(),
                    )
                    if not dry_run:
                        db_session.add(p)
                    preview.append({"plasmid_id": pid_value, "name": p.name})
                    created += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"row {idx}: {exc}")
            if not dry_run:
                batch_ctx.__exit__(None, None, None)
        elif entity == "order":
            # Orders are an inventory now; import into the one the page named.
            # Statuses follow the inventory's own list (any case; unknown →
            # its first), and the import is one batch, so it can be undone.
            from . import inventory_service as inventories
            named = request.args.get("module", "")
            module = (inventories.get_module(db_session, named) if named
                      else inventories.first_of_kind(db_session, "orders"))
            if module is None or module.kind != "orders":
                return jsonify({"ok": False, "error": gettext("Orders can only be imported into an orders inventory.")}), 400
            mv = inventories.view(module)
            number = inventories.next_number(db_session, module.id)
            cell = lambda row, *names: next((str(row.get(n) or "").strip() for n in names if str(row.get(n) or "").strip()), "")
            from contextlib import nullcontext
            batch = (nullcontext() if dry_run
                     else audit.batch(db_session, "create", f"import orders into {module.label}", "inventory_items"))
            with batch:
                for idx, row in enumerate(rows, start=2):
                    try:
                        owner = cell(row, "requester_name", "requester", "owner") or g.user.username
                        if owner != g.user.username and not access.is_admin():
                            owner = g.user.username
                        received_raw = cell(row, "received_on", "received", "date_received")
                        o = InventoryItem(
                            module_id_fk=module.id, number=number, owner=owner[:80],
                            vendor=cell(row, "vendor_name", "vendor")[:120],
                            name=cell(row, "item_name", "item", "name")[:200],
                            catalog_number=cell(row, "catalog_number", "catalog")[:120],
                            quantity=(cell(row, "quantity") or "1")[:60],
                            unit=cell(row, "unit", "units")[:30],
                            notes=cell(row, "notes"),
                        )
                        if received_raw:
                            try:
                                o.received_on = date.fromisoformat(received_raw)
                            except ValueError:
                                raise ValueError(f"received date “{received_raw}” is not YYYY-MM-DD") from None
                        if not o.name:
                            raise ValueError("no item name")
                        price = cell(row, "price", "unit_price", "cost")
                        if price:
                            o.attrs = json.dumps({"price": price})
                        status = cell(row, "status")
                        inventories.apply_status(mv, o, inventories.match_status(mv, status)
                                                 or (mv.statuses[0] if mv.statuses else status))
                        if not dry_run:
                            db_session.add(o)
                        number += 1
                        preview.append({"item": o.name, "vendor": o.vendor, "status": o.status})
                        created += 1
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"row {idx}: {exc}")
        if not dry_run:
            db_session.commit()

    return jsonify({
        "ok": True,
        "count": created,
        "errors": errors[:20],
        "preview": preview[:6],
        "dry_run": dry_run,
    })


@app.route("/batches")
@login_required
def batches_view():
    """Recent batch operations, with what undoing each would do.

    Everyone sees their own; admins see the whole lab's, because the
    "someone bulk-edited the colony this morning" question is theirs to
    answer.
    """
    show_all = access.is_admin() and request.args.get("scope") != "mine"
    with SessionLocal() as db_session:
        rows = undo_service.recent(
            db_session, limit=60,
            actor=None if show_all else g.user.username)
        batches = []
        for row in rows:
            summary = undo_service.describe(db_session, row)
            batches.append({
                "id": row.id,
                "action": row.action,
                "description": row.description,
                "table": row.target_table,
                "actor": row.actor,
                "count": row.record_count or summary["entries"],
                "created_at": row.created_at,
                "undone_at": row.undone_at,
                "undone_by": row.undone_by,
                "mine": row.actor == g.user.username,
                "can_undo": (not row.is_undone
                             and (row.actor == g.user.username or access.is_admin())),
                **summary,
            })
    return render_template("batches.html", batches=batches, show_all=show_all)


@app.route("/batches/<int:batch_id>/undo", methods=["POST"])
@login_required
def undo_batch(batch_id: int):
    force = request.form.get("force") == "1"
    with SessionLocal() as db_session:
        row = db_session.get(BatchRecord, batch_id)
        if row is None:
            flash(gettext("That batch no longer exists."), "error")
            return redirect(_undo_back())
        if not (row.actor == g.user.username or access.is_admin()):
            flash(gettext("Only whoever ran a batch, or an admin, can undo it."), "error")
            return redirect(_undo_back())

        result = undo_service.undo(db_session, row, g.user.username, force=force)
        if not result["ok"]:
            db_session.rollback()
            for problem in result["problems"]:
                flash(problem, "error")
            return redirect(_undo_back())
        db_session.commit()

    if result["skipped"]:
        flash(gettext("Undid %(reverted)s change(s) — %(skipped)s could not be reversed.",
                      reverted=result["reverted"], skipped=result["skipped"]), "success")
    else:
        flash(gettext("Undid %(reverted)s change(s).", reverted=result["reverted"]), "success")
    for note in result["notes"][:5]:
        flash(note, "info")
    return redirect(_undo_back())



def _undo_back() -> str:
    """Undo from Settings → History goes back there; from Batch history, there."""
    return url_for("settings", _anchor="history") if request.form.get("from") == "settings" else url_for("batches_view")

@app.route("/audit")
@admin_required
def audit_log_view():
    """Admin-only change history across the app.

    Every create, edit and delete on a tracked table lands here via the
    flush listener in app/audit.py, with a field-level diff — so the
    question is no longer just "who deleted that mouse?" but "what was its
    genotype before someone changed it?"."""
    with SessionLocal() as db_session:
        rows = db_session.scalars(
            select(AuditEntry).order_by(AuditEntry.changed_at.desc()).limit(200)
        ).all()
        entries = [
            {
                "id": e.id,
                "table_name": e.table_name,
                "record_id": e.record_id,
                "record_label": e.record_label or f"#{e.record_id}",
                "action": e.action,
                "changed_by": e.changed_by,
                "changed_at": local_time(e.changed_at).strftime("%Y-%m-%d %H:%M:%S"),
                "details": e.details or "",
            }
            for e in rows
        ]
    return render_template("audit.html", entries=entries)
