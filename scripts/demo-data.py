#!/usr/bin/env python3
"""Fill an empty data folder with a made-up lab, for screenshots and demos.

    python scripts/demo-data.py /tmp/biomanager-demo
    BIOMANAGER_DATA_DIR=/tmp/biomanager-demo PORT=5077 python run.py

Everything goes in through the app's own routes, as the test suite does, so
the data is exactly what a lab would have made by hand. The folder must be
empty or missing: this never opens a real database. People, lines and
records are invented.

It prints the admin's username. The password is written to
<folder>/demo-password, readable by you only.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import date, timedelta
from pathlib import Path

if len(sys.argv) != 2:
    sys.exit(__doc__)
DATA = Path(sys.argv[1]).expanduser().resolve()
if DATA.exists() and any(DATA.iterdir()):
    sys.exit(f"{DATA} is not empty; give an empty or new folder.")
DATA.mkdir(parents=True, exist_ok=True)
os.environ["BIOMANAGER_DATA_DIR"] = str(DATA)
os.environ["DATABASE_URL"] = f"sqlite:///{DATA / 'biomanager.db'}"
os.environ.pop("BIOMANAGER_ENV", None)
os.environ["BIOMANAGER_SEED_DEFAULTS"] = "1"  # every default database, as a set-up lab has
os.environ["BIOMANAGER_TELEMETRY"] = "0"      # a made-up lab is not a lab using BioManager

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from werkzeug.security import generate_password_hash  # noqa: E402

from app import security  # noqa: E402
from app.app import app  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models import UserAccount  # noqa: E402

TODAY = date.today()


def ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


def ahead(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def one(sql: str, *args):
    with engine.connect() as con:
        found = con.exec_driver_sql(sql, tuple(args)).fetchone()
    return found[0] if found else None


# ------------------------------------------------------------------ people

PEOPLE = [
    # username, display name, role
    ("alex", "Alex Rivera", "admin"),
    ("sam", "Sam Okafor", "member"),
    ("jordan", "Jordan Lee", "member"),
    ("priya", "Priya Nair", "member"),
]
password = secrets.token_urlsafe(12)
with SessionLocal() as s:
    from app import whats_new
    for username, name, role in PEOPLE:
        person = UserAccount(username=username, display_name=name, role=role,
                             password_hash=generate_password_hash(password),
                             welcomed_at=__import__("datetime").datetime.utcnow())
        # Past the welcome tour and this version's What's new, so neither
        # covers the screenshots and clips.
        whats_new.stamp(person)
        s.add(person)
    # A lab that has already answered the setup survey (app/lab.py), so the
    # screenshots show the app in use rather than its first-run pages.
    from app import lab
    lab.mark_setup_done(s)
    from app.inventory_service import set_setting
    set_setting(s, "lab_name", "Rivera Lab")
    s.commit()
pw_file = DATA / "demo-password"
pw_file.write_text(password + "\n")
pw_file.chmod(0o600)
(DATA / "setup-code").unlink(missing_ok=True)


def client(username: str):
    c = app.test_client()
    with SessionLocal() as s, app.app_context():
        user = s.scalar(__import__("sqlalchemy").select(UserAccount).where(UserAccount.username == username))
        stamp = security.session_stamp(user)
        uid = user.id
    with c.session_transaction() as sess:
        sess["user_id"] = uid
        sess["auth"] = stamp
    return c


alex, sam, jordan, priya = (client(p[0]) for p in PEOPLE)
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
    ("101", sam, "sam", rack_a, "A1", "Breeding", 12),
    ("102", sam, "sam", rack_a, "A2", "Breeding", 17),
    ("103", sam, "sam", rack_a, "A3", "Experiment", None),
    ("104", sam, "sam", rack_a, "A4", "Experiment", None),
    ("105", jordan, "jordan", rack_a, "B1", "Breeding", 5),
    ("106", jordan, "jordan", rack_a, "B2", "Breeder", None),
    ("107", jordan, "jordan", rack_a, "B3", "Experiment", None),
    ("108", priya, "priya", rack_a, "C1", "Breeder", None),
    ("109", priya, "priya", rack_a, "C2", "Experiment", None),
    ("110", priya, "priya", rack_a, "C3", "Breeding", 19),
    ("111", alex, "alex", rack_b, "A1", "Breeder", None),
    ("112", alex, "alex", rack_b, "A2", "Breeder", None),
    ("113", sam, "sam", rack_b, "B1", "Experiment", None),
    ("114", jordan, "jordan", rack_b, "B2", "Experiment", None),
]
for code, c, owner, rack, pos, purpose, born in CAGES:
    c.post("/colony/cages/create", data={"cage_id": code, "owner": owner, "rack_id": str(rack),
                                          "position": pos, "purpose": purpose, "room": "B-204",
                                          "card_id": f"F-{20316 + int(code)}"})   # the facility's card
    if born is not None:
        cage_row = one("select id from mouse_cages where cage_id=?", code)
        c.post(f"/colony/cages/{cage_row}/update", data={"date_give_birth": ago(born)},
               headers={"X-Autosave": "1"})

# cage, sex, transgenes, dob (days ago), litter, status, note
MICE = [
    ("101", "M", ["DAT-IRES-Cre/+"], 210, "L-1", "breeding", "Proven breeder"),
    ("101", "F", ["Ai14/Ai14"], 190, "L-2", "breeding", ""),
    ("101", "F", ["Ai14/Ai14"], 190, "L-2", "breeding", ""),
    ("102", "M", ["Vglut2-Cre/+"], 240, "L-3", "breeding", "Retire after this litter"),
    ("102", "F", ["C57BL/6J"], 230, "L-4", "breeding", ""),
    ("103", "M", ["DAT-IRES-Cre/+", "Ai14/+"], 70, "L-10", "experiment", "Fiber implanted"),
    ("103", "M", ["DAT-IRES-Cre/+", "Ai14/+"], 70, "L-10", "experiment", "Fiber implanted"),
    ("103", "M", ["DAT-IRES-Cre/+"], 70, "L-10", "experiment", ""),
    ("103", "M", ["+/+", "Ai14/+"], 70, "L-10", "experiment", "Control"),
    ("104", "F", ["DAT-IRES-Cre/+", "Ai14/+"], 72, "L-11", "experiment", ""),
    ("104", "F", ["DAT-IRES-Cre/+", "Ai14/+"], 72, "L-11", "experiment", ""),
    ("104", "F", ["+/+", "Ai14/+"], 72, "L-11", "experiment", "Control"),
    ("105", "M", ["PV-Cre/PV-Cre"], 150, "L-5", "breeding", ""),
    ("105", "F", ["Ai14/Ai14"], 140, "L-6", "breeding", ""),
    ("106", "F", ["PV-Cre/+"], 95, "L-12", "stock", ""),
    ("106", "F", ["PV-Cre/+"], 95, "L-12", "stock", ""),
    ("106", "F", ["PV-Cre/+"], 95, "L-12", "stock", ""),
    ("107", "M", ["PV-Cre/+", "Ai14/+"], 84, "L-13", "experiment", "Slice ephys"),
    ("107", "M", ["PV-Cre/+", "Ai14/+"], 84, "L-13", "experiment", "Slice ephys"),
    ("108", "M", ["Vglut2-Cre/+"], 120, "L-14", "stock", ""),
    ("108", "M", ["Vglut2-Cre/+"], 120, "L-14", "stock", ""),
    ("109", "F", ["Vglut2-Cre/+"], 60, "L-15", "experiment", "AAV injected"),
    ("109", "F", ["Vglut2-Cre/+"], 60, "L-15", "experiment", "AAV injected"),
    ("109", "F", ["Vglut2-Cre/+"], 60, "L-15", "experiment", "AAV injected"),
    ("110", "M", ["C57BL/6J"], 230, "L-7", "breeding", ""),
    ("110", "F", ["Vglut2-Cre/+"], 180, "L-8", "breeding", ""),
    ("111", "M", ["C57BL/6J"], 56, "L-16", "stock", ""),
    ("111", "M", ["C57BL/6J"], 56, "L-16", "stock", ""),
    ("111", "M", ["C57BL/6J"], 56, "L-16", "stock", ""),
    ("112", "F", ["C57BL/6J"], 56, "L-17", "stock", ""),
    ("112", "F", ["C57BL/6J"], 56, "L-17", "stock", ""),
    ("113", "M", ["DAT-IRES-Cre/+"], 110, "L-18", "experiment", "Behaviour cohort 2"),
    ("113", "M", ["DAT-IRES-Cre/+"], 110, "L-18", "experiment", "Behaviour cohort 2"),
    ("114", "F", ["PV-Cre/+"], 100, "L-19", "experiment", ""),
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
for c, code, born, pups, cohort in ((sam, "L-20", 17, "7", "DAT-Cre x Ai14"),
                                    (priya, "L-21", 19, "6", "Vglut2-Cre x B6"),
                                    (sam, "L-22", 12, "8", "DAT-Cre x Ai14"),
                                    (jordan, "L-23", 5, "5", "PV-Cre x Ai14")):
    c.post("/colony/litters/create", data={"litter_id": code, "date_of_birth": ago(born), "total_pups": pups,
                                            "cohort_name": cohort})
for sex in ("M", "M", "F"):
    sam.post("/colony/mice/create", data={"cage_id": "113", "litter_id": "L-9", "date_of_birth": ago(30),
                                           "gender": sex, "status": "geno", "owner": "sam",
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
    jordan.post("/zebrafish/lines/create", data={"name": name, "zfin_name": zfin, "owner": "jordan"})
for code in ("T-01", "T-02", "T-03", "T-04"):
    jordan.post("/zebrafish/tanks/create", data={"tank_id": code})
for code, n in (("T-01", 12), ("T-02", 8), ("T-03", 10)):
    tank = one("select id from tanks where tank_id=?", code)
    jordan.post("/zebrafish/fish/create", data={"tank_id_fk": str(tank), "count": str(n)})
jordan.post("/zebrafish/clutches/create", data={"clutch_id": "CL-0412", "date_fertilized": ago(4)})

# ------------------------------------------------------------------ flies

fly = one("select key from stock_modules where kind='fly' order by id limit 1")
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
    return one("select key from inventory_modules where kind=? order by id limit 1", kind)


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

# Viral vectors, each made from one of the plasmids above, in a −80 °C box.
viruses = keys_of("viruses") or module("viruses", "Viruses")
alex.post(f"/inventory/{viruses}/racks/save", data={"id": "", "name": "Virus box −80 #1", "rows": "9", "cols": "9",
                                                    "kind": "box", **GRID})
virus_box = one("select id from inventory_racks where name='Virus box −80 #1'")
for i, (name, vector, serotype, plasmid, promoter, payload, titer, status) in enumerate((
    ("AAV9-EF1a-DIO-GCaMP6s", "AAV", "AAV9", "pAAV-EF1a-DIO-GCaMP6s", "EF1a", "GCaMP6s (Cre-dependent)", "2.1e13 GC/mL", "in stock"),
    ("AAV5-hSyn-ChR2-mCherry", "AAV", "AAV5", "pAAV-hSyn-ChR2-mCherry", "hSyn", "ChR2-mCherry", "4.0e12 GC/mL", "low"),
    ("AAV-PHP.eB-CAG-FLEX-tdTomato", "AAV", "PHP.eB", "pAAV-CAG-FLEX-tdTomato", "CAG", "tdTomato (Cre-dependent)", "1.2e13 GC/mL", "in stock"),
    ("AAV8-hSyn-DIO-hM4D(Gi)", "AAV", "AAV8", "pAAV-hSyn-DIO-hM4D(Gi)", "hSyn", "hM4D(Gi)-mCherry", "7.5e12 GC/mL", "in stock"),
    ("LV-CMV-Puro", "lentivirus", "VSV-G", "pLenti-CMV-Puro", "CMV", "puromycin resistance", "3.0e8 TU/mL", "in stock"),
)):
    sam.post(f"/inventory/{viruses}/items/save", data={
        "id": "", "name": name, "category": vector, "status": status, "is_shared": "1",
        "quantity": str(6 - i), "unit": "× 10 µL", "rack_id": str(virus_box), "position": f"A{i + 1}",
        "attr_serotype": serotype, "attr_plasmid": plasmid, "attr_promoter": promoter, "attr_payload": payload,
        "attr_titer": titer, "attr_biosafety": "BSL-2" if vector == "lentivirus" else "BSL-1",
        "attr_made_on": ago(40 + 9 * i), "expires_on": ahead(320 - 30 * i)})

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

# A made-up lab never sends the daily anonymous counts (app/telemetry.py),
# however it is run later: the screenshots, the launch clips, a demo.
from app import telemetry  # noqa: E402

with SessionLocal() as s:
    telemetry.set_lab_on(s, False)
    s.commit()

# ------------------------------------------------------------------ report

counts = {t: one(f"select count(*) from {t}") for t in
          ("mice", "mouse_cages", "litters", "strains", "tanks", "fish_lines", "stock_units",
           "plasmids", "inventory_items", "calendar_events", "tasks", "notebook_pages")}
print(json.dumps(counts, indent=1))
print(f"Demo lab ready in {DATA}. Sign in as 'alex'; the password is in {pw_file}.")
