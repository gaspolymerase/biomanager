#!/usr/bin/env python3
"""Draw one turn of the front page's glass double helix.

    python scripts/hero-helix.py

The opening's helix is two backbones of frosted glass with paler glass base
pairs between them. Each backbone is worked out as a real helix in 3D: where
it comes towards you it is wider and brighter, where it turns away it is
thinner, and where the two cross, the near one passes over the far one. So
the picture is drawn as three layers of glass, back to front: the backbone
parts behind the axis, the base pairs, and the parts in front, each with a
coloured core line under its glass (which the glass frosts into a glow, so
the backbone looks thick) and bright edges over it. A helix slid along its
axis looks the same as one turning about it, so the CSS turns it by sliding
one turn on a loop.

Writes site/assets/helix/*.svg, one turn each, which the CSS repeats. Keep
PERIOD and HEIGHT in step with .hero-glass in site/style.css.
"""
import math
from pathlib import Path

PERIOD, HEIGHT = 1400, 560      # one turn, and the tile's height (px)
R = 200                         # the helix's radius
HALF = 32                       # a backbone's half-width at the axis's depth
DEPTH = 0.5                     # how much nearer is wider, farther narrower
OFFSET = 0.72 * math.pi         # strand B's lag: a wide groove and a narrow one
RUNGS = 22                      # base pairs per turn
RUNG_HALF = 7                   # a base pair's half-width
A, B = "#2cc4a6", "#5aa8ff"
CY = HEIGHT / 2
OUT = Path(__file__).resolve().parent.parent / "site" / "assets" / "helix"


def point(strand, x):
    """Centre (x, y), depth z (-1 far .. 1 near) and half-width at x."""
    th = 2 * math.pi * x / PERIOD + (OFFSET if strand else 0)
    z = math.cos(th)
    return CY - R * math.sin(th), z, HALF * (1 + DEPTH * z)


FADE = 0.16                     # depth over which a backbone passes from back to front


def strand_run(strand):
    """A whole strand, a little past both ends of the turn so it tiles
    without a seam."""
    return [(i, *point(strand, i)) for i in range(-120, PERIOD + 121, 6)]


def near(z):
    """How much of a point belongs to the near layer: 0 behind the axis,
    1 in front, easing across the axis so the change shows no join."""
    t = min(max((z + FADE) / (2 * FADE), 0), 1)
    return t * t * (3 - 2 * t)


def near_stretches(run):
    """The stretches of a strand that are at all in front."""
    out, cur = [], []
    for k, q in enumerate(run):
        if near(q[2]) > 0:
            cur.append((k, q))
        elif cur:
            out.append(cur); cur = []
    if cur:
        out.append(cur)
    return out


def across(run, f):
    """Points a fraction f of the half-width off the centre line, across the
    band (f = -1 the edge higher on the page, 1 the lower one)."""
    out = []
    for k, (x, y, z, hw) in enumerate(run):
        x0, y0 = run[max(k - 1, 0)][:2]
        x1, y1 = run[min(k + 1, len(run) - 1)][:2]
        n = math.hypot(x1 - x0, y1 - y0) or 1
        nx, ny = (y0 - y1) / n, (x1 - x0) / n
        out.append((x + nx * hw * f, y + ny * hw * f))
    return out


def edges(run):
    """The two long edges of a stretch, offset along the curve's normal."""
    up, down = [], []
    for k, (x, y, z, hw) in enumerate(run):
        x0, y0 = run[max(k - 1, 0)][:2]
        x1, y1 = run[min(k + 1, len(run) - 1)][:2]
        dx, dy = x1 - x0, y1 - y0
        n = math.hypot(dx, dy) or 1
        nx, ny = -dy / n, dx / n
        up.append((x + nx * hw, y + ny * hw))
        down.append((x - nx * hw, y - ny * hw))
    return up, down


def pts(points):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def band(run):
    up, down = edges(run)
    return f"<polygon points='{pts(up + down[::-1])}'/>"


def line(points, **attrs):
    a = " ".join(f"{k.replace('_', '-')}='{v}'" for k, v in attrs.items())
    return f"<polyline points='{pts(points)}' fill='none' stroke-linecap='round' stroke-linejoin='round' {a}/>"


def shaded(run, points, width, alpha, stroke, weight=lambda z: 1):
    """A line along a stretch in short pieces, its width and strength
    following the depth, so it fades smoothly as the strand turns away;
    weight shares it between the back and front layers."""
    out = []
    for k in range(len(points) - 1):
        (x0, y0), (x1, y1) = points[k], points[k + 1]
        d = (run[k][2] + run[k + 1][2]) / 4 + 0.5          # 0 far .. 1 near
        dx, dy = x1 - x0, y1 - y0
        n = math.hypot(dx, dy) or 1
        ex, ey = dx / n * 0.6, dy / n * 0.6                  # a hair of overlap
        w, a = width(d), alpha(d) * weight((run[k][2] + run[k + 1][2]) / 2)
        if a > 0.01:
            out.append(f"<path d='M{x0 - ex:.1f} {y0 - ey:.1f}L{x1 + ex:.1f} {y1 + ey:.1f}' stroke-width='{w:.1f}' stroke-opacity='{a:.2f}'/>")
    return f"<g stroke='{stroke}'>{''.join(out)}</g>"


def rungs():
    out = []
    for k in range(-1, RUNGS + 1):
        x = (k + 0.5) * PERIOD / RUNGS
        ya, za, ha = point(0, x)
        yb, zb, hb = point(1, x)
        if abs(ya - yb) < ha + hb + 24:
            continue
        top, bot = (ya + ha, yb - hb) if ya < yb else (yb + hb, ya - ha)
        out.append((x, top, bot))
    return out


