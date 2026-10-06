"""Service layer for the configurable organism modules.

Everything a module does at runtime that is not a route: reading its JSON
configuration back into usable objects, seeding the built-in presets,
allocating record codes, turning schedule rules into an actual due list, and
counting a census.

The design rule here is that no function knows what species it is looking at.
Anything species-specific arrives as configuration on the module row.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from .models import (
    ModuleField,
    OrgCohort,
    OrgCross,
    OrgDue,
    OrgEvent,
    OrgGenotype,
    OrgHousing,
    OrgLine,
    OrgLocation,
    OrgMeasurement,
    OrgPreservationLot,
    Organism,
    OrganismModule,
)
from .formutil import like_pattern
from .organisms import (
    AUTO_SEED_PRESETS,
    CAPABILITY_BY_KEY,
    FIELD_TYPE_BY_KEY,
    PRESET_BY_KEY,
    PRESETS,
    SERVICE_ANCHORS,
    anchor_allowed,
    default_dead_statuses,
    normalize_capabilities,
    pluralise,
)


# ---------------------------------------------------------------------------
# JSON helpers
#
# Config columns are Text so the schema is identical on SQLite and Postgres.
# These keep the "it might be malformed" handling in one place.
# ---------------------------------------------------------------------------


def load_list(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
        return value if isinstance(value, list) else []
    except (ValueError, TypeError):
        return []


def load_dict(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def dump(value) -> str:
    return json.dumps(value, separators=(",", ":"))


# ---------------------------------------------------------------------------
# The module view
#
# Templates should never call json.loads. `ModuleView` is the decoded, ready
# to render form of a module row, and it is what every route hands to Jinja.
# ---------------------------------------------------------------------------


@dataclass
class ModuleView:
    row: OrganismModule
    capabilities: set[str]
    schedule_rules: list[dict]
    housing_purposes: list[str]
    statuses: list[str]
    sexes: list[str]
    settings: dict

    # --- Passthroughs so templates can treat this as the module ----------
    @property
    def id(self) -> int: return self.row.id
    @property
    def key(self) -> str: return self.row.key
    @property
    def label(self) -> str: return self.row.label
    @property
    def label_plural(self) -> str: return self.row.label_plural or self.row.label
    @property
    def icon(self) -> str: return self.row.icon
    @property
    def blurb(self) -> str: return self.row.blurb
    @property
    def identity_mode(self) -> str: return self.row.identity_mode
    @property
    def age_unit(self) -> str: return self.row.age_unit
    @property
    def organism_noun(self) -> str: return self.row.organism_noun
    @property
    def organism_noun_plural(self) -> str: return self.row.organism_noun_plural
    @property
    def housing_noun(self) -> str: return self.row.housing_noun
    @property
    def housing_noun_plural(self) -> str: return self.row.housing_noun_plural
    @property
    def container_noun(self) -> str: return self.row.container_noun
    @property
    def line_noun(self) -> str: return self.row.line_noun
    @property
    def line_noun_plural(self) -> str: return self.row.line_noun_plural
    @property
    def cohort_noun(self) -> str: return self.row.cohort_noun
    @property
    def cohort_noun_plural(self) -> str: return self.row.cohort_noun_plural
    @property
    def cross_noun(self) -> str: return self.row.cross_noun
    @property
    def cross_noun_plural(self) -> str:
        return self.settings.get("cross_noun_plural") or pluralise(self.row.cross_noun)

    # --- Statuses that end a record (see organisms.END_STATUS_WORDS) ------
    @property
    def dead_statuses(self) -> list[str]:
        configured = self.settings.get("dead_statuses")
        if isinstance(configured, list):
            return [str(s) for s in configured]
        return default_dead_statuses(self.statuses)

    def is_dead_status(self, status: str | None) -> bool:
        value = (status or "").strip().lower()
        return bool(value) and value in {s.strip().lower() for s in self.dead_statuses}

    def is_alive(self, organism) -> bool:
        return organism.death_on is None and not self.is_dead_status(organism.status)

    def has(self, *keys: str) -> bool:
        """True when every named capability is enabled."""
        return all(k in self.capabilities for k in keys)

    def any_of(self, *keys: str) -> bool:
        return any(k in self.capabilities for k in keys)

    @property
    def tracks_individuals(self) -> bool:
        return self.identity_mode in ("individual", "hybrid")

    @property
    def tracks_groups(self) -> bool:
        return self.identity_mode in ("group", "hybrid")

    def rule(self, key: str) -> dict | None:
        return next((r for r in self.schedule_rules if r.get("key") == key), None)


def view(module: OrganismModule) -> ModuleView:
    return ModuleView(
        row=module,
        capabilities=set(load_list(module.capabilities)),
        schedule_rules=load_list(module.schedule_rules),
        housing_purposes=load_list(module.housing_purposes),
        statuses=load_list(module.statuses),
        sexes=load_list(module.sexes),
        settings=load_dict(module.settings),
    )


def list_modules(session, include_disabled: bool = False, everyone: bool = False) -> list[OrganismModule]:
    """In a request: the lab's databases and the signed-in person's own
    (app/lab.py). `everyone`, or outside a request: every database."""
    stmt = select(OrganismModule).order_by(OrganismModule.position, OrganismModule.label)
    if not include_disabled:
        stmt = stmt.where(OrganismModule.enabled.is_(True))
    modules = list(session.scalars(stmt).all())
    from .lab import visible_list
    return modules if everyone else visible_list(modules)


def get_module(session, key: str) -> OrganismModule | None:
    """By its address, or one it had before a rename (app/database_keys.py)."""
    module = session.scalar(select(OrganismModule).where(OrganismModule.key == key))
    if module is None and key:
        from .database_keys import resolve
        module = resolve(session, "organisms", key)
    return module


# ---------------------------------------------------------------------------
# Creating modules
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")
    return slug or "organism"


def _key_taken(session, key: str) -> bool:
    # Reserved words, live keys and old ones, of organisms and of fly and worm
    # databases (they share /organisms/<key> through its redirect).
    from .database_keys import taken
    return taken(session, "organisms", key)


def unique_key(session, base: str) -> str:
    key = slugify(base)
    if not _key_taken(session, key):
        return key
    for n in range(2, 60):
        candidate = f"{key}_{n}"
        if not _key_taken(session, candidate):
            return candidate
    return f"{key}_{int(datetime.utcnow().timestamp())}"


def create_module(session, spec: dict, created_by: str = "") -> OrganismModule:
    """Create a module from a plain dict.

    `spec` uses the same names as the preset dataclass, so the builder form
    and the presets go through one code path.
    """
    capabilities = normalize_capabilities(spec.get("capabilities") or [])
    identity = spec.get("identity_mode") or "hybrid"
    # Keep identity_mode and the capability flags consistent — they are two
    # views of the same decision and it is easy to set only one.
    if identity == "individual":
        capabilities = [c for c in capabilities if c != "group_counts"]
        if "individuals" not in capabilities:
            capabilities.append("individuals")
    elif identity == "group":
        capabilities = [c for c in capabilities if c != "individuals"]
        if "group_counts" not in capabilities:
            capabilities.append("group_counts")
    else:
        for needed in ("individuals", "group_counts"):
            if needed not in capabilities:
                capabilities.append(needed)
    capabilities = normalize_capabilities(capabilities)

    label = (spec.get("label") or "Organism").strip()
    module = OrganismModule(
        key=spec.get("key") or unique_key(session, label),
        label=label,
        label_plural=(spec.get("label_plural") or label).strip(),
        icon=spec.get("icon") or "paw",
        blurb=spec.get("blurb") or "",
        organism_noun=spec.get("organism_noun") or "animal",
        organism_noun_plural=spec.get("organism_noun_plural") or "animals",
        housing_noun=spec.get("housing_noun") or "enclosure",
        housing_noun_plural=spec.get("housing_noun_plural") or "enclosures",
        container_noun=spec.get("container_noun") or "rack",
        line_noun=spec.get("line_noun") or "line",
        line_noun_plural=spec.get("line_noun_plural") or "lines",
        cohort_noun=spec.get("cohort_noun") or "cohort",
        cohort_noun_plural=spec.get("cohort_noun_plural") or "cohorts",
        cross_noun=spec.get("cross_noun") or "cross",
        identity_mode=identity,
        age_unit=spec.get("age_unit") or "days",
        capabilities=dump(capabilities),
        schedule_rules=dump(spec.get("schedule_rules") or []),
        housing_purposes=dump(spec.get("housing_purposes") or []),
        statuses=dump(spec.get("statuses") or ["alive", "removed"]),
        sexes=dump(spec.get("sexes") or ["mixed", "female", "male", "unknown"]),
        settings=dump(spec.get("settings") or {}),
        preset_key=spec.get("preset_key") or "",
        position=int(spec.get("position") or 100),
        created_by=created_by,
    )
    session.add(module)
    session.flush()

    for position, field_spec in enumerate(spec.get("fields") or []):
        add_field(session, module, field_spec, position=position * 10)
    session.flush()
    return module


def add_field(session, module: OrganismModule, spec: dict, position: int | None = None) -> ModuleField | None:
    key = slugify(spec.get("key") or spec.get("label") or "")
    if not key or spec.get("field_type") not in FIELD_TYPE_BY_KEY:
        return None
    existing = session.scalar(
        select(ModuleField).where(
            ModuleField.module_id_fk == module.id,
            ModuleField.entity == (spec.get("entity") or "organism"),
            ModuleField.key == key,
        )
    )
    if existing is not None:
        return existing
    if position is None:
        highest = session.scalar(
            select(func.max(ModuleField.position)).where(ModuleField.module_id_fk == module.id)
        ) or 0
        position = highest + 10
    row = ModuleField(
        module_id_fk=module.id,
        entity=spec.get("entity") or "organism",
        key=key,
        label=(spec.get("label") or key).strip(),
        field_type=spec["field_type"],
        options=dump(spec.get("options") or []),
        default_value=spec.get("default_value") or "",
        help_text=spec.get("help_text") or "",
        required=bool(spec.get("required")),
        show_in_table=bool(spec.get("show_in_table")),
        position=position,
    )
    session.add(row)
    return row


def preset_spec(preset_key: str) -> dict:
    """A preset as the dict `create_module` expects."""
    preset = PRESET_BY_KEY.get(preset_key)
    if preset is None:
        return {}
    return {
        "key": preset.key,
        "preset_key": preset.key,
        "label": preset.label,
        "label_plural": preset.label_plural,
        "icon": preset.icon,
        "blurb": preset.blurb,
        "organism_noun": preset.organism_noun,
        "organism_noun_plural": preset.organism_noun_plural,
        "housing_noun": preset.housing_noun,
        "housing_noun_plural": preset.housing_noun_plural,
        "container_noun": preset.container_noun,
        "line_noun": preset.line_noun,
        "line_noun_plural": preset.line_noun_plural,
        "cohort_noun": preset.cohort_noun,
        "cohort_noun_plural": preset.cohort_noun_plural,
        "cross_noun": preset.cross_noun,
        "identity_mode": preset.identity_mode,
        "age_unit": preset.age_unit,
        "capabilities": list(preset.capabilities),
        "schedule_rules": [r.as_dict() for r in preset.schedule_rules],
        "housing_purposes": list(preset.housing_purposes),
        "statuses": list(preset.statuses),
        "sexes": list(preset.sexes),
        "fields": [dict(f) for f in preset.fields],
        "settings": dict(preset.settings),
    }


def seed_builtin_modules(session) -> list[str]:
    """Create the presets that have no hand-written module in this app.

    Mouse and zebrafish are deliberately excluded: they already ship as
    dedicated modules with live data, and seeding a second, empty copy would
    just be confusing. Both remain available in the builder.
    """
    created = []
    for position, preset_key in enumerate(AUTO_SEED_PRESETS):
        if get_module(session, preset_key) is not None:
            continue
        spec = preset_spec(preset_key)
        if not spec:
            continue
        spec["position"] = 200 + position
        create_module(session, spec, created_by="system")
        created.append(preset_key)
    return created


def repair_icon_names(session) -> int:
    """Rewrite icon names the sprite no longer has, in module rows and their
    schedule rules. Returns how many rows changed; idempotent."""
    from .icons import known, resolve

    names = known()
    if not names:
        return 0
    changed = 0
    for module in session.scalars(select(OrganismModule)):
        dirty = False
        if module.icon not in names:
            module.icon = resolve(module.icon)
            dirty = True
        rules = load_list(module.schedule_rules)
        for rule in rules:
            if rule.get("icon") and rule["icon"] not in names:
                rule["icon"] = resolve(rule["icon"])
                dirty = True
        if dirty:
            module.schedule_rules = dump(rules)
            changed += 1
    return changed


def offer_housing_grid(session) -> int:
    """Give the built-in fly and worm modules rack positions for their
    vials and plates. Done once per module (remembered in its settings), so
    a lab that switches positions off in Configure keeps that choice."""
    changed = 0
    for module in session.scalars(select(OrganismModule)):
        if module.preset_key not in ("drosophila", "c_elegans"):
            continue
        settings = load_dict(module.settings)
        if settings.get("housing_grid_offered"):
            continue
        capabilities = load_list(module.capabilities)
        if "housing" in capabilities and "housing_grid" not in capabilities:
            capabilities.insert(capabilities.index("housing") + 1, "housing_grid")
            module.capabilities = dump(capabilities)
        settings["housing_grid_offered"] = True
        module.settings = dump(settings)
        changed += 1
    return changed


# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------


def fields_for(session, module_id: int, entity: str) -> list[ModuleField]:
    return list(
        session.scalars(
            select(ModuleField)
            .where(ModuleField.module_id_fk == module_id, ModuleField.entity == entity)
            .order_by(ModuleField.position, ModuleField.id)
        ).all()
    )


def fields_by_entity(session, module_id: int) -> dict[str, list[ModuleField]]:
    rows = session.scalars(
        select(ModuleField)
        .where(ModuleField.module_id_fk == module_id)
        .order_by(ModuleField.position, ModuleField.id)
    ).all()
    grouped: dict[str, list[ModuleField]] = {}
    for row in rows:
        grouped.setdefault(row.entity, []).append(row)
    return grouped


def field_options(row: ModuleField) -> list[str]:
    return [str(v) for v in load_list(row.options)]


def read_attrs(form, field_rows: list[ModuleField], existing: dict | None = None) -> dict:
    """Pull this entity's custom field values out of a submitted form.

    Inputs are named `attr_<key>` so they cannot collide with the fixed
    columns. Only keys the module actually declares are accepted.
    """
    attrs = dict(existing or {})
    for row in field_rows:
        name = f"attr_{row.key}"
        if row.field_type == "checkbox":
            attrs[row.key] = bool(form.get(name))
            continue
        if name not in form:
            continue
        raw = (form.get(name) or "").strip()
        if row.field_type == "number":
            if raw == "":
                attrs[row.key] = None
            else:
                try:
                    attrs[row.key] = float(raw) if "." in raw else int(raw)
                except ValueError:
                    attrs[row.key] = raw
        else:
            attrs[row.key] = raw
    return attrs


def read_attrs_checked(form, field_rows: list[ModuleField], existing: dict | None = None,
                       creating: bool = False, full: bool = False) -> tuple[dict, list[str]]:
    """Like read_attrs, but for a save that should be refused when wrong.

    * Only inputs present in the form are written, so an inline edit of one
      cell (or a dialog that did not render a field) leaves the rest alone.
      Checkboxes, which submit nothing when unticked, are only read from a
      full form (`full`: the dialog, which renders every field).
    * On a new record, a field the form did not send takes its default.
    * Number fields must hold a number; required fields must not be empty
      when the form carried them (or when creating).
    """
    attrs = dict(existing or {})
    errors: list[str] = []
    for row in field_rows:
        name = f"attr_{row.key}"
        if row.field_type == "checkbox":
            if full or name in form:
                attrs[row.key] = bool(form.get(name))
            elif creating and row.default_value:
                attrs[row.key] = row.default_value.strip().lower() in ("1", "yes", "true", "on", "y")
            continue
        if name in form and f"{name}_was" in form and (form.get(name) or "").strip() == (form.get(f"{name}_was") or "").strip():
            continue        # a sheet cell left as it was: a colleague may have changed it since
        if name in form:
            raw = (form.get(name) or "").strip()
        elif creating:
            raw = (row.default_value or "").strip()
        else:
            continue
        if row.field_type == "number":
            if raw == "":
                attrs[row.key] = None
            else:
                try:
                    number = float(raw)
                    attrs[row.key] = int(number) if number.is_integer() and "." not in raw else number
                except ValueError:
                    errors.append(f"{row.label} must be a number (got “{raw}”).")
                    continue
        else:
            attrs[row.key] = raw
        if row.required and raw == "":
            errors.append(f"{row.label} is required.")
    return attrs, errors


def display_attr(row: ModuleField, attrs: dict):
    value = attrs.get(row.key)
    if row.field_type == "checkbox":
        return "Yes" if value else ""
    if value is None:
        return ""
    return value


# ---------------------------------------------------------------------------
# Codes
#
# Auto-allocated human-readable IDs, per module and per entity, e.g. the
# third vial in the Drosophila module becomes DROSOPHILA-V003. Labs that
# prefer their own scheme can always type over it.
# ---------------------------------------------------------------------------


ENTITY_MODELS = {
    "organism": Organism,
    "housing": OrgHousing,
    "line": OrgLine,
    "cohort": OrgCohort,
    "cross": OrgCross,
}
ENTITY_LETTER = {"organism": "A", "housing": "H", "line": "L", "cohort": "C", "cross": "X"}


def code_prefix(module: OrganismModule, entity: str) -> str:
    # The first key's stem: a renamed database goes on numbering its codes.
    from sqlalchemy.orm import object_session
    from .database_keys import first_key
    session = object_session(module)
    key = first_key(session, "organisms", module) if session is not None else module.key
    stem = re.sub(r"[^A-Z0-9]", "", key.upper())[:4] or "ORG"
    return f"{stem}-{ENTITY_LETTER.get(entity, 'A')}"


def next_code(session, module: OrganismModule, entity: str) -> str:
    model = ENTITY_MODELS.get(entity)
    if model is None:
        return ""
    prefix = code_prefix(module, entity)
    rows = session.scalars(
        select(model.code).where(
            model.module_id_fk == module.id, model.code.like(f"{prefix}%")
        )
    ).all()
    highest = 0
    for code in rows:
        match = re.search(r"(\d+)$", code or "")
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}{highest + 1:03d}"


def code_sequence(first: str, count: int) -> list[str]:
    """`count` consecutive codes starting at `first`: M-009 → M-009, M-010,
    M-011 (zero padding kept). A code with no number at the end gets one:
    TANK → TANK-01, TANK-02."""
    first = (first or "").strip()
    if count <= 1 or not first:
        return [first] * max(count, 1)
    match = re.search(r"(\d+)$", first)
    if match is None:
        width = max(2, len(str(count)))
        return [f"{first}-{n:0{width}d}" for n in range(1, count + 1)]
    stem, digits = first[:match.start()], match.group(1)
    start = int(digits)
    return [f"{stem}{start + n:0{len(digits)}d}" for n in range(count)]


def codes_in_use(session, model, module_id: int, codes) -> list[str]:
    """Which of `codes` already name a record of this kind in the module."""
    wanted = [c for c in codes if c]
    if not wanted:
        return []
    taken = set(session.scalars(select(model.code).where(
        model.module_id_fk == module_id, model.code.in_(wanted))).all())
    return [c for c in wanted if c in taken]


# ---------------------------------------------------------------------------
# Genotyping
#
# A call is an OrgGenotype row against a subject (usually an animal record,
# sometimes a housing unit when a whole tank or vial was typed together).
# The subject's own `genotype` text is left as the person typed it; the
# latest call is shown beside it.
# ---------------------------------------------------------------------------

ZYGOSITIES = ("het", "hom", "wt", "hemi", "carrier", "trans-het", "unknown")


def organism_label(o: Organism) -> str:
    return o.code or f"#{o.id}"


def latest_calls(session, module_id: int, kind: str = "organism") -> dict[int, OrgGenotype]:
    """The most recent call per subject of one kind."""
    rows = session.scalars(select(OrgGenotype).where(
        OrgGenotype.module_id_fk == module_id, OrgGenotype.subject_kind == kind)
        .order_by(OrgGenotype.called_on, OrgGenotype.id)).all()
    latest: dict[int, OrgGenotype] = {}
    for row in rows:
        latest[row.subject_id] = row
    return latest


def genotype_assays(session, module_id: int) -> list[str]:
    """Assays used before in this module, most recent first."""
    seen: list[str] = []
    for assay in session.scalars(select(OrgGenotype.assay).where(
            OrgGenotype.module_id_fk == module_id, OrgGenotype.assay != "")
            .order_by(OrgGenotype.id.desc())):
        if assay not in seen:
            seen.append(assay)
    return seen


def resolve_genotype_subject(session, module: OrganismModule, text: str):
    """(kind, row) for what a person typed as the subject of a call: an
    animal's code, "#<id>" for an uncoded group, or a housing unit's code.
    (None, None) when nothing in this module answers to it."""
    text = (text or "").strip()
    if not text:
        return None, None
    if re.fullmatch(r"#\d+", text):
        row = session.get(Organism, int(text[1:]))
        if row is not None and row.module_id_fk == module.id:
            return "organism", row
        return None, None
    matches = session.scalars(select(Organism).where(
        Organism.module_id_fk == module.id,
        func.lower(Organism.code) == text.lower()).order_by(Organism.id.desc())).all()
    if matches:
        mv = view(module)
        alive = [o for o in matches if mv.is_alive(o)]
        return "organism", (alive or matches)[0]
    unit = session.scalar(select(OrgHousing).where(
        OrgHousing.module_id_fk == module.id,
        func.lower(OrgHousing.code) == text.lower()).order_by(OrgHousing.id.desc()))
    if unit is not None:
        return "housing", unit
    return None, None


def safe_link(raw: str) -> str | None:
    """A gel image link, if it is one: http(s) or a path on this server.
    None for anything else (javascript:, data:, …)."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if re.match(r"^https?://\S+$", raw, re.I) or (raw.startswith("/") and not raw.startswith("//")):
        return raw[:300]
    return None


