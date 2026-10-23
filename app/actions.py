"""What an AI assistant may propose, and running a list of it as one.

Each Action is something a person does on the pages: record a litter, wean
a cage, move mice. It names its fields, says in a plain line what it will
do, and turns into the requests the page itself would send (or the API's own
writes, app/api.py). Nothing here repeats a page's rules: `run` sends those
requests through app/contained.py, so every check, permission and message is
the page's own.

    outcome = actions.run(app, "alex", [
        {"action": "litter_born", "target": {"kind": "cage", "id": 412},
         "fields": {"date": "2026-10-04"}},
        {"action": "mice_move", "target": 88, "fields": {"mice": [14, 15]}},
    ])                                        # a preview: nothing is kept
    outcome.ok, outcome.changes[0].summary, outcome.changes[0].records

`run(..., apply=True, description=...)` does the same for real, as one
batch in Batch history (undoable as one), and keeps it only if every change
went through.

A record is named by a reference from /api/v1/resolve, {"kind": "cage",
"id": 412} (its row), or by the number the lab writes on it: a cage's or
litter's ID, a mouse's number.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from sqlalchemy import func, select

from . import contained
from .db import SessionLocal
from .i18n import gettext, ngettext, translate_value
from .models import (FISH_SEX_OPTIONS, FISH_STATUS_OPTIONS, TANK_PURPOSE_OPTIONS, AuditEntry, BatchRecord, CageRecord,
                     ClutchRecord, FishLine, FishRack, FishRecord, LitterRecord, MouseRack, MouseRecord, TankRecord,
                     Experiment, ExperimentStep, Organism, OrganismModule, OrgCohort, OrgCross, OrgDue, OrgHousing,
                     OrgLine, OrgLocation,
                     StockFrozen, StockModule, StockRack, StockUnit, TaskItem, UserAccount, WaterSystem)

MAX_CHANGES = 200
MAX_REPEAT = 40          # new mice in one change, like a litter's most pups


class ActionError(ValueError):
    """A change that can't be sent as it is, in words for the person."""


# ---------------------------------------------------------------- shapes

@dataclass(frozen=True)
class Field:
    name: str
    kind: str            # text, date, int, number, bool, sex, list, mice, rows, or a kind of record (KINDS)
    help: str
    required: bool = False
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class Call:
    """One request to the app: a page's form, or an /api/v1 JSON write."""
    method: str
    path: str
    form: list[tuple[str, str]] | None = None
    json: dict | None = None


@dataclass(frozen=True)
class Action:
    name: str
    area: str            # the database it changes: "mice", "zebrafish", …
    title: str
    help: str
    target: str | None   # the kind of record it is about, or None for something new
    fields: tuple[Field, ...]
    build: Callable[[Any, Any, dict], list[Call]]
    describe: Callable[[Any, Any, dict], str]
    # What the person should know before approving, beyond what the pages
    # say (an item of the same name already there): warnings, in words.
    warn: Callable[[Any, Any, dict], list[str]] | None = None
    # What it changes that the change history doesn't keep (a notebook
    # page's text, its tags), as records like the history's: shown with
    # the records the change wrote, before → after.
    shows: Callable[[Any, Any, dict], list[dict]] | None = None

    def schema(self) -> dict:
        """JSON Schema for this action's fields (the MCP server's tools)."""
        props = {f.name: _field_schema(f) for f in self.fields}
        return {"type": "object", "properties": props, "additionalProperties": False,
                "required": [f.name for f in self.fields if f.required]}


CATALOGUE: dict[str, Action] = {}


def nearest_actions(name: str, n: int = 3) -> list[str]:
    """The actions a mistyped or guessed name most likely meant ("item_use": inventory_quantity_used…),
    for the error, so an assistant that guessed is shown the way."""
    words = [w for w in re.split(r"[^a-z]+", str(name).lower()) if len(w) > 2]

    def score(candidate: str) -> float:
        theirs = candidate.split("_")
        shared = sum(any(t.startswith(w) or w.startswith(t) for t in theirs) for w in words)
        return shared + difflib.SequenceMatcher(None, str(name).lower(), candidate).ratio()

    ranked = sorted(CATALOGUE, key=score, reverse=True)
    return [c for c in ranked[:n] if score(c) >= 0.6]


def action(**kwargs):
    def register(build):
        act = Action(build=build, **kwargs)
        CATALOGUE[act.name] = act
        return build
    return register


def _ref_schema(kind: str) -> dict:
    return {"anyOf": [{"type": "object", "properties": {"kind": {"const": kind}, "id": {"type": "integer"}},
                       "required": ["kind", "id"]},
                      {"type": ["string", "integer"], "description": f"the {kind}'s number as the lab writes it"}]}


# experiment_new's groups and regimen, as JSON Schema (app/experiments.py
# design() takes them, through _design_groups and _design_steps).
_ANIMAL_REF = {"anyOf": [{"type": "object", "properties": {"kind": {"type": "string"}, "id": {"type": "integer"}},
                          "required": ["kind", "id"]},
                         {"type": ["string", "integer"], "description": "a mouse's number, in the colony"}]}
_DESIGN_GROUP = {"type": "object", "additionalProperties": False, "required": ["name"], "properties": {
    "name": {"type": "string", "description": "The group's name (HDM, Vehicle…)"},
    "animals": {"type": "array", "items": _ANIMAL_REF,
                "description": "Its animals: references from resolve (mouse, fish, clutch, stock_unit, org_animal, "
                               "org_cohort) or mouse numbers"},
    "housing": {"type": "array", "items": _ANIMAL_REF,
                "description": "Every living animal in these: cages (their IDs), tanks, stock racks or housing units"}}}
_DESIGN_STEP = {"type": "object", "additionalProperties": False, "required": ["days"], "properties": {
    "days": {"type": ["string", "integer"], "description": "Its days: 1, \"1-5\", \"1, 3, 5\" (day 1 = the start "
                                                          "date)"},
    "kind": {"type": "string", "description": "What is done. Mice and other organisms: injection, challenge, "
                                              "treatment, surgery, behavior, stimulation, sample, imaging; "
                                              "zebrafish: immersion, "
                                              "microinjection, heat_shock, injury, feeding…; flies: food, "
                                              "temperature…; worms: plate, rnai…; a readout day is \"reading\""},
    "agent": {"type": "string", "description": "What is given (Tamoxifen, HDM): needed for an injection, "
                                               "challenge or treatment"},
    "dose": {"type": "string", "description": "e.g. 75 mg/kg"},
    "route": {"type": "string", "description": "e.g. i.p."},
    "concentration": {"type": "string", "description": "e.g. 20 mg/mL"},
    "frequency": {"type": "string", "description": "A stimulation's frequency, e.g. 20 Hz"},
    "intensity": {"type": "string", "description": "A stimulation's intensity, e.g. 5 mW or 1 mA"},
    "pulse_width": {"type": "string", "description": "A stimulation's pulse width, e.g. 5 ms"},
    "duration": {"type": "string", "description": "How long it lasts, e.g. 30 min or 10 s on / 20 s off"},
    "group": {"type": "string", "description": "The group it is for (every animal if left out)"},
    "notes": {"type": "string"}}}
# notebook_page_change's edits: parts of the text, changed where they are.
_TEXT_EDIT = {"type": "object", "additionalProperties": False, "required": ["find", "replace"], "properties": {
    "find": {"type": "string", "description": "Text now on the page, exactly as it is there, found once only"},
    "replace": {"type": "string", "description": "What it becomes (empty: taken out)"}}}


def _field_schema(f: Field) -> dict:
    kinds = {"text": {"type": "string"}, "date": {"type": "string", "format": "date"},
             "int": {"type": "integer"}, "number": {"type": "number"}, "bool": {"type": "boolean"},
             "sex": {"type": "string", "enum": ["F", "M", ""]},
             "list": {"type": "array", "items": {"type": "string"}},
             "object": {"type": "object"},
             "amount": {"type": ["number", "string"]},
             "groups": {"type": "array", "items": _DESIGN_GROUP},
             "steps": {"type": "array", "items": _DESIGN_STEP},
             "edits": {"type": "array", "items": _TEXT_EDIT, "minItems": 1}}
    if f.kind in kinds:
        out = dict(kinds[f.kind])
    elif f.kind in KINDS:
        out = _ref_schema(f.kind)
    elif f.kind == "mice":
        out = {"type": "array", "items": _ref_schema("mouse"), "minItems": 1}
    elif f.kind == "rows":
        out = {"type": "array", "items": {"type": "object", "properties": {
            "mice": {"type": "array", "items": _ref_schema("mouse")},
            "sex": {"type": "string", "enum": ["F", "M", ""]},
            "cage": _ref_schema("cage"),
            "new_card": {"type": "string", "description": "a new cage with this card label, when no cage is given"}},
            "required": ["mice"]}}
    else:
        out = {}
    if f.choices:
        out["enum"] = list(f.choices)
    out["description"] = f.help
    return out


# ---------------------------------------------------------------- values

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Each kind of record a change can name: its table, and the column holding
# what the lab writes on it ("number": a whole number; "name": any case;
# None: only its row, as /api/v1/resolve gives it).
KINDS: dict[str, tuple] = {
    "mouse": (MouseRecord, "mouse_id", "number"),
    "cage": (CageRecord, "cage_id", "code"),
    "litter": (LitterRecord, "litter_id", "code"),
    "rack": (MouseRack, "name", "name"),
    "tank": (TankRecord, "tank_id", "code"),
    "fish": (FishRecord, None, None),
    "clutch": (ClutchRecord, "clutch_id", "code"),
    "fish_line": (FishLine, "name", "name"),
    "fish_rack": (FishRack, "name", "name"),
    "water_system": (WaterSystem, "name", "name"),
}


def _date(name: str, value) -> str:
    text = str(value or "").strip()[:10]
    if not DATE.match(text):
        raise ActionError(gettext("%(field)s must be a date like 2026-10-04, not “%(value)s”.", field=name,
                                  value=value))
    try:
        date.fromisoformat(text)
    except ValueError:
        raise ActionError(gettext("%(field)s must be a date like 2026-10-04, not “%(value)s”.", field=name,
                                  value=value)) from None
    return text


def find(s, kind: str, ref):
    """The record `ref` names (a reference, or what the lab writes on it),
    or an ActionError saying there is none (or that it could be several)."""
    model, column, how = KINDS[kind]
    found = []
    if isinstance(ref, dict):
        if ref.get("kind") != kind or not isinstance(ref.get("id"), int):
            raise ActionError(gettext("Expected a %(kind)s here, as %(example)s.", kind=kind,
                                      example=f'{{"kind": "{kind}", "id": …}}'))
        found = [s.get(model, ref["id"])]
    elif isinstance(ref, (int, str)) and not isinstance(ref, bool) and str(ref).strip():
        code = str(ref).strip()
        if callable(how):
            found = how(s, code)
        elif column is None:
            found = [s.get(model, int(code))] if code.isdigit() else []
        elif how == "number":
            code = code.lstrip("#")
            found = [s.scalar(select(model).where(getattr(model, column) == int(code)))] if code.isdigit() else []
        elif how == "name":
            found = list(s.scalars(select(model).where(func.lower(getattr(model, column)) == code.lower())))
        else:
            found = [s.scalar(select(model).where(getattr(model, column) == code))]
    found = [r for r in found if r is not None]
    if len(found) > 1:
        raise ActionError(gettext("%(ref)s could be several %(kind)s records; name it by its reference from resolve.",
                                  ref=_show(ref), kind=kind.replace("_", " ")))
    if not found:
        raise ActionError(gettext("There is no %(kind)s %(ref)s.", kind=kind.replace("_", " "), ref=_show(ref)))
    return found[0]


def _stock_units(s, code: str) -> list:
    """Vials or plates written as their database's prefix and number (FV12)."""
    from . import stock_service as svc
    out = []
    for module in s.scalars(select(StockModule)):
        prefix = svc.view(module).s.get("code_prefix") or ""
        rest = code[len(prefix):] if code.upper().startswith(prefix.upper()) else ""
        if rest.isdigit():
            out.append(s.scalar(select(StockUnit).where(StockUnit.module_id_fk == module.id,
                                                       StockUnit.number == int(rest))))
    return out


KINDS.update({
    "stock_unit": (StockUnit, None, _stock_units),
    "stock_rack": (StockRack, "name", "name"),
    "stock_frozen": (StockFrozen, None, None),
})


def _show(ref) -> str:
    if isinstance(ref, dict):
        return f"#{ref.get('id')}"
    return str(ref)


def clean(act: Action, raw: dict) -> dict:
    """The change's fields, checked against the action: known names, the
    right kinds, nothing required left out."""
    given = raw.get("fields") or {}
    if not isinstance(given, dict):
        raise ActionError(gettext("fields must be an object."))
    known = {f.name: f for f in act.fields}
    unknown = sorted(set(given) - set(known))
    if unknown:
        raise ActionError(gettext("%(action)s has no %(fields)s; it takes %(known)s.", action=act.name,
                                  fields=", ".join(unknown), known=", ".join(known) or "-"))
    values = {}
    for name, f in known.items():
        if name not in given or given[name] is None:
            if f.required:
                raise ActionError(gettext("%(action)s needs %(field)s.", action=act.name, field=name))
            continue
        v = given[name]
        if f.kind == "date":
            v = _date(name, v)
        elif f.kind == "int":
            if isinstance(v, bool) or not str(v).strip().lstrip("-").isdigit():
                raise ActionError(gettext("%(field)s must be a whole number.", field=name))
            v = int(str(v).strip())
        elif f.kind == "number":
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ActionError(gettext("%(field)s must be a number.", field=name)) from None
        elif f.kind == "amount":     # a number, or a number with its unit: read by the action
            if isinstance(v, bool) or not isinstance(v, (int, float, str)) or not str(v).strip():
                raise ActionError(gettext("%(field)s must be a number.", field=name))
            v = v.strip() if isinstance(v, str) else v
        elif f.kind == "bool":
            v = bool(v)
        elif f.kind == "sex":
            v = str(v).strip().upper()[:1]
            if v not in ("F", "M", ""):
                raise ActionError(gettext("sex is F, M or empty."))
        elif f.kind == "list":
            if not isinstance(v, list):
                raise ActionError(gettext("%(field)s is a list.", field=name))
            v = [str(x).strip() for x in v]
        elif f.kind in ("mice", "rows"):
            if not isinstance(v, list) or not v:
                raise ActionError(gettext("%(field)s is a list.", field=name))
        elif f.kind == "edits":
            if not isinstance(v, list) or not v or not all(
                    isinstance(e, dict) and isinstance(e.get("find"), str) and e["find"]
                    and isinstance(e.get("replace", ""), str) and not set(e) - {"find", "replace"} for e in v):
                raise ActionError(gettext("%(field)s is a list of {find, replace}, each find some text on the page.", field=name))
            v = [{"find": e["find"].replace("\r\n", "\n"), "replace": e.get("replace", "").replace("\r\n", "\n")}
                 for e in v]
        elif f.kind == "object":
            if not isinstance(v, dict):
                raise ActionError(gettext("%(field)s is an object.", field=name))
        elif f.kind == "text":
            v = str(v).strip()
        if f.choices and v not in f.choices:
            raise ActionError(gettext("%(field)s is one of %(choices)s.", field=name, choices=", ".join(f.choices)))
        values[name] = v
    return values


def _mouse_label(m) -> str:
    return f"#{m.mouse_id}"


def _mice_label(mice) -> str:
    return ", ".join(_mouse_label(m) for m in mice)


def _added(old: str | None, text: str) -> str:
    old = (old or "").strip()
    return f"{old}; {text}" if old else text


# ---------------------------------------------------------------- mouse colony

@action(name="mouse_new", area="mice", title="New mouse",
        help="Add a mouse, or several alike (count). Its number is the next free one.", target=None,
        fields=(Field("count", "int", "How many alike (1 if left out)"),
                Field("sex", "sex", "F, M or empty"),
                Field("date_of_birth", "date", "Date of birth"),
                Field("cage", "cage", "The cage it is in (an existing cage)"),
                Field("litter", "litter", "The litter it came from"),
                Field("transgenes", "list", "Up to four transgenes or alleles, as the lab writes them"),
                Field("status", "text", "breeder, experiment, geno, transfer or sac (as the lab's list has them)"),
                Field("owner", "text", "Username of its owner (you if left out)"),
                Field("note", "text", "A note")),
        describe=lambda s, _t, v: ngettext("%(num)s new mouse%(where)s", "%(num)s new mice%(where)s",
                                           v.get("count", 1), where=_in_cage(s, v.get("cage"))))
def _mouse_new(s, _target, v):
    count = v.get("count", 1)
    if not 1 <= count <= MAX_REPEAT:
        raise ActionError(gettext("Make between 1 and %(max)s mice in one change.", max=MAX_REPEAT))
    form = [("gender", v.get("sex", "")), ("owner", v.get("owner", "")), ("status", v.get("status", "")),
            ("note", v.get("note", ""))]
    if "date_of_birth" in v:
        form.append(("date_of_birth", v["date_of_birth"]))
    if "cage" in v:
        form.append(("cage_id", find(s, "cage", v["cage"]).cage_id))
    if "litter" in v:
        form.append(("litter_id", find(s, "litter", v["litter"]).litter_id))
    if len(v.get("transgenes", [])) > 4:
        raise ActionError(gettext("%(field)s is a list of at most four.", field="transgenes"))
    for i, t in enumerate(v.get("transgenes", []), 1):
        form.append((f"transgene_{i}", t))
    return [Call("POST", "/colony/mice/create", form=form)] * count


def _in_cage(s, ref) -> str:
    return gettext(" in cage %(cage)s", cage=find(s, "cage", ref).cage_id) if ref is not None else ""


MOUSE_CHANGE_FIELDS = ("sex", "transgenes", "status", "owner", "note", "litter", "date_of_death")


@action(name="mouse_change", area="mice", title="Change a mouse",
        help="Set any of these on one mouse; what is left out stays as it is. To move mice, use mice_move; "
             "to record a death, mice_died; to add to its note, note_add.",
        target="mouse",
        fields=(Field("sex", "sex", "F, M or empty"),
                Field("transgenes", "list", "Its transgenes or alleles, up to four (replaces them all)"),
                Field("status", "text", "breeder, experiment, geno, transfer or sac"),
                Field("owner", "text", "Username of its new owner"),
                Field("note", "text", "Its note (replaces it)"),
                Field("litter", "litter", "The litter it came from"),
                Field("date_of_death", "date", "When it died")),
        describe=lambda s, m, v: gettext("Mouse %(mouse)s: %(fields)s", mouse=_mouse_label(m),
                                         fields=", ".join(f"{k} → {_said(s, k, v[k])}" for k in v)))
def _mouse_change(s, m, v):
    if not v:
        raise ActionError(gettext("Say what to change on mouse %(mouse)s.", mouse=_mouse_label(m)))
    body = dict(v)
    if "litter" in body:
        body["litter"] = find(s, "litter", body["litter"]).litter_id
    return [Call("PATCH", f"/api/v1/mice/{m.mouse_id}", json=body)]


def _said(s, key, value) -> str:
    if key == "litter":
        return find(s, "litter", value).litter_id
    if isinstance(value, list):
        return "; ".join(x for x in value if x) or "-"
    return str(value) if value != "" else "-"


@action(name="mouse_weight", area="mice", title="Record a weight",
        help="One weight a day: the same day again replaces it.", target="mouse",
        fields=(Field("grams", "number", "Weight in grams", required=True),
                Field("date", "date", "The day it was weighed (today if left out)"),
                Field("notes", "text", "A note")),
        describe=lambda _s, m, v: gettext("Mouse %(mouse)s weighed %(grams)s g%(on)s", mouse=_mouse_label(m),
                                          grams=f"{v['grams']:g}", on=_on(v.get("date"))))
def _mouse_weight(_s, m, v):
    return [Call("POST", f"/api/v1/mice/{m.mouse_id}/weights", json=dict(v))]


def _on(day) -> str:
    return gettext(" on %(day)s", day=day) if day else ""


@action(name="mice_move", area="mice", title="Move mice to a cage",
        help="Put these mice in the target cage.", target="cage",
        fields=(Field("mice", "mice", "The mice to move", required=True),),
        describe=lambda s, c, v: gettext("%(mice)s moved to cage %(cage)s",
                                         mice=_mice_label(_mice(s, v["mice"])), cage=c.cage_id))
