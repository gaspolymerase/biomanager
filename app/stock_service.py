"""Fly and worm stocks: the rules behind the vial/plate pages.

Numbers, positions, flip intervals, egg collection and the schedule live
here so the routes stay thin and the tests can call them directly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select

from . import i18n, positions
from . import stocks as presets
from .db import SessionLocal
from .i18n import gettext, pgettext, translate_value
from .models import (
    StockGenotype, StockIncubator, StockModule, StockRack, StockUnit,
)


# ---------------------------------------------------------------------------
# Module view
# ---------------------------------------------------------------------------

DEFAULT_DAYS = {"flip": 14, "develop": 10, "collect": 2}


def _w(text) -> str:
    """A stock word from the database's settings (vial, rack, Flip, a
    purpose…) in the page's language; a lab's own word stays as typed."""
    return translate_value(text, "stocks")


def norm_temp(raw) -> str:
    """"25.0", " 25 ", "25 °C" → "25"; "18.5" stays; anything else as typed."""
    text = str(raw or "").replace("°C", "").replace("°", "").strip()
    try:
        value = float(text)
    except ValueError:
        return text
    return str(int(value)) if value == int(value) else f"{value:g}"


def is_cross_label(text: str) -> bool:
    """Cross labels ("A × B", "F1 of A × B") describe a vial; they are not
    genotypes to suggest."""
    text = text or ""
    return " × " in text or text.startswith("F1 of ")


@dataclass
class ModuleView:
    row: StockModule
    s: dict

    def __getattr__(self, name):
        return getattr(self.row, name)

    # Nouns ---------------------------------------------------------------
    @property
    def unit(self) -> str:
        return self.s["unit_noun"]

    @property
    def units(self) -> str:
        return self.s["unit_noun_plural"]

    @property
    def rack_noun(self) -> str:
        return self.s["rack_noun"]

    @property
    def racks_noun(self) -> str:
        return self.s["rack_noun_plural"]

    @property
    def room(self) -> str:
        return self.s["room_noun"]

    @property
    def rooms(self) -> str:
        return self.s["room_noun_plural"]

    # Purposes ------------------------------------------------------------
    @property
    def purposes(self) -> list[dict]:
        return self.s["purposes"]

    def purpose_label(self, key: str) -> str:
        for p in self.purposes:
            if p["key"] == key:
                return p["label"]
        return (key or "").replace("_", " ").capitalize()

    @property
    def default_purpose(self) -> str:
        return self.purposes[0]["key"] if self.purposes else "stock"

    # Temperatures --------------------------------------------------------
    @property
    def temperatures(self) -> list[dict]:
        return self.s["temperatures"]

    @property
    def temp_values(self) -> list[str]:
        return [str(t["temp"]) for t in self.temperatures]

    def interval(self, what: str, temp: str | None) -> int:
        """Days for "flip", "develop" or "collect" at a temperature: the one
        listed, else the nearest one listed (21 °C takes 22's, 30 takes
        29's; "RT", room temperature, counts as 22), else the default
        temperature's."""
        table = {norm_temp(t["temp"]): t for t in self.temperatures}
        row = table.get(norm_temp(temp))
        if row is None and self.temperatures:
            raw = norm_temp(temp).lower()
            target = 22.0 if raw in ("rt", "room", "room temp", "room temperature") else None
            try:
                target = target if target is not None else float(raw)
                listed = [t for t in self.temperatures if _number(t["temp"]) is not None]
                if listed:
                    row = min(listed, key=lambda t: abs(_number(t["temp"]) - target))
            except (TypeError, ValueError):
                pass
        if row is None:
            row = table.get(norm_temp(self.s["default_temperature"])) or (self.temperatures[0] if self.temperatures else None)
        try:
            return max(1, int(row[what]))
        except (TypeError, ValueError, KeyError):
            return DEFAULT_DAYS[what]

    def code(self, unit: StockUnit) -> str:
        return f"{self.s['code_prefix']}{unit.number}"


def _number(text) -> float | None:
    try:
        return float(norm_temp(text))
    except (TypeError, ValueError):
        return None


def view(module: StockModule) -> ModuleView:
    return ModuleView(module, presets.settings_for(module.kind, module.settings))


def list_modules(session, include_disabled: bool = False, everyone: bool = False) -> list[StockModule]:
    """In a request: the lab's databases and the signed-in person's own
    (app/lab.py). `everyone`, or outside a request: every database."""
    stmt = select(StockModule).order_by(StockModule.position, StockModule.label)
    if not include_disabled:
        stmt = stmt.where(StockModule.enabled.is_(True))
    modules = list(session.scalars(stmt))
    from .lab import visible_list
    return modules if everyone else visible_list(modules)


def get_module(session, key: str) -> StockModule | None:
    """By its address, or one it had before a rename (app/database_keys.py)."""
    module = session.scalar(select(StockModule).where(StockModule.key == key))
    if module is None and key:
        from .database_keys import resolve
        module = resolve(session, "stocks", key)
    return module


def unique_key(session, label: str) -> str:
    from .database_keys import free_key
    return free_key(session, "stocks", label)


def create_module(session, kind: str, label: str = "", created_by: str = "", key: str = "") -> StockModule:
    preset = presets.PRESETS.get(kind) or presets.PRESETS["fly"]
    kind = kind if kind in presets.PRESETS else "fly"
    label = (label or preset["label"]).strip()
    count = session.scalar(select(func.count(StockModule.id))) or 0
    module = StockModule(key=key or unique_key(session, label), label=label, kind=kind,
                         icon=preset["icon"], blurb=preset["blurb"],
                         settings=json.dumps(presets.preset_settings(kind)),
                         position=200 + count, created_by=created_by)
    session.add(module)
    session.flush()
    return module


# ---------------------------------------------------------------------------
# Incubators and racks
# ---------------------------------------------------------------------------


def racks_of(session, module_id: int) -> list[StockRack]:
    return sorted(session.scalars(select(StockRack).where(StockRack.module_id_fk == module_id)),
                  key=lambda r: positions.place_order(r.name))


def incubators_of(session, module_id: int) -> list[StockIncubator]:
    return list(session.scalars(select(StockIncubator).where(StockIncubator.module_id_fk == module_id)
                                .order_by(StockIncubator.name)))


def rack_temperature(mv: ModuleView, rack: StockRack | None) -> str:
    if rack is not None and rack.incubator is not None and rack.incubator.temperature:
        return str(rack.incubator.temperature)
    return str(mv.s["default_temperature"])


def unit_temperature(mv: ModuleView, unit: StockUnit) -> str:
    return rack_temperature(mv, unit.rack)


def follow_temperature(mv: ModuleView, unit: StockUnit, before: StockRack | None, after: StockRack | None) -> None:
    """Progeny still developing that move to another temperature (18 °C
    from 25 °C) emerge later or sooner: what is left of their development
    is rescaled to the new temperature's time. The emerge date was worked
    out once, at collection, and stayed put before."""
    if unit.purpose != presets.PROGENY or unit.ready_on is None:
        return
    today = date.today()
    old, new = rack_temperature(mv, before), rack_temperature(mv, after)
    if unit.ready_on <= today or old == new:
        return
    old_days, new_days = mv.interval("develop", old), mv.interval("develop", new)
    if old_days and new_days and old_days != new_days:
        left = (unit.ready_on - today).days / old_days
        unit.ready_on = today + timedelta(days=max(1, round(left * new_days)))


def flip_interval(mv: ModuleView, rack: StockRack) -> int:
    return rack.flip_days or mv.interval("flip", rack_temperature(mv, rack))


def next_flip(mv: ModuleView, rack: StockRack) -> date | None:
    if rack.last_flipped_on is None:
        return None
    return rack.last_flipped_on + timedelta(days=flip_interval(mv, rack))


def flip_status(mv: ModuleView, rack: StockRack, today: date | None = None) -> dict:
    """The rack's last flip and when the next is due, for the line above its
    grid: {"text", "tone" ("", "due" or "overdue"), "title"}."""
    today = today or date.today()
    raw_verb = mv.s.get("flip_verb", "Flip")
    verb = _w(raw_verb).lower()
    done = _w(mv.s.get("flip_done", f"{raw_verb}ped"))
    values = {"n": flip_interval(mv, rack), "temp": rack_temperature(mv, rack)}
    title = (gettext("Every %(n)s days at %(temp)s °C (set on this rack)", **values) if rack.flip_days
             else gettext("Every %(n)s days at %(temp)s °C", **values))
    if rack.last_flipped_on is None:
        return {"text": gettext("No %(verb)s recorded yet", verb=verb), "tone": "due",
                "title": gettext("%(title)s. Set the last %(verb)s date under Edit.", title=title, verb=verb)}
    last = rack.last_flipped_on
    ago = (today - last).days
    on = i18n.strftime(last, "%a %d %b")
    if ago == 0:
        when = gettext("%(done)s %(on)s (today)", done=done, on=on)
    elif ago > 0:
        when = gettext("%(done)s %(on)s (%(n)s d ago)", done=done, on=on, n=ago)
    else:
        when = gettext("%(done)s %(on)s", done=done, on=on)
    due = next_flip(mv, rack)
    if due < today:
        return {"text": gettext("%(when)s · %(verb)s %(n)s d overdue", when=when, verb=verb, n=(today - due).days),
                "tone": "overdue", "title": title}
    if due == today:
        return {"text": gettext("%(when)s · %(verb)s due today", when=when, verb=verb), "tone": "due", "title": title}
    return {"text": gettext("%(when)s · next %(on)s", when=when, on=i18n.strftime(due, "%a %d %b")),
            "tone": "", "title": title}


def unit_stage(mv: ModuleView, unit: StockUnit, today: date | None = None) -> tuple[str, str]:
    """The colour of a vial's or plate's dot, and what it means: "young"
    (blue) while its progeny are still developing (before Progeny eclose /
    Progeny adult), "old" (red) once it is older than its rack's flip or
    chunk interval, "adult" (green) between. ("", "") for a discarded one.
    Its age counts from when it was set up or its rack last flipped."""
    if not unit.active:
        return "", ""
    today = today or date.today()
    if unit.ready_on and unit.ready_on > today:
        return "young", gettext("%(label)s %(on)s", label=_w(mv.s.get("ready_label", "Ready")),
                                on=i18n.strftime(unit.ready_on, "%d %b"))
    started = [d for d in (unit.set_up_on, unit.rack.last_flipped_on if unit.rack else None) if d]
    every = flip_interval(mv, unit.rack) if unit.rack else mv.interval("flip", rack_temperature(mv, None))
    if started and every and (today - max(started)).days > every:
        return "old", gettext("%(age)s days old, past its %(every)s-day %(verb)s", age=(today - max(started)).days,
                              every=every, verb=_w(mv.s.get("flip_verb", "Flip")).lower())
    return "adult", pgettext("stocks", "adult")


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def next_number(session, module_id: int) -> int:
    top = session.scalar(select(func.max(StockUnit.number)).where(StockUnit.module_id_fk == module_id))
    return (top or 0) + 1


def position_label(unit: StockUnit) -> str:
    rack = unit.rack
    if rack is None:
        return ""
    return positions.label(unit.rack_row, unit.rack_col, rack.naming, rack.cols)


def occupied(session, rack_id: int, exclude_ids=()) -> set[tuple[int, int]]:
    rows = session.execute(select(StockUnit.rack_row, StockUnit.rack_col, StockUnit.id).where(
        StockUnit.rack_id_fk == rack_id, StockUnit.active.is_(True),
        StockUnit.rack_row.is_not(None))).all()
    return {(r, c) for r, c, i in rows if i not in set(exclude_ids)}


def free_cells(session, rack: StockRack, count: int, start: tuple[int, int] | None = None,
               taken: set | None = None) -> list[tuple[int, int]]:
    """Up to `count` empty cells, reading along rows from `start`."""
    taken = set(taken if taken is not None else occupied(session, rack.id))
    begin = ((start[0] - 1) * rack.cols + start[1] - 1) if start else 0
    out = []
    for index in range(begin, rack.rows * rack.cols):
        cell = (index // rack.cols + 1, index % rack.cols + 1)
        if cell not in taken:
            out.append(cell)
            taken.add(cell)
            if len(out) == count:
                break
    return out


def apply_position(session, unit: StockUnit, rack_raw, position_raw) -> str | None:
    """Place a unit from a typed rack + position; an error message instead
    of a guess. A blank position in a rack takes the next free cell."""
    rack_raw = str(rack_raw or "").strip()
    position_raw = str(position_raw or "").strip()
    if not rack_raw:
        unit.rack_id_fk = unit.rack_row = unit.rack_col = None
        return None
    rack = session.get(StockRack, int(rack_raw)) if rack_raw.isdigit() else None
    if rack is None or rack.module_id_fk != unit.module_id_fk:
        return gettext("That rack is not part of this database.")
    if not position_raw:
        if unit.rack_id_fk == rack.id and unit.rack_row:
            return None
        cells = free_cells(session, rack, 1, taken=occupied(session, rack.id, exclude_ids=[unit.id or 0]))
        unit.rack_id_fk = rack.id
        unit.rack_row, unit.rack_col = cells[0] if cells else (None, None)
        unit.rack = rack
        if cells:
            return None
        return (gettext("%(rack)s is full; the record is in it but unplaced.", rack=rack.name) if unit.id
                else gettext("%(rack)s is full; the new one is in it but unplaced.", rack=rack.name))
    cell = positions.parse(position_raw, rack.naming, rack.rows, rack.cols)
    if cell is None:
        first = positions.label(1, 1, rack.naming, rack.cols)
        last = positions.label(rack.rows, rack.cols, rack.naming, rack.cols)
        return gettext("“%(position)s” is not a position in %(rack)s (%(first)s–%(last)s).",
                       position=position_raw, rack=rack.name, first=first, last=last)
    holder = session.scalar(select(StockUnit).where(
        StockUnit.rack_id_fk == rack.id, StockUnit.rack_row == cell[0], StockUnit.rack_col == cell[1],
        StockUnit.active.is_(True), StockUnit.id != (unit.id or 0)))
    if holder is not None:
        return gettext("%(rack)s · %(position)s already holds #%(number)s. Drag on the grid to swap.",
                       rack=rack.name, position=position_raw, number=holder.number)
    unit.rack_id_fk, (unit.rack_row, unit.rack_col) = rack.id, cell
    unit.rack = rack
    return None


def remember_genotype(session, module_id: int, text: str, user: str = "") -> None:
    """Add a genotype to the list the first time anyone writes it."""
    text = (text or "").strip()
    if not text or is_cross_label(text):
        return
    exists = session.scalar(select(StockGenotype.id).where(
        StockGenotype.module_id_fk == module_id, StockGenotype.genotype == text))
    if exists is None:
        # Flush first, so two vials with the same new genotype in one
        # request do not both insert it.
        session.add(StockGenotype(module_id_fk=module_id, genotype=text, created_by=user))
        session.flush()


def cross_label(unit: StockUnit) -> str:
    female, male = (unit.female_genotype or "").strip(), (unit.male_genotype or "").strip()
    if female or male:
        return f"{female or '?'} × {male or '?'}"
    return unit.genotype


def collect_eggs(session, mv: ModuleView, cross: StockUnit, user: str, today: date | None = None) -> tuple[StockUnit, str]:
    """Egg collection from a cross: a new progeny vial with the cross's
    parents written on it, placed in the next free cell of the same rack,
    and a due date for when the progeny emerge. Returns (vial, note)."""
    today = today or date.today()
    temp = unit_temperature(mv, cross)
    progeny = StockUnit(
        module_id_fk=cross.module_id_fk, number=next_number(session, cross.module_id_fk),
        genotype=f"F1 of {cross_label(cross)}", purpose=presets.PROGENY,
        female_genotype=cross.female_genotype, male_genotype=cross.male_genotype,
        parent_id_fk=cross.id, set_up_on=today, owner=user or cross.owner,
        ready_on=today + timedelta(days=mv.interval("develop", temp)), updated_by=user)
    session.add(progeny)
    note = ""
    if cross.rack is not None:
        # Same rack as the cross; without a cell if the rack is full.
        progeny.rack_id_fk, progeny.rack = cross.rack_id_fk, cross.rack
        cells = free_cells(session, cross.rack, 1)
        if cells:
            progeny.rack_row, progeny.rack_col = cells[0]
        else:
            note = gettext("%(rack)s is full, so it has no position yet.", rack=cross.rack.name)
    cross.last_collected_on = today
    session.flush()
    return progeny, note


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


def schedule(session, mv: ModuleView, today: date | None = None, horizon: int = 14) -> list[dict]:
    """Everything due up to `horizon` days ahead, overdue first.

    Each item: kind (flip | collect | ready | shift | score), due (date),
    subject (rack or unit), title, detail."""
    today = today or date.today()
    until = today + timedelta(days=horizon)
    out: list[dict] = []
    for rack in racks_of(session, mv.id):
        due = next_flip(mv, rack)
        count = session.scalar(select(func.count(StockUnit.id)).where(
            StockUnit.rack_id_fk == rack.id, StockUnit.active.is_(True))) or 0
        verb = _w(mv.s["flip_verb"])
        noun = _w(mv.unit if count == 1 else mv.units)
        if due is None and count:
            out.append({"kind": "flip", "due": today, "rack": rack, "unset": True,
                        "title": gettext("%(verb)s %(rack)s", verb=verb, rack=rack.name),
                        "detail": gettext("%(count)s %(units)s; no %(verb)s date recorded yet",
                                          count=count, units=noun, verb=verb.lower())})
        elif due is not None and due <= until and count:
            out.append({"kind": "flip", "due": due, "rack": rack,
                        "title": gettext("%(verb)s %(rack)s", verb=verb, rack=rack.name),
                        "detail": gettext("%(count)s %(units)s · every %(n)s days at %(temp)s °C · last %(on)s",
                                          count=count, units=noun, n=flip_interval(mv, rack),
                                          temp=rack_temperature(mv, rack),
                                          on=i18n.strftime(rack.last_flipped_on, "%d %b"))})
    units = list(session.scalars(select(StockUnit).where(
        StockUnit.module_id_fk == mv.id, StockUnit.active.is_(True))))
    for u in units:
        code = mv.code(u)
        if u.purpose == presets.CROSS:
            last = u.last_collected_on or u.set_up_on
            if last is not None:
                due = last + timedelta(days=mv.interval("collect", unit_temperature(mv, u)))
                if due <= until:
                    out.append({"kind": "collect", "due": due, "unit": u,
                                "title": gettext("%(verb)s: %(code)s", verb=_w(mv.s["collect_verb"]), code=code),
                                "detail": (gettext("%(cross)s · last %(on)s", cross=cross_label(u),
                                                   on=i18n.strftime(u.last_collected_on, "%d %b"))
                                           if u.last_collected_on
                                           else gettext("%(cross)s · first collection", cross=cross_label(u)))})
        if u.ready_on is not None and u.ready_on <= until:
            detail = (gettext("%(genotype)s from %(code)s", genotype=u.genotype or "",
                              code=f"{mv.s['code_prefix']}{u.parent.number}")
                      if u.parent is not None else (u.genotype or ""))
            out.append({"kind": "ready", "due": u.ready_on, "unit": u,
                        "title": gettext("%(verb)s: %(code)s", verb=_w(mv.s["ready_label"]), code=code),
                        "detail": detail})
        if u.shift_on is not None and u.shifted_on is None and u.shift_on <= until:
            out.append({"kind": "shift", "due": u.shift_on, "unit": u,
                        "title": gettext("Shift %(code)s to %(temp)s °C", code=code, temp=u.shift_to or "?"),
                        "detail": gettext("%(genotype)s · now at %(temp)s °C", genotype=u.genotype,
                                          temp=unit_temperature(mv, u))})
        if u.score_on is not None and u.score_on <= until and not u.attrs_dict.get("scored_on"):
            out.append({"kind": "score", "due": u.score_on, "unit": u,
                        "title": gettext("Score %(code)s", code=code), "detail": u.genotype})
    for item in out:
        item["overdue"] = item["due"] < today
        item["today"] = item["due"] == today
    return sorted(out, key=lambda i: (i["due"], i["kind"], i["title"]))


# ---------------------------------------------------------------------------
# Boot: create the fly and worm databases, moving any organism-engine data
# ---------------------------------------------------------------------------


def tidy_genotype_lists() -> int:
    """Once: drop cross labels that earlier versions saved into the
    genotype lists."""
    from .inventory_service import get_setting, set_setting

    with SessionLocal() as session:
        if get_setting(session, "stock_genotypes_tidied"):
            return 0
        removed = 0
        for item in session.scalars(select(StockGenotype)):
            if is_cross_label(item.genotype):
                session.delete(item)
                removed += 1
        set_setting(session, "stock_genotypes_tidied", "1")
        session.commit()
        return removed


def seed_modules() -> list[str]:
    """Once: create Drosophila and C. elegans as stock databases. Where the
    old configurable organism modules exist, their vials, lines, crosses
    and racks are copied across and the old module is hidden (its rows are
    kept untouched)."""
    from .inventory_service import get_setting, set_setting
    from .models import OrganismModule

    done = []
    with SessionLocal() as session:
        if get_setting(session, "stocks_seeded"):
            return done
        for kind, old_key in (("fly", "drosophila"), ("worm", "c_elegans")):
            old = session.scalar(select(OrganismModule).where(
                (OrganismModule.key == old_key) | (OrganismModule.preset_key == old_key)))
            label = old.label if old is not None else presets.PRESETS[kind]["label"]
            key = old.key if old is not None else old_key
            if get_module(session, key) is not None:
                continue
            module = create_module(session, kind, label, created_by="system", key=key)
            module.position = 200 + len(done)
            if old is not None:
                _copy_from_organism_module(session, view(module), old)
                old.enabled = False
            done.append(key)
        set_setting(session, "stocks_seeded", "1")
        session.commit()
    return done


def _copy_from_organism_module(session, mv: ModuleView, old) -> None:
    from .models import OrgCross, OrgHousing, OrgLine, OrgLocation, Organism

    # Locations: grids become racks, the rest incubators.
    incubators, racks = {}, {}
    locations = list(session.scalars(select(OrgLocation).where(OrgLocation.module_id_fk == old.id)))
    for loc in locations:
        if not (loc.rows and loc.cols):
            settings = presets.load(loc.settings)
            temp = str(settings.get("temperature_c") or "")
            if not temp:
                # "Incubator 25", "25 °C room": the temperature is often the name.
                found = re.search(r"(?<!\d)(1[0-9]|2[0-9]|3[0-5])(?!\d)", loc.name or "")
                temp = found.group(1) if found else ""
            inc = StockIncubator(module_id_fk=mv.id, name=loc.name, temperature=temp, notes=loc.notes or "")
            session.add(inc)
            incubators[loc.id] = inc
    session.flush()
    for loc in locations:
        if loc.rows and loc.cols:
            parent = incubators.get(loc.parent_id_fk)
            rack = StockRack(module_id_fk=mv.id, name=loc.name, rows=loc.rows, cols=loc.cols,
                             naming=presets.load(loc.settings).get("naming") and json.dumps(presets.load(loc.settings)["naming"]) or "{}",
                             incubator_id_fk=parent.id if parent else None, notes=loc.notes or "")
            session.add(rack)
            racks[loc.id] = rack
    session.flush()

    lines = {l.id: l for l in session.scalars(select(OrgLine).where(OrgLine.module_id_fk == old.id))}
    for line in lines.values():
        text = (line.genotype or line.name or line.code or "").strip()
        if text:
            remember_genotype(session, mv.id, text, "system")
            g = session.scalar(select(StockGenotype).where(StockGenotype.module_id_fk == mv.id,
                                                           StockGenotype.genotype == text))
            attrs = line.attrs_dict
            g.alias = line.code if line.code != text else ""
            g.source = str(attrs.get("stock_centre") or attrs.get("source") or line.source or "")
            g.stock_number = str(attrs.get("stock_number") or line.external_ref or "")
            g.notes = line.notes or ""

    def line_text(line_id):
        line = lines.get(line_id)
        return (line.genotype or line.name or line.code or "").strip() if line else ""

    purpose_keys = {p["key"] for p in mv.purposes}
    crosses = list(session.scalars(select(OrgCross).where(OrgCross.module_id_fk == old.id)))
    cross_by_housing = {c.housing_id_fk: c for c in crosses if c.housing_id_fk}
    number = next_number(session, mv.id)
    for h in session.scalars(select(OrgHousing).where(OrgHousing.module_id_fk == old.id).order_by(OrgHousing.id)):
        resident = session.scalar(select(Organism).where(Organism.housing_id_fk == h.id))
        genotype = line_text(h.line_id_fk) or (resident.genotype if resident else "")
        rack = racks.get(h.location_id_fk)
        purpose = h.purpose if h.purpose in purpose_keys else mv.default_purpose
        unit = StockUnit(module_id_fk=mv.id, number=number, genotype=genotype, purpose=purpose,
                         active=h.active, rack_id_fk=rack.id if rack else None,
                         rack_row=h.row if rack else None, rack_col=h.col if rack else None,
                         set_up_on=h.established_on, owner=h.owner or "", notes=h.notes or "",
                         attrs=json.dumps({"old_code": h.code}))
        cross = cross_by_housing.get(h.id)
        if cross is not None:
            unit.purpose = presets.CROSS
            unit.female_genotype = cross.dam_label or line_text(cross.dam_line_id_fk)
            unit.male_genotype = cross.sire_label or line_text(cross.sire_line_id_fk)
            unit.set_up_on = unit.set_up_on or cross.set_up_on
        session.add(unit)
        if rack is not None and h.last_serviced_on and (rack.last_flipped_on is None or h.last_serviced_on > rack.last_flipped_on):
            rack.last_flipped_on = h.last_serviced_on
        remember_genotype(session, mv.id, genotype, "system")
        number += 1
    for cross in crosses:
        if cross.housing_id_fk:
            continue
        female = cross.dam_label or line_text(cross.dam_line_id_fk)
        male = cross.sire_label or line_text(cross.sire_line_id_fk)
        session.add(StockUnit(module_id_fk=mv.id, number=number, purpose=presets.CROSS,
                              genotype=f"{female or '?'} × {male or '?'}", female_genotype=female,
                              male_genotype=male, set_up_on=cross.set_up_on, owner=cross.owner or "",
                              notes=cross.notes or "", attrs=json.dumps({"old_code": cross.code})))
        number += 1
    session.flush()
