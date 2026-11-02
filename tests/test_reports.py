"""The write-ups an assistant drafts (app/assistant_reports.py, over
/api/v1/animal-use and /api/v1/handover): what each count is based on, and
who may ask about whom."""
from __future__ import annotations

import json
import re
import unittest
from datetime import date, timedelta
from pathlib import Path

from tests.base import client_for, days_ago, execute, location, make_user, one, uniq
from tests.test_api import Base

from app import assistant_reports as ar
from app import assistant_tools, mcp_http

TODAY = date.today()
ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- the pure parts

class Protocols(unittest.TestCase):
    def test_a_column_named_for_a_protocol_holds_it(self):
        defined = [{"key": "weight_at_wean", "label": "Weaning weight"}, {"key": "iacuc", "label": "IACUC #"},
                   {"key": "lic", "label": "Project licence"}, {"key": "x", "label": "伦理批号"}]
        self.assertEqual([f["key"] for f in ar.protocol_columns(defined)], ["iacuc", "lic", "x"])
        columns = ar.protocol_columns(defined)
        self.assertEqual(ar.protocol_of({"iacuc": "  2024-0012 ", "lic": "PP1"}, columns), ("2024-0012", "IACUC #"))
        self.assertEqual(ar.protocol_of({"iacuc": "", "lic": "PP1"}, columns), ("PP1", "Project licence"))
        self.assertEqual(ar.protocol_of({}, columns), ("", ""))

    def test_one_protocol_however_it_was_typed(self):
        self.assertTrue(ar.same_protocol("IS 2024-12", " is  2024-12"))
        self.assertFalse(ar.same_protocol("2024-12", "2024-13"))


class Reasons(unittest.TestCase):
    def test_the_notes_say_why_before_the_status(self):
        self.assertEqual(ar.death_reason("sac", "Hunched, lost weight: humane endpoint")[0],
                         "Humane endpoint or ill health")
        self.assertEqual(ar.death_reason("sac", "Tissue collected at the end of the experiment")[0],
                         "End of experiment or tissue collection")
        self.assertEqual(ar.death_reason("sac", "retired breeder")[0], "Retired breeder")
        self.assertEqual(ar.death_reason("sac", "wrong genotype, culled")[0], "Surplus or unwanted genotype")
        self.assertEqual(ar.death_reason("sac", "found dead in the cage")[0], "Found dead")
        # Only the status: it says died, or nothing about why.
        self.assertEqual(ar.death_reason("found dead"), ("Found dead", "found dead"))
        self.assertEqual(ar.death_reason("sac", "Genotyped by PCR in May"), ("Euthanized, reason not recorded", ""))
        self.assertEqual(ar.death_reason("experiment", ""), ("Ended, reason not recorded", ""))

    def test_the_words_it_was_read_from_come_back(self):
        self.assertEqual(ar.death_reason("sac", "Tumour on the flank")[1], "Tumour")


class Strains(unittest.TestCase):
    def test_the_labs_strain_in_the_genotype_else_the_genotype(self):
        strains = ["C57BL/6J", "Ai14", "B6"]
        self.assertEqual(ar.strain_of("C57BL/6J; Cre het", strains), "C57BL/6J")
        self.assertEqual(ar.strain_of("Dbh-Cre; Ai14 het", strains), "Ai14")
        self.assertEqual(ar.strain_of("Foxp3 fl/fl", strains), "Foxp3 fl/fl")
        self.assertEqual(ar.strain_of("", strains), "(not recorded)")