def _mice_move(s, cage, v):
    return [Call("POST", f"/colony/cages/{cage.id}/add-mouse", form=[("mouse_id", str(m.mouse_id))])
            for m in _mice(s, v["mice"])]


def _mice(s, refs) -> list:
    mice = [find(s, "mouse", r) for r in refs]
    seen, out = set(), []
    for m in mice:
        if m.id not in seen:
            seen.add(m.id)
            out.append(m)
    return out


@action(name="mice_died", area="mice", title="Mice died or were culled",
        help="Mark these mice as sac (ended), on a date, with the reason added to their notes.", target=None,
        fields=(Field("mice", "mice", "The mice", required=True),
                Field("date", "date", "When (today if left out)"),
                Field("reason", "text", "Why: found dead, culled, end of experiment…")),
        describe=lambda s, _t, v: gettext("%(mice)s marked dead or culled%(on)s%(why)s",
                                          mice=_mice_label(_mice(s, v["mice"])), on=_on(v.get("date")),
                                          why=f" ({v['reason']})" if v.get("reason") else ""))
def _mice_died(s, _target, v):
    calls = []
    for m in _mice(s, v["mice"]):
        body = {"status": "sac", "date_of_death": v.get("date") or date.today().isoformat()}
        if v.get("reason"):
            body["note"] = _added(m.note, v["reason"])
        calls.append(Call("PATCH", f"/api/v1/mice/{m.mouse_id}", json=body))
    return calls


CAGE_FIELDS = (Field("purpose", "text", "Breeder (the lab's breeding stock, shared), Breeding (a mating cage, the one "
                                        "with litters and weaning) or Experiment, as the lab's list has them. A cage "
                                        "made Breeder becomes shared with the lab; one that stops being Breeder, "
                                        "personal"),
               Field("owner", "text", "Username of its owner"),
               Field("rack", "rack", "The rack it sits in"),
               Field("position", "text", "Its position in the rack, as the rack names them (B3, 4-7…)"),
               Field("room", "text", "Room"),
               Field("card_id", "text", "The label on its cage card"),
               Field("genotype_summary", "text", "What the cage holds, in a few words"),
               Field("location_detail", "text", "Where it is, when not in a rack"),
               Field("notes", "text", "Its notes (replaces them; note_add adds to them)"))


def _cage_form(s, v, cage=None) -> list[tuple[str, str]]:
    form = [(k, str(v[k])) for k in ("purpose", "owner", "room", "card_id", "genotype_summary",
                                      "location_detail", "notes") if k in v]
    if "rack" in v or "position" in v:
        rack = find(s, "rack", v["rack"]) if v.get("rack") not in (None, "") else (
            cage.rack if cage is not None and "rack" not in v else None)
        form += [("rack_id", str(rack.id) if rack else ""), ("position", str(v.get("position", "")))]
    return form


@action(name="cage_new", area="mice", title="New cage",
        help="A new empty cage. Its ID is the next free one unless cage_id is given (which must be free).",
        target=None,
        fields=(Field("cage_id", "text", "Its ID, when the lab numbers it by hand"), *CAGE_FIELDS),
        describe=lambda _s, _t, v: gettext("New cage %(cage)s%(purpose)s", cage=v.get("cage_id", ""),
                                           purpose=f" ({v['purpose']})" if v.get("purpose") else ""))
def _cage_new(s, _target, v):
    return [Call("POST", "/colony/cages/create", form=[("cage_id", v.get("cage_id", "")), ("count", "1"),
                                                       *_cage_form(s, v)])]


@action(name="cage_change", area="mice", title="Change a cage",
        help="Set any of these on one cage; what is left out stays as it is.", target="cage",
        fields=(Field("new_cage_id", "text", "A new ID for it (its mice follow)"), *CAGE_FIELDS),
        describe=lambda s, c, v: gettext("Cage %(cage)s: %(fields)s", cage=c.cage_id,
                                         fields=", ".join(f"{k} → {_cage_said(s, k, v[k])}" for k in v)))
def _cage_change(s, cage, v):
    if not v:
        raise ActionError(gettext("Say what to change on cage %(cage)s.", cage=cage.cage_id))
    form = _cage_form(s, v, cage)
    if "new_cage_id" in v:
        form.append(("cage_id", v["new_cage_id"]))
    return [Call("POST", f"/colony/cages/{cage.id}/update", form=form)]


def _cage_said(s, key, value) -> str:
    return find(s, "rack", value).name if key == "rack" and value else (str(value) if value != "" else "-")


def _open_litters(cage) -> list:
    from .services import open_litters
    return open_litters(cage)


def _days(litters) -> str:
    return ", ".join(cl.born.isoformat() for cl in litters)


def _cage_litter(s, cage, ref, what: str):
    """The cage's litter not yet weaned that `ref` names: its birth date
    (2026-09-29), or the litter its pups were made at Genotyping (a
    reference or its ID). None when not given: the page's default then."""
    if ref in (None, ""):
        return None
    still = _open_litters(cage)
    text = str(ref).strip()[:10] if not isinstance(ref, dict) else ""
    if DATE.match(text):
        found = next((cl for cl in still if cl.born.isoformat() == text), None)
    else:
        litter = find(s, "litter", ref)
        found = next((cl for cl in still if cl.litter_id_fk == litter.id), None) \
            or next((cl for cl in still if cl.born == litter.date_of_birth), None)
    if found is None:
        raise ActionError(gettext("Cage %(cage)s has no litter %(litter)s waiting; its litters not yet weaned were born %(born)s.", cage=cage.cage_id, litter=_show(ref),
                                  born=_days(still) or "-"))
    return found


def _which_said(s, cage, v, what: str) -> str:
    """Which litter a change on a cage with two litters is about, in words
    ("" for a cage with one): the one named, or the page's default."""
    from .services import waiting_litters
    still = _open_litters(cage)
    if len(still) < 2:
        return ""
    chosen = _cage_litter(s, cage, v.get("litter"), what)
    if chosen is None:
        waiting = waiting_litters(cage) or still
        chosen = (next((cl for cl in waiting if cl.litter_id_fk is None), waiting[0]) if what == "genotyping"
                  else waiting[0])
    return gettext(" (the litter born %(born)s)", born=chosen.born.isoformat())


def _litter_born_said(_s, c, v) -> str:
    if v.get("another") is False:
        return gettext("Litter in cage %(cage)s corrected: born %(day)s", cage=c.cage_id,
                       day=v.get("date") or date.today().isoformat())
    if "pups" in v:
        said = ngettext("Litter of %(num)s pup born in cage %(cage)s%(on)s",
                        "Litter of %(num)s pups born in cage %(cage)s%(on)s", v["pups"],
                        cage=c.cage_id, on=_on(v.get("date")))
    else:
        said = gettext("Litter born in cage %(cage)s%(on)s", cage=c.cage_id, on=_on(v.get("date")))
    if v.get("another"):
        said = gettext("%(what)s, another litter beside the one not yet weaned", what=said)
    return said


@action(name="litter_born", area="mice", title="Litter born",
        help="A litter was born in this mating (Breeding) cage; weaning and genotyping are due from its birth date. "
             "A cage can have more than one litter at a time (a trio's two females): when it has a litter not yet "
             "weaned, say another: true for a second litter (both are kept, each with its own reminders) or "
             "another: false to correct that litter's date (with litter, its birth date, when there are two). "
             "The cage's own birth date again with pups only adds the count.",
        target="cage",
        fields=(Field("date", "date", "The day they were born (today if left out)"),
                Field("pups", "int", "How many pups were born (1–40), if counted"),
                Field("father", "text", "The father, if known (a mouse number or a note)"),
                Field("mother", "text", "The mother, if known (a mouse number or a note)"),
                Field("another", "bool", "true: a second litter beside the one not yet weaned (both kept); "
                                         "false: this corrects that litter's date"),
                Field("litter", "date", "Correcting on a cage with two litters: the birth date of the one to "
                                        "correct")),
        describe=_litter_born_said)
def _litter_born(_s, cage, v):
    if "pups" in v and not 1 <= v["pups"] <= MAX_REPEAT:
        raise ActionError(gettext("pups is a whole number from 1 to %(max)s.", max=MAX_REPEAT))
    born = v.get("date") or date.today().isoformat()
    still = _open_litters(cage)
    if still and "another" not in v and all(cl.born.isoformat() != born for cl in still):
        raise ActionError(gettext("Cage %(cage)s already has a litter not yet weaned (born %(born)s). Say another: true if this is a second litter (both are kept), or another: false if it corrects that date.", cage=cage.cage_id,
                                  born=_days(still)))
    choice = "" if "another" not in v else ("another" if v["another"] else "correct")
    form = [("date_give_birth", v.get("date", "")), ("pups", str(v.get("pups", ""))),
            ("sire", v.get("father", "")), ("dam", v.get("mother", "")), ("litter_choice", choice), ("litter", v.get("litter", ""))]
    return [Call("POST", f"/colony/cages/{cage.id}/give-birth", form=form)]


@action(name="litter_genotyping", area="mice", title="Create the litter at genotyping",
        help="Make the litter for this cage's pups and one mouse per pup (status geno), in the cage. On a cage with "
             "two litters, litter says whose pups (its birth date); the oldest not yet genotyped otherwise.",
        target="cage",
        fields=(Field("pups", "int", "How many pups (1–40)", required=True),
                Field("father", "text", "The father, as the lab writes it (a mouse number or a note)"),
                Field("mother", "text", "The mother, or mothers"),
                Field("litter", "litter", "Which of the cage's litters: its birth date (2026-09-29)")),
        describe=lambda s, c, v: gettext("Litter of %(pups)s pups made in cage %(cage)s, a mouse for each",
                                         pups=v["pups"], cage=c.cage_id) + _which_said(s, c, v, "genotyping"))
def _litter_genotyping(s, cage, v):
    chosen = _cage_litter(s, cage, v.get("litter"), "genotyping")
    return [Call("POST", f"/colony/cages/{cage.id}/genotyping",
                 form=[("total_pups", str(v["pups"])), ("father_info", v.get("father", "")),
                       ("mother_info", v.get("mother", "")),
                       ("litter", chosen.born.isoformat() if chosen is not None else "")])]


@action(name="wean", area="mice", title="Wean a cage",
        help="Wean this cage. With rows, move its pups out: each row is some of its mice, their sex, and an "
             "existing cage or a new one (new_card). Pups younger than P18 are refused unless early is true. "
             "On a cage with two litters, litter says which is weaned (its birth date, or the litter its pups "
             "are); the oldest otherwise.",
        target="cage",
        fields=(Field("rows", "rows", "Where the pups go: [{mice, sex, cage | new_card}]"),
                Field("early", "bool", "Wean although the pups are younger than usual"),
                Field("litter", "litter", "Which of the cage's litters: its birth date (2026-09-29) or its "
                                          "litter")),
        describe=lambda s, c, v: _wean_said(s, c, v) + _which_said(s, c, v, "wean"))
def _wean(s, cage, v):
    chosen = _cage_litter(s, cage, v.get("litter"), "wean")
    early = [("early", "1")] if v.get("early") else []
    if chosen is not None:
        early.append(("litter", chosen.born.isoformat()))
    if not v.get("rows"):
        return [Call("POST", f"/colony/cages/{cage.id}/wean", form=early)]
    form = list(early)
    for row in v["rows"]:
        if not isinstance(row, dict) or not row.get("mice"):
            raise ActionError(gettext("Each row of a weaning names its mice."))
        mice = _mice(s, row["mice"])
        sex = str(row.get("sex") or "").strip().upper()[:1]
        dest = find(s, "cage", row["cage"]).cage_id if row.get("cage") not in (None, "") else ""
        form += [("mouse_ids[]", ",".join(str(m.mouse_id) for m in mice)), ("gender[]", sex),
                 ("cage_id[]", dest), ("card_id[]", "" if dest else str(row.get("new_card") or ""))]
    return [Call("POST", f"/colony/cages/{cage.id}/wean-distribute", form=form)]


def _wean_said(s, cage, v) -> str:
    if not v.get("rows"):
        return gettext("Cage %(cage)s weaned", cage=cage.cage_id)
    parts = []
    for row in v["rows"]:
        if not isinstance(row, dict) or not row.get("mice"):
            continue
        mice = _mice(s, row["mice"])
        where = (gettext("cage %(cage)s", cage=find(s, "cage", row["cage"]).cage_id)
                 if row.get("cage") not in (None, "") else gettext("a new cage"))
        parts.append(gettext("%(mice)s to %(where)s", mice=_mice_label(mice), where=where))
    return gettext("Cage %(cage)s weaned: %(moves)s", cage=cage.cage_id, moves="; ".join(parts))


@action(name="note_add", area="mice", title="Add to a note",
        help="Add a line to the notes of a mouse, cage or litter, or to an experiment's description (what is there "
             "stays).", target="mouse|cage|litter|experiment",
        fields=(Field("text", "text", "What to add", required=True),),
        describe=lambda _s, r, v: gettext("Note on %(record)s: “%(text)s”", record=_record_label(r), text=v["text"]))
def _note_add(_s, record, v):
    if isinstance(record, MouseRecord):
        return [Call("PATCH", f"/api/v1/mice/{record.mouse_id}", json={"note": _added(record.note, v["text"])})]
    if isinstance(record, CageRecord):
        return [Call("POST", f"/colony/cages/{record.id}/update", form=[("notes", _added(record.notes, v["text"]))])]
    if isinstance(record, Experiment):
        text = f"{record.description.rstrip()}\n{v['text']}" if (record.description or "").strip() else v["text"]
        return [Call("POST", f"/experiments/{record.id}/update", form=[("description", text)])]
    return [Call("POST", f"/colony/litters/{record.id}/update", form=[("notes", _added(record.notes, v["text"]))])]


def _record_label(r) -> str:
    if isinstance(r, MouseRecord):
        return gettext("mouse %(mouse)s", mouse=_mouse_label(r))
    if isinstance(r, CageRecord):
        return gettext("cage %(cage)s", cage=r.cage_id)
    if isinstance(r, Experiment):
        return gettext("the experiment “%(name)s”", name=r.name)
    return gettext("litter %(litter)s", litter=r.litter_id)


# ---------------------------------------------------------------- zebrafish

def _id(s, kind, ref) -> str:
    return str(find(s, kind, ref).id) if ref not in (None, "") else ""


TANK_FIELDS = (Field("purpose", "text", "What the tank is for", choices=tuple(TANK_PURPOSE_OPTIONS)),
               Field("line", "fish_line", "The line it holds"),
               Field("owner", "text", "Username of its owner"),
               Field("card_id", "text", "The label on its tank card"),
               Field("notes", "text", "Its notes (replaces them)"),
               Field("rack", "fish_rack", "The rack it sits in"),
               Field("position", "text", "Its position in the rack, as the rack names them"))


def _tank_form(s, v) -> list[tuple[str, str]]:
    form = [(k, str(v[k])) for k in ("purpose", "owner", "card_id", "notes") if k in v]
    if "line" in v:
        form.append(("line_id_fk", _id(s, "fish_line", v["line"])))
    if "rack" in v or "position" in v:
        form += [("rack_id_fk", _id(s, "fish_rack", v.get("rack"))), ("position", str(v.get("position", "")))]
    return form


@action(name="tank_new", area="zebrafish", title="New tank",
        help="A new tank, or several alike (count, side by side in the rack). Its ID is the next free one unless "
             "tank_id is given.", target=None,
        fields=(Field("tank_id", "text", "Its ID (or the first of several, ending in a number)"),
                Field("count", "int", "How many alike (1–30)"), *TANK_FIELDS),
        describe=lambda s, _t, v: ngettext("%(num)s new tank%(what)s", "%(num)s new tanks%(what)s",
                                           v.get("count", 1), what=_tank_what(s, v)))
def _tank_new(s, _target, v):
    return [Call("POST", "/zebrafish/tanks/create",
                 form=[("tank_id", v.get("tank_id", "")), ("how_many", str(v.get("count", 1))), *_tank_form(s, v)])]


def _tank_what(s, v) -> str:
    bits = [find(s, "fish_line", v["line"]).name] if v.get("line") not in (None, "") else []
    bits += [v["purpose"]] if v.get("purpose") else []
    return f" ({', '.join(bits)})" if bits else ""


@action(name="tank_change", area="zebrafish", title="Change a tank",
        help="Set any of these on one tank; what is left out stays as it is. active false retires it.",
        target="tank",
        fields=(Field("new_tank_id", "text", "A new ID for it"), *TANK_FIELDS,
                Field("active", "bool", "false: the tank is retired")),
        describe=lambda s, t, v: gettext("Tank %(tank)s: %(fields)s", tank=t.tank_id,
                                         fields=", ".join(f"{k} → {_named(s, k, v[k])}" for k in v)))
def _tank_change(s, tank, v):
    if not v:
        raise ActionError(gettext("Say what to change on tank %(tank)s.", tank=tank.tank_id))
    form = _tank_form(s, v)
    if "new_tank_id" in v:
        form.append(("tank_id", v["new_tank_id"]))
    if "active" in v:
        form.append(("active", "1" if v["active"] else "0"))
    return [Call("POST", f"/zebrafish/tanks/{tank.id}/update", form=form)]


_NAMED = {"line": "fish_line", "rack": "fish_rack", "system": "water_system", "tank": "tank",
          "father_tank": "tank", "mother_tank": "tank"}


def _named(s, key, value) -> str:
    """A field's value as the summary shows it: a record by its name."""
    if key in _NAMED and value not in (None, ""):
        rec = find(s, _NAMED[key], value)
        return getattr(rec, "name", None) or getattr(rec, "tank_id", "")
    if isinstance(value, bool):
        return gettext("yes") if value else gettext("no")
    return str(value) if value != "" else "-"


@action(name="mating_set_up", area="zebrafish", title="Set up a cross",
        help="A mating tank from a male and a female tank, with a return date; males and females move that many "
             "fish (rows marked M / F) into it.", target=None,
        fields=(Field("father_tank", "tank", "The male tank", required=True),
                Field("mother_tank", "tank", "The female tank", required=True),
                Field("males", "int", "How many males to move in"),
                Field("females", "int", "How many females to move in"),
                Field("return_days", "int", "Return them after this many days (1 if left out)"),
                Field("notes", "text", "A note")),
        describe=lambda s, _t, v: gettext("Cross %(father)s × %(mother)s%(fish)s",
                                          father=find(s, "tank", v["father_tank"]).tank_id,
                                          mother=find(s, "tank", v["mother_tank"]).tank_id,
                                          fish=f" ({v.get('males', 0)} ♂, {v.get('females', 0)} ♀)"
                                          if v.get("males") or v.get("females") else ""))
def _mating(s, _target, v):
    return [Call("POST", "/zebrafish/mate", form=[
        ("father_tank_id", _id(s, "tank", v["father_tank"])), ("mother_tank_id", _id(s, "tank", v["mother_tank"])),
        ("males", str(v.get("males", 0))), ("females", str(v.get("females", 0))),
        ("return_days", str(v.get("return_days", 1))), ("notes", v.get("notes", ""))])]


@action(name="mating_returned", area="zebrafish", title="Mating tank returned",
        help="The mating tank's fish go back to the tanks they came from, and it is retired.", target="tank",
        fields=(),
        describe=lambda _s, t, _v: gettext("Mating tank %(tank)s returned", tank=t.tank_id))
def _mating_returned(_s, tank, _v):
    return [Call("POST", f"/zebrafish/tanks/{tank.id}/return", form=[])]


CLUTCH_FIELDS = (Field("date", "date", "The day it was fertilised (today if left out)"),
                 Field("line", "fish_line", "Its line"),
                 Field("father_tank", "tank", "The male's tank"),
                 Field("mother_tank", "tank", "The female's tank"),
                 Field("embryos", "int", "How many embryos"),
                 Field("larvae", "int", "How many larvae"),
                 Field("adults", "int", "How many adults"),
                 Field("owner", "text", "Username of its owner"),
                 Field("notes", "text", "Its notes (replaces them)"))


def _clutch_form(s, v) -> list[tuple[str, str]]:
    form = [(k, str(v[k])) for k in ("owner", "notes") if k in v]
    names = {"date": "date_of_fertilization", "embryos": "embryo_count", "larvae": "larvae_count",
             "adults": "adults_count"}
    form += [(names[k], str(v[k])) for k in names if k in v]
    if "line" in v:
        form.append(("line_id_fk", _id(s, "fish_line", v["line"])))
    for k in ("father_tank", "mother_tank"):
        if k in v:
            form.append((f"{k}_id", _id(s, "tank", v[k])))
    return form


