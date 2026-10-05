"""app/actions.py: what an assistant may propose, previewed (nothing kept)
or applied as one batch, always through the pages' own code."""
from __future__ import annotations

import unittest

from tests.base import *  # noqa: F401,F403
from tests.base import AppTestCase, count, days_ago, one, row, rows, uniq

from app import actions
from app.app import app


def cage_ref(cage_row: int) -> dict:
    return {"kind": "cage", "id": cage_row}


def number(mouse_row: int) -> int:
    return one("select mouse_id from mice where id=?", mouse_row)


class APreview(AppTestCase):
    def test_says_what_would_change_and_keeps_nothing(self):
        cage = self.make_cage(self.m)
        code = one("select cage_id from mouse_cages where id=?", cage)
        mice, batches, entries = count("mice"), count("batches"), count("audit_log")
        out = actions.run(app, self.member, [
            {"action": "litter_born", "target": cage_ref(cage), "fields": {"date": days_ago(20)}},
            {"action": "litter_genotyping", "target": code, "fields": {"pups": 3, "father": "#12"}},
        ])
        self.assertTrue(out.ok, out.errors)
        self.assertEqual(out.changes[0].summary, f"Litter born in cage {code} on {days_ago(20)}")
        self.assertIn("3 pups", out.changes[1].summary)
        self.assertTrue(any("cage" in r["table"] for r in out.changes[0].records), out.changes[0].records)
        self.assertEqual(sum(r["table"] == "mice" and r["action"] == "create" for r in out.changes[1].records), 3)
        self.assertEqual((count("mice"), count("batches"), count("audit_log")), (mice, batches, entries))
        self.assertIsNone(one("select date_give_birth from mouse_cages where id=?", cage))

    def test_the_pages_warning_comes_through(self):
        cage = self.make_cage(self.m, date_give_birth=days_ago(40))
        out = actions.run(app, self.member, [{"action": "litter_born", "target": cage_ref(cage),
                                              "fields": {"date": days_ago(1)}}])
        self.assertTrue(out.ok, out.errors)
        self.assertTrue(any("replaces the litter born" in w for w in out.changes[0].warnings), out.changes[0])

    def test_the_pages_refusal_comes_through_and_the_next_change_still_runs(self):
        cage = self.make_cage(self.m)
        other = self.make_cage(self.m)
        out = actions.run(app, self.member, [
            {"action": "litter_genotyping", "target": cage_ref(cage), "fields": {"pups": 99}},
            {"action": "litter_born", "target": cage_ref(other), "fields": {"date": days_ago(2)}},
        ])
        self.assertFalse(out.ok)
        self.assertTrue(any("Pups must be a whole number" in e for e in out.changes[0].errors), out.changes[0])
        self.assertTrue(out.changes[1].ok, out.changes[1])

    def test_a_failed_change_leaves_nothing_for_the_next_to_see(self):
        source, mine, theirs, later = (self.make_cage(self.m), self.make_cage(self.m), self.make_cage(self.o),
                                       self.make_cage(self.m))
        code = one("select cage_id from mouse_cages where id=?", source)
        a, b = (self.make_mouse(self.m, self.member, cage=code) for _ in range(2))
        theirs_code = one("select cage_id from mouse_cages where id=?", theirs)
        mine_code = one("select cage_id from mouse_cages where id=?", mine)
        out = actions.run(app, self.member, [
            # The page moves a, then refuses b (their cage is private): the change fails.
            {"action": "wean", "target": cage_ref(source), "fields": {"early": True, "rows": [
                {"mice": [number(a)], "cage": mine_code}, {"mice": [number(b)], "cage": theirs_code}]}},
            {"action": "mice_move", "target": cage_ref(later), "fields": {"mice": [number(a)]}},
        ])
        self.assertFalse(out.changes[0].ok)
        self.assertTrue(any("Not moved" in e for e in out.changes[0].errors), out.changes[0])
        self.assertTrue(out.changes[1].ok, out.changes[1])
        moved = [r for r in out.changes[1].records if r["table"] == "mice" and r["id"] == a]
        self.assertEqual(moved[0]["changes"]["changes"]["cage_id_fk"], [source, later])

    def test_someone_else_cannot_propose_changes_to_your_cage(self):
        cage = self.make_cage(self.m)
        out = actions.run(app, self.other, [{"action": "litter_born", "target": cage_ref(cage), "fields": {}}])
        self.assertFalse(out.ok)
        self.assertTrue(out.changes[0].errors)

    def test_what_is_not_an_action_is_refused_plainly(self):
        cage = self.make_cage(self.m)
        cases = [
            ({"action": "drop_tables"}, "no action called"),
            ({"action": "litter_born", "target": cage_ref(cage), "fields": {"when": "today"}}, "has no when"),
            ({"action": "litter_born", "target": cage_ref(cage), "fields": {"date": "4 Oct"}}, "a date like"),
            ({"action": "litter_born", "fields": {}}, "needs a target"),
            ({"action": "litter_born", "target": {"kind": "cage", "id": 10 ** 9}}, "There is no cage"),
            ({"action": "litter_born", "target": {"kind": "mouse", "id": cage}}, "Expected a cage"),
            ({"action": "litter_genotyping", "target": cage_ref(cage), "fields": {}}, "needs pups"),
            ({"action": "cage_new", "target": cage_ref(cage), "fields": {}}, "takes no target"),
        ]
        for change, words in cases:
            out = actions.run(app, self.member, [change])
            self.assertFalse(out.ok, change)
            self.assertIn(words, " ".join(out.changes[0].errors), change)

    def test_an_unknown_person_changes_nothing(self):
        out = actions.run(app, uniq("nobody"), [{"action": "cage_new", "fields": {}}])
        self.assertFalse(out.ok)


