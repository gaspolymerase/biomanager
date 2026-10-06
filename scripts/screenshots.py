#!/usr/bin/env python3
"""Take the README and website screenshots from a running demo lab.

    python scripts/demo-data.py /tmp/biomanager-demo
    BIOMANAGER_DATA_DIR=/tmp/biomanager-demo PORT=5077 python run.py
    python scripts/screenshots.py http://127.0.0.1:5077 /tmp/biomanager-demo docs/screenshots

Needs Playwright, which is not an app dependency:
    pip install playwright && playwright install chromium
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

if len(sys.argv) != 4:
    sys.exit(__doc__)
BASE, DATA, OUT = sys.argv[1].rstrip("/"), Path(sys.argv[2]), Path(sys.argv[3])
OUT.mkdir(parents=True, exist_ok=True)
PASSWORD = (DATA / "demo-password").read_text().strip()

# name, path, color scheme, and anything to do before the shot
DESKTOP = [
    ("home", "/home", "light", None),
    ("home-dark", "/home", "dark", None),
    ("mice", "/colony?view=mice", "light", None),
    ("cages", "/colony?view=cages", "light", None),
    ("rack-grid", "/colony?view=cages", "light", "[data-layout=grid]"),
    ("fly-stocks", "/stocks/drosophila", "light", None),
    ("fly-grid", "/stocks/drosophila", "light", "[data-layout=grid]"),
    ("plasmid-map", "/plasmids/2", "light", None),
    ("orders", "/orders", "light", "[data-layout=board]"),
    ("reagents", "/inventory/reagents", "light", None),
    ("calendar", "/calendar", "light", None),
    ("new-database", "/organisms/new", "light", None),
    ("utilities", "/utilities#pcrmix", "light", None),
    ("cage-cards", "/labels/cards/cages", "light", None),
]
PHONE = [
    ("phone-cage", "/colony?view=cages&scope=all#cage-1", "light"),
    ("phone-home", "/home", "light"),
]


def sign_in(page):
    page.goto(f"{BASE}/login")
    page.fill("input[name=username]", "alex")
    page.fill("input[name=password]", PASSWORD)
    page.press("input[name=password]", "Enter")
    page.wait_for_load_state("networkidle")
    # A lab past its first days has put Home's "Getting started" list away.
    page.goto(f"{BASE}/home")
    hide = page.get_by_role("button", name="Hide this list")
    if hide.count():
        hide.first.click()
        page.wait_for_load_state("networkidle")


def shoot(page, name, path, click=None, full=False, crop=None):
    """`crop` is a CSS selector: the shot is that element and a little around
    it, rather than the whole window. A cropped shot shows the reader the
    panel the step is about, and — because the crop lives here and not in an
    image editor — it survives every retake from a fresh demo lab."""
    page.goto(f"{BASE}{path}")
    page.wait_for_load_state("networkidle")
    if click:
        page.locator(click).first.click()
        page.wait_for_load_state("networkidle")
    page.wait_for_timeout(800)  # let maps, grids and fonts settle
    if "#" not in path:  # a page that focuses a field scrolls to it; start at the top
        page.evaluate("document.activeElement && document.activeElement.blur();"
                      "document.querySelectorAll('*').forEach(e => { if (e.scrollTop) e.scrollTop = 0 });"
                      "window.scrollTo(0, 0)")
    target, box = page, None
    if crop:
        found = page.locator(crop).first
        if found.count() and found.is_visible():
            found.scroll_into_view_if_needed()
            page.wait_for_timeout(250)
            rect = found.bounding_box()
            if rect:
                pad = 14
                box = {"x": max(rect["x"] - pad, 0), "y": max(rect["y"] - pad, 0),
                       "width": rect["width"] + 2 * pad, "height": rect["height"] + 2 * pad}
        if box is None:
            print(f"  ! {name}: nothing matched {crop}, taking the whole window")
    if box:
        page.screenshot(path=str(OUT / f"{name}.png"), clip=box)
    else:
        target.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
    print(f"  {name}.png" + (" (cropped)" if box else ""))


with sync_playwright() as p:
    browser = p.chromium.launch()
    for scheme in ("light", "dark"):
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2,
                                  color_scheme=scheme)
        page = ctx.new_page()
        sign_in(page)
        for name, path, want, click, *rest in DESKTOP:
            if want == scheme:
                shoot(page, name, path, click, crop=rest[0] if rest else None)
        ctx.close()

    phone = browser.new_context(**p.devices["iPhone 13"], color_scheme="light")
    page = phone.new_page()
    sign_in(page)
    for name, path, _ in PHONE:
        shoot(page, name, path)
    phone.close()
    browser.close()
