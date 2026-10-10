"""The second week's clips: the notebook's data sheets, plates and qPCR,
signing a page, protocols at the bench, fly racks, the calendar, undo,
search and tabs, the app's looks, a phone, and label printers.

Each walk gets a Director (scripts/feature-clips.py) on a signed-in page of a
fresh demo lab (scripts/demo-data.py). Notebook pages are made in PREPARE
through the notebook's own routes, as a Markdown body whose fenced blocks
(```sheet, ```plate, ```qpcr) hold each block's data.
"""
from __future__ import annotations

import http.cookiejar
import json
import random
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

TODAY = date.today()
PAGES: dict[str, str] = {}      # clip -> the notebook page it opens
LAB: dict[str, str] = {}        # what PREPARE learnt: the demo password, the topic


def post_json(api, url, body):
    r = api.post(url, data=json.dumps(body), headers={"Content-Type": "application/json"})
    if not r.ok:
        raise RuntimeError(f"{url}: {r.status} {r.text()[:200]}")
    return r.json()


# ---------------------------------------------------------------------------
# Notebook pages, and the lab mates who sign and book alongside alex
# ---------------------------------------------------------------------------

def topic(api, base) -> int:
    """alex's notebook topic for these clips, made once."""
    if "topic" not in LAB:
        r = api.post(f"{base}/notebook/tabs/create", form={"title": "Bench work"})
        LAB["topic"] = r.json()["id"]
    return LAB["topic"]


def notebook_page(api, base, title, body) -> int:
    page = post_json(api, f"{base}/notebook/api/pages/new", {"title": title, "tab_id": topic(api, base)})["page_id"]
    r = api.post(f"{base}/notebook/pages/{page}/update", form={"body": body})
    if not r.ok:
        raise RuntimeError(f"page {page}: {r.status}")
    return page


def fence(kind, data) -> str:
    return f"```{kind}\n{json.dumps(data)}\n```\n"


