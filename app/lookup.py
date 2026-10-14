"""What an assistant reads before it proposes anything (app/api.py):

* resolve(q, kinds): the records a phrase could mean ("cage 88", "#14",
  "FV12", "the TMX tank"), each with a reference {"kind", "id"} that a
  proposal names it by (app/actions.py). Several candidates mean the
  assistant should ask, not guess.
* vocabulary(): what this lab calls things: its databases, strains and
  lines, statuses, purposes, racks, people, and each organism database's
  own fields, so free text maps onto values the lab uses.
* due(days): Home's list of what is due, each with the change that would
  record it done where there is one.
* search(q): the app's own search box, with a reference, a snippet and a
  link for each match; history(): a record's change history, as the pages
  let this person see it; mouse_lineage() and plasmid_lineage(): family.

Everything is read as the person the token belongs to: a database they
can't open is not listed.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from flask import request
from sqlalchemy import func, or_, select

from . import lab
from .formutil import like_pattern
from .models import (CageRecord, ClutchRecord, DropdownOption, Experiment, ExperimentStep, FishLine, FishRack,
                     LitterRecord, MouseRack,
                     MouseRecord, OrgCohort, OrgCross, OrgHousing, OrgLine, OrgLocation, Organism, StockRack,
                     StockUnit, StrainRecord, TankRecord, UserAccount, WaterSystem)

PER_KIND = 8
# Words a person puts before a number ("cage 88"): they say what kind it is.
KIND_WORDS = {"cage": ["cage"], "mouse": ["mouse"], "mice": ["mouse"], "litter": ["litter"],
              "tank": ["tank", "org_housing"], "rack": ["rack", "fish_rack", "stock_rack", "org_location"],
              "clutch": ["clutch"], "line": ["fish_line", "org_line"], "vial": ["stock_unit"],
              "plate": ["stock_unit"], "fish": ["fish_line", "tank"], "cross": ["org_cross"],
              "cohort": ["org_cohort"], "experiment": ["experiment"], "page": ["notebook_page"],
              "plasmid": ["plasmid"], "item": ["inventory_item"], "instrument": ["equipment"],
              "equipment": ["equipment"], "template": ["notebook_template"],
              "protocol": ["notebook_page", "protocol_folder"], "folder": ["protocol_folder"]}
# Words that name a kind of inventory ("primer 12"): only its items.
INVENTORY_WORDS = {"sample": "samples", "order": "orders", "reagent": "reagents", "chemical": "chemicals",
                   "antibody": "antibodies", "virus": "viruses", "primer": "primers", "oligo": "primers",
                   "glycerol": "glycerol_stocks"}
KIND_WORDS.update({word: ["inventory_item"] for word in INVENTORY_WORDS})


def _hit(kind: str, row, label: str, database: str, exact: bool, path: str | None = None) -> dict:
    """A match: its reference (for proposals, history and lineage) and,
    for a kind the API reads one at a time, the path `get` reads it at."""
    path = path or path_of(None, kind, row)
    return {"kind": kind, "id": row.id, "ref": {"kind": kind, "id": row.id}, "label": label,
            "database": database, "exact": exact, **({"path": path} if path else {})}


def path_of(session, kind: str, row) -> str | None:
    """Where /api/v1 reads one record: by what the lab writes on it (cages/88,
    mice/1043, plasmids/14, inventories/reagents/items/12), or an
    experiment's id; None for a kind it reads only in lists."""
    from urllib.parse import quote
    if kind == "mouse":
        return f"mice/{row.mouse_id}"
    if kind == "cage":
        return f"cages/{quote(row.cage_id, safe='')}"
    if kind == "plasmid":
        return f"plasmids/{row.plasmid_id}"
    if kind == "experiment":
        return f"experiments/{row.id}"
    if kind == "notebook_page":
        return f"notebook/pages/{row.id}"
    if kind == "inventory_item" and session is not None:
        from .models import InventoryModule
        module = session.get(InventoryModule, row.module_id_fk)
        return f"inventories/{module.key}/items/{row.number}" if module is not None else None
    return None


def _rack_where(rack, row, col) -> str:
    from . import positions
    if rack is None:
        return ""
    if row and col:
        return f"{rack.name} · {positions.label(row, col, rack.naming, rack.cols)}"
    return rack.name


def _match(column, q: str):
    return func.lower(column).like(like_pattern(q.lower()), escape="\\")


def _ranked(rows, exact) -> list:
    return sorted(rows, key=lambda r: (not exact(r),))[:PER_KIND]


