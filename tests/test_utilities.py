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


if __name__ == "__main__":
    unittest.main()
