"""Settings, laid out like the Mac's: a list of panes beside the chosen one
(templates/settings.html), each change saved as it is made."""
from __future__ import annotations

import re

from tests.base import AppTestCase, client_for, make_user, row, uniq


class SettingsWindowTests(AppTestCase):
    def test_lists_its_panes_and_has_one_section_for_each(self):
        html = self.get_ok(self.m, "/settings")
        links = re.findall(r'data-pane-link="([a-z]+)"', html)
        panes = re.findall(r'data-pane="([a-z]+)"', html)
        self.assertEqual(links, ["profile", "appearance", "notifications", "assistant", "data",
                                 "stats", "general", "databases", "people", "devices", "version", "history"])
        self.assertEqual(panes, links)
        self.assertIn('placeholder="Search settings"', html)

    def test_older_links_still_land_on_something(self):
        # #notifications is a pane; #language and #api-tokens are inside one
        # (an admin always has API tokens).
        html = self.get_ok(self.a, "/settings")
        for anchor in ('id="notifications"', 'id="language"', 'id="app-icon"', 'id="api-tokens"'):
            self.assertIn(anchor, html)

    def test_no_admin_tools_or_roadmap_cards(self):
        html = self.get_ok(self.a, "/settings")
        self.assertNotIn("Admin tools", html)
        self.assertNotIn("Coming next", html)


class SavedAsYouChangeTests(AppTestCase):
    def test_profile_saves_in_the_background(self):
        who = make_user(uniq("prof"))
        r = self.autosave(client_for(who), "/settings", {"action": "profile", "display_name": "Ada Lovelace",
                                                          "short_name": "ADALOV", "email": "", "role_title": "Postdoc"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(tuple(row("select display_name, short_name, role_title from users where username=?", who)),
                         ("Ada Lovelace", "ADALO", "Postdoc"))

    def test_a_form_with_some_fields_leaves_the_others_alone(self):
        # "When you sign in" is its own form: picking a start page must not
        # blank the name typed under Profile.
        who = make_user(uniq("part"))
        c = client_for(who)
        self.autosave(c, "/settings", {"action": "profile", "display_name": "Rosalind Franklin", "short_name": "RF"})
        self.autosave(c, "/settings", {"action": "profile", "default_landing": "notebook", "home_layout": "tracks"})
        self.assertEqual(tuple(row("select display_name, short_name, default_landing from users where username=?", who)),
                         ("Rosalind Franklin", "RF", "notebook"))

    def test_your_badge_shows_your_short_name(self):
        # The round badge (top right, and on your records) says what you chose
        # as your short name; without one, your initials.
        who = make_user(uniq("badge"))
        c = client_for(who)
        self.autosave(c, "/settings", {"action": "profile", "display_name": "Tang Hao Xuan", "short_name": ""})
        self.assertRegex(self.get_ok(c, "/settings"), r'class="owner-badge"[^>]*data-len="2"[^>]*>TX</span>')
        self.autosave(c, "/settings", {"action": "profile", "display_name": "Tang Hao Xuan", "short_name": "THX"})
        self.assertRegex(self.get_ok(c, "/settings"), r'class="owner-badge"[^>]*data-len="3"[^>]*>THX</span>')

    def test_notifications_save_in_the_background(self):
        who = make_user(uniq("noti"))
        r = self.autosave(client_for(who), "/settings", {"action": "notifications", "notify_orders": "1"})
        self.assertTrue(r.get_json()["ok"])
        orders, transfer = row("select notify_orders, notify_transfer from users where username=?", who)
        self.assertEqual((bool(orders), bool(transfer)), (True, False))

    def test_a_mouse_picked_from_your_breeder_cage_follows_transfers(self):
        # One switch for both: a pick is one of your animals moving.
        who = make_user(uniq("pick"))
        c = client_for(who)
        html = self.get_ok(c, "/settings")
        self.assertIn('name="notify_transfer"', html)
        self.assertNotIn('name="notify_picked"', html)
        self.autosave(c, "/settings", {"action": "notifications", "notify_orders": "1"})
        self.assertEqual(tuple(map(bool, row("select notify_transfer, notify_picked from users where username=?", who))),
                         (False, False))
        self.autosave(c, "/settings", {"action": "notifications", "notify_transfer": "1"})
        self.assertEqual(tuple(map(bool, row("select notify_transfer, notify_picked from users where username=?", who))),
                         (True, True))

    def test_sign_in_is_on_profile_and_the_extras_are_gone(self):
        html = self.get_ok(self.a, "/settings")
        profile = html[html.index('data-pane="profile"'):html.index('data-pane="appearance"')]
        self.assertIn('id="security"', profile)
        self.assertIn('name="new_password"', profile)
        # What was shown twice, or belongs elsewhere: the header already says
        # who you are, History holds the batches, Help the welcome tour, and
        # the design gallery is for whoever works on the app's look.
        self.assertNotIn(">Username<", html)
        self.assertNotIn("Batch history", html[html.index('data-pane="data"'):html.index('data-pane="stats"')])
        self.assertNotIn("See the welcome tour again", html)
        self.assertNotIn("/design", html[html.index('data-pane="version"'):])
        self.assertNotIn("send-reminders", html)
        self.assertIn('href="/welcome"', html)            # in the Help menu now

    def test_a_plain_post_comes_back_to_its_pane(self):
        for action, pane in (("profile", "profile"), ("notifications", "notifications"),
                             ("language", "appearance"), ("password", "security")):
            with self.subTest(action=action):
                r = self.m.post("/settings", data={"action": action})
                self.assertEqual(r.status_code, 302)
                self.assertTrue(r.headers["Location"].endswith(f"/settings#{pane}"), r.headers["Location"])


class SettingsSaysWhoseAndWhereTests(AppTestCase):
    """Each pane says whose settings it holds and who changes them; the list
    says what a pane is set to; Databases reaches every database's own set-up."""

    def test_every_database_in_the_sidebar_has_its_configure_link(self):
        html = self.get_ok(client_for(make_user(role="admin")), "/settings")
        self.assertIn("Set up each database", html)
        configure = re.findall(r'class="set-row set-config" href="([^"]+)"', html)
        self.assertTrue(configure)
        self.assertTrue(all(url.startswith("/") for url in configure))

    def test_a_pane_says_whose_settings_these_are(self):
        member = self.get_ok(client_for(make_user()), "/settings")
        self.assertIn("Yours alone: only you see and change these.", member)
        self.assertIn("admins change them; you can see them.", member)
        admin = self.get_ok(client_for(make_user(role="admin")), "/settings")
        self.assertIn("you change them as an admin, for everyone.", admin)

    def test_the_list_says_what_a_pane_is_set_to(self):
        html = self.get_ok(client_for(make_user(role="admin")), "/settings")
        self.assertRegex(html, r'class="set-link-sub">\d+ in the sidebar<')
        self.assertRegex(html, r'class="set-link-sub">\d+ members?<')