def resolve(session, q: str, kinds: set[str] | None = None) -> list[dict]:
    q = (q or "").strip()
    words = q.split()
    inventory_kind = None
    if len(words) > 1 and words[0].lower() in KIND_WORDS:
        narrowed = set(KIND_WORDS[words[0].lower()])
        kinds = (kinds & narrowed) if kinds else narrowed
        inventory_kind = INVENTORY_WORDS.get(words[0].lower())
        q = " ".join(words[1:])
    if not q:
        return []
    want = lambda kind: kinds is None or kind in kinds  # noqa: E731
    features = lab.request_features()
    labels = _database_labels(session)
    out: list[dict] = []
    number = q.lstrip("#")

    if features.get("colony", True):
        colony = labels.get("colony", "Mouse colony")
        if want("mouse") and number.isdigit():
            for m in session.scalars(select(MouseRecord).where(MouseRecord.mouse_id == int(number))):
                bits = [f"Mouse #{m.mouse_id}", m.gender or "", m.genotype or "",
                        f"cage {m.cage.cage_id}" if m.cage else "", m.status or "",
                        "dead" if m.date_of_death else "", m.owner or ""]
                out.append(_hit("mouse", m, " · ".join(b for b in bits if b), colony, True))
        if want("cage"):
            rows = session.scalars(select(CageRecord).where(or_(_match(CageRecord.cage_id, q),
                                                                _match(CageRecord.card_id, q)))
                                   .order_by(CageRecord.cage_id).limit(50)).all()
            for c in _ranked(rows, lambda c: c.cage_id.lower() == q.lower()):
                live = [m for m in c.mice if m.date_of_death is None]
                bits = [f"Cage {c.cage_id}", _rack_where(c.rack, c.rack_row, c.rack_col), c.purpose or "",
                        f"{len(live)} mice", c.owner or ""]
                out.append(_hit("cage", c, " · ".join(b for b in bits if b), colony, c.cage_id.lower() == q.lower()))
        if want("litter"):
            rows = session.scalars(select(LitterRecord).where(_match(LitterRecord.litter_id, q)).limit(50)).all()
            for li in _ranked(rows, lambda li: li.litter_id.lower() == q.lower()):
                bits = [f"Litter {li.litter_id}", f"born {li.date_of_birth.isoformat()}" if li.date_of_birth else "",
                        li.cohort_name or ""]
                out.append(_hit("litter", li, " · ".join(b for b in bits if b), colony,
                                li.litter_id.lower() == q.lower()))
        if want("rack"):
            for r in session.scalars(select(MouseRack).where(_match(MouseRack.name, q)).limit(PER_KIND)):
                out.append(_hit("rack", r, f"Rack {r.name}" + (f" · {r.room}" if r.room else ""), colony,
                                r.name.lower() == q.lower()))

    if features.get("zebrafish", True):
        zf = labels.get("zebrafish", "Zebrafish")
        if want("tank"):
            rows = session.scalars(select(TankRecord).where(or_(_match(TankRecord.tank_id, q),
                                                                _match(TankRecord.card_id, q)))
                                   .order_by(TankRecord.tank_id).limit(50)).all()
            for t in _ranked(rows, lambda t: t.tank_id.lower() == q.lower()):
                bits = [f"Tank {t.tank_id}", t.line.name if t.line else "", t.purpose or "",
                        "" if t.active else "retired", t.owner or ""]
                out.append(_hit("tank", t, " · ".join(b for b in bits if b), zf, t.tank_id.lower() == q.lower()))
        if want("fish_line"):
            for li in session.scalars(select(FishLine).where(or_(_match(FishLine.name, q), _match(FishLine.zfin_name, q)))
                                      .limit(PER_KIND)):
                out.append(_hit("fish_line", li, f"Line {li.name}", zf, li.name.lower() == q.lower()))
        if want("clutch"):
            for c in session.scalars(select(ClutchRecord).where(_match(ClutchRecord.clutch_id, q)).limit(PER_KIND)):
                out.append(_hit("clutch", c, f"Clutch {c.clutch_id} · {c.date_of_fertilization.isoformat()}", zf,
                                c.clutch_id.lower() == q.lower()))
        for kind, model, word in (("fish_rack", FishRack, "Rack"), ("water_system", WaterSystem, "Water system")):
            if want(kind):
                for r in session.scalars(select(model).where(_match(model.name, q)).limit(PER_KIND)):
                    out.append(_hit(kind, r, f"{word} {r.name}", zf, r.name.lower() == q.lower()))

    out += _resolve_stocks(session, q, want)
    out += _resolve_organisms(session, q, want)
    if want("experiment"):
        from .experiments import place_for
        for e in session.scalars(select(Experiment).where(_match(Experiment.name, q)).order_by(Experiment.id.desc())
                                 .limit(PER_KIND)):
            if place_for(session, e.db or "colony") is None:
                continue
            bits = [f"Experiment {e.name}", e.status or "", f"from {e.start_date.isoformat()}" if e.start_date else "",
                    e.owner_username or ""]
            out.append(_hit("experiment", e, " · ".join(b for b in bits if b), "Experiments",
                            e.name.lower() == q.lower()))
    if want("notebook_page") and features.get("notebook", True):
        from .lab_notebook import accessible_filter
        from .models import NotebookPage, NotebookPageInfo, NotebookTab
        stmt = accessible_filter(select(NotebookPage).join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id)
                                 .where(_match(NotebookPage.title, q)).order_by(NotebookPage.updated_at.desc())
                                 .limit(PER_KIND))
        pages = session.scalars(stmt).all()
        kinds = dict(session.execute(select(NotebookPageInfo.page_id_fk, NotebookPageInfo.kind).where(
            NotebookPageInfo.page_id_fk.in_([p.id for p in pages] or [0]))).all())
        for p in pages:
            kind = kinds.get(p.id) or "note"
            out.append(_hit("notebook_page", p, f"Page {p.title}" + (f" · {kind}" if kind != "note" else ""),
                            "Notebook", p.title.lower() == q.lower()))
    if want("protocol_folder") and features.get("notebook", True):
        from .models import NotebookFolder
        rows = session.scalars(select(NotebookFolder).where(NotebookFolder.kind == "protocol",
                                                            _match(NotebookFolder.name, q)).limit(50)).all()
        for f in _ranked(rows, lambda f: f.name.lower() == q.lower()):
            out.append(_hit("protocol_folder", f, f"Folder {f.name} · protocols", "Notebook",
                            f.name.lower() == q.lower()))
    out += _resolve_inventories(session, q, want, inventory_kind)
    if features.get("plasmids", True):
        out += _resolve_plasmids(session, q, want)
    if features.get("calendar", True) and want("equipment"):
        from .models import Equipment
        for e in session.scalars(select(Equipment).where(Equipment.active.is_(True), _match(Equipment.name, q))
                                 .limit(PER_KIND)):
            out.append(_hit("equipment", e, " · ".join(b for b in (f"Instrument {e.name}", e.location or "") if b),
                            "Calendar", e.name.lower() == q.lower()))
    if want("notebook_template") and features.get("notebook", True):
        from .actions import _templates_titled
        from .models import NotebookTemplate
        rows = [t for t in session.scalars(select(NotebookTemplate).where(_match(NotebookTemplate.title, q))
                                           .limit(50)) if _templates_titled(session, str(t.id))]
        for t in rows[:PER_KIND]:
            out.append(_hit("notebook_template", t, f"Template {t.title}" + (" · lab" if t.lab else ""), "Notebook",
                            t.title.lower() == q.lower()))
    out.sort(key=lambda h: not h["exact"])
    return out