class Tally(unittest.TestCase):
    start, end = date(2025, 10, 1), date(2026, 9, 30)

    def entry(self, **kw):
        return {"species": ar.MOUSE, "strain": "B6", "protocol": "P1", **kw}

    def test_each_column_counts_what_the_period_holds(self):
        entries = [
            self.entry(born=date(2026, 1, 5), used=True),                                   # born, used
            self.entry(born=date(2024, 3, 1), added=date(2026, 2, 1), ended=date(2026, 3, 1),
                       reason="Humane endpoint or ill health"),                             # added, died
            self.entry(born=None, added=date(2026, 2, 1)),                                   # added: no birth date
            self.entry(born=date(2025, 6, 1), added=date(2025, 6, 2), breeding=True),       # breeding only
            self.entry(born=date(2025, 6, 1), added=date(2025, 6, 2)),                      # nothing in the period
            self.entry(protocol="", born=date(2026, 4, 1)),                                  # no protocol
            {"species": ar.FISH, "strain": "casper", "protocol": "P1", "count": 30, "born": date(2026, 5, 1),
             "used": 12},
            {"species": ar.FISH, "strain": "casper", "protocol": "p1 ", "count": 4, "ended": date(2026, 6, 1),
             "reason": "End of experiment or tissue collection"},
        ]
        out = ar.tally(entries, self.start, self.end)
        mice = next(r for r in out["rows"] if r["protocol"] == "P1" and r["species"] == ar.MOUSE)
        self.assertEqual((mice["born"], mice["added"], mice["new"], mice["used_in_experiments"],
                          mice["died_or_euthanized"], mice["breeding"]), (1, 2, 3, 1, 1, 1))
        self.assertEqual(mice["died_by_reason"], {"Humane endpoint or ill health": 1})
        # The rows keep each protocol as written; asking for one finds it however it was typed.
        only = ar.tally(entries, self.start, self.end, protocol="p1")
        self.assertEqual({r["protocol"] for r in only["rows"]}, {"P1", "p1 "})
        fish = sum(r["born"] for r in only["rows"] if r["species"] == ar.FISH)
        self.assertEqual(fish, 30)
        self.assertEqual(only["totals"]["used_in_experiments"], 1 + 12)
        self.assertEqual(only["totals"]["died_or_euthanized"], 1 + 4)
        # No protocol comes last, as None.
        self.assertIsNone(out["rows"][-1]["protocol"])
        self.assertEqual(out["protocols"][-1]["protocol"], None)
        self.assertEqual(out["protocols"][-1]["by_species"][ar.MOUSE]["born"], 1)

    def test_the_twelve_months_to_a_day(self):
        self.assertEqual(ar.year_before(date(2026, 10, 11)), date(2025, 10, 12))
        self.assertEqual(ar.year_before(date(2028, 2, 29)), date(2027, 3, 1))


# ---------------------------------------------------------------- over the API

class Reports(Base):
    def setUp(self):
        super().setUp()
        self.read = self.token(self.m, scope="read")

    def get(self, path, token=None):
        return self.call("get", path, token or self.read)

    def ok(self, r):
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:500])
        return r.get_json()


