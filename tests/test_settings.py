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
        self.assertEqual(links, ["profile", "security", "appearance", "notifications", "assistant", "data",
                                 "stats", "general", "databases", "people", "devices", "history"])
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

    def test_notifications_save_in_the_background(self):
        who = make_user(uniq("noti"))
        r = self.autosave(client_for(who), "/settings", {"action": "notifications", "notify_orders": "1"})
        self.assertTrue(r.get_json()["ok"])
        orders, transfer = row("select notify_orders, notify_transfer from users where username=?", who)
        self.assertEqual((bool(orders), bool(transfer)), (True, False))

    def test_a_plain_post_comes_back_to_its_pane(self):
        for action, pane in (("profile", "profile"), ("notifications", "notifications"),
                             ("language", "appearance"), ("password", "security")):
            with self.subTest(action=action):
                r = self.m.post("/settings", data={"action": action})
                self.assertEqual(r.status_code, 302)
                self.assertTrue(r.headers["Location"].endswith(f"/settings#{pane}"), r.headers["Location"])