# ---------------------------------------------------------------------------
# Derived schedule
#
# A rule is (anchor date on a subject) + (offset in days). The offset can
# depend on the subject's rearing temperature, which is how one rule covers
# "flip flies every 14 days at 25 °C but every 28 at 18 °C".
# ---------------------------------------------------------------------------


RULE_SUBJECTS = {
    "cohort": OrgCohort,
    "housing": OrgHousing,
    "line": OrgLine,
    "organism": Organism,
}


def rule_offset(rule: dict, attrs: dict) -> int:
    offsets = rule.get("temp_offsets") or {}
    if offsets:
        temp = attrs.get("temperature_c")
        if temp not in (None, ""):
            key = str(temp).strip()
            if key in offsets:
                return int(offsets[key])
            # Nearest configured temperature, so an odd value still resolves.
            try:
                target = float(key)
                nearest = min(offsets, key=lambda k: abs(float(k) - target))
                return int(offsets[nearest])
            except (TypeError, ValueError):
                pass
    return int(rule.get("offset_days") or 0)


def recompute_due(session, module: OrganismModule) -> int:
    """Materialise outstanding schedule items for one module.

    Open (not-yet-done) rows are rebuilt from the current anchors so that
    editing a birth date moves the due date with it. Completed rows are left
    alone — they are the history of what was actually done. An open row that
    still applies keeps its id (and is updated in place), so a "Done" button
    rendered before some other save still points at the same item.
    """
    mv = view(module)
    open_rows = {
        (row.rule_key, row.subject_kind, row.subject_id): row
        for row in session.scalars(select(OrgDue).where(
            OrgDue.module_id_fk == module.id, OrgDue.done_on.is_(None)))
    }
    wanted: dict[tuple, tuple] = {}
    rules = mv.schedule_rules if mv.has("schedule") else []
    for rule in rules:
        model = RULE_SUBJECTS.get(rule.get("applies_to"))
        anchor = rule.get("anchor")
        if (model is None or not anchor or not hasattr(model, anchor)
                or not anchor_allowed(rule.get("applies_to"), anchor)):
            continue

        stmt = select(model).where(model.module_id_fk == module.id)
        # Don't schedule work for things that are gone.
        if hasattr(model, "active"):
            stmt = stmt.where(model.active.is_(True))
        if hasattr(model, "retired"):
            stmt = stmt.where(model.retired.is_(False))
        if hasattr(model, "death_on"):
            stmt = stmt.where(model.death_on.is_(None))

        for subject in session.scalars(stmt).all():
            start = getattr(subject, anchor, None)
            if start is None:
                continue
            attrs = load_dict(getattr(subject, "attrs", "{}"))
            offset = timedelta(days=rule_offset(rule, attrs))
            due_on = start + offset

            # A rule that has been done before: a one-off is finished; a
            # recurring one counts from the last completion (or from a later
            # "last serviced" date typed in since).
            last_done = session.scalar(
                select(func.max(OrgDue.done_on)).where(
                    OrgDue.module_id_fk == module.id,
                    OrgDue.rule_key == rule["key"],
                    OrgDue.subject_kind == rule["applies_to"],
                    OrgDue.subject_id == subject.id,
                    OrgDue.done_on.is_not(None),
                )
            )
            if last_done is not None:
                if not rule.get("recurring"):
                    continue
                base = max(start, last_done) if anchor in SERVICE_ANCHORS else last_done
                due_on = base + offset
            wanted[(rule["key"], rule["applies_to"], subject.id)] = (
                due_on, getattr(subject, "owner", "") or "")

    created = 0
    for key, (due_on, owner) in wanted.items():
        row = open_rows.pop(key, None)
        if row is None:
            session.add(OrgDue(module_id_fk=module.id, rule_key=key[0], subject_kind=key[1],
                               subject_id=key[2], due_on=due_on, assigned_to=owner))
            created += 1
        else:
            row.due_on, row.assigned_to = due_on, owner
    for row in open_rows.values():
        session.delete(row)
    mark_schedule_fresh(session, module.id)
    return created


