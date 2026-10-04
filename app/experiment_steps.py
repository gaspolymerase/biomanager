"""What is done to an experiment's animals, planned and recorded.

An experiment's plan is a list of manipulations (ExperimentStep): the kind
(each animal has its own: an injection for a mouse, drug in the water for
fish, drug in the food for flies, RNAi feeding for worms; app/experiments.py),
what (Tamoxifen, HDM), the dose (20 mg/kg, 25 µg), the route, which
treatment group (blank: every animal), and the days: "1", "2-5", "1, 8,
15". Day 1 is the experiment's start date, so each day has a date once it
has one.

Each day of each manipulation is recorded when it is done
(ExperimentStepRecord): the date, by whom, which animals got it, and for a
dose per body weight the amount each got, worked out from its latest
weight (and the volume, given the solution's concentration). A readout day
("reading") records the readout itself: weights, lengths, how many are
alive. "Record manipulation" also takes one that isn't in the plan: it is
added to the plan on that day, and done.

The same plan, records and readout are what a notebook page shows in its
"Colony experiment" block (frontend/src/blocks/experiment.js), live or
frozen at a moment; "Add to notebook" on the experiment makes a notebook
page with that block in it. Due days are on the calendar.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, g, jsonify, redirect, request, url_for
from sqlalchemy import select

from . import access, i18n, lab
from . import experiments as ex
from .db import SessionLocal
from .i18n import gettext, ngettext
from .models import Experiment, ExperimentRegimen, ExperimentStep, ExperimentStepRecord, InventoryItem

bp = Blueprint("expsteps", __name__, url_prefix="/colony/experiments")

MAX_DAY = 1000
MAX_DAYS_PER_STEP = 400
NOTEBOOK_PAGE_KEY = "experiment_notebook_page:{experiment}:{user}"


@bp.before_request
def require_login():
    if g.get("user") is None:
        return redirect(url_for("login", next=request.path))
    return None


# ---------------------------------------------------------------- days

_RANGE = re.compile(r"^(-?\d+)\s*(?:-|–|—|~|to|\.\.)\s*(-?\d+)$")


def parse_days(text: str) -> list[int]:
    """"1" → [1]; "2-5" and "2/3/4/5" → [2, 3, 4, 5]; "1, 8, 15" → [1, 8,
    15]; "d1-3, d7" too. ValueError with a sentence to show otherwise."""
    raw = (text or "").strip().lower().replace("days", "").replace("day", "")
    if not raw:
        raise ValueError(gettext("Say on which days, e.g. 1, or 2-5, or 1, 8, 15."))
    out: set[int] = set()
    for part in re.split(r"[,;/&]|\band\b|\s{2,}", raw):
        part = part.strip().lstrip("d").strip()
        if not part:
            continue
        match = _RANGE.match(part.replace(" d", " ").replace("-d", "-"))
        if match:
            first, last = int(match.group(1)), int(match.group(2))
            if last < first:
                first, last = last, first
            if last - first > MAX_DAYS_PER_STEP:
                raise ValueError(gettext("Day %(first)s to %(last)s is too long a stretch for one line.", first=first, last=last))
            out.update(range(first, last + 1))
        elif re.fullmatch(r"-?\d+", part):
            out.add(int(part))
        else:
            # "2 3 4 5": single spaces between numbers
            bits = part.split()
            if bits and all(re.fullmatch(r"-?\d+", b) for b in bits):
                out.update(int(b) for b in bits)
            else:
                raise ValueError(gettext("“%(part)s” isn't a day or a range of days (like 2-5).", part=part))
    if any(abs(d) > MAX_DAY for d in out):
        raise ValueError(gettext("Days go up to %(max)s.", max=MAX_DAY))
    if len(out) > MAX_DAYS_PER_STEP:
        raise ValueError(gettext("That is more days than one line can hold."))
    return sorted(out)


def days_label(days: list[int]) -> str:
    """[2, 3, 4, 5, 9] → "2–5, 9"."""
    runs, out = [], []
    for d in days:
        if runs and d == runs[-1][1] + 1:
            runs[-1][1] = d
        else:
            runs.append([d, d])
    for first, last in runs:
        out.append(str(first) if first == last else f"{first}–{last}")
    return ", ".join(out)


def day_date(start: date | None, day: int) -> date | None:
    """Day 1 is the start date."""
    return start + timedelta(days=day - 1) if start else None


def safe_days(step: ExperimentStep) -> list[int]:
    try:
        return parse_days(step.days)
    except ValueError:
        return []


# ---------------------------------------------------------------- doses

_MASS = {"kg": 1e6, "g": 1e3, "mg": 1.0, "µg": 1e-3, "ug": 1e-3, "mcg": 1e-3, "ng": 1e-6, "pg": 1e-9}
_VOLUME = {"l": 1e6, "ml": 1e3, "µl": 1.0, "ul": 1.0, "nl": 1e-3}
_UNITS = {"iu": "IU", "u": "U", "mmol": "mmol", "µmol": "µmol", "umol": "µmol", "nmol": "nmol"}
_QTY = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([a-zµμ]+)\s*(?:/\s*([a-zµμ]+))?\s*$", re.I)


def _unit(u: str) -> str:
    return (u or "").lower().replace("μ", "µ")


def parse_quantity(text: str):
    """"20 mg/kg" → (20.0, "mg", "kg"); "25 µg" → (25.0, "µg", None);
    None when it isn't a number and a unit."""
    m = _QTY.match(text or "")
    if not m:
        return None
    return float(m.group(1).replace(",", ".")), _unit(m.group(2)), (_unit(m.group(3)) if m.group(3) else None)


