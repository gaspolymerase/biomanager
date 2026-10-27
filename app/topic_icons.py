"""A notebook topic's icon (notebook_tabs.icon, 40 characters), one of:

- one of BioManager's own (lab_notebook.FOLDER_ICONS), by name; empty is a
  folder;
- an emoji, as the computer's own emoji picker types it ("emoji:🧪"), drawn
  in the system's emoji font;
- an SVG of the person's own ("svg:uploads/ti_<16 hex>.svg"): cleaned to
  what draws (no script, no event attributes, no links out of the file, no
  DOCTYPE) and saved as an upload, so it is backed up and copied with the
  lab's other files, and the topic's row names it, so anyone signed in may
  fetch it, as the lab's other shared records (app/uploads.py). It is drawn as a picture (<img>),
  where nothing in it could run anyway.
"""
from __future__ import annotations

import re
import secrets
import unicodedata
import xml.etree.ElementTree as ET

EMOJI = "emoji:"
SVG = "svg:"
EMOJI_MAX = 16          # characters: a family or a flag is several
SVG_MAX = 100_000       # bytes as uploaded
SVG_ELEMENTS_MAX = 3000
SVG_VALUE = re.compile(r"svg:uploads/(ti_[0-9a-f]{16}\.svg)\Z")

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

# What draws, and nothing else: no <script>, <foreignObject>, <image>, <a>
# or <animate>. <style> stays (a drawing program keeps its colours there)
# without @import or a url() reaching outside the file.
ELEMENTS = {
    "svg", "g", "style", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon", "defs", "symbol", "use",
    "linearGradient", "radialGradient", "stop", "clipPath", "mask", "pattern", "title", "desc", "text", "tspan",
}
ATTRIBUTES = {
    "viewBox", "width", "height", "x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry", "d", "points",
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
    "stroke-opacity", "stroke-dasharray", "stroke-dashoffset", "stroke-miterlimit", "opacity", "transform",
    "id", "offset", "stop-color", "stop-opacity", "gradientUnits", "gradientTransform", "spreadMethod", "fx", "fy",
    "clip-path", "clip-rule", "mask", "maskUnits", "patternUnits", "patternTransform", "preserveAspectRatio",
    "version", "style", "class", "color", "display", "visibility", "font-size", "font-family", "font-weight",
    "text-anchor", "dominant-baseline", "letter-spacing", "href",
}
# A reference may only point inside the file: url(#a), href="#a".
_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)


def is_emoji(text: str) -> bool:
    """One emoji, or a few (a flag, a family): symbols and their joiners and
    variation selectors, no letters, digits, spaces or markup."""
    if not text or len(text) > EMOJI_MAX:
        return False
    if any(c.isalnum() and ord(c) < 0x2000 or c.isspace() or c in "<>&\"'`" for c in text):
        return False
    return any(unicodedata.category(c) == "So" or 0x1F000 <= ord(c) <= 0x1FAFF for c in text)


def valid(value: str, built_in) -> bool:
    if value in built_in:
        return True
    if value.startswith(EMOJI):
        return is_emoji(value[len(EMOJI):])
    return bool(SVG_VALUE.match(value))


def from_form(raw, built_in) -> str | None:
    """What to keep for an icon chosen in the dialog ("folder" is kept as
    none), or None when it is none of the three."""
    value = str(raw or "folder").strip()
    if value.startswith(EMOJI):
        value = EMOJI + unicodedata.normalize("NFC", value[len(EMOJI):].strip())
    if not valid(value, built_in) or len(value) > 40:
        return None
    return "" if value == "folder" else value


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def _inside_only(value: str) -> str | None:
    """A style or paint value with any url() pointing outside the file
    taken out; None when nothing is left or it tries something else."""
    low = value.lower()
    if any(bad in low for bad in ("javascript:", "expression(", "@import", "behavior:", "-moz-binding")):
        return None
    cleaned = _URL.sub(lambda m: m.group(0) if m.group(2).startswith("#") else "none", value)
    return cleaned


def clean_svg(data: bytes) -> bytes | None:
    """The drawing in an uploaded SVG, or None when it isn't one."""
    if not data or len(data) > SVG_MAX:
        return None
    head = data[:2048].lower()
    if b"<!doctype" in head or b"<!entity" in data.lower():
        return None
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    if _local(root.tag) != "svg":
        return None
    count = 0
    for parent in list(root.iter()):
        for child in list(parent):
            if not isinstance(child.tag, str) or _local(child.tag) not in ELEMENTS:
                parent.remove(child)
    for el in root.iter():
        count += 1
        if count > SVG_ELEMENTS_MAX:
            return None
        el.tag = f"{{{SVG_NS}}}{_local(el.tag)}"
        if _local(el.tag) == "style" and el.text:
            el.text = _inside_only(el.text) or ""
        for name in list(el.attrib):
            local = _local(name)
            value = el.attrib.pop(name)
            if local not in ATTRIBUTES or local.lower().startswith("on"):
                continue
            if local == "href":
                if not value.startswith("#"):
                    continue
                name = f"{{{XLINK_NS}}}href" if name.startswith(f"{{{XLINK_NS}}}") else "href"
            elif "url(" in value.lower() or local == "style":
                value = _inside_only(value)
                if value is None:
                    continue
            else:
                name = local
            el.set(name, value)
    # It draws at the size of the place it is shown in.
    if "viewBox" not in root.attrib:
        try:
            w, h = (float(re.sub(r"[a-z%]+$", "", root.get(k, ""))) for k in ("width", "height"))
            root.set("viewBox", f"0 0 {w:g} {h:g}")
        except ValueError:
            pass
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    return ET.tostring(root, encoding="utf-8", xml_declaration=False)


def save_svg(data: bytes, folder) -> str | None:
    """Clean and keep an uploaded SVG; its icon value, or None."""
    cleaned = clean_svg(data)
    if cleaned is None:
        return None
    name = f"ti_{secrets.token_hex(8)}.svg"
    (folder / name).write_bytes(cleaned)
    return f"{SVG}uploads/{name}"