# ---------------------------------------------------------------------------
# Schedule freshness
#
# OrgDue rows are the materialised schedule. Every write route recomputes
# them in the same transaction, so the home page can read them as they are
# rather than rebuilding every module's schedule on each load. It only
# recomputes a module when the rows may be stale:
#
#   * something changed one of the module's records (or the module) in a
#     transaction that did not recompute it — an undo, an import, any code
#     path outside organism_routes. A flush listener notices and marks the
#     module dirty when that transaction commits;
#   * or the last recompute was not today (a once-a-day safety net).
#
# The state is one app_settings row per module: the ISO date of the last
# recompute, or "" when dirty.
# ---------------------------------------------------------------------------

SCHEDULE_KEY = "org_schedule_fresh:{}"
_TOUCHED = "org_schedule_touched"


def _put_setting(session, key: str, value: str) -> None:
    """inventory_service.set_setting, but also finding a row added earlier
    in this (unflushed: the app's sessions do not autoflush) transaction,
    so recomputing one module twice before a flush cannot insert it twice."""
    from .inventory_service import set_setting
    from .models import AppSetting
    pending = next((o for o in session.new if isinstance(o, AppSetting) and o.key == key), None)
    if pending is not None:
        pending.value = value
    else:
        set_setting(session, key, value)


