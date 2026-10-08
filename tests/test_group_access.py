"""What a project group's members may do in it (app/groups.py member_may):
a lead and an admin always may; a member when the group's switch is on; one
who can only view, never. Each switch starts as groups worked before."""
from __future__ import annotations

import unittest

from tests.base import AppTestCase, client_for, execute, make_user, one, uniq
from tests.test_groups import GroupCase, make_group


def switch(gid, column, on):
    execute(f"update lab_groups set {column}=? where id=?", bool(on), gid)


def part(gid, who, lead=False, can_edit=True):
    execute("update lab_group_members set lead=?, can_edit=? where group_id_fk=? and username=?",
            bool(lead), bool(can_edit), gid, who)


class SharedThingsTests(GroupCase):
    def shared_cage(self):
        cage = self.make_cage(self.m, purpose="Breeder")
        self.assertSaved(self.autosave(self.m, f"/colony/cages/{cage}/update", {"is_shared": f"g{self.gid}"}))
        return cage

    def test_a_group_starts_as_before(self):
        self.assertSaved(self.autosave(self.c, f"/colony/cages/{self.shared_cage()}/update", {"notes": "fed"}))

    def test_a_member_who_can_only_view_cannot_edit_the_groups_cage(self):
        cage = self.shared_cage()
        part(self.gid, self.colleague, can_edit=False)
        self.assertNotEqual(self.autosave(self.c, f"/colony/cages/{cage}/update", {"notes": "x"}).status_code, 200)
        self.assertSaved(self.autosave(self.m, f"/colony/cages/{cage}/update", {"notes": "mine"}))   # its owner

    def test_switching_off_editing_leaves_it_to_the_leads(self):
        cage = self.shared_cage()
        switch(self.gid, "may_edit_shared", False)
        self.assertNotEqual(self.autosave(self.c, f"/colony/cages/{cage}/update", {"notes": "x"}).status_code, 200)
        part(self.gid, self.colleague, lead=True)
        self.assertSaved(self.autosave(self.c, f"/colony/cages/{cage}/update", {"notes": "lead"}))

    def test_sharing_with_the_group_can_be_left_to_its_leads(self):
        switch(self.gid, "may_share", False)
        cage = self.make_cage(self.c, purpose="Breeder")
        r = self.autosave(self.c, f"/colony/cages/{cage}/update", {"is_shared": f"g{self.gid}"})
        self.assertRefused(r)
        self.assertIn("Only the leads", r.get_json()["error"])

    def test_to_dos_follow_their_switch(self):
        r = self.m.post("/calendar/items", json={"kind": "task", "title": uniq("todo"), "start": "2026-10-05T00:00:00",
                                                 "end": "2026-10-05T23:59:59", "isAllday": True,
                                                 "audience": f"g{self.gid}"})
        tid = r.get_json()["item"]["raw"]["rowId"]
        switch(self.gid, "may_tick_todos", False)
        self.assertEqual(self.c.post(f"/calendar/items/task-{tid}/toggle").status_code, 403)
        switch(self.gid, "may_tick_todos", True)
        self.assertEqual(self.c.post(f"/calendar/items/task-{tid}/toggle").status_code, 200)

    def test_a_page_shared_to_edit_is_read_only_when_the_group_says_so(self):
        page = self.m.post("/notebook/api/pages/new", json={"title": uniq("plan")}).get_json()["page_id"]
        self.m.post(f"/notebook/api/pages/{page}/shares", json={"username": f"group:{self.gid}", "role": "edit"})
        self.assertEqual(self.c.get(f"/notebook/api/pages/{page}").get_json()["page"]["role"], "edit")
        switch(self.gid, "may_edit_pages", False)
        self.assertEqual(self.c.get(f"/notebook/api/pages/{page}").get_json()["page"]["role"], "view")