def svg(name, body, defs=""):
    s = (f"<svg xmlns='http://www.w3.org/2000/svg' width='{PERIOD}' height='{HEIGHT}' "
         f"viewBox='0 0 {PERIOD} {HEIGHT}'>{defs}{body}</svg>\n")
    (OUT / name).write_text(s, encoding="utf-8")


def near_mask(run, tag):
    """The near layer's glass for a strand: its in-front stretches, each
    filled with a gradient that fades it in and out across the axis. There
    the backbone is at its highest or lowest, running level, so a gradient
    across the page follows it."""
    shapes, grads = [], []
    for n, stretch in enumerate(near_stretches(run)):
        pts_ = [q for _, q in stretch]
        if len(pts_) < 2:
            continue
        gid = f"g{tag}{n}"
        x0, x1 = pts_[0][0], pts_[-1][0]
        stops = "".join(f"<stop offset='{(x - x0) / ((x1 - x0) or 1):.4f}' stop-color='white' stop-opacity='{near(z):.3f}'/>"
                        for x, _, z, _ in pts_ if near(z) < 0.999 or x in (x0, x1))
        grads.append(f"<linearGradient id='{gid}' gradientUnits='userSpaceOnUse' x1='{x0}' y1='0' x2='{x1}' y2='0'>{stops}</linearGradient>")
        up, down = edges(pts_)
        shapes.append(f"<polygon fill='url(#{gid})' points='{pts(up + down[::-1])}'/>")
    return grads, shapes


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    strands = [(s_, strand_run(s_)) for s_ in (0, 1)]
    bars = rungs()
    rung_shapes = "".join(f"<rect x='{x - RUNG_HALF:.1f}' y='{t:.1f}' width='{2 * RUNG_HALF}' height='{b - t:.1f}' rx='{RUNG_HALF}'/>" for x, t, b in bars)
    whole = "".join(band(r) for _, r in strands)
    # The far layer's glass is the whole of both backbones; the near layer's
    # fades in over it where a backbone comes round in front.
    svg("back.svg", whole)
    grads, shapes = [], []
    for s_, r in strands:
        g, sh = near_mask(r, "ab"[s_])
        grads += g; shapes += sh
    svg("front.svg", "".join(shapes), f"<defs>{''.join(grads)}</defs>")
    hw_at = lambda d: HALF * (1 + DEPTH * (2 * d - 1))
    for side, weight in (("back", lambda z: 1 - near(z)), ("front", near)):
        # The coloured core the glass frosts into a glow, thicker and stronger
        # as it comes near: all of it under the far glass, so it is frosted
        # everywhere, even where the near glass is only fading in.
        if side == "back":
            # (soft already, so it never shows as a hard line)
            svg("core.svg", "<g filter='url(#b)'>" + "".join(
                shaded(r, [(x, y) for x, y, _, _ in r], lambda d: 4 + 6 * d, lambda d: 0.45 + 0.55 * d, A if s_ == 0 else B)
                for s_, r in strands) + "</g>",
                "<defs><filter id='b' x='-5%' y='-20%' width='110%' height='140%'><feGaussianBlur stdDeviation='3'/></filter></defs>")
        # Rounded glass: a soft rim of light just inside each edge, fading
        # towards the middle as light does through a glass rod, a soft
        # highlight along the upper side, and a faint line at the edge itself.
        # The rims are kept inside the backbones by clipping to their shape.
        rims, lines = [], []
        for _, r in strands:
            rims.append(shaded(r, across(r, -0.8), lambda d: 0.42 * hw_at(d), lambda d: 0.18 + 0.5 * d, "white", weight))
            rims.append(shaded(r, across(r, 0.8), lambda d: 0.36 * hw_at(d), lambda d: 0.08 + 0.25 * d, "white", weight))
            rims.append(shaded(r, across(r, -0.42), lambda d: 0.16 * hw_at(d), lambda d: 0.1 + 0.45 * d, "white", weight))
            up, down = edges(r)
            lines.append(shaded(r, down, lambda d: 1, lambda d: 0.12 + 0.4 * d, "white", weight))  # higher on the page
            lines.append(shaded(r, up, lambda d: 1, lambda d: 0.06 + 0.2 * d, "white", weight))
        defs = (f"<defs><clipPath id='c'>{whole}</clipPath>"
                "<filter id='s' x='-10%' y='-10%' width='120%' height='120%'><feGaussianBlur stdDeviation='3'/></filter></defs>")
        body = [f"<g clip-path='url(#c)'><g filter='url(#s)'>{''.join(rims)}</g></g>", *lines]
        svg(f"{side}-edge.svg", "".join(body), defs)
    svg("rungs.svg", rung_shapes)
    svg("rungs-edge.svg", "".join(f"<rect x='{x - RUNG_HALF + .6:.1f}' y='{t + .6:.1f}' width='{2 * RUNG_HALF - 1.2}' height='{b - t - 1.2:.1f}' rx='{RUNG_HALF - .6}' fill='none' stroke='white' stroke-opacity='.7' stroke-width='1.2'/>" for x, t, b in bars))
    # everything, for the shadow under the glass and its hairline
    svg("all.svg", whole + rung_shapes)
    print(f"one turn: {PERIOD}px, {len(bars) - 2} base pairs; written to {OUT}")
    # style.css links these by their content: bring the links up to date
    import subprocess, sys
    subprocess.run([sys.executable, str(Path(__file__).with_name("site-stamp.py"))], check=True)


if __name__ == "__main__":
    main()