def mark_schedule_fresh(session, module_id: int) -> None:
    _put_setting(session, SCHEDULE_KEY.format(module_id), date.today().isoformat())
    session.info.get(_TOUCHED, set()).discard(module_id)


def mark_schedule_dirty(session, module_id: int) -> None:
    _put_setting(session, SCHEDULE_KEY.format(module_id), "")


def schedule_is_fresh(session, module_id: int) -> bool:
    from .inventory_service import get_setting
    return get_setting(session, SCHEDULE_KEY.format(module_id)) == date.today().isoformat()


def _touched_module_id(obj):
    if isinstance(obj, OrganismModule):
        return obj.id
    if isinstance(obj, (Organism, OrgHousing, OrgLine, OrgCohort)):
        return getattr(obj, "module_id_fk", None)
    return None


@event.listens_for(Session, "after_flush")
def _note_schedule_inputs(session, flush_context):
    """Remember which modules' records this transaction wrote, so a commit
    that did not recompute their schedule can mark it stale."""
    touched = None
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        module_id = _touched_module_id(obj)
        if module_id:
            if touched is None:
                touched = session.info.setdefault(_TOUCHED, set())
            touched.add(module_id)


@event.listens_for(Session, "before_commit")
def _flag_stale_schedules(session):
    # Flush first (the app's sessions do not autoflush) so writes still
    # pending at commit are seen too.
    if session.new or session.dirty or session.deleted:
        session.flush()
    if not session.info.get(_TOUCHED):
        return
    touched = session.info.pop(_TOUCHED, set())
    if not touched:
        return
    with session.no_autoflush:
        for module_id in touched:
            if session.get(OrganismModule, module_id) is not None:
                mark_schedule_dirty(session, module_id)


