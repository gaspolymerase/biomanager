"""Changing a mouse is one operation (colony.change_mouse) with one set of
rules, whether the change comes from a row of the mouse sheet, a dialog or
the API (and so an assistant): a date of birth or of death can't be in the
future, a mouse goes only to a lab member, and a cohort's date of birth
changes only once the person confirms."""
from __future__ import annotations

import unittest
from datetime import date, timedelta

from tests.base import AppTestCase, one
from tests.test_api import Base as ApiBase

TOMORROW = (date.today() + timedelta(days=1)).isoformat()


class FromTheSheet(AppTestCase):

    def test_a_mouse_goes_only_to_a_lab_member(self):
        mouse = self.make_mouse(self.m, self.member)
        r = self.autosave(self.m, f"/colony/mice/{mouse}/update", {"owner": "nobody-here", "owner_was": self.member})
        self.assertRefused(r)
        self.assertIn("is not a lab member", r.get_json()["error"])
        self.assertEqual(one("select owner from mice where id=?", mouse), self.member)

    def test_a_date_of_death_cant_be_in_the_future(self):
        mouse = self.make_mouse(self.m, self.member)
        r = self.autosave(self.m, f"/colony/mice/{mouse}/update", {"date_of_death": TOMORROW, "date_of_death_was": ""})
        self.assertRefused(r)
        self.assertIsNone(one("select date_of_death from mice where id=?", mouse))


class FromTheApi(ApiBase):

    def test_a_date_of_death_cant_be_in_the_future_there_either(self):
        mouse = self.make_mouse(self.m, self.member)
        tok = self.token(self.m)
        mouse_id = one("select mouse_id from mice where id=?", mouse)
        r = self.call("patch", f"/mice/{mouse_id}", tok, json={"date_of_death": TOMORROW})
        self.assertEqual(r.status_code, 400, r.get_json())
        self.assertIsNone(one("select date_of_death from mice where id=?", mouse))

    def test_a_refusal_is_said_as_the_sheet_says_it(self):
        mouse = self.make_mouse(self.m, self.member)
        tok = self.token(self.m)
        mouse_id = one("select mouse_id from mice where id=?", mouse)
        r = self.call("patch", f"/mice/{mouse_id}", tok, json={"owner": "nobody-here"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Pick a username from the list", r.get_data(as_text=True))

    def test_a_value_of_the_wrong_shape_is_refused_and_nothing_changes(self):
        mouse = self.make_mouse(self.m, self.member)
        tok = self.token(self.m)
        mouse_id = one("select mouse_id from mice where id=?", mouse)
        before = one("select gender || '|' || coalesce(note, '') from mice where id=?", mouse)
        for body, said in (({"colour": "brown"}, "A mouse has no colour to change"),
                           ({"sex": "X", "note": "x"}, "sex is F, M or empty."),
                           ({"transgenes": ["a", "b", "c", "d", "e"]}, "transgenes is a list of at most four."),
                           ({"date_of_death": "yesterday"}, "date_of_death is a date"),
                           ({"note": "x", "sex": "Q"}, "sex is F, M or empty.")):
            r = self.call("patch", f"/mice/{mouse_id}", tok, json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn(said, r.get_data(as_text=True))
        self.assertEqual(one("select gender || '|' || coalesce(note, '') from mice where id=?", mouse), before)

    def test_the_values_change_the_mouse(self):
        mouse = self.make_mouse(self.m, self.member)
        tok = self.token(self.m)
        mouse_id = one("select mouse_id from mice where id=?", mouse)
        r = self.call("patch", f"/mice/{mouse_id}", tok, json={"sex": "f", "genotype": "Cre/+; Ai14/+", "note": "API"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(one("select gender || '|' || transgene_1 || '|' || transgene_2 || '|' || note from mice where id=?",
                             mouse), "F|Cre/+|Ai14/+|API")


if __name__ == "__main__":
    unittest.main()