def _resolve_inventories(session, q: str, want, only_kind: str | None = None) -> list[dict]:
    """Items in the inventories this person can open: by number (#12) or by
    name, catalogue number or lot."""
    from . import inventory_service as isvc
    from .models import InventoryItem
    if not want("inventory_item"):
        return []
    out = []
    number = q.lstrip("#")
    for module in isvc.list_modules(session):
        if not lab.can_see(module) or (only_kind and module.kind != only_kind):
            continue
        mv = isvc.view(module)
        stmt = select(InventoryItem).where(InventoryItem.module_id_fk == module.id)
        if number.isdigit():
            stmt = stmt.where(InventoryItem.number == int(number))
        else:
            stmt = stmt.where(or_(_match(InventoryItem.name, q), _match(InventoryItem.catalog_number, q),
                                  _match(InventoryItem.lot, q))).order_by(InventoryItem.number.desc())
        for i in session.scalars(stmt.limit(PER_KIND)):
            where = _rack_where(i.rack, i.rack_row, i.rack_col) or i.location_note or ""
            bits = [f"{mv.item_noun.capitalize()} #{i.number}", i.name or "", i.category or "", i.status or "",
                    " ".join(b for b in (i.quantity or "", i.unit or "") if b), where, i.owner or ""]
            out.append(_hit("inventory_item", i, " · ".join(b for b in bits if b), module.label,
                            number.isdigit() or (i.name or "").lower() == q.lower(),
                            path=f"inventories/{module.key}/items/{i.number}"))
    return out


def _resolve_plasmids(session, q: str, want) -> list[dict]:
    from .models import PlasmidRecord
    if not want("plasmid"):
        return []
    number = q.lstrip("#")
    if number.isdigit():
        rows = session.scalars(select(PlasmidRecord).where(PlasmidRecord.plasmid_id == int(number))).all()
    else:
        rows = _ranked(session.scalars(select(PlasmidRecord).where(or_(_match(PlasmidRecord.name, q),
                                                                       _match(PlasmidRecord.insert_seq, q)))
                                       .order_by(PlasmidRecord.plasmid_id.desc()).limit(50)).all(),
                       lambda p: (p.name or "").lower() == q.lower())
    out = []
    for p in rows:
        bits = [f"Plasmid #{p.plasmid_id}", p.name or "", p.backbone or "", p.storage_box or p.location or "",
                f"{len(p.full_sequence)} bp" if p.full_sequence else "", p.owner or ""]
        out.append(_hit("plasmid", p, " · ".join(b for b in bits if b), "Plasmids",
                        number.isdigit() or (p.name or "").lower() == q.lower()))
    return out


