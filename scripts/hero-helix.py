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
HALF = 30                       # a backbone's half-width at the axis's depth
DEPTH = 0.42                    # how much nearer is wider, farther narrower
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


def runs(strand, front):
    """The stretches of a strand on one side of the axis, as point lists,
    a little past both ends of the turn so it tiles without a seam."""
    out, cur = [], []
    pts = [(i, *point(strand, i)) for i in range(-120, PERIOD + 121, 6)]
    for k, q in enumerate(pts):
        near = [z >= 0 for _, _, z, _ in pts[max(k - 2, 0):k + 3]]
        # the far side reaches two steps under the near side's ends
        keep = (q[2] >= 0) if front else not all(near)
        if keep:
            cur.append(q)
        elif cur:
            out.append(cur); cur = []
    if cur:
        out.append(cur)
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


def shaded(run, points, width, alpha, stroke):
    """A line along a stretch in short pieces, its width and strength
    following the depth, so it fades smoothly as the strand turns away."""
    out = []
    for k in range(len(points) - 1):
        (x0, y0), (x1, y1) = points[k], points[k + 1]
        d = (run[k][2] + run[k + 1][2]) / 4 + 0.5          # 0 far .. 1 near
        dx, dy = x1 - x0, y1 - y0
        n = math.hypot(dx, dy) or 1
        ex, ey = dx / n * 0.6, dy / n * 0.6                  # a hair of overlap
        w, a = width(d), alpha(d)
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


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    layers = {side: [(s, r) for s in (0, 1) for r in runs(s, side == "front")] for side in ("back", "front")}
    bars = rungs()
    rung_shapes = "".join(f"<rect x='{x - RUNG_HALF:.1f}' y='{t:.1f}' width='{2 * RUNG_HALF}' height='{b - t:.1f}' rx='{RUNG_HALF}'/>" for x, t, b in bars)
    for side, parts in layers.items():
        svg(f"{side}.svg", "".join(band(r) for _, r in parts))
        # the coloured core the glass frosts into a glow: thicker and
        # stronger as it comes near
        svg(f"{side}-core.svg", "".join(
            shaded(r, [(x, y) for x, y, _, _ in r], lambda d: 3 + 4 * d, lambda d: 0.45 + 0.55 * d, stroke=A if s == 0 else B)
            for s, r in parts))
        # bright edges, and a highlight along the upper side
        body = []
        for _, r in parts:
            up, down = edges(r)
            body.append(shaded(r, up, lambda d: 1.6, lambda d: 0.25 + 0.75 * d, stroke="white"))
            body.append(shaded(r, down, lambda d: 1.2, lambda d: 0.1 + 0.3 * d, stroke="white"))
            shine = [(x, y - hw * 0.5) for x, y, _, hw in r]
            body.append(f"<g filter='url(#s)'>{shaded(r, shine, lambda d: 3, lambda d: 0.08 + 0.4 * d, stroke='white')}</g>")
        defs = "<defs><filter id='s' x='-5%' y='-50%' width='110%' height='200%'><feGaussianBlur stdDeviation='1.5'/></filter></defs>"
        svg(f"{side}-edge.svg", "".join(body), defs)
    svg("rungs.svg", rung_shapes)
    svg("rungs-edge.svg", "".join(f"<rect x='{x - RUNG_HALF + .6:.1f}' y='{t + .6:.1f}' width='{2 * RUNG_HALF - 1.2}' height='{b - t - 1.2:.1f}' rx='{RUNG_HALF - .6}' fill='none' stroke='white' stroke-opacity='.7' stroke-width='1.2'/>" for x, t, b in bars))
    # everything, for the shadow under the glass and its hairline
    svg("all.svg", "".join(band(r) for parts in layers.values() for _, r in parts) + rung_shapes)
    print(f"one turn: {PERIOD}px, {len(bars) - 2} base pairs; written to {OUT}")


if __name__ == "__main__":
    main()
