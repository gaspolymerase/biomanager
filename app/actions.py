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

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from sqlalchemy import func, select

from . import contained
from .db import SessionLocal
from .i18n import gettext, ngettext
from .models import (FISH_SEX_OPTIONS, FISH_STATUS_OPTIONS, TANK_PURPOSE_OPTIONS, AuditEntry, BatchRecord, CageRecord,
                     ClutchRecord, FishLine, FishRack, FishRecord, LitterRecord, MouseRack, MouseRecord, TankRecord,
                     Organism, OrganismModule, OrgCohort, OrgCross, OrgDue, OrgHousing, OrgLine, OrgLocation,
                     StockFrozen, StockModule, StockRack, StockUnit, UserAccount, WaterSystem)

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

    def schema(self) -> dict:
        """JSON Schema for this action's fields (the MCP server's tools)."""
        props = {f.name: _field_schema(f) for f in self.fields}
        return {"type": "object", "properties": props, "additionalProperties": False,
                "required": [f.name for f in self.fields if f.required]}


CATALOGUE: dict[str, Action] = {}


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


def _field_schema(f: Field) -> dict:
    kinds = {"text": {"type": "string"}, "date": {"type": "string", "format": "date"},
             "int": {"type": "integer"}, "number": {"type": "number"}, "bool": {"type": "boolean"},
             "sex": {"type": "string", "enum": ["F", "M", ""]},
             "list": {"type": "array", "items": {"type": "string"}},
             "object": {"type": "object"}}
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


CAGE_FIELDS = (Field("purpose", "text", "Breeder, Holding, Experiment… as the lab's list has them"),
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


@action(name="litter_born", area="mice", title="Litter born",
        help="A litter was born in this cage (the cage keeps one birth date; weaning is due from it).",
        target="cage",
        fields=(Field("date", "date", "The day they were born (today if left out)"),),
        describe=lambda _s, c, v: gettext("Litter born in cage %(cage)s%(on)s", cage=c.cage_id,
                                          on=_on(v.get("date"))))
def _litter_born(_s, cage, v):
    return [Call("POST", f"/colony/cages/{cage.id}/give-birth", form=[("date_give_birth", v.get("date", ""))])]


@action(name="litter_genotyping", area="mice", title="Create the litter at genotyping",
        help="Make the litter for this cage's pups and one mouse per pup (status geno), in the cage.",
        target="cage",
        fields=(Field("pups", "int", "How many pups (1–40)", required=True),
                Field("father", "text", "The father, as the lab writes it (a mouse number or a note)"),
                Field("mother", "text", "The mother, or mothers")),
        describe=lambda _s, c, v: gettext("Litter of %(pups)s pups made in cage %(cage)s, a mouse for each",
                                          pups=v["pups"], cage=c.cage_id))
def _litter_genotyping(_s, cage, v):
    return [Call("POST", f"/colony/cages/{cage.id}/genotyping",
                 form=[("total_pups", str(v["pups"])), ("father_info", v.get("father", "")),
                       ("mother_info", v.get("mother", ""))])]


@action(name="wean", area="mice", title="Wean a cage",
        help="Wean this cage. With rows, move its pups out: each row is some of its mice, their sex, and an "
             "existing cage or a new one (new_card). Pups younger than P18 are refused unless early is true.",
        target="cage",
        fields=(Field("rows", "rows", "Where the pups go: [{mice, sex, cage | new_card}]"),
                Field("early", "bool", "Wean although the pups are younger than usual")),
        describe=lambda s, c, v: _wean_said(s, c, v))
def _wean(s, cage, v):
    early = [("early", "1")] if v.get("early") else []
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
        help="Add a line to the notes of a mouse, cage or litter (what is there stays).", target="mouse|cage|litter",
        fields=(Field("text", "text", "What to add", required=True),),
        describe=lambda _s, r, v: gettext("Note on %(record)s: “%(text)s”", record=_record_label(r), text=v["text"]))
def _note_add(_s, record, v):
    if isinstance(record, MouseRecord):
        return [Call("PATCH", f"/api/v1/mice/{record.mouse_id}", json={"note": _added(record.note, v["text"])})]
    if isinstance(record, CageRecord):
        return [Call("POST", f"/colony/cages/{record.id}/update", form=[("notes", _added(record.notes, v["text"]))])]
    return [Call("POST", f"/colony/litters/{record.id}/update", form=[("notes", _added(record.notes, v["text"]))])]


def _record_label(r) -> str:
    if isinstance(r, MouseRecord):
        return gettext("mouse %(mouse)s", mouse=_mouse_label(r))
    if isinstance(r, CageRecord):
        return gettext("cage %(cage)s", cage=r.cage_id)
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


def run(app, username: str, changes: list[dict], apply: bool = False, label: str = "",
        description: str = "") -> Outcome:
    """Run `changes` as `username`, in order, in one transaction: a preview
    (rolled back) or, with apply, for real as one batch, kept only if every
    change went through. Each change's failed part is undone before the next
    runs, so a preview reports every change's own problems."""
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
            step = tx.savepoint()
            try:
                if not isinstance(raw, dict):
                    raise ActionError(gettext("Each change is an object: {action, target, fields}."))
                act = CATALOGUE.get(out.action)
                if act is None:
                    raise ActionError(gettext("There is no action called “%(action)s”.", action=out.action))
                with SessionLocal() as s:
                    record = _target(s, act, raw)
                    values = clean(act, raw)
                    out.summary = act.describe(s, record, values)
                    calls = act.build(s, record, values)
            except ActionError as exc:
                out.ok = False
                out.errors.append(str(exc))
                calls = []
            for call in calls:
                result = contained.run(app, username, call.method, call.path, data=call.form, json_body=call.json,
                                       batch_id=batch_id, label=label)
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