class AnimalUse(Reports):
    def setUp(self):
        super().setUp()
        self.kept = one("select value from app_settings where key='custom_fields:colony'")
        from app import custom_fields
        from app.db import SessionLocal
        with SessionLocal() as s:
            custom_fields.set_fields(s, "colony", [{"key": "iacuc", "label": "IACUC #"}])
            s.commit()

    def tearDown(self):
        if self.kept is None:
            execute("delete from app_settings where key='custom_fields:colony'")
        else:
            execute("update app_settings set value=? where key='custom_fields:colony'", self.kept)
        super().tearDown()

    def mouse(self, protocol, born, cage="", **fields):
        row = self.make_mouse(self.m, self.member, cage=cage or uniq("C"), litter=uniq("L"), date_of_birth=born,
                              **fields)
        if protocol:
            execute("update mice set attrs=? where id=?", json.dumps({"iacuc": protocol}), row)
        return row

    def test_mice_per_protocol_with_what_each_count_is_based_on(self):
        p = uniq("IACUC-")
        used = self.mouse(p, days_ago(30))
        old = self.mouse(p, days_ago(400))
        breeding = uniq("C")
        self.make_cage(self.m, breeding, purpose="Breeding")
        self.mouse(p, days_ago(200), cage=breeding)
        self.mouse("", days_ago(10))
        r = self.m.post("/colony/experiments/create", data={"name": uniq("Exp "), "start_date": days_ago(10)})
        exp = int(location(r).rsplit("/", 1)[1])
        self.m.post(f"/colony/experiments/{exp}/add-mouse", data={"mouse_row_id": used, "treatment_group": "A"})
        number = one("select mouse_id from mice where id=?", old)
        r = self.call("patch", f"/mice/{number}", self.token(self.m), json={
            "status": "sac", "date_of_death": days_ago(5), "note": "Hunched and lost weight: humane endpoint"})
        self.assertEqual(r.status_code, 200, r.get_json())

        out = self.ok(self.get(f"/animal-use?protocol={p}"))
        self.assertEqual(out["period"], {"start": ar.year_before(TODAY).isoformat(), "end": TODAY.isoformat()})
        self.assertEqual({row["protocol"] for row in out["rows"]}, {p})
        self.assertEqual({row["species"] for row in out["rows"]}, {ar.MOUSE})
        t = out["totals"]
        self.assertEqual((t["born"], t["added"], t["new"], t["used_in_experiments"], t["died_or_euthanized"],
                          t["breeding"]), (2, 1, 3, 1, 1, 1))
        [mice] = [r for r in out["protocols"] if r["protocol"] == p]
        self.assertEqual(mice["by_species"][ar.MOUSE]["died_or_euthanized"], 1)
        self.assertEqual({k: v for row in out["rows"] for k, v in row["died_by_reason"].items()},
                         {"Humane endpoint or ill health": 1})
        self.assertIn("“IACUC #”", out["basis"]["protocol"])
        for column in ar.COLUMNS:
            self.assertIn(column, out["basis"])
        self.assertIn("draft", out["note"])
        # The mouse with no protocol is among those the records can't place.
        [flag] = [f for f in out["flags"] if f["what"] == "no_protocol" and f["species"] == ar.MOUSE]
        self.assertGreaterEqual(flag["count"], 1)
        self.assertIn("may belong to this protocol", flag["says"])

    def test_a_period_before_the_records_were_made_counts_nothing(self):
        p = uniq("IACUC-")
        self.mouse(p, days_ago(30))
        out = self.ok(self.get(f"/animal-use?protocol={p}&end={days_ago(15)}"))
        self.assertEqual(out["rows"], [])
        # Born before a period that starts later: in it only as added to the records.
        out = self.ok(self.get(f"/animal-use?protocol={p}&start={days_ago(20)}"))
        self.assertEqual((out["totals"]["born"], out["totals"]["added"]), (0, 1))
        self.assertEqual(self.get("/animal-use?start=soon").status_code, 400)
        self.assertEqual(self.get(f"/animal-use?start={days_ago(1)}&end={days_ago(2)}").status_code, 400)

    def test_zebrafish_take_their_lines_protocol_and_the_sac_log(self):
        p = uniq("ZF-")
        line_name = uniq("casper")
        line = self.make_line(self.m, line_name, iacuc_protocol=p)
        tank = self.make_tank(self.m, line_id_fk=str(line), purpose="breeder")
        self.m.post("/zebrafish/fish/create", data={"tank_id_fk": str(tank), "count": "12",
                                                     "date_of_fertilization": days_ago(60)})
        fish = one("select max(id) from fish where tank_id_fk=?", tank)
        self.m.post("/zebrafish/sac/create", data={"fish_id_fk": str(fish), "count": "3", "sac_date": days_ago(2),
                                                    "reason": "end of experiment, tissue for RNA"})
        out = self.ok(self.get(f"/animal-use?protocol={p}"))
        [row] = out["rows"]
        self.assertEqual((row["species"], row["strain"]), (ar.FISH, line_name))
        self.assertEqual((row["born"], row["died_or_euthanized"], row["breeding"]), (12, 3, 9))
        self.assertEqual(row["died_by_reason"], {"End of experiment or tissue collection": 3})

    def test_pups_are_under_their_parents_protocol_with_or_without_a_record(self):
        p = uniq("IACUC-")
        mother = self.mouse(p, days_ago(400), gender="F")
        pup = self.mouse("", days_ago(20))
        litter = one("select litter_id_fk from mice where id=?", pup)
        execute("update litters set mother_info=?, total_pups=6 where id=?",
                f"#{one('select mouse_id from mice where id=?', mother)} ♀", litter)
        out = self.ok(self.get(f"/animal-use?protocol={p}"))
        self.assertEqual(out["totals"]["born"], 6)             # the pup with a record, and 5 counted by the litter
        self.assertGreaterEqual(out["litters"]["pups_without_records"], 5)
        self.assertTrue(any("their parents' protocol" in n for n in out["notes"]), out["notes"])
        self.assertTrue(any("mother's" in n for n in out["notes"]), out["notes"])

    def test_the_labs_own_organisms_are_counted_under_their_protocol(self):
        p = uniq("RAT-")
        key = self.make_organism_module(self.a)
        animal = self.make_animal(self.a, key)
        execute("update organisms set protocol=?, birth_on=? where id=?", p, date.fromisoformat(days_ago(30)), animal)
        label = one("select label from organism_modules where key=?", key)
        out = self.ok(self.get(f"/animal-use?protocol={p}"))
        [row] = out["rows"]
        self.assertEqual((row["species"], row["born"]), (label, 1))
        self.assertIn("own Protocol", out["basis"]["protocol"])
        self.assertFalse(any(n.startswith("Not counted") for n in out["notes"]))

    def test_anyone_in_the_lab_may_ask(self):
        self.ok(self.get("/animal-use", self.token(self.o, scope="read")))
        self.assertEqual(self.bare.get("/api/v1/animal-use").status_code, 401)


