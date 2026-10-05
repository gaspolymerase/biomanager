"""Lab inventories: creating them, reading their settings, and the helpers
their routes and the rest of the app share. Presets live in inventory.py."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import get_history

from . import inventory as presets
from . import positions
from .db import SessionLocal
from .models import AppSetting, InventoryItem, InventoryModule, InventoryRack


# ---------------------------------------------------------------------------
# Module view
# ---------------------------------------------------------------------------


@dataclass
class ModuleView:
    row: InventoryModule
    settings: dict

    def __getattr__(self, name):
        return getattr(self.row, name)

    def has(self, feature: str) -> bool:
        return feature in self.settings["features"]

    @property
    def fields(self) -> list[dict]:
        return self.settings["fields"]

    @property
    def table_fields(self) -> list[dict]:
        return [f for f in self.settings["fields"] if f["in_table"]]

    @property
    def statuses(self) -> list[str]:
        return self.settings["statuses"]

    @property
    def categories(self) -> list[str]:
        return self.settings["categories"]

    @property
    def category_label(self) -> str:
        return self.settings["category_label"]

    @property
    def name_label(self) -> str:
        return {"samples": "Sample ID", "orders": "Item", "antibodies": "Target", "viruses": "Virus",
                "cell_lines": "Cell line"}.get(self.row.kind, "Name")

    @property
    def requirable(self) -> list[tuple[str, str]]:
        """(form name, label) of the columns that can be made required."""
        names = {"name": self.name_label, "category": self.category_label}
        out = [(key, label or names[key]) for key, feature, label in presets.REQUIRABLE
               if feature is None or self.has(feature)]
        return out + [(f"attr_{f['key']}", f["label"]) for f in self.fields if f["type"] != "source"]

    @property
    def required(self) -> list[str]:
        """Columns a new entry must have filled in. An order always needs a name."""
        chosen = self.settings.get("required")
        if chosen is None:
            chosen = (presets.PRESETS.get(self.row.kind) or {}).get("required", [])
        if self.row.kind == "orders" and "name" not in chosen:
            chosen = ["name", *chosen]
        known = {key for key, _ in self.requirable}
        return [key for key in chosen if key in known]

    @property
    def required_labels(self) -> dict[str, str]:
        labels = dict(self.requirable)
        return {key: labels[key] for key in self.required}

    @property
    def open_statuses(self) -> list[str]:
        """Statuses that still need action (for an Open chip on boards)."""
        s = self.statuses
        return s[:-2] if len(s) > 2 else s[:1]

    @property
    def available_statuses(self) -> list[str]:
        return available_statuses(self)

    def is_available(self, status: str) -> bool | None:
        return is_available(self, status)


def view(module: InventoryModule) -> ModuleView:
    return ModuleView(module, presets.normalise_settings(module.settings))


def list_modules(session, include_disabled: bool = False, everyone: bool = False) -> list[InventoryModule]:
    """In a request: the lab's databases and the signed-in person's own
    (app/lab.py). `everyone`, or outside a request: every database."""
    stmt = select(InventoryModule).order_by(InventoryModule.position, InventoryModule.label)
    if not include_disabled:
        stmt = stmt.where(InventoryModule.enabled.is_(True))
    modules = list(session.scalars(stmt))
    from .lab import visible_list
    return modules if everyone else visible_list(modules)


def get_module(session, key: str) -> InventoryModule | None:
    """By its address, or one it had before a rename (app/database_keys.py)."""
    module = session.scalar(select(InventoryModule).where(InventoryModule.key == key))
    if module is None and key:
        from .database_keys import resolve
        module = resolve(session, "inventory", key)
    return module


def first_of_kind(session, kind: str) -> InventoryModule | None:
    """The lab's inventory of this kind (never someone's personal one, or a
    project group's)."""
    return session.scalar(select(InventoryModule).where(InventoryModule.kind == kind,
                                                        InventoryModule.private_to == "",
                                                        InventoryModule.share_group_id.is_(None))
                          .order_by(InventoryModule.position, InventoryModule.id))