class Applying(AppTestCase):
    def test_everything_goes_in_as_one_batch_that_undoes_as_one(self):
        cage = self.make_cage(self.m)
        dest = self.make_cage(self.m)
        code, dest_code = (one("select cage_id from mouse_cages where id=?", c) for c in (cage, dest))
        mice = count("mice")
        out = actions.run(app, self.member, [
            {"action": "litter_born", "target": cage_ref(cage), "fields": {"date": days_ago(25)}},
            {"action": "litter_genotyping", "target": cage_ref(cage), "fields": {"pups": 4}},
        ], apply=True, label="Proposal #1 (via Claude)", description="Proposal #1: a litter (via Claude)")
        self.assertTrue(out.ok, out.errors)
        pups = [r[0] for r in rows("select mouse_id from mice where cage_id_fk=? order by mouse_id", cage)]
        self.assertEqual(len(pups), 4)
        out2 = actions.run(app, self.member, [
            {"action": "wean", "target": code, "fields": {"rows": [
                {"mice": pups[:2], "sex": "F", "cage": dest_code},
                {"mice": pups[2:], "sex": "M", "new_card": "males"}]}},
        ], apply=True, label="Proposal #2 (via Claude)", description="Proposal #2: weaned (via Claude)")
        self.assertTrue(out2.ok, out2.errors)
        self.assertEqual(count("mice", "cage_id_fk=?", dest), 2)
        self.assertEqual(count("mice", "cage_id_fk=?", cage), 0)
        self.assertEqual(count("batches", "description like ?", "Proposal #2%"), 1)
        # Every row it wrote belongs to that one batch, labelled with it.
        batch = out2.batch_id
        self.assertEqual(row("select description, actor, action from batches where id=?", batch),
                         ("Proposal #2: weaned (via Claude)", self.member, "mixed"))
        self.assertEqual(count("audit_log", "batch_id_fk=? and details not like ?", batch, "[Proposal #2%"), 0)
        self.assertGreaterEqual(count("audit_log", "batch_id_fk=?", batch), 5)
        self.assertEqual(count("batches", "id>? and id<>?", out.batch_id, batch), 0)  # no page made its own
        self.post(self.m, f"/batches/{batch}/undo")
        self.assertEqual(count("mice", "cage_id_fk=?", cage), 4)
        self.assertEqual(count("mice"), mice + 4)

    def test_nothing_is_kept_when_one_change_fails(self):
        cage = self.make_cage(self.m)
        out = actions.run(app, self.member, [
            {"action": "litter_born", "target": cage_ref(cage), "fields": {"date": days_ago(3)}},
            {"action": "litter_genotyping", "target": cage_ref(cage), "fields": {"pups": 0}},
        ], apply=True, description="Proposal")
        self.assertFalse(out.ok)
        self.assertIsNone(out.batch_id)
        self.assertIsNone(one("select date_give_birth from mouse_cages where id=?", cage))
        self.assertEqual(count("batches", "description='Proposal'"), 0)


