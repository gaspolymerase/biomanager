"""A chemical or reagent can have several hazards (#58): the Hazard field of
the Chemicals and Reagents databases becomes "Several choices". One a lab
never changed gets the full list, with the controlled classes a lab's safety
office asks about (drug precursor 易制毒, explosive precursor 易制爆, highly
toxic 剧毒, narcotic or psychotropic); one a lab edited keeps its own
choices. "none" goes: with several choices, an empty field already says so,
and items marked "none" are left empty.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from migrations.helpers import has_table

revision = "0030_several_hazards"
down_revision = "0029_cage_purposes"
branch_labels = None
depends_on = None

# As app/inventory.py has them (a migration never imports the app).
OLD = ["none", "flammable", "corrosive", "toxic", "oxidiser", "irritant", "biohazard"]
NEW = ["flammable", "explosive", "corrosive", "toxic", "highly toxic", "oxidiser", "irritant", "biohazard",
       "drug precursor", "explosive precursor", "narcotic or psychotropic"]


def upgrade() -> None:
    if not has_table("inventory_modules"):
        return
    bind = op.get_bind()
    changed = []
    for module_id, raw in bind.execute(sa.text(
            "SELECT id, settings FROM inventory_modules WHERE kind IN ('chemicals', 'reagents')")).fetchall():
        try:
            settings = json.loads(raw or "{}")
        except ValueError:
            continue
        fields = settings.get("fields") if isinstance(settings, dict) else None
        touched = False
        for field in fields if isinstance(fields, list) else []:
            if not isinstance(field, dict) or field.get("key") != "hazard" or field.get("type") != "select":
                continue
            options = [str(o) for o in field.get("options") or []]
            field["options"] = NEW if options == OLD else [o for o in options if o.strip().lower() != "none"]
            field["type"] = "multiselect"
            field["width"] = max(int(field.get("width") or 0), 140)
            touched = True
        if touched:
            bind.execute(sa.text("UPDATE inventory_modules SET settings = :s WHERE id = :id"),
                         {"s": json.dumps(settings), "id": module_id})
            changed.append(module_id)
    if not changed or not has_table("inventory_items"):
        return
    for item_id, raw in bind.execute(sa.text(
            "SELECT id, attrs FROM inventory_items WHERE module_id_fk IN :ids").bindparams(
                sa.bindparam("ids", expanding=True)), {"ids": changed}).fetchall():
        try:
            attrs = json.loads(raw or "{}")
        except ValueError:
            continue
        if isinstance(attrs, dict) and str(attrs.get("hazard", "")).strip().lower() == "none":
            attrs["hazard"] = ""
            bind.execute(sa.text("UPDATE inventory_items SET attrs = :a WHERE id = :id"),
                         {"a": json.dumps(attrs), "id": item_id})


def downgrade() -> None:
    pass
