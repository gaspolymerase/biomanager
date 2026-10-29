"""The calendar's Board: to-dos as cards in lists (app/task_board.py)."""
from __future__ import annotations

from tests.base import AppTestCase, client_for, days_ago, make_user, one  # isort: skip


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


class ADayWrittenInACard(AppTestCase):
    """A day written in a new card is its day on the calendar (when_in)."""

    def test_days_in_english_and_chinese(self):
        from datetime import date

        from app.task_board import when_in
        thursday = date(2026, 10, 29)
        for text, title, day in (
                ("Order primers Fri", "Order primers", date(2026, 10, 30)),
                ("Call the vendor tomorrow", "Call the vendor", date(2026, 10, 30)),
                ("Book the scope by Nov 8", "Book the scope", date(2026, 11, 8)),
                ("Renew IACUC on 2026-11-20", "Renew IACUC", date(2026, 11, 20)),
                ("Flip vials 11/3", "Flip vials", date(2026, 11, 3)),
                ("Perfuse cohort in 3 days", "Perfuse cohort", date(2026, 11, 1)),
                ("Sync next fri", "Sync", date(2026, 11, 6)),
                ("明天订抗体", "订抗体", date(2026, 10, 30)),
                ("周五交报告", "交报告", date(2026, 10, 30)),
                ("交报告 下周一", "交报告", date(2026, 11, 2)),
                ("11月8日开组会", "开组会", date(2026, 11, 8))):
            with self.subTest(text=text):
                self.assertEqual(when_in(text, thursday), (title, day))
        for text in ("Order 2 boxes", "Friday's prep", "Tomorrow", "周报"):
            with self.subTest(text=text):
                self.assertEqual(when_in(text, thursday), (text, None))

    def test_a_new_card_takes_its_day_unless_told_not_to(self):
        c = client_for(make_user())
        card = c.post("/calendar/board/cards", json={"title": "Order primers tomorrow"}).get_json()["card"]
        self.assertEqual(card["title"], "Order primers")
        self.assertTrue(card["due"])
        kept = c.post("/calendar/board/cards", json={"title": "Results from Sat", "read_day": False}).get_json()["card"]
        self.assertEqual((kept["title"], kept["due"]), ("Results from Sat", ""))
        picked = c.post("/calendar/board/cards", json={"title": "Order tips", "due": "2026-12-01"}).get_json()["card"]
        self.assertEqual(picked["due"], "2026-12-01")
        self.assertEqual(c.get("/calendar/board/when?text=Book+the+scope+Fri").get_json()["title"], "Book the scope")

    def test_inbox_has_the_calendars_work_for_the_week(self):
        self.make_mouse(self.a, self.admin, date_of_birth=days_ago(7 * 31))       # past 30 weeks
        inbox = self.a.get("/calendar/board").get_json()["columns"][0]
        self.assertEqual(inbox["kind"], "inbox")
        work = inbox["calendar"]
        self.assertTrue(any(item["track"] == "colony" for item in work), work)
        self.assertTrue(all(item["url"] and item["due"] and item["from"] for item in work))
