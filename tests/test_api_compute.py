"""What an assistant looks up and has BioManager work out, over /api/v1
(app/lookup.py, app/assistant_compute.py): as the token's person, with
what they can't open left out."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from tests.base import days_ago, days_ahead, execute, location, one, uniq
from tests.test_api import Base

from app import actions
from app.app import app

TODAY = date.today()


class Compute(Base):
    def setUp(self):
        super().setUp()
        self.read = self.token(self.m, scope="read")

    def get(self, path, token=None):
        r = self.call("get", path, token or self.read)
        return r

    def post_json(self, path, body, token=None):
        return self.call("post", path, token or self.read, json=body)

    def ok(self, r):
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:500])
        return r.get_json()

    def number(self, row):
        return one("select mouse_id from mice where id=?", row)

    def experiment(self, client, start=None, name=None):
        r = client.post("/colony/experiments/create", data={"name": name or uniq("Exp "),
                                                            "start_date": (start or days_ago(10))})
        return int(location(r).rsplit("/", 1)[1])

    def add(self, client, exp, mouse, group=""):
        client.post(f"/colony/experiments/{exp}/add-mouse", data={"mouse_row_id": mouse, "treatment_group": group})

    def weigh(self, client, mouse, grams, on):
        client.post(f"/colony/mice/{mouse}/weights/create", data={"weigh_date": on, "grams": str(grams)})

    def personal_organisms(self, client):
        label = uniq("MyFrogs")
        client.post("/organisms/new", data={"preset_key": "custom", "label": label, "audience": "me",
                                            "organism_noun": "frog", "identity_mode": "individual",
                                            "capabilities": ["housing", "individuals"]})
        return one("select key from organism_modules where label=?", label)


class Search(Compute):
    def test_words_in_notes_and_pages_with_a_snippet_and_a_reference(self):
        word = uniq("zebrinoid")
        mouse = self.make_mouse(self.m, self.member, note=f"Found under the hopper, {word} and calm.")
        r = self.m.post("/notebook/api/notes", data=json.dumps({"text": f"Checked the {word} cohort"}),
                        content_type="application/json")
        page = r.get_json()["page_id"]
        hits = self.ok(self.get(f"/search?q={word}"))["data"]
        by = {h["kind"]: h for h in hits}
        self.assertEqual(by["mouse"]["ref"], {"kind": "mouse", "id": mouse})
        self.assertIn(word, by["mouse"]["snippet"])
        self.assertTrue(by["mouse"]["url"].startswith("http://localhost/colony"))
        self.assertEqual(by["notebook_page"]["ref"], {"kind": "notebook_page", "id": page})
        self.assertIn(f"Checked the {word} cohort", by["notebook_page"]["snippet"])
        # Another member's search finds the mouse (the colony is the lab's), not the page.
        other = self.ok(self.get(f"/search?q={word}", self.token(self.o, scope="read")))["data"]
        self.assertEqual([h["kind"] for h in other], ["mouse"])
        self.assertEqual(self.get("/search").status_code, 400)

    def test_someone_elses_own_databases_stay_out(self):
        label = uniq("Mine")
        self.m.post("/inventory/new", data={"preset": "custom", "label": label, "audience": "me"})
        key = one("select key from inventory_modules where label=?", label)
        secret = uniq("Secretab")
        item = self.make_item(self.m, key, secret)
        mine = self.ok(self.get(f"/search?q={secret}"))["data"]
        self.assertEqual([h["ref"] for h in mine], [{"kind": "inventory_item", "id": item}])
        self.assertEqual(self.ok(self.get(f"/search?q={secret}", self.token(self.o, scope="read")))["data"], [])
        # An animal in someone's own organism database, here and on the search box.
        org = self.personal_organisms(self.m)
        code = uniq("FROG")
        animal = self.make_animal(self.m, org, code=code)
        self.assertIn({"kind": "org_animal", "id": animal},
                      [h["ref"] for h in self.ok(self.get(f"/search?q={code}"))["data"]])
        self.assertEqual(self.ok(self.get(f"/search?q={code}", self.token(self.o, scope="read")))["data"], [])
        self.assertEqual(self.o.get(f"/search?q={code}").get_json()["results"], [])
        self.assertEqual(len(self.m.get(f"/search?q={code}").get_json()["results"]), 1)


class History(Compute):
    def test_your_changes_and_an_admins_view_of_everyones(self):
        row = self.make_mouse(self.m, self.member, cage=uniq("C"))
        number = self.number(row)
        r = self.call("patch", f"/mice/{number}", self.token(self.m), json={"genotype": "Ai14/+"})
        self.assertEqual(r.status_code, 200, r.get_json())
        out = self.ok(self.get(f"/history?kind=mouse&code={number}"))
        self.assertEqual(out["ref"], {"kind": "mouse", "id": row})
        self.assertEqual(out["entries"][0]["by"], self.member)
        self.assertEqual(out["entries"][0]["changes"]["genotype"], ["", "Ai14/+"])
        self.assertIn("create", [e["action"] for e in out["entries"]])
        self.assertFalse(out["everyone"])
        # The same mouse, by its reference: another member sees none of these changes, an admin all.
        theirs = self.ok(self.get(f"/history?kind=mouse&id={row}", self.token(self.o, scope="read")))
        self.assertEqual(theirs["entries"], [])
        self.assertIn("admins", theirs["note"])
        admin = self.ok(self.get(f"/history?kind=mouse&id={row}", self.token(self.a, scope="read")))
        self.assertTrue(admin["everyone"])
        self.assertEqual(admin["entries"][0]["changes"]["genotype"][1], "Ai14/+")

    def test_a_record_in_someone_elses_own_database_is_not_there(self):
        label = uniq("Mine")
        self.m.post("/inventory/new", data={"preset": "custom", "label": label, "audience": "me"})
        key = one("select key from inventory_modules where label=?", label)
        item = self.make_item(self.m, key)
        self.ok(self.get(f"/history?kind=inventory_item&id={item}"))
        r = self.get(f"/history?kind=inventory_item&id={item}", self.token(self.o, scope="read"))
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.get("/history?kind=mouse").status_code, 400)
        self.assertEqual(self.get("/history?kind=spaceship&code=1").status_code, 404)


class Lineage(Compute):
    def test_a_mouses_parents_litter_and_offspring(self):
        father = self.make_mouse(self.m, self.member, gender="M", date_of_birth=days_ago(120), litter=uniq("L"))
        code = uniq("L")
        pup = self.make_mouse(self.m, self.member, litter=code, date_of_birth=days_ago(30))
        mate = self.make_mouse(self.m, self.member, litter=code, date_of_birth=days_ago(30))
        litter = one("select id from litters where litter_id=?", code)
        self.m.post(f"/colony/litters/{litter}/update", data={"father_info": f"#{self.number(father)} ♂",
                                                               "total_pups": "7"})
        out = self.ok(self.get(f"/lineage?kind=mouse&code={self.number(pup)}"))
        self.assertEqual(out["parents"]["father"]["mouse"], self.number(father))
        self.assertIsNone(out["parents"]["mother"])
        self.assertEqual(out["litter"]["pups_born"], 7)
        self.assertEqual([x["mouse"] for x in out["litter"]["littermates"]], [self.number(mate)])
        up = self.ok(self.get(f"/lineage?kind=mouse&id={father}"))
        self.assertEqual([o["litter"] for o in up["offspring"]], [code])
        self.assertEqual(sorted(x["mouse"] for x in up["offspring"][0]["mice"]),
                         sorted([self.number(pup), self.number(mate)]))

    def test_a_plasmids_family(self):
        parent = self.make_plasmid(self.m, name=uniq("pBack"))
        child = self.make_plasmid(self.m, name=uniq("pChild"))
        p_number = one("select plasmid_id from plasmids where id=?", parent)
        c_number = one("select plasmid_id from plasmids where id=?", child)
        self.post(self.m, f"/plasmids/{child}/parents", data={"parent": f"#{p_number}", "role": "backbone",
                                                             "method": "gibson"})
        out = self.ok(self.get(f"/lineage?kind=plasmid&code={c_number}"))
        self.assertEqual([(p["plasmid"], p["role"], p["method"]) for p in out["made_from"]],
                         [(p_number, "backbone", "gibson")])
        self.assertEqual(out["tree"]["up"][0]["plasmid"], p_number)
        down = self.ok(self.get(f"/lineage?kind=plasmid&id={parent}"))
        self.assertEqual([c["plasmid"] for c in down["used_to_make"]], [c_number])
        self.assertEqual(self.get(f"/lineage?kind=cage&code={uniq('C')}").status_code, 400)


class Availability(Compute):
    def test_by_genotype_sex_and_age_leaving_out_the_busy(self):
        locus = uniq("Gx")
        geno = {"transgene_1": f"{locus} fl/fl"}
        free = self.make_mouse(self.m, self.member, litter=uniq("L"), date_of_birth=days_ago(60), **geno)
        self.make_mouse(self.m, self.member, litter=uniq("L"), date_of_birth=days_ago(60), gender="M", **geno)
        self.make_mouse(self.m, self.member, litter=uniq("L"), date_of_birth=days_ago(200), **geno)
        busy = self.make_mouse(self.m, self.member, litter=uniq("L"), date_of_birth=days_ago(61), **geno)
        self.add(self.m, self.experiment(self.m), busy, "Vehicle")
        breeders = uniq("C")
        self.make_cage(self.m, breeders, purpose="Breeder")
        self.make_mouse(self.m, self.member, cage=breeders, litter=uniq("L"), date_of_birth=days_ago(62), **geno)
        out = self.ok(self.get(f"/availability?genotype={locus}&sex=F&age_min_days=50&age_max_days=90"))
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["animals"][0]["ref"], {"kind": "mouse", "id": free})
        self.assertEqual(out["animals"][0]["age_days"], 60)
        self.assertTrue(out["animals"][0]["yours_to_use"])
        # An admin may edit the member's mouse, but it isn't theirs to take.
        theirs = self.ok(self.get(f"/availability?genotype={locus}&sex=F&age_min_days=50&age_max_days=90",
                                  self.token(self.a, scope="read")))
        self.assertFalse(theirs["animals"][0]["yours_to_use"])
        self.assertEqual(out["left_out"], {"in_experiments": 1, "breeders": 1, "age_unknown": 0})
        more = self.ok(self.get(f"/availability?genotype={locus}&sex=F&age_min_days=50&age_max_days=90"
                                "&include_in_experiments=true&include_breeders=true"))
        self.assertEqual(more["count"], 3)
        later = self.ok(self.get(f"/availability?genotype={locus}&sex=F&age_min_days=50&age_max_days=90"
                                 f"&on={days_ahead(40)}"))
        self.assertEqual(later["count"], 0)                    # 100 days old by then
        self.assertEqual(self.get("/availability?sex=X").status_code, 400)
        fish = self.ok(self.get(f"/availability?species=zebrafish&genotype={locus}"))
        self.assertEqual((fish["species"], fish["count"]), ("zebrafish", 0))


class BreedingPlan(Compute):
    def test_odds_from_the_labs_own_breeders_and_when_to_set_up(self):
        locus = uniq("Fx")
        cage = uniq("C")
        self.make_cage(self.m, cage, purpose="Breeder")
        for sex in ("F", "M"):
            self.make_mouse(self.m, self.member, cage=cage, litter=uniq("L"), date_of_birth=days_ago(70), gender=sex,
                            transgene_1=f"{locus} fl/+")
        by = TODAY + timedelta(days=120)
        r = self.post_json("/breeding-plan", {"want": {locus: "hom"}, "sex": "F", "count": 4, "age_min_days": 56,
                                              "age_max_days": 70, "by": by.isoformat()})
        out = self.ok(r)
        best = out["crosses"][0]
        self.assertEqual((best["female"], best["male"]), (f"{locus} het", f"{locus} het"))
        self.assertEqual((best["odds"], best["odds_with_sex"], best["females"]), (0.25, 0.125, 1))
        self.assertGreaterEqual(best["matings"], best["matings_on_average"])
        self.assertEqual(best["females_short"], best["matings"] - 1)
        timing = out["timing"]
        self.assertEqual(timing["born_by"], (by - timedelta(days=56)).isoformat())
        self.assertEqual(timing["set_up_by"], (by - timedelta(days=56 + 21)).isoformat())
        self.assertEqual(timing["wean_by"], (by - timedelta(days=56 - 21)).isoformat())
        self.assertFalse(timing["too_late"])
        self.assertTrue(out["assumptions"])
        self.assertGreater(out["litter_size"], 0)
        self.assertIn(f"{locus} hom", json.dumps(out["best_possible"]))
        late = self.ok(self.post_json("/breeding-plan", {"want": {locus: "hom"}, "count": 2, "age_min_days": 56,
                                                         "by": days_ahead(10)}))
        self.assertTrue(late["timing"]["too_late"])

    def test_crosses_given_and_what_it_refuses(self):
        out = self.ok(self.post_json("/breeding-plan", {"want": {"Cre": "carrier", "Foxp3": "hom"}, "count": 6,
                                                        "crosses": [{"female": {"Foxp3": "hom"},
                                                                     "male": {"Cre": "het", "Foxp3": "hom"}}]}))
        self.assertEqual(out["crosses"][0]["odds"], 0.5)
        self.assertIsNone(out["timing"])
        for body in ({"want": {}}, {"want": {"A": "purple"}}, {"want": {"A": "hom"}, "sex": "X"},
                     {"want": {k: "hom" for k in "ABCDEF"}}, {"want": {"A": "hom"}, "count": "many"},
                     {"want": {"A": "hom"}, "litters_per_mating": 9},
                     {"want": {"A": "hom"}, "age_min_days": 2.5}):
            self.assertEqual(self.post_json("/breeding-plan", body).status_code, 400, body)


    def test_five_loci_and_more_litters_from_a_mating_given_time(self):
        out = self.ok(self.post_json("/breeding-plan", {"want": {k: "het" for k in "ABCDE"}, "count": 2,
                                                        "crosses": [{"female": {k: "hom" for k in "ABCDE"},
                                                                     "male": {}}]}))
        self.assertEqual(out["crosses"][0]["odds"], 1.0)
        one_each = self.ok(self.post_json("/breeding-plan", {"want": {"A": "hom"}, "count": 12,
                                                             "crosses": [{"female": {"A": "het"}, "male": {"A": "het"}}]}))
        self.assertEqual(one_each["litters_per_mating"], 1)
        # Wanted in 200 days, any age past weaning: a mating has time for several litters.
        roomy = self.ok(self.post_json("/breeding-plan", {"want": {"A": "hom"}, "count": 12, "by": days_ahead(200),
                                                          "crosses": [{"female": {"A": "het"}, "male": {"A": "het"}}]}))
        self.assertGreater(roomy["litters_per_mating"], 1)
        self.assertLess(roomy["crosses"][0]["matings"], one_each["crosses"][0]["matings"])
        asked = self.ok(self.post_json("/breeding-plan", {"want": {"A": "hom"}, "count": 12, "litters_per_mating": 2,
                                                          "crosses": [{"female": {"A": "het"}, "male": {"A": "het"}}]}))
        self.assertEqual(asked["crosses"][0]["litters_per_mating"], 2)

    def test_matings_under_way_breeders_in_experiments_and_clashes(self):
        locus = uniq("Gx")
        stock, pair = uniq("C"), uniq("C")
        self.make_cage(self.m, stock, purpose="Breeder")
        self.make_cage(self.m, pair, purpose="Breeding")
        het = {"transgene_1": f"{locus} fl/+"}
        free_f = self.make_mouse(self.m, self.member, cage=stock, litter=uniq("L"), date_of_birth=days_ago(70),
                                 gender="F", **het)
        busy_f = self.make_mouse(self.m, self.member, cage=stock, litter=uniq("L"), date_of_birth=days_ago(70),
                                 gender="F", **het)
        self.add(self.m, self.experiment(self.m), busy_f, "Vehicle")
        self.make_mouse(self.m, self.member, cage=stock, litter=uniq("L"), date_of_birth=days_ago(70), gender="M", **het)
        for sex in ("F", "M"):                              # a het × het pair mating now
            self.make_mouse(self.m, self.member, cage=pair, litter=uniq("L"), date_of_birth=days_ago(70), gender=sex,
                            **het)
        hom_m = self.make_mouse(self.m, self.member, cage=stock, litter=uniq("L"), date_of_birth=days_ago(70),
                                gender="M", transgene_1=f"{locus} fl/fl")
        out = self.ok(self.post_json("/breeding-plan", {"want": {locus: "hom"}, "count": 4}))
        het_het = next(c for c in out["crosses"] if c["male"] == f"{locus} het")
        self.assertEqual(het_het["matings_under_way"], 1)
        self.assertEqual(het_het["cages_under_way"], [pair])
        self.assertEqual(het_het["females"], 1)                         # one free: one mating, one in an experiment
        self.assertEqual(het_het["to_set_up"], het_het["matings"] - 1)
        self.assertEqual([b["mouse"] for b in het_het["in_experiments"]], [self.number(busy_f)])
        # Het females are wanted by both the het and the hom male's cross: a clash, said so.
        self.assertTrue(any(c["sex"] == "F" and len(c["crosses"]) == 2 for c in out["clashes"]), out["clashes"])
        self.assertTrue(any(c["male"] == f"{locus} hom" for c in out["crosses"]), hom_m)
        self.assertNotEqual(free_f, busy_f)


class Randomize(Compute):
    def test_balanced_seeded_groups_and_the_changes_to_propose(self):
        litters = [uniq("L"), uniq("L")]
        mice = []
        for i in range(8):
            row = self.make_mouse(self.m, self.member, litter=litters[i % 2], date_of_birth=days_ago(60),
                                  gender="F" if i < 4 else "M")
            self.weigh(self.m, row, 20 + i, days_ago(1))
            mice.append(row)
        exp = self.experiment(self.m)
        body = {"animals": [self.number(m) for m in mice], "groups": ["Vehicle", "Drug"], "seed": 11,
                "experiment": exp}
        out = self.ok(self.post_json("/randomize", body))                  # a read token may ask
        self.assertEqual(out["seed"], 11)
        for g in out["allocation"]:
            self.assertEqual(len(g["mice"]), 4)
            self.assertEqual(sorted(a["sex"] for a in g["animals"]), ["F", "F", "M", "M"])
        self.assertEqual([g["n"] for g in out["balance"]["groups"]], [4, 4])
        self.assertEqual(self.ok(self.post_json("/randomize", body))["allocation"], out["allocation"])
        self.assertEqual(one("select count(*) from experiment_mice where experiment_id_fk=?", exp), 0)
        self.assertEqual(len(out["propose"]), 2)
        preview = actions.run(app, self.member, out["propose"])
        self.assertTrue(preview.ok, preview.errors)
        self.assertEqual(one("select count(*) from experiment_mice where experiment_id_fk=?", exp), 0)
        picked = self.ok(self.post_json("/randomize", {**body, "seed": None}))["seed"]
        self.assertIsInstance(picked, int)

    def test_the_dead_are_left_out_and_bad_requests_refused(self):
        alive = [self.make_mouse(self.m, self.member) for _ in range(3)]
        dead = self.make_mouse(self.m, self.member, status="sac")
        out = self.ok(self.post_json("/randomize", {"animals": [self.number(m) for m in alive + [dead]],
                                                    "groups": [{"name": "A", "size": 1}, {"name": "B", "size": 1}],
                                                    "seed": 3}))
        self.assertEqual(out["left_out_not_alive"], [self.number(dead)])
        self.assertEqual(len(out["spares"]), 1)
        for body in ({"animals": [], "groups": ["A", "B"]}, {"animals": [self.number(alive[0])], "groups": ["A"]},
                     {"animals": [999999999], "groups": ["A", "B"]}):
            self.assertEqual(self.post_json("/randomize", body).status_code, 400, body)


class PowerFromRecords(Compute):
    def test_from_an_effect_size_or_the_labs_own_sd(self):
        out = self.ok(self.post_json("/power", {"d": 0.5}, self.token(self.m, scope="propose")))
        self.assertEqual(out["n_per_group"], 64)
        exp = self.experiment(self.m, start=days_ago(5))
        for group, grams in (("Vehicle", (20, 22, 24)), ("Drug", (30, 32, 34))):
            for g in grams:
                row = self.make_mouse(self.m, self.member)
                self.add(self.m, exp, row, group)
                self.weigh(self.m, row, g, days_ago(4))
        out = self.ok(self.post_json("/power", {"experiment": exp, "difference": 2}))
        self.assertEqual(out["sd_from"]["sd"], 2.0)
        self.assertEqual(out["sd_from"]["groups"], {"Vehicle": 3, "Drug": 3})
        self.assertEqual(out["sd_from"]["day"], 2)
        self.assertEqual(out["n_per_group"], 17)                          # d = 1
        self.assertEqual(self.post_json("/power", {"experiment": exp, "difference": 2, "day": 9}).status_code, 400)
        self.assertEqual(self.post_json("/power", {"groups": 3, "difference": 1}).status_code, 400)

    def test_an_experiment_in_someone_elses_own_database_is_not_there(self):
        org = self.personal_organisms(self.m)
        r = self.m.post(f"/experiments/in/organisms:{org}/create", data={"name": uniq("Exp "),
                                                                         "start_date": days_ago(3)})
        exp = int(location(r).rsplit("/", 1)[1])
        other = self.token(self.o, scope="read")
        self.assertEqual(self.post_json("/power", {"experiment": exp, "difference": 1}, other).status_code, 404)
        self.assertEqual(self.get(f"/experiments/{exp}/summary", other).status_code, 404)
        self.ok(self.get(f"/experiments/{exp}/summary"))


class ReorderForecast(Compute):
    def used(self, client, key, token):
        name = uniq("Tris")
        row = self.make_item(client, key, name, quantity="10", status="in stock")
        number = one("select number from inventory_items where id=?", row)
        for left in ("8", "6"):
            r = self.call("patch", f"/inventories/{key}/items/{number}", token, json={"quantity": left})
            self.assertEqual(r.status_code, 200, r.get_json())
        execute("update inventory_items set created_at=? where id=?", datetime.utcnow() - timedelta(days=20), row)
        execute("update audit_log set changed_at=? where table_name='inventory_items' and record_id=? "
                "and action='update'", datetime.utcnow() - timedelta(days=10), row)
        return row

    def test_when_numeric_stock_runs_out(self):
        row = self.used(self.m, "reagents", self.token(self.m))
        out = self.ok(self.get("/reorder-forecast?days=60&lead_days=7"))
        [item] = [i for i in out["items"] if i["ref"]["id"] == row]
        self.assertEqual(item["history"]["used"], 4.0)
        self.assertAlmostEqual(item["used_per_week"], 1.4, places=2)          # 4 used over 20 days
        self.assertAlmostEqual(item["days_left"], 30, delta=0.1)
        self.assertEqual(item["reorder_by"], (date.fromisoformat(item["runs_out_on"]) - timedelta(days=7)).isoformat())
        self.assertFalse(item["reorder_now"])
        soon = self.ok(self.get("/reorder-forecast?days=10"))
        self.assertNotIn(row, [i["ref"]["id"] for i in soon["items"]])

    def test_someone_elses_own_inventory_is_left_out(self):
        label = uniq("MyReagents")
        self.m.post("/inventory/new", data={"preset": "reagents", "label": label, "audience": "me"})
        key = one("select key from inventory_modules where label=?", label)
        row = self.used(self.m, key, self.token(self.m))
        self.assertIn(row, [i["ref"]["id"] for i in self.ok(self.get("/reorder-forecast?days=60"))["items"]])
        theirs = self.ok(self.get("/reorder-forecast?days=60", self.token(self.o, scope="read")))
        self.assertNotIn(row, [i["ref"]["id"] for i in theirs["items"]])


class LabChecks(Compute):
    def test_the_weeks_checks_with_their_fixes(self):
        empty = uniq("C")
        cage = self.make_cage(self.m, empty, purpose="Experiment", owner=self.member)
        execute("update mouse_cages set created_at=? where id=?", datetime.utcnow() - timedelta(days=10), cage)
        breeders = uniq("C")
        self.make_cage(self.m, breeders, purpose="Breeder", owner=self.member)
        old = self.make_mouse(self.m, self.member, cage=breeders, litter=uniq("L"), date_of_birth=days_ago(250))
        exp = self.experiment(self.m)
        thin = self.make_mouse(self.m, self.member)
        self.add(self.m, exp, thin, "Drug")
        self.weigh(self.m, thin, 30, days_ago(5))
        self.weigh(self.m, thin, 24, days_ago(1))
        expired = self.make_item(self.m, "reagents", uniq("Old buffer"), status="in stock", expires_on=days_ago(3))
        out = self.ok(self.get("/lab-checks?scope=mine"))
        checks = {c["id"]: c for c in out["checks"]}
        self.assertEqual([c["severity"] for c in out["checks"]],
                         sorted((c["severity"] for c in out["checks"]), key=["high", "medium", "low", "info"].index))
        [lost] = [i for i in checks["weight_loss"]["items"] if i["ref"]["id"] == thin]
        self.assertEqual((lost["loss_pct"], lost["peak_g"], lost["latest_g"]), (20.0, 30, 24))
        self.assertIn(old, [i["ref"]["id"] for i in checks["old_breeders"]["items"]])
        [gone] = [i for i in checks["empty_cages"]["items"] if i["ref"]["id"] == cage]
        self.assertEqual(gone["propose"], {"action": "cage_change", "target": {"kind": "cage", "id": cage},
                                           "fields": {"purpose": "", "rack": ""}})
        preview = actions.run(app, self.member, [gone["propose"]])
        self.assertTrue(preview.ok, preview.errors)
        self.assertEqual(one("select purpose from mouse_cages where id=?", cage), "Experiment")   # only a preview
        self.assertIn(expired, [i["ref"]["id"] for i in checks["expired_reagents"]["items"]])
        self.assertIn("note", checks["unsigned_pages"])                      # never signed a page: not checked
        self.assertLessEqual(out["summary"]["high"], sum(c["count"] for c in out["checks"] if c["severity"] == "high"))
        # Another member's own view has none of these.
        theirs = {c["id"]: c for c in self.ok(self.get("/lab-checks?scope=mine",
                                                       self.token(self.o, scope="read")))["checks"]}
        self.assertNotIn(thin, [i["ref"]["id"] for i in theirs["weight_loss"]["items"]])
        self.assertNotIn(cage, [i["ref"]["id"] for i in theirs["empty_cages"]["items"]])
        self.assertEqual(self.get("/lab-checks?scope=everyone").status_code, 400)


class ExperimentSummary(Compute):
    def test_the_facts_for_the_methods_and_arrive(self):
        start = days_ago(6)
        exp = self.experiment(self.m, start=start)
        mice = []
        for group, grams in (("Vehicle", (20, 21)), ("Tamoxifen", (22, 23))):
            for g in grams:
                row = self.make_mouse(self.m, self.member, litter=uniq("L"), date_of_birth=days_ago(66))
                self.add(self.m, exp, row, group)
                self.weigh(self.m, row, g, start)
                self.weigh(self.m, row, g - 1, days_ago(1))
                mice.append(row)
        r = self.m.post(f"/colony/experiments/{exp}/steps/save", json={
            "kind": "injection", "agent": "Tamoxifen", "dose": "75 mg/kg", "route": "i.p.", "days": "1",
            "group": "Tamoxifen"})
        step = r.get_json()["steps"][0]["id"]
        self.m.post(f"/colony/experiments/{exp}/steps/{step}/day/1/record", json={"done_on": start})
        out = self.ok(self.get(f"/experiments/{exp}/summary"))
        animals = out["animals"]
        self.assertEqual(animals["species"], "Mus musculus (mouse)")
        self.assertEqual(animals["total"], 4)
        self.assertEqual({g["group"]: g["n"] for g in animals["groups"]}, {"Vehicle": 2, "Tamoxifen": 2})
        self.assertEqual(animals["age_at_start_days"]["min"], 60)
        self.assertEqual(animals["weight_at_start_g"]["mean"], 21.5)
        [step_out] = out["procedures"]
        self.assertEqual((step_out["what"], step_out["done"]), ("Tamoxifen 75 mg/kg i.p.", 1))
        self.assertEqual(out["readouts"]["days_measured"], 2)
        self.assertEqual(out["statistics"]["tests"], ["Welch's t-test"])
        self.assertIn("difference", out["statistics"]["last_day"])
        items = {i["name"]: i for i in out["arrive"]}
        self.assertEqual(len(items), 10)
        self.assertIn("Control group: Vehicle.", items["Study design"]["recorded"])
        self.assertEqual(items["Randomisation"]["status"], "missing")
        self.assertEqual(self.get("/experiments/999999999/summary").status_code, 404)


class Tools(Compute):
    """The same, as an assistant's tools (app/assistant_tools.py)."""

    def test_through_the_tools(self):
        from tests.test_mcp import TestApi, bm
        api = TestApi(self.token(self.m, scope="propose"))
        self.assertEqual(bm.call_tool(api, "power_analysis", {"d": 0.8})["n_per_group"], 26)
        self.assertIn("error", bm.call_tool(api, "history", {}))
        row = self.make_mouse(self.m, self.member)
        out = bm.call_tool(api, "history", {"ref": {"kind": "mouse", "id": row}})
        self.assertEqual(out["ref"], {"kind": "mouse", "id": row})
        self.assertIn("checks", bm.call_tool(api, "lab_checks", {"scope": "mine"}))
        self.assertIn("matches", bm.call_tool(api, "search", {"q": str(self.number(row))}))
        plan = bm.call_tool(api, "breeding_plan", {"want": {"Ai14": "het"}, "crosses": [
            {"female": {"Ai14": "hom"}, "male": {}}]})
        self.assertEqual(plan["crosses"][0]["odds"], 1.0)
        self.assertIn("data", bm.list_records(api, "regimens", {"family": "mouse"}))
