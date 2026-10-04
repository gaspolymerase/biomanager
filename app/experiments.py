"""Experiments on any database's animals: mice, zebrafish, flies, worms and
the lab's own organisms.

An experiment is on one database's animals (Experiment.db): the mouse
colony's mice (ExperimentMouse, as before), or zebrafish rows, fly vials,
worm plates or organisms (ExperimentSubject). Each has

- a readout, measured over its days: a mouse's body weight (MouseWeight,
  so it is on the mouse as well), or a length, a score, how many are alive
  of those at the start (ExperimentReading). The readout fits the animal:
  flies and worms have no body weight, so their usual one is survival, and
  a zebrafish's is survival too (length and a phenotype are there);
- manipulations, planned and recorded (app/experiment_steps.py), with the
  kinds the animal has: injections for a mouse, drug in the water or a heat
  shock for fish, drug in the food or a temperature shift for flies, RNAi
  feeding for worms.

The page (templates/experiment.html, static/experiment-page.js) is the
same for all: the experiment's details; a sheet of its animals that
switches between the treatments each got and the readout, editable in
place; the button that records a manipulation; and the readout over the
days as a chart, the manipulation days dashed, each saying what was done
when you point at it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, select

from . import access, i18n, lab
from .db import SessionLocal
from .i18n import gettext
from .models import (CageRecord, ClutchRecord, Experiment, ExperimentMouse, ExperimentReading, ExperimentSubject,
                     FishRecord, MouseRecord, MouseWeight, OrgCohort, Organism, StockUnit, TankRecord)

bp = Blueprint("experiments", __name__, url_prefix="/experiments")

STATUSES = ["active", "paused", "done", "cancelled"]

# ---------------------------------------------------------------- readouts

READOUTS = {
    "body_weight": {"label": "Body weight", "unit": "g", "kind": "value", "verb": "Weigh"},
    "tumour_volume": {"label": "Tumour volume", "unit": "mm³", "kind": "value", "verb": "Measure"},
    "score": {"label": "Clinical score", "unit": "", "kind": "value", "verb": "Score"},
    "length": {"label": "Standard length", "unit": "mm", "kind": "value", "verb": "Measure"},
    "survival": {"label": "Survival", "unit": "alive", "kind": "fraction", "verb": "Count"},
    "phenotype": {"label": "Phenotype", "unit": "affected", "kind": "fraction", "verb": "Score"},
    "eclosion": {"label": "Eclosed adults", "unit": "flies", "kind": "value", "verb": "Count"},
    "climbing": {"label": "Climbing", "unit": "climbed", "kind": "fraction", "verb": "Test"},
    "brood": {"label": "Brood size", "unit": "progeny", "kind": "value", "verb": "Count"},
    "paralysis": {"label": "Paralysis", "unit": "paralysed", "kind": "fraction", "verb": "Score"},
}
READOUT_CHOICES = {
    "mouse": ["body_weight", "tumour_volume", "score", "survival"],
    "fish": ["survival", "length", "phenotype"],
    "fly": ["survival", "eclosion", "climbing"],
    "worm": ["survival", "brood", "paralysis"],
    "organism": ["body_weight", "length", "survival", "score"],
}

# What can be done to each kind of animal: key → (label, icon). "reading"
# is a day of the readout ("weigh" in the first version of this).
KINDS = {
    "mouse": [("injection", "Injection", "syringe"), ("challenge", "Challenge", "virus"),
              ("treatment", "Treatment", "droplet"), ("surgery", "Surgery", "stethoscope"),
              ("behavior", "Behaviour", "gauge"), ("sample", "Sample", "vial"), ("imaging", "Imaging", "microscope")],
    "fish": [("immersion", "Drug in the water", "droplet"), ("microinjection", "Microinjection", "syringe"),
             ("heat_shock", "Heat shock", "temperature"), ("injury", "Injury or fin clip", "stethoscope"),
             ("feeding", "Feeding", "flask"), ("behavior", "Behaviour", "gauge"), ("imaging", "Imaging", "microscope"),
             ("sample", "Sample", "vial")],
    "fly": [("food", "Drug in the food", "flask"), ("temperature", "Temperature shift", "temperature"),
            ("starvation", "Starvation", "clock"), ("infection", "Infection", "virus"),
            ("irradiation", "Irradiation", "bolt"), ("transfer", "Flip to fresh food", "repeat"),
            ("behavior", "Behaviour", "gauge")],
    "worm": [("plate", "Drug on the plate", "flask"), ("rnai", "RNAi feeding", "dna"),
             ("temperature", "Temperature shift", "temperature"), ("heat_shock", "Heat shock", "temperature"),
             ("starvation", "Starvation", "clock"), ("infection", "Infection", "virus"),
             ("transfer", "Transfer to a fresh plate", "repeat")],
    "organism": [("injection", "Injection", "syringe"), ("treatment", "Treatment", "droplet"),
                 ("challenge", "Challenge", "virus"), ("surgery", "Surgery", "stethoscope"),
                 ("behavior", "Behaviour", "gauge"), ("sample", "Sample", "vial"), ("imaging", "Imaging", "microscope")],
}
READING_KIND = ("reading", "Readout day", "chart")
OTHER_KIND = ("other", "Other", "note")


def kinds_for(family: str) -> list[tuple[str, str, str]]:
    return [*KINDS.get(family, KINDS["organism"]), READING_KIND, OTHER_KIND]


def kind_info(family: str, key: str) -> tuple[str, str]:
    """(label, icon) of a manipulation kind; any kind a step already has
    keeps a label, whichever animal it is on."""
    if key == "weigh":
        key = "reading"
    for k, label, icon in kinds_for(family):
        if k == key:
            return label, icon
    for rows in KINDS.values():
        for k, label, icon in rows:
            if k == key:
                return label, icon
    return key.replace("_", " ").capitalize() or "Other", "note"


class NotYours(str):
    """set_reading's answer when the animal's own record is not this
    person's to change: skipped, unlike a value that is wrong."""