class GroupDatabaseTests(GroupCase):
    def setUp(self):
        super().setUp()
        r = self.m.post("/stocks/new", data={"kind": "fly", "label": uniq("Group flies "), "audience": f"group:{self.gid}"})
        self.key = r.headers["Location"].split("?")[0].rsplit("/", 1)[1]

    def test_a_member_adds_and_changes_records_unless_switched_off(self):
        self.make_vial(self.c, self.key)
        switch(self.gid, "may_change_records", False)
        r = self.c.post(f"/stocks/{self.key}/units/save", data={"id": "", "genotype": "w", "purpose": "stock", "count": 1})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.c.get(f"/stocks/{self.key}").status_code, 200)     # still sees it

    def test_one_who_can_only_view_changes_nothing_there(self):
        part(self.gid, self.colleague, can_edit=False)
        r = self.c.post(f"/stocks/{self.key}/units/save", data={"id": "", "genotype": "w", "purpose": "cross", "count": 1})
        self.assertEqual(r.status_code, 403)

    def test_each_others_records_only_when_the_group_lets_them(self):
        vial = self.make_vial(self.m, self.key, purpose="cross")
        r = self.autosave(self.c, f"/stocks/{self.key}/units/{vial}/update", {"notes": "x"})
        self.assertEqual(r.status_code, 403)
        switch(self.gid, "may_edit_each_other", True)
        self.assertSaved(self.autosave(self.c, f"/stocks/{self.key}/units/{vial}/update", {"notes": "checked"}))
        self.assertEqual(one("select notes from stock_units where id=?", vial), "checked")


class LeadsAndPartsTests(GroupCase):
    def test_a_lead_sets_switches_and_parts_but_not_another_lead(self):
        part(self.gid, self.member, lead=True)
        r = self.autosave(self.m, f"/groups/{self.gid}/switches", {"shared": "1", "records": "1"})
        self.assertTrue(r.get_json()["ok"])
        self.assertFalse(one("select may_tick_todos from lab_groups where id=?", self.gid))
        self.assertTrue(self.autosave(self.m, f"/groups/{self.gid}/members/{self.colleague}/part",
                                      {"part": "viewer"}).get_json()["ok"])
        self.assertFalse(one("select can_edit from lab_group_members where group_id_fk=? and username=?",
                             self.gid, self.colleague))
        r = self.autosave(self.m, f"/groups/{self.gid}/members/{self.colleague}/part", {"part": "lead"})
        self.assertEqual(r.status_code, 403)

    def test_a_member_changes_nothing_about_the_group(self):
        self.assertEqual(self.c.post(f"/groups/{self.gid}/switches", data={"shared": "1"}).status_code, 403)
        self.assertEqual(self.c.post(f"/groups/{self.gid}/members/{self.member}/part",
                                     data={"part": "viewer"}).status_code, 403)

    def test_an_admin_makes_a_lead(self):
        self.assertTrue(self.autosave(self.a, f"/groups/{self.gid}/members/{self.colleague}/part",
                                      {"part": "lead"}).get_json()["ok"])
        self.assertTrue(one("select lead from lab_group_members where group_id_fk=? and username=?",
                            self.gid, self.colleague))

    def test_the_group_page_shows_its_parts_and_switches(self):
        html = self.get_ok(self.c, "/settings")
        detail = html.split(f'id="group-{self.gid}"', 1)[1].split("data-tabpane", 1)[0]
        self.assertIn("In this group, members may", detail)
        self.assertIn("Only admins and the group", detail)       # a member: read only
        self.assertIn("disabled", detail)


class LabPanesTests(AppTestCase):
    def test_general_saves_without_touching_the_rest(self):
        execute("delete from app_settings where key='lab_name'")
        before = one("select value from app_settings where key='feature:colony'")
        r = self.autosave(self.a, "/settings/lab", {"section": "general", "lab_name": "Franklin Lab",
                                                     "genotyping_day": "21", "date_style": "iso"})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(one("select value from app_settings where key='lab_name'"), "Franklin Lab")
        self.assertEqual(one("select value from app_settings where key='feature:colony'"), before)

    def test_a_member_cannot_save_lab_panes(self):
        self.assertNotEqual(self.m.post("/settings/lab", data={"section": "general", "lab_name": "x"}).status_code, 200)

    def test_a_member_sees_the_lab_panes_read_only(self):
        html = self.get_ok(self.m, "/settings")
        general = html.split('id="general"', 1)[1].split('id="databases"', 1)[0]
        self.assertIn("Only admins change these", general)
        self.assertNotIn("data-autosave-form", general)
        self.assertNotIn("Waiting to join", html)

    def test_old_admin_pages_open_their_pane(self):
        for path, anchor in (("/admin/users", "people"), ("/admin/colony", "stats"), ("/admin/racks/", "racks"),
                             ("/groups", "groups"), ("/setup", "general")):
            with self.subTest(path=path):
                self.assertTrue(self.a.get(path).headers["Location"].endswith(f"/settings#{anchor}"))

    def test_the_sidebar_has_no_more_menu(self):
        html = self.get_ok(self.a, "/home")
        self.assertNotIn('data-label="More"', html)


if __name__ == "__main__":
    unittest.main()
