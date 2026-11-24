"""How BioManager looks for each person: the app icon, the accent colour and
the rest of Settings → Appearance.

The app icon is a picture (the double helix, a mouse, a zebrafish…) on a
ground: one of eight colours, white or black. It is drawn here as SVG on
Apple's macOS icon grid: a 1024 canvas with the rounded square inset to
824 × 824 and a 185.4 corner radius.

The glass follows Apple's guidance for Liquid Glass icons: flat shapes with
clear edges, and depth from layers of different opacity rather than painted
bevels, inner shadows or glows. The only effects drawn are the ones the
system itself adds: the picture lets a little of the ground through, a thin
reflection runs along its top-left and bottom-right edges, and a soft
neutral shadow sits under it. Details are cut out so the ground shows
through; secondary parts (an ear, a cap, colonies) are the same colour at
lower opacity. On a coloured ground the picture is white; on white or black
it takes the colour.

The colour also becomes the app's accent (`--color-brand-*`), so buttons,
links and selections match the icon. Jade, the default, has the accent that
tailwind.css ships with, so it needs no override. Ink is the one colour that
is no colour: black, and white in dark mode (INK_LIGHT, INK_DARK).

Each person's choices are kept in app_settings under "<name>:<username>",
like the home layout, so they follow the person and need no schema change:
the icon as "app_icon" = "glyph/color/ground", and the mode, look, font,
row height and row lines each under their own name. A theme (THEMES) sets
all of them at once; it is a starting point, not a setting of its own.

`scripts/build-app-icon.py` renders the default (helix, jade, colour) to
the static icon, the PWA and touch PNGs, the desktop app and the Android
launcher. The Mac app's Dock shows the person's own icon while it runs
(desktop_menu.py DesktopApi.set_dock_icon).
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from functools import lru_cache

from . import inventory_service

DEFAULT_GLYPH = "helix"
DEFAULT_COLOR = "jade"
DEFAULT_GROUND = "color"


# ---------------------------------------------------------------- colours

def _hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02X}" for c in rgb)


def _rgb(hex_: str):
    hex_ = hex_.lstrip("#")
    return tuple(int(hex_[i:i + 2], 16) for i in (0, 2, 4))


def _luma(hex_: str) -> float:
    """Relative luminance, 0 for black to 1 for white."""
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in _rgb(hex_))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _mix(a: str, b: str, t: float) -> str:
    """`a` moved `t` of the way towards `b`."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex(x + (y - x) * t for x, y in zip(ra, rb))


def _light_ramp(base: str) -> dict[str, str]:
    white, black = "#FFFFFF", "#000000"
    return {
        "50": _mix(white, base, 0.09), "100": _mix(white, base, 0.20),
        "200": _mix(white, base, 0.38), "300": _mix(white, base, 0.58),
        "400": _mix(white, base, 0.78), "500": _mix(white, base, 0.92),
        "600": base,
        "700": _mix(base, black, 0.18), "800": _mix(base, black, 0.35),
        "900": _mix(base, black, 0.50), "950": _mix(base, black, 0.68),
    }


def _dark_ramp(base: str) -> dict[str, str]:
    # Mirrors the dark-mode overrides in tailwind.css: a brighter accent, and
    # tinted fills that sit on the dark grey rather than glowing.
    ground, white = "#1C1C1E", "#FFFFFF"
    return {
        "600": _mix(base, white, 0.18), "700": _mix(base, white, 0.06),
        "50": _mix(ground, base, 0.17), "100": _mix(ground, base, 0.23),
        "200": _mix(ground, base, 0.36),
        "800": _mix(base, white, 0.40), "900": _mix(base, white, 0.60),
    }


@dataclass(frozen=True)
class Palette:
    label: str
    top: str      # icon ground, top of the gradient
    bottom: str   # icon ground, bottom
    accent: str   # the app's brand-600 in light mode


