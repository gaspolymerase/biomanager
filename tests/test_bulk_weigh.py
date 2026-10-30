"""Weigh, on the mouse sheet's selection bar (app.py bulk_weigh_mice).

Tick mice, press W: a weight box for each, one day for all. A mouse left
blank is not weighed, one that is not yours is skipped, and weighing again
on the same day replaces the weight, as weighing one mouse does."""
from __future__ import annotations

from tests.base import *  # noqa: F401,F403
from tests.base import AppTestCase, T, client_for, make_user, one, rows
from tests.test_mice import flash_texts


class BulkWeighTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.me = make_user()
        self.client = client_for(self.me)
        self.ids = [self.make_mouse(self.client, self.me) for _ in range(3)]

    def weights(self, mid):
        return [(str(d), g) for d, g in rows(
            "select weigh_date, grams from mouse_weights where mouse_id_fk=? order by weigh_date", mid)]

    def weigh(self, client, grams: dict, day: str = ""):
        data = {"selected_ids": list(grams), "weigh_date": day}
        data.update({f"grams_{mid}": g for mid, g in grams.items()})
        return client.post("/colony/mice/bulk-weigh", data=data)

    def test_each_ticked_mouse_gets_its_own_weight_on_one_day(self):
        a, b, c = self.ids
        self.weigh(self.client, {a: "21.5", b: "19,8", c: ""}, "2026-10-01")
        self.assertEqual(self.weights(a), [("2026-10-01", 21.5)])
        self.assertEqual(self.weights(b), [("2026-10-01", 19.8)])
        self.assertEqual(self.weights(c), [])
        self.assertIn("Weighed: 2 mice", flash_texts(self.client, "success"))

    def test_the_day_defaults_to_today_and_a_second_weight_replaces_the_first(self):
        a = self.ids[0]
        self.weigh(self.client, {a: "20"})
        self.weigh(self.client, {a: "22"})
        self.assertEqual(self.weights(a), [(T, 22.0)])

    def test_someone_elses_mouse_is_skipped(self):
        other = make_user()
        theirs = self.make_mouse(client_for(other), other)
        self.weigh(self.client, {self.ids[0]: "20", theirs: "25"})
        self.assertEqual(self.weights(theirs), [])
        self.assertEqual(len(self.weights(self.ids[0])), 1)

    def test_a_weight_that_is_not_a_number_weighs_nothing(self):
        self.weigh(self.client, {self.ids[0]: "20", self.ids[1]: "heavy"})
        self.assertEqual(one("select count(*) from mouse_weights where mouse_id_fk in (?, ?)", *self.ids[:2]), 0)
        self.assertIn("not a weight", flash_texts(self.client, "error"))

    def test_the_bar_offers_weigh_move_and_status_with_their_keys(self):
        html = self.get_ok(self.client, "/colony?view=mice")
        for key in ('data-key="M"', 'data-key="S"', 'data-key="W"', 'data-weigh-form', "bulk-weigh", "bulk-sac"):
            self.assertIn(key, html)
