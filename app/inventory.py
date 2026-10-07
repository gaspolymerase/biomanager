"""Lab inventory presets: what a samples, orders, reagents, antibodies or
viruses database tracks, and how it behaves.

An inventory module's `settings` JSON holds:

  features    which built-in behaviours are on:
                storage   freezer boxes / shelves with positions (rack grid)
                board     a status board (orders: requested → ordered → received)
                sharing   "Mine" vs "Lab common" stock
                quantity  quantity + unit columns
                supplier  vendor, catalogue number, lot
                expiry    an expiry date, with expired / expiring-soon flags
                received  a received date
  categories  the choices for the category column ("Type", "Kind"…)
  category_label
  statuses    the status workflow, first is the default
  fields      extra, preset-specific columns kept in `attrs`:
                {key, label, type, options?, icon?, width?, in_table?}
              type "plasmid" holds a plasmid's number (Plasmids'
              #): the sheet links to that plasmid, and its page lists
              the records made from it.

Presets are starting points; everything is editable in Configure.
"""

from __future__ import annotations

import json

FIELD_TYPES = ("text", "textarea", "number", "date", "select", "user", "url", "source", "plasmid")

FEATURES = {
    "storage": "Freezer boxes and shelves with positions, shown as a grid",
    "board": "A status board (e.g. requested → ordered → received)",
    "sharing": "Separate my own stock from lab common stock",
    "quantity": "Quantity and unit",
    "supplier": "Vendor, catalogue number and lot",
    "expiry": "Expiry date, with expired and expiring-soon warnings",
    "received": "Date received",
}

STORAGE_TEMPS = ["RT", "4 °C", "−20 °C", "−80 °C", "LN₂"]
CONC_UNITS = ["ng/µL", "µg/mL", "mg/mL", "nM", "µM", "cells/mL"]
# What a Nanodrop, Qubit or BCA reading gives, as numbers a sheet can sort
# and sum (revision 0008 adds them to Samples made before they were here).
SAMPLE_MEASURES = [
    {"key": "concentration", "label": "Conc.", "type": "number", "icon": "amount", "width": 80},
    {"key": "conc_unit", "label": "Conc. unit", "type": "select", "options": CONC_UNITS, "icon": "amount", "width": 84},
    {"key": "a260_280", "label": "260/280", "type": "number", "icon": "amount", "width": 72},
    {"key": "a260_230", "label": "260/230", "type": "number", "icon": "amount", "width": 72},
    {"key": "volume_ul", "label": "Volume (µL)", "type": "number", "icon": "droplet", "width": 84},
]