class EachActionDoesWhatThePageDoes(AppTestCase):
    def apply(self, *changes, who=None):
        out = actions.run(app, who or self.member, list(changes), apply=True, description=uniq("P"))
        self.assertTrue(out.ok, [c.as_dict() for c in out.changes])
        return out

    def test_new_mice_in_a_cage(self):
        cage = self.make_cage(self.m)
        self.apply({"action": "mouse_new", "fields": {"count": 3, "sex": "F", "cage": cage_ref(cage),
                                                      "transgenes": ["Ai14", "Cre"], "status": "experiment"}})
        self.assertEqual(rows("select gender, transgene_1, transgene_2, owner from mice where cage_id_fk=?", cage),
                         [("F", "Ai14", "Cre", self.member)] * 3)

    def test_change_a_mouse_leaves_the_rest(self):
        mouse = self.make_mouse(self.m, self.member, note="keep me")
        self.apply({"action": "mouse_change", "target": number(mouse), "fields": {"status": "breeder", "sex": "M"}})
        self.assertEqual(row("select status, gender, note from mice where id=?", mouse), ("breeder", "M", "keep me"))

    def test_a_weight(self):
        mouse = self.make_mouse(self.m, self.member)
        self.apply({"action": "mouse_weight", "target": {"kind": "mouse", "id": mouse},
                    "fields": {"grams": 23.5, "date": days_ago(1)}})
        self.assertEqual(one("select grams from mouse_weights where mouse_id_fk=?", mouse), 23.5)

    def test_move_mice(self):
        cage = self.make_cage(self.m)
        a, b = self.make_mouse(self.m, self.member), self.make_mouse(self.m, self.member)
        self.apply({"action": "mice_move", "target": cage_ref(cage), "fields": {"mice": [number(a), f"#{number(b)}"]}})
        self.assertEqual(count("mice", "cage_id_fk=?", cage), 2)

    def test_mice_died_keeps_their_notes(self):
        mouse = self.make_mouse(self.m, self.member, note="tail clipped")
        self.apply({"action": "mice_died", "fields": {"mice": [number(mouse)], "date": days_ago(2),
                                                      "reason": "found dead"}})
        status, died, note = row("select status, date_of_death, note from mice where id=?", mouse)
        self.assertEqual((status, str(died)[:10], note), ("sac", days_ago(2), "tail clipped; found dead"))

    def test_new_cage_and_change_it(self):
        code = uniq("C")
        self.apply({"action": "cage_new", "fields": {"cage_id": code, "purpose": "Breeder"}})
        cage = one("select id from mouse_cages where cage_id=?", code)
        self.assertEqual(one("select purpose from mouse_cages where id=?", cage), "Breeder")
        rack = uniq("R")
        self.m.post("/colony/racks/save", data={"id": "", "name": rack, "rows": "4", "cols": "4"})
        self.apply({"action": "cage_change", "target": code, "fields": {"rack": rack, "position": "A2",
                                                                        "room": "B12"}})
        self.assertEqual(row("select rack_row, rack_col, room, purpose from mouse_cages where id=?", cage),
                         (1, 2, "B12", "Breeder"))

    def test_notes_add_to_what_is_there(self):
        cage = self.make_cage(self.m, notes="first")
        litter = self.make_litter(self.m, notes="born at night")
        mouse = self.make_mouse(self.m, self.member)
        self.apply({"action": "note_add", "target": cage_ref(cage), "fields": {"text": "flooded"}},
                   {"action": "note_add", "target": {"kind": "litter", "id": litter}, "fields": {"text": "7 pups"}},
                   {"action": "note_add", "target": {"kind": "mouse", "id": mouse}, "fields": {"text": "ear tag 3"}})
        self.assertEqual(one("select notes from mouse_cages where id=?", cage), "first; flooded")
        self.assertEqual(one("select notes from litters where id=?", litter), "born at night; 7 pups")
        self.assertEqual(one("select note from mice where id=?", mouse), "ear tag 3")

    def test_weaning_too_young_needs_early(self):
        cage = self.make_cage(self.m, date_give_birth=days_ago(10))
        out = actions.run(app, self.member, [{"action": "wean", "target": cage_ref(cage), "fields": {}}])
        self.assertFalse(out.ok)
        self.assertIn("days old", out.changes[0].errors[0])
        self.apply({"action": "wean", "target": cage_ref(cage), "fields": {"early": True}})

    def test_every_action_describes_its_fields(self):
        for item in actions.catalogue():
            self.assertTrue(item["title"] and item["help"], item["name"])
            self.assertEqual(item["fields"]["type"], "object")
            for name, spec in item["fields"]["properties"].items():
                self.assertTrue(spec.get("description"), f"{item['name']}.{name}")


