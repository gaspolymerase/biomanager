#!/usr/bin/env python3
"""How BioManager holds up with a big lab: fill a demo lab with many
records, then time the pages people open.

    .venv/bin/python scripts/load-test.py /tmp/bm-load            # 100,000 mice and the rest
    .venv/bin/python scripts/load-test.py /tmp/bm-load --mice 20000
    .venv/bin/python scripts/load-test.py /tmp/bm-load --reuse    # time again, same data

The folder must be empty (or --reuse one this made). It is a demo lab
(scripts/demo-data.py) with, added in bulk: mice in cages and litters,
their weights, zebrafish tanks and fish rows, fly vials, worm strains,
plants, plasmids (a fifth with a sequence), inventory items, orders,
calendar events and to-dos, and notebook pages. Like a real colony it has years of history: most
mice died, most cages emptied and most tubes were used up, on days
spread over five years. Each page is fetched twice as the demo admin; the
second time is reported, with the size of the answer. SQLite: a lab
server on PostgreSQL is usually faster.

    .venv/bin/python scripts/load-test.py /tmp/bm-load --reuse --browser

--browser also opens each page in Chromium (pip install playwright, then
playwright install chromium; CHROMIUM=<path> to use another), as served
by the app on this computer, and reports what the person waits for: the
time to the page's load event, the browser's busy time (scripts, styles,
layout), the page's DOM nodes and its JS heap, each the median of three.
"""
from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = [
    "/home", "/colony?view=mice", "/colony?view=cages", "/colony?view=litters", "/colony?view=breeders",
    "/colony?view=experiments", "/zebrafish?view=tanks", "/zebrafish?view=fish", "/stocks/drosophila",
    "/inventory/reagents", "/inventory/samples", "/plasmids", "/calendar",
    f"/calendar/events.json?start={date.today() - timedelta(days=35)}&end={date.today() + timedelta(days=7)}",
    "/search?q=Ai14", "/notebook", "/audit", "/batches", "/admin/colony",
    "/stocks/c_elegans", "/organisms/arabidopsis", "/inventory/orders", "/calendar/board",
]


