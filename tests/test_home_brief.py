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


def tracked(title, due, status, track):
    return {**item(title, due, status), "track": track}


class OverviewBentoTests(AppTestCase):
    """Overview's This week and Needs a look (home_layouts.week_view, needs_look)."""

    def week(self, agenda, todos=None):
        from app.app import app
        with app.test_request_context():
            return home_layouts.week_view(agenda, {"calendar": ("Calendar", "Events", "/calendar"),
                                                   "colony:wean": ("Mouse colony", "Weaning", "/colony")},
                                          TODAY, todos)

    def test_the_week_runs_monday_to_sunday_with_a_dot_for_each_kind_of_work(self):
        week = self.week([tracked("Talk", TODAY, "today", "calendar"),
                          tracked("Wean 110", TODAY + timedelta(days=1), "soon", "colony:wean"),
                          tracked("Wean 101", TODAY - timedelta(days=3), "overdue", "colony:wean")])
        days = week["days"]
        self.assertEqual([d["date"].weekday() for d in days], list(range(7)))
        today = next(d for d in days if d["today"])
        self.assertEqual(today["dots"], ["event", "colony"])         # the overdue weaning shows on today
        self.assertEqual(next(d for d in days if d["date"] == TODAY + timedelta(days=1))["dots"], ["colony"])

    def test_today_lists_todays_work_first_then_what_is_overdue_and_due_to_dos(self):
        week = self.week([tracked("Wean 101", TODAY - timedelta(days=3), "overdue", "colony:wean"),
                          tracked("Talk", TODAY, "today", "calendar"),
                          tracked("Later", TODAY + timedelta(days=2), "soon", "calendar")],
                         todos=[{"title": "Order primers", "due": TODAY, "overdue": False}])
        self.assertEqual([r["title"] for r in week["today"]], ["Talk", "Order primers", "Wean 101"])
        self.assertEqual([r["kind"] for r in week["today"]], ["Calendar", "To-do", "Mouse colony"])

    def test_needs_a_look_gives_each_animal_a_short_tag(self):
        view = {"groups": [
            {"id": "weight_loss", "rows": [{"mouse": 20, "loss_pct": 12.5, "url": "/m/20"}]},
            {"id": "not_weighed", "rows": [{"mouse": 6, "experiment": "Photometry", "days": 17}]}]}
        rows = home_layouts.needs_look(view)
        self.assertEqual([(r["id"], r["tag"]) for r in rows], [("#20", "−12.5%"), ("#6", "17 d")])
        self.assertTrue(rows[0]["high"])
        self.assertEqual(home_layouts.needs_look(None), [])

    def test_overview_draws_the_bento(self):
        html = self.get_ok(client_for(make_user()), "/home")
        for part in ('aria-label="This week"', 'class="home-week"', 'aria-label="Your databases"',
                     'aria-label="Notebook"'):
            self.assertIn(part, html)