def _resolve_stocks(session, q: str, want) -> list[dict]:
    from . import stock_service as svc
    out = []
    for module in svc.list_modules(session):
        mv = svc.view(module)
        prefix = mv.s.get("code_prefix") or ""
        if want("stock_unit"):
            rest = q[len(prefix):] if q.upper().startswith(prefix.upper()) else (q.lstrip("#") if not prefix else "")
            if rest.isdigit():
                rows = session.scalars(select(StockUnit).where(StockUnit.module_id_fk == module.id,
                                                               StockUnit.number == int(rest))).all()
            else:
                rows = session.scalars(select(StockUnit).where(StockUnit.module_id_fk == module.id,
                                                               StockUnit.active.is_(True),
                                                               _match(StockUnit.genotype, q))
                                       .order_by(StockUnit.number).limit(PER_KIND)).all()
            for u in rows:
                bits = [mv.code(u), u.genotype or "", mv.purpose_label(u.purpose) if u.purpose else "",
                        _rack_where(u.rack, u.rack_row, u.rack_col), "" if u.active else "discarded", u.owner or ""]
                out.append(_hit("stock_unit", u, " · ".join(b for b in bits if b), mv.label, rest.isdigit()))
        if want("stock_rack"):
            for r in session.scalars(select(StockRack).where(StockRack.module_id_fk == module.id,
                                                             _match(StockRack.name, q)).limit(PER_KIND)):
                out.append(_hit("stock_rack", r, f"Rack {r.name}", mv.label, r.name.lower() == q.lower()))
    return out


def _resolve_organisms(session, q: str, want) -> list[dict]:
    from . import organism_service as svc
    out = []
    for module in svc.list_modules(session):
        mv = svc.view(module)
        for kind, model, noun in (("org_animal", Organism, mv.organism_noun), ("org_housing", OrgHousing, mv.housing_noun),
                                  ("org_line", OrgLine, "line"), ("org_cross", OrgCross, "cross"),
                                  ("org_cohort", OrgCohort, "cohort")):
            if not want(kind):
                continue
            clause = _match(model.code, q)
            if model is OrgLine:
                clause = or_(clause, _match(OrgLine.name, q))
            for r in session.scalars(select(model).where(model.module_id_fk == module.id, clause).limit(PER_KIND)):
                extra = getattr(r, "name", "") or getattr(r, "genotype", "") or ""
                out.append(_hit(kind, r, " · ".join(b for b in (f"{noun.capitalize()} {r.code or '#' + str(r.id)}",
                                                                extra) if b),
                                mv.label, (r.code or "").lower() == q.lower()))
        if want("org_location"):
            for r in session.scalars(select(OrgLocation).where(OrgLocation.module_id_fk == module.id,
                                                               _match(OrgLocation.name, q)).limit(PER_KIND)):
                out.append(_hit("org_location", r, r.name, mv.label, r.name.lower() == q.lower()))
    return out


def _database_labels(session) -> dict[str, str]:
    from . import inventory_service
    return inventory_service.builtin_labels(session)


# ---------------------------------------------------------------- vocabulary