@action(name="clutch_new", area="zebrafish", title="New clutch",
        help="Record a clutch. Its ID is the next free one unless clutch_id is given.", target=None,
        fields=(Field("clutch_id", "text", "Its ID"), *CLUTCH_FIELDS),
        describe=lambda s, _t, v: gettext("New clutch%(what)s", what=_clutch_what(s, v)))
def _clutch_new(s, _target, v):
    return [Call("POST", "/zebrafish/clutches/create", form=[("clutch_id", v.get("clutch_id", "")),
                                                             *_clutch_form(s, v)])]


def _clutch_what(s, v) -> str:
    bits = []
    if v.get("father_tank") not in (None, "") and v.get("mother_tank") not in (None, ""):
        bits.append(f"{find(s, 'tank', v['father_tank']).tank_id} × {find(s, 'tank', v['mother_tank']).tank_id}")
    if "embryos" in v:
        bits.append(gettext("%(n)s embryos", n=v["embryos"]))
    return f" ({', '.join(bits)})" if bits else ""


@action(name="clutch_change", area="zebrafish", title="Change a clutch",
        help="Set any of these on one clutch (counts as they grow, say); what is left out stays.", target="clutch",
        fields=CLUTCH_FIELDS,
        describe=lambda s, c, v: gettext("Clutch %(clutch)s: %(fields)s", clutch=c.clutch_id,
                                         fields=", ".join(f"{k} → {_named(s, k, v[k])}" for k in v)))
def _clutch_change(s, clutch, v):
    if not v:
        raise ActionError(gettext("Say what to change on clutch %(clutch)s.", clutch=clutch.clutch_id))
    return [Call("POST", f"/zebrafish/clutches/{clutch.id}/update", form=_clutch_form(s, v))]


FISH_FIELDS = (Field("line", "fish_line", "Their line"),
               Field("count", "int", "How many fish the row is"),
               Field("sex", "text", "M, F, mixed or unknown", choices=tuple(FISH_SEX_OPTIONS)),
               Field("status", "text", "alive, transfer, geno or sac", choices=tuple(FISH_STATUS_OPTIONS)),
               Field("genotype", "text", "Their genotype"),
               Field("individual_id", "text", "A single fish's own ID"),
               Field("date_of_fertilization", "date", "When they were fertilised"),
               Field("notes", "text", "Notes (replaces them)"))


def _fish_form(s, v) -> list[tuple[str, str]]:
    form = [(k, str(v[k])) for k in ("count", "sex", "status", "genotype", "individual_id",
                                      "date_of_fertilization", "notes") if k in v]
    if "line" in v:
        form.append(("line_id_fk", _id(s, "fish_line", v["line"])))
    if "tank" in v:
        form.append(("tank_id_fk", _id(s, "tank", v["tank"])))
    return form


@action(name="fish_new", area="zebrafish", title="New fish row",
        help="Fish in a tank: one row per group (line, sex, age), or a single fish with its own ID.", target=None,
        fields=(Field("tank", "tank", "The tank they are in", required=True), *FISH_FIELDS),
        describe=lambda s, _t, v: gettext("%(n)s fish in %(tank)s%(what)s", n=v.get("count", 1),
                                          tank=find(s, "tank", v["tank"]).tank_id,
                                          what=f" ({find(s, 'fish_line', v['line']).name})"
                                          if v.get("line") not in (None, "") else ""))
def _fish_new(s, _target, v):
    return [Call("POST", "/zebrafish/fish/create", form=_fish_form(s, v))]


@action(name="fish_change", area="zebrafish", title="Change a fish row",
        help="Set any of these on one fish row; tank moves it. What is left out stays.", target="fish",
        fields=(Field("tank", "tank", "Move the row to this tank"), *FISH_FIELDS),
        describe=lambda s, f, v: gettext("Fish row #%(row)s in %(tank)s: %(fields)s", row=f.id,
                                         tank=f.tank.tank_id if f.tank else "-",
                                         fields=", ".join(f"{k} → {_named(s, k, v[k])}" for k in v)))
def _fish_change(s, fish, v):
    if not v:
        raise ActionError(gettext("Say what to change on fish row #%(row)s.", row=fish.id))
    return [Call("POST", f"/zebrafish/fish/{fish.id}/update", form=_fish_form(s, v))]


@action(name="fish_sac", area="zebrafish", title="Log fish sac'd",
        help="Fish sacrificed or found dead: from a fish row (taken off its count), or only logged for a tank.",
        target=None,
        fields=(Field("fish", "fish", "The fish row they came from"),
                Field("tank", "tank", "Their tank, when there is no fish row"),
                Field("line", "fish_line", "Their line, when there is no fish row"),
                Field("count", "int", "How many (1 if left out)"),
                Field("date", "date", "When (today if left out)"),
                Field("reason", "text", "Why")),
        describe=lambda s, _t, v: gettext("%(n)s fish sac'd from %(where)s%(on)s%(why)s", n=v.get("count", 1),
                                          where=_sac_where(s, v), on=_on(v.get("date")),
                                          why=f" ({v['reason']})" if v.get("reason") else ""))
def _fish_sac(s, _target, v):
    if v.get("fish") in (None, "") and v.get("tank") in (None, ""):
        raise ActionError(gettext("Say which fish row or tank they came from."))
    form = [("fish_id_fk", _id(s, "fish", v.get("fish"))), ("count", str(v.get("count", 1))),
            ("sac_date", v.get("date", "")), ("reason", v.get("reason", ""))]
    if v.get("fish") in (None, ""):
        form += [("tank_id_fk", _id(s, "tank", v.get("tank"))), ("line_id_fk", _id(s, "fish_line", v.get("line")))]
    return [Call("POST", "/zebrafish/sac/create", form=form)]


def _sac_where(s, v) -> str:
    if v.get("fish") not in (None, ""):
        f = find(s, "fish", v["fish"])
        return gettext("row #%(row)s in %(tank)s", row=f.id, tank=f.tank.tank_id if f.tank else "-")
    return find(s, "tank", v["tank"]).tank_id if v.get("tank") not in (None, "") else "-"


@action(name="water_reading", area="zebrafish", title="Water reading",
        help="A reading for one water system.", target=None,
        fields=(Field("system", "water_system", "The water system", required=True),
                Field("ph", "number", "pH"),
                Field("conductivity", "number", "Conductivity (µS/cm)"),
                Field("temperature", "number", "Temperature (°C)"),
                Field("salinity", "number", "Salinity (ppt)"),
                Field("alarm", "bool", "An alarm went off"),
                Field("notes", "text", "A note")),
        describe=lambda s, _t, v: gettext("Water reading for %(system)s: %(values)s",
                                          system=find(s, "water_system", v["system"]).name,
                                          values=", ".join(f"{k} {_named(s, k, v[k])}" for k in v if k != "system")
                                          or "-"))
def _water(s, _target, v):
    form = [("system_id_fk", _id(s, "water_system", v["system"]))]
    form += [(k, f"{v[k]:g}") for k in ("ph", "conductivity", "salinity") if k in v]
    if "temperature" in v:
        form.append(("temperature_c", f"{v['temperature']:g}"))
    if v.get("alarm"):
        form.append(("alarm", "1"))
    form.append(("notes", v.get("notes", "")))
    return [Call("POST", "/zebrafish/water/log", form=form)]


# ---------------------------------------------------------------- flies and worms

def _module(s, key) -> StockModule:
    row = s.scalar(select(StockModule).where(func.lower(StockModule.key) == str(key or "").strip().lower()))
    if row is None:
        raise ActionError(gettext("There is no fly or worm database “%(key)s”.", key=key))
    return row


def _unit_code(s, unit) -> str:
    from . import stock_service as svc
    return svc.view(s.get(StockModule, unit.module_id_fk)).code(unit)


UNIT_FIELDS = (Field("genotype", "text", "Its genotype"),
               Field("purpose", "text", "One of the database's purposes (stock, cross, progeny…; see vocabulary)"),
               Field("female_genotype", "text", "A cross's female parents"),
               Field("male_genotype", "text", "A cross's male parents"),
               Field("owner", "text", "Username of its owner"),
               Field("rack", "stock_rack", "The rack (tray) it is in"),
               Field("position", "text", "Its position in the rack"),
               Field("set_up_on", "date", "When it was set up"),
               Field("generation", "text", "Its generation (F1, F2…)"),
               Field("notes", "text", "Its notes (replaces them)"))
UNIT_DATES = (Field("ready_on", "date", "When progeny are due"),
              Field("shift_on", "date", "When it moves to another temperature"),
              Field("shift_to", "text", "The temperature it moves to"),
              Field("score_on", "date", "When to score it"))


def _unit_form(s, module, v) -> list[tuple[str, str]]:
    form = [(k, str(v[k])) for k in ("genotype", "purpose", "female_genotype", "male_genotype", "owner",
                                      "set_up_on", "generation", "notes", "ready_on", "shift_on", "shift_to",
                                      "score_on") if k in v]
    if "rack" in v or "position" in v:
        rack = find(s, "stock_rack", v["rack"]) if v.get("rack") not in (None, "") else None
        if rack is not None and rack.module_id_fk != module.id:
            raise ActionError(gettext("Rack %(rack)s is in another database.", rack=rack.name))
        form += [("rack_id", str(rack.id) if rack else ""), ("position", str(v.get("position", "")))]
    return form


@action(name="stock_new", area="stocks", title="New vial or plate",
        help="One or several alike (count, side by side in the rack), in a fly or worm database.", target=None,
        fields=(Field("database", "text", "The database's key (see vocabulary)", required=True),
                Field("count", "int", "How many alike (1–60)"), *UNIT_FIELDS),
        describe=lambda s, _t, v: gettext("%(n)s new in %(database)s%(what)s", n=v.get("count", 1),
                                          database=_module(s, v["database"]).label,
                                          what=f" ({v['genotype']})" if v.get("genotype") else ""))
def _stock_new(s, _target, v):
    module = _module(s, v["database"])
    return [Call("POST", f"/stocks/{module.key}/units/save",
                 form=[("count", str(v.get("count", 1))), *_unit_form(s, module, v)])]


@action(name="stock_change", area="stocks", title="Change a vial or plate",
        help="Set any of these on one vial or plate; what is left out stays as it is.", target="stock_unit",
        fields=(*UNIT_FIELDS, *UNIT_DATES),
        describe=lambda s, u, v: gettext("%(code)s: %(fields)s", code=_unit_code(s, u),
                                         fields=", ".join(f"{k} → {_stock_said(s, k, v[k])}" for k in v)))
def _stock_change(s, unit, v):
    if not v:
        raise ActionError(gettext("Say what to change on %(code)s.", code=_unit_code(s, unit)))
    module = s.get(StockModule, unit.module_id_fk)
    return [Call("POST", f"/stocks/{module.key}/units/{unit.id}/update", form=_unit_form(s, module, v))]


def _stock_said(s, key, value) -> str:
    return find(s, "stock_rack", value).name if key == "rack" and value else (str(value) if value != "" else "-")


STOCK_EVENTS = {"copy": "duplicate", "collect": "collect", "discard": "discard", "restore": "restore",
                "shifted": "shifted", "ready_done": "ready-done", "scored": "scored"}


@action(name="stock_event", area="stocks", title="Something done to a vial or plate",
        help="copy (a fresh one of the same stock, as when flipping), collect (eggs from a cross: a new progeny "
             "vial), discard, restore, shifted (moved to its new temperature), ready_done (progeny dealt with), "
             "scored.", target="stock_unit",
        fields=(Field("event", "text", "What was done", required=True, choices=tuple(STOCK_EVENTS)),
                Field("rack", "stock_rack", "For shifted: the rack it moved to")),
        describe=lambda s, u, v: gettext("%(code)s: %(event)s", code=_unit_code(s, u), event=v["event"]))
def _stock_event(s, unit, v):
    module = s.get(StockModule, unit.module_id_fk)
    form = []
    if v.get("rack") not in (None, ""):
        if v["event"] != "shifted":
            raise ActionError(gettext("rack only goes with shifted."))
        form.append(("rack_id", str(find(s, "stock_rack", v["rack"]).id)))
    return [Call("POST", f"/stocks/{module.key}/units/{unit.id}/{STOCK_EVENTS[v['event']]}", form=form)]


@action(name="rack_flipped", area="stocks", title="Rack flipped",
        help="Every vial or plate in this rack (tray) was flipped or chunked.", target="stock_rack",
        fields=(Field("date", "date", "When (today if left out)"),),
        describe=lambda _s, r, v: gettext("Rack %(rack)s flipped%(on)s", rack=r.name, on=_on(v.get("date"))))
def _rack_flipped(s, rack, v):
    module = s.get(StockModule, rack.module_id_fk)
    return [Call("POST", f"/stocks/{module.key}/racks/{rack.id}/flipped", form=[("on", v.get("date", ""))])]


FROZEN_FIELDS = (Field("genotype", "text", "Its genotype"),
                 Field("vials", "int", "How many vials were frozen"),
                 Field("vials_left", "int", "How many are left"),
                 Field("frozen_on", "date", "When it was frozen"),
                 Field("thaw_tested_on", "date", "When a thaw was tested"),
                 Field("thaw_ok", "bool", "Whether the thaw test worked"),
                 Field("location", "text", "Where it is kept"),
                 Field("owner", "text", "Username of its owner"),
                 Field("notes", "text", "Notes"))


def _frozen_form(v, lot=None) -> list[tuple[str, str]]:
    """The whole form (the page saves every field), from the lot's own values
    with the given ones over them."""
    def now(key, attr=None):
        value = getattr(lot, attr or key, None) if lot is not None else None
        return "" if value is None else str(value)
    form = []
    for key in ("genotype", "vials", "vials_left", "frozen_on", "thaw_tested_on", "location", "owner", "notes"):
        form.append((key, str(v[key]) if key in v else now(key)))
    ok = v.get("thaw_ok", lot.thaw_ok if lot is not None else None)
    form.append(("thaw_ok", "" if ok is None else ("1" if ok else "0")))
    return form


@action(name="frozen_new", area="stocks", title="New frozen stock",
        help="A lot of frozen vials (worms).", target=None,
        fields=(Field("database", "text", "The database's key", required=True), *FROZEN_FIELDS),
        describe=lambda s, _t, v: gettext("Frozen %(genotype)s in %(database)s%(vials)s",
                                          genotype=v.get("genotype", "-"), database=_module(s, v["database"]).label,
                                          vials=f" ×{v['vials']}" if v.get("vials") else ""))
def _frozen_new(s, _target, v):
    module = _module(s, v["database"])
    return [Call("POST", f"/stocks/{module.key}/frozen/save", form=_frozen_form(v))]


@action(name="frozen_change", area="stocks", title="Change a frozen stock",
        help="Vials used, a thaw test, where it is…; what is left out stays.", target="stock_frozen",
        fields=FROZEN_FIELDS,
        describe=lambda _s, f, v: gettext("Frozen %(genotype)s: %(fields)s", genotype=f.genotype,
                                          fields=", ".join(f"{k} → {_named(_s, k, v[k])}" for k in v)))
def _frozen_change(s, lot, v):
    if not v:
        raise ActionError(gettext("Say what to change on frozen %(genotype)s.", genotype=lot.genotype))
    module = s.get(StockModule, lot.module_id_fk)
    return [Call("POST", f"/stocks/{module.key}/frozen/save", form=[("id", str(lot.id)), *_frozen_form(v, lot)])]


# ---------------------------------------------------------------- any other organism

def _org_module(s, key) -> OrganismModule:
    row = s.scalar(select(OrganismModule).where(func.lower(OrganismModule.key) == str(key or "").strip().lower()))
    if row is None:
        raise ActionError(gettext("There is no organism database “%(key)s”.", key=key))
    return row


def _org_codes(model):
    """Records written by their code, which is unique within one database."""
    return lambda s, code: list(s.scalars(select(model).where(model.code == code)))


KINDS.update({
    "org_animal": (Organism, None, _org_codes(Organism)),
    "org_housing": (OrgHousing, None, _org_codes(OrgHousing)),
    "org_line": (OrgLine, None, _org_codes(OrgLine)),
    "org_cohort": (OrgCohort, None, _org_codes(OrgCohort)),
    "org_cross": (OrgCross, None, _org_codes(OrgCross)),
    "org_location": (OrgLocation, "name", "name"),
    "org_due": (OrgDue, None, None),
})

# Per entity: its save page, its kind, and its fields as (field, form name).
# A field whose kind is a record is checked to be in the same database (the
# page would quietly drop one from another).
ORG_ENTITIES = {
    "animal": ("animal", "org_animal", "animals or groups", (
        (Field("how_many", "int", "New ones only: how many records alike"), "how_many"),
        (Field("group_size", "int", "How many animals this record is (a group), 1 for one animal"), "count"),
        (Field("housing", "org_housing", "Where it lives"), "housing_id_fk"),
        (Field("line", "org_line", "Its line"), "line_id_fk"),
        (Field("cohort", "org_cohort", "Its cohort"), "cohort_id_fk"),
        (Field("parent_a", "org_animal", "A parent"), "parent_a_id_fk"),
        (Field("parent_b", "org_animal", "The other parent"), "parent_b_id_fk"),
        (Field("sex", "text", "Its sex, as the database writes it"), "sex"),
        (Field("status", "text", "Its status (see vocabulary)"), "status"),
        (Field("genotype", "text", "Its genotype"), "genotype"),
        (Field("owner", "text", "Username of its owner"), "owner"),
        (Field("protocol", "text", "Its protocol"), "protocol"),
        (Field("birth_on", "date", "Born or hatched"), "birth_on"),
        (Field("death_on", "date", "Died or removed"), "death_on"),
        (Field("last_procedure_on", "date", "Its last procedure"), "last_procedure_on"),
        (Field("notes", "text", "Notes (replaces them)"), "notes"))),
    "housing": ("housing", "org_housing", "housing", (
        (Field("how_many", "int", "New ones only: how many alike"), "how_many"),
        (Field("purpose", "text", "What it is for"), "purpose"),
        (Field("line", "org_line", "The line it holds"), "line_id_fk"),
        (Field("location", "org_location", "The rack or room it is in"), "location_id_fk"),
        (Field("position", "text", "Its position in the rack"), "position"),
        (Field("owner", "text", "Username of its owner"), "owner"),
        (Field("protocol", "text", "Its protocol"), "protocol"),
        (Field("card_id", "text", "Its card label"), "card_id"),
        (Field("established_on", "date", "Set up on"), "established_on"),
        (Field("last_serviced_on", "date", "Last cleaned, fed or serviced"), "last_serviced_on"),
        (Field("retired", "bool", "No longer in use"), "retired"),
        (Field("needs_attention", "bool", "Flag it for attention"), "needs_attention"),
        (Field("notes", "text", "Notes (replaces them)"), "notes"))),
    "line": ("line", "org_line", "lines", (
        (Field("name", "text", "Its name"), "name"),
        (Field("genotype", "text", "Its genotype"), "genotype"),
        (Field("owner", "text", "Username of its owner"), "owner"),
        (Field("protocol", "text", "Its protocol"), "protocol"),
        (Field("source", "text", "Where it came from"), "source"),
        (Field("external_ref", "text", "A stock centre number or link"), "external_ref"),
        (Field("parent_line", "org_line", "The line it was made from"), "parent_line_id_fk"),
        (Field("last_refreshed_on", "date", "Last refreshed"), "last_refreshed_on"),
        (Field("last_frozen_on", "date", "Last frozen"), "last_frozen_on"),
        (Field("retired", "bool", "No longer kept"), "retired"),
        (Field("notes", "text", "Notes (replaces them)"), "notes"))),
    "cross": ("cross", "org_cross", "crosses", (
        (Field("cross_type", "text", "pair, trio, harem, mass…"), "cross_type"),
        (Field("housing", "org_housing", "Where it is set up"), "housing_id_fk"),
        (Field("sire_line", "org_line", "The father's line"), "sire_line_id_fk"),
        (Field("dam_line", "org_line", "The mother's line"), "dam_line_id_fk"),
        (Field("sire_label", "text", "The father(s), in words"), "sire_label"),
        (Field("dam_label", "text", "The mother(s), in words"), "dam_label"),
        (Field("set_up_on", "date", "Set up on"), "set_up_on"),
        (Field("expected_on", "date", "Offspring expected"), "expected_on"),
        (Field("collected_on", "date", "Offspring collected"), "collected_on"),
        (Field("owner", "text", "Username of its owner"), "owner"),
        (Field("notes", "text", "Notes (replaces them)"), "notes"))),
    "cohort": ("cohort", "org_cohort", "cohorts", (
        (Field("line", "org_line", "Its line"), "line_id_fk"),
        (Field("cross", "org_cross", "The cross it came from"), "cross_id_fk"),
        (Field("location", "org_location", "Where it is"), "location_id_fk"),
        (Field("birth_on", "date", "Born or hatched"), "birth_on"),
        (Field("stage", "text", "Its stage"), "stage"),
        (Field("count_initial", "int", "How many there were"), "count_initial"),
        (Field("count_current", "int", "How many there are now"), "count_current"),
        (Field("owner", "text", "Username of its owner"), "owner"),
        (Field("notes", "text", "Notes (replaces them)"), "notes"))),
}
ATTRS_FIELD = Field("fields", "object", "The database's own fields, by key: {\"tank_volume\": \"3 L\"} (see vocabulary)")


