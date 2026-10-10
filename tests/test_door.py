"""The way in (app/door.py, templates/door/): the sign-in that is the front
page, asking to join and waiting for approval, a guest code, asking the
lab's admins, remembering an account on a computer, and a first-timer
starting a lab or opening the one they have."""
from __future__ import annotations

from unittest import mock

# tests.base first: it points the app at a throwaway database before app is imported.
from tests.base import AppTestCase, count, execute, one, uniq, user_id
from app import door
from app.app import app  # noqa: E402

PASSWORD = "a long enough passphrase"
INTERNET = {"X-BioManager-Entry": "internet"}


class FrontPage(AppTestCase):
    def anonymous(self):
        return app.test_client()

    def test_signed_out_the_front_page_is_the_sign_in(self):
        html = self.get_ok(self.anonymous(), "/")
        for text in ('action="/login', 'name="password"', 'name="remember"', 'href="/register"', 'href="/guest"',
                     'href="/ask?why=password"', "icon.svg", "door-pattern"):
            self.assertIn(text, html)
        self.assertNotIn("What it does for you", html)

    def test_it_names_the_lab_and_lists_its_databases_but_not_personal_ones(self):
        execute("delete from app_settings where key='lab_name'")
        execute("insert into app_settings (key, value) values ('lab_name', 'Rivera Lab')")
        self.a.post("/inventory/new", data={"preset": "custom", "label": "Secret stash", "audience": "me"})
        html = self.get_ok(self.anonymous(), "/")
        self.assertIn("Sign in to Rivera Lab", html)
        self.assertIn(">RL<", html)
        kept = html[html.index("In this lab"):html.index("</section>", html.index("In this lab"))]
        self.assertIn("Reagents", kept)
        self.assertNotIn("Secret stash", kept)

    def test_signed_in_the_front_page_is_the_app(self):
        self.assertEqual(self.m.get("/").status_code, 302)

    def test_from_the_internet_it_is_still_only_the_guest_page_and_asking(self):
        r = self.anonymous().get("/", headers=INTERNET)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/guest", r.headers["Location"])
        html = self.anonymous().get("/guest", headers=INTERNET).get_data(as_text=True)
        self.assertNotIn("In this lab", html)       # the lab's name, not what it keeps
        self.assertEqual(self.anonymous().get("/ask", headers=INTERNET).status_code, 200)

    def test_a_wrong_password_comes_back_to_the_same_page_with_the_name_kept(self):
        r = self.anonymous().post("/login", data={"username": self.member, "password": "wrong"})
        html = r.get_data(as_text=True)
        self.assertIn("Incorrect username or password", html)
        self.assertIn(f'value="{self.member}"', html)


class Joining(AppTestCase):
    def ask_to_join(self, client):
        username = uniq("joiner")
        r = client.post("/register", data={"username": username, "display_name": "Sam Lee", "password": PASSWORD,
                                           "confirm_password": PASSWORD})
        return username, r

    def test_asking_to_join_waits_on_a_page_that_sees_the_approval(self):
        c = app.test_client()
        username, r = self.ask_to_join(c)
        self.assertEqual(r.headers["Location"], "/joined")
        html = self.get_ok(c, "/joined")
        self.assertIn(username, html)
        self.assertIn("needs to approve it", html)
        self.assertFalse(c.get("/joined/status").get_json()["approved"])
        self.post(self.a, f"/admin/users/{user_id(username)}/disable")          # approve
        state = c.get("/joined/status").get_json()
        self.assertTrue(state["approved"])
        self.assertIn(f"u={username}", state["signin"])

    def test_another_browser_learns_nothing_from_the_status(self):
        self.ask_to_join(app.test_client())
        self.assertEqual(app.test_client().get("/joined/status").get_json(), {"approved": False, "known": False})
        self.assertEqual(app.test_client().get("/joined").status_code, 302)

    def test_the_sign_in_link_fills_in_the_name(self):
        html = self.get_ok(app.test_client(), f"/login?u={self.member}")
        self.assertIn(f'value="{self.member}"', html)


class AskingTheAdmins(AppTestCase):
    def setUp(self):
        door.ask_throttle._failures.clear()

    def test_a_visitor_without_a_code_reaches_every_admin(self):
        name = uniq("Visitor ")
        r = app.test_client().post("/ask", data={"why": "guest", "name": name, "contact": "v@example.org",
                                                 "note": "Visiting for the cryostat data."})
        self.assertIn("Sent to the lab", r.get_data(as_text=True))
        self.assertEqual(count("notifications", "recipient_username=? and title like ?", self.admin,
                               f"{name} asks for a guest code"), 1)
        message = one("select message from notifications where recipient_username=? and title like ?",
                      self.admin, f"{name}%")
        self.assertIn("v@example.org", message)
        self.assertIn("cryostat", message)

    def test_it_needs_a_name_and_is_limited(self):
        r = app.test_client().post("/ask", data={"why": "password", "name": ""})
        self.assertIn("Say who you are", r.get_data(as_text=True))
        for _ in range(4):
            app.test_client().post("/ask", data={"why": "account", "name": uniq("n")})
        r = app.test_client().post("/ask", data={"why": "account", "name": uniq("n")})
        self.assertIn("Several requests", r.get_data(as_text=True))