@event.listens_for(Session, "after_soft_rollback")
def _forget_schedule_inputs(session, previous_transaction):
    session.info.pop(_TOUCHED, None)


def due_items(session, module: OrganismModule, horizon_days: int = 14, include_done: bool = False):
    """Open schedule items due within the horizon, plus anything overdue."""
    mv = view(module)
    rules = {r["key"]: r for r in mv.schedule_rules}
    cutoff = date.today() + timedelta(days=horizon_days)

    stmt = select(OrgDue).where(OrgDue.module_id_fk == module.id)
    if not include_done:
        stmt = stmt.where(OrgDue.done_on.is_(None), OrgDue.due_on <= cutoff)
    rows = session.scalars(stmt.order_by(OrgDue.due_on)).all()

    # Resolve each subject's label in one pass per kind.
    wanted: dict[str, set[int]] = {}
    for row in rows:
        wanted.setdefault(row.subject_kind, set()).add(row.subject_id)
    labels: dict[tuple[str, int], str] = {}
    for kind, ids in wanted.items():
        model = RULE_SUBJECTS.get(kind)
        if model is None or not ids:
            continue
        for subject in session.scalars(select(model).where(model.id.in_(ids))).all():
            labels[(kind, subject.id)] = getattr(subject, "code", None) or f"#{subject.id}"

    today = date.today()
    out = []
    for row in rows:
        rule = rules.get(row.rule_key, {})
        out.append({
            "id": row.id,
            "rule_key": row.rule_key,
            "label": rule.get("label", row.rule_key.replace("_", " ").title()),
            "icon": rule.get("icon", "calendar-clock"),
            "subject_kind": row.subject_kind,
            "subject_id": row.subject_id,
            "subject_label": labels.get((row.subject_kind, row.subject_id), f"#{row.subject_id}"),
            "due_on": row.due_on,
            "days": (row.due_on - today).days,
            "overdue": row.due_on < today,
            "assigned_to": row.assigned_to,
            "done_on": row.done_on,
        })
    return out


