"""App icon and accent colour (app/appearance.py).

Each person picks a picture and a colour in Settings; it is saved at once and
kept for them alone. The colour retints the app; Mint, the default, leaves the
shipped accent alone."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

# tests.base first: it points the app at a throwaway database before anything
# imports the app. Importing app first would open data/biomanager.db.
from tests.base import AppTestCase, client_for, make_user, uniq  # isort: skip

from app import appearance  # noqa: E402

STATIC_ICON = Path(__file__).resolve().parent.parent / "app" / "static" / "icon.svg"


class DrawingTests(AppTestCase):
    def test_every_picture_in_every_colour_is_valid_svg(self):
        for glyph in appearance.GLYPHS:
            for color in appearance.PALETTES:
                for variant in ("app", "full", "glyph", "background"):
                    with self.subTest(glyph=glyph, color=color, variant=variant):
                        root = ET.fromstring(appearance.render(glyph, color, variant))
                        self.assertEqual(root.get("viewBox"), "0 0 1024 1024")

    def test_the_colour_reaches_the_drawing(self):
        self.assertIn(appearance.PALETTES["rose"].top, appearance.render("mouse", "rose"))
        self.assertNotIn(appearance.PALETTES["rose"].top, appearance.render("mouse", "sky"))

    def test_mint_keeps_the_shipped_accent(self):
        self.assertEqual(appearance.brand_css("mint"), "")
        css = appearance.brand_css("lavender")
        self.assertIn("--color-brand-600:#7457DB;", css)
        self.assertIn('prefers-color-scheme: dark', css)
        self.assertIn(':root[data-theme="dark"]', css)

    def test_the_static_icon_is_the_default_drawing(self):
        # scripts/build-app-icon.py writes it; rerun that after changing a drawing.
        self.assertIn(appearance.render("helix", "mint").split("<defs>", 1)[1], STATIC_ICON.read_text())


class ChoiceTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.pick(self.m, "helix", "mint")

    def pick(self, client, glyph, color):
        return self.autosave(client, "/settings", {"action": "appearance", "glyph": glyph, "color": color})

    def test_the_default_is_the_helix_in_mint(self):
        fresh = client_for(make_user(uniq("fresh")))
        html = self.get_ok(fresh, "/settings")
        self.assertIn('/static/icon.svg?v=', html)
        self.assertIn('<style id="brand-css"></style>', html)
        self.assertRegex(html, r'value="helix" checked')
        self.assertRegex(html, r'value="mint" checked')

    def test_picking_saves_at_once_and_answers_with_the_new_look(self):
        r = self.pick(self.m, "zebrafish", "rose")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertIn("/app-icon/zebrafish/rose.svg?v=", body["icon"])
        self.assertIn("--color-brand-600:#DC4478;", body["brand_css"])

        html = self.get_ok(self.m, "/settings")
        self.assertIn('href="/app-icon/zebrafish/rose.svg?v=', html)
        self.assertIn("--color-brand-600:#DC4478;", html)
        self.assertRegex(html, r'value="zebrafish" checked')

    def test_the_choice_is_kept_per_person(self):
        self.pick(self.m, "worm", "sky")
        self.assertIn("/app-icon/worm/sky.svg", self.get_ok(self.m, "/home"))
        self.assertNotIn("/app-icon/worm/sky.svg", self.get_ok(self.o, "/home"))

    def test_unknown_values_fall_back_to_the_default(self):
        self.pick(self.m, "unicorn", "tartan")
        html = self.get_ok(self.m, "/settings")
        self.assertIn('/static/icon.svg?v=', html)
        self.assertRegex(html, r'value="helix" checked')

    def test_a_plain_form_post_works_without_javascript(self):
        r = self.post(self.m, "/settings", {"action": "appearance", "glyph": "fly", "color": "peach"})
        self.assertFlash(r, "App icon updated")
        self.assertIn("/app-icon/fly/peach.svg", r.get_data(as_text=True))


class FontTests(AppTestCase):
    """Inter by default; the computer's own font for whoever picks it."""

    def font(self, client, value):
        return self.autosave(client, "/settings", {"action": "profile", "ui_font": value})

    def test_inter_is_the_default(self):
        html = self.get_ok(client_for(make_user(uniq("fresh"))), "/settings")
        self.assertNotIn('data-font="system"', html)
        self.assertIn('<option value="inter" selected>', html)

    def test_the_system_font_is_kept_per_person(self):
        me = client_for(make_user(uniq("sysfont")))
        self.font(me, "system")
        self.assertIn('data-font="system"', self.get_ok(me, "/home"))
        self.assertNotIn('data-font="system"', self.get_ok(client_for(make_user(uniq("other"))), "/home"))
        self.font(me, "inter")
        self.assertNotIn('data-font="system"', self.get_ok(me, "/home"))

    def test_unknown_fonts_fall_back_to_inter(self):
        me = client_for(make_user(uniq("oddfont")))
        self.font(me, "comic-sans")
        self.assertNotIn('data-font="system"', self.get_ok(me, "/home"))


class IconRouteTests(AppTestCase):
    def test_serves_an_svg_without_signing_in(self):
        client = self.m.application.test_client()
        r = client.get("/app-icon/petri/lemon.svg")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "image/svg+xml")
        self.assertIn("immutable", r.headers["Cache-Control"])
        ET.fromstring(r.get_data(as_text=True))

    def test_unknown_pictures_and_colours_are_not_found(self):
        self.assertEqual(self.m.get("/app-icon/unicorn/mint.svg").status_code, 404)
        self.assertEqual(self.m.get("/app-icon/helix/tartan.svg").status_code, 404)
