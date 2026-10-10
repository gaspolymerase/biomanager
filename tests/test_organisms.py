"""Configurable organism databases (/organisms/…): the module builder and
its configuration, animals and housing, the rack grid, Add many, lines,
cohorts, crosses, frozen lots, readings, the derived schedule, genotyping,
and escaping of whatever people type into names."""
from __future__ import annotations

import json
import re
import unittest

from markupsafe import escape

from tests.base import *  # noqa: F401,F403
from tests.base import (AppTestCase, T, count, days_ago, days_ahead, errors, execute, flash_text,
                        flashes, location, one, rows, uniq)

ALL_VIEWS_CAPS = ["housing", "housing_grid", "individuals", "group_counts", "lines", "crosses",
                  "cohorts", "genotyping", "schedule", "environment", "preservation"]


def org_id(key: str, code: str):
    return one("select o.id from organisms o join organism_modules m on m.id=o.module_id_fk "
               "where m.key=? and o.code=?", key, code)


def module_row(key: str, column: str):
    return one(f"select {column} from organism_modules where key=?", key)


def hub_card(page: str, key: str) -> str:
    """The header of a module's card on the hub (not its sidebar link)."""
    start = page.find(f'href="/organisms/{key}">')
    assert start >= 0, f"no hub card for {key}"
    return page[start:page.find("</header>", start)]


def batch_of(table: str, record_id: int):
    """The newest audit batch that touched this record."""
    return one("select max(batch_id_fk) from audit_log where table_name=? and record_id=?", table, record_id)


class OrganismCase(AppTestCase):
    """One custom module (created by the admin) per TestCase."""

    capabilities = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.make_organism_module(cls.a, capabilities=cls.capabilities)
        cls.url = f"/organisms/{cls.key}"
        cls.mid = cls.organism_module_id(cls.key)

    def page(self, client, view=None) -> str:
        return self.get_ok(client, self.url + (f"?view={view}" if view else ""))

    def rack(self, rows_=2, cols=4, name=None) -> int:
        name = name or uniq("Rack")
        self.post(self.a, f"{self.url}/location/save", {"name": name, "kind": "rack",
                                                        "rows": str(rows_), "cols": str(cols)})
        found = one("select id from organism_locations where module_id_fk=? and name=?", self.mid, name)
        assert found, "rack not created"
        return found


# ============================================================ builder and hub

class ModuleBuilderTests(AppTestCase):

    def test_custom_module_is_created_and_opens_on_configure(self):
        label = uniq("Axolotls ")
        r = self.a.post("/organisms/new", data={"audience": "lab", "preset_key": "custom", "label": label,
                                                "capabilities": ["housing"]})
        key = location(r).split("?")[0].rsplit("/", 1)[1]
        self.assertIn("view=settings", location(r))
        self.assertEqual(module_row(key, "label"), label)
        self.assertEqual(module_row(key, "created_by"), self.admin)
        self.assertFlash(self.a.get(location(r)), f"Created the {label} database.", "success")

    def test_names_that_make_the_same_address_get_distinct_keys(self):
        label = uniq("Twins ")
        first = self.make_organism_module(self.a, label=label)
        second = self.make_organism_module(self.a, label=label + ".")
        self.assertNotEqual(first, second)
        self.assertTrue(second.startswith(first + "_"), (first, second))

    def test_reserved_word_never_becomes_a_key(self):
        key = self.make_organism_module(self.a, label="New.")
        self.assertNotEqual(key, "new")
        self.assertTrue(key.startswith("new_"), key)
        self.assertIn("Pick a starting point", self.get_ok(self.a, "/organisms/new"))

    def test_blank_name_is_refused(self):
        before = count("organism_modules")
        r = self.post(self.a, "/organisms/new", {"preset_key": "custom", "label": "  ",
                                                 "capabilities": ["housing"]})
        self.assertFlash(r, "Give the database a name.", "error")
        self.assertEqual(count("organism_modules"), before)

    def test_mouse_preset_prechecks_its_capabilities(self):
        h = self.get_ok(self.a, "/organisms/new?preset=mouse")
        checked = re.findall(r'name="capabilities" value="([^"]+)"[^>]*checked', h)
        self.assertIn("housing_grid", checked)
        self.assertIn("genotyping", checked)

    def test_preset_keeps_its_schedule_rules_and_fields(self):
        h = self.get_ok(self.a, "/organisms/new?preset=mouse")
        caps = re.findall(r'name="capabilities" value="([^"]+)"[^>]*checked', h)
        key = self.make_organism_module(self.a, label=uniq("Mice "), capabilities=caps,
                                        preset_key="mouse", identity_mode="individual")
        rules = {r["key"] for r in json.loads(module_row(key, "schedule_rules"))}
        self.assertLessEqual({"wean", "genotype", "retire"}, rules)
        self.assertEqual(count("organism_module_fields", "module_id_fk=? and key='ear_tag'",
                               self.organism_module_id(key)), 1)

    def test_preset_rules_dropped_without_the_schedule_capability(self):
        key = self.make_organism_module(self.a, label=uniq("Mice "), capabilities=["housing", "cohorts"],
                                        preset_key="mouse")
        self.assertEqual(json.loads(module_row(key, "schedule_rules")), [])

    def test_no_genotyping_tab_without_the_capability(self):
        with_it = self.make_organism_module(self.a)
        without = self.make_organism_module(self.a, capabilities=["housing", "group_counts"])
        self.assertIn("view=genotyping", self.get_ok(self.a, f"/organisms/{with_it}"))
        h = self.get_ok(self.a, f"/organisms/{without}")
        self.assertNotIn("view=genotyping", h)
        self.assertNotIn("view=schedule", h)

    def test_unknown_module_is_404(self):
        self.assertEqual(self.a.get(f"/organisms/{uniq('nope')}").status_code, 404)