def fmt_mass(mg: float) -> str:
    if mg >= 1000:
        return f"{mg / 1000:.3g} g"
    if mg >= 0.1:
        return f"{mg:.3g} mg"
    if mg >= 1e-4:
        return f"{mg * 1000:.3g} µg"
    return f"{mg * 1e6:.3g} ng"


def fmt_volume(ul: float) -> str:
    return f"{ul / 1000:.3g} mL" if ul >= 1000 else f"{ul:.3g} µL"


def dose_for(dose: str, concentration: str, grams: float | None) -> dict:
    """What one mouse gets: {"amount": "0.48 mg", "volume": "48 µL"}, or
    {"needs": "a weight"} when a per-weight dose has no weight to use.
    A dose that isn't a number and unit is shown as it is."""
    q = parse_quantity(dose)
    if q is None:
        return {"amount": dose.strip()} if (dose or "").strip() else {}
    value, unit, per = q
    body = None
    if per in ("kg", "g"):
        if grams is None:
            return {"needs": "a weight"}
        body = grams / 1000 if per == "kg" else grams
        value *= body
    out: dict = {}
    if unit in _MASS:
        mg = value * _MASS[unit]
        out["amount"] = fmt_mass(mg)
        c = parse_quantity(concentration)
        if c and c[1] in _MASS and c[2] in _VOLUME and c[0] > 0:
            ul = mg / (c[0] * _MASS[c[1]] / _VOLUME[c[2]])
            out["volume"] = fmt_volume(ul)
    elif unit in _VOLUME:
        out["volume"] = fmt_volume(value * _VOLUME[unit])
    elif unit in _UNITS:
        out["amount"] = f"{value:.3g} {_UNITS[unit]}"
        c = parse_quantity(concentration)
        if c and _UNITS.get(c[1]) == _UNITS[unit] and c[2] in _VOLUME and c[0] > 0:
            out["volume"] = fmt_volume(value / (c[0] / _VOLUME[c[2]]))
    else:
        out["amount"] = f"{value:.3g} {unit}" + ("" if body is not None else "")
    return out


def per_weight(dose: str) -> bool:
    q = parse_quantity(dose)
    return bool(q and q[2] in ("kg", "g"))




# ---------------------------------------------------------------- reading

def _record_dict(r: ExperimentStepRecord) -> dict:
    """A record, its animals as {"subject": "mouse:12", "label", …} (the
    first version kept only "mouse" ids)."""
    try:
        entries = json.loads(r.mice or "[]")
    except ValueError:
        entries = []
    subjects = []
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict):
            continue
        if not e.get("subject") and e.get("mouse"):
            e = {**e, "subject": f"mouse:{e['mouse']}", "label": e.get("label") or f"#{e.get('mouse_id', '')}"}
        subjects.append(e)
    def load(raw, empty):
        try:
            value = json.loads(raw or "")
        except ValueError:
            return empty
        return value if isinstance(value, type(empty)) else empty
    return {"id": r.id, "done_on": r.done_on.isoformat(), "done_by": r.done_by, "note": r.note,
            "mice": subjects, "subjects": subjects, "count": len(subjects),
            "reagent": load(r.reagent, {}), "samples": load(r.samples, [])}


def step_dict(step: ExperimentStep, family: str = "mouse", session=None) -> dict:
    label, icon = ex.kind_info(family, step.kind)
    reagent = reagent_snapshot(session, step.reagent_item_id_fk) if session is not None and step.reagent_item_id_fk else {}
    return {"id": step.id, "days": step.days, "days_label": days_label(safe_days(step)) or step.days,
            "kind": "reading" if step.kind == "weigh" else step.kind, "kind_label": label, "icon": icon,
            "agent": step.agent, "dose": step.dose, "route": step.route, "concentration": step.concentration,
            "group": step.treatment_group, "notes": step.notes, "per_weight": per_weight(step.dose),
            "reading": ex.is_reading(step.kind), "reagent_id": step.reagent_item_id_fk, "reagent": reagent}


