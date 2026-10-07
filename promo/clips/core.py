"""The first week's clips: the tour, Excel import, cage cards, plasmids,
experiments, orders, a database of your own, and Home.

Each walk gets a Director (scripts/feature-clips.py) on a signed-in page of a
fresh demo lab (scripts/demo-data.py). Selectors use what a person sees:
button text, the app's data-* hooks, and names.
"""
from __future__ import annotations

import json
import random
import tempfile
from datetime import date, timedelta
from pathlib import Path

TODAY = date.today()
SHEET = Path(tempfile.gettempdir()) / "biomanager-clips" / "colony-2025.xlsx"


def post_json(api, url, body):
    r = api.post(url, data=json.dumps(body), headers={"Content-Type": "application/json"})
    if not r.ok:
        raise RuntimeError(f"{url}: {r.status} {r.text()[:200]}")
    return r.json()


# ---------------------------------------------------------------------------
# Day 0: the tour
# ---------------------------------------------------------------------------

def tour(d):
    d.goto("/home")
    d.start()
    d.wait(1.2)
    d.click("nav a[data-label='Mouse colony'], a[data-label='Mouse colony']", after=1.0)
    d.scroll(300, at="th:has-text('Transgene 1')")
    d.wait(0.6)
    d.click("a:has-text('Cages') >> nth=0", after=0.6)
    d.click("[data-layout=grid]", after=1.4)
    d.click("a[data-label=Plasmids]", after=0.8)
    d.click("a[href='/plasmid/2'] >> nth=0", after=2.2)
    d.click("a[data-label=Calendar]", after=1.8)
    d.click("a[data-label=Notebook]", after=1.2)
    d.click("a[data-label=Home]", after=1.0)


# ---------------------------------------------------------------------------
# Excel in, columns matched by meaning
# ---------------------------------------------------------------------------

def make_sheet(api, base):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Colony 2025"
    SHEET.parent.mkdir(exist_ok=True)
    ws.append(["Tag", "Sex", "DOB", "Genotype", "Cage #", "Rack", "Owner", "Comments"])
    born = [(TODAY - timedelta(days=n)).strftime("%-m/%-d/%Y") for n in (190, 172, 131)]
    for row in (("M-201", "Male", born[0], "Pvalb-Cre/+", "301", "Rack A", "alex", "founder"),
                ("M-202", "Female", born[0], "Pvalb-Cre/+", "301", "Rack A", "alex", ""),
                ("M-203", "♂", born[1], "Ai14/+", "302", "Rack A", "alex", "ear notch L"),
                ("M-204", "f", born[1], "Ai14/+", "302", "Rack A", "alex", ""),
                ("M-205", "M", born[2], "Pvalb-Cre/+; Ai14/+", "303", "Rack B", "alex", "for slice ephys"),
                ("M-206", "F", born[2], "Pvalb-Cre/+; Ai14/+", "303", "Rack B", "alex", "")):
        ws.append(list(row))
    wb.save(SHEET)


def excel_import(d):
    d.goto("/colony?view=mice")
    d.start()
    d.click("a:has-text('Import from Excel')", after=0.8)
    d.move("input[name=file]", 0.6)
    d.page.set_input_files("input[name=file]", str(SHEET))
    d.wait(0.6)
    d.click("button:has-text('Continue')", after=1.0)
    d.zoom(box=(236, 190, 1024, 460), scale=1.5)
    d.move("text=“DOB” means Date of birth", 1.0)
    d.wait(0.8)
    d.cover()
    d.wait(0.6)
    d.move("text=“Cage #” means Cage", 0.8)
    d.wait(1.0)
    d.unzoom()
    d.click("button:has-text('Preview the import')", after=1.2)
    d.scroll(250)
    d.wait(1.2)
    button = d.page.locator("button:has-text('Import')").last
    d.click(button, after=1.6)
    d.wait(1.0)


# ---------------------------------------------------------------------------
# Cage cards, and the phone that scans one
# ---------------------------------------------------------------------------

def cards_desktop(d):
    d.goto("/colony?view=cages")
    d.start()
    d.click("a:has-text('Cage cards') >> nth=0", after=1.0)
    d.zoom(box=(244, 230, 380, 226), scale=2.0)
    d.move("text=Cage 101", 0.8)
    d.cover()
    d.wait(1.6)
    d.unzoom()
    d.move("select >> nth=0", 0.8)
    d.wait(1.0)


def cards_phone(d):
    d.goto("/colony?view=cages&scope=all#cage-1", settle=1.0)
    d.start()
    d.wait(1.8)
    d.scroll(260, seconds=1.2)
    d.wait(1.6)


