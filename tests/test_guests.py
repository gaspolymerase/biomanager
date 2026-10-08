"""Guest passes: a code that signs someone outside the lab in as a
temporary member, and the gate that shows internet visitors nothing else."""
from __future__ import annotations

import re
import unittest
from datetime import datetime, timedelta

from tests.base import AppTestCase, app, execute, flash_text, location, one, uniq

INTERNET = {"X-BioManager-Entry": "internet"}


class GuestCase(AppTestCase):

    def setUp(self):
        super().setUp()
        from app import guests
        guests.guest_throttle.reset()

    def make_pass(self, label=None, days="3") -> tuple[str, str]:
        """(code, guest username) of a new pass, from the admin page."""
        label = label or uniq("Visitor ")
        r = self.a.post("/admin/guests", data={"label": label, "days": days})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        html = r.get_data(as_text=True)
        code = re.search(r'id="guest-new-code"[^>]*>([A-Z0-9-]+)<', html).group(1)
        username = one("select u.username from guest_passes p join users u on u.id=p.user_id_fk "
                       "where p.label=?", label)
        return code, username

    def visitor(self):
        return app.test_client()

    def enter(self, client, code, headers=None):
        return client.post("/guest", data={"code": code}, headers=headers or {})


class PassTests(GuestCase):

    def test_a_pass_makes_a_temporary_member_and_shows_its_code_once(self):
        code, username = self.make_pass(label="Alex Chen")
        self.assertRegex(code, r"^[A-Z0-9]{4}(-[A-Z0-9]{4}){3}$")
        self.assertEqual(one("select role from users where username=?", username), "member")
        self.assertIsNotNone(one("select expires_at from users where username=?", username))
        self.assertTrue(username.startswith("guest-alex-chen"))
        # Only a hash is kept, and the list does not show the code again.
        self.assertNotIn(code, str(one("select code_hash from guest_passes where label='Alex Chen'")))
        self.assertNotIn(code, self.get_ok(self.a, "/admin/guests"))

    def test_the_code_signs_the_guest_in(self):
        code, username = self.make_pass()
        guest = self.visitor()
        r = self.enter(guest, code.lower().replace("-", " "))   # however it is typed
        self.assertEqual(r.status_code, 302)
        self.assertEqual(guest.get("/settings").status_code, 200)
        self.assertEqual(one("select uses from guest_passes p join users u on u.id=p.user_id_fk "
                             "where u.username=?", username), 1)

    def test_a_wrong_code_signs_nobody_in(self):
        self.make_pass()
        guest = self.visitor()
        r = self.enter(guest, "AAAA-BBBB-CCCC-DDDD")
        self.assertEqual(r.status_code, 401)
        self.assertIn("not right", flash_text(r))
        self.assertEqual(location(guest.get("/settings")).split("?")[0], "/login")

    def test_too_many_wrong_codes_are_refused_for_a_while(self):
        from app import guests
        code, _ = self.make_pass()
        guest = self.visitor()
        for _ in range(guests.guest_throttle.limit):
            self.enter(guest, "AAAA-BBBB-CCCC-DDDD")
        self.assertEqual(self.enter(guest, code).status_code, 429)

    def test_a_guest_works_like_a_member_but_is_not_an_admin(self):
        code, username = self.make_pass()
        guest = self.visitor()
        self.enter(guest, code)
        rid = self.make_item(guest, "reagents", uniq("Guest buffer "))
        self.assertEqual(one("select owner from inventory_items where id=?", rid), username)
        self.assertEqual(guest.get("/admin/guests").status_code, 403)
        self.assertNotEqual(guest.get("/admin/users").status_code, 200)

    def test_members_cannot_make_passes(self):
        self.assertEqual(self.m.post("/admin/guests", data={"label": "x", "days": "3"}).status_code, 403)
        self.assertEqual(self.m.get("/admin/guests").status_code, 403)

    def test_a_pass_needs_a_name_and_a_known_length(self):
        before = one("select count(*) from guest_passes")
        self.assertEqual(self.a.post("/admin/guests", data={"label": "", "days": "3"}).status_code, 400)
        self.assertEqual(self.a.post("/admin/guests", data={"label": "x", "days": "365"}).status_code, 400)
        self.assertEqual(one("select count(*) from guest_passes"), before)

    def test_the_guest_is_signed_out_when_the_pass_runs_out(self):
        code, username = self.make_pass()
        guest = self.visitor()
        self.enter(guest, code)
        self.assertEqual(guest.get("/settings").status_code, 200)
        past = datetime.utcnow() - timedelta(minutes=1)
        execute("update users set expires_at=? where username=?", past, username)
        execute("update guest_passes set expires_at=? where user_id_fk=(select id from users where username=?)",
                past, username)
        self.assertEqual(location(guest.get("/settings")).split("?")[0], "/login")
        self.assertEqual(self.enter(self.visitor(), code).status_code, 401)

    def test_ending_a_pass_signs_the_guest_out_at_once(self):
        code, username = self.make_pass()
        guest = self.visitor()
        self.enter(guest, code)
        pid = one("select p.id from guest_passes p join users u on u.id=p.user_id_fk where u.username=?", username)
        self.post(self.a, f"/admin/guests/{pid}/end")
        self.assertEqual(location(guest.get("/settings")).split("?")[0], "/login")
        self.assertEqual(self.enter(self.visitor(), code).status_code, 401)
        guests = self.get_ok(self.a, "/settings").split(">Guests<", 1)[1].split('id="groups"', 1)[0]
        self.assertIn("Ended", guests)
        self.assertNotIn(f"/admin/guests/{pid}/end", guests)

    def test_a_guest_has_no_password_to_sign_in_with(self):
        _, username = self.make_pass()
        r = self.visitor().post("/login", data={"username": username, "password": "!no-password"})
        self.assertIn("Incorrect username or password", flash_text(r))