def unique_key(session, label: str) -> str:
    from .database_keys import free_key
    return free_key(session, "inventory", label)


def create_module(session, preset_key: str, label: str = "", created_by: str = "") -> InventoryModule:
    preset = presets.PRESETS.get(preset_key) or presets.PRESETS["custom"]
    label = (label or preset["label"]).strip()
    count = session.scalar(select(func.count(InventoryModule.id))) or 0
    module = InventoryModule(
        key=unique_key(session, label), label=label, kind=preset_key if preset_key in presets.PRESETS else "custom",
        icon=preset["icon"], blurb=preset["blurb"],
        item_noun=preset["item_noun"], item_noun_plural=preset["item_noun_plural"],
        settings=json.dumps(presets.preset_settings(preset_key)),
        position=300 + count, created_by=created_by,
    )
    session.add(module)
    session.flush()
    return module


# ---------------------------------------------------------------------------
# Settings kept in app_settings
# ---------------------------------------------------------------------------


def get_setting(session, key: str, default: str = "") -> str:
    row = session.get(AppSetting, key)
    return row.value if row else default


def set_setting(session, key: str, value: str) -> None:
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value


# Built-in databases a lab can rename: {key: (default name, short name)}.
# The chosen name is kept in app_settings as "db_label:<key>".
BUILTIN_DATABASES = {
    "colony": ("Mouse colony", "Mouse"),
    "zebrafish": ("Zebrafish", "Fish"),
    "plasmids": ("Plasmids", "Plasmids"),
}


def builtin_labels(session) -> dict[str, str]:
    """{key: name} for every built-in database, renamed or not."""
    rows = session.scalars(select(AppSetting).where(AppSetting.key.like("db_label:%")))
    renamed = {r.key.split(":", 1)[1]: r.value.strip() for r in rows if r.value.strip()}
    return {key: renamed.get(key, default) for key, (default, _short) in BUILTIN_DATABASES.items()}


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


def next_number(session, module_id: int) -> int:
    top = session.scalar(select(func.max(InventoryItem.number)).where(InventoryItem.module_id_fk == module_id))
    return (top or 0) + 1


def rack_label(item: InventoryItem) -> str:
    rack = item.rack
    if rack is None:
        return ""
    return positions.label(item.rack_row, item.rack_col, rack.naming, rack.cols)


def expiry_state(item: InventoryItem, today: date | None = None) -> str:
    """"expired", "soon" (within 30 days) or ""."""
    if not item.expires_on:
        return ""
    today = today or date.today()
    if item.expires_on < today:
        return "expired"
    if item.expires_on <= today + timedelta(days=30):
        return "soon"
    return ""


# ---------------------------------------------------------------------------
# Status: one rule for the dialog, the sheet, the board, bulk edits and CSV
# ---------------------------------------------------------------------------

# Statuses after which an item is gone: used up, emptied, thrown out,
# cancelled. Moving into one stamps the day in attrs[ENDED_ATTR].
TERMINAL_STATUSES = {"used up", "empty", "discarded", "cancelled"}
# Of those, the ones that leave the box: the tube's cell is freed for the
# next one, and its location note says where it was.
GONE_FROM_BOX = {"used up", "empty", "discarded"}
# Stock that runs out or expires: Home's "Expiring & low stock", and what a
# received order can be added to (inventory_routes.STOCK_KINDS).
RESTOCK_KINDS = ("reagents", "antibodies", "viruses")
ENDED_ATTR = "used_up_on"

# The statuses that mean "usable" (or, for orders, "still open"): the green
# dot on the sheet. Custom lists: anything not terminal.
AVAILABLE_BY_KIND = {
    "samples": {"available", "in use"},
    "reagents": {"in stock", "low"},
    "antibodies": {"in stock", "low"},
    "viruses": {"in stock", "low"},
    "primers": {"in stock", "low"},
    "cell_lines": {"in stock"},
    "orders": {"requested", "ordered"},
}