def vocabulary(session) -> dict:
    from . import organism_service as org_svc
    from . import stock_service as stock_svc
    from .app import _merged_choices, cage_purposes
    from .models import FISH_SEX_OPTIONS, FISH_STATUS_OPTIONS, MOUSE_STATUS_OPTIONS, TANK_PURPOSE_OPTIONS
    from .services import dropdown_options_map
    features = lab.request_features()
    labels = _database_labels(session)
    out: dict = {"today": date.today().isoformat(),
                 "people": [u for (u,) in session.execute(select(UserAccount.username)
                                                           .where(UserAccount.disabled.is_(False))
                                                           .order_by(UserAccount.username))],
                 "databases": []}
    if features.get("colony", True):
        options = dropdown_options_map(session)
        transgenes = set()
        for row in session.execute(select(MouseRecord.transgene_1, MouseRecord.transgene_2, MouseRecord.transgene_3,
                                          MouseRecord.transgene_4).where(MouseRecord.date_of_death.is_(None))):
            transgenes.update(t.strip() for t in row if t and t.strip())
        out["databases"].append({"kind": "mouse colony", "key": "colony", "label": labels.get("colony", "Mouse colony")})
        in_use = session.scalars(select(MouseRecord.status).distinct())
        out["mouse_colony"] = {
            # As the mouse sheet offers them: the built-in ones, the lab's
            # own, then any others its mice have (app.mouse_sheet_meta).
            "statuses": _merged_choices(MOUSE_STATUS_OPTIONS, options.get("status", []), in_use),
            "cage_purposes": cage_purposes(options),
            "strains": [n for (n,) in session.execute(select(StrainRecord.strain_name).order_by(StrainRecord.strain_name))],
            "transgenes_in_use": sorted(transgenes)[:300],
            "racks": [n for (n,) in session.execute(select(MouseRack.name).order_by(MouseRack.name))],
            "presets": {k: v for k, v in options.items() if v},
        }
    if features.get("zebrafish", True):
        out["databases"].append({"kind": "zebrafish", "key": "zebrafish", "label": labels.get("zebrafish", "Zebrafish")})
        out["zebrafish"] = {
            "lines": [n for (n,) in session.execute(select(FishLine.name).order_by(FishLine.name))],
            "racks": [n for (n,) in session.execute(select(FishRack.name).order_by(FishRack.name))],
            "water_systems": [n for (n,) in session.execute(select(WaterSystem.name).order_by(WaterSystem.name))],
            "tank_purposes": list(TANK_PURPOSE_OPTIONS), "fish_sexes": list(FISH_SEX_OPTIONS),
            "fish_statuses": list(FISH_STATUS_OPTIONS),
        }
    for module in stock_svc.list_modules(session):
        mv = stock_svc.view(module)
        out["databases"].append({
            "kind": module.kind, "key": module.key, "label": mv.label, "code_prefix": mv.s.get("code_prefix") or "",
            "purposes": [p["key"] for p in mv.purposes],
            "racks": [n for (n,) in session.execute(select(StockRack.name).where(StockRack.module_id_fk == module.id)
                                                    .order_by(StockRack.name))]})
    for module in org_svc.list_modules(session):
        mv = org_svc.view(module)
        fields = {}
        for entity in ("organism", "housing", "line", "cohort", "cross"):
            rows = org_svc.fields_for(session, module.id, entity)
            if rows:
                fields["animal" if entity == "organism" else entity] = [
                    {"key": f.key, "label": f.label, "type": f.field_type} for f in rows]
        out["databases"].append({
            "kind": "organism", "key": module.key, "label": mv.label,
            "nouns": {"animal": mv.organism_noun, "housing": mv.housing_noun},
            "statuses": list(mv.statuses), "sexes": list(mv.sexes), "housing_purposes": list(mv.housing_purposes),
            "fields": fields,
            "readings": [{"key": m["key"], "label": m.get("label", ""), "unit": m.get("unit", "")}
                         for m in org_svc.measurement_metrics(mv)],
            "locations": [n for (n,) in session.execute(select(OrgLocation.name)
                                                        .where(OrgLocation.module_id_fk == module.id)
                                                        .order_by(OrgLocation.name))]})
    out["databases"] += _inventory_vocabulary(session)
    if features.get("plasmids", True):
        from .models import PlasmidBox
        out["plasmid_boxes"] = [n for (n,) in session.execute(select(PlasmidBox.name).order_by(PlasmidBox.name))]
    if features.get("calendar", True):
        from .models import Equipment
        out["instruments"] = [n for (n,) in session.execute(select(Equipment.name).where(Equipment.active.is_(True))
                                                            .order_by(Equipment.name))]
    if features.get("notebook", True):
        from . import access
        from .models import NotebookTemplate
        out["notebook_templates"] = [n for (n,) in session.execute(
            select(NotebookTemplate.title).where(or_(NotebookTemplate.owner_username == access.username(),
                                                     NotebookTemplate.lab.is_(True)))
            .order_by(NotebookTemplate.title))]
    return out


def _inventory_vocabulary(session) -> list[dict]:
    """Each inventory this person can open: its key, kind, statuses, the
    categories it offers, its own columns and its boxes."""
    from . import inventory_service as isvc
    from .models import InventoryRack
    out = []
    for module in isvc.list_modules(session):
        if not lab.can_see(module):
            continue
        mv = isvc.view(module)
        out.append({"kind": "inventory", "inventory_kind": module.kind, "key": module.key, "label": module.label,
                    "noun": mv.item_noun, "statuses": list(mv.statuses), "categories": list(mv.categories),
                    "fields": [{"key": f["key"], "label": f["label"], "type": f["type"]} for f in mv.fields],
                    "required": list(mv.required),
                    "boxes": [n for (n,) in session.execute(select(InventoryRack.name)
                                                            .where(InventoryRack.module_id_fk == module.id)
                                                            .order_by(InventoryRack.name))]})
    return out


# ---------------------------------------------------------------- due

def due(session, days: int) -> list[dict]:
    from . import home_layouts
    from .app import builtin_labels, zebrafish_home_summary
    today = date.today()
    items, tracks = home_layouts.build_agenda(session, today, days, lab.request_features(),
                                              zebrafish_home_summary(),
                                              colony_label=builtin_labels().get("colony", "Mouse colony"))
    out = []
    for it in items:
        group, name, _url = tracks.get(it["track"], ("", "", ""))
        out.append({"due": it["due"].isoformat(), "status": it["status"], "title": it["title"],
                    "detail": it.get("detail", ""), "database": it.get("database") or group, "list": name,
                    "where": (it.get("loc") or {}).get("text", ""), "owner": it.get("owner", ""),
                    "ref": it.get("ref"), **({"mice": it["mice"]} if it.get("mice") else {}),
                    **({"litter": it["litter"]} if it.get("litter") else {}),
                    "propose": it.get("propose"),
                    "url": request.host_url.rstrip("/") + it["url"] if it.get("url") else ""})
    out += _experiment_due(session, today, today + timedelta(days=days))
    out.sort(key=lambda i: i["due"])
    return out


