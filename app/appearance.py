"""The app icon and the accent colour that goes with it.

Each person picks a picture (the double helix, a mouse, a zebrafish…) and one
of six macaron colours in Settings. The picture is drawn here as SVG on
Apple's macOS icon grid: a 1024 canvas with the rounded square inset to
824 × 824 and a 185.4 corner radius. The style is Apple's Liquid Glass: flat
shapes in two layers, solid white in front and translucent glass behind with
a bright rim, lit from above. Details are cut out of the white so the
background shows through; there is never a second colour. Shadows are kept
tight: the same drawing is shown at 28 px beside a heading and at 512 px on a
desktop, and a wide blur that reads as depth at the large end is only haze at
the small one.

The colour also becomes the app's accent (`--color-brand-*`), so buttons,
links and selections match the icon. Mint is the default and matches the
accent that tailwind.css ships with, so it needs no override.

The choice is kept in app_settings as "app_icon:<username>" = "glyph/color",
like the home layout, so it follows the person and needs no schema change.

`scripts/build-app-icon.py` renders the default (helix, mint) to the static
icon, the PWA and touch PNGs, the desktop app and the Android launcher.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from functools import lru_cache

from . import inventory_service

DEFAULT_GLYPH = "helix"
DEFAULT_COLOR = "mint"


# ---------------------------------------------------------------- colours

def _hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02X}" for c in rgb)


def _rgb(hex_: str):
    hex_ = hex_.lstrip("#")
    return tuple(int(hex_[i:i + 2], 16) for i in (0, 2, 4))


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
    top: str      # icon background, top of the gradient
    bottom: str   # icon background, bottom
    shade: str    # colour of the shadows
    accent: str   # the app's brand-600 in light mode


PALETTES: dict[str, Palette] = {
    "mint":     Palette("Mint",     "#3FD6BC", "#0B8C7E", "#03453E", "#17A38F"),
    "rose":     Palette("Rose",     "#FFA6C1", "#E0527F", "#5C0F2A", "#DC4478"),
    "lavender": Palette("Lavender", "#C4AEFF", "#7B5CE0", "#241060", "#7457DB"),
    "sky":      Palette("Sky",      "#94D7FF", "#2F86E0", "#0A2C5E", "#2A7FDB"),
    "lemon":    Palette("Lemon",    "#FFDD73", "#EBA313", "#5C3A00", "#B97A00"),
    "peach":    Palette("Peach",    "#FFC19C", "#EE7446", "#5E220A", "#DC5F2D"),
}

# What tailwind.css already ships; mint must not override it.
_SHIPPED = PALETTES[DEFAULT_COLOR].accent


def brand_css(color: str) -> str:
    """CSS that retints the app to match `color`; empty for the default."""
    pal = PALETTES.get(color)
    if pal is None or pal.accent == _SHIPPED:
        return ""
    light = "".join(f"--color-brand-{k}:{v};" for k, v in _light_ramp(pal.accent).items())
    dark = "".join(f"--color-brand-{k}:{v};" for k, v in _dark_ramp(pal.accent).items())
    return (f":root{{{light}}}"
            f"@media (prefers-color-scheme: dark){{:root:not([data-theme=\"light\"]){{{dark}}}}}"
            f":root[data-theme=\"dark\"]{{{dark}}}")


# ---------------------------------------------------------------- drawing

def _f(x: float) -> str:
    return f"{x:.1f}"


def _defs(pal: Palette) -> str:
    return f'''<linearGradient id="bm-bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{pal.top}"/><stop offset="1" stop-color="{pal.bottom}"/>
    </linearGradient>
    <radialGradient id="bm-light" cx="0.5" cy="0" r="0.9">
      <stop offset="0" stop-color="#fff" stop-opacity="0.28"/>
      <stop offset="1" stop-color="#fff" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="bm-white" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#FFFFFF"/><stop offset="1" stop-color="#EEF2F6"/>
    </linearGradient>
    <linearGradient id="bm-glass" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#fff" stop-opacity="0.46"/>
      <stop offset="1" stop-color="#fff" stop-opacity="0.22"/>
    </linearGradient>
    <linearGradient id="bm-rim" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#fff" stop-opacity="0.95"/>
      <stop offset="0.5" stop-color="#fff" stop-opacity="0.25"/>
      <stop offset="1" stop-color="#fff" stop-opacity="0.55"/>
    </linearGradient>'''


def _helix(pal: Palette) -> tuple[str, str]:
    """Two strands: white where a strand is in front, glass where behind.

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
    return f'''<g fill="none" stroke-linecap="round" stroke-linejoin="round">
    <g stroke="#fff" stroke-opacity="0.38" stroke-width="54">{"".join(back)}</g>
    <g stroke="url(#bm-white)" stroke-width="66">{"".join(front)}</g>
  </g>''', ""