class ConfigureTests(OrganismCase):

    def configure(self, client, **fields):
        data = {"_full": "1", "label": module_row(self.key, "label"), "identity_mode": "individual",
                "age_unit": "days", "capabilities": json.loads(module_row(self.key, "capabilities"))}
        data.update(fields)
        return self.post(client, f"{self.url}/configure", data)

    def test_turning_a_capability_off_hides_its_tab(self):
        caps = [c for c in json.loads(module_row(self.key, "capabilities")) if c != "genotyping"]
        self.assertFlash(self.configure(self.a, capabilities=caps), "Configuration saved.")
        self.assertNotIn("view=genotyping", self.page(self.a))
        self.configure(self.a, capabilities=caps + ["genotyping"])
        self.assertIn("view=genotyping", self.page(self.a))

    def test_member_cannot_configure_an_admins_module(self):
        label = module_row(self.key, "label")
        r = self.post(self.m, f"{self.url}/configure", {"label": "hijack"})
        self.assertIn("Only an admin or whoever created this database", " ".join(errors(r)))
        self.assertEqual(module_row(self.key, "label"), label)
        h = self.page(self.m, "settings")
        self.assertIn("Only an admin or", h)
        self.assertNotIn("Delete this database", h)

    def test_creator_member_can_configure_own_module(self):
        key = self.make_organism_module(self.m, label=uniq("Mine "))
        new_label = uniq("Renamed ")
        self.post(self.m, f"/organisms/{key}/configure", {"_full": "1", "label": new_label,
                                                          "capabilities": ["housing"]})
        key = one("select key from organism_modules where label=?", new_label)    # its address follows the name
        self.assertTrue(key)
        self.post(self.m, f"/organisms/{key}/location/save", {"name": "My rack", "rows": "2", "cols": "2"})
        self.assertEqual(count("organism_locations", "module_id_fk=? and name='My rack'",
                               self.organism_module_id(key)), 1)

    def test_hidden_module_is_marked_hidden_on_the_hub(self):
        key = self.make_organism_module(self.a, label=uniq("Hidden "))
        self.post(self.a, f"/organisms/{key}/configure", {"_full": "1", "label": module_row(key, "label"),
                                                          "capabilities": ["housing"], "disabled": "1"})
        self.assertEqual(module_row(key, "enabled"), 0)
        h = self.get_ok(self.a, "/organisms/")
        self.assertRegex(hub_card(h, key), r'badge badge-quiet">hidden</span>')

    def test_add_field_then_duplicate_is_refused(self):
        label = uniq("Tail ")
        r = self.post(self.a, f"{self.url}/field/add", {"entity": "organism", "label": label, "field_type": "text"})
        self.assertFlash(r, f"Added field {label}.", "success")
        r = self.post(self.a, f"{self.url}/field/add", {"entity": "organism", "label": label, "field_type": "number"})
        self.assertIn("already has a field", " ".join(errors(r)))
        self.assertEqual(count("organism_module_fields", "module_id_fk=? and label=?", self.mid, label), 1)

    def test_field_definitions_are_validated(self):
        r = self.post(self.a, f"{self.url}/field/add", {"entity": "organism", "label": uniq("Diet"),
                                                         "field_type": "select", "options": ""})
        self.assertIn("needs its choices", " ".join(errors(r)))
        r = self.post(self.a, f"{self.url}/field/add", {"entity": "organism", "label": uniq("Mass"),
                                                         "field_type": "number", "default_value": "heavy"})
        self.assertIn("must be a number", " ".join(errors(r)))
        r = self.post(self.a, f"{self.url}/field/add", {"entity": "organism", "label": "", "field_type": "text"})
        self.assertIn("Give the field a label", " ".join(errors(r)))

    def test_deleting_a_field_keeps_stored_values(self):
        label = uniq("Mark ")
        self.post(self.a, f"{self.url}/field/add", {"entity": "organism", "label": label, "field_type": "text"})
        fid, fkey = rows("select id, key from organism_module_fields where module_id_fk=? and label=?",
                         self.mid, label)[0]
        animal = self.make_animal(self.a, self.key, owner=self.admin, **{f"attr_{fkey}": "blue"})
        r = self.post(self.a, f"{self.url}/field/{fid}/delete")
        self.assertFlash(r, "Stored values are kept", "success")
        self.assertEqual(count("organism_module_fields", "id=?", fid), 0)
        self.assertEqual(json.loads(one("select attrs from organisms where id=?", animal))[fkey], "blue")

    def test_move_up_and_down_set_the_order_of_the_sheets_columns(self):
        key = self.make_organism_module(self.a, label=uniq("Ordered "))
        mid = self.organism_module_id(key)
        labels = [uniq("Tail "), uniq("Diet "), uniq("Coat ")]
        for label in labels:
            self.post(self.a, f"/organisms/{key}/field/add",
                      {"entity": "organism", "label": label, "field_type": "text", "show_in_table": "1"})
        ids = [one("select id from organism_module_fields where module_id_fk=? and label=?", mid, label)
               for label in labels]

        def columns():
            html = self.get_ok(self.a, f"/organisms/{key}")
            thead = html[html.index("<thead>"):html.index("</thead>")]
            # The code (ID) column stays put when the others are dragged.
            self.assertRegex(thead, r'data-sort-key="code"[^>]*data-dt-fixed')
            keys = re.findall(r'data-sort-key="attr_([^"]+)"', thead)
            return [one("select label from organism_module_fields where module_id_fk=? and key=?", mid, k)
                    for k in keys]

        self.assertEqual(columns(), labels)
        settings = self.get_ok(self.a, f"/organisms/{key}?view=settings")
        self.assertIn(f"/organisms/{key}/field/{ids[2]}/move", settings)
        r = self.a.post(f"/organisms/{key}/field/{ids[2]}/move", data={"step": "-1"})
        self.assertEqual(location(r), f"/organisms/{key}?view=settings")
        self.assertEqual(columns(), [labels[0], labels[2], labels[1]])
        self.post(self.a, f"/organisms/{key}/field/{ids[0]}/move", {"step": "1"})
        self.assertEqual(columns(), [labels[2], labels[0], labels[1]])
        # Past either end, nothing moves.
        self.post(self.a, f"/organisms/{key}/field/{ids[2]}/move", {"step": "-1"})
        self.assertEqual(columns(), [labels[2], labels[0], labels[1]])
        # A member can't reorder the lab's columns.
        r = self.post(self.m, f"/organisms/{key}/field/{ids[1]}/move", {"step": "-1"})
        self.assertTrue(errors(r))
        self.assertEqual(columns(), [labels[2], labels[0], labels[1]])

    def test_member_cannot_add_or_delete_fields(self):
        label = uniq("Kept ")
        self.post(self.a, f"{self.url}/field/add", {"entity": "housing", "label": label, "field_type": "text"})
        fid = one("select id from organism_module_fields where module_id_fk=? and label=?", self.mid, label)
        self.post(self.m, f"{self.url}/field/add", {"entity": "organism", "label": uniq("Student"),
                                                     "field_type": "text"})
        r = self.post(self.m, f"{self.url}/field/{fid}/delete")
        self.assertTrue(errors(r))
        self.assertEqual(count("organism_module_fields", "id=?", fid), 1)
        self.assertEqual(count("organism_module_fields", "module_id_fk=? and label like 'Student%'", self.mid), 0)

    def test_required_and_number_fields_are_enforced_on_save(self):
        req, num = uniq("Req "), uniq("Weight ")
        self.post(self.a, f"{self.url}/field/add", {"entity": "cohort", "label": req, "field_type": "text",
                                                     "required": "1"})
        self.post(self.a, f"{self.url}/field/add", {"entity": "cohort", "label": num, "field_type": "number"})
        req_key = one("select key from organism_module_fields where module_id_fk=? and label=?", self.mid, req)
        num_key = one("select key from organism_module_fields where module_id_fk=? and label=?", self.mid, num)
        code = uniq("CO")
        r = self.post(self.a, f"{self.url}/cohort/save", {"code": code, "_full": "1", f"attr_{req_key}": ""})
        self.assertTrue(errors(r))
        self.assertEqual(count("organism_cohorts", "module_id_fk=? and code=?", self.mid, code), 0)
        r = self.post(self.a, f"{self.url}/cohort/save", {"code": code, "_full": "1", f"attr_{req_key}": "x",
                                                           f"attr_{num_key}": "heavy"})
        self.assertTrue(errors(r))
        self.post(self.a, f"{self.url}/cohort/save", {"code": code, "_full": "1", f"attr_{req_key}": "x",
                                                      f"attr_{num_key}": "21.5"})
        attrs = json.loads(one("select attrs from organism_cohorts where module_id_fk=? and code=?", self.mid, code))
        self.assertEqual(attrs[num_key], 21.5)


class ModuleDeleteAndBuiltinTests(AppTestCase):

    def test_delete_needs_the_exact_name(self):
        key = self.make_organism_module(self.a)
        label = module_row(key, "label")
        r = self.post(self.a, f"/organisms/{key}/delete", {"confirm": label.lower()})
        self.assertFlash(r, "Type the database name exactly", "error")
        self.assertIsNotNone(self.organism_module_id(key))

    def test_delete_removes_the_module_and_its_records(self):
        key = self.make_organism_module(self.a)
        mid = self.organism_module_id(key)
        unit = self.make_housing(self.a, key, owner=self.admin)
        self.make_animal(self.a, key, owner=self.admin, housing_id_fk=unit)
        self.post(self.a, f"/organisms/{key}/field/add", {"entity": "organism", "label": "Tag", "field_type": "text"})
        r = self.post(self.a, f"/organisms/{key}/delete", {"confirm": module_row(key, "label")})
        self.assertFlash(r, "Deleted the", "success")
        self.assertIsNone(self.organism_module_id(key))
        for table in ("organisms", "organism_housing", "organism_events", "organism_module_fields"):
            self.assertEqual(count(table, "module_id_fk=?", mid), 0, table)
        self.assertEqual(count("app_settings", "key=?", f"org_schedule_fresh:{mid}"), 0)

    def test_member_cannot_delete_someone_elses_module(self):
        key = self.make_organism_module(self.a)
        r = self.m.post(f"/organisms/{key}/delete", data={"confirm": module_row(key, "label")})
        self.assertEqual(r.status_code, 403)
        self.assertIsNotNone(self.organism_module_id(key))

    def test_admin_renames_a_builtin_and_blank_restores_it(self):
        name = uniq("Our mice ")
        self.addCleanup(self.a.post, "/organisms/builtin/colony/rename", data={"label": ""})
        r = self.post(self.a, "/organisms/builtin/colony/rename", {"label": name})
        self.assertFlash(r, f"Renamed to {name}.", "success")
        self.assertIn(name, self.get_ok(self.a, "/organisms/"))
        self.post(self.a, "/organisms/builtin/colony/rename", {"label": ""})
        self.assertNotIn(name, self.get_ok(self.a, "/organisms/"))

    def test_member_cannot_rename_a_builtin(self):
        name = uniq("Student mice ")
        r = self.post(self.m, "/organisms/builtin/colony/rename", {"label": name})
        self.assertFlash(r, "Only an admin can rename", "error")
        self.assertNotIn(name, self.get_ok(self.a, "/organisms/"))
        self.assertNotIn("/organisms/builtin/", self.get_ok(self.m, "/organisms/"))

    def test_unknown_builtin_is_404(self):
        self.assertEqual(self.a.post("/organisms/builtin/nothing/rename", data={"label": "x"}).status_code, 404)