def available_statuses(mv) -> list[str]:
    wanted = AVAILABLE_BY_KIND.get(mv.row.kind)
    if wanted:
        picked = [s for s in mv.statuses if s.lower() in wanted]
        if picked:
            return picked
    return [s for s in mv.statuses if s.lower() not in TERMINAL_STATUSES]


def is_available(mv, status: str) -> bool | None:
    """True / False for the availability dot; None when the inventory has
    no statuses (no dot)."""
    if not mv.statuses:
        return None
    return (status or "").strip().lower() in {s.lower() for s in available_statuses(mv)}


def match_status(mv, raw) -> str | None:
    """The inventory's own spelling of a status typed in any case."""
    raw = str(raw or "").strip().lower()
    return next((s for s in mv.statuses if s.lower() == raw), None)


def apply_status(mv, item: InventoryItem, new_status, today: date | None = None) -> str | None:
    """Set an item's status the one way every path does it; an error message
    instead when the status is not one of the inventory's.

    An item keeps a status the list no longer has as long as it is not
    changed. Changing to "received" fills an empty received date; changing
    to a terminal status (used up, empty, discarded, cancelled) records the
    day in attrs, and reviving the item clears it again. Used up, empty or
    discarded also frees its box position (the location note, if empty,
    keeps where it was)."""
    new = str(new_status or "").strip()[:40]
    old = item.status or ""
    if mv.statuses:
        canonical = match_status(mv, new)
        if canonical is None:
            if new != old:
                return f"“{new}” is not a status here. Use one of: {', '.join(mv.statuses)}."
            canonical = new
        new = canonical
    if new == old and item.id:
        return None
    today = today or date.today()
    item.status = new
    if new.lower() == "received" and mv.has("received") and not item.received_on:
        item.received_on = today
    attrs = item.attrs_dict
    before = dict(attrs)
    if new.lower() in TERMINAL_STATUSES:
        attrs.setdefault(ENDED_ATTR, today.isoformat())
    else:
        attrs.pop(ENDED_ATTR, None)
    if new.lower() in GONE_FROM_BOX and item.rack is not None:
        where = " · ".join(filter(None, [item.rack.name, rack_label(item)]))
        if not (item.location_note or "").strip():
            item.location_note = f"was in {where}"[:200]
        item.rack_id_fk = item.rack_row = item.rack_col = None
        item.rack = None
    if attrs != before:
        item.attrs = json.dumps(attrs)
    return None


def free_cell(session, rack: InventoryRack, taken: set | None = None) -> tuple[int, int] | None:
    """The first empty cell of a box, row by row."""
    used = {(r, c) for r, c in session.execute(select(InventoryItem.rack_row, InventoryItem.rack_col).where(
        InventoryItem.rack_id_fk == rack.id, InventoryItem.rack_row.is_not(None)))}
    used |= taken or set()
    for r in range(1, rack.rows + 1):
        for c in range(1, rack.cols + 1):
            if (r, c) not in used:
                return r, c
    return None