class Handover(Reports):
    def setUp(self):
        super().setUp()
        self.leaver = make_user(uniq("leaver"))
        self.c = client_for(self.leaver)
        self.mine = self.token(self.c, scope="read")

    def stock_up(self):
        """Something of each kind in the leaver's name."""
        rack = uniq("R")
        self.c.post("/colony/racks/save", data={"id": "", "name": rack, "rows": "3", "cols": "3"})
        rack_id = one("select id from mouse_racks where name=?", rack)
        cage = uniq("C")
        # Its litter, born 18 days ago (a cage's litters are cage_litters rows: Litter born makes one).
        self.make_cage(self.c, cage, purpose="Breeding", rack_id=str(rack_id), position="A1",
                       date_give_birth=days_ago(18))
        mice = [self.make_mouse(self.c, self.leaver, cage=cage) for _ in range(2)]
        # A mouse of theirs in someone else's cage.
        elsewhere = uniq("C")
        self.make_cage(self.o, elsewhere)
        self.make_mouse(self.o, self.leaver, cage=elsewhere)
        plasmid = self.make_plasmid(self.c, uniq("pLeave"))
        item = self.make_item(self.c, "reagents", uniq("Leaver's antibody"), location_note="Fridge 2, door")
        r = self.c.post("/colony/experiments/create", data={"name": uniq("Leaver exp "), "start_date": days_ago(1)})
        exp = int(location(r).rsplit("/", 1)[1])
        self.c.post(f"/colony/experiments/{exp}/steps/save",
                    json={"kind": "injection", "agent": "Tamoxifen", "dose": "20 mg/kg", "route": "i.p.", "days": "3"})
        page = self.c.post("/notebook/api/notes", data=json.dumps({"text": "Last notes before leaving"}),
                           content_type="application/json").get_json()["page_id"]
        return {"cage": cage, "rack": rack, "mice": mice, "elsewhere": elsewhere, "plasmid": plasmid, "item": item,
                "experiment": exp, "page": page}

    def test_everything_in_their_name_grouped_with_links_and_whats_due(self):
        made = self.stock_up()
        out = self.ok(self.get("/handover", self.mine))
        self.assertEqual(out["person"]["username"], self.leaver)
        cages = {c["cage"]: c for c in out["cages"]}
        cage = cages[made["cage"]]
        self.assertTrue(cage["yours"])
        self.assertEqual((cage["rack"], cage["position"]), (made["rack"], "A1"))
        self.assertEqual(sorted(cage["mice"]), sorted(one("select mouse_id from mice where id=?", m)
                                                     for m in made["mice"]))
        self.assertTrue(cage["url"].startswith("http://localhost/colony"))
        # Their mouse in someone else's cage is listed there, the cage not counted as theirs.
        self.assertFalse(cages[made["elsewhere"]]["yours"])
        self.assertEqual(len(cages[made["elsewhere"]]["mice"]), 1)
        [breeding] = out["breeding_cages"]
        self.assertEqual(breeding["cage"], made["cage"])
        self.assertTrue(any(d["title"].startswith("Wean") for d in breeding["due"]), breeding["due"])
        self.assertEqual([p["ref"]["id"] for p in out["plasmids"]], [made["plasmid"]])
        [reagents] = [i for i in out["inventories"] if i["key"] == "reagents"]
        [item] = reagents["items"]
        self.assertEqual((item["ref"]["id"], item["where"]), (made["item"], "Fridge 2, door"))
        [exp] = out["experiments"]
        self.assertEqual(exp["ref"], {"kind": "experiment", "id": made["experiment"]})
        self.assertEqual(exp["next_steps"][0]["date"], (TODAY + timedelta(days=1)).isoformat())
        self.assertIn("Tamoxifen", exp["next_steps"][0]["what"])
        self.assertEqual(out["notebook"]["unsigned"], 1)
        self.assertEqual(out["notebook"]["pages"][0]["ref"]["id"], made["page"])
        self.assertEqual(out["proposals"], {"waiting": 0, "items": []})
        self.assertIn(made["rack"], [r["name"] for r in out["racks_and_boxes"]])
        s = out["summary"]
        self.assertEqual((s["mice"], s["cages"], s["breeding_cages"], s["plasmids"], s["inventory_items"],
                          s["experiments"], s["unsigned_pages"]), (3, 1, 1, 1, 1, 1, 1))
        self.assertGreaterEqual(s["due"], 2)                          # the weaning and the injection
        self.assertTrue(all(d["owner"] == self.leaver for d in out["due"]))
        # How each kind is handed on, from the action catalogue.
        changes = out["owner_changes"]
        for kind, action in (("mouse", "mouse_change"), ("cage", "cage_change"), ("tank", "tank_change"),
                             ("plasmid", "plasmid_change"), ("inventory_item", "inventory_item_change"),
                             ("stock_unit", "stock_change")):
            self.assertEqual(changes.get(kind), action, kind)
        self.assertIn("only when the person asks", out["next"])

    def test_a_member_asks_about_themselves_an_admin_about_anyone(self):
        made = self.stock_up()
        r = self.get(f"/handover?username={self.leaver}")              # another member's token
        self.assertEqual(r.status_code, 403)
        self.assertIn("own handover", r.get_json()["error"])
        self.assertEqual(self.get(f"/handover?username={self.member}").status_code, 200)   # their own, by name
        admin = self.ok(self.get(f"/handover?username={self.leaver}", self.token(self.a, scope="read")))
        self.assertEqual(admin["person"]["username"], self.leaver)
        self.assertEqual(admin["asked_by"], self.admin)
        self.assertIn(made["cage"], [c["cage"] for c in admin["cages"]])
        # Their notebook stays theirs: counted, not named; their proposals likewise.
        self.assertEqual(admin["notebook"]["unsigned"], 1)
        self.assertEqual(admin["notebook"]["pages"], [])
        self.assertEqual(admin["notebook"]["not_named"], 1)
        self.assertNotIn("items", admin["proposals"])
        self.assertIn("private", " ".join(admin["notes"]))

    def test_their_own_database_is_theirs_and_an_admins_to_see(self):
        label = uniq("Mine")
        self.c.post("/inventory/new", data={"preset": "custom", "label": label, "audience": "me"})
        key = one("select key from inventory_modules where label=?", label)
        self.make_item(self.c, key, uniq("secret"))
        self.assertIn(key, [i["key"] for i in self.ok(self.get("/handover", self.mine))["inventories"]])
        admin = self.ok(self.get(f"/handover?username={self.leaver}", self.token(self.a, scope="read")))
        self.assertIn(key, [i["key"] for i in admin["inventories"]])

    def test_someone_with_nothing_has_an_empty_handover(self):
        out = self.ok(self.get("/handover", self.mine))
        self.assertEqual(out["summary"]["mice"], 0)
        self.assertEqual(out["cages"], [])
        nobody = self.ok(self.get(f"/handover?username={uniq('gone')}", self.token(self.a, scope="read")))
        self.assertFalse(nobody["person"]["has_account"])