def is_reading(kind: str) -> bool:
    return kind in ("reading", "weigh")


# ---------------------------------------------------------------- databases

@dataclass
class Place:
    """The database an experiment is on, as far as experiments care."""
    key: str                 # colony, zebrafish, stocks:<key>, organisms:<key>
    label: str               # Mouse colony, Zebrafish, Drosophila…
    family: str              # mouse | fish | fly | worm | organism
    subject_kind: str        # mouse | fish | unit | organism
    noun: str
    nouns: str
    housing: str             # cage, tank, rack, housing noun
    icon: str
    list_url: str
    home_url: str = ""
    module: object = None
    mv: object = None


def place_for(session, key: str) -> Place | None:
    """None when there is no such database, or it's switched off or not
    this person's to see."""
    from . import organism_service, stock_service
    kind, _, sub = (key or "colony").partition(":")
    features = lab.features_on(session)
    if kind == "colony":
        if not features.get("colony", True):
            return None
        return Place("colony", lab.FEATURES["colony"].label, "mouse", "mouse", "mouse", "mice", "cage", "mouse",
                     url_for("colony", view="experiments"), url_for("colony"))
    if kind == "zebrafish":
        if not features.get("zebrafish", True):
            return None
        return Place("zebrafish", lab.FEATURES["zebrafish"].label, "fish", "fish", "fish row", "fish rows", "tank",
                     "fish", url_for("zebrafish", view="experiments"), url_for("zebrafish", view="fish"))
    if kind == "stocks":
        module = stock_service.get_module(session, sub)
        if module is None or not lab.can_see(module):
            return None
        mv = stock_service.view(module)
        family = "worm" if module.kind == "worm" else "fly"
        return Place(key, mv.label, family, "unit", mv.unit, mv.units, mv.rack_noun, module.icon or family,
                     url_for("stocks.module", key=module.key, view="experiments"),
                     url_for("stocks.module", key=module.key), module, mv)
    if kind == "organisms":
        module = organism_service.get_module(session, sub)
        if module is None or not lab.can_see(module):
            return None
        mv = organism_service.view(module)
        return Place(key, mv.label, "organism", "organism", mv.organism_noun, mv.organism_noun_plural,
                     mv.housing_noun, module.icon or "paw", url_for("organisms.module", key=module.key, view="experiments"),
                     url_for("organisms.module", key=module.key), module, mv)
    return None


def page_url(exp: Experiment) -> str:
    if (exp.db or "colony") == "colony":
        return url_for("experiment_detail", experiment_id=exp.id)
    return url_for("experiments.page", experiment_id=exp.id)


def readout_of(exp: Experiment, place: Place) -> dict:
    """The readout: the one chosen, or the database's usual one."""
    try:
        chosen = json.loads(exp.readout or "{}")
    except ValueError:
        chosen = {}
    if not isinstance(chosen, dict) or not chosen.get("key"):
        chosen = {"key": READOUT_CHOICES.get(place.family, ["survival"])[0]}
    preset = READOUTS.get(chosen["key"])
    if preset:
        return {"key": chosen["key"], **preset}
    kind = chosen.get("kind") if chosen.get("kind") in ("value", "fraction") else "value"
    return {"key": "custom", "label": str(chosen.get("label") or "Measurement")[:60],
            "unit": str(chosen.get("unit") or "")[:20], "kind": kind, "verb": "Record"}


def readout_choices(place: Place) -> list[dict]:
    return [{"key": k, **READOUTS[k]} for k in READOUT_CHOICES.get(place.family, READOUT_CHOICES["organism"])]


def weighs(exp: Experiment, place: Place) -> bool:
    """Whether the readout is the animals' weight, which doses per body
    weight are worked out from."""
    return readout_of(exp, place)["key"] == "body_weight"


# ---------------------------------------------------------------- the animals

@dataclass
class Subject:
    key: str                  # "mouse:12", "fish:3", "unit:7", "organism:40"
    kind: str
    id: int
    label: str
    sex: str = ""
    genotype: str = ""
    housing: str = ""
    group: str = ""
    start: int | None = None  # animals at the start (a group); 1 for one animal
    note: str = ""
    alive: bool = True
    can_edit: bool = True     # its own record may be changed (a mouse's weight)
    row: object = None        # the membership row
    record: object = None     # the animal's own record

    @property
    def card(self) -> str:
        """The anchor its cage or housing card's QR code opens (#cage-12…),
        for scanning in bench mode."""
        rec = self.record
        if self.kind == "mouse" and getattr(rec, "cage_id_fk", None):
            return f"cage-{rec.cage_id_fk}"
        if self.kind == "fish" and getattr(rec, "tank_id_fk", None):
            return f"tank-{rec.tank_id_fk}"
        if self.kind == "organism" and getattr(rec, "housing_id_fk", None):
            return f"unit-{rec.housing_id_fk}"
        return ""

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "sex": self.sex, "genotype": self.genotype,
                "housing": self.housing, "group": self.group, "start": self.start, "note": self.note,
                "alive": self.alive, "card": self.card}