# Every picture below is built like the helix: a solid white layer in front,
# a glass layer behind it (translucent, with a bright rim where light catches
# the edge), and details cut out of the white so the background shows
# through. No second colour, no painted detail.

def _cut(mask_id: str, holes: str) -> str:
    """A mask that punches `holes` (black shapes) out of whatever uses it."""
    return (f'<mask id="{mask_id}" maskUnits="userSpaceOnUse" x="0" y="0" width="1024" height="1024">'
            f'<rect width="1024" height="1024" fill="#fff"/><g fill="#000" stroke="#000">{holes}</g></mask>')


GLASS = 'fill="url(#bm-glass)" stroke="url(#bm-rim)" stroke-width="4"'
GLASS_LINE = 'fill="none" stroke="#fff" stroke-opacity="0.38" stroke-linecap="round"'


def _mouse(pal: Palette) -> tuple[str, str]:
    body = ("M318 648 C298 540 382 432 518 432 C630 432 708 506 760 592 "
            "C770 610 762 628 742 632 L338 654 C326 654 320 652 318 648 Z")
    extra = _cut("bm-mouse", '<circle cx="682" cy="538" r="17"/>')
    return f"""<g transform="translate(512 512) scale(1.16) translate(-494 -560)">
    <path d="M324 632 C250 640 218 706 268 744 C318 782 404 764 456 742" {GLASS_LINE} stroke-width="28"/>
    <circle cx="552" cy="404" r="90" {GLASS}/>
    <g><path d="{body}" fill="url(#bm-white)" mask="url(#bm-mouse)"/></g>
  </g>""", extra


def _zebrafish(pal: Palette) -> tuple[str, str]:
    body = "M300 512 C372 404 604 386 762 498 C776 508 776 516 762 526 C604 638 372 620 300 512 Z"
    stripes = "".join(f'<line x1="330" y1="{y}" x2="680" y2="{y}" stroke-width="20" stroke-linecap="round"/>'
                      for y in (490, 538))
    extra = _cut("bm-fish", stripes + '<circle cx="714" cy="494" r="16" stroke="none"/>')
    return f"""<g transform="translate(512 512) scale(1.16) translate(-500 -512)">
    <path d="M344 512 L230 404 C262 462 270 488 274 512 C270 536 262 562 230 620 Z" {GLASS}/>
    <path d="M458 432 Q514 348 598 426 Z" {GLASS}/>
    <path d="M492 596 Q540 668 612 598 Z" {GLASS}/>
    <g><path d="{body}" fill="url(#bm-white)" mask="url(#bm-fish)"/></g>
  </g>""", extra


def _worm_outline():
    """A C. elegans body along an S curve: round head, long pointed tail."""
    (ax, ay), (bx, by) = (292, 716), (716, 350)
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    nx, ny = -dy / length, dx / length
    width, amp, n = 50, 72, 160

    def centre(t: float):
        off = amp * math.sin(2 * math.pi * 1.15 * t - 0.45)
        return ax + dx * t + nx * off, ay + dy * t + ny * off

    left, right = [], []
    for i in range(n + 1):
        t = i / n
        x, y = centre(t)
        x1, y1 = centre(max(0.0, t - 1e-3))
        x2, y2 = centre(min(1.0, t + 1e-3))
        tl = math.hypot(x2 - x1, y2 - y1) or 1
        px, py = -(y2 - y1) / tl, (x2 - x1) / tl
        w = max(2.5, width * min(1.0, t / 0.45) ** 0.9)
        left.append((x + px * w, y + py * w))
        right.append((x - px * w, y - py * w))
    d = ("M" + " L".join(f"{_f(x)} {_f(y)}" for x, y in left) + " L"
         + " L".join(f"{_f(x)} {_f(y)}" for x, y in reversed(right)) + " Z")
    return d, centre(1.0), width


