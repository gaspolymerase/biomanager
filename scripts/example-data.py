#!/usr/bin/env python3
"""Add a made-up lab's records to a real BioManager, to try it out.

    docker compose exec app python scripts/example-data.py <admin username>

Unlike demo-data.py (a whole new lab in an empty folder, for screenshots),
this writes into the database the app is configured with, next to what is
already there: mice and cages, zebrafish, fly stocks, plasmids, orders,
reagents, antibodies, calendar items and a notebook page. It goes in through
the app's own routes, as the given admin and three example members
(ex-sam, ex-jordan, ex-priya). They have no password, so nobody can sign in
as them; disable them in Admin when you are done.

It refuses to run on a lab that already has mice, cages, tanks or plasmids,
or twice. Take a backup first: restoring it is the way to remove everything
this adds.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

if len(sys.argv) != 2:
    sys.exit(__doc__)
ADMIN = sys.argv[1]

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app import security  # noqa: E402
from app.app import app  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models import UserAccount  # noqa: E402

TODAY = date.today()
# Cookies are Secure on a server behind HTTPS: talk to the app as that site.
BASE = (os.environ.get("BIOMANAGER_BASE_URL") or "https://localhost").rstrip("/")


def ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


def ahead(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def one(sql: str, *args):
    if engine.dialect.name == "postgresql":
        sql = sql.replace("%", "%%").replace("?", "%s")
    with engine.connect() as con:
        found = con.exec_driver_sql(sql, tuple(args)).fetchone()
    return found[0] if found else None


# ------------------------------------------------------------------ checks

for table in ("mice", "mouse_cages", "tanks", "plasmids"):
    if one(f"select count(*) from {table}"):
        sys.exit(f"The lab already has {table.replace('_', ' ')}; this is for trying out an empty one.")
if one("select count(*) from users where username like 'ex-%'"):
    sys.exit("Example data was added already (there are ex-… accounts).")
if one("select role from users where username=?", ADMIN) != "admin":
    sys.exit(f"{ADMIN} is not an admin account here.")

# ------------------------------------------------------------------ people

# username, display name, short name (the owner badge; "ex-…" would read "EX-" for all)
PEOPLE = [("ex-sam", "Sam Okafor (example)", "Sam"), ("ex-jordan", "Jordan Lee (example)", "Jor"),
          ("ex-priya", "Priya Nair (example)", "Pri")]
with SessionLocal() as s:
    for username, name, short in PEOPLE:
        s.add(UserAccount(username=username, display_name=name, short_name=short, role="member",
                          password_hash=security.NO_PASSWORD, welcomed_at=datetime.utcnow()))
    s.commit()


class Client:
    """A signed-in test client that reports any request the app refused."""

    def __init__(self, username: str):
        self.c = app.test_client()
        with SessionLocal() as s, app.app_context():
            user = s.scalar(select(UserAccount).where(UserAccount.username == username))
            stamp, uid = security.session_stamp(user), user.id
        with self.c.session_transaction(base_url=BASE) as sess:
            sess["user_id"] = uid
            sess["auth"] = stamp

    def post(self, path: str, **kwargs):
        r = self.c.post(path, base_url=BASE, **kwargs)
        if r.status_code >= 400:
            print(f"  refused ({r.status_code}): POST {path}", file=sys.stderr)
        with self.c.session_transaction(base_url=BASE) as sess:
            for kind, text in sess.pop("_flashes", []):
                if kind == "error":
                    print(f"  refused: POST {path}: {text}", file=sys.stderr)
        return r


alex = Client(ADMIN)
sam, jordan, priya = (Client(p[0]) for p in PEOPLE)
GRID = {"naming_mode": "grid", "naming_rows": "letters", "naming_cols": "numbers",
        "naming_order": "row_col", "naming_separator": "", "naming_start": "1"}

# ------------------------------------------------------------------ mouse colony

for name, rows, cols in (("Rack A", 5, 7), ("Rack B", 5, 7)):
    alex.post("/colony/racks/save", data={"id": "", "name": name, "rows": str(rows), "cols": str(cols), **GRID})
rack_a = one("select id from mouse_racks where name='Rack A'")
rack_b = one("select id from mouse_racks where name='Rack B'")

for number, name, background, supplier, desc in (
    ("000664", "C57BL/6J", "C57BL/6J", "JAX", "Wild-type background"),
    ("006660", "DAT-IRES-Cre", "C57BL/6J", "JAX", "Cre in dopaminergic neurons"),
    ("007914", "Ai14", "C57BL/6J", "JAX", "Cre-dependent tdTomato reporter"),
    ("016963", "Vglut2-IRES-Cre", "C57BL/6J", "JAX", "Cre in glutamatergic neurons"),
    ("017320", "PV-Cre", "C57BL/6J", "JAX", "Cre in parvalbumin interneurons"),
):
    alex.post("/colony/strains/create", data={"strain_number": number, "strain_name": name,
                                               "strain_background": background, "supplier": supplier,
                                               "description": desc})

# cage, owner client, owner, rack, position, purpose, gave birth (days ago).
# Breeding: mating, so the cages with litters; Breeder: the lab's breeding stock.
CAGES = [
    ("101", sam, "ex-sam", rack_a, "A1", "Breeding", 12),
    ("102", sam, "ex-sam", rack_a, "A2", "Breeding", 17),
    ("103", sam, "ex-sam", rack_a, "A3", "Experiment", None),
    ("104", sam, "ex-sam", rack_a, "A4", "Experiment", None),
    ("105", jordan, "ex-jordan", rack_a, "B1", "Breeding", 5),
    ("106", jordan, "ex-jordan", rack_a, "B2", "Breeder", None),
    ("107", jordan, "ex-jordan", rack_a, "B3", "Experiment", None),
    ("108", priya, "ex-priya", rack_a, "C1", "Breeder", None),
    ("109", priya, "ex-priya", rack_a, "C2", "Experiment", None),
    ("110", priya, "ex-priya", rack_a, "C3", "Breeding", 19),
    ("111", alex, ADMIN, rack_b, "A1", "Breeder", None),
    ("112", alex, ADMIN, rack_b, "A2", "Breeder", None),
    ("113", sam, "ex-sam", rack_b, "B1", "Experiment", None),
    ("114", jordan, "ex-jordan", rack_b, "B2", "Experiment", None),
]
for code, c, owner, rack, pos, purpose, born in CAGES:
    c.post("/colony/cages/create", data={"cage_id": code, "owner": owner, "rack_id": str(rack),
                                          "position": pos, "purpose": purpose, "room": "B-204"})
    if born is not None:
        cage_row = one("select id from mouse_cages where cage_id=?", code)
        c.post(f"/colony/cages/{cage_row}/update", data={"date_give_birth": ago(born)},
               headers={"X-Autosave": "1"})

# cage, sex, transgenes, dob (days ago), litter, status, note
MICE = [
    ("101", "M", ["DAT-IRES-Cre/+"], 210, "L-2601", "breeding", "Proven breeder"),
    ("101", "F", ["Ai14/Ai14"], 190, "L-2602", "breeding", ""),
    ("101", "F", ["Ai14/Ai14"], 190, "L-2602", "breeding", ""),
    ("102", "M", ["Vglut2-Cre/+"], 240, "L-2603", "breeding", "Retire after this litter"),
    ("102", "F", ["C57BL/6J"], 230, "L-2604", "breeding", ""),
    ("103", "M", ["DAT-IRES-Cre/+", "Ai14/+"], 70, "L-2610", "experiment", "Fiber implanted"),
    ("103", "M", ["DAT-IRES-Cre/+", "Ai14/+"], 70, "L-2610", "experiment", "Fiber implanted"),
    ("103", "M", ["DAT-IRES-Cre/+"], 70, "L-2610", "experiment", ""),
    ("103", "M", ["+/+", "Ai14/+"], 70, "L-2610", "experiment", "Control"),
    ("104", "F", ["DAT-IRES-Cre/+", "Ai14/+"], 72, "L-2611", "experiment", ""),
    ("104", "F", ["DAT-IRES-Cre/+", "Ai14/+"], 72, "L-2611", "experiment", ""),
    ("104", "F", ["+/+", "Ai14/+"], 72, "L-2611", "experiment", "Control"),
    ("105", "M", ["PV-Cre/PV-Cre"], 150, "L-2605", "breeding", ""),
    ("105", "F", ["Ai14/Ai14"], 140, "L-2606", "breeding", ""),
    ("106", "F", ["PV-Cre/+"], 95, "L-2612", "stock", ""),
    ("106", "F", ["PV-Cre/+"], 95, "L-2612", "stock", ""),
    ("106", "F", ["PV-Cre/+"], 95, "L-2612", "stock", ""),
    ("107", "M", ["PV-Cre/+", "Ai14/+"], 84, "L-2613", "experiment", "Slice ephys"),
    ("107", "M", ["PV-Cre/+", "Ai14/+"], 84, "L-2613", "experiment", "Slice ephys"),
    ("108", "M", ["Vglut2-Cre/+"], 120, "L-2614", "stock", ""),
    ("108", "M", ["Vglut2-Cre/+"], 120, "L-2614", "stock", ""),
    ("109", "F", ["Vglut2-Cre/+"], 60, "L-2615", "experiment", "AAV injected"),
    ("109", "F", ["Vglut2-Cre/+"], 60, "L-2615", "experiment", "AAV injected"),
    ("109", "F", ["Vglut2-Cre/+"], 60, "L-2615", "experiment", "AAV injected"),
    ("110", "M", ["C57BL/6J"], 230, "L-2607", "breeding", ""),
    ("110", "F", ["Vglut2-Cre/+"], 180, "L-2608", "breeding", ""),
    ("111", "M", ["C57BL/6J"], 56, "L-2616", "stock", ""),
    ("111", "M", ["C57BL/6J"], 56, "L-2616", "stock", ""),
    ("111", "M", ["C57BL/6J"], 56, "L-2616", "stock", ""),
    ("112", "F", ["C57BL/6J"], 56, "L-2617", "stock", ""),
    ("112", "F", ["C57BL/6J"], 56, "L-2617", "stock", ""),
    ("113", "M", ["DAT-IRES-Cre/+"], 110, "L-2618", "experiment", "Behaviour cohort 2"),
    ("113", "M", ["DAT-IRES-Cre/+"], 110, "L-2618", "experiment", "Behaviour cohort 2"),
    ("114", "F", ["PV-Cre/+"], 100, "L-2619", "experiment", ""),
]
owners = {code: (c, owner) for code, c, owner, *_ in CAGES}
for cage, sex, tg, dob, litter, status, note in MICE:
    c, owner = owners[cage]
    data = {"cage_id": cage, "litter_id": litter, "date_of_birth": ago(dob), "gender": sex,
            "status": status, "owner": owner, "note": note}
    data.update({f"transgene_{i + 1}": v for i, v in enumerate(tg)})
    c.post("/colony/mice/create", data=data)

# Two that have been used, so the sheet shows the alive dot doing its job.
for cage, dob in (("113", 110), ("114", 100)):
    c, owner = owners[cage]
    c.post("/colony/mice/create", data={"cage_id": cage, "date_of_birth": ago(dob), "gender": "M" if cage == "113" else "F",
                                         "status": "sacrificed", "owner": owner, "date_of_death": ago(3),
                                         "transgene_1": "DAT-IRES-Cre/+" if cage == "113" else "PV-Cre/+",
                                         "note": "Perfused, brain to histology"})

# Litters on the way to weaning (P21), and last month's pups waiting on genotyping.
for c, code, born, pups, cohort in ((sam, "L-2620", 17, "7", "DAT-Cre x Ai14"),
                                    (priya, "L-2621", 19, "6", "Vglut2-Cre x B6"),
                                    (sam, "L-2622", 12, "8", "DAT-Cre x Ai14"),
                                    (jordan, "L-2623", 5, "5", "PV-Cre x Ai14")):
    c.post("/colony/litters/create", data={"litter_id": code, "date_of_birth": ago(born), "total_pups": pups,
                                            "cohort_name": cohort})
for sex in ("M", "M", "F"):
    sam.post("/colony/mice/create", data={"cage_id": "113", "litter_id": "L-2609", "date_of_birth": ago(30),
                                           "gender": sex, "status": "geno", "owner": "ex-sam",
                                           "note": "Tail clipped, PCR pending"})

sam.post("/colony/experiments/create", data={"name": "Fiber photometry: reward", "start_date": ago(14),
                                              "description": "DA release to sucrose in DAT-Cre;Ai14",
                                              "treatment_plan": "Sucrose vs water, 10 sessions",
                                              "from_cage_id": "103"})

# ------------------------------------------------------------------ zebrafish

alex.post("/zebrafish/systems/create", data={"name": "System 1", "room": "B-110"})
for name, zfin in (("casper", "mitfa(w2/w2); mpv17(a9/a9)"),
                   ("elavl3:GCaMP6s", "Tg(elavl3:GCaMP6s)"),
                   ("kdrl:EGFP", "Tg(kdrl:EGFP)")):
    jordan.post("/zebrafish/lines/create", data={"name": name, "zfin_name": zfin, "owner": "ex-jordan"})
for code in ("T-01", "T-02", "T-03", "T-04"):
    jordan.post("/zebrafish/tanks/create", data={"tank_id": code})
for code, n in (("T-01", 12), ("T-02", 8), ("T-03", 10)):
    tank = one("select id from tanks where tank_id=?", code)
    jordan.post("/zebrafish/fish/create", data={"tank_id_fk": str(tank), "count": str(n)})
jordan.post("/zebrafish/clutches/create", data={"clutch_id": "CL-0412", "date_fertilized": ago(4)})

# ------------------------------------------------------------------ flies

fly = one("select key from stock_modules where kind='fly' and private_to='' order by id limit 1")
if not fly:
    r = alex.post("/stocks/new", data={"kind": "fly", "label": "Drosophila", "audience": "lab"})
    fly = r.headers.get("Location", "").split("?")[0].rsplit("/", 1)[-1]
fly_id = one("select id from stock_modules where key=?", fly)
for inc, temp in (("Incubator 25 °C", "25"), ("Incubator 18 °C", "18")):
    priya.post(f"/stocks/{fly}/incubators/save", data={"name": inc, "temperature": temp})
inc25 = one("select id from stock_incubators where module_id_fk=? and temperature like '25%'", fly_id)
inc18 = one("select id from stock_incubators where module_id_fk=? and temperature like '18%'", fly_id)
priya.post(f"/stocks/{fly}/racks/save", data={"name": "Stocks 1", "rows": 4, "cols": 6, "incubator_id": inc18,
                                             "last_flipped_on": ago(20), **GRID})
priya.post(f"/stocks/{fly}/racks/save", data={"name": "Crosses", "rows": 4, "cols": 6, "incubator_id": inc25,
                                             "last_flipped_on": ago(9), **GRID})
stocks_rack = one("select id from stock_racks where module_id_fk=? and name='Stocks 1'", fly_id)
cross_rack = one("select id from stock_racks where module_id_fk=? and name='Crosses'", fly_id)
VIALS = [
    ("w[1118]", "stock", stocks_rack, "A1"),
    ("y[1] w[*]; P{UAS-mCD8::GFP}LL5", "stock", stocks_rack, "A2"),
    ("w[*]; P{GAL4-elav.L}3", "stock", stocks_rack, "A3"),
    ("w[*]; P{nSyb-GAL4}", "stock", stocks_rack, "A4"),
    ("w[*]; UAS-GCaMP7f", "stock", stocks_rack, "A5"),
    ("Canton-S", "stock", stocks_rack, "A6"),
    ("w; Sp/CyO; TM2/TM6B", "stock", stocks_rack, "B1"),
    ("elav>GCaMP7f", "cross", cross_rack, "A1"),
    ("nSyb>mCD8::GFP", "cross", cross_rack, "A2"),
    ("elav>GCaMP7f", "experiment", cross_rack, "B1"),
]
for geno, purpose, rack, pos in VIALS:
    priya.post(f"/stocks/{fly}/units/save", data={"id": "", "genotype": geno, "purpose": purpose, "count": 1,
                                                  "rack_id": rack, "position": pos})

# ------------------------------------------------------------------ plasmids

alex.post("/plasmids/boxes/save", data={"id": "", "name": "Plasmid box 1", "rows": "9", "cols": "9",
                                        "storage_temp": "−20 °C", **GRID})
box = one("select id from plasmid_boxes where name='Plasmid box 1'")
PLASMIDS = ["pAAV-EF1a-DIO-GCaMP6s", "pCAG-EGFP", "pAAV-hSyn-ChR2-mCherry", "pLenti-CMV-Puro",
            "pAAV-CAG-FLEX-tdTomato", "pcDNA3.1-mScarlet", "pX330-sgRNA", "pAAV-hSyn-DIO-hM4D(Gi)"]
for i, name in enumerate(PLASMIDS):
    pos = f"A{i + 1}"
    sam.post("/plasmids", data={"name": name, "count": "1", "box_id": str(box), "position": pos})


def genbank() -> str:
    """A made-up 5.5 kb plasmid with plausible features, for the map."""
    import random
    rnd = random.Random(7)
    seq = "".join(rnd.choice("ACGT") for _ in range(5500)).lower()
    features = [("promoter", 1, 584, "CAG promoter", 1), ("CDS", 700, 1419, "EGFP", 1),
                ("polyA_signal", 1450, 1674, "bGH poly(A)", 1), ("rep_origin", 2100, 2688, "ori", -1),
                ("CDS", 2860, 3720, "AmpR", -1), ("promoter", 3721, 3825, "AmpR promoter", -1),
                ("misc_feature", 4200, 4340, "f1 ori", 1), ("CDS", 4500, 5100, "WPRE", 1)]
    lines = [f"LOCUS       pCAG-EGFP               5500 bp    DNA     circular SYN {TODAY:%d-%b-%Y}".upper(),
             "DEFINITION  pCAG-EGFP (demo).", "FEATURES             Location/Qualifiers"]
    for kind, start, end, label, strand in features:
        loc = f"{start}..{end}" if strand == 1 else f"complement({start}..{end})"
        lines += [f"     {kind:<16}{loc}", f'                     /label="{label}"']
    lines.append("ORIGIN")
    for i in range(0, len(seq), 60):
        chunk = " ".join(seq[j:j + 10] for j in range(i, min(i + 60, len(seq)), 10))
        lines.append(f"{i + 1:>9} {chunk}")
    lines.append("//")
    return "\n".join(lines)


pcag = one("select id from plasmids where name='pCAG-EGFP'")
sam.post(f"/plasmids/{pcag}/upload-sequence", data={"sequence_text": genbank()})

# ------------------------------------------------------------------ inventories


def module(preset: str, label: str) -> str:
    r = alex.post("/inventory/new", data={"preset": preset, "label": label, "audience": "lab"})
    return r.headers.get("Location", "").split("?")[0].rstrip("/").rsplit("/", 1)[-1]


def keys_of(kind: str) -> str | None:
    """The lab's inventory of this kind, never someone's personal one."""
    return one("select key from inventory_modules where kind=? and private_to='' order by id limit 1", kind)


orders = keys_of("orders") or module("orders", "Orders")
for name, status, vendor, cat, qty, price in (
    ("Isoflurane 250 mL", "requested", "Covetrus", "11695-6776-2", "2", "96"),
    ("AAV9-hSyn-GCaMP8m", "ordered", "Addgene", "162375-AAV9", "1", "450"),
    ("Tamoxifen 1 g", "ordered", "Sigma-Aldrich", "T5648-1G", "1", "112"),
    ("PFA 16% ampoules", "received", "EMS", "15710", "4", "210"),
    ("Superfrost Plus slides", "received", "Fisher", "12-550-15", "2", "138"),
    ("DAPI Fluoromount-G", "requested", "SouthernBiotech", "0100-20", "3", "75"),
):
    sam.post(f"/inventory/{orders}/items/save", data={"id": "", "name": name, "status": status, "vendor": vendor,
                                                      "catalog_number": cat, "quantity": qty,
                                                      "attr_price": price, "attr_account": "R01-NS-DEMO"})

reagents = keys_of("reagents") or module("reagents", "Reagents")
for name, status, vendor, cat, qty, unit, expires, cas, conc, temp, hazard in (
    ("Tamoxifen", "in stock", "Sigma-Aldrich", "T5648", "1", "g", ahead(200), "10540-29-1", "powder", "4 °C", "toxic"),
    ("Paraformaldehyde", "low", "EMS", "15710", "2", "ampoules", ahead(20), "30525-89-4", "16%", "RT", "toxic"),
    ("Normal donkey serum", "in stock", "Jackson", "017-000-121", "10", "mL", ahead(9), "", "100%", "−20 °C", "none"),
    ("Triton X-100", "in stock", "Sigma-Aldrich", "T8787", "100", "mL", ahead(400), "9036-19-5", "100%", "RT", "irritant"),
    ("Sucrose", "in stock", "Fisher", "S5-500", "500", "g", ahead(600), "57-50-1", "powder", "RT", "none"),
    ("Ethanol, 200 proof", "in stock", "Decon Labs", "2716", "4", "L", ahead(700), "64-17-5", "100%", "RT", "flammable"),
    ("Ketamine", "low", "Covetrus", "11695-0703-1", "1", "vial", ahead(-5), "6740-88-1", "100 mg/mL", "RT", "none"),
):
    alex.post(f"/inventory/{reagents}/items/save", data={"id": "", "name": name, "status": status, "vendor": vendor,
                                                         "catalog_number": cat, "quantity": qty, "unit": unit,
                                                         "expires_on": expires, "is_shared": "1", "attr_cas": cas,
                                                         "attr_concentration": conc, "attr_storage_temp": temp,
                                                         "attr_hazard": hazard})

antibodies = keys_of("antibodies") or module("antibodies", "Antibodies")
for name, host, dil, vendor, cat in (
    ("anti-TH", "Rabbit", "1:1000", "Millipore", "AB152"),
    ("anti-GFP", "Chicken", "1:2000", "Aves", "GFP-1020"),
    ("anti-RFP", "Rabbit", "1:1000", "Rockland", "600-401-379"),
    ("anti-parvalbumin", "Mouse", "1:500", "Swant", "PV235"),
    ("anti-c-Fos", "Guinea pig", "1:1000", "Synaptic Systems", "226 308"),
):
    jordan.post(f"/inventory/{antibodies}/items/save", data={"id": "", "name": name, "status": "in stock",
                                                             "vendor": vendor, "catalog_number": cat,
                                                             "attr_host": host, "attr_dilution": dil,
                                                             "is_shared": "1"})

samples = keys_of("samples")
if samples:
    for name, ref, kind in (("Brain, perfused", "", "tissue"), ("Tail clip", "", "tissue")):
        sam.post(f"/inventory/{samples}/items/save", data={"id": "", "name": name, "attr_collected_on": ago(3),
                                                           "attr_storage_temp": "4 °C"})

# ------------------------------------------------------------------ calendar

for c, title, start, kind, etype in (
    (sam, "Photometry session 6", ahead(1) + "T10:00", "event", "experiment"),
    (sam, "Photometry session 7", ahead(3) + "T10:00", "event", "experiment"),
    (jordan, "Slice ephys: PV-Cre;Ai14", ahead(2) + "T13:00", "event", "experiment"),
    (priya, "Flip stock rack", ahead(4), "task", ""),
    (alex, "Lab meeting", ahead(5) + "T15:00", "event", "meeting"),
    (sam, "Perfuse cohort 2", ahead(8) + "T09:00", "event", "experiment"),
    (alex, "IACUC protocol renewal", ahead(10), "task", ""),
    (jordan, "Order AAVs", ahead(0), "task", ""),
):
    payload = {"title": title, "start": start, "kind": kind, "event_type": etype, "priority": "normal",
               "status": "open"}
    c.post("/calendar/items", data=json.dumps(payload), content_type="application/json")

# ------------------------------------------------------------------ notebook

sam.post("/notebook/tabs/create", data={"title": "Photometry"})
tab = one("select id from notebook_tabs order by id desc limit 1")
if tab:
    sam.post("/notebook/pages/create", data={"tab_id": tab, "title": "Session 5: sucrose vs water"})

# ------------------------------------------------------------------ report

counts = {t: one(f"select count(*) from {t}") for t in
          ("mice", "mouse_cages", "litters", "strains", "tanks", "fish_lines", "stock_units",
           "plasmids", "inventory_items", "calendar_events", "tasks", "notebook_pages")}
print(json.dumps(counts, indent=1))
print("Example data added. The example members cannot sign in; disable them in Admin when done.")