PRESETS: dict[str, dict] = {
    "samples": {
        "label": "Samples",
        "icon": "vial",
        "item_noun": "sample", "item_noun_plural": "samples",
        "blurb": "Harvested tissue, blood, DNA and RNA, traced back to the animal they came from and kept in freezer boxes.",
        "features": ["storage", "sharing"],
        "category_label": "Type",
        "categories": ["tissue", "blood", "serum", "DNA", "RNA", "protein", "organoid", "cells", "other"],
        "statuses": ["available", "in use", "used up", "discarded"],
        "fields": [
            # Wider than a plain column: the cell holds the colony and the
            # ID in it, both editable in the sheet.
            {"key": "source", "label": "Source", "type": "source", "icon": "signpost", "width": 210},
            {"key": "collected_on", "label": "Collected", "type": "date", "icon": "calendar", "width": 116},
            {"key": "amount", "label": "Amount", "type": "text", "icon": "amount", "width": 84},
            *SAMPLE_MEASURES,
            {"key": "storage_temp", "label": "Stored at", "type": "select", "options": STORAGE_TEMPS, "icon": "snowflake", "width": 88},
        ],
    },
    "orders": {
        "label": "Orders",
        "icon": "cart",
        "item_noun": "order", "item_noun_plural": "orders",
        "blurb": "What the lab has asked for and where each order stands, on a board from requested to received.",
        "features": ["board", "quantity", "supplier", "received"],
        "category_label": "Category",
        "categories": ["reagent", "antibody", "virus", "consumable", "equipment", "service", "other"],
        "statuses": ["requested", "ordered", "received", "cancelled"],
        # What has to be filled in before an order can be placed.
        "required": ["name", "vendor", "catalog_number", "quantity"],
        "fields": [
            {"key": "price", "label": "Price", "type": "number", "icon": "receipt", "width": 80},
            {"key": "account", "label": "Account / grant", "type": "text", "icon": "barcode", "width": 120},
            {"key": "url", "label": "Link", "type": "url", "icon": "link", "width": 136, "in_table": False},
        ],
    },
    "reagents": {
        "label": "Reagents",
        "icon": "flask",
        "item_noun": "reagent", "item_noun_plural": "reagents",
        "blurb": "Chemicals, buffers, enzymes and kits. Keep your own stock apart from the lab's common shelf.",
        "features": ["storage", "sharing", "quantity", "supplier", "expiry", "received"],
        "category_label": "Kind",
        "categories": ["chemical", "buffer", "enzyme", "kit", "media", "primer", "dye", "other"],
        "statuses": ["in stock", "low", "empty", "discarded"],
        "fields": [
            {"key": "cas", "label": "CAS", "type": "text", "icon": "barcode", "width": 92},
            {"key": "concentration", "label": "Concentration", "type": "text", "icon": "amount", "width": 100},
            {"key": "storage_temp", "label": "Stored at", "type": "select", "options": STORAGE_TEMPS, "icon": "snowflake", "width": 88},
            {"key": "hazard", "label": "Hazard", "type": "select",
             "options": ["none", "flammable", "corrosive", "toxic", "oxidiser", "irritant", "biohazard"], "icon": "warning", "width": 92},
        ],
    },
    "antibodies": {
        "label": "Antibodies",
        "icon": "antibody",
        "item_noun": "antibody", "item_noun_plural": "antibodies",
        "blurb": "Primary and secondary antibodies with host, clone, conjugate, validated applications and working dilutions.",
        "features": ["storage", "sharing", "quantity", "supplier", "expiry", "received"],
        "category_label": "Role",
        "categories": ["primary", "secondary", "isotype control"],
        "statuses": ["in stock", "low", "empty", "discarded"],
        "fields": [
            {"key": "host", "label": "Host", "type": "select",
             "options": ["mouse", "rabbit", "rat", "goat", "donkey", "chicken", "sheep", "guinea pig", "hamster", "other"],
             "icon": "paw", "width": 84},
            {"key": "clonality", "label": "Clonality", "type": "select",
             "options": ["monoclonal", "polyclonal", "recombinant"], "icon": "sitemap", "width": 100},
            {"key": "clone", "label": "Clone", "type": "text", "icon": "dna", "width": 80},
            {"key": "conjugate", "label": "Conjugate", "type": "text", "icon": "sparkle", "width": 100},
            {"key": "reactivity", "label": "Reactivity", "type": "text", "icon": "target", "width": 104},
            {"key": "applications", "label": "Applications", "type": "text", "icon": "microscope", "width": 112},
            {"key": "dilution", "label": "Dilution", "type": "text", "icon": "droplet", "width": 92},
            {"key": "isotype", "label": "Isotype", "type": "text", "icon": "tag", "width": 76, "in_table": False},
            {"key": "rrid", "label": "RRID", "type": "text", "icon": "link", "width": 112},
        ],
    },
    "viruses": {
        "label": "Viruses",
        "icon": "virus",
        "item_noun": "virus", "item_noun_plural": "viruses",
        "blurb": "Viral vectors — AAV, lentivirus, rabies and more — with serotype, titer, the plasmid each was made from, "
                 "biosafety level and aliquots in −80 °C boxes.",
        "features": ["storage", "sharing", "quantity", "supplier", "expiry", "received"],
        "category_label": "Vector",
        "categories": ["AAV", "lentivirus", "retrovirus", "adenovirus", "rabies", "HSV", "VSV", "other"],
        "statuses": ["in stock", "low", "used up", "discarded"],
        "fields": [
            {"key": "serotype", "label": "Serotype", "type": "text", "icon": "tag", "width": 92},
            {"key": "plasmid", "label": "Made from", "type": "plasmid", "icon": "plasmid", "width": 128},
            {"key": "promoter", "label": "Promoter", "type": "text", "icon": "signpost", "width": 84},
            {"key": "payload", "label": "Payload", "type": "text", "icon": "dna", "width": 128},
            {"key": "titer", "label": "Titer", "type": "text", "icon": "amount", "width": 104},
            {"key": "biosafety", "label": "Biosafety", "type": "select",
             "options": ["BSL-1", "BSL-2", "BSL-2+", "BSL-3"], "icon": "warning", "width": 84},
            {"key": "made_on", "label": "Made", "type": "date", "icon": "calendar", "width": 112},
        ],
    },
    "primers": {
        "label": "Primers & oligos",
        "icon": "dna",
        "item_noun": "primer", "item_noun_plural": "primers",
        "blurb": "Primers, probes and guide oligos by sequence: length, GC and Tm are worked out from it, "
                 "and Add primer pair makes the forward and reverse at once.",
        "features": ["storage", "sharing", "quantity", "supplier", "received"],
        "category_label": "Use",
        "categories": ["PCR", "qPCR", "sequencing", "cloning", "genotyping", "mutagenesis", "probe", "gRNA", "other"],
        "statuses": ["to order", "ordered", "in stock", "low", "used up", "discarded"],
        "fields": [
            {"key": "sequence", "label": "Sequence (5′→3′)", "type": "text", "icon": "dna", "width": 204},
            {"key": "direction", "label": "Direction", "type": "select", "options": ["forward", "reverse", "probe"],
             "icon": "signpost", "width": 80},
            {"key": "target", "label": "Target", "type": "text", "icon": "target", "width": 92},
            # The plasmid it binds (app/primer_records.py): primers drawn on a map land here.
            {"key": "template", "label": "Plasmid", "type": "plasmid", "icon": "plasmid", "width": 120},
            {"key": "pair", "label": "Pair", "type": "text", "icon": "link", "width": 104},
            {"key": "length", "label": "Length", "type": "number", "icon": "count", "width": 64},
            {"key": "gc", "label": "GC %", "type": "number", "icon": "gauge", "width": 60},
            {"key": "tm", "label": "Tm (°C)", "type": "number", "icon": "temperature", "width": 68},
            {"key": "modification", "label": "Modification", "type": "text", "icon": "sparkle", "width": 92, "in_table": False},
            {"key": "stock_um", "label": "Stock (µM)", "type": "number", "icon": "amount", "width": 76},
            {"key": "purification", "label": "Purification", "type": "select",
             "options": ["desalted", "cartridge", "HPLC", "PAGE"], "icon": "flask", "width": 88, "in_table": False},
        ],
    },
    # A plasmid kept in bacteria: its page lists them (Glycerol stocks, on Storage).
    "glycerol_stocks": {
        "label": "Glycerol stocks",
        "icon": "bacterium",
        "item_noun": "glycerol stock", "item_noun_plural": "glycerol stocks",
        "blurb": "Bacteria carrying each plasmid, frozen in glycerol: the plasmid, strain, colony, how it was checked, "
                 "and where each tube sits in the −80 °C boxes.",
        "features": ["storage", "sharing"],
        "category_label": "Strain",
        "categories": ["DH5α", "Stbl3", "TOP10", "DH10B", "XL1-Blue", "NEB Stable", "ccdB Survival", "BL21(DE3)", "other"],
        "statuses": ["in stock", "low", "used up", "discarded"],
        "fields": [
            {"key": "plasmid", "label": "Plasmid", "type": "plasmid", "icon": "plasmid", "width": 128},
            {"key": "colony", "label": "Colony / clone", "type": "text", "icon": "tag", "width": 92},
            {"key": "resistance", "label": "Resistance", "type": "text", "icon": "resistance", "width": 92},
            {"key": "verified", "label": "Checked by", "type": "select",
             "options": ["not checked", "digest", "colony PCR", "Sanger", "whole plasmid"], "icon": "success", "width": 104},
            {"key": "frozen_on", "label": "Frozen", "type": "date", "icon": "calendar", "width": 112},
            {"key": "glycerol", "label": "Glycerol %", "type": "number", "icon": "droplet", "width": 76, "in_table": False},
        ],
    },
    "cell_lines": {
        "label": "Cell lines",
        "icon": "petri",
        "item_noun": "vial", "item_noun_plural": "vials",
        "blurb": "Frozen vials of each line and clone: passage, freeze date, cells per vial, mycoplasma tests, "
                 "and where each vial sits in the LN₂ boxes.",
        "features": ["storage", "sharing", "supplier", "received"],
        "category_label": "Type",
        "categories": ["line", "primary", "iPSC", "stable pool", "clone", "hybridoma", "other"],
        "statuses": ["in stock", "thawed", "used up", "discarded"],
        "fields": [
            {"key": "species", "label": "Species", "type": "select",
             "options": ["human", "mouse", "rat", "hamster", "monkey", "insect", "other"], "icon": "paw", "width": 80},
            {"key": "parent", "label": "Parent / made from", "type": "text", "icon": "sitemap", "width": 120},
            {"key": "passage", "label": "Passage", "type": "number", "icon": "count", "width": 68},
            {"key": "frozen_on", "label": "Frozen", "type": "date", "icon": "calendar", "width": 112},
            {"key": "cells_per_vial", "label": "Cells / vial", "type": "text", "icon": "amount", "width": 84},
            {"key": "medium", "label": "Medium", "type": "text", "icon": "flask", "width": 112, "in_table": False},
            {"key": "selection", "label": "Selection", "type": "text", "icon": "resistance", "width": 92, "in_table": False},
            {"key": "mycoplasma", "label": "Mycoplasma", "type": "select", "options": ["not tested", "negative", "positive"],
             "icon": "warning", "width": 88},
            {"key": "myco_tested_on", "label": "Myco tested", "type": "date", "icon": "calendar", "width": 112, "in_table": False},
            {"key": "storage_temp", "label": "Stored at", "type": "select", "options": STORAGE_TEMPS, "icon": "snowflake", "width": 88},
        ],
    },
    "custom": {
        "label": "Custom list",
        "icon": "list",
        "item_noun": "item", "item_noun_plural": "items",
        "blurb": "An empty list. Pick the features you need and add your own columns.",
        "features": ["sharing"],
        "category_label": "Category",
        "categories": [],
        "statuses": [],
        "fields": [],
    },
}

