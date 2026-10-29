#!/usr/bin/env python3
"""Build app/static/icons.svg — the app's single icon sprite.

Why a sprite rather than an icon font or a JS library:

  * it is one file, served once, cached, and works offline in the packaged
    desktop app — no CDN, no runtime DOM rewriting
  * `<use href="…#name">` inherits `currentColor`, so icons follow text
  * Lucide's interface icons and our own lab drawings sit side by side
    with no visual seam

Every icon is a line drawing on Lucide's 24 grid, stroked in currentColor
with round ends. Lucide (lucide-static, installed by npm in frontend/)
supplies the interface and most objects; it has no laboratory mouse, fruit
fly, plasmid, cage or culture vial, so those and the other lab and organ
drawings are drawn here in the same style.

The stroke is set on each symbol and the fill is left to CSS
(`svg.icon { fill: none }`), so a toggle can fill an icon in: a starred
favourite is `fill: currentColor`.

    python scripts/build-icons.py [path/to/lucide-static]

Icons: Lucide, ISC licence. Attribution is embedded in the sprite.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = PROJECT_ROOT / "app" / "static" / "icons.svg"
DEFAULT_SOURCE = PROJECT_ROOT / "frontend" / "node_modules" / "lucide-static"

STROKE = 'stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"'

# name-in-app -> Lucide icon
FROM_LUCIDE = {
    # --- column headers: one glyph per kind of value ---
    "sex": "venus-and-mars",
    "female": "venus",
    "male": "mars",
    "age": "hourglass",
    "born": "cake",
    "died": "calendar-x",
    "litter": "users-round",
    "status": "contrast",
    "note": "sticky-note",
    "count": "hash",
    "barcode": "barcode",
    "vendor": "building-2",
    "resistance": "pill",
    "protocol": "clipboard-check",
    "position": "crosshair",
    "amount": "beaker",
    "target": "target",
    # --- navigation & chrome ---
    "home": "house",
    "calendar": "calendar-days",
    "notebook": "notebook",
    "search": "search",
    "settings": "settings",
    "sign-out": "log-out",
    "menu": "menu",
    "plus": "plus",
    "close": "x",
    "check": "check",
    "chevron-right": "chevron-right",
    "chevron-down": "chevron-down",
    "chevron-left": "chevron-left",
    "chevron-up": "chevron-up",
    "ellipsis": "ellipsis",
    "sidebar": "panel-left",
    "grid": "layout-grid",
    "list": "list",
    "table": "table",
    "filter": "list-filter",
    "sort": "arrow-down-wide-narrow",
    "eye-off": "eye-off",
    "external": "external-link",
    "back": "arrow-left",
    "kanban": "square-kanban",
    "inbox": "inbox",
    "todo": "list-todo",
    "share": "share-2",
    "command": "command",
    "pin": "pin",
    "panel-right": "panel-right",
    # --- objects & actions ---
    "database": "database",
    "layers": "layers",
    "box": "package",
    "archive": "archive",
    "trash": "trash-2",
    "edit": "pencil",
    "copy": "copy",
    "duplicate": "copy-plus",
    "print": "printer",
    "download": "download",
    "upload": "upload",
    "import": "file-input",
    "tag": "tag",
    "folder": "folder",
    "file": "file-text",
    "link": "link",
    "image": "image",
    "book-open": "book-open",
    "person-chalkboard": "presentation",
    "key": "key",
    "mail": "mail",
    "bell": "bell",
    "clock": "clock",
    "history": "history",
    "undo": "undo-2",
    "refresh": "refresh-cw",
    "warning": "triangle-alert",
    "info": "info",
    "success": "circle-check",
    "error": "circle-x",
    "user": "user",
    "users": "users",
    "shield": "shield",
    "chart": "chart-line",
    "scale": "scale",
    "receipt": "receipt",
    "location": "map-pin",
    "qrcode": "qr-code",
    "snowflake": "snowflake",
    "droplet": "droplet",
    "temperature": "thermometer",
    "sparkle": "sparkles",
    "heart": "heart",
    "star": "star",
    # --- biology Lucide draws well ---
    "dna": "dna",
    "microscope": "microscope",
    "vial": "test-tube",
    "vials": "test-tubes",
    "flask": "flask-conical",
    "syringe": "syringe",
    "worm": "worm",
    "fish": "fish",
    "virus": "virus",
    "paw": "paw-print",
    "egg": "egg",
    "seedling": "sprout",
    "stethoscope": "stethoscope",
    "notes-medical": "clipboard-plus",
    "heart-pulse": "heart-pulse",
    "weight": "weight",
    # --- library folders (lab_notebook.FOLDER_ICONS): body ---
    "brain": "brain",
    "bone": "bone",
    "skull": "skull",
    "hand": "hand",
    "ear": "ear",
    "person": "person-standing",
    # --- library folders: microbes, chemistry, safety ---
    "atom": "atom",
    "pills": "tablets",
    "biohazard": "biohazard",
    "radiation": "radiation",
    "toxic": "skull",
    # --- library folders: procedures ---
    "dropper": "pipette",
    "scissors": "scissors",
    "bandage": "bandage",
    "aseptic": "soap-dispenser-droplet",
    "recovery": "bed",
    "recording": "audio-waveform",
    "x-ray": "scan",
    "clinician": "briefcase-medical",
    "ruler": "ruler",
    # --- library folders: more organisms ---
    "bird": "bird",
    # Lucide's rat is the lab mouse: a rodent in profile, its tail curled.
    "mouse": "rat",
    "rat": "rat",
    "rabbit": "rabbit",
    "squirrel": "squirrel",
    "cat": "cat",
    "dog": "dog",
    "turtle": "turtle",
    "snail": "snail",
    "shrimp": "shrimp",
    "joint": "bone",
    "leaf": "leaf",
    "wheat": "wheat",
    "clipboard": "clipboard-check",
    "sitemap": "network",
    "alarm": "alarm-clock",
    "arrow-down": "arrow-down",
    "arrow-up": "arrow-up",
    "baby": "baby",
    "bolt": "zap",
    "calculator": "calculator",
    "calendar-clock": "calendar-check",
    "cart": "shopping-cart",
    "chevrons-right": "chevrons-right",
    "circle": "circle",
    "columns": "columns-2",
    "eye": "eye",
    "file-archive": "file-archive",
    "flask-vial": "test-tubes",
    "folder-plus": "folder-plus",
    "gauge": "gauge",
    "list-check": "list-checks",
    "lock": "lock",
    "repeat": "repeat",
    "rss": "rss",
    "sign-in": "log-in",
    "signpost": "signpost",
    "sliders": "sliders-horizontal",
    "template": "clipboard-list",
    "type": "type",
    "user-gear": "user-cog",
    "user-plus": "user-plus",
    "wand": "wand-sparkles",
}

# Drawn here, on Lucide's 24 grid and in its style: Lucide has no fruit
# fly, plasmid or culture vial, and no organs but the brain and bones.
# A dot is a zero-length line ("h.01"), as Lucide draws one.
CUSTOM: dict[str, str] = {}

# A fruit fly from above: head, thorax, the banded abdomen, a wing each side
# swept back.
CUSTOM["fly"] = '''
<circle cx="12" cy="4.6" r="1.9"/>
<ellipse cx="12" cy="9.2" rx="2.7" ry="2.5"/>
<path d="M12 11.7c-1.7 0-2.7 1.9-2.7 4.4s1.2 4.9 2.7 4.9 2.7-2.4 2.7-4.9-1-4.4-2.7-4.4z"/>
<path d="M9.6 15.3h4.8M9.9 18h4.2"/>
<path d="M9.6 9.8C6.6 9.4 3 11.3 3 14.2c0 1.6 1.6 2.4 3.1 1.6l3.6-3.6"/>
<path d="M14.4 9.8c3-.4 6.6 1.5 6.6 4.4 0 1.6-1.6 2.4-3.1 1.6l-3.6-3.6"/>
'''

# A plasmid: the circle, its insert drawn bold, and the promoter's arrow.
CUSTOM["plasmid"] = '''
<circle cx="12" cy="12" r="8"/>
<path d="M12 4a8 8 0 0 1 7.6 5.5" stroke-width="3.6"/>
<path d="M6.3 17.6a8 8 0 0 1-2.1-3.3"/>
<path d="m8.5 18.9-2.2-1.3.4 2.5"/>
'''

# A Petri dish from above: rim, base and three colonies.
CUSTOM["petri"] = '''
<circle cx="12" cy="12" r="9"/>
<path d="M18.4 8.2a7.3 7.3 0 0 0-4.4-3.9"/>
<circle cx="9.3" cy="10" r="1.3"/>
<circle cx="14.6" cy="13.8" r="1"/>
<path d="M9.8 15.4h.01"/>
'''

# A mouse cage: the tub, the wire lid arching over it.
CUSTOM["cage"] = '''
<path d="M3 11h18"/>
<path d="m4.8 11 1.3 7.3A2 2 0 0 0 8.1 20h7.8a2 2 0 0 0 2-1.7L19.2 11"/>
<path d="M5.5 11V8.5c4-2.6 9-2.6 13 0V11"/>
<path d="M9 11V7.2M12 11V6.6M15 11V7.2"/>
'''

# An aquarium tank: the glass, the water line, a bubble or two.
CUSTOM["tank"] = '''
<rect x="3" y="5" width="18" height="14" rx="2"/>
<path d="M3 10c1.5 0 1.5.9 3 .9s1.5-.9 3-.9 1.5.9 3 .9 1.5-.9 3-.9 1.5.9 3 .9 1.5-.9 3-.9"/>
<path d="M8 15.5c.9-1 2.4-1 3.3 0-.9 1-2.4 1-3.3 0zM11.3 15.5l1.2-.9v1.8z"/>
<path d="M16.5 14.5h.01M17.5 12.8h.01"/>
'''

# A fly vial: the foam plug, the tube, the food in its bottom.
CUSTOM["culture-vial"] = '''
<rect x="7.5" y="2.5" width="9" height="4" rx="1.2"/>
<path d="M8.5 6.5v12.7A2.3 2.3 0 0 0 10.8 21.5h2.4a2.3 2.3 0 0 0 2.3-2.3V6.5"/>
<path d="M8.5 16h7"/>
'''

# An antibody (IgG): the Y of the heavy chains, a light chain beside each arm.
CUSTOM["antibody"] = '''
<path d="M12 21v-8.5M12 12.5 6.5 5.5M12 12.5l5.5-7"/>
<path d="M9.3 14.1 4 7.6M14.7 14.1 20 7.6"/>
'''

# A cell: its membrane, the nucleus, an organelle and a vesicle.
CUSTOM["cell"] = '''
<path d="M12 3c5 0 9 3.6 9 8.6 0 5.3-4 9.4-9.2 9.4C6.7 21 3 17.1 3 12.1 3 7 7 3 12 3z"/>
<circle cx="10.4" cy="11" r="3.2"/>
<ellipse cx="16" cy="15.6" rx="1.9" ry="1" transform="rotate(-35 16 15.6)"/>
<path d="M15.6 7.4h.01M7.6 16.8h.01"/>
'''

# A microcentrifuge tube: its lid open on the hinge, the rim, the conical
# body with a fill line.
CUSTOM["microtube"] = '''
<path d="M8 8h8v3.5l-2.7 8.7a1.4 1.4 0 0 1-2.6 0L8 11.5z"/>
<path d="M7 6h10v2H7z"/>
<path d="M7.5 6 5.2 3.3a1 1 0 0 1 .3-1.5l.6-.3a1 1 0 0 1 1.3.4L9 5"/>
<path d="M8.3 12h7.4"/>
'''

# A multi-well plate with its A1 corner cut.
CUSTOM["well-plate"] = '''
<path d="M5 5h14.5a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-15a2 2 0 0 1-2-2V7.5z"/>
<circle cx="7.5" cy="10" r="1.5"/><circle cx="12" cy="10" r="1.5"/><circle cx="16.5" cy="10" r="1.5"/>
<circle cx="7.5" cy="14.5" r="1.5"/><circle cx="12" cy="14.5" r="1.5"/><circle cx="16.5" cy="14.5" r="1.5"/>
'''

# Blood: a red cell with its pale centre, another behind it.
CUSTOM["blood"] = '''
<circle cx="14.5" cy="14.5" r="6"/>
<circle cx="14.5" cy="14.5" r="2.1"/>
<path d="M8.6 15.3a5.8 5.8 0 1 1 6.7-6.7"/>
'''

# A kidney: the bean, and the ureter leaving its hilum.
CUSTOM["kidney"] = '''
<path d="M14.5 3C10 2.2 5.5 5.8 5.5 11.8S9.5 21 13.5 21c2.4 0 3.5-1.9 2.6-3.9-.6-1.4-2.4-2.3-2.4-4.6s1.8-3.1 2.6-4.6C17.4 5.9 17 3.4 14.5 3z"/>
<path d="M14.2 12.6h1.6c1.5 0 2.7 1.2 2.7 2.7V21"/>
'''

# The liver: its lobes as one wedge, the ligament between them.
CUSTOM["liver"] = '''
<path d="M2.5 9.5C6 6.5 11 5.5 15.5 5.7c4.3.2 6.2 2.7 5.8 6.1-.4 3.7-3 6.5-6.5 7C11 19.3 7 16.5 2.5 9.5z"/>
<path d="M11.5 6.2c-.3 2.4 0 4.5 1.2 6.4"/>
'''

# The stomach: the oesophagus in, the bag, the duodenum out.
CUSTOM["stomach"] = '''
<path d="M9 2v3.2c0 .9-.5 1.6-1.3 2.2C5.4 9 4.3 11.6 4.7 14.6c.6 4.2 4.6 6.9 9.2 6.3 3.3-.4 5.6-2.7 5.6-5.4 0-1.7-1.1-2.8-2.6-2.8-1.4 0-2.1.9-3.3 1.2-1.6.4-2.9-.6-3-2.1-.1-1.4.8-2.3 1.5-3.2.7-.9.9-2.1.4-3"/>
<path d="M19.4 14.8 22 13"/>
'''

# The gut: one tube winding back and forth.
CUSTOM["intestine"] = '''
<path d="M15 2.5v2H7.5a3 3 0 0 0 0 6h9a3 3 0 0 1 0 6h-9a3 3 0 0 0 0 6H15"/>
'''

# Lungs: the trachea, the bronchi and a lobe each side.
CUSTOM["lungs"] = '''
<path d="M12 3v7.5M12 8.5l-2.2 2.2M12 8.5l2.2 2.2"/>
<path d="M9.5 7C6 7.6 4 12 4 16.5 4 18.6 5.2 20 7 20c2 0 3-1.3 3-3V9"/>
<path d="M14.5 7C18 7.6 20 12 20 16.5c0 2.1-1.2 3.5-3 3.5-2 0-3-1.3-3-3V9"/>
'''

# Lungs with a virus beside them.
CUSTOM["lungs-virus"] = '''
<path d="M12 3v7.5M12 8.5l-2.2 2.2M12 8.5l2.2 2.2"/>
<path d="M9.5 7C6 7.6 4 12 4 16.5 4 18.6 5.2 20 7 20c2 0 3-1.3 3-3V9"/>
<path d="M14.5 7c2 .3 3.5 2 4.4 4.2"/>
<circle cx="17.5" cy="17" r="2.4"/>
<path d="M17.5 13.3v1.3M17.5 19.4v1.3M21.2 17h-1.3M15.1 17h-1.3"/>
'''

# A molar: its crown and two roots.
CUSTOM["tooth"] = '''
<path d="M7 3.5c-2.5 0-4 2-4 4.5 0 2.4 1.3 3.8 1.8 6.6.5 2.8.8 5.9 2.3 5.9 1.8 0 1.6-4.3 2.4-5.6.4-.7.8-1 1.5-1s1.1.3 1.5 1c.8 1.3.6 5.6 2.4 5.6 1.5 0 1.8-3.1 2.3-5.9.5-2.8 1.8-4.2 1.8-6.6 0-2.5-1.5-4.5-4-4.5-1.7 0-2.6.8-4 .8S8.7 3.5 7 3.5z"/>
'''

# A surgical mask with its ties.
CUSTOM["mask"] = '''
<path d="M4 9c2.7-1.3 5.3-2 8-2s5.3.7 8 2v4c0 3.3-3.6 6-8 6s-8-2.7-8-6z"/>
<path d="M4 10H2.5M20 10h1.5M8 11.5h8M8.5 14.5h7"/>
'''

# A cow's head: ears, the face, the muzzle.
CUSTOM["cow"] = '''
<path d="M7 8C5 8 3.5 6.8 3 5c1.8 0 3.4.4 4.4 1.3M17 8c2 0 3.5-1.2 4-3-1.8 0-3.4.4-4.4 1.3"/>
<path d="M7 7.5h10v6.5c0 2.2-1.8 4-4 4h-2c-2.2 0-4-1.8-4-4z"/>
<path d="M9 18.3c0 1.5 1.3 2.7 3 2.7s3-1.2 3-2.7"/>
<path d="M10 11h.01M14 11h.01"/>
'''

# A frog's face: the eyes on top, the wide mouth.
CUSTOM["frog"] = '''
<path d="M8.5 8a3 3 0 1 0-5 2.2C2.6 11.6 2 13.3 2 15c0 3.3 4.5 6 10 6s10-2.7 10-6c0-1.7-.6-3.4-1.5-4.8a3 3 0 1 0-5-2.2"/>
<path d="M8.5 8c1-.6 2.2-1 3.5-1s2.5.4 3.5 1"/>
<path d="M6 6.5h.01M18 6.5h.01M8.5 15c1 1 2.2 1.5 3.5 1.5s2.5-.5 3.5-1.5"/>
'''

# A rod-shaped bacterium with its flagella.
CUSTOM["bacterium"] = '''
<rect x="3.5" y="9" width="13" height="6" rx="3" transform="rotate(-30 10 12)"/>
<path d="M16.6 7.4c1.5-1 2-2.6 1.4-4.1M17.8 11c1.8-.2 3 .6 3.7 2"/>
'''

# Bacteria: two rods.
CUSTOM["bacteria"] = '''
<rect x="2.5" y="5" width="11" height="5" rx="2.5" transform="rotate(-25 8 7.5)"/>
<rect x="10.5" y="13.5" width="11" height="5" rx="2.5" transform="rotate(-25 16 16)"/>
<path d="M14.6 4.4c.9-.8 1.2-1.9.8-2.9M4 17.5c-.9.8-1.2 1.9-.8 2.9"/>
'''

# Disease: a cell gone ragged, with spots.
CUSTOM["disease"] = '''
<path d="M12 3.5c3 0 4.2 2 6.2 2.6 2 .6 2.8 2.6 2.3 4.8-.4 2 1 3.4.2 5.4-.9 2.4-3.6 2.6-5.3 3.7-1.6 1.1-2.9 2.5-5.4 1.8-2.3-.6-2.6-2.6-4.4-3.8C3.9 16.8 2.5 15 3.2 12.6c.6-2-.7-3.6.6-5.6 1.4-2.2 3.4-1.8 5-2.6 1.2-.6 1.8-.9 3.2-.9z"/>
<circle cx="9" cy="10" r="1.5"/>
<circle cx="14.5" cy="13.5" r="2"/>
<path d="M10 15.6h.01M15 8.5h.01"/>
'''

# A tube with a virus beside it.
CUSTOM["vial-virus"] = '''
<path d="M7.5 3h6M8.5 3v13.5a2 2 0 0 0 4 0V3"/>
<path d="M8.5 11h4"/>
<circle cx="18" cy="15.5" r="2.4"/>
<path d="M18 11.8v1.3M18 17.9v1.3M21.7 15.5h-1.3M15.6 15.5h-1.3"/>
'''

# A mortar and its pestle.
CUSTOM["mortar-pestle"] = '''
<path d="M3.5 11h17"/>
<path d="M5 11a7 7 0 0 0 14 0"/>
<path d="m9.5 18.4-1 2.6h7l-1-2.6"/>
<path d="m13 11 6.6-7.6a1.4 1.4 0 0 1 2 2L15.2 11"/>
'''

# Pregnancy: a person with the belly rounded.
CUSTOM["pregnancy"] = '''
<circle cx="11" cy="4" r="2"/>
<path d="M11 7.5v4c0 .8.5 1.5 1.3 1.8 2 .7 3.2 2.2 3.2 3.7 0 1.7-1.6 3-3.7 3H11"/>
<path d="m11 11-2.5 3M11 20v1.5"/>
'''

# Drawing blood: a drop over an open hand.
CUSTOM["blood-draw"] = '''
<path d="M14 2.5s3.5 3.9 3.5 6.3a3.5 3.5 0 0 1-7 0c0-2.4 3.5-6.3 3.5-6.3z"/>
<path d="M2.5 21H6l4-2h4.5a1.5 1.5 0 0 0 0-3H11"/>
<path d="M6 21v-5l3-2h2.5"/>
'''

# An electrophoresis gel: its wells and lanes of bands, a ladder first.
CUSTOM["gel"] = '''
<rect x="3.5" y="3" width="17" height="18" rx="2"/>
<path d="M7 6.5h2M11 6.5h2M15 6.5h2"/>
<path d="M7 10h2M7 13h2M7 16h2M11 12.5h2M15 10.5h2M15 15.5h2"/>
'''

# A centrifuge rotor from above, its tube slots, and the spin.
CUSTOM["centrifuge"] = '''
<circle cx="12" cy="12" r="7.5"/>
<circle cx="12" cy="7.8" r="1.3"/><circle cx="15.6" cy="9.9" r="1.3"/><circle cx="15.6" cy="14.1" r="1.3"/>
<circle cx="12" cy="16.2" r="1.3"/><circle cx="8.4" cy="14.1" r="1.3"/><circle cx="8.4" cy="9.9" r="1.3"/>
<path d="M21 9.2A9.5 9.5 0 0 0 14.8 3"/>
<path d="m21.6 5.9-.6 3.3-3.1-1"/>
'''


HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<!--
  BioManager icon sprite — GENERATED, do not edit.
  Rebuild with: python scripts/build-icons.py

  Sources:
    - Lucide (lucide.dev) — ISC licence, Copyright (c) Lucide Contributors
    - fly, plasmid, petri, cage, tank, culture-vial, antibody, cell,
      microtube, well-plate, blood, organs and the other lab drawings
      — original to this project, in Lucide's style
-->
<svg xmlns="http://www.w3.org/2000/svg" style="display:none">
"""


