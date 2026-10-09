"""Utilities: the page, and the lab's centrifuge rotors it saves for rpm ↔ × g.
The calculators' arithmetic is checked in Node (tests/js/bench-calcs.check.mjs)."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta

import tests.base  # noqa: F401  (before app: tests must not touch data/biomanager.db)
from tests.base import AppTestCase, client_for, execute, make_user, uniq


def rotors_on(html: str) -> list:
    return json.loads(html.split('id="util-rotors">', 1)[1].split("</script>", 1)[0])


class Rotors(AppTestCase):
    def setUp(self):
        self.member = client_for(make_user(uniq("rotor")))

    def save(self, client, rotors):
        return client.post("/utilities/rotors", json={"rotors": rotors})

    def test_a_member_saves_the_labs_rotors_and_everyone_sees_them(self):
        r = self.save(self.member, [{"name": "5424, FA-45-24-11", "radius": "8.4", "max": "15000"}])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["rotors"], [{"name": "5424, FA-45-24-11", "radius": 8.4, "max": 15000}])
        other = client_for(make_user(uniq("other")))
        page = other.get("/utilities").get_data(as_text=True)
        self.assertEqual(rotors_on(page)[0]["name"], "5424, FA-45-24-11")
        self.assertIn('data-rotors-editable="1"', page)

    def test_a_rotor_without_a_name_or_with_an_odd_radius_is_refused(self):
        self.assertEqual(self.save(self.member, [{"name": "", "radius": 8}]).status_code, 400)
        self.assertEqual(self.save(self.member, [{"name": "Big", "radius": 900}]).status_code, 400)
        self.assertEqual(self.save(self.member, [{"name": "x", "radius": 5}] * 41).status_code, 400)
        self.assertEqual(self.member.post("/utilities/rotors", json={"rotors": "nope"}).status_code, 400)

    def test_a_guest_sees_them_but_cannot_change_them(self):
        name = make_user(uniq("guest"))
        execute("update users set expires_at=? where username=?", datetime.now() + timedelta(days=7), name)
        guest = client_for(name)
        self.assertIn('data-rotors-editable="0"', guest.get("/utilities").get_data(as_text=True))
        self.assertEqual(self.save(guest, [{"name": "Mine", "radius": 9}]).status_code, 403)


GLYCEROL = {"name": "Glycerol stock", "does": "Glycerol to add for a frozen stock.",
            "inputs": [{"name": "culture", "label": "Culture", "unit": "µL", "value": "500"},
                       {"name": "want", "label": "Wanted", "unit": "%", "value": "15"},
                       {"name": "stock", "label": "Stock", "unit": "%", "value": "50"}],
            "outputs": [{"label": "Glycerol to add", "formula": "culture * want / (stock - want)", "unit": "µL", "name": "add"}]}


def lab_tools_on(html: str) -> list:
    return json.loads(html.split('id="util-lab-tools">', 1)[1].split("</script>", 1)[0])


class LabTools(AppTestCase):
    """The lab's own tools: anyone but a guest makes one; its maker or an
    admin changes or deletes it; everyone sees it."""

    def setUp(self):
        self.maker_name = make_user(uniq("maker"))
        self.maker = client_for(self.maker_name)

    def save(self, client, tool):
        return client.post("/utilities/lab-tools", json={"tool": tool})

    def test_a_tool_made_by_one_member_is_on_everyone_s_page(self):
        r = self.save(self.maker, GLYCEROL)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        made = next(t for t in r.get_json()["tools"] if t["id"] == r.get_json()["id"])
        self.assertEqual((made["author"], made["outputs"][0]["formula"]), (self.maker_name, "culture * want / (stock - want)"))
        page = client_for(make_user(uniq("reader"))).get("/utilities").get_data(as_text=True)
        self.assertIn(made["id"], [t["id"] for t in lab_tools_on(page)])

    def test_only_its_maker_or_an_admin_changes_or_deletes_it(self):
        tool_id = self.save(self.maker, GLYCEROL).get_json()["id"]
        other = client_for(make_user(uniq("other")))
        self.assertEqual(self.save(other, {**GLYCEROL, "id": tool_id, "name": "Mine now"}).status_code, 403)
        self.assertEqual(other.post(f"/utilities/lab-tools/{tool_id}/delete").status_code, 403)
        r = self.save(self.maker, {**GLYCEROL, "id": tool_id, "name": "Glycerol stock, 15 %"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Glycerol stock, 15 %", [t["name"] for t in r.get_json()["tools"]])
        admin = client_for(make_user(uniq("admin"), role="admin"))
        self.assertEqual(admin.post(f"/utilities/lab-tools/{tool_id}/delete").status_code, 200)
        self.assertNotIn(tool_id, [t["id"] for t in lab_tools_on(self.maker.get("/utilities").get_data(as_text=True))])
        self.assertEqual(self.save(self.maker, {**GLYCEROL, "id": tool_id}).status_code, 404)

    def test_a_guest_uses_them_but_does_not_make_them(self):
        name = make_user(uniq("guest"))
        execute("update users set expires_at=? where username=?", datetime.now() + timedelta(days=7), name)
        self.assertEqual(self.save(client_for(name), GLYCEROL).status_code, 403)

    def test_a_tool_must_have_a_name_inputs_answers_and_plain_formulas(self):
        for broken in ({**GLYCEROL, "name": ""}, {**GLYCEROL, "inputs": []}, {**GLYCEROL, "outputs": []},
                       {**GLYCEROL, "inputs": [{"name": "1st", "label": "x"}]},
                       {**GLYCEROL, "outputs": [{"label": "x", "formula": "culture; fetch('/x')"}]},
                       {**GLYCEROL, "outputs": [{"label": "x", "formula": "culture", "name": "has space"}]}, "nope"):
            self.assertEqual(self.save(self.maker, broken).status_code, 400, broken)


if __name__ == "__main__":
    unittest.main()