class ZebrafishActions(AppTestCase):
    def apply(self, *changes):
        out = actions.run(app, self.member, list(changes), apply=True, description=uniq("P"))
        self.assertTrue(out.ok, [c.as_dict() for c in out.changes])
        return out

    def test_tanks_new_and_changed(self):
        from tests.test_zebrafish import make_rack
        line = self.make_line(self.m)
        name = one("select name from fish_lines where id=?", line)
        code = uniq("T") + "1"
        out = self.apply({"action": "tank_new", "fields": {"tank_id": code, "line": name.upper(),
                                                           "purpose": "stock"}})
        self.assertIn(name, out.changes[0].summary)
        tank = one("select id from tanks where tank_id=?", code)
        self.assertEqual(row("select line_id_fk, owner from tanks where id=?", tank), (line, self.member))
        rack = make_rack(self.a)
        self.apply({"action": "tank_change", "target": code,
                    "fields": {"rack": {"kind": "fish_rack", "id": rack}, "position": "A2", "notes": "top shelf"}})
        self.assertEqual(row("select rack_id_fk, row, col, notes from tanks where id=?", tank),
                         (rack, 0, 1, "top shelf"))
        self.apply({"action": "tank_change", "target": code, "fields": {"active": False}})
        self.assertFalse(one("select active from tanks where id=?", tank))

    def test_a_cross_set_up_and_returned_and_its_clutch(self):
        from tests.test_zebrafish import make_fish
        father, mother = self.make_tank(self.m), self.make_tank(self.m)
        make_fish(self.m, father, 3, sex="M")
        make_fish(self.m, mother, 4, sex="F")
        before = one("select coalesce(max(id), 0) from tanks")
        self.apply({"action": "mating_set_up", "fields": {"father_tank": {"kind": "tank", "id": father},
                                                          "mother_tank": {"kind": "tank", "id": mother},
                                                          "males": 1, "females": 2}})
        mt = one("select id from tanks where id>? and purpose='mating'", before)
        self.assertEqual(one("select sum(count) from fish where tank_id_fk=?", mt), 3)
        self.apply({"action": "clutch_new", "fields": {"father_tank": {"kind": "tank", "id": father},
                                                       "mother_tank": {"kind": "tank", "id": mother},
                                                       "embryos": 120, "date": days_ago(0)}},
                   {"action": "mating_returned", "target": {"kind": "tank", "id": mt}})
        self.assertEqual(one("select sum(count) from fish where tank_id_fk=?", father), 3)
        self.assertFalse(one("select active from tanks where id=?", mt))
        clutch = one("select clutch_id from clutches where father_tank_id=? and mother_tank_id=?", father, mother)
        self.apply({"action": "clutch_change", "target": clutch, "fields": {"larvae": 80}})
        self.assertEqual(row("select embryo_count, larvae_count from clutches where clutch_id=?", clutch), (120, 80))

    def test_fish_rows_and_the_sac_log(self):
        tank, other = self.make_tank(self.m), self.make_tank(self.m)
        self.apply({"action": "fish_new", "fields": {"tank": {"kind": "tank", "id": tank}, "count": 6, "sex": "F"}})
        fish = one("select id from fish where tank_id_fk=?", tank)
        self.apply({"action": "fish_change", "target": fish, "fields": {"tank": {"kind": "tank", "id": other},
                                                                        "genotype": "het"}},
                   {"action": "fish_sac", "fields": {"fish": fish, "count": 2, "reason": "sick"}})
        self.assertEqual(row("select tank_id_fk, genotype, count from fish where id=?", fish), (other, "het", 4))
        self.assertEqual(one("select reason from fish_sac_log where fish_id_fk=?", fish), "sick")

    def test_a_water_reading(self):
        from tests.test_zebrafish import make_system
        system = make_system(self.a)
        name = one("select name from water_systems where id=?", system)
        out = self.apply({"action": "water_reading", "fields": {"system": name, "ph": 7.2, "temperature": 28.5}})
        self.assertIn("ph 7.2", out.changes[0].summary)
        self.assertEqual(row("select ph, temperature_c from water_logs where system_id_fk=?", system), (7.2, 28.5))

    def test_the_pages_own_refusal(self):
        tank = self.make_tank(self.m)
        out = actions.run(app, self.member, [{"action": "mating_set_up", "fields": {
            "father_tank": {"kind": "tank", "id": tank}, "mother_tank": {"kind": "tank", "id": tank}}}])
        self.assertIn("two different tanks", " ".join(out.changes[0].errors))