# Eight colours of about the same strength, white text clear on each;
# graphite for whoever wants a quiet one, and ink for black and white.
PALETTES: dict[str, Palette] = {
    "jade":     Palette("Jade",      "#3ED3B6", "#0A8776", "#17A38F"),
    "ocean":    Palette("Ocean",     "#6FB0FF", "#2460CC", "#2C6FE0"),
    "indigo":   Palette("Indigo",    "#9B9CFF", "#4A49C4", "#5A5BD8"),
    "plum":     Palette("Wisteria",  "#CE94F5", "#8140B4", "#9650C8"),
    "rose":     Palette("Wild rose", "#FF8FB3", "#C23A6C", "#D4477C"),
    "coral":    Palette("Coral",     "#FF9E7A", "#CC4F2E", "#DD5F3C"),
    "amber":    Palette("Amber",     "#FFD15C", "#E0960C", "#C48A12"),
    "graphite": Palette("Graphite",  "#8D99AE", "#454F60", "#566174"),
    "ink":      Palette("Ink",       "#3A3A3C", "#0B0B0C", "#0D0D0D"),
}

# The six colours before these, each to the nearest of the eight.
OLD_COLORS = {"mint": "jade", "sky": "ocean", "lavender": "indigo", "lemon": "amber", "peach": "coral"}

# What the picture sits on.
GROUNDS = {"color": "In colour", "light": "On white", "dark": "On black"}

# What tailwind.css already ships; jade must not override it.
_SHIPPED = PALETTES[DEFAULT_COLOR].accent


# Ink: black buttons and grey fills, and in dark mode white buttons with
# black words on them. Its own ramps, as a mix towards black or white would
# make every fill too dark.
INK_LIGHT = {"50": "#F2F2F2", "100": "#E8E8E8", "200": "#D6D6D6", "300": "#B4B4B4", "400": "#7C7C7C",
             "500": "#262626", "600": "#0D0D0D", "700": "#0D0D0D", "800": "#000000", "900": "#000000",
             "950": "#000000"}
INK_DARK = {"50": "#262626", "100": "#2E2E2E", "200": "#3A3A3A", "300": "#555555", "400": "#8A8A8A",
            "500": "#E0E0E0", "600": "#F2F2F2", "700": "#F2F2F2", "800": "#FFFFFF", "900": "#FFFFFF",
            "950": "#FFFFFF"}

# What draws white words on a solid accent with `text-white` (tailwind.css,
# templates). On Ink's white accent in dark mode those words must be black,
# so there white means the words that go on the accent.
_ON_ACCENT = (".btn-primary,.bg-brand-600,.menu-item:hover,.dt-more-menu>:is(.dt-toolbar-btn,a,button):hover,"
              ".dt-menu-item:hover,.util-rail details a.is-active,.biocal-due-btn.is-active,.mouse-peek-page:hover,"
              ".choice-card:has(input:checked) .choice-check,.set-link-badge,.combo-opt.is-active,"
              ".gs-step.is-done .gs-mark")


def brand_css(color: str) -> str:
    """CSS that retints the app to match `color`; empty for the default."""
    pal = PALETTES.get(color)
    if pal is None or pal.accent == _SHIPPED:
        return ""
    if color == "ink":
        light_ramp, dark_ramp = INK_LIGHT, INK_DARK
        light_on, dark_on = "--color-on-brand:#FFFFFF;", "--color-on-brand:#0D0D0D;"
    else:
        light_ramp, dark_ramp = _light_ramp(pal.accent), _dark_ramp(pal.accent)
        light_on = dark_on = ""
    light = "".join(f"--color-brand-{k}:{v};" for k, v in light_ramp.items()) + light_on
    dark = "".join(f"--color-brand-{k}:{v};" for k, v in dark_ramp.items()) + dark_on
    css = (f":root{{{light}}}"
           f"@media (prefers-color-scheme: dark){{:root:not([data-theme=\"light\"]){{{dark}}}}}"
           f":root[data-theme=\"dark\"]{{{dark}}}")
    if color == "ink":
        css += f":is({_ON_ACCENT}){{--color-white:var(--color-on-brand)}}"
    return css


# ---------------------------------------------------------------- drawing

def _f(x: float) -> str:
    return f"{x:.1f}"


# The picture is filled with one gradient, #bm-w: white on a coloured
# ground, the colour on white or black.
S = 'fill="url(#bm-w)"'
LINE = 'fill="none" stroke="url(#bm-w)" stroke-linecap="round" stroke-linejoin="round"'
# A secondary part: the same colour, more of the ground showing through.
BACK = 'fill-opacity="0.55"'


