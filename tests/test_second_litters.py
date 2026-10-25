"""A Breeding cage with more than one litter at a time (models.CageLitter):
a trio's second female gives a litter a week or two after the first, and
each keeps its own weaning and genotyping days until it is weaned."""
from __future__ import annotations

import importlib
import re
from datetime import date, timedelta

import sqlalchemy as sa

from tests.base import *  # noqa: F401,F403
from tests.base import AppTestCase, count, days_ago, days_ahead, execute, one, row, rows, uniq

from app import actions
from app.app import app
from app.db import engine


def cage_ref(cage_row: int) -> dict:
    return {"kind": "cage", "id": cage_row}


def litters_of(cage_row: int, weaned: bool = False) -> list[tuple]:
    """(born, pups, dam) of the cage's litters, not yet weaned unless asked."""
    where = "" if weaned else " and weaned_on is null"
    return [(str(b)[:10], p, d) for b, p, d in
            rows(f"select born, pups, dam from cage_litters where cage_id_fk=?{where} order by born", cage_row)]


def birth_date(cage_row: int) -> str | None:
    value = one("select date_give_birth from mouse_cages where id=?", cage_row)
    return str(value)[:10] if value else None


def short(day: str) -> str:
    """A date as the pages write it, in the lab's style ("Sep 29")."""
    from app.app import fmt_day
    with app.test_request_context("/"):
        return fmt_day(date.fromisoformat(day))


class Base(AppTestCase):
    def tearDown(self):
        # Wean what these tests left waiting, so Home's short list stays the
        # other modules' (they share the database).
        execute("update cage_litters set weaned_on=? where weaned_on is null and cage_id_fk in "
                "(select id from mouse_cages where owner=?)", date.today(), self.member)
        execute("update litters set weaned_on=? where weaned_on is null and id in "
                "(select litter_id_fk from mice where owner=?)", date.today(), self.member)
        super().tearDown()

    def trio(self, first: int = 20, second: int = 8) -> tuple[int, str]:
        """A Breeding cage with two litters, born `first` and `second` days ago."""
        code = uniq("C")
        cage = self.make_cage(self.m, code, purpose="Breeding")
        self.assertNoErrors(self.post(self.m, f"/colony/cages/{cage}/give-birth", {"date_give_birth": days_ago(first)}))
        self.assertNoErrors(self.post(self.m, f"/colony/cages/{cage}/give-birth",
                                      {"date_give_birth": days_ago(second), "litter_choice": "another"}))
        return cage, code

    def due(self, code: str) -> list[dict]:
        """Home's list of what is due (as /api/v1/due gives it): this cage's."""
        from flask import g
        from app import lookup
        from app.db import SessionLocal
        from app.models import UserAccount
        with app.test_request_context("/api/v1/due"), SessionLocal() as s:
            g.user = s.scalar(sa.select(UserAccount).where(UserAccount.username == self.member))
            return [i for i in lookup.due(s, 30) if code in i["title"]]

    def calendar(self, client, kind: str) -> list[str]:
        r = client.get("/calendar/events.json", query_string={"start": days_ago(40), "end": days_ahead(40)})
        return [i["title"] for i in r.get_json()["items"] if re.search(rf"-{kind}(-[\d-]+)?$", i["id"])]