def subjects(session, exp: Experiment, place: Place, group: str = "") -> list[Subject]:
    """The experiment's animals, in order; only one treatment group's when
    `group` is given."""
    from .app import can_edit_mouse, mouse_is_active
    out: list[Subject] = []
    if place.subject_kind == "mouse":
        for em in exp.memberships:
            m = em.mouse
            if m is None:
                continue
            out.append(Subject(f"mouse:{m.id}", "mouse", m.id, f"#{m.mouse_id}", m.gender or "", m.genotype or "",
                               m.cage.cage_id if m.cage else "", em.treatment_group or "", 1, em.note or "",
                               mouse_is_active(m), can_edit_mouse(m), em, m))
        out.sort(key=lambda s: s.record.mouse_id or 0)
    else:
        rows = session.scalars(select(ExperimentSubject).where(ExperimentSubject.experiment_id_fk == exp.id)
                               .order_by(ExperimentSubject.id)).all()
        by_kind: dict[str, list[int]] = {}
        for r in rows:
            by_kind.setdefault(r.subject_kind, []).append(r.subject_id)
        records = {}
        if by_kind.get("fish"):
            records.update({("fish", f.id): f for f in session.scalars(select(FishRecord).where(FishRecord.id.in_(by_kind["fish"])))})
        if by_kind.get("unit"):
            records.update({("unit", u.id): u for u in session.scalars(select(StockUnit).where(StockUnit.id.in_(by_kind["unit"])))})
        if by_kind.get("organism"):
            records.update({("organism", o.id): o for o in session.scalars(select(Organism).where(Organism.id.in_(by_kind["organism"])))})
        if by_kind.get("clutch"):
            records.update({("clutch", c.id): c for c in session.scalars(select(ClutchRecord).where(ClutchRecord.id.in_(by_kind["clutch"])))})
        if by_kind.get("cohort"):
            records.update({("cohort", c.id): c for c in session.scalars(select(OrgCohort).where(OrgCohort.id.in_(by_kind["cohort"])))})
        for r in rows:
            rec = records.get((r.subject_kind, r.subject_id))
            if rec is None:
                continue
            out.append(_describe(place, r, rec))
    wanted = (group or "").strip().lower()
    if wanted:
        out = [s for s in out if s.group.strip().lower() == wanted]
    return out


def _describe(place: Place, row: ExperimentSubject, rec) -> Subject:
    key = f"{row.subject_kind}:{rec.id}"
    common = {"group": row.treatment_group or "", "note": row.note or "", "row": row, "record": rec}
    if row.subject_kind == "fish":
        tank = rec.tank.tank_id if rec.tank else ""
        label = " · ".join(filter(None, [tank, rec.individual_id or (rec.line.name if rec.line else "")]))
        return Subject(key, "fish", rec.id, label or f"Fish {rec.id}", rec.sex or "", rec.genotype or "", tank,
                       start=row.start_count if row.start_count is not None else rec.count,
                       alive=rec.status == "alive", **common)
    if row.subject_kind == "clutch":
        # Embryos or larvae of one clutch, before they are fish rows in a tank.
        start = row.start_count if row.start_count is not None else (rec.larvae_count or rec.embryo_count or None)
        return Subject(key, "clutch", rec.id, f"Clutch {rec.clutch_id}", "", rec.line.name if rec.line else "",
                       f"fertilised {rec.date_of_fertilization.isoformat()}", start=start, alive=True, **common)
    if row.subject_kind == "cohort":
        start = row.start_count if row.start_count is not None else (rec.count_initial or rec.count_current or None)
        return Subject(key, "cohort", rec.id, rec.code, "", rec.line.name if rec.line else "", rec.stage or "",
                       start=start, alive=bool(rec.count_current), **common)
    if row.subject_kind == "unit":
        mv = place.mv
        from .stock_service import position_label
        where = " ".join(filter(None, [rec.rack.name if rec.rack else "", position_label(rec)]))
        return Subject(key, "unit", rec.id, mv.code(rec) if mv else f"#{rec.number}", "", rec.genotype or "", where,
                       start=row.start_count, alive=bool(rec.active), **common)
    mv = place.mv
    label = rec.code or f"{rec.count} {mv.organism_noun_plural if mv else ''}".strip()
    alive = mv.is_alive(rec) if mv else rec.death_on is None
    return Subject(key, "organism", rec.id, label, rec.sex or "", rec.genotype or "",
                   rec.housing.code if getattr(rec, "housing", None) else "",
                   start=row.start_count if row.start_count is not None else (rec.count or 1), alive=alive, **common)


def find_subject(session, exp, place, key: str) -> Subject | None:
    return next((s for s in subjects(session, exp, place) if s.key == key), None)


