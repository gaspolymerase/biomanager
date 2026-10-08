"""Admin pages: who looks after each rack, box and incubator
(/admin/racks/), and the user administration page."""
from tests.base import *  # noqa: F401,F403
from tests.base import AppTestCase, execute, one, uniq, make_user, client_for, batch_of

import unittest

from flask import g

from app import access
from app.app import app
from app.db import SessionLocal
from app.models import MouseRack, UserAccount


class RackHandOverPage(AppTestCase):
    def make_mouse_rack(self, client, name=None):
        name = name or uniq("MR")
        client.post("/colony/racks/save", data={"id": "", "name": name, "rows": "4", "cols": "4"})
        rack = one("select id from mouse_racks where name=?", name)
        self.assertIsNotNone(rack)
        return rack

    def assign(self, **data):
        return self.post(self.a, "/admin/racks/assign", data=data)

    def test_a_member_is_refused_the_page(self):
        self.assertEqual(self.m.get("/admin/racks/").status_code, 403)

    def test_a_member_cannot_assign(self):
        rack = self.make_mouse_rack(self.a)
        r = self.m.post("/admin/racks/assign", data={"kind": "mouse_rack", "id": rack, "creator": self.member})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(one("select created_by from mouse_racks where id=?", rack), self.admin)

    def test_settings_statistics_lists_the_racks_by_kind(self):
        # Racks & boxes is part of Settings → Statistics now; the old page sends you there.
        self.make_mouse_rack(self.a)
        self.assertTrue(self.a.get("/admin/racks/").headers["Location"].endswith("/settings#racks"))
        html = self.get_ok(self.a, "/settings")
        self.assertIn('id="racks"', html)
        self.assertIn("Mouse racks", html)

    def test_a_container_without_a_creator_is_shown_as_admin_only(self):
        rack = self.make_mouse_rack(self.a)
        execute("update mouse_racks set created_by='' where id=?", rack)
        html = self.get_ok(self.a, "/settings")
        form = html.split(f'name="id" value="{rack}"', 1)[1].split("</form>", 1)[0]
        self.assertIn('<option value="" selected>Admins only</option>', form)

    def test_the_page_is_linked_from_settings(self):
        self.assertIn("/admin/racks/", self.get_ok(self.a, "/settings"))

    def test_assigning_one_rack_hands_it_to_the_member(self):
        rack = self.make_mouse_rack(self.a)
        r = self.assign(kind="mouse_rack", id=rack, creator=self.member)
        self.assertEqual(one("select created_by from mouse_racks where id=?", rack), self.member)
        self.assertFlash(r, "looked after by", "success")

    def test_the_new_creator_may_edit_the_rack(self):
        rack = self.make_mouse_rack(self.a)
        self.m.post("/colony/racks/save", data={"id": rack, "name": uniq("MR"), "rows": "6", "cols": "4"})
        self.assertEqual(one("select rows from mouse_racks where id=?", rack), 4)  # not theirs yet
        self.assign(kind="mouse_rack", id=rack, creator=self.member)
        with app.test_request_context():
            with SessionLocal() as s:
                g.user = s.get(UserAccount, one("select id from users where username=?", self.member))
                self.assertTrue(access.can_edit_rack(s.get(MouseRack, rack), g.user))
        self.m.post("/colony/racks/save", data={"id": rack, "name": one("select name from mouse_racks where id=?", rack),
                                                "rows": "6", "cols": "4"})
        self.assertEqual(one("select rows from mouse_racks where id=?", rack), 6)

    def test_an_unknown_member_is_refused(self):
        rack = self.make_mouse_rack(self.a)
        r = self.assign(kind="mouse_rack", id=rack, creator=uniq("nobody"))
        self.assertEqual(one("select created_by from mouse_racks where id=?", rack), self.admin)
        self.assertFlash(r, "not an active lab member", "error")

    def test_a_disabled_member_is_refused(self):
        gone = make_user()
        execute("update users set disabled=true where username=?", gone)
        rack = self.make_mouse_rack(self.a)
        self.assign(kind="mouse_rack", id=rack, creator=gone)
        self.assertEqual(one("select created_by from mouse_racks where id=?", rack), self.admin)

    def test_an_empty_creator_makes_it_admin_only_again(self):
        rack = self.make_mouse_rack(self.a)
        self.assign(kind="mouse_rack", id=rack, creator="")
        self.assertEqual(one("select created_by from mouse_racks where id=?", rack), "")

    def test_nothing_to_change_is_said_so(self):
        rack = self.make_mouse_rack(self.a)
        r = self.assign(kind="mouse_rack", id=rack, creator=self.admin)
        self.assertFlash(r, "Nothing to change")

    def test_an_unknown_kind_is_not_found(self):
        self.assertEqual(self.a.post("/admin/racks/assign", data={"kind": "spaceship", "creator": ""}).status_code, 404)

    def test_an_unknown_container_id_is_not_found(self):
        r = self.a.post("/admin/racks/assign", data={"kind": "mouse_rack", "id": "987654321", "creator": self.member})
        self.assertEqual(r.status_code, 404)

    def test_handing_over_every_unassigned_rack_of_one_database_leaves_others_alone(self):
        mine = self.make_stock_module(self.a)
        others = self.make_stock_module(self.a)
        racks = [self.make_stock_rack(self.a, mine) for _ in range(2)]
        other_rack = self.make_stock_rack(self.a, others)
        execute(f"update stock_racks set created_by='' where id in ({','.join('?' * 3)})", *racks, other_rack)
        label = one("select label from stock_modules where key=?", mine)
        self.assign(kind="stock_rack", database=label, creator=self.member)
        for rack in racks:
            self.assertEqual(one("select created_by from stock_racks where id=?", rack), self.member)
        self.assertEqual(one("select created_by from stock_racks where id=?", other_rack), "")

    def test_a_bulk_hand_over_is_one_batch_that_can_be_undone(self):
        key = self.make_stock_module(self.a)
        incubators = [self.make_incubator(self.a, key) for _ in range(3)]
        execute(f"update stock_incubators set created_by='' where id in ({','.join('?' * 3)})", *incubators)
        label = one("select label from stock_modules where key=?", key)
        self.assign(kind="stock_incubator", database=label, creator=self.member)
        batch = batch_of("stock_incubators", incubators[0], "update")
        self.assertEqual({batch_of("stock_incubators", i, "update") for i in incubators}, {batch})
        self.post(self.a, f"/batches/{batch}/undo")
        for inc in incubators:
            self.assertEqual(one("select created_by from stock_incubators where id=?", inc), "")

    def test_plasmid_and_inventory_boxes_can_be_handed_over(self):
        box = self.make_box(self.a)
        self.assign(kind="plasmid_box", id=box, creator=self.member)
        self.assertEqual(one("select created_by from plasmid_boxes where id=?", box), self.member)
        name = uniq("IB")
        self.a.post("/inventory/reagents/racks/save", data={"name": name, "rows": 3, "cols": 3, "kind": "box", **GRID_NAMING})
        ibox = one("select id from inventory_racks where name=?", name)
        self.assign(kind="inventory_box", id=ibox, creator=self.member)
        self.assertEqual(one("select created_by from inventory_racks where id=?", ibox), self.member)


class UserAdministration(AppTestCase):
    def test_a_member_cannot_open_user_administration(self):
        r = self.m.get("/admin/users")
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("/admin/users", r.headers["Location"])

    def test_admin_sees_the_users(self):
        self.assertIn(self.member, self.get_ok(self.a, "/settings"))

    def test_a_member_cannot_change_roles(self):
        target = make_user()
        self.m.post(f"/admin/users/{one('select id from users where username=?', target)}/role")
        self.assertEqual(one("select role from users where username=?", target), "member")

    def test_admin_can_disable_a_member(self):
        target = make_user()
        self.a.post(f"/admin/users/{one('select id from users where username=?', target)}/disable")
        self.assertTrue(one("select disabled from users where username=?", target))

    def test_a_disabled_user_is_logged_out(self):
        target = make_user()
        c = client_for(target)
        self.assertEqual(c.get("/home").status_code, 200)
        self.a.post(f"/admin/users/{one('select id from users where username=?', target)}/disable")
        self.assertEqual(c.get("/home").status_code, 302)

    def test_the_audit_log_is_admin_only(self):
        self.get_ok(self.a, "/audit")
        self.assertEqual(self.m.get("/audit").status_code, 302)


if __name__ == "__main__":
    unittest.main()