# ---------------------------------------------------------------- the tools and prompts

class ToolsAndPrompts(Reports):
    def test_the_tools_read_and_the_local_server_registers_them(self):
        for name in ("animal_use", "handover"):
            self.assertIn(name, assistant_tools.TOOLS)
            self.assertIn(name, mcp_http.READ_ONLY)
            self.assertIn(name, mcp_http.TITLES)
        source = (ROOT / "mcp" / "biomanager_mcp.py").read_text(encoding="utf-8")
        self.assertEqual(set(re.findall(r'@server\.tool\(name="(\w+)"', source)), set(assistant_tools.TOOLS))
        self.assertEqual(set(re.findall(r'@server\.prompt\(name="(\w+)"', source)), set(assistant_tools.PROMPTS))

    def test_over_mcp_as_the_tokens_person(self):
        from tests.test_mcp import TestApi
        api = TestApi(self.token(self.m, scope="propose"))
        out = assistant_tools.call_tool(api, "handover", {})
        self.assertEqual(out["person"]["username"], self.member)
        refused = assistant_tools.call_tool(api, "handover", {"username": self.admin})
        self.assertIn("own handover", refused["error"])
        self.assertIn("totals", assistant_tools.call_tool(api, "animal_use", {"protocol": uniq("none-")}))

    def test_the_prompts_ask_for_a_draft_and_no_changes_unasked(self):
        report = assistant_tools.PROMPTS["animal_use_report"][2]({"protocol": "2024-0012"})
        self.assertIn("animal_use", report)
        self.assertIn("draft", report)
        self.assertIn("Never invent", report)
        self.assertIn("2024-0012", report)
        handover = assistant_tools.PROMPTS["handover_report"][2]({"person": "Ana"})
        self.assertIn("handover", handover)
        self.assertIn("only if I ask", handover)
        self.assertIn("Ana", handover)


if __name__ == "__main__":
    unittest.main()