def _worm(pal: Palette) -> tuple[str, str]:
    """C. elegans in white, crawling across a glass plate."""
    body, (hx, hy), width = _worm_outline()
    return f"""<circle cx="512" cy="530" r="236" {GLASS}/>
  <g fill="url(#bm-white)"><path d="{body}"/><circle cx="{_f(hx)}" cy="{_f(hy)}" r="{width}"/></g>""", ""


def _fly(pal: Palette) -> tuple[str, str]:
    """Drosophila from above: glass wings folded back, a banded abdomen."""
    gap = 'fill="none" stroke-width="14"'
    extra = (
        _cut("bm-abdomen",
             f'<ellipse cx="512" cy="446" rx="92" ry="82" {gap}/>'
             + "".join(f'<rect x="400" y="{y}" width="224" height="20" stroke="none"/>' for y in (592, 640, 688)))
        + _cut("bm-head",
               f'<ellipse cx="512" cy="446" rx="92" ry="82" {gap}/>'
               f'<circle cx="460" cy="322" r="32" {gap}/><circle cx="564" cy="322" r="32" {gap}/>'))
    wing = '<ellipse cx="{cx}" cy="606" rx="70" ry="182" transform="rotate({r} {cx} 606)" ' + GLASS + '/>'
    return f"""<g transform="translate(512 512) scale(1.08) translate(-512 -506)">
    {wing.format(cx=622, r=-24)}
    {wing.format(cx=402, r=24)}
    <g fill="url(#bm-white)">
      <ellipse cx="512" cy="604" rx="78" ry="134" mask="url(#bm-abdomen)"/>
      <ellipse cx="512" cy="446" rx="92" ry="82"/>
      <circle cx="512" cy="330" r="52" mask="url(#bm-head)"/>
      <circle cx="460" cy="322" r="32"/><circle cx="564" cy="322" r="32"/>
    </g>
  </g>""", extra


def _cryobox(pal: Palette) -> tuple[str, str]:
    """A freezer box from above: eight caps and the slot one was taken from."""
    step, r = 150, 50
    caps, rings, slot = [], [], ""
    for row in range(3):
        for col in range(3):
            cx, cy = 512 + (col - 1) * step, 512 + (row - 1) * step
            if (row, col) == (1, 2):
                slot = (f'<circle cx="{cx}" cy="{cy}" r="{r - 6}" fill="none" '
                        f'stroke="#fff" stroke-opacity="0.6" stroke-width="8"/>')
                continue
            caps.append(f'<circle cx="{cx}" cy="{cy}" r="{r}"/>')
            rings.append(f'<circle cx="{cx}" cy="{cy}" r="24" fill="none" stroke-width="8"/>')
    extra = _cut("bm-caps", "".join(rings))
    return f"""<rect x="262" y="262" width="500" height="500" rx="100" {GLASS}/>
  {slot}
  <g><g fill="url(#bm-white)" mask="url(#bm-caps)">{"".join(caps)}</g></g>""", extra


def _microtube(pal: Palette) -> tuple[str, str]:
    """A microcentrifuge tube: white cap, glass body, the sample in white."""
    tube = ("M406 356 L406 560 C406 622 480 758 496 784 Q512 806 528 784 "
            "C544 758 618 622 618 560 L618 356 Z")
    extra = f'<clipPath id="bm-tube"><path d="{tube}"/></clipPath>'
    return f"""<g transform="translate(0 -6)">
    <path d="{tube}" {GLASS}/>
    <g fill="url(#bm-white)">
      <g clip-path="url(#bm-tube)"><path d="M380 600 Q512 624 644 600 L644 820 L380 820 Z"/></g>
      <rect x="378" y="304" width="268" height="52" rx="18"/>
      <rect x="394" y="238" width="236" height="56" rx="22"/>
    </g>
  </g>""", extra