def complete_due(session, module: OrganismModule, due_id: int, user: str,
                 done_on: date | None = None) -> OrgDue | None:
    """Tick a schedule item off, today or on the day it was really done
    (never later than today)."""
    row = session.scalar(
        select(OrgDue).where(OrgDue.id == due_id, OrgDue.module_id_fk == module.id)
    )
    if row is None or row.done_on is not None:
        return None
    today = date.today()
    done = min(done_on or today, today)
    row.done_on = done
    row.done_by = user

    # Roll a service anchor ("last flipped", "last refreshed") forward so
    # recurring maintenance restarts its clock. Anchors that are facts about
    # the subject — a birth date, the day a unit was set up — are never
    # rewritten: recompute_due counts those rules from the last completion.
    # Nor is an anchor two recurring rules share (feed every 2 days, split
    # every 4, both from "last serviced"): feeding would restart the split's
    # clock, so each of those counts from its own last completion instead.
    mv = view(module)
    rule = mv.rule(row.rule_key) or {}
    model = RULE_SUBJECTS.get(row.subject_kind)
    anchor = rule.get("anchor")
    shared = sum(1 for r in mv.schedule_rules if r.get("recurring") and r.get("anchor") == anchor
                 and r.get("applies_to") == row.subject_kind) > 1
    if (rule.get("recurring") and model is not None and anchor in SERVICE_ANCHORS and not shared
            and anchor_allowed(row.subject_kind, anchor)):
        subject = session.get(model, row.subject_id)
        if subject is not None and hasattr(subject, anchor):
            current = getattr(subject, anchor)
            if current is None or current < done:          # a backdated Done never moves it back
                setattr(subject, anchor, done)

    log_event(session, module, row.subject_kind, row.subject_id, row.rule_key,
              recorded_by=user, notes=f"{rule.get('label', row.rule_key)} completed")
    return row


# ---------------------------------------------------------------------------
# Events and census
# ---------------------------------------------------------------------------


def log_event(session, module: OrganismModule, subject_kind: str, subject_id: int,
              event_type: str, occurred_on: date | None = None, count: int = 0,
              detail: dict | None = None, recorded_by: str = "", notes: str = "") -> OrgEvent:
    row = OrgEvent(
        module_id_fk=module.id,
        subject_kind=subject_kind,
        subject_id=subject_id,
        event_type=event_type,
        occurred_on=occurred_on or date.today(),
        count=count,
        detail=dump(detail or {}),
        recorded_by=recorded_by,
        notes=notes,
    )
    session.add(row)
    return row