def _org_form(s, module, entity: str, v) -> list[tuple[str, str]]:
    form = []
    for f, name in ORG_ENTITIES[entity][3]:
        if f.name not in v:
            continue
        value = v[f.name]
        if f.kind in KINDS:
            rec = find(s, f.kind, value) if value not in (None, "") else None
            if rec is not None and rec.module_id_fk != module.id:
                raise ActionError(gettext("%(field)s is in another database.", field=f.name))
            form.append((name, str(rec.id) if rec else ""))
        elif f.kind == "bool":
            form.append((name, "1" if value else ""))  # a box sent empty is unticked
        else:
            form.append((name, str(value)))
    for key, value in (v.get("fields") or {}).items():
        form.append((f"attr_{key}", "1" if value is True else "" if value is False else str(value)))
    return form


def _org_said(s, entity: str, v) -> str:
    kinds = {f.name: f.kind for f, _n in ORG_ENTITIES[entity][3]}
    return ", ".join(f"{k} → {_org_value(s, kinds.get(k, ''), k, v[k])}" for k in v if k != "database")


def _org_value(s, kind: str, key: str, value) -> str:
    if key == "fields" and isinstance(value, dict):
        return ", ".join(f"{k}: {x}" for k, x in value.items())
    if kind in KINDS and value not in (None, ""):
        rec = find(s, kind, value)
        return getattr(rec, "code", None) or getattr(rec, "name", None) or f"#{rec.id}"
    if isinstance(value, bool):
        return gettext("yes") if value else gettext("no")
    return str(value) if value != "" else "-"


def _org_actions():
    for entity, (path, kind, plural, pairs) in ORG_ENTITIES.items():
        fields = tuple(f for f, _n in pairs)
        new_fields = (Field("database", "text", "The organism database's key (see vocabulary)", required=True),
                      Field("code", "text", "Its ID (the next free one if left out)"), *fields, ATTRS_FIELD)
        change_fields = (Field("code", "text", "A new ID for it"),
                         *(f for f in fields if f.name != "how_many"), ATTRS_FIELD)

        def build_new(s, _t, v, path=path, entity=entity):
            module = _org_module(s, v["database"])
            form = _org_form(s, module, entity, v)
            if "code" in v:
                form.append(("code", v["code"]))
            return [Call("POST", f"/organisms/{module.key}/{path}/save", form=[("id", ""), *form])]

        def build_change(s, rec, v, path=path, entity=entity):
            if not v:
                raise ActionError(gettext("Say what to change on %(record)s.", record=rec.code or f"#{rec.id}"))
            module = s.get(OrganismModule, rec.module_id_fk)
            form = _org_form(s, module, entity, v)
            if "code" in v:
                form.append(("code", v["code"]))
            return [Call("POST", f"/organisms/{module.key}/{path}/save", form=[("id", str(rec.id)), *form])]

        action(name=f"org_{entity}_new", area="organisms", title=f"New: {entity}",
               help=f"Add to an organism database's {plural}.", target=None, fields=new_fields,
               describe=lambda s, _t, v, entity=entity: gettext(
                   "New %(what)s in %(database)s: %(fields)s", what=entity,
                   database=_org_module(s, v["database"]).label, fields=_org_said(s, entity, v) or "-"))(build_new)
        action(name=f"org_{entity}_change", area="organisms", title=f"Change: {entity}",
               help=f"Set any of these on one of an organism database's {plural}; what is left out stays.",
               target=kind, fields=change_fields,
               describe=lambda s, r, v, entity=entity: gettext("%(record)s: %(fields)s", record=r.code or f"#{r.id}",
                                                               fields=_org_said(s, entity, v)))(build_change)


_org_actions()


@action(name="org_reading", area="organisms", title="Environment reading",
        help="Readings for one rack or room (temperature, humidity… as the database measures them).", target=None,
        fields=(Field("location", "org_location", "The rack or room", required=True),
                Field("values", "object", "Each reading by its key: {\"temperature\": 26.5} (see vocabulary)",
                      required=True),
                Field("alarms", "list", "Keys of readings that set off an alarm"),
                Field("notes", "text", "A note")),
        describe=lambda s, _t, v: gettext("Reading for %(where)s: %(values)s",
                                          where=find(s, "org_location", v["location"]).name,
                                          values=", ".join(f"{k} {x}" for k, x in v["values"].items())))
def _org_reading(s, _target, v):
    loc = find(s, "org_location", v["location"])
    module = s.get(OrganismModule, loc.module_id_fk)
    if not isinstance(v["values"], dict) or not v["values"]:
        raise ActionError(gettext("values is an object of readings by key."))
    form = [("location_id_fk", str(loc.id)), ("notes", v.get("notes", ""))]
    form += [(f"metric_{k}", str(x)) for k, x in v["values"].items()]
    form += [(f"alarm_{k}", "1") for k in v.get("alarms", [])]
    return [Call("POST", f"/organisms/{module.key}/reading/save", form=form)]


@action(name="org_genotype", area="organisms", title="Genotype call",
        help="Record a genotype result for an animal (or group) or a housing unit.", target="org_animal|org_housing",
        fields=(Field("result", "text", "The result", required=True),
                Field("assay", "text", "The assay (PCR, sequencing…)"),
                Field("zygosity", "text", "het, hom, wt…"),
                Field("called_on", "date", "When (today if left out)"),
                Field("notes", "text", "Notes")),
        describe=lambda _s, r, v: gettext("Genotype of %(record)s: %(result)s", record=r.code or f"#{r.id}",
                                          result=" ".join(filter(None, [v.get("assay"), v["result"],
                                                                        v.get("zygosity")]))))
def _org_genotype(s, rec, v):
    module = s.get(OrganismModule, rec.module_id_fk)
    kind = "organism" if isinstance(rec, Organism) else "housing"
    return [Call("POST", f"/organisms/{module.key}/genotype/save", form=[
        ("subject_kind", kind), ("subject_id", str(rec.id)),
        *[(k, str(v.get(k, ""))) for k in ("result", "assay", "zygosity", "called_on", "notes")]])]


def _due_said(s, due) -> str:
    from . import organism_service as svc
    subject = svc.due_subject(s, due)
    who = (getattr(subject, "code", None) or f"#{subject.id}") if subject is not None else ""
    return f"{due.rule_key.replace('_', ' ')} {who} ({gettext('due %(day)s', day=due.due_on.isoformat())})".strip()


@action(name="org_due_done", area="organisms", title="Scheduled job done",
        help="Mark one item on an organism database's schedule done (from whats_due).", target="org_due",
        fields=(Field("done_on", "date", "When (today if left out)"),),
        describe=lambda s, d, v: gettext("Done%(on)s: %(what)s", on=_on(v.get("done_on")), what=_due_said(s, d)))
def _org_due_done(s, due, v):
    module = s.get(OrganismModule, due.module_id_fk)
    return [Call("POST", f"/organisms/{module.key}/due/{due.id}/done", form=[("done_on", v.get("done_on", ""))])]


# ---------------------------------------------------------------- to-dos

KINDS["task"] = (TaskItem, None, None)


@action(name="task_done", area="calendar", title="To-do done",
        help="Tick a to-do done. One already done stays done.", target="task", fields=(),
        describe=lambda _s, t, _v: gettext("Done: %(task)s", task=t.title))
def _task_done(_s, task, _v):
    # The calendar's toggle flips: send it only to tick, never to untick.
    return [] if task.done_at is not None else [Call("POST", f"/calendar/items/task-{task.id}/toggle", form=[])]


# ---------------------------------------------------------------- experiments

def _experiments_named(s, name: str) -> list:
    rows = list(s.scalars(select(Experiment).where(func.lower(Experiment.name) == name.lower())))
    return rows or ([s.get(Experiment, int(name))] if name.isdigit() else [])


KINDS.update({
    "experiment": (Experiment, None, _experiments_named),
    "experiment_step": (ExperimentStep, None, None),
})


def _place(s, exp):
    from . import experiments as xp
    place = xp.place_for(s, exp.db or "colony")
    if place is None:
        raise ActionError(gettext("You can't open the database of the experiment “%(name)s”.", name=exp.name))
    return place


def _subject_keys(s, exp, names) -> list[str]:
    """The experiment's animals a person named: by key ("mouse:12"), label
    ("#1043", "1043"), or a reference to the mouse."""
    from . import experiments as xp
    place = _place(s, exp)
    members = xp.subjects(s, exp, place)
    by = {x.key: x for x in members}
    by.update({x.label.lstrip("#"): x for x in members})
    keys = []
    for name in names:
        if isinstance(name, dict) and name.get("kind") == "mouse":
            name = f"mouse:{name.get('id')}"
        x = by.get(str(name).strip().lstrip("#"))
        if x is None:
            raise ActionError(gettext("%(animal)s isn't in the experiment “%(name)s”.", animal=_show(name), name=exp.name))
        keys.append(x.key)
    return keys


def _step_day(step, exp, v) -> int:
    from .experiment_steps import day_date, safe_days
    days = safe_days(step)
    if "day" in v:
        if v["day"] not in days:
            raise ActionError(gettext("Day %(day)s is not in this step's plan (%(days)s).", day=v["day"], days=step.days))
        return v["day"]
    on = date.fromisoformat(v["done_on"]) if v.get("done_on") else date.today()
    for day in days:
        if day_date(exp.start_date, day) == on:
            return day
    raise ActionError(gettext("No day of this step falls on %(date)s; say which day of the plan (%(days)s).",
                              date=on.isoformat(), days=step.days))


def _step_label(s, step) -> str:
    exp = s.get(Experiment, step.experiment_id_fk)
    what = step.agent or step.kind
    return f"{what} · {exp.name}" if exp else what


def _step_reagent(s, step, v):
    """The inventory item a step done used, which the record keeps with its
    lot: the reagent named, or the item of that name with the lot named;
    else the step's own item, or the one item named as its agent. An
    unknown lot, or several items to choose from, refused in words."""
    from .experiment_steps import default_reagent, reagents_named
    from .models import InventoryItem
    on = date.fromisoformat(v["done_on"]) if v.get("done_on") else date.today()
    item = find(s, "inventory_item", v["reagent"]) if v.get("reagent") not in (None, "") else None
    if item is None and not v.get("lot"):
        item_id, named = default_reagent(s, step, on)
        if item_id is None and len(named) > 1:
            raise ActionError(gettext("%(name)s is in the inventory more than once (%(items)s): say which was used, with reagent or lot.", name=step.agent, items="; ".join(_lot_said(s, i) for i in named[:6])))  # noqa: E501
        return s.get(InventoryItem, item_id) if item_id else None
    if v.get("lot"):
        planned = s.get(InventoryItem, step.reagent_item_id_fk) if step.reagent_item_id_fk else None
        name = (item or planned).name if (item or planned) is not None else step.agent
        named = reagents_named(s, name)
        lot = str(v["lot"]).strip().lower()
        match = next((i for i in ([item] if item is not None else []) + named if (i.lot or "").strip().lower() == lot),
                     None)
        if match is None:
            known = "; ".join(_lot_said(s, i) for i in named[:6])
            raise ActionError(gettext("There is no %(name)s of lot %(lot)s in the inventory (%(known)s): add the new lot as an item first, or name one there is.", name=name, lot=v["lot"], known=known or gettext("none")))  # noqa: E501
        item = match
    return item


def _lot_said(s, item) -> str:
    """"Reagents #3 · Tamoxifen, lot L7, expires 2027-01-31"."""
    parts = [_item_label(s, item)]
    if item.lot:
        parts.append(gettext("lot %(lot)s", lot=item.lot))
    if item.expires_on:
        parts.append(gettext("expires %(date)s", date=item.expires_on.isoformat()))
    return ", ".join(parts)


def _step_done_said(s, step, v) -> str:
    said = gettext("Experiment: %(step)s, day %(day)s done%(on)s%(who)s", step=_step_label(s, step),
                   day=_step_day(step, s.get(Experiment, step.experiment_id_fk), v), on=_on(v.get("done_on")),
                   who=gettext(" for %(n)s animals", n=len(v["animals"])) if v.get("animals") else "")
    item = _step_reagent(s, step, v)
    if item is not None:
        said += gettext(", using %(item)s", item=_lot_said(s, item))
        if v.get("take_off"):
            said += gettext(", taken off its quantity")
    return said


@action(name="experiment_step_done", area="experiments", title="Mark a step done",
        help="Record a planned step (an injection, a readout day…) as done, for all of its animals or some. "
             "Steps and their days are in GET /api/v1/experiments/{id} (page.steps). The record keeps the "
             "inventory item used and its lot: the step's own item (page.steps[].reagent) unless reagent or lot "
             "says otherwise, or the one item named as the step's agent; with several of that name, say which. "
             "A lot that expired before the day is warned about; take_off takes what was given off the item.", target="experiment_step",
        fields=(Field("day", "int", "Which day of the plan (found from done_on if left out)"),
                Field("done_on", "date", "When it was done (today if left out)"),
                Field("animals", "list", "Only these animals: their keys or labels (all of the step's group if left out)"),
                Field("values", "object", "For a readout step: each animal's value, by key or label"),
                Field("note", "text", "A note"),
                Field("reagent", "inventory_item", "The inventory item used (reagents:12), when it isn't the step's own"),
                Field("lot", "text", "The lot used, when it isn't the item's: the item of the same name with this lot"),
                Field("take_off", "bool", "Also take what the animals got off the item's quantity")),
        describe=_step_done_said)
def _experiment_step_done(s, step, v):
    exp = s.get(Experiment, step.experiment_id_fk)
    day = _step_day(step, exp, v)
    body = {"done_on": v.get("done_on") or date.today().isoformat(), "note": v.get("note", "")}
    item = _step_reagent(s, step, v)
    if item is not None:
        body["reagent_id"] = str(item.id)
    if v.get("take_off"):
        if item is None:
            raise ActionError(gettext("take_off needs the reagent used: this step has no inventory item; name it with reagent."))  # noqa: E501
        body["take_off"] = True
    if v.get("animals"):
        body["subjects"] = _subject_keys(s, exp, v["animals"])
    if v.get("values"):
        keys = _subject_keys(s, exp, list(v["values"]))
        body["values"] = {k: str(x) for k, x in zip(keys, v["values"].values())}
    return [Call("POST", f"/colony/experiments/{exp.id}/steps/{step.id}/day/{day}/record", json=body)]


def _derived_readings(s, exp, v) -> list[str]:
    """For a readout BioManager works out (app/derived.py): the inputs given
    per animal, checked, and each animal's line "1043: Length 12 mm, Width
    8 mm → 384 mm³", the value worked out as the page will. An ActionError,
    in words, for inputs that don't fit the formula."""
    from . import derived
    from . import experiments as xp
    place = _place(s, exp)
    readout = xp.readout_for(exp, place, v.get("measure")) or xp.readout_of(exp, place)
    names = list(v["inputs"])
    keys = _subject_keys(s, exp, names)
    labels = {x.key: x.label for x in xp.subjects(s, exp, place)}
    on = date.fromisoformat(v["date"]) if v.get("date") else date.today()
    checked = {}
    for name, key in zip(names, keys):
        try:
            checked[key] = derived.check(readout["formula"], v["inputs"][name])
        except derived.DerivedError as e:
            raise ActionError(f"{_show(name)}: {e}") from None
    try:
        values = xp.derive_day(s, exp, place, on, checked, readout=readout)
    except derived.DerivedError as e:
        raise ActionError(str(e)) from None
    unit = f" {readout['unit']}" if readout["unit"] else ""
    lines = [f"{labels.get(k, k)}: {derived.inputs_text(readout['formula'], checked[k])} → {values[k]:g}{unit}"
             for k in checked]
    return lines


def _reading_check(s, exp, v) -> list[str]:
    """The lines a readout change shows; refuses inputs for a readout typed
    as a number, and a number for one BioManager works out."""
    from . import experiments as xp
    readout = xp.readout_for(exp, _place(s, exp), v.get("measure"))
    if readout is None:
        raise ActionError(gettext("Experiment %(name)s has no measure “%(measure)s”; its other measures: %(names)s.",
                                  name=exp.name, measure=v.get("measure"),
                                  names=", ".join(m["label"] for m in xp.measures_of(exp)) or gettext("none yet")))
    if not v.get("values") and not v.get("inputs"):
        raise ActionError(gettext("experiment_reading needs values (or, for a readout BioManager works out, inputs)."))
    if readout.get("formula"):
        if v.get("values"):
            raise ActionError(gettext("“%(name)s”'s readout, %(readout)s, is worked out by BioManager from %(inputs)s: send them as inputs for each animal, not values.",  # noqa: E501
                                      name=exp.name, readout=translate_value(readout["label"]),
                                      inputs=", ".join(i["key"] for i in readout["inputs"])))
        return _derived_readings(s, exp, v)
    if v.get("inputs"):
        raise ActionError(gettext("“%(name)s”'s readout, %(readout)s, is one number for each animal: send values, not inputs.",
                                  name=exp.name, readout=translate_value(readout["label"])))
    return []


@action(name="experiment_reading", area="experiments", title="Record a readout",
        help="The experiment's readout (a weight, a count, a score) for some animals on one day. When the readout "
             "is one BioManager works out (its key starts derived:, e.g. a tumour volume from length and width; "
             "GET /api/v1/experiments/{id} has readout.inputs), send what was measured as inputs and BioManager "
             "computes the value: never work it out yourself.",
        target="experiment",
        fields=(Field("values", "object", "Each animal's reading, by its key or label: {\"1043\": 24.5}"),
                Field("inputs", "object", "For a readout BioManager works out: each animal's measurements, by its key "
                      "or label: {\"1043\": {\"length\": 12.1, \"width\": 8.4}}"),
                Field("date", "date", "The day (today if left out)"),
                Field("measure", "text", "Another measure of the experiment to record instead of its main readout "
                      "(its name, e.g. IL-6 ELISA, as plate_readout gives it)")),
        describe=lambda s, e, v: _reading_summary(s, e, v))
def _experiment_reading(s, exp, v):
    _reading_check(s, exp, v)
    body = {"date": v.get("date") or date.today().isoformat()}
    if v.get("measure"):
        body["measure"] = v["measure"]
    if v.get("inputs"):
        body["inputs"] = v["inputs"]
    else:
        body["values"] = v["values"]
    return [Call("POST", f"/api/v1/experiments/{exp.id}/readings", json=body)]


def _reading_summary(s, exp, v) -> str:
    lines = _reading_check(s, exp, v)
    if v.get("measure"):
        head = gettext("Experiment “%(name)s”: %(measure)s%(on)s for %(n)s animals", name=exp.name,
                       measure=v["measure"], on=_on(v.get("date")), n=len(v.get("inputs") or v.get("values") or {}))
    else:
        head = gettext("Experiment “%(name)s”: readout%(on)s for %(n)s animals", name=exp.name, on=_on(v.get("date")),
                       n=len(v.get("inputs") or v.get("values") or {}))
    return f"{head}: {'; '.join(lines)}" if lines else head