def _experiment_due(session, today: date, until: date) -> list[dict]:
    """Planned experiment steps not yet recorded, from a week overdue: the
    experiments this person may record in."""
    from flask import url_for
    from . import access
    from .experiment_steps import day_date, safe_days
    out = []
    for exp in session.scalars(select(Experiment).where(Experiment.status == "active",
                                                        Experiment.start_date.is_not(None))):
        if not access.can_edit_experiment(exp):
            continue
        for step in session.scalars(select(ExperimentStep).where(ExperimentStep.experiment_id_fk == exp.id)):
            done = {r.day for r in step.records}
            for day in safe_days(step):
                on = day_date(exp.start_date, day)
                if day in done or on is None or on > until or on < today - timedelta(days=7):
                    continue
                ref = {"kind": "experiment_step", "id": step.id}
                out.append({"due": on.isoformat(),
                            "status": "overdue" if on < today else ("today" if on == today else "soon"),
                            "title": f"Day {day}: {step.agent or step.kind}" + (f" {step.dose}" if step.dose else ""),
                            "detail": step.treatment_group or "", "database": "Experiments", "list": exp.name,
                            "where": "", "owner": exp.owner_username or "", "ref": ref,
                            "propose": {"action": "experiment_step_done", "target": ref, "fields": {"day": day}},
                            "url": request.host_url.rstrip("/") + url_for("experiment_detail", experiment_id=exp.id)})
    return out


# ---------------------------------------------------------------- search, history, lineage

# The search's own words for what it found, as references name them.
SEARCH_KINDS = {"page": "notebook_page", "vial": "stock_unit", "fish-line": "fish_line", "organism": "org_animal"}
# Where a match in a record's text is, for the snippet around it.
SNIPPET_TEXT = {"mouse": (MouseRecord, ("note",)), "cage": (CageRecord, ("notes",)),
                "litter": (LitterRecord, ("notes",)), "experiment": (Experiment, ("description", "treatment_plan")),
                "strain": (StrainRecord, ("description",)), "stock_unit": (StockUnit, ("notes",)),
                "tank": (TankRecord, ("notes",)), "clutch": (ClutchRecord, ("notes",))}
SNIPPET_AROUND = 90


def _snippet(text: str, q: str) -> str:
    """The words around the first place `q` is in `text`, on one line."""
    flat = " ".join((text or "").split())
    at = flat.lower().find(q.lower())
    if at < 0:
        return ""
    start, end = max(0, at - SNIPPET_AROUND), min(len(flat), at + len(q) + SNIPPET_AROUND)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


def search(session, q: str, limit: int = 5) -> list[dict]:
    """The app's own search (the ⌘K palette's, app.search_results), as the
    person: records in the databases they can open and notebook pages they
    may read, each with its reference, a snippet and its address."""
    from .app import search_results
    from .models import InventoryItem, NotebookPage, PlasmidRecord
    text_of = {**SNIPPET_TEXT, "notebook_page": (NotebookPage, ("body",)),
               "plasmid": (PlasmidRecord, ("notes",)), "inventory_item": (InventoryItem, ("notes",))}
    q, results = search_results(q, limit)
    base = request.host_url.rstrip("/")
    out = []
    for r in results:
        ref = r.get("ref") or {"kind": SEARCH_KINDS.get(r["type"], r["type"]), "id": r["id"]}
        snippet = ""
        model, columns = text_of.get(ref["kind"], (None, ()))
        row = session.get(model, ref["id"]) if model is not None else None
        for column in columns if row is not None else ():
            snippet = _snippet(getattr(row, column, ""), q)
            if snippet:
                break
        out.append({"kind": ref["kind"], "ref": ref, "title": r["label"],
                    "snippet": snippet or r.get("sublabel", ""), "url": base + r["url"]})
    return out


# What history and lineage may be asked about: kind → (model, its table in
# the change history, what it belongs to: a function or a kind of database).
RECORD_KINDS = {
    "mouse": (MouseRecord, "mice", "colony"), "cage": (CageRecord, "mouse_cages", "colony"),
    "litter": (LitterRecord, "litters", "colony"), "strain": (StrainRecord, "strains", "colony"),
    "tank": (TankRecord, "tanks", "zebrafish"), "clutch": (ClutchRecord, "clutches", "zebrafish"),
    "fish_line": (FishLine, "fish_lines", "zebrafish"),
    "stock_unit": (StockUnit, "stock_units", "stocks"),
    "org_animal": (Organism, "organisms", "organisms"), "org_housing": (OrgHousing, "organism_housing", "organisms"),
    "org_line": (OrgLine, "organism_lines", "organisms"), "org_cohort": (OrgCohort, "organism_cohorts", "organisms"),
    "org_cross": (OrgCross, "organism_crosses", "organisms"),
    "experiment": (Experiment, "experiments", "experiments"),
}