class _Pen:
    """Collects the masks a drawing cuts its details with."""

    def __init__(self):
        self.defs: list[str] = []

    def masked(self, shape: str, holes: str) -> str:
        """`shape` with `holes` (drawn in black) cut out of it."""
        mask_id = f"bm-m{len(self.defs) + 1}"
        self.defs.append(f'<mask id="{mask_id}" maskUnits="userSpaceOnUse" x="0" y="0" width="1024" height="1024">'
                         f'<rect width="1024" height="1024" fill="#fff"/><g fill="#000" stroke="#000">{holes}</g></mask>')
        return f'<g mask="url(#{mask_id})">{shape}</g>'

    def done(self, *parts: str) -> tuple[str, str]:
        return "".join(parts), "".join(self.defs)


def _gap(shape: str, width: float = 46) -> str:
    """As a hole: `shape` widened by a stroke, so what lies behind it stands
    clear of it by a gap the ground shows through."""
    return f'<g stroke-width="{width}" stroke-linejoin="round">{shape}</g>'


def _dot(x: float, y: float, r: float) -> str:
    return f'<circle cx="{x}" cy="{y}" r="{r}" stroke="none"/>'


def _stripes(ys, x1: float, x2: float, width: float) -> str:
    return "".join(f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke-width="{width}" stroke-linecap="round"/>'
                   for y in ys)


def _pair(d: str, width: float) -> str:
    """A line and its mirror image across the middle (legs, antennae)."""
    import re
    mirrored = re.sub(r"(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)",
                      lambda m: f"{1024 - float(m.group(1)):g} {m.group(2)}", d)
    return f'<path d="{d}" {LINE} stroke-width="{width}"/><path d="{mirrored}" {LINE} stroke-width="{width}"/>'


def _scaled(body: str, k: float) -> str:
    """`body` enlarged by `k` about the middle."""
    shift = round(512 * (1 - k))
    return f'<g transform="translate({shift} {shift}) scale({k})">{body}</g>'


def _helix(pen: _Pen) -> tuple[str, str]:
    """Two strands: solid where a strand is in front, translucent behind.

    Each strand is x = 512 + amp·sin(φ + phase), y rising evenly with φ. It
    is in front while cos(φ + phase) ≥ 0, so it changes sides where that
    crosses zero; each run between is drawn as cubic Béziers a quarter-turn
    long, whose handles follow the curve's slope (under a pixel from the sine).
    """
    amp, y0, y1 = 118, 262, 762
    phi0, phi1 = -math.pi / 2, 2.5 * math.pi
    rise = (y1 - y0) / (phi1 - phi0)

    def point(phi, phase):
        return 512 + amp * math.sin(phi + phase), y0 + rise * (phi - phi0)

    def slope(phi, phase):
        return amp * math.cos(phi + phase), rise

    def curve(a, b, phase):
        steps = max(1, math.ceil((b - a) / (math.pi / 4) - 1e-9))
        h = (b - a) / steps
        x, y = point(a, phase)
        out = [f"M{_f(x)} {_f(y)}"]
        for i in range(steps):
            p, q = a + i * h, a + (i + 1) * h
            (x0, y0_), (x3, y3) = point(p, phase), point(q, phase)
            (dx0, dy0), (dx3, dy3) = slope(p, phase), slope(q, phase)
            out.append(f"C{_f(x0 + dx0 * h / 3)} {_f(y0_ + dy0 * h / 3)} "
                       f"{_f(x3 - dx3 * h / 3)} {_f(y3 - dy3 * h / 3)} {_f(x3)} {_f(y3)}")
        return "".join(out)

    front, back = [], []
    for phase in (0, math.pi):
        # Where this strand passes behind the other or comes back in front.
        k = math.ceil((phi0 + phase - math.pi / 2) / math.pi - 1e-9)
        cuts = [phi0]
        while (c := math.pi / 2 + k * math.pi - phase) < phi1 - 1e-9:
            if c > phi0 + 1e-9:
                cuts.append(c)
            k += 1
        cuts.append(phi1)
        for a, b in zip(cuts, cuts[1:]):
            mid = (a + b) / 2
            (front if math.cos(mid + phase) >= 0 else back).append(f'<path d="{curve(a, b, phase)}"/>')
    return pen.done(f'''<g fill="none" stroke-linecap="round" stroke-linejoin="round">
    <g stroke="url(#bm-w)" stroke-opacity="0.45" stroke-width="54">{"".join(back)}</g>
    <g stroke="url(#bm-w)" stroke-width="66">{"".join(front)}</g>
  </g>''')