def free_cells(session, rack: InventoryRack, count: int, start: tuple[int, int] | None = None) -> list[tuple[int, int]]:
    """Up to `count` empty cells of a box, reading along rows from `start`
    (the first cell when None): side by side, skipping taken ones."""
    taken = {(r, c) for r, c in session.execute(select(InventoryItem.rack_row, InventoryItem.rack_col).where(
        InventoryItem.rack_id_fk == rack.id, InventoryItem.rack_row.is_not(None)))}
    begin = ((start[0] - 1) * rack.cols + start[1] - 1) if start else 0
    out = []
    for index in range(begin, rack.rows * rack.cols):
        cell = (index // rack.cols + 1, index % rack.cols + 1)
        if cell not in taken:
            out.append(cell)
            if len(out) == count:
                break
    return out


# ---------------------------------------------------------------------------
# Configure: renaming or removing a status or category relabels the items
# ---------------------------------------------------------------------------

# The item column each choice list labels, and how long a value may be.
CHOICE_COLUMNS = {"statuses": ("status", 40), "categories": ("category", 80)}


@dataclass
class ChoicePlan:
    values: list[str]                       # the new list, in order
    relabel: dict[str, tuple[str, str]]     # {old: (new, "rename" | "replace")}
    problems: list[str]                     # removed values still in use, with no replacement


def choice_counts(session, module_id: int, column: str) -> dict[str, int]:
    """{value: how many items of this inventory hold it}."""
    col = getattr(InventoryItem, column)
    return {v or "": n for v, n in session.execute(
        select(col, func.count(InventoryItem.id)).where(InventoryItem.module_id_fk == module_id).group_by(col))}


def plan_choices(rows: list[dict], counts: dict[str, int]) -> ChoicePlan:
    """Read the Configure rows ({value, was, remove, replace}; `was` is the
    value the row was showing, blank for a new row) into the new list and
    what happens to the items on each old value.

    A row whose text changed is a rename: its items follow it. A removed
    (or emptied) row whose value items still hold needs a replacement from
    the values that remain, unless no values remain at all (the column
    becomes free text / no status, and items keep what they have)."""
    values, canonical = [], {}
    for r in rows:
        v = r["value"]
        if r["remove"] or not v or v.lower() in canonical:
            continue
        canonical[v.lower()] = v
        values.append(v)
    relabel: dict[str, tuple[str, str]] = {}
    problems: list[str] = []
    for r in rows:
        old = r["was"]
        if not old or old in relabel:
            continue
        if not r["remove"] and r["value"]:
            new = canonical[r["value"].lower()]
            if new != old:
                relabel[old] = (new, "rename")
            continue
        if old.lower() in canonical:          # still listed (another row has it)
            if canonical[old.lower()] != old:
                relabel[old] = (canonical[old.lower()], "rename")
            continue
        if not counts.get(old) or not values:
            continue
        target = canonical.get((r.get("replace") or "").strip().lower())
        if target is None:
            problems.append(old)
        else:
            relabel[old] = (target, "replace")
    return ChoicePlan(values, relabel, problems)


def relabel_items(session, module_id: int, column: str, relabel: dict[str, tuple[str, str]]) -> int:
    """Move every item of the inventory from an old value to its new one.
    Not a status change: no received or used-up date is stamped (see
    apply_status); only the label changes. Returns how many moved."""
    if not relabel:
        return 0
    col = getattr(InventoryItem, column)
    moved = 0
    for item in session.scalars(select(InventoryItem).where(
            InventoryItem.module_id_fk == module_id, col.in_(list(relabel)))):
        setattr(item, column, relabel[getattr(item, column)][0])
        moved += 1
    return moved


def attention_items(session, days: int = 30, limit: int = 12) -> list[dict]:
    """Reagents and antibodies to restock: expiring within `days` (or
    already expired) or marked low, and not already used up."""
    today = date.today()
    modules = {m.id: m for m in list_modules(session) if m.kind in RESTOCK_KINDS}
    if not modules:
        return []
    rows = session.scalars(select(InventoryItem).where(
        InventoryItem.module_id_fk.in_(list(modules)),
        (InventoryItem.expires_on <= today + timedelta(days=days)) | (func.lower(InventoryItem.status) == "low"),
    ).order_by(InventoryItem.expires_on.is_(None), InventoryItem.expires_on, InventoryItem.name))
    out = []
    for item in rows:
        if (item.status or "").lower() in TERMINAL_STATUSES:
            continue
        module = modules[item.module_id_fk]
        out.append({"id": item.id, "key": module.key, "module": module.label, "name": item.name or f"#{item.number}",
                    "number": item.number, "status": item.status, "expires_on": item.expires_on,
                    "expiry": expiry_state(item, today), "low": (item.status or "").lower() == "low"})
    return out[:limit]


def open_order_count(session) -> int:
    """Orders still waiting, by each orders inventory's own open statuses."""
    total = 0
    for module in (m for m in list_modules(session) if m.kind == "orders"):
        statuses = view(module).open_statuses
        if statuses:
            total += session.scalar(select(func.count(InventoryItem.id)).where(
                InventoryItem.module_id_fk == module.id, InventoryItem.status.in_(statuses))) or 0
    return total


def stored_at_field(mv) -> dict | None:
    """The inventory's "Stored at" column (−80 °C, LN₂…), if it has one."""
    return next((f for f in mv.fields if f["key"] == "storage_temp"
                 or f["label"].strip().lower() == "stored at"), None)


def follow_box(session, item: InventoryItem) -> None:
    """A tube put in a box whose place is known takes it as its "Stored at"."""
    rack = item.rack if item.rack is not None and item.rack.id == item.rack_id_fk else (
        session.get(InventoryRack, item.rack_id_fk) if item.rack_id_fk else None)
    if rack is None or not rack.stored_at:
        return
    module = session.get(InventoryModule, item.module_id_fk)
    field = module and stored_at_field(view(module))
    if field is None:
        return
    attrs = item.attrs_dict
    if attrs.get(field["key"]) != rack.stored_at:
        attrs[field["key"]] = rack.stored_at
        item.attrs = json.dumps(attrs)


# ---------------------------------------------------------------------------
# Primers: length, GC and Tm worked out from the sequence
# ---------------------------------------------------------------------------

# Nearest-neighbour stacks (SantaLucia 1998): ΔH kcal/mol, ΔS cal/(K·mol).
_NN = {"AA": (-7.9, -22.2), "AT": (-7.2, -20.4), "TA": (-7.2, -21.3), "CA": (-8.5, -22.7),
       "GT": (-8.4, -22.4), "CT": (-7.8, -21.0), "GA": (-8.2, -22.2), "CG": (-10.6, -27.2),
       "GC": (-9.8, -24.4), "GG": (-8.0, -19.9)}
_PAIR = str.maketrans("ACGT", "TGCA")
PRIMER_SALT_M, PRIMER_CONC_M = 0.05, 250e-9   # 50 mM Na⁺, 250 nM oligo (as most calculators)
PRIMER_DERIVED = ("length", "gc", "tm")


def clean_sequence(raw) -> str:
    """"5'-ACG tgc-3'" → "ACGTGC": bases only, upper case."""
    text = str(raw or "").upper().replace("5'", "").replace("3'", "")
    return "".join(ch for ch in text if ch.isalpha())


def primer_tm(seq: str) -> float | None:
    """Melting temperature in °C by nearest neighbours, or None for a
    sequence with other than A, C, G, T or shorter than 8 bases."""
    if len(seq) < 8 or set(seq) - set("ACGT"):
        return None
    dh = ds = 0.0
    for end in (seq[0], seq[-1]):                       # initiation, by terminal pair
        h, e = (0.1, -2.8) if end in "GC" else (2.3, 4.1)
        dh, ds = dh + h, ds + e
    for i in range(len(seq) - 1):
        step = seq[i:i + 2]
        h, e = _NN.get(step) or _NN[step.translate(_PAIR)[::-1]]
        dh, ds = dh + h, ds + e
    self_comp = seq == seq.translate(_PAIR)[::-1]
    if self_comp:
        ds += -1.4
    ds += 0.368 * (len(seq) - 1) * math.log(PRIMER_SALT_M)
    # The primer is in excess over its template in a PCR, so its own
    # concentration counts (as Biopython's Tm_NN with no second strand).
    ct = PRIMER_CONC_M
    return dh * 1000 / (ds + 1.987 * math.log(ct)) - 273.15


def primer_numbers(raw) -> dict[str, str]:
    """{"length": "20", "gc": "55.0", "tm": "58.4"} for a primer sequence
    (Tm left out when it can't be worked out); {} with no sequence."""
    seq = clean_sequence(raw)
    if not seq:
        return {}
    gc = sum(seq.count(b) for b in "GCS") / len(seq) * 100
    out = {"length": str(len(seq)), "gc": f"{gc:.1f}"}
    tm = primer_tm(seq)
    if tm is not None:
        out["tm"] = f"{tm:.1f}"
    return out


def derive_primer(session, item: InventoryItem) -> None:
    """A primer's Length, GC % and Tm follow its sequence."""
    module = session.get(InventoryModule, item.module_id_fk)
    if module is None or module.kind != "primers":
        return
    keys = {f["key"] for f in view(module).fields}
    if "sequence" not in keys:
        return
    attrs = item.attrs_dict
    numbers = primer_numbers(attrs.get("sequence"))
    changed = False
    for key in PRIMER_DERIVED:
        if key in keys and attrs.get(key, "") != numbers.get(key, "") and (numbers or attrs.get("sequence") == ""):
            attrs[key] = numbers.get(key, "")
            changed = True
    if changed:
        item.attrs = json.dumps(attrs)


@event.listens_for(Session, "before_flush", insert=True)
def _items_follow_their_box(session, _context, _instances) -> None:
    """Whichever way an item changed box (dialog, sheet, grid drag, Move,
    Add many, import), its "Stored at" follows; whichever way a primer's
    sequence changed, so do its length, GC and Tm. Runs before the audit
    listener, so undoing the change puts the old values back too."""
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, InventoryItem):
            continue
        new = obj in session.new
        if obj.rack_id_fk and (new or get_history(obj, "rack_id_fk").has_changes()):
            follow_box(session, obj)
        if new or get_history(obj, "attrs").has_changes():
            derive_primer(session, obj)