# Created automatically on first start, so every lab has them.
AUTO_SEED = ("samples", "orders", "reagents", "antibodies")


def load(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def normalise_settings(raw) -> dict:
    """Settings with every key present and junk dropped."""
    s = load(raw)
    fields = []
    for f in s.get("fields") or []:
        if not isinstance(f, dict) or not f.get("key") or f.get("type") not in FIELD_TYPES:
            continue
        fields.append({
            "key": str(f["key"]), "label": str(f.get("label") or f["key"]), "type": f["type"],
            "options": [str(o) for o in f.get("options") or []],
            "icon": str(f.get("icon") or ""), "width": int(f.get("width") or 130),
            "in_table": f.get("in_table", True) is not False,
        })
    return {
        "features": [x for x in (s.get("features") or []) if x in FEATURES],
        "category_label": str(s.get("category_label") or "Category"),
        "categories": [str(c) for c in s.get("categories") or []],
        "statuses": [str(c) for c in s.get("statuses") or []],
        "fields": fields,
        # None: never chosen, so the preset's list applies (ModuleView.required).
        "required": ([str(k) for k in s["required"] if isinstance(k, str)]
                     if isinstance(s.get("required"), list) else None),
    }


def preset_settings(key: str) -> dict:
    preset = PRESETS.get(key) or PRESETS["custom"]
    return normalise_settings({k: preset.get(k) for k in
                               ("features", "category_label", "categories", "statuses", "fields", "required")})


# Built-in columns that can be made required: (form name, feature it needs, label).
# None as the label means the module's own name for it.
REQUIRABLE = (
    ("name", None, None), ("category", None, None),
    ("vendor", "supplier", "Vendor"), ("catalog_number", "supplier", "Catalog #"), ("lot", "supplier", "Lot"),
    ("quantity", "quantity", "Quantity"), ("unit", "quantity", "Unit"),
    ("location_note", None, "Location note"),
    ("received_on", "received", "Received"), ("expires_on", "expiry", "Expires"),
)


# Icon for a field type, used when a custom field has none.
FIELD_TYPE_ICONS = {"text": "type", "textarea": "note", "number": "count", "date": "calendar",
                    "select": "tag", "user": "user", "url": "link", "source": "signpost", "plasmid": "plasmid"}