def _mouse(pen: _Pen) -> tuple[str, str]:
    """A mouse from the side: round back, the far ear behind it, a long tail."""
    body = "M300 664 C292 506 404 420 534 420 C650 420 724 500 770 590 Q792 664 732 664 Z"
    return pen.done(
        f'<path d="M352 640 C262 652 204 616 216 548" {LINE} stroke-width="38"/>',
        pen.masked(f'<circle cx="486" cy="384" r="80" {S} {BACK}/>', _gap(f'<path d="{body}"/>')),
        pen.masked(f'<path d="{body}" {S}/>', _dot(676, 560, 18)))


def _zebrafish(pen: _Pen) -> tuple[str, str]:
    """A zebrafish: forked tail, two stripes, a big eye with a catch-light."""
    body = ("M770 512 C770 430 690 400 600 404 C500 408 430 450 380 490 L310 440 Q270 414 266 460 V564 "
            "Q270 610 310 584 L380 534 C430 574 500 616 600 620 C690 624 770 594 770 512 Z")
    eye_x, eye_y, eye_r = 688, 486, 32
    catch = (f'<circle cx="{eye_x + 0.32 * eye_r:.0f}" cy="{eye_y - 0.32 * eye_r:.0f}" '
             f'r="{0.34 * eye_r:.0f}" {S}/>')
    fish = pen.masked(f'<path d="{body}" {S}/>',
                      _stripes((488, 538), 296, 636, 22) + _dot(eye_x, eye_y, eye_r))
    return pen.done(_scaled(fish + catch, 1.16))


def _worm(pen: _Pen) -> tuple[str, str]:
    """C. elegans: one thick S."""
    return pen.done(f'<path d="M336 610 C400 760 548 640 512 512 C476 384 624 264 688 414" {LINE} stroke-width="76"/>')


def _fly(pen: _Pen) -> tuple[str, str]:
    """Drosophila from above: eyes as wide as the thorax, a short banded
    abdomen, translucent wings spread to the lower corners, the front legs
    and antennae."""
    eye_r, eye_dx, eye_y = 70, 56, 328
    eyes = (f'<circle cx="{512 - eye_dx}" cy="{eye_y}" r="{eye_r}"/>'
            f'<circle cx="{512 + eye_dx}" cy="{eye_y}" r="{eye_r}"/>')
    thorax = '<ellipse cx="512" cy="466" rx="102" ry="78"/>'
    abdomen = ('<path d="M444 560 C444 530 580 530 580 560 C580 650 560 720 540 752 '
               'Q512 790 484 752 C464 720 444 650 444 560 Z"/>')
    # Each wing hangs from the shoulder, turned out by `angle`.
    rx, ry, angle = 84, 172, 33
    shoulder_x, shoulder_y = 452, 478
    cx = shoulder_x - math.sin(math.radians(angle)) * ry * 0.9
    cy = shoulder_y + math.cos(math.radians(angle)) * ry * 0.9
    wings = "".join(f'<g transform="rotate({a} {x:.0f} {cy:.0f})"><ellipse cx="{x:.0f}" cy="{cy:.0f}" '
                    f'rx="{rx}" ry="{ry}" {S} fill-opacity="0.5"/></g>'
                    for x, a in ((cx, angle), (1024 - cx, -angle)))
    return pen.done(
        _pair(f"M500 {eye_y - eye_r + 10} L468 {eye_y - eye_r - 56}", 24),
        _pair("M430 432 L366 420 L348 330 L312 298", 28),
        pen.masked(wings, _gap(thorax + abdomen, 30)),
        pen.masked(f'<g {S}>{abdomen}</g>', _gap(thorax) + _stripes((612, 662, 710), 420, 604, 18)),
        pen.masked(f'<g {S}>{thorax}</g>', _gap(eyes, 34)),
        pen.masked(f'<g {S}>{eyes}</g>',
                   f'<line x1="512" y1="{eye_y - eye_r - 10}" x2="512" y2="{eye_y + eye_r + 10}" stroke-width="16"/>'))


