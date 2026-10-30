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

# cage, sex, transgenes, dob (days ago), litter, status, note. Every mouse
# is on a C57BL/6J background (its Strain); the wild-type ones carry no transgene.
MICE = [
    ("101", "M", ["DAT-IRES-Cre/+"], 210, "L-1", "breeding", "Proven breeder"),
    ("101", "F", ["Ai14/Ai14"], 190, "L-2", "breeding", ""),
    ("101", "F", ["Ai14/Ai14"], 190, "L-2", "breeding", ""),
    ("102", "M", ["Vglut2-Cre/+"], 240, "L-3", "breeding", "Retire after this litter"),
    ("102", "F", [], 230, "L-4", "breeding", ""),
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
    ("110", "M", [], 230, "L-7", "breeding", ""),
    ("110", "F", ["Vglut2-Cre/+"], 180, "L-8", "breeding", ""),
    ("111", "M", [], 56, "L-16", "stock", ""),
    ("111", "M", [], 56, "L-16", "stock", ""),
    ("111", "M", [], 56, "L-16", "stock", ""),
    ("112", "F", [], 56, "L-17", "stock", ""),
    ("112", "F", [], 56, "L-17", "stock", ""),
    ("113", "M", ["DAT-IRES-Cre/+"], 110, "L-18", "experiment", "Behaviour cohort 2"),
    ("113", "M", ["DAT-IRES-Cre/+"], 110, "L-18", "experiment", "Behaviour cohort 2"),
    ("114", "F", ["PV-Cre/+"], 100, "L-19", "experiment", ""),
]
owners = {code: (c, owner) for code, c, owner, *_ in CAGES}
for cage, sex, tg, dob, litter, status, note in MICE:
    c, owner = owners[cage]
    data = {"cage_id": cage, "litter_id": litter, "date_of_birth": ago(dob), "gender": sex,
            "strain": "C57BL/6J", "status": status, "owner": owner, "note": note}
    data.update({f"transgene_{i + 1}": v for i, v in enumerate(tg)})
    c.post("/colony/mice/create", data=data)