# ---------------------------------------------------------------------------
# Plasmids: the map, and where the tube is
# ---------------------------------------------------------------------------

def plasmid(d):
    """Molecular biology end to end. Kept brisk: this plays in the front
    page's dock beside clips of about twelve seconds, and the wizard — the
    part worth staying for — has to arrive before a visitor clicks away."""
    d.goto("/plasmids")
    d.start()
    d.wait(0.6)
    # Primers, glycerol stocks and viruses are tabs of the same area.
    strip = d.page.locator("nav.seg").first
    if strip.count() and strip.is_visible():
        d.zoom(strip, scale=1.8)
        d.move("a.seg-item >> nth=1", 0.7)
        d.wait(0.5)
        d.unzoom()
    # A map, briefly.
    d.click("a[href='/plasmid/2'] >> nth=0", after=1.5)
    d.zoom(box=(250, 300, 500, 420), scale=1.8)
    d.wait(0.9)
    d.cover()
    d.unzoom()
    d.wait(0.4)
    # And the next construct, built out of the ones the lab has.
    d.goto("/plasmids/assembly/", settle=0.8)
    d.wait(0.6)
    for which in (3, 1):          # ori, then EGFP into it
        d.click("[data-add]", after=0.7)
        d.zoom(d.page.locator("#assembly-picker"), scale=1.4)
        d.click("[data-pick-kind=feature]", after=0.6)
        d.page.select_option("[data-pick-feature]", index=which)
        d.wait(0.5)
        d.click("[data-pick-ok]", after=0.9)
        d.unzoom()
        d.wait(0.3)
    tray = d.page.locator(".assembly-tray").first
    d.zoom(tray, scale=1.5)
    d.wait(1.2)
    d.unzoom()
    preview = d.page.locator(".assembly-preview").first
    if preview.count() and preview.is_visible():
        d.zoom(preview, scale=1.4)
        d.wait(1.3)
        d.unzoom()
    d.wait(0.4)


# ---------------------------------------------------------------------------
# Experiments: doses from body weight, the chart, the tests
# ---------------------------------------------------------------------------

EXPERIMENT_URL = {}


def make_experiment(api, base):
    start = TODAY - timedelta(days=9)
    r = api.post(f"{base}/colony/experiments/create", max_redirects=0, form={
        "name": "Tamoxifen induction", "start_date": start.isoformat(),
        "description": "Cre induction in Vglut2-Cre mice", "treatment_plan": "Tamoxifen 75 mg/kg i.p. days 1–5, or corn oil"})
    exp = int(r.headers["location"].rstrip("/").rsplit("/", 1)[1])
    EXPERIMENT_URL["path"] = f"/colony/experiments/{exp}"
    for cage, group in (("109", "Tamoxifen"), ("104", "Corn oil")):
        post_json(api, f"{base}/experiments/{exp}/subjects/add", {"how": "group", "value": cage, "group": group})
    data = api.get(f"{base}/experiments/{exp}/data.json").json()
    rng = random.Random(7)
    base_weight = {s["key"]: 19.5 + rng.random() * 2 for s in data["subjects"]}
    group = {s["key"]: s["group"] for s in data["subjects"]}
    dip = [0, 0, -1.5, -4, -7, -9, -8, -6, -4, -3]   # % change on days 1–10 under tamoxifen
    for day in range(10):
        values = {}
        for key, w in base_weight.items():
            change = dip[day] if group[key] == "Tamoxifen" else day * 0.25
            values[key] = f"{w * (1 + (change + rng.uniform(-0.8, 0.8)) / 100):.1f}"
        post_json(api, f"{base}/experiments/{exp}/readings", {"on": (start + timedelta(days=day)).isoformat(),
                                                               "values": values})
    tam = post_json(api, f"{base}/colony/experiments/{exp}/steps/save", {
        "kind": "injection", "agent": "Tamoxifen", "dose": "75 mg/kg", "route": "i.p.", "concentration": "20 mg/mL",
        "days": "1/2/3/4/5", "group": "Tamoxifen"})
    oil = post_json(api, f"{base}/colony/experiments/{exp}/steps/save", {
        "kind": "injection", "agent": "Corn oil", "dose": "100 µL", "route": "i.p.", "days": "1/2/3/4/5",
        "group": "Corn oil"})
    for step in (tam["steps"][0]["id"], oil["steps"][-1]["id"]):
        for day in range(1, 6):
            post_json(api, f"{base}/colony/experiments/{exp}/steps/{step}/day/{day}/record", {})