def alive_clause(module: OrganismModule | ModuleView):
    """SQL condition for 'this organism is alive': no date of death and no
    status that ends a record."""
    mv = module if isinstance(module, ModuleView) else view(module)
    dead = [s.strip().lower() for s in mv.dead_statuses if s.strip()]
    clause = Organism.death_on.is_(None)
    if dead:
        clause = clause & func.lower(func.coalesce(Organism.status, "")).notin_(dead)
    return clause


def census(session, module: OrganismModule) -> dict:
    """Live counts for the module header.

    `animals` respects identity mode: summing `count` is correct for both an
    individually tracked animal (count=1) and a group of forty flies.
    """
    mid = module.id
    living = alive_clause(module)
    alive = select(func.coalesce(func.sum(Organism.count), 0)).where(
        Organism.module_id_fk == mid, living
    )
    return {
        "animals": session.scalar(alive) or 0,
        "records": session.scalar(
            select(func.count(Organism.id)).where(Organism.module_id_fk == mid, living)
        ) or 0,
        "all_records": session.scalar(
            select(func.count(Organism.id)).where(Organism.module_id_fk == mid)
        ) or 0,
        "housing": session.scalar(
            select(func.count(OrgHousing.id)).where(
                OrgHousing.module_id_fk == mid, OrgHousing.active.is_(True))
        ) or 0,
        "lines": session.scalar(
            select(func.count(OrgLine.id)).where(
                OrgLine.module_id_fk == mid, OrgLine.retired.is_(False))
        ) or 0,
        "cohorts": session.scalar(
            select(func.count(OrgCohort.id)).where(OrgCohort.module_id_fk == mid)
        ) or 0,
        "crosses": session.scalar(
            select(func.count(OrgCross.id)).where(
                OrgCross.module_id_fk == mid, OrgCross.collected_on.is_(None))
        ) or 0,
        "overdue": session.scalar(
            select(func.count(OrgDue.id)).where(
                OrgDue.module_id_fk == mid, OrgDue.done_on.is_(None),
                OrgDue.due_on < date.today())
        ) or 0,
        "frozen_vials": session.scalar(
            select(func.coalesce(func.sum(OrgPreservationLot.vials_remaining), 0))
            .where(OrgPreservationLot.module_id_fk == mid)
        ) or 0,
    }


# ---------------------------------------------------------------------------
# Presentation helpers
# ---------------------------------------------------------------------------


# Where a record keeps its count when age is counted in passages or
# generations: a custom column with one of these keys.
COUNT_KEYS = {"passages": ("passage", "passages", "passage_number", "p"),
              "generations": ("generation", "generations", "f")}


def age_label(module: OrganismModule, start: date | None, end: date | None = None,
              attrs: dict | None = None) -> str:
    """Age in the unit this organism's community actually uses. Counted in
    passages or generations, it is the record's own count ("P12", "F3")
    from its Passage / Generation column; days only when it has none."""
    unit = module.age_unit
    if unit in COUNT_KEYS and attrs:
        count = next((str(attrs[k]).strip() for k in COUNT_KEYS[unit] if str(attrs.get(k) or "").strip()), "")
        if count:
            return f"{'P' if unit == 'passages' else 'F'}{count.lstrip('PpFf')}"
    if start is None:
        return ""
    days = ((end or date.today()) - start).days
    if days < 0:
        return ""
    if unit == "weeks":
        return f"{days // 7}w"
    if unit == "dpf":
        return f"{days} dpf"
    return f"{days}d"


def capability_labels(module: OrganismModule) -> list[str]:
    return [
        CAPABILITY_BY_KEY[key].label
        for key in load_list(module.capabilities)
        if key in CAPABILITY_BY_KEY
    ]


def available_presets() -> list:
    """Presets for the builder, blank one last."""
    from .organisms import STOCK_ENGINE_PRESETS
    return sorted((p for p in PRESETS if p.key not in STOCK_ENGINE_PRESETS),
                  key=lambda p: (p.key == "custom", p.label))


def location_tree(session, module_id: int) -> list[OrgLocation]:
    return list(
        session.scalars(
            select(OrgLocation)
            .where(OrgLocation.module_id_fk == module_id)
            .order_by(OrgLocation.kind, OrgLocation.name)
        ).all()
    )


# What to offer a module that turned on environment logging without saying
# which metrics it cares about. Covers the aquatic and incubator cases.
DEFAULT_ENVIRONMENT_METRICS = [
    {"key": "temperature_c", "label": "Temperature", "unit": "\u00b0C"},
    {"key": "ph", "label": "pH", "unit": ""},
    {"key": "conductivity", "label": "Conductivity", "unit": "\u00b5S"},
    {"key": "ammonia", "label": "Ammonia", "unit": "ppm"},
    {"key": "nitrite", "label": "Nitrite", "unit": "ppm"},
    {"key": "nitrate", "label": "Nitrate", "unit": "ppm"},
]


def measurement_metrics(module_view: ModuleView) -> list[dict]:
    """Environment metrics this module logs, from its settings."""
    metrics = module_view.settings.get("environment_metrics") or []
    clean = [m for m in metrics if isinstance(m, dict) and m.get("key")]
    return clean or list(DEFAULT_ENVIRONMENT_METRICS)


def metrics_to_text(metrics: list[dict]) -> str:
    """Render metrics for the Configure textarea: one `key:label:unit` per line."""
    return "\n".join(
        ":".join([m.get("key", ""), m.get("label", ""), m.get("unit", "")]).rstrip(":")
        for m in metrics
    )


