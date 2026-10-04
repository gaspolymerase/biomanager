#!/usr/bin/env python3
"""Draw one turn of the front page's glass double helix into site/style.css.

    python scripts/hero-helix.py

The opening's helix is a row of glass slabs, one per base pair, so their ends
trace the two strands. This draws one turn (N slabs) as SVG pictures and
writes them as CSS variables in .hero-glass, replacing the ones there: the
slab shapes (the glass's mask and its shadow), their tints at the strand ends,
their bright edges, and the two strands that run behind the glass. The CSS
repeats the turn and slides it by one turn on a loop. Change the sizes here
and the PERIOD and TILE_H in style.css (980px, 560px) to match.
"""
import math, re, urllib.parse
from pathlib import Path

W, GAP, N, HMAX, TILE_H = 52, 18, 14, 500, 560
P = W + GAP; PERIOD = P * N; CY = TILE_H / 2
A, B = "#2cc4a6", "#5aa8ff"
def slabs():
    for i in range(N):
        c = math.cos(2 * math.pi * i / N)
        h = W + (HMAX - W) * abs(c)
        x = i * P + GAP / 2; y = CY - h / 2
        yield i, x, y, h, (A, B) if c >= 0 else (B, A)
def svg(body, defs=""):
    s = f"<svg xmlns='http://www.w3.org/2000/svg' width='{PERIOD}' height='{TILE_H}' viewBox='0 0 {PERIOD} {TILE_H}'>{defs}{body}</svg>"
    return 'url("data:image/svg+xml,' + urllib.parse.quote(s, safe="/:=' ,.-") + '")'
r = W / 2
shape = svg("".join(f"<rect x='{x:.1f}' y='{y:.1f}' width='{W}' height='{h:.1f}' rx='{r}' fill='black'/>" for i, x, y, h, _ in slabs()))
edefs = "<defs><linearGradient id='e' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='white'/><stop offset='.5' stop-color='white' stop-opacity='.25'/><stop offset='1' stop-color='white' stop-opacity='.55'/></linearGradient></defs>"
defs = "<defs>" + "".join(
    f"<linearGradient id='t{i}' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='{t}'/><stop offset='{e:.3f}' stop-color='{t}' stop-opacity='0'/><stop offset='{1-e:.3f}' stop-color='{b}' stop-opacity='0'/><stop offset='1' stop-color='{b}'/></linearGradient>"
    for i, x, y, h, (t, b) in slabs() for e in [min(0.45, 70 / h)]) + "</defs>"
tint = svg("".join(f"<rect x='{x:.1f}' y='{y:.1f}' width='{W}' height='{h:.1f}' rx='{r}' fill='url(#t{i})'/>" for i, x, y, h, _ in slabs()), defs)
edge = svg("".join(f"<rect x='{x+.75:.2f}' y='{y+.75:.2f}' width='{W-1.5}' height='{h-1.5:.1f}' rx='{r-.75}' fill='none' stroke='url(#e)' stroke-width='1.5'/>" for i, x, y, h, _ in slabs()), edefs)
out = [f"--hg-shape: {shape};"]
out.append(f"--hg-tint: {tint};")
out.append(f"--hg-edge: {edge};")
R = (HMAX - W) / 2
def strand(sign):
    pts = []
    for x in range(0, PERIOD + 1, 7):
        th = 2 * math.pi * (x - P / 2) / PERIOD
        pts.append(f"{x},{CY - sign * R * math.cos(th):.1f}")
    return "M" + " L".join(pts)
strands = svg(f"<path d='{strand(1)}' fill='none' stroke='{A}' stroke-width='3' stroke-linecap='round'/><path d='{strand(-1)}' fill='none' stroke='{B}' stroke-width='3' stroke-linecap='round'/>")
out.append(f"--hg-strands: {strands};")

css = Path(__file__).resolve().parent.parent / "site" / "style.css"
text = css.read_text(encoding="utf-8")
text, n = re.subn(r"  --hg-shape: .*?\n(  --hg-[a-z]+: url\(.*?\n)+", lambda m: "".join(f"  {line}\n" for line in out), text, count=1)
if not n:
    raise SystemExit("style.css has no --hg-shape line in .hero-glass to replace")
css.write_text(text, encoding="utf-8")
print(f"one turn: {N} slabs, {PERIOD}px; written to {css}")
