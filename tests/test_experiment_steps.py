"""An experiment's manipulations (app/experiment_steps.py): the plan, doses
from body weight, recording days, the calendar and the notebook."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

# tests.base first: it points the app at a throwaway database before app is imported.
from tests.base import AppTestCase, count, execute, location, one, row, uniq
from app import i18n  # noqa: E402
from app import experiment_steps as xs  # noqa: E402


class Days(AppTestCase):
    def test_the_ways_labs_write_days(self):
        self.assertEqual(xs.parse_days("1"), [1])
        self.assertEqual(xs.parse_days("2-5"), [2, 3, 4, 5])
        self.assertEqual(xs.parse_days("day2/3/4/5"), [2, 3, 4, 5])
        self.assertEqual(xs.parse_days("1, 8, 15"), [1, 8, 15])
        self.assertEqual(xs.parse_days("d1-3, d7"), [1, 2, 3, 7])
        self.assertEqual(xs.parse_days("0 to 2"), [0, 1, 2])
        self.assertEqual(xs.days_label([2, 3, 4, 5, 9]), "2–5, 9")
        for bad in ("", "soon", "1-9999"):
            with self.assertRaises(ValueError):
                xs.parse_days(bad)

    def test_day_one_is_the_start_date(self):
        self.assertEqual(xs.day_date(date(2026, 9, 27), 1), date(2026, 9, 27))
        self.assertEqual(xs.day_date(date(2026, 9, 27), 4), date(2026, 9, 30))
        self.assertIsNone(xs.day_date(None, 4))


class Doses(AppTestCase):
    def test_per_weight_doses_and_volumes(self):
        self.assertEqual(xs.dose_for("20 mg/kg", "", 25.0), {"amount": "0.5 mg"})
        self.assertEqual(xs.dose_for("20 mg/kg", "10 mg/mL", 25.0), {"amount": "0.5 mg", "volume": "50 µL"})
        self.assertEqual(xs.dose_for("75 mg/kg", "20 mg/ml", 22.0), {"amount": "1.65 mg", "volume": "82.5 µL"})
        self.assertEqual(xs.dose_for("10 mL/kg", "", 25.0), {"volume": "250 µL"})
        self.assertEqual(xs.dose_for("20 mg/kg", "", None), {"needs": "a weight"})

    def test_fixed_and_free_text_doses(self):
        self.assertEqual(xs.dose_for("25 µg", "0.5 mg/mL", None), {"amount": "25 µg", "volume": "50 µL"})
        self.assertEqual(xs.dose_for("25 ug", "", 30.0), {"amount": "25 µg"})
        self.assertEqual(xs.dose_for("ad libitum", "", 30.0), {"amount": "ad libitum"})
        self.assertEqual(xs.dose_for("", "", 30.0), {})


class Recording(AppTestCase):
    def setUp(self):
        super().setUp()
        self.start = date.today() - timedelta(days=1)            # day 2 is today
        r = self.a.post("/colony/experiments/create", data={"name": uniq("HDM "), "start_date": self.start.isoformat()})
        self.exp = int(location(r).rsplit("/", 1)[1])
        self.mice = [self.make_mouse(self.a, self.admin) for _ in range(3)]
        for i, mouse in enumerate(self.mice):
            self.a.post(f"/colony/experiments/{self.exp}/add-mouse",
                        data={"mouse_row_id": mouse, "treatment_group": "HDM" if i < 2 else "PBS"})
            self.a.post(f"/colony/mice/{mouse}/weights/create",
                        data={"weigh_date": self.start.isoformat(), "grams": str(20 + i * 5)})

    def step(self, **fields):
        r = self.a.post(f"/colony/experiments/{self.exp}/steps/save", json=fields)
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()

    def test_a_plan_becomes_a_schedule_with_dates(self):
        self.step(kind="injection", agent="Tamoxifen", dose="20 mg/kg", route="i.p.", days="1")
        data = self.step(kind="challenge", agent="HDM", dose="25 µg", route="intranasal", days="2/3/4/5", group="HDM")
        rows = [(r["day"], r["title"], r["date"], r["state"], r["group_size"]) for r in data["schedule"]]
        self.assertEqual(rows[0], (1, "Tamoxifen 20 mg/kg i.p.", self.start.isoformat(), "overdue", 3))
        self.assertEqual(rows[1][:4], (2, "HDM 25 µg intranasal", date.today().isoformat(), "today"))
        self.assertEqual(rows[1][4], 2)                             # only the HDM group
        self.assertEqual(len(rows), 5)
        self.assertEqual(data["steps"][1]["days"], "2–5")

    def test_a_stimulation_keeps_its_settings(self):
        data = self.step(kind="stimulation", agent="473 nm laser", frequency="20 Hz", intensity="5 mW",
                         pulse_width="5 ms", duration="30 min", days="1-3")
        st = data["steps"][0]
        self.assertEqual((st["kind_label"], st["frequency"], st["intensity"], st["pulse_width"], st["duration"]),
                         ("Stimulation", "20 Hz", "5 mW", "5 ms", "30 min"))
        self.assertEqual(data["schedule"][0]["title"], "473 nm laser 20 Hz 5 mW 5 ms 30 min")
        self.assertIn("stimulation", [k["key"] for k in data["kinds"]])
        # A saved regimen carries them to the next experiment.
        r = self.a.post(f"/colony/experiments/{self.exp}/regimens/save", json={"name": uniq("Opto ")})
        self.assertEqual(r.status_code, 200, r.get_json())
        kept = json.loads(one("select steps from experiment_regimens order by id desc limit 1"))
        self.assertEqual((kept[0]["frequency"], kept[0]["duration"]), ("20 Hz", "30 min"))
        # Changed: they go with the line, and a blank one is taken off.
        data = self.step(id=st["id"], kind="stimulation", agent="473 nm laser", frequency="10 Hz", days="1-3")
        self.assertEqual((data["steps"][0]["frequency"], data["steps"][0]["intensity"]), ("10 Hz", ""))

    def test_recording_a_day_keeps_each_mouse_s_dose(self):
        data = self.step(kind="injection", agent="Tamoxifen", dose="20 mg/kg", concentration="10 mg/mL", days="1")
        step_id = data["steps"][0]["id"]
        detail = self.a.get(f"/colony/experiments/{self.exp}/steps/{step_id}/day/1").get_json()
        self.assertEqual([m["amount"] for m in detail["mice"]], ["0.4 mg", "0.5 mg", "0.6 mg"])
        self.assertEqual(detail["on"], self.start.isoformat())        # planned yesterday, so yesterday
        r = self.a.post(f"/colony/experiments/{self.exp}/steps/{step_id}/day/1/record",
                        json={"done_on": self.start.isoformat(), "mice": self.mice[:2], "note": "#3 escaped"})
        self.assertEqual(r.status_code, 200, r.get_json())
        done = r.get_json()["schedule"][0]["record"]
        self.assertEqual((done["count"], done["done_by"], done["note"]), (2, self.admin, "#3 escaped"))
        self.assertEqual([m["volume"] for m in done["mice"]], ["40 µL", "50 µL"])
        # Undone, it is due again.
        r = self.a.post(f"/colony/experiments/{self.exp}/steps/{step_id}/day/1/undo", json={})
        self.assertIsNone(r.get_json()["schedule"][0]["record"])

    def test_a_body_weight_day_records_the_weights(self):
        data = self.step(kind="weigh", days="2")
        step_id = data["steps"][0]["id"]
        today = date.today().isoformat()
        grams = {str(self.mice[0]): "19.2", str(self.mice[1]): "24,6", str(self.mice[2]): ""}
        r = self.a.post(f"/colony/experiments/{self.exp}/steps/{step_id}/day/2/record",
                        json={"done_on": today, "mice": self.mice, "grams": grams})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(one("select grams from mouse_weights where mouse_id_fk=? and weigh_date=?",
                             self.mice[1], today), 24.6)
        self.assertEqual(count("mouse_weights", "mouse_id_fk=? and weigh_date=?", self.mice[2], today), 0)
        table = self.a.get(f"/colony/experiments/{self.exp}/notebook.json").get_json()["experiment"]["weights"]
        self.assertEqual(table["days"], [1, 2])
        self.assertEqual(table["rows"][0]["pct"], [100.0, 96.0])

    def test_no_recording_ahead_or_for_someone_else(self):
        data = self.step(kind="injection", agent="Tamoxifen", dose="20 mg/kg", days="1")
        step_id = data["steps"][0]["id"]
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        r = self.a.post(f"/colony/experiments/{self.exp}/steps/{step_id}/day/1/record", json={"done_on": tomorrow})
        self.assertEqual(r.status_code, 400)
        r = self.m.post(f"/colony/experiments/{self.exp}/steps/{step_id}/day/1/record", json={})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.m.post(f"/colony/experiments/{self.exp}/steps/save",
                                     json={"agent": "x", "days": "1"}).status_code, 403)
        # …but they can read it, here and in their notebook.
        self.assertEqual(self.m.get(f"/colony/experiments/{self.exp}/notebook.json").status_code, 200)

    def test_a_recorded_day_can_t_silently_leave_the_plan(self):
        data = self.step(kind="challenge", agent="HDM", dose="25 µg", days="2-5")
        step_id = data["steps"][0]["id"]
        self.a.post(f"/colony/experiments/{self.exp}/steps/{step_id}/day/2/record", json={})
        r = self.a.post(f"/colony/experiments/{self.exp}/steps/save",
                        json={"id": step_id, "kind": "challenge", "agent": "HDM", "days": "3-5"})
        self.assertEqual(r.status_code, 409)
        r = self.a.post(f"/colony/experiments/{self.exp}/steps/{step_id}/delete", json={})
        self.assertEqual(r.status_code, 409)
        self.a.post(f"/colony/experiments/{self.exp}/steps/{step_id}/delete", json={"confirm": "1"})
        self.assertEqual(count("experiment_steps", "id=?", step_id), 0)
        self.assertEqual(count("experiment_step_records", "step_id_fk=?", step_id), 0)

    def test_due_days_are_on_the_calendar(self):
        self.step(kind="challenge", agent="HDM", dose="25 µg", days="2-5", group="HDM")
        start, end = date.today().isoformat(), (date.today() + timedelta(days=5)).isoformat()
        items = self.a.get(f"/calendar/events.json?start={start}&end={end}").get_json()["items"]
        mine = [i for i in items if i["id"].startswith("auto-expstep-") and i["raw"]["anchor_id"] == self.exp]
        self.assertEqual(len(mine), 4)
        self.assertIn("HDM 25 µg", mine[0]["title"])
        self.assertIn(f"/colony/experiments/{self.exp}#day-2", mine[0]["raw"]["href"])

    def test_the_page_shows_the_panel(self):
        html = self.get_ok(self.a, f"/colony/experiments/{self.exp}")
        self.assertIn("data-xp-sheet", html)
        self.assertIn('data-xp-open="record"', html)
        self.assertIn("Add to notebook", html)
        # The button, not its name: What's new may name it to anyone after an update.
        self.assertNotIn('data-xp-open="record"', self.get_ok(self.m, f"/colony/experiments/{self.exp}"))

    def test_add_to_notebook_makes_one_page_with_the_block(self):
        r = self.a.post(f"/colony/experiments/{self.exp}/notebook")
        self.assertEqual(r.status_code, 302)
        page = int(location(r).split("page=")[1])
        body, title = row("select body, title from notebook_pages where id=?", page)
        self.assertIn("```experiment", body)
        self.assertEqual(json.loads(body.split("```experiment\n")[1].split("\n```")[0])["id"], self.exp)
        self.assertEqual(one("select kind from notebook_page_info where page_id_fk=?", page), "experiment")
        again = self.a.post(f"/colony/experiments/{self.exp}/notebook")
        self.assertEqual(location(again), location(r))
        self.assertIn("Notebook page", self.get_ok(self.a, f"/colony/experiments/{self.exp}"))

    def test_the_block_s_list_puts_yours_first(self):
        items = self.a.get("/colony/experiments/notebook-list.json").get_json()["experiments"]
        self.assertTrue(items[0]["mine"])
        self.assertIn(self.exp, [e["id"] for e in items])

    def test_the_block_carries_the_other_measures_and_their_readings(self):
        """The notebook block can show another measure (an ELISA a plate
        filled) as well as the main readout: notebook.json carries each,
        with its readings by animal and day as the experiment page has them."""
        execute("update experiments set readout=? where id=?",
                json.dumps({"key": "body_weight", "measures": [{"id": 1, "label": "IL-6 ELISA", "unit": "pg/mL"}]}),
                self.exp)
        url = f"/colony/experiments/{self.exp}/notebook.json"
        keys = [m["key"] for m in self.a.get(url).get_json()["experiment"]["members"]]
        r = self.a.post(f"/experiments/{self.exp}/readings", json={"on": self.start.isoformat(), "measure": "measure:1",
                                                                    "values": {keys[0]: "0.45", keys[1]: "1.2"}})
        self.assertEqual(r.status_code, 200, r.get_json())
        got = self.a.get(url).get_json()["experiment"]
        self.assertEqual(got["readout"]["label"], "Body weight")            # the main readout is as it was
        self.assertEqual(got["weights"]["rows"][0]["values"], [20.0])
        [measure] = got["measures"]
        self.assertEqual((measure["key"], measure["label"], measure["unit"]), ("measure:1", "IL-6 ELISA", "pg/mL"))
        table = measure["table"]
        self.assertEqual((table["dates"], table["days"]), ([self.start.isoformat()], [1]))
        values = {row["key"]: row["values"] for row in table["rows"]}
        self.assertEqual((values[keys[0]], values[keys[1]], values[keys[2]]), ([0.45], [1.2], [None]))
        self.assertEqual(measure["table"]["rows"][1]["pct"], [100.0])
        # An experiment with none has an empty list (a frozen copy from before has no key at all).
        execute("update experiments set readout=? where id=?", json.dumps({"key": "body_weight"}), self.exp)
        self.assertEqual(self.a.get(url).get_json()["experiment"]["measures"], [])

    def test_the_words_the_block_gets_as_values_have_their_chinese(self):
        """The block translates what arrives in English, as the experiment
        page does (t() of the readout's name and unit, the kinds, the noun,
        the status, the database): each built-in one has its Chinese in the
        catalogue the notebook page loads. Units like g and mm stay as they are."""
        from app import derived
        from app import experiments as ex
        from app.app import app
        with app.test_request_context(headers={"Accept-Language": "zh-CN"}):
            js = i18n.js_catalog()
        words = {"Mouse colony", "Readout day", "Other", "mice", "mouse", "fish rows", "fish row", "Body weight",
                 "Measurement"}
        words |= {r["label"] for r in ex.READOUTS.values()}
        words |= {r["unit"] for r in ex.READOUTS.values() if re.search(r"[a-z]{3}", r["unit"])}
        words |= {f.label for f in derived.FORMULAS.values() if re.search(r"[a-z]{3}", f.label)}     # not ΔCt
        words |= {label for rows in ex.KINDS.values() for _key, label, _icon in rows}
        words |= {f"experiment::{status}" for status in ex.STATUSES}
        self.assertEqual(sorted(w for w in words if w not in js), [])