def read_lucide(root: Path, lucide_name: str) -> str | None:
    """The drawing inside a Lucide icon (all of them are on a 24 grid)."""
    path = root / "icons" / f"{lucide_name}.svg"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    inner = re.sub(r"^.*?<svg[^>]*>", "", text, flags=re.S)
    inner = re.sub(r"</svg>\s*$", "", inner, flags=re.S)
    inner = re.sub(r"<!--.*?-->", "", inner, flags=re.S)
    return " ".join(inner.split())


def symbol(name: str, inner: str) -> str:
    return f'  <symbol id="{name}" viewBox="0 0 24 24"><g {STROKE}>{inner}</g></symbol>'


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    if not (root / "icons").is_dir():
        sys.exit(f"Lucide icons not found under {root}/icons (npm install in frontend/)")

    parts = [HEADER]
    missing = []
    clash = sorted(set(FROM_LUCIDE) & set(CUSTOM))
    if clash:
        sys.exit("drawn here and taken from Lucide both: " + ", ".join(clash))
    for name, lucide_name in sorted(FROM_LUCIDE.items()):
        inner = read_lucide(root, lucide_name)
        if inner is None:
            missing.append(f"{name} ({lucide_name})")
            continue
        parts.append(symbol(name, inner))
    for name, inner in sorted(CUSTOM.items()):
        parts.append(symbol(name, " ".join(inner.split())))

    parts.append("</svg>")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(parts) + "\n", encoding="utf-8")

    total = len(FROM_LUCIDE) - len(missing) + len(CUSTOM)
    print(f"wrote {OUTPUT.relative_to(PROJECT_ROOT)} — {total} icons "
          f"({len(CUSTOM)} drawn here), {OUTPUT.stat().st_size // 1024} KB")
    if missing:
        print("  not found in Lucide:", ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