def candidates(session, exp: Experiment, place: Place) -> dict:
    """What can be added: {"groups": [(value, label, count)], "single": [(value, label)]}
    — a cage, tank, rack or housing of animals, or one."""
    from .app import can_edit_mouse, mouse_is_active
    have = {s.key for s in subjects(session, exp, place)}
    groups, single = [], []
    if place.subject_kind == "mouse":
        for c in session.scalars(select(CageRecord).order_by(CageRecord.cage_id)):
            n = sum(1 for m in c.mice if mouse_is_active(m) and f"mouse:{m.id}" not in have)
            if n:
                groups.append((c.cage_id, gettext("Cage %(cage)s", cage=c.cage_id), n))
        for m in session.scalars(select(MouseRecord).where(MouseRecord.date_of_death.is_(None)).order_by(MouseRecord.mouse_id)):
            if f"mouse:{m.id}" not in have and mouse_is_active(m) and can_edit_mouse(m):
                single.append((str(m.id), f"#{m.mouse_id} · {m.gender or '?'} · {m.genotype or gettext('no genotype')}"))
    elif place.subject_kind == "fish":
        for t in session.scalars(select(TankRecord).where(TankRecord.active.is_(True)).order_by(TankRecord.tank_id)):
            live = [f for f in t.fish if f.status == "alive" and f"fish:{f.id}" not in have]
            if live:
                groups.append((str(t.id), gettext("Tank %(tank)s", tank=t.tank_id), len(live)))
                single += [(f"fish:{f.id}", f"{t.tank_id} · {f.count} {f.sex or ''} {f.line.name if f.line else ''} {f.genotype or ''}".strip())
                           for f in live]
        recent = date.today().toordinal() - 60
        for c in session.scalars(select(ClutchRecord).order_by(ClutchRecord.date_of_fertilization.desc()).limit(200)):
            if f"clutch:{c.id}" not in have and c.date_of_fertilization.toordinal() >= recent:
                n = c.larvae_count or c.embryo_count
                single.append((f"clutch:{c.id}", " · ".join(filter(None, [
                    gettext("Clutch %(clutch)s", clutch=c.clutch_id), c.date_of_fertilization.isoformat(),
                    gettext("%(count)s larvae", count=n) if n else "", c.line.name if c.line else ""]))))
    elif place.subject_kind == "unit":
        units = session.scalars(select(StockUnit).where(StockUnit.module_id_fk == place.module.id,
                                                        StockUnit.active.is_(True)).order_by(StockUnit.number)).all()
        racks: dict = {}
        for u in units:
            if f"unit:{u.id}" in have:
                continue
            if u.rack is not None:
                racks.setdefault((u.rack.id, u.rack.name), []).append(u)
            single.append((str(u.id), f"{place.mv.code(u)} · {u.genotype or gettext('no genotype')}"))
        groups = [(str(rid), f"{i18n.translate_value(place.housing).capitalize()} {name}", len(us)) for (rid, name), us in sorted(racks.items(), key=lambda kv: kv[0][1])]
    else:
        from .models import OrgHousing
        rows = session.scalars(select(Organism).where(Organism.module_id_fk == place.module.id).order_by(Organism.id)).all()
        houses: dict = {}
        for o in rows:
            if f"organism:{o.id}" in have or not place.mv.is_alive(o):
                continue
            if o.housing_id_fk:
                houses.setdefault(o.housing_id_fk, []).append(o)
            single.append((f"organism:{o.id}", " · ".join(filter(None, [o.code or f"{o.count} {i18n.translate_value(place.nouns)}", o.sex, o.genotype]))))
        codes = {h.id: h.code for h in session.scalars(select(OrgHousing).where(OrgHousing.id.in_(list(houses) or [0])))}
        groups = [(str(hid), f"{i18n.translate_value(place.housing).capitalize()} {codes.get(hid, hid)}", len(v)) for hid, v in houses.items()]
        cohort_noun = i18n.translate_value((place.mv.cohort_noun if place.mv else "") or "cohort").capitalize()
        for c in session.scalars(select(OrgCohort).where(OrgCohort.module_id_fk == place.module.id).order_by(OrgCohort.code)):
            if f"cohort:{c.id}" not in have and c.count_current:
                single.append((f"cohort:{c.id}", f"{cohort_noun} {c.code} · {c.count_current}"))
    return {"groups": groups, "single": single}


# ---------------------------------------------------------------- readings

def readings(session, exp: Experiment, place: Place, subs: list[Subject] | None = None) -> dict[str, dict[date, float]]:
    """{subject key: {date: value}} for the readout."""
    subs = subjects(session, exp, place) if subs is None else subs
    out: dict[str, dict[date, float]] = {s.key: {} for s in subs}
    readout = readout_of(exp, place)
    if place.subject_kind == "mouse" and readout["key"] == "body_weight":
        ids = {s.id: s.key for s in subs}
        if ids:
            for w in session.scalars(select(MouseWeight).where(MouseWeight.mouse_id_fk.in_(list(ids)))):
                out[ids[w.mouse_id_fk]][w.weigh_date] = w.grams
        return out
    for r in session.scalars(select(ExperimentReading).where(ExperimentReading.experiment_id_fk == exp.id,
                                                             ExperimentReading.readout_key == readout["key"])):
        if r.subject in out:
            out[r.subject][r.read_on] = r.value
    return out


def weight_on(values: dict[date, float], day: date | None) -> tuple[float | None, date | None]:
    """The latest value on or before `day` (the latest of all without one)."""
    usable = sorted(d for d in values if day is None or d <= day)
    return (values[usable[-1]], usable[-1]) if usable else (None, None)


def set_reading(session, exp: Experiment, place: Place, subject: Subject, on: date, raw: str) -> str | None:
    """Store (or, blank, remove) one day's readout; a sentence when it can't."""
    readout = readout_of(exp, place)
    raw = (raw or "").strip().replace(",", ".")
    value = None
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return gettext("“%(value)s” isn't a number.", value=raw)
        if value < 0:
            return gettext("A readout can't be below zero.")
        if readout["kind"] == "fraction" and subject.start is not None and value > subject.start:
            return gettext("%(label)s started with %(start)s: %(value)s is more than that.",
                           label=subject.label, start=subject.start, value=f"{value:g}")
        if readout["key"] == "body_weight" and place.subject_kind == "mouse" and not 0 < value < 200:
            return gettext("%(value)s g isn't a mouse's weight.", value=f"{value:g}")
    if place.subject_kind == "mouse" and readout["key"] == "body_weight":
        if not subject.can_edit:
            return NotYours(f"{subject.label}: {access.reason_denied(subject.record)}")
        row = session.scalar(select(MouseWeight).where(MouseWeight.mouse_id_fk == subject.id, MouseWeight.weigh_date == on))
        if value is None:
            if row is not None:
                session.delete(row)
        elif row is None:
            session.add(MouseWeight(mouse_id_fk=subject.id, weigh_date=on, grams=value,
                                    notes=f"{exp.name}"[:200], recorded_by=g.user.username))
        else:
            row.grams, row.recorded_by = value, g.user.username
        return None
    row = session.scalar(select(ExperimentReading).where(
        ExperimentReading.experiment_id_fk == exp.id, ExperimentReading.subject == subject.key,
        ExperimentReading.readout_key == readout["key"], ExperimentReading.read_on == on))
    if value is None:
        if row is not None:
            session.delete(row)
    elif row is None:
        session.add(ExperimentReading(experiment_id_fk=exp.id, subject=subject.key, readout_key=readout["key"],
                                      read_on=on, value=value, recorded_by=g.user.username))
    else:
        row.value, row.recorded_by = value, g.user.username
    return None