class TwoLitters(Base):
    def test_another_litter_keeps_the_first_with_its_own_days(self):
        cage, code = self.trio(20, 8)
        self.assertEqual(litters_of(cage), [(days_ago(20), None, ""), (days_ago(8), None, "")])
        self.assertEqual(birth_date(cage), days_ago(20))   # the oldest, for everything reading one date

    def test_the_message_says_both_are_kept(self):
        code = uniq("C")
        cage = self.make_cage(self.m, code, purpose="Breeding", date_give_birth=days_ago(15))
        r = self.post(self.m, f"/colony/cages/{cage}/give-birth",
                      {"date_give_birth": days_ago(3), "litter_choice": "another", "pups": "7", "dam": "41"})
        self.assertFlash(r, f"Recorded another litter in cage {code}", "success")
        self.assertFlash(r, "is kept, with its own reminders")
        self.assertEqual(litters_of(cage), [(days_ago(15), None, ""), (days_ago(3), 7, "41")])

    def test_without_saying_which_nothing_changes(self):
        cage = self.make_cage(self.m, purpose="Breeding", date_give_birth=days_ago(15))
        r = self.post(self.m, f"/colony/cages/{cage}/give-birth", {"date_give_birth": days_ago(3)})
        self.assertFlash(r, "Say whether this is another litter", "error")
        self.assertEqual(litters_of(cage), [(days_ago(15), None, "")])
        self.assertEqual(birth_date(cage), days_ago(15))

    def test_the_same_date_again_adds_its_count(self):
        cage = self.make_cage(self.m, purpose="Breeding", date_give_birth=days_ago(4))
        r = self.post(self.m, f"/colony/cages/{cage}/give-birth", {"date_give_birth": days_ago(4), "pups": "9"})
        self.assertNoErrors(r)
        self.assertEqual(litters_of(cage), [(days_ago(4), 9, "")])

    def test_correct_the_date_replaces_the_earlier_one(self):
        code = uniq("C")
        cage = self.make_cage(self.m, code, purpose="Breeding", date_give_birth=days_ago(15))
        r = self.post(self.m, f"/colony/cages/{cage}/give-birth", {"date_give_birth": days_ago(13), "litter_choice": "correct"})
        self.assertFlash(r, f"Corrected the litter in cage {code}", "success")
        self.assertEqual(litters_of(cage), [(days_ago(13), None, "")])
        self.assertEqual(birth_date(cage), days_ago(13))

    def test_correcting_one_of_two_names_which(self):
        cage, _ = self.trio(20, 8)
        r = self.post(self.m, f"/colony/cages/{cage}/give-birth", {"date_give_birth": days_ago(7), "litter_choice": "correct"})
        self.assertFlash(r, "say which one's date to correct", "error")
        r = self.post(self.m, f"/colony/cages/{cage}/give-birth",
                      {"date_give_birth": days_ago(7), "litter_choice": "correct", "litter": days_ago(8)})
        self.assertNoErrors(r)
        self.assertEqual([b for b, _p, _d in litters_of(cage)], [days_ago(20), days_ago(7)])

    def test_the_sheet_cell_is_the_oldest_litter(self):
        cage, _ = self.trio(20, 8)
        r = self.autosave(self.m, f"/colony/cages/{cage}/update",
                          {"date_give_birth": days_ago(19), "date_give_birth_was": days_ago(20)})
        self.assertSaved(r)
        self.assertEqual([b for b, _p, _d in litters_of(cage)], [days_ago(19), days_ago(8)])
        # A stale row saving another cell does not write its old date back.
        self.assertSaved(self.autosave(self.m, f"/colony/cages/{cage}/update",
                                       {"notes": "x", "date_give_birth": days_ago(20), "date_give_birth_was": days_ago(20)}))
        self.assertEqual([b for b, _p, _d in litters_of(cage)], [days_ago(19), days_ago(8)])

    def test_each_litter_has_its_own_reminders_on_home_and_the_calendar(self):
        cage, code = self.trio(20, 16)
        titles = [i["title"] for i in self.due(code)]
        for born in (days_ago(20), days_ago(16)):
            self.assertIn(f"Wean Cage {code} (born {short(born)})", titles)
        weans = [t for t in self.calendar(self.m, "wean") if code in t]
        self.assertEqual(len(weans), 2, weans)
        self.assertTrue(any(short(days_ago(20)) in t for t in weans) and any(short(days_ago(16)) in t for t in weans))
        genos = [t for t in self.calendar(self.m, "geno") if code in t]
        self.assertEqual(len(genos), 2, genos)

    def test_the_cage_lists_each_litter_with_its_age(self):
        cage, code = self.trio(20, 8)
        html = self.get_ok(self.m, f"/colony?view=cages&scope=all&q={code}")
        block = html.split(f'data-cage-card="{cage}"', 1)[1].split("</article>", 1)[0]
        listed = block.split("data-cage-litters", 1)[1]
        self.assertIn(f"Litter born {short(days_ago(20))}", listed)
        self.assertIn("P20", listed)
        self.assertIn("P8", listed)