def _measure_of(s, exp, v) -> dict:
    """The other measure a change names (its name or key); an ActionError, in words, for none."""
    from . import experiments as xp
    m = xp.readout_for(exp, _place(s, exp), v.get("measure"))
    if m is None or not m.get("measure"):
        raise ActionError(gettext("Experiment %(name)s has no measure “%(measure)s”; its other measures: %(names)s.",
                                  name=exp.name, measure=v.get("measure"),
                                  names=", ".join(x["label"] for x in xp.measures_of(exp)) or gettext("none yet")))
    return m


def _measure_change_said(s, exp, v) -> str:
    from . import experiments as xp
    m = _measure_of(s, exp, v)
    if v.get("delete"):
        return gettext("Experiment “%(name)s”: delete the measure %(measure)s, with its %(n)s readings", name=exp.name,
                       measure=m["label"], n=xp.measure_count(s, exp, m["key"]))
    return gettext("Experiment “%(name)s”, measure %(measure)s: %(fields)s", name=exp.name, measure=m["label"],
                   fields=", ".join(f"{k} → {v[k]}" for k in ("label", "kind", "unit", "control") if k in v))


@action(name="experiment_measure_change", area="experiments", title="Rename or delete a measure",
        help="Another measure of an experiment (beside its main readout; GET /api/v1/experiments/{id} lists "
             "page.measures): rename it or change its unit (its readings stay), change its kind (a number, "
             "delta_ct, delta_delta_ct or fold_change: its readings are worked out again as that kind, and a number "
             "or a typed ΔCt has no Cts for ΔΔCt or fold change), change the control group a ΔΔCt or fold change "
             "measure is worked out against, or delete it with all its readings (delete: true).",
        target="experiment",
        fields=(Field("measure", "text", "The measure: its name, e.g. IL-6 ELISA, or key, measure:1", required=True),
                Field("label", "text", "Its new name"),
                Field("kind", "text", "Its new kind: value (a number), delta_ct, delta_delta_ct or fold_change"),
                Field("unit", "text", "Its unit (not for one worked out from Cts)"),
                Field("control", "text", "The control group, for a ΔΔCt or fold change measure"),
                Field("delete", "bool", "Delete the measure and every reading of it")),
        describe=_measure_change_said)
def _experiment_measure_change(s, exp, v):
    m = _measure_of(s, exp, v)
    if v.get("delete"):
        return [Call("POST", f"/experiments/{exp.id}/measures/{m['id']}/delete", json={})]
    body = {k: v[k] for k in ("label", "kind", "unit", "control") if k in v}
    if not body:
        raise ActionError(gettext("Say what to change on the measure %(measure)s: label, unit, control, or delete.",
                                  measure=m["label"]))
    return [Call("POST", f"/experiments/{exp.id}/measures/{m['id']}/update", json=body)]


@action(name="experiment_add_animals", area="experiments", title="Add animals to an experiment",
        help="Add mice (one by one, or a whole cage), or a tank's fish, or a housing unit's animals, to a treatment group.",
        target="experiment",
        fields=(Field("mice", "mice", "Mice to add"),
                Field("cage", "cage", "Every living mouse in this cage"),
                Field("tank", "tank", "Every fish row in this tank (a zebrafish experiment)"),
                Field("housing", "org_housing", "Every animal in this housing (an organism experiment)"),
                Field("group", "text", "Their treatment group"),
                Field("reason", "text", "Why: needed when the experiment was started with a plan (GET "
                      "/api/v1/experiments/{id} has plan), since adding animals changes its groups")),
        describe=lambda s, e, v: gettext("Experiment “%(name)s”: add %(what)s%(group)s", name=e.name,
                                         what=_added_what(s, v), group=f" ({v['group']})" if v.get("group") else ""))
def _experiment_add(s, exp, v):
    from . import experiment_plan
    if experiment_plan.fixed(exp) and not v.get("reason"):
        raise ActionError(gettext("“%(name)s” was started with a plan: adding animals changes its groups. Say why in reason.", name=exp.name))  # noqa: E501
    calls = []
    group = v.get("group", "")
    reason = {"reason": v["reason"]} if v.get("reason") else {}
    for m in _mice(s, v.get("mice") or []):
        calls.append(Call("POST", f"/experiments/{exp.id}/subjects/add",
                          json={"how": "one", "value": str(m.id), "group": group, **reason}))
    if v.get("cage") not in (None, ""):
        calls.append(Call("POST", f"/experiments/{exp.id}/subjects/add",
                          json={"how": "group", "value": find(s, "cage", v["cage"]).cage_id, "group": group, **reason}))
    for key in ("tank", "housing"):
        if v.get(key) not in (None, ""):
            rec = find(s, "tank" if key == "tank" else "org_housing", v[key])
            calls.append(Call("POST", f"/experiments/{exp.id}/subjects/add",
                              json={"how": "group", "value": str(rec.id), "group": group, **reason}))
    if not calls:
        raise ActionError(gettext("Say which animals to add: mice, a cage, a tank or a housing unit."))
    return calls


def _added_what(s, v) -> str:
    bits = []
    if v.get("mice"):
        bits.append(_mice_label(_mice(s, v["mice"])))
    if v.get("cage") not in (None, ""):
        bits.append(gettext("cage %(cage)s", cage=find(s, "cage", v["cage"]).cage_id))
    if v.get("tank") not in (None, ""):
        bits.append(find(s, "tank", v["tank"]).tank_id)
    if v.get("housing") not in (None, ""):
        rec = find(s, "org_housing", v["housing"])
        bits.append(rec.code or f"#{rec.id}")
    return ", ".join(bits) or "-"


@action(name="experiment_change", area="experiments", title="Change an experiment",
        help="Its status, dates or name, its main readout's day or the test planned; what is left out stays. A "
             "planned experiment's status can't be changed here: only a person starts it, with Start on its page. "
             "To add to its description, use note_add.",
        target="experiment",
        fields=(Field("status", "text", "active, paused, done or cancelled",
                      choices=("active", "paused", "done", "cancelled")),
                Field("start_date", "date", "Day 1"),
                Field("end_date", "date", "When it ends"),
                Field("name", "text", "A new name"),
                Field("readout_day", "int", "The day of the main readout (day 1 = the start date)"),
                Field("planned_test", "text", "The statistical test planned: welch (Welch's t-test, two groups), "
                      "anova (one-way ANOVA, more groups), chi2 (χ² test, survival or another count)",
                      choices=("welch", "anova", "chi2")),
                Field("reason", "text", "Why: needed to change the readout day or the test once it was started "
                      "with a plan")),
        describe=lambda s, e, v: gettext("Experiment “%(name)s”: %(fields)s", name=e.name,
                                         fields=", ".join(f"{k} → {_named(s, k, v[k])}" for k in v if k != "reason")
                                         + (f" ({v['reason']})" if v.get("reason") else "")))
def _experiment_change(_s, exp, v):
    from . import experiment_plan
    if not [k for k in v if k != "reason"]:
        raise ActionError(gettext("Say what to change on the experiment “%(name)s”.", name=exp.name))
    if exp.status == "planned" and "status" in v:
        raise ActionError(gettext("“%(name)s” is planned: only a person starts it, with Start on its page. Leave status out.", name=exp.name))  # noqa: E501
    if experiment_plan.fixed(exp) and not v.get("reason") and (
            ("readout_day" in v and v["readout_day"] != exp.readout_day)
            or ("planned_test" in v and v["planned_test"] != (exp.planned_test or ""))):
        raise ActionError(gettext("“%(name)s” was started with a plan: a new readout day or test is a change from it. Say why in reason.", name=exp.name))  # noqa: E501
    return [Call("POST", f"/experiments/{exp.id}/update", form=[(k, str(x)) for k, x in v.items()])]


# The kinds of animal reference a design may name in each database, and the
# value the page's Add takes for one ("one") or for all in a housing ("group").
_DESIGN_ANIMALS = {"colony": {"mouse": lambda r: str(r.id)},
                   "zebrafish": {"fish": lambda r: f"fish:{r.id}", "clutch": lambda r: f"clutch:{r.id}"},
                   "stocks": {"stock_unit": lambda r: str(r.id)},
                   "organisms": {"org_animal": lambda r: f"organism:{r.id}", "org_cohort": lambda r: f"cohort:{r.id}"}}
_DESIGN_HOUSING = {"colony": ("cage", lambda r: r.cage_id), "zebrafish": ("tank", lambda r: str(r.id)),
                   "stocks": ("stock_rack", lambda r: str(r.id)), "organisms": ("org_housing", lambda r: str(r.id))}
_STEP_KEYS = ("days", "kind", "agent", "dose", "route", "concentration", "frequency", "intensity", "pulse_width",
              "duration", "group", "notes")


def _design_database(s, v) -> str:
    from . import experiments as xp
    key = v.get("database") or "colony"
    if xp.place_for(s, key) is None:
        raise ActionError(gettext("There is no database “%(key)s” to plan an experiment in.", key=key))
    return key


def _design_groups(s, db: str, groups) -> list[dict]:
    kind = db.partition(":")[0]
    animals, (housing_kind, housing_value) = _DESIGN_ANIMALS[kind], _DESIGN_HOUSING[kind]
    out = []
    for group in groups or []:
        if not isinstance(group, dict):
            raise ActionError(gettext("Each group is an object with its name, animals and housing."))
        adds = []
        for ref in group.get("animals") or []:
            ref_kind = ref.get("kind") if isinstance(ref, dict) else next(iter(animals))
            if ref_kind not in animals:
                raise ActionError(gettext("%(ref)s isn't an animal this database's experiments take (%(kinds)s).",
                                          ref=_show(ref), kinds=", ".join(animals)))
            adds.append({"how": "one", "value": animals[ref_kind](find(s, ref_kind, ref))})
        housing = group.get("housing") or []
        for ref in housing if isinstance(housing, list) else [housing]:
            adds.append({"how": "group", "value": housing_value(find(s, housing_kind, ref))})
        out.append({"name": str(group.get("name") or "").strip()[:80], "add": adds})
    return out


def _design_steps(steps) -> list[dict]:
    from .experiment_steps import parse_days
    out = []
    for st in steps or []:
        if not isinstance(st, dict):
            raise ActionError(gettext("Each step is an object with its days, kind, agent, dose, route and group."))
        try:
            parse_days(str(st.get("days") or ""))
        except ValueError as error:
            raise ActionError(str(error)) from None
        out.append({k: str(st.get(k) or "") for k in _STEP_KEYS})
    return out


@action(name="experiment_new", area="experiments", title="Design a new experiment",
        help="A planned experiment, designed from a goal: its treatment groups and their animals, the regimen, the "
             "main readout and its day, and the statistical test planned. Nothing of it is due, and it doesn't "
             "start, until a person presses Start on its page (no change can start it); Start keeps a copy of the "
             "design, and later changes to it ask for a reason.", target=None,
        fields=(Field("name", "text", "Its name", required=True),
                Field("database", "text", "Whose animals: colony (mice; if left out), zebrafish, stocks:<key> or "
                      "organisms:<key> (see vocabulary)"),
                Field("description", "text", "The goal and the design, in words"),
                Field("groups", "groups", "The treatment groups: [{\"name\": \"HDM\", \"animals\": [references, or "
                      "mouse numbers in the colony], \"housing\": [a cage, tank, stock rack or housing unit: every "
                      "living animal in it]}]. randomize's groups are already in this shape"),
                Field("seed", "int", "The seed randomize allocated the groups with: kept in the description, so the "
                      "allocation can be repeated"),
                Field("steps", "steps", "The regimen: [{\"days\": \"1-5\", \"kind\": \"injection\", \"agent\": "
                      "\"Tamoxifen\", \"dose\": \"75 mg/kg\", \"route\": \"i.p.\", \"concentration\": \"20 mg/mL\", "
                      "\"group\": \"HDM\" (every animal if left out), \"notes\"}]; a readout day is kind \"reading\""),
                Field("readout", "text", "The main readout: body_weight, tumour_volume, score, survival, length, "
                      "phenotype, eclosion, climbing, brood or paralysis; one BioManager works out from what is "
                      "measured, derived:<formula> (GET /api/v1/derive lists them: derived:tumour_lw, "
                      "derived:fold_change…); or your own one's name"),
                Field("readout_control", "text", "For derived:delta_delta_ct or derived:fold_change: the control "
                      "group, whose mean ΔCt each day the others are against"),
                Field("readout_unit", "text", "Your own readout's unit"),
                Field("readout_kind", "text", "Your own readout: a number for each, or how many of those at the start",
                      choices=("value", "fraction")),
                Field("readout_day", "int", "The day of the main readout (day 1 = the start date)"),
                Field("planned_test", "text", "The statistical test planned: welch (Welch's t-test, two groups), "
                      "anova (one-way ANOVA, more groups), chi2 (χ² test, survival or another count)",
                      choices=("welch", "anova", "chi2")),
                Field("start_date", "date", "The day planned as day 1 (Start makes it today if left out)"),
                Field("notes", "text", "Notes: endpoints, vehicle, what to watch for")),
        describe=lambda s, _t, v: gettext("New planned experiment “%(name)s”: %(groups)s; %(lines)s regimen lines. It starts when you press Start.", name=v["name"],  # noqa: E501
                                          groups=", ".join(str(g.get("name") or "-") for g in v.get("groups") or []
                                                           if isinstance(g, dict)) or "-",
                                          lines=len(v.get("steps") or [])))
def _experiment_new(s, _target, v):
    from flask import g
    from . import experiments as xp
    db = _design_database(s, v)
    if not isinstance(v.get("groups") or [], list) or not isinstance(v.get("steps") or [], list):
        raise ActionError(gettext("groups and steps are lists."))
    readout = v.get("readout") or ""
    if readout.startswith(xp.DERIVED):
        readout = {"key": readout, "control": v.get("readout_control", "")}
    elif readout and readout not in xp.READOUTS:
        readout = {"label": readout, "unit": v.get("readout_unit", ""), "kind": v.get("readout_kind") or "value"}
    description = v.get("description", "")
    if "seed" in v:
        # The experiment has no place of its own for it: its design's words.
        description = "\n\n".join(filter(None, [description, gettext("Groups allocated at random by BioManager with seed %(seed)s: the same animals, groups and seed give the same allocation.", seed=v["seed"])]))  # noqa: E501
    body = {"name": v["name"], "description": description, "notes": v.get("notes", ""),
            "via": g.get("proposal_source") or "", "readout": readout, "readout_day": v.get("readout_day"),
            "planned_test": v.get("planned_test", ""), "start_date": v.get("start_date", ""),
            "groups": _design_groups(s, db, v.get("groups")), "steps": _design_steps(v.get("steps"))}
    return [Call("POST", f"/experiments/in/{db}/design", json=body)]


# ---------------------------------------------------------------- notebook

def _pages_titled(s, title: str) -> list:
    """Pages this person may read, by title (any case) or id: another's
    page of the same title neither shows nor makes it ambiguous."""
    from .lab_notebook import role_for
    from .models import NotebookPage
    rows = list(s.scalars(select(NotebookPage).where(func.lower(NotebookPage.title) == title.lower())))
    rows = [r for r in rows if role_for(s, r) is not None]
    return rows or ([s.get(NotebookPage, int(title))] if title.isdigit() else [])


def _notebook_kind():
    from .models import NotebookPage
    return (NotebookPage, None, _pages_titled)


KINDS["notebook_page"] = _notebook_kind()


@action(name="notebook_note", area="notebook", title="Add to the notebook's log",
        help="A timestamped line in the Log of today's daily log (made if it doesn't exist yet), or of another "
             "notebook page you may edit.", target=None,
        fields=(Field("text", "text", "What to write", required=True),
                Field("time", "text", "The time it happened, like 14:30 (now if left out)"),
                Field("page", "notebook_page", "Another page (today's daily log if left out)")),
        describe=lambda s, _t, v: gettext("Notebook: “%(text)s” in the log of %(page)s", text=v["text"],
                                          page=f"“{find(s, 'notebook_page', v['page']).title}”"
                                          if v.get("page") not in (None, "") else gettext("today's daily log")))
def _notebook_note(s, _target, v):
    from flask import g
    form = [("text", v["text"]), ("time", v.get("time", "")), ("via", g.get("proposal_source") or "")]
    if v.get("page") not in (None, ""):
        form.append(("page_id", str(find(s, "notebook_page", v["page"]).id)))
    return [Call("POST", "/notebook/api/notes", form=form)]


def _drafted_line() -> str:
    """The line that says who wrote a page or section an assistant drafted."""
    from flask import g
    source = g.get("proposal_source") or ""
    said = gettext("Drafted by %(source)s", source=source) if source else gettext("Drafted by an assistant")
    return f"_{said}_"


def _readable_page(s, page):
    """The page, if this person may open it (its title is not told otherwise)."""
    from . import lab_notebook
    if lab_notebook.role_for(s, page) is None:
        raise ActionError(gettext("There is no %(kind)s %(ref)s.", kind="notebook page", ref=f"#{page.id}"))
    return page


def _templates_titled(s, title: str) -> list:
    """Notebook templates this person may start a page from (their own, and
    the lab's), by title."""
    from . import access
    from .models import NotebookTemplate
    me = access.username()
    usable = (NotebookTemplate.owner_username == me) | NotebookTemplate.lab.is_(True)
    rows = list(s.scalars(select(NotebookTemplate).where(usable, func.lower(NotebookTemplate.title) == title.lower())))
    return rows or ([s.scalar(select(NotebookTemplate).where(usable, NotebookTemplate.id == int(title)))]
                    if title.isdigit() else [])


def _template_kind():
    from .models import NotebookTemplate
    return (NotebookTemplate, None, _templates_titled)


KINDS["notebook_template"] = _template_kind()


def _starters() -> tuple[str, ...]:
    from .lab_notebook import STARTERS
    return tuple(k for k in STARTERS if k not in ("blank", "daily"))


PAGE_KINDS = ("note", "experiment", "protocol", "reading", "meeting", "seminar")


@action(name="notebook_page_new", area="notebook", title="New notebook page",
        help="A new page in your notebook, with a title and Markdown text, optionally started from one of your or "
             "the lab's templates (template) or a built-in starter (starter). Its first line says an assistant "
             "drafted it; it is never signed.", target=None,
        fields=(Field("title", "text", "Its title", required=True),
                Field("body", "text", "Its text, in Markdown (after the template's, if there is one)"),
                Field("template", "notebook_template", "A saved template to start from, by its title"),
                Field("starter", "text", "Or a built-in starter to start from", choices=_starters()),
                Field("kind", "text", "What kind of page it is (a note if left out)", choices=PAGE_KINDS),
                Field("tags", "list", "Tags"),
                Field("folder", "protocol_folder", "For a protocol: the folder of the protocol library to file it "
                                                   "in, by its name (one made earlier in the same proposal too)")),
        describe=lambda s, _t, v: gettext("Notebook: new page “%(title)s”%(start)s", title=v["title"],
                                          start=_page_from(s, v)))
def _notebook_page_new(s, _target, v):
    import io
    from . import access, lab_notebook
    if v.get("template") not in (None, "") and v.get("starter"):
        raise ActionError(gettext("Start from a template or a starter, not both."))
    parts, kind = [_drafted_line()], v.get("kind", "")
    if v.get("template") not in (None, ""):
        template = find(s, "notebook_template", v["template"])
        if template.owner_username != access.username() and not template.lab:
            raise ActionError(gettext("Template not found."))
        parts.append((template.body or "").strip())
        kind = kind or (template.kind if template.kind in PAGE_KINDS else "")
    elif v.get("starter"):
        starter = lab_notebook.STARTERS[v["starter"]]
        parts.append(lab_notebook._fill(starter["body"], date=date.today().isoformat(), presenter="",
                                        now=lab_notebook._clock()).strip())
        kind = kind or starter["kind"]
    if v.get("body"):
        parts.append(v["body"].strip())
    front = ["---", "title: " + " ".join(v["title"].split())]
    if kind:
        front.append(f"kind: {kind}")
    tags = [t.replace(",", " ").strip() for t in v.get("tags", []) if t.strip()]
    if tags:
        front.append("tags: [" + ", ".join(tags) + "]")
    text = "\n".join(front) + "\n---\n\n" + "\n\n".join(p for p in parts if p) + "\n"
    form = [("file", (io.BytesIO(text.encode("utf-8")), "page.md"))]
    folder = _protocol_folder(s, v.get("folder"))
    if folder is not None:
        if kind != "protocol":
            raise ActionError(gettext("Only a protocol is filed in a folder: give kind protocol too."))
        form.append(("folder_id", str(folder.id)))
    # The notebook's Import (a Markdown file), the one page that takes a
    # page's whole text at once.
    return [Call("POST", "/notebook/api/import", form=form)]