def apply_position(session, item: InventoryItem, rack_raw, position_raw) -> str | None:
    """Place an item from a typed rack + position ("D7" under that box's
    naming scheme); an error message instead of a guess."""
    rack_raw = str(rack_raw or "").strip()
    position_raw = str(position_raw or "").strip()
    if not rack_raw:
        item.rack_id_fk = item.rack_row = item.rack_col = None
        return None
    rack = session.get(InventoryRack, int(rack_raw)) if rack_raw.isdigit() else None
    if rack is None or rack.module_id_fk != item.module_id_fk:
        return "That box is not part of this inventory."
    if not position_raw:
        # Into a box with no position given: the next free one, as Add many
        # and Move do (a delivery otherwise sat "unplaced" in its box). One
        # already in this box whose position is cleared on purpose stays so.
        cells = free_cells(session, rack, 1) if item.rack_id_fk != rack.id else []
        item.rack_id_fk, (item.rack_row, item.rack_col) = rack.id, (cells[0] if cells else (None, None))
        return None
    cell = positions.parse(position_raw, rack.naming, rack.rows, rack.cols)
    if cell is None:
        return (f"“{position_raw}” is not a position in {rack.name} "
                f"({positions.label(1, 1, rack.naming, rack.cols)}–{positions.label(rack.rows, rack.cols, rack.naming, rack.cols)}).")
    holder = session.scalar(select(InventoryItem).where(
        InventoryItem.rack_id_fk == rack.id, InventoryItem.rack_row == cell[0],
        InventoryItem.rack_col == cell[1], InventoryItem.id != (item.id or 0)))
    if holder is not None:
        return f"{rack.name} · {position_raw} already holds {holder.name or '#' + str(holder.number)}. Drag on the grid to swap."
    item.rack_id_fk, (item.rack_row, item.rack_col) = rack.id, cell
    return None


