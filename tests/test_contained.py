"""app/contained.py: the pages' own code, run on someone's behalf inside one
transaction that is rolled back (a preview) or kept (an approval)."""
from __future__ import annotations

import unittest

from tests.base import *  # noqa: F401,F403
from tests.base import AppTestCase, count, days_ago, iso, one, uniq

from app import contained
from app.app import app
from app.db import SessionLocal
from app.models import CageRecord


class AContainedTransaction(AppTestCase):
    def test_a_preview_sees_the_change_and_leaves_nothing_behind(self):
        cage = self.make_cage(self.m)
        with contained.transaction():
            result = contained.run(app, self.member, "POST", f"/colony/cages/{cage}/give-birth",
                                   data={"date_give_birth": days_ago(1)})
            self.assertTrue(result.ok, result.messages)
            with SessionLocal() as s:  # joins the same transaction
                self.assertEqual(iso(s.get(CageRecord, cage).date_give_birth), days_ago(1))
        self.assertIsNone(one("select date_give_birth from mouse_cages where id=?", cage))

    def test_committing_keeps_it(self):
        cage = self.make_cage(self.m)
        with contained.transaction() as tx:
            self.assertTrue(contained.run(app, self.member, "POST", f"/colony/cages/{cage}/give-birth",
                                          data={"date_give_birth": days_ago(2)}).ok)
            tx.commit()
        self.assertEqual(str(one("select date_give_birth from mouse_cages where id=?", cage))[:10], days_ago(2))

    def test_many_rows_and_a_batch_roll_back_together(self):
        cage = self.make_cage(self.m)
        mice, litters, batches = count("mice"), count("litters"), count("batches")
        with contained.transaction():
            self.assertTrue(contained.run(app, self.member, "POST", f"/colony/cages/{cage}/give-birth",
                                          data={"date_give_birth": days_ago(25)}).ok)
            made = contained.run(app, self.member, "POST", f"/colony/cages/{cage}/genotyping",
                                 data={"total_pups": "5", "father_info": "", "mother_info": ""})
            self.assertTrue(made.ok, made.messages)
            weaned = contained.run(app, self.member, "POST", f"/colony/cages/{cage}/wean", data={})
            self.assertTrue(weaned.ok, weaned.messages)
            with SessionLocal() as s:
                from app.models import MouseRecord
                from sqlalchemy import func, select
                self.assertEqual(s.scalar(select(func.count()).select_from(MouseRecord)), mice + 5)
        self.assertEqual((count("mice"), count("litters"), count("batches")), (mice, litters, batches))

    def test_the_page_refuses_what_the_person_could_not_do(self):
        cage = self.make_cage(self.m)
        result = None
        with contained.transaction():
            result = contained.run(app, self.other, "POST", f"/colony/cages/{cage}/give-birth",
                                   data={"date_give_birth": days_ago(1)})
        self.assertFalse(result.ok)
        self.assertIsNone(one("select date_give_birth from mouse_cages where id=?", cage))

    def test_the_page_own_message_explains_a_refusal(self):
        cage = self.make_cage(self.m)
        with contained.transaction():
            result = contained.run(app, self.member, "POST", f"/colony/cages/{cage}/genotyping",
                                   data={"total_pups": "zero"})
        self.assertFalse(result.ok)
        self.assertTrue(any("Pups must be a whole number" in e for e in result.errors), result.messages)

    def test_an_unknown_person_is_not_signed_in(self):
        cage = self.make_cage(self.m)
        with contained.transaction():
            result = contained.run(app, uniq("nobody"), "POST", f"/colony/cages/{cage}/give-birth", data={})
        self.assertFalse(result.ok)

    def test_ordinary_requests_are_untouched(self):
        cage = self.make_cage(self.m)
        self.post(self.m, f"/colony/cages/{cage}/give-birth", {"date_give_birth": days_ago(3)})
        self.assertEqual(str(one("select date_give_birth from mouse_cages where id=?", cage))[:10], days_ago(3))


if __name__ == "__main__":
    unittest.main()