def _petri(pal: Palette) -> tuple[str, str]:
    """A glass dish, the agar's edge, and white colonies growing on it."""
    colonies = [(432, 440, 60), (604, 414, 34), (606, 604, 50), (446, 626, 26), (528, 520, 16)]
    dots = "".join(f'<circle cx="{x}" cy="{y}" r="{r}"/>' for x, y, r in colonies)
    return f"""<circle cx="512" cy="512" r="300" {GLASS}/>
  <circle cx="512" cy="512" r="250" fill="#fff" fill-opacity="0.16" stroke="#fff" stroke-opacity="0.5" stroke-width="4"/>
  <g fill="url(#bm-white)">{dots}</g>
  <path d="M288 392 A274 274 0 0 1 392 288" fill="none" stroke="#fff" stroke-opacity="0.9" stroke-width="10" stroke-linecap="round"/>""", ""


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

# How much to enlarge the subject when there is no rounded square around it
# (a phone rounds or masks the full-bleed square itself).
_FULL_SCALE = 1.2


@lru_cache(maxsize=None)
def render(glyph: str, color: str, variant: str = "app") -> str:
    """The icon as SVG.

    variant: "app" (the rounded square with its shadow), "full" (edge to edge,
    for touch and maskable icons), "glyph" (the subject alone on transparent,
    Android's adaptive foreground) or "background" (the colour alone).
    """
    glyph = glyph if glyph in GLYPHS else DEFAULT_GLYPH
    pal = PALETTES.get(color) or PALETTES[DEFAULT_COLOR]
    body, extra = GLYPHS[glyph][1](pal)
    if variant == "full":
        ground = '<rect width="1024" height="1024" fill="url(#bm-bg)"/><rect width="1024" height="1024" fill="url(#bm-light)"/>'
        body = f'<g transform="translate(512 512) scale({_FULL_SCALE}) translate(-512 -512)">{body}</g>'
    elif variant == "background":
        ground = '<rect width="1024" height="1024" fill="url(#bm-bg)"/><rect width="1024" height="1024" fill="url(#bm-light)"/>'
        body = ""
    elif variant == "glyph":
        ground = ""
    else:
        ground = ('<rect x="100" y="100" width="824" height="824" rx="185.4" fill="url(#bm-bg)"/>'
                  '<rect x="100" y="100" width="824" height="824" rx="185.4" fill="url(#bm-light)"/>')
        body += ('<rect x="102" y="102" width="820" height="820" rx="184" fill="none" '
                 'stroke="#fff" stroke-opacity="0.16" stroke-width="2"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">'
            f'<title>BioManager</title><defs>{_defs(pal)}{extra}</defs>{ground}{body}</svg>\n')


@lru_cache(maxsize=None)
def version(glyph: str, color: str) -> str:
    """Changes whenever the drawing does, so a cached icon is never stale."""
    return hashlib.sha1(render(glyph, color).encode()).hexdigest()[:10]


# ---------------------------------------------------------------- the choice

def _key(username: str) -> str:
    return f"app_icon:{username}"


def parse(value: str) -> tuple[str, str]:
    glyph, _, color = (value or "").partition("/")
    return (glyph if glyph in GLYPHS else DEFAULT_GLYPH,
            color if color in PALETTES else DEFAULT_COLOR)


def get_choice(session, username: str) -> tuple[str, str]:
    return parse(inventory_service.get_setting(session, _key(username), ""))


def set_choice(session, username: str, glyph: str, color: str) -> tuple[str, str]:
    glyph, color = parse(f"{glyph}/{color}")
    inventory_service.set_setting(session, _key(username), f"{glyph}/{color}")
    return glyph, color


# The person's font (Settings → Appearance): Inter, which the app ships, or the
# computer's own (SF Pro on a Mac, Segoe UI on Windows). base.html puts
# data-font="system" on the page for the latter (frontend/src/tailwind.css).
FONTS = ("inter", "system")


def get_font(session, username: str) -> str:
    value = inventory_service.get_setting(session, f"font:{username}", "")
    return value if value in FONTS else FONTS[0]


def set_font(session, username: str, font: str) -> str:
    font = font if font in FONTS else FONTS[0]
    inventory_service.set_setting(session, f"font:{username}", font)
    return font


def is_default(glyph: str, color: str) -> bool:
    return (glyph, color) == (DEFAULT_GLYPH, DEFAULT_COLOR)
