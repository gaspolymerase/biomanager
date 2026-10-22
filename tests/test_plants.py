"""Plant databases (issue: a plant lab's nursery, sowing and harvest dates):
the Plants and Field crops presets, a database's own date words, stages and
kinds of place, seed lots harvested and sown again a generation on, their
descent, a selection plate's 3:1, seed labels, and the dot's age bands."""
from __future__ import annotations

import json
import unittest
from datetime import date

from tests.base import *  # noqa: F401,F403
from tests.base import AppTestCase, SessionLocal, count, days_ago, flash_text, location, one, uniq

from app import organism_service as svc
from app.organisms import PRESET_BY_KEY

TODAY = date.today().isoformat()


def plant_module(client, preset="plant", label=None) -> str:
    """A database made from a plant preset, as the builder makes it."""
    label = label or uniq("Arabidopsis ")
    r = client.post("/organisms/new", data={"audience": "lab", "preset_key": preset, "label": label,
                                            "capabilities": PRESET_BY_KEY[preset].capabilities,
                                            "identity_mode": PRESET_BY_KEY[preset].identity_mode})
    path = location(r)
    assert path.startswith("/organisms/"), (r.status_code, path)
    return path.split("?")[0].rsplit("/", 1)[1]


class PlantCase(AppTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = plant_module(cls.a)
        cls.url = f"/organisms/{cls.key}"
        cls.mid = one("select id from organism_modules where key=?", cls.key)

    def page(self, view, client=None) -> str:
        return self.get_ok(client or self.a, f"{self.url}?view={view}")

    def line(self, code=None) -> int:
        code = code or uniq("L")
        self.post(self.a, f"{self.url}/line/save", {"code": code, "_full": "1"})
        return one("select id from organism_lines where module_id_fk=? and code=?", self.mid, code)

    def sowing(self, code=None, **fields) -> int:
        code = code or uniq("SOW")
        self.post(self.a, f"{self.url}/cohort/save", {"code": code, "birth_on": TODAY, **fields})
        found = one("select id from organism_cohorts where module_id_fk=? and code=?", self.mid, code)
        assert found, f"sowing {code} not made"
        return found

    def plant(self, code=None, **fields) -> int:
        code = code or uniq("P")
        self.post(self.a, f"{self.url}/animal/save", {"code": code, "count": "1", "status": "growing", **fields})
        found = one("select id from organisms where module_id_fk=? and code=?", self.mid, code)
        assert found, f"plant {code} not made"
        return found

    def lot(self, code=None, **fields) -> int:
        code = code or uniq("S")
        r = self.post(self.a, f"{self.url}/seedlot/save", {"code": code, **fields})
        found = one("select id from organism_seed_lots where module_id_fk=? and code=?", self.mid, code)
        assert found, (f"seed lot {code} not made", flash_text(r))
        return found

    def lot_value(self, lot_id, column):
        return one(f"select {column} from organism_seed_lots where id=?", lot_id)


# ============================================================ the presets

class ThePlantPresets(PlantCase):

    def test_the_builder_offers_both(self):
        h = self.get_ok(self.a, "/organisms/new")
        self.assertIn("?preset=plant", h)
        self.assertIn("?preset=field_crop", h)

    def test_a_plant_database_has_no_sexes_and_its_own_words(self):
        self.assertEqual(json.loads(one("select sexes from organism_modules where id=?", self.mid)), [])
        h = self.page("animals")
        self.assertIn("Sown", h)
        self.assertIn("Harvested / removed", h)
        self.assertNotIn('name="sex"', h)
        self.assertNotIn(">Born<", h)

    def test_it_keeps_seed_and_a_nursery(self):
        caps = json.loads(one("select capabilities from organism_modules where id=?", self.mid))
        self.assertIn("seed_bank", caps)
        self.assertIn("nursery", caps)
        h = self.page("cohorts")
        self.assertIn("transplanted", h)        # its own stages, not embryo / larva
        self.assertNotIn(">larva<", h)
        self.assertIn('name="seed_lot_id_fk"', h)

    def test_its_rooms_are_growth_rooms_and_greenhouses(self):
        h = self.page("housing")
        self.assertIn('value="greenhouse"', h)
        self.assertIn('value="growth chamber"', h)

    def test_a_field_crop_database_has_a_nursery_and_plots(self):
        key = plant_module(self.a, "field_crop", uniq("Rice "))
        h = self.get_ok(self.a, f"/organisms/{key}?view=housing")
        self.assertIn('value="nursery"', h)
        self.assertIn('value="field"', h)
        self.assertIn("Plots", h)
        rules = {r["key"] for r in json.loads(one("select schedule_rules from organism_modules where key=?", key))}
        self.assertIn("transplant", rules)

    def test_every_view_opens(self):
        for view in ("animals", "housing", "lines", "crosses", "cohorts", "genotyping", "schedule",
                     "environment", "seeds", "settings"):
            self.page(view)

    def test_a_mouse_like_database_keeps_its_sexes_and_born(self):
        key = self.make_organism_module(self.a)
        h = self.get_ok(self.a, f"/organisms/{key}?view=animals")
        self.assertIn('name="sex"', h)
        self.assertIn("Born", h)
        self.assertNotIn("?view=seeds", h)


class ItsOwnWords(PlantCase):

    def test_configure_sets_and_clears_them(self):
        key = self.make_organism_module(self.a, capabilities=["housing", "individuals", "cohorts", "nursery"])
        url = f"/organisms/{key}"
        self.post(self.a, f"{url}/configure", {"born_word": "Hatched", "ended_word": "Gone",
                                                "stages": "egg, tadpole, froglet", "sexes": "",
                                                "location_kinds": "pond, tank"})
        settings = json.loads(one("select settings from organism_modules where key=?", key))
        self.assertEqual(settings["born_word"], "Hatched")
        self.assertEqual(settings["stages"], ["egg", "tadpole", "froglet"])
        h = self.get_ok(self.a, f"{url}?view=animals")
        self.assertIn("Hatched", h)
        self.assertNotIn('name="sex"', h)
        self.assertIn(">tadpole<", self.get_ok(self.a, f"{url}?view=cohorts"))
        self.assertIn('value="pond"', self.get_ok(self.a, f"{url}?view=housing"))
        self.post(self.a, f"{url}/configure", {"born_word": "", "ended_word": ""})
        settings = json.loads(one("select settings from organism_modules where key=?", key))
        self.assertNotIn("born_word", settings)
        self.assertIn("Born", self.get_ok(self.a, f"{url}?view=animals"))

    def test_harvested_ends_a_plant(self):
        plant = self.plant()
        self.post(self.a, f"{self.url}/animal/save", {"id": str(plant), "status": "harvested"})
        self.assertEqual(one("select death_on from organisms where id=?", plant), TODAY)


# ============================================================ seed lots

class SeedLots(PlantCase):

    def test_a_new_lot_has_all_of_it_left(self):
        lot = self.lot(amount="120", unit="mg", generation="T2")
        self.assertEqual(self.lot_value(lot, "amount_left"), 120)
        self.assertIn("120 / 120 mg", self.page("seeds"))

    def test_left_cannot_be_more_than_harvested(self):
        code = uniq("S")
        r = self.post(self.a, f"{self.url}/seedlot/save", {"code": code, "amount": "10", "amount_left": "12"})
        self.assertFlash(r, "cannot be more than was harvested", "error")
        self.assertEqual(count("organism_seed_lots", "code=?", code), 0)

    def test_a_code_is_one_lot(self):
        code = uniq("S")
        self.lot(code)
        r = self.post(self.a, f"{self.url}/seedlot/save", {"code": code})
        self.assertFlash(r, f"There is already a seed lot {code}.", "error")

    def test_germination_is_a_percentage(self):
        code = uniq("S")
        r = self.post(self.a, f"{self.url}/seedlot/save", {"code": code, "germination_pct": "140"})
        self.assertFlash(r, "Germination is a percentage", "error")

    def test_harvesting_a_plant_makes_the_next_generation_and_ends_it(self):
        line = self.line()
        sowing = self.sowing(line_id_fk=str(line), generation="T1")
        plant = self.plant(cohort_id_fk=str(sowing), line_id_fk=str(line))
        code = uniq("S")
        r = self.post(self.a, f"{self.url}/seedlot/save", {"code": code, "source_kind": "organism",
                                                           "source_id": str(plant), "amount": "50",
                                                           "end_source": "1"})
        self.assertFlash(r, f"Harvested {code} from", "success")
        lot = one("select id from organism_seed_lots where code=?", code)
        self.assertEqual(self.lot_value(lot, "generation"), "T2")
        self.assertEqual(self.lot_value(lot, "line_id_fk"), line)
        self.assertEqual(self.lot_value(lot, "source_kind"), "organism")
        self.assertEqual(one("select status from organisms where id=?", plant), "harvested")
        self.assertEqual(one("select death_on from organisms where id=?", plant), TODAY)

    def test_harvesting_a_cross_is_f1_and_collects_it(self):
        line = self.line()
        self.post(self.a, f"{self.url}/cross/save", {"code": "XC1" + uniq(""), "cross_type": "pair",
                                                     "dam_line_id_fk": str(line), "set_up_on": TODAY})
        cross = one("select max(id) from organism_crosses where module_id_fk=?", self.mid)
        lot = self.lot(source_kind="cross", source_id=str(cross), end_source="1")
        self.assertEqual(self.lot_value(lot, "generation"), "F1")
        self.assertEqual(self.lot_value(lot, "line_id_fk"), line)
        self.assertEqual(one("select collected_on from organism_crosses where id=?", cross), TODAY)

    def test_a_source_from_another_database_is_refused(self):
        other = plant_module(self.a)
        oid = one("select id from organism_modules where key=?", other)
        self.post(self.a, f"/organisms/{other}/animal/save", {"code": "X", "count": "1"})
        foreign = one("select id from organisms where module_id_fk=? and code='X'", oid)
        r = self.post(self.a, f"{self.url}/seedlot/save", {"code": uniq("S"), "source_kind": "organism",
                                                           "source_id": str(foreign)})
        self.assertFlash(r, "no longer in this database", "error")

    def test_sowing_from_a_lot_takes_its_line_generation_and_seed(self):
        line = self.line()
        lot = self.lot(line_id_fk=str(line), generation="T3", amount="100", unit="seeds")
        sowing = self.sowing(seed_lot_id_fk=str(lot), seed_taken="36")
        self.assertEqual(one("select line_id_fk from organism_cohorts where id=?", sowing), line)
        self.assertEqual(one("select generation from organism_cohorts where id=?", sowing), "T3")
        self.assertEqual(one("select count_initial from organism_cohorts where id=?", sowing), 36)
        self.assertEqual(self.lot_value(lot, "amount_left"), 64)
        self.assertIn(f"#lot-{lot}", self.page("cohorts"))

    def test_sowing_more_than_is_left_is_refused(self):
        lot = self.lot(amount="5", unit="mg")
        code = uniq("SOW")
        r = self.post(self.a, f"{self.url}/cohort/save", {"code": code, "seed_lot_id_fk": str(lot), "seed_taken": "8"})
        self.assertFlash(r, "Only 5 mg left", "error")
        self.assertEqual(count("organism_cohorts", "code=?", code), 0)
        self.assertEqual(self.lot_value(lot, "amount_left"), 5)

    def test_only_whoever_may_edit_a_lot_takes_seed_from_it(self):
        lot = self.lot(amount="10", unit="mg")             # the admin's
        code = uniq("SOW")
        r = self.post(self.m, f"{self.url}/cohort/save", {"code": code, "seed_lot_id_fk": str(lot), "seed_taken": "2"})
        self.assertEqual(count("organism_cohorts", "code=?", code), 0, flash_text(r))
        self.assertEqual(self.lot_value(lot, "amount_left"), 10)
        # Sowing from it without taking seed is only a link.
        self.post(self.m, f"{self.url}/cohort/save", {"code": code, "seed_lot_id_fk": str(lot)})
        self.assertEqual(count("organism_cohorts", "code=?", code), 1)

    def test_selection_counts_are_whole_numbers(self):
        code = uniq("S")
        r = self.post(self.a, f"{self.url}/seedlot/save", {"code": code, "resistant": "12.5", "sensitive": "4"})
        self.assertFlash(r, "whole numbers", "error")
        self.assertEqual(count("organism_seed_lots", "code=?", code), 0)

    def test_an_empty_lot_moves_down_and_off_the_schedule(self):
        lot = self.lot(amount="2", unit="g", harvested_on=days_ago(800))
        self.page("schedule")
        self.assertEqual(count("organism_due", "subject_kind='seedlot' and subject_id=? and done_on is null", lot), 1)
        self.sowing(seed_lot_id_fk=str(lot), seed_taken="2")
        self.page("schedule")
        self.assertEqual(count("organism_due", "subject_kind='seedlot' and subject_id=? and done_on is null", lot), 0)

    def test_deleting_a_lot_leaves_its_sowings(self):
        lot = self.lot(generation="F2")
        sowing = self.sowing(seed_lot_id_fk=str(lot))
        self.post(self.a, f"{self.url}/seedlot/{lot}/delete")
        self.assertEqual(count("organism_seed_lots", "id=?", lot), 0)
        self.assertIsNone(one("select seed_lot_id_fk from organism_cohorts where id=?", sowing))
        self.assertEqual(one("select generation from organism_cohorts where id=?", sowing), "F2")

    def test_a_line_with_seed_is_not_deleted(self):
        line = self.line()
        self.lot(line_id_fk=str(line))
        r = self.post(self.a, f"{self.url}/line/{line}/delete")
        self.assertFlash(r, "seed lot", "error")

    def test_the_labels_print(self):
        code = uniq("S")
        self.lot(code, generation="T2", storage_text="Seed cabinet")
        h = self.get_ok(self.a, f"/labels/cards/seeds/{self.key}")
        self.assertIn(code, h)
        self.assertIn("Seed cabinet", h)

    def test_search_finds_a_lot(self):
        code = uniq("SEEDLOT")
        self.lot(code)
        hits = self.a.get(f"/search?q={code}").get_json()["results"]
        self.assertTrue(any(code in h["label"] and "view=seeds" in h["url"] for h in hits), hits)

    def test_the_database_goes_with_its_seed(self):
        key = plant_module(self.a)
        mid = one("select id from organism_modules where key=?", key)
        self.post(self.a, f"/organisms/{key}/seedlot/save", {"code": "S1"})
        label = one("select label from organism_modules where id=?", mid)
        self.post(self.a, f"/organisms/{key}/delete", {"confirm": label})
        self.assertEqual(count("organism_seed_lots", "module_id_fk=?", mid), 0)


class Descent(PlantCase):

    def test_a_lot_reads_back_to_the_first(self):
        line = self.line()
        first = self.lot(uniq("S"), line_id_fk=str(line), generation="T1")
        sowing = self.sowing(seed_lot_id_fk=str(first))
        plant = self.plant(cohort_id_fk=str(sowing))
        second = self.lot(source_kind="organism", source_id=str(plant))
        self.assertEqual(self.lot_value(second, "generation"), "T2")
        sowing2 = self.sowing(seed_lot_id_fk=str(second))
        third = self.lot(source_kind="cohort", source_id=str(sowing2))
        self.assertEqual(self.lot_value(third, "generation"), "T3")
        with SessionLocal() as session:
            book = svc.seed_book(session, self.mid)
            steps = [s["kind"] for s in book.descent(book.lots[third])]
        self.assertEqual(steps, ["seedlot", "cohort", "seedlot", "organism", "cohort", "seedlot"])
        h = self.page("seeds")
        self.assertIn(f'href="#lot-{first}"', h)


# ============================================================ pure parts

class Generations(unittest.TestCase):
    def test_the_next_one(self):
        self.assertEqual(svc.next_generation("T1"), "T2")
        self.assertEqual(svc.next_generation("F9"), "F10")
        self.assertEqual(svc.next_generation("BC1F2"), "BC1F3")
        self.assertEqual(svc.next_generation("WT"), "WT")
        self.assertEqual(svc.next_generation(""), "")


class Segregation(unittest.TestCase):
    def verdict(self, r, s):
        return svc.segregation(r, s)["verdict"]

    def test_a_single_insertion(self):
        self.assertEqual(self.verdict(75, 25), "one_insertion")
        self.assertEqual(self.verdict(160, 40), "one_insertion")    # 4:1 is still 3:1 at p 0.05 with 200

    def test_two_insertions(self):
        self.assertEqual(self.verdict(150, 10), "two_insertions")

    def test_neither(self):
        self.assertEqual(self.verdict(50, 50), "neither")

    def test_too_few_to_tell(self):
        self.assertEqual(self.verdict(10, 1), "either")

    def test_all_resistant(self):
        self.assertEqual(self.verdict(40, 0), "homozygous")
        self.assertEqual(self.verdict(12, 0), "all_resistant_few")
        self.assertEqual(self.verdict(0, 30), "all_sensitive")

    def test_nothing_without_both_counts(self):
        self.assertIsNone(svc.segregation(None, 3))
        self.assertIsNone(svc.segregation(0, 0))


class TheSelectionPlateOnTheSheet(PlantCase):
    def test_it_says_3_to_1(self):
        self.lot(selection="kanamycin", resistant="76", sensitive="24")
        self.assertIn("fits 3:1, one insertion", self.page("seeds"))


# ============================================================ age bands and the plasmid

class AgeBands(PlantCase):
    def test_a_seedling_is_young(self):
        sowing = self.sowing()
        young = self.plant(birth_on=days_ago(3), cohort_id_fk=str(sowing))
        old = self.plant(birth_on=days_ago(100))
        h = self.page("animals")
        row = lambda pid: h[h.find(f'data-id="{pid}"'):h.find("</tr>", h.find(f'data-id="{pid}"'))]
        self.assertIn('data-stage="young"', row(young))
        self.assertIn('data-stage="old"', row(old))

    def test_configure_sets_and_checks_them(self):
        key = self.make_organism_module(self.a)
        url = f"/organisms/{key}"
        self.post(self.a, f"{url}/configure", {"stage_young": "10", "stage_old": "40"})
        self.assertEqual(json.loads(one("select settings from organism_modules where key=?", key))["stage_bands"],
                         {"young": 10, "old": 40})
        r = self.post(self.a, f"{url}/configure", {"stage_young": "50", "stage_old": "40"})
        self.assertFlash(r, "Young and old are days", "error")
        self.post(self.a, f"{url}/configure", {"stage_young": "", "stage_old": ""})
        self.assertNotIn("stage_bands", json.loads(one("select settings from organism_modules where key=?", key)))


class TheConstruct(PlantCase):
    def test_a_line_links_its_plasmid(self):
        row = self.make_plasmid(self.a, "pCAMBIA-GFP")
        number = one("select plasmid_id from plasmids where id=?", row)
        code = uniq("L")
        self.post(self.a, f"{self.url}/line/save", {"code": code, "attr_construct": str(number), "_full": "1"})
        h = self.page("lines")
        self.assertIn('id="org-plasmid-options"', h)
        self.assertIn(f'href="/plasmid/{number}"', h)


if __name__ == "__main__":
    unittest.main()
