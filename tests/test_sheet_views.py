"""A sheet's saved views and who is here (app/sheet_views.py, static/sheet-views.js)."""
from __future__ import annotations

from tests.base import AppTestCase, client_for, make_user  # isort: skip

from app import sheet_views  # noqa: E402


class SavedViewTests(AppTestCase):
    def test_a_view_is_kept_for_its_person_only(self):
        me, other = client_for(make_user()), client_for(make_user())
        views = [{"name": "My breeders", "state": {"query": "cre", "chip": "active:true",
                                                    "fields": [{"key": "gender", "value": "F", "label": "Sex"}],
                                                    "sort": ["age", -1], "hidden": ["note"]}}]
        r = me.post("/sheet-views/colony-mice", json={"views": views})
        self.assertEqual(r.status_code, 200)
        got = me.get("/sheet-views/colony-mice").get_json()["views"]
        self.assertEqual(got[0]["name"], "My breeders")
        self.assertEqual(got[0]["state"]["fields"][0]["value"], "F")
        self.assertEqual(got[0]["state"]["sort"], ["age", -1])
        self.assertEqual(other.get("/sheet-views/colony-mice").get_json()["views"], [])
        self.assertEqual(me.get("/sheet-views/colony-cages").get_json()["views"], [])

    def test_what_is_stored_is_only_a_view(self):
        kept = sheet_views.clean([{"name": "  x  ", "state": {"query": 5, "evil": "<script>", "sort": ["a", 7]}},
                                  {"name": ""}, "junk", {"name": "y"}] + [{"name": f"v{i}"} for i in range(40)])
        self.assertEqual(kept[0], {"name": "x", "state": {"query": "5", "chip": "", "fields": [], "sort": ["a", 1]}})
        self.assertEqual(kept[1]["name"], "y")
        self.assertLessEqual(len(kept), sheet_views.MAX_VIEWS)

    def test_an_odd_sheet_name_is_refused(self):
        me = client_for(make_user())
        self.assertEqual(me.get("/sheet-views/" + "x" * 40).status_code, 404)

    def test_signed_out_gets_nothing(self):
        from app.app import app
        self.assertEqual(app.test_client().get("/sheet-views/colony-mice").status_code, 401)


class PresenceTests(AppTestCase):
    def test_two_people_on_a_sheet_see_each_other_until_one_leaves(self):
        a_name, b_name = make_user(), make_user()
        a, b = client_for(a_name), client_for(b_name)
        here = {"path": "/colony", "view": "mice"}
        self.assertEqual(a.post("/presence", json=here).get_json()["here"], [])
        seen = b.post("/presence", json=here).get_json()["here"]
        self.assertEqual([p["username"] for p in seen], [a_name])
        self.assertEqual([p["username"] for p in a.post("/presence", json=here).get_json()["here"]], [b_name])
        # Another tab of the sheet is another page.
        self.assertEqual(a.post("/presence", json={"path": "/colony", "view": "cages"}).get_json()["here"], [])
        b.post("/presence", json={**here, "left": 1})
        self.assertEqual(a.post("/presence", json=here).get_json()["here"], [])

    def test_an_address_must_look_like_one(self):
        me = client_for(make_user())
        self.assertEqual(me.post("/presence", json={"path": "javascript:alert(1)"}).status_code, 400)