class GenotypingAndWeaning(Base):
    def test_genotyping_takes_the_oldest_litter_not_yet_genotyped(self):
        cage, _ = self.trio(20, 8)
        self.assertNoErrors(self.post(self.m, f"/colony/cages/{cage}/genotyping", {"total_pups": "3"}))
        self.assertNoErrors(self.post(self.m, f"/colony/cages/{cage}/genotyping", {"total_pups": "4"}))
        made = rows("select l.date_of_birth, l.total_pups from cage_litters c join litters l on l.id = c.litter_id_fk "
                    "where c.cage_id_fk=? order by c.born", cage)
        self.assertEqual([(str(b)[:10], n) for b, n in made], [(days_ago(20), 3), (days_ago(8), 4)])

    def test_genotyping_the_litter_named_with_its_recorded_count_and_mother(self):
        cage = self.make_cage(self.m, purpose="Breeding", date_give_birth=days_ago(20))
        self.post(self.m, f"/colony/cages/{cage}/give-birth",
                  {"date_give_birth": days_ago(8), "litter_choice": "another", "pups": "5", "dam": "77"})
        self.assertNoErrors(self.post(self.m, f"/colony/cages/{cage}/genotyping", {"litter": days_ago(8)}))
        litter = row("select date_of_birth, total_pups, mother_info from litters order by id desc limit 1")
        self.assertEqual((str(litter[0])[:10], litter[1], litter[2]), (days_ago(8), 5, "77"))

    def test_the_father_and_mother_from_litter_born_reach_genotyping(self):
        cage = self.make_cage(self.m, purpose="Breeding")
        self.assertNoErrors(self.post(self.m, f"/colony/cages/{cage}/give-birth",
                                      {"date_give_birth": days_ago(10), "pups": "4", "sire": "31", "dam": "32"}))
        html = self.get_ok(self.m, "/colony?view=cages")
        self.assertIn("&#34;sire&#34;: &#34;31&#34;", html)
        self.assertNoErrors(self.post(self.m, f"/colony/cages/{cage}/genotyping", {}))
        litter = row("select total_pups, father_info, mother_info from litters order by id desc limit 1")
        self.assertEqual(tuple(litter), (4, "31", "32"))

    def test_wean_takes_the_oldest_and_leaves_the_other_due(self):
        cage, code = self.trio(22, 8)
        r = self.post(self.m, f"/colony/cages/{cage}/wean")
        self.assertFlash(r, f"Weaned the litter born {short(days_ago(22))} in cage {code}", "success")
        self.assertEqual(litters_of(cage), [(days_ago(8), None, "")])
        self.assertEqual(birth_date(cage), days_ago(8))
        # The second is left, without "(born …)" now.
        self.assertEqual([i["title"] for i in self.due(code)], [f"Wean Cage {code}"])

    def test_wean_the_named_litter_asks_first_when_young(self):
        cage, code = self.trio(22, 8)
        r = self.post(self.m, f"/colony/cages/{cage}/wean", {"litter": days_ago(8)})
        self.assertFlash(r, "are 8 days old", "error")
        self.assertEqual(len(litters_of(cage)), 2)
        self.assertNoErrors(self.post(self.m, f"/colony/cages/{cage}/wean", {"litter": days_ago(8), "early": "1"}))
        self.assertEqual(litters_of(cage), [(days_ago(22), None, "")])

    def test_wean_and_distribute_moves_the_named_litters_pups(self):
        cage, code = self.trio(24, 22)
        self.post(self.m, f"/colony/cages/{cage}/genotyping", {"total_pups": "2"})        # the oldest's
        self.post(self.m, f"/colony/cages/{cage}/genotyping", {"total_pups": "2"})        # the second's
        second = [m for (m,) in rows("select m.mouse_id from mice m join cage_litters c on c.litter_id_fk = m.litter_id_fk "
                                     "where c.cage_id_fk=? and c.born=? order by m.mouse_id", cage, days_ago(22))]
        r = self.post(self.m, f"/colony/cages/{cage}/wean-distribute",
                      {"litter": days_ago(22), "mouse_ids[]": ",".join(map(str, second)), "gender[]": "F",
                       "cage_id[]": "", "card_id[]": "pups"})
        self.assertNoErrors(r)
        self.assertEqual(litters_of(cage), [(days_ago(24), 2, "")])     # its count, from Genotyping
        self.assertFlash(r, "is still to wean")
        weaned = one("select weaned_on from litters where id=(select litter_id_fk from cage_litters where cage_id_fk=? and born=?)",
                     cage, days_ago(22))
        self.assertIsNotNone(weaned)

    def test_a_litter_whose_pups_all_died_leaves_the_lists(self):
        cage, code = self.trio(20, 8)
        self.post(self.m, f"/colony/cages/{cage}/genotyping", {"total_pups": "2", "litter": days_ago(8)})
        execute("update mice set date_of_death=?, status='sac' where litter_id_fk=(select litter_id_fk from cage_litters "
                "where cage_id_fk=? and born=?)", date.today(), cage, days_ago(8))
        weans = [t for t in self.calendar(self.m, "wean") if code in t]
        self.assertEqual(len(weans), 1, weans)

    def test_unknown_litter_is_refused_in_words(self):
        cage, code = self.trio(20, 8)
        r = self.post(self.m, f"/colony/cages/{cage}/wean", {"litter": days_ago(3)})
        self.assertFlash(r, f"Cage {code} has no litter born", "error")
        self.assertEqual(len(litters_of(cage)), 2)


