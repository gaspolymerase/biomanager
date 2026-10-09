#!/usr/bin/env python3
"""Take the README and website screenshots from a running demo lab.

    python scripts/demo-data.py /tmp/biomanager-demo
    BIOMANAGER_DATA_DIR=/tmp/biomanager-demo PORT=5077 python run.py
    python scripts/screenshots.py http://127.0.0.1:5077 /tmp/biomanager-demo docs/screenshots
    python scripts/screenshots.py … docs/screenshots task-litter    # just these

Needs Playwright, which is not an app dependency:
    pip install playwright && playwright install chromium
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

if len(sys.argv) < 4:
    sys.exit(__doc__)
BASE, DATA, OUT = sys.argv[1].rstrip("/"), Path(sys.argv[2]), Path(sys.argv[3])
ONLY = set(sys.argv[4:])        # names to take; empty means all of them
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
    ("utilities", "/utilities", "light", None),
    ("cage-cards", "/labels/cards/cages", "light", None),
    # For the task pages: the panel the step is about, not the whole window.
    # The fifth item crops the shot to that element (see shoot).
    ("task-cage", "/colony?view=cages", "light", "[data-layout=cards]", ".cage-detail-body"),
    # No task-litter shot: every breeder cage in the demo lab already has a
    # litter, so the Litter born dialog opens on its "this replaces it"
    # warning rather than the plain case the guide describes. A breeder cage
    # with no litter in scripts/demo-data.py would make it shootable.
    ("task-genotyping", "/colony?view=cages", "light",
     ["[data-layout=cards]", "button:has-text('Genotyping')"], "#genotyping-modal"),
    # Set it back first, so the shot works whether or not this order has
    # already been received: only a change to received raises the offer.
    ("task-stock", "/orders", "light",
     [("select.status-pill", "ordered"), ("select.status-pill", "received")], "#stock-offer-dialog"),
    ("task-booking", "/calendar", "light",
     ["#cal-new-more", "button.menu-item[data-new=booking]"], "#biocal-modal"),
    # Signing opens a panel in the page's drawer, not a dialog — the control
    # is .nb-tool[data-panel=sign] (promo/clips/more.py's sign walk knows the
    # same selectors).
    ("task-sign", "/notebook", "light", [".nb-tool[data-panel=sign]"], "form.nb-sig-form"),
    # No restore-a-backup shot: its steps are in Finder and the file system,
    # which a browser cannot photograph.
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
    # A fresh demo lab has never seen this release, so What's new opens over
    # the page and swallows the first click of every shot (app/whats_new.py).
    for _ in range(3):
        if not page.locator("dialog#whats-new[open]").count():
            break
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
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
    for step in ([click] if isinstance(click, str) else click or []):
        # A dialog shot needs two steps: open the view, then open the dialog.
        # A (selector, value) step chooses in a dropdown instead of clicking,
        # which is how the orders sheet raises "Add it to stock?".
        if isinstance(step, tuple):
            page.select_option(step[0], step[1])
        else:
            page.locator(step).first.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(400)
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
            if want == scheme and (not ONLY or name in ONLY):
                shoot(page, name, path, click, crop=rest[0] if rest else None)
        ctx.close()

    phone = browser.new_context(**p.devices["iPhone 13"], color_scheme="light")
    page = phone.new_page()
    sign_in(page)
    for name, path, _ in PHONE:
        if not ONLY or name in ONLY:
            shoot(page, name, path)
    phone.close()
    browser.close()