def metrics_from_text(raw: str) -> list[dict]:
    """Parse `key:label:unit` lines back into metric dicts."""
    metrics = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(":")]
        key = slugify(parts[0])
        if not key:
            continue
        metrics.append({
            "key": key,
            "label": parts[1] if len(parts) > 1 and parts[1] else parts[0],
            "unit": parts[2] if len(parts) > 2 else "",
        })
    return metrics


# ---------------------------------------------------------------------------
# Record rules shared by the dialog, the sheet and the batch bar
# ---------------------------------------------------------------------------


def apply_status_rules(mv: ModuleView, organism: Organism, previous_status: str | None,
                       death_given: bool = False) -> None:
    """A status that ends a record stamps today as the date of death when
    none is set; moving back out of one clears the date (unless the same
    save typed a date explicitly). See organisms.END_STATUS_WORDS."""
    if mv.is_dead_status(organism.status):
        if organism.death_on is None:
            organism.death_on = date.today()
    elif mv.is_dead_status(previous_status) and not death_given:
        organism.death_on = None


def line_references(session, line: OrgLine) -> dict[str, int]:
    """What still points at a line, by kind, for refusing a delete."""
    mid, lid = line.module_id_fk, line.id
    counts = {
        "animal record": session.scalar(select(func.count(Organism.id)).where(
            Organism.module_id_fk == mid, Organism.line_id_fk == lid)) or 0,
        "housing unit": session.scalar(select(func.count(OrgHousing.id)).where(
            OrgHousing.module_id_fk == mid, OrgHousing.line_id_fk == lid)) or 0,
        "cohort": session.scalar(select(func.count(OrgCohort.id)).where(
            OrgCohort.module_id_fk == mid, OrgCohort.line_id_fk == lid)) or 0,
        "cross": session.scalar(select(func.count(OrgCross.id)).where(
            OrgCross.module_id_fk == mid,
            (OrgCross.sire_line_id_fk == lid) | (OrgCross.dam_line_id_fk == lid))) or 0,
        "frozen lot": session.scalar(select(func.count(OrgPreservationLot.id)).where(
            OrgPreservationLot.module_id_fk == mid, OrgPreservationLot.line_id_fk == lid)) or 0,
        "derived line": session.scalar(select(func.count(OrgLine.id)).where(
            OrgLine.module_id_fk == mid, OrgLine.parent_line_id_fk == lid)) or 0,
    }
    return {k: v for k, v in counts.items() if v}


def due_subject(session, row: OrgDue):
    model = RULE_SUBJECTS.get(row.subject_kind)
    return session.get(model, row.subject_id) if model is not None else None


# ---------------------------------------------------------------------------
# Search and the home page
# ---------------------------------------------------------------------------


def search(session, q: str, limit: int = 5) -> list[dict]:
    """Animals, housing units and lines across every organism module, in the
    shape the Cmd+K palette expects ({type, id, label, sublabel, url})."""
    from flask import url_for

    q = (q or "").strip()
    if not q:
        return []
    like = like_pattern(q)
    modules = {m.id: m for m in session.scalars(select(OrganismModule))}
    out: list[dict] = []

    def add(module, entity_view, label, sub, row_id):
        out.append({
            "type": "organism", "id": row_id, "label": label,
            "sublabel": " · ".join(filter(None, [module.label] + sub)),
            "url": url_for("organisms.module", key=module.key, view=entity_view),
        })

    for row in session.scalars(select(Organism).where(
            Organism.code.ilike(like, escape="\\") | Organism.genotype.ilike(like, escape="\\") | Organism.notes.ilike(like, escape="\\")
            | Organism.attrs.ilike(like, escape="\\")).order_by(Organism.id.desc()).limit(limit)):
        module = modules.get(row.module_id_fk)
        if module is not None:
            add(module, "animals", row.code or f"{module.organism_noun} #{row.id}",
                [row.status, row.genotype, row.owner], row.id)
    for row in session.scalars(select(OrgHousing).where(
            OrgHousing.code.ilike(like, escape="\\") | OrgHousing.card_id.ilike(like, escape="\\") | OrgHousing.notes.ilike(like, escape="\\")
            | OrgHousing.attrs.ilike(like, escape="\\")).order_by(OrgHousing.id.desc()).limit(limit)):
        module = modules.get(row.module_id_fk)
        if module is not None:
            add(module, "housing", f"{module.housing_noun.capitalize()} {row.code}",
                [row.purpose, row.owner], row.id)
    for row in session.scalars(select(OrgLine).where(
            OrgLine.code.ilike(like, escape="\\") | OrgLine.name.ilike(like, escape="\\") | OrgLine.genotype.ilike(like, escape="\\")
            | OrgLine.attrs.ilike(like, escape="\\")).order_by(OrgLine.id.desc()).limit(limit)):
        module = modules.get(row.module_id_fk)
        if module is not None:
            add(module, "lines", f"{row.code}{' · ' + row.name if row.name else ''}",
                [module.line_noun, row.genotype, row.owner], row.id)
    return out


def home_due(session, horizon_days: int = 2) -> list[dict]:
    """Open schedule items due soon (or overdue) across every enabled
    organism module, for the home page."""
    items = []
    for module in list_modules(session):
        mv = view(module)
        if not mv.has("schedule") or not mv.schedule_rules:
            continue
        # The materialised rows are current unless something changed them
        # behind the schedule's back, or they were last built before today.
        if not schedule_is_fresh(session, module.id):
            recompute_due(session, module)
            session.flush()
        for item in due_items(session, module, horizon_days=horizon_days):
            items.append({
                "id": item["id"], "module": mv.label, "key": mv.key, "icon": item["icon"],
                "title": f"{item['label']} · {item['subject_label']}",
                "due": item["due_on"], "overdue": item["overdue"], "is_today": item["days"] == 0,
            })
    items.sort(key=lambda i: i["due"])
    return items