def reagent_snapshot(session, item_id) -> dict:
    """An inventory item as a manipulation keeps it: name, lot, expiry."""
    if not item_id:
        return {}
    item = session.get(InventoryItem, int(item_id))
    if item is None:
        return {}
    from .models import InventoryModule
    module = session.get(InventoryModule, item.module_id_fk)
    return {"id": item.id, "name": item.name or "", "lot": item.lot or "",
            "expires": item.expires_on.isoformat() if item.expires_on else "",
            "inventory": module.label if module else ""}


def step_title(step: ExperimentStep, family: str = "mouse", readout: dict | None = None, shown: bool = False) -> str:
    """What a day of `step` is called; `shown`: in the page's language
    (the readout's or the kind's name), for the calendar."""
    tr = i18n.translate_value if shown else str
    if ex.is_reading(step.kind):
        return tr((readout or {}).get("label") or "Readout")
    what = step.agent or tr(ex.kind_info(family, step.kind)[0])
    return " ".join(filter(None, [what, step.dose, step.route]))


def schedule(session, exp: Experiment, place, subs=None, today: date | None = None) -> list[dict]:
    """Every day of every manipulation, in order: its date, and whether it
    is done, due today, overdue or to come."""
    today = today or date.today()
    subs = ex.subjects(session, exp, place) if subs is None else subs
    readout = ex.readout_of(exp, place)
    rows = []
    for step in exp.steps:
        done = {r.day: r for r in step.records}
        wanted = (step.treatment_group or "").strip().lower()
        group_size = len([x for x in subs if not wanted or x.group.strip().lower() == wanted])
        info = step_dict(step, place.family)
        for day in safe_days(step):
            when = day_date(exp.start_date, day)
            record = done.get(day)
            if record is not None:
                state = "done"
            elif when is None:
                state = "planned"
            elif when < today:
                state = "overdue"
            elif when == today:
                state = "today"
            else:
                state = "upcoming"
            rows.append({"step_id": step.id, "day": day, "date": when.isoformat() if when else "",
                         "title": step_title(step, place.family, readout), "kind": info["kind"], "icon": info["icon"],
                         "reading": info["reading"], "group": step.treatment_group, "group_size": group_size,
                         "state": state, "record": _record_dict(record) if record else None})
    rows.sort(key=lambda r: (r["day"], r["step_id"]))
    return rows


def weight_table(session, exp: Experiment) -> dict:
    """The readout by animal and date (kept under this name for the
    notebook block's first version)."""
    place = ex.place_for(session, exp.db or "colony")
    return ex.readout_table(session, exp, place)


