"""What an assistant reads (app/assistant_tools.py over app/api.py and
app/lookup.py), as an end-to-end run of the tools found it wanting: a
reference that get can read, filters that aren't quietly ignored, a mouse's
experiments, a lean experiment, due items that name their records, the
person's own jobs first, checks counted once, and every status in use."""
from __future__ import annotations

from datetime import datetime, timedelta

from tests.base import days_ago, execute, one, uniq
from tests.test_api import Base
from tests.test_mcp import TestApi, bm

from app import assistant_tools as tools


class Paths(Base):
    def setUp(self):
        super().setUp()
        self.api = TestApi(self.token(self.m, scope="read"))

    def test_resolve_gives_the_path_get_reads_and_get_takes_a_ref(self):
        code = uniq("Q")
        cage = self.make_cage(self.m, code)
        self.assertNotEqual(str(cage), code)
        [hit] = bm.resolve(self.api, f"cage {code}")
        self.assertEqual(hit["path"], f"cages/{code}")
        self.assertEqual(bm.get(self.api, hit["path"])["cage_id"], code)
        self.assertEqual(bm.get(self.api, ref=hit["ref"])["cage_id"], code)
        out = bm.call_tool(self.api, "get", {"ref": {"kind": "cage", "id": cage}})
        self.assertEqual(out["cage_id"], code)

        mouse = self.make_mouse(self.m, self.member, cage=code)
        number = self.mouse_id(mouse)
        [hit] = bm.resolve(self.api, f"#{number}", "mouse")
        self.assertEqual(hit["path"], f"mice/{number}")
        self.assertEqual(bm.get(self.api, ref=hit["ref"])["mouse_id"], number)

        name = uniq("Ketamine")
        item = self.make_item(self.m, "reagents", name)
        [hit] = bm.resolve(self.api, name, "inventory_item")
        item_number = one("select number from inventory_items where id=?", item)
        self.assertEqual(hit["path"], f"inventories/reagents/items/{item_number}")
        self.assertEqual(bm.get(self.api, ref=hit["ref"])["name"], name)

        plasmid = self.make_plasmid(self.m)
        number = one("select plasmid_id from plasmids where id=?", plasmid)
        self.assertEqual(bm.get(self.api, ref={"kind": "plasmid", "id": plasmid})["plasmid_id"], number)

    def test_a_kind_read_only_in_lists_says_so(self):
        litter = self.make_litter(self.m)
        out = bm.call_tool(self.api, "get", {"ref": {"kind": "litter", "id": litter}})
        self.assertIn("isn't read on its own", out["error"])
        self.assertIn("error", bm.call_tool(self.api, "get", {"ref": {"kind": "cage", "id": 10 ** 9}}))
        self.assertIn("error", bm.call_tool(self.api, "get", {}))

    def test_the_descriptions_say_how_paths_and_filters_go(self):
        get, listing, history = (tools.TOOLS[n][0] for n in ("get", "list", "history"))
        self.assertIn("inventories/reagents/items/1", get)
        self.assertIn("ref", tools.TOOLS["get"][1]["properties"])
        for words in ("mice: alive, cage", "experiments: status (planned, active, paused, done, cancelled or all)",
                      "litters: born_since", "inventories/reagents/items"):
            self.assertIn(words, listing)
        self.assertIn("not mice moving in or out", history)
        self.assertIn("call whats_due too", tools.TOOLS["lab_checks"][0])
        self.assertIn("needs no proposal", tools.INSTRUCTIONS)
        self.assertIn("path resolve gives", tools.INSTRUCTIONS)


class Filters(Base):
    def test_experiments_all_and_unknown_filters_refused(self):
        tok = self.token(self.m, scope="read")
        r = self.m.post("/colony/experiments/create", data={"name": uniq("Exp "), "start_date": days_ago(3)})
        exp = int(r.headers["Location"].rsplit("/", 1)[1])
        every = self.call("get", "/experiments?status=all&limit=1000", tok)
        self.assertEqual(every.status_code, 200, every.get_json())
        self.assertIn(exp, [e["id"] for e in every.get_json()["data"]])
        bad = self.call("get", "/experiments?status=running", tok)
        self.assertEqual(bad.status_code, 400)
        self.assertIn("planned, active, paused, done, cancelled, or all", bad.get_json()["error"])
        for path, named in (("/mice?colour=brown", "alive"), ("/cages?rack=R1", "occupied"),
                            ("/litters?rack=12", "born_since"), ("/experiments?name=x", "status")):
            r = self.call("get", path, tok)
            self.assertEqual(r.status_code, 400, path)
            self.assertIn(named, r.get_json()["error"])
        self.assertEqual(self.call("get", "/mice?alive=true&limit=2", tok).status_code, 200)