def _record_kinds() -> dict:
    from .models import FishRecord, InventoryItem, PlasmidRecord
    return {**RECORD_KINDS, "fish": (FishRecord, "fish", "zebrafish"),
            "plasmid": (PlasmidRecord, "plasmids", "plasmids"),
            "inventory_item": (InventoryItem, "inventory_items", "inventories")}


def _visible(session, home: str, row) -> bool:
    """May this person open the database `row` is in (app/lab.py)?"""
    from .models import InventoryModule, OrganismModule, StockModule
    if home in ("colony", "zebrafish", "plasmids"):
        return lab.request_features().get(home, True)
    if home == "experiments":
        from .experiments import place_for
        return place_for(session, row.db or "colony") is not None
    model = {"stocks": StockModule, "organisms": OrganismModule, "inventories": InventoryModule}[home]
    module = session.get(model, row.module_id_fk)
    return module is not None and lab.can_see(module)


def record(session, kind: str, ref):
    """The record a reference ({"kind", "id"}) or what the lab writes on it
    (mouse 1043, cage 88, FV12, plasmid 14) names, if this person may open
    its database; a LookupError in words otherwise."""
    from . import actions
    from .models import PlasmidRecord
    kinds = _record_kinds()
    if kind not in kinds:
        raise LookupError(f"No kind {kind}; kinds are {', '.join(sorted(kinds))}.")
    model, _table, home = kinds[kind]
    row = None
    if isinstance(ref, dict):
        row = session.get(model, ref["id"]) if ref.get("kind") == kind and isinstance(ref.get("id"), int) else None
    elif kind == "plasmid" and str(ref).strip().lstrip("#").isdigit():
        row = session.scalar(select(PlasmidRecord).where(PlasmidRecord.plasmid_id == int(str(ref).strip().lstrip("#"))))
    elif kind in actions.KINDS:
        try:
            row = actions.find(session, kind, ref)
        except actions.ActionError as e:
            raise LookupError(str(e)) from None
    if row is None or not _visible(session, home, row):
        shown = f"#{ref['id']}" if isinstance(ref, dict) else str(ref)
        raise LookupError(f"There is no {kind.replace('_', ' ')} {shown} that you can open.")
    return row


def notebook_page(session, ref):
    """A notebook page this person may read, by its reference or title."""
    from . import actions
    from .lab_notebook import role_for
    from .models import NotebookPage
    row = None
    if isinstance(ref, dict):
        row = session.get(NotebookPage, ref["id"]) if ref.get("kind") == "notebook_page" and isinstance(
            ref.get("id"), int) else None
    else:
        try:
            row = actions.find(session, "notebook_page", ref)
        except actions.ActionError as e:
            raise LookupError(str(e)) from None
    if row is None or not lab.request_features().get("notebook", True) or role_for(session, row) is None:
        shown = f"#{ref['id']}" if isinstance(ref, dict) else str(ref)
        raise LookupError(f"There is no notebook page {shown} that you can open.")
    return row


def history(session, kind: str, row, limit: int = 50) -> dict:
    """A record's change history (audit_log), newest first. An admin reads
    everyone's changes, as on the Audit log; anyone else their own, as on
    Batch history."""
    import json as _json
    from . import access
    from .models import AuditEntry, BatchRecord
    table = _record_kinds()[kind][1]
    stmt = select(AuditEntry).where(AuditEntry.table_name == table, AuditEntry.record_id == row.id)
    everyone = access.is_admin()
    if not everyone:
        stmt = stmt.where(AuditEntry.changed_by == access.username())
    rows = session.scalars(stmt.order_by(AuditEntry.id.desc()).limit(limit)).all()
    batches = {b.id: b.description for b in session.scalars(select(BatchRecord).where(
        BatchRecord.id.in_({e.batch_id_fk for e in rows if e.batch_id_fk})))} if rows else {}

    def plain(value):
        if isinstance(value, dict) and ("__d__" in value or "__dt__" in value):
            return value.get("__d__") or value.get("__dt__")
        return value

    entries = []
    for e in rows:
        try:
            payload = _json.loads(e.changes_json) if e.changes_json else {}
        except ValueError:
            payload = {}
        changes = {field: [plain(v) for v in pair] for field, pair in (payload.get("changes") or {}).items()}
        entries.append({"at": e.changed_at.isoformat(timespec="seconds") + "Z", "by": e.changed_by,
                        "action": e.action, "changes": changes, "details": e.details or "",
                        "batch": batches.get(e.batch_id_fk, "")})
    return {"kind": kind, "ref": {"kind": kind, "id": row.id}, "entries": entries, "everyone": everyone,
            "note": "" if everyone else "Only your own changes: the whole change history is for admins (the Audit log)."}