class OneLitterAsBefore(Base):
    def test_a_cage_with_one_litter_reads_as_before(self):
        code = uniq("C")
        cage = self.make_cage(self.m, code, purpose="Breeding")
        r = self.post(self.m, f"/colony/cages/{cage}/give-birth", {"date_give_birth": days_ago(19)})
        self.assertFlash(r, "Recorded a litter born on", "success")
        self.assertEqual(birth_date(cage), days_ago(19))
        self.assertEqual([i["title"] for i in self.due(code)], [f"Wean Cage {code}"])
        self.assertEqual([t for t in self.calendar(self.m, "wean") if code in t], [f"Wean - Cage {code}"])
        html = self.get_ok(self.m, f"/colony?view=cages&scope=all&q={code}")
        block = html.split(f'data-cage-card="{cage}"', 1)[1].split("</article>", 1)[0]
        self.assertNotIn("data-cage-litters", block)
        self.assertIn("genotype by", block)
        r = self.post(self.m, f"/colony/cages/{cage}/wean")
        self.assertFlash(r, f"Cage {code} is weaned.", "success")
        self.assertIsNone(birth_date(cage))
        self.assertEqual(litters_of(cage, weaned=True), [(days_ago(19), None, "")])

    def test_weaning_is_undone_with_its_litter(self):
        cage = self.make_cage(self.m, purpose="Breeding", date_give_birth=days_ago(21))
        self.m.post(f"/colony/cages/{cage}/wean")
        batch = one("select id from batches order by id desc limit 1")
        self.assertNoErrors(self.post(self.m, f"/batches/{batch}/undo"))
        self.assertEqual(litters_of(cage), [(days_ago(21), None, "")])
        self.assertEqual(birth_date(cage), days_ago(21))


class TheActions(Base):
    def run_one(self, action, target, **fields):
        return actions.run(app, self.member, [{"action": action, "target": target, "fields": fields}], apply=True,
                           description=uniq("test"))

    def test_litter_born_refuses_to_guess(self):
        cage = self.make_cage(self.m, purpose="Breeding", date_give_birth=days_ago(15))
        out = self.run_one("litter_born", cage_ref(cage), date=days_ago(2))
        self.assertFalse(out.ok)
        self.assertTrue(any("another: true" in e for e in out.changes[0].errors), out.changes[0].errors)
        self.assertEqual(len(litters_of(cage)), 1)

    def test_another_and_correct(self):
        cage = self.make_cage(self.m, purpose="Breeding", date_give_birth=days_ago(15))
        out = self.run_one("litter_born", cage_ref(cage), date=days_ago(2), another=True, pups=6, mother="#41")
        self.assertTrue(out.ok, out.errors)
        self.assertIn("another litter", out.changes[0].summary)
        self.assertEqual(litters_of(cage), [(days_ago(15), None, ""), (days_ago(2), 6, "#41")])
        out = self.run_one("litter_born", cage_ref(cage), date=days_ago(3), another=False, litter=days_ago(2))
        self.assertTrue(out.ok, out.errors)
        self.assertEqual([b for b, _p, _d in litters_of(cage)], [days_ago(15), days_ago(3)])

    def test_genotyping_and_wean_take_which_litter(self):
        cage, code = self.trio(22, 9)
        out = self.run_one("litter_genotyping", cage_ref(cage), pups=3, litter=days_ago(9))
        self.assertTrue(out.ok, out.errors)
        self.assertIn(f"(the litter born {days_ago(9)})", out.changes[0].summary)
        made = one("select litter_id from litters where id=(select litter_id_fk from cage_litters where cage_id_fk=? and born=?)",
                   cage, days_ago(9))
        self.assertTrue(made)
        out = self.run_one("wean", code, litter=made, early=True)        # named by the litter its pups are
        self.assertTrue(out.ok, out.errors)
        self.assertEqual(litters_of(cage), [(days_ago(22), None, "")])
        out = self.run_one("wean", code, litter=days_ago(5))
        self.assertFalse(out.ok)

    def test_the_due_list_carries_each_litter(self):
        cage, code = self.trio(20, 8)
        items = [i for i in self.due(code) if i.get("litter")]
        self.assertEqual(sorted(i["litter"]["born"] for i in items), [days_ago(20), days_ago(8)])
        self.assertTrue(all(i["propose"]["fields"]["litter"] == i["litter"]["born"] for i in items))


