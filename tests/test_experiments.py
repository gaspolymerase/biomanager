"""Experiments on every database's animals (app/experiments.py): fish,
flies, worms and organisms as well as mice, each with a readout that fits
it, manipulations of its own kinds, and the same page."""
from __future__ import annotations

from datetime import date, timedelta

# tests.base first: it points the app at a throwaway database before app is imported.
from tests.base import AppTestCase, count, location, one, uniq
from app import experiments as xp  # noqa: E402

TODAY = date.today()


class Base(AppTestCase):
    def new(self, db_key, readout="", start=None, client=None):
        client = client or self.a
        r = client.post(f"/experiments/in/{db_key}/create", data={
            "name": uniq("Exp "), "readout": readout, "start_date": (start or TODAY - timedelta(days=2)).isoformat()})
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        return int(location(r).rsplit("/", 1)[1])

    def data(self, exp, client=None):
        return (client or self.a).get(f"/experiments/{exp}/data.json").get_json()

    def add(self, exp, how, value, group="", client=None):
        return (client or self.a).post(f"/experiments/{exp}/subjects/add", json={"how": how, "value": value, "group": group})

    def read(self, exp, on, values, client=None):
        return (client or self.a).post(f"/experiments/{exp}/readings", json={"on": on.isoformat(), "values": values})