class Records(Base):
    def test_a_mouse_names_its_experiments_and_a_plasmid_its_place(self):
        tok = self.token(self.m, scope="read")
        mouse = self.make_mouse(self.m, self.member, cage=uniq("C"))
        name = uniq("Exp ")
        r = self.m.post("/colony/experiments/create", data={"name": name, "start_date": days_ago(3)})
        exp = int(r.headers["Location"].rsplit("/", 1)[1])
        self.m.post(f"/colony/experiments/{exp}/add-mouse", data={"mouse_row_id": mouse, "treatment_group": "Drug"})
        execute("update experiment_mice set left_out=? where experiment_id_fk=? and mouse_id_fk=?",
                "Died on day 2", exp, mouse)
        body = self.call("get", f"/mice/{self.mouse_id(mouse)}", tok).get_json()
        [entry] = body["experiments"]
        self.assertEqual((entry["id"], entry["name"], entry["group"], entry["left_out"]),
                         (exp, name, "Drug", "Died on day 2"))
        self.assertIn("status", entry)

        box = self.make_box(self.a)
        plasmid = self.make_plasmid(self.m, box_id=box, position="B3")
        number = one("select plasmid_id from plasmids where id=?", plasmid)
        self.assertEqual(self.call("get", f"/plasmids/{number}", tok).get_json()["position"], "B3")

    def test_an_experiment_without_the_pages_pick_lists(self):
        tok = self.token(self.m, scope="read")
        r = self.m.post("/colony/experiments/create", data={"name": uniq("Exp "), "start_date": days_ago(3)})
        exp = int(r.headers["Location"].rsplit("/", 1)[1])
        mouse = self.make_mouse(self.m, self.member)
        self.m.post(f"/colony/experiments/{exp}/add-mouse", data={"mouse_row_id": mouse, "treatment_group": "Drug"})
        page = self.call("get", f"/experiments/{exp}", tok).get_json()["page"]
        for gone in ("kinds", "regimens", "reagents", "sample_inventories", "tests"):
            self.assertNotIn(gone, page)
        for kept in ("steps", "subjects", "groups", "table", "schedule", "deviations"):
            self.assertIn(kept, page)
        self.assertEqual(page["groups"], ["Drug"])
        html = self.m.get(f"/experiments/{exp}", follow_redirects=True).get_data(as_text=True)
        self.assertIn('"sample_inventories"', html)                         # the page keeps its pick lists