def code_of(unit_row: int) -> str:
    """A vial as the lab writes it (FV12)."""
    from app import stock_service as svc
    from app.db import SessionLocal
    from app.models import StockModule, StockUnit
    with SessionLocal() as s:
        unit = s.get(StockUnit, unit_row)
        return svc.view(s.get(StockModule, unit.module_id_fk)).code(unit)


class FlyAndWormActions(AppTestCase):
    def apply(self, *changes):
        out = actions.run(app, self.member, list(changes), apply=True, description=uniq("P"))
        self.assertTrue(out.ok, [c.as_dict() for c in out.changes])
        return out

    def test_vials_new_changed_copied_and_discarded(self):
        key, _inc, rack = self.make_fly_setup(self.m)
        mid = self.stock_module_id(key)
        rack_name = one("select name from stock_racks where id=?", rack)
        self.apply({"action": "stock_new", "fields": {"database": key, "count": 2, "genotype": "w; Sp/CyO",
                                                      "rack": rack_name}})
        units = [r[0] for r in rows("select id from stock_units where module_id_fk=? order by number", mid)]
        self.assertEqual(len(units), 2)
        self.assertEqual(count("stock_units", "module_id_fk=? and rack_row is not null", mid), 2)
        prefix_code = code_of(units[0])
        # Another database numbering its vials the same way: the code alone could be either, so it is refused.
        twin = self.make_vial(self.m, self.make_stock_module(self.m, "fly"))
        if code_of(twin) == prefix_code:
            out = actions.run(app, self.member, [{"action": "stock_change", "target": prefix_code,
                                                  "fields": {"notes": "weak"}}])
            self.assertIn("could be several", out.changes[0].errors[0])
        out = self.apply({"action": "stock_change", "target": {"kind": "stock_unit", "id": units[0]},
                          "fields": {"notes": "weak"}},
                         {"action": "stock_event", "target": {"kind": "stock_unit", "id": units[1]},
                          "fields": {"event": "copy"}},
                         {"action": "stock_event", "target": {"kind": "stock_unit", "id": units[1]},
                          "fields": {"event": "discard"}})
        self.assertIn(prefix_code, out.changes[0].summary)
        self.assertEqual(one("select notes from stock_units where id=?", units[0]), "weak")
        self.assertEqual(count("stock_units", "module_id_fk=?", mid), 3)
        self.assertFalse(one("select active from stock_units where id=?", units[1]))

    def test_rack_flipped(self):
        key, _inc, rack = self.make_fly_setup(self.m)
        self.apply({"action": "rack_flipped", "target": {"kind": "stock_rack", "id": rack},
                    "fields": {"date": days_ago(1)}})
        self.assertEqual(str(one("select last_flipped_on from stock_racks where id=?", rack))[:10], days_ago(1))

    def test_frozen_stocks(self):
        key = self.make_stock_module(self.m, "worm")
        self.apply({"action": "frozen_new", "fields": {"database": key, "genotype": "N2", "vials": 6,
                                                       "location": "-80 box 3"}})
        lot = one("select id from stock_frozen where genotype='N2' and module_id_fk=?", self.stock_module_id(key))
        self.apply({"action": "frozen_change", "target": {"kind": "stock_frozen", "id": lot},
                    "fields": {"vials_left": 5, "thaw_ok": True}})
        self.assertEqual(row("select vials, vials_left, location, thaw_ok from stock_frozen where id=?", lot),
                         (6, 5, "-80 box 3", True))

    def test_a_rack_from_another_database_is_refused(self):
        key, _inc, _rack = self.make_fly_setup(self.m)
        other_key, _i, other_rack = self.make_fly_setup(self.m)
        out = actions.run(app, self.member, [{"action": "stock_new", "fields": {
            "database": key, "rack": {"kind": "stock_rack", "id": other_rack}}}])
        self.assertIn("another database", " ".join(out.changes[0].errors))