# Two that have been used, so the sheet shows the alive dot doing its job.
for cage, dob in (("113", 110), ("114", 100)):
    c, owner = owners[cage]
    c.post("/colony/mice/create", data={"cage_id": cage, "date_of_birth": ago(dob), "gender": "M" if cage == "113" else "F",
                                         "strain": "C57BL/6J", "status": "sacrificed", "owner": owner, "date_of_death": ago(3),
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
                                           "gender": sex, "strain": "C57BL/6J", "status": "geno", "owner": "sam",
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

# ------------------------------------------------------------------ plants

# An Arabidopsis database from the Plants preset (app/organisms.py): a growth
# room of trays, seed lots harvested and sown again a generation on, a T2's
# selection plate, and a cross. Its Seeds tab is the plant-seeds shot.
from app.organisms import PRESET_BY_KEY  # noqa: E402

PLANT = PRESET_BY_KEY["plant"]
r = alex.post("/organisms/new", data={"audience": "lab", "preset_key": "plant", "label": "Arabidopsis",
                                      "blurb": PLANT.blurb, "icon": PLANT.icon, "identity_mode": PLANT.identity_mode,
                                      "capabilities": PLANT.capabilities})
plants = r.headers.get("Location", "").split("?")[0].rstrip("/").rsplit("/", 1)[-1]
plants_id = one("select id from organism_modules where key=?", plants)
P = f"/organisms/{plants}"
alex.post(f"{P}/location/save", data={"name": "Growth room 2", "kind": "growth room", "rows": "3", "cols": "4", **GRID})
room = one("select id from organism_locations where module_id_fk=? and name='Growth room 2'", plants_id)
for code, purpose, pos in (("T-01", "selection", "A1"), ("T-02", "selection", "A2"), ("T-03", "cross", "B1"),
                           ("T-04", "stock", "B2")):
    priya.post(f"{P}/housing/save", data={"code": code, "purpose": purpose, "owner": "priya", "_full": "1",
                                          "location_id_fk": str(room), "position": pos,
                                          "attr_light": "long day (16 h)", "attr_medium": "soil"})
tray = {code: one("select id from organism_housing where module_id_fk=? and code=?", plants_id, code)
        for code in ("T-01", "T-02", "T-03", "T-04")}
for code, name, background, source, number, marker, transgenic in (
    ("Col-0", "wild type", "Col-0", "ABRC", "CS70000", "none", ""),
    ("35S:GFP-PIN1", "PIN1-GFP", "Col-0", "own", "", "kanamycin", "1"),
    ("pif4-2", "pif4 mutant", "Col-0", "NASC", "N66043", "Basta", ""),
):
    priya.post(f"{P}/line/save", data={"code": code, "name": name, "owner": "priya", "_full": "1",
                                       "attr_background": background, "attr_source": source,
                                       "attr_stock_number": number, "attr_selection_marker": marker,
                                       **({"attr_transgenic": "1", "attr_biosafety_permit": "IBC-2026-014"}
                                          if transgenic else {})})
line = {code: one("select id from organism_lines where module_id_fk=? and code=?", plants_id, code)
        for code in ("Col-0", "35S:GFP-PIN1", "pif4-2")}


def lot(code: str, **fields) -> int:
    priya.post(f"{P}/seedlot/save", data={"code": code, "owner": "priya", "storage_text": "Seed cabinet, box 1",
                                          **fields})
    return one("select id from organism_seed_lots where module_id_fk=? and code=?", plants_id, code)


def sowing(code: str, **fields) -> int:
    priya.post(f"{P}/cohort/save", data={"code": code, "owner": "priya", **fields})
    return one("select id from organism_cohorts where module_id_fk=? and code=?", plants_id, code)


lot("S-001", line_id_fk=line["Col-0"], amount="500", amount_left="320", unit="mg", harvested_on=ago(400),
    position="A1", germination_pct="96", germination_tested_on=ago(30))
t1 = lot("S-002", line_id_fk=line["35S:GFP-PIN1"], generation="T1", amount="40", unit="mg", harvested_on=ago(150),
         position="A2", germination_pct="91", germination_tested_on=ago(120))
first = sowing("SOW-01", seed_lot_id_fk=str(t1), birth_on=ago(82), seed_taken="10", count_initial="48",
               stage="seed set", location_id_fk=str(room))
for n in range(1, 5):
    priya.post(f"{P}/animal/save", data={"code": f"P-0{n}", "count": "1", "status": "flowering", "owner": "priya",
                                         "cohort_id_fk": str(first), "line_id_fk": str(line["35S:GFP-PIN1"]),
                                         "housing_id_fk": str(tray["T-01"]), "birth_on": ago(82),
                                         "attr_flowered_on": ago(48 - n)})
p01 = one("select id from organisms where module_id_fk=? and code='P-01'", plants_id)
t2 = lot("S-003", source_kind="organism", source_id=str(p01), amount="60", unit="mg", harvested_on=ago(12),
         end_source="1", position="A3", selection="kanamycin", resistant="152", sensitive="49",
         germination_pct="94", germination_tested_on=ago(10))
second = sowing("SOW-02", seed_lot_id_fk=str(t2), birth_on=ago(9), seed_taken="5", count_initial="36",
                stage="seedling", location_id_fk=str(room))
priya.post(f"{P}/animal/save", data={"code": "", "count": "34", "status": "growing", "owner": "priya",
                                     "cohort_id_fk": str(second), "line_id_fk": str(line["35S:GFP-PIN1"]),
                                     "housing_id_fk": str(tray["T-02"]), "birth_on": ago(9)})
priya.post(f"{P}/cross/save", data={"code": "X-01", "cross_type": "pair", "owner": "priya", "_full": "1",
                                    "dam_line_id_fk": str(line["pif4-2"]), "sire_line_id_fk": str(line["35S:GFP-PIN1"]),
                                    "housing_id_fk": str(tray["T-03"]), "set_up_on": ago(30)})
cross = one("select id from organism_crosses where module_id_fk=? and code='X-01'", plants_id)
lot("S-004", source_kind="cross", source_id=str(cross), amount="120", unit="seeds", harvested_on=ago(6),
    end_source="1", position="A4")
alex.post(f"{P}/reading/save", data={"location_id_fk": str(room), "metric_temperature_c": "22",
                                     "metric_humidity": "60", "metric_light": "120", "metric_day_length": "16"})
# A lab in good order has ticked off what fell due before today, on its day.
with SessionLocal() as s:
    past = s.execute(__import__("sqlalchemy").text(
        "SELECT id, due_on FROM organism_due WHERE module_id_fk = :m AND done_on IS NULL AND due_on < :t"),
        {"m": plants_id, "t": TODAY}).all()
for due_id, due_on in past:
    priya.post(f"{P}/due/{due_id}/done", data={"done_on": str(due_on)[:10]})

# ------------------------------------------------------------------ plasmids

alex.post("/plasmids/boxes/save", data={"id": "", "name": "Plasmid box 1", "rows": "9", "cols": "9",
                                        "storage_temp": "−20 °C", **GRID})
box = one("select id from plasmid_boxes where name='Plasmid box 1'")
PLASMIDS = ["pAAV-EF1a-DIO-GCaMP6s", "pCAG-EGFP", "pAAV-hSyn-ChR2-mCherry", "pLenti-CMV-Puro",
            "pAAV-CAG-FLEX-tdTomato", "pcDNA3.1-mScarlet", "pX330-sgRNA", "pAAV-hSyn-DIO-hM4D(Gi)"]
for i, name in enumerate(PLASMIDS):
    pos = f"A{i + 1}"
    sam.post("/plasmids", data={"name": name, "count": "1", "box_id": str(box), "position": pos})


PLASMIDS.append("pCAGGS")
sam.post("/plasmids", data={"name": "pCAGGS", "count": "1", "box_id": str(box), "position": f"A{len(PLASMIDS)}"})

# The real EGFP coding sequence, so a digest, a primer and a translation
# of the demo's pCAG-EGFP read true; the rest of the plasmid is made up.
EGFP = ("ATGGTGAGCAAGGGCGAGGAGCTGTTCACCGGGGTGGTGCCCATCCTGGTCGAGCTGGACGGCGACGTAAACGGCCACAAGTTCAGCGTGTCCGGCGAGGGCGAGGGCGATGCCACC"
        "TACGGCAAGCTGACCCTGAAGTTCATCTGCACCACCGGCAAGCTGCCCGTGCCCTGGCCCACCCTCGTGACCACCCTGACCTACGGCGTGCAGTGCTTCAGCCGCTACCCCGACCAC"
        "ATGAAGCAGCACGACTTCTTCAAGTCCGCCATGCCCGAAGGCTACGTCCAGGAGCGCACCATCTTCTTCAAGGACGACGGCAACTACAAGACCCGCGCCGAGGTGAAGTTCGAGGGC"
        "GACACCCTGGTGAACCGCATCGAGCTGAAGGGCATCGACTTCAAGGAGGACGGCAACATCCTGGGGCACAAGCTGGAGTACAACTACAACAGCCACAACGTCTATATCATGGCCGAC"
        "AAGCAGAAGAACGGCATCAAGGTGAACTTCAAGATCCGCCACAACATCGAGGACGGCAGCGTGCAGCTCGCCGACCACTACCAGCAGAACACCCCCATCGGCGACGGCCCCGTGCTG"
        "CTGCCCGACAACCACTACCTGAGCACCCAGTCCGCCCTGAGCAAAGACCCCAACGAGAAGCGCGATCACATGGTCCTGCTGGAGTTCGTGACCGCCGCCGGGATCACTCTCGGCATG"
        "GACGAGCTGTACAAGTAA")
CUT_HERE = ("GAATTC", "GCGGCCGC", "TCTAGA", "GGTACC", "GGATCC", "CCATGG")


def demo_plasmid(insert: bool = True) -> tuple[str, list]:
    """pCAG-EGFP (5.5 kb): EGFP between EcoRI and NotI, an XbaI site Dam
    blocks just after; without the insert, its backbone pCAGGS."""
    import random
    rnd = random.Random(7)

    def filler(n: int) -> str:
        text = "".join(rnd.choice("ACGT") for _ in range(n))
        for site in CUT_HERE:              # these cut only where the map says
            text = text.replace(site, site[:2] + ("A" if site[2] != "A" else "C") + site[3:])
        return text
    head, gap = filler(680), filler(10)
    tail = filler(5500 - 680 - 12 - len(EGFP) - 10 - 16)
    seq = head + "GAATTC" + "GCCACC" + (EGFP if insert else "") + gap + "GCGGCCGC" + "TCTAGATC" + tail
    egfp_at = 692
    shift = 0 if insert else -len(EGFP)
    features = [("promoter", 1, 584, "CAG promoter", 1)]
    if insert:
        features.append(("CDS", egfp_at + 1, egfp_at + len(EGFP), "EGFP", 1))
    features += [(kind, a + shift, b + shift, label, strand) for kind, a, b, label, strand in (
        ("polyA_signal", 1460, 1684, "bGH poly(A)", 1), ("rep_origin", 2100, 2688, "ori", -1),
        ("CDS", 2860, 3720, "AmpR", -1), ("promoter", 3721, 3825, "AmpR promoter", -1),
        ("misc_feature", 4200, 4340, "f1 ori", 1), ("CDS", 4500, 5100, "WPRE", 1))]
    return seq.lower(), features


def genbank(name: str = "pCAG-EGFP", insert: bool = True) -> str:
    seq, features = demo_plasmid(insert)
    lines = [f"LOCUS       {name:<24}{len(seq)} bp    DNA     circular SYN {TODAY:%d-%b-%Y}".upper(),
             f"DEFINITION  {name} (demo).", "FEATURES             Location/Qualifiers"]
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
pcaggs = one("select id from plasmids where name='pCAGGS'")
sam.post(f"/plasmids/{pcaggs}/upload-sequence", data={"sequence_text": genbank("pCAGGS", insert=False)})
sam.post(f"/plasmids/{pcag}/parents", data={
    "parent": str(one("select plasmid_id from plasmids where name='pCAGGS'")), "role": "backbone",
    "method": "digest_ligate"})



def ab1(bases: str, quality: list[int], seed: int) -> bytes:
    """A made-up ABI chromatogram: a peak for each called base in its dye's
    channel, a little noise under them, and the quality the caller gave."""
    import math
    import random
    import struct
    rnd = random.Random(seed)
    spacing = 12
    size = spacing * len(bases) + spacing
    order = "GATC"
    channels = {b: [rnd.randint(4, 30) for _ in range(size)] for b in "ACGT"}
    peaks = []
    for i, base in enumerate(bases):
        centre = spacing * i + spacing // 2 + rnd.randint(-1, 1)
        peaks.append(centre)
        height = rnd.randint(700, 1300) * (0.3 if quality[i] < 15 else 1)
        for dx in range(-6, 7):
            channels[base][centre + dx] += int(height * math.exp(-(dx * dx) / 6.5))
    entries = [("SMPL", 1, 18, 1, 6, b"\x05clone"), ("PBAS", 2, 2, 1, len(bases), bases.encode()),
               ("PCON", 2, 2, 1, len(quality), bytes(quality)), ("FWO_", 1, 2, 1, 4, order.encode()),
               *(("DATA", n, 4, 2, size, struct.pack(f">{size}h", *[min(v, 32000) for v in channels[b]]))
                 for n, b in zip(range(9, 13), order)),
               ("PLOC", 2, 4, 2, len(bases), struct.pack(f">{len(bases)}h", *peaks))]
    blob, directory, offset = b"", b"", 128
    for tag, num, etype, esize, count, data in entries:
        if len(data) <= 4:
            directory += struct.pack(">4sihhii", tag.encode(), num, etype, esize, count, len(data)) + \
                data.ljust(4, b"\0") + struct.pack(">i", 0)
        else:
            directory += struct.pack(">4sihhiiii", tag.encode(), num, etype, esize, count, len(data), offset, 0)
            blob += data
            offset += len(data)
    header = (b"ABIF" + struct.pack(">h", 101) + struct.pack(">4sihhiiii", b"tdir", 1, 1023, 28, len(entries),
                                                            28 * len(entries), offset, 0)).ljust(128, b"\0")
    return header + blob + directory


def sanger(seq: str, start: int, end: int, reverse: bool, seed: int, changes=None) -> bytes:
    """A read of seq[start:end] (0-based), forward or reverse, with poor ends."""
    import random
    rnd = random.Random(seed)
    bases = list(seq[start:end].upper())
    for at, base in (changes or {}).items():
        bases[at - start] = base
    text = "".join(bases)
    if reverse:
        text = text.translate(str.maketrans("ACGT", "TGCA"))[::-1]
    n = len(text)
    quality = [rnd.randint(4, 14) if i < 25 or i > n - 60 else rnd.randint(36, 60) for i in range(n)]
    return ab1(text, quality, seed)


# Clones of pCAG-EGFP checked by sequencing: clone 3 matches and is the
# plasmid; clone 2 failed; clone 1 has a silent change in EGFP (codon 61,
# CTC → CTT). Each read is named by its clone, as the Verify tab reads them.
from io import BytesIO  # noqa: E402

demo_seq = demo_plasmid()[0].upper()
silent = 692 + 60 * 3 + 2                      # EGFP's codon 61, third base (0-based)
assert demo_seq[silent - 2:silent + 1] == "CTC"
for clone, reads in (
    ("3", [("C3_CAG-F.ab1", 560, 1480, False, 31, None), ("C3_bGH-R.ab1", 600, 1560, True, 32, None)]),
    ("2", [("C2_CAG-F.ab1", 560, 650, False, 21, None)]),
    ("1", [("C1_CAG-F.ab1", 560, 1480, False, 11, {silent: "T"}), ("C1_bGH-R.ab1", 600, 1560, True, 12, {silent: "T"})]),
):
    files = [(BytesIO(sanger(demo_seq, a, b, rev, seed, ch)), name) for name, a, b, rev, seed, ch in reads]
    sam.post(f"/plasmids/{pcag}/clones", data={"reads": files}, content_type="multipart/form-data")
    if clone == "3":
        sam.post(f"/plasmids/{pcag}/clones/{one('select id from plasmid_clones where name=?', '3')}/accept")

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

# The restriction enzymes in the freezer door: the Digest tab marks them.
for name, cat, box_pos in (("EcoRI-HF", "R3101S", "door, row 1"), ("NotI-HF", "R3189S", "door, row 1"),
                           ("HindIII-HF", "R3104S", "door, row 2"), ("PstI-HF", "R3140S", "door, row 2"),
                           ("BamHI-HF", "R3136S", "door, row 2"), ("XbaI", "R0145S", "door, row 3")):
    alex.post(f"/inventory/{reagents}/items/save", data={"id": "", "name": name, "category": "enzyme",
                                                         "status": "in stock", "vendor": "NEB", "catalog_number": cat,
                                                         "quantity": "1", "unit": "tube", "is_shared": "1",
                                                         "location_note": f"−20 °C freezer, {box_pos}",
                                                         "attr_storage_temp": "−20 °C", "attr_hazard": "none"})

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
    (sam, "Perfuse cohort 2", ahead(8) + "T09:00", "event", "experiment"),
    (alex, "IACUC protocol renewal", ahead(10), "task", ""),
    (jordan, "Order AAVs", ahead(0), "task", ""),
):
    payload = {"title": title, "start": start, "kind": kind, "event_type": etype, "priority": "normal",
               "status": "open"}
    c.post("/calendar/items", data=json.dumps(payload), content_type="application/json")

# The weekly lab meeting, kept in the calendar with who presents in turn;
# the next four are on the calendar, each naming its presenter.
meeting_day = date.today() + timedelta(days=5)
series = alex.post("/notebook/api/meetings", json={
    "name": "Lab meeting", "weekday": meeting_day.weekday(), "time": "15:00", "location": "Room 2.14",
    "members": [p[0] for p in PEOPLE]}).get_json()["series"]
alex.post(f"/notebook/api/meetings/{series['id']}/calendar", json={"count": 4})

# alex's to-dos on the calendar's Board: two lists of cards, with no day.
for name, cards in (("This week", ("Order Cre antibody", "Book the confocal for Friday")),
                    ("Waiting on", ("Sequencing results from the core",))):
    alex.post("/calendar/board/lists", json={"add": name})
    for title in cards:
        alex.post("/calendar/board/cards", json={"title": title, "list": name})

# ------------------------------------------------------------------ notebook

# alex's own notebook: three topics, each with an icon, and pages of each
# kind: a data sheet, a protocol's steps, notes and a paper.
SHEET = """```sheet
{"title": "Peak dF/F", "columns": [{"name": "Group", "type": "text"}, {"name": "dF/F", "type": "number", "unit": "%"}],
 "rows": [["Sucrose", "4.1"], ["Sucrose", "3.8"], ["Sucrose", "4.6"], ["Water", "1.2"], ["Water", "0.9"], ["Water", "1.4"]],
 "showPlot": true, "showStats": true, "chart": {"type": "bar", "x": 0, "y": [1], "group": -1, "error": "sem"},
 "stats": {"group": 0, "value": 1, "test": "auto", "control": "Water"}}
```"""
NOTEBOOK = (
    ("Fiber photometry", "emoji:🔬", (
        ("Session 7: sucrose vs water", "note", "Recorded 12 trials per mouse.\n\n" + SHEET),
        ("Fiber implant surgery", "protocol", "## Steps\n\n1. Anesthetize with isoflurane, 2% induction\n"
                                              "2. Level the skull, mark AP −1.2, ML 0.8\n3. Lower the fiber to DV −4.6\n"
                                              "4. Cement and suture"),
    )),
    ("Slice ephys", "emoji:⚡", (
        ("PV-Cre;Ai14 recordings", "note", "Whole-cell from tdTomato+ cells in layer 5.\n\n"
                                          "| Cell | Rin (MΩ) | Firing (Hz) |\n|---|---|---|\n| 1 | 142 | 88 |\n| 2 | 118 | 102 |"),
        ("Cutting solution", "protocol", "- [ ] 93 mM NMDG\n- [ ] 2.5 mM KCl\n- [ ] 1.2 mM NaH2PO4\n- [ ] 30 mM NaHCO3"),
    )),
    ("Reading", "emoji:📚", (
        ("Dopamine and reward prediction", "reading", "Notes on the paper: phasic dopamine encodes the error, not the reward."),
    )),
)
for topic, icon, pages in NOTEBOOK:
    tab_id = alex.post("/notebook/tabs/create", data={"title": topic}).get_json()["id"]
    alex.post(f"/notebook/api/topics/{tab_id}", json={"icon": icon})
    for title, kind, body in pages:
        page_id = alex.post("/notebook/api/pages/new", json={"tab_id": tab_id, "title": title, "kind": kind}).get_json()["page_id"]
        alex.post(f"/notebook/pages/{page_id}/update", data={"body": body})

sam.post("/notebook/tabs/create", data={"title": "Photometry"})
tab = one("select id from notebook_tabs order by id desc limit 1")
if tab:
    sam.post("/notebook/pages/create", data={"tab_id": tab, "title": "Session 5: sucrose vs water"})
    # The topic shared with the lab: it lists under Shared with me.
    sam.post(f"/notebook/api/topics/{tab}/shares", json={"username": "*", "role": "view"})

# A made-up lab never sends the daily anonymous counts (app/telemetry.py),
# however it is run later: the screenshots, the launch clips, a demo.
from app import telemetry  # noqa: E402

with SessionLocal() as s:
    telemetry.set_lab_on(s, False)
    s.commit()

# ------------------------------------------------------------------ report

counts = {t: one(f"select count(*) from {t}") for t in
          ("mice", "mouse_cages", "litters", "strains", "tanks", "fish_lines", "stock_units",
           "plasmids", "inventory_items", "calendar_events", "tasks", "notebook_pages",
           "organism_seed_lots")}
print(json.dumps(counts, indent=1))
print(f"Demo lab ready in {DATA}. Sign in as 'alex'; the password is in {pw_file}.")