def notebook_payload(session, exp: Experiment) -> dict:
    place = ex.place_for(session, exp.db or "colony")
    subs = ex.subjects(session, exp, place)
    table = ex.readout_table(session, exp, place, subs)
    return {
        "id": exp.id, "name": exp.name, "status": exp.status, "owner": exp.owner_username,
        "db": place.key, "db_label": place.label, "noun": place.noun, "nouns": place.nouns,
        "description": exp.description, "treatment_plan": exp.treatment_plan,
        "start_date": exp.start_date.isoformat() if exp.start_date else "",
        "end_date": exp.end_date.isoformat() if exp.end_date else "",
        "url": ex.page_url(exp),
        "members": [{"mouse": x.id, "mouse_id": x.label.lstrip("#"), "key": x.key, "label": x.label, "group": x.group,
                     "sex": x.sex, "genotype": x.genotype} for x in subs],
        "steps": [step_dict(st, place.family) for st in exp.steps],
        "schedule": schedule(session, exp, place, subs),
        "readout": table["readout"],
        "weights": table,
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ---------------------------------------------------------------- calendar

def calendar_items(session, start: date, end: date, owner: str | None = None) -> list[dict]:
    """Days of manipulations not yet done, on the colony's calendar layer
    (done ones stay on the experiment). A personal feed gets its owner's."""
    query = select(Experiment).where(Experiment.status == "active", Experiment.start_date.is_not(None))
    if owner is not None:
        query = query.where(Experiment.owner_username == owner)
    out = []
    for exp in session.scalars(query):
        if not exp.steps:
            continue
        place = ex.place_for(session, exp.db or "colony")
        if place is None:
            continue
        readout = ex.readout_of(exp, place)
        titles = {st.id: step_title(st, place.family, readout, shown=True) for st in exp.steps}
        for row in schedule(session, exp, place):
            if row["state"] == "done" or not row["date"]:
                continue
            when = date.fromisoformat(row["date"])
            if not (start <= when <= end):
                continue
            who = f" · {row['group']}" if row["group"] else ""
            title = titles.get(row["step_id"], row["title"])
            color = "#ff9f0a" if row["state"] == "overdue" else "#34c759"
            out.append({
                "id": f"auto-expstep-{row['step_id']}-{row['day']}", "kind": "auto", "calendarId": "auto",
                "title": f"{title} · {exp.name}{who}", "category": "allday", "isAllday": True,
                "start": datetime.combine(when, datetime.min.time()).isoformat(),
                "end": datetime.combine(when, datetime.max.time()).isoformat(),
                "backgroundColor": color, "borderColor": color,
                "body": gettext("Day %(day)s of %(name)s (%(count)s %(nouns)s)", day=row["day"], name=exp.name,
                                count=row["group_size"], nouns=i18n.translate_value(place.nouns)),
                "isReadOnly": True,
                "raw": {"source": "experiment-step", "anchor_id": exp.id, "icon": row["icon"],
                        "href": ex.page_url(exp) + f"#day-{row['day']}"},
            })
    return out


# ---------------------------------------------------------------- saving

def _json():
    return request.get_json(silent=True) or request.form.to_dict()


def _load(session, experiment_id: int):
    return ex._load(session, experiment_id)


def _answer(session, exp, place, **extra):
    session.refresh(exp)
    return jsonify({"ok": True, **ex.payload(session, exp, place), **extra})


@bp.post("/<int:experiment_id>/steps/save")
def save_step(experiment_id: int):
    data = _json()
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        refused = ex._refuse(exp)
        if refused:
            return refused
        try:
            days = parse_days(str(data.get("days", "")))
        except ValueError as error:
            return jsonify({"ok": False, "error": str(error)}), 400
        step, error = _fill_step(s, exp, place, data, days)
        if error:
            return error
        s.commit()
        return _answer(s, exp, place)


def _fill_step(session, exp, place, data, days, step=None):
    kinds = {k for k, _l, _i in ex.kinds_for(place.family)} | {"weigh"}
    kind = str(data.get("kind") or "other")
    if kind not in kinds and not any(kind == k for rows in ex.KINDS.values() for k, _l, _i in rows):
        return None, (jsonify({"ok": False, "error": gettext("Pick what kind of manipulation it is.")}), 400)
    agent = str(data.get("agent") or "").strip()[:200]
    if not agent and kind in ("injection", "challenge", "treatment", "immersion", "microinjection", "food", "plate", "rnai"):
        return None, (jsonify({"ok": False, "error": gettext("Say what is given, e.g. Tamoxifen or HDM.")}), 400)
    step_id = int(data.get("id") or 0)
    if step is None and step_id:
        step = session.get(ExperimentStep, step_id)
        if step is None or step.experiment_id_fk != exp.id:
            return None, (jsonify({"ok": False, "error": gettext("That manipulation is no longer there.")}), 404)
        gone = sorted(r.day for r in step.records if r.day not in days)
        if gone:
            return None, (jsonify({"ok": False, "error": gettext(
                "Day %(days)s is recorded as done: undo that first to take the day out of the plan.",
                days=days_label(gone))}), 409)
    if step is None:
        step = ExperimentStep(experiment_id_fk=exp.id, created_by=g.user.username, position=len(exp.steps))
        session.add(step)
    step.days = days_label(days)
    step.kind = "reading" if kind == "weigh" else kind
    step.agent = agent
    step.dose = str(data.get("dose") or "").strip()[:80]
    step.route = str(data.get("route") or "").strip()[:60]
    step.concentration = str(data.get("concentration") or "").strip()[:60]
    step.treatment_group = str(data.get("group") or "").strip()[:80]
    step.notes = str(data.get("notes") or "").strip()
    if "reagent_id" in data:
        raw = str(data.get("reagent_id") or "")
        step.reagent_item_id_fk = int(raw) if raw.isdigit() and session.get(InventoryItem, int(raw)) else None
    exp.updated_at = datetime.utcnow()
    session.flush()
    return step, None


@bp.post("/<int:experiment_id>/steps/<int:step_id>/delete")
def delete_step(experiment_id: int, step_id: int):
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        refused = ex._refuse(exp)
        if refused:
            return refused
        step = s.get(ExperimentStep, step_id)
        if step is not None and step.experiment_id_fk == exp.id:
            if step.records and (_json().get("confirm") != "1"):
                return jsonify({"ok": False, "needs_confirm": True,
                                "error": ngettext("%(num)s day of it is recorded as done.",
                                                  "%(num)s days of it are recorded as done.", len(step.records))}), 409
            s.delete(step)
            s.commit()
        return _answer(s, exp, place)


def _entries(session, exp, place, step, chosen, on: date, values: dict | None = None) -> list[dict]:
    """Each chosen animal as a record entry: what it got (from its weight
    that day, for a dose per body weight), or its readout."""
    series = ex.readings(session, exp, place, chosen) if ex.weighs(exp, place) or ex.is_reading(step.kind) else {}
    out = []
    for x in chosen:
        entry = {"subject": x.key, "label": x.label}
        if x.kind == "mouse":
            entry.update({"mouse": x.id, "mouse_id": x.record.mouse_id})
        value, _when = ex.weight_on(series.get(x.key, {}), on) if series else (None, None)
        if ex.is_reading(step.kind):
            entry["value"] = (series.get(x.key) or {}).get(on)
        else:
            weight = value if ex.weighs(exp, place) else None
            if weight is not None:
                entry["grams"] = weight
            entry.update(dose_for(step.dose, step.concentration, weight))
        out.append(entry)
    return out


@bp.get("/<int:experiment_id>/steps/<int:step_id>/day/<int(signed=True):day>")
def day_detail(experiment_id: int, step_id: int, day: int):
    """For the record dialog: the day's date, and each animal it is for,
    with its latest readout and the amount it gets."""
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        step = s.get(ExperimentStep, step_id)
        if step is None or step.experiment_id_fk != exp.id or day not in safe_days(step):
            abort(404)
        record = next((r for r in step.records if r.day == day), None)
        planned = day_date(exp.start_date, day)
        on = date.fromisoformat(request.args["on"]) if request.args.get("on") else (
            record.done_on if record else (planned if planned and planned <= date.today() else date.today()))
        members = ex.subjects(s, exp, place, step.treatment_group)
        series = ex.readings(s, exp, place, members)
        given = {e.get("subject") for e in (_record_dict(record)["subjects"] if record else [])}
        weighs = ex.weighs(exp, place)
        animals = []
        for x in members:
            value, when = ex.weight_on(series[x.key], on)
            animals.append({
                "subject": x.key, "mouse": x.id, "mouse_id": x.label.lstrip("#"), "label": x.label, "group": x.group,
                "sex": x.sex, "start": x.start, "grams": value, "weighed_on": when.isoformat() if when else "",
                "today_grams": series[x.key].get(on), "can_weigh": x.can_edit,
                "given": (x.key in given) if record else True,
                **({} if ex.is_reading(step.kind) else dose_for(step.dose, step.concentration, value if weighs else None)),
            })
        return jsonify({"ok": True, "step": step_dict(step, place.family), "day": day,
                        "planned": planned.isoformat() if planned else "", "on": on.isoformat(),
                        "record": _record_dict(record) if record else None, "mice": animals, "animals": animals,
                        "readout": ex.readout_of(exp, place), "editable": access.can_edit_experiment(exp)})


def _chosen(session, exp, place, step, data) -> list:
    members = {x.key: x for x in ex.subjects(session, exp, place, step.treatment_group)}
    if "subjects" in data:
        wanted = [str(k) for k in data.get("subjects") or []]
    elif "mice" in data:                      # the first version sent mouse row ids
        wanted = [f"mouse:{int(m)}" for m in data.get("mice") or []]
    else:
        wanted = list(members)
    return [members[k] for k in wanted if k in members]


def _record(session, exp, place, step, day, data):
    try:
        on = date.fromisoformat(str(data.get("done_on") or date.today().isoformat())[:10])
    except ValueError:
        return None, (jsonify({"ok": False, "error": gettext("That date isn't a date.")}), 400)
    if on > date.today():
        return None, (jsonify({"ok": False, "error": gettext("It can't be recorded as done on a day still to come.")}), 400)
    chosen = _chosen(session, exp, place, step, data)
    if not chosen:
        return None, (jsonify({"ok": False, "error": gettext("Tick the %(nouns)s it was done to.",
                                                             nouns=i18n.translate_value(place.nouns))}), 400)
    problems = []
    if ex.is_reading(step.kind):
        values = data.get("values") or data.get("grams") or {}
        for x in chosen:
            raw = str(values.get(x.key, values.get(str(x.id), ""))).strip()
            if not raw:
                continue
            problem = ex.set_reading(session, exp, place, x, on, raw)
            if problem:
                if not isinstance(problem, ex.NotYours):      # a wrong value, not one skipped
                    return None, (jsonify({"ok": False, "error": problem}), 400)
                problems.append(problem)
        session.flush()
    record = next((r for r in step.records if r.day == day), None)
    if record is None:
        record = ExperimentStepRecord(step_id_fk=step.id, day=day)
        session.add(record)
    record.done_on, record.done_by = on, g.user.username
    record.mice = json.dumps(_entries(session, exp, place, step, chosen, on))
    record.note = str(data.get("note") or "").strip()
    # The reagent used: the plan's, or the one picked at the bench.
    reagent_id = data.get("reagent_id", step.reagent_item_id_fk)
    snapshot = reagent_snapshot(session, reagent_id) if str(reagent_id or "").isdigit() else {}
    record.reagent = json.dumps(snapshot) if snapshot else ""
    if snapshot.get("expires") and snapshot["expires"] < on.isoformat():
        if snapshot.get("lot"):
            problems.append(gettext("%(name)s lot %(lot)s expired on %(date)s.", name=snapshot["name"],
                                    lot=snapshot["lot"], date=snapshot["expires"]))
        else:
            problems.append(gettext("%(name)s expired on %(date)s.", name=snapshot["name"], date=snapshot["expires"]))
    # Sample records, only when the person asked for them.
    if data.get("make_samples") and not _record_dict(record)["samples"]:
        made, trouble = make_samples(session, exp, place, step, day, chosen, on, str(data.get("sample_inventory") or ""),
                                     str(data.get("sample_name") or ""))
        record.samples = json.dumps(made) if made else ""
        problems += trouble
    exp.updated_at = datetime.utcnow()
    return problems, None


def make_samples(session, exp, place, step, day, chosen, on: date, inventory_key: str, name: str):
    """One sample record per animal in the inventory chosen, its Source
    the animal: through the inventory's own save code, so its required
    columns and permissions hold."""
    from . import inventory_service as inv
    from .inventory_routes import Refused, _item_from_form
    module = inv.get_module(session, inventory_key)
    if module is None or not lab.can_see(module):
        return [], [gettext("There is no such inventory for the samples.")]
    mv = inv.view(module)
    source = next((f for f in mv.fields if f["type"] == "source"), None)
    dated = next((f for f in mv.fields if f["type"] == "date" and f["key"] in ("collected_on", "collected", "date")), None)
    what = (name or step.agent or ex.kind_info(place.family, step.kind)[0]).strip()
    made, trouble = [], []
    for x in chosen:
        item = InventoryItem(module_id_fk=module.id, number=inv.next_number(session, module.id), owner=g.user.username,
                             status=mv.statuses[0] if mv.statuses else "")
        session.add(item)
        session.flush()
        form = {"name": f"{what} · {x.label} · day {day}"[:200], "owner": g.user.username,
                "notes": f"From the experiment {exp.name}, day {day} ({on.isoformat()})."}
        if source:
            kind, ref = _source_of(place, x)
            form[f"attr_{source['key']}_kind"], form[f"attr_{source['key']}_ref"] = kind, ref
        if dated:
            form[f"attr_{dated['key']}"] = on.isoformat()
        savepoint = session.begin_nested()
        try:
            _item_from_form(session, mv, item, form, creating=True)
            savepoint.commit()
            made.append({"id": item.id, "number": item.number, "name": item.name, "inventory": mv.label,
                         "key": module.key})
        except Refused as error:
            savepoint.rollback()
            session.delete(item)
            trouble.append(gettext("No sample for %(label)s: %(error)s", label=x.label, error=error))
            break
    return made, trouble


def _source_of(place, x) -> tuple[str, str]:
    if x.kind == "mouse":
        return "mouse", str(x.record.mouse_id)
    if x.kind in ("fish", "clutch"):
        return "fish", x.housing or x.label
    if x.kind in ("organism", "cohort"):
        return f"organism:{place.module.key}", x.label
    return "other", f"{place.label} {x.label}"


@bp.post("/<int:experiment_id>/steps/<int:step_id>/day/<int(signed=True):day>/record")
def record_day(experiment_id: int, step_id: int, day: int):
    """Record the day as done: the date, which animals (all of the group by
    default), and for a readout day the readout."""
    data = request.get_json(silent=True) or {}
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        refused = ex._refuse(exp)
        if refused:
            return refused
        step = s.get(ExperimentStep, step_id)
        if step is None or step.experiment_id_fk != exp.id or day not in safe_days(step):
            abort(404)
        problems, error = _record(s, exp, place, step, day, data)
        if error:
            return error
        s.commit()
        return _answer(s, exp, place, problems=problems)


@bp.get("/<int:experiment_id>/preview")
def preview(experiment_id: int):
    """For "Record manipulation" with one not in the plan: the animals of a
    group on a day, and what each would get of a dose."""
    args = request.args
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        try:
            on = date.fromisoformat(args.get("on") or date.today().isoformat())
        except ValueError:
            on = date.today()
        members = ex.subjects(s, exp, place, args.get("group") or "")
        series = ex.readings(s, exp, place, members)
        weighs, reading = ex.weighs(exp, place), ex.is_reading(args.get("kind") or "")
        animals = []
        for x in members:
            value, when = ex.weight_on(series[x.key], on)
            animals.append({"subject": x.key, "label": x.label, "group": x.group, "sex": x.sex, "start": x.start,
                            "grams": value, "weighed_on": when.isoformat() if when else "", "given": True,
                            "today_grams": series[x.key].get(on), "can_weigh": x.can_edit,
                            **({} if reading else dose_for(args.get("dose") or "", args.get("concentration") or "",
                                                         value if weighs else None))})
        return jsonify({"ok": True, "animals": animals, "on": on.isoformat(), "readout": ex.readout_of(exp, place)})


@bp.post("/<int:experiment_id>/record-now")
def record_now(experiment_id: int):
    """"Record manipulation" for one that isn't in the plan: it joins the
    plan on that day and is recorded done. With no start date yet, that day
    becomes day 1."""
    data = request.get_json(silent=True) or {}
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        refused = ex._refuse(exp)
        if refused:
            return refused
        try:
            on = date.fromisoformat(str(data.get("done_on") or date.today().isoformat())[:10])
        except ValueError:
            return jsonify({"ok": False, "error": gettext("That date isn't a date.")}), 400
        if on > date.today():
            return jsonify({"ok": False, "error": gettext("It can't be recorded as done on a day still to come.")}), 400
        note = ""
        if exp.start_date is None:
            exp.start_date = on
            note = gettext("The experiment had no start date, so %(date)s is its day 1.", date=on.isoformat())
        day = (on - exp.start_date).days + 1
        step, error = _fill_step(s, exp, place, data, [day])
        if error:
            return error
        problems, error = _record(s, exp, place, step, day, data)
        if error:
            s.rollback()
            return error
        s.commit()
        return _answer(s, exp, place, problems=problems, message=note)


@bp.post("/<int:experiment_id>/steps/<int:step_id>/day/<int(signed=True):day>/subject")
def toggle_subject(experiment_id: int, step_id: int, day: int):
    """From the sheet: whether one animal got a recorded day's manipulation."""
    data = request.get_json(silent=True) or {}
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        refused = ex._refuse(exp)
        if refused:
            return refused
        step = s.get(ExperimentStep, step_id)
        record = next((r for r in step.records if r.day == day), None) if step and step.experiment_id_fk == exp.id else None
        if record is None:
            return jsonify({"ok": False, "error": gettext("Record the day first, with Record manipulation.")}), 409
        key = str(data.get("subject") or "")
        entries = [e for e in _record_dict(record)["subjects"] if e.get("subject") != key]
        if data.get("given"):
            x = ex.find_subject(s, exp, place, key)
            if x is None:
                return jsonify({"ok": False, "error": gettext("That one is no longer in the experiment.")}), 404
            entries += _entries(s, exp, place, step, [x], record.done_on)
        record.mice = json.dumps(entries)
        s.commit()
        return _answer(s, exp, place)


@bp.post("/<int:experiment_id>/steps/<int:step_id>/day/<int(signed=True):day>/undo")
def undo_day(experiment_id: int, step_id: int, day: int):
    """Back to not done. A readout recorded with it stays in the sheet."""
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        refused = ex._refuse(exp)
        if refused:
            return refused
        record = s.scalar(select(ExperimentStepRecord).join(ExperimentStep).where(
            ExperimentStep.experiment_id_fk == exp.id, ExperimentStepRecord.step_id_fk == step_id,
            ExperimentStepRecord.day == day))
        if record is not None:
            s.delete(record)
            s.commit()
        return _answer(s, exp, place)


# ---------------------------------------------------------------- saved regimens

def regimens_for(session, family: str) -> list[dict]:
    """The lab's saved regimens for this kind of animal."""
    me = g.user.username if g.get("user") else ""
    out = []
    for r in session.scalars(select(ExperimentRegimen).where(ExperimentRegimen.family == family)
                             .order_by(ExperimentRegimen.name)):
        try:
            steps = json.loads(r.steps or "[]")
        except ValueError:
            steps = []
        out.append({"id": r.id, "name": r.name, "owner": r.owner, "steps": steps,
                    "summary": "; ".join(gettext("day %(days)s: %(what)s", days=st.get("days"),
                                                 what=st.get("agent") or i18n.translate_value(ex.kind_info(family, st.get("kind") or "")[0]))
                                         for st in steps[:4]),
                    "mine": r.owner == me or access.is_admin()})
    return out


@bp.post("/<int:experiment_id>/regimens/save")
def save_regimen(experiment_id: int):
    """Keep this experiment's regimen, to start the next one from."""
    data = _json()
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        name = str(data.get("name") or "").strip()[:200]
        if not name:
            return jsonify({"ok": False, "error": gettext("Give the regimen a name.")}), 400
        if not exp.steps:
            return jsonify({"ok": False, "error": gettext("This experiment has nothing planned to save yet.")}), 400
        steps = [{"kind": st.kind, "agent": st.agent, "dose": st.dose, "route": st.route,
                  "concentration": st.concentration, "days": st.days, "group": st.treatment_group, "notes": st.notes,
                  "reagent_id": st.reagent_item_id_fk} for st in exp.steps]
        s.add(ExperimentRegimen(name=name, family=place.family, steps=json.dumps(steps), owner=g.user.username))
        s.commit()
        return _answer(s, exp, place, message=gettext("Saved the regimen %(name)s.", name=name))


@bp.post("/<int:experiment_id>/regimens/<int:regimen_id>/apply")
def apply_regimen(experiment_id: int, regimen_id: int):
    """Plan a saved regimen's days in this experiment. Nothing is done yet:
    each day is recorded as it happens."""
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        refused = ex._refuse(exp)
        if refused:
            return refused
        regimen = s.get(ExperimentRegimen, regimen_id)
        if regimen is None:
            return jsonify({"ok": False, "error": gettext("That regimen is gone.")}), 404
        added = 0
        for st in json.loads(regimen.steps or "[]"):
            try:
                days = parse_days(str(st.get("days") or ""))
            except ValueError:
                continue
            step, error = _fill_step(s, exp, place, {**st, "id": 0}, days)
            if error is None:
                added += 1
        s.commit()
        return _answer(s, exp, place, message=ngettext("Planned %(num)s line from %(name)s.",
                                                       "Planned %(num)s lines from %(name)s.", added, name=regimen.name))


@bp.post("/<int:experiment_id>/regimens/<int:regimen_id>/delete")
def delete_regimen(experiment_id: int, regimen_id: int):
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        regimen = s.get(ExperimentRegimen, regimen_id)
        if regimen is not None:
            if regimen.owner != g.user.username and not access.is_admin():
                return jsonify({"ok": False, "error": gettext("That regimen is %(owner)s’s.", owner=regimen.owner)}), 403
            s.delete(regimen)
            s.commit()
        return _answer(s, exp, place)


# ---------------------------------------------------------------- the notebook

@bp.get("/<int:experiment_id>/notebook.json")
def notebook_json(experiment_id: int):
    with SessionLocal() as s:
        exp, _place = _load(s, experiment_id)
        return jsonify({"ok": True, "experiment": notebook_payload(s, exp)})


@bp.get("/notebook-list.json")
def notebook_list():
    """Experiments to pick from in a notebook block: yours first."""
    me = g.user.username
    with SessionLocal() as s:
        items = []
        for e in s.scalars(select(Experiment).order_by(Experiment.created_at.desc())):
            place = ex.place_for(s, e.db or "colony")
            if place is None:
                continue
            items.append({"id": e.id, "name": e.name, "status": e.status, "owner": e.owner_username,
                          "db_label": place.label, "start_date": e.start_date.isoformat() if e.start_date else "",
                          "mice": len(ex.subjects(s, e, place)), "nouns": place.nouns, "mine": e.owner_username == me})
    items.sort(key=lambda e: (not e["mine"], e["status"] != "active"))
    return jsonify({"ok": True, "experiments": items})


def my_notebook_page(session, experiment_id: int) -> int | None:
    from .inventory_service import get_setting
    from .models import NotebookPage
    raw = get_setting(session, NOTEBOOK_PAGE_KEY.format(experiment=experiment_id, user=g.user.username), "")
    if not raw.isdigit():
        return None
    return int(raw) if session.get(NotebookPage, int(raw)) is not None else None


def block_markdown(experiment_id: int) -> str:
    return "```experiment\n" + json.dumps({"id": experiment_id, "show": ["plan", "weights"]}) + "\n```"


@bp.post("/<int:experiment_id>/notebook")
def add_to_notebook(experiment_id: int):
    """A notebook page for this experiment (yours), with its manipulations
    and readout in it, live. The second time, that page again."""
    from . import lab_notebook as nb
    from .inventory_service import set_setting
    with SessionLocal() as s:
        exp, place = _load(s, experiment_id)
        if not lab.feature_on(s, "notebook"):
            flash(gettext("The notebook is switched off for this lab."), "error")
            return redirect(ex.page_url(exp))
        page_id = my_notebook_page(s, exp.id)
        if page_id is None:
            tab = nb.tab_named(s, g.user.username, nb.EXPERIMENTS_TAB)
            lines = [f"{place.label} experiment [{exp.name}]({ex.page_url(exp)})"
                     + (f", started {exp.start_date.isoformat()}" if exp.start_date else "") + ".", "",
                     block_markdown(exp.id), "", "## Notes", "", "## Deviations", ""]
            status = {"active": "running", "done": "done"}.get(exp.status, "planned")
            extra = {"status": status, **({"started_at": nb._now()} if status == "running" else {})}
            page = nb.new_page(s, tab, exp.name, "\n".join(lines), kind="experiment", **extra)
            nb.record_edit(s, page)
            set_setting(s, NOTEBOOK_PAGE_KEY.format(experiment=exp.id, user=g.user.username), str(page.id))
            s.commit()
            page_id = page.id
    return redirect(url_for("notebook", page=page_id))