def demo_password(api, base, page: int) -> str:
    """Every demo account shares one password, written to the lab's folder.
    Try --data's, then the labs feature-clips.py made (newest first), on a
    check that changes nothing: amending a page that isn't signed says the
    password is right (409) or wrong (403), and isn't a sign-in attempt, so
    nobody is locked out."""
    if "password" in LAB:
        return LAB["password"]
    files = []
    for i, arg in enumerate(sys.argv):
        if arg == "--data" and i + 1 < len(sys.argv):
            files.append(Path(sys.argv[i + 1]) / "demo-password")
        elif arg.startswith("--data="):
            files.append(Path(arg.split("=", 1)[1]) / "demo-password")
    files += sorted(Path(tempfile.gettempdir()).glob("biomanager-clips-*/lab/demo-password"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        try:
            pw = f.read_text().strip()
        except OSError:
            continue
        r = api.post(f"{base}/notebook/api/pages/{page}/sign", data=json.dumps({"action": "amend", "password": pw}),
                     headers={"Content-Type": "application/json"})
        if r.status == 409:
            LAB["password"] = pw
            return pw
    raise RuntimeError("Couldn't find the demo lab's password (pass --data with --base).")


def member(base, username, password):
    """A lab mate signed in on their own session (urllib: the APIRequestContext is alex's)."""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    r = opener.open(f"{base}/login", data=urllib.parse.urlencode({"username": username, "password": password}).encode())
    if r.geturl().rstrip("/").endswith("/login"):
        raise RuntimeError(f"{username} couldn't sign in")

    def post(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.loads(opener.open(req).read())
    return post


def scroll_to(d, target, y: float, seconds: float = 1.0):
    """Scroll (with the wheel, so it shows) until target's top is at y."""
    box = d.loc(target).bounding_box()
    d.scroll(box["y"] - y, seconds=seconds, at=target)


def pick(d, select, value, after: float = 1.0):
    """Choose from a <select>: the cursor goes to it, the value changes.
    (A native dropdown's list isn't in the recording.)"""
    d.move(select, 0.6)
    d.wait(0.25)
    d.page.select_option(select, value)
    d.wait(after)


# ---------------------------------------------------------------------------
# Data sheets: numbers in, a plot with error bars and the stars out
# ---------------------------------------------------------------------------

DRUG_B = ["62", "58", "66", "55"]


def make_datasheet(api, base):
    rows = [["Vehicle", v] for v in ("98", "102", "95", "104")] + \
           [["Drug A", v] for v in ("81", "76", "85", "79")] + [["Drug B", ""] for _ in DRUG_B]
    sheet = {"title": "HEK293T viability, 10 µM", "columns": [{"name": "Treatment", "type": "text"},
                                                              {"name": "Viability", "type": "number", "unit": "%"}],
             "rows": rows, "showPlot": True, "showStats": True,
             "chart": {"type": "bar", "x": 0, "y": [1], "group": -1, "error": "sem"},
             "stats": {"group": 0, "value": 1, "test": "auto", "control": ""}}
    body = "## Viability assay\n\nCellTiter-Glo, four wells per treatment, read two days after dosing.\n\n" + fence("sheet", sheet)
    PAGES["datasheet"] = f"/notebook?page={notebook_page(api, base, 'Viability: Drug A vs Drug B', body)}"


def datasheet(d):
    d.goto(PAGES["datasheet"])
    d.start()
    d.wait(0.8)
    cell = "input[data-r='{}'][data-c='1']:visible"
    scroll_to(d, cell.format(8), 250, seconds=1.2)
    # 1.0's floating editor toolbar covers the window's bottom: keep the rows above it.
    d.page.locator(cell.format(8)).first.evaluate("e => e.scrollIntoView({block: 'center', behavior: 'smooth'})")
    d.wait(0.8)
    for i, value in enumerate(DRUG_B):
        d.type(cell.format(8 + i), value, delay=110, after=0.5)
    d.wait(0.8)
    plot = d.page.locator(".nb-block-sheet .nb-plot-host").first
    scroll_to(d, "select[data-k=type]", 110, seconds=1.0)
    d.zoom(plot, scale=1.5)
    d.wait(1.6)
    d.unzoom()
    pick(d, "select[data-k=type]", "dots", after=1.4)
    pick(d, "select[data-k=type]", "box", after=1.4)
    pick(d, "select[data-k=type]", "bar", after=0.6)
    pick(d, "select[data-k=error]", "sd", after=1.0)
    d.scroll(460, seconds=1.2, at=plot)
    result = d.page.locator(".nb-block-sheet .nb-stats-result").first
    box = result.bounding_box()
    d.zoom(box=(box["x"] - 10, box["y"] - 20, 520, 190), scale=1.9)
    d.move(result, 0.8)
    d.wait(2.4)
    d.unzoom()
    d.wait(0.6)


# ---------------------------------------------------------------------------
# Plate reader and qPCR: a BCA standard curve, then ΔΔCt from pasted Cts
# ---------------------------------------------------------------------------

def bca_plate() -> dict:
    rng = random.Random(3)
    values, roles = {}, {}
    for col, conc in enumerate((2000, 1500, 1000, 750, 500, 250, 125, 25, 0), start=1):
        for row in "AB":
            values[f"{row}{col}"] = f"{0.095 + 0.00058 * conc + rng.uniform(-0.012, 0.012):.3f}"
            roles[f"{row}{col}"] = {"t": "blank"} if conc == 0 else {"t": "std", "conc": str(conc)}
    for col, (name, conc) in enumerate((("Vehicle 1", 620), ("Vehicle 2", 655), ("EGF 10 min", 590),
                                        ("EGF 10 min b", 640), ("EGF 30 min", 610), ("EGF 30 min b", 700)), start=1):
        for row in "CD":
            values[f"{row}{col}"] = f"{0.095 + 0.00058 * conc + rng.uniform(-0.01, 0.01):.3f}"
            roles[f"{row}{col}"] = {"t": "sample", "name": name, "df": "5"}
    return {"title": "BCA: HEK293T lysates", "format": 96, "values": values, "roles": roles, "fit": "linear",
            "unit": "µg/mL"}


def qpcr_export() -> str:
    """The instrument's export: well, sample, target and Ct, in triplicate."""
    rng = random.Random(5)
    base_ct = {"GAPDH": 17.2, "FOS": 29.5, "EGR1": 27.8}
    shift = {"Vehicle": {}, "EGF 10 min": {"FOS": -4.1, "EGR1": -2.6}, "EGF 30 min": {"FOS": -2.2, "EGR1": -3.4}}
    lines, n = ["Well\tSample Name\tTarget Name\tCT"], 0
    for sample, change in shift.items():
        for target, ct in base_ct.items():
            for _ in range(3):
                lines.append(f"{'ABCDEFGH'[n // 12]}{n % 12 + 1}\t{sample}\t{target}\t"
                             f"{ct + change.get(target, 0) + rng.uniform(-0.18, 0.18):.2f}")
                n += 1
    return "\n".join(lines)


def make_qpcr(api, base):
    body = ("## Protein\n\n" + fence("plate", bca_plate()) + "\n## Immediate early genes\n\n"
            + fence("qpcr", {"title": "FOS and EGR1 after EGF", "rows": [], "reference": "", "control": "",
                             "plotTarget": ""}))
    PAGES["qpcr"] = f"/notebook?page={notebook_page(api, base, 'EGF time course', body)}"


def qpcr(d):
    d.goto(PAGES["qpcr"])
    plate = ".nb-block-plate"
    d.page.locator(f"{plate} [data-mode=readings]").click()
    d.wait(0.4)
    d.start()
    grid = d.page.locator(f"{plate} .nb-plate-grid").first
    d.zoom(grid, scale=1.5)
    d.move(f"{plate} td[data-w=A1]", 0.8)
    d.move(f"{plate} td[data-w=D6]", 1.0)
    d.wait(0.6)
    d.click(f"{plate} [data-mode=layout]", after=1.6)
    d.unzoom()
    d.click(f"{plate} [data-mode=results]", after=1.0)
    scroll_to(d, f"{plate} .nb-plate-curve", 120, seconds=1.2)
    d.wait(1.8)
    d.scroll(300, seconds=0.8)
    d.wait(1.0)
    q = ".nb-block-qpcr"
    scroll_to(d, q, 130, seconds=1.0)
    text = qpcr_export()
    head, rest = text.split("\n", 2)[:2], text
    d.type(f"{q} textarea", "\n".join(head) + "\n", delay=18, after=0.2)
    d.page.fill(f"{q} textarea", rest)
    d.wait(0.8)
    d.click(f"{q} [data-act=paste]", after=1.0)
    pick(d, f"{q} select[data-f=control]", "Vehicle", after=1.2)
    table = d.page.locator(f"{q} .nb-stats-table").first
    d.zoom(table, scale=1.5)
    d.wait(2.2)
    d.unzoom()
    scroll_to(d, f"{q} select[data-f=plotTarget]", 110, seconds=1.0)
    d.wait(1.4)
    pick(d, f"{q} select[data-f=plotTarget]", "EGR1", after=2.0)


# ---------------------------------------------------------------------------
# Signing a page: fingerprinted, locked, witnessed, and in the history
# ---------------------------------------------------------------------------

BLOT_PLAN = """## Aim

Does EGF raise ERK phosphorylation in HEK293T, and how fast?

## Samples

| Lane | Treatment | Protein |
| --- | --- | --- |
| 1 | Vehicle | 20 µg |
| 2 | EGF 100 ng/mL, 5 min | 20 µg |
| 3 | EGF 100 ng/mL, 15 min | 20 µg |

## Antibodies

- pERK1/2 (Thr202/Tyr204), 1:2000, lot 0042
- Total ERK1/2, 1:1000, lot 0117
"""

BLOT_RESULT = BLOT_PLAN + """
## Result

pERK/total ERK up 4.2-fold at 5 min and 2.9-fold at 15 min; total ERK even across lanes.
Blot image and the raw .scn file are in the lab drive under 2026/blots.
"""


def make_signed(api, base):
    page = notebook_page(api, base, "Western blot: pERK after EGF", BLOT_PLAN)
    post_json(api, f"{base}/notebook/api/pages/{page}/versions", {"label": "Plan, before running the gel"})
    api.post(f"{base}/notebook/pages/{page}/update", form={"body": BLOT_RESULT})
    post_json(api, f"{base}/notebook/api/pages/{page}/versions", {"label": "Blot imaged, result written"})
    post_json(api, f"{base}/notebook/api/pages/{page}/shares", {"username": "sam", "role": "view"})
    demo_password(api, base, page)
    LAB["witness"] = member(base, "sam", LAB["password"])
    PAGES["sign"] = f"/notebook?page={page}"


def sign(d):
    d.goto(PAGES["sign"])
    d.start()
    d.wait(0.8)
    d.scroll(360, seconds=1.4, at="text=Result")
    d.wait(0.8)
    d.scroll(-360, seconds=0.9)
    d.click(".nb-tool[data-panel=sign]", after=0.8)
    panel = d.page.locator("#nb-sig-secret").locator("xpath=ancestor::*[contains(@class,'nb-drawer') or "
                                                     "contains(@class,'panel')][1]")
    box = d.page.locator("#nb-sig-meaning").bounding_box()
    d.zoom(box=(box["x"] - 20, box["y"] - 150, 350, 290), scale=2.0)
    d.move("#nb-sig-meaning", 0.8)
    d.wait(0.8)
    d.type("#nb-sig-secret", LAB["password"], delay=45, after=0.4)
    d.click("button:has-text('Sign and lock')", after=1.2)
    d.unzoom()
    d.zoom("#nb-signed-callout", scale=1.6)
    d.wait(2.0)
    d.unzoom()
    # Sam, reading it, witnesses it.
    LAB["witness"](f"/notebook/api/pages/{PAGES['sign'].rsplit('=', 1)[1]}/sign", {"action": "witness",
                                                                                   "password": LAB["password"]})
    d.goto(PAGES["sign"], settle=0.4)
    d.click("#nb-signed-callout [data-panel=sign]", after=1.0)
    first = d.page.locator("text=Signed by").last.bounding_box()
    d.zoom(box=(first["x"] - 20, first["y"] - 60, 350, 220), scale=2.0)
    d.move("text=Witnessed", 0.8)
    d.wait(2.2)
    d.unzoom()
    d.click(".nb-tool[data-panel=history]", after=1.2)
    d.wait(1.6)
    del panel


# ---------------------------------------------------------------------------
# Protocols: from the library into the page, then run it at the bench
# ---------------------------------------------------------------------------

def make_protocol_page(api, base):
    body = "Tail clips from litter L-2601, six pups at P14. Ear tags 41–46.\n"
    PAGES["protocol"] = f"/notebook?page={notebook_page(api, base, 'Genotyping: litter L-2601', body)}"


def protocol(d):
    d.goto(PAGES["protocol"])
    d.start()
    d.wait(0.6)
    # The lab's protocols and the common ones: type /protocol on a new line.
    last = d.page.locator(".ProseMirror > *").last
    d.click(last, after=0.3)
    d.key("End", after=0.1)
    d.key("Enter", after=0.2)
    d.page.keyboard.type("/protocol", delay=70)
    d.wait(0.6)
    d.key("Enter", after=1.0)
    d.move("text=Genotyping PCR and agarose gel", 0.8)
    d.wait(0.5)
    d.click("[data-insert-preset=hotshot]", after=1.4)
    d.click("#nb-drawer-close", after=0.6)
    chip = d.page.locator(".nb-timer-chip").first
    scroll_to(d, "text=Steps", 150, seconds=1.2)
    d.zoom(box=(530, 150, 700, 330), scale=1.5)
    d.move(chip, 0.8)
    d.wait(1.2)
    d.unzoom()
    d.click("#nb-run", after=1.2)
    d.click(".nb-run [data-act=done]", after=1.2)
    d.click(".nb-run .nb-run-timer", after=1.0)
    d.zoom(".nb-timers", scale=1.8)
    d.wait(1.8)
    d.unzoom()
    d.click(".nb-run [data-act=done]", after=1.0)
    d.click(".nb-run [data-act=done]", after=1.2)
    d.click(".nb-run [data-act=close]", after=1.0)
    d.zoom(box=(530, 150, 700, 330), scale=1.5)
    d.wait(2.4)
    d.unzoom()


# ---------------------------------------------------------------------------
# Drosophila: the rack as a grid, when it was flipped, Flipped today
# ---------------------------------------------------------------------------

def make_flies(api, base):
    """Stocks 1 last flipped a month ago, so it shows overdue."""
    import re
    html = api.get(f"{base}/stocks/drosophila").text()
    rack = re.search(r'<option value="(\d+)"[^>]*>Stocks 1</option>', html).group(1)
    r = api.post(f"{base}/stocks/drosophila/racks/{rack}/flipped", max_redirects=0,
                 form={"on": (TODAY - timedelta(days=31)).isoformat(), "back": "units"})
    if r.status >= 400:
        raise RuntimeError(f"flip: {r.status}")


def flies(d):
    d.goto("/stocks/drosophila")
    d.start()
    d.wait(1.0)
    d.move("text=Cross >> nth=1", 0.8)
    d.wait(0.6)
    d.click("[data-layout=grid]", after=1.2)
    d.zoom(box=(250, 205, 1000, 50), scale=1.6)
    d.wait(1.8)
    d.unzoom()
    pick(d, "[data-rack-group]", "Incubator 18 °C", after=1.0)
    d.zoom(box=(560, 205, 700, 50), scale=1.8)
    d.move("text=overdue", 0.8)
    d.wait(1.6)
    d.click("button:has-text('Flipped today'):visible", after=1.2)
    d.unzoom()
    d.zoom(box=(240, 100, 1020, 200), scale=1.4)
    d.wait(2.2)
    d.unzoom()
    d.click("a:has-text('Schedule') >> nth=0", after=1.8)
    d.move("text=Flip Crosses", 0.8)
    d.wait(1.2)


# ---------------------------------------------------------------------------
# The calendar: colony dates, a protocol timeline, and booking the confocal
# ---------------------------------------------------------------------------

def make_calendar(api, base):
    template = post_json(api, f"{base}/calendar/protocols/templates", {
        "name": "Tamoxifen → photometry", "steps": [
            {"from": 0, "to": 4, "title": "Tamoxifen i.p."}, {"from": 14, "to": 14, "title": "Implant fibre"},
            {"from": 21, "to": 24, "title": "Photometry sessions"}, {"from": 42, "to": 42, "title": "Perfuse"}]})
    post_json(api, f"{base}/calendar/protocols/runs", {"template_id": template["template"]["id"],
                                                       "start_date": (TODAY - timedelta(days=14)).isoformat(),
                                                       "label": "Cohort 3"})
    confocal = post_json(api, f"{base}/calendar/equipment", {"name": "Confocal LSM 900", "location": "Room 312"})
    probe = notebook_page(api, base, "Scratch", "")
    sam = member(base, "sam", demo_password(api, base, probe))
    api.post(f"{base}/notebook/pages/{probe}/delete", headers={"X-Requested-With": "fetch"})
    day = (TODAY + timedelta(days=1)).isoformat()
    sam("/calendar/bookings", {"equipment_id": confocal["equipment"]["id"], "start": f"{day}T10:00",
                               "end": f"{day}T12:00", "purpose": "Slice imaging, PV-Cre;Ai14"})


def calendar(d):
    d.goto("/calendar", settle=1.0)
    d.start()
    d.wait(0.8)
    # Point at what the month shows: which of these fall in it depends on the day it's recorded.
    for text, seconds in (("Wean", 0.9), ("Genotype", 0.7), ("Tamoxifen i.p.", 0.8), ("Implant fibre", 0.7)):
        item = d.page.locator(f"text={text}").first
        if item.count() and item.is_visible():
            d.move(item, seconds)
            d.wait(0.7)
    d.click("label:has-text('Mouse colony')", after=1.2)
    d.click("label:has-text('Mouse colony')", after=1.0)
    d.click("#cal-new-more", after=0.6)
    d.click("button.menu-item[data-new=booking]", after=0.8)
    dialog = d.page.locator("dialog[open]").first
    d.zoom(dialog, scale=1.5)
    day = (TODAY + timedelta(days=1)).isoformat()
    d.move("input[name=booking_start]", 0.6)
    d.page.fill("input[name=booking_start]", f"{day}T11:00")
    d.page.fill("input[name=booking_end]", f"{day}T13:00")
    d.wait(0.6)
    d.type("input[name=purpose]", "Imaging cohort 3 slices", delay=45)
    d.click("dialog[open] button[type=submit]", after=2.2)
    d.move("input[name=booking_start]", 0.6)
    d.page.fill("input[name=booking_start]", f"{day}T12:00")
    d.page.fill("input[name=booking_end]", f"{day}T14:00")
    d.wait(0.8)
    d.click("dialog[open] button[type=submit]", after=1.2)
    d.unzoom()
    d.click("[data-view=week]", after=1.0)
    grid = d.page.locator(".toastui-calendar-time").first
    d.scroll(-700, seconds=1.0, at=grid)
    d.wait(0.4)
    booking = d.page.locator("text=Confocal LSM 900 >> nth=0")
    try:
        box = booking.bounding_box()
        d.zoom(box=(box["x"] - 60, box["y"] - 40, 420, 260), scale=1.8)
    except Exception:  # noqa: BLE001 — the zoom is a nicety
        pass
    d.wait(2.4)
    d.unzoom()


# ---------------------------------------------------------------------------
# Batch actions with undo, and the audit log that keeps both
# ---------------------------------------------------------------------------

def undo(d):
    d.goto("/colony?view=mice")
    d.start()
    d.wait(0.6)
    d.type(".dt-search", "106", delay=140, after=1.0)
    for i in range(3):
        d.click(d.page.locator("tbody input[type=checkbox]:visible").nth(i), after=0.3)   # the rows the search left
    d.wait(0.4)
    bar = d.page.locator("[data-selection-bar]").first
    d.zoom(box=(240, 300, 800, 300), scale=1.4)
    pick(d, ".selbar select[name=field]", "status", after=0.5)
    d.type(".selbar input[name=value]", "experiment", delay=70)
    d.click(".selbar button:has-text('Set')", after=1.4)
    d.unzoom()
    d.type(".dt-search", "106", delay=60, after=0.8)
    d.zoom(box=(240, 340, 700, 130), scale=1.5)
    d.wait(1.6)
    d.unzoom()
    d.goto("/batches", settle=1.2)          # 1.0 keeps Batches under More in the sidebar
    d.zoom(box=(240, 105, 1020, 70), scale=1.5)
    d.wait(1.2)
    d.click(d.page.locator("button:has-text('Undo')").first, after=0.8)
    d.click("dialog[open] button:has-text('OK')", after=1.6)
    d.unzoom()
    d.wait(0.8)
    d.goto("/audit", settle=1.2)
    d.zoom(box=(245, 160, 1010, 300), scale=1.3)
    d.wait(2.6)
    d.unzoom()
    del bar


# ---------------------------------------------------------------------------
# Search everything with ⌘K, and tabs
# ---------------------------------------------------------------------------

def search(d):
    d.goto("/home")
    d.start()
    d.wait(0.8)
    d.move("#app-global-search", 0.8)
    d.wait(0.3)
    d.key("ControlOrMeta+k", after=0.6)
    d.page.keyboard.type("GCaMP", delay=150)
    d.wait(1.4)
    d.zoom(box=(360, 95, 560, 500), scale=1.4)
    for _ in range(3):
        d.key("ArrowDown", after=0.45)
    d.wait(0.6)
    d.click(".cmdk-row:has-text('pAAV-EF1a-DIO-GCaMP6s')", after=1.6)
    d.unzoom()
    d.wait(1.0)
    d.click("button[data-new-tab]", after=1.2)
    d.zoom(box=(220, 0, 640, 90), scale=2.0)
    d.wait(1.0)
    d.unzoom()
    d.key("ControlOrMeta+k", after=0.5)
    d.page.keyboard.type("Tamoxifen", delay=120)
    d.wait(1.2)
    d.key("Enter", after=1.6)
    d.zoom(box=(220, 0, 640, 90), scale=2.0)
    d.click("a.wtab-link >> nth=0", after=1.4)
    d.unzoom()
    d.wait(1.0)


# ---------------------------------------------------------------------------
# Looks: your own app icon and accent, and dark mode
# ---------------------------------------------------------------------------

def looks(d):
    d.goto("/home")
    d.start()
    d.wait(1.0)
    d.click("a[data-label=Settings]", after=0.8)
    d.click("a:has-text('Appearance & language') >> visible=true", after=0.8)
    scroll_to(d, "[data-icon-picker]", 150, seconds=1.0)
    d.zoom(box=(245, 90, 880, 330), scale=1.4)
    for glyph, colour in (("fly", None), (None, "lavender"), ("zebrafish", "sky"), ("mouse", "rose")):
        if glyph:
            d.click(d.page.locator(f"input[name=glyph][value={glyph}]").locator("xpath=.."), after=0.8)
        if colour:
            d.click(d.page.locator(f"input[name=color][value={colour}]").locator("xpath=.."), after=1.0)
    d.click(d.page.locator("input[name=glyph][value=fly]").locator("xpath=.."), after=0.6)
    d.click(d.page.locator("input[name=color][value=lavender]").locator("xpath=.."), after=1.2)
    d.unzoom()
    d.click("a[data-label=Home]", after=1.4)
    d.page.emulate_media(color_scheme="dark")
    d.wait(1.8)
    d.click("a[data-label='Mouse colony']", after=1.6)
    d.click("a[data-label=Calendar]", after=1.8)
    # Put the lab's look back for the clips recorded after this one.
    d.page.request.post(f"{d.base}/settings", form={"action": "appearance", "glyph": "helix", "color": "mint"},
                        headers={"X-Autosave": "1"})


# ---------------------------------------------------------------------------
# A phone: Home, and every sheet row as a card
# ---------------------------------------------------------------------------

def phone(d):
    d.goto("/home", settle=0.8)
    d.start()
    d.wait(1.2)
    d.scroll(520, seconds=1.4, at="body")
    d.wait(1.0)
    d.scroll(420, seconds=1.2)
    d.wait(0.8)
    d.click("[data-drawer-toggle]", after=0.8)
    d.click("a[data-label='Mouse colony']", after=1.4)
    d.scroll(380, seconds=1.2)
    d.wait(1.0)
    d.scroll(520, seconds=1.4)
    d.wait(1.0)
    d.scroll(-900, seconds=1.0)
    d.click("a.seg-item:has-text('Cages')", after=1.4)
    d.scroll(420, seconds=1.2)
    d.wait(1.4)


# ---------------------------------------------------------------------------
# Cage cards on a label printer: Brother, Zebra, and .zpl
# ---------------------------------------------------------------------------

def labels(d):
    d.goto("/colony?view=cages")
    d.start()
    d.click("a:has-text('Cage cards') >> nth=0", after=1.2)
    d.zoom(box=(240, 105, 520, 50), scale=2.0)
    pick(d, "select[name=stock]", "62x29", after=0.4)
    d.click("button:has-text('Show')", after=1.0)
    d.unzoom()
    d.zoom(box=(240, 240, 480, 250), scale=1.9)
    d.move("text=Cage 101", 0.8)
    d.wait(1.6)
    d.unzoom()
    pick(d, "select[name=stock]", "102x64", after=0.4)
    d.click("button:has-text('Show')", after=1.0)
    d.zoom(box=(240, 105, 1020, 260), scale=1.4)
    d.wait(1.4)
    d.move("a:has-text('Download for Zebra (.zpl)')", 0.8)
    d.wait(1.2)
    d.click("summary:has-text('Send labels straight to a Zebra')", after=1.8)
    d.unzoom()
    d.wait(0.6)
    # Back to a sheet of cards, the choice each person's cards remember.
    d.page.request.get(f"{d.base}/labels/cards/cages?scope=mine&stock=sheet")


# ---------------------------------------------------------------------------
# Connected records: samples from a mouse, and a mouse mentioned in a notebook
# ---------------------------------------------------------------------------

def make_links(api, base):
    for name, mouse in (("Liver, snap-frozen", "16"), ("Serum", "16"), ("Liver, snap-frozen", "17"), ("Serum", "17")):
        r = api.post(f"{base}/inventory/samples/items/save", form={
            "id": "", "name": name, "attr_source_kind": "mouse", "attr_source_ref": mouse,
            "attr_collected_on": TODAY.isoformat(), "attr_storage_temp": "−80 °C"})
        if not r.ok:
            raise RuntimeError(f"sample: {r.status} {r.text()[:200]}")
    body = ("## qPCR: Il6 in liver after LPS\n\n"
            "Liver from @mouse 16 (LPS 1 mg/kg), collected 4 h after injection. "
            "RNA with TRIzol, cDNA from 1 µg, Il6 and Gapdh in triplicate.\n\n"
            "Samples are in Samples, box 1, A1–A4.\n")
    PAGES["links"] = f"/notebook?page={notebook_page(api, base, 'qPCR: Il6 in liver after LPS', body)}"


def links(d):
    d.goto("/inventory/samples")
    d.start()
    d.wait(0.8)
    chip = d.page.locator(".source-chip-link:visible").first
    box = chip.bounding_box()
    # The samples' Source column: each one's mouse, a link.
    d.zoom(box=(box["x"] - 520, box["y"] - 60, 760, 200), scale=1.9)
    d.move(chip, 0.9)
    d.wait(1.4)
    d.cover()
    d.unzoom()
    d.click(chip, after=2.2)         # the mouse it came from, in the colony
    d.goto(PAGES["links"], settle=1.0)
    para = d.page.locator("[contenteditable=true] p:visible").first
    d.zoom(para, scale=1.5)
    mention = d.page.locator("[contenteditable=true] :text('@mouse 16'):visible").first
    d.move(mention if mention.count() else para, 0.9)
    d.wait(2.4)                      # its card: the mouse, from the colony
    d.move("[contenteditable=true] h2:visible", 0.6)   # away, so the card closes
    d.key("Escape", after=0.8)
    last = d.page.locator("[contenteditable=true] p:visible").last
    d.click(last, after=0.3)
    d.key("Meta+ArrowDown", after=0.2)
    d.key("Enter", after=0.2)
    d.zoom(last, scale=1.6)
    d.page.keyboard.type("Its cage mate @mouse 17 got saline.", delay=75)
    d.key("Escape", after=1.0)
    typed = d.page.locator("[contenteditable=true] :text('@mouse 17'):visible").first
    if typed.count():
        d.move(typed, 0.8)
    d.wait(2.0)
    d.unzoom()
    d.wait(0.6)


def assembly(d):
    """Build the next plasmid out of the ones the lab has: fill the tray with
    fragments, let the wizard check the junctions, and create the product."""
    d.goto("/plasmids/assembly/", settle=1.0)
    d.start()
    d.wait(1.0)
    for which in (3, 1):          # the ori, then EGFP into it
        d.click("[data-add]", after=0.9)
        picker = d.page.locator("#assembly-picker")
        d.zoom(picker, scale=1.4)
        d.wait(0.6)
        d.click("[data-pick-kind=feature]", after=0.8)
        # Choosing one is what the wizard waits for: pressing Add to the tray
        # without it answers "Choose a feature." and nothing is added.
        d.page.select_option("[data-pick-feature]", index=which)
        d.wait(0.7)
        d.click("[data-pick-ok]", after=1.2)
        d.unzoom()
        d.wait(0.7)
    # The tray, in the order the fragments go together.
    tray = d.page.locator(".assembly-tray").first
    d.zoom(tray, scale=1.5)
    d.wait(1.6)
    d.cover()
    d.unzoom()
    # The product, drawn before it exists.
    preview = d.page.locator(".assembly-preview").first
    if preview.count() and preview.is_visible():
        d.zoom(preview, scale=1.4)
        d.wait(1.8)
        d.unzoom()
    d.wait(0.8)


CLIPS = {
    "datasheet": [("desktop", datasheet)],
    "qpcr": [("desktop", qpcr)],
    "flies": [("desktop", flies)],
    "sign": [("desktop", sign)],
    "protocol": [("desktop", protocol)],
    "calendar": [("desktop", calendar)],
    "undo": [("desktop", undo)],
    "search": [("desktop", search)],
    "looks": [("desktop", looks)],
    "phone": [("phone", phone)],
    "labels": [("desktop", labels)],
    "links": [("desktop", links)],
    "assembly": [("desktop", assembly)],
}
PREPARE = {
    "datasheet": make_datasheet,
    "qpcr": make_qpcr,
    "sign": make_signed,
    "protocol": make_protocol_page,
    "flies": make_flies,
    "calendar": make_calendar,
    "links": make_links,
}