def _cryobox(pen: _Pen) -> tuple[str, str]:
    """A freezer box from above: nine slots, five of them holding a tube."""
    slots = "".join(_dot(512 + col * 128, 512 + row * 128, 46) for col in (-1, 0, 1) for row in (-1, 0, 1))
    tubes = "".join(f'<circle cx="{x}" cy="{y}" r="24" {S} {BACK}/>'
                    for x, y in ((384, 384), (512, 384), (640, 384), (384, 512), (640, 640)))
    box = f'<rect x="286" y="286" width="452" height="452" rx="84" {S}/>'
    return pen.done(pen.masked(box, slots), tubes)


def _microtube(pen: _Pen) -> tuple[str, str]:
    """A microcentrifuge tube: the cap and its tab behind, the tube in front."""
    tube = "M394 352 H630 V560 L572 730 Q556 770 520 770 H504 Q468 770 452 730 L394 560 Z"
    return pen.done(
        f'<rect x="372" y="262" width="256" height="64" rx="20" {S} {BACK}/>',
        f'<path d="M652 262 H680 Q720 262 720 302 V368 Q720 402 686 402 H652 Z" {S} {BACK}/>',
        f'<path d="{tube}" {S}/>')


def _petri(pen: _Pen) -> tuple[str, str]:
    """A dish's rim and three colonies growing in it."""
    colonies = "".join(f'<circle cx="{x}" cy="{y}" r="{r}" {S} {BACK}/>'
                       for x, y, r in ((452, 468, 50), (584, 536, 36), (488, 596, 24)))
    return pen.done(f'<circle cx="512" cy="512" r="208" fill="none" stroke="url(#bm-w)" stroke-width="44"/>', colonies)


GLYPHS = {
    "helix": ("Helix", _helix),
    "mouse": ("Mouse", _mouse),
    "zebrafish": ("Zebrafish", _zebrafish),
    "worm": ("C. elegans", _worm),
    "fly": ("Drosophila", _fly),
    "cryobox": ("Cryobox", _cryobox),
    "microtube": ("Microtube", _microtube),
    "petri": ("Petri dish", _petri),
}

SQUARE = 'x="100" y="100" width="824" height="824" rx="185.4"'


@dataclass(frozen=True)
class _Ground:
    top: str          # the ground's gradient
    bottom: str
    ink_top: str      # the picture's gradient
    ink_bottom: str
    glow: str         # a soft light rising from the bottom edge
    glow_opacity: float
    sheen: float      # the light across the top of the ground
    shadow: str
    shadow_opacity: float
    picture_opacity: float   # how much of the ground the picture lets through


def _ground(pal: Palette, ground: str) -> _Ground:
    if ground == "light":
        return _Ground("#FFFFFF", "#E4E9F0", pal.top, pal.bottom, pal.top, 0.30, 0.9,
                       _mix(pal.accent, "#000000", 0.3), 0.20, 0.9)
    if ground == "dark":
        # Ink would vanish on black: its picture is white there.
        ink = ("#FFFFFF", "#D4D4D8") if _luma(pal.top) < 0.1 else (_mix(pal.top, "#FFFFFF", 0.3), pal.top)
        return _Ground("#3B414D", "#111317", *ink, pal.accent, 0.42, 0.16, "#000000", 0.22, 0.9)
    return _Ground(pal.top, pal.bottom, "#FFFFFF", "#E6ECF4", _mix(pal.top, "#FFFFFF", 0.35), 0.55, 0.34,
                   _mix(pal.accent, "#000000", 0.65), 0.22, 0.86)