def _page_from(s, v) -> str:
    said = ""
    if v.get("template") not in (None, ""):
        said = gettext(", from the template “%(name)s”", name=find(s, "notebook_template", v["template"]).title)
    elif v.get("starter"):
        said = gettext(", from the starter “%(name)s”", name=v["starter"])
    folder = _protocol_folder(s, v.get("folder"))
    if folder is not None:
        said += gettext(", filed in “%(folder)s”", folder=folder.name)
    return said


def _not_open(s, page) -> None:
    """Refused while someone has the page open in the editor: its text is
    saved over by the open editor, so what is written here could be lost."""
    from datetime import datetime
    from .lab_notebook import PRESENCE_TTL
    from .models import NotebookPresence
    there = s.scalar(select(NotebookPresence.username).where(
        NotebookPresence.page_id_fk == page.id,
        NotebookPresence.updated_at >= datetime.utcnow() - PRESENCE_TTL).limit(1))
    if there:
        raise ActionError(gettext("“%(page)s” is open in the editor (%(who)s): add it there, or propose it again once the page is closed.", page=page.title, who=there))


@action(name="notebook_page_append", area="notebook", title="Add a section to a notebook page",
        help="Add Markdown text at the end of a notebook page you may edit, under a heading if one is given, "
             "with a line saying an assistant drafted it. Refused while someone has the page open in the editor "
             "(for a line in its Log, use notebook_note).", target="notebook_page",
        fields=(Field("text", "text", "What to add, in Markdown", required=True),
                Field("heading", "text", "A heading for it")),
        describe=lambda s, p, v: gettext("Notebook: a section%(heading)s added to “%(page)s”",
                                         heading=f" “{v['heading']}”" if v.get("heading") else "",
                                         page=_readable_page(s, p).title))
def _notebook_page_append(s, page, v):
    _readable_page(s, page)
    _not_open(s, page)
    section = [f"## {' '.join(v['heading'].split())}"] if v.get("heading") else []
    section += [_drafted_line(), v["text"].strip()]
    body = (page.body or "").rstrip("\n")
    text = (f"{body}\n\n" if body else "") + "\n\n".join(section) + "\n"
    # The page's own save, as its plain-text editor sends it: the live
    # editors start again from the new text.
    return [Call("POST", f"/notebook/pages/{page.id}/update", form=[("body", text)])]


def _editable_page(s, page):
    """The page, if this person may change it now, as its editor would let
    them: they may edit it, it is not signed and locked, and no one has it
    open in the editor."""
    from . import lab_notebook, signatures
    _readable_page(s, page)
    if not lab_notebook.can_edit_role(lab_notebook.role_for(s, page)):
        raise ActionError(gettext("“%(page)s” is shared with you to read, not to edit.", page=page.title))
    if signatures.is_locked(s, page.id):
        raise ActionError(gettext("“%(page)s” is signed and locked. Its owner can amend it to change it.", page=page.title))
    _not_open(s, page)
    return page


DRAFTED = re.compile(r"^_Drafted by [^\n]*_$", re.M)


def _edited_text(page, v) -> str:
    """The page's text once the change's body or edits are made."""
    old = (page.body or "").replace("\r\n", "\n")
    if "body" in v:
        new = v["body"].replace("\r\n", "\n").strip() + "\n"
        drafted = DRAFTED.match(old.lstrip("\n"))
        if drafted and not DRAFTED.search(new):
            # Drafted by an assistant: the line saying so stays at its top.
            new = drafted.group(0) + "\n\n" + new
        return new
    new = old
    for edit in v.get("edits", []):
        times = new.count(edit["find"])
        if times != 1:
            shown = " ".join(edit["find"].split())
            shown = shown if len(shown) <= 80 else shown[:79] + "…"
            raise ActionError(gettext("“%(text)s” is not on “%(page)s”; get the page and copy the text as it is there.", text=shown, page=page.title)
                              if not times else
                              gettext("“%(text)s” is on “%(page)s” %(times)s times; make find longer, so it is found once.", text=shown, page=page.title, times=times))  # noqa: E501
        new = new.replace(edit["find"], edit["replace"], 1)
    return new


def _page_now_and_after(s, page, v) -> tuple[dict, dict]:
    """The page's title, kind, tags and text now and after the change."""
    from . import lab_notebook
    info = lab_notebook.info_for(s, page.id)
    now = {"title": page.title or "", "kind": info.kind if info else "note",
           "tags": lab_notebook.tag_list(info.tags if info else ""), "body": (page.body or "").replace("\r\n", "\n")}
    after = dict(now)
    if "body" in v and "edits" in v:
        raise ActionError(gettext("Give body (the whole new text) or edits (parts of it), not both."))
    if "title" in v:
        after["title"] = " ".join(v["title"].split())
        if not after["title"]:
            raise ActionError(gettext("A page needs a title."))
    if "kind" in v:
        after["kind"] = v["kind"]
    if "tags" in v:
        after["tags"] = lab_notebook.tag_list(lab_notebook.normalize_tags(v["tags"]))
    if "body" in v or "edits" in v:
        after["body"] = _edited_text(page, v)
    if after == now:
        raise ActionError(gettext("“%(page)s” already says this: nothing would change.", page=page.title))
    return now, after


def _lines(text: str) -> list[str]:
    return text.rstrip("\n").split("\n") if text.strip() else []


def _text_counts(old: str, new: str) -> tuple[int, int]:
    """(lines put in, lines taken out) from old to new."""
    import difflib
    added = removed = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, _lines(old), _lines(new), autojunk=False).get_opcodes():
        if op != "equal":
            removed, added = removed + i2 - i1, added + j2 - j1
    return added, removed


def _shown_lines(lines: list[str], width: int = 300) -> str:
    text = " ↵ ".join(lines)
    return text if len(text) <= width else text[:width - 1] + "…"


MAX_HUNKS = 30


def _page_change_shows(s, page, v) -> list[dict]:
    """The change as before → after: title, kind and tags, and each part of
    the text that changes, by the line it starts at."""
    import difflib
    now, after = _page_now_and_after(s, page, v)
    fields: dict[str, list] = {}
    for key, name in (("title", gettext("Title")), ("kind", gettext("Kind"))):
        if now[key] != after[key]:
            fields[name] = [translate_value(now[key]), translate_value(after[key])]
    if now["tags"] != after["tags"]:
        fields[gettext("Tags")] = [", ".join(now["tags"]), ", ".join(after["tags"])]
    old, new = _lines(now["body"]), _lines(after["body"])
    hunks = [h for h in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes() if h[0] != "equal"]
    for op, i1, i2, j1, j2 in hunks[:MAX_HUNKS]:
        fields[gettext("Text, line %(line)s", line=(i1 if op == "insert" else i1 + 1) or 1)] = [
            _shown_lines(old[i1:i2]), _shown_lines(new[j1:j2])]
    if len(hunks) > MAX_HUNKS:
        fields[gettext("Text, further on")] = ["", ngettext("%(num)s more change", "%(num)s more changes",
                                                           len(hunks) - MAX_HUNKS)]
    return [{"table": "notebook_pages", "id": page.id, "label": page.title, "action": "update", "details": "",
             "changes": {"changes": fields}}]


def _page_change_said(s, page, v) -> str:
    now, after = _page_now_and_after(s, _readable_page(s, page), v)
    parts = []
    if now["title"] != after["title"]:
        parts.append(gettext("title → “%(title)s”", title=after["title"]))
    if now["kind"] != after["kind"]:
        parts.append(gettext("kind %(before)s → %(after)s", before=translate_value(now["kind"]),
                             after=translate_value(after["kind"])))
    if now["tags"] != after["tags"]:
        parts.append(gettext("tags %(before)s → %(after)s", before=", ".join(now["tags"]) or "-",
                             after=", ".join(after["tags"]) or "-"))
    if now["body"] != after["body"]:
        added, removed = _text_counts(now["body"], after["body"])
        parts.append(gettext("text: %(added)s lines in, %(removed)s out", added=added, removed=removed)
                     if "edits" in v or not now["body"].strip() else
                     gettext("text rewritten: %(added)s lines in, %(removed)s out", added=added, removed=removed))
    return gettext("Notebook: “%(page)s”: %(changes)s", page=now["title"], changes="; ".join(parts))


@action(name="notebook_page_change", area="notebook", title="Change a notebook page",
        help="Change a notebook page you may edit: its title, kind, tags, and its text, either all of it (body: "
             "the whole new Markdown, as get reads it at notebook/pages/<id>) or parts of it (edits: each find, "
             "exactly as on the page and there once, becomes its replace). What is left out stays as it is. The "
             "text as it was is kept in the page's History, to restore. Refused while someone has the page open in "
             "the editor, and for a signed page. Use it to fix a page, rather than making a new one.",
        target="notebook_page",
        fields=(Field("title", "text", "Its new title"),
                Field("body", "text", "Its whole new text, in Markdown (replaces it; a line saying an assistant "
                                      "drafted the page stays at its top)"),
                Field("edits", "edits", "Or parts of the text to change, each {find, replace}, in order"),
                Field("kind", "text", "What kind of page it is", choices=PAGE_KINDS),
                Field("tags", "list", "Its tags (replaces them all)")),
        describe=_page_change_said, shows=_page_change_shows)
def _notebook_page_change(s, page, v):
    from flask import g
    _editable_page(s, page)
    now, after = _page_now_and_after(s, page, v)
    calls = []
    if now["title"] != after["title"] or now["body"] != after["body"]:
        source = g.get("proposal_source") or ""
        label = (gettext("Before the change by %(source)s", source=source) if source
                 else gettext("Before an assistant's change"))
        # Kept as it is now, as a version of its own: History restores it.
        calls.append(Call("POST", f"/notebook/api/pages/{page.id}/versions", json={"label": label}))
    meta = {k: after[k] for k in ("kind", "tags") if now[k] != after[k]}
    if meta:
        calls.append(Call("POST", f"/notebook/api/pages/{page.id}/meta", json=meta))
    form = [(k, after[k]) for k in ("title", "body") if now[k] != after[k]]
    if form:
        # The page's own save, as its plain-text editor sends it: the live
        # editors start again from the new text.
        calls.append(Call("POST", f"/notebook/pages/{page.id}/update", form=form))
    return calls


# ---------------------------------------------------------------- protocol library

def _folders_named(s, name: str) -> list:
    """Folders of the protocol library, by name (any case) or id."""
    from .models import NotebookFolder
    rows = list(s.scalars(select(NotebookFolder).where(NotebookFolder.kind == "protocol",
                                                       func.lower(NotebookFolder.name) == name.lower())))
    if rows or not name.isdigit():
        return rows
    folder = s.get(NotebookFolder, int(name))
    return [folder] if folder is not None and folder.kind == "protocol" else []


def _folder_kind():
    from .models import NotebookFolder
    return (NotebookFolder, None, _folders_named)


KINDS["protocol_folder"] = _folder_kind()


def _protocol_folder(s, ref):
    """The protocol library's folder `ref` names, or None for none ("")."""
    if ref in ("", None) or (isinstance(ref, str) and not ref.strip()):
        return None
    folder = find(s, "protocol_folder", ref)
    if isinstance(ref, dict) and folder.kind != "protocol":
        raise ActionError(gettext("There is no %(kind)s %(ref)s.", kind="protocol folder", ref=_show(ref)))
    return folder


def _icons() -> tuple[str, ...]:
    from .lab_notebook import FOLDER_ICONS
    return FOLDER_ICONS


@action(name="protocol_folder_new", area="notebook", title="New folder of protocols",
        help="A folder in the lab's protocol library (Notebook → Protocols), shared with the lab. Its name must not "
             "be a folder's already. File protocols in it with protocol_file, or with folder on notebook_page_new.",
        target=None,
        fields=(Field("name", "text", "Its name", required=True),
                Field("icon", "text", "Its icon (a plain folder if left out)", choices=_icons())),
        describe=lambda _s, _t, v: gettext("Protocols: new folder “%(name)s”", name=" ".join(v["name"].split())))
def _protocol_folder_new(s, _target, v):
    from .models import NotebookFolder
    name = " ".join(v["name"].split())
    if s.scalar(select(NotebookFolder.id).where(NotebookFolder.kind == "protocol",
                                                func.lower(NotebookFolder.name) == name.lower())):
        raise ActionError(gettext("There is a folder of protocols called “%(name)s” already: file them in it.", name=name))
    return [Call("POST", "/notebook/api/folders", json={"kind": "protocol", "name": name,
                                                        "icon": v.get("icon", "folder")})]


def _file_said(s, page, v) -> str:
    folder = _protocol_folder(s, v["folder"])
    page = _readable_page(s, page)
    if folder is None:
        return gettext("Protocols: “%(page)s” taken out of its folder", page=page.title)
    return gettext("Protocols: “%(page)s” filed in “%(folder)s”", page=page.title, folder=folder.name)


@action(name="protocol_file", area="notebook", title="File a protocol in a folder",
        help="File a protocol (a notebook page of kind protocol, which you may edit) in a folder of the protocol "
             "library, or take it out of its folder (folder: \"\").", target="notebook_page",
        fields=(Field("folder", "protocol_folder", "The folder, by its name or reference; \"\" for none",
                      required=True),),
        describe=_file_said)
def _protocol_file(s, page, v):
    from . import lab_notebook
    folder = _protocol_folder(s, v["folder"])
    _readable_page(s, page)
    if not lab_notebook.can_edit_role(lab_notebook.role_for(s, page)):
        raise ActionError(gettext("“%(page)s” is shared with you to read, not to edit.", page=page.title))
    info = lab_notebook.info_for(s, page.id)
    if info is None or info.kind != "protocol":
        raise ActionError(gettext("“%(page)s” is not a protocol: make it one first (notebook_page_change, kind protocol).", page=page.title))
    return [Call("POST", "/notebook/api/folders/file", json={"kind": "protocol", "item_id": page.id,
                                                             "folder_id": folder.id if folder else None})]


# ---------------------------------------------------------------- inventories

def _inventory(s, key):
    """An inventory this person can open, by its key or its name."""
    from . import inventory_service as isvc
    from . import lab
    text = str(key or "").strip()
    module = isvc.get_module(s, text) if text else None
    if module is None and text:
        named = [m for m in isvc.list_modules(s) if (m.label or "").lower() == text.lower()]
        module = named[0] if len(named) == 1 else None
    if module is None or not lab.can_see(module):
        raise ActionError(gettext("There is no inventory “%(key)s”.", key=key))
    return module


def _inventory_items(s, code: str) -> list:
    """Items written as their inventory and number (reagents:12, Reagents #12),
    a number alone (#12, in whichever inventory has it), or their name."""
    from . import inventory_service as isvc
    from .models import InventoryItem
    visible = [m.id for m in isvc.list_modules(s)] or [0]
    match = re.fullmatch(r"(.*?)[\s:]*#?(\d+)", code)
    if match:
        prefix, number = match.group(1).strip(), int(match.group(2))
        if not prefix:
            return list(s.scalars(select(InventoryItem).where(InventoryItem.module_id_fk.in_(visible),
                                                              InventoryItem.number == number)))
        try:
            module = _inventory(s, prefix)
        except ActionError:
            module = None
        if module is not None:
            return [s.scalar(select(InventoryItem).where(InventoryItem.module_id_fk == module.id,
                                                         InventoryItem.number == number))]
    return list(s.scalars(select(InventoryItem).where(InventoryItem.module_id_fk.in_(visible),
                                                      func.lower(InventoryItem.name) == code.lower())))


def _inventory_kinds():
    from .models import Equipment, InventoryItem, PlasmidRecord
    from .models import InventoryModule
    return {"inventory_item": (InventoryItem, None, _inventory_items),
            "inventory": (InventoryModule, None, _inventories_named),
            "plasmid": (PlasmidRecord, None, _plasmids_named),
            "equipment": (Equipment, "name", "name")}


def _inventories_named(s, code: str) -> list:
    """An inventory this person can open, by its key or its name."""
    try:
        return [_inventory(s, code)]
    except ActionError:
        return []


def _plasmids_named(s, code: str) -> list:
    """A plasmid by its number (#42, 42) or its name."""
    from .models import PlasmidRecord
    number = code.lstrip("#").strip()
    if number.isdigit():
        return [s.scalar(select(PlasmidRecord).where(PlasmidRecord.plasmid_id == int(number)))]
    return list(s.scalars(select(PlasmidRecord).where(func.lower(PlasmidRecord.name) == code.lower())))


KINDS.update(_inventory_kinds())


def _item_label(s, item) -> str:
    from .i18n import translate_value
    from .models import InventoryModule
    module = s.get(InventoryModule, item.module_id_fk)
    return f"{translate_value(module.label)} #{item.number}" + (f" · {item.name}" if item.name else "")


def _value_said(value) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_value_said(x)}" for k, x in value.items()) or "-"
    if isinstance(value, bool):
        return gettext("yes") if value else gettext("no")
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, list):
        return "; ".join(str(x) for x in value) or "-"
    return str(value) if value not in (None, "") else "-"


def _fields_said(v, skip=()) -> str:
    return ", ".join(f"{k} → {_value_said(x)}" for k, x in v.items() if k not in skip)


ITEM_FIELDS = (Field("name", "text", "Its name (the Sample ID, Item, Target… as the inventory calls it)"),
               Field("category", "text", "Its category or type, from the inventory's list (see inventories)"),
               Field("status", "text", "One of the inventory's statuses (see inventories)"),
               Field("owner", "text", "Username of its owner"),
               Field("shared", "bool", "true: lab common, anyone may use and edit it; false: personal"),
               Field("quantity", "text", "How much there is; a number (12, 2.5) where the inventory follows Low at"),
               Field("unit", "text", "The quantity's unit"),
               Field("low_at", "number", "Low at: at or below this quantity it counts as low"),
               Field("pack_size", "text", "What one of its unit holds, a number and a unit (\"10 mL\" for an item kept "
                     "in ampoules), so an amount used in mL or mg comes off as part of one"),
               Field("vendor", "text", "Who sells it"),
               Field("catalog_number", "text", "Its catalogue number"),
               Field("lot", "text", "Its lot"),
               Field("box", "text", "The box it is in, by name"),
               Field("position", "text", "Its position in the box (A1, 12…)"),
               Field("location_note", "text", "Where it is kept, in words, when not in a box"),
               Field("received_on", "date", "When it arrived"),
               Field("expires_on", "date", "When it expires"),
               Field("notes", "text", "Its notes (replaces them)"),
               Field("fields", "object", "The inventory's own columns, by key: {\"host\": \"rabbit\"} (see inventories)"))


def _item_body(v, skip=("database",)) -> dict:
    """The API's item, from the fields given."""
    body = {k: x for k, x in v.items() if k not in skip}
    if "low_at" in body:
        body["low_at"] = f"{body['low_at']:g}"
    return body


@action(name="inventory_item_new", area="inventory", title="New inventory item",
        help="Add an item to an inventory (samples, orders, reagents, chemicals, antibodies, viruses, primers, "
             "glycerol stocks, cell lines or the lab's own lists; their keys, statuses and columns are in "
             "inventories). What the inventory needs for a new item must be given.", target=None,
        fields=(Field("database", "text", "The inventory, by its key or name", required=True), *ITEM_FIELDS),
        describe=lambda s, _t, v: gettext("New in %(database)s: %(fields)s", database=_inventory_said(s, v["database"]),
                                          fields=_fields_said(v, skip=("database",)) or "-"))
def _inventory_item_new(s, _target, v):
    module = _inventory(s, v["database"])
    return [Call("POST", f"/api/v1/inventories/{module.key}/items", json=_item_body(v))]


def _inventory_said(s, key) -> str:
    from .i18n import translate_value
    return translate_value(_inventory(s, key).label)


def _item_module(s, item):
    from .models import InventoryModule
    return s.get(InventoryModule, item.module_id_fk)


@action(name="inventory_item_change", area="inventory", title="Change an inventory item",
        help="Set any of these on one item; what is left out stays as it is. To take some of its quantity off, "
             "use inventory_quantity_used; to receive an order, order_received.", target="inventory_item",
        fields=ITEM_FIELDS,
        describe=lambda s, i, v: gettext("%(item)s: %(fields)s", item=_item_label(s, i), fields=_fields_said(v)))