def readout_table(session, exp: Experiment, place: Place, subs: list[Subject] | None = None) -> dict:
    """The readout by animal and date, each date's day number, and each
    value as a percentage: of the animal's first value, or for a count of
    how many there were at the start."""
    subs = subjects(session, exp, place) if subs is None else subs
    series = readings(session, exp, place, subs)
    readout = readout_of(exp, place)
    dates = sorted({d for values in series.values() for d in values})
    start = exp.start_date
    rows = []
    for s in subs:
        values = [series[s.key].get(d) for d in dates]
        if readout["kind"] == "fraction":
            pct = [round(v / s.start * 100, 1) if (v is not None and s.start) else None for v in values]
        else:
            first = next((v for v in values if v is not None), None)
            pct = [round(v / first * 100, 1) if (v is not None and first) else None for v in values]
        rows.append({"key": s.key, "mouse": s.id if s.kind == "mouse" else None,
                     "mouse_id": s.label.lstrip("#") if s.kind == "mouse" else s.label, "label": s.label,
                     "group": s.group, "sex": s.sex, "start": s.start, "values": values, "pct": pct})
    table = {"dates": [d.isoformat() for d in dates],
             "days": [((d - start).days + 1) if start else None for d in dates], "rows": rows, "readout": readout}
    from . import exp_stats
    table["stats"] = {"raw": exp_stats.by_day(table), "pct": exp_stats.by_day(table, percent=True)}
    return table


# ---------------------------------------------------------------- the page's data

def payload(session, exp: Experiment, place: Place) -> dict:
    from . import experiment_steps as xs
    subs = subjects(session, exp, place)
    readout = readout_of(exp, place)
    groups = sorted({s.group for s in subs if s.group})
    return {
        "id": exp.id, "name": exp.name, "status": exp.status, "owner": exp.owner_username,
        "db": place.key, "db_label": place.label, "family": place.family, "noun": place.noun, "nouns": place.nouns,
        "housing": place.housing, "editable": access.can_edit_experiment(exp),
        "start_date": exp.start_date.isoformat() if exp.start_date else "",
        "readout": readout, "weighs": readout["key"] == "body_weight",
        "subjects": [s.as_dict() for s in subs], "groups": groups,
        "kinds": [{"key": k, "label": label, "icon": icon} for k, label, icon in kinds_for(place.family)],
        "steps": [xs.step_dict(st, place.family, session) for st in exp.steps],
        "schedule": xs.schedule(session, exp, place, subs),
        "table": readout_table(session, exp, place, subs),
        "regimens": xs.regimens_for(session, place.family),
        "reagents": reagent_choices(session),
        "sample_inventories": sample_inventories(session),
    }


def reagent_choices(session, limit: int = 400) -> list[dict]:
    """Inventory items to say a manipulation used: the lab's reagents,
    antibodies and the like, with their lot, newest first."""
    from . import inventory_service as inv
    from .models import InventoryItem
    out = []
    for module in inv.list_modules(session):
        if module.kind in ("orders", "samples") or not lab.can_see(module):
            continue
        mv = inv.view(module)
        rows = session.scalars(select(InventoryItem).where(InventoryItem.module_id_fk == module.id)
                               .order_by(InventoryItem.id.desc()).limit(limit)).all()
        for item in rows:
            if mv.is_available(item.status) is False:
                continue
            out.append({"id": item.id, "label": " · ".join(filter(None, [item.name, f"lot {item.lot}" if item.lot else "",
                                                                         mv.label])),
                        "expires": item.expires_on.isoformat() if item.expires_on else ""})
    return out[:limit]


def sample_inventories(session) -> list[dict]:
    """Inventories a sample record can go in: those with a Source column."""
    from . import inventory_service as inv
    out = []
    for module in inv.list_modules(session):
        if not lab.can_see(module):
            continue
        mv = inv.view(module)
        if any(f["type"] == "source" for f in mv.fields):
            out.append({"key": module.key, "label": mv.label})
    return out


def _load(session, experiment_id: int) -> tuple[Experiment, Place]:
    exp = session.get(Experiment, experiment_id)
    if exp is None:
        abort(404)
    place = place_for(session, exp.db or "colony")
    if place is None:
        abort(404)
    return exp, place


def _refuse(exp: Experiment):
    if access.can_edit_experiment(exp):
        return None
    return jsonify({"ok": False, "error": access.denied_message("experiment", exp.owner_username)}), 403


@bp.before_request
def require_login():
    if g.get("user") is None:
        return redirect(url_for("login", next=request.path))
    return None


# ---------------------------------------------------------------- pages

def render_page(experiment_id: int):
    """The experiment page, for every database (the colony's route calls this)."""
    from . import experiment_steps as xs
    with SessionLocal() as s:
        exp = s.get(Experiment, experiment_id)
        if exp is None:
            flash(gettext("Experiment not found."), "error")
            return redirect(url_for("colony", view="experiments"))
        place = place_for(s, exp.db or "colony")
        if place is None:
            abort(404)
        data = payload(s, exp, place)
        info = {"id": exp.id, "name": exp.name, "description": exp.description, "treatment_plan": exp.treatment_plan,
                "status": exp.status, "owner": exp.owner_username, "editable": data["editable"],
                "start_date": data["start_date"], "end_date": exp.end_date.isoformat() if exp.end_date else ""}
        return render_template(
            "experiment.html", experiment=info, place=place, data=data, statuses=STATUSES,
            readouts=readout_choices(place), readout=data["readout"], add=candidates(s, exp, place) if data["editable"] else None,
            notebook_on=lab.feature_on(s, "notebook"), notebook_page=xs.my_notebook_page(s, exp.id),
            back_url=place.list_url)


@bp.get("/<int:experiment_id>")
def page(experiment_id: int):
    with SessionLocal() as s:
        exp = s.get(Experiment, experiment_id)
        if exp is not None and (exp.db or "colony") == "colony":
            return redirect(url_for("experiment_detail", experiment_id=experiment_id))
    return render_page(experiment_id)