def _glass_filter(picture_opacity: float, edge: float = 5) -> str:
    """The picture's glass: a little translucent, with a thin reflection
    along the edges that face up-left (bright) and down-right (fainter)."""
    def band(dx: float, dy: float, opacity: float, name: str) -> str:
        return (f'<feOffset in="SourceAlpha" dx="{dx:.1f}" dy="{dy:.1f}" result="{name}-off"/>'
                f'<feComposite in="SourceAlpha" in2="{name}-off" operator="out" result="{name}-band"/>'
                f'<feGaussianBlur in="{name}-band" stdDeviation="1"/>'
                f'<feComposite in2="SourceAlpha" operator="in" result="{name}-soft"/>'
                f'<feFlood flood-color="#fff" flood-opacity="{opacity}"/>'
                f'<feComposite in2="{name}-soft" operator="in" result="{name}"/>')
    return (f'<filter id="bm-glass" filterUnits="userSpaceOnUse" x="0" y="0" width="1024" height="1024" '
            f'color-interpolation-filters="sRGB">'
            f'<feComponentTransfer in="SourceGraphic" result="body"><feFuncA type="linear" slope="{picture_opacity}"/></feComponentTransfer>'
            + band(edge * 0.6, edge, 1, "top") + band(-edge * 0.6, -edge, 0.6, "low")
            + '<feMerge><feMergeNode in="body"/><feMergeNode in="low"/><feMergeNode in="top"/></feMerge></filter>')


def _defs(pal: Palette, g: _Ground) -> str:
    return (f'<clipPath id="bm-sq"><rect {SQUARE}/></clipPath>'
            f'<linearGradient id="bm-bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{g.top}"/>'
            f'<stop offset="1" stop-color="{g.bottom}"/></linearGradient>'
            f'<radialGradient id="bm-glow" cx="0.5" cy="1.05" r="0.75"><stop offset="0" stop-color="{g.glow}" '
            f'stop-opacity="{g.glow_opacity}"/><stop offset="1" stop-color="{g.glow}" stop-opacity="0"/></radialGradient>'
            f'<linearGradient id="bm-sheen" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" '
            f'stop-opacity="{g.sheen}"/><stop offset="0.72" stop-color="#fff" stop-opacity="0"/></linearGradient>'
            f'<linearGradient id="bm-edge" x1="0.2" y1="0" x2="0.8" y2="1"><stop offset="0" stop-color="#fff" '
            f'stop-opacity="0.95"/><stop offset="0.45" stop-color="#fff" stop-opacity="0.12"/>'
            f'<stop offset="1" stop-color="#fff" stop-opacity="0.55"/></linearGradient>'
            f'<linearGradient id="bm-w" gradientUnits="userSpaceOnUse" x1="0" y1="260" x2="0" y2="770">'
            f'<stop offset="0" stop-color="{g.ink_top}"/><stop offset="1" stop-color="{g.ink_bottom}"/></linearGradient>'
            f'<filter id="bm-lift" x="-20%" y="-20%" width="140%" height="150%"><feDropShadow dx="0" dy="10" '
            f'stdDeviation="14" flood-color="{g.shadow}" flood-opacity="{g.shadow_opacity}"/></filter>'
            + _glass_filter(g.picture_opacity))


# How much to enlarge the picture when there is no rounded square around it
# (a phone rounds or masks the full-bleed square itself).
_FULL_SCALE = 1.2


