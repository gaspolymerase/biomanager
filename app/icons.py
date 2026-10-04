"""Which icon names exist, and what older names now mean.

Every icon in the app is a <symbol> in static/icons.svg. A name that is not
in the sprite renders as an empty box with no error anywhere, which is how
Drosophila lost its icon: its module row was seeded with the Lucide name
"bug" before the sprite replaced Lucide. `resolve()` is the one gate every
name passes through, so a stale name is translated or, failing that,
replaced by a visible placeholder instead of silently disappearing.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

SPRITE = Path(__file__).resolve().parent / "static" / "icons.svg"

# Names from the Lucide set the app used before the sprite, mapped to their
# sprite equivalents. Stored module rows and older code can still carry them.
ALIASES = {
    "bug": "fly",
    "refresh-cw": "refresh",
    "git-branch": "sitemap",
    "heart-handshake": "heart",
    "droplets": "droplet",
    "settings-2": "sliders",
    "layout-dashboard": "home",
}

FALLBACK = "circle"

# Housing icon by the noun a module uses for it, so a fly module shows vials
# and a worm module shows plates without anyone configuring it.
HOUSING_ICONS = {
    "vial": "culture-vial",
    "plate": "petri",
    "dish": "petri",
    "tank": "tank",
    "cage": "cage",
}


@lru_cache(maxsize=1)
def known() -> frozenset[str]:
    try:
        return frozenset(re.findall(r'<symbol id="([^"]+)"', SPRITE.read_text(encoding="utf-8")))
    except OSError:
        return frozenset()


def resolve(name: str | None) -> str:
    """The sprite name to draw for `name`."""
    name = (name or "").strip()
    names = known()
    if not names or name in names:
        return name or FALLBACK
    alias = ALIASES.get(name)
    if alias in names:
        return alias
    log.warning("icon %r is not in icons.svg; drawing %r instead", name, FALLBACK)
    return FALLBACK


def housing_icon(noun: str | None) -> str:
    return HOUSING_ICONS.get((noun or "").strip().lower(), "box")


# The icons offered in an icon picker (templates/_icon_picker.html): what a
# lab keeps, then general marks. Any name in the sprite is still accepted.
PICKER_ICONS = [
    "mouse", "fish", "fly", "worm", "frog", "paw", "egg", "seedling", "bacterium", "virus",
    "cage", "tank", "petri", "culture-vial", "vial", "vials", "flask", "flask-vial", "antibody", "plasmid",
    "dna", "microscope", "syringe", "droplet", "snowflake", "temperature", "box", "archive", "cart", "tag",
    "list", "file", "notebook", "calendar", "calendar-clock", "alarm", "heart", "heart-pulse", "scale", "star",
]