# ---------------------------------------------------------------------------
# Columns that name a plasmid (field type "plasmid")
# ---------------------------------------------------------------------------

def plasmid_fields(mv) -> list[dict]:
    return [f for f in mv.fields if f["type"] == "plasmid"]


def resolve_plasmid(session, raw: str):
    """The plasmid a typed value names, or None: its number ("42", "#42",
    "Plasmid 42", or "42 · pAAV-…" picked from the list), else its exact
    name, ignoring case."""
    from .models import PlasmidRecord
    import re

    text = (raw or "").strip()
    if not text:
        return None
    number = re.match(r"^(?:plasmid\s*)?#?\s*(\d+)\b", text, re.I)
    if number:
        found = session.scalar(select(PlasmidRecord).where(PlasmidRecord.plasmid_id == int(number.group(1))))
        if found is not None:
            return found
    return session.scalar(select(PlasmidRecord).where(func.lower(PlasmidRecord.name) == text.lower()).limit(1))


def plasmid_links(session, values) -> dict[str, dict]:
    """{stored value: {"row_id", "number", "name"}} for the values that are
    a plasmid's number, to link the sheet's cells to the plasmid."""
    from .models import PlasmidRecord

    numbers = {int(v) for v in values if str(v).strip().isdigit()}
    if not numbers:
        return {}
    return {str(p.plasmid_id): {"row_id": p.id, "number": p.plasmid_id, "name": p.name or ""}
            for p in session.scalars(select(PlasmidRecord).where(PlasmidRecord.plasmid_id.in_(numbers)))}