def _inventory_item_change(s, item, v):
    if not v:
        raise ActionError(gettext("Say what to change on %(item)s.", item=_item_label(s, item)))
    return [Call("PATCH", f"/api/v1/inventories/{_item_module(s, item).key}/items/{item.number}",
                 json=_item_body(v))]


def _renumber_plan(s, module, v):
    from . import inventory_renumber as rn
    from .lab import can_see
    if not can_see(module):
        raise ActionError(gettext("There is no inventory “%(key)s”.", key=module.key))
    plan = rn.plan(s, module, v.get("by", rn.NAME_NUMBER), v.get("order", "ascending"))
    if plan.problems:
        raise ActionError(" ".join(plan.problems))
    return plan


def _renumber_said(s, module, v) -> str:
    from . import inventory_renumber as rn
    plan = _renumber_plan(s, module, v)
    by = v.get("by", rn.NAME_NUMBER)
    how = (gettext("to the number in each name") if by == rn.NAME_NUMBER else
           gettext("by %(column)s, %(order)s", column=translate_value(dict(rn.columns(module)).get(by, by)),
                   order=translate_value(plan.order)))
    first = "; ".join(f"#{old} → #{new}" + (f" {item.name}" if item.name else "")
                      for item, old, new in plan.moves[:5])
    return gettext("%(database)s renumbered %(how)s: %(changed)s of %(count)s get a new # (%(first)s%(more)s)",
                   database=translate_value(module.label), how=how, changed=len(plan.moves), count=plan.count,
                   first=first, more="; …" if len(plan.moves) > 5 else "")


def _renumber_columns_help() -> str:
    from . import inventory_renumber as rn
    return ", ".join(rn.COLUMNS)


@action(name="inventory_renumber", area="inventory", title="Renumber an inventory",
        help="Give every item of an inventory a new # (1, 2, 3…): to the number in its name (by: name_number, "
             "\"B12\" becomes #12; refused, naming the items, when a name has none, two names have one number or "
             "a number is missing), or in the order of a column (by: a column key: " + _renumber_columns_help() +
             ", or one of the inventory's own; natural order, B2 before B10). Links to the items in the notebook, "
             "orders put into stock and an experiment's samples follow; the records list every change. Only an "
             "admin or whoever created the inventory may. One batch: Batch history can undo it.",
        target="inventory",
        fields=(Field("by", "text", "name_number (the default), or a column's key"),
                Field("order", "text", "ascending (the default) or descending, for a column",
                      choices=("ascending", "descending"))),
        describe=_renumber_said)
def _inventory_renumber(s, module, v):
    _renumber_plan(s, module, v)
    from . import inventory_renumber as rn
    return [Call("POST", f"/inventory/{module.key}/renumber",
                 form=[("by", v.get("by", rn.NAME_NUMBER)), ("order", v.get("order", "ascending"))])]


def _left_after(s, item, used: float) -> tuple[str, str]:
    """(what the quantity is, what it will be) once `used` is taken off."""
    from . import inventory_service as isvc
    now = isvc.amount(item.quantity)
    if now is None:
        raise ActionError(gettext("The quantity of %(item)s is “%(quantity)s”, not a number: set it with inventory_item_change.", item=_item_label(s, item), quantity=item.quantity or ""))
    if used <= 0:
        raise ActionError(gettext("%(field)s must be more than 0.", field="amount"))
    if used > now:
        raise ActionError(gettext("Only %(left)s %(unit)s of %(item)s is left.", left=isvc.number_text(now),
                                  unit=isvc.singular(item.unit) if now == 1 else item.unit or "",
                                  item=_item_label(s, item)))
    return isvc.number_text(now), isvc.number_text(round(now - used, 6))


# Units an amount used may be given in, as powers of ten of the family's
# base (g, L). Any other unit (vials, tubes, µM…) is only itself.
UNITS = {"kg": ("mass", 3), "g": ("mass", 0), "mg": ("mass", -3), "µg": ("mass", -6), "ug": ("mass", -6),
         "mcg": ("mass", -6), "ng": ("mass", -9),
         "l": ("volume", 0), "ml": ("volume", -3), "µl": ("volume", -6), "ul": ("volume", -6), "nl": ("volume", -9)}
AMOUNT = re.compile(r"\s*(\d[\d.,]*|[.,]\d+)\s*(\D*?)\s*")


def _unit_key(unit: str) -> str:
    return " ".join(str(unit or "").split()).replace("μ", "µ").lower()


def _used(s, item, v) -> tuple[float, str, str]:
    """(the amount in the item's unit, the amount as given, and what it is
    in the item's unit when converted, else ""), from an amount (12 or
    "40 mg") and an optional unit."""
    from . import inventory_service as isvc
    raw, unit = v["amount"], v.get("unit", "")
    if isinstance(raw, str):
        match = AMOUNT.fullmatch(raw)
        number = isvc.amount(match.group(1)) if match else None
        if number is None:
            raise ActionError(gettext("amount is a number, with its unit if it isn't the item's (40 mg), not “%(value)s”.", value=raw))  # noqa: E501
        raw, unit = number, match.group(2) or unit
    amount = float(raw)
    have, want = _unit_key(item.unit), _unit_key(unit)
    if not want or want == have or isvc.singular(want) == isvc.singular(have):    # 1 ampoule of ampoules
        return amount, isvc.amount_text(amount, item.unit), ""
    given = isvc.amount_text(amount, unit)
    if _same_family(want, have):
        converted = round(amount * _factor(want, have), 9)
        return converted, given, isvc.amount_text(converted, item.unit)
    # Kept in packs (ampoules, vials, bottles) of a known size: 2 mL used of
    # 10 mL ampoules is 0.2 of one.
    pack = isvc.pack_size(item.pack_size)
    if pack is not None:
        size, pack_unit = pack[0], _unit_key(pack[1])
        if want == pack_unit or _same_family(want, pack_unit):
            factor = 1.0 if want == pack_unit else _factor(want, pack_unit)
            converted = round(amount * factor / size, 9)
            return converted, given, isvc.amount_text(converted, item.unit)
        raise ActionError(gettext("%(item)s comes in packs of %(pack)s, and %(amount)s can't be turned into that.",
                                  item=_item_label(s, item), pack=item.pack_size, amount=given))
    if not have:
        raise ActionError(gettext("%(item)s has no unit, so %(amount)s can't be taken off it: give the amount alone, as its quantity is kept.", item=_item_label(s, item), amount=given))  # noqa: E501
    if have not in UNITS:
        raise ActionError(gettext("%(item)s is kept in %(unit)s, and %(amount)s can't be turned into that: give it a pack size (what one of its %(unit)s holds, 10 mL) or the amount in %(unit)s.", item=_item_label(s, item), unit=item.unit, amount=given))  # noqa: E501
    raise ActionError(gettext("%(item)s is kept in %(unit)s, and %(amount)s can't be turned into that.",
                              item=_item_label(s, item), unit=item.unit, amount=given))


def _same_family(a: str, b: str) -> bool:
    """Both units of mass, or both of volume (UNITS)."""
    return a in UNITS and b in UNITS and UNITS[a][0] == UNITS[b][0]


def _factor(have: str, want: str) -> float:
    """What one `have` is in `want` (both of a family): mL → L is 0.001."""
    return 10.0 ** (UNITS[have][1] - UNITS[want][1])


def _used_said(s, item, v) -> str:
    used, given, converted = _used(s, item, v)
    before, after = _left_after(s, item, used)
    if converted:
        return gettext("%(item)s: %(amount)s used, which is %(converted)s (%(before)s → %(after)s)",
                       item=_item_label(s, item), amount=given, converted=converted, before=before, after=after)
    return gettext("%(item)s: %(amount)s used (%(before)s → %(after)s)", item=_item_label(s, item), amount=given,
                   before=before, after=after)


@action(name="inventory_quantity_used", area="inventory", title="Some of an item used",
        help="Take an amount off an item's quantity. Give it in the item's unit, or in another of mass (kg, g, mg, "
             "µg, ng) or volume (L, mL, µL, nL) and BioManager converts it; an item kept in ampoules, vials, tubes "
             "or bottles takes mL or mg once it has a pack size (what one holds: 2 mL of 10 mL ampoules is 0.2), "
             "and otherwise only its own unit. Where the inventory keeps Low at, its status follows as on the page: low at or below "
             "Low at, empty (or used up) at 0.", target="inventory_item",
        fields=(Field("amount", "amount", "How much was used: a number in the item's unit, or with its own unit "
                      "(\"40 mg\")", required=True),
                Field("unit", "text", "The amount's unit, when it isn't the item's (mg, µL…)")),
        describe=_used_said)
def _inventory_quantity_used(s, item, v):
    _now, left = _left_after(s, item, _used(s, item, v)[0])
    return [Call("PATCH", f"/api/v1/inventories/{_item_module(s, item).key}/items/{item.number}",
                 json={"quantity": left})]


def _inventory_box(s, module, name) -> str:
    from .i18n import translate_value
    from .models import InventoryRack
    rack = s.scalar(select(InventoryRack).where(InventoryRack.module_id_fk == module.id,
                                                func.lower(InventoryRack.name) == str(name).strip().lower()))
    if rack is None:
        raise ActionError(gettext("%(database)s has no box “%(box)s”.", database=translate_value(module.label),
                                  box=name))
    return str(rack.id)


STOCK_ONLY = ("name", "quantity", "unit", "shared", "expires_on", "box", "position", "location_note")


def _restock(s, order, v):
    """(the stock inventory, the stock record as it will be made) for an
    order received with to_stock."""
    from . import inventory_service as isvc
    from .inventory_routes import _stock_from_order
    target = _inventory(s, v["to_stock"])
    draft = _stock_from_order(s, isvc.view(_item_module(s, order)), isvc.view(target), order,
                              shared=v.get("shared", True))
    for key in ("name", "quantity", "unit", "lot", "expires_on"):
        if key in v:
            setattr(draft, key, date.fromisoformat(v[key]) if key == "expires_on" else v[key])
    return target, draft


def _received_said(s, order, v) -> str:
    """"Orders #4 · Tamoxifen 1 g received, added to Reagents: Tamoxifen, 1 g,
    lot L7, expires 2027-10-01": what the stock record will hold."""
    stock = ""
    if v.get("to_stock") not in (None, ""):
        target, draft = _restock(s, order, v)
        parts = [draft.name or "-"]
        if draft.quantity or draft.unit:
            parts.append(f"{draft.quantity or ''} {draft.unit or ''}".strip())
        if draft.lot:
            parts.append(gettext("lot %(lot)s", lot=draft.lot))
        if draft.expires_on:
            parts.append(gettext("expires %(date)s", date=draft.expires_on.isoformat()))
        stock = gettext(", added to %(database)s: %(what)s", database=_inventory_said(s, target.key),
                        what=", ".join(parts))
    return gettext("%(item)s received%(on)s%(stock)s", item=_item_label(s, order), on=_on(v.get("received_on")),
                   stock=stock)


def _received_warn(s, order, v) -> list[str]:
    """An item of the same name already in the stock inventory: the person
    may rather add to it than keep a second."""
    from .models import InventoryItem
    if v.get("to_stock") in (None, ""):
        return []
    target, draft = _restock(s, order, v)
    same = list(s.scalars(select(InventoryItem).where(InventoryItem.module_id_fk == target.id,
                                                      func.lower(InventoryItem.name) == (draft.name or "").lower())
                          .order_by(InventoryItem.number).limit(5)))
    if not draft.name or not same:
        return []
    found = "; ".join(f"#{i.number}" + (f", {i.quantity} {i.unit or ''}".rstrip() if i.quantity else "")
                      for i in same)
    return [gettext("%(database)s already has “%(name)s” (%(items)s): this adds another item rather than adding to it.",
                    database=_inventory_said(s, target.key), name=draft.name, items=found)]


@action(name="order_received", area="inventory", title="Order received",
        help="Mark an order received. With to_stock, also add it to a reagents, chemicals, antibodies or viruses "
             "inventory, as the order's Add to stock does: its name, vendor, catalogue number, quantity and unit "
             "come from the order unless given here (the order says \"Tamoxifen 1 g\", the stock keeps "
             "\"Tamoxifen\", quantity 1, unit g); give the lot, when it expires and where it is kept. The summary "
             "shows the stock record as it will be made, and a warning when that inventory already has an item "
             "of the same name.", target="inventory_item",
        fields=(Field("received_on", "date", "When it arrived (today if left out)"),
                Field("lot", "text", "Its lot"),
                Field("to_stock", "text", "Add it to this inventory, by its key or name"),
                Field("name", "text", "In stock: its name (the order's if left out)"),
                Field("quantity", "text", "In stock: how much there is (the order's if left out)"),
                Field("unit", "text", "In stock: the quantity's unit (the order's if left out)"),
                Field("shared", "bool", "In stock as lab common (true, the page's first choice) or yours (false)"),
                Field("expires_on", "date", "In stock: when it expires"),
                Field("box", "text", "In stock: the box it goes in"),
                Field("position", "text", "In stock: its position in the box"),
                Field("location_note", "text", "In stock: where it is kept, in words")),
        describe=_received_said, warn=_received_warn)
def _order_received(s, order, v):
    from . import inventory_service as isvc
    module = _item_module(s, order)
    if module.kind != "orders":
        raise ActionError(gettext("%(item)s is not an order.", item=_item_label(s, order)))
    received = isvc.match_status(isvc.view(module), "received")
    if received is None:
        raise ActionError(gettext("%(database)s has no status “received”.", database=module.label))
    body = {"status": received, **{k: v[k] for k in ("received_on", "lot") if k in v}}
    calls = [Call("PATCH", f"/api/v1/inventories/{module.key}/items/{order.number}", json=body)]
    if v.get("to_stock") in (None, ""):
        extra = [k for k in STOCK_ONLY if k in v]
        if extra:
            raise ActionError(gettext("%(fields)s only go with to_stock.", fields=", ".join(extra)))
        return calls
    stocked = order.attrs_dict.get("stocked_as")
    if stocked:
        raise ActionError(gettext("%(name)s is already in stock (%(where)s).", name=order.name or f"#{order.number}",
                                  where=stocked.replace(":", " #")))
    target = _inventory(s, v["to_stock"])
    if target.kind not in isvc.RESTOCK_KINDS:
        raise ActionError(gettext("Pick a reagents, chemicals, antibodies or viruses inventory to add it to."))
    return calls + [Call("POST", f"/inventory/{target.key}/items/save", form=_stock_form(s, module, target, order, v))]


def _stock_form(s, module, target, order, v) -> list[tuple[str, str]]:
    """The stock inventory's new-item form as Add to stock fills it from the
    order (inventory_routes._stock_from_order), with what was given over it.
    `from_order` links the two, as when the page's dialog is saved."""
    _target, draft = _restock(s, order, v)
    received = v.get("received_on") or (draft.received_on.isoformat() if draft.received_on else "")
    expires = draft.expires_on.isoformat() if draft.expires_on else ""
    form = [("id", ""), ("from_order", f"{module.key}:{order.id}"), ("name", draft.name or ""),
            ("is_shared", "1" if draft.is_shared else "0"), ("received_on", received), ("expires_on", expires),
            ("lot", draft.lot or "")]
    form += [(k, getattr(draft, k) or "") for k in ("quantity", "unit", "vendor", "catalog_number", "notes")]
    form += [(f"attr_{k}", str(x)) for k, x in json.loads(draft.attrs or "{}").items() if not isinstance(x, dict)]
    if "location_note" in v:
        form.append(("location_note", v["location_note"]))
    if v.get("box") not in (None, ""):
        form += [("rack_id", _inventory_box(s, target, v["box"])), ("position", str(v.get("position", "")))]
    elif v.get("position"):
        raise ActionError(gettext("A position needs a box."))
    return form


def _source(s, v) -> dict:
    """A sample's Source, as the page keeps it: which colony, and the ID in it."""
    given = [k for k in ("mouse", "tank", "animal") if v.get(k) not in (None, "")]
    if len(given) != 1:
        raise ActionError(gettext("Say which one animal it came from: a mouse, a tank or an animal."))
    if given[0] == "mouse":
        return {"kind": "mouse", "ref": str(find(s, "mouse", v["mouse"]).mouse_id)}
    if given[0] == "tank":
        return {"kind": "fish", "ref": find(s, "tank", v["tank"]).tank_id}
    animal = find(s, "org_animal", v["animal"])
    return {"kind": f"organism:{s.get(OrganismModule, animal.module_id_fk).key}", "ref": animal.code or str(animal.id)}


def _kind_inventory(s, kind: str, what: str, key=None):
    """The inventory named, or the lab's own of this kind."""
    from . import inventory_service as isvc
    from . import lab
    if key not in (None, ""):
        return _inventory(s, key)
    module = isvc.first_of_kind(s, kind)
    if module is None or not module.enabled or not lab.can_see(module):
        raise ActionError(gettext("There is no %(what)s inventory to add it to.", what=what))
    return module


ITEM_PLACE = ("owner", "shared", "box", "position", "location_note", "notes")


@action(name="sample_from_animal", area="inventory", title="Sample from an animal",
        help="A new sample (tissue, blood, DNA…) whose Source is the mouse, zebrafish tank or other animal it came "
             "from, in the lab's Samples (or another inventory with a Source column).", target=None,
        fields=(Field("mouse", "mouse", "The mouse it came from"),
                Field("tank", "tank", "Or the zebrafish tank it came from"),
                Field("animal", "org_animal", "Or the animal (or group) in another organism database"),
                Field("database", "text", "The inventory, by its key or name (the lab's Samples if left out)"),
                Field("name", "text", "Its sample ID"),
                Field("category", "text", "What it is: tissue, blood, serum, DNA, RNA… as the inventory's list has them"),
                Field("collected_on", "date", "When it was collected"),
                Field("amount", "text", "How much"),
                Field("status", "text", "One of the inventory's statuses (its first if left out)"),
                *(f for f in ITEM_FIELDS if f.name in ITEM_PLACE),
                Field("fields", "object", "Its other columns, by key (see inventories)")),
        describe=lambda s, _t, v: gettext("Sample from %(source)s in %(database)s: %(fields)s",
                                          source=_source_said(s, v),
                                          database=_inventory_said(s, v.get("database") or _samples(s).key),
                                          fields=_fields_said(v, skip=("mouse", "tank", "animal", "database")) or "-"))
def _sample_from_animal(s, _target, v):
    module = _samples(s, v.get("database"))
    from . import inventory_service as isvc
    source = next((f["key"] for f in isvc.view(module).fields if f["type"] == "source"), None)
    if source is None:
        raise ActionError(gettext("%(database)s has no Source column.", database=module.label))
    body = _item_body(v, skip=("database", "mouse", "tank", "animal", "collected_on", "amount", "fields"))
    body["fields"] = {**v.get("fields", {}), source: _source(s, v),
                      **{k: v[k] for k in ("collected_on", "amount") if k in v}}
    return [Call("POST", f"/api/v1/inventories/{module.key}/items", json=body)]


def _samples(s, key=None):
    return _kind_inventory(s, "samples", gettext("Samples"), key)


def _source_said(s, v) -> str:
    src = _source(s, v)
    if src["kind"] == "mouse":
        return gettext("mouse %(mouse)s", mouse=f"#{src['ref']}")
    if src["kind"] == "fish":
        return gettext("tank %(tank)s", tank=src["ref"])
    return gettext("animal %(animal)s", animal=src["ref"])


# ---------------------------------------------------------------- plasmids

PLASMID_FIELDS = (Field("name", "text", "Its name"),
                  Field("backbone", "text", "Its backbone"),
                  Field("insert", "text", "Its insert"),
                  Field("resistance", "text", "Its resistance (Amp, Kan…)"),
                  Field("owner", "text", "Username of its owner"),
                  Field("location", "text", "Where it is kept, in words"),
                  Field("box", "text", "The plasmid box it is in, by name (\"\" takes it out of its box)"),
                  Field("position", "text", "Its position in the box (D7…; its first free cell if left out)"),
                  Field("concentration", "text", "DNA concentration, ng/µL"),
                  Field("a260_280", "text", "A260/280"),
                  Field("shared", "bool", "true: lab common, anyone may edit it; false: personal"),
                  Field("notes", "text", "Its notes (replaces them)"),
                  Field("sequence", "text", "Its sequence, pasted: GenBank, FASTA or the bases alone (replaces any "
                                            "sequence and map it has)"))