@bp.get("/<int:experiment_id>/data.json")
def data_json(experiment_id: int):
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        return jsonify({"ok": True, **payload(s, exp, place)})


def tab_context(session, place: Place) -> dict:
    """What a database's Experiments tab shows (templates/_experiments_tab.html):
    its experiments, newest first by status, and the readouts a new one can
    follow. The zebrafish, fly and worm, and organism pages pass it on as
    `experiments_tab`; the mouse colony has its own tab."""
    rows = session.scalars(select(Experiment).where(Experiment.db == place.key)
                           .order_by(Experiment.status, Experiment.created_at.desc())).all()
    counts = dict(session.execute(
        select(ExperimentSubject.experiment_id_fk, func.count(ExperimentSubject.id))
        .where(ExperimentSubject.experiment_id_fk.in_([e.id for e in rows]))
        .group_by(ExperimentSubject.experiment_id_fk)).all()) if rows else {}
    cards = [{"id": e.id, "name": e.name, "description": e.description, "status": e.status,
              "owner": e.owner_username, "editable": access.can_edit_experiment(e), "count": counts.get(e.id, 0),
              "start_date": i18n.strftime(e.start_date, "%b %d, %Y") if e.start_date else "",
              "readout": readout_of(e, place)["label"], "url": url_for("experiments.page", experiment_id=e.id)}
             for e in rows]
    # A new experiment can start with a whole tank, rack or housing of them,
    # as a mouse one starts with a cage's mice.
    groups = candidates(session, Experiment(id=0, db=place.key), place)["groups"]
    return {"place": place, "cards": cards, "readouts": readout_choices(place), "groups": groups,
            "today": date.today().isoformat(), "statuses": STATUSES}


def tab_count(session, db_key: str) -> int:
    """How many experiments a database has, for the count on its tab."""
    return session.scalar(select(func.count(Experiment.id)).where(Experiment.db == db_key)) or 0


@bp.get("/in/<db_key>")
def index(db_key: str):
    """A database's experiments are on its Experiments tab; this address
    (from before the tab, in bookmarks and notebook links) goes there."""
    if db_key == "colony":
        return redirect(url_for("colony", view="experiments"))
    with SessionLocal() as s:
        place = place_for(s, db_key)
        if place is None:
            abort(404)
        return redirect(place.list_url)


@bp.post("/in/<db_key>/create")
def create(db_key: str):
    with SessionLocal() as s:
        place = place_for(s, db_key)
        if place is None:
            abort(404)
        form = request.form
        from .services import parse_date
        readout = form.get("readout") or ""
        exp = Experiment(name=(form.get("name") or "").strip()[:200] or "Untitled experiment",
                         description=(form.get("description") or "").strip(),
                         treatment_plan=(form.get("treatment_plan") or "").strip(), status="active",
                         owner_username=g.user.username, start_date=parse_date(form.get("start_date")),
                         db=place.key, readout=json.dumps({"key": readout}) if readout in READOUTS else "")
        s.add(exp)
        s.flush()
        start_with = (form.get("from_group") or "").strip()
        if start_with:
            for kind, rec, start in _records_to_add(s, place, "group", start_with):
                s.add(ExperimentSubject(experiment_id_fk=exp.id, subject_kind=kind, subject_id=rec.id,
                                        start_count=start))
        s.commit()
        return redirect(page_url(exp))


# ---------------------------------------------------------------- changing it

@bp.post("/<int:experiment_id>/update")
def update(experiment_id: int):
    """The details, autosaved one field at a time, and the readout."""
    from .services import parse_date
    form = request.form
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        refused = _refuse(exp)
        if refused:
            return refused
        start = parse_date(form.get("start_date")) if "start_date" in form else exp.start_date
        end = parse_date(form.get("end_date")) if "end_date" in form else exp.end_date
        status = (form.get("status") or "").strip().lower() if "status" in form else exp.status
        if start and end and end < start:
            return jsonify({"ok": False, "error": gettext("The end date (%(end)s) is before the start date (%(start)s).",
                                                          end=end.isoformat(), start=start.isoformat())}), 409
        if status and status not in STATUSES:
            return jsonify({"ok": False, "error": gettext("“%(status)s” is not an experiment status.", status=status)}), 409
        for name in ("name", "description", "treatment_plan"):
            if name in form:
                value = (form.get(name) or "").strip() if name == "name" else form.get(name, "")
                if name != "name" or value:
                    setattr(exp, name, value[:200] if name == "name" else value)
        if "readout" in form:
            key = form.get("readout") or ""
            if key == "custom":
                kind = form.get("readout_kind") if form.get("readout_kind") in ("value", "fraction") else "value"
                exp.readout = json.dumps({"key": "custom", "label": (form.get("readout_label") or "Measurement")[:60],
                                          "unit": (form.get("readout_unit") or "")[:20], "kind": kind})
            elif key in READOUTS:
                exp.readout = json.dumps({"key": key})
        exp.status, exp.start_date, exp.end_date = status or "active", start, end
        exp.updated_at = datetime.utcnow()
        s.commit()
        return jsonify({"ok": True, **payload(s, exp, place)})