def fill(n_mice: int, alive: float = 0.05) -> None:
    from sqlalchemy import insert, select
    from app.db import SessionLocal
    from app.models import (CageRecord, FishRecord, InventoryItem, InventoryModule, LitterRecord, MouseRecord,
                            MouseWeight, NotebookPage, NotebookTab, StockModule, StockUnit, TankRecord, UserAccount)
    rnd = random.Random(7)
    today = date.today()
    genes = ["Ai14/+", "DAT-IRES-Cre/+", "Sst-Cre/+", "Pvalb-Cre/Pvalb-Cre", "WT", "Vglut2-Cre/+", "Rosa26-CreERT2/+"]
    with SessionLocal() as s:
        users = [u for u in s.scalars(select(UserAccount.username))]
        start_mouse = (s.scalar(select(MouseRecord.mouse_id).order_by(MouseRecord.mouse_id.desc()).limit(1)) or 0) + 1
        n_cages, n_litters = n_mice // 4, n_mice // 6
        t = time.time()
        s.execute(insert(CageRecord), [{"cage_id": f"L{i:06d}", "owner": rnd.choice(users), "purpose": rnd.choice(["Breeder", "Breeding", "Experiment"]),
                                        "room": rnd.choice(["B12", "B14", "C3"]),
                                        "created_at": datetime.utcnow() - timedelta(days=rnd.randint(0, 1800))} for i in range(n_cages)])
        s.execute(insert(LitterRecord), [{"litter_id": f"LT{i:06d}", "date_of_birth": today - timedelta(days=rnd.randint(1, 1800)),
                                          "created_at": datetime.utcnow()} for i in range(n_litters)])
        s.commit()
        cage_ids = [c for c in s.scalars(select(CageRecord.id).where(CageRecord.cage_id.like("L%")))]
        litter_ids = [c for c in s.scalars(select(LitterRecord.id).where(LitterRecord.litter_id.like("LT%")))]
        batch = []
        for i in range(n_mice):
            dead = rnd.random() >= alive
            batch.append({"mouse_id": start_mouse + i, "gender": rnd.choice("MF"), "transgene_1": rnd.choice(genes),
                          "genotype": rnd.choice(genes), "status": "sac" if dead else rnd.choice(["breeder", "experiment", "geno"]),
                          "owner": rnd.choice(users), "cage_id_fk": None if dead else rnd.choice(cage_ids),
                          "litter_id_fk": rnd.choice(litter_ids), "date_of_death": today - timedelta(days=rnd.randint(1, 1800)) if dead else None,
                          "note": "", "created_at": datetime.utcnow()})
            if len(batch) == 20000:
                s.execute(insert(MouseRecord), batch)
                batch = []
        if batch:
            s.execute(insert(MouseRecord), batch)
        s.commit()
        mice = [m for m in s.scalars(select(MouseRecord.id).where(MouseRecord.mouse_id >= start_mouse))]
        s.execute(insert(MouseWeight), [{"mouse_id_fk": rnd.choice(mice), "weigh_date": today - timedelta(days=rnd.randint(0, 200)),
                                         "grams": round(rnd.uniform(18, 32), 1), "recorded_by": users[0], "notes": "",
                                         "created_at": datetime.utcnow()} for _ in range(n_mice // 2)])
        s.execute(insert(TankRecord), [{"tank_id": f"LT-{i:05d}", "owner": rnd.choice(users), "purpose": "stock", "active": True,
                                        "created_at": datetime.utcnow()} for i in range(n_mice // 30)])
        s.commit()
        tanks = [t_ for t_ in s.scalars(select(TankRecord.id).where(TankRecord.tank_id.like("LT-%")))]
        s.execute(insert(FishRecord), [{"tank_id_fk": rnd.choice(tanks), "count": rnd.randint(1, 30), "sex": "mixed", "status": "alive",
                                        "genotype": rnd.choice(genes), "created_at": datetime.utcnow()} for _ in range(n_mice // 6)])
        fly = s.scalar(select(StockModule.id).where(StockModule.key == "drosophila"))
        if fly:
            s.execute(insert(StockUnit), [{"module_id_fk": fly, "number": 100000 + i, "genotype": f"w; P{{UAS-x{i % 400}}}",
                                           "purpose": "stock", "active": i % 10 < 2, "owner": rnd.choice(users), "attrs": "{}",
                                           "discarded_on": None if i % 10 < 2 else today - timedelta(days=rnd.randint(1, 1800))}
                                          for i in range(n_mice // 10)])
        for key in ("reagents", "samples"):
            mid = s.scalar(select(InventoryModule.id).where(InventoryModule.key == key))
            if mid:
                import json as _json
                s.execute(insert(InventoryItem), [{"module_id_fk": mid, "number": 100000 + i, "name": f"{key[:-1]} {i}",
                                                   "status": "" if i % 10 < 2 else "used up", "owner": rnd.choice(users),
                                                   "attrs": "{}" if i % 10 < 2 else _json.dumps(
                                                       {"used_up_on": (today - timedelta(days=rnd.randint(1, 1800))).isoformat()})}
                                                  for i in range(n_mice // 5)])
        tab = s.scalar(select(NotebookTab.id).order_by(NotebookTab.id).limit(1))
        if tab:
            s.execute(insert(NotebookPage), [{"tab_id_fk": tab, "title": f"Notes {i}", "body": "Lorem ipsum. " * 40, "position": i,
                                              "properties": "", "created_at": datetime.utcnow(), "updated_at": datetime.utcnow()}
                                             for i in range(n_mice // 40)])
        s.commit()
        print(f"filled in {time.time() - t:.0f} s: {n_mice} mice, {n_cages} cages, {n_litters} litters, "
              f"{n_mice // 2} weights, {len(tanks)} tanks, {n_mice // 6} fish rows, {n_mice // 10} vials, "
              f"{2 * n_mice // 5} inventory items, {n_mice // 40} notebook pages")
    fill_more(n_mice)


def fill_more(n_mice: int) -> None:
    """The lab's other databases, in proportion to its mice: worm strains,
    the plant database's plants, plasmids (a fifth with a sequence), orders,
    and five years of calendar events and to-dos. Like the rest, most of it
    is history: discarded, dead, received, done."""
    import json
    from sqlalchemy import func, insert, select
    from app.db import SessionLocal
    from app.models import (CalendarEvent, InventoryItem, InventoryModule, Organism, OrganismModule, PlasmidRecord,
                            StockModule, StockUnit, TaskItem, UserAccount)
    rnd = random.Random(11)
    today = date.today()
    ago = lambda: today - timedelta(days=rnd.randint(1, 1800))
    t = time.time()
    with SessionLocal() as s:
        users = [u for u in s.scalars(select(UserAccount.username))]
        worm = s.scalar(select(StockModule.id).where(StockModule.key == "c_elegans"))
        if worm:
            s.execute(insert(StockUnit), [{"module_id_fk": worm, "number": 200000 + i, "genotype": f"unc-{i % 120}(e{i % 900}) X",
                                           "purpose": "stock", "active": i % 10 < 2, "owner": rnd.choice(users), "attrs": "{}",
                                           "discarded_on": None if i % 10 < 2 else ago()} for i in range(n_mice // 20)])
        plant = s.scalar(select(OrganismModule.id).where(OrganismModule.key == "arabidopsis"))
        if plant:
            s.execute(insert(Organism), [{"module_id_fk": plant, "code": f"AT-{i:06d}", "count": 1, "status": "",
                                          "birth_on": ago(), "death_on": None if i % 10 < 3 else ago(),
                                          "genotype": f"Col-0 x{i % 50}", "owner": rnd.choice(users), "attrs": "{}",
                                          "notes": "", "created_at": datetime.utcnow()} for i in range(n_mice // 10)])
        start = (s.scalar(select(func.max(PlasmidRecord.plasmid_id))) or 0) + 1
        seq = lambda: "".join(rnd.choice("ACGT") for _ in range(rnd.randint(4000, 9000)))
        s.execute(insert(PlasmidRecord), [{"plasmid_id": start + i, "name": f"pLoad-{i}", "backbone": "pAAV",
                                           "insert_seq": f"insert {i % 300}", "resistance": "Amp", "owner": rnd.choice(users),
                                           "full_sequence": seq() if i % 5 == 0 else "", "is_circular": True,
                                           "features_json": "", "created_at": datetime.utcnow()} for i in range(n_mice // 20)])
        orders = s.scalar(select(InventoryModule.id).where(InventoryModule.key == "orders"))
        if orders:
            s.execute(insert(InventoryItem), [{"module_id_fk": orders, "number": 300000 + i, "name": f"order {i}",
                                               "status": "requested" if i % 25 == 0 else "received", "owner": rnd.choice(users),
                                               "attrs": json.dumps({"ordered_on": ago().isoformat()})} for i in range(n_mice // 25)])
        s.execute(insert(CalendarEvent), [{"title": f"Event {i}", "event_date": today + timedelta(days=rnd.randint(-1800, 60)),
                                           "is_all_day": True, "owner": rnd.choice(users), "event_type": "experiment",
                                           "description": "", "is_shared": i % 3 == 0, "created_at": datetime.utcnow()}
                                          for i in range(n_mice // 20)])
        s.execute(insert(TaskItem), [{"title": f"To-do {i}", "due_date": today + timedelta(days=rnd.randint(-1800, 30)),
                                      "status": "todo" if i % 5 == 0 else "done", "priority": "medium", "owner": rnd.choice(users),
                                      "is_shared": i % 4 == 0, "notes": "", "created_at": datetime.utcnow()}
                                     for i in range(n_mice // 50)])
        s.commit()
    print(f"and in {time.time() - t:.0f} s: {n_mice // 20} worm strains, {n_mice // 10} plants, {n_mice // 20} plasmids, "
          f"{n_mice // 25} orders, {n_mice // 20} calendar events, {n_mice // 50} to-dos")


def time_pages(data: Path) -> None:
    from app.app import app
    password = (data / "demo-password").read_text(encoding="utf-8").strip()
    c = app.test_client()
    c.post("/login", data={"username": "alex", "password": password}, headers={"Origin": "http://localhost"})
    print(f"\n{'page':<58} {'ms':>7} {'KB':>8}", flush=True)
    for page in PAGES:
        c.get(page)
        t = time.perf_counter()
        r = c.get(page)
        ms = (time.perf_counter() - t) * 1000
        flag = "  slow" if ms > 1000 else ""
        print(f"{page[:58]:<58} {ms:7.0f} {len(r.data) / 1024:8.0f}  {r.status_code}{flag}", flush=True)


def time_in_browser(data: Path) -> None:
    import statistics
    import threading

    from playwright.sync_api import sync_playwright
    from werkzeug.serving import WSGIRequestHandler, make_server

    from app.app import app

    class Quiet(WSGIRequestHandler):
        def log_request(self, *args, **kwargs):
            pass

    server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    password = (data / "demo-password").read_text(encoding="utf-8").strip()
    pages = [p for p in PAGES if not p.startswith(("/calendar/events.json", "/admin"))]
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        tab = ctx.new_page()
        tab.goto(base + "/login")
        tab.fill("input[name=username]", "alex")
        tab.fill("input[name=password]", password)
        tab.press("input[name=password]", "Enter")
        tab.wait_for_load_state("load")
        tab.close()
        print(f"\n{'in Chromium':<40} {'load ms':>8} {'busy ms':>8} {'nodes':>8} {'heap MB':>8}", flush=True)
        for page in pages:
            runs = []
            for _ in range(3):
                tab = ctx.new_page()
                cdp = ctx.new_cdp_session(tab)
                cdp.send("Performance.enable")
                tab.goto(base + page, wait_until="load", timeout=600000)
                tab.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
                load = tab.evaluate("() => performance.getEntriesByType('navigation')[0].loadEventEnd")
                cdp.send("HeapProfiler.collectGarbage")
                m = {x["name"]: x["value"] for x in cdp.send("Performance.getMetrics")["metrics"]}
                runs.append((load, m["TaskDuration"] * 1000, m["Nodes"], m["JSHeapUsedSize"] / 1e6))
                tab.close()
            load, busy, nodes, heap = (statistics.median(col) for col in zip(*runs))
            flag = "  slow" if load > 3000 else ""
            print(f"{page[:40]:<40} {load:8.0f} {busy:8.0f} {nodes:8.0f} {heap:8.1f}{flag}", flush=True)
        browser.close()
    server.shutdown()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--mice", type=int, default=100_000)
    ap.add_argument("--alive", type=float, default=0.05,
                    help="how many of them are alive now (a colony keeps its history: 0.05 is 5,000 of 100,000)")
    ap.add_argument("--reuse", action="store_true")
    ap.add_argument("--browser", action="store_true", help="also time each page in Chromium (needs playwright)")
    args = ap.parse_args()
    data = Path(args.folder).expanduser().resolve()
    if not args.reuse:
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "demo-data.py"), str(data)], capture_output=True, text=True)
        if r.returncode:
            sys.exit(r.stderr[-2000:])
    os.environ["BIOMANAGER_DATA_DIR"] = str(data)
    os.environ["DATABASE_URL"] = f"sqlite:///{data / 'biomanager.db'}"
    sys.path.insert(0, str(ROOT))
    if not args.reuse:
        fill(args.mice, args.alive)
    time_pages(data)
    if args.browser:
        time_in_browser(data)


if __name__ == "__main__":
    main()
