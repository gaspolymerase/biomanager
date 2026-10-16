"""Animals that may need a look (app/welfare.py): losing weight, status and
experiments disagreeing, welfare words in recent notes, and mice not
weighed in an experiment that weighs; on Home, in the morning notification
and in the assistants' lab_checks."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from tests.base import client_for, days_ago, execute, location, make_user, one, uniq
from tests.test_api import Base

from app import actions, notify, welfare
from app.app import app
from app.db import SessionLocal
from app.models import UserAccount

TODAY = date.today()


class Welfare(Base):
    def tearDown(self):
        execute("delete from app_settings where key=?", welfare.LOSS_SETTING)
        super().tearDown()

    # --- helpers
    def experiment(self, client, start=None, name=None, status=None):
        r = client.post("/colony/experiments/create", data={"name": name or uniq("Exp "),
                                                            "start_date": start or days_ago(10)})
        exp = int(location(r).rsplit("/", 1)[1])
        if status:
            execute("update experiments set status=? where id=?", status, exp)
        return exp

    def add(self, client, exp, mouse, group=""):
        client.post(f"/colony/experiments/{exp}/add-mouse", data={"mouse_row_id": mouse, "treatment_group": group})

    def weigh(self, client, mouse, grams, on, notes=""):
        client.post(f"/colony/mice/{mouse}/weights/create", data={"weigh_date": on, "grams": str(grams),
                                                                 "notes": notes})

    def number(self, row):
        return one("select mouse_id from mice where id=?", row)

    def checks(self, owner=None, pct=None):
        with app.test_request_context(), SessionLocal() as s:
            return {c["id"]: c for c in welfare.checks(s, TODAY, owner, pct)}

    # A mouse's row id, which another kind's (a fish's, an animal's) may
    # share in a database of any size: matched with its kind.
    def ids(self, check, owner=None, pct=None, kind="mouse"):
        return [i["ref"]["id"] for i in self.checks(owner, pct)[check]["items"] if i["ref"]["kind"] == kind]

    def item(self, check, row, owner=None, pct=None, kind="mouse"):
        found = [i for i in self.checks(owner, pct)[check]["items"] if i["ref"] == {"kind": kind, "id": row}]
        self.assertEqual(len(found), 1, found)
        return found[0]

    def home_card(self, client, query=""):
        html = self.get_ok(client, "/home" + query)
        self.assertIn('data-home-card="welfare"', html)
        return html.split('data-home-card="welfare"', 1)[1].split('data-home-card="', 1)[0]


class LosingWeight(Welfare):
    def test_any_living_mouse_below_its_peak_of_the_last_30_days(self):
        thin = self.make_mouse(self.m, self.member, status="breeder")
        self.weigh(self.m, thin, 30, days_ago(20))
        self.weigh(self.m, thin, 25, days_ago(2))
        lost = self.item("weight_loss", thin)
        self.assertEqual((lost["peak_g"], lost["latest_g"], lost["loss_pct"], lost["basis"]), (30, 25, 16.7, "peak"))
        self.assertEqual((lost["peak_on"], lost["latest_on"]), (days_ago(20), days_ago(2)))
        self.assertNotIn("experiment", lost)
        # 10% is under the lab's 15%, until the lab says 10.
        steady = self.make_mouse(self.m, self.member)
        self.weigh(self.m, steady, 30, days_ago(10))
        self.weigh(self.m, steady, 27, days_ago(1))
        self.assertNotIn(steady, self.ids("weight_loss"))
        self.assertIn(steady, self.ids("weight_loss", pct=10))
        self.autosave(self.a, "/settings/lab", {"section": "general", "weight_loss_pct": "10", "lab_name": ""})
        self.assertEqual(one("select value from app_settings where key=?", welfare.LOSS_SETTING), "10")
        self.assertIn(steady, self.ids("weight_loss"))
        self.assertIn('name="weight_loss_pct"', self.get_ok(self.a, "/settings"))
        # Out of range: kept as it was.
        self.autosave(self.a, "/settings/lab", {"section": "general", "weight_loss_pct": "80", "lab_name": ""})
        self.assertEqual(one("select value from app_settings where key=?", welfare.LOSS_SETTING), "10")

    def test_one_weight_an_old_peak_or_a_dead_mouse_is_not_listed(self):
        once = self.make_mouse(self.m, self.member)
        self.weigh(self.m, once, 20, days_ago(1))
        old_peak = self.make_mouse(self.m, self.member)
        self.weigh(self.m, old_peak, 30, days_ago(40))
        self.weigh(self.m, old_peak, 24, days_ago(1))
        gone = self.make_mouse(self.m, self.member)
        self.weigh(self.m, gone, 30, days_ago(5))
        self.weigh(self.m, gone, 20, days_ago(1))
        self.post(self.m, f"/colony/mice/{gone}/update", {"status": "sac"})
        self.assertEqual(one("select status from mice where id=?", gone), "sac")
        gaining = self.make_mouse(self.m, self.member)
        self.weigh(self.m, gaining, 20, days_ago(5))
        self.weigh(self.m, gaining, 25, days_ago(1))
        moved = self.make_mouse(self.m, self.member, status="transfer")     # no longer the lab's
        self.weigh(self.m, moved, 30, days_ago(5))
        self.weigh(self.m, moved, 20, days_ago(1))
        found = self.ids("weight_loss")
        for row in (once, old_peak, gone, gaining, moved):
            self.assertNotIn(row, found)

    def test_below_its_first_weight_in_an_experiment_that_started(self):
        exp = self.experiment(self.m, start=days_ago(45))
        mouse = self.make_mouse(self.m, self.member)
        self.add(self.m, exp, mouse, "Drug")
        self.weigh(self.m, mouse, 30, days_ago(44))
        self.weigh(self.m, mouse, 28, days_ago(25))
        self.weigh(self.m, mouse, 24.5, days_ago(1))      # 12.5% below the window's peak, 18.3% below day 1
        lost = self.item("weight_loss", mouse)
        self.assertEqual((lost["basis"], lost["peak_g"], lost["peak_on"], lost["loss_pct"]),
                         ("experiment_start", 30, days_ago(44), 18.3))
        self.assertEqual((lost["experiment_ref"], lost["group"]), ({"kind": "experiment", "id": exp}, "Drug"))
        # A planned experiment hasn't started: only the window counts.
        execute("update experiments set status='planned' where id=?", exp)
        self.assertNotIn(mouse, self.ids("weight_loss"))


class StatusAndExperiment(Welfare):
    def test_experiment_status_in_no_experiment(self):
        self.experiment(self.m)                    # the lab keeps mouse experiments here
        loose = self.make_mouse(self.m, self.member, status="Experiment")
        booked = self.make_mouse(self.m, self.member, status="experiment")
        self.add(self.m, self.experiment(self.m, status="planned"), booked)
        breeder = self.make_mouse(self.m, self.member, status="breeder")
        item = self.item("status_experiment", loose)
        self.assertEqual((item["kind"], item["status"]), ("no_experiment", "Experiment"))
        self.assertNotIn("propose", item)
        found = self.ids("status_experiment")
        self.assertNotIn(booked, found)
        self.assertNotIn(breeder, found)

    def test_another_status_in_an_active_experiment_proposes_experiment(self):
        exp = self.experiment(self.m)
        geno = self.make_mouse(self.m, self.member, status="geno")
        fine = self.make_mouse(self.m, self.member, status="experiment")
        left = self.make_mouse(self.m, self.member, status="breeder")
        for row in (geno, fine, left):
            self.add(self.m, exp, row)
        execute("update experiment_mice set left_out='moved' where mouse_id_fk=?", left)
        item = self.item("status_experiment", geno)
        self.assertEqual((item["kind"], item["status"], item["experiment_ref"]["id"]), ("other_status", "geno", exp))
        self.assertEqual(item["propose"], {"action": "mouse_change", "target": {"kind": "mouse", "id": geno},
                                           "fields": {"status": "experiment"}})
        preview = actions.run(app, self.member, [item["propose"]])
        self.assertTrue(preview.ok, preview.errors)
        self.assertEqual(one("select status from mice where id=?", geno), "geno")      # only a preview
        found = self.ids("status_experiment")
        self.assertNotIn(fine, found)
        self.assertNotIn(left, found)
        # Paused: nothing is being done, so its statuses aren't asked about.
        execute("update experiments set status='paused' where id=?", exp)
        self.assertNotIn(geno, self.ids("status_experiment"))


class WordsInNotes(Welfare):
    def test_the_words_in_english_and_chinese(self):
        self.assertEqual(welfare.welfare_words("Looks HUNCHED today"), (["HUNCHED"], "Looks HUNCHED today"))
        self.assertEqual(welfare.welfare_words("Moved cage.\nnot eating, thin\nok")[0], ["not eating", "thin"])
        self.assertEqual(welfare.welfare_words("Moved cage.\nnot eating, thin\nok")[1], "not eating, thin")
        self.assertEqual(welfare.welfare_words("今天弓背，消瘦")[0], ["弓背", "消瘦"])
        self.assertEqual(welfare.welfare_words("精神差，呼吸困难")[0], ["精神差", "呼吸困难"])
        self.assertEqual(welfare.welfare_words("Labored  breathing")[0], ["Labored  breathing"])
        for calm in ("no wound seen", "Not hunched", "within limits, nothing to note", "未见溃疡", "无伤口", ""):
            self.assertEqual(welfare.welfare_words(calm), ([], ""), calm)

    def test_a_note_changed_in_the_last_two_weeks(self):
        hunched = self.make_mouse(self.m, self.member, note="Looks hunched, eyes half shut")
        item = self.item("welfare_notes", hunched)
        self.assertEqual((item["where"], item["words"], item["line"], item["changed_on"]),
                         ("note", ["hunched"], "Looks hunched, eyes half shut", TODAY.isoformat()))
        # The same note, last changed three weeks ago: not read.
        execute("update audit_log set changed_at=? where table_name='mice' and record_id=?",
                datetime.utcnow() - timedelta(days=21), hunched)
        self.assertNotIn(hunched, self.ids("welfare_notes"))
        # Changed again (another field doesn't count; the note does).
        self.post(self.m, f"/colony/mice/{hunched}/update", {"gender": "M"})
        self.assertNotIn(hunched, self.ids("welfare_notes"))
        self.post(self.m, f"/colony/mice/{hunched}/update", {"note": "消瘦，弓背"})
        self.assertEqual(self.item("welfare_notes", hunched)["words"], ["消瘦", "弓背"])
        calm = self.make_mouse(self.m, self.member, note="Bright and active")
        self.assertNotIn(calm, self.ids("welfare_notes"))

    def test_a_weights_note_and_a_tanks(self):
        mouse = self.make_mouse(self.m, self.member)
        self.weigh(self.m, mouse, 21, days_ago(3), notes="Dehydrated, skin tent")
        self.weigh(self.m, mouse, 22, days_ago(20), notes="wound on the back")
        [item] = [i for i in self.checks()["welfare_notes"]["items"] if i["ref"]["id"] == mouse]
        self.assertEqual((item["where"], item["words"], item["changed_on"], item["grams"]),
                         ("weight", ["Dehydrated"], days_ago(3), 21))
        tank = self.make_tank(self.m, notes="两条鱼精神差")
        found = [i for i in self.checks()["welfare_notes"]["items"] if i["ref"] == {"kind": "tank", "id": tank}]
        self.assertEqual([i["words"] for i in found], [["精神差"]])
        self.assertTrue(found[0]["url"].endswith(f"#tank-{tank}"))


class NotWeighed(Welfare):
    def test_seven_days_unless_the_regimen_weighs_more_often(self):
        exp = self.experiment(self.m, start=days_ago(10))
        late, recent, never, out = (self.make_mouse(self.m, self.member) for _ in range(4))
        for row in (late, recent, never, out):
            self.add(self.m, exp, row)
        self.weigh(self.m, late, 25, days_ago(9))
        self.weigh(self.m, recent, 25, days_ago(3))
        self.weigh(self.m, never, 25, days_ago(30))                 # before it started
        execute("update experiment_mice set left_out='died' where mouse_id_fk=?", out)
        item = self.item("not_weighed", late)
        self.assertEqual((item["days"], item["every_days"], item["last_weighed"]), (9, 7, days_ago(9)))
        self.assertEqual(self.item("not_weighed", never)["days"], 10)
        found = self.ids("not_weighed")
        self.assertNotIn(recent, found)
        self.assertNotIn(out, found)
        # Weighing every third day: five days is too long.
        r = self.m.post(f"/colony/experiments/{exp}/steps/save", json={"kind": "reading", "days": "1, 4, 7, 10, 13"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        self.weigh(self.m, recent, 25, days_ago(5))
        execute("delete from mouse_weights where mouse_id_fk=? and weigh_date=?", recent, date.fromisoformat(days_ago(3)))
        self.assertEqual(self.item("not_weighed", recent)["every_days"], 3)
        # Another readout: not weighed is not asked about.
        execute("update experiments set readout=? where id=?", json.dumps({"key": "tumour_volume"}), exp)
        self.assertEqual([i for i in self.checks()["not_weighed"]["items"] if i["experiment_ref"]["id"] == exp], [])

    def test_any_organism_is_counted_by_its_experiment_s_weight_readings(self):
        key = self.make_organism_module(self.a)
        late, weighed, out = (self.make_animal(self.a, key) for _ in range(3))
        r = self.a.post(f"/experiments/in/organisms:{key}/create", data={
            "name": uniq("Exp "), "readout": "", "start_date": days_ago(10)})
        exp = int(location(r).rsplit("/", 1)[1])
        execute("update experiments set status='active' where id=?", exp)
        for animal in (late, weighed, out):
            self.a.post(f"/experiments/{exp}/subjects/add", json={"how": "one", "value": str(animal), "group": ""})
        self.a.post(f"/experiments/{exp}/readings", json={"on": days_ago(9), "values": {f"organism:{late}": "30",
                                                                                      f"organism:{weighed}": "30"}})
        self.a.post(f"/experiments/{exp}/readings", json={"on": days_ago(2), "values": {f"organism:{weighed}": "29"}})
        execute("update experiment_subjects set left_out='died' where experiment_id_fk=? and subject_id=?", exp, out)
        found = [i for i in self.checks()["not_weighed"]["items"] if i["experiment_ref"]["id"] == exp]
        self.assertEqual([(i["ref"], i["days"], i["last_weighed"]) for i in found],
                         [({"kind": "org_animal", "id": late}, 9, days_ago(9))])
        self.assertTrue(found[0]["label"])

    def test_weigh_every_from_the_regimen(self):
        from types import SimpleNamespace
        step = lambda days, kind="reading": SimpleNamespace(kind=kind, days=days, treatment_group="")  # noqa: E731
        self.assertEqual(welfare.weigh_every(SimpleNamespace(steps=[])), 7)
        self.assertEqual(welfare.weigh_every(SimpleNamespace(steps=[step("1, 8, 15")])), 7)
        self.assertEqual(welfare.weigh_every(SimpleNamespace(steps=[step("1-5"), step("1", "injection")]), 3), 1)
        self.assertEqual(welfare.weigh_every(SimpleNamespace(steps=[step("1-5")]), 9), 7)    # after the last
        self.assertEqual(welfare.weigh_every(SimpleNamespace(steps=[step("1"), step("4, 6", "weigh")]), 2), 3)


class OnHome(Welfare):
    def test_a_member_sees_their_own_and_animal_care_the_labs(self):
        owner_name, other_name = make_user(uniq("keeper")), make_user(uniq("neighbour"))
        care_name = make_user(uniq("tech"), role="care")
        keeper, neighbour, tech = client_for(owner_name), client_for(other_name), client_for(care_name)
        thin = self.make_mouse(keeper, owner_name)
        self.weigh(keeper, thin, 30, days_ago(6))
        self.weigh(keeper, thin, 24, days_ago(1))
        mark = f"#{self.number(thin)}<"
        card = self.home_card(keeper)
        self.assertIn(mark, card)
        self.assertIn("Show the lab’s", card)
        self.assertIn("−20%", card)
        self.assertNotIn(mark, self.home_card(neighbour))
        self.assertIn(mark, self.home_card(neighbour, "?animals=lab"))
        self.assertIn(mark, self.home_card(tech))
        self.assertNotIn(mark, self.home_card(tech, "?animals=mine"))
        self.assertIn("Losing weight", card)

    def test_the_card_can_be_put_away(self):
        who = make_user(uniq("tidy"))
        c = client_for(who)
        self.assertIn('data-home-card="welfare"', self.get_ok(c, "/home"))
        self.post(c, "/home/cards", {"order": ["stats", "welfare"], "show": ["stats"]})
        self.assertNotIn('data-home-card="welfare"', self.get_ok(c, "/home"))


class MorningNotice(Welfare):
    def remind(self, username):
        with app.test_request_context(), SessionLocal() as s:
            user = s.query(UserAccount).filter_by(username=username).one()
            done = notify.daily_welfare_reminder(s, user)
            s.commit()
            return done

    def test_once_a_day_when_something_needs_a_look(self):
        name = make_user(uniq("early"))
        c = client_for(name)
        self.assertFalse(self.remind(name))                  # nothing of theirs
        thin = self.make_mouse(c, name, note="lethargic")
        self.weigh(c, thin, 30, days_ago(6))
        self.weigh(c, thin, 24, days_ago(1))
        self.assertTrue(self.remind(name))
        title = one("select title from notifications where recipient_username=? and category='welfare'", name)
        self.assertEqual(title, "Animals that may need a look: 1 mouse losing weight; 1 note that may need a look")
        self.assertEqual(one("select link from notifications where recipient_username=? and category='welfare'", name),
                         "/home#welfare")
        self.assertFalse(self.remind(name))                  # once a day
        # Turned off in Settings → Notifications.
        quiet = make_user(uniq("quiet"))
        q = client_for(quiet)
        other = self.make_mouse(q, quiet, note="hunched")
        self.assertIsNotNone(other)
        r = q.post("/settings", data={"action": "notifications", "notify_lab": "1"}, headers={"X-Autosave": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(one("select value from app_settings where key=?", f"notify_welfare:{quiet}"), "off")
        self.assertFalse(self.remind(quiet))
        q.post("/settings", data={"action": "notifications", "notify_welfare": "1"}, headers={"X-Autosave": "1"})
        self.assertTrue(self.remind(quiet))


class InLabChecks(Welfare):
    def test_the_four_checks_with_their_shape(self):
        token = self.token(self.m, scope="read")
        exp = self.experiment(self.m)
        geno = self.make_mouse(self.m, self.member, status="geno", note="弓背")
        self.add(self.m, exp, geno)
        self.weigh(self.m, geno, 30, days_ago(9))
        self.weigh(self.m, geno, 24, days_ago(8))
        r = self.call("get", "/lab-checks?scope=mine", token)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        checks = {c["id"]: c for c in r.get_json()["checks"]}
        for cid, severity in (("weight_loss", "high"), ("welfare_notes", "medium"), ("not_weighed", "medium"),
                              ("status_experiment", "low")):
            self.assertEqual(checks[cid]["severity"], severity)
            self.assertEqual(checks[cid]["count"], len(checks[cid]["items"]))
            [item] = [i for i in checks[cid]["items"] if i["ref"] == {"kind": "mouse", "id": geno}]
            self.assertTrue(item["url"].startswith("http://localhost/"), item["url"])
        self.assertIn("propose", [i for i in checks["status_experiment"]["items"] if i["ref"]["id"] == geno][0])
        self.assertIn("never a diagnosis", __import__("app.assistant_tools", fromlist=["TOOLS"]).TOOLS["lab_checks"][0])
        # The lab's threshold, unless asked for another.
        lost = self.call("get", "/lab-checks?scope=mine&weight_loss_pct=25", token).get_json()
        self.assertNotIn(geno, [i["ref"]["id"] for c in lost["checks"] if c["id"] == "weight_loss" for i in c["items"]])
        # Someone else's own view has none of these.
        theirs = self.call("get", "/lab-checks?scope=mine", self.token(self.o, scope="read")).get_json()
        for c in theirs["checks"]:
            self.assertNotIn({"kind": "mouse", "id": geno}, [i.get("ref") for i in c["items"]], c["id"])