@bp.post("/<int:experiment_id>/subjects/add")
def add_subjects(experiment_id: int):
    """A cage, tank, rack or housing of animals ("group"), or one ("one")."""
    from .app import can_edit_mouse, mouse_is_active
    data = request.get_json(silent=True) or request.form.to_dict()
    how, value, group = data.get("how"), str(data.get("value") or ""), str(data.get("group") or "").strip()[:80]
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        refused = _refuse(exp)
        if refused:
            return refused
        have = {x.key for x in subjects(s, exp, place)}
        added = skipped = 0
        if place.subject_kind == "mouse":
            if how == "group":
                cage = s.scalar(select(CageRecord).where(CageRecord.cage_id == value))
                mice = list(cage.mice) if cage else []
            else:
                mice = [m for m in [s.get(MouseRecord, int(value))] if m] if value.isdigit() else []
            for m in mice:
                if f"mouse:{m.id}" in have or not mouse_is_active(m):
                    continue
                if not can_edit_mouse(m):
                    skipped += 1
                    continue
                s.add(ExperimentMouse(experiment_id_fk=exp.id, mouse_id_fk=m.id, treatment_group=group))
                added += 1
        else:
            records = _records_to_add(s, place, how, value)
            for kind, rec, start in records:
                key = f"{kind}:{rec.id}"
                if key in have:
                    continue
                s.add(ExperimentSubject(experiment_id_fk=exp.id, subject_kind=kind, subject_id=rec.id,
                                        treatment_group=group, start_count=start))
                have.add(key)
                added += 1
        s.commit()
        s.refresh(exp)
        if added:
            message = gettext("Added %(count)s %(nouns)s.", count=added,
                              nouns=i18n.translate_value(place.noun if added == 1 else place.nouns))
        else:
            message = gettext("No %(nouns)s to add there.", nouns=i18n.translate_value(place.nouns))
        if skipped:
            message += " " + gettext("%(count)s skipped: not yours to change.", count=skipped)
        return jsonify({"ok": True, "message": message, **payload(s, exp, place),
                        "add": candidates(s, exp, place)})


def _records_to_add(session, place: Place, how: str, value: str) -> list[tuple[str, object, int | None]]:
    """(kind, record, animals at the start) for the fish rows of a tank, the
    vials in a rack, the animals in a housing, or one ("fish:12",
    "clutch:3", "organism:40", "cohort:2", or a bare id)."""
    kind, _, raw = value.rpartition(":")
    if not raw.isdigit():
        return []
    ident = int(raw)
    if how != "group" and kind == "clutch" and place.subject_kind == "fish":
        c = session.get(ClutchRecord, ident)
        return [("clutch", c, c.larvae_count or c.embryo_count or None)] if c else []
    if how != "group" and kind == "cohort" and place.subject_kind == "organism":
        c = session.get(OrgCohort, ident)
        return [("cohort", c, c.count_initial or c.count_current or None)] if c and c.module_id_fk == place.module.id else []
    return [(place.subject_kind, rec, start) for rec, start in _plain_records(session, place, how, ident)]


def _plain_records(session, place: Place, how: str, ident: int) -> list[tuple[object, int | None]]:
    if place.subject_kind == "fish":
        if how == "group":
            tank = session.get(TankRecord, ident)
            return [(f, f.count) for f in (tank.fish if tank else []) if f.status == "alive"]
        f = session.get(FishRecord, ident)
        return [(f, f.count)] if f else []
    if place.subject_kind == "unit":
        query = select(StockUnit).where(StockUnit.module_id_fk == place.module.id, StockUnit.active.is_(True))
        query = query.where(StockUnit.rack_id_fk == ident) if how == "group" else query.where(StockUnit.id == ident)
        return [(u, None) for u in session.scalars(query)]
    query = select(Organism).where(Organism.module_id_fk == place.module.id)
    query = query.where(Organism.housing_id_fk == ident) if how == "group" else query.where(Organism.id == ident)
    return [(o, o.count or 1) for o in session.scalars(query) if place.mv.is_alive(o)]


@bp.post("/<int:experiment_id>/subjects/<path:key>/update")
def update_subject(experiment_id: int, key: str):
    data = request.get_json(silent=True) or request.form.to_dict()
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        refused = _refuse(exp)
        if refused:
            return refused
        subject = find_subject(s, exp, place, key)
        if subject is None:
            return jsonify({"ok": False, "error": gettext("That one is no longer in the experiment.")}), 404
        row = subject.row
        if "group" in data:
            row.treatment_group = str(data.get("group") or "").strip()[:80]
        if "note" in data:
            row.note = str(data.get("note") or "").strip()[:200]
        if "start" in data and isinstance(row, ExperimentSubject):
            raw = str(data.get("start") or "").strip()
            if raw and not raw.isdigit():
                return jsonify({"ok": False, "error": gettext("“%(value)s” isn't a number of animals.", value=raw)}), 400
            row.start_count = int(raw) if raw else None
        s.commit()
        s.refresh(exp)
        return jsonify({"ok": True, **payload(s, exp, place)})


@bp.post("/<int:experiment_id>/subjects/<path:key>/remove")
def remove_subject(experiment_id: int, key: str):
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        refused = _refuse(exp)
        if refused:
            return refused
        subject = find_subject(s, exp, place, key)
        if subject is not None:
            s.delete(subject.row)
            s.commit()
            s.refresh(exp)
        return jsonify({"ok": True, **payload(s, exp, place), "add": candidates(s, exp, place)})


@bp.post("/<int:experiment_id>/readings")
def save_readings(experiment_id: int):
    """{"on": date, "values": {subject key: "24.5" or "" (remove)}}: one
    cell from the sheet, or a whole day."""
    data = request.get_json(silent=True) or {}
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        refused = _refuse(exp)
        if refused:
            return refused
        try:
            on = date.fromisoformat(str(data.get("on") or "")[:10])
        except ValueError:
            return jsonify({"ok": False, "error": gettext("Say which day it is for.")}), 400
        if on > date.today():
            return jsonify({"ok": False, "error": gettext("A readout can't be for a day still to come.")}), 400
        by_key = {x.key: x for x in subjects(s, exp, place)}
        problems = []
        for key, raw in (data.get("values") or {}).items():
            subject = by_key.get(key)
            if subject is None:
                continue
            problem = set_reading(s, exp, place, subject, on, str(raw))
            if problem:
                problems.append(problem)
        if problems and len(problems) == len(data.get("values") or {}):
            s.rollback()
            return jsonify({"ok": False, "error": " ".join(problems[:3])}), 400
        s.commit()
        s.refresh(exp)
        return jsonify({"ok": True, "problems": problems, **payload(s, exp, place)})


