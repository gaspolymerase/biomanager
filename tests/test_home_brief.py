"""Home's daily brief (app/home_layouts.daily_brief) and the ⌘K palette's words."""
from __future__ import annotations

from datetime import date, timedelta

from tests.base import AppTestCase, client_for, make_user  # isort: skip

from app import home_layouts  # noqa: E402

TODAY = date(2026, 10, 29)


def item(title, due, status):
    return {"title": title, "due": due, "status": status, "url": "/x"}


class BriefTests(AppTestCase):
    def test_today_first_three_by_name_then_how_many_more(self):
        agenda = [item(f"Job {n}", TODAY, "today") for n in range(5)] + [item("Late", TODAY - timedelta(days=2), "overdue")]
        brief = home_layouts.daily_brief(agenda, TODAY, animals=4)
        self.assertEqual([t["title"] for t in brief["today"]], ["Job 0", "Job 1", "Job 2"])
        self.assertEqual((brief["more"], brief["overdue"], brief["animals"], brief["quiet"]), (2, 1, 4, False))

    def test_a_quiet_day(self):
        brief = home_layouts.daily_brief([item("Next week", TODAY + timedelta(days=7), "later")], TODAY)
        self.assertTrue(brief["quiet"])
        self.assertEqual(brief["today"], [])

    def test_home_opens_with_it(self):
        html = self.get_ok(client_for(make_user()), "/home")
        self.assertIn('class="home-brief"', html)
        self.assertIn("Daily brief", html)
