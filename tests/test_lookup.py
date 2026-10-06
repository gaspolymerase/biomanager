"""/api/v1/resolve, /vocabulary, /due and /actions (app/lookup.py): what an
assistant reads before proposing, as the token's person."""
from __future__ import annotations

from tests.base import days_ago, one, uniq
from tests.test_api import Base

from app import actions
from app.app import app


class Resolve(Base):
    def test_a_cage_by_its_number_with_its_reference_first(self):
        tok = self.token(self.m)
        code = uniq("88")
        cage = self.make_cage(self.m, code, purpose="Breeder")
        self.make_cage(self.m, code + "0")
        r = self.call("get", f"/resolve?q=cage {code}", tok)
        self.assertEqual(r.status_code, 200, r.get_json())
        data = r.get_json()["data"]
        self.assertEqual(data[0]["ref"], {"kind": "cage", "id": cage})
        self.assertTrue(data[0]["exact"])
        self.assertIn("Breeder", data[0]["label"])
        self.assertTrue(all(d["kind"] == "cage" for d in data))   # "cage …" looks only at cages
        self.assertEqual(len(data), 2)

    def test_a_mouse_by_its_number_and_a_reference_that_proposals_take(self):
        tok = self.token(self.m)
        row = self.make_mouse(self.m, self.member, cage=uniq("C"))
        number = self.mouse_id(row)
        [hit] = [d for d in self.call("get", f"/resolve?q=%23{number}&kinds=mouse", tok).get_json()["data"]]
        self.assertEqual(hit["ref"], {"kind": "mouse", "id": row})
        out = actions.run(app, self.member, [{"action": "note_add", "target": hit["ref"], "fields": {"text": "ok"}}])
        self.assertTrue(out.ok, out.errors)

    def test_vials_tanks_and_organism_records(self):
        tok = self.token(self.m)
        key, _inc, _rack = self.make_fly_setup(self.m)
        vial = self.make_vial(self.m, key, genotype="w; Sp/CyO")
        from tests.test_actions import code_of
        code = code_of(vial)
        hits = self.call("get", f"/resolve?q={code}", tok).get_json()["data"]
        self.assertIn({"kind": "stock_unit", "id": vial}, [h["ref"] for h in hits])
        hits = self.call("get", "/resolve?q=Sp/CyO&kinds=stock_unit", tok).get_json()["data"]
        self.assertIn(vial, [h["id"] for h in hits])
        tank = self.make_tank(self.m)
        tank_code = one("select tank_id from tanks where id=?", tank)
        self.assertEqual(self.call("get", f"/resolve?q={tank_code}&kinds=tank", tok).get_json()["data"][0]["id"], tank)
        org = self.make_organism_module(self.m)
        animal = self.make_animal(self.m, org, code=uniq("NW"))
        animal_code = one("select code from organisms where id=?", animal)
        hits = self.call("get", f"/resolve?q={animal_code}", tok).get_json()["data"]
        self.assertIn({"kind": "org_animal", "id": animal}, [h["ref"] for h in hits])

    def test_asks_for_something_to_look_for(self):
        tok = self.token(self.m)
        self.assertEqual(self.call("get", "/resolve", tok).status_code, 400)
        r = self.call("get", "/resolve?q=1&kinds=spaceship", tok)
        self.assertEqual(r.status_code, 400)
        self.assertIn("spaceship", r.get_json()["error"])


class Vocabulary(Base):
    def test_the_labs_own_words(self):
        tok = self.token(self.m, scope="read")
        self.make_mouse(self.m, self.member, cage=uniq("C"), transgene_1="Ai14")
        key = self.make_stock_module(self.m, "fly")
        org = self.make_organism_module(self.m)
        v = self.call("get", "/vocabulary", tok).get_json()
        self.assertIn("sac", v["mouse_colony"]["statuses"])
        self.assertIn("Ai14", v["mouse_colony"]["transgenes_in_use"])
        self.assertIn(self.member, v["people"])
        keys = {d["key"]: d for d in v["databases"]}
        self.assertIn("stock", keys[key]["purposes"])
        self.assertTrue(keys[key]["code_prefix"])
        self.assertIn("alive", keys[org]["statuses"])
        self.assertEqual(v["today"], days_ago(0))


class Due(Base):
    def test_a_weaning_due_comes_with_the_change_that_records_it(self):
        tok = self.token(self.m, scope="read")
        cage = self.make_cage(self.m, date_give_birth=days_ago(21))
        r = self.call("get", "/due?days=3", tok)
        self.assertEqual(r.status_code, 200, r.get_json())
        [item] = [d for d in r.get_json()["data"] if d["ref"] == {"kind": "cage", "id": cage}
                  and d["propose"]]
        self.assertEqual(item["propose"], {"action": "wean", "target": {"kind": "cage", "id": cage}, "fields": {}})
        self.assertTrue(item["url"].startswith("http"))
        out = actions.run(app, self.member, [item["propose"]])
        self.assertTrue(out.ok, out.errors)

    def test_a_flip_due_marks_the_rack(self):
        tok = self.token(self.m, scope="read")
        key, _inc, rack = self.make_fly_setup(self.m)
        from tests.base import execute
        self.make_vial(self.m, key, rack_id=rack)
        execute("update stock_racks set last_flipped_on=? where id=?", days_ago(60), rack)
        items = [d for d in self.call("get", "/due", tok).get_json()["data"]
                 if d["ref"] == {"kind": "stock_rack", "id": rack}]
        self.assertTrue(items)
        self.assertEqual(items[0]["propose"]["action"], "rack_flipped")


class Actions(Base):
    def test_the_catalogue_and_the_reference_list_it(self):
        tok = self.token(self.m, scope="read")
        r = self.call("get", "/actions", tok).get_json()
        names = {a["name"] for a in r["data"]}
        self.assertTrue({"litter_born", "wean", "tank_new", "stock_event", "org_due_done"} <= names)
        self.assertIn("cage", r["kinds"])
        spec = self.call("get", "/openapi.json", tok).get_json()
        self.assertIn("/resolve", spec["paths"])
        self.assertNotIn("limit", [p["name"] for p in spec["paths"]["/due"]["get"].get("parameters", [])])