@bp.post("/<int:experiment_id>/delete")
def delete(experiment_id: int):
    from .app import log_delete
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        if not access.can_edit_experiment(exp):
            flash(access.denied_message("experiment", exp.owner_username), "error")
            return redirect(page_url(exp))
        for model in (ExperimentSubject, ExperimentReading):
            for row in s.scalars(select(model).where(model.experiment_id_fk == exp.id)):
                s.delete(row)
        log_delete(s, "experiments", exp.id, record_label=f"Experiment: {exp.name}", details=place.key)
        name = exp.name
        s.delete(exp)
        s.commit()
    flash(gettext("Deleted experiment %(name)s. Its %(nouns)s are unchanged.", name=name,
                  nouns=i18n.translate_value(place.nouns)), "success")
    return redirect(place.list_url)


# ---------------------------------------------------------------- export

def _rows_for_export(session, exp, place):
    from . import experiment_steps as xs
    subs = subjects(session, exp, place)
    table = readout_table(session, exp, place, subs)
    by_key = {x.key: x for x in subs}
    readout = table["readout"]
    unit = f" ({readout['unit']})" if readout["unit"] else ""
    long_rows = [["Experiment", "Database", place.noun.capitalize(), "Group", "Sex", "Genotype", place.housing.capitalize(),
                  "Date", "Day", f"{readout['label']}{unit}", "At start", "% of first" if readout["kind"] == "value" else "% of start"]]
    for r in table["rows"]:
        x = by_key.get(r["key"])
        for i, d in enumerate(table["dates"]):
            if r["values"][i] is None:
                continue
            long_rows.append([exp.name, place.label, r["label"], r["group"], r["sex"], x.genotype if x else "",
                              x.housing if x else "", d, table["days"][i], r["values"][i], r["start"], r["pct"][i]])
    wide = [[place.noun.capitalize(), "Group"] + [f"Day {n}" if n is not None else d for d, n in zip(table["dates"], table["days"])]]
    wide += [[r["label"], r["group"]] + r["values"] for r in table["rows"]]
    manip = [["Day", "Date planned", "Done on", "Done by", "What", "Kind", "Dose", "Route", "Group", place.noun.capitalize(),
              "Given", "Amount", "Volume", "Weight used", "Reagent", "Lot", "Note"]]
    for row in xs.schedule(session, exp, place, subs):
        if row["reading"]:
            continue
        step = next(st for st in exp.steps if st.id == row["step_id"])
        rec = row["record"]
        given = {e["subject"]: e for e in (rec["subjects"] if rec else [])}
        reagent = (rec or {}).get("reagent") or {}
        for x in subs:
            if step.treatment_group and x.group.strip().lower() != step.treatment_group.strip().lower():
                continue
            e = given.get(x.key)
            manip.append([row["day"], row["date"], rec["done_on"] if rec else "", rec["done_by"] if rec else "",
                          step.agent, kind_info(place.family, step.kind)[0], step.dose, step.route, step.treatment_group,
                          x.label, ("yes" if e else "no") if rec else "", (e or {}).get("amount", ""),
                          (e or {}).get("volume", ""), (e or {}).get("grams", ""), reagent.get("name", ""),
                          reagent.get("lot", ""), rec["note"] if rec else ""])
    stats = [["Day", "Date", "Groups", "Test", "p", ""]]
    for day in table["stats"]["raw"]:
        stats.append([day["day"], day["date"],
                      "; ".join(f"{g['group']}: {g['value']}" + (f" ± {g['sem']}" if g.get("sem") is not None else "")
                                + f" (n={g['n']})" for g in day["groups"]),
                      day["test"], day["p"], day["stars"]])
    return long_rows, wide, manip, stats


@bp.get("/<int:experiment_id>/export.<fmt>")
def export(experiment_id: int, fmt: str):
    """The readout (long, for Prism or R, and wide), each animal's
    manipulations, and the day-by-day tests: an Excel workbook, or the
    long readout as CSV."""
    import csv
    import io
    import re as _re
    from flask import Response
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        long_rows, wide, manip, stats = _rows_for_export(s, exp, place)
        base = _re.sub(r"[^A-Za-z0-9._-]+", "-", exp.name).strip("-")[:60] or "experiment"
    from .services import csv_text
    if fmt == "csv":
        return Response(csv_text(long_rows), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={base}-readout.csv"})
    if fmt != "xlsx":
        abort(404)
    from openpyxl import Workbook
    from openpyxl.styles import Font
    book = Workbook()
    for i, (title, rows) in enumerate((("Readout", long_rows), ("Readout, wide", wide), ("Manipulations", manip),
                                       ("Tests by day", stats))):
        sheet = book.active if i == 0 else book.create_sheet()
        sheet.title = title
        for row in rows:
            sheet.append(row)
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    cell.data_type = "s"          # someone's text, never a formula
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
    if len(stats) > 1:
        book["Tests by day"].append([])
        book["Tests by day"].append(["Each day is tested on its own, not corrected for the number of days."])
    buffer = io.BytesIO()
    book.save(buffer)
    return Response(buffer.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename={base}.xlsx"})


# ---------------------------------------------------------------- bench mode

@bp.get("/<int:experiment_id>/bench")
def bench(experiment_id: int):
    """One animal at a time, for a phone at the bench: weigh or count each,
    or give each today's manipulation and tick it."""
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        data = payload(s, exp, place)
        return render_template("experiment_bench.html", place=place, data=data, page_url=page_url(exp))


@bp.get("/<int:experiment_id>/bench.svg")
def bench_qr(experiment_id: int):
    """A QR code of bench mode's address, to open it on a phone."""
    from flask import Response
    from .labels import _qr_svg
    url = request.host_url.rstrip("/") + url_for("experiments.bench", experiment_id=experiment_id)
    svg = _qr_svg(url, scale=5)
    if not svg:
        abort(404)
    return Response(svg, mimetype="image/svg+xml", headers={"Cache-Control": "private, max-age=3600"})