class InternetGateTests(GuestCase):

    def test_internet_visitors_see_only_the_code_page(self):
        visitor = self.visitor()
        for path in ("/", "/home", "/colony", "/login", "/register", "/inventory/orders", "/setup"):
            r = visitor.get(path, headers=INTERNET)
            self.assertEqual((r.status_code, location(r)), (302, "/guest"), path)
        self.assertEqual(visitor.get("/guest", headers=INTERNET).status_code, 200)
        self.assertEqual(visitor.get("/healthz", headers=INTERNET).status_code, 200)
        self.assertEqual(visitor.get("/static/icon.svg", headers=INTERNET).status_code, 200)

    def test_internet_visitors_cannot_sign_in_or_sign_up(self):
        visitor = self.visitor()
        r = visitor.post("/login", data={"username": self.member, "password": "x"}, headers=INTERNET)
        self.assertEqual(r.status_code, 403)
        r = visitor.post("/register", data={"username": uniq("u"), "password": "x" * 20,
                                            "confirm_password": "x" * 20}, headers=INTERNET)
        self.assertEqual(r.status_code, 403)

    def test_a_code_from_the_internet_opens_the_app(self):
        code, _ = self.make_pass()
        visitor = self.visitor()
        self.assertEqual(self.enter(visitor, code, INTERNET).status_code, 302)
        self.assertEqual(visitor.get("/settings", headers=INTERNET).status_code, 200)

    def test_someone_signed_in_is_let_through(self):
        self.assertEqual(self.m.get("/settings", headers=INTERNET).status_code, 200)

    def test_on_the_lab_network_sign_in_is_as_before(self):
        self.assertEqual(self.visitor().get("/login").status_code, 200)

    def test_the_admin_page_says_whether_internet_access_is_on(self):
        import os
        self.assertIn("Internet access is off", self.get_ok(self.a, "/admin/guests"))
        os.environ["BIOMANAGER_PUBLIC_URL"] = "https://lab.example.ts.net:8443"
        try:
            self.assertIn("Internet access is on: https://lab.example.ts.net:8443", self.get_ok(self.a, "/admin/guests"))
        finally:
            del os.environ["BIOMANAGER_PUBLIC_URL"]


if __name__ == "__main__":
    unittest.main()