def made_from_plasmid(session, plasmid_number: int) -> list[dict]:
    """The records (a virus, say) that name this plasmid in a plasmid column,
    in the inventories this person can see: for the plasmid's own page."""
    out = []
    for module in list_modules(session):
        if module.kind == "primers":   # primers bind it; the plasmid's Primers card lists them
            continue
        mv = view(module)
        keys = [f["key"] for f in plasmid_fields(mv)]
        if not keys:
            continue
        for item in session.scalars(select(InventoryItem).where(
                InventoryItem.module_id_fk == module.id,
                InventoryItem.attrs.like(f'%"{plasmid_number}"%')).order_by(InventoryItem.number.desc())):
            attrs = item.attrs_dict
            if any(str(attrs.get(k, "")).strip() == str(plasmid_number) for k in keys):
                out.append({"module": module, "item": item, "noun": mv.item_noun,
                            "available": is_available(mv, item.status)})
    return out


# ---------------------------------------------------------------------------
# Boot: seeding and moving the old samples / orders tables in
# ---------------------------------------------------------------------------


def seed_modules() -> list[str]:
    """Create samples, orders, reagents and antibodies once. Remembered in
    app_settings, so an inventory a lab deletes does not come back."""
    created = []
    with SessionLocal() as session:
        if get_setting(session, "inventory_seeded"):
            return created
        for position, key in enumerate(presets.AUTO_SEED):
            if first_of_kind(session, key) is None:
                module = create_module(session, key, created_by="system")
                module.position = 300 + position
                created.append(key)
        set_setting(session, "inventory_seeded", "1")
        session.commit()
    return created


def migrate_legacy() -> int:
    """Copy rows from the old fixed `samples` and `orders` tables into the
    Samples and Orders inventories, once. The old tables are left in place
    (untouched) as a record."""
    from .models import Order, SampleRecord

    moved = 0
    with SessionLocal() as session:
        if get_setting(session, "legacy_inventory_migrated"):
            return 0
        samples = first_of_kind(session, "samples")
        orders = first_of_kind(session, "orders")
        if samples is not None:
            n = next_number(session, samples.id)
            for s in session.scalars(select(SampleRecord).order_by(SampleRecord.id)):
                attrs = {"source": {"kind": s.source_kind or "", "ref": s.source_ref or ""},
                         "collected_on": s.collection_date.isoformat() if s.collection_date else "",
                         "amount": s.amount or ""}
                session.add(InventoryItem(
                    module_id_fk=samples.id, number=n, name=s.sample_id, category=s.sample_type or "",
                    status="available", owner=s.owner or "", location_note=s.storage_location or "",
                    attrs=json.dumps(attrs), notes=s.notes or "", created_at=s.created_at))
                n += 1
                moved += 1
        if orders is not None:
            n = next_number(session, orders.id)
            for o in session.scalars(select(Order).order_by(Order.id)):
                session.add(InventoryItem(
                    module_id_fk=orders.id, number=n, name=o.item_name, status=o.status or "requested",
                    owner=o.requester_name or "", vendor=o.vendor_name or "", catalog_number=o.catalog_number or "",
                    quantity=o.quantity or "", notes=o.notes or "", created_at=o.created_at))
                n += 1
                moved += 1
        set_setting(session, "legacy_inventory_migrated", "1")
        session.commit()
    return moved


# ---------------------------------------------------------------------------
# Values typed before
# ---------------------------------------------------------------------------


