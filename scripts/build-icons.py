#!/usr/bin/env python3
"""Build app/static/icons.svg — the app's single icon sprite.

Why a sprite rather than an icon font or a JS library:

  * it is one file, served once, cached, and works offline in the packaged
    desktop app — no CDN, no runtime DOM rewriting
  * `<use href="…#name">` inherits `currentColor`, so icons follow text
  * Font Awesome's biology set and our own organism drawings sit side by
    side with no visual seam

Font Awesome Free supplies the general UI and most of the biology (worm,
mosquito, fish, frog, dna, vial, microscope…). It has no laboratory mouse
and no plasmid, so those — and a few others where FA's shape reads wrong at
18px — are drawn here in a matching solid style on the same 512 grid.

    python scripts/build-icons.py [path/to/fontawesome-free-6.7.2]

Icons: CC BY 4.0 (Font Awesome Free). Attribution is embedded in the sprite.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = PROJECT_ROOT / "app" / "static" / "icons.svg"

# name-in-app -> font-awesome solid file
FROM_FONTAWESOME = {
    # --- column headers: one glyph per kind of value ---
    "sex": "venus-mars",
    "female": "venus",
    "male": "mars",
    "age": "hourglass-half",
    "born": "cake-candles",
    "died": "calendar-xmark",
    "litter": "people-group",
    "status": "circle-half-stroke",
    "note": "note-sticky",
    "count": "hashtag",
    "barcode": "barcode",
    "vendor": "building",
    "resistance": "capsules",
    "protocol": "clipboard-check",
    "position": "location-crosshairs",
    "amount": "flask",
    "target": "bullseye",
    # --- navigation & chrome ---
    "home": "house",
    "calendar": "calendar-days",
    "notebook": "book",
    "search": "magnifying-glass",
    "settings": "gear",
    "sign-out": "right-from-bracket",
    "menu": "bars",
    "plus": "plus",
    "close": "xmark",
    "check": "check",
    "chevron-right": "chevron-right",
    "chevron-down": "chevron-down",
    "chevron-left": "chevron-left",
    "chevron-up": "chevron-up",
    "ellipsis": "ellipsis",
    "sidebar": "table-columns",
    "grid": "table-cells-large",
    "list": "list",
    "table": "table",
    "filter": "filter",
    "sort": "arrow-up-wide-short",
    "eye-off": "eye-slash",
    "external": "arrow-up-right-from-square",
    "back": "arrow-left",
    # --- objects & actions ---
    "database": "database",
    "layers": "layer-group",
    "box": "box",
    "archive": "box-archive",
    "trash": "trash",
    "edit": "pen",
    "copy": "copy",
    "duplicate": "clone",
    "print": "print",
    "download": "download",
    "upload": "upload",
    "import": "file-import",
    "tag": "tag",
    "folder": "folder",
    "file": "file-lines",
    "link": "link",
    "key": "key",
    "mail": "envelope",
    "bell": "bell",
    "clock": "clock",
    "history": "clock-rotate-left",
    "undo": "rotate-left",
    "refresh": "arrows-rotate",
    "warning": "triangle-exclamation",
    "info": "circle-info",
    "success": "circle-check",
    "error": "circle-xmark",
    "user": "user",
    "users": "users",
    "shield": "shield-halved",
    "chart": "chart-line",
    "scale": "scale-balanced",
    "receipt": "receipt",
    "location": "location-dot",
    "qrcode": "qrcode",
    "snowflake": "snowflake",
    "droplet": "droplet",
    "temperature": "temperature-half",
    "sparkle": "wand-magic-sparkles",
    "heart": "heart",
    "star": "star",
    # --- biology (Font Awesome has these and they read well) ---
    "dna": "dna",
    "microscope": "microscope",
    "vial": "vial",
    "vials": "vials",
    "flask": "flask",
    "syringe": "syringe",
    "worm": "worm",
    "fish": "fish",
    "frog": "frog",
    "bacterium": "bacterium",
    "virus": "virus",
    "paw": "paw",
    "egg": "egg",
    "seedling": "seedling",
    "stethoscope": "stethoscope",
    "notes-medical": "notes-medical",
    "heart-pulse": "heart-pulse",
    "weight": "weight-scale",
    # --- library folders (lab_notebook.FOLDER_ICONS): organs ---
    "brain": "brain",
    "lungs": "lungs",
    "bone": "bone",
    "joint": "joint",
    "tooth": "tooth",
    "skull": "skull",
    "hand": "hand",
    "ear": "ear-listen",
    "person": "person",
    "pregnancy": "person-pregnant",
    # --- library folders: microbes, disease, chemistry, safety ---
    "bacteria": "bacteria",
    "vial-virus": "vial-virus",
    "lungs-virus": "lungs-virus",
    "disease": "disease",
    "atom": "atom",
    "mortar-pestle": "mortar-pestle",
    "pills": "pills",
    "biohazard": "biohazard",
    "radiation": "radiation",
    "toxic": "skull-crossbones",
    # --- library folders: procedures ---
    "dropper": "eye-dropper",
    "scissors": "scissors",
    "bandage": "bandage",
    "blood-draw": "hand-holding-droplet",
    "mask": "mask-face",
    "aseptic": "hands-bubbles",
    "recovery": "bed-pulse",
    "recording": "file-waveform",
    "x-ray": "x-ray",
    "clinician": "user-doctor",
    "ruler": "ruler",
    # --- library folders: more organisms ---
    "bird": "dove",
    "cow": "cow",
    "leaf": "leaf",
    "wheat": "wheat-awn",
    "clipboard": "clipboard-check",
    "sitemap": "sitemap",
    "alarm": "stopwatch",
    "arrow-down": "arrow-down",
    "arrow-up": "arrow-up",
    "baby": "baby",
    "bolt": "bolt",
    "calculator": "calculator",
    "calendar-clock": "calendar-check",
    "cart": "cart-shopping",
    "chevrons-right": "angles-right",
    "circle": "circle",
    "columns": "table-columns",
    "eye": "eye",
    "file-archive": "file-zipper",
    "flask-vial": "flask-vial",
    "folder-plus": "folder-plus",
    "gauge": "gauge-simple-high",
    "list-check": "list-check",
    "lock": "lock",
    "repeat": "repeat",
    "rss": "rss",
    "sign-in": "right-to-bracket",
    "signpost": "signs-post",
    "sliders": "sliders",
    "template": "clipboard-list",
    "type": "font",
    "user-gear": "user-gear",
    "user-plus": "user-plus",
    "wand": "wand-magic-sparkles",
}

# Drawn here because Font Awesome has no equivalent, or its shape is
# unrecognisable at sidebar size. Same 0 0 512 512 grid, solid fills.
CUSTOM: dict[str, str] = {}

# Plasmid: a supercoiled circle with a highlighted insert arc.
CUSTOM["plasmid"] = '''
<circle cx="256" cy="256" r="132" fill="none" stroke="currentColor" stroke-width="40"/>
<path d="M 64 201 A 200 200 0 0 1 201 64" fill="none" stroke="currentColor"
 stroke-width="46" stroke-linecap="round"/>
<path d="M 450 304 A 200 200 0 0 1 304 450" fill="none" stroke="currentColor"
 stroke-width="46" stroke-linecap="round"/>
<path d="M 356 83 A 200 200 0 0 1 448 201" fill="none" stroke="currentColor"
 stroke-width="24" stroke-linecap="round"/>
'''

# Petri dish seen from above: rim, base and a few colonies.
CUSTOM["petri"] = '''
<path d="M256 76C155 76 74 157 74 256s81 180 182 180 182-81 182-180S357 76
256 76zm0 48c74 0 134 59 134 132s-60 132-134 132-134-59-134-132 60-132
134-132z"/>
<circle cx="214" cy="212" r="26"/>
<circle cx="300" cy="256" r="18"/>
<circle cx="226" cy="308" r="14"/>
'''

# A mouse cage: tub with a wire lid and a water bottle.
CUSTOM["cage"] = '''
<path d="M86 208h340c13 0 24 11 24 24v140c0 35-29 64-64 64H126c-35 0-64-29
-64-64V232c0-13 11-24 24-24zm24 48v120c0 9 7 16 16 16h260c9 0 16-7 16-16V256H110z"/>
<path d="M104 160h304c11 0 20 9 20 20s-9 20-20 20H104c-11 0-20-9-20-20s9-20
20-20z"/>
<path d="M168 96c11 0 20 9 20 20v28h-40v-28c0-11 9-20 20-20zm88 0c11 0 20 9
20 20v28h-40v-28c0-11 9-20 20-20zm88 0c11 0 20 9 20 20v28h-40v-28c0-11
9-20 20-20z"/>
'''

# An antibody (IgG): the Y of two heavy chains, a light chain beside each
# arm. Bold strokes so the Y survives at 15px.
CUSTOM["antibody"] = '''
<g fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">
<path d="M256 468V288L118 128M256 288l138-160" stroke-width="58"/>
<path d="M186 300 72 168M326 300l114-132" stroke-width="34"/>
</g>
'''

# An aquatic tank: glass box with a water line.
CUSTOM["tank"] = '''
<path d="M88 120h336c22 0 40 18 40 40v192c0 22-18 40-40 40H88c-22 0-40-18
-40-40V160c0-22 18-40 40-40zm8 48v176h320V168H96z"/>
<path d="M96 248c34 0 34 22 68 22s34-22 68-22 34 22 68 22 34-22 68-22v96H96z"/>
'''

# A fly vial / culture tube with a plug.
CUSTOM["culture-vial"] = '''
<path d="M196 56h120c13 0 24 11 24 24s-11 24-24 24h-8v40h8c40 0 72 32 72
72v186c0 30-24 54-54 54H178c-30 0-54-24-54-54V216c0-40 32-72 72-72h8v-40h-8
c-13 0-24-11-24-24s11-24 24-24zm-24 304v42c0 3 3 6 6 6h156c3 0 6-3 6-6v-42H172z"/>
'''

# A cell: its membrane, the nucleus and two organelles.
CUSTOM["cell"] = '''
<path fill-rule="evenodd" d="M256 40c128 0 216 82 216 210 0 130-92 222-220 222C120 472 40 384 40 254 40 124 128 40 256 40zm-2 52C156 92 92 160 92 254c0 98 64 166 160 166 96 0 168-68 168-170 0-96-68-158-166-158z"/>
<circle cx="226" cy="232" r="78"/>
<ellipse cx="342" cy="336" rx="44" ry="24" transform="rotate(-35 342 336)"/>
<circle cx="330" cy="168" r="20"/>
<circle cx="172" cy="352" r="16"/>
'''

# A 1.5 mL microcentrifuge (Eppendorf) tube: rim, conical body, the lid
# open on its hinge, which is what tells it from a vial at 15px.
CUSTOM["microtube"] = '''
<path d="M190 112h220a22 22 0 0 1 0 44h-24v144l-66 152q-6 14-20 14t-20-14l-66-152V156h-24a22 22 0 0 1 0-44z"/>
<rect x="50" y="112" width="160" height="44" rx="22" transform="rotate(-58 196 134)"/>
'''

# A multi-well plate with its A1 corner cut: 12 wells stand for 96, as
# many as stay apart at 15px.
CUSTOM["well-plate"] = '''
<path fill-rule="evenodd" d="M80 96h376c22 0 40 18 40 40v240c0 22-18 40-40 40H56c-22 0-40-18-40-40V160l64-64zM78 176a34 34 0 1 0 68 0a34 34 0 1 0 -68 0zM174 176a34 34 0 1 0 68 0a34 34 0 1 0 -68 0zM270 176a34 34 0 1 0 68 0a34 34 0 1 0 -68 0zM366 176a34 34 0 1 0 68 0a34 34 0 1 0 -68 0zM78 256a34 34 0 1 0 68 0a34 34 0 1 0 -68 0zM174 256a34 34 0 1 0 68 0a34 34 0 1 0 -68 0zM270 256a34 34 0 1 0 68 0a34 34 0 1 0 -68 0zM366 256a34 34 0 1 0 68 0a34 34 0 1 0 -68 0zM78 336a34 34 0 1 0 68 0a34 34 0 1 0 -68 0zM174 336a34 34 0 1 0 68 0a34 34 0 1 0 -68 0zM270 336a34 34 0 1 0 68 0a34 34 0 1 0 -68 0zM366 336a34 34 0 1 0 68 0a34 34 0 1 0 -68 0z"/>
'''

# Blood: two red blood cells, the front one with its pale centre, the one
# behind it a crescent.
CUSTOM["blood"] = '''
<path fill-rule="evenodd" d="M156 326a170 170 0 1 0 340 0a170 170 0 1 0 -340 0zM260 326a66 66 0 1 1 132 0a66 66 0 1 1 -132 0z"/>
<path d="M312 122A150 150 0 1 0 122 312A204 204 0 0 1 312 122Z"/>
'''

# A kidney: the bean, its hilum and the ureter leaving it.
CUSTOM["kidney"] = '''
<path d="M300 56C170 40 70 140 74 272c4 124 96 200 202 188 76-8 108-60 84-110-16-32-56-48-56-88s40-56 58-86c30-56 6-112-62-120z"/>
<path d="M318 262c52 0 94 36 98 86l10 112" fill="none" stroke="currentColor" stroke-width="40" stroke-linecap="round"/>
'''

# The liver: the wedge of its lobes, the ligament between them as a slit,
# the gallbladder beneath.
CUSTOM["liver"] = '''
<path fill-rule="evenodd" d="M36 176C108 112 228 90 340 94c104 4 146 60 136 140-10 86-70 150-152 160C238 404 160 340 36 176zM246 104c-6 54 0 104 26 150l-20 12c-30-50-36-104-28-162z"/>
<path d="M306 380c18-8 42-6 52 14 12 24-2 54-28 58-24 4-42-14-40-36 2-14 6-30 16-36z"/>
'''

# The stomach: the bag with the oesophagus coming in and the duodenum
# leaving.
CUSTOM["stomach"] = '''
<path d="M300 112c-40-24-110-26-160 20-58 54-74 160-30 236 46 80 160 114 248 68 50-26 66-66 50-96-14-26-48-32-82-18-44 18-84 8-92-30-8-40 22-66 58-82 30-14 40-58 8-78z"/>
<path d="M304 120c20-30 30-58 30-88M412 386c34-14 54-44 58-84" fill="none" stroke="currentColor" stroke-width="50" stroke-linecap="round"/>
'''

# The gut: the colon's arch, scalloped as it is, around the coiled small
# intestine, which joins it at the bottom. The scallops and the round top
# keep it from reading as a document at 20px.
CUSTOM["intestine"] = '''
<path d="M50 400a46 46 0 1 0 92 0a46 46 0 1 0 -92 0zM50 330a46 46 0 1 0 92 0a46 46 0 1 0 -92 0zM50 260a46 46 0 1 0 92 0a46 46 0 1 0 -92 0zM51 190a46 46 0 1 0 92 0a46 46 0 1 0 -92 0zM79 127a46 46 0 1 0 92 0a46 46 0 1 0 -92 0zM140 94a46 46 0 1 0 92 0a46 46 0 1 0 -92 0zM209 92a46 46 0 1 0 92 0a46 46 0 1 0 -92 0zM279 94a46 46 0 1 0 92 0a46 46 0 1 0 -92 0zM340 127a46 46 0 1 0 92 0a46 46 0 1 0 -92 0zM369 189a46 46 0 1 0 92 0a46 46 0 1 0 -92 0zM370 259a46 46 0 1 0 92 0a46 46 0 1 0 -92 0zM370 329a46 46 0 1 0 92 0a46 46 0 1 0 -92 0zM370 399a46 46 0 1 0 92 0a46 46 0 1 0 -92 0zM314 440a46 46 0 1 0 92 0a46 46 0 1 0 -92 0zM254 462a46 46 0 1 0 92 0a46 46 0 1 0 -92 0z"/>
<path d="M200 186h100c22 0 36 14 36 34s-14 34-36 34h-88c-22 0-36 14-36 34s14 34 36 34h88c22 0 36 14 36 34s-14 34-36 34h-84c-30 0-52 0-72 20" fill="none" stroke="currentColor" stroke-width="38" stroke-linecap="round" stroke-linejoin="round"/>
'''

# An electrophoresis gel: its wells and lanes of bands, a ladder first.
CUSTOM["gel"] = '''
<path fill-rule="evenodd" d="M76 40h360c20 0 36 16 36 36v360c0 20-16 36-36 36H76c-20 0-36-16-36-36V76c0-20 16-36 36-36zm8 36c-4 0-8 4-8 8v344c0 4 4 8 8 8h344c4 0 8-4 8-8V84c0-4-4-8-8-8z"/>
<path d="M80 150h72v30h-72zM80 230h72v30h-72zM80 310h72v30h-72zM80 390h72v30h-72zM176 230h72v30h-72zM272 190h72v30h-72zM272 330h72v30h-72zM368 270h72v30h-72zM86 88h60v26h-60zM182 88h60v26h-60zM278 88h60v26h-60zM374 88h60v26h-60z"/>
'''

# A centrifuge rotor seen from above, its tube slots, and the spin.
CUSTOM["centrifuge"] = '''
<path fill-rule="evenodd" d="M82 256a174 174 0 1 0 348 0a174 174 0 1 0 -348 0zM256 198A50 25 -90 1 0 256 98A50 25 -90 1 0 256 198zM306 227A50 25 -30 1 0 393 177A50 25 -30 1 0 306 227zM306 285A50 25 30 1 0 393 335A50 25 30 1 0 306 285zM256 314A50 25 90 1 0 256 414A50 25 90 1 0 256 314zM206 285A50 25 150 1 0 119 335A50 25 150 1 0 206 285zM206 227A50 25 210 1 0 119 177A50 25 210 1 0 206 227z"/>
<path d="M47 180A222 222 0 0 1 180 47" fill="none" stroke="currentColor" stroke-width="34" stroke-linecap="round"/>
<path d="M214 38L156 20L183 83z"/>
<path d="M465 332A222 222 0 0 1 332 465" fill="none" stroke="currentColor" stroke-width="34" stroke-linecap="round"/>
<path d="M298 474L356 492L329 429z"/>
'''


# Drawn by other people and vendored under scripts/icon-sources/, because a
# convincing mouse or housefly needs more detail than a hand-written path of
# this size can carry. See that folder's README for authorship and licence.
FROM_SOURCES = {
    "mouse": "mouse.svg",   # game-icons.net "rat", Delapouite, CC BY 3.0
    "fly": "fly.svg",       # game-icons.net "fly", Delapouite, CC BY 3.0
}


def read_source(name: str) -> str | None:
    """Inner markup of a vendored SVG, stripped of its background plate and fills."""
    path = Path(__file__).resolve().parent / "icon-sources" / name
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    inner = re.sub(r"^.*?<svg[^>]*>", "", text, flags=re.S)
    inner = re.sub(r"</svg>\s*$", "", inner, flags=re.S)
    # game-icons ship a white glyph on a full-bleed black plate.
    inner = re.sub(r'<path d="M0 0h512v512H0z"\s*/?>', "", inner)
    inner = re.sub(r'\sfill="#fff"', "", inner)
    return inner.strip()


HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<!--
  BioManager icon sprite — GENERATED, do not edit.
  Rebuild with: python scripts/build-icons.py

  Sources:
    - Font Awesome Free 6.7.2 (fontawesome.com) — Icons: CC BY 4.0
    - game-icons.net by Delapouite — CC BY 3.0 — the mouse and fly
    - plasmid, petri, cage, tank, culture-vial, antibody, cell, microtube,
      well-plate, blood, kidney, liver, stomach, intestine, gel, centrifuge
      — original to this project
-->
<svg xmlns="http://www.w3.org/2000/svg" style="display:none">
"""


