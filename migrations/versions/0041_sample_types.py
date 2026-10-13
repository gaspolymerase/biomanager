"""Samples by type: Samples databases made before this get what new ones
have.

- The *tabs* feature: a tab for each type above the sheet, each showing the
  columns used for that type.
- Which types the measured columns are for: Conc. and its unit for DNA, RNA
  and protein; 260/280 and 260/230 for DNA and RNA; Volume (µL) for blood,
  serum, DNA, RNA and protein. Only where the column has no types yet, and
  only types the database still has (a lab that renamed DNA keeps the column
  for every type).
- Tissue, Genotype, Condition, Fixation and Orientation, after Collected
  (else at the end), unless the database already has a column of that key or
  name.

Settings only (inventory_modules.settings, JSON); no table changes, and
running it again changes nothing.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from migrations.helpers import has_table

revision = "0041_sample_types"
down_revision = "0040_plate_layouts"
branch_labels = None
depends_on = None

EXTRACTS = ["DNA", "RNA", "protein"]
MEASURED_FOR = {
    "concentration": EXTRACTS, "conc_unit": EXTRACTS,
    "a260_280": ["DNA", "RNA"], "a260_230": ["DNA", "RNA"],
    "volume_ul": ["blood", "serum", *EXTRACTS],
}
DETAILS = [
    {"key": "tissue", "label": "Tissue", "type": "text", "icon": "paw", "width": 104, "types": ["tissue", *EXTRACTS]},
    {"key": "genotype", "label": "Genotype", "type": "text", "icon": "dna", "width": 150},
    {"key": "condition", "label": "Condition", "type": "text", "icon": "flask", "width": 140},
    {"key": "fixation", "label": "Fixation", "type": "select",
     "options": ["fresh frozen", "4% PFA", "formalin", "RNAlater", "methanol", "none"],
     "icon": "droplet", "width": 104, "types": ["tissue"]},
    {"key": "orientation", "label": "Orientation", "type": "select",
     "options": ["sagittal", "coronal", "horizontal", "transverse"], "icon": "signpost", "width": 96,
     "types": ["tissue"]},
]


def _types(wanted: list[str], categories: list[str]) -> list[str]:
    """The wanted types as this database spells them; those it hasn't got
    are left out (none left: every type)."""
    known = {str(c).casefold(): str(c) for c in categories}
    return [known[t.casefold()] for t in wanted if t.casefold() in known]


def upgrade() -> None:
    if not has_table("inventory_modules"):
        return
    bind = op.get_bind()
    for module_id, raw in bind.execute(sa.text("select id, settings from inventory_modules where kind = 'samples'")).all():
        try:
            settings = json.loads(raw or "{}")
        except ValueError:
            continue
        fields = settings.get("fields")
        if not isinstance(settings, dict) or not isinstance(fields, list):
            continue
        before = json.dumps(settings, sort_keys=True)
        categories = settings.get("categories") if isinstance(settings.get("categories"), list) else []
        features = settings.get("features") if isinstance(settings.get("features"), list) else []
        if "tabs" not in features:
            settings["features"] = [*features, "tabs"]
        for f in fields:
            if isinstance(f, dict) and f.get("key") in MEASURED_FOR and not f.get("types"):
                f["types"] = _types(MEASURED_FOR[f["key"]], categories)
        keys = {str(f.get("key", "")).lower() for f in fields if isinstance(f, dict)}
        labels = {str(f.get("label", "")).strip().lower() for f in fields if isinstance(f, dict)}
        new = []
        for d in DETAILS:
            if d["key"] in keys or d["label"].lower() in labels:
                continue
            d = dict(d)
            if d.get("types"):
                d["types"] = _types(d["types"], categories)
            new.append(d)
        at = next((i + 1 for i, f in enumerate(fields) if isinstance(f, dict) and f.get("key") == "collected_on"),
                  len(fields))
        settings["fields"] = fields[:at] + new + fields[at:]
        if json.dumps(settings, sort_keys=True) != before:
            bind.execute(sa.text("update inventory_modules set settings = :s where id = :i"),
                         {"s": json.dumps(settings), "i": module_id})


def downgrade() -> None:
    pass