# Built-in columns whose earlier values are offered again as you type.
REMEMBERED = ("name", "category", "vendor", "catalog_number", "unit", "quantity", "location_note")
# Kinds of fields whose values are worth offering again.
REMEMBERED_FIELD_TYPES = ("text", "number")
# What picking an earlier name or catalogue number may fill in.
FILLED = ("name", "category", "vendor", "catalog_number", "unit", "quantity")
# What another inventory's entries lend: the thing itself, not how it is filed.
SHARED_COLUMNS = ("name", "vendor", "catalog_number", "unit", "quantity")
MAX_SUGGESTIONS = 300
MAX_FILLS = 600


def _shown(mv: ModuleView, column: str) -> bool:
    need = {"vendor": "supplier", "catalog_number": "supplier", "unit": "quantity", "quantity": "quantity"}
    if column == "name":
        return mv.row.kind != "samples"   # sample IDs are one of a kind
    if column == "category":
        return not mv.categories          # a fixed list is a select already
    if column == "location_note":
        return not mv.has("storage")
    return mv.has(need[column]) if column in need else True


def remembered(session, mv: ModuleView, items: list[InventoryItem]) -> dict:
    """What was typed before, so it can be picked instead of typed again.

    "suggest": for each text column, the values used before, newest first.
    "fill": an earlier entry's details by its name and by its catalogue
    number (lower-cased), so choosing one fills in the rest.

    Besides this inventory's own entries, the lab's other inventories that
    track a supplier count too: an order suggests what is in Reagents, a
    reagent what was ordered. Only the name, vendor and catalogue number
    carry over from them (quantity and unit are suggested, never filled)."""
    columns = [c for c in REMEMBERED if _shown(mv, c)]
    fields = [f["key"] for f in mv.fields if f["type"] in REMEMBERED_FIELD_TYPES]
    others: list[InventoryItem] = []
    labels = {mv.id: mv.row.label}
    if mv.has("supplier"):
        related = [m for m in list_modules(session) if m.id != mv.id and "supplier" in view(m).settings["features"]]
        labels.update({m.id: m.label for m in related})
        if related:
            others = list(session.scalars(
                select(InventoryItem).where(InventoryItem.module_id_fk.in_([m.id for m in related]))
                .order_by(InventoryItem.id.desc()).limit(2000)))
    own = sorted(items, key=lambda i: i.id, reverse=True)

    suggest: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}

    def offer(column: str, value) -> None:
        value = (value or "").strip() if isinstance(value, str) else ""
        if not value or len(suggest.setdefault(column, [])) >= MAX_SUGGESTIONS:
            return
        folded = value.lower()
        if folded not in seen.setdefault(column, set()):
            seen[column].add(folded)
            suggest[column].append(value)

    fill: dict[str, dict[str, dict]] = {"name": {}, "catalog_number": {}}
    categories = set(mv.categories)
    for item in own + others:
        mine = item.module_id_fk == mv.id
        attrs = item.attrs_dict if mine else {}
        for column in columns:
            if mine or column in SHARED_COLUMNS:
                offer(column, getattr(item, column))
        for key in fields:
            offer(f"attr_{key}", attrs.get(key) if isinstance(attrs.get(key), str) else "")
        values = {c: getattr(item, c) for c in FILLED if getattr(item, c)}
        if not mine:
            # How much is on the shelf says nothing about how much to order.
            values.pop("quantity", None)
            values.pop("unit", None)
            if values.get("category") not in categories:
                values.pop("category", None)
        if mine:
            values.update({f"attr_{f['key']}": attrs[f["key"]] for f in mv.fields
                           if f["type"] in ("text", "number", "select", "url") and attrs.get(f["key"])
                           and isinstance(attrs[f["key"]], str)})
        values["_from"] = f"{mv.item_noun if mine else labels.get(item.module_id_fk, '')} #{item.number}".strip()
        for column in fill:
            folded = (getattr(item, column) or "").strip().lower()
            if folded and folded not in fill[column] and len(fill[column]) < MAX_FILLS:
                fill[column][folded] = values
    return {"suggest": suggest, "fill": fill}