def experiment(d):
    d.goto(EXPERIMENT_URL["path"])
    d.start()
    d.wait(0.8)
    d.click("[data-xp-view=readout]", after=1.2)
    d.zoom(box=(240, 340, 1020, 300), scale=1.3)
    d.wait(1.4)
    d.unzoom()
    d.scroll(520, seconds=1.2, at="text=Body weight >> nth=1")
    d.wait(1.0)
    chart = d.page.locator("svg").filter(has=d.page.locator("path")).nth(1)
    try:
        box = chart.bounding_box()
        d.zoom(box=(box["x"], box["y"], box["width"], box["height"]), scale=1.4)
        d.wait(0.9)
        d.cover()
        for fx in (0.3, 0.45, 0.6):
            d.page.mouse.move(box["x"] + box["width"] * fx, box["y"] + box["height"] * 0.5, steps=20)
            d.wait(0.9)
    except Exception:  # noqa: BLE001 — the chart is optional to the clip
        pass
    d.unzoom()
    d.scroll(380, seconds=1.0)
    d.wait(1.6)


# ---------------------------------------------------------------------------
# Orders: the board, and received goes to stock
# ---------------------------------------------------------------------------

def orders(d):
    d.goto("/inventory/orders")
    d.start()
    d.click("[data-layout=board]", after=1.0)
    card = d.page.locator("article.board-card:has-text('Tamoxifen')").first
    target = d.page.locator(".board-col[data-status=received] [data-board-drop]").first
    d.move(card, 0.8)
    d.page.mouse.down()
    box = target.bounding_box()
    x0, y0 = d.pos
    x1, y1 = box["x"] + box["width"] / 2, box["y"] + box["height"] - 30
    for i in range(1, 46):
        k = i / 45
        d.page.mouse.move(x0 + (x1 - x0) * k, y0 + (y1 - y0) * k)
        d.page.wait_for_timeout(16)
    d.page.mouse.up()
    d.pos = (x1, y1)
    d.wait(1.8)
    dialog = d.page.locator("dialog[open]").first
    if dialog.count():
        d.zoom(dialog)
        d.wait(1.0)
        d.cover()
        d.wait(1.2)
        add = dialog.locator("button:has-text('Add to stock')")
        if add.count():
            d.click(add, after=1.2)
        d.unzoom()
    d.wait(0.8)


# ---------------------------------------------------------------------------
# A database for any organism, in its own words
# ---------------------------------------------------------------------------

def new_database(d):
    d.goto("/home")
    d.start()
    d.click("a[data-label='Add database']", after=0.8)
    d.click("a:has-text('Custom organism')", after=0.8)
    label = d.page.locator("input[name=label]")
    d.click(label, after=0.2)
    label.fill("")
    d.page.keyboard.type("Xenopus colony", delay=60)
    for name, word in (("organism_noun", "frog"), ("housing_noun", "tank"), ("line_noun", "line"),
                       ("cohort_noun", "clutch")):
        field = d.page.locator(f"input[name={name}]")
        d.click(field, after=0.1)
        field.fill("")
        d.page.keyboard.type(word, delay=70)
        d.page.keyboard.press("Tab")
        d.wait(0.2)
    frog = d.page.locator("input[name=icon][value=frog]")
    d.click(frog.locator("xpath=..") if frog.count() else frog, after=0.6)
    d.scroll(420, seconds=1.0)
    for cap in ("crosses", "genotyping", "weights"):
        box = d.page.locator(f"input[name=capabilities][value={cap}]")
        if box.count() and not box.is_checked():
            d.click(box, after=0.3)
    d.click("button:has-text('Create database')", after=1.2)
    d.cover()
    d.wait(1.0)


# ---------------------------------------------------------------------------
# Home: what needs doing today
# ---------------------------------------------------------------------------

def home(d):
    d.goto("/home")
    d.start()
    d.wait(1.0)
    d.zoom(box=(240, 190, 1020, 110), scale=1.3)
    d.move("text=WEANINGS", 1.0)
    d.wait(1.2)
    d.unzoom()
    d.wait(0.8)
    d.cover()
    d.scroll(420, at="text=Upcoming weanings")
    d.wait(1.0)
    d.scroll(-420)
    d.click("button[name=layout]:has-text('Tracks')", after=1.8)
    d.click("button[name=layout]:has-text('Freezer')", after=2.0)
    d.click("button[name=layout]:has-text('Classic')", after=0.8)


CLIPS = {
    "tour": [("desktop", tour)],
    "import": [("desktop", excel_import)],
    "cards": [("desktop", cards_desktop), ("phone", cards_phone)],
    "plasmid": [("desktop", plasmid)],
    "experiment": [("desktop", experiment)],
    "orders": [("desktop", orders)],
    "new-database": [("desktop", new_database)],
    "home": [("desktop", home)],
}
PREPARE = {
    "import": make_sheet,
    "experiment": make_experiment,
}