class HubAndViewsTests(OrganismCase):
    capabilities = ALL_VIEWS_CAPS

    def test_hub_lists_the_module_with_its_census(self):
        key = self.make_organism_module(self.a)
        self.make_animal(self.a, key, owner=self.admin)
        self.make_animal(self.a, key, owner=self.admin, status="removed")
        h = self.get_ok(self.a, "/organisms/")
        self.assertIn(module_row(key, "label"), hub_card(h, key))
        self.assertRegex(hub_card(h, key), r'badge badge-quiet">1 newt</span>')

    def test_every_view_renders_for_admin_and_member(self):
        self.make_animal(self.a, self.key, owner=self.admin)
        for view in ("animals", "housing", "lines", "crosses", "cohorts", "genotyping", "schedule",
                     "environment", "preservation", "settings"):
            for client in (self.a, self.m):
                h = self.page(client, view)
                self.assertIn(f"view={view}", h, view)

    def test_unknown_view_falls_back_to_the_first_tab(self):
        self.assertEqual(self.a.get(f"{self.url}?view=bogus").status_code, 200)


# ============================================================ animals and housing

class AnimalAndHousingTests(OrganismCase):

    def test_animal_and_housing_are_saved(self):
        unit_code = uniq("T")
        r = self.post(self.a, f"{self.url}/housing/save", {"code": unit_code, "owner": self.admin,
                                                           "purpose": "stock", "_full": "1"})
        self.assertFlash(r, f"Saved {unit_code}.", "success")
        unit = one("select id from organism_housing where module_id_fk=? and code=?", self.mid, unit_code)
        code = uniq("N")
        r = self.post(self.a, f"{self.url}/animal/save", {"code": code, "status": "alive", "sex": "female",
                                                          "housing_id_fk": unit, "owner": self.admin,
                                                          "birth_on": days_ago(10), "_full": "1"})
        self.assertFlash(r, f"Saved {code}.", "success")
        self.assertEqual(rows("select housing_id_fk, sex, birth_on, count from organisms where id=?",
                              org_id(self.key, code)), [(unit, "female", days_ago(10), 1)])

    def test_blank_code_on_an_individual_gets_the_next_free_id(self):
        self.post(self.a, f"{self.url}/animal/save", {"code": "", "status": "alive", "notes": "auto-me"})
        code = one("select code from organisms where module_id_fk=? and notes='auto-me'", self.mid)
        self.assertRegex(code or "", r"-A\d{3}$")

    def test_reference_to_another_modules_housing_is_dropped(self):
        other = self.make_organism_module(self.a)
        foreign = self.make_housing(self.a, other)
        animal = self.make_animal(self.a, self.key, housing_id_fk=foreign, owner=self.admin)
        self.assertIsNone(one("select housing_id_fk from organisms where id=?", animal))

    def test_member_cannot_edit_someone_elses_animal(self):
        animal = self.make_animal(self.a, self.key, owner=self.admin, notes="orig")
        r = self.post(self.m, f"{self.url}/animal/save", {"id": animal, "notes": "hijack"})
        self.assertIn(f"belongs to {self.admin}", " ".join(errors(r)))
        self.assertAutosaveRefused(self.autosave(self.m, f"{self.url}/animal/save", {"id": animal, "notes": "x"}), 403)
        self.assertEqual(one("select notes from organisms where id=?", animal), "orig")

    def assertAutosaveRefused(self, r, status):
        self.assertEqual(r.status_code, status)
        self.assertFalse(r.get_json()["ok"])

    def test_admin_can_edit_a_members_animal(self):
        animal = self.make_animal(self.m, self.key, owner=self.member)
        self.assertSaved(self.autosave(self.a, f"{self.url}/animal/save", {"id": animal, "notes": "by admin"}))
        self.assertEqual(one("select notes from organisms where id=?", animal), "by admin")

    def test_member_can_edit_own_and_unowned_animals(self):
        mine = self.make_animal(self.m, self.key, owner=self.member)
        unowned = self.make_animal(self.a, self.key, owner="")
        for animal in (mine, unowned):
            self.assertSaved(self.autosave(self.m, f"{self.url}/animal/save", {"id": animal, "notes": "ok"}))
            self.assertEqual(one("select notes from organisms where id=?", animal), "ok")

    def test_member_cannot_edit_or_delete_someone_elses_housing(self):
        unit = self.make_housing(self.a, self.key, owner=self.admin, purpose="stock")
        r = self.post(self.m, f"{self.url}/housing/save", {"id": unit, "purpose": "experiment"})
        self.assertTrue(errors(r))
        r = self.post(self.m, f"{self.url}/housing/{unit}/delete")
        self.assertTrue(errors(r))
        self.assertEqual(rows("select purpose from organism_housing where id=?", unit), [("stock",)])

    def test_member_cannot_delete_someone_elses_animal(self):
        animal = self.make_animal(self.a, self.key, owner=self.admin)
        r = self.post(self.m, f"{self.url}/animal/{animal}/delete")
        self.assertTrue(errors(r))
        self.assertEqual(count("organisms", "id=?", animal), 1)

    def test_member_sees_someone_elses_rows_locked(self):
        self.make_animal(self.a, self.key, owner=self.admin)
        h = self.page(self.m, "animals")
        self.assertIn("sheet-lock", h)
        self.assertIn("&#34;locked&#34;: true", h)

    def test_deleting_housing_keeps_its_residents_unassigned(self):
        unit = self.make_housing(self.m, self.key, owner=self.member)
        resident = self.make_animal(self.a, self.key, owner=self.admin, housing_id_fk=unit)
        r = self.post(self.m, f"{self.url}/housing/{unit}/delete")
        self.assertIn("now without a tank", flash_text(r))
        self.assertEqual(count("organism_housing", "id=?", unit), 0)
        self.assertEqual(rows("select housing_id_fk from organisms where id=?", resident), [(None,)])

    def test_count_below_one_is_refused(self):
        code = uniq("G")
        r = self.post(self.a, f"{self.url}/animal/save", {"code": code, "count": "0", "owner": self.admin})
        self.assertIn("Count must be a whole number", " ".join(errors(r)))
        self.assertIsNone(org_id(self.key, code))

    def test_ending_status_stamps_the_date_and_reviving_clears_it(self):
        animal = self.make_animal(self.a, self.key, owner=self.admin)
        self.post(self.a, f"{self.url}/animal/save", {"id": animal, "status": "removed"})
        self.assertEqual(one("select death_on from organisms where id=?", animal), T)
        r = self.autosave(self.a, f"{self.url}/animal/save", {"id": animal, "status": "alive"})
        self.assertSaved(r)
        self.assertIs(r.get_json()["row"]["active"], True)
        self.assertIsNone(one("select death_on from organisms where id=?", animal))

    def test_one_cell_autosave_leaves_other_fields_alone(self):
        unit = self.make_housing(self.a, self.key, owner=self.admin, purpose="stock", needs_attention="1")
        self.assertSaved(self.autosave(self.a, f"{self.url}/housing/save", {"id": unit, "notes": "inline"}))
        self.assertEqual(rows("select purpose, needs_attention, active, notes from organism_housing where id=?",
                              unit), [("stock", 1, 1, "inline")])