def _first_number(info: str) -> int | None:
    """The mouse a litter's father or mother field names: its first number
    ("M3 ♂" is mouse 3), as services._parent_label reads it."""
    found = re.search(r"\d+", info or "")
    return int(found.group(0)) if found else None


def mouse_lineage(session, m: MouseRecord, generations: int = 2) -> dict:
    """A mouse's parents (and theirs, `generations` up), its litter and
    littermates, and the litters it fathered or mothered with their pups.
    Parents are what its litter's Father and Mother say."""
    def node(x: MouseRecord) -> dict:
        return {"mouse": x.mouse_id, "ref": {"kind": "mouse", "id": x.id}, "sex": x.gender or "",
                "genotype": x.genotype or "",
                "born": x.litter.date_of_birth.isoformat() if x.litter and x.litter.date_of_birth else None,
                "alive": x.date_of_death is None, "cage": x.cage.cage_id if x.cage else None}

    def parents(x: MouseRecord, depth: int, seen: set) -> dict:
        out = {}
        litter = x.litter
        for side, info in (("father", litter.father_info if litter else ""),
                           ("mother", litter.mother_info if litter else "")):
            number = _first_number(info)
            parent = session.scalar(select(MouseRecord).where(MouseRecord.mouse_id == number)) if number else None
            if parent is None:
                out[side] = {"written": info} if info else None
            elif parent.id in seen or depth <= 1:
                out[side] = node(parent)
            else:
                out[side] = {**node(parent), "parents": parents(parent, depth - 1, seen | {parent.id})}
        return out

    offspring = []
    number = str(m.mouse_id)
    for lt in session.scalars(select(LitterRecord).where(or_(LitterRecord.father_info.contains(number),
                                                             LitterRecord.mother_info.contains(number)))
                              .order_by(LitterRecord.date_of_birth, LitterRecord.id)):
        if m.mouse_id in (_first_number(lt.father_info), _first_number(lt.mother_info)):
            offspring.append({"litter": lt.litter_id, "ref": {"kind": "litter", "id": lt.id},
                              "born": lt.date_of_birth.isoformat() if lt.date_of_birth else None,
                              "pups_born": lt.total_pups, "father": lt.father_info, "mother": lt.mother_info,
                              "mice": [node(x) for x in sorted(lt.mice, key=lambda x: x.mouse_id)]})
    lt = m.litter
    return {"mouse": node(m), "parents": parents(m, generations, {m.id}),
            "litter": {"litter": lt.litter_id, "ref": {"kind": "litter", "id": lt.id},
                       "born": lt.date_of_birth.isoformat() if lt.date_of_birth else None, "pups_born": lt.total_pups,
                       "littermates": [node(x) for x in sorted(lt.mice, key=lambda x: x.mouse_id) if x.id != m.id]}
            if lt else None,
            "offspring": offspring,
            "note": "Parents are what the litter's Father and Mother say (the mouse number they name)."}


def plasmid_lineage(session, p) -> dict:
    """A plasmid's family as its page shows it: what it was made from, what
    was made from it (plasmids, and records in an inventory with a plasmid
    column, such as viruses), and the tree both ways."""
    from flask import url_for
    from . import inventory_service, plasmid_lineage as lineage
    base = request.host_url.rstrip("/")

    def plasmid(x) -> dict:
        return {"plasmid": x.plasmid_id, "ref": {"kind": "plasmid", "id": x.id}, "name": x.name or "",
                "url": base + url_for("plasmid_page", number=x.plasmid_id)}

    def tree(nodes: list) -> list:
        return [{**(plasmid(n["plasmid"]) if n["plasmid"] is not None else {"plasmid": None, "name": n["label"]}),
                 "role": n["role"], "method": n["method"], "children": tree(n["children"])} for n in nodes]

    family = lineage.tree(session, p)
    return {
        **plasmid(p),
        "made_from": [{**(plasmid(e["plasmid"]) if e["plasmid"] is not None else {"plasmid": None}),
                       "label": e["label"], "role": e["role"], "method": e["method"], "details": e["details"]}
                      for e in lineage.parents(session, p)],
        "used_to_make": [plasmid(c) for c in lineage.children(session, p)],
        "records_made_from_it": [{"database": m["module"].label, "number": m["item"].number,
                                  "ref": {"kind": "inventory_item", "id": m["item"].id},
                                  "name": m["item"].name or "", "status": m["item"].status or ""}
                                 for m in inventory_service.made_from_plasmid(session, p.plasmid_id)],
        "tree": {"up": tree(family["up"]), "down": tree(family["down"]), "generations": lineage.TREE_DEPTH},
    }
