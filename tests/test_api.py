"""The public API (app/api.py): tokens that act as their person, lists by
page, and changes through the same checks as the pages."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

# tests.base first: it points the app at a throwaway database before app is imported.
from tests.base import AppTestCase, app, execute, one, uniq
from app import api  # noqa: E402

TODAY = date.today()


class Base(AppTestCase):
    def setUp(self):
        super().setUp()
        api.rate.reset()
        execute("delete from app_settings where key='members_api_tokens'")  # another module may have left it off
        self.bare = app.test_client()             # no session: the API is signed in by its token alone

    def tearDown(self):
        execute("delete from app_settings where key='members_api_tokens'")
        super().tearDown()

    def token(self, client, scope="write", label=None, expires="90"):
        r = client.post("/settings/api-tokens", data={"label": label or uniq("Script "), "scope": scope, "expires": expires})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        return re.search(r'id="api-token"[^>]*>(bmt_\w+)<', r.get_data(as_text=True)).group(1)

    def call(self, method, path, token, **kw):
        headers = {"Authorization": f"Bearer {token}", **kw.pop("headers", {})}
        return getattr(self.bare, method)(f"/api/v1{path}", headers=headers, **kw)

    def mouse_id(self, row_id):
        return one("select mouse_id from mice where id=?", row_id)


class Tokens(Base):
    def test_a_token_is_its_person_and_nothing_else_signs_in(self):
        tok = self.token(self.m)
        r = self.call("get", "/me", tok)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["username"], self.member)
        self.assertNotIn("Set-Cookie", r.headers)
        self.assertEqual(self.bare.get("/api/v1/me").status_code, 401)
        self.assertEqual(self.m.get("/api/v1/me").status_code, 401)            # a session cookie is not enough
        r = self.m.get("/api/v1/me", headers={"Authorization": f"Bearer {tok}"})    # token and cookie
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("Set-Cookie", r.headers)                             # the cookie isn't refreshed
        self.assertEqual(self.call("get", "/me", tok[:-2] + "xx").status_code, 401)
        self.assertEqual(one("select token_hash from api_tokens where token_hash=?", api.token_hash(tok)),
                         api.token_hash(tok))                                     # only the hash is kept
        self.assertIsNone(one("select id from api_tokens where token_hash=?", tok))

    def test_read_tokens_only_read(self):
        tok = self.token(self.m, scope="read")
        row = self.make_mouse(self.m, self.member, cage=uniq("C"))
        r = self.call("post", f"/mice/{self.mouse_id(row)}/weights", tok, json={"grams": 20})
        self.assertEqual(r.status_code, 403)
        self.assertIn("only read", r.get_json()["error"])

    def test_revoked_expired_and_switched_off(self):
        tok = self.token(self.m)
        token_id = one("select id from api_tokens where token_hash=?", api.token_hash(tok))
        self.a.post(f"/settings/api-tokens/{token_id}/revoke")                   # an admin may revoke anyone's
        self.assertEqual(self.call("get", "/me", tok).status_code, 401)
        tok = self.token(self.m)
        execute("update api_tokens set expires_at=? where token_hash=?", datetime.utcnow() - timedelta(minutes=1),
                api.token_hash(tok))
        self.assertEqual(self.call("get", "/me", tok).status_code, 401)
        tok, admin_tok = self.token(self.m), self.token(self.a)
        from app.db import SessionLocal
        from app.inventory_service import set_setting
        with SessionLocal() as s:
            set_setting(s, "members_api_tokens", "off")
            s.commit()
        self.assertEqual(self.call("get", "/me", tok).status_code, 401)          # members' tokens stop
        self.assertEqual(self.call("get", "/me", admin_tok).status_code, 200)    # an admin's still work
        self.assertEqual(self.m.post("/settings/api-tokens", data={"label": "x"}).status_code, 403)

    def test_a_token_from_another_site_is_still_just_a_token(self):
        tok = self.token(self.m)
        row = self.make_mouse(self.m, self.member, cage=uniq("C"))
        r = self.call("post", f"/mice/{self.mouse_id(row)}/weights", tok, json={"grams": 21.5},
                      headers={"Origin": "https://elsewhere.example"})
        self.assertEqual(r.status_code, 201)
        r = self.m.post(f"/api/v1/mice/{self.mouse_id(row)}/weights", json={"grams": 30},
                        headers={"Origin": "https://elsewhere.example"})
        self.assertEqual(r.status_code, 401)                                      # the cookie alone does nothing

    def test_the_reference_and_openapi(self):
        html = self.get_ok(self.m, "/api")
        self.assertIn("/api/v1/mice/{mouse_id}/weights", html)
        tok = self.token(self.m, scope="read")
        spec = self.call("get", "/openapi.json", tok).get_json()
        self.assertEqual(spec["openapi"], "3.0.3")
        self.assertIn("post", spec["paths"]["/mice/{mouse_id}/weights"])
        self.assertIn("API tokens", self.get_ok(self.m, "/settings"))
        self.assertEqual(self.call("get", "/nothing-here", tok).status_code, 404)

    def test_the_reference_lists_every_endpoint_served(self):
        # Scripts are written from /api: nothing served may be missing from it, nothing in it may be gone.
        served = set()
        for rule in app.url_map.iter_rules():
            if rule.rule.startswith("/api/v1") and "_rest" not in rule.rule and rule.rule != "/api/v1/openapi.json":
                path = re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", rule.rule).rstrip("/") or "/api/v1"
                served |= {(m, path) for m in rule.methods - {"HEAD", "OPTIONS"}}
        self.assertEqual(served, {(method, path) for method, path, *_ in api.ENDPOINTS})


class Colony(Base):
    def test_mice_by_page(self):
        tok = self.token(self.m, scope="read")
        cage = uniq("C")
        rows = [self.make_mouse(self.m, self.member, cage=cage) for _ in range(3)]
        first = self.call("get", f"/mice?cage={cage}&limit=2", tok).get_json()
        self.assertEqual(len(first["data"]), 2)
        self.assertTrue(first["next"])
        rest = self.bare.get(first["next"], headers={"Authorization": f"Bearer {tok}"}).get_json()
        self.assertEqual([m["mouse_id"] for m in first["data"] + rest["data"]], [self.mouse_id(r) for r in rows])
        self.assertIsNone(rest["next"])
        one_mouse = self.call("get", f"/mice/{self.mouse_id(rows[0])}", tok).get_json()
        self.assertEqual((one_mouse["cage"], one_mouse["alive"]), (cage, True))
        self.assertEqual(self.call("get", f"/cages/{cage}", tok).get_json()["mice"], [self.mouse_id(r) for r in rows])

    def test_weights_and_changes_go_through_the_same_rules(self):
        tok = self.token(self.m, label="Balance in B12")
        mine = self.mouse_id(self.make_mouse(self.m, self.member, cage=uniq("C")))
        r = self.call("post", f"/mice/{mine}/weights", tok, json={"grams": 22.4, "date": TODAY.isoformat()})
        self.assertEqual(r.status_code, 201)
        r = self.call("post", f"/mice/{mine}/weights", tok, json={"grams": 22.9})    # the same day again replaces it
        self.assertEqual(r.status_code, 200)
        self.assertEqual([w["grams"] for w in self.call("get", f"/mice/{mine}/weights", tok).get_json()["data"]], [22.9])
        self.assertEqual(self.call("post", f"/mice/{mine}/weights", tok,
                                   json={"grams": 20, "date": (TODAY + timedelta(days=2)).isoformat()}).status_code, 400)
        new_cage = uniq("C")
        r = self.call("patch", f"/mice/{mine}", tok, json={"note": "HDM cohort", "cage": new_cage, "genotype": "Ai14/+"})
        self.assertEqual(r.status_code, 200, r.get_json())
        body = r.get_json()
        self.assertEqual((body["note"], body["cage"], body["genotype"], body["sex"]), ("HDM cohort", new_cage, "Ai14/+", "F"))
        self.assertIn("[API: Balance in B12]", one(
            "select details from audit_log where table_name='mice' and changed_by=? order by id desc limit 1", self.member))
        self.assertEqual(self.call("patch", f"/mice/{mine}", tok, json={"colour": "black"}).status_code, 400)
        theirs = self.mouse_id(self.make_mouse(self.a, self.admin, cage=uniq("C")))
        r = self.call("patch", f"/mice/{theirs}", tok, json={"note": "mine now"})
        self.assertEqual(r.status_code, 403)                                      # not the member's to change
        self.assertEqual(self.call("post", f"/mice/{theirs}/weights", tok, json={"grams": 20}).status_code, 403)


class Stocks(Base):
    def test_vials_and_tubes(self):
        tok = self.token(self.m)
        key = self.make_stock_module(self.m)
        vial = self.make_vial(self.m, key, genotype="w; UAS-GFP", purpose="cross")
        number = one("select number from stock_units where id=?", vial)
        units = self.call("get", f"/stocks/{key}/units?genotype=UAS-GFP", tok).get_json()["data"]
        self.assertEqual([u["number"] for u in units], [number])
        r = self.call("patch", f"/stocks/{key}/units/{number}", tok, json={"genotype": "w; UAS-mCherry"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(one("select genotype from stock_units where id=?", vial), "w; UAS-mCherry")

        name = uniq("Liver ")
        r = self.call("post", "/inventories/reagents/items", tok, json={"name": name, "lot": "L-9", "quantity": "5"})
        self.assertEqual(r.status_code, 201, r.get_json())
        number = r.get_json()["number"]
        self.assertEqual(r.get_json()["owner"], self.member)
        r = self.call("patch", f"/inventories/reagents/items/{number}", tok, json={"status": "empty"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["status"], "empty")
        self.assertEqual(self.call("patch", f"/inventories/reagents/items/{number}", tok,
                                   json={"status": "on the moon"}).status_code, 400)
        listed = self.call("get", "/inventories", tok).get_json()["data"]
        self.assertIn("reagents", [m["key"] for m in listed])


class Experiments(Base):
    def test_readings_from_a_script(self):
        tok = self.token(self.a)
        tank = self.make_tank(self.a)
        self.a.post("/zebrafish/fish/create", data={"tank_id_fk": str(tank), "count": "12"})
        r = self.a.post("/experiments/in/zebrafish/create", data={
            "name": uniq("Exp "), "readout": "", "start_date": (TODAY - timedelta(days=2)).isoformat()})
        exp = int(r.headers["Location"].rsplit("/", 1)[1])
        subjects = self.a.post(f"/experiments/{exp}/subjects/add", json={"how": "group", "value": str(tank),
                                                                         "group": "Tricaine"}).get_json()["subjects"]
        key = subjects[0]["key"]
        r = self.call("post", f"/experiments/{exp}/readings", tok, json={"values": {key: 9}})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["saved"], 1)
        page = self.call("get", f"/experiments/{exp}", tok).get_json()
        row = next(x for x in page["page"]["table"]["rows"] if x["key"] == key)
        self.assertEqual(row["values"], [9.0])
        self.assertIn(exp, [e["id"] for e in self.call("get", "/experiments?database=zebrafish", tok).get_json()["data"]])
        bad = self.call("post", f"/experiments/{exp}/readings", tok, json={"values": {key: 13}})
        self.assertEqual(bad.status_code, 400)