@lru_cache(maxsize=None)
def render(glyph: str, color: str, variant: str = "app", ground: str = DEFAULT_GROUND) -> str:
    """The icon as SVG.

    variant: "app" (the rounded square with its shadow), "full" (edge to edge,
    for touch and maskable icons), "glyph" (the picture alone on transparent,
    Android's adaptive foreground) or "background" (the ground alone).
    """
    glyph = glyph if glyph in GLYPHS else DEFAULT_GLYPH
    pal = PALETTES.get(color) or PALETTES[DEFAULT_COLOR]
    g = _ground(pal, ground if ground in GROUNDS else DEFAULT_GROUND)
    body, masks = GLYPHS[glyph][1](_Pen())
    picture = f'<g filter="url(#bm-lift)"><g filter="url(#bm-glass)">{body}</g></g>'
    full = ('<rect width="1024" height="1024" fill="url(#bm-bg)"/><rect width="1024" height="1024" fill="url(#bm-glow)"/>'
            '<ellipse cx="512" cy="-4" rx="760" ry="400" fill="url(#bm-sheen)"/>')
    if variant == "full":
        art = full + f'<g transform="translate(512 512) scale({_FULL_SCALE}) translate(-512 -512)">{picture}</g>'
    elif variant == "background":
        art = full
    elif variant == "glyph":
        art = picture
    else:
        art = (f'<g clip-path="url(#bm-sq)"><rect {SQUARE} fill="url(#bm-bg)"/><rect {SQUARE} fill="url(#bm-glow)"/>'
               f'<ellipse cx="512" cy="96" rx="620" ry="330" fill="url(#bm-sheen)"/></g>'
               + picture
               + '<rect x="104" y="104" width="816" height="816" rx="182" fill="none" stroke="url(#bm-edge)" stroke-width="7"/>')
        if ground == "light":
            # White on a white page needs an edge to be seen.
            art += ('<rect x="101" y="101" width="822" height="822" rx="184.5" fill="none" '
                    'stroke="#000" stroke-opacity="0.10" stroke-width="3"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">'
            f'<title>BioManager</title><defs>{_defs(pal, g)}{masks}</defs>{art}</svg>\n')


@lru_cache(maxsize=None)
def version(glyph: str, color: str, ground: str = DEFAULT_GROUND) -> str:
    """Changes whenever the drawing does, so a cached icon is never stale."""
    return hashlib.sha1(render(glyph, color, "app", ground).encode()).hexdigest()[:10]


def is_default(glyph: str, color: str, ground: str = DEFAULT_GROUND) -> bool:
    return (glyph, color, ground) == (DEFAULT_GLYPH, DEFAULT_COLOR, DEFAULT_GROUND)


# ---------------------------------------------------------------- the icon

def _key(username: str) -> str:
    return f"app_icon:{username}"


def parse(value: str) -> tuple[str, str, str]:
    """ "glyph/color/ground" as saved; an older "glyph/color" is on its
    colour, and an old colour becomes the nearest new one."""
    glyph, _, rest = (value or "").partition("/")
    color, _, ground = rest.partition("/")
    color = OLD_COLORS.get(color, color)
    return (glyph if glyph in GLYPHS else DEFAULT_GLYPH,
            color if color in PALETTES else DEFAULT_COLOR,
            ground if ground in GROUNDS else DEFAULT_GROUND)


def get_choice(session, username: str) -> tuple[str, str, str]:
    return parse(inventory_service.get_setting(session, _key(username), ""))


def set_choice(session, username: str, glyph: str, color: str, ground: str = DEFAULT_GROUND) -> tuple[str, str, str]:
    choice = parse(f"{glyph}/{color}/{ground}")
    inventory_service.set_setting(session, _key(username), "/".join(choice))
    return choice


# ---------------------------------------------------------------- the rest

# Each is a person's own choice, kept as "<name>:<username>"; the first
# value is the default, which base.html marks on the page by leaving the
# attribute out (frontend/src/tailwind.css, static/appearance.css).
CHOICES: dict[str, tuple[str, ...]] = {
    # Light or dark: the computer's own, or always one. data-theme.
    "mode": ("auto", "light", "dark"),
    # Liquid Glass over a wallpaper, Solid (paper and white sheets, no
    # glass), or Mono (white and greys, no glass and no colour but warnings;
    # static/appearance.css). data-look.
    "look": ("glass", "solid", "mono"),
    # Geist or Inter, which the app ships, or the computer's own (SF Pro on
    # a Mac, Segoe UI on Windows). data-font.
    "font": ("geist", "inter", "system"),
    # How tall the rows of a sheet are. data-density.
    "density": ("roomy", "regular", "compact"),
    # What tells one row from the next: lines, stripes, both or neither.
    # data-rows.
    "rows": ("lines", "stripes", "both", "none"),
}
FONTS = CHOICES["font"]
LOOKS = CHOICES["look"]


def get_value(session, username: str, name: str) -> str:
    value = inventory_service.get_setting(session, f"{name}:{username}", "")
    return value if value in CHOICES[name] else CHOICES[name][0]


def set_value(session, username: str, name: str, value: str) -> str:
    value = value if value in CHOICES[name] else CHOICES[name][0]
    inventory_service.set_setting(session, f"{name}:{username}", value)
    return value