class PlacementTests(OrganismCase):

    def test_housing_is_saved_at_the_typed_position(self):
        rack = self.rack(4, 6)
        unit = self.make_housing(self.a, self.key, owner=self.admin, location_id_fk=rack, position="B3")
        self.assertEqual(rows("select location_id_fk, row, col from organism_housing where id=?", unit),
                         [(rack, 2, 3)])

    def test_taken_position_is_refused_and_left_unplaced(self):
        rack = self.rack()
        holder = uniq("H")
        self.make_housing(self.a, self.key, holder, owner=self.admin, location_id_fk=rack, position="A1")
        code = uniq("H")
        r = self.post(self.a, f"{self.url}/housing/save", {"code": code, "owner": self.admin, "_full": "1",
                                                           "location_id_fk": rack, "position": "A1"})
        self.assertIn(f"already holds {holder}", " ".join(errors(r)))
        self.assertEqual(rows("select row, col from organism_housing where module_id_fk=? and code=?",
                              self.mid, code), [(None, None)])

    def test_position_outside_the_rack_is_refused(self):
        rack = self.rack(2, 2)
        unit = self.make_housing(self.a, self.key, owner=self.admin, location_id_fk=rack, position="A1")
        r = self.autosave(self.a, f"{self.url}/housing/save", {
            "id": unit, "location_id_fk": rack, "location_id_fk_was": rack, "position": "D6", "position_was": "A1"})
        self.assertRefused(r)
        self.assertEqual(rows("select row, col from organism_housing where id=?", unit), [(1, 1)])

    def test_grid_drag_places_a_unit_and_logs_a_move(self):
        rack = self.rack()
        unit = self.make_housing(self.a, self.key, owner=self.admin)
        r = self.a.post(f"{self.url}/housing/{unit}/place", data={"rack_id": rack, "row": "2", "col": "3"})
        self.assertEqual(r.get_json(), {"ok": True})
        self.assertEqual(rows("select location_id_fk, row, col from organism_housing where id=?", unit),
                         [(rack, 2, 3)])
        self.assertGreaterEqual(count("organism_events", "module_id_fk=? and subject_id=? and event_type='move'",
                                      self.mid, unit), 1)

    def test_drag_onto_a_cell_held_by_someone_elses_unit_is_refused(self):
        rack = self.rack()
        self.make_housing(self.a, self.key, owner=self.admin, location_id_fk=rack, position="A2")
        mine = self.make_housing(self.m, self.key, owner=self.member, location_id_fk=rack, position="B1")
        r = self.m.post(f"{self.url}/housing/{mine}/place", data={"rack_id": rack, "row": "1", "col": "2"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(rows("select row, col from organism_housing where id=?", mine), [(2, 1)])

    def test_drag_onto_an_editable_occupant_swaps(self):
        rack = self.rack()
        a = self.make_housing(self.a, self.key, owner=self.admin, location_id_fk=rack, position="A1")
        b = self.make_housing(self.a, self.key, owner=self.admin, location_id_fk=rack, position="A2")
        self.a.post(f"{self.url}/housing/{a}/place", data={"rack_id": rack, "row": "1", "col": "2"})
        self.assertEqual(rows("select id, row, col from organism_housing where id in (?,?) order by id", a, b),
                         [(a, 1, 2), (b, 1, 1)])

    def test_member_cannot_drag_someone_elses_unit(self):
        rack = self.rack()
        unit = self.make_housing(self.a, self.key, owner=self.admin)
        r = self.m.post(f"{self.url}/housing/{unit}/place", data={"rack_id": rack, "row": "1", "col": "1"})
        self.assertEqual(r.status_code, 403)
        self.assertIsNone(one("select row from organism_housing where id=?", unit))

    def test_drag_outside_the_rack_is_refused(self):
        rack = self.rack(2, 2)
        unit = self.make_housing(self.a, self.key, owner=self.admin)
        r = self.a.post(f"{self.url}/housing/{unit}/place", data={"rack_id": rack, "row": "3", "col": "1"})
        self.assertEqual(r.status_code, 400)

    def test_shrinking_a_rack_unplaces_units_outside_it(self):
        name = uniq("Rack")
        rack = self.rack(4, 4, name=name)
        unit = self.make_housing(self.a, self.key, owner=self.admin, location_id_fk=rack, position="D4")
        self.post(self.a, f"{self.url}/location/save", {"id": rack, "name": name, "rows": "2", "cols": "2"})
        self.assertEqual(rows("select location_id_fk, row, col from organism_housing where id=?", unit),
                         [(rack, None, None)])

    def test_a_rack_records_who_added_it_and_they_may_change_it_once_handed_over(self):
        name = uniq("Rack")
        rack = self.rack(2, 2, name=name)
        self.assertEqual(one("select created_by from organism_locations where id=?", rack), self.admin)
        self.post(self.m, f"{self.url}/location/save", {"id": rack, "name": name, "rows": "3", "cols": "3"})
        self.assertEqual(one("select rows from organism_locations where id=?", rack), 2)   # not theirs
        self.post(self.a, "/admin/racks/assign", {"kind": "org_location", "id": rack, "creator": self.member})
        self.post(self.m, f"{self.url}/location/save", {"id": rack, "name": name, "rows": "3", "cols": "3"})
        self.assertEqual(one("select rows, cols from organism_locations where id=?", rack), 3)
        self.assertIn(name, self.get_ok(self.a, "/settings"))

    def test_the_rack_form_starts_with_a_real_size_and_offers_edit(self):
        rack = self.rack()
        html = self.page(self.a, "housing")
        self.assertIn('name="rows" min="1" value="8"', html)
        self.assertIn("data-location-edit", html)

    def test_member_cannot_add_or_delete_racks(self):
        rack = self.rack()
        name = uniq("Student rack")
        self.post(self.m, f"{self.url}/location/save", {"name": name, "kind": "rack", "rows": "2", "cols": "2"})
        self.post(self.m, f"{self.url}/location/{rack}/delete")
        self.assertEqual(count("organism_locations", "name=?", name), 0)
        self.assertEqual(count("organism_locations", "id=?", rack), 1)


class BulkTests(OrganismCase):

    def test_bulk_status_stamps_death_and_undo_restores(self):
        ids = [self.make_animal(self.a, self.key, owner=self.admin, status="alive") for _ in range(2)]
        r = self.post(self.a, f"{self.url}/animals/bulk", {"action": "set", "field": "status",
                                                           "value": "removed", "selected_ids": ids})
        self.assertFlash(r, "Set status on 2 newts.", "success")
        self.assertEqual(rows("select status, death_on from organisms where id in (?,?)", *ids),
                         [("removed", T)] * 2)
        self.post(self.a, f"/batches/{batch_of('organisms', ids[0])}/undo")
        self.assertEqual(rows("select status, death_on from organisms where id in (?,?)", *ids),
                         [("alive", None)] * 2)

    def test_bulk_delete_skips_someone_elses_records(self):
        theirs = self.make_animal(self.a, self.key, owner=self.admin)
        mine = self.make_animal(self.m, self.key, owner=self.member)
        r = self.post(self.m, f"{self.url}/animals/bulk", {"action": "delete", "selected_ids": [theirs, mine]})
        self.assertIn("1 belong to someone else", flash_text(r))
        self.assertEqual(count("organisms", "id=?", theirs), 1)
        self.assertEqual(count("organisms", "id=?", mine), 0)

    def custom_field(self, field_type="number", **extra):
        label = uniq("Passage ")
        self.post(self.a, f"{self.url}/field/add", {"entity": "organism", "label": label, "field_type": field_type,
                                                     "show_in_table": "1", **extra})
        return rows("select key from organism_module_fields where module_id_fk=? and label=?", self.mid, label)[0][0]

    def test_set_field_sets_a_custom_field_on_the_ticked_records_in_one_undoable_batch(self):
        key = self.custom_field()
        ids = [self.make_animal(self.a, self.key, owner=self.admin) for _ in range(3)]
        r = self.post(self.a, f"{self.url}/animals/bulk", {"action": "set", "field": f"attr_{key}", "value": "14",
                                                           "selected_ids": ids})
        self.assertIn("on 3 newts", flash_text(r))
        values = [json.loads(a or "{}").get(key) for (a,) in rows(
            f"select attrs from organisms where id in ({','.join('?' * 3)}) order by id", *ids)]
        self.assertEqual(values, [14, 14, 14])
        r = self.post(self.a, f"{self.url}/animals/bulk", {"action": "set", "field": f"attr_{key}", "value": "lots",
                                                           "selected_ids": ids})
        self.assertIn("must be a number", " ".join(errors(r)))

    def test_a_custom_column_is_edited_in_the_sheet_and_a_stale_cell_keeps_a_later_change(self):
        key = self.custom_field()
        animal = self.make_animal(self.a, self.key, owner=self.admin, **{f"attr_{key}": "12"})
        self.assertIn(f'name="attr_{key}" form="row-a-{animal}"', self.page(self.a, "animals"))
        r = self.autosave(self.a, f"{self.url}/animal/save", {"id": animal, f"attr_{key}": "13", f"attr_{key}_was": "12"})
        self.assertTrue(r.get_json()["ok"])
        # A row still showing 12, saving another cell, leaves 13 alone.
        self.autosave(self.a, f"{self.url}/animal/save", {"id": animal, "notes": "fed", f"attr_{key}": "12",
                                                           f"attr_{key}_was": "12"})
        self.assertEqual(json.loads(rows("select attrs from organisms where id=?", animal)[0][0])[key], 13)

    def test_bulk_with_nothing_selected_changes_nothing(self):
        r = self.post(self.a, f"{self.url}/animals/bulk", {"action": "delete"})
        self.assertEqual(flashes(r)[0][0], "info")

    def test_bulk_ignores_another_modules_ids(self):
        other = self.make_organism_module(self.a)
        foreign = self.make_animal(self.a, other, owner=self.admin)
        self.post(self.a, f"{self.url}/animals/bulk", {"action": "delete", "selected_ids": [foreign]})
        self.assertEqual(count("organisms", "id=?", foreign), 1)

    def test_bulk_housing_delete_keeps_residents_and_undo_restores(self):
        units = [self.make_housing(self.a, self.key, owner=self.admin) for _ in range(2)]
        resident = self.make_animal(self.a, self.key, owner=self.admin, housing_id_fk=units[0])
        r = self.post(self.a, f"{self.url}/housing/bulk", {"action": "delete", "selected_ids": units})
        self.assertFlash(r, "Deleted 2 tanks.", "success")
        self.assertEqual(count("organism_housing", f"id in ({units[0]},{units[1]})"), 0)
        self.assertIsNone(one("select housing_id_fk from organisms where id=?", resident))
        self.post(self.a, f"/batches/{batch_of('organism_housing', units[0])}/undo")
        self.assertEqual(count("organism_housing", f"id in ({units[0]},{units[1]})"), 2)
        self.assertEqual(one("select housing_id_fk from organisms where id=?", resident), units[0])

    def test_bulk_housing_move_unplaces(self):
        rack_a, rack_b = self.rack(), self.rack()
        unit = self.make_housing(self.a, self.key, owner=self.admin, location_id_fk=rack_a, position="A1")
        self.post(self.a, f"{self.url}/housing/bulk", {"action": "set", "field": "location_id_fk",
                                                       "value": rack_b, "selected_ids": [unit]})
        self.assertEqual(rows("select location_id_fk, row from organism_housing where id=?", unit), [(rack_b, None)])


# ============================================================ add many

class AddManyTests(OrganismCase):

    def test_how_many_makes_consecutive_codes_with_the_same_fields(self):
        unit_code = uniq("T")
        unit = self.make_housing(self.a, self.key, unit_code, owner=self.admin)
        stem = uniq("NW") + "-"
        r = self.post(self.a, f"{self.url}/animal/save", {"code": f"{stem}010", "how_many": "5", "sex": "F",
                                                          "housing_id_fk": unit, "status": "alive",
                                                          "owner": self.admin, "_full": "1"})
        made = [c for (c,) in rows("select code from organisms where housing_id_fk=? and sex='F' order by code", unit)]
        self.assertEqual(made, [f"{stem}{n:03d}" for n in range(10, 15)])
        self.assertFlash(r, f"Created 5 newts: {stem}010–{stem}014 in {unit_code}.", "success")

    def test_add_many_is_one_batch_that_undo_removes(self):
        stem = uniq("UN") + "-"
        self.post(self.a, f"{self.url}/animal/save", {"code": f"{stem}1", "how_many": "3", "owner": self.admin})
        ids = [org_id(self.key, f"{stem}{n}") for n in (1, 2, 3)]
        self.assertTrue(all(ids), ids)
        batch = batch_of("organisms", ids[0])
        self.assertEqual({batch_of("organisms", i) for i in ids}, {batch})
        self.assertEqual(count("audit_log", "batch_id_fk=? and action='create'", batch), 3)
        self.post(self.a, f"/batches/{batch}/undo")
        self.assertEqual(count("organisms", f"id in ({','.join(map(str, ids))})"), 0)

    def test_overlapping_codes_are_refused(self):
        stem = uniq("OV") + "-"
        self.make_animal(self.a, self.key, f"{stem}3", owner=self.admin)
        r = self.post(self.a, f"{self.url}/animal/save", {"code": f"{stem}1", "how_many": "3"})
        self.assertIn(f"{stem}3", " ".join(errors(r)))
        self.assertIsNone(org_id(self.key, f"{stem}1"))

    def test_more_than_fifty_is_refused(self):
        code = uniq("BIG") + "-1"
        r = self.post(self.a, f"{self.url}/animal/save", {"code": code, "how_many": "51"})
        self.assertIn("from 1 to 50", " ".join(errors(r)))
        self.assertIsNone(org_id(self.key, code))

    def test_an_invalid_field_refuses_the_whole_batch(self):
        label = uniq("Grams ")
        self.post(self.a, f"{self.url}/field/add", {"entity": "organism", "label": label, "field_type": "number"})
        fkey = one("select key from organism_module_fields where module_id_fk=? and label=?", self.mid, label)
        stem = uniq("IV") + "-"
        r = self.post(self.a, f"{self.url}/animal/save", {"code": f"{stem}1", "how_many": "3",
                                                          f"attr_{fkey}": "heavy"})
        self.assertTrue(errors(r))
        self.assertEqual(count("organisms", "module_id_fk=? and code like ?", self.mid, f"{stem}%"), 0)

    def test_how_many_is_ignored_on_edit(self):
        animal = self.make_animal(self.a, self.key, owner=self.admin)
        before = count("organisms", "module_id_fk=?", self.mid)
        self.post(self.a, f"{self.url}/animal/save", {"id": animal, "how_many": "5", "notes": "edited"})
        self.assertEqual(count("organisms", "module_id_fk=?", self.mid), before)
        self.assertEqual(one("select notes from organisms where id=?", animal), "edited")

    def test_housing_add_many_places_side_by_side_skipping_taken_cells(self):
        rack = self.rack(2, 4)
        self.make_housing(self.a, self.key, owner=self.admin, location_id_fk=rack, position="A3")
        stem = uniq("RK") + "-"
        r = self.post(self.a, f"{self.url}/housing/save", {"code": f"{stem}01", "how_many": "4",
                                                           "location_id_fk": rack, "position": "A2",
                                                           "owner": self.admin, "_full": "1"})
        placed = rows("select code, row, col from organism_housing where module_id_fk=? and code like ? order by code",
                      self.mid, f"{stem}%")
        self.assertEqual(placed, [(f"{stem}01", 1, 2), (f"{stem}02", 1, 4), (f"{stem}03", 2, 1), (f"{stem}04", 2, 2)])
        self.assertIn(f"Created 4 tanks: {stem}01–{stem}04", flash_text(r))
        batch = batch_of("organism_housing", one("select id from organism_housing where module_id_fk=? and code=?",
                                                 self.mid, f"{stem}01"))
        self.assertEqual(count("audit_log", "batch_id_fk=? and table_name='organism_housing' and action='create'",
                               batch), 4)

    def test_housing_that_does_not_fit_is_unplaced_and_reported(self):
        rack = self.rack(1, 2)
        self.make_housing(self.a, self.key, owner=self.admin, location_id_fk=rack, position="A2")
        stem = uniq("OF") + "-"
        r = self.post(self.a, f"{self.url}/housing/save", {"code": f"{stem}1", "how_many": "3",
                                                           "location_id_fk": rack, "owner": self.admin})
        placed = rows("select code, row, col from organism_housing where module_id_fk=? and code like ? order by code",
                      self.mid, f"{stem}%")
        self.assertEqual(placed, [(f"{stem}1", 1, 1), (f"{stem}2", None, None), (f"{stem}3", None, None)])
        self.assertTrue(any("room for 1 of 3" in e for e in errors(r)), flashes(r))

    def test_housing_add_many_refuses_a_taken_or_bad_start(self):
        rack = self.rack(2, 2)
        holder = uniq("HD")
        self.make_housing(self.a, self.key, holder, owner=self.admin, location_id_fk=rack, position="B1")
        stem = uniq("TS") + "-"
        r = self.post(self.a, f"{self.url}/housing/save", {"code": f"{stem}1", "how_many": "2",
                                                           "location_id_fk": rack, "position": "B1"})
        self.assertIn(holder, " ".join(errors(r)))
        r = self.post(self.a, f"{self.url}/housing/save", {"code": f"{stem}1", "how_many": "2",
                                                           "location_id_fk": rack, "position": "Z9"})
        self.assertTrue(errors(r))
        r = self.post(self.a, f"{self.url}/housing/save", {"code": f"{stem}1", "how_many": "31"})
        self.assertIn("from 1 to 30", " ".join(errors(r)))
        self.assertEqual(count("organism_housing", "module_id_fk=? and code like ?", self.mid, f"{stem}%"), 0)

    def test_dialogs_offer_how_many(self):
        self.assertIn('name="how_many" min="1" max="50"', self.page(self.a, "animals"))
        self.assertIn('name="how_many" min="1" max="30"', self.page(self.a, "housing"))


class GroupModuleTests(AppTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = cls.make_organism_module(cls.a, capabilities=["housing", "group_counts", "genotyping"],
                                           identity_mode="group")
        cls.url = f"/organisms/{cls.key}"

    def test_group_module_add_many_makes_anonymous_groups(self):
        unit = self.make_housing(self.a, self.key, owner=self.admin)
        self.post(self.a, f"{self.url}/animal/save", {"count": "20", "how_many": "4", "housing_id_fk": unit,
                                                      "_full": "1"})
        self.assertEqual(rows("select count(*), count(code) from organisms where housing_id_fk=? and count=20", unit),
                         [(4, 0)])


# ============================================================ lines, cohorts, crosses, lots, readings

class LinesCohortsCrossesTests(OrganismCase):
    capabilities = ALL_VIEWS_CAPS

    def line(self, code=None, **fields) -> int:
        code = code or uniq("L")
        self.post(self.a, f"{self.url}/line/save", {"code": code, "owner": self.admin, **fields})
        found = one("select id from organism_lines where module_id_fk=? and code=?", self.mid, code)
        assert found, "line not created"
        return found

    def test_duplicate_line_code_names_the_field(self):
        code = uniq("L")
        self.line(code)
        r = self.post(self.a, f"{self.url}/line/save", {"code": code, "name": "dup"})
        msgs = " ".join(errors(r))
        self.assertIn("That code is already used", msgs)
        self.assertNotIn("module ID fk", msgs)

    def test_line_in_use_cannot_be_deleted(self):
        line = self.line()
        self.make_animal(self.a, self.key, owner=self.admin, line_id_fk=line)
        self.make_housing(self.a, self.key, owner=self.admin, line_id_fk=line)
        r = self.post(self.a, f"{self.url}/line/{line}/delete")
        msg = " ".join(errors(r))
        self.assertIn("1 animal record", msg)
        self.assertIn("1 housing unit", msg)
        self.assertEqual(count("organism_lines", "id=?", line), 1)

    def test_unused_line_deletes(self):
        line = self.line()
        self.post(self.a, f"{self.url}/line/{line}/delete")
        self.assertEqual(count("organism_lines", "id=?", line), 0)

    def test_partial_line_save_keeps_fields_not_in_the_form(self):
        parent = self.line()
        line = self.line(protocol="P-9", last_frozen_on="2025-01-01", parent_line_id_fk=parent)
        self.post(self.a, f"{self.url}/line/save", {"id": line, "name": "renamed", "_full": "1"})
        self.assertEqual(rows("select protocol, last_frozen_on, parent_line_id_fk, name from organism_lines where id=?",
                              line), [("P-9", "2025-01-01", parent, "renamed")])

    def test_cohort_current_count_defaults_to_initial(self):
        code = uniq("C")
        self.post(self.a, f"{self.url}/cohort/save", {"code": code, "count_initial": "300", "owner": self.admin})
        self.assertEqual(rows("select count_initial, count_current from organism_cohorts where module_id_fk=? "
                              "and code=?", self.mid, code), [(300, 300)])

    def test_negative_cohort_count_is_refused(self):
        code = uniq("C")
        r = self.post(self.a, f"{self.url}/cohort/save", {"code": code, "count_initial": "-3"})
        self.assertIn("Counts must be whole numbers", " ".join(errors(r)))
        self.assertEqual(count("organism_cohorts", "module_id_fk=? and code=?", self.mid, code), 0)

    def test_cross_is_saved_and_only_its_owner_may_edit_it(self):
        r = self.post(self.a, f"{self.url}/cross/save", {"code": "", "owner": self.admin, "set_up_on": T,
                                                         "sire_label": "M1"})
        cross = one("select max(id) from organism_crosses where module_id_fk=?", self.mid)
        code = one("select code from organism_crosses where id=?", cross)
        self.assertRegex(code, r"-X\d{3}$")
        self.assertFlash(r, f"Saved {code}.", "success")
        r = self.post(self.m, f"{self.url}/cross/save", {"id": cross, "sire_label": "hijack"})
        self.assertTrue(errors(r))
        self.assertEqual(one("select sire_label from organism_crosses where id=?", cross), "M1")

    def test_deleting_a_cross_unlinks_its_cohorts(self):
        code = uniq("X")
        self.post(self.a, f"{self.url}/cross/save", {"code": code, "owner": self.admin})
        cross = one("select id from organism_crosses where module_id_fk=? and code=?", self.mid, code)
        cohort_code = uniq("C")
        self.post(self.a, f"{self.url}/cohort/save", {"code": cohort_code, "cross_id_fk": cross, "owner": self.admin})
        self.post(self.a, f"{self.url}/cross/{cross}/delete")
        self.assertEqual(rows("select cross_id_fk from organism_cohorts where module_id_fk=? and code=?",
                              self.mid, cohort_code), [(None,)])

    def test_lot_needs_a_line_and_sane_vial_counts(self):
        line = self.line()
        code = uniq("LOT")
        r = self.post(self.a, f"{self.url}/lot/save", {"line_id_fk": "", "code": code, "vial_count": "5"})
        self.assertTrue(errors(r))
        r = self.post(self.a, f"{self.url}/lot/save", {"line_id_fk": line, "code": code, "vial_count": "5",
                                                       "vials_remaining": "9"})
        self.assertIn("cannot be more than vials frozen", " ".join(errors(r)))
        self.assertEqual(count("organism_preservation", "module_id_fk=? and code=?", self.mid, code), 0)

    def test_freezing_a_lot_moves_the_lines_last_frozen_date(self):
        line = self.line(last_frozen_on=days_ago(100))
        r = self.post(self.a, f"{self.url}/lot/save", {"line_id_fk": line, "code": uniq("LOT"),
                                                       "vial_count": "4", "frozen_on": days_ago(2)})
        self.assertFlash(r, "Saved frozen lot.", "success")
        self.assertEqual(one("select last_frozen_on from organism_lines where id=?", line), days_ago(2))
        self.assertEqual(one("select vials_remaining from organism_preservation where line_id_fk=?", line), 4)

    def test_reading_needs_a_location_and_skips_non_numbers(self):
        r = self.post(self.a, f"{self.url}/reading/save", {"location_id_fk": "", "metric_ph": "7"})
        self.assertIn("to log against", " ".join(errors(r)))
        room = uniq("Room")
        self.post(self.a, f"{self.url}/location/save", {"name": room, "kind": "room"})
        loc = one("select id from organism_locations where module_id_fk=? and name=?", self.mid, room)
        r = self.post(self.a, f"{self.url}/reading/save", {"location_id_fk": loc, "metric_ph": "7.2",
                                                           "metric_temperature_c": "warm"})
        self.assertFlash(r, "Logged 1 reading.", "success")
        self.assertFlash(r, "Not a number, so not logged: Temperature.", "error")
        self.assertEqual(rows("select metric, value_num from organism_measurements where module_id_fk=? "
                              "and subject_id=?", self.mid, loc), [("ph", 7.2)])


# ============================================================ schedule

class ScheduleTests(OrganismCase):

    def rule(self, client=None, **fields):
        data = {"label": uniq("Rule "), "applies_to": "organism", "anchor": "birth_on", "offset_days": "30"}
        data.update(fields)
        r = self.post(client or self.a, f"{self.url}/rule/save", data)
        return r, data["label"]

    def rule_key(self, label):
        return next(r["key"] for r in json.loads(module_row(self.key, "schedule_rules")) if r["label"] == label)

    def open_due(self, rule_key, subject_id):
        return rows("select id, due_on from organism_due where module_id_fk=? and rule_key=? and subject_id=? "
                    "and done_on is null", self.mid, rule_key, subject_id)

    def test_birth_anchored_item_stays_on_its_date(self):
        birth = days_ago(300)
        animal = self.make_animal(self.a, self.key, owner=self.admin, birth_on=birth)
        r, label = self.rule(recurring="1")
        self.assertFlash(r, f"Saved rule {label}.", "success")
        key = self.rule_key(label)
        [(due_id, due_on)] = self.open_due(key, animal)
        self.assertEqual(due_on, days_ago(270))
        # An unrelated save recomputes the schedule; the overdue item is not rolled forward.
        self.post(self.a, f"{self.url}/animal/save", {"id": animal, "notes": "touched"})
        self.assertEqual(self.open_due(key, animal), [(due_id, days_ago(270))])

    def test_completing_a_birth_anchored_item_never_rewrites_the_birth_date(self):
        birth = days_ago(300)
        animal = self.make_animal(self.a, self.key, owner=self.admin, birth_on=birth)
        _, label = self.rule(recurring="1")
        key = self.rule_key(label)
        [(due_id, _)] = self.open_due(key, animal)
        r = self.post(self.a, f"{self.url}/due/{due_id}/done")
        self.assertFlash(r, "Marked done.", "success")
        self.assertEqual(one("select birth_on from organisms where id=?", animal), birth)
        self.assertEqual(one("select done_on from organism_due where id=?", due_id), T)
        # The next one counts from the completion instead.
        self.assertEqual([d for _, d in self.open_due(key, animal)], [days_ahead(30)])

    def test_one_off_rule_is_finished_once_done(self):
        animal = self.make_animal(self.a, self.key, owner=self.admin, birth_on=days_ago(40))
        _, label = self.rule(offset_days="10")
        key = self.rule_key(label)
        [(due_id, _)] = self.open_due(key, animal)
        self.post(self.a, f"{self.url}/due/{due_id}/done")
        self.assertEqual(self.open_due(key, animal), [])
        r = self.post(self.a, f"{self.url}/due/{due_id}/done")
        self.assertFlash(r, "Already done.", "info")

    def test_service_anchor_rolls_forward_to_today(self):
        unit = self.make_housing(self.a, self.key, owner=self.admin, last_serviced_on=days_ago(10))
        _, label = self.rule(applies_to="housing", anchor="last_serviced_on", offset_days="7", recurring="1")
        key = self.rule_key(label)
        [(due_id, due_on)] = self.open_due(key, unit)
        self.assertEqual(due_on, days_ago(3))
        self.post(self.a, f"{self.url}/due/{due_id}/done")
        self.assertEqual(one("select last_serviced_on from organism_housing where id=?", unit), T)
        self.assertEqual([d for _, d in self.open_due(key, unit)], [days_ahead(7)])

    def test_two_rules_on_one_date_each_keep_their_own_last_done(self):
        key = self.make_organism_module(self.a)
        url = f"/organisms/{key}"
        mid = self.organism_module_id(key)
        unit = self.make_housing(self.a, key, owner=self.admin, last_serviced_on=days_ago(5))
        for label, days in (("Feed", "2"), ("Split", "4")):
            self.post(self.a, f"{url}/rule/save", {"label": label, "applies_to": "housing", "anchor": "last_serviced_on",
                                                   "offset_days": days, "recurring": "1"})
        rules = {r["label"]: r["key"] for r in json.loads(module_row(key, "schedule_rules"))}

        def due(label):
            return rows("select id, due_on from organism_due where module_id_fk=? and rule_key=? and subject_id=? "
                        "and done_on is null", mid, rules[label], unit)

        [(feed_id, _)] = due("Feed")
        [(_split_id, split_due)] = due("Split")
        # Fed yesterday, ticked today: the next feed is from yesterday, and the split keeps its own date.
        r = self.post(self.a, f"{url}/due/{feed_id}/done", {"done_on": days_ago(1)})
        self.assertFlash(r, "Marked done on", "success")
        self.assertEqual(one("select done_on from organism_due where id=?", feed_id), days_ago(1))
        self.assertEqual([d for _, d in due("Feed")], [days_ahead(1)])
        self.assertEqual([d for _, d in due("Split")], [split_due])
        self.assertEqual(one("select last_serviced_on from organism_housing where id=?", unit), days_ago(5))

    def test_done_cannot_be_in_the_future(self):
        animal = self.make_animal(self.a, self.key, owner=self.admin, birth_on=days_ago(40))
        _, label = self.rule(offset_days="10")
        [(due_id, _)] = self.open_due(self.rule_key(label), animal)
        r = self.post(self.a, f"{self.url}/due/{due_id}/done", {"done_on": days_ahead(2)})
        self.assertIn("future", " ".join(errors(r)))
        self.assertIsNone(one("select done_on from organism_due where id=?", due_id))

    def test_age_counted_in_passages_is_the_passage_number(self):
        from types import SimpleNamespace
        from datetime import date, timedelta
        from app.organism_service import age_label
        TODAY = date.today()
        cells = SimpleNamespace(age_unit="passages")
        self.assertEqual(age_label(cells, TODAY - timedelta(days=9), attrs={"passage": "12"}), "P12")
        self.assertEqual(age_label(cells, TODAY - timedelta(days=9), attrs={}), "9d")

    def test_rule_anchor_must_belong_to_its_subject(self):
        r, label = self.rule(applies_to="cohort", anchor="last_serviced_on")
        self.assertIn("counts from one of", " ".join(errors(r)))
        self.assertNotIn(label, module_row(self.key, "schedule_rules"))

    def test_same_rule_name_is_not_silently_replaced(self):
        _, label = self.rule(offset_days="21")
        r, _ = self.rule(label=label, offset_days="99")
        self.assertIn("already a rule", " ".join(errors(r)))
        rule = next(x for x in json.loads(module_row(self.key, "schedule_rules")) if x["label"] == label)
        self.assertEqual(rule["offset_days"], 21)

    def test_negative_offset_is_refused(self):
        r, label = self.rule(offset_days="-1")
        self.assertIn("whole number of 0 or more", " ".join(errors(r)))
        self.assertNotIn(label, module_row(self.key, "schedule_rules"))

    def test_member_cannot_change_rules(self):
        _, label = self.rule()
        key = self.rule_key(label)
        before = module_row(self.key, "schedule_rules")
        self.rule(client=self.m)
        self.post(self.m, f"{self.url}/rule/{key}/delete")
        self.assertEqual(module_row(self.key, "schedule_rules"), before)

    def test_admin_removes_a_rule_and_its_open_items(self):
        animal = self.make_animal(self.a, self.key, owner=self.admin, birth_on=days_ago(5))
        _, label = self.rule()
        key = self.rule_key(label)
        self.assertEqual(len(self.open_due(key, animal)), 1)
        self.assertFlash(self.post(self.a, f"{self.url}/rule/{key}/delete"), "Removed rule.")
        self.assertEqual(self.open_due(key, animal), [])

    def test_member_completes_own_item_but_not_someone_elses(self):
        _, label = self.rule(offset_days="1")
        key = self.rule_key(label)
        theirs = self.make_animal(self.a, self.key, owner=self.admin, birth_on=days_ago(5))
        mine = self.make_animal(self.m, self.key, owner=self.member, birth_on=days_ago(5))
        [(their_due, _)] = self.open_due(key, theirs)
        [(my_due, _)] = self.open_due(key, mine)
        r = self.post(self.m, f"{self.url}/due/{their_due}/done")
        self.assertTrue(errors(r))
        self.assertIsNone(one("select done_on from organism_due where id=?", their_due))
        self.post(self.m, f"{self.url}/due/{my_due}/done")
        self.assertEqual(one("select done_on from organism_due where id=?", my_due), T)

    def test_deleting_an_animal_drops_its_open_items(self):
        _, label = self.rule()
        key = self.rule_key(label)
        animal = self.make_animal(self.a, self.key, owner=self.admin, birth_on=days_ago(5))
        [(due_id, _)] = self.open_due(key, animal)
        self.post(self.a, f"{self.url}/animal/{animal}/delete")
        self.assertEqual(self.open_due(key, animal), [])
        r = self.post(self.a, f"{self.url}/due/{due_id}/done")
        self.assertFlash(r, "no longer on the schedule", "info")

    def test_cohort_rule_schedules_and_shows_on_the_schedule_view(self):
        _, label = self.rule(applies_to="cohort", anchor="birth_on", offset_days="21")
        code = uniq("LIT")
        self.post(self.a, f"{self.url}/cohort/save", {"code": code, "birth_on": days_ago(20), "owner": self.admin})
        cohort = one("select id from organism_cohorts where module_id_fk=? and code=?", self.mid, code)
        self.assertEqual([d for _, d in self.open_due(self.rule_key(label), cohort)], [days_ahead(1)])
        h = self.page(self.a, "schedule")
        self.assertIn(code, h)
        self.assertIn(label, h)


# ============================================================ escaping

class EscapingTests(OrganismCase):
    capabilities = ALL_VIEWS_CAPS
    PAYLOADS = ("<img src=x onerror=alert(1)>", '"><script>alert(1)</script>')

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        a, url = cls.a, cls.url
        for n, pay in enumerate(cls.PAYLOADS):
            a.post(f"{url}/line/save", data={"code": pay, "name": pay, "owner": cls.admin})
            line = one("select id from organism_lines where module_id_fk=? and code=?", cls.mid, pay)
            a.post(f"{url}/housing/save", data={"code": pay, "owner": cls.admin, "purpose": pay})
            a.post(f"{url}/animal/save", data={"code": pay, "owner": cls.admin, "notes": pay, "genotype": pay})
            a.post(f"{url}/cross/save", data={"code": pay, "owner": cls.admin})
            a.post(f"{url}/cohort/save", data={"code": pay, "owner": cls.admin})
            a.post(f"{url}/location/save", data={"name": pay, "kind": "rack", "rows": "1", "cols": "1"})
            a.post(f"{url}/field/add", data={"entity": "organism", "label": pay + str(n), "field_type": "text"})
            a.post(f"{url}/rule/save", data={"label": pay, "applies_to": "housing", "anchor": "last_serviced_on",
                                             "offset_days": "3"})
            a.post(f"{url}/lot/save", data={"line_id_fk": line, "code": pay, "vial_count": "3"})
            a.post(f"{url}/genotype/save", data={"subject": pay, "assay": pay, "result": pay})

    VIEWS = ("animals", "housing", "lines", "crosses", "cohorts", "genotyping", "schedule", "preservation",
             "settings")

    def test_payloads_were_stored(self):
        for pay in self.PAYLOADS:
            for table in ("organism_lines", "organism_housing", "organisms", "organism_crosses",
                          "organism_cohorts", "organism_preservation"):
                self.assertEqual(count(table, "module_id_fk=? and code=?", self.mid, pay), 1, table)
            self.assertEqual(count("organism_genotypes", "module_id_fk=? and assay=?", self.mid, pay), 1)

    def test_names_never_render_unescaped(self):
        for view in self.VIEWS:
            h = self.page(self.a, view)
            for pay in self.PAYLOADS:
                self.assertNotIn(pay, h, view)
            self.assertNotIn("onsubmit", h, view)

    def test_delete_buttons_carry_names_only_inside_data_confirm(self):
        expect = {"animals": 1, "housing": 2, "lines": 1, "crosses": 1, "cohorts": 1, "preservation": 1,
                  "genotyping": 1, "settings": 2}
        for view, least in expect.items():
            h = self.page(self.a, view)
            for pay in self.PAYLOADS:
                escaped = re.escape(str(escape(pay)))
                found = re.findall(rf'data-confirm="[^"]*{escaped}[^"]*"', h)
                self.assertGreaterEqual(len(found), least, (view, pay))

    def test_member_view_and_hub_escape_names(self):
        key = self.make_organism_module(self.a, label=self.PAYLOADS[0])
        for client in (self.a, self.m):
            h = self.get_ok(client, "/organisms/")
            self.assertNotIn(self.PAYLOADS[0], h)
            self.assertIn(str(escape(self.PAYLOADS[0])), h)
            self.assertNotIn(self.PAYLOADS[0], self.get_ok(client, f"/organisms/{key}?view=settings"))
        for pay in self.PAYLOADS:
            self.assertNotIn(pay, self.page(self.m, "animals"))


# ============================================================ genotyping

class GenotypingTests(OrganismCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.other_key = cls.make_organism_module(cls.a)

    def setUp(self):
        self.tank_code = uniq("NT")
        self.tank = self.make_housing(self.a, self.key, self.tank_code, owner=self.admin)
        self.code = uniq("NW")
        self.animal = self.make_animal(self.a, self.key, self.code, owner=self.admin, housing_id_fk=self.tank)

    def call(self, client, **fields):
        return self.post(client, f"{self.url}/genotype/save", {"return_view": "genotyping", **fields})

    def calls_for(self, subject_id, kind="organism"):
        return rows("select assay, result, zygosity, called_by, image_path from organism_genotypes "
                    "where module_id_fk=? and subject_kind=? and subject_id=?", self.mid, kind, subject_id)

    def test_call_by_typed_code(self):
        r = self.call(self.a, subject=self.code.lower(), assay="PCR", result="tg/+", zygosity="het",
                      called_on=T, image_path="https://gels.example/1.png")
        self.assertFlash(r, f"Recorded genotype for {self.code}.", "success")
        self.assertEqual(self.calls_for(self.animal),
                         [("PCR", "tg/+", "het", self.admin, "https://gels.example/1.png")])
        self.assertIn('id="genotype-dialog"', r.get_data(as_text=True))

    def test_unknown_subject_is_refused(self):
        before = count("organism_genotypes", "module_id_fk=?", self.mid)
        r = self.call(self.a, subject=uniq("ghost"), result="het")
        self.assertIn("so no genotype was recorded", " ".join(errors(r)))
        r = self.call(self.a, subject_kind="organism", subject_id="999999999", result="het")
        self.assertTrue(errors(r))
        r = self.call(self.a, subject="", result="het")
        self.assertTrue(errors(r))
        self.assertEqual(count("organism_genotypes", "module_id_fk=?", self.mid), before)

    def test_another_modules_animal_is_refused(self):
        foreign_code = uniq("FX")
        foreign = self.make_animal(self.a, self.other_key, foreign_code, owner=self.admin)
        for data in ({"subject_kind": "organism", "subject_id": foreign},
                     {"subject": f"#{foreign}"}, {"subject": foreign_code}):
            r = self.call(self.a, result="het", **data)
            self.assertTrue(errors(r), data)
        self.assertEqual(count("organism_genotypes", "subject_kind='organism' and subject_id=?", foreign), 0)

    def test_empty_result_and_zygosity_is_refused(self):
        r = self.call(self.a, subject=self.code, assay="PCR", result="", zygosity="")
        self.assertIn("Give the call a result or a zygosity.", errors(r))
        self.assertEqual(self.calls_for(self.animal), [])

    def test_javascript_gel_link_is_refused(self):
        r = self.call(self.a, subject=self.code, result="wt", image_path="javascript:alert(1)")
        self.assertIn("must start with http", " ".join(errors(r)))
        self.assertEqual(self.calls_for(self.animal), [])

    def test_bad_date_is_refused(self):
        r = self.call(self.a, subject=self.code, result="x", called_on="31/02/2020")
        self.assertIn("is not a date", " ".join(errors(r)))
        self.assertEqual(self.calls_for(self.animal), [])

    def test_housing_code_makes_a_housing_level_call(self):
        self.call(self.a, subject=self.tank_code.lower(), assay="pool", result="mixed")
        self.assertEqual([c[:2] for c in self.calls_for(self.tank, "housing")], [("pool", "mixed")])

    def test_uncoded_group_is_called_by_hash_id(self):
        # An individual module gives a blank code an ID, so make an uncoded record directly.
        group = self.make_animal(self.a, self.key, owner=self.admin)
        execute("update organisms set code=null where id=?", group)
        self.assertIn(f'<option value="#{group}"', self.page(self.a, "genotyping"))
        self.call(self.a, subject=f"#{group}", result="mixed")
        self.assertEqual(len(self.calls_for(group)), 1)

    def test_member_can_only_call_own_animals(self):
        r = self.call(self.m, subject=self.code, result="hom")
        self.assertIn(f"belongs to {self.admin}", " ".join(errors(r)))
        self.assertEqual(self.calls_for(self.animal), [])
        mine_code = uniq("MS")
        mine = self.make_animal(self.m, self.key, mine_code, owner=self.member)
        r = self.call(self.m, subject=mine_code, assay="qPCR", result="wt")
        self.assertFlash(r, f"Recorded genotype for {mine_code}.", "success")
        self.assertEqual([c[3] for c in self.calls_for(mine)], [self.member])

    def test_member_cannot_remove_a_call_on_someone_elses_animal(self):
        self.call(self.a, subject=self.code, result="het")
        call = one("select id from organism_genotypes where subject_kind='organism' and subject_id=?", self.animal)
        r = self.post(self.m, f"{self.url}/genotype/{call}/delete")
        self.assertTrue(errors(r))
        self.assertEqual(count("organism_genotypes", "id=?", call), 1)
        self.assertIn(f'data-subject="{self.code}"', self.page(self.m, "genotyping"))
        self.assertRegex(self.page(self.m, "genotyping"),
                         rf'data-subject="{self.code}"[^>]*class="is-locked"')

    def test_admin_removes_a_call(self):
        self.call(self.a, subject=self.code, result="het")
        call = one("select id from organism_genotypes where subject_kind='organism' and subject_id=?", self.animal)
        r = self.post(self.a, f"{self.url}/genotype/{call}/delete")
        self.assertFlash(r, "Removed the genotype call.", "success")
        self.assertEqual(count("organism_genotypes", "id=?", call), 0)

    def test_pending_lists_living_animals_without_a_call(self):
        h = self.page(self.a, "genotyping")
        self.assertRegex(h, rf'data-id="p{self.animal}" data-call="false" data-pending="true"')
        self.call(self.a, subject=self.code, result="het")
        self.assertNotIn(f'data-id="p{self.animal}"', self.page(self.a, "genotyping"))

    def test_animals_sheet_shows_the_latest_call(self):
        self.call(self.a, subject=self.code, assay="PCR", result="old", zygosity="hom", called_on=days_ago(5))
        self.call(self.a, subject=self.code, assay="PCR", result="new", zygosity="het", called_on=T)
        h = self.page(self.a, "animals")
        self.assertIn('data-sort-key="call"', h)
        self.assertRegex(h, rf'data-code="{self.code}"[^>]*data-call="new het"')
        self.assertNotRegex(h, rf'data-code="{self.code}"[^>]*data-call="old hom"')

    def test_batch_genotype_only_calls_editable_subjects(self):
        mine = self.make_animal(self.m, self.key, owner=self.member)
        assay = uniq("Batch ")
        r = self.post(self.m, f"{self.url}/animals/genotype", {"selected_ids": [self.animal, mine], "assay": assay,
                                                               "result": "tg/tg", "zygosity": "hom"})
        self.assertIn("1 belong to someone else", flash_text(r))
        self.assertEqual([s for (s,) in rows("select subject_id from organism_genotypes where assay=?", assay)], [mine])

    def test_batch_genotype_is_one_undoable_batch(self):
        second = self.make_animal(self.a, self.key, owner=self.admin)
        assay = uniq("Batch ")
        r = self.post(self.a, f"{self.url}/animals/genotype", {"selected_ids": [self.animal, second], "assay": assay,
                                                               "result": "tg/tg", "zygosity": "hom"})
        self.assertFlash(r, "Recorded a genotype for 2 newts.", "success")
        ids = [i for (i,) in rows("select id from organism_genotypes where assay=?", assay)]
        self.assertEqual(len(ids), 2)
        batch = batch_of("organism_genotypes", ids[0])
        self.assertEqual(count("audit_log", "batch_id_fk=? and table_name='organism_genotypes' and action='create'",
                               batch), 2)
        self.post(self.a, f"/batches/{batch}/undo")
        self.assertEqual(count("organism_genotypes", "assay=?", assay), 0)

    def test_batch_genotype_without_a_result_is_refused(self):
        assay = uniq("Batch ")
        r = self.post(self.a, f"{self.url}/animals/genotype", {"selected_ids": [self.animal], "assay": assay,
                                                               "result": "", "zygosity": ""})
        self.assertTrue(errors(r))
        self.assertEqual(count("organism_genotypes", "assay=?", assay), 0)


if __name__ == "__main__":
    unittest.main()