def read_fa(root: Path, fa_name: str) -> tuple[str, str] | None:
    """(viewBox, inner markup) of a Font Awesome icon. Its own viewBox, not a
    512 square: Font Awesome icons are 320 to 640 wide, and in a square box
    a narrow one sits to the left and a wide one is cut off on the right.
    In its own box each is centred and fits."""
    path = root / "svgs" / "solid" / f"{fa_name}.svg"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    box = re.search(r'<svg[^>]*viewBox="([^"]+)"', text)
    inner = re.sub(r"^.*?<svg[^>]*>", "", text, flags=re.S)
    inner = re.sub(r"</svg>\s*$", "", inner, flags=re.S)
    inner = re.sub(r"<!--.*?-->", "", inner, flags=re.S)
    return (box.group(1) if box else "0 0 512 512"), inner.strip()


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("package")
    if not (root / "svgs" / "solid").is_dir():
        sys.exit(f"Font Awesome solid SVGs not found under {root}/svgs/solid")

    parts = [HEADER]
    missing = []
    for name, fa_name in sorted(FROM_FONTAWESOME.items()):
        found = read_fa(root, fa_name)
        if found is None:
            missing.append(f"{name} ({fa_name})")
            continue
        box, inner = found
        parts.append(f'  <symbol id="{name}" viewBox="{box}">{inner}</symbol>')

    for name, filename in sorted(FROM_SOURCES.items()):
        inner = read_source(filename)
        if inner is None:
            missing.append(f"{name} (icon-sources/{filename})")
            continue
        parts.append(f'  <symbol id="{name}" viewBox="0 0 512 512">{inner}</symbol>')

    for name, inner in sorted(CUSTOM.items()):
        compact = " ".join(inner.split())
        parts.append(f'  <symbol id="{name}" viewBox="0 0 512 512">{compact}</symbol>')

    parts.append("</svg>")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(parts) + "\n", encoding="utf-8")

    total = len(FROM_FONTAWESOME) + len(FROM_SOURCES) - len(missing) + len(CUSTOM)
    print(f"wrote {OUTPUT.relative_to(PROJECT_ROOT)} — {total} icons "
          f"({len(CUSTOM)} drawn here, {len(FROM_SOURCES)} vendored), {OUTPUT.stat().st_size // 1024} KB")
    if missing:
        print("  not found in Font Awesome:", ", ".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