PLASMID_FORM = {"name": "name", "backbone": "backbone", "insert": "insert_seq", "resistance": "resistance",
                "owner": "owner", "location": "location", "concentration": "concentration", "a260_280": "a260_280",
                "notes": "notes", "position": "position"}


def _plasmid_form(s, v) -> list[tuple[str, str]]:
    from .models import PlasmidBox
    form = [(PLASMID_FORM[k], str(v[k])) for k in PLASMID_FORM if k in v]
    if "shared" in v:
        form.append(("is_shared", "1" if v["shared"] else "0"))
    if "box" in v:
        box = None
        if v["box"] not in (None, ""):
            box = s.scalar(select(PlasmidBox).where(func.lower(PlasmidBox.name) == str(v["box"]).strip().lower()))
            if box is None:
                raise ActionError(gettext("There is no plasmid box “%(box)s”.", box=v["box"]))
        form.append(("box_id", str(box.id) if box else ""))
    for key, value in (v.get("fields") or {}).items():
        form.append((f"attr_{key}", "" if value is None else str(value)))
    return form


def _plasmid_label(p) -> str:
    return f"#{p.plasmid_id}" + (f" {p.name}" if p.name else "")


def _seq_said(v) -> dict:
    return {k: (gettext("%(n)s letters", n=len(x)) if k == "sequence" else x) for k, x in v.items()}


@action(name="plasmid_new", area="plasmids", title="New plasmid",
        help="Add a plasmid; its number is the next free one unless number is given (and free). A pasted "
             "GenBank or FASTA sequence gives it its map.", target=None,
        fields=(Field("number", "int", "Its number, when the lab numbers it by hand"), *PLASMID_FIELDS),
        describe=lambda _s, _t, v: gettext("New plasmid %(name)s: %(fields)s", name=v.get("name", ""),
                                           fields=_fields_said(_seq_said(v), skip=("name",)) or "-"))
def _plasmid_new(s, _target, v):
    form = [("count", "1"), ("plasmid_id", str(v.get("number", ""))), *_plasmid_form(s, v)]
    if v.get("sequence"):
        form.append(("sequence_text", v["sequence"]))
    return [Call("POST", "/plasmids", form=form)]


@action(name="plasmid_change", area="plasmids", title="Change a plasmid",
        help="Set any of these on one plasmid; what is left out stays as it is. sequence replaces its sequence "
             "(the old one stays in its versions).", target="plasmid",
        fields=(*PLASMID_FIELDS,
                Field("fields", "object", "The lab's own Plasmids columns, by key")),
        describe=lambda _s, p, v: gettext("Plasmid %(plasmid)s: %(fields)s", plasmid=_plasmid_label(p),
                                          fields=_fields_said(_seq_said(v))))
def _plasmid_change(s, plasmid, v):
    if not v:
        raise ActionError(gettext("Say what to change on plasmid %(plasmid)s.", plasmid=_plasmid_label(plasmid)))
    calls = []
    form = _plasmid_form(s, v)
    if form:
        calls.append(Call("POST", f"/plasmids/{plasmid.id}/update", form=form))
    if v.get("sequence"):
        calls.append(Call("POST", f"/plasmids/{plasmid.id}/upload-sequence", form=[("sequence_text", v["sequence"])]))
    return calls


def _plasmid_number(s, ref) -> str:
    return str(find(s, "plasmid", ref).plasmid_id)


def _in_brackets(text: str) -> str:
    return f" ({text})" if text else ""


@action(name="glycerol_stock_new", area="plasmids", title="New glycerol stock",
        help="Bacteria carrying a plasmid, frozen in glycerol, in the lab's Glycerol stocks: it then shows on the "
             "plasmid's page with the box it is in.", target=None,
        fields=(Field("plasmid", "plasmid", "The plasmid it carries", required=True),
                Field("name", "text", "Its name"),
                Field("strain", "text", "The bacterial strain (DH5α, Stbl3…)"),
                Field("colony", "text", "The colony or clone"),
                Field("resistance", "text", "Its resistance"),
                Field("verified", "text", "How it was checked: not checked, digest, colony PCR, Sanger, whole plasmid"),
                Field("frozen_on", "date", "When it was frozen"),
                Field("glycerol", "number", "Glycerol %"),
                Field("status", "text", "One of the inventory's statuses (its first if left out)"),
                *(f for f in ITEM_FIELDS if f.name in ITEM_PLACE),
                Field("database", "text", "The inventory, by its key or name (the lab's Glycerol stocks if left out)")),
        describe=lambda s, _t, v: gettext("New glycerol stock of plasmid %(plasmid)s%(what)s",
                                          plasmid=_plasmid_label(find(s, "plasmid", v["plasmid"])),
                                          what=_in_brackets(_fields_said(v, skip=("plasmid", "database")))))
def _glycerol_stock_new(s, _target, v):
    module = _kind_inventory(s, "glycerol_stocks", gettext("Glycerol stocks"), v.get("database"))
    own = ("colony", "resistance", "verified", "frozen_on", "glycerol")
    body = _item_body(v, skip=("database", "plasmid", "strain", *own))
    if "strain" in v:
        body["category"] = v["strain"]
    body["fields"] = {"plasmid": _plasmid_number(s, v["plasmid"]),
                      **{k: (f"{v[k]:g}" if isinstance(v[k], float) else v[k]) for k in own if k in v}}
    return [Call("POST", f"/api/v1/inventories/{module.key}/items", json=body)]


PRIMER_OWN = ("direction", "target", "pair", "modification", "stock_um", "purification")


@action(name="primer_new", area="plasmids", title="New primer",
        help="A primer or oligo in the lab's Primers, by its sequence (length, GC and Tm are worked out from it). "
             "With reverse, a pair: <name>-F and <name>-R, each the other's Pair, as Add primer pair makes them.",
        target=None,
        fields=(Field("name", "text", "Its name (a pair's, without -F and -R)", required=True),
                Field("sequence", "text", "Its sequence, 5′→3′ (a pair's forward)", required=True),
                Field("reverse", "text", "A pair's reverse sequence, 5′→3′"),
                Field("direction", "text", "forward, reverse or probe", choices=("forward", "reverse", "probe")),
                Field("use", "text", "What it is for: PCR, qPCR, sequencing, cloning, genotyping…"),
                Field("target", "text", "What it targets"),
                Field("plasmid", "plasmid", "The plasmid it binds"),
                Field("pair", "text", "The name of its partner"),
                Field("modification", "text", "A modification"),
                Field("stock_um", "number", "Stock concentration, µM"),
                Field("purification", "text", "desalted, cartridge, HPLC or PAGE"),
                Field("status", "text", "One of the inventory's statuses (its first, to order, if left out)"),
                Field("vendor", "text", "Who makes it"),
                *(f for f in ITEM_FIELDS if f.name in ITEM_PLACE),
                Field("database", "text", "The inventory, by its key or name (the lab's Primers if left out)")),
        describe=lambda s, _t, v: (gettext("New primer pair %(name)s-F / %(name)s-R", name=v["name"]) if v.get("reverse")
                                   else gettext("New primer %(name)s (%(n)s nt)", name=v["name"],
                                                n=len(re.sub(r"[^A-Za-z]", "", v["sequence"])))))
def _primer_new(s, _target, v):
    module = _kind_inventory(s, "primers", gettext("Primers"), v.get("database"))
    own = {k: (f"{v[k]:g}" if isinstance(v[k], float) else v[k]) for k in PRIMER_OWN if k in v}
    if "plasmid" in v:
        own["template"] = _plasmid_number(s, v["plasmid"])
    if not v.get("reverse"):
        body = _item_body(v, skip=("database", "sequence", "reverse", "use", "plasmid", *PRIMER_OWN))
        if "use" in v:
            body["category"] = v["use"]
        body["fields"] = {"sequence": v["sequence"], **own}
        return [Call("POST", f"/api/v1/inventories/{module.key}/items", json=body)]
    if "direction" in v or "pair" in v:
        raise ActionError(gettext("A pair is a forward and a reverse, each the other's Pair: leave out direction and pair."))
    form = [("name", v["name"]), ("forward", v["sequence"]), ("reverse", v["reverse"])]
    form += [(k, str(v[k])) for k in ("status", "owner", "vendor", "location_note", "notes") if k in v]
    if "use" in v:
        form.append(("category", v["use"]))
    if "shared" in v:
        form.append(("is_shared", "1" if v["shared"] else "0"))
    if v.get("box") not in (None, ""):
        form += [("rack_id", _inventory_box(s, module, v["box"])), ("position", str(v.get("position", "")))]
    form += [(f"attr_{k}", str(x)) for k, x in own.items()]
    return [Call("POST", f"/inventory/{module.key}/items/pair", form=form)]


# ---------------------------------------------------------------- calendar

TIME = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")


def _time(name: str, value) -> str:
    match = TIME.fullmatch(str(value or "").strip())
    if not match:
        raise ActionError(gettext("%(field)s is a time of day, like 14:30, not “%(value)s”.", field=name, value=value))
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def _span(v) -> tuple[str, str | None]:
    """(start, end) as the calendar's pages send them: a day, or a day and a time."""
    if "time" not in v:
        if "end_time" in v or "end_date" in v:
            raise ActionError(gettext("end_time and end_date go with a start time."))
        return v["date"], None
    start = f"{v['date']}T{_time('time', v['time'])}"
    if "end_time" not in v:
        if "end_date" in v:
            raise ActionError(gettext("end_date goes with end_time."))
        return start, None
    return start, f"{v.get('end_date', v['date'])}T{_time('end_time', v['end_time'])}"


def _when_said(v) -> str:
    start, end = _span(v)
    start = start.replace("T", " ")
    if end is None:
        return start
    end = end.replace("T", " ")
    return f"{start}–{end[11:] if end[:10] == start[:10] else end}"


@action(name="calendar_event_new", area="calendar", title="New calendar event",
        help="An event on the lab's calendar, which everyone sees (or, with personal, only you). All day unless it "
             "has a time.", target=None,
        fields=(Field("title", "text", "What it is", required=True),
                Field("date", "date", "The day", required=True),
                Field("time", "text", "When it starts, like 14:30 (all day if left out)"),
                Field("end_time", "text", "When it ends, like 16:00"),
                Field("end_date", "date", "The day it ends, when not the same day (with end_time)"),
                Field("description", "text", "More about it"),
                Field("personal", "bool", "true: only you see it")),
        describe=lambda _s, _t, v: gettext("Calendar: “%(title)s”, %(when)s%(whose)s", title=v["title"],
                                           when=_when_said(v),
                                           whose=gettext(" (only you)") if v.get("personal") else ""))
def _calendar_event_new(_s, _target, v):
    start, end = _span(v)
    body = {"kind": "event", "title": v["title"], "start": start, "end": end, "isAllday": "time" not in v,
            "description": v.get("description", ""), "audience": "0" if v.get("personal") else "1"}
    return [Call("POST", "/calendar/items", json=body)]


@action(name="equipment_booking_new", area="calendar", title="Book an instrument",
        help="Book time on a shared instrument (a confocal, a rig); refused if someone has it then. With repeat, "
             "the same time each day, weekday or week until a date, each a booking of its own.", target="equipment",
        fields=(Field("date", "date", "The day", required=True),
                Field("time", "text", "When it starts, like 09:00", required=True),
                Field("end_time", "text", "When it ends, like 11:30", required=True),
                Field("end_date", "date", "The day it ends, when not the same day"),
                Field("purpose", "text", "What it is for"),
                Field("repeat", "text", "Repeat it: daily, weekdays or weekly", choices=("daily", "weekdays", "weekly")),
                Field("until", "date", "With repeat: the last day it repeats")),
        describe=lambda _s, e, v: gettext("Book %(instrument)s, %(when)s%(repeat)s", instrument=e.name,
                                          when=_when_said(v),
                                          repeat=f" ({v['repeat']}, {gettext('until %(day)s', day=v.get('until', '-'))})"
                                          if v.get("repeat") else ""))
def _equipment_booking_new(_s, equipment, v):
    start, end = _span(v)
    body = {"equipment_id": equipment.id, "start": start, "end": end, "purpose": v.get("purpose", "")}
    if v.get("repeat"):
        body["repeat"] = {"freq": v["repeat"], "until": v.get("until", "")}
    elif "until" in v:
        raise ActionError(gettext("until goes with repeat."))
    return [Call("POST", "/calendar/bookings", json=body)]


# ---------------------------------------------------------------- running them

@dataclass
class ChangeOutcome:
    action: str
    summary: str = ""
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    said: list[str] = field(default_factory=list)       # what the pages answered
    records: list[dict] = field(default_factory=list)   # the change history rows it wrote

    def as_dict(self) -> dict:
        return {"action": self.action, "summary": self.summary, "ok": self.ok, "errors": self.errors,
                "warnings": self.warnings, "said": self.said, "records": self.records}


@dataclass
class Outcome:
    ok: bool
    changes: list[ChangeOutcome]
    batch_id: int | None = None

    @property
    def errors(self) -> list[str]:
        return [f"{i}. {e}" for i, c in enumerate(self.changes, 1) for e in c.errors]

    @property
    def warnings(self) -> list[str]:
        return [f"{i}. {w}" for i, c in enumerate(self.changes, 1) for w in c.warnings]


def _target(s, act: Action, raw: dict):
    if act.target is None:
        if raw.get("target") not in (None, "", {}):
            raise ActionError(gettext("%(action)s makes something new; it takes no target.", action=act.name))
        return None
    ref = raw.get("target")
    if ref in (None, "", {}):
        raise ActionError(gettext("%(action)s needs a target: a %(kind)s.", action=act.name,
                                  kind=act.target.replace("|", gettext(" or "))))
    kinds = act.target.split("|")
    if len(kinds) > 1:
        if not isinstance(ref, dict) or ref.get("kind") not in kinds:
            raise ActionError(gettext("The target of %(action)s is a reference: %(example)s.", action=act.name,
                                      example='{"kind": ' + " | ".join(f'"{k}"' for k in kinds) + ', "id": …}'))
        return find(s, ref["kind"], ref)
    return find(s, act.target, ref)


def _entries_since(s, last: int) -> tuple[list[dict], int]:
    rows = s.scalars(select(AuditEntry).where(AuditEntry.id > last).order_by(AuditEntry.id)).all()
    out = []
    for e in rows:
        try:
            changes = json.loads(e.changes_json) if e.changes_json else None
        except ValueError:
            changes = None
        out.append({"table": e.table_name, "id": e.record_id, "label": e.record_label, "action": e.action,
                    "details": e.details, "changes": changes})
    return out, (rows[-1].id if rows else last)


def side_effects(values: dict, records: list[dict]) -> list[str]:
    """What a change did that wasn't asked for, as its records show it: a
    record no longer (or now) shared with the lab, as when a Breeder cage's
    purpose changes (models._breeder_cages_start_shared)."""
    if "shared" in values:
        return []
    out = []
    for r in records:
        changes = r.get("changes") if isinstance(r.get("changes"), dict) else {}
        shared = (changes.get("changes") or {}).get("is_shared") if isinstance(changes, dict) else None
        if r.get("action") == "create" or not isinstance(shared, list) or len(shared) != 2 \
                or bool(shared[0]) == bool(shared[1]):
            continue
        name = r.get("label") or f"#{r.get('id')}"
        if r.get("table") == "mouse_cages":
            out.append(gettext("Cage %(cage)s will be shared with the lab, as Breeder cages are, for anyone to Pick from.", cage=name)  # noqa: E501
                       if shared[1] else
                       gettext("Cage %(cage)s will no longer be shared with the lab: only Breeder cages are shared on their own. Its owner can share it again.", cage=name))  # noqa: E501
        else:
            out.append(gettext("%(record)s will be shared with the lab.", record=name) if shared[1] else
                       gettext("%(record)s will no longer be shared with the lab.", record=name))
    return out


def run(app, username: str, changes: list[dict], apply: bool = False, label: str = "",
        description: str = "", lang: str = "en", source: str = "") -> Outcome:
    """Run `changes` as `username`, in order, in one transaction: a preview
    (rolled back) or, with apply, for real as one batch, kept only if every
    change went through. Each change's failed part is undone before the next
    runs, so a preview reports every change's own problems. Summaries and
    the pages' messages are in `lang`."""
    from flask import g
    with app.test_request_context("/"):
        g.lang = lang
        g.proposal_source = source      # who proposed it, for what says so (a notebook line)
        # Summaries are written as this person sees the lab (helpers that
        # read g.user: which databases they may open).
        with SessionLocal() as s:
            g.user = s.scalar(select(UserAccount).where(UserAccount.username == username,
                                                        UserAccount.disabled.is_(False)))
            if g.user is not None:
                s.expunge(g.user)
        return _run(app, username, changes, apply, label, description, lang)


def _run(app, username, changes, apply, label, description, lang) -> Outcome:
    if not isinstance(changes, list) or not changes:
        return Outcome(False, [ChangeOutcome("", ok=False, errors=[gettext("There are no changes.")])])
    if len(changes) > MAX_CHANGES:
        return Outcome(False, [ChangeOutcome("", ok=False, errors=[
            gettext("At most %(max)s changes at once.", max=MAX_CHANGES)])])
    outcomes: list[ChangeOutcome] = []
    batch_id = None
    with contained.transaction() as tx:
        with SessionLocal() as s:
            user = s.scalar(select(UserAccount).where(UserAccount.username == username,
                                                      UserAccount.disabled.is_(False)))
            last = s.scalar(select(func.max(AuditEntry.id))) or 0
            if user is None:
                return Outcome(False, [ChangeOutcome("", ok=False, errors=[gettext("Not signed in")])])
            if apply:
                row = BatchRecord(action="mixed", description=(description or label)[:200], target_table="",
                                  actor=username)
                s.add(row)
                s.commit()
                batch_id = row.id
        for raw in changes:
            out = ChangeOutcome(str(raw.get("action", "")) if isinstance(raw, dict) else "")
            outcomes.append(out)
            values: dict = {}
            step = tx.savepoint()
            try:
                if not isinstance(raw, dict):
                    raise ActionError(gettext("Each change is an object: {action, target, fields}."))
                act = CATALOGUE.get(out.action)
                if act is None:
                    near = nearest_actions(out.action)
                    if near:
                        raise ActionError(gettext("There is no action called “%(action)s”. The nearest: %(names)s.",
                                                  action=out.action, names=", ".join(near)))
                    raise ActionError(gettext("There is no action called “%(action)s”.", action=out.action))
                with SessionLocal() as s:
                    record = _target(s, act, raw)
                    values = clean(act, raw)
                    out.summary = act.describe(s, record, values)
                    shown = act.shows(s, record, values) if act.shows is not None else []
                    calls = act.build(s, record, values)
                    if act.warn is not None:
                        out.warnings += act.warn(s, record, values)
            except ActionError as exc:
                out.ok = False
                out.errors.append(str(exc))
                calls, shown = [], []
            for call in calls:
                result = contained.run(app, username, call.method, call.path, data=call.form, json_body=call.json,
                                       batch_id=batch_id, label=label, lang=lang)
                out.errors += [t for c, t in result.messages if c == "error"]
                out.warnings += [t for c, t in result.messages if c == "warning"]
                out.said += [t for c, t in result.messages if c not in ("error", "warning")]
                if not result.ok:
                    out.ok = False
                    if not result.messages:
                        out.errors.append(gettext("The page refused it (%(status)s).", status=result.status))
                    break
            if out.ok:
                step.commit()
                with SessionLocal() as s:
                    out.records, last = _entries_since(s, last)
                out.records += shown
                out.warnings += side_effects(values, out.records)
            else:
                step.rollback()
                if apply:
                    break
        ok = bool(outcomes) and all(o.ok for o in outcomes)
        if apply and ok:
            with SessionLocal() as s:
                row = s.get(BatchRecord, batch_id)
                row.record_count = len({(r["table"], r["id"]) for o in outcomes for r in o.records})
                s.commit()
            tx.commit()
    return Outcome(ok, outcomes, batch_id if apply and ok else None)


def catalogue() -> list[dict]:
    """Every action, for /api/v1 and the MCP server."""
    return [{"name": a.name, "area": a.area, "title": a.title, "help": a.help,
             "target": a.target, "fields": a.schema()} for a in CATALOGUE.values()]
