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
              "cohort": ["org_cohort"], "experiment": ["experiment"], "page": ["notebook_page"]}


def _hit(kind: str, row, label: str, database: str, exact: bool) -> dict:
    return {"kind": kind, "id": row.id, "ref": {"kind": kind, "id": row.id}, "label": label,
            "database": database, "exact": exact}


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
    if len(words) > 1 and words[0].lower() in KIND_WORDS:
        narrowed = set(KIND_WORDS[words[0].lower()])
        kinds = (kinds & narrowed) if kinds else narrowed
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
        from .models import NotebookPage, NotebookTab
        stmt = accessible_filter(select(NotebookPage).join(NotebookTab, NotebookPage.tab_id_fk == NotebookTab.id)
                                 .where(_match(NotebookPage.title, q)).order_by(NotebookPage.updated_at.desc())
                                 .limit(PER_KIND))
        for p in session.scalars(stmt):
            out.append(_hit("notebook_page", p, f"Page {p.title}", "Notebook", p.title.lower() == q.lower()))
    out.sort(key=lambda h: not h["exact"])
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
    from .app import cage_purposes
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
        out["mouse_colony"] = {
            "statuses": sorted(set(MOUSE_STATUS_OPTIONS) | set(options.get("status", []))),
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
                    "detail": it.get("detail", ""), "database": group, "list": name,
                    "where": (it.get("loc") or {}).get("text", ""), "owner": it.get("owner", ""),
                    "ref": it.get("ref"), "propose": it.get("propose"),
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
