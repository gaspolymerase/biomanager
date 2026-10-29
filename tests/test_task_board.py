"""The calendar's Board: to-dos as cards in lists (app/task_board.py)."""
from __future__ import annotations

from tests.base import AppTestCase, client_for, make_user, one  # isort: skip


class BoardTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.me = make_user()
        self.c = client_for(self.me)

    def board(self, client=None):
        r = (client or self.c).get("/calendar/board")
        self.assertEqual(r.status_code, 200)
        return {col["name"]: [card["title"] for card in col["cards"]] for col in r.get_json()["columns"]}

    def add_list(self, name):
        r = self.c.post("/calendar/board/lists", json={"add": name})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def card(self, title, list_name=""):
        r = self.c.post("/calendar/board/cards", json={"title": title, "list": list_name})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return r.get_json()["card"]["id"]

    def move(self, card_id, client=None, **body):
        return (client or self.c).post(f"/calendar/board/cards/{card_id}", json=body)

    def test_inbox_lists_and_done(self):
        self.assertEqual(list(self.board()), ["", "__done__"])
        self.add_list("This week")
        first = self.card("Order antibody", "This week")
        second = self.card("Genotype L-22", "This week")
        inbox = self.card("Read paper")
        self.assertEqual(self.board()["This week"], ["Order antibody", "Genotype L-22"])
        self.assertIn("Read paper", self.board()[""])
        # Before another card, in another list.
        self.assertEqual(self.move(inbox, list="This week", before=first).status_code, 200)
        self.assertEqual(self.board()["This week"], ["Read paper", "Order antibody", "Genotype L-22"])
        # Into Done ticks it off; out again opens it.
        self.move(second, list="__done__")
        self.assertIn("Genotype L-22", self.board()["__done__"])
        self.assertIsNotNone(one("select done_at from tasks where id=?", second))
        self.move(second, list="")
        self.assertIsNone(one("select done_at from tasks where id=?", second))
        self.assertIn("Genotype L-22", self.board()[""])

    def test_a_card_in_a_list_with_no_day_stays_off_the_calendar(self):
        self.add_list("Someday")
        kept = self.card("Clean the freezer", "Someday")
        loose = self.card("Loose end")
        items = self.c.get("/calendar/events.json").get_json()["items"]
        keys = {item["id"] for item in items}
        self.assertNotIn(f"task-{kept}", keys)
        self.assertIn(f"task-{loose}", keys)
        # Given a day, it is on the calendar that day.
        self.move(kept, due="2026-11-20")
        self.assertEqual(str(one("select due_date from tasks where id=?", kept))[:10], "2026-11-20")
        items = self.c.get("/calendar/events.json?start=2026-11-01&end=2026-11-30").get_json()["items"]
        self.assertIn(f"task-{kept}", {item["id"] for item in items})
        self.move(kept, due="")
        self.assertIsNone(one("select due_date from tasks where id=?", kept))

    def test_renaming_and_deleting_a_list(self):
        self.add_list("Bench")
        card = self.card("Pour plates", "Bench")
        self.c.post("/calendar/board/lists", json={"rename": ["Bench", "Bench work"]})
        self.assertEqual(self.board()["Bench work"], ["Pour plates"])
        self.c.post("/calendar/board/lists", json={"remove": "Bench work"})
        self.assertNotIn("Bench work", self.board())
        self.assertEqual(one("select board_list from tasks where id=?", card), "")

    def test_lists_are_each_persons_own_and_cards_are_theirs(self):
        self.add_list("Mine")
        card = self.card("Private thing", "Mine")
        other = client_for(make_user())
        self.assertNotIn("Mine", self.board(other))
        self.assertEqual(self.move(card, client=other, list="").status_code, 403)

    def test_names_must_be_names(self):
        self.assertEqual(self.c.post("/calendar/board/lists", json={"add": "   "}).status_code, 400)
        self.assertEqual(self.c.post("/calendar/board/cards", json={"title": ""}).status_code, 400)