class Due(Base):
    def test_supplies_name_their_item_and_inventory_and_say_both(self):
        tok = self.token(self.m, scope="read")
        name = uniq("Ketamine")
        item = self.make_item(self.m, "reagents", name, status="low", expires_on=days_ago(900))
        due = self.call("get", "/due?days=0", tok).get_json()["data"]
        [it] = [i for i in due if i["title"] == f"Expired: {name}"]
        self.assertEqual(it["ref"], {"kind": "inventory_item", "id": item})
        label = one("select label from inventory_modules where key='reagents'")
        self.assertEqual(it["database"], label)
        self.assertEqual(it["detail"], f"{label} · expired and low")

    def test_colony_items_name_their_mice(self):
        tok = self.token(self.m, scope="read")
        cage = uniq("C")
        old = self.make_mouse(self.m, self.member, cage=cage, litter=uniq("L"), date_of_birth=days_ago(250))
        geno = self.make_mouse(self.m, self.member, cage=uniq("C"), status="geno")
        due = self.call("get", "/due?days=0", tok).get_json()["data"]
        [aged] = [i for i in due if i["title"].startswith("Past 30 w") and f"#{self.mouse_id(old)}" in i["title"]]
        self.assertIn({"mouse": self.mouse_id(old), "ref": {"kind": "mouse", "id": old}}, aged["mice"])
        [typing] = [i for i in due if i["title"] == f"Genotype #{self.mouse_id(geno)}"]
        self.assertEqual(typing["mice"], [{"mouse": self.mouse_id(geno), "ref": {"kind": "mouse", "id": geno}}])

    def test_a_transferred_mouse_is_not_due_at_30_weeks(self):
        tok = self.token(self.m, scope="read")
        # The oldest in the lab, so Home's card (its 15 oldest) would list both.
        kept = self.make_mouse(self.m, self.member, cage=uniq("C"), litter=uniq("L"), date_of_birth=days_ago(9000))
        gone = self.make_mouse(self.m, self.member, cage=uniq("C"), litter=uniq("L"), date_of_birth=days_ago(9000))
        execute("update mice set status='Transfer' where id=?", gone)   # left the lab: no date of death
        due = self.call("get", "/due?days=0", tok).get_json()["data"]
        aged = " ".join(i["title"] for i in due if i["title"].startswith("Past 30 w"))
        self.assertIn(f"#{self.mouse_id(kept)}", aged)
        self.assertNotIn(f"#{self.mouse_id(gone)}", aged)
        home = self.m.get("/home").get_data(as_text=True)
        self.assertIn(f">#{self.mouse_id(kept)}</span>", home)
        self.assertNotIn(f">#{self.mouse_id(gone)}</span>", home)

    def test_the_overview_puts_the_persons_own_jobs_first(self):
        theirs = self.make_mouse(self.m, self.member, cage=uniq("C"), status="geno")
        mine = self.make_mouse(self.a, self.admin, cage=uniq("C"), status="geno")
        api = TestApi(self.token(self.a, scope="read"))
        due = bm.lab_overview(api)["due_today"]
        flags = [i["mine"] for i in due]
        self.assertEqual(flags, sorted(flags, reverse=True))          # every one of mine before anyone else's
        titles = {i["title"]: i["mine"] for i in due}
        self.assertTrue(titles.get(f"Genotype #{self.mouse_id(mine)}"))
        if f"Genotype #{self.mouse_id(theirs)}" in titles:
            self.assertFalse(titles[f"Genotype #{self.mouse_id(theirs)}"])


class Checks(Base):
    def test_a_reagent_in_two_checks_counts_once(self):
        from app.assistant_compute import _distinct
        ref = {"kind": "inventory_item", "id": 5}
        checks = [{"id": "overdue_jobs", "items": [{"ref": ref}, {"ref": {"kind": "cage", "id": 1}},
                                                    {"ref": {"kind": "cage", "id": 1}}, {"ref": None}]},
                  {"id": "expired_reagents", "items": [{"ref": ref}]}]
        self.assertEqual(_distinct(checks), 4)        # the reagent once; two jobs on one cage are two
        tok = self.token(self.m, scope="read")
        self.make_item(self.m, "reagents", uniq("Old buffer"), status="low", expires_on=days_ago(5))
        out = self.call("get", "/lab-checks", tok).get_json()
        for severity in ("high", "medium"):
            self.assertLessEqual(out["summary"][severity],
                                 sum(c["count"] for c in out["checks"] if c["severity"] == severity))
        self.assertIn("summary_note", out)

    def test_the_forecast_names_what_it_cannot_forecast(self):
        tok = self.token(self.m)
        name = uniq("Tris")
        row = self.make_item(self.m, "reagents", name, quantity="10", status="in stock")
        execute("update inventory_items set created_at=? where id=?", datetime.utcnow() - timedelta(days=20), row)
        out = self.call("get", "/reorder-forecast?days=60", tok).get_json()
        self.assertIn({"ref": {"kind": "inventory_item", "id": row}, "name": name},
                      [{"ref": i["ref"], "name": i["name"]} for i in out["without_enough_history"]])


class Statuses(Base):
    def test_every_status_the_lab_s_mice_have(self):
        tok = self.token(self.m, scope="read")
        # A word of the lab's own (as "stock" or "breeding" is in some labs);
        # not "stock" itself, which the sheet import's tests give a capital.
        word = uniq("holding").lower()
        self.make_mouse(self.m, self.member, cage=uniq("C"), status=word)
        statuses = self.call("get", "/vocabulary", tok).get_json()["mouse_colony"]["statuses"]
        for status in ("breeder", "experiment", "geno", "transfer", "sac", word):
            self.assertIn(status, statuses)
        self.assertEqual(statuses[:5], ["breeder", "experiment", "geno", "transfer", "sac"])