class RememberingAnAccount(AppTestCase):
    def test_ticked_the_next_page_keeps_the_name_in_this_browser(self):
        from tests.test_lab import PASSWORD as LAB_PASSWORD, real_user
        c = app.test_client()
        username = real_user("member")
        c.post("/login", data={"username": username, "password": LAB_PASSWORD, "remember": "1"})
        html = c.get("/home", follow_redirects=True).get_data(as_text=True)
        self.assertIn("biomanager:accounts", html)
        self.assertIn(username, html.split("biomanager:accounts", 1)[1][:400])
        # Once: the page after does not write it again.
        self.assertNotIn("biomanager:accounts", c.get("/home", follow_redirects=True).get_data(as_text=True))

    def test_not_ticked_nothing_is_kept(self):
        from tests.test_lab import PASSWORD as LAB_PASSWORD, real_user
        c = app.test_client()
        c.post("/login", data={"username": real_user("member"), "password": LAB_PASSWORD})
        self.assertNotIn("biomanager:accounts", c.get("/home", follow_redirects=True).get_data(as_text=True))


class StartingOrOpeningALab(AppTestCase):
    def test_with_accounts_there_is_nothing_to_start(self):
        self.assertEqual(app.test_client().get("/start").status_code, 302)
        self.assertEqual(app.test_client().get("/open-lab").status_code, 404)

    def test_before_any_account_a_server_says_it_has_no_lab_and_starts_one_by_name(self):
        c = app.test_client()
        with mock.patch.object(door, "no_accounts_yet", return_value=True):
            html = self.get_ok(c, "/")
            self.assertIn("This server has no lab yet", html)
            self.assertIn("wrong address", html)
            html = self.get_ok(c, "/start")
            self.assertIn("This makes a new, empty lab database", html)
            r = c.post("/start", data={"lab_name": "Okafor Lab"})
            self.assertEqual(r.headers["Location"], "/register")          # a server: no "where"
        with c.session_transaction() as sess:
            self.assertEqual(sess[door.NEW_LAB_NAME], "Okafor Lab")

    def test_the_desktop_app_asks_first_whether_the_lab_has_one(self):
        c = app.test_client()
        with mock.patch.object(door, "no_accounts_yet", return_value=True), \
                mock.patch("app.devices.on_this_computer", return_value=True):
            html = self.get_ok(c, "/")
            self.assertIn("Does your lab already use BioManager?", html)
            self.assertIn('href="/open-lab"', html)
            self.assertIn("Nothing is created on this computer", html)
            r = c.post("/start", data={"lab_name": "Okafor Lab"})
            self.assertEqual(r.headers["Location"], "/start/where")
            html = self.get_ok(c, "/start/where")
            self.assertIn("Where should Okafor Lab live?", html)

    def test_opening_a_lab_finds_it_by_address_and_keeps_it_for_the_window(self):
        pages = {"https://lab.example.org/healthz": (200, "ok\n"),
                 "https://lab.example.org/": (200, "<html><title>Rivera Lab · BioManager</title></html>")}
        saved = {}
        with mock.patch.object(door, "no_accounts_yet", return_value=True), \
                mock.patch("app.devices.on_this_computer", return_value=True), \
                mock.patch("app.devices._save_prefs", side_effect=lambda **kw: saved.update(kw)), \
                mock.patch.object(door, "_fetch", side_effect=lambda url, timeout=6: pages[url]):
            c = app.test_client()
            html = c.post("/open-lab", data={"address": "lab.example.org"}).get_data(as_text=True)
            self.assertIn("Found your lab", html)
            self.assertIn("Rivera Lab", html)
            self.assertEqual(saved, {})                 # only on Open (tests/test_lab_move.py)
            c.post("/open-lab", data={"action": "open"})
        self.assertEqual(saved, {"window_url": "https://lab.example.org"})

    def test_an_address_that_does_not_answer_says_so(self):
        def nothing(url, timeout=6):
            raise OSError("no route")
        with mock.patch.object(door, "no_accounts_yet", return_value=True), \
                mock.patch("app.devices.on_this_computer", return_value=True), \
                mock.patch.object(door, "_fetch", side_effect=nothing):
            html = app.test_client().post("/open-lab", data={"address": "nowhere.example"}).get_data(as_text=True)
        self.assertIn("No BioManager answered at nowhere.example", html)

    def test_addresses_are_tried_as_typed(self):
        self.assertEqual(door.normalise_address("lab.example.org/"), ["https://lab.example.org", "http://lab.example.org"])
        self.assertEqual(door.normalise_address("http://10.0.0.5:5077"), ["http://10.0.0.5:5077"])
        self.assertEqual(door.normalise_address("two words"), [])
        self.assertEqual(door.initials("Rivera Lab"), "RL")
        self.assertEqual(door.initials(""), "B")
