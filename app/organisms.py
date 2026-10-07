"""Organism module registry — the definition of a species "database".

The mouse and zebrafish modules in this app are hand-written: dedicated
tables, dedicated routes, dedicated templates. That works, but it means a
lab that needs flies or worms has to wait for someone to write another one.

This module replaces that with a description. An *organism module* is a row
in `organism_modules` that declares:

  * the nouns it uses            — cage / tank / vial / plate, litter / clutch
  * how it counts animals        — individuals, groups, or both
  * which capabilities are on    — crosses, nursery, genotyping, water quality…
  * its schedule rules           — wean at P21, flip every 14 days at 25 °C
  * any extra fields             — arbitrary per-module columns

Everything below is the vocabulary those declarations are written in.
`CAPABILITIES` is the checklist the "new animal database" builder shows;
`PRESETS` are complete, research-grounded starting points for the organisms
labs actually use.

Adding a species should mean filling in this form, not writing a migration.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Capabilities
#
# Each capability switches on a slice of the module: usually a sub-view, and
# sometimes a table that only makes sense when it is enabled. They are grouped
# the way the builder presents them.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Capability:
    key: str
    label: str
    group: str
    blurb: str
    icon: str
    # Capabilities that must also be on for this one to mean anything.
    requires: tuple[str, ...] = ()
    # True for the handful without which a module has nothing to show.
    core: bool = False


CAPABILITIES: tuple[Capability, ...] = (
    # --- Structure ---------------------------------------------------------
    Capability(
        "housing", "Housing units", "Structure",
        "Cages, tanks, vials or plates — the physical thing that carries a label.",
        "cage", core=True,
    ),
    Capability(
        "individuals", "Individual records", "Structure",
        "One row per animal, with its own ID. Needed wherever a single animal "
        "has a history of its own.",
        "user",
    ),
    Capability(
        "group_counts", "Group counts", "Structure",
        "One row per group with a headcount, for animals kept and counted in "
        "bulk rather than tracked singly.",
        "users",
    ),
    Capability(
        "housing_grid", "Rack positions", "Structure",
        "Place housing units at row/column coordinates on a rack, and show the "
        "rack as a grid.",
        "grid", requires=("housing",),
    ),
    Capability(
        "lines", "Line registry", "Structure",
        "Strains, lines or stocks as named genetic entities, separate from the "
        "animals carrying them.",
        "sitemap",
    ),
    # --- Breeding ----------------------------------------------------------
    Capability(
        "crosses", "Crosses", "Breeding",
        "Mating records: who was crossed, when it was set up, what came out.",
        "heart",
    ),
    Capability(
        "cohorts", "Birth cohorts", "Breeding",
        "Litters, clutches or progeny batches — a group born together whose "
        "birth date drives derived dates.",
        "baby",
    ),
    Capability(
        "nursery", "Rearing stage", "Breeding",
        "An intermediate stage between birth and the main system, with its own "
        "location and a graduation step.",
        "seedling", requires=("cohorts",),
    ),
    Capability(
        "pedigree", "Pedigree", "Breeding",
        "Parent links and a navigable lineage tree.",
        "sitemap",
    ),
    Capability(
        "genotyping", "Genotyping", "Breeding",
        "Genotype results per animal or group, with assay, date and gel image.",
        "microscope",
    ),
    # --- Husbandry ---------------------------------------------------------
    Capability(
        "schedule", "Derived schedule", "Husbandry",
        "Due dates computed from a rule rather than typed: wean, flip, "
        "graduate, turn over.",
        "calendar-clock",
    ),
    Capability(
        "environment", "Environment logs", "Husbandry",
        "Recurring measurements against a location — water chemistry, "
        "temperature, humidity.",
        "droplet",
    ),
    Capability(
        "health", "Health observations", "Husbandry",
        "Sick and dead reports, clinical observations and humane endpoints.",
        "stethoscope",
    ),
    Capability(
        "weights", "Body weights", "Husbandry",
        "A weight series per animal, with a trend sparkline.",
        "scale", requires=("individuals",),
    ),
    Capability(
        "cull_log", "Cull log", "Husbandry",
        "Quantity-based culling records, for animals removed in batches.",
        "trash",
    ),
    Capability(
        "preservation", "Cryo inventory", "Husbandry",
        "Frozen stocks with vial counts, storage position and thaw-recovery "
        "verification.",
        "snowflake",
    ),
    # --- Compliance --------------------------------------------------------
    Capability(
        "protocol", "Protocol & census", "Compliance",
        "Assign animals to an approved protocol and produce a running census.",
        "clipboard",
    ),
    Capability(
        "billing", "Cost attribution", "Compliance",
        "Per-diem accounting against the housing unit or the animal.",
        "receipt",
    ),
    Capability(
        "samples", "Sample harvests", "Compliance",
        "Specimens derived from an animal, traceable back to its housing unit "
        "and cross.",
        "vial",
    ),
)

CAPABILITY_BY_KEY = {c.key: c for c in CAPABILITIES}
CAPABILITY_GROUPS = ("Structure", "Breeding", "Husbandry", "Compliance")


def capability_groups() -> list[tuple[str, list[Capability]]]:
    """Capabilities bucketed for the builder UI, in display order."""
    return [
        (group, [c for c in CAPABILITIES if c.group == group])
        for group in CAPABILITY_GROUPS
    ]


def normalize_capabilities(keys) -> list[str]:
    """Drop unknown keys, add implied dependencies, keep registry order."""
    chosen = {k for k in (keys or []) if k in CAPABILITY_BY_KEY}
    # Pull in requirements transitively (the graph is one level deep today).
    for _ in range(3):
        for key in list(chosen):
            chosen.update(CAPABILITY_BY_KEY[key].requires)
    for cap in CAPABILITIES:
        if cap.core:
            chosen.add(cap.key)
    # A module must count animals somehow.
    if not {"individuals", "group_counts"} & chosen:
        chosen.add("group_counts")
    return [c.key for c in CAPABILITIES if c.key in chosen]


# ---------------------------------------------------------------------------
# Custom field types
#
# What a lab can add to an entity in the builder without anyone writing SQL.
# Values land in the entity's `attrs` JSON column.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldType:
    key: str
    label: str
    blurb: str


FIELD_TYPES: tuple[FieldType, ...] = (
    FieldType("text", "Text", "A single line."),
    FieldType("textarea", "Long text", "A paragraph."),
    FieldType("mono", "Code / genotype", "Monospaced, preserves a genotype string verbatim."),
    FieldType("number", "Number", "Numeric, right-aligned, sortable."),
    FieldType("date", "Date", "A date picker."),
    FieldType("select", "Choice", "Pick one of a fixed list you define."),
    FieldType("checkbox", "Yes / no", "A checkbox."),
    FieldType("user", "Lab member", "Pick a registered user."),
    FieldType("line", "Line reference", "Pick one of this module's lines."),
    FieldType("url", "Link", "An external URL, e.g. a stock centre page."),
)

FIELD_TYPE_BY_KEY = {f.key: f for f in FIELD_TYPES}

# Entities a custom field can be attached to.
FIELD_ENTITIES = (
    ("organism", "Animal / group"),
    ("housing", "Housing unit"),
    ("line", "Line"),
    ("cohort", "Cohort"),
    ("cross", "Cross"),
)
FIELD_ENTITY_KEYS = tuple(k for k, _ in FIELD_ENTITIES)


# ---------------------------------------------------------------------------
# Schedule rules
#
# A rule says: take this anchor date, add this many days, and that is when
# something falls due. `temp_offsets` exists because for flies and worms the
# rearing temperature changes the interval — 14 days at 25 °C is 28 at 18 °C.
# ---------------------------------------------------------------------------


@dataclass
class ScheduleRule:
    key: str
    label: str
    applies_to: str          # "cohort" | "housing" | "line" | "organism"
    anchor: str              # attribute on the subject holding the start date
    offset_days: int
    icon: str = "calendar-clock"
    recurring: bool = False  # True = falls due again after being completed
    temp_offsets: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "applies_to": self.applies_to,
            "anchor": self.anchor,
            "offset_days": self.offset_days,
            "icon": self.icon,
            "recurring": self.recurring,
            "temp_offsets": self.temp_offsets,
        }


# Anchors a rule may reference, per subject kind. Used to validate a rule and
# to label the picker in the builder.
SCHEDULE_ANCHORS = {
    "cohort": [("birth_on", "Birth / fertilisation date")],
    "housing": [
        ("established_on", "Date the unit was set up"),
        ("last_serviced_on", "Last flip / transfer"),
    ],
    "line": [
        ("last_refreshed_on", "Last line refresh"),
        ("last_frozen_on", "Last freeze"),
    ],
    "organism": [("birth_on", "Birth date"), ("last_procedure_on", "Last procedure")],
}

# Anchors that record when routine work was last done. Completing a
# recurring item moves these to today so the clock restarts. Every other
# anchor is a fact about the subject (a birth date, the day a unit was set
# up) and is never rewritten by the schedule: a recurring rule counted from
# one of those restarts from the last completion instead (see
# organism_service.recompute_due).
SERVICE_ANCHORS = frozenset({
    "last_serviced_on", "last_refreshed_on", "last_frozen_on", "last_procedure_on",
})


def anchor_allowed(subject: str, anchor: str) -> bool:
    """True when `anchor` is a date the `subject` kind actually has."""
    return any(value == anchor for value, _label in SCHEDULE_ANCHORS.get(subject, ()))


# ---------------------------------------------------------------------------
# Statuses that end a record
#
# The rule, mirroring the mouse sheet: choosing a status that means the
# animal (or group) is dead or gone stamps today as its date of death /
# removal when none is set; moving it back to any other status clears that
# date, so it counts as alive again. Which statuses end a record is a
# module setting ("dead_statuses", editable in Configure); a module that has
# never set it uses those of its statuses that appear in END_STATUS_WORDS.
# ---------------------------------------------------------------------------

END_STATUS_WORDS = frozenset({
    "dead", "died", "found dead", "euthanized", "euthanised", "sac", "sacrificed",
    "culled", "removed", "discarded", "lost", "transferred", "exported", "retired",
})


def default_dead_statuses(statuses) -> list[str]:
    return [s for s in statuses or [] if str(s).strip().lower() in END_STATUS_WORDS]


def pluralise(noun: str) -> str:
    """English plural for the nouns modules use: cross → crosses,
    mating → matings, progeny → progeny, mouse → mice."""
    noun = (noun or "").strip()
    if not noun:
        return noun
    head, _, last = noun.rpartition(" ")
    lower = last.lower()
    irregular = {"mouse": "mice", "child": "children", "fish": "fish", "progeny": "progeny",
                 "sheep": "sheep", "offspring": "offspring", "larva": "larvae",
                 "pupa": "pupae", "embryo": "embryos"}
    if lower in irregular:
        plural = irregular[lower]
    elif lower.endswith(("s", "x", "z", "ch", "sh")):
        plural = last + "es"
    elif lower.endswith("y") and len(lower) > 1 and lower[-2] not in "aeiou":
        plural = last[:-1] + "ies"
    else:
        plural = last + "s"
    return f"{head} {plural}" if head else plural


# Module keys that would shadow a route under /organisms/ (or read as one).
RESERVED_KEYS = frozenset({
    "new", "builtin", "static", "api", "index", "delete", "configure", "settings",
    "search", "edit", "save", "bulk", "admin", "all",
})


# ---------------------------------------------------------------------------
# Presets
#
# Complete modules, grounded in how each community actually works. These are
# what the builder offers as starting points; every field stays editable.
# ---------------------------------------------------------------------------


@dataclass
class Preset:
    key: str
    label: str
    label_plural: str
    icon: str
    blurb: str
    # Nouns. These are what the UI calls things, everywhere.
    organism_noun: str
    organism_noun_plural: str
    housing_noun: str
    housing_noun_plural: str
    container_noun: str
    line_noun: str
    line_noun_plural: str
    cohort_noun: str
    cohort_noun_plural: str
    cross_noun: str
    identity_mode: str            # "individual" | "group" | "hybrid"
    age_unit: str                 # weeks | days | dpf | generations
    capabilities: list[str]
    schedule_rules: list[ScheduleRule] = field(default_factory=list)
    housing_purposes: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    sexes: list[str] = field(default_factory=list)
    fields: list[dict] = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    # Shown in the builder so the choice is explainable.
    notes: str = ""


PRESETS: tuple[Preset, ...] = (

    # --- Drosophila --------------------------------------------------------
    Preset(
        key="drosophila",
        label="Drosophila",
        label_plural="Drosophila",
        icon="fly",
        blurb="Fly stocks in vials, maintained by flipping. No individual animals, "
              "no protocol — genotype search and flip reminders are the job.",
        organism_noun="stock population", organism_noun_plural="stock populations",
        housing_noun="vial", housing_noun_plural="vials",
        container_noun="incubator",
        line_noun="stock", line_noun_plural="stocks",
        cohort_noun="progeny", cohort_noun_plural="progeny",
        cross_noun="cross",
        identity_mode="group",
        age_unit="generations",
        capabilities=[
            "housing", "housing_grid", "group_counts", "lines", "crosses", "cohorts",
            "schedule", "health", "cull_log", "samples",
        ],
        schedule_rules=[
            ScheduleRule(
                "flip", "Flip to fresh food", "housing", "last_serviced_on", 14,
                icon="refresh", recurring=True,
                # Development scales with temperature, so the interval does too.
                temp_offsets={"25": 14, "22": 18, "18": 28},
            ),
            ScheduleRule(
                "backup", "Refresh backup copy", "line", "last_refreshed_on", 60,
                icon="copy", recurring=True,
            ),
        ],
        housing_purposes=["stock", "cross", "expansion", "experiment", "backup"],
        statuses=["alive", "crossed", "discarded", "lost"],
        sexes=["mixed", "virgin female", "male"],
        fields=[
            {"entity": "line", "key": "balancers", "label": "Balancers",
             "field_type": "text", "help_text": "CyO, TM3, TM6B…"},
            {"entity": "line", "key": "stock_centre", "label": "Stock centre",
             "field_type": "select", "options": ["BDSC", "VDRC", "Kyoto", "NIG-FLY", "own", "gift"],
             "show_in_table": True},
            {"entity": "line", "key": "stock_number", "label": "Stock number",
             "field_type": "text", "show_in_table": True},
            {"entity": "line", "key": "flybase_id", "label": "FlyBase ID", "field_type": "text"},
            {"entity": "housing", "key": "temperature_c", "label": "Temperature (°C)",
             "field_type": "select", "options": ["18", "22", "25", "29"],
             "default_value": "25", "show_in_table": True,
             "help_text": "Drives the flip interval."},
            {"entity": "housing", "key": "generation", "label": "Generation",
             "field_type": "text", "show_in_table": True},
            {"entity": "housing", "key": "food_type", "label": "Food",
             "field_type": "select", "options": ["standard", "molasses", "grape", "apple juice"]},
            {"entity": "cross", "key": "virgins_collected_on", "label": "Virgins collected",
             "field_type": "date"},
            {"entity": "cross", "key": "selection_marker", "label": "Selection marker",
             "field_type": "text", "help_text": "Visible marker used to pick progeny."},
        ],
        settings={"protocol_required": False, "regulated": False},
        notes="Invertebrate: no IACUC protocol, no census, no per-diem. Balancer "
              "stocks and a temperature-dependent flip clock are the distinctive parts.",
    ),

    # --- C. elegans --------------------------------------------------------
    Preset(
        key="c_elegans",
        label="C. elegans",
        label_plural="C. elegans",
        icon="worm",
        blurb="Worm strains on plates. Mostly self-fertilising, so lineage is "
              "single-parent — and the freezer is the real colony.",
        organism_noun="plate population", organism_noun_plural="plate populations",
        housing_noun="plate", housing_noun_plural="plates",
        container_noun="incubator",
        line_noun="strain", line_noun_plural="strains",
        cohort_noun="generation", cohort_noun_plural="generations",
        cross_noun="cross",
        identity_mode="group",
        age_unit="generations",
        capabilities=[
            "housing", "housing_grid", "group_counts", "lines", "crosses", "schedule",
            "preservation", "genotyping", "health", "samples",
        ],
        schedule_rules=[
            ScheduleRule(
                "chunk", "Chunk to fresh plate", "housing", "last_serviced_on", 7,
                icon="refresh", recurring=True,
                temp_offsets={"25": 4, "20": 7, "15": 12},
            ),
            ScheduleRule(
                "refreeze", "Re-freeze strain", "line", "last_frozen_on", 1095,
                icon="snowflake", recurring=True,
            ),
        ],
        housing_purposes=["maintenance", "cross", "expansion", "experiment", "starved"],
        statuses=["growing", "starved", "contaminated", "discarded"],
        sexes=["hermaphrodite", "male", "mixed"],
        fields=[
            {"entity": "line", "key": "allele", "label": "Allele",
             "field_type": "text", "show_in_table": True,
             "help_text": "CGC-assigned allele designation."},
            {"entity": "line", "key": "lab_prefix", "label": "Lab prefix",
             "field_type": "text", "help_text": "Registered lab code, e.g. CB, N2.",
             "show_in_table": True},
            {"entity": "line", "key": "wormbase_id", "label": "WormBase ID", "field_type": "text"},
            {"entity": "line", "key": "source", "label": "Source",
             "field_type": "select", "options": ["CGC", "NBRP", "own", "gift"]},
            {"entity": "housing", "key": "temperature_c", "label": "Temperature (°C)",
             "field_type": "select", "options": ["15", "20", "25"],
             "default_value": "20", "show_in_table": True,
             "help_text": "Drives the chunk interval."},
            {"entity": "housing", "key": "plate_type", "label": "Plate",
             "field_type": "select", "options": ["NGM", "NGM + OP50", "RNAi", "enriched peptone"]},
            {"entity": "housing", "key": "generation", "label": "Generation",
             "field_type": "text", "show_in_table": True},
            {"entity": "organism", "key": "stage", "label": "Stage",
             "field_type": "select",
             "options": ["egg", "L1", "L2", "L3", "L4", "adult", "dauer", "mixed"],
             "show_in_table": True},
        ],
        settings={"protocol_required": False, "regulated": False,
                  "preservation_methods": ["-80 °C soft agar", "liquid nitrogen"]},
        notes="Selfing hermaphrodites break bi-parental pedigree, so lineage allows a "
              "single parent. Cryo is routine, not exotic: freeze lots carry a "
              "thaw-recovery check.",
    ),

    # --- Zebrafish ---------------------------------------------------------
    Preset(
        key="zebrafish",
        label="Zebrafish",
        label_plural="Zebrafish",
        icon="fish",
        blurb="Tanks on a recirculating system. Groups with a headcount, "
              "individuals when one gets fin-clipped, plus a nursery stage.",
        organism_noun="fish group", organism_noun_plural="fish groups",
        housing_noun="tank", housing_noun_plural="tanks",
        container_noun="rack",
        line_noun="line", line_noun_plural="lines",
        cohort_noun="clutch", cohort_noun_plural="clutches",
        cross_noun="cross",
        identity_mode="hybrid",
        age_unit="dpf",
        capabilities=[
            "housing", "housing_grid", "group_counts", "individuals", "lines",
            "crosses", "cohorts", "nursery", "pedigree", "genotyping",
            "schedule", "environment", "health", "cull_log", "protocol",
            "billing", "samples",
        ],
        schedule_rules=[
            ScheduleRule("graduate", "Graduate to system", "cohort", "birth_on", 30,
                         icon="seedling"),
            ScheduleRule("fin_clip", "Fin-clip for genotyping", "cohort", "birth_on", 60,
                         icon="microscope"),
            ScheduleRule("turnover", "Line turnover", "line", "last_refreshed_on", 540,
                         icon="refresh", recurring=True),
        ],
        housing_purposes=["stock", "breeding", "mating", "nursery", "experiment", "quarantine"],
        statuses=["alive", "euthanized", "found dead", "transferred"],
        sexes=["mixed", "female", "male", "unknown"],
        fields=[
            {"entity": "line", "key": "zfin_name", "label": "ZFIN name",
             "field_type": "text", "show_in_table": True},
            {"entity": "line", "key": "allele", "label": "Allele", "field_type": "text"},
            {"entity": "line", "key": "background", "label": "Background",
             "field_type": "select", "options": ["AB", "TL", "TU", "WIK", "casper", "mixed"],
             "show_in_table": True},
            {"entity": "housing", "key": "density", "label": "Fish / L", "field_type": "number"},
            {"entity": "cohort", "key": "embryo_count", "label": "Embryos",
             "field_type": "number", "show_in_table": True},
            {"entity": "cohort", "key": "larvae_count", "label": "Larvae",
             "field_type": "number", "show_in_table": True},
        ],
        settings={
            "protocol_required": True, "regulated": True,
            "billing_unit": "housing",
            "environment_metrics": [
                {"key": "ph", "label": "pH", "unit": ""},
                {"key": "conductivity", "label": "Conductivity", "unit": "µS"},
                {"key": "temperature_c", "label": "Temperature", "unit": "°C"},
                {"key": "nitrite", "label": "Nitrite", "unit": "ppm"},
                {"key": "nitrate", "label": "Nitrate", "unit": "ppm"},
                {"key": "ammonia", "label": "Ammonia", "unit": "ppm"},
            ],
        },
        notes="A bespoke zebrafish module already ships with this app. This preset "
              "exists so the generic engine can be checked against it, and for labs "
              "that would rather run fish on the configurable engine.",
    ),

    # --- Mouse (reference) -------------------------------------------------
    Preset(
        key="mouse",
        label="Mouse",
        label_plural="Mice",
        icon="mouse",
        blurb="Individually tracked animals in cages, litters weaned at P21. "
              "The shape the rest of this engine was generalised from.",
        organism_noun="mouse", organism_noun_plural="mice",
        housing_noun="cage", housing_noun_plural="cages",
        container_noun="rack",
        line_noun="strain", line_noun_plural="strains",
        cohort_noun="litter", cohort_noun_plural="litters",
        cross_noun="mating",
        identity_mode="individual",
        age_unit="weeks",
        capabilities=[
            "housing", "housing_grid", "individuals", "lines", "crosses", "cohorts",
            "pedigree", "genotyping", "schedule", "weights", "health", "protocol",
            "billing", "samples",
        ],
        schedule_rules=[
            ScheduleRule("wean", "Wean", "cohort", "birth_on", 21, icon="baby"),
            ScheduleRule("genotype", "Genotype", "cohort", "birth_on", 21, icon="microscope"),
            ScheduleRule("retire", "Retire breeder", "organism", "birth_on", 210,
                         icon="clock"),
        ],
        housing_purposes=["breeding", "holding", "experiment", "weaning", "quarantine"],
        statuses=["alive", "euthanized", "found dead", "transferred"],
        sexes=["F", "M", "unknown"],
        fields=[
            {"entity": "line", "key": "background", "label": "Background",
             "field_type": "text", "show_in_table": True},
            {"entity": "line", "key": "supplier", "label": "Supplier", "field_type": "text"},
            {"entity": "line", "key": "mgi_id", "label": "MGI ID", "field_type": "text"},
            {"entity": "organism", "key": "ear_tag", "label": "Custom tag",
             "field_type": "text", "show_in_table": True},
        ],
        settings={"protocol_required": True, "regulated": True, "billing_unit": "housing"},
        notes="A bespoke mouse colony module already ships with this app; this preset is "
              "the reference expression of it in the configurable engine.",
    ),

    # --- Blank -------------------------------------------------------------
    Preset(
        key="custom",
        label="Custom organism",
        label_plural="Custom organisms",
        icon="paw",
        blurb="Start from nothing and pick every capability, noun and field yourself.",
        organism_noun="animal", organism_noun_plural="animals",
        housing_noun="enclosure", housing_noun_plural="enclosures",
        container_noun="rack",
        line_noun="line", line_noun_plural="lines",
        cohort_noun="cohort", cohort_noun_plural="cohorts",
        cross_noun="cross",
        identity_mode="hybrid",
        age_unit="days",
        capabilities=["housing", "group_counts", "lines"],
        housing_purposes=["stock", "experiment"],
        statuses=["alive", "removed"],
        sexes=["mixed", "female", "male", "unknown"],
        notes="",
    ),
)

PRESET_BY_KEY = {p.key: p for p in PRESETS}

# Seeded automatically on first run. Flies and worms now have their own
# vial/plate databases (app/stocks.py), so nothing is seeded here.
AUTO_SEED_PRESETS: tuple[str, ...] = ()

# Presets now served by the stock engine; kept above so old modules still
# describe themselves, but not offered for new databases.
STOCK_ENGINE_PRESETS = ("drosophila", "c_elegans")

AGE_UNITS = (
    ("days", "Days"),
    ("weeks", "Weeks"),
    ("dpf", "Days post-fertilisation"),
    ("generations", "Generations"),
    ("passages", "Passages"),
)

IDENTITY_MODES = (
    ("individual", "Individuals only",
     "Every animal is its own record with a unique ID."),
    ("group", "Groups only",
     "A record is a group with a headcount; no per-animal rows."),
    ("hybrid", "Groups, with individuals when needed",
     "Groups by default, but any group can resolve into named individuals."),
)