class TheAPI(Base):
    def test_a_cage_lists_its_litters(self):
        from app import api
        api.rate.reset()
        execute("delete from app_settings where key='members_api_tokens'")
        cage, code = self.trio(20, 8)
        r = self.m.post("/settings/api-tokens", data={"label": uniq("Script "), "scope": "read", "expires": "90"})
        tok = re.search(r'id="api-token"[^>]*>(bmt_\w+)<', r.get_data(as_text=True)).group(1)
        got = app.test_client().get(f"/api/v1/cages/{code}", headers={"Authorization": f"Bearer {tok}"}).get_json()
        self.assertEqual(got["litter_born"], days_ago(20))
        self.assertEqual([(lt["born"], lt["age_days"]) for lt in got["litters"]], [(days_ago(20), 20), (days_ago(8), 8)])
        self.assertEqual(got["litters"][1]["wean_due"], days_ahead(13))


class TheUpgrade(Base):
    """Revision 0037: each cage's birth date becomes its first litter, linked
    to the litter of its pups, and running it again changes nothing."""

    def migrate(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        migration = importlib.import_module("migrations.versions.0037_cage_litters")
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()

    def test_an_existing_labs_birth_dates_become_litters(self):
        col = self.make_colony(self.m, self.member, purpose="Breeding", n_mice=2, dob=days_ago(10))
        plain = self.make_cage(self.m, purpose="Breeding")
        # As a lab before 0037 had them: a date on the cage, no litter rows.
        execute("update mouse_cages set date_give_birth=? where id in (?, ?)", date.fromisoformat(days_ago(10)),
                col["cage_id"], plain)
        execute("delete from cage_litters where cage_id_fk in (?, ?)", col["cage_id"], plain)
        self.migrate()
        after = count("cage_litters")
        self.migrate()                                    # again: nothing more
        self.assertEqual(count("cage_litters"), after)
        self.assertEqual(litters_of(plain), [(days_ago(10), None, "")])
        self.assertEqual(one("select litter_id_fk from cage_litters where cage_id_fk=?", col["cage_id"]), col["litter_id"])
        # And it reads as before: one weaning, as the litter of its pups.
        weans = [t for t in self.calendar(self.m, "wean") if col["cage"] in t]
        self.assertEqual(weans, [f"Wean - Litter {col['litter']} · cage {col['cage']}"])

    def test_a_new_database_has_the_table_from_its_model(self):
        self.assertIn("cage_litters", sa.inspect(engine).get_table_names())



class OnlyABreedingCageHasLitters(AppTestCase):
    """A litter belongs to a Breeding (mating) cage alone (app/litters.py),
    however its date arrives: the sheet's Litter born cell, the cage dialog
    or New cage."""

    def test_the_litter_born_cell_of_another_cage_is_refused(self):
        cage = self.make_cage(self.m, purpose="Experiment")
        r = self.autosave(self.m, f"/colony/cages/{cage}/update",
                          {"date_give_birth": days_ago(3), "date_give_birth_was": ""})
        self.assertRefused(r)
        self.assertIn("isn't a Breeding cage", r.get_json()["error"])
        self.assertEqual((litters_of(cage), birth_date(cage)), ([], None))

    def test_new_cage_with_a_birth_date_must_be_a_breeding_cage(self):
        code = uniq("C")
        r = self.post(self.m, "/colony/cages/create", {"cage_id": code, "purpose": "Experiment",
                                                      "date_give_birth": days_ago(3)})
        self.assertFlash(r, "isn't a Breeding cage", "error")
        self.assertEqual(count("cage_litters", "cage_id_fk=(select id from mouse_cages where cage_id=?)", code), 0)

    def test_a_breeding_cage_takes_its_date_and_any_cage_may_clear_one(self):
        cage = self.make_cage(self.m, purpose="Breeding", date_give_birth=days_ago(3))
        self.assertEqual(birth_date(cage), days_ago(3))
        self.assertSaved(self.autosave(self.m, f"/colony/cages/{cage}/update", {"purpose": "Experiment"}))
        self.assertSaved(self.autosave(self.m, f"/colony/cages/{cage}/update",
                                       {"date_give_birth": "", "date_give_birth_was": days_ago(3)}))
        self.assertEqual((litters_of(cage), birth_date(cage)), ([], None))