class Zebrafish(Base):
    def setUp(self):
        super().setUp()
        self.tank = self.make_tank(self.a)
        for n in (12, 8):
            self.a.post("/zebrafish/fish/create", data={"tank_id_fk": str(self.tank), "count": str(n)})

    def test_a_new_experiment_starts_with_a_tank_s_fish(self):
        tab = self.get_ok(self.a, "/zebrafish?view=experiments")
        self.assertIn("Start with a tank’s fish rows", tab)
        self.assertIn(f'<option value="{self.tank}">', tab)
        r = self.a.post("/experiments/in/zebrafish/create", data={"name": uniq("Exp "), "from_group": str(self.tank)})
        exp = int(location(r).rsplit("/", 1)[1])
        subs = self.data(exp)["subjects"]
        self.assertEqual(sorted(s["start"] for s in subs), [8, 12])          # both rows, with their counts

    def test_a_tank_of_fish_counted_as_they_survive(self):
        exp = self.new("zebrafish")
        data = self.data(exp)
        self.assertEqual(data["readout"]["key"], "survival")                 # fish: survival first
        self.assertIn("immersion", [k["key"] for k in data["kinds"]])      # and their own manipulations
        self.assertNotIn("injection", [k["key"] for k in data["kinds"]])
        r = self.add(exp, "group", str(self.tank), group="Tricaine")
        self.assertEqual(r.status_code, 200, r.get_json())
        subs = r.get_json()["subjects"]
        self.assertEqual(sorted(s["start"] for s in subs), [8, 12])
        # Bench mode's scanner finds them by the tank card's QR anchor.
        self.assertEqual({s["card"] for s in subs}, {f"tank-{self.tank}"})
        big = next(s["key"] for s in subs if s["start"] == 12)
        data = self.read(exp, TODAY, {big: "9"}).get_json()
        row = next(r for r in data["table"]["rows"] if r["key"] == big)
        self.assertEqual((row["values"], row["pct"]), ([9.0], [75.0]))
        self.assertEqual(data["table"]["days"], [3])                          # started two days ago
        bad = self.read(exp, TODAY, {big: "13"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("started with 12", bad.get_json()["error"])

    def test_the_page_and_the_list(self):
        exp = self.new("zebrafish")
        html = self.get_ok(self.a, f"/experiments/{exp}")
        self.assertIn("Record manipulation", html)
        self.assertIn("Survival", html)
        # The list is a tab beside Tanks and Fish, as the mice's is, and the
        # experiment's page goes back to it.
        listing = self.get_ok(self.a, "/zebrafish?view=experiments")
        self.assertIn(f"/experiments/{exp}", listing)
        self.assertIn("Survival", listing)
        self.assertIn("/zebrafish?view=experiments", html)
        self.assertNotIn(f"/experiments/{exp}\"", self.get_ok(self.a, "/colony?view=experiments"))
        fish = self.get_ok(self.a, "/zebrafish?view=fish")
        self.assertIn('href="/zebrafish?view=experiments"', fish)
        self.assertNotIn("/experiments/in/zebrafish", fish)
        # The address the list had before (bookmarks, notebook links) leads to the tab.
        old = self.a.get("/experiments/in/zebrafish")
        self.assertEqual((old.status_code, location(old)), (302, "/zebrafish?view=experiments"))


class FlyStocks(Base):
    def test_vials_with_a_drug_in_the_food_and_their_survival(self):
        key = self.make_stock_module(self.a)
        vials = [self.make_vial(self.a, key) for _ in range(2)]
        exp = self.new(f"stocks:{key}", start=TODAY)
        data = self.data(exp)
        self.assertEqual(data["readout"]["key"], "survival")
        self.assertIn("food", [k["key"] for k in data["kinds"]])
        for v in vials:
            self.add(exp, "one", str(v), group="Paraquat" if v == vials[0] else "Control")
        subs = self.data(exp)["subjects"]
        first = next(s["key"] for s in subs if s["group"] == "Paraquat")
        self.assertIsNone(subs[0]["start"])                                  # flies aren't counted in a vial
        self.a.post(f"/experiments/{exp}/subjects/{first}/update", json={"start": "20"})
        # Drug in the food, recorded as it happens: it joins the plan on day 1.
        r = self.a.post(f"/colony/experiments/{exp}/record-now", json={
            "kind": "food", "agent": "Paraquat", "dose": "10 mM", "group": "Paraquat", "done_on": TODAY.isoformat()})
        self.assertEqual(r.status_code, 200, r.get_json())
        done = r.get_json()["schedule"][0]
        self.assertEqual((done["day"], done["title"], done["record"]["count"]), (1, "Paraquat 10 mM", 1))
        pct = self.read(exp, TODAY, {first: "15"}).get_json()["table"]["rows"]
        self.assertEqual(next(r for r in pct if r["key"] == first)["pct"], [75.0])

    def test_the_experiments_tab_beside_the_vials(self):
        key = self.make_stock_module(self.a)
        exp = self.new(f"stocks:{key}")
        tab = self.get_ok(self.a, f"/stocks/{key}?view=experiments")
        self.assertIn(f"/experiments/{exp}", tab)
        self.assertIn("Eclosed adults", tab)                                # a fly's own readouts to start with
        self.assertIn(f'href="/stocks/{key}?view=experiments"', self.get_ok(self.a, f"/stocks/{key}"))
        self.assertIn(f"/stocks/{key}?view=experiments", self.get_ok(self.a, f"/experiments/{exp}"))
        self.assertEqual(location(self.a.get(f"/experiments/in/stocks:{key}")), f"/stocks/{key}?view=experiments")

    def test_worms_have_rnai_and_brood_size(self):
        key = self.make_stock_module(self.a, kind="worm")
        data = self.data(self.new(f"stocks:{key}", readout="brood"))
        self.assertEqual((data["readout"]["label"], data["readout"]["kind"]), ("Brood size", "value"))
        self.assertIn("rnai", [k["key"] for k in data["kinds"]])


class Organisms(Base):
    def test_the_experiments_tab_beside_the_animals(self):
        key = self.make_organism_module(self.a)
        exp = self.new(f"organisms:{key}")
        tab = self.get_ok(self.a, f"/organisms/{key}?view=experiments")
        self.assertIn(f"/experiments/{exp}", tab)
        self.assertIn(f'href="/organisms/{key}?view=experiments"', self.get_ok(self.a, f"/organisms/{key}"))
        self.assertEqual(location(self.a.get(f"/experiments/in/organisms:{key}")),
                         f"/organisms/{key}?view=experiments")

    def test_a_new_experiment_can_follow_a_readout_worked_out_from_cts(self):
        key = self.make_organism_module(self.a)
        page = self.a.get(f"/organisms/{key}?view=experiments").get_data(as_text=True)
        self.assertIn('value="derived:fold_change"', page)
        r = self.a.post(f"/experiments/in/organisms:{key}/create", data={
            "name": uniq("Exp "), "readout": "derived:fold_change", "readout_control": "Vehicle",
            "start_date": TODAY.isoformat()})
        exp = int(location(r).rsplit("/", 1)[1])
        readout = self.data(exp)["readout"]
        self.assertEqual((readout["formula"], readout["control"]), ("fold_change", "Vehicle"))
        # The colony's New experiment form too.
        self.assertIn('value="derived:delta_ct"', self.a.get("/colony?view=experiments").get_data(as_text=True))
        r = self.a.post("/colony/experiments/create", data={"name": uniq("Exp "), "readout": "derived:delta_ct"})
        exp = int(location(r).rsplit("/", 1)[1])
        self.assertEqual(self.data(exp)["readout"]["formula"], "delta_ct")

    def test_an_organism_weighed_and_dosed_by_weight(self):
        key = self.make_organism_module(self.a)
        animal = self.make_animal(self.a, key)
        exp = self.new(f"organisms:{key}", start=TODAY)
        self.assertEqual(self.data(exp)["readout"]["key"], "body_weight")
        self.add(exp, "one", str(animal))
        subject = f"organism:{animal}"
        self.read(exp, TODAY, {subject: "30"})
        step = self.a.post(f"/colony/experiments/{exp}/steps/save", json={
            "kind": "injection", "agent": "Drug", "dose": "10 mg/kg", "days": "1"}).get_json()["steps"][0]["id"]
        detail = self.a.get(f"/colony/experiments/{exp}/steps/{step}/day/1").get_json()
        self.assertEqual(detail["animals"][0]["amount"], "0.3 mg")


class Mice(Base):
    def test_another_readout_for_mice_is_kept_with_the_experiment(self):
        r = self.a.post("/colony/experiments/create", data={"name": uniq("Tumour "), "start_date": TODAY.isoformat()})
        exp = int(location(r).rsplit("/", 1)[1])
        mouse = self.make_mouse(self.a, self.admin)
        self.add(exp, "one", str(mouse))
        self.a.post(f"/colony/experiments/{exp}/add-mouse", data={"mouse_row_id": mouse})   # the old way too: no duplicate
        self.assertEqual(count("experiment_mice", "experiment_id_fk=?", exp), 1)
        self.a.post(f"/experiments/{exp}/update", data={"readout": "tumour_volume"})
        self.read(exp, TODAY, {f"mouse:{mouse}": "112.5"})
        self.assertEqual(one("select value from experiment_readings where experiment_id_fk=?", exp), 112.5)
        self.assertEqual(count("mouse_weights", "mouse_id_fk=?", mouse), 0)
        # Body weight again: the mouse's own weights.
        self.a.post(f"/experiments/{exp}/update", data={"readout": "body_weight"})
        self.read(exp, TODAY, {f"mouse:{mouse}": "24.2"})
        self.assertEqual(one("select grams from mouse_weights where mouse_id_fk=?", mouse), 24.2)
        # A mouse experiment's generic address is the colony's page.
        self.assertEqual(location(self.a.get(f"/experiments/{exp}")), f"/colony/experiments/{exp}")

    def test_toggling_one_mouse_in_a_recorded_day(self):
        r = self.a.post("/colony/experiments/create", data={"name": uniq("TAM "), "start_date": TODAY.isoformat()})
        exp = int(location(r).rsplit("/", 1)[1])
        mice = [self.make_mouse(self.a, self.admin) for _ in range(2)]
        for m in mice:
            self.add(exp, "one", str(m))
        data = self.a.post(f"/colony/experiments/{exp}/record-now", json={
            "kind": "injection", "agent": "Tamoxifen", "dose": "20 mg/kg", "done_on": TODAY.isoformat()}).get_json()
        step = data["schedule"][0]["step_id"]
        self.assertEqual(data["schedule"][0]["record"]["count"], 2)
        data = self.a.post(f"/colony/experiments/{exp}/steps/{step}/day/1/subject",
                           json={"subject": f"mouse:{mice[0]}", "given": False}).get_json()
        self.assertEqual([e["subject"] for e in data["schedule"][0]["record"]["subjects"]], [f"mouse:{mice[1]}"])


class Rules(Base):
    def test_only_the_owner_or_an_admin_changes_it(self):
        exp = self.new("zebrafish", client=self.a)
        self.assertEqual(self.add(exp, "group", "1", client=self.m).status_code, 403)
        self.assertEqual(self.read(exp, TODAY, {}, client=self.m).status_code, 403)
        self.assertEqual(self.m.post(f"/experiments/{exp}/update", data={"name": "x"}).status_code, 403)
        self.assertEqual(self.m.get(f"/experiments/{exp}/data.json").status_code, 200)       # but may look

    def test_no_readout_ahead_and_the_page_needs_its_database(self):
        exp = self.new("zebrafish")
        self.assertEqual(self.read(exp, TODAY + timedelta(days=1), {}).status_code, 400)
        self.assertEqual(self.a.get("/experiments/in/stocks:no-such").status_code, 404)

    def test_deleting_leaves_the_animals(self):
        tank = self.make_tank(self.a)
        self.a.post("/zebrafish/fish/create", data={"tank_id_fk": str(tank), "count": "3"})
        exp = self.new("zebrafish")
        self.add(exp, "group", str(tank))
        before = count("fish")
        r = self.post(self.a, f"/experiments/{exp}/delete")
        self.assertIn("Deleted experiment", r.get_data(as_text=True))
        self.assertEqual((count("experiments", "id=?", exp), count("experiment_subjects", "experiment_id_fk=?", exp)), (0, 0))
        self.assertEqual(count("fish"), before)

    def test_readouts_and_kinds_fit_the_animal(self):
        self.assertEqual(xp.READOUT_CHOICES["fly"][0], "survival")
        self.assertNotIn("body_weight", xp.READOUT_CHOICES["fly"] + xp.READOUT_CHOICES["worm"])
        self.assertEqual(xp.kind_info("worm", "weigh"), ("Readout day", "chart"))


class Notebook(Base):
    def test_the_block_reads_any_experiment_and_its_readout(self):
        key = self.make_stock_module(self.a)
        vial = self.make_vial(self.a, key)
        exp = self.new(f"stocks:{key}", start=TODAY)
        self.add(exp, "one", str(vial))
        self.a.post(f"/experiments/{exp}/subjects/unit:{vial}/update", json={"start": "20"})
        self.read(exp, TODAY, {f"unit:{vial}": "17"})
        got = self.a.get(f"/colony/experiments/{exp}/notebook.json").get_json()["experiment"]
        self.assertEqual((got["readout"]["label"], got["nouns"]), ("Survival", got["nouns"]))
        self.assertEqual(got["weights"]["rows"][0]["pct"], [85.0])
        self.assertEqual(got["url"], f"/experiments/{exp}")
        listed = self.a.get("/colony/experiments/notebook-list.json").get_json()["experiments"]
        self.assertIn(exp, [e["id"] for e in listed])
        r = self.a.post(f"/colony/experiments/{exp}/notebook")
        body = one("select body from notebook_pages where id=?", int(location(r).split("page=")[1]))
        self.assertIn("```experiment", body)


class Regimens(Base):
    def mouse_exp(self, start=TODAY):
        r = self.a.post("/colony/experiments/create", data={"name": uniq("Asthma "), "start_date": start.isoformat()})
        return int(location(r).rsplit("/", 1)[1])

    def test_a_saved_regimen_plans_the_next_cohort_but_records_nothing(self):
        first = self.mouse_exp()
        self.a.post(f"/colony/experiments/{first}/steps/save", json={"kind": "injection", "agent": "Tamoxifen", "dose": "20 mg/kg", "days": "1"})
        self.a.post(f"/colony/experiments/{first}/steps/save", json={"kind": "challenge", "agent": "HDM", "dose": "25 µg", "days": "2-5"})
        name = uniq("TAM then HDM ")
        self.a.post(f"/colony/experiments/{first}/regimens/save", json={"name": name})
        second = self.mouse_exp()
        regimen = next(r for r in self.data(second)["regimens"] if r["name"] == name)
        data = self.a.post(f"/colony/experiments/{second}/regimens/{regimen['id']}/apply", json={}).get_json()
        self.assertEqual([(s["agent"], s["days_label"]) for s in data["steps"]], [("Tamoxifen", "1"), ("HDM", "2–5")])
        self.assertTrue(all(r["record"] is None for r in data["schedule"]))          # planned, not done
        self.assertEqual(count("experiment_step_records", "step_id_fk in (select id from experiment_steps where experiment_id_fk=?)", second), 0)

    def test_the_owner_is_reminded_once_a_day_of_what_is_due(self):
        from app import notify
        from app.db import SessionLocal
        from app.models import UserAccount
        from tests.base import client_for, make_user
        who = make_user(uniq("owner"))                 # nobody who opened a page today, so not reminded yet
        c = client_for(who)
        r = c.post("/colony/experiments/create", data={"name": uniq("Asthma "), "start_date": (TODAY - timedelta(days=1)).isoformat()})
        exp = int(location(r).rsplit("/", 1)[1])
        c.post(f"/colony/experiments/{exp}/steps/save", json={"kind": "challenge", "agent": "HDM", "dose": "25 µg", "days": "1-2"})
        execute_ = __import__("tests.base", fromlist=["execute"]).execute
        execute_("delete from notifications where recipient_username=?", who)
        from app.app import app
        with app.test_request_context(), SessionLocal() as s:
            from flask import g
            user = s.scalar(__import__("sqlalchemy").select(UserAccount).where(UserAccount.username == who))
            g.user = user
            self.assertTrue(notify.daily_experiment_reminder(s, user))
            s.commit()
            self.assertFalse(notify.daily_experiment_reminder(s, user))               # once a day
        title = one("select title from notifications where recipient_username=? and category='experiments' order by id desc limit 1", who)
        self.assertIn("HDM 25 µg", title)
        self.assertIn("overdue", title)


class ReagentsAndSamples(Base):
    def test_the_lot_used_is_kept_and_an_expired_one_is_flagged(self):
        item = self.make_item(self.a, "reagents", name=uniq("HDM extract "), lot="L-77", expires_on=(TODAY - timedelta(days=3)).isoformat())
        r = self.a.post("/colony/experiments/create", data={"name": uniq("HDM "), "start_date": TODAY.isoformat()})
        exp = int(location(r).rsplit("/", 1)[1])
        self.add(exp, "one", str(self.make_mouse(self.a, self.admin)))
        self.assertIn(item, [x["id"] for x in self.data(exp)["reagents"]])
        data = self.a.post(f"/colony/experiments/{exp}/record-now", json={
            "kind": "challenge", "agent": "HDM", "dose": "25 µg", "done_on": TODAY.isoformat(), "reagent_id": str(item)}).get_json()
        rec = data["schedule"][0]["record"]
        self.assertEqual((rec["reagent"]["lot"], rec["reagent"]["id"]), ("L-77", item))
        self.assertTrue(any("expired" in p for p in data["problems"]))

    def test_sample_records_only_when_asked(self):
        r = self.a.post("/colony/experiments/create", data={"name": uniq("Harvest "), "start_date": TODAY.isoformat()})
        exp = int(location(r).rsplit("/", 1)[1])
        mice = [self.make_mouse(self.a, self.admin) for _ in range(2)]
        for m in mice:
            self.add(exp, "one", str(m))
        self.assertIn("samples", [i["key"] for i in self.data(exp)["sample_inventories"]])
        before = count("inventory_items")
        self.a.post(f"/colony/experiments/{exp}/record-now", json={"kind": "sample", "agent": "Blood", "done_on": TODAY.isoformat()})
        self.assertEqual(count("inventory_items"), before)                             # not asked: none
        data = self.a.post(f"/colony/experiments/{exp}/record-now", json={
            "kind": "sample", "agent": "Lung", "done_on": TODAY.isoformat(), "make_samples": True,
            "sample_inventory": "samples", "sample_name": "Lung"}).get_json()
        made = data["schedule"][-1]["record"]["samples"]
        self.assertEqual(len(made), 2)
        mouse_id = one("select mouse_id from mice where id=?", mice[0])
        attrs = __import__("json").loads(one("select attrs from inventory_items where id=?", made[0]["id"]))
        self.assertEqual(attrs["source"], {"kind": "mouse", "ref": str(mouse_id)})
        self.assertIn("Lung", one("select name from inventory_items where id=?", made[0]["id"]))


class StatsAndExport(Base):
    def test_groups_compared_day_by_day_and_exported(self):
        from app import exp_stats
        tank_a, tank_b = self.make_tank(self.a), self.make_tank(self.a)
        for t in (tank_a, tank_b):
            for _ in range(3):
                self.a.post("/zebrafish/fish/create", data={"tank_id_fk": str(t), "count": "10"})
        exp = self.new("zebrafish", readout="length", start=TODAY)
        self.add(exp, "group", str(tank_a), group="Drug")
        self.add(exp, "group", str(tank_b), group="Vehicle")
        subs = self.data(exp)["subjects"]
        values = {s["key"]: str((3.1 + i * 0.1) if s["group"] == "Drug" else (4.0 + i * 0.1)) for i, s in enumerate(subs)}
        day = self.read(exp, TODAY, values).get_json()["table"]["stats"]["raw"][0]
        self.assertEqual(day["test"], "Welch's t-test")
        self.assertLess(day["p"], 0.05)
        self.assertEqual(exp_stats.chi_square([(10, 0), (10, 0)])["p"], 1.0)
        xlsx = self.a.get(f"/experiments/{exp}/export.xlsx")
        self.assertEqual(xlsx.status_code, 200)
        from openpyxl import load_workbook
        import io
        book = load_workbook(io.BytesIO(xlsx.data))
        self.assertEqual(book.sheetnames, ["Readout", "Readout, wide", "Manipulations", "Tests by day"])
        self.assertEqual(book["Readout"].max_row, len(subs) + 1)
        csv = self.a.get(f"/experiments/{exp}/export.csv").get_data(as_text=True)
        self.assertIn("Standard length (mm)", csv.splitlines()[0])

    def test_bench_mode_and_its_qr(self):
        exp = self.new("zebrafish")
        html = self.get_ok(self.a, f"/experiments/{exp}/bench")
        self.assertIn("xb-data", html)
        qr = self.a.get(f"/experiments/{exp}/bench.svg")
        self.assertIn(qr.status_code, (200, 404))          # 404 only without segno
        if qr.status_code == 200:
            # An <img> draws an SVG file only with its namespace: without it, a broken-image icon.
            self.assertIn('<svg xmlns="http://www.w3.org/2000/svg"', qr.get_data(as_text=True))


class ClutchesAndCohorts(Base):
    def test_larvae_of_a_clutch_and_an_organism_cohort(self):
        from app.db import SessionLocal
        from app.models import ClutchRecord, OrgCohort
        with SessionLocal() as s:
            clutch = ClutchRecord(clutch_id=uniq("CL"), date_of_fertilization=TODAY - timedelta(days=5), larvae_count=60)
            s.add(clutch)
            s.commit()
            clutch_id = clutch.id
        exp = self.new("zebrafish")
        page = self.get_ok(self.a, f"/experiments/{exp}")
        self.assertIn(f'value="clutch:{clutch_id}"', page)
        subs = self.add(exp, "one", f"clutch:{clutch_id}").get_json()["subjects"]
        self.assertEqual((subs[0]["key"], subs[0]["start"]), (f"clutch:{clutch_id}", 60))
        key = self.make_organism_module(self.a)
        mid = self.organism_module_id(key)
        with SessionLocal() as s:
            cohort = OrgCohort(module_id_fk=mid, code=uniq("C"), count_initial=30, count_current=28)
            s.add(cohort)
            s.commit()
            cohort_id = cohort.id
        exp2 = self.new(f"organisms:{key}")
        subs = self.add(exp2, "one", f"cohort:{cohort_id}").get_json()["subjects"]
        self.assertEqual((subs[0]["key"], subs[0]["start"]), (f"cohort:{cohort_id}", 30))


class EachDatabaseItsOwn(Base):
    """What an experiment offers follows its database's animals."""

    def test_a_plant_database_measures_and_treats_plants(self):
        from tests.test_plants import plant_module
        key = plant_module(self.a)
        tab = self.get_ok(self.a, f"/organisms/{key}?view=experiments")
        self.assertIn('value="germination"', tab)
        self.assertNotIn('value="body_weight"', tab)
        self.assertNotIn("Tumour", tab)
        exp = self.new(f"organisms:{key}")
        data = self.data(exp)
        self.assertEqual(data["family"], "plant")
        self.assertEqual(data["readout"]["key"], "germination")
        kinds = {k["key"] for k in data["kinds"]}
        self.assertIn("spray", kinds)
        self.assertNotIn("surgery", kinds)
        # An animal database made from scratch keeps the animal's.
        other = self.data(self.new(f"organisms:{self.make_organism_module(self.a)}"))
        self.assertEqual(other["family"], "organism")
        self.assertIn("surgery", {k["key"] for k in other["kinds"]})

    def test_each_animal_shows_its_age_on_day_1(self):
        start = TODAY - timedelta(days=2)
        mouse = self.make_mouse(self.a, self.admin, date_of_birth=(start - timedelta(days=70)).isoformat())
        r = self.a.post("/colony/experiments/create", data={"name": uniq("Age "), "start_date": start.isoformat()})
        exp = int(location(r).rsplit("/", 1)[1])
        self.add(exp, "one", str(mouse))
        self.assertEqual(self.data(exp)["subjects"][0]["age"], "10w")
        csv = self.a.get(f"/experiments/{exp}/export.csv").get_data(as_text=True)
        self.assertIn("Age on day 1", csv.splitlines()[0])

    def test_survival_over_the_whole_experiment_by_the_log_rank_test(self):
        from app import exp_stats
        rows = [{"group": "Drug", "start": 20, "values": [20, 15, 9, 4], "left_out": ""},
                {"group": "Vehicle", "start": 20, "values": [20, 20, 19, 19], "left_out": ""},
                {"group": "Vehicle", "start": 5, "values": [None, 5, None, 5], "left_out": "too old"}]
        table = {"rows": rows, "dates": ["d1", "d2", "d3", "d4"], "days": [1, 2, 3, 4],
                 "readout": {"key": "survival", "kind": "fraction"}}
        out = exp_stats.logrank(table)
        self.assertEqual(out["test"], "Log-rank test")
        self.assertLess(out["p"], 0.001)
        drug = next(g for g in out["groups"] if g["group"] == "Drug")
        self.assertEqual((drug["start"], drug["deaths"]), (20, 16))
        # The same counts in both: no difference; one group alone: no test.
        same = {**table, "rows": [{**rows[0], "group": "A"}, {**rows[0], "group": "B"}]}
        self.assertGreater(exp_stats.logrank(same)["p"], 0.99)
        self.assertIsNone(exp_stats.logrank({**table, "rows": rows[:1]}))


class EveryAnimalLooked(Base):
    """Randomizing, counts at the start, weights on the animal and their loss,
    for every database, not the colony's alone."""

    def test_randomize_shows_then_sets_balanced_seeded_groups(self):
        key = self.make_organism_module(self.a)
        animals = [self.make_animal(self.a, key, sex="F" if i % 2 else "M") for i in range(6)]
        exp = self.new(f"organisms:{key}")
        for a in animals:
            self.add(exp, "one", str(a))
        url = f"/experiments/{exp}/randomize"
        shown = self.a.post(url, json={"groups": "Drug, Vehicle", "seed": 7}).get_json()
        self.assertEqual([len(g["animals"]) for g in shown["allocation"]], [3, 3])
        self.assertEqual(self.a.post(url, json={"groups": ["Drug", "Vehicle"], "seed": 7}).get_json()["allocation"],
                         shown["allocation"])                                     # the same seed, the same groups
        self.assertTrue(all(s["group"] == "" for s in self.data(exp)["subjects"]))   # shown, not set
        done = self.a.post(url, json={"groups": "Drug, Vehicle", "seed": 7, "apply": 1}).get_json()
        groups = {s["label"]: s["group"] for s in done["subjects"]}
        for g in shown["allocation"]:
            self.assertEqual({groups[label] for label in g["animals"]}, {g["group"]})
        self.assertEqual(self.a.post(url, json={"groups": "Drug"}).status_code, 400)

    def test_a_rack_of_vials_added_with_how_many_each_started_with(self):
        key = self.make_stock_module(self.a)
        vials = [self.make_vial(self.a, key) for _ in range(2)]
        exp = self.new(f"stocks:{key}")
        self.a.post(f"/experiments/{exp}/subjects/add", json={"how": "one", "value": str(vials[0]), "start_each": "25"})
        self.a.post(f"/experiments/{exp}/subjects/add", json={"how": "one", "value": str(vials[1])})
        starts = sorted((s["start"] for s in self.data(exp)["subjects"]), key=lambda x: (x is None, x))
        self.assertEqual(starts, [25, None])
        r = self.a.post(f"/experiments/{exp}/subjects/add", json={"how": "one", "value": str(vials[1]), "start_each": "lots"})
        self.assertEqual(r.status_code, 400)

    def test_an_organism_weighed_keeps_its_weight_and_its_loss_is_flagged(self):
        from app import welfare
        from app.db import SessionLocal
        # Flagged only where the database keeps body weights (its Body weights capability).
        key = self.make_organism_module(self.a, capabilities=[
            "housing", "housing_grid", "individuals", "group_counts", "lines", "cohorts", "weights"])
        animal = self.make_animal(self.a, key)
        exp = self.new(f"organisms:{key}", start=TODAY - timedelta(days=3))
        self.add(exp, "one", str(animal))
        subject = f"organism:{animal}"
        self.read(exp, TODAY - timedelta(days=3), {subject: "30"})
        self.read(exp, TODAY, {subject: "24"})
        kept = self.a.get(f"/records/measurements?kind=org_animal&id={animal}").get_json()["measurements"]
        self.assertEqual([(m["metric"], m["value"]) for m in kept], [("body_weight", 24.0), ("body_weight", 30.0)])
        from app.app import app
        with app.test_request_context(), SessionLocal() as s:
            found = welfare.others_weight_loss(s, TODAY, 15)
        mine = [x for x in found if x["ref"] == {"kind": "org_animal", "id": animal}]
        self.assertEqual(len(mine), 1)
        self.assertEqual((mine[0]["peak_g"], mine[0]["latest_g"], mine[0]["loss_pct"]), (30.0, 24.0, 20.0))
        # The same weights in a database that doesn't keep body weights: not flagged.
        plain = self.make_organism_module(self.a)
        other = self.make_animal(self.a, plain)
        exp2 = self.new(f"organisms:{plain}", start=TODAY - timedelta(days=3))
        self.add(exp2, "one", str(other))
        self.read(exp2, TODAY - timedelta(days=3), {f"organism:{other}": "30"})
        self.read(exp2, TODAY, {f"organism:{other}": "24"})
        with app.test_request_context(), SessionLocal() as s:
            self.assertFalse([x for x in welfare.others_weight_loss(s, TODAY, 15)
                              if x["ref"] == {"kind": "org_animal", "id": other}])
        # Blanking a day's weight takes it off the animal too.
        self.read(exp, TODAY, {subject: ""})
        kept = self.a.get(f"/records/measurements?kind=org_animal&id={animal}").get_json()["measurements"]
        self.assertEqual([m["value"] for m in kept], [30.0])
        self.assertEqual(self.a.get("/records/measurements?kind=mouse&id=1").status_code, 400)

    def test_an_assistant_adds_a_rack_or_single_vials(self):
        from app import actions
        from app.app import app
        key = self.make_stock_module(self.a)
        vials = [self.make_vial(self.a, key) for _ in range(2)]
        exp = self.new(f"stocks:{key}")
        out = actions.run(app, self.admin, [{"action": "experiment_add_animals", "target": {"kind": "experiment", "id": exp},
                                             "fields": {"animals": [{"kind": "stock_unit", "id": v} for v in vials],
                                                        "group": "Paraquat", "start_each": 20}}],
                          apply=True, description=uniq("A"), source="Claude")
        self.assertTrue(out.ok, [c.as_dict() for c in out.changes])
        subs = self.data(exp)["subjects"]
        self.assertEqual(sorted((s["group"], s["start"]) for s in subs), [("Paraquat", 20), ("Paraquat", 20)])
        wrong = actions.run(app, self.admin, [{"action": "experiment_add_animals", "target": {"kind": "experiment", "id": exp},
                                               "fields": {"animals": [{"kind": "fish", "id": 1}]}}],
                            apply=False, description=uniq("A"), source="Claude")
        self.assertFalse(wrong.ok)