class OrganismActions(AppTestCase):
    """A newt database made by the member (any organism the lab sets up)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.make_organism_module(cls.m)
        cls.mid = cls.organism_module_id(cls.key)

    def apply(self, *changes):
        out = actions.run(app, self.member, list(changes), apply=True, description=uniq("P"))
        self.assertTrue(out.ok, [c.as_dict() for c in out.changes])
        return out

    def test_animals_housing_lines_crosses_and_cohorts(self):
        tank = uniq("H")
        line = uniq("LN")
        self.apply({"action": "org_housing_new", "fields": {"database": self.key, "code": tank, "purpose": "stock"}},
                   {"action": "org_line_new", "fields": {"database": self.key, "code": line, "name": "albino"}})
        out = self.apply({"action": "org_animal_new", "fields": {
            "database": self.key, "how_many": 3, "housing": tank, "line": line, "sex": "F", "birth_on": days_ago(30)}})
        self.assertIn(f"line → {line}", out.changes[0].summary)
        made = rows("select id, sex from organisms where module_id_fk=? and housing_id_fk=(select id from "
                    "organism_housing where code=? and module_id_fk=?)", self.mid, tank, self.mid)
        self.assertEqual([sx for _i, sx in made], ["F"] * 3)
        self.apply({"action": "org_animal_change", "target": {"kind": "org_animal", "id": made[0][0]},
                    "fields": {"status": "dead", "death_on": days_ago(1), "notes": "found dead"}},
                   {"action": "org_housing_change", "target": tank, "fields": {"retired": True}},
                   {"action": "org_cross_new", "fields": {"database": self.key, "sire_line": line,
                                                          "dam_label": "wild females"}},
                   {"action": "org_cohort_new", "fields": {"database": self.key, "line": line,
                                                           "count_initial": 40, "stage": "larvae"}})
        self.assertEqual(str(one("select death_on from organisms where id=?", made[0][0]))[:10], days_ago(1))
        self.assertFalse(one("select active from organism_housing where code=? and module_id_fk=?", tank, self.mid))
        self.assertEqual(one("select dam_label from organism_crosses where module_id_fk=? order by id desc limit 1",
                             self.mid), "wild females")
        self.assertEqual(row("select count_initial, count_current from organism_cohorts where module_id_fk=? "
                             "order by id desc limit 1", self.mid), (40, 40))
        self.apply({"action": "org_housing_change", "target": tank, "fields": {"retired": False}})
        self.assertTrue(one("select active from organism_housing where code=? and module_id_fk=?", tank, self.mid))

    def test_a_genotype_call_and_a_scheduled_job_done(self):
        animal = self.make_animal(self.m, self.key, owner=self.member, birth_on=days_ago(300))
        self.apply({"action": "org_genotype", "target": {"kind": "org_animal", "id": animal},
                    "fields": {"assay": "PCR", "result": "Cre+", "zygosity": "het"}})
        self.assertEqual(one("select result from organism_genotypes where subject_id=?", animal), "Cre+")
        label = uniq("Rule ")
        self.m.post(f"/organisms/{self.key}/rule/save", data={"label": label, "applies_to": "organism",
                                                               "anchor": "birth_on", "offset_days": "30"})
        due = one("select id from organism_due where module_id_fk=? and subject_id=? and done_on is null",
                  self.mid, animal)
        self.assertTrue(due)
        out = self.apply({"action": "org_due_done", "target": {"kind": "org_due", "id": due}})
        self.assertIn("due", out.changes[0].summary)
        self.assertTrue(one("select done_on from organism_due where id=?", due))

    def test_a_record_from_another_database_is_refused(self):
        other_key = self.make_organism_module(self.m)
        foreign = self.make_housing(self.m, other_key)
        out = actions.run(app, self.member, [{"action": "org_animal_new", "fields": {
            "database": self.key, "housing": {"kind": "org_housing", "id": foreign}}}])
        self.assertIn("another database", " ".join(out.changes[0].errors))


class ExperimentActions(AppTestCase):
    def setUp(self):
        super().setUp()
        from datetime import date, timedelta
        self.start = date.today() - timedelta(days=1)            # day 2 is today
        from tests.base import location
        r = self.m.post("/colony/experiments/create", data={"name": uniq("TMX "), "start_date": self.start.isoformat()})
        self.exp = int(location(r).rsplit("/", 1)[1])
        self.name = one("select name from experiments where id=?", self.exp)
        self.mice = [self.make_mouse(self.m, self.member) for _ in range(3)]
        for mouse in self.mice:
            self.m.post(f"/colony/experiments/{self.exp}/add-mouse", data={"mouse_row_id": mouse})
        r = self.m.post(f"/colony/experiments/{self.exp}/steps/save",
                        json={"kind": "injection", "agent": "Tamoxifen", "dose": "20 mg/kg", "days": "1-3"})
        self.step = r.get_json()["steps"][0]["id"]

    def apply(self, *changes):
        out = actions.run(app, self.member, list(changes), apply=True, description=uniq("P"))
        self.assertTrue(out.ok, [c.as_dict() for c in out.changes])
        return out

    def test_a_step_done_today_finds_its_day(self):
        out = self.apply({"action": "experiment_step_done", "target": {"kind": "experiment_step", "id": self.step},
                          "fields": {"animals": [number(self.mice[0]), f"#{number(self.mice[1])}"], "note": "#3 escaped"}})
        self.assertIn("day 2 done", out.changes[0].summary)
        day, note, mice = row("select day, note, mice from experiment_step_records where step_id_fk=?", self.step)
        self.assertEqual((day, note), (2, "#3 escaped"))
        self.assertEqual(mice.count('"subject"'), 2)

    def test_a_day_off_the_plan_is_refused(self):
        out = actions.run(app, self.member, [{"action": "experiment_step_done",
                                              "target": {"kind": "experiment_step", "id": self.step},
                                              "fields": {"day": 9}}])
        self.assertIn("not in this step's plan", out.changes[0].errors[0])

    def test_status_readout_animals_and_a_note(self):
        extra = self.make_mouse(self.m, self.member)
        self.apply({"action": "experiment_change", "target": self.name, "fields": {"status": "paused"}},
                   {"action": "experiment_add_animals", "target": {"kind": "experiment", "id": self.exp},
                    "fields": {"mice": [number(extra)], "group": "vehicle"}},
                   {"action": "experiment_reading", "target": {"kind": "experiment", "id": self.exp},
                    "fields": {"values": {str(number(self.mice[0])): 22.5}}},
                   {"action": "note_add", "target": {"kind": "experiment", "id": self.exp},
                    "fields": {"text": "cage flooded on day 2"}})
        self.assertEqual(one("select status from experiments where id=?", self.exp), "paused")
        self.assertEqual(one("select treatment_group from experiment_mice where experiment_id_fk=? and mouse_id_fk=?",
                             self.exp, extra), "vehicle")
        self.assertIn("cage flooded on day 2", one("select description from experiments where id=?", self.exp))

    def test_due_lists_the_days_not_yet_done(self):
        from app import api
        api.rate.reset()
        r = self.m.post("/settings/api-tokens", data={"label": uniq("T"), "scope": "read", "expires": "90"})
        import re
        tok = re.search(r'id="api-token"[^>]*>(bmt_\w+)<', r.get_data(as_text=True)).group(1)
        data = app.test_client().get("/api/v1/due", headers={"Authorization": f"Bearer {tok}"}).get_json()["data"]
        mine = [d for d in data if d["ref"] == {"kind": "experiment_step", "id": self.step}]
        self.assertEqual([d["propose"]["fields"]["day"] for d in mine], [1, 2, 3])
        self.assertEqual(mine[0]["status"], "overdue")
        self.apply(mine[1]["propose"])
        data = app.test_client().get("/api/v1/due", headers={"Authorization": f"Bearer {tok}"}).get_json()["data"]
        self.assertEqual([d["propose"]["fields"]["day"] for d in data
                          if d["ref"] == {"kind": "experiment_step", "id": self.step}], [1, 3])


if __name__ == "__main__":
    unittest.main()