def get_all(session, username: str) -> dict[str, str]:
    return {name: get_value(session, username, name) for name in CHOICES}


def get_font(session, username: str) -> str:
    return get_value(session, username, "font")


def set_font(session, username: str, font: str) -> str:
    return set_value(session, username, "font", font)


def get_look(session, username: str) -> str:
    return get_value(session, username, "look")


def set_look(session, username: str, look: str) -> str:
    return set_value(session, username, "look", look)


@dataclass(frozen=True)
class Theme:
    label: str
    blurb: str
    choices: dict           # name → value, as in CHOICES
    icon: tuple             # glyph, color, ground
    text_size: str          # the Mac app's window zoom (desktop_menu.py)


# A theme sets everything at once; changing anything after makes it your own.
THEMES: dict[str, Theme] = {
    "glass": Theme("Glass", "The default. Frosted glass over a soft wallpaper, jade, roomy rows with lines.",
                   {"mode": "auto", "look": "glass", "font": "geist", "density": "roomy", "rows": "lines"},
                   ("helix", "jade", "color"), "default"),
    "paper": Theme("Paper white", "Clean as a sheet of paper: solid, no glass, ocean blue, Inter, striped rows.",
                   {"mode": "light", "look": "solid", "font": "inter", "density": "regular", "rows": "stripes"},
                   ("helix", "ocean", "light"), "default"),
    "night": Theme("Night", "Always dark: dark glass, indigo, the icon on black. For a dark room and late hours.",
                   {"mode": "dark", "look": "glass", "font": "geist", "density": "roomy", "rows": "lines"},
                   ("helix", "indigo", "dark"), "default"),
    "mono": Theme("Mono", "Black and white: no glass and no colour, black buttons, the system font, rows with lines.",
                  {"mode": "auto", "look": "mono", "font": "system", "density": "regular", "rows": "lines"},
                  ("helix", "ink", "color"), "default"),
}


def theme_sketch(theme: Theme) -> str:
    """CSS variables for a theme's card in Settings (static/appearance.css):
    the canvas, panes and rows as the theme draws them, in light mode unless
    the theme is always dark."""
    dark, look = theme.choices["mode"] == "dark", theme.choices["look"]
    accent = PALETTES[theme.icon[1]].accent
    if look == "mono":
        canvas, pane = ("#181818", "#1F1F1F") if dark else ("#F4F4F4", "#FFFFFF")
    elif look == "glass":
        canvas = _mix("#14171C" if dark else "#E9EDF3", accent, 0.22 if dark else 0.16)
        pane = "rgb(30 34 41 / 0.62)" if dark else "rgb(255 255 255 / 0.6)"
    else:
        canvas, pane = ("#101216", "#1E2229") if dark else ("#EEF0F2", "#FFFFFF")
    row = {"roomy": 20, "regular": 17, "compact": 14}[theme.choices["density"]]
    return (f"--m-canvas:{canvas};--m-pane:{pane};--m-ink:{'#E8EAED' if dark else '#1C2026'};"
            f"--m-line:{'rgb(255 255 255 / 0.12)' if dark else 'rgb(0 0 0 / 0.09)'};"
            f"--m-stripe:{'rgb(255 255 255 / 0.05)' if dark else 'rgb(0 0 0 / 0.04)'};"
            f"--m-accent:{accent};--m-row:{row}px;"
            f"font-family:{_SKETCH_FONTS[theme.choices['font']]}")


_SKETCH_FONTS = {"geist": '"Geist BM",sans-serif', "inter": '"Inter Text",sans-serif',
                 "system": "-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif"}


def apply_theme(session, username: str, name: str) -> Theme | None:
    theme = THEMES.get(name)
    if theme is None:
        return None
    for key, value in theme.choices.items():
        set_value(session, username, key, value)
    set_choice(session, username, *theme.icon)
    return theme


def current_theme(choices: dict[str, str], icon: tuple) -> str:
    """The theme everything matches now, or "" once the person changed it."""
    for name, theme in THEMES.items():
        if theme.choices == choices and tuple(theme.icon) == tuple(icon):
            return name
    return ""
